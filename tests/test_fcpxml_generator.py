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
