"""
tests/core/test_storage_assets_strict.py
=============================================================================
Finite TDD Unit Test Suite cho StorageManager.assets(*, raise_on_error: bool = False):
- Kiểm tra kiểu dữ liệu của cờ raise_on_error (chỉ nhận strict bool).
- Kiểm tra nhánh legacy (raise_on_error=False): bảo toàn bắt lỗi và trả về [].
- Kiểm tra nhánh strict (raise_on_error=True):
  * Kho chưa cấu hình -> ném StorageOfflineError.
  * Kho ở trạng thái StorageState.ERROR -> ném OSError.
  * Kho online nhưng thiếu registry.json -> ném StorageOfflineError.
  * Kho online nhưng registry.json bị corrupt / oversize / sai cấu trúc envelope -> ném ValueError.
  * Kho online hợp lệ catalog rỗng -> trả về [].
  * Kho online hợp lệ có assets -> trả về danh sách records đầy đủ metadata.
  * Kho offline không có snapshot -> ném StorageOfflineError.
  * Kho offline có snapshot nhưng lệch origin_library_id -> ném StorageOfflineError.
  * Kho offline có snapshot corrupt / sai envelope -> ném ValueError.
  * Kho offline có snapshot hợp lệ -> trả về danh sách records availability='offline'.
=============================================================================
"""

import json
import pytest
from pathlib import Path

from src.core.storage_manager import (
    MAX_JSON,
    StorageManager,
    StorageOfflineError,
    StorageState,
)


def _setup_mock_library(root: Path, lib_id: str = "lib_test_01") -> None:
    """Tạo thư mục kho hợp lệ kèm marker library.json."""
    meta = root / ".chundvc_meta"
    meta.mkdir(parents=True, exist_ok=True)
    marker = {"library_id": lib_id, "schema_version": 1}
    (meta / "library.json").write_text(json.dumps(marker), encoding="utf-8")


def test_assets_raise_on_error_flag_typegate(tmp_path: Path):
    sm = StorageManager(config_dir=tmp_path / "cfg")
    # Gửi int thay vì bool
    with pytest.raises(ValueError, match="raise_on_error bắt buộc phải là kiểu bool thuần túy"):
        sm.assets(raise_on_error=1)  # type: ignore

    # Gửi string thay vì bool
    with pytest.raises(ValueError, match="raise_on_error bắt buộc phải là kiểu bool thuần túy"):
        sm.assets(raise_on_error="True")  # type: ignore


def test_assets_unconfigured_storage(tmp_path: Path):
    cfg_dir = tmp_path / "cfg_unconfigured"
    sm = StorageManager(config_dir=cfg_dir)

    # Legacy: trả về []
    assert sm.assets(raise_on_error=False) == []

    # Strict: ném StorageOfflineError
    with pytest.raises(StorageOfflineError, match="chưa được cấu hình"):
        sm.assets(raise_on_error=True)


def test_assets_online_corrupt_registry(tmp_path: Path):
    lib_dir = tmp_path / "lib_online"
    cfg_dir = tmp_path / "cfg_online"
    lib_id = "lib_online_test"
    _setup_mock_library(lib_dir, lib_id)

    sm = StorageManager(config_dir=cfg_dir)
    sm._config = {"root": str(lib_dir), "library_id": lib_id, "reserve_bytes": 0}

    # Ghi registry.json bị hỏng cú pháp JSON
    reg_file = sm.meta / "registry.json"
    reg_file.write_text("{ corrupt json data ::::", encoding="utf-8")

    # Legacy: nuốt lỗi trả []
    assert sm.assets(raise_on_error=False) == []

    # Strict: ném ValueError có thông điệp rõ ràng
    with pytest.raises(ValueError, match="JSON bị hỏng"):
        sm.assets(raise_on_error=True)


def test_assets_online_oversized_registry(tmp_path: Path):
    lib_dir = tmp_path / "lib_oversize"
    cfg_dir = tmp_path / "cfg_oversize"
    lib_id = "lib_oversize_test"
    _setup_mock_library(lib_dir, lib_id)

    sm = StorageManager(config_dir=cfg_dir)
    sm._config = {"root": str(lib_dir), "library_id": lib_id, "reserve_bytes": 0}

    # Giả lập registry stat vượt quá MAX_JSON (16 MiB)
    reg_file = sm.meta / "registry.json"
    reg_file.write_text("{}", encoding="utf-8")

    class MockStat:
        st_size = MAX_JSON + 1024

    orig_stat = Path.stat
    try:
        def mock_stat(self_path, *args, **kwargs):
            if self_path == reg_file:
                return MockStat()
            return orig_stat(self_path, *args, **kwargs)

        Path.stat = mock_stat
        with pytest.raises(ValueError, match="quá lớn"):
            sm.assets(raise_on_error=True)
    finally:
        Path.stat = orig_stat


def test_assets_online_malformed_envelope(tmp_path: Path):
    lib_dir = tmp_path / "lib_envelope"
    cfg_dir = tmp_path / "cfg_envelope"
    lib_id = "lib_envelope_test"
    _setup_mock_library(lib_dir, lib_id)

    sm = StorageManager(config_dir=cfg_dir)
    sm._config = {"root": str(lib_dir), "library_id": lib_id, "reserve_bytes": 0}

    # Envelope thiếu assets list
    reg_file = sm.meta / "registry.json"
    reg_file.write_text(json.dumps({"packs": []}), encoding="utf-8")

    # Legacy: nuốt lỗi trả []
    assert sm.assets(raise_on_error=False) == []

    # Strict: ném ValueError
    with pytest.raises(ValueError, match="Registry bị lỗi cấu trúc envelope"):
        sm.assets(raise_on_error=True)


