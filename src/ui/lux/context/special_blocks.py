"""
Special Blocks for each DaVinci Resolve page context:
- Edit: Transcript suggestion block (ContextualSuggester)
- Cut: 12 Sound Pads grid (keys 1-9, 0, -, =)
- Fusion: Node capture block (preset_capture)
- Color: LUT intensity & scope controls
- Fairlight: Loudness normalization + pads
- Deliver: Export presets + live render job queue
- Media: Footage summary + Director launcher
"""
from typing import Optional, List, Dict, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QComboBox, QSlider, QFrame, QGridLayout
)
from PySide6.QtCore import Qt, Signal as pyqtSignal, QTimer


class EditSuggestBlock(QWidget):
    """
    Contextual speech suggestion pill at playhead:
    [ Gợi ý tại 00:01:12 — "thật không thể tin nổi"   ✕ ]
    [ Camera Shake ] [ Vine Boom ] [ Ngạc nhiên zoom ]
    """
    asset_suggested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        # Container styling
        self.container = QFrame(self)
        self.container.setStyleSheet("""
            QFrame {
                background: rgba(124, 58, 237, 0.12);
                border: 1px solid rgba(124, 58, 237, 0.3);
                border-radius: 8px;
            }
        """)
        c_layout = QVBoxLayout(self.container)
        c_layout.setContentsMargins(8, 6, 8, 6)
        c_layout.setSpacing(6)

        # Header row with title and dismiss button
        h_row = QHBoxLayout()
        self.lbl_text = QLabel("✨ Gợi ý theo câu thoại — “thật không thể tin nổi”")
        self.lbl_text.setStyleSheet("color: #e4e4e7; font-size: 11.5px; font-weight: 500;")
        h_row.addWidget(self.lbl_text, stretch=1)

        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(18, 18)
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #71717a;
                border: none;
                font-size: 11px;
            }
            QPushButton:hover {
                color: #ffffff;
            }
        """)
        self.btn_close.clicked.connect(self.hide)
        h_row.addWidget(self.btn_close)
        c_layout.addLayout(h_row)

        # Suggestion chips
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        suggestions = [
            ("tr_shake", "Camera Shake"),
            ("s_boom", "Vine Boom"),
            ("m_wow", "Ngạc nhiên zoom"),
        ]
        for aid, label in suggestions:
            btn = QPushButton(label)
            btn.setFixedHeight(24)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: #18181b;
                    color: #c4b5fd;
                    border: 1px solid rgba(124, 58, 237, 0.4);
                    border-radius: 4px;
                    padding: 0 8px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background: #7c3aed;
                    color: #ffffff;
                }
            """)
            btn.clicked.connect(lambda checked=False, x=aid: self.asset_suggested.emit(x))
            btn_row.addWidget(btn)

        btn_row.addStretch(1)
        c_layout.addLayout(btn_row)

        layout.addWidget(self.container)

    def set_suggestion(self, timecode: str, transcript: str, items: List[tuple[str, str]]):
        self.lbl_text.setText(f"✨ Gợi ý tại {timecode} — “{transcript}”")
        self.show()


