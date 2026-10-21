"""
Module phát hiện thực thể web và tự động chụp ảnh màn hình bằng chứng (Web Evidence Hunter).
Học hỏi từ kiến trúc SeeCut: tự động săn bằng chứng web thật làm B-Roll thay vì ảnh stock giả.
"""

import os
import re
import sys
import shutil
import hashlib
import subprocess
import socket
import ipaddress
import urllib.parse
from typing import List, Dict, Optional, Tuple
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont


class EvidenceTarget(BaseModel):
    """Mô hình dữ liệu đại diện cho một mục tiêu minh chứng cần hiển thị trên timeline."""
    keyword: str = Field(..., description="Từ khóa hoặc tên thương hiệu được nhắc tới")
    target_url: str = Field(..., description="Đường link URL cần truy cập và chụp màn hình")
    start_time: float = Field(default=0.0, description="Thời điểm bắt đầu câu thoại (giây)")
    end_time: float = Field(default=0.0, description="Thời điểm kết thúc câu thoại (giây)")
    confidence: float = Field(default=0.9, description="Độ tin cậy của thực thể")
    evidence_path: Optional[str] = Field(default=None, description="Đường dẫn tệp ảnh đã chụp")


class EvidenceKeywordDetector:
    """Bộ phân tích phát hiện URL và thực thể công nghệ trong transcript."""

    # Bảng ánh xạ các thương hiệu công nghệ và nền tảng thông dụng sang URL chính thức
    TECH_ENTITY_MAP: Dict[str, str] = {
        "davinci resolve": "https://www.blackmagicdesign.com/products/davinciresolve",
        "chatgpt": "https://chatgpt.com",
        "openai": "https://openai.com",
        "github": "https://github.com",
        "huggingface": "https://huggingface.co",
        "midjourney": "https://www.midjourney.com",
        "notion": "https://www.notion.so",
        "python": "https://www.python.org",
        "claude": "https://claude.ai",
        "gemini": "https://gemini.google.com",
        "youtube": "https://www.youtube.com",
        "vnexpress": "https://vnexpress.net",
        "seecut": "https://github.com/YeJe-cpu/SeeCut",
        "resolveflow": "https://github.com",
    }

    # Regex quét URL trần (http / https)
    URL_PATTERN = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')
    # Regex quét domain dạng example.com/path
    DOMAIN_PATTERN = re.compile(r'\b(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|io|ai|co|vn|edu|gov)(?:/[^\s<>"]*)?\b', re.IGNORECASE)

    def detect_targets(self, text: str, start_time: float = 0.0, end_time: float = 0.0) -> List[EvidenceTarget]:
        """Quét và trích xuất danh sách các thực thể minh chứng từ câu thoại."""
        targets: List[EvidenceTarget] = []
        found_urls = set()

        # 1. Quét URL trực tiếp (http/https)
        for match in self.URL_PATTERN.finditer(text):
            raw_url = match.group(0).rstrip('.,;!?')
            clean_url = raw_url if raw_url.startswith("http") else f"https://{raw_url}"
            if clean_url not in found_urls:
                found_urls.add(clean_url)
                targets.append(EvidenceTarget(
                    keyword=clean_url,
                    target_url=clean_url,
                    start_time=start_time,
                    end_time=end_time,
                    confidence=1.0
                ))

        # 2. Quét domain không có tiền tố http
        for match in self.DOMAIN_PATTERN.finditer(text):
            raw_domain = match.group(0).rstrip('.,;!?')
            clean_url = f"https://{raw_domain}"
            if clean_url not in found_urls and not any(raw_domain in u for u in found_urls):
                found_urls.add(clean_url)
                targets.append(EvidenceTarget(
                    keyword=raw_domain,
                    target_url=clean_url,
                    start_time=start_time,
                    end_time=end_time,
                    confidence=0.95
                ))

        # 3. Quét các tên thương hiệu / nền tảng công nghệ
        text_lower = text.lower()
        for entity, url in self.TECH_ENTITY_MAP.items():
            pattern = r'\b' + re.escape(entity) + r'\b'
            if re.search(pattern, text_lower):
                if url not in found_urls:
                    found_urls.add(url)
                    targets.append(EvidenceTarget(
                        keyword=entity.title(),
                        target_url=url,
                        start_time=start_time,
                        end_time=end_time,
                        confidence=0.90
                    ))

        return targets


