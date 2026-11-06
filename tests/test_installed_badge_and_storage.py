import os
import shutil
import tempfile
from pathlib import Path
import pytest
from src.core.storage_manager import StorageManager
from src.core.pack_manager import PackManager, PackManifest
from src.core.pack_ingest import PackIngestService
from src.services.update_service import parse_version_tuple, check_for_updates_sync


def test_parse_version_tuple():
    assert parse_version_tuple("v1.0.1") == (1, 0, 1)
    assert parse_version_tuple("1.0.2") == (1, 0, 2)
    assert parse_version_tuple("v2.0.0-beta1") == (2, 0, 0)
    assert parse_version_tuple("v1.0.2") > parse_version_tuple("v1.0.1")
    assert parse_version_tuple("1.1.0") > parse_version_tuple("1.0.9")
    assert parse_version_tuple("v1.0.1") == parse_version_tuple("1.0.1")


def test_commit_registers_installed_pack(tmp_path: Path, monkeypatch):
    test_installed_file = tmp_path / "data" / "installed_packs.json"
    monkeypatch.setattr(PackManager, "INSTALLED_PACKS_FILE", test_installed_file)

    # Setup test storage
    config_dir = tmp_path / "config"
    lib_dir = tmp_path / "lib"
    sm = StorageManager(config_dir=config_dir)
    sm.configure(lib_dir)

    service = PackIngestService(sm)

    # Tạo dummy staging payload
    staging = sm.new_staging()
    payload = staging / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    sample_file = payload / "sample_sfx.wav"
    sample_file.write_text("dummy audio")

    job = {
        "job_dir": str(staging),
        "pack_id": "test_pack_123",
        "name": "Test Sound FX Pack",
        "version": "1.0",
        "assets": [
            {
                "id": "item1",
                "relative_path": "sample_sfx.wav",
                "category": "sfx",
                "sub_group": "",
                "tags": ["whoosh"],
                "chosen": True,
                "size_bytes": 11,
                "media_type": "audio"
            }
        ]
    }

    # Trước khi commit: chưa cài
    assert PackManager.is_pack_installed("test_pack_123") is False

    # Thực hiện commit
    res = service.commit(job)
    assert res["pack_id"] == "test_pack_123"

    # Sau khi commit: PackManager phải trả về True!
    assert PackManager.is_pack_installed("test_pack_123") is True


def test_pack_ingest_wizard_silent_default_storage(tmp_path: Path, qapp):
    from src.ui.widgets.pack_ingest_wizard import PackIngestWizard
    # Tạo storage chưa cấu hình root
    empty_cfg = tmp_path / "empty_cfg"
    empty_cfg.mkdir(parents=True, exist_ok=True)
    sm = StorageManager(config_dir=empty_cfg)
    assert sm.online is False

    # Khi khởi tạo PackIngestWizard, nó phải tự động kích hoạt kho mặc định
    wizard = PackIngestWizard(storage=sm)
    assert wizard.storage.online is True
    assert wizard.storage.root.exists()
    wizard.close()