def test_assets_online_missing_registry_file(tmp_path: Path):
    lib_dir = tmp_path / "lib_missing_reg"
    cfg_dir = tmp_path / "cfg_missing_reg"
    lib_id = "lib_missing_reg_test"
    _setup_mock_library(lib_dir, lib_id)

    sm = StorageManager(config_dir=cfg_dir)
    sm._config = {"root": str(lib_dir), "library_id": lib_id, "reserve_bytes": 0}

    # Không tạo file registry.json
    reg_file = sm.meta / "registry.json"
    if reg_file.exists():
        reg_file.unlink()

    # Legacy: trả []
    assert sm.assets(raise_on_error=False) == []

    # Strict: ném StorageOfflineError
    with pytest.raises(StorageOfflineError, match="Không tìm thấy tệp metadata bắt buộc"):
        sm.assets(raise_on_error=True)


def test_assets_online_valid_empty_and_populated(tmp_path: Path):
    lib_dir = tmp_path / "lib_valid"
    cfg_dir = tmp_path / "cfg_valid"
    lib_id = "lib_valid_test"
    _setup_mock_library(lib_dir, lib_id)

    sm = StorageManager(config_dir=cfg_dir)
    sm._config = {"root": str(lib_dir), "library_id": lib_id, "reserve_bytes": 0}

    # 1. Valid empty catalog
    reg_file = sm.meta / "registry.json"
    reg_file.write_text(json.dumps({"packs": [], "assets": []}), encoding="utf-8")

    assert sm.assets(raise_on_error=False) == []
    assert sm.assets(raise_on_error=True) == []

    # 2. Valid populated catalog
    master_file = lib_dir / "memes" / "funny.mp4"
    master_file.parent.mkdir(parents=True, exist_ok=True)
    master_file.write_bytes(b"dummy video content")

    asset_item = {
        "id": "asset_001",
        "name": "Funny Meme",
        "category": "memes",
        "subcategory": "funny",
        "library_path": "memes/funny.mp4",
        "source_kind": "managed"
    }
    reg_file.write_text(json.dumps({"packs": [], "assets": [asset_item]}), encoding="utf-8")

    records = sm.assets(raise_on_error=True)
    assert len(records) == 1
    assert records[0]["id"] == "asset_001"
    assert records[0]["availability"] == "online"
    assert records[0]["file_path"] == str(master_file)
    assert records[0]["origin_library_id"] == lib_id


def test_assets_offline_missing_and_mismatched_snapshot(tmp_path: Path):
    cfg_dir = tmp_path / "cfg_offline"
    lib_id = "lib_offline_test"

    sm = StorageManager(config_dir=cfg_dir)
    sm._config = {
        "root": "/nonexistent/path/never_plugged_in",
        "library_id": lib_id,
        "reserve_bytes": 0
    }
    assert sm.get_state() == StorageState.OFFLINE

    # 1. Snapshot không tồn tại
    assert sm.assets(raise_on_error=False) == []
    with pytest.raises(StorageOfflineError, match="Không tìm thấy tệp metadata bắt buộc"):
        sm.assets(raise_on_error=True)

    # 2. Snapshot có origin_library_id không khớp
    snap_path = sm._snapshot_path()
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    mismatched_snap = {
        "origin_library_id": "different_lib_id",
        "origin_root": "/some/root",
        "registry": {"packs": [], "assets": []}
    }
    snap_path.write_text(json.dumps(mismatched_snap), encoding="utf-8")

    with pytest.raises(StorageOfflineError, match="không khớp cấu hình kho"):
        sm.assets(raise_on_error=True)


def test_assets_offline_valid_snapshot(tmp_path: Path):
    cfg_dir = tmp_path / "cfg_offline_valid"
    lib_id = "lib_offline_valid"

    sm = StorageManager(config_dir=cfg_dir)
    sm._config = {
        "root": "/nonexistent/offline_drive",
        "library_id": lib_id,
        "reserve_bytes": 0
    }
    assert sm.get_state() == StorageState.OFFLINE

    snap_path = sm._snapshot_path()
    snap_path.parent.mkdir(parents=True, exist_ok=True)

    asset_record = {
        "id": "offline_asset_001",
        "name": "Offline Clip",
        "category": "broll",
        "library_path": "broll/clip.mp4",
        "source_kind": "managed"
    }
    valid_snap = {
        "origin_library_id": lib_id,
        "origin_root": "/nonexistent/offline_drive",
        "registry": {"packs": [], "assets": [asset_record]}
    }
    snap_path.write_text(json.dumps(valid_snap), encoding="utf-8")

    # Strict read thành công và trả về records với availability='offline'
    records = sm.assets(raise_on_error=True)
    assert len(records) == 1
    assert records[0]["id"] == "offline_asset_001"
    assert records[0]["availability"] == "offline"
    assert records[0]["origin_library_id"] == lib_id
