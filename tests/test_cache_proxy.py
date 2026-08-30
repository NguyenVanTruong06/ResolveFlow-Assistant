import os
import pytest
from src.core.cache_manager import compute_file_checksum, ScanCacheManager
from src.core.proxy_manager import suggest_whisper_model, ParallelScanPipeline

def test_compute_file_checksum(tmp_path):
    f1 = tmp_path / "video1.mp4"
    f1.write_bytes(b"dummy_video_bytes_123456789")
    
    hash1 = compute_file_checksum(str(f1))
    assert len(hash1) > 0
    
    # Check that identical file returns same hash
    hash2 = compute_file_checksum(str(f1))
    assert hash1 == hash2

def test_scan_cache_hit_and_miss(tmp_path):
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    dummy_video = str(tmp_path / "test.mp4")
    with open(dummy_video, "wb") as f:
        f.write(b"mock video data")

    # Cache miss
    assert cache_mgr.get_cached_scan(dummy_video, "small", "Auto") is None

    # Save scan data
    mock_data = {
        "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "test", "words": []}],
        "clip_dur": 2.0,
        "silence_keep_intervals": [(0.0, 2.0)]
    }
    cache_mgr.save_scan_result(dummy_video, mock_data, "small", "Auto")

    # Cache hit
    cached = cache_mgr.get_cached_scan(dummy_video, "small", "Auto")
    assert cached is not None
    assert cached["clip_dur"] == 2.0
    assert len(cached["raw_subtitles"]) == 1

def test_suggest_whisper_model():
    # Video ngắn < 3 min (100s) -> large-v3
    s_short = suggest_whisper_model(100.0)
    assert s_short["suggested_model"] == "large-v3"
    assert s_short["is_warning"] is False

    # Video trung bình 10 min (600s) -> small
    s_med = suggest_whisper_model(600.0)
    assert s_med["suggested_model"] == "small"
    assert s_med["is_warning"] is False

    # Video dài > 30 min (2000s) -> small with warning
    s_long = suggest_whisper_model(2000.0)
    assert s_long["suggested_model"] == "small"
    assert s_long["is_warning"] is True

def test_parallel_scan_pipeline():
    def task_a():
        return "audio_done"

    def task_b():
        return "video_proxy_done"

    res_a, res_b = ParallelScanPipeline.execute_parallel_scan(task_a, task_b)
    assert res_a == "audio_done"
    assert res_b == "video_proxy_done"
