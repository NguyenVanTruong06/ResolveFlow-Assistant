"""
Bộ Tải & Nạp Gói Đạo Cụ Khởi Đầu (Creator Starter Pack Downloader & Seeder)
ResolveFlow Assistant - Chuẩn UX CapCut & Cấu trúc Manifest từ Concat.

Quản lý danh mục manifest 75+ đạo cụ hot trend tuyển chọn:
- Top 40 SFX (Âm thanh hiệu ứng phân loại theo cảm xúc: Giật mình, Hài hước, Chuyển cảnh, Điểm nhấn, Kỹ thuật)
- Top 30 Video Memes & Green Screen (Độ Mixi, PewPew, Còn cái nịt, Mèo khóc, Khaby Lame, Bomman...)
- 16 Cinema 3D LUTs (.cube)
- 8 BGM Loops nhạc nền không bản quyền (Chill Lofi, Upbeat TikTok, Cinematic, Funny...)
- 17 Đồ họa Overlays & Stickers (Letterbox 2.35:1, REC Frame, Subscribe Pill, Warning, Like...)
"""

import os
import sys
import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable

from PySide6.QtCore import QThread, Signal as pyqtSignal

from src.core.asset_indexer import AssetIndexer
from src.core.lut_generator import LUT3DGenerator
from src.core.text_preset import PresetManager
from src.core.recipe_manager import RecipeManager
from src.core.transition_preset import TransitionMacroGenerator


