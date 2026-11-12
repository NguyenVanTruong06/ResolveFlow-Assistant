"""
Tests for ChunDVC Auto-Update & Floating Notification Toast ("như Orca").
"""

import sys
import json
from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.services.update_service import (
    parse_version_tuple, check_for_updates_sync,
    UpdateCheckWorker, UpdateDownloadAndInstallWorker,
    CURRENT_VERSION
)
from src.ui.widgets.update_toast import UpdateToastWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_parse_version_tuple():
    assert parse_version_tuple("v1.0.0") == (1, 0, 0)
    assert parse_version_tuple("v1.0.4") == (1, 0, 4)
    assert parse_version_tuple("v1.0.4.1") == (1, 0, 4, 1)
    assert parse_version_tuple("v1.0.4.2") > parse_version_tuple("v1.0.4.1")
    assert parse_version_tuple("v1.0.4.1") > parse_version_tuple("v1.0.4")
    assert parse_version_tuple("1.2.3") == (1, 2, 3)
    assert parse_version_tuple("v2.0.0-beta1") == (2, 0, 0)
    assert parse_version_tuple("v1.0.5") > parse_version_tuple("v1.0.4")
    assert parse_version_tuple("v1.1.0") > parse_version_tuple("v1.0.9")


def test_check_for_updates_sync_mock():
    mock_payload = {
        "tag_name": "v9.9.9",
        "name": "ChunDVC v9.9.9",
        "html_url": "https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/releases/tag/v9.9.9",
        "body": "Bản cập nhật lớn với nhiều tính năng mới",
        "assets": [
            {
                "name": "resolveflow-assistant_9.9.9_amd64.deb",
                "browser_download_url": "https://example.com/chundvc.deb",
                "size": 1024000
            },
            {
                "name": "ResolveFlow-Assistant-v9.9.9-windows-x64.zip",
                "browser_download_url": "https://example.com/chundvc_win.zip",
                "size": 2048000
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        info = check_for_updates_sync()
        assert info is not None
        assert info["has_update"] is True
        assert info["latest_version"] == "9.9.9"
        assert info["assets"]["deb"] == "https://example.com/chundvc.deb"
        assert info["assets"]["windows_zip"] == "https://example.com/chundvc_win.zip"


def test_update_toast_widget_ui(qapp):
    fake_info = {
        "has_update": True,
        "current_version": "1.0.3",
        "latest_version": "1.0.5",
        "tag_name": "v1.0.5",
        "release_name": "ChunDVC v1.0.5",
        "html_url": "https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/releases/tag/v1.0.5",
        "body": "Sửa lỗi và thêm auto-update",
        "assets": {
            "deb": "https://example.com/test.deb",
            "windows_zip": "https://example.com/test.zip"
        }
    }

    toast = UpdateToastWidget(fake_info)
    assert toast.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert toast.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert toast.windowFlags() & Qt.WindowType.Tool

    assert "1.0.5" in toast.lbl_headline.text()
    assert "🚀 Cập Nhật Ngay" in toast.btn_update.text()
    assert not toast.btn_later.isHidden()
    assert toast.prog_container.isHidden()

    # Simulate close
    toast.close()


def test_current_version_v1_0_28():
    """Xác nhận phiên bản hệ thống đã nâng đồng bộ lên v1.0.28."""
    from src.core.branding import APP_VERSION
    assert CURRENT_VERSION == "1.0.28"
    assert APP_VERSION == "v1.0.28"


def test_restart_app_clean_env_and_guard(qapp, monkeypatch):
    """Kiểm tra restart_app dọn sạch biến môi trường PyInstaller và mở khóa shutdown guard."""
    import os
    import subprocess
    from PySide6.QtCore import QTimer
    from src.services.update_service import restart_app

    called_kwargs = {}
    exit_calls = []
    os_exit_calls = []
    single_shot_calls = []

    def mock_popen(args, **kwargs):
        called_kwargs.update(kwargs)
        mock_proc = MagicMock()
        return mock_proc

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr(sys, "exit", lambda code=0: exit_calls.append(code))
    monkeypatch.setattr(os, "_exit", lambda code=0: os_exit_calls.append(code))
    monkeypatch.setattr(QTimer, "singleShot", lambda ms, cb: single_shot_calls.append((ms, cb)))
    monkeypatch.setattr(qapp, "quit", lambda: None)

    monkeypatch.setenv("_MEIPASS2", "/tmp/_MEI999")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/chundvc/_internal:/usr/lib")

    guard_mock = MagicMock()
    guard_mock._allowed = False
    monkeypatch.setattr(qapp, "_source_shutdown_guard", guard_mock, raising=False)

    restart_app()

    env = called_kwargs.get("env", {})
    assert "_MEIPASS2" not in env
    assert env.get("PYINSTALLER_RESET_ENVIRONMENT") == "1"
    assert "_internal" not in env.get("LD_LIBRARY_PATH", "")
    assert "/usr/lib" in env.get("LD_LIBRARY_PATH", "")

    if os.name == "posix":
        assert called_kwargs.get("start_new_session") is True

    assert guard_mock._allowed is True
    assert exit_calls == [0]
    assert len(single_shot_calls) == 1
    assert single_shot_calls[0][0] == 250
    single_shot_calls[0][1]()
    assert os_exit_calls == [0]


def test_download_file_retry_on_504_and_succeed(qapp, tmp_path, monkeypatch):
    """Kiểm tra _download_file tự động thử lại khi gặp HTTP 504 và tải thành công ở lần sau."""
    import urllib.error
    import io

    worker = UpdateDownloadAndInstallWorker({"latest_version": "1.0.17", "assets": {}})
    dest = str(tmp_path / "test_download.deb")

    attempts = 0
    def mock_urlopen(req, timeout=45):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # Lần đầu giả lập 504 Gateway Time-out
            fp = io.BytesIO(b"Gateway Time-out")
            raise urllib.error.HTTPError("https://example.com/asset.deb", 504, "Gateway Time-out", {}, fp)
        # Lần hai trả về dữ liệu thành công
        mock_resp = MagicMock()
        payload = b"FAKE_DEB_DATA_1234567890"
        mock_resp.headers = {"content-length": str(len(payload))}
        mock_resp.read.side_effect = [payload, b""]
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)  # Bỏ qua sleep để test chạy tức thì

    ok = worker._download_file("https://example.com/asset.deb", dest, expected_size=24, max_retries=3)
    assert ok is True
    assert attempts == 2
    with open(dest, "rb") as f:
        assert f.read() == b"FAKE_DEB_DATA_1234567890"


def test_download_file_fallback_url_success(qapp, tmp_path, monkeypatch):
    """Kiểm tra _download_file tự động chuyển sang fallback_url khi URL chính bị 504."""
    import urllib.error
    import io

    worker = UpdateDownloadAndInstallWorker({"latest_version": "1.0.17", "assets": {}})
    dest = str(tmp_path / "test_download_fb.deb")

    urls_called = []
    def mock_urlopen(req, timeout=45):
        urls_called.append(req.full_url)
        if "primary.com" in req.full_url:
            fp = io.BytesIO(b"Gateway Time-out")
            raise urllib.error.HTTPError(req.full_url, 504, "Gateway Time-out", {}, fp)
        mock_resp = MagicMock()
        payload = b"FALLBACK_DATA"
        mock_resp.headers = {"content-length": str(len(payload))}
        mock_resp.read.side_effect = [payload, b""]
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)

    ok = worker._download_file(
        "https://primary.com/asset.deb",
        dest,
        expected_size=13,
        max_retries=2,
        fallback_url="https://fallback.com/asset.deb"
    )
    assert ok is True
    assert "https://primary.com/asset.deb" in urls_called
    assert "https://fallback.com/asset.deb" in urls_called
    with open(dest, "rb") as f:
        assert f.read() == b"FALLBACK_DATA"


def test_is_valid_deb_validation(tmp_path):
    """Kiểm tra xác thực gói .deb bằng magic header !<arch>\n và kích thước."""
    deb_path = tmp_path / "test.deb"

    # Không tồn tại
    assert not UpdateDownloadAndInstallWorker._is_valid_deb(str(deb_path))

    # Kích thước nhỏ (< 1MB)
    deb_path.write_bytes(b"!<arch>\n" + b"x" * 100)
    assert not UpdateDownloadAndInstallWorker._is_valid_deb(str(deb_path))

    # Đủ lớn nhưng sai header (ví dụ file JSON 1.5MB)
    fake_json = b"{\"message\": \"error\"}" * 100000
    deb_path.write_bytes(fake_json)
    assert not UpdateDownloadAndInstallWorker._is_valid_deb(str(deb_path))

    # Hợp lệ: đủ lớn và đúng header !<arch>\n
    valid_data = b"!<arch>\n" + b"0" * 1_050_000
    deb_path.write_bytes(valid_data)
    assert UpdateDownloadAndInstallWorker._is_valid_deb(str(deb_path), expected_size=1_050_000)


def test_is_valid_zip_validation(tmp_path):
    """Kiểm tra xác thực gói .zip bằng magic header PK\x03\x04 và kích thước."""
    zip_path = tmp_path / "test.zip"

    # Không tồn tại
    assert not UpdateDownloadAndInstallWorker._is_valid_zip(str(zip_path))

    # Quá nhỏ
    zip_path.write_bytes(b"PK\x03\x04" + b"x" * 100)
    assert not UpdateDownloadAndInstallWorker._is_valid_zip(str(zip_path))

    # Đủ lớn nhưng sai header
    zip_path.write_bytes(b"NOT_A_ZIP" * 120_000)
    assert not UpdateDownloadAndInstallWorker._is_valid_zip(str(zip_path))

    # Hợp lệ
    valid_data = b"PK\x03\x04" + b"0" * 1_050_000
    zip_path.write_bytes(valid_data)
    assert UpdateDownloadAndInstallWorker._is_valid_zip(str(zip_path), expected_size=1_050_000)


def test_download_file_rejects_json_response(qapp, tmp_path, monkeypatch):
    """Kiểm tra _download_file chủ động từ chối nếu máy chủ trả về JSON thay vì binary."""
    worker = UpdateDownloadAndInstallWorker({"latest_version": "1.0.24", "assets": {}})
    dest = str(tmp_path / "test_json_reject.deb")

    mock_resp = MagicMock()
    mock_resp.headers = {
        "content-type": "application/json; charset=utf-8",
        "content-length": "100"
    }
    mock_resp.__enter__.return_value = mock_resp

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=45: mock_resp)

    with pytest.raises(IOError, match="JSON"):
        worker._download_file("https://api.github.com/repos/assets/123", dest, expected_size=100_000_000, max_retries=1)


