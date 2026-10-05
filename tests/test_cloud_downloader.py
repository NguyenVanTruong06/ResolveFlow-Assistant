import os
import io
import pytest
from unittest.mock import patch, MagicMock
from src.core.cloud_downloader import (
    parse_google_drive_url,
    GoogleDriveDownloader,
    CloudResourceInfo,
    DownloadProgress
)


def test_parse_google_drive_file_urls():
    url1 = "https://drive.google.com/file/d/1A2B3C4D5E6F7G8H9I0J_K-L/view?usp=sharing"
    res1 = parse_google_drive_url(url1)
    assert res1 is not None
    assert res1.resource_id == "1A2B3C4D5E6F7G8H9I0J_K-L"
    assert res1.resource_type == "file"

    url2 = "https://drive.google.com/uc?id=1234567890abcdefABCDEF_-"
    res2 = parse_google_drive_url(url2)
    assert res2 is not None
    assert res2.resource_id == "1234567890abcdefABCDEF_-"
    assert res2.resource_type == "file"


def test_parse_google_drive_folder_urls():
    url_folder = "https://drive.google.com/drive/folders/1FolderID_ABC1234567890xyz?usp=drive_link"
    res = parse_google_drive_url(url_folder)
    assert res is not None
    assert res.resource_id == "1FolderID_ABC1234567890xyz"
    assert res.resource_type == "folder"


def test_parse_invalid_urls():
    assert parse_google_drive_url("") is None
    assert parse_google_drive_url("https://youtube.com/watch?v=12345") is None
    assert parse_google_drive_url("not a url") is None


def test_google_drive_download_mock(tmp_path):
    mock_content = b"Mock video file binary stream content for testing."
    mock_resp = MagicMock()
    mock_resp.headers = {
        "Content-Disposition": 'attachment; filename="Test_Vlog_Clip.mp4"',
        "Content-Length": str(len(mock_content)),
        "Content-Type": "video/mp4"
    }
    mock_resp.read.side_effect = [mock_content, b""]
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    mock_opener = MagicMock()
    mock_opener.open.return_value = mock_resp

    progress_reports = []
    def on_progress(p: DownloadProgress):
        progress_reports.append(p)

    with patch("urllib.request.build_opener", return_value=mock_opener):
        out_file = GoogleDriveDownloader.download_file(
            file_id="dummy_file_id",
            output_dir=str(tmp_path),
            progress_callback=on_progress
        )

        assert os.path.exists(out_file)
        assert os.path.basename(out_file) == "Test_Vlog_Clip.mp4"
        assert open(out_file, "rb").read() == mock_content
        assert len(progress_reports) > 0
        assert progress_reports[-1].status == "completed"


def test_google_drive_download_cancelled(tmp_path):
    mock_resp = MagicMock()
    mock_resp.headers = {
        "Content-Disposition": 'attachment; filename="cancelled.mp4"',
        "Content-Length": "1000",
        "Content-Type": "video/mp4"
    }
    mock_resp.read.return_value = b"some data"
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    mock_opener = MagicMock()
    mock_opener.open.return_value = mock_resp

    with patch("urllib.request.build_opener", return_value=mock_opener):
        with pytest.raises(InterruptedError):
            GoogleDriveDownloader.download_file(
                file_id="dummy_file_id",
                output_dir=str(tmp_path),
                cancel_checker=lambda: True
            )


def test_google_drive_download_folder_mock(tmp_path):
    with patch("gdown.download_folder", return_value=["file1.mp4", "file2.mp4"]):
        res = GoogleDriveDownloader.download_folder(
            folder_id="mock_folder_123",
            output_dir=str(tmp_path)
        )
        assert res == str(tmp_path)


def test_google_drive_download_folder_permission_error(tmp_path):
    with patch("gdown.download_folder", side_effect=Exception("Permission denied")):
        with pytest.raises(ValueError) as exc_info:
            GoogleDriveDownloader.download_folder(
                folder_id="mock_folder_123",
                output_dir=str(tmp_path)
            )
        assert "Google Drive" in str(exc_info.value)


