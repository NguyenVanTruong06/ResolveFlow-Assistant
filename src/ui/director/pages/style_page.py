"""
src/ui/director/pages/style_page.py
Bước 2: Chọn Kiểu Dựng & Thiết Lập Nhanh.
Bao gồm:
- 4 Thẻ phong cách (Podcast, Shorts 9:16, Vlog có Hook, Tự chỉnh).
- Segmented controls: Loại footage & Nhịp dựng.
- Slider Mức cắt vấp & lặng (Nhẹ / Vừa / Mạnh).
- Cụm chip tính năng bật/tắt (Hook, Punch-in, B-roll, SFX, Reframe, Sub, v.v.).
- Bảng nhập ngữ cảnh sáng tạo & tham chiếu (CreativeIntakePanel - D03).
- Nút "Tùy chỉnh nâng cao ›" trượt mở bảng bên phải 280px.
"""

import os
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from src.core.contracts.edit_plan_contracts import (
    MAX_REFERENCE_ITEMS,
    MAX_REFERENCE_URLS,
    MAX_RULES_PER_KIND,
    VALID_REFERENCE_ROLE,
    CreativeContext,
    creative_context_to_dict,
    normalize_creative_context,
)
from src.ui.director.director_state import (
    ALL_FEATURE_CHIPS,
    STYLE_CUSTOM,
    STYLE_PODCAST,
    STYLE_SHORTS,
    STYLE_VLOG,
    DirectorConfig,
)

RECOGNIZED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif", ".svg"}
RECOGNIZED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".ts"}


def parse_duration_text(text: str) -> Any:
    """Chuyển đổi chuỗi thời lượng thành số int hoặc float mà không làm mất độ chính xác.
    
    Hỗ trợ ký hiệu số thực thông thường (45.5) và ký hiệu khoa học (1e-07, 1e+20).
    Nếu không phải số hợp lệ, trả về nguyên bản chuỗi để core normalizer thẩm định và ném lỗi kiểm soát.
    """
    if not text:
        return text
    # 1. Thử chuyển đổi thành int (chỉ thành công nếu chuỗi số nguyên thuần túy)
    try:
        return int(text)
    except ValueError:
        pass
    # 2. Thử chuyển đổi thành float (bao gồm số thập phân và ký hiệu khoa học 1e-07, 1e20)
    try:
        return float(text)
    except ValueError:
        pass
    # 3. Trả về chuỗi thô để core validation ném ERR_INVALID_DURATION
    return text


class StyleCard(QFrame):
    """Một thẻ lớn đại diện cho 1 phong cách dựng video."""
    clicked = Signal(str)

    def __init__(self, style_id: str, title: str, desc: str, icon: str, parent=None):
        super().__init__(parent)
        self.style_id = style_id
        self.title = title
        self.desc = desc
        self.icon = icon
        self.is_selected = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(84)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        h_title = QHBoxLayout()
        lbl_icon = QLabel(self.icon)
        lbl_icon.setStyleSheet("font-size: 16px;")
        h_title.addWidget(lbl_icon)

        self.lbl_title = QLabel(self.title)
        self.lbl_title.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: bold;")
        h_title.addWidget(self.lbl_title, stretch=1)
        layout.addLayout(h_title)

        self.lbl_desc = QLabel(self.desc)
        self.lbl_desc.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_desc.setWordWrap(True)
        layout.addWidget(self.lbl_desc)

        self._update_style()

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self._update_style()

    def _update_style(self):
        if self.is_selected:
            self.setStyleSheet("""
                QFrame {
                    background-color: #292c34;
                    border: 1.5px solid #e05a47;
                    border-radius: 5px;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: #1f2126;
                    border: 1px solid #2e3138;
                    border-radius: 5px;
                }
                QFrame:hover {
                    border-color: #434752;
                    background-color: #25282e;
                }
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.style_id)


class SegmentedControl(QFrame):
    """Thanh segmented control chọn 1 giá trị trong danh sách."""
    valueChanged = Signal(str)

    def __init__(self, options: List[str], current: str, parent=None):
        super().__init__(parent)
        self.options = options
        self.current = current
        self._buttons: Dict[str, QPushButton] = {}
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame {
                background-color: #151619;
                border: 1px solid #2e3138;
                border-radius: 4px;
                padding: 2px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        for opt in self.options:
            btn = QPushButton(opt, self)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked=False, val=opt: self._on_btn_clicked(val))
            layout.addWidget(btn)
            self._buttons[opt] = btn

        self._refresh()

    def _on_btn_clicked(self, val: str):
        self.current = val
        self._refresh()
        self.valueChanged.emit(val)

    def set_value(self, val: str):
        if val in self.options:
            self.current = val
            self._refresh()

    def _refresh(self):
        for opt, btn in self._buttons.items():
            if opt == self.current:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #d94f3d;
                        color: #ffffff;
                        border: none;
                        border-radius: 3px;
                        padding: 0 10px;
                        font-size: 11px;
                        font-weight: 600;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background: transparent;
                        color: #9da3ae;
                        border: none;
                        border-radius: 3px;
                        padding: 0 10px;
                        font-size: 11px;
                    }
                    QPushButton:hover {
                        color: #ffffff;
                        background: rgba(255, 255, 255, 0.05);
                    }
                """)


class FeatureChipButton(QPushButton):
    """Một chip tính năng bật/tắt."""
    toggled_chip = Signal(str, bool)

    def __init__(self, name: str, is_active: bool = False, parent=None):
        super().__init__(name, parent)
        self.name = name
        self.is_active = is_active
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self.clicked.connect(self._toggle)
        self._update_appearance()

    def _toggle(self):
        self.is_active = not self.is_active
        self._update_appearance()
        self.toggled_chip.emit(self.name, self.is_active)

    def set_active(self, active: bool):
        self.is_active = active
        self._update_appearance()

    def _update_appearance(self):
        if self.is_active:
            self.setStyleSheet("""
                QPushButton {
                    background-color: rgba(217, 79, 61, 0.16);
                    color: #f8b4a8;
                    border: 1px solid #d94f3d;
                    border-radius: 4px;
                    padding: 0 12px;
                    font-size: 11px;
                    font-weight: 600;
                }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #191a1e;
                    color: #7c828f;
                    border: 1px solid #2e3138;
                    border-radius: 4px;
                    padding: 0 12px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    color: #c8ccd4;
                    border-color: #434752;
                }
            """)


