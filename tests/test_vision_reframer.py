import pytest
from src.core.vision_reframer import VisionReframer, ReframeConfig

def test_reframe_config_defaults():
    config = ReframeConfig()
    assert config.target_aspect_ratio == "9:16"
    assert config.default_face_center_x == 0.5

def test_calculate_crop_box_9_16():
    reframer = VisionReframer(ReframeConfig(target_aspect_ratio="9:16"))
    # Với video 1920x1080, khung hình 9:16 sẽ có width = 1080 * 9 / 16 = 607, height = 1080
    crop = reframer.calculate_crop_box_for_aspect_ratio(1920, 1080, center_x_ratio=0.5)
    assert crop["height"] == 1080
    assert crop["width"] == 607
    # Tâm điểm ở 0.5 (960px), x_left = 960 - 303 = 657
    assert 650 <= crop["x"] <= 665
    assert crop["y"] == 0

def test_generate_reframe_timeline_events():
    reframer = VisionReframer(ReframeConfig(target_aspect_ratio="9:16"))
    intervals = [(0.0, 3.0), (3.0, 6.0), (6.0, 10.0)]
    events = reframer.generate_reframe_timeline_events(intervals, 1920, 1080)

    assert len(events) == 3
    assert events[0]["aspect_ratio"] == "9:16"
    assert "crop_box" in events[0]
    assert "pan_offset_x" in events[0]
