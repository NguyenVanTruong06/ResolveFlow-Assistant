"""
ChunDVC Branding & Identity Constants.
Tập trung tên thương hiệu, phiên bản, tác giả và định danh ứng dụng dùng chung.
"""

from typing import Optional
from PySide6.QtWidgets import QLabel

APP_BRAND = "ChunDVC"
APP_NAME = "ChunDVC"
APP_VERSION = "v1.0.14"
APP_AUTHOR = "By Truong"

# Badge chuẩn: "ChunDVC Studio v1.0 • By Truong"
BRAND_BADGE_TEXT = f"{APP_NAME} {APP_VERSION} • {APP_AUTHOR}"
BRAND_BADGE_COMPACT = f"{APP_VERSION} • {APP_AUTHOR}"

# Tiêu đề cửa sổ chuẩn
STUDIO_WINDOW_TITLE = f"{APP_NAME} · Kho Đạo Cụ & Xưởng Sáng Tạo"
DOCK_WINDOW_TITLE = f"{APP_BRAND} Dock"
DIRECTOR_WINDOW_TITLE = f"{APP_BRAND} AI Director"
AUTO_WINDOW_TITLE = f"{APP_BRAND} {APP_VERSION} - AI Visual & Director Automation Suite"

# Tray
TRAY_TOOLTIP = f"{APP_BRAND} — Trợ Lý Dựng DaVinci Resolve"

# Root logger namespace
LOGGER_ROOT = APP_BRAND


def create_brand_badge(compact: bool = False, parent: Optional[object] = None) -> QLabel:
    """Tạo badge thương hiệu giao diện chuẩn, nhỏ gọn, dark-theme."""
    text = BRAND_BADGE_COMPACT if compact else BRAND_BADGE_TEXT
    lbl = QLabel(text, parent)
    lbl.setStyleSheet("""
        QLabel {
            background: rgba(217, 79, 61, 0.18);
            color: #f8b4a8;
            border: 1px solid rgba(217, 79, 61, 0.45);
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 11px;
            font-weight: 600;
        }
    """)
    lbl.setToolTip(BRAND_BADGE_TEXT)
    return lbl
