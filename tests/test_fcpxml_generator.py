import os
import pytest
from src.core.fcpxml_generator import FCPXMLGenerator

def test_generate_karaoke_fcpxml(tmp_path) -> None:
    subtitles = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "Hello world",
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.8},
                {"word": "world", "start": 0.9, "end": 1.8}
            ]
        }
    ]
    output_xml = os.path.join(tmp_path, "timeline.fcpxml")
    FCPXMLGenerator.generate_karaoke_fcpxml(
        subtitles=subtitles,
        output_path=output_xml,
        font_size=40
    )
    
    assert os.path.exists(output_xml)
    with open(output_xml, "r", encoding="utf-8") as f:
        content = f.read()
        
    assert '<?xml version="1.0" encoding="UTF-8"?>' in content
    assert '<fcpxml version="1.9">' in content
    assert 'fontSize="40"' in content
    assert 'fontSize="48"' in content  # 40 * 1.2 = 48
    assert 'ts_highlight' in content
    assert 'ts_normal' in content

def test_generate_timeline_fcpxml(tmp_path) -> None:
    events = [
        {
            "video_path": os.path.join(tmp_path, "clip1.mp4"),
            "src_in": 1.0,
            "src_out": 4.0,
            "rec_in": 0.0,
            "rec_out": 3.0,
            "fps": 30.0
        }
    ]
    output_xml = os.path.join(tmp_path, "cut_timeline.fcpxml")
    FCPXMLGenerator.generate_timeline_fcpxml(
        events=events,
        output_xml_path=output_xml,
        timeline_name="Test Timeline"
    )
    
    assert os.path.exists(output_xml)
    with open(output_xml, "r", encoding="utf-8") as f:
        content = f.read()
        
    assert '<fcpxml version="1.9">' in content
    assert '<asset id="r_asset_1"' in content
    assert '<asset-clip name="clip1.mp4"' in content
    assert 'project name="Test Timeline"' in content

def test_generate_timeline_fcpxml_with_subtitles_offset_sync(tmp_path) -> None:
    # 2 clip cuts: Clip 1 (src: 10s-15s, rec: 0s-5s), Clip 2 (src: 30s-40s, rec: 5s-15s)
    events = [
        {
            "video_path": os.path.join(tmp_path, "clip1.mp4"),
            "src_in": 10.0,
            "src_out": 15.0,
            "rec_in": 0.0,
            "rec_out": 5.0,
            "fps": 30.0
        },
        {
            "video_path": os.path.join(tmp_path, "clip2.mp4"),
            "src_in": 30.0,
            "src_out": 40.0,
            "rec_in": 5.0,
            "rec_out": 15.0,
            "fps": 30.0
        }
    ]
    # Sub 1 ở timeline 2.0s -> Phải neo vào Clip 1 với offset = 10.0 + 2.0 = 12.0s (12000/1000s)
    # Sub 2 ở timeline 7.0s -> Phải neo vào Clip 2 với offset = 30.0 + (7.0 - 5.0) = 32.0s (32000/1000s)
    subtitles = [
        {
            "start": 2.0,
            "end": 4.0,
            "text": "Câu nói ở clip một",
            "words": [
                {"word": "Câu", "start": 2.0, "end": 2.5},
                {"word": "nói", "start": 2.6, "end": 3.0},
                {"word": "ở", "start": 3.1, "end": 3.3},
                {"word": "clip", "start": 3.4, "end": 3.7},
                {"word": "một", "start": 3.8, "end": 4.0}
            ]
        },
        {
            "start": 7.0,
            "end": 9.0,
            "text": "Câu nói ở clip hai",
            "words": [
                {"word": "Câu", "start": 7.0, "end": 7.5},
                {"word": "nói", "start": 7.6, "end": 8.0},
                {"word": "ở", "start": 8.1, "end": 8.3},
                {"word": "clip", "start": 8.4, "end": 8.7},
                {"word": "hai", "start": 8.8, "end": 9.0}
            ]
        }
    ]
    output_xml = os.path.join(tmp_path, "synced_timeline.fcpxml")
    FCPXMLGenerator.generate_timeline_fcpxml(
        events=events,
        output_xml_path=output_xml,
        timeline_name="Synced Timeline",
        subtitles=subtitles
    )

    with open(output_xml, "r", encoding="utf-8") as f:
        content = f.read()

    # Giá trị thời gian giờ được ghi theo khung hình (vd 360/30s), nên so sánh theo số giây thay vì chuỗi
    import re
    offsets = {round(int(n) / int(d), 3) for n, d in re.findall(r'offset="(\d+)/(\d+)s"', content)}
    # Offset của từ "Câu" đầu tiên (2.0s timeline) trong Clip 1 (src_in=10s) phải là 12.0s
    assert 12.0 in offsets
    # Offset của từ "Câu" thứ hai (7.0s timeline) trong Clip 2 (src_in=30s, rec_in=5s) phải là 32.0s
    assert 32.0 in offsets




