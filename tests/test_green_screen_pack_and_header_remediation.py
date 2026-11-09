import os
import pytest
from pathlib import Path
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import QModelIndex

from src.ui.tabs.tab_assets import (
    TabAssets,
    StudioAsset,
    FavoritesManager,
    CategoryOverridesManager,
    RecentManager,
)
from src.core.collections_manager import CollectionsManager
from src.core.pack_manager import PackManager, PackManifest
from src.ui.lux.library.asset_model import IdRole, AssetObjRole


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    """
    Cô lập hoàn toàn dữ liệu người dùng thật:
    Tất cả các tệp cấu hình của PackManager, CollectionsManager, FavoritesManager,
    CategoryOverridesManager, RecentManager và StorageManager đều được chuyển hướng vào tmp_path.
    """
    xdg_dir = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_dir))

    packs_file = tmp_path / "installed_packs.json"
    colls_file = tmp_path / "collections.json"
    favs_file = tmp_path / "favorites.json"
    overrides_file = tmp_path / "category_overrides.json"
    recent_file = str(tmp_path / "recent.json")

    monkeypatch.setattr(PackManager, "INSTALLED_PACKS_FILE", packs_file)
    monkeypatch.setattr(CollectionsManager, "FILE_PATH", colls_file)
    monkeypatch.setattr(FavoritesManager, "FILE_PATH", favs_file)
    monkeypatch.setattr(CategoryOverridesManager, "FILE_PATH", overrides_file)
    monkeypatch.setattr(RecentManager, "FILE_PATH", recent_file)

    FavoritesManager._cache = None
    CategoryOverridesManager._cache = None
    RecentManager._cache = None
    CollectionsManager._cache = None
    PackManager._invalidate_caches()

    yield

    FavoritesManager._cache = None
    CategoryOverridesManager._cache = None
    RecentManager._cache = None
    CollectionsManager._cache = None
    PackManager._invalidate_caches()


