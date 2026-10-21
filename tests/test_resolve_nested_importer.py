"""
Unit tests for ChunDVC (ResolveFlow) NEW-NESTED-NO-RIPPLE-V2 Engine and Preset Capture.
"""
from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch
import pytest

from src.core.preset_capture import (
    auto_relink_setting_file,
    extract_tools_without_mediaout,
    format_fusion_code,
    get_davinci_titles_dir,
    safe_preset_name,
)
from src.core.resolve_api import (
    _existing_snapshot_unchanged,
    _nominal_fps,
    _safe_name,
    _timecode_to_frames,
    _track_is_free,
    current_record_frame,
    find_or_create_free_video_track,
    import_edit_preset_v2,
    snapshot_video_timeline,
)


def test_safe_name_sanitization():
    assert _safe_name("Tiêu Đề / Test * 123!") == "Tiêu_Đề_Test_123"
    assert len(_safe_name("A" * 100, limit=20)) == 20
    assert _safe_name("") == "Preset"


def test_timecode_to_frames():
    # 24 fps: 1 second = 24 frames
    assert _timecode_to_frames("00:00:01:00", 24) == 24
    # 30 fps: 1 minute = 1800 frames
    assert _timecode_to_frames("00:01:00:00", 30) == 1800
    # Drop frame notation
    drop_frames = _timecode_to_frames("00:01:00;02", 30)
    assert drop_frames > 0


def test_track_is_free_and_half_open_interval():
    mock_timeline = MagicMock()
    clip1 = MagicMock()
    clip1.GetStart.return_value = 100
    clip1.GetEnd.return_value = 200

    mock_timeline.GetItemListInTrack.return_value = [clip1]

    # Overlaps [150, 250) -> False
    assert _track_is_free(mock_timeline, 1, 150, 100) is False
    # Overlaps [50, 150) -> False
    assert _track_is_free(mock_timeline, 1, 50, 100) is False
    # Half-open exact boundary [50, 100) touches 100 but does not overlap -> True
    assert _track_is_free(mock_timeline, 1, 50, 50) is True
    # Half-open exact boundary [200, 300) touches 200 but does not overlap -> True
    assert _track_is_free(mock_timeline, 1, 200, 100) is True


def test_find_or_create_free_video_track():
    mock_timeline = MagicMock()
    mock_timeline.GetTrackCount.return_value = 2
    mock_timeline.GetIsTrackLocked.return_value = False
    mock_timeline.GetIsTrackEnabled.return_value = True

    # Track V2 is occupied [100, 200)
    clip_v2 = MagicMock()
    clip_v2.GetStart.return_value = 100
    clip_v2.GetEnd.return_value = 200

    def get_items(track_type, idx):
        if idx == 2:
            return [clip_v2]
        return []

    mock_timeline.GetItemListInTrack.side_effect = get_items

    # Preferred track V2 is occupied for [120, 180), so it should check other tracks or add track
    track_idx, was_added = find_or_create_free_video_track(
        mock_timeline, start_frame=120, duration=60, preferred_track=2
    )
    # Track 1 is checked as fallback and is free
    assert track_idx == 1
    assert was_added is False

    # If all existing tracks are occupied, it adds a new track
    def all_occupied(track_type, idx):
        c = MagicMock()
        c.GetStart.return_value = 0
        c.GetEnd.return_value = 500
        return [c]

    mock_timeline.GetItemListInTrack.side_effect = all_occupied
    mock_timeline.GetTrackCount.return_value = 3  # After AddTrack
    track_idx, was_added = find_or_create_free_video_track(
        mock_timeline, start_frame=100, duration=100, preferred_track=2
    )
    assert was_added is True
    assert track_idx == 3
    mock_timeline.AddTrack.assert_called_with("video")


def test_snapshot_and_rollback_detection():
    mock_timeline = MagicMock()
    mock_timeline.GetTrackCount.return_value = 1

    clip1 = MagicMock()
    clip1.GetUniqueId.return_value = "clip_abc"
    clip1.GetStart.return_value = 100
    clip1.GetEnd.return_value = 200
    mock_timeline.GetItemListInTrack.return_value = [clip1]

    snapshot = snapshot_video_timeline(mock_timeline)
    assert snapshot["clip_abc"] == (100, 200)
    assert _existing_snapshot_unchanged(mock_timeline, snapshot) is True

    # Simulate ripple shift: clip moved from 100 to 101
    clip1.GetStart.return_value = 101
    assert _existing_snapshot_unchanged(mock_timeline, snapshot) is False


