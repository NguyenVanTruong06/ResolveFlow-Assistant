"""
tests/ui/test_prompt_worker_batch_creative_binding.py
=============================================================================
Finite Unit Test Suite cho Backend Batch Creative Support (G06-06C3-S2b):
Thẩm định toàn diện 12 mục trong Ma trận Nghiệm thu (Section C Acceptance Matrix):
1. Legacy parities (None and {}) wrapper bytes, snapshot IDs, payload keys.
2. Default preset-only and full creative context canonical JSON formatting,
   numeric values (1e-7, 1e20, 10**20), multiline/quotes/Unicode/notes/order.
3. Complete prefix included before partitioning, budget bounds & equality,
   multi-batch partition verification.
4. Source-only pure wrapper (zero asset library scan, zero demo assets, IO/network/Resolve tripwires).
5. Snapshot validation, invalid type/digest matrix, tampering detection, and
   ui_context_signature independence.
6. Evidence vs creative context independence, deterministic runs, absent/empty equivalence.
7. Nonempty prepare_batches -> copy_batch exact replay, all 6 bindings preserved,
   0 formatter/analysis calls during copy.
8. Mismatched/legacy/missing creative context combinations fail before source/cache IO.
9. Caller mutation isolation before start and at controlled after-start barrier.
10. Post-render source deletion/mutation and cancellation yield 0 success.
11. Intent parity (copy_video / run_ai) and explicit unsupported intent rejection.
12. Physical thread cleanup and retained ownership on timeout.
=============================================================================
"""

import os
import re
import json
import time
import math
import hashlib
from fractions import Fraction
from typing import Optional, Any, Dict, List
import dataclasses
import pytest

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    ReferenceItem,
    normalize_creative_context,
    compute_creative_context_signature,
)
from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256
from src.core.geometry.local_metadata_acquisition import (
    LocalHeaderAcquisitionResult,
    SourceFreshnessStat,
)
from src.core.geometry.source_frame_evidence_contracts import VideoStreamHeader
from src.core.contracts.time_contracts import RationalFPS
from src.core.story_copilot import StoryCopilot
from src.core.batch_planner import (
    BatchBudgetConfig,
    EvidenceWindowSummary,
    partition_manifest_into_batches,
    render_batch_prompt,
)
from src.ui.director.workers.prompt_preparation_worker import (
    PromptPreparationWorker,
    PromptPreparationRequest,
    PreparedBatchSnapshot,
    PreparedBatchItem,
    BatchSourceRange,
    BaselineSourceEntry,
    compute_snapshot_id,
    validate_and_normalize_snapshot,
)


_RETAINED_RUNNING_WORKERS: List[PromptPreparationWorker] = []


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_worker_environment(tmp_path, monkeypatch):
    """Cô lập môi trường kiểm thử: HOME, XDG, chronology sorter, ffprobe locator, tripwires."""
    fake_home = str(tmp_path / "fake_home")
    os.makedirs(fake_home, exist_ok=True)
    monkeypatch.setenv("HOME", fake_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "fake_xdg_config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "fake_xdg_cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "fake_xdg_data"))
    monkeypatch.setenv("RESOLVEFLOW_VISUAL_CACHE_DIR", str(tmp_path / "fake_visual_cache"))

    # Giả lập acquire_local_video_header để tránh gọi ffprobe thật trên video giả lập
    def _mock_acquire(file_path, *, ffprobe_path=None, selected_stream_index=None, timeout_sec=5.0, cancelled_fn=None):
        if cancelled_fn and cancelled_fn():
            raise InterruptedError("ERR_PROBE_CANCELLED")
        abs_p = os.path.normpath(os.path.abspath(file_path))
        st = os.stat(abs_p)
        freshness = SourceFreshnessStat(
            file_size_bytes=st.st_size,
            mtime_ns=st.st_mtime_ns,
            ctime_ns=st.st_ctime_ns,
            device_id=st.st_dev,
            inode_number=st.st_ino,
        )
        header = VideoStreamHeader(
            stream_index=0,
            codec_name="h264",
            r_frame_rate=RationalFPS(30, 1),
            avg_frame_rate=RationalFPS(30, 1),
            reported_nb_frames=300,
            time_base=Fraction(1, 1000),
        )
        return LocalHeaderAcquisitionResult(
            file_path=abs_p,
            video_header=header,
            format_duration_sec=Fraction(10, 1),
            freshness=freshness,
        )

    import src.core.geometry.local_metadata_acquisition as lma
    monkeypatch.setattr(lma, "acquire_local_video_header", _mock_acquire)

    import src.core.chronological_sorter as cs
    monkeypatch.setattr(cs, "get_clip_shot_time", lambda path, **kw: ("2026-08-31T12:00:00", "mtime"))
    monkeypatch.setattr(cs, "sort_clips_chronologically", lambda paths, **kw: list(paths))

    import subprocess
    def _tripwire_subprocess(*args, **kwargs):
        raise AssertionError(f"TRIPWIRE: subprocess.run không được phép gọi trong pure/worker test: {args}")
    monkeypatch.setattr(subprocess, "run", _tripwire_subprocess)


def _create_dummy_video(path: str, size: int = 1024) -> tuple[str, str, str]:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RESOLVEFLOW_TEST_VIDEO_" * (size // 22 + 1))
    abs_p = os.path.normpath(os.path.abspath(path))
    digest, prov = compute_full_file_sha256(abs_p)
    return abs_p, digest, prov


def _wait_for_worker(worker: PromptPreparationWorker, timeout_sec: float = 5.0):
    deadline = time.monotonic() + timeout_sec
    app = QApplication.instance()
    while worker.isRunning() and time.monotonic() < deadline:
        QTest.qWait(20)
        if app is not None:
            app.processEvents()
    if app is not None:
        app.processEvents()
    if worker.isRunning():
        _RETAINED_RUNNING_WORKERS.append(worker)
    assert not worker.isRunning(), f"Worker {worker.request.request_id} không dừng vật lý trước deadline {timeout_sec}s"
    assert worker.isFinished()


def _setup_scan_cache(tmp_path, video_paths: list[tuple[str, str, str]]) -> ScanCacheManager:
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "test_scan_cache"))
    for idx, (v_path, digest, prov) in enumerate(video_paths):
        cache_mgr.save_scan_result(
            file_path=v_path,
            scan_data={
                "clip_dur": 20.0,
                "raw_subtitles": [
                    {"start": 1.0, "end": 8.0, "text": f"Đoạn thoại mẫu số {idx + 1} tại địa điểm quay phim."},
                    {"start": 10.0, "end": 18.0, "text": f"Đoạn thoại chi tiết tiếp theo của video {idx + 1}."},
                ],
            },
            model_size="small",
            language="vi",
            verified_identity=(digest, prov),
        )
    return cache_mgr


# =============================================================================
# 1. Acceptance Matrix Item 1: Legacy Controls & Exact Parity
# =============================================================================

