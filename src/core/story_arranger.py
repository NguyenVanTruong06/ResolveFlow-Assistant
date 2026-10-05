"""
Story Arranger - sắp xếp timeline có ý đồ kể chuyện.

Toàn bộ làm việc trên timeline ĐÃ CẮT (mốc record time) nên phần bị cắt vẫn cắt:
1. build_blocks: chia timeline thành các khối ý (theo khoảng nghỉ thoại / điểm cắt, 3-25s) và chấm điểm
   (hook, nội dung, cao trào âm thanh, chuyển động).
2. tag_roles: gắn vai trò Hook / Mở đầu / Diễn biến / Cao trào / Kết / Phụ (heuristic, hoặc do LLM quyết định).
3. arrange: sắp xếp theo ý đồ
     - cold_open     : chép khoảnh khắc hay nhất lên đầu làm Hook, phần còn lại giữ nguyên thứ tự;
     - rising_action : đổi thứ tự các "cảnh" để kịch tính tăng dần, Mở đầu luôn đứng đầu, Kết luôn đứng cuối;
     - shorts        : công thức Hook -> Diễn biến -> Cao trào -> Chốt trong ngân sách thời lượng.
4. apply_arrangement: cắt lại events / phụ đề / marker theo thứ tự mới (kể cả nhiều clip nguồn).

Các khối thuộc cùng một cảnh (gần nhau trong clip nguồn) luôn giữ thứ tự gốc để không phá mạch.
"""
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from pydantic import BaseModel, Field

from src.core import story_planner
from src.core.vlog_hook import VlogHookGenerator

INTENTS = {
    "keep": "Giữ nguyên thứ tự (không sắp xếp lại)",
    "cold_open": "Mở bằng Hook (chép khoảnh khắc hay nhất lên đầu, giữ nguyên phần còn lại)",
    "rising_action": "Kịch tính dần (đổi thứ tự cảnh: Mở đầu → tăng dần → Cao trào → Kết)",
    "shorts": "Shorts / TikTok (Hook → Diễn biến → Cao trào → Chốt)",
    "aida": "AIDA Marketing (Attention/Hook → Interest/Vấn đề → Desire/Giải pháp → Action/CTA)",
    "pas": "PAS Chuyển Đổi (Problem/Nỗi đau → Agitation/Đẩy cao → Solution/Kết quả → Action)",
    "open_loop": "Open-Loop Retention (Teaser bí mật ở 0-3s → Giữ lời giải đến cuối)",
}
ROLE_LABELS = {"hook": "Hook", "intro": "Mở đầu", "build": "Diễn biến", "climax": "Cao trào",
               "outro": "Kết", "filler": "Phụ"}
ROLE_COLORS = {"hook": "Green", "intro": "Blue", "build": "Yellow", "climax": "Red",
               "outro": "Purple", "filler": "Cyan"}


class Block(BaseModel):
    id: int
    t0: float
    t1: float
    text: str = ""
    video_path: str = ""
    src_start: float = 0.0
    src_end: float = 0.0
    speech: float = 0.0
    hook: float = 0.0
    content: float = 0.0
    energy: Optional[float] = None
    motion: Optional[float] = None
    intensity: float = 0.0
    peak_t0: float = 0.0     # câu đắt giá nhất trong khối (để cắt trích đoạn cho hook/shorts)
    peak_t1: float = 0.0
    gap_before: float = 0.0   # độ mạnh ranh giới phía trước (khoảng nghỉ thoại / điểm cắt nguồn)
    scene: int = 0
    role: str = "build"
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.t1 - self.t0


class ArrangedItem(BaseModel):
    block_id: int
    t0: float
    t1: float
    role: str
    reason: str = ""
    is_copy: bool = False
    enabled: bool = True


class Arrangement(BaseModel):
    intent: str
    items: List[ArrangedItem] = Field(default_factory=list)
    total_seconds: float = 0.0
    changed: bool = False


