"""
Giao diện Quản Lý Nguồn Tài Nguyên & Kho Đạo Cụ Cộng Đồng (Asset Hub Widget).
Bao gồm:
1. Kho Gói Đám Mây Tuyển Chọn (Tải 1-Click từ Cloud / Google Drive).
2. Quản lý Thư Mục Máy Tính Đã Nối (Linked External Folders - Không tốn ổ cứng).
3. Cài Đặt & Xuất Gói Định Dạng ResolveFlow (.rfpack).
"""

import os
from pathlib import Path
from typing import Optional, List, Dict, Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar, QScrollArea, QFrame, QFileDialog,
    QMessageBox, QGridLayout, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QDragEnterEvent, QDropEvent

from src.core.pack_manager import PackManager, PackManifest, COMMUNITY_PACKS_CATALOG
from src.core.asset_indexer import AssetIndexer
from src.ui.tabs.cloud_pack_worker import CloudPackInstallWorker
from src.ui.icons import get_vector_icon
from src.ui.theme import ThemeColors


class RfPackDropZone(QFrame):
    """Khu vực kéo thả tệp .rfpack trực quan."""
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedHeight(110)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_CARD};
                border: 2px dashed {ThemeColors.BORDER_DEFAULT};
                border-radius: 10px;
            }}
            QFrame:hover {{
                border-color: {ThemeColors.PRIMARY_HOVER};
                background-color: {ThemeColors.BG_CARD_ACTIVE};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        lbl_icon = QLabel()
        lbl_icon.setPixmap(get_vector_icon("download", ThemeColors.PRIMARY_HOVER, 28).pixmap(28, 28))
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_icon)

        self.lbl_text = QLabel("Kéo thả tệp .rfpack hoặc .zip vào đây để cài đặt tự động")
        self.lbl_text.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 12.5px; font-weight: 500;")
        self.lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_text)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                fn = url.toLocalFile().lower()
                if fn.endswith(".rfpack") or fn.endswith(".zip"):
                    event.acceptProposedAction()
                    self.setStyleSheet(f"""
                        QFrame {{
                            background-color: rgba(217, 79, 61, 0.16);
                            border: 1px dashed {ThemeColors.PRIMARY_HOVER};
                            border-radius: 6px;
                        }}
                    """)
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_CARD};
                border: 2px dashed {ThemeColors.BORDER_DEFAULT};
                border-radius: 10px;
            }}
        """)

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            fn = url.toLocalFile()
            if fn.lower().endswith(".rfpack") or fn.lower().endswith(".zip"):
                self.file_dropped.emit(fn)
                event.acceptProposedAction()
                self.dragLeaveEvent(None)
                return
        event.ignore()


class CloudPackCard(QFrame):
    """Thẻ hiển thị thông tin và nút tải của 1 gói Starter Pack."""
    install_requested = Signal(object)  # PackManifest
    uninstall_requested = Signal(object)  # PackManifest

    def __init__(self, manifest: PackManifest, parent=None):
        super().__init__(parent)
        self.manifest = manifest
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            CloudPackCard {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 8px;
                padding: 10px;
            }}
            CloudPackCard:hover {{
                border-color: {ThemeColors.BORDER_HOVER};
                background-color: {ThemeColors.BG_CARD_ACTIVE};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        from src.core.pack_manager import PackManager
        self.is_installed = PackManager.is_pack_installed(manifest.id)

        # Header: Tên gói & tác giả
        h_row = QHBoxLayout()
        lbl_title = QLabel(f"📦 {manifest.name}")
        lbl_title.setStyleSheet("color: #f4f4f5; font-size: 13px; font-weight: bold;")
        h_row.addWidget(lbl_title, stretch=1)

        self.lbl_installed_badge = QLabel("✔ ĐÃ CÀI ĐẶT")
        self.lbl_installed_badge.setStyleSheet("""
            background-color: #064e3b;
            color: #34d399;
            font-size: 10px;
            font-weight: bold;
            padding: 2px 6px;
            border-radius: 4px;
            border: 1px solid #10b981;
        """)
        h_row.addWidget(self.lbl_installed_badge)
        if not self.is_installed:
            self.lbl_installed_badge.hide()

        size_str = f"{manifest.size_mb:.1f} MB" if manifest.size_mb else "Cloud"
        lbl_size = QLabel(f"{size_str} · {manifest.item_count} items")
        lbl_size.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        h_row.addWidget(lbl_size)
        layout.addLayout(h_row)

        # Mô tả
        lbl_desc = QLabel(manifest.description)
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11.5px;")
        layout.addWidget(lbl_desc)

        # Thanh tiến trình tải (ẩn mặc định)
        self.prog_bar = QProgressBar()
        self.prog_bar.setFixedHeight(6)
        self.prog_bar.setTextVisible(False)
        self.prog_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {ThemeColors.BG_INPUT};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background-color: {ThemeColors.PRIMARY};
                border-radius: 3px;
            }}
        """)
        self.prog_bar.hide()
        layout.addWidget(self.prog_bar)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #f59e0b; font-size: 11px;")
        self.lbl_status.hide()
        layout.addWidget(self.lbl_status)

        # Action row
        act_row = QHBoxLayout()
        act_row.setSpacing(6)
        lbl_author = QLabel(f"Bởi: {manifest.author}")
        lbl_author.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 10.5px;")
        act_row.addWidget(lbl_author, stretch=1)

        self.btn_download = QPushButton()
        self.btn_download.setFixedHeight(28)
        self.btn_download.setCursor(Qt.PointingHandCursor)
        self.btn_download.clicked.connect(self._on_download_clicked)
        act_row.addWidget(self.btn_download)

        self.btn_uninstall = QPushButton("🗑 Xóa Gói")
        self.btn_uninstall.setFixedHeight(28)
        self.btn_uninstall.setCursor(Qt.PointingHandCursor)
        self.btn_uninstall.setToolTip(f"Xóa bỏ gói {manifest.name} khỏi máy để giải phóng dung lượng đĩa")
        self.btn_uninstall.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #f87171;
                font-size: 11px;
                font-weight: 600;
                border: 1px solid rgba(248, 113, 113, 0.4);
                border-radius: 6px;
                padding: 0 8px;
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.15);
                border-color: #ef4444;
                color: #fca5a5;
            }
        """)
        self.btn_uninstall.clicked.connect(self._on_uninstall_clicked)
        act_row.addWidget(self.btn_uninstall)

        self._update_download_button_state()
        layout.addLayout(act_row)

    def _on_download_clicked(self):
        if self.is_installed:
            reply = QMessageBox.question(
                self,
                "Cài đặt lại gói đạo cụ",
                f"Gói '{self.manifest.name}' đã được cài đặt trong máy tính.\n\nBạn có muốn tải lại và cài đặt ghi đè không?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.install_requested.emit(self.manifest)

    def _on_uninstall_clicked(self):
        reply = QMessageBox.question(
            self,
            "Xác nhận xóa gói đạo cụ",
            f"Bạn có chắc muốn xóa gói '{self.manifest.name}' khỏi máy tính?\n\nHành động này sẽ gỡ bỏ các tệp media của gói trong kho lưu trữ để giải phóng dung lượng ổ cứng.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.uninstall_requested.emit(self.manifest)

    def _update_download_button_state(self):
        if self.is_installed:
            self.lbl_installed_badge.show()
            self.btn_download.setText(" 🔄 Cài Lại")
            self.btn_download.setIcon(get_vector_icon("rotate", "#34d399", 14))
            self.btn_download.setStyleSheet(f"""
                QPushButton {{
                    background: {ThemeColors.BG_CARD};
                    color: #d1d5db;
                    font-size: 11.5px;
                    font-weight: 600;
                    border: 1px solid #10b981;
                    border-radius: 6px;
                    padding: 0 10px;
                }}
                QPushButton:hover {{
                    background: {ThemeColors.BG_INPUT};
                    color: #ffffff;
                }}
            """)
            self.btn_uninstall.show()
        else:
            self.lbl_installed_badge.hide()
            self.btn_download.setText(" Tải && Cài Đặt (1-Click)")
            self.btn_download.setIcon(get_vector_icon("download", "#ffffff", 14))
            self.btn_download.setStyleSheet(f"""
                QPushButton {{
                    background: {ThemeColors.PRIMARY};
                    color: #ffffff;
                    font-size: 11.5px;
                    font-weight: 600;
                    border: 1px solid {ThemeColors.PRIMARY_HOVER};
                    border-radius: 6px;
                    padding: 0 10px;
                }}
                QPushButton:hover {{
                    background: {ThemeColors.PRIMARY_HOVER};
                }}
                QPushButton:disabled {{
                    background: {ThemeColors.BG_INPUT};
                    color: {ThemeColors.TEXT_MUTED};
                    border-color: {ThemeColors.BORDER_DEFAULT};
                }}
            """)
            self.btn_uninstall.hide()

    def set_installed(self, installed: bool = True):
        self.is_installed = installed
        self._update_download_button_state()

    def set_downloading(self, percent: float, message: str):
        self.prog_bar.show()
        self.prog_bar.setValue(int(percent))
        self.lbl_status.show()
        self.lbl_status.setText(message)
        self.btn_download.setEnabled(False)
        self.btn_download.setText(" Đang xử lý...")

    def set_finished(self, success: bool, message: str):
        self.prog_bar.hide()
        self.lbl_status.show()
        if success:
            self.lbl_status.setStyleSheet("color: #34d399; font-size: 11px;")
            self.lbl_status.setText(f"✓ {message}")
            self.set_installed(True)
            self.btn_download.setEnabled(True)
        else:
            self.lbl_status.setStyleSheet("color: #f87171; font-size: 11px;")
            self.lbl_status.setText(f"✕ {message}")
            self.btn_download.setText("Thử lại")
            self.btn_download.setEnabled(True)


class AssetHubWidget(QWidget):
    """
    Giao diện trung tâm quản lý nguồn tài nguyên:
    - Kho Gói Đám Mây (Cloud Packs)
    - Thư Mục Máy Tính Đã Nối (Linked External Folders)
    - Nhập / Xuất gói .rfpack
    """
    assets_changed = Signal()

    def __init__(self, indexer: Optional[AssetIndexer] = None, parent=None):
        super().__init__(parent)
        self.indexer = indexer or AssetIndexer()
        self._active_worker: Optional[CloudPackInstallWorker] = None
        self._pack_cards: Dict[str, CloudPackCard] = {}

        self._init_ui()
        self._library_refreshed()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # Scroll area bao bọc toàn bộ nội dung
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        # ==========================================
        # PHẦN 1: KHO GÓI ĐÁM MÂY TUYỂN CHỌN
        # ==========================================
        sec1_header = QHBoxLayout()
        lbl_sec1 = QLabel("☁️ Kho Gói Đạo Cụ Đám Mây Tuyển Chọn (Tải 1-Click)")
        lbl_sec1.setStyleSheet("color: #f4f4f5; font-size: 14px; font-weight: bold;")
        sec1_header.addWidget(lbl_sec1)
        sec1_header.addStretch(1)
        layout.addLayout(sec1_header)

        # Grid hiển thị các gói starter pack
        grid_packs = QGridLayout()
        grid_packs.setSpacing(12)

        for idx, manifest in enumerate(COMMUNITY_PACKS_CATALOG):
            card = CloudPackCard(manifest)
            card.install_requested.connect(self._on_install_pack_requested)
            card.uninstall_requested.connect(self._on_uninstall_pack_requested)
            self._pack_cards[manifest.id] = card
            row = idx // 2
            col = idx % 2
            grid_packs.addWidget(card, row, col)

        layout.addLayout(grid_packs)

        # Ô nhập link Google Drive tùy chọn
        drive_box = QFrame()
        drive_box.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 8px;
                padding: 6px;
            }}
        """)
        drive_layout = QHBoxLayout(drive_box)
        drive_layout.setContentsMargins(10, 8, 10, 8)
        drive_layout.setSpacing(10)

        lbl_drive_icon = QLabel()
        lbl_drive_icon.setPixmap(get_vector_icon("cloud", "#34d399", 18).pixmap(18, 18))
        drive_layout.addWidget(lbl_drive_icon)

        self.txt_drive_url = QLineEdit()
        self.txt_drive_url.setPlaceholderText("Dán liên kết GitHub Releases / Google Drive gói .rfpack hoặc thư mục meme/sfx tại đây...")
        self.txt_drive_url.setStyleSheet(f"""
            QLineEdit {{
                background: {ThemeColors.BG_INPUT};
                color: #f4f4f5;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {ThemeColors.PRIMARY_HOVER};
            }}
        """)
        drive_layout.addWidget(self.txt_drive_url, stretch=1)

        self.btn_download_drive = QPushButton(" Tải && Cài Đặt")
        self.btn_download_drive.setIcon(get_vector_icon("download", "#ffffff", 14))
        self.btn_download_drive.setFixedHeight(30)
        self.btn_download_drive.setCursor(Qt.PointingHandCursor)
        self.btn_download_drive.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.PRIMARY};
                color: #ffffff;
                font-weight: 600;
                border-radius: 6px;
                padding: 0 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {ThemeColors.PRIMARY_HOVER};
            }}
        """)
        self.btn_download_drive.clicked.connect(self._on_custom_drive_download)
        drive_layout.addWidget(self.btn_download_drive)

        layout.addWidget(drive_box)

        # ==========================================
        # PHẦN 2: THƯ MỤC MÁY TÍNH ĐÃ NỐI (LINKED FOLDERS)
        # ==========================================
        sec2_header = QHBoxLayout()
        lbl_sec2 = QLabel("📁 Thư Mục Ổ Cứng Ngoài (Không tốn thêm 1MB dung lượng)")
        lbl_sec2.setStyleSheet("color: #f4f4f5; font-size: 14px; font-weight: bold;")
        sec2_header.addWidget(lbl_sec2)
        sec2_header.addStretch(1)

        btn_add_folder = QPushButton(" ➕ Thêm Thư Mục Máy Tính...")
        btn_add_folder.setIcon(get_vector_icon("folder", "#fbbf24", 14))
        btn_add_folder.setCursor(Qt.PointingHandCursor)
        btn_add_folder.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.BG_INPUT};
                color: #f4f4f5;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 11.5px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {ThemeColors.BG_CARD_ACTIVE};
                border-color: {ThemeColors.PRIMARY_HOVER};
            }}
        """)
        btn_add_folder.clicked.connect(self._on_add_folder_clicked)
        sec2_header.addWidget(btn_add_folder)

        btn_rescan_all = QPushButton(" 🔄 Quét Lại")
        btn_rescan_all.setCursor(Qt.PointingHandCursor)
        btn_rescan_all.setStyleSheet(btn_add_folder.styleSheet())
        btn_rescan_all.clicked.connect(self._refresh_linked_folders_view)
        sec2_header.addWidget(btn_rescan_all)

        layout.addLayout(sec2_header)

        # Khung danh sách các thư mục ngoài
        self.folder_list_container = QVBoxLayout()
        self.folder_list_container.setSpacing(6)
        layout.addLayout(self.folder_list_container)

        self._render_linked_folders()

        # ==========================================
        # PHẦN 3: GÓI ĐẠO CỤ (.RFPACK) CỘNG ĐỒNG
        # ==========================================
        sec3_header = QHBoxLayout()
        lbl_sec3 = QLabel("📦 Cài Đặt & Chia Sẻ Gói Định Dạng ResolveFlow (.rfpack)")
        lbl_sec3.setStyleSheet("color: #f4f4f5; font-size: 14px; font-weight: bold;")
        sec3_header.addWidget(lbl_sec3)
        sec3_header.addStretch(1)
        layout.addLayout(sec3_header)

        # Drop zone
        self.drop_zone = RfPackDropZone()
        self.drop_zone.file_dropped.connect(self._install_local_pack_file)
        layout.addWidget(self.drop_zone)

        # Buttons row: Cài đặt file thủ công hoặc xuất pack
        rf_btn_row = QHBoxLayout()
        btn_pick_file = QPushButton(" 📥 Chọn Tệp .rfpack / .zip Từ Máy...")
        btn_pick_file.setIcon(get_vector_icon("download", "#ffffff", 14))
        btn_pick_file.setFixedHeight(32)
        btn_pick_file.setCursor(Qt.PointingHandCursor)
        btn_pick_file.setStyleSheet(btn_add_folder.styleSheet())
        btn_pick_file.clicked.connect(self._on_pick_pack_file)
        rf_btn_row.addWidget(btn_pick_file)

        btn_export_pack = QPushButton(" 📤 Xuất Bộ Sưu Tập Của Tôi Ra .rfpack...")
        btn_export_pack.setFixedHeight(32)
        btn_export_pack.setCursor(Qt.PointingHandCursor)
        btn_export_pack.setStyleSheet(btn_add_folder.styleSheet())
        btn_export_pack.clicked.connect(self._on_export_pack_clicked)
        rf_btn_row.addWidget(btn_export_pack)

        rf_btn_row.addStretch(1)
        layout.addLayout(rf_btn_row)

        layout.addStretch(1)
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    def _render_linked_folders(self):
        """Vẽ danh sách các thư mục ổ cứng ngoài đang kết nối."""
        while self.folder_list_container.count():
            item = self.folder_list_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        from src.core.storage_manager import StorageManager
        managed_links = StorageManager().links()
        folders = list(dict.fromkeys(self.indexer.get_linked_folders() + [link["path"] for link in managed_links]))
        if not folders:
            lbl_empty = QLabel("Chưa liên kết thư mục ngoài nào. Bấm 'Thêm Thư Mục Máy Tính' để nạp kho meme/SFX có sẵn của bạn.")
            lbl_empty.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11.5px; font-style: italic; padding: 6px;")
            self.folder_list_container.addWidget(lbl_empty)
            return

        for folder_path in folders:
            row = QFrame()
            row.setStyleSheet(f"""
                QFrame {{
                    background-color: {ThemeColors.BG_CARD};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 6px;
                }}
            """)
            r_layout = QHBoxLayout(row)
            r_layout.setContentsMargins(10, 6, 10, 6)
            r_layout.setSpacing(10)

            lbl_ficon = QLabel()
            lbl_ficon.setPixmap(get_vector_icon("folder", "#fbbf24", 16).pixmap(16, 16))
            r_layout.addWidget(lbl_ficon)

            lbl_p = QLabel(folder_path)
            lbl_p.setStyleSheet("color: #ededf0; font-size: 12px; font-family: monospace;")
            r_layout.addWidget(lbl_p, stretch=1)

            btn_del = QPushButton("Gỡ bỏ")
            btn_del.setCursor(Qt.PointingHandCursor)
            btn_del.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #f87171;
                    border: 1px solid transparent;
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background: rgba(239, 68, 68, 0.15);
                    border-color: #ef4444;
                }
            """)
            btn_del.clicked.connect(lambda checked=False, p=folder_path: self._on_remove_folder(p))
            r_layout.addWidget(btn_del)

            self.folder_list_container.addWidget(row)

    def _on_add_folder_clicked(self):
        self._open_storage_settings()

    def _on_remove_folder(self, folder_path: str):
        reply = QMessageBox.question(
            self,
            "Xác nhận",
            f"Bạn có chắc muốn gỡ liên kết thư mục này khỏi kho đạo cụ?\n{folder_path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            from src.core.storage_manager import StorageManager
            storage = StorageManager()
            try:
                for link in storage.links():
                    if link["path"] == folder_path: storage.unlink_folder(link["id"])
            except Exception as exc:
                QMessageBox.warning(self, "Không gỡ được liên kết", str(exc)); return
            self.indexer.remove_linked_folder(folder_path)
            self._render_linked_folders()
            self.assets_changed.emit()

    def _refresh_linked_folders_view(self):
        self.indexer.clear_cache()
        self._render_linked_folders()
        self.assets_changed.emit()
        QMessageBox.information(self, "Thông báo", "Đã quét lại toàn bộ kho đạo cụ!")

    def _open_storage_settings(self):
        from src.ui.widgets.pack_ingest_wizard import StorageSettingsDialog
        dialog = StorageSettingsDialog(parent=self)
        dialog.library_changed.connect(self._library_refreshed)
        dialog.exec()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_cards()

    def refresh_cards(self):
        """Cập nhật trạng thái đã cài đặt của toàn bộ các thẻ gói trên UI."""
        try:
            from src.core.pack_manager import PackManager
            for pid, card in self._pack_cards.items():
                card.set_installed(PackManager.is_pack_installed(pid))
        except Exception:
            pass

    def _library_refreshed(self):
        self.indexer.clear_cache()
        self.assets_changed.emit()
        self._render_linked_folders()
        self.refresh_cards()

    def _review_pack(self, source=None, url=None, auto_prepare=False, manifest: Optional[PackManifest] = None):
        from src.ui.widgets.pack_ingest_wizard import PackIngestWizard
        pack_id = manifest.id if manifest else None
        pack_name = manifest.name if manifest else None
        try:
            dialog = PackIngestWizard(
                parent=self, source=source, url=url, auto_prepare=auto_prepare,
                pack_id=pack_id, pack_name=pack_name
            )
        except TypeError:
            try:
                dialog = PackIngestWizard(parent=self, source=source, url=url, auto_prepare=auto_prepare)
            except TypeError:
                dialog = PackIngestWizard(parent=self, source=source, url=url)
        dialog.library_changed.connect(self._library_refreshed)
        committed_payload = []

        def _on_committed(meta):
            committed_payload.append(meta if isinstance(meta, dict) else {})
            pid = (meta.get("pack_id") if isinstance(meta, dict) else None) or pack_id
            if pid and pid in self._pack_cards:
                self._pack_cards[pid].set_installed(True)
            elif manifest and manifest.id in self._pack_cards:
                self._pack_cards[manifest.id].set_installed(True)

        dialog.pack_committed.connect(_on_committed)
        dialog.exec()
        if committed_payload and committed_payload[0].get("snapshot_synced") is False:
            QMessageBox.warning(
                self,
                "Thông báo",
                "Gói đã nhập vào kho; bộ đệm offline chưa đồng bộ."
            )

    def _on_install_pack_requested(self, manifest: PackManifest):
        self._review_pack(url=manifest.download_url, auto_prepare=True, manifest=manifest)

    def _on_uninstall_pack_requested(self, manifest: PackManifest):
        from src.core.pack_manager import PackManager
        from src.core.storage_manager import StorageManager

        # 1. Gỡ trong PackManager
        PackManager.uninstall_pack(manifest.id)

        # 2. Gỡ trong StorageManager nếu online
        try:
            sm = StorageManager()
            if sm.online:
                sm.remove_pack(manifest.id)
        except Exception as exc:
            pass

        # 3. Cập nhật card
        if manifest.id in self._pack_cards:
            self._pack_cards[manifest.id].set_installed(False)
            self._pack_cards[manifest.id].lbl_status.setText("Đã gỡ bỏ gói khỏi kho.")
            self._pack_cards[manifest.id].lbl_status.show()

        # 4. Quét lại kho đạo cụ
        self._library_refreshed()
        QMessageBox.information(
            self,
            "Đã gỡ bỏ gói",
            f"Đã gỡ bỏ gói '{manifest.name}' khỏi kho lưu trữ thành công!"
        )

    def _on_custom_drive_download(self):
        url = self.txt_drive_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Liên kết", "Vui lòng nhập liên kết Google Drive.")
            return
        self._review_pack(url=url, auto_prepare=True)

    def _on_custom_drive_finished(self, success: bool, message: str):
        self.btn_download_drive.setEnabled(True)
        self.btn_download_drive.setText(" Tải && Cài Đặt")
        if success:
            self.txt_drive_url.clear()
            self.indexer.clear_cache()
            self.assets_changed.emit()
            QMessageBox.information(self, "Hoàn tất", f"Cài đặt thành công!\n{message}")
        else:
            QMessageBox.critical(self, "Lỗi", message)

    def _on_install_worker_finished(self, success: bool, message: str, counts: dict, card: Optional[CloudPackCard]):
        if card:
            card.set_finished(success, message)
        if success:
            self.indexer.clear_cache()
            self.assets_changed.emit()

    def _install_local_pack_file(self, filepath: str):
        self._review_pack(source=filepath, auto_prepare=True)

    def _on_pick_pack_file(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn tệp gói ResolveFlow Pack",
            "",
            "ResolveFlow Pack (*.rfpack *.zip)"
        )
        if chosen:
            self._install_local_pack_file(chosen)

    def _on_export_pack_clicked(self):
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Lưu gói ResolveFlow Pack",
            "My_Community_Pack.rfpack",
            "ResolveFlow Pack (*.rfpack)"
        )
        if not save_path:
            return

        try:
            # Gom tài nguyên hiện có để đóng gói mẫu
            all_assets = self.indexer.scan_all_assets()
            cat_map: Dict[str, List[str]] = {}
            for cat, items in all_assets.items():
                cat_clean = cat.split("_")[0]
                if cat_clean not in cat_map:
                    cat_map[cat_clean] = []
                for it in items[:15]:  # Mẫu tối đa 15 item mỗi loại
                    p = it.get("path") or it.get("file_path")
                    if p and Path(p).is_file():
                        cat_map[cat_clean].append(p)

            manifest = PackManifest(
                id=Path(save_path).stem.lower().replace(" ", "_"),
                name=Path(save_path).stem.replace("_", " ").title(),
                version="1.0.0",
                author="ChunDVC Creator",
                description="Bộ sưu tập đạo cụ chia sẻ bởi cộng đồng ResolveFlow."
            )
            out = PackManager.create_pack(save_path, manifest, cat_map)
            QMessageBox.information(
                self,
                "Xuất gói thành công",
                f"Đã xuất thành công gói:\n{out}\n\nBạn có thể gửi tệp này cho cộng đồng hoặc đăng lên nhóm mạng xã hội!"
            )
        except Exception as e:
            QMessageBox.critical(self, "Lỗi xuất gói", f"Không thể xuất gói: {e}")
