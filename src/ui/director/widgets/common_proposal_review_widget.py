"""
src/ui/director/widgets/common_proposal_review_widget.py
=============================================================================
Khung Đánh giá Kịch bản Toàn bản (Common Proposal Review Widget) — G06-06C3-S3:
- Hiển thị toàn cảnh đề xuất kịch bản biên tập (EditPlanProposal) gồm 3 bảng Model/View:
  1. CutsTableModel: Đường cắt chính (Primary cuts từ video_plan).
  2. AssignmentsTableModel: Tài nguyên được đề xuất gán (B-roll/SFX từ sidecar).
  3. OmissionsTableModel: Các mục đề xuất lược bỏ (B-roll/SFX theo điều khoản D02).
- Thẩm định trạng thái trước khi duyệt (Preflight Checklist):
  + Schema kịch bản: Hợp lệ / Không hợp lệ.
  + Manifest nguồn: Khớp loaded manifest (ghi rõ: file freshness not checked / G07).
  + Hình học khung hình (Geometry): UNRESOLVED (chưa có bằng chứng frame) / VERIFIED / BLOCKED.
  + Hồ sơ phê duyệt: Chưa duyệt / Đã duyệt toàn bản / Kịch bản cũ cần cập nhật.
- Hành động phê duyệt biên tập:
  + Nút '✓ Phê duyệt Kịch bản Toàn bản' phát tín hiệu proposal_approved(proposal_id, revision).
  + Bắt buộc có thông báo: "Duyệt nội dung; chưa kiểm tra/thi công Resolve (G07)".
  + Banner lược bỏ: Thông báo rõ toàn bộ đề xuất lược bỏ sẽ được chấp thuận khi phê duyệt.
- Tương thích kích thước: Thiết kế gọn gàng dạng TabWidget hoạt động mượt mà ở 1200x750 & 1280x800.
=============================================================================
"""

from typing import Any, Dict, List, Mapping, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    verify_proposal_approval_binding,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.plan_geometry_contracts import (
    PlanGeometryAssemblyResult,
    PlanGeometryIssue,
)


class CutsTableModel(QAbstractTableModel):
    """Model hiển thị bảng đường cắt chính (Primary Cuts) từ video_plan."""

    COLUMNS = [
        "STT",
        "Clip ID",
        "In (s)",
        "Out (s)",
        "Thời lượng (s)",
        "Hành động",
        "Khung hình (Geometry)",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: List[Dict[str, Any]] = []
        self._frame_info: List[str] = []

    def set_data(
        self,
        segments: List[Dict[str, Any]],
        geometry_result: Optional[Any] = None,
    ) -> None:
        self.beginResetModel()
        self._segments = list(segments)
        self._frame_info = []

        # Trích xuất thông tin khung hình thực từ geometry_result (nếu có bằng chứng thực sự)
        assembled = getattr(geometry_result, "assembled_segments", None) if geometry_result else None
        for idx, seg in enumerate(self._segments):
            if assembled and idx < len(assembled):
                geom_seg = assembled[idx]
                sf = getattr(geom_seg, "source_start_frame", None)
                ef = getattr(geom_seg, "source_end_frame", None)
                df = getattr(geom_seg, "duration_frames", None)
                if sf is not None and ef is not None:
                    self._frame_info.append(f"{sf} - {ef} ({df}f)")
                else:
                    self._frame_info.append("— (Chưa có bằng chứng frame)")
            else:
                self._frame_info.append("— (Chưa có bằng chứng frame)")

        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._segments = []
        self._frame_info = []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._segments)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._segments)):
            return None

        seg = self._segments[index.row()]
        col = index.column()

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if col == 0:
                return str(index.row() + 1)
            if col == 1:
                return str(seg.get("clip_id", "—"))
            if col == 2:
                s_in = seg.get("start_sec", seg.get("source_in_sec", 0.0))
                return f"{float(s_in):.2f}"
            if col == 3:
                s_out = seg.get("end_sec", seg.get("source_out_sec", 0.0))
                return f"{float(s_out):.2f}"
            if col == 4:
                s_in = float(seg.get("start_sec", seg.get("source_in_sec", 0.0)))
                s_out = float(seg.get("end_sec", seg.get("source_out_sec", 0.0)))
                return f"{max(0.0, s_out - s_in):.2f}"
            if col == 5:
                return str(seg.get("action", "keep"))
            if col == 6:
                if index.row() < len(self._frame_info):
                    return self._frame_info[index.row()]
                return "— (Chưa có bằng chứng frame)"

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (0, 2, 3, 4, 5):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 5:
                act = str(seg.get("action", "keep")).lower()
                if act == "cut":
                    return QColor("#f87171")
                if act == "speedup":
                    return QColor("#38bdf8")
                return QColor("#4ade80")
            if col == 6:
                text = self._frame_info[index.row()] if index.row() < len(self._frame_info) else ""
                if "Chưa có bằng chứng" in text:
                    return QColor("#a1a1aa")
                return QColor("#34d399")

        return None