class CutPadsBlock(QWidget):
    """
    12 Sound Pads grid pinned at the top of the Cut page:
    Keys: 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, -, =
    """
    pad_triggered = pyqtSignal(str)

    PADS = [
        ("s_whoosh", "Whoosh", "1"),
        ("s_swoosh", "Swoosh", "2"),
        ("s_pop", "Pop", "3"),
        ("s_ding", "Ding", "4"),
        ("s_click", "Click", "5"),
        ("s_camera", "Camera", "6"),
        ("s_glitch", "Glitch", "7"),
        ("s_riser", "Riser", "8"),
        ("s_sub", "Sub", "9"),
        ("s_boom", "Boom", "0"),
        ("s_beep", "Beep", "-"),
        ("s_punch", "Punch", "="),
    ]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._buttons: Dict[str, QPushButton] = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(4)

        lbl = QLabel("Pad âm thanh nhanh (Phím 1–9, 0, -, = khi mở Dock)")
        lbl.setStyleSheet("color: #71717a; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.5px;")
        layout.addWidget(lbl)

        grid = QGridLayout()
        grid.setSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)

        for i, (pid, name, key) in enumerate(self.PADS):
            btn = QPushButton(f"{name}\n{key}")
            btn.setFixedHeight(34)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: #18181b;
                    color: #d4d4d8;
                    border: 1px solid #27272a;
                    border-radius: 5px;
                    font-size: 10.5px;
                    line-height: 1.1;
                }
                QPushButton:hover {
                    background: #27272a;
                    border-color: #06b6d4;
                    color: #ffffff;
                }
            """)
            btn.clicked.connect(lambda checked=False, p=pid: self.trigger_pad(p))
            row = i // 4
            col = i % 4
            grid.addWidget(btn, row, col)
            self._buttons[pid] = btn

        layout.addLayout(grid)

    def trigger_pad(self, pad_id: str):
        btn = self._buttons.get(pad_id)
        if btn:
            btn.setStyleSheet("""
                QPushButton {
                    background: #06b6d4;
                    color: #000000;
                    border: 1px solid #22d3ee;
                    border-radius: 5px;
                    font-size: 10.5px;
                    font-weight: bold;
                }
            """)
            QTimer.singleShot(180, lambda: btn.setStyleSheet("""
                QPushButton {
                    background: #18181b;
                    color: #d4d4d8;
                    border: 1px solid #27272a;
                    border-radius: 5px;
                    font-size: 10.5px;
                }
                QPushButton:hover {
                    background: #27272a;
                    border-color: #06b6d4;
                    color: #ffffff;
                }
            """))
        self.pad_triggered.emit(pad_id)

    def trigger_by_key(self, key_char: str) -> bool:
        for pid, name, k in self.PADS:
            if k == key_char:
                self.trigger_pad(pid)
                return True
        return False


class FusionCaptureBlock(QWidget):
    """
    Fusion node style capture:
    [ Node đang chọn: Text1, Transform1 ]  [ 📥 Hút thành preset ]
    """
    capture_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        self.lbl_node = QLabel("Node tại playhead: Sẵn sàng hút")
        self.lbl_node.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        layout.addWidget(self.lbl_node, stretch=1)

        self.btn_capture = QPushButton("📥 Hút thành preset")
        self.btn_capture.setFixedHeight(28)
        self.btn_capture.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_capture.setCursor(Qt.PointingHandCursor)
        self.btn_capture.setStyleSheet("""
            QPushButton {
                background: rgba(6, 182, 212, 0.15);
                color: #22d3ee;
                border: 1px solid rgba(6, 182, 212, 0.4);
                border-radius: 5px;
                padding: 0 10px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #06b6d4;
                color: #000000;
            }
        """)
        self.btn_capture.clicked.connect(self.capture_requested.emit)
        layout.addWidget(self.btn_capture)


class ColorSpecialBlock(QWidget):
    """
    Color Page controls: Intensity & Scope
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        row_scope = QHBoxLayout()
        lbl_s = QLabel("Phạm vi LUT:")
        lbl_s.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        row_scope.addWidget(lbl_s)

        self.combo_scope = QComboBox()
        self.combo_scope.addItems(["Clip tại playhead", "Toàn bộ timeline"])
        self.combo_scope.setFixedHeight(24)
        self.combo_scope.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.combo_scope.setStyleSheet("""
            QComboBox {
                background: #18181b;
                color: #fbbf24;
                border: 1px solid #27272a;
                border-radius: 4px;
                padding: 0 6px;
                font-size: 11px;
            }
        """)
        row_scope.addWidget(self.combo_scope, stretch=1)
        layout.addLayout(row_scope)


