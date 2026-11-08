"""
Luồng chạy ngầm tải gói từ Google Drive / Cloud và tự động cài đặt vào ResolveFlow.
"""

import os
import tempfile
from pathlib import Path
from typing import Optional, Dict
from PySide6.QtCore import QThread, Signal

from src.core.pack_manager import PackManager, PackManifest
from src.core.cloud_downloader import (
    parse_google_drive_url,
    GoogleDriveDownloader,
    DownloadProgress,
    CloudResourceInfo
)


class CloudPackInstallWorker(QThread):
    """
    Worker tải ngầm gói tài nguyên (.rfpack / .zip) từ Google Drive hoặc tệp nội bộ
    và giải nén cài đặt vào hệ thống.
    """
    progress_signal = Signal(float, str)  # (percent, status_message)
    finished_signal = Signal(bool, str, dict)  # (success, message, installed_counts)

    def __init__(
        self,
        pack_manifest: Optional[PackManifest] = None,
        drive_url: Optional[str] = None,
        direct_file_path: Optional[str] = None,
        target_assets_dir: str = "assets",
        parent=None
    ):
        super().__init__(parent)
        self.pack_manifest = pack_manifest
        self.drive_url = drive_url or (pack_manifest.download_url if pack_manifest else None)
        self.direct_file_path = direct_file_path
        self.target_assets_dir = target_assets_dir
        self.is_cancelled = False

    def cancel(self):
        """Yêu cầu dừng tiến trình tải."""
        self.is_cancelled = True

    def run(self):
        try:
            # 1. Trường hợp có tệp cục bộ sẵn (kéo thả hoặc test)
            if self.direct_file_path:
                self.progress_signal.emit(50.0, "Đang giải nén và phân loại tài nguyên...")
                counts = PackManager.install_pack(
                    self.direct_file_path,
                    target_base_dir=self.target_assets_dir,
                    pack_manifest=self.pack_manifest,
                    isolate_pack_folder=True
                )
                self.progress_signal.emit(100.0, "Hoàn tất cài đặt!")
                self.finished_signal.emit(True, "Cài đặt gói tài nguyên thành công!", counts)
                return

            # 2. Trường hợp tải từ Google Drive / Cloud URL
            if not self.drive_url:
                raise ValueError("Không có đường dẫn tải về hoặc tệp cài đặt hợp lệ.")

            download_tmp_dir = Path(tempfile.gettempdir()) / "resolveflow_pack_cache"
            try:
                from src.core.storage_manager import StorageManager
                sm = StorageManager()
                if sm.online:
                    download_tmp_dir = sm.meta / "staging" / "cloud_pack_cache"
            except Exception:
                pass
            download_tmp_dir.mkdir(parents=True, exist_ok=True)

            resource_info = parse_google_drive_url(self.drive_url)

            def on_progress(p: DownloadProgress):
                if not self.is_cancelled:
                    msg = f"Đang tải {p.filename}: {p.percent:.1f}% ({p.speed_mb_s:.1f} MB/s)"
                    self.progress_signal.emit(p.percent, msg)

            if resource_info:
                self.progress_signal.emit(5.0, "Đang kết nối tới Google Drive...")
                if resource_info.resource_type == "folder":
                    downloaded_target = GoogleDriveDownloader.download_folder(
                        folder_id=resource_info.resource_id,
                        output_dir=str(download_tmp_dir),
                        progress_callback=on_progress,
                        cancel_checker=lambda: self.is_cancelled
                    )
                else:
                    downloaded_target = GoogleDriveDownloader.download_file(
                        file_id=resource_info.resource_id,
                        output_dir=str(download_tmp_dir),
                        progress_callback=on_progress,
                        cancel_checker=lambda: self.is_cancelled
                    )
            elif self.drive_url.startswith(("http://", "https://")):
                import urllib.request
                from urllib.parse import urlparse, unquote
                parsed = urlparse(self.drive_url)
                fname = Path(unquote(parsed.path)).name or "cloud_pack.rfpack"
                dest_file = download_tmp_dir / fname
                self.progress_signal.emit(5.0, f"Đang kết nối tới máy chủ tải {fname}...")
                req = urllib.request.Request(self.drive_url, headers={"User-Agent": "ResolveFlow/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp, open(dest_file, "wb") as out_f:
                    total_bytes = int(resp.headers.get("Content-Length") or 0)
                    downloaded = 0
                    while not self.is_cancelled:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        downloaded += len(chunk)
                        if total_bytes > 0:
                            pct = min(88.0, 5.0 + (downloaded / total_bytes) * 83.0)
                            self.progress_signal.emit(pct, f"Đang tải {fname}: {downloaded / (1024**2):.1f}/{total_bytes / (1024**2):.1f} MB")
                downloaded_target = str(dest_file)
            else:
                raise ValueError(f"Không nhận diện được liên kết tải: {self.drive_url}")

            if self.is_cancelled:
                try:
                    if os.path.isfile(downloaded_target):
                        os.unlink(downloaded_target)
                except OSError:
                    pass
                self.finished_signal.emit(False, "Đã hủy tải gói.", {})
                return

            self.progress_signal.emit(90.0, "Đang giải nén và nạp vào kho ResolveFlow...")

            # Nếu tải về là tệp nén .rfpack hoặc .zip, tiến hành cài đặt
            if zipfile_is_valid(downloaded_target):
                counts = PackManager.install_pack(
                    downloaded_target,
                    target_base_dir=self.target_assets_dir,
                    pack_manifest=self.pack_manifest,
                    isolate_pack_folder=True
                )
                try:
                    if os.path.isfile(downloaded_target):
                        os.unlink(downloaded_target)
                except OSError:
                    pass
            else:
                counts = {"custom": 1}

            self.progress_signal.emit(100.0, "Đã cài đặt thành công!")
            pack_name = self.pack_manifest.name if self.pack_manifest else "Gói đám mây"
            self.finished_signal.emit(
                True,
                f"Đã tải và cài đặt thành công {pack_name}!",
                counts
            )

        except Exception as e:
            self.finished_signal.emit(False, f"Lỗi trong quá trình cài đặt: {e}", {})


def zipfile_is_valid(filepath: str) -> bool:
    import zipfile
    try:
        return zipfile.is_zipfile(filepath)
    except Exception:
        return False
