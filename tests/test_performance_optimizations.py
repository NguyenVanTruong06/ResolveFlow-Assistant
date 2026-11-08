"""
Kiểm thử hiệu năng đa nền tảng (Windows & Linux) và chống treo luồng chính (UI Freeze / Not Responding).
Đảm bảo các đường dẫn nóng (StorageManager, PackManager, AssetFilterProxyModel, AssetListModel,
TextLivePreviewWidget, TransitionLoopWidget, ResolveWindowAnchor) hoạt động tức thời và không hồi quy.
"""
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from src.core.storage_manager import StorageManager, atomic_json
from src.core.pack_manager import PackManager, COMMUNITY_PACKS_CATALOG
from src.ui.lux.library.asset_model import AssetListModel, ThumbnailRole
from src.ui.lux.library.asset_filter import AssetFilterProxyModel
from src.services.search_index import SearchIndex


def test_storage_manager_ui_assets_memoization_speed(tmp_path):
    """StorageManager.ui_assets() với 1,500+ mẫu thuộc các gói dùng chung .drfx chạy < 250ms."""
    cfg_dir = tmp_path / "cfg"
    lib_root = tmp_path / "lib_root"
    pack_dir = lib_root / "02_cloud_downloads" / "packs" / "pack_cine"
    pack_dir.mkdir(parents=True, exist_ok=True)

    drfx_file = pack_dir / "bundle.drfx"
    drfx_file.write_bytes(b"PK\x03\x04dummy")
    preview_file = pack_dir / "preview.mp4"
    preview_file.write_bytes(b"\x00\x00\x00\x20ftypmp42")

    sm = StorageManager(config_dir=cfg_dir, reserve_bytes=0)
    sm.configure(str(lib_root))

    # Tạo 1,500 assets dùng chung pack và .drfx
    assets_list = []
    for i in range(1500):
        aid = f"trans_item_{i:04d}"
        assets_list.append(
            {
                "id": aid,
                "name": f"Transition Cine {i}",
                "category": "trans",
                "sub_category": "Motion",
                "pack_id": "pack_cine",
                "pack_name": "Cine Pack",
                "library_path": "02_cloud_downloads/packs/pack_cine/bundle.drfx",
                "preview_relative_path": "preview.mp4",
                "source_kind": "pack",
            }
        )

    packs_list = [
        {
            "id": "pack_cine",
            "pack_id": "pack_cine",
            "name": "Cine Pack",
            "category": "trans",
            "folder": "02_cloud_downloads/packs/pack_cine",
        }
    ]
    atomic_json(
        sm.meta / "registry.json",
        {"schema_version": 1, "packs": packs_list, "assets": assets_list},
    )

    t0 = time.perf_counter()
    strict_items = sm.ui_assets()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert len(strict_items) == 1500
    assert strict_items[0]["availability"] == "online"
    assert strict_items[0]["preview_path"] == str(preview_file)
    assert elapsed_ms < 250.0, f"ui_assets() quá chậm cho 1,500 mẫu: {elapsed_ms:.1f}ms"


def test_pack_manager_cached_lookup_and_invalidation(tmp_path, monkeypatch):
    """PackManager.is_pack_installed() cho 18 gói chạy < 15ms nhờ bộ đệm mtime và tự làm mới khi ghi/xóa."""
    installed_json = tmp_path / "installed_packs.json"
    monkeypatch.setattr(PackManager, "INSTALLED_PACKS_FILE", installed_json)
    PackManager._invalidate_caches()

    # Giả lập StorageManager rỗng để cô lập bài test
    monkeypatch.setattr(PackManager, "_get_storage_registry_packs", classmethod(lambda cls: (None, [])))

    folder = tmp_path / "pack_folder"
    folder.mkdir(parents=True, exist_ok=True)
    sample_wav = folder / "sample.wav"
    sample_wav.write_bytes(b"RIFF")

    PackManager.record_installed_pack(
        "sfx_whoosh_pack_2026",
        {"name": "Whoosh Pack", "category": "sfx", "installed_paths": [str(sample_wav)]},
    )
    assert PackManager.is_pack_installed("sfx_whoosh_pack_2026") is True

    # Chạy 18 lần kiểm tra như AssetHubWidget.refresh_cards()
    t0 = time.perf_counter()
    for manifest in COMMUNITY_PACKS_CATALOG:
        PackManager.is_pack_installed(manifest.id)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 15.0, f"18x PackManager.is_pack_installed quá chậm: {elapsed_ms:.2f}ms"

    # Gỡ cài đặt phải làm mới cache ngay lập tức
    PackManager.uninstall_pack("sfx_whoosh_pack_2026")
    assert PackManager.is_pack_installed("sfx_whoosh_pack_2026") is False