class FairlightBlock(QWidget):
    """
    Fairlight controls: Loudness normalization + Sound Pads
    """
    normalize_requested = pyqtSignal(str, str)
    pad_triggered = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        # Normalization row
        row = QHBoxLayout()
        self.combo_target = QComboBox()
        self.combo_target.addItems(["YouTube −14 LUFS", "Podcast −16 LUFS", "EBU R128 −23 LUFS"])
        self.combo_target.setFixedHeight(26)
        self.combo_target.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.combo_target.setStyleSheet("background: #18181b; color: #f472b6; border: 1px solid #27272a; border-radius: 4px; font-size: 11px;")
        row.addWidget(self.combo_target)

        self.combo_track = QComboBox()
        self.combo_track.addItems(["Track A1 (Voice)", "Track A2", "Tất cả track"])
        self.combo_track.setFixedHeight(26)
        self.combo_track.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.combo_track.setStyleSheet("background: #18181b; color: #e4e4e7; border: 1px solid #27272a; border-radius: 4px; font-size: 11px;")
        row.addWidget(self.combo_track)

        self.btn_norm = QPushButton("Chuẩn hóa")
        self.btn_norm.setFixedHeight(26)
        self.btn_norm.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_norm.setCursor(Qt.PointingHandCursor)
        self.btn_norm.setStyleSheet("background: #ec4899; color: #ffffff; border: none; border-radius: 4px; font-size: 11.5px; font-weight: 600; padding: 0 8px;")
        self.btn_norm.clicked.connect(lambda: self.normalize_requested.emit(self.combo_target.currentText(), self.combo_track.currentText()))
        row.addWidget(self.btn_norm)
        layout.addLayout(row)

        # Pads
        self.pads = CutPadsBlock(self)
        self.pads.pad_triggered.connect(self.pad_triggered.emit)
        layout.addWidget(self.pads)

    def trigger_by_key(self, key_char: str) -> bool:
        return self.pads.trigger_by_key(key_char)


class DeliverBlock(QWidget):
    """
    Deliver page: 4 presets + Live render queue with progress bar
    """
    start_render_requested = pyqtSignal(str)

    PRESETS = [
        ("tiktok_916", "TikTok 9:16", "1080×1920 · 60fps · H.264"),
        ("youtube_1080p", "YouTube 1080p", "1920×1080 · 60fps · H.264"),
        ("youtube_4k", "YouTube 4K", "3840×2160 · 60fps · H.265"),
        ("podcast_audio", "Podcast Audio", "WAV 48 kHz Master"),
    ]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._selected_preset = "tiktok_916"
        self._preset_buttons = {}
        self._jobs: List[Dict[str, Any]] = []

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        lbl = QLabel("Mục tiêu xuất bản:")
        lbl.setStyleSheet("color: #a1a1aa; font-size: 11.5px; font-weight: 600;")
        layout.addWidget(lbl)

        # Preset grid
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, (pid, name, desc) in enumerate(self.PRESETS):
            btn = QPushButton(f"{name}\n{desc}")
            btn.setFixedHeight(44)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            is_active = (pid == self._selected_preset)
            btn.setStyleSheet(self._btn_style(is_active))
            btn.clicked.connect(lambda checked=False, p=pid: self._select_preset(p))
            row = i // 2
            col = i % 2
            grid.addWidget(btn, row, col)
            self._preset_buttons[pid] = btn
        layout.addLayout(grid)

        # Action button
        self.btn_render = QPushButton("🚀 Thêm vào hàng đợi & Kết xuất")
        self.btn_render.setFixedHeight(34)
        self.btn_render.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_render.setCursor(Qt.PointingHandCursor)
        self.btn_render.setStyleSheet("""
            QPushButton {
                background: #7c3aed;
                color: #ffffff;
                border: 1px solid #8b5cf6;
                border-radius: 6px;
                font-size: 12.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #6d28d9;
            }
        """)
        self.btn_render.clicked.connect(self._on_render_clicked)
        layout.addWidget(self.btn_render)

        # Render Queue Section
        lbl_q = QLabel("Hàng đợi Render:")
        lbl_q.setStyleSheet("color: #a1a1aa; font-size: 11.5px; font-weight: 600;")
        layout.addWidget(lbl_q)

        self.queue_container = QVBoxLayout()
        self.queue_container.setSpacing(6)
        layout.addLayout(self.queue_container)

        self.lbl_empty = QLabel("Chưa có tác vụ kết xuất.")
        self.lbl_empty.setStyleSheet("color: #71717a; font-size: 11px;")
        self.queue_container.addWidget(self.lbl_empty)

        layout.addStretch(1)

    def _select_preset(self, preset_id: str):
        self._selected_preset = preset_id
        for pid, btn in self._preset_buttons.items():
            btn.setStyleSheet(self._btn_style(pid == self._selected_preset))

    def _on_render_clicked(self):
        self.add_render_job(f"Timeline_01 · {self._selected_preset}")
        self.start_render_requested.emit(self._selected_preset)

    def add_render_job(self, name: str):
        self.lbl_empty.hide()
        job_frame = QFrame()
        job_frame.setStyleSheet("background: #18181b; border: 1px solid #27272a; border-radius: 6px; padding: 6px;")
        j_layout = QVBoxLayout(job_frame)
        j_layout.setContentsMargins(6, 4, 6, 4)
        j_layout.setSpacing(4)

        j_lbl = QLabel(name)
        j_lbl.setStyleSheet("color: #ffffff; font-size: 11px; font-weight: 500;")
        j_layout.addWidget(j_lbl)

        pbar = QProgressBar()
        pbar.setRange(0, 100)
        pbar.setValue(0)
        pbar.setFixedHeight(10)
        pbar.setTextVisible(False)
        pbar.setStyleSheet("""
            QProgressBar {
                background: #27272a;
                border-radius: 5px;
            }
            QProgressBar::chunk {
                background: #7c3aed;
                border-radius: 5px;
            }
        """)
        j_layout.addWidget(pbar)
        self.queue_container.addWidget(job_frame)

        # Simulate rendering progress smoothly if not connected to live DaVinci
        timer = QTimer(job_frame)
        def _step():
            val = pbar.value() + 10
            pbar.setValue(min(100, val))
            if val >= 100:
                timer.stop()
                j_lbl.setText(f"{name}  ✓ Xong")
        timer.timeout.connect(_step)
        timer.start(500)

    def _btn_style(self, active: bool) -> str:
        if active:
            return "background: rgba(124, 58, 237, 0.2); color: #c4b5fd; border: 1px solid #7c3aed; border-radius: 6px; font-size: 11px; font-weight: 600;"
        return "background: #18181b; color: #a1a1aa; border: 1px solid #27272a; border-radius: 6px; font-size: 11px;"