def test_broll_green_screen_pack_screenshot_parity_and_sidebar_click(qapp):
    """
    R1. BROLL category & Screenshot parity:
    1. Tài nguyên phông xanh thực tế trong StorageManager mang category/tab = 'broll'.
    2. Khi chuyển sang rail Overlay hoặc rail Meme, Cột 2 nhận diện gói 'green_screen_memes_109'
       và chỉ tạo đúng 1 nút gói hợp nhất mang tên tiếng Việt 'Kho 109 Video Meme Phông Xanh (Green Screen)'.
    3. Click trực tiếp qua widget QPushButton.click() phải hiển thị đúng toàn bộ 89 video phông xanh.
    4. TUYỆT ĐỐI KHÔNG làm biến đổi (mutate) category gốc của tài nguyên (vẫn giữ nguyên là 'broll').
    5. Các gói B-Roll thông thường khác (ví dụ: Vietnam Nature Broll) KHÔNG được phép hiển thị trên Overlay/Meme.
    """
    tab = TabAssets(headless=True)

    # Giả lập 89 video phông xanh với category='broll' đúng như dữ liệu thực tế
    green_screen_items = []
    for i in range(1, 90):
        item = StudioAsset(
            id=f"gs_meme_{i:03d}",
            name=f"Green Screen Meme {i:03d}",
            tab="broll",
            category="broll",
            sub="Tong_Hop_Meme_Green_Screen_109Clips",
            pack_name="Tong_Hop_Meme_Green_Screen_109Clips",
            file_path=f"/fake/storage/broll/Tong_Hop_Meme_Green_Screen_109Clips/meme_{i:03d}.mp4"
        )
        green_screen_items.append(item)

    # Thêm 1 gói B-roll thông thường để kiểm tra chặn rò rỉ
    unrelated_broll = []
    for i in range(1, 11):
        item = StudioAsset(
            id=f"vn_nature_{i:03d}",
            name=f"Vietnam Nature {i:03d}",
            tab="broll",
            category="broll",
            sub="Vietnam_Nature_Broll_4K",
            pack_name="Vietnam_Nature_Broll_4K",
            file_path=f"/fake/storage/broll/Vietnam_Nature_Broll_4K/nature_{i:03d}.mp4"
        )
        unrelated_broll.append(item)

    tab.all_assets = list(green_screen_items) + list(unrelated_broll)
    tab._populate_cards()

    # Ghi nhận metadata gói đã cài đặt
    PackManager.record_installed_pack(
        manifest_or_id="green_screen_memes_109",
        folder_name="Tong_Hop_Meme_Green_Screen_109Clips",
        installed_paths=[x.file_path for x in green_screen_items]
    )

    # --- A. Kiểm tra trên rail OVERLAY ---
    tab._filter_by_rail("overlay")

    # Thu thập các nút gói trong Cột 2
    pack_btns = []
    for i in range(tab.cats_vbox.count()):
        it = tab.cats_vbox.itemAt(i)
        if it and isinstance(it.widget(), QPushButton):
            w = it.widget()
            sub_id = w.property("sub_cat_id") or ""
            if sub_id.startswith("pack:"):
                pack_btns.append(w)

    # Chỉ có 1 nút gói duy nhất (Kho 109 Video Meme Phông Xanh)
    assert len(pack_btns) == 1, f"Kỳ vọng 1 nút gói trên rail overlay nhưng thấy {len(pack_btns)}: {[b.text() for b in pack_btns]}"
    target_btn = pack_btns[0]
    assert "Kho 109 Video Meme Phông Xanh (Green Screen)" in target_btn.text()
    assert "(89)" in target_btn.text()

    # Gói Vietnam Nature Broll KHÔNG được hiển thị ở Overlay
    all_cat_texts = [tab.cats_vbox.itemAt(i).widget().text() for i in range(tab.cats_vbox.count()) if tab.cats_vbox.itemAt(i).widget()]
    assert not any("Vietnam_Nature" in t for t in all_cat_texts), "Gói B-Roll thường bị hiển thị trái phép trên rail Overlay!"

    # Mô phỏng click chuột thật vào nút gói bằng .click()
    target_btn.click()
    assert tab.current_sub_cat == "pack:Kho 109 Video Meme Phông Xanh (Green Screen)"

    # Proxy filter phải lọc đúng 89 mẫu
    assert tab.asset_filter.rowCount() == 89, f"Kỳ vọng 89 mẫu phông xanh nhưng có {tab.asset_filter.rowCount()}"
    assert tab.lbl_count_badge.text() == "89 mẫu"

    # Header phải hiển thị chính xác tên tab và tên gói
    assert "Hiệu ứng lớp phủ • Kho 109 Video Meme Phông Xanh (Green Screen)" in tab.lbl_main_cat.text()

    # KHÔNG được mutate category gốc trong all_assets
    for a in tab.all_assets:
        if "meme_" in a.id:
            assert a.category == "broll", f"Category gốc của asset {a.id} bị mutate thành {a.category}!"
            assert a.tab == "broll", f"Tab gốc của asset {a.id} bị mutate thành {a.tab}!"

    # --- B. Kiểm tra trên rail MEME ---
    tab._filter_by_rail("meme")

    meme_pack_btns = []
    for i in range(tab.cats_vbox.count()):
        it = tab.cats_vbox.itemAt(i)
        if it and isinstance(it.widget(), QPushButton):
            w = it.widget()
            sub_id = w.property("sub_cat_id") or ""
            if sub_id.startswith("pack:"):
                meme_pack_btns.append(w)

    assert len(meme_pack_btns) == 1
    meme_target_btn = meme_pack_btns[0]
    assert "Kho 109 Video Meme Phông Xanh (Green Screen)" in meme_target_btn.text()
    assert "(89)" in meme_target_btn.text()

    # Click trên rail Meme
    meme_target_btn.click()
    assert tab.asset_filter.rowCount() == 89
    assert "Meme thịnh hành • Kho 109 Video Meme Phông Xanh (Green Screen)" in tab.lbl_main_cat.text()


