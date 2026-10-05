"""
Module Quản lý Theme, Bảng màu và Định kiểu Giao diện (Design System) cho ResolveFlow Assistant.
Đồng bộ 100% với Design Tokens từ src/ui/resolveflow_ui.html (Bảng màu Violet & Cyan Dark Theme).
"""

from typing import Dict


class ThemeColors:
    # 1. Backgrounds (Nền)
    BG_CANVAS = "#131316"       # --bg-0 (Màn hình desktop / Canvas tối)
    BG_MAIN = "#18181b"         # --bg-1 (Nền chính của các cửa sổ)
    BG_CARD = "#1e1e24"         # --bg-2 (Nền thẻ Card, GroupBox, Panel)
    BG_INPUT = "#26262d"        # --bg-3 (Nền ô nhập liệu, button phụ)
    BG_CARD_ACTIVE = "#2f2f37"  # --bg-4 (Nền khi active / hover)
    BG_HEADER = "#18181b"       # Nền thanh tiêu đề
    BG_CONSOLE = "#0e0e11"      # Nền khung nhật ký AI Console

    # 2. Borders & Lines (Đường viền)
    BORDER_DEFAULT = "#27272a"  # --line (Viền mặc định)
    BORDER_HOVER = "#33333a"    # --line-2 (Viền khi rê chuột)
    BORDER_FOCUS = "#8b5cf6"    # --violet-hi (Viền khi focus)
    BORDER_LINE3 = "#45454e"    # --line-3 (Viền nổi)
    BORDER_ACTIVE = "#7c3aed"   # --violet (Viền kích hoạt)
    BORDER_ACTIVE_BLUE = "#06b6d4" # --cyan (Viền media / progress)

    # 3. Typography Colors (Màu chữ)
    TEXT_PRIMARY = "#ededf0"    # --text (Chữ chính)
    TEXT_SECONDARY = "#a8a8b3"  # --text-2 (Chữ phụ)
    TEXT_MUTED = "#74747f"      # --text-3 (Chữ mờ / chú thích)
    TEXT_ACCENT = "#8b5cf6"     # --violet-hi (Chữ điểm nhấn)
    TEXT_SUCCESS = "#86efac"    # Chữ xanh lá thành công
    TEXT_WARNING = "#fcd34d"    # Chữ vàng cảnh báo
    TEXT_ERROR = "#fca5a5"      # Chữ đỏ lỗi

    # 4. Brand & Action (Tím Violet)
    PRIMARY = "#7c3aed"         # --violet (Màu tím thương hiệu)
    PRIMARY_HOVER = "#8b5cf6"   # --violet-hi
    PRIMARY_PRESSED = "#6d28d9"
    PRIMARY_TEXT = "#ffffff"    # Màu chữ trên nút tím
    VIOLET = "#7c3aed"
    VIOLET_HI = "#8b5cf6"
    VIOLET_LO = "rgba(124, 58, 237, 0.16)"

    # 5. Media, Progress & Playhead (Xanh Cyan)
    CYAN = "#06b6d4"            # --cyan (Màu tiến độ, playhead)
    CYAN_HI = "#22d3ee"         # --cyan-hi
    CYAN_LO = "rgba(6, 182, 212, 0.15)"

    # 6. Status Colors (Trạng thái chức năng)
    GREEN = "#22c55e"
    AMBER = "#f59e0b"
    RED = "#ef4444"
    PINK = "#ec4899"

    SUCCESS = "#22c55e"
    SUCCESS_HOVER = "#16a34a"
    SUCCESS_PRESSED = "#15803d"
    SUCCESS_LIGHT = "#22d3ee"

    DANGER = "#ef4444"
    DANGER_HOVER = "#dc2626"
    DANGER_PRESSED = "#b91c1c"

    WARNING = "#f59e0b"
    WARNING_HOVER = "#d97706"


