"""
tests/core/test_storage_inventory.py
Kiểm thử dịch vụ kiểm kê kho chỉ đọc (Bounded Read-only Storage Inventory):
- Kiểm kê có giới hạn (max_items, budget_sec, byte budget, cancel).
- Trạng thái báo cáo: complete, partial, cancelled, offline, busy, error.
- Phân loại items: staging_candidate, unregistered_pack, invalid_entry với ownership="unknown".
- An toàn tuyệt đối: Không ghi, không xóa, không acquire/remove lock, không sửa registry/snapshot.
- Không quét thư mục liên kết 03_local_linked, không đọc/hash media.
"""

import errno
import json
import math
import os
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from src.core.storage_manager import (
    StorageManager,
    StorageState,
    atomic_json,
    read_json,
)
from src.core.pack_ingest import PackIngestService


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def inventory_storage(tmp_path):
    """Khởi tạo kho media cấu hình đầy đủ trong tmp_path."""
    config_dir = tmp_path / "local_config"
    library_root = tmp_path / "library_root"
    sm = StorageManager(config_dir=config_dir, reserve_bytes=1024**2)
    sm.configure(library_root)
    return sm, library_root, config_dir


# ---------------------------------------------------------------------------
# 1. Parameter Validation (max_items, budget_sec)
# ---------------------------------------------------------------------------

def test_invalid_parameters_raise_value_error(inventory_storage):
    """Các tham số max_items và budget_sec không hợp lệ phải ném ValueError ngay trước IO."""
    from src.core.storage_inventory import StorageInventoryService
    sm, _, _ = inventory_storage
    svc = StorageInventoryService(sm)

    # max_items sai
    for bad in (0, -1, -100, True, False, "10", 1.5, None):
        with pytest.raises(ValueError, match="max_items"):
            svc.scan_inventory(max_items=bad)

    # budget_sec sai
    for bad in (0, -0.5, -10, float("nan"), float("inf"), float("-inf"), True, False, "2.0", None, 10**1000):
        with pytest.raises(ValueError, match="budget_sec"):
            svc.scan_inventory(budget_sec=bad)


# ---------------------------------------------------------------------------
# 2. Normal Empty Library
# ---------------------------------------------------------------------------

def test_normal_empty_library_is_complete(inventory_storage):
    """Kho sạch chưa có staging hay pack nào trả về status complete và items rỗng."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage
    svc = StorageInventoryService(sm)

    report = svc.scan_inventory()
    assert report["status"] == "complete"
    assert report["library_id"] == sm._config["library_id"]
    assert report["items"] == []
    assert report["scanned_items"] == 0
    assert report["truncated"] is False
    assert report["consistency"] == "unverified"
    assert isinstance(report["warnings"], list)


# ---------------------------------------------------------------------------
# 3. Staging Candidate (Ownership Unknown)
# ---------------------------------------------------------------------------

def test_staging_child_is_staging_candidate_unknown_ownership(inventory_storage):
    """Thư mục con trực tiếp trong staging được báo cáo là staging_candidate với ownership unknown."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage
    staging_child = root / ".chundvc_meta" / "staging" / "job_uuid_123"
    staging_child.mkdir()
    (staging_child / "payload").mkdir()

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    assert report["status"] == "complete"
    assert len(report["items"]) == 1
    item = report["items"][0]
    assert item["kind"] == "staging_candidate"
    assert item["relative_path"] == ".chundvc_meta/staging/job_uuid_123"
    assert item["pack_id"] is None
    assert item["ownership"] == "unknown"
    assert "staging" in item["reason"].lower()
    # Không có cờ deletable hay approved
    assert "deletable" not in item and "approved" not in item


# ---------------------------------------------------------------------------
# 4. Valid Unregistered Pack vs Registered Pack
# ---------------------------------------------------------------------------

