#!/usr/bin/env python3
"""
ResolveFlow Agent - "bộ não" điều khiển ResolveFlow bằng ngôn ngữ tự nhiên
==========================================================================

Editor gõ yêu cầu ("làm phụ đề Hormozi, nhấn mạnh các con số") -> Claude tự
lên kế hoạch, gọi tools (Whisper, cắt khoảng lặng, dựng phụ đề lên V2, xem
frame preview), tự sửa nếu sai, rồi báo lại.

Cài đặt:
    pip install anthropic jsonschema faster-whisper
    export ANTHROPIC_API_KEY=...
Chạy (Resolve Studio đang mở, External scripting = Local):
    python resolveflow_agent.py
Chạy thử không cần Resolve (chỉ sinh .comp, không đặt lên timeline):
    RF_DRY_RUN=1 python resolveflow_agent.py

Cần nằm cùng thư mục với resolveflow_kinetic.py và director_text_plan.schema.json.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import traceback

try:
    from src.core.kinetic import kinetic_engine as rk
except ImportError:
    try:
        import resolveflow_kinetic as rk  # type: ignore
    except ImportError:
        import kinetic_engine as rk  # type: ignore

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "director_text_plan.schema.json")
OUT_DIR = os.path.join(HERE, "build")
DRY_RUN = os.environ.get("RF_DRY_RUN") == "1"

MAIN_MODEL = "claude-sonnet-5-5"     # làm việc chính, rẻ + nhanh
MAX_TURNS = 25                       # chặn vòng lặp vô hạn

with open(SCHEMA_PATH, encoding="utf-8") as f:
    PLAN_SCHEMA = json.load(f)


# --------------------------------------------------------------------------
# Trạng thái phiên: giữ transcript ở phía Python, AI chỉ thấy bản rút gọn
# --------------------------------------------------------------------------
class Session:
    words: list = []          # [{"word","start","end"}] của clip đang làm
    clip_name: str | None = None
    last_plan: dict | None = None


S = Session()


# --------------------------------------------------------------------------
# Helpers Resolve
# --------------------------------------------------------------------------
def _resolve_objects():
    resolve = rk.get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise RuntimeError("Chưa mở project nào trong Resolve.")
    tl = project.GetCurrentTimeline()
    if not tl:
        raise RuntimeError("Project chưa có timeline đang mở.")
    return resolve, project, tl


def _frames_to_tc(frame: int, fps: float) -> str:
    """Timecode non-drop-frame (đủ cho preview)."""
    fr = int(round(fps))
    h, rem = divmod(frame, fr * 3600)
    m, rem = divmod(rem, fr * 60)
    s, f = divmod(rem, fr)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _find_clip(tl, clip_name: str):
    for kind in ("video", "audio"):
        for t in range(1, tl.GetTrackCount(kind) + 1):
            for it in tl.GetItemListInTrack(kind, t) or []:
                if it.GetName() == clip_name:
                    return it
    return None


# --------------------------------------------------------------------------
# TOOL HANDLERS - mỗi hàm trả về dict (JSON) hoặc list content blocks (ảnh)
# --------------------------------------------------------------------------
def get_timeline_info() -> dict:
    if DRY_RUN:
        return {"dry_run": True, "fps": 30.0, "width": 1080, "height": 1920,
                "video_tracks": [{"index": 1, "clips": ["demo.mp4"]}], "audio_tracks": 1}
    _, project, tl = _resolve_objects()
    vt = []
    for t in range(1, tl.GetTrackCount("video") + 1):
        items = tl.GetItemListInTrack("video", t) or []
        vt.append({"index": t, "name": tl.GetTrackName("video", t),
                   "clips": [i.GetName() for i in items[:30]], "clip_count": len(items)})
    return {
        "project": project.GetName(), "timeline": tl.GetName(),
        "fps": float(tl.GetSetting("timelineFrameRate")),
        "width": int(tl.GetSetting("timelineResolutionWidth")),
        "height": int(tl.GetSetting("timelineResolutionHeight")),
        "start_frame": tl.GetStartFrame(), "end_frame": tl.GetEndFrame(),
        "video_tracks": vt, "audio_tracks": tl.GetTrackCount("audio"),
    }


def transcribe(clip_name: str, language: str = "vi") -> dict:
    """Whisper word-level. Nối trực tiếp vào engine của ResolveFlow."""
    if DRY_RUN:
        S.words, S.clip_name = rk.DEMO_WORDS, clip_name
    else:
        _, _, tl = _resolve_objects()
        item = _find_clip(tl, clip_name)
        if not item:
            return {"error": f"Không thấy clip '{clip_name}' trên timeline. Gọi get_timeline_info để xem tên clip."}
        path = item.GetMediaPoolItem().GetClipProperty("File Path")
        
        try:
            from src.core.transcriber import AudioTranscriber
            from src.core.autocut import AudioExtractor
            import tempfile
            temp_wav = os.path.join(tempfile.gettempdir(), f"rf_transcribe_{os.path.basename(path)}.wav")
            AudioExtractor.extract_audio(path, temp_wav)
            t_engine = AudioTranscriber(model_size="base")
            t_res = t_engine.transcribe(temp_wav, language=language)
            raw_subs = t_res.get("raw_subtitles", [])
            words = []
            for sub in raw_subs:
                w_list = sub.get("words", [])
                if w_list:
                    for w in w_list:
                        words.append({
                            "word": w.get("word", "").strip(),
                            "start": round(w.get("start", 0.0), 3),
                            "end": round(w.get("end", 0.0), 3)
                        })
                else:
                    parts = sub.get("text", "").strip().split()
                    st = sub.get("start", 0.0)
                    et = sub.get("end", 0.0)
                    step = (et - st) / max(len(parts), 1)
                    for idx, p in enumerate(parts):
                        words.append({
                            "word": p,
                            "start": round(st + idx * step, 3),
                            "end": round(st + (idx + 1) * step, 3)
                        })
            S.words = [w for w in words if w["word"]]
            S.clip_name = clip_name
        except Exception:
            from faster_whisper import WhisperModel
            model = WhisperModel("base", compute_type="auto")
            segs, _ = model.transcribe(path, language=language, word_timestamps=True)
            S.words = [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
                       for seg in segs for w in (seg.words or [])]
            S.clip_name = clip_name
    indexed = " ".join(f"[{i}]{w['word']}({w['start']:.2f})" for i, w in enumerate(S.words))
    return {"word_count": len(S.words), "transcript_indexed": indexed,
            "note": "Tham chiếu từ bằng chỉ số [i]. Không gõ lại transcript."}


def cut_silence(min_silence_s: float = 0.4, padding_s: float = 0.08) -> dict:
    """Gọi chức năng cắt khoảng lặng hiện có của ResolveFlow."""
    try:
        from src.core.autocut import SilenceDetector, SilenceCutConfig, AudioExtractor
        if DRY_RUN:
            return {"ok": True, "dry_run": True, "message": "Giả lập cắt khoảng lặng thành công."}
        _, _, tl = _resolve_objects()
        items = tl.GetItemListInTrack("video", 1) or []
        if not items:
            return {"error": "Không có clip nào trên Track V1 để cắt khoảng lặng."}
        target_clip = items[0]
        media_item = target_clip.GetMediaPoolItem()
        if not media_item:
            return {"error": "Clip không có MediaPoolItem liên kết."}
        video_path = media_item.GetClipProperty("File Path")
        import tempfile
        wav_path = os.path.join(tempfile.gettempdir(), "rf_agent_temp.wav")
        AudioExtractor.extract_audio(video_path, wav_path)
        cfg = SilenceCutConfig(min_silence_duration=min_silence_s, padding_duration=padding_s)
        intervals = SilenceDetector.detect_voice_intervals(wav_path, cfg)
        return {"ok": True, "voice_intervals_count": len(intervals), "sample": intervals[:5]}
    except Exception as e:
        return {"error": f"Lỗi cắt khoảng lặng: {e}"}


def apply_text_plan(plan: dict) -> dict:
    if not S.words:
        return {"error": "Chưa có transcript. Gọi transcribe trước."}
    try:
        rk.validate_plan(plan, len(S.words), SCHEMA_PATH)
    except Exception as e:                       # trả lỗi cho AI tự sửa plan
        return {"error": f"Plan không hợp lệ: {e}"}

    if DRY_RUN:
        fps, (W, H) = 30.0, rk.CANVAS[plan["canvas"]["aspect"]]
    else:
        _, _, tl = _resolve_objects()
        fps = float(tl.GetSetting("timelineFrameRate"))
        W, H = int(tl.GetSetting("timelineResolutionWidth")), int(tl.GetSetting("timelineResolutionHeight"))

    os.makedirs(OUT_DIR, exist_ok=True)
    pages = rk.build_pages(S.words, plan, fps)
    for p in pages:
        txt, _ = rk.page_to_comp(p, fps, W, H)
        with open(os.path.join(OUT_DIR, f"page_{p.idx:04d}.comp"), "w", encoding="utf-8") as f:
            f.write(txt)
    S.last_plan = plan
    with open(os.path.join(OUT_DIR, "last_plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)

    if not DRY_RUN:
        rk.place_on_timeline(pages, plan, OUT_DIR, track=2)
    return {"ok": True, "pages": len(pages), "dry_run": DRY_RUN,
            "page_frames": [[p.start_f, p.end_f, " ".join(w.text for w in p.words)] for p in pages[:40]]}


def preview_frame(frame: int):
    """Xuất 1 frame timeline thành ảnh để AI tự kiểm tra (chữ tràn, che mặt...)."""
    if DRY_RUN:
        return {"error": "Không có preview ở chế độ dry-run."}
    _, project, tl = _resolve_objects()
    tl.SetCurrentTimecode(_frames_to_tc(tl.GetStartFrame() + frame, float(tl.GetSetting("timelineFrameRate"))))
    path = os.path.join(OUT_DIR, "preview.jpg")
    # VERIFY: ExportCurrentFrameAsStill có thể cần đang ở trang Color tùy phiên bản.
    if not project.ExportCurrentFrameAsStill(path):
        return {"error": "ExportCurrentFrameAsStill thất bại."}
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode()
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
            {"type": "text", "text": f"Frame {frame} của timeline."}]


HANDLERS = {
    "get_timeline_info": get_timeline_info,
    "transcribe": transcribe,
    "cut_silence": cut_silence,
    "apply_text_plan": apply_text_plan,
    "preview_frame": preview_frame,
}

# Thao tác ghi đè timeline -> hỏi editor trước khi chạy
NEEDS_CONFIRM = {"cut_silence", "apply_text_plan"}


# --------------------------------------------------------------------------
# TOOL DEFINITIONS
# --------------------------------------------------------------------------
TOOLS = [
    {"name": "get_timeline_info",
     "description": "Xem project/timeline đang mở: fps, độ phân giải, track và tên clip. Gọi đầu tiên.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "transcribe",
     "description": "Chạy Whisper word-level trên một clip của timeline. Trả transcript có chỉ số [i] cho từng từ.",
     "input_schema": {"type": "object",
                      "properties": {"clip_name": {"type": "string"},
                                     "language": {"type": "string", "default": "vi"}},
                      "required": ["clip_name"]}},
    {"name": "cut_silence",
     "description": "Cắt khoảng lặng trên timeline. Thay đổi timeline, chỉ gọi khi editor yêu cầu.",
     "input_schema": {"type": "object",
                      "properties": {"min_silence_s": {"type": "number", "default": 0.4},
                                     "padding_s": {"type": "number", "default": 0.08}}}},
    {"name": "apply_text_plan",
     "description": ("Dựng phụ đề động lên track V2 theo Director Text Plan. Xóa và dựng lại toàn bộ V2. "
                     "Nếu trả về error, đọc lỗi, sửa plan và gọi lại."),
     # $defs phải nằm ở gốc input_schema để các "$ref": "#/$defs/..." còn trỏ đúng
     "input_schema": {"type": "object",
                      "properties": {"plan": {k: v for k, v in PLAN_SCHEMA.items()
                                              if k not in ("$defs", "$schema", "$id")}},
                      "required": ["plan"],
                      "$defs": PLAN_SCHEMA["$defs"]}},
    {"name": "preview_frame",
     "description": "Xuất một frame (tính từ đầu timeline) thành ảnh để kiểm tra phụ đề bằng mắt.",
     "input_schema": {"type": "object", "properties": {"frame": {"type": "integer", "minimum": 0}},
                      "required": ["frame"]}},
]

SYSTEM_PROMPT = """Bạn là ResolveFlow Agent, trợ lý dựng phim chạy bên trong DaVinci Resolve.