def test_asset_filter_proxy_batch_and_fast_row_filtering(qapp):
    """AssetFilterProxyModel.set_filters_batch() chỉ invalidate 1 lần và lọc 2,000 mẫu < 50ms."""
    raw_assets = [
        {
            "id": f"asset_{i}",
            "name": f"Sample Asset {i}",
            "category": "sfx" if i % 2 == 0 else "lut",
            "sub_category": "whoosh" if i % 4 == 0 else "cinema",
            "pack_name": "Starter Pack" if i % 3 == 0 else "Mặc định",
            "tags": ["fast", "cinematic"] if i % 5 == 0 else ["clean"],
        }
        for i in range(2000)
    ]

    model = AssetListModel(raw_assets)
    search_idx = SearchIndex(raw_assets)
    proxy = AssetFilterProxyModel(search_index=search_idx)
    proxy.setSourceModel(model)

    t0 = time.perf_counter()
    proxy.set_filters_batch(
        category="sfx",
        sub_category="whoosh",
        filter_mode="all",
        search_query="",
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert proxy.rowCount() == 500
    assert elapsed_ms < 50.0, f"Lọc 2,000 mẫu qua AssetFilterProxyModel quá chậm: {elapsed_ms:.2f}ms"


def test_preview_widgets_pause_30fps_timers_when_hidden(qapp):
    """TextLivePreviewWidget và TransitionLoopWidget tự dừng timer 30 FPS khi ẩn (hide) và chạy lại khi hiện (show)."""
    from src.ui.tabs.tab_assets import TextLivePreviewWidget, TransitionLoopWidget

    txt_w = TextLivePreviewWidget()
    trans_w = TransitionLoopWidget()

    txt_w.show()
    trans_w.show()
    assert txt_w.anim_timer.isActive() is True
    assert trans_w.anim_timer.isActive() is True

    txt_w.hide()
    trans_w.hide()
    assert txt_w.anim_timer.isActive() is False
    assert trans_w.anim_timer.isActive() is False

    txt_w.show()
    trans_w.show()
    assert txt_w.anim_timer.isActive() is True
    assert trans_w.anim_timer.isActive() is True

    txt_w.close()
    trans_w.close()


def test_asset_model_coalesces_duplicate_video_thumbnails_and_negative_caches_failures(qapp, tmp_path, monkeypatch):
    """Nhiều mẫu dùng chung 1 video chỉ gọi extract bất đồng bộ 1 lần và không lặp vô hạn khi lỗi."""
    shared_video = tmp_path / "shared_preview.mp4"
    shared_video.write_bytes(b"corrupt_video_bytes")

    captured_callbacks = []

    monkeypatch.setattr("src.core.asset_cache.get_thumb_cache_dir", lambda: str(tmp_path / "thumbs"))
    monkeypatch.setattr(
        "src.core.asset_cache.request_thumbnail_async",
        lambda fp, cb=None: captured_callbacks.append((fp, cb)),
    )

    assets = [
        {"id": "t1", "name": "Trans 1", "category": "trans", "file_path": str(shared_video)},
        {"id": "t2", "name": "Trans 2", "category": "trans", "file_path": str(shared_video)},
    ]
    model = AssetListModel(assets)

    # Cả 2 item cùng yêu cầu ThumbnailRole
    _ = model.data(model.index(0, 0), ThumbnailRole)
    _ = model.data(model.index(1, 0), ThumbnailRole)

    # Chỉ gửi đúng 1 yêu cầu extract_thumbnail cho shared_video
    assert len(captured_callbacks) == 1

    # Giả lập trích xuất thất bại (thumb_path == "")
    _, cb = captured_callbacks[0]
    cb("")

    # Yêu cầu lại ThumbnailRole không được gửi thêm job mới (negative cache)
    _ = model.data(model.index(0, 0), ThumbnailRole)
    assert len(captured_callbacks) == 1
