"""
Edit Policy - "tư duy của người dựng" cho việc cắt khoảng lặng và tua nhanh.

Người dựng không cắt mọi khoảng lặng. Họ tự hỏi: video này là loại gì, khoảng lặng này có đáng cắt không,
cắt rồi có bị giật không, tua thì tua đoạn nào để khán giả không thấy rối. Module này mã hóa các luật đó:

1. classify_video: nhận diện "talk" (nói chuyện, talkshow, ngồi nói, hình tĩnh), "vlog" (quay cảnh, nhiều chuyển
   động, ít lời) hoặc "mixed".
2. EditPolicy theo từng loại:
   - Khoảng lặng ngắn hơn min_cut là nhịp thở tự nhiên: GIỮ, không cắt/tua.
   - Khoảng lặng hình đang chuyển động (vlog): GIỮ; chỉ khi rất dài mới TUA.
   - Khoảng lặng tĩnh và đủ dài: CẮT (không khí chết).
   - Tua chỉ dành cho đoạn đủ dài (speed_min_len), tốc độ tăng theo độ dài, và hai đoạn tua phải cách nhau
     tối thiểu speed_gap_min giây thoại bình thường (tránh kiểu "tua - bình thường - tua" gây khó chịu).
   - Mật độ nhát cắt có trần (max_cuts_per_min): thừa thì chỉ giữ những nhát cắt đáng giá nhất.
"""
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from pydantic import BaseModel

from src.core.autocut import CutSegment
from src.core.scene_activity import default_active_threshold


class EditPolicy(BaseModel):
    key: str
    label: str
    min_cut: float               # khoảng lặng ngắn hơn mức này (giây) = nhịp thở, giữ nguyên
    protect_active: bool         # khoảng lặng có chuyển động hình ảnh thì không cắt
    speed_min_len: float         # chỉ tua đoạn dài ít nhất từng này giây
    speed_gap_min: float         # 2 đoạn tua phải cách nhau ít nhất từng này giây thoại bình thường
    max_cuts_per_min: float      # trần số nhát cắt mỗi phút
    summary: str = ""


POLICIES: Dict[str, EditPolicy] = {
    "talk": EditPolicy(
        key="talk", label="Nói chuyện / Talkshow / Phỏng vấn", min_cut=0.35, protect_active=False,
        speed_min_len=15.0, speed_gap_min=30.0, max_cuts_per_min=14.0,
        summary="Cắt khoảng lặng từ 0,35s, giữ nhịp thở; tua chỉ khi lặng dài từ 15s",
    ),
    "mixed": EditPolicy(
        key="mixed", label="Hỗn hợp", min_cut=1.0, protect_active=True,
        speed_min_len=12.0, speed_gap_min=30.0, max_cuts_per_min=6.0,
        summary="Chỉ cắt khoảng lặng tĩnh từ 1s; giữ đoạn hình đang chuyển động; tua khi lặng dài từ 12s",
    ),
    "vlog": EditPolicy(
        key="vlog", label="Vlog / Quay cảnh", min_cut=2.0, protect_active=True,
        speed_min_len=10.0, speed_gap_min=45.0, max_cuts_per_min=3.0,
        summary="Chỉ cắt khoảng lặng tĩnh từ 2s, tối đa 3 nhát/phút; giữ mọi đoạn hình đang chuyển động; tua từ 10s",
    ),
}
VIDEO_TYPES = {"auto": "Tự nhận diện", **{k: p.label for k, p in POLICIES.items()}}


def classify_video(
    speech_ratio: float,
    activity: Optional[np.ndarray],
) -> Tuple[str, Dict[str, float]]:
    """
    Nhận diện loại video từ tỷ lệ thời lượng có lời thoại và mức chuyển động hình ảnh trung vị.
    - chuyển động trung vị >= 0.12: vlog (máy cầm tay / cảnh thay đổi);
    - ít chuyển động: talk (hình tĩnh, ngồi nói), nếu có lời hoặc gần như không chuyển động;
    - còn lại: mixed. Không đo được chuyển động: dựa vào lời thoại.
    """
    metrics = {"speech_ratio": round(float(speech_ratio), 3)}
    if activity is None or len(activity) == 0:
        return ("talk" if speech_ratio >= 0.5 else "mixed"), metrics
    motion = float(np.median(activity))
    metrics["motion_median"] = round(motion, 3)
    if motion >= 0.12:
        return "vlog", metrics
    if speech_ratio >= 0.35 or motion <= 0.06:
        return "talk", metrics
    return "mixed", metrics


def speed_for_length(length: float, max_speed: float) -> float:
    """Đoạn càng dài tua càng nhanh (lặng 10-20s: 3x; 20-45s: 5x; từ 45s: 8x), không vượt mức người dùng chọn."""
    base = 3.0 if length < 20 else (5.0 if length < 45 else 8.0)
    return float(min(base, max_speed)) if max_speed >= 2.0 else base


