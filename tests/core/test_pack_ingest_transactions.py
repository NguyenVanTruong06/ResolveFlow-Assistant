"""
tests/core/test_pack_ingest_transactions.py
Kiểm thử giao dịch xuất bản gói (Pack Ingest Commit Transactions):
- External registry là nguồn chân lý duy nhất (Authoritative Source of Truth).
- Snapshot cục bộ là bản sao ngoại tuyến (Offline Replica) - lỗi snapshot không được làm hỏng pack đã publish.
- Rollback an toàn khi ghi registry ngoài thất bại.
- Recheck identity trước khi rename trong lock ngăn ngừa tráo ổ / đổi marker.
- Giải phóng lock an toàn trong mọi trường hợp.
"""

import errno
import logging
from pathlib import Path
from unittest.mock import patch
import pytest

from src.core.storage_manager import (
    StorageManager,
    StorageOfflineError,
    atomic_json,
    read_json,
)
from src.core.pack_ingest import PackIngestService


@pytest.fixture
def configured_ingest(tmp_path):
    """Khởi tạo kho media và dịch vụ Ingest trong tmp_path."""
    config_dir = tmp_path / "local_config"
    library_root = tmp_path / "external_library"
    sm = StorageManager(config_dir=config_dir, reserve_bytes=1024**2)
    sm.configure(library_root)

    source_dir = tmp_path / "source_pack"
    source_dir.mkdir(parents=True, exist_ok=True)
    video_file = source_dir / "clip_01.mp4"
    video_file.write_bytes(b"sample-video-content-bytes")

    svc = PackIngestService(sm)
    job = svc.prepare(source_dir)
    return sm, svc, job, library_root, config_dir


def test_commit_succeeds_even_when_local_snapshot_fails(configured_ingest):
    """
    RED TEST (02B1): Khi local snapshot gặp lỗi I/O (ENOSPC), commit() vẫn thành công:
    - Pack đã được publish vào external registry.json
    - Thư mục media đã được rename vào 02_cloud_downloads/packs/
    - Kết quả trả về snapshot_synced=False kèm warning
    - Staging job được dọn dẹp
    - Lock được giải phóng
    """
    sm, svc, job, root, config_dir = configured_ingest
    snapshot_file = sm._snapshot_path()
    original_atomic = atomic_json

    def selective_atomic_json(path, data):
        if Path(path) == snapshot_file:
            raise OSError(errno.ENOSPC, "No space left on device for local snapshot")
        return original_atomic(path, data)

    with patch("src.core.storage_manager.atomic_json", side_effect=selective_atomic_json):
        result = svc.commit(job)

    # 1. Kết quả trả về chứa thông tin pack và cảnh báo snapshot
    assert result["pack_id"] == job["pack_id"]
    assert result["count"] == 1
    assert result["snapshot_synced"] is False
    assert len(result["warnings"]) > 0
    assert "chưa được đồng bộ" in result["warnings"][0]

    # 2. External registry trên kho ngoài đã có pack và asset
    reg = sm.registry()
    pack_ids = [p["id"] for p in reg["packs"]]
    assert job["pack_id"] in pack_ids
    assert any(a["pack_id"] == job["pack_id"] for a in reg["assets"])

    # 3. Thư mục media đích tồn tại trên đĩa
    pack_folder = root / "02_cloud_downloads/packs" / f"{job['name']}_{job['version']}_{job['pack_id']}"
    assert pack_folder.is_dir()
    assert (pack_folder / "clip_01.mp4").is_file()

    # 4. Staging job đã được dọn dẹp
    assert not Path(job["job_dir"]).exists()

    # 5. Lock được giải phóng, có thể chiếm lock mới ngay lập tức
    with sm.lock():
        pass


@pytest.mark.parametrize(
    "snapshot_error",
    [
        PermissionError(errno.EACCES, "Permission denied on snapshot path"),
        ValueError("Snapshot payload serialization overflow"),
        StorageOfflineError("Snapshot config directory inaccessible"),
    ],
)
def test_commit_handles_various_snapshot_errors(configured_ingest, snapshot_error):
    """Bắt các loại lỗi snapshot (PermissionError, ValueError, StorageOfflineError) mà không làm hỏng commit."""
    sm, svc, job, root, _ = configured_ingest
    snapshot_file = sm._snapshot_path()
    original_atomic = atomic_json

    def faulty_atomic(path, data):
        if Path(path) == snapshot_file:
            raise snapshot_error
        return original_atomic(path, data)

    with patch("src.core.storage_manager.atomic_json", side_effect=faulty_atomic):
        result = svc.commit(job)

    assert result["snapshot_synced"] is False
    assert len(result["warnings"]) == 1
    assert result["pack_id"] == job["pack_id"]
    # Kiểm tra external registry đã ghi nhận
    assert any(p["id"] == job["pack_id"] for p in sm.registry()["packs"])