def test_import_edit_preset_v2_zero_ripple():
    mock_resolve = MagicMock()
    mock_pm = MagicMock()
    mock_project = MagicMock()
    mock_main_tl = MagicMock()
    mock_source_tl = MagicMock()
    mock_mp = MagicMock()
    mock_root = MagicMock()
    mock_source_media = MagicMock()
    mock_source_item = MagicMock()
    mock_placed_item = MagicMock()

    mock_resolve.GetProjectManager.return_value = mock_pm
    mock_pm.GetCurrentProject.return_value = mock_project
    mock_project.GetCurrentTimeline.return_value = mock_main_tl
    mock_project.GetMediaPool.return_value = mock_mp
    mock_project.GetSetting.return_value = "24.0"

    mock_mp.GetRootFolder.return_value = mock_root
    mock_root.GetSubFolderList.return_value = []
    mock_mp.CreateEmptyTimeline.return_value = mock_source_tl
    mock_source_tl.GetMediaPoolItem.return_value = mock_source_media
    mock_source_tl.InsertFusionTitleIntoTimeline.return_value = mock_source_item
    mock_source_item.GetDuration.return_value = 72  # 3 seconds at 24fps

    # Main timeline state
    mock_main_tl.GetCurrentTimecode.return_value = "00:00:02:00"  # frame 48
    mock_main_tl.GetStartTimecode.return_value = "00:00:00:00"
    mock_main_tl.GetStartFrame.return_value = 0
    mock_main_tl.GetTrackCount.return_value = 2
    mock_main_tl.GetIsTrackLocked.return_value = False
    mock_main_tl.GetIsTrackEnabled.return_value = True
    mock_main_tl.GetItemListInTrack.return_value = []

    mock_mp.AppendToTimeline.return_value = [mock_placed_item]
    mock_placed_item.GetName.return_value = "Alex Hormozi Pop"

    result = import_edit_preset_v2(
        title_name="Alex Hormozi Pop",
        display_name="Hormozi Test",
        preferred_track=2,
        resolve_app=mock_resolve,
    )

    assert result["ok"] is True
    assert result["track"] == 2
    assert result["start"] == 48
    assert result["end"] == 48 + 72
    # Verify title was inserted into source_tl (never main_tl)
    mock_source_tl.InsertFusionTitleIntoTimeline.assert_called_once_with("Alex Hormozi Pop")
    mock_main_tl.InsertFusionTitleIntoTimeline.assert_not_called()
    # Verify placed via AppendToTimeline
    mock_mp.AppendToTimeline.assert_called_once()


def test_import_edit_preset_v2_rollback_on_ripple():
    mock_resolve = MagicMock()
    mock_pm = MagicMock()
    mock_project = MagicMock()
    mock_main_tl = MagicMock()
    mock_source_tl = MagicMock()
    mock_mp = MagicMock()
    mock_root = MagicMock()
    mock_source_media = MagicMock()
    mock_source_item = MagicMock()
    mock_placed_item = MagicMock()

    mock_resolve.GetProjectManager.return_value = mock_pm
    mock_pm.GetCurrentProject.return_value = mock_project
    mock_project.GetCurrentTimeline.return_value = mock_main_tl
    mock_project.GetMediaPool.return_value = mock_mp
    mock_project.GetSetting.return_value = "24.0"

    mock_mp.GetRootFolder.return_value = mock_root
    mock_root.GetSubFolderList.return_value = []
    mock_mp.CreateEmptyTimeline.return_value = mock_source_tl
    mock_source_tl.GetMediaPoolItem.return_value = mock_source_media
    mock_source_tl.InsertFusionTitleIntoTimeline.return_value = mock_source_item
    mock_source_item.GetDuration.return_value = 48

    # Existing clip on main timeline
    existing_clip = MagicMock()
    existing_clip.GetUniqueId.return_value = "existing_1"
    existing_clip.GetStart.return_value = 0
    existing_clip.GetEnd.return_value = 100
    mock_main_tl.GetItemListInTrack.return_value = [existing_clip]

    mock_main_tl.GetCurrentTimecode.return_value = "00:00:05:00"
    mock_main_tl.GetStartTimecode.return_value = "00:00:00:00"
    mock_main_tl.GetStartFrame.return_value = 0
    mock_main_tl.GetTrackCount.return_value = 2
    mock_main_tl.GetIsTrackLocked.return_value = False
    mock_main_tl.GetIsTrackEnabled.return_value = True

    mock_mp.AppendToTimeline.return_value = [mock_placed_item]

    # After AppendToTimeline, simulate existing clip shifted position
    def simulate_corruption(*args, **kwargs):
        existing_clip.GetStart.return_value = 10  # Corrupted!
        return [mock_placed_item]

    mock_mp.AppendToTimeline.side_effect = simulate_corruption

    result = import_edit_preset_v2(
        title_name="Some_Preset",
        preferred_track=2,
        resolve_app=mock_resolve,
    )

    assert result["ok"] is False
    assert result["error"] == "ripple_detected_rollback"
    # Verify rollback called DeleteClips
    mock_main_tl.DeleteClips.assert_called_once_with([mock_placed_item], False)