def _sec(v):
    import re
    m = re.match(r"(\d+)/(\d+)s", v)
    return int(m.group(1)) / int(m.group(2)) if m else 0.0


def _spine(xml_path):
    import xml.etree.ElementTree as ET
    root = ET.parse(xml_path).getroot()
    return root, root.findall(".//spine/asset-clip")


def _ev(src_in, src_out, rec_in, speed=1.0):
    return {"video_path": "clip.mp4", "src_in": src_in, "src_out": src_out, "rec_in": rec_in,
            "rec_out": rec_in + (src_out - src_in) / speed, "fps": 30.0, "speed": speed,
            "is_speedup": speed > 1.0}


def test_speedup_clips_do_not_overlap_and_use_timemap(tmp_path):
    # Ca thực tế gây lỗi mất hình/lệch tiếng: đoạn tua nhanh 8x xen giữa các đoạn thoại
    events, rec = [], 0.0
    for src_in, src_out, speed in [(0, 10, 1.0), (10, 50, 8.0), (50, 60, 1.0), (60, 100, 4.0), (100, 110, 1.0)]:
        ev = _ev(src_in, src_out, rec, speed)
        events.append(ev)
        rec = ev["rec_out"]
    out = os.path.join(tmp_path, "speed.fcpxml")
    FCPXMLGenerator.generate_timeline_fcpxml(events, out)
    root, clips = _spine(out)
    prev_end = 0.0
    for c in clips:
        off, dur = _sec(c.get("offset")), _sec(c.get("duration"))
        assert abs(off - prev_end) < 1e-6, "clip phải nối liền nhau, không chồng lấn / hở"
        prev_end = off + dur
    assert abs(prev_end - rec) < 0.1                      # độ dài timeline = tổng thời lượng đầu ra
    retimed = [c for c in clips if c.find("timeMap") is not None]
    assert len(retimed) == 2
    pts = retimed[0].find("timeMap").findall("timept")
    assert _sec(pts[1].get("time")) == 5.0 and _sec(pts[1].get("value")) == 40.0   # 40s nguồn -> 5s đầu ra (8x)


def test_all_times_are_on_frame_grid_for_29_97(tmp_path):
    events, rec = [], 0.0
    for i in range(200):
        ev = _ev(i * 1.237, i * 1.237 + 0.913, rec)
        ev["fps"] = 29.97
        events.append(ev)
        rec = ev["rec_out"]
    out = os.path.join(tmp_path, "grid.fcpxml")
    FCPXMLGenerator.generate_timeline_fcpxml(events, out, fps=29.97)
    root, clips = _spine(out)
    fd = 1001 / 30000
    for c in clips:
        for attr in ("offset", "start", "duration"):
            frames = _sec(c.get(attr)) / fd
            assert abs(frames - round(frames)) < 1e-6, f"{attr} lệch lưới khung hình"
    assert all(abs(_sec(b.get("offset")) - (_sec(a.get("offset")) + _sec(a.get("duration")))) < 1e-9
               for a, b in zip(clips, clips[1:]))


def test_subtitles_not_anchored_inside_retimed_clips(tmp_path):
    events = [_ev(0, 10, 0.0), _ev(10, 50, 10.0, speed=8.0), _ev(50, 60, 15.0)]
    subs = [{"start": 1.0, "end": 3.0, "text": "trước", "words": []},
            {"start": 11.0, "end": 13.0, "text": "trong đoạn tua", "words": []},
            {"start": 16.0, "end": 18.0, "text": "sau", "words": []}]
    out = os.path.join(tmp_path, "subs.fcpxml")
    FCPXMLGenerator.generate_timeline_fcpxml(events, out, subtitles=subs, preset="clean_outline")
    root, clips = _spine(out)
    titles_per_clip = [len(c.findall("title")) for c in clips]
    assert titles_per_clip == [1, 0, 1]


