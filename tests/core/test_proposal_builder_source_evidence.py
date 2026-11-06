"""
tests/core/test_proposal_builder_source_evidence.py
=============================================================================
Kiểm thử bộ phận cho ràng buộc bằng chứng nguồn tiền phê duyệt trong ProposalBuilder (G07-07A4-I).
Bao phủ các nhóm T01, T02, T03, T04 theo lệnh thi công.
=============================================================================
"""

import copy
import pytest
from src.core.clip_manifest import ClipEntry, ClipManifest
from src.core.contracts.edit_plan_proposal_contracts import (
    compute_proposal_revision,
    edit_plan_proposal_to_dict,
)
from src.core.contracts.time_contracts import RationalFPS
from src.core.proposal_builder import (
    PROFILE_UNIFORM_TICKS_V1,
    build_edit_plan_proposal,
    compute_proof_signature,
    validate_and_normalize_source_evidence_snapshot,
)

SAMPLE_SHA256_A = "a" * 64
SAMPLE_SHA256_B = "b" * 64


def _create_minimal_context_and_plans():
    manifest = {
        "manifest_id": "mf_001",
        "clips": [
            {"clip_id": "C01", "path": "/media/footage/c01.mp4", "duration": 10.0},
            {"clip_id": "C02", "path": "/media/footage/c02.mp4", "duration": 15.0},
        ],
    }
    video_plan = {
        "manifest_id": "mf_001",
        "timeline_segments": [
            {
                "clip_id": "C01",
                "timeline_in_sec": 0.0,
                "timeline_out_sec": 5.0,
                "source_in_sec": 0.0,
                "source_out_sec": 5.0,
            },
            {
                "clip_id": "C02",
                "timeline_in_sec": 5.0,
                "timeline_out_sec": 10.0,
                "source_in_sec": 0.0,
                "source_out_sec": 5.0,
            },
        ],
    }
    creative_context = {
        "free_brief": "Test brief",
        "style_preset": "default",
        "reference_items": [],
    }
    return manifest, video_plan, creative_context


def _create_valid_snapshot(
    clip_id="C01",
    manifest_id="mf_001",
    file_path="/media/footage/c01.mp4",
    sha=SAMPLE_SHA256_A,
    stream_index=0,
):
    return {
        "manifest_id": manifest_id,
        "clip_id": clip_id,
        "file_path": file_path,
        "stream_index": stream_index,
        "observed_full_sha256": sha,
        "freshness": {
            "file_size_bytes": 1024000,
            "mtime_ns": 1700000000000,
            "ctime_ns": 1700000000000,
            "device_id": 66311,
            "inode_number": 12345,
        },
        "frame_evidence": {
            "manifest_id": manifest_id,
            "clip_id": clip_id,
            "source_fps": {"numerator": 30, "denominator": 1},
            "total_frames": 300,
            "evidence_status": "VERIFIED_CFR",
        },
        "complete_scan": True,
        "profile_policy": PROFILE_UNIFORM_TICKS_V1,
    }


# =============================================================================
# T01: Pure legacy behavior
# =============================================================================

def test_t01_legacy_none_and_empty_evidence_preserve_proposal():
    manifest, video_plan, creative_context = _create_minimal_context_and_plans()

    # None
    p_none = build_edit_plan_proposal(
        proposal_id="prop_legacy_none",
        manifest=manifest,
        video_plan=video_plan,
        creative_context=creative_context,
        source_evidence_by_clip=None,
    )
    # Empty
    p_empty = build_edit_plan_proposal(
        proposal_id="prop_legacy_none",
        manifest=manifest,
        video_plan=video_plan,
        creative_context=creative_context,
        source_evidence_by_clip={},
    )

    # Cả hai phải giống hệt nhau về revision và các clips không có expected_full_sha256
    rev_none = compute_proposal_revision(p_none)
    rev_empty = compute_proposal_revision(p_empty)
    assert rev_none == rev_empty

    for c in p_none.source_binding["clips"]:
        assert "expected_full_sha256" not in c
        assert "stream_index" not in c


