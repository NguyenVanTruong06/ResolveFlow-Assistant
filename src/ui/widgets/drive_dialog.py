"""
Google Drive Import Dialog for ResolveFlow Assistant.
Giao diện nhập liên kết Google Drive, hiển thị tiến trình tải trực quan và tự động nạp video vào pipeline.
"""

import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QProgressBar, QFileDialog, QMessageBox, QFrame
)
from PySide6.QtCore import Qt, QThread, Signal as pyqtSignal
from src.ui.theme import ThemeColors, ThemeFonts
from src.core.cloud_downloader import (
    parse_google_drive_url,
    GoogleDriveDownloader,
    DownloadProgress,
    CloudResourceInfo
)


class CloudDownloadWorker(QThread):
    """Luồng chạy ngầm tải video từ Google Drive."""
    progress_signal = pyqtSignal(object)  # DownloadProgress
    finished_signal = pyqtSignal(bool, str, str)  # success, target_path, message

    def __init__(self, resource_info: CloudResourceInfo, output_dir: str):
        super().__init__()
        self.resource_info = resource_info
        self.output_dir = output_dir
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            if self.resource_info.resource_type == "folder":
                target_path = GoogleDriveDownloader.download_folder(
                    folder_id=self.resource_info.resource_id,
                    output_dir=self.output_dir,
                    progress_callback=lambda p: self.progress_signal.emit(p),
                    cancel_checker=lambda: self.is_cancelled
                )
                self.finished_signal.emit(True, target_path, "Tải thư mục Google Drive thành công!")
            else:
                target_path = GoogleDriveDownloader.download_file(
                    file_id=self.resource_info.resource_id,
                    output_dir=self.output_dir,
                    progress_callback=lambda p: self.progress_signal.emit(p),
                    cancel_checker=lambda: self.is_cancelled
                )
                self.finished_signal.emit(True, target_path, "Tải tệp Google Drive thành công!")
        except Exception as e:
            self.finished_signal.emit(False, "", str(e))