def test_check_for_updates_sync_falls_back_to_web_on_403_rate_limit():
    """
    Khi api.github.com trả về HTTP 403 (rate limit exceeded), check_for_updates_sync()
    PHẢI tự động fallback sang github.com/.../releases/latest và expanded_assets
    để người dùng vẫn kiểm tra & tải được bản cập nhật.
    """
    import urllib.error

    def fake_urlopen(req, timeout=8):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "api.github.com" in url:
            raise urllib.error.HTTPError(url, 403, "rate limit exceeded", hdrs=None, fp=None)
        resp = MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        if url.endswith("/releases/latest"):
            resp.read.return_value = json.dumps({"tag_name": "v9.9.9"}).encode("utf-8")
            return resp
        if "/releases/expanded_assets/v9.9.9" in url:
            html = (
                '<a href="/NguyenVanTruong06/ResolveFlow-Assistant/releases/download/v9.9.9/ResolveFlow-Assistant-v9.9.9-windows-x64.zip">win</a>'
                '<a href="/NguyenVanTruong06/ResolveFlow-Assistant/releases/download/v9.9.9/resolveflow-assistant_9.9.9_amd64.deb">deb</a>'
                '<a href="/NguyenVanTruong06/ResolveFlow-Assistant/releases/download/v9.9.9/ResolveFlow-Assistant-v9.9.9-linux-x64.tar.gz">tar</a>'
            )
            resp.read.return_value = html.encode("utf-8")
            return resp
        raise AssertionError(f"Unexpected URL requested: {url}")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        info = check_for_updates_sync()

    assert info is not None
    assert info["has_update"] is True
    assert info["tag_name"] == "v9.9.9"
    assert info["latest_version"] == "9.9.9"
    assert info["assets"]["windows_zip"] == (
        "https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/releases/download/v9.9.9/"
        "ResolveFlow-Assistant-v9.9.9-windows-x64.zip"
    )
    assert info["assets"]["deb"] == (
        "https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/releases/download/v9.9.9/"
        "resolveflow-assistant_9.9.9_amd64.deb"
    )
    assert info["assets"]["linux_tar"] == (
        "https://github.com/NguyenVanTruong06/ResolveFlow-Assistant/releases/download/v9.9.9/"
        "ResolveFlow-Assistant-v9.9.9-linux-x64.tar.gz"
    )