def test_generate_fcp7_xml_multitrack(tmp_path):
    events = [
        {
            "video_path": os.path.join(tmp_path, "clip1.mp4"),
            "src_in": 0.0,
            "src_out": 5.0,
            "rec_in": 0.0,
            "rec_out": 5.0,
            "fps": 30.0,
            "speed": 1.0
        }
    ]
    broll = [
        {
            "video_path": os.path.join(tmp_path, "meme1.mp4"),
            "timeline_sec": 1.0,
            "duration_sec": 2.0,
            "src_in": 0.0
        }
    ]
    sfx = [
        {
            "audio_path": os.path.join(tmp_path, "whoosh.wav"),
            "timeline_sec": 1.0,
            "duration_sec": 0.5
        }
    ]
    clip_db = {
        os.path.join(tmp_path, "clip1.mp4"): {
            "name": "clip1.mp4",
            "path": os.path.join(tmp_path, "clip1.mp4"),
            "pathurl": f"file://localhost/{os.path.join(tmp_path, 'clip1.mp4')}",
            "duration": 10.0,
            "dur_frames": 300,
            "timecode": "01:00:00:00",
            "tc_frames": 108000,
            "has_audio": True
        },
        os.path.join(tmp_path, "meme1.mp4"): {
            "name": "meme1.mp4",
            "path": os.path.join(tmp_path, "meme1.mp4"),
            "pathurl": f"file://localhost/{os.path.join(tmp_path, 'meme1.mp4')}",
            "duration": 5.0,
            "dur_frames": 150,
            "timecode": "00:00:00:00",
            "tc_frames": 0,
            "has_audio": False
        },
        os.path.join(tmp_path, "whoosh.wav"): {
            "name": "whoosh.wav",
            "path": os.path.join(tmp_path, "whoosh.wav"),
            "pathurl": f"file://localhost/{os.path.join(tmp_path, 'whoosh.wav')}",
            "duration": 1.0,
            "dur_frames": 30,
            "timecode": "00:00:00:00",
            "tc_frames": 0,
            "has_audio": True
        }
    }

    # Create dummy files
    for p in [os.path.join(tmp_path, "clip1.mp4"), os.path.join(tmp_path, "meme1.mp4"), os.path.join(tmp_path, "whoosh.wav")]:
        with open(p, "w") as f:
            f.write("dummy")

    out_xml = os.path.join(tmp_path, "multitrack_timeline.xml")
    FCPXMLGenerator.generate_fcp7_xml(
        events=events,
        output_xml_path=out_xml,
        timeline_name="Test Multi-Track",
        fps=30.0,
        broll_inserts=broll,
        sfx_inserts=sfx,
        clip_metadata_db=clip_db
    )

    assert os.path.exists(out_xml)
    with open(out_xml, "rb") as f:
        content = f.read().decode("utf-8")

    assert '<xmeml version="5">' in content
    assert '<name>Test Multi-Track</name>' in content
    assert 'clipitem-v1-1' in content
    assert 'clipitem-broll-1' in content
    assert 'clipitem-sfx-1' in content
    assert 'timecode' in content


