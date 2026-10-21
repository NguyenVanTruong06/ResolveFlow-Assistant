"""
src/ui/director/director_state.py
Quản lý trạng thái và cấu hình của ChunDVC Director (Wizard 4 bước).
Bao gồm:
- Định nghĩa SourceClipInfo, StylePreset, DirectorConfig.
- Bảng ánh xạ giữa 4 Thẻ phong cách (Style Cards) và 20+ tham số của PipelineWorker.
- Bộ giải mã thông minh Web Prompt JSON từ ChatGPT / Claude.
"""

from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class SourceClipInfo:
    """Thông tin một clip nguồn được chọn."""
    path: str
    filename: str
    duration_sec: float = 0.0
    duration_str: str = "00:00"
    resolution: str = "1080p"
    has_cache: bool = False
    file_size_mb: float = 0.0


# 4 Phong cách dựng chuẩn
STYLE_PODCAST = "podcast"
STYLE_SHORTS = "shorts"
STYLE_VLOG = "vlog"
STYLE_CUSTOM = "custom"

# Danh sách tính năng bật/tắt dạng chip
FEAT_HOOK = "Hook mở đầu"
FEAT_PUNCH_IN = "Punch-in"
FEAT_BROLL = "Gợi ý B-roll"
FEAT_SFX = "Tự chèn SFX"
FEAT_REFRAME = "Reframe 9:16"
FEAT_KARAOKE = "Phụ đề Karaoke"
FEAT_WEB_BROLL = "Săn B-roll web"
FEAT_SAFE_ZONE = "Khử đè mặt"

ALL_FEATURE_CHIPS = [
    FEAT_HOOK,
    FEAT_PUNCH_IN,
    FEAT_BROLL,
    FEAT_SFX,
    FEAT_REFRAME,
    FEAT_KARAOKE,
    FEAT_WEB_BROLL,
    FEAT_SAFE_ZONE,
]


@dataclass
class DirectorConfig:
    """Toàn bộ cấu hình 4 bước của ChunDVC Director."""
    # Bước 1: Nguồn
    video_paths: List[str] = field(default_factory=list)
    clip_infos: List[SourceClipInfo] = field(default_factory=list)
    whisper_model: str = "small"
    whisper_language: str = "vi"
    use_cache: bool = True

    # Bước 2: Kiểu dựng
    style: str = STYLE_VLOG
    footage_type: str = "Tự nhận diện"   # Tự nhận diện, Nói liên tục, Hỗn hợp, Du lịch
    pacing: str = "Cân bằng"             # Thong thả, Cân bằng, Nhanh
    intensity: int = 2                   # 1: Nhẹ, 2: Vừa, 3: Mạnh
    enabled_features: Set[str] = field(default_factory=lambda: {
        FEAT_HOOK, FEAT_PUNCH_IN, FEAT_BROLL, FEAT_SFX, FEAT_SAFE_ZONE
    })

    # Tham số chi tiết (từ Bảng nâng cao 280px - khớp 100% TabAutoCut)
    silence_cut_enabled: bool = True
    scene_guard_enabled: bool = True
    speedup_silence: bool = False
    audio_track_index: int = 1
    silence_db: int = -35
    min_silence_dur: float = 0.5

    ai_semantic_mode: str = "clean_talk"
    remove_bad_takes: bool = True
    remove_repeats: bool = True
    confidence_threshold: float = 0.70
    hide_weak_subs: bool = True
    fill_gaps: bool = False

    vlog_hook_dur: float = 2.0
    vlog_hook_total: float = 20.0
    punch_in_scale: float = 1.15
    safe_zone_platform: str = "tiktok"

    # Bước 3: Duyệt & Kịch bản AI
    ai_engine_mode: str = "web"          # web, ollama, cloud
    cloud_provider: str = "gemini"
    api_key: Optional[str] = None
    ai_script_result: Optional[str] = None
    web_prompt_pasted_data: Optional[Dict[str, Any]] = None
    web_prompt_summary: str = ""

    # Bước 4: Dựng timeline
    kinetic_subtitles_v2: bool = True
    text_preset_id: str = "karaoke_pop"
    beat_sync: bool = True
    bgm_name: str = "Chill Vlog Lofi"
    bgm_path: Optional[str] = None

    def apply_style_preset(self, style_id: str):
        """Áp dụng bộ tham số mặc định theo kiểu dựng."""
        self.style = style_id
        if style_id == STYLE_PODCAST:
            self.footage_type = "Nói liên tục"
            self.pacing = "Thong thả"
            self.intensity = 2
            self.silence_db = -38
            self.min_silence_dur = 0.6
            self.enabled_features = {FEAT_BROLL, FEAT_SFX, FEAT_SAFE_ZONE}
            self.ai_semantic_mode = "clean_talk"
            self.speedup_silence = False
            self.kinetic_subtitles_v2 = True

        elif style_id == STYLE_SHORTS:
            self.footage_type = "Tự nhận diện"
            self.pacing = "Nhanh"
            self.intensity = 3
            self.silence_db = -32
            self.min_silence_dur = 0.35
            self.enabled_features = {
                FEAT_HOOK, FEAT_PUNCH_IN, FEAT_REFRAME, FEAT_KARAOKE, FEAT_SFX, FEAT_SAFE_ZONE
            }
            self.ai_semantic_mode = "viral_shorts"
            self.speedup_silence = False
            self.vlog_hook_dur = 1.5
            self.vlog_hook_total = 15.0
            self.kinetic_subtitles_v2 = True

        elif style_id == STYLE_VLOG:
            self.footage_type = "Hỗn hợp"
            self.pacing = "Cân bằng"
            self.intensity = 2
            self.silence_db = -35
            self.min_silence_dur = 0.5
            self.enabled_features = {
                FEAT_HOOK, FEAT_PUNCH_IN, FEAT_BROLL, FEAT_SFX, FEAT_WEB_BROLL, FEAT_SAFE_ZONE
            }
            self.ai_semantic_mode = "clean_talk"
            self.speedup_silence = True
            self.scene_guard_enabled = True
            self.vlog_hook_dur = 2.0
            self.vlog_hook_total = 20.0
            self.kinetic_subtitles_v2 = True

        elif style_id == STYLE_CUSTOM:
            # Giữ nguyên các tham số tùy biến của người dùng
            pass