class ThemeFonts:
    FAMILY = "'Segoe UI Variable Text', 'Segoe UI', system-ui, -apple-system, sans-serif"
    FAMILY_MONO = "'Cascadia Mono', 'Consolas', 'Courier New', monospace"

    SIZE_TITLE = 16
    SIZE_SUBTITLE = 11
    SIZE_SECTION = 13
    SIZE_BODY = 12
    SIZE_SMALL = 11
    SIZE_BTN_RUN = 14


# Từ điển Tooltips & Giải thích thuật ngữ bằng ngôn ngữ đơn giản cho Editor / Creator
TOOLTIPS: Dict[str, str] = {
    # Workflow Mode
    "workflow_mode": "Chọn kiểu video bạn muốn làm để app tự động kích hoạt tổ hợp tính năng tối ưu nhất chỉ với 1 cú click.",
    "recipe": "Chọn hoặc lưu lại cấu hình cài đặt riêng của bạn để tái sử dụng nhanh chóng cho các dự án sau.",

    # Master Intensity
    "master_intensity": "Kéo thanh trượt để chỉnh nhanh mức độ cắt gọt: Nhẹ (giữ nhịp tự nhiên), Vừa (tiêu chuẩn), Mạnh (tiết tấu nhanh, dứt khoát).",

    # Modules
    "whisper_model": "Kích thước mô hình AI nhận diện giọng nói: Model càng lớn nhận diện càng chuẩn nhưng thời gian xử lý lâu hơn.",
    "language": "Ngôn ngữ trong video. Chọn 'Auto' để AI tự phát hiện hoặc chọn chính xác để tăng tốc độ xử lý.",
    "scan_cache": "Ghi nhớ kết quả nhận diện giọng nói từ lần trước. Nếu file không đổi, nạp lại ngay trong 0.05s mà không cần dịch lại.",

    "ai_mode": "Kịch bản AI: Lọc sạch nói vấp (Clean Talk), Trích xuất clip ngắn (Viral Shorts), Tóm tắt (Summary) hoặc Chỉ cắt im lặng cơ bản.",
    "bad_takes": "Tự động phát hiện khi bạn nói sai, vấp rồi thử nói lại câu đó, AI sẽ tự cắt bỏ các câu nói hỏng trước đó.",
    "punch_in": "Tự động phóng to nhẹ khung hình (1.15x) luân phiên giữa các câu thoại để tạo hiệu ứng như đang quay 2 góc máy.",
    "confidence_threshold": "Ngưỡng độ tin cậy của chữ nhận dạng (0.0 đến 1.0). Câu dưới ngưỡng chỉ được TÔ VÀNG trong bảng duyệt để bạn xem lại; KHÔNG bị tự cắt.",

    "vlog_hook": "Tự động tìm kiếm các câu thoại gay cấn/đắt giá nhất trong video và trích xuất làm đoạn giới thiệu Teaser 10-30s mở đầu.",
    "hook_duration": "Thời lượng trích xuất cho mỗi phân đoạn highlight trong đoạn giới thiệu Teaser.",

    "reframe": "Tự động phân tích và căn chỉnh khuôn mặt chủ thể vào giữa khung hình khi đổi từ video ngang 16:9 sang video dọc 9:16 (TikTok/Reels).",
    "broll": "Tự động quét nội dung câu thoại và gợi ý các từ khóa footage minh họa trên Track Video 2 để chèn cảnh minh họa.",
    "sfx": "Tự động chèn các hiệu ứng âm thanh (tiếng whoosh, pop, ding...) vào các cú chuyển cảnh và phóng to trên Track Audio 2.",

    "subtitles": "Tự động tạo phụ đề Text+ động vào Timeline DaVinci Resolve và xuất tệp phụ đề .SRT / .FCPXML.",
    "text_preset": "Chọn phong cách hiển thị phụ đề Text+ (Chữ nhảy động, Đổi màu theo từ Karaoke, Viền nét thanh lịch, Hiệu ứng Neon...).",
    "split_mode": "Cách ngắt câu phụ đề: Theo số ký tự (vừa mắt người đọc) hoặc Theo số từ (kiểu chữ ngắn năng động cho Shorts).",
    "split_limit": "Số ký tự hoặc số từ tối đa hiển thị trên 1 dòng phụ đề.",
    "font_name": "Tên phông chữ sẽ dùng để tạo phụ đề Text+ trong DaVinci Resolve.",
    "font_size": "Kích cỡ phông chữ phụ đề.",
    "font_color": "Màu sắc chính của chữ phụ đề (mã màu Hex, ví dụ #FFFFFF là màu trắng).",

    "silence_cut": "Tự động phát hiện và loại bỏ các khoảng im lặng không có tiếng nói để tiết tấu video gọn gàng, liền mạch.",
    "speedup_silence": "Tua nhanh các khoảng im lặng (8x) thay vì cắt bỏ hẳn, tạo hiệu ứng Timelapse lướt qua mượt mà.",
    "silence_db": "Mức âm lượng được coi là im lặng. Giá trị càng nhỏ (-42 dB) thì chỉ cắt những chỗ thật sự tĩnh lặng.",
    "min_duration": "Khoảng im lặng phải dài ít nhất bao nhiêu giây thì mới bị xử lý (giúp tránh cắt nhầm nhịp thở tự nhiên)."
}


