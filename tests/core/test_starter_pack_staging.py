"""
Kiểm thử Staging Producer cho gói Starter Pack (SFX & LUTs).
Bảo đảm không ghi đè vào thư mục repo git, tuân thủ taxonomy ChunDVC,
và dọn dẹp staging an toàn khi lỗi hoặc bị hủy.
"""

import errno
import os
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch

from src.core.storage_manager import StorageManager, StorageOfflineError, StorageState
from src.core.starter_pack_downloader import StarterPackProducer


def test_producer_requires_online_storage(tmp_path):
    """Kho chưa cấu hình / offline phải bị từ chối ngay trước khi tạo staging."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    assert not storage.online

    with pytest.raises((StorageOfflineError, ValueError)):
        StarterPackProducer.produce_to_staging(storage)

    # Không có thư mục staging nào được tạo ra
    assert not (tmp_path / "config" / "staging").exists()


def test_producer_rejects_readonly_or_full_storage(tmp_path, monkeypatch):
    """Kho ở trạng thái READ_ONLY hoặc FULL phải bị từ chối trước khi sinh."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    monkeypatch.setattr(storage, "get_state", lambda: StorageState.READ_ONLY)
    with pytest.raises(ValueError, match="trạng thái ghi"):
        StarterPackProducer.produce_to_staging(storage)

    monkeypatch.setattr(storage, "get_state", lambda: StorageState.FULL)
    with pytest.raises(ValueError, match="trạng thái ghi"):
        StarterPackProducer.produce_to_staging(storage)


def test_producer_writes_only_to_staging_payload_with_fakes(tmp_path, monkeypatch):
    """
    Producer chỉ sinh vào staging payload của StorageManager.
    Thư mục repo 'assets/', 'presets/', 'recipes/' tuyệt đối không bị đụng tới.
    """
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    # Giả lập generator nhẹ để kiểm thử cô lập nhanh
    def fake_sfx(target_dir: str) -> int:
        d = Path(target_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "whoosh.wav").write_bytes(b"RIFF44WAVEfmt ")
        (d / "pop.wav").write_bytes(b"RIFF44WAVEfmt ")
        return 2

    def fake_luts(target_dir: str) -> int:
        d = Path(target_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "ResolveFlow_Clean_Rec709.cube").write_text("LUT_3D_SIZE 33\n0 0 0\n")
        return 1

    # Kiểm tra trước khi chạy
    repo_assets = Path("assets/sfx")
    existing_sfx_files = set(repo_assets.glob("*.wav")) if repo_assets.exists() else set()

    job_draft = StarterPackProducer.produce_to_staging(
        storage,
        sfx_generator_fn=fake_sfx,
        lut_generator_fn=fake_luts,
    )

    # Đảm bảo trả về draft hợp lệ
    assert isinstance(job_draft, dict)
    assert job_draft["name"] == "Starter Pack (SFX & LUTs)"
    assert len(job_draft["assets"]) == 3

    # Kiểm tra payload nằm trọn vẹn trong storage.meta / 'staging'
    job_dir = Path(job_draft["job_dir"])
    assert job_dir.parent == storage.meta / "staging"
    assert (job_dir / "payload" / "sfx" / "whoosh.wav").is_file()
    assert (job_dir / "payload" / "luts" / "ResolveFlow_Clean_Rec709.cube").is_file()
    assert (job_dir / "payload" / "pack_manifest.json").is_file()

    # Không ghi vào repo assets/
    if repo_assets.exists():
        assert set(repo_assets.glob("*.wav")) == existing_sfx_files


def test_producer_manifest_schema_and_canonical_taxonomy(tmp_path):
    """
    Manifest tạo ra phải có schema_version: 1 và mọi asset đều có category
    nằm trong StorageManager.taxonomy() ('sfx' hoặc 'luts').
    """
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    def fake_sfx(target_dir: str) -> int:
        d = Path(target_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "cinematic_braam_hit.wav").write_bytes(b"RIFF44WAVEfmt ")
        return 1

    def fake_luts(target_dir: str) -> int:
        d = Path(target_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "ResolveFlow_Cinematic_Teal_Orange.cube").write_text("LUT_3D_SIZE 33\n0 0 0\n")
        return 1

    job_draft = StarterPackProducer.produce_to_staging(
        storage,
        sfx_generator_fn=fake_sfx,
        lut_generator_fn=fake_luts,
    )

    tax = storage.taxonomy()
    for item in job_draft["assets"]:
        assert item["category"] in tax, f"Category {item['category']} không hợp lệ trong taxonomy"
        assert item["category"] in ("sfx", "luts")
        assert "subcategory" in item
        assert isinstance(item.get("tags"), list)
        assert item["excluded"] is False


