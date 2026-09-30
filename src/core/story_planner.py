"""
Story Planner - tư duy biên tập theo cấu trúc câu chuyện và nhịp cắt (pacing).

Thay thế các heuristic ngây thơ ("lấy N giây đầu", "lấy mẫu đều") bằng:
- Chấm điểm giá trị nội dung từng câu thoại (mật độ thông tin, câu hỏi, từ khóa nhấn mạnh...).
- Chọn Hook mạnh nhất, các ý chính và Payoff (câu chốt) theo cấu trúc 3 hồi.
- Làm mượt nhịp cắt: nối các nhát cắt quá vụn, tách cảnh tĩnh quá dài để đổi nhịp.
"""
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.autocut import CutSegment

Subtitle = Dict[str, Any]

EMPHASIS_CUES = [
    "quan trọng", "bí quyết", "bí mật", "mẹo", "lý do", "sự thật", "sai lầm", "đừng", "tuyệt đối",
    "cực kỳ", "kết quả", "thật ra", "điều đặc biệt", "quan trọng nhất",
    "important", "secret", "mistake", "tip", "never", "result", "truth", "actually",
]
PAYOFF_CUES = [
    "tóm lại", "kết luận", "cuối cùng", "như vậy", "hãy", "đăng ký", "cảm ơn", "hẹn gặp",
    "in summary", "finally", "thank", "subscribe", "see you",
]
FILLERS = {"à", "ừm", "ờ", "ừ", "ơ", "hả", "dạ", "um", "uh", "er", "ah"}

# Preset nhịp dựng một cú bấm: (nhãn, tham số cho AIDirectorConfig, đệm im lặng quanh lời thoại)
PACING_PRESETS = {
    "relaxed": {
        "label": "🌿 Thong thả (Podcast, phỏng vấn)",
        "min_cut_gap": 0.6, "max_static_shot": 20.0, "min_punch_in_duration": 3.0, "padding_seconds": 0.35,
    },
    "balanced": {
        "label": "⚖ Cân bằng (YouTube tiêu chuẩn)",
        "min_cut_gap": 0.35, "max_static_shot": 12.0, "min_punch_in_duration": 1.5, "padding_seconds": 0.25,
    },
    "fast": {
        "label": "⚡ Nhanh (TikTok, Shorts)",
        "min_cut_gap": 0.15, "max_static_shot": 6.0, "min_punch_in_duration": 1.0, "padding_seconds": 0.15,
    },
}
DEFAULT_PACING = "balanced"


def get_pacing(key: str) -> Dict[str, Any]:
    """Trả về bản sao tham số của preset; khóa lạ rơi về preset mặc định."""
    return dict(PACING_PRESETS.get(key, PACING_PRESETS[DEFAULT_PACING]))


# Phân bổ ngân sách thời lượng cho bản tóm tắt theo cấu trúc 3 hồi
ACT_BOUNDS = [("intro", 0.0, 0.15, 0.15), ("body", 0.15, 0.85, 0.70), ("outro", 0.85, 1.0, 0.15)]


def _tokens(text: str) -> List[str]:
    return re.sub(r"[^\w\s]", "", text.lower()).split()


def _has_cue(text: str, cues: List[str]) -> bool:
    low = text.lower()
    return any(c in low for c in cues)


def score_subtitle(sub: Subtitle) -> float:
    """Điểm giá trị nội dung của một câu thoại trong khoảng 0..1."""
    text = sub.get("text", "")
    tokens = _tokens(text)
    dur = max(0.1, sub["end"] - sub["start"])
    if not tokens:
        return 0.0

    score = min(len(tokens) / dur, 4.0) / 4.0 * 0.25  # mật độ thông tin
    if 5 <= len(tokens) <= 25:
        score += 0.2
    elif len(tokens) < 3:
        score -= 0.15
    if any(ch.isdigit() for ch in text):
        score += 0.1
    if "?" in text or "!" in text:
        score += 0.15
    if _has_cue(text, EMPHASIS_CUES):
        score += 0.25

    words = sub.get("words") or []
    prob = sum(w.get("probability", 1.0) for w in words) / len(words) if words else 1.0
    score += prob * 0.15

    filler_ratio = sum(1 for t in tokens if t in FILLERS) / len(tokens)
    score -= filler_ratio * 0.3
    return max(0.0, min(1.0, score))


def _dur(sub: Subtitle) -> float:
    return sub["end"] - sub["start"]


