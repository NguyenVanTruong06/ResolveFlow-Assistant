"""
tests/core/test_proposal_builder.py
=============================================================================
Unit Tests cho Proposal Builder thuần Python (G06-06C3-S3-I)
Kiểm thử chuyển đổi có chủ quyền từ Slot 1/2, Manifest, CreativeContext và Sidecar
thành EditPlanProposal bất biến tuân thủ đúng 8 fields chuẩn tắc.
=============================================================================
"""

import pytest
from src.core.clip_manifest import ClipManifest, ClipEntry
from src.core.contracts.edit_plan_contracts import normalize_creative_context
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
)
from src.core.contracts.asset_matching_contracts import (
    OfflineAssetCandidate,
    OfflineAssetMatchResult,
)
from src.core.contracts.plan_demand_contracts import (
    AssetDemandItem,
    DemandMatchEntry,
    AssetMatchingSidecar,
)
from src.core.plan_demand_adapter import PlanDemandAdapter
from src.core.proposal_builder import build_edit_plan_proposal


def _make_sample_manifest(manifest_id: str = "mf_sample_01") -> ClipManifest:
    m = ClipManifest(project_name="TestProj", manifest_id=manifest_id)
    m.clips = [
        ClipEntry.from_dict({"clip_id": "clip_01", "file_name": "clip1.mp4", "current_path": "/media/clip1.mp4", "duration_sec": 60.0}),
        ClipEntry.from_dict({"clip_id": "clip_02", "file_name": "clip2.mp4", "current_path": "/media/clip2.mp4", "duration_sec": 120.0}),
        ClipEntry.from_dict({"clip_id": "clip_03", "file_name": "clip3.mp4", "current_path": "/media/clip3.mp4", "duration_sec": 90.0}),
    ]
    return m


def _make_sample_video_plan(manifest_id: str = "mf_sample_01") -> dict:
    return {
        "manifest_id": manifest_id,
        "timeline_segments": [
            {
                "clip_id": "clip_01",
                "start_sec": 0.0,
                "end_sec": 10.0,
                "action": "keep",
            },
            {
                "clip_id": "clip_02",
                "start_sec": 5.0,
                "end_sec": 15.0,
                "action": "keep",
            },
        ],
    }


def _make_candidate(
    asset_id: str,
    *,
    category: str = "broll",
    source_kind: str = "managed",
    availability: str = "online",
    origin_library_id: str = "lib_core",
    source_id: str = "src_01",
    pack_id: str = "pack_01",
    match_score: int = 95,
    requires_reconnect: bool = False,
    is_native_guidance: bool = False,
) -> OfflineAssetCandidate:
    return OfflineAssetCandidate(
        asset_id=asset_id,
        name=f"Name {asset_id}",
        category=category,
        subcategory="general",
        source_kind=source_kind,
        availability=availability,
        match_score=match_score,
        matched_signals=("keyword",),
        duration_sec=3.0,
        pack_id=pack_id,
        pack_name="Pack Name",
        source_id=source_id,
        origin_library_id=origin_library_id,
        requires_reconnect=requires_reconnect,
        is_native_guidance=is_native_guidance,
    )


def test_build_legacy_video_only_proposal():
    """T01: Kịch bản video đơn lẻ hợp lệ, 0 sidecar, 0 kinetic plan -> EditPlanProposal 8 fields."""
    manifest = _make_sample_manifest("mf_test_01")
    v_plan = _make_sample_video_plan("mf_test_01")
    ctx = normalize_creative_context({"free_brief": "Video du lịch Đà Lạt"})

    prop = build_edit_plan_proposal(
        proposal_id="prop_01",
        manifest=manifest,
        video_plan=v_plan,
        creative_context=ctx,
        kinetic_plan=None,
        sidecar=None,
    )

    assert isinstance(prop, EditPlanProposal)
    assert prop.proposal_id == "prop_01"
    assert prop.manifest_id == "mf_test_01"
    assert prop.creative_context.free_brief == "Video du lịch Đà Lạt"
    assert prop.kinetic_plan is None
    assert len(prop.source_binding["clips"]) == 3
    assert len(prop.video_plan["timeline_segments"]) == 2
    assert len(prop.proposed_asset_assignments) == 0
    assert len(prop.proposed_omissions) == 0

    rev = compute_proposal_revision(prop)
    assert len(rev) == 64
    assert rev == compute_proposal_revision(prop), "Revision phải tất định"


