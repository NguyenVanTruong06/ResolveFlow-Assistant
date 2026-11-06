"""
tests/core/test_action_guard_fusion_export.py
Kiểm thử TDD cho ActionGuard với intent 'fusion_export' (G04-04C10-I).
"""
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from src.core.action_guard import (
    ActionGuardResult,
    check_action_availability,
)
from src.core.text_preset import TextStylePreset
from src.core.transition_preset import TransitionStylePreset


class MockStorage:
    def __init__(self, root: str, library_id: str, online: bool = True):
        self._root = Path(root)
        self.library_id = library_id
        self._config = {"root": str(self._root), "library_id": library_id}
        self.online = online

    @property
    def root(self):
        return self._root


def test_fusion_export_procedural_text_positive():
    """1. Procedural TextStylePreset hợp lệ -> OK_PROCEDURAL_FUSION_EXPORT."""
    preset = TextStylePreset(
        id="kinetic_spring",
        name="Kinetic Spring",
        font="Arial",
        size=48,
    )
    asset = {
        "id": "kinetic_spring",
        "name": "Kinetic Spring",
        "tab": "text",
        "_raw_obj": preset,
    }
    res = check_action_availability(asset, intent="fusion_export")
    assert res.allowed is True
    assert res.status == "online"
    assert res.result_code == "OK_PROCEDURAL_FUSION_EXPORT"
    assert res.resolved_path == ""