# ----------------------------------------------------------------------------
# 1. Chia khối & chấm điểm
# ----------------------------------------------------------------------------
def _boundaries(events: Sequence[Dict[str, Any]], subtitles: Sequence[Dict[str, Any]], pause: float) -> List[float]:
    pts = set()
    subs = sorted(subtitles, key=lambda s: s["start"])
    for a, b in zip(subs, subs[1:]):
        if b["start"] - a["end"] >= pause:
            pts.add((a["end"] + b["start"]) / 2)
    for prev, cur in zip(events, events[1:]):
        jump = cur["src_in"] - prev["src_out"]
        if cur["video_path"] != prev["video_path"] or abs(jump) > 2.0:
            pts.add(cur["rec_in"])
    return sorted(pts)


def _span_signals(events, t0, t1, energy_by_video, activity_by_video):
    """Tín hiệu âm thanh / chuyển động trong một đoạn record time (tính trên mốc nguồn của từng event)."""
    energies, motions, weights = [], [], []
    first, last = None, None
    for ev in events:
        a, b = max(t0, ev["rec_in"]), min(t1, ev["rec_out"])
        if b - a <= 1e-3:
            continue
        speed = ev.get("speed", 1.0) or 1.0
        s0 = ev["src_in"] + (a - ev["rec_in"]) * speed
        s1 = ev["src_in"] + (b - ev["rec_in"]) * speed
        first = first or (ev["video_path"], s0)
        last = (ev["video_path"], s1)
        e_arr = energy_by_video.get(ev["video_path"])
        if e_arr is not None and len(e_arr):
            e_np = np.asarray(e_arr)
            seg = e_np[int(s0): max(int(s0) + 1, int(np.ceil(s1)))]
            if len(seg):
                energies.append(float(np.clip((float(seg.max()) - float(np.median(e_np))) / 15.0, 0.0, 1.0)))
                weights.append(b - a)
        a_arr = activity_by_video.get(ev["video_path"])
        if a_arr is not None and len(a_arr):
            a_np = np.asarray(a_arr)
            seg = a_np[int(s0): max(int(s0) + 1, int(np.ceil(s1)))]
            if len(seg):
                ref = max(float(np.percentile(a_np, 90)), 0.15)
                motions.append(float(np.clip(float(np.mean(seg)) / ref, 0.0, 1.0)))
    energy = max(energies) if energies else None
    motion = float(np.average(motions, weights=weights[: len(motions)])) if motions and len(weights) >= len(motions) else (
        float(np.mean(motions)) if motions else None)
    return energy, motion, first, last


