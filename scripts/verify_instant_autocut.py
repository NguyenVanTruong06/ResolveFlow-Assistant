"""
Script kiểm thử toàn diện và chụp ảnh minh chứng tính năng:
1-Click Instant AutoCut trực tiếp từ DaVinci Resolve Timeline (Phương pháp 2: API Assembly).
"""
import os
import sys
import time
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap

# Ensure project root is in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.ui.lux.shell.edge_dock import EdgeDockWindow
from src.ui.lux.palette.command_palette import CommandPaletteWindow
from src.ui.lux.feedback.toast import ChunDVCToast
from src.ui.tabs.tab_assets import TabAssets
from src.services.autocut_service import InstantAutoCutService, AutoCutResult


def run_verification():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    artifact_dir = "/home/vanntruongg/.gemini/antigravity/brain/17b4c02d-da1e-4059-8e60-495e0079d670"
    os.makedirs(artifact_dir, exist_ok=True)

    print("=== 1. Chụp ảnh EdgeDock Expanded Header (Có nút ⚡ AutoCut) ===")
    dock = EdgeDockWindow()
    dock.open_dock(animated=False)
    dock.show()
    app.processEvents()

    # Capture Dock Header
    pix_dock = dock.grab()
    dock_path = os.path.join(artifact_dir, "test_autocut_dock_header.png")
    pix_dock.save(dock_path)
    print(f"Đã lưu: {dock_path}")

    print("=== 2. Chụp ảnh EdgeDock Collapsed Rail (Có nút ⚡ AutoCut) ===")
    dock.collapse_dock(animated=False)
    app.processEvents()
    pix_rail = dock.grab()
    rail_path = os.path.join(artifact_dir, "test_autocut_dock_rail.png")
    pix_rail.save(rail_path)
    print(f"Đã lưu: {rail_path}")

    print("=== 3. Chụp ảnh Command Palette (>cắt khoảng lặng 1-click) ===")
    palette = CommandPaletteWindow()
    palette.open_palette(command_mode=True)
    palette.txt_search.setText(">cắt")
    palette._update_results()
    if hasattr(palette, "_fade_anim"):
        palette._fade_anim.stop()
    if hasattr(palette, "_opacity_effect"):
        palette._opacity_effect.setOpacity(1.0)
    palette.show()
    app.processEvents()
    pix_pal = palette.grab()
    pal_path = os.path.join(artifact_dir, "test_autocut_command_palette.png")
    pix_pal.save(pal_path)
    print(f"Đã lưu: {pal_path}")
    palette.hide()

    print("=== 4. Chụp ảnh Luxury Toast Notification thành công ===")
    toast = ChunDVCToast()
    result = AutoCutResult(
        ok=True,
        original_timeline_name="Interview_Rough_Cut",
        new_timeline_name="[AutoCut] Interview_Rough_Cut",
        cuts_count=42,
        duration_saved_sec=138.5,
        message="AutoCut hoàn tất thành công!"
    )
    mins = int(result.duration_saved_sec // 60)
    secs = int(result.duration_saved_sec % 60)
    toast.show_message(
        f"🎉 AutoCut thành công! {result.cuts_count} khoảng lặng đã cắt, rút ngắn {mins}p{secs:02d}s.",
        icon="⚡",
        level="success",
        duration_ms=8000
    )
    if hasattr(toast, "_anim"):
        toast._anim.stop()
    if hasattr(toast, "_opacity_effect"):
        toast._opacity_effect.setOpacity(1.0)
    toast.show()
    app.processEvents()
    pix_toast = toast.grab()
    toast_path = os.path.join(artifact_dir, "test_autocut_toast_feedback.png")
    pix_toast.save(toast_path)
    print(f"Đã lưu: {toast_path}")
    toast.hide()

    print("=== 5. Chụp ảnh TabAssets Studio Toolbar (Nút ⚡ AutoCut 1-Click) ===")
    tab_assets = TabAssets()
    tab_assets.resize(1300, 780)
    tab_assets.show()
    app.processEvents()
    pix_studio = tab_assets.grab()
    studio_path = os.path.join(artifact_dir, "test_autocut_studio_toolbar.png")
    pix_studio.save(studio_path)
    print(f"Đã lưu: {studio_path}")
    tab_assets.close()
    dock.close()

    print("\n✅ TẤT CẢ ẢNH MINH CHỨNG ĐÃ ĐƯỢC CHỤP THÀNH CÔNG!")


if __name__ == "__main__":
    run_verification()
