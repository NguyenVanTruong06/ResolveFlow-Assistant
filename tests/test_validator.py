import os
import sys
from unittest.mock import patch, MagicMock
import pytest
from src.core.validator import DryRunValidator, ValidationResult
from src.core.fcpxml_generator import FCPXMLGenerator

def test_validate_empty_paths():
    res = DryRunValidator.validate_media_files([])
    assert res.is_valid is False
    assert len(res.errors) > 0
    assert "Không có tệp video nào được chọn" in res.errors[0]

@patch("os.path.exists")
def test_validate_nonexistent_file(mock_exists):
    mock_exists.return_value = False
    res = DryRunValidator.validate_media_files(["nonexistent.mp4"])
    assert res.is_valid is False
    assert any("Tệp không tồn tại" in err for err in res.errors)

@patch("os.path.exists")
@patch("os.path.getsize")
def test_validate_empty_file(mock_getsize, mock_exists):
    mock_exists.return_value = True
    mock_getsize.return_value = 0
    res = DryRunValidator.validate_media_files(["empty.mp4"])
    assert res.is_valid is False
    assert any("Tệp video bị rỗng" in err for err in res.errors)

@patch("os.path.exists")
@patch("os.path.getsize")
@patch("src.core.validator.DryRunValidator.get_video_stream_info")
def test_validate_10bit_warning_windows(mock_get_info, mock_getsize, mock_exists):
    mock_exists.return_value = True
    mock_getsize.return_value = 1000
    mock_get_info.return_value = {
        "codec_name": "h264",
        "pix_fmt": "yuv420p10le",
        "fps": 30.0,
        "width": 1920,
        "height": 1080,
        "duration": 10.0
    }
    
    with patch("sys.platform", "win32"):
        res = DryRunValidator.validate_media_files(["video_10bit.mp4"])
        assert res.is_valid is True
        assert len(res.warnings) > 0
        assert "sử dụng codec 10-bit" in res.warnings[0]

@patch("os.path.exists")
@patch("os.path.getsize")
@patch("src.core.validator.DryRunValidator.get_video_stream_info")
def test_validate_10bit_no_warning_mac(mock_get_info, mock_getsize, mock_exists):
    mock_exists.return_value = True
    mock_getsize.return_value = 1000
    mock_get_info.return_value = {
        "codec_name": "h264",
        "pix_fmt": "yuv420p10le",
        "fps": 30.0,
        "width": 1920,
        "height": 1080,
        "duration": 10.0
    }
    
    with patch("sys.platform", "darwin"):
        res = DryRunValidator.validate_media_files(["video_10bit.mp4"])
        assert res.is_valid is True
        assert len(res.warnings) == 0

@patch("os.path.exists")
@patch("os.path.getsize")
@patch("src.core.validator.DryRunValidator.get_video_stream_info")
def test_validate_mismatched_fps(mock_get_info, mock_getsize, mock_exists):
    mock_exists.return_value = True
    mock_getsize.return_value = 1000
    
    def side_effect(path):
        if "vid1" in path:
            return {
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "fps": 24.0,
                "width": 1920,
                "height": 1080,
                "duration": 10.0
            }
        else:
            return {
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "fps": 30.0,
                "width": 1920,
                "height": 1080,
                "duration": 10.0
            }
    mock_get_info.side_effect = side_effect
    
    res = DryRunValidator.validate_media_files(["vid1.mp4", "vid2.mp4"])
    assert res.is_valid is True
    assert len(res.warnings) > 0
    assert "Tốc độ khung hình (Frame Rate) không đồng nhất" in res.warnings[0]

def test_validate_vietnamese_unicode_media_paths(tmp_path):
    # Tạo đường dẫn tiếng Việt có dấu
    vn_dir = tmp_path / "Dự Án Podcast"
    vn_dir.mkdir()
    vn_file = vn_dir / "Tập 1 - Giới Thiệu Công Nghệ AI.mp4"
    vn_file.write_bytes(b"mock video data with vietnamese name")

    res = DryRunValidator.validate_media_files([str(vn_file)])
    assert res.is_valid is True
    assert res.details.get("has_unicode_path") is True

def test_validate_fcpxml_integrity_success(tmp_path):
    media_file = tmp_path / "video_source.mp4"
    media_file.write_bytes(b"mock media")

    out_xml = str(tmp_path / "test_timeline.fcpxml")
    events = [
        {
            "video_path": str(media_file),
            "src_in": 0.0,
            "src_out": 5.0,
            "rec_in": 0.0,
            "rec_out": 5.0,
            "fps": 30.0
        }
    ]
    subtitles = [
        {
            "start": 1.0,
            "end": 4.0,
            "text": "Xin chào DaVinci Resolve và ResolveFlow",
            "words": [
                {"word": "Xin", "start": 1.0, "end": 1.5},
                {"word": "chào", "start": 1.5, "end": 2.0},
                {"word": "DaVinci", "start": 2.0, "end": 3.0},
                {"word": "Resolve", "start": 3.0, "end": 4.0}
            ]
        }
    ]

    with patch("src.core.autocut.get_media_metadata", return_value={"duration": 10.0, "has_audio": True}):
        FCPXMLGenerator.generate_timeline_fcpxml(
            events=events,
            output_xml_path=out_xml,
            subtitles=subtitles,
            preset="karaoke_pop"
        )

    assert os.path.exists(out_xml)
    val_res = DryRunValidator.validate_fcpxml_integrity(out_xml, expected_media_paths=[str(media_file)])
    assert val_res.is_valid is True
    assert len(val_res.errors) == 0
    assert val_res.details.get("total_titles") > 0