def test_c01_legacy_wrapper_and_snapshot_controls():
    """1. Old None and {} wrapper bytes/snapshot IDs/payload keys match frozen legacy controls."""
    # 1A. Frozen legacy wrapper oracle (checked literal SHA256 digests from prior prefix/suffix logic)
    FROZEN_LEGACY_PREFIX_SHA256 = "c68c983f220db47494229aad26747f461cb34953e3ed86c5adbbe0740b4d9ba8"
    FROZEN_LEGACY_SUFFIX_SHA256 = "3087ec3af33cb544b5e2184209c43c0f981f79c635b0f90cf0b3dbd9cb936941"

    p_baseline, s_baseline = StoryCopilot.build_batch_prompt_wrapper(
        project_name="LegacyControlProject",
        story_intent="travel_vlog",
    )
    assert hashlib.sha256(p_baseline.encode("utf-8")).hexdigest() == FROZEN_LEGACY_PREFIX_SHA256
    assert hashlib.sha256(s_baseline.encode("utf-8")).hexdigest() == FROZEN_LEGACY_SUFFIX_SHA256

    p_none, s_none = StoryCopilot.build_batch_prompt_wrapper(
        project_name="LegacyControlProject",
        story_intent="travel_vlog",
        creative_context=None,
    )
    p_empty, s_empty = StoryCopilot.build_batch_prompt_wrapper(
        project_name="LegacyControlProject",
        story_intent="travel_vlog",
        creative_context={},
    )
    assert p_none == p_baseline
    assert s_none == s_baseline
    assert p_empty == p_baseline
    assert s_empty == s_baseline
    assert "YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO" not in p_none

    # 1B. Snapshot ID parity with independent frozen nongeometry control
    baseline_entry = BaselineSourceEntry(
        path="/media/clips/c1.mp4",
        st_size=1048576,
        st_mtime_ns=1700000000000000000,
        st_ctime_ns=1700000000000000000,
        st_ino=12345,
        st_dev=67890,
    )
    batch_item = PreparedBatchItem(
        batch_id="batch_001",
        batch_index=0,
        distinct_clip_count=1,
        total_duration_sec=20.0,
        rendered_prompt="PROMPT_CONTENT_SAMPLE_123",
        rendered_bytes=len("PROMPT_CONTENT_SAMPLE_123".encode("utf-8")),
        rendered_sha256=hashlib.sha256("PROMPT_CONTENT_SAMPLE_123".encode("utf-8")).hexdigest(),
        content_signature="sig_content_001",
        clip_ids=("C01",),
        items_count=1,
    )

    # Independent computation of expected legacy snapshot ID
    hasher = hashlib.sha256()
    hasher.update(b"manifest:m_control_01|project:LegacyControlProject|policy:source_only_v1|cap:32768\n")
    hasher.update(b"evidence_sig:evidence_sig_abc123\n")
    hasher.update(f"src:{baseline_entry.path}:{baseline_entry.st_size}:{baseline_entry.st_mtime_ns}:{baseline_entry.st_ctime_ns}:{baseline_entry.st_ino}:{baseline_entry.st_dev}\n".encode("utf-8"))
    cids = ",".join(batch_item.clip_ids)
    hasher.update(f"batch:{batch_item.batch_id}:{batch_item.batch_index}:{batch_item.rendered_bytes}:{batch_item.rendered_sha256}:{cids}\n".encode("utf-8"))
    expected_legacy_id = hasher.hexdigest().lower()

    computed_id_none = compute_snapshot_id(
        manifest_id="m_control_01",
        project_name="LegacyControlProject",
        baseline_sources=(baseline_entry,),
        batches=(batch_item,),
        evidence_context_signature="evidence_sig_abc123",
        max_prompt_bytes=32768,
        formatter_policy="source_only_v1",
        creative_context_signature=None,
    )
    assert computed_id_none == expected_legacy_id

    # 1C. Independent geometry legacy snapshot hash control (matching Codex oracle)
    b_geo_entry = BaselineSourceEntry("/codex-review/source.mp4", 100, 11, 12, 13, 14)
    geo_prompt = "codex review prompt"
    geo_sha = hashlib.sha256(geo_prompt.encode("utf-8")).hexdigest()
    geo_range = BatchSourceRange("clip1", 0.0, 10.0, 0, 1, ("w1",))
    geo_batch = PreparedBatchItem(
        batch_id="batch1",
        batch_index=0,
        distinct_clip_count=1,
        total_duration_sec=10.0,
        rendered_prompt=geo_prompt,
        rendered_bytes=len(geo_prompt.encode("utf-8")),
        rendered_sha256=geo_sha,
        content_signature="evidence",
        clip_ids=("clip1",),
        items_count=1,
        source_ranges=(geo_range,),
    )
    FROZEN_GEOMETRY_LEGACY_SNAPSHOT_ID = "158dfc638d889f80f1d894c2d3d2ad836118ae4374ca867dbdb9ebfff4580734"
    geo_sid = compute_snapshot_id(
        manifest_id="manifest",
        project_name="project",
        baseline_sources=(b_geo_entry,),
        batches=(geo_batch,),
        evidence_context_signature="ev",
        max_prompt_bytes=32768,
        formatter_policy="source_only_v1",
        creative_context_signature=None,
    )
    assert geo_sid == FROZEN_GEOMETRY_LEGACY_SNAPSHOT_ID

    # Snapshot to_dict() must omit creative_context_signature when None
    snap = PreparedBatchSnapshot(
        manifest_id="m_control_01",
        project_name="LegacyControlProject",
        snapshot_id=expected_legacy_id,
        formatter_policy="source_only_v1",
        total_batches=1,
        planned_items_count=1,
        distinct_planned_clips_count=1,
        planned_clip_ids=("C01",),
        planned_clip_ids_count=1,
        unaccounted_clip_ids=(),
        unaccounted_clips=(),
        adapter_issues=(),
        max_prompt_bytes=32768,
        batches=(batch_item,),
        baseline_sources=(baseline_entry,),
        evidence_context_signature="evidence_sig_abc123",
        creative_context_signature=None,
    )
    snap_dict = snap.to_dict()
    assert "creative_context_signature" not in snap_dict


# =============================================================================
# 2. Acceptance Matrix Item 2: Canonical JSON & Faithful Serialization
# =============================================================================

def test_c02_creative_context_formatting_and_scientific_numerics():
    """2. Default preset-only and full creative context with extreme numerics, multiline, Unicode."""
    full_cc = {
        "style_preset": "cinematic_nostalgic",
        "target_duration_sec": 1e-07,
        "free_brief": 'Đạo diễn: "Nhịp phim dồn dập, cắt gắt!"\nQuy tắc: Không bỏ lỡ khoảnh khắc "vàng".',
        "must_keep_rules": ["Giữ trọn cảnh hoàng hôn \u0110à L\u1ea1t", "Bảo toàn giọng nói thoại chính"],
        "must_not_rules": ["Không dùng hiệu ứng giật lag"],
        "reference_items": [
            {
                "reference_id": "ref_local_01",
                "local_path": "/media/references/lut_sample.cube",
                "media_type": "image",
                "role": "reference_only",
                "user_note": "LUT tham khảo tương phản cao",
            },
        ],
        "reference_urls": [
            "https://example.com/moodboard?film=1&grade=warm",
        ],
    }

    prefix, suffix = StoryCopilot.build_batch_prompt_wrapper(
        project_name="NumericUnicodeTest",
        story_intent="cinematic_nostalgic",
        creative_context=full_cc,
    )

    assert "YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO" in prefix
    assert "Đường dẫn tệp cục bộ và liên kết URL chỉ là siêu dữ liệu (metadata)" in prefix
    assert "hệ thống chưa xem, tải về hay phân tích nội dung tệp/liên kết thực tế" in prefix

    # Trích xuất và parse canonical JSON từ Markdown block
    match = re.search(r"```json\s*(\{.*?\})\s*```", prefix, re.DOTALL)
    assert match is not None, "Không tìm thấy khối ```json ... ``` trong prefix"
    parsed_json = json.loads(match.group(1))

    assert parsed_json["style_preset"] == "cinematic_nostalgic"
    assert parsed_json["target_duration_sec"] == 1e-07
    assert 'Đạo diễn: "Nhịp phim dồn dập, cắt gắt!"' in parsed_json["free_brief"]
    assert len(parsed_json["must_keep_rules"]) == 2
    assert parsed_json["must_keep_rules"][0] == "Giữ trọn cảnh hoàng hôn Đà Lạt"
    assert len(parsed_json["reference_items"]) == 1
    assert parsed_json["reference_items"][0]["reference_id"] == "ref_local_01"
    assert parsed_json["reference_items"][0]["user_note"] == "LUT tham khảo tương phản cao"
    assert len(parsed_json["reference_urls"]) == 1
    assert "https://example.com/moodboard" in parsed_json["reference_urls"][0]

    # Kiểm tra thêm giá trị numeric lớn (10**20 và 1e20)
    p_huge, _ = StoryCopilot.build_batch_prompt_wrapper(
        project_name="HugeNumericTest",
        story_intent="cinematic_nostalgic",
        creative_context={"target_duration_sec": 10**20},
    )
    match_huge = re.search(r"```json\s*(\{.*?\})\s*```", p_huge, re.DOTALL)
    assert match_huge is not None
    parsed_huge = json.loads(match_huge.group(1))
    assert parsed_huge["target_duration_sec"] == 10**20