class AssignmentsTableModel(QAbstractTableModel):
    """Model hiển thị bảng tài nguyên đề xuất gán (Proposed Asset Assignments)."""

    COLUMNS = [
        "Demand ID",
        "Phân loại",
        "Vị trí (s)",
        "Thời lượng (s)",
        "Asset ID",
        "Nguồn (Source)",
        "Kho lưu trữ (Origin)",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._assignments: List[Dict[str, Any]] = []

    def set_data(self, assignments: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._assignments = list(assignments)
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._assignments = []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._assignments)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._assignments)):
            return None

        row = self._assignments[index.row()]
        col = index.column()

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if col == 0:
                return str(row.get("demand_id", "—"))
            if col == 1:
                return str(row.get("slot", "—")).upper()
            if col == 2:
                return f"{float(row.get('timeline_sec', 0.0)):.2f}"
            if col == 3:
                return f"{float(row.get('duration_sec', 0.0)):.2f}"
            if col == 4:
                return str(row.get("asset_id", "—"))
            if col == 5:
                return str(row.get("source_kind", "—"))
            if col == 6:
                return str(row.get("origin_library_id", "—"))

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (1, 2, 3, 5):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 1:
                slot = str(row.get("slot", "")).lower()
                return QColor("#c084fc") if "broll" in slot else QColor("#38bdf8")
            if col == 4:
                return QColor("#4ade80")

        return None

    def get_demand_id(self, row_idx: int) -> Optional[str]:
        if 0 <= row_idx < len(self._assignments):
            return self._assignments[row_idx].get("demand_id")
        return None


class OmissionsTableModel(QAbstractTableModel):
    """Model hiển thị bảng các mục đề xuất lược bỏ (Proposed Omissions)."""

    COLUMNS = [
        "Demand ID",
        "Phân loại",
        "Vị trí (s)",
        "Thời lượng (s)",
        "Lý do lược bỏ",
        "Cảnh báo",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._omissions: List[Dict[str, Any]] = []

    def set_data(self, omissions: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._omissions = list(omissions)
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._omissions = []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._omissions)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._omissions)):
            return None

        row = self._omissions[index.row()]
        col = index.column()

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if col == 0:
                return str(row.get("demand_id", "—"))
            if col == 1:
                return str(row.get("slot", "—")).upper()
            if col == 2:
                return f"{float(row.get('timeline_sec', 0.0)):.2f}"
            if col == 3:
                return f"{float(row.get('duration_sec', 0.0)):.2f}"
            if col == 4:
                return str(row.get("reason", "—"))
            if col == 5:
                return str(row.get("warning_message", "—"))

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (1, 2, 3):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 4:
                return QColor("#fbbf24")
            if col == 5:
                return QColor("#f87171")

        return None

    def get_demand_id(self, row_idx: int) -> Optional[str]:
        if 0 <= row_idx < len(self._omissions):
            return self._omissions[row_idx].get("demand_id")
        return None


