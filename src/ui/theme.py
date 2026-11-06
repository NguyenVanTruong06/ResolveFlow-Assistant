"""
Module Quản lý Theme, Bảng màu và Định kiểu Giao diện (Design System) cho ResolveFlow Assistant.
Đồng bộ 100% với Design Tokens từ src/ui/resolveflow_ui.html (Bảng màu Violet & Cyan Dark Theme).
"""

from typing import Dict


class ThemeColors:
    # 1. Backgrounds (Nền chuẩn Studio NLE DaVinci Resolve 19 / Blackmagic Graphite)
    BG_STUDIO_DEEP = "#111214"  # --bg-deep (Nền sâu chuẩn bàn dựng NLE)
    BG_CANVAS = "#16171a"       # --bg-0 (Màn hình desktop / Canvas tối)
    BG_MAIN = "#1c1d21"         # --bg-1 (Nền chính của các cửa sổ - Matte Graphite trung tính)
    BG_CARD = "#23252a"         # --bg-2 (Nền thẻ Card, GroupBox, Panel)
    BG_INPUT = "#151619"        # --bg-3 (Nền ô nhập liệu âm bản chuẩn NLE)
    BG_CARD_ACTIVE = "#2c2f36"  # --bg-4 (Nền khi active / hover)
    BG_HEADER = "#18191c"       # Nền thanh tiêu đề Topbar
    BG_CONSOLE = "#101113"      # Nền khung nhật ký AI Console

    # 2. Borders & Lines (Đường viền chuẩn NLE 1px sắc nét)
    BORDER_SUBTLE = "#25272c"   # Viền phân tách mảnh nội bộ
    BORDER_DEFAULT = "#2e3138"  # --line (Viền mặc định chuẩn khung máy)
    BORDER_HOVER = "#434752"    # --line-2 (Viền khi rê chuột)
    BORDER_FOCUS = "#e05a47"    # --studio-coral (Viền khi focus)
    BORDER_LINE3 = "#525764"    # --line-3 (Viền nổi)
    BORDER_ACTIVE = "#d94f3d"   # --studio-coral (Viền kích hoạt)
    BORDER_ACTIVE_BLUE = "#38bdf8" # --steel-blue (Viền media / timecode)

    # 3. Typography Colors (Màu chữ độ tương phản chuẩn phòng dựng)
    TEXT_PRIMARY = "#e6e8ec"    # --text (Chữ chính trắng dịu mắt)
    TEXT_SECONDARY = "#9da3ae"  # --text-2 (Chữ phụ xám bạc)
    TEXT_MUTED = "#686e7a"      # --text-3 (Chữ mờ / chú thích)
    TEXT_ACCENT = "#f59e0b"     # --studio-amber (Chữ điểm nhấn tungsten)
    TEXT_SUCCESS = "#4ade80"    # Chữ xanh lá thành công
    TEXT_WARNING = "#fbbf24"    # Chữ vàng cảnh báo
    TEXT_ERROR = "#f87171"      # Chữ đỏ lỗi

    # 4. Brand & Action (DaVinci Resolve Studio Coral Red & Tungsten Amber)
    PRIMARY = "#d94f3d"         # Màu đỏ cam Studio Coral chuẩn nút hành động DaVinci Resolve
    PRIMARY_HOVER = "#e86352"   # Sáng hơn khi hover
    PRIMARY_PRESSED = "#b83f2f" # Đậm hơn khi nhấn
    PRIMARY_TEXT = "#ffffff"    # Màu chữ trên nút chính
    VIOLET = "#d94f3d"          # Alias tương thích ngược -> chuyển sang Studio Coral
    VIOLET_HI = "#e86352"       # Alias tương thích ngược -> chuyển sang Studio Coral Hi
    VIOLET_LO = "rgba(217, 79, 61, 0.14)"

    # 5. Media, Progress & Playhead (Precision Steel Blue)
    CYAN = "#0284c7"            # Màu tiến độ, playhead chuẩn NLE
    CYAN_HI = "#38bdf8"         # Steel Blue sáng
    CYAN_LO = "rgba(56, 189, 248, 0.14)"

    # 6. Status Colors (Trạng thái chức năng)
    GREEN = "#22c55e"
    AMBER = "#f59e0b"
    RED = "#ef4444"
    PINK = "#e11d48"

    SUCCESS = "#22c55e"
    SUCCESS_HOVER = "#16a34a"
    SUCCESS_PRESSED = "#15803d"
    SUCCESS_LIGHT = "#38bdf8"

    DANGER = "#dc2626"
    DANGER_HOVER = "#ef4444"
    DANGER_PRESSED = "#991b1b"

    ERROR = "#ef4444"
    ERROR_HOVER = "#dc2626"
    INFO = "#0284c7"
    INFO_LIGHT = "#38bdf8"

    WARNING = "#d97706"
    WARNING_HOVER = "#f59e0b"


