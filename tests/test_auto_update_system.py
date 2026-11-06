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
