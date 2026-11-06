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
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    ReferenceItem,
    normalize_creative_context,
    creative_context_to_dict,
)


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
    sort_key: tuple = ("", "", "")


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
    creative_context_dict: Dict[str, Any] = field(default_factory=dict)

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

    def get_creative_context(self) -> CreativeContext:
        """Trả về instance CreativeContext tươi mới, bất biến và độc lập từ cấu hình canonical.

        Quy định:
        - Nếu trường creative_context_dict vắng mặt (legacy missing), rỗng hoặc None, trả về CreativeContext rỗng chuẩn tắc.
        - Nếu creative_context_dict có dữ liệu: phải là Mapping và normalize thành CreativeContext.
        - Dữ liệu lỗi (không phải dict/mapping, hoặc trường con không hợp lệ) sẽ raise ValueError kiểm soát.
        - Tuyệt đối không cho phép alias rò rỉ; caller không thể mutate dữ liệu đã lưu trong config.
        """
        if not hasattr(self, "creative_context_dict"):
            return normalize_creative_context({})

        raw = self.creative_context_dict
        if raw is None or (isinstance(raw, dict) and len(raw) == 0):
            return normalize_creative_context({})

        if not isinstance(raw, (dict, Mapping)):
            raise ValueError(
                f"ERR_MALFORMED_CREATIVE_CONTEXT: creative_context_dict phải là Mapping hoặc dict, "
                f"nhận: {type(raw).__name__}"
            )

        return normalize_creative_context(raw)

    def set_creative_context(self, raw: Any) -> None:
        """Xác thực và lưu trữ CreativeContext chuẩn tắc vào creative_context_dict (transactional).

        Quy định:
        - raw phải là Mapping (hoặc dict) hoặc instance CreativeContext.
        - Bác bỏ None, bool, str, list hoặc kiểu không hợp lệ qua ValueError kiểm soát.
        - Thực hiện normalize và serialize hoàn chỉnh trước khi gán vào trường dữ liệu.
        - Nếu validation thất bại, giữ nguyên 100% cấu hình trước đó (transactional, last-good config).
        - Toàn bộ giá trị lưu trữ là JSON primitives, không giữ alias tới caller list/dict.
        """
        if raw is None:
            raise ValueError("ERR_INVALID_CREATIVE_CONTEXT: raw không được là None")
        if isinstance(raw, bool):
            raise ValueError("ERR_INVALID_CREATIVE_CONTEXT: raw không được là bool")
        if not isinstance(raw, (dict, Mapping, CreativeContext)):
            raise ValueError(
                f"ERR_INVALID_CREATIVE_CONTEXT: raw phải là Mapping hoặc CreativeContext, "
                f"nhận: {type(raw).__name__}"
            )

        # Chuẩn hóa và serialize (transactional: nếu lỗi, self.creative_context_dict được giữ nguyên)
        normalized = normalize_creative_context(raw)
        canonical_dict = creative_context_to_dict(normalized)

        # Gán bản sao sạch dạng JSON primitives
        self.creative_context_dict = canonical_dict


BATCH_DRAFT_REJECT_MSG = (
    "Đây là bản thảo của một lô, chưa phải kịch bản tổng thể. "
    "Không thể nạp vào Slot 1/Slot 2. Vui lòng nạp vào ô "
    "'Nạp Bản Thảo Cho Lô Đang Chọn' trong mục Phân Lô Kịch Bản phía trên."
)

PROJECT_OVERVIEW_REJECT_MSG = (
    "Đây là bản thảo tổng quan dự án (project overview draft), chưa phải định dạng kế hoạch "
    "chi tiết Slot 1 (Video Plan) hoặc Slot 2 (Kinetic Plan). Trình nhập kế hoạch chưa hỗ trợ "
    "nạp trực tiếp bản thảo tổng quan vào Slot."
)


