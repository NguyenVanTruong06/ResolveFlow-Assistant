import os
import tempfile
import numpy as np
import pytest
from PIL import Image
from unittest.mock import patch, MagicMock
from src.core.thumbnail_generator import (
    ThumbnailConfig,
    ThumbnailCandidate,
    ThumbnailExtractor
)


def test_thumbnail_config_defaults_and_validation():
    cfg = ThumbnailConfig()
    assert cfg.top_n == 3
    assert cfg.sample_interval_sec == 2.0
    assert cfg.min_time_distance_sec == 3.0
    assert cfg.image_format == "PNG"
    assert cfg.min_brightness == 35.0
    assert cfg.max_brightness == 225.0

    # Test custom values
    custom_cfg = ThumbnailConfig(top_n=5, sample_interval_sec=1.0, min_time_distance_sec=5.0)
    assert custom_cfg.top_n == 5
    assert custom_cfg.sample_interval_sec == 1.0


def test_thumbnail_candidate_model():
    cand = ThumbnailCandidate(
        index=1,
        timestamp_sec=12.5,
        timecode="00:00:12:15",
        overall_score=95.5,
        sharpness_score=120.0,
        contrast_score=45.0,
        brightness=110.0,
        image_path="/path/to/thumb_01.png",
        width=1920,
        height=1080
    )
    assert cand.index == 1
    assert cand.timestamp_sec == 12.5
    assert cand.timecode == "00:00:12:15"
    assert cand.overall_score == 95.5
    assert cand.width == 1920


def test_compute_laplacian_variance_sharp_vs_flat():
    # 1. Flat image (all uniform gray) -> variance must be 0
    flat_arr = np.full((100, 100), 128, dtype=np.uint8)
    flat_var = ThumbnailExtractor.compute_laplacian_variance(flat_arr)
    assert flat_var == 0.0

    # 2. Invalid shape (< 3x3)
    tiny_arr = np.full((2, 2), 128, dtype=np.uint8)
    assert ThumbnailExtractor.compute_laplacian_variance(tiny_arr) == 0.0

    # 3. High contrast pattern (checkerboard) -> high variance
    checker = np.zeros((100, 100), dtype=np.uint8)
    checker[::2, ::2] = 255
    checker[1::2, 1::2] = 255
    sharp_var = ThumbnailExtractor.compute_laplacian_variance(checker)
    assert sharp_var > 1000.0


def test_analyze_image_quality_lighting_penalties():
    cfg = ThumbnailConfig(min_brightness=35.0, max_brightness=225.0)

    # 1. Normal image with good contrast
    normal_img = Image.new("RGB", (100, 100), color=(128, 128, 128))
    q_normal = ThumbnailExtractor.analyze_image_quality(normal_img, cfg)
    assert q_normal["brightness"] == 128.0
    assert q_normal["sharpness"] == 0.0

    # 2. Completely dark image (black) -> should suffer penalty
    dark_img = Image.new("RGB", (100, 100), color=(5, 5, 5))
    q_dark = ThumbnailExtractor.analyze_image_quality(dark_img, cfg)
    assert q_dark["brightness"] == 5.0

    # 3. Blown out image (white) -> should suffer penalty
    bright_img = Image.new("RGB", (100, 100), color=(250, 250, 250))
    q_bright = ThumbnailExtractor.analyze_image_quality(bright_img, cfg)
    assert q_bright["brightness"] == 250.0


def test_extract_single_frame_nonexistent():
    res = ThumbnailExtractor.extract_single_frame("non_existent_video_path.mp4", 1.0, "out.png")
    assert res is False


@patch("subprocess.run")
@patch("os.path.exists")
@patch("os.path.getsize")
def test_extract_single_frame_success(mock_getsize, mock_exists, mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    mock_exists.return_value = True
    mock_getsize.return_value = 1024

    with tempfile.TemporaryDirectory() as td:
        out_img = os.path.join(td, "thumb.png")
        success = ThumbnailExtractor.extract_single_frame("dummy.mp4", 5.0, out_img)
        assert success is True


@patch("src.core.thumbnail_generator.get_media_metadata")
@patch.object(ThumbnailExtractor, "extract_single_frame")
def test_generate_thumbnails_pipeline(mock_extract, mock_meta):
    mock_meta.return_value = {
        "duration": 20.0,
        "fps": 30.0,
        "width": 1920,
        "height": 1080
    }

    # Giả lập extract_single_frame tạo ảnh test thực tế vào output path
    def fake_extract(video_path, timestamp_sec, output_image_path):
        os.makedirs(os.path.dirname(output_image_path), exist_ok=True)
        # Tạo ảnh giả lập với độ sáng và họa tiết khác nhau theo timestamp
        arr = np.zeros((50, 50), dtype=np.uint8)
        arr[::2, :] = int((timestamp_sec * 15) % 180 + 40)
        arr[:, ::2] = 200
        img = Image.fromarray(arr)
        img.save(output_image_path)
        return True

    mock_extract.side_effect = fake_extract

    with tempfile.TemporaryDirectory() as td:
        cfg = ThumbnailConfig(top_n=3, sample_interval_sec=2.0, min_time_distance_sec=3.0)
        candidates = ThumbnailExtractor.generate_thumbnails(
            video_path="dummy_video.mp4",
            output_dir=td,
            config=cfg
        )

        assert len(candidates) <= 3
        assert len(candidates) > 0
        for cand in candidates:
            assert os.path.exists(cand.image_path)
            assert cand.overall_score > 0
            assert cand.timecode != ""