def test_extract_tools_without_mediaout():
    comp_sample = [
        "Composition {",
        "\tTools = ordered() {",
        '\t\tTextPlus1 = TextPlus {',
        '\t\t\tInputs = { StyledText = Input { Value = "ChunDVC", }, },',
        '\t\t},',
        '\t\tMediaOut1 = MediaOut {',
        '\t\t\tInputs = { Input = Input { SourceOp = "TextPlus1", }, },',
        '\t\t},',
        '\t},',
        "}",
    ]
    extracted = extract_tools_without_mediaout(comp_sample)
    assert extracted is not None
    assert "TextPlus1 = TextPlus" in extracted
    assert "MediaOut1 = MediaOut" not in extracted


def test_format_fusion_code():
    raw_tools = '\t\tText1 = TextPlus { Inputs = { Text = "Test" } }'
    code = format_fusion_code(raw_tools, "MyPreset")
    assert 'MacroOperator {' in code
    assert 'Name = "MyPreset"' in code
    assert 'PassThrough = true' in code
    assert 'Tools = ordered() {' in code


def test_auto_relink_setting_file():
    raw = 'Loader1 = Loader { Inputs = { Filename = Input { Value = "C:\\\\Users\\\\Admin\\\\texture.png", }, } }'
    media_idx = {"texture.png": "/media/assets/texture.png"}
    relinked = auto_relink_setting_file(raw, media_idx)
    assert "/media/assets/texture.png" in relinked
    assert "C:\\" not in relinked


def test_title_insert_worker_cli():
    # Verify CLI help output runs cleanly without exceptions
    cmd = [sys.executable, "-m", "src.core.title_insert_worker", "--help"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--title" in proc.stdout


def test_capture_playhead_text_preset_flow(tmp_path):
    from src.core.preset_capture import capture_playhead_text_preset

    mock_resolve = MagicMock()
    mock_pm = MagicMock()
    mock_project = MagicMock()
    mock_timeline = MagicMock()
    mock_item = MagicMock()
    mock_comp = MagicMock()

    mock_resolve.GetProjectManager.return_value = mock_pm
    mock_pm.GetCurrentProject.return_value = mock_project
    mock_project.GetCurrentTimeline.return_value = mock_timeline

    mock_timeline.GetCurrentTimecode.return_value = "00:00:02:00"
    mock_timeline.GetSetting.return_value = "24.0"
    mock_timeline.GetTrackCount.return_value = 1

    mock_item.GetStart.return_value = 0
    mock_item.GetEnd.return_value = 100
    mock_item.GetFusionCompNameList.return_value = ["Composition1"]
    mock_item.GetFusionCompByName.return_value = mock_comp
    mock_item.GetName.return_value = "Text1"
    mock_timeline.GetItemListInTrack.return_value = [mock_item]

    # Simulate exporting comp
    def mock_export(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write('Composition {\n\tTools = ordered() {\n\t\tText1 = TextPlus {\n\t\t\tInputs = {\n\t\t\t\tStyledText = Input { Value = "Sample Text", },\n\t\t\t},\n\t\t},\n\t\tMediaOut1 = MediaOut {},\n\t},\n}')
        return True

    mock_comp.Export.side_effect = mock_export

    dest_folder = str(tmp_path / "Titles")
    res = capture_playhead_text_preset(
        preset_name="MyTestStyle",
        resolve_app=mock_resolve,
        dest_dir=dest_folder
    )

    assert res["ok"] is True
    assert os.path.exists(res["setting_path"])
    with open(res["setting_path"], "r", encoding="utf-8") as fh:
        content = fh.read()
    assert 'Name = "MyTestStyle"' in content
    assert 'Text1 = TextPlus' in content
    assert 'MediaOut1' not in content

