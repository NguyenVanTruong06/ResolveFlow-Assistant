import shutil
import subprocess
import numpy as np
import pytest
from src.core.autocut import CutSegment
from src.core import scene_activity as sa


def _segs():
    return [
        CutSegment(start=0, end=10, action="keep"),
        CutSegment(start=10, end=13, action="cut"),    # ngắn, có chuyển động -> giữ
        CutSegment(start=13, end=20, action="keep"),
        CutSegment(start=20, end=40, action="cut"),    # dài, có chuyển động -> tua nhanh
        CutSegment(start=40, end=50, action="keep"),
        CutSegment(start=50, end=53, action="cut"),    # tĩnh -> vẫn cắt
        CutSegment(start=53, end=60, action="keep"),
    ]


def _activity():
    a = np.zeros(60, dtype=np.float32)
    a[10:40] = 0.3
    return a


def test_protect_active_scenes_keeps_speeds_and_cuts():
    segs = _segs()
    out, rep = sa.protect_active_scenes(segs, _activity(), threshold=0.1)
    assert rep["kept"] == 1 and rep["sped_up"] == 1 and rep["cut"] == 1
    actions = [(s.start, s.end, s.action, s.speed) for s in out]
    assert (0, 20, "keep", 1.0) in actions                # keep + cảnh được giữ + keep gộp thành 1
    assert (20, 40, "speedup", 4.0) in actions
    assert (50, 53, "cut", 1.0) in actions
    assert [s.action for s in segs].count("cut") == 3     # đầu vào không bị sửa


def test_protect_without_activity_is_noop():
    segs = _segs()
    out, rep = sa.protect_active_scenes(segs, None)
    assert out == segs and rep["kept"] == 0


def test_default_threshold_has_floor():
    assert sa.default_active_threshold(np.zeros(100)) == 0.03


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")
def test_compute_visual_activity_moving_vs_static(tmp_path):
    def make(src, name):
        out = tmp_path / name
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-t", "8",
                        "-g", "10", "-pix_fmt", "yuv420p", str(out)], check=True)
        return str(out)
    moving = sa.compute_visual_activity(make("testsrc=size=320x240:rate=10", "moving.mp4"), duration=8.0)
    static = sa.compute_visual_activity(make("color=c=gray:size=320x240:rate=10", "static.mp4"), duration=8.0)
    assert moving is not None and static is not None
    assert float(np.mean(moving)) > 0.02 and float(np.mean(static)) < 0.005


def test_compute_visual_activity_missing_file():
    assert sa.compute_visual_activity("khong_ton_tai.mp4") is None
