"""
tests/core/test_action_guard.py
Bộ kiểm thử TDD toàn diện cho Action-Time Authority & Availability Guard (G04-04C2-I).
"""
import errno
import os
from pathlib import Path
import stat
from unittest.mock import MagicMock
import pytest

from src.core.action_guard import (
    ActionGuardResult,
    check_action_availability,
)


class MockStorage:
    def __init__(self, root: str, library_id: str, online: bool = True):
        self._root = Path(root)
        self.library_id = library_id
        self._config = {"root": str(self._root), "library_id": library_id}
        self.online = online

    @property
    def root(self):
        return self._root


def test_invalid_asset_input_rejected():
    """1. Input không phải Mapping/dict bị từ chối với ERR_INVALID_ASSET_INPUT."""
    res1 = check_action_availability(None)
    assert not res1.allowed
    assert res1.status == "offline"
    assert res1.result_code == "ERR_INVALID_ASSET_INPUT"

    res2 = check_action_availability("not_a_dict")
    assert not res2.allowed
    assert res2.result_code == "ERR_INVALID_ASSET_INPUT"

    res3 = check_action_availability([1, 2, 3])
    assert not res3.allowed
    assert res3.result_code == "ERR_INVALID_ASSET_INPUT"


def test_unknown_intent_rejected():
    """2. Intent không được hỗ trợ (ngoài insert/inspect) bị từ chối với ERR_UNKNOWN_INTENT."""
    asset = {"id": "clip1", "name": "Clip", "file_path": "/some/path.mp4"}
    res = check_action_availability(asset, intent="unsupported_action")
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_UNKNOWN_INTENT"
    assert "không được hỗ trợ" in res.reason


def test_inspect_intent_allows_metadata():
    """3. Intent inspect luôn cho phép xem thông số metadata mà không đòi hỏi file."""
    asset_offline = {"id": "clip1", "name": "Clip", "availability": "offline"}
    res_off = check_action_availability(asset_offline, intent="inspect")
    assert res_off.allowed
    assert res_off.status == "offline"
    assert res_off.result_code == "OK_INSPECT_ONLY"

    asset_online = {"id": "clip2", "name": "Clip Online"}
    res_on = check_action_availability(asset_online, intent="inspect")
    assert res_on.allowed
    assert res_on.status == "online"
    assert res_on.result_code == "OK_INSPECT_ONLY"


def test_staging_asset_always_blocked():
    """4. Tài nguyên staging chưa commit luôn bị chặn với ERR_STAGING_UNPUBLISHED."""
    asset = {
        "id": "staging_clip",
        "name": "Staging Media",
        "availability": "staging",
        "source_kind": "managed",
    }
    res = check_action_availability(asset, intent="insert")
    assert not res.allowed
    assert res.status == "staging"
    assert res.result_code == "ERR_STAGING_UNPUBLISHED"
    assert "chưa sẵn sàng" in res.reason


def test_native_transition_guidance_allowed_without_preview():
    """5. Chuyển cảnh native với intent insert không đòi hỏi file preview MP4."""
    # Case A: Category trans
    asset_trans = {
        "id": "trans_01",
        "name": "Dip to Black",
        "category": "trans",
        "availability": "missing",  # Preview bị missing nhưng native insert vẫn allowed
    }
    res = check_action_availability(asset_trans, intent="insert")
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_NATIVE_TRANSITION_GUIDANCE"
    assert res.is_native_guidance is True

    # Case B: Template type trans
    asset_tt = {
        "id": "trans_02",
        "name": "Cross Dissolve",
        "category": "templates",
        "template_type": "trans",
    }
    res2 = check_action_availability(asset_tt, intent="insert")
    assert res2.allowed
    assert res2.is_native_guidance is True


def test_procedural_preset_true_generator_allowed():
    """6. Preset sinh mã Fusion động với runtime generator capability được cho phép."""
    class MockTextStylePreset:
        def generate(self):
            return "TextPlus { ... }"

    preset_obj = MockTextStylePreset()
    asset = {
        "id": "text_kinetic_01",
        "name": "Kinetic Title",
        "category": "text",
        "_raw_obj": preset_obj,
    }
    res = check_action_availability(asset, intent="insert")
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_PROCEDURAL_PRESET"