def test_build_dual_slot_with_matching_sidecar():
    """T01 & T02: Kịch bản kép (Video + Kinetic) kèm Sidecar có cả mục khớp và mục không khớp."""
    manifest = _make_sample_manifest("mf_dual_01")
    v_plan = {
        "manifest_id": "mf_dual_01",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep"},
            {"clip_id": "clip_02", "start_sec": 5.0, "end_sec": 15.0, "action": "keep"},
        ],
        "broll_inserts": [
            {
                "asset_type": "broll",
                "timeline_sec": 2.5,
                "duration_sec": 3.0,
                "description": "mon an ngon",
            }
        ],
        "sfx_inserts": [
            {
                "asset_type": "sfx",
                "timeline_sec": 5.0,
                "duration_sec": 1.5,
                "description": "tieng xao nau",
            }
        ],
    }
    k_plan = {
        "manifest_id": "mf_dual_01",
        "subtitles": [{"text": "Chào mừng bạn", "in_sec": 1.0, "out_sec": 3.0}],
    }
    ctx = normalize_creative_context({"free_brief": "Phóng sự ẩm thực"})

    # Trích xuất canonical batch từ plan thực tế
    batch = PlanDemandAdapter.extract(v_plan, plan_revision="rev_test_sidecar")
    demand_broll = batch.demands[0]
    demand_sfx = batch.demands[1]

    cand_online = _make_candidate("asset_food_01", source_kind="managed", origin_library_id="lib_core")
    result_broll = OfflineAssetMatchResult(
        request_id=demand_broll.demand_id,
        category="broll",
        query_text="mon an ngon",
        is_matched=True,
        candidates=(cand_online,),
    )
    entry_broll = DemandMatchEntry(demand=demand_broll, result=result_broll)

    result_sfx = OfflineAssetMatchResult(
        request_id=demand_sfx.demand_id,
        category="sfx",
        query_text="tieng xao nau",
        is_matched=False,
        candidates=(),
        unmatched_reason="Không tìm thấy hiệu ứng âm thanh xào nấu",
    )
    entry_sfx = DemandMatchEntry(demand=demand_sfx, result=result_sfx)

    sidecar = AssetMatchingSidecar(
        plan_revision="rev_test_sidecar",
        manifest_id="mf_dual_01",
        entries=(entry_broll, entry_sfx),
    )

    prop = build_edit_plan_proposal(
        proposal_id="prop_dual_01",
        manifest=manifest,
        video_plan=v_plan,
        creative_context=ctx,
        kinetic_plan=k_plan,
        sidecar=sidecar,
        matching_plan_revision="rev_test_sidecar",
        expected_batch=batch,
    )

    assert prop.kinetic_plan is not None
    assert len(prop.proposed_asset_assignments) == 1
    assign = prop.proposed_asset_assignments[0]
    assert assign["demand_id"] == demand_broll.demand_id
    assert assign["slot"] == "broll"
    assert assign["asset_id"] == "asset_food_01"
    assert assign["source_kind"] == "managed"
    assert assign["origin_library_id"] == "lib_core"
    assert assign["status"] == "PROPOSED"

    assert len(prop.proposed_omissions) == 1
    omit = prop.proposed_omissions[0]
    assert omit["demand_id"] == demand_sfx.demand_id
    assert omit["slot"] == "sfx"
    assert omit["reason"] == "Không tìm thấy hiệu ứng âm thanh xào nấu"
    assert "tieng xao nau" in omit["warning_message"] or "sfx" in omit["warning_message"].lower()


