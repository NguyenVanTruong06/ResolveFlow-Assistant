"""Kiểm thử Hợp đồng DTO Bất biến Biên bản Tiền Đột biến & Nhật ký (G07-07A6-I)."""

import pytest

from src.core.contracts.execution_receipt_contracts import (
    MAX_RECEIPT_BYTES,
    MAX_LINE_BYTES,
    SCHEMA_VERSION,
    VALID_RECEIPT_STATES,
    CatalogAssetDigestBinding,
    ExecutionReceipt,
    JournalEntry,
    SourceFileDigestBinding,
    TargetTrackReservation,
    compute_idempotency_key,
    journal_entry_to_json_line,
    receipt_from_json,
    receipt_to_json,
)


def _sample_source_digest():
    return SourceFileDigestBinding(
        clip_id="C01",
        absolute_path="/media/footage/cam_a.mp4",
        full_sha256="a" * 64,
        stream_index=0,
        stat_identity=(2049, 123456, 1048576, 1699999999, 1699999999),
    )


def _sample_catalog_digest():
    return CatalogAssetDigestBinding(
        asset_id="asset_cat_01",
        absolute_path="/assets/broll/cat.mp4",
        full_sha256="b" * 64,
        stat_identity=(2049, 654321, 2097152, 1699999999, 1699999999),
    )


def _sample_track_reservation():
    return TargetTrackReservation(
        track_type="video",
        track_index=1,
        reserved_start_frame=0,
        reserved_end_frame=180,
        intended_usage="V1_CUTS",
    )


def _sample_receipt(execution_id="exec_test_001"):
    ik = compute_idempotency_key(
        proposal_id="prop_01",
        proposal_revision="1" * 64,
        approval_id="appr_01",
        project_id="proj_uid_123",
        timeline_id="tl_uid_456",
    )
    return ExecutionReceipt(
        execution_id=execution_id,
        idempotency_key=ik,
        proposal_id="prop_01",
        proposal_revision="1" * 64,
        approval_id="appr_01",
        approved_at_iso="2026-11-02T14:00:00Z",
        project_name="MyProject",
        project_id="proj_uid_123",
        timeline_name="Timeline 1",
        timeline_id="tl_uid_456",
        timeline_fps_numerator=30,
        timeline_fps_denominator=1,
        timeline_resolution=(1920, 1080),
        source_digests=(_sample_source_digest(),),
        catalog_digests=(_sample_catalog_digest(),),
        track_reservations=(_sample_track_reservation(),),
        planned_actions_count=2,
        created_at_iso="2026-11-02T14:00:01Z",
        state="INITIALIZED",
    )


def test_compute_idempotency_key_deterministic():
    rev1 = "1" * 64
    rev2 = "2" * 64
    k1 = compute_idempotency_key("prop1", rev1, "appr1", "proj1", "tl1")
    k2 = compute_idempotency_key("prop1", rev1, "appr1", "proj1", "tl1")
    assert k1 == k2
    assert len(k1) == 64

    # Any change produces a distinct key
    k3 = compute_idempotency_key("prop1", rev2, "appr1", "proj1", "tl1")
    assert k1 != k3


def test_compute_idempotency_key_rejects_invalid_inputs():
    valid_rev = "1" * 64
    with pytest.raises(ValueError, match="ERR_BLANK_FIELD: proposal_id"):
        compute_idempotency_key("", valid_rev, "appr1", "proj1", "tl1")

    with pytest.raises(ValueError, match="ERR_INVALID_TYPE: proposal_revision"):
        compute_idempotency_key("prop1", True, "appr1", "proj1", "tl1")  # bool rejected

    with pytest.raises(ValueError, match="ERR_INVALID_TYPE: proposal_revision"):
        compute_idempotency_key("prop1", 1, "appr1", "proj1", "tl1")  # int rejected

    with pytest.raises(ValueError, match="ERR_INVALID_PROPOSAL_REVISION"):
        compute_idempotency_key("prop1", "1" * 63, "appr1", "proj1", "tl1")  # too short

    with pytest.raises(ValueError, match="ERR_INVALID_PROPOSAL_REVISION"):
        compute_idempotency_key("prop1", "A" * 64, "appr1", "proj1", "tl1")  # uppercase rejected

    with pytest.raises(ValueError, match="ERR_INVALID_PROPOSAL_REVISION"):
        compute_idempotency_key("prop1", "g" * 64, "appr1", "proj1", "tl1")  # non-hex rejected


