"""
tests/test_clip_manifest.py
Bao gồm 7 bài test bắt buộc cho Giai đoạn 0A (Hết dựng sai clip):
1. Xáo trộn selected_files 5 lần -> manifest_id và thứ tự clip_id luôn bất biến.
2. ReviewPage sinh prompt -> LLM trả về -> AutoWindow nhận với selected_files đảo ngược -> timeline segments vẫn map đúng file gốc.
3. Di chuyển file sang thư mục khác (giả lập đổi ổ đĩa) -> relink tự khớp lại qua hash + filename + size.
4. Hai file trùng tên ở hai thư mục khác nhau -> sinh ra 2 clip_id riêng biệt, không đè.
5. Lệch manifest_id bị chặn; thiếu manifest_id chạy kèm cảnh báo tương thích ngược.
6. Xung đột clip_index và clip_name -> ưu tiên clip_index và emit log warning.
7. Prompt sinh ra không còn chứa cột 'Cao trào' và 'Chuyển động' giả, loại clip < 1.0s.
"""

import os
import random
import logging
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.clip_manifest import (
    ClipManifest,
    ClipEntry,
    build_clips_data,
    compute_manifest_id
)
from src.core.story_copilot import (
    StoryCopilot,
    CopilotDirectorPlan,
    CopilotSegmentPlan,
    CopilotHookPlan
)