def test_build_missing_sidecar_generates_not_matched_yet_omissions():
    """T02: Video plan có broll_inserts nhưng sidecar=None -> Báo NOT_MATCHED_YET omissions."""
    manifest = _make_sample_manifest("mf_missing_sc")
    v_plan = {
        "manifest_id": "mf_missing_sc",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep"},
        ],
        "broll_inserts": [
            {"timeline_sec": 2.0, "duration_sec": 4.0, "query": "broll canh bien"},
        ],
        "sfx_inserts": [
            {"timeline_sec": 2.0, "duration_sec": 1.0, "query": "tieng song vo"},
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Video bien"})

    prop = build_edit_plan_proposal(
        proposal_id="prop_no_sidecar",
        manifest=manifest,
        video_plan=v_plan,
        creative_context=ctx,
        sidecar=None,
    )

    assert len(prop.proposed_asset_assignments) == 0
    assert len(prop.proposed_omissions) == 2
    for omit in prop.proposed_omissions:
        assert omit["reason"] == "NOT_MATCHED_YET"
        assert "Chưa tra cứu" in omit["warning_message"] or "NOT_MATCHED_YET" in omit["warning_message"]


def test_build_manifest_id_mismatch_denied():
    """T03: Manifest ID giữa Video Plan và Manifest khác nhau -> Bị từ chối bằng ValueError."""
    manifest = _make_sample_manifest("mf_manifest_A")
    v_plan = _make_sample_video_plan("mf_manifest_B")  # Lệch!
    ctx = normalize_creative_context({"free_brief": "Test"})

    with pytest.raises(ValueError, match="ERR_MANIFEST_MISMATCH"):
        build_edit_plan_proposal(
            proposal_id="prop_mismatch",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
        )


def test_build_unknown_source_clip_id_denied():
    """T02 & T03: Đoạn cắt yêu cầu clip_id không có trong Manifest -> Từ chối fail-closed."""
    manifest = _make_sample_manifest("mf_clip_check")
    v_plan = {
        "manifest_id": "mf_clip_check",
        "timeline_segments": [
            {"clip_id": "clip_99_phantom", "start_sec": 0.0, "end_sec": 5.0, "action": "keep"},
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})

    with pytest.raises(ValueError, match="ERR_UNKNOWN_CLIP_ID"):
        build_edit_plan_proposal(
            proposal_id="prop_bad_clip",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
        )


def test_build_reference_item_as_source_clip_denied():
    """T02: Clip ID trùng với reference_item trong CreativeContext -> Bị từ chối ERR_REFERENCE_LEAK."""
    manifest = _make_sample_manifest("mf_ref_leak")
    # Biến clip_01 thành reference_only trong creative context
    ctx = normalize_creative_context({
        "free_brief": "Test",
        "reference_items": [{"reference_id": "clip_01", "local_path": "/refs/ref1.mp4", "media_type": "video", "role": "reference_only"}],
    })
    v_plan = _make_sample_video_plan("mf_ref_leak")

    with pytest.raises(ValueError, match="ERR_REFERENCE_LEAK"):
        build_edit_plan_proposal(
            proposal_id="prop_ref_leak",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
        )


def test_build_offline_candidate_becomes_omission():
    """T02: Ứng viên chỉ có offline -> Không được tự phong gán, phải chuyển thành proposed omission."""
    manifest = _make_sample_manifest("mf_offline_test")
    v_plan = {
        "manifest_id": "mf_offline_test",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep"},
        ],
        "broll_inserts": [
            {
                "asset_type": "broll",
                "timeline_sec": 3.0,
                "duration_sec": 2.0,
                "description": "offline meme",
            }
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})

    batch = PlanDemandAdapter.extract(v_plan, plan_revision="rev_offline")
    demand_broll = batch.demands[0]

    cand_offline = _make_candidate(
        "asset_offline_meme",
        source_kind="managed",
        origin_library_id="lib_offline",
        availability="offline",  # Offline!
    )
    result_broll = OfflineAssetMatchResult(
        request_id=demand_broll.demand_id,
        category="broll",
        query_text="offline meme",
        is_matched=True,
        candidates=(cand_offline,),
    )
    entry_broll = DemandMatchEntry(demand=demand_broll, result=result_broll)

    sidecar = AssetMatchingSidecar(
        plan_revision="rev_offline",
        manifest_id="mf_offline_test",
        entries=(entry_broll,),
    )

    prop = build_edit_plan_proposal(
        proposal_id="prop_offline",
        manifest=manifest,
        video_plan=v_plan,
        creative_context=ctx,
        sidecar=sidecar,
        matching_plan_revision="rev_offline",
    )

    # Khẳng định: 0 assignments, 1 omission!
    assert len(prop.proposed_asset_assignments) == 0
    assert len(prop.proposed_omissions) == 1
    assert prop.proposed_omissions[0]["demand_id"] == demand_broll.demand_id
    assert "offline" in prop.proposed_omissions[0]["reason"].lower()