def build_blocks(
    events: Sequence[Dict[str, Any]],
    subtitles: Sequence[Dict[str, Any]],
    energy_by_video: Optional[Dict[str, np.ndarray]] = None,
    activity_by_video: Optional[Dict[str, np.ndarray]] = None,
    target_block: float = 12.0,
    max_block: float = 25.0,
    min_block: float = 3.0,
    pause: float = 1.2,
    scene_gap: float = 60.0,
    chapter_min: float = 150.0,
    chapter_max: float = 420.0,
) -> List[Block]:
    energy_by_video = energy_by_video or {}
    activity_by_video = activity_by_video or {}
    if not events:
        return []
    total = max(ev["rec_out"] for ev in events)
    bounds = _boundaries(events, subtitles, pause)

    cuts: List[Tuple[float, float]] = []
    cur = 0.0
    while cur < total - 1e-6:
        if total - cur <= max_block + min_block:
            end = total
        else:
            cands = [b for b in bounds if cur + min_block <= b <= cur + max_block]
            end = min(cands, key=lambda b: abs(b - (cur + target_block))) if cands else cur + max_block
        cuts.append((cur, end))
        cur = end
    if len(cuts) > 1 and cuts[-1][1] - cuts[-1][0] < min_block:
        last = cuts.pop()
        cuts[-1] = (cuts[-1][0], last[1])

    subs = sorted(subtitles, key=lambda s: s["start"])
    blocks: List[Block] = []
    for i, (t0, t1) in enumerate(cuts):
        inside = [s for s in subs if s["end"] > t0 and s["start"] < t1]
        dur = max(1e-6, t1 - t0)
        covered = sum(max(0.0, min(s["end"], t1) - max(s["start"], t0)) for s in inside)
        hook, content, peak, peak_val = 0.0, 0.0, None, -1.0
        for s in inside:
            h = min(1.0, VlogHookGenerator.score_subtitle_hook(s.get("text", ""))[0] / 10.0)
            c = story_planner.score_subtitle({"start": s["start"], "end": s["end"], "text": s.get("text", ""),
                                              "words": s.get("words") or []})
            hook = max(hook, h)
            content += c
            if h + c > peak_val:
                peak_val, peak = h + c, s
        content = content / len(inside) if inside else 0.0
        energy, motion, first, last = _span_signals(events, t0, t1, energy_by_video, activity_by_video)

        parts = {"hook": (hook, 0.20), "content": (content, 0.10)}
        if energy is not None:
            parts["energy"] = (energy, 0.35)
        if motion is not None:
            parts["motion"] = (motion, 0.35)
        wsum = sum(w for _, w in parts.values())
        intensity = sum(v * w for v, w in parts.values()) / wsum

        blocks.append(Block(
            id=i, t0=t0, t1=t1, text=" ".join(s.get("text", "").strip() for s in inside)[:300],
            video_path=first[0] if first else "", src_start=first[1] if first else 0.0,
            src_end=last[1] if last else 0.0, speech=min(1.0, covered / dur), hook=hook, content=content,
            energy=energy, motion=motion, intensity=round(intensity, 4),
            peak_t0=max(t0, peak["start"]) if peak else t0, peak_t1=min(t1, peak["end"]) if peak else min(t1, t0 + 3.0),
        ))

    scene = 0
    for prev, cur_b in zip(blocks, blocks[1:]):
        gap = cur_b.src_start - prev.src_end
        jump = cur_b.video_path != prev.video_path or gap > scene_gap or gap < -0.5
        if jump:
            scene += 1
        cur_b.scene = scene
        # Độ mạnh ranh giới: điểm cắt nguồn rất mạnh; nếu không thì là khoảng nghỉ thoại quanh ranh giới
        before = [s for s in subs if s["end"] <= cur_b.t0]
        after = [s for s in subs if s["start"] >= cur_b.t0]
        pause_len = (after[0]["start"] - before[-1]["end"]) if before and after else 0.0
        cur_b.gap_before = 100.0 if jump else max(0.0, pause_len)
    if chapter_max > 0:
        assign_chapters(blocks, chapter_min, chapter_max)
    return blocks


def assign_chapters(blocks: List[Block], min_len: float = 150.0, max_len: float = 420.0) -> None:
    """
    Chia các cảnh quá dài (vlog quay liền mạch) thành "chương" 2.5-7 phút, cắt ở ranh giới nghỉ rõ nhất,
    để bước sắp xếp có đơn vị đủ lớn mà không xé ngang một đoạn đang diễn ra.
    """
    runs: List[List[Block]] = []
    for b in blocks:
        if runs and runs[-1][0].scene == b.scene:
            runs[-1].append(b)
        else:
            runs.append([b])
    chapter = 0
    for run in runs:
        start = 0
        while start < len(run):
            t_start = run[start].t0
            cands = [k for k in range(start + 1, len(run)) if min_len <= run[k].t0 - t_start <= max_len]
            end = len(run) if (run[-1].t1 - t_start <= max_len or not cands) else max(cands, key=lambda k: run[k].gap_before)
            for b in run[start:end]:
                b.scene = chapter
            chapter += 1
            start = end


# ----------------------------------------------------------------------------
# 2. Gắn vai trò
# ----------------------------------------------------------------------------
def _has_payoff_cue(text: str) -> bool:
    low = text.lower()
    return any(c in low for c in story_planner.PAYOFF_CUES)


