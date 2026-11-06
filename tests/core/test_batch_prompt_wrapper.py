"""
tests/core/test_batch_prompt_wrapper.py
=============================================================================
Finite TDD Unit Tests cho StoryCopilot.build_batch_prompt_wrapper (G06-06A3-I):
1. Pure stdlib: 0 IO, 0 network, 0 model, 0 demo assets scan.
2. Source-only: Tuyệt đối không gọi get_available_assets_summary, không demo filenames.
3. Input validation: Bắt buộc project_name nonblank str, story_intent/duration str.
4. Byte Authority: Tích hợp trực tiếp với BatchBudgetConfig và render_batch_prompt.
5. Strict Cap Enforcement: Toàn bộ chuỗi kết xuất <= max_prompt_bytes (32768).
=============================================================================
"""

import pytest
from types import MappingProxyType

from src.core.story_copilot import StoryCopilot
from src.core.batch_planner import (
    BatchBudgetConfig,
    EvidenceWindowSummary,
    partition_manifest_into_batches,
    render_batch_prompt,
    estimate_batch_prompt_bytes,
)


class DummyClip:
    def __init__(self, clip_id: str, file_name: str, duration_sec: float, content_hash: str = "hash123", current_path: str = "/media/test.mp4"):
        self.clip_id = clip_id
        self.file_name = file_name
        self.duration_sec = duration_sec
        self.content_hash = content_hash
        self.current_path = current_path


class DummyManifest:
    def __init__(self, manifest_id: str = "m_test_01", project_name: str = "Project_Test", clips=None):
        self.manifest_id = manifest_id
        self.project_name = project_name
        self.clips = clips or []


def test_build_batch_prompt_wrapper_returns_tuple_of_strings():
    prefix, suffix = StoryCopilot.build_batch_prompt_wrapper(
        project_name="DuLichDaLat",
        story_intent="travel_vlog",
        target_duration="full",
        hook_duration="30_60s",
    )
    assert isinstance(prefix, str)
    assert isinstance(suffix, str)
    assert len(prefix) > 0
    assert len(suffix) > 0
    assert "DuLichDaLat" in prefix
    assert "TRAVEL_VLOG" in prefix


def test_build_batch_prompt_wrapper_is_source_only_no_demo_assets():
    prefix, suffix = StoryCopilot.build_batch_prompt_wrapper(
        project_name="SourceOnlyTest",
        story_intent="cinematic",
    )
    full_wrapper = prefix + suffix
    # Source-only: tuyệt đối không chứa tên file asset demo hay hàm get_available_assets_summary
    for banned in ["do_mixi_ao_that_day.mp4", "vine_boom.wav", "meme", "sfx_inserts", "broll_inserts"]:
        # Lưu ý: sfx/meme template của V1 không được xuất hiện như danh sách demo asset
        assert "do_mixi" not in full_wrapper
        assert "vine_boom" not in full_wrapper


def test_build_batch_prompt_wrapper_input_validation():
    # project_name rỗng hoặc sai kiểu
    with pytest.raises(ValueError, match="ERR_INVALID_INPUT"):
        StoryCopilot.build_batch_prompt_wrapper(project_name="")

    with pytest.raises(ValueError, match="ERR_INVALID_INPUT"):
        StoryCopilot.build_batch_prompt_wrapper(project_name=123)

    # story_intent sai kiểu
    with pytest.raises(ValueError, match="ERR_INVALID_INPUT"):
        StoryCopilot.build_batch_prompt_wrapper(project_name="Valid", story_intent=None)


def test_build_batch_prompt_wrapper_integrated_with_a1_planner_and_exact_byte_cap():
    prefix, suffix = StoryCopilot.build_batch_prompt_wrapper(
        project_name="IntegrationTest",
        story_intent="travel_vlog",
    )

    manifest = DummyManifest(
        manifest_id="m_integration",
        project_name="IntegrationTest",
        clips=[
            DummyClip("C01", "clip1.mp4", 120.0),
            DummyClip("C02", "clip2.mp4", 150.0),
        ],
    )

    analysis_cache = {
        "C01": {
            "has_audio": True,
            "speech_status": "transcribed",
            "full_content_sha256": "f" * 64,
            "analysis_signature": "a" * 64,
            "windows": (
                EvidenceWindowSummary(
                    window_id="C01:s0",
                    start_sec=10.0,
                    end_sec=30.0,
                    speech_summary="Xin chào các bạn đây là Đà Lạt buổi sớm mai.",
                    speech_word_count=12,
                    visual_action="Cảnh sương mù bay lượn trên thung lũng",
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
            "has_audio": False,
            "speech_status": "no_audio_track",
            "full_content_sha256": "e" * 64,
            "analysis_signature": "b" * 64,
            "windows": (
                EvidenceWindowSummary(
                    window_id="C02:v0",
                    start_sec=0.0,
                    end_sec=25.0,
                    speech_summary="",
                    speech_word_count=0,
                    visual_action="Người đi bộ ngắm hoa anh đào nở rộ",
                    has_audio=False,
                    is_sampled=True,
                    status="described",
                    origin="vision",
                    overlap=False,
                    clipped=False,
                ),
            ),
        },
    }

    config = BatchBudgetConfig(
        max_prompt_bytes=32768,
        prompt_prefix=prefix,
        prompt_suffix=suffix,
    )

    plan = partition_manifest_into_batches(manifest, analysis_cache, config=config)
    assert len(plan.batches) >= 1

    for batch in plan.batches:
        rendered = render_batch_prompt(batch)
        exact_bytes = len(rendered.encode("utf-8"))
        assert exact_bytes == batch.estimated_prompt_bytes
        assert exact_bytes <= 32768
        assert prefix in rendered
        assert suffix in rendered
        assert batch.manifest_id == "m_integration"


def test_build_batch_prompt_wrapper_oversized_wrapper_triggers_budget_error():
    # Tạo prefix giả siêu dài để vượt ngân sách 32,768 bytes
    huge_prefix = "A" * 32500
    suffix = "\nEND\n"

    manifest = DummyManifest(
        manifest_id="m_huge",
        project_name="HugeTest",
        clips=[DummyClip("C01", "clip1.mp4", 60.0)],
    )

    analysis_cache = {
        "C01": {
            "has_audio": True,
            "speech_status": "transcribed",
            "full_content_sha256": "f" * 64,
            "analysis_signature": "a" * 64,
            "windows": (
                EvidenceWindowSummary(
                    window_id="C01:s0",
                    start_sec=0.0,
                    end_sec=30.0,
                    speech_summary="Nội dung dài dằng dặc " * 50,
                    speech_word_count=200,
                    visual_action="Mô tả",
                    has_audio=True,
                    is_sampled=True,
                    status="transcribed",
                    origin="whisper",
                    overlap=False,
                    clipped=False,
                ),
            ),
        }
    }

    config = BatchBudgetConfig(
        max_prompt_bytes=32768,
        prompt_prefix=huge_prefix,
        prompt_suffix=suffix,
    )

    with pytest.raises(ValueError, match="ERR_ITEM_EXCEEDS_PROMPT_BUDGET"):
        partition_manifest_into_batches(manifest, analysis_cache, config=config)