def test_valid_unregistered_pack_detected(inventory_storage):
    """Thư mục pack có manifest hợp lệ nhưng chưa đăng ký trong registry.json báo unregistered_pack."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage
    pack_folder = root / "02_cloud_downloads" / "packs" / "VlogPack_1.0_pack123"
    pack_folder.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "schema_version": 1,
        "pack_id": "pack123",
        "name": "VlogPack",
        "version": "1.0",
        "assets": [
            {"relative_path": "shot.mp4", "category": "broll", "size_bytes": 100},
        ],
    }
    atomic_json(pack_folder / "pack_manifest.json", manifest_data)

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    assert report["status"] == "complete"
    assert len(report["items"]) == 1
    item = report["items"][0]
    assert item["kind"] == "unregistered_pack"
    assert item["pack_id"] == "pack123"
    assert item["relative_path"] == "02_cloud_downloads/packs/VlogPack_1.0_pack123"
    assert item["ownership"] == "unknown"
    assert "chưa được đăng ký" in item["reason"]


def test_registered_pack_folder_is_not_unregistered(inventory_storage, tmp_path):
    """Gói đã được commit vào registry.json không bị báo là unregistered."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    # Tạo gói và commit thật
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"content")
    pack_svc = PackIngestService(sm)
    pack_svc.commit(pack_svc.prepare(src))

    # Chạy inventory
    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    assert report["status"] == "complete"
    # Không có item nào bị báo là unregistered
    assert not any(i["kind"] == "unregistered_pack" for i in report["items"])


def test_directory_scandir_permission_error_fails_complete(inventory_storage):
    """Lỗi I/O (PermissionError) khi quét thư mục không được báo là complete."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage
    svc = StorageInventoryService(sm)

    with patch("os.scandir", side_effect=PermissionError(errno.EACCES, "Denied")):
        report = svc.scan_inventory()
    assert report["status"] != "complete"
    assert report["status"] in ("error", "partial")


def test_registry_with_empty_pack_dict_fails_authority(inventory_storage):
    """registry.json có packs=[{}] là corrupt authority, không được phân loại pack là unregistered."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage
    # Tạo pack hợp lệ trên đĩa
    pack_dir = root / "02_cloud_downloads" / "packs" / "ValidPack_1.0_p1"
    pack_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(pack_dir / "pack_manifest.json", {
        "schema_version": 1, "pack_id": "p1", "assets": [{"relative_path": "a.mp4"}]
    })

    # Cố tình ghi registry có pack record rỗng
    atomic_json(root / ".chundvc_meta" / "registry.json", {
        "schema_version": 1, "packs": [{}], "assets": []
    })

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()
    assert report["status"] == "error"
    assert not any(i["kind"] == "unregistered_pack" for i in report["items"])


def test_manifest_assets_wrong_shape_is_invalid_entry(inventory_storage):
    """pack_manifest.json có assets=[123, {}] hoặc relative_path sai kiểu báo invalid_entry."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage
    pack_dir = root / "02_cloud_downloads" / "packs" / "BadAssetPack_1.0_p1"
    pack_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(pack_dir / "pack_manifest.json", {
        "schema_version": 1, "pack_id": "p1", "assets": [123, {}]
    })

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()
    assert not any(i["kind"] == "unregistered_pack" for i in report["items"])
    invalids = [i for i in report["items"] if i["kind"] == "invalid_entry"]
    assert len(invalids) == 1


def test_duplicate_unregistered_pack_ids_both_marked_conflict(inventory_storage):
    """Khi có 2 thư mục unregistered cùng pack_id, cả hai không được để item đầu là unregistered_pack hợp lệ."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    f1 = root / "02_cloud_downloads" / "packs" / "Pack1_1.0_dupid"
    f1.mkdir(parents=True, exist_ok=True)
    atomic_json(f1 / "pack_manifest.json", {
        "schema_version": 1, "pack_id": "dupid", "assets": [{"relative_path": "a.mp4"}]
    })

    f2 = root / "02_cloud_downloads" / "packs" / "Pack2_1.0_dupid"
    f2.mkdir(parents=True, exist_ok=True)
    atomic_json(f2 / "pack_manifest.json", {
        "schema_version": 1, "pack_id": "dupid", "assets": [{"relative_path": "b.mp4"}]
    })

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    # Không được để f1 là unregistered_pack hợp lệ độc nhất
    assert not any(i["kind"] == "unregistered_pack" for i in report["items"])
    # Cả hai phải là invalid_entry vì xung đột
    conflicts = [i for i in report["items"] if i["kind"] == "invalid_entry" and "trùng" in i["reason"].lower()]
    assert len(conflicts) == 2


