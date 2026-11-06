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


def test_green_screen_memes_alias_installed_matching(tmp_path: Path, monkeypatch):
    test_installed_file = tmp_path / "data" / "installed_packs.json"
    monkeypatch.setattr(PackManager, "INSTALLED_PACKS_FILE", test_installed_file)

    config_dir = tmp_path / "config"
    lib_dir = tmp_path / "lib"
    sm = StorageManager(config_dir=config_dir)
    sm.configure(lib_dir)

    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda *args, **kwargs: sm)

    pack_folder = lib_dir / "02_cloud_downloads" / "packs" / "Tong_Hop_Meme_Green_Screen_109Clips_1.0_b8de7a8c74f944efae8cf85def72253f"
    pack_folder.mkdir(parents=True, exist_ok=True)
    (pack_folder / "meme.mp4").write_text("dummy")

    with sm.lock():
        reg = sm.registry()
        reg["packs"].append({
            "id": "b8de7a8c74f944efae8cf85def72253f",
            "name": "Tong_Hop_Meme_Green_Screen_109Clips",
            "version": "1.0",
            "folder": str(pack_folder.relative_to(lib_dir).as_posix())
        })
        import json
        with open(sm.meta / "registry.json", "w", encoding="utf-8") as f:
            json.dump(reg, f)

    assert PackManager.is_pack_installed("green_screen_memes_109") is True
    installed = PackManager.get_installed_packs()
    assert "green_screen_memes_109" in installed


def test_storage_settings_dialog_has_confirm_button(qapp, tmp_path):
    from src.ui.widgets.pack_ingest_wizard import StorageSettingsDialog
    config_dir = tmp_path / "config"
    sm = StorageManager(config_dir=config_dir)
    
    dialog = StorageSettingsDialog(storage=sm)
    assert hasattr(dialog, "btn_confirm")
    assert "Xác Nhận" in dialog.btn_confirm.text()

    emitted = []
    dialog.library_changed.connect(lambda: emitted.append(True))
    dialog.btn_confirm.click()

    assert len(emitted) >= 1
    assert dialog.result() == 1
    dialog.deleteLater()


def test_remove_pack_and_recommit_overwrite(tmp_path: Path):
    """Xác nhận việc xóa gói dọn dẹp sạch thư mục và cho phép nhập đè lại thành công."""
    config_dir = tmp_path / "config"
    lib_dir = tmp_path / "lib"
    sm = StorageManager(config_dir=config_dir)
    sm.configure(lib_dir)

    service = PackIngestService(sm)

    # 1. Tạo và commit gói lần 1
    staging = sm.new_staging()
    payload = staging / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    (payload / "whoosh.wav").write_text("audio1")

    job = {
        "job_dir": str(staging),
        "pack_id": "davinci_gr44_seamless_transitions",
        "name": "Gói Chuyển Cảnh GR-44 v1.4 & Sound FX",
        "version": "1.0",
        "assets": [
            {
                "id": "item1",
                "relative_path": "whoosh.wav",
                "category": "templates",
                "sub_group": "",
                "tags": ["transition"],
                "chosen": True,
                "size_bytes": 6,
                "media_type": "audio"
            }
        ]
    }
    service.commit(job)

    # Kiểm tra folder đã tạo
    pack_dir = lib_dir / "02_cloud_downloads" / "packs" / "Goi_Chuyen_Canh_GR_44_v1_4_Sound_FX_1_0_davinci_gr44_seamless_transitions"
    # Hoặc folder tương ứng theo slug
    folders = list((lib_dir / "02_cloud_downloads" / "packs").iterdir())
    assert len(folders) == 1

    # 2. Xóa gói qua remove_pack
    assert sm.remove_pack("davinci_gr44_seamless_transitions") is True
    # Thư mục trên đĩa phải được dọn dẹp sạch
    remaining_folders = [f for f in (lib_dir / "02_cloud_downloads" / "packs").iterdir() if f.is_dir()]
    assert len(remaining_folders) == 0

    # 3. Commit lại lần 2 (người dùng tải lại và nhập vào kho)
    staging2 = sm.new_staging()
    payload2 = staging2 / "payload"
    payload2.mkdir(parents=True, exist_ok=True)
    (payload2 / "whoosh.wav").write_text("audio2")

    job2 = {
        "job_dir": str(staging2),
        "pack_id": "davinci_gr44_seamless_transitions",
        "name": "Gói Chuyển Cảnh GR-44 v1.4 & Sound FX",
        "version": "1.0",
        "assets": [
            {
                "id": "item1",
                "relative_path": "whoosh.wav",
                "category": "templates",
                "sub_group": "",
                "tags": ["transition"],
                "chosen": True,
                "size_bytes": 6,
                "media_type": "audio"
            }
        ]
    }
    # Phải thành công mượt mà, không quăng lỗi 'Pack đã được nhập'
    res2 = service.commit(job2)
    assert res2["pack_id"] == "davinci_gr44_seamless_transitions"
