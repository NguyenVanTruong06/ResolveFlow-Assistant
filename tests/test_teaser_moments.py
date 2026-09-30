import numpy as np
from src.core.vlog_hook import VlogHookGenerator as V, HookSegment


def _synthetic(total=300):
    energy = np.full(total, -40.0)
    energy[50:53] = -15.0                      # tiếng động lớn ở giây 50
    activity = np.full(total, 0.02)
    activity[100:106] = 0.4                    # cảnh hành động ở giây 100
    subs = [{"start": 150.0, "end": 153.0, "text": "Wow nhìn này, không thể tin được!"}]
    return total, energy, activity, subs


def test_candidates_cover_audio_motion_and_speech_signals():
    total, energy, activity, subs = _synthetic()
    pool = V.find_moment_candidates("clip.mp4", total, subs, energy, activity, moment_len=2.5)
    starts = [p.src_in for p in pool]
    assert any(48 <= s <= 53 for s in starts)        # cao trào âm thanh
    assert any(97 <= s <= 106 for s in starts)       # chuyển động mạnh
    assert any(148 <= s <= 153 for s in starts)      # câu hook thoại
    hook = next(p for p in pool if 148 <= p.src_in <= 153)
    assert hook.reason.startswith("Hook thoại") and "Wow" in hook.text
    assert all(a.src_out <= b.src_in or b.src_out <= a.src_in for a in pool for b in pool if a is not b)


def test_dead_and_edge_windows_are_ignored_or_penalised():
    total = 200
    energy = np.full(total, -40.0)
    activity = np.full(total, 0.02)
    assert V.find_moment_candidates("c.mp4", total, [], energy, activity) == []   # im lặng + tĩnh = không có gì đáng chọn


def test_select_teaser_hook_first_chronological_rest_and_budget():
    mk = lambda s, sc: HookSegment(video_path="a.mp4", src_in=s, src_out=s + 2.5, duration=2.5, score=sc, reason="r")
    pool = [mk(10, 5), mk(400, 9), mk(800, 7), mk(805, 8), mk(1200, 6)]
    out = V.select_teaser(pool, target_total=10.0, min_gap=8.0)
    assert out[0].src_in == 400                      # mạnh nhất mở đầu
    assert [o.src_in for o in out[1:]] == sorted(o.src_in for o in out[1:])
    assert sum(o.duration for o in out) <= 10.0
    assert not (any(o.src_in == 800 for o in out) and any(o.src_in == 805 for o in out))   # không chọn 2 mốc sát nhau


def test_select_teaser_multi_source_respects_video_order():
    mk = lambda v, s, sc: HookSegment(video_path=v, src_in=s, src_out=s + 2, duration=2, score=sc, reason="r")
    pool = [mk("b.mp4", 5, 9), mk("a.mp4", 5, 5), mk("b.mp4", 500, 4)]
    out = V.select_teaser(pool, target_total=20.0, video_order=["a.mp4", "b.mp4"])
    assert out[0].video_path == "b.mp4" and out[1].video_path == "a.mp4"


def test_select_teaser_empty_and_fallback():
    assert V.select_teaser([], 20.0) == []
    big = HookSegment(video_path="a", src_in=0, src_out=30, duration=30, score=5, reason="r")
    assert V.select_teaser([big], target_total=10.0) == [big]     # không có gì vừa ngân sách -> vẫn trả mốc tốt nhất