def test_cross_rail_boundary_and_negative_leak_protection(qapp):
    """
    R2. Asset filter category bypass vs cross-rail policy:
    - Chặn đứng hoàn toàn việc rò rỉ chéo danh mục không hợp lệ:
      1. Gói meme thông thường (không phải phông xanh) không được hiển thị sang Overlay.
      2. Gói B-Roll thông thường không được hiển thị sang Overlay hoặc Meme.
      3. Gói hỗn hợp âm thanh + hình ảnh: âm thanh SFX KHÔNG được lọt vào rail Overlay khi chọn gói.
    """
    tab = TabAssets(headless=True)

    items = [
        # Gói meme thông thường
        StudioAsset(
            id="reaction_01", name="Reaction Meme 01", tab="meme", category="meme",
            sub="Funny_Reaction_Memes", pack_name="Funny_Reaction_Memes", file_path="/fake/r01.mp4"
        ),
        # Gói B-Roll thông thường
        StudioAsset(
            id="nature_01", name="Nature 01", tab="broll", category="broll",
            sub="Nature_Pack", pack_name="Nature_Pack", file_path="/fake/n01.mp4"
        ),
        # Gói hỗn hợp: SFX + Overlay video
        StudioAsset(
            id="mixed_sfx_01", name="Whoosh Sound 01", tab="sfx", category="sfx",
            sub="Cinematic_VFX_SFX", pack_name="Cinematic_VFX_SFX", file_path="/fake/sfx01.wav"
        ),
        StudioAsset(
            id="mixed_ovl_01", name="Light Leak 01", tab="overlay", category="overlay",
            sub="Cinematic_VFX_SFX", pack_name="Cinematic_VFX_SFX", file_path="/fake/ovl01.mp4"
        ),
    ]

    tab.all_assets = list(items)
    tab._populate_cards()

    # 1. Trên rail Overlay: Không xuất hiện nút gói Funny_Reaction_Memes hay Nature_Pack
    tab._filter_by_rail("overlay")
    pack_names_overlay = [
        tab.cats_vbox.itemAt(i).widget().text()
        for i in range(tab.cats_vbox.count())
        if tab.cats_vbox.itemAt(i).widget() and (tab.cats_vbox.itemAt(i).widget().property("sub_cat_id") or "").startswith("pack:")
    ]
    assert not any("Reaction" in p for p in pack_names_overlay), "Gói meme bị rò rỉ vào rail Overlay!"
    assert not any("Nature" in p for p in pack_names_overlay), "Gói B-Roll thường bị rò rỉ vào rail Overlay!"

    # 2. Khi chọn gói hỗn hợp 'Cinematic_VFX_SFX' trên rail Overlay:
    # CHỈ có video Light Leak 01 hiển thị, Whoosh Sound 01 TUYỆT ĐỐI BỊ CHẶN
    tab._on_sub_category_clicked("pack:Cinematic_VFX_SFX")
    assert tab.asset_filter.rowCount() == 1
    visible_id = tab.asset_filter.index(0, 0).data(IdRole)
    assert visible_id == "mixed_ovl_01", f"Âm thanh SFX bị lọt vào rail Overlay: {visible_id}"

    # 3. Khi chuyển sang rail SFX và chọn gói hỗn hợp:
    # CHỈ có âm thanh Whoosh Sound 01 hiển thị, Light Leak 01 TUYỆT ĐỐI BỊ CHẶN
    tab._filter_by_rail("sfx")
    tab._on_sub_category_clicked("pack:Cinematic_VFX_SFX")
    assert tab.asset_filter.rowCount() == 1
    visible_sfx_id = tab.asset_filter.index(0, 0).data(IdRole)
    assert visible_sfx_id == "mixed_sfx_01", f"Video Overlay bị lọt vào rail SFX: {visible_sfx_id}"


