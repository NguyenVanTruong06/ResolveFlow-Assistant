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

        self.signals.stats_ready.emit(lbl_cpu_html, lbl_gpu_html, lbl_ram_html)


class StudioWindow(QMainWindow):
    """
    Cửa sổ Hậu kỳ & Kho Đạo Cụ (Studio Mode).
    """
    open_auto_requested = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ResolveFlow · Kho Đạo Cụ")
        self.resize(1140, 740)
        self.setMinimumSize(980, 620)
        self.setStyleSheet(get_application_stylesheet())

        # Bật cờ để nhớ vị trí/kích thước sau này
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self.tab_assets = TabAssets(self)
        self._init_ui()

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
        # Vẽ biểu tượng camera play bên trong logo
        lbl_logo.setAlignment(Qt.AlignCenter)
        lbl_logo.setText("🎬")
        h_layout.addWidget(lbl_logo)

        lbl_title = QLabel("<b>Kho Đạo Cụ</b>")
        lbl_title.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; font-size: 14px;")
        h_layout.addWidget(lbl_title)

        h_layout.addStretch(1)

        # Nút chuyển nhanh sang Cửa sổ AI Director
        self.btn_switch_auto = QPushButton("⚡ AI Director (Auto Cut)")
        self.btn_switch_auto.setProperty("role", "nav-switch")
        self.btn_switch_auto.setCursor(Qt.PointingHandCursor)
        self.btn_switch_auto.setToolTip("Chuyển sang Cửa sổ AI Director (Auto Cut, Kịch Bản)")
        self.btn_switch_auto.clicked.connect(self.open_auto_requested.emit)
        h_layout.addWidget(self.btn_switch_auto)

        # 2. Thống kê phần cứng (.stats)
        stats_frame = QFrame()
        stats_frame.setStyleSheet(f"""
            color: {ThemeColors.TEXT_MUTED};
            font-size: 11.5px;
        """)
        s_layout = QHBoxLayout(stats_frame)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(12)

        self.lbl_cpu = QLabel("CPU <b style='color:#ededf0'>--</b>")
        self.lbl_gpu = QLabel("GPU <b style='color:#ededf0'>--</b>")
        self.lbl_ram = QLabel("RAM <b style='color:#ededf0'>--</b>")
        s_layout.addWidget(self.lbl_cpu)
        s_layout.addWidget(self.lbl_gpu)
        s_layout.addWidget(self.lbl_ram)
        h_layout.addWidget(stats_frame)

        # 3. Trạng thái Playhead DaVinci Resolve (.pill)
        self.pill_playhead = QLabel("● Playhead V1 · 14:49")
        self.pill_playhead.setStyleSheet(f"""
            background-color: {ThemeColors.BG_CARD};
            border: 1px solid {ThemeColors.BORDER_DEFAULT};
            border-radius: 6px;
            color: {ThemeColors.GREEN};
            font-size: 11.5px;
            font-weight: 500;
            padding: 3px 10px;
        """)
        h_layout.addWidget(self.pill_playhead)

        # 4. Nút Ghim Luôn trên cùng (#pinBtn)
        self.btn_pin = QPushButton("📌 Luôn trên cùng")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setCursor(Qt.PointingHandCursor)
        self.btn_pin.clicked.connect(self._toggle_always_on_top)
        self.btn_pin.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {ThemeColors.TEXT_MUTED};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                padding: 4px 12px;
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

    def closeEvent(self, event):
        if hasattr(self, "_stats_timer") and self._stats_timer.isActive():
            self._stats_timer.stop()
        super().closeEvent(event)
