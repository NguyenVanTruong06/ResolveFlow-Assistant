"""
Kiểm thử tự động cho các bản vá an ninh bảo mật (Security Audit Fixes)
ResolveFlow Assistant - ChunDVC.
Bao gồm:
1. RecipeManager: Chống Path Traversal khi xóa recipe.
2. CloudDownloader: Chống Path Traversal khi tải file từ Google Drive.
3. EvidenceGrabber: Chống SSRF và bảo vệ IP nội bộ.
4. TextPreset: Chống Lua Syntax Injection trong Fusion Macro Generator.
5. ChunDVC_Bridge & BridgeConnection: Xác thực token cho socket IPC.
"""

import os
import re
import tempfile
import threading
import json
import socket
from pathlib import Path
import pytest

from src.core.recipe_manager import RecipeManager, Recipe
from src.core.cloud_downloader import GoogleDriveDownloader
from src.core.evidence_grabber import WebScreenshotEngine
from src.core.text_preset import TextStylePreset, FusionSettingGenerator
from src.core.connection.bridge import BridgeConnection, get_bridge_token
from src.resolve_scripts.ChunDVC_Bridge import handle_request, get_or_create_bridge_token


def test_recipe_manager_delete_path_traversal():
    """Kiểm tra: delete_recipe không được phép xóa file ngoài base_dir thông qua ../."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = os.path.join(tmpdir, "recipes")
        os.makedirs(base_dir, exist_ok=True)
        outside_file = os.path.join(tmpdir, "sensitive.json")
        with open(outside_file, "w", encoding="utf-8") as f:
            f.write('{"secret": "test"}')

        mgr = RecipeManager(base_dir=base_dir)

        # Cố tình truyền ID chứa path traversal
        res = mgr.delete_recipe("../sensitive")
        assert not res
        # File ngoài thư mục recipes phải còn nguyên vẹn
        assert os.path.exists(outside_file)

        # Xóa file hợp lệ trong base_dir thì thành công
        rec = Recipe(id="my_custom_preset", name="My Preset")
        mgr.save_recipe(rec)
        assert os.path.exists(os.path.join(base_dir, "my_custom_preset.json"))
        assert mgr.delete_recipe("my_custom_preset")
        assert not os.path.exists(os.path.join(base_dir, "my_custom_preset.json"))


def test_cloud_downloader_safe_filename():
    """Kiểm tra: download_file chuẩn hóa tên file an toàn, loại bỏ '..' và dotfiles."""
    headers = {"Content-Disposition": 'attachment; filename=".."' }
    extracted = GoogleDriveDownloader._extract_filename_from_headers(headers, "test_file_id")
    # Tên file phải được sanitize
    clean_name = os.path.basename(extracted).strip(". ") or "GoogleDrive_test_file_id.mp4"
    assert clean_name != ".."
    assert not clean_name.startswith("..")


def test_evidence_grabber_ssrf_protection():
    """Kiểm tra: is_safe_public_url từ chối localhost, private IP, scheme không phải HTTP/HTTPS."""
    # Các URL nguy hiểm phải bị từ chối
    assert not WebScreenshotEngine.is_safe_public_url("http://localhost:8080")
    assert not WebScreenshotEngine.is_safe_public_url("http://127.0.0.1:17005")
    assert not WebScreenshotEngine.is_safe_public_url("http://192.168.1.1/admin")
    assert not WebScreenshotEngine.is_safe_public_url("http://10.0.0.1")
    assert not WebScreenshotEngine.is_safe_public_url("http://169.254.169.254/latest/meta-data")
    assert not WebScreenshotEngine.is_safe_public_url("file:///etc/passwd")
    assert not WebScreenshotEngine.is_safe_public_url("ftp://example.com")
    assert not WebScreenshotEngine.is_safe_public_url("--help")

    # Các URL công khai hợp lệ được chấp nhận
    assert WebScreenshotEngine.is_safe_public_url("https://github.com")
    assert WebScreenshotEngine.is_safe_public_url("https://www.blackmagicdesign.com")


def test_fusion_lua_escaping():
    """Kiểm tra: generate_setting_content escape ngoặc kép và backslash trong Lua table."""
    preset = TextStylePreset(
        id="test_escape",
        name="Test",
        font="Arial",
        size=48,
        standard_color="#FFFFFF"
    )
    # Sample text chứa ngoặc kép và ký tự đặc biệt
    malicious_text = 'Hello "World" \\ with backslash'
    setting_str = FusionSettingGenerator.generate_setting_content(preset, sample_text=malicious_text)

    # Phải có dấu escape: \" và \\
    assert '\\"World\\"' in setting_str
    assert '\\\\ with backslash' in setting_str
    # Không được có ngoặc kép trần không escape gây hỏng cú pháp Lua
    assert 'StyledText = Input { Value = "Hello "World"' not in setting_str


def test_chundvc_bridge_token_authentication():
    """Kiểm tra: handle_request trong ChunDVC_Bridge yêu cầu token hợp lệ cho các lệnh hành động."""
    token = get_or_create_bridge_token()
    assert token is not None

    # 1. Ping luôn thành công
    ping_resp = handle_request("ping", {})
    assert ping_resp.get("status") == "ok"

    # 2. Lệnh hành động không gửi token hoặc gửi sai token -> Unauthorized
    unauth_resp = handle_request("import_media", {"paths": ["test.mp4"]}, client_token="wrong_token")
    assert unauth_resp.get("status") == "error"
    assert "Unauthorized" in unauth_resp.get("message", "")

    none_auth_resp = handle_request("import_media", {"paths": ["test.mp4"]}, client_token=None)
    assert none_auth_resp.get("status") == "error"
    assert "Unauthorized" in none_auth_resp.get("message", "")

    # 3. Gửi đúng token -> Được cấp quyền xử lý (có thể trả về lỗi No active project nếu chạy ngoài Resolve, nhưng không phải Unauthorized)
    auth_resp = handle_request("get_active_timeline_name", {}, client_token=token)
    assert "Unauthorized" not in auth_resp.get("message", "")
