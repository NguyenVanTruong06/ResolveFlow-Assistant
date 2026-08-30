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