def test_registry_consumes_metadata_byte_budget(inventory_storage):
    """Kích thước registry.json phải được tính vào trần đọc metadata total_metadata_read."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    # Tạo 1 staging job
    (root / ".chundvc_meta" / "staging" / "job_0").mkdir()

    svc = StorageInventoryService(sm)
    with patch("src.core.storage_inventory.MAX_TOTAL_METADATA_BYTES", 50):
        # registry.json ban đầu khoảng 100 bytes > 50 bytes
        report = svc.scan_inventory()
    # Khi registry vượt quá metadata budget, phải báo partial/truncated hoặc error
    assert report["truncated"] is True
    assert report["status"] == "partial"



def test_corrupt_registry_does_not_false_unregistered_all(inventory_storage):
    """Khi registry.json bị hỏng cú pháp, không được kết luận mọi pack là unregistered mà báo error."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    # Tạo 1 folder pack trên đĩa
    pack_folder = root / "02_cloud_downloads" / "packs" / "PackA_1.0_id"
    pack_folder.mkdir(parents=True, exist_ok=True)
    atomic_json(pack_folder / "pack_manifest.json", {
        "schema_version": 1, "pack_id": "id", "assets": [],
    })

    # Làm hỏng cú pháp registry.json
    (root / ".chundvc_meta" / "registry.json").write_text("{broken json...", encoding="utf-8")

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    # Phải báo error và có warning, không được coi PackA là unregistered_pack
    assert report["status"] == "error"
    assert not any(i["kind"] == "unregistered_pack" for i in report["items"])
    assert any("registry" in w.lower() for w in report["warnings"])


# ---------------------------------------------------------------------------
# 5. Offline, Error, Read-Only & Full States
# ---------------------------------------------------------------------------

def test_offline_state_returns_offline_report(inventory_storage):
    """Kho OFFLINE trả về status offline và không thực hiện scan thư mục con."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage
    # Đổi tên root để giả lập offline
    root.rename(root.parent / "unplugged_root")

    svc = StorageInventoryService(sm)
    with patch("os.scandir") as mock_scandir:
        report = svc.scan_inventory()
        assert report["status"] == "offline"
        assert report["items"] == []
        mock_scandir.assert_not_called()


def test_error_state_returns_error_report(inventory_storage, tmp_path):
    """Kho ERROR (ví dụ root là symlink) trả về status error và không scan."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    sym_root = tmp_path / "sym_root"
    sym_root.symlink_to(real_dir)
    sm._config = {"root": str(sym_root), "library_id": "uuid-123"}

    svc = StorageInventoryService(sm)
    with patch("os.scandir") as mock_scandir:
        report = svc.scan_inventory()
        assert report["status"] == "error"
        assert report["items"] == []
        mock_scandir.assert_not_called()


def test_read_only_and_full_states_allowed_to_scan(inventory_storage):
    """Trạng thái READ_ONLY và FULL vẫn được phép scan chỉ đọc."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    # READ_ONLY
    def fake_access(path, mode):
        return False if mode == os.W_OK else True

    with patch("os.access", side_effect=fake_access):
        assert sm.get_state() == StorageState.READ_ONLY
        svc = StorageInventoryService(sm)
        report = svc.scan_inventory()
        assert report["status"] == "complete"

    # FULL
    mock_usage = MagicMock(free=10)
    with patch("os.access", return_value=True), \
         patch("shutil.disk_usage", return_value=mock_usage):
        assert sm.get_state() == StorageState.FULL
        svc = StorageInventoryService(sm)
        report = svc.scan_inventory()
        assert report["status"] == "complete"


# ---------------------------------------------------------------------------
# 6. Lock Presence Check (Busy)
# ---------------------------------------------------------------------------

def test_lock_presence_returns_busy_without_mutation(inventory_storage):
    """Khi registry.lock tồn tại, trả về status busy, không xóa lock, không acquire lock."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage
    lock_file = root / ".chundvc_meta" / "registry.lock"
    lock_file.write_text("lock-holder-dummy-content")

    svc = StorageInventoryService(sm)
    with patch("src.core.storage_manager.StorageManager.lock") as mock_lock:
        report = svc.scan_inventory()
        assert report["status"] == "busy"
        assert report["items"] == []
        assert any("lock" in w.lower() for w in report["warnings"])
        # Lock không bị acquire
        mock_lock.assert_not_called()
        # File lock trên đĩa không bị xóa
        assert lock_file.exists()