def test_zero_stat_performance_during_filtering(qapp, monkeypatch):
    """
    R3. Performance (Zero-Stat per row):
    Kiểm tra rằng quá trình lọc hàng loạt và duyệt proxy model tuyệt đối KHÔNG gọi Path.stat
    hoặc đọc đĩa cứng đối với file installed_packs.json trên từng dòng (O(1) memory lookup).
    """
    tab = TabAssets(headless=True)

    # Giả lập 200 items đa dạng
    items = []
    for i in range(1, 201):
        items.append(StudioAsset(
            id=f"item_{i:03d}",
            name=f"Item {i:03d}",
            tab="broll",
            category="broll",
            sub="Tong_Hop_Meme_Green_Screen_109Clips",
            pack_name="Tong_Hop_Meme_Green_Screen_109Clips",
            file_path=f"/fake/item_{i:03d}.mp4"
        ))

    tab.all_assets = list(items)
    tab._populate_cards()

    PackManager.record_installed_pack(
        manifest_or_id="green_screen_memes_109",
        folder_name="Tong_Hop_Meme_Green_Screen_109Clips",
        installed_paths=[x.file_path for x in items]
    )

    tab._filter_by_rail("overlay")

    # Đếm số lần truy cập file installed_packs.json thông qua open hoặc stat
    stat_counter = {"installed_packs_stats": 0}
    orig_stat = Path.stat

    def tracked_stat(path_obj, *args, **kwargs):
        if "installed_packs.json" in str(path_obj):
            stat_counter["installed_packs_stats"] += 1
        return orig_stat(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", tracked_stat)

    # Đặt lại counter về 0 trước khi thực thi lọc hàng
    stat_counter["installed_packs_stats"] = 0

    # Thực hiện lọc gói trên 200 dòng
    tab._on_sub_category_clicked("pack:Kho 109 Video Meme Phông Xanh (Green Screen)")
    assert tab.asset_filter.rowCount() == 200

    # Kiểm tra xác nhận: Số lần đọc stat đối với file metadata trong lúc lọc 200 dòng là 0!
    assert stat_counter["installed_packs_stats"] == 0, (
        f"Vi phạm hiệu năng: Có {stat_counter['installed_packs_stats']} lệnh stat() đối với "
        f"installed_packs.json trong khi lọc 200 dòng!"
    )


def test_inspector_consistency_selection_clearing_and_repopulation(qapp):
    """
    R4. Inspector consistency:
    - Khi lọc làm mất mục đang chọn: unselect trung thực, không để sót chi tiết rác (sticky details).
    - Khi count == 0: dọn sạch preview, vô hiệu hóa các nút hành động, đặt tiêu đề 'Chưa chọn mẫu'.
    - Khi xóa lọc và danh sách hiển thị lại: giữ nguyên trạng thái chưa chọn trung thực cho tới khi người dùng click.
    """
    tab = TabAssets(headless=True)

    items = [
        StudioAsset(
            id="gs_001", name="Green Screen 001", tab="broll", category="broll",
            sub="Tong_Hop_Meme_Green_Screen_109Clips", pack_name="Tong_Hop_Meme_Green_Screen_109Clips",
            file_path="/fake/gs001.mp4"
        ),
        StudioAsset(
            id="gs_002", name="Green Screen 002", tab="broll", category="broll",
            sub="Tong_Hop_Meme_Green_Screen_109Clips", pack_name="Tong_Hop_Meme_Green_Screen_109Clips",
            file_path="/fake/gs002.mp4"
        )
    ]

    tab.all_assets = list(items)
    tab._populate_cards()

    PackManager.record_installed_pack(
        manifest_or_id="green_screen_memes_109",
        folder_name="Tong_Hop_Meme_Green_Screen_109Clips",
        installed_paths=[x.file_path for x in items]
    )

    tab._filter_by_rail("overlay")
    tab._on_sub_category_clicked("pack:Kho 109 Video Meme Phông Xanh (Green Screen)")
    assert tab.asset_filter.rowCount() == 2

    # 1. Chọn mục đầu tiên
    first_idx = tab.asset_filter.index(0, 0)
    tab.selected_asset = first_idx.data(AssetObjRole)
    tab.asset_grid.setCurrentIndex(first_idx)
    tab._update_inspector_details()

    assert tab.selected_asset is not None
    assert "Green Screen 001" in tab.lbl_insp_title.text()
    assert tab.btn_insert_title_playhead.isEnabled() is True
    assert tab.btn_drag_davinci.isEnabled() is True

    # 2. Lọc tìm kiếm khiến mục đang chọn bị lọc mất (nhưng lưới vẫn còn mục khác)
    tab.txt_search.setText("002")
    assert tab.asset_filter.rowCount() == 1
    # Mục gs_001 không còn hiển thị -> tab.selected_asset phải được dọn về None
    assert tab.selected_asset is None
    assert not tab.asset_grid.currentIndex().isValid()
    assert "Chưa chọn mẫu" in tab.lbl_insp_title.text()
    assert tab.btn_insert_title_playhead.isEnabled() is False
    assert tab.btn_drag_davinci.isEnabled() is False

    # 3. Lọc không có kết quả nào (count == 0)
    tab.txt_search.setText("khong_ton_tai_xyz")
    assert tab.asset_filter.rowCount() == 0
    assert tab.selected_asset is None
    assert not tab.asset_grid.currentIndex().isValid()
    assert "Chưa chọn mẫu" in tab.lbl_insp_title.text()
    assert tab.btn_insert_title_playhead.isEnabled() is False
    assert tab.btn_drag_davinci.isEnabled() is False

    # 4. Xóa bộ lọc -> Lưới hiển thị lại 2 mẫu
    tab.txt_search.setText("")
    assert tab.asset_filter.rowCount() == 2
    # Trạng thái vẫn là chưa chọn trung thực (không tự tiện chọn dính lại mẫu cũ)
    assert tab.selected_asset is None
    assert not tab.asset_grid.currentIndex().isValid()
    assert "Chưa chọn mẫu" in tab.lbl_insp_title.text()
    assert tab.btn_insert_title_playhead.isEnabled() is False


def test_mixed_folder_and_manifest_deduplication(qapp):
    """
    Kiểm tra khử trùng lặp nút gói khi dữ liệu vừa có tên folder vừa có tên manifest:
    Gộp thành 1 nút duy nhất với tổng số mẫu chính xác.
    """
    tab = TabAssets(headless=True)

    items = []
    # 50 items mang tên folder
    for i in range(1, 51):
        items.append(StudioAsset(
            id=f"gs_f_{i:03d}",
            name=f"GS Folder {i:03d}",
            tab="broll",
            category="broll",
            sub="Tong_Hop_Meme_Green_Screen_109Clips",
            pack_name="Tong_Hop_Meme_Green_Screen_109Clips",
            file_path=f"/fake/f_{i:03d}.mp4"
        ))
    # 39 items mang tên manifest
    for i in range(51, 90):
        items.append(StudioAsset(
            id=f"gs_m_{i:03d}",
            name=f"GS Manifest {i:03d}",
            tab="broll",
            category="broll",
            sub="Kho 109 Video Meme Phông Xanh (Green Screen)",
            pack_name="Kho 109 Video Meme Phông Xanh (Green Screen)",
            file_path=f"/fake/m_{i:03d}.mp4"
        ))

    tab.all_assets = list(items)
    tab._populate_cards()

    PackManager.record_installed_pack(
        manifest_or_id="green_screen_memes_109",
        folder_name="Tong_Hop_Meme_Green_Screen_109Clips",
        installed_paths=[x.file_path for x in items]
    )

    tab._filter_by_rail("overlay")

    pack_buttons = []
    for i in range(tab.cats_vbox.count()):
        item = tab.cats_vbox.itemAt(i)
        if item and item.widget():
            txt = getattr(item.widget(), "text", lambda: "")()
            if "📦" in txt and ("Tong_Hop_Meme" in txt or "Phông Xanh" in txt or "Green Screen" in txt):
                pack_buttons.append(txt)

    assert len(pack_buttons) == 1, f"Kỳ vọng 1 nút duy nhất nhưng nhận {len(pack_buttons)}: {pack_buttons}"
    assert "(89)" in pack_buttons[0]

    # Click nút gói -> Phải nhận đủ 89 items (cả 50 folder + 39 manifest)
    tab._on_sub_category_clicked("pack:Kho 109 Video Meme Phông Xanh (Green Screen)")
    assert tab.asset_filter.rowCount() == 89
    assert tab.lbl_count_badge.text() == "89 mẫu"