def tag_roles(blocks: List[Block], roles_override: Optional[Dict[int, str]] = None) -> List[Block]:
    n = len(blocks)
    for b in blocks:
        b.role, b.reason = "build", "Diễn biến nội dung"
        if b.intensity < 0.12 and b.hook == 0 and b.speech < 0.15:
            b.role, b.reason = "filler", "Ít thoại, ít chuyển động"
    if n == 0:
        return blocks

    if n >= 3:
        blocks[0].role, blocks[0].reason = "intro", "Mở đầu video"
        blocks[-1].role = "outro"
        blocks[-1].reason = "Câu chốt/kêu gọi" if _has_payoff_cue(blocks[-1].text) else "Đoạn kết video"
    pool = blocks[1:-1] if n >= 3 else list(blocks)
    pool = [b for b in pool if b.role != "filler"] or pool
    hook = max(pool, key=lambda b: 0.45 * b.hook + 0.35 * b.intensity + 0.20 * b.content)
    hook.role, hook.reason = "hook", "Khoảnh khắc thu hút nhất (lời hook + cao trào)"
    rest = [b for b in pool if b is not hook and b.role != "filler"]
    if rest:
        climax = max(rest, key=lambda b: b.intensity)
        climax.role, climax.reason = "climax", "Đỉnh cường độ (âm thanh + chuyển động)"

    for bid, role in (roles_override or {}).items():
        if role in ROLE_LABELS and 0 <= bid < n:
            blocks[bid].role = role
            blocks[bid].reason = "LLM xác định vai trò: " + ROLE_LABELS[role]
    return blocks


def llm_assign_roles(blocks: List[Block], intent: str, api_key: str, selector: Any = None) -> Tuple[Dict[int, str], str]:
    """
    Nhờ LLM gán vai trò cho từng khối. Trả về ({block_id: role}, lý do). Lỗi bất kỳ -> ném ValueError
    để người gọi tự động dùng heuristic.
    """
    if selector is None:
        from src.core.llm_director import LLMSemanticSelector
        selector = LLMSemanticSelector(api_key=api_key)
    lines = [f'{b.id}. [{b.duration:.0f}s] "{b.text[:140]}" (cường độ {b.intensity:.2f})' for b in blocks]
    prompt = (
        "Bạn là đạo diễn dựng phim. Dưới đây là các khối nội dung của một video theo thứ tự thời gian.\n"
        f"Ý đồ dựng: {INTENTS.get(intent, intent)}.\n"
        "Gán cho MỖI khối đúng một vai trò trong: hook, intro, build, climax, outro, filler. "
        "Chỉ đúng MỘT hook và MỘT climax; khối đầu thường là intro, khối cuối thường là outro.\n"
        'Trả về JSON: {"roles": {"<id>": "<role>", ...}, "reasoning": "<lý do ngắn>"}\n\n' + "\n".join(lines)
    )
    data = selector.complete_json(prompt)
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict) or not roles:
        raise ValueError("LLM không trả về trường 'roles' hợp lệ.")
    out: Dict[int, str] = {}
    for k, v in roles.items():
        try:
            bid = int(k)
        except (TypeError, ValueError):
            raise ValueError(f"Id khối không hợp lệ: {k}")
        if not (0 <= bid < len(blocks)) or v not in ROLE_LABELS:
            raise ValueError(f"Vai trò/id không hợp lệ: {k}={v}")
        out[bid] = v
    return out, str(data.get("reasoning", ""))


# ----------------------------------------------------------------------------
# 3. Sắp xếp
# ----------------------------------------------------------------------------
def _trim(block: Block, max_len: float) -> Tuple[float, float]:
    """Trích đoạn tối đa max_len giây quanh câu đắt giá nhất của khối (không cắt ngang câu nếu có thể)."""
    if block.duration <= max_len:
        return block.t0, block.t1
    start = max(block.t0, block.peak_t0 - 0.2)
    end = max(start + max_len, min(block.peak_t1 + 0.3, start + max_len + 4.0))
    end = min(block.t1, end)
    if end - start < max_len:
        start = max(block.t0, end - max_len)
    return start, end


def _item(block: Block, span: Optional[Tuple[float, float]] = None, is_copy: bool = False) -> ArrangedItem:
    a, b = span if span else (block.t0, block.t1)
    return ArrangedItem(block_id=block.id, t0=a, t1=b, role=block.role, reason=block.reason, is_copy=is_copy)


def _first(blocks: Sequence[Block], role: str) -> Optional[Block]:
    return next((b for b in blocks if b.role == role), None)


