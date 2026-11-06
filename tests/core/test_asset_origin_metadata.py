"""
tests/core/test_asset_origin_metadata.py
Bộ kiểm thử TDD cho G04-04C2-P-I: Bảo toàn origin và trường nguồn trước guard.
"""
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
import pytest

from src.core.storage_manager import StorageManager, atomic_json
from src.core.contracts.asset_dto import (
    to_ui_asset,
    to_persisted_record,
    UIAssetView,
)
from src.ui.tabs.tab_assets import TabAssets, StudioAsset
from src.services.insert_service import to_asset_dict


@pytest.fixture
def temp_storage_env(tmp_path):
    """Tạo môi trường kho giả lập tạm thời với config và root riêng biệt."""
    config_dir = tmp_path / "config"
    root_dir = tmp_path / "library_a"
    config_dir.mkdir(parents=True, exist_ok=True)
    root_dir.mkdir(parents=True, exist_ok=True)

    meta_dir = root_dir / ".chundvc_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)

    lib_id = "lib_alpha_001"
    marker = {"schema_version": 1, "library_id": lib_id}
    atomic_json(meta_dir / "library.json", marker)

    cfg = {
        "root": str(root_dir),
        "library_id": lib_id,
        "reserve_bytes": 1024 * 1024,
    }
    atomic_json(config_dir / "storage.json", cfg)

    # Tạo file media thật
    pack_dir = root_dir / "pack_sample"
    pack_dir.mkdir(parents=True, exist_ok=True)
    media_file = pack_dir / "clip_a.mp4"
    media_file.write_bytes(b"dummy video data")

    # Tạo registry
    registry_data = {
        "schema_version": 1,
        "packs": [
            {
                "id": "pack_sample",
                "name": "Sample Pack",
                "category": "broll",
            }
        ],
        "assets": [
            {
                "schema_version": 1,
                "id": "asset_managed_01",
                "name": "Sample Clip A",
                "category": "broll",
                "pack_id": "pack_sample",
                "library_path": "pack_sample/clip_a.mp4",
                "relative_path": "pack_sample/clip_a.mp4",
                "source_kind": "managed",
                "size_bytes": len(b"dummy video data"),
            },
            {
                "schema_version": 1,
                "id": "asset_linked_01",
                "name": "Linked Broll",
                "category": "broll",
                "source_id": "ext_src_1",
                "external_path": "/tmp/external_media.mp4",
                "source_kind": "linked",
                "size_bytes": 100,
            },
        ],
    }
    atomic_json(meta_dir / "registry.json", registry_data)

    sm = StorageManager(config_dir=config_dir)
    return {
        "sm": sm,
        "config_dir": config_dir,
        "root_dir": root_dir,
        "lib_id": lib_id,
        "meta_dir": meta_dir,
        "registry_data": registry_data,
        "media_file": media_file,
    }


def test_online_managed_and_linked_runtime_origin(temp_storage_env):
    """1. Online: assets() gắn đúng origin_library_id và origin_root vào bản ghi runtime."""
    sm = temp_storage_env["sm"]
    lib_id = temp_storage_env["lib_id"]
    root_dir = temp_storage_env["root_dir"]

    assert sm.online is True
    assets = sm.assets()
    assert len(assets) == 2

    managed_item = next(a for a in assets if a["id"] == "asset_managed_01")
    assert managed_item.get("origin_library_id") == lib_id
    assert managed_item.get("origin_root") == str(root_dir)
    assert managed_item.get("source_kind") == "managed"
    assert managed_item.get("availability") == "online"

    linked_item = next(a for a in assets if a["id"] == "asset_linked_01")
    assert linked_item.get("origin_library_id") == lib_id
    assert linked_item.get("origin_root") == str(root_dir)
    assert linked_item.get("source_kind") == "linked"


def test_raw_registry_not_mutated(temp_storage_env):
    """2. assets() không mutate registry trong bộ nhớ và không ghi origin vào registry.json."""
    sm = temp_storage_env["sm"]
    meta_dir = temp_storage_env["meta_dir"]

    _ = sm.assets()

    # Kiểm tra registry trên đĩa
    with open(meta_dir / "registry.json", "r", encoding="utf-8") as f:
        disk_registry = json.load(f)

    for item in disk_registry["assets"]:
        assert "origin_library_id" not in item
        assert "origin_root" not in item

    # Kiểm tra registry() object
    mem_registry = sm.registry()
    for item in mem_registry["assets"]:
        assert "origin_library_id" not in item
        assert "origin_root" not in item