class GoogleDriveImportDialog(QDialog):
    """
    Hộp thoại nhập liên kết Google Drive.
    """
    import_completed = pyqtSignal(str, bool)  # (path, is_folder)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("☁ Nhập Video / Thư mục từ Google Drive")
        self.resize(600, 360)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {ThemeColors.BG_CARD};
                color: {ThemeColors.TEXT_PRIMARY};
            }}
            QLabel {{
                color: {ThemeColors.TEXT_PRIMARY};
            }}
            QLineEdit {{
                background-color: {ThemeColors.BG_INPUT};
                color: {ThemeColors.TEXT_PRIMARY};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 8px;
            }}
            QLineEdit:focus {{
                border: 1px solid {ThemeColors.PRIMARY};
            }}
            QPushButton {{
                background-color: {ThemeColors.BG_INPUT};
                color: {ThemeColors.TEXT_PRIMARY};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                border-color: {ThemeColors.BORDER_HOVER};
            }}
        """)

        self.worker: Optional[CloudDownloadWorker] = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        # Header Title
        lbl_title = QLabel("☁ Nhập Nguồn Media Từ Google Drive")
        lbl_title.setStyleSheet(f"color: {ThemeColors.PRIMARY}; font-size: 15px; font-weight: bold;")
        lbl_desc = QLabel("Dán đường dẫn (URL) tệp video hoặc thư mục Google Drive để tự động tải về và đưa vào pipeline.")
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11px;")
        layout.addWidget(lbl_title)
        layout.addWidget(lbl_desc)

        # URL Input
        lbl_url = QLabel("<b>Đường dẫn Google Drive (URL):</b>")
        self.txt_url = QLineEdit()
        self.txt_url.setPlaceholderText("https://drive.google.com/file/d/... hoặc /drive/folders/...")
        self.txt_url.textChanged.connect(self._on_url_changed)
        layout.addWidget(lbl_url)
        layout.addWidget(self.txt_url)

        # Info Card
        self.lbl_info = QLabel("ℹ Chưa nhập URL hợp lệ")
        self.lbl_info.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; padding: 4px;")
        layout.addWidget(self.lbl_info)

        # Output Directory
        lbl_out = QLabel("<b>Thư mục lưu tệp tải về:</b>")
        out_layout = QHBoxLayout()
        default_dir = os.path.join(os.path.expanduser("~"), "Downloads", "ResolveFlow_Drive")
        self.txt_output_dir = QLineEdit(default_dir)
        btn_choose_dir = QPushButton("Chọn Thư Mục...")
        btn_choose_dir.clicked.connect(self._choose_output_dir)
        out_layout.addWidget(self.txt_output_dir, stretch=3)
        out_layout.addWidget(btn_choose_dir, stretch=1)
        layout.addWidget(lbl_out)
        layout.addLayout(out_layout)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.lbl_progress_status = QLabel("")
        self.lbl_progress_status.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-size: 11px;")
        self.lbl_progress_status.hide()
        layout.addWidget(self.lbl_progress_status)

        layout.addStretch(1)

        # Actions
        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Hủy bỏ")
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_download = QPushButton("🚀 Bắt Đầu Tải & Dựng")
        self.btn_download.setEnabled(False)
        self.btn_download.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.PRIMARY};
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.PRIMARY_HOVER};
            }}
            QPushButton:disabled {{
                background-color: {ThemeColors.BORDER_DEFAULT};
                color: {ThemeColors.TEXT_MUTED};
            }}
        """)
        self.btn_download.clicked.connect(self._start_download)

        btn_layout.addStretch(1)
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_download)
        layout.addLayout(btn_layout)

    def _on_url_changed(self, text: str):
        info = parse_google_drive_url(text)
        if info:
            type_str = "Thư mục (Folder)" if info.resource_type == "folder" else "Tệp Video Đơn (File)"
            self.lbl_info.setText(f"✔ Đã nhận diện: <b>{type_str}</b> • ID: <code>{info.resource_id}</code>")
            self.lbl_info.setStyleSheet(f"color: {ThemeColors.SUCCESS}; font-size: 11px;")
            self.btn_download.setEnabled(True)
        else:
            self.lbl_info.setText("⚠ Vui lòng dán liên kết Google Drive hợp lệ.")
            self.lbl_info.setStyleSheet(f"color: {ThemeColors.WARNING}; font-size: 11px;")
            self.btn_download.setEnabled(False)

    def _choose_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu tệp", self.txt_output_dir.text())
        if d:
            self.txt_output_dir.setText(d)

    def _start_download(self):
        url = self.txt_url.text().strip()
        info = parse_google_drive_url(url)
        if not info:
            QMessageBox.warning(self, "Lỗi URL", "Liên kết Google Drive không hợp lệ.")
            return

        out_dir = self.txt_output_dir.text().strip()
        os.makedirs(out_dir, exist_ok=True)

        self.btn_download.setEnabled(False)
        self.txt_url.setEnabled(False)
        self.txt_output_dir.setEnabled(False)
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.lbl_progress_status.show()
        self.lbl_progress_status.setText("⏳ Đang kết nối tới máy chủ Google Drive...")

        self.worker = CloudDownloadWorker(info, out_dir)
        self.worker.progress_signal.connect(self._on_progress)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, p: DownloadProgress):
        self.progress_bar.setValue(int(p.percent))
        mb_down = p.downloaded_bytes / (1024 * 1024)
        mb_tot = p.total_bytes / (1024 * 1024) if p.total_bytes > 0 else 0
        if mb_tot > 0:
            self.lbl_progress_status.setText(
                f"📥 Đang tải: <b>{p.filename}</b> ({mb_down:.1f} MB / {mb_tot:.1f} MB) • ⚡ {p.speed_mb_s:.1f} MB/s"
            )
        else:
            self.lbl_progress_status.setText(
                f"📥 Đang tải: <b>{p.filename}</b> ({mb_down:.1f} MB) • ⚡ {p.speed_mb_s:.1f} MB/s"
            )

    def _on_finished(self, success: bool, target_path: str, message: str):
        if success and target_path and os.path.exists(target_path):
            self.lbl_progress_status.setText(f"✔ Hoàn thành: {os.path.basename(target_path)}")
            is_folder = os.path.isdir(target_path)
            self.import_completed.emit(target_path, is_folder)
            self.accept()
        else:
            self.btn_download.setEnabled(True)
            self.txt_url.setEnabled(True)
            self.txt_output_dir.setEnabled(True)
            self.lbl_progress_status.setText(f"❌ {message}")
            QMessageBox.critical(self, "Lỗi Tải Google Drive", f"Không thể tải tệp từ Google Drive:\n{message}")

    def _on_cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)
        self.reject()
