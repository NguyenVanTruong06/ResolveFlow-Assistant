"""Focused unit tests for Dispatcher Admission IPC Contracts.

Workorder: G07-07B7-GATE6-PHASE2-DISPATCHER-ADMISSION-CONTRACTS-R1
Governing Reference: docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md §7.3

Safety Boundary:
- Pure unit tests using synthetic in-memory values only.
- Strictly no socket creation, bind, connect, listen, or IPC calls.
- Strictly no access to /var/lib/resolveflow, /run/resolveflow, or production state.
- Strictly no DaVinci Resolve import or connection.
"""

from __future__ import annotations

import struct
from typing import Any, Dict

import pytest

from src.core.contracts.dispatcher_admission_contracts import (
    DISPATCHER_ADMISSION_REQUEST_FIELDS,
    DISPATCHER_ADMISSION_RESPONSE_FIELDS,
    FRAME_HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    MIN_PAYLOAD_SIZE,
    SCHEMA_VERSION_V1,
    STATUS_ADMITTED,
    STATUS_REJECTED,
    DispatcherAdmissionContractError,
    DispatcherAdmissionRequest,
    DispatcherAdmissionResponse,
    decode_admission_frame,
    encode_admission_frame,
    encode_framed_bytes,
    parse_strict_json,
)
from src.core.contracts.edit_plan_approval_contracts import AppOwnedApprovalRecord
from src.core.contracts.signed_approval_envelope import SignedApprovalEnvelope
from src.core.contracts.signed_execution_workorder_contracts import SignedExecutionWorkorderRecord
from src.core.contracts.signed_operator_consent_envelope import SignedOperatorConsentEnvelope
from src.core.contracts.signed_preflight_contracts import (
    PINNED_TARGET_PROJECT_NAME,
    PINNED_TARGET_PROJECT_UID,
    PINNED_TARGET_TIMELINE_NAME,
    PINNED_TARGET_TIMELINE_UID,
    SignedPreflightEvidenceEnvelope,
)


def make_synthetic_approval_envelope() -> SignedApprovalEnvelope:
    record = AppOwnedApprovalRecord(
        approval_id="appr_01j7b9c4d2e1f8a3b5c7d9e0f1",
        proposal_id="prop_01j7b9c4d2e1f8a3b5c7d9e0f1",
        proposal_revision="a1" * 32,
        approved_at_iso="2026-11-05T08:00:00Z",
        accepted_omission_ids=("omit_1", "omit_2"),
        approval_scope="ENTIRE_PROPOSAL",
    )
    return SignedApprovalEnvelope(
        schema_version=1,
        approval_record=record,
        plan_digest_sha256="1" * 64,
        proposal_content_digest_sha256="2" * 64,
        director_key_id="dir_key_2026_primary",
        director_signature="e" * 128,
        signed_at_iso="2026-11-05T08:00:05Z",
    )


def make_synthetic_operator_consent_envelope() -> SignedOperatorConsentEnvelope:
    return SignedOperatorConsentEnvelope(
        schema_version=1,
        operator_id="op_vanntruongg",
        challenge_id="CHALLENGE_2026_CANARY_01",
        client_nonce="a1b2c3d4-e5f6-47a8-b9c0-d1e2f3a4b5c6",
        confirmation_phrase="CONFIRM_CANARY_TRIAL_EXECUTION",
        consent_timestamp_iso="2026-11-05T08:10:00Z",
        freshness_attestation="OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED",
        approval_envelope_digest_sha256="1" * 64,
        plan_digest_sha256="2" * 64,
        action_payload_digest_sha256="3" * 64,
        target_project_name="RF_TRIAL_CANARY_P02",
        target_project_uid="01j7b9c4-d2e1-48a3-b5c7-d9e0f1a2b3c4",
        target_timeline_name="RF_TRIAL_TL_CANARY_T02",
        target_timeline_uid="01j7b9c4-d2e1-48a3-b5c7-d9e0f1a2b3c5",
        source_file_path="/var/media/resolveflow/canary_sample.mov",
        source_file_sha256="4" * 64,
        action_type="INSERT_CLIP",
        track_type="video",
        track_index=1,
        record_frame=86400,
        duration_frames=120,
        operator_key_id="op_key_2026_ed25519",
        operator_signature="d" * 128,
    )


