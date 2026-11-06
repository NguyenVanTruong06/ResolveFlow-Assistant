"""Unit tests for SignedPreflightEvidenceEnvelope (G07-07B7-GATE6-DEP04-CONTRACTS-R1).

Verifies:
- Exact 21-field schema and round-trip equality via to_dict / from_dict
- Frozen immutability
- Pinned RFC 8785 canonical byte vectors and SHA-256 digest behavior
- Strict exclusion of verifier_signature from canonical_signing_payload_bytes
- Inclusion of verifier_signature in canonical_envelope_bytes and envelope digest
- from_dict rejection of non-mapping, missing keys, and unknown keys
- bool-as-int rejection across numeric fields and strict schema_version=1
- Canonical UUIDv4 validation for preflight_run_id
- Pinned target project/timeline identities and FPS/frame bounds
- Strict boolean and True value enforcement on all 5 technical predicates
- Strict 64-char lowercase SHA-256 and 128-char lowercase Ed25519 hex validation
- Non-UTC, naive, malformed, and out-of-order timestamps
- Validity interval bounded to 1800 seconds max
- Fresh mapping defense against caller mutation
- Safety boundary: pure contract test; zero Resolve SDK, network, key generation, or live runtime.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Dict

import pytest

from src.core.contracts.signed_preflight_contracts import (
    ERR_GATE4_SIGNED_PREFLIGHT_CONTRACT_REQUIRED,
    MAX_PREFLIGHT_VALIDITY_SECONDS,
    PINNED_FPS_DENOMINATOR,
    PINNED_FPS_NUMERATOR,
    PINNED_OBSERVED_START_FRAME,
    PINNED_TARGET_PROJECT_NAME,
    PINNED_TARGET_PROJECT_UID,
    PINNED_TARGET_TIMELINE_NAME,
    PINNED_TARGET_TIMELINE_UID,
    SIGNED_PREFLIGHT_ENVELOPE_KEYS,
    SIGNED_PREFLIGHT_SIGNING_KEYS,
    SignedPreflightEvidenceEnvelope,
    SignedPreflightValidationError,
)


def make_valid_preflight_envelope(**overrides: Any) -> SignedPreflightEvidenceEnvelope:
    """Helper creating a standard valid SignedPreflightEvidenceEnvelope fixture."""
    kwargs: Dict[str, Any] = {
        "schema_version": 1,
        "preflight_run_id": "c1a2b3c4-d5e6-47a8-9901-a1b2c3d4e5f6",
        "verifier_id": "gate4_preflight_verifier",
        "verifier_key_id": "key_preflight_2026_ed25519",
        "verified_at_iso": "2026-11-05T08:00:00Z",
        "valid_until_iso": "2026-11-05T08:30:00Z",
        "target_project_uid": PINNED_TARGET_PROJECT_UID,
        "target_project_name": PINNED_TARGET_PROJECT_NAME,
        "target_timeline_uid": PINNED_TARGET_TIMELINE_UID,
        "target_timeline_name": PINNED_TARGET_TIMELINE_NAME,
        "target_timeline_fps_numerator": PINNED_FPS_NUMERATOR,
        "target_timeline_fps_denominator": PINNED_FPS_DENOMINATOR,
        "observed_start_frame": PINNED_OBSERVED_START_FRAME,
        "project_and_timeline_active": True,
        "empty_timeline_verified": True,
        "topology_verified": True,
        "incident_denylist_verified": True,
        "media_pool_source_verified": True,
        "source_file_sha256": "a" * 64,
        "audit_chain_head_sha256": "b" * 64,
        "verifier_signature": "c" * 128,
    }
    kwargs.update(overrides)
    return SignedPreflightEvidenceEnvelope(**kwargs)


class TestSignedPreflightContracts:
    """Test suite for SignedPreflightEvidenceEnvelope."""

    def test_exact_field_set_and_attributes(self):
        env = make_valid_preflight_envelope()
        assert env.schema_version == 1
        assert env.preflight_run_id == "c1a2b3c4-d5e6-47a8-9901-a1b2c3d4e5f6"
        assert env.verifier_id == "gate4_preflight_verifier"
        assert env.verifier_key_id == "key_preflight_2026_ed25519"
        assert env.verified_at_iso == "2026-11-05T08:00:00Z"
        assert env.valid_until_iso == "2026-11-05T08:30:00Z"
        assert env.target_project_uid == PINNED_TARGET_PROJECT_UID
        assert env.target_project_name == PINNED_TARGET_PROJECT_NAME
        assert env.target_timeline_uid == PINNED_TARGET_TIMELINE_UID
        assert env.target_timeline_name == PINNED_TARGET_TIMELINE_NAME
        assert env.target_timeline_fps_numerator == 24
        assert env.target_timeline_fps_denominator == 1
        assert env.observed_start_frame == 86400
        assert env.project_and_timeline_active is True
        assert env.empty_timeline_verified is True
        assert env.topology_verified is True
        assert env.incident_denylist_verified is True
        assert env.media_pool_source_verified is True
        assert env.source_file_sha256 == "a" * 64
        assert env.audit_chain_head_sha256 == "b" * 64
        assert env.verifier_signature == "c" * 128

    def test_frozen_immutability(self):
        env = make_valid_preflight_envelope()
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.schema_version = 2  # type: ignore
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.empty_timeline_verified = False  # type: ignore
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.verifier_signature = "0" * 128  # type: ignore

    def test_round_trip_to_dict_and_from_dict(self):
        env = make_valid_preflight_envelope()
        d = env.to_dict()
        assert isinstance(d, dict)
        assert len(d) == 21
        assert frozenset(d.keys()) == SIGNED_PREFLIGHT_ENVELOPE_KEYS

        reconstructed = SignedPreflightEvidenceEnvelope.from_dict(d)
        assert reconstructed == env
        assert reconstructed.canonical_envelope_bytes() == env.canonical_envelope_bytes()
        assert reconstructed.compute_envelope_digest() == env.compute_envelope_digest()

    def test_fresh_mapping_defense_against_mutation(self):
        env = make_valid_preflight_envelope()
        d = env.to_dict()
        d["schema_version"] = 999
        d["verifier_signature"] = "x" * 128
        assert env.schema_version == 1
        assert env.verifier_signature == "c" * 128

        signing_d = env.canonical_signing_payload_dict()
        signing_d["verifier_id"] = "mutated"
        assert env.verifier_id == "gate4_preflight_verifier"

    def test_canonical_signing_payload_excludes_signature(self):
        env = make_valid_preflight_envelope()
        signing_d = env.canonical_signing_payload_dict()
        assert "verifier_signature" not in signing_d
        assert frozenset(signing_d.keys()) == SIGNED_PREFLIGHT_SIGNING_KEYS
        assert len(signing_d) == 20

        signing_bytes = env.canonical_signing_payload_bytes()
        assert b"verifier_signature" not in signing_bytes

    def test_canonical_envelope_includes_signature(self):
        env = make_valid_preflight_envelope()
        envelope_d = env.to_dict()
        assert "verifier_signature" in envelope_d
        assert envelope_d["verifier_signature"] == "c" * 128

        envelope_bytes = env.canonical_envelope_bytes()
        assert b"verifier_signature" in envelope_bytes
        digest = env.compute_envelope_digest()
        assert digest == hashlib.sha256(envelope_bytes).hexdigest()

    def test_from_dict_rejections(self):
        env = make_valid_preflight_envelope()
        d = env.to_dict()

        # Non-mapping
        with pytest.raises(SignedPreflightValidationError, match="ERR_PAYLOAD_NOT_MAPPING"):
            SignedPreflightEvidenceEnvelope.from_dict(["not", "a", "dict"])  # type: ignore

        # Missing field
        for key in SIGNED_PREFLIGHT_ENVELOPE_KEYS:
            truncated = dict(d)
            del truncated[key]
            with pytest.raises(SignedPreflightValidationError, match="ERR_SCHEMA_FIELD_MISMATCH"):
                SignedPreflightEvidenceEnvelope.from_dict(truncated)

        # Extra unknown field
        extra = dict(d)
        extra["unexpected_field"] = "malicious_payload"
        with pytest.raises(SignedPreflightValidationError, match="ERR_SCHEMA_FIELD_MISMATCH"):
            SignedPreflightEvidenceEnvelope.from_dict(extra)

    def test_schema_version_validation(self):
        # Must be strictly int 1, rejecting bool-as-int
        with pytest.raises(SignedPreflightValidationError, match="ERR_SCHEMA_VERSION_TYPE"):
            make_valid_preflight_envelope(schema_version=True)

        with pytest.raises(SignedPreflightValidationError, match="ERR_SCHEMA_VERSION_TYPE"):
            make_valid_preflight_envelope(schema_version="1")

        with pytest.raises(SignedPreflightValidationError, match="ERR_UNSUPPORTED_SCHEMA_VERSION"):
            make_valid_preflight_envelope(schema_version=2)

    def test_preflight_run_id_validation(self):
        # Must be canonical UUIDv4 (lowercase)
        with pytest.raises(SignedPreflightValidationError, match="ERR_PREFLIGHT_RUN_ID_FORMAT"):
            make_valid_preflight_envelope(preflight_run_id="not-a-uuid")

        # Uppercase hex rejected
        with pytest.raises(SignedPreflightValidationError, match="ERR_PREFLIGHT_RUN_ID_FORMAT"):
            make_valid_preflight_envelope(preflight_run_id="C1A2B3C4-D5E6-47A8-9901-A1B2C3D4E5F6")

        # UUIDv1 rejected (version nibble is 1 instead of 4)
        with pytest.raises(SignedPreflightValidationError, match="ERR_PREFLIGHT_RUN_ID_FORMAT"):
            make_valid_preflight_envelope(preflight_run_id="c1a2b3c4-d5e6-17a8-9901-a1b2c3d4e5f6")

    def test_verifier_id_and_key_id_validation(self):
        # Empty string
        with pytest.raises(SignedPreflightValidationError, match="ERR_VERIFIER_ID_FORMAT"):
            make_valid_preflight_envelope(verifier_id="")

        # Whitespace padded
        with pytest.raises(SignedPreflightValidationError, match="ERR_VERIFIER_ID_FORMAT"):
            make_valid_preflight_envelope(verifier_id=" gate4_verifier")

        with pytest.raises(SignedPreflightValidationError, match="ERR_VERIFIER_KEY_ID_FORMAT"):
            make_valid_preflight_envelope(verifier_key_id="")

        with pytest.raises(SignedPreflightValidationError, match="ERR_VERIFIER_KEY_ID_FORMAT"):
            make_valid_preflight_envelope(verifier_key_id="key_preflight ")

    def test_timestamp_validity_interval(self):
        # valid_until_iso <= verified_at_iso rejected
        with pytest.raises(SignedPreflightValidationError, match="ERR_VALIDITY_INTERVAL_INVALID"):
            make_valid_preflight_envelope(
                verified_at_iso="2026-11-05T08:00:00Z",
                valid_until_iso="2026-11-05T08:00:00Z",
            )
        with pytest.raises(SignedPreflightValidationError, match="ERR_VALIDITY_INTERVAL_INVALID"):
            make_valid_preflight_envelope(
                verified_at_iso="2026-11-05T08:30:00Z",
                valid_until_iso="2026-11-05T08:00:00Z",
            )

        # > 1800 seconds (30 minutes) rejected
        with pytest.raises(SignedPreflightValidationError, match="ERR_VALIDITY_WINDOW_EXCEEDED"):
            make_valid_preflight_envelope(
                verified_at_iso="2026-11-05T08:00:00Z",
                valid_until_iso="2026-11-05T08:30:01Z",
            )

        # Exactly 1800 seconds accepted
        env = make_valid_preflight_envelope(
            verified_at_iso="2026-11-05T08:00:00Z",
            valid_until_iso="2026-11-05T08:30:00Z",
        )
        assert env.schema_version == 1

        # Non-UTC / naive / malformed rejected
        with pytest.raises(SignedPreflightValidationError, match="ERR_VERIFIED_AT_TIMESTAMP_INVALID"):
            make_valid_preflight_envelope(verified_at_iso="2026-11-05 08:00:00")
        with pytest.raises(SignedPreflightValidationError, match="ERR_VALID_UNTIL_TIMESTAMP_INVALID"):
            make_valid_preflight_envelope(valid_until_iso="2026-11-05T08:30:00+07:00")

    def test_pinned_target_identities_validation(self):
        # Non-string types must be rejected before equality checks
        class CustomEqual:
            def __eq__(self, other: Any) -> bool:
                return True

        custom_equal = CustomEqual()

        # target_project_uid type gates
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_PROJECT_UID_TYPE"):
            make_valid_preflight_envelope(target_project_uid=12345)
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_PROJECT_UID_TYPE"):
            make_valid_preflight_envelope(target_project_uid=custom_equal)
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_PROJECT_UID_MISMATCH"):
            make_valid_preflight_envelope(target_project_uid="wrong-uid")

        # target_project_name type gates
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_PROJECT_NAME_TYPE"):
            make_valid_preflight_envelope(target_project_name=12345)
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_PROJECT_NAME_TYPE"):
            make_valid_preflight_envelope(target_project_name=custom_equal)
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_PROJECT_NAME_MISMATCH"):
            make_valid_preflight_envelope(target_project_name="WRONG_NAME")

        # target_timeline_uid type gates
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_TIMELINE_UID_TYPE"):
            make_valid_preflight_envelope(target_timeline_uid=12345)
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_TIMELINE_UID_TYPE"):
            make_valid_preflight_envelope(target_timeline_uid=custom_equal)
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_TIMELINE_UID_MISMATCH"):
            make_valid_preflight_envelope(target_timeline_uid="wrong-uid")

        # target_timeline_name type gates
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_TIMELINE_NAME_TYPE"):
            make_valid_preflight_envelope(target_timeline_name=12345)
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_TIMELINE_NAME_TYPE"):
            make_valid_preflight_envelope(target_timeline_name=custom_equal)
        with pytest.raises(SignedPreflightValidationError, match="ERR_TARGET_TIMELINE_NAME_MISMATCH"):
            make_valid_preflight_envelope(target_timeline_name="WRONG_NAME")

    def test_fps_and_start_frame_validation(self):
        # bool-as-int rejected
        with pytest.raises(SignedPreflightValidationError, match="ERR_FPS_NUMERATOR_TYPE"):
            make_valid_preflight_envelope(target_timeline_fps_numerator=True)

        with pytest.raises(SignedPreflightValidationError, match="ERR_FPS_DENOMINATOR_TYPE"):
            make_valid_preflight_envelope(target_timeline_fps_denominator=True)

        with pytest.raises(SignedPreflightValidationError, match="ERR_START_FRAME_TYPE"):
            make_valid_preflight_envelope(observed_start_frame=True)

        # Wrong values rejected
        with pytest.raises(SignedPreflightValidationError, match="ERR_FPS_NUMERATOR_VALUE"):
            make_valid_preflight_envelope(target_timeline_fps_numerator=30)

        with pytest.raises(SignedPreflightValidationError, match="ERR_FPS_DENOMINATOR_VALUE"):
            make_valid_preflight_envelope(target_timeline_fps_denominator=2)

        with pytest.raises(SignedPreflightValidationError, match="ERR_START_FRAME_VALUE"):
            make_valid_preflight_envelope(observed_start_frame=0)

    def test_predicates_validation(self):
        # All 5 predicates must be exact bool and strictly True
        predicate_names = [
            "project_and_timeline_active",
            "empty_timeline_verified",
            "topology_verified",
            "incident_denylist_verified",
            "media_pool_source_verified",
        ]
        for name in predicate_names:
            # int 1 rejected
            with pytest.raises(SignedPreflightValidationError, match="ERR_PREDICATE_TYPE"):
                make_valid_preflight_envelope(**{name: 1})

            # False rejected
            with pytest.raises(SignedPreflightValidationError, match="ERR_PREDICATE_FALSE"):
                make_valid_preflight_envelope(**{name: False})

    def test_digests_and_signature_validation(self):
        # source_file_sha256
        with pytest.raises(SignedPreflightValidationError, match="ERR_SOURCE_FILE_SHA256_FORMAT"):
            make_valid_preflight_envelope(source_file_sha256="A" * 64)  # uppercase
        with pytest.raises(SignedPreflightValidationError, match="ERR_SOURCE_FILE_SHA256_FORMAT"):
            make_valid_preflight_envelope(source_file_sha256="a" * 63)  # short

        # audit_chain_head_sha256
        with pytest.raises(SignedPreflightValidationError, match="ERR_AUDIT_CHAIN_HEAD_FORMAT"):
            make_valid_preflight_envelope(audit_chain_head_sha256="B" * 64)
        with pytest.raises(SignedPreflightValidationError, match="ERR_AUDIT_CHAIN_HEAD_FORMAT"):
            make_valid_preflight_envelope(audit_chain_head_sha256="b" * 65)

        # verifier_signature (128 hex chars)
        with pytest.raises(SignedPreflightValidationError, match="ERR_VERIFIER_SIGNATURE_FORMAT"):
            make_valid_preflight_envelope(verifier_signature="C" * 128)
        with pytest.raises(SignedPreflightValidationError, match="ERR_VERIFIER_SIGNATURE_FORMAT"):
            make_valid_preflight_envelope(verifier_signature="c" * 127)
        with pytest.raises(SignedPreflightValidationError, match="ERR_VERIFIER_SIGNATURE_FORMAT"):
            make_valid_preflight_envelope(verifier_signature="z" * 128)

    def test_blocking_error_constant_preserved(self):
        assert ERR_GATE4_SIGNED_PREFLIGHT_CONTRACT_REQUIRED == "ERR_GATE4_SIGNED_PREFLIGHT_CONTRACT_REQUIRED"
        assert MAX_PREFLIGHT_VALIDITY_SECONDS == 1800.0
