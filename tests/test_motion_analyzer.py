"""
Unit tests for SmartMotionAnalyzer (Optical Flow & Bilingual Tag Search).
"""
import os
import tempfile
import pytest
import numpy as np

from src.core.motion_analyzer import (
    SmartMotionAnalyzer,
    strip_accents,
    matches_query,
    BILINGUAL_TAG_MAP
)


def test_strip_accents():
    assert strip_accents("phóng to") == "phong to"
    assert strip_accents("thu nhỏ") == "thu nho"
    assert strip_accents("trượt trái") == "truot trai"
    assert strip_accents("rung giật") == "rung giat"
    assert strip_accents("Đứng Yên") == "dung yen"


def test_matches_query():
    tags = ["shake", "rung giật", "glitch", "camera shake"]
    
    # Matching with accents
    assert matches_query("rung giật", tags) is True
    # Matching without accents
    assert matches_query("rung giat", tags) is True
    assert matches_query("giat", tags) is True
    assert matches_query("shake", tags) is True
    # Unmatched
    assert matches_query("phong to", tags) is False


def test_inspect_setting_file():
    with tempfile.NamedTemporaryFile("w", suffix=".setting", delete=False) as f:
        f.write('''
            {
                Tools = ordered() {
                    MyText = TextPlus {
                        Inputs = {
                            StyledText = Input { Value = "HELLO RESOLVE", },
                        },
                    },
                    ShakeEffect = Shake {
                        Inputs = {
                            Smoothness = Input { Value = 10, },
                        },
                    },
                    AnimSpline = BezierSpline {
                        SplineColor = { Red = 255, Green = 0, Blue = 0, },
                    }
                }
            }
        ''')
        temp_path = f.name

    try:
        tags = SmartMotionAnalyzer.inspect_setting_file(temp_path)
        # Should detect TextPlus -> text, chữ, tiêu đề...
        assert any("chu" in t or "chữ" in t or "text" in t for t in tags)
        # Should detect Shake -> rung, giật...
        assert any("rung" in t or "shake" in t for t in tags)
        # Should detect BezierSpline -> animated, chuyển động...
        assert any("animated" in t or "chuyen dong" in t or "chuyển động" in t for t in tags)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_classify_motion_static():
    # 3 identical white frames -> static
    frame = np.ones((108, 192, 3), dtype=np.uint8) * 200
    frames = [frame.copy(), frame.copy(), frame.copy()]

    result = SmartMotionAnalyzer.classify_motion(frames)
    assert result["motion"] == "static"
    assert "static" in result["tags"]
    assert any("tinh" in t or "tĩnh" in t for t in result["tags"])


def test_classify_motion_slide_right():
    # Frames where a square translates horizontally to the right
    frames = []
    for step in range(5):
        f = np.zeros((108, 192, 3), dtype=np.uint8)
        x_start = 20 + step * 10
        f[30:70, x_start:x_start+40] = 255
        frames.append(f)

    result = SmartMotionAnalyzer.classify_motion(frames)
    assert result["motion"] in ("slide_right", "animated")
    if result["motion"] == "slide_right":
        assert "right" in result["directions"]
        assert any("phai" in t or "phải" in t for t in result["tags"])


def test_classify_motion_shake():
    # Frames alternating back and forth rapidly
    frames = []
    for step in range(6):
        f = np.zeros((108, 192, 3), dtype=np.uint8)
        x_start = 50 + (15 if step % 2 == 0 else -15)
        f[20:80, x_start:x_start+40] = 255
        frames.append(f)

    result = SmartMotionAnalyzer.classify_motion(frames)
    assert result["motion"] in ("shake", "animated")
    assert result["motion_energy"] > 0
