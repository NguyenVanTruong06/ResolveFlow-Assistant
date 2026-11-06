"""
tests/core/test_action_guard_preview.py
Kiểm thử đơn vị TDD toàn diện cho Thẩm quyền Preview (Preview Authority Guard - G04-04C6b-I).
"""
import os
from pathlib import Path
import pytest

from src.core.action_guard import (
    ActionGuardResult,
    check_action_availability,
)
from src.core.text_preset import TextStylePreset


class MockStorage:
    def __init__(self, root: str, library_id: str, online: bool = True):
        self._root = Path(root)
        self.library_id = library_id
        self._config = {"root": str(self._root), "library_id": library_id}
        self.online = online

    @property
    def root(self):
        return self._root


# ==============================================================================
# 1. Staging & Invalid Source Kind Gates
# ==============================================================================

def test_preview_staging_denied():
    """Tài nguyên staging chưa commit luôn bị từ chối preview với ERR_STAGING_UNPUBLISHED."""
    asset = {
        "id": "staging_asset",
        "name": "Staging Asset",
        "availability": "staging",
        "preview_path": "/some/proxy.mp4",
    }
    res = check_action_availability(asset, intent="preview")
    assert not res.allowed
    assert res.status == "staging"
    assert res.result_code == "ERR_STAGING_UNPUBLISHED"
    assert res.resolved_path == ""


def test_preview_invalid_source_kind_denied():
    """source_kind rõ ràng nhưng không hợp lệ bị từ chối với ERR_INVALID_SOURCE_KIND."""
    asset = {
        "id": "bad_src",
        "name": "Bad Source",
        "source_kind": "invalid_kind",
        "preview_path": "/some/proxy.mp4",
    }
    res = check_action_availability(asset, intent="preview")
    assert not res.allowed
    assert res.result_code == "ERR_INVALID_SOURCE_KIND"


# ==============================================================================
# 2. Managed Preview: Active Root, Identity, & Companion/Master Fallback
# ==============================================================================

def test_preview_managed_asset_within_active_root_allowed(tmp_path):
    """Managed asset có preview_path nằm trong cùng root kho hợp lệ -> OK_PREVIEW_VERIFIED."""
    root = tmp_path / "storage_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_preview_001"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    preview_file = root / "previews" / "clip_proxy.mp4"
    preview_file.parent.mkdir(parents=True, exist_ok=True)
    preview_file.write_bytes(b"proxy mp4 data")

    master_file = root / "clips" / "clip_master.mp4"
    master_file.parent.mkdir(parents=True, exist_ok=True)
    master_file.write_bytes(b"master prores video data")

    asset = {
        "id": "managed_01",
        "name": "Managed Video",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "clips/clip_master.mp4",
        "file_path": str(master_file),
        "preview_path": str(preview_file),
    }
    res = check_action_availability(asset, intent="preview", storage_provider=lambda: storage)
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_PREVIEW_VERIFIED"
    assert res.resolved_path == str(preview_file.resolve())


def test_preview_managed_fallback_to_file_path_master(tmp_path):
    """Managed asset khi preview_path rỗng hoặc mất trên đĩa -> fallback master file_path."""
    root = tmp_path / "storage_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_preview_002"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    master_file = root / "clips" / "clip_master.mp4"
    master_file.parent.mkdir(parents=True, exist_ok=True)
    master_file.write_bytes(b"master video data")

    # Case A: preview_path rỗng
    asset_empty_prev = {
        "id": "managed_02",
        "name": "Managed Video 2",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "clips/clip_master.mp4",
        "file_path": str(master_file),
        "preview_path": "",
    }
    res1 = check_action_availability(asset_empty_prev, intent="preview", storage_provider=lambda: storage)
    assert res1.allowed
    assert res1.result_code == "OK_PREVIEW_VERIFIED"
    assert res1.resolved_path == str(master_file.resolve())

    # Case B: preview_path trỏ tới file không tồn tại
    asset_missing_prev = {
        "id": "managed_03",
        "name": "Managed Video 3",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "clips/clip_master.mp4",
        "file_path": str(master_file),
        "preview_path": str(root / "previews" / "nonexistent.mp4"),
    }
    res2 = check_action_availability(asset_missing_prev, intent="preview", storage_provider=lambda: storage)
    assert res2.allowed
    assert res2.result_code == "OK_PREVIEW_VERIFIED"
    assert res2.resolved_path == str(master_file.resolve())


