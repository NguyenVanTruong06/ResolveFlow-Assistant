import numpy as np
from src.core.autocut import CutSegment
from src.core import edit_policy as ep


def K(a, b):
    return CutSegment(start=a, end=b, action="keep")


def C(a, b):
    return CutSegment(start=a, end=b, action="cut")


def S(a, b, sp=8.0):
    return CutSegment(start=a, end=b, action="speedup", speed=sp)


def test_classify_video_types():
    assert ep.classify_video(0.3, np.full(100, 0.25))[0] == "vlog"
    assert ep.classify_video(0.8, np.full(100, 0.03))[0] == "talk"
    assert ep.classify_video(0.05, np.full(100, 0.03))[0] == "talk"      # tĩnh hoàn toàn: không khí chết, cắt được
    assert ep.classify_video(0.15, np.full(100, 0.09))[0] == "mixed"
    assert ep.classify_video(0.7, None)[0] == "talk" and ep.classify_video(0.1, None)[0] == "mixed"


def test_short_pauses_are_kept_as_natural_breath():
    segs = [K(0, 10), C(10, 10.25), K(10.25, 20), C(20, 20.3), K(20.3, 30)]
    out, rep = ep.apply_edit_policy(segs, ep.POLICIES["talk"])
    assert rep["cut"] == 0 and rep["kept_as_is"] == 2 and len(out) == 1        # 2 nhịp thở được giữ và gộp liền


def test_talk_cuts_static_pauses_but_vlog_keeps_them_unless_long():
    segs = [K(0, 10), C(10, 11.0), K(11, 20), C(20, 24.0), K(24, 40)]
    _, talk = ep.apply_edit_policy(segs, ep.POLICIES["talk"])
    _, vlog = ep.apply_edit_policy(segs, ep.POLICIES["vlog"])
    assert talk["cut"] == 2
    assert vlog["cut"] == 1 and vlog["kept_as_is"] == 1                          # chỉ nhát >= 2s mới cắt


def test_active_scene_is_kept_and_only_very_long_ones_sped_with_length_based_speed():
    act = np.full(400, 0.3, dtype=np.float32)
    segs = [K(0, 10), C(10, 18), K(18, 100), C(100, 130), K(130, 250), C(250, 330), K(330, 400)]
    out, rep = ep.apply_edit_policy(segs, ep.POLICIES["vlog"], act, max_speed=8.0)
    sp = {round(s.duration): s.speed for s in out if s.action == "speedup"}
    assert rep["cut"] == 0
    assert 8 not in sp                        # 8s < 10s: giữ nguyên, không tua nhỏ
    assert sp == {30: 5.0, 80: 8.0}           # 20-45s: 5x, từ 45s: 8x
    out2, _ = ep.apply_edit_policy(segs, ep.POLICIES["vlog"], act, max_speed=4.0)
    assert max(s.speed for s in out2 if s.action == "speedup") == 4.0           # không vượt mức người dùng chọn


def test_no_fast_normal_fast_flip_flop():
    # 2 đoạn tua cách nhau chỉ 20s thoại bình thường: chỉ giữ đoạn dài hơn
    act = np.full(400, 0.3, dtype=np.float32)
    segs = [K(0, 10), C(10, 25), K(25, 45), C(45, 85), K(85, 100)]
    out, rep = ep.apply_edit_policy(segs, ep.POLICIES["vlog"], act)
    sp = [s for s in out if s.action == "speedup"]
    assert len(sp) == 1 and sp[0].duration == 40 and rep["speed_demoted"] == 1
    # cách nhau đủ xa (>= speed_gap_min) thì cả hai đều được tua
    segs2 = [K(0, 10), C(10, 25), K(25, 120), C(120, 160), K(160, 200)]
    out2, rep2 = ep.apply_edit_policy(segs2, ep.POLICIES["vlog"], act)
    assert sum(1 for s in out2 if s.action == "speedup") == 2 and rep2["speed_demoted"] == 0


def test_prefer_speed_never_cuts_and_skips_short_silences():
    segs = [K(0, 10), S(10, 11.0), K(11, 30), S(30, 50), K(50, 80)]
    out, rep = ep.apply_edit_policy(segs, ep.POLICIES["talk"], prefer_speed=True)
    assert rep["cut"] == 0 and rep["speedup"] == 1
    assert [s.duration for s in out if s.action == "speedup"] == [20.0]


def test_cut_density_cap_keeps_only_longest_cuts():
    segs, t = [], 0.0
    for i in range(20):                               # 20 nhát lặng tĩnh dài 2..3.9s trong ~5 phút
        segs += [K(t, t + 10), C(t + 10, t + 12 + i * 0.1)]
        t += 12 + i * 0.1
    segs.append(K(t, t + 10))
    out, rep = ep.apply_edit_policy(segs, ep.POLICIES["vlog"])
    allowed = int(np.ceil(3.0 * (segs[-1].end / 60)))
    assert rep["cut"] == allowed and rep["cut_trimmed"] == 20 - allowed
    kept_cut_lengths = sorted(s.duration for s in out if s.action == "cut")
    assert kept_cut_lengths[0] >= 2.0 + 0.1 * (20 - allowed) - 1e-6           # giữ lại các nhát dài nhất


def test_protect_override_and_no_input_mutation():
    act = np.full(100, 0.3, dtype=np.float32)
    segs = [K(0, 10), C(10, 15), K(15, 30)]
    _, guarded = ep.apply_edit_policy(segs, ep.POLICIES["vlog"], act)
    _, unguarded = ep.apply_edit_policy(segs, ep.POLICIES["vlog"], act, protect_active=False)
    assert guarded["cut"] == 0 and unguarded["cut"] == 1
    assert [s.action for s in segs] == ["keep", "cut", "keep"]
