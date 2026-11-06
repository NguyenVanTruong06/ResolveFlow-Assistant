"""Unit tests for SignedOperatorConsentEnvelope (G07-07B7-I1).

Verifies:
- Exact 23-field schema and round-trip equality
- Frozen immutability
- Pinned RFC 8785 canonical byte vectors and digest behavior
- Strict exclusion of operator_signature from signing payload bytes
- Inclusion of operator_signature in complete envelope bytes and consent digest
- Missing and unknown fields in from_dict
- bool-as-int and scalar type gates across all numeric/boolean fields
- Malformed/wrong-length digests, uppercase hex rejection
- Canonical lowercase UUIDv4 nonce and challenge ID grammar validation
- Fixed action/track literals and geometry bounds
- Absolute source file path validation
- Non-UTC, naive, malformed, and whitespace-padded timestamps
- Isolation of exported mappings (defense against mutation)
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Dict

import pytest

from src.core.contracts.signed_operator_consent_envelope import (
    SignedOperatorConsentEnvelope,
    SignedOperatorConsentEnvelopeValidationError,
)


def make_valid_signed_operator_consent_envelope(**overrides: Any) -> SignedOperatorConsentEnvelope:
    """Helper creating a standard valid SignedOperatorConsentEnvelope with optional overrides."""
    kwargs: Dict[str, Any] = {
        "schema_version": 1,
        "operator_id": "op_vanntruongg",
        "challenge_id": "CHALLENGE_2026_CANARY_01",
        "client_nonce": "a1b2c3d4-e5f6-47a8-b9c0-d1e2f3a4b5c6",
        "confirmation_phrase": "CONFIRM_CANARY_TRIAL_EXECUTION",
        "consent_timestamp_iso": "2026-11-03T15:10:00Z",
        "freshness_attestation": "OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED",
        "approval_envelope_digest_sha256": "1" * 64,
        "plan_digest_sha256": "2" * 64,
        "action_payload_digest_sha256": "3" * 64,
        "target_project_name": "RF_TRIAL_CANARY_P02",
        "target_project_uid": "01j7b9c4-d2e1-48a3-b5c7-d9e0f1a2b3c4",
        "target_timeline_name": "RF_TRIAL_TL_CANARY_T02",
        "target_timeline_uid": "01j7b9c4-d2e1-48a3-b5c7-d9e0f1a2b3c5",
        "source_file_path": "/var/media/resolveflow/canary_sample.mov",
        "source_file_sha256": "4" * 64,
        "action_type": "INSERT_CLIP",
        "track_type": "video",
        "track_index": 1,
        "record_frame": 86400,
        "duration_frames": 120,
        "operator_key_id": "op_key_2026_ed25519",
        "operator_signature": "d" * 128,
    }
    kwargs.update(overrides)
    return SignedOperatorConsentEnvelope(**kwargs)


class TestSignedOperatorConsentEnvelope:
    """Test suite for SignedOperatorConsentEnvelope."""

    def test_exact_field_set_and_attributes(self):
        env = make_valid_signed_operator_consent_envelope()
        assert env.schema_version == 1
        assert env.operator_id == "op_vanntruongg"
        assert env.challenge_id == "CHALLENGE_2026_CANARY_01"
        assert env.client_nonce == "a1b2c3d4-e5f6-47a8-b9c0-d1e2f3a4b5c6"
        assert env.confirmation_phrase == "CONFIRM_CANARY_TRIAL_EXECUTION"
        assert env.consent_timestamp_iso == "2026-11-03T15:10:00Z"
        assert env.freshness_attestation == "OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED"
        assert env.approval_envelope_digest_sha256 == "1" * 64
        assert env.plan_digest_sha256 == "2" * 64
        assert env.action_payload_digest_sha256 == "3" * 64
        assert env.target_project_name == "RF_TRIAL_CANARY_P02"
        assert env.target_project_uid == "01j7b9c4-d2e1-48a3-b5c7-d9e0f1a2b3c4"
        assert env.target_timeline_name == "RF_TRIAL_TL_CANARY_T02"
        assert env.target_timeline_uid == "01j7b9c4-d2e1-48a3-b5c7-d9e0f1a2b3c5"
        assert env.source_file_path == "/var/media/resolveflow/canary_sample.mov"
        assert env.source_file_sha256 == "4" * 64
        assert env.action_type == "INSERT_CLIP"
        assert env.track_type == "video"
        assert env.track_index == 1
        assert env.record_frame == 86400
        assert env.duration_frames == 120
        assert env.operator_key_id == "op_key_2026_ed25519"
        assert env.operator_signature == "d" * 128

    def test_frozen_immutability(self):
        env = make_valid_signed_operator_consent_envelope()
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.schema_version = 2  # type: ignore
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.operator_signature = "e" * 128  # type: ignore
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.track_index = 2  # type: ignore
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.target_timeline_name = "tampered"  # type: ignore

    def test_round_trip_to_dict_and_from_dict(self):
        env = make_valid_signed_operator_consent_envelope()
        d = env.to_dict()
        assert isinstance(d, dict)
        assert len(d) == 23
        assert d["confirmation_phrase"] == "CONFIRM_CANARY_TRIAL_EXECUTION"

        reconstructed = SignedOperatorConsentEnvelope.from_dict(d)
        assert reconstructed == env
        assert reconstructed.canonical_envelope_bytes() == env.canonical_envelope_bytes()

    def test_pinned_canonical_byte_vectors(self):
        env = make_valid_signed_operator_consent_envelope()

        signing_bytes = env.canonical_signing_payload_bytes()
        envelope_bytes = env.canonical_envelope_bytes()

        assert isinstance(signing_bytes, bytes)
        assert isinstance(envelope_bytes, bytes)

        # Signing bytes strictly exclude operator_signature (22 fields)
        assert b"operator_signature" not in signing_bytes
        assert "operator_signature" not in env.canonical_signing_payload_dict()
        assert len(env.canonical_signing_payload_dict()) == 22

        # Complete envelope bytes strictly include operator_signature (23 fields)
        assert b"operator_signature" in envelope_bytes
        assert "operator_signature" in env.to_dict()
        assert len(env.to_dict()) == 23

        # Pinned start of sorted canonical JSON
        assert signing_bytes.startswith(b'{"action_payload_digest_sha256":"3333333333333333333333333333333333333333333333333333333333333333"')

        # Consent digest matches sha256 of canonical envelope bytes
        consent_digest = env.compute_consent_digest()
        assert len(consent_digest) == 64
        assert consent_digest == hashlib.sha256(envelope_bytes).hexdigest()

    def test_signature_mutation_affects_envelope_not_signing_payload(self):
        env1 = make_valid_signed_operator_consent_envelope(operator_signature="a" * 128)
        env2 = make_valid_signed_operator_consent_envelope(operator_signature="b" * 128)

        # Signing payload bytes remain identical
        assert env1.canonical_signing_payload_bytes() == env2.canonical_signing_payload_bytes()
        assert env1.canonical_signing_payload_dict() == env2.canonical_signing_payload_dict()

        # Complete envelope bytes and digests change
        assert env1.canonical_envelope_bytes() != env2.canonical_envelope_bytes()
        assert env1.compute_consent_digest() != env2.compute_consent_digest()

    def test_payload_field_mutation_affects_signing_bytes_and_envelope_bytes(self):
        env1 = make_valid_signed_operator_consent_envelope(record_frame=86400)
        env2 = make_valid_signed_operator_consent_envelope(record_frame=0)

        assert env1.canonical_signing_payload_bytes() != env2.canonical_signing_payload_bytes()
        assert env1.canonical_envelope_bytes() != env2.canonical_envelope_bytes()
        assert env1.compute_consent_digest() != env2.compute_consent_digest()

    def test_exported_mapping_mutation_defense(self):
        env = make_valid_signed_operator_consent_envelope()
        d = env.to_dict()
        orig_bytes = env.canonical_envelope_bytes()

        # Mutate dictionary
        d["track_index"] = 999
        d["confirmation_phrase"] = "TAMPERED"

        # Verify frozen instance remains pristine
        assert env.track_index == 1
        assert env.confirmation_phrase == "CONFIRM_CANARY_TRIAL_EXECUTION"
        assert env.canonical_envelope_bytes() == orig_bytes

    def test_from_dict_rejects_missing_keys(self):
        d = make_valid_signed_operator_consent_envelope().to_dict()
        del d["client_nonce"]
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_SCHEMA_FIELD_MISMATCH"):
            SignedOperatorConsentEnvelope.from_dict(d)

    def test_from_dict_rejects_extra_keys(self):
        d = make_valid_signed_operator_consent_envelope().to_dict()
        d["unauthorized_override"] = "injection"
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_SCHEMA_FIELD_MISMATCH"):
            SignedOperatorConsentEnvelope.from_dict(d)

    def test_from_dict_rejects_non_mapping(self):
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_PAYLOAD_NOT_MAPPING"):
            SignedOperatorConsentEnvelope.from_dict("not-a-mapping")  # type: ignore

    def test_schema_version_type_gates_reject_bool_as_int(self):
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_SCHEMA_VERSION_TYPE"):
            make_valid_signed_operator_consent_envelope(schema_version=True)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_SCHEMA_VERSION_TYPE"):
            make_valid_signed_operator_consent_envelope(schema_version=False)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_SCHEMA_VERSION_TYPE"):
            make_valid_signed_operator_consent_envelope(schema_version="1")

    def test_schema_version_unsupported_int(self):
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_UNSUPPORTED_SCHEMA_VERSION"):
            make_valid_signed_operator_consent_envelope(schema_version=0)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_UNSUPPORTED_SCHEMA_VERSION"):
            make_valid_signed_operator_consent_envelope(schema_version=2)

    def test_operator_id_validation(self):
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_OPERATOR_ID_FORMAT"):
            make_valid_signed_operator_consent_envelope(operator_id="")
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_OPERATOR_ID_FORMAT"):
            make_valid_signed_operator_consent_envelope(operator_id=" op_padded ")
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_OPERATOR_ID_FORMAT"):
            make_valid_signed_operator_consent_envelope(operator_id=12345)

    def test_challenge_id_token_grammar(self):
        # Too short (< 4 chars)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CHALLENGE_ID_FORMAT"):
            make_valid_signed_operator_consent_envelope(challenge_id="abc")

        # Too long (> 64 chars)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CHALLENGE_ID_FORMAT"):
            make_valid_signed_operator_consent_envelope(challenge_id="a" * 65)

        # Invalid characters (space, punctuation)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CHALLENGE_ID_FORMAT"):
            make_valid_signed_operator_consent_envelope(challenge_id="chal with space")
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CHALLENGE_ID_FORMAT"):
            make_valid_signed_operator_consent_envelope(challenge_id="chal@123")

        # Valid challenge tokens
        assert make_valid_signed_operator_consent_envelope(challenge_id="A7B9_4C2D").challenge_id == "A7B9_4C2D"
        assert make_valid_signed_operator_consent_envelope(challenge_id="CHALLENGE-001").challenge_id == "CHALLENGE-001"

    def test_client_nonce_uuidv4_grammar(self):
        # Valid canonical lowercase UUIDv4
        env = make_valid_signed_operator_consent_envelope(client_nonce="f47ac10b-58cc-4372-a567-0e02b2c3d479")
        assert env.client_nonce == "f47ac10b-58cc-4372-a567-0e02b2c3d479"

        # Uppercase UUIDv4 strictly rejected (canonical lowercase required)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CLIENT_NONCE_FORMAT"):
            make_valid_signed_operator_consent_envelope(client_nonce="F47AC10B-58CC-4372-A567-0E02B2C3D479")

        # UUID version 1 rejected (not version 4)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CLIENT_NONCE_FORMAT"):
            make_valid_signed_operator_consent_envelope(client_nonce="f47ac10b-58cc-1372-a567-0e02b2c3d479")

        # Non-UUID string
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CLIENT_NONCE_FORMAT"):
            make_valid_signed_operator_consent_envelope(client_nonce="not-a-uuid-string")

        # Non-string
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CLIENT_NONCE_FORMAT"):
            make_valid_signed_operator_consent_envelope(client_nonce=12345678)

    def test_confirmation_phrase_literal_enforcement(self):
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CONFIRMATION_PHRASE_MISMATCH"):
            make_valid_signed_operator_consent_envelope(confirmation_phrase="CONFIRM")
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CONFIRMATION_PHRASE_MISMATCH"):
            make_valid_signed_operator_consent_envelope(confirmation_phrase="confirm_canary_trial_execution")

    def test_freshness_attestation_literal_enforcement(self):
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_FRESHNESS_ATTESTATION_MISMATCH"):
            make_valid_signed_operator_consent_envelope(freshness_attestation="EMPTY_TIMELINE")
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_FRESHNESS_ATTESTATION_MISMATCH"):
            make_valid_signed_operator_consent_envelope(freshness_attestation=123)

    def test_digest_grammar_enforces_64_lowercase_hex(self):
        fields = [
            "approval_envelope_digest_sha256",
            "plan_digest_sha256",
            "action_payload_digest_sha256",
            "source_file_sha256",
        ]
        for f in fields:
            # 63 characters
            with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_DIGEST_FORMAT"):
                make_valid_signed_operator_consent_envelope(**{f: "a" * 63})

            # 65 characters
            with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_DIGEST_FORMAT"):
                make_valid_signed_operator_consent_envelope(**{f: "a" * 65})

            # Uppercase hex rejected
            with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_DIGEST_FORMAT"):
                make_valid_signed_operator_consent_envelope(**{f: ("a" * 63) + "F"})

            # Non-hex rejected
            with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_DIGEST_FORMAT"):
                make_valid_signed_operator_consent_envelope(**{f: "z" * 64})

    def test_target_identifiers_validation(self):
        fields = [
            "target_project_name",
            "target_project_uid",
            "target_timeline_name",
            "target_timeline_uid",
        ]
        for f in fields:
            with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_TARGET_STRING_FORMAT"):
                make_valid_signed_operator_consent_envelope(**{f: ""})
            with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_TARGET_STRING_FORMAT"):
                make_valid_signed_operator_consent_envelope(**{f: "  padded  "})
            with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_TARGET_STRING_FORMAT"):
                make_valid_signed_operator_consent_envelope(**{f: 9999})

    def test_source_file_path_validation(self):
        # Relative path rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_SOURCE_FILE_PATH_FORMAT"):
            make_valid_signed_operator_consent_envelope(source_file_path="relative/path/video.mov")

        # Empty rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_SOURCE_FILE_PATH_FORMAT"):
            make_valid_signed_operator_consent_envelope(source_file_path="")

        # Whitespace-padded rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_SOURCE_FILE_PATH_FORMAT"):
            make_valid_signed_operator_consent_envelope(source_file_path=" /absolute/path.mov ")

        # Valid absolute path accepted
        env = make_valid_signed_operator_consent_envelope(source_file_path="/var/media/clip.mov")
        assert env.source_file_path == "/var/media/clip.mov"

    def test_fixed_action_and_track_literals(self):
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_ACTION_TYPE_MISMATCH"):
            make_valid_signed_operator_consent_envelope(action_type="APPEND_CLIP")

        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_TRACK_TYPE_MISMATCH"):
            make_valid_signed_operator_consent_envelope(track_type="audio")

    def test_track_index_type_and_boundary_gates(self):
        # bool-as-int rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_TRACK_INDEX_TYPE"):
            make_valid_signed_operator_consent_envelope(track_index=True)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_TRACK_INDEX_TYPE"):
            make_valid_signed_operator_consent_envelope(track_index=False)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_TRACK_INDEX_TYPE"):
            make_valid_signed_operator_consent_envelope(track_index="1")

        # Index out of bounds (must be strictly 1 for canary)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_TRACK_INDEX_BOUNDS"):
            make_valid_signed_operator_consent_envelope(track_index=2)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_TRACK_INDEX_BOUNDS"):
            make_valid_signed_operator_consent_envelope(track_index=0)

    def test_record_frame_type_and_boundary_gates(self):
        # bool-as-int rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_RECORD_FRAME_TYPE"):
            make_valid_signed_operator_consent_envelope(record_frame=True)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_RECORD_FRAME_TYPE"):
            make_valid_signed_operator_consent_envelope(record_frame=False)

        # Negative record frame rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_RECORD_FRAME_BOUNDS"):
            make_valid_signed_operator_consent_envelope(record_frame=-1)

        # RFC 8785 / JCS safe integer boundary tests (I1-02)
        # Max safe integer 2^53 - 1 (9007199254740991) accepted
        env_max = make_valid_signed_operator_consent_envelope(record_frame=9_007_199_254_740_991)
        assert env_max.record_frame == 9_007_199_254_740_991

        # First unsafe integer 2^53 (9007199254740992) rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_RECORD_FRAME_BOUNDS"):
            make_valid_signed_operator_consent_envelope(record_frame=9_007_199_254_740_992)

        # Large int64 rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_RECORD_FRAME_BOUNDS"):
            make_valid_signed_operator_consent_envelope(record_frame=10**18)

        # Zero and positive frames accepted
        assert make_valid_signed_operator_consent_envelope(record_frame=0).record_frame == 0
        assert make_valid_signed_operator_consent_envelope(record_frame=86400).record_frame == 86400

    def test_duration_frames_type_and_boundary_gates(self):
        # bool-as-int rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_DURATION_FRAMES_TYPE"):
            make_valid_signed_operator_consent_envelope(duration_frames=True)

        # Non-120 rejected (must be strictly 120 for canary)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_DURATION_FRAMES_BOUNDS"):
            make_valid_signed_operator_consent_envelope(duration_frames=240)
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_DURATION_FRAMES_BOUNDS"):
            make_valid_signed_operator_consent_envelope(duration_frames=0)

    def test_operator_key_id_validation(self):
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_OPERATOR_KEY_ID_FORMAT"):
            make_valid_signed_operator_consent_envelope(operator_key_id="")
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_OPERATOR_KEY_ID_FORMAT"):
            make_valid_signed_operator_consent_envelope(operator_key_id="  op_key  ")

    def test_operator_signature_grammar_enforces_128_lowercase_hex(self):
        # 127 chars
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_OPERATOR_SIGNATURE_FORMAT"):
            make_valid_signed_operator_consent_envelope(operator_signature="d" * 127)

        # 129 chars
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_OPERATOR_SIGNATURE_FORMAT"):
            make_valid_signed_operator_consent_envelope(operator_signature="d" * 129)

        # Uppercase hex rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_OPERATOR_SIGNATURE_FORMAT"):
            make_valid_signed_operator_consent_envelope(operator_signature=("d" * 127) + "E")

        # Non-hex rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_OPERATOR_SIGNATURE_FORMAT"):
            make_valid_signed_operator_consent_envelope(operator_signature="x" * 128)

    def test_consent_timestamp_iso_strict_utc_validation(self):
        # Non-UTC timezone offset rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CONSENT_TIMESTAMP_INVALID"):
            make_valid_signed_operator_consent_envelope(consent_timestamp_iso="2026-11-03T15:10:00+07:00")

        # Naive timestamp rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CONSENT_TIMESTAMP_INVALID"):
            make_valid_signed_operator_consent_envelope(consent_timestamp_iso="2026-11-03T15:10:00")

        # Whitespace rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CONSENT_TIMESTAMP_INVALID"):
            make_valid_signed_operator_consent_envelope(consent_timestamp_iso=" 2026-11-03T15:10:00Z ")

        # RFC 3339 unknown-offset marker -00:00 rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CONSENT_TIMESTAMP_INVALID"):
            make_valid_signed_operator_consent_envelope(consent_timestamp_iso="2026-11-03T15:10:00-00:00")

        # Malformed rejected
        with pytest.raises(SignedOperatorConsentEnvelopeValidationError, match="ERR_CONSENT_TIMESTAMP_INVALID"):
            make_valid_signed_operator_consent_envelope(consent_timestamp_iso="invalid-datetime")

        # Positive control with explicit zero-offset +00:00 accepted
        env = make_valid_signed_operator_consent_envelope(consent_timestamp_iso="2026-11-03T15:10:00+00:00")
        assert env.consent_timestamp_iso == "2026-11-03T15:10:00+00:00"