def test_generate_fcp7_xml_broll_audio_and_file_dedup(tmp_path):
    events = [
        {
            "video_path": os.path.join(tmp_path, "main.mp4"),
            "src_in": 0.0,
            "src_out": 4.0,
            "rec_in": 0.0,
            "rec_out": 4.0,
            "fps": 30.0,
            "speed": 1.0
        }
    ]
    broll = [
        {
            "video_path": os.path.join(tmp_path, "meme_sound.mp4"),
            "timeline_sec": 1.0,
            "duration_sec": 2.0,
            "src_in": 0.0
        }
    ]
    clip_db = {
        os.path.join(tmp_path, "main.mp4"): {
            "name": "main.mp4",
            "path": os.path.join(tmp_path, "main.mp4"),
            "pathurl": f"file://localhost/{os.path.join(tmp_path, 'main.mp4')}",
            "duration": 10.0,
            "dur_frames": 300,
            "timecode": "01:00:00:00",
            "tc_frames": 108000,
            "has_audio": True,
            "channels": 1  # Mono
        },
        os.path.join(tmp_path, "meme_sound.mp4"): {
            "name": "meme_sound.mp4",
            "path": os.path.join(tmp_path, "meme_sound.mp4"),
            "pathurl": f"file://localhost/{os.path.join(tmp_path, 'meme_sound.mp4')}",
            "duration": 5.0,
            "dur_frames": 150,
            "timecode": "00:00:00:00",
            "tc_frames": 0,
            "has_audio": True,
            "channels": 2
        }
    }

    for p in [os.path.join(tmp_path, "main.mp4"), os.path.join(tmp_path, "meme_sound.mp4")]:
        with open(p, "w") as f:
            f.write("dummy")

    out_xml = os.path.join(tmp_path, "broll_audio.xml")
    FCPXMLGenerator.generate_fcp7_xml(
        events=events,
        output_xml_path=out_xml,
        timeline_name="Test B-Roll Audio",
        fps=30.0,
        broll_inserts=broll,
        clip_metadata_db=clip_db
    )

    assert os.path.exists(out_xml)
    with open(out_xml, "rb") as f:
        content = f.read().decode("utf-8")

    # B-Roll Audio tracks A4 and A5 should be generated
    assert 'clipitem-broll-a1-1' in content
    assert 'clipitem-broll-a2-1' in content
    # File node deduplication: subsequent file nodes should be self-closing <file id="file-v1-1"/>
    assert '<file id="file-v1-1"/>' in content
    assert '<file id="file-broll-1"/>' in content


def test_generate_fcp7_xml_with_camera_timecode(tmp_path):
    clip_p = os.path.join(tmp_path, "dji_clip.mp4")
    with open(clip_p, "w") as f:
        f.write("dummy")

    events = [
        {
            "video_path": clip_p,
            "src_in": 1.0,
            "src_out": 5.0,
            "rec_in": 0.0,
            "rec_out": 4.0,
            "fps": 30.0
        }
    ]
    clip_db = {
        clip_p: {
            "name": "dji_clip.mp4",
            "path": clip_p,
            "duration": 60.0,
            "dur_frames": 1800,
            "start_tc_frames": 1776663,
            "start_tc_str": "16:27:02:03",
            "tc_format": "NDF",
            "has_audio": True
        }
    }

    out_xml = os.path.join(tmp_path, "camera_tc.xml")
    FCPXMLGenerator.generate_fcp7_xml(
        events=events,
        output_xml_path=out_xml,
        timeline_name="Camera TC Test",
        fps=30.0,
        clip_metadata_db=clip_db
    )

    assert os.path.exists(out_xml)
    with open(out_xml, "r", encoding="utf-8") as f:
        content = f.read()

    # Timecode metadata in file node
    assert "<frame>1776663</frame>" in content
    assert "<string>16:27:02:03</string>" in content
    assert "<displayformat>NDF</displayformat>" in content
    # In/Out offsets adjusted with camera timecode: src_in = 1.0s (30 frames) -> 1776663 + 30 = 1776693
    assert "<in>1776693</in>" in content
    # Out: 1776693 + 120 = 1776813
    assert "<out>1776813</out>" in content


def test_generate_timeline_fcpxml_with_camera_timecode(tmp_path):
    clip_p = os.path.join(tmp_path, "dji_clip.mp4")
    with open(clip_p, "w") as f:
        f.write("dummy")

    events = [
        {
            "video_path": clip_p,
            "src_in": 2.0,
            "src_out": 6.0,
            "rec_in": 0.0,
            "rec_out": 4.0,
            "fps": 30.0
        }
    ]
    clip_db = {
        clip_p: {
            "name": "dji_clip.mp4",
            "path": clip_p,
            "duration": 60.0,
            "dur_frames": 1800,
            "start_tc_frames": 1776663,
            "start_tc_str": "16:27:02:03",
            "has_audio": True
        }
    }

    out_fcpxml = os.path.join(tmp_path, "camera_tc.fcpxml")
    FCPXMLGenerator.generate_timeline_fcpxml(
        events=events,
        output_xml_path=out_fcpxml,
        timeline_name="Camera TC FCPXML Test",
        fps=30.0,
        clip_metadata_db=clip_db
    )

    assert os.path.exists(out_fcpxml)
    with open(out_fcpxml, "r", encoding="utf-8") as f:
        content = f.read()

    # Asset start attribute matches start_tc_f (1776663 / 30s)
    assert 'start="1776663/30s"' in content
    # Asset clip start: (1776663 + 60) / 30s = 1776723/30s
    assert 'start="1776723/30s"' in content