def make_synthetic_preflight_envelope() -> SignedPreflightEvidenceEnvelope:
    return SignedPreflightEvidenceEnvelope(
        schema_version=1,
        preflight_run_id="c1a2b3c4-d5e6-47a8-9901-a1b2c3d4e5f6",
        verifier_id="verifier_host_daemon",
        verifier_key_id="key_verifier_2026_ed25519",
        verified_at_iso="2026-11-05T08:15:00Z",
        valid_until_iso="2026-11-05T08:45:00Z",
        target_project_uid=PINNED_TARGET_PROJECT_UID,
        target_project_name=PINNED_TARGET_PROJECT_NAME,
        target_timeline_uid=PINNED_TARGET_TIMELINE_UID,
        target_timeline_name=PINNED_TARGET_TIMELINE_NAME,
        target_timeline_fps_numerator=24,
        target_timeline_fps_denominator=1,
        observed_start_frame=86400,
        project_and_timeline_active=True,
        empty_timeline_verified=True,
        topology_verified=True,
        incident_denylist_verified=True,
        media_pool_source_verified=True,
        source_file_sha256="7c83154437b2e05e01f6a795d6295e9827e04907c4479c7b0435e3a4ac18d916",
        audit_chain_head_sha256="b" * 64,
        verifier_signature="c" * 128,
    )


def make_synthetic_execution_workorder() -> SignedExecutionWorkorderRecord:
    return SignedExecutionWorkorderRecord(
        schema_version=1,
        workorder_id="G07-07B7-GATE6-EXEC-R1",
        tech_lead_id="tech_lead_codex",
        tech_lead_key_id="key_techlead_2026_ed25519",
        issued_at_iso="2026-11-05T08:00:00Z",
        valid_until_iso="2026-11-05T09:00:00Z",
        target_project_uid=PINNED_TARGET_PROJECT_UID,
        target_timeline_uid=PINNED_TARGET_TIMELINE_UID,
        reviewed_candidate_sha256="1" * 64,
        permitted_command="execute_canary_trial_g07",
        plan_digest_sha256="2" * 64,
        action_payload_digest_sha256="3" * 64,
        execution_id="exec_20261105_001",
        tech_lead_signature="4" * 128,
    )


def make_valid_admission_request(**overrides: Any) -> DispatcherAdmissionRequest:
    kwargs: Dict[str, Any] = {
        "schema_version": 1,
        "request_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "approval_envelope": make_synthetic_approval_envelope(),
        "operator_consent_envelope": make_synthetic_operator_consent_envelope(),
        "preflight_evidence_envelope": make_synthetic_preflight_envelope(),
        "execution_workorder_record": make_synthetic_execution_workorder(),
    }
    kwargs.update(overrides)
    return DispatcherAdmissionRequest(**kwargs)


def make_valid_admission_response(**overrides: Any) -> DispatcherAdmissionResponse:
    kwargs: Dict[str, Any] = {
        "schema_version": 1,
        "request_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "status": STATUS_ADMITTED,
        "error_code": None,
        "execution_id": "8b9a0c1d-2e3f-4a5b-8c9d-0e1f2a3b4c5d",
        "preflight_run_id": "c1a2b3c4-d5e6-47a8-9901-a1b2c3d4e5f6",
        "admitted_at_iso": "2026-11-05T08:20:00Z",
    }
    kwargs.update(overrides)
    return DispatcherAdmissionResponse(**kwargs)


# ==============================================================================
# DispatcherAdmissionRequest Tests
# ==============================================================================


