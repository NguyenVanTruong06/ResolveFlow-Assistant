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
        """Xóa recipe theo ID."""
        file_path = os.path.join(self.base_dir, f"{recipe_id}.json")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                return True
            except Exception:
                return False
        return False
