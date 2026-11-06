"""
src/ui/director/widgets/asset_advisory_drawer.py
=============================================================================
Khung hiển thị Cố vấn tài nguyên ngoại tuyến (Asset Advisory Drawer):
- Hoàn toàn ĐỌC-CHỈ-ĐỌC (Read-Only): 0 preview, 0 autoplay, 0 insert, 0 auto-pick.
- Sử dụng QTableView kết hợp AssetAdvisoryModel bọc trực tiếp AssetMatchingSidecar.
- Hiển thị đầy đủ các trường DTO (source_slot, index, timeline_sec, query_text, category, status).
- Điểm tương đồng định dạng từ điển chuẩn 'NN/100' (cấm confidence%).
- Hiển thị đầy đủ phân hoạch counters (matched, unmatched, empty_query, unsupported, invalid, overflow).
- Hỗ trợ cảnh báo kịch bản cũ (Stale Banner) và thông báo lỗi có kiểm soát không dùng popup.
=============================================================================
"""

from typing import List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
)

from src.core.contracts.plan_demand_contracts import (
    AssetDemandItem,
    AssetMatchingSidecar,
    DemandMatchEntry,
)
from src.ui.director.workers.asset_matching_worker import MatchingStorageContext


class AssetAdvisoryModel(QAbstractTableModel):
    """Model hiển thị dạng bảng chỉ-đọc cho danh sách kết quả cố vấn tài nguyên."""

    COLUMNS = [
        "Vị trí / Cảnh",
        "Phân loại",
        "Yêu cầu / Từ khóa",
        "Thời lượng",
        "Trạng thái",
        "Ứng viên hàng đầu (Advisory)",
        "Điểm phù hợp",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: List[DemandMatchEntry] = []

    def set_sidecar(self, sidecar: Optional[AssetMatchingSidecar]) -> None:
        self.beginResetModel()
        if sidecar is not None and hasattr(sidecar, "entries"):
            self._entries = list(sidecar.entries)
        else:
            self._entries = []
        self.endResetModel()

    def clear(self) -> None:
        self.set_sidecar(None)

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._entries)

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
        if not index.isValid() or not (0 <= index.row() < len(self._entries)):
            return None

        entry: DemandMatchEntry = self._entries[index.row()]
        demand: AssetDemandItem = entry.demand
        col = index.column()

        # Hiển thị văn bản thuần túy (Plain text)
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                slot_name = "B-roll" if demand.source_slot in ("broll", "broll_inserts") else ("SFX" if demand.source_slot in ("sfx", "sfx_inserts") else str(demand.source_slot))
                time_str = f" @ {demand.timeline_sec:.1f}s" if demand.timeline_sec is not None else ""
                return f"{slot_name} #{demand.source_index + 1}{time_str}"

            if col == 1:
                return demand.category or demand.raw_type or "-"

            if col == 2:
                # Ưu tiên query_text, fallback raw_query_hint
                q = demand.query_text or demand.raw_query_hint
                return q if q else "(Rỗng)"

            if col == 3:
                if demand.desired_duration_sec is not None:
                    return f"{demand.desired_duration_sec:.1f}s"
                return "-"

            if col == 4:
                if entry.is_matched:
                    return "✓ Khớp"
                if demand.status == "eligible":
                    return "✕ Không khớp"
                status_labels = {
                    "invalid": "⚠ Không hợp lệ",
                    "unsupported": "— Chưa hỗ trợ",
                    "empty_query": "∅ Từ khóa rỗng",
                    "overflow": "⚠ Vượt hạn ngạch",
                }
                return status_labels.get(demand.status, demand.status)

            if col == 5:
                if entry.is_matched and entry.candidates:
                    top = entry.candidates[0]
                    pack_info = f" [{top.pack_name}]" if top.pack_name else ""
                    avail = f" ({top.availability})" if top.availability else ""
                    return f"{top.name}{pack_info}{avail}"
                if demand.status == "eligible":
                    return "(Không tìm thấy)"
                return f"({demand.status})"

            if col == 6:
                if entry.is_matched and entry.candidates:
                    score = getattr(entry.candidates[0], "match_score", getattr(entry.candidates[0], "score", 0))
                    return f"{score}/100"
                return "-"

        # Canh lề
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (0, 1, 3, 4, 6):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Màu sắc huy hiệu trạng thái
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 4:
                if entry.is_matched:
                    return QColor("#4ade80")  # Xanh lá
                if demand.status == "eligible":
                    return QColor("#a1a1aa")  # Xám
                if demand.status in ("invalid", "overflow"):
                    return QColor("#f87171")  # Đỏ
                if demand.status == "empty_query":
                    return QColor("#fb923c")  # Cam
                if demand.status == "unsupported":
                    return QColor("#facc15")  # Vàng
            if col == 6 and entry.is_matched:
                return QColor("#38bdf8")  # Xanh dương

        return None