def _greedy_pick(indices: List[int], subs: List[Subtitle], scores: List[float], budget: float,
                 chosen: Set[int]) -> float:
    """Chọn câu điểm cao nhất theo thứ tự giảm dần cho tới hết ngân sách. Trả về thời lượng đã dùng."""
    used = 0.0
    for i in sorted(indices, key=lambda k: scores[k], reverse=True):
        if i in chosen:
            continue
        d = _dur(subs[i])
        if used + d <= budget:
            chosen.add(i)
            used += d
    return used


def _bridge_gaps(subs: List[Subtitle], chosen: Set[int], budget_left: float, max_bridge: float = 3.0) -> None:
    """Nếu chỉ thiếu đúng 1 câu ngắn giữa hai câu được chọn, nối vào để không đứt mạch ý."""
    for i in range(1, len(subs) - 1):
        if i in chosen or (i - 1) not in chosen or (i + 1) not in chosen:
            continue
        d = _dur(subs[i])
        if d <= max_bridge and d <= budget_left:
            chosen.add(i)
            budget_left -= d


def _finish(subs: List[Subtitle], chosen: Set[int], roles: Dict[int, str],
            hook_idx: Optional[int]) -> Tuple[List[Subtitle], List[Dict[str, Any]], Dict[Tuple[float, float], str]]:
    ordered = sorted(chosen)
    selected = [subs[i] for i in ordered]
    role_map = {(subs[i]["start"], subs[i]["end"]): roles.get(i, "body") for i in ordered}
    markers: List[Dict[str, Any]] = []
    if hook_idx is not None and hook_idx in chosen:
        h = subs[hook_idx]
        markers.append({"time": h["start"], "duration": _dur(h), "name": "🔥 Hook",
                        "note": h.get("text", "")[:60], "color": "Green"})
    for i in ordered:
        if roles.get(i) == "payoff":
            p = subs[i]
            markers.append({"time": p["start"], "duration": _dur(p), "name": "🎯 Payoff",
                            "note": p.get("text", "")[:60], "color": "Blue"})
    return selected, markers, role_map


def plan_viral(subs: List[Subtitle], target: float):
    """Hook mạnh nhất (ưu tiên nửa đầu) + ý chính điểm cao + Payoff chốt. Giữ thứ tự thời gian."""
    if not subs:
        return [], [], {}
    if subs[-1]["end"] - subs[0]["start"] <= target:
        return list(subs), [], {(s["start"], s["end"]): "body" for s in subs}

    scores = [score_subtitle(s) for s in subs]
    n = len(subs)
    chosen: Set[int] = set()
    roles: Dict[int, str] = {}
    budget = target

    early = [i for i in range(max(1, int(n * 0.4))) if 3 <= len(_tokens(subs[i].get("text", ""))) and _dur(subs[i]) <= 10.0]
    hook_idx = max(early, key=lambda i: scores[i]) if early else 0
    chosen.add(hook_idx)
    roles[hook_idx] = "hook"
    budget -= _dur(subs[hook_idx])

    tail = [i for i in range(int(n * 0.85), n) if i != hook_idx and _has_cue(subs[i].get("text", ""), PAYOFF_CUES)]
    if tail:
        pay = max(tail, key=lambda i: scores[i])
        if _dur(subs[pay]) <= budget:
            chosen.add(pay)
            roles[pay] = "payoff"
            budget -= _dur(subs[pay])

    used = _greedy_pick(list(range(n)), subs, scores, budget, chosen)
    _bridge_gaps(subs, chosen, budget - used)
    return _finish(subs, chosen, roles, hook_idx)


def plan_summary(subs: List[Subtitle], target: float):
    """Tóm tắt theo cấu trúc 3 hồi (mở - thân - kết), mỗi hồi chọn câu điểm cao nhất."""
    if not subs:
        return [], [], {}
    start, end = subs[0]["start"], subs[-1]["end"]
    if end - start <= target:
        return list(subs), [], {(s["start"], s["end"]): "body" for s in subs}

    scores = [score_subtitle(s) for s in subs]
    span = end - start
    chosen: Set[int] = set()
    roles: Dict[int, str] = {}
    used_total = 0.0

    for name, lo, hi, share in ACT_BOUNDS:
        idxs = [i for i, s in enumerate(subs)
                if lo <= (s["start"] - start) / span < hi or (hi == 1.0 and (s["start"] - start) / span >= lo)]
        before = set(chosen)
        used_total += _greedy_pick(idxs, subs, scores, target * share, chosen)
        for i in chosen - before:
            roles[i] = {"intro": "hook", "body": "body", "outro": "payoff"}[name]

    # Ngân sách còn dư (hồi nào thiếu ý hay) được dồn cho câu điểm cao nhất còn lại
    used_total += _greedy_pick(list(range(len(subs))), subs, scores, target - used_total, chosen)
    _bridge_gaps(subs, chosen, target - used_total)
    hook_idx = min((i for i in chosen if roles.get(i) == "hook"), default=None)
    return _finish(subs, chosen, roles, hook_idx)