def arrange(
    blocks: List[Block],
    intent: str,
    target_seconds: float = 60.0,
    cold_open_len: float = 6.0,
) -> Arrangement:
    original = [_item(b) for b in blocks]
    total_original = sum(b.duration for b in blocks)
    if intent == "keep" or not blocks:
        return Arrangement(intent=intent, items=original, total_seconds=total_original, changed=False)

    hook = _first(blocks, "hook")

    if intent == "cold_open":
        if hook is None:
            return Arrangement(intent=intent, items=original, total_seconds=total_original, changed=False)
        opener = _item(hook, _trim(hook, cold_open_len), is_copy=True)
        opener.reason = "Hook mở đầu: " + hook.reason
        items = [opener] + original
        return Arrangement(intent=intent, items=items, total_seconds=sum(i.t1 - i.t0 for i in items), changed=True)

    if intent == "rising_action":
        intro, outro = _first(blocks, "intro"), _first(blocks, "outro")
        middle = [b for b in blocks if b is not intro and b is not outro]
        scenes: Dict[int, List[Block]] = {}
        for b in middle:
            scenes.setdefault(b.scene, []).append(b)

        def scene_score(bs: List[Block]) -> float:
            top = sorted((x.intensity for x in bs), reverse=True)[:2]
            return sum(top) / len(top)

        ordered_scenes = sorted(scenes.values(), key=lambda bs: (scene_score(bs), bs[0].t0))
        order = ([intro] if intro else []) + [b for bs in ordered_scenes for b in bs] + ([outro] if outro else [])
        items = [_item(b) for b in order]
        changed = [i.block_id for i in items] != [i.block_id for i in original]
        return Arrangement(intent=intent, items=items, total_seconds=total_original, changed=changed)

    if intent == "shorts":
        budget = max(10.0, target_seconds)
        hook_len = min(5.0, max(3.0, 0.1 * budget))
        payoff_len = min(6.0, max(3.0, 0.12 * budget))
        outro = _first(blocks, "outro")
        lead = _item(hook, _trim(hook, hook_len)) if hook else None
        tail = _item(outro, _trim(outro, payoff_len)) if outro and outro is not hook else None
        used = sum((x.t1 - x.t0) for x in (lead, tail) if x)
        skip = {b.id for b in (hook, outro) if b}
        cands = [b for b in blocks if b.id not in skip and b.role != "filler"]

        def value(b: Block) -> float:
            return 0.5 * b.intensity + 0.3 * b.content + 0.2 * b.hook

        climax = _first(blocks, "climax")
        order = sorted(cands, key=value, reverse=True)
        if climax in order:
            order.remove(climax)
            order.insert(0, climax)
        picked: List[ArrangedItem] = []
        for b in order:
            span = _trim(b, 10.0)
            if used + (span[1] - span[0]) <= budget + 1e-6:
                picked.append(_item(b, span))
                used += span[1] - span[0]
        picked.sort(key=lambda it: it.t0)
        items = ([lead] if lead else []) + picked + ([tail] if tail else [])
        return Arrangement(intent=intent, items=items, total_seconds=used, changed=True)

    if intent == "aida":
        # Attention (Hook) -> Interest (Intro/Build) -> Desire (Climax/Solution) -> Action (Outro/CTA)
        budget = target_seconds if target_seconds and target_seconds > 0 else float("inf")
        hook = _first(blocks, "hook") or _first(blocks, "climax") or blocks[0]
        intro = _first(blocks, "intro")
        climax = _first(blocks, "climax") or hook
        outro = _first(blocks, "outro")
        used_ids = {b.id for b in (hook, intro, climax, outro) if b}
        middle = [b for b in blocks if b.id not in used_ids and b.role != "filler"]

        order = []
        used = 0.0
        if hook:
            span = _trim(hook, 6.0) if budget < float("inf") else (hook.t0, hook.t1)
            order.append(_item(hook, span))
            used += span[1] - span[0]
        if intro and intro.id != hook.id:
            span = _trim(intro, 10.0) if budget < float("inf") else (intro.t0, intro.t1)
            if used + (span[1] - span[0]) <= budget + 1e-6:
                order.append(_item(intro, span))
                used += span[1] - span[0]
        for b in middle:
            span = _trim(b, 10.0) if budget < float("inf") else (b.t0, b.t1)
            if used + (span[1] - span[0]) <= budget + 1e-6:
                order.append(_item(b, span))
                used += span[1] - span[0]
        if climax and climax.id != hook.id and climax.id != (intro.id if intro else -1):
            span = _trim(climax, 10.0) if budget < float("inf") else (climax.t0, climax.t1)
            if used + (span[1] - span[0]) <= budget + 1e-6:
                order.append(_item(climax, span))
                used += span[1] - span[0]
        if outro and outro.id not in {it.block_id for it in order}:
            span = _trim(outro, 8.0) if budget < float("inf") else (outro.t0, outro.t1)
            if used + (span[1] - span[0]) <= budget + 1e-6 or not order:
                order.append(_item(outro, span))
                used += span[1] - span[0]

        return Arrangement(intent=intent, items=order, total_seconds=used, changed=True)

    if intent == "pas":
        # Problem (Intro/Vấn đề) -> Agitation (Build/Đẩy cao) -> Solution (Climax/Kết quả) -> Action (Outro)
        budget = target_seconds if target_seconds and target_seconds > 0 else float("inf")
        problem = _first(blocks, "intro") or blocks[0]
        solution = _first(blocks, "climax") or _first(blocks, "hook")
        action = _first(blocks, "outro")
        used_ids = {b.id for b in (problem, solution, action) if b}
        agitation = [b for b in blocks if b.id not in used_ids and b.role != "filler"]
        # Sắp xếp các đoạn agitation theo cường độ tăng dần
        agitation.sort(key=lambda b: b.intensity)

        order = []
        used = 0.0
        if problem:
            span = _trim(problem, 10.0) if budget < float("inf") else (problem.t0, problem.t1)
            order.append(_item(problem, span))
            used += span[1] - span[0]

        for b in agitation:
            span = _trim(b, 10.0) if budget < float("inf") else (b.t0, b.t1)
            if used + (span[1] - span[0]) <= budget + 1e-6:
                order.append(_item(b, span))
                used += span[1] - span[0]

        if solution and solution.id != problem.id:
            span = _trim(solution, 10.0) if budget < float("inf") else (solution.t0, solution.t1)
            if used + (span[1] - span[0]) <= budget + 1e-6:
                order.append(_item(solution, span))
                used += span[1] - span[0]

        if action and action.id not in {it.block_id for it in order}:
            span = _trim(action, 8.0) if budget < float("inf") else (action.t0, action.t1)
            if used + (span[1] - span[0]) <= budget + 1e-6 or not order:
                order.append(_item(action, span))
                used += span[1] - span[0]

        return Arrangement(intent=intent, items=order, total_seconds=used, changed=True)

    if intent == "open_loop":
        # Chép 3-4s khoảnh khắc gây tò mò / cao trào lên đầu (Teaser), mạch chính giữ nguyên đến cuối mới lộ kết quả
        highlight = _first(blocks, "hook") or _first(blocks, "climax")
        if highlight is None:
            return Arrangement(intent=intent, items=original, total_seconds=total_original, changed=False)
        teaser = _item(highlight, _trim(highlight, 4.5), is_copy=True)
        teaser.reason = "Open-Loop Teaser: Mở vòng lặp tò mò trong 3s đầu"
        
        budget = target_seconds if target_seconds and target_seconds > 0 else float("inf")
        if budget < float("inf"):
            used = teaser.t1 - teaser.t0
            items = [teaser]
            for it in original:
                span_len = it.t1 - it.t0
                if used + span_len <= budget + 1e-6:
                    items.append(it)
                    used += span_len
            return Arrangement(intent=intent, items=items, total_seconds=used, changed=True)
        else:
            items = [teaser] + original
            return Arrangement(intent=intent, items=items, total_seconds=sum(i.t1 - i.t0 for i in items), changed=True)

    raise ValueError(f"Ý đồ không hỗ trợ: {intent}")