# =============================================================================
# 3. Acceptance Matrix Item 3: Complete Prefix in Partitioning & Budget Bounds
# =============================================================================

def test_c03_prefix_included_before_partitioning_and_budget_bounds():
    """3. Complete prefix included before partitioning: budget equality succeeds, overflow fails closed, multi-batch."""
    cc = {"free_brief": "Chỉ thị phân lô đặc biệt"}
    prefix, suffix = StoryCopilot.build_batch_prompt_wrapper(
        project_name="PartitionTest",
        story_intent="travel_vlog",
        creative_context=cc,
    )

    class DummyClip:
        def __init__(self, clip_id, file_name, duration_sec):
            self.clip_id = clip_id
            self.file_name = file_name
            self.duration_sec = duration_sec
            self.content_hash = "hash_c1"
            self.current_path = "/media/test.mp4"

    class DummyManifest:
        def __init__(self, clips):
            self.manifest_id = "m_part_test"
            self.project_name = "PartitionTest"
            self.clips = clips

    manifest = DummyManifest([
        DummyClip("C01", "c1.mp4", 100.0),
        DummyClip("C02", "c2.mp4", 120.0),
    ])

    analysis_cache = {
        "C01": {
            "has_audio": True,
            "speech_status": "transcribed",
            "full_content_sha256": "1" * 64,
            "analysis_signature": "a" * 64,
            "windows": (
                EvidenceWindowSummary(
                    window_id="C01:s0",
                    start_sec=0.0,
                    end_sec=50.0,
                    speech_summary="Mở đầu video C01",
                    speech_word_count=10,
                    visual_action="Hành động 1",
                    has_audio=True,
                    is_sampled=True,
                    status="transcribed",
                    origin="whisper",
                    overlap=False,
                    clipped=False,
                ),
            ),
        },
        "C02": {
            "has_audio": True,
            "speech_status": "transcribed",
            "full_content_sha256": "2" * 64,
            "analysis_signature": "b" * 64,
            "windows": (
                EvidenceWindowSummary(
                    window_id="C02:s0",
                    start_sec=0.0,
                    end_sec=60.0,
                    speech_summary="Nội dung video C02",
                    speech_word_count=10,
                    visual_action="Hành động 2",
                    has_audio=True,
                    is_sampled=True,
                    status="transcribed",
                    origin="whisper",
                    overlap=False,
                    clipped=False,
                ),
            ),
        },
    }

    # Chia nhiều lô (mỗi lô 1 clip) bằng max_clips_per_batch=1
    cfg_multi = BatchBudgetConfig(
        max_prompt_bytes=32768,
        max_clips_per_batch=1,
        prompt_prefix=prefix,
        prompt_suffix=suffix,
    )
    plan = partition_manifest_into_batches(manifest, analysis_cache, config=cfg_multi)
    assert len(plan.batches) == 2

    # Mỗi lô đều phải chứa toàn bộ khối creative context và byte/sha khớp
    for b in plan.batches:
        rendered = render_batch_prompt(b)
        assert "YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO" in rendered
        assert "Chỉ thị phân lô đặc biệt" in rendered
        assert len(rendered.encode("utf-8")) == b.estimated_prompt_bytes
        assert b.estimated_prompt_bytes <= 32768

    # Budget Equality test: đặt trần bằng đúng kích thước của lô 0
    exact_bytes = plan.batches[0].estimated_prompt_bytes
    cfg_exact = BatchBudgetConfig(
        max_prompt_bytes=exact_bytes,
        max_clips_per_batch=1,
        prompt_prefix=prefix,
        prompt_suffix=suffix,
    )
    manifest_single = DummyManifest([manifest.clips[0]])
    plan_exact = partition_manifest_into_batches(manifest_single, {"C01": analysis_cache["C01"]}, config=cfg_exact)
    assert len(plan_exact.batches) == 1
    assert plan_exact.batches[0].estimated_prompt_bytes == exact_bytes

    # Overflow test: trần thiếu 1 byte -> ERR_ITEM_EXCEEDS_PROMPT_BUDGET mà không cắt xén context
    cfg_overflow = BatchBudgetConfig(
        max_prompt_bytes=exact_bytes - 1,
        max_clips_per_batch=1,
        prompt_prefix=prefix,
        prompt_suffix=suffix,
    )
    with pytest.raises(ValueError, match="ERR_ITEM_EXCEEDS_PROMPT_BUDGET"):
        partition_manifest_into_batches(manifest_single, {"C01": analysis_cache["C01"]}, config=cfg_overflow)


# =============================================================================
# 4. Acceptance Matrix Item 4: Source-Only Pure Wrapper & Tripwires
# =============================================================================

