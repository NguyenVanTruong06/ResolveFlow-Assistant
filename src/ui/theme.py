"""
Module Quản lý Theme, Bảng màu và Định kiểu Giao diện (Design System) cho ResolveFlow Assistant.
Chuẩn hóa màu sắc, kích thước, font chữ và các giải thích thuật ngữ thân thiện với người dùng.
"""

from typing import Dict

class ThemeColors:
    # Scooter Palette (taste-skill applied)
    BG_MAIN = "#021416"        # scooter-1400
    BG_CARD = "#031e22"        # scooter-1300
    BG_CARD_ACTIVE = "#05282d" # scooter-1200
    BG_INPUT = "#083338"       # scooter-1100
    BG_CONSOLE = "#021416"     # scooter-1400
    BG_HEADER = "#0c3d44"      # scooter-1000
    
    # Borders
    BORDER_DEFAULT = "#0c3d44" # scooter-1000
    BORDER_ACTIVE = "#33b0c1"  # scooter-400
    BORDER_ACTIVE_BLUE = "#39c1d3" # scooter-300
    BORDER_FOCUS = "#3dcee1"   # scooter-200
    BORDER_HOVER = "#1e737e"   # scooter-700
    
    # Text
    TEXT_PRIMARY = "#d5f7fe"   # scooter-50
    TEXT_SECONDARY = "#84ebfc" # scooter-100
    TEXT_MUTED = "#39c1d3"     # scooter-300
    TEXT_ACCENT = "#3dcee1"    # scooter-200
    TEXT_SUCCESS = "#84ebfc"   # scooter-100
    TEXT_WARNING = "#FFD54F"
    TEXT_ERROR = "#FF5252"
    
    # Accents & Brand (Single saturated pop)
    PRIMARY = "#33b0c1"        # scooter-400
    PRIMARY_HOVER = "#2c9dac"  # scooter-500
    PRIMARY_PRESSED = "#258895" # scooter-600
    PRIMARY_TEXT = "#021416"   # scooter-1400 (Contrast check pass)
    
    SUCCESS = "#175e67"        # scooter-800
    SUCCESS_HOVER = "#1e737e"  # scooter-700
    SUCCESS_PRESSED = "#0f4850" # scooter-900
    SUCCESS_LIGHT = "#3dcee1"  # scooter-200
    
    DANGER = "#C62828"
    DANGER_HOVER = "#D32F2F"
    DANGER_PRESSED = "#8E0000"
    
    WARNING = "#F57C00"
    WARNING_HOVER = "#FB8C00"