class AssetAdvisoryDrawer(QFrame):
    """Khung co giãn co/mở hiển thị bảng cố vấn tài nguyên ngoại tuyến."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            AssetAdvisoryDrawer {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                margin-top: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Thanh tiêu đề và điều khiển thu gọn
        h_top = QHBoxLayout()
        h_top.setSpacing(6)

        self.lbl_title = QLabel("💡 Bảng Cố Vấn Tài Nguyên Ngoại Tuyến")
        self.lbl_title.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 11px; color: #38bdf8;")
        h_top.addWidget(self.lbl_title)

        self.lbl_stale_banner = QLabel("⚠ Kết quả cũ — bấm Gợi ý để cập nhật")
        self.lbl_stale_banner.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_stale_banner.setStyleSheet("""
            background: rgba(234, 179, 8, 0.15);
            color: #eab308;
            border: 1px solid #eab308;
            border-radius: 3px;
            padding: 1px 6px;
            font-size: 10px;
            font-weight: bold;
        """)
        self.lbl_stale_banner.setVisible(False)
        h_top.addWidget(self.lbl_stale_banner)

        h_top.addStretch(1)

        self.btn_toggle = QPushButton("▲ Thu gọn")
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                background: #27272a;
                color: #d4d4d8;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 1px 6px;
                font-size: 10px;
            }
            QPushButton:hover {
                background: #3f3f46;
                color: #ffffff;
            }
        """)
        self.btn_toggle.clicked.connect(self._toggle_collapse)
        h_top.addWidget(self.btn_toggle)

        layout.addLayout(h_top)

        # Thanh thông tin ngữ cảnh kho và thống kê phân hoạch counters
        self.info_frame = QFrame()
        v_info = QVBoxLayout(self.info_frame)
        v_info.setContentsMargins(0, 0, 0, 0)
        v_info.setSpacing(2)

        self.lbl_summary = QLabel("Chưa có kết quả gợi ý tài nguyên.")
        self.lbl_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_summary.setStyleSheet("color: #a1a1aa; font-size: 10px;")
        v_info.addWidget(self.lbl_summary)

        self.lbl_storage_info = QLabel("Kho media: Chưa xác định")
        self.lbl_storage_info.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_storage_info.setStyleSheet("color: #71717a; font-size: 9.5px;")
        v_info.addWidget(self.lbl_storage_info)

        self.lbl_error_banner = QLabel("")
        self.lbl_error_banner.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_error_banner.setStyleSheet("""
            background: rgba(239, 68, 68, 0.15);
            color: #ef4444;
            border: 1px solid #ef4444;
            border-radius: 3px;
            padding: 2px 6px;
            font-size: 10px;
            font-weight: bold;
        """)
        self.lbl_error_banner.setVisible(False)
        v_info.addWidget(self.lbl_error_banner)

        layout.addWidget(self.info_frame)

        # Bảng dữ liệu QTableView đọc-chỉ-đọc
        self._model = AssetAdvisoryModel(self)
        self.table_view = QTableView(self)
        self.table_view.setModel(self._model)
        self.table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.verticalHeader().setVisible(False)
        self.table_view.horizontalHeader().setStretchLastSection(False)
        self.table_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_view.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table_view.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_view.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table_view.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table_view.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table_view.setStyleSheet("""
            QTableView {
                background: #09090b;
                alternate-background-color: #121215;
                gridline-color: #27272a;
                color: #e4e4e7;
                font-size: 10.5px;
                border: 1px solid #27272a;
                border-radius: 4px;
            }
            QHeaderView::section {
                background: #18181b;
                color: #a1a1aa;
                padding: 3px;
                font-size: 10px;
                font-weight: bold;
                border: 1px solid #27272a;
            }
            QTableView::item:selected {
                background: #1e293b;
                color: #38bdf8;
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.table_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.table_view.setMinimumHeight(130)
        self.table_view.setMaximumHeight(260)
        layout.addWidget(self.table_view)

        self._is_collapsed = False

    def sizeHint(self) -> QSize:
        if self._is_collapsed:
            h_header = self.lbl_title.sizeHint().height() + 14
            return QSize(250, max(h_header, 32))
        info_h = self.info_frame.sizeHint().height() if self.info_frame.isVisible() else 0
        table_h = self.table_view.minimumHeight() if self.table_view.isVisible() else 0
        h_header = self.lbl_title.sizeHint().height() + 14
        total_h = h_header + info_h + table_h + 16
        return QSize(300, max(total_h, 180))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _toggle_collapse(self) -> None:
        self._is_collapsed = not self._is_collapsed
        self.table_view.setVisible(not self._is_collapsed)
        self.info_frame.setVisible(not self._is_collapsed)
        self.btn_toggle.setText("▼ Mở rộng" if self._is_collapsed else "▲ Thu gọn")
        self.updateGeometry()

    def set_sidecar(
        self,
        sidecar: Optional[AssetMatchingSidecar],
        storage_context: Optional[MatchingStorageContext] = None
    ) -> None:
        """Cập nhật dữ liệu sidecar và thông tin kho lên drawer."""
        self._model.set_sidecar(sidecar)
        self.lbl_error_banner.setVisible(False)
        self.lbl_stale_banner.setVisible(False)

        if sidecar is not None:
            self.lbl_summary.setText(
                f"Tổng: {sidecar.total_demands} mục  |  "
                f"Khớp: {sidecar.matched_count}  |  "
                f"Không khớp: {sidecar.unmatched_count}  |  "
                f"Từ khóa rỗng: {sidecar.empty_query_count}  |  "
                f"Chưa hỗ trợ: {sidecar.unsupported_count}  |  "
                f"Không hợp lệ: {sidecar.invalid_count}  |  "
                f"Vượt hạn ngạch: {sidecar.overflow_count}"
            )
            self.lbl_summary.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
        else:
            self.lbl_summary.setText("Chưa có kết quả gợi ý tài nguyên.")
            self.lbl_summary.setStyleSheet("color: #a1a1aa; font-size: 10px;")

        if isinstance(storage_context, MatchingStorageContext):
            self.lbl_storage_info.setText(f"Kho media: {storage_context.library_id} ({storage_context.root})")
        else:
            self.lbl_storage_info.setText("Kho media: Chưa kích hoạt tra cứu kho")

    def set_stale(self, is_stale: bool = True) -> None:
        """Đánh dấu kết quả hiển thị hiện tại là kết quả cũ từ kịch bản trước."""
        self.lbl_stale_banner.setVisible(is_stale)
        if is_stale:
            self.lbl_summary.setStyleSheet("color: #eab308; font-size: 10px;")

    def set_error(self, message: str, *, retain_stale: bool = True) -> None:
        """Hiển thị thông báo lỗi có kiểm soát trên banner drawer."""
        self.lbl_error_banner.setText(f"✕ {message}")
        self.lbl_error_banner.setVisible(True)
        if not retain_stale:
            self.lbl_stale_banner.setVisible(False)

    def clear(self) -> None:
        """Xóa trắng bảng và đặt lại các nhãn."""
        self._model.clear()
        self.lbl_summary.setText("Chưa có kết quả gợi ý tài nguyên.")
        self.lbl_summary.setStyleSheet("color: #a1a1aa; font-size: 10px;")
        self.lbl_storage_info.setText("Kho media: Chưa xác định")
        self.lbl_stale_banner.setVisible(False)
        self.lbl_error_banner.setVisible(False)