def test_build_user_candidate_override_and_user_omit():
    """T02: Người dùng có thể ghi đè ứng viên được chọn hoặc chủ động omit một mục đã khớp."""
    manifest = _make_sample_manifest("mf_override")
    v_plan = {
        "manifest_id": "mf_override",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep"},
        ],
        "broll_inserts": [
            {
                "asset_type": "broll",
                "timeline_sec": 1.0,
                "duration_sec": 2.0,
                "description": "candidate choices",
            }
        ],
        "sfx_inserts": [
            {
                "asset_type": "sfx",
                "timeline_sec": 4.0,
                "duration_sec": 1.0,
                "description": "sfx sound",
            }
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})

    batch = PlanDemandAdapter.extract(v_plan, plan_revision="rev_override")
    demand_1 = batch.demands[0]
    demand_2 = batch.demands[1]

    cand_a = _make_candidate("asset_cand_a", source_kind="builtin", match_score=90)
    cand_b = _make_candidate("asset_cand_b", source_kind="builtin", match_score=85)
    res_1 = OfflineAssetMatchResult(
        request_id=demand_1.demand_id,
        category="broll",
        query_text="candidate choices",
        is_matched=True,
        candidates=(cand_a, cand_b),
    )

    cand_sfx = _make_candidate("asset_sfx_pop", category="sfx", source_kind="builtin", match_score=92)
    res_2 = OfflineAssetMatchResult(
        request_id=demand_2.demand_id,
        category="sfx",
        query_text="sfx sound",
        is_matched=True,
        candidates=(cand_sfx,),
    )

    sidecar = AssetMatchingSidecar(
        plan_revision="rev_override",
        manifest_id="mf_override",
        entries=(
            DemandMatchEntry(demand=demand_1, result=res_1),
            DemandMatchEntry(demand=demand_2, result=res_2),
        ),
    )

    # Người dùng chọn cand_b cho demand_1, và chủ động omit demand_2
    prop = build_edit_plan_proposal(
        proposal_id="prop_override",
        manifest=manifest,
        video_plan=v_plan,
        creative_context=ctx,
        sidecar=sidecar,
        matching_plan_revision="rev_override",
        selected_candidate_ids={
            demand_1.demand_id: "asset_cand_b",
            demand_2.demand_id: "__omit__",
        },
    )

    assert len(prop.proposed_asset_assignments) == 1
    assert prop.proposed_asset_assignments[0]["demand_id"] == demand_1.demand_id
    assert prop.proposed_asset_assignments[0]["asset_id"] == "asset_cand_b"

    assert len(prop.proposed_omissions) == 1
    assert prop.proposed_omissions[0]["demand_id"] == demand_2.demand_id
    assert "chủ động" in prop.proposed_omissions[0]["reason"].lower() or "user" in prop.proposed_omissions[0]["reason"].lower()


def test_build_invalid_timing_in_demand_raises_error():
    """T02 & T03: Dữ liệu thời lượng/thời gian âm hoặc NaN bị từ chối, cấm bịa 3.0s."""
    manifest = _make_sample_manifest("mf_bad_timing")
    v_plan = {
        "manifest_id": "mf_bad_timing",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep"},
        ],
        "broll_inserts": [
            {"timeline_sec": -10.0, "duration_sec": 0.0, "query": "canh loi"},
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})

    with pytest.raises(ValueError, match="ERR_INVALID_NUMERIC"):
        build_edit_plan_proposal(
            proposal_id="prop_bad_time",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
            sidecar=None,
        )