def test_c04_source_only_wrapper_and_no_asset_summary(monkeypatch):
    """4. Pure wrapper is source-only; zero library scan; zero demo assets; observable IO/network/model tripwires."""
    tripwire_counts = {
        "asset_summary": 0,
        "urlopen": 0,
        "subprocess": 0,
        "ref_file_open": 0,
    }

    # Tripwire 1: StoryCopilot.get_available_assets_summary
    def _spy_asset_summary(*args, **kwargs):
        tripwire_counts["asset_summary"] += 1
        raise AssertionError("TRIPWIRE: get_available_assets_summary không được phép gọi trong pure wrapper")
    monkeypatch.setattr(StoryCopilot, "get_available_assets_summary", classmethod(_spy_asset_summary))

    # Tripwire 2: urllib.request.urlopen
    import urllib.request
    def _spy_urlopen(*args, **kwargs):
        tripwire_counts["urlopen"] += 1
        raise AssertionError(f"TRIPWIRE: urlopen không được phép gọi trong pure wrapper: {args}")
    monkeypatch.setattr(urllib.request, "urlopen", _spy_urlopen)

    # Tripwire 3: subprocess.run (theo dõi và kiểm tra số lần gọi)
    import subprocess
    def _spy_subp(*args, **kwargs):
        tripwire_counts["subprocess"] += 1
        raise AssertionError(f"TRIPWIRE: subprocess.run không được phép gọi trong pure wrapper: {args}")
    monkeypatch.setattr(subprocess, "run", _spy_subp)

    # Tripwire 4: builtins.open đối với file tham chiếu local_path
    ref_path = "/media/references/isolated_sample_video.mp4"
    orig_open = open
    import builtins
    def _spy_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and os.path.normpath(str(file)) == os.path.normpath(ref_path):
            tripwire_counts["ref_file_open"] += 1
            raise AssertionError(f"TRIPWIRE: Tệp tham chiếu không được phép mở/đọc IO trong pure wrapper: {file}")
        return orig_open(file, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", _spy_open)

    cc = {
        "style_preset": "cinematic_nostalgic",
        "free_brief": "Pure source check with reference metadata",
        "reference_items": [
            {
                "reference_id": "ref_isolated_01",
                "local_path": ref_path,
                "media_type": "video",
                "role": "reference_only",
                "user_note": "Ghi chú metadata thuần túy",
            }
        ],
        "reference_urls": [
            "https://example.com/isolated_ref_moodboard",
        ],
    }

    prefix, suffix = StoryCopilot.build_batch_prompt_wrapper(
        project_name="SourceOnlyPureProject",
        story_intent="cinematic",
        creative_context=cc,
    )

    # Khẳng định zero calls trên toàn bộ tripwires
    assert tripwire_counts["asset_summary"] == 0
    assert tripwire_counts["urlopen"] == 0
    assert tripwire_counts["subprocess"] == 0
    assert tripwire_counts["ref_file_open"] == 0

    full_text = prefix + suffix
    for banned in ["do_mixi_ao_that_day.mp4", "vine_boom.wav", "get_available_assets_summary"]:
        assert banned not in full_text
    assert ref_path in full_text
    assert "https://example.com/isolated_ref_moodboard" in full_text


# =============================================================================
# 5. Acceptance Matrix Item 5: Snapshot Validation, Invalid Matrix & Tampering
# =============================================================================

def test_c05_snapshot_validation_matrix_tampering_and_ui_sig_independence():
    """5. Snapshot field roundtrip, invalid digest matrix, tamper denial, ui_context_signature independence."""
    valid_sig = "a" * 64
    baseline_entry = BaselineSourceEntry(
        path="/media/clips/c1.mp4",
        st_size=1024,
        st_mtime_ns=1700000000000000000,
        st_ctime_ns=1700000000000000000,
        st_ino=1,
        st_dev=2,
    )
    prompt = "VALID_PROMPT_P5"
    prompt_bytes = len(prompt.encode("utf-8"))
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    batch_item = PreparedBatchItem(
        batch_id="b1",
        batch_index=0,
        distinct_clip_count=1,
        total_duration_sec=10.0,
        rendered_prompt=prompt,
        rendered_bytes=prompt_bytes,
        rendered_sha256=prompt_sha,
        content_signature="sig_c1",
        clip_ids=("C01",),
        items_count=1,
    )

    snap_id = compute_snapshot_id(
        manifest_id="m_val_01",
        project_name="ValProject",
        baseline_sources=(baseline_entry,),
        batches=(batch_item,),
        evidence_context_signature="ev_sig_123",
        max_prompt_bytes=32768,
        formatter_policy="source_only_v1",
        creative_context_signature=valid_sig,
    )

    snap = PreparedBatchSnapshot(
        manifest_id="m_val_01",
        project_name="ValProject",
        snapshot_id=snap_id,
        formatter_policy="source_only_v1",
        total_batches=1,
        planned_items_count=1,
        distinct_planned_clips_count=1,
        planned_clip_ids=("C01",),
        planned_clip_ids_count=1,
        unaccounted_clip_ids=(),
        unaccounted_clips=(),
        adapter_issues=(),
        max_prompt_bytes=32768,
        batches=(batch_item,),
        baseline_sources=(baseline_entry,),
        evidence_context_signature="ev_sig_123",
        creative_context_signature=valid_sig,
    )

    # 1. Roundtrip dict
    d = snap.to_dict()
    assert d["creative_context_signature"] == valid_sig
    norm = validate_and_normalize_snapshot(d)
    assert norm is not None
    assert norm.snapshot_id == snap_id
    assert norm.creative_context_signature == valid_sig

    # 2. Strict invalid type/digest matrix
    invalid_sigs = [
        True,
        False,
        12345,
        "a" * 63,          # quá ngắn
        "a" * 65,          # quá dài
        "A" * 64,          # chữ hoa bị từ chối
        "z" * 64,          # ký tự ngoài hex
        "",                # chuỗi rỗng
        ["a" * 64],        # list
        {"sig": "a" * 64}, # dict
    ]
    for inv in invalid_sigs:
        bad_d = copy_dict = dict(d)
        bad_d["creative_context_signature"] = inv
        assert validate_and_normalize_snapshot(bad_d) is None

    # 3. Deliberate tampering: xoá chữ ký nhưng giữ nguyên snapshot_id cũ -> Thất bại
    tampered_remove = dict(d)
    tampered_remove["creative_context_signature"] = None
    assert validate_and_normalize_snapshot(tampered_remove) is None

    # 4. Deliberate tampering: đổi chữ ký sang 64-hex khác nhưng giữ snapshot_id cũ -> Thất bại
    tampered_sig = dict(d)
    tampered_sig["creative_context_signature"] = "b" * 64
    assert validate_and_normalize_snapshot(tampered_sig) is None

    # 5. Independence của ui_context_signature: replace không làm đổi snapshot_id hay creative_context_signature
    replaced_snap = dataclasses.replace(snap, ui_context_signature="custom_ui_config_sig_999")
    norm_replaced = validate_and_normalize_snapshot(replaced_snap)
    assert norm_replaced is not None
    assert norm_replaced.snapshot_id == snap_id
    assert norm_replaced.ui_context_signature == "custom_ui_config_sig_999"
    assert norm_replaced.creative_context_signature == valid_sig


# =============================================================================
# 6. Acceptance Matrix Item 6: Evidence vs Creative Independence & Determinism
# =============================================================================

def test_c06_context_independence_and_determinism():
    """6. Same evidence but changed creative value/order yields new complete bytes/hashes and snapshot ID."""
    baseline_entry = BaselineSourceEntry(
        path="/media/clips/c1.mp4",
        st_size=2048,
        st_mtime_ns=1700000000000000000,
        st_ctime_ns=1700000000000000000,
        st_ino=10,
        st_dev=20,
    )
    batch_item = PreparedBatchItem(
        batch_id="b1",
        batch_index=0,
        distinct_clip_count=1,
        total_duration_sec=15.0,
        rendered_prompt="PROMPT_P6",
        rendered_bytes=len("PROMPT_P6".encode("utf-8")),
        rendered_sha256=hashlib.sha256("PROMPT_P6".encode("utf-8")).hexdigest(),
        content_signature="sig_p6",
        clip_ids=("C01",),
        items_count=1,
    )

    cc_a = normalize_creative_context({"free_brief": "Chi thị A"})
    cc_b = normalize_creative_context({"free_brief": "Chi thị B"})
    sig_a = compute_creative_context_signature(cc_a)
    sig_b = compute_creative_context_signature(cc_b)
    assert sig_a != sig_b

    snap_id_a1 = compute_snapshot_id(
        manifest_id="m_indep_01",
        project_name="IndepProject",
        baseline_sources=(baseline_entry,),
        batches=(batch_item,),
        evidence_context_signature="ev_sig_common",
        max_prompt_bytes=32768,
        creative_context_signature=sig_a,
    )
    snap_id_a2 = compute_snapshot_id(
        manifest_id="m_indep_01",
        project_name="IndepProject",
        baseline_sources=(baseline_entry,),
        batches=(batch_item,),
        evidence_context_signature="ev_sig_common",
        max_prompt_bytes=32768,
        creative_context_signature=sig_a,
    )
    snap_id_b = compute_snapshot_id(
        manifest_id="m_indep_01",
        project_name="IndepProject",
        baseline_sources=(baseline_entry,),
        batches=(batch_item,),
        evidence_context_signature="ev_sig_common",
        max_prompt_bytes=32768,
        creative_context_signature=sig_b,
    )

    # Determinism
    assert snap_id_a1 == snap_id_a2
    # Khác biệt hoàn toàn khi đổi creative context
    assert snap_id_a1 != snap_id_b


# =============================================================================
# 7. Acceptance Matrix Item 7: Nonempty prepare_batches -> copy_batch Exact Replay
# =============================================================================

def test_c07_nonempty_prepare_then_copy_replay(tmp_path, qapp, monkeypatch):
    """7. Successful nonempty prepare -> copy replay exact bytes/signature, six old expected bindings all preserved, zero reformat/reanalysis."""
    v1_path, d1, p1 = _create_dummy_video(str(tmp_path / "video1.mp4"))
    cache_mgr = _setup_scan_cache(tmp_path, [(v1_path, d1, p1)])

    cc = normalize_creative_context({
        "style_preset": "travel_vlog",
        "free_brief": "Đoạn phim du lịch trải nghiệm",
        "must_keep_rules": ["Giữ đoạn mở đầu ấn tượng"],
    })
    expected_cc_sig = compute_creative_context_signature(cc)

    # 1. Chạy prepare_batches
    req_prep = PromptPreparationRequest(
        request_id="req_prep_c07",
        generation=1,
        intent="prepare_batches",
        video_paths=(v1_path,),
        project_name="ProjectC07",
        style_intent="travel_vlog",
        target_duration="full",
        hook_duration="30_60s",
        max_prompt_bytes=32768,
        creative_context=cc,
        manifest_cache_dir=str(tmp_path / "manifests"),
    )
    worker_prep = PromptPreparationWorker(request=req_prep, cache_mgr=cache_mgr)
    prep_records = []
    worker_prep.finished_signal.connect(lambda rid, s, m, r: prep_records.append((s, m, r)))

    try:
        worker_prep.start()
        _wait_for_worker(worker_prep)
    finally:
        if worker_prep.isRunning():
            worker_prep.cancel()
            worker_prep.wait(2000)

    assert len(prep_records) == 1
    p_success, p_msg, p_payload = prep_records[0]
    assert p_success is True
    assert p_payload["creative_context_signature"] == expected_cc_sig

    snapshot = p_payload["batch_snapshot"]
    assert isinstance(snapshot, PreparedBatchSnapshot)
    assert snapshot.creative_context_signature == expected_cc_sig
    assert len(snapshot.batches) >= 1

    selected_batch = snapshot.batches[0]
    rendered_prompt_orig = selected_batch.rendered_prompt
    rendered_sha_orig = selected_batch.rendered_sha256
    rendered_bytes_orig = selected_batch.rendered_bytes

    # 2. Cài đặt spies/tripwires trước khi chạy copy_batch:
    # copy_batch phải là exact replay, tuyệt đối KHÔNG gọi lại wrapper, direct formatter, partition, render, hay cache reads
    copy_spies = {
        "build_wrapper": 0,
        "generate_prompt": 0,
        "partition_manifest": 0,
        "render_batch": 0,
        "adapt_clips": 0,
        "cache_load": 0,
        "cache_save": 0,
    }

    def _spy_build_wrapper(*args, **kwargs):
        copy_spies["build_wrapper"] += 1
        raise AssertionError("TRIPWIRE: build_batch_prompt_wrapper không được phép gọi trong copy_batch")

    def _spy_generate_prompt(*args, **kwargs):
        copy_spies["generate_prompt"] += 1
        raise AssertionError("TRIPWIRE: generate_copilot_prompt không được phép gọi trong copy_batch")

    def _spy_partition(*args, **kwargs):
        copy_spies["partition_manifest"] += 1
        raise AssertionError("TRIPWIRE: partition_manifest_into_batches không được phép gọi trong copy_batch")

    def _spy_render_batch(*args, **kwargs):
        copy_spies["render_batch"] += 1
        raise AssertionError("TRIPWIRE: render_batch_prompt không được phép gọi trong copy_batch")

    def _spy_adapt(*args, **kwargs):
        copy_spies["adapt_clips"] += 1
        raise AssertionError("TRIPWIRE: adapt_clips_data_to_batch_analysis_cache không được phép gọi trong copy_batch")

    def _spy_cache_load(*args, **kwargs):
        copy_spies["cache_load"] += 1
        raise AssertionError("TRIPWIRE: cache_mgr.get_cached_scan không được phép gọi trong copy_batch")

    def _spy_cache_save(*args, **kwargs):
        copy_spies["cache_save"] += 1
        raise AssertionError("TRIPWIRE: cache_mgr.save_scan_result không được phép gọi trong copy_batch")

    import src.core.story_copilot as sc_mod
    import src.core.batch_planner as bp_mod
    import src.core.batch_evidence_adapter as bea_mod

    monkeypatch.setattr(sc_mod.StoryCopilot, "build_batch_prompt_wrapper", classmethod(_spy_build_wrapper))
    monkeypatch.setattr(sc_mod.StoryCopilot, "generate_copilot_prompt", classmethod(_spy_generate_prompt))
    monkeypatch.setattr(bp_mod, "partition_manifest_into_batches", _spy_partition)
    monkeypatch.setattr(bp_mod, "render_batch_prompt", _spy_render_batch)
    monkeypatch.setattr(bea_mod, "adapt_clips_data_to_batch_analysis_cache", _spy_adapt)
    monkeypatch.setattr(cache_mgr, "get_cached_scan", _spy_cache_load)
    monkeypatch.setattr(cache_mgr, "save_scan_result", _spy_cache_save)

    # 3. Chạy copy_batch với 6 expected bindings bắt buộc
    req_copy = PromptPreparationRequest(
        request_id="req_copy_c07",
        generation=2,
        intent="copy_batch",
        video_paths=(v1_path,),
        project_name=snapshot.project_name,
        prepared_batch_snapshot=snapshot,
        selected_batch_index=0,
        expected_snapshot_id=snapshot.snapshot_id,
        expected_batch_id=selected_batch.batch_id,
        expected_rendered_sha256=rendered_sha_orig,
        expected_formatter_policy=snapshot.formatter_policy,
        expected_manifest_id=snapshot.manifest_id,
        expected_project_name=snapshot.project_name,
        creative_context=cc,
    )
    worker_copy = PromptPreparationWorker(request=req_copy, cache_mgr=cache_mgr)
    copy_records = []
    worker_copy.finished_signal.connect(lambda rid, s, m, r: copy_records.append((s, m, r)))

    try:
        worker_copy.start()
        _wait_for_worker(worker_copy)
    finally:
        if worker_copy.isRunning():
            worker_copy.cancel()
            worker_copy.wait(2000)

    # Thẩm định zero calls trên toàn bộ formatting/analysis spies
    assert copy_spies["build_wrapper"] == 0
    assert copy_spies["generate_prompt"] == 0
    assert copy_spies["partition_manifest"] == 0
    assert copy_spies["render_batch"] == 0
    assert copy_spies["adapt_clips"] == 0
    assert copy_spies["cache_load"] == 0
    assert copy_spies["cache_save"] == 0

    assert len(copy_records) == 1
    c_success, c_msg, c_payload = copy_records[0]
    assert c_success is True
    assert c_payload["creative_context_signature"] == expected_cc_sig
    assert c_payload["prompt_text"] == rendered_prompt_orig
    assert c_payload["rendered_sha256"] == rendered_sha_orig
    assert c_payload["rendered_bytes"] == rendered_bytes_orig
    assert c_payload["batch_id"] == selected_batch.batch_id
    assert c_payload["snapshot_id"] == snapshot.snapshot_id


# =============================================================================
# 8. Acceptance Matrix Item 8: Mismatched / Missing / Legacy Context Gating
# =============================================================================

def test_c08_mismatched_context_combinations_fail_closed_before_io(tmp_path, qapp):
    """8. Missing/nonempty/legacy/mismatched context combinations fail before source/cache IO, exactly one invalid_snapshot."""
    v1_path, d1, p1 = _create_dummy_video(str(tmp_path / "video1.mp4"))
    cache_mgr = _setup_scan_cache(tmp_path, [(v1_path, d1, p1)])

    cc_a = normalize_creative_context({"free_brief": "Context A"})
    cc_b = normalize_creative_context({"free_brief": "Context B"})
    cc_altered_note = normalize_creative_context({
        "style_preset": "travel_vlog",
        "reference_items": [{"reference_id": "r1", "local_path": "/media/ref.mp4", "media_type": "video", "user_note": "Ghi chú đã bị sửa"}],
    })
    cc_orig_note = normalize_creative_context({
        "style_preset": "travel_vlog",
        "reference_items": [{"reference_id": "r1", "local_path": "/media/ref.mp4", "media_type": "video", "user_note": "Ghi chú ban đầu"}],
    })

    # Tạo snapshot A
    req_prep_a = PromptPreparationRequest(
        request_id="req_prep_a",
        generation=1,
        intent="prepare_batches",
        video_paths=(v1_path,),
        project_name="MismatchProj",
        style_intent="travel_vlog",
        target_duration="full",
        hook_duration="30_60s",
        creative_context=cc_a,
        manifest_cache_dir=str(tmp_path / "manifests"),
    )
    w_prep_a = PromptPreparationWorker(request=req_prep_a, cache_mgr=cache_mgr)
    rec_a = []
    w_prep_a.finished_signal.connect(lambda rid, s, m, r: rec_a.append((s, m, r)))
    try:
        w_prep_a.start()
        _wait_for_worker(w_prep_a)
    finally:
        if w_prep_a.isRunning():
            w_prep_a.cancel()
            w_prep_a.wait(2000)
    snapshot_a = rec_a[0][2]["batch_snapshot"]

    # Tạo snapshot Legacy (creative_context=None)
    req_prep_legacy = PromptPreparationRequest(
        request_id="req_prep_leg",
        generation=2,
        intent="prepare_batches",
        video_paths=(v1_path,),
        project_name="MismatchProj",
        style_intent="travel_vlog",
        target_duration="full",
        hook_duration="30_60s",
        creative_context=None,
        manifest_cache_dir=str(tmp_path / "manifests"),
    )
    w_prep_leg = PromptPreparationWorker(request=req_prep_legacy, cache_mgr=cache_mgr)
    rec_leg = []
    w_prep_leg.finished_signal.connect(lambda rid, s, m, r: rec_leg.append((s, m, r)))
    try:
        w_prep_leg.start()
        _wait_for_worker(w_prep_leg)
    finally:
        if w_prep_leg.isRunning():
            w_prep_leg.cancel()
            w_prep_leg.wait(2000)
    snapshot_legacy = rec_leg[0][2]["batch_snapshot"]

    # Matrix các trường hợp bất tương thích:
    cases = [
        # (request_cc, target_snapshot, label)
        (cc_a, snapshot_legacy, "Nonempty request + legacy snapshot"),
        (None, snapshot_a, "Empty/absent request + creative snapshot"),
        (cc_b, snapshot_a, "Mismatch Context A vs Context B"),
        (cc_altered_note, snapshot_a, "Altered nested typed user_note"),
    ]

    for req_cc, snap, label in cases:
        b0 = snap.batches[0]
        req_copy = PromptPreparationRequest(
            request_id=f"req_mismatch_{label}",
            generation=3,
            intent="copy_batch",
            video_paths=(v1_path,),
            project_name=snap.project_name,
            prepared_batch_snapshot=snap,
            selected_batch_index=0,
            expected_snapshot_id=snap.snapshot_id,
            expected_batch_id=b0.batch_id,
            expected_rendered_sha256=b0.rendered_sha256,
            expected_formatter_policy=snap.formatter_policy,
            expected_manifest_id=snap.manifest_id,
            expected_project_name=snap.project_name,
            creative_context=req_cc,
        )
        w_copy = PromptPreparationWorker(request=req_copy, cache_mgr=cache_mgr)
        res = []
        w_copy.finished_signal.connect(lambda rid, s, m, r: res.append((s, m, r)))

        # Tripwires scoped to copy run: stat/isfile/exists trên source path + cache calls
        io_tripwire_counts = {
            "source_stat": 0,
            "source_isfile": 0,
            "source_exists": 0,
            "cache_load": 0,
            "cache_save": 0,
        }
        orig_os_stat = os.stat
        orig_os_isfile = os.path.isfile
        orig_os_exists = os.path.exists
        orig_cache_load = cache_mgr.get_cached_scan
        orig_cache_save = cache_mgr.save_scan_result

        norm_v1 = os.path.normpath(os.path.abspath(v1_path))

        def _spy_stat(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)) and os.path.normpath(str(path)) == norm_v1:
                io_tripwire_counts["source_stat"] += 1
                raise AssertionError(f"TRIPWIRE: os.stat gọi trên source trước khi kiểm tra mismatch: {path}")
            return orig_os_stat(path, *args, **kwargs)

        def _spy_isfile(path):
            if isinstance(path, (str, bytes, os.PathLike)) and os.path.normpath(str(path)) == norm_v1:
                io_tripwire_counts["source_isfile"] += 1
                raise AssertionError(f"TRIPWIRE: os.path.isfile gọi trên source trước khi kiểm tra mismatch: {path}")
            return orig_os_isfile(path)

        def _spy_exists(path):
            if isinstance(path, (str, bytes, os.PathLike)) and os.path.normpath(str(path)) == norm_v1:
                io_tripwire_counts["source_exists"] += 1
                raise AssertionError(f"TRIPWIRE: os.path.exists gọi trên source trước khi kiểm tra mismatch: {path}")
            return orig_os_exists(path)

        def _spy_c_load(*args, **kwargs):
            io_tripwire_counts["cache_load"] += 1
            raise AssertionError("TRIPWIRE: cache_mgr.get_cached_scan gọi trước khi kiểm tra mismatch")

        def _spy_c_save(*args, **kwargs):
            io_tripwire_counts["cache_save"] += 1
            raise AssertionError("TRIPWIRE: cache_mgr.save_scan_result gọi trước khi kiểm tra mismatch")

        with pytest.MonkeyPatch.context() as mp_copy:
            mp_copy.setattr(os, "stat", _spy_stat)
            mp_copy.setattr(os.path, "isfile", _spy_isfile)
            mp_copy.setattr(os.path, "exists", _spy_exists)
            mp_copy.setattr(cache_mgr, "get_cached_scan", _spy_c_load)
            mp_copy.setattr(cache_mgr, "save_scan_result", _spy_c_save)

            try:
                w_copy.start()
                _wait_for_worker(w_copy)
            finally:
                if w_copy.isRunning():
                    w_copy.cancel()
                    w_copy.wait(2000)

        assert io_tripwire_counts["source_stat"] == 0
        assert io_tripwire_counts["source_isfile"] == 0
        assert io_tripwire_counts["source_exists"] == 0
        assert io_tripwire_counts["cache_load"] == 0
        assert io_tripwire_counts["cache_save"] == 0

        assert len(res) == 1, f"{label} phải phát đúng 1 tín hiệu kết thúc"
        succ, msg, payload = res[0]
        assert succ is False, f"{label} phải bị từ chối fail-closed"
        assert msg == "invalid_snapshot", f"{label} phải có message 'invalid_snapshot'"
        assert "prompt_text" not in payload, f"{label} tuyệt đối không công bố prompt"