# =============================================================================
# T02: Proof authority & pair binding
# =============================================================================

def test_t02_valid_complete_entry_binds_pair():
    manifest, video_plan, creative_context = _create_minimal_context_and_plans()
    sn_c01 = _create_valid_snapshot(clip_id="C01", sha=SAMPLE_SHA256_A, stream_index=1)

    prop = build_edit_plan_proposal(
        proposal_id="prop_bound_01",
        manifest=manifest,
        video_plan=video_plan,
        creative_context=creative_context,
        source_evidence_by_clip={"C01": sn_c01},
    )

    clips_dict = {c["clip_id"]: c for c in prop.source_binding["clips"]}
    assert clips_dict["C01"]["expected_full_sha256"] == SAMPLE_SHA256_A
    assert clips_dict["C01"]["stream_index"] == 1
    # C02 không có bằng chứng nên không có hai trường này
    assert "expected_full_sha256" not in clips_dict["C02"]
    assert "stream_index" not in clips_dict["C02"]


def test_t02_reject_mismatched_and_malformed_evidence():
    manifest, video_plan, creative_context = _create_minimal_context_and_plans()

    # 1. Sai manifest_id
    sn_bad_mf = _create_valid_snapshot(manifest_id="mf_other")
    with pytest.raises(ValueError, match="ERR_EVIDENCE_MANIFEST_MISMATCH"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip={"C01": sn_bad_mf},
        )

    # 2. Sai path
    sn_bad_path = _create_valid_snapshot(file_path="/wrong/path.mp4")
    with pytest.raises(ValueError, match="ERR_EVIDENCE_PATH_MISMATCH"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip={"C01": sn_bad_path},
        )

    # 3. complete_scan là False
    sn_inc = _create_valid_snapshot()
    sn_inc["complete_scan"] = False
    with pytest.raises(ValueError, match="ERR_INCOMPLETE_SCAN"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip={"C01": sn_inc},
        )

    # 4. evidence_status không phải VERIFIED_CFR
    sn_vfr = _create_valid_snapshot()
    sn_vfr["frame_evidence"]["evidence_status"] = "HEADER_REPORTED_ONLY"
    with pytest.raises(ValueError, match="ERR_UNVERIFIED_FRAME_STATUS"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip={"C01": sn_vfr},
        )

    # 5. SHA-256 không hợp lệ
    sn_bad_sha = _create_valid_snapshot()
    sn_bad_sha["observed_full_sha256"] = "invalid_sha"
    with pytest.raises(ValueError, match="ERR_INVALID_DIGEST"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip={"C01": sn_bad_sha},
        )

    # 6. Clip ID lạ không thuộc manifest
    sn_extra = _create_valid_snapshot(clip_id="C01")
    with pytest.raises(ValueError, match="ERR_UNKNOWN_EVIDENCE_CLIP"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip={"C_UNKNOWN": sn_extra},
        )

    # 7. Sai source_fps: số âm, 0, bool hoặc thiếu numerator/denominator
    sn_bad_fps = _create_valid_snapshot()
    sn_bad_fps["frame_evidence"]["source_fps"] = {"numerator": -1, "denominator": 1}
    with pytest.raises(ValueError, match="ERR_INVALID_RATIONAL_FPS"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip={"C01": sn_bad_fps},
        )

    sn_bool_fps = _create_valid_snapshot()
    sn_bool_fps["frame_evidence"]["source_fps"] = {"numerator": True, "denominator": 1}
    with pytest.raises(ValueError, match="ERR_INVALID_RATIONAL_FPS"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip={"C01": sn_bool_fps},
        )

    # 8. Sai freshness: số âm hoặc bool
    sn_bad_fresh = _create_valid_snapshot()
    sn_bad_fresh["freshness"]["file_size_bytes"] = True
    with pytest.raises(ValueError, match="ERR_INVALID_FRESHNESS_STAT"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip={"C01": sn_bad_fresh},
        )

    # 9. RationalFPS instance được hỗ trợ
    sn_rf = _create_valid_snapshot()
    sn_rf["frame_evidence"]["source_fps"] = RationalFPS(60, 1)
    prop_rf = build_edit_plan_proposal(
        proposal_id="p1",
        manifest=manifest,
        video_plan=video_plan,
        creative_context=creative_context,
        source_evidence_by_clip={"C01": sn_rf},
    )
    c01_row = [c for c in prop_rf.source_binding["clips"] if c["clip_id"] == "C01"][0]
    assert c01_row["expected_full_sha256"] == SAMPLE_SHA256_A


