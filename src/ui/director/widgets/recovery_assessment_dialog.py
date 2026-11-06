"""src/ui/director/widgets/recovery_assessment_dialog.py
Hộp thoại giao diện trình bày kết quả đối soát và phục hồi chỉ đọc (G07-07A8-I).

RÀNG BUỘC KIẾN TRÚC & BẢO MẬT:
- Thuần túy chỉ đọc (Strict Read-Only): Không có nút thực thi bù trừ hay rollback timeline.
  `ready_to_execute=False` được bảo toàn nguyên vẹn 100%.
- Bounded UI: Giới hạn trần 100 dòng cho bảng ứng viên bù trừ (`MAX_UI_ROWS = 100`).
- Phòng vệ UI Injection: Toàn bộ nhãn chẩn đoán, nội dung bảng, và thông báo đều ép buộc
  `setTextFormat(Qt.TextFormat.PlainText)` để triệt tiêu nguy cơ chèn HTML/script/ANSI escape.
- Phân biệt rạch ròi 5 nhóm trạng thái và nhóm lỗi I/O hệ điều hành (`ASSESSMENT_UNAVAILABLE_READ_ERROR`).
- Ghi chú chuẩn tắc: Hồ sơ biên nhận và nhật ký là lịch sử quá khứ của ứng dụng, không chứng minh
  timeline DaVinci Resolve hiện tại còn giữ nguyên các clip.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.contracts.recovery_assessment_contracts import (
    AmbiguousActionDetail,
    CandidateCompensatingAction,
    RecoveryAssessment,
    TargetIdentityMatchReport,
)

logger = logging.getLogger(__name__)

MAX_UI_ROWS: int = 100


class RecoveryAssessmentDialog(QDialog):
    """Hộp thoại hiển thị báo cáo đối soát phục hồi chỉ đọc dành cho người vận hành."""

    def __init__(
        self,
        assessment: Optional[RecoveryAssessment] = None,
        io_error: Optional[str] = None,
        refresh_callback: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Đối Soát & Phục Hồi Phiên Thi Công (Chỉ Đọc)")
        self.resize(880, 720)
        self.setMinimumSize(780, 560)
        self.setStyleSheet("""
            QDialog {
                background-color: #121214;
                color: #e4e4e7;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            }
        """)

        self._assessment = assessment
        self._io_error = io_error
        self._refresh_callback = refresh_callback

        self._init_ui()
        self._setup_shortcuts()

        if self._io_error:
            self.show_io_error(self._io_error)
        elif self._assessment:
            self.show_assessment(self._assessment)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(10)

        # -------------------------------------------------------------
        # 1. Header Banner (mã màu động theo trạng thái)
        # -------------------------------------------------------------
        self.frame_header = QFrame(self)
        self.frame_header.setFrameShape(QFrame.Shape.StyledPanel)
        layout_header = QVBoxLayout(self.frame_header)
        layout_header.setContentsMargins(14, 10, 14, 10)
        layout_header.setSpacing(4)

        h_badge = QHBoxLayout()
        self.lbl_status_badge = QLabel("ĐANG ĐỐI SOÁT...", self.frame_header)
        self.lbl_status_badge.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_status_badge.setStyleSheet("font-size: 13px; font-weight: bold; color: #ffffff;")
        h_badge.addWidget(self.lbl_status_badge)

        self.lbl_recommendation = QLabel("", self.frame_header)
        self.lbl_recommendation.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_recommendation.setStyleSheet("font-size: 11.5px; font-weight: 600; color: #d4d4d8;")
        h_badge.addWidget(self.lbl_recommendation)
        h_badge.addStretch(1)
        layout_header.addLayout(h_badge)

        self.lbl_explanation = QLabel("", self.frame_header)
        self.lbl_explanation.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_explanation.setWordWrap(True)
        self.lbl_explanation.setStyleSheet("font-size: 12px; color: #f4f4f5; line-height: 1.4;")
        layout_header.addWidget(self.lbl_explanation)

        main_layout.addWidget(self.frame_header)

        # -------------------------------------------------------------
        # Khung Báo Lỗi I/O Hệ Điều Hành (Assessment Unavailable Box)
        # -------------------------------------------------------------
        self.frame_io_error = QFrame(self)
        self.frame_io_error.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_io_error.setStyleSheet("""
            QFrame {
                background-color: #27272a;
                border: 1px solid #71717a;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        layout_io = QVBoxLayout(self.frame_io_error)
        lbl_io_title = QLabel("⚠️ KHÔNG THỂ ĐỌC HỒ SƠ LƯU TRỮ (LỖI TRUY CẬP I/O)", self.frame_io_error)
        lbl_io_title.setTextFormat(Qt.TextFormat.PlainText)
        lbl_io_title.setStyleSheet("font-weight: bold; color: #fbbf24; font-size: 12px;")
        layout_io.addWidget(lbl_io_title)

        self.lbl_io_error_message = QLabel("", self.frame_io_error)
        self.lbl_io_error_message.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_io_error_message.setWordWrap(True)
        self.lbl_io_error_message.setStyleSheet("color: #e4e4e7; font-size: 11.5px;")
        layout_io.addWidget(self.lbl_io_error_message)

        lbl_io_guide = QLabel(
            "Lưu ý: Chưa có bằng chứng cấu trúc hồ sơ bị hỏng (corruption). Trạng thái phiên hiện KHÔNG XÁC ĐỊNH.\n"
            "Vui lòng kiểm tra quyền đọc tệp (chmod/chown) hoặc thiết bị lưu trữ, sau đó bấm 'Làm mới (F5)'.",
            self.frame_io_error,
        )
        lbl_io_guide.setTextFormat(Qt.TextFormat.PlainText)
        lbl_io_guide.setWordWrap(True)
        lbl_io_guide.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        layout_io.addWidget(lbl_io_guide)

        self.frame_io_error.setVisible(False)
        main_layout.addWidget(self.frame_io_error)

        # -------------------------------------------------------------
        # 2. Thẻ Đối Chiếu Danh Tính Đích (Target Identity Card)
        # -------------------------------------------------------------
        self.frame_target = QFrame(self)
        self.frame_target.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_target.setStyleSheet("""
            QFrame {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
            }
        """)
        layout_target = QVBoxLayout(self.frame_target)
        layout_target.setContentsMargins(12, 8, 12, 8)
        layout_target.setSpacing(6)

        lbl_target_hdr = QLabel("ĐỐI CHIẾU DANH TÍNH RESOLVE & HỒ SƠ PHIÊN", self.frame_target)
        lbl_target_hdr.setTextFormat(Qt.TextFormat.PlainText)
        lbl_target_hdr.setStyleSheet("font-weight: bold; font-size: 11.5px; color: #a1a1aa;")
        layout_target.addWidget(lbl_target_hdr)

        grid_target = QGridLayout()
        grid_target.setSpacing(6)

        self.lbl_exec_id = QLabel("-", self.frame_target)
        self.lbl_idemp_key = QLabel("-", self.frame_target)
        self.lbl_lock_status = QLabel("-", self.frame_target)
        self.lbl_pid = QLabel("-", self.frame_target)
        self.lbl_project_match = QLabel("-", self.frame_target)
        self.lbl_timeline_match = QLabel("-", self.frame_target)

        for w in (self.lbl_exec_id, self.lbl_idemp_key, self.lbl_lock_status, self.lbl_pid, self.lbl_project_match, self.lbl_timeline_match):
            w.setTextFormat(Qt.TextFormat.PlainText)
            w.setStyleSheet("color: #e4e4e7; font-size: 11.5px;")

        grid_target.addWidget(self._make_label("Mã phiên (Execution ID):"), 0, 0)
        grid_target.addWidget(self.lbl_exec_id, 0, 1)
        grid_target.addWidget(self._make_label("Khóa Idempotency:"), 0, 2)
        grid_target.addWidget(self.lbl_idemp_key, 0, 3)

        grid_target.addWidget(self._make_label("Khóa cố vấn OS:"), 1, 0)
        grid_target.addWidget(self.lbl_lock_status, 1, 1)
        grid_target.addWidget(self._make_label("PID chẩn đoán:"), 1, 2)
        grid_target.addWidget(self.lbl_pid, 1, 3)

        grid_target.addWidget(self._make_label("Dự án (Project):"), 2, 0)
        grid_target.addWidget(self.lbl_project_match, 2, 1)
        grid_target.addWidget(self._make_label("Timeline:"), 2, 2)
        grid_target.addWidget(self.lbl_timeline_match, 2, 3)

        layout_target.addLayout(grid_target)

        # Cảnh báo trôi lệch danh tính
        self.lbl_target_drift_warning = QLabel(
            "⚠️ CẢNH BÁO: Timeline đang mở trong Resolve không khớp với Timeline phiên sự cố. "
            "Toàn bộ thao tác đối chiếu hoặc xem xét ứng viên bù trừ bị vô hiệu hóa.",
            self.frame_target,
        )
        self.lbl_target_drift_warning.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_target_drift_warning.setWordWrap(True)
        self.lbl_target_drift_warning.setStyleSheet("""
            background-color: rgba(239, 68, 68, 0.2);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.4);
            border-radius: 4px;
            padding: 6px;
            font-weight: bold;
            font-size: 11.5px;
        """)
        self.lbl_target_drift_warning.setVisible(False)
        layout_target.addWidget(self.lbl_target_drift_warning)

        # Cảnh báo thẩm quyền Resolve chưa sẵn sàng
        self.lbl_target_unavailable_warning = QLabel(
            "ℹ️ Thẩm quyền DaVinci Resolve hiện chưa sẵn sàng (chưa mở hoặc lỗi IPC). "
            "Hệ thống thực hiện đánh giá bảo thủ không có đối chiếu đích trực tiếp.",
            self.frame_target,
        )
        self.lbl_target_unavailable_warning.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_target_unavailable_warning.setWordWrap(True)
        self.lbl_target_unavailable_warning.setStyleSheet("""
            background-color: rgba(113, 113, 122, 0.2);
            color: #d4d4d8;
            border: 1px solid rgba(113, 113, 122, 0.4);
            border-radius: 4px;
            padding: 6px;
            font-size: 11.5px;
        """)
        self.lbl_target_unavailable_warning.setVisible(False)
        layout_target.addWidget(self.lbl_target_unavailable_warning)

        main_layout.addWidget(self.frame_target)

        # -------------------------------------------------------------
        # 3. Khung Cảnh Báo Bất Định (In-Doubt Ambiguity Frame)
        # -------------------------------------------------------------
        self.frame_in_doubt = QFrame(self)
        self.frame_in_doubt.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_in_doubt.setStyleSheet("""
            QFrame {
                background-color: rgba(234, 88, 12, 0.15);
                border: 1px solid rgba(234, 88, 12, 0.45);
                border-radius: 6px;
                padding: 8px;
            }
        """)
        layout_in_doubt = QVBoxLayout(self.frame_in_doubt)
        lbl_in_doubt_hdr = QLabel("🚨 RANH GIỚI BẤT ĐỊNH (IN-DOUBT ACTION AMBIGUITY)", self.frame_in_doubt)
        lbl_in_doubt_hdr.setTextFormat(Qt.TextFormat.PlainText)
        lbl_in_doubt_hdr.setStyleSheet("font-weight: bold; color: #fdba74; font-size: 12px;")
        layout_in_doubt.addWidget(lbl_in_doubt_hdr)

        self.lbl_in_doubt_warning = QLabel(
            "Tiến trình dừng ngay sau lệnh ACTION_PENDING. Hệ thống không thể chứng minh từ đĩa xem clip đã được "
            "Resolve chèn hay chưa. TUYỆT ĐỐI CẤM TỰ ĐỘNG CHẠY LẠI HOẶC XÓA MÙ. Người vận hành bắt buộc phải đối "
            "chiếu trực quan bằng mắt trên DaVinci Resolve.",
            self.frame_in_doubt,
        )
        self.lbl_in_doubt_warning.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_in_doubt_warning.setWordWrap(True)
        self.lbl_in_doubt_warning.setStyleSheet("color: #ffedd5; font-size: 11.5px;")
        layout_in_doubt.addWidget(self.lbl_in_doubt_warning)

        self.table_in_doubt = QTableWidget(0, 4, self.frame_in_doubt)
        self.table_in_doubt.setHorizontalHeaderLabels(["Seq", "Track", "Tóm tắt tham số", "Lý do bất định"])
        self.table_in_doubt.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_in_doubt.verticalHeader().setVisible(False)
        self.table_in_doubt.horizontalHeader().setStretchLastSection(True)
        self.table_in_doubt.setMaximumHeight(90)
        layout_in_doubt.addWidget(self.table_in_doubt)

        self.frame_in_doubt.setVisible(False)
        main_layout.addWidget(self.frame_in_doubt)

        # -------------------------------------------------------------
        # 4. Bảng Ứng Viên Bù Trừ (Candidate Compensations Table - LIFO)
        # -------------------------------------------------------------
        h_table_hdr = QHBoxLayout()
        lbl_comp_title = QLabel("DANH SÁCH ỨNG VIÊN BÙ TRỪ (THỨ TỰ ĐẢO NGƯỢC LIFO):", self)
        lbl_comp_title.setTextFormat(Qt.TextFormat.PlainText)
        lbl_comp_title.setStyleSheet("font-weight: bold; font-size: 12px; color: #ffffff;")
        h_table_hdr.addWidget(lbl_comp_title)

        self.lbl_compensations_count = QLabel("", self)
        self.lbl_compensations_count.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_compensations_count.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        h_table_hdr.addWidget(self.lbl_compensations_count)
        h_table_hdr.addStretch(1)
        main_layout.addLayout(h_table_hdr)

        self.table_compensations = QTableWidget(0, 6, self)
        self.table_compensations.setHorizontalHeaderLabels([
            "Seq", "Loại thao tác", "Resolve Item ID", "Track", "Tên Clip", "Tóm tắt tham số (Sanitized)"
        ])
        self.table_compensations.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_compensations.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_compensations.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_compensations.verticalHeader().setVisible(False)
        self.table_compensations.horizontalHeader().setStretchLastSection(True)
        self.table_compensations.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_compensations.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_compensations.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_compensations.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_compensations.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table_compensations.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table_compensations.setStyleSheet("""
            QTableWidget {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                gridline-color: #27272a;
                color: #e4e4e7;
                font-size: 11.5px;
            }
            QHeaderView::section {
                background-color: #202024;
                color: #a1a1aa;
                padding: 4px 8px;
                border: none;
                border-right: 1px solid #27272a;
                border-bottom: 1px solid #27272a;
                font-weight: 600;
                font-size: 11.5px;
            }
        """)
        main_layout.addWidget(self.table_compensations, 1)

        self.lbl_table_note = QLabel(
            "LƯU Ý BẢO MẬT: Bảng trên là dữ liệu ghi nhận trong quá khứ của ứng dụng. "
            "Không bảo đảm timeline DaVinci Resolve hiện tại vẫn còn chứa các clip này.",
            self,
        )
        self.lbl_table_note.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_table_note.setStyleSheet("color: #71717a; font-size: 11px; font-style: italic;")
        main_layout.addWidget(self.lbl_table_note)

        # -------------------------------------------------------------
        # 5. Khung Chẩn Đoán Dòng Rách (Torn-Tail Diagnostic Frame)
        # -------------------------------------------------------------
        self.frame_torn_tail = QFrame(self)
        self.frame_torn_tail.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_torn_tail.setStyleSheet("""
            QFrame {
                background-color: rgba(217, 119, 6, 0.15);
                border: 1px solid rgba(217, 119, 6, 0.4);
                border-radius: 6px;
                padding: 8px;
            }
        """)
        layout_torn = QVBoxLayout(self.frame_torn_tail)
        lbl_torn_hdr = QLabel("🔍 CHẨN ĐOÁN DÒNG RÁCH NHẬT KÝ (TORN-TAIL RECORD)", self.frame_torn_tail)
        lbl_torn_hdr.setTextFormat(Qt.TextFormat.PlainText)
        lbl_torn_hdr.setStyleSheet("font-weight: bold; color: #fde68a; font-size: 11.5px;")
        layout_torn.addWidget(lbl_torn_hdr)

        h_torn_info = QHBoxLayout()
        self.lbl_torn_tail_line = QLabel("Dòng: -", self.frame_torn_tail)
        self.lbl_torn_tail_bytes = QLabel("Số byte: 0", self.frame_torn_tail)
        self.lbl_torn_tail_sha = QLabel("SHA-256: -", self.frame_torn_tail)
        for w in (self.lbl_torn_tail_line, self.lbl_torn_tail_bytes, self.lbl_torn_tail_sha):
            w.setTextFormat(Qt.TextFormat.PlainText)
            w.setStyleSheet("color: #fef3c7; font-size: 11px;")
            h_torn_info.addWidget(w)
        h_torn_info.addStretch(1)
        layout_torn.addLayout(h_torn_info)

        self.lbl_torn_tail_preview = QLabel("", self.frame_torn_tail)
        self.lbl_torn_tail_preview.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_torn_tail_preview.setWordWrap(True)
        self.lbl_torn_tail_preview.setStyleSheet("""
            background-color: #1c1917;
            border: 1px solid #44403c;
            border-radius: 4px;
            padding: 4px 6px;
            font-family: monospace;
            font-size: 10.5px;
            color: #fed7aa;
        """)
        layout_torn.addWidget(self.lbl_torn_tail_preview)

        self.frame_torn_tail.setVisible(False)
        main_layout.addWidget(self.frame_torn_tail)

        # -------------------------------------------------------------
        # 6. Thanh Điều Khiển Chỉ Đọc (Action Controls - No Mutation)
        # -------------------------------------------------------------
        h_actions = QHBoxLayout()
        h_actions.setSpacing(10)

        # Nút thực thi bù trừ: KHÓA VĨNH VIỄN
        self.btn_execute_compensation = QPushButton("🔒 Thi công bù trừ đang khóa (ready_to_execute=False)", self)
        self.btn_execute_compensation.setEnabled(False)
        self.btn_execute_compensation.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #71717a;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 600;
            }
        """)
        h_actions.addWidget(self.btn_execute_compensation)
        h_actions.addStretch(1)

        self.btn_refresh = QPushButton("Làm mới (F5)", self)
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3f3f46;
                color: #ffffff;
            }
        """)
        self.btn_refresh.clicked.connect(self._on_refresh)
        h_actions.addWidget(self.btn_refresh)

        self.btn_close = QPushButton("Đóng", self)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #3f3f46;
                color: #ffffff;
                border: 1px solid #52525b;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #52525b;
            }
        """)
        self.btn_close.clicked.connect(self.accept)
        h_actions.addWidget(self.btn_close)

        main_layout.addLayout(h_actions)

    def _make_label(self, text: str) -> QLabel:
        lbl = QLabel(text, self.frame_target)
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setStyleSheet("color: #71717a; font-size: 11.5px;")
        return lbl

    def _setup_shortcuts(self) -> None:
        shortcut_f5 = QShortcut(QKeySequence("F5"), self)
        shortcut_f5.activated.connect(self._on_refresh)

        shortcut_esc = QShortcut(QKeySequence("Esc"), self)
        shortcut_esc.activated.connect(self.reject)

    def _on_refresh(self) -> None:
        if callable(self._refresh_callback):
            self._refresh_callback()

    def show_io_error(self, error_message: str) -> None:
        """Hiển thị trạng thái lỗi truy cập I/O hệ điều hành (Assessment Unavailable Box)."""
        self._io_error = error_message
        self._assessment = None

        self.frame_io_error.setVisible(True)
        self.lbl_io_error_message.setText(error_message)

        # Đặt banner về trạng thái xám than trung tính
        self.frame_header.setStyleSheet("""
            QFrame {
                background-color: #27272a;
                border: 1px solid #52525b;
                border-radius: 6px;
            }
        """)
        self.lbl_status_badge.setText("ASSESSMENT_UNAVAILABLE_READ_ERROR")
        self.lbl_recommendation.setText("KIỂM TRA QUYỀN TRUY CẬP VÀ THỬ LẠI")
        self.lbl_explanation.setText(
            "Không thể đọc hồ sơ do lỗi quyền truy cập hệ thống tệp (EACCES) hoặc lỗi I/O (EIO).\n"
            "Chưa có bằng chứng cấu trúc tệp bị hỏng. Trạng thái phiên hiện KHÔNG XÁC ĐỊNH. "
            "Khóa toàn bộ thao tác thi công; vui lòng kiểm tra quyền tệp trước khi làm mới."
        )

        self.frame_in_doubt.setVisible(False)
        self.frame_torn_tail.setVisible(False)
        self.lbl_target_drift_warning.setVisible(False)
        self.lbl_target_unavailable_warning.setVisible(False)
        self.table_compensations.setRowCount(0)
        self.lbl_compensations_count.setText("Bảng ứng viên rỗng do lỗi đọc")

    def show_assessment(self, assessment: RecoveryAssessment) -> None:
        """Cập nhật giao diện toàn diện từ DTO RecoveryAssessment."""
        self._assessment = assessment
        self._io_error = None
        self.frame_io_error.setVisible(False)

        state = assessment.classified_state
        action = assessment.recommended_action
        explanation = assessment.explanation_for_operator

        if state in ("STATE_CLEAN_TERMINAL", "STATE_INITIALIZED_NO_JOURNAL"):
            explanation += (
                "\n\nLưu ý: Biên nhận và nhật ký chỉ là lịch sử ứng dụng ghi nhận trong quá khứ, "
                "không phải bằng chứng xác thực độc lập rằng timeline DaVinci Resolve hiện tại còn nguyên vẹn 100% hoặc chưa bị chỉnh sửa."
            )
        elif state == "STATE_CLEAN_ACTIVE_LOCKED":
            explanation += (
                "\n\nLưu ý: Khóa cố vấn hệ điều hành đang bị chiếm giữ. Giao diện chỉ cho phép Đóng; cấm mọi thao tác can thiệp."
            )

        self.lbl_status_badge.setText(state)
        self.lbl_recommendation.setText(f"KHUYẾN NGHỊ: {action}")
        self.lbl_explanation.setText(explanation)

        # 1. Áp dụng mã màu cho Header Banner theo nhóm trạng thái
        if state in ("STATE_CLEAN_TERMINAL", "STATE_INITIALIZED_NO_JOURNAL"):
            self.frame_header.setStyleSheet("""
                QFrame {
                    background-color: #064e3b;
                    border: 1px solid #059669;
                    border-radius: 6px;
                }
            """)
        elif state == "STATE_CLEAN_ACTIVE_LOCKED":
            self.frame_header.setStyleSheet("""
                QFrame {
                    background-color: #312e81;
                    border: 1px solid #6366f1;
                    border-radius: 6px;
                }
            """)
        elif state in ("STATE_COMMITTED_CANDIDATE_COMPENSATION", "STATE_TORN_TAIL_DETECTED"):
            self.frame_header.setStyleSheet("""
                QFrame {
                    background-color: #78350f;
                    border: 1px solid #d97706;
                    border-radius: 6px;
                }
            """)
        elif state == "STATE_AMBIGUOUS_IN_DOUBT":
            self.frame_header.setStyleSheet("""
                QFrame {
                    background-color: #7c2d12;
                    border: 1px solid #ea580c;
                    border-radius: 6px;
                }
            """)
        elif state == "STATE_ARTIFACT_CORRUPTED_OR_MISSING":
            self.frame_header.setStyleSheet("""
                QFrame {
                    background-color: #7f1d1d;
                    border: 1px solid #dc2626;
                    border-radius: 6px;
                }
            """)
        else:
            self.frame_header.setStyleSheet("""
                QFrame {
                    background-color: #27272a;
                    border: 1px solid #52525b;
                    border-radius: 6px;
                }
            """)

        # 2. Thẻ danh tính đích
        self.lbl_exec_id.setText(assessment.execution_id)
        idemp_short = f"{assessment.idempotency_key[:12]}..." if len(assessment.idempotency_key) > 12 else assessment.idempotency_key
        self.lbl_idemp_key.setText(idemp_short)
        self.lbl_lock_status.setText("ĐANG BỊ CHIẾM (Held)" if assessment.is_advisory_lock_held else "RẢNH (Free)")
        self.lbl_pid.setText(str(assessment.diagnostic_recorded_pid) if assessment.diagnostic_recorded_pid is not None else "Không ghi nhận")

        rep = assessment.target_match_report
        p_receipt = rep.receipt_project_id or "-"
        p_active = rep.active_project_id or "-"
        self.lbl_project_match.setText(f"{p_receipt} (Active: {p_active})")

        tl_receipt = rep.receipt_timeline_id or "-"
        tl_active = rep.active_timeline_id or "-"
        self.lbl_timeline_match.setText(f"{tl_receipt} (Active: {tl_active})")

        self.lbl_target_drift_warning.setVisible(rep.drift_detected)
        self.lbl_target_unavailable_warning.setVisible(not rep.authority_available)

        # 3. Khung cảnh báo bất định
        if state == "STATE_AMBIGUOUS_IN_DOUBT":
            self.frame_in_doubt.setVisible(True)
            self._render_ambiguous_actions(assessment.ambiguous_actions)
        else:
            self.frame_in_doubt.setVisible(False)

        # 4. Bảng ứng viên bù trừ LIFO (giới hạn trần 100 dòng)
        self._render_candidate_compensations(assessment.candidate_compensations)

        # 5. Khung dòng rách
        if assessment.torn_tail_detected:
            self.frame_torn_tail.setVisible(True)
            self.lbl_torn_tail_line.setText(f"Dòng: {assessment.torn_tail_line_number or '-'}")
            self.lbl_torn_tail_bytes.setText(f"Số byte: {assessment.torn_tail_byte_count}")
            self.lbl_torn_tail_sha.setText(f"SHA-256: {assessment.torn_tail_sha256 or '-'}")
            self.lbl_torn_tail_preview.setText(assessment.torn_tail_preview_escaped or "")
        else:
            self.frame_torn_tail.setVisible(False)

    def _render_ambiguous_actions(self, actions: Sequence[AmbiguousActionDetail]) -> None:
        self.table_in_doubt.setRowCount(len(actions))
        for r_idx, act in enumerate(actions):
            seq_val = getattr(act, "seq", getattr(act, "sequence_number", 0))
            it_seq = QTableWidgetItem(str(seq_val))
            it_trk = QTableWidgetItem(f"{act.track_type}:{act.track_index}")
            it_prm = QTableWidgetItem(act.action_params_summary)
            it_rsn = QTableWidgetItem(act.uncertainty_reason)
            for it in (it_seq, it_trk, it_prm, it_rsn):
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_in_doubt.setItem(r_idx, 0, it_seq)
            self.table_in_doubt.setItem(r_idx, 1, it_trk)
            self.table_in_doubt.setItem(r_idx, 2, it_prm)
            self.table_in_doubt.setItem(r_idx, 3, it_rsn)

    def _render_candidate_compensations(self, actions: Sequence[CandidateCompensatingAction]) -> None:
        total = len(actions)
        display_actions = actions[:MAX_UI_ROWS]

        if total > MAX_UI_ROWS:
            self.lbl_compensations_count.setText(f"Hiển thị {len(display_actions)}/tổng số {total} ứng viên")
        elif total > 0:
            self.lbl_compensations_count.setText(f"Tổng số: {total} ứng viên")
        else:
            self.lbl_compensations_count.setText("Không có ứng viên nào")

        self.table_compensations.setRowCount(len(display_actions))
        for r_idx, act in enumerate(display_actions):
            seq_val = getattr(act, "seq", getattr(act, "sequence_number", 0))
            it0 = QTableWidgetItem(str(seq_val))
            it1 = QTableWidgetItem(act.action_type)
            it2 = QTableWidgetItem(act.resolve_item_id or "-")
            it3 = QTableWidgetItem(f"{act.track_type}:{act.track_index}")
            it4 = QTableWidgetItem(act.clip_name or "-")
            it5 = QTableWidgetItem(act.action_params_summary)

            for it in (it0, it1, it2, it3, it4, it5):
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)

            self.table_compensations.setItem(r_idx, 0, it0)
            self.table_compensations.setItem(r_idx, 1, it1)
            self.table_compensations.setItem(r_idx, 2, it2)
            self.table_compensations.setItem(r_idx, 3, it3)
            self.table_compensations.setItem(r_idx, 4, it4)
            self.table_compensations.setItem(r_idx, 5, it5)
