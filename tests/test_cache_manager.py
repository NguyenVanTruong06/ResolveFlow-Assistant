"""
Unit tests for ScanCacheManager (Schema V2)
"""

import os
import json
import pytest
from src.core.cache_manager import (
    ScanCacheManager,
    CACHE_SCHEMA_VERSION,
    normalize_language_code,
    compute_file_checksum
)


def test_normalize_language_code():
    assert normalize_language_code("vi") == "vi"
    assert normalize_language_code("Tiếng Việt") == "vi"
    assert normalize_language_code("Vietnamese") == "vi"
    assert normalize_language_code("en") == "en"
    assert normalize_language_code("English") == "en"
    assert normalize_language_code("auto") == "auto"
    assert normalize_language_code("Auto") == "auto"
    assert normalize_language_code("Tự nhận diện") == "auto"
    assert normalize_language_code(None) == "auto"


def test_save_scan_result_schema_v2(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    dummy_video = str(tmp_path / "clip_01.mp4")
    with open(dummy_video, "wb") as f:
        f.write(b"0" * 1024)

    scan_data = {
        "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Chào bạn"}],
        "clip_dur": 10.0,
        "silence_keep_intervals": [(0.0, 10.0)]
    }

    cache_file = mgr.save_scan_result(
        file_path=dummy_video,
        scan_data=scan_data,
        model_size="large-v3-turbo",
        language="vi",
        detected_language="vi",
        language_probability=0.99
    )

    assert os.path.exists(cache_file)
    with open(cache_file, "r", encoding="utf-8") as f:
        saved = json.load(f)

    assert saved["cache_schema_version"] == CACHE_SCHEMA_VERSION == 2
    assert saved["model"] == "large-v3-turbo"
    assert saved["language_requested"] == "vi"
    assert saved["detected_language"] == "vi"
    assert saved["language_probability"] == 0.99
    assert saved["file_size"] == 1024
    assert saved["content_hash"] == compute_file_checksum(dummy_video)
    assert len(saved["raw_subtitles"]) == 1


def test_get_cached_scan_exact_hit(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    dummy_video = str(tmp_path / "clip_02.mp4")
    with open(dummy_video, "wb") as f:
        f.write(b"x" * 2048)

    mgr.save_scan_result(
        file_path=dummy_video,
        scan_data={"clip_dur": 5.0, "raw_subtitles": []},
        model_size="large-v3-turbo",
        language="vi"
    )

    # Exact hit
    hit = mgr.get_cached_scan(dummy_video, model_size="large-v3-turbo", language="vi")
    assert hit is not None
    assert hit["clip_dur"] == 5.0
    assert hit["model"] == "large-v3-turbo"


def test_get_cached_scan_model_mismatch_returns_miss(tmp_path):
    # Tránh lỗi cũ: chuyển sang large-v3-turbo nhưng vẫn nạp nhầm cache của small
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    dummy_video = str(tmp_path / "clip_03.mp4")
    with open(dummy_video, "wb") as f:
        f.write(b"y" * 4096)

    # File từng được quét với "small"
    mgr.save_scan_result(
        file_path=dummy_video,
        scan_data={"clip_dur": 12.0, "raw_subtitles": []},
        model_size="small",
        language="vi"
    )

    # Khi người dùng yêu cầu "large-v3-turbo", không được nạp cache của "small"
    miss = mgr.get_cached_scan(dummy_video, model_size="large-v3-turbo", language="vi")
    assert miss is None


def test_get_cached_scan_language_mismatch_returns_miss(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    dummy_video = str(tmp_path / "clip_04.mp4")
    with open(dummy_video, "wb") as f:
        f.write(b"z" * 1024)

    mgr.save_scan_result(
        file_path=dummy_video,
        scan_data={"clip_dur": 8.0, "raw_subtitles": []},
        model_size="small",
        language="en"
    )

    # Người dùng yêu cầu tiếng Việt -> không nạp cache tiếng Anh
    miss = mgr.get_cached_scan(dummy_video, model_size="small", language="vi")
    assert miss is None


def test_get_cached_scan_backward_compatibility_old_small_auto(tmp_path):
    # Các cache cũ 'small_auto' vẫn được nhận diện khi người dùng yêu cầu 'vi' hoặc 'auto'
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    dummy_video = str(tmp_path / "clip_legacy.mp4")
    with open(dummy_video, "wb") as f:
        f.write(b"legacy" * 200)

    f_hash = compute_file_checksum(dummy_video)
    old_cache_file = os.path.join(cache_dir, f"{f_hash}_small_auto.json")
    old_data = {
        "clip_dur": 15.0,
        "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Câu cũ"}],
        # Schema v1 cũ không có trường file_size hay cache_schema_version
    }
    with open(old_cache_file, "w", encoding="utf-8") as f:
        json.dump(old_data, f)

    # Nạp với requested "vi" -> nhận diện tương thích ngược thành công
    hit = mgr.get_cached_scan(dummy_video, model_size="small", language="vi")
    assert hit is not None
    assert hit["clip_dur"] == 15.0


def test_filename_fallback_verifies_file_size(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    video_file = str(tmp_path / "same_name.mp4")
    with open(video_file, "wb") as f:
        f.write(b"A" * 5000)

    mgr.save_scan_result(
        file_path=video_file,
        scan_data={"clip_dur": 20.0, "raw_subtitles": []},
        model_size="small",
        language="vi"
    )

    # 1. Khi file giữ nguyên kích thước: fallback hit
    hit = mgr.get_cached_scan(video_file, model_size="small", language="vi")
    assert hit is not None

    # 2. Khi nội dung/kích thước file thay đổi (file bị ghi đè thành 100 bytes):
    with open(video_file, "wb") as f:
        f.write(b"B" * 100)

    # Xóa cache key checksum để buộc phải fallback bằng name_key
    f_hash_old = compute_file_checksum(video_file)  # calculates on new content
    # Name cache vẫn còn với file_size cũ 5000 bytes
    miss = mgr.get_cached_scan(video_file, model_size="small", language="vi")
    assert miss is None  # Bị từ chối vì file_size không khớp!