def test_receipt_rejects_invalid_proposal_revision():
    r = _sample_receipt()
    # Reject int
    with pytest.raises(ValueError, match="ERR_INVALID_TYPE: proposal_revision"):
        ExecutionReceipt(
            execution_id=r.execution_id,
            idempotency_key=r.idempotency_key,
            proposal_id=r.proposal_id,
            proposal_revision=1,  # int
            approval_id=r.approval_id,
            approved_at_iso=r.approved_at_iso,
            project_name=r.project_name,
            project_id=r.project_id,
            timeline_name=r.timeline_name,
            timeline_id=r.timeline_id,
            timeline_fps_numerator=r.timeline_fps_numerator,
            timeline_fps_denominator=r.timeline_fps_denominator,
            timeline_resolution=r.timeline_resolution,
            source_digests=r.source_digests,
            catalog_digests=r.catalog_digests,
            track_reservations=r.track_reservations,
            planned_actions_count=r.planned_actions_count,
            created_at_iso=r.created_at_iso,
        )

    # Reject uppercase hex
    with pytest.raises(ValueError, match="ERR_INVALID_PROPOSAL_REVISION"):
        ExecutionReceipt(
            execution_id=r.execution_id,
            idempotency_key=r.idempotency_key,
            proposal_id=r.proposal_id,
            proposal_revision="A" * 64,
            approval_id=r.approval_id,
            approved_at_iso=r.approved_at_iso,
            project_name=r.project_name,
            project_id=r.project_id,
            timeline_name=r.timeline_name,
            timeline_id=r.timeline_id,
            timeline_fps_numerator=r.timeline_fps_numerator,
            timeline_fps_denominator=r.timeline_fps_denominator,
            timeline_resolution=r.timeline_resolution,
            source_digests=r.source_digests,
            catalog_digests=r.catalog_digests,
            track_reservations=r.track_reservations,
            planned_actions_count=r.planned_actions_count,
            created_at_iso=r.created_at_iso,
        )

    # from_dict rejects legacy numeric payload fail-closed
    data = r.to_dict()
    data["proposal_revision"] = 1
    with pytest.raises(ValueError, match="ERR_INVALID_PROPOSAL_REVISION"):
        ExecutionReceipt.from_dict(data)


def test_source_digest_binding_validation():
    b = _sample_source_digest()
    assert b.clip_id == "C01"
    d = b.to_dict()
    restored = SourceFileDigestBinding.from_dict(d)
    assert restored == b

    with pytest.raises(ValueError, match="ERR_INVALID_SHA256"):
        SourceFileDigestBinding(
            clip_id="C01",
            absolute_path="/path",
            full_sha256="short_hash",
            stream_index=0,
            stat_identity=(1, 2, 3, 4, 5),
        )


def test_catalog_digest_binding_validation():
    b = _sample_catalog_digest()
    assert b.asset_id == "asset_cat_01"
    d = b.to_dict()
    restored = CatalogAssetDigestBinding.from_dict(d)
    assert restored == b


def test_target_track_reservation_validation():
    res = _sample_track_reservation()
    assert res.track_type == "video"
    d = res.to_dict()
    restored = TargetTrackReservation.from_dict(d)
    assert restored == res

    with pytest.raises(ValueError, match="ERR_INVALID_TRACK_TYPE"):
        TargetTrackReservation(
            track_type="subtitle",  # invalid
            track_index=1,
            reserved_start_frame=0,
            reserved_end_frame=100,
            intended_usage="V1",
        )

    with pytest.raises(ValueError, match="ERR_INT_OUT_OF_RANGE"):
        TargetTrackReservation(
            track_type="video",
            track_index=1,
            reserved_start_frame=100,
            reserved_end_frame=50,  # end < start
            intended_usage="V1",
        )