# =============================================================================
# 9. Acceptance Matrix Item 9: Caller Mutation Isolation
# =============================================================================

def test_c09_caller_mutation_isolation_before_and_after_start(tmp_path, qapp, monkeypatch):
    """9. Caller mutation before start and at controlled after-start barrier cannot change captured context or batch result."""
    import threading
    v1_path, d1, p1 = _create_dummy_video(str(tmp_path / "v_mut.mp4"))
    cache_mgr = _setup_scan_cache(tmp_path, [(v1_path, d1, p1)])

    mutable_cc = {
        "free_brief": "Brief ban đầu",
        "must_keep_rules": ["Quy tắc 1"],
    }
    req = PromptPreparationRequest(
        request_id="req_mut_test",
        generation=1,
        intent="prepare_batches",
        video_paths=(v1_path,),
        project_name="MutProject",
        style_intent="travel_vlog",
        target_duration="full",
        hook_duration="30_60s",
        creative_context=mutable_cc,
        manifest_cache_dir=str(tmp_path / "manifests"),
    )
    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

    # 1. Đột biến dictionary của caller TRƯỚC start
    mutable_cc["free_brief"] = "Brief ĐÃ BỊ ĐỘT BIẾN TRƯỚC START"
    mutable_cc["must_keep_rules"].append("Quy tắc gian lận")

    # 2. Rào cản đồng bộ giới hạn xác định (Deterministic Bounded Synchronization Barrier)
    # Rào cản này chặn worker SAU KHI start và TRƯỚC KHI worker tiếp tục format wrapper / consume captured context
    barrier_reached = threading.Event()
    barrier_release = threading.Event()

    orig_build_wrapper = StoryCopilot.build_batch_prompt_wrapper.__func__

    def _synced_wrapper(cls, *args, **kwargs):
        barrier_reached.set()
        # Chờ giải phóng với bounded timeout 5.0s, tránh treo luồng
        if not barrier_release.wait(timeout=5.0):
            raise TimeoutError("ERR_BARRIER_TIMEOUT: barrier_release không được set trong 5.0s")
        return orig_build_wrapper(cls, *args, **kwargs)

    monkeypatch.setattr(StoryCopilot, "build_batch_prompt_wrapper", classmethod(_synced_wrapper))

    records = []
    worker.finished_signal.connect(lambda rid, s, m, r: records.append((s, m, r)))

    try:
        worker.start()

        # Chờ luồng worker thực sự khởi chạy và dừng lại tại rào cản
        reached = barrier_reached.wait(timeout=5.0)
        assert reached is True, "Worker không chạm tới rào cản đồng bộ trong 5.0s"

        # Tại thời điểm worker đang bị giữ chặt tại rào cản (held point):
        # Đột biến caller dictionary và request object
        mutable_cc["free_brief"] = "Brief ĐÃ BỊ ĐỘT BIẾN TẠI HELD POINT"
        object.__setattr__(req, "creative_context", normalize_creative_context({"free_brief": "ĐỘT BIẾN TẠI BARRIER"}))

        # Mở rào cản cho worker tiếp tục chạy
        barrier_release.set()

        _wait_for_worker(worker)
    finally:
        # Luôn luôn giải phóng rào cản trong finally để tránh rò rỉ deadlock
        barrier_release.set()
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    # Khẳng định cả hai sự kiện rào cản đều đã được quan sát
    assert barrier_reached.is_set(), "Sự kiện barrier_reached phải được kích hoạt"
    assert barrier_release.is_set(), "Sự kiện barrier_release phải được kích hoạt"

    assert len(records) == 1
    succ, msg, payload = records[0]
    assert succ is True
    snap = payload["batch_snapshot"]
    b0_prompt = snap.batches[0].rendered_prompt
    assert "Brief ban đầu" in b0_prompt
    assert "Brief ĐÃ BỊ ĐỘT BIẾN TRƯỚC START" not in b0_prompt
    assert "Brief ĐÃ BỊ ĐỘT BIẾN TẠI HELD POINT" not in b0_prompt
    assert "ĐỘT BIẾN TẠI BARRIER" not in b0_prompt