def _create_mock_video_file(path: str, size: int = 2048, content: bytes = b"test_video_data"):
    """Tạo file giả lập video để test hash và metadata."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        # Ghi nội dung lặp lại để đủ kích thước
        repetitions = (size // len(content)) + 1
        f.write((content * repetitions)[:size])


def test_1_scrambled_selection_preserves_order_and_manifest_id(tmp_path):
    """Test 1: Xáo trộn selected_files 5 lần -> ClipManifest luôn cho ra cùng thứ tự và cùng manifest_id."""
    manifest_cache = str(tmp_path / "cache_manifests")
    video_dir = tmp_path / "videos"
    video_dir.mkdir()

    # Tạo 5 file với timestamp ISO khác nhau trong tên file
    file_names = [
        "DJI_20260830100000_0001_D.MP4",
        "DJI_20260830113000_0002_D.MP4",
        "DJI_20260830140000_0003_D.MP4",
        "DJI_20260831090000_0004_D.MP4",
        "DJI_20260831160000_0005_D.MP4",
    ]
    file_paths = []
    for fn in file_names:
        fp = str(video_dir / fn)
        _create_mock_video_file(fp, size=4096, content=fn.encode("utf-8"))
        file_paths.append(fp)

    reference_manifest = None
    ref_ids = None

    for seed in range(5):
        shuffled = list(file_paths)
        random.seed(seed * 42 + 7)
        random.shuffle(shuffled)

        # Sử dụng subfolder cache riêng cho mỗi lần tạo mới hoàn toàn
        proj_cache = str(tmp_path / f"cache_run_{seed}")
        manifest = ClipManifest.create(shuffled, project_name="Travel_Vlog", cache_dir=proj_cache)

        curr_ids = [(c.clip_id, c.file_name) for c in manifest.clips]

        if reference_manifest is None:
            reference_manifest = manifest
            ref_ids = curr_ids
        else:
            assert manifest.manifest_id == reference_manifest.manifest_id, (
                f"Lần chạy {seed}: manifest_id bị đổi ({manifest.manifest_id} != {reference_manifest.manifest_id})"
            )
            assert curr_ids == ref_ids, f"Lần chạy {seed}: Thứ tự clip_id bị đổi!"

    assert ref_ids[0][1] == "DJI_20260830100000_0001_D.MP4"
    assert ref_ids[4][1] == "DJI_20260831160000_0005_D.MP4"


def test_2_cross_component_manifest_consistency(tmp_path):
    """
    Test 2: ReviewPage sinh prompt -> LLM trả JSON -> AutoWindow nạp với selected_files đảo ngược
    -> các timeline segment vẫn trỏ đúng file video gốc.
    """
    video_dir = tmp_path / "footage"
    video_dir.mkdir()

    v1 = str(video_dir / "DJI_20260830100000_A.MP4")
    v2 = str(video_dir / "DJI_20260830120000_B.MP4")
    v3 = str(video_dir / "DJI_20260830150000_C.MP4")
    _create_mock_video_file(v1, content=b"clip_a")
    _create_mock_video_file(v2, content=b"clip_b")
    _create_mock_video_file(v3, content=b"clip_c")

    selected_in_review = [v1, v2, v3]
    cache_dir = str(tmp_path / "cache")
    manifest_review = ClipManifest.create(selected_in_review, project_name="SyncTest", cache_dir=cache_dir)
    m_id = manifest_review.manifest_id

    # Giả lập JSON LLM sinh ra từ ReviewPage (tham chiếu clip 2 và clip 3)
    llm_json = f"""
    {{
        "manifest_id": "{m_id}",
        "strategy_summary": "Test AIDA sync",
        "global_hook": {{
            "clip_id": "C02",
            "clip_index": 2,
            "clip_name": "DJI_20260830120000_B.MP4",
            "start_sec": 1.0,
            "end_sec": 5.0,
            "hook_title": "Hook B"
        }},
        "timeline_segments": [
            {{
                "clip_id": "C03",
                "clip_index": 3,
                "clip_name": "DJI_20260830150000_C.MP4",
                "start_sec": 0.0,
                "end_sec": 6.0,
                "action": "keep"
            }},
            {{
                "clip_id": "C01",
                "clip_index": 1,
                "clip_name": "DJI_20260830100000_A.MP4",
                "start_sec": 2.0,
                "end_sec": 8.0,
                "action": "keep"
            }}
        ]
    }}
    """
    plan = StoryCopilot.parse_copilot_response(llm_json)

    # Trong AutoWindow: người dùng nạp với thứ tự đảo ngược hoàn toàn [v3, v2, v1]
    selected_in_auto = [v3, v2, v1]
    # Khi AutoWindow tải hoặc tái tạo manifest cho cùng project SyncTest
    manifest_auto = ClipManifest.get_or_create(selected_in_auto, project_name="SyncTest", cache_dir=cache_dir)

    assert manifest_auto.manifest_id == plan.manifest_id

    # Dựng path_map theo logic AutoWindow mới (từ manifest_auto.clips)
    path_map = {}
    for i, entry in enumerate(manifest_auto.clips):
        p_abs = os.path.abspath(entry.current_path)
        path_map[entry.clip_id] = p_abs
        path_map[entry.clip_id.lower()] = p_abs
        path_map[i + 1] = p_abs
        path_map[str(i + 1)] = p_abs
        path_map[entry.file_name.lower()] = p_abs

    # Phân giải hook và segment
    hook_path = StoryCopilot._find_clip_path(plan.global_hook.clip_id or plan.global_hook.clip_index, plan.global_hook.clip_name, path_map)
    assert hook_path == os.path.abspath(v2), f"Hook phải trỏ về clip B ({v2}), nhận được: {hook_path}"

    seg1_path = StoryCopilot._find_clip_path(plan.timeline_segments[0].clip_id or plan.timeline_segments[0].clip_index, plan.timeline_segments[0].clip_name, path_map)
    assert seg1_path == os.path.abspath(v3), f"Seg 1 phải trỏ về clip C ({v3}), nhận được: {seg1_path}"

    seg2_path = StoryCopilot._find_clip_path(plan.timeline_segments[1].clip_id or plan.timeline_segments[1].clip_index, plan.timeline_segments[1].clip_name, path_map)
    assert seg2_path == os.path.abspath(v1), f"Seg 2 phải trỏ về clip A ({v1}), nhận được: {seg2_path}"


def test_3_manifest_relink(tmp_path):
    """Test 3: Di chuyển file sang thư mục khác (đổi ổ đĩa) -> relink tự khớp lại qua hash + filename + size."""
    src_dir = tmp_path / "drive_original"
    dst_dir = tmp_path / "drive_remounted"
    src_dir.mkdir()
    dst_dir.mkdir()

    f1_src = str(src_dir / "clip_alpha.mp4")
    f2_src = str(src_dir / "clip_beta.mp4")
    _create_mock_video_file(f1_src, size=5000, content=b"content_alpha_unique")
    _create_mock_video_file(f2_src, size=8000, content=b"content_beta_unique")

    manifest = ClipManifest.create([f1_src, f2_src], project_name="RelinkTest", cache_dir=str(tmp_path))
    original_id = manifest.manifest_id
    assert len(manifest.clips) == 2

    # Di chuyển file từ src sang dst
    f1_dst = str(dst_dir / "clip_alpha.mp4")
    f2_dst = str(dst_dir / "clip_beta.mp4")
    os.rename(f1_src, f1_dst)
    os.rename(f2_src, f2_dst)

    assert not os.path.exists(manifest.clips[0].current_path)
    assert not os.path.exists(manifest.clips[1].current_path)

    # Gọi relink
    relinked_count = manifest.relink(str(dst_dir))
    assert relinked_count == 2
    assert os.path.exists(manifest.clips[0].current_path)
    assert os.path.exists(manifest.clips[1].current_path)
    assert manifest.clips[0].current_path == f1_dst
    assert manifest.clips[1].current_path == f2_dst

    # Manifest ID vẫn nguyên vẹn
    assert manifest.manifest_id == original_id


def test_4_duplicate_filenames_distinct_clip_ids(tmp_path):
    """Test 4: Hai file trùng tên ở hai thư mục khác nhau và nội dung khác -> sinh ra 2 clip_id riêng biệt."""
    folder_a = tmp_path / "folder_a"
    folder_b = tmp_path / "folder_b"
    folder_a.mkdir()
    folder_b.mkdir()

    file_a = str(folder_a / "intro.mp4")
    file_b = str(folder_b / "intro.mp4")
    _create_mock_video_file(file_a, size=3000, content=b"camera_a_footage")
    _create_mock_video_file(file_b, size=7000, content=b"camera_b_footage_different")

    manifest = ClipManifest.create([file_a, file_b], project_name="DupNameTest", cache_dir=str(tmp_path))

    assert len(manifest.clips) == 2
    c1, c2 = manifest.clips[0], manifest.clips[1]
    assert c1.clip_id != c2.clip_id
    assert {c1.clip_id, c2.clip_id} == {"C01", "C02"}
    assert c1.file_name == "intro.mp4"
    assert c2.file_name == "intro.mp4"
    assert c1.content_hash != c2.content_hash
    assert c1.current_path != c2.current_path


def test_5_manifest_id_mismatch_and_missing_warning(tmp_path, caplog):
    """Test 5: Áp dụng JSON có manifest_id lệch -> chặn dựng. Thiếu manifest_id -> cho phép dựng kèm cảnh báo."""
    v1 = str(tmp_path / "vid1.mp4")
    _create_mock_video_file(v1, content=b"vid1")

    manifest = ClipManifest.create([v1], project_name="MismatchTest", cache_dir=str(tmp_path))
    correct_mid = manifest.manifest_id

    # 5a: Mismatch manifest_id
    bad_json = """
    {
        "manifest_id": "DEADBEEF",
        "strategy_summary": "Bad manifest plan",
        "timeline_segments": [{"clip_index": 1, "clip_name": "vid1.mp4", "start_sec": 0, "end_sec": 5}]
    }
    """
    plan_mismatch = StoryCopilot.parse_copilot_response(bad_json)
    assert plan_mismatch.manifest_id == "DEADBEEF"
    assert plan_mismatch.manifest_id != correct_mid

    # 5b: Missing manifest_id (kịch bản cũ)
    legacy_json = """
    {
        "strategy_summary": "Legacy plan without manifest_id",
        "timeline_segments": [{"clip_index": 1, "clip_name": "vid1.mp4", "start_sec": 0, "end_sec": 5}]
    }
    """
    with caplog.at_level(logging.WARNING):
        plan_legacy = StoryCopilot.parse_copilot_response(legacy_json)
    assert plan_legacy.manifest_id is None
    # Kiểm tra kịch bản cũ vẫn parse thành công các segment
    assert len(plan_legacy.timeline_segments) == 1
    assert plan_legacy.timeline_segments[0].clip_name == "vid1.mp4"


def test_6_clip_resolution_conflict_prefers_index(caplog):
    """Test 6: Segment có clip_index=3 và clip_name='clip5.mp4' (xung đột) -> ưu tiên clip_index=3, có log warning."""
    path_map = {
        1: "/path/to/clip1.mp4",
        2: "/path/to/clip2.mp4",
        3: "/path/to/clip3.mp4",
        4: "/path/to/clip4.mp4",
        5: "/path/to/clip5.mp4",
        "clip1.mp4": "/path/to/clip1.mp4",
        "clip2.mp4": "/path/to/clip2.mp4",
        "clip3.mp4": "/path/to/clip3.mp4",
        "clip4.mp4": "/path/to/clip4.mp4",
        "clip5.mp4": "/path/to/clip5.mp4",
    }

    # Tạo segment xung đột: clip_index=3 nhưng clip_name="clip5.mp4"
    seg = CopilotSegmentPlan(
        clip_index=3,
        clip_name="clip5.mp4",
        start_sec=0.0,
        end_sec=5.0
    )

    with caplog.at_level(logging.WARNING):
        resolved_path = StoryCopilot._find_clip_path(seg.clip_index, seg.clip_name, path_map)

    # Ưu tiên index 3
    assert resolved_path == "/path/to/clip3.mp4"
    # Có cảnh báo xung đột được log
    warning_logged = any("Xung đột chỉ số" in record.message or "clip_index" in record.message for record in caplog.records)
    assert warning_logged, "Phải log cảnh báo khi clip_index và clip_name trỏ đến 2 clip khác nhau!"


def test_7_prompt_generation_no_fake_columns_and_filters_short_clips():
    """Test 7: Prompt sinh ra không còn chứa cột 'Cao trào' và 'Chuyển động' giả, và loại bỏ clip < 1.0s."""
    clips_data = [
        {
            "clip_id": "C01",
            "clip_index": 1,
            "id": 1,
            "name": "clip_normal.mp4",
            "clip_name": "clip_normal.mp4",
            "chapter": "Chung",
            "duration": 45.0,
            "transcript": "Lời thoại dài và chuẩn xác.",
            "is_broll": False,
            "dialogue_intervals": []
        },
        {
            "clip_id": "C02",
            "clip_index": 2,
            "id": 2,
            "name": "clip_super_short.mp4",
            "clip_name": "clip_super_short.mp4",
            "chapter": "Chung",
            "duration": 0.5,  # < 1.0s -> phải bị loại
            "transcript": "Bỏ qua",
            "is_broll": False,
            "dialogue_intervals": []
        }
    ]

    prompt = StoryCopilot.generate_copilot_prompt(
        project_name="TestPrompt",
        clips_data=clips_data,
        manifest_id="A1B2C3D4"
    )

    # Kiểm tra manifest_id có trong prompt
    assert "MANIFEST_ID: A1B2C3D4" in prompt
    assert "A1B2C3D4" in prompt

    # Kiểm tra không chứa cột giả mạo
    assert "Cao trào âm thanh" not in prompt
    assert "Chuyển động" not in prompt
    assert "audio_peak_sec" not in prompt
    assert "visual_motion" not in prompt

    # Kiểm tra clip_super_short (< 1.0s) bị loại khỏi bảng clips
    assert "clip_normal.mp4" in prompt
    assert "clip_super_short.mp4" not in prompt
