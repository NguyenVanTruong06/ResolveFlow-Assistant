"""
Cửa sổ Kho Đạo Cụ (ResolveFlow Studio Window).
Thiết kế theo chuẩn 4 cột từ src/ui/resolveflow_ui.html (#v-studio):
- Thanh tiêu đề phong cách chuyên nghiệp: Logo vector, Tên Kho Đạo Cụ, Thống kê tài nguyên, Playhead DaVinci, Nút Ghim trên cùng.
- Nhúng toàn bộ 4 cột của TabAssets:
  + Cột 1: Rail danh mục (Chữ, Màu, Chuyển cảnh, Sticker, Lớp phủ, Meme, SFX, Mẫu của tôi)
  + Cột 2: Phân loại nhóm con & nút nạp file cá nhân
  + Cột 3: Lưới thẻ mẫu trực quan (Text, LUT, Transition, Sticker, SFX...)
  + Cột 4: Inspector tinh chỉnh & nút chèn/kéo thả DaVinci Resolve
"""

import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame
)
from PySide6.QtCore import Qt, QSize, Signal as pyqtSignal, QRunnable, QThreadPool, QObject, QTimer
from PySide6.QtGui import QIcon, QPainter, QColor, QPen, QBrush, QPainterPath

from src.ui.theme import ThemeColors, ThemeFonts, get_application_stylesheet
from src.ui.tabs.tab_assets import TabAssets
from src.services.multi_screen_service import get_multi_screen_service


class HardwareStatsSignals(QObject):
    stats_ready = pyqtSignal(str, str, str)  # cpu_html, gpu_html, ram_html


class HardwareStatsWorker(QRunnable):
    """Worker ngầm định kỳ thu thập tài nguyên hệ thống (CPU, GPU, RAM) không làm giật lag giao diện."""
    def __init__(self):
        super().__init__()
        self.signals = HardwareStatsSignals()

    def run(self):
        import sys
        import time
        import subprocess

        cpu_txt = "21%"
        gpu_txt = "0%"
        ram_txt = "3.4 GB"

        if sys.platform == "win32":
            # --- WINDOWS IMPLEMENTATION ---
            used_psutil = False
            try:
                import psutil
                vm = psutil.virtual_memory()
                used_gb = (vm.total - vm.available) / (1024 ** 3)
                ram_txt = f"{used_gb:.1f} GB"
                cpu_p = psutil.cpu_percent(interval=0.08)
                cpu_txt = f"{int(cpu_p)}%"
                used_psutil = True
            except Exception:
                used_psutil = False

            if not used_psutil:
                try:
                    import ctypes
                    class MEMORYSTATUSEX(ctypes.Structure):
                        _fields_ = [
                            ("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                        ]
                    stat = MEMORYSTATUSEX()
                    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                        used_bytes = stat.ullTotalPhys - stat.ullAvailPhys
                        ram_txt = f"{used_bytes / (1024 ** 3):.1f} GB"
                except Exception:
                    pass

                try:
                    import ctypes
                    class FILETIME(ctypes.Structure):
                        _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

                    def _ft_to_int(ft):
                        return (ft.dwHighDateTime << 32) + ft.dwLowDateTime

                    i1, k1, u1 = FILETIME(), FILETIME(), FILETIME()
                    i2, k2, u2 = FILETIME(), FILETIME(), FILETIME()
                    if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i1), ctypes.byref(k1), ctypes.byref(u1)):
                        time.sleep(0.08)
                        if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i2), ctypes.byref(k2), ctypes.byref(u2)):
                            d_idle = _ft_to_int(i2) - _ft_to_int(i1)
                            d_kernel = _ft_to_int(k2) - _ft_to_int(k1)
                            d_user = _ft_to_int(u2) - _ft_to_int(u1)
                            total = d_kernel + d_user
                            if total > 0:
                                pct = max(0, min(100, int((1.0 - d_idle / total) * 100)))
                                cpu_txt = f"{pct}%"
                except Exception:
                    pass
        else:
            # --- LINUX IMPLEMENTATION ---
            # 1. RAM qua /proc/meminfo
            try:
                if os.path.exists("/proc/meminfo"):
                    m_tot, m_av = 0, 0
                    with open("/proc/meminfo", "r") as f:
                        for line in f:
                            if line.startswith("MemTotal:"):
                                m_tot = int(line.split()[1])
                            elif line.startswith("MemAvailable:"):
                                m_av = int(line.split()[1])
                    if m_tot > 0:
                        used_gb = (m_tot - m_av) / (1024 * 1024)
                        ram_txt = f"{used_gb:.1f} GB"
            except Exception:
                pass

            # 2. CPU qua /proc/stat
            try:
                if os.path.exists("/proc/stat"):
                    with open("/proc/stat", "r") as f:
                        fields = [float(x) for x in f.readline().split()[1:]]
                    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
                    tot = sum(fields)
                    time.sleep(0.08)
                    with open("/proc/stat", "r") as f:
                        f2 = [float(x) for x in f.readline().split()[1:]]
                    i2 = f2[3] + (f2[4] if len(f2) > 4 else 0)
                    t2 = sum(f2)
                    dt = t2 - tot
                    di = i2 - idle
                    if dt > 0:
                        pct = max(0, min(100, int((1.0 - di / dt) * 100)))
                        cpu_txt = f"{pct}%"
            except Exception:
                pass

        # 3. GPU qua nvidia-smi (chạy ngầm an toàn trên cả Windows & Linux)
        try:
            extra_kwargs = {}
            if sys.platform == "win32":
                extra_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=1.0,
                **extra_kwargs
            )
            if res.returncode == 0 and res.stdout.strip():
                gpu_val = res.stdout.strip().split("\n")[0]
                gpu_txt = f"{gpu_val}%"
        except Exception:
            gpu_txt = "0%"

        lbl_cpu_html = f"CPU <b style='color:#ededf0'>{cpu_txt}</b>"
        lbl_gpu_html = f"GPU <b style='color:#ededf0'>{gpu_txt}</b>"
        lbl_ram_html = f"RAM <b style='color:#ededf0'>{ram_txt}</b>"

        try:
            self.signals.stats_ready.emit(lbl_cpu_html, lbl_gpu_html, lbl_ram_html)
        except Exception:
            pass