class CommonProposalReviewWidget(QFrame):
    """Widget Đánh giá Kịch bản Toàn bản độc lập, tích hợp vào Step 3 ReviewPage."""

    # Tín hiệu phát khi người dùng xác nhận duyệt đề xuất hiện hành
    proposal_approved = Signal(str, str)  # (proposal_id, proposal_revision)

    # Tín hiệu yêu cầu chọn ứng viên khác hoặc lược bỏ một demand
    candidate_selected = Signal(str, str)  # (demand_id, asset_id)
    candidate_omitted = Signal(str)        # (demand_id)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._proposal: Optional[EditPlanProposal] = None
        self._proposal_revision: str = ""
        self._geometry_result: Optional[Any] = None
        self._approval_record: Optional[AppOwnedApprovalRecord] = None
        self._sidecar: Optional[Any] = None
        self._is_stale: bool = False
        self._schema_valid: bool = False
        self._error_message: str = ""

        self._init_ui()

    def _init_ui(self) -> None:
        self.setObjectName("common_proposal_review_widget")
        self.setStyleSheet("""
            QFrame#common_proposal_review_widget {
                background-color: #141418;
                border: 1px solid #282832;
                border-radius: 9px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # 1. Header Bar: Tiêu đề & Thông tin kịch bản
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        self.lbl_title = QLabel("📋 ĐỀ XUẤT KỊCH BẢN TOÀN BẢN (COMMON REVIEW)")
        self.lbl_title.setStyleSheet("color: #f4f4f6; font-size: 12px; font-weight: 700; letter-spacing: 0.3px;")
        header_row.addWidget(self.lbl_title)

        header_row.addStretch(1)

        self.lbl_proposal_meta = QLabel("Chưa có đề xuất nào được nạp")
        self.lbl_proposal_meta.setStyleSheet("color: #9ca3af; font-size: 10.5px; font-weight: 500;")
        header_row.addWidget(self.lbl_proposal_meta)

        layout.addLayout(header_row)

        # 2. Preflight Checklist / Trạng thái Thẩm định
        preflight_box = QFrame(self)
        preflight_box.setStyleSheet("""
            QFrame {
                background-color: #1a1a20;
                border: 1px solid #292933;
                border-radius: 7px;
                padding: 4px;
            }
        """)
        h_preflight = QHBoxLayout(preflight_box)
        h_preflight.setContentsMargins(8, 4, 8, 4)
        h_preflight.setSpacing(12)

        lbl_pf_title = QLabel("Thẩm định tiền kiểm:")
        lbl_pf_title.setStyleSheet("color: #8b8b97; font-size: 10px; font-weight: 700; border: none;")
        h_preflight.addWidget(lbl_pf_title)

        self.badge_schema = QLabel("SCHEMA: CHƯA KIỂM TRA")
        self.badge_schema.setStyleSheet("color: #a1a1aa; font-size: 10px; font-weight: 600; border: none;")
        h_preflight.addWidget(self.badge_schema)

        self.badge_manifest = QLabel("MANIFEST: CHƯA GẮN")
        self.badge_manifest.setStyleSheet("color: #a1a1aa; font-size: 10px; font-weight: 600; border: none;")
        h_preflight.addWidget(self.badge_manifest)

        self.badge_geometry = QLabel("HÌNH HỌC: CHƯA KIỂM TRA")
        self.badge_geometry.setStyleSheet("color: #a1a1aa; font-size: 10px; font-weight: 600; border: none;")
        h_preflight.addWidget(self.badge_geometry)

        self.badge_approval = QLabel("TRẠNG THÁI: CHƯA DUYỆT")
        self.badge_approval.setStyleSheet("color: #a1a1aa; font-size: 10px; font-weight: 600; border: none;")
        h_preflight.addWidget(self.badge_approval)

        h_preflight.addStretch(1)
        layout.addWidget(preflight_box)

        # 3. Tab Widget chứa 3 bảng dữ liệu (Chuẩn NLE Studio Table)
        self.tab_widget = QTabWidget(self)
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #282832;
                border-radius: 6px;
                background-color: #101014;
            }
            QTabBar::tab {
                background: #18181e;
                color: #9ca3af;
                border: 1px solid #282832;
                border-bottom: none;
                padding: 5px 12px;
                font-size: 10.5px;
                font-weight: 600;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                margin-right: 2px;
            }
            QTabBar::tab:hover {
                background: #202028;
                color: #e4e4e7;
            }
            QTabBar::tab:selected {
                background: #262630;
                color: #ffffff;
                font-weight: 700;
                border-color: #3f3f4e;
                border-top: 2px solid #8b5cf6;
            }
            QTableView {
                background-color: #101014;
                alternate-background-color: #141419;
                gridline-color: #22222a;
                border: none;
                color: #e4e4e7;
                font-size: 10.5px;
                selection-background-color: rgba(124, 58, 237, 0.30);
                selection-color: #ffffff;
            }
            QTableView::item {
                padding: 2px 5px;
            }
            QTableView::item:selected {
                background-color: rgba(124, 58, 237, 0.34);
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #18181f;
                color: #a1a1aa;
                border: none;
                border-right: 1px solid #262630;
                border-bottom: 1px solid #262630;
                padding: 4px 6px;
                font-size: 10px;
                font-weight: 700;
            }
        """)

        # Tab 1: Primary Cuts
        self.model_cuts = CutsTableModel(self)
        self.tbl_cuts = QTableView(self)
        self.tbl_cuts.setModel(self.model_cuts)
        self.tbl_cuts.setAlternatingRowColors(True)
        self.tbl_cuts.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tbl_cuts.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.tbl_cuts.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.tbl_cuts.horizontalHeader().setDefaultSectionSize(76)
        self.tbl_cuts.horizontalHeader().setMinimumSectionSize(44)
        self.tbl_cuts.horizontalHeader().setStretchLastSection(True)
        self.tbl_cuts.verticalHeader().setVisible(False)
        self.tbl_cuts.verticalHeader().setDefaultSectionSize(24)
        self.tbl_cuts.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.tbl_cuts.setMinimumHeight(120)
        self.tab_widget.addTab(self.tbl_cuts, "🎬 Đường cắt chính (0)")

        # Tab 2: Proposed Assignments
        pane_assignments = QWidget(self)
        v_assign = QVBoxLayout(pane_assignments)
        v_assign.setContentsMargins(0, 0, 0, 0)
        v_assign.setSpacing(4)

        self.model_assignments = AssignmentsTableModel(self)
        self.tbl_assignments = QTableView(pane_assignments)
        self.tbl_assignments.setModel(self.model_assignments)
        self.tbl_assignments.setAlternatingRowColors(True)
        self.tbl_assignments.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tbl_assignments.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.tbl_assignments.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.tbl_assignments.horizontalHeader().setDefaultSectionSize(74)
        self.tbl_assignments.horizontalHeader().setMinimumSectionSize(44)
        self.tbl_assignments.horizontalHeader().setStretchLastSection(True)
        self.tbl_assignments.verticalHeader().setVisible(False)
        self.tbl_assignments.verticalHeader().setDefaultSectionSize(24)
        self.tbl_assignments.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.tbl_assignments.setMinimumHeight(100)
        v_assign.addWidget(self.tbl_assignments)

        # Thanh công cụ phụ dưới bảng gán: Cho phép đổi ứng viên hoặc lược bỏ mục
        bar_assign_tools = QHBoxLayout()
        bar_assign_tools.setContentsMargins(4, 2, 4, 2)
        bar_assign_tools.setSpacing(6)

        self.lbl_assign_candidate = QLabel("Ứng viên:")
        self.lbl_assign_candidate.setStyleSheet("color: #a1a1aa; font-size: 10px;")
        bar_assign_tools.addWidget(self.lbl_assign_candidate)

        self.combo_assign_candidate = QComboBox(pane_assignments)
        self.combo_assign_candidate.setStyleSheet("""
            QComboBox {
                background: #18181b;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10px;
                min-width: 140px;
            }
        """)
        self.combo_assign_candidate.setEnabled(False)
        bar_assign_tools.addWidget(self.combo_assign_candidate)

        self.btn_apply_candidate = QPushButton("Đổi ứng viên")
        self.btn_apply_candidate.setStyleSheet("""
            QPushButton {
                background: #3b82f6;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover { background: #2563eb; }
            QPushButton:disabled { background: #27272a; color: #52525b; }
        """)
        self.btn_apply_candidate.setEnabled(False)
        self.btn_apply_candidate.clicked.connect(self._on_apply_candidate_clicked)
        bar_assign_tools.addWidget(self.btn_apply_candidate)

        self.btn_omit_selected = QPushButton("Lược bỏ mục đã chọn")
        self.btn_omit_selected.setStyleSheet("""
            QPushButton {
                background: #27272a;
                color: #fbbf24;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
            }
            QPushButton:hover {
                background: #3f3f46;
                color: #ffffff;
            }
            QPushButton:disabled {
                color: #52525b;
                border-color: #27272a;
            }
        """)
        self.btn_omit_selected.setEnabled(False)
        self.btn_omit_selected.clicked.connect(self._on_omit_selected_clicked)
        bar_assign_tools.addWidget(self.btn_omit_selected)
        bar_assign_tools.addStretch(1)
        v_assign.addLayout(bar_assign_tools)

        self.tbl_assignments.selectionModel().selectionChanged.connect(self._on_assignment_selection_changed)
        self.tab_widget.addTab(pane_assignments, "✨ Tài nguyên đề xuất (0)")

        # Tab 3: Proposed Omissions
        pane_omissions = QWidget(self)
        v_omit = QVBoxLayout(pane_omissions)
        v_omit.setContentsMargins(0, 0, 0, 0)
        v_omit.setSpacing(4)

        self.model_omissions = OmissionsTableModel(self)
        self.tbl_omissions = QTableView(pane_omissions)
        self.tbl_omissions.setModel(self.model_omissions)
        self.tbl_omissions.setAlternatingRowColors(True)
        self.tbl_omissions.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tbl_omissions.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.tbl_omissions.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.tbl_omissions.horizontalHeader().setDefaultSectionSize(78)
        self.tbl_omissions.horizontalHeader().setMinimumSectionSize(44)
        self.tbl_omissions.horizontalHeader().setStretchLastSection(True)
        self.tbl_omissions.verticalHeader().setVisible(False)
        self.tbl_omissions.verticalHeader().setDefaultSectionSize(24)
        self.tbl_omissions.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.tbl_omissions.setMinimumHeight(100)
        v_omit.addWidget(self.tbl_omissions)

        bar_omit_tools = QHBoxLayout()
        bar_omit_tools.setContentsMargins(4, 2, 4, 2)
        bar_omit_tools.setSpacing(6)

        self.lbl_restore_candidate = QLabel("Khôi phục với:")
        self.lbl_restore_candidate.setStyleSheet("color: #a1a1aa; font-size: 10px;")
        bar_omit_tools.addWidget(self.lbl_restore_candidate)

        self.combo_restore_candidate = QComboBox(pane_omissions)
        self.combo_restore_candidate.setStyleSheet("""
            QComboBox {
                background: #18181b;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10px;
                min-width: 140px;
            }
        """)
        self.combo_restore_candidate.setEnabled(False)
        bar_omit_tools.addWidget(self.combo_restore_candidate)

        self.btn_restore_selected = QPushButton("Khôi phục mục đã chọn")
        self.btn_restore_selected.setStyleSheet("""
            QPushButton {
                background: #10b981;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover { background: #059669; }
            QPushButton:disabled { background: #27272a; color: #52525b; }
        """)
        self.btn_restore_selected.setEnabled(False)
        self.btn_restore_selected.clicked.connect(self._on_restore_selected_clicked)
        bar_omit_tools.addWidget(self.btn_restore_selected)
        bar_omit_tools.addStretch(1)
        v_omit.addLayout(bar_omit_tools)

        self.tbl_omissions.selectionModel().selectionChanged.connect(self._on_omission_selection_changed)
        self.tab_widget.addTab(pane_omissions, "⚠️ Mục đề xuất lược bỏ (0)")

        layout.addWidget(self.tab_widget)

        # 4. Banner Cảnh báo Lược bỏ (Omission Acceptance Banner)
        self.banner_omissions = QLabel("")
        self.banner_omissions.setStyleSheet("color: #fbbf24; font-size: 10.5px; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.25); border-radius: 4px; padding: 4px 8px;")
        self.banner_omissions.setWordWrap(True)
        self.banner_omissions.setVisible(False)
        layout.addWidget(self.banner_omissions)

        # 5. Thanh Điều khiển Phê duyệt & Cảnh báo Pháp lý
        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        # Cảnh báo ranh giới pháp lý bắt buộc (Strict Warning)
        self.lbl_legal_warning = QLabel(
            "⚠️ Duyệt nội dung biên tập; chưa kiểm tra/thi công DaVinci Resolve (G07)."
        )
        self.lbl_legal_warning.setStyleSheet("color: #71717a; font-size: 10px; font-style: italic;")
        self.lbl_legal_warning.setWordWrap(True)
        action_row.addWidget(self.lbl_legal_warning, stretch=1)

        # Nút phê duyệt kịch bản toàn bản
        self.btn_approve_proposal = QPushButton("✓ Phê duyệt Kịch bản Toàn bản")
        self.btn_approve_proposal.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_approve_proposal.setStyleSheet("""
            QPushButton {
                background-color: #10b981;
                color: #ffffff;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 6px;
                font-size: 11.5px;
                border: none;
            }
            QPushButton:hover {
                background-color: #059669;
            }
            QPushButton:disabled {
                background-color: #27272a;
                color: #71717a;
            }
        """)
        self.btn_approve_proposal.setEnabled(False)
        self.btn_approve_proposal.clicked.connect(self._on_approve_clicked)
        action_row.addWidget(self.btn_approve_proposal)

        layout.addLayout(action_row)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def set_proposal(
        self,
        proposal: Optional[EditPlanProposal],
        geometry_result: Optional[Any] = None,
        approval: Optional[AppOwnedApprovalRecord] = None,
        sidecar: Optional[Any] = None,
    ) -> None:
        """Nạp và hiển thị toàn bộ nội dung của đề xuất kịch bản đã chuẩn hóa."""
        if proposal is None:
            self.clear()
            return

        try:
            owned_prop = normalize_edit_plan_proposal(proposal)
            rev = compute_proposal_revision(owned_prop)
        except Exception as e:
            self.set_error(f"Lỗi chuẩn hóa proposal: {e}")
            return

        self._proposal = owned_prop
        self._proposal_revision = rev
        self._sidecar = sidecar
        self._approval_record = None
        self._is_stale = False
        self._schema_valid = True
        self._error_message = ""

        # Thẩm định an toàn kết quả hình học:
        # Bắt buộc phải là thể hiện thực sự của PlanGeometryAssemblyResult và rehydrate toàn bộ qua constructor
        self._geometry_result = None
        if geometry_result is not None:
            if not isinstance(geometry_result, PlanGeometryAssemblyResult):
                self._geometry_result = PlanGeometryAssemblyResult(
                    status="GEOMETRY_BLOCKED",
                    proposal_id=owned_prop.proposal_id,
                    proposal_revision=rev,
                    timeline_fps=RationalFPS(24, 1),
                    assembled_segments=(),
                    total_timeline_frames=None,
                    total_timeline_duration_sec=None,
                    issues=(PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_UNTYPED_GEOMETRY_OBJECT",
                        clip_id=None,
                        segment_index=None,
                        message=f"geometry_result có kiểu {type(geometry_result).__name__} không được chấp nhận. Bắt buộc là PlanGeometryAssemblyResult.",
                    ),),
                    unresolved_reviews=(),
                )
            else:
                try:
                    # Rehydrate TOÀN BỘ các trường qua constructor để kích hoạt __post_init__ xác thực và sở hữu an toàn
                    rehydrated_geom = PlanGeometryAssemblyResult(
                        status=geometry_result.status,
                        proposal_id=geometry_result.proposal_id,
                        proposal_revision=geometry_result.proposal_revision,
                        timeline_fps=geometry_result.timeline_fps,
                        assembled_segments=geometry_result.assembled_segments,
                        total_timeline_frames=geometry_result.total_timeline_frames,
                        total_timeline_duration_sec=geometry_result.total_timeline_duration_sec,
                        issues=geometry_result.issues,
                        unresolved_reviews=geometry_result.unresolved_reviews,
                    )
                    # Xác thực ràng buộc proposal_id và proposal_revision
                    if (
                        rehydrated_geom.proposal_id == owned_prop.proposal_id
                        and rehydrated_geom.proposal_revision == rev
                        and rehydrated_geom.status in ("GEOMETRY_VERIFIED", "GEOMETRY_UNRESOLVED", "GEOMETRY_BLOCKED")
                    ):
                        self._geometry_result = rehydrated_geom
                    else:
                        self._geometry_result = PlanGeometryAssemblyResult(
                            status="GEOMETRY_BLOCKED",
                            proposal_id=owned_prop.proposal_id,
                            proposal_revision=rev,
                            timeline_fps=getattr(rehydrated_geom, "timeline_fps", RationalFPS(24, 1)),
                            assembled_segments=(),
                            total_timeline_frames=None,
                            total_timeline_duration_sec=None,
                            issues=(PlanGeometryIssue(
                                severity="BLOCKING",
                                issue_code="ERR_GEOMETRY_BINDING_MISMATCH",
                                clip_id=None,
                                segment_index=None,
                                message=f"geometry binding mismatch (prop={rehydrated_geom.proposal_id}/{rehydrated_geom.proposal_revision[:8]} vs actual={owned_prop.proposal_id}/{rev[:8]})",
                            ),),
                            unresolved_reviews=(),
                        )
                except Exception as err:
                    self._geometry_result = PlanGeometryAssemblyResult(
                        status="GEOMETRY_BLOCKED",
                        proposal_id=owned_prop.proposal_id,
                        proposal_revision=rev,
                        timeline_fps=RationalFPS(24, 1),
                        assembled_segments=(),
                        total_timeline_frames=None,
                        total_timeline_duration_sec=None,
                        issues=(PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_GEOMETRY_REHYDRATION_FAILED",
                            clip_id=None,
                            segment_index=None,
                            message=f"Lỗi tái tạo geometry: {err}",
                        ),),
                        unresolved_reviews=(),
                    )

        # Kiểm tra hồ sơ duyệt (nếu có)
        if approval is not None:
            try:
                is_bound = verify_proposal_approval_binding(owned_prop, approval)
                if is_bound:
                    self._approval_record = approval
            except Exception:
                self._approval_record = None

        self._refresh_display()

    def set_stale(self, is_stale: bool = True) -> None:
        """Đánh dấu kịch bản đã bị thay đổi (stale), vô hiệu hóa hồ sơ phê duyệt hiện hành."""
        self._is_stale = is_stale
        if is_stale:
            self._approval_record = None
            self.btn_approve_proposal.setEnabled(False)
        self._update_approval_status()

    def set_error(self, error_message: str) -> None:
        """Hiển thị trạng thái lỗi schema hoặc dữ liệu kịch bản."""
        self._proposal = None
        self._proposal_revision = ""
        self._geometry_result = None
        self._approval_record = None
        self._is_stale = False
        self._schema_valid = False
        self._error_message = str(error_message)

        self.model_cuts.clear()
        self.model_assignments.clear()
        self.model_omissions.clear()

        self.lbl_proposal_meta.setText(f"Lỗi: {error_message}")
        self.lbl_proposal_meta.setStyleSheet("color: #f87171; font-size: 10px;")

        self.badge_schema.setText("SCHEMA: KHÔNG HỢP LỆ")
        self.badge_schema.setStyleSheet("color: #f87171; font-size: 10px; font-weight: 600;")

        self.badge_manifest.setText("MANIFEST: CHƯA GẮN")
        self.badge_manifest.setStyleSheet("color: #71717a; font-size: 10px; font-weight: 600;")

        self.badge_geometry.setText("HÌNH HỌC: BỊ CHẶN")
        self.badge_geometry.setStyleSheet("color: #71717a; font-size: 10px; font-weight: 600;")

        self.badge_approval.setText("TRẠNG THÁI: KHÔNG THỂ DUYỆT")
        self.badge_approval.setStyleSheet("color: #71717a; font-size: 10px; font-weight: 600;")

        self.banner_omissions.setVisible(False)
        self.btn_approve_proposal.setEnabled(False)

    def clear(self) -> None:
        """Xóa trắng giao diện về trạng thái khởi tạo ban đầu."""
        self._proposal = None
        self._proposal_revision = ""
        self._geometry_result = None
        self._approval_record = None
        self._is_stale = False
        self._schema_valid = False
        self._error_message = ""

        self.model_cuts.clear()
        self.model_assignments.clear()
        self.model_omissions.clear()

        self.tab_widget.setTabText(0, "🎬 Đường cắt chính (0)")
        self.tab_widget.setTabText(1, "✨ Tài nguyên đề xuất (0)")
        self.tab_widget.setTabText(2, "⚠️ Mục đề xuất lược bỏ (0)")

        self.lbl_proposal_meta.setText("Chưa có đề xuất nào được nạp")
        self.lbl_proposal_meta.setStyleSheet("color: #a1a1aa; font-size: 10.5px;")

        self.badge_schema.setText("SCHEMA: CHƯA KIỂM TRA")
        self.badge_schema.setStyleSheet("color: #71717a; font-size: 10px; font-weight: 600;")

        self.badge_manifest.setText("MANIFEST: CHƯA GẮN")
        self.badge_manifest.setStyleSheet("color: #71717a; font-size: 10px; font-weight: 600;")

        self.badge_geometry.setText("HÌNH HỌC: CHƯA KIỂM TRA")
        self.badge_geometry.setStyleSheet("color: #71717a; font-size: 10px; font-weight: 600;")

        self.badge_approval.setText("TRẠNG THÁI: CHƯA DUYỆT")
        self.badge_approval.setStyleSheet("color: #71717a; font-size: 10px; font-weight: 600;")

        self.banner_omissions.setVisible(False)
        self.btn_approve_proposal.setEnabled(False)

    # -------------------------------------------------------------------------
    # Internal Handlers
    # -------------------------------------------------------------------------

    def _refresh_display(self) -> None:
        if self._proposal is None:
            self.clear()
            return

        # 1. Cập nhật Model cho 3 bảng
        segments = self._proposal.video_plan.get("timeline_segments", [])
        self.model_cuts.set_data(segments, self._geometry_result)
        self.tab_widget.setTabText(0, f"🎬 Đường cắt chính ({len(segments)})")

        assignments = self._proposal.proposed_asset_assignments
        self.model_assignments.set_data(assignments)
        self.tab_widget.setTabText(1, f"✨ Tài nguyên đề xuất ({len(assignments)})")

        omissions = self._proposal.proposed_omissions
        self.model_omissions.set_data(omissions)
        self.tab_widget.setTabText(2, f"⚠️ Mục đề xuất lược bỏ ({len(omissions)})")

        # 2. Header metadata
        style_name = getattr(self._proposal.creative_context, "style_preset", None) or getattr(self._proposal.creative_context, "style_preset_name", None) or "Mặc định"
        rev_short = self._proposal_revision[:10]
        self.lbl_proposal_meta.setText(
            f"ID: {self._proposal.proposal_id} | Rev: {rev_short}... | Manifest: {self._proposal.manifest_id} | Phong cách: {style_name}"
        )
        self.lbl_proposal_meta.setStyleSheet("color: #a1a1aa; font-size: 10px;")

        # 3. Preflight Checklist badges
        self.badge_schema.setText("✓ SCHEMA_VALID")
        self.badge_schema.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: 600;")

        self.badge_manifest.setText("✓ Khớp loaded manifest (Chưa kiểm tra file disk/G07)")
        self.badge_manifest.setStyleSheet("color: #38bdf8; font-size: 10px; font-weight: 600;")

        # Geometry badge
        if isinstance(self._geometry_result, PlanGeometryAssemblyResult) and self._geometry_result.status == "GEOMETRY_VERIFIED":
            self.badge_geometry.setText("✓ HÌNH HỌC: ĐÃ XÁC THỰC")
            self.badge_geometry.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: 600;")
        elif getattr(self._geometry_result, "status", None) == "GEOMETRY_BLOCKED":
            self.badge_geometry.setText("✕ HÌNH HỌC: BỊ CHẶN")
            self.badge_geometry.setStyleSheet("color: #f87171; font-size: 10px; font-weight: 600;")
        else:
            self.badge_geometry.setText("⚠ HÌNH HỌC: UNRESOLVED (Chưa có bằng chứng frame)")
            self.badge_geometry.setStyleSheet("color: #fbbf24; font-size: 10px; font-weight: 600;")

        # 4. Banner lược bỏ
        if omissions:
            self.banner_omissions.setText(
                f"ℹ️ Đề xuất có {len(omissions)} mục lược bỏ (B-roll/SFX). "
                f"Khi phê duyệt, toàn bộ đề xuất lược bỏ này sẽ được chấp thuận theo điều khoản D02."
            )
            self.banner_omissions.setVisible(True)
        else:
            self.banner_omissions.setText("ℹ️ Không có mục nào bị lược bỏ.")
            self.banner_omissions.setVisible(False)

        # 5. Cập nhật nút duyệt & trạng thái duyệt
        self._update_approval_status()

    def _update_approval_status(self) -> None:
        if self._proposal is None or not self._schema_valid:
            self.btn_approve_proposal.setEnabled(False)
            return

        geom_status = getattr(self._geometry_result, "status", None)
        is_geom_blocked = (geom_status == "GEOMETRY_BLOCKED")

        if self._is_stale:
            self.badge_approval.setText("⚠ KỊCH BẢN ĐÃ THAY ĐỔI - CẦN CẬP NHẬT")
            self.badge_approval.setStyleSheet("color: #f87171; font-size: 10px; font-weight: 600;")
            self.btn_approve_proposal.setEnabled(False)  # KHÓA NÚT DUYỆT KHI STALE!
            self.btn_approve_proposal.setText("✓ Phê duyệt Kịch bản Toàn bản")
        elif is_geom_blocked:
            self.badge_approval.setText("✕ HÌNH HỌC BỊ CHẶN - KHÔNG THỂ DUYỆT")
            self.badge_approval.setStyleSheet("color: #f87171; font-size: 10px; font-weight: 600;")
            self.btn_approve_proposal.setEnabled(False)
            self.btn_approve_proposal.setText("✕ Bị chặn hình học")
        elif self._approval_record is not None:
            aid = self._approval_record.approval_id
            self.badge_approval.setText(f"✓ ĐÃ PHÊ DUYỆT TOÀN BẢN ({aid})")
            self.badge_approval.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: 600;")
            self.btn_approve_proposal.setEnabled(False)
            self.btn_approve_proposal.setText("✓ Đã Phê Duyệt")
        else:
            self.badge_approval.setText("TRẠNG THÁI: CHƯA PHÊ DUYỆT")
            self.badge_approval.setStyleSheet("color: #fbbf24; font-size: 10px; font-weight: 600;")
            self.btn_approve_proposal.setEnabled(True)
            self.btn_approve_proposal.setText("✓ Phê duyệt Kịch bản Toàn bản")

    def _get_candidates_for_demand(self, demand_id: str) -> List[Tuple[str, str]]:
        """Lấy danh sách các ứng viên có sẵn cho demand_id từ sidecar."""
        if not self._sidecar or not getattr(self._sidecar, "entries", None):
            return []
        for entry in self._sidecar.entries:
            if getattr(entry.demand, "demand_id", None) == demand_id:
                res = []
                for c in getattr(entry, "candidates", ()):
                    aid = getattr(c, "asset_id", "")
                    name = getattr(c, "name", aid)
                    avail = getattr(c, "availability", "online")
                    res.append((aid, f"{aid} ({name}) [{avail}]"))
                return res
        return []

    def _on_assignment_selection_changed(self) -> None:
        selected_indexes = self.tbl_assignments.selectionModel().selectedRows()
        has_sel = len(selected_indexes) > 0
        self.btn_omit_selected.setEnabled(has_sel)
        self.combo_assign_candidate.clear()
        if not has_sel:
            self.combo_assign_candidate.setEnabled(False)
            self.btn_apply_candidate.setEnabled(False)
            return

        row_idx = selected_indexes[0].row()
        demand_id = self.model_assignments.get_demand_id(row_idx)
        if not demand_id:
            self.combo_assign_candidate.setEnabled(False)
            self.btn_apply_candidate.setEnabled(False)
            return

        cands = self._get_candidates_for_demand(demand_id)
        if cands:
            for aid, label in cands:
                self.combo_assign_candidate.addItem(label, aid)
            self.combo_assign_candidate.setEnabled(True)
            self.btn_apply_candidate.setEnabled(True)
        else:
            self.combo_assign_candidate.setEnabled(False)
            self.btn_apply_candidate.setEnabled(False)

    def _on_apply_candidate_clicked(self) -> None:
        selected_indexes = self.tbl_assignments.selectionModel().selectedRows()
        if not selected_indexes:
            return
        row_idx = selected_indexes[0].row()
        demand_id = self.model_assignments.get_demand_id(row_idx)
        asset_id = self.combo_assign_candidate.currentData()
        if demand_id and asset_id:
            self.candidate_selected.emit(demand_id, str(asset_id))

    def _on_omit_selected_clicked(self) -> None:
        selected_indexes = self.tbl_assignments.selectionModel().selectedRows()
        if not selected_indexes:
            return
        row_idx = selected_indexes[0].row()
        demand_id = self.model_assignments.get_demand_id(row_idx)
        if demand_id:
            self.candidate_omitted.emit(demand_id)

    def _on_omission_selection_changed(self) -> None:
        selected_indexes = self.tbl_omissions.selectionModel().selectedRows()
        has_sel = len(selected_indexes) > 0
        self.combo_restore_candidate.clear()
        if not has_sel:
            self.combo_restore_candidate.setEnabled(False)
            self.btn_restore_selected.setEnabled(False)
            return

        row_idx = selected_indexes[0].row()
        demand_id = self.model_omissions.get_demand_id(row_idx)
        if not demand_id:
            self.combo_restore_candidate.setEnabled(False)
            self.btn_restore_selected.setEnabled(False)
            return

        cands = self._get_candidates_for_demand(demand_id)
        if cands:
            for aid, label in cands:
                self.combo_restore_candidate.addItem(label, aid)
            self.combo_restore_candidate.setEnabled(True)
            self.btn_restore_selected.setEnabled(True)
        else:
            self.combo_restore_candidate.setEnabled(False)
            self.btn_restore_selected.setEnabled(False)

    def _on_restore_selected_clicked(self) -> None:
        selected_indexes = self.tbl_omissions.selectionModel().selectedRows()
        if not selected_indexes:
            return
        row_idx = selected_indexes[0].row()
        demand_id = self.model_omissions.get_demand_id(row_idx)
        asset_id = self.combo_restore_candidate.currentData()
        if demand_id and asset_id:
            self.candidate_selected.emit(demand_id, str(asset_id))

    def _on_approve_clicked(self) -> None:
        """Xử lý sự kiện bấm nút Phê duyệt: Thẩm định an toàn ranh giới trước khi phát tín hiệu."""
        if self._is_stale:
            return  # Trả về ngay lập tức không phát tín hiệu nếu stale!

        if self._proposal is None or not self._schema_valid:
            return

        geom_status = getattr(self._geometry_result, "status", None)
        if geom_status == "GEOMETRY_BLOCKED":
            return

        # Tính toán lại revision tại chính thời điểm click chuột để chống TOCTOU / stale event
        current_rev = compute_proposal_revision(self._proposal)
        if current_rev != self._proposal_revision:
            self.set_stale(True)
            return

        self.proposal_approved.emit(self._proposal.proposal_id, current_rev)