def test_build_blank_proposal_id_or_empty_timeline_denied():
    """T03: proposal_id rỗng hoặc timeline_segments rỗng bị từ chối có kiểm soát."""
    manifest = _make_sample_manifest("mf_blank_test")
    v_plan = _make_sample_video_plan("mf_blank_test")
    ctx = normalize_creative_context({"free_brief": "Test"})

    with pytest.raises(ValueError, match="proposal_id"):
        build_edit_plan_proposal(
            proposal_id="   ",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
        )

    v_plan_empty = {"manifest_id": "mf_blank_test", "timeline_segments": []}
    with pytest.raises(ValueError, match="ERR_EMPTY_TIMELINE_SEGMENTS"):
        build_edit_plan_proposal(
            proposal_id="prop_empty_segs",
            manifest=manifest,
            video_plan=v_plan_empty,
            creative_context=ctx,
        )


# =============================================================================
# Probes B1-B3: Bounds, BigInt, Strict Accounting & Candidate Provenance
# =============================================================================

def test_probe_b1_primary_cut_exceeds_loaded_duration():
    """B1: Đường cắt vượt quá thời lượng clip đã nạp (vd 10000s > 60s) bị từ chối bằng ValueError."""
    manifest = _make_sample_manifest("mf_b1")
    v_plan = {
        "manifest_id": "mf_b1",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 10000.0, "action": "keep"},
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})
    with pytest.raises(ValueError, match="ERR_PRIMARY_CUT_EXCEEDS_DURATION"):
        build_edit_plan_proposal(
            proposal_id="prop_b1",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
        )


def test_cisco_fractional_end_keeps_strict_source_bounds():
    manifest = ClipManifest(project_name="Cisco", manifest_id="e1a4b04c", clips=[
        ClipEntry.from_dict({"clip_id": "C01", "file_name": "Cisco.mp4", "current_path": "/tmp/Cisco.mp4", "duration_sec": 349.274558})
    ])
    plan = {
        "manifest_id": "e1a4b04c",
        "global_hook": {"clip_id": "C01", "start_sec": 0.0, "end_sec": 40.0},
        "timeline_segments": [{"clip_id": "C01", "start_sec": 40.0, "end_sec": 349.3, "action": "keep"}],
    }
    with pytest.raises(ValueError, match="ERR_PRIMARY_CUT_EXCEEDS_DURATION"):
        build_edit_plan_proposal(proposal_id="cisco", manifest=manifest, video_plan=plan, creative_context={})
    plan["timeline_segments"][0]["end_sec"] = 349.274558
    proposal = build_edit_plan_proposal(proposal_id="cisco", manifest=manifest, video_plan=plan, creative_context={})
    assert proposal.proposal_id == "cisco"


def test_probe_b1_manifest_duration_bigint_overflow():
    """B1: Thời lượng clip kiểu bigint khổng lồ (10**400) bị bắt an toàn bằng ValueError."""
    manifest = {
        "manifest_id": "mf_b1_bigint",
        "clips": [
            {"clip_id": "clip_big", "duration": 10**400},
        ],
    }
    v_plan = {
        "manifest_id": "mf_b1_bigint",
        "timeline_segments": [
            {"clip_id": "clip_big", "start_sec": 0.0, "end_sec": 5.0, "action": "keep"},
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})
    with pytest.raises(ValueError, match="ERR_NUMERIC_OVERFLOW"):
        build_edit_plan_proposal(
            proposal_id="prop_b1_overflow",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
        )


def test_probe_b2_nonmapping_raw_optional_demand_skipped_rejected():
    """B2: broll_inserts chứa phần tử không phải Mapping ([123]) bị từ chối bằng ValueError."""
    manifest = _make_sample_manifest("mf_b2")
    v_plan = {
        "manifest_id": "mf_b2",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 5.0, "action": "keep"},
        ],
        "broll_inserts": [123],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})
    with pytest.raises(ValueError, match="ERR_INVALID_INSERT_ROW"):
        build_edit_plan_proposal(
            proposal_id="prop_b2_nonmap",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
        )