def test_commit_normal_case_synced_true(configured_ingest):
    """Khi không có lỗi snapshot, snapshot_synced là True và warnings rỗng."""
    sm, svc, job, root, _ = configured_ingest
    result = svc.commit(job)

    assert result["pack_id"] == job["pack_id"]
    assert result["count"] == 1
    assert result["snapshot_synced"] is True
    assert result["warnings"] == []

    # Snapshot cục bộ tồn tại và khớp với registry
    snap_data = read_json(sm._snapshot_path(), {})
    snap_packs = [p["id"] for p in snap_data.get("registry", {}).get("packs", [])]
    assert job["pack_id"] in snap_packs


def test_external_registry_atomic_json_failure_rolls_back(configured_ingest):
    """
    Khi ghi external registry.json thất bại:
    - commit() PHẢI ném ngoại lệ (không nuốt lỗi source of truth)
    - Rollback folder về payload trong staging
    - Pack không được đăng ký trong registry
    - sm.snapshot() tuyệt đối KHÔNG được gọi
    """
    sm, svc, job, root, _ = configured_ingest
    reg_file = sm.meta / "registry.json"
    original_atomic = atomic_json

    def faulty_registry_atomic(path, data):
        if Path(path) == reg_file:
            raise OSError(errno.EIO, "I/O error writing external registry")
        return original_atomic(path, data)

    with patch("src.core.pack_ingest.atomic_json", side_effect=faulty_registry_atomic), \
         patch.object(sm, "snapshot") as mock_snapshot:
        with pytest.raises(OSError, match="I/O error writing external registry"):
            svc.commit(job)

        # Snapshot không được gọi
        mock_snapshot.assert_not_called()

    # Kiểm tra registry cũ nguyên vẹn, không có pack mới
    reg = sm.registry()
    assert not any(p["id"] == job["pack_id"] for p in reg["packs"])

    # Thư mục đích trong 02_cloud_downloads/packs không tồn tại
    pack_folder = root / "02_cloud_downloads/packs" / f"{job['name']}_{job['version']}_{job['pack_id']}"
    assert not pack_folder.exists()

    # Payload đã được rollback về staging
    staging_payload = Path(job["job_dir"]) / "payload"
    assert staging_payload.is_dir()
    assert (staging_payload / "clip_01.mp4").is_file()

    # Lock được giải phóng
    with sm.lock():
        pass


def test_marker_changed_before_rename_in_lock_raises_and_aborts(configured_ingest):
    """
    Khi marker bị thay đổi danh tính ngay trước rename trong lock (sau khi manifest đã ghi và files đã prune):
    - Đổi marker ngay sau khi pack_manifest.json được ghi trong staging
    - sm.require_online() THỰC TẾ (không mock) ném StorageOfflineError
    - os.rename không được thực thi
    - atomic_json(registry.json) không được gọi, registry cũ nguyên vẹn
    - sm.snapshot() không được gọi
    - Lock được giải phóng sau khi ngoại lệ ném ra
    """
    sm, svc, job, root, _ = configured_ingest
    marker_file = sm.meta / "library.json"
    manifest_target = Path(job["job_dir"]) / "payload" / "pack_manifest.json"
    original_atomic = atomic_json

    manifest_written = False

    def hook_after_manifest_written(path, data):
        nonlocal manifest_written
        # Ghi file thật
        res = original_atomic(path, data)
        # Ngay sau khi pack_manifest.json được ghi trong lock, đổi marker_file trên đĩa
        if Path(path) == manifest_target:
            manifest_written = True
            original_atomic(marker_file, {"schema_version": 1, "library_id": "tampered-uuid-xyz"})
        return res

    with patch("src.core.pack_ingest.atomic_json", side_effect=hook_after_manifest_written), \
         patch("src.core.pack_ingest.os.rename") as mock_rename, \
         patch.object(sm, "snapshot") as mock_snapshot:
        with pytest.raises(StorageOfflineError):
            svc.commit(job)

        # Khẳng định manifest trong staging đã được ghi trước khi đổi marker
        assert manifest_written is True
        # Khẳng định rename tuyệt đối không được gọi
        mock_rename.assert_not_called()
        # Khẳng định snapshot không được gọi
        mock_snapshot.assert_not_called()

    # Thư mục đích trong packs không tồn tại
    pack_folder = root / "02_cloud_downloads/packs" / f"{job['name']}_{job['version']}_{job['pack_id']}"
    assert not pack_folder.exists()

    # Registry ngoài không chứa pack này
    # Phục hồi marker để đọc registry và kiểm tra lock
    original_atomic(marker_file, {"schema_version": 1, "library_id": sm._config["library_id"]})
    reg = sm.registry()
    assert not any(p["id"] == job["pack_id"] for p in reg["packs"])

    # Lock được giải phóng, có thể chiếm lock mới
    with sm.lock():
        pass

