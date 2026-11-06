"""
tests/ui/test_studio_storage_snapshot.py
Kiểm thử bộ thu nhận (receiver) managed snapshot nhẹ tại TabAssets (G04-04A2a-I):
- Thay thế managed assets trong all_assets mà không quét lại bundled hay chạm I/O
- Idempotence: gọi nhiều lần cùng snapshot không gây lỗi hay sinh nút lặp
- Danh sách rỗng [] xóa managed assets nhưng bảo toàn 100% bundled/local
- Phân biệt managed_assets=None / taxonomy=None với dữ liệu rỗng [] / {}
- Adapter bảo toàn metadata: sub != pack_name, tags không chia sẻ mutable list, preview/thumbnail, availability
- Xử lý duration chuẩn: None không sinh duration 3s giả, bảo toàn duration_sec, float/int hữu hạn
- Bảo toàn tham chiếu identity cho runtime objects (_raw_obj, native_preset), không deepcopy
- Không làm biến đổi (mutate) dict input của caller
- Từ chối (reject) ID trùng lặp nội bộ snapshot hoặc va chạm với non-managed trước khi thay đổi dữ liệu
- Không phát signal library_assets_changed (passive update = zero echo)
- SearchIndex, ContextualSuggester và AssetListModel đồng bộ nội dung mới
- Bảo toàn lựa chọn (selection) nếu còn hợp lệ; xóa sạch selection/Inspector nếu asset bị xóa khỏi kho
- Giữ search query hợp lệ; thanh rail danh mục tùy biến cập nhật đúng và không lặp nút
"""

import os
from pathlib import Path
from typing import Any, Dict, List
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.core.storage_manager import StorageManager
from src.ui.tabs.tab_assets import TabAssets, StudioAsset