def test_validate_fcpxml_integrity_vietnamese_titles(tmp_path):
    media_file = tmp_path / "dự_án_quay_vlog.mp4"
    media_file.write_bytes(b"mock media vlog")

    out_xml = str(tmp_path / "test_vietnamese.fcpxml")
    events = [
        {
            "video_path": str(media_file),
            "src_in": 0.0,
            "src_out": 5.0,
            "rec_in": 0.0,
            "rec_out": 5.0,
            "fps": 30.0
        }
    ]
    # Phụ đề chứa nhiều ký tự tiếng Việt có dấu, ký tự đặc biệt & < > " '
    subtitles = [
        {
            "start": 0.5,
            "end": 3.5,
            "text": "Tôi yêu Việt Nam & Trí tuệ nhân tạo (AI) <v4.1>!",
            "words": [
                {"word": "Tôi", "start": 0.5, "end": 1.0},
                {"word": "yêu", "start": 1.0, "end": 1.5},
                {"word": "Việt", "start": 1.5, "end": 2.0},
                {"word": "Nam", "start": 2.0, "end": 2.5},
                {"word": "AI", "start": 2.5, "end": 3.5}
            ]
        }
    ]

    with patch("src.core.autocut.get_media_metadata", return_value={"duration": 10.0, "has_audio": True}):
        FCPXMLGenerator.generate_timeline_fcpxml(
            events=events,
            output_xml_path=out_xml,
            subtitles=subtitles,
            preset="glow_neon"
        )

    val_res = DryRunValidator.validate_fcpxml_integrity(out_xml, expected_media_paths=[str(media_file)])
    assert val_res.is_valid is True
    assert len(val_res.errors) == 0

def test_validate_fcpxml_integrity_media_offline(tmp_path):
    # XML trỏ tới file không tồn tại
    fake_xml = tmp_path / "offline.fcpxml"
    fake_xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r1" name="FFVideoFormat1080p" frameDuration="1/30s"/>
    <asset id="r_asset_1" name="missing.mp4" src="file:///D:/MissingFolder/missing_video_file.mp4" start="0s" duration="10s" hasVideo="1" hasAudio="1"/>
  </resources>
  <library>
    <event name="Test">
      <project name="Test Project">
        <sequence format="r1">
          <spine>
            <asset-clip name="missing.mp4" ref="r_asset_1" offset="0s" start="0s" duration="10s"/>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>""", encoding="utf-8")

    val_res = DryRunValidator.validate_fcpxml_integrity(str(fake_xml))
    assert val_res.is_valid is False
    assert any("LỖI MEDIA OFFLINE" in err for err in val_res.errors)

def test_uri_to_local_path():
    uri_win = "file:///D:/Videos/My%20Video%20D%E1%BB%B1%20%C3%81n.mp4"
    local_p = DryRunValidator._uri_to_local_path(uri_win)
    assert "D:" in local_p
    assert "My Video Dự Án.mp4" in local_p

def test_validate_fcpxml_integrity_reel_name_warning(tmp_path):
    media_file = tmp_path / "valid_clip.mp4"
    media_file.write_bytes(b"content")

    # XML thiếu thẻ metadata reel name
    no_reel_xml = tmp_path / "no_reel.fcpxml"
    no_reel_xml.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r_fmt" name="FFVideoFormat1080p" frameDuration="1/30s"/>
    <asset id="r_asset_1" name="valid_clip.mp4" src="{media_file.as_uri()}" start="0s" duration="10s" hasVideo="1" format="r_fmt" hasAudio="1"/>
  </resources>
  <library>
    <event name="Test">
      <project name="Test Project">
        <sequence format="r_fmt">
          <spine>
            <asset-clip name="valid_clip.mp4" ref="r_asset_1" offset="0s" start="0s" duration="10s" format="r_fmt" audioRole="dialogue"/>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>""", encoding="utf-8")

    val_res = DryRunValidator.validate_fcpxml_integrity(str(no_reel_xml))
    assert val_res.is_valid is True
    # Phải có cảnh báo thiếu Reel Name
    assert any("thiếu Reel Name" in w for w in val_res.warnings)

def test_validate_fcpxml_integrity_resolve_media_pool_check(tmp_path):
    from unittest.mock import MagicMock
    media_file = tmp_path / "1.mp4"
    media_file.write_bytes(b"video content")

    out_xml = tmp_path / "test_pool.fcpxml"
    out_xml.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r_fmt" name="FFVideoFormat1080p" frameDuration="1/30s"/>
    <asset id="r_asset_1" name="1.mp4" src="{media_file.as_uri()}" start="0s" duration="10s" hasVideo="1" format="r_fmt" hasAudio="1">
      <metadata>
        <md key="com.apple.proapps.studio.reel" value="1"/>
      </metadata>
    </asset>
  </resources>
  <library>
    <event name="Test">
      <project name="Test Project">
        <sequence format="r_fmt">
          <spine>
            <asset-clip name="1.mp4" ref="r_asset_1" offset="0s" start="0s" duration="10s" format="r_fmt" audioRole="dialogue">
              <metadata>
                <md key="com.apple.proapps.studio.reel" value="1"/>
              </metadata>
            </asset-clip>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>""", encoding="utf-8")

    mock_resolve = MagicMock()
    mock_resolve.is_connected.return_value = True
    # Giả lập file 1.mp4 chưa có trong Media Pool
    mock_resolve.check_clips_in_media_pool.return_value = {str(media_file): False}

    val_res = DryRunValidator.validate_fcpxml_integrity(
        str(out_xml),
        expected_media_paths=[str(media_file)],
        resolve_automation=mock_resolve
    )
    assert val_res.is_valid is True
    assert any("chưa có trong Media Pool của DaVinci Resolve" in w for w in val_res.warnings)
    assert str(media_file) in val_res.details.get("missing_in_media_pool", [])

