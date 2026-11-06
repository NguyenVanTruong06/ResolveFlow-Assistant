"""
tests/core/test_plan_demand_adapter.py
=============================================================================
Finite TDD Unit Test Suite cho G06-06B3-I1 (Plan Demand Adapter & Sidecar):
- Nhóm 1: Option validation (revision, top_k, allow_offline, plan mapping, manifest_id).
- Nhóm 2: Precheck structural limits (cap 4096, non-list arrays rejection).
- Nhóm 3: Demand ID canonical JSON compact array, namespace isolation, collision resistance.
- Nhóm 4: Canonical traversal order (broll then sfx) and index stability.
- Nhóm 5: Malformed entries and consumed scalar validation (strict numbers, lone surrogates).
- Nhóm 6: Search query precedence (description -> asset_file -> suggested_file), safe filename,
          unsafe path rejection, extension strip, raw hint retention, empty query rejection.
- Nhóm 7: Category resolution: canonical, aliases, custom categories, green_screen & unknown rejection.
- Nhóm 8: Duration placement metadata vs unconstrained request bounds.
- Nhóm 9: Quota 256 cap, overflow retention without quota leakage.
- Nhóm 10: Sidecar assembler: out-of-order join, strict 1-to-1 validation, echo validation,
           disjoint partition counters.
- Nhóm 11: End-to-end integration với real OfflineAssetMatchingService (injected provider),
           zero plan mutation.
=============================================================================
"""

import copy
import math
import pytest

from src.core.contracts.asset_matching_contracts import (
    OfflineAssetCandidate,
    OfflineAssetMatchRequest,
    OfflineAssetMatchResult,
)
from src.core.contracts.plan_demand_contracts import (
    AssetDemandBatch,
    AssetDemandItem,
    DemandMatchEntry,
    AssetMatchingSidecar,
)
from src.core.plan_demand_adapter import PlanDemandAdapter
from src.core.matching_service import OfflineAssetMatchingService


# =============================================================================
# Nhóm 1: Option & Root Validation
# =============================================================================

def test_extract_validates_options_even_when_plan_is_empty():
    # plan_revision validation
    with pytest.raises(ValueError, match="plan_revision"):
        PlanDemandAdapter.extract({}, plan_revision="")
    with pytest.raises(ValueError, match="plan_revision"):
        PlanDemandAdapter.extract({}, plan_revision="   ")
    with pytest.raises(ValueError, match="plan_revision"):
        PlanDemandAdapter.extract({}, plan_revision="\ud800")
    with pytest.raises(ValueError, match="plan_revision"):
        PlanDemandAdapter.extract({}, plan_revision=123)  # type: ignore

    # top_k validation
    with pytest.raises(ValueError, match="top_k"):
        PlanDemandAdapter.extract({}, plan_revision="rev1", top_k=0)
    with pytest.raises(ValueError, match="top_k"):
        PlanDemandAdapter.extract({}, plan_revision="rev1", top_k=21)
    with pytest.raises(ValueError, match="top_k"):
        PlanDemandAdapter.extract({}, plan_revision="rev1", top_k=True)  # type: ignore
    with pytest.raises(ValueError, match="top_k"):
        PlanDemandAdapter.extract({}, plan_revision="rev1", top_k=5.0)  # type: ignore

    # allow_offline validation
    with pytest.raises(ValueError, match="allow_offline"):
        PlanDemandAdapter.extract({}, plan_revision="rev1", allow_offline=1)  # type: ignore
    with pytest.raises(ValueError, match="allow_offline"):
        PlanDemandAdapter.extract({}, plan_revision="rev1", allow_offline="false")  # type: ignore

    # plan root mapping validation
    with pytest.raises(ValueError, match="plan"):
        PlanDemandAdapter.extract([], plan_revision="rev1")  # type: ignore
    with pytest.raises(ValueError, match="plan"):
        PlanDemandAdapter.extract(None, plan_revision="rev1")  # type: ignore

    # Happy path empty plan
    batch = PlanDemandAdapter.extract({}, plan_revision="rev1")
    assert isinstance(batch, AssetDemandBatch)
    assert batch.plan_revision == "rev1"
    assert batch.manifest_id is None
    assert batch.demands == ()
    assert batch.requests == ()


def test_manifest_id_validation_and_raw_retention():
    # Valid manifest_id
    batch = PlanDemandAdapter.extract({"manifest_id": "m_test_123"}, plan_revision="rev1")
    assert batch.manifest_id == "m_test_123"

    # None manifest_id
    batch2 = PlanDemandAdapter.extract({"manifest_id": None}, plan_revision="rev1")
    assert batch2.manifest_id is None

    # Invalid manifest_id
    with pytest.raises(ValueError, match="manifest_id"):
        PlanDemandAdapter.extract({"manifest_id": ""}, plan_revision="rev1")
    with pytest.raises(ValueError, match="manifest_id"):
        PlanDemandAdapter.extract({"manifest_id": "   "}, plan_revision="rev1")
    with pytest.raises(ValueError, match="manifest_id"):
        PlanDemandAdapter.extract({"manifest_id": 999}, plan_revision="rev1")
    with pytest.raises(ValueError, match="manifest_id"):
        PlanDemandAdapter.extract({"manifest_id": "\ud800"}, plan_revision="rev1")


# =============================================================================
# Nhóm 2: Precheck Structural Limits (Cap 4096, Non-list arrays)
# =============================================================================

