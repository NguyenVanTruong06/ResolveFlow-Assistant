"""
Scene Activity - đo mức chuyển động hình ảnh để cắt khoảng lặng "có mắt nhìn" trong vlog.

Vlog dài thường có những quãng không ai nói nhưng hình ảnh vẫn đang diễn ra (đi bộ, quay cảnh, ăn uống...).
Cắt thẳng các quãng này làm cảnh bị giật. Module này:
- compute_visual_activity: lấy mẫu keyframe của video ở độ phân giải cực nhỏ để ước lượng chuyển động theo giây
  (nhanh hơn giải mã toàn bộ khung hình rất nhiều).
- protect_active_scenes: với mỗi khoảng lặng định cắt, nếu hình ảnh đang chuyển động thì giữ nguyên hoặc tua nhanh
  thay vì cắt.
"""
import os
import re
import subprocess
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.core.autocut import CutSegment

FRAME_W, FRAME_H = 64, 36


def compute_visual_activity(
    video_path: str,
    duration: Optional[float] = None,
    bin_seconds: float = 1.0,
    timeout: int = 1200,
) -> Optional[np.ndarray]:
    """
    Trả về mảng mức chuyển động theo từng `bin_seconds` giây (0 = tĩnh, cao = chuyển động mạnh),
    hoặc None nếu không đo được (thiếu ffmpeg, video lỗi, quá thời gian).
    Chỉ giải mã keyframe (-skip_frame nokey) nên nhanh; độ phân giải thời gian bằng khoảng cách keyframe.
    """
    if not os.path.exists(video_path):
        return None
    raw_path = os.path.join(tempfile.gettempdir(), f"rf_activity_{uuid.uuid4().hex[:8]}.raw")
    base_cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-skip_frame", "nokey", "-i", video_path,
        "-an", "-sn", "-vf", f"scale={FRAME_W}:{FRAME_H}:flags=fast_bilinear,format=gray,showinfo",
    ]
    tail = ["-f", "rawvideo", "-y", raw_path]
    stderr = ""
    try:
        for sync_flag in (["-fps_mode", "passthrough"], ["-vsync", "0"]):
            res = subprocess.run(base_cmd + sync_flag + tail, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=timeout)
            if res.returncode == 0:
                stderr = res.stderr
                break
        else:
            return None
        times = [float(t) for t in re.findall(r"pts_time:\s*([0-9.]+)", stderr)]
        data = np.fromfile(raw_path, dtype=np.uint8)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    finally:
        try:
            os.remove(raw_path)
        except OSError:
            pass

    frame_size = FRAME_W * FRAME_H
    n = min(len(times), len(data) // frame_size)
    if n < 2:
        return None
    frames = data[: n * frame_size].reshape(n, frame_size).astype(np.float32)
    times_arr = np.array(times[:n])
    diffs = np.mean(np.abs(np.diff(frames, axis=0)), axis=1) / 255.0  # chuyển động giữa keyframe i -> i+1

    total = duration if duration and duration > 0 else float(times_arr[-1]) + 1.0
    bins = int(np.ceil(total / bin_seconds))
    activity = np.zeros(bins, dtype=np.float32)
    for i, d in enumerate(diffs):
        b0 = int(times_arr[i] // bin_seconds)
        b1 = int(times_arr[i + 1] // bin_seconds)
        activity[b0: min(bins, max(b1, b0 + 1))] = d
    if bins and times_arr[-1] < total:
        activity[int(times_arr[-1] // bin_seconds):] = diffs[-1]
    return activity


def default_active_threshold(activity: np.ndarray) -> float:
    """Ngưỡng 'đang chuyển động' thích nghi theo chính video: cao hơn mặt bằng chung nhưng không dưới mức sàn."""
    if activity is None or len(activity) == 0:
        return 0.05
    return float(max(0.03, np.percentile(activity, 35)))


def protect_active_scenes(
    segments: List[CutSegment],
    activity: Optional[np.ndarray],
    bin_seconds: float = 1.0,
    threshold: Optional[float] = None,
    min_active_ratio: float = 0.5,
    keep_max_seconds: float = 6.0,
    long_speed: float = 4.0,
) -> Tuple[List[CutSegment], Dict[str, Any]]:
    """
    Với mỗi khoảng lặng định CẮT: nếu >= min_active_ratio số giây trong đó có chuyển động hình ảnh thì
      - ngắn (<= keep_max_seconds): GIỮ nguyên (cảnh liền mạch, không giật);
      - dài: TUA NHANH long_speed lần (vẫn thấy cảnh diễn ra nhưng không lê thê).
    Khoảng lặng tĩnh (người ngồi im, màn hình đen...) vẫn bị cắt. Không đụng vào đoạn tua nhanh/giữ có sẵn.
    """
    report = {"kept": 0, "kept_seconds": 0.0, "sped_up": 0, "sped_up_seconds": 0.0, "cut": 0, "cut_seconds": 0.0}
    if activity is None or len(activity) == 0:
        return segments, report
    thr = threshold if threshold is not None else default_active_threshold(activity)

    out: List[CutSegment] = []
    for seg in segments:
        if seg.action != "cut" or seg.duration <= 0:
            out.append(seg)
            continue
        b0 = int(seg.start // bin_seconds)
        b1 = max(b0 + 1, int(np.ceil(seg.end / bin_seconds)))
        window = activity[b0:b1]
        ratio = float(np.mean(window >= thr)) if len(window) else 0.0
        if ratio < min_active_ratio:
            report["cut"] += 1
            report["cut_seconds"] += seg.duration
            out.append(seg)
        elif seg.duration <= keep_max_seconds:
            report["kept"] += 1
            report["kept_seconds"] += seg.duration
            out.append(CutSegment(start=seg.start, end=seg.end, action="keep", speed=1.0))
        else:
            report["sped_up"] += 1
            report["sped_up_seconds"] += seg.duration
            out.append(CutSegment(start=seg.start, end=seg.end, action="speedup", speed=long_speed))

    # Gộp các đoạn "keep" liền kề (đoạn thoại + cảnh được bảo vệ) thành một để không chia nhỏ vô ích
    merged: List[CutSegment] = []
    for seg in sorted(out, key=lambda s: s.start):
        prev = merged[-1] if merged else None
        if (prev and prev.action == seg.action == "keep" and prev.speed == seg.speed == 1.0
                and prev.punch_in == seg.punch_in and abs(prev.end - seg.start) < 1e-3):
            prev.end = seg.end
        else:
            merged.append(seg.model_copy())  # bản sao: không sửa vào danh sách đầu vào của người gọi
    for k in ("kept_seconds", "sped_up_seconds", "cut_seconds"):
        report[k] = round(report[k], 1)
    return merged, report