def test_producer_cancel_between_batches_aborts_and_cleans_staging(tmp_path):
    """Khi bị hủy giữa 2 batch (sau khi sinh SFX), phải dọn dẹp staging và ném InterruptedError."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    cancel_flag = False

    def fake_sfx(target_dir: str) -> int:
        nonlocal cancel_flag
        d = Path(target_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "whoosh.wav").write_bytes(b"data")
        # Kích hoạt cancel ngay sau khi sinh SFX
        cancel_flag = True
        return 1

    def fake_luts(target_dir: str) -> int:
        pytest.fail("Không được gọi generator LUT khi đã bị cancel")

    with pytest.raises(InterruptedError, match="Đã hủy"):
        StarterPackProducer.produce_to_staging(
            storage,
            cancel=lambda: cancel_flag,
            sfx_generator_fn=fake_sfx,
            lut_generator_fn=fake_luts,
        )

    # Thư mục staging phải được dọn dẹp sạch sẽ
    staging_dirs = list((storage.meta / "staging").iterdir())
    assert len(staging_dirs) == 0


def test_producer_empty_generator_output_raises_error(tmp_path):
    """Bộ sinh trả về 0 file hoặc rỗng phải báo lỗi và dọn dẹp staging."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    def empty_sfx(target_dir: str) -> int:
        return 0

    with pytest.raises(ValueError, match="không tạo ra tệp"):
        StarterPackProducer.produce_to_staging(
            storage,
            sfx_generator_fn=empty_sfx,
        )

    staging_dirs = list((storage.meta / "staging").iterdir())
    assert len(staging_dirs) == 0


def test_producer_generator_error_cleans_staging_and_raises(tmp_path):
    """Khi generator ném lỗi bất ngờ, staging phải được dọn dẹp và lỗi được ném ra."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    def broken_sfx(target_dir: str) -> int:
        raise RuntimeError("Lỗi tổng hợp âm thanh")

    with pytest.raises(RuntimeError, match="Lỗi tổng hợp âm thanh"):
        StarterPackProducer.produce_to_staging(
            storage,
            sfx_generator_fn=broken_sfx,
        )

    staging_dirs = list((storage.meta / "staging").iterdir())
    assert len(staging_dirs) == 0


def test_producer_disk_space_guard(tmp_path, monkeypatch):
    """Không đủ dung lượng đĩa khả dụng phải chặn ngay lập tức."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    def fail_space(size=0):
        raise OSError(errno.ENOSPC, "Hết dung lượng đĩa trống")

    monkeypatch.setattr(storage, "ensure_space", fail_space)

    with pytest.raises(OSError):
        StarterPackProducer.produce_to_staging(storage)


def test_producer_does_not_cleanup_when_library_id_or_marker_changes(tmp_path):
    """
    Khi library_id hoặc marker.json bị đổi sang UUID mới (dù cùng root) trong quá trình sinh,
    nếu generator ném lỗi thì producer KHÔNG ĐƯỢC xóa job cũ (giữ nguyên job) và re-raise lỗi.
    """
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    created_job_dir = []

    def probe_sfx(target_dir: str) -> int:
        p = Path(target_dir)
        job_dir = p.parent
        created_job_dir.append(job_dir)
        (p / "whoosh.wav").write_bytes(b"data")

        # Đổi library_id trong config và marker.json sang UUID mới tại CÙNG root
        new_id = "other-library-uuid"
        storage._config["library_id"] = new_id
        from src.core.storage_manager import atomic_json
        atomic_json(storage.meta / "library.json", {"library_id": new_id})

        # Ném lỗi OSError
        raise OSError("Disk write failed during probe")

    with pytest.raises(OSError, match="Disk write failed during probe"):
        StarterPackProducer.produce_to_staging(
            storage,
            sfx_generator_fn=probe_sfx,
        )

    # Job ban đầu PHẢI ĐƯỢC GIỮ LẠI (không bị xóa nhầm)
    assert len(created_job_dir) == 1
    assert created_job_dir[0].exists()


def test_producer_aborts_before_lut_if_identity_changes_without_raise(tmp_path):
    """
    Nếu library_id hoặc marker bị đổi sau SFX mà SFX không raise,
    producer phải phát hiện identity thay đổi và từ chối sinh tiếp LUTs.
    """
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    def mutate_sfx(target_dir: str) -> int:
        p = Path(target_dir)
        (p / "whoosh.wav").write_bytes(b"data")
        storage._config["library_id"] = "different-uuid"
        from src.core.storage_manager import atomic_json
        atomic_json(storage.meta / "library.json", {"library_id": "different-uuid"})
        return 1

    def never_called_lut(target_dir: str) -> int:
        pytest.fail("Không được gọi generator LUT khi danh tính kho đã thay đổi")

    with pytest.raises(ValueError, match="Danh tính kho"):
        StarterPackProducer.produce_to_staging(
            storage,
            sfx_generator_fn=mutate_sfx,
            lut_generator_fn=never_called_lut,
        )


def test_producer_rejects_symlink_job_dir(tmp_path, monkeypatch):
    """Nếu job_dir hoặc payload bị tráo thành symlink, producer phải phát hiện và từ chối."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    real_staging = storage.meta / "staging"
    fake_target = tmp_path / "fake_target"
    fake_target.mkdir()

    symlink_job = real_staging / "symlink_job"
    symlink_job.symlink_to(fake_target, target_is_directory=True)

    monkeypatch.setattr(storage, "new_staging", lambda: symlink_job)

    with pytest.raises(ValueError, match="symlink"):
        StarterPackProducer.produce_to_staging(storage)