def contains_project_overview_draft(data: Any, max_depth: int = 8, max_visits: int = 64) -> bool:
    """
    Pure helper phát hiện cấu trúc Project Overview Draft (bản thảo tổng quan dự án).
    Ngăn chặn nạp nhầm vào Slot 1 (Video Plan) hoặc Slot 2 (Kinetic Plan).

    Chính sách an toàn (Fail-closed, Container-semantic & DAG-aware):
    - Kiểm tra sự hiện diện của marker 'kind' == 'project_overview_draft'.
    - Hoặc discriminator tường minh: type == 'project_overview_draft' hoặc $schema == 'chundvc_project_overview_v1'.
    - Phân biệt DAG và Cycle qua active_ancestors (đang trên nhánh đệ quy hiện tại)
      và safe_nodes (node đã được thẩm định an toàn không chu trình và không marker).
      Shared alias giữa video_plan và kinetic_plan được chấp nhận, trong khi true cycle bị reject.
    - Container-semantic: Chỉ duyệt sâu vào recognized container keys:
      ('data', 'video_plan', 'kinetic_plan', 'generic_plan', 'dual_plan').
    - Bounded work / queue trước enqueue: Giới hạn max_depth và max_visits;
      nếu cạn ngân sách thì fail-closed trả về True ngay.
    """
    if not isinstance(data, dict):
        return False

    CONTAINER_KEYS = ("data", "video_plan", "kinetic_plan", "generic_plan", "dual_plan")

    active_ancestors: Set[int] = set()
    safe_nodes: Set[int] = set()
    visits = 0

    def _check_node(curr: Dict[str, Any], depth: int) -> bool:
        nonlocal visits

        curr_id = id(curr)

        # 1. Phát hiện chu trình thực sự (True Cycle)
        if curr_id in active_ancestors:
            return True

        # 2. Bỏ qua node đã thẩm định an toàn (Shared DAG node / Alias)
        if curr_id in safe_nodes:
            return False

        # 3. Kiểm tra giới hạn độ sâu
        if depth > max_depth:
            return True

        # 4. Kiểm tra ngân sách visits
        visits += 1
        if visits > max_visits:
            return True

        # 5. Kiểm tra markers trực tiếp trên dict hiện tại
        if curr.get("kind") == "project_overview_draft":
            return True
        if curr.get("type") == "project_overview_draft" or curr.get("$schema") == "chundvc_project_overview_v1":
            return True

        active_ancestors.add(curr_id)

        try:
            # 6. Duyệt có kiểm soát vào các recognized container keys
            for k in CONTAINER_KEYS:
                if k not in curr:
                    continue

                val = curr[k]

                if isinstance(val, dict):
                    if _check_node(val, depth + 1):
                        return True

                elif isinstance(val, (list, tuple)) or (hasattr(val, "__iter__") and not isinstance(val, (str, bytes, dict))):
                    list_steps = 0
                    for item in val:
                        list_steps += 1
                        if visits >= max_visits or list_steps > (max_visits - visits + 1):
                            return True
                        if isinstance(item, dict):
                            if _check_node(item, depth + 1):
                                return True

            safe_nodes.add(curr_id)
            return False

        finally:
            active_ancestors.remove(curr_id)

    return _check_node(data, 0)