class StudioWindow(QMainWindow):
    """
    Cửa sổ Hậu kỳ & Kho Đạo Cụ (Studio Asset Hub & Workshop).
    Nơi quản lý thư viện lớn, nạp thư mục từ máy tính, gom nhóm bộ sưu tập và chế tác mẫu cá nhân.
    """
    open_auto_requested = pyqtSignal()
    return_to_dock_requested = pyqtSignal()
    assets_updated = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ResolveFlow Studio · Kho Đạo Cụ & Xưởng Sáng Tạo")
        self.resize(1180, 760)
        self.setMinimumSize(980, 620)
        self.setStyleSheet(get_application_stylesheet())

        # Bật cờ để nhớ vị trí/kích thước sau này
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self.tab_assets = TabAssets(self)
        self._init_ui()

        # Dịch vụ đa màn hình (Dual-Monitor Workflow)
        self._multi_screen_service = get_multi_screen_service()
        self._multi_screen_service.screens_changed.connect(lambda _: self._update_screen_button_label())
        self._update_screen_button_label()

        # Timer ngầm làm mới tài nguyên phần cứng mỗi 5s
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(5000)
        self._stats_timer.timeout.connect(self._fetch_hardware_stats)
        self._stats_timer.start()
        QTimer.singleShot(500, self._fetch_hardware_stats)

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # =====================================================================
        # HEADER (Titlebar) theo chuẩn resolveflow_ui.html
        # =====================================================================
        header = QFrame()
        header.setFixedHeight(46)
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-bottom: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(14, 0, 14, 0)
        h_layout.setSpacing(12)

        # 1. Logo Vector + Tiêu đề
        lbl_logo = QLabel()
        lbl_logo.setFixedSize(26, 26)
        lbl_logo.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8b5cf6, stop:0.5 #7c3aed, stop:1 #0e7490);
            border-radius: 7px;
        """)
        lbl_logo.setAlignment(Qt.AlignCenter)
        lbl_logo.setText("🎬")
        h_layout.addWidget(lbl_logo)

        lbl_title = QLabel("<b>ResolveFlow Studio</b> <span style='background:rgba(124, 58, 237, 0.2); color:#c4b5fd; border:1px solid rgba(124, 58, 237, 0.5); border-radius:4px; padding:2px 6px; font-size:11px; font-weight:600;'>Xưởng & Nạp Kho</span>")
        lbl_title.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; font-size: 13.5px;")
        h_layout.addWidget(lbl_title)

        h_layout.addStretch(1)

        # Style cho các nút quản lý kho
        btn_mgmt_style = """
            QPushButton {
                background-color: #18181b;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                padding: 4px 10px;
                border-radius: 6px;
                font-size: 11.5px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #27272a;
                border-color: #71717a;
                color: #ffffff;
            }
        """

        # 2. Bộ nút tác vụ quản lý cấp cao (High-Level Management Actions)
        self.btn_import_folder = QPushButton("📁 Nạp Thư Mục")
        self.btn_import_folder.setCursor(Qt.PointingHandCursor)
        self.btn_import_folder.setToolTip("Quét nạp cả thư mục SFX, Meme, Footage, LUTs từ máy tính vào kho")
        self.btn_import_folder.setStyleSheet(btn_mgmt_style)
        self.btn_import_folder.clicked.connect(self._on_import_folder_clicked)
        h_layout.addWidget(self.btn_import_folder)

        self.btn_import_file = QPushButton("📥 Thêm Tệp")
        self.btn_import_file.setCursor(Qt.PointingHandCursor)
        self.btn_import_file.setToolTip("Nhập tệp mẫu đơn lẻ (.setting, .mp4, .mov, .wav, .cube, .png)")
        self.btn_import_file.setStyleSheet(btn_mgmt_style)
        self.btn_import_file.clicked.connect(self._on_import_file_clicked)
        h_layout.addWidget(self.btn_import_file)

        self.btn_sync_dock = QPushButton("🔄 Đồng Bộ Dock")
        self.btn_sync_dock.setCursor(Qt.PointingHandCursor)
        self.btn_sync_dock.setToolTip("Làm mới và đồng bộ tức thì toàn bộ tài nguyên sang Thanh Dock")
        self.btn_sync_dock.setStyleSheet(btn_mgmt_style)
        self.btn_sync_dock.clicked.connect(self._on_sync_dock_clicked)
        h_layout.addWidget(self.btn_sync_dock)

        # Nút chuyển nhanh sang Cửa sổ AI Director
        self.btn_switch_auto = QPushButton("⚡ AI Director")
        self.btn_switch_auto.setProperty("role", "nav-switch")
        self.btn_switch_auto.setCursor(Qt.PointingHandCursor)
        self.btn_switch_auto.setToolTip("Chuyển sang Cửa sổ AI Director (Auto Cut, Kịch Bản, Phụ đề)")
        self.btn_switch_auto.clicked.connect(self.open_auto_requested.emit)
        h_layout.addWidget(self.btn_switch_auto)

        # Nút chuyển sang Thanh Dock dán mép để bắt đầu dựng phim
        self.btn_switch_dock = QPushButton("⚡ Dựng Với Thanh Dock")
        self.btn_switch_dock.setCursor(Qt.PointingHandCursor)
        self.btn_switch_dock.setToolTip("Thu nhỏ cửa sổ Kho và chuyển sang Thanh Dock dán sát mép DaVinci Resolve")
        self.btn_switch_dock.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7c3aed, stop:1 #6d28d9);
                color: #ffffff;
                border: 1px solid #8b5cf6;
                padding: 4px 12px;
                border-radius: 6px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #6d28d9;
            }
        """)
        self.btn_switch_dock.clicked.connect(self.return_to_dock_requested.emit)
        h_layout.addWidget(self.btn_switch_dock)

        # Nút chuyển đổi màn hình (Dual-Monitor Switch)
        self.btn_switch_screen = QPushButton("🖥️ Sang Màn 2")
        self.btn_switch_screen.setCursor(Qt.PointingHandCursor)
        self.btn_switch_screen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_switch_screen.setToolTip("Chuyển cửa sổ Kho Đạo Cụ sang màn hình phụ hoặc màn hình chính")
        self.btn_switch_screen.setStyleSheet(btn_mgmt_style)
        self.btn_switch_screen.clicked.connect(self._on_switch_screen_clicked)
        h_layout.addWidget(self.btn_switch_screen)

        # 3. Thống kê phần cứng (.stats)
        stats_frame = QFrame()
        stats_frame.setStyleSheet(f"""
            color: {ThemeColors.TEXT_MUTED};
            font-size: 11.5px;
        """)
        s_layout = QHBoxLayout(stats_frame)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(10)

        self.lbl_cpu = QLabel("CPU <b style='color:#ededf0'>--</b>")
        self.lbl_gpu = QLabel("GPU <b style='color:#ededf0'>--</b>")
        self.lbl_ram = QLabel("RAM <b style='color:#ededf0'>--</b>")
        s_layout.addWidget(self.lbl_cpu)
        s_layout.addWidget(self.lbl_gpu)
        s_layout.addWidget(self.lbl_ram)
        h_layout.addWidget(stats_frame)

        # 4. Trạng thái Playhead DaVinci Resolve (.pill)
        self.pill_playhead = QLabel("● Playhead V1 · 14:49")
        self.pill_playhead.setStyleSheet(f"""
            background-color: {ThemeColors.BG_CARD};
            border: 1px solid {ThemeColors.BORDER_DEFAULT};
            border-radius: 6px;
            color: {ThemeColors.GREEN};
            font-size: 11.5px;
            font-weight: 500;
            padding: 3px 8px;
        """)
        h_layout.addWidget(self.pill_playhead)

        # 5. Nút Ghim Luôn trên cùng (#pinBtn)
        self.btn_pin = QPushButton("📌")
        self.btn_pin.setToolTip("Ghim cửa sổ luôn trên cùng")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setCursor(Qt.PointingHandCursor)
        self.btn_pin.clicked.connect(self._toggle_always_on_top)
        self.btn_pin.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {ThemeColors.TEXT_MUTED};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                padding: 4px 8px;
                border-radius: 6px;
                font-size: 11.5px;
            }}
            QPushButton:hover {{
                border-color: {ThemeColors.BORDER_HOVER};
                color: {ThemeColors.TEXT_PRIMARY};
            }}
            QPushButton:checked {{
                color: #c4b5fd;
                border-color: {ThemeColors.PRIMARY};
                background-color: {ThemeColors.VIOLET_LO};
                font-weight: bold;
            }}
        """)
        h_layout.addWidget(self.btn_pin)

        main_layout.addWidget(header)

        # =====================================================================
        # WORKFLOW BANNER (Thanh định vị mục đích sử dụng)
        # =====================================================================
        self.workflow_banner = QFrame()
        self.workflow_banner.setFixedHeight(34)
        self.workflow_banner.setStyleSheet("""
            QFrame {
                background-color: #12101e;
                border-bottom: 1px solid #27272a;
            }
        """)
        wb_layout = QHBoxLayout(self.workflow_banner)
        wb_layout.setContentsMargins(14, 0, 14, 0)
        wb_layout.setSpacing(10)

        lbl_hint = QLabel(
            "💡 <b style='color:#c4b5fd;'>Phân định vai trò:</b> "
            "Dùng <b>Thanh Dock (dán mép)</b> để tìm nhanh & chèn playhead trong 1s khi đang dựng. "
            "Mở <b>Kho Đạo Cụ</b> này khi cần: Nạp thư mục từ máy tính · Quản lý thư viện · Gom nhóm bộ sưu tập · Chế tác mẫu."
        )
        lbl_hint.setStyleSheet("font-size: 11.5px; color: #a1a1aa;")
        wb_layout.addWidget(lbl_hint, stretch=1)

        btn_banner_dock = QPushButton("⚡ Sang Thanh Dock")
        btn_banner_dock.setCursor(Qt.PointingHandCursor)
        btn_banner_dock.setStyleSheet("""
            QPushButton {
                background: rgba(124, 58, 237, 0.2);
                color: #c4b5fd;
                border: 1px solid #7c3aed;
                border-radius: 4px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #7c3aed;
                color: #ffffff;
            }
        """)
        btn_banner_dock.clicked.connect(self.return_to_dock_requested.emit)
        wb_layout.addWidget(btn_banner_dock)

        main_layout.addWidget(self.workflow_banner)

        # =====================================================================
        # GIAO DIỆN 4 CỘT KHO ĐẠO CỤ (TabAssets)
        # =====================================================================
        main_layout.addWidget(self.tab_assets, stretch=1)

    def _fetch_hardware_stats(self):
        worker = HardwareStatsWorker()
        worker.signals.stats_ready.connect(self._update_hardware_stats_ui)
        self._current_stats_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _update_hardware_stats_ui(self, cpu_html: str, gpu_html: str, ram_html: str):
        if hasattr(self, "lbl_cpu"):
            self.lbl_cpu.setText(cpu_html)
        if hasattr(self, "lbl_gpu"):
            self.lbl_gpu.setText(gpu_html)
        if hasattr(self, "lbl_ram"):
            self.lbl_ram.setText(ram_html)

    def _toggle_always_on_top(self):
        is_on_top = self.btn_pin.isChecked()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, is_on_top)
        self.show()

    def _on_import_folder_clicked(self):
        if hasattr(self.tab_assets, "_scan_local_folder"):
            self.tab_assets._scan_local_folder()
            self._notify_assets_changed()

    def _on_import_file_clicked(self):
        if hasattr(self.tab_assets, "_import_files_or_folders"):
            self.tab_assets._import_files_or_folders()
            self._notify_assets_changed()

    def _on_sync_dock_clicked(self):
        if hasattr(self.tab_assets, "_rescan_assets"):
            self.tab_assets._rescan_assets()
        self._notify_assets_changed()
        if hasattr(self.tab_assets, "_show_toast"):
            self.tab_assets._show_toast("🔄 Đã làm mới & đồng bộ toàn bộ tài nguyên sang Thanh Dock!")

    def _notify_assets_changed(self):
        assets = getattr(self.tab_assets, "all_assets", [])
        if assets:
            self.assets_updated.emit(assets)

    def _on_switch_screen_clicked(self):
        """Chuyển đổi tức thì Kho Đạo Cụ giữa màn hình chính và màn hình phụ."""
        svc = get_multi_screen_service()
        if not svc.is_multi_monitor():
            return

        curr_idx = svc.get_screen_index_of_widget(self)
        target_idx = svc.get_other_screen_index(curr_idx)
        success = svc.move_widget_to_screen(self, target_idx)
        if success:
            svc.set_preferred_studio_screen(target_idx)
            self._update_screen_button_label()
            if hasattr(self.tab_assets, "_show_toast"):
                self.tab_assets._show_toast(f"🖥️ Đã chuyển Kho Đạo Cụ sang Màn hình {target_idx + 1}!")

    def _update_screen_button_label(self):
        """Cập nhật nhãn và trạng thái nút chuyển màn hình theo vị trí hiện tại."""
        if not hasattr(self, "btn_switch_screen"):
            return

        svc = get_multi_screen_service()
        if not svc.is_multi_monitor():
            self.btn_switch_screen.setText("🖥️ 1 Màn Hình")
            self.btn_switch_screen.setEnabled(False)
            self.btn_switch_screen.setToolTip("Hệ thống chỉ có 1 màn hình. Khi cắm thêm màn hình thứ 2, tính năng chuyển màn hình sẽ sẵn sàng.")
        else:
            self.btn_switch_screen.setEnabled(True)
            curr_idx = svc.get_screen_index_of_widget(self)
            target_idx = svc.get_other_screen_index(curr_idx)
            self.btn_switch_screen.setText(f"🖥️ Sang Màn {target_idx + 1}")
            self.btn_switch_screen.setToolTip(f"Đang ở Màn {curr_idx + 1}. Bấm để chuyển tức thì sang Màn {target_idx + 1}")

    def showEvent(self, event):
        super().showEvent(event)
        self._update_screen_button_label()

    def closeEvent(self, event):
        if hasattr(self, "_stats_timer") and self._stats_timer.isActive():
            self._stats_timer.stop()
        super().closeEvent(event)