class TestDispatcherAdmissionRequest:
    """Test suite verifying DispatcherAdmissionRequest contracts."""

    def test_valid_request_construction_and_attributes(self):
        req = make_valid_admission_request()
        assert req.schema_version == 1
        assert req.request_id == "f47ac10b-58cc-4372-a567-0e02b2c3d479"
        assert isinstance(req.approval_envelope, SignedApprovalEnvelope)
        assert isinstance(req.operator_consent_envelope, SignedOperatorConsentEnvelope)
        assert isinstance(req.preflight_evidence_envelope, SignedPreflightEvidenceEnvelope)
        assert isinstance(req.execution_workorder_record, SignedExecutionWorkorderRecord)

    def test_request_to_dict_and_to_json_bytes_canonical(self):
        req = make_valid_admission_request()
        d = req.to_dict()
        assert set(d.keys()) == DISPATCHER_ADMISSION_REQUEST_FIELDS
        assert d["schema_version"] == 1
        assert d["request_id"] == "f47ac10b-58cc-4372-a567-0e02b2c3d479"
        assert isinstance(d["approval_envelope"], dict)
        assert isinstance(d["operator_consent_envelope"], dict)
        assert isinstance(d["preflight_evidence_envelope"], dict)
        assert isinstance(d["execution_workorder_record"], dict)

        raw_bytes = req.to_json_bytes()
        assert isinstance(raw_bytes, bytes)
        # RFC 8785 deterministic ordering: keys are sorted
        assert b'"action_payload_digest_sha256"' in raw_bytes
        assert b'"schema_version":1' in raw_bytes

    def test_request_round_trip_from_dict(self):
        req1 = make_valid_admission_request()
        d = req1.to_dict()
        req2 = DispatcherAdmissionRequest.from_dict(d)
        assert req1 == req2
        assert req1.to_dict() == req2.to_dict()

    def test_request_round_trip_from_json_bytes(self):
        req1 = make_valid_admission_request()
        raw = req1.to_json_bytes()
        req2 = DispatcherAdmissionRequest.from_json_bytes(raw)
        assert req1 == req2

    @pytest.mark.parametrize("invalid_version", [0, 2, True, False, 1.0, "1", None])
    def test_request_rejects_invalid_schema_version(self, invalid_version):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_request(schema_version=invalid_version)
        assert exc_info.value.code == "ERR_SCHEMA_VERSION_INVALID"

    @pytest.mark.parametrize(
        "invalid_uuid",
        [
            "",
            "not-a-uuid",
            "f47ac10b-58cc-1372-a567-0e02b2c3d479",  # v1 not v4
            "F47AC10B-58CC-4372-A567-0E02B2C3D479",  # uppercase
            " f47ac10b-58cc-4372-a567-0e02b2c3d479",  # leading space
            12345,
            None,
        ],
    )
    def test_request_rejects_invalid_request_id(self, invalid_uuid):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_request(request_id=invalid_uuid)
        assert exc_info.value.code == "ERR_INVALID_UUID"

    def test_request_direct_type_rejection_for_nested_envelopes(self):
        # Dict passed instead of DTO
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_request(approval_envelope={"schema_version": 1})
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"

        # Wrong envelope object passed
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_request(
                operator_consent_envelope=make_synthetic_approval_envelope()
            )
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"

    def test_request_from_dict_rejects_missing_fields(self):
        d = make_valid_admission_request().to_dict()
        del d["preflight_evidence_envelope"]
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            DispatcherAdmissionRequest.from_dict(d)
        assert exc_info.value.code == "ERR_MISSING_FIELD"

    def test_request_from_dict_rejects_unexpected_fields(self):
        d = make_valid_admission_request().to_dict()
        d["rogue_key"] = "unauthorized"
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            DispatcherAdmissionRequest.from_dict(d)
        assert exc_info.value.code == "ERR_UNEXPECTED_FIELD"

    def test_request_from_dict_rejects_non_mapping(self):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            DispatcherAdmissionRequest.from_dict("not-a-dict")  # type: ignore[arg-type]
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"

    def test_request_from_dict_rejects_malformed_nested_envelope(self):
        d = make_valid_admission_request().to_dict()
        d["approval_envelope"]["schema_version"] = 999  # Invalid nested envelope
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            DispatcherAdmissionRequest.from_dict(d)
        assert exc_info.value.code == "ERR_INVALID_NESTED_ENVELOPE"

    def test_request_from_json_bytes_rejects_duplicate_keys(self):
        raw = b'{"schema_version":1,"schema_version":1,"request_id":"f47ac10b-58cc-4372-a567-0e02b2c3d479"}'
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            DispatcherAdmissionRequest.from_json_bytes(raw)
        assert exc_info.value.code == "ERR_DUPLICATE_KEY"

    def test_request_preserves_nested_envelopes_byte_for_byte(self):
        req = make_valid_admission_request()
        # Ensure nested DTO digests and canonical outputs match their native implementations
        assert (
            req.approval_envelope.canonical_envelope_bytes()
            == make_synthetic_approval_envelope().canonical_envelope_bytes()
        )
        assert (
            req.preflight_evidence_envelope.compute_envelope_digest()
            == make_synthetic_preflight_envelope().compute_envelope_digest()
        )

    def test_request_rejects_subclasses_of_nested_envelopes(self):
        class SubclassedApprovalEnvelope(SignedApprovalEnvelope):
            def to_dict(self) -> Dict[str, Any]:
                d = super().to_dict()
                d["malicious_override"] = True
                return d

        sub_env = SubclassedApprovalEnvelope(
            schema_version=1,
            approval_record=make_synthetic_approval_envelope().approval_record,
            plan_digest_sha256="1" * 64,
            proposal_content_digest_sha256="2" * 64,
            director_key_id="dir_key_2026_primary",
            director_signature="e" * 128,
            signed_at_iso="2026-11-05T08:00:05Z",
        )
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_request(approval_envelope=sub_env)
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"
        assert "exact type SignedApprovalEnvelope" in exc_info.value.detail

        class SubclassedOperatorConsent(SignedOperatorConsentEnvelope):
            pass

        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_request(
                operator_consent_envelope=SubclassedOperatorConsent(
                    **make_synthetic_operator_consent_envelope().to_dict()
                )
            )
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"

        class SubclassedPreflight(SignedPreflightEvidenceEnvelope):
            pass

        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_request(
                preflight_evidence_envelope=SubclassedPreflight(
                    **make_synthetic_preflight_envelope().to_dict()
                )
            )
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"

        class SubclassedWorkorder(SignedExecutionWorkorderRecord):
            pass

        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_request(
                execution_workorder_record=SubclassedWorkorder(
                    **make_synthetic_execution_workorder().to_dict()
                )
            )
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"


