"""
Unit test cho EvidenceKeywordDetector và WebScreenshotEngine (src/core/evidence_grabber.py).
"""

import os
import pytest
from src.core.evidence_grabber import (
    EvidenceTarget,
    EvidenceKeywordDetector,
    WebScreenshotEngine,
)


def test_evidence_detector_detects_urls():
    detector = EvidenceKeywordDetector()
    text = "Các bạn có thể xem repo tại https://github.com/YeJe-cpu/SeeCut hoặc trang chủ openai.com nhé."
    targets = detector.detect_targets(text, start_time=1.0, end_time=4.5)
    
    assert len(targets) >= 2
    urls = [t.target_url for t in targets]
    assert "https://github.com/YeJe-cpu/SeeCut" in urls
    assert any("openai.com" in u for u in urls)
    
    # Kiểm tra thời gian neo
    for t in targets:
        assert t.start_time == 1.0
        assert t.end_time == 4.5


def test_evidence_detector_detects_tech_entities():
    detector = EvidenceKeywordDetector()
    text = "Chúng ta sẽ dùng DaVinci Resolve và ChatGPT để viết kịch bản."
    targets = detector.detect_targets(text, start_time=10.0, end_time=15.0)
    
    keywords = [t.keyword.lower() for t in targets]
    assert any("davinci resolve" in k for k in keywords)
    assert any("chatgpt" in k for k in keywords)


def test_web_screenshot_engine_mock_capture(tmp_path):
    out_dir = str(tmp_path / "evidence")
    engine = WebScreenshotEngine(output_dir=out_dir)
    
    # Chạy chụp màn hình ở chế độ mock offline hoặc direct
    out_path = engine.capture_screenshot("https://github.com", mock=True)
    assert os.path.exists(out_path)
    assert out_path.endswith(".png")
    assert os.path.getsize(out_path) > 0


def test_web_screenshot_engine_caching(tmp_path):
    out_dir = str(tmp_path / "evidence")
    engine = WebScreenshotEngine(output_dir=out_dir)
    
    # Chụp lần đầu
    p1 = engine.capture_screenshot("https://example.com/test", mock=True)
    # Chụp lần hai cùng URL -> phải trả về file cache
    p2 = engine.capture_screenshot("https://example.com/test", mock=True)
    
    assert p1 == p2
