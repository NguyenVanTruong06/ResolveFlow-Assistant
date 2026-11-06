"""
Toast Feedback with Undo Button for ChunDVC.
Floats at the bottom right of the DaVinci Resolve window for 4 seconds.
(UX_REDESIGN.md Section 5.4)
"""
from typing import Optional, Callable, Any, Tuple
import logging

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QGraphicsOpacityEffect, QApplication
)
from PySide6.QtCore import (
    Qt, QTimer, Signal as pyqtSignal, QPropertyAnimation, QEasingCurve, QRect
)

from src.ui.lux.window_anchor import get_resolve_window_rect

logger = logging.getLogger("ChunDVC.Toast")


class ChunDVCToast(QWidget):
    """
    Floating notification toast anchored at the bottom right of DaVinci Resolve.
    Dismisses automatically after 4 seconds with smooth fade-out.
    Supports 1-click Undo for InsertService actions.
    """
    undo_requested = pyqtSignal(object)   # Emits insert_result
    action_requested = pyqtSignal(str)    # Emits action id for error retry
    closed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._insert_result: Optional[Any] = None
        self._undo_callback: Optional[Callable] = None
        self._anchor_rect: Optional[Tuple[int, int, int, int]] = None

        # Opacity animation for smooth entry and exit
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)

        # 4-second auto dismiss timer
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)

        self._init_ui()

    def _init_ui(self):
        self.setFixedHeight(44)
        self.setMinimumWidth(320)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(12, 6, 12, 6)
        self.layout.setSpacing(10)

        # Status icon
        self.lbl_icon = QLabel("✓")
        self.lbl_icon.setStyleSheet("color: #a78bfa; font-weight: bold; font-size: 13px;")
        self.layout.addWidget(self.lbl_icon)

        # Message label
        self.lbl_msg = QLabel("Đã chèn asset thành công")
        self.lbl_msg.setStyleSheet("color: #f4f4f5; font-size: 12px; font-weight: 500;")
        self.layout.addWidget(self.lbl_msg, stretch=1)

        # Action / Undo Button
        self.btn_action = QPushButton("Hoàn tác")
        self.btn_action.setFixedHeight(26)
        self.btn_action.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_action.setCursor(Qt.PointingHandCursor)
        self.btn_action.setStyleSheet("""
            QPushButton {
                background: rgba(124, 58, 237, 0.2);
                color: #c4b5fd;
                border: 1px solid rgba(124, 58, 237, 0.5);
                border-radius: 4px;
                padding: 0 10px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #7c3aed;
                color: #ffffff;
            }
        """)
        self.btn_action.clicked.connect(self._on_action_clicked)
        self.layout.addWidget(self.btn_action)

        # Dismiss button
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(20, 20)
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
        self.btn_close.clicked.connect(self.dismiss)
        self.layout.addWidget(self.btn_close)

    def set_anchor_rect(self, rect: Optional[Tuple[int, int, int, int]]):
        self._anchor_rect = rect

    def show_success(
        self,
        asset_name: str,
        track_name: str = "",
        timecode: str = "",
        result: Optional[Any] = None,
        undo_callback: Optional[Callable] = None,
    ):
        """Displays 4-second success toast with [Hoàn tác] button."""
        self._insert_result = result
        self._undo_callback = undo_callback

        parts = [f"Đã chèn {asset_name}"]
        if track_name:
            parts.append(str(track_name))
        if timecode:
            parts.append(str(timecode))
        msg_text = " · ".join(parts)

        self.lbl_icon.setText("✓")
        self.lbl_icon.setStyleSheet("color: #34d399; font-weight: bold; font-size: 13px;")
        self.lbl_msg.setText(msg_text)
        self.lbl_msg.setStyleSheet("color: #f4f4f5; font-size: 12px; font-weight: 500;")

        self.btn_action.setText("Hoàn tác")
        self.btn_action.show()
        self.btn_action.setStyleSheet("""
            QPushButton {
                background: rgba(124, 58, 237, 0.25);
                color: #c4b5fd;
                border: 1px solid rgba(124, 58, 237, 0.5);
                border-radius: 4px;
                padding: 0 10px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #7c3aed;
                color: #ffffff;
            }
        """)

        self.setStyleSheet("""
            ChunDVCToast {
                background: #18181b;
                border: 1px solid #7c3aed;
                border-radius: 8px;
            }
        """)

        self._popup()

    def show_message(
        self,
        message: str,
        icon: str = "✓",
        level: str = "success",
        duration_ms: int = 4000,
        action_label: Optional[str] = None,
        action_callback: Optional[Callable] = None,
    ):
        """Displays generic toast with custom message, icon, and styling."""
        self._insert_result = None
        self._undo_callback = action_callback

        self.lbl_icon.setText(icon)
        self.lbl_msg.setText(message)

        if level == "error":
            self.lbl_icon.setStyleSheet("color: #f87171; font-weight: bold; font-size: 13px;")
            self.lbl_msg.setStyleSheet("color: #fca5a5; font-size: 12px; font-weight: 500;")
            self.setStyleSheet("""
                ChunDVCToast {
                    background: #201215;
                    border: 1px solid #ef4444;
                    border-radius: 8px;
                }
            """)
        elif level == "warning":
            self.lbl_icon.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 13px;")
            self.lbl_msg.setStyleSheet("color: #fde68a; font-size: 12px; font-weight: 500;")
            self.setStyleSheet("""
                ChunDVCToast {
                    background: #1c1917;
                    border: 1px solid #f59e0b;
                    border-radius: 8px;
                }
            """)
        elif level == "info":
            self.lbl_icon.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 13px;")
            self.lbl_msg.setStyleSheet("color: #e0f2fe; font-size: 12px; font-weight: 500;")
            self.setStyleSheet("""
                ChunDVCToast {
                    background: #0f172a;
                    border: 1px solid #0284c7;
                    border-radius: 8px;
                }
            """)
        else: # success
            self.lbl_icon.setStyleSheet("color: #34d399; font-weight: bold; font-size: 13px;")
            self.lbl_msg.setStyleSheet("color: #f4f4f5; font-size: 12px; font-weight: 500;")
            self.setStyleSheet("""
                ChunDVCToast {
                    background: #18181b;
                    border: 1px solid #10b981;
                    border-radius: 8px;
                }
            """)

        if action_label and action_callback:
            self.btn_action.setText(action_label)
            self.btn_action.show()
        else:
            self.btn_action.hide()

        self._popup()
        if duration_ms > 0:
            self._dismiss_timer.start(duration_ms)
        else:
            self._dismiss_timer.stop()

    def show_toast(self, message: str, level: str = "info", duration_ms: int = 4000):
        """Compatibility alias for show_message."""
        icon = "⚠" if level in ("warning", "error") else "✓"
        self.show_message(message=message, icon=icon, level=level, duration_ms=duration_ms)

    def show_error(
        self,
        reason: str,
        action_label: str = "Tạo track mới",
        action_id: str = "new_track",
    ):
        """Displays error toast with red border and retry/action button."""
        self._insert_result = None
        self._undo_callback = None

        self.lbl_icon.setText("⚠")
        self.lbl_icon.setStyleSheet("color: #f87171; font-weight: bold; font-size: 13px;")
        self.lbl_msg.setText(f"Lỗi: {reason}")
        self.lbl_msg.setStyleSheet("color: #fca5a5; font-size: 12px; font-weight: 500;")

        if action_label:
            self.btn_action.setText(action_label)
            self.btn_action.show()
            self.btn_action.setStyleSheet("""
                QPushButton {
                    background: rgba(239, 68, 68, 0.2);
                    color: #fca5a5;
                    border: 1px solid #ef4444;
                    border-radius: 4px;
                    padding: 0 10px;
                    font-size: 11.5px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background: #ef4444;
                    color: #ffffff;
                }
            """)
        else:
            self.btn_action.hide()

        self.setStyleSheet("""
            ChunDVCToast {
                background: #201215;
                border: 1px solid #ef4444;
                border-radius: 8px;
            }
        """)

        self._popup()

    def _popup(self):
        self._update_geometry()
        self._dismiss_timer.stop()
        self._anim.stop()

        self._opacity_effect.setOpacity(0.0)
        self.show()

    def paintEvent(self, event):
        from PySide6.QtWidgets import QStyleOption, QStyle
        from PySide6.QtGui import QPainter
        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)
        super().paintEvent(event)

        # Fade in 150ms
        self._anim.setDuration(150)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

        # Start 4-second auto dismiss
        self._dismiss_timer.start(4000)

    def dismiss(self):
        """Fades out and hides."""
        self._dismiss_timer.stop()
        self._anim.stop()

        self._anim.setDuration(200)
        self._anim.setStartValue(self._opacity_effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.finished.connect(self._on_dismiss_finished)
        self._anim.start()

    def _on_dismiss_finished(self):
        try:
            self._anim.finished.disconnect(self._on_dismiss_finished)
        except Exception:
            pass
        self.hide()
        self.closed.emit()

    def _on_action_clicked(self):
        if self.btn_action.text() == "Hoàn tác":
            logger.info("Toast Undo clicked for result: %s", self._insert_result)
            if self._undo_callback and self._insert_result:
                self._undo_callback(self._insert_result)
            self.undo_requested.emit(self._insert_result)

            # Update toast to confirm undo
            self.lbl_msg.setText("Đã hoàn tác thao tác chèn ✓")
            self.btn_action.hide()
            self._dismiss_timer.start(1500)
        else:
            if self._undo_callback:
                try:
                    self._undo_callback()
                except Exception as ex:
                    logger.error("Error executing toast action callback: %s", ex)
            self.action_requested.emit("action")
            self.dismiss()

    def _update_geometry(self):
        self.adjustSize()
        w = max(340, self.sizeHint().width() + 20)
        h = self.height()

        anchor = self._anchor_rect or get_resolve_window_rect()
        if anchor:
            ax, ay, aw, ah = anchor
            x = ax + aw - w - 16
            y = ay + ah - h - 24
        else:
            screen = QApplication.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                x = geom.x() + geom.width() - w - 24
                y = geom.y() + geom.height() - h - 32
            else:
                x, y = 100, 100

        self.setGeometry(int(x), int(y), int(w), int(h))
