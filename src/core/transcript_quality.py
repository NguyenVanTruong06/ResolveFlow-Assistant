"""
Kiểm soát chất lượng bản nhận dạng giọng nói (Whisper) cho video vlog dài, nhiều tạp âm.

- split_stretched_segments: tách câu bị Whisper kéo giãn (vd 4 từ trải trên 41 giây) tại các khe hở giữa các từ.
- find_uncovered_gaps: tìm khoảng dài có âm thanh lớn nhưng Whisper không ra chữ nào (bị bỏ sót).
- coverage_stats / merge_segments: thống kê độ phủ và gộp kết quả quét bổ sung.
"""
import os
import wave
from typing import Any, Dict, List, Tuple

import numpy as np

Segment = Dict[str, Any]


def _join_words(words: List[Dict[str, Any]]) -> str:
    return "".join(w.get("word", "") for w in words).strip()


def split_stretched_segments(
    segments: List[Segment],
    max_word_gap: float = 2.5,
    max_word_seconds: float = 4.0,
) -> List[Segment]:
    """
    Tách một câu thành nhiều câu khi giữa hai từ liên tiếp có khe hở > max_word_gap giây,
    và co các từ bị kéo giãn bất thường (> max_word_seconds) về 1 giây.
    Câu không có dữ liệu từ (words) được giữ nguyên.
    """
    out: List[Segment] = []
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            out.append(seg)
            continue

        fixed = []
        for w in words:
            w = dict(w)
            if w["end"] - w["start"] > max_word_seconds:
                w["end"] = w["start"] + 1.0
            fixed.append(w)

        groups: List[List[Dict[str, Any]]] = [[fixed[0]]]
        for prev, cur in zip(fixed, fixed[1:]):
            if cur["start"] - prev["end"] > max_word_gap:
                groups.append([cur])
            else:
                groups[-1].append(cur)

        if len(groups) == 1 and fixed[-1]["end"] - fixed[0]["start"] <= (seg["end"] - seg["start"]) + 1e-6:
            piece = dict(seg)
            piece["words"] = fixed
            piece["start"] = fixed[0]["start"]
            piece["end"] = fixed[-1]["end"]
            out.append(piece)
            continue

        for g in groups:
            piece = {k: v for k, v in seg.items() if k not in ("words", "text", "start", "end")}
            piece.update(start=g[0]["start"], end=g[-1]["end"], text=_join_words(g), words=g)
            out.append(piece)
    return out


def _window_db(audio: np.ndarray, sample_rate: int, window: float = 0.05) -> np.ndarray:
    size = int(window * sample_rate)
    n = len(audio) // size
    if n == 0:
        return np.array([-100.0])
    rms = np.sqrt(np.mean(audio[: n * size].reshape(n, size) ** 2, axis=1)) + 1e-10
    return 20 * np.log10(rms)


def read_wav_mono(wav_path: str) -> Tuple[np.ndarray, int]:
    """Đọc WAV PCM 16-bit mono (định dạng app tạo ra) thành float32 [-1, 1]."""
    with wave.open(os.path.normpath(wav_path), "rb") as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return data, sr


def find_uncovered_gaps(
    segments: List[Segment],
    audio: np.ndarray,
    sample_rate: int,
    min_gap: float = 20.0,
    loud_db: float = -35.0,
    min_loud_ratio: float = 0.35,
) -> List[Tuple[float, float]]:
    """
    Các khoảng >= min_gap giây không có chữ nào nhưng >= min_loud_ratio cửa sổ âm thanh lớn hơn loud_db:
    nhiều khả năng có tiếng nói/ồn mà Whisper bỏ sót. Khoảng im lặng thật (nhỏ) không bị báo.
    """
    total = len(audio) / sample_rate
    ordered = sorted(segments, key=lambda s: s["start"])
    edges = [0.0] + [t for s in ordered for t in (s["start"], s["end"])] + [total]
    block = 5.0
    gaps: List[Tuple[float, float]] = []
    for a, b in zip(edges[0::2], edges[1::2]):
        if b - a < min_gap:
            continue
        # Chia khoảng trống thành khối 5s, chỉ gom các khối liên tiếp có âm thanh lớn
        run_start = None
        t = a
        while t < b - 1e-6:
            t_end = min(b, t + block)
            db = _window_db(audio[int(t * sample_rate): int(t_end * sample_rate)], sample_rate)
            loud = float(np.mean(db > loud_db)) >= min_loud_ratio
            if loud and run_start is None:
                run_start = t
            if not loud and run_start is not None:
                if t - run_start >= min_gap:
                    gaps.append((round(run_start, 2), round(t, 2)))
                run_start = None
            t = t_end
        if run_start is not None and b - run_start >= min_gap:
            gaps.append((round(run_start, 2), round(b, 2)))
    return gaps


def coverage_stats(segments: List[Segment], duration: float) -> Dict[str, float]:
    """Tỷ lệ thời lượng có chữ và khoảng trống dài nhất."""
    ordered = sorted(segments, key=lambda s: s["start"])
    covered = sum(max(0.0, s["end"] - s["start"]) for s in ordered)
    edges = [0.0] + [t for s in ordered for t in (s["start"], s["end"])] + [duration]
    longest = max((b - a for a, b in zip(edges[0::2], edges[1::2])), default=0.0)
    return {
        "covered_seconds": round(covered, 1),
        "coverage_percent": round(covered / duration * 100, 1) if duration > 0 else 0.0,
        "longest_gap_seconds": round(longest, 1),
    }


def merge_segments(base: List[Segment], extra: List[Segment]) -> List[Segment]:
    """Gộp kết quả quét bổ sung; bỏ câu bổ sung nếu trùng lên câu đã có quá nửa thời lượng."""
    merged = list(base)
    for e in extra:
        dur = max(1e-6, e["end"] - e["start"])
        overlap = sum(
            max(0.0, min(e["end"], b["end"]) - max(e["start"], b["start"])) for b in base
        )
        if overlap / dur < 0.5:
            merged.append(e)
    return sorted(merged, key=lambda s: s["start"])