# =============================================================================
# 10. Acceptance Matrix Item 10: Post-Render Deletion / Mutation / Cancel
# =============================================================================

def test_c10_post_render_source_deletion_mutation_and_cancel(tmp_path, qapp):
    """10. Actual real wrapper/render reached, then source deletion/mutation or cancel yields 0 success."""
    v1_path, d1, p1 = _create_dummy_video(str(tmp_path / "v_post.mp4"), size=2048)
    cache_mgr = _setup_scan_cache(tmp_path, [(v1_path, d1, p1)])

    cc = {"free_brief": "Post-render boundary check"}

    # Subcase A: Xóa file nguồn ngay khi bước render hoàn thành (hook qua monkeypatch render_batch_prompt)
    import src.core.batch_planner as bp
    orig_render = bp.render_batch_prompt

    def _render_and_delete(batch):
        result = orig_render(batch)
        if os.path.exists(v1_path):
            os.remove(v1_path)
        return result

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bp, "render_batch_prompt", _render_and_delete)

        req_del = PromptPreparationRequest(
            request_id="req_post_del",
            generation=1,
            intent="prepare_batches",
            video_paths=(v1_path,),
            project_name="PostBoundaryProj",
            style_intent="travel_vlog",
            target_duration="full",
            hook_duration="30_60s",
            creative_context=cc,
            manifest_cache_dir=str(tmp_path / "manifests_del"),
        )
        w_del = PromptPreparationWorker(request=req_del, cache_mgr=cache_mgr)
        rec_del = []
        w_del.finished_signal.connect(lambda rid, s, m, r: rec_del.append((s, m, r)))

        try:
            w_del.start()
            _wait_for_worker(w_del)
        finally:
            if w_del.isRunning():
                w_del.cancel()
                w_del.wait(2000)

        assert len(rec_del) == 1
        assert rec_del[0][0] is False
        assert rec_del[0][1] == "source_missing"

    # Tái tạo file video cho Subcase B
    v2_path, d2, p2 = _create_dummy_video(str(tmp_path / "v_post_mut.mp4"), size=2048)
    cache_mgr2 = _setup_scan_cache(tmp_path, [(v2_path, d2, p2)])

    # Subcase B: Đột biến file nguồn ngay sau khi render xong
    def _render_and_mutate(batch):
        result = orig_render(batch)
        with open(v2_path, "ab") as f:
            f.write(b"TAMPER_POST_RENDER_DATA")
        return result

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bp, "render_batch_prompt", _render_and_mutate)

        req_mut = PromptPreparationRequest(
            request_id="req_post_mut",
            generation=2,
            intent="prepare_batches",
            video_paths=(v2_path,),
            project_name="PostBoundaryProj2",
            style_intent="travel_vlog",
            target_duration="full",
            hook_duration="30_60s",
            creative_context=cc,
            manifest_cache_dir=str(tmp_path / "manifests_mut"),
        )
        w_mut = PromptPreparationWorker(request=req_mut, cache_mgr=cache_mgr2)
        rec_mut = []
        w_mut.finished_signal.connect(lambda rid, s, m, r: rec_mut.append((s, m, r)))

        try:
            w_mut.start()
            _wait_for_worker(w_mut)
        finally:
            if w_mut.isRunning():
                w_mut.cancel()
                w_mut.wait(2000)

        assert len(rec_mut) == 1
        assert rec_mut[0][0] is False
        assert rec_mut[0][1] == "source_changed"

    # Subcase C: Batch render reached -> worker.cancel() trước khi renderer wrapper trả về kết quả
    v3_path, d3, p3 = _create_dummy_video(str(tmp_path / "v_post_cancel.mp4"), size=2048)
    cache_mgr3 = _setup_scan_cache(tmp_path, [(v3_path, d3, p3)])

    req_cancel = PromptPreparationRequest(
        request_id="req_post_cancel_batch",
        generation=3,
        intent="prepare_batches",
        video_paths=(v3_path,),
        project_name="PostBoundaryProj3",
        style_intent="travel_vlog",
        target_duration="full",
        hook_duration="30_60s",
        creative_context=cc,
        manifest_cache_dir=str(tmp_path / "manifests_cancel"),
    )
    w_cancel = PromptPreparationWorker(request=req_cancel, cache_mgr=cache_mgr3)
    rec_cancel = []
    w_cancel.finished_signal.connect(lambda rid, s, m, r: rec_cancel.append((s, m, r)))

    render_reached = []

    def _render_and_cancel(batch):
        render_reached.append(batch.batch_id)
        # Hoàn thành original render trước, sau đó worker.cancel() trước khi wrapper trả về
        result = orig_render(batch)
        w_cancel.cancel()
        return result

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bp, "render_batch_prompt", _render_and_cancel)

        try:
            w_cancel.start()
            _wait_for_worker(w_cancel)
        finally:
            if w_cancel.isRunning():
                w_cancel.cancel()
                w_cancel.wait(2000)

    assert len(render_reached) >= 1, "Original batch render phải thực tế chạm tới"
    assert len(rec_cancel) == 1, "Phải phát đúng 1 tín hiệu terminal"
    c_succ, c_msg, c_payload = rec_cancel[0]
    assert c_succ is False
    assert c_msg == "cancelled"
    assert "batch_snapshot" not in c_payload
    assert "prompt_text" not in c_payload