def contains_batch_draft_plan(data: Any, max_depth: int = 8, max_visits: int = 64) -> bool:
    """
    Pure helper phát hiện cấu trúc Batch Draft (kịch bản phân lô).
    Ngăn chặn nạp nhầm vào Slot 1 (Video Plan) hoặc Slot 2 (Kinetic Plan).

    Chính sách an toàn (Fail-closed, Container-semantic & DAG-aware):
    - Kiểm tra sự hiện diện của key 'batch_id' (bất kể giá trị là None, "", False, 0 hay object).
    - Hoặc discriminator tường minh: type == 'batch_draft' hoặc $schema == 'chundvc_batch_draft_v1'.
    - Phân biệt DAG và Cycle qua active_ancestors (đang trên nhánh đệ quy hiện tại)
      và safe_nodes (node đã được thẩm định an toàn không chu trình và không marker).
      Shared alias giữa video_plan và kinetic_plan được chấp nhận, trong khi true cycle bị reject.
    - Container-semantic: Chỉ duyệt sâu vào recognized container keys:
      ('data', 'video_plan', 'kinetic_plan', 'generic_plan', 'dual_plan').
    - Bounded work / queue trước enqueue: Khi duyệt danh sách phần tử trong container,
      giới hạn số lần duyệt theo remaining budget (+ sentinel nhỏ), không enumerate
      toàn bộ danh sách rộng; nếu cạn ngân sách thì fail-closed trả về True ngay.
    - Giới hạn độ sâu (max_depth) và số lần duyệt (max_visits).
    """
    if not isinstance(data, dict):
        return False

    CONTAINER_KEYS = ("data", "video_plan", "kinetic_plan", "generic_plan", "dual_plan")

    active_ancestors: Set[int] = set()
    safe_nodes: Set[int] = set()
    visits = 0

    def _check_node(curr: Dict[str, Any], depth: int) -> bool:
        nonlocal visits

        curr_id = id(curr)

        # 1. Phát hiện chu trình thực sự (True Cycle)
        # Node nằm trên đường dẫn tổ tiên đang duyệt (active ancestor path) -> CYCLE!
        if curr_id in active_ancestors:
            return True

        # 2. Bỏ qua node đã thẩm định an toàn (Shared DAG node / Alias)
        if curr_id in safe_nodes:
            return False

        # 3. Kiểm tra giới hạn độ sâu
        if depth > max_depth:
            return True

        # 4. Kiểm tra ngân sách visits
        visits += 1
        if visits > max_visits:
            return True

        # 5. Kiểm tra markers trực tiếp trên dict hiện tại
        if "batch_id" in curr:
            return True
        if curr.get("type") == "batch_draft" or curr.get("$schema") == "chundvc_batch_draft_v1":
            return True

        active_ancestors.add(curr_id)

        try:
            # 6. Duyệt có kiểm soát vào các recognized container keys
            for k in CONTAINER_KEYS:
                if k not in curr:
                    continue

                val = curr[k]

                if isinstance(val, dict):
                    if _check_node(val, depth + 1):
                        return True

                elif isinstance(val, (list, tuple)) or (hasattr(val, "__iter__") and not isinstance(val, (str, bytes, dict))):
                    list_steps = 0
                    for item in val:
                        list_steps += 1
                        if visits >= max_visits or list_steps > (max_visits - visits + 1):
                            return True
                        if isinstance(item, dict):
                            if _check_node(item, depth + 1):
                                return True

            safe_nodes.add(curr_id)
            return False

        finally:
            active_ancestors.remove(curr_id)

    return _check_node(data, 0)


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

    # 1a. Project overview draft guard: Nếu bất kỳ JSON candidate nào chứa dấu vết Project Overview Draft,
    # từ chối toàn bộ đầu vào ngay TRƯỚC mọi phân loại target_slot/dual/auto.
    if any(contains_project_overview_draft(obj) for obj in extracted_objs):
        return False, PROJECT_OVERVIEW_REJECT_MSG, None

    # 1b. Batch draft guard: Nếu bất kỳ JSON candidate nào chứa dấu vết Batch Draft,
    # từ chối toàn bộ đầu vào ngay TRƯỚC mọi phân loại target_slot/dual/auto.
    if any(contains_batch_draft_plan(obj) for obj in extracted_objs):
        return False, BATCH_DRAFT_REJECT_MSG, None

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

    # Project overview draft guard trên fallback decode path
    if contains_project_overview_draft(data):
        return False, PROJECT_OVERVIEW_REJECT_MSG, None

    # Batch draft guard trên fallback decode path
    if contains_batch_draft_plan(data):
        return False, BATCH_DRAFT_REJECT_MSG, None

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