def bridge_tiny_cuts(segments: List[CutSegment], protected: List[Tuple[float, float]],
                     min_cut_gap: float) -> List[CutSegment]:
    """
    Nối các nhát cắt quá ngắn (< min_cut_gap) nằm giữa hai đoạn giữ để tránh nhịp cắt giật cục.
    Không nối các khoảng cắt trùng với đoạn thoại bị chủ động loại (bad take, câu bị loại).
    """
    if min_cut_gap <= 0:
        return segments
    ordered = sorted(segments, key=lambda s: s.start)
    result: List[CutSegment] = []
    for idx, seg in enumerate(ordered):
        is_tiny = (
            seg.action == "cut" and seg.duration < min_cut_gap
            and 0 < idx < len(ordered) - 1
            and ordered[idx - 1].action == "keep" and ordered[idx + 1].action == "keep"
            and not any(seg.start < p_end and seg.end > p_start for p_start, p_end in protected)
        )
        if is_tiny:
            result.append(CutSegment(start=seg.start, end=seg.end, action="keep", speed=1.0))
        else:
            result.append(seg)
    return result


def drop_orphan_slivers(segments: List[CutSegment], subs: List[Subtitle], min_keep: float = 0.3) -> List[CutSegment]:
    """
    Bỏ các mảnh giữ cực ngắn (< min_keep) không chứa lời thoại nào: phần đệm còn sót quanh câu bị cắt,
    nếu để lại sẽ thành khung hình chớp nháy giữa hai nhát cắt.
    """
    out = []
    for seg in segments:
        orphan = (
            seg.action == "keep" and seg.duration < min_keep
            and not any(s["start"] < seg.end and s["end"] > seg.start for s in subs)
        )
        out.append(CutSegment(start=seg.start, end=seg.end, action="cut", speed=1.0) if orphan else seg)
    return out


def split_long_takes(segments: List[CutSegment], subs: List[Subtitle], max_static: float) -> Tuple[List[CutSegment], int]:
    """
    Tách cảnh tĩnh quá dài (> max_static giây) tại ranh giới câu gần giữa nhất,
    để bước Punch-in tạo nhịp đổi khung hình giữa chừng. Trả về (segments, số lần tách).
    """
    if max_static <= 0:
        return segments, 0
    out: List[CutSegment] = []
    splits = 0
    for seg in segments:
        cur = seg
        while cur.action == "keep" and cur.duration > max_static:
            mid = (cur.start + cur.end) / 2
            cands = []
            for a, b in zip(subs, subs[1:]):
                point = (a["end"] + b["start"]) / 2 if b["start"] > a["end"] else a["end"]
                if cur.start + 2.0 < point < cur.end - 2.0:
                    cands.append(point)
            if not cands:
                break
            point = min(cands, key=lambda p: abs(p - mid))
            out.append(CutSegment(start=cur.start, end=point, action="keep", speed=cur.speed))
            cur = CutSegment(start=point, end=cur.end, action="keep", speed=cur.speed)
            splits += 1
        out.append(cur)
    return out, splits


def pacing_stats(segments: List[CutSegment]) -> Dict[str, float]:
    """Chỉ số nhịp dựng: độ dài shot trung bình và số nhát cắt / phút trên timeline đầu ra."""
    keeps = [s for s in segments if s.action != "cut"]
    if not keeps:
        return {"avg_shot_length": 0.0, "cuts_per_minute": 0.0, "longest_shot": 0.0}
    out_dur = sum(s.timeline_duration for s in keeps)
    return {
        "avg_shot_length": round(out_dur / len(keeps), 2),
        "cuts_per_minute": round((len(keeps) - 1) / out_dur * 60.0, 1) if out_dur > 0 else 0.0,
        "longest_shot": round(max(s.timeline_duration for s in keeps), 2),
    }