def test_structural_insert_arrays_validation_and_cap_4096():
    # Non-list/tuple arrays
    with pytest.raises(ValueError, match="broll_inserts"):
        PlanDemandAdapter.extract({"broll_inserts": "not_a_list"}, plan_revision="rev1")
    with pytest.raises(ValueError, match="broll_inserts"):
        PlanDemandAdapter.extract({"broll_inserts": None}, plan_revision="rev1")
    with pytest.raises(ValueError, match="broll_inserts"):
        PlanDemandAdapter.extract({"broll_inserts": {"key": "val"}}, plan_revision="rev1")

    with pytest.raises(ValueError, match="sfx_inserts"):
        PlanDemandAdapter.extract({"sfx_inserts": 123}, plan_revision="rev1")
    with pytest.raises(ValueError, match="sfx_inserts"):
        PlanDemandAdapter.extract({"sfx_inserts": True}, plan_revision="rev1")

    # Exceeding 4096 total entries
    huge_broll = [{"asset_type": "meme", "description": "test"}] * 2049
    huge_sfx = [{"asset_type": "sfx", "description": "test"}] * 2048
    with pytest.raises(ValueError, match="4096"):
        PlanDemandAdapter.extract({"broll_inserts": huge_broll, "sfx_inserts": huge_sfx}, plan_revision="rev1")


# =============================================================================
# Nhóm 3 & 4: Demand ID Canonical Encoding & Traversal Order
# =============================================================================

def test_demand_id_json_compact_encoding_and_uniqueness():
    plan = {
        "broll_inserts": [{"asset_type": "meme", "description": "funny meme"}],
        "sfx_inserts": [{"asset_type": "sfx", "description": "loud boom"}]
    }
    # Delimiters and quotes in revision
    rev = 'rev:1,"quote"\n/special'
    batch = PlanDemandAdapter.extract(plan, plan_revision=rev)
    assert len(batch.demands) == 2

    id0 = batch.demands[0].demand_id
    id1 = batch.demands[1].demand_id
    assert id0 == '["rev:1,\\"quote\\"\\n/special","broll_inserts",0]'
    assert id1 == '["rev:1,\\"quote\\"\\n/special","sfx_inserts",0]'
    assert batch.requests[0].request_id == id0
    assert batch.requests[1].request_id == id1

    # Different revision produces strictly different IDs
    batch_b = PlanDemandAdapter.extract(plan, plan_revision="other_rev")
    assert batch_b.demands[0].demand_id != id0


def test_canonical_traversal_order_preserves_indices():
    plan = {
        "sfx_inserts": [
            {"asset_type": "sfx", "description": "sfx 0"},
            {"asset_type": "sfx", "description": "sfx 1"}
        ],
        "broll_inserts": [
            {"asset_type": "meme", "description": "meme 0"},
            {"asset_type": "broll", "description": "broll 1"}
        ]
    }
    # Regardless of dict key order, broll_inserts must precede sfx_inserts
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev1")
    assert len(batch.demands) == 4
    assert batch.demands[0].source_slot == "broll_inserts"
    assert batch.demands[0].source_index == 0
    assert batch.demands[1].source_slot == "broll_inserts"
    assert batch.demands[1].source_index == 1
    assert batch.demands[2].source_slot == "sfx_inserts"
    assert batch.demands[2].source_index == 0
    assert batch.demands[3].source_slot == "sfx_inserts"
    assert batch.demands[3].source_index == 1


# =============================================================================
# Nhóm 5: Malformed Entries & Consumed Scalar Validation
# =============================================================================