# Mô tả ngắn 1 dòng cho từng module
MODULE_DESCRIPTIONS = {
    "whisper": "Chuyển giọng nói thành văn bản chuẩn xác với bộ nhớ đệm Scan Cache siêu tốc.",
    "director": "Tự động lọc câu nói vấp, thử lại và tạo hiệu ứng phóng to (Punch-in) đổi góc nhìn.",
    "vlog_hook": "Trích xuất câu thoại đắt giá nhất ở đầu clip tạo đoạn mở đầu 10-30s cuốn hút.",
    "visual_audio": "Tự động đổi tỷ lệ dọc 9:16 bám mặt, gợi ý B-Roll và chèn âm thanh SFX.",
    "subtitles": "Tạo phụ đề Text+ động theo từ/ký tự với nhiều phong cách bắt mắt.",
    "silence_cut": "Tự động loại bỏ khoảng im lặng hoặc tua nhanh 8x để video liền mạch."
}


def get_application_stylesheet() -> str:
    """Tạo bộ QSS Stylesheet hoàn chỉnh, đồng bộ toàn bộ ứng dụng."""
    return f"""
    /* --- TOÀN BỘ CỬA SỔ & WIDGET CƠ BẢN --- */
    QWidget {{
        background-color: {ThemeColors.BG_MAIN};
        color: {ThemeColors.TEXT_PRIMARY};
        font-family: {ThemeFonts.FAMILY};
        font-size: 13px;
        selection-background-color: {ThemeColors.PRIMARY};
        selection-color: #FFFFFF;
    }}

    QScrollArea {{
        background-color: transparent;
        border: none;
    }}

    QScrollBar:vertical {{
        background-color: transparent;
        width: 10px;
        margin: 0px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical {{
        background-color: #34343c;
        min-height: 30px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background-color: {ThemeColors.BORDER_LINE3};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}

    QScrollBar:horizontal {{
        background-color: transparent;
        height: 10px;
        margin: 0px;
        border-radius: 5px;
    }}
    QScrollBar::handle:horizontal {{
        background-color: #34343c;
        min-width: 30px;
        border-radius: 5px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background-color: {ThemeColors.BORDER_LINE3};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* --- CARDS & GROUPBOXES --- */
    QGroupBox {{
        background-color: {ThemeColors.BG_CARD};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 10px;
        margin-top: 14px;
        padding-top: 14px;
        padding-bottom: 10px;
        padding-left: 10px;
        padding-right: 10px;
        font-weight: 600;
        font-size: 12px;
        color: {ThemeColors.TEXT_PRIMARY};
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        top: -8px;
        padding: 0px 8px;
        background-color: {ThemeColors.BG_MAIN};
        color: {ThemeColors.TEXT_MUTED};
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}

    QGroupBox[active="true"] {{
        border: 1px solid {ThemeColors.BORDER_FOCUS};
        background-color: {ThemeColors.BG_CARD_ACTIVE};
    }}

    /* --- LABELS & TYPOGRAPHY --- */
    QLabel {{
        color: {ThemeColors.TEXT_PRIMARY};
        font-size: 12.5px;
    }}
    QLabel.section_desc {{
        color: {ThemeColors.TEXT_MUTED};
        font-size: 11.5px;
        font-weight: normal;
        margin-bottom: 6px;
    }}

    /* --- INPUTS & COMBOBOXES --- */
    QLineEdit, QComboBox {{
        background-color: {ThemeColors.BG_CANVAS};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 8px;
        padding: 6px 10px;
        color: {ThemeColors.TEXT_PRIMARY};
        font-size: 12.5px;
        min-height: 20px;
    }}
    QLineEdit:hover, QComboBox:hover {{
        border-color: {ThemeColors.BORDER_HOVER};
    }}
    QLineEdit:focus, QComboBox:focus {{
        border: 1px solid {ThemeColors.BORDER_FOCUS};
        background-color: {ThemeColors.BG_CARD};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border-left-width: 0px;
        border-top-right-radius: 8px;
        border-bottom-right-radius: 8px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {ThemeColors.BG_CARD};
        border: 1px solid {ThemeColors.BORDER_HOVER};
        border-radius: 8px;
        color: {ThemeColors.TEXT_PRIMARY};
        selection-background-color: {ThemeColors.PRIMARY};
        selection-color: #FFFFFF;
        outline: none;
        padding: 4px;
    }}

    /* --- CHECKBOXES & RADIO BUTTONS --- */
    QCheckBox, QRadioButton {{
        color: {ThemeColors.TEXT_PRIMARY};
        spacing: 8px;
        font-size: 12.5px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid {ThemeColors.BORDER_HOVER};
        background-color: {ThemeColors.BG_INPUT};
    }}
    QCheckBox::indicator:hover {{
        border-color: {ThemeColors.BORDER_FOCUS};
    }}
    QCheckBox::indicator:checked {{
        background-color: {ThemeColors.PRIMARY};
        border-color: {ThemeColors.PRIMARY_HOVER};
    }}

    QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 8px;
        border: 1px solid {ThemeColors.BORDER_HOVER};
        background-color: {ThemeColors.BG_INPUT};
    }}
    QRadioButton::indicator:hover {{
        border-color: {ThemeColors.BORDER_FOCUS};
    }}
    QRadioButton::indicator:checked {{
        background-color: {ThemeColors.PRIMARY};
        border-color: {ThemeColors.PRIMARY_HOVER};
    }}

    /* --- SLIDERS --- */
    QSlider::groove:horizontal {{
        border: none;
        height: 4px;
        background: {ThemeColors.BG_CARD_ACTIVE};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {ThemeColors.PRIMARY_HOVER};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: #FFFFFF;
        border: 3px solid {ThemeColors.PRIMARY_HOVER};
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: #FFFFFF;
        border: 3px solid {ThemeColors.PRIMARY};
    }}

    /* --- BUTTONS --- */
    QPushButton {{
        background-color: {ThemeColors.BG_INPUT};
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER_HOVER};
        border-radius: 8px;
        padding: 7px 14px;
        font-weight: 500;
        font-size: 12.5px;
        min-height: 20px;
    }}
    QPushButton:hover {{
        background-color: {ThemeColors.BG_CARD_ACTIVE};
        border-color: {ThemeColors.BORDER_LINE3};
    }}
    QPushButton:pressed {{
        background-color: {ThemeColors.BG_INPUT};
    }}
    QPushButton:disabled {{
        background-color: {ThemeColors.BG_INPUT};
        border-color: {ThemeColors.BORDER_DEFAULT};
        color: {ThemeColors.TEXT_MUTED};
    }}

    /* Nút chính Primary (Tím Violet) */
    QPushButton.primary, QPushButton#btn_primary {{
        background-color: {ThemeColors.PRIMARY};
        color: #FFFFFF;
        border: 1px solid {ThemeColors.PRIMARY_HOVER};
        font-weight: 600;
    }}
    QPushButton.primary:hover, QPushButton#btn_primary:hover {{
        background-color: {ThemeColors.PRIMARY_HOVER};
    }}
    QPushButton.primary:pressed, QPushButton#btn_primary:pressed {{
        background-color: {ThemeColors.PRIMARY_PRESSED};
    }}

    /* Nút Dừng Stop / Cancel */
    QPushButton.stop, QPushButton#btn_stop {{
        background-color: transparent;
        border: 1px solid rgba(239, 68, 68, 0.35);
        color: #fca5a5;
    }}
    QPushButton.stop:hover, QPushButton#btn_stop:hover {{
        background-color: rgba(239, 68, 68, 0.14);
        border-color: rgba(239, 68, 68, 0.6);
    }}

    /* --- PROGRESS BAR --- */
    QProgressBar {{
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 4px;
        text-align: center;
        background-color: {ThemeColors.BG_CARD_ACTIVE};
        color: #FFFFFF;
        font-weight: bold;
        font-size: 11px;
        height: 6px;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ThemeColors.PRIMARY_HOVER}, stop:1 {ThemeColors.CYAN_HI});
        border-radius: 4px;
    }}

    /* --- CONSOLE LOGS --- */
    QPlainTextEdit {{
        background-color: {ThemeColors.BG_CONSOLE};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 10px;
        font-family: {ThemeFonts.FAMILY_MONO};
        color: {ThemeColors.TEXT_SECONDARY};
        font-size: 11.5px;
        padding: 10px 12px;
        line-height: 1.6;
    }}

    /* --- TABLES --- */
    QTableWidget {{
        background-color: {ThemeColors.BG_CARD};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 8px;
        gridline-color: {ThemeColors.BORDER_DEFAULT};
        color: {ThemeColors.TEXT_PRIMARY};
    }}
    QHeaderView::section {{
        background-color: {ThemeColors.BG_INPUT};
        color: {ThemeColors.TEXT_SECONDARY};
        font-weight: 600;
        padding: 6px;
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
    }}
    QTableWidget::item:selected {{
        background-color: {ThemeColors.PRIMARY};
        color: #FFFFFF;
    }}

    /* --- TAB WIDGET & TAB BAR --- */
    QTabWidget::pane {{
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        background-color: transparent;
        border-radius: 8px;
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: transparent;
        color: {ThemeColors.TEXT_SECONDARY};
        border: none;
        padding: 10px 18px;
        margin-right: 4px;
        font-weight: 500;
        font-size: 12.5px;
        border-radius: 6px;
    }}
    QTabBar::tab:hover {{
        color: {ThemeColors.TEXT_PRIMARY};
        background-color: {ThemeColors.BG_CARD};
    }}
    QTabBar::tab:selected {{
        background-color: {ThemeColors.BG_INPUT};
        color: {ThemeColors.TEXT_PRIMARY};
        border-bottom: 2px solid {ThemeColors.PRIMARY_HOVER};
        font-weight: 600;
    }}

    /* --- SPLITTER --- */
    QSplitter::handle {{
        background-color: {ThemeColors.BORDER_DEFAULT};
        width: 1px;
        margin: 0px;
    }}
    QSplitter::handle:hover {{
        background-color: {ThemeColors.BORDER_FOCUS};
    }}

    /* --- TOOLTIPS --- */
    QToolTip {{
        background-color: #232329;
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER_LINE3};
        border-radius: 8px;
        padding: 6px 10px;
        font-size: 12px;
    }}

    /* --- CONTEXT MENUS --- */
    QMenu {{
        background-color: #1a1a1e;
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER_HOVER};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 20px;
        border-radius: 6px;
    }}
    QMenu::item:selected {{
        background-color: {ThemeColors.BG_INPUT};
        color: {ThemeColors.PRIMARY_HOVER};
    }}
    """