def test_local_snapshot_envelope_origin(temp_storage_env):
    """3. snapshot() lưu origin_library_id và origin_root ở envelope của file snapshot cục bộ."""
    sm = temp_storage_env["sm"]
    lib_id = temp_storage_env["lib_id"]
    root_dir = temp_storage_env["root_dir"]

    sm.snapshot()

    snap_file = sm.config_dir / f"snapshot-{lib_id}.json"
    assert snap_file.exists()

    with open(snap_file, "r", encoding="utf-8") as f:
        snap_content = json.load(f)

    assert snap_content.get("origin_library_id") == lib_id
    assert snap_content.get("origin_root") == str(root_dir)
    assert "registry" in snap_content

    # Không ghi origin vào bên trong registry.assets của snapshot
    for item in snap_content["registry"]["assets"]:
        assert "origin_library_id" not in item
        assert "origin_root" not in item


def test_offline_legacy_snapshot_unknown_origin_not_fabricated(tmp_path):
    """4. Offline với snapshot legacy (không có origin trong envelope): origin là None, không bịa từ config."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_legacy_999"

    # Tạo config trỏ tới một root không tồn tại (offline)
    cfg = {
        "root": "/nonexistent/path/legacy",
        "library_id": lib_id,
        "reserve_bytes": 1024,
    }
    atomic_json(config_dir / "storage.json", cfg)

    # Tạo legacy snapshot KHÔNG CÓ origin_library_id / origin_root trong envelope
    legacy_snap = {
        "registry": {
            "packs": [],
            "assets": [
                {
                    "schema_version": 1,
                    "id": "legacy_clip_01",
                    "name": "Legacy Clip",
                    "category": "broll",
                    "library_path": "legacy/clip.mp4",
                    "source_kind": "managed",
                    "size_bytes": 500,
                }
            ],
        },
        "taxonomy": {},
        "links": {},
    }
    atomic_json(config_dir / f"snapshot-{lib_id}.json", legacy_snap)

    sm = StorageManager(config_dir=config_dir)
    assert sm.online is False

    assets = sm.assets()
    assert len(assets) == 1
    item = assets[0]
    # Origin không được tự tiện lấy từ cfg (/nonexistent/path/legacy)
    assert item.get("origin_library_id") is None
    assert item.get("origin_root") is None
    assert item.get("availability") == "offline"


def test_offline_new_snapshot_preserves_origin_after_config_switch(temp_storage_env, tmp_path):
    """5. Snapshot A giữ origin A ngay cả khi config đã chuyển sang Root B."""
    sm_a = temp_storage_env["sm"]
    lib_a = temp_storage_env["lib_id"]
    root_a = temp_storage_env["root_dir"]
    config_dir = temp_storage_env["config_dir"]

    # Chụp snapshot A
    sm_a.snapshot()

    # Chuyển cấu hình storage.json sang Kho B
    root_b = tmp_path / "library_b"
    root_b.mkdir(parents=True, exist_ok=True)
    lib_b = "lib_beta_002"
    atomic_json(config_dir / "storage.json", {
        "root": str(root_b),
        "library_id": lib_b,
        "reserve_bytes": 1024,
    })

    # Tạo sm đại diện đọc snapshot A offline
    # Mock config_dir với library_id = lib_a để đọc snapshot-lib_a
    sm_offline_a = StorageManager(config_dir=config_dir)
    sm_offline_a._config["library_id"] = lib_a
    sm_offline_a._config["root"] = str(root_b)  # root config đang là B

    # Giả lập offline cho library A
    assets_a = sm_offline_a.assets()
    assert len(assets_a) == 2
    item_a = next(a for a in assets_a if a["id"] == "asset_managed_01")

    # Phải giữ origin của A, không lấy root/lib của B!
    assert item_a.get("origin_library_id") == lib_a
    assert item_a.get("origin_root") == str(root_a)
    assert item_a.get("origin_root") != str(root_b)


def test_same_path_marker_and_config_swap_origin_tracking(temp_storage_env):
    """6. Tráo marker & config trên cùng thư mục: asset cũ giữ old origin, nạp mới có new origin."""
    sm = temp_storage_env["sm"]
    meta_dir = temp_storage_env["meta_dir"]
    config_dir = temp_storage_env["config_dir"]
    root_dir = temp_storage_env["root_dir"]
    lib_old = temp_storage_env["lib_id"]

    # 1. Nạp tài nguyên lần đầu vào UI
    old_assets = sm.ui_assets()
    old_item = old_assets[0]
    assert old_item.get("origin_library_id") == lib_old

    # 2. Người dùng đổi sang library_id mới trên CÙNG thư mục root_dir
    lib_new = "lib_alpha_002_swapped"
    atomic_json(meta_dir / "library.json", {"schema_version": 1, "library_id": lib_new})
    atomic_json(config_dir / "storage.json", {
        "root": str(root_dir),
        "library_id": lib_new,
        "reserve_bytes": 1024,
    })

    sm_new = StorageManager(config_dir=config_dir)
    assert sm_new.online is True

    new_assets = sm_new.ui_assets()
    new_item = new_assets[0]

    # Bản ghi cũ trong bộ nhớ vẫn giữ nguyên origin cũ
    assert old_item.get("origin_library_id") == lib_old
    # Bản ghi mới nạp mang origin mới
    assert new_item.get("origin_library_id") == lib_new
    assert old_item.get("origin_library_id") != new_item.get("origin_library_id")


def test_dto_studio_asset_to_asset_dict_conservation():
    """7. Bảo toàn origin và các trường paths/source_kind qua DTO -> StudioAsset -> to_asset_dict."""
    ui_record = {
        "id": "asset_test_01",
        "name": "Test Clip",
        "category": "broll",
        "library_path": "packs/test/clip.mp4",
        "external_path": "/ext/clip.mp4",
        "relative_path": "packs/test/clip.mp4",
        "source_id": "src_42",
        "pack_id": "pack_99",
        "source_kind": "managed",
        "origin_library_id": "lib_origin_777",
        "origin_root": "/media/storage/lib_a",
        "file_path": "/media/storage/lib_a/packs/test/clip.mp4",
        "availability": "online",
        "_raw_obj": "runtime_handle_reference",
    }

    # 1. Qua to_ui_asset
    ui_asset = to_ui_asset(ui_record)
    assert ui_asset["origin_library_id"] == "lib_origin_777"
    assert ui_asset["origin_root"] == "/media/storage/lib_a"
    assert ui_asset["library_path"] == "packs/test/clip.mp4"
    assert ui_asset["external_path"] == "/ext/clip.mp4"
    assert ui_asset["relative_path"] == "packs/test/clip.mp4"

    # 2. Qua TabAssets._dict_to_studio_asset
    tab_assets = TabAssets()
    studio_asset = tab_assets._dict_to_studio_asset(ui_asset)

    assert getattr(studio_asset, "origin_library_id", None) == "lib_origin_777"
    assert getattr(studio_asset, "origin_root", None) == "/media/storage/lib_a"
    assert getattr(studio_asset, "library_path", None) == "packs/test/clip.mp4"
    assert getattr(studio_asset, "external_path", None) == "/ext/clip.mp4"
    assert getattr(studio_asset, "relative_path", None) == "packs/test/clip.mp4"
    assert getattr(studio_asset, "_raw_obj", None) == "runtime_handle_reference"

    # 3. Qua to_asset_dict (đầu vào của InsertService mà không sửa InsertService)
    final_dict = to_asset_dict(studio_asset)
    assert final_dict.get("origin_library_id") == "lib_origin_777"
    assert final_dict.get("origin_root") == "/media/storage/lib_a"
    assert final_dict.get("library_path") == "packs/test/clip.mp4"
    assert final_dict.get("external_path") == "/ext/clip.mp4"
    assert final_dict.get("relative_path") == "packs/test/clip.mp4"
    assert final_dict.get("source_kind") == "managed"
    # to_asset_dict gán _raw_obj là chính StudioAsset, và StudioAsset lưu giữ runtime reference gốc
    assert final_dict.get("_raw_obj") is studio_asset
    assert getattr(final_dict.get("_raw_obj"), "_raw_obj", None) == "runtime_handle_reference"


def test_strict_persisted_serializer_strips_origins():
    """8. to_persisted_record loại bỏ origin_library_id, origin_root và các runtime fields."""
    record = {
        "schema_version": 1,
        "id": "asset_persist_01",
        "name": "Persist Clip",
        "category": "broll",
        "source_kind": "managed",
        "pack_id": "pack_1",
        "library_path": "pack_1/video.mp4",
        "relative_path": "pack_1/video.mp4",
        "size_bytes": 1024,
        # Runtime fields
        "origin_library_id": "lib_temp_999",
        "origin_root": "/some/root",
        "file_path": "/some/root/pack_1/video.mp4",
        "availability": "online",
        "_raw_obj": "runtime_handle",
    }

    persisted = to_persisted_record(record)
    assert "origin_library_id" not in persisted
    assert "origin_root" not in persisted
    assert "file_path" not in persisted
    assert "availability" not in persisted
    assert "_raw_obj" not in persisted
    assert persisted["id"] == "asset_persist_01"


def test_source_kind_inference_missing_vs_explicit(tmp_path):
    """9. Suy luận source_kind runtime trên production StorageManager.assets(): external_path -> linked, library_path -> managed."""
    config_dir = tmp_path / "config"
    root_dir = tmp_path / "lib_root"
    config_dir.mkdir(parents=True, exist_ok=True)
    root_dir.mkdir(parents=True, exist_ok=True)

    meta_dir = root_dir / ".chundvc_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_infer_001"
    atomic_json(meta_dir / "library.json", {"schema_version": 1, "library_id": lib_id})
    atomic_json(config_dir / "storage.json", {"root": str(root_dir), "library_id": lib_id, "reserve_bytes": 1024})

    # File media managed
    pack_dir = root_dir / "pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "clip.mp4").write_bytes(b"media")

    # Case A: Thiếu source_kind, có external_path hợp lệ -> linked
    item_linked = {
        "id": "a1",
        "name": "Clip 1",
        "category": "broll",
        "external_path": "/var/media/ext.mp4",
        "size_bytes": 100,
    }
    # Case B: Thiếu source_kind, có library_path hợp lệ -> managed
    item_managed = {
        "id": "a2",
        "name": "Clip 2",
        "category": "broll",
        "library_path": "pack/clip.mp4",
        "size_bytes": 200,
    }
    # Case C: Đã có source_kind = builtin -> giữ nguyên
    item_builtin = {
        "id": "a3",
        "name": "Clip 3",
        "category": "broll",
        "library_path": "pack/clip.mp4",
        "source_kind": "builtin",
        "size_bytes": 300,
    }

    atomic_json(meta_dir / "registry.json", {
        "schema_version": 1,
        "packs": [],
        "assets": [item_linked, item_managed, item_builtin],
    })

    sm = StorageManager(config_dir=config_dir)
    res = sm.assets()

    assert len(res) == 3
    assert res[0]["source_kind"] == "linked"
    assert res[1]["source_kind"] == "managed"
    assert res[2]["source_kind"] == "builtin"


def test_integration_pack_record_missing_source_kind_runtime_managed(tmp_path):
    """10. Tích hợp: Pack ingest prepare/commit, bản ghi thiếu source_kind vẫn được assets() suy luận runtime managed đúng."""
    import zipfile
    from src.core.pack_ingest import PackIngestService

    sm = StorageManager(config_dir=tmp_path / "config", reserve_bytes=0)
    sm.configure(tmp_path / "lib_root")
    lib_id = sm._config["library_id"]

    # Ingest zip pack
    archive = tmp_path / "test_pack.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("city/shot.mp4", b"video footage")

    svc = PackIngestService(sm)
    job = svc.prepare(archive)
    svc.commit(job)

    # Giả lập bản ghi thiếu source_kind trong registry disk (như dữ liệu pack ingest cũ)
    disk_reg = sm.registry()
    for a in disk_reg["assets"]:
        a.pop("source_kind", None)
    atomic_json(sm.meta / "registry.json", disk_reg)

    assets = sm.assets()
    assert len(assets) >= 1
    shot = assets[0]
    assert shot["source_kind"] == "managed"
    assert shot["origin_library_id"] == lib_id
    assert shot["origin_root"] == str(sm.root)
    assert shot["availability"] == "online"
