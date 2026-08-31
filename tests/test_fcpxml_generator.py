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

    # Offset của từ "Câu" đầu tiên (2.0s timeline) trong Clip 1 (src_in=10s) phải là 12000/1000s
    assert 'offset="12000/1000s"' in content
    # Offset của từ "Câu" thứ hai (7.0s timeline) trong Clip 2 (src_in=30s, rec_in=5s) phải là 32000/1000s
    assert 'offset="32000/1000s"' in content