@pytest.fixture
def clean_studio_tab(qapp, tmp_path, monkeypatch):
    """Khởi tạo TabAssets độc lập được gắn StorageManager tạm thời, không telemetry."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata_studio"))
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    tab = TabAssets(headless=False)
    tab.storage = storage

    # Mock _rescan_assets để kiểm tra receiver không bao giờ gọi hàm quét nặng này
    monkeypatch.setattr(tab, "_rescan_assets", lambda: pytest.fail("_rescan_assets không được phép gọi trong receiver!"))

    yield tab

    if hasattr(tab, "toast_timer") and tab.toast_timer.isActive():
        tab.toast_timer.stop()
    if hasattr(tab, "sfx_playback_timer") and tab.sfx_playback_timer.isActive():
        tab.sfx_playback_timer.stop()
    tab.close()


def test_replace_managed_assets_and_idempotence(clean_studio_tab):
    """apply_storage_snapshot thay thế managed assets và chạy nhiều lần giữ nguyên kết quả (idempotence)."""
    initial_total = len(clean_studio_tab.all_assets)
    initial_managed = sum(1 for a in clean_studio_tab.all_assets if getattr(a, "storage_managed", False))

    snap1 = [
        {"id": "snap_01", "name": "Vlog Transition 01", "tab": "trans", "category": "trans", "sub": "Vlog", "file_path": "/tmp/t1.setting"},
        {"id": "snap_02", "name": "Whoosh SFX", "tab": "sfx", "category": "sfx", "sub": "Whoosh", "file_path": "/tmp/s1.wav"},
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap1, taxonomy={})

    expected_total = initial_total - initial_managed + 2
    assert len(clean_studio_tab.all_assets) == expected_total
    managed_ids = {a.id for a in clean_studio_tab.all_assets if getattr(a, "storage_managed", False)}
    assert managed_ids == {"snap_01", "snap_02"}

    # Gọi lần thứ 2 với cùng dữ liệu (Idempotence)
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap1, taxonomy={})
    assert len(clean_studio_tab.all_assets) == expected_total
    managed_ids_2 = {a.id for a in clean_studio_tab.all_assets if getattr(a, "storage_managed", False)}
    assert managed_ids_2 == {"snap_01", "snap_02"}


def test_empty_managed_assets_clears_managed_without_deleting_bundled_or_local(clean_studio_tab):
    """Nạp snapshot rỗng [] phải xóa toàn bộ managed assets nhưng giữ nguyên vẹn 100% bundled và local assets."""
    bundled_counts = (
        len(clean_studio_tab.all_presets),
        len(clean_studio_tab.all_transitions),
        len(clean_studio_tab.all_luts),
        len(clean_studio_tab.all_icons),
        len(clean_studio_tab.all_overlays),
        len(clean_studio_tab.all_memes),
        len(clean_studio_tab.all_sfx),
        len(clean_studio_tab.all_bgm),
        len(clean_studio_tab.local_assets),
    )
    expected_non_managed = sum(bundled_counts)

    # Nạp danh sách rỗng
    clean_studio_tab.apply_storage_snapshot(managed_assets=[], taxonomy={})

    current_managed = [a for a in clean_studio_tab.all_assets if getattr(a, "storage_managed", False)]
    assert len(current_managed) == 0
    assert len(clean_studio_tab.all_assets) == expected_non_managed

    # Kiểm tra identity của các danh sách bundled
    current_counts = (
        len(clean_studio_tab.all_presets),
        len(clean_studio_tab.all_transitions),
        len(clean_studio_tab.all_luts),
        len(clean_studio_tab.all_icons),
        len(clean_studio_tab.all_overlays),
        len(clean_studio_tab.all_memes),
        len(clean_studio_tab.all_sfx),
        len(clean_studio_tab.all_bgm),
        len(clean_studio_tab.local_assets),
    )
    assert current_counts == bundled_counts


def test_none_vs_empty_list_and_dict(clean_studio_tab, monkeypatch):
    """Phân biệt rõ: None đọc từ StorageManager, trong khi [] và {} dùng dữ liệu caller cấp, không gọi IO."""
    io_called = {"ui_assets": 0, "taxonomy": 0}

    def fake_ui_assets(self):
        io_called["ui_assets"] += 1
        return [{"id": "io_asset", "name": "IO Asset", "tab": "sfx", "category": "sfx", "sub": "Tất cả"}]

    def fake_taxonomy(self):
        io_called["taxonomy"] += 1
        return {"custom_cat": "Danh mục tùy biến"}

    monkeypatch.setattr(StorageManager, "ui_assets", fake_ui_assets)
    monkeypatch.setattr(StorageManager, "taxonomy", fake_taxonomy)

    # 1. Cung cấp dữ liệu rõ ràng (kể cả rỗng) -> Không được gọi StorageManager IO
    clean_studio_tab.apply_storage_snapshot(managed_assets=[], taxonomy={})
    assert io_called["ui_assets"] == 0
    assert io_called["taxonomy"] == 0

    # 2. Để mặc định None -> Phải gọi StorageManager IO
    clean_studio_tab.apply_storage_snapshot(managed_assets=None, taxonomy=None)
    assert io_called["ui_assets"] == 1
    assert io_called["taxonomy"] == 1
    assert any(getattr(a, "id", "") == "io_asset" for a in clean_studio_tab.all_assets)


def test_metadata_preservation_and_sub_not_pack_name(clean_studio_tab):
    """Adapter bảo toàn đầy đủ metadata, đặc biệt sub KHÁC pack_name, tags là list độc lập, và paths đầy đủ."""
    tags_original = ["cinematic", "vlog", "action"]
    record = {
        "id": "meta_probe_01",
        "name": "Cinematic Drone Shot",
        "tab": "broll",
        "category": "broll",
        "subcategory": "Aerial",
        "pack_name": "Pro Aerial Vol 1",
        "pack_id": "pack_123",
        "source_kind": "managed",
        "source_id": "src_456",
        "template_type": None,
        "size_bytes": 1048576,
        "tags": tags_original,
        "availability": "cached",
        "file_path": "/var/media/shot.mp4",
        "thumbnail_path": "/var/media/shot.png",
        "preview_path": "/var/media/shot_proxy.mp4",
        "duration_sec": 12.5,
    }

    clean_studio_tab.apply_storage_snapshot(managed_assets=[record], taxonomy={"broll": "B-roll"})
    asset = next(a for a in clean_studio_tab.all_assets if getattr(a, "id", "") == "meta_probe_01")

    # Kiểm tra khôi phục sub độc lập với pack_name
    assert asset.sub == "Aerial"
    assert asset.pack_name == "Pro Aerial Vol 1"
    assert asset.pack_id == "pack_123"

    # Kiểm tra tags không chia sẻ mutable list
    assert asset.tags == tags_original
    assert asset.tags is not tags_original
    asset.tags.append("mutated_local")
    assert "mutated_local" not in tags_original

    # Kiểm tra paths và metadata khác
    assert asset.file_path == "/var/media/shot.mp4"
    assert asset.thumbnail_path == "/var/media/shot.png"
    assert asset.preview_path == "/var/media/shot_proxy.mp4"
    assert asset.availability == "cached"
    assert asset.storage_managed is True
    assert asset.source_kind == "managed"
    assert asset.source_id == "src_456"
    assert asset.size_bytes == 1048576
    assert asset.duration_sec == 12.5
    assert asset.duration == 12.5


def test_runtime_and_native_identity_and_unknown_duration(clean_studio_tab):
    """Bảo toàn tham chiếu runtime đối tượng theo identity (không deepcopy) và duration unknown an toàn."""
    class MockRuntimeObj:
        def __init__(self):
            self.tag = "no_deepcopy_handle"

    raw_runtime = MockRuntimeObj()
    native_preset_obj = MockRuntimeObj()

    # Asset 1: unknown duration (None), có runtime handles
    rec1 = {
        "id": "unknown_dur_01",
        "name": "Unknown Duration SFX",
        "tab": "sfx",
        "category": "sfx",
        "sub": "Impact",
        "duration_sec": None,
        "_raw_obj": raw_runtime,
        "native_preset": native_preset_obj,
    }
    # Asset 2: invalid duration (NaN / string / negative)
    rec2 = {
        "id": "invalid_dur_02",
        "name": "Invalid Duration Clip",
        "tab": "broll",
        "category": "broll",
        "sub": "Nature",
        "duration_sec": float("nan"),
    }

    clean_studio_tab.apply_storage_snapshot(managed_assets=[rec1, rec2], taxonomy={})
    a1 = next(a for a in clean_studio_tab.all_assets if getattr(a, "id", "") == "unknown_dur_01")
    a2 = next(a for a in clean_studio_tab.all_assets if getattr(a, "id", "") == "invalid_dur_02")

    # a1: Duration None không biến thành 3.0s giả
    assert a1.duration_sec is None
    assert a1.duration == 0.0
    assert getattr(a1, "_raw_obj") is raw_runtime
    assert getattr(a1, "native_preset") is native_preset_obj

    # a2: Duration NaN đưa về 0.0 an toàn, không ném exception hay crash Model
    assert a2.duration == 0.0


def test_old_snapshot_input_non_mutated(clean_studio_tab):
    """apply_storage_snapshot không làm biến đổi (mutate) các dict phần tử trong danh sách truyền vào."""
    original_dict = {
        "id": "immutable_test",
        "name": "Sample Asset",
        "tab": "lut",
        "category": "lut",
        "sub": "Vintage",
        "tags": ["film", "retro"],
    }
    input_list = [dict(original_dict)]

    clean_studio_tab.apply_storage_snapshot(managed_assets=input_list, taxonomy={})
    assert input_list[0] == original_dict


def test_duplicates_and_id_collision_rejected_before_mutation(clean_studio_tab):
    """Snapshot chứa ID trùng lặp hoặc va chạm với non-managed ID phải bị từ chối bằng ValueError và không phá dữ liệu cũ."""
    initial_assets = list(clean_studio_tab.all_assets)

    # 1. Trùng lặp ID nội bộ snapshot
    duplicate_snapshot = [
        {"id": "dup_01", "name": "Item A", "tab": "sfx", "category": "sfx"},
        {"id": "dup_01", "name": "Item B", "tab": "sfx", "category": "sfx"},
    ]
    with pytest.raises(ValueError, match="Duplicate asset ID in managed snapshot"):
        clean_studio_tab.apply_storage_snapshot(managed_assets=duplicate_snapshot, taxonomy={})

    # Dữ liệu cũ phải được bảo toàn nguyên vẹn
    assert clean_studio_tab.all_assets == initial_assets

    # 2. Va chạm ID với non-managed asset
    non_managed = StudioAsset(id="non_managed_lut_01", name="Custom Local LUT", tab="lut", category="custom")
    clean_studio_tab.all_assets.append(non_managed)
    collision_snapshot = [
        {"id": "non_managed_lut_01", "name": "Colliding LUT", "tab": "lut", "category": "lut"}
    ]
    with pytest.raises(ValueError, match="Asset ID collision"):
        clean_studio_tab.apply_storage_snapshot(managed_assets=collision_snapshot, taxonomy={})

    assert clean_studio_tab.all_assets == initial_assets + [non_managed]


def test_passive_signal_zero(clean_studio_tab):
    """apply_storage_snapshot là receiver thụ động, TUYỆT ĐỐI KHÔNG phát library_assets_changed."""
    emitted = []
    clean_studio_tab.library_assets_changed.connect(lambda assets: emitted.append(assets))

    snap = [
        {"id": "passive_01", "name": "Passive SFX", "tab": "sfx", "category": "sfx", "sub": "Tất cả"}
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap, taxonomy={})

    assert len(emitted) == 0, "apply_storage_snapshot không được phát library_assets_changed để tránh Echo Loop!"


def test_search_index_suggester_model_updated_with_new_content(clean_studio_tab):
    """SearchIndex, ContextualSuggester và AssetListModel đều được cập nhật nội dung snapshot mới."""
    snap = [
        {
            "id": "needle_shot_99",
            "name": "SuperUniqueKeywordFootage",
            "tab": "broll",
            "category": "broll",
            "sub": "Action",
            "tags": ["adrenaline", "speed"],
        }
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap, taxonomy={"broll": "B-roll"})

    # 1. SearchIndex
    matches = clean_studio_tab.search_index.search("superuniquekeywordfootage")
    assert len(matches) == 1
    assert matches[0]["asset"]["id"] == "needle_shot_99"

    # 2. ContextualSuggester
    assert any(getattr(a, "id", "") == "needle_shot_99" for a in clean_studio_tab.suggester.assets)

    # 3. AssetListModel
    src_idx = clean_studio_tab.asset_model.index_of_asset_id("needle_shot_99")
    assert src_idx.isValid()


def test_selection_updated_removed_and_filtered(clean_studio_tab):
    """Kiểm tra xử lý selection: cập nhật đối tượng mới nếu còn, dọn sạch nếu bị xóa khỏi kho hoặc bị lọc mất."""
    # Nhánh A: Đang chọn asset A, snapshot cập nhật asset A với tên mới
    snap_a = [
        {"id": "sel_test_a", "name": "Name Version 1", "tab": "text", "category": "text", "sub": "all"}
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap_a, taxonomy={})
    clean_studio_tab._filter_by_rail("text")

    # Chọn asset A
    clean_studio_tab._on_card_selected("sel_test_a")
    assert clean_studio_tab.selected_asset is not None
    assert clean_studio_tab.selected_asset.id == "sel_test_a"
    assert clean_studio_tab.selected_asset.name == "Name Version 1"

    # Cập nhật snapshot với Name Version 2
    snap_a_v2 = [
        {"id": "sel_test_a", "name": "Name Version 2", "tab": "text", "category": "text", "sub": "all"}
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap_a_v2, taxonomy={})
    assert clean_studio_tab.selected_asset is not None
    assert clean_studio_tab.selected_asset.id == "sel_test_a"
    assert clean_studio_tab.selected_asset.name == "Name Version 2"

    # Nhánh B: Asset A bị xóa khỏi snapshot -> selection trở thành None
    clean_studio_tab.apply_storage_snapshot(managed_assets=[], taxonomy={})
    assert clean_studio_tab.selected_asset is None

    # Nhánh C: Asset B tồn tại trong kho nhưng bị lọc mất bởi subcategory -> selection bị xóa, không chọn clip đầu thay thế
    snap_b = [
        {"id": "sel_test_b", "name": "SFX Boom", "tab": "sfx", "category": "sfx", "sub": "Whoosh"},
        {"id": "sel_test_c", "name": "SFX Click", "tab": "sfx", "category": "sfx", "sub": "UI"},
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap_b, taxonomy={})
    clean_studio_tab._filter_by_rail("sfx")
    clean_studio_tab._on_card_selected("sel_test_b")
    assert clean_studio_tab.selected_asset is not None
    assert clean_studio_tab.selected_asset.id == "sel_test_b"

    # Lọc sang sub UI (không chứa sel_test_b) và nạp snapshot -> sel_test_b bị che -> deselect
    clean_studio_tab.current_sub_cat = "UI"
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap_b, taxonomy={})
    assert clean_studio_tab.selected_asset is None, "Asset bị filter che phải deselect, KHÔNG được tự ý fallback chọn clip đầu!"


def test_valid_query_and_sub_preserved_stale_sub_reset(clean_studio_tab):
    """Giữ nguyên search query; sub còn hợp lệ thì giữ, sub bị mất thì reset về all."""
    clean_studio_tab.txt_search.setText("myquery")
    clean_studio_tab.current_rail_tab = "sfx"
    clean_studio_tab.current_sub_cat = "ValidSub"

    # Snapshot có sub ValidSub
    snap1 = [
        {"id": "sfx_01", "name": "S1", "tab": "sfx", "category": "sfx", "sub": "ValidSub"}
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap1, taxonomy={})
    assert clean_studio_tab.txt_search.text() == "myquery"
    assert clean_studio_tab.current_sub_cat == "ValidSub"

    # Snapshot mất sub ValidSub, thay bằng OtherSub
    snap2 = [
        {"id": "sfx_02", "name": "S2", "tab": "sfx", "category": "sfx", "sub": "OtherSub"}
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap2, taxonomy={})
    assert clean_studio_tab.txt_search.text() == "myquery"
    assert clean_studio_tab.current_sub_cat == "all"


def test_custom_rail_label_and_no_duplicate_buttons(clean_studio_tab):
    """Rail cập nhật nhãn tiếng Việt cho custom category và không tạo duplicate buttons."""
    initial_rail_count = len(clean_studio_tab.rail_btns)

    tax1 = {"project_x": "Dự án X"}
    snap1 = [
        {"id": "px_01", "name": "Clip 1", "tab": "project_x", "category": "project_x", "sub": "Tất cả"}
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap1, taxonomy=tax1)

    assert "project_x" in clean_studio_tab.rail_btns
    btn = clean_studio_tab.rail_btns["project_x"]
    assert "Dự án X" in btn.text()
    assert len(clean_studio_tab.rail_btns) == initial_rail_count + 1

    # Nạp tiếp snapshot thứ 2 cùng category project_x
    snap2 = [
        {"id": "px_02", "name": "Clip 2", "tab": "project_x", "category": "project_x", "sub": "Tất cả"}
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=snap2, taxonomy=tax1)
    assert len(clean_studio_tab.rail_btns) == initial_rail_count + 1
    assert clean_studio_tab.rail_btns["project_x"] is btn


def test_collision_with_non_managed_dict_rejected(clean_studio_tab):
    """G04-04A2a-R1: Va chạm ID với non-managed asset lưu dạng dict phải bị từ chối bằng ValueError trước khi mutate."""
    initial_sig = getattr(clean_studio_tab, "_last_storage_sig", None)
    bundled_dict = {
        "id": "bundled_dict_coll_01",
        "name": "Bundled Dict Asset",
        "tab": "text",
        "category": "text",
        "storage_managed": False,
    }
    clean_studio_tab.all_assets.append(bundled_dict)
    initial_assets = list(clean_studio_tab.all_assets)

    colliding_snap = [
        {"id": "bundled_dict_coll_01", "name": "Managed Trying Same ID", "tab": "text", "category": "text"}
    ]
    with pytest.raises(ValueError, match="Asset ID collision"):
        clean_studio_tab.apply_storage_snapshot(managed_assets=colliding_snap, generation_sig="bad_sig_collision")

    # Dữ liệu cũ và signature phải được bảo toàn nguyên vẹn
    assert clean_studio_tab.all_assets == initial_assets
    assert clean_studio_tab._last_storage_sig == initial_sig


def test_invalid_record_or_missing_id_rejected_preserves_state(clean_studio_tab):
    """G04-04A2a-R1: Bản ghi không phải Mapping hoặc ID rỗng/thiếu/sai kiểu phải reject và không đổi signature."""
    initial_sig = "initial_sig_checkpoint"
    clean_studio_tab._last_storage_sig = initial_sig
    initial_assets = list(clean_studio_tab.all_assets)

    # 1. Phần tử không phải Mapping / dict
    with pytest.raises(ValueError, match="Mapping"):
        clean_studio_tab.apply_storage_snapshot(managed_assets=["not-a-dict"], generation_sig="bad_sig_1")
    assert clean_studio_tab._last_storage_sig == initial_sig
    assert clean_studio_tab.all_assets == initial_assets

    # 2. ID rỗng
    with pytest.raises(ValueError, match="non-empty str 'id'"):
        clean_studio_tab.apply_storage_snapshot(managed_assets=[{"id": "", "name": "Empty"}], generation_sig="bad_sig_2")
    assert clean_studio_tab._last_storage_sig == initial_sig
    assert clean_studio_tab.all_assets == initial_assets

    # 3. ID là None hoặc không phải str
    with pytest.raises(ValueError, match="non-empty str 'id'"):
        clean_studio_tab.apply_storage_snapshot(managed_assets=[{"id": None, "name": "None ID"}], generation_sig="bad_sig_3")
    assert clean_studio_tab._last_storage_sig == initial_sig
    assert clean_studio_tab.all_assets == initial_assets

    # 4. Trùng lặp nội bộ
    with pytest.raises(ValueError, match="Duplicate asset ID"):
        clean_studio_tab.apply_storage_snapshot(
            managed_assets=[{"id": "dup_key", "name": "1"}, {"id": "dup_key", "name": "2"}],
            generation_sig="bad_sig_4"
        )
    assert clean_studio_tab._last_storage_sig == initial_sig
    assert clean_studio_tab.all_assets == initial_assets


def test_selected_non_managed_dict_preserved_after_apply(clean_studio_tab):
    """G04-04A2a-R1: Selected asset là non-managed dict vẫn giữ đúng tham chiếu khi visible sau snapshot."""
    dict_item = {
        "id": "dict_keep_sel",
        "name": "Dict Selected Clip",
        "tab": "text",
        "category": "text",
        "sub": "Kinetic",
        "storage_managed": False,
    }
    clean_studio_tab.all_assets.append(dict_item)
    clean_studio_tab._populate_cards()
    clean_studio_tab._filter_by_rail("text")

    src_idx = clean_studio_tab.asset_model.index_of_asset_id("dict_keep_sel")
    assert src_idx.isValid()
    proxy_idx = clean_studio_tab.asset_filter.mapFromSource(src_idx)
    assert proxy_idx.isValid()

    clean_studio_tab.selected_asset = dict_item
    clean_studio_tab.asset_grid.setCurrentIndex(proxy_idx)

    # Nạp snapshot managed hợp lệ
    valid_snap = [
        {"id": "other_managed_item", "name": "Other Item", "tab": "sfx", "category": "sfx"}
    ]
    clean_studio_tab.apply_storage_snapshot(managed_assets=valid_snap, generation_sig="success_sig_123")

    # Signature được cập nhật sau khi apply thành công
    assert clean_studio_tab._last_storage_sig == "success_sig_123"
    # Selected asset phải giữ đúng object identity của dict_item
    assert clean_studio_tab.selected_asset is dict_item