# ----------------------------------------------------------------------------
# 4. Áp lại lên timeline
# ----------------------------------------------------------------------------
def slice_events(events: Sequence[Dict[str, Any]], a: float, b: float) -> List[Dict[str, Any]]:
    """Cắt các event của timeline gốc theo đoạn record time [a, b], giữ đúng mốc nguồn (kể cả tua nhanh)."""
    out = []
    for ev in events:
        oa, ob = max(a, ev["rec_in"]), min(b, ev["rec_out"])
        if ob - oa <= 1e-3:
            continue
        speed = ev.get("speed", 1.0) or 1.0
        new = dict(ev)
        new["src_in"] = ev["src_in"] + (oa - ev["rec_in"]) * speed
        new["src_out"] = ev["src_in"] + (ob - ev["rec_in"]) * speed
        new["_rec_a"], new["_rec_b"] = oa, ob
        out.append(new)
    return out


def apply_arrangement(
    events: Sequence[Dict[str, Any]],
    subtitles: Sequence[Dict[str, Any]],
    markers: Sequence[Dict[str, Any]],
    arrangement: Arrangement,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    new_events: List[Dict[str, Any]] = []
    new_subs: List[Dict[str, Any]] = []
    new_markers: List[Dict[str, Any]] = []
    cursor = 0.0
    for item in arrangement.items:
        if not getattr(item, "enabled", True):
            continue
        a, b = item.t0, item.t1
        for ev in slice_events(events, a, b):
            oa, ob = ev.pop("_rec_a"), ev.pop("_rec_b")
            ev["rec_in"] = cursor + (oa - a)
            ev["rec_out"] = cursor + (ob - a)
            new_events.append(ev)

        for s in subtitles:
            if s["end"] <= a or s["start"] >= b:
                continue
            ns = dict(s)
            ns["start"] = cursor + max(s["start"], a) - a
            ns["end"] = cursor + min(s["end"], b) - a
            words = s.get("words") or []
            if words:
                kept = [dict(w, start=cursor + max(w["start"], a) - a, end=cursor + min(w["end"], b) - a)
                        for w in words if w["end"] > a and w["start"] < b]
                if not kept:
                    continue
                ns["words"] = kept
                if len(kept) != len(words):
                    ns["text"] = " ".join(w["word"].strip() for w in kept)
                    ns["start"], ns["end"] = kept[0]["start"], kept[-1]["end"]
            new_subs.append(ns)

        for m in markers:
            if a <= m.get("time", -1.0) < b:
                new_markers.append(dict(m, time=cursor + m["time"] - a))
        new_markers.append({
            "time": cursor, "duration": min(b - a, 3.0),
            "name": f"🧭 {ROLE_LABELS.get(item.role, item.role)}" + (" (mở đầu)" if item.is_copy else ""),
            "note": item.reason, "color": ROLE_COLORS.get(item.role, "Blue"),
        })
        cursor += b - a
    new_markers.sort(key=lambda m: m["time"])
    return _merge_contiguous(new_events), new_subs, new_markers


def _merge_contiguous(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gộp các event liền kề liên tục cả ở timeline lẫn mốc nguồn (do cắt khối sinh ra) để không chia clip vô ích."""
    keys = ("video_path", "speed", "punch_in", "punch_in_scale", "is_speedup")
    merged: List[Dict[str, Any]] = []
    for ev in events:
        prev = merged[-1] if merged else None
        if (prev and all(prev.get(k) == ev.get(k) for k in keys)
                and abs(prev["rec_out"] - ev["rec_in"]) < 1e-3 and abs(prev["src_out"] - ev["src_in"]) < 1e-3):
            prev["rec_out"], prev["src_out"] = ev["rec_out"], ev["src_out"]
        else:
            merged.append(ev)
    return merged


def describe(arrangement: Arrangement, blocks: Sequence[Block]) -> List[str]:
    """Các dòng mô tả kế hoạch sắp xếp để hiển thị cho editor duyệt."""
    by_id = {b.id: b for b in blocks}
    lines = []
    for n, it in enumerate(arrangement.items, 1):
        b = by_id.get(it.block_id)
        snippet = (b.text[:50] + "…") if b and b.text else "(không lời)"
        state_tag = " [TẮT]" if not getattr(it, "enabled", True) else (" [chép]" if it.is_copy else "")
        lines.append(f"{n:2d}. {ROLE_LABELS.get(it.role, it.role):<9} {it.t0:7.1f}s-{it.t1:7.1f}s "
                     f"({it.t1 - it.t0:4.1f}s){state_tag}  {snippet}")
    return lines

