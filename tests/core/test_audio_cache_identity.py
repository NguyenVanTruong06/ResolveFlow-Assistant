"""
Unit tests for Full-Content Audio Cache Identity and Compatibility (Schema V3)
G05-05A-R1 Acceptance Test Suite
"""

import os
import json
import math
import time
import pytest
from unittest.mock import patch, MagicMock

from src.core.cache_manager import (
    ScanCacheManager,
    compute_file_checksum,
    compute_full_file_sha256,
    validate_file_identity,
    validate_v3_payload,
    CACHE_SCHEMA_VERSION,
)


def test_full_hash_rename_touch_restart_hit(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    video_a = str(tmp_path / "original_clip.mp4")
    content = b"VIDEO_CONTENT_PAYLOAD_1234567890" * 100
    with open(video_a, "wb") as f:
        f.write(content)

    full_hash, prov = compute_full_file_sha256(video_a)
    assert full_hash is not None
    assert prov is not None

    scan_data = {
        "clip_dur": 15.0,
        "raw_subtitles": [{"start": 1.0, "end": 4.0, "text": "Kiểm tra cache v3"}],
        "silence_keep_intervals": [(0.0, 15.0)]
    }

    v3_path = mgr.save_scan_result(
        file_path=video_a,
        scan_data=scan_data,
        model_size="small",
        language="vi",
        verified_identity=(full_hash, prov)
    )
    assert os.path.exists(v3_path)

    # 1. Exact hit
    hit = mgr.get_cached_scan(video_a, model_size="small", language="vi", verify_full=True)
    assert hit is not None
    assert hit["raw_subtitles"] == scan_data["raw_subtitles"]
    assert hit["cache_schema_version"] == 3
    assert hit["full_content_sha256"] == full_hash

    # 2. Rename hit: Di chuyển/đổi tên file sang thư mục khác
    new_dir = tmp_path / "renamed_folder"
    new_dir.mkdir()
    video_b = str(new_dir / "moved_clip.mp4")
    os.rename(video_a, video_b)

    hit_renamed = mgr.get_cached_scan(video_b, model_size="small", language="vi", verify_full=True)
    assert hit_renamed is not None
    assert hit_renamed["full_content_sha256"] == full_hash
    assert hit_renamed["raw_subtitles"] == scan_data["raw_subtitles"]

    # 3. Touch hit: Thay đổi mtime nhưng giữ nguyên nội dung
    new_mtime = time.time() - 5000.0
    os.utime(video_b, (new_mtime, new_mtime))
    hit_touched = mgr.get_cached_scan(video_b, model_size="small", language="vi", verify_full=True)
    assert hit_touched is not None
    assert hit_touched["full_content_sha256"] == full_hash

    # 4. Restart manager hit
    fresh_mgr = ScanCacheManager(cache_dir=cache_dir)
    hit_restarted = fresh_mgr.get_cached_scan(video_b, model_size="small", language="vi", verify_full=True)
    assert hit_restarted is not None
    assert hit_restarted["full_content_sha256"] == full_hash


def test_same_filename_and_size_different_content_miss(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    dir1 = tmp_path / "cam1"
    dir1.mkdir()
    file1 = str(dir1 / "DJI_0001.MP4")
    with open(file1, "wb") as f:
        f.write(b"CAMERA_1_AUDIO_DATA_" * 50)

    h1, p1 = compute_full_file_sha256(file1)
    mgr.save_scan_result(
        file_path=file1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Cam 1"}]},
        model_size="small",
        language="vi",
        verified_identity=(h1, p1)
    )

    # File 2 khác camera nhưng trùng tên generic DJI_0001.MP4 và cùng size
    dir2 = tmp_path / "cam2"
    dir2.mkdir()
    file2 = str(dir2 / "DJI_0001.MP4")
    with open(file2, "wb") as f:
        f.write(b"CAMERA_2_DIFFERENT__" * 50)
    assert os.path.getsize(file1) == os.path.getsize(file2)

    # Phải từ chối cache hit vì nội dung thực khác nhau
    miss = mgr.get_cached_scan(file2, model_size="small", language="vi", verify_full=True)
    assert miss is None


def test_same_size_and_mtime_replacement_detected_by_inode_or_ctime(tmp_path):
    """Khi file bị thay thế bằng file khác cùng size và khôi phục mtime (inode/ctime đổi), identity cũ bị từ chối."""
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    target_file = str(tmp_path / "target.mp4")
    with open(target_file, "wb") as f:
        f.write(b"ORIGINAL_PAYLOAD_AAA" * 50)

    h_orig, p_orig = compute_full_file_sha256(target_file)
    assert p_orig is not None

    # Tạo tệp thay thế độc lập cùng dung lượng
    replacement_file = str(tmp_path / "replacement.mp4")
    with open(replacement_file, "wb") as f:
        f.write(b"REPLACEMENT_DATA_BBB" * 50)
    assert os.path.getsize(target_file) == os.path.getsize(replacement_file)

    # Khôi phục mtime của replacement cho trùng target (dùng ns để bảo toàn nano-giây)
    orig_mtime_ns = p_orig["mtime_ns"]
    os.utime(replacement_file, ns=(orig_mtime_ns, orig_mtime_ns))

    # Dùng os.replace để thay thế file cũ (thao tác này thay đổi inode và ctime)
    os.replace(replacement_file, target_file)

    # 1. validate_file_identity trực tiếp phải phát hiện ra (inode hoặc ctime thay đổi)
    assert validate_file_identity(target_file, p_orig) is False

    # 2. get_cached_scan truyền verified_identity cũ phải bị từ chối
    miss_get = mgr.get_cached_scan(
        target_file,
        model_size="small",
        language="vi",
        verify_full=True,
        verified_identity=(h_orig, p_orig)
    )
    assert miss_get is None

    # 3. save_scan_result truyền verified_identity cũ phải từ chối ghi v3
    denied_save = mgr.save_scan_result(
        file_path=target_file,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Stale"}]},
        model_size="small",
        language="vi",
        verified_identity=(h_orig, p_orig)
    )
    assert denied_save == ""


def test_in_place_edit_restore_mtime_detected_by_ctime(tmp_path):
    """Khi file bị sửa đổi tại chỗ (in-place) cùng size và khôi phục mtime, ctime đổi khiến identity cũ bị từ chối."""
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    target_file = str(tmp_path / "inplace_target.mp4")
    with open(target_file, "wb") as f:
        f.write(b"ORIGINAL_INPLACE_DATA" * 50)

    h_orig, p_orig = compute_full_file_sha256(target_file)
    assert p_orig is not None

    mgr.save_scan_result(
        file_path=target_file,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Inplace Original"}]},
        model_size="small",
        language="vi",
        verified_identity=(h_orig, p_orig)
    )

    # Chờ ngắn để ctime hệ thống có bước nhảy
    time.sleep(0.01)

    # Sửa đổi nội dung tại chỗ bằng r+b với dung lượng chính xác tương đương
    with open(target_file, "r+b") as f:
        f.seek(0)
        f.write(b"MODIFIED_INPLACE_DATA" * 50)
        f.flush()
        os.fsync(f.fileno())

    # Khôi phục mtime về đúng mtime ban đầu (dùng ns để giữ nguyên độ chính xác nano-giây)
    orig_mtime_ns = p_orig["mtime_ns"]
    os.utime(target_file, ns=(orig_mtime_ns, orig_mtime_ns))

    # Xác minh kích thước và mtime giống hệt
    st_current = os.stat(target_file)
    assert st_current.st_size == p_orig["file_size"]
    assert st_current.st_mtime_ns == p_orig["mtime_ns"]
    assert st_current.st_ino == p_orig["ino"]
    assert st_current.st_ctime_ns != p_orig["ctime_ns"]

    # Nhưng ctime hoặc nội dung đã đổi, nên validate_file_identity phải từ chối
    assert validate_file_identity(target_file, p_orig) is False

    # get_cached_scan với verified_identity cũ phải trả về None
    miss = mgr.get_cached_scan(
        target_file,
        model_size="small",
        language="vi",
        verify_full=True,
        verified_identity=(h_orig, p_orig)
    )
    assert miss is None

    # save_scan_result với verified_identity cũ phải từ chối ghi
    denied = mgr.save_scan_result(
        file_path=target_file,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Stale Inplace"}]},
        model_size="small",
        language="vi",
        verified_identity=(h_orig, p_orig)
    )
    assert denied == ""


def test_compute_full_file_sha256_detects_file_replacement_during_read(tmp_path):
    """Khi file bị thay thế trên đường dẫn trong khi đang stream chunk, compute_full_file_sha256 phát hiện và trả về (None, None)."""
    target_file = str(tmp_path / "streaming_target.mp4")
    # Tạo file 3MB
    content = b"STREAMING_TEST_CHUNK" * (3 * 1024 * 1024 // 20)
    with open(target_file, "wb") as f:
        f.write(content)

    replacement_file = str(tmp_path / "streaming_repl.mp4")
    with open(replacement_file, "wb") as f:
        f.write(b"REPLACEMENT_NEW_DATA" * (3 * 1024 * 1024 // 20))

    chunks_checked = 0
    def check_and_replace():
        nonlocal chunks_checked
        chunks_checked += 1
        if chunks_checked == 2:
            os.replace(replacement_file, target_file)
        return False

    h, p = compute_full_file_sha256(target_file, cancelled_fn=check_and_replace, chunk_size=1024 * 1024)
    # Do đường dẫn bị đổi inode giữa chừng, compute_full_file_sha256 phải phát hiện và từ chối
    assert h is None
    assert p is None


def test_compute_full_file_sha256_descriptor_mismatch_fails_safe(tmp_path):
    """Khi file descriptor fstat không khớp path stat ban đầu (TOCTOU ngay lúc mở), trả về (None, None)."""
    target_file = str(tmp_path / "toctou_target.mp4")
    with open(target_file, "wb") as f:
        f.write(b"TOCTOU_DATA" * 50)

    real_fstat = os.fstat
    def altered_fstat(fd):
        st = real_fstat(fd)
        # Giả lập mtime fstat khác với path stat
        mock_st = MagicMock(wraps=st)
        mock_st.st_mtime_ns = st.st_mtime_ns + 999999
        mock_st.st_size = st.st_size
        mock_st.st_ctime_ns = st.st_ctime_ns
        mock_st.st_dev = st.st_dev
        mock_st.st_ino = st.st_ino
        return mock_st

    with patch("os.fstat", side_effect=altered_fstat):
        h, p = compute_full_file_sha256(target_file)
    assert h is None
    assert p is None


def test_middle_edit_over_2mb_same_head_tail_miss(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    chunk_1mb = 1024 * 1024
    head = b"H" * chunk_1mb
    mid_orig = b"M_ORIGINAL" * (chunk_1mb // 10)
    tail = b"T" * chunk_1mb

    video_file = str(tmp_path / "big_video.mp4")
    with open(video_file, "wb") as f:
        f.write(head + mid_orig + tail)

    h_orig, p_orig = compute_full_file_sha256(video_file)
    mgr.save_scan_result(
        file_path=video_file,
        scan_data={"clip_dur": 120.0, "raw_subtitles": [{"start": 0.0, "end": 10.0, "text": "Bản gốc"}]},
        model_size="small",
        language="vi",
        verified_identity=(h_orig, p_orig)
    )

    mid_modified = b"M_MODIFIED" * (chunk_1mb // 10)
    with open(video_file, "wb") as f:
        f.write(head + mid_modified + tail)

    miss = mgr.get_cached_scan(video_file, model_size="small", language="vi", verify_full=True)
    assert miss is None


def test_model_or_language_mismatch_miss(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "lang_test.mp4")
    with open(video, "wb") as f:
        f.write(b"SAMPLE_SPEECH_DATA" * 100)

    h, p = compute_full_file_sha256(video)
    mgr.save_scan_result(
        file_path=video,
        scan_data={"clip_dur": 30.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Xin chào"}]},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )

    assert mgr.get_cached_scan(video, model_size="large-v3", language="vi", verify_full=True) is None
    assert mgr.get_cached_scan(video, model_size="small", language="en", verify_full=True) is None
    assert mgr.get_cached_scan(video, model_size="small", language="auto", verify_full=True) is None


def test_empty_transcript_is_valid_hit_but_missing_or_wrong_type_miss(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "broll_silent.mp4")
    with open(video, "wb") as f:
        f.write(b"SILENT_BROLL_CONTENT" * 100)

    h, p = compute_full_file_sha256(video)
    
    # 1. raw_subtitles = [] là hợp lệ (b-roll không thoại)
    mgr.save_scan_result(
        file_path=video,
        scan_data={"clip_dur": 8.0, "raw_subtitles": []},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )

    hit = mgr.get_cached_scan(video, model_size="small", language="vi", verify_full=True)
    assert hit is not None
    assert hit["raw_subtitles"] == []
    assert hit["clip_dur"] == 8.0

    # 2. Corrupt payload: raw_subtitles is None or not a list -> MUST return None (miss)
    v3_key = mgr._build_v3_cache_key(h, "small", "vi")
    v3_file = os.path.join(cache_dir, f"{v3_key}.json")
    with open(v3_file, "r", encoding="utf-8") as f:
        corrupted = json.load(f)
    
    corrupted["raw_subtitles"] = None
    with open(v3_file, "w", encoding="utf-8") as f:
        json.dump(corrupted, f)

    miss_corrupt = mgr.get_cached_scan(video, model_size="small", language="vi", verify_full=True)
    assert miss_corrupt is None


@pytest.mark.parametrize("invalid_dur", [
    True, False, float("nan"), float("inf"), -5.0, 0.0, None, "10.0", 10**400, -10**400
])
def test_validate_v3_payload_parametrized_duration(invalid_dur):
    """clip_dur phải là số dương hữu hạn, không được là bool, NaN, Inf, âm, 0 hoặc bigint gây overflow."""
    payload = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "a" * 64,
        "clip_dur": invalid_dur,
        "raw_subtitles": []
    }
    assert validate_v3_payload(payload) is False


@pytest.mark.parametrize("invalid_schema_ver", [
    3.0, "3", 2, 4, True, False, None
])
def test_validate_v3_payload_schema_version_strict_int(invalid_schema_ver):
    """cache_schema_version bắt buộc phải là số nguyên int = 3, không chấp nhận float 3.0 hay bool."""
    payload = {
        "cache_schema_version": invalid_schema_ver,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "a" * 64,
        "clip_dur": 10.0,
        "raw_subtitles": []
    }
    assert validate_v3_payload(payload) is False


@pytest.mark.parametrize("invalid_subs", [
    [123],
    [{"start": 5.0, "end": 2.0, "text": "end_before_start"}],
    [{"start": -1.0, "end": 2.0, "text": "negative_start"}],
    [{"start": 1.0, "end": 2.0, "text": 456}],
    [{"start": float("nan"), "end": 2.0, "text": "nan_start"}],
    [{"start": 0.0, "end": float("inf"), "text": "inf_end"}],
    [{"start": 0.0, "end": 10**400, "text": "bigint_end"}],
    [{"start": 10**400, "end": 10**401, "text": "bigint_start"}],
    "not_a_list"
])
def test_validate_v3_payload_invalid_segments(invalid_subs):
    """raw_subtitles phải là danh sách các segment mapping hợp lệ theo đúng shape producer."""
    payload = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "a" * 64,
        "clip_dur": 10.0,
        "raw_subtitles": invalid_subs
    }
    assert validate_v3_payload(payload) is False


@pytest.mark.parametrize("bad_identity", [
    "not_a_tuple",
    (None, None),
    ("valid_hash", "not_a_dict"),
    ("valid_hash", {}),
    ("", {"file_size": 100, "mtime_ns": 100}),
    ("not_64_hex", {"file_size": 100, "mtime_ns": 100}),
    ("g" * 64, {"file_size": 100, "mtime_ns": 100}),
    12345
])
def test_malformed_supplied_identity_fails_closed(tmp_path, bad_identity):
    """Truyền supplied identity không đúng định dạng phải fail-closed an toàn, không ném exception."""
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)
    dummy = str(tmp_path / "dummy.mp4")
    with open(dummy, "wb") as f:
        f.write(b"DATA")

    # get_cached_scan trả về None
    assert mgr.get_cached_scan(dummy, verify_full=True, verified_identity=bad_identity) is None
    # save_scan_result trả về "" và tuyệt đối không fallback legacy
    assert mgr.save_scan_result(dummy, {"clip_dur": 5.0, "raw_subtitles": []}, verified_identity=bad_identity) == ""
    assert len(os.listdir(cache_dir)) == 0


@pytest.mark.parametrize("bad_digest", [
    "short_digest",
    "a" * 63,
    "a" * 65,
    "g" * 64,
    "G" * 64,
    1234567890,
    None,
    ""
])
def test_malformed_digest_rejected_before_io(tmp_path, bad_digest):
    """Digest không phải canonical 64hex phải bị từ chối ngay trước khi tạo cache key hoặc tra cứu IO."""
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)
    dummy = str(tmp_path / "dummy.mp4")
    with open(dummy, "wb") as f:
        f.write(b"SAMPLE_DATA_FOR_DIGEST")

    h, prov = compute_full_file_sha256(dummy)
    assert prov is not None

    # 1. get_cached_scan bị deny
    assert mgr.get_cached_scan(dummy, verify_full=True, verified_identity=(bad_digest, prov)) is None

    # 2. save_scan_result bị deny và không tạo file
    saved = mgr.save_scan_result(
        dummy,
        {"clip_dur": 5.0, "raw_subtitles": []},
        verified_identity=(bad_digest, prov)
    )
    assert saved == ""
    assert len(os.listdir(cache_dir)) == 0

    # 3. validate_v3_payload bị deny
    payload = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": bad_digest,
        "clip_dur": 5.0,
        "raw_subtitles": []
    }
    assert validate_v3_payload(payload) is False


def test_validate_file_identity_parametrized_platform_evidence(tmp_path):
    """Provenance khuyết/null/sai kiểu bất kỳ trường nào platform có (ctime/dev/ino/size/mtime) đều bị từ chối fail-closed."""
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "platform_evidence_test.mp4")
    with open(video, "wb") as f:
        f.write(b"PLATFORM_EVIDENCE_SAMPLE" * 50)

    h, p_real = compute_full_file_sha256(video)
    assert p_real is not None
    # Provenance chuẩn phải hợp lệ
    assert validate_file_identity(video, p_real) is True

    # Tạo danh sách các provenance lỗi biên / hạ cấp trái phép
    bad_provenances = [
        # 1. Rút gọn chỉ có file_size và mtime_ns (thiếu ctime_ns, dev, ino)
        {"file_size": p_real["file_size"], "mtime_ns": p_real["mtime_ns"]},
        # 2. Khuyết từng trường platform
        {k: v for k, v in p_real.items() if k != "ctime_ns"},
        {k: v for k, v in p_real.items() if k != "dev"},
        {k: v for k, v in p_real.items() if k != "ino"},
        {k: v for k, v in p_real.items() if k != "file_size"},
        {k: v for k, v in p_real.items() if k != "mtime_ns"},
        # 3. Trường có giá trị None
        {**p_real, "ctime_ns": None},
        {**p_real, "dev": None},
        {**p_real, "ino": None},
        # 4. Trường sai kiểu dữ liệu (str, float, bool)
        {**p_real, "ctime_ns": str(p_real["ctime_ns"])},
        {**p_real, "ctime_ns": float(p_real["ctime_ns"])},
        {**p_real, "ctime_ns": True},
        {**p_real, "dev": str(p_real["dev"])},
        {**p_real, "dev": False},
        {**p_real, "ino": str(p_real["ino"])},
        {**p_real, "ino": float(p_real["ino"])},
        {**p_real, "file_size": str(p_real["file_size"])},
        {**p_real, "file_size": True},
        {**p_real, "file_size": -1},
        {**p_real, "mtime_ns": float(p_real["mtime_ns"])},
        {**p_real, "mtime_ns": False},
    ]

    for bad_p in bad_provenances:
        # validate_file_identity trực tiếp từ chối
        assert validate_file_identity(video, bad_p) is False
        # get_cached_scan với verified_identity chứa bad_p trả về None
        assert mgr.get_cached_scan(video, verify_full=True, verified_identity=(h, bad_p)) is None
        # save_scan_result từ chối ghi và trả về chuỗi rỗng
        saved = mgr.save_scan_result(
            video,
            {"clip_dur": 10.0, "raw_subtitles": []},
            verified_identity=(h, bad_p)
        )
        assert saved == ""
        # 0 tệp v3 nào được ghi vào cache_dir
        assert not any(f.startswith("v3_") for f in os.listdir(cache_dir))


def test_bigint_payload_handling_does_not_throw_and_preserves_good_cache(tmp_path):
    """Payload chứa bigint (10**400) không gây OverflowError, bị từ chối an toàn và giữ nguyên cache tốt trên đĩa."""
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "bigint_test.mp4")
    with open(video, "wb") as f:
        f.write(b"BIGINT_TEST_PAYLOAD" * 50)

    h, prov = compute_full_file_sha256(video)

    # 1. Ghi một bản cache tốt ban đầu
    good_file = mgr.save_scan_result(
        video,
        {"clip_dur": 20.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Bản tốt"}]},
        verified_identity=(h, prov)
    )
    assert os.path.exists(good_file)

    # 2. Thử ghi đè với clip_dur là bigint 10**400
    res1 = mgr.save_scan_result(
        video,
        {"clip_dur": 10**400, "raw_subtitles": []},
        verified_identity=(h, prov)
    )
    assert res1 == ""

    # 3. Thử ghi đè với segment start/end là bigint 10**400
    res2 = mgr.save_scan_result(
        video,
        {"clip_dur": 20.0, "raw_subtitles": [{"start": 0.0, "end": 10**400, "text": "overflow"}]},
        verified_identity=(h, prov)
    )
    assert res2 == ""

    # 4. Thử ghi đè với interval là bigint 10**400
    res3 = mgr.save_scan_result(
        video,
        {"clip_dur": 20.0, "raw_subtitles": [], "silence_keep_intervals": [(0.0, 10**400)]},
        verified_identity=(h, prov)
    )
    assert res3 == ""

    # 5. Xác minh bản cache tốt ban đầu vẫn còn nguyên vẹn trên đĩa
    hit = mgr.get_cached_scan(video, verify_full=True, verified_identity=(h, prov))
    assert hit is not None
    assert hit["clip_dur"] == 20.0
    assert hit["raw_subtitles"][0]["text"] == "Bản tốt"


def test_legacy_v2_exact_hit_read_by_default_but_miss_on_verified_and_no_auto_promote(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "legacy_clip.mp4")
    with open(video, "wb") as f:
        f.write(b"LEGACY_FORMAT_VIDEO" * 100)

    legacy_file = mgr.save_scan_result(
        file_path=video,
        scan_data={"clip_dur": 25.0, "raw_subtitles": [{"text": "Bản cũ v2"}]},
        model_size="small",
        language="vi"
    )
    assert os.path.exists(legacy_file)
    with open(legacy_file, "r", encoding="utf-8") as f:
        leg_data = json.load(f)
    assert leg_data.get("cache_schema_version") == 2

    # 1. Tra cứu mặc định -> Hit tương thích ngược
    fast_hit = mgr.get_cached_scan(video, model_size="small", language="vi", verify_full=False)
    assert fast_hit is not None
    assert fast_hit["clip_dur"] == 25.0

    # 2. Tra cứu xác minh -> Miss
    verified_miss = mgr.get_cached_scan(video, model_size="small", language="vi", verify_full=True)
    assert verified_miss is None

    # 3. Không tự ý tạo tệp v3 giả mạo
    all_files = os.listdir(cache_dir)
    assert not any(f.startswith("v3_") for f in all_files)


def test_name_only_fallback_denied(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    video1 = str(tmp_path / "folder_a" / "sample.mp4")
    os.makedirs(os.path.dirname(video1), exist_ok=True)
    with open(video1, "wb") as f:
        f.write(b"FIRST_VERSION" * 100)

    mgr.save_scan_result(
        file_path=video1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "A"}]},
        model_size="small",
        language="vi"
    )

    video2 = str(tmp_path / "folder_b" / "sample.mp4")
    os.makedirs(os.path.dirname(video2), exist_ok=True)
    with open(video2, "wb") as f:
        f.write(b"SECOND_VERSION" * 100)

    assert mgr.get_cached_scan(video2, model_size="small", language="vi", verify_full=False) is None
    assert mgr.get_cached_scan(video2, model_size="small", language="vi", verify_full=True) is None


def test_cancel_and_unstable_file_handling(tmp_path):
    video = str(tmp_path / "cancel_test.mp4")
    with open(video, "wb") as f:
        f.write(b"LONG_STREAMING_DATA" * 50000)

    # 1. Hủy ngay lập tức
    h, p = compute_full_file_sha256(video, cancelled_fn=lambda: True)
    assert h is None
    assert p is None

    # 2. Hủy giữa chừng
    chunk_count = 0
    def cancel_after_one_chunk():
        nonlocal chunk_count
        chunk_count += 1
        return chunk_count > 1

    h_mid, p_mid = compute_full_file_sha256(video, cancelled_fn=cancel_after_one_chunk)
    assert h_mid is None
    assert p_mid is None


def test_atomic_write_failure_preserves_good_cache(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "atomic_test.mp4")
    with open(video, "wb") as f:
        f.write(b"ATOMIC_TEST_PAYLOAD" * 100)

    h, p = compute_full_file_sha256(video)
    v3_file = mgr.save_scan_result(
        file_path=video,
        scan_data={"clip_dur": 20.0, "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Bản tốt"}]},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )
    assert os.path.exists(v3_file)

    with patch("os.replace", side_effect=OSError("Disk write failure")):
        with pytest.raises(OSError):
            mgr._atomic_write_json(v3_file, {"corrupted": True})

    with open(v3_file, "r", encoding="utf-8") as f:
        intact_data = json.load(f)
    assert intact_data.get("clip_dur") == 20.0
    assert intact_data.get("raw_subtitles") == [{"start": 0.0, "end": 2.0, "text": "Bản tốt"}]


def test_source_modified_between_analysis_and_save_not_published(tmp_path):
    cache_dir = str(tmp_path / "cache")
    mgr = ScanCacheManager(cache_dir=cache_dir)

    video = str(tmp_path / "mutating.mp4")
    with open(video, "wb") as f:
        f.write(b"ORIGINAL_BEFORE_ANALYSIS" * 100)

    h, p = compute_full_file_sha256(video)

    time.sleep(0.01)
    with open(video, "wb") as f:
        f.write(b"MUTATED_DURING_ANALYSIS_EXTRA_DATA" * 120)

    saved = mgr.save_scan_result(
        file_path=video,
        scan_data={"clip_dur": 10.0, "raw_subtitles": []},
        model_size="small",
        language="vi",
        verified_identity=(h, p)
    )
    assert saved == ""
    assert not any(f.startswith("v3_") for f in os.listdir(cache_dir))