Quy tắc làm việc:
1. Luôn gọi get_timeline_info trước khi làm gì khác, để biết tên clip, fps, khung hình.
2. Phụ đề: transcribe -> lập Director Text Plan -> apply_text_plan -> preview_frame 1-2 page để tự kiểm tra.
3. Khi lập plan:
   - Tham chiếu từ bằng chỉ số [i]. Không bao giờ tự đặt timestamp hay gõ lại transcript.
   - Mỗi page 1-4 từ, ngắt theo nhịp nói và dấu câu; các page liên tục, phủ hết mọi từ.
   - emphasis "high" chỉ cho ~10-15% số từ: con số, kết quả, động từ mạnh, từ cảm xúc.
   - Không dùng quá 2 loại animation nổi bật liên tiếp; glitch/neon dùng tiết chế.
   - Khung 9:16 mặc định position "bottom" (đã tránh UI mạng xã hội); đổi "center" cho câu hook mở đầu.
   - text_override chỉ để sửa lỗi chính tả Whisper hoặc viết số dạng chữ số.
4. Nếu tool trả error, đọc kỹ, sửa và thử lại; sau 2 lần vẫn lỗi thì báo editor rõ nguyên nhân.
5. Trả lời editor bằng tiếng Việt, ngắn gọn: đã làm gì, bao nhiêu page, điều gì cần họ kiểm tra.
"""


# --------------------------------------------------------------------------
# AGENT LOOP
# --------------------------------------------------------------------------
_anthropic_client = None


def get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        try:
            import anthropic
            _anthropic_client = anthropic.Anthropic()
        except Exception as e:
            raise RuntimeError(f"Chưa cấu hình Anthropic API (cần pip install anthropic và ANTHROPIC_API_KEY): {e}")
    return _anthropic_client


def _confirm(name: str, args: dict) -> bool:
    preview = json.dumps(args, ensure_ascii=False)[:300]
    ans = input(f"\n[Xác nhận] Agent muốn chạy '{name}' {preview} ... (y/n): ").strip().lower()
    return ans in ("y", "yes", "c", "có", "co")


def run_tool(block) -> dict:
    name, args = block.name, block.input or {}
    if name not in HANDLERS:
        out, is_err = {"error": f"Tool không tồn tại: {name}"}, True
    elif name in NEEDS_CONFIRM and not DRY_RUN and not _confirm(name, args):
        out, is_err = {"error": "Editor từ chối thao tác này. Hỏi lại họ muốn gì."}, True
    else:
        try:
            out = HANDLERS[name](**args)
            is_err = isinstance(out, dict) and "error" in out
        except Exception as e:
            traceback.print_exc()
            out, is_err = {"error": f"{type(e).__name__}: {e}"}, True
    content = out if isinstance(out, list) else json.dumps(out, ensure_ascii=False)
    print(f"  ↳ {name}: {'LỖI' if is_err else 'ok'}")
    return {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_err}


def agent_turn(messages: list) -> None:
    """Chạy đến khi Claude trả lời xong (không còn gọi tool)."""
    try:
        client = get_anthropic_client()
    except Exception as e:
        print(f"\n[Lỗi Agent] {e}")
        return

    for _ in range(MAX_TURNS):
        resp = client.messages.create(
            model=MAIN_MODEL,
            max_tokens=16000,
            # cache system prompt + tools -> rẻ hơn ở các vòng lặp sau
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})
        for b in resp.content:
            if b.type == "text" and b.text.strip():
                print(f"\nAgent: {b.text}")
            elif b.type == "tool_use":
                print(f"  → gọi {b.name}")
        if resp.stop_reason != "tool_use":
            return
        results = [run_tool(b) for b in resp.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": results})
    print("\n[Dừng] Quá số vòng tối đa.")


def main():
    print("ResolveFlow Agent" + (" (DRY RUN)" if DRY_RUN else "") + " — gõ yêu cầu, 'exit' để thoát.")
    messages: list = []
    while True:
        try:
            req = input("\nEditor: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if req.lower() in ("exit", "quit", "thoát"):
            break
        if req:
            messages.append({"role": "user", "content": req})
            agent_turn(messages)


if __name__ == "__main__":
    sys.exit(main())