# ---------------------------------------------------------------------------
# 7. Symlink & Manifest Security Guards
# ---------------------------------------------------------------------------

def test_symlink_entries_and_manifest_path_traversal_reported_invalid(inventory_storage, tmp_path):
    """Các mục symlink hoặc manifest path traversal bị báo invalid_entry và không follow."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    # 1. Symlink trong staging
    outside_dir = tmp_path / "outside_staging"
    outside_dir.mkdir()
    sym_staging = root / ".chundvc_meta" / "staging" / "symlink_job"
    sym_staging.symlink_to(outside_dir, target_is_directory=True)

    # 2. Symlink manifest trong packs
    pack_dir = root / "02_cloud_downloads" / "packs" / "SymManifestPack_1.0_p1"
    pack_dir.mkdir(parents=True, exist_ok=True)
    outside_manifest = tmp_path / "outside_manifest.json"
    outside_manifest.write_text('{"schema_version": 1, "pack_id": "p1", "assets": []}', encoding="utf-8")
    (pack_dir / "pack_manifest.json").symlink_to(outside_manifest)

    # 3. Manifest chứa path traversal
    pack_dir2 = root / "02_cloud_downloads" / "packs" / "TraversalPack_1.0_p2"
    pack_dir2.mkdir(parents=True, exist_ok=True)
    atomic_json(pack_dir2 / "pack_manifest.json", {
        "schema_version": 1,
        "pack_id": "p2",
        "assets": [{"relative_path": "../../etc/passwd"}],
    })

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    assert report["status"] == "complete"
    invalids = [i for i in report["items"] if i["kind"] == "invalid_entry"]
    assert len(invalids) == 3


def test_oversized_manifest_reported_invalid(inventory_storage):
    """Manifest lớn hơn 2 MiB bị báo invalid_entry mà không nạp toàn bộ file."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    pack_dir = root / "02_cloud_downloads" / "packs" / "OversizedPack_1.0_p3"
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest = pack_dir / "pack_manifest.json"
    manifest.write_text("{" + " " * (2 * 1024 * 1024 + 50) + "}", encoding="utf-8")

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    invalids = [i for i in report["items"] if i["kind"] == "invalid_entry"]
    assert len(invalids) == 1
    assert "2 mib" in invalids[0]["reason"].lower() or "quá lớn" in invalids[0]["reason"].lower()


# ---------------------------------------------------------------------------
# 8. Bounds, Budget & Cancel
# ---------------------------------------------------------------------------

def test_max_items_truncates_scan_to_partial(inventory_storage):
    """Khi số lượng mục đạt max_items, scan dừng lại và trả về partial + truncated=True."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    # Tạo 4 staging jobs
    for i in range(4):
        (root / ".chundvc_meta" / "staging" / f"job_{i}").mkdir()

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory(max_items=2)

    assert report["status"] == "partial"
    assert report["scanned_items"] == 2
    assert report["truncated"] is True
    assert len(report["items"]) == 2


def test_cancel_returns_cancelled(inventory_storage):
    """Khi callback cancel() trả về True, scan dừng lại với status cancelled."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    (root / ".chundvc_meta" / "staging" / "job_1").mkdir()

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory(cancel=lambda: True)

    assert report["status"] == "cancelled"
    assert report["truncated"] is False


def test_time_budget_truncates_scan_to_partial(inventory_storage):
    """Khi hết thời gian budget_sec (kiểm thử với mock monotonic), trả về partial + truncated=True."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    for i in range(5):
        (root / ".chundvc_meta" / "staging" / f"job_{i}").mkdir()

    fake_times = [0.0, 0.5, 2.5, 3.0, 4.0]
    call_idx = 0

    def mock_monotonic():
        nonlocal call_idx
        val = fake_times[min(call_idx, len(fake_times) - 1)]
        call_idx += 1
        return val

    svc = StorageInventoryService(sm)
    with patch("time.monotonic", side_effect=mock_monotonic):
        report = svc.scan_inventory(budget_sec=2.0)

    assert report["status"] == "partial"
    assert report["truncated"] is True


def test_identity_change_during_scan_returns_offline(inventory_storage):
    """Nếu marker library.json bị đổi library_id trong khi scan, báo cáo trả về offline."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    (root / ".chundvc_meta" / "staging" / "job_1").mkdir()

    marker_file = root / ".chundvc_meta" / "library.json"
    original_scandir = os.scandir

    def tamper_scandir(path):
        # Đổi UUID marker khi đang scandir
        atomic_json(marker_file, {"schema_version": 1, "library_id": "tampered-uuid-xyz"})
        return original_scandir(path)

    svc = StorageInventoryService(sm)
    with patch("os.scandir", side_effect=tamper_scandir):
        report = svc.scan_inventory()

    assert report["status"] == "offline"
    assert report["truncated"] is False
    assert any("danh tính" in w.lower() or "offline" in w.lower() for w in report["warnings"])


