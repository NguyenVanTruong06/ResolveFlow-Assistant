import numpy as np
from src.core import transcript_quality as tq


def _w(word, s, e):
    return {"word": word, "start": s, "end": e, "probability": 0.9}


def test_split_stretched_segment_real_case():
    # Ca thật từ video vlog: 4 từ trải trên 41 giây vì hai cụm cách nhau 40 giây
    seg = {"start": 615.0, "end": 656.0, "text": "Có vãi dạm vãi", "words": [
        _w(" Có", 615.0, 615.2), _w(" vãi", 615.3, 618.0), _w(" dạm", 655.0, 655.4), _w(" vãi", 655.5, 656.0)]}
    out = tq.split_stretched_segments([seg])
    assert [(round(s["start"]), round(s["end"]), s["text"]) for s in out] == [
        (615, 618, "Có vãi"), (655, 656, "dạm vãi")]


def test_split_keeps_normal_segments_and_clamps_long_words():
    normal = {"start": 0.0, "end": 2.0, "text": "xin chào", "words": [_w(" xin", 0, 1), _w(" chào", 1, 2)]}
    no_words = {"start": 3.0, "end": 5.0, "text": "abc"}
    drift = {"start": 10.0, "end": 30.0, "text": "à", "words": [_w(" à", 10.0, 30.0)]}
    out = tq.split_stretched_segments([normal, no_words, drift])
    assert out[0]["text"] == "xin chào" and out[1] is no_words
    assert out[2]["end"] - out[2]["start"] == 1.0


def test_find_uncovered_gaps_only_reports_loud_gaps():
    sr = 16000
    loud = (0.3 * np.sin(np.linspace(0, 2000, 100 * sr))).astype(np.float32)   # 100s ồn lớn
    quiet = np.zeros(100 * sr, dtype=np.float32)                                # 100s im lặng
    audio = np.concatenate([loud, quiet])
    segs = [{"start": 1.0, "end": 3.0, "text": "a"}]  # chỉ có chữ ở đầu
    gaps = tq.find_uncovered_gaps(segs, audio, sr, min_gap=20.0)
    # Chỉ báo phần ồn (3s -> ~100s); đoạn im lặng thật ở cuối không bị báo
    assert len(gaps) == 1 and gaps[0][0] == 3.0 and 95 <= gaps[0][1] <= 105


def test_coverage_and_merge():
    base = [{"start": 0.0, "end": 10.0, "text": "a"}, {"start": 50.0, "end": 60.0, "text": "b"}]
    st = tq.coverage_stats(base, 100.0)
    assert st["coverage_percent"] == 20.0 and st["longest_gap_seconds"] == 40.0
    extra = [{"start": 5.0, "end": 9.0, "text": "trùng"}, {"start": 20.0, "end": 25.0, "text": "mới"}]
    merged = tq.merge_segments(base, extra)
    assert [m["text"] for m in merged] == ["a", "mới", "b"]