def test_probe_b2_empty_sidecar_hides_valid_plan_demand_rejected():
    """B2: Kịch bản có 1 demand nhưng sidecar có 0 entries -> Bị từ chối ERR_SIDECAR_DEMAND_MISMATCH."""
    manifest = _make_sample_manifest("mf_b2_empty_sc")
    v_plan = {
        "manifest_id": "mf_b2_empty_sc",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 5.0, "action": "keep"},
        ],
        "broll_inserts": [
            {"timeline_sec": 1.0, "duration_sec": 2.0, "asset_type": "broll", "description": "city"},
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})
    empty_sidecar = AssetMatchingSidecar(
        plan_revision="rev_b2",
        manifest_id="mf_b2_empty_sc",
        entries=(),  # 0 entries!
    )
    with pytest.raises(ValueError, match="ERR_SIDECAR_DEMAND_MISMATCH"):
        build_edit_plan_proposal(
            proposal_id="prop_b2_empty",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
            sidecar=empty_sidecar,
            matching_plan_revision="rev_b2",
        )


def test_probe_b3_first_online_reconnect_second_online_valid_selects_second():
    """B3: Ứng viên 1 online nhưng requires_reconnect=True, ứng viên 2 online hợp lệ -> Chọn ứng viên 2."""
    manifest = _make_sample_manifest("mf_b3")
    v_plan = {
        "manifest_id": "mf_b3",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep"},
        ],
        "broll_inserts": [
            {
                "asset_type": "broll",
                "timeline_sec": 2.0,
                "duration_sec": 3.0,
                "description": "ocean wave",
            }
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})

    batch = PlanDemandAdapter.extract(v_plan, plan_revision="rev_b3")
    demand_broll = batch.demands[0]

    cand_1_reconnect = _make_candidate(
        "asset_cand_reconnect",
        source_kind="managed",
        origin_library_id="lib_1",
        availability="online",
        requires_reconnect=True,  # Ineligible!
    )
    cand_2_valid = _make_candidate(
        "asset_cand_good",
        source_kind="managed",
        origin_library_id="lib_1",
        availability="online",
        requires_reconnect=False,  # Fully eligible!
    )
    res = OfflineAssetMatchResult(
        request_id=demand_broll.demand_id,
        category="broll",
        query_text="ocean wave",
        is_matched=True,
        candidates=(cand_1_reconnect, cand_2_valid),
    )
    entry = DemandMatchEntry(demand=demand_broll, result=res)
    sidecar = AssetMatchingSidecar(
        plan_revision="rev_b3",
        manifest_id="mf_b3",
        entries=(entry,),
    )

    prop = build_edit_plan_proposal(
        proposal_id="prop_b3",
        manifest=manifest,
        video_plan=v_plan,
        creative_context=ctx,
        sidecar=sidecar,
        matching_plan_revision="rev_b3",
    )

    assert len(prop.proposed_asset_assignments) == 1
    assert prop.proposed_asset_assignments[0]["asset_id"] == "asset_cand_good"
    assert len(prop.proposed_omissions) == 0


def test_probe_r2_zero_demand_plan_with_nonempty_sidecar_rejected():
    """R2: Kịch bản 0 demands nhưng sidecar có entries -> Bị từ chối ERR_SIDECAR_DEMAND_MISMATCH."""
    manifest = _make_sample_manifest("mf_r2_zero")
    v_plan = _make_sample_video_plan("mf_r2_zero")  # 0 inserts!
    ctx = normalize_creative_context({"free_brief": "Test"})

    dummy_demand = AssetDemandItem(
        demand_id='["rev_r2","broll_inserts",0]',
        source_slot="broll_inserts",
        source_index=0,
        status="eligible",
        category="broll",
        timeline_sec=1.0,
        desired_duration_sec=2.0,
        query_text="dummy",
    )
    sidecar = AssetMatchingSidecar(
        plan_revision="rev_r2",
        manifest_id="mf_r2_zero",
        entries=(DemandMatchEntry(demand=dummy_demand),),
    )

    with pytest.raises(ValueError, match="ERR_SIDECAR_DEMAND_MISMATCH"):
        build_edit_plan_proposal(
            proposal_id="prop_r2_zero",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
            sidecar=sidecar,
            matching_plan_revision="rev_r2",
        )