# ---------------------------------------------------------------------------
# 9. Zero Mutation Verification
# ---------------------------------------------------------------------------

def test_zero_mutation_guarantee(inventory_storage, tmp_path):
    """Khẳng định scan_inventory không gọi bất kỳ lệnh ghi, tạo thư mục, xóa file, lock hay snapshot nào."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, config_dir = inventory_storage

    # Tạo vài thư mục staging và pack
    (root / ".chundvc_meta" / "staging" / "job_test").mkdir()
    pack_dir = root / "02_cloud_downloads" / "packs" / "PackTest_1.0_p1"
    pack_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(pack_dir / "pack_manifest.json", {
        "schema_version": 1, "pack_id": "p1", "assets": [],
    })

    svc = StorageInventoryService(sm)

    with patch("pathlib.Path.mkdir") as mock_mkdir, \
         patch("pathlib.Path.unlink") as mock_unlink, \
         patch("shutil.rmtree") as mock_rmtree, \
         patch("os.rename") as mock_rename, \
         patch("os.replace") as mock_replace, \
         patch("src.core.storage_manager.atomic_json") as mock_atomic, \
         patch.object(sm, "lock") as mock_lock, \
         patch.object(sm, "snapshot") as mock_snapshot, \
         patch.object(sm, "ensure_space") as mock_ensure:

        report = svc.scan_inventory()
        assert report["status"] == "complete"

        mock_mkdir.assert_not_called()
        mock_unlink.assert_not_called()
        mock_rmtree.assert_not_called()
        mock_rename.assert_not_called()
        mock_replace.assert_not_called()
        mock_atomic.assert_not_called()
        mock_lock.assert_not_called()
        mock_snapshot.assert_not_called()
        mock_ensure.assert_not_called()


# ---------------------------------------------------------------------------
# 10. Group 1: IO Report Truth at Entry (PermissionError, EIO)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exc_factory", [
    lambda: PermissionError("Permission denied"),
    lambda: OSError(errno.EIO, "Input/output error"),
])
def test_manifest_open_io_error_returns_partial_or_error(inventory_storage, exc_factory):
    """Khi mở pack_manifest.json gặp lỗi I/O / EACCES / EIO, báo cáo phải là partial hoặc error, không được báo complete."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    pack_dir = root / "02_cloud_downloads" / "packs" / "PackA_1.0_pA"
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = pack_dir / "pack_manifest.json"
    atomic_json(manifest_file, {"schema_version": 1, "pack_id": "pA", "assets": []})

    real_open = open

    def fake_open(file, *args, **kwargs):
        if str(manifest_file) in str(file):
            raise exc_factory()
        return real_open(file, *args, **kwargs)

    svc = StorageInventoryService(sm)
    with patch("builtins.open", side_effect=fake_open):
        report = svc.scan_inventory()

    assert report["status"] in ("partial", "error")
    assert any("pack_manifest" in w.lower() or "lỗi" in w.lower() for w in report["warnings"])


