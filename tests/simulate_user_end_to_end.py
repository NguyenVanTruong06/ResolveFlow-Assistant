"""
tests/simulate_user_end_to_end.py
Mô phỏng hành vi người dùng cuối (End-to-End User Simulation):
- Nạp video thực tế từ /media/vanntruongg/Ventoy/ResolveFlow_H264/_PCM/01_Cay_Tung_Bu/
- Đi qua Bước 1 (Source), Bước 2 (Style), Bước 3 (Review & Dual Plan), Bước 4 (Build Summary)
- Thử nghiệm copy Prompt V1 & V2, dán JSON Video Plan & Kinetic Plan
- Chụp ảnh màn hình lưu vào Artifacts Directory
"""

import sys
import os
from pathlib import Path

# Thêm đường dẫn gốc vào sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import json
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer

from src.ui.director.director_window import DirectorWindow
from src.ui.director.director_state import DirectorConfig
from src.core.cache_manager import ScanCacheManager

ARTIFACT_DIR = "/home/vanntruongg/.gemini/antigravity/brain/17b4c02d-da1e-4059-8e60-495e0079d670"
os.makedirs(ARTIFACT_DIR, exist_ok=True)

REAL_CLIPS = [
    "/media/vanntruongg/Ventoy/ResolveFlow_H264/_PCM/01_Cay_Tung_Bu/DJI_20260831134237_0085_D.mov",
    "/media/vanntruongg/Ventoy/ResolveFlow_H264/_PCM/01_Cay_Tung_Bu/DJI_20260831135537_0129_D.mov"
]

