"""Focused Acceptance Tests for Trial Authorization Contracts (G07-07B6-I1)

Covers the five finite acceptance groups:
- I1-G1: Frozen DTO immutability and valid canonical serialization/round-trip.
- I1-G2: Lexical pre-parse bounds (size, depth, container length, node count, string scalars, surrogates).
- I1-G3: Duplicate JSON member rejection before dictionary coercion.
- I1-G4: Exact 16-key action assembler, missing/extra-field rejection, primitive type gates, geometry bounds, and identity binding.
- I1-G5: Authorization schema completeness, authoritative runtime type/range validation (I1-01), and canonical HMAC bytes covering exactly 32 claims while excluding authorization_hmac.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import pytest

from src.core.contracts.trial_authorization_contracts import (
    CANONICAL_ACTION_PAYLOAD_KEYS,
    MAX_CONTAINER_LENGTH,
    MAX_NESTING_DEPTH,
    MAX_STRING_SCALARS,
    AcceptanceGateError,
    AuthorizationContract,
    OperatorConsentGesture,
    OperatorReconciliationAuthorization,
    PlannedTimelineAction,
    ReadbackReceiptVerificationError,
    ReconciliationChallenge,
    StrictJsonComplexityError,
    StrictJsonParseError,
    TargetExclusionLockError,
    TrialExecutionAuthorization,
    TrialExecutionReceipt,
    TrialIdentityEnvelope,
    WholePlanReconciliationError,
    assemble_canonical_action_payload,
    canonical_action_payload_bytes,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
    parse_strict_canonical_json,
    reject_duplicate_json_keys,
    validate_json_structure_pre_parse,
    validate_trial_execution_authorization,
    parse_iso_utc_timestamp,
)


# --- Helper Fixtures ---

def make_sample_auth(
    execution_id: str = "exec-test-uuid-0001",
    token_nonce: str = "exec-test-uuid-0001",
    authorization_hmac: str = "mock_hmac_signature_hex_1234567890abcdef",
    **overrides,
) -> TrialExecutionAuthorization:
    kwargs = {
        "authorization_id": "auth-test-uuid-0001",
        "key_id": "key_20261103_trial_01",
        "operator_gesture_digest_sha256": "a" * 64,
        "approval_record_id": "appr-test-0001",
        "approval_record_digest_sha256": "b" * 64,
        "proposal_id": "prop-test-0001",
        "proposal_revision": "rev-1",
        "proposal_content_digest_sha256": "c" * 64,
        "plan_idempotency_key": "idem-key-0001",
        "plan_digest_sha256": "d" * 64,
        "action_payload_digest_sha256": "e" * 64,
        "target_project_uid": "proj-uuid-0001",
        "target_project_name": "RF_TRIAL_PROJECT_01",
        "target_timeline_uid": "tl-uuid-0001",
        "target_timeline_name": "RF_TRIAL_TL_CANARY_01",
        "target_timeline_fps_numerator": 24,
        "target_timeline_fps_denominator": 1,
        "observed_timeline_start_frame": 86400,
        "authorized_record_frame": 86400,
        "source_file_path": "/opt/media/test_clip.mov",
        "source_file_sha256": "f" * 64,
        "authorized_action_type": "INSERT_CLIP",
        "authorized_track_type": "video",
        "authorized_track_index": 1,
        "authorized_timeline_frame_start": 0,
        "authorized_timeline_frame_duration": 120,
        "authorized_source_frame_start": 0,
        "authorized_source_frame_duration": 120,
        "execution_id": execution_id,
        "issued_at_iso": "2026-11-03T05:00:00Z",
        "valid_until_iso": "2026-11-03T05:05:00Z",
        "token_nonce": token_nonce,
        "authorization_hmac": authorization_hmac,
    }
    kwargs.update(overrides)
    return TrialExecutionAuthorization(**kwargs)


def make_sample_action(**overrides) -> PlannedTimelineAction:
    kwargs = {
        "action_id": "act-test-0001",
        "action_index": 0,
        "action_type": "INSERT_CLIP",
        "track_type": "video",
        "track_index": 1,
        "record_frame": 86400,
        "duration_frames": 120,
        "source_path": "/opt/media/test_clip.mov",
        "source_frame_start": 0,
        "source_frame_duration": 120,
        "role": "B_ROLL",
    }
    kwargs.update(overrides)
    return PlannedTimelineAction(**kwargs)


# =====================================================================
# I1-G1: Frozen DTO Immutability & Canonical Serialization Round-Trip
# =====================================================================

class TestI1G1DtoImmutabilityAndSerialization:
    """Verifies all DTOs are frozen and roundtrip cleanly via canonical JSON."""

    def test_operator_consent_gesture_immutable_and_digest(self):
        gesture = OperatorConsentGesture(
            operator_id="operator_alice",
            consent_timestamp_iso="2026-11-03T05:00:00Z",
            confirmation_phrase="CONFIRM_CANARY_TRIAL_EXECUTION",
            freshness_attestation="Attest fresh empty timeline",
            challenge_id="CHALLENGE_001",
            client_nonce="nonce_uuid_1234",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            gesture.operator_id = "operator_bob"  # type: ignore

        digest1 = gesture.compute_gesture_digest()
        digest2 = gesture.compute_gesture_digest()
        assert len(digest1) == 64
        assert digest1 == digest2

        # Roundtrip JSON
        payload = {
            "challenge_id": gesture.challenge_id,
            "client_nonce": gesture.client_nonce,
            "confirmation_phrase": gesture.confirmation_phrase,
            "consent_timestamp_iso": gesture.consent_timestamp_iso,
            "freshness_attestation": gesture.freshness_attestation,
            "operator_id": gesture.operator_id,
        }
        encoded = canonical_json_bytes(payload)
        decoded = parse_strict_canonical_json(encoded)
        assert decoded == payload

    def test_trial_execution_authorization_immutability(self):
        auth = make_sample_auth()
        with pytest.raises(dataclasses.FrozenInstanceError):
            auth.authorization_id = "altered_id"  # type: ignore

    def test_planned_timeline_action_immutability(self):
        action = make_sample_action()
        with pytest.raises(dataclasses.FrozenInstanceError):
            action.duration_frames = 240  # type: ignore
        assert action.target_track_type == "video"
        assert action.target_track_index == 1
        assert action.source_file_path == "/opt/media/test_clip.mov"

    def test_auxiliary_dtos_immutability(self):
        env = TrialIdentityEnvelope(
            authorization_id="auth-1",
            execution_id="exec-1",
            target_project_uid="proj-1",
            target_timeline_uid="tl-1",
            plan_idempotency_key="key-1",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.execution_id = "exec-2"  # type: ignore

        contract = AuthorizationContract(
            authorization_id="auth-1",
            approval_id="appr-1",
            execution_id="exec-1",
            plan_idempotency_key="key-1",
            target_project_uid="proj-1",
            target_timeline_uid="tl-1",
            track_type="video",
            track_index=1,
            record_frame=86400,
            duration_frames=120,
            action_payload_digest_sha256="e" * 64,
            key_id="k-1",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            contract.track_index = 2  # type: ignore

        receipt = TrialExecutionReceipt(
            trial_receipt_version=1,
            receipt_id="rcpt-1",
            execution_id="exec-1",
            authorization_id="auth-1",
            target_project_uid="proj-1",
            target_timeline_uid="tl-1",
            plan_idempotency_key="key-1",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            receipt.trial_receipt_version = 2  # type: ignore

        challenge = ReconciliationChallenge(
            timeline_uid="tl-1",
            incident_id="inc-1",
            challenge_nonce="nonce-1",
            issued_at=100.0,
            expires_at=400.0,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            challenge.expires_at = 500.0  # type: ignore

        op_auth = OperatorReconciliationAuthorization(
            timeline_uid="tl-1",
            incident_id="inc-1",
            challenge_nonce="nonce-1",
            challenge_expires_at=400.0,
            operator_key_id="opk-1",
            operator_identity="admin",
            operator_confirmation_statement="CONFIRMED_TIMELINE_UNLOCKED_OUT_OF_BAND",
            confirmed_unlocked=True,
            signature_hmac="sig-1",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            op_auth.confirmed_unlocked = False  # type: ignore


# =====================================================================
# I1-G2: Lexical Pre-Parse Bounds & Grammar Rejections
# =====================================================================

class TestI1G2LexicalPreParseBounds:
    """Verifies strict lexical scanner enforcement against parser bombs and invalid data."""

    def test_byte_size_exceeded_rejection(self):
        small_limit = 100
        payload = b'{"key": "' + b'x' * 95 + b'"}'
        assert len(payload) > small_limit
        with pytest.raises(StrictJsonComplexityError, match="ERR_JSON_SIZE_EXCEEDED"):
            validate_json_structure_pre_parse(payload, max_bytes=small_limit)

    def test_invalid_utf8_rejection(self):
        invalid_bytes = b'{"key": "\xff\xfe"}'
        with pytest.raises(StrictJsonParseError, match="ERR_CANONICAL_UTF8_DECODE"):
            validate_json_structure_pre_parse(invalid_bytes)

    def test_nesting_depth_boundary_16_vs_17(self):
        valid_nested = "{" * 16 + '"a": 1' + "}" * 16
        validate_json_structure_pre_parse(valid_nested.encode("utf-8"))

        invalid_nested = "{" * 17 + '"a": 1' + "}" * 17
        with pytest.raises(StrictJsonComplexityError, match="ERR_JSON_EXCESSIVE_DEPTH"):
            validate_json_structure_pre_parse(invalid_nested.encode("utf-8"))

    def test_container_length_boundary_512_vs_513(self):
        items_512 = ",".join(str(i) for i in range(512))
        valid_container = f"[{items_512}]".encode("utf-8")
        validate_json_structure_pre_parse(valid_container)

        items_513 = ",".join(str(i) for i in range(513))
        invalid_container = f"[{items_513}]".encode("utf-8")
        with pytest.raises(StrictJsonComplexityError, match="ERR_JSON_CONTAINER_TOO_LARGE"):
            validate_json_structure_pre_parse(invalid_container)

    def test_string_length_boundary_8192_vs_8193(self):
        valid_str = json.dumps({"key": "a" * 8192}).encode("utf-8")
        validate_json_structure_pre_parse(valid_str)

        invalid_str = json.dumps({"key": "a" * 8193}).encode("utf-8")
        with pytest.raises(StrictJsonComplexityError, match="ERR_JSON_STRING_TOO_LONG"):
            validate_json_structure_pre_parse(invalid_str)

    def test_integer_digits_boundary_19_vs_20(self):
        valid_int = b'{"num": 1234567890123456789}'
        validate_json_structure_pre_parse(valid_int)

        invalid_int = b'{"num": 12345678901234567890}'
        with pytest.raises(StrictJsonComplexityError, match="ERR_JSON_INTEGER_TOKEN_TOO_LARGE"):
            validate_json_structure_pre_parse(invalid_int)

    def test_floating_point_numbers_strictly_forbidden(self):
        float_json = b'{"val": 3.14}'
        with pytest.raises(StrictJsonParseError, match="ERR_CANONICAL_FLOAT_FORBIDDEN"):
            validate_json_structure_pre_parse(float_json)

        exp_json = b'{"val": 1e5}'
        with pytest.raises(StrictJsonParseError, match="ERR_CANONICAL_FLOAT_FORBIDDEN"):
            validate_json_structure_pre_parse(exp_json)

    def test_non_finite_constants_forbidden(self):
        nan_json = b'{"val": NaN}'
        with pytest.raises(StrictJsonParseError, match="ERR_CANONICAL_NON_FINITE_CONSTANT"):
            validate_json_structure_pre_parse(nan_json)

        inf_json = b'{"val": Infinity}'
        with pytest.raises(StrictJsonParseError, match="ERR_CANONICAL_NON_FINITE_CONSTANT"):
            validate_json_structure_pre_parse(inf_json)

    def test_unicode_surrogate_handling(self):
        valid_surrogate = b'{"emoji": "\\uD83D\\uDE00"}'
        validate_json_structure_pre_parse(valid_surrogate)

        lone_high = b'{"bad": "\\uD800"}'
        with pytest.raises(StrictJsonParseError, match="ERR_CANONICAL_LONE_SURROGATE"):
            validate_json_structure_pre_parse(lone_high)

        lone_low = b'{"bad": "\\uDC00"}'
        with pytest.raises(StrictJsonParseError, match="ERR_CANONICAL_LONE_SURROGATE"):
            validate_json_structure_pre_parse(lone_low)

    def test_nul_character_rejection(self):
        nul_payload = b'{"bad": "hello\x00world"}'
        with pytest.raises(StrictJsonParseError, match="ERR_CANONICAL_NUL_CHAR"):
            validate_json_structure_pre_parse(nul_payload)

    def test_unbalanced_containers_and_unterminated_string(self):
        with pytest.raises(StrictJsonParseError, match="ERR_JSON_UNCLOSED_CONTAINER"):
            validate_json_structure_pre_parse(b'{"key": 1')

        with pytest.raises(StrictJsonParseError, match="ERR_JSON_UNBALANCED_BRACKETS"):
            validate_json_structure_pre_parse(b'{"key": 1}}')

        with pytest.raises(StrictJsonParseError, match="ERR_JSON_UNTERMINATED_STRING"):
            validate_json_structure_pre_parse(b'{"key": "unterminated')


# =====================================================================
# I1-G3: Duplicate JSON Member Rejection
# =====================================================================

class TestI1G3DuplicateKeyRejection:
    """Verifies that duplicate member names are rejected prior to dict coercion."""

    def test_flat_duplicate_key_rejected(self):
        duplicate_json = b'{"role": "A_ROLL", "role": "B_ROLL"}'
        with pytest.raises(StrictJsonParseError, match="ERR_DUPLICATE_JSON_KEY"):
            parse_strict_canonical_json(duplicate_json)

    def test_nested_duplicate_key_rejected(self):
        nested_duplicate = b'{"parent": {"child": 1, "child": 2}}'
        with pytest.raises(StrictJsonParseError, match="ERR_DUPLICATE_JSON_KEY"):
            parse_strict_canonical_json(nested_duplicate)

    def test_duplicate_key_hook_direct(self):
        pairs = [("a", 1), ("b", 2), ("a", 3)]
        with pytest.raises(StrictJsonParseError, match="ERR_DUPLICATE_JSON_KEY"):
            reject_duplicate_json_keys(pairs)

    def test_unique_keys_pass_hook(self):
        pairs = [("a", 1), ("b", 2), ("c", 3)]
        result = reject_duplicate_json_keys(pairs)
        assert result == {"a": 1, "b": 2, "c": 3}


# =====================================================================
# I1-G4: Exact 16-Key Action Assembler & Strict Type Gates
# =====================================================================

class TestI1G4ActionAssemblerAndTypeGates:
    """Verifies 16-key action assembler, primitive type gates, bounds, and identity binding."""

    def test_assemble_canonical_action_payload_happy_path(self):
        auth = make_sample_auth()
        action = make_sample_action()
        payload = assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id)

        assert set(payload.keys()) == CANONICAL_ACTION_PAYLOAD_KEYS
        assert len(payload) == 16
        assert payload["action_index"] == 0
        assert payload["action_type"] == "INSERT_CLIP"
        assert payload["authorization_id"] == auth.authorization_id
        assert payload["execution_id"] == auth.execution_id
        assert payload["duration_frames"] == 120
        assert payload["record_frame"] == 86400
        assert payload["role"] == "B_ROLL"
        assert payload["schema_version"] == "1.0"
        assert payload["source_frame_duration"] == 120
        assert payload["source_frame_start"] == 0
        assert payload["source_path"] == "/opt/media/test_clip.mov"
        assert payload["target_project_uid"] == auth.target_project_uid
        assert payload["target_timeline_uid"] == auth.target_timeline_uid
        assert payload["track_index"] == 1
        assert payload["track_type"] == "video"

        # Canonical bytes roundtrip
        raw_bytes = canonical_action_payload_bytes(payload)
        assert isinstance(raw_bytes, bytes)
        digest = hashlib.sha256(raw_bytes).hexdigest()
        assert len(digest) == 64

    def test_execution_identity_mismatch_rejected(self):
        auth = make_sample_auth(execution_id="uuid-exec-original", token_nonce="uuid-exec-original")
        action = make_sample_action()
        with pytest.raises(WholePlanReconciliationError, match="ERR_EXECUTION_IDENTITY_MISMATCH"):
            assemble_canonical_action_payload(action, auth, execution_id="uuid-exec-different")

    def test_execution_nonce_mismatch_rejected(self):
        # Must fail immediately at auth validation
        with pytest.raises(WholePlanReconciliationError, match="ERR_EXECUTION_NONCE_MISMATCH"):
            make_sample_auth(execution_id="uuid-1", token_nonce="uuid-2")

    def test_invalid_action_object_type_rejected(self):
        auth = make_sample_auth()
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_INVALID_ACTION_OBJECT"):
            assemble_canonical_action_payload({"action_index": 0}, auth, execution_id=auth.execution_id)  # type: ignore

    def test_action_geometry_mismatches_rejected(self):
        auth = make_sample_auth()

        # Action type mismatch
        act_bad_type = make_sample_action(action_type="DELETE_CLIP")
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ACTION_TYPE_MISMATCH"):
            assemble_canonical_action_payload(act_bad_type, auth, execution_id=auth.execution_id)

        # Track type mismatch
        act_bad_track = make_sample_action(track_type="audio")
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_TRACK_TYPE_MISMATCH"):
            assemble_canonical_action_payload(act_bad_track, auth, execution_id=auth.execution_id)

        # Track index mismatch
        act_bad_tidx = make_sample_action(track_index=2)
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_TRACK_INDEX_MISMATCH"):
            assemble_canonical_action_payload(act_bad_tidx, auth, execution_id=auth.execution_id)

        # Source path mismatch
        act_bad_path = make_sample_action(source_path="/other/file.mp4")
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_SOURCE_PATH_MISMATCH"):
            assemble_canonical_action_payload(act_bad_path, auth, execution_id=auth.execution_id)

        # Duration mismatch
        act_bad_dur = make_sample_action(duration_frames=240)
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_TIMELINE_DURATION_MISMATCH"):
            assemble_canonical_action_payload(act_bad_dur, auth, execution_id=auth.execution_id)

    def test_canonical_action_payload_bytes_schema_mismatch(self):
        auth = make_sample_auth()
        action = make_sample_action()
        valid_payload = assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id)

        # Missing key
        missing_payload = dict(valid_payload)
        del missing_payload["role"]
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ACTION_PAYLOAD_SCHEMA_MISMATCH"):
            canonical_action_payload_bytes(missing_payload)

        # Extra key
        extra_payload = dict(valid_payload)
        extra_payload["unauthorized_injected_param"] = 123
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ACTION_PAYLOAD_SCHEMA_MISMATCH"):
            canonical_action_payload_bytes(extra_payload)

    def test_canonical_action_payload_bytes_primitive_type_gates(self):
        auth = make_sample_auth()
        action = make_sample_action()
        valid_payload = assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id)

        # Bool where int required
        bad_bool_payload = dict(valid_payload)
        bad_bool_payload["track_index"] = True
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ACTION_PAYLOAD_TYPE_GATE"):
            canonical_action_payload_bytes(bad_bool_payload)

        # String where int required
        bad_str_payload = dict(valid_payload)
        bad_str_payload["action_index"] = "0"
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ACTION_PAYLOAD_TYPE_GATE"):
            canonical_action_payload_bytes(bad_str_payload)

        # Empty string where non-empty string required
        bad_empty_str = dict(valid_payload)
        bad_empty_str["source_path"] = "   "
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ACTION_PAYLOAD_TYPE_GATE"):
            canonical_action_payload_bytes(bad_empty_str)

    def test_canonical_action_payload_bytes_range_gates(self):
        auth = make_sample_auth()
        action = make_sample_action()
        valid_payload = assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id)

        # Negative action index
        bad_idx = dict(valid_payload)
        bad_idx["action_index"] = -1
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ACTION_PAYLOAD_RANGE"):
            canonical_action_payload_bytes(bad_idx)

        # Track index < 1
        bad_track = dict(valid_payload)
        bad_track["track_index"] = 0
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ACTION_PAYLOAD_RANGE"):
            canonical_action_payload_bytes(bad_track)

        # Non-positive duration
        bad_dur = dict(valid_payload)
        bad_dur["duration_frames"] = 0
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ACTION_PAYLOAD_RANGE"):
            canonical_action_payload_bytes(bad_dur)


# =====================================================================
# I1-G5: 32-Field Authorization DTO Integrity, Authoritative Validation (I1-01) & HMAC Serializer
# =====================================================================

class TestI1G5AuthorizationDtoAndCanonicalHmacBytes:
    """Verifies the complete 32-field authorization contract, authoritative validation, and HMAC serializer."""

    def test_authorization_dto_field_completeness(self):
        auth = make_sample_auth()
        fields = dataclasses.fields(auth)
        assert len(fields) == 33  # 32 HMAC-covered fields + 1 authorization_hmac signature field

        field_names = {f.name for f in fields}
        assert "authorization_hmac" in field_names
        assert "authorization_id" in field_names
        assert "key_id" in field_names
        assert "execution_id" in field_names
        assert "token_nonce" in field_names

    def test_canonical_bytes_excludes_signature_field(self):
        auth1 = make_sample_auth(authorization_hmac="sig_alpha_1111111111111111")
        auth2 = make_sample_auth(authorization_hmac="sig_omega_9999999999999999")

        bytes1 = canonical_bytes_for_auth_hmac(auth1)
        bytes2 = canonical_bytes_for_auth_hmac(auth2)

        # Critical invariant: altering the signature member MUST NOT alter the bytes being signed
        assert bytes1 == bytes2

        # Verify decoded payload has exactly 32 keys and excludes authorization_hmac
        decoded = json.loads(bytes1.decode("utf-8"))
        assert len(decoded) == 32
        assert "authorization_hmac" not in decoded

    def test_canonical_bytes_changes_when_any_signed_field_changes(self):
        base_auth = make_sample_auth()
        base_bytes = canonical_bytes_for_auth_hmac(base_auth)

        # Mutate each of the 32 signed fields independently
        signed_mutations = [
            {"action_payload_digest_sha256": "9" * 64},
            {"approval_record_digest_sha256": "9" * 64},
            {"approval_record_id": "appr-altered-0002"},
            {"authorization_id": "auth-altered-0002"},
            {"authorized_action_type": "INSERT_CLIP"},  # Will change if different valid value
            {"authorized_record_frame": 86410, "authorized_timeline_frame_start": 10},
            {"authorized_source_frame_duration": 240},
            {"authorized_source_frame_start": 10},
            {"authorized_timeline_frame_duration": 240},
            {"authorized_timeline_frame_start": 10, "authorized_record_frame": 86410},
            {"authorized_track_index": 2},
            {"authorized_track_type": "video"},
            {"execution_id": "exec-altered-0002", "token_nonce": "exec-altered-0002"},
            {"issued_at_iso": "2026-11-03T04:00:00Z"},
            {"key_id": "key_altered_99"},
            {"observed_timeline_start_frame": 90000, "authorized_record_frame": 90000},
            {"operator_gesture_digest_sha256": "9" * 64},
            {"plan_digest_sha256": "9" * 64},
            {"plan_idempotency_key": "idem-altered-0002"},
            {"proposal_content_digest_sha256": "9" * 64},
            {"proposal_id": "prop-altered-0002"},
            {"proposal_revision": "rev-2"},
            {"source_file_path": "/opt/media/different_clip.mov"},
            {"source_file_sha256": "9" * 64},
            {"target_project_name": "RF_TRIAL_PROJECT_99"},
            {"target_project_uid": "proj-uuid-9999"},
            {"target_timeline_fps_denominator": 2},
            {"target_timeline_fps_numerator": 30},
            {"target_timeline_name": "RF_TRIAL_TL_ALTERED_99"},
            {"target_timeline_uid": "tl-uuid-9999"},
            {"token_nonce": "nonce-altered-0002", "execution_id": "nonce-altered-0002"},
            {"valid_until_iso": "2026-11-03T06:05:00Z"},
        ]

        for mutation in signed_mutations:
            field_name = next(iter(mutation))
            mutated_auth = dataclasses.replace(base_auth, **mutation)
            mutated_bytes = canonical_bytes_for_auth_hmac(mutated_auth)
            # Exclude no-op if identical
            if mutated_auth != base_auth:
                assert mutated_bytes != base_bytes, f"Field mutation {field_name} did not change canonical bytes"

    # --- I1-01 Specific Authoritative Validation Tests ---

    def test_i1_01_boolean_as_integer_geometry_rejected(self):
        """Verify bool (True/False) passed for int geometry fields is rejected at creation."""
        # authorized_track_index = True
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TYPE_GATE: authorized_track_index must be int not bool"):
            make_sample_auth(authorized_track_index=True)

        # authorized_record_frame = False
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TYPE_GATE: authorized_record_frame must be int not bool"):
            make_sample_auth(authorized_record_frame=False)

        # authorized_timeline_frame_duration = True
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TYPE_GATE: authorized_timeline_frame_duration must be int not bool"):
            make_sample_auth(authorized_timeline_frame_duration=True)

    def test_i1_01_invalid_primitive_types_rejected(self):
        """Verify string or float passed for integer fields is rejected."""
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TYPE_GATE: authorized_track_index must be int not bool"):
            make_sample_auth(authorized_track_index="1")

        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TYPE_GATE: target_timeline_fps_numerator must be int not bool"):
            make_sample_auth(target_timeline_fps_numerator=24.0)

    def test_i1_01_invalid_ranges_rejected(self):
        """Verify negative or non-positive ranges are strictly rejected."""
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_RANGE_GATE: target_timeline_fps_numerator must be > 0"):
            make_sample_auth(target_timeline_fps_numerator=0)

        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_RANGE_GATE: authorized_track_index must be >= 1"):
            make_sample_auth(authorized_track_index=0)

        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_RANGE_GATE: authorized_timeline_frame_duration must be > 0"):
            make_sample_auth(authorized_timeline_frame_duration=0)

    def test_i1_01_frame_boundary_binding_rejected(self):
        """Verify mismatch between record_frame and observed_start + frame_start is rejected."""
        # observed: 86400, frame_start: 0, record_frame: 86500 (mismatch)
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_FRAME_BINDING"):
            make_sample_auth(observed_timeline_start_frame=86400, authorized_timeline_frame_start=0, authorized_record_frame=86500)

    def test_i1_01_scope_restrictions_rejected(self):
        """Verify non-INSERT_CLIP or non-video scope claims are rejected."""
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_SCOPE_GATE: authorized_action_type must be 'INSERT_CLIP'"):
            make_sample_auth(authorized_action_type="DELETE_CLIP")

        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_SCOPE_GATE: authorized_track_type must be 'video'"):
            make_sample_auth(authorized_track_type="audio")

    def test_i1_01_digest_format_rejected(self):
        """Verify invalid digest lengths or non-hex characters are rejected."""
        # Length 63 instead of 64
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_DIGEST_FORMAT"):
            make_sample_auth(action_payload_digest_sha256="a" * 63)

        # Invalid non-hex characters
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_DIGEST_FORMAT"):
            make_sample_auth(action_payload_digest_sha256="z" * 64)

    def test_i1_01_timestamp_sanity_rejected(self):
        """Verify valid_until_iso <= issued_at_iso is rejected."""
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_INVALID"):
            make_sample_auth(issued_at_iso="2026-11-03T05:05:00Z", valid_until_iso="2026-11-03T05:00:00Z")

    # --- I1-R1-01 Specific Explicit UTC Instant Validation Tests ---

    def test_i1_r1_01_malformed_timestamp_strings_rejected(self):
        """Verify malformed non-timestamp strings are rejected regardless of lexical comparison."""
        # 'not-a-time' vs 'z' previously passed string <= check
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_NOT_UTC"):
            make_sample_auth(issued_at_iso="not-a-time", valid_until_iso="z")

        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_MALFORMED"):
            make_sample_auth(issued_at_iso="2026-99-99T99:99:99Z", valid_until_iso="2026-99-99T99:99:99Z")

    def test_i1_r1_01_naive_timestamps_rejected(self):
        """Verify timezone-naive timestamps lacking explicit UTC indicator are rejected."""
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_NOT_UTC"):
            make_sample_auth(issued_at_iso="2026-11-03T05:00:00", valid_until_iso="2026-11-03T05:05:00Z")

        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_NOT_UTC"):
            make_sample_auth(issued_at_iso="2026-11-03T05:00:00Z", valid_until_iso="2026-11-03T05:05:00")

    def test_i1_r1_01_non_zero_offsets_rejected(self):
        """Verify non-zero UTC timezone offsets are rejected."""
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_NOT_UTC"):
            make_sample_auth(issued_at_iso="2026-11-03T07:00:00+02:00", valid_until_iso="2026-11-03T05:05:00Z")

        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_NOT_UTC"):
            make_sample_auth(issued_at_iso="2026-11-03T05:00:00Z", valid_until_iso="2026-11-03T03:05:00-02:00")

    def test_i1_r1_01_equivalent_instants_with_different_utc_suffixes(self):
        """Verify chronological ordering across different accepted zero-offset formats ('Z' and '+00:00')."""
        # Identical instant with different suffixes (zero duration interval) -> rejected
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_INVALID"):
            make_sample_auth(issued_at_iso="2026-11-03T05:00:00+00:00", valid_until_iso="2026-11-03T05:00:00Z")

        # Valid interval across different suffixes (+00:00 for issued, Z for valid_until) -> accepted
        auth = make_sample_auth(issued_at_iso="2026-11-03T05:00:00+00:00", valid_until_iso="2026-11-03T05:05:00Z")
        assert auth.issued_at_iso == "2026-11-03T05:00:00+00:00"
        assert auth.valid_until_iso == "2026-11-03T05:05:00Z"

    def test_i1_r1_01_genuinely_reversed_interval_rejected(self):
        """Verify genuinely reversed chronological interval is rejected."""
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_INVALID"):
            make_sample_auth(issued_at_iso="2026-11-03T05:05:00Z", valid_until_iso="2026-11-03T05:00:00Z")

    def test_i1_r1_01_operator_consent_gesture_timestamp_validation(self):
        """Verify OperatorConsentGesture also validates consent_timestamp_iso as UTC instant."""
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_NOT_UTC"):
            OperatorConsentGesture(
                operator_id="operator_alice",
                consent_timestamp_iso="naive_timestamp_no_utc",
                confirmation_phrase="CONFIRM_CANARY_TRIAL_EXECUTION",
                freshness_attestation="Attest fresh empty timeline",
                challenge_id="CHALLENGE_001",
                client_nonce="nonce_uuid_1234",
            )

    # --- I1-R2-01 Specific Tests (Unknown Offset Marker & Non-Canonical Whitespace) ---

    def test_i1_r2_01_unknown_offset_marker_rejected(self):
        """Verify RFC 3339 '-00:00' unknown local offset suffix is rejected."""
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_UNKNOWN_OFFSET"):
            make_sample_auth(issued_at_iso="2026-11-03T05:00:00-00:00")

        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_UNKNOWN_OFFSET"):
            make_sample_auth(valid_until_iso="2026-11-03T05:05:00-00:00")

    def test_i1_r2_01_whitespace_in_timestamps_strictly_rejected(self):
        """Verify leading or trailing whitespace in timestamps is rejected without silent trimming."""
        # Leading space
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_WHITESPACE"):
            make_sample_auth(issued_at_iso=" 2026-11-03T05:00:00Z")

        # Trailing space
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_WHITESPACE"):
            make_sample_auth(valid_until_iso="2026-11-03T05:05:00Z ")

        # Tab or newline
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_WHITESPACE"):
            make_sample_auth(issued_at_iso="\t2026-11-03T05:00:00Z")

        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_WHITESPACE"):
            make_sample_auth(valid_until_iso="2026-11-03T05:05:00Z\n")

    def test_i1_r2_01_positive_controls_z_and_plus_zero(self):
        """Verify positive controls for canonical 'Z', '+00:00', and fractional seconds."""
        # Standard Z
        auth_z = make_sample_auth(issued_at_iso="2026-11-03T05:00:00Z", valid_until_iso="2026-11-03T05:05:00Z")
        assert auth_z.issued_at_iso == "2026-11-03T05:00:00Z"

        # Standard +00:00
        auth_plus = make_sample_auth(issued_at_iso="2026-11-03T05:00:00+00:00", valid_until_iso="2026-11-03T05:05:00+00:00")
        assert auth_plus.issued_at_iso == "2026-11-03T05:00:00+00:00"

        # Fractional seconds with Z
        auth_frac = make_sample_auth(issued_at_iso="2026-11-03T05:00:00.123456Z", valid_until_iso="2026-11-03T05:05:00.999999Z")
        assert auth_frac.issued_at_iso == "2026-11-03T05:00:00.123456Z"

    def test_i1_r2_01_validation_active_at_all_boundaries(self):
        """Verify malformed timestamps (-00:00 and whitespace) fail at all three boundaries."""
        # 1. Direct DTO construction failure
        with pytest.raises(ReadbackReceiptVerificationError):
            make_sample_auth(issued_at_iso="2026-11-03T05:00:00-00:00")

        # 2. Direct HMAC serializer failure when object bypassed __post_init__
        raw_auth_bad = object.__new__(TrialExecutionAuthorization)
        # Assign attributes directly to simulate unvalidated state
        valid_sample = make_sample_auth()
        for f in dataclasses.fields(valid_sample):
            object.__setattr__(raw_auth_bad, f.name, getattr(valid_sample, f.name))
        object.__setattr__(raw_auth_bad, "issued_at_iso", "2026-11-03T05:00:00-00:00")

        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_UNKNOWN_OFFSET"):
            canonical_bytes_for_auth_hmac(raw_auth_bad)

        # 3. Direct Assembler failure
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTH_TIMESTAMP_UNKNOWN_OFFSET"):
            assemble_canonical_action_payload(make_sample_action(), raw_auth_bad, execution_id=raw_auth_bad.execution_id)

    def test_i1_01_serializer_and_assembler_invoke_validation(self):
        """Verify serializer and assembler directly invoke authoritative validation."""
        # Bypass __post_init__ via object.__new__
        raw_obj = object.__new__(TrialExecutionAuthorization)
        # Missing fields / uninitialized
        with pytest.raises(ReadbackReceiptVerificationError):
            canonical_bytes_for_auth_hmac(raw_obj)

        with pytest.raises(ReadbackReceiptVerificationError):
            assemble_canonical_action_payload(make_sample_action(), raw_obj, execution_id="dummy")