def test_malformed_entries_and_scalars_become_invalid_demands():
    plan = {
        "broll_inserts": [
            "not a mapping",  # 0: non-mapping entry
            {"asset_type": "meme", "description": 12345},  # 1: description wrong type
            {"asset_type": "meme", "description": "valid", "asset_file": None},  # 2: asset_file is null
            {"asset_type": "meme", "description": "\ud800"},  # 3: surrogate in description
            {"asset_type": "meme", "description": "valid", "timeline_sec": -1.0},  # 4: negative timeline
            {"asset_type": "meme", "description": "valid", "timeline_sec": "10.0"},  # 5: string timeline
            {"asset_type": "meme", "description": "valid", "timeline_sec": float("nan")},  # 6: nan timeline
            {"asset_type": "meme", "description": "valid", "duration_sec": 0.0},  # 7: zero duration
            {"asset_type": "meme", "description": "valid", "duration_sec": -2.5},  # 8: negative duration
            {"asset_type": "meme", "description": "valid", "duration_sec": True},  # 9: bool duration
            {"asset_type": "meme", "description": "valid", "timeline_sec": 10**400},  # 10: bigint overflow
            {"asset_type": "meme", "description": "good", "timeline_sec": 2.5, "duration_sec": 1.0}  # 11: good
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev1")
    assert len(batch.demands) == 12

    # First 11 are invalid
    for i in range(11):
        d = batch.demands[i]
        assert d.status == "invalid"
        assert d.rejection_reason == "ERR_DEMAND_INVALID"
        assert d.source_index == i

    # Last entry is eligible
    good_d = batch.demands[11]
    assert good_d.status == "eligible"
    assert good_d.query_text == "good"
    assert good_d.timeline_sec == 2.5
    assert good_d.desired_duration_sec == 1.0

    # Only 1 request generated
    assert len(batch.requests) == 1
    assert batch.requests[0].query_text == "good"


# =============================================================================
# Nhóm 6: Search Query Precedence & Safe Filename
# =============================================================================

def test_query_precedence_and_safe_filename():
    plan = {
        "broll_inserts": [
            # 0: Description present wins over asset_file
            {
                "asset_type": "meme",
                "description": "  Meme cười ngả nghiêng  ",
                "asset_file": "pewpew_cuoi.mp4"
            },
            # 1: Description empty -> fallback to asset_file stem
            {
                "asset_type": "meme",
                "description": "   ",
                "asset_file": "do_mixi_ao_that_day.mp4"
            },
            # 2: Description absent, asset_file empty -> fallback to suggested_file stem
            {
                "asset_type": "sfx",
                "asset_file": "",
                "suggested_file": "vine_boom_fast.wav"
            },
            # 3: Unsafe asset_file (slash) -> invalid demand (no basename fallback)
            {
                "asset_type": "meme",
                "description": "",
                "asset_file": "/var/tmp/hacked.mp4",
                "suggested_file": "safe_backup.mp4"
            },
            # 4: Unsafe asset_file (backslash) -> invalid demand
            {
                "asset_type": "meme",
                "asset_file": "c:\\windows\\system32.mp4"
            },
            # 5: Unsafe asset_file (URI) -> invalid demand
            {
                "asset_type": "meme",
                "asset_file": "http://evil.com/clip.mp4"
            },
            # 6: Unsafe asset_file (dot / dotdot) -> invalid demand
            {
                "asset_type": "meme",
                "asset_file": ".."
            },
            # 7: Both description and files empty -> empty_query
            {
                "asset_type": "meme",
                "description": "   ",
                "asset_file": ""
            }
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev1")
    demands = batch.demands

    # 0: Description wins
    assert demands[0].status == "eligible"
    assert demands[0].query_text == "Meme cười ngả nghiêng"
    assert demands[0].query_source == "description"
    assert demands[0].raw_query_hint == "  Meme cười ngả nghiêng  "

    # 1: asset_file stem wins, underscores to spaces
    assert demands[1].status == "eligible"
    assert demands[1].query_text == "do mixi ao that day"
    assert demands[1].query_source == "asset_file"
    assert demands[1].raw_query_hint == "do_mixi_ao_that_day.mp4"

    # 2: suggested_file stem wins
    assert demands[2].status == "eligible"
    assert demands[2].query_text == "vine boom fast"
    assert demands[2].query_source == "suggested_file"

    # 3, 4, 5, 6: Unsafe files become invalid
    assert demands[3].status == "invalid"
    assert demands[3].rejection_reason == "ERR_DEMAND_INVALID"
    assert demands[4].status == "invalid"
    assert demands[4].rejection_reason == "ERR_DEMAND_INVALID"
    assert demands[5].status == "invalid"
    assert demands[5].rejection_reason == "ERR_DEMAND_INVALID"
    assert demands[6].status == "invalid"
    assert demands[6].rejection_reason == "ERR_DEMAND_INVALID"

    # 7: Empty query
    assert demands[7].status == "empty_query"
    assert demands[7].rejection_reason == "ERR_DEMAND_QUERY_EMPTY"


# =============================================================================
# Nhóm 7: Category Resolution & Green Screen Rejection
# =============================================================================

def test_category_resolution_aliases_custom_and_green_screen():
    plan = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "m1"},
            {"asset_type": "overlay", "description": "o1"},
            {"asset_type": "broll", "description": "b1"},
            {"asset_type": "text", "description": "t1"},
            {"asset_type": "trans", "description": "tr1"},
            {"asset_type": "lut", "description": "l1"},
            {"asset_type": "custom_hud_fx", "description": "c1"},
            {"asset_type": "green_screen", "description": "gs1"},
            {"asset_type": "Invalid Category Name!", "description": "w1"},
            {"description": "missing_type"}  # missing asset_type
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev1")
    demands = batch.demands
    requests = {r.request_id: r for r in batch.requests}

    # meme -> memes
    assert demands[0].category == "memes"
    assert requests[demands[0].demand_id].category == "memes"

    # overlay -> overlays
    assert demands[1].category == "overlays"
    assert requests[demands[1].demand_id].category == "overlays"

    # broll -> broll
    assert demands[2].category == "broll"
    assert requests[demands[2].demand_id].category == "broll"

    # text -> templates, template_type="text"
    assert demands[3].category == "templates"
    assert requests[demands[3].demand_id].category == "templates"
    assert requests[demands[3].demand_id].template_type == "text"

    # trans -> templates, template_type="trans"
    assert demands[4].category == "templates"
    assert requests[demands[4].demand_id].category == "templates"
    assert requests[demands[4].demand_id].template_type == "trans"

    # lut -> luts
    assert demands[5].category == "luts"
    assert requests[demands[5].demand_id].category == "luts"

    # custom_hud_fx -> custom_hud_fx
    assert demands[6].category == "custom_hud_fx"
    assert requests[demands[6].demand_id].category == "custom_hud_fx"

    # green_screen -> unsupported
    assert demands[7].status == "unsupported"
    assert demands[7].rejection_reason == "ERR_UNSUPPORTED_CATEGORY"

    # weird_unknown_category -> unsupported
    assert demands[8].status == "unsupported"
    assert demands[8].rejection_reason == "ERR_UNSUPPORTED_CATEGORY"

    # missing asset_type -> unsupported
    assert demands[9].status == "unsupported"
    assert demands[9].rejection_reason == "ERR_UNSUPPORTED_CATEGORY"


# =============================================================================
# Nhóm 8: Duration Metadata vs Unconstrained Request Bounds
# =============================================================================

def test_duration_placement_metadata_vs_unconstrained_request():
    plan = {
        "broll_inserts": [
            {"asset_type": "broll", "description": "city", "timeline_sec": 12.0, "duration_sec": 3.5},
            {"asset_type": "broll", "description": "nature"}  # absent numbers
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev1")
    d0, d1 = batch.demands

    assert d0.timeline_sec == 12.0
    assert d0.desired_duration_sec == 3.5

    assert d1.timeline_sec is None
    assert d1.desired_duration_sec is None

    # Both requests must have min_duration_sec and max_duration_sec as None
    for req in batch.requests:
        assert req.min_duration_sec is None
        assert req.max_duration_sec is None


# =============================================================================
# Nhóm 9: Quota 256 Cap & Overflow Handling
# =============================================================================

def test_quota_256_cap_and_overflow_handling():
    # 270 eligible items + 5 invalid items + 5 unsupported items
    items = []
    # Add 5 invalid items first
    for i in range(5):
        items.append({"asset_type": "meme", "description": 12345})
    # Add 5 unsupported items
    for i in range(5):
        items.append({"asset_type": "green_screen", "description": f"gs {i}"})
    # Add 270 eligible items
    for i in range(270):
        items.append({"asset_type": "meme", "description": f"meme {i}"})

    plan = {"broll_inserts": items}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev1")

    assert len(batch.demands) == 280
    assert len(batch.requests) == 256  # strictly capped at 256

    # Verify invalid and unsupported items did not consume quota
    invalid_count = sum(1 for d in batch.demands if d.status == "invalid")
    unsupported_count = sum(1 for d in batch.demands if d.status == "unsupported")
    eligible_count = sum(1 for d in batch.demands if d.status == "eligible")
    overflow_count = sum(1 for d in batch.demands if d.status == "overflow")

    assert invalid_count == 5
    assert unsupported_count == 5
    assert eligible_count == 256
    assert overflow_count == 14

    # Verify overflow items have proper reason
    overflow_items = [d for d in batch.demands if d.status == "overflow"]
    assert len(overflow_items) == 14
    for o in overflow_items:
        assert o.rejection_reason == "ERR_BATCH_CAP_OVERFLOW"


# =============================================================================
# Nhóm 10: Sidecar Assembler & Strict Revalidation
# =============================================================================

def test_assembler_strict_validation_and_out_of_order_join():
    plan = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "meme A"},
            {"asset_type": "green_screen", "description": "gs B"},  # unsupported
            {"asset_type": "sfx", "description": "sfx C"}
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev1")
    assert len(batch.demands) == 3
    assert len(batch.requests) == 2

    req_a = batch.requests[0]
    req_c = batch.requests[1]

    cand = OfflineAssetCandidate(
        asset_id="a1", name="Clip A", category="memes", subcategory="humor",
        source_kind="linked", availability="online", match_score=85,
        matched_signals=("name_token",), duration_sec=5.0, pack_id=None, pack_name="P1"
    )

    res_a = OfflineAssetMatchResult(
        request_id=req_a.request_id,
        category=req_a.category,
        query_text=req_a.query_text,
        candidates=(cand,),
        is_matched=True
    )
    res_c = OfflineAssetMatchResult(
        request_id=req_c.request_id,
        category=req_c.category,
        query_text=req_c.query_text,
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_MATCH_FOUND"
    )

    # Positive test: provide results out of order (res_c first, then res_a)
    sidecar = PlanDemandAdapter.assemble(batch, [res_c, res_a])
    assert isinstance(sidecar, AssetMatchingSidecar)
    assert sidecar.total_demands == 3
    assert sidecar.matched_count == 1
    assert sidecar.unmatched_count == 1
    assert sidecar.unsupported_count == 1
    assert sidecar.invalid_count == 0
    assert sidecar.empty_query_count == 0
    assert sidecar.overflow_count == 0

    # Ensure canonical demand order is preserved in entries
    assert sidecar.entries[0].demand.demand_id == req_a.request_id
    assert sidecar.entries[0].is_matched is True
    assert len(sidecar.entries[0].candidates) == 1

    assert sidecar.entries[1].demand.status == "unsupported"
    assert sidecar.entries[1].result is None
    assert sidecar.entries[1].unmatched_reason == "ERR_UNSUPPORTED_CATEGORY"

    assert sidecar.entries[2].demand.demand_id == req_c.request_id
    assert sidecar.entries[2].is_matched is False
    assert sidecar.entries[2].unmatched_reason == "ERR_NO_MATCH_FOUND"

    # Partition disjoint counter sum check
    assert (sidecar.matched_count + sidecar.unmatched_count + sidecar.invalid_count +
            sidecar.empty_query_count + sidecar.unsupported_count + sidecar.overflow_count == sidecar.total_demands)

    # Assembler rejection tests
    # 1. Missing results count
    with pytest.raises(ValueError, match="results"):
        PlanDemandAdapter.assemble(batch, [res_a])

    # 2. Duplicate results
    with pytest.raises(ValueError, match="Trùng lặp"):
        PlanDemandAdapter.assemble(batch, [res_a, res_a])

    # 3. Unknown request_id
    res_unknown = OfflineAssetMatchResult(
        request_id="unknown_req_id", category="memes", query_text="x",
        candidates=(), is_matched=False, unmatched_reason="ERR_NO_MATCH_FOUND"
    )
    with pytest.raises(ValueError, match="không xác định"):
        PlanDemandAdapter.assemble(batch, [res_a, res_unknown])

    # 4. Category echo mismatch
    res_bad_cat = OfflineAssetMatchResult(
        request_id=req_c.request_id, category="memes",  # mismatch with sfx
        query_text=req_c.query_text, candidates=(), is_matched=False,
        unmatched_reason="ERR_NO_MATCH_FOUND"
    )
    with pytest.raises(ValueError, match="Category"):
        PlanDemandAdapter.assemble(batch, [res_a, res_bad_cat])

    # 5. Query echo mismatch
    res_bad_query = OfflineAssetMatchResult(
        request_id=req_c.request_id, category=req_c.category,
        query_text="tampered query", candidates=(), is_matched=False,
        unmatched_reason="ERR_NO_MATCH_FOUND"
    )
    with pytest.raises(ValueError, match="query_text"):
        PlanDemandAdapter.assemble(batch, [res_a, res_bad_query])


# =============================================================================
# Nhóm 11: End-to-end Integration với Service Injected Provider
# =============================================================================

def test_end_to_end_service_integration_and_zero_plan_mutation():
    raw_plan = {
        "manifest_id": "m_e2e_456",
        "broll_inserts": [
            {"asset_type": "meme", "description": "Alpha Sunset Clip", "timeline_sec": 1.0, "duration_sec": 2.5},
            {"asset_type": "sfx", "description": "Thunder Sound Effect", "timeline_sec": 3.0, "duration_sec": 1.0},
            {"asset_type": "green_screen", "description": "Explosion FX", "timeline_sec": 5.0}
        ],
        "sfx_inserts": [
            {"asset_type": "sfx", "description": "Unknown Alien Voice", "timeline_sec": 7.0}
        ]
    }
    raw_plan_copy = copy.deepcopy(raw_plan)

    catalog_data = [
        {
            "schema_version": 1,
            "id": "clip_alpha",
            "name": "Alpha Sunset Clip Full HD",
            "category": "memes",
            "subcategory": "sunsets",
            "source_kind": "managed",
            "availability": "online",
            "size_bytes": 1024 * 1024,
            "duration_sec": 15.0,
            "relative_path": "memes/alpha.mp4",
            "library_path": "02_cloud_downloads/packs/pack_1/memes/alpha.mp4",
            "pack_id": "pack_1",
            "pack_name": "Pack 1"
        },
        {
            "schema_version": 1,
            "id": "sfx_thunder",
            "name": "Thunder Sound Effect HQ",
            "category": "sfx",
            "subcategory": "weather",
            "source_kind": "managed",
            "availability": "online",
            "size_bytes": 1024 * 1024,
            "duration_sec": 2.0,
            "relative_path": "sfx/thunder.wav",
            "library_path": "02_cloud_downloads/packs/pack_2/sfx/thunder.wav",
            "pack_id": "pack_2",
            "pack_name": "Pack 2"
        }
    ]

    service = OfflineAssetMatchingService(catalog_provider=lambda: catalog_data)

    # 1. Extract
    batch = PlanDemandAdapter.extract(raw_plan, plan_revision="rev_e2e_01")
    assert len(batch.demands) == 4
    assert len(batch.requests) == 3  # green_screen excluded

    # 2. Match via real Service
    results = service.match_batch(batch.requests)
    assert len(results) == 3

    # 3. Assemble
    sidecar = PlanDemandAdapter.assemble(batch, results)

    # 4. Assert sidecar correctness
    assert sidecar.total_demands == 4
    assert sidecar.matched_count == 2
    assert sidecar.unmatched_count == 1  # Unknown Alien Voice
    assert sidecar.unsupported_count == 1  # green_screen
    assert sidecar.invalid_count == 0

    # 5. ZERO MUTATION check
    assert raw_plan == raw_plan_copy



# =============================================================================
# Nhóm 12: Codex R1 Counterexample Probe Tests (A, B, C, D)
# =============================================================================

def test_probe_bigint_overflow_safe_numeric_keeps_siblings_alive():
    """Probe A.1: 10**400 không làm văng OverflowError; trở thành invalid demand, sibling sống."""
    plan = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "huge", "duration_sec": 10**400},
            {"asset_type": "meme", "description": "huge timeline", "timeline_sec": 10**400},
            {"asset_type": "meme", "description": "good sibling", "duration_sec": 2.5}
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_num_01")
    assert len(batch.demands) == 3
    assert batch.demands[0].status == "invalid"
    assert batch.demands[0].rejection_reason == "ERR_DEMAND_INVALID"
    assert batch.demands[1].status == "invalid"
    assert batch.demands[1].rejection_reason == "ERR_DEMAND_INVALID"

    # Sibling hợp lệ vẫn sống và tạo request bình thường
    assert batch.demands[2].status == "eligible"
    assert batch.demands[2].desired_duration_sec == 2.5
    assert len(batch.requests) == 1
    assert batch.requests[0].query_text == "good sibling"


def test_probe_finite_unapproved_cap_accepts_1e19():
    """Probe A.2: Số hữu hạn 1e19 được chấp nhận (loại bỏ cap 10**18 không được giao)."""
    plan = {
        "broll_inserts": [
            {"asset_type": "broll", "description": "epic shot", "timeline_sec": 1e19, "duration_sec": 1e19}
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_num_02")
    assert len(batch.demands) == 1
    d0 = batch.demands[0]
    assert d0.status == "eligible"
    assert d0.timeline_sec == 1e19
    assert d0.desired_duration_sec == 1e19
    assert len(batch.requests) == 1


def test_probe_wrapper_envelopes_rejected_with_controlled_value_error():
    """Probe B.1: Chặn đứng các known envelopes (dual, kinetic, overview, batch draft) thay vì trả 0 demands."""
    # Dual plan wrapper
    with pytest.raises(ValueError, match="wrapper envelope"):
        PlanDemandAdapter.extract({"type": "dual_plan", "video_plan": {"broll_inserts": []}}, plan_revision="rev1")
    with pytest.raises(ValueError, match="dual plan"):
        PlanDemandAdapter.extract({"video_plan": {}, "kinetic_plan": {}}, plan_revision="rev1")

    # Kinetic plan wrapper
    with pytest.raises(ValueError, match="wrapper envelope"):
        PlanDemandAdapter.extract({"type": "kinetic_plan", "subtitles": []}, plan_revision="rev1")

    # Generic plan wrapper
    with pytest.raises(ValueError, match="wrapper envelope"):
        PlanDemandAdapter.extract({"type": "generic_plan", "data": {}}, plan_revision="rev1")

    # Project overview wrapper
    with pytest.raises(ValueError, match="wrapper envelope"):
        PlanDemandAdapter.extract({"type": "project_overview", "scenes": []}, plan_revision="rev1")

    # Batch draft wrapper
    with pytest.raises(ValueError, match="batch draft"):
        PlanDemandAdapter.extract({"batch_id": "b1", "items": []}, plan_revision="rev1")
    with pytest.raises(ValueError, match="batch draft"):
        PlanDemandAdapter.extract({"batch_draft": {}}, plan_revision="rev1")


def test_probe_nonblank_filename_stem_empty_does_not_fallback_to_suggested_file():
    """Probe B.2: asset_file nonblank nhưng stem rỗng -> empty_query, không fallback sang suggested_file."""
    plan = {
        "broll_inserts": [
            # asset_file = "___" -> stem "" -> KHÔNG fallback sang suggested_file
            {
                "asset_type": "meme",
                "asset_file": "___",
                "suggested_file": "fallback.mp4"
            },
            # asset_file = ".mp4" -> stem "" -> KHÔNG fallback
            {
                "asset_type": "meme",
                "asset_file": ".mp4",
                "suggested_file": "fallback.mp4"
            },
            # asset_file khuyết hoặc hoàn toàn rỗng -> MỚI ĐƯỢC fallback sang suggested_file
            {
                "asset_type": "meme",
                "asset_file": "   ",
                "suggested_file": "valid_fallback.mp4"
            }
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev1")
    assert len(batch.demands) == 3

    # Mục 0 và 1 phải là empty_query, không có request
    assert batch.demands[0].status == "empty_query"
    assert batch.demands[0].rejection_reason == "ERR_DEMAND_QUERY_EMPTY"
    assert batch.demands[1].status == "empty_query"
    assert batch.demands[1].rejection_reason == "ERR_DEMAND_QUERY_EMPTY"

    # Mục 2 khuyết/trắng mới được fallback sang suggested_file
    assert batch.demands[2].status == "eligible"
    assert batch.demands[2].query_text == "valid fallback"
    assert batch.demands[2].query_source == "suggested_file"
    assert len(batch.requests) == 1


def test_probe_batch_revision_mismatch_in_assemble():
    """Probe C.1: assemble phát hiện lệch revision giữa batch và demand_id."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_A")

    # 1. Constructor trực tiếp từ chối mismatch
    with pytest.raises(ValueError, match="không khớp mã hóa chuẩn tắc mong đợi"):
        AssetDemandBatch(
            plan_revision="rev_B",
            manifest_id=batch.manifest_id,
            demands=batch.demands,
            requests=batch.requests
        )

    # 2. Giả mạo revision trên frozen object cũng bị assemble phát hiện và từ chối
    tampered_batch = copy.copy(batch)
    object.__setattr__(tampered_batch, "plan_revision", "rev_B")

    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category="memes",
        query_text="clip A",
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_MATCH_FOUND"
    )
    with pytest.raises(ValueError, match="không khớp mã hóa chuẩn tắc mong đợi"):
        PlanDemandAdapter.assemble(tampered_batch, [res])


def test_probe_duplicated_demands_in_assemble():
    """Probe C.2: assemble phát hiện trùng lặp demand trong batch."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "clip A"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_C")

    # Giả mạo demands có 2 phần tử giống nhau
    tampered_demands = (batch.demands[0], batch.demands[0])
    with pytest.raises(ValueError, match="Trùng lặp demand_id"):
        AssetDemandBatch(
            plan_revision="rev_C",
            manifest_id=None,
            demands=tampered_demands,
            requests=batch.requests
        )


def test_probe_dropped_eligible_demand_in_assemble():
    """Probe C.3: assemble phát hiện eligible demand bị mất hoặc request thừa."""
    plan = {
        "broll_inserts": [
            {"asset_type": "meme", "description": "clip 0"},
            {"asset_type": "meme", "description": "clip 1"}
        ]
    }
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_D")
    assert len(batch.demands) == 2
    assert len(batch.requests) == 2

    # Giả mạo batch chỉ giữ demand[0] nhưng requests vẫn có 2
    with pytest.raises(ValueError, match="không khớp"):
        AssetDemandBatch(
            plan_revision="rev_D",
            manifest_id=None,
            demands=(batch.demands[0],),
            requests=batch.requests
        )


def test_probe_batch_typed_bad_item_in_assemble():
    """Probe C.4: assemble phát hiện demand item sai kiểu (None / non-AssetDemandItem)."""
    with pytest.raises(ValueError, match="AssetDemandItem"):
        AssetDemandBatch(
            plan_revision="rev_E",
            manifest_id=None,
            demands=(None,),  # type: ignore
            requests=()
        )


def test_probe_batch_unknown_status_partition_rejected():
    """Probe C.5: demand mang status lạ bị reject ngay lập tức."""
    with pytest.raises(ValueError, match="status"):
        AssetDemandItem(
            demand_id='["rev","broll_inserts",0]',
            source_slot="broll_inserts",
            source_index=0,
            status="weird_status"
        )


def test_probe_result_bad_id_list_rejected_without_type_error():
    """Probe D.1: result có request_id là list ném ValueError controlled thay vì TypeError."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "test"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_F")

    # Tạo result thật có request_id bị mutate thành []
    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category="memes",
        query_text="test",
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_MATCH_FOUND"
    )
    object.__setattr__(res, "request_id", [])

    with pytest.raises(ValueError, match="request_id trong result bắt buộc phải là chuỗi UTF-8"):
        PlanDemandAdapter.assemble(batch, [res])


def test_probe_result_nonbool_is_matched_rejected():
    """Probe D.2: is_matched=0 (int) bị reject bắt buộc strict bool."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "test"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_G")

    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category="memes",
        query_text="test",
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_MATCH_FOUND"
    )
    # Giả mạo is_matched thành 0 (int) qua object.__setattr__
    object.__setattr__(res, "is_matched", 0)

    with pytest.raises(ValueError, match="bool thuần"):
        PlanDemandAdapter.assemble(batch, [res])


def test_probe_result_mutated_candidates_rejected():
    """Probe D.3: candidates chứa object lạ hoặc scalar lỗi bị reject."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "test"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_H")

    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category="memes",
        query_text="test",
        candidates=(object(),),  # type: ignore
        is_matched=True,
        unmatched_reason=None
    )

    with pytest.raises(ValueError, match="OfflineAssetCandidate"):
        PlanDemandAdapter.assemble(batch, [res])


# =============================================================================
# Nhóm R2: Concrete Residual Probes (A, B, C, D)
# =============================================================================

def test_probe_r2_bool_placement_in_demand_item_rejected():
    """Probe R2: timeline_sec=True hoặc desired_duration_sec=True bị reject."""
    with pytest.raises(ValueError, match="boolean"):
        AssetDemandItem(
            demand_id='["rev","broll_inserts",0]',
            source_slot="broll_inserts",
            source_index=0,
            status="eligible",
            category="memes",
            timeline_sec=True  # type: ignore
        )

    with pytest.raises(ValueError, match="boolean"):
        AssetDemandItem(
            demand_id='["rev","broll_inserts",0]',
            source_slot="broll_inserts",
            source_index=0,
            status="eligible",
            category="memes",
            desired_duration_sec=True  # type: ignore
        )


def test_probe_r2_rejection_reason_mismatch_rejected():
    """Probe R2: status='eligible' nhưng có rejection_reason bị reject."""
    with pytest.raises(ValueError, match="rejection_reason bắt buộc phải là None"):
        AssetDemandItem(
            demand_id='["rev","broll_inserts",0]',
            source_slot="broll_inserts",
            source_index=0,
            status="eligible",
            category="memes",
            rejection_reason="ERR_DEMAND_INVALID"
        )


def test_probe_r2_request_unhashable_id_rejected_as_value_error():
    """Probe R2: batch.requests có request_id=[] bị reject thành ValueError, không ném TypeError."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "test"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_R2_A")

    req = batch.requests[0]
    object.__setattr__(req, "request_id", [])  # type: ignore

    res = OfflineAssetMatchResult(
        request_id='["rev_R2_A","broll_inserts",0]',
        category="memes",
        query_text="test",
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_MATCH_FOUND"
    )

    with pytest.raises(ValueError, match="request_id"):
        PlanDemandAdapter.assemble(batch, [res])


def test_probe_r2_request_bool_topk_rejected():
    """Probe R2: batch.requests có top_k=False bị reject thành ValueError."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "test"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_R2_B")

    req = batch.requests[0]
    object.__setattr__(req, "top_k", False)

    res = OfflineAssetMatchResult(
        request_id=req.request_id,
        category="memes",
        query_text="test",
        candidates=(),
        is_matched=False,
        unmatched_reason="ERR_NO_MATCH_FOUND"
    )

    with pytest.raises(ValueError, match="top_k"):
        PlanDemandAdapter.assemble(batch, [res])


def test_probe_r2_candidate_availability_strict_online_offline_only():
    """Probe R2: candidate availability chỉ nhận 'online' hoặc 'offline', reject missing/staging/unknown."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "test"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_R2_C")

    cand_missing = OfflineAssetCandidate(
        asset_id="asset_1",
        name="Asset 1",
        category="memes",
        subcategory="reaction",
        source_kind="managed",
        availability="missing",  # Không được nhận
        match_score=90,
        matched_signals=("test",),
        duration_sec=5.0,
        pack_id=None,
        pack_name="Pack 1"
    )

    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category="memes",
        query_text="test",
        candidates=(cand_missing,),
        is_matched=True,
        unmatched_reason=None
    )

    with pytest.raises(ValueError, match="availability"):
        PlanDemandAdapter.assemble(batch, [res])

    # staging cũng bị reject
    object.__setattr__(cand_missing, "availability", "staging")
    with pytest.raises(ValueError, match="availability"):
        PlanDemandAdapter.assemble(batch, [res])

    # unknown cũng bị reject
    object.__setattr__(cand_missing, "availability", "unknown")
    with pytest.raises(ValueError, match="availability"):
        PlanDemandAdapter.assemble(batch, [res])

    # online và offline được chấp nhận
    object.__setattr__(cand_missing, "availability", "online")
    sidecar_online = PlanDemandAdapter.assemble(batch, [res])
    assert sidecar_online.matched_count == 1

    object.__setattr__(cand_missing, "availability", "offline")
    sidecar_offline = PlanDemandAdapter.assemble(batch, [res])
    assert sidecar_offline.matched_count == 1


def test_probe_r2_candidate_duration_zero_and_overflow_rejected():
    """Probe R2: candidate duration_sec=0.0 hoặc 10**400 bị reject an toàn (không OverflowError)."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "test"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_R2_D")

    cand = OfflineAssetCandidate(
        asset_id="asset_2",
        name="Asset 2",
        category="memes",
        subcategory="reaction",
        source_kind="managed",
        availability="online",
        match_score=90,
        matched_signals=("test",),
        duration_sec=0.0,  # 0.0 bị từ chối, phải > 0.0
        pack_id=None,
        pack_name="Pack 1"
    )

    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category="memes",
        query_text="test",
        candidates=(cand,),
        is_matched=True,
        unmatched_reason=None
    )

    with pytest.raises(ValueError, match="duration_sec"):
        PlanDemandAdapter.assemble(batch, [res])

    # 10**400 ném ValueError có kiểm soát, không ném OverflowError
    object.__setattr__(cand, "duration_sec", 10**400)
    with pytest.raises(ValueError, match="tràn số"):
        PlanDemandAdapter.assemble(batch, [res])


def test_probe_r2_candidate_utf8_lone_surrogate_rejected():
    """Probe R2: candidate chứa chuỗi lone surrogate bị reject."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "test"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_R2_E")

    cand = OfflineAssetCandidate(
        asset_id="\ud800",  # Lone surrogate
        name="Asset Surrogate",
        category="memes",
        subcategory="reaction",
        source_kind="managed",
        availability="online",
        match_score=90,
        matched_signals=("test",),
        duration_sec=5.0,
        pack_id=None,
        pack_name="Pack 1"
    )

    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category="memes",
        query_text="test",
        candidates=(cand,),
        is_matched=True,
        unmatched_reason=None
    )

    with pytest.raises(ValueError, match="asset_id"):
        PlanDemandAdapter.assemble(batch, [res])


