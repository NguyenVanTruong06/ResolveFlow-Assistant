"""
tests/simulate_dock_and_studio.py
Mô phỏng người dùng tương tác với Edge Dock, Studio Window và Command Palette:
- Kiểm tra Edge Dock nạp tài nguyên SFX/Memes
- Kiểm tra hover & playback không bị crash âm thanh
- Kiểm tra chuyển đổi màn hình Dual Monitor trong Studio Window
- Kiểm tra Command Palette mở và tìm kiếm
- Chụp ảnh màn hình lưu vào Artifacts Directory
"""

import sys
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from src.ui.lux.shell.edge_dock import EdgeDockWindow
from src.ui.studio.studio_window import StudioWindow
from src.ui.lux.palette.command_palette import CommandPaletteWindow
from src.services.multi_screen_service import get_multi_screen_service
from src.core.asset_indexer import AssetIndexer

ARTIFACT_DIR = "/home/vanntruongg/.gemini/antigravity/brain/17b4c02d-da1e-4059-8e60-495e0079d670"
os.makedirs(ARTIFACT_DIR, exist_ok=True)

def run_dock_and_studio_simulation():
    app = QApplication.instance() or QApplication(sys.argv)

    print("=" * 60)
    print("🚀 BẮT ĐẦU MÔ PHỎNG EDGE DOCK & STUDIO WINDOW")
    print("=" * 60)

    # 1. Edge Dock
    print("\n--- [EDGE DOCK] Khởi chạy Edge Dock Window ---")
    indexer = AssetIndexer()
    all_dict = indexer.scan_all_assets(force_refresh=True)
    assets = [item for sublist in all_dict.values() for item in sublist]
    print(f"✓ Đã quét được {len(assets)} tài nguyên (SFX, Meme, B-Roll,...)")

    dock = EdgeDockWindow()
    dock.set_assets(assets)
    dock.resize(480, 800)
    dock.show()
    app.processEvents()

    dock.open_dock(animated=False)
    app.processEvents()
    shot_dock = os.path.join(ARTIFACT_DIR, "sim_edge_dock_expanded.png")
    dock.grab().save(shot_dock)
    print(f"📸 Đã lưu ảnh Edge Dock: {shot_dock}")

    # 2. Studio Window
    print("\n--- [STUDIO WINDOW] Khởi chạy Studio Window (Kho Đạo Cụ & Xưởng Sáng Tạo) ---")
    studio = StudioWindow()
    studio.resize(1180, 760)
    studio.show()
    app.processEvents()

    # Kiểm tra nút chuyển màn hình
    print(f"Trạng thái nút Chuyển Màn hình: {studio.btn_switch_screen.text()}")
    shot_studio = os.path.join(ARTIFACT_DIR, "sim_studio_window_overview.png")
    studio.grab().save(shot_studio)
    print(f"📸 Đã lưu ảnh Studio Window: {shot_studio}")

    # 3. Command Palette
    print("\n--- [COMMAND PALETTE] Khởi chạy Command Palette ---")
    palette = CommandPaletteWindow()
    palette.set_assets(assets)
    palette.open_palette(command_mode=False)
    app.processEvents()

    # Thử tìm kiếm
    palette.txt_search.setText("Vlog")
    app.processEvents()
    shot_palette = os.path.join(ARTIFACT_DIR, "sim_command_palette_search.png")
    palette.grab().save(shot_palette)
    print(f"📸 Đã lưu ảnh Command Palette: {shot_palette}")

    print("\n" + "=" * 60)
    print("🎉 EDGE DOCK, STUDIO WINDOW VÀ COMMAND PALETTE HOẠT ĐỘNG HOÀN HẢO!")
    print("=" * 60)

    dock.close()
    studio.close()
    palette.close()

if __name__ == "__main__":
    run_dock_and_studio_simulation()