def parse_web_prompt_json(raw_text: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Tự động bóc tách và nhận diện chuỗi JSON kịch bản được dán từ ChatGPT / Claude.
    Hỗ trợ cả Video Plan (cắt ghép cảnh) và Kinetic Plan (phụ đề động).
    Trả về: (ok: bool, message: str, parsed_data: Optional[dict])
    """
    if not raw_text or not raw_text.strip():
        return False, "Nội dung dán rỗng. Vui lòng copy JSON từ AI.", None

    text = raw_text.strip()

    # Bóc markdown code block ```json ... ``` hoặc ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()
    else:
        # Tìm ngoặc nhọn đầu tiên và cuối cùng
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

    try:
        data = json.loads(text)
    except Exception as e:
        return False, f"JSON không hợp lệ: {str(e)}", None

    if not isinstance(data, dict):
        return False, "Dữ liệu JSON phải là một Object {...}", None

    # Nhận diện 1: Video Plan (Story / Scenes)
    if "scenes" in data or "timeline" in data or "segments" in data:
        items = data.get("scenes") or data.get("timeline") or data.get("segments") or []
        count = len(items)
        hook_info = "Có Hook" if any("hook" in str(item).lower() for item in items) else "Không Hook"
        summary = f"Đã nhận diện: Video Plan · {count} cảnh · {hook_info}"
        return True, summary, {"type": "video_plan", "data": data, "count": count}

    # Nhận diện 2: Kinetic Subtitle Plan
    if "subtitles" in data or "kinetic" in data or "words" in data:
        subs = data.get("subtitles") or data.get("kinetic") or data.get("words") or []
        count = len(subs)
        summary = f"Đã nhận diện: Kinetic Plan · {count} phụ đề hiệu ứng"
        return True, summary, {"type": "kinetic_plan", "data": data, "count": count}

    # Nhận diện 3: Cấu trúc JSON tùy biến có keys
    keys = list(data.keys())[:3]
    summary = f"Đã nhận diện: Kịch bản tùy biến ({', '.join(keys)})"
    return True, summary, {"type": "generic_plan", "data": data}