def test_probe_r2_candidate_mutable_signals_and_pack_name_rejected():
    """Probe R2: candidate có matched_signals list hoặc pack_name list bị reject."""
    plan = {"broll_inserts": [{"asset_type": "meme", "description": "test"}]}
    batch = PlanDemandAdapter.extract(plan, plan_revision="rev_R2_F")

    cand = OfflineAssetCandidate(
        asset_id="asset_4",
        name="Asset 4",
        category="memes",
        subcategory="reaction",
        source_kind="managed",
        availability="online",
        match_score=90,
        matched_signals=("test",),
        duration_sec=5.0,
        pack_id=None,
        pack_name="Pack 1"
    )

    res = OfflineAssetMatchResult(
        request_id=batch.requests[0].request_id,
        category="memes",
        query_text="test",
        candidates=(cand,),
        is_matched=True,
        unmatched_reason=None
    )

    # matched_signals là list
    object.__setattr__(cand, "matched_signals", ["test"])
    with pytest.raises(ValueError, match="matched_signals"):
        PlanDemandAdapter.assemble(batch, [res])

    # pack_name là list
    object.__setattr__(cand, "matched_signals", ("test",))
    object.__setattr__(cand, "pack_name", ["Pack 1"])
    with pytest.raises(ValueError, match="pack_name"):
        PlanDemandAdapter.assemble(batch, [res])