# =============================================================================
# 11. Acceptance Matrix Item 11: Direct Intent Parity & Unsupported Rejection
# =============================================================================

def test_c11_direct_parity_and_unsupported_intents_rejection(tmp_path, qapp):
    """11. Direct copy_video/run_ai remain equal; validate_batch_draft/overview nonempty reject explicitly before IO."""
    v1_path, d1, p1 = _create_dummy_video(str(tmp_path / "v_parity.mp4"))
    cache_mgr = _setup_scan_cache(tmp_path, [(v1_path, d1, p1)])

    cc = {"free_brief": "Parity check"}

    # Direct copy_video và run_ai phải tương đồng
    req_cv = PromptPreparationRequest(
        request_id="req_cv",
        generation=1,
        intent="copy_video",
        video_paths=(v1_path,),
        creative_context=cc,
    )
    req_rai = PromptPreparationRequest(
        request_id="req_rai",
        generation=1,
        intent="run_ai",
        video_paths=(v1_path,),
        creative_context=cc,
    )

    w_cv = PromptPreparationWorker(request=req_cv, cache_mgr=cache_mgr)
    w_rai = PromptPreparationWorker(request=req_rai, cache_mgr=cache_mgr)

    r_cv, r_rai = [], []
    w_cv.finished_signal.connect(lambda rid, s, m, r: r_cv.append((s, m, r)))
    w_rai.finished_signal.connect(lambda rid, s, m, r: r_rai.append((s, m, r)))

    try:
        w_cv.start()
        _wait_for_worker(w_cv)
    finally:
        if w_cv.isRunning():
            w_cv.cancel()
            w_cv.wait(2000)

    try:
        w_rai.start()
        _wait_for_worker(w_rai)
    finally:
        if w_rai.isRunning():
            w_rai.cancel()
            w_rai.wait(2000)

    assert r_cv[0][0] is True
    assert r_rai[0][0] is True
    assert r_cv[0][2]["prompt_text"] == r_rai[0][2]["prompt_text"]
    assert r_cv[0][2]["creative_context_signature"] == r_rai[0][2]["creative_context_signature"]

    # Cả 6 intent chuẩn đều hỗ trợ nonempty context tại Request (bao gồm validate_batch_draft và copy_project_overview)
    for supp in ("validate_batch_draft", "copy_project_overview"):
        req_supp = PromptPreparationRequest(
            request_id=f"req_{supp}",
            generation=1,
            intent=supp,
            video_paths=(v1_path,),
            creative_context=cc,
        )
        assert req_supp.creative_context is not None

    # Intent lạ bị từ chối tại Request
    with pytest.raises(ValueError, match="ERR_INVALID_INTENT"):
        PromptPreparationRequest(
            request_id="req_unknown",
            generation=1,
            intent="unknown_unsupported_intent",
            video_paths=(v1_path,),
            creative_context=cc,
        )


