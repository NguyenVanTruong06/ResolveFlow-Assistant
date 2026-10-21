import os
import json
import re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

def pydantic_dump(model: BaseModel) -> Dict[str, Any]:
    """Hỗ trợ serialize Pydantic model trên cả v1 (Python 3.10) và v2 (Python 3.11+)."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    elif hasattr(model, "dict"):
        return model.dict()
    return vars(model)


class Recipe(BaseModel):
    """
    Schema định nghĩa một Recipe - gói cấu hình hoàn chỉnh lưu trữ toàn bộ
    tùy chọn bật/tắt module và thông số chi tiết để tái sử dụng 1-click.
    """
    id: str = Field(..., description="ID định danh duy nhất của recipe")
    name: str = Field(..., description="Tên hiển thị người dùng (vd: Kênh Podcast A)")
    description: Optional[str] = Field(default="", description="Ghi chú mô tả mục đích recipe")
    workflow_mode: str = Field(default="advanced", description="Chế độ làm việc (podcast / shorts / vlog / advanced)")
    
    # Cấu hình Whisper & Ngôn ngữ
    model_size: str = Field(default="small")
    language: str = Field(default="Auto")
    
    # AI Director & Semantic Cutting
    ai_mode: str = Field(default="clean_talk")
    remove_bad_takes: bool = Field(default=True)
    remove_repeated_phrases: bool = Field(default=True)
    pacing: str = Field(default="balanced", description="Nhịp dựng: relaxed / balanced / fast")
    enable_punch_in: bool = Field(default=True)
    punch_in_scale: float = Field(default=1.15)
    confidence_threshold: float = Field(default=0.70)
    
    # Visual & Multi-Track Media
    enable_reframe: bool = Field(default=False)
    enable_broll: bool = Field(default=True)
    enable_sfx: bool = Field(default=True)
    
    # Subtitle Style & Split
    enable_subtitles: bool = Field(default=True)
    text_preset_id: str = Field(default="karaoke_pop")
    split_mode: str = Field(default="characters")
    split_limit: int = Field(default=42)
    font_name: str = Field(default="Arial")
    font_size: int = Field(default=48)
    
    # Silence Cut & Speed-Ramp
    run_cut: bool = Field(default=True)
    speed_up_silence: bool = Field(default=False)
    silence_speed: float = Field(default=8.0)
    silence_db: float = Field(default=-35.0)
    min_duration: float = Field(default=0.5)
    
    # Smart Vlog Hook / Intro
    enable_vlog_hook: bool = Field(default=False)
    vlog_hook_duration: float = Field(default=2.0, description="Độ dài mỗi khoảnh khắc trong teaser (giây)")
    vlog_hook_total: float = Field(default=20.0, description="Tổng thời lượng teaser (giây)")

    hide_weak_subs: bool = Field(default=True)

    # Tư duy cắt/tua theo loại video
    video_type: str = Field(default="auto", description="auto / talk / mixed / vlog")

    # Sắp xếp có ý đồ (Story Arranger)
    story_intent: str = Field(default="keep", description="keep / cold_open / rising_action / shorts")
    story_target: float = Field(default=60.0, description="Thời lượng mục tiêu cho ý đồ Shorts (giây)")

    # Vlog dài: bảo vệ cảnh quay & quét bổ sung vùng Whisper bỏ sót
    scene_guard: bool = Field(default=True)
    fill_gaps: bool = Field(default=False)


class RecipeManager:
    """
    Quản lý lưu trữ, tải và tạo mới các Recipe cấu hình workflow tự động hóa.
    """
    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "recipes")
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self.ensure_default_recipes()

    def ensure_default_recipes(self):
        """Khởi tạo một số recipe mẫu sẵn có cho người dùng."""
        defaults = [
            Recipe(
                id="podcast_pro",
                name="Podcast Studio Chuẩn",
                description="Lọc sạch nói vấp, khử im lặng, phụ đề viền thanh lịch cho podcast dài",
                workflow_mode="podcast",
                model_size="small",
                language="Tiếng Việt",
                ai_mode="clean_talk",
                remove_bad_takes=True,
                enable_punch_in=True,
                enable_reframe=False,
                enable_broll=True,
                enable_sfx=False,
                enable_subtitles=True,
                text_preset_id="clean_outline",
                split_mode="characters",
                split_limit=42,
                run_cut=True,
                silence_db=-35.0,
                min_duration=0.5
            ),
            Recipe(
                id="tiktok_viral_reels",
                name="TikTok / Shorts Viral 9:16",
                description="Cắt nhanh 60s, Auto Reframe dọc 9:16, Sub Karaoke Pop nhảy từ, Auto SFX",
                workflow_mode="shorts",
                model_size="small",
                language="Auto",
                ai_mode="viral_shorts",
                remove_bad_takes=True,
                enable_punch_in=True,
                enable_reframe=True,
                enable_broll=True,
                enable_sfx=True,
                enable_subtitles=True,
                text_preset_id="karaoke_pop",
                split_mode="words",
                split_limit=6,
                run_cut=True,
                silence_db=-30.0,
                min_duration=0.3
            ),
            Recipe(
                id="vlog_hook_speedramp",
                name="Vlog Hook & Speed-Ramp",
                description="Intro Teaser giật gân mở đầu vlog kèm hiệu ứng tua nhanh Timelapse khoảng lặng",
                workflow_mode="vlog",
                model_size="small",
                language="Auto",
                ai_mode="clean_talk",
                remove_bad_takes=True,
                enable_punch_in=True,
                enable_reframe=False,
                enable_broll=True,
                enable_sfx=True,
                enable_subtitles=True,
                text_preset_id="bounce_word",
                split_mode="characters",
                split_limit=42,
                run_cut=True,
                speed_up_silence=True,
                silence_speed=8.0,
                enable_vlog_hook=True,
                vlog_hook_duration=2.0
            ),
            # --- 4 Global Recipes ---
            Recipe(
                id="tech_review_4k",
                name="Review Đồ Công Nghệ 4K Sharp",
                description="Quy trình dựng unboxing & review công nghệ: Cắt gọt chuẩn xác, đệm B-Roll sản phẩm, âm SFX thao tác & phụ đề tối giản",
                workflow_mode="advanced",
                model_size="small",
                language="Auto",
                ai_mode="clean_talk",
                remove_bad_takes=True,
                remove_repeated_phrases=True,
                pacing="balanced",
                enable_punch_in=True,
                punch_in_scale=1.15,
                confidence_threshold=0.75,
                enable_reframe=False,
                enable_broll=True,
                enable_sfx=True,
                enable_subtitles=True,
                text_preset_id="apple_keynote_clean",
                split_mode="characters",
                split_limit=42,
                font_name="Arial",
                font_size=46,
                run_cut=True,
                silence_db=-36.0,
                min_duration=0.4,
                video_type="mixed",
                scene_guard=True
            ),
            Recipe(
                id="gaming_funny_moments",
                name="Gaming Funny Moments & Highlights",
                description="Nhịp cắt dồn dập, tự chèn meme reaction, âm thanh vui nhộn và phụ đề nổ dạng truyện tranh cho streamer/gameplay",
                workflow_mode="shorts",
                model_size="small",
                language="Auto",
                ai_mode="viral_shorts",
                remove_bad_takes=True,
                remove_repeated_phrases=False,
                pacing="fast",
                enable_punch_in=True,
                punch_in_scale=1.25,
                confidence_threshold=0.65,
                enable_reframe=False,
                enable_broll=True,
                enable_sfx=True,
                enable_subtitles=True,
                text_preset_id="comic_manga_burst",
                split_mode="words",
                split_limit=5,
                font_name="Arial",
                font_size=56,
                run_cut=True,
                silence_db=-30.0,
                min_duration=0.3,
                video_type="mixed"
            ),
            Recipe(
                id="educational_course_clean",
                name="Khóa Học Giảng Dạy & Tutorial Sạch Sẽ",
                description="Giữ nhịp điệu từ tốn, loại bỏ hoàn toàn tiếng thở và ngập ngừng, khung chữ dạ quang đánh dấu bài giảng chuyên nghiệp",
                workflow_mode="podcast",
                model_size="small",
                language="Auto",
                ai_mode="clean_talk",
                remove_bad_takes=True,
                remove_repeated_phrases=True,
                pacing="relaxed",
                enable_punch_in=False,
                enable_reframe=False,
                enable_broll=False,
                enable_sfx=False,
                enable_subtitles=True,
                text_preset_id="highlighter_swipe",
                split_mode="characters",
                split_limit=45,
                font_name="Arial",
                font_size=44,
                run_cut=True,
                silence_db=-38.0,
                min_duration=0.5,
                video_type="talk"
            ),
            Recipe(
                id="cinematic_travel_story",
                name="Cinematic Travel Story & Vlog Đời Sống",
                description="Dựng phim du lịch điện ảnh: Tạo Teaser Hook mở đầu, tua nhanh Timelapse các đoạn chuyển cảnh và phụ đề chữ vàng dịu mắt",
                workflow_mode="vlog",
                model_size="small",
                language="Auto",
                ai_mode="clean_talk",
                remove_bad_takes=True,
                remove_repeated_phrases=True,
                pacing="balanced",
                enable_punch_in=True,
                punch_in_scale=1.12,
                enable_reframe=False,
                enable_broll=True,
                enable_sfx=True,
                enable_subtitles=True,
                text_preset_id="khoai_lang_thang_chua_lanh",
                split_mode="characters",
                split_limit=40,
                font_name="Arial",
                font_size=44,
                run_cut=True,
                speed_up_silence=True,
                silence_speed=8.0,
                silence_db=-34.0,
                min_duration=0.6,
                enable_vlog_hook=True,
                vlog_hook_duration=2.0,
                vlog_hook_total=18.0,
                video_type="vlog",
                scene_guard=True
            ),
            # --- 3 Vietnamese Recipes ---
            Recipe(
                id="vlog_chua_lanh_vietnam",
                name="Vlog Chữa Lành Mộc Mạc Việt Nam",
                description="Tông màu vàng đất nắng ấm, nhịp dựng thư thái, phụ đề êm đềm kiểu Khoai Lang Thang & Ẩm Thực Mẹ Làm",
                workflow_mode="vlog",
                model_size="small",
                language="Tiếng Việt",
                ai_mode="clean_talk",
                remove_bad_takes=True,
                remove_repeated_phrases=True,
                pacing="relaxed",
                enable_punch_in=True,
                punch_in_scale=1.10,
                enable_reframe=False,
                enable_broll=True,
                enable_sfx=True,
                enable_subtitles=True,
                text_preset_id="khoai_lang_thang_chua_lanh",
                split_mode="characters",
                split_limit=38,
                font_name="Arial",
                font_size=44,
                run_cut=True,
                speed_up_silence=True,
                silence_speed=6.0,
                silence_db=-35.0,
                min_duration=0.6,
                video_type="vlog",
                scene_guard=True
            ),
            Recipe(
                id="tiktok_review_cong_nghe_vn",
                name="TikTok Review Đồ Công Nghệ Schannel VN",
                description="Tối ưu khung hình dọc 9:16, cắt nói vấp nhịp cực nhanh, nhảy chữ Schannel vàng đen, tự động ghép SFX tiếng bấm chuột và swoosh",
                workflow_mode="shorts",
                model_size="small",
                language="Tiếng Việt",
                ai_mode="viral_shorts",
                remove_bad_takes=True,
                remove_repeated_phrases=True,
                pacing="fast",
                enable_punch_in=True,
                punch_in_scale=1.20,
                enable_reframe=True,
                enable_broll=True,
                enable_sfx=True,
                enable_subtitles=True,
                text_preset_id="schannel_vlog_nhanh",
                split_mode="words",
                split_limit=5,
                font_name="Arial",
                font_size=52,
                run_cut=True,
                silence_db=-28.0,
                min_duration=0.25,
                story_intent="shorts",
                story_target=60.0
            ),
            Recipe(
                id="podcast_chuyen_doi_talkshow",
                name="Podcast Chuyện Đời & Talkshow Chuyên Sâu",
                description="Lọc sạch tạp âm, loại bỏ câu trùng lặp, căn chỉnh nhịp thở tự nhiên, phụ đề khung đen mờ tinh tế phong cách The Quoc Khanh Show / Vietcetera",
                workflow_mode="podcast",
                model_size="small",
                language="Tiếng Việt",
                ai_mode="clean_talk",
                remove_bad_takes=True,
                remove_repeated_phrases=True,
                pacing="balanced",
                enable_punch_in=True,
                punch_in_scale=1.15,
                enable_reframe=False,
                enable_broll=False,
                enable_sfx=False,
                enable_subtitles=True,
                text_preset_id="minimal_podcast_pill",
                split_mode="characters",
                split_limit=42,
                font_name="Arial",
                font_size=42,
                run_cut=True,
                silence_db=-38.0,
                min_duration=0.5,
                video_type="talk"
            )
        ]

        for r in defaults:
            fpath = os.path.join(self.base_dir, f"{r.id}.json")
            if not os.path.exists(fpath):
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(pydantic_dump(r), f, indent=2, ensure_ascii=False)

    def list_recipes(self) -> List[Recipe]:
        """Tải toàn bộ danh sách recipe hiện có."""
        recipes = []
        if os.path.exists(self.base_dir):
            for fname in sorted(os.listdir(self.base_dir)):
                if fname.endswith(".json"):
                    fpath = os.path.join(self.base_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        recipes.append(Recipe(**data))
                    except Exception:
                        continue
        return recipes

    def get_recipe(self, recipe_id: str) -> Optional[Recipe]:
        """Lấy recipe theo ID."""
        for r in self.list_recipes():
            if r.id == recipe_id:
                return r
        return None

    def save_recipe(self, recipe: Recipe) -> str:
        """Lưu một Recipe cấu hình."""
        safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', recipe.id).lower()
        recipe.id = safe_id
        file_path = os.path.join(self.base_dir, f"{safe_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(pydantic_dump(recipe), f, indent=2, ensure_ascii=False)
        return file_path

    def delete_recipe(self, recipe_id: str) -> bool:
        """Xóa recipe theo ID một cách an toàn."""
        safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(recipe_id)).lower().strip()
        if not safe_id:
            return False
        file_path = os.path.abspath(os.path.join(self.base_dir, f"{safe_id}.json"))
        base_dir_abs = os.path.abspath(self.base_dir)
        if not file_path.startswith(base_dir_abs):
            return False
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                return True
            except Exception:
                return False
        return False

