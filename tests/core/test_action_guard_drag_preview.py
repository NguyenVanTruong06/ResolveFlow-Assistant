"""
tests/core/test_action_guard_drag_preview.py
Bộ kiểm thử TDD cho ActionGuard với intent="drag" và trạng thái deferred của intent="preview" (G04-04C6a-I).
"""
import errno
import os
from pathlib import Path
import stat
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
# 1. Unsupported Intent Gate
# ==============================================================================

def test_unsupported_intent_denied_in_action_guard():
    """Intent không nằm trong tập (insert, inspect, drag, preview) bị từ chối với ERR_UNKNOWN_INTENT."""
    asset = {"id": "asset1", "name": "Asset", "file_path": "/some/media.mp4"}
    res = check_action_availability(asset, intent="export_timeline")
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_UNKNOWN_INTENT"
    assert "không được hỗ trợ" in res.reason


# ==============================================================================
# 2. Transition Drag Denied (All Transition Forms)
# ==============================================================================

def test_drag_native_transition_by_category_denied(tmp_path):
    """Transition nhận diện qua category='trans' bị deny khi kéo thả, dù preview file có tồn tại."""
    preview_mp4 = tmp_path / "trans_preview.mp4"
    preview_mp4.write_bytes(b"dummy mp4 video content")

    asset = {
        "id": "trans_dip_black",
        "name": "Dip to Black",
        "category": "trans",
        "file_path": str(preview_mp4),
        "preview_path": str(preview_mp4),
        "availability": "online",
    }
    res = check_action_availability(asset, intent="drag")
    assert not res.allowed
    assert res.status == "online"
    assert res.result_code == "ERR_NATIVE_TRANSITION_DRAG_UNSUPPORTED"
    assert res.is_native_guidance is True
    assert res.resolved_path == ""


def test_drag_native_transition_by_tab_and_template_type_denied(tmp_path):
    """Transition nhận diện qua tab hoặc template_type='trans' đều bị deny khi kéo thả, dù category='templates'."""
    file_real = tmp_path / "trans_demo.mp4"
    file_real.write_bytes(b"demo mp4")

    # Category là 'templates' nhưng tab là 'trans'
    asset1 = {
        "id": "trans_tab",
        "name": "Cross Dissolve",
        "category": "templates",
        "tab": "trans",
        "file_path": str(file_real),
    }
    res1 = check_action_availability(asset1, intent="drag")
    assert not res1.allowed
    assert res1.result_code == "ERR_NATIVE_TRANSITION_DRAG_UNSUPPORTED"
    assert res1.is_native_guidance is True

    asset2 = {
        "id": "trans_tt",
        "name": "Edge Wipe",
        "template_type": "transition",
    }
    res2 = check_action_availability(asset2, intent="drag")
    assert not res2.allowed
    assert res2.result_code == "ERR_NATIVE_TRANSITION_DRAG_UNSUPPORTED"
    assert res2.is_native_guidance is True


def test_drag_transition_preset_runtime_object_denied():
    """TransitionStylePreset runtime object bị deny khi kéo thả trong slice 04C6a."""
    class MockTransitionStylePreset:
        def __init__(self, id_val, name):
            self.id = id_val
            self.name = name
            self.transition_type = "CrossDissolve"

    preset = MockTransitionStylePreset("tr_preset_1", "Smooth Cut")
    asset = {
        "id": "tr_asset",
        "name": "Smooth Cut",
        "_raw_obj": preset,
    }
    res = check_action_availability(asset, intent="drag")
    assert not res.allowed
    assert res.result_code == "ERR_NATIVE_TRANSITION_DRAG_UNSUPPORTED"
    assert res.is_native_guidance is True


# ==============================================================================
# 3. Procedural Drag vs Managed Text Guard
# ==============================================================================