@pytest.mark.parametrize("exc_factory", [
    lambda: PermissionError("Permission denied"),
    lambda: OSError(errno.EIO, "Input/output error"),
])
def test_entry_stat_io_error_returns_partial_or_error(inventory_storage, exc_factory):
    """Khi entry.stat() gặp PermissionError hoặc EIO, báo cáo phải là partial hoặc error, kèm cảnh báo."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    pack_dir = root / "02_cloud_downloads" / "packs" / "PackA_1.0_pA"
    pack_dir.mkdir(parents=True, exist_ok=True)

    real_stat = os.DirEntry.stat

    def fake_stat(self, *, follow_symlinks=True):
        if "PackA" in self.name:
            raise exc_factory()
        return real_stat(self, follow_symlinks=follow_symlinks)

    svc = StorageInventoryService(sm)
    with patch.object(os.DirEntry, "stat", fake_stat):
        report = svc.scan_inventory()

    assert report["status"] in ("partial", "error")
    assert any("packs" in w.lower() or "lỗi" in w.lower() for w in report["warnings"])


def test_corrupt_or_missing_manifest_keeps_complete_status(inventory_storage):
    """Khi manifest bị missing hoặc hỏng cú pháp (đọc được nhưng invalid), báo cáo vẫn complete và ghi invalid_entry."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    pack1 = root / "02_cloud_downloads" / "packs" / "PackMissing_1.0_p1"
    pack1.mkdir(parents=True, exist_ok=True)

    pack2 = root / "02_cloud_downloads" / "packs" / "PackCorrupt_1.0_p2"
    pack2.mkdir(parents=True, exist_ok=True)
    (pack2 / "pack_manifest.json").write_text("{ corrupt json ...", encoding="utf-8")

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    assert report["status"] == "complete"
    assert len(report["items"]) == 2
    assert all(it["kind"] == "invalid_entry" for it in report["items"])


# ---------------------------------------------------------------------------
# 11. Group 2: Strict Version Type Validation (int 1, not bool/float/None/str)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_version", [1.0, True, None, "1", 2])
def test_registry_schema_version_invalid_rejected(inventory_storage, bad_version):
    """Khi registry có schema_version không phải int 1 (1.0, True, None, '1', 2), phải báo error."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    reg_file = root / ".chundvc_meta" / "registry.json"
    atomic_json(reg_file, {"schema_version": bad_version, "packs": [], "assets": []})

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    assert report["status"] == "error"
    assert any("schema_version" in w for w in report["warnings"])


def test_registry_missing_schema_version_accepted_for_legacy(inventory_storage):
    """Registry không có key schema_version vẫn được chấp nhận để tương thích ngược."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    reg_file = root / ".chundvc_meta" / "registry.json"
    atomic_json(reg_file, {"packs": [], "assets": []})

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    assert report["status"] == "complete"