def run_simulation():
    app = QApplication.instance() or QApplication(sys.argv)
    
    print("=" * 60)
    print("🚀 BẮT ĐẦU MÔ PHỎNG NGƯỜI DÙNG END-TO-END VỚI DỮ LIỆU THỰC TẾ")
    print("=" * 60)

    # 1. Khởi tạo DirectorWindow
    window = DirectorWindow()
    window.resize(1180, 780)
    window.show()
    app.processEvents()

    # Kiểm tra cache của 2 clip thực tế
    cache_mgr = ScanCacheManager()
    for c in REAL_CLIPS:
        cached = cache_mgr.get_cached_scan(c, "small", "vi")
        assert cached is not None, f"Lỗi: Clip {c} chưa có cache!"
        sub_count = len(cached.get("raw_subtitles", []))
        print(f"✓ Cache Hit: {Path(c).name} ({sub_count} câu thoại)")

    # ==========================================================
    # BƯỚC 1: SOURCE FOOTAGE
    # ==========================================================
    print("\n--- [BƯỚC 1] Nạp Footage Nguồn ---")
    window.page_source.add_paths(REAL_CLIPS)
    app.processEvents()
    
    # Kiểm tra badge quét thô
    badge_text = window.page_source.btn_scan.text()
    print(f"Trạng thái nút Quét Thô Bước 1: {badge_text}")
    assert "Đã có cache" in badge_text, "Nút quét thô phải báo đã có cache toàn bộ!"

    # Chụp ảnh Bước 1
    shot1 = os.path.join(ARTIFACT_DIR, "sim_step1_source_loaded.png")
    window.grab().save(shot1)
    print(f"📸 Đã lưu ảnh Bước 1: {shot1}")

    # Chuyển bước tiếp theo
    window.btn_primary.click()
    app.processEvents()
    assert window.current_step == 2, f"Kỳ vọng Bước 2 nhưng đang ở {window.current_step}"

    # ==========================================================
    # BƯỚC 2: STYLE & PRESET
    # ==========================================================
    print("\n--- [BƯỚC 2] Chọn Phong Cách Dựng & Tính Năng ---")
    # Chọn kiểu Vlog có Hook
    window.page_style._select_style("vlog")
    app.processEvents()

    shot2 = os.path.join(ARTIFACT_DIR, "sim_step2_style_vlog.png")
    window.grab().save(shot2)
    print(f"📸 Đã lưu ảnh Bước 2: {shot2}")

    # Chuyển sang Bước 3
    window.btn_primary.click()
    app.processEvents()
    assert window.current_step == 3, f"Kỳ vọng Bước 3 nhưng đang ở {window.current_step}"

    # ==========================================================
    # BƯỚC 3: DUYỆT CÂU THOẠI & KỊCH BẢN AI
    # ==========================================================
    print("\n--- [BƯỚC 3] Duyệt Câu Thoại & Nạp Kịch Bản Kép ---")
    row_count = len(window.page_review._review_rows)
    print(f"Tổng số hàng câu thoại thực tế hiển thị: {row_count}")
    assert row_count >= 80, f"Kỳ vọng ít nhất 80 hàng câu thoại nhưng nhận được {row_count}"

    # Test tương tác: Cắt thử hàng đầu tiên
    print("Thử nghiệm: Cắt câu thoại đầu tiên...")
    window.page_review._row_widgets[0].btn_act.click()
    app.processEvents()
    assert window.page_review._review_rows[0][2] == 1, "Hàng đầu tiên phải ở trạng thái ĐÃ CẮT (1)"

    # Test Ctrl+Z hoàn tác
    print("Thử nghiệm: Ctrl+Z hoàn tác...")
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    window.page_review.keyPressEvent(event)
    app.processEvents()
    assert window.page_review._review_rows[0][2] == 0, "Hàng đầu tiên phải trở lại GIỮ (0) sau Ctrl+Z"
    print("✓ Hoàn tác Ctrl+Z hoạt động chính xác!")

    # Test sinh Prompt V1 (Dựng Video)
    print("Thử nghiệm: Sinh Prompt V1 (Dựng Video)...")
    prompt_v1 = window.page_review.get_video_prompt()
    assert len(prompt_v1) > 200, "Prompt V1 quá ngắn hoặc rỗng!"
    assert "DJI_20260831134237_0085_D" in prompt_v1, "Prompt V1 phải chứa tên clip thực tế 0085!"
    assert "DJI_20260831135537_0129_D" in prompt_v1, "Prompt V1 phải chứa tên clip thực tế 0129!"
    print(f"✓ Prompt V1 sinh thành công ({len(prompt_v1)} ký tự, có đủ 2 clips và câu thoại thực tế)!")

    # Test sinh Prompt V2 (Chữ Kinetic)
    print("Thử nghiệm: Sinh Prompt V2 (Chữ Kinetic)...")
    prompt_v2 = window.page_review.get_kinetic_prompt(aspect="9:16")
    assert len(prompt_v2) > 100, "Prompt V2 quá ngắn hoặc rỗng!"
    print(f"✓ Prompt V2 sinh thành công ({len(prompt_v2)} ký tự)!")

    # Test Nạp Video Plan (Slot 1)
    print("Thử nghiệm: Nạp JSON Video Plan vào Slot 1...")
    video_plan_mock = {
        "timeline_segments": [
            {
                "hook_title": "🔥 [TEASER HOOK 35s] Khởi đầu chuyến đi Đà Lạt săn cây tùng búp",
                "source_clip": "DJI_20260831134237_0085_D.mov",
                "start_time": "00:00:15.000",
                "end_time": "00:00:50.000",
                "story_pacing": "fast",
                "broll_inserts": [
                    {
                        "asset_file": "xach_balo_len_va_di.mp4",
                        "insert_time": "00:00:20.000",
                        "duration_sec": 3.0,
                        "track": 3
                    }
                ],
                "sfx_inserts": [
                    {
                        "asset_file": "whoosh_gio_thoi.wav",
                        "insert_time": "00:00:15.000",
                        "volume_db": -6.0
                    }
                ]
            },
            {
                "title": "Chặng 1: Tìm kiếm gốc cây tùng búp cổ thụ",
                "source_clip": "DJI_20260831135537_0129_D.mov",
                "start_time": "00:00:05.000",
                "end_time": "00:01:10.000",
                "story_pacing": "medium",
                "broll_inserts": [],
                "sfx_inserts": []
            }
        ]
    }
    
    window.page_review.txt_json_input.setPlainText(json.dumps(video_plan_mock, indent=2, ensure_ascii=False))
    window.page_review._apply_json_script()
    app.processEvents()

    assert window.page_review._video_plan_data is not None, "Slot 1 Video Plan phải được nạp!"
    print(f"Trạng thái Slot 1: {window.page_review.lbl_slot_vid_status.text()}")
    assert "Đã nạp" in window.page_review.lbl_slot_vid_status.text(), "Slot 1 phải báo đã nạp thành công!"

    # Test Nạp Kinetic Plan (Slot 2)
    print("Thử nghiệm: Nạp JSON Kinetic Plan vào Slot 2...")
    kinetic_plan_mock = {
        "style_preset": "karaoke_glow",
        "subtitles": [
            {
                "start": "00:00:15.200",
                "end": "00:00:18.500",
                "words": [
                    {"word": "Chào", "highlight": True, "anim": "pop"},
                    {"word": "mọi", "highlight": False, "anim": "fade"},
                    {"word": "người,", "highlight": False, "anim": "fade"},
                    {"word": "hôm", "highlight": False, "anim": "fade"},
                    {"word": "nay", "highlight": True, "anim": "pop"}
                ]
            }
        ]
    }
    window.page_review.txt_json_input.setPlainText(json.dumps(kinetic_plan_mock, indent=2, ensure_ascii=False))
    window.page_review._apply_json_script()
    app.processEvents()

    assert window.page_review._kinetic_plan_data is not None, "Slot 2 Kinetic Plan phải được nạp!"
    print(f"Trạng thái Slot 2: {window.page_review.lbl_slot_kin_status.text()}")
    assert "Đã nạp" in window.page_review.lbl_slot_kin_status.text(), "Slot 2 phải báo đã nạp thành công!"

    shot3 = os.path.join(ARTIFACT_DIR, "sim_step3_review_dual_plan_loaded.png")
    window.grab().save(shot3)
    print(f"📸 Đã lưu ảnh Bước 3: {shot3}")

    # Chuyển sang Bước 4
    window.btn_primary.click()
    app.processEvents()
    assert window.current_step == 4, f"Kỳ vọng Bước 4 nhưng đang ở {window.current_step}"

    # ==========================================================
    # BƯỚC 4: XÁC NHẬN & TỔNG KẾT DỰNG TIMELINE
    # ==========================================================
    print("\n--- [BƯỚC 4] Xác Nhận Dựng Timeline DaVinci Resolve ---")
    summary_card = window.page_build.summary_card
    print(f"Footage: {summary_card.lbl_footage.text()}")
    print(f"Kiểu dựng: {summary_card.lbl_style.text()}")
    print(f"Kịch bản: {summary_card.lbl_script.text()}")
    
    assert "2 clip" in summary_card.lbl_footage.text(), "Summary phải ghi đúng 2 clip!"
    assert "Vlog có Hook" in summary_card.lbl_style.text(), "Summary phải ghi đúng Vlog có Hook!"

    shot4 = os.path.join(ARTIFACT_DIR, "sim_step4_build_summary.png")
    window.grab().save(shot4)
    print(f"📸 Đã lưu ảnh Bước 4: {shot4}")

    # Kiểm tra cấu hình tạo kwargs cho pipeline
    window._sync_all_pages_to_config()
    kwargs = window.controller.build_pipeline_kwargs(phase=0)
    print("\nKiểm tra kwargs xuất ra PipelineWorker:")
    print(f"- video_paths ({len(kwargs['video_paths'])} clips): {[Path(p).name for p in kwargs['video_paths']]}")
    print(f"- text_preset_id: {kwargs['text_preset_id']}")
    print(f"- story_arrangement_override: {'Có (' + str(len(kwargs['story_arrangement_override'].get('timeline_segments', []))) + ' segments)' if kwargs['story_arrangement_override'] else 'Không'}")
    assert kwargs['text_preset_id'] == "karaoke_glow", "text_preset_id phải nhận từ kinetic plan!"
    assert kwargs['story_arrangement_override'] is not None, "story_arrangement_override phải nhận từ video plan!"

    print("\n" + "=" * 60)
    print("🎉 TẤT CẢ 4 BƯỚC CỦA AI DIRECTOR ĐÃ HOẠT ĐỘNG HOÀN HẢO 100%!")
    print("=" * 60)
    window.close()

if __name__ == "__main__":
    run_simulation()