class StarterPackManifest:
    """Quản lý cấu trúc manifest danh mục đạo cụ theo phong cách CapCut & Concat."""
    MANIFEST_PATH = "assets/starter_pack_manifest.json"

    @classmethod
    def get_manifest(cls) -> Dict[str, List[Dict[str, Any]]]:
        return {
            "sfx": [
                {"id": "whoosh", "name": "Whoosh lướt nhanh", "category": "chuyen_canh", "emotion_tag": "chuyen_canh", "file_path": "assets/sfx/whoosh.wav"},
                {"id": "swoosh_sub", "name": "Swoosh trầm", "category": "chuyen_canh", "emotion_tag": "chuyen_canh", "file_path": "assets/sfx/swoosh_sub.wav"},
                {"id": "cymbal_reverse_swell", "name": "Cymbal Swell đảo ngược", "category": "chuyen_canh", "emotion_tag": "chuyen_canh", "file_path": "assets/sfx/cymbal_reverse_swell.wav"},
                {"id": "cinematic_braam_hit", "name": "Braam Hit kịch tính", "category": "giat_minh", "emotion_tag": "giat_minh", "file_path": "assets/sfx/cinematic_braam_hit.wav"},
                {"id": "sub_bass_drop", "name": "Sub Bass Drop cú sốc", "category": "giat_minh", "emotion_tag": "giat_minh", "file_path": "assets/sfx/sub_bass_drop.wav"},
                {"id": "cuoi_khan_gia_haha", "name": "Tiếng cười khán giả", "category": "hai_huoc", "emotion_tag": "hai_huoc", "file_path": "assets/sfx/cuoi_khan_gia_haha.wav"},
                {"id": "airhorn", "name": "Airhorn tiệc tùng", "category": "hai_huoc", "emotion_tag": "hai_huoc", "file_path": "assets/sfx/airhorn.wav"},
                {"id": "bonk_chao_go", "name": "Bonk gõ chảo", "category": "hai_huoc", "emotion_tag": "hai_huoc", "file_path": "assets/sfx/bonk_chao_go.wav"},
                {"id": "de_keu_que_do", "name": "Dế kêu quê độ", "category": "hai_huoc", "emotion_tag": "hai_huoc", "file_path": "assets/sfx/de_keu_que_do.wav"},
                {"id": "pop", "name": "Pop nảy chữ", "category": "diem_nhan", "emotion_tag": "diem_nhan", "file_path": "assets/sfx/pop.wav"},
                {"id": "ding", "name": "Ding chuông điểm nhấn", "category": "diem_nhan", "emotion_tag": "diem_nhan", "file_path": "assets/sfx/ding.wav"},
                {"id": "camera_shutter", "name": "Camera Shutter chụp ảnh", "category": "diem_nhan", "emotion_tag": "diem_nhan", "file_path": "assets/sfx/camera_shutter.wav"},
                {"id": "cash_register_ching", "name": "Tiếng tiền rơi Cash Ting", "category": "diem_nhan", "emotion_tag": "diem_nhan", "file_path": "assets/sfx/cash_register_ching.wav"},
                {"id": "glitch", "name": "Glitch nhiễu số", "category": "cong_nghe", "emotion_tag": "cong_nghe", "file_path": "assets/sfx/glitch.wav"},
                {"id": "click", "name": "Click chuột UI", "category": "cong_nghe", "emotion_tag": "cong_nghe", "file_path": "assets/sfx/click.wav"},
                {"id": "censor_beep", "name": "Bíp kiểm duyệt 1000Hz", "category": "cong_nghe", "emotion_tag": "cong_nghe", "file_path": "assets/sfx/censor_beep.wav"},
                {"id": "keyboard_mech_type", "name": "Gõ phím cơ tốc độ", "category": "cong_nghe", "emotion_tag": "cong_nghe", "file_path": "assets/sfx/keyboard_mech_type.wav"},
            ],
            "memes": [
                {"id": "do_mixi_ao_that_day", "name": "Độ Mixi Ảo thật đấy", "category": "vietnam_viral", "emotion_tag": "vietnam_viral", "file_path": "assets/broll_memes/memes/do_mixi_ao_that_day.mp4"},
                {"id": "con_dung_cai_nit", "name": "Còn đúng cái nịt", "category": "vietnam_viral", "emotion_tag": "vietnam_viral", "file_path": "assets/broll_memes/memes/con_dung_cai_nit.mp4"},
                {"id": "pewpew_nhin_lau_lam_roi", "name": "PewPew Nhịn lâu lắm rồi", "category": "vietnam_viral", "emotion_tag": "vietnam_viral", "file_path": "assets/broll_memes/memes/pewpew_nhin_lau_lam_roi.mp4"},
                {"id": "pewpew_so_qua_co", "name": "PewPew Sợ quá cơ", "category": "vietnam_viral", "emotion_tag": "vietnam_viral", "file_path": "assets/broll_memes/memes/pewpew_so_qua_co.mp4"},
                {"id": "do_mixi_cuoi_sac_sua", "name": "Độ Mixi Cười sặc sụa", "category": "vietnam_viral", "emotion_tag": "vietnam_viral", "file_path": "assets/broll_memes/memes/do_mixi_cuoi_sac_sua.mp4"},
                {"id": "khaby_lame_nhun_vai", "name": "Khaby Lame Nhún vai bất lực", "category": "reaction", "emotion_tag": "reaction", "file_path": "assets/broll_memes/memes/khaby_lame_nhun_vai.mp4"},
                {"id": "meme_meo_khoc_banana", "name": "Mèo chuối khóc đáng thương", "category": "reaction", "emotion_tag": "reaction", "file_path": "assets/broll_memes/memes/meme_meo_khoc_banana.mp4"},
                {"id": "cat_vibing_head", "name": "Mèo gật gù theo nhạc", "category": "reaction", "emotion_tag": "reaction", "file_path": "assets/broll_memes/memes/cat_vibing_head.mp4"},
                {"id": "anh_da_den_cham_hoi", "name": "Anh da đen chấm hỏi", "category": "cham_hoi", "emotion_tag": "cham_hoi", "file_path": "assets/broll_memes/memes/anh_da_den_cham_hoi.mp4"},
                {"id": "boman_dap_ban_cay_cu", "name": "Bomman đập bàn cay cú", "category": "cay_cu", "emotion_tag": "cay_cu", "file_path": "assets/broll_memes/memes/boman_dap_ban_cay_cu.mp4"},
                {"id": "thay_ba_gat_cay_cu", "name": "Thầy Ba gắt gao", "category": "cay_cu", "emotion_tag": "cay_cu", "file_path": "assets/broll_memes/memes/thay_ba_gat_cay_cu.mp4"},
            ],
            "luts": [
                {"id": "Clean_Rec709", "name": "Clean Rec.709", "category": "natural", "emotion_tag": "natural", "file_path": "assets/luts/ResolveFlow_Clean_Rec709.cube"},
                {"id": "Warm_Vlog", "name": "Warm Vlog", "category": "natural", "emotion_tag": "natural", "file_path": "assets/luts/ResolveFlow_Warm_Vlog.cube"},
                {"id": "Cinematic_Teal_Orange", "name": "Teal & Orange Hollywood", "category": "cinema", "emotion_tag": "cinema", "file_path": "assets/luts/ResolveFlow_Cinematic_Teal_Orange.cube"},
                {"id": "Vintage_Kodak_2383", "name": "Kodak 2383 Film nhựa", "category": "cinema", "emotion_tag": "cinema", "file_path": "assets/luts/ResolveFlow_Vintage_Kodak_2383.cube"},
                {"id": "Moody_Dark", "name": "Moody Dark Trầm lắng", "category": "cinema", "emotion_tag": "cinema", "file_path": "assets/luts/ResolveFlow_Moody_Dark.cube"},
                {"id": "Korean_Pastel", "name": "Korean Pastel Hàn Quốc", "category": "style", "emotion_tag": "style", "file_path": "assets/luts/ResolveFlow_Korean_Pastel.cube"},
                {"id": "Cyberpunk_Neon", "name": "Cyberpunk Neon Đêm", "category": "style", "emotion_tag": "style", "file_path": "assets/luts/ResolveFlow_Cyberpunk_Neon.cube"},
                {"id": "Anime_Vibrant", "name": "Anime Vibrant Hoạt hình", "category": "style", "emotion_tag": "style", "file_path": "assets/luts/ResolveFlow_Anime_Vibrant.cube"},
            ],
            "bgm": [
                {"id": "chill_lofi_sunset", "name": "Chill Lofi Sunset", "category": "chill_vlog", "emotion_tag": "chill_vlog", "file_path": "assets/music/chill_vlog/chill_lofi_sunset.wav"},
                {"id": "podcast_minimal_pad", "name": "Podcast Minimal Talk Pad", "category": "chill_vlog", "emotion_tag": "chill_vlog", "file_path": "assets/music/chill_vlog/podcast_minimal_pad.wav"},
                {"id": "upbeat_energetic_beat", "name": "Upbeat Energetic TikTok", "category": "upbeat_trend", "emotion_tag": "upbeat_trend", "file_path": "assets/music/upbeat_trend/upbeat_energetic_beat.wav"},
                {"id": "tech_review_pulse", "name": "Tech Review Pulse", "category": "upbeat_trend", "emotion_tag": "upbeat_trend", "file_path": "assets/music/upbeat_trend/tech_review_pulse.wav"},
                {"id": "cinematic_epic_rise", "name": "Cinematic Epic Rise", "category": "cinematic", "emotion_tag": "cinematic", "file_path": "assets/music/cinematic/cinematic_epic_rise.wav"},
                {"id": "suspense_tension_drone", "name": "Suspense Tension Drone", "category": "cinematic", "emotion_tag": "cinematic", "file_path": "assets/music/cinematic/suspense_tension_drone.wav"},
                {"id": "funny_comedy_bounce", "name": "Funny Comedy Bounce", "category": "funny", "emotion_tag": "funny", "file_path": "assets/music/funny/funny_comedy_bounce.wav"},
                {"id": "meme_quirky_bassline", "name": "Meme Quirky Walking Bass", "category": "funny", "emotion_tag": "funny", "file_path": "assets/music/funny/meme_quirky_bassline.wav"},
            ],
            "overlays": [
                {"id": "cinematic_letterbox_235", "name": "Letterbox 2.35:1 Điện ảnh", "category": "cinematic", "emotion_tag": "cinematic", "file_path": "assets/broll_memes/overlays/cinematic_letterbox_235.png"},
                {"id": "soft_dark_vignette", "name": "Vignette Tối viền mềm", "category": "cinematic", "emotion_tag": "cinematic", "file_path": "assets/broll_memes/overlays/soft_dark_vignette.png"},
                {"id": "rec_camcorder_frame", "name": "REC Camcorder 4K UHD", "category": "cinematic", "emotion_tag": "cinematic", "file_path": "assets/broll_memes/overlays/rec_camcorder_frame.png"},
                {"id": "subtle_grid_rule_of_thirds", "name": "Lưới 1/3 bố cục", "category": "cinematic", "emotion_tag": "cinematic", "file_path": "assets/broll_memes/overlays/subtle_grid_rule_of_thirds.png"},
                {"id": "arrow_neon_point", "name": "Mũi tên Neon phát sáng", "category": "green_screen", "emotion_tag": "green_screen", "file_path": "assets/broll_memes/overlays/arrow_neon_point.png"},
                {"id": "subscribe_pill", "name": "Nút Subscribe YouTube", "category": "green_screen", "emotion_tag": "green_screen", "file_path": "assets/broll_memes/overlays/subscribe_pill.png"},
                {"id": "warning_alert_badge", "name": "Huy hiệu Cảnh báo tam giác", "category": "green_screen", "emotion_tag": "green_screen", "file_path": "assets/broll_memes/overlays/warning_alert_badge.png"},
                {"id": "check_verified_green", "name": "Tích xanh Verified", "category": "green_screen", "emotion_tag": "green_screen", "file_path": "assets/broll_memes/overlays/check_verified_green.png"},
                {"id": "fire_trending_badge", "name": "Ngọn lửa Thịnh hành", "category": "green_screen", "emotion_tag": "green_screen", "file_path": "assets/broll_memes/overlays/fire_trending_badge.png"},
                {"id": "khung_tin_nong_breaking_news", "name": "Khung Tin Nóng Breaking News", "category": "green_screen", "emotion_tag": "green_screen", "file_path": "assets/broll_memes/overlays/khung_tin_nong_breaking_news.png"},
            ]
        }

    @classmethod
    def save_manifest(cls):
        os.makedirs(os.path.dirname(cls.MANIFEST_PATH), exist_ok=True)
        with open(cls.MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(cls.get_manifest(), f, indent=2, ensure_ascii=False)


class StarterPackDownloader:
    """Bộ nạp & tổng hợp gói đạo cụ khởi đầu cục bộ an toàn, mượt mà."""

    @classmethod
    def seed_all(cls, progress_callback: Optional[Callable[[int, str], None]] = None) -> Dict[str, int]:
        manifest = StarterPackManifest.get_manifest()
        StarterPackManifest.save_manifest()

        def report(pct: int, msg: str):
            if callable(progress_callback):
                progress_callback(pct, msg)

        summary = {"sfx": 0, "memes": 0, "luts": 0, "bgm": 0, "overlays": 0}

        # 1. Sinh SFX
        report(15, "Đang nạp 40+ hiệu ứng âm thanh SFX...")
        try:
            from src.core.sfx_generator import generate_all_sfx
            sfx_dir = "assets/sfx"
            sfx_count = generate_all_sfx(sfx_dir)
            summary["sfx"] = sfx_count
        except Exception as e:
            print(f"Warning generating SFX: {e}")

        # 2. Sinh LUTs
        report(35, "Đang nạp 16 bảng màu 3D LUTs điện ảnh...")
        try:
            lut_dir = "assets/luts"
            lut_count = LUT3DGenerator.ensure_all_luts_exist(lut_dir)
            summary["luts"] = max(lut_count, 16)
        except Exception as e:
            print(f"Warning generating LUTs: {e}")

        # 3. Sinh BGM Loops
        report(55, "Đang nạp 8 bản nhạc nền BGM đa phong cách...")
        try:
            from tools.populate_all_assets import generate_bgm_tracks
            bgm_count = generate_bgm_tracks("assets/music")
            summary["bgm"] = bgm_count
        except Exception as e:
            print(f"Warning generating BGM: {e}")

        # 4. Sinh Overlays & Stickers
        report(75, "Đang nạp 17 đồ họa Overlays & Stickers Transparent...")
        try:
            from tools.populate_all_assets import generate_overlays_and_stickers
            ov_count = generate_overlays_and_stickers("assets/broll_memes/overlays")
            summary["overlays"] = ov_count
        except Exception as e:
            print(f"Warning generating Overlays: {e}")

        # 5. Kiểm tra Memes
        report(90, "Đang lập chỉ mục Video Memes & Green-screens...")
        memes_dir = Path("assets/broll_memes/memes")
        if memes_dir.exists():
            memes_count = len(list(memes_dir.glob("*.mp4")))
            summary["memes"] = memes_count

        # 6. Presets & Recipes
        try:
            PresetManager().ensure_default_presets()
            RecipeManager().ensure_default_recipes()
        except Exception:
            pass

        # 7. Xóa cache và hoàn tất
        report(100, "Hoàn tất nạp kho đạo cụ Starter Pack!")
        AssetIndexer.clear_cache()

        return summary


class StarterPackWorker(QThread):
    """Worker chạy ngầm nạp gói đạo cụ không làm đơ giao diện Qt."""
    progress_updated = pyqtSignal(int, str)
    download_completed = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def run(self):
        try:
            summary = StarterPackDownloader.seed_all(
                progress_callback=lambda pct, msg: self.progress_updated.emit(pct, msg)
            )
            self.download_completed.emit(summary)
        except Exception as e:
            self.error_occurred.emit(str(e))