class ThemeFonts:
    FAMILY = "'Geist', 'Inter', 'Outfit', 'Segoe UI Variable Text', 'Segoe UI', 'Ubuntu', 'Noto Sans', 'Cantarell', system-ui, sans-serif"
    FAMILY_MONO = "'JetBrains Mono', 'Geist Mono', 'Cascadia Mono', 'Fira Code', 'Consolas', 'DejaVu Sans Mono', monospace"

    SIZE_TITLE = 16
    SIZE_SUBTITLE = 12
    SIZE_SECTION = 13
    SIZE_BODY = 12.5
    SIZE_SMALL = 11.5
    SIZE_FLOOR = 11.5
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
        width: 6px;
        margin: 0px;
        border-radius: 3px;
    }}
    QScrollBar::handle:vertical {{
        background-color: #363942;
        min-height: 20px;
        border-radius: 3px;
    }}
    QScrollBar::handle:vertical:hover {{
        background-color: {ThemeColors.PRIMARY_HOVER};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}

    QScrollBar:horizontal {{
        background-color: transparent;
        height: 6px;
        margin: 0px;
        border-radius: 3px;
    }}
    QScrollBar::handle:horizontal {{
        background-color: #363942;
        min-width: 20px;
        border-radius: 3px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background-color: {ThemeColors.PRIMARY_HOVER};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}

    /* --- CARDS & GROUPBOXES --- */
    QGroupBox {{
        background-color: {ThemeColors.BG_CARD};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 5px;
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
        color: {ThemeColors.TEXT_SECONDARY};
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
        background-color: {ThemeColors.BG_INPUT};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 4px;
        padding: 5px 10px;
        color: {ThemeColors.TEXT_PRIMARY};
        font-size: 12.5px;
        min-height: 20px;
    }}
    QLineEdit:hover, QComboBox:hover {{
        border-color: {ThemeColors.BORDER_HOVER};
    }}
    QLineEdit:focus, QComboBox:focus {{
        border: 1px solid {ThemeColors.BORDER_FOCUS};
        background-color: #191b1f;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border-left-width: 0px;
        border-top-right-radius: 4px;
        border-bottom-right-radius: 4px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {ThemeColors.BG_CARD};
        border: 1px solid {ThemeColors.BORDER_HOVER};
        border-radius: 4px;
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
        width: 15px;
        height: 15px;
        border-radius: 3px;
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
        width: 15px;
        height: 15px;
        border-radius: 7px;
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
        background: {ThemeColors.BG_INPUT};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {ThemeColors.PRIMARY};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: #e6e8ec;
        border: 2px solid {ThemeColors.PRIMARY};
        width: 12px;
        height: 12px;
        margin: -4px 0;
        border-radius: 6px;
    }}
    QSlider::handle:horizontal:hover {{
        background: #FFFFFF;
        border: 2px solid {ThemeColors.PRIMARY_HOVER};
    }}

    /* --- BUTTONS --- */
    QPushButton {{
        background-color: #272930;
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid #353942;
        border-radius: 4px;
        padding: 6px 12px;
        font-weight: 600;
        font-size: 12px;
        min-height: 20px;
    }}
    QPushButton:hover {{
        background-color: #30333c;
        border-color: {ThemeColors.BORDER_HOVER};
    }}
    QPushButton:pressed {{
        background-color: {ThemeColors.BG_INPUT};
    }}
    QPushButton:disabled {{
        background-color: #191a1e;
        border-color: {ThemeColors.BORDER_SUBTLE};
        color: {ThemeColors.TEXT_MUTED};
    }}

    /* Nút chính Primary (DaVinci Studio Coral) */
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
        border: 1px solid rgba(239, 68, 68, 0.4);
        color: #f87171;
    }}
    QPushButton.stop:hover, QPushButton#btn_stop:hover {{
        background-color: rgba(239, 68, 68, 0.14);
        border-color: #ef4444;
    }}

    /* --- PROGRESS BAR --- */
    QProgressBar {{
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 3px;
        text-align: center;
        background-color: {ThemeColors.BG_INPUT};
        color: #FFFFFF;
        font-weight: bold;
        font-size: 11px;
        height: 6px;
    }}
    QProgressBar::chunk {{
        background-color: {ThemeColors.PRIMARY};
        border-radius: 2px;
    }}

    /* --- CONSOLE LOGS --- */
    QPlainTextEdit {{
        background-color: {ThemeColors.BG_CONSOLE};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 4px;
        font-family: {ThemeFonts.FAMILY_MONO};
        color: {ThemeColors.TEXT_SECONDARY};
        font-size: 11.5px;
        padding: 10px 12px;
        line-height: 1.6;
    }}

    /* --- TABLES (Chuẩn NLE DaVinci Resolve / Premiere Pro) --- */
    QTableWidget, QTableView {{
        background-color: {ThemeColors.BG_CANVAS};
        alternate-background-color: #191b1f;
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 4px;
        gridline-color: {ThemeColors.BORDER_SUBTLE};
        color: {ThemeColors.TEXT_PRIMARY};
        selection-background-color: rgba(217, 79, 61, 0.25);
        selection-color: #FFFFFF;
        outline: none;
    }}
    QHeaderView::section {{
        background-color: #1e2025;
        color: {ThemeColors.TEXT_SECONDARY};
        font-weight: 600;
        font-size: 11px;
        padding: 5px 8px;
        border: none;
        border-right: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-bottom: 1px solid {ThemeColors.BORDER_DEFAULT};
    }}
    QTableWidget::item:selected, QTableView::item:selected {{
        background-color: rgba(217, 79, 61, 0.28);
        color: #FFFFFF;
    }}
    QTableWidget::item:hover, QTableView::item:hover {{
        background-color: rgba(255, 255, 255, 0.03);
    }}

    /* --- TAB WIDGET & TAB BAR --- */
    QTabWidget::pane {{
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        background-color: transparent;
        border-radius: 4px;
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: transparent;
        color: {ThemeColors.TEXT_SECONDARY};
        border: none;
        padding: 8px 16px;
        margin-right: 2px;
        font-weight: 600;
        font-size: 12px;
        border-radius: 4px;
    }}
    QTabBar::tab:hover {{
        color: {ThemeColors.TEXT_PRIMARY};
        background-color: {ThemeColors.BG_CARD};
    }}
    QTabBar::tab:selected {{
        background-color: {ThemeColors.BG_CARD_ACTIVE};
        color: {ThemeColors.TEXT_PRIMARY};
        border-bottom: 2px solid {ThemeColors.PRIMARY};
        font-weight: 700;
    }}

    /* --- SPLITTER --- */
    QSplitter::handle:horizontal {{
        background-color: {ThemeColors.BORDER_SUBTLE};
        width: 2px;
        margin: 0px;
    }}
    QSplitter::handle:horizontal:hover {{
        background-color: {ThemeColors.PRIMARY_HOVER};
    }}
    QSplitter::handle:vertical {{
        background-color: {ThemeColors.BORDER_SUBTLE};
        height: 2px;
        margin: 0px;
    }}
    QSplitter::handle:vertical:hover {{
        background-color: {ThemeColors.PRIMARY_HOVER};
    }}

    /* --- SEMANTIC ROLES & PROPERTY SELECTORS --- */
    QFrame[role="card"] {{
        background-color: {ThemeColors.BG_CARD};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 5px;
    }}
    QFrame[role="card"]:hover {{
        border-color: {ThemeColors.BORDER_HOVER};
    }}

    QFrame[role="panel"] {{
        background-color: {ThemeColors.BG_MAIN};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 4px;
    }}

    QFrame[role="header"] {{
        background-color: {ThemeColors.BG_HEADER};
        border-bottom: 1px solid {ThemeColors.BORDER_DEFAULT};
    }}

    QPushButton[role="nav-switch"] {{
        background-color: #25272d;
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid #353841;
        border-radius: 4px;
        padding: 5px 12px;
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton[role="nav-switch"]:hover {{
        background-color: #2f323a;
        border-color: {ThemeColors.PRIMARY_HOVER};
        color: #ffffff;
    }}
    QPushButton[role="nav-switch"]:pressed {{
        background-color: {ThemeColors.BG_INPUT};
    }}

    /* --- HIGH-END CARDS & ISLAND BUTTONS --- */
    QFrame[role="card_nested"] {{
        background-color: {ThemeColors.BG_CARD};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 5px;
    }}
    QFrame[role="card_nested"]:hover {{
        border-color: {ThemeColors.BORDER_HOVER};
    }}

    QPushButton[role="btn_island"] {{
        background-color: {ThemeColors.PRIMARY};
        border: 1px solid {ThemeColors.PRIMARY_HOVER};
        color: #ffffff;
        border-radius: 4px;
        padding: 8px 16px;
        font-weight: 600;
        font-size: 12.5px;
    }}
    QPushButton[role="btn_island"]:hover {{
        background-color: {ThemeColors.PRIMARY_HOVER};
        border-color: #f07b6b;
    }}
    QPushButton[role="btn_island"]:pressed {{
        background-color: {ThemeColors.PRIMARY_PRESSED};
    }}

    /* --- TOOLTIPS --- */
    QToolTip {{
        background-color: #23252a;
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER_HOVER};
        border-radius: 4px;
        padding: 5px 8px;
        font-size: 11.5px;
    }}

    /* --- CONTEXT MENUS --- */
    QMenu {{
        background-color: #1c1d21;
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER_HOVER};
        border-radius: 4px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 18px;
        border-radius: 3px;
    }}
    QMenu::item:selected {{
        background-color: {ThemeColors.PRIMARY};
        color: #ffffff;
    }}
    """


def get_badge_style(color: str = ThemeColors.PRIMARY, bg_color: str = ThemeColors.VIOLET_LO, font_size: int = 11) -> str:
    """Tạo stylesheet đồng bộ cho các nhãn badge / pill."""
    return f"""
        color: {color};
        background-color: {bg_color};
        border-radius: 3px;
        padding: 2px 6px;
        font-size: {font_size}px;
        font-weight: 600;
    """


def get_card_style(active: bool = False, accent_color: str = None) -> str:
    """Tạo stylesheet đồng bộ cho thẻ QFrame/SectionCard."""
    bg = ThemeColors.BG_CARD_ACTIVE if active else ThemeColors.BG_CARD
    border = ThemeColors.BORDER_ACTIVE if active else ThemeColors.BORDER_DEFAULT
    border_left = f"border-left: 3px solid {accent_color};" if accent_color else f"border-left: 1px solid {border};"
    return f"""
        background-color: {bg};
        border: 1px solid {border};
        {border_left}
        border-radius: 5px;
    """


def get_button_style(variant: str = "secondary") -> str:
    """Tạo stylesheet đồng bộ cho các nút QPushButton theo variant."""
    if variant == "primary":
        return f"""
            QPushButton {{
                background-color: {ThemeColors.PRIMARY};
                color: {ThemeColors.PRIMARY_TEXT};
                border: 1px solid {ThemeColors.PRIMARY_HOVER};
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.PRIMARY_HOVER};
                border-color: #f07b6b;
            }}
            QPushButton:pressed {{
                background-color: {ThemeColors.PRIMARY_PRESSED};
            }}
        """
    elif variant == "gradient":
        return f"""
            QPushButton {{
                background-color: {ThemeColors.PRIMARY};
                color: #ffffff;
                border: 1px solid {ThemeColors.PRIMARY_HOVER};
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.PRIMARY_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {ThemeColors.PRIMARY_PRESSED};
            }}
        """
    elif variant == "danger":
        return f"""
            QPushButton {{
                background-color: {ThemeColors.DANGER};
                color: #ffffff;
                border: 1px solid {ThemeColors.DANGER};
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.DANGER_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {ThemeColors.DANGER_PRESSED};
            }}
        """
    else:  # secondary
        return f"""
            QPushButton {{
                background-color: #272930;
                color: {ThemeColors.TEXT_PRIMARY};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 4px;
                padding: 6px 14px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.BG_CARD_ACTIVE};
                border-color: {ThemeColors.BORDER_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {ThemeColors.BG_INPUT};
            }}
        """