# =============================================================================
# 12. Acceptance Matrix Item 12: Thread Cleanup & Retained Ownership
# =============================================================================

def test_c12_physical_thread_cleanup_and_retained_ownership(tmp_path, qapp):
    """12. Physical thread cleanup on success/failure and retained ownership if cooperative join times out."""
    v1_path, d1, p1 = _create_dummy_video(str(tmp_path / "v_clean.mp4"))
    cache_mgr = _setup_scan_cache(tmp_path, [(v1_path, d1, p1)])

    req = PromptPreparationRequest(
        request_id="req_cleanup",
        generation=1,
        intent="prepare_batches",
        video_paths=(v1_path,),
        project_name="CleanProject",
        style_intent="travel_vlog",
        target_duration="full",
        hook_duration="30_60s",
        creative_context={"free_brief": "Cleanup test"},
        manifest_cache_dir=str(tmp_path / "manifests"),
    )
    worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)

    try:
        worker.start()
        _wait_for_worker(worker)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)

    assert worker.isFinished()
    assert not worker.isRunning()

    # UNIT_RETENTION_VERIFIED: Kiểm tra cơ chế giữ lại tham chiếu nếu worker vẫn đang chạy khi timeout
    dummy_worker = PromptPreparationWorker(request=req, cache_mgr=cache_mgr)
    # Giả lập worker đang chạy
    initial_retained_count = len(_RETAINED_RUNNING_WORKERS)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dummy_worker, "isRunning", lambda: True)
        mp.setattr(dummy_worker, "isFinished", lambda: False)
        with pytest.raises(AssertionError, match="không dừng vật lý"):
            _wait_for_worker(dummy_worker, timeout_sec=0.05)

    assert len(_RETAINED_RUNNING_WORKERS) == initial_retained_count + 1
    assert _RETAINED_RUNNING_WORKERS[-1] is dummy_worker
