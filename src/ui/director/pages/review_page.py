"""
src/ui/director/pages/review_page.py
Bước 3: Duyệt Câu Thoại & Kịch Bản AI.
Bao gồm:
- Cột 1: Danh sách câu thoại Giữ/Cắt kết nối ReviewState (Ctrl+Z hoàn tác), Mini Timeline màu.
- Cột 2: Khối Kịch bản AI với 3 chế độ:
    1. 📋 Prompt Web (Miễn phí 0đ): Copy 2 luồng prompt (Dựng Video V1 & Chữ Kinetic V2) + Khu vực dán JSON tự nhận diện + Áp dụng.
    2. ☁️ Cloud API: Gemini, DeepSeek, Claude, OpenAI với ô nhập API Key tự nhận diện tiền tố và lưu QSettings.
    3. 💻 Local Ollama: Mô hình dropdown, endpoint URL, nút kiểm tra kết nối (ping) và 1-click dựng.
"""

import os
import copy
import json
import math
import hashlib
import logging
import dataclasses
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple, Callable, Union, Mapping
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QApplication, QStackedWidget,
    QPlainTextEdit, QLineEdit, QComboBox, QButtonGroup,
    QMessageBox, QSplitter, QTableView, QHeaderView, QCheckBox
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QSettings, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QKeyEvent
from src.core.review_state import ReviewState
from src.ui.director.director_state import (
    BATCH_DRAFT_REJECT_MSG,
    PROJECT_OVERVIEW_REJECT_MSG,
    DirectorConfig,
    contains_batch_draft_plan,
    contains_project_overview_draft,
    parse_web_prompt_json,
)
from src.ui.director.widgets.mini_timeline_view import MiniTimelineWidget
from src.core.contracts.plan_demand_contracts import (
    AssetDemandBatch,
    AssetMatchingSidecar,
    DemandMatchEntry,
    validate_demand_batch,
)
from src.core.contracts.asset_matching_contracts import (
    OfflineAssetMatchResult,
)
from src.core.plan_demand_adapter import PlanDemandAdapter
from src.ui.director.workers.asset_matching_worker import (
    AssetMatchingOutcome,
    AssetMatchingWorker,
    MatchingStorageContext,
    read_matching_storage_context,
)
from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    creative_context_to_dict,
    normalize_creative_context,
    compute_creative_context_signature,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    create_app_owned_approval_record,
    verify_proposal_approval_binding,
)
from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.plan_geometry_contracts import (
    PlanGeometryAssemblyResult,
    PlanGeometryIssue,
    SourceFrameEvidence,
    reconstruct_source_frame_evidence,
)
from src.core.geometry.plan_geometry_assembler import assemble_plan_frame_geometry
from src.core.proposal_builder import (
    build_edit_plan_proposal,
    compute_proof_signature,
    validate_and_normalize_source_evidence_snapshot,
)
from src.ui.director.widgets.asset_advisory_drawer import AssetAdvisoryDrawer
from src.ui.director.widgets.common_proposal_review_widget import CommonProposalReviewWidget
import datetime
import uuid

logger = logging.getLogger(__name__)

try:
    from src.core.story_copilot import StoryCopilot
except Exception:
    StoryCopilot = None

try:
    from src.core.kinetic import generate_kinetic_prompt, DEMO_WORDS
except Exception:
    generate_kinetic_prompt = None
    DEMO_WORDS = []


class OllamaPingWorker(QThread):
    """Worker ngầm để ping kiểm tra kết nối Ollama không làm đơ giao diện."""
    finished_ping = Signal(bool, str, list)  # (is_online, message, model_names)

    def __init__(self, endpoint: str, parent=None):
        super().__init__(parent)
        self.endpoint = endpoint

    def run(self):
        import urllib.request
        url = f"{self.endpoint.rstrip('/')}/api/tags"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ChunDVC-Director/1.0"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                msg = f"Đã kết nối ({len(models)} model sẵn sàng)" if models else "Đã kết nối Ollama"
                self.finished_ping.emit(True, msg, models)
        except Exception as e:
            self.finished_ping.emit(False, f"Không kết nối được ({str(e)[:45]})", [])


class ReviewRowWidget(QFrame):
    """Một dòng hiển thị câu thoại với nút Giữ lại / Cắt."""
    toggled = Signal(int)

    def __init__(self, row_idx: int, tc: str, text: str, is_cut: bool, tag: str = "", parent=None):
        super().__init__(parent)
        self.row_idx = row_idx
        self.tc = tc
        self.text = text
        self.is_cut = is_cut
        self.tag = tag
        self.setFixedHeight(34)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)

        # Timecode
        self.lbl_tc = QLabel(self.tc)
        self.lbl_tc.setStyleSheet("color: #71717a; font-family: monospace; font-size: 11px;")
        layout.addWidget(self.lbl_tc)

        # Text
        self.lbl_text = QLabel(self.text)
        self.lbl_text.setStyleSheet("font-size: 11.5px;")
        layout.addWidget(self.lbl_text, stretch=1)

        # Tag nếu có (Hook, Vấp, Lặp)
        if self.tag:
            lbl_tag = QLabel(self.tag)
            lbl_tag.setStyleSheet("""
                color: #f59e0b;
                background: rgba(245, 158, 11, 0.14);
                border-radius: 3px;
                padding: 1px 5px;
                font-size: 10px;
                font-weight: 600;
            """)
            layout.addWidget(lbl_tag)

        # Nút Giữ / Cắt
        self.btn_act = QPushButton()
        self.btn_act.setFixedSize(54, 22)
        self.btn_act.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_act.clicked.connect(lambda: self.toggled.emit(self.row_idx))
        layout.addWidget(self.btn_act)

        self._refresh()

    def set_cut(self, is_cut: bool):
        self.is_cut = is_cut
        self._refresh()

    def _refresh(self):
        if self.is_cut:
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(239, 68, 68, 0.08);
                    border: 1px solid rgba(239, 68, 68, 0.2);
                    border-radius: 4px;
                }
            """)
            self.lbl_text.setStyleSheet("color: #71717a; text-decoration: line-through; font-size: 11.5px;")
            self.btn_act.setText("Giữ lại")
            self.btn_act.setStyleSheet("""
                QPushButton {
                    background-color: #27272a;
                    color: #4ade80;
                    border: 1px solid #3f3f46;
                    border-radius: 3px;
                    font-size: 10.5px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #3f3f46;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: #18181b;
                    border: 1px solid #27272a;
                    border-radius: 4px;
                }
            """)
            self.lbl_text.setStyleSheet("color: #ffffff; font-size: 11.5px;")
            self.btn_act.setText("Cắt")
            self.btn_act.setStyleSheet("""
                QPushButton {
                    background-color: rgba(239, 68, 68, 0.15);
                    color: #ef4444;
                    border: 1px solid rgba(239, 68, 68, 0.3);
                    border-radius: 3px;
                    font-size: 10.5px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: rgba(239, 68, 68, 0.3);
                }
            """)


class PasteZoneWidget(QFrame):
    """Vùng dán tự động nhận diện JSON từ Claude / ChatGPT."""
    json_recognized = Signal(dict, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(54)
        self.is_recognized = False
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        self.lbl_icon = QLabel("📋")
        self.lbl_icon.setStyleSheet("font-size: 16px;")
        layout.addWidget(self.lbl_icon)

        self.lbl_text = QLabel("Bấm vào đây rồi nhấn Ctrl+V để dán nhanh JSON kịch bản")
        self.lbl_text.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        layout.addWidget(self.lbl_text, stretch=1)

        self._update_appearance()

    def _update_appearance(self):
        if self.is_recognized:
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(34, 197, 94, 0.12);
                    border: 1.5px solid #22c55e;
                    border-radius: 6px;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: #16171a;
                    border: 1.5px dashed #3d414c;
                    border-radius: 4px;
                }
                QFrame:hover {
                    border-color: #e05a47;
                    background-color: rgba(217, 79, 61, 0.06);
                }
            """)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_V and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.paste_from_clipboard()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.paste_from_clipboard()

    def paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        ok, msg, data = parse_web_prompt_json(text)
        if ok and data:
            self.set_success(msg, data)
            self.json_recognized.emit(data, msg)
        else:
            self.set_error(msg)

    def set_success(self, summary: str, data: dict):
        self.is_recognized = True
        self.lbl_icon.setText("✓")
        self.lbl_icon.setStyleSheet("color: #4ade80; font-size: 16px; font-weight: bold;")
        self.lbl_text.setText(summary)
        self.lbl_text.setStyleSheet("color: #4ade80; font-size: 11px; font-weight: bold;")
        self._update_appearance()

    def set_error(self, err_msg: str):
        self.is_recognized = False
        self.lbl_icon.setText("✕")
        self.lbl_icon.setStyleSheet("color: #ef4444; font-size: 16px; font-weight: bold;")
        self.lbl_text.setText(f"Lỗi: {err_msg}")
        self.lbl_text.setStyleSheet("color: #f87171; font-size: 11px;")
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(239, 68, 68, 0.1);
                border: 1.5px solid #ef4444;
                border-radius: 6px;
            }
        """)


class VisualObservationsTableModel(QAbstractTableModel):
    """Model hiển thị danh sách các quan sát thị giác (Visual Observations)."""

    COLUMNS = ["Thời gian", "Cửa sổ", "Mô tả quan sát", "Hành động", "Trạng thái"]

    def __init__(self, observations: Optional[List[Dict[str, Any]]] = None, parent=None):
        super().__init__(parent)
        self._observations: List[Dict[str, Any]] = list(observations or [])

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._observations)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._observations)):
            return None

        obs = self._observations[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                st = float(obs.get("start_sec", 0.0))
                en = float(obs.get("end_sec", 0.0))
                if en > st + 0.1:
                    return f"{int(st//60):02d}:{int(st%60):02d} - {int(en//60):02d}:{int(en%60):02d}"
                return f"{int(st//60):02d}:{int(st%60):02d}"
            elif col == 1:
                clip_n = obs.get("clip_name", "")
                w_id = obs.get("window_id", "")
                return f"{clip_n} ({w_id})" if clip_n and w_id else (clip_n or w_id or "-")
            elif col == 2:
                return obs.get("description", "")
            elif col == 3:
                actions = obs.get("observed_actions", [])
                return ", ".join(actions) if isinstance(actions, list) else str(actions)
            elif col == 4:
                status = obs.get("analysis_status", "described")
                origin = obs.get("origin", "")
                suffix = f" · {origin}" if origin else ""
                if status == "described":
                    return f"Mô tả AI (cần đối chiếu){suffix}"
                elif status == "needs_review":
                    return f"Cần đối chiếu lại{suffix}"
                elif status == "stale":
                    return f"Lệch cấu hình (cũ){suffix}"
                elif status == "partial":
                    return f"Một phần (cần đối chiếu){suffix}"
                elif status == "failed":
                    return f"Lỗi phân tích{suffix}"
                return f"{status}{suffix}"

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (0, 1):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        elif role == Qt.ItemDataRole.ForegroundRole:
            from PySide6.QtGui import QColor
            status = obs.get("analysis_status", "described")
            if status == "stale":
                return QColor("#f59e0b")
            elif status == "needs_review":
                return QColor("#fbbf24")
            return QColor("#e4e4e7")

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def set_observations(self, observations: List[Dict[str, Any]]):
        self.beginResetModel()
        self._observations = list(observations or [])
        self.endResetModel()

    def clear(self):
        self.beginResetModel()
        self._observations.clear()
        self.endResetModel()

    def get_observations(self) -> List[Dict[str, Any]]:
        return list(self._observations)


def _deep_dict_copy(obj: Any) -> Any:
    """Sao chép sâu an toàn cho Mapping/Sequence mà không gặp lỗi pickle trên MappingProxyType."""
    if isinstance(obj, Mapping):
        return {str(k): _deep_dict_copy(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_deep_dict_copy(v) for v in obj]
    return obj


class ReviewPage(QWidget):
    """Trang Bước 3: Duyệt câu thoại & Kịch bản AI."""
    ai_run_requested = Signal()
    prompt_copied = Signal(str)
    prompt_ready_for_run = Signal(str, str)  # (request_id, prompt_text)
    config_changed = Signal()
    json_applied = Signal(dict, str)
    rough_scan_requested = Signal()
    analysis_workers_idle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("ChunDVC", "ResolveFlow")
        self._review_rows: List[List[Any]] = [
            # [timecode, text, is_cut, tag]
            ["00:00:02", "Chào mừng các bạn đã quay trở lại với ChunDVC Studio", 0, "Hook"],
            ["00:00:06", "Hôm nay chúng ta sẽ cùng khám phá một tính năng rất đặc biệt", 0, ""],
            ["00:00:11", "ờ... thì là... để tôi xem lại kịch bản tí nhé", 1, "Vấp"],
            ["00:00:15", "Đó chính là hệ thống dựng tự động thông minh bằng AI", 0, ""],
            ["00:00:20", "Đó chính là hệ thống dựng tự động thông minh bằng AI", 1, "Lặp"],
            ["00:00:25", "Nó giúp rút ngắn thời gian dựng thô từ hàng tiếng xuống chỉ còn vài giây", 0, ""]
        ]
        self._row_widgets: List[ReviewRowWidget] = []
        self._pasted_data: Optional[Dict[str, Any]] = None
        self._pasted_summary: str = ""
        self._video_plan_data: Optional[Dict[str, Any]] = None
        self._video_plan_summary: str = ""
        self._kinetic_plan_data: Optional[Dict[str, Any]] = None
        self._kinetic_plan_summary: str = ""
        self._review_cuts_modified: bool = False
        self._clip_infos: List[Any] = []
        self._style_intent: str = "travel_vlog"
        self._hook_duration: str = "30_60s"
        self._whisper_model: str = "small"
        self._whisper_lang: str = "vi"
        self._ping_worker: Optional[OllamaPingWorker] = None

        # Trạng thái nạp metadata phân tích nguồn bất đồng bộ (G05-05C1)
        self._analysis_generation: int = 0
        self._active_analysis_workers: Dict[str, Any] = {}
        self._pending_request_payloads: Dict[str, List[Dict[str, Any]]] = {}
        self._is_analysis_loading: bool = False
        self.visual_model = VisualObservationsTableModel(parent=self)

        # Trạng thái chuẩn bị Prompt bất đồng bộ (G05-05C2b)
        self._prompt_worker: Optional[Any] = None
        self._prompt_workers_registry: Dict[str, Any] = {}
        self._prompt_generation: int = 0
        self._prepared_prompt_snapshot: Optional[Dict[str, Any]] = None
        self._prepared_batch_snapshot: Optional[Dict[str, Any]] = None
        self._batch_drafts_ledger: Dict[Tuple[str, str], Any] = {}
        self._context_supplier: Optional[Any] = None
        self._active_prompt_request_id: Optional[str] = None
        self._active_prompt_intent: Optional[str] = None
        self._consumed_prompt_request_ids: set = set()
        self._pending_prompt_expected: Optional[Dict[str, Any]] = None

        # Trạng thái chuẩn bị Kinetic Typography bất đồng bộ (G05-05C2c2)
        self._kinetic_workers_registry: Dict[str, Any] = {}
        self._active_kinetic_request_id: Optional[str] = None
        self._active_kinetic_intent: Optional[str] = None
        self._active_kinetic_aspect: str = "9:16"
        self._kinetic_generation: int = 0
        self._prepared_kinetic_snapshot: Optional[Dict[str, Any]] = None
        self._consumed_kinetic_request_ids: set = set()

        # Trạng thái Cố vấn tài nguyên ngoại tuyến bất đồng bộ (G06-06B3-I2-P2)
        self._matching_workers_registry: Dict[str, Any] = {}
        self._matching_generation: int = 0
        self._matching_pending_context: Optional[Dict[str, Any]] = None
        self._last_good_matching_sidecar: Optional[Any] = None
        self._last_good_matching_revision: Optional[str] = None

        # Trạng thái Đề xuất Kịch bản Toàn bản & Phê duyệt Biên tập (G06-06C3-S3)
        self._proposal_session_id: str = f"prop_{uuid.uuid4().hex[:12]}"
        self._current_proposal: Optional[EditPlanProposal] = None
        self._current_approval_record: Optional[AppOwnedApprovalRecord] = None
        self._current_user_candidate_choices: Dict[str, str] = {}
        self._current_geometry_result: Optional[Any] = None
        self._source_frame_evidence_table: Dict[str, Any] = {}

        # Trạng thái Quét bằng chứng nguồn tiền phê duyệt (G07-07A4-I)
        self._preapproval_evidence_service: Optional[Any] = None
        self._preapproval_evidence_workers: Dict[str, Any] = {}
        self._preapproval_evidence_generation: int = 0
        self._preapproval_evidence_map: Dict[str, Dict[str, Any]] = {}
        self._preapproval_evidence_diagnostics: Tuple[str, ...] = ()
        self._pending_preapproval_expected: Optional[Tuple[Any, str, int, str, Tuple[Any, ...]]] = None
        self._preapproval_service_reset_pending: bool = False

        self._init_ui()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(18, 12, 18, 12)
        main_layout.setSpacing(16)

        # -------------------------------------------------------------
        # Cột trái: Duyệt câu thoại & Quan sát thị giác (flex: 6)
        # -------------------------------------------------------------
        col_left = QWidget(self)
        v_left = QVBoxLayout(col_left)
        v_left.setContentsMargins(0, 0, 0, 0)
        v_left.setSpacing(8)

        splitter_left = QSplitter(Qt.Orientation.Vertical, col_left)
        splitter_left.setStyleSheet("QSplitter::handle { background-color: #27272a; height: 3px; }")

        # 1. Khối trên: Duyệt câu thoại
        pane_speech = QWidget(splitter_left)
        v_speech = QVBoxLayout(pane_speech)
        v_speech.setContentsMargins(0, 0, 0, 0)
        v_speech.setSpacing(6)

        h_review_header = QHBoxLayout()
        lbl_h_review = QLabel("Duyệt câu thoại (Speech Transcripts)")
        lbl_h_review.setStyleSheet("color: #ffffff; font-size: 13.5px; font-weight: bold;")
        h_review_header.addWidget(lbl_h_review)
        h_review_header.addStretch(1)

        self.btn_review_scan = QPushButton("⚡ Quét thô Whisper")
        self.btn_review_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_review_scan.setStyleSheet("""
            QPushButton {
                background: #23252a;
                color: #f59e0b;
                border: 1px solid #353942;
                border-radius: 4px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #2c2f36;
                border-color: #f59e0b;
                color: #fbbf24;
            }
        """)
        self.btn_review_scan.setToolTip("Chạy bóc tách lại câu thoại và phát hiện khoảng lặng bằng Whisper")
        self.btn_review_scan.clicked.connect(self.rough_scan_requested.emit)
        h_review_header.addWidget(self.btn_review_scan)
        v_speech.addLayout(h_review_header)

        # ScrollArea chứa các dòng thoại
        scroll = QScrollArea(pane_speech)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)
        self.rows_layout.setSpacing(4)

        for i, row in enumerate(self._review_rows):
            w = ReviewRowWidget(i, row[0], row[1], bool(row[2]), row[3], self.rows_container)
            w.toggled.connect(self._on_row_toggled)
            self.rows_layout.addWidget(w)
            self._row_widgets.append(w)

        self.rows_layout.addStretch(1)
        scroll.setWidget(self.rows_container)
        v_speech.addWidget(scroll, stretch=1)

        # Gợi ý phím tắt
        lbl_keys = QLabel("Ctrl+Z hoàn tác · Space nghe thử câu đang chọn")
        lbl_keys.setStyleSheet("color: #71717a; font-size: 11px;")
        v_speech.addWidget(lbl_keys)

        # Mini Timeline Preview
        lbl_mtl = QLabel("Xem trước timeline:")
        lbl_mtl.setStyleSheet("color: #ffffff; font-size: 11.5px; font-weight: 600; margin-top: 2px;")
        v_speech.addWidget(lbl_mtl)

        self.mini_timeline = MiniTimelineWidget(pane_speech)
        v_speech.addWidget(self.mini_timeline)

        splitter_left.addWidget(pane_speech)

        # 2. Khối dưới: Cảnh quay & Quan sát thị giác (Visual Observations)
        pane_visual = QWidget(splitter_left)
        v_visual = QVBoxLayout(pane_visual)
        v_visual.setContentsMargins(0, 4, 0, 0)
        v_visual.setSpacing(4)

        h_visual_header = QHBoxLayout()
        lbl_h_visual = QLabel("Cảnh quay & Quan sát thị giác (Visual Observations)")
        lbl_h_visual.setStyleSheet("color: #ffffff; font-size: 12.5px; font-weight: bold;")
        h_visual_header.addWidget(lbl_h_visual)
        h_visual_header.addStretch(1)

        self.lbl_visual_status = QLabel("Chưa nạp quan sát")
        self.lbl_visual_status.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        h_visual_header.addWidget(self.lbl_visual_status)
        v_visual.addLayout(h_visual_header)

        self.visual_table = QTableView(pane_visual)
        self.visual_table.setModel(self.visual_model)
        self.visual_table.setAlternatingRowColors(True)
        self.visual_table.setStyleSheet("""
            QTableView {
                background-color: #121214;
                border: 1px solid #27272a;
                border-radius: 6px;
                color: #e4e4e7;
                gridline-color: #1f1f23;
                font-size: 11px;
            }
            QTableView::item {
                padding: 4px 6px;
            }
            QTableView::item:selected {
                background-color: #3b82f6;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #18181b;
                color: #a1a1aa;
                border: 1px solid #27272a;
                padding: 4px;
                font-size: 11px;
                font-weight: 600;
            }
        """)
        vheader = self.visual_table.horizontalHeader()
        vheader.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        vheader.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        vheader.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        vheader.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        vheader.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        v_visual.addWidget(self.visual_table, stretch=1)

        splitter_left.addWidget(pane_visual)
        splitter_left.setSizes([340, 200])

        v_left.addWidget(splitter_left, stretch=1)
        main_layout.addWidget(col_left, stretch=6)

        # Divider
        divider = QFrame(self)
        divider.setFixedWidth(1)
        divider.setStyleSheet("background-color: #27272a;")
        main_layout.addWidget(divider)

        # -------------------------------------------------------------
        # Cột phải: Kịch bản AI & Prompt Đạo Diễn (flex: 5)
        # -------------------------------------------------------------
        col_right = QWidget(self)
        v_right = QVBoxLayout(col_right)
        v_right.setContentsMargins(0, 0, 0, 0)
        v_right.setSpacing(10)

        lbl_h_ai = QLabel("Kịch bản AI & Prompt Đạo Diễn")
        lbl_h_ai.setStyleSheet("color: #ffffff; font-size: 13.5px; font-weight: bold;")
        v_right.addWidget(lbl_h_ai)

        # 3 Nút chọn chế độ AI (Segmented Tabs)
        mode_btn_layout = QHBoxLayout()
        mode_btn_layout.setSpacing(4)

        self.btn_mode_web = QPushButton("📋 Prompt Web (0đ)")
        self.btn_mode_cloud = QPushButton("☁️ Cloud API")
        self.btn_mode_ollama = QPushButton("💻 Local Ollama")

        self.btn_mode_web.setCheckable(True)
        self.btn_mode_cloud.setCheckable(True)
        self.btn_mode_ollama.setCheckable(True)
        self.btn_mode_web.setChecked(True)

        mode_style = """
            QPushButton {
                background-color: #191a1e;
                border: 1px solid #2e3138;
                border-radius: 4px;
                padding: 6px 4px;
                font-size: 11px;
                color: #9da3ae;
                font-weight: 600;
            }
            QPushButton:hover {
                border-color: #434752;
                color: #ffffff;
            }
            QPushButton:checked {
                background-color: #d94f3d;
                color: #ffffff;
                font-weight: bold;
                border-color: #e86352;
            }
        """
        self.btn_mode_web.setStyleSheet(mode_style)
        self.btn_mode_cloud.setStyleSheet(mode_style)
        self.btn_mode_ollama.setStyleSheet(mode_style)

        mode_btn_layout.addWidget(self.btn_mode_web)
        mode_btn_layout.addWidget(self.btn_mode_cloud)
        mode_btn_layout.addWidget(self.btn_mode_ollama)
        v_right.addLayout(mode_btn_layout)

        # Card container cho Stacked Pages
        ai_card = QFrame(col_right)
        ai_card.setObjectName("ai_box_frame")
        ai_card.setStyleSheet("""
            QFrame#ai_box_frame {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 8px;
            }
        """)
        v_card = QVBoxLayout(ai_card)
        v_card.setContentsMargins(12, 12, 12, 12)
        v_card.setSpacing(8)

        # Stacked Pages cho 3 chế độ
        self.ai_stack = QStackedWidget(ai_card)

        # ---------------------------------------------------------
        # Page 0: Prompt Web (0đ)
        # ---------------------------------------------------------
        self.scroll_web = QScrollArea()
        self.scroll_web.setWidgetResizable(True)
        self.scroll_web.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_web.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_web.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_web.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
        """)

        p_web = QWidget()
        p_web.setObjectName("p_web_container")
        self.p_web = p_web
        pw_lay = QVBoxLayout(p_web)
        pw_lay.setContentsMargins(0, 0, 4, 0)
        pw_lay.setSpacing(8)

        lbl_step1 = QLabel("1. Chọn copy Prompt gửi cho ChatGPT / Claude (Miễn phí 0đ):")
        lbl_step1.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        pw_lay.addWidget(lbl_step1)

        row_copy_btns = QGridLayout()
        row_copy_btns.setSpacing(6)

        self.btn_copy_video_prompt = QPushButton("📋 1. Prompt Dựng Video (V1)")
        self.btn_copy_video_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_video_prompt.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                padding: 7px 8px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        self.btn_copy_video_prompt.clicked.connect(self._copy_video_prompt)
        row_copy_btns.addWidget(self.btn_copy_video_prompt, 0, 0)

        self.btn_copy_final_transcript = QPushButton("✂ 2. Copy Transcript Bản Dựng")
        self.btn_copy_final_transcript.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_final_transcript.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: #ffffff;
                padding: 7px 8px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover {
                background-color: #047857;
            }
        """)
        self.btn_copy_final_transcript.setToolTip("Trích xuất chỉ riêng các câu thoại thuộc Bản Dựng Cuối (Final Cut) đã lọc sạch ảo giác, đánh số [0]..[N-1] liên tục không tốn token")
        self.btn_copy_final_transcript.clicked.connect(self._copy_final_cut_transcript)
        row_copy_btns.addWidget(self.btn_copy_final_transcript, 0, 1)

        self.btn_copy_kinetic_prompt = QPushButton("✨ 3. Prompt Chữ Kinetic (V2)")
        self.btn_copy_kinetic_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_kinetic_prompt.setToolTip("Tính năng đang nâng cấp V2 Kinetic (chuẩn bị bất đồng bộ).")
        self.btn_copy_kinetic_prompt.setStyleSheet("""
            QPushButton {
                background-color: #d97706;
                color: #ffffff;
                padding: 7px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover {
                background-color: #b45309;
            }
        """)
        self.btn_copy_kinetic_prompt.clicked.connect(self._copy_kinetic_prompt)
        row_copy_btns.addWidget(self.btn_copy_kinetic_prompt, 1, 0, 1, 2)

        pw_lay.addLayout(row_copy_btns)

        # Additive Section cho Batch Planner (G06-06A3-I)
        self.frame_batch_section = QFrame()
        self.frame_batch_section.setStyleSheet("""
            QFrame {
                background: #16171a;
                border: 1px dashed #353942;
                border-radius: 4px;
                padding: 4px 6px;
                margin-top: 4px;
            }
        """)
        lay_batch = QVBoxLayout(self.frame_batch_section)
        lay_batch.setContentsMargins(4, 3, 4, 3)
        lay_batch.setSpacing(4)

        self.btn_prepare_batches = QPushButton("📦 4. Phân Lô Kịch Bản (Batch Planner)")
        self.btn_prepare_batches.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prepare_batches.setStyleSheet("""
            QPushButton {
                background-color: #d94f3d;
                color: #ffffff;
                padding: 6px 10px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover {
                background-color: #e86352;
            }
            QPushButton:disabled {
                background-color: #23252a;
                color: #686e7a;
            }
        """)
        self.btn_prepare_batches.clicked.connect(self._on_prepare_batches_clicked)
        lay_batch.addWidget(self.btn_prepare_batches)

        h_batch_top = QHBoxLayout()
        h_batch_top.setSpacing(6)

        self.cbo_batch_selector = QComboBox()
        self.cbo_batch_selector.setStyleSheet("""
            QComboBox {
                background: #27272a;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 10.5px;
            }
        """)
        self.cbo_batch_selector.currentIndexChanged.connect(self._on_batch_selection_changed)
        h_batch_top.addWidget(self.cbo_batch_selector, stretch=1)

        self.btn_copy_selected_batch = QPushButton("📋 Sao Chép Lô")
        self.btn_copy_selected_batch.setEnabled(False)
        self.btn_copy_selected_batch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_selected_batch.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: #ffffff;
                padding: 5px 10px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover {
                background-color: #047857;
            }
            QPushButton:disabled {
                background-color: #27272a;
                color: #71717a;
            }
        """)
        self.btn_copy_selected_batch.clicked.connect(self._on_copy_selected_batch_clicked)
        h_batch_top.addWidget(self.btn_copy_selected_batch)

        lay_batch.addLayout(h_batch_top)

        self.lbl_batch_summary = QLabel("Chưa lập kế hoạch phân lô.")
        self.lbl_batch_summary.setStyleSheet("color: #a1a1aa; font-size: 10px;")
        lay_batch.addWidget(self.lbl_batch_summary)

        self.txt_batch_preview = QPlainTextEdit()
        self.txt_batch_preview.setReadOnly(True)
        self.txt_batch_preview.setMaximumHeight(70)
        self.txt_batch_preview.setStyleSheet("""
            QPlainTextEdit {
                background: #09090b;
                color: #a1a1aa;
                border: 1px solid #27272a;
                border-radius: 4px;
                font-family: monospace;
                font-size: 9.5px;
            }
        """)
        self.txt_batch_preview.setVisible(False)
        lay_batch.addWidget(self.txt_batch_preview)

        # Dedicated Batch Draft Intake Section (G06-06A4b-I)
        lay_intake = QVBoxLayout()
        lay_intake.setSpacing(3)

        lbl_intake_title = QLabel("📥 Tiếp Nhận Bản Thảo Lô (Batch Draft Intake) - Chỉ lưu trong phiên:")
        lbl_intake_title.setStyleSheet("color: #a1a1aa; font-size: 10px; font-weight: 500;")
        lay_intake.addWidget(lbl_intake_title)

        h_intake = QHBoxLayout()
        h_intake.setSpacing(6)

        self.txt_batch_draft_input = QPlainTextEdit()
        self.txt_batch_draft_input.setPlaceholderText("Dán mã JSON bản thảo của lô đang chọn từ AI vào đây...")
        self.txt_batch_draft_input.setMaximumHeight(65)
        self.txt_batch_draft_input.setStyleSheet("""
            QPlainTextEdit {
                background: #18181b;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                font-family: monospace;
                font-size: 10px;
            }
        """)
        h_intake.addWidget(self.txt_batch_draft_input, stretch=1)

        v_intake_btn = QVBoxLayout()
        v_intake_btn.setSpacing(3)
        self.btn_submit_batch_draft = QPushButton("📥 Nạp Bản Thảo Cho Lô Đang Chọn")
        self.btn_submit_batch_draft.setEnabled(False)
        self.btn_submit_batch_draft.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_submit_batch_draft.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                padding: 6px 10px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton:disabled {
                background-color: #27272a;
                color: #71717a;
            }
        """)
        self.btn_submit_batch_draft.clicked.connect(self._on_submit_batch_draft_clicked)
        v_intake_btn.addWidget(self.btn_submit_batch_draft)

        lbl_intake_consent_note = QLabel("Bản thảo do AI đề xuất cho lô hiện tại, đối soát hình học trước khi lưu.")
        lbl_intake_consent_note.setStyleSheet("color: #71717a; font-size: 9px;")
        lbl_intake_consent_note.setWordWrap(True)
        v_intake_btn.addWidget(lbl_intake_consent_note)

        self.chk_replace_existing_draft = QCheckBox("Ghi đè bản thảo đã có của lô này")
        self.chk_replace_existing_draft.setChecked(False)
        self.chk_replace_existing_draft.setStyleSheet("""
            QCheckBox {
                color: #a1a1aa;
                font-size: 9.5px;
            }
            QCheckBox::indicator {
                width: 12px;
                height: 12px;
            }
        """)
        v_intake_btn.addWidget(self.chk_replace_existing_draft)

        h_intake.addLayout(v_intake_btn)
        lay_intake.addLayout(h_intake)

        self.lbl_batch_draft_status = QLabel("")
        self.lbl_batch_draft_status.setStyleSheet("color: #a1a1aa; font-size: 10px;")
        lay_intake.addWidget(self.lbl_batch_draft_status)

        lay_batch.addLayout(lay_intake)

        # Project Overview Section (G06-06A4c2b-I)
        h_overview = QHBoxLayout()
        h_overview.setSpacing(6)
        self.btn_copy_project_overview = QPushButton("📋 Sao Chép Tổng Quan Dự Án")
        self.btn_copy_project_overview.setEnabled(False)
        self.btn_copy_project_overview.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_project_overview.setStyleSheet("""
            QPushButton {
                background-color: #0d9488;
                color: #ffffff;
                padding: 5px 10px;
                border-radius: 5px;
                font-weight: bold;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover {
                background-color: #0f766e;
            }
            QPushButton:disabled {
                background-color: #27272a;
                color: #71717a;
            }
        """)
        self.btn_copy_project_overview.clicked.connect(self._on_copy_project_overview_clicked)
        h_overview.addWidget(self.btn_copy_project_overview)

        self.lbl_overview_status = QLabel("")
        self.lbl_overview_status.setStyleSheet("color: #a1a1aa; font-size: 10px;")
        h_overview.addWidget(self.lbl_overview_status, stretch=1)

        lay_batch.addLayout(h_overview)

        pw_lay.addWidget(self.frame_batch_section)

        # Tương thích với alias cũ
        self.btn_copy_prompt = self.btn_copy_video_prompt

        # Thẻ trạng thái 2 Kế hoạch độc lập (Dual Plan Slots V1 & V2)
        lbl_slots_head = QLabel("Trạng thái nạp 2 kịch bản (Độc lập không bị đè mất):")
        lbl_slots_head.setStyleSheet("color: #a1a1aa; font-size: 11px; margin-top: 4px; font-weight: 500;")
        pw_lay.addWidget(lbl_slots_head)

        plan_status_layout = QHBoxLayout()
        plan_status_layout.setSpacing(8)

        self.card_slot_video = QFrame()
        self.card_slot_video.setStyleSheet("""
            QFrame {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 4px 6px;
            }
        """)
        v_slot_vid = QVBoxLayout(self.card_slot_video)
        v_slot_vid.setContentsMargins(4, 2, 4, 2)
        v_slot_vid.setSpacing(3)

        h_slot_vid_top = QHBoxLayout()
        self.lbl_slot_vid_title = QLabel("🎬 1. Video Plan (V1)")
        self.lbl_slot_vid_title.setStyleSheet("font-weight: bold; font-size: 11px; color: #38bdf8;")
        h_slot_vid_top.addWidget(self.lbl_slot_vid_title)
        h_slot_vid_top.addStretch(1)

        self.btn_paste_slot1 = QPushButton("📋 Dán vào Slot 1")
        self.btn_paste_slot1.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_paste_slot1.setStyleSheet("""
            QPushButton {
                background: rgba(56, 189, 248, 0.15);
                color: #38bdf8;
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #0284c7;
                color: #ffffff;
            }
        """)
        self.btn_paste_slot1.setToolTip("Dán trực tiếp JSON từ Clipboard vào riêng Slot 1 (Video Plan)")
        self.btn_paste_slot1.clicked.connect(lambda: self._paste_to_slot("slot1"))
        h_slot_vid_top.addWidget(self.btn_paste_slot1)

        self.btn_match_assets = QPushButton("💡 Gợi ý tài nguyên")
        self.btn_match_assets.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_match_assets.setStyleSheet("""
            QPushButton {
                background: rgba(74, 222, 128, 0.15);
                color: #4ade80;
                border: 1px solid rgba(74, 222, 128, 0.35);
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #16a34a;
                color: #ffffff;
            }
            QPushButton:disabled {
                background: #27272a;
                color: #71717a;
                border: 1px solid #3f3f46;
            }
        """)
        self.btn_match_assets.setToolTip("Khớp và gợi ý tài nguyên ngoại tuyến từ kho media cho các broll & sfx inserts")
        self.btn_match_assets.setEnabled(False)
        self.btn_match_assets.clicked.connect(self._on_match_assets_clicked)
        h_slot_vid_top.addWidget(self.btn_match_assets)

        self.btn_check_source_evidence = QPushButton("🔍 Kiểm tra nguồn trước khi duyệt")
        self.btn_check_source_evidence.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_check_source_evidence.setStyleSheet("""
            QPushButton {
                background: rgba(56, 189, 248, 0.15);
                color: #38bdf8;
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #0284c7;
                color: #ffffff;
            }
            QPushButton:disabled {
                background: #27272a;
                color: #71717a;
                border: 1px solid #3f3f46;
            }
        """)
        self.btn_check_source_evidence.setToolTip("Quét giải mã khung hình và tính mã băm toàn bộ cho các clip nguồn trước khi duyệt")
        self.btn_check_source_evidence.setEnabled(False)
        self.btn_check_source_evidence.clicked.connect(self._on_check_source_evidence_clicked)
        h_slot_vid_top.addWidget(self.btn_check_source_evidence)

        v_slot_vid.addLayout(h_slot_vid_top)

        self.lbl_slot_vid_status = QLabel("Chưa nạp (Dùng cắt thô)")
        self.lbl_slot_vid_status.setStyleSheet("color: #71717a; font-size: 10px;")
        v_slot_vid.addWidget(self.lbl_slot_vid_status)

        self.lbl_matching_status = QLabel("")
        self.lbl_matching_status.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_matching_status.setStyleSheet("color: #71717a; font-size: 10px;")
        v_slot_vid.addWidget(self.lbl_matching_status)

        self.lbl_source_evidence_status = QLabel("⚪ Chưa thu thập bằng chứng nguồn")
        self.lbl_source_evidence_status.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_source_evidence_status.setStyleSheet("color: #71717a; font-size: 10px;")
        v_slot_vid.addWidget(self.lbl_source_evidence_status)
        plan_status_layout.addWidget(self.card_slot_video, stretch=1)

        self.card_slot_kinetic = QFrame()
        self.card_slot_kinetic.setStyleSheet("""
            QFrame {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 4px 6px;
            }
        """)
        v_slot_kin = QVBoxLayout(self.card_slot_kinetic)
        v_slot_kin.setContentsMargins(4, 2, 4, 2)
        v_slot_kin.setSpacing(3)

        h_slot_kin_top = QHBoxLayout()
        self.lbl_slot_kin_title = QLabel("✨ 2. Kinetic Plan (V2)")
        self.lbl_slot_kin_title.setStyleSheet("font-weight: bold; font-size: 11px; color: #f59e0b;")
        h_slot_kin_top.addWidget(self.lbl_slot_kin_title)
        h_slot_kin_top.addStretch(1)

        self.btn_auto_kinetic = QPushButton("⚡ Tự tạo (0đ Token)")
        self.btn_auto_kinetic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auto_kinetic.setStyleSheet("""
            QPushButton {
                background-color: #d94f3d;
                color: #ffffff;
                border: 1px solid #e86352;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e86352;
            }
        """)
        self.btn_auto_kinetic.setToolTip("Tự động ngắt trang, căn thời gian và highlight từ khóa hoàn toàn Offline (không tốn token)")
        self.btn_auto_kinetic.clicked.connect(self._auto_generate_kinetic_plan)
        h_slot_kin_top.addWidget(self.btn_auto_kinetic)

        self.btn_paste_slot2 = QPushButton("✨ Dán vào Slot 2")
        self.btn_paste_slot2.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_paste_slot2.setStyleSheet("""
            QPushButton {
                background: #23252a;
                color: #f59e0b;
                border: 1px solid #353942;
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #d97706;
                color: #ffffff;
                border-color: #f59e0b;
            }
        """)
        self.btn_paste_slot2.setToolTip("Dán trực tiếp JSON từ Clipboard vào riêng Slot 2 (Kinetic Plan)")
        self.btn_paste_slot2.clicked.connect(lambda: self._paste_to_slot("slot2"))
        h_slot_kin_top.addWidget(self.btn_paste_slot2)
        v_slot_kin.addLayout(h_slot_kin_top)

        self.lbl_slot_kin_status = QLabel("Chưa nạp (Dùng phụ đề)")
        self.lbl_slot_kin_status.setStyleSheet("color: #71717a; font-size: 10px;")
        v_slot_kin.addWidget(self.lbl_slot_kin_status)
        plan_status_layout.addWidget(self.card_slot_kinetic, stretch=1)

        pw_lay.addLayout(plan_status_layout)

        # Drawer Cố vấn tài nguyên ngoại tuyến (G06-06B3-I2-P2)
        self.asset_advisory_drawer = AssetAdvisoryDrawer(self)
        pw_lay.addWidget(self.asset_advisory_drawer)

        # Header bước 2 + Bộ chọn Slot mục tiêu
        row_step2_header = QHBoxLayout()
        row_step2_header.setSpacing(6)

        lbl_step2 = QLabel("2. Dán mã JSON (Dán lần lượt hoặc dán cả 2 cùng lúc):")
        lbl_step2.setStyleSheet("color: #a1a1aa; font-size: 11px; margin-top: 2px;")
        row_step2_header.addWidget(lbl_step2)
        row_step2_header.addStretch(1)

        self.btn_target_auto = QPushButton("🤖 Tự động")
        self.btn_target_slot1 = QPushButton("🎬 Slot 1")
        self.btn_target_slot2 = QPushButton("✨ Slot 2")
        self.target_btn_group = QButtonGroup(self)
        self.target_btn_group.addButton(self.btn_target_auto, 0)
        self.target_btn_group.addButton(self.btn_target_slot1, 1)
        self.target_btn_group.addButton(self.btn_target_slot2, 2)

        target_btn_style = """
            QPushButton {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 10px;
                color: #a1a1aa;
            }
            QPushButton:hover {
                color: #ffffff;
                border-color: #3f3f46;
            }
            QPushButton:checked {
                background: #272930;
                color: #ffffff;
                border-color: #e05a47;
                font-weight: bold;
            }
        """
        for b in [self.btn_target_auto, self.btn_target_slot1, self.btn_target_slot2]:
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(target_btn_style)
            row_step2_header.addWidget(b)
        self.btn_target_auto.setChecked(True)
        self.target_btn_group.idClicked.connect(self._on_target_slot_changed)

        pw_lay.addLayout(row_step2_header)

        self.txt_json_input = QPlainTextEdit()
        self.txt_json_input.setPlaceholderText("Dán JSON kịch bản vào đây (Dán từng JSON hoặc dán cả 2 cùng lúc — Hệ thống nạp độc lập từng Slot không sợ đè)...")
        self.txt_json_input.setFixedHeight(75)
        self.txt_json_input.setStyleSheet("""
            QPlainTextEdit {
                background-color: #111214;
                border: 1px solid #2e3138;
                border-radius: 4px;
                color: #38bdf8;
                font-family: monospace;
                font-size: 11px;
                padding: 6px;
            }
            QPlainTextEdit:focus {
                border-color: #e05a47;
            }
        """)
        self.txt_json_input.textChanged.connect(self._on_json_text_changed)
        pw_lay.addWidget(self.txt_json_input)

        # Dropzone dán nhanh
        self.paste_zone = PasteZoneWidget(p_web)
        self.paste_zone.json_recognized.connect(self._on_dropzone_json_recognized)
        pw_lay.addWidget(self.paste_zone)

        self.lbl_json_hint = QLabel("💡 Bạn có thể dán lần lượt từng JSON hoặc dán cả 2 cùng lúc — Hệ thống nạp độc lập từng Slot.")
        self.lbl_json_hint.setStyleSheet("color: #71717a; font-size: 10.5px;")
        self.lbl_json_hint.setWordWrap(True)
        pw_lay.addWidget(self.lbl_json_hint)

        self.btn_apply_json = QPushButton("🎬 Áp Dụng Kịch Bản Này")
        self.btn_apply_json.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply_json.setStyleSheet("""
            QPushButton {
                background-color: #06b6d4;
                color: #000000;
                font-weight: bold;
                padding: 8px;
                border-radius: 6px;
                font-size: 11.5px;
                border: none;
            }
            QPushButton:hover {
                background-color: #22d3ee;
            }
        """)
        self.btn_apply_json.clicked.connect(self._apply_json_script)
        pw_lay.addWidget(self.btn_apply_json)

        # Nút Cập nhật / Xem đề xuất toàn bản (G06-06C3-S3)
        self.btn_update_proposal = QPushButton("📋 Cập nhật / Xem đề xuất toàn bản")
        self.btn_update_proposal.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_update_proposal.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                font-weight: bold;
                padding: 7px;
                border-radius: 6px;
                font-size: 11.5px;
                border: none;
            }
            QPushButton:hover {
                background-color: #3b82f6;
            }
        """)
        self.btn_update_proposal.clicked.connect(self._rebuild_and_present_proposal)
        pw_lay.addWidget(self.btn_update_proposal)

        # Khung Đánh giá Kịch bản Toàn bản (Common Review Widget — G06-06C3-S3)
        self.common_review_widget = CommonProposalReviewWidget(self)
        self.common_review_widget.proposal_approved.connect(self._on_proposal_approved)
        self.common_review_widget.candidate_selected.connect(self._on_candidate_selected)
        self.common_review_widget.candidate_omitted.connect(self._on_candidate_omitted)
        pw_lay.addWidget(self.common_review_widget)

        self.scroll_web.setWidget(p_web)
        self.ai_stack.addWidget(self.scroll_web)

        # ---------------------------------------------------------
        # Page 1: Cloud API (Gemini / DeepSeek / Claude / OpenAI)
        # ---------------------------------------------------------
        p_cloud = QWidget()
        pc_lay = QVBoxLayout(p_cloud)
        pc_lay.setContentsMargins(0, 0, 0, 0)
        pc_lay.setSpacing(8)

        row_c1 = QHBoxLayout()
        lbl_p = QLabel("Nhà cung cấp:")
        lbl_p.setStyleSheet("color: #e4e4e7; font-size: 11.5px; font-weight: 500;")
        row_c1.addWidget(lbl_p)

        self.combo_cloud_provider = QComboBox()
        self.combo_cloud_provider.addItem("Google Gemini (AI Studio - Tốc độ cao)", "gemini")
        self.combo_cloud_provider.addItem("DeepSeek V3 (Chi phí thấp, phản hồi nhanh)", "deepseek")
        self.combo_cloud_provider.addItem("Claude 3.7 Sonnet (Phân tích kịch bản sâu)", "claude")
        self.combo_cloud_provider.addItem("OpenAI GPT-4o (Đa dụng, ổn định)", "openai")
        self.combo_cloud_provider.setStyleSheet("""
            QComboBox {
                background-color: #111113;
                border: 1px solid #27272a;
                padding: 6px;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
            }
        """)
        self.combo_cloud_provider.currentIndexChanged.connect(self._on_cloud_provider_changed)
        row_c1.addWidget(self.combo_cloud_provider, stretch=1)
        pc_lay.addLayout(row_c1)

        row_c2 = QHBoxLayout()
        lbl_k = QLabel("API Key:")
        lbl_k.setStyleSheet("color: #e4e4e7; font-size: 11.5px; font-weight: 500;")
        row_c2.addWidget(lbl_k)

        self.txt_cloud_api_key = QLineEdit()
        self.txt_cloud_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_cloud_api_key.setPlaceholderText("Dán API Key vào đây (AQ... hoặc sk-...)")
        self.txt_cloud_api_key.setStyleSheet("""
            QLineEdit {
                background-color: #111113;
                border: 1px solid #27272a;
                padding: 6px;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
            }
            QLineEdit:focus {
                border-color: #e05a47;
            }
        """)
        self.txt_cloud_api_key.textChanged.connect(self._on_cloud_key_changed)
        row_c2.addWidget(self.txt_cloud_api_key, stretch=1)
        pc_lay.addLayout(row_c2)

        lbl_cloud_hint = QLabel("💡 Tự động nhận diện tiền tố: AQ./AIza (Gemini), sk-ant- (Claude), sk-proj- (OpenAI), sk- (DeepSeek). Lưu vĩnh viễn trên máy.")
        lbl_cloud_hint.setStyleSheet("color: #71717a; font-size: 10.5px;")
        lbl_cloud_hint.setWordWrap(True)
        pc_lay.addWidget(lbl_cloud_hint)

        self.lbl_cloud_status = QLabel("")
        self.lbl_cloud_status.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_cloud_status.setWordWrap(True)
        pc_lay.addWidget(self.lbl_cloud_status)

        self.btn_run_cloud = QPushButton("⚡ 1-Click: Cloud AI Tự Động Lên Kịch Bản && Dựng")
        self.btn_run_cloud.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run_cloud.setStyleSheet("""
            QPushButton {
                background-color: #d94f3d;
                color: #ffffff;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
                font-size: 11.5px;
                border: 1px solid #e86352;
            }
            QPushButton:hover {
                background-color: #e86352;
            }
        """)
        self.btn_run_cloud.clicked.connect(self._on_run_cloud_clicked)
        pc_lay.addWidget(self.btn_run_cloud)

        pc_lay.addStretch(1)
        self.ai_stack.addWidget(p_cloud)

        # ---------------------------------------------------------
        # Page 2: Local AI Ollama
        # ---------------------------------------------------------
        p_ollama = QWidget()
        po_lay = QVBoxLayout(p_ollama)
        po_lay.setContentsMargins(0, 0, 0, 0)
        po_lay.setSpacing(8)

        row_o1 = QHBoxLayout()
        lbl_m = QLabel("Mô hình:")
        lbl_m.setStyleSheet("color: #e4e4e7; font-size: 11.5px; font-weight: 500;")
        row_o1.addWidget(lbl_m)

        self.combo_ollama_model = QComboBox()
        self.combo_ollama_model.setEditable(True)
        self.combo_ollama_model.addItems(["llama3:latest", "qwen2.5:7b", "mistral:latest", "deepseek-r1:8b"])
        self.combo_ollama_model.setStyleSheet("""
            QComboBox {
                background-color: #111113;
                border: 1px solid #27272a;
                padding: 6px;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
            }
        """)
        self.combo_ollama_model.currentTextChanged.connect(self._on_ollama_model_changed)
        row_o1.addWidget(self.combo_ollama_model, stretch=1)
        po_lay.addLayout(row_o1)

        row_o2 = QHBoxLayout()
        lbl_ep = QLabel("Endpoint:")
        lbl_ep.setStyleSheet("color: #e4e4e7; font-size: 11.5px; font-weight: 500;")
        row_o2.addWidget(lbl_ep)

        self.txt_ollama_endpoint = QLineEdit("http://localhost:11434")
        self.txt_ollama_endpoint.setStyleSheet("""
            QLineEdit {
                background-color: #111113;
                border: 1px solid #27272a;
                padding: 6px;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
            }
        """)
        self.txt_ollama_endpoint.textChanged.connect(self._on_ollama_endpoint_changed)
        row_o2.addWidget(self.txt_ollama_endpoint, stretch=1)
        po_lay.addLayout(row_o2)

        row_o3 = QHBoxLayout()
        self.btn_ping_ollama = QPushButton("🔄 Kiểm tra kết nối")
        self.btn_ping_ollama.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ping_ollama.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
        """)
        self.btn_ping_ollama.clicked.connect(self._ping_ollama)
        row_o3.addWidget(self.btn_ping_ollama)

        self.lbl_ping_status = QLabel("Chưa kiểm tra")
        self.lbl_ping_status.setStyleSheet("color: #71717a; font-size: 10.5px;")
        row_o3.addWidget(self.lbl_ping_status, stretch=1)
        po_lay.addLayout(row_o3)

        self.lbl_ollama_status = QLabel("")
        self.lbl_ollama_status.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_ollama_status.setWordWrap(True)
        po_lay.addWidget(self.lbl_ollama_status)

        self.btn_run_ollama = QPushButton("💻 1-Click: Local AI Tự Động Lên Kịch Bản && Dựng")
        self.btn_run_ollama.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run_ollama.setStyleSheet("""
            QPushButton {
                background-color: #d94f3d;
                color: #ffffff;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
                font-size: 11.5px;
                border: 1px solid #e86352;
            }
            QPushButton:hover {
                background-color: #e86352;
            }
        """)
        self.btn_run_ollama.clicked.connect(self._on_run_ollama_clicked)
        po_lay.addWidget(self.btn_run_ollama)

        po_lay.addStretch(1)
        self.ai_stack.addWidget(p_ollama)

        # Kết nối 3 nút chuyển chế độ
        self.btn_mode_web.clicked.connect(lambda: self._switch_mode(0))
        self.btn_mode_cloud.clicked.connect(lambda: self._switch_mode(1))
        self.btn_mode_ollama.clicked.connect(lambda: self._switch_mode(2))

        v_card.addWidget(self.ai_stack)
        v_right.addWidget(ai_card, stretch=1)

        # Alias hỗ trợ mã cũ
        self.btn_ai_run = self.btn_run_cloud
        self.lbl_ai_res = self.lbl_cloud_status

        main_layout.addWidget(col_right, stretch=5)

        # Tải cấu hình đã lưu từ QSettings
        self._load_saved_settings()
        self._update_source_evidence_button_state()

    def _switch_mode(self, idx: int):
        self.btn_mode_web.setChecked(idx == 0)
        self.btn_mode_cloud.setChecked(idx == 1)
        self.btn_mode_ollama.setChecked(idx == 2)
        self.ai_stack.setCurrentIndex(idx)
        self.config_changed.emit()

    def _load_saved_settings(self):
        saved_key = self._settings.value("ai/api_key", "")
        if saved_key:
            self.txt_cloud_api_key.setText(str(saved_key))

        saved_prov = self._settings.value("ai/cloud_provider", "gemini")
        idx = self.combo_cloud_provider.findData(saved_prov)
        if idx >= 0:
            self.combo_cloud_provider.setCurrentIndex(idx)

        saved_model = self._settings.value("ai/ollama_model", "llama3:latest")
        if saved_model:
            self.combo_ollama_model.setCurrentText(str(saved_model))

        saved_endpoint = self._settings.value("ai/ollama_endpoint", "http://localhost:11434")
        if saved_endpoint:
            self.txt_ollama_endpoint.setText(str(saved_endpoint))

    def _on_cloud_key_changed(self, text: str):
        key = text.strip()
        self._settings.setValue("ai/api_key", key)
        # Tự nhận diện prefix
        if key.startswith("AQ.") or key.startswith("AIza"):
            idx = self.combo_cloud_provider.findData("gemini")
            if idx >= 0:
                self.combo_cloud_provider.setCurrentIndex(idx)
        elif key.startswith("sk-ant-"):
            idx = self.combo_cloud_provider.findData("claude")
            if idx >= 0:
                self.combo_cloud_provider.setCurrentIndex(idx)
        elif key.startswith("sk-proj-"):
            idx = self.combo_cloud_provider.findData("openai")
            if idx >= 0:
                self.combo_cloud_provider.setCurrentIndex(idx)
        elif key.startswith("sk-"):
            idx = self.combo_cloud_provider.findData("deepseek")
            if idx >= 0:
                self.combo_cloud_provider.setCurrentIndex(idx)
        self.config_changed.emit()

    def _on_cloud_provider_changed(self, idx: int):
        prov = self.combo_cloud_provider.currentData() or "gemini"
        self._settings.setValue("ai/cloud_provider", prov)
        self.config_changed.emit()

    def _on_ollama_model_changed(self, text: str):
        if text.strip():
            self._settings.setValue("ai/ollama_model", text.strip())
            self.config_changed.emit()

    def _on_ollama_endpoint_changed(self, text: str):
        if text.strip():
            self._settings.setValue("ai/ollama_endpoint", text.strip())
            self.config_changed.emit()

    def _ping_ollama(self):
        endpoint = self.txt_ollama_endpoint.text().strip() or "http://localhost:11434"
        self.lbl_ping_status.setText("Đang kiểm tra kết nối...")
        self.lbl_ping_status.setStyleSheet("color: #a78bfa; font-size: 10.5px;")

        self._ping_worker = OllamaPingWorker(endpoint, self)
        self._ping_worker.finished_ping.connect(self._on_ping_finished)
        self._ping_worker.start()

    def _on_ping_finished(self, is_online: bool, message: str, models: list):
        if is_online:
            self.lbl_ping_status.setText(f"🟢 {message}")
            self.lbl_ping_status.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
            if models:
                # Cập nhật danh sách model nếu chưa có
                current_items = [self.combo_ollama_model.itemText(i) for i in range(self.combo_ollama_model.count())]
                for m in models:
                    if m not in current_items:
                        self.combo_ollama_model.addItem(m)
        else:
            self.lbl_ping_status.setText(f"🔴 {message}")
            self.lbl_ping_status.setStyleSheet("color: #f87171; font-size: 10.5px;")

    def set_review_rows(self, rows: List[List[Any]]):
        """Cập nhật toàn bộ danh sách dòng thoại và làm mới giao diện."""
        self._review_rows = list(rows or [])
        self._review_cuts_modified = False
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._row_widgets.clear()

        if not self._review_rows:
            lbl_empty = QLabel("🔇 Không có câu thoại nào (Clip im lặng hoặc chưa có dữ liệu thoại)")
            lbl_empty.setStyleSheet("color: #71717a; font-size: 11px; padding: 12px; font-style: italic;")
            self.rows_layout.insertWidget(0, lbl_empty)
            self._row_widgets.append(lbl_empty)
            self.mini_timeline.set_segments([])
            return

        for i, row in enumerate(self._review_rows):
            tag = row[3] if len(row) > 3 else ""
            w = ReviewRowWidget(i, row[0], row[1], bool(row[2]), tag, self.rows_container)
            w.toggled.connect(self._on_row_toggled)
            self.rows_layout.insertWidget(i, w)
            self._row_widgets.append(w)
            if i % 30 == 0:
                QApplication.processEvents()

        # Cập nhật Mini Timeline
        blocks = []
        for i, r in enumerate(self._review_rows[:12]):
            lbl = f"Đoạn {i+1}"
            color = "#ef4444" if r[2] else ("#8b5cf6" if "hook" in str(r[3] if len(r) > 3 else "").lower() else "#22c55e")
            blocks.append((lbl, 10.0, color))
        if blocks:
            self.mini_timeline.set_segments(blocks)

    def set_story_metadata(self, style_intent: str = "travel_vlog", hook_dur: str = "30_60s"):
        """Cập nhật thể loại kịch bản và thời lượng Hook mong muốn."""
        self._style_intent = style_intent or "travel_vlog"
        self._hook_duration = hook_dur or "30_60s"
        self._prepared_prompt_snapshot = None
        self._invalidate_prepared_batches()

    def load_transcripts_from_cache(self, clip_infos: List[Any], whisper_model: str = "small", whisper_lang: str = "vi"):
        """Nạp các câu thoại thực tế từ bộ nhớ đệm (Adapter tương thích ngược, không chặn GUI)."""
        self.load_analysis_data(
            clip_infos=clip_infos,
            whisper_model=whisper_model,
            whisper_lang=whisper_lang,
        )

    def load_analysis_data(
        self,
        clip_infos: List[Any],
        snapshot: Optional[Any] = None,
        cache_mgr: Optional[Any] = None,
        visual_index: Optional[Any] = None,
        runtime_results: Optional[Dict[str, Any]] = None,
        whisper_model: Optional[str] = None,
        whisper_lang: Optional[str] = None,
        vision_model: Optional[str] = None,
        vision_endpoint: Optional[str] = None,
        **kwargs: Any,
    ):
        """Khởi chạy worker nạp siêu dữ liệu phân tích nguồn hai kênh (Thoại & Thị giác) bất đồng bộ.
        Áp dụng mô hình Transactional Input Preparation:
        - Chuẩn bị và kiểm định toàn diện in-memory TRƯỚC KHI thay đổi UI, generation hay cancel worker cũ.
        - Không ném uncaught exception tại Qt slot; log cảnh báo tiếng Việt và trả về an toàn khi input sai.
        - Giữ nguyên dữ liệu, selection, trạng thái UI khi reject.
        - Không thực hiện bất kỳ I/O nào (ffprobe, os.stat, content_hash) trên GUI thread.
        """
        # =========================================================================
        # 1. TRANSACTIONAL VALIDATION & INPUT PREPARATION (IN-MEMORY TRƯỚC MUTATE)
        # =========================================================================
        if clip_infos is None or not isinstance(clip_infos, (list, tuple)):
            logger.warning("Bỏ qua load_analysis_data do clip_infos không hợp lệ: %s", type(clip_infos))
            return

        import os
        import math
        import time
        import copy
        from types import SimpleNamespace
        from src.ui.director.workers.review_analysis_worker import (
            ReviewAnalysisWorker,
            ReviewAnalysisRequest,
            _normalize_duration,
        )

        # 1.1 Kiểm định snapshot và các thông số số học (Window, Sampling, Model)
        snap_ws = getattr(snapshot, "window_sec", None) if snapshot is not None else None
        if snap_ws is not None:
            if isinstance(snap_ws, bool):
                logger.warning("Bỏ qua load_analysis_data do snapshot.window_sec là boolean: %s", snap_ws)
                return
            try:
                w_sec = float(snap_ws)
                if math.isnan(w_sec) or math.isinf(w_sec) or w_sec <= 0.0 or w_sec > 1e6:
                    logger.warning("Bỏ qua load_analysis_data do snapshot.window_sec ngoài phạm vi: %s", snap_ws)
                    return
            except (TypeError, ValueError, OverflowError):
                logger.warning("Bỏ qua load_analysis_data do snapshot.window_sec không thể ép kiểu float: %s", snap_ws)
                return
        else:
            w_sec = 8.0

        snap_fpw = getattr(snapshot, "frames_per_window", None) if snapshot is not None else None
        if snap_fpw is not None:
            if isinstance(snap_fpw, bool):
                logger.warning("Bỏ qua load_analysis_data do snapshot.frames_per_window là boolean: %s", snap_fpw)
                return
            if isinstance(snap_fpw, float) and (math.isnan(snap_fpw) or math.isinf(snap_fpw)):
                logger.warning("Bỏ qua load_analysis_data do snapshot.frames_per_window không hữu hạn: %s", snap_fpw)
                return
            try:
                fpw = int(snap_fpw)
                if fpw < 1 or fpw > 10000:
                    logger.warning("Bỏ qua load_analysis_data do snapshot.frames_per_window ngoài phạm vi [1, 10000]: %s", snap_fpw)
                    return
            except (TypeError, ValueError, OverflowError):
                logger.warning("Bỏ qua load_analysis_data do snapshot.frames_per_window không thể ép kiểu int: %s", snap_fpw)
                return
        else:
            fpw = 2

        snap_mw = getattr(snapshot, "max_windows", None) if snapshot is not None else None
        if snap_mw is not None:
            if isinstance(snap_mw, bool):
                logger.warning("Bỏ qua load_analysis_data do snapshot.max_windows là boolean: %s", snap_mw)
                return
            if isinstance(snap_mw, float) and (math.isnan(snap_mw) or math.isinf(snap_mw)):
                logger.warning("Bỏ qua load_analysis_data do snapshot.max_windows không hữu hạn: %s", snap_mw)
                return
            try:
                max_w = int(snap_mw)
                if max_w < 1 or max_w > 1000000:
                    logger.warning("Bỏ qua load_analysis_data do snapshot.max_windows ngoài phạm vi [1, 1000000]: %s", snap_mw)
                    return
            except (TypeError, ValueError, OverflowError):
                logger.warning("Bỏ qua load_analysis_data do snapshot.max_windows không thể ép kiểu int: %s", snap_mw)
                return
        else:
            max_w = 10

        snap_mr = getattr(snapshot, "max_refinements", None) if snapshot is not None else None
        if snap_mr is not None:
            if isinstance(snap_mr, bool):
                logger.warning("Bỏ qua load_analysis_data do snapshot.max_refinements là boolean: %s", snap_mr)
                return
            if isinstance(snap_mr, float) and (math.isnan(snap_mr) or math.isinf(snap_mr)):
                logger.warning("Bỏ qua load_analysis_data do snapshot.max_refinements không hữu hạn: %s", snap_mr)
                return
            try:
                max_ref = int(snap_mr)
                if max_ref < 0 or max_ref > 10000:
                    logger.warning("Bỏ qua load_analysis_data do snapshot.max_refinements ngoài phạm vi [0, 10000]: %s", snap_mr)
                    return
            except (TypeError, ValueError, OverflowError):
                logger.warning("Bỏ qua load_analysis_data do snapshot.max_refinements không thể ép kiểu int: %s", snap_mr)
                return
        else:
            max_ref = 1

        w_model = whisper_model or getattr(snapshot, "whisper_model", None) or getattr(self, "_whisper_model", "small")
        if not isinstance(w_model, str) or not w_model.strip():
            w_model = "small"
        else:
            w_model = w_model.strip()

        w_lang = whisper_lang or getattr(snapshot, "whisper_language", None) or getattr(self, "_whisper_lang", "vi")
        if not isinstance(w_lang, str) or not w_lang.strip():
            w_lang = "vi"
        else:
            w_lang = w_lang.strip()

        v_model = vision_model or getattr(snapshot, "vision_model", "") or ""
        if not isinstance(v_model, str):
            v_model = ""
        else:
            v_model = v_model.strip()

        v_endpoint = vision_endpoint or getattr(snapshot, "vision_endpoint", "http://127.0.0.1:11434")
        if not isinstance(v_endpoint, str) or not v_endpoint.strip():
            v_endpoint = "http://127.0.0.1:11434"
        else:
            v_endpoint = v_endpoint.strip()

        v_enable = bool(getattr(snapshot, "enable_visual_describe", False)) if snapshot else bool(v_model)

        # 1.2 Bóc tách an toàn danh sách clip và chuẩn hóa primitive paths/durations
        ordered_raw_clips = list(clip_infos)
        snapshotted_clip_infos = []

        for idx, c in enumerate(ordered_raw_clips):
            if isinstance(c, (str, os.PathLike)):
                try:
                    p_str = os.fspath(c).strip()
                except Exception:
                    logger.warning("Bỏ qua load_analysis_data do đường dẫn clip không hợp lệ tại vị trí %d", idx)
                    return
                if not p_str:
                    logger.warning("Bỏ qua load_analysis_data do đường dẫn clip rỗng tại vị trí %d", idx)
                    return
                snapshotted_clip_infos.append(
                    SimpleNamespace(
                        path=p_str,
                        duration_sec=None,
                        display_name=os.path.basename(p_str),
                        shot_time=None,
                    )
                )
            elif hasattr(c, "path"):
                raw_p = getattr(c, "path")
                if not isinstance(raw_p, (str, os.PathLike)):
                    logger.warning("Bỏ qua load_analysis_data do clip.path không phải str/PathLike tại vị trí %d: %s", idx, type(raw_p))
                    return
                try:
                    p_str = os.fspath(raw_p).strip()
                except Exception:
                    logger.warning("Bỏ qua load_analysis_data do không thể trích xuất fspath tại vị trí %d", idx)
                    return
                if not p_str:
                    logger.warning("Bỏ qua load_analysis_data do clip.path rỗng tại vị trí %d", idx)
                    return

                raw_d = getattr(c, "duration_sec", None)
                d_val, d_ok = _normalize_duration(raw_d)

                raw_dn = getattr(c, "display_name", None)
                if isinstance(raw_dn, str) and raw_dn.strip():
                    dn = raw_dn.strip()
                else:
                    dn = os.path.basename(p_str)

                raw_st = getattr(c, "shot_time", None)
                st_val = None
                if raw_st is not None and not isinstance(raw_st, bool):
                    try:
                        st_f = float(raw_st)
                        if not math.isnan(st_f) and not math.isinf(st_f):
                            st_val = st_f
                    except (TypeError, ValueError, OverflowError):
                        st_val = None

                snapshotted_clip_infos.append(
                    SimpleNamespace(
                        path=p_str,
                        duration_sec=d_val if d_ok else None,
                        display_name=dn,
                        shot_time=st_val,
                    )
                )
            else:
                logger.warning("Bỏ qua load_analysis_data do phần tử thiếu thuộc tính path tại vị trí %d: %s", idx, type(c))
                return

        # 1.3 Sắp xếp thuần in-memory (Nhiệm vụ C - Tuyệt đối không I/O trên GUI thread)
        def _in_memory_sort_key(item_tuple):
            idx, orig_c, snap_c = item_tuple
            st = snap_c.shot_time
            if st is not None:
                return (0, st, idx)
            return (1, 0.0, idx)

        paired_items = [
            (i, ordered_raw_clips[i], snapshotted_clip_infos[i])
            for i in range(len(ordered_raw_clips))
        ]
        paired_items.sort(key=_in_memory_sort_key)

        final_raw_clips = [p[1] for p in paired_items]
        final_snap_clips = [p[2] for p in paired_items]
        final_raw_paths = tuple(sc.path for sc in final_snap_clips)

        # 1.4 Sao chép an toàn runtime_results (Deepcopy in-memory có try/catch)
        rt_copy = None
        if runtime_results is not None:
            if not isinstance(runtime_results, dict):
                logger.warning("Bỏ qua load_analysis_data do runtime_results không phải dict: %s", type(runtime_results))
                return
            try:
                rt_copy = copy.deepcopy(runtime_results)
            except Exception as cp_err:
                logger.warning("Bỏ qua load_analysis_data do không thể deepcopy runtime_results: %s", cp_err)
                return

        # =========================================================================
        # 2. COMMIT STATE & START ASYNC WORKER (CHỈ SAU KHI PREPARATION THÀNH CÔNG)
        # =========================================================================
        self._review_close_pending = False
        self._clip_infos = final_raw_clips

        # Tăng generation token để vô hiệu hóa callback của các worker cũ
        self._analysis_generation += 1
        gen = self._analysis_generation
        req_id = f"rev_req_{gen}_{int(time.time()*1000)}"

        # Yêu cầu dừng cooperative các worker đang chạy
        for old_w in list(self._active_analysis_workers.values()):
            if hasattr(old_w, "cancel"):
                try:
                    old_w.cancel()
                except Exception:
                    pass

        # Xóa sạch hàng thoại và quan sát cũ ngay khi bắt đầu nạp danh sách mới
        self.set_review_rows([])
        self.visual_model.set_observations([])
        self._prepared_prompt_snapshot = None
        self._invalidate_prepared_batches()

        self._whisper_model = w_model
        self._whisper_lang = w_lang

        # Đánh dấu trạng thái đang nạp
        self._is_analysis_loading = True
        if hasattr(self, "lbl_visual_status"):
            self.lbl_visual_status.setText("⏳ Đang nạp quan sát...")

        req = ReviewAnalysisRequest(
            request_id=req_id,
            generation=gen,
            video_paths=final_raw_paths,
            clip_infos=tuple(final_snap_clips),
            snapshot=snapshot,
            whisper_model=w_model,
            whisper_language=w_lang,
            vision_model=v_model,
            vision_endpoint=v_endpoint,
            enable_visual_describe=v_enable,
            runtime_results=rt_copy,
            window_sec=w_sec,
            frames_per_window=fpw,
            max_windows=max_w,
            max_refinements=max_ref,
        )

        self._pending_request_payloads[req_id] = []

        worker = ReviewAnalysisWorker(
            request=req,
            cache_mgr=cache_mgr,
            visual_index=visual_index,
            parent=self,
        )
        self._active_analysis_workers[req_id] = worker

        worker.clip_loaded_signal.connect(self._on_analysis_clip_loaded)
        worker.finished_signal.connect(self._on_analysis_finished)
        worker.finished.connect(lambda w=worker, r=req_id: self._on_analysis_worker_thread_finished(r, w))

        worker.start()

    def _on_analysis_clip_loaded(self, req_id: str, clip_path: str, payload: dict):
        if getattr(self, "_review_close_pending", False):
            return
        if req_id in self._pending_request_payloads:
            self._pending_request_payloads[req_id].append(payload)

    def _on_analysis_finished(self, req_id: str, success: bool, message: str, summary: dict):
        payloads = self._pending_request_payloads.pop(req_id, [])
        if getattr(self, "_review_close_pending", False):
            return
        worker = self._active_analysis_workers.get(req_id)
        if not worker or worker.request.generation != self._analysis_generation:
            # Yêu cầu cũ đã bị supersede, bỏ qua trong yên lặng
            return

        self._is_analysis_loading = False

        combined_speech_rows: List[List[Any]] = []
        combined_visual_obs: List[Dict[str, Any]] = []

        for p in payloads:
            # Bỏ qua dữ liệu của clip có nguồn không hợp lệ
            sp_st = p.get("speech_status")
            vis_st = p.get("visual_status")
            is_invalid_source = (sp_st in ("source_missing", "source_changed") or vis_st in ("source_missing", "source_changed"))
            if is_invalid_source:
                continue

            for row in p.get("subtitle_rows", []):
                combined_speech_rows.append(row)
            for v in p.get("visual_observations", []):
                combined_visual_obs.append({
                    **v,
                    "clip_name": p.get("display_name", "")
                })

        # Cập nhật danh sách câu thoại (giữ rows hợp lệ)
        self.set_review_rows(combined_speech_rows)

        # Cập nhật bảng quan sát thị giác
        self.visual_model.set_observations(combined_visual_obs)
        if hasattr(self, "lbl_visual_status"):
            v_count = len(combined_visual_obs)
            if not success:
                if message == "cancelled":
                    self.lbl_visual_status.setText(f"⚠ Đã dừng ({v_count} quan sát)")
                elif message == "source_unavailable":
                    self.lbl_visual_status.setText("⚠ Tệp nguồn không khả dụng (thiếu hoặc bị chỉnh sửa)")
                else:
                    self.lbl_visual_status.setText(f"⚠ Lỗi nạp ({message}) - {v_count} quan sát")
            elif message == "partial":
                self.lbl_visual_status.setText(f"⚠ Nạp một phần: {v_count} quan sát")
            elif message in ("no_data", "zero_clips"):
                self.lbl_visual_status.setText("Chưa có dữ liệu phân tích")
            elif v_count > 0:
                self.lbl_visual_status.setText(f"✔ {v_count} quan sát thị giác")
            else:
                self.lbl_visual_status.setText("Không có quan sát thị giác")

    def _on_analysis_worker_thread_finished(self, req_id: str, worker: Any):
        self._pending_request_payloads.pop(req_id, None)
        self._active_analysis_workers.pop(req_id, None)
        try:
            worker.deleteLater()
        except Exception:
            pass
        if (
            not self._active_analysis_workers
            and not self._prompt_workers_registry
            and not self._kinetic_workers_registry
            and not self._matching_workers_registry
            and not self._preapproval_evidence_workers
        ):
            self._is_analysis_loading = False
            self.analysis_workers_idle.emit()
        self._update_source_evidence_button_state()

    def _on_prompt_worker_thread_finished(self, req_id: str, worker: Any):
        """Dọn dẹp tài nguyên khi QThread chuẩn bị prompt kết thúc vật lý."""
        self._prompt_workers_registry.pop(req_id, None)
        try:
            worker.deleteLater()
        except Exception:
            pass

        # Tính busy aggregate hiện tại của toàn registry
        if self._prompt_workers_registry:
            # Nếu registry vẫn còn worker đang chạy trong nền: nút giữ khóa
            if hasattr(self, "btn_copy_video_prompt"):
                self.btn_copy_video_prompt.setEnabled(False)
            if hasattr(self, "btn_prepare_batches"):
                self.btn_prepare_batches.setEnabled(False)
            if hasattr(self, "btn_copy_selected_batch"):
                self.btn_copy_selected_batch.setEnabled(False)
            if hasattr(self, "btn_submit_batch_draft"):
                self.btn_submit_batch_draft.setEnabled(False)
        else:
            # Registry đã hoàn toàn rỗng (tất cả worker đã hoàn tất vật lý)
            if not getattr(self, "_review_close_pending", False):
                if hasattr(self, "btn_copy_video_prompt"):
                    self.btn_copy_video_prompt.setEnabled(True)
                    # Phục hồi nhãn nút: nếu active request đã có snapshot thành công,
                    # KHÔNG đè nhãn của active request. Nếu chưa có snapshot thành công,
                    # khôi phục nhãn gốc.
                    has_active_success = (
                        self._prepared_prompt_snapshot is not None
                        and self._prepared_prompt_snapshot.get("request_id") == self._active_prompt_request_id
                    )
                    if not has_active_success:
                        self.btn_copy_video_prompt.setText("📋 1. Prompt Dựng Video (V1)")
                if hasattr(self, "btn_prepare_batches"):
                    self.btn_prepare_batches.setEnabled(True)
                    self.btn_prepare_batches.setText("📦 4. Phân Lô Kịch Bản (Batch Planner)")
                if hasattr(self, "btn_copy_selected_batch"):
                    has_batches = bool(self._prepared_batch_snapshot and (getattr(self._prepared_batch_snapshot, "batches", None) or self._prepared_batch_snapshot.get("batches")))
                    curr_ctx = self._get_current_context()
                    curr_sig = self._extract_context_signature(curr_ctx)
                    snap_sig = (
                        getattr(self._prepared_batch_snapshot, "ui_context_signature", None)
                        or (self._prepared_batch_snapshot.get("ui_context_signature") if isinstance(self._prepared_batch_snapshot, dict) else None)
                        or (self._prepared_batch_snapshot.get("context_signature") if isinstance(self._prepared_batch_snapshot, dict) else None)
                    ) if self._prepared_batch_snapshot else None
                    curr_cc = curr_ctx.get("creative_context")
                    curr_cc_sig = (
                        compute_creative_context_signature(curr_cc)
                        if (curr_cc is not None and curr_cc != CreativeContext())
                        else None
                    )
                    snap_cc_sig = (
                        getattr(self._prepared_batch_snapshot, "creative_context_signature", None)
                        if not isinstance(self._prepared_batch_snapshot, dict)
                        else self._prepared_batch_snapshot.get("creative_context_signature")
                    ) if self._prepared_batch_snapshot else None
                    is_match = has_batches and (curr_sig == snap_sig) and (curr_cc_sig == snap_cc_sig)
                    self.btn_copy_selected_batch.setEnabled(is_match)
                    self.btn_copy_selected_batch.setText("📋 Sao Chép Lô")
                if hasattr(self, "btn_submit_batch_draft"):
                    has_batches = bool(self._prepared_batch_snapshot and (getattr(self._prepared_batch_snapshot, "batches", None) or self._prepared_batch_snapshot.get("batches")))
                    curr_ctx = self._get_current_context()
                    curr_sig = self._extract_context_signature(curr_ctx)
                    snap_sig = (
                        getattr(self._prepared_batch_snapshot, "ui_context_signature", None)
                        or (self._prepared_batch_snapshot.get("ui_context_signature") if isinstance(self._prepared_batch_snapshot, dict) else None)
                        or (self._prepared_batch_snapshot.get("context_signature") if isinstance(self._prepared_batch_snapshot, dict) else None)
                    ) if self._prepared_batch_snapshot else None
                    curr_cc = curr_ctx.get("creative_context")
                    curr_cc_sig = (
                        compute_creative_context_signature(curr_cc)
                        if (curr_cc is not None and curr_cc != CreativeContext())
                        else None
                    )
                    snap_cc_sig = (
                        getattr(self._prepared_batch_snapshot, "creative_context_signature", None)
                        if not isinstance(self._prepared_batch_snapshot, dict)
                        else self._prepared_batch_snapshot.get("creative_context_signature")
                    ) if self._prepared_batch_snapshot else None
                    is_match = has_batches and (curr_sig == snap_sig) and (curr_cc_sig == snap_cc_sig)
                    self.btn_submit_batch_draft.setEnabled(is_match)
                    self.btn_submit_batch_draft.setText("📥 Nạp Bản Thảo Cho Lô Đang Chọn")

        self._update_overview_button_state()
        self._update_source_evidence_button_state()

        if (
            not self._active_analysis_workers
            and not self._prompt_workers_registry
            and not self._kinetic_workers_registry
            and not self._matching_workers_registry
            and not self._preapproval_evidence_workers
        ):
            self._is_analysis_loading = False
            self.analysis_workers_idle.emit()

    def _on_kinetic_worker_thread_finished(self, req_id: str, worker: Any):
        """Dọn dẹp tài nguyên khi QThread chuẩn bị kinetic V2 kết thúc vật lý."""
        self._kinetic_workers_registry.pop(req_id, None)
        try:
            worker.deleteLater()
        except Exception:
            pass

        # Tính busy aggregate của kinetic registry
        if self._kinetic_workers_registry:
            if hasattr(self, "btn_copy_kinetic_prompt"):
                self.btn_copy_kinetic_prompt.setEnabled(False)
            if hasattr(self, "btn_auto_kinetic"):
                self.btn_auto_kinetic.setEnabled(False)
            if hasattr(self, "btn_copy_final_transcript"):
                self.btn_copy_final_transcript.setEnabled(False)
        else:
            # Registry rỗng
            if not getattr(self, "_review_close_pending", False):
                if hasattr(self, "btn_copy_kinetic_prompt"):
                    self.btn_copy_kinetic_prompt.setEnabled(True)
                if hasattr(self, "btn_auto_kinetic"):
                    self.btn_auto_kinetic.setEnabled(True)
                if hasattr(self, "btn_copy_final_transcript"):
                    self.btn_copy_final_transcript.setEnabled(True)

                has_active_success = (
                    self._prepared_kinetic_snapshot is not None
                    and self._prepared_kinetic_snapshot.get("request_id") == self._active_kinetic_request_id
                )
                if not has_active_success:
                    if hasattr(self, "btn_copy_kinetic_prompt"):
                        self.btn_copy_kinetic_prompt.setText("✨ 3. Prompt Chữ Kinetic (V2)")
                    if hasattr(self, "btn_auto_kinetic"):
                        self.btn_auto_kinetic.setText("⚡ Tự tạo (0đ Token)")
                    if hasattr(self, "btn_copy_final_transcript"):
                        self.btn_copy_final_transcript.setText("📑 Copy Transcript Bản Dựng")

        self._update_overview_button_state()
        self._update_source_evidence_button_state()

        if (
            not self._active_analysis_workers
            and not self._prompt_workers_registry
            and not self._kinetic_workers_registry
            and not self._matching_workers_registry
            and not self._preapproval_evidence_workers
        ):
            self._is_analysis_loading = False
            self.analysis_workers_idle.emit()

    def _collect_valid_overview_drafts(self, snapshot_id: str):
        """
        Thu thập và kiểm tra tính hợp lệ của toàn bộ các bản thảo lô thuộc snapshot hiện tại.
        Trả về tuple: (drafts_list, ledger_signature, error_message).
        Nếu dữ liệu không hợp lệ (corrupt RAM/entry/key/counters/signature fail):
            trả về (None, None, error_message) an toàn, tuyệt đối không ném exception ra ngoài.
        Nếu ledger rỗng (0 drafts):
            trả về ([], None, "empty") an toàn.
        Nếu hợp lệ (>= 1 draft):
            trả về (drafts_list, ledger_signature, None).
        """
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            return None, None, "snapshot_id không hợp lệ"

        ledger = getattr(self, "_batch_drafts_ledger", None)
        if not isinstance(ledger, dict):
            return None, None, "ledger không phải là dict"

        collected = []
        seen_batches = set()

        for k, entry in ledger.items():
            # Key bắt buộc phải là tuple 2 phần tử: (snapshot_id, batch_id)
            if not isinstance(k, tuple) or len(k) != 2:
                return None, None, f"Khóa ledger không phải 2-tuple: {k!r}"
            k_snap, k_batch = k
            if not isinstance(k_snap, str) or not isinstance(k_batch, str) or not k_snap.strip() or not k_batch.strip():
                return None, None, f"Phần tử trong khóa ledger không phải chuỗi hợp lệ: {k!r}"

            if k_snap != snapshot_id:
                continue

            # Thẩm định entry: bắt buộc là object DTO hợp lệ, không phải primitives
            if entry is None or isinstance(entry, (bool, int, float, str, bytes, list, tuple, set, dict)):
                return None, None, f"Entry trong ledger cho {k!r} có kiểu không hợp lệ: {type(entry)}"

            # Kiểm tra các thuộc tính tối thiểu của DTO
            e_snap = getattr(entry, "snapshot_id", None)
            e_batch = getattr(entry, "batch_id", None)
            if e_snap != k_snap or e_batch != k_batch:
                return None, None, f"Inner ID của entry ({e_snap!r}, {e_batch!r}) không khớp khóa ledger ({k_snap!r}, {k_batch!r})"

            if k_batch in seen_batches:
                return None, None, f"Trùng lặp batch_id trong ledger: {k_batch!r}"
            seen_batches.add(k_batch)
            collected.append(entry)

        if not collected:
            return [], None, "Chưa có bản thảo nào cho snapshot hiện tại"

        # Sắp xếp an toàn theo batch_id
        try:
            collected.sort(key=lambda d: getattr(d, "batch_id", ""))
        except Exception as e:
            return None, None, f"Lỗi sắp xếp bản thảo: {e}"

        # Tính toán chữ ký ledger qua helper core/worker, bọc try/except toàn diện
        from src.ui.director.workers.prompt_preparation_worker import compute_project_overview_ledger_signature
        try:
            sig = compute_project_overview_ledger_signature(snapshot_id, tuple(collected))
            return collected, sig, None
        except Exception as e:
            return None, None, f"Lỗi thẩm định chữ ký ledger: {e}"

    def _update_overview_button_state(self):
        """Cập nhật trạng thái bật/tắt của nút sao chép tổng quan dự án (cheap GUI refresh)."""
        if not hasattr(self, "btn_copy_project_overview"):
            return

        if getattr(self, "_review_close_pending", False):
            self.btn_copy_project_overview.setEnabled(False)
            return

        # Physical busy check: phân tích nguồn, prompt worker, hoặc kinetic worker đang chạy
        has_busy_workers = (
            bool(getattr(self, "_active_analysis_workers", None))
            or bool(getattr(self, "_prompt_workers_registry", None))
            or bool(getattr(self, "_kinetic_workers_registry", None))
        )
        if has_busy_workers:
            self.btn_copy_project_overview.setEnabled(False)
            return

        if not self._prepared_batch_snapshot:
            self.btn_copy_project_overview.setEnabled(False)
            return

        from src.ui.director.workers.prompt_preparation_worker import validate_and_normalize_snapshot
        norm_snap = validate_and_normalize_snapshot(self._prepared_batch_snapshot)
        if norm_snap is None:
            self.btn_copy_project_overview.setEnabled(False)
            return

        curr_ctx = self._get_current_context()
        if not curr_ctx:
            self.btn_copy_project_overview.setEnabled(False)
            return
        curr_sig = self._extract_context_signature(curr_ctx)
        if not curr_sig:
            self.btn_copy_project_overview.setEnabled(False)
            return

        # ZERO FALLBACK sang context_signature trong overview path
        snap_sig = getattr(norm_snap, "ui_context_signature", None)
        if snap_sig is None and isinstance(self._prepared_batch_snapshot, dict):
            snap_sig = self._prepared_batch_snapshot.get("ui_context_signature")
        if not snap_sig or curr_sig != snap_sig:
            self.btn_copy_project_overview.setEnabled(False)
            return

        # Thẩm định creative signature của snapshot so với context
        curr_cc = curr_ctx.get("creative_context")
        curr_cc_sig = compute_creative_context_signature(curr_cc) if (curr_cc is not None and curr_cc != CreativeContext()) else None
        snap_cc_sig = getattr(norm_snap, "creative_context_signature", None)
        if curr_cc_sig != snap_cc_sig:
            self.btn_copy_project_overview.setEnabled(False)
            return

        # Controlled collection: kiểm tra toàn vẹn bản thảo của snapshot hiện tại
        curr_drafts, sig, draft_err = self._collect_valid_overview_drafts(norm_snap.snapshot_id)
        if not curr_drafts or sig is None:
            self.btn_copy_project_overview.setEnabled(False)
            return

        self.btn_copy_project_overview.setEnabled(True)
        self.btn_copy_project_overview.setText("📋 Sao Chép Tổng Quan Dự Án")

    def notify_creative_context_changed(self):
        """Xử lý thông báo khi StylePage (hoặc cấu hình sáng tạo) thay đổi."""
        self._prepared_prompt_snapshot = None
        self._invalidate_prepared_batches()
        if hasattr(self, "common_review_widget") and self.common_review_widget:
            self.common_review_widget.set_stale(True)
        self._current_approval_record = None
        if hasattr(self, "lbl_json_hint"):
            self.lbl_json_hint.setText("⚠ Yêu cầu thay đổi, chuẩn bị lại.")
            self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px;")
        if hasattr(self, "lbl_overview_status"):
            self.lbl_overview_status.setText("⚠ Yêu cầu thay đổi, chuẩn bị lại.")
            self.lbl_overview_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
        if hasattr(self, "btn_copy_video_prompt"):
            self.btn_copy_video_prompt.setText("📋 1. Prompt Dựng Video (V1)")
        if hasattr(self, "btn_copy_project_overview"):
            self.btn_copy_project_overview.setEnabled(False)
            self.btn_copy_project_overview.setText("📋 Sao Chép Tổng Quan Dự Án")
        self._update_overview_button_state()

    def _on_copy_project_overview_clicked(self):
        """Kích hoạt chuẩn bị và sao chép Prompt Tổng Quan Toàn Dự Án."""
        self.request_prompt_preparation(intent="copy_project_overview")

    def shutdown_analysis_workers(self) -> bool:
        """Yêu cầu hủy cooperative toàn bộ worker nạp siêu dữ liệu, prompt V1, kinetic V2, matching và preapproval đang chạy."""
        self._review_close_pending = True
        self._analysis_generation += 1
        self._prompt_generation += 1
        self._kinetic_generation += 1
        self._matching_generation += 1
        self._preapproval_evidence_generation += 1
        self._matching_pending_context = None
        self._pending_preapproval_expected = None
        for w in list(self._active_analysis_workers.values()):
            if hasattr(w, "cancel"):
                try:
                    w.cancel()
                except Exception:
                    pass
        for pw in list(self._prompt_workers_registry.values()):
            if hasattr(pw, "cancel"):
                try:
                    pw.cancel()
                except Exception:
                    pass
        for kw in list(self._kinetic_workers_registry.values()):
            if hasattr(kw, "cancel"):
                try:
                    kw.cancel()
                except Exception:
                    pass
        for mw in list(self._matching_workers_registry.values()):
            if hasattr(mw, "cancel"):
                try:
                    mw.cancel()
                except Exception:
                    pass
        for sew in list(self._preapproval_evidence_workers.values()):
            if hasattr(sew, "cancel"):
                try:
                    sew.cancel()
                except Exception:
                    pass
        return (
            len(self._active_analysis_workers) == 0
            and len(self._prompt_workers_registry) == 0
            and len(self._kinetic_workers_registry) == 0
            and len(self._matching_workers_registry) == 0
            and len(self._preapproval_evidence_workers) == 0
        )

    def is_analysis_idle(self) -> bool:
        return (
            len(self._active_analysis_workers) == 0
            and len(self._prompt_workers_registry) == 0
            and len(self._kinetic_workers_registry) == 0
            and len(self._matching_workers_registry) == 0
            and len(self._preapproval_evidence_workers) == 0
        )

    def is_analysis_loading(self) -> bool:
        return (
            self._is_analysis_loading
            or bool(self._prompt_workers_registry)
            or bool(self._kinetic_workers_registry)
            or bool(self._matching_workers_registry)
            or bool(self._preapproval_evidence_workers)
        )

    def is_matching_busy(self) -> bool:
        """True nếu đang có worker cố vấn tài nguyên chạy trên luồng nền."""
        return len(self._matching_workers_registry) > 0

    def _update_matching_button_state(self) -> None:
        """Cập nhật trạng thái bật/tắt của nút btn_match_assets theo đúng chính sách physical-busy."""
        if not hasattr(self, "btn_match_assets"):
            return
        if getattr(self, "_review_close_pending", False):
            self.btn_match_assets.setEnabled(False)
            return
        if self.is_matching_busy():
            self.btn_match_assets.setEnabled(False)
            return
        raw_plan = getattr(self, "_video_plan_data", None)
        if raw_plan and isinstance(raw_plan, Mapping):
            self.btn_match_assets.setEnabled(True)
        else:
            self.btn_match_assets.setEnabled(False)

    def is_source_evidence_busy(self) -> bool:
        """True nếu đang có worker kiểm tra bằng chứng nguồn chạy trên luồng nền."""
        return len(self._preapproval_evidence_workers) > 0

    def _update_source_evidence_button_state(self) -> None:
        """Cập nhật trạng thái bật/tắt của nút btn_check_source_evidence."""
        if not hasattr(self, "btn_check_source_evidence"):
            return
        if getattr(self, "_review_close_pending", False):
            self.btn_check_source_evidence.setEnabled(False)
            return
        if self.is_source_evidence_busy():
            self.btn_check_source_evidence.setEnabled(True)
            self.btn_check_source_evidence.setText("⏹ Hủy kiểm tra nguồn")
            return
        if (
            bool(getattr(self, "_active_analysis_workers", None))
            or bool(getattr(self, "_prompt_workers_registry", None))
            or bool(getattr(self, "_kinetic_workers_registry", None))
            or bool(getattr(self, "_matching_workers_registry", None))
        ):
            self.btn_check_source_evidence.setEnabled(False)
            return
        manifest = getattr(self, "_manifest", None)
        video_plan = getattr(self, "_video_plan_data", None)
        has_sources = False
        if manifest is not None:
            clips = manifest.get("clips") if isinstance(manifest, Mapping) else getattr(manifest, "clips", ())
            has_sources = bool(clips)
        can_check = bool(has_sources and video_plan)
        self.btn_check_source_evidence.setText("🔍 Kiểm tra nguồn trước khi duyệt")
        self.btn_check_source_evidence.setEnabled(can_check)

    def _get_or_create_preapproval_service(self) -> Any:
        if self._preapproval_evidence_service is None:
            from src.core.geometry.source_evidence_service import SourceEvidenceService
            self._preapproval_evidence_service = SourceEvidenceService()
        return self._preapproval_evidence_service

    def _on_preapproval_progress(self, percent: int, msg: str) -> None:
        """Cập nhật tiến độ quét bằng chứng nguồn."""
        if hasattr(self, "lbl_source_evidence_status") and not getattr(self, "_review_close_pending", False):
            self.lbl_source_evidence_status.setText(f"⏳ Đang kiểm tra ({percent}%): {msg}")

    def _on_preapproval_terminal(self, payload: Any, *, worker: Optional[Any] = None) -> None:
        """Tiếp nhận terminal payload từ PreapprovalEvidenceWorker qua signal Qt."""
        try:
            from src.ui.director.workers.preapproval_evidence_worker import (
                PreapprovalEvidenceRequest,
                PreapprovalEvidenceTerminalPayload,
                normalize_preapproval_terminal_payload,
            )

            if not isinstance(payload, PreapprovalEvidenceTerminalPayload):
                logger.warning("Bỏ qua terminal payload không đúng kiểu: %r", type(payload))
                return

            expected = getattr(self, "_pending_preapproval_expected", None)
            if expected is None or not isinstance(expected, tuple) or len(expected) != 5:
                logger.warning("Bỏ qua terminal payload do pending expected không hợp lệ (bắt buộc 5-tuple): %r", expected)
                return

            exp_worker, exp_req_id, exp_gen, exp_sig, exp_items = expected

            # 1. Strict raw type check trên generation và so khớp current generation
            current_gen = getattr(self, "_preapproval_evidence_generation", None)
            if (
                type(payload.generation) is not int
                or isinstance(payload.generation, bool)
                or type(current_gen) is not int
                or isinstance(current_gen, bool)
                or payload.generation != current_gen
            ):
                logger.warning(
                    "Bỏ qua terminal payload có generation không khớp current page generation (payload=%r, current=%r)",
                    payload.generation, current_gen
                )
                return

            # 2. Strict ID/generation/signature matching
            if (payload.request_id, payload.generation, payload.input_signature) != (exp_req_id, exp_gen, exp_sig):
                logger.info(
                    "Bỏ qua terminal payload do sai khớp định danh (nhận (%s, %s, %s) vs chờ (%s, %s, %s))",
                    payload.request_id, payload.generation, payload.input_signature, exp_req_id, exp_gen, exp_sig
                )
                return

            # 3. Exact sender/worker instance verification
            registered_w = self._preapproval_evidence_workers.get(payload.request_id)
            effective_worker = worker if worker is not None else self.sender()
            if registered_w is None or effective_worker is None or effective_worker is not registered_w or (exp_worker is not None and effective_worker is not exp_worker):
                logger.info("Bỏ qua terminal payload: worker không khớp registered / pending instance")
                return

            # 4. Thẩm định active context hiện hành trước khi mutate
            curr_ctx = self._get_current_context() or {}
            try:
                curr_pending_sig = self._build_pending_scan_input_signature(curr_ctx)
            except Exception as exc:
                logger.warning("Không thể tính base signature hiện hành tại terminal: %s", exc)
                self._pending_preapproval_expected = None
                if hasattr(self, "lbl_source_evidence_status"):
                    self.lbl_source_evidence_status.setText("⚠ Dữ liệu ngữ cảnh không hợp lệ khi nhận kết quả quét.")
                    self.lbl_source_evidence_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                return

            if curr_pending_sig != payload.input_signature:
                logger.info("Dữ liệu nguồn đã thay đổi trong khi quét, từ chối nhận kết quả cũ.")
                self._pending_preapproval_expected = None
                if hasattr(self, "lbl_source_evidence_status"):
                    self.lbl_source_evidence_status.setText("⚠ Kết quả quét đã cũ do dữ liệu nguồn đã thay đổi.")
                    self.lbl_source_evidence_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                return

            # 5. Xác định expected request để thẩm định qua shared terminal normalizer
            req_to_verify = getattr(exp_worker, "_request", None)
            if not isinstance(req_to_verify, PreapprovalEvidenceRequest):
                req_to_verify = getattr(exp_worker, "request", None)
            if not isinstance(req_to_verify, PreapprovalEvidenceRequest):
                manifest = getattr(self, "_manifest", None)
                m_id = manifest.get("manifest_id") if isinstance(manifest, Mapping) else getattr(manifest, "manifest_id", "")
                first_mid = exp_items[0].manifest_id if (exp_items and hasattr(exp_items[0], "manifest_id")) else ""
                req_to_verify = PreapprovalEvidenceRequest(
                    request_id=exp_req_id,
                    generation=exp_gen,
                    manifest_id=str(m_id or first_mid or "mf_unknown"),
                    input_signature=exp_sig,
                    items=tuple(exp_items),
                )

            # Thẩm định toàn diện bằng shared terminal normalizer
            norm_payload = normalize_preapproval_terminal_payload(
                payload,
                expected_request=req_to_verify,
            )

            # 6. ATOMIC MUTATION CHỈ THỰC HIỆN KHI TOÀN BỘ VALIDATION HOÀN TẤT
            if norm_payload.status == "completed":
                self._pending_preapproval_expected = None
                # Deepcopy qua _deep_dict_copy để cách ly alias hoàn toàn mà không lỗi pickle trên mappingproxy
                self._preapproval_evidence_map = _deep_dict_copy(norm_payload.verified_evidence_by_clip)
                self._preapproval_evidence_diagnostics = tuple(norm_payload.diagnostics)

                k = norm_payload.verified_count
                n = norm_payload.total_count
                if hasattr(self, "lbl_source_evidence_status"):
                    if n > 0 and k == n:
                        status_text = f"✓ Đã xác minh {k}/{n} nguồn — chưa kiểm tra đích dựng"
                        status_color = "#10b981"
                    else:
                        status_text = f"⚠ Đã xác minh {k}/{n} nguồn — chưa kiểm tra đích dựng"
                        status_color = "#f59e0b"
                    self.lbl_source_evidence_status.setText(status_text)
                    self.lbl_source_evidence_status.setStyleSheet(f"color: {status_color}; font-size: 10px; font-weight: bold;")

                # Cập nhật proposal và giao diện hiển thị
                self._rebuild_and_present_proposal()

            elif norm_payload.status == "cancelled":
                self._pending_preapproval_expected = None
                if hasattr(self, "lbl_source_evidence_status"):
                    self.lbl_source_evidence_status.setText("Đã hủy kiểm tra nguồn.")
                    self.lbl_source_evidence_status.setStyleSheet("color: #a1a1aa; font-size: 10px;")

            elif norm_payload.status == "failed":
                self._pending_preapproval_expected = None
                err_diag = "; ".join(str(d) for d in norm_payload.diagnostics) if norm_payload.diagnostics else "Lỗi quét bằng chứng nguồn."
                if hasattr(self, "lbl_source_evidence_status"):
                    self.lbl_source_evidence_status.setText(f"✕ Lỗi kiểm tra nguồn: {err_diag}")
                    self.lbl_source_evidence_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
            else:
                logger.warning("Trạng thái payload không xác định: %s", norm_payload.status)
                self._pending_preapproval_expected = None

        except Exception as exc:
            logger.warning("Bỏ qua terminal payload không hợp lệ hoặc lỗi xử lý: %s", exc)
            return

    def _on_preapproval_evidence_worker_finished(self, req_id: str, worker: Any) -> None:
        """Dọn dẹp vật lý khi PreapprovalEvidenceWorker hoàn tất trên QThread."""
        registered_w = self._preapproval_evidence_workers.get(req_id)
        if registered_w is not worker:
            logger.info("Bỏ qua finished callback từ worker không theo dõi hoặc đã cũ: %s", req_id)
            return

        self._preapproval_evidence_workers.pop(req_id, None)

        try:
            worker.deleteLater()
        except Exception:
            pass

        if hasattr(self, "btn_check_source_evidence"):
            self.btn_check_source_evidence.setText("🔍 Kiểm tra nguồn trước khi duyệt")

        self._update_source_evidence_button_state()
        self._update_overview_button_state()

        # Dọn dẹp idle service nếu có yêu cầu reset đang pending
        if getattr(self, "_preapproval_service_reset_pending", False):
            still_running = any(w.isRunning() for w in self._preapproval_evidence_workers.values())
            if not still_running:
                self._preapproval_service_reset_pending = False
                if getattr(self, "_preapproval_evidence_service", None) is not None:
                    svc = self._preapproval_evidence_service
                    if hasattr(svc, "clear_cache") and callable(svc.clear_cache):
                        try:
                            svc.clear_cache()
                        except Exception:
                            pass
                    self._preapproval_evidence_service = None

        if (
            not self._active_analysis_workers
            and not self._prompt_workers_registry
            and not self._kinetic_workers_registry
            and not self._matching_workers_registry
            and not self._preapproval_evidence_workers
        ):
            self._is_analysis_loading = False
            self.analysis_workers_idle.emit()

    def _on_check_source_evidence_clicked(self) -> None:
        """Xử lý sự kiện người dùng bấm 'Kiểm tra nguồn trước khi duyệt' (toggle Hủy khi busy)."""
        if getattr(self, "_review_close_pending", False):
            return
        if self.is_source_evidence_busy():
            # Người dùng bấm nút Hủy khi đang quét
            self._pending_preapproval_expected = None
            for w in list(self._preapproval_evidence_workers.values()):
                if hasattr(w, "cancel"):
                    try:
                        w.cancel()
                    except Exception:
                        pass
            if hasattr(self, "btn_check_source_evidence"):
                self.btn_check_source_evidence.setEnabled(False)
                self.btn_check_source_evidence.setText("⏳ Đang hủy kiểm tra nguồn...")
            if hasattr(self, "lbl_source_evidence_status"):
                self.lbl_source_evidence_status.setText("⏳ Đang hủy kiểm tra nguồn...")
                self.lbl_source_evidence_status.setStyleSheet("color: #a1a1aa; font-size: 10px;")
            return
        if (
            bool(getattr(self, "_active_analysis_workers", None))
            or bool(getattr(self, "_prompt_workers_registry", None))
            or bool(getattr(self, "_kinetic_workers_registry", None))
            or bool(getattr(self, "_matching_workers_registry", None))
        ):
            return

        try:
            curr_ctx = self._get_current_context() or {}
            manifest = getattr(self, "_manifest", None)
            video_plan = getattr(self, "_video_plan_data", None)

            if not manifest or not video_plan:
                if hasattr(self, "lbl_source_evidence_status"):
                    self.lbl_source_evidence_status.setText("⚠ Cần có danh sách clip nguồn và kịch bản video để kiểm tra.")
                    self.lbl_source_evidence_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                return

            m_id = manifest.get("manifest_id") if isinstance(manifest, Mapping) else getattr(manifest, "manifest_id", None)
            m_clips = manifest.get("clips") if isinstance(manifest, Mapping) else getattr(manifest, "clips", ())
            if not m_id or not m_clips:
                if hasattr(self, "lbl_source_evidence_status"):
                    self.lbl_source_evidence_status.setText("⚠ Manifest rỗng hoặc không hợp lệ.")
                    self.lbl_source_evidence_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                return

            pending_sig = self._build_pending_scan_input_signature(curr_ctx)

            from src.core.geometry.source_evidence_service import SourceEvidenceItemRequest
            items = []
            for c in m_clips:
                cid = c.get("clip_id") if isinstance(c, Mapping) else getattr(c, "clip_id", None)
                cpath = (c.get("current_path") or c.get("file_path")) if isinstance(c, Mapping) else (getattr(c, "current_path", None) or getattr(c, "file_path", None))
                if cid and cpath:
                    items.append(
                        SourceEvidenceItemRequest(
                            manifest_id=str(m_id),
                            clip_id=str(cid),
                            file_path=str(cpath),
                            expected_full_sha256=None,
                            stream_index=None,
                        )
                    )

            if not items:
                if hasattr(self, "lbl_source_evidence_status"):
                    self.lbl_source_evidence_status.setText("⚠ Không tìm thấy clip hợp lệ trong manifest.")
                    self.lbl_source_evidence_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                return

            from src.ui.director.workers.preapproval_evidence_worker import (
                PreapprovalEvidenceRequest,
                PreapprovalEvidenceWorker,
            )

            req_id = f"preapp_{uuid.uuid4().hex[:12]}"
            next_gen = self._preapproval_evidence_generation + 1

            req = PreapprovalEvidenceRequest(
                request_id=req_id,
                generation=next_gen,
                manifest_id=str(m_id),
                input_signature=pending_sig,
                items=tuple(items),
            )
            service = self._get_or_create_preapproval_service()
            worker = PreapprovalEvidenceWorker(
                request=req,
                service=service,
                parent=None,
            )

            # Candidate passed validation: thực hiện admission mutation
            self._preapproval_evidence_generation = next_gen
            self._pending_preapproval_expected = (worker, req_id, next_gen, pending_sig, tuple(items))
            self._preapproval_evidence_workers[req_id] = worker
            self._is_analysis_loading = True

            worker.progress.connect(self._on_preapproval_progress)
            worker.terminal_signal.connect(lambda p, w=worker: self._on_preapproval_terminal(p, worker=w))
            worker.finished.connect(lambda r=req_id, w=worker: self._on_preapproval_evidence_worker_finished(r, w))

            if hasattr(self, "btn_check_source_evidence"):
                self.btn_check_source_evidence.setEnabled(True)
                self.btn_check_source_evidence.setText("⏹ Hủy kiểm tra nguồn")
            if hasattr(self, "lbl_source_evidence_status"):
                self.lbl_source_evidence_status.setText("⏳ Đang quét bằng chứng nguồn (băm & kiểm tra frame)...")
                self.lbl_source_evidence_status.setStyleSheet("color: #38bdf8; font-size: 10px;")

            try:
                worker.start()
            except Exception as start_exc:
                logger.exception("Lỗi khởi động PreapprovalEvidenceWorker: %s", start_exc)
                is_running = None
                try:
                    is_running = worker.isRunning()
                except Exception as q_exc:
                    logger.warning("Không thể xác định trạng thái worker sau lỗi start: %s", q_exc)
                    is_running = None

                if is_running is False:
                    self._preapproval_evidence_workers.pop(req_id, None)
                    if self._pending_preapproval_expected and len(self._pending_preapproval_expected) >= 2 and self._pending_preapproval_expected[1] == req_id:
                        self._pending_preapproval_expected = None
                    self._update_source_evidence_button_state()
                    if hasattr(self, "lbl_source_evidence_status"):
                        self.lbl_source_evidence_status.setText(f"✕ Không thể khởi động kiểm tra: {start_exc}")
                        self.lbl_source_evidence_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                    try:
                        worker.deleteLater()
                    except Exception:
                        pass
                    if (
                        not self._active_analysis_workers
                        and not self._prompt_workers_registry
                        and not self._kinetic_workers_registry
                        and not self._matching_workers_registry
                        and not self._preapproval_evidence_workers
                    ):
                        self._is_analysis_loading = False
                        self.analysis_workers_idle.emit()
                else:
                    try:
                        worker.cancel()
                    except Exception:
                        pass
                    if hasattr(self, "lbl_source_evidence_status"):
                        self.lbl_source_evidence_status.setText(f"✕ Lỗi khởi động worker (không rõ trạng thái): {start_exc}")
                        self.lbl_source_evidence_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")

        except Exception as admission_exc:
            logger.warning("Lỗi trong quá trình tiếp nhận yêu cầu kiểm tra nguồn: %s", admission_exc)
            if hasattr(self, "lbl_source_evidence_status"):
                self.lbl_source_evidence_status.setText(f"✕ Lỗi tiếp nhận kiểm tra: {admission_exc}")
                self.lbl_source_evidence_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
            self._update_source_evidence_button_state()

    def _build_matching_context(
        self,
        raw_plan: Optional[Mapping[str, Any]],
        *,
        top_k: int = 5,
        allow_offline: bool = False
    ) -> Tuple[AssetDemandBatch, str, bytes]:
        """
        Bóc tách và sinh định danh phiên bản toàn vẹn (Full SHA-256) từ cả broll_inserts và sfx_inserts:
        - Lấy PlanDemandAdapter làm SSOT tuyệt đối: trích xuất placeholder batch trước để lấy typed demands.
        - Precheck số lượng mục source arrays (broll_inserts + sfx_inserts <= 4096).
        - Phân định rõ ràng: raw_plan_manifest_id, active_manifest_id và effective_manifest_id.
        - Mismatch giữa 2 valid manifest IDs lập tức ném ValueError kiểm soát trước dispatch.
        - Preflight kiểm tra độ dài các chuỗi consumed và manifest_ids trước khi tuần tự hóa (trần 512 KiB).
        - options (top_k, allow_offline) được đồng bộ tuyệt đối giữa signature và batch requests.
        - Trả về tuple: (batch, plan_revision, canonical_bytes).
        """
        if not isinstance(raw_plan, Mapping):
            raise ValueError("Kịch bản không phải là Mapping (dict) hợp lệ.")

        if type(top_k) is not int or top_k < 1 or top_k > 20:
            raise ValueError("top_k bắt buộc là số nguyên trong khoảng [1, 20] (không nhận bool).")

        if type(allow_offline) is not bool:
            raise ValueError("allow_offline bắt buộc là giá trị boolean (không nhận kiểu khác).")

        MAX_MATCH_CONTEXT_BYTES: int = 524288  # 512 KiB

        # 1. Precheck cấu trúc mảng nguồn và giới hạn tối đa 4096 mục
        raw_brolls = raw_plan.get("broll_inserts", ())
        raw_sfx = raw_plan.get("sfx_inserts", ())
        if "broll_inserts" in raw_plan and not isinstance(raw_brolls, (list, tuple)):
            raise ValueError("broll_inserts bắt buộc phải là list hoặc tuple.")
        if "sfx_inserts" in raw_plan and not isinstance(raw_sfx, (list, tuple)):
            raise ValueError("sfx_inserts bắt buộc phải là list hoặc tuple.")

        broll_count = len(raw_brolls) if isinstance(raw_brolls, (list, tuple)) else 0
        sfx_count = len(raw_sfx) if isinstance(raw_sfx, (list, tuple)) else 0
        total_source_entries = broll_count + sfx_count
        if total_source_entries > 4096:
            raise ValueError(f"Tổng số lượng broll và sfx ({total_source_entries}) vượt quá giới hạn 4096 mục.")

        # 2. Xử lý và thẩm định manifest IDs (raw plan vs active in-memory)
        raw_manifest_id = raw_plan.get("manifest_id")
        if raw_manifest_id is not None:
            if not isinstance(raw_manifest_id, str):
                raise ValueError(f"raw_plan manifest_id bắt buộc là chuỗi UTF-8, nhận: {type(raw_manifest_id).__name__}")
            try:
                raw_manifest_id.encode("utf-8")
            except (UnicodeEncodeError, AttributeError) as exc:
                raise ValueError(f"raw_plan manifest_id chứa ký tự UTF-8 không hợp lệ: {exc}")
            raw_valid_id = raw_manifest_id.strip() if raw_manifest_id.strip() else None
        else:
            raw_valid_id = None

        active_manifest_obj = getattr(self, "_manifest", None)
        active_manifest_id = getattr(active_manifest_obj, "manifest_id", None) if active_manifest_obj is not None else None
        if active_manifest_id is not None:
            if not isinstance(active_manifest_id, str):
                raise ValueError(f"active manifest_id bắt buộc là chuỗi UTF-8, nhận: {type(active_manifest_id).__name__}")
            try:
                active_manifest_id.encode("utf-8")
            except (UnicodeEncodeError, AttributeError) as exc:
                raise ValueError(f"active manifest_id chứa ký tự UTF-8 không hợp lệ: {exc}")
            active_valid_id = active_manifest_id.strip() if active_manifest_id.strip() else None
        else:
            active_valid_id = None

        # Kiểm tra mismatch: Nếu cả 2 đều có giá trị hợp lệ nhưng khác nhau -> ném ValueError có kiểm soát
        if raw_valid_id is not None and active_valid_id is not None and raw_valid_id != active_valid_id:
            raise ValueError(
                f"Lệch định danh manifest: kịch bản ({raw_manifest_id}) khác với manifest hiện hành ({active_manifest_id})."
            )

        # Xác định effective_manifest_id cho batch
        if raw_valid_id is not None:
            effective_manifest_id = raw_valid_id
        elif active_valid_id is not None:
            effective_manifest_id = active_valid_id
        else:
            effective_manifest_id = None

        # 3. Preflight kiểm tra độ dài các trường consumed và manifest_ids
        total_consumed_chars = 0
        if isinstance(raw_manifest_id, str):
            total_consumed_chars += len(raw_manifest_id)
        if isinstance(active_manifest_id, str):
            total_consumed_chars += len(active_manifest_id)

        for slot_entries in (raw_brolls if isinstance(raw_brolls, (list, tuple)) else (),
                             raw_sfx if isinstance(raw_sfx, (list, tuple)) else ()):
            for entry in slot_entries:
                if isinstance(entry, Mapping):
                    for field in ("asset_type", "description", "asset_file", "suggested_file"):
                        val = entry.get(field)
                        if isinstance(val, str):
                            total_consumed_chars += len(val)

        if total_consumed_chars > MAX_MATCH_CONTEXT_BYTES:
            raise ValueError(f"Tổng độ dài ký tự dữ liệu tiêu thụ ({total_consumed_chars}) vượt trần 512 KiB.")

        # 4. Extract placeholder batch để có canonical typed demands làm SSOT (không mutate raw_plan)
        plan_for_adapter = dict(raw_plan)
        if effective_manifest_id is not None:
            plan_for_adapter["manifest_id"] = effective_manifest_id
        elif "manifest_id" in plan_for_adapter:
            del plan_for_adapter["manifest_id"]

        placeholder_batch = PlanDemandAdapter.extract(
            plan_for_adapter,
            plan_revision="rev_placeholder",
            top_k=top_k,
            allow_offline=allow_offline,
        )

        # 5. Dựng material từ typed demands của adapter (bỏ derived demand_id)
        canonical_demands = []
        for d in placeholder_batch.demands:
            entry_dict = {
                "category": d.category,
                "desired_duration_sec": d.desired_duration_sec,
                "query_source": d.query_source,
                "query_text": d.query_text,
                "raw_query_hint": d.raw_query_hint,
                "raw_type": d.raw_type,
                "rejection_reason": d.rejection_reason,
                "source_index": d.source_index,
                "source_slot": d.source_slot,
                "status": d.status,
                "timeline_sec": d.timeline_sec,
            }
            canonical_demands.append(entry_dict)

        material = {
            "active_manifest_id": active_manifest_id,
            "demands": canonical_demands,
            "effective_manifest_id": placeholder_batch.manifest_id,
            "options": {
                "allow_offline": allow_offline,
                "top_k": top_k,
            },
            "raw_plan_manifest_id": raw_manifest_id,
        }

        try:
            canonical_bytes = json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False
            ).encode("utf-8")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Lỗi tuần tự hóa JSON ngữ cảnh tra cứu: {exc}") from exc

        if len(canonical_bytes) > MAX_MATCH_CONTEXT_BYTES:
            raise ValueError(f"Dung lượng ngữ cảnh ({len(canonical_bytes)} bytes) vượt trần MAX_MATCH_CONTEXT_BYTES (524288).")

        plan_revision = f"rev_{hashlib.sha256(canonical_bytes).hexdigest()}"

        # 6. Trích xuất final batch với plan_revision thực tế
        final_batch = PlanDemandAdapter.extract(
            plan_for_adapter,
            plan_revision=plan_revision,
            top_k=top_k,
            allow_offline=allow_offline,
        )

        return final_batch, plan_revision, canonical_bytes

    def _cancel_matching_workers(self) -> None:
        """Hủy cooperative toàn bộ worker matching đang chạy."""
        self._matching_generation += 1
        self._matching_pending_context = None
        for w in list(self._matching_workers_registry.values()):
            if hasattr(w, "cancel"):
                try:
                    w.cancel()
                except Exception:
                    pass
        self._update_matching_button_state()

    def scroll_to_advisory(self) -> None:
        """Đảm bảo bảng Cố vấn tài nguyên hiển thị trọn vẹn trong vùng cuộn."""
        if hasattr(self, "scroll_web") and hasattr(self, "asset_advisory_drawer"):
            self.scroll_web.ensureWidgetVisible(self.asset_advisory_drawer)
            h_bar = self.scroll_web.horizontalScrollBar()
            if h_bar is not None:
                h_bar.setValue(0)

    def _on_video_plan_changed(self) -> None:
        """Xử lý khi video plan thay đổi: hủy matching cũ, cập nhật trạng thái drawer và nút bấm."""
        self._cancel_matching_workers()
        self._update_matching_button_state()
        self._update_source_evidence_button_state()
        if hasattr(self, "common_review_widget") and self.common_review_widget:
            self.common_review_widget.set_stale(True)
        self._current_approval_record = None
        self._current_user_candidate_choices.clear()
        raw_plan = getattr(self, "_video_plan_data", None)
        if raw_plan and isinstance(raw_plan, Mapping):
            if self._last_good_matching_sidecar is not None:
                if hasattr(self, "asset_advisory_drawer"):
                    self.asset_advisory_drawer.set_stale(True)
                if hasattr(self, "lbl_matching_status"):
                    self.lbl_matching_status.setText("Kết quả cũ — bấm Gợi ý để cập nhật")
                    self.lbl_matching_status.setStyleSheet("color: #eab308; font-size: 10px; font-weight: bold;")
            else:
                if hasattr(self, "lbl_matching_status"):
                    self.lbl_matching_status.setText("Sẵn sàng gợi ý")
                    self.lbl_matching_status.setStyleSheet("color: #a1a1aa; font-size: 10px;")
        else:
            if hasattr(self, "lbl_matching_status"):
                self.lbl_matching_status.setText("")

    def _on_match_assets_clicked(self) -> None:
        """Kích hoạt tra cứu tài nguyên cố vấn ngoại tuyến trên luồng nền với start lifecycle an toàn."""
        import uuid

        if self.is_matching_busy():
            return

        raw_plan = getattr(self, "_video_plan_data", None)
        if not raw_plan or not isinstance(raw_plan, Mapping):
            if hasattr(self, "lbl_matching_status"):
                self.lbl_matching_status.setText("Chưa có kịch bản Video Plan hợp lệ.")
                self.lbl_matching_status.setStyleSheet("color: #71717a; font-size: 10px;")
            return

        try:
            batch, plan_revision, canonical_bytes = self._build_matching_context(raw_plan)
        except Exception as exc:
            if hasattr(self, "lbl_matching_status"):
                self.lbl_matching_status.setText(f"✕ Lỗi kịch bản: {exc}")
                self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
            return

        self._matching_generation += 1
        current_gen = self._matching_generation
        req_id = f"match_req_{current_gen}_{uuid.uuid4().hex[:8]}"

        self._matching_pending_context = {
            "req_id": req_id,
            "generation": current_gen,
            "plan_revision": plan_revision,
            "canonical_bytes": canonical_bytes,
            "batch": batch,
            "manifest_id": batch.manifest_id,
            "worker": None,
        }

        self.btn_match_assets.setEnabled(False)
        self.lbl_matching_status.setText("Đang tra cứu kho tài nguyên...")
        self.lbl_matching_status.setStyleSheet("color: #38bdf8; font-size: 10px; font-weight: bold;")

        worker = None
        try:
            worker = AssetMatchingWorker(
                batch=batch,
                generation=current_gen,
                storage_context_reader=read_matching_storage_context,
                parent=self
            )
            self._matching_workers_registry[req_id] = worker
            self._matching_pending_context["worker"] = worker

            worker.terminal.connect(lambda oc, w=worker, r=req_id: self._on_matching_terminal(w, r, oc))
            worker.finished.connect(lambda w=worker, r=req_id: self._on_matching_finished(r, w))

            worker.start()
        except Exception as exc:
            logger.exception("Lỗi khởi tạo hoặc khởi chạy AssetMatchingWorker cho %s: %s", req_id, exc)

            is_running = False
            if worker is not None:
                if hasattr(worker, "isRunning"):
                    try:
                        is_running = bool(worker.isRunning())
                    except Exception:
                        is_running = False
                elif getattr(worker, "running", False):
                    is_running = True

            if is_running:
                # Thread đang thực sự chạy: RETAIN trong registry, gọi cancel, giữ busy Disabled,
                # chờ tín hiệu native finished dọn dẹp vật lý
                if hasattr(worker, "cancel"):
                    try:
                        worker.cancel()
                    except Exception:
                        pass
                err_text = f"Lỗi khởi chạy tiến trình nền (tiến trình đang chạy đã được yêu cầu hủy): {exc}"
                if hasattr(self, "lbl_matching_status"):
                    self.lbl_matching_status.setText(f"✕ {err_text}")
                    self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                if hasattr(self, "asset_advisory_drawer"):
                    self.asset_advisory_drawer.set_error(err_text, retain_stale=True)
                self._update_matching_button_state()
            else:
                # Non-running failure: thread chưa chạy hoặc khởi tạo ctor thất bại
                # Rollback riêng owned request
                self._matching_workers_registry.pop(req_id, None)
                if self._matching_pending_context and self._matching_pending_context.get("req_id") == req_id:
                    self._matching_pending_context = None
                if worker is not None:
                    try:
                        worker.deleteLater()
                    except Exception:
                        pass
                err_text = f"Không thể khởi chạy tra cứu: {exc}"
                if hasattr(self, "lbl_matching_status"):
                    self.lbl_matching_status.setText(f"✕ {err_text}")
                    self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                if hasattr(self, "asset_advisory_drawer"):
                    self.asset_advisory_drawer.set_error(err_text, retain_stale=True)
                self._update_matching_button_state()

    def _on_matching_terminal(self, worker: Any, req_id: str, outcome: Any) -> None:
        """
        Tiếp nhận kết quả logic của worker; thẩm định chống stale trước khi publish.
        Bảo đảm 100% an toàn trên Main Thread, không leak ngoại lệ ra ngoài Qt slot.
        """
        try:
            # 1. Kiểm tra định danh worker và registry
            if self._matching_workers_registry.get(req_id) is not worker:
                logger.info("Discarding matching outcome: worker identity mismatch or already removed for req_id %s", req_id)
                return

            # 2. Kiểm tra pending context và worker binding
            pending = self._matching_pending_context
            if not pending or pending.get("req_id") != req_id or pending.get("worker") is not worker:
                logger.info("Discarding matching outcome: pending context mismatch for req_id %s", req_id)
                return

            # 3. Kiểm tra nominal type của outcome
            if not isinstance(outcome, AssetMatchingOutcome):
                logger.warning("Rejecting malformed matching outcome: not an AssetMatchingOutcome instance (received %s)", type(outcome).__name__)
                self._matching_pending_context = None
                self.lbl_matching_status.setText("✕ Lỗi kết quả: Dữ liệu outcome không hợp lệ.")
                self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                if hasattr(self, "asset_advisory_drawer"):
                    self.asset_advisory_drawer.set_error("Dữ liệu outcome không đúng định dạng AssetMatchingOutcome.", retain_stale=True)
                return

            # 4. Strict int generation check (chặn boolean True/False)
            if type(outcome.generation) is not int or type(pending.get("generation")) is not int:
                logger.warning("Rejecting outcome with non-int generation: %r", outcome.generation)
                self._matching_pending_context = None
                return
            if outcome.generation != self._matching_generation or outcome.generation != pending["generation"]:
                logger.info("Discarding matching outcome: generation mismatch (%s != %s)", outcome.generation, self._matching_generation)
                return

            # 5. Kiểm tra plan_revision
            if outcome.plan_revision != pending.get("plan_revision"):
                logger.warning("Rejecting outcome: plan_revision mismatch (%s != %s)", outcome.plan_revision, pending.get("plan_revision"))
                self._matching_pending_context = None
                return

            status = outcome.status

            if status == "completed":
                sidecar = outcome.sidecar
                storage_ctx = outcome.storage_context
                batch: AssetDemandBatch = pending.get("batch")

                # 1. Revalidate pending batch bằng validate_demand_batch
                try:
                    validate_demand_batch(batch)
                except Exception as exc:
                    logger.warning("Pending batch validation failed in terminal: %s", exc)
                    self._matching_pending_context = None
                    self.lbl_matching_status.setText(f"✕ Lỗi kịch bản: Dữ liệu batch không hợp lệ ({exc})")
                    self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                    if hasattr(self, "asset_advisory_drawer"):
                        self.asset_advisory_drawer.set_error("Dữ liệu kịch bản nội bộ không hợp lệ.", retain_stale=True)
                    return

                # 2. Sidecar bắt buộc phải là AssetMatchingSidecar hợp lệ (không nhận None)
                if not isinstance(sidecar, AssetMatchingSidecar):
                    logger.warning("Rejecting completed outcome with invalid sidecar: %s", type(sidecar).__name__)
                    self._matching_pending_context = None
                    self.lbl_matching_status.setText("✕ Lỗi kết quả: Thiếu báo cáo tư vấn sidecar.")
                    self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                    if hasattr(self, "asset_advisory_drawer"):
                        self.asset_advisory_drawer.set_error("Báo cáo sidecar trả về không hợp lệ.", retain_stale=True)
                    return

                # 3. Chặn foreign sidecar: revision và manifest phải khớp chính xác pending
                if sidecar.plan_revision != pending["plan_revision"]:
                    logger.warning("Rejecting foreign sidecar: revision %s != expected %s", sidecar.plan_revision, pending["plan_revision"])
                    self._matching_pending_context = None
                    self.lbl_matching_status.setText("✕ Lỗi kết quả: Sidecar thuộc phiên bản ngoại lai.")
                    self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                    if hasattr(self, "asset_advisory_drawer"):
                        self.asset_advisory_drawer.set_error("Báo cáo sidecar không thuộc kịch bản hiện hành.", retain_stale=True)
                    return

                if sidecar.manifest_id != pending.get("manifest_id"):
                    logger.warning("Rejecting sidecar manifest_id mismatch: %s != expected %s", sidecar.manifest_id, pending.get("manifest_id"))
                    self._matching_pending_context = None
                    self.lbl_matching_status.setText("✕ Lỗi kết quả: Lệch manifest ID trong sidecar.")
                    self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                    if hasattr(self, "asset_advisory_drawer"):
                        self.asset_advisory_drawer.set_error("Lệch manifest ID trong sidecar.", retain_stale=True)
                    return

                # 4. Thẩm định cấu trúc entries và thu thập actual eligible results theo thứ tự
                if not isinstance(sidecar.entries, tuple) or len(sidecar.entries) != len(batch.demands):
                    logger.warning("Rejecting sidecar: entries count (%s) != demands count (%d)", 
                                   len(sidecar.entries) if isinstance(sidecar.entries, tuple) else "invalid", len(batch.demands))
                    self._matching_pending_context = None
                    self.lbl_matching_status.setText("✕ Lỗi kết quả: Số lượng mục sidecar không khớp.")
                    self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                    if hasattr(self, "asset_advisory_drawer"):
                        self.asset_advisory_drawer.set_error("Số lượng mục sidecar không khớp với kịch bản.", retain_stale=True)
                    return

                eligible_results: List[OfflineAssetMatchResult] = []
                for i, entry in enumerate(sidecar.entries):
                    if not isinstance(entry, DemandMatchEntry):
                        logger.warning("Rejecting sidecar: entry %d is not DemandMatchEntry", i)
                        self._matching_pending_context = None
                        return
                    expected_demand = batch.demands[i]
                    if entry.demand != expected_demand:
                        logger.warning("Rejecting sidecar: entry %d demand mismatch", i)
                        self._matching_pending_context = None
                        return
                    if expected_demand.status == "eligible":
                        if not isinstance(entry.result, OfflineAssetMatchResult):
                            logger.warning("Rejecting sidecar: eligible entry %d has invalid result: %r", i, entry.result)
                            self._matching_pending_context = None
                            return
                        eligible_results.append(entry.result)
                    else:
                        if entry.result is not None:
                            logger.warning("Rejecting sidecar: noneligible entry %d has non-None result: %r", i, entry.result)
                            self._matching_pending_context = None
                            return

                # 5. Gọi PlanDemandAdapter.assemble(batch, eligible_results) để revalidate toàn diện graph/results/candidates
                try:
                    canonical_sidecar = PlanDemandAdapter.assemble(batch, eligible_results)
                except Exception as exc:
                    logger.warning("Rejecting sidecar: PlanDemandAdapter.assemble failed: %s", exc)
                    self._matching_pending_context = None
                    self.lbl_matching_status.setText("✕ Lỗi kết quả: Kết quả tra cứu vi phạm cấu trúc.")
                    self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                    if hasattr(self, "asset_advisory_drawer"):
                        self.asset_advisory_drawer.set_error(f"Kết quả tra cứu không hợp lệ: {exc}", retain_stale=True)
                    return

                # Đối soát canonical reconstructed sidecar với received sidecar
                if sidecar != canonical_sidecar:
                    logger.warning("Rejecting sidecar: received sidecar differs from canonical reconstructed sidecar")
                    self._matching_pending_context = None
                    return

                # 6. Thẩm định storage context
                if len(batch.requests) > 0:
                    if not isinstance(storage_ctx, MatchingStorageContext):
                        logger.warning("Rejecting completed nonempty outcome with non-MatchingStorageContext: %r", storage_ctx)
                        self._matching_pending_context = None
                        self.lbl_matching_status.setText("✕ Lỗi: Ngữ cảnh kho media không hợp lệ.")
                        self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                        if hasattr(self, "asset_advisory_drawer"):
                            self.asset_advisory_drawer.set_error("Thiếu hoặc sai định dạng ngữ cảnh kho media.", retain_stale=True)
                        return
                    root_val = getattr(storage_ctx, "root", None)
                    lib_val = getattr(storage_ctx, "library_id", None)
                    if not isinstance(root_val, str) or not root_val.strip() or not isinstance(lib_val, str) or not lib_val.strip():
                        logger.warning("Rejecting storage context: blank or non-str root/library_id (%r, %r)", root_val, lib_val)
                        self._matching_pending_context = None
                        self.lbl_matching_status.setText("✕ Lỗi: Cấu hình kho media bị rỗng.")
                        self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                        if hasattr(self, "asset_advisory_drawer"):
                            self.asset_advisory_drawer.set_error("Cấu hình kho media không hợp lệ.", retain_stale=True)
                        return
                    try:
                        root_val.encode("utf-8")
                        lib_val.encode("utf-8")
                    except (UnicodeEncodeError, AttributeError) as exc:
                        logger.warning("Rejecting storage context: non-UTF8 root/library_id: %s", exc)
                        self._matching_pending_context = None
                        return
                else:
                    if storage_ctx is not None and not isinstance(storage_ctx, MatchingStorageContext):
                        logger.warning("Rejecting empty request outcome with invalid storage_ctx object: %r", storage_ctx)
                        self._matching_pending_context = None
                        return

                # 7. Recompute current context trên GUI để đảm bảo kịch bản không bị sửa đổi trong lúc worker chạy
                raw_plan = getattr(self, "_video_plan_data", None)
                try:
                    _, curr_rev, curr_bytes = self._build_matching_context(raw_plan)
                    if curr_bytes != pending.get("canonical_bytes") or curr_rev != pending.get("plan_revision"):
                        logger.info("Discarding matching outcome: current plan context changed during execution")
                        self._matching_pending_context = None
                        return
                except Exception:
                    logger.info("Discarding matching outcome: current context recompute failed")
                    self._matching_pending_context = None
                    return

                # Tiêu thụ pending record duy nhất một lần
                self._matching_pending_context = None

                # Publish canonical sidecar
                self._last_good_matching_sidecar = canonical_sidecar
                self._last_good_matching_revision = outcome.plan_revision
                self.asset_advisory_drawer.set_sidecar(canonical_sidecar, storage_ctx)
                self.lbl_matching_status.setText(f"✓ Đã gợi ý ({canonical_sidecar.matched_count}/{canonical_sidecar.total_demands} mục)")
                self.lbl_matching_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
                self.scroll_to_advisory()
                if hasattr(self, "common_review_widget") and self.common_review_widget:
                    self.common_review_widget.set_stale(True)
                self._current_approval_record = None
                self._current_user_candidate_choices.clear()

            elif status == "error":
                # Consistency check: error outcome không được mang sidecar hay storage_context
                if outcome.sidecar is not None or outcome.storage_context is not None:
                    logger.warning("Rejecting error outcome carrying sidecar or storage_context")
                    self._matching_pending_context = None
                    return

                self._matching_pending_context = None
                err_msg = outcome.error_message or "Lỗi không xác định"
                self.asset_advisory_drawer.set_error(err_msg, retain_stale=True)
                self.lbl_matching_status.setText(f"✕ Lỗi tra cứu: {err_msg}")
                self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                self.scroll_to_advisory()

            elif status == "cancelled":
                # Consistency check: cancelled outcome không được mang sidecar hay storage_context
                if outcome.sidecar is not None or outcome.storage_context is not None:
                    logger.warning("Rejecting cancelled outcome carrying sidecar or storage_context")
                    self._matching_pending_context = None
                    return

                self._matching_pending_context = None
                self.lbl_matching_status.setText("Đã hủy tra cứu tài nguyên.")
                self.lbl_matching_status.setStyleSheet("color: #a1a1aa; font-size: 10px;")

            else:
                logger.warning("Unknown outcome status: %s", status)
                self._matching_pending_context = None

        except Exception as exc:
            logger.exception("Unexpected exception in _on_matching_terminal: %s", exc)
            self._matching_pending_context = None
            if hasattr(self, "lbl_matching_status"):
                self.lbl_matching_status.setText(f"✕ Lỗi xử lý kết quả: {exc}")
                self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
            if hasattr(self, "asset_advisory_drawer"):
                self.asset_advisory_drawer.set_error(f"Lỗi tiếp nhận kết quả: {exc}", retain_stale=True)

    def _on_matching_finished(self, req_id: str, worker: Any) -> None:
        """Dọn dẹp vật lý khi QThread kết thúc; chỉ xóa đúng worker và mở lại nút khi registry thực sự idle."""
        registered_w = self._matching_workers_registry.get(req_id)
        if registered_w is not worker:
            logger.info("Ignoring finished callback from untracked/stale worker %s for %s", worker, req_id)
            return

        self._matching_workers_registry.pop(req_id, None)

        try:
            worker.deleteLater()
        except Exception:
            pass

        self._update_matching_button_state()
        self._update_source_evidence_button_state()

        if (
            not self._active_analysis_workers
            and not self._prompt_workers_registry
            and not self._kinetic_workers_registry
            and not self._matching_workers_registry
            and not self._preapproval_evidence_workers
        ):
            self._is_analysis_loading = False
            self.analysis_workers_idle.emit()

    def _extract_words_from_rows(self) -> List[Dict[str, Any]]:
        """Bóc tách từ ngữ từ snapshot Kinetic đã được chuẩn bị in-memory (0 I/O)."""
        return self.get_final_cut_words()

    def get_final_cut_words(self) -> List[Dict[str, Any]]:
        """Lấy danh sách từ ngữ bản dựng cuối từ snapshot in-memory (0 I/O, không DEMO_WORDS)."""
        snap = getattr(self, "_prepared_kinetic_snapshot", None)
        if snap is None:
            return []

        if not callable(getattr(self, "_get_current_context", None)):
            return []

        try:
            curr_ctx = self._get_current_context()
            if not curr_ctx or not isinstance(curr_ctx, dict):
                return []  # Fail-closed khi context rỗng hoặc lỗi

            snap_sig = snap.get("context_signature")
            if snap_sig is not None:
                rc = curr_ctx.get("review_cuts")
                if rc is None and getattr(self, "_review_cuts_modified", False) and hasattr(self, "_review_rows"):
                    rc = tuple(bool(r[2]) for r in self._review_rows)

                curr_sig = self._extract_v2_context_signature(
                    curr_ctx,
                    video_plan=getattr(self, "_video_plan_data", None),
                    review_cuts=rc,
                    review_cuts_modified=getattr(self, "_review_cuts_modified", False),
                    aspect=snap.get("aspect", "9:16"),
                    font=snap.get("font", "Montserrat"),
                    intent=snap.get("intent", "copy_kinetic"),
                    fps=curr_ctx.get("fps") or curr_ctx.get("timeline_fps"),
                )
                if not curr_sig or snap_sig != curr_sig:
                    return []

            return copy.deepcopy(snap.get("words", []))
        except Exception as e:
            logger.error("Lỗi get_final_cut_words: %s", e)
            return []  # Fail-closed tuyệt đối khi có lỗi!

    def export_final_cut_transcript(self) -> str:
        """Xuất chuỗi transcript định dạng [i] từ snapshot in-memory (0 I/O)."""
        snap = getattr(self, "_prepared_kinetic_snapshot", None)
        if snap is not None and callable(getattr(self, "_get_current_context", None)):
            try:
                curr_ctx = self._get_current_context()
                if curr_ctx:
                    rc = curr_ctx.get("review_cuts")
                    if rc is None and getattr(self, "_review_cuts_modified", False) and hasattr(self, "_review_rows"):
                        rc = tuple(bool(r[2]) for r in self._review_rows)
                    curr_sig = self._extract_v2_context_signature(
                        curr_ctx,
                        video_plan=getattr(self, "_video_plan_data", None),
                        review_cuts=rc,
                        review_cuts_modified=getattr(self, "_review_cuts_modified", False),
                        aspect=snap.get("aspect", "9:16"),
                        font=snap.get("font", "Montserrat"),
                        intent=snap.get("intent", "copy_kinetic"),
                        fps=curr_ctx.get("fps") or curr_ctx.get("timeline_fps"),
                    )
                    if curr_sig and curr_sig == snap.get("context_signature"):
                        return snap.get("transcript_text", "")
            except Exception:
                pass
        words = self.get_final_cut_words()
        if not words:
            return ""
        return " ".join([f"[{w.get('index', i)}] {w['word']} ({w['start']:.2f}s - {w['end']:.2f}s)" for i, w in enumerate(words)])

    def _copy_final_cut_transcript(self):
        """Sao chép toàn bộ lời thoại Bản Dựng Cuối vào Clipboard (async route nếu chưa có)."""
        snap = getattr(self, "_prepared_kinetic_snapshot", None)
        if snap is not None and callable(getattr(self, "_get_current_context", None)):
            try:
                curr_ctx = self._get_current_context()
                if curr_ctx:
                    rc = curr_ctx.get("review_cuts")
                    if rc is None and getattr(self, "_review_cuts_modified", False) and hasattr(self, "_review_rows"):
                        rc = tuple(bool(r[2]) for r in self._review_rows)
                    curr_sig = self._extract_v2_context_signature(
                        curr_ctx,
                        video_plan=getattr(self, "_video_plan_data", None),
                        review_cuts=rc,
                        review_cuts_modified=getattr(self, "_review_cuts_modified", False),
                        aspect=snap.get("aspect", "9:16"),
                        font=snap.get("font", "Montserrat"),
                        intent=snap.get("intent", "copy_kinetic"),
                        fps=curr_ctx.get("fps") or curr_ctx.get("timeline_fps"),
                    )
                    if curr_sig and curr_sig == snap.get("context_signature"):
                        transcript_str = snap.get("transcript_text")
                        words = snap.get("words", [])
                        if transcript_str and words:
                            QApplication.clipboard().setText(transcript_str)
                            if hasattr(self, "btn_copy_final_transcript"):
                                self.btn_copy_final_transcript.setText(f"✓ Đã copy {len(words)} từ!")
                                QTimer.singleShot(2500, lambda: self.btn_copy_final_transcript.setText("📑 Copy Transcript Bản Dựng") if not getattr(self, "_review_close_pending", False) else None)
                            self.lbl_json_hint.setText(f"✓ Đã copy {len(words)} từ thoại của bản dựng vào Clipboard!")
                            self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
                            return
            except Exception:
                pass

        # Nếu chưa sẵn sàng -> kích hoạt async preparation
        self.request_kinetic_preparation(intent="copy_transcript")

    def _auto_generate_kinetic_plan(self):
        """Tự động sinh kịch bản Kinetic Typography (async route nếu chưa có snapshot)."""
        snap = getattr(self, "_prepared_kinetic_snapshot", None)
        if snap is not None and callable(getattr(self, "_get_current_context", None)):
            try:
                curr_ctx = self._get_current_context()
                if curr_ctx:
                    rc = curr_ctx.get("review_cuts")
                    if rc is None and getattr(self, "_review_cuts_modified", False) and hasattr(self, "_review_rows"):
                        rc = tuple(bool(r[2]) for r in self._review_rows)
                    curr_sig = self._extract_v2_context_signature(
                        curr_ctx,
                        video_plan=getattr(self, "_video_plan_data", None),
                        review_cuts=rc,
                        review_cuts_modified=getattr(self, "_review_cuts_modified", False),
                        aspect=snap.get("aspect", "9:16"),
                        font=snap.get("font", "Montserrat"),
                        intent=snap.get("intent", "copy_kinetic"),
                        fps=curr_ctx.get("fps") or curr_ctx.get("timeline_fps"),
                    )
                    if curr_sig and curr_sig == snap.get("context_signature"):
                        plan = snap.get("plan")
                        if plan:
                            pages_count = len(plan.get("pages", []))
                            is_preview = snap.get("is_source_preview") or not snap.get("is_final_cut", True)
                            preview_mode_text = "Xem trước nguồn, chưa có bản dựng" if is_preview else "Bản Dựng Cuối"
                            summary = f"Tự động tạo {pages_count} trang Kinetic Typography ({preview_mode_text}, 0đ Token)"
                            wrapped = {
                                "type": "kinetic_plan",
                                "count": pages_count,
                                "is_offline": True,
                                "data": plan
                            }
                            self._apply_json_data(wrapped, summary)
                            if is_preview:
                                self.lbl_json_hint.setText(f"✓ Đã tạo {pages_count} trang phụ đề Kinetic ({preview_mode_text} - 0đ Token)!")
                            else:
                                self.lbl_json_hint.setText(f"✓ Đã tự động tạo {pages_count} trang phụ đề Kinetic (Offline - 0đ Token)!")
                            self.lbl_json_hint.setStyleSheet("color: #a78bfa; font-size: 10.5px; font-weight: bold;")
                            return
            except Exception:
                pass

        # Chưa có snapshot hoặc ngữ cảnh thay đổi -> kích hoạt async preparation
        self.request_kinetic_preparation(intent="auto_plan")

    def set_context_supplier(self, supplier: Any):
        """Tiêm hàm cung cấp ngữ cảnh hiện tại từ DirectorWindow."""
        self._context_supplier = supplier

    @staticmethod
    def _validate_int(val: Any, default: Optional[int], min_val: int, max_val: int) -> Optional[int]:
        """Kiểm tra và chuẩn hóa giá trị int: từ chối bool, Inf, NaN, non-integer float, bigint ngoài bounds, hoặc invalid string."""
        if val is None:
            return default
        if isinstance(val, bool):
            return None
        if isinstance(val, (int, float)):
            try:
                if isinstance(val, float):
                    if math.isinf(val) or math.isnan(val):
                        return None
                    if not val.is_integer():
                        return None
                    val = int(val)
                if val < min_val or val > max_val:
                    return None
                return val
            except (OverflowError, ValueError, TypeError):
                return None
        if isinstance(val, str):
            try:
                val_str = val.strip()
                val_int = int(val_str)
                if val_int < min_val or val_int > max_val:
                    return None
                return val_int
            except (OverflowError, ValueError, TypeError):
                return None
        return None

    @staticmethod
    def _validate_float(val: Any, default: Optional[float], min_val: float, max_val: float) -> Optional[float]:
        """Kiểm tra và chuẩn hóa giá trị float: từ chối bool, Inf, NaN, hoặc invalid string/type."""
        if val is None:
            return default
        if isinstance(val, bool):
            return None
        if isinstance(val, (int, float)):
            try:
                f = float(val)
                if math.isinf(f) or math.isnan(f):
                    return None
                if f < min_val or f > max_val:
                    return None
                return f
            except (OverflowError, ValueError, TypeError):
                return None
        if isinstance(val, str):
            try:
                f = float(val.strip())
                if math.isinf(f) or math.isnan(f):
                    return None
                if f < min_val or f > max_val:
                    return None
                return f
            except (OverflowError, ValueError, TypeError):
                return None
        return None

    @staticmethod
    def _sanitize_and_validate_context(ctx: Any) -> Optional[Dict[str, Any]]:
        """Xác thực allowlist các trường nguyên thủy trong context; loại bỏ mọi QObject/Resolve proxy."""
        if not isinstance(ctx, dict):
            return None
        video_paths = ctx.get("video_paths")
        if not isinstance(video_paths, (list, tuple)) or not video_paths:
            return None
        clean_paths = []
        for p in video_paths:
            if not isinstance(p, str) or isinstance(p, bool) or not p.strip():
                return None
            clean_paths.append(p)  # Bảo toàn chuỗi đường dẫn thực tế, reject all-whitespace nhưng không trim

        raw_w_model = ctx.get("whisper_model")
        if raw_w_model is None:
            w_model = "small"
        elif isinstance(raw_w_model, str) and raw_w_model.strip():
            w_model = raw_w_model.strip().lower()
        else:
            return None

        raw_w_lang = ctx.get("whisper_language") or ctx.get("whisper_lang")
        if raw_w_lang is None:
            w_lang = "vi"
        elif isinstance(raw_w_lang, str) and raw_w_lang.strip():
            w_lang = raw_w_lang.strip().lower()
        else:
            return None

        raw_v_enable = ctx.get("enable_visual_describe")
        if raw_v_enable is None:
            v_enable = False
        elif isinstance(raw_v_enable, bool):
            v_enable = raw_v_enable
        else:
            return None

        raw_v_model = ctx.get("vision_model")
        if raw_v_model is None:
            v_model = ""
        elif isinstance(raw_v_model, str):
            v_model = raw_v_model.strip().lower()
        else:
            return None

        raw_v_ep = ctx.get("vision_endpoint")
        if raw_v_ep is None:
            v_endpoint = "http://127.0.0.1:11434"
        elif isinstance(raw_v_ep, str) and raw_v_ep.strip():
            v_endpoint = raw_v_ep.strip()
        else:
            return None

        window_sec = ReviewPage._validate_float(ctx.get("window_sec"), default=8.0, min_val=0.1, max_val=3600.0)
        if window_sec is None:
            return None

        frames_per_window = ReviewPage._validate_int(ctx.get("frames_per_window"), default=2, min_val=1, max_val=100)
        if frames_per_window is None:
            return None

        max_windows = ReviewPage._validate_int(ctx.get("max_windows"), default=10, min_val=1, max_val=1000)
        if max_windows is None:
            return None

        max_refinements = ReviewPage._validate_int(ctx.get("max_refinements"), default=1, min_val=0, max_val=100)
        if max_refinements is None:
            return None

        max_prompt_bytes = ReviewPage._validate_int(ctx.get("max_prompt_bytes"), default=32768, min_val=1, max_val=32768)
        if max_prompt_bytes is None:
            return None

        raw_style = ctx.get("style_intent")
        if raw_style is None:
            style_intent = "travel_vlog"
        elif isinstance(raw_style, str) and raw_style.strip():
            style_intent = raw_style.strip()
        else:
            return None

        raw_hook = ctx.get("hook_duration")
        if raw_hook is None:
            hook_duration = "30_60s"
        elif isinstance(raw_hook, str) and raw_hook.strip():
            hook_duration = raw_hook.strip()
        else:
            return None

        raw_target = ctx.get("target_duration")
        if raw_target is None:
            target_duration = "full"
        elif isinstance(raw_target, str) and raw_target.strip():
            target_duration = raw_target.strip()
        else:
            return None

        raw_proj = ctx.get("project_name")
        if raw_proj is None:
            project_name = "ChunDVC_Project"
        elif isinstance(raw_proj, str) and raw_proj.strip():
            project_name = raw_proj.strip()
        else:
            return None

        raw_manifest_id = ctx.get("manifest_id")
        if raw_manifest_id is None:
            manifest_id = ""
        elif isinstance(raw_manifest_id, str):
            manifest_id = raw_manifest_id.strip()
        else:
            return None

        # V2 optional FPS: phân biệt absent/None với False/0/NaN/Inf/bad string
        raw_fps = ctx.get("fps") if ctx.get("fps") is not None else ctx.get("timeline_fps")
        canonical_fps = None
        if raw_fps is not None:
            if isinstance(raw_fps, bool):
                return None
            try:
                from src.core.contracts.time_contracts import RationalFPS
                rat = RationalFPS.from_value(raw_fps)
                if rat.as_float() <= 0:
                    return None
                canonical_fps = rat.as_fraction()
            except Exception:
                return None

        # V2 optional review_cuts
        raw_cuts = ctx.get("review_cuts")
        canonical_cuts = None
        if raw_cuts is not None:
            if not isinstance(raw_cuts, (list, tuple)):
                return None
            for c in raw_cuts:
                if isinstance(c, bool) or c in (0, 1):
                    continue
                return None
            canonical_cuts = tuple(bool(c) for c in raw_cuts)

        # V2 optional review_cuts_modified
        raw_mod = ctx.get("review_cuts_modified")
        canonical_mod = False
        if raw_mod is not None:
            if not isinstance(raw_mod, bool):
                return None
            canonical_mod = raw_mod

        out = {
            "video_paths": clean_paths,
            "whisper_model": w_model,
            "whisper_language": w_lang,
            "enable_visual_describe": v_enable,
            "vision_model": v_model,
            "vision_endpoint": v_endpoint,
            "window_sec": window_sec,
            "frames_per_window": frames_per_window,
            "max_windows": max_windows,
            "max_refinements": max_refinements,
            "style_intent": style_intent,
            "hook_duration": hook_duration,
            "target_duration": target_duration,
            "project_name": project_name,
            "max_prompt_bytes": max_prompt_bytes,
            "manifest_id": manifest_id,
        }
        if canonical_fps is not None:
            out["fps"] = canonical_fps
            out["timeline_fps"] = canonical_fps
        if canonical_cuts is not None:
            out["review_cuts"] = canonical_cuts
        out["review_cuts_modified"] = canonical_mod

        # Tham số cấu hình phân chia lô kịch bản (batch planning)
        if "max_clips_per_batch" in ctx:
            max_clips_per_batch = ReviewPage._validate_int(ctx.get("max_clips_per_batch"), default=20, min_val=1, max_val=1000)
            if max_clips_per_batch is None:
                return None
            out["max_clips_per_batch"] = max_clips_per_batch

        if "target_batch_duration_sec" in ctx:
            target_batch_duration_sec = ReviewPage._validate_float(ctx.get("target_batch_duration_sec"), default=600.0, min_val=1.0, max_val=86400.0)
            if target_batch_duration_sec is None:
                return None
            out["target_batch_duration_sec"] = target_batch_duration_sec

        if "max_batch_duration_sec" in ctx:
            max_batch_duration_sec = ReviewPage._validate_float(ctx.get("max_batch_duration_sec"), default=900.0, min_val=1.0, max_val=86400.0)
            if max_batch_duration_sec is None:
                return None
            out["max_batch_duration_sec"] = max_batch_duration_sec

        if "window_partition_sec" in ctx:
            window_partition_sec = ReviewPage._validate_float(ctx.get("window_partition_sec"), default=600.0, min_val=1.0, max_val=86400.0)
            if window_partition_sec is None:
                return None
            out["window_partition_sec"] = window_partition_sec

        if "manifest_metadata" in ctx:
            raw_meta = ctx.get("manifest_metadata")
            if raw_meta is not None:
                if not isinstance(raw_meta, dict):
                    return None
                out["manifest_metadata"] = raw_meta

        # Creative Context: chấp nhận Mapping hoặc CreativeContext, reject malformed falsy/types
        raw_cc = ctx.get("creative_context")
        canonical_cc = None
        if raw_cc is not None:
            if isinstance(raw_cc, bool) or not isinstance(raw_cc, (Mapping, CreativeContext, dict)):
                return None
            try:
                canonical_cc = normalize_creative_context(raw_cc)
            except Exception:
                return None
        out["creative_context"] = canonical_cc

        return out

    def _get_current_context(self) -> Dict[str, Any]:
        """Lấy snapshot ngữ cảnh phân tích hiện tại (ưu tiên supplier từ DirectorWindow)."""
        supplier = getattr(self, "_context_supplier", None)
        if callable(supplier):
            try:
                ctx = supplier()
                if isinstance(ctx, dict):
                    clean = self._sanitize_and_validate_context(ctx)
                    if clean is not None:
                        return clean
                return {}  # Fail-closed: context unavailable khi supplier trả về dữ liệu lỗi
            except Exception as e:
                logger.error("Lỗi khi gọi context_supplier: %s", e)
                return {}  # Fail-closed: context unavailable, KHÔNG fallback sang stored context cũ!

        # Fallback nội bộ nếu chạy standalone ReviewPage
        raw_paths = []
        clip_infos = getattr(self, "_clip_infos", None)
        if clip_infos:
            for c in clip_infos:
                p = getattr(c, "path", c) if not isinstance(c, str) else c
                if p:
                    raw_paths.append(str(p))
        elif getattr(self, "_manifest", None):
            m = self._manifest
            clips = m.get("clips", ()) if isinstance(m, Mapping) else getattr(m, "clips", ())
            for c in clips:
                p = c.get("current_path") or c.get("path") if isinstance(c, Mapping) else (getattr(c, "current_path", None) or getattr(c, "path", None))
                if p:
                    raw_paths.append(str(p))

        # Fallback stored validated context nếu chạy standalone ReviewPage trong test
        if getattr(self, "_stored_validated_context", None) is not None:
            ctx = dict(self._stored_validated_context)
            if "video_paths" not in ctx:
                ctx["video_paths"] = raw_paths
            return ctx

        manifest = getattr(self, "_manifest", None)
        manifest_id = getattr(manifest, "manifest_id", "") if manifest else ""
        return {
            "video_paths": raw_paths,
            "whisper_model": getattr(self, "_whisper_model", "small"),
            "whisper_language": getattr(self, "_whisper_lang", "vi"),
            "enable_visual_describe": False,
            "vision_model": "",
            "vision_endpoint": "http://127.0.0.1:11434",
            "window_sec": 8.0,
            "frames_per_window": 2,
            "max_windows": 10,
            "max_refinements": 1,
            "style_intent": getattr(self, "_style_intent", "travel_vlog"),
            "hook_duration": getattr(self, "_hook_duration", "30_60s"),
            "target_duration": "full",
            "project_name": "ChunDVC_Project",
            "max_prompt_bytes": 32768,
            "manifest_id": manifest_id,
            "creative_context": getattr(self, "_creative_context", None),
        }

    @staticmethod
    def _extract_context_signature(ctx: Dict[str, Any]) -> Tuple:
        """Trích xuất chữ ký bất biến của ngữ cảnh để đối chiếu trước khi ghi nhận kết quả."""
        from src.ui.director.workers.prompt_preparation_worker import _normalize_endpoint
        if not isinstance(ctx, dict) or not ctx:
            return ()
        raw_paths = ctx.get("video_paths") or []
        norm_paths = []
        for p in raw_paths:
            if isinstance(p, str) and not isinstance(p, bool) and p.strip():
                try:
                    norm_paths.append(os.path.normpath(os.path.abspath(p)))
                except Exception:
                    pass
        norm_paths = tuple(norm_paths)

        w_model = str(ctx.get("whisper_model", "small")).strip().lower() if isinstance(ctx.get("whisper_model"), str) else ("small" if ctx.get("whisper_model") is None else "INVALID_W_MODEL")
        w_lang = str(ctx.get("whisper_language") or ctx.get("whisper_lang") or "vi").strip().lower() if (isinstance(ctx.get("whisper_language") or ctx.get("whisper_lang"), str) or (ctx.get("whisper_language") is None and ctx.get("whisper_lang") is None)) else "INVALID_W_LANG"
        v_enable = bool(ctx.get("enable_visual_describe", False)) if isinstance(ctx.get("enable_visual_describe", False), bool) else False
        v_model = str(ctx.get("vision_model", "")).strip().lower() if isinstance(ctx.get("vision_model"), str) else ("" if ctx.get("vision_model") is None else "INVALID_V_MODEL")
        v_endpoint = _normalize_endpoint(str(ctx.get("vision_endpoint", ""))) if isinstance(ctx.get("vision_endpoint"), str) else _normalize_endpoint("http://127.0.0.1:11434")

        # Xác thực số học không để lọt uncaught OverflowError và không fallback về defaults hợp lệ
        # Nếu trường nào sai/tràn số, đặt giá trị là None để không bao giờ khớp với snapshot hợp lệ
        window_sec = ReviewPage._validate_float(ctx.get("window_sec"), default=8.0, min_val=0.1, max_val=3600.0)
        frames_per_window = ReviewPage._validate_int(ctx.get("frames_per_window"), default=2, min_val=1, max_val=100)
        max_windows = ReviewPage._validate_int(ctx.get("max_windows"), default=10, min_val=1, max_val=1000)
        max_refinements = ReviewPage._validate_int(ctx.get("max_refinements"), default=1, min_val=0, max_val=100)
        budget = ReviewPage._validate_int(ctx.get("max_prompt_bytes"), default=32768, min_val=1, max_val=32768)

        style = str(ctx.get("style_intent", "travel_vlog")).strip() if isinstance(ctx.get("style_intent"), str) else ("travel_vlog" if ctx.get("style_intent") is None else "INVALID_STYLE")
        hook = str(ctx.get("hook_duration", "30_60s")).strip() if isinstance(ctx.get("hook_duration"), str) else ("30_60s" if ctx.get("hook_duration") is None else "INVALID_HOOK")
        target = str(ctx.get("target_duration", "full")).strip() if isinstance(ctx.get("target_duration"), str) else ("full" if ctx.get("target_duration") is None else "INVALID_TARGET")
        proj = str(ctx.get("project_name", "ChunDVC_Project")).strip() if isinstance(ctx.get("project_name"), str) else ("ChunDVC_Project" if ctx.get("project_name") is None else "INVALID_PROJ")
        manifest_id = str(ctx.get("manifest_id") or "").strip() if (isinstance(ctx.get("manifest_id"), str) or ctx.get("manifest_id") is None) else "INVALID_MID"

        base_sig = (
            norm_paths, w_model, w_lang, v_enable, v_model, v_endpoint,
            window_sec, frames_per_window, max_windows, max_refinements,
            style, hook, target, proj, budget, manifest_id
        )

        raw_cc = ctx.get("creative_context")
        if raw_cc is not None:
            try:
                if isinstance(raw_cc, CreativeContext):
                    norm_cc = raw_cc
                elif isinstance(raw_cc, (Mapping, dict)) and not isinstance(raw_cc, bool):
                    norm_cc = normalize_creative_context(raw_cc)
                else:
                    norm_cc = None
                if norm_cc is not None and norm_cc != CreativeContext():
                    cc_sig = compute_creative_context_signature(norm_cc)
                    return base_sig + (f"cc:{cc_sig}",)
            except Exception:
                return base_sig + ("cc:INVALID_CREATIVE_CONTEXT",)

        return base_sig

    @staticmethod
    def _canonicalize_plan_primitives(obj: Any) -> Any:
        """Chuẩn hóa dữ liệu plan nguyên thủy thành cấu trúc tuple đệ quy có thứ tự khóa xác định (deterministic).
        Bảo toàn thứ tự list, bao gồm toàn bộ các trường path, current_path, refs, geometry, và custom properties.
        Xử lý None rõ ràng, lossless float encoding bằng float.hex(), validate string dict keys trước sort.
        Ném TypeError/ValueError có kiểm soát khi gặp non-finite float, mixed/non-string keys, hoặc unsupported types."""
        if obj is None:
            return ("null", None)
        if isinstance(obj, bool):
            return ("bool", obj)
        if isinstance(obj, int):
            return ("int", obj)
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                raise ValueError(f"Non-finite float không được phép trong plan: {obj}")
            return ("float", obj.hex())
        if isinstance(obj, Fraction):
            return ("fraction", obj.numerator, obj.denominator)
        if isinstance(obj, str):
            return ("str", obj)
        if isinstance(obj, dict):
            for k in obj.keys():
                if not isinstance(k, str) or isinstance(k, bool):
                    raise TypeError(f"Plan dict key phải là chuỗi string, nhận được {type(k)}: {k!r}")
            clean_keys = [k for k in obj.keys() if not k.startswith("_")]
            sorted_keys = sorted(clean_keys)
            return ("dict", tuple((k, ReviewPage._canonicalize_plan_primitives(obj[k])) for k in sorted_keys))
        if isinstance(obj, (list, tuple)):
            return ("list", tuple(ReviewPage._canonicalize_plan_primitives(x) for x in obj))
        raise TypeError(f"Unsupported plan object type: {type(obj).__name__}: {obj!r}")

    @staticmethod
    def _extract_v2_context_signature(
        ctx: Dict[str, Any],
        video_plan: Optional[Dict[str, Any]] = None,
        review_cuts: Optional[Union[Tuple, List]] = None,
        review_cuts_modified: bool = False,
        aspect: str = "9:16",
        font: str = "Montserrat",
        intent: str = "copy_kinetic",
        fps: Optional[Any] = None,
    ) -> Tuple:
        """Trích xuất chữ ký V2 toàn diện bao gồm:
        - Source context V1 (16 trường)
        - Actual manifest ID
        - Toàn bộ VideoPlan primitive snapshot deterministically canonicalized
        - Review cuts tuple + review_cuts_modified flag
        - Canonical FPS (Rational tuple) / speed policy
        - aspect, font, intent
        """
        if not isinstance(ctx, dict) or not ctx:
            return ()

        v1_sig = ReviewPage._extract_context_signature(ctx)
        if not v1_sig:
            return ()

        # 1. Manifest ID
        mid = str(ctx.get("manifest_id") or "").strip()

        # 2. Toàn bộ VideoPlan ordered deterministic fingerprint
        plan = video_plan if video_plan is not None else ctx.get("video_plan_data")
        plan_tuple = None
        if plan is not None:
            if not isinstance(plan, dict):
                return ()
            try:
                plan_tuple = ReviewPage._canonicalize_plan_primitives(plan)
            except Exception:
                return ()

        # 3. Review cuts
        rc = review_cuts if review_cuts is not None else ctx.get("review_cuts")
        cuts_tuple = tuple(bool(c) for c in rc) if isinstance(rc, (list, tuple)) else ()
        rc_mod = bool(review_cuts_modified if review_cuts_modified else ctx.get("review_cuts_modified", False))

        # 4. FPS Canonicalization
        fps_val = fps if fps is not None else (ctx.get("fps") if ctx.get("fps") is not None else ctx.get("timeline_fps"))
        fps_tuple = None
        if fps_val is not None and not isinstance(fps_val, bool):
            try:
                from src.core.contracts.time_contracts import RationalFPS
                rat = RationalFPS.from_value(fps_val)
                if rat.as_float() > 0:
                    fps_tuple = (rat.numerator, rat.denominator)
            except Exception:
                fps_tuple = str(fps_val).strip()
        elif fps_val is not None:
            fps_tuple = str(fps_val)

        # 5. Aspect, font, intent
        asp = str(aspect).strip()
        fnt = str(font).strip()
        itn = str(intent).strip()

        return (v1_sig, mid, plan_tuple, cuts_tuple, rc_mod, fps_tuple, asp, fnt, itn)

    def _invalidate_prepared_batches(self, clear_user_draft: bool = False):
        """Vô hiệu hóa và dọn dẹp toàn bộ trạng thái snapshot phân lô; bảo tồn văn bản người dùng nếu không yêu cầu xóa rõ ràng."""
        self._prepared_batch_snapshot = None
        if hasattr(self, "cbo_batch_selector"):
            self.cbo_batch_selector.blockSignals(True)
            self.cbo_batch_selector.clear()
            self.cbo_batch_selector.blockSignals(False)
        if hasattr(self, "btn_copy_selected_batch"):
            self.btn_copy_selected_batch.setEnabled(False)
            self.btn_copy_selected_batch.setText("📋 Sao Chép Lô")
        if hasattr(self, "btn_submit_batch_draft"):
            self.btn_submit_batch_draft.setEnabled(False)
            self.btn_submit_batch_draft.setText("📥 Nạp Bản Thảo Cho Lô Đang Chọn")
        if hasattr(self, "lbl_batch_draft_status"):
            self.lbl_batch_draft_status.setText("")
        if clear_user_draft and hasattr(self, "txt_batch_draft_input"):
            self.txt_batch_draft_input.clear()
        if hasattr(self, "txt_batch_preview"):
            self.txt_batch_preview.clear()
            self.txt_batch_preview.setVisible(False)
        if hasattr(self, "lbl_batch_summary"):
            self.lbl_batch_summary.setText("0 lô | 0 clips đã phân lô | Trần: 32,768 B")
            self.lbl_batch_summary.setStyleSheet("color: #71717a; font-size: 10px; font-weight: 500;")
        if hasattr(self, "btn_copy_project_overview"):
            self.btn_copy_project_overview.setEnabled(False)
            self.btn_copy_project_overview.setText("📋 Sao Chép Tổng Quan Dự Án")
        if hasattr(self, "lbl_overview_status"):
            self.lbl_overview_status.setText("")

    def request_prompt_preparation(
        self,
        intent: str = "copy_video",
        context: Optional[Dict[str, Any]] = None,
        cache_mgr: Optional[Any] = None,
        visual_index: Optional[Any] = None,
        manifest_cache_dir: Optional[str] = None,
        selected_batch_index: Optional[int] = None,
        manifest_metadata: Optional[Dict[str, Any]] = None,
        batch_draft_text: str = "",
        associate_with_current_snapshot: bool = False,
        expected_existing_draft_digest: Optional[str] = None,
        allow_replace_draft: bool = False,
    ) -> Optional[str]:
        """Khởi động tiến trình chuẩn bị Prompt V1 hoặc Batch bất đồng bộ trên QThread nền.

        Args:
            intent: 'copy_video' | 'run_ai' | 'prepare_batches' | 'copy_batch' | 'validate_batch_draft'
            context: Dict cấu hình nguồn (nếu None sẽ lấy từ supplier)
            cache_mgr: Dependency injection cho tests
            visual_index: Dependency injection cho tests
            manifest_cache_dir: Thư mục lưu manifest (cho tests)
            selected_batch_index: Chỉ số lô cần sao chép khi intent='copy_batch' hoặc 'validate_batch_draft'
            manifest_metadata: Inject manifest metadata với known duration cho tests / happy path
            batch_draft_text: Nội dung JSON bản thảo phân lô khi intent='validate_batch_draft'
            associate_with_current_snapshot: Cờ người dùng chủ động liên kết bản thảo với snapshot hiện tại
            expected_existing_draft_digest: Digest bản thảo cũ cần thay thế để phát hiện race condition
            allow_replace_draft: Cờ opt-in cho phép ghi đè bản thảo đã có của lô

        Returns:
            request_id nếu khởi tạo thành công, None nếu bị từ chối
        """
        import uuid
        from src.ui.director.workers.prompt_preparation_worker import (
            PromptPreparationWorker, PromptPreparationRequest, validate_and_normalize_snapshot
        )

        valid_intents = ("copy_video", "run_ai", "prepare_batches", "copy_batch", "validate_batch_draft", "copy_project_overview")
        if intent not in valid_intents:
            logger.warning("Intent chuẩn bị prompt không hợp lệ: %s", intent)
            return None

        clean_ctx = None
        if context is not None:
            clean_ctx = self._sanitize_and_validate_context(context)
            if clean_ctx is None:
                logger.warning("Context được truyền vào không hợp lệ.")
                self.lbl_json_hint.setText("⚠ Không có tệp video nào để tạo kịch bản.")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                return None
            curr_ctx = clean_ctx
        else:
            curr_ctx = self._get_current_context()
            clean_ctx = self._sanitize_and_validate_context(curr_ctx)
            if clean_ctx is None:
                self.lbl_json_hint.setText("⚠ Không có tệp video nào để tạo kịch bản.")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                return None
            curr_ctx = clean_ctx

        curr_sig = self._extract_context_signature(curr_ctx)
        curr_cc = curr_ctx.get("creative_context")
        if curr_cc is not None and curr_cc != CreativeContext():
            expected_cc_sig = compute_creative_context_signature(curr_cc)
        else:
            expected_cc_sig = None

        if intent in ("copy_batch", "validate_batch_draft"):
            # 1. Thẩm định nghiêm ngặt kiểu int: loại bỏ bool, str, None, float trước mọi state mutation
            if type(selected_batch_index) is not int or selected_batch_index < 0:
                logger.warning("Chỉ số lô không hợp lệ: %r", selected_batch_index)
                self.lbl_json_hint.setText(f"⚠ Chỉ số lô không hợp lệ: {selected_batch_index!r}")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                return None

            if not self._prepared_batch_snapshot:
                logger.warning("Không có snapshot phân lô hợp lệ để thao tác.")
                self.lbl_json_hint.setText("⚠ Vui lòng bấm 'Phân Lô Kịch Bản' trước khi thực hiện.")
                self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px;")
                return None

            # 2. Thẩm định qua pure validation SSOT
            norm_snap = validate_and_normalize_snapshot(self._prepared_batch_snapshot)
            if norm_snap is None:
                logger.warning("Snapshot phân lô không hợp lệ.")
                self._invalidate_prepared_batches()
                self.lbl_json_hint.setText("⚠ Snapshot phân lô không hợp lệ. Vui lòng phân lô lại.")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                return None

            batches = norm_snap.batches
            if selected_batch_index >= len(batches):
                logger.warning("Chỉ số lô vượt quá giới hạn: %s (tổng: %s)", selected_batch_index, len(batches))
                self.lbl_json_hint.setText(f"⚠ Chỉ số lô không hợp lệ: {selected_batch_index}")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                return None

            # 3. Thẩm định context signature so với UI context signature và creative signature của snapshot
            curr_sig = self._extract_context_signature(curr_ctx)
            snap_ui_sig = getattr(norm_snap, "ui_context_signature", None)
            if snap_ui_sig is None and isinstance(self._prepared_batch_snapshot, dict):
                snap_ui_sig = self._prepared_batch_snapshot.get("ui_context_signature")
            if snap_ui_sig is None and isinstance(self._prepared_batch_snapshot, dict):
                snap_ui_sig = self._prepared_batch_snapshot.get("context_signature")

            if curr_sig != snap_ui_sig:
                logger.warning("Ngữ cảnh đã thay đổi so với snapshot phân lô. Từ chối thao tác.")
                self.lbl_json_hint.setText("⚠ Yêu cầu thay đổi, chuẩn bị lại.")
                self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px;")
                self._invalidate_prepared_batches()
                return None

            snap_cc_sig = getattr(norm_snap, "creative_context_signature", None)
            if expected_cc_sig != snap_cc_sig:
                logger.warning("Ngữ cảnh sáng tạo không khớp với snapshot phân lô (%r != %r). Từ chối thao tác.", expected_cc_sig, snap_cc_sig)
                self.lbl_json_hint.setText("⚠ Yêu cầu thay đổi, chuẩn bị lại.")
                self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px;")
                self._invalidate_prepared_batches()
                return None

            # 4. Kiểm tra các tham số chuyên biệt của validate_batch_draft TRƯỚC mọi worker cancel / generation delta
            if intent == "validate_batch_draft":
                if isinstance(batch_draft_text, bool) or not isinstance(batch_draft_text, str) or not batch_draft_text.strip():
                    logger.warning("batch_draft_text không hợp lệ hoặc rỗng: %r", batch_draft_text)
                    if hasattr(self, "lbl_batch_draft_status"):
                        self.lbl_batch_draft_status.setText("⚠ Mã JSON bản thảo không hợp lệ hoặc rỗng.")
                        self.lbl_batch_draft_status.setStyleSheet("color: #ef4444; font-size: 10px;")
                    return None
                try:
                    raw_bytes = batch_draft_text.encode("utf-8")
                except Exception:
                    return None
                if len(raw_bytes) > 65536:
                    logger.warning("batch_draft_text vượt quá 65,536 bytes: %s", len(raw_bytes))
                    if hasattr(self, "lbl_batch_draft_status"):
                        self.lbl_batch_draft_status.setText(f"⚠ Dung lượng bản thảo ({len(raw_bytes)} bytes) vượt quá 65,536 bytes (64KiB).")
                        self.lbl_batch_draft_status.setStyleSheet("color: #ef4444; font-size: 10px;")
                    return None

                if not isinstance(associate_with_current_snapshot, bool):
                    logger.warning("associate_with_current_snapshot phải là strict bool: %r", associate_with_current_snapshot)
                    return None

                if expected_existing_draft_digest is not None and (
                    isinstance(expected_existing_draft_digest, bool) or not isinstance(expected_existing_draft_digest, str) or not expected_existing_draft_digest.strip()
                ):
                    logger.warning("expected_existing_draft_digest không hợp lệ: %r", expected_existing_draft_digest)
                    return None

                selected_b = batches[selected_batch_index]
                if not getattr(selected_b, "source_ranges", None):
                    logger.warning("Lô %s thiếu source_ranges geometry.", selected_batch_index)
                    if hasattr(self, "lbl_batch_draft_status"):
                        self.lbl_batch_draft_status.setText("⚠ Lô kịch bản này thiếu thông tin hình học phân mảnh. Vui lòng phân lô lại.")
                        self.lbl_batch_draft_status.setStyleSheet("color: #ef4444; font-size: 10px;")
                    return None

        overview_ctx_input = None
        exp_overview_ledger_sig = None
        if intent == "copy_project_overview":
            if not self._prepared_batch_snapshot:
                logger.warning("Không có snapshot phân lô hợp lệ để tạo tổng quan.")
                if hasattr(self, "lbl_overview_status"):
                    self.lbl_overview_status.setText("⚠ Vui lòng phân lô kịch bản và nạp bản thảo trước.")
                    self.lbl_overview_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                return None

            norm_snap = validate_and_normalize_snapshot(self._prepared_batch_snapshot)
            if norm_snap is None:
                logger.warning("Snapshot phân lô không hợp lệ.")
                self._invalidate_prepared_batches()
                return None

            # Đối soát UI context signature (bắt buộc khớp root proof, không fallback)
            curr_sig = self._extract_context_signature(curr_ctx)
            if not curr_sig:
                logger.warning("Context signature thiếu root proof.")
                if hasattr(self, "lbl_overview_status"):
                    self.lbl_overview_status.setText("⚠ Ngữ cảnh dự án không có chữ ký hợp lệ.")
                    self.lbl_overview_status.setStyleSheet("color: #ef4444; font-size: 10px;")
                return None

            snap_ui_sig = getattr(norm_snap, "ui_context_signature", None)
            if snap_ui_sig is None and isinstance(self._prepared_batch_snapshot, dict):
                snap_ui_sig = self._prepared_batch_snapshot.get("ui_context_signature")
            if not snap_ui_sig or curr_sig != snap_ui_sig:
                logger.warning("Ngữ cảnh UI đã thay đổi so với snapshot phân lô. Từ chối tạo tổng quan.")
                if hasattr(self, "lbl_overview_status"):
                    self.lbl_overview_status.setText("⚠ Yêu cầu thay đổi, chuẩn bị lại.")
                    self.lbl_overview_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                return None

            snap_cc_sig = getattr(norm_snap, "creative_context_signature", None)
            if expected_cc_sig != snap_cc_sig:
                logger.warning("Ngữ cảnh sáng tạo không khớp với snapshot phân lô (%r != %r). Từ chối tạo tổng quan.", expected_cc_sig, snap_cc_sig)
                if hasattr(self, "lbl_overview_status"):
                    self.lbl_overview_status.setText("⚠ Yêu cầu thay đổi, chuẩn bị lại.")
                    self.lbl_overview_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                self._invalidate_prepared_batches()
                return None

            # Thu thập và thẩm định có kiểm soát các bản thảo trong ledger
            curr_drafts, exp_overview_ledger_sig, draft_err = self._collect_valid_overview_drafts(norm_snap.snapshot_id)
            if curr_drafts is None or exp_overview_ledger_sig is None:
                logger.warning("Ledger bản thảo không hợp lệ cho overview: %s", draft_err)
                if hasattr(self, "lbl_overview_status"):
                    self.lbl_overview_status.setText("⚠ Bản thảo trong bộ nhớ không hợp lệ.")
                    self.lbl_overview_status.setStyleSheet("color: #ef4444; font-size: 10px;")
                return None

            if len(curr_drafts) == 0:
                logger.warning("Chưa có bản thảo nào cho snapshot hiện tại: %s", norm_snap.snapshot_id)
                if hasattr(self, "lbl_overview_status"):
                    self.lbl_overview_status.setText("⚠ Cần ít nhất 1 bản thảo lô đã nạp để sao chép tổng quan.")
                    self.lbl_overview_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                return None

            style_intent = curr_ctx.get("style_intent", self._style_intent)
            max_p_bytes = curr_ctx.get("max_prompt_bytes", 32768)
            if norm_snap.max_prompt_bytes != max_p_bytes:
                logger.warning("Budget mismatch 3 chiều: snapshot=%s, ctx=%s", norm_snap.max_prompt_bytes, max_p_bytes)
                if hasattr(self, "lbl_overview_status"):
                    self.lbl_overview_status.setText("⚠ Cấu hình dung lượng đã thay đổi. Vui lòng phân lô lại.")
                    self.lbl_overview_status.setStyleSheet("color: #ef4444; font-size: 10px;")
                return None

            from src.core.project_overview_contracts import (
                BatchPartitionRef,
                OverviewUnaccountedClip,
                ProjectOverviewContextInput,
                FORMATTER_POLICY,
            )

            try:
                batches_refs = tuple(
                    BatchPartitionRef(
                        batch_id=b.batch_id,
                        batch_index=b.batch_index,
                        source_ranges=b.source_ranges,
                    )
                    for b in norm_snap.batches
                )
                unaccounted = tuple(
                    OverviewUnaccountedClip(
                        clip_id=u.clip_id,
                        reason=u.reason,
                        message=u.message,
                    )
                    for u in norm_snap.unaccounted_clips
                )
                overview_ctx_input = ProjectOverviewContextInput(
                    manifest_id=norm_snap.manifest_id,
                    project_name=norm_snap.project_name,
                    snapshot_id=norm_snap.snapshot_id,
                    batches=batches_refs,
                    received_drafts=tuple(curr_drafts),
                    unaccounted_clips=unaccounted,
                    style_intent=style_intent,
                    max_prompt_bytes=max_p_bytes,
                    creative_context=curr_cc,
                )
            except Exception as e:
                logger.warning("Lỗi khởi tạo ProjectOverviewContextInput: %s", e)
                if hasattr(self, "lbl_overview_status"):
                    self.lbl_overview_status.setText("⚠ Bản thảo trong bộ nhớ không hợp lệ.")
                    self.lbl_overview_status.setStyleSheet("color: #ef4444; font-size: 10px;")
                return None

        # FAIL-CLOSED: Bỏ fallback lấy paths từ baseline khi video_paths rỗng
        video_paths = curr_ctx.get("video_paths") or []
        if not video_paths:
            self.lbl_json_hint.setText("⚠ Không có tệp video nào để tạo kịch bản.")
            self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            return None

        # VALIDATION HOÀN TẤT: Lưu stored validated context
        self._stored_validated_context = dict(curr_ctx)

        # Hủy các worker chuẩn bị prompt đang chạy trước đó
        for w in list(self._prompt_workers_registry.values()):
            if hasattr(w, "cancel"):
                try:
                    w.cancel()
                except Exception:
                    pass

        self._prompt_generation += 1
        gen = self._prompt_generation
        req_id = f"prep_{uuid.uuid4().hex[:8]}_{gen}"
        self._active_prompt_request_id = req_id
        self._active_prompt_intent = intent
        self._active_prompt_signature = self._extract_context_signature(curr_ctx)

        # G06-06A3-R2 / R3 / 06A4b-I / R2: Lưu expected pending record độc lập để đối chiếu terminal authority
        if intent in ("copy_batch", "validate_batch_draft"):
            selected_b = norm_snap.batches[selected_batch_index]
            submitted_raw_sha256 = hashlib.sha256(batch_draft_text.encode("utf-8")).hexdigest() if intent == "validate_batch_draft" else ""
            if intent == "validate_batch_draft":
                allowed_modes = ("echo_matched", "user_associated_current_snapshot") if associate_with_current_snapshot else ("echo_matched",)
                if expected_existing_draft_digest is None:
                    existing_entry = self._batch_drafts_ledger.get((norm_snap.snapshot_id, selected_b.batch_id))
                    expected_existing_draft_digest = existing_entry.draft_digest if existing_entry is not None else None
            else:
                allowed_modes = ()
            self._pending_prompt_expected = {
                "request_id": req_id,
                "generation": gen,
                "intent": intent,
                "context_signature": curr_sig,
                "snapshot_id": norm_snap.snapshot_id,
                "manifest_id": norm_snap.manifest_id,
                "project_name": norm_snap.project_name,
                "formatter_policy": getattr(norm_snap, "formatter_policy", "source_only_v1"),
                "batch_id": selected_b.batch_id,
                "batch_index": selected_batch_index,
                "rendered_sha256": selected_b.rendered_sha256,
                "rendered_bytes": selected_b.rendered_bytes,
                "expected_existing_draft_digest": expected_existing_draft_digest,
                "allow_replace_draft": bool(allow_replace_draft),
                "expected_raw_payload_sha256": submitted_raw_sha256,
                "allowed_binding_modes": allowed_modes,
                "expected_binding_mode": "user_associated_current_snapshot" if associate_with_current_snapshot else "echo_matched",
                "expected_creative_context_signature": expected_cc_sig,
            }
        elif intent == "copy_project_overview":
            self._pending_prompt_expected = {
                "request_id": req_id,
                "generation": gen,
                "intent": intent,
                "context_signature": curr_sig,
                "ui_context_signature": curr_sig,
                "snapshot_id": norm_snap.snapshot_id,
                "manifest_id": norm_snap.manifest_id,
                "project_name": norm_snap.project_name,
                "expected_ledger_signature": exp_overview_ledger_sig,
                "formatter_policy": FORMATTER_POLICY,
                "expected_formatter_policy": FORMATTER_POLICY,
                "style_intent": style_intent,
                "max_prompt_bytes": max_p_bytes,
                "worker_ref": None,
                "expected_creative_context_signature": expected_cc_sig,
            }
        else:
            self._pending_prompt_expected = {
                "request_id": req_id,
                "generation": gen,
                "intent": intent,
                "context_signature": curr_sig,
                "project_name": curr_ctx.get("project_name", "ChunDVC_Project"),
                "expected_creative_context_signature": expected_cc_sig,
            }

        v_ctx = None
        if curr_ctx.get("enable_visual_describe"):
            v_ctx = {
                "vision_model": curr_ctx.get("vision_model", ""),
                "vision_endpoint": curr_ctx.get("vision_endpoint", "http://127.0.0.1:11434"),
                "window_sec": curr_ctx.get("window_sec", 8.0),
                "frames_per_window": curr_ctx.get("frames_per_window", 2),
                "max_windows": curr_ctx.get("max_windows", 10),
                "max_refinements": curr_ctx.get("max_refinements", 1),
            }

        # Tái sử dụng metadata của manifest hiện có nếu khớp exact paths hoặc được inject
        meta_data = None
        if manifest_metadata is not None:
            meta_data = manifest_metadata
        elif curr_ctx.get("manifest_metadata"):
            meta_data = curr_ctx.get("manifest_metadata")
        elif getattr(self, "_manifest", None) is not None and getattr(self._manifest, "clips", None):
            manifest_norm = [os.path.normpath(os.path.abspath(c.current_path)) for c in self._manifest.clips]
            req_norm = [os.path.normpath(os.path.abspath(p)) for p in video_paths]
            if manifest_norm == req_norm and self._manifest.project_name == curr_ctx.get("project_name", "ChunDVC_Project"):
                meta_data = {
                    "project_name": self._manifest.project_name,
                    "manifest_id": self._manifest.manifest_id,
                    "created_at": getattr(self._manifest, "created_at", ""),
                    "updated_at": getattr(self._manifest, "updated_at", ""),
                    "clips": [c.to_dict() for c in self._manifest.clips],
                }

        eff_cache_mgr = cache_mgr if cache_mgr is not None else getattr(self, "_cache_mgr", None)
        eff_vis_index = visual_index if visual_index is not None else getattr(self, "_visual_index", None)
        eff_manifest_dir = manifest_cache_dir if manifest_cache_dir is not None else getattr(self, "_manifest_cache_dir", None)

        req = PromptPreparationRequest(
            request_id=req_id,
            generation=gen,
            intent=intent,
            video_paths=tuple(video_paths),
            whisper_model=curr_ctx.get("whisper_model", "small"),
            whisper_language=curr_ctx.get("whisper_language") or curr_ctx.get("whisper_lang") or "vi",
            vision_context=v_ctx,
            enable_visual_describe=bool(curr_ctx.get("enable_visual_describe", False)),
            style_intent=curr_ctx.get("style_intent", self._style_intent),
            target_duration=curr_ctx.get("target_duration", "full"),
            hook_duration=curr_ctx.get("hook_duration", self._hook_duration),
            project_name=curr_ctx.get("project_name", "ChunDVC_Project"),
            max_prompt_bytes=curr_ctx.get("max_prompt_bytes", 32768),
            manifest_cache_dir=eff_manifest_dir,
            manifest_metadata=meta_data,
            prepared_batch_snapshot=norm_snap if intent in ("copy_batch", "validate_batch_draft", "copy_project_overview") else None,
            project_overview_context=overview_ctx_input if intent == "copy_project_overview" else None,
            selected_batch_index=selected_batch_index if intent in ("copy_batch", "validate_batch_draft") else None,
            target_batch_duration_sec=float(curr_ctx.get("target_batch_duration_sec", 600.0)),
            max_batch_duration_sec=float(curr_ctx.get("max_batch_duration_sec", 900.0)),
            max_clips_per_batch=int(curr_ctx.get("max_clips_per_batch", 20)),
            window_partition_sec=min(float(curr_ctx.get("window_partition_sec", 600.0)), float(curr_ctx.get("max_batch_duration_sec", 900.0))),
            expected_snapshot_id=norm_snap.snapshot_id if intent in ("copy_batch", "validate_batch_draft", "copy_project_overview") else None,
            expected_batch_id=norm_snap.batches[selected_batch_index].batch_id if intent in ("copy_batch", "validate_batch_draft") else None,
            expected_rendered_sha256=norm_snap.batches[selected_batch_index].rendered_sha256 if intent in ("copy_batch", "validate_batch_draft") else None,
            expected_formatter_policy=FORMATTER_POLICY if intent == "copy_project_overview" else (norm_snap.formatter_policy if intent in ("copy_batch", "validate_batch_draft") else "source_only_v1"),
            expected_manifest_id=norm_snap.manifest_id if intent in ("copy_batch", "validate_batch_draft", "copy_project_overview") else None,
            expected_project_name=norm_snap.project_name if intent in ("copy_batch", "validate_batch_draft", "copy_project_overview") else None,
            expected_ledger_signature=exp_overview_ledger_sig if intent == "copy_project_overview" else None,
            expected_ui_context_signature=curr_sig if intent in ("copy_batch", "validate_batch_draft", "copy_project_overview") else None,
            batch_draft_text=batch_draft_text,
            associate_with_current_snapshot=associate_with_current_snapshot,
            selected_batch_id=norm_snap.batches[selected_batch_index].batch_id if intent == "validate_batch_draft" else None,
            creative_context=curr_cc,
        )

        worker = PromptPreparationWorker(
            request=req,
            cache_mgr=eff_cache_mgr,
            visual_index=eff_vis_index,
            parent=self
        )
        self._prompt_workers_registry[req_id] = worker
        self._prompt_worker = worker

        if intent == "copy_project_overview" and isinstance(self._pending_prompt_expected, dict):
            self._pending_prompt_expected["worker_ref"] = worker

        worker.finished_signal.connect(self._on_prompt_preparation_finished)
        worker.finished.connect(lambda w=worker, r=req_id: self._on_prompt_worker_thread_finished(r, w))

        # Cập nhật UI trạng thái pending & khóa nút bấm (Busy control)
        if intent == "copy_video" and hasattr(self, "btn_copy_video_prompt"):
            self.btn_copy_video_prompt.setEnabled(False)
            self.btn_copy_video_prompt.setText("⏳ Đang chuẩn bị Prompt V1...")
            self.lbl_json_hint.setText("Đang chuẩn bị Prompt V1 ngoại tuyến (xác thực dữ liệu nguồn)...")
            self.lbl_json_hint.setStyleSheet("color: #38bdf8; font-size: 10.5px;")
        elif intent == "run_ai":
            if hasattr(self, "btn_copy_video_prompt"):
                self.btn_copy_video_prompt.setEnabled(False)
            self.lbl_json_hint.setText("Đang chuẩn bị kịch bản AI ngoại tuyến...")
            self.lbl_json_hint.setStyleSheet("color: #38bdf8; font-size: 10.5px;")
        elif intent == "prepare_batches":
            if hasattr(self, "btn_prepare_batches"):
                self.btn_prepare_batches.setEnabled(False)
                self.btn_prepare_batches.setText("⏳ Đang phân lô...")
            if hasattr(self, "btn_copy_selected_batch"):
                self.btn_copy_selected_batch.setEnabled(False)
            if hasattr(self, "btn_submit_batch_draft"):
                self.btn_submit_batch_draft.setEnabled(False)
            self.lbl_json_hint.setText("Đang phân lô kịch bản kẹp khung an toàn...")
            self.lbl_json_hint.setStyleSheet("color: #a78bfa; font-size: 10.5px;")
        elif intent == "copy_batch":
            if hasattr(self, "btn_copy_selected_batch"):
                self.btn_copy_selected_batch.setEnabled(False)
                self.btn_copy_selected_batch.setText("⏳ Đang kiểm tra nguồn...")
            b_num = (selected_batch_index or 0) + 1
            self.lbl_json_hint.setText(f"Đang kiểm tra tính toàn vẹn của tệp nguồn cho Lô {b_num}...")
            self.lbl_json_hint.setStyleSheet("color: #38bdf8; font-size: 10.5px;")
        elif intent == "validate_batch_draft":
            if hasattr(self, "btn_submit_batch_draft"):
                self.btn_submit_batch_draft.setEnabled(False)
                self.btn_submit_batch_draft.setText("⏳ Đang thẩm định...")
            b_num = (selected_batch_index or 0) + 1
            self.lbl_batch_draft_status.setText(f"Đang thẩm định hình học bản thảo cho Lô {b_num}...")
            self.lbl_batch_draft_status.setStyleSheet("color: #38bdf8; font-size: 10px;")
        elif intent == "copy_project_overview":
            if hasattr(self, "btn_copy_project_overview"):
                self.btn_copy_project_overview.setEnabled(False)
                self.btn_copy_project_overview.setText("⏳ Đang chuẩn bị tổng quan...")
            if hasattr(self, "lbl_overview_status"):
                self.lbl_overview_status.setText("Đang tổng hợp kịch bản toàn dự án...")
                self.lbl_overview_status.setStyleSheet("color: #0d9488; font-size: 10px;")

        worker.start()
        return req_id

    def _on_prompt_preparation_finished(self, req_id: str, success: bool, message: str, result: dict):
        """Xử lý kết quả chuẩn bị Prompt logic từ worker nền."""
        try:
            # 0. Thẩm định kiểu payload result an toàn (không để exception thoát Qt slot)
            if not isinstance(result, dict):
                logger.warning("Bỏ qua result payload không phải dict: %r", type(result))
                return

            # 1. Exactly-once: consume kết quả mỗi request đúng 1 lần
            if not hasattr(self, "_consumed_prompt_request_ids"):
                self._consumed_prompt_request_ids = set()
            if req_id in self._consumed_prompt_request_ids:
                logger.warning("Bỏ qua duplicate finished signal cho request: %s", req_id)
                return
            self._consumed_prompt_request_ids.add(req_id)

            # 2. Bỏ qua nếu bị supersede bởi generation mới hơn hoặc cửa sổ đang đóng
            if req_id != self._active_prompt_request_id or getattr(self, "_review_close_pending", False):
                logger.info("Bỏ qua kết quả Prompt preparation cũ hoặc đang đóng: %s (active: %s)", req_id, self._active_prompt_request_id)
                return

            # Xử lý thất bại hoặc hủy bỏ TRƯỚC: Failure không cần full success schema nhưng phải dọn dẹp vật lý đúng
            if not success:
                self._pending_prompt_expected = None
                if hasattr(self, "btn_copy_video_prompt") and not getattr(self, "_review_close_pending", False):
                    self.btn_copy_video_prompt.setText("📋 1. Prompt Dựng Video (V1)")
                if hasattr(self, "btn_prepare_batches") and not getattr(self, "_review_close_pending", False):
                    self.btn_prepare_batches.setText("📦 4. Phân Lô Kịch Bản (Batch Planner)")
                if hasattr(self, "btn_copy_selected_batch") and not getattr(self, "_review_close_pending", False):
                    self.btn_copy_selected_batch.setText("📋 Sao Chép Lô")
                if hasattr(self, "btn_submit_batch_draft") and not getattr(self, "_review_close_pending", False):
                    self.btn_submit_batch_draft.setText("📥 Nạp Bản Thảo Cho Lô Đang Chọn")
                if hasattr(self, "btn_copy_project_overview") and not getattr(self, "_review_close_pending", False):
                    self.btn_copy_project_overview.setText("📋 Sao Chép Tổng Quan Dự Án")
                err_msg = result.get("error") or message if isinstance(result, dict) else message
                if self._active_prompt_intent == "copy_project_overview":
                    err_text = f"✕ {err_msg}"
                    self.lbl_overview_status.setText(err_text)
                    self.lbl_overview_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                elif self._active_prompt_intent == "validate_batch_draft":
                    err_code = result.get("error_code") if isinstance(result, dict) else None
                    err_text = f"✕ {err_msg} ({err_code})" if err_code else f"✕ {err_msg}"
                    self.lbl_batch_draft_status.setText(err_text)
                    self.lbl_batch_draft_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                elif message == "cancelled":
                    self.lbl_json_hint.setText("ℹ Đã hủy chuẩn bị Prompt.")
                    self.lbl_json_hint.setStyleSheet("color: #a1a1aa; font-size: 10.5px;")
                elif message == "no_data":
                    self.lbl_json_hint.setText(f"⚠ {err_msg}")
                    self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px;")
                elif message == "budget_error":
                    self.lbl_json_hint.setText(f"⚠ Giới hạn dung lượng: {err_msg}")
                    self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                elif message in ("source_missing", "source_changed", "source_error"):
                    self.lbl_json_hint.setText(f"⚠ Lỗi tệp nguồn: {err_msg}")
                    self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                    if self._active_prompt_intent == "copy_batch":
                        self._invalidate_prepared_batches()
                else:
                    self.lbl_json_hint.setText(f"⚠ {err_msg}")
                    self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                self._update_overview_button_state()
                return

            # SUCCESS PATH: Bắt buộc result là dict
            if not isinstance(result, dict):
                logger.warning("Rejecting non-dict result payload for request: %s", req_id)
                return

            from src.ui.director.workers.prompt_preparation_worker import validate_and_normalize_snapshot

            # 3. Terminal Request Authority: Owned pending record IS MANDATORY for batch
            pending_expected = getattr(self, "_pending_prompt_expected", None)
            if not pending_expected or pending_expected.get("request_id") != req_id:
                logger.warning("Bỏ qua terminal thành công do thiếu pending record hoặc request_id không khớp: %s", req_id)
                return

            expected_intent = pending_expected.get("intent")
            if type(expected_intent) is not str or not expected_intent.strip():
                logger.warning("Invalid expected_intent type: %r", expected_intent)
                return

            # Thẩm định generation trong pending_expected: strict int và khớp self._prompt_generation
            expected_gen = pending_expected.get("generation")
            if type(expected_gen) is not int or expected_gen != self._prompt_generation:
                logger.warning("Generation mismatch: %r != %r", expected_gen, self._prompt_generation)
                return

            # G06-06A3-FINAL Required Envelope Gate: Đối với prepare_batches, copy_batch và validate_batch_draft,
            # result envelope bắt buộc phải có request_id, generation (strict int không bool), intent (str exact).
            if expected_intent in ("prepare_batches", "copy_batch", "validate_batch_draft"):
                res_req_id = result.get("request_id")
                if type(res_req_id) is not str or not res_req_id.strip() or res_req_id != req_id:
                    logger.warning("Batch result request_id missing, not str, or mismatch: %r != %r", res_req_id, req_id)
                    return

                res_gen = result.get("generation")
                if type(res_gen) is not int or res_gen != expected_gen:
                    logger.warning("Batch result generation missing, not int (e.g. bool), or mismatch: %r != %r", res_gen, expected_gen)
                    return

                res_intent = result.get("intent")
                if type(res_intent) is not str or not res_intent.strip() or res_intent != expected_intent:
                    logger.warning("Batch result intent missing, not str, or forged: %r != %r", res_intent, expected_intent)
                    return
            else:
                # V1 copy_video / run_ai compatibility
                res_gen = result.get("generation")
                if res_gen is not None:
                    if type(res_gen) is not int or res_gen != expected_gen:
                        logger.warning("Result generation mismatch or not int: %r != %r", res_gen, expected_gen)
                        return

                res_req_id = result.get("request_id")
                if res_req_id is not None and res_req_id != req_id:
                    logger.warning("Result request_id mismatch: %r != %r", res_req_id, req_id)
                    return

                res_intent = result.get("intent")
                if res_intent is not None and res_intent != expected_intent:
                    logger.warning("Forged intent detected! result.intent=%r != expected_intent=%r. Discarding.", res_intent, expected_intent)
                    return

            # Terminal Creative Authority Check: đối soát chữ ký creative_context giữa pending_expected và result:
            if "expected_creative_context_signature" not in pending_expected:
                logger.warning(
                    "Terminal creative authority check denied: pending_expected thiếu trường 'expected_creative_context_signature'."
                )
                self._pending_prompt_expected = None
                return

            exp_cc_sig = pending_expected["expected_creative_context_signature"]
            res_cc_sig = result.get("creative_context_signature")

            def _is_canonical_hex64(val: Any) -> bool:
                """Kiểm tra giá trị có phải chuỗi hex 64 ký tự viết thường chuẩn tắc (strict lowercase 64hex)."""
                if type(val) is not str or len(val) != 64:
                    return False
                return all(c in "0123456789abcdef" for c in val)

            if exp_cc_sig is not None:
                # Ngữ cảnh sáng tạo không rỗng: cả expected và result đều PHẢI là canonical lowercase 64hex và bằng nhau từng ký tự
                if not _is_canonical_hex64(exp_cc_sig):
                    logger.warning(
                        "Terminal creative expected signature malformed (not lowercase 64hex): %r",
                        exp_cc_sig,
                    )
                    self._pending_prompt_expected = None
                    return

                if not _is_canonical_hex64(res_cc_sig) or res_cc_sig != exp_cc_sig:
                    logger.warning(
                        "Terminal creative context signature mismatch or malformed: res=%r, exp=%r",
                        res_cc_sig,
                        exp_cc_sig,
                    )
                    self._pending_prompt_expected = None
                    return
            else:
                # Legacy expected (explicit None): result signature phải là None hoặc vắng mặt
                if res_cc_sig is not None:
                    logger.warning(
                        "Terminal creative signature unexpectedly present for legacy pending: %r",
                        res_cc_sig,
                    )
                    self._pending_prompt_expected = None
                    return

            # 4. Kiểm tra xem ngữ cảnh hiện tại và ngữ cảnh sáng tạo có bị thay đổi trong lúc worker đang chạy không
            curr_ctx = self._get_current_context()
            curr_sig = self._extract_context_signature(curr_ctx)
            expected_sig = pending_expected.get("context_signature")
            if expected_sig is not None and curr_sig != expected_sig:
                logger.info("Bỏ qua kết quả Prompt preparation do ngữ cảnh đã bị thay đổi: %s", req_id)
                self.lbl_json_hint.setText("⚠ Yêu cầu thay đổi, chuẩn bị lại.")
                self.lbl_json_hint.setStyleSheet("color: #a1a1aa; font-size: 10.5px;")
                self._prepared_prompt_snapshot = None
                self._invalidate_prepared_batches()
                self._pending_prompt_expected = None
                self._update_overview_button_state()
                return

            curr_cc = curr_ctx.get("creative_context")
            curr_cc_sig = (
                compute_creative_context_signature(curr_cc)
                if (curr_cc is not None and curr_cc != CreativeContext())
                else None
            )
            if curr_cc_sig != exp_cc_sig:
                logger.info("Ngữ cảnh sáng tạo hiện tại đã thay đổi trong lúc worker chạy: %r != %r", curr_cc_sig, exp_cc_sig)
                self.lbl_json_hint.setText("⚠ Yêu cầu thay đổi, chuẩn bị lại.")
                self.lbl_json_hint.setStyleSheet("color: #a1a1aa; font-size: 10.5px;")
                self._prepared_prompt_snapshot = None
                self._invalidate_prepared_batches()
                self._pending_prompt_expected = None
                self._update_overview_button_state()
                return

            # 6. Xử lý thành công - Dùng expected_intent để phân nhánh
            if expected_intent == "prepare_batches":
                batch_snapshot = result.get("batch_snapshot")
                if not batch_snapshot:
                    self.lbl_json_hint.setText("⚠ Kết quả phân lô không hợp lệ.")
                    self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                    return

                # Validate full prepare result/snapshot trước khi gán _manifest/UI controls/snapshot
                norm_snap = validate_and_normalize_snapshot(batch_snapshot)
                if norm_snap is None:
                    self.lbl_json_hint.setText("⚠ Dữ liệu phân lô không vượt qua kiểm định tính toàn vẹn.")
                    self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                    return

                snap_cc_sig = getattr(norm_snap, "creative_context_signature", None)
                if snap_cc_sig != exp_cc_sig:
                    logger.warning("Snapshot creative context signature mismatch: %r != %r", snap_cc_sig, exp_cc_sig)
                    self._pending_prompt_expected = None
                    return

                # Đối chiếu project_name và manifest_id nếu đã biết trong pending_expected
                exp_proj = pending_expected.get("project_name")
                if exp_proj and norm_snap.project_name != exp_proj:
                    logger.warning("Snapshot project_name (%s) != expected (%s)", norm_snap.project_name, exp_proj)
                    return
                res_proj = result.get("project_name")
                if exp_proj and res_proj and res_proj != exp_proj:
                    logger.warning("Result project_name (%s) != expected (%s)", res_proj, exp_proj)
                    return

                exp_mid = pending_expected.get("manifest_id")
                if exp_mid and norm_snap.manifest_id != exp_mid:
                    logger.warning("Snapshot manifest_id (%s) != expected (%s)", norm_snap.manifest_id, exp_mid)
                    return
                res_mid = result.get("manifest_id")
                if exp_mid and res_mid and res_mid != exp_mid:
                    logger.warning("Result manifest_id (%s) != expected (%s)", res_mid, exp_mid)
                    return
                norm_snap = validate_and_normalize_snapshot(batch_snapshot)
                if norm_snap is None:
                    self.lbl_json_hint.setText("⚠ Dữ liệu phân lô không vượt qua kiểm định tính toàn vẹn.")
                    self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                    return

                # Nhận manifest_metadata và khôi phục self._manifest in-memory nếu có
                manifest_meta = result.get("manifest_metadata")
                if manifest_meta and isinstance(manifest_meta, dict):
                    try:
                        from src.core.clip_manifest import ClipEntry, ClipManifest
                        clips = [ClipEntry.from_dict(c) for c in manifest_meta.get("clips", [])]
                        self._manifest = ClipManifest(
                            project_name=manifest_meta.get("project_name", "ChunDVC_Project"),
                            manifest_id=str(manifest_meta.get("manifest_id", "")),
                            clips=clips,
                            created_at=str(manifest_meta.get("created_at", "")),
                            updated_at=str(manifest_meta.get("updated_at", "")),
                        )
                    except Exception as me:
                        logger.warning("Không thể khôi phục self._manifest từ manifest_metadata: %s", me)

                # Cập nhật ui_context_signature vào frozen DTO thông qua replace bao gồm cả manifest_id vừa được gán
                snapshot_ctx = self._get_current_context()
                snapshot_sig = self._extract_context_signature(snapshot_ctx)
                owned_snapshot = dataclasses.replace(
                    norm_snap,
                    ui_context_signature=snapshot_sig
                )
                self._prepared_batch_snapshot = owned_snapshot

                batches = owned_snapshot.batches
                total_batches = len(batches)
                self.cbo_batch_selector.blockSignals(True)
                self.cbo_batch_selector.clear()
                for b in batches:
                    b_idx = b.batch_index
                    n_items = b.items_count
                    n_bytes = b.rendered_bytes
                    self.cbo_batch_selector.addItem(
                        f"Lô {b_idx + 1}/{total_batches} ({n_items} clips, {n_bytes:,} B)",
                        userData=b_idx
                    )
                self.cbo_batch_selector.blockSignals(False)

                distinct_clips = owned_snapshot.distinct_planned_clips_count
                planned_items = owned_snapshot.planned_items_count
                unaccounted_list = owned_snapshot.unaccounted_clips
                unaccounted_ids = owned_snapshot.unaccounted_clip_ids
                budget_cap = owned_snapshot.max_prompt_bytes

                summary_txt = f"{total_batches} lô | {distinct_clips} clips ({planned_items} đoạn)"
                if unaccounted_ids:
                    reasons = set()
                    for u in unaccounted_list:
                        r = getattr(u, "reason", None) or (u.get("reason") if isinstance(u, dict) else "")
                        if r in ("ERR_UNKNOWN_DURATION", "unknown_duration", "DURATION_UNKNOWN"):
                            reasons.add("chưa rõ thời lượng")
                        elif r == "unsupported_media":
                            reasons.add("định dạng không hỗ trợ")
                        elif r == "no_evidence":
                            reasons.add("chưa có bằng chứng")
                        elif r:
                            reasons.add(str(r))
                    r_str = f" ({', '.join(sorted(reasons))})" if reasons else ""
                    summary_txt += f" | {len(unaccounted_ids)} clips chưa phân lô{r_str}"
                summary_txt += f" | Trần: {budget_cap:,} B"

                self.lbl_batch_summary.setText(summary_txt)
                self.lbl_batch_summary.setStyleSheet("color: #a78bfa; font-size: 10px; font-weight: 500;")

                # Physical busy check: chỉ bật Copy nếu batches > 0 VÀ không còn worker vật lý nào trong registry
                has_physical_workers = bool(self._prompt_workers_registry)
                if batches and not has_physical_workers:
                    self.btn_copy_selected_batch.setEnabled(True)
                else:
                    self.btn_copy_selected_batch.setEnabled(False)

                if batches:
                    self.cbo_batch_selector.setCurrentIndex(0)
                    self._update_batch_preview(0)
                    self.txt_batch_preview.setVisible(True)
                    self.lbl_json_hint.setText(f"✓ Đã phân chia thành {total_batches} lô an toàn! Chọn lô và bấm '📋 Sao Chép Lô'.")
                    self.lbl_json_hint.setStyleSheet("color: #10b981; font-size: 10.5px; font-weight: bold;")
                else:
                    self.btn_copy_selected_batch.setEnabled(False)
                    self.txt_batch_preview.clear()
                    self.txt_batch_preview.setVisible(False)
                    self.lbl_json_hint.setText("⚠ Không có lô nào được tạo (toàn bộ clip chưa có phân tích hợp lệ).")
                    self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px;")

                # Tuyệt đối KHÔNG tự động ghi Clipboard khi phân lô xong!
                self._pending_prompt_expected = None
                self._update_overview_button_state()
                return

            if expected_intent == "copy_batch":
                # Result envelope verification: ALL required fields MUST be present and match pending_expected
                # 1. snapshot_id: bắt buộc và phải khớp
                res_snap_id = result.get("snapshot_id")
                if type(res_snap_id) is not str or not res_snap_id or res_snap_id != pending_expected["snapshot_id"]:
                    logger.warning("Result snapshot_id mismatch or not str: %r != %r", res_snap_id, pending_expected.get("snapshot_id"))
                    return

                # 2. manifest_id: bắt buộc và phải khớp
                res_man_id = result.get("manifest_id")
                if type(res_man_id) is not str or not res_man_id or res_man_id != pending_expected["manifest_id"]:
                    logger.warning("Result manifest_id mismatch or not str: %r != %r", res_man_id, pending_expected.get("manifest_id"))
                    return

                # 3. project_name: bắt buộc và phải khớp
                res_proj = result.get("project_name")
                if type(res_proj) is not str or not res_proj or res_proj != pending_expected["project_name"]:
                    logger.warning("Result project_name mismatch or not str: %r != %r", res_proj, pending_expected.get("project_name"))
                    return

                # 4. batch_id: bắt buộc và phải khớp
                res_b_id = result.get("batch_id")
                if type(res_b_id) is not str or not res_b_id or res_b_id != pending_expected["batch_id"]:
                    logger.warning("Result batch_id mismatch or not str: %r != %r", res_b_id, pending_expected.get("batch_id"))
                    return

                # 5. selected_batch_index: strict int (loại bỏ bool: True == 1), khớp expected
                res_idx = result.get("selected_batch_index")
                if type(res_idx) is not int or res_idx != pending_expected["batch_index"]:
                    logger.warning("Result selected_batch_index mismatch or not int: %r != %r", res_idx, pending_expected.get("batch_index"))
                    return

                # 6. UI selection matching
                current_ui_index = self.cbo_batch_selector.currentIndex()
                if res_idx != current_ui_index:
                    logger.info("Bỏ qua kết quả copy Lô %s vì UI đã đổi sang Lô %s", res_idx + 1, current_ui_index + 1)
                    return

                # 7. Current snapshot in-memory matching
                if not self._prepared_batch_snapshot:
                    logger.warning("Snapshot hiện tại đã bị mất hoặc vô hiệu hóa.")
                    return
                curr_snap = self._prepared_batch_snapshot
                curr_snap_id = getattr(curr_snap, "snapshot_id", None) or (curr_snap.get("snapshot_id") if isinstance(curr_snap, dict) else None)
                if curr_snap_id != pending_expected["snapshot_id"]:
                    logger.warning("Snapshot hiện tại (%s) khác snapshot lúc dispatch (%s). Bỏ qua stale result.", curr_snap_id, pending_expected["snapshot_id"])
                    return
                curr_snap_cc_sig = getattr(curr_snap, "creative_context_signature", None) if not isinstance(curr_snap, dict) else curr_snap.get("creative_context_signature")
                if curr_snap_cc_sig != exp_cc_sig:
                    logger.warning("Snapshot creative signature mismatch in copy_batch: %r != %r", curr_snap_cc_sig, exp_cc_sig)
                    self._pending_prompt_expected = None
                    return

                # 8. rendered_sha256
                res_sha = result.get("rendered_sha256")
                if type(res_sha) is not str or not res_sha or res_sha != pending_expected["rendered_sha256"]:
                    logger.warning("Result rendered_sha256 mismatch or not str: %r != %r", res_sha, pending_expected.get("rendered_sha256"))
                    return

                # 9. prompt_text & bytes
                prompt_text = result.get("prompt_text")
                if type(prompt_text) is not str or not prompt_text.strip():
                    self.lbl_json_hint.setText("⚠ Kịch bản lô rỗng.")
                    self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                    return

                actual_bytes = len(prompt_text.encode("utf-8"))
                if actual_bytes != pending_expected["rendered_bytes"]:
                    logger.warning("Prompt bytes nhận được (%s) lệch expected (%s)", actual_bytes, pending_expected["rendered_bytes"])
                    return

                # 10. actual SHA-256
                actual_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
                if actual_sha != pending_expected["rendered_sha256"]:
                    logger.warning("Prompt text nhận được bị lệch SHA256 so với expected!")
                    return

                try:
                    QApplication.clipboard().setText(prompt_text)
                    self.btn_copy_selected_batch.setText(f"✓ Đã chép Lô {res_idx + 1}!")
                    target_req = req_id
                    target_gen = self._prompt_generation
                    def _reset_batch_btn_label():
                        if getattr(self, "_review_close_pending", False):
                            return
                        if (
                            self._active_prompt_request_id == target_req
                            and self._prompt_generation == target_gen
                            and hasattr(self, "btn_copy_selected_batch")
                        ):
                            self.btn_copy_selected_batch.setText("📋 Sao Chép Lô")
                    QTimer.singleShot(2500, _reset_batch_btn_label)
                    self.lbl_json_hint.setText(f"✓ Đã sao chép Lô {res_idx + 1} ({actual_bytes:,} B) vào Clipboard! Hãy dán vào AI.")
                    self.lbl_json_hint.setStyleSheet("color: #10b981; font-size: 10.5px; font-weight: bold;")
                    self.prompt_copied.emit("batch")
                except Exception as ce:
                    logger.error("Lỗi khi ghi clipboard cho batch: %s", ce)
                    self.lbl_json_hint.setText(f"⚠ Lỗi sao chép clipboard: {ce}")
                    self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                finally:
                    self._pending_prompt_expected = None
                self._update_overview_button_state()
                return

            if expected_intent == "validate_batch_draft":
                # Result envelope verification: ALL required fields MUST be present and match pending_expected
                if hasattr(self, "btn_submit_batch_draft"):
                    self.btn_submit_batch_draft.setText("📥 Nạp Bản Thảo Cho Lô Đang Chọn")

                # 1. snapshot_id: bắt buộc và phải khớp
                res_snap_id = result.get("snapshot_id")
                if type(res_snap_id) is not str or not res_snap_id or res_snap_id != pending_expected["snapshot_id"]:
                    logger.warning("Result snapshot_id mismatch or not str: %r != %r", res_snap_id, pending_expected.get("snapshot_id"))
                    self._pending_prompt_expected = None
                    return

                # 2. manifest_id: bắt buộc và phải khớp
                res_man_id = result.get("manifest_id")
                if type(res_man_id) is not str or not res_man_id or res_man_id != pending_expected["manifest_id"]:
                    logger.warning("Result manifest_id mismatch or not str: %r != %r", res_man_id, pending_expected.get("manifest_id"))
                    self._pending_prompt_expected = None
                    return

                # 3. project_name: bắt buộc và phải khớp
                res_proj = result.get("project_name")
                if type(res_proj) is not str or not res_proj or res_proj != pending_expected["project_name"]:
                    logger.warning("Result project_name mismatch or not str: %r != %r", res_proj, pending_expected.get("project_name"))
                    self._pending_prompt_expected = None
                    return

                # 4. batch_id: bắt buộc và phải khớp
                res_b_id = result.get("batch_id")
                if type(res_b_id) is not str or not res_b_id or res_b_id != pending_expected["batch_id"]:
                    logger.warning("Result batch_id mismatch or not str: %r != %r", res_b_id, pending_expected.get("batch_id"))
                    self._pending_prompt_expected = None
                    return

                # 5. selected_batch_index: strict int (loại bỏ bool: True == 1), khớp expected
                res_idx = result.get("selected_batch_index")
                if type(res_idx) is not int or res_idx != pending_expected["batch_index"]:
                    logger.warning("Result selected_batch_index mismatch or not int: %r != %r", res_idx, pending_expected.get("batch_index"))
                    self._pending_prompt_expected = None
                    return

                # 6. UI selection matching
                current_ui_index = self.cbo_batch_selector.currentIndex()
                if res_idx != current_ui_index:
                    logger.info("Bỏ qua kết quả thẩm định Lô %s vì UI đã đổi sang Lô %s", res_idx + 1, current_ui_index + 1)
                    self._pending_prompt_expected = None
                    return

                # 7. Current snapshot in-memory matching
                if not self._prepared_batch_snapshot:
                    logger.warning("Snapshot hiện tại đã bị mất hoặc vô hiệu hóa.")
                    self._pending_prompt_expected = None
                    return
                norm_snap = validate_and_normalize_snapshot(self._prepared_batch_snapshot)
                if norm_snap is None or norm_snap.snapshot_id != pending_expected["snapshot_id"]:
                    logger.warning("Snapshot hiện tại khác snapshot lúc dispatch. Bỏ qua stale result.")
                    self._pending_prompt_expected = None
                    return
                if getattr(norm_snap, "creative_context_signature", None) != exp_cc_sig:
                    logger.warning("Snapshot creative signature mismatch in validate_batch_draft: %r != %r", getattr(norm_snap, "creative_context_signature", None), exp_cc_sig)
                    self._pending_prompt_expected = None
                    return

                # 8. PENDING RAW PROOF & BINDING POLICY REQUIRED (No fallbacks!)
                pending_raw_hash = pending_expected.get("expected_raw_payload_sha256")
                if (
                    not isinstance(pending_raw_hash, str)
                    or len(pending_raw_hash) != 64
                    or not all(c in "0123456789abcdefABCDEF" for c in pending_raw_hash)
                ):
                    logger.warning("Missing or malformed expected_raw_payload_sha256 in pending record: %r", pending_raw_hash)
                    self._pending_prompt_expected = None
                    return

                VALID_BINDING_MODES = ("echo_matched", "user_associated_current_snapshot")
                allowed_modes = pending_expected.get("allowed_binding_modes")
                if (
                    not isinstance(allowed_modes, tuple)
                    or not allowed_modes
                    or not all(isinstance(m, str) and m in VALID_BINDING_MODES for m in allowed_modes)
                ):
                    logger.warning("Missing or malformed allowed_binding_modes in pending record: %r", allowed_modes)
                    self._pending_prompt_expected = None
                    return

                res_binding_mode = result.get("binding_mode")
                if not isinstance(res_binding_mode, str) or res_binding_mode not in allowed_modes:
                    logger.warning("Result binding_mode %r not in allowed_binding_modes %r", res_binding_mode, allowed_modes)
                    self._pending_prompt_expected = None
                    return

                val_dict = result.get("validated_entry")
                if not val_dict or not isinstance(val_dict, dict):
                    logger.warning("Missing or invalid validated_entry dict in result")
                    self._pending_prompt_expected = None
                    return

                if val_dict.get("binding_mode") != res_binding_mode:
                    logger.warning("Envelope binding_mode %r != inner validated_entry binding_mode %r", res_binding_mode, val_dict.get("binding_mode"))
                    self._pending_prompt_expected = None
                    return

                # 9. Pure Integrity Gate Verification
                from src.core.batch_draft_contracts import verify_validated_entry_integrity

                selected_b = norm_snap.batches[res_idx]
                verified_entry = verify_validated_entry_integrity(
                    val_dict,
                    expected_manifest_id=pending_expected["manifest_id"],
                    expected_snapshot_id=pending_expected["snapshot_id"],
                    expected_batch_id=pending_expected["batch_id"],
                    expected_source_ranges=selected_b.source_ranges,
                    expected_binding_mode=res_binding_mode,
                    expected_draft_digest=result.get("draft_digest", ""),
                    expected_raw_payload_sha256=pending_raw_hash,
                )

                if verified_entry is None:
                    logger.warning("Bản thảo không vượt qua cổng đối soát toàn vẹn hình học (Integrity Gate failed). 0 ledger publish.")
                    self.lbl_batch_draft_status.setText("⚠ Dữ liệu bản thảo không khớp hình học lô phân mảnh. Đã từ chối nạp.")
                    self.lbl_batch_draft_status.setStyleSheet("color: #ef4444; font-size: 10px;")
                    self._pending_prompt_expected = None
                    return

                # 10. Replacement Race Check & Idempotent No-op (Chỉ chạy SAU KHI proof gate đã xác nhận hợp lệ)
                cur_entry = self._batch_drafts_ledger.get((res_snap_id, res_b_id))
                cur_digest = cur_entry.draft_digest if cur_entry is not None else None
                exp_old_digest = pending_expected.get("expected_existing_draft_digest")

                # So currentdigest với captured expectedold vô điều kiện
                if cur_digest != exp_old_digest:
                    logger.warning("Bản thảo hiện tại đã bị thay đổi trong lúc worker chạy (%r != %r). Bỏ qua để tránh overwrite race.", cur_digest, exp_old_digest)
                    self.lbl_batch_draft_status.setText("⚠ Bản thảo hiện tại đã bị thay đổi trong lúc thẩm định. Đã hủy nạp để tránh ghi đè.")
                    self.lbl_batch_draft_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                    self._pending_prompt_expected = None
                    return

                # Nếu trước đó đã có bản thảo (cur_entry is not None):
                if cur_entry is not None:
                    # 10a. Same-digest no-op:
                    if cur_digest == verified_entry.draft_digest:
                        self.lbl_batch_draft_status.setText(f"ℹ Bản thảo Lô {res_idx + 1} trùng khớp nội dung bản thảo đã có (Idempotent no-op).")
                        self.lbl_batch_draft_status.setStyleSheet("color: #38bdf8; font-size: 10px;")
                        self._pending_prompt_expected = None
                        return

                    # 10b. Khác digest: Kiểm tra cờ opt-in trước dispatch
                    allow_replace = pending_expected.get("allow_replace_draft", False)
                    if not allow_replace:
                        logger.info("Lô %s đã có bản thảo nhưng chưa bật tùy chọn ghi đè. Giữ nguyên bản thảo cũ.", res_idx + 1)
                        self.lbl_batch_draft_status.setText(f"ℹ Giữ nguyên bản thảo cũ của Lô {res_idx + 1} (chưa đánh dấu 'Ghi đè bản thảo đã có của lô này').")
                        self.lbl_batch_draft_status.setStyleSheet("color: #a1a1aa; font-size: 10px;")
                        self._pending_prompt_expected = None
                        return

                # 11. Ghi nhận vào ledger
                self._batch_drafts_ledger[(res_snap_id, res_b_id)] = verified_entry

                # 12. Cập nhật tiến độ & nhãn selector (schema/bounds only, TUYỆT ĐỐI KHÔNG MUTATE slots hay plans!)
                cur_batches = norm_snap.batches
                total_b = len(cur_batches)
                received_cnt = sum(1 for (sid, bid) in self._batch_drafts_ledger.keys() if sid == res_snap_id)

                self.lbl_batch_draft_status.setText(
                    f"✓ Đã nhận bản thảo Lô {res_idx + 1}! Tiến độ: Đã nhận {received_cnt}/{total_b} bản thảo (Chỉ lưu trong phiên)."
                )
                self.lbl_batch_draft_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
                self.txt_batch_draft_input.clear()

                # Cập nhật nhãn combobox text thêm badge ✓ Đã nhận, bảo toàn itemData
                cur_item_text = self.cbo_batch_selector.itemText(res_idx)
                cur_user_data = self.cbo_batch_selector.itemData(res_idx)
                if "[✓ Đã nhận]" not in cur_item_text:
                    self.cbo_batch_selector.blockSignals(True)
                    self.cbo_batch_selector.setItemText(res_idx, f"{cur_item_text} [✓ Đã nhận]")
                    if cur_user_data is not None:
                        self.cbo_batch_selector.setItemData(res_idx, cur_user_data)
                    self.cbo_batch_selector.blockSignals(False)

                self._pending_prompt_expected = None
                self._update_overview_button_state()
                return

            if expected_intent == "copy_project_overview":
                from src.core.project_overview_contracts import FORMATTER_POLICY
                from src.ui.director.workers.prompt_preparation_worker import (
                    compute_project_overview_ledger_signature,
                    validate_project_overview_result_against_artifact,
                )

                # 0. Pending expected gate & field validation
                if not isinstance(pending_expected, dict):
                    logger.warning("Pending expected record missing or invalid for overview.")
                    return

                req_fields = (
                    "request_id", "generation", "intent", "snapshot_id", "manifest_id",
                    "project_name", "expected_ledger_signature", "formatter_policy",
                    "max_prompt_bytes", "ui_context_signature", "worker_ref"
                )
                missing_or_invalid = False
                for fld in req_fields:
                    val = pending_expected.get(fld)
                    if val is None:
                        missing_or_invalid = True
                        break
                    if fld in ("request_id", "intent", "snapshot_id", "manifest_id", "project_name", "expected_ledger_signature", "formatter_policy") and not isinstance(val, str):
                        missing_or_invalid = True
                        break
                    if fld == "generation" and not isinstance(val, int):
                        missing_or_invalid = True
                        break
                    if fld == "max_prompt_bytes" and (not isinstance(val, int) or val <= 0):
                        missing_or_invalid = True
                        break

                if missing_or_invalid:
                    logger.warning("Pending expected record missing required fields or has invalid types for overview.")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                # Policy gate: formatter_policy in pending must match FORMATTER_POLICY
                pending_policy = pending_expected.get("formatter_policy")
                if pending_policy != FORMATTER_POLICY:
                    logger.warning("Pending formatter policy invalid or unsupported: %r != %r", pending_policy, FORMATTER_POLICY)
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                # 1. Envelope verification: request_id, generation, intent
                res_req_id = result.get("request_id")
                if type(res_req_id) is not str or not res_req_id.strip() or res_req_id != req_id:
                    logger.warning("Project overview result request_id mismatch or not str: %r != %r", res_req_id, req_id)
                    return

                res_gen = result.get("generation")
                if type(res_gen) is not int or res_gen != expected_gen:
                    logger.warning("Project overview result generation mismatch: %r != %r", res_gen, expected_gen)
                    return

                res_intent = result.get("intent")
                if res_intent != "copy_project_overview":
                    logger.warning("Project overview result intent mismatch: %r", res_intent)
                    return

                # 2. Recheck current context and snapshot (ZERO fallback context_signature)
                curr_ctx = self._get_current_context()
                if not curr_ctx:
                    logger.warning("Current context missing during overview completion.")
                    return

                curr_sig = self._extract_context_signature(curr_ctx)
                if not curr_sig or curr_sig != pending_expected.get("ui_context_signature"):
                    logger.warning("Context changed during overview execution. Stale (0 copy).")
                    if hasattr(self, "lbl_overview_status"):
                        self.lbl_overview_status.setText("⚠ Cấu hình đã thay đổi trong lúc tổng hợp. Vui lòng sao chép lại.")
                        self.lbl_overview_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                if not self._prepared_batch_snapshot:
                    logger.warning("Prepared batch snapshot missing during overview completion. Stale.")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                norm_snap = validate_and_normalize_snapshot(self._prepared_batch_snapshot)
                if norm_snap is None or norm_snap.snapshot_id != pending_expected.get("snapshot_id"):
                    logger.warning("Snapshot missing or changed during overview execution. Stale (0 copy).")
                    if hasattr(self, "lbl_overview_status"):
                        self.lbl_overview_status.setText("⚠ Phân lô đã thay đổi trong lúc tổng hợp. Vui lòng phân lô lại.")
                        self.lbl_overview_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                if getattr(norm_snap, "creative_context_signature", None) != exp_cc_sig:
                    logger.warning("Snapshot creative signature mismatch during overview terminal: %r != %r", getattr(norm_snap, "creative_context_signature", None), exp_cc_sig)
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                snap_ui_sig = getattr(norm_snap, "ui_context_signature", None)
                if snap_ui_sig is None and isinstance(self._prepared_batch_snapshot, dict):
                    snap_ui_sig = self._prepared_batch_snapshot.get("ui_context_signature")
                if snap_ui_sig != curr_sig:
                    logger.warning("Snapshot UI signature mismatch with current context signature.")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                # 3. Controlled collection & recompute ledger signature
                curr_drafts, recomputed_sig, draft_err = self._collect_valid_overview_drafts(norm_snap.snapshot_id)
                if curr_drafts is None or recomputed_sig is None:
                    logger.warning("Current ledger invalid during overview terminal: %s. Controlled deny (0 copy).", draft_err)
                    if hasattr(self, "lbl_overview_status"):
                        self.lbl_overview_status.setText("⚠ Bản thảo trong bộ nhớ không hợp lệ.")
                        self.lbl_overview_status.setStyleSheet("color: #ef4444; font-size: 10px;")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                if recomputed_sig != pending_expected.get("expected_ledger_signature"):
                    logger.warning("Ledger signature changed during overview execution. Stale (0 copy).")
                    if hasattr(self, "lbl_overview_status"):
                        self.lbl_overview_status.setText("⚠ Bản thảo lô đã thay đổi trong lúc tổng hợp. Vui lòng sao chép lại.")
                        self.lbl_overview_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                # 4. Lấy artifact từ owned worker của pending
                worker = pending_expected.get("worker_ref")
                if not worker or not hasattr(worker, "get_project_overview_artifact"):
                    logger.warning("Worker ref missing or invalid in pending record.")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                artifact = worker.get_project_overview_artifact()
                if artifact is None:
                    logger.warning("Project overview artifact is None.")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                # 5. Thẩm định validate_project_overview_result_against_artifact
                if not validate_project_overview_result_against_artifact(result, artifact):
                    logger.warning("Result does not match artifact! Possible tamper or mismatch.")
                    if hasattr(self, "lbl_overview_status"):
                        self.lbl_overview_status.setText("✕ Thẩm định artifact thất bại.")
                        self.lbl_overview_status.setStyleSheet("color: #ef4444; font-size: 10px;")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                # 6. Đối soát identity artifact
                if (
                    artifact.request_id != pending_expected.get("request_id")
                    or artifact.generation != pending_expected.get("generation")
                    or artifact.intent != "copy_project_overview"
                    or artifact.snapshot_id != norm_snap.snapshot_id
                    or artifact.manifest_id != norm_snap.manifest_id
                    or artifact.project_name != norm_snap.project_name
                    or artifact.ledger_signature != pending_expected.get("expected_ledger_signature")
                    or getattr(artifact, "formatter_policy", None) != pending_expected.get("formatter_policy")
                    or getattr(artifact, "max_prompt_bytes", None) != pending_expected.get("max_prompt_bytes")
                    or getattr(artifact, "context_signature", None) != pending_expected.get("ui_context_signature")
                    or getattr(artifact, "creative_context_signature", None) != exp_cc_sig
                ):
                    logger.warning("Artifact identity mismatch with pending record or current snapshot.")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                # 7. Ghi nhận clipboard ĐÚNG 1 LẦN
                try:
                    QApplication.clipboard().setText(artifact.prompt_text)
                except Exception as ce:
                    logger.error("Lỗi clipboard setText: %s", ce)
                    if hasattr(self, "lbl_overview_status"):
                        self.lbl_overview_status.setText(f"⚠ Lỗi sao chép: {ce}")
                    self._pending_prompt_expected = None
                    self._update_overview_button_state()
                    return

                # 8. Cập nhật label trạng thái
                m_count = len(curr_drafts)
                n_count = len(norm_snap.batches)
                u_count = len(norm_snap.unaccounted_clips)
                u_str = f", {u_count} clip chưa phân lô" if u_count > 0 else ""
                if hasattr(self, "lbl_overview_status"):
                    self.lbl_overview_status.setText(f"✓ Đã sao chép tổng quan dự án ({m_count}/{n_count} lô{u_str})!")
                    self.lbl_overview_status.setStyleSheet("color: #10b981; font-size: 10px; font-weight: bold;")

                if hasattr(self, "btn_copy_project_overview"):
                    self.btn_copy_project_overview.setText("📋 Sao Chép Tổng Quan Dự Án")

                self._pending_prompt_expected = None
                self._update_overview_button_state()
                return

            # 7. Xử lý thành công cho V1 (copy_video / run_ai)
            prompt_text = result.get("prompt_text", "")
            if not prompt_text:
                self.lbl_json_hint.setText("⚠ Kịch bản chuẩn bị rỗng.")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
                return

            # Nhận manifest_metadata và khôi phục self._manifest in-memory
            manifest_meta = result.get("manifest_metadata")
            if manifest_meta and isinstance(manifest_meta, dict):
                try:
                    from src.core.clip_manifest import ClipEntry, ClipManifest
                    clips = [ClipEntry.from_dict(c) for c in manifest_meta.get("clips", [])]
                    self._manifest = ClipManifest(
                        project_name=manifest_meta.get("project_name", "ChunDVC_Project"),
                        manifest_id=str(manifest_meta.get("manifest_id", "")),
                        clips=clips,
                        created_at=str(manifest_meta.get("created_at", "")),
                        updated_at=str(manifest_meta.get("updated_at", "")),
                    )
                except Exception as me:
                    logger.warning("Không thể khôi phục self._manifest từ manifest_metadata: %s", me)

            # Cập nhật signature chính thức của snapshot bao gồm cả manifest_id vừa được gán
            snapshot_ctx = self._get_current_context()
            snapshot_sig = self._extract_context_signature(snapshot_ctx)

            # Lưu snapshot đã sẵn sàng
            self._prepared_prompt_snapshot = {
                "prompt_text": prompt_text,
                "context_signature": snapshot_sig,
                "request_id": req_id,
                "manifest_id": result.get("manifest_id"),
                "creative_context_signature": exp_cc_sig,
            }

            orig_btn_text = "📋 1. Prompt Dựng Video (V1)"
            if expected_intent == "copy_video":
                try:
                    QApplication.clipboard().setText(prompt_text)
                    if hasattr(self, "btn_copy_video_prompt"):
                        self.btn_copy_video_prompt.setText("✓ Đã sao chép Prompt V1!")
                        target_req = req_id
                        def _reset_btn_label():
                            if getattr(self, "_review_close_pending", False):
                                return
                            if self._active_prompt_request_id == target_req:
                                if hasattr(self, "btn_copy_video_prompt"):
                                    self.btn_copy_video_prompt.setText(orig_btn_text)
                        QTimer.singleShot(2500, _reset_btn_label)
                    self.lbl_json_hint.setText("✓ Đã sao chép Prompt Dựng Video (V1) vào Clipboard! Hãy dán (Ctrl+V) vào ChatGPT hoặc Claude.")
                    self.lbl_json_hint.setStyleSheet("color: #38bdf8; font-size: 10.5px;")
                    self.prompt_copied.emit("video")
                except Exception as ce:
                    logger.error("Lỗi khi ghi clipboard: %s", ce)
                    self.lbl_json_hint.setText(f"⚠ Lỗi sao chép clipboard: {ce}")
                    self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            elif expected_intent == "run_ai":
                self.lbl_json_hint.setText("✓ Đã chuẩn bị kịch bản AI thành công, đang khởi chạy...")
                self.lbl_json_hint.setStyleSheet("color: #10b981; font-size: 10.5px;")
                self.prompt_ready_for_run.emit(req_id, prompt_text)
        except Exception as exc:
            logger.error("Lỗi trong _on_prompt_preparation_finished: %s", exc)
            return

    def get_video_prompt(self) -> str:
        """Lấy Prompt V1 đã được chuẩn bị sẵn in-memory khớp với ngữ cảnh hiện tại.

        Tuyệt đối không thực hiện I/O, hash, hoặc nạp cache trên GUI thread.
        Nếu chưa sẵn sàng hoặc ngữ cảnh không hợp lệ, trả về chuỗi rỗng ''.
        """
        if self._prepared_prompt_snapshot is not None:
            curr_ctx = self._get_current_context()
            if not curr_ctx:
                return ""
            curr_sig = self._extract_context_signature(curr_ctx)
            snap_sig = self._prepared_prompt_snapshot.get("context_signature")
            if not curr_sig or snap_sig != curr_sig:
                return ""
            curr_cc = curr_ctx.get("creative_context")
            curr_cc_sig = (
                compute_creative_context_signature(curr_cc)
                if (curr_cc is not None and curr_cc != CreativeContext())
                else None
            )
            snap_cc_sig = self._prepared_prompt_snapshot.get("creative_context_signature")
            if snap_cc_sig is None and isinstance(snap_sig, tuple) and len(snap_sig) == 17 and isinstance(snap_sig[16], str) and snap_sig[16].startswith("cc:"):
                snap_cc_sig = snap_sig[16][3:]
            if curr_cc_sig != snap_cc_sig:
                return ""
            return self._prepared_prompt_snapshot.get("prompt_text", "")
        return ""

    def request_kinetic_preparation(
        self,
        intent: str = "copy_kinetic",
        aspect: str = "9:16",
        font: str = "Montserrat",
        context: Optional[Dict[str, Any]] = None,
        cache_mgr: Optional[Any] = None,
        manifest_cache_dir: Optional[str] = None,
        metadata_provider: Optional[Any] = None,
        timeline_fps: Optional[Any] = None,
        **kwargs
    ) -> Optional[str]:
        """Khởi động tiến trình chuẩn bị Kinetic V2 bất đồng bộ trên QThread nền.

        Args:
            intent: 'copy_kinetic' (sao chép prompt), 'auto_plan' (tự động tạo plan) hoặc 'copy_transcript'
            aspect: Tỷ lệ khung hình (mặc định '9:16')
            font: Phông chữ phụ đề (mặc định 'Montserrat')
            context: Ngữ cảnh nguồn (tùy chọn)
            cache_mgr: Dependency injection cho tests
            manifest_cache_dir: Thư mục cache manifest cho tests
            metadata_provider: Hàm cung cấp metadata cho tests
            timeline_fps: FPS mục tiêu (tùy chọn)

        Returns:
            request_id nếu khởi tạo thành công, None nếu bị từ chối
        """
        import uuid
        from src.core.contracts.time_contracts import RationalFPS
        from src.ui.director.workers.kinetic_preparation_worker import (
            KineticPreparationWorker, KineticPreparationRequest
        )

        # 1. Thẩm định chặt chẽ toàn bộ tham số TRƯỚC KHI thực hiện bất kỳ mutation nào
        if intent not in ("copy_kinetic", "auto_plan", "copy_transcript"):
            logger.warning("Intent chuẩn bị kinetic không hợp lệ: %s", intent)
            return None

        if not isinstance(aspect, str) or type(aspect) is bool or not aspect.strip() or aspect.strip() not in ("9:16", "16:9"):
            logger.warning("Aspect không hợp lệ: %r", aspect)
            return None

        if not isinstance(font, str) or type(font) is bool or not font.strip():
            logger.warning("Font không hợp lệ: %r", font)
            return None

        clean_ctx = None
        if context is not None:
            clean_ctx = self._sanitize_and_validate_context(context)
            if clean_ctx is None:
                logger.warning("Context được truyền vào không hợp lệ.")
                return None
            curr_ctx = clean_ctx
        else:
            supplier = getattr(self, "_context_supplier", None)
            if callable(supplier):
                try:
                    raw_ctx = supplier()
                    if not raw_ctx or not isinstance(raw_ctx, dict):
                        return None
                    clean_ctx = self._sanitize_and_validate_context(raw_ctx)
                except Exception as ce:
                    logger.warning("Lỗi khi đọc context từ supplier: %s", ce)
                    return None
            else:
                curr_ctx = self._get_current_context()
                clean_ctx = self._sanitize_and_validate_context(curr_ctx)

            if clean_ctx is None:
                return None
            curr_ctx = clean_ctx

        video_paths = curr_ctx.get("video_paths") or []
        if not video_paths:
            self.lbl_json_hint.setText("⚠ Prompt Kinetic V2 chưa khả dụng khi chưa có tệp video nguồn.")
            self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            return None

        explicit_fps = kwargs.get("timeline_fps", timeline_fps)
        if explicit_fps is not None:
            if isinstance(explicit_fps, bool):
                logger.warning("timeline_fps không thể là boolean: %r", explicit_fps)
                return None
            try:
                rat = RationalFPS.from_value(explicit_fps)
                if rat.as_float() <= 0:
                    return None
                fps_val = rat.as_fraction()
            except Exception as fe:
                logger.warning("timeline_fps không hợp lệ: %s (%s)", explicit_fps, fe)
                return None
        else:
            fps_val = curr_ctx.get("fps") or curr_ctx.get("timeline_fps")
            if fps_val is not None:
                if isinstance(fps_val, bool):
                    return None
                try:
                    rat = RationalFPS.from_value(fps_val)
                    if rat.as_float() <= 0:
                        return None
                    fps_val = rat.as_fraction()
                except Exception as fe:
                    logger.warning("FPS trong context không hợp lệ: %s (%s)", fps_val, fe)
                    return None

        review_cuts = curr_ctx.get("review_cuts")
        if review_cuts is None:
            if getattr(self, "_review_cuts_modified", False) and hasattr(self, "_review_rows"):
                review_cuts = tuple(bool(r[2]) for r in self._review_rows)
            else:
                review_cuts = ()
        rc_mod = getattr(self, "_review_cuts_modified", False)

        # 2. Thẩm định video_plan_data nguyên thủy trước mutation
        raw_plan = getattr(self, "_video_plan_data", None)
        if raw_plan is None and curr_ctx is not None:
            raw_plan = curr_ctx.get("video_plan_data")
        plan_snapshot = None
        if raw_plan is not None:
            if not isinstance(raw_plan, dict):
                logger.warning("video_plan_data phải là dict, nhận được: %s", type(raw_plan))
                return None
            try:
                plan_snapshot = copy.deepcopy(raw_plan)
            except Exception as pe:
                logger.warning("Không thể deepcopy video_plan_data: %s", pe)
                return None
            # Validate plan structure (timeline_segments must be list of dicts)
            segs = plan_snapshot.get("timeline_segments") or plan_snapshot.get("scenes") or plan_snapshot.get("timeline") or plan_snapshot.get("segments")
            if segs is not None:
                if not isinstance(segs, list):
                    return None
                for s in segs:
                    if not isinstance(s, dict):
                        return None
            gh = plan_snapshot.get("global_hook")
            if gh is not None and not isinstance(gh, dict):
                return None
            # Pre-canonicalize plan primitives to ensure valid types, keys, and finite numbers
            try:
                ReviewPage._canonicalize_plan_primitives(plan_snapshot)
            except Exception as ce:
                logger.warning("video_plan_data không thể canonicalize: %s", ce)
                return None

        # 3. Thẩm định manifest_metadata nguyên thủy
        meta_data = None
        manifest = getattr(self, "_manifest", None)
        if manifest is not None and getattr(manifest, "clips", None):
            try:
                manifest_norm = [os.path.normpath(os.path.abspath(c.current_path)) for c in manifest.clips]
                req_norm = [os.path.normpath(os.path.abspath(p)) for p in video_paths]
                if manifest_norm == req_norm and manifest.project_name == curr_ctx.get("project_name", "ChunDVC_Project"):
                    meta_data = {
                        "project_name": manifest.project_name,
                        "manifest_id": manifest.manifest_id,
                        "created_at": getattr(manifest, "created_at", ""),
                        "updated_at": getattr(manifest, "updated_at", ""),
                        "clips": [c.to_dict() for c in manifest.clips],
                    }
            except Exception:
                return None

        primitives = {
            "video_paths": list(video_paths),
            "whisper_model": curr_ctx.get("whisper_model", "small"),
            "whisper_language": curr_ctx.get("whisper_language") or curr_ctx.get("whisper_lang") or "vi",
            "project_name": curr_ctx.get("project_name", "ChunDVC_Project"),
            "review_cuts": review_cuts,
            "review_cuts_modified": rc_mod,
        }
        if fps_val is not None:
            primitives["fps"] = fps_val
            primitives["timeline_fps"] = fps_val

        # 4. Thử khởi tạo KineticPreparationRequest và tính signature trước mutation
        v2_sig = None
        try:
            KineticPreparationRequest(
                request_id="pre_validation_probe",
                generation=0,
                intent=intent,
                context_primitives=primitives,
                manifest_metadata=meta_data,
                video_plan_data=plan_snapshot,
                aspect=aspect,
                font=font,
                max_prompt_bytes=curr_ctx.get("max_prompt_bytes", 32768),
                timeline_fps=fps_val,
            )
            v2_sig = self._extract_v2_context_signature(
                curr_ctx,
                video_plan=plan_snapshot,
                review_cuts=review_cuts,
                review_cuts_modified=rc_mod,
                aspect=aspect,
                font=font,
                intent=intent,
                fps=fps_val,
            )
        except Exception as ve:
            logger.warning("Thẩm định KineticPreparationRequest hoặc v2 signature thất bại: %s", ve)
            return None

        if not v2_sig:
            logger.warning("v2 context signature rỗng, từ chối request.")
            return None

        # 5. TOÀN BỘ VALIDATION ĐÃ ĐẠT: tiến hành hủy worker cũ và thiết lập state mới
        self._stored_validated_context = dict(curr_ctx)

        for w in list(self._kinetic_workers_registry.values()):
            if hasattr(w, "cancel"):
                try:
                    w.cancel()
                except Exception:
                    pass

        self._kinetic_generation += 1
        gen = self._kinetic_generation
        req_id = f"prep_kin_{uuid.uuid4().hex[:8]}_{gen}"
        self._active_kinetic_request_id = req_id
        self._active_kinetic_intent = intent
        self._active_kinetic_aspect = aspect
        self._active_kinetic_signature = v2_sig

        req = KineticPreparationRequest(
            request_id=req_id,
            generation=gen,
            intent=intent,
            context_primitives=primitives,
            manifest_metadata=meta_data,
            video_plan_data=plan_snapshot,
            aspect=aspect,
            font=font,
            max_prompt_bytes=curr_ctx.get("max_prompt_bytes", 32768),
            timeline_fps=fps_val,
        )

        worker = KineticPreparationWorker(
            request=req,
            cache_mgr=cache_mgr,
            manifest_cache_dir=manifest_cache_dir,
            metadata_provider=metadata_provider,
            parent=self,
        )
        self._kinetic_workers_registry[req_id] = worker

        worker.finished_signal.connect(self._on_kinetic_preparation_finished)
        worker.finished.connect(lambda w=worker, r=req_id: self._on_kinetic_worker_thread_finished(r, w))

        # Busy control: Khóa cả 3 nút liên quan tới Kinetic
        if hasattr(self, "btn_copy_kinetic_prompt"):
            self.btn_copy_kinetic_prompt.setEnabled(False)
            if intent == "copy_kinetic":
                self.btn_copy_kinetic_prompt.setText("⏳ Đang chuẩn bị Prompt V2...")
        if hasattr(self, "btn_auto_kinetic"):
            self.btn_auto_kinetic.setEnabled(False)
            if intent == "auto_plan":
                self.btn_auto_kinetic.setText("⏳ Đang tạo kịch bản...")
        if hasattr(self, "btn_copy_final_transcript"):
            self.btn_copy_final_transcript.setEnabled(False)
            if intent == "copy_transcript":
                self.btn_copy_final_transcript.setText("⏳ Đang trích xuất...")

        self.lbl_json_hint.setText("Đang chuẩn bị dữ liệu từ vựng Kinetic (V2) ngoại tuyến...")
        self.lbl_json_hint.setStyleSheet("color: #a78bfa; font-size: 10.5px;")

        worker.start()
        return req_id

    def _on_kinetic_preparation_finished(self, req_id: str, success: bool, message: str, result: dict):
        """Xử lý kết quả chuẩn bị Kinetic V2 logic từ worker nền."""
        logger.info("_on_kinetic_preparation_finished: req_id=%s, success=%s, message=%s", req_id, success, message)
        # 1. Exactly-once: consume kết quả mỗi request đúng 1 lần
        if not hasattr(self, "_consumed_kinetic_request_ids"):
            self._consumed_kinetic_request_ids = set()
        if req_id in self._consumed_kinetic_request_ids:
            logger.warning("Bỏ qua duplicate finished signal cho kinetic request: %s", req_id)
            return
        self._consumed_kinetic_request_ids.add(req_id)

        # 2. Bỏ qua nếu bị supersede bởi generation mới hơn hoặc cửa sổ đang đóng
        if req_id != self._active_kinetic_request_id or getattr(self, "_review_close_pending", False):
            logger.info("Bỏ qua kết quả kinetic cũ hoặc đang đóng: %s (active: %s)", req_id, self._active_kinetic_request_id)
            return

        # 3. Kiểm tra xem ngữ cảnh hiện tại có bị thay đổi trong lúc worker đang chạy không
        curr_ctx = self._get_current_context()
        if not curr_ctx or not isinstance(curr_ctx, dict):
            logger.info("Ngữ cảnh rỗng khi worker hoàn tất, bỏ qua: %s", req_id)
            return

        rc = curr_ctx.get("review_cuts")
        if rc is None and getattr(self, "_review_cuts_modified", False) and hasattr(self, "_review_rows"):
            rc = tuple(bool(r[2]) for r in self._review_rows)

        curr_sig = self._extract_v2_context_signature(
            curr_ctx,
            video_plan=getattr(self, "_video_plan_data", None),
            review_cuts=rc,
            review_cuts_modified=getattr(self, "_review_cuts_modified", False),
            aspect=result.get("aspect", self._active_kinetic_aspect),
            font=result.get("font", "Montserrat"),
            intent=result.get("intent", self._active_kinetic_intent),
            fps=curr_ctx.get("fps") or curr_ctx.get("timeline_fps"),
        )
        if getattr(self, "_active_kinetic_signature", None) is not None:
            if curr_sig != self._active_kinetic_signature:
                logger.info("Bỏ qua kết quả kinetic do ngữ cảnh đã bị thay đổi: %s", req_id)
                self.lbl_json_hint.setText("ℹ Cấu hình đã thay đổi trong lúc xử lý, kết quả cũ đã bị bỏ qua.")
                self.lbl_json_hint.setStyleSheet("color: #a1a1aa; font-size: 10.5px;")
                self._prepared_kinetic_snapshot = None
                return

        # 4. Xử lý thất bại hoặc hủy bỏ
        if not success:
            err_msg = result.get("error") or message
            if message == "cancelled":
                self.lbl_json_hint.setText("ℹ Đã hủy chuẩn bị Kinetic.")
                self.lbl_json_hint.setStyleSheet("color: #a1a1aa; font-size: 10.5px;")
            elif message == "no_dialogue":
                self.lbl_json_hint.setText(f"⚠ {err_msg}")
                self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px;")
            elif message == "word_timestamps_missing":
                self.lbl_json_hint.setText(f"⚠ Không có word timestamps: {err_msg}")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            elif message == "budget_error":
                self.lbl_json_hint.setText(f"⚠ Vượt trần dung lượng: {err_msg}")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            elif message == "edit_plan_required":
                self.lbl_json_hint.setText(f"⚠ {err_msg}")
                self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px;")
            elif message == "unsupported_speed":
                self.lbl_json_hint.setText(f"⚠ {err_msg}")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            else:
                self.lbl_json_hint.setText(f"⚠ {err_msg}")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            return

        # 5. Xử lý thành công
        snapshot_ctx = self._get_current_context()
        snapshot_rc = snapshot_ctx.get("review_cuts")
        if snapshot_rc is None and getattr(self, "_review_cuts_modified", False) and hasattr(self, "_review_rows"):
            snapshot_rc = tuple(bool(r[2]) for r in self._review_rows)

        snapshot_sig = self._extract_v2_context_signature(
            snapshot_ctx,
            video_plan=getattr(self, "_video_plan_data", None),
            review_cuts=snapshot_rc,
            review_cuts_modified=getattr(self, "_review_cuts_modified", False),
            aspect=result.get("aspect", self._active_kinetic_aspect),
            font=result.get("font", "Montserrat"),
            intent=result.get("intent", self._active_kinetic_intent),
            fps=snapshot_ctx.get("fps") or snapshot_ctx.get("timeline_fps"),
        )

        self._prepared_kinetic_snapshot = copy.deepcopy(result)
        self._prepared_kinetic_snapshot["context_signature"] = snapshot_sig
        self._prepared_kinetic_snapshot["aspect"] = result.get("aspect", self._active_kinetic_aspect)

        intent = result.get("intent", self._active_kinetic_intent)
        is_preview = result.get("is_source_preview") or not result.get("is_final_cut", True)
        preview_mode_text = "Xem trước nguồn, chưa có bản dựng" if is_preview else "Bản Dựng Cuối"

        if intent == "copy_kinetic":
            prompt_text = result.get("prompt_text", "")
            if prompt_text:
                try:
                    QApplication.clipboard().setText(prompt_text)
                    if hasattr(self, "btn_copy_kinetic_prompt"):
                        self.btn_copy_kinetic_prompt.setText("✓ Đã sao chép Prompt V2!")
                        target_req = req_id
                        def _reset_k_btn():
                            if getattr(self, "_review_close_pending", False):
                                return
                            if self._active_kinetic_request_id == target_req:
                                if hasattr(self, "btn_copy_kinetic_prompt"):
                                    self.btn_copy_kinetic_prompt.setText("✨ 3. Prompt Chữ Kinetic (V2)")
                        QTimer.singleShot(2500, _reset_k_btn)

                    count = result.get("count", 0)
                    if result.get("mode") == "style_only":
                        self.lbl_json_hint.setText(f"ℹ Video dài ({count} từ). Đã sao chép mẫu phong cách (40/{count} từ). Hãy dùng '⚡ Tự tạo (0đ Token)' để ngắt trang 100% offline!")
                    else:
                        if is_preview:
                            self.lbl_json_hint.setText(f"✓ Đã sao chép Prompt ({count} từ) - {preview_mode_text}.")
                        else:
                            self.lbl_json_hint.setText(f"✓ Đã sao chép Prompt Kinetic Typography ({count} từ) vào Clipboard! Hãy dán (Ctrl+V) vào ChatGPT hoặc Claude.")
                    self.lbl_json_hint.setStyleSheet("color: #a78bfa; font-size: 10.5px; font-weight: bold;")
                    self.prompt_copied.emit("kinetic")
                except Exception as ce:
                    logger.error("Lỗi khi sao chép clipboard: %s", ce)

        elif intent == "auto_plan":
            plan = result.get("plan")
            if plan:
                pages_count = result.get("pages_count", len(plan.get("pages", [])))
                summary = f"Tự động tạo {pages_count} trang Kinetic Typography ({preview_mode_text}, 0đ Token)"
                wrapped = {
                    "type": "kinetic_plan",
                    "count": pages_count,
                    "is_offline": True,
                    "data": plan
                }
                self._apply_json_data(wrapped, summary)
                if is_preview:
                    self.lbl_json_hint.setText(f"✓ Đã tạo {pages_count} trang phụ đề Kinetic ({preview_mode_text} - 0đ Token)!")
                else:
                    self.lbl_json_hint.setText(f"✓ Đã tự động tạo {pages_count} trang phụ đề Kinetic (Offline - 0đ Token)!")
                self.lbl_json_hint.setStyleSheet("color: #a78bfa; font-size: 10.5px; font-weight: bold;")

        elif intent == "copy_transcript":
            transcript_text = result.get("transcript_text", "")
            if transcript_text:
                try:
                    QApplication.clipboard().setText(transcript_text)
                    count = result.get("count", 0)
                    if hasattr(self, "btn_copy_final_transcript"):
                        self.btn_copy_final_transcript.setText(f"✓ Đã copy {count} từ!")
                        target_req = req_id
                        def _reset_t_btn():
                            if getattr(self, "_review_close_pending", False):
                                return
                            if self._active_kinetic_request_id == target_req:
                                if hasattr(self, "btn_copy_final_transcript"):
                                    self.btn_copy_final_transcript.setText("📑 Copy Transcript Bản Dựng")
                        QTimer.singleShot(2500, _reset_t_btn)
                    self.lbl_json_hint.setText(f"✓ Đã copy {count} từ thoại ({preview_mode_text}) vào Clipboard!")
                    self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
                except Exception as ce:
                    logger.error("Lỗi khi sao chép clipboard: %s", ce)

    def get_kinetic_prompt(self, aspect: str = "9:16") -> str:
        """Lấy Prompt Phụ Đề Chữ Kinetic Hoạt Ảnh (V2) đã chuẩn bị in-memory (0 I/O)."""
        snap = getattr(self, "_prepared_kinetic_snapshot", None)
        if snap is None:
            return ""

        if not callable(getattr(self, "_get_current_context", None)):
            return ""

        try:
            curr_ctx = self._get_current_context()
            if not curr_ctx or not isinstance(curr_ctx, dict):
                return ""  # Fail-closed!

            rc = curr_ctx.get("review_cuts")
            if rc is None and getattr(self, "_review_cuts_modified", False) and hasattr(self, "_review_rows"):
                rc = tuple(bool(r[2]) for r in self._review_rows)

            curr_sig = self._extract_v2_context_signature(
                curr_ctx,
                video_plan=getattr(self, "_video_plan_data", None),
                review_cuts=rc,
                review_cuts_modified=getattr(self, "_review_cuts_modified", False),
                aspect=aspect,
                font=snap.get("font", "Montserrat"),
                intent=snap.get("intent", "copy_kinetic"),
                fps=curr_ctx.get("fps") or curr_ctx.get("timeline_fps"),
            )
            if not curr_sig or snap.get("context_signature") != curr_sig:
                return ""

            if snap.get("aspect") != aspect:
                return ""

            return snap.get("prompt_text", "")
        except Exception as e:
            logger.error("Lỗi get_kinetic_prompt: %s", e)
            return ""  # Fail-closed!

    def get_active_prompt(self) -> str:
        """Lấy prompt tùy theo nhu cầu hiện tại."""
        return self.get_video_prompt()

    def _copy_video_prompt(self):
        """Kích hoạt chuẩn bị Prompt V1 bất đồng bộ và sao chép vào clipboard khi hoàn tất."""
        self.request_prompt_preparation(intent="copy_video")

    def get_batch_prompt(self, batch_index: int = 0) -> str:
        """Lấy toàn văn chuỗi prompt của lô đã chuẩn bị in-memory nếu còn hiệu lực."""
        if not self._prepared_batch_snapshot:
            return ""
        curr_ctx = self._get_current_context()
        if not curr_ctx:
            self._invalidate_prepared_batches()
            return ""
        curr_sig = self._extract_context_signature(curr_ctx)
        snap = self._prepared_batch_snapshot
        if isinstance(snap, dict):
            snap_sig = snap.get("ui_context_signature") or snap.get("context_signature")
            snap_cc_sig = snap.get("creative_context_signature")
            batches = snap.get("batches") or []
        else:
            snap_sig = getattr(snap, "ui_context_signature", None) or getattr(snap, "context_signature", None)
            snap_cc_sig = getattr(snap, "creative_context_signature", None)
            batches = getattr(snap, "batches", []) or []

        if curr_sig != snap_sig:
            self._invalidate_prepared_batches()
            return ""

        curr_cc = curr_ctx.get("creative_context")
        curr_cc_sig = (
            compute_creative_context_signature(curr_cc)
            if (curr_cc is not None and curr_cc != CreativeContext())
            else None
        )
        if snap_cc_sig is None and isinstance(snap_sig, tuple) and len(snap_sig) == 17 and isinstance(snap_sig[16], str) and snap_sig[16].startswith("cc:"):
            snap_cc_sig = snap_sig[16][3:]
        if curr_cc_sig != snap_cc_sig:
            self._invalidate_prepared_batches()
            return ""

        if 0 <= batch_index < len(batches):
            b = batches[batch_index]
            if isinstance(b, dict):
                return b.get("rendered_prompt", "")
            return getattr(b, "rendered_prompt", "")
        return ""

    def _on_prepare_batches_clicked(self):
        """Kích hoạt phân lô kịch bản an toàn theo dung lượng tối đa."""
        self.request_prompt_preparation(intent="prepare_batches")

    def _on_copy_selected_batch_clicked(self):
        """Sao chép kịch bản của lô đang chọn vào Clipboard sau khi recheck nguồn."""
        if not self._prepared_batch_snapshot:
            self.lbl_json_hint.setText("⚠ Vui lòng bấm 'Phân Lô Kịch Bản' trước khi sao chép.")
            self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px;")
            return
        sel_idx = self.cbo_batch_selector.currentIndex()
        if sel_idx < 0:
            return
        self.request_prompt_preparation(intent="copy_batch", selected_batch_index=sel_idx)

    def _on_batch_selection_changed(self, index: int):
        """Cập nhật preview và hiển thị trạng thái bản thảo khi chuyển đổi lô trên selector."""
        if index >= 0:
            self._update_batch_preview(index)
            # Cập nhật hiển thị trạng thái bản thảo của lô đang chọn nếu đã có trong ledger
            if self._prepared_batch_snapshot and hasattr(self, "_batch_drafts_ledger"):
                norm_snap = self._prepared_batch_snapshot
                snapshot_id = getattr(norm_snap, "snapshot_id", None) or (norm_snap.get("snapshot_id") if isinstance(norm_snap, dict) else "")
                batches = getattr(norm_snap, "batches", None) or (norm_snap.get("batches") if isinstance(norm_snap, dict) else [])
                if 0 <= index < len(batches):
                    b = batches[index]
                    b_id = getattr(b, "batch_id", None) or (b.get("batch_id") if isinstance(b, dict) else "")
                    entry = self._batch_drafts_ledger.get((snapshot_id, b_id))
                    if entry is not None and hasattr(self, "lbl_batch_draft_status"):
                        self.lbl_batch_draft_status.setText(
                            f"✓ Lô {index + 1} đã có bản thảo (Digest: {entry.draft_digest[:8]}..., {entry.total_cuts_count} đoạn, {entry.total_selected_duration_sec:.1f}s)."
                        )
                        self.lbl_batch_draft_status.setStyleSheet("color: #4ade80; font-size: 10px;")
                    elif hasattr(self, "lbl_batch_draft_status"):
                        self.lbl_batch_draft_status.setText("")

    def _on_submit_batch_draft_clicked(self):
        """Tiếp nhận và gửi bản thảo phân lô đi thẩm định hình học bất đồng bộ."""
        raw_text = self.txt_batch_draft_input.toPlainText().strip()
        if not raw_text:
            self.lbl_batch_draft_status.setText("⚠ Vui lòng dán mã JSON bản thảo trước khi nạp.")
            self.lbl_batch_draft_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
            return

        text_bytes = raw_text.encode("utf-8")
        if len(text_bytes) > 65536:
            self.lbl_batch_draft_status.setText(f"⚠ Dung lượng bản thảo ({len(text_bytes)} bytes) vượt quá 65,536 bytes (64KiB).")
            self.lbl_batch_draft_status.setStyleSheet("color: #ef4444; font-size: 10px;")
            return

        if not self._prepared_batch_snapshot:
            self.lbl_batch_draft_status.setText("⚠ Vui lòng bấm 'Phân Lô Kịch Bản' trước khi nạp bản thảo.")
            self.lbl_batch_draft_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
            return

        from src.ui.director.workers.prompt_preparation_worker import validate_and_normalize_snapshot
        norm_snap = validate_and_normalize_snapshot(self._prepared_batch_snapshot)
        if norm_snap is None:
            self.lbl_batch_draft_status.setText("⚠ Snapshot phân lô không hợp lệ. Vui lòng phân lô lại.")
            self.lbl_batch_draft_status.setStyleSheet("color: #ef4444; font-size: 10px;")
            return

        sel_idx = self.cbo_batch_selector.currentIndex()
        if sel_idx < 0 or sel_idx >= len(norm_snap.batches):
            self.lbl_batch_draft_status.setText("⚠ Chỉ số lô không hợp lệ.")
            self.lbl_batch_draft_status.setStyleSheet("color: #ef4444; font-size: 10px;")
            return

        selected_b = norm_snap.batches[sel_idx]
        batch_id = selected_b.batch_id
        snapshot_id = norm_snap.snapshot_id

        # Kiểm tra xem lô đã có bản thảo chưa: nếu có, ghi nhớ digest để kiểm tra race condition
        existing_entry = self._batch_drafts_ledger.get((snapshot_id, batch_id))
        expected_old_digest = existing_entry.draft_digest if existing_entry is not None else None

        allow_replace = False
        if hasattr(self, "chk_replace_existing_draft"):
            allow_replace = self.chk_replace_existing_draft.isChecked()

        self.request_prompt_preparation(
            intent="validate_batch_draft",
            selected_batch_index=sel_idx,
            batch_draft_text=raw_text,
            associate_with_current_snapshot=True,
            expected_existing_draft_digest=expected_old_digest,
            allow_replace_draft=allow_replace,
        )

    def _update_batch_preview(self, index: int):
        """Hiển thị nội dung xem trước của lô được chọn (toàn văn không crop dòng)."""
        if not self._prepared_batch_snapshot:
            self.txt_batch_preview.clear()
            self.txt_batch_preview.setVisible(False)
            return
        batches = getattr(self._prepared_batch_snapshot, "batches", None) or (
            self._prepared_batch_snapshot.get("batches") if isinstance(self._prepared_batch_snapshot, dict) else []
        )
        if 0 <= index < len(batches):
            b = batches[index]
            p_text = getattr(b, "rendered_prompt", None) or (b.get("rendered_prompt", "") if isinstance(b, dict) else "")
            p_bytes = getattr(b, "rendered_bytes", None) or (b.get("rendered_bytes") if isinstance(b, dict) else len(p_text.encode("utf-8")))
            n_items = getattr(b, "items_count", None) or (b.get("items_count") if isinstance(b, dict) else 0)
            d_clips = getattr(b, "distinct_clip_count", None) or (b.get("distinct_clip_count", 0) if isinstance(b, dict) else 0)
            b_idx = getattr(b, "batch_index", None) or (b.get("batch_index", index) if isinstance(b, dict) else index)
            preview_header = f"--- [Lô {b_idx + 1}/{len(batches)} | {d_clips} clips ({n_items} đoạn) | {p_bytes:,} B] ---\n"
            self.txt_batch_preview.setPlainText(preview_header + p_text)
            self.txt_batch_preview.setVisible(True)
        else:
            self.txt_batch_preview.clear()

    def _copy_kinetic_prompt(self):
        """Kích hoạt chuẩn bị Prompt V2 bất đồng bộ hoặc sao chép ngay nếu đã sẵn sàng."""
        aspect = "9:16"
        ready_prompt = self.get_kinetic_prompt(aspect=aspect)
        if ready_prompt:
            try:
                QApplication.clipboard().setText(ready_prompt)
                if hasattr(self, "btn_copy_kinetic_prompt"):
                    self.btn_copy_kinetic_prompt.setText("✓ Đã sao chép Prompt V2!")
                    orig_btn_text = "✨ 3. Prompt Chữ Kinetic (V2)"
                    QTimer.singleShot(2500, lambda: self.btn_copy_kinetic_prompt.setText(orig_btn_text) if not getattr(self, "_review_close_pending", False) else None)
                self.lbl_json_hint.setText("✓ Đã sao chép Prompt Kinetic Typography (V2) vào Clipboard!")
                self.lbl_json_hint.setStyleSheet("color: #a78bfa; font-size: 10.5px; font-weight: bold;")
                self.prompt_copied.emit("kinetic")
                return
            except Exception as ce:
                logger.error("Lỗi khi ghi clipboard: %s", ce)

        req = self.request_kinetic_preparation(intent="copy_kinetic", aspect=aspect)
        if req is None:
            self.lbl_json_hint.setText("⚠ Prompt Kinetic V2 chưa khả dụng khi chưa có dữ liệu video nguồn.")
            self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")

    def _get_current_target_slot(self) -> Optional[str]:
        if hasattr(self, "btn_target_slot1") and self.btn_target_slot1.isChecked():
            return "slot1"
        if hasattr(self, "btn_target_slot2") and self.btn_target_slot2.isChecked():
            return "slot2"
        return None

    def _on_target_slot_changed(self, btn_id: int):
        if self.txt_json_input.toPlainText().strip():
            self._on_json_text_changed()

    def _paste_to_slot(self, slot: str):
        """Dán trực tiếp nội dung từ Clipboard vào riêng một Slot (Slot 1 hoặc Slot 2)."""
        cb_text = QApplication.clipboard().text().strip()
        if not cb_text:
            self.lbl_json_hint.setText("⚠ Clipboard rỗng. Hãy copy mã JSON từ ChatGPT / Claude trước.")
            self.lbl_json_hint.setStyleSheet("color: #fbbf24; font-size: 10.5px;")
            return

        ok, msg, data = parse_web_prompt_json(cb_text, target_slot=slot)
        if not ok or not data:
            self.lbl_json_hint.setText(f"✕ Không thể nạp vào {slot.upper()}: {msg}")
            self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            return

        self.txt_json_input.setPlainText(cb_text)
        self.paste_zone.set_success(msg, data)
        self.lbl_json_hint.setText(f"✓ Đã nạp thành công vào {slot.upper()}: {msg}")
        self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
        self._apply_json_data(data, msg)

    def _on_json_text_changed(self):
        text = self.txt_json_input.toPlainText().strip()
        if not text:
            return
        slot = self._get_current_target_slot()
        ok, msg, data = parse_web_prompt_json(text, target_slot=slot)
        if ok and data:
            self.paste_zone.set_success(msg, data)
            self.lbl_json_hint.setText(f"✓ {msg}")
            self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
        else:
            self.lbl_json_hint.setText(f"⚠ {msg}")
            self.lbl_json_hint.setStyleSheet("color: #fbbf24; font-size: 10.5px;")

    def _on_dropzone_json_recognized(self, data: dict, summary: str):
        if contains_project_overview_draft(data):
            self.lbl_json_hint.setText(f"⚠ {PROJECT_OVERVIEW_REJECT_MSG}")
            self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px; font-weight: bold;")
            if hasattr(self, "paste_zone") and self.paste_zone:
                try:
                    self.paste_zone.set_error(PROJECT_OVERVIEW_REJECT_MSG)
                except Exception:
                    pass
            raw_dict = data.get("data", {}) if isinstance(data.get("data"), dict) else data
            if isinstance(raw_dict, dict) and raw_dict:
                self.txt_json_input.setPlainText(json.dumps(raw_dict, indent=2, ensure_ascii=False))
            return

        if contains_batch_draft_plan(data):
            self.lbl_json_hint.setText(f"⚠ {BATCH_DRAFT_REJECT_MSG}")
            self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px; font-weight: bold;")
            if hasattr(self, "paste_zone") and self.paste_zone:
                try:
                    self.paste_zone.set_error(BATCH_DRAFT_REJECT_MSG)
                except Exception:
                    pass
            raw_dict = data.get("data", {}) if isinstance(data.get("data"), dict) else data
            if isinstance(raw_dict, dict) and raw_dict:
                self.txt_json_input.setPlainText(json.dumps(raw_dict, indent=2, ensure_ascii=False))
            return

        self._pasted_data = data
        self._pasted_summary = summary
        raw_dict = data.get("data", {})
        self.txt_json_input.setPlainText(json.dumps(raw_dict, indent=2, ensure_ascii=False))
        self.lbl_json_hint.setText(f"✓ {summary}")
        self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
        self._apply_json_data(data, summary)

    def _apply_json_script(self):
        text = self.txt_json_input.toPlainText().strip()
        if not text:
            # Nếu ô nhập trống, thử lấy từ clipboard
            cb_text = QApplication.clipboard().text().strip()
            if cb_text:
                text = cb_text
                self.txt_json_input.setPlainText(text)

        slot = self._get_current_target_slot()
        ok, msg, data = parse_web_prompt_json(text, target_slot=slot)
        if not ok or not data:
            self.lbl_json_hint.setText(f"✕ Không thể áp dụng: {msg}")
            self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            return

        self._pasted_data = data
        self._pasted_summary = msg
        self.paste_zone.set_success(msg, data)
        self.lbl_json_hint.setText(f"✓ Đã áp dụng thành công: {msg}")
        self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
        self._apply_json_data(data, msg)

    def _apply_json_data(self, data: dict, summary: str):
        """Phân loại và nạp độc lập vào Slot 1 (Video Plan) hoặc Slot 2 (Kinetic Plan) hoặc CẢ HAI (dual_plan)."""
        # Project overview draft guard: Chặn tuyệt đối mọi mutation vào Slot 1 / Slot 2 khi dữ liệu là Macro Overview
        if contains_project_overview_draft(data):
            self.lbl_json_hint.setText(f"⚠ {PROJECT_OVERVIEW_REJECT_MSG}")
            self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px; font-weight: bold;")
            if hasattr(self, "paste_zone") and self.paste_zone:
                try:
                    self.paste_zone.set_error(PROJECT_OVERVIEW_REJECT_MSG)
                except Exception:
                    pass
            return

        # Batch draft guard: Chặn tuyệt đối mọi mutation vào Slot 1 / Slot 2 khi dữ liệu là Batch Draft
        if contains_batch_draft_plan(data):
            self.lbl_json_hint.setText(f"⚠ {BATCH_DRAFT_REJECT_MSG}")
            self.lbl_json_hint.setStyleSheet("color: #f59e0b; font-size: 10.5px; font-weight: bold;")
            if hasattr(self, "paste_zone") and self.paste_zone:
                try:
                    self.paste_zone.set_error(BATCH_DRAFT_REJECT_MSG)
                except Exception:
                    pass
            return

        ptype = data.get("type", "generic_plan")
        raw_plan = data.get("data", {})

        # Kiểm tra manifest_id đối soát an toàn
        target_dict = data.get("video_plan", {}) if ptype == "dual_plan" else (raw_plan if isinstance(raw_plan, dict) and raw_plan else data)
        plan_manifest_id = target_dict.get("manifest_id") if isinstance(target_dict, dict) else None

        # Đóng đường get_or_create GUI nếu prompt đã prepare (hoặc đã có manifest) hoặc nếu type là kinetic_plan
        has_prepared = (self._prepared_prompt_snapshot is not None) or (self._prepared_kinetic_snapshot is not None)
        if ptype in ("video_plan", "dual_plan", "generic_plan") and not getattr(self, "_manifest", None) and not has_prepared and getattr(self, "_clip_infos", None):
            try:
                from src.core.clip_manifest import ClipManifest
                raw_paths = [getattr(c, "path", c) if not isinstance(c, str) else c for c in self._clip_infos]
                self._manifest = ClipManifest.get_or_create(raw_paths, project_name="ChunDVC_Project")
            except Exception as e:
                logger.warning(f"Không thể khởi tạo ClipManifest trong _apply_json_data: {e}")

        if getattr(self, "_manifest", None) and ptype in ("video_plan", "dual_plan", "generic_plan"):
            curr_id = self._manifest.manifest_id
            if plan_manifest_id and plan_manifest_id != curr_id:
                logger.warning("Lệch Manifest ID: plan=%s, manifest=%s. Từ chối áp dụng kịch bản.", plan_manifest_id, curr_id)
                self.lbl_json_hint.setText(f"✕ Từ chối kịch bản: Lệch Manifest ID ({plan_manifest_id} != {curr_id}).")
                self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px; font-weight: bold;")
                self._video_plan_data = None
                self._cancel_matching_workers()
                if hasattr(self, "btn_match_assets"):
                    self.btn_match_assets.setEnabled(False)
                if hasattr(self, "lbl_matching_status"):
                    self.lbl_matching_status.setText("✕ Kịch bản bị từ chối")
                    self.lbl_matching_status.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
                if not getattr(self, "_headless_mode", False):
                    try:
                        reply = QMessageBox.critical(
                            self,
                            "Lệch Manifest ID - Nguy cơ dựng sai clip!",
                            f"⚠ CẢNH BÁO: Kịch bản AI được sinh từ danh sách clip khác!\n\n"
                            f"- Manifest ID trong kịch bản: {plan_manifest_id}\n"
                            f"- Manifest ID dự án hiện tại: {curr_id}\n\n"
                            f"Nếu tiếp tục, DaVinci Resolve sẽ cắt nhầm clip. Bạn có muốn tạo lại Prompt mới theo danh sách clip hiện tại không?",
                            QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
                            QMessageBox.StandardButton.Cancel
                        )
                        if reply == QMessageBox.StandardButton.Retry:
                            self._copy_video_prompt()
                    except Exception:
                        pass
                return
            elif not plan_manifest_id:
                logger.warning("Kịch bản cũ không có manifest_id, có thể dựng nhầm clip nếu thứ tự file đổi.")
                self.lbl_json_hint.setText("⚠ Cảnh báo: Kịch bản cũ không có manifest_id, có thể lệch clip.")
                self.lbl_json_hint.setStyleSheet("color: #eab308; font-size: 10.5px; font-weight: bold;")

        if ptype == "dual_plan":
            v_plan = data.get("video_plan", {})
            k_plan = data.get("kinetic_plan", {})
            v_sum = data.get("video_summary", "✓ Đã nạp Video Plan (V1)")
            k_sum = data.get("kinetic_summary", "✓ Đã nạp Kinetic Plan (V2)")

            self._video_plan_data = v_plan
            self._video_plan_summary = v_sum
            self._on_video_plan_changed()
            self.lbl_slot_vid_status.setText(v_sum)
            self.lbl_slot_vid_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
            self.card_slot_video.setStyleSheet("background: rgba(56, 189, 248, 0.12); border: 1px solid #38bdf8; border-radius: 6px; padding: 4px 6px;")

            self._kinetic_plan_data = k_plan
            self._kinetic_plan_summary = k_sum
            self.lbl_slot_kin_status.setText(k_sum)
            self.lbl_slot_kin_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
            self.card_slot_kinetic.setStyleSheet("background: rgba(245, 158, 11, 0.12); border: 1px solid #f59e0b; border-radius: 4px; padding: 4px 6px;")

            scenes = v_plan.get("timeline_segments") or v_plan.get("scenes") or v_plan.get("timeline") or v_plan.get("segments") or []
            if scenes:
                blocks = []
                for i, sc in enumerate(scenes[:8]):
                    title = sc.get("title") or sc.get("hook_title") or sc.get("chapter_name") or f"Cảnh {i+1}"
                    color = "#d94f3d" if "hook" in str(title).lower() else ("#0284c7" if i % 2 == 0 else "#06b6d4")
                    blocks.append({"label": title, "color": color, "flex": 1})
                self.mini_timeline.set_blocks(blocks)

        elif ptype == "video_plan":
            self._video_plan_data = raw_plan
            self._video_plan_summary = summary
            self._on_video_plan_changed()
            cnt = data.get("count", 0)
            br_cnt = data.get("broll_count", 0)
            sfx_cnt = data.get("sfx_count", 0)
            self.lbl_slot_vid_status.setText(f"✓ Đã nạp ({cnt} cảnh · {br_cnt} meme · {sfx_cnt} sfx)")
            self.lbl_slot_vid_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
            self.card_slot_video.setStyleSheet("background: rgba(56, 189, 248, 0.12); border: 1px solid #38bdf8; border-radius: 4px; padding: 4px 6px;")

            scenes = raw_plan.get("timeline_segments") or raw_plan.get("scenes") or raw_plan.get("timeline") or raw_plan.get("segments") or []
            if scenes:
                blocks = []
                for i, sc in enumerate(scenes[:8]):
                    title = sc.get("title") or sc.get("hook_title") or sc.get("chapter_name") or f"Cảnh {i+1}"
                    color = "#d94f3d" if "hook" in str(title).lower() else ("#0284c7" if i % 2 == 0 else "#06b6d4")
                    blocks.append({"label": title, "color": color, "flex": 1})
                self.mini_timeline.set_blocks(blocks)

        elif ptype == "kinetic_plan":
            self._kinetic_plan_data = raw_plan
            self._kinetic_plan_summary = summary
            cnt = data.get("count", 0)
            if "0đ token" in summary.lower() or data.get("is_offline"):
                self.lbl_slot_kin_status.setText(f"✓ Đã nạp ({cnt} trang Kinetic · 0đ Token)")
            else:
                self.lbl_slot_kin_status.setText(f"✓ Đã nạp ({cnt} phụ đề động)")
            self.lbl_slot_kin_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
            self.card_slot_kinetic.setStyleSheet("background: rgba(245, 158, 11, 0.12); border: 1px solid #f59e0b; border-radius: 4px; padding: 4px 6px;")

        else:
            self._video_plan_data = raw_plan
            self._video_plan_summary = summary
            self._on_video_plan_changed()
            self.lbl_slot_vid_status.setText("✓ Đã nạp kịch bản tùy biến")
            self.lbl_slot_vid_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")

        self.config_changed.emit()
        self._update_source_evidence_button_state()
        self.json_applied.emit(data, summary)
        if getattr(self, "_manifest", None) and getattr(self, "_video_plan_data", None):
            try:
                self._rebuild_and_present_proposal()
            except Exception as pe:
                logger.debug("Auto-rebuild proposal after json apply: %s", pe)

    def _on_run_cloud_clicked(self):
        self.lbl_cloud_status.setText("Đang gửi transcript sang Cloud AI...")
        self.lbl_cloud_status.setStyleSheet("color: #a78bfa; font-size: 11px;")
        self.ai_run_requested.emit()

    def _on_run_ollama_clicked(self):
        self.lbl_ollama_status.setText("Đang gọi Local Ollama xử lý kịch bản...")
        self.lbl_ollama_status.setStyleSheet("color: #a78bfa; font-size: 11px;")
        self.ai_run_requested.emit()

    def set_ai_result(self, ok: bool, message: str, raw_json: str = ""):
        target_lbl = self.lbl_ollama_status if self.ai_stack.currentIndex() == 2 else self.lbl_cloud_status
        if ok:
            target_lbl.setText(message or "✓ Kịch bản AI đã hoàn thành!")
            target_lbl.setStyleSheet("color: #4ade80; font-size: 11px; font-weight: bold;")
            if raw_json and raw_json.strip():
                self.txt_json_input.setPlainText(raw_json)
                self._apply_json_script()
        else:
            target_lbl.setText(f"Lỗi: {message}")
            target_lbl.setStyleSheet("color: #ef4444; font-size: 11px;")

    def _on_row_toggled(self, idx: int):
        if 0 <= idx < len(self._review_rows):
            cur = self._review_rows[idx][2]
            new_val = 0 if cur else 1
            self._review_rows[idx][2] = new_val
            self._review_cuts_modified = True
            if idx < len(self._row_widgets):
                self._row_widgets[idx].set_cut(bool(new_val))
            self.config_changed.emit()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Z and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            for r in reversed(self._review_rows):
                if r[2] == 1:
                    r[2] = 0
                    self._review_cuts_modified = True
                    break
            for i, r in enumerate(self._review_rows):
                if i < len(self._row_widgets):
                    self._row_widgets[i].set_cut(bool(r[2]))
            self.config_changed.emit()
        else:
            super().keyPressEvent(event)

    def load_from_config(self, config: DirectorConfig):
        # Chọn mode phù hợp
        if config.ai_engine_mode == "ollama":
            self._switch_mode(2)
        elif config.ai_engine_mode == "cloud":
            self._switch_mode(1)
        else:
            self._switch_mode(0)

        # Provider
        idx = self.combo_cloud_provider.findData(config.cloud_provider)
        if idx >= 0:
            self.combo_cloud_provider.setCurrentIndex(idx)

        # API key
        if config.api_key:
            self.txt_cloud_api_key.setText(config.api_key)

        # Ollama
        if config.ollama_model:
            self.combo_ollama_model.setCurrentText(config.ollama_model)
        if config.ollama_endpoint:
            self.txt_ollama_endpoint.setText(config.ollama_endpoint)

        # Thẩm định an toàn toàn bộ các ứng viên plan trong config:
        # Nếu bất kỳ ứng viên nào là macro overview hoặc batch draft, giữ nguyên toàn bộ cả 2 slots
        # và plan bookkeeping last-good hiện tại, tuyệt đối không half-restore một plan khác.
        plan_candidates = [
            config.web_prompt_pasted_data,
            getattr(config, "video_plan_data", None),
            getattr(config, "kinetic_plan_data", None),
        ]
        has_invalid_plan = any(
            c is not None and (contains_project_overview_draft(c) or contains_batch_draft_plan(c))
            for c in plan_candidates
        )

        if not has_invalid_plan:
            # Pasted data
            if config.web_prompt_pasted_data:
                self._pasted_data = config.web_prompt_pasted_data
                self._pasted_summary = config.web_prompt_summary
                self.paste_zone.set_success(self._pasted_summary, self._pasted_data)
                self.txt_json_input.setPlainText(json.dumps(self._pasted_data.get("data", {}), indent=2, ensure_ascii=False))

            # Khôi phục Dual Plan slots nếu có sẵn trong config
            if getattr(config, "video_plan_data", None):
                self._video_plan_data = config.video_plan_data
                self._video_plan_summary = getattr(config, "video_plan_summary", "") or "Kịch bản mạch video & B-Roll / SFX"
                self.card_slot_video.setStyleSheet("background: rgba(56, 189, 248, 0.12); border: 1px solid #38bdf8; border-radius: 6px; padding: 4px 8px;")
                self.lbl_slot_vid_status.setText(f"✓ Đã nạp: {self._video_plan_summary}")
                self.lbl_slot_vid_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
                self._on_video_plan_changed()

            if getattr(config, "kinetic_plan_data", None):
                self._kinetic_plan_data = config.kinetic_plan_data
                self._kinetic_plan_summary = getattr(config, "kinetic_plan_summary", "") or "Kịch bản phụ đề Kinetic Typography"
                self.card_slot_kinetic.setStyleSheet("background: rgba(167, 139, 250, 0.12); border: 1px solid #a78bfa; border-radius: 6px; padding: 4px 8px;")
                self.lbl_slot_kin_status.setText(f"✓ Đã nạp: {self._kinetic_plan_summary}")
                self.lbl_slot_kin_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")

    def save_to_config(self, config: DirectorConfig):
        curr_idx = self.ai_stack.currentIndex()
        if curr_idx == 1:
            config.ai_engine_mode = "cloud"
        elif curr_idx == 2:
            config.ai_engine_mode = "ollama"
        else:
            config.ai_engine_mode = "web"

        config.cloud_provider = self.combo_cloud_provider.currentData() or "gemini"
        config.api_key = self.txt_cloud_api_key.text().strip() or None
        config.ollama_model = self.combo_ollama_model.currentText().strip() or "llama3:latest"
        config.ollama_endpoint = self.txt_ollama_endpoint.text().strip() or "http://localhost:11434"
        config.web_prompt_pasted_data = self._pasted_data
        config.web_prompt_summary = self._pasted_summary
        config.video_plan_data = self._video_plan_data
        config.video_plan_summary = self._video_plan_summary
        config.kinetic_plan_data = self._kinetic_plan_data
        config.kinetic_plan_summary = self._kinetic_plan_summary

    # -------------------------------------------------------------------------
    # Đề xuất kịch bản toàn bản & Phê duyệt biên tập (G06-06C3-S3)
    # -------------------------------------------------------------------------

    def _build_current_input_fingerprint(
        self,
        curr_ctx: Optional[Mapping[str, Any]] = None,
        include_proof: bool = True,
    ) -> Dict[str, Any]:
        """Chụp snapshot/fingerprint in-memory của toàn bộ dữ liệu đầu vào có thẩm quyền hiện hành."""
        if curr_ctx is None:
            curr_ctx = self._get_current_context()
        if not curr_ctx or not isinstance(curr_ctx, Mapping):
            raise ValueError("ERR_UNAVAILABLE_CONTEXT: Ngữ cảnh supplier rỗng hoặc không khả dụng.")

        # 1. Thẩm định tập active video_paths (bảo toàn thứ tự/chuỗi, cấm duplicate/empty/silent trim)
        raw_paths = curr_ctx.get("video_paths")
        if raw_paths is None or not isinstance(raw_paths, (list, tuple)):
            raise ValueError("ERR_INVALID_SOURCE_PATHS: video_paths phải là list hoặc tuple.")
        clean_paths: List[str] = []
        for p in raw_paths:
            if not isinstance(p, str) or not p:
                raise ValueError(f"ERR_INVALID_SOURCE_PATH: video_path không hợp lệ: {p!r}")
            clean_paths.append(p)
        if len(set(clean_paths)) != len(clean_paths):
            raise ValueError("ERR_DUPLICATE_SOURCE_PATH: video_paths chứa đường dẫn trùng lặp.")

        # 2. Thẩm định manifest và đối chiếu chính xác tập nguồn
        manifest = getattr(self, "_manifest", None)
        if manifest is None:
            raise ValueError("ERR_MISSING_MANIFEST: manifest chưa được nạp.")
        if isinstance(manifest, Mapping):
            m_id = manifest.get("manifest_id")
            m_clips = manifest.get("clips", ())
        else:
            m_id = getattr(manifest, "manifest_id", None)
            m_clips = getattr(manifest, "clips", ())

        if not m_id or not isinstance(m_id, str):
            raise ValueError(f"ERR_INVALID_MANIFEST_ID: manifest_id không hợp lệ: {m_id!r}")
        if not isinstance(m_clips, (list, tuple)):
            raise ValueError("ERR_INVALID_MANIFEST_CLIPS: manifest.clips phải là list hoặc tuple.")

        # Thẩm định supplier manifest_id nếu được cung cấp nonblank
        supplier_manifest_id = curr_ctx.get("manifest_id")
        if supplier_manifest_id is not None:
            if not isinstance(supplier_manifest_id, str):
                raise ValueError(f"ERR_INVALID_SUPPLIER_MANIFEST_ID: supplier manifest_id không phải str: {type(supplier_manifest_id).__name__}")
            if supplier_manifest_id.strip() and supplier_manifest_id != m_id:
                raise ValueError(
                    f"ERR_MANIFEST_ID_MISMATCH: supplier manifest_id {supplier_manifest_id!r} "
                    f"không khớp loaded manifest_id {m_id!r}."
                )

        manifest_paths_ordered: List[str] = []
        manifest_clip_tuples: List[Tuple[Any, ...]] = []
        for c in m_clips:
            cid = c.get("clip_id") if isinstance(c, Mapping) else getattr(c, "clip_id", None)
            cpath = (c.get("current_path") or c.get("file_path")) if isinstance(c, Mapping) else (getattr(c, "current_path", None) or getattr(c, "file_path", None))
            cfname = c.get("file_name") if isinstance(c, Mapping) else getattr(c, "file_name", "")
            cdur = c.get("duration_sec") if isinstance(c, Mapping) else getattr(c, "duration_sec", None)
            cfps = c.get("fps") if isinstance(c, Mapping) else getattr(c, "fps", None)

            if not cid or not isinstance(cid, str):
                raise ValueError(f"ERR_INVALID_CLIP_ID: clip_id không hợp lệ trong manifest: {cid!r}")
            if not cpath or not isinstance(cpath, str):
                raise ValueError(f"ERR_INVALID_CLIP_PATH: clip_path không hợp lệ cho clip '{cid}': {cpath!r}")

            manifest_paths_ordered.append(cpath)
            fps_repr = (cfps.numerator, cfps.denominator) if hasattr(cfps, "numerator") and hasattr(cfps, "denominator") else str(cfps)
            manifest_clip_tuples.append((cid, cfname, cpath, cdur, fps_repr))

        # RÀNG BUỘC CHẶT CHẼ: Tập active source paths phải khớp CHÍNH XÁC tập loaded manifest source paths
        if set(clean_paths) != set(manifest_paths_ordered):
            raise ValueError(
                f"ERR_SOURCE_PATHS_MISMATCH: Tập video_paths hiện hành ({len(clean_paths)} đường dẫn) "
                f"không khớp chính xác tập loaded manifest source ({len(manifest_paths_ordered)} đường dẫn)."
            )

        # 3. Creative context
        raw_creative = curr_ctx.get("creative_context")
        c_ctx_obj = normalize_creative_context(raw_creative if raw_creative is not None else {})
        c_ctx_dict = creative_context_to_dict(c_ctx_obj)

        # 4. Slot 1 video_plan & Slot 2 kinetic_plan (không dùng default=str)
        vp = getattr(self, "_video_plan_data", None)
        if not vp or not isinstance(vp, Mapping):
            raise ValueError("ERR_MISSING_VIDEO_PLAN: video_plan_data chưa được nạp hoặc không hợp lệ.")
        kp = getattr(self, "_kinetic_plan_data", None)
        vp_repr = json.dumps(vp, sort_keys=True)
        kp_repr = json.dumps(kp, sort_keys=True) if kp else None

        # 5. Matching options & Sidecar state (chụp toàn bộ 12 fields demands và candidates)
        sidecar = getattr(self, "_last_good_matching_sidecar", None)
        sidecar_manifest_id = getattr(sidecar, "manifest_id", None) if sidecar is not None else None
        sidecar_rev = getattr(sidecar, "plan_revision", None) if sidecar is not None else None

        if sidecar is not None and sidecar_manifest_id and sidecar_manifest_id != m_id:
            raise ValueError(
                f"ERR_MANIFEST_ID_MISMATCH: sidecar manifest_id {sidecar_manifest_id!r} "
                f"không khớp loaded manifest_id {m_id!r}."
            )

        sidecar_entries_summary: List[Tuple[Any, ...]] = []
        if sidecar is not None and getattr(sidecar, "entries", None):
            for e in sidecar.entries:
                d = e.demand
                d_tuple = (
                    d.demand_id,
                    d.source_slot,
                    d.source_index,
                    d.status,
                    d.raw_type,
                    d.category,
                    d.timeline_sec,
                    d.desired_duration_sec,
                    d.raw_query_hint if isinstance(d.raw_query_hint, str) else repr(d.raw_query_hint),
                    d.query_text,
                    d.query_source,
                    d.rejection_reason,
                )
                res = e.result
                res_tuple = (
                    getattr(res, "request_id", ""),
                    getattr(res, "is_matched", False),
                    getattr(res, "category", ""),
                    getattr(res, "query_text", ""),
                    getattr(res, "unmatched_reason", None),
                    getattr(res, "suggested_action", None),
                    tuple(getattr(res, "warnings", ()) or ()),
                    getattr(res, "rejected_record_count", 0),
                )
                candidates_tuple: List[Tuple[Any, ...]] = []
                for c in getattr(e, "candidates", ()):
                    candidates_tuple.append((
                        getattr(c, "asset_id", None),
                        getattr(c, "name", None),
                        getattr(c, "category", None),
                        getattr(c, "subcategory", None),
                        getattr(c, "source_kind", None),
                        getattr(c, "availability", None),
                        getattr(c, "match_score", None),
                        tuple(getattr(c, "matched_signals", ()) or ()),
                        getattr(c, "duration_sec", None),
                        getattr(c, "pack_id", None),
                        getattr(c, "pack_name", None),
                        getattr(c, "source_id", None),
                        getattr(c, "origin_library_id", None),
                        getattr(c, "template_type", None),
                        getattr(c, "is_native_guidance", False),
                        getattr(c, "requires_reconnect", False),
                    ))
                sidecar_entries_summary.append((d_tuple, res_tuple, tuple(candidates_tuple)))

        # 6. User candidate choices
        user_choices = dict(getattr(self, "_current_user_candidate_choices", {}))
        user_choices_sorted = tuple(sorted((str(k), str(v)) for k, v in user_choices.items()))

        # 7. Explicit Frame Evidence & FPS
        ev_table = getattr(self, "_source_frame_evidence_table", None)
        ev_status = "ABSENT"
        ev_capture: Any = ()

        if ev_table is None or (isinstance(ev_table, Mapping) and len(ev_table) == 0):
            ev_status = "ABSENT"
            ev_capture = ()
        elif not isinstance(ev_table, Mapping):
            ev_status = "MALFORMED"
            ev_capture = f"INVALID_TYPE:{type(ev_table).__name__}"
        else:
            ev_status = "PROVIDED"
            ev_items: List[Tuple[Any, ...]] = []
            for k, ev in sorted(ev_table.items(), key=lambda x: str(x[0])):
                if not isinstance(k, str) or not k:
                    ev_status = "MALFORMED"
                    ev_items = [(f"INVALID_KEY:{repr(k)}",)]
                    break
                try:
                    owned_ev = reconstruct_source_frame_evidence(ev)
                    fps_repr = (
                        (owned_ev.source_fps.numerator, owned_ev.source_fps.denominator)
                        if owned_ev.source_fps
                        else None
                    )
                    ev_items.append((
                        owned_ev.manifest_id,
                        owned_ev.clip_id,
                        fps_repr,
                        owned_ev.total_frames,
                        owned_ev.evidence_status,
                    ))
                except Exception as ex:
                    ev_status = "MALFORMED"
                    ev_items = [(f"RECONSTRUCT_ERROR:{ex}",)]
                    break
            ev_capture = tuple(ev_items)

        fps_val = curr_ctx.get("timeline_fps") if curr_ctx.get("timeline_fps") is not None else curr_ctx.get("fps")
        fps_capture = None
        if fps_val is not None:
            try:
                rf = RationalFPS.from_value(fps_val)
                fps_capture = (rf.numerator, rf.denominator)
            except Exception:
                fps_capture = f"INVALID_FPS:{repr(fps_val)}"

        proof_signature = None
        if include_proof:
            proof_map = getattr(self, "_preapproval_evidence_map", None)
            proof_signature = compute_proof_signature(proof_map)

        return {
            "manifest_id": m_id,
            "supplier_manifest_id": supplier_manifest_id,
            "clean_paths_ordered": tuple(clean_paths),
            "manifest_clips": tuple(manifest_clip_tuples),
            "creative_context": c_ctx_dict,
            "video_plan_repr": vp_repr,
            "kinetic_plan_repr": kp_repr,
            "sidecar_manifest_id": sidecar_manifest_id,
            "sidecar_rev": sidecar_rev,
            "sidecar_entries_summary": tuple(sidecar_entries_summary),
            "user_choices": user_choices_sorted,
            "ev_status": ev_status,
            "ev_capture": ev_capture,
            "fps_capture": fps_capture,
            "proof_signature": proof_signature,
        }

    def _build_pending_scan_input_signature(self, curr_ctx: Optional[Mapping[str, Any]] = None) -> str:
        """Tính SHA-256 canonical của base editorial input fingerprint (không phụ thuộc kết quả proof đang tạo)."""
        fp = self._build_current_input_fingerprint(curr_ctx, include_proof=False)
        canonical_json = json.dumps(fp, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def _rebuild_and_present_proposal(self) -> None:
        """Thực hiện chu trình lắp ráp và hiển thị kịch bản đề xuất toàn bản lên CommonProposalReviewWidget."""
        manifest = getattr(self, "_manifest", None)

        # Thẩm định và vô hiệu hóa bằng chứng nguồn nếu manifest/clip_paths đã thay đổi
        if getattr(self, "_preapproval_evidence_map", None):
            invalidate_proof = False
            m_id = manifest.get("manifest_id") if isinstance(manifest, Mapping) else getattr(manifest, "manifest_id", None)
            m_clips = manifest.get("clips") if isinstance(manifest, Mapping) else getattr(manifest, "clips", ())
            current_clip_paths: Dict[str, str] = {}
            for c in m_clips:
                cid = c.get("clip_id") if isinstance(c, Mapping) else getattr(c, "clip_id", None)
                cpath = (c.get("current_path") or c.get("file_path")) if isinstance(c, Mapping) else (getattr(c, "current_path", None) or getattr(c, "file_path", None))
                if cid and cpath:
                    current_clip_paths[str(cid)] = str(cpath)

            for cid, sn in list(self._preapproval_evidence_map.items()):
                if cid not in current_clip_paths:
                    invalidate_proof = True
                    break
                exp_p = current_clip_paths[cid]
                sn_p = sn.get("file_path") if isinstance(sn, Mapping) else getattr(sn, "file_path", None)
                sn_mid = sn.get("manifest_id") if isinstance(sn, Mapping) else getattr(sn, "manifest_id", None)
                if str(sn_mid) != str(m_id) or not exp_p or not sn_p or os.path.normpath(str(sn_p)) != os.path.normpath(str(exp_p)):
                    invalidate_proof = True
                    break

            if invalidate_proof:
                logger.info("Bằng chứng nguồn đã cũ do tệp nguồn hoặc manifest thay đổi, tự động xóa proof map.")
                self._preapproval_evidence_map = {}
                self._preapproval_evidence_diagnostics = ()
                if hasattr(self, "lbl_source_evidence_status"):
                    self.lbl_source_evidence_status.setText("⚠ Bằng chứng nguồn đã hết hiệu lực do tệp nguồn đã thay đổi.")
                    self.lbl_source_evidence_status.setStyleSheet("color: #f59e0b; font-size: 10px;")

                running_workers = any(w.isRunning() for w in self._preapproval_evidence_workers.values()) if hasattr(self, "_preapproval_evidence_workers") else False
                if running_workers:
                    self._preapproval_service_reset_pending = True
                    for w in list(self._preapproval_evidence_workers.values()):
                        if hasattr(w, "cancel") and callable(w.cancel):
                            w.cancel()
                else:
                    self._preapproval_service_reset_pending = False
                    if getattr(self, "_preapproval_evidence_service", None) is not None:
                        svc = self._preapproval_evidence_service
                        if hasattr(svc, "clear_cache") and callable(svc.clear_cache):
                            try:
                                svc.clear_cache()
                            except Exception:
                                pass
                        self._preapproval_evidence_service = None

        curr_ctx_dict = self._get_current_context() or {}
        try:
            fingerprint = self._build_current_input_fingerprint(curr_ctx_dict)
        except Exception as exc:
            logger.warning("Không thể xây dựng fingerprint dữ liệu hiện hành: %s", exc)
            self._current_approval_record = None
            self._last_displayed_input_fingerprint = None
            if hasattr(self, "common_review_widget") and self.common_review_widget:
                self.common_review_widget.set_error(str(exc))
                self.common_review_widget.set_stale(True)
            return

        video_plan = getattr(self, "_video_plan_data", None)
        manifest = getattr(self, "_manifest", None)
        raw_creative = curr_ctx_dict.get("creative_context")
        creative_context = raw_creative if raw_creative is not None else {}

        # Thẩm định sidecar cố vấn tài nguyên hiện tại
        sidecar = getattr(self, "_last_good_matching_sidecar", None)
        matching_plan_revision = None
        placeholder_batch = None
        if sidecar is not None:
            try:
                batch_obj, curr_rev, _ = self._build_matching_context(video_plan)
                if sidecar.plan_revision == curr_rev:
                    matching_plan_revision = curr_rev
                    placeholder_batch = batch_obj
                else:
                    sidecar = None
            except Exception:
                sidecar = None

        if not getattr(self, "_proposal_session_id", None):
            self._proposal_session_id = f"prop_{uuid.uuid4().hex[:12]}"

        try:
            proposal = build_edit_plan_proposal(
                proposal_id=self._proposal_session_id,
                manifest=manifest,
                video_plan=video_plan,
                creative_context=creative_context,
                kinetic_plan=getattr(self, "_kinetic_plan_data", None),
                sidecar=sidecar,
                matching_plan_revision=matching_plan_revision,
                expected_batch=placeholder_batch,
                selected_candidate_ids=self._current_user_candidate_choices,
                source_evidence_by_clip=getattr(self, "_preapproval_evidence_map", None) or None,
            )
        except Exception as exc:
            logger.warning("Lỗi lắp ráp đề xuất kịch bản toàn bản: %s", exc)
            self._current_approval_record = None
            self._last_displayed_input_fingerprint = None
            if hasattr(self, "common_review_widget") and self.common_review_widget:
                self.common_review_widget.set_error(str(exc))
                self.common_review_widget.set_stale(True)
            return

        # Thẩm định hình học khung hình (SourceFrameEvidence nếu có cung cấp explicit snapshot)
        geom_result = None
        self._geometry_blocked_diagnostic = None
        ev_table = getattr(self, "_source_frame_evidence_table", None)
        fps_val = curr_ctx_dict.get("timeline_fps") if curr_ctx_dict.get("timeline_fps") is not None else curr_ctx_dict.get("fps")

        if ev_table is None or (isinstance(ev_table, Mapping) and len(ev_table) == 0):
            # Honest absence -> UNRESOLVED (cho phép duyệt biên tập)
            geom_result = None
        elif not isinstance(ev_table, Mapping):
            self._geometry_blocked_diagnostic = f"ERR_MALFORMED_EVIDENCE_TABLE: ev_table có kiểu {type(ev_table).__name__}, bắt buộc là Mapping[str, SourceFrameEvidence]."
            geom_result = PlanGeometryAssemblyResult(
                status="GEOMETRY_BLOCKED",
                proposal_id=proposal.proposal_id,
                proposal_revision=compute_proposal_revision(proposal),
                timeline_fps=RationalFPS(24, 1),
                assembled_segments=(),
                total_timeline_frames=None,
                total_timeline_duration_sec=None,
                issues=(PlanGeometryIssue(
                    severity="BLOCKING",
                    issue_code="ERR_MALFORMED_EVIDENCE_TABLE",
                    clip_id=None,
                    segment_index=None,
                    message=self._geometry_blocked_diagnostic,
                ),),
                unresolved_reviews=(),
            )
        else:
            # ev_table là Mapping
            if fps_val is None:
                self._geometry_blocked_diagnostic = "ERR_MISSING_FPS: Bảng bằng chứng frame được cung cấp nhưng thiếu timeline_fps hợp lệ."
                geom_result = PlanGeometryAssemblyResult(
                    status="GEOMETRY_BLOCKED",
                    proposal_id=proposal.proposal_id,
                    proposal_revision=compute_proposal_revision(proposal),
                    timeline_fps=RationalFPS(24, 1),
                    assembled_segments=(),
                    total_timeline_frames=None,
                    total_timeline_duration_sec=None,
                    issues=(PlanGeometryIssue(
                        severity="BLOCKING",
                        issue_code="ERR_MISSING_FPS",
                        clip_id=None,
                        segment_index=None,
                        message=self._geometry_blocked_diagnostic,
                    ),),
                    unresolved_reviews=(),
                )
            else:
                try:
                    rat_fps = RationalFPS.from_value(fps_val)
                    owned_evidence_table = {}
                    for k, ev in ev_table.items():
                        if not isinstance(k, str) or not k.strip():
                            raise ValueError(f"ERR_INVALID_EVIDENCE_KEY: Khóa evidence không hợp lệ: {k!r}")
                        owned_ev = reconstruct_source_frame_evidence(ev)
                        if owned_ev.clip_id != k:
                            raise ValueError(f"ERR_EVIDENCE_CLIP_MISMATCH: Khóa mapping '{k}' không khớp clip_id '{owned_ev.clip_id}' trong evidence.")
                        m_expected = getattr(manifest, "manifest_id", None) if manifest else None
                        if m_expected and owned_ev.manifest_id != m_expected:
                            raise ValueError(f"ERR_EVIDENCE_MANIFEST_MISMATCH: manifest_id '{owned_ev.manifest_id}' trong evidence không khớp loaded manifest '{m_expected}'.")
                        owned_evidence_table[k] = owned_ev

                    geom_result = assemble_plan_frame_geometry(proposal, owned_evidence_table, rat_fps)
                except Exception as ge:
                    logger.warning("Thẩm định hình học thất bại: %s", ge)
                    self._geometry_blocked_diagnostic = str(ge)
                    fallback_fps = RationalFPS(24, 1)
                    try:
                        fallback_fps = RationalFPS.from_value(fps_val)
                    except Exception:
                        pass
                    geom_result = PlanGeometryAssemblyResult(
                        status="GEOMETRY_BLOCKED",
                        proposal_id=proposal.proposal_id,
                        proposal_revision=compute_proposal_revision(proposal),
                        timeline_fps=fallback_fps,
                        assembled_segments=(),
                        total_timeline_frames=None,
                        total_timeline_duration_sec=None,
                        issues=(PlanGeometryIssue(
                            severity="BLOCKING",
                            issue_code="ERR_FRAME_EVIDENCE_INVALID",
                            clip_id=None,
                            segment_index=None,
                            message=str(ge),
                        ),),
                        unresolved_reviews=(),
                    )

        # Hủy bỏ hồ sơ phê duyệt runtime nếu:
        # 1. Dữ liệu đầu vào (fingerprint) thay đổi so với bản hiển thị trước đó
        # 2. Proposal revision thay đổi
        # 3. Hoặc hình học bị BLOCKED
        # 4. Hoặc liên kết phê duyệt không còn hợp lệ
        old_fingerprint = getattr(self, "_last_displayed_input_fingerprint", None)
        if self._current_approval_record is not None and old_fingerprint is not None:
            if fingerprint != old_fingerprint:
                logger.info("Dữ liệu đầu vào thay đổi so với bản hiển thị trước đó, xóa hồ sơ phê duyệt cũ.")
                self._current_approval_record = None

        self._last_displayed_input_fingerprint = fingerprint
        self._current_proposal = proposal
        self._current_geometry_result = geom_result

        if self._current_approval_record is not None:
            new_rev = compute_proposal_revision(proposal)
            geom_blocked = (geom_result is not None and getattr(geom_result, "status", None) == "GEOMETRY_BLOCKED") or bool(self._geometry_blocked_diagnostic)
            binding_valid = False
            try:
                binding_valid = verify_proposal_approval_binding(proposal, self._current_approval_record)
            except Exception:
                binding_valid = False

            if (
                self._current_approval_record.proposal_revision != new_rev
                or geom_blocked
                or not binding_valid
            ):
                self._current_approval_record = None

        if hasattr(self, "common_review_widget") and self.common_review_widget:
            self.common_review_widget.set_proposal(
                proposal=proposal,
                geometry_result=geom_result,
                approval=self._current_approval_record,
                sidecar=sidecar,
            )

    def _on_proposal_approved(self, proposal_id: str, revision: str) -> None:
        """Xử lý sự kiện người dùng bấm Phê duyệt trên CommonProposalReviewWidget."""
        if hasattr(self, "common_review_widget") and self.common_review_widget:
            if getattr(self.common_review_widget, "_is_stale", False):
                logger.warning("Từ chối phê duyệt: widget đang ở trạng thái stale.")
                self._current_approval_record = None
                return

        if self._current_proposal is None or getattr(self, "_last_displayed_input_fingerprint", None) is None:
            self._current_approval_record = None
            return

        # 1. Thẩm định thẩm quyền dữ liệu hiện hành (Current-Input Authority):
        # Đọc supplier ĐÚNG 1 LẦN duy nhất, tính toán fingerprint hiện hành và so sánh với fingerprint bản hiển thị
        curr_ctx = self._get_current_context() or {}
        try:
            curr_fingerprint = self._build_current_input_fingerprint(curr_ctx)
        except Exception as exc:
            logger.warning("Từ chối phê duyệt: Dữ liệu đầu vào hiện tại không hợp lệ (%s)", exc)
            self._current_approval_record = None
            if hasattr(self, "common_review_widget") and self.common_review_widget:
                self.common_review_widget.set_stale(True)
            return

        if curr_fingerprint != self._last_displayed_input_fingerprint:
            logger.warning(
                "Từ chối phê duyệt: Dữ liệu đầu vào hiện tại đã bị thay đổi so với bản hiển thị (fingerprint mismatch). "
                "Yêu cầu người dùng bấm Cập nhật trước khi duyệt."
            )
            self._current_approval_record = None
            if hasattr(self, "common_review_widget") and self.common_review_widget:
                self.common_review_widget.set_stale(True)
            return

        # Chống TOCTOU / Programmatic mutation: Đối chiếu proposal_id và revision với event
        current_rev = compute_proposal_revision(self._current_proposal)
        if self._current_proposal.proposal_id != proposal_id or current_rev != revision:
            logger.warning(
                "Từ chối phê duyệt: Kịch bản đã bị biến đổi ngầm (event: %s/%s vs fresh: %s/%s)",
                proposal_id, revision, self._current_proposal.proposal_id, current_rev
            )
            self._current_approval_record = None
            if hasattr(self, "common_review_widget") and self.common_review_widget:
                self.common_review_widget.set_stale(True)
            return

        # 2. Thẩm định toàn vẹn đồ thị (Full Graph Validation) tại thời điểm phê duyệt:
        # Tái tạo và kiểm tra kịch bản thông qua build_edit_plan_proposal để bảo đảm đồ thị hợp lệ
        try:
            vp = getattr(self, "_video_plan_data", None)
            kp = getattr(self, "_kinetic_plan_data", None)
            manifest = getattr(self, "_manifest", None)
            sidecar = getattr(self, "_last_good_matching_sidecar", None)
            c_ctx_dict = curr_fingerprint.get("creative_context") or {}
            user_choices = getattr(self, "_current_user_candidate_choices", None)

            # Lấy canonical expected_batch để validate đồ thị tương tự _rebuild_and_present_proposal
            matching_plan_revision = None
            placeholder_batch = None
            if sidecar is not None and vp:
                try:
                    batch_obj, curr_rev, _ = self._build_matching_context(vp)
                    if sidecar.plan_revision == curr_rev:
                        matching_plan_revision = curr_rev
                        placeholder_batch = batch_obj
                    else:
                        sidecar = None
                except Exception:
                    sidecar = None

            fresh_prop = build_edit_plan_proposal(
                proposal_id=self._current_proposal.proposal_id,
                manifest=manifest,
                video_plan=vp,
                creative_context=c_ctx_dict,
                kinetic_plan=kp,
                sidecar=sidecar,
                matching_plan_revision=matching_plan_revision,
                expected_batch=placeholder_batch,
                selected_candidate_ids=user_choices,
                source_evidence_by_clip=getattr(self, "_preapproval_evidence_map", None) or None,
            )
            fresh_prop_rev = compute_proposal_revision(fresh_prop)
            if fresh_prop_rev != current_rev:
                logger.warning(
                    "Từ chối phê duyệt: Tái tạo kịch bản từ graph hiện tại cho revision khác với bản hiển thị (%s vs %s)",
                    fresh_prop_rev, current_rev
                )
                self._current_approval_record = None
                if hasattr(self, "common_review_widget") and self.common_review_widget:
                    self.common_review_widget.set_stale(True)
                return
        except Exception as b_err:
            logger.warning("Từ chối phê duyệt: Thẩm định graph qua proposal_builder thất bại: %s", b_err)
            self._current_approval_record = None
            if hasattr(self, "common_review_widget") and self.common_review_widget:
                self.common_review_widget.set_stale(True)
            return

        # 3. Kiểm tra nếu hình học bị chặn
        geom_status = getattr(self._current_geometry_result, "status", None)
        if geom_status == "GEOMETRY_BLOCKED" or getattr(self, "_geometry_blocked_diagnostic", None):
            logger.warning("Từ chối phê duyệt: Hình học kịch bản đang ở trạng thái BLOCKED.")
            self._current_approval_record = None
            if hasattr(self, "common_review_widget") and self.common_review_widget:
                self.common_review_widget.set_stale(True)
            return

        # Tái thẩm định hình học nếu có evidence table
        ev_table = getattr(self, "_source_frame_evidence_table", None)
        if ev_table is not None and not (isinstance(ev_table, Mapping) and len(ev_table) == 0):
            if not isinstance(ev_table, Mapping):
                logger.warning("Từ chối phê duyệt: ev_table không phải Mapping.")
                self._current_approval_record = None
                if hasattr(self, "common_review_widget") and self.common_review_widget:
                    self.common_review_widget.set_stale(True)
                return
            fps_val = curr_ctx.get("timeline_fps") if curr_ctx.get("timeline_fps") is not None else curr_ctx.get("fps")
            if fps_val is None:
                logger.warning("Từ chối phê duyệt: thiếu timeline_fps khi có bảng bằng chứng frame.")
                self._current_approval_record = None
                if hasattr(self, "common_review_widget") and self.common_review_widget:
                    self.common_review_widget.set_stale(True)
                return
            try:
                rf = RationalFPS.from_value(fps_val)
                fresh_geom = assemble_plan_frame_geometry(self._current_proposal, ev_table, rf)
                if fresh_geom.status == "GEOMETRY_BLOCKED":
                    logger.warning("Từ chối phê duyệt: Fresh geometry assembly bị BLOCKED.")
                    self._current_approval_record = None
                    if hasattr(self, "common_review_widget") and self.common_review_widget:
                        self.common_review_widget.set_stale(True)
                    return
            except Exception as e_geom:
                logger.warning("Từ chối phê duyệt: Fresh geometry assembly thất bại: %s", e_geom)
                self._current_approval_record = None
                if hasattr(self, "common_review_widget") and self.common_review_widget:
                    self.common_review_widget.set_stale(True)
                return

        # 3. Tạo hồ sơ phê duyệt thuộc quyền sở hữu ứng dụng (AppOwnedApprovalRecord)
        approval_id = f"appr_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            approval_rec = create_app_owned_approval_record(
                self._current_proposal,
                approval_id=approval_id,
                approved_at_iso=now_iso,
            )
            # 4. Xác minh liên kết toàn vẹn bất biến
            is_bound = verify_proposal_approval_binding(self._current_proposal, approval_rec)
            if not is_bound:
                logger.error("Từ chối phê duyệt: verify_proposal_approval_binding trả về False!")
                self._current_approval_record = None
                return
        except Exception as err:
            logger.error("Không thể tạo hoặc xác thực AppOwnedApprovalRecord: %s", err)
            self._current_approval_record = None
            return

        # 5. Lưu trữ kịch bản tươi mới và hồ sơ phê duyệt runtime
        self._current_approval_record = approval_rec

        # 6. Cập nhật hiển thị widget (CHỈ DUYỆT BIÊN TẬP, ZERO CALLS TO DAVINCI RESOLVE / G07)
        if hasattr(self, "common_review_widget") and self.common_review_widget:
            self.common_review_widget.set_proposal(
                proposal=self._current_proposal,
                geometry_result=self._current_geometry_result,
                approval=approval_rec,
                sidecar=getattr(self, "_last_good_matching_sidecar", None),
            )

    def _on_candidate_selected(self, demand_id: str, asset_id: str) -> None:
        """Xử lý khi người dùng chọn một ứng viên tài nguyên cụ thể cho demand."""
        self._current_user_candidate_choices[demand_id] = asset_id
        self._current_approval_record = None  # Thay đổi lựa chọn hủy hồ sơ phê duyệt cũ
        self._rebuild_and_present_proposal()

    def _on_candidate_omitted(self, demand_id: str) -> None:
        """Xử lý khi người dùng chọn chủ động lược bỏ một demand."""
        self._current_user_candidate_choices[demand_id] = "__omit__"
        self._current_approval_record = None  # Thay đổi lựa chọn hủy hồ sơ phê duyệt cũ
        self._rebuild_and_present_proposal()

    def get_approved_proposal_and_record(
        self,
    ) -> Optional[Tuple[EditPlanProposal, AppOwnedApprovalRecord]]:
        """Lấy cặp (EditPlanProposal, AppOwnedApprovalRecord) đã phê duyệt và còn hiệu lực hiện hành.

        Ràng buộc G07-07A1-I:
        - Đọc trực tiếp app-owned self._current_proposal và self._current_approval_record.
          Tuyệt đối không đọc cờ bool từ config hay tự động sinh approval mới.
        - Kiểm tra nominal type của cả 2 đối tượng.
        - Kiểm tra widget stale và hình học bị BLOCKED.
        - Đọc context supplier ĐÚNG 1 LẦN duy nhất, xây dựng fingerprint hiện hành và so khớp.
        - So khớp revision: compute_proposal_revision(proposal) == approval_record.proposal_revision.
        - Xác minh liên kết toàn vẹn và bất biến qua verify_proposal_approval_binding
          (bao gồm cả thứ tự và danh sách accepted_omission_ids, approval_scope == 'ENTIRE_PROPOSAL').
        - Nếu bất kỳ kiểm tra nào thất bại -> trả về None, xóa approval không hợp lệ, giữ nguyên
          dữ liệu nháp / view của người dùng.
        - Trả về bản sao sở hữu an toàn (owned copies) qua normalize_edit_plan_proposal và
          AppOwnedApprovalRecord re-validated.
        - 1 supplier read, 0 I/O, 0 clock, 0 UUID, 0 Resolve call, 0 AI model.
        - Chấp nhận frame evidence ABSENT (editorial handoff hợp lệ, chưa chứng minh execution readiness).
        """
        if self._current_proposal is None or self._current_approval_record is None:
            return None

        if not isinstance(self._current_proposal, EditPlanProposal) or not isinstance(
            self._current_approval_record, AppOwnedApprovalRecord
        ):
            self._current_approval_record = None
            return None

        if hasattr(self, "common_review_widget") and self.common_review_widget:
            if getattr(self.common_review_widget, "_is_stale", False):
                self._current_approval_record = None
                return None

        geom_status = getattr(self._current_geometry_result, "status", None)
        if geom_status == "GEOMETRY_BLOCKED" or bool(getattr(self, "_geometry_blocked_diagnostic", None)):
            self._current_approval_record = None
            return None

        # 1 supplier read duy nhất
        curr_ctx = self._get_current_context() or {}
        try:
            curr_fingerprint = self._build_current_input_fingerprint(curr_ctx)
        except Exception:
            self._current_approval_record = None
            if hasattr(self, "common_review_widget") and self.common_review_widget:
                self.common_review_widget.set_stale(True)
            return None

        last_fp = getattr(self, "_last_displayed_input_fingerprint", None)
        if last_fp is None or curr_fingerprint != last_fp:
            self._current_approval_record = None
            if hasattr(self, "common_review_widget") and self.common_review_widget:
                self.common_review_widget.set_stale(True)
            return None

        try:
            owned_prop = normalize_edit_plan_proposal(self._current_proposal)
            owned_appr = AppOwnedApprovalRecord(
                approval_id=self._current_approval_record.approval_id,
                proposal_id=self._current_approval_record.proposal_id,
                proposal_revision=self._current_approval_record.proposal_revision,
                approved_at_iso=self._current_approval_record.approved_at_iso,
                accepted_omission_ids=tuple(self._current_approval_record.accepted_omission_ids),
                approval_scope=self._current_approval_record.approval_scope,
            )
            current_rev = compute_proposal_revision(owned_prop)
            if owned_appr.proposal_revision != current_rev:
                self._current_approval_record = None
                if hasattr(self, "common_review_widget") and self.common_review_widget:
                    self.common_review_widget.set_stale(True)
                return None

            if not verify_proposal_approval_binding(owned_prop, owned_appr):
                self._current_approval_record = None
                return None

            return (owned_prop, owned_appr)
        except Exception as exc:
            logger.warning("get_approved_proposal_and_record gặp ngoại lệ xác thực payload: %s", exc)
            self._current_approval_record = None
            if hasattr(self, "common_review_widget") and self.common_review_widget:
                self.common_review_widget.set_stale(True)
            return None