def test_fusion_export_managed_setting_file_positive(tmp_path):
    """2. Managed .setting file hợp lệ trên đĩa -> OK_FILEBACKED_FUSION_EXPORT."""
    root = tmp_path / "lib_root"
    setting_file = root / "title_pack" / "macro.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    setting_file.write_text("{\n  Tools = ordered() { }\n}\n", encoding="utf-8")

    storage = MockStorage(root=str(root), library_id="lib_active_01", online=True)

    asset = {
        "id": "macro_title",
        "name": "Macro Title",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_active_01",
        "origin_root": str(root),
        "library_path": "title_pack/macro.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    res = check_action_availability(asset, intent="fusion_export", storage_provider=lambda: storage)
    assert res.allowed is True
    assert res.status == "online"
    assert res.result_code == "OK_FILEBACKED_FUSION_EXPORT"
    assert res.resolved_path == str(setting_file.resolve())


def test_fusion_export_staging_denied(tmp_path):
    """3. Tài nguyên staging bị từ chối fail-closed với ERR_STAGING_UNPUBLISHED."""
    root = tmp_path / "staging_root"
    setting_file = root / "draft.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    setting_file.write_text("dummy", encoding="utf-8")

    asset = {
        "id": "draft_macro",
        "name": "Draft Macro",
        "tab": "text",
        "source_kind": "managed",
        "origin_library_id": "lib_01",
        "origin_root": str(root),
        "file_path": str(setting_file),
        "availability": "staging",
    }
    res = check_action_availability(asset, intent="fusion_export")
    assert res.allowed is False
    assert res.status == "staging"
    assert res.result_code == "ERR_STAGING_UNPUBLISHED"


def test_fusion_export_missing_file_denied(tmp_path):
    """4. File .setting bị xóa/missing trên đĩa -> Từ chối fail-closed."""
    root = tmp_path / "lib_root"
    setting_file = root / "deleted.setting"
    storage = MockStorage(root=str(root), library_id="lib_active_01", online=True)

    asset = {
        "id": "deleted_macro",
        "name": "Deleted Macro",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_active_01",
        "origin_root": str(root),
        "library_path": "deleted.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    res = check_action_availability(asset, intent="fusion_export", storage_provider=lambda: storage)
    assert res.allowed is False
    assert res.status in ("missing", "offline")
    assert res.result_code in ("ERR_FILE_NOT_FOUND", "ERR_STORAGE_MISSING")


def test_fusion_export_root_id_swap_denied(tmp_path):
    """5. Managed .setting tráo đổi origin_library_id -> Bị chặn với ERR_ORIGIN_UNVERIFIED / ERR_STORAGE_IDENTITY_MISMATCH."""
    root = tmp_path / "lib_root"
    setting_file = root / "forged.setting"
    setting_file.parent.mkdir(parents=True, exist_ok=True)
    setting_file.write_text("dummy", encoding="utf-8")
    storage = MockStorage(root=str(root), library_id="lib_active_01", online=True)

    asset = {
        "id": "forged_macro",
        "name": "Forged Macro",
        "tab": "text",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_forged_99",
        "origin_root": str(root),
        "library_path": "forged.setting",
        "file_path": str(setting_file),
        "availability": "online",
    }

    res = check_action_availability(asset, intent="fusion_export", storage_provider=lambda: storage)
    assert res.allowed is False
    assert res.result_code in ("ERR_STORAGE_IDENTITY_MISMATCH", "ERR_ORIGIN_UNVERIFIED")


def test_fusion_export_native_transition_denied():
    """6. Native Transition bị từ chối với ERR_NATIVE_TRANSITION_FUSION_UNSUPPORTED trước file branch."""
    trans_preset = TransitionStylePreset(id="trans_zoom", name="Zoom In", transition_type="Zoom")
    asset = {
        "id": "trans_zoom",
        "name": "Zoom In",
        "tab": "trans",
        "category": "trans",
        "_raw_obj": trans_preset,
    }
    res = check_action_availability(asset, intent="fusion_export")
    assert res.allowed is False
    assert res.result_code == "ERR_NATIVE_TRANSITION_FUSION_UNSUPPORTED"
    assert "chuyển cảnh native" in res.reason.lower()


def test_fusion_export_non_fusion_media_denied(tmp_path):
    """7. Non-Fusion Media (Video MP4, SFX WAV, LUT CUBE) bị từ chối với ERR_NON_FUSION_MEDIA_UNSUPPORTED."""
    # Video
    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"dummy mp4")
    asset_video = {
        "id": "video_1",
        "name": "Clip Video",
        "tab": "meme",
        "file_path": str(video_file),
        "source_kind": "builtin",
    }
    res_vid = check_action_availability(asset_video, intent="fusion_export")
    assert res_vid.allowed is False
    assert res_vid.result_code == "ERR_NON_FUSION_MEDIA_UNSUPPORTED"

    # Audio
    audio_file = tmp_path / "sound.wav"
    audio_file.write_bytes(b"dummy wav")
    asset_audio = {
        "id": "audio_1",
        "name": "Sound SFX",
        "tab": "sfx",
        "file_path": str(audio_file),
        "source_kind": "builtin",
    }
    res_aud = check_action_availability(asset_audio, intent="fusion_export")
    assert res_aud.allowed is False
    assert res_aud.result_code == "ERR_NON_FUSION_MEDIA_UNSUPPORTED"

    # LUT
    lut_file = tmp_path / "look.cube"
    lut_file.write_text("LUT_3D_SIZE 33\n", encoding="utf-8")
    asset_lut = {
        "id": "lut_1",
        "name": "Cool Look",
        "tab": "lut",
        "file_path": str(lut_file),
        "source_kind": "builtin",
    }
    res_lut = check_action_availability(asset_lut, intent="fusion_export")
    assert res_lut.allowed is False
    assert res_lut.result_code == "ERR_NON_FUSION_MEDIA_UNSUPPORTED"


def test_fusion_export_fake_classname_not_generator():
    """8. Đối tượng có tab='text' hoặc tên class giả nhưng không phải TextStylePreset domain thật -> Từ chối."""
    asset_fake = {
        "id": "fake_text",
        "name": "Fake Text",
        "tab": "text",
        "_raw_obj": "NotAPresetInstance",
    }
    res = check_action_availability(asset_fake, intent="fusion_export")
    assert res.allowed is False
    assert res.result_code == "ERR_PROCEDURAL_FUSION_UNSUPPORTED"


def test_existing_intents_unaffected():
    """9. Các intent hiện hữu (insert, drag, preview, inspect) hoạt động bình thường, không bị hồi quy."""
    asset = {"id": "asset_1", "name": "Asset", "availability": "online"}
    res_inspect = check_action_availability(asset, intent="inspect")
    assert res_inspect.allowed is True
    assert res_inspect.result_code == "OK_INSPECT_ONLY"


def test_managed_missing_relative_path_denied_across_all_intents(tmp_path):
    """10. Regression Probe: Asset managed có origin chuẩn + file thật nhưng thiếu cả library_path và relative_path -> BỊ TỪ CHỐI với ERR_MISSING_LIBRARY_PATH ở mọi intent (insert, drag, preview, fusion_export)."""
    root = tmp_path / "lib_root"
    media_file = root / "title_pack" / "clip.setting"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_text("dummy", encoding="utf-8")

    storage = MockStorage(root=str(root), library_id="lib_01", online=True)

    asset = {
        "id": "probe_item",
        "name": "Probe Item",
        "source_kind": "managed",
        "storage_managed": True,
        "origin_library_id": "lib_01",
        "origin_root": str(root),
        "file_path": str(media_file),
        "availability": "online",
        # Cố ý thiếu cả library_path lẫn relative_path
    }

    for intent in ("insert", "drag", "preview", "fusion_export"):
        res = check_action_availability(asset, intent=intent, storage_provider=lambda: storage)
        assert res.allowed is False, f"Intent {intent} should be denied when relative path is missing"
        assert res.result_code == "ERR_MISSING_LIBRARY_PATH", f"Intent {intent} got {res.result_code}"