def test_receipt_roundtrip_serialization():
    receipt = _sample_receipt()
    assert receipt.schema_version == SCHEMA_VERSION
    assert receipt.state == "INITIALIZED"

    json_str = receipt_to_json(receipt)
    assert isinstance(json_str, str)

    restored = receipt_from_json(json_str)
    assert restored == receipt
    assert restored.idempotency_key == receipt.idempotency_key


def test_receipt_validation_rejects_invalid_state():
    with pytest.raises(ValueError, match="ERR_INVALID_RECEIPT_STATE"):
        r = _sample_receipt()
        ExecutionReceipt(
            execution_id=r.execution_id,
            idempotency_key=r.idempotency_key,
            proposal_id=r.proposal_id,
            proposal_revision=r.proposal_revision,
            approval_id=r.approval_id,
            approved_at_iso=r.approved_at_iso,
            project_name=r.project_name,
            project_id=r.project_id,
            timeline_name=r.timeline_name,
            timeline_id=r.timeline_id,
            timeline_fps_numerator=r.timeline_fps_numerator,
            timeline_fps_denominator=r.timeline_fps_denominator,
            timeline_resolution=r.timeline_resolution,
            source_digests=r.source_digests,
            catalog_digests=r.catalog_digests,
            track_reservations=r.track_reservations,
            planned_actions_count=r.planned_actions_count,
            created_at_iso=r.created_at_iso,
            state="INVALID_STATE",
        )


def test_receipt_size_limit_rejection():
    r = _sample_receipt()
    # Mocking a huge string exceeding MAX_RECEIPT_BYTES
    huge_str = "a" * (MAX_RECEIPT_BYTES + 10)
    with pytest.raises(ValueError, match="ERR_RECEIPT_TOO_LARGE"):
        receipt_from_json(huge_str)


def test_journal_entry_roundtrip():
    entry = JournalEntry(
        seq=1,
        stage="ACTION_COMMITTED",
        action_type="INSERT_CLIP",
        timestamp_iso="2026-11-02T14:01:00Z",
        action_params={"clip_id": "C01", "record_frame": 0},
        resolve_item_id="tl_item_12345",
        track_type="video",
        track_index=1,
    )
    d = entry.to_dict()
    restored = JournalEntry.from_dict(d)
    assert restored == entry

    line = journal_entry_to_json_line(entry)
    assert line.endswith("\n")


def test_journal_entry_rejects_invalid_stage_or_action():
    with pytest.raises(ValueError, match="ERR_INVALID_STAGE"):
        JournalEntry(
            seq=1,
            stage="UNKNOWN_STAGE",
            action_type="INSERT_CLIP",
            timestamp_iso="2026-11-02T14:01:00Z",
            action_params={},
        )

    with pytest.raises(ValueError, match="ERR_INVALID_ACTION_TYPE"):
        JournalEntry(
            seq=1,
            stage="ACTION_PENDING",
            action_type="UNKNOWN_ACTION",
            timestamp_iso="2026-11-02T14:01:00Z",
            action_params={},
        )