def test_probe_r2_sidecar_same_id_changed_timing_rejected():
    """R2: Sidecar giữ nguyên demand_id nhưng đổi timeline_sec thành 999.0 -> Bị từ chối ERR_SIDECAR_DEMAND_MISMATCH."""
    manifest = _make_sample_manifest("mf_r2_timing")
    v_plan = {
        "manifest_id": "mf_r2_timing",
        "timeline_segments": [
            {"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep"},
        ],
        "broll_inserts": [
            {"asset_type": "broll", "timeline_sec": 2.5, "duration_sec": 3.0, "description": "city night"},
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})
    batch = PlanDemandAdapter.extract(v_plan, plan_revision="rev_r2_time")
    orig_demand = batch.demands[0]

    # Tampered demand: cùng demand_id nhưng timeline_sec = 999.0
    tampered_demand = AssetDemandItem(
        demand_id=orig_demand.demand_id,
        source_slot=orig_demand.source_slot,
        source_index=orig_demand.source_index,
        status=orig_demand.status,
        raw_type=orig_demand.raw_type,
        category=orig_demand.category,
        timeline_sec=999.0,  # Bị sửa đổi ngầm!
        desired_duration_sec=orig_demand.desired_duration_sec,
        raw_query_hint=orig_demand.raw_query_hint,
        query_text=orig_demand.query_text,
        query_source=orig_demand.query_source,
    )
    cand = _make_candidate("asset_tampered", source_kind="managed", origin_library_id="lib_1")
    res = OfflineAssetMatchResult(
        request_id=orig_demand.demand_id,
        category="broll",
        query_text=orig_demand.query_text,
        is_matched=True,
        candidates=(cand,),
    )
    tampered_sidecar = AssetMatchingSidecar(
        plan_revision="rev_r2_time",
        manifest_id="mf_r2_timing",
        entries=(DemandMatchEntry(demand=tampered_demand, result=res),),
    )

    with pytest.raises(ValueError, match="ERR_SIDECAR_DEMAND_MISMATCH"):
        build_edit_plan_proposal(
            proposal_id="prop_r2_time",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
            sidecar=tampered_sidecar,
            matching_plan_revision="rev_r2_time",
        )


def test_probe_r2_falsy_nonmapping_choices_rejected():
    """R2: selected_candidate_ids là falsy non-mapping ([], False, 0, '') -> Bị từ chối ERR_INVALID_MAPPING."""
    manifest = _make_sample_manifest("mf_r2_choices")
    v_plan = _make_sample_video_plan("mf_r2_choices")
    ctx = normalize_creative_context({"free_brief": "Test"})

    for bad_val in ([], False, 0, ""):
        with pytest.raises(ValueError, match="ERR_INVALID_MAPPING"):
            build_edit_plan_proposal(
                proposal_id="prop_r2_bad_choices",
                manifest=manifest,
                video_plan=v_plan,
                creative_context=ctx,
                selected_candidate_ids=bad_val,  # type: ignore
            )


def test_probe_r2_conflicting_choice_aliases_rejected():
    """R2: selected_candidate_ids và user_choices cung cấp cùng lúc nhưng khác nội dung -> Bị từ chối."""
    manifest = _make_sample_manifest("mf_r2_conflict")
    v_plan = _make_sample_video_plan("mf_r2_conflict")
    ctx = normalize_creative_context({"free_brief": "Test"})

    with pytest.raises(ValueError, match="ERR_CONFLICTING_CHOICE_ALIASES"):
        build_edit_plan_proposal(
            proposal_id="prop_r2_conflict",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
            selected_candidate_ids={"dem_1": "asset_1"},
            user_choices={"dem_1": "asset_2"},
        )


# =============================================================================
# R3: Exhaustive 12-Field DemandItem & ExpectedBatch Graph Parity Tests
# =============================================================================

