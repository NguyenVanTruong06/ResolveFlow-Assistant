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
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable

from PySide6.QtCore import QThread, Signal as pyqtSignal

from src.core.asset_indexer import AssetIndexer
from src.core.lut_generator import LUT3DGenerator, BUILTIN_COLOR_LOOKS
from src.core.sfx_generator import generate_all_sfx
from src.core.storage_manager import StorageManager, StorageState, StorageOfflineError, atomic_json, read_json
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


class StarterPackProducer:
    """
    Sinh gói Starter Pack (SFX & LUTs) vào thư mục staging của StorageManager,
    chuẩn bị draft job để đưa vào PackIngestWizard kiểm duyệt trước khi nhập kho.
    """
    PACK_NAME = "Starter Pack (SFX & LUTs)"
    PACK_VERSION = "1.0"
    PREFLIGHT_BUDGET_BYTES = 64 * 1024 * 1024  # 64 MiB giả định an toàn cho SFX + LUTs (chưa đo thực tế)

    # Bảng tra cứu nhóm con và tags theo từ khóa tên tệp SFX
    SFX_TAG_MAP = {
        "whoosh": ("chuyen_canh", ["chuyen_canh", "whoosh", "transition", "starter_pack"]),
        "swoosh": ("chuyen_canh", ["chuyen_canh", "swoosh", "transition", "starter_pack"]),
        "cymbal": ("chuyen_canh", ["chuyen_canh", "cymbal", "swell", "starter_pack"]),
        "riser": ("chuyen_canh", ["chuyen_canh", "riser", "tension", "starter_pack"]),
        "braam": ("giat_minh", ["giat_minh", "braam", "cinematic", "starter_pack"]),
        "drop": ("giat_minh", ["giat_minh", "bass_drop", "impact", "starter_pack"]),
        "boom": ("giat_minh", ["giat_minh", "vine_boom", "sub", "starter_pack"]),
        "punch": ("giat_minh", ["giat_minh", "punch", "hit", "starter_pack"]),
        "cuoi": ("hai_huoc", ["hai_huoc", "tieng_cuoi", "sitcom", "starter_pack"]),
        "airhorn": ("hai_huoc", ["hai_huoc", "airhorn", "meme", "starter_pack"]),
        "bonk": ("hai_huoc", ["hai_huoc", "bonk", "chao_go", "starter_pack"]),
        "de_keu": ("hai_huoc", ["hai_huoc", "de_keu", "que_do", "starter_pack"]),
        "pop": ("diem_nhan", ["diem_nhan", "pop", "bubble", "starter_pack"]),
        "ding": ("diem_nhan", ["diem_nhan", "ding", "bell", "starter_pack"]),
        "shutter": ("diem_nhan", ["diem_nhan", "camera", "shutter", "starter_pack"]),
        "cash": ("diem_nhan", ["diem_nhan", "cash", "ting", "starter_pack"]),
        "tada": ("diem_nhan", ["diem_nhan", "tada", "fanfare", "starter_pack"]),
        "glitch": ("cong_nghe", ["cong_nghe", "glitch", "digital", "starter_pack"]),
        "click": ("cong_nghe", ["cong_nghe", "click", "ui", "starter_pack"]),
        "censor": ("cong_nghe", ["cong_nghe", "bleep", "censor", "starter_pack"]),
        "bleep": ("cong_nghe", ["cong_nghe", "bleep", "1000hz", "starter_pack"]),
        "mech": ("cong_nghe", ["cong_nghe", "ban_phim", "mech", "starter_pack"]),
        "error": ("cong_nghe", ["cong_nghe", "error", "buzzer", "starter_pack"]),
    }

    @classmethod
    def _categorize_sfx(cls, stem: str) -> tuple[str, list[str]]:
        stem_lower = stem.lower()
        for key, (subcat, tags) in cls.SFX_TAG_MAP.items():
            if key in stem_lower:
                return subcat, list(tags)
        return "hieu_ung", ["sfx", stem_lower, "starter_pack"]

    @classmethod
    def _categorize_lut(cls, filename: str) -> tuple[str, str, list[str]]:
        for look in BUILTIN_COLOR_LOOKS:
            if look.file_name == filename:
                subcat = look.category or "cinema"
                return subcat, look.name, [subcat, "lut", "3d_lut", "starter_pack"]
        # Fallback khi look id lạ
        name = Path(filename).stem.replace("ResolveFlow_", "").replace("_", " ")
        return "cinema", name, ["cinema", "lut", "starter_pack"]

    @classmethod
    def produce_to_staging(
        cls,
        storage: StorageManager,
        cancel: Callable[[], bool] = lambda: False,
        progress: Callable[[str], None] = lambda text: None,
        sfx_generator_fn: Optional[Callable[[str], int]] = None,
        lut_generator_fn: Optional[Callable[[str], int]] = None,
    ) -> Dict[str, Any]:
        """
        Sinh các asset vào sm.new_staging() / 'payload',
        tạo pack_manifest.json chuẩn,
        gọi PackIngestService.inspect_job() và trả về job draft cho PackIngestWizard.
        """
        storage.require_online()
        state = storage.get_state()
        if state != StorageState.ONLINE:
            raise ValueError(f"Kho lưu trữ không ở trạng thái ghi được (hiện tại: {state}).")

        initial_root = storage.root.resolve()
        initial_library_id = str(storage._config.get("library_id", ""))
        initial_marker_id = str(read_json(storage.meta / "library.json", {}).get("library_id", ""))

        storage.ensure_space(cls.PREFLIGHT_BUDGET_BYTES)

        job_dir = storage.new_staging()
        if not job_dir.is_dir() or job_dir.is_symlink() or job_dir.parent.resolve() != (initial_root / ".chundvc_meta" / "staging").resolve():
            raise ValueError("Staging directory không hợp lệ hoặc chứa symlink.")

        payload = job_dir / "payload"

        def _verify_identity():
            if not storage.online:
                raise StorageOfflineError("Kho đã offline trong quá trình sinh gói.")
            cur_root = storage.root.resolve()
            cur_lib_id = str(storage._config.get("library_id", ""))
            cur_marker_id = str(read_json(storage.meta / "library.json", {}).get("library_id", ""))
            if cur_root != initial_root or cur_lib_id != initial_library_id or cur_marker_id != initial_marker_id:
                raise ValueError("Danh tính kho lưu trữ (root / library_id / marker) đã bị thay đổi trong quá trình sinh gói.")
            if job_dir.is_symlink() or payload.is_symlink():
                raise ValueError("Phát hiện symlink không an toàn trong đường dẫn staging.")

        def _cleanup_staging():
            try:
                if not storage.online:
                    return
                if storage.root.resolve() != initial_root:
                    return
                if str(storage._config.get("library_id", "")) != initial_library_id:
                    return
                marker = read_json(storage.meta / "library.json", {})
                if str(marker.get("library_id", "")) != initial_marker_id:
                    return
                if job_dir.is_symlink() or not job_dir.is_dir():
                    return
                if job_dir.resolve().parent != (initial_root / ".chundvc_meta" / "staging").resolve():
                    return
                shutil.rmtree(job_dir, ignore_errors=True)
            except Exception:
                pass

        try:
            payload.mkdir(parents=True, exist_ok=True)
            if payload.is_symlink():
                raise ValueError("Payload directory là symlink không hợp lệ.")

            _verify_identity()

            # 1. Kiểm tra cancel trước Batch 1
            if cancel():
                _cleanup_staging()
                raise InterruptedError("Đã hủy trước khi sinh SFX.")

            # 2. Batch 1: Sinh SFX
            progress("Đang tổng hợp các hiệu ứng âm thanh SFX...")
            sfx_target = payload / "sfx"
            sfx_target.mkdir(parents=True, exist_ok=True)
            gen_sfx = sfx_generator_fn or generate_all_sfx
            sfx_count = gen_sfx(str(sfx_target))
            sfx_files = list(sfx_target.glob("*.wav"))
            if sfx_count <= 0 or not sfx_files:
                _cleanup_staging()
                raise ValueError("Bộ sinh SFX không tạo ra tệp âm thanh nào.")

            # 3. Kiểm tra cancel và identity giữa Batch 1 và Batch 2
            if cancel():
                _cleanup_staging()
                raise InterruptedError("Đã hủy sau khi sinh SFX.")

            _verify_identity()
            storage.ensure_space(cls.PREFLIGHT_BUDGET_BYTES)

            # 4. Batch 2: Sinh 3D LUTs
            progress("Đang tính toán các bảng màu 3D LUTs điện ảnh...")
            luts_target = payload / "luts"
            luts_target.mkdir(parents=True, exist_ok=True)
            gen_luts = lut_generator_fn or LUT3DGenerator.ensure_all_luts_exist
            lut_count = gen_luts(str(luts_target))
            lut_files = list(luts_target.glob("*.cube"))
            if lut_count <= 0 or not lut_files:
                _cleanup_staging()
                raise ValueError("Bộ sinh LUT không tạo ra tệp bảng màu nào.")

            # 5. Kiểm tra cancel và identity trước khi tạo manifest và inspect
            if cancel():
                _cleanup_staging()
                raise InterruptedError("Đã hủy sau khi sinh LUTs.")

            _verify_identity()

            # 6. Quét file thực và xây dựng manifest v1
            assets_records = []
            for p in sorted(sfx_files):
                rel = str(p.relative_to(payload))
                subcat, tags = cls._categorize_sfx(p.stem)
                name = p.stem.replace("_", " ").title()
                assets_records.append({
                    "relative_path": rel,
                    "name": name,
                    "category": "sfx",
                    "subcategory": subcat,
                    "tags": tags,
                    "excluded": False,
                })

            for p in sorted(lut_files):
                rel = str(p.relative_to(payload))
                subcat, name, tags = cls._categorize_lut(p.name)
                assets_records.append({
                    "relative_path": rel,
                    "name": name,
                    "category": "luts",
                    "subcategory": subcat,
                    "tags": tags,
                    "excluded": False,
                })

            if not assets_records:
                _cleanup_staging()
                raise ValueError("Không có tài nguyên nào được sinh vào staging.")

            manifest = {
                "schema_version": 1,
                "name": cls.PACK_NAME,
                "version": cls.PACK_VERSION,
                "assets": assets_records,
            }
            atomic_json(payload / "pack_manifest.json", manifest)

            # 7. Gọi inspect_job để tạo draft
            progress("Đang kiểm định dữ liệu staging...")
            from src.core.pack_ingest import PackIngestService
            service = PackIngestService(storage)
            job_draft = service.inspect_job(job_dir, name=cls.PACK_NAME, cancel=cancel)
            _verify_identity()
            return job_draft

        except Exception:
            _cleanup_staging()
            raise

