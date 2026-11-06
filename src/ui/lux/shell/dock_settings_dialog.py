"""
src/ui/lux/shell/dock_settings_dialog.py
Hộp thoại Cài Đặt Thanh Dock (Dock Settings Dialog):
Cho phép người dùng tùy biến:
- Vị trí thanh Dock: Bên phải (mặc định) hoặc Bên trái màn hình.
- Tùy chọn tự động mở ChunDVC Director (Trạm AI) khi khởi chạy.
- Tùy chọn ghim thanh Dock mặc định.
- Xem danh sách phím tắt toàn cục.
Tự động lưu vào QSettings để duy trì trạng thái khi khởi động lại.
"""

from typing import Optional
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QButtonGroup, QCheckBox,
    QFrame, QGridLayout, QSlider, QScrollArea
)
from PySide6.QtCore import Qt, Signal as pyqtSignal, QSettings, QSize
from src.ui.icons import get_vector_icon
from src.ui.theme import ThemeColors
from src.services.multi_screen_service import get_multi_screen_service


class DockSettingsDialog(QDialog):
    """
    Cửa sổ Cài Đặt Giao Diện, Vị Trí Thanh Dock & Đa Màn Hình.
    """
    dock_edge_changed = pyqtSignal(str)          # 'left' | 'right'
    auto_director_changed = pyqtSignal(bool)
    pin_default_changed = pyqtSignal(bool)
    auto_collapse_changed = pyqtSignal(bool)
    hover_audio_changed = pyqtSignal(bool)
    hover_volume_changed = pyqtSignal(float)
    auto_secondary_studio_changed = pyqtSignal(bool)
    dock_screen_changed = pyqtSignal(int)

    def __init__(self, current_edge: str = "right", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Cài Đặt Thanh Dock & Hiển Thị")
        self.setFixedSize(510, 640)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self._current_edge = current_edge if current_edge in ("left", "right") else "right"
        self._settings = QSettings("ChunDVC", "ResolveFlow")
        self._multi_screen_service = get_multi_screen_service()

        self._init_ui()
        self._load_preferences()

    def _init_ui(self):
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {ThemeColors.BG_CANVAS};
                color: {ThemeColors.TEXT_PRIMARY};
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }}
            QLabel {{
                color: {ThemeColors.TEXT_PRIMARY};
            }}
            QFrame#card_frame {{
                background-color: {ThemeColors.BG_MAIN};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 8px;
            }}
            QRadioButton {{
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 13px;
                spacing: 8px;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 1px solid {ThemeColors.BORDER_HOVER};
                background: {ThemeColors.BG_INPUT};
            }}
            QRadioButton::indicator:checked {{
                border: 2px solid {ThemeColors.PRIMARY};
                background: {ThemeColors.PRIMARY};
            }}
            QCheckBox {{
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 13px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid {ThemeColors.BORDER_HOVER};
                background: {ThemeColors.BG_INPUT};
            }}
            QCheckBox::indicator:checked {{
                border: 1px solid {ThemeColors.PRIMARY};
                background: {ThemeColors.PRIMARY};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # 1. Header Bar
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)

        lbl_icon = QLabel()
        lbl_icon.setPixmap(get_vector_icon("settings", ThemeColors.PRIMARY_HOVER, 24).pixmap(24, 24))
        header_layout.addWidget(lbl_icon)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        lbl_title = QLabel("Cài Đặt Thanh Công Cụ (Dock Settings)")
        lbl_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        title_vbox.addWidget(lbl_title)

        lbl_subtitle = QLabel("Tùy biến vị trí dán mép màn hình và hành vi hiển thị")
        lbl_subtitle.setStyleSheet(f"font-size: 11.5px; color: {ThemeColors.TEXT_MUTED};")
        title_vbox.addWidget(lbl_subtitle)
        header_layout.addLayout(title_vbox)
        header_layout.addStretch(1)

        layout.addLayout(header_layout)

        # 2. Section 1: Vị trí hiển thị trên màn hình
        sec1_frame = QFrame()
        sec1_frame.setObjectName("card_frame")
        sec1_layout = QVBoxLayout(sec1_frame)
        sec1_layout.setContentsMargins(14, 12, 14, 14)
        sec1_layout.setSpacing(10)

        lbl_sec1 = QLabel("📍 VỊ TRÍ THANH DOCK (SCREEN EDGE)")
        lbl_sec1.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {ThemeColors.TEXT_ACCENT}; letter-spacing: 0.5px;")
        sec1_layout.addWidget(lbl_sec1)

        lbl_sec1_desc = QLabel("Chọn cạnh màn hình mà thanh công cụ sẽ neo và mở rộng ra:")
        lbl_sec1_desc.setStyleSheet(f"font-size: 12px; color: {ThemeColors.TEXT_SECONDARY};")
        sec1_layout.addWidget(lbl_sec1_desc)

        edge_btn_layout = QHBoxLayout()
        edge_btn_layout.setSpacing(20)

        self.btn_group_edge = QButtonGroup(self)
        self.rad_right = QRadioButton("Bên phải (Right - Mặc định)")
        self.rad_right.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rad_left = QRadioButton("Bên trái (Left)")
        self.rad_left.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_group_edge.addButton(self.rad_right, 1)
        self.btn_group_edge.addButton(self.rad_left, 2)

        if self._current_edge == "left":
            self.rad_left.setChecked(True)
        else:
            self.rad_right.setChecked(True)

        self.rad_right.toggled.connect(self._on_edge_radio_toggled)
        self.rad_left.toggled.connect(self._on_edge_radio_toggled)

        edge_btn_layout.addWidget(self.rad_right)
        edge_btn_layout.addWidget(self.rad_left)
        edge_btn_layout.addStretch(1)
        sec1_layout.addLayout(edge_btn_layout)

        layout.addWidget(sec1_frame)

        # 3. Section 2: Khởi động & Hành vi
        sec2_frame = QFrame()
        sec2_frame.setObjectName("card_frame")
        sec2_layout = QVBoxLayout(sec2_frame)
        sec2_layout.setContentsMargins(14, 12, 14, 14)
        sec2_layout.setSpacing(10)

        lbl_sec2 = QLabel("⚙️ HÀNH VI & KHỞI CHẠY (BEHAVIOR)")
        lbl_sec2.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {ThemeColors.TEXT_ACCENT}; letter-spacing: 0.5px;")
        sec2_layout.addWidget(lbl_sec2)

        self.chk_auto_director = QCheckBox("Tự động mở ChunDVC Director (Trạm AI) khi khởi động")
        self.chk_auto_director.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_auto_director.toggled.connect(self._on_auto_director_toggled)
        sec2_layout.addWidget(self.chk_auto_director)

        self.chk_pin_default = QCheckBox("Ghim thanh Dock mặc định (Không tự thu gọn khi mất tiêu điểm)")
        self.chk_pin_default.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_pin_default.toggled.connect(self._on_pin_default_toggled)
        sec2_layout.addWidget(self.chk_pin_default)

        self.chk_auto_collapse = QCheckBox("Tự thu gọn khi rời Dock (về Mini Strip 28px)")
        self.chk_auto_collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_auto_collapse.toggled.connect(self._on_auto_collapse_toggled)
        sec2_layout.addWidget(self.chk_auto_collapse)

        self.chk_hover_audio = QCheckBox("Bật âm thanh khi lướt chuột qua Meme / Video / SFX (Hover Audio)")
        self.chk_hover_audio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_hover_audio.toggled.connect(self._on_hover_audio_toggled)
        sec2_layout.addWidget(self.chk_hover_audio)

        vol_layout = QHBoxLayout()
        vol_layout.setSpacing(10)
        lbl_vol_title = QLabel("Âm lượng lướt xem:")
        lbl_vol_title.setStyleSheet(f"font-size: 12px; color: {ThemeColors.TEXT_SECONDARY};")
        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(75)
        self.slider_volume.setFixedHeight(20)
        self.slider_volume.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: #27272a;
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ThemeColors.PRIMARY};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: #ffffff;
                width: 14px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 7px;
            }}
        """)
        self.lbl_vol_val = QLabel("75%")
        self.lbl_vol_val.setFixedWidth(36)
        self.lbl_vol_val.setStyleSheet(f"font-size: 11.5px; color: {ThemeColors.TEXT_MUTED}; font-weight: 600;")
        self.slider_volume.valueChanged.connect(self._on_volume_slider_changed)

        vol_layout.addWidget(lbl_vol_title)
        vol_layout.addWidget(self.slider_volume, stretch=1)
        vol_layout.addWidget(self.lbl_vol_val)
        sec2_layout.addLayout(vol_layout)

        # 4. Section 3: Cấu hình Đa Màn Hình (Dual Monitor Workflow)
        sec3_monitor_frame = QFrame()
        sec3_monitor_frame.setObjectName("card_frame")
        sec3_mon_layout = QVBoxLayout(sec3_monitor_frame)
        sec3_mon_layout.setContentsMargins(14, 12, 14, 14)
        sec3_mon_layout.setSpacing(10)

        lbl_sec3_mon = QLabel("🖥️ THIẾT LẬP ĐA MÀN HÌNH (DUAL MONITOR)")
        lbl_sec3_mon.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {ThemeColors.TEXT_ACCENT}; letter-spacing: 0.5px;")
        sec3_mon_layout.addWidget(lbl_sec3_mon)

        screens_info = self._multi_screen_service.get_screens_info()
        count = len(screens_info)

        if count >= 2:
            status_text = f"✓ Phát hiện <b>{count} màn hình</b> đang hoạt động:"
        else:
            status_text = f"ℹ️ Phát hiện <b>1 màn hình</b> đang kết nối:"

        lbl_screens_status = QLabel(status_text)
        lbl_screens_status.setStyleSheet("font-size: 12px; color: #a1a1aa;")
        sec3_mon_layout.addWidget(lbl_screens_status)

        screens_box = QVBoxLayout()
        screens_box.setSpacing(6)
        for s in screens_info:
            role_desc = "Màn hình Chính (DaVinci Resolve Timeline/Viewer)" if s["is_primary"] else "Màn hình Phụ (Kho Đạo Cụ & AI Director)"
            badge_color = "#7c3aed" if s["is_primary"] else "#0891b2"
            s_label = QLabel(
                f"<span style='background:{badge_color}; color:#ffffff; padding:2px 7px; border-radius:4px; font-weight:700; font-size:10px;'>MÀN {s['index'] + 1}</span> "
                f"<b>{s['name']}</b> ({s['width']}x{s['height']}) · <span style='color:#a1a1aa; font-size:11px;'>{role_desc}</span>"
            )
            s_label.setStyleSheet("font-size: 12px; color: #ededf0;")
            screens_box.addWidget(s_label)
        sec3_mon_layout.addLayout(screens_box)

        # Chọn màn hình neo cố định cho Thanh Dock (tránh tự nhảy màn hình)
        dock_scr_layout = QHBoxLayout()
        dock_scr_layout.setSpacing(12)
        lbl_dock_scr = QLabel("Màn hình neo Thanh Dock:")
        lbl_dock_scr.setStyleSheet(f"font-size: 12px; color: {ThemeColors.TEXT_SECONDARY};")
        dock_scr_layout.addWidget(lbl_dock_scr)

        self.btn_group_dock_scr = QButtonGroup(self)
        self.rad_dock_scr1 = QRadioButton("Màn 1 (Chính - Mặc định)")
        self.rad_dock_scr1.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rad_dock_scr2 = QRadioButton("Màn 2 (Phụ)")
        self.rad_dock_scr2.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rad_dock_scr2.setEnabled(count >= 2)

        self.btn_group_dock_scr.addButton(self.rad_dock_scr1, 0)
        self.btn_group_dock_scr.addButton(self.rad_dock_scr2, 1)

        self.rad_dock_scr1.toggled.connect(self._on_dock_screen_radio_toggled)
        self.rad_dock_scr2.toggled.connect(self._on_dock_screen_radio_toggled)

        dock_scr_layout.addWidget(self.rad_dock_scr1)
        dock_scr_layout.addWidget(self.rad_dock_scr2)
        dock_scr_layout.addStretch(1)
        sec3_mon_layout.addLayout(dock_scr_layout)

        self.chk_auto_secondary = QCheckBox("Tự động mở Kho Đạo Cụ (Studio) trên màn hình phụ (Màn 2)")
        self.chk_auto_secondary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_auto_secondary.setEnabled(count >= 2)
        if count < 2:
            self.chk_auto_secondary.setToolTip("Cần kết nối tối thiểu 2 màn hình để kích hoạt tính năng này.")
        else:
            self.chk_auto_secondary.setToolTip("Dành trọn Màn hình 1 cho DaVinci Resolve, mở toàn màn hình Kho Đạo Cụ trên Màn hình 2")
        self.chk_auto_secondary.toggled.connect(self._on_auto_secondary_toggled)
        sec3_mon_layout.addWidget(self.chk_auto_secondary)

        lbl_sec3_hint = QLabel(
            "💡 Mẹo: Khi dùng 2 màn hình, bạn có thể bấm nút <b>[🖥️ Sang Màn 2]</b> "
            "trên thanh tiêu đề Kho Đạo Cụ để chuyển qua lại tức thì."
        )
        lbl_sec3_hint.setStyleSheet(f"font-size: 11px; color: {ThemeColors.TEXT_MUTED};")
        lbl_sec3_hint.setWordWrap(True)
        sec3_mon_layout.addWidget(lbl_sec3_hint)

        # 5. Section 4: Phím tắt nhanh
        sec4_frame = QFrame()
        sec4_frame.setObjectName("card_frame")
        sec4_layout = QVBoxLayout(sec4_frame)
        sec4_layout.setContentsMargins(14, 12, 14, 14)
        sec4_layout.setSpacing(8)

        lbl_sec4 = QLabel("⌨️ PHÍM TẮT NHANH (GLOBAL HOTKEYS)")
        lbl_sec4.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {ThemeColors.TEXT_ACCENT}; letter-spacing: 0.5px;")
        sec4_layout.addWidget(lbl_sec4)

        grid = QGridLayout()
        grid.setSpacing(6)

        hotkeys = [
            ("Ctrl + Alt + D", "Bật / thu gọn thanh Dock (Mini Strip 28px)"),
            ("Ctrl + Alt + Space", "Mở nhanh Command Palette HUD"),
            ("Ctrl + Alt + K", "Mở Palette ở chế độ gõ lệnh"),
            ("Esc", "Thu gọn thanh Dock về mép màn hình"),
            ("Tab", "Mở rộng Dock / Hiệu chỉnh thông số"),
        ]
        for row, (key, desc) in enumerate(hotkeys):
            lbl_k = QLabel(key)
            lbl_k.setStyleSheet("""
                background: #27272a;
                color: #c4b5fd;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 6px;
                font-family: monospace;
                font-size: 11px;
                font-weight: bold;
            """)
            lbl_d = QLabel(desc)
            lbl_d.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11.5px;")
            grid.addWidget(lbl_k, row, 0)
            grid.addWidget(lbl_d, row, 1)

        sec4_layout.addLayout(grid)

        # Cuộn mượt mà các Card
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: #18181b;
                width: 6px;
                border-radius: 3px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #3f3f46;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #71717a;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(0, 0, 4, 0)
        content_layout.setSpacing(14)

        content_layout.addWidget(sec1_frame)
        content_layout.addWidget(sec2_frame)
        content_layout.addWidget(sec3_monitor_frame)
        content_layout.addWidget(sec4_frame)
        content_layout.addStretch(1)

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, stretch=1)

        # 6. Bottom Buttons (Reset Defaults, Close)
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)

        self.btn_reset = QPushButton("Khôi phục mặc định")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {ThemeColors.TEXT_MUTED};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {ThemeColors.BG_INPUT};
                color: {ThemeColors.TEXT_PRIMARY};
            }}
        """)
        self.btn_reset.clicked.connect(self._on_reset_defaults)
        btn_box.addWidget(self.btn_reset)

        btn_box.addStretch(1)

        self.btn_close = QPushButton("Hoàn tất")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.PRIMARY};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 20px;
                font-size: 12.5px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {ThemeColors.PRIMARY_HOVER};
            }}
        """)
        self.btn_close.clicked.connect(self.accept)
        btn_box.addWidget(self.btn_close)

        layout.addLayout(btn_box)

    def _load_preferences(self):
        edge = self._settings.value("dock_edge", self._current_edge)
        if edge == "left":
            self.rad_left.setChecked(True)
        else:
            self.rad_right.setChecked(True)

        auto_dir = self._settings.value("auto_open_director", True, type=bool)
        self.chk_auto_director.setChecked(auto_dir)

        pin_def = self._settings.value("pinned_by_default", False, type=bool)
        self.chk_pin_default.setChecked(pin_def)

        auto_collapse = self._settings.value("auto_collapse_on_leave", False, type=bool)
        self.chk_auto_collapse.setChecked(auto_collapse)

        hover_aud = self._settings.value("library/hover_audio_enabled", True, type=bool)
        self.chk_hover_audio.setChecked(hover_aud)

        hover_vol = int(float(self._settings.value("library/hover_audio_volume", 0.75)) * 100)
        self.slider_volume.setValue(hover_vol)
        self.lbl_vol_val.setText(f"{hover_vol}%")

        auto_sec = self._multi_screen_service.is_auto_open_studio_on_secondary()
        self.chk_auto_secondary.setChecked(auto_sec)

        pref_dock_scr = self._multi_screen_service.get_preferred_dock_screen()
        if pref_dock_scr == 1 and hasattr(self, "rad_dock_scr2"):
            self.rad_dock_scr2.setChecked(True)
        elif hasattr(self, "rad_dock_scr1"):
            self.rad_dock_scr1.setChecked(True)

    def _on_edge_radio_toggled(self):
        new_edge = "left" if self.rad_left.isChecked() else "right"
        if new_edge != self._current_edge:
            self._current_edge = new_edge
            self._settings.setValue("dock_edge", new_edge)
            self.dock_edge_changed.emit(new_edge)

    def _on_dock_screen_radio_toggled(self):
        new_scr = 1 if (hasattr(self, "rad_dock_scr2") and self.rad_dock_scr2.isChecked()) else 0
        self._multi_screen_service.set_preferred_dock_screen(new_scr)
        self.dock_screen_changed.emit(new_scr)

    def _on_auto_director_toggled(self, checked: bool):
        self._settings.setValue("auto_open_director", checked)
        self.auto_director_changed.emit(checked)

    def _on_pin_default_toggled(self, checked: bool):
        self._settings.setValue("pinned_by_default", checked)
        self.pin_default_changed.emit(checked)

    def _on_auto_collapse_toggled(self, checked: bool):
        self._settings.setValue("auto_collapse_on_leave", checked)
        self.auto_collapse_changed.emit(checked)

    def _on_hover_audio_toggled(self, checked: bool):
        self._settings.setValue("library/hover_audio_enabled", checked)
        self.hover_audio_changed.emit(checked)

    def _on_volume_slider_changed(self, val: int):
        self.lbl_vol_val.setText(f"{val}%")
        vol_float = val / 100.0
        self._settings.setValue("library/hover_audio_volume", vol_float)
        self.hover_volume_changed.emit(vol_float)

    def _on_auto_secondary_toggled(self, checked: bool):
        self._multi_screen_service.set_auto_open_studio_on_secondary(checked)
        self.auto_secondary_studio_changed.emit(checked)

    def _on_reset_defaults(self):
        self.rad_right.setChecked(True)
        self.chk_auto_director.setChecked(True)
        self.chk_pin_default.setChecked(False)
        self.chk_hover_audio.setChecked(True)
        self.slider_volume.setValue(75)
        self._multi_screen_service.set_auto_open_studio_on_secondary(True)
        self.chk_auto_secondary.setChecked(True)
        self._multi_screen_service.set_preferred_dock_screen(0)
        if hasattr(self, "rad_dock_scr1"):
            self.rad_dock_scr1.setChecked(True)