def test_drag_procedural_real_text_style_preset_allowed():
    """Runtime TextStylePreset thực và không có bằng chứng managed -> OK_PROCEDURAL_DRAG_SETTING."""
    preset = TextStylePreset(id="text_kinetic_1", name="Kinetic Glow")
    asset = {
        "id": "text_kinetic_1",
        "name": "Kinetic Glow",
        "category": "text",
        "_raw_obj": preset,
    }
    res = check_action_availability(asset, intent="drag")
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_PROCEDURAL_DRAG_SETTING"
    assert res.resolved_path == ""  # Export thuộc trách nhiệm của UI layer


def test_drag_fake_text_preset_denied():
    """Chỉ có category='text' hoặc class name giả TextStylePreset không có TextStylePreset thật -> deny."""
    asset_fake_cat = {
        "id": "fake_text_1",
        "name": "Fake Text Preset",
        "category": "text",
    }
    res1 = check_action_availability(asset_fake_cat, intent="drag")
    assert not res1.allowed
    assert res1.result_code == "ERR_PROCEDURAL_DRAG_UNSUPPORTED"

    # Giả lập đối tượng có tên class 'TextStylePreset' nhưng không phải domain type thật
    class FakeTextStylePreset:
        pass
    fake_obj = FakeTextStylePreset()
    fake_obj.__class__.__name__ = "TextStylePreset"

    asset_fake_class = {
        "id": "fake_text_class",
        "name": "Fake Class Spoof",
        "category": "text",
        "_raw_obj": fake_obj,
    }
    res_spoof = check_action_availability(asset_fake_class, intent="drag")
    assert not res_spoof.allowed
    assert res_spoof.result_code == "ERR_PROCEDURAL_DRAG_UNSUPPORTED"

    asset_fake_gen = {
        "id": "fake_text_2",
        "name": "Fake Generated Text",
        "source_kind": "generated",
        "category": "text",
    }
    res2 = check_action_availability(asset_fake_gen, intent="drag")
    assert not res2.allowed
    assert res2.result_code == "ERR_PROCEDURAL_DRAG_UNSUPPORTED"