@pytest.mark.parametrize("bad_version", [1.0, True, None, "1", 2])
def test_manifest_schema_version_invalid_rejected(inventory_storage, bad_version):
    """Khi pack_manifest.json có schema_version không phải int 1, pack phải bị báo invalid_entry."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    pack_dir = root / "02_cloud_downloads" / "packs" / "PackA_1.0_pA"
    pack_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(pack_dir / "pack_manifest.json", {
        "schema_version": bad_version, "pack_id": "pA", "assets": []
    })

    svc = StorageInventoryService(sm)
    report = svc.scan_inventory()

    assert report["status"] == "complete"
    assert len(report["items"]) == 1
    assert report["items"][0]["kind"] == "invalid_entry"
    assert "schema_version" in report["items"][0]["reason"]


# ---------------------------------------------------------------------------
# 12. Group 3: Account Reads Immediately & Marker Budget Exhaustion
# ---------------------------------------------------------------------------

def test_rejected_and_growing_reads_consume_budget(inventory_storage):
    """Các lượt đọc bị từ chối (oversize/growing/invalid) phải được tính vào total_metadata_read và chặn đọc file tiếp theo."""
    from src.core.storage_inventory import StorageInventoryService, MAX_MANIFEST_BYTES
    import src.core.storage_inventory as mod
    sm, root, _ = inventory_storage

    # Tạo 2 pack trên đĩa
    pack1_dir = root / "02_cloud_downloads" / "packs" / "Pack1Grow_1.0_p1"
    pack1_dir.mkdir(parents=True, exist_ok=True)
    manifest1 = pack1_dir / "pack_manifest.json"
    manifest1.write_bytes(b"A" * 100)

    pack2_dir = root / "02_cloud_downloads" / "packs" / "Pack2Next_1.0_p2"
    pack2_dir.mkdir(parents=True, exist_ok=True)
    manifest2 = pack2_dir / "pack_manifest.json"
    manifest2.write_bytes(b"B" * 100)

    manifest_open_calls = 0
    opened_manifest_paths = []
    real_open = open

    def fake_open(file, *args, **kwargs):
        nonlocal manifest_open_calls
        if "pack_manifest.json" in str(file):
            manifest_open_calls += 1
            opened_manifest_paths.append(str(file))
            m = MagicMock()
            m.__enter__.return_value = m
            # Lần đọc manifest đầu tiên phình to 1000 bytes làm cạn ngay ngân sách 600 bytes
            m.read.return_value = b"X" * 1000
            return m
        return real_open(file, *args, **kwargs)

    svc = StorageInventoryService(sm)
    # Patch tổng ngân sách nhỏ: registry đọc ~100 bytes, sau khi manifest đầu đọc 1000 bytes sẽ cạn ngay lập tức
    with patch("builtins.open", side_effect=fake_open), \
         patch.object(mod, "MAX_TOTAL_METADATA_BYTES", 600):
        report = svc.scan_inventory()

    assert report["status"] == "partial"
    assert report["truncated"] is True
    # Chỉ đúng 1 manifest được mở đọc, manifest thứ 2 bị chặn hoàn toàn vì ngân sách đã cạn
    assert manifest_open_calls == 1
    assert len(opened_manifest_paths) == 1


def test_marker_recheck_budget_exhaustion_returns_partial_not_offline(inventory_storage):
    """Khi marker recheck bị cạn ngân sách byte còn lại, phải trả về partial + truncated=True chứ không báo offline giả."""
    from src.core.storage_inventory import StorageInventoryService
    import src.core.storage_inventory as mod
    sm, root, _ = inventory_storage

    svc = StorageInventoryService(sm)
    # Giới hạn trần byte sao cho vừa đủ đọc registry nhưng không đủ đọc marker recheck
    reg_size = (root / ".chundvc_meta" / "registry.json").stat().st_size
    with patch.object(mod, "MAX_TOTAL_METADATA_BYTES", reg_size + 5):
        report = svc.scan_inventory()

    assert report["status"] == "partial"
    assert report["truncated"] is True


# ---------------------------------------------------------------------------
# 13. Group 4: Manifest Validation Loop Cancel & Deadline
# ---------------------------------------------------------------------------

def test_manifest_validation_loop_cancel(inventory_storage):
    """Khi cancel() kích hoạt trong vòng lặp kiểm tra assets của manifest, dừng ngay với status cancelled."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    pack_dir = root / "02_cloud_downloads" / "packs" / "PackMulti_1.0_pM"
    pack_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(pack_dir / "pack_manifest.json", {
        "schema_version": 1,
        "pack_id": "pM",
        "assets": [
            {"relative_path": f"asset_{i}.mp4"} for i in range(10)
        ],
    })

    cancel_flag = False
    asset_checks = 0

    def cancel_hook():
        nonlocal cancel_flag, asset_checks
        if asset_checks >= 3:
            return True
        return cancel_flag

    def counting_safe_relative(rel):
        nonlocal asset_checks
        asset_checks += 1
        return Path(rel)

    svc = StorageInventoryService(sm)
    with patch("src.core.storage_inventory.safe_relative", side_effect=counting_safe_relative):
        report = svc.scan_inventory(cancel=cancel_hook)

    assert report["status"] == "cancelled"
    # Không được phân loại pack là unregistered_pack hợp lệ
    assert not any(it.get("kind") == "unregistered_pack" for it in report["items"])


def test_manifest_validation_loop_time_budget(inventory_storage):
    """Khi hết thời gian trong vòng lặp asset của manifest, trả về partial + truncated=True."""
    from src.core.storage_inventory import StorageInventoryService
    sm, root, _ = inventory_storage

    pack_dir = root / "02_cloud_downloads" / "packs" / "PackMulti_1.0_pM"
    pack_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(pack_dir / "pack_manifest.json", {
        "schema_version": 1,
        "pack_id": "pM",
        "assets": [
            {"relative_path": f"asset_{i}.mp4"} for i in range(10)
        ],
    })

    call_count = 0
    def fake_monotonic():
        nonlocal call_count
        call_count += 1
        # Sau 5 lần gọi time.monotonic, giả lập hết thời gian (trả về 100.0s)
        if call_count > 5:
            return 100.0
        return 0.0

    svc = StorageInventoryService(sm)
    with patch("time.monotonic", side_effect=fake_monotonic):
        report = svc.scan_inventory(budget_sec=1.0)

    assert report["status"] == "partial"
    assert report["truncated"] is True
    assert not any(it.get("kind") == "unregistered_pack" for it in report["items"])