def test_preview_managed_storage_offline_denied(tmp_path):
    """Kho offline -> Deny ERR_STORAGE_OFFLINE (cấm phát dù file proxy cũ còn trên đĩa)."""
    root = tmp_path / "offline_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_preview_003"
    storage = MockStorage(root=str(root), library_id=lib_id, online=False)

    preview_file = root / "proxy.mp4"
    preview_file.write_bytes(b"proxy data")

    asset = {
        "id": "managed_offline",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "proxy.mp4",
        "preview_path": str(preview_file),
    }
    res = check_action_availability(asset, intent="preview", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_STORAGE_OFFLINE"
    assert res.resolved_path == ""


def test_preview_managed_foreign_root_mismatch_denied(tmp_path):
    """Kho đổi thư mục root khác origin_root -> Deny ERR_FOREIGN_ROOT."""
    origin_root = tmp_path / "old_root"
    origin_root.mkdir(parents=True, exist_ok=True)
    active_root = tmp_path / "new_root"
    active_root.mkdir(parents=True, exist_ok=True)

    lib_id = "lib_preview_004"
    storage = MockStorage(root=str(active_root), library_id=lib_id, online=True)

    preview_file = origin_root / "proxy.mp4"
    preview_file.write_bytes(b"proxy data")

    asset = {
        "id": "managed_foreign",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(origin_root),
        "library_path": "proxy.mp4",
        "preview_path": str(preview_file),
    }
    res = check_action_availability(asset, intent="preview", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.result_code == "ERR_FOREIGN_ROOT"


def test_preview_managed_source_missing_but_preview_present_allowed(tmp_path):
    """Master file bị xóa trên đĩa, nhưng preview proxy còn trong root -> cho phép preview-only."""
    root = tmp_path / "storage_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_preview_005"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    preview_file = root / "previews" / "cached_preview.mp4"
    preview_file.parent.mkdir(parents=True, exist_ok=True)
    preview_file.write_bytes(b"proxy data")

    master_file = root / "clips" / "deleted_master.mp4"
    # master_file không tồn tại

    asset = {
        "id": "managed_preview_only",
        "name": "Preview Only Video",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "clips/deleted_master.mp4",
        "file_path": str(master_file),
        "preview_path": str(preview_file),
    }
    # 1. Preview được phép
    res_prev = check_action_availability(asset, intent="preview", storage_provider=lambda: storage)
    assert res_prev.allowed
    assert res_prev.result_code == "OK_PREVIEW_VERIFIED"
    assert res_prev.resolved_path == str(preview_file.resolve())

    # 2. Nhưng drag và insert trên cùng asset bị từ chối ERR_FILE_NOT_FOUND
    res_drag = check_action_availability(asset, intent="drag", storage_provider=lambda: storage)
    assert not res_drag.allowed
    assert res_drag.result_code == "ERR_FILE_NOT_FOUND"

    res_insert = check_action_availability(asset, intent="insert", storage_provider=lambda: storage)
    assert not res_insert.allowed
    assert res_insert.result_code == "ERR_FILE_NOT_FOUND"


def test_preview_managed_traversal_and_symlink_escape_denied(tmp_path):
    """preview_path chứa .. hoặc symlink thoát khỏi active_root đều bị từ chối."""
    root = tmp_path / "safe_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_preview_006"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    # 1. Traversal ..
    asset_trav = {
        "id": "trav_preview",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "media/clip.mp4",
        "preview_path": str(root / ".." / "escape.mp4"),
    }
    res_trav = check_action_availability(asset_trav, intent="preview", storage_provider=lambda: storage)
    assert not res_trav.allowed
    assert res_trav.result_code == "ERR_INVALID_PATH_TRAVERSAL"

    # 2. Symlink escape
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    secret_file = outside / "secret.mp4"
    secret_file.write_bytes(b"secret")

    symlink_preview = root / "symlink_proxy.mp4"
    try:
        os.symlink(str(secret_file), str(symlink_preview))
    except (OSError, NotImplementedError):
        pytest.skip("Symlink không được hỗ trợ trên filesystem này")

    asset_sym = {
        "id": "sym_preview",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "media/clip.mp4",
        "preview_path": str(symlink_preview),
    }
    res_sym = check_action_availability(asset_sym, intent="preview", storage_provider=lambda: storage)
    assert not res_sym.allowed
    assert res_sym.result_code == "ERR_SYMLINK_ESCAPE"


# ==============================================================================
# 3. Linked Preview: Independent Root, Valid Anchor & Companion Verification
# ==============================================================================

def test_preview_linked_external_path_allowed_independent_of_storage(tmp_path):
    """Linked asset có external_path hợp lệ được phép preview bất kể kho chính offline hay đổi root."""
    ext_file = tmp_path / "external_media" / "footage.mp4"
    ext_file.parent.mkdir(parents=True, exist_ok=True)
    ext_file.write_bytes(b"external video content")

    storage_offline = MockStorage(root=str(tmp_path / "other_root"), library_id="other_lib", online=False)

    asset = {
        "id": "linked_prev_01",
        "source_kind": "linked",
        "external_path": str(ext_file),
        "origin_library_id": "storage_lib_999",
        "origin_root": str(tmp_path / "storage_root"),
    }
    res = check_action_availability(asset, intent="preview", storage_provider=lambda: storage_offline)
    assert res.allowed
    assert res.result_code == "OK_LINKED_PREVIEW_VERIFIED"
    assert res.resolved_path == str(ext_file.resolve())


def test_preview_linked_companion_preview_allowed(tmp_path):
    """Linked asset có companion preview cùng parent và cùng stem (theo producer scan_folder_cached) được phép phát."""
    ext_dir = tmp_path / "footage"
    ext_dir.mkdir(parents=True, exist_ok=True)
    master_file = ext_dir / "drone_shot.mov"
    master_file.write_bytes(b"prores master")
    companion_proxy = ext_dir / "drone_shot.mp4"
    companion_proxy.write_bytes(b"h264 proxy")

    asset = {
        "id": "linked_prev_comp",
        "source_kind": "linked",
        "external_path": str(master_file),
        "preview_path": str(companion_proxy),
    }
    res = check_action_availability(asset, intent="preview")
    assert res.allowed
    assert res.result_code == "OK_LINKED_PREVIEW_VERIFIED"
    assert res.resolved_path == str(companion_proxy.resolve())


def test_preview_linked_unrelated_same_folder_denied_fallbacks_to_master(tmp_path):
    """preview_path trỏ sang file không cùng stem trong cùng folder bị từ chối companion -> fallback master."""
    ext_dir = tmp_path / "footage"
    ext_dir.mkdir(parents=True, exist_ok=True)
    master_file = ext_dir / "clip_a.mov"
    master_file.write_bytes(b"master a")
    unrelated_file = ext_dir / "different_clip.mp4"
    unrelated_file.write_bytes(b"unrelated video")

    asset = {
        "id": "linked_unrelated",
        "source_kind": "linked",
        "external_path": str(master_file),
        "preview_path": str(unrelated_file),
    }
    res = check_action_availability(asset, intent="preview")
    assert res.allowed
    assert res.result_code == "OK_LINKED_PREVIEW_VERIFIED"
    # Fallback về master_file vì unrelated_file bị từ chối
    assert res.resolved_path == str(master_file.resolve())


# ==============================================================================
# 4. Builtin Assets & Bundled Native Transition Demo
# ==============================================================================

def test_preview_builtin_transition_demo_allowed(tmp_path):
    """Native transition có file demo MP4 bundled trong bundled_root được phép preview demo."""
    bundled_root = tmp_path / "app_assets"
    previews_dir = bundled_root / "templates" / "transitions" / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    demo_mp4 = previews_dir / "CrossDissolve.mp4"
    demo_mp4.write_bytes(b"demo transition video")

    asset = {
        "id": "trans_01",
        "name": "Cross Dissolve",
        "category": "trans",
        "source_kind": "builtin",
        "preview_path": str(demo_mp4),
        "file_path": str(bundled_root / "templates" / "transitions" / "CrossDissolve.setting"),
    }
    res = check_action_availability(asset, intent="preview", bundled_root=bundled_root)
    assert res.allowed
    assert res.result_code == "OK_BUILTIN_PREVIEW_VERIFIED"
    assert res.resolved_path == str(demo_mp4.resolve())


def test_preview_native_transition_demo_allowed_but_drag_denied(tmp_path):
    """Transition demo MP4 được phép xem trước, nhưng TUYỆT ĐỐI BỊ CHẶN khi drag và hướng dẫn khi insert."""
    bundled_root = tmp_path / "app_assets"
    demo_mp4 = bundled_root / "transitions" / "DipToBlack.mp4"
    demo_mp4.parent.mkdir(parents=True, exist_ok=True)
    demo_mp4.write_bytes(b"dip demo")

    asset = {
        "id": "trans_02",
        "name": "Dip to Black",
        "tab": "trans",
        "category": "templates",
        "preview_path": str(demo_mp4),
    }

    # 1. Preview -> Allowed demo MP4
    res_prev = check_action_availability(asset, intent="preview", bundled_root=bundled_root)
    assert res_prev.allowed
    assert res_prev.result_code in ("OK_BUILTIN_PREVIEW_VERIFIED", "OK_LEGACY_PREVIEW")
    assert res_prev.resolved_path == str(demo_mp4.resolve())

    # 2. Drag -> Denied cấm kéo demo MP4
    res_drag = check_action_availability(asset, intent="drag")
    assert not res_drag.allowed
    assert res_drag.result_code == "ERR_NATIVE_TRANSITION_DRAG_UNSUPPORTED"
    assert res_drag.is_native_guidance is True

    # 3. Insert -> Guidance
    res_ins = check_action_availability(asset, intent="insert")
    assert res_ins.allowed
    assert res_ins.result_code == "OK_NATIVE_TRANSITION_GUIDANCE"
    assert res_ins.is_native_guidance is True


def test_preview_builtin_outside_bundled_root_denied(tmp_path):
    """Builtin asset khai báo preview_path nằm ngoài bundled_root bị từ chối."""
    bundled_root = tmp_path / "bundled"
    bundled_root.mkdir(parents=True, exist_ok=True)
    outside_file = tmp_path / "outside" / "hack.mp4"
    outside_file.parent.mkdir(parents=True, exist_ok=True)
    outside_file.write_bytes(b"outside")

    asset = {
        "id": "builtin_bad",
        "source_kind": "builtin",
        "preview_path": str(outside_file),
    }
    res = check_action_availability(asset, intent="preview", bundled_root=bundled_root)
    assert not res.allowed
    assert res.result_code in ("ERR_SYMLINK_ESCAPE", "ERR_INVALID_PATH_TRAVERSAL")


# ==============================================================================
# 5. Origin-Only Fail-Closed & Procedural Canvas
# ==============================================================================

def test_preview_origin_only_failclosed(tmp_path):
    """source_kind khuyết + origin metadata cũ -> fail-closed ERR_ORIGIN_UNVERIFIED khi preview."""
    real_file = tmp_path / "stale.mp4"
    real_file.write_bytes(b"stale")

    asset = {
        "id": "stale_prev_01",
        "origin_library_id": "old_lib",
        "origin_root": str(tmp_path / "old_root"),
        "preview_path": str(real_file),
        "file_path": str(real_file),
    }
    res = check_action_availability(asset, intent="preview")
    assert not res.allowed
    assert res.result_code == "ERR_ORIGIN_UNVERIFIED"
    assert "làm mới danh mục kho" in res.reason


def test_preview_procedural_text_preset_returns_canvas_guidance():
    """TextStylePreset thật không có file media -> trả về OK_PROCEDURAL_PREVIEW_CANVAS với resolved_path rỗng."""
    preset = TextStylePreset(id="p1", name="Neon Kinetic")
    asset = {
        "id": "text_p1",
        "name": "Neon Kinetic",
        "category": "text",
        "_raw_obj": preset,
    }
    res = check_action_availability(asset, intent="preview")
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_PROCEDURAL_PREVIEW_CANVAS"
    assert res.resolved_path == ""


def test_preview_fake_or_managed_text_preset_not_procedural_canvas(tmp_path):
    """Fake class hoặc managed text mất file không được trả canvas guidance."""
    # 1. Fake text class
    class FakeText:
        pass

    asset_fake = {
        "id": "fake_t",
        "category": "text",
        "_raw_obj": FakeText(),
    }
    res1 = check_action_availability(asset_fake, intent="preview")
    assert not res1.allowed

    # 2. Managed text mất file
    root = tmp_path / "storage_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_text_01"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    asset_managed_text = {
        "id": "m_text",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "presets/missing.setting",
        "_raw_obj": TextStylePreset(id="m_text", name="Managed Text"),
    }
    res2 = check_action_availability(asset_managed_text, intent="preview", storage_provider=lambda: storage)
    assert not res2.allowed
    assert res2.result_code == "ERR_FILE_NOT_FOUND"


# ==============================================================================
# 6. Plain Legacy & Input Immutability
# ==============================================================================

def test_preview_plain_legacy_master_allowed(tmp_path):
    """Plain legacy không có origin và file thật tồn tại -> OK_LEGACY_PREVIEW."""
    real_file = tmp_path / "legacy.mp4"
    real_file.write_bytes(b"legacy data")

    asset = {
        "id": "legacy_01",
        "file_path": str(real_file),
    }
    res = check_action_availability(asset, intent="preview")
    assert res.allowed
    assert res.result_code == "OK_LEGACY_PREVIEW"
    assert res.resolved_path == str(real_file.resolve())


def test_preview_input_immutability(tmp_path):
    """check_action_availability với intent='preview' không làm thay đổi từ điển asset đầu vào."""
    real_file = tmp_path / "imm_prev.mp4"
    real_file.write_bytes(b"data")

    asset = {
        "id": "imm_prev_01",
        "name": "Immutable Preview",
        "preview_path": str(real_file),
        "file_path": str(real_file),
        "tags": ["a", "b"],
    }
    snapshot = dict(asset)
    snapshot["tags"] = list(asset["tags"])

    _ = check_action_availability(asset, intent="preview")
    assert asset == snapshot


# ==============================================================================
# 7. G04-04C6b-R1 Regression Probes: Boundaries & Fallback Verifications
# ==============================================================================

def test_preview_bundled_root_permission_error_fails_closed(tmp_path):
    """Probe 1: Khi bundled_root gặp lỗi phân quyền (PermissionError/EACCES),
    guard phải fail-closed (allowed=False), không được bỏ qua containment hay phát file tùy tiện."""
    arbitrary_file = tmp_path / "arbitrary.mp4"
    arbitrary_file.write_bytes(b"arbitrary")

    asset = {
        "id": "builtin_demo",
        "source_kind": "builtin",
        "preview_path": str(arbitrary_file),
    }

    def failing_realpath(p):
        if str(p) == "/fake/bundled/assets":
            raise PermissionError("[Errno 13] Permission denied: bundled_root")
        return str(Path(p).resolve())

    res = check_action_availability(
        asset,
        intent="preview",
        bundled_root="/fake/bundled/assets",
        realpath_fn=failing_realpath,
    )
    assert not res.allowed
    assert res.result_code == "ERR_FILESYSTEM_IO_OR_PERMISSION"


def test_preview_linked_missing_anchor_denied_even_if_companion_exists(tmp_path):
    """Probe 2: Tài nguyên linked khi anchor external_path không tồn tại trên đĩa
    thì tuyệt đối không được cấp quyền phát companion proxy cùng stem."""
    linked_dir = tmp_path / "linked_footage"
    linked_dir.mkdir(parents=True, exist_ok=True)

    companion_proxy = linked_dir / "scene_01.mp4"
    companion_proxy.write_bytes(b"proxy data")

    missing_anchor = linked_dir / "scene_01.braw"  # Master file không tồn tại

    asset = {
        "id": "linked_01",
        "source_kind": "linked",
        "external_path": str(missing_anchor),
        "preview_path": str(companion_proxy),
    }
    res = check_action_availability(asset, intent="preview")
    assert not res.allowed
    assert res.result_code == "ERR_FILE_NOT_FOUND"


def test_preview_managed_outside_root_falls_back_to_valid_master(tmp_path):
    """Probe 4: Managed asset có preview_path nằm ngoài active_root nhưng master file_path
    hợp lệ và tồn tại trong kho -> fallback phát master thành công."""
    root = tmp_path / "storage_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_preview_r1_004"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    master_file = root / "clips" / "scene.mp4"
    master_file.parent.mkdir(parents=True, exist_ok=True)
    master_file.write_bytes(b"valid master video")

    outside_dir = tmp_path / "outside_cache"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_preview = outside_dir / "scene_proxy.mp4"
    outside_preview.write_bytes(b"outside proxy")

    asset = {
        "id": "managed_r1_04",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "clips/scene.mp4",
        "file_path": str(master_file),
        "preview_path": str(outside_preview),
    }
    res = check_action_availability(asset, intent="preview", storage_provider=lambda: storage)
    assert res.allowed
    assert res.result_code == "OK_PREVIEW_VERIFIED"
    assert res.resolved_path == str(master_file.resolve())