class StringListEditor(QFrame):
    """Bộ chỉnh sửa danh sách chuỗi theo từng mục độc lập (per-item editing).
    
    Bảo toàn 100% tính toàn vẹn của dữ liệu:
    - Không tự ý .strip() khoảng trắng đầu/cuối của mục.
    - Một mục chứa ký tự xuống dòng ('\\n') vẫn là một mục duy nhất, không bị phân tách sai.
    - Lưu và nạp không làm biến đổi bất kỳ ký tự nào (lossless no-op roundtrip).
    - Các mục rỗng hoặc lỗi được giữ lại đầy đủ để validation của core bắt lỗi và hiển thị rõ ràng.
    """
    items_changed = Signal()

    def __init__(self, title: str, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.title = title
        self.placeholder = placeholder
        self._items: List[str] = []
        self._is_updating_ui = False
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Header hàng tiêu đề và các nút
        h_top = QHBoxLayout()
        lbl_title = QLabel(self.title)
        lbl_title.setStyleSheet("color: #a1a1aa; font-size: 11px; font-weight: 600;")
        h_top.addWidget(lbl_title, stretch=1)

        self.btn_add = QPushButton("Thêm", self)
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 1px 8px;
                font-size: 10.5px;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
        """)
        self.btn_add.clicked.connect(self._on_btn_add_clicked)
        h_top.addWidget(self.btn_add)

        self.btn_remove = QPushButton("Xóa", self)
        self.btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remove.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #f87171;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 1px 8px;
                font-size: 10.5px;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
        """)
        self.btn_remove.clicked.connect(self._on_btn_remove_clicked)
        h_top.addWidget(self.btn_remove)

        layout.addLayout(h_top)

        # Hàng hiển thị danh sách mục và ô soạn thảo mục đang chọn
        h_content = QHBoxLayout()
        h_content.setSpacing(8)

        self.list_widget = QListWidget(self)
        self.list_widget.setFixedHeight(64)
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: #09090b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 2px;
                font-size: 11px;
            }
            QListWidget::item:selected {
                background-color: rgba(124, 58, 237, 0.25);
                color: #ffffff;
            }
        """)
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        h_content.addWidget(self.list_widget, stretch=2)

        self.txt_item = QPlainTextEdit(self)
        self.txt_item.setPlaceholderText(self.placeholder)
        self.txt_item.setFixedHeight(64)
        self.txt_item.setStyleSheet("""
            QPlainTextEdit {
                background-color: #09090b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 4px;
                font-size: 11px;
            }
            QPlainTextEdit:focus {
                border-color: #7c3aed;
            }
        """)
        self.txt_item.setEnabled(False)
        self.txt_item.textChanged.connect(self._on_item_text_edited)
        h_content.addWidget(self.txt_item, stretch=3)

        layout.addLayout(h_content)

    def _sync_editor_to_current_item(self):
        curr_row = self.list_widget.currentRow()
        if 0 <= curr_row < len(self._items):
            self._items[curr_row] = self.txt_item.toPlainText()

    def get_items(self) -> List[str]:
        self._sync_editor_to_current_item()
        return list(self._items)

    def set_items(self, items: Sequence[str]):
        self._is_updating_ui = True
        try:
            self._items = list(items)
            self.list_widget.clear()
            for idx, text in enumerate(self._items):
                # Hiển thị tóm tắt một dòng trên danh sách
                summary = text.replace("\n", " ↵ ")
                display_label = summary if summary else "(mục rỗng)"
                self.list_widget.addItem(QListWidgetItem(f"#{idx+1}: {display_label}"))

            if self._items:
                self.list_widget.setCurrentRow(0)
                self.txt_item.setEnabled(True)
                self.txt_item.setPlainText(self._items[0])
            else:
                self.txt_item.setPlainText("")
                self.txt_item.setEnabled(False)
        finally:
            self._is_updating_ui = False

    def _on_selection_changed(self, row: int):
        if self._is_updating_ui:
            return
        if 0 <= row < len(self._items):
            self._is_updating_ui = True
            try:
                self.txt_item.setEnabled(True)
                self.txt_item.setPlainText(self._items[row])
            finally:
                self._is_updating_ui = False
        else:
            self.txt_item.setEnabled(False)
            self._is_updating_ui = True
            try:
                self.txt_item.setPlainText("")
            finally:
                self._is_updating_ui = False

    def _on_item_text_edited(self):
        if self._is_updating_ui:
            return
        curr_row = self.list_widget.currentRow()
        if 0 <= curr_row < len(self._items):
            new_text = self.txt_item.toPlainText()
            self._items[curr_row] = new_text
            summary = new_text.replace("\n", " ↵ ")
            display_label = summary if summary else "(mục rỗng)"
            item_widget = self.list_widget.item(curr_row)
            if item_widget:
                item_widget.setText(f"#{curr_row+1}: {display_label}")
            self.items_changed.emit()

    def _on_btn_add_clicked(self):
        new_text = ""
        self._items.append(new_text)
        new_row = len(self._items) - 1
        item_widget = QListWidgetItem(f"#{new_row+1}: (mục rỗng)")
        self.list_widget.addItem(item_widget)
        self.list_widget.setCurrentRow(new_row)
        self.txt_item.setEnabled(True)
        self.txt_item.setFocus()
        self.items_changed.emit()

    def _on_btn_remove_clicked(self):
        curr_row = self.list_widget.currentRow()
        if 0 <= curr_row < len(self._items):
            self._items.pop(curr_row)
            self._is_updating_ui = True
            try:
                self.list_widget.takeItem(curr_row)
                # Đánh lại số thứ tự
                for i in range(self.list_widget.count()):
                    raw = self._items[i]
                    summary = raw.replace("\n", " ↵ ")
                    self.list_widget.item(i).setText(f"#{i+1}: {summary if summary else '(mục rỗng)'}")
                if self._items:
                    next_row = min(curr_row, len(self._items) - 1)
                    self.list_widget.setCurrentRow(next_row)
                    self.txt_item.setPlainText(self._items[next_row])
                else:
                    self.txt_item.setPlainText("")
                    self.txt_item.setEnabled(False)
            finally:
                self._is_updating_ui = False
            self.items_changed.emit()


class CreativeIntakePanel(QFrame):
    """Bảng nhập ngữ cảnh sáng tạo & tài liệu tham chiếu (D03).
    
    Quản lý:
    - free_brief: Tóm tắt ý đồ dựng tự do (không tự tiện .strip()).
    - target_duration_sec: Thời lượng mục tiêu tùy chọn (hỗ trợ số thực, ký hiệu khoa học 1e-07, 1e20).
    - must_keep_rules / must_not_rules: Quy tắc ngữ nghĩa biên tập theo từng mục độc lập.
    - reference_items: Tài liệu tham chiếu cục bộ (role=reference_only, ID ổn định, ghi chú an toàn).
    - reference_urls: Liên kết tham chiếu (lưu metadata, chưa tự duyệt link).
    """
    draft_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_loading = False
        self._reference_items: List[Dict[str, Any]] = []
        self._has_rejection_error = False
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame#creative_intake_frame {
                background-color: #131316;
                border: 1px solid #27272a;
                border-radius: 8px;
            }
        """)
        self.setObjectName("creative_intake_frame")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # Header
        h_header = QVBoxLayout()
        h_header.setSpacing(2)
        lbl_title = QLabel("Ngữ cảnh sáng tạo & Tài liệu tham chiếu (Tùy chọn)")
        lbl_title.setStyleSheet("color: #ffffff; font-size: 12.5px; font-weight: bold;")
        h_header.addWidget(lbl_title)

        lbl_subtitle = QLabel(
            "Bản thảo yêu cầu biên tập gửi AI. Chưa gửi AI hoặc can thiệp timeline Resolve."
        )
        lbl_subtitle.setStyleSheet("color: #71717a; font-size: 11px;")
        h_header.addWidget(lbl_subtitle)
        layout.addLayout(h_header)

        # Inline Error Label
        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet("""
            QLabel {
                color: #f87171;
                background-color: rgba(248, 113, 113, 0.12);
                border: 1px solid rgba(248, 113, 113, 0.4);
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: 500;
            }
        """)
        self.lbl_error.setWordWrap(True)
        self.lbl_error.hide()
        layout.addWidget(self.lbl_error)

        # 1. Free Brief & Target Duration
        h_brief_dur = QHBoxLayout()
        h_brief_dur.setSpacing(12)

        # Brief column
        v_brief = QVBoxLayout()
        v_brief.setSpacing(4)
        lbl_brief = QLabel("Tóm tắt ý đồ dựng (Free Brief):")
        lbl_brief.setStyleSheet("color: #a1a1aa; font-size: 11px; font-weight: 600;")
        v_brief.addWidget(lbl_brief)

        self.txt_brief = QPlainTextEdit(self)
        self.txt_brief.setPlaceholderText("Nhập tóm tắt câu chuyện, phong cách cá nhân, lưu ý nhịp điệu tự do...")
        self.txt_brief.setFixedHeight(68)
        self.txt_brief.setStyleSheet("""
            QPlainTextEdit {
                background-color: #09090b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 6px;
                font-size: 11.5px;
            }
            QPlainTextEdit:focus {
                border-color: #7c3aed;
            }
        """)
        self.txt_brief.textChanged.connect(self._on_field_changed)
        v_brief.addWidget(self.txt_brief)
        h_brief_dur.addLayout(v_brief, stretch=3)

        # Duration column
        v_dur = QVBoxLayout()
        v_dur.setSpacing(4)
        self.chk_duration = QCheckBox("Thời lượng mục tiêu (giây):", self)
        self.chk_duration.setStyleSheet("color: #a1a1aa; font-size: 11px; font-weight: 600;")
        self.chk_duration.toggled.connect(self._on_duration_toggled)
        v_dur.addWidget(self.chk_duration)

        self.txt_duration = QLineEdit(self)
        self.txt_duration.setPlaceholderText("Ví dụ: 60 hoặc 45.5")
        self.txt_duration.setEnabled(False)
        self.txt_duration.setFixedHeight(28)
        self.txt_duration.setStyleSheet("""
            QLineEdit {
                background-color: #09090b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 0 8px;
                font-size: 11.5px;
            }
            QLineEdit:disabled {
                background-color: #18181b;
                color: #52525b;
                border-color: #27272a;
            }
            QLineEdit:focus {
                border-color: #7c3aed;
            }
        """)
        self.txt_duration.textChanged.connect(self._on_field_changed)
        v_dur.addWidget(self.txt_duration)
        v_dur.addStretch(1)
        h_brief_dur.addLayout(v_dur, stretch=1)

        layout.addLayout(h_brief_dur)

        # 2. Quy tắc ngữ nghĩa độc lập từng mục (Must Keep & Must Not)
        h_rules = QHBoxLayout()
        h_rules.setSpacing(12)

        self.editor_must_keep = StringListEditor(
            "Quy tắc bắt buộc giữ:",
            "Nhập nội dung quy tắc giữ...",
            self
        )
        self.editor_must_keep.items_changed.connect(self._on_field_changed)
        h_rules.addWidget(self.editor_must_keep)

        self.editor_must_not = StringListEditor(
            "Quy tắc cấm / loại bỏ:",
            "Nhập nội dung quy tắc loại bỏ...",
            self
        )
        self.editor_must_not.items_changed.connect(self._on_field_changed)
        h_rules.addWidget(self.editor_must_not)

        layout.addLayout(h_rules)

        # 3. Tài liệu tham chiếu cục bộ (Reference Items)
        v_refs = QVBoxLayout()
        v_refs.setSpacing(4)

        h_ref_top = QHBoxLayout()
        lbl_refs = QLabel("Tài liệu tham chiếu cục bộ (role=reference_only, chỉ tham chiếu phong cách):")
        lbl_refs.setStyleSheet("color: #a1a1aa; font-size: 11px; font-weight: 600;")
        h_ref_top.addWidget(lbl_refs, stretch=1)

        self.btn_add_ref = QPushButton("Thêm tham chiếu...", self)
        self.btn_add_ref.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_ref.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
        """)
        self.btn_add_ref.clicked.connect(self._on_btn_add_ref_clicked)
        h_ref_top.addWidget(self.btn_add_ref)

        self.btn_remove_ref = QPushButton("Xóa", self)
        self.btn_remove_ref.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remove_ref.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #f87171;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
        """)
        self.btn_remove_ref.clicked.connect(self.remove_selected_reference)
        h_ref_top.addWidget(self.btn_remove_ref)
        v_refs.addLayout(h_ref_top)

        self.list_refs = QListWidget(self)
        self.list_refs.setFixedHeight(68)
        self.list_refs.setStyleSheet("""
            QListWidget {
                background-color: #09090b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 2px;
                font-size: 11px;
            }
            QListWidget::item {
                padding: 3px 6px;
                border-radius: 4px;
            }
            QListWidget::item:selected {
                background-color: rgba(124, 58, 237, 0.25);
                color: #ffffff;
            }
        """)
        self.list_refs.currentRowChanged.connect(self._on_ref_selection_changed)
        v_refs.addWidget(self.list_refs)

        # Note field for selected reference
        h_ref_note = QHBoxLayout()
        h_ref_note.setSpacing(6)
        lbl_ref_note = QLabel("Ghi chú tham chiếu:")
        lbl_ref_note.setStyleSheet("color: #71717a; font-size: 10.5px;")
        h_ref_note.addWidget(lbl_ref_note)

        self.txt_ref_note = QLineEdit(self)
        self.txt_ref_note.setPlaceholderText("Ghi chú ý đồ (ví dụ: Tham khảo cách chuyển cảnh, tone màu...)")
        self.txt_ref_note.setEnabled(False)
        self.txt_ref_note.setFixedHeight(26)
        self.txt_ref_note.setStyleSheet("""
            QLineEdit {
                background-color: #09090b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 4px;
                padding: 0 6px;
                font-size: 11px;
            }
            QLineEdit:disabled {
                background-color: #18181b;
                color: #52525b;
            }
            QLineEdit:focus {
                border-color: #7c3aed;
            }
        """)
        self.txt_ref_note.textChanged.connect(self._on_ref_note_edited)
        h_ref_note.addWidget(self.txt_ref_note, stretch=1)
        v_refs.addLayout(h_ref_note)

        layout.addLayout(v_refs)

        # 4. Liên kết tham chiếu (Reference URLs - per-item)
        self.editor_urls = StringListEditor(
            "Liên kết tham chiếu (Reference URLs):",
            "https://example.com/video-tham-chieu",
            self
        )
        self.editor_urls.items_changed.connect(self._on_field_changed)
        layout.addWidget(self.editor_urls)

        lbl_disclaimer = QLabel("⚠️ Link được lưu trữ dưới dạng metadata; ứng dụng chưa tự xem hoặc phân tích nội dung link.")
        lbl_disclaimer.setStyleSheet("color: #71717a; font-size: 10.5px; font-style: italic;")
        layout.addWidget(lbl_disclaimer)

    def _on_field_changed(self):
        if not self._is_loading:
            self.draft_changed.emit()

    def _on_duration_toggled(self, checked: bool):
        self.txt_duration.setEnabled(checked)
        if not checked:
            self.txt_duration.setText("")
        if not self._is_loading:
            self.draft_changed.emit()

    def _on_btn_add_ref_clicked(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Chọn tệp ảnh hoặc video tham chiếu",
            "",
            "Media Files (*.mp4 *.mov *.mkv *.avi *.webm *.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not paths:
            return
        self.add_reference_paths(paths)

    def add_reference_paths(self, paths: Sequence[str]) -> Dict[str, Any]:
        """Thêm hàng loạt tệp tham chiếu với tổng hợp kết quả chấp nhận/từ chối minh bạch.
        
        Quy định:
        - Báo cáo rõ ràng số lượng tệp được chấp nhận và số lượng tệp bị từ chối kèm lý do.
        - Thứ tự tệp không làm che giấu các tệp bị từ chối (dù rejected trước valid sau hay ngược lại).
        - Nếu có bất kỳ tệp nào bị từ chối: hiển thị thông báo lỗi/cảnh báo bền vững trên lbl_error.
        - Nếu toàn bộ tệp hợp lệ: xóa thông báo lỗi.
        """
        accepted = []
        rejected = []
        for p in paths:
            if not p:
                continue
            if len(self._reference_items) >= MAX_REFERENCE_ITEMS:
                rejected.append((p, f"vượt quá giới hạn tối đa {MAX_REFERENCE_ITEMS} tệp"))
                continue

            abs_p = os.path.abspath(p)
            _, ext = os.path.splitext(abs_p)
            ext_l = ext.lower()
            if ext_l in RECOGNIZED_IMAGE_EXTS:
                media_type = "image"
            elif ext_l in RECOGNIZED_VIDEO_EXTS:
                media_type = "video"
            else:
                rejected.append((p, f"định dạng '{ext}' không được hỗ trợ"))
                continue

            ref_id = f"ref_{uuid.uuid4().hex[:8]}"
            item_data = {
                "reference_id": ref_id,
                "local_path": abs_p,
                "media_type": media_type,
                "role": VALID_REFERENCE_ROLE,
                "user_note": "",
            }
            self._reference_items.append(item_data)
            accepted.append(item_data)

        self._sync_refs_list()

        if rejected:
            self._has_rejection_error = True
            rej_summary = "; ".join(f"{os.path.basename(p)} ({reason})" for p, reason in rejected)
            if accepted:
                self.set_error(f"Đã thêm {len(accepted)} tệp; từ chối {len(rejected)} tệp ({rej_summary})")
            else:
                self.set_error(f"Từ chối toàn bộ {len(rejected)} tệp ({rej_summary})")
        else:
            if accepted:
                self._has_rejection_error = False
                self.set_error(None)

        if accepted and not self._is_loading:
            self.draft_changed.emit()

        return {
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "accepted": accepted,
            "rejected": rejected,
        }

    def add_reference_path(
        self,
        path: str,
        media_type: Optional[str] = None,
        user_note: str = "",
        reference_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Thêm đường dẫn tham chiếu cục bộ với nhận diện extension nghiêm ngặt."""
        # 1. Kiểm tra giới hạn 64 tài liệu tham chiếu
        if len(self._reference_items) >= MAX_REFERENCE_ITEMS:
            self._has_rejection_error = True
            self.set_error(f"Đã đạt giới hạn tối đa {MAX_REFERENCE_ITEMS} tài liệu tham chiếu.")
            return None

        # 2. Kiểm tra trùng lặp ID nếu có chỉ định ID
        if reference_id and any(r["reference_id"] == reference_id for r in self._reference_items):
            self._has_rejection_error = True
            self.set_error(f"ERR_DUPLICATE_REFERENCE_ID: reference_id {reference_id!r} đã tồn tại.")
            return None

        abs_path = os.path.abspath(path)
        _, ext = os.path.splitext(abs_path)
        ext_lower = ext.lower()

        # 3. Nhận diện media_type nghiêm ngặt (không tự tiện fallback video cho file lạ như .pdf/.zip)
        inferred_type = media_type
        if inferred_type is None:
            if ext_lower in RECOGNIZED_IMAGE_EXTS:
                inferred_type = "image"
            elif ext_lower in RECOGNIZED_VIDEO_EXTS:
                inferred_type = "video"
            else:
                self._has_rejection_error = True
                self.set_error(
                    f"Định dạng tệp không được hỗ trợ: '{ext}'. "
                    f"Chỉ chấp nhận ảnh hoặc video."
                )
                return None
        else:
            if inferred_type not in ("image", "video"):
                self._has_rejection_error = True
                self.set_error(f"media_type không hợp lệ: '{inferred_type}'. Chỉ nhận 'image' hoặc 'video'.")
                return None

        ref_id = reference_id or f"ref_{uuid.uuid4().hex[:8]}"
        item_data = {
            "reference_id": ref_id,
            "local_path": abs_path,
            "media_type": inferred_type,
            "role": VALID_REFERENCE_ROLE,
            "user_note": user_note,
        }
        self._reference_items.append(item_data)
        self._sync_refs_list()
        if not getattr(self, "_has_rejection_error", False):
            self.set_error(None)
        if not self._is_loading:
            self.draft_changed.emit()
        return item_data

    def remove_selected_reference(self) -> None:
        """Xóa mục tham chiếu đang được chọn trong danh sách."""
        curr_row = self.list_refs.currentRow()
        if 0 <= curr_row < len(self._reference_items):
            self._reference_items.pop(curr_row)
            self._sync_refs_list()
            if not self._is_loading:
                self.draft_changed.emit()

    def _on_ref_selection_changed(self, row: int):
        if 0 <= row < len(self._reference_items):
            self.txt_ref_note.setEnabled(True)
            self.txt_ref_note.blockSignals(True)
            self.txt_ref_note.setText(self._reference_items[row].get("user_note", ""))
            self.txt_ref_note.blockSignals(False)
        else:
            self.txt_ref_note.setEnabled(False)
            self.txt_ref_note.blockSignals(True)
            self.txt_ref_note.setText("")
            self.txt_ref_note.blockSignals(False)

    def _on_ref_note_edited(self, text: str):
        """Cập nhật ghi chú trực tiếp dạng chuỗi an toàn trong UI, không để lỗi lọt qua Qt slot."""
        curr_row = self.list_refs.currentRow()
        if 0 <= curr_row < len(self._reference_items):
            self._reference_items[curr_row]["user_note"] = text
            self._update_ref_item_text(curr_row, self._reference_items[curr_row])
            if not self._is_loading:
                self.draft_changed.emit()

    def _update_ref_item_text(self, row: int, item_data: Dict[str, Any]):
        item_widget = self.list_refs.item(row)
        if item_widget:
            tag = "[ẢNH]" if item_data.get("media_type") == "image" else "[VIDEO]"
            basename = os.path.basename(item_data.get("local_path", "")) or item_data.get("local_path", "")
            note_raw = item_data.get("user_note", "")
            # Làm sạch hiển thị an toàn nếu có ký tự lạ
            note_clean = note_raw.replace("\0", "\\0")
            note_str = f" - '{note_clean}'" if note_raw else ""
            item_widget.setText(f"{tag} {basename} ({item_data.get('reference_id')}){note_str}")

    def _sync_refs_list(self):
        self.list_refs.blockSignals(True)
        self.list_refs.clear()
        for idx, item in enumerate(self._reference_items):
            tag = "[ẢNH]" if item.get("media_type") == "image" else "[VIDEO]"
            basename = os.path.basename(item.get("local_path", "")) or item.get("local_path", "")
            note_raw = item.get("user_note", "")
            note_clean = note_raw.replace("\0", "\\0")
            note_str = f" - '{note_clean}'" if note_raw else ""
            item_widget = QListWidgetItem(f"{tag} {basename} ({item.get('reference_id')}){note_str}")
            self.list_refs.addItem(item_widget)
        self.list_refs.blockSignals(False)

        if self._reference_items:
            self.list_refs.setCurrentRow(len(self._reference_items) - 1)
        else:
            self._on_ref_selection_changed(-1)

    def set_error(self, message: Optional[str]) -> None:
        """Hiển thị hoặc ẩn thông báo lỗi inline tiếng Việt."""
        if message:
            self.lbl_error.setText(message)
            self.lbl_error.show()
        else:
            self.lbl_error.setText("")
            self.lbl_error.hide()

    def get_draft(self) -> Dict[str, Any]:
        """Thu thập bản thảo thô hiện tại từ các trường nhập liệu trên giao diện."""
        # Không tự tiện .strip() nội dung của người dùng!
        brief = self.txt_brief.toPlainText()
        keep_rules = self.editor_must_keep.get_items()
        not_rules = self.editor_must_not.get_items()

        dur_val = None
        if self.chk_duration.isChecked():
            raw_dur = self.txt_duration.text()
            dur_val = parse_duration_text(raw_dur)

        # Cập nhật note hiện tại từ ô txt_ref_note nếu có mục đang chọn
        curr_ref_row = self.list_refs.currentRow()
        if 0 <= curr_ref_row < len(self._reference_items):
            self._reference_items[curr_ref_row]["user_note"] = self.txt_ref_note.text()

        refs = [
            {
                "reference_id": r["reference_id"],
                "local_path": r["local_path"],
                "media_type": r["media_type"],
                "role": r["role"],
                "user_note": r["user_note"],
            }
            for r in self._reference_items
        ]

        urls = self.editor_urls.get_items()

        return {
            "free_brief": brief,
            "target_duration_sec": dur_val,
            "must_keep_rules": keep_rules,
            "must_not_rules": not_rules,
            "reference_items": refs,
            "reference_urls": urls,
        }

    def load_from_dict(self, data: Any) -> bool:
        """Nạp dữ liệu canonical/dict vào các control UI với thẩm định transactional trước khi ghi UI.
        
        Quy định:
        - Thẩm định toàn bộ candidate thông qua normalize_creative_context TRƯỚC KHI chạm vào bất kỳ control nào.
        - Empty {} hoặc dict rỗng là hợp lệ (nạp default empty context và trả về True).
        - Nếu data là bool, non-mapping, chứa unknown keys, hoặc trường dữ liệu sai kiểu (ví dụ free_brief=123,
          bad reference role/ID, malformed sequences...):
          Bác bỏ có kiểm soát, hiển thị lỗi tiếng Việt inline trên lbl_error, GIỮ NGUYÊN trạng thái UI/drafts,
          và trả về False.
        - Không str-coercion, không drop malformed refs, không tự sinh UUID ngẫu nhiên khi nạp.
        - Khi nạp thành công: cập nhật controls, xóa lỗi, trả về True.
        """
        if isinstance(data, bool) or not isinstance(data, (dict, Mapping)):
            self.set_error(f"Dữ liệu nạp không hợp lệ (yêu cầu dictionary/mapping, nhận: {type(data).__name__})")
            return False

        try:
            # normalize_creative_context thẩm định toàn diện: unknown keys, kiểu dữ liệu, bounds, byte budget
            normalized = normalize_creative_context(data)
            canonical = creative_context_to_dict(normalized)
        except Exception as e:
            self.set_error(f"Không thể nạp cấu hình: {e}")
            return False

        self._has_rejection_error = False
        self._is_loading = True
        try:
            self.set_error(None)
            # 1. Brief nguyên bản (bảo toàn 100% chuỗi chuẩn tắc)
            self.txt_brief.setPlainText(canonical["free_brief"])

            # 2. Duration
            dur = canonical["target_duration_sec"]
            if dur is not None:
                self.chk_duration.setChecked(True)
                self.txt_duration.setEnabled(True)
                self.txt_duration.setText(str(dur))
            else:
                self.chk_duration.setChecked(False)
                self.txt_duration.setEnabled(False)
                self.txt_duration.setText("")

            # 3. Rules theo từng mục (không str coercion trên dữ liệu thô)
            self.editor_must_keep.set_items(list(canonical["must_keep_rules"]))
            self.editor_must_not.set_items(list(canonical["must_not_rules"]))

            # 4. References (sử dụng canonical items đã được kiểm chứng ID/role/path/type/note)
            self._reference_items.clear()
            for r in canonical["reference_items"]:
                item_dict = {
                    "reference_id": r["reference_id"],
                    "local_path": r["local_path"],
                    "media_type": r["media_type"],
                    "role": r["role"],
                    "user_note": r["user_note"],
                }
                self._reference_items.append(item_dict)
            self._sync_refs_list()

            # 5. URLs
            self.editor_urls.set_items(list(canonical["reference_urls"]))
            return True
        finally:
            self._is_loading = False


class StylePage(QWidget):
    """Trang Bước 2: Kiểu dựng."""
    style_changed = Signal(str)
    advanced_toggle_requested = Signal()
    config_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_loading = False
        self._in_style_transition = False
        self._explicit_preset_selected = False
        self._loaded_preset = ""

        # Quản lý trạng thái nạp lỗi để bảo vệ target config không bị ghi đè âm thầm
        self._target_load_failed: bool = False
        self._target_load_error: Optional[str] = None

        self.current_style = STYLE_VLOG
        self.current_footage = "Tự nhận diện"
        self.current_pacing = "Cân bằng"
        self.current_intensity = 2
        self.enabled_features: Set[str] = set()

        self._cards: Dict[str, StyleCard] = {}
        self._chips: Dict[str, FeatureChipButton] = {}
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ScrollArea bọc toàn bộ nội dung để đảm bảo truy cập mượt mà tại 1200x750 / 1280x800
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        # 1. 4 Thẻ Style
        lbl_h3 = QLabel("Bạn muốn dựng video kiểu gì?")
        lbl_h3.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        layout.addWidget(lbl_h3)

        grid_cards = QHBoxLayout()
        grid_cards.setSpacing(10)

        styles_def = [
            (STYLE_PODCAST, "Podcast, phỏng vấn", "Cắt khoảng lặng, lọc câu vấp, phụ đề", "🎙️"),
            (STYLE_SHORTS, "Shorts, TikTok 9:16", "Cắt viral 60s, reframe dọc, karaoke sub", "📱"),
            (STYLE_VLOG, "Vlog có Hook", "Teaser mở đầu, punch-in, B-roll, tua lặng", "🎬"),
            (STYLE_CUSTOM, "Tự chỉnh", "Mở toàn bộ thông số nâng cao", "⚙️")
        ]

        for sid, title, desc, icon in styles_def:
            card = StyleCard(sid, title, desc, icon, self)
            card.clicked.connect(self._on_style_selected)
            grid_cards.addWidget(card)
            self._cards[sid] = card

        layout.addLayout(grid_cards)

        # Label hiển thị preset tùy chỉnh đã lưu nếu không thuộc 4 thẻ mặc định
        self.lbl_unrecognized_preset = QLabel("")
        self.lbl_unrecognized_preset.setStyleSheet("color: #f59e0b; font-size: 11.5px; font-style: italic;")
        self.lbl_unrecognized_preset.hide()
        layout.addWidget(self.lbl_unrecognized_preset)

        # 2. Thiết lập nhanh
        lbl_quick = QLabel("Thiết lập nhanh")
        lbl_quick.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold; margin-top: 6px;")
        layout.addWidget(lbl_quick)

        # Hàng 1: Loại footage
        h_row1 = QHBoxLayout()
        h_row1.addWidget(QLabel("Loại footage:", styleSheet="color: #a1a1aa; font-size: 11.5px; min-width: 130px;"))
        self.seg_footage = SegmentedControl(
            ["Tự nhận diện", "Nói liên tục", "Hỗn hợp", "Du lịch"],
            self.current_footage, self
        )
        self.seg_footage.valueChanged.connect(self._on_footage_changed)
        h_row1.addWidget(self.seg_footage)
        h_row1.addStretch(1)
        layout.addLayout(h_row1)

        # Hàng 2: Nhịp dựng
        h_row2 = QHBoxLayout()
        h_row2.addWidget(QLabel("Nhịp dựng:", styleSheet="color: #a1a1aa; font-size: 11.5px; min-width: 130px;"))
        self.seg_pacing = SegmentedControl(
            ["Thong thả", "Cân bằng", "Nhanh"],
            self.current_pacing, self
        )
        self.seg_pacing.valueChanged.connect(self._on_pacing_changed)
        h_row2.addWidget(self.seg_pacing)
        h_row2.addStretch(1)
        layout.addLayout(h_row2)

        # Hàng 3: Mức cắt vấp & lặng
        h_row3 = QHBoxLayout()
        h_row3.addWidget(QLabel("Mức cắt vấp & lặng:", styleSheet="color: #a1a1aa; font-size: 11.5px; min-width: 130px;"))
        self.slider_intensity = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_intensity.setRange(1, 3)
        self.slider_intensity.setValue(self.current_intensity)
        self.slider_intensity.setFixedWidth(240)
        self.slider_intensity.valueChanged.connect(self._on_intensity_changed)
        h_row3.addWidget(self.slider_intensity)

        self.lbl_intensity = QLabel("Vừa")
        self.lbl_intensity.setStyleSheet("color: #f59e0b; font-size: 11.5px; font-weight: bold; min-width: 50px;")
        h_row3.addWidget(self.lbl_intensity)
        h_row3.addStretch(1)
        layout.addLayout(h_row3)

        # 3. Tính năng bật
        lbl_feats = QLabel("Tính năng bật")
        lbl_feats.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold; margin-top: 6px;")
        layout.addWidget(lbl_feats)

        h_chips = QHBoxLayout()
        h_chips.setSpacing(6)
        for feat in ALL_FEATURE_CHIPS:
            chip = FeatureChipButton(feat, False, self)
            chip.toggled_chip.connect(self._on_chip_toggled)
            h_chips.addWidget(chip)
            self._chips[feat] = chip
        h_chips.addStretch(1)
        layout.addLayout(h_chips)

        # 4. Bảng nhập CreativeIntakePanel
        self.panel_intake = CreativeIntakePanel(self)
        self.panel_intake.draft_changed.connect(self._on_intake_draft_changed)
        layout.addWidget(self.panel_intake)

        # Nút mở bảng nâng cao
        layout.addSpacing(6)
        self.btn_adv = QPushButton("Tùy chỉnh nâng cao ›", self)
        self.btn_adv.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_adv.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #f59e0b;
                border: none;
                font-size: 12px;
                font-weight: 600;
                text-align: left;
                padding: 4px 0;
            }
            QPushButton:hover {
                color: #fbbf24;
            }
        """)
        self.btn_adv.clicked.connect(self.advanced_toggle_requested.emit)
        layout.addWidget(self.btn_adv)

        layout.addStretch(1)
        self.scroll_area.setWidget(content_widget)
        main_layout.addWidget(self.scroll_area)

        self._select_style(self.current_style)

    def _on_style_selected(self, style_id: str):
        self._explicit_preset_selected = True
        self.lbl_unrecognized_preset.hide()
        self._in_style_transition = True
        try:
            self._select_style(style_id)
            if style_id == STYLE_CUSTOM:
                self.advanced_toggle_requested.emit()
            self.style_changed.emit(style_id)
            self.config_changed.emit()
        finally:
            self._in_style_transition = False

    def _select_style(self, style_id: str):
        self.current_style = style_id
        for sid, card in self._cards.items():
            card.set_selected(sid == style_id)

    def _on_footage_changed(self, val: str):
        self.current_footage = val
        if not self._is_loading:
            self.config_changed.emit()

    def _on_pacing_changed(self, val: str):
        self.current_pacing = val
        if not self._is_loading:
            self.config_changed.emit()

    def _on_intensity_changed(self, val: int):
        self.current_intensity = val
        labels = {1: "Nhẹ", 2: "Vừa", 3: "Mạnh"}
        self.lbl_intensity.setText(labels.get(val, "Vừa"))
        if not self._is_loading:
            self.config_changed.emit()

    def _on_chip_toggled(self, name: str, active: bool):
        if active:
            self.enabled_features.add(name)
        else:
            self.enabled_features.discard(name)
        if not self._is_loading:
            self.config_changed.emit()

    def _on_intake_draft_changed(self):
        if not self._is_loading:
            self.config_changed.emit()

    def load_from_config(self, config: DirectorConfig) -> bool:
        """Nạp dữ liệu cấu hình vào giao diện theo nguyên tắc an toàn.
        
        Quy định:
        - Sử dụng signal guard (_is_loading) để không phát tán sự kiện thay đổi của người dùng.
        - Nếu đang trong chu kỳ callback _on_style_selected -> style_changed -> load_from_config,
          bảo toàn 100% bản thảo đang nhập dở (kể cả bản thảo chưa lưu hoặc không hợp lệ).
        - Khi nạp cấu hình có chủ đích từ ngoài: Thẩm định hợp lệ của config TRƯỚC KHI chỉnh sửa
          bất kỳ control hay trường nào trên UI.
        - Nếu config nguồn bị lỗi: Giữ nguyên toàn bộ nội dung người dùng trên UI, báo lỗi inline,
          kích hoạt cờ _target_load_failed và trả về False để ngăn chặn việc ghi đè âm thầm sau này.
        """
        # Nếu đang trong quá trình chuyển style preset từ UI, chỉ cập nhật legacy style parameters
        if self._in_style_transition:
            self._select_style(config.style)
            self.current_footage = config.footage_type
            self.seg_footage.set_value(config.footage_type)
            self.current_pacing = config.pacing
            self.seg_pacing.set_value(config.pacing)
            self.current_intensity = config.intensity
            self.slider_intensity.setValue(config.intensity)
            self._on_intensity_changed(config.intensity)

            self.enabled_features = set(config.enabled_features)
            for name, chip in self._chips.items():
                chip.set_active(name in self.enabled_features)
            return True

        # Nạp có chủ đích từ ngoài: Thẩm định trước để không phá hủy UI hiện có
        try:
            if hasattr(config, "get_creative_context"):
                incoming_ctx = config.get_creative_context()
            else:
                raw_dict = getattr(config, "creative_context_dict", None)
                if raw_dict is None or (isinstance(raw_dict, dict) and len(raw_dict) == 0):
                    incoming_ctx = normalize_creative_context({})
                else:
                    incoming_ctx = normalize_creative_context(raw_dict)
        except Exception as exc:
            self._target_load_failed = True
            self._target_load_error = f"Lỗi nạp cấu hình: {exc}"
            self.panel_intake.set_error(self._target_load_error)
            # Giữ nguyên toàn bộ dữ liệu editor UI cũ và cấu hình
            return False

        # Thẩm định thành công
        self._target_load_failed = False
        self._target_load_error = None
        self.panel_intake.set_error(None)

        self._is_loading = True
        try:
            if config.style in self._cards:
                self._select_style(config.style)
                self.lbl_unrecognized_preset.hide()
            else:
                for card in self._cards.values():
                    card.set_selected(False)
                self.current_style = config.style
                self.lbl_unrecognized_preset.setText(f"Preset đã lưu: {config.style}")
                self.lbl_unrecognized_preset.show()

            self.current_footage = config.footage_type
            self.seg_footage.set_value(config.footage_type)
            self.current_pacing = config.pacing
            self.seg_pacing.set_value(config.pacing)
            self.current_intensity = config.intensity
            self.slider_intensity.setValue(config.intensity)
            self._on_intensity_changed(config.intensity)

            self.enabled_features = set(config.enabled_features)
            for name, chip in self._chips.items():
                chip.set_active(name in self.enabled_features)

            self._explicit_preset_selected = False
            self._loaded_preset = incoming_ctx.style_preset
            if incoming_ctx.style_preset and incoming_ctx.style_preset not in self._cards:
                self.lbl_unrecognized_preset.setText(f"Preset đã lưu: {incoming_ctx.style_preset}")
                self.lbl_unrecognized_preset.show()

            panel_ok = self.panel_intake.load_from_dict(creative_context_to_dict(incoming_ctx))
            if not panel_ok:
                self._target_load_failed = True
                self._target_load_error = "CreativeIntakePanel từ chối nạp cấu hình không hợp lệ"
                return False
        finally:
            self._is_loading = False

        return True

    def save_to_config(self, config: DirectorConfig) -> bool:
        """Lưu toàn bộ cấu hình vào DirectorConfig theo nguyên tắc transactional.
        
        Quy định:
        - Nếu lần nạp trước bị lỗi (_target_load_failed=True), từ chối lưu đè lên config và trả về False.
        - Chuẩn bị và thẩm định candidate CreativeContext hoàn chỉnh TRƯỚC KHI chỉnh sửa bất kỳ
          trường nào của config.
        - Nếu input sáng tạo lỗi/vượt giới hạn: giữ nguyên toàn bộ config, giữ nguyên nội dung
          trên giao diện, hiển thị lỗi inline và trả về False.
        - Nếu hợp lệ: cập nhật các trường legacy và gán canonical creative_context_dict sạch,
          xóa thông báo lỗi và trả về True.
        """
        if self._target_load_failed:
            self.panel_intake.set_error(
                f"Không thể lưu đè: Cấu hình nguồn đang bị lỗi ({self._target_load_error}). "
                "Vui lòng nạp lại cấu hình hợp lệ trước khi lưu."
            )
            return False

        # Xác định style_preset chuẩn tắc cần lưu
        if self._explicit_preset_selected:
            chosen_preset = self.current_style
        elif self._loaded_preset:
            chosen_preset = self._loaded_preset
        else:
            chosen_preset = self.current_style

        # Lấy bản thảo từ panel
        raw_draft = self.panel_intake.get_draft()
        raw_draft["style_preset"] = chosen_preset

        # Thẩm định an toàn trước khi gán
        try:
            normalized = normalize_creative_context(raw_draft)
            canonical_dict = creative_context_to_dict(normalized)
        except Exception as exc:
            self.panel_intake.set_error(f"Lỗi nhập liệu: {exc}")
            return False

        # Thẩm định thành công: xóa lỗi và ghi nhận vào config
        self.panel_intake.set_error(None)

        config.style = self.current_style
        config.footage_type = self.current_footage
        config.pacing = self.current_pacing
        config.intensity = self.current_intensity
        config.enabled_features = set(self.enabled_features)
        config.creative_context_dict = canonical_dict

        return True
