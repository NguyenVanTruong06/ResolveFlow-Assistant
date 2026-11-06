"""
tests/core/test_storage_states.py
Kiểm thử trạng thái kho (StorageState) và bảo vệ ghi (ensure_space guard):
- 5 trạng thái tổng hợp có thứ tự ưu tiên (Precedence):
  1. Identity / Safety / Error -> OFFLINE hoặc ERROR
  2. Permissions Hint -> READ_ONLY
  3. Capacity -> FULL hoặc ONLINE
- ensure_space chặn ghi khi OFFLINE (StorageOfflineError), READ_ONLY (PermissionError/EACCES), FULL (OSError/ENOSPC).
- Không phá vỡ hành vi legacy của online / require_online.
- get_state() là truy vấn thuần túy, bounded read cho marker, tuyệt đối không tạo thư mục, ghi file hay chiếm lock.
"""

import errno
import os
import shutil
import stat
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from src.core.storage_manager import (
    StorageManager,
    StorageState,
    StorageOfflineError,
    MAX_JSON,
    atomic_json,
)


@pytest.fixture
def configured_storage(tmp_path):
    """Khởi tạo kho hoàn chỉnh trong tmp_path."""
    config_dir = tmp_path / "config"
    root_dir = tmp_path / "library_root"
    sm = StorageManager(config_dir=config_dir, reserve_bytes=256 * 1024**2)
    sm.configure(root_dir)
    return sm, root_dir, config_dir


# ---------------------------------------------------------------------------
# 1. Identity & Safety Checks -> OFFLINE vs ERROR
# ---------------------------------------------------------------------------

def test_unconfigured_storage_is_offline(tmp_path):
    """Kho chưa cấu hình trả về OFFLINE."""
    sm = StorageManager(config_dir=tmp_path / "empty_config")
    assert sm.get_state() == StorageState.OFFLINE


def test_missing_root_path_is_offline(tmp_path):
    """Đường dẫn root không tồn tại trên hệ thống trả về OFFLINE."""
    sm = StorageManager(config_dir=tmp_path / "config")
    sm._config = {"root": str(tmp_path / "non_existent"), "library_id": "uuid-123"}
    assert sm.get_state() == StorageState.OFFLINE


def test_missing_marker_file_is_offline(configured_storage):
    """Root tồn tại nhưng thiếu file library.json trả về OFFLINE."""
    sm, root, _ = configured_storage
    marker = root / ".chundvc_meta" / "library.json"
    marker.unlink()
    assert sm.get_state() == StorageState.OFFLINE


def test_library_id_mismatch_is_offline(configured_storage):
    """library_id trong marker không khớp với config trả về OFFLINE."""
    sm, root, _ = configured_storage
    marker = root / ".chundvc_meta" / "library.json"
    atomic_json(marker, {"schema_version": 1, "library_id": "wrong-uuid"})
    assert sm.get_state() == StorageState.OFFLINE


def test_malformed_marker_json_is_error(configured_storage):
    """File library.json bị hỏng cú pháp JSON trả về ERROR."""
    sm, root, _ = configured_storage
    marker = root / ".chundvc_meta" / "library.json"
    marker.write_text("{broken json syntax...", encoding="utf-8")
    assert sm.get_state() == StorageState.ERROR


def test_marker_wrong_shape_is_error(configured_storage):
    """File library.json chứa list hoặc không phải JSON dict trả về ERROR."""
    sm, root, _ = configured_storage
    marker = root / ".chundvc_meta" / "library.json"
    marker.write_text("[\"not\", \"a\", \"dict\"]", encoding="utf-8")
    assert sm.get_state() == StorageState.ERROR


def test_marker_permission_error_is_error(configured_storage):
    """PermissionError khi đọc library.json không chứng minh được identity -> trả về ERROR."""
    sm, root, _ = configured_storage
    with patch("builtins.open", side_effect=PermissionError("Cannot read marker")):
        assert sm.get_state() == StorageState.ERROR


def test_invalid_root_type_is_error(tmp_path):
    """Cấu hình root có giá trị sai kiểu (int, list, bool...) trả về ERROR; chỉ absent/None/chuỗi rỗng là OFFLINE."""
    sm = StorageManager(config_dir=tmp_path / "config")
    sm._config = {"root": 123, "library_id": "uuid-123"}
    assert sm.get_state() == StorageState.ERROR

    sm._config = {"root": ["/path"], "library_id": "uuid-123"}
    assert sm.get_state() == StorageState.ERROR

    sm._config = {"root": False, "library_id": "uuid-123"}
    assert sm.get_state() == StorageState.ERROR

    sm._config = {"root": None, "library_id": "uuid-123"}
    assert sm.get_state() == StorageState.OFFLINE

    sm._config = {"root": "", "library_id": "uuid-123"}
    assert sm.get_state() == StorageState.OFFLINE


