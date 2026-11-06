import os
import wave
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.services.autocut_service import InstantAutoCutService, AutoCutResult
from src.core.autocut import AudioCutConfig, CutSegment


@pytest.fixture
def sample_wav(tmp_path):
    wav_path = tmp_path / "test_audio.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        # 1s sound (amp ~16384, ~ -6dB), 1s silence, 1s sound
        sound = b"\x00\x40" * 16000
        silence = b"\x00\x00" * 16000
        wf.writeframes(sound + silence + sound)
    return str(wav_path)


@pytest.fixture
def continuous_sound_wav(tmp_path):
    wav_path = tmp_path / "continuous_sound.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        sound = b"\x00\x40" * (16000 * 3)
        wf.writeframes(sound)
    return str(wav_path)


def test_autocut_service_no_connection():
    mock_ra = MagicMock()
    mock_ra.is_connected.return_value = False
    mock_ra.connect.return_value = False
    mock_ra.last_connect_error = "DaVinci not open"

    service = InstantAutoCutService(resolve_automation=mock_ra)
    res = service.run_instant_autocut()

    assert res.ok is False
    assert "kết nối" in res.message.lower()


def test_autocut_service_no_timeline():
    mock_ra = MagicMock()
    mock_ra.is_connected.return_value = True
    mock_ra.get_active_timeline.return_value = None

    service = InstantAutoCutService(resolve_automation=mock_ra)
    res = service.run_instant_autocut()

    assert res.ok is False
    assert "timeline" in res.message.lower()


def test_autocut_service_no_video_clip():
    mock_ra = MagicMock()
    mock_ra.is_connected.return_value = True
    mock_timeline = MagicMock()
    mock_timeline.GetName.return_value = "Timeline 1"
    mock_ra.get_active_timeline.return_value = mock_timeline
    mock_ra.auto_detect_video_paths.return_value = []

    service = InstantAutoCutService(resolve_automation=mock_ra)
    res = service.run_instant_autocut()

    assert res.ok is False
    assert "video" in res.message.lower()


def test_autocut_service_successful_cut(sample_wav, tmp_path):
    mock_ra = MagicMock()
    mock_ra.is_connected.return_value = True
    mock_project = MagicMock()
    mock_media_pool = MagicMock()
    mock_timeline = MagicMock()
    mock_new_timeline = MagicMock()

    mock_timeline.GetName.return_value = "Vlog_Ep1"
    mock_new_timeline.GetName.return_value = "[AutoCut] Vlog_Ep1"
    mock_timeline.GetUniqueId.return_value = "original"
    mock_new_timeline.GetUniqueId.return_value = "created"
    mock_project.GetTimelineCount.return_value = 2
    mock_project.GetTimelineByIndex.side_effect = lambda index: [mock_timeline, mock_new_timeline][index - 1]

    mock_ra.current_project = mock_project
    mock_project.GetMediaPool.return_value = mock_media_pool
    mock_ra.get_active_timeline.return_value = mock_timeline
    mock_ra.auto_detect_video_paths.return_value = [sample_wav]

    # Mock ImportTimelineFromFile returning mock_new_timeline
    mock_media_pool.ImportTimelineFromFile.return_value = mock_new_timeline

    service = InstantAutoCutService(
        resolve_automation=mock_ra,
        config=AudioCutConfig(min_silent_duration=0.5, silence_threshold_db=-30.0)
    )

    # Mock extract_audio returning sample_wav directly
    service.extract_audio = MagicMock(return_value=sample_wav)

    res = service.run_instant_autocut()

    assert res.ok is True
    assert res.original_timeline_name == "Vlog_Ep1"
    assert res.new_timeline_name == "[AutoCut] Vlog_Ep1"
    assert res.cuts_count >= 1
    assert res.duration_saved_sec > 0

    # Verify project.SetCurrentTimeline was called with new timeline
    mock_project.SetCurrentTimeline.assert_called_with(mock_new_timeline)


def test_autocut_service_continuous_speech(continuous_sound_wav):
    mock_ra = MagicMock()
    mock_ra.is_connected.return_value = True
    mock_project = MagicMock()
    mock_timeline = MagicMock()

    mock_timeline.GetName.return_value = "Speech"
    mock_ra.current_project = mock_project
    mock_ra.get_active_timeline.return_value = mock_timeline
    mock_ra.auto_detect_video_paths.return_value = [continuous_sound_wav]

    service = InstantAutoCutService(
        resolve_automation=mock_ra,
        config=AudioCutConfig(min_silent_duration=0.5, silence_threshold_db=-30.0)
    )
    service.extract_audio = MagicMock(return_value=continuous_sound_wav)

    res = service.run_instant_autocut()
    assert res.ok is True
    assert res.cuts_count == 0
    assert "lưu loát" in res.message.lower()