def test_drag_managed_text_template_missing_file_never_falls_back_to_procedural(tmp_path):
    """Managed text template mất file vật lý -> ERR_FILE_NOT_FOUND, TUYỆT ĐỐI KHÔNG fallback procedural."""
    root = tmp_path / "library_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_title_001"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    # Có _raw_obj là TextStylePreset nhưng có bằng chứng managed
    preset = TextStylePreset(id="managed_title_preset", name="Managed Title")
    asset = {
        "id": "managed_title_preset",
        "name": "Managed Title",
        "category": "text",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "titles/missing_template.setting",
        "file_path": str(root / "titles/missing_template.setting"),
        "_raw_obj": preset,
    }
    res = check_action_availability(asset, intent="drag", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_FILE_NOT_FOUND"


# ==============================================================================
# 4. Drag Positive: Managed, Linked, Builtin, Generated File-Backed Media
# ==============================================================================

def test_drag_managed_media_verified_allowed(tmp_path):
    """Managed media hợp lệ được phép kéo thả với resolved_path chuẩn xác."""
    root = tmp_path / "lib_root"
    media_dir = root / "memes"
    media_dir.mkdir(parents=True, exist_ok=True)
    media_file = media_dir / "cat_cry.mp4"
    media_file.write_bytes(b"cat cry video content")

    lib_id = "lib_media_001"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    asset = {
        "id": "cat_cry_01",
        "name": "Cat Cry",
        "category": "meme",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "memes/cat_cry.mp4",
        "file_path": str(media_file),
    }
    res = check_action_availability(asset, intent="drag", storage_provider=lambda: storage)
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_MANAGED_MEDIA_VERIFIED"
    assert res.resolved_path == str(media_file.resolve())


def test_drag_linked_media_verified_allowed(tmp_path):
    """Linked media hợp lệ trên đĩa được phép kéo thả."""
    ext_file = tmp_path / "external_sound.wav"
    ext_file.write_bytes(b"wav audio data")

    asset = {
        "id": "ext_sfx_01",
        "name": "External Sound",
        "category": "sfx",
        "source_kind": "linked",
        "external_path": str(ext_file),
        "file_path": str(ext_file),
    }
    res = check_action_availability(asset, intent="drag")
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_LINKED_MEDIA_VERIFIED"
    assert res.resolved_path == str(ext_file.resolve())


def test_drag_builtin_and_generated_file_backed_allowed(tmp_path):
    """Builtin và generated media có file vật lý hợp lệ được phép kéo thả."""
    b_file = tmp_path / "builtin.cube"
    b_file.write_bytes(b"LUT DATA")

    asset_b = {
        "id": "b_lut",
        "name": "Builtin LUT",
        "category": "lut",
        "source_kind": "builtin",
        "file_path": str(b_file),
    }
    res_b = check_action_availability(asset_b, intent="drag")
    assert res_b.allowed
    assert res_b.result_code == "OK_BUILTIN_MEDIA_VERIFIED"
    assert res_b.resolved_path == str(b_file.resolve())


# ==============================================================================
# 5. Drag Negative: Staging, Foreign Root, Symlink, Traversal, Metadata Conflicts
# ==============================================================================

def test_drag_staging_asset_denied():
    """Tài nguyên staging chưa commit bị chặn khi kéo thả."""
    asset = {
        "id": "staging_video",
        "name": "Staging Video",
        "availability": "staging",
        "file_path": "/tmp/downloading.mp4",
    }
    res = check_action_availability(asset, intent="drag")
    assert not res.allowed
    assert res.status == "staging"
    assert res.result_code == "ERR_STAGING_UNPUBLISHED"


def test_drag_invalid_source_kind_denied():
    """source_kind explicit không hợp lệ bị từ chối ngay lập tức."""
    asset = {
        "id": "invalid_kind_asset",
        "name": "Bad Kind",
        "source_kind": "unknown_custom_kind",
        "file_path": "/some/path.mp4",
    }
    res = check_action_availability(asset, intent="drag")
    assert not res.allowed
    assert res.result_code == "ERR_INVALID_SOURCE_KIND"


def test_drag_managed_foreign_root_denied(tmp_path):
    """Tài nguyên thuộc kho cũ (root A) khi kho hiện tại là root B bị từ chối."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir(parents=True, exist_ok=True)
    root_b.mkdir(parents=True, exist_ok=True)

    lib_id = "shared_lib_id"
    storage = MockStorage(root=str(root_b), library_id=lib_id, online=True)

    asset = {
        "id": "asset_old_root",
        "name": "Asset From Root A",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root_a),
        "library_path": "media/clip.mp4",
        "file_path": str(root_a / "media/clip.mp4"),
    }
    res = check_action_availability(asset, intent="drag", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_FOREIGN_ROOT"


def test_drag_managed_missing_origin_denied(tmp_path):
    """Tài nguyên có storage_managed=True nhưng thiếu origin_library_id/origin_root -> ERR_ORIGIN_UNVERIFIED."""
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    storage = MockStorage(root=str(root), library_id="lib_1", online=True)

    asset = {
        "id": "managed_no_origin",
        "name": "Managed No Origin",
        "storage_managed": True,
        "library_path": "media/clip.mp4",
        "file_path": str(root / "media/clip.mp4"),
    }
    res = check_action_availability(asset, intent="drag", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_ORIGIN_UNVERIFIED"


def test_drag_linked_media_with_origin_metadata_allowed(tmp_path):
    """Linked media do StorageManager gắn origin_library_id/origin_root vẫn hợp lệ cho cả drag và insert."""
    ext_file = tmp_path / "outside_storage" / "linked_clip.mp4"
    ext_file.parent.mkdir(parents=True, exist_ok=True)
    ext_file.write_bytes(b"linked video data")

    root = tmp_path / "storage_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_storage_001"

    # Payload chuẩn sinh ra từ StorageManager.assets() cho nguồn linked
    asset = {
        "id": "linked_01",
        "name": "Linked Footage",
        "category": "broll",
        "source_kind": "linked",
        "external_path": str(ext_file),
        "file_path": str(ext_file),
        "origin_library_id": lib_id,
        "origin_root": str(root),
    }

    # 1. Thẩm định cho drag
    res_drag = check_action_availability(asset, intent="drag")
    assert res_drag.allowed
    assert res_drag.status == "online"
    assert res_drag.result_code == "OK_LINKED_MEDIA_VERIFIED"
    assert res_drag.resolved_path == str(ext_file.resolve())

    # 2. Thẩm định cho insert (không bị đổi behavior ngoài slice)
    res_insert = check_action_availability(asset, intent="insert")
    assert res_insert.allowed
    assert res_insert.status == "online"
    assert res_insert.result_code == "OK_LINKED_MEDIA_VERIFIED"

    # 3. Khi Storage đổi root hoặc offline, linked media ngoài ổ đĩa vẫn giữ authority độc lập
    storage_offline = MockStorage(root=str(tmp_path / "other_root"), library_id="other_lib", online=False)
    res_offline = check_action_availability(asset, intent="drag", storage_provider=lambda: storage_offline)
    assert res_offline.allowed
    assert res_offline.result_code == "OK_LINKED_MEDIA_VERIFIED"


def test_drag_conflicting_source_metadata_denied(tmp_path):
    """Contradictory ownership: linked nhưng storage_managed=True, hoặc builtin/generated có storage_managed=True."""
    # 1. Linked nhưng storage_managed = True
    asset_linked_conflict = {
        "id": "conflict_linked",
        "name": "Conflicting Linked",
        "source_kind": "linked",
        "storage_managed": True,
        "external_path": str(tmp_path / "clip.mp4"),
    }
    res1 = check_action_availability(asset_linked_conflict, intent="drag")
    assert not res1.allowed
    assert res1.result_code == "ERR_CONFLICTING_SOURCE_METADATA"

    # 2. Builtin nhưng storage_managed = True hoặc có library_path
    asset_builtin_conflict = {
        "id": "conflict_builtin",
        "name": "Conflicting Builtin",
        "source_kind": "builtin",
        "storage_managed": True,
        "file_path": "/some/file.mp4",
    }
    res2 = check_action_availability(asset_builtin_conflict, intent="drag")
    assert not res2.allowed
    assert res2.result_code == "ERR_CONFLICTING_SOURCE_METADATA"


def test_drag_source_deleted_after_selection_denied(tmp_path):
    """Source file bị xóa sau khi chọn; preview_path tồn tại cũng không cứu được source đã mất."""
    root = tmp_path / "lib_root"
    media_dir = root / "clips"
    media_dir.mkdir(parents=True, exist_ok=True)
    source_file = media_dir / "deleted_video.mp4"
    # File không tồn tại trên đĩa

    preview_file = tmp_path / "cache" / "preview_proxy.mp4"
    preview_file.parent.mkdir(parents=True, exist_ok=True)
    preview_file.write_bytes(b"local preview cache proxy")

    lib_id = "lib_del_001"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    asset = {
        "id": "deleted_clip",
        "name": "Deleted Clip",
        "category": "broll",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "clips/deleted_video.mp4",
        "file_path": str(source_file),
        "preview_path": str(preview_file),  # Proxy preview còn nhưng source đã mất
    }
    res = check_action_availability(asset, intent="drag", storage_provider=lambda: storage)
    assert not res.allowed
    assert res.status == "missing"
    assert res.result_code == "ERR_FILE_NOT_FOUND"


def test_drag_traversal_and_symlink_escape_denied(tmp_path):
    """Đường dẫn chứa .. hoặc symlink thoát khỏi root đều bị từ chối khi drag."""
    root = tmp_path / "safe_root"
    root.mkdir(parents=True, exist_ok=True)
    lib_id = "lib_sym_001"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    # 1. Traversal ..
    asset_trav = {
        "id": "trav_asset",
        "name": "Traversal Asset",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "../escape.mp4",
    }
    res_trav = check_action_availability(asset_trav, intent="drag", storage_provider=lambda: storage)
    assert not res_trav.allowed
    assert res_trav.result_code == "ERR_INVALID_PATH_TRAVERSAL"

    # 2. Symlink escape
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    outside_file = outside / "secret.mp4"
    outside_file.write_bytes(b"secret")

    symlink_file = root / "symlink_media.mp4"
    try:
        os.symlink(str(outside_file), str(symlink_file))
    except (OSError, NotImplementedError):
        pytest.skip("Hệ thống không hỗ trợ tạo symlink")

    asset_sym = {
        "id": "sym_asset",
        "name": "Symlink Escape Asset",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "symlink_media.mp4",
        "file_path": str(symlink_file),
    }
    res_sym = check_action_availability(asset_sym, intent="drag", storage_provider=lambda: storage)
    assert not res_sym.allowed
    assert res_sym.result_code == "ERR_SYMLINK_ESCAPE"


# ==============================================================================
# 7. Origin-Only Authority & Inference Guard (G04-04C6a-R2)
# ==============================================================================

def test_drag_origin_only_stale_file_denied(tmp_path):
    """source_kind khuyết + origin_library_id/root + file thật -> deny với ERR_ORIGIN_UNVERIFIED khi drag."""
    real_file = tmp_path / "stale_footage.mp4"
    real_file.write_bytes(b"dummy video data")

    asset = {
        "id": "stale_01",
        "name": "Stale Asset",
        "origin_library_id": "old_lib_999",
        "origin_root": str(tmp_path / "old_root"),
        "file_path": str(real_file),
    }
    res = check_action_availability(asset, intent="drag")
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_ORIGIN_UNVERIFIED"
    assert "làm mới danh mục kho" in res.reason


def test_drag_origin_only_text_style_preset_denied():
    """source_kind khuyết + origin_library_id/root + TextStylePreset -> deny với ERR_ORIGIN_UNVERIFIED khi drag."""
    preset = TextStylePreset(id="p1", name="Glowing Title")
    asset = {
        "id": "gen_origin_01",
        "name": "Glowing Title",
        "origin_library_id": "old_lib_888",
        "origin_root": "/some/old/root",
        "_raw_obj": preset,
    }
    res = check_action_availability(asset, intent="drag")
    assert not res.allowed
    assert res.status == "offline"
    assert res.result_code == "ERR_ORIGIN_UNVERIFIED"
    assert "làm mới danh mục kho" in res.reason


def test_drag_origin_only_single_origin_field_denied(tmp_path):
    """Chỉ một trường origin (origin_library_id HOẶC origin_root) khi drag vẫn bị deny với ERR_ORIGIN_UNVERIFIED."""
    real_file = tmp_path / "single_origin.mp4"
    real_file.write_bytes(b"data")

    # Chỉ có origin_library_id
    asset_lib_only = {
        "id": "single_lib_01",
        "origin_library_id": "lib_only_123",
        "file_path": str(real_file),
    }
    res1 = check_action_availability(asset_lib_only, intent="drag")
    assert not res1.allowed
    assert res1.result_code == "ERR_ORIGIN_UNVERIFIED"

    # Chỉ có origin_root
    asset_root_only = {
        "id": "single_root_01",
        "origin_root": str(tmp_path / "root_only"),
        "file_path": str(real_file),
    }
    res2 = check_action_availability(asset_root_only, intent="drag")
    assert not res2.allowed
    assert res2.result_code == "ERR_ORIGIN_UNVERIFIED"


def test_legacy_insert_with_library_path_and_real_file_preserves_behavior(tmp_path):
    """source_kind khuyết + library_path + file thật: intent='insert' giữ nguyên kết quả OK_LEGACY_MEDIA cũ."""
    real_file = tmp_path / "legacy_insert.mp4"
    real_file.write_bytes(b"legacy media")

    asset = {
        "id": "legacy_ins_01",
        "name": "Legacy Media",
        "library_path": "media/legacy_insert.mp4",
        "file_path": str(real_file),
    }
    res = check_action_availability(asset, intent="insert")
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_LEGACY_MEDIA"
    assert res.resolved_path == str(real_file.resolve())


def test_drag_linked_inferred_and_explicit_with_origin_allowed(tmp_path):
    """Nguồn linked (inferred từ external_path hoặc explicit) mang origin metadata vẫn allowed khi drag."""
    ext_file = tmp_path / "linked_ext.mp4"
    ext_file.write_bytes(b"linked content")

    # A. Inferred linked (source_kind is None, có external_path và origin)
    asset_inferred = {
        "id": "linked_inf_01",
        "name": "Inferred Linked",
        "external_path": str(ext_file),
        "origin_library_id": "storage_lib_01",
        "origin_root": str(tmp_path / "storage_root"),
    }
    res_inf = check_action_availability(asset_inferred, intent="drag")
    assert res_inf.allowed
    assert res_inf.status == "online"
    assert res_inf.result_code == "OK_LINKED_MEDIA_VERIFIED"
    assert res_inf.resolved_path == str(ext_file.resolve())

    # B. Explicit linked
    asset_explicit = {
        "id": "linked_exp_01",
        "name": "Explicit Linked",
        "source_kind": "linked",
        "external_path": str(ext_file),
        "origin_library_id": "storage_lib_01",
        "origin_root": str(tmp_path / "storage_root"),
    }
    res_exp = check_action_availability(asset_explicit, intent="drag")
    assert res_exp.allowed
    assert res_exp.status == "online"
    assert res_exp.result_code == "OK_LINKED_MEDIA_VERIFIED"
    assert res_exp.resolved_path == str(ext_file.resolve())


def test_drag_plain_legacy_without_origin_allowed(tmp_path):
    """Plain legacy không có bất kỳ trường origin nào vẫn allowed khi drag nếu file tồn tại."""
    real_file = tmp_path / "plain_legacy.mp4"
    real_file.write_bytes(b"plain content")

    asset = {
        "id": "plain_01",
        "name": "Plain Legacy Media",
        "file_path": str(real_file),
    }
    res = check_action_availability(asset, intent="drag")
    assert res.allowed
    assert res.status == "online"
    assert res.result_code == "OK_LEGACY_MEDIA"
    assert res.resolved_path == str(real_file.resolve())


def test_drag_builtin_or_generated_with_unauthorized_origin_conflicts(tmp_path):
    """source_kind builtin/generated mang origin_library_id hoặc origin_root bị từ chối ERR_CONFLICTING_SOURCE_METADATA."""
    real_file = tmp_path / "mock_file.mp4"
    real_file.write_bytes(b"content")

    # Builtin mang origin_library_id
    asset_builtin = {
        "id": "b1",
        "source_kind": "builtin",
        "origin_library_id": "some_lib",
        "file_path": str(real_file),
    }
    res_b = check_action_availability(asset_builtin, intent="drag")
    assert not res_b.allowed
    assert res_b.result_code == "ERR_CONFLICTING_SOURCE_METADATA"

    # Generated mang origin_root
    asset_gen = {
        "id": "g1",
        "source_kind": "generated",
        "origin_root": str(tmp_path / "root"),
        "file_path": str(real_file),
    }
    res_g = check_action_availability(asset_gen, intent="drag")
    assert not res_g.allowed
    assert res_g.result_code == "ERR_CONFLICTING_SOURCE_METADATA"


def test_action_guard_input_immutability(tmp_path):
    """check_action_availability không thay đổi (mutate) từ điển asset đầu vào."""
    real_file = tmp_path / "immutable.mp4"
    real_file.write_bytes(b"immutable test")

    asset = {
        "id": "imm_01",
        "name": "Immutable Asset",
        "origin_library_id": "lib_test",
        "origin_root": str(tmp_path),
        "file_path": str(real_file),
        "category": "broll",
    }
    snapshot = dict(asset)

    _ = check_action_availability(asset, intent="drag")
    assert asset == snapshot

    _ = check_action_availability(asset, intent="insert")
    assert asset == snapshot

    _ = check_action_availability(asset, intent="inspect")
    assert asset == snapshot