def test_root_as_regular_file_is_error_and_rejects_ensure_space(tmp_path):
    """Root là tệp thường thay vì thư mục -> ERROR và ensure_space ném StorageOfflineError."""
    file_root = tmp_path / "file_root"
    file_root.write_text("regular file", encoding="utf-8")
    sm = StorageManager(config_dir=tmp_path / "config")
    sm._config = {"root": str(file_root), "library_id": "uuid-123"}
    assert sm.get_state() == StorageState.ERROR
    with pytest.raises(StorageOfflineError):
        sm.ensure_space(0)


def test_root_stat_permission_error_is_error(configured_storage):
    """lstat root bị PermissionError trả về ERROR chứ không nuốt thành OFFLINE."""
    sm, root, _ = configured_storage
    with patch("pathlib.Path.lstat", side_effect=PermissionError("Permission denied")):
        assert sm.get_state() == StorageState.ERROR


def test_oversized_marker_is_error_and_bounded_read(configured_storage):
    """File marker quá lớn (> MAX_JSON) trả về ERROR và không bị read_text toàn file."""
    sm, root, _ = configured_storage
    marker = root / ".chundvc_meta" / "library.json"
    fake_st = MagicMock(st_size=MAX_JSON + 1024, st_mode=stat.S_IFREG | 0o644)
    with patch.object(Path, "lstat", return_value=fake_st), \
         patch("pathlib.Path.read_text") as mock_read_text:
        assert sm.get_state() == StorageState.ERROR
        mock_read_text.assert_not_called()


def test_symlink_root_rejected_by_get_state_and_ensure_space(configured_storage, tmp_path):
    """
    Probe Codex: Khi root bị tráo thành symlink trỏ tới thư mục khác (dù marker bên trong hợp lệ):
    - get_state() phải trả về ERROR.
    - ensure_space() phải ném StorageOfflineError TRƯỚC capacity/permissions hoặc bất kỳ thao tác ghi nào.
    """
    sm, root, _ = configured_storage
    moved = tmp_path / "moved_library"
    root.rename(moved)
    root.symlink_to(moved, target_is_directory=True)

    assert sm.get_state() == StorageState.ERROR
    with pytest.raises(StorageOfflineError) as exc_info:
        sm.ensure_space(0)
    assert "symlink không an toàn" in str(exc_info.value)


def test_symlink_marker_rejected_by_get_state_and_ensure_space(configured_storage, tmp_path):
    """
    Probe Codex: Khi marker library.json là symlink trỏ tới file marker hợp lệ ở ngoài:
    - get_state() phải trả về ERROR.
    - ensure_space() phải ném StorageOfflineError trước khi đọc file ngoài.
    """
    sm, root, _ = configured_storage
    marker = root / ".chundvc_meta" / "library.json"
    external_marker = tmp_path / "external_library.json"
    shutil.copyfile(marker, external_marker)
    marker.unlink()
    marker.symlink_to(external_marker)

    assert sm.get_state() == StorageState.ERROR
    with pytest.raises(StorageOfflineError):
        sm.ensure_space(0)


def test_dangling_symlink_marker_rejected_by_ensure_space(configured_storage, tmp_path):
    """Marker là dangling symlink trỏ tới file không tồn tại phải bị chặn bởi ensure_space."""
    sm, root, _ = configured_storage
    marker = root / ".chundvc_meta" / "library.json"
    marker.unlink()
    marker.symlink_to(tmp_path / "non_existent_marker.json")

    assert sm.get_state() == StorageState.ERROR
    with pytest.raises(StorageOfflineError):
        sm.ensure_space(0)


def test_root_symlink_is_error(tmp_path):
    """Root là symlink trỏ đi nơi khác bị từ chối với trạng thái ERROR."""
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    sym_root = tmp_path / "sym_root"
    sym_root.symlink_to(real_dir)

    sm = StorageManager(config_dir=tmp_path / "config")
    sm._config = {"root": str(sym_root), "library_id": "uuid-123"}
    assert sm.get_state() == StorageState.ERROR


@pytest.mark.parametrize("core_sub", [
    ".chundvc_meta",
    ".chundvc_meta/staging",
    "01_core",
    "02_cloud_downloads",
    "02_cloud_downloads/packs",
    "03_local_linked",
])
def test_core_managed_symlink_is_error(configured_storage, core_sub, tmp_path):
    """Bất kỳ thư mục core nào là symlink đều trả về ERROR để ngăn directory traversal."""
    sm, root, _ = configured_storage
    target = root / core_sub
    # Xóa thư mục thật và thay bằng symlink
    if target.is_dir():
        shutil.rmtree(target)
    outside = tmp_path / "outside_dir"
    outside.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside)

    assert sm.get_state() == StorageState.ERROR