class MediaBlock(QWidget):
    """
    Media Page: footage summary + launch Director
    """
    open_director_requested = pyqtSignal()
    create_proxy_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        lbl_head = QLabel("Footage đang chọn trong Media Pool:")
        lbl_head.setStyleSheet("color: #a1a1aa; font-size: 11.5px; font-weight: 600;")
        layout.addWidget(lbl_head)

        lbl_stats = QLabel("12 clip · 18:42 tổng thời lượng · Đã quét cache")
        lbl_stats.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: 500;")
        layout.addWidget(lbl_stats)

        lbl_desc = QLabel("Director sẽ tự động tách thoại (Whisper AI), lọc khoảng lặng, nhận diện nói vấp và dựng timeline thô mới an toàn mà không đè lên timeline hiện tại.")
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #71717a; font-size: 11px; line-height: 1.4;")
        layout.addWidget(lbl_desc)

        btn_row = QHBoxLayout()
        self.btn_director = QPushButton("🤖 Phân tích & dựng thô (Director)")
        self.btn_director.setFixedHeight(32)
        self.btn_director.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_director.setCursor(Qt.PointingHandCursor)
        self.btn_director.setStyleSheet("background: #06b6d4; color: #000000; border: none; border-radius: 6px; font-size: 12px; font-weight: 600; padding: 0 12px;")
        self.btn_director.clicked.connect(self.open_director_requested.emit)
        btn_row.addWidget(self.btn_director)

        self.btn_proxy = QPushButton("Tạo proxy 480p")
        self.btn_proxy.setFixedHeight(32)
        self.btn_proxy.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_proxy.setCursor(Qt.PointingHandCursor)
        self.btn_proxy.setStyleSheet("background: #18181b; color: #e4e4e7; border: 1px solid #27272a; border-radius: 6px; font-size: 11.5px; padding: 0 10px;")
        self.btn_proxy.clicked.connect(self.create_proxy_requested.emit)
        btn_row.addWidget(self.btn_proxy)

        layout.addLayout(btn_row)
        layout.addStretch(1)