def test_managed_text_template_never_fallbacks_to_generator(tmp_path):
    """7. Known managed text template thiếu file KHÔNG ĐƯỢC fallback generator mà phải block."""
    root = tmp_path / "lib_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_text_001"

    asset = {
        "id": "managed_title_01",
        "name": "Title Template in Storage",
        "category": "text",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "templates/text/missing.setting",
        "file_path": str(root / "templates/text/missing.setting"),
    }
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    res = check_action_availability(asset, intent="insert", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_FILE_NOT_FOUND"
    assert "không tồn tại" in res.reason


def test_non_generator_generated_missing_file_blocked():
    """8. source_kind=generated nhưng không có generator object, thiếu file -> block."""
    asset = {
        "id": "gen_without_object",
        "name": "Custom Gen",
        "category": "titles",
        "source_kind": "generated",
        "file_path": "/nonexistent/generated/macro.setting",
    }
    res = check_action_availability(asset, intent="insert")
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_FILE_NOT_FOUND"


def test_managed_missing_origin_library_id_or_root_unverified(tmp_path):
    """9. source_kind=managed nhưng thiếu origin_library_id hoặc origin_root -> ERR_ORIGIN_UNVERIFIED."""
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    storage = MockStorage(root=str(root), library_id="lib_123", online=True)

    # Thiếu origin_library_id
    asset1 = {
        "id": "m1",
        "name": "Clip 1",
        "source_kind": "managed",
        "origin_root": str(root),
        "library_path": "pack/a.mp4",
    }
    res1 = check_action_availability(asset1, intent="insert", storage_provider=lambda: storage)
    assert not res1.allowed
    assert res1.status == "offline"
    assert res1.result_code == "ERR_ORIGIN_UNVERIFIED"

    # Thiếu origin_root
    asset2 = {
        "id": "m2",
        "name": "Clip 2",
        "source_kind": "managed",
        "origin_library_id": "lib_123",
        "library_path": "pack/a.mp4",
    }
    res2 = check_action_availability(asset2, intent="insert", storage_provider=lambda: storage)
    assert not res2.allowed
    assert res2.result_code == "ERR_ORIGIN_UNVERIFIED"


def test_managed_storage_offline_blocked(tmp_path):
    """10. Kho đang offline (storage.online == False) -> ERR_STORAGE_OFFLINE."""
    root = tmp_path / "root"
    storage = MockStorage(root=str(root), library_id="lib_123", online=False)

    asset = {
        "id": "m1",
        "name": "Clip 1",
        "source_kind": "managed",
        "origin_library_id": "lib_123",
        "origin_root": str(root),
        "library_path": "pack/a.mp4",
    }
    res = check_action_availability(asset, intent="insert", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_STORAGE_OFFLINE"


def test_managed_same_path_id_swap_blocked(tmp_path):
    """11. Cùng đường dẫn thư mục nhưng marker/config ID đổi -> ERR_STORAGE_IDENTITY_MISMATCH."""
    root = tmp_path / "same_root"
    root.mkdir(parents=True, exist_ok=True)

    # Storage hiện tại có ID mới
    storage = MockStorage(root=str(root), library_id="lib_new_456", online=True)

    # Asset trong bộ nhớ mang origin của thế hệ trước (ID cũ)
    asset = {
        "id": "m1",
        "name": "Clip 1",
        "source_kind": "managed",
        "origin_library_id": "lib_old_123",  # Mismatch!
        "origin_root": str(root),
        "library_path": "pack/a.mp4",
    }
    res = check_action_availability(asset, intent="insert", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_STORAGE_IDENTITY_MISMATCH"


def test_managed_foreign_root_blocked(tmp_path):
    """12. Đổi Root A -> Root B (Foreign Root): Asset Root A bị từ chối với ERR_FOREIGN_ROOT."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir(parents=True, exist_ok=True)
    root_b.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_common"

    # Kho hiện hành là Root B
    storage = MockStorage(root=str(root_b), library_id=lib_id, online=True)

    # Asset nạp từ Root A
    asset = {
        "id": "m1",
        "name": "Clip Root A",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root_a),  # Root A != Root B
        "library_path": "pack/a.mp4",
        "file_path": str(root_a / "pack/a.mp4"),
    }
    res = check_action_availability(asset, intent="insert", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_FOREIGN_ROOT"


def test_managed_symlink_escape_blocked(tmp_path):
    """13. Symlink trỏ ra ngoài kho gốc (Symlink Escape) -> ERR_SYMLINK_ESCAPE."""
    root = tmp_path / "safe_root"
    outside = tmp_path / "outside_secret"
    root.mkdir(parents=True, exist_ok=True)
    outside.mkdir(parents=True, exist_ok=True)

    target_file = outside / "secret.mp4"
    target_file.write_bytes(b"secret")

    # Tạo symlink trong root trỏ ra ngoài
    symlink_file = root / "escape.mp4"
    try:
        os.symlink(target_file, symlink_file)
    except OSError:
        pytest.skip("Hệ thống không hỗ trợ symlink.")

    lib_id = "lib_safe"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    asset = {
        "id": "sym1",
        "name": "Symlink Escape Asset",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "escape.mp4",
        "file_path": str(symlink_file),
    }
    res = check_action_availability(asset, intent="insert", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_SYMLINK_ESCAPE"


def test_managed_path_traversal_blocked(tmp_path):
    """14. Đường dẫn chứa traversal '..' bị từ chối với ERR_INVALID_PATH_TRAVERSAL."""
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_01"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    asset = {
        "id": "trav1",
        "name": "Traversal Asset",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "../etc/passwd",
    }
    res = check_action_availability(asset, intent="insert", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_INVALID_PATH_TRAVERSAL"


def test_path_is_directory_blocked(tmp_path):
    """15. Đường dẫn trỏ tới thư mục thay vì file media -> ERR_PATH_IS_DIRECTORY."""
    dir_path = tmp_path / "some_folder"
    dir_path.mkdir(parents=True, exist_ok=True)

    asset = {
        "id": "dir1",
        "name": "Directory As File",
        "file_path": str(dir_path),
    }
    res = check_action_availability(asset, intent="insert")
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_PATH_IS_DIRECTORY"
    assert "thư mục" in res.reason


def test_stat_permission_and_eio_error_handling():
    """16. Lỗi phân quyền hoặc I/O (EACCES/EIO) trả về status=offline và ERR_FILESYSTEM_IO_OR_PERMISSION."""
    def mock_stat_perm(path):
        err = OSError("Permission denied")
        err.errno = errno.EACCES
        raise err

    asset = {
        "id": "perm1",
        "name": "Locked File",
        "file_path": "/media/locked.mp4",
    }
    res_perm = check_action_availability(asset, intent="insert", stat_fn=mock_stat_perm)
    assert not res_perm.allowed
    assert res_perm.status == "offline"
    assert res_perm.result_code == "ERR_FILESYSTEM_IO_OR_PERMISSION"

    def mock_stat_eio(path):
        err = OSError("Input/output error")
        err.errno = errno.EIO
        raise err

    res_eio = check_action_availability(asset, intent="insert", stat_fn=mock_stat_eio)
    assert not res_eio.allowed
    assert res_eio.status == "offline"
    assert res_eio.result_code == "ERR_FILESYSTEM_IO_OR_PERMISSION"


def test_linked_external_offline_primary_root_independent(tmp_path):
    """17. Tài nguyên linked có file ngoài tồn tại vẫn chèn được ngay cả khi kho chính offline."""
    ext_file = tmp_path / "external_media.mp4"
    ext_file.write_bytes(b"ext content")

    asset = {
        "id": "link1",
        "name": "Linked Broll",
        "source_kind": "linked",
        "external_path": str(ext_file),
        "file_path": str(ext_file),
        "availability": "offline",  # Do kho chính offline, nhưng linked độc lập
    }
    # Primary storage offline
    storage = MockStorage(root="/nonexistent", library_id="none", online=False)

    res = check_action_availability(asset, intent="insert", storage_provider=lambda: storage)
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_LINKED_MEDIA_VERIFIED"
    assert res.resolved_path == str(ext_file.resolve())


def test_linked_missing_file_blocked(tmp_path):
    """18. Tài nguyên linked nhưng file ngoài bị xóa -> ERR_FILE_NOT_FOUND."""
    asset = {
        "id": "link_missing",
        "name": "Linked Missing",
        "source_kind": "linked",
        "external_path": str(tmp_path / "not_there.mp4"),
    }
    res = check_action_availability(asset, intent="insert")
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_FILE_NOT_FOUND"


def test_linked_path_conflict_blocked(tmp_path):
    """19. Tài nguyên linked có external_path và file_path xung đột -> ERR_PATH_CONFLICT."""
    f1 = tmp_path / "file1.mp4"
    f2 = tmp_path / "file2.mp4"
    f1.write_bytes(b"1")
    f2.write_bytes(b"2")

    asset = {
        "id": "link_conflict",
        "name": "Linked Conflict",
        "source_kind": "linked",
        "external_path": str(f1),
        "file_path": str(f2),
    }
    res = check_action_availability(asset, intent="insert")
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_PATH_CONFLICT"


def test_builtin_media_deleted_blocked(tmp_path):
    """20. source_kind=builtin WAV/LUT/MP4 nhưng file bị xóa khỏi app bundle -> ERR_FILE_NOT_FOUND."""
    asset = {
        "id": "builtin_sfx_missing",
        "name": "App SFX Deleted",
        "source_kind": "builtin",
        "category": "sfx",
        "file_path": str(tmp_path / "missing_app_sfx.wav"),
    }
    res = check_action_availability(asset, intent="insert")
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_FILE_NOT_FOUND"


def test_builtin_media_exists_allowed(tmp_path):
    """21. source_kind=builtin WAV/LUT/MP4 với file tồn tại -> OK_BUILTIN_MEDIA_VERIFIED."""
    sfx_file = tmp_path / "pop.wav"
    sfx_file.write_bytes(b"audio")

    asset = {
        "id": "builtin_sfx_ok",
        "name": "App SFX Pop",
        "source_kind": "builtin",
        "category": "sfx",
        "file_path": str(sfx_file),
    }
    res = check_action_availability(asset, intent="insert")
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_BUILTIN_MEDIA_VERIFIED"


def test_legacy_ordinary_media_exists_allowed(tmp_path):
    """22. Tài nguyên legacy không có source_kind, file tồn tại -> OK_LEGACY_MEDIA."""
    leg_file = tmp_path / "legacy.mp4"
    leg_file.write_bytes(b"video")

    asset = {
        "id": "legacy_clip_01",
        "name": "Old Project Clip",
        "file_path": str(leg_file),
    }
    res = check_action_availability(asset, intent="insert")
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_LEGACY_MEDIA"


def test_input_dict_not_mutated(tmp_path):
    """23. check_action_availability tuyệt đối không mutate input dictionary."""
    f = tmp_path / "c.mp4"
    f.write_bytes(b"content")

    original = {
        "id": "c1",
        "name": "Clip",
        "file_path": str(f),
        "source_kind": "builtin",
    }
    copy_orig = dict(original)

    _ = check_action_availability(original, intent="insert")
    assert original == copy_orig


def test_pure_injected_zero_real_io():
    """24. Pure mode: Inject mock stat_fn & realpath_fn, zero real filesystem access."""
    calls = []

    def mock_stat(p):
        calls.append(("stat", p))
        st = MagicMock()
        st.st_mode = stat.S_IFREG | 0o644
        return st

    def mock_realpath(p):
        calls.append(("realpath", p))
        return p

    asset = {
        "id": "mock_clip",
        "name": "Mock Clip",
        "file_path": "/fake/virtual/path.mp4",
    }
    res = check_action_availability(
        asset,
        intent="insert",
        stat_fn=mock_stat,
        realpath_fn=mock_realpath,
    )
    assert res.allowed
    assert len(calls) >= 1
    assert calls[0][0] in ("realpath", "stat")


# ==============================================================================
# BỘ KIỂM THỬ HỒI QUY G04-04C2-I-R1 (VALIDATION VÀ DEPENDENCY FAILURES)
# ==============================================================================

@pytest.mark.parametrize("invalid_kind", [
    [],
    {},
    True,
    False,
    123,
    "",
    "   ",
    "unknown_kind",
    "unsupported",
])
def test_explicit_invalid_source_kind_rejected(invalid_kind):
    """
    R1.1: Bất kỳ giá trị source_kind tường minh nào không hợp lệ (không phải str
    hoặc không nằm trong VALID_SOURCE_KINDS) đều bị từ chối với ERR_INVALID_SOURCE_KIND,
    không được fallback sang legacy.
    """
    def fake_stat(p):
        st = MagicMock()
        st.st_mode = stat.S_IFREG | 0o644
        return st

    asset = {
        "id": "clip_bad_source",
        "name": "Bad Source Clip",
        "file_path": "/virtual/media.wav",
        "source_kind": invalid_kind,
    }
    res = check_action_availability(
        asset,
        intent="insert",
        stat_fn=fake_stat,
        realpath_fn=lambda p: str(p),
    )
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_INVALID_SOURCE_KIND"
    assert "source_kind" in res.reason or "không hợp lệ" in res.reason


@pytest.mark.parametrize("missing_or_none", [None, "MISSING_KEY"])
def test_missing_or_none_source_kind_allows_legacy_fallback(missing_or_none):
    """
    R1.2: Chỉ khi source_kind là None hoặc khuyết thiếu hoàn toàn mới được fallback sang legacy.
    """
    def fake_stat(p):
        st = MagicMock()
        st.st_mode = stat.S_IFREG | 0o644
        return st

    asset = {
        "id": "legacy_clip",
        "name": "Legacy Media",
        "file_path": "/virtual/legacy.wav",
    }
    if missing_or_none != "MISSING_KEY":
        asset["source_kind"] = None

    res = check_action_availability(
        asset,
        intent="insert",
        stat_fn=fake_stat,
        realpath_fn=lambda p: str(p),
    )
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_LEGACY_MEDIA"


@pytest.mark.parametrize("branch", ["managed", "linked", "builtin", "legacy"])
@pytest.mark.parametrize("exc_to_raise", [
    PermissionError("Permission denied"),
    OSError(errno.EACCES, "Permission denied"),
    OSError(errno.EIO, "I/O error"),
])
def test_realpath_fn_permission_and_io_errors_handled_fail_closed(branch, exc_to_raise):
    """
    R1.3: realpath_fn ném PermissionError/EACCES/EIO ở mọi nhánh nguồn (managed, linked, builtin, legacy)
    đều được bắt an toàn trong helper, trả về offline ERR_FILESYSTEM_IO_OR_PERMISSION,
    không để ngoại lệ rò rỉ ra caller.
    """
    def raising_realpath(p):
        raise exc_to_raise

    def fake_stat(p):
        st = MagicMock()
        st.st_mode = stat.S_IFREG | 0o644
        return st

    storage = MockStorage(root="/virtual/lib_root", library_id="lib_01", online=True)

    if branch == "managed":
        asset = {
            "id": "m1",
            "name": "Managed Asset",
            "source_kind": "managed",
            "origin_library_id": "lib_01",
            "origin_root": "/virtual/lib_root",
            "library_path": "pack/video.mp4",
            "file_path": "/virtual/lib_root/pack/video.mp4",
        }
    elif branch == "linked":
        asset = {
            "id": "l1",
            "name": "Linked Asset",
            "source_kind": "linked",
            "external_path": "/ext/footage.mp4",
        }
    elif branch == "builtin":
        asset = {
            "id": "b1",
            "name": "Builtin Asset",
            "source_kind": "builtin",
            "file_path": "/app/assets/ding.wav",
        }
    else:  # legacy
        asset = {
            "id": "leg1",
            "name": "Legacy Asset",
            "file_path": "/legacy/clip.mp4",
        }

    res = check_action_availability(
        asset,
        intent="insert",
        storage_provider=lambda: storage,
        stat_fn=fake_stat,
        realpath_fn=raising_realpath,
    )
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_FILESYSTEM_IO_OR_PERMISSION"


@pytest.mark.parametrize("invalid_realpath_return", [None, "", "   ", 123, []])
def test_realpath_fn_returns_invalid_type_fail_closed(invalid_realpath_return):
    """
    R1.4: realpath_fn trả về giá trị không hợp lệ (None, chuỗi rỗng, không phải str)
    được xử lý fail-closed với ERR_PATH_RESOLVE_FAILURE hoặc ERR_MEDIA_PATH_EMPTY.
    """
    asset = {
        "id": "leg_bad_realpath",
        "name": "Legacy Asset",
        "file_path": "/path/sample.mp4",
    }
    res = check_action_availability(
        asset,
        intent="insert",
        realpath_fn=lambda p: invalid_realpath_return,
    )
    assert not res.allowed
    assert res.status in ("offline", "missing")
    assert res.result_code in ("ERR_PATH_RESOLVE_FAILURE", "ERR_MEDIA_PATH_EMPTY")


def test_storage_provider_property_exceptions_handled_fail_closed():
    """
    R1.5: Truy cập thuộc tính của storage_provider (online, root, _config) ném ngoại lệ
    hoặc _config không phải mapping đều được xử lý an toàn với ERR_STORAGE_PROVIDER_FAILURE.
    """
    class BadStorage:
        @property
        def online(self):
            raise RuntimeError("Database connection lost during online check")

        @property
        def root(self):
            raise OSError(errno.EIO, "Disk failure on root")

        _config = "not_a_mapping"

    asset = {
        "id": "m_bad_storage",
        "name": "Managed Media",
        "source_kind": "managed",
        "origin_library_id": "lib_01",
        "origin_root": "/virtual/root",
        "library_path": "pack/video.mp4",
        "file_path": "/virtual/root/pack/video.mp4",
    }
    res = check_action_availability(
        asset,
        intent="insert",
        storage_provider=lambda: BadStorage(),
        realpath_fn=lambda p: str(p),
    )
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_STORAGE_PROVIDER_FAILURE"


@pytest.mark.parametrize("bad_stat_result", [
    None,
    "not_a_stat_result",
    MagicMock(st_mode="not_an_int"),
    MagicMock(spec=[]),  # thiếu st_mode
])
def test_stat_fn_returns_invalid_data_fail_closed(bad_stat_result):
    """
    R1.6: stat_fn trả về đối tượng không hợp lệ hoặc thiếu st_mode
    được xử lý fail-closed với ERR_STAT_FAILURE.
    """
    asset = {
        "id": "leg_bad_stat",
        "name": "Legacy Media",
        "file_path": "/virtual/clip.mp4",
    }
    res = check_action_availability(
        asset,
        intent="insert",
        stat_fn=lambda p: bad_stat_result,
        realpath_fn=lambda p: str(p),
    )
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_STAT_FAILURE"


# ==============================================================================
# BỘ KIỂM THỬ HỒI QUY G04-04C2-I-R2 (GENERATED MEDIA CÓ FILE HỢP LỆ)
# ==============================================================================

def test_generated_media_file_exists_allowed_with_stat_called(tmp_path):
    """
    R2.1: source_kind='generated' có file vật lý hợp lệ trên đĩa được guard cho phép,
    trả về OK_GENERATED_MEDIA_VERIFIED và resolved_path chuẩn xác.
    Phép thử stat_fn được gọi đúng trình tự.
    """
    real_media = tmp_path / "ai_voice.wav"
    real_media.write_bytes(b"RIFFwav_data")

    stat_calls = []
    def spy_stat(p):
        stat_calls.append(p)
        return os.stat(p)

    asset = {
        "id": "gen_voice_01",
        "name": "Generated Voice",
        "category": "sfx",
        "source_kind": "generated",
        "file_path": str(real_media),
    }

    res = check_action_availability(
        asset,
        intent="insert",
        stat_fn=spy_stat,
    )
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_GENERATED_MEDIA_VERIFIED"
    assert res.resolved_path == str(real_media.resolve())
    assert len(stat_calls) >= 1
    assert str(real_media.resolve()) in [str(Path(p).resolve()) for p in stat_calls]


def test_generated_media_file_missing_denied():
    """
    R2.2: source_kind='generated' nhưng file nguồn không tồn tại -> bị từ chối với ERR_FILE_NOT_FOUND.
    """
    stat_calls = []
    def spy_stat(p):
        stat_calls.append(p)
        raise FileNotFoundError("Missing generated file")

    asset = {
        "id": "gen_missing",
        "name": "Missing Generated Video",
        "category": "broll",
        "source_kind": "generated",
        "file_path": "/virtual/generated/clip.mp4",
    }
    res = check_action_availability(
        asset,
        intent="insert",
        stat_fn=spy_stat,
        realpath_fn=lambda p: str(p),
    )
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_FILE_NOT_FOUND"
    assert len(stat_calls) == 1


def test_generated_media_path_empty_denied():
    """
    R2.3: source_kind='generated' nhưng không có file_path hoặc path rỗng -> ERR_MEDIA_PATH_EMPTY.
    """
    asset = {
        "id": "gen_no_path",
        "name": "Generated Without Path",
        "category": "sfx",
        "source_kind": "generated",
        "file_path": "",
    }
    res = check_action_availability(asset, intent="insert")
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_MEDIA_PATH_EMPTY"


def test_generated_media_staging_denied_before_io():
    """
    R2.4: source_kind='generated' có file tồn tại nhưng availability='staging'
    phải bị chặn ngay tại Staging Gate trước khi thực hiện bất kỳ lệnh I/O nào.
    """
    stat_calls = []
    def spy_stat(p):
        stat_calls.append(p)
        st = MagicMock()
        st.st_mode = stat.S_IFREG | 0o644
        return st

    asset = {
        "id": "gen_staging",
        "name": "Staging Generated Video",
        "source_kind": "generated",
        "availability": "staging",
        "file_path": "/virtual/staging_video.mp4",
    }
    res = check_action_availability(
        asset,
        intent="insert",
        stat_fn=spy_stat,
    )
    assert not res.allowed
    assert res.status == "staging"
    assert res.result_code == "ERR_STAGING_UNPUBLISHED"
    assert len(stat_calls) == 0, "Không được gọi stat_fn khi tài nguyên đang ở trạng thái staging"


def test_procedural_generator_allowed_without_file():
    """
    R2.5: Recognized procedural runtime generator (như TextStylePreset) được miễn thử file đĩa.
    """
    stat_calls = []
    class TextStylePreset:
        pass

    preset = TextStylePreset()
    asset = {
        "id": "preset_macro",
        "name": "Dynamic Title Preset",
        "category": "titles",
        "source_kind": "generated",
        "_raw_obj": preset,
    }
    res = check_action_availability(
        asset,
        intent="insert",
        stat_fn=lambda p: stat_calls.append(p),
    )
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_PROCEDURAL_PRESET"
    assert len(stat_calls) == 0, "Procedural runtime generator không được thử file đĩa"


def test_managed_text_missing_file_denied_never_generator_fallback(tmp_path):
    """
    R2.6: Managed text template trong kho thiếu file bắt buộc phải deny,
    tuyệt đối không được fallback sang procedural generator.
    """
    root = tmp_path / "lib_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_text_001"

    asset = {
        "id": "managed_text_01",
        "name": "Managed Text Template",
        "category": "text",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "templates/kinetic.setting",
        "file_path": str(root / "templates/kinetic.setting"),
    }
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    res = check_action_availability(
        asset,
        intent="insert",
        storage_provider=lambda: storage,
    )
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_FILE_NOT_FOUND"