@pytest.mark.parametrize("field,bad_value", [
    ("demand_id", "different_demand_id"),
    ("source_slot", "slot2_different"),
    ("source_index", False),  # bool must not equate to int 0!
    ("source_index", 999),
    ("status", "UNMATCHED"),
    ("raw_type", "different_raw_type"),
    ("category", "sfx"),
    ("timeline_sec", 123.4),
    ("desired_duration_sec", 99.0),
    ("raw_query_hint", object()),  # untyped object
    ("raw_query_hint", "different_hint"),
    ("query_text", "different query text"),
    ("query_source", "different_source"),
    ("rejection_reason", "REJECTED_MANUALLY"),
])
def test_probe_r3_sidecar_demand_all_12_fields_parity(field, bad_value):
    """R3: Mọi biến đổi trên bất kỳ trường nào trong 12 fields của AssetDemandItem đều bị từ chối ValueError."""
    manifest = _make_sample_manifest("mf_r3_p12")
    v_plan = {
        "manifest_id": "mf_r3_p12",
        "timeline_segments": [{"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep"}],
        "broll_inserts": [
            {"asset_type": "broll", "timeline_sec": 2.0, "duration_sec": 3.0, "description": "broll query"},
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})
    batch = PlanDemandAdapter.extract(v_plan, plan_revision="rev_r3_p12")
    cand = _make_candidate("asset_01")
    res = OfflineAssetMatchResult(
        request_id=batch.demands[0].demand_id,
        category=batch.demands[0].category,
        query_text=batch.demands[0].query_text,
        candidates=(cand,),
        is_matched=True,
    )
    sidecar = PlanDemandAdapter.assemble(batch, (res,))

    # Đột biến trường được chỉ định trên demand của sidecar
    object.__setattr__(sidecar.entries[0].demand, field, bad_value)

    with pytest.raises(ValueError):
        build_edit_plan_proposal(
            proposal_id="prop_r3_p12",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
            sidecar=sidecar,
            matching_plan_revision="rev_r3_p12",
        )


def test_probe_r3_expected_batch_requests_mismatch_denied():
    """R3: expected_batch có requests không khớp canonical extract -> ValueError."""
    manifest = _make_sample_manifest("mf_r3_exp")
    v_plan = {
        "manifest_id": "mf_r3_exp",
        "timeline_segments": [{"clip_id": "clip_01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep"}],
        "broll_inserts": [
            {"asset_type": "broll", "timeline_sec": 2.0, "duration_sec": 3.0, "description": "broll query"},
        ],
    }
    ctx = normalize_creative_context({"free_brief": "Test"})
    batch = PlanDemandAdapter.extract(v_plan, plan_revision="rev_r3_exp")
    cand = _make_candidate("asset_01")
    res = OfflineAssetMatchResult(
        request_id=batch.demands[0].demand_id,
        category=batch.demands[0].category,
        query_text=batch.demands[0].query_text,
        candidates=(cand,),
        is_matched=True,
    )
    sidecar = PlanDemandAdapter.assemble(batch, (res,))

    # Tạo foreign expected_batch với request có top_k khác canonical (top_k=10 thay vì 5)
    from src.core.contracts.asset_matching_contracts import OfflineAssetMatchRequest
    from src.core.contracts.plan_demand_contracts import AssetDemandBatch
    foreign_req = OfflineAssetMatchRequest(
        request_id=batch.demands[0].demand_id,
        category=batch.demands[0].category,
        query_text=batch.demands[0].query_text,
        top_k=10,  # Mismatch với canonical top_k=5
    )
    foreign_batch = AssetDemandBatch(
        plan_revision="rev_r3_exp",
        manifest_id="mf_r3_exp",
        demands=batch.demands,
        requests=(foreign_req,),
    )

    with pytest.raises(ValueError, match="ERR_EXPECTED_BATCH_MISMATCH"):
        build_edit_plan_proposal(
            proposal_id="prop_r3_exp",
            manifest=manifest,
            video_plan=v_plan,
            creative_context=ctx,
            sidecar=sidecar,
            matching_plan_revision="rev_r3_exp",
            expected_batch=foreign_batch,
        )