def test_t03_budget_overflow_rejected():
    manifest, video_plan, creative_context = _create_minimal_context_and_plans()
    sn = _create_valid_snapshot()
    # Tạo chuỗi cực lớn vượt quá 16 MiB
    sn["frame_evidence"]["extra_huge"] = "x" * (17 * 1024 * 1024)
    with pytest.raises(ValueError, match="ERR_PROOF_METADATA_BUDGET_EXCEEDED"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip={"C01": sn},
        )



# =============================================================================
# T03: Owned & Bounded Input
# =============================================================================

def test_t03_input_mutations_do_not_leak():
    manifest, video_plan, creative_context = _create_minimal_context_and_plans()
    sn = _create_valid_snapshot(clip_id="C01")

    prop = build_edit_plan_proposal(
        proposal_id="p1",
        manifest=manifest,
        video_plan=video_plan,
        creative_context=creative_context,
        source_evidence_by_clip={"C01": sn},
    )

    # Đột biến snapshot sau khi truyền
    sn["observed_full_sha256"] = SAMPLE_SHA256_B
    sn["freshness"]["file_size_bytes"] = 999999

    # Dữ liệu trong proposal không bị thay đổi
    c01_row = [c for c in prop.source_binding["clips"] if c["clip_id"] == "C01"][0]
    assert c01_row["expected_full_sha256"] == SAMPLE_SHA256_A


def test_t03_count_and_budget_bounds():
    manifest, video_plan, creative_context = _create_minimal_context_and_plans()

    # 1. Type error nếu không phải Mapping
    with pytest.raises(ValueError, match="ERR_INVALID_MAPPING"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip=["not", "a", "mapping"],
        )

    # 2. Vượt quá MAX_MANIFEST_CLIPS (4096)
    huge_map = {f"C{i}": {} for i in range(4097)}
    with pytest.raises(ValueError, match="ERR_COUNT_OVERFLOW"):
        build_edit_plan_proposal(
            proposal_id="p1",
            manifest=manifest,
            video_plan=video_plan,
            creative_context=creative_context,
            source_evidence_by_clip=huge_map,
        )


# =============================================================================
# T04: Revision & Semantic Signature
# =============================================================================

def test_t04_semantic_proof_signature_and_revision():
    manifest, video_plan, creative_context = _create_minimal_context_and_plans()
    sn_c01 = _create_valid_snapshot(clip_id="C01", sha=SAMPLE_SHA256_A, stream_index=0)

    # 1. Signature rỗng
    assert compute_proof_signature(None) == "empty_proof"
    assert compute_proof_signature({}) == "empty_proof"

    # 2. Signature nhất quán khi truyền cùng dữ liệu
    sig1 = compute_proof_signature({"C01": sn_c01})
    sig2 = compute_proof_signature({"C01": copy.deepcopy(sn_c01)})
    assert sig1 == sig2
    assert len(sig1) == 64

    # 3. Đổi SHA làm đổi signature
    sn_c01_alt = copy.deepcopy(sn_c01)
    sn_c01_alt["observed_full_sha256"] = SAMPLE_SHA256_B
    sig_alt = compute_proof_signature({"C01": sn_c01_alt})
    assert sig1 != sig_alt

    # 4. Proposal revision thay đổi khi có bằng chứng khác nhau
    p_unbound = build_edit_plan_proposal(
        proposal_id="p1",
        manifest=manifest,
        video_plan=video_plan,
        creative_context=creative_context,
    )
    p_bound_a = build_edit_plan_proposal(
        proposal_id="p1",
        manifest=manifest,
        video_plan=video_plan,
        creative_context=creative_context,
        source_evidence_by_clip={"C01": sn_c01},
    )
    p_bound_b = build_edit_plan_proposal(
        proposal_id="p1",
        manifest=manifest,
        video_plan=video_plan,
        creative_context=creative_context,
        source_evidence_by_clip={"C01": sn_c01_alt},
    )

    rev_unbound = compute_proposal_revision(p_unbound)
    rev_a = compute_proposal_revision(p_bound_a)
    rev_b = compute_proposal_revision(p_bound_b)

    assert rev_unbound != rev_a
    assert rev_a != rev_b


