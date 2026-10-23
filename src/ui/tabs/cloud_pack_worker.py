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

            resource_info = parse_google_drive_url(self.drive_url)
            if not resource_info:
                raise ValueError(f"Không nhận diện được liên kết Google Drive: {self.drive_url}")

            download_tmp_dir = Path(tempfile.gettempdir()) / "resolveflow_pack_cache"
            download_tmp_dir.mkdir(parents=True, exist_ok=True)

            self.progress_signal.emit(5.0, "Đang kết nối tới Google Drive...")

            def on_progress(p: DownloadProgress):
                if not self.is_cancelled:
                    msg = f"Đang tải {p.filename}: {p.percent:.1f}% ({p.speed_mb_s:.1f} MB/s)"
                    self.progress_signal.emit(p.percent, msg)

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

            if self.is_cancelled:
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
