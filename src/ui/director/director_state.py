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
    ollama_model: str = "llama3:latest"
    ollama_endpoint: str = "http://localhost:11434"
    ai_script_result: Optional[str] = None
    web_prompt_pasted_data: Optional[Dict[str, Any]] = None
    web_prompt_summary: str = ""
    # Lưu trữ độc lập cả 2 bản vẽ Prompt 1 (Video Plan) và Prompt 2 (Kinetic Plan)
    video_plan_data: Optional[Dict[str, Any]] = None
    video_plan_summary: str = ""
    kinetic_plan_data: Optional[Dict[str, Any]] = None
    kinetic_plan_summary: str = ""

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


def _is_video_plan_dict(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    return any(k in data for k in [
        "timeline_segments", "global_hook", "broll_inserts", "sfx_inserts",
        "scenes", "timeline", "segments", "video_plan"
    ])


def _is_kinetic_plan_dict(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    return any(k in data for k in [
        "subtitles", "kinetic", "words", "kinetic_plan"
    ])


def _extract_all_json_objects(raw_text: str) -> List[Dict[str, Any]]:
    """Trích xuất tất cả các đối tượng JSON hợp lệ trong đoạn văn bản."""
    decoder = json.JSONDecoder()
    clean_s = raw_text.replace("\r\n", "\n")
    idx = 0
    objects = []
    while idx < len(clean_s):
        start = clean_s.find('{', idx)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(clean_s, start)
            if isinstance(obj, dict):
                objects.append(obj)
            idx = max(end, start + 1)
        except Exception:
            idx = start + 1
    return objects


def _summarize_video_plan(data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    items = data.get("timeline_segments") or data.get("scenes") or data.get("timeline") or data.get("segments") or []
    count = len(items)
    has_hook = bool(data.get("global_hook")) or any("hook" in str(item).lower() for item in items)
    hook_info = "Có Hook mở đầu" if has_hook else "Không Hook"
    broll_cnt = len(data.get("broll_inserts", []))
    sfx_cnt = len(data.get("sfx_inserts", []))
    details = []
    if count > 0:
        details.append(f"{count} phân đoạn")
    if broll_cnt > 0:
        details.append(f"{broll_cnt} meme/b-roll")
    if sfx_cnt > 0:
        details.append(f"{sfx_cnt} sfx")
    det_str = " · ".join(details) if details else f"{count} cảnh"
    summary = f"🎬 Đã nhận diện: Video Plan (V1) · {det_str} · {hook_info}"
    return summary, {
        "type": "video_plan",
        "data": data,
        "count": count,
        "has_hook": has_hook,
        "broll_count": broll_cnt,
        "sfx_count": sfx_cnt
    }


def _summarize_kinetic_plan(data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    subs = data.get("subtitles") or data.get("kinetic") or data.get("words") or []
    count = len(subs)
    summary = f"✨ Đã nhận diện: Kinetic Plan (V2) · {count} phụ đề hoạt ảnh nghệ thuật"
    return summary, {
        "type": "kinetic_plan",
        "data": data,
        "count": count
    }


def parse_web_prompt_json(raw_text: str, target_slot: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Tự động bóc tách và nhận diện chuỗi JSON kịch bản được dán từ ChatGPT / Claude.
    Hỗ trợ:
    - Video Plan V1 (cắt ghép cảnh, hook, meme, sfx)
    - Kinetic Plan V2 (phụ đề hoạt ảnh động)
    - Dual Plan: CẢ 2 JSON được dán cùng lúc trong một lượt dán (qua markdown hoặc JSON lồng)
    - target_slot: 'slot1' (Video Plan), 'slot2' (Kinetic Plan), hoặc None/'auto'
    Trả về: (ok: bool, message: str, parsed_data: Optional[dict])
    """
    if not raw_text or not raw_text.strip():
        return False, "Nội dung dán rỗng. Vui lòng copy JSON từ AI.", None

    text = raw_text.strip()

    # 1. Trích xuất tất cả đối tượng JSON có trong chuỗi
    extracted_objs = _extract_all_json_objects(text)

    # 2. Nếu target_slot được chỉ định rõ:
    if target_slot == "slot1":
        if extracted_objs:
            data = extracted_objs[0]
            summary, res = _summarize_video_plan(data)
            return True, summary, res
    elif target_slot == "slot2":
        if extracted_objs:
            data = extracted_objs[0]
            summary, res = _summarize_kinetic_plan(data)
            return True, summary, res

    # 3. Kiểm tra trường hợp DUAL PLAN (cả 2 kịch bản cùng xuất hiện)
    # 3a. Nhiều block JSON riêng biệt (ví dụ 2 khối markdown code)
    if len(extracted_objs) >= 2:
        v_obj = None
        k_obj = None
        for obj in extracted_objs:
            if _is_video_plan_dict(obj) and v_obj is None:
                v_obj = obj
            elif _is_kinetic_plan_dict(obj) and k_obj is None:
                k_obj = obj
        if v_obj and k_obj:
            _, v_res = _summarize_video_plan(v_obj)
            _, k_res = _summarize_kinetic_plan(k_obj)
            v_cnt = v_res["count"]
            br_cnt = v_res["broll_count"]
            sfx_cnt = v_res["sfx_count"]
            k_cnt = k_res["count"]
            v_sum = f"✓ Đã nạp ({v_cnt} cảnh · {br_cnt} meme · {sfx_cnt} sfx)"
            k_sum = f"✓ Đã nạp ({k_cnt} phụ đề động)"
            dual_summary = f"🎬✨ Đã nhận diện CẢ 2: Video Plan ({v_cnt} cảnh) & Kinetic Plan ({k_cnt} phụ đề)"
            return True, dual_summary, {
                "type": "dual_plan",
                "video_plan": v_obj,
                "kinetic_plan": k_obj,
                "video_summary": v_sum,
                "kinetic_summary": k_sum
            }

    # 3b. Một JSON object duy nhất nhưng chứa cả 2 key video_plan và kinetic_plan
    if len(extracted_objs) == 1:
        single_obj = extracted_objs[0]
        if "video_plan" in single_obj and "kinetic_plan" in single_obj:
            v_obj = single_obj["video_plan"]
            k_obj = single_obj["kinetic_plan"]
            _, v_res = _summarize_video_plan(v_obj)
            _, k_res = _summarize_kinetic_plan(k_obj)
            v_cnt = v_res["count"]
            br_cnt = v_res["broll_count"]
            sfx_cnt = v_res["sfx_count"]
            k_cnt = k_res["count"]
            v_sum = f"✓ Đã nạp ({v_cnt} cảnh · {br_cnt} meme · {sfx_cnt} sfx)"
            k_sum = f"✓ Đã nạp ({k_cnt} phụ đề động)"
            dual_summary = f"🎬✨ Đã nhận diện CẢ 2: Video Plan ({v_cnt} cảnh) & Kinetic Plan ({k_cnt} phụ đề)"
            return True, dual_summary, {
                "type": "dual_plan",
                "video_plan": v_obj,
                "kinetic_plan": k_obj,
                "video_summary": v_sum,
                "kinetic_summary": k_sum
            }
        # Hoặc chứa đồng thời cả timeline_segments và subtitles
        if _is_video_plan_dict(single_obj) and _is_kinetic_plan_dict(single_obj):
            _, v_res = _summarize_video_plan(single_obj)
            _, k_res = _summarize_kinetic_plan(single_obj)
            v_cnt = v_res["count"]
            br_cnt = v_res["broll_count"]
            sfx_cnt = v_res["sfx_count"]
            k_cnt = k_res["count"]
            v_sum = f"✓ Đã nạp ({v_cnt} cảnh · {br_cnt} meme · {sfx_cnt} sfx)"
            k_sum = f"✓ Đã nạp ({k_cnt} phụ đề động)"
            dual_summary = f"🎬✨ Đã nhận diện CẢ 2: Video Plan ({v_cnt} cảnh) & Kinetic Plan ({k_cnt} phụ đề)"
            return True, dual_summary, {
                "type": "dual_plan",
                "video_plan": single_obj,
                "kinetic_plan": single_obj,
                "video_summary": v_sum,
                "kinetic_summary": k_sum
            }

    # 4. Nếu chỉ có 1 JSON object hoặc thử fallback bóc code fence đơn lẻ
    if extracted_objs:
        data = extracted_objs[0]
    else:
        # Fallback bóc markdown code block
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        cand = match.group(1).strip() if match else text
        start = cand.find("{")
        end = cand.rfind("}")
        if start != -1 and end != -1 and end > start:
            cand = cand[start:end + 1]
        try:
            data = json.loads(cand)
        except Exception as e:
            return False, f"JSON không hợp lệ: {str(e)}", None

    if not isinstance(data, dict):
        return False, "Dữ liệu JSON phải là một Object {...}", None

    # Nhận diện 1: Video Plan V1
    if _is_video_plan_dict(data):
        summary, res = _summarize_video_plan(data)
        return True, summary, res

    # Nhận diện 2: Kinetic Subtitle Plan V2
    if _is_kinetic_plan_dict(data):
        summary, res = _summarize_kinetic_plan(data)
        return True, summary, res

    # Nhận diện 3: Cấu trúc JSON tùy biến có keys
    keys = list(data.keys())[:3]
    summary = f"Đã nhận diện: Kịch bản tùy biến ({', '.join(keys)})"
    return True, summary, {"type": "generic_plan", "data": data}