def test_google_drive_download_virus_warning_bypass(tmp_path):
    # Mock HTML virus warning response from Google Drive
    mock_html = """
    <html>
      <form action="https://drive.usercontent.google.com/download" method="get">
        <input type="hidden" name="id" value="test_id_123">
        <input type="hidden" name="export" value="download">
        <input type="hidden" name="confirm" value="t">
        <input type="hidden" name="uuid" value="bbe41995-e7c0-4b50-a236-4cd1c7add44b">
      </form>
    </html>
    """.encode("utf-8")

    mock_resp1 = MagicMock()
    mock_resp1.headers = {"Content-Type": "text/html; charset=utf-8"}
    mock_resp1.read.return_value = mock_html
    mock_resp1.__enter__.return_value = mock_resp1
    mock_resp1.__exit__.return_value = None

    mock_video_bytes = b"Large 4K Raw Drone Footage Stream Bytes"
    mock_resp2 = MagicMock()
    mock_resp2.headers = {
        "Content-Disposition": 'attachment; filename="DJI_20260830175112_0084_D.MP4"',
        "Content-Length": str(len(mock_video_bytes)),
        "Content-Type": "video/mp4"
    }
    mock_resp2.read.side_effect = [mock_video_bytes, b""]
    mock_resp2.__enter__.return_value = mock_resp2
    mock_resp2.__exit__.return_value = None

    mock_opener = MagicMock()
    mock_opener.open.side_effect = [mock_resp1, mock_resp2]

    with patch("urllib.request.build_opener", return_value=mock_opener):
        out_file = GoogleDriveDownloader.download_file(
            file_id="test_id_123",
            output_dir=str(tmp_path)
        )
        assert os.path.exists(out_file)
        assert os.path.basename(out_file) == "DJI_20260830175112_0084_D.MP4"
        assert open(out_file, "rb").read() == mock_video_bytes


def test_google_drive_download_folder_structured_streaming(tmp_path):
    class MockGDriveFileItem:
        def __init__(self, file_id, rel_path, local_p):
            self.id = file_id
            self.path = rel_path
            self.local_path = local_p

    item1 = MockGDriveFileItem("id_1", "part1/clip1.mp4", str(tmp_path / "part1" / "clip1.mp4"))
    item2 = MockGDriveFileItem("id_2", "part2/clip2.mp4", str(tmp_path / "part2" / "clip2.mp4"))

    # File 2 already exists
    os.makedirs(str(tmp_path / "part2"), exist_ok=True)
    with open(str(tmp_path / "part2" / "clip2.mp4"), "wb") as f:
        f.write(b"existing clip content")

    with patch("gdown.download_folder", return_value=[item1, item2]):
        with patch.object(GoogleDriveDownloader, "download_file") as mock_dl_file:
            # When downloading file 1, simulate creation
            def side_effect_dl(file_id, output_dir, custom_filename, **kwargs):
                os.makedirs(output_dir, exist_ok=True)
                p = os.path.join(output_dir, custom_filename)
                with open(p, "wb") as f:
                    f.write(b"clip1 content")
                return p

            mock_dl_file.side_effect = side_effect_dl

            res = GoogleDriveDownloader.download_folder(
                folder_id="mock_folder_123",
                output_dir=str(tmp_path)
            )
            assert res == str(tmp_path)
            # mock_dl_file should be called for item1, but skipped for item2 because it exists
            assert mock_dl_file.call_count == 1
            assert mock_dl_file.call_args[1]["file_id"] == "id_1"


def test_is_valid_binary_file_detection(tmp_path):
    # Valid binary file
    valid_file = tmp_path / "valid.mp4"
    valid_file.write_bytes(b"\x00\x00\x00 ftypisom" + b"\x00" * 40000)
    assert GoogleDriveDownloader._is_valid_binary_file(str(valid_file)) is True

    # Corrupted HTML error file disguised as MP4
    html_file = tmp_path / "corrupt.mp4"
    html_file.write_bytes(b"<!DOCTYPE html><html><head><title>Google Drive - Quota exceeded</title></head></html>")
    assert GoogleDriveDownloader._is_valid_binary_file(str(html_file)) is False

    # Empty file
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")
    assert GoogleDriveDownloader._is_valid_binary_file(str(empty_file)) is False


def test_extract_download_link_from_html():
    html_link = '<a id="uc-download-link" href="https://drive.usercontent.google.com/download?id=123&confirm=t&uuid=abc">Download</a>'
    extracted = GoogleDriveDownloader._extract_download_link_from_html(html_link, "123")
    assert extracted == "https://drive.usercontent.google.com/download?id=123&confirm=t&uuid=abc"

    html_form = '<form action="https://drive.usercontent.google.com/download"><input name="id" value="xyz"><input name="confirm" value="t"></form>'
    extracted_form = GoogleDriveDownloader._extract_download_link_from_html(html_form, "xyz")
    assert "xyz" in extracted_form
    assert "confirm=t" in extracted_form



