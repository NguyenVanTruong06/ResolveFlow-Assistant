"""
ChunDVC / ResolveFlow Assistant - Update Checking Service.
Kiểm tra bản cập nhật mới nhất từ GitHub Releases (tương tự cơ chế của Orca).
"""

import re
import json
import logging
import urllib.request
from typing import Optional, Dict, Any, Tuple
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("ChunDVC.UpdateService")

CURRENT_VERSION = "1.0.3"
GITHUB_REPO = "NguyenVanTruong06/ResolveFlow-Assistant"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def parse_version_tuple(ver_str: str) -> Tuple[int, ...]:
    """Chuyển chuỗi version (vd 'v1.0.1' hoặc '1.0.2-beta1') thành tuple (1, 0, 2) để so sánh."""
    cleaned = re.sub(r'^[vV]', '', str(ver_str).strip())
    cleaned = cleaned.split('-')[0].split('+')[0]
    parts = cleaned.split('.')
    numeric = []
    for p in parts:
        digits = re.findall(r'\d+', p)
        if digits:
            numeric.append(int(digits[0]))
        else:
            break
    return tuple(numeric) if numeric else (0, 0, 0)


def check_for_updates_sync(timeout: int = 6) -> Optional[Dict[str, Any]]:
    """
    Kiểm tra đồng bộ xem có phiên bản mới hơn trên GitHub Releases hay không.
    Trả về dict thông tin nếu có bản mới, hoặc None nếu đã là bản mới nhất / mất mạng.
    """
    try:
        req = urllib.request.Request(
            LATEST_RELEASE_API,
            headers={
                "User-Agent": f"ChunDVC-ResolveFlow/{CURRENT_VERSION}",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read().decode("utf-8"))

        tag_name = data.get("tag_name", "")
        remote_ver = parse_version_tuple(tag_name)
        local_ver = parse_version_tuple(CURRENT_VERSION)

        has_update = remote_ver > local_ver

        assets_info = {}
        for asset in data.get("assets", []):
            name = asset.get("name", "").lower()
            url = asset.get("browser_download_url", "")
            if name.endswith(".deb"):
                assets_info["deb"] = url
            elif name.endswith(".tar.gz"):
                assets_info["linux_tar"] = url
            elif name.endswith(".zip"):
                assets_info["windows_zip"] = url

        return {
            "has_update": has_update,
            "current_version": CURRENT_VERSION,
            "latest_version": tag_name.lstrip("vV"),
            "tag_name": tag_name,
            "release_name": data.get("name") or tag_name,
            "html_url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases/tag/{tag_name}",
            "body": data.get("body", "").strip(),
            "published_at": data.get("published_at", ""),
            "assets": assets_info
        }
    except Exception as exc:
        logger.debug("Lỗi khi kiểm tra cập nhật từ GitHub: %s", exc)
        return None


class UpdateCheckWorker(QThread):
    """Worker chạy ngầm không làm đơ giao diện khi kiểm tra bản cập nhật mới."""
    update_found = Signal(dict)
    up_to_date = Signal(str)
    check_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        info = check_for_updates_sync(timeout=8)
        if info is None:
            self.check_failed.emit("Không thể kết nối tới máy chủ GitHub để kiểm tra cập nhật.")
            return

        if info.get("has_update"):
            self.update_found.emit(info)
        else:
            self.up_to_date.emit(f"Bạn đang dùng phiên bản mới nhất ({CURRENT_VERSION}).")