def test_execution_receipt_schema_version_boundaries():
    """Validates schema_version 2 enforcement, legacy v1 rejection, and invalid type fail-closed."""
    import copy
    r = _sample_receipt()
    d = r.to_dict()
    assert d["schema_version"] == 2
    assert r.schema_version == 2

    # v2 round-trip
    restored = ExecutionReceipt.from_dict(d)
    assert restored == r
    assert restored.schema_version == 2

    # Direct construction rejects v1 and invalid versions/types
    with pytest.raises(ValueError, match="ERR_LEGACY_SCHEMA_VERSION"):
        ExecutionReceipt(
            schema_version=1,
            execution_id=r.execution_id,
            idempotency_key=r.idempotency_key,
            proposal_id=r.proposal_id,
            proposal_revision=r.proposal_revision,
            approval_id=r.approval_id,
            approved_at_iso=r.approved_at_iso,
            project_name=r.project_name,
            project_id=r.project_id,
            timeline_name=r.timeline_name,
            timeline_id=r.timeline_id,
            timeline_fps_numerator=r.timeline_fps_numerator,
            timeline_fps_denominator=r.timeline_fps_denominator,
            timeline_resolution=r.timeline_resolution,
            source_digests=r.source_digests,
            catalog_digests=r.catalog_digests,
            track_reservations=r.track_reservations,
            planned_actions_count=r.planned_actions_count,
            created_at_iso=r.created_at_iso,
        )

    with pytest.raises(ValueError, match="ERR_INVALID_TYPE"):
        ExecutionReceipt(
            schema_version=True,
            execution_id=r.execution_id,
            idempotency_key=r.idempotency_key,
            proposal_id=r.proposal_id,
            proposal_revision=r.proposal_revision,
            approval_id=r.approval_id,
            approved_at_iso=r.approved_at_iso,
            project_name=r.project_name,
            project_id=r.project_id,
            timeline_name=r.timeline_name,
            timeline_id=r.timeline_id,
            timeline_fps_numerator=r.timeline_fps_numerator,
            timeline_fps_denominator=r.timeline_fps_denominator,
            timeline_resolution=r.timeline_resolution,
            source_digests=r.source_digests,
            catalog_digests=r.catalog_digests,
            track_reservations=r.track_reservations,
            planned_actions_count=r.planned_actions_count,
            created_at_iso=r.created_at_iso,
        )

    # Deserialization tests with zero mutation assertions
    # 1. Missing version
    d_missing = copy.deepcopy(d)
    del d_missing["schema_version"]
    d_missing_orig = copy.deepcopy(d_missing)
    with pytest.raises(ValueError, match="ERR_MISSING_SCHEMA_VERSION"):
        ExecutionReceipt.from_dict(d_missing)
    assert d_missing == d_missing_orig

    # 2. Boolean version True/False
    for bool_val in (True, False):
        d_bool = copy.deepcopy(d)
        d_bool["schema_version"] = bool_val
        d_bool_orig = copy.deepcopy(d_bool)
        with pytest.raises(ValueError, match="ERR_INVALID_TYPE"):
            ExecutionReceipt.from_dict(d_bool)
        assert d_bool == d_bool_orig

    # 3. String / Float version
    for bad_ver in ("2", "1", 2.0, 1.5):
        d_bad = copy.deepcopy(d)
        d_bad["schema_version"] = bad_ver
        d_bad_orig = copy.deepcopy(d_bad)
        with pytest.raises(ValueError, match="ERR_INVALID_TYPE"):
            ExecutionReceipt.from_dict(d_bad)
        assert d_bad == d_bad_orig

    # 4. Legacy v1 record with numeric proposal_revision
    d_v1_num = copy.deepcopy(d)
    d_v1_num["schema_version"] = 1
    d_v1_num["proposal_revision"] = 0
    d_v1_num_orig = copy.deepcopy(d_v1_num)
    with pytest.raises(ValueError, match="ERR_LEGACY_SCHEMA_VERSION"):
        ExecutionReceipt.from_dict(d_v1_num)
    assert d_v1_num == d_v1_num_orig

    # 5. Legacy v1 record with string proposal_revision
    d_v1_str = copy.deepcopy(d)
    d_v1_str["schema_version"] = 1
    d_v1_str_orig = copy.deepcopy(d_v1_str)
    with pytest.raises(ValueError, match="ERR_LEGACY_SCHEMA_VERSION"):
        ExecutionReceipt.from_dict(d_v1_str)
    assert d_v1_str == d_v1_str_orig

    # 6. Unsupported future version (e.g. 3, 99, -1)
    for fut_ver in (3, 99, -1):
        d_fut = copy.deepcopy(d)
        d_fut["schema_version"] = fut_ver
        d_fut_orig = copy.deepcopy(d_fut)
        with pytest.raises(ValueError, match="ERR_UNSUPPORTED_SCHEMA_VERSION"):
            ExecutionReceipt.from_dict(d_fut)
        assert d_fut == d_fut_orig