# ==============================================================================
# DispatcherAdmissionResponse Tests
# ==============================================================================


class TestDispatcherAdmissionResponse:
    """Test suite verifying DispatcherAdmissionResponse contracts."""

    def test_valid_admitted_response(self):
        resp = make_valid_admission_response()
        assert resp.schema_version == 1
        assert resp.status == STATUS_ADMITTED
        assert resp.error_code is None
        assert resp.execution_id == "8b9a0c1d-2e3f-4a5b-8c9d-0e1f2a3b4c5d"
        assert resp.preflight_run_id == "c1a2b3c4-d5e6-47a8-9901-a1b2c3d4e5f6"
        assert resp.admitted_at_iso == "2026-11-05T08:20:00Z"

    def test_valid_rejected_response(self):
        resp = make_valid_admission_response(
            status=STATUS_REJECTED,
            error_code="ERR_GATE4_SIGNATURE_INVALID",
        )
        assert resp.status == STATUS_REJECTED
        assert resp.error_code == "ERR_GATE4_SIGNATURE_INVALID"

    def test_response_to_dict_and_to_json_bytes(self):
        resp = make_valid_admission_response()
        d = resp.to_dict()
        assert set(d.keys()) == DISPATCHER_ADMISSION_RESPONSE_FIELDS
        assert d["status"] == STATUS_ADMITTED
        assert d["error_code"] is None

        raw = resp.to_json_bytes()
        assert b'"status":"ADMITTED"' in raw
        assert b'"error_code":null' in raw

    def test_response_round_trip_admitted(self):
        resp1 = make_valid_admission_response()
        d = resp1.to_dict()
        resp2 = DispatcherAdmissionResponse.from_dict(d)
        assert resp1 == resp2
        assert resp1.to_dict() == resp2.to_dict()

    def test_response_round_trip_rejected(self):
        resp1 = make_valid_admission_response(
            status=STATUS_REJECTED,
            error_code="ERR_PREFLIGHT_LEDGER_LOCKED",
        )
        raw = resp1.to_json_bytes()
        resp2 = DispatcherAdmissionResponse.from_json_bytes(raw)
        assert resp1 == resp2
        assert resp2.status == STATUS_REJECTED
        assert resp2.error_code == "ERR_PREFLIGHT_LEDGER_LOCKED"

    @pytest.mark.parametrize("invalid_version", [0, 2, True, False, 1.0, "1"])
    def test_response_rejects_invalid_schema_version(self, invalid_version):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_response(schema_version=invalid_version)
        assert exc_info.value.code == "ERR_SCHEMA_VERSION_INVALID"

    @pytest.mark.parametrize(
        "invalid_uuid",
        ["not-uuid", "f47ac10b-58cc-1372-a567-0e02b2c3d479", "UPPER-UUID", 1234],
    )
    def test_response_rejects_invalid_uuids(self, invalid_uuid):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_response(request_id=invalid_uuid)
        assert exc_info.value.code == "ERR_INVALID_UUID"

        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_response(execution_id=invalid_uuid)
        assert exc_info.value.code == "ERR_INVALID_UUID"

        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_response(preflight_run_id=invalid_uuid)
        assert exc_info.value.code == "ERR_INVALID_UUID"

    @pytest.mark.parametrize("invalid_status", ["PENDING", "admitted", "UNKNOWN", "", 1])
    def test_response_rejects_invalid_status(self, invalid_status):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_response(status=invalid_status)
        assert exc_info.value.code == "ERR_STATUS_INVALID"

    def test_response_rejects_admitted_with_error_code(self):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_response(
                status=STATUS_ADMITTED,
                error_code="ERR_SOME_ERROR",
            )
        assert exc_info.value.code == "ERR_STATUS_ERROR_INCONSISTENT"

    @pytest.mark.parametrize(
        "invalid_error_code",
        [
            None,
            "",
            "err_lowercase",
            "HAS SPACES",
            "TOOLONG" + "A" * 65,
            123,
            True,
        ],
    )
    def test_response_rejects_rejected_with_invalid_error_code(self, invalid_error_code):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_response(
                status=STATUS_REJECTED,
                error_code=invalid_error_code,
            )
        assert exc_info.value.code == "ERR_STATUS_ERROR_INCONSISTENT"

    @pytest.mark.parametrize(
        "invalid_ts",
        [
            "2026-11-05 08:20:00",  # no T, no Z
            "2026-11-05T08:20:00+07:00",  # non-UTC offset
            "2026-11-05T08:20:00-00:00",  # RFC 3339 unknown-offset
            "not-a-timestamp",
            "",
            1730794800,
        ],
    )
    def test_response_rejects_invalid_timestamp(self, invalid_ts):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            make_valid_admission_response(admitted_at_iso=invalid_ts)
        assert exc_info.value.code in ("ERR_INVALID_TIMESTAMP", "ERR_TYPE_MISMATCH")

    def test_response_from_dict_missing_fields(self):
        d = make_valid_admission_response().to_dict()
        del d["status"]
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            DispatcherAdmissionResponse.from_dict(d)
        assert exc_info.value.code == "ERR_MISSING_FIELD"

    def test_response_from_dict_unexpected_fields(self):
        d = make_valid_admission_response().to_dict()
        d["dispatcher_signature"] = "rogue_signature"  # Rejection of unprovisioned 7th key
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            DispatcherAdmissionResponse.from_dict(d)
        assert exc_info.value.code == "ERR_UNEXPECTED_FIELD"

    def test_response_from_json_bytes_duplicate_keys(self):
        raw = b'{"schema_version":1,"schema_version":1,"request_id":"f47ac10b-58cc-4372-a567-0e02b2c3d479"}'
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            DispatcherAdmissionResponse.from_json_bytes(raw)
        assert exc_info.value.code == "ERR_DUPLICATE_KEY"


