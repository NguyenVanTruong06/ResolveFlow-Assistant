import pytest
from pathlib import Path
from src.core.story_copilot import StoryCopilot, CopilotDirectorPlan
from src.core.source_resolver import SourceResolver, SourceAuditResult


@pytest.fixture
def sample_plan():
    json_text = """{
  "strategy_summary": "Test plan",
  "timeline_segments": [
    {"clip_index": 54, "clip_name": "DJI_20260830113907_0076_D.MP4", "start_sec": 5.0, "end_sec": 20.0},
    {"clip_index": 56, "clip_name": "DJI_20260830121454_0078_D.MP4", "start_sec": 15.0, "end_sec": 45.0}
  ],
  "broll_inserts": [
    {"timeline_sec": 36.0, "duration_sec": 3.0, "asset_file": "xach_balo_len_va_di.mp4", "asset_type": "meme"},
    {"timeline_sec": 79.4, "duration_sec": 2.5, "asset_file": "do_mixi_cuoi_sac_sua.mp4", "asset_type": "meme"}
  ],
  "sfx_inserts": [
    {"timeline_sec": 6.0, "duration_sec": 0.8, "asset_file": "cinematic_braam_hit.wav", "asset_type": "sfx"},
    {"timeline_sec": 36.0, "duration_sec": 0.8, "asset_file": "whoosh_deep_cinematic.wav", "asset_type": "sfx"}
  ]
}"""
    return StoryCopilot.parse_copilot_response(json_text)


def test_audit_sources_detects_found_and_missing(sample_plan, tmp_path):
    # Create fake footage for clip 54 only
    fake_clip = tmp_path / "DJI_20260830113907_0076_D.MP4"
    fake_clip.write_text("dummy video")
    
    resolver = SourceResolver(available_videos=[str(fake_clip)])
    audit = resolver.audit_plan(sample_plan)
    
    assert isinstance(audit, SourceAuditResult)
    # 1 clip found, 1 missing
    assert len(audit.found_footage) == 1
    assert len(audit.missing_footage) == 1
    assert audit.missing_footage[0]["clip_name"] == "DJI_20260830121454_0078_D.MP4"


def test_auto_resolve_missing_sfx_maps_to_available_assets(sample_plan, tmp_path):
    # Mock asset dir with some existing sfx
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir(parents=True)
    (sfx_dir / "whoosh_fast_short.wav").write_text("audio")
    (sfx_dir / "vine_boom_sub_drop.wav").write_text("audio")
    
    resolver = SourceResolver(assets_dir=str(tmp_path))
    resolved_plan = resolver.auto_resolve_missing_assets(sample_plan)
    
    # "whoosh_deep_cinematic.wav" should be mapped to "whoosh_fast_short.wav"
    mapped_sfx = [s.asset_file for s in resolved_plan.sfx_inserts]
    assert any("whoosh" in s for s in mapped_sfx)
    assert any("boom" in s or "braam" in s for s in mapped_sfx)


def test_readiness_score_calculation(sample_plan, tmp_path):
    fake_clip1 = tmp_path / "DJI_20260830113907_0076_D.MP4"
    fake_clip2 = tmp_path / "DJI_20260830121454_0078_D.MP4"
    fake_clip1.write_text("dummy")
    fake_clip2.write_text("dummy")
    
    resolver = SourceResolver(available_videos=[str(fake_clip1), str(fake_clip2)])
    audit = resolver.audit_plan(sample_plan)
    
    # Footage is 100% ready (2/2)
    assert audit.footage_readiness == 100.0