class ThemeFonts:
    FAMILY = "'Segoe UI', -apple-system, BlinkMacSystemFont, Arial, sans-serif"
    FAMILY_MONO = "'Consolas', 'Courier New', monospace"
    
    SIZE_TITLE = 18
    SIZE_SUBTITLE = 11
    SIZE_SECTION = 13
    SIZE_BODY = 12
    SIZE_SMALL = 11
    SIZE_BTN_RUN = 13


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
        font-size: 12px;
    }}
    
    QScrollArea {{
        background-color: transparent;
        border: none;
    }}
    
    QScrollBar:vertical {{
        background-color: {ThemeColors.BG_MAIN};
        width: 8px;
        margin: 0px;
        border-radius: 6px;
    }}
    QScrollBar::handle:vertical {{
        background-color: {ThemeColors.BORDER_DEFAULT};
        min-height: 24px;
        border-radius: 6px;
    }}
    QScrollBar::handle:vertical:hover {{
        background-color: {ThemeColors.BORDER_HOVER};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    /* --- CARDS & GROUPBOXES --- */
    QGroupBox {
        background-color: transparent;
        border: none;
        border-top: 1px solid {ThemeColors.BORDER_DEFAULT};
        margin-top: 24px;
        padding-top: 20px;
        padding-bottom: 8px;
        font-weight: 600;
        font-size: 13px;
        color: {ThemeColors.TEXT_PRIMARY};
    };
        border: 1.5px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 6px;
        margin-top: 14px;
        padding-top: 12px;
        padding-bottom: 8px;
        padding-left: 8px;
        padding-right: 8px;
        font-weight: bold;
        font-size: 12px;
        color: {ThemeColors.TEXT_ACCENT};
    }}
    
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 0px;
        top: -8px;
        padding: 0px 8px 0px 0px;
        background-color: {ThemeColors.BG_MAIN};
        color: {ThemeColors.TEXT_MUTED};
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 1px;
    };
        border-radius: 6px;
    }}
    
    QGroupBox[active="true"] {{
        border: 1.5px solid {ThemeColors.BORDER_ACTIVE};
        background-color: {ThemeColors.BG_CARD_ACTIVE};
    }}

    QGroupBox[active="false"] {{
        border: 1.5px solid {ThemeColors.BORDER_DEFAULT};
    }}

    /* Thẻ Card đặc biệt (Workflow, Recipe, Master Slider) */
    QGroupBox#group_wf {{
        border-color: {ThemeColors.PRIMARY};
        color: {ThemeColors.TEXT_ACCENT};
    }}
    QGroupBox#group_recipe {{
        border-color: #5C6BC0;
        color: #9FA8DA;
    }}
    QGroupBox#group_master {{
        border-color: #2E7D32;
        color: {ThemeColors.TEXT_SUCCESS};
    }}

    /* --- LABELS & TYPOGRAPHY --- */
    QLabel {{
        color: {ThemeColors.TEXT_PRIMARY};
        font-size: 12px;
    }}
    QLabel.section_desc {{
        color: {ThemeColors.TEXT_MUTED};
        font-size: 11px;
        font-weight: normal;
        margin-bottom: 6px;
    }}
    QLabel.badge_active {{
        color: {ThemeColors.TEXT_SUCCESS};
        font-size: 10px;
        font-weight: bold;
        padding: 1px 4px;
        border-radius: 6px;
        background-color: rgba(0, 200, 83, 0.15);
    }}
    QLabel.badge_inactive {{
        color: {ThemeColors.TEXT_MUTED};
        font-size: 10px;
        padding: 1px 4px;
        border-radius: 6px;
        background-color: rgba(108, 108, 133, 0.15);
    }}

    /* --- INPUTS & CONTROLS --- */
    QLineEdit, QComboBox {
        background-color: transparent;
        border: none;
        border-bottom: 1.5px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 0px;
        padding: 8px 4px;
        color: {ThemeColors.TEXT_PRIMARY};
        font-size: 13px;
    };
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 6px;
        padding: 6px 8px;
        color: {ThemeColors.TEXT_PRIMARY};
        selection-background-color: {ThemeColors.PRIMARY};
    }}
    QLineEdit:hover, QComboBox:hover {
        border-bottom: 1.5px solid {ThemeColors.BORDER_HOVER};
    };
    }}
    QLineEdit:focus, QComboBox:focus {
        border-bottom: 2px solid {ThemeColors.PRIMARY};
        background-color: rgba(51, 176, 193, 0.05);
    };
        background-color: #22222E;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 22px;
        border-left-width: 0px;
        border-top-right-radius: 5px;
        border-bottom-right-radius: 5px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {ThemeColors.BG_INPUT};
        border: 1px solid {ThemeColors.BORDER_HOVER};
        color: {ThemeColors.TEXT_PRIMARY};
        selection-background-color: {ThemeColors.PRIMARY};
        selection-color: {ThemeColors.PRIMARY_TEXT};
        outline: none;
        padding: 4px;
    }}

    /* --- CHECKBOXES --- */
    QCheckBox {{
        color: {ThemeColors.TEXT_PRIMARY};
        spacing: 8px;
        font-size: 12px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 6px;
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        background-color: {ThemeColors.BG_INPUT};
    }}
    QCheckBox::indicator:hover {{
        border: 1px solid {ThemeColors.BORDER_FOCUS};
    }}
    QCheckBox::indicator:checked {{
        background-color: {ThemeColors.SUCCESS_LIGHT};
        border: 1px solid {ThemeColors.SUCCESS_LIGHT};
        image: none;
    }}

    /* --- SLIDERS --- */
    QSlider::groove:horizontal {{
        border: none;
        height: 6px;
        background: {ThemeColors.BG_INPUT};
        border-radius: 6px;
    }}
    QSlider::sub-page:horizontal {{
        background: {ThemeColors.PRIMARY};
        border-radius: 6px;
    }}
    QSlider::handle:horizontal {{
        background: #FFFFFF;
        border: 2px solid {ThemeColors.PRIMARY};
        width: 16px;
        height: 16px;
        margin: -5px 0;
        border-radius: 6px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {ThemeColors.PRIMARY_HOVER};
        border: 2px solid #FFFFFF;
    }}

    /* --- BUTTONS --- */
    QPushButton {{
        background-color: {ThemeColors.PRIMARY};
        color: {ThemeColors.PRIMARY_TEXT};
        border: none;
        border-radius: 6px;
        padding: 8px 14px;
        font-weight: bold;
        font-size: 12px;
        min-height: 32px;
    }}
    QPushButton:hover {{
        background-color: {ThemeColors.PRIMARY_HOVER};
    }}
    QPushButton:pressed {{
        background-color: {ThemeColors.PRIMARY_PRESSED};
    }}
    QPushButton:disabled {{
        background-color: {ThemeColors.BORDER_DEFAULT};
        color: {ThemeColors.TEXT_MUTED};
    }}

    /* Nút chính Bắt đầu xử lý (Primary Run Button >= 44px) */
    QPushButton#btn_run {{
        background-color: {ThemeColors.SUCCESS};
        color: {ThemeColors.PRIMARY_TEXT};
        padding: 12px 20px;
        border-radius: 6px;
        font-size: 14px;
        font-weight: bold;
        min-height: 44px;
    }}
    QPushButton#btn_run:hover {{
        background-color: {ThemeColors.SUCCESS_HOVER};
    }}
    QPushButton#btn_run:pressed {{
        background-color: {ThemeColors.SUCCESS_PRESSED};
    }}

    /* Nút toggle nâng cao */
    QPushButton#btn_toggle_advanced {{
        background-color: #22222E;
        color: {ThemeColors.TEXT_ACCENT};
        text-align: left;
        padding: 9px 12px;
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 6px;
    }}
    QPushButton#btn_toggle_advanced:hover {{
        background-color: #2A2A38;
        border: 1px solid {ThemeColors.BORDER_HOVER};
    }}

    /* --- PROGRESS BAR --- */
    QProgressBar {{
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 6px;
        text-align: center;
        background-color: {ThemeColors.BG_INPUT};
        color: {ThemeColors.PRIMARY_TEXT};
        font-weight: bold;
        font-size: 11px;
        height: 18px;
    }}
    QProgressBar::chunk {{
        background-color: {ThemeColors.SUCCESS_LIGHT};
        border-radius: 6px;
    }}

    /* --- CONSOLE LOGS --- */
    QPlainTextEdit {{
        background-color: {ThemeColors.BG_CONSOLE};
        border: 1.5px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 6px;
        font-family: {ThemeFonts.FAMILY_MONO};
        color: {ThemeColors.TEXT_SUCCESS};
        font-size: 12px;
        padding: 6px;
        line-height: 1.4;
    }}

    /* --- TABLES --- */
    QTableWidget {{
        background-color: {ThemeColors.BG_CARD};
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-radius: 6px;
        gridline-color: {ThemeColors.BORDER_DEFAULT};
        color: {ThemeColors.TEXT_PRIMARY};
    }}
    QHeaderView::section {{
        background-color: {ThemeColors.BG_HEADER};
        color: {ThemeColors.TEXT_ACCENT};
        font-weight: bold;
        padding: 5px;
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
    }}
    QTableWidget::item:selected {{
        background-color: {ThemeColors.PRIMARY};
        color: {ThemeColors.PRIMARY_TEXT};
    }}

    /* --- TAB WIDGET & TAB BAR --- */
    QTabWidget::pane {
        border: none;
        border-top: 1px solid {ThemeColors.BORDER_DEFAULT};
        background-color: transparent;
        top: -1px;
    };
        background-color: {ThemeColors.BG_MAIN};
        border-radius: 6px;
        top: -1px;
    }}
    QTabBar::tab {
        background-color: transparent;
        color: {ThemeColors.TEXT_SECONDARY};
        border: none;
        padding: 12px 20px;
        margin-right: 8px;
        font-weight: 600;
        font-size: 13px;
    };
        border: 1px solid {ThemeColors.BORDER_DEFAULT};
        border-bottom: none;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        padding: 9px 16px;
        margin-right: 4px;
        font-weight: bold;
        font-size: 12px;
    }}
    QTabBar::tab:hover {
        color: {ThemeColors.TEXT_PRIMARY};
        background-color: rgba(255, 255, 255, 0.05);
        border-radius: 6px;
    };
        border-color: {ThemeColors.BORDER_HOVER};
    }}
    QTabBar::tab:selected {
        background-color: transparent;
        color: {ThemeColors.PRIMARY};
        border-bottom: 2px solid {ThemeColors.PRIMARY};
    };
        color: {ThemeColors.TEXT_ACCENT};
        border: 1.5px solid {ThemeColors.BORDER_DEFAULT};
        border-bottom: 2px solid {ThemeColors.PRIMARY};
    }}

    /* --- SPLITTER --- */
    QSplitter::handle {{
        background-color: #20202C;
        width: 6px;
        margin: 2px;
        border-radius: 6px;
    }}
    QSplitter::handle:hover {{
        background-color: #3B82F6;
    }}

    /* --- SFX PAD & PRESET CARDS --- */
    QPushButton.sfx_pad_btn {{
        background-color: #181B26;
        border: 1.5px solid #282E40;
        border-radius: 6px;
        color: #E2E8F0;
        font-size: 12px;
        font-weight: bold;
        padding: 10px 8px;
        text-align: center;
    }}
    QPushButton.sfx_pad_btn:hover {{
        background-color: #22293A;
        border-color: #3B82F6;
        color: #93C5FD;
    }}
    QPushButton.sfx_pad_btn:pressed {{
        background-color: #1E3A8A;
        border-color: #60A5FA;
    }}
    QPushButton.sfx_pad_btn[playing="true"] {{
        background-color: #14532D;
        border-color: #22C55E;
        color: #86EFAC;
    }}

    /* --- VISUAL TECHNIQUE CARDS (EYECANDY / MIXKIT STYLE) --- */
    QFrame.technique_card {{
        background-color: #12151F;
        border: 1.5px solid #232A3B;
        border-radius: 6px;
        padding: 10px;
    }}
    QFrame.technique_card:hover {{
        border: 1.5px solid #3B82F6;
        background-color: #181E2C;
    }}
    QFrame.technique_card[selected="true"] {{
        border: 2px solid #60A5FA;
        background-color: #1A2234;
    }}

    QPushButton.category_pill {{
        background-color: #161A26;
        border: 1px solid #283144;
        border-radius: 6px;
        color: #94A3B8;
        font-size: 11px;
        font-weight: bold;
        padding: 5px 12px;
    }}
    QPushButton.category_pill:hover {{
        background-color: #202738;
        border-color: #475569;
        color: #F8FAFC;
    }}
    QPushButton.category_pill[active="true"] {{
        background-color: #2563EB;
        border-color: #60A5FA;
        color: {ThemeColors.PRIMARY_TEXT};
    }}

    QLabel.tag_chip {{
        background-color: #1E2433;
        border: 1px solid #2D3748;
        border-radius: 6px;
        color: #94A3B8;
        font-size: 10px;
        padding: 2px 6px;
    }}

    /* --- TOOLTIPS --- */
    QToolTip {{
        background-color: #1F1F2B;
        color: #F2F2F7;
        border: 1px solid #454560;
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 12px;
        line-height: 1.3;
    }}
    """