def test_r1_group_a_canonical_policy():
    from collections import UserDict
    from unittest.mock import patch

    snap = _create_valid_snapshot()

    # 1. corrupt_typed_fps_rejected
    bad_fps = copy.deepcopy(snap)
    fps = RationalFPS(30, 1)
    object.__setattr__(fps, "numerator", True)
    bad_fps["frame_evidence"]["source_fps"] = fps
    with pytest.raises(ValueError, match="ERR_INVALID_RATIONAL_FPS"):
        validate_and_normalize_source_evidence_snapshot(
            bad_fps, clip_id="C01", expected_manifest_id="mf_001", expected_path="/media/footage/c01.mp4"
        )

    # 2. invalid_utf8_path_rejected
    bad_path = copy.deepcopy(snap)
    bad_path["file_path"] = "/media/\ud800.mp4"
    with pytest.raises(ValueError, match="ERR_INVALID_UTF8_PATH"):
        validate_and_normalize_source_evidence_snapshot(
            bad_path, clip_id="C01", expected_manifest_id="mf_001", expected_path=bad_path["file_path"]
        )

    # 3. signature_nonmapping_rejected
    with pytest.raises(ValueError, match="ERR_INVALID_MAPPING"):
        compute_proof_signature([])

    # 4. signature_bad_entry_rejected
    with pytest.raises(ValueError, match="ERR_INVALID_EVIDENCE_ENTRY"):
        compute_proof_signature({"C01": 123})

    # 5. budget_custom_mapping_same_policy
    class ShortMap(UserDict):
        def __str__(self):
            return "tiny"

    manifest, vp, creative = _create_minimal_context_and_plans()
    with patch("src.core.proposal_builder.MAX_PROOF_METADATA_BYTES", 64):
        with pytest.raises(ValueError, match="ERR_PROOF_METADATA_BUDGET_EXCEEDED"):
            build_edit_plan_proposal(
                proposal_id="p", manifest=manifest, video_plan=vp, creative_context=creative, source_evidence_by_clip={"C01": snap}
            )
        with pytest.raises(ValueError, match="ERR_PROOF_METADATA_BUDGET_EXCEEDED"):
            build_edit_plan_proposal(
                proposal_id="p", manifest=manifest, video_plan=vp, creative_context=creative, source_evidence_by_clip=ShortMap({"C01": snap})
            )

    # 6. signed_freshness_mtime_ctime accepted
    neg_fresh = copy.deepcopy(snap)
    neg_fresh["freshness"]["mtime_ns"] = -5000000000
    neg_fresh["freshness"]["ctime_ns"] = -1000000000
    norm_neg = validate_and_normalize_source_evidence_snapshot(
        neg_fresh, clip_id="C01", expected_manifest_id="mf_001", expected_path="/media/footage/c01.mp4"
    )
    assert norm_neg["freshness"]["mtime_ns"] == -5000000000
    assert norm_neg["freshness"]["ctime_ns"] == -1000000000