def apply_edit_policy(
    segments: Sequence[CutSegment],
    policy: EditPolicy,
    activity: Optional[np.ndarray] = None,
    bin_seconds: float = 1.0,
    prefer_speed: bool = False,
    max_speed: float = 8.0,
    protect_active: Optional[bool] = None,
    min_active_ratio: float = 0.5,
) -> Tuple[List[CutSegment], Dict[str, Any]]:
    """
    Quyết định lại số phận từng khoảng lặng (segment action cut/speedup) theo chính sách.
    prefer_speed=True: người dùng muốn "tua thay vì cắt": khoảng lặng ngắn được GIỮ, không cắt, chỉ đoạn dài mới tua.
    protect_active: ghi đè policy.protect_active (vd người dùng tắt 'Bảo vệ cảnh quay').
    """
    protect = policy.protect_active if protect_active is None else protect_active
    thr = default_active_threshold(activity) if activity is not None else None

    def is_active(seg: CutSegment) -> bool:
        if activity is None or thr is None or len(activity) == 0:
            return False
        b0 = int(seg.start // bin_seconds)
        window = activity[b0: max(b0 + 1, int(np.ceil(seg.end / bin_seconds)))]
        return len(window) > 0 and float(np.mean(window >= thr)) >= min_active_ratio

    decided: List[Tuple[CutSegment, str, float]] = []   # (segment gốc, quyết định, tốc độ)
    for seg in sorted(segments, key=lambda s: s.start):
        if seg.action == "keep":
            decided.append((seg, "keep", 1.0))
            continue
        length = seg.duration
        active = protect and is_active(seg)
        if length < policy.min_cut:
            decided.append((seg, "keep", 1.0))                 # nhịp thở tự nhiên
        elif length >= policy.speed_min_len and (prefer_speed or active):
            decided.append((seg, "speedup", speed_for_length(length, max_speed)))
        elif prefer_speed or active:
            decided.append((seg, "keep", 1.0))                 # người dùng không muốn cắt / cảnh đang diễn ra
        else:
            decided.append((seg, "cut", 1.0))                  # khoảng lặng tĩnh, đủ dài: cắt

    # Luật "không tua - bình thường - tua": trong mỗi cụm tua gần nhau chỉ giữ đoạn dài nhất
    demoted = 0
    idx = [i for i, (_, d, _) in enumerate(decided) if d == "speedup"]
    clusters: List[List[int]] = []
    for i in idx:
        if clusters:
            prev = clusters[-1][-1]
            between = sum(s.duration for s, d, _ in decided[prev + 1: i] if d != "cut")
            if between < policy.speed_gap_min:
                clusters[-1].append(i)
                continue
        clusters.append([i])
    for cl in clusters:
        if len(cl) < 2:
            continue
        best = max(cl, key=lambda k: decided[k][0].duration)
        for k in cl:
            if k != best:
                seg = decided[k][0]
                decided[k] = (seg, "keep", 1.0)
                demoted += 1

    # Trần mật độ nhát cắt: thừa thì chỉ giữ các nhát cắt dài nhất
    total = max((s.end for s in segments), default=0.0)
    allowed = max(1, int(np.ceil(policy.max_cuts_per_min * (total / 60.0)))) if total > 0 else 0
    cut_idx = [i for i, (_, d, _) in enumerate(decided) if d == "cut"]
    trimmed = 0
    if len(cut_idx) > allowed:
        for k in sorted(cut_idx, key=lambda k: decided[k][0].duration)[: len(cut_idx) - allowed]:
            decided[k] = (decided[k][0], "keep", 1.0)
            trimmed += 1

    out: List[CutSegment] = []
    for seg, action, speed in decided:
        new = seg.model_copy()
        new.action, new.speed = action, speed
        if out and out[-1].action == new.action == "keep" and out[-1].speed == new.speed == 1.0 \
                and out[-1].punch_in == new.punch_in and abs(out[-1].end - new.start) < 1e-3:
            out[-1].end = new.end
        else:
            out.append(new)

    orig_silent = [s for s in segments if s.action != "keep"]
    cuts = [s for s in out if s.action == "cut"]
    speeds = [s for s in out if s.action == "speedup"]
    report = {
        "policy": policy.key,
        "silences_found": len(orig_silent),
        "cut": len(cuts), "cut_seconds": round(sum(s.duration for s in cuts), 1),
        "speedup": len(speeds), "speedup_seconds": round(sum(s.duration for s in speeds), 1),
        "kept_as_is": len(orig_silent) - len(cuts) - len(speeds),
        "speed_demoted": demoted, "cut_trimmed": trimmed,
    }
    return out, report
