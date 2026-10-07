import os
import xml.etree.ElementTree as ET
import pytest
from src.core.fcpxml_generator import FCPXMLGenerator
from src.core.story_copilot import (
    StoryCopilot,
    CopilotDirectorPlan,
    CopilotSegmentPlan,
)


def test_generate_fcp7_xml_with_bgm_and_beat_markers(tmp_path):
    v_path = tmp_path / "main_clip.mp4"
    bgm_path = tmp_path / "song1.mp3"
    v_path.write_bytes(b"dummy video")
    bgm_path.write_bytes(b"dummy audio")

    events = [
        {
            "video_path": str(v_path),
            "src_in": 0.0,
            "src_out": 10.0,
            "rec_in": 0.0,
            "rec_out": 10.0,
            "fps": 30.0,
        }
    ]

    clip_db = {
        str(v_path): {
            "name": "main_clip.mp4",
            "path": str(v_path),
            "duration": 10.0,
            "dur_frames": 300,
            "has_audio": True,
            "channels": 2,
        },
        str(bgm_path): {
            "name": "song1.mp3",
            "path": str(bgm_path),
            "duration": 5.0,
            "dur_frames": 150,
            "has_audio": True,
            "channels": 2,
        },
    }

    out_xml = os.path.join(tmp_path, "timeline_bgm.xml")
    FCPXMLGenerator.generate_fcp7_xml(
        events=events,
        output_xml_path=out_xml,
        timeline_name="BGM Beat Test",
        fps=30.0,
        bgm_files=[str(bgm_path)],
        music_beats=[1.5, 3.0, 6.0, 9.0],
        clip_metadata_db=clip_db,
    )

    assert os.path.exists(out_xml)
    tree = ET.parse(out_xml)
    root = tree.getroot()

    # Verify <media><audio> has at least 4 tracks (A1, A2, A3, A4, A5)
    audio_el = root.find(".//media/audio")
    assert audio_el is not None
    audio_tracks = audio_el.findall("track")
    assert len(audio_tracks) >= 4

    # Verify BGM clipitems exist and cover the timeline (song of 5s loops to cover 10s)
    bgm_items = [ci for ci in root.findall(".//clipitem") if "song1.mp3" in (ci.findtext("name") or "")]
    assert len(bgm_items) >= 4  # 2 chunks * 2 stereo tracks (L & R)

    # Verify markers contain 🎵 Beat Drop with color Cyan
    markers = root.findall(".//marker")
    beat_markers = [m for m in markers if "🎵 Beat Drop" in (m.findtext("name") or "")]
    assert len(beat_markers) == 4
    for bm in beat_markers:
        color_node = bm.find("color")
        assert color_node is not None
        assert color_node.text == "Cyan"


def test_generate_fcp7_xml_multi_song_chain(tmp_path):
    v_path = tmp_path / "main_20s.mp4"
    bgm1 = tmp_path / "song1.mp3"
    bgm2 = tmp_path / "song2.mp3"
    v_path.write_bytes(b"dummy video")
    bgm1.write_bytes(b"dummy audio 1")
    bgm2.write_bytes(b"dummy audio 2")

    events = [
        {
            "video_path": str(v_path),
            "src_in": 0.0,
            "src_out": 20.0,
            "rec_in": 0.0,
            "rec_out": 20.0,
            "fps": 30.0,
        }
    ]

    clip_db = {
        str(v_path): {
            "name": "main_20s.mp4",
            "path": str(v_path),
            "duration": 20.0,
            "dur_frames": 600,
            "has_audio": True,
            "channels": 2,
        },
        str(bgm1): {
            "name": "song1.mp3",
            "path": str(bgm1),
            "duration": 10.0,
            "dur_frames": 300,
            "has_audio": True,
            "channels": 2,
        },
        str(bgm2): {
            "name": "song2.mp3",
            "path": str(bgm2),
            "duration": 15.0,
            "dur_frames": 450,
            "has_audio": True,
            "channels": 2,
        },
    }

    out_xml = os.path.join(tmp_path, "timeline_multi_bgm.xml")
    FCPXMLGenerator.generate_fcp7_xml(
        events=events,
        output_xml_path=out_xml,
        timeline_name="Multi BGM Chain Test",
        fps=30.0,
        bgm_files=[str(bgm1), str(bgm2)],
        clip_metadata_db=clip_db,
    )

    assert os.path.exists(out_xml)
    tree = ET.parse(out_xml)
    root = tree.getroot()

    audio_el = root.find(".//media/audio")
    assert audio_el is not None
    audio_tracks = audio_el.findall("track")
    assert len(audio_tracks) >= 4

    # Verify both audio files appear in the BGM tracks
    clip_names = [ci.findtext("name") for ci in root.findall(".//clipitem")]
    assert any("song1.mp3" in (name or "") for name in clip_names)
    assert any("song2.mp3" in (name or "") for name in clip_names)


def test_story_copilot_beat_markers(tmp_path):
    v1 = tmp_path / "clip1.mp4"
    v1.write_bytes(b"clip content")

    plan = CopilotDirectorPlan(
        strategy_summary="Beat test",
        timeline_segments=[
            CopilotSegmentPlan(
                clip_index=1,
                clip_name="clip1.mp4",
                chapter_name="Intro",
                start_sec=0.0,
                end_sec=10.0,
                role="intro",
            )
        ],
    )

    path_map = {1: str(v1)}
    events, subs, markers = StoryCopilot.convert_plan_to_resolve_timeline(
        plan=plan,
        video_paths_by_index=path_map,
        music_beats=[2.0, 4.0],
    )

    beat_markers = [m for m in markers if "Beat Drop" in m.get("name", "")]
    assert len(beat_markers) == 2
    assert beat_markers[0]["time"] == 2.0
    assert beat_markers[0]["color"] == "Cyan"
    assert "🎵 Beat Drop" in beat_markers[0]["name"]
    assert beat_markers[1]["time"] == 4.0
    assert beat_markers[1]["color"] == "Cyan"


def test_generate_timeline_fcpxml_with_bgm_and_beat_markers(tmp_path):
    v_path = tmp_path / "clip1.mp4"
    bgm_path = tmp_path / "bgm.mp3"
    v_path.write_bytes(b"dummy")
    bgm_path.write_bytes(b"dummy")

    events = [
        {
            "video_path": str(v_path),
            "src_in": 0.0,
            "src_out": 10.0,
            "rec_in": 0.0,
            "rec_out": 10.0,
            "fps": 30.0,
        }
    ]

    clip_db = {
        str(v_path): {
            "duration": 10.0,
            "has_audio": True,
        },
        str(bgm_path): {
            "duration": 10.0,
            "has_audio": True,
        },
    }

    out_xml = os.path.join(tmp_path, "timeline.fcpxml")
    FCPXMLGenerator.generate_timeline_fcpxml(
        events=events,
        output_xml_path=out_xml,
        bgm_files=[str(bgm_path)],
        music_beats=[2.5, 5.0],
        clip_metadata_db=clip_db,
    )

    assert os.path.exists(out_xml)
    with open(out_xml, "r", encoding="utf-8") as f:
        content = f.read()

    assert '<marker' in content
    assert '🎵 Beat Drop' in content or 'Beat Drop' in content
    assert 'role="music"' in content or 'audioRole="music"' in content or 'lane="-1"' in content
