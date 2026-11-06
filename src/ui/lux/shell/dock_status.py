"""
Dock Status Bar: 22px bottom status indicator.
Displays: Connection status dot, timeline name, and playhead timecode.
"""
from typing import Optional
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt


class DockStatusBar(QWidget):
    """
    22px bottom status bar:
    ● Resolve 19 · Timeline_01 · 00:01:12:04
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(6)

        # Connection dot & app name
        self.lbl_conn = QLabel("● Resolve 19")
        self.lbl_conn.setStyleSheet("color: #10b981; font-size: 11px; font-weight: 500;")
        self.lbl_conn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.lbl_conn)

        # Separator bullet
        lbl_dot1 = QLabel("·")
        lbl_dot1.setStyleSheet("color: #52525b; font-size: 11px;")
        layout.addWidget(lbl_dot1)

        # Timeline name
        self.lbl_timeline = QLabel("Timeline_01")
        self.lbl_timeline.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_timeline.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.lbl_timeline, stretch=1)

        # Playhead timecode (Tabular NLE Monospace)
        self.lbl_timecode = QLabel("00:00:00:00")
        self.lbl_timecode.setStyleSheet(
            "color: #e4e4e7; font-family: 'JetBrains Mono', 'Cascadia Mono', 'Consolas', monospace; "
            "font-size: 11px; font-weight: 600; letter-spacing: 0.3px;"
        )
        self.lbl_timecode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.lbl_timecode)

        self.setStyleSheet("""
            QWidget {
                background: #0b0b0e;
                border-top: 1px solid #1f1f27;
            }
        """)

    def set_connected(self, connected: bool, version: str = "19"):
        if connected:
            self.lbl_conn.setText(f"● Resolve {version}")
            self.lbl_conn.setStyleSheet("color: #10b981; font-size: 11px; font-weight: 500;")
        else:
            self.lbl_conn.setText("● Mất kết nối")
            self.lbl_conn.setStyleSheet("color: #71717a; font-size: 11px;")

    def set_timeline(self, name: str):
        self.lbl_timeline.setText(name or "Chưa mở timeline")

    def set_timecode(self, timecode: str):
        self.lbl_timecode.setText(timecode or "00:00:00:00")