# ==============================================================================
# Pure Wire Framing Tests
# ==============================================================================


class TestFramingHelpers:
    """Test suite for pure byte framing and decoding helpers."""

    def test_encode_framed_bytes_valid(self):
        payload = b'{"hello":"world"}'
        framed = encode_framed_bytes(payload)
        assert len(framed) == FRAME_HEADER_SIZE + len(payload)
        (header_len,) = struct.unpack(">I", framed[:FRAME_HEADER_SIZE])
        assert header_len == len(payload)
        assert framed[FRAME_HEADER_SIZE:] == payload

    def test_encode_framed_bytes_empty_payload_rejected(self):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            encode_framed_bytes(b"")
        assert exc_info.value.code == "ERR_EMPTY_FRAME"

    def test_encode_framed_bytes_oversized_payload_rejected(self):
        oversized = b"x" * (MAX_PAYLOAD_SIZE + 1)
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            encode_framed_bytes(oversized)
        assert exc_info.value.code == "ERR_FRAME_TOO_LARGE"

    def test_encode_framed_bytes_non_bytes_rejected(self):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            encode_framed_bytes("not bytes")  # type: ignore[arg-type]
        assert exc_info.value.code == "ERR_INVALID_FRAME"

    def test_encode_admission_frame_round_trip_request(self):
        req = make_valid_admission_request()
        frame = encode_admission_frame(req)
        assert len(frame) > FRAME_HEADER_SIZE
        decoded = decode_admission_frame(frame, DispatcherAdmissionRequest)
        assert decoded == req

    def test_encode_admission_frame_round_trip_response_admitted(self):
        resp = make_valid_admission_response()
        frame = encode_admission_frame(resp)
        decoded = decode_admission_frame(frame, DispatcherAdmissionResponse)
        assert decoded == resp

    def test_encode_admission_frame_round_trip_response_rejected(self):
        resp = make_valid_admission_response(
            status=STATUS_REJECTED,
            error_code="ERR_DISPATCHER_PEER_UNTRUSTED",
        )
        frame = encode_admission_frame(resp)
        decoded = decode_admission_frame(frame, DispatcherAdmissionResponse)
        assert decoded == resp

    def test_encode_admission_frame_invalid_object(self):
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            encode_admission_frame("not a DTO")  # type: ignore[arg-type]
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"

    def test_encode_admission_frame_rejects_subclasses(self):
        class SubclassedRequest(DispatcherAdmissionRequest):
            def to_json_bytes(self) -> bytes:
                return b'{"injected":true}'

        req = make_valid_admission_request()
        sub_req = SubclassedRequest(
            schema_version=req.schema_version,
            request_id=req.request_id,
            approval_envelope=req.approval_envelope,
            operator_consent_envelope=req.operator_consent_envelope,
            preflight_evidence_envelope=req.preflight_evidence_envelope,
            execution_workorder_record=req.execution_workorder_record,
        )
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            encode_admission_frame(sub_req)
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"
        assert "Expected exact type DispatcherAdmissionRequest" in exc_info.value.detail

        class SubclassedResponse(DispatcherAdmissionResponse):
            pass

        resp = make_valid_admission_response()
        sub_resp = SubclassedResponse(
            schema_version=resp.schema_version,
            request_id=resp.request_id,
            status=resp.status,
            error_code=resp.error_code,
            execution_id=resp.execution_id,
            preflight_run_id=resp.preflight_run_id,
            admitted_at_iso=resp.admitted_at_iso,
        )
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            encode_admission_frame(sub_resp)
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"

    def test_decode_frame_short_header(self):
        short = b"\x00\x00\x01"  # 3 bytes < 4
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            decode_admission_frame(short, DispatcherAdmissionResponse)
        assert exc_info.value.code == "ERR_INVALID_FRAME"

    def test_decode_frame_zero_payload_length(self):
        zero_len_header = struct.pack(">I", 0)
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            decode_admission_frame(zero_len_header, DispatcherAdmissionResponse)
        assert exc_info.value.code == "ERR_EMPTY_FRAME"

    def test_decode_frame_oversized_declared_length(self):
        oversized_header = struct.pack(">I", MAX_PAYLOAD_SIZE + 1)
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            decode_admission_frame(oversized_header, DispatcherAdmissionResponse)
        assert exc_info.value.code == "ERR_FRAME_TOO_LARGE"

    def test_decode_frame_truncated_payload(self):
        payload = b'{"schema_version":1}'
        declared_len = len(payload) + 10
        truncated_frame = struct.pack(">I", declared_len) + payload
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            decode_admission_frame(truncated_frame, DispatcherAdmissionResponse)
        assert exc_info.value.code == "ERR_INVALID_FRAME"
        assert "Truncated frame payload" in exc_info.value.detail

    def test_decode_frame_extra_trailing_bytes(self):
        payload = b'{"schema_version":1}'
        frame = struct.pack(">I", len(payload)) + payload + b"EXTRA_BYTES"
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            decode_admission_frame(frame, DispatcherAdmissionResponse)
        assert exc_info.value.code == "ERR_INVALID_FRAME"
        assert "Extra trailing bytes" in exc_info.value.detail

    def test_decode_frame_invalid_utf8_payload(self):
        invalid_utf8 = b"\xff\xfe\xfd"
        frame = struct.pack(">I", len(invalid_utf8)) + invalid_utf8
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            decode_admission_frame(frame, DispatcherAdmissionResponse)
        assert exc_info.value.code == "ERR_INVALID_FRAME"
        assert "Non-UTF8" in exc_info.value.detail

    def test_decode_frame_duplicate_key_in_payload(self):
        duplicate_key_json = b'{"schema_version":1,"schema_version":1}'
        frame = struct.pack(">I", len(duplicate_key_json)) + duplicate_key_json
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            decode_admission_frame(frame, DispatcherAdmissionResponse)
        assert exc_info.value.code == "ERR_DUPLICATE_KEY"

    def test_decode_frame_wrong_expected_type(self):
        resp = make_valid_admission_response()
        frame = encode_admission_frame(resp)
        with pytest.raises(DispatcherAdmissionContractError) as exc_info:
            decode_admission_frame(frame, int)  # type: ignore[arg-type]
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"