def test_core_managed_path_as_file_is_error(configured_storage):
    """Thư mục managed hiện diện nhưng là tệp thường thay vì thư mục -> ERROR."""
    sm, root, _ = configured_storage
    staging = root / ".chundvc_meta" / "staging"
    if staging.is_dir():
        shutil.rmtree(staging)
    staging.write_text("i am a file not a directory")

    assert sm.get_state() == StorageState.ERROR


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: PermissionError(errno.EACCES, "Permission denied on 01_core"),
        lambda: OSError(errno.EIO, "I/O error on 01_core"),
    ],
)
def test_ensure_space_rejects_core_dir_oserror_fail_open(configured_storage, error_factory):
    """
    Regression 02A-R2: ensure_space() phải ném StorageOfflineError khi lstat() thư mục core gặp OSError/PermissionError.
    Không được fail-open (pass) và cho phép ghi.
    """
    sm, root, _ = configured_storage
    target_path = root / "01_core"
    original_lstat = Path.lstat

    def selective_lstat(self):
        if self == target_path:
            raise error_factory()
        return original_lstat(self)

    with patch("os.access", return_value=True), \
         patch.object(Path, "lstat", selective_lstat):
        assert sm.get_state() == StorageState.ERROR
        with pytest.raises(StorageOfflineError) as exc_info:
            sm.ensure_space(0)
        assert "01_core" in str(exc_info.value) or "thư mục kho" in str(exc_info.value)


def test_ensure_space_allows_missing_optional_core_dir(configured_storage):
    """ensure_space bỏ qua FileNotFoundError khi thư mục core chưa được tạo (optional core missing)."""
    sm, root, _ = configured_storage
    core_dir = root / "01_core"
    if core_dir.exists():
        shutil.rmtree(core_dir)
    assert not core_dir.exists()
    with patch("os.access", return_value=True):
        sm.ensure_space(0)


def test_ensure_space_checks_core_dirs_before_marker(configured_storage, tmp_path):
    """
    Regression 02A-R2: Kiểm tra các thư mục core managed TRƯỚC marker.
    Nếu .chundvc_meta là symlink trỏ ra ngoài, ensure_space ném StorageOfflineError
    ngay tại kiểm tra thư mục core trước khi truy vấn marker con.
    """
    sm, root, _ = configured_storage
    meta_dir = root / ".chundvc_meta"
    outside = tmp_path / "outside_meta"
    outside.mkdir(parents=True, exist_ok=True)
    shutil.copytree(meta_dir, outside, dirs_exist_ok=True)
    shutil.rmtree(meta_dir)
    meta_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(StorageOfflineError) as exc_info:
        sm.ensure_space(0)
    assert "symlink không an toàn" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Permissions (READ_ONLY) & Capacity (FULL vs ONLINE) Precedence
# ---------------------------------------------------------------------------

def test_matching_marker_writable_sufficient_space_is_online(configured_storage):
    """Kho hợp lệ, có quyền ghi và đủ dung lượng trả về ONLINE."""
    sm, root, _ = configured_storage
    mock_usage = MagicMock(free=500 * 1024**2, total=1000 * 1024**2)
    with patch("os.access", return_value=True), \
         patch("shutil.disk_usage", return_value=mock_usage):
        assert sm.get_state() == StorageState.ONLINE


def test_equality_free_equals_reserve_is_online(configured_storage):
    """Khi free_space đúng bằng reserve_bytes, trạng thái là ONLINE."""
    sm, root, _ = configured_storage
    mock_usage = MagicMock(free=sm.reserve_bytes, total=1000 * 1024**2)
    with patch("os.access", return_value=True), \
         patch("shutil.disk_usage", return_value=mock_usage):
        assert sm.get_state() == StorageState.ONLINE


def test_matching_marker_read_only_access_is_read_only(configured_storage):
    """os.access W_OK trả về False cho root hoặc core path -> READ_ONLY."""
    sm, root, _ = configured_storage
    def fake_access(path, mode):
        if mode == os.W_OK:
            return False
        return True

    mock_usage = MagicMock(free=500 * 1024**2, total=1000 * 1024**2)
    with patch("os.access", side_effect=fake_access), \
         patch("shutil.disk_usage", return_value=mock_usage):
        assert sm.get_state() == StorageState.READ_ONLY


def test_matching_marker_low_free_space_is_full(configured_storage):
    """Dung lượng trống thấp hơn reserve_bytes -> FULL."""
    sm, root, _ = configured_storage
    mock_usage = MagicMock(free=sm.reserve_bytes - 1, total=1000 * 1024**2)
    with patch("os.access", return_value=True), \
         patch("shutil.disk_usage", return_value=mock_usage):
        assert sm.get_state() == StorageState.FULL