def test_probe_r2_raw_video_envelope_rejected():
    """Probe R2: {'type': 'video_plan', 'data': ...} và project_overview_draft bị reject."""
    envelope_data = {
        "type": "video_plan",
        "data": {
            "broll_inserts": [{"asset_type": "meme", "description": "test"}]
        }
    }
    with pytest.raises(ValueError, match="video_plan.*data"):
        PlanDemandAdapter.extract(envelope_data, plan_revision="rev_R2_G")

    envelope_draft = {
        "kind": "project_overview_draft",
        "broll_inserts": [{"asset_type": "meme", "description": "test"}]
    }
    with pytest.raises(ValueError, match="project_overview_draft"):
        PlanDemandAdapter.extract(envelope_draft, plan_revision="rev_R2_G")

    envelope_schema = {
        "$schema": "chundvc_batch_draft_v1",
        "broll_inserts": [{"asset_type": "meme", "description": "test"}]
    }
    with pytest.raises(ValueError, match="batch_draft"):
        PlanDemandAdapter.extract(envelope_schema, plan_revision="rev_R2_G")

    # Positive control: raw video plan có type='video_plan' nhưng trực tiếp chứa inserts (không có 'data')
    positive_raw = {
        "type": "video_plan",
        "broll_inserts": [{"asset_type": "meme", "description": "test"}]
    }
    batch_pos = PlanDemandAdapter.extract(positive_raw, plan_revision="rev_R2_G")
    assert len(batch_pos.demands) == 1
    assert batch_pos.demands[0].status == "eligible"

    # Positive control: raw empty dict {}
    batch_empty = PlanDemandAdapter.extract({}, plan_revision="rev_R2_G")
    assert len(batch_empty.demands) == 0
    assert len(batch_empty.requests) == 0


def test_probe_r2_unknown_status_without_request_rejected():
    """Probe R2: demand có status lạ bị reject, partition sum lệch bị reject."""
    with pytest.raises(ValueError, match="status"):
        AssetDemandItem(
            demand_id='["rev","broll_inserts",0]',
            source_slot="broll_inserts",
            source_index=0,
            status="weird_status"
        )

    # Nếu tạo demand hợp lệ rồi mutate status thành 'weird_status' qua object.__setattr__
    item = AssetDemandItem(
        demand_id='["rev_R2_H","broll_inserts",0]',
        source_slot="broll_inserts",
        source_index=0,
        status="invalid",
        rejection_reason="ERR_DEMAND_INVALID"
    )
    object.__setattr__(item, "status", "weird_status")

    with pytest.raises(ValueError, match="status"):
        AssetDemandBatch(
            plan_revision="rev_R2_H",
            manifest_id=None,
            demands=(item,),
            requests=()
        )