class WebScreenshotEngine:
    """Trình duyệt không đầu (Headless Browser) tự động chụp ảnh màn hình bằng chứng sắc nét."""

    def __init__(self, output_dir: str = "data/evidence", timeout: float = 8.0):
        self.output_dir = output_dir
        self.timeout = timeout
        os.makedirs(self.output_dir, exist_ok=True)
        self.browser_exe = self._find_browser_executable()

    @staticmethod
    def _find_browser_executable() -> Optional[str]:
        """Tự động dò tìm đường dẫn thực thi của Chrome/Chromium trên hệ thống."""
        # Ưu tiên các binary chuẩn trên Linux / macOS
        posix_candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
        for c in posix_candidates:
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c

        which_chrome = shutil.which("google-chrome") or shutil.which("chromium")
        if which_chrome:
            return which_chrome

        # Dò tìm trên Windows
        if sys.platform == "win32":
            win_candidates = [
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            ]
            for c in win_candidates:
                if os.path.isfile(c):
                    return c

        return None

    def _generate_mock_screenshot(self, url: str, out_path: str):
        """Tạo ảnh đồ họa mock thanh lịch (khi offline hoặc không có trình duyệt)."""
        w, h = 1920, 1080
        img = Image.new("RGBA", (w, h), (15, 23, 42, 255)) # Dark slate bg
        draw = ImageDraw.Draw(img)

        # Thanh tiêu đề trình duyệt (Browser Header)
        draw.rectangle([0, 0, w, 70], fill=(30, 41, 59, 255))
        # 3 nút bấm macOS (Đỏ, Vàng, Xanh)
        draw.ellipse([30, 25, 50, 45], fill=(239, 68, 68, 255))
        draw.ellipse([65, 25, 85, 45], fill=(245, 158, 11, 255))
        draw.ellipse([100, 25, 120, 45], fill=(34, 197, 94, 255))

        # Khung địa chỉ URL (Address Bar)
        draw.rounded_rectangle([200, 15, w - 200, 55], radius=8, fill=(15, 23, 42, 255))
        # Vẽ URL text
        font = ImageFont.load_default()
        draw.text((220, 25), f"🔒 {url}", fill=(148, 163, 184, 255), font=font)

        # Nội dung minh chứng trung tâm (Evidence Card)
        draw.rounded_rectangle([150, 130, w - 150, h - 100], radius=16, fill=(30, 41, 59, 200), outline=(51, 65, 85, 255), width=2)
        draw.text((w // 2 - 150, h // 2 - 40), "EVIDENCE SNAPSHOT", fill=(56, 189, 248, 255), font=font)
        draw.text((w // 2 - 120, h // 2), url, fill=(241, 245, 249, 255), font=font)

        img.save(out_path)

    @staticmethod
    def is_safe_public_url(url: str) -> bool:
        """
        Kiểm tra xem URL có an toàn để chụp màn hình hay không:
        - Chỉ cho phép scheme http và https.
        - Không cho phép localhost, IP loopback (127.0.0.0/8), RFC 1918 private IPs,
          cloud metadata (169.254.169.254), hoặc multicast/broadcast.
        """
        if not url or not isinstance(url, str):
            return False
        try:
            parsed = urllib.parse.urlparse(url.strip())
            if parsed.scheme.lower() not in ("http", "https"):
                return False
            hostname = parsed.hostname
            if not hostname:
                return False
            hostname_lower = hostname.lower()
            if hostname_lower in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
                return False

            try:
                addr_info = socket.getaddrinfo(hostname, None)
                for item in addr_info:
                    ip_str = item[4][0]
                    ip = ipaddress.ip_address(ip_str)
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                        return False
            except Exception:
                return False
            return True
        except Exception:
            return False

    def capture_screenshot(self, url: str, out_path: Optional[str] = None, mock: bool = False) -> str:
        """
        Chụp ảnh màn hình web full HD 1920x1080.
        Có cơ chế cache theo hash URL để không phải chụp lại cùng 1 link.
        Tự động bảo vệ chống SSRF và flag injection.
        """
        clean_url = str(url).strip()
        if not out_path:
            url_hash = hashlib.sha256(clean_url.encode("utf-8")).hexdigest()[:16]
            out_path = os.path.join(self.output_dir, f"evidence_{url_hash}.png")

        # 1. Kiểm tra cache
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            return out_path

        # 2. Nếu ở chế độ mock, không có trình duyệt hoặc URL là mạng nội bộ / không an toàn
        if mock or not self.browser_exe or not self.is_safe_public_url(clean_url):
            self._generate_mock_screenshot(clean_url, out_path)
            return out_path

        # 3. Gọi trình duyệt headless trực tiếp với cờ kết thúc '--'
        cmd = [
            self.browser_exe,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1920,1080",
            f"--screenshot={out_path}",
            "--",
            clean_url
        ]

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout
            )
            if res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        except Exception:
            pass

        # Fallback tạo ảnh đồ họa nếu chụp web thật bị lỗi/timeout
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            self._generate_mock_screenshot(clean_url, out_path)

        return out_path
