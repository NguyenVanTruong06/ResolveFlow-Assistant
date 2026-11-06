"""
tests/core/test_resolve_media_frame_bounds.py
=============================================================================
TDD Unit test cho task G04-04C5-I:
Kiểm tra tính đúng đắn của append_spec (đặc biệt là endFrame) khi chèn media:
1. Nguồn 168 frames / no-limit (duration_sec=None hoặc 0) -> endFrame = 168 (startFrame=0).
2. Explicit 3.0s @ 24fps -> dur_frames = 72 -> endFrame = 72 (startFrame=0).
3. Clamp 10.0s (lớn hơn source 168 frames @ 24fps) -> endFrame = 168.
4. Very short positive (duration_sec=0.001) -> tối thiểu 1 frame -> endFrame = 1.
5. Still image (.png) -> giữ nguyên không có startFrame/endFrame.
6. recordFrame (86400), target_track (V3), mediaType (1) được bảo toàn.
=============================================================================
"""
import os
from unittest.mock import MagicMock, patch
import pytest

from src.core.resolve_api import ResolveAutomation


def _create_mock_automation(tmp_path, filename="video.mp4", total_frames=168, fps=24.0, record_frame=86400):
    auto = ResolveAutomation()
    auto.connect = MagicMock(return_value=True)
    auto.is_connected = MagicMock(return_value=True)
    auto.import_media_to_media_pool = MagicMock(return_value=True)

    mock_proj = MagicMock()
    mock_mp = MagicMock()
    mock_tl = MagicMock()
    mock_proj.GetMediaPool.return_value = mock_mp
    mock_proj.GetCurrentTimeline.return_value = mock_tl
    mock_tl.GetTrackCount.return_value = 3
    mock_tl.GetSetting.return_value = str(fps)

    auto.resolve = MagicMock()
    auto.current_project = mock_proj
    auto.current_timeline = mock_tl
    auto.get_active_timeline = MagicMock(return_value=mock_tl)

    test_file = tmp_path / filename
    test_file.write_bytes(b"FAKE_MEDIA_CONTENT")
    norm_path = os.path.normcase(os.path.abspath(str(test_file)))

    fake_clip = MagicMock()
    fake_clip.GetName.return_value = filename
    fake_clip.GetClipProperty.side_effect = lambda prop: norm_path if prop == "File Path" else str(total_frames)
    auto.get_media_pool_clips = MagicMock(return_value=[fake_clip])

    mock_mp.AppendToTimeline.return_value = [MagicMock()]
    return auto, mock_mp, str(test_file), record_frame


def test_insert_media_no_limit_sends_exact_end_frame(tmp_path):
    """Nguồn 168 frames không giới hạn (duration_sec=None) -> endFrame phải bằng 168."""
    auto, mock_mp, media_path, rec_frame = _create_mock_automation(
        tmp_path, filename="video_7s.mp4", total_frames=168, fps=24.0, record_frame=86400
    )

    with patch("src.core.resolve_api.current_record_frame", return_value=(rec_frame, "01:00:00:00")):
        ok = auto.insert_media_at_playhead(media_path, target_track=3, duration_sec=None)

    assert ok is True
    assert mock_mp.AppendToTimeline.called
    spec = mock_mp.AppendToTimeline.call_args[0][0][0]

    assert spec["trackIndex"] == 3
    assert spec["recordFrame"] == 86400
    assert spec["mediaType"] == 1
    assert spec["startFrame"] == 0
    assert spec["endFrame"] == 168, f"endFrame phải là 168 để bảo toàn đủ 168 frames trên timeline, thực tế={spec.get('endFrame')}"


def test_insert_media_explicit_duration_sends_exact_end_frame(tmp_path):
    """Explicit 3.0s @ 24fps -> dur_frames = 72 -> endFrame phải là 72."""
    auto, mock_mp, media_path, rec_frame = _create_mock_automation(
        tmp_path, filename="video_7s.mp4", total_frames=168, fps=24.0, record_frame=86400
    )

    with patch("src.core.resolve_api.current_record_frame", return_value=(rec_frame, "01:00:00:00")):
        ok = auto.insert_media_at_playhead(media_path, target_track=3, duration_sec=3.0)

    assert ok is True
    spec = mock_mp.AppendToTimeline.call_args[0][0][0]
    assert spec["startFrame"] == 0
    assert spec["endFrame"] == 72, f"endFrame phải là 72 cho 3.0s @ 24fps, thực tế={spec.get('endFrame')}"


def test_insert_media_clamp_longer_than_source(tmp_path):
    """Yêu cầu 10.0s (240 frames) trên nguồn 168 frames -> clamp về 168 -> endFrame = 168."""
    auto, mock_mp, media_path, rec_frame = _create_mock_automation(
        tmp_path, filename="video_7s.mp4", total_frames=168, fps=24.0, record_frame=86400
    )

    with patch("src.core.resolve_api.current_record_frame", return_value=(rec_frame, "01:00:00:00")):
        ok = auto.insert_media_at_playhead(media_path, target_track=2, duration_sec=10.0)

    assert ok is True
    spec = mock_mp.AppendToTimeline.call_args[0][0][0]
    assert spec["trackIndex"] == 2
    assert spec["startFrame"] == 0
    assert spec["endFrame"] == 168, f"endFrame bị clamp về 168, thực tế={spec.get('endFrame')}"


def test_insert_media_very_short_positive_minimum_one_frame(tmp_path):
    """Thời lượng rất ngắn (0.001s) -> tối thiểu 1 frame -> endFrame = 1."""
    auto, mock_mp, media_path, rec_frame = _create_mock_automation(
        tmp_path, filename="video_7s.mp4", total_frames=168, fps=24.0, record_frame=86400
    )

    with patch("src.core.resolve_api.current_record_frame", return_value=(rec_frame, "01:00:00:00")):
        ok = auto.insert_media_at_playhead(media_path, target_track=3, duration_sec=0.001)

    assert ok is True
    spec = mock_mp.AppendToTimeline.call_args[0][0][0]
    assert spec["startFrame"] == 0
    assert spec["endFrame"] == 1, f"endFrame tối thiểu phải là 1, thực tế={spec.get('endFrame')}"


def test_insert_still_image_preserves_no_source_bounds(tmp_path):
    """Ảnh tĩnh (.png) -> không có startFrame / endFrame trong append_spec."""
    auto, mock_mp, media_path, rec_frame = _create_mock_automation(
        tmp_path, filename="sticker.png", total_frames=1, fps=24.0, record_frame=86400
    )

    with patch("src.core.resolve_api.current_record_frame", return_value=(rec_frame, "01:00:00:00")):
        ok = auto.insert_media_at_playhead(media_path, target_track=2, duration_sec=5.0)

    assert ok is True
    spec = mock_mp.AppendToTimeline.call_args[0][0][0]
    assert spec["trackIndex"] == 2
    assert spec["recordFrame"] == 86400
    assert spec["mediaType"] == 1
    assert "startFrame" not in spec, "Ảnh tĩnh không được chứa startFrame"
    assert "endFrame" not in spec, "Ảnh tĩnh không được chứa endFrame"