def test_read_only_takes_precedence_over_full(configured_storage):
    """Nếu vừa không có quyền ghi vừa thiếu dung lượng, READ_ONLY ưu tiên hơn FULL."""
    sm, root, _ = configured_storage
    def fake_access(path, mode):
        if mode == os.W_OK:
            return False
        return True

    mock_usage = MagicMock(free=sm.reserve_bytes - 100, total=1000 * 1024**2)
    with patch("os.access", side_effect=fake_access), \
         patch("shutil.disk_usage", return_value=mock_usage):
        assert sm.get_state() == StorageState.READ_ONLY


def test_disk_usage_exception_is_error(configured_storage):
    """disk_usage ném ngoại lệ OSError (ngoài FileNotFoundError) trả về ERROR (không được ONLINE)."""
    sm, root, _ = configured_storage
    with patch("os.access", return_value=True), \
         patch("shutil.disk_usage", side_effect=OSError("I/O error querying disk")):
        assert sm.get_state() == StorageState.ERROR


def test_disk_usage_file_not_found_is_offline(configured_storage):
    """disk_usage gặp FileNotFoundError (ổ vừa bị rút) trả về OFFLINE."""
    sm, root, _ = configured_storage
    with patch("os.access", return_value=True), \
         patch("shutil.disk_usage", side_effect=FileNotFoundError("Mount point disappeared")):
        assert sm.get_state() == StorageState.OFFLINE


# ---------------------------------------------------------------------------
# 3. ensure_space Guard Integration
# ---------------------------------------------------------------------------

def test_ensure_space_when_read_only_raises_permission_error(configured_storage):
    """ensure_space ném PermissionError với errno EACCES khi kho ở chế độ chỉ đọc."""
    sm, root, _ = configured_storage
    def fake_access(path, mode):
        if mode == os.W_OK:
            return False
        return True

    with patch("os.access", side_effect=fake_access):
        with pytest.raises(PermissionError) as exc_info:
            sm.ensure_space(100)
        assert exc_info.value.errno == errno.EACCES
        assert "chỉ đọc" in str(exc_info.value)


def test_ensure_space_when_insufficient_space_raises_enospc(configured_storage):
    """ensure_space ném OSError với errno ENOSPC khi không đủ dung lượng yêu cầu."""
    sm, root, _ = configured_storage
    mock_usage = MagicMock(free=sm.reserve_bytes + 50)
    with patch("os.access", return_value=True), \
         patch("shutil.disk_usage", return_value=mock_usage):
        # Yêu cầu 100 bytes trong khi chỉ thừa 50 bytes so với reserve_bytes
        with pytest.raises(OSError) as exc_info:
            sm.ensure_space(100)
        assert exc_info.value.errno == errno.ENOSPC
        assert "vùng trống dự phòng" in str(exc_info.value)


def test_ensure_space_when_exact_equality_passes(configured_storage):
    """ensure_space cho phép ghi khi free đúng bằng size + reserve_bytes."""
    sm, root, _ = configured_storage
    req_size = 500
    mock_usage = MagicMock(free=req_size + sm.reserve_bytes)
    with patch("os.access", return_value=True), \
         patch("shutil.disk_usage", return_value=mock_usage):
        # Không ném ngoại lệ
        sm.ensure_space(req_size)


# ---------------------------------------------------------------------------
# 4. Legacy Properties & Query Purity
# ---------------------------------------------------------------------------

def test_online_property_remains_true_when_read_only_or_full(configured_storage):
    """sm.online vẫn trả về True khi kho là READ_ONLY hoặc FULL để không chặn duyệt cache."""
    sm, root, _ = configured_storage
    # Trạng thái READ_ONLY
    def fake_access(path, mode):
        return False if mode == os.W_OK else True

    with patch("os.access", side_effect=fake_access):
        assert sm.get_state() == StorageState.READ_ONLY
        assert sm.online is True

    # Trạng thái FULL
    mock_usage = MagicMock(free=10)
    with patch("os.access", return_value=True), \
         patch("shutil.disk_usage", return_value=mock_usage):
        assert sm.get_state() == StorageState.FULL
        assert sm.online is True


def test_get_state_is_pure_query_no_side_effects(configured_storage):
    """get_state() không tạo thư mục, không ghi file, không gọi atomic_json hay lock."""
    sm, root, _ = configured_storage
    with patch("src.core.storage_manager.atomic_json") as mock_atomic, \
         patch("pathlib.Path.mkdir") as mock_mkdir, \
         patch.object(sm, "lock") as mock_lock, \
         patch.object(sm, "snapshot") as mock_snapshot:
        state = sm.get_state()
        assert state in (StorageState.ONLINE, StorageState.FULL)
        mock_atomic.assert_not_called()
        mock_mkdir.assert_not_called()
        mock_lock.assert_not_called()
        mock_snapshot.assert_not_called()
