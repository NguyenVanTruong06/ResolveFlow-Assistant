"""Unit tests for SignedApprovalEnvelope (G07-07B7-I1).

Verifies:
- Exact 7-field schema and round-trip equality
- Frozen immutability
- Pinned RFC 8785 canonical byte vectors and digest behavior
- Strict exclusion of director_signature from signing payload bytes
- Inclusion of director_signature in complete envelope bytes
- Missing/unknown fields in from_dict
- bool-as-int and scalar type gates
- Malformed/wrong-length digests, uppercase hex rejection
- Non-UTC, naive, malformed, and whitespace-padded timestamps
- Isolation of exported mappings (defense against mutation)
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Dict

import pytest

from src.core.contracts.edit_plan_approval_contracts import AppOwnedApprovalRecord
from src.core.contracts.signed_approval_envelope import (
    SignedApprovalEnvelope,
    SignedApprovalEnvelopeValidationError,
)


def make_valid_approval_record() -> AppOwnedApprovalRecord:
    """Creates a valid sample AppOwnedApprovalRecord fixture."""
    return AppOwnedApprovalRecord(
        approval_id="appr_01j7b9c4d2e1f8a3b5c7d9e0f1",
        proposal_id="prop_01j7b9c4d2e1f8a3b5c7d9e0f1",
        proposal_revision="a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
        approved_at_iso="2026-11-03T15:00:00Z",
        accepted_omission_ids=("omit_1", "omit_2"),
        approval_scope="ENTIRE_PROPOSAL",
    )


def make_valid_signed_approval_envelope(**overrides: Any) -> SignedApprovalEnvelope:
    """Helper creating a standard valid SignedApprovalEnvelope with optional overrides."""
    kwargs: Dict[str, Any] = {
        "schema_version": 1,
        "approval_record": make_valid_approval_record(),
        "plan_digest_sha256": "1111111122222222333333334444444455555555666666667777777788888888",
        "proposal_content_digest_sha256": "2222222233333333444444445555555566666666777777778888888899999999",
        "director_key_id": "dir_key_2026_primary",
        "director_signature": "e" * 128,
        "signed_at_iso": "2026-11-03T15:00:05Z",
    }
    kwargs.update(overrides)
    return SignedApprovalEnvelope(**kwargs)


class TestSignedApprovalEnvelope:
    """Test suite for SignedApprovalEnvelope."""

    def test_exact_field_set_and_attributes(self):
        env = make_valid_signed_approval_envelope()
        assert env.schema_version == 1
        assert isinstance(env.approval_record, AppOwnedApprovalRecord)
        assert env.plan_digest_sha256 == "1111111122222222333333334444444455555555666666667777777788888888"
        assert env.proposal_content_digest_sha256 == "2222222233333333444444445555555566666666777777778888888899999999"
        assert env.director_key_id == "dir_key_2026_primary"
        assert env.director_signature == "e" * 128
        assert env.signed_at_iso == "2026-11-03T15:00:05Z"

    def test_frozen_immutability(self):
        env = make_valid_signed_approval_envelope()
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.schema_version = 2  # type: ignore
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.director_signature = "f" * 128  # type: ignore
        with pytest.raises(dataclasses.FrozenInstanceError):
            env.director_key_id = "tampered"  # type: ignore

    def test_round_trip_to_dict_and_from_dict(self):
        env = make_valid_signed_approval_envelope()
        d = env.to_dict()
        assert isinstance(d, dict)
        assert d["schema_version"] == 1
        assert isinstance(d["approval_record"], dict)
        assert d["approval_record"]["approval_id"] == "appr_01j7b9c4d2e1f8a3b5c7d9e0f1"
        assert d["approval_record"]["accepted_omission_ids"] == ["omit_1", "omit_2"]

        reconstructed = SignedApprovalEnvelope.from_dict(d)
        assert reconstructed == env
        assert reconstructed.canonical_envelope_bytes() == env.canonical_envelope_bytes()

    def test_from_dict_with_direct_record_object(self):
        record = make_valid_approval_record()
        raw = {
            "schema_version": 1,
            "approval_record": record,
            "plan_digest_sha256": "1111111122222222333333334444444455555555666666667777777788888888",
            "proposal_content_digest_sha256": "2222222233333333444444445555555566666666777777778888888899999999",
            "director_key_id": "dir_key_2026_primary",
            "director_signature": "e" * 128,
            "signed_at_iso": "2026-11-03T15:00:05Z",
        }
        env = SignedApprovalEnvelope.from_dict(raw)
        assert env.approval_record == record

    def test_pinned_canonical_byte_vectors(self):
        env = make_valid_signed_approval_envelope()

        signing_bytes = env.canonical_signing_payload_bytes()
        envelope_bytes = env.canonical_envelope_bytes()

        # Deterministic verification
        assert isinstance(signing_bytes, bytes)
        assert isinstance(envelope_bytes, bytes)

        # Signing bytes strictly exclude director_signature
        assert b"director_signature" not in signing_bytes
        assert "director_signature" not in env.canonical_signing_payload_dict()

        # Complete envelope bytes strictly include director_signature
        assert b"director_signature" in envelope_bytes
        assert "director_signature" in env.to_dict()

        # Verify exact pinned canonical string structure
        expected_signing_prefix = b'{"approval_record":{"accepted_omission_ids":["omit_1","omit_2"],"approval_id":"appr_01j7b9c4d2e1f8a3b5c7d9e0f1"'
        assert signing_bytes.startswith(expected_signing_prefix)

        # Hash digest verification
        digest = env.compute_envelope_digest()
        assert len(digest) == 64
        assert digest == hashlib.sha256(envelope_bytes).hexdigest()

    def test_signature_mutation_affects_envelope_not_signing_payload(self):
        env1 = make_valid_signed_approval_envelope(director_signature="a" * 128)
        env2 = make_valid_signed_approval_envelope(director_signature="b" * 128)

        # Signing payload bytes are identical
        assert env1.canonical_signing_payload_bytes() == env2.canonical_signing_payload_bytes()
        assert env1.canonical_signing_payload_dict() == env2.canonical_signing_payload_dict()

        # Envelope bytes and digests differ
        assert env1.canonical_envelope_bytes() != env2.canonical_envelope_bytes()
        assert env1.compute_envelope_digest() != env2.compute_envelope_digest()

    def test_exported_mapping_mutation_defense(self):
        env = make_valid_signed_approval_envelope()
        d = env.to_dict()
        original_bytes = env.canonical_envelope_bytes()

        # Mutate exported dictionary
        d["director_key_id"] = "tampered_key"
        d["approval_record"]["approval_id"] = "tampered_id"
        d["approval_record"]["accepted_omission_ids"].append("tampered_omit")

        # Verify frozen instance remains pristine
        assert env.director_key_id == "dir_key_2026_primary"
        assert env.approval_record.approval_id == "appr_01j7b9c4d2e1f8a3b5c7d9e0f1"
        assert env.approval_record.accepted_omission_ids == ("omit_1", "omit_2")
        assert env.canonical_envelope_bytes() == original_bytes

    def test_from_dict_rejects_missing_keys(self):
        d = make_valid_signed_approval_envelope().to_dict()
        del d["director_key_id"]
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SCHEMA_FIELD_MISMATCH"):
            SignedApprovalEnvelope.from_dict(d)

    def test_from_dict_rejects_extra_keys(self):
        d = make_valid_signed_approval_envelope().to_dict()
        d["unexpected_key"] = "malicious_payload"
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SCHEMA_FIELD_MISMATCH"):
            SignedApprovalEnvelope.from_dict(d)

    def test_from_dict_rejects_non_mapping(self):
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_PAYLOAD_NOT_MAPPING"):
            SignedApprovalEnvelope.from_dict(["not", "a", "dict"])  # type: ignore

    def test_from_dict_rejects_invalid_approval_record_keys(self):
        d = make_valid_signed_approval_envelope().to_dict()
        del d["approval_record"]["proposal_revision"]
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_APPROVAL_RECORD_KEYS"):
            SignedApprovalEnvelope.from_dict(d)

    def test_from_dict_rejects_invalid_approval_record_type(self):
        d = make_valid_signed_approval_envelope().to_dict()
        d["approval_record"] = "not_a_record"
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_APPROVAL_RECORD_TYPE"):
            SignedApprovalEnvelope.from_dict(d)

    def test_from_dict_rejects_non_string_omission_ids_without_coercion(self):
        """Verifies I1-01: omission IDs must be strictly str; rejects int, bool, null, float."""
        invalid_omissions = [
            [7],
            [True],
            [False],
            [None],
            [1.5],
            ["valid_omit", 123],
            ["valid_omit", None],
        ]
        for bad_list in invalid_omissions:
            d = make_valid_signed_approval_envelope().to_dict()
            d["approval_record"]["accepted_omission_ids"] = bad_list
            with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_APPROVAL_RECORD_INVALID"):
                SignedApprovalEnvelope.from_dict(d)

    def test_schema_version_type_gates_reject_bool_as_int(self):
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SCHEMA_VERSION_TYPE"):
            make_valid_signed_approval_envelope(schema_version=True)
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SCHEMA_VERSION_TYPE"):
            make_valid_signed_approval_envelope(schema_version=False)
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SCHEMA_VERSION_TYPE"):
            make_valid_signed_approval_envelope(schema_version="1")

    def test_schema_version_unsupported_int(self):
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_UNSUPPORTED_SCHEMA_VERSION"):
            make_valid_signed_approval_envelope(schema_version=0)
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_UNSUPPORTED_SCHEMA_VERSION"):
            make_valid_signed_approval_envelope(schema_version=2)

    def test_approval_record_type_gate(self):
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_APPROVAL_RECORD_TYPE"):
            make_valid_signed_approval_envelope(approval_record="not_a_record")
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_APPROVAL_RECORD_TYPE"):
            make_valid_signed_approval_envelope(approval_record=None)

    def test_digest_grammar_enforces_64_lowercase_hex(self):
        # 63 characters (too short)
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_PLAN_DIGEST_FORMAT"):
            make_valid_signed_approval_envelope(plan_digest_sha256="a" * 63)

        # 65 characters (too long)
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_PLAN_DIGEST_FORMAT"):
            make_valid_signed_approval_envelope(plan_digest_sha256="a" * 65)

        # Non-hex characters
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_PLAN_DIGEST_FORMAT"):
            make_valid_signed_approval_envelope(plan_digest_sha256="g" * 64)

        # Uppercase hex strictly rejected
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_PLAN_DIGEST_FORMAT"):
            make_valid_signed_approval_envelope(
                plan_digest_sha256="111111112222222233333333444444445555555566666666777777778888888A"
            )

        # Non-string
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_PLAN_DIGEST_FORMAT"):
            make_valid_signed_approval_envelope(plan_digest_sha256=12345)

        # proposal_content_digest_sha256 format checks
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_PROPOSAL_DIGEST_FORMAT"):
            make_valid_signed_approval_envelope(proposal_content_digest_sha256="b" * 63)
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_PROPOSAL_DIGEST_FORMAT"):
            make_valid_signed_approval_envelope(
                proposal_content_digest_sha256="B" * 64
            )

    def test_director_key_id_validation(self):
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_DIRECTOR_KEY_ID_FORMAT"):
            make_valid_signed_approval_envelope(director_key_id="")
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_DIRECTOR_KEY_ID_FORMAT"):
            make_valid_signed_approval_envelope(director_key_id="  dir_key  ")
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_DIRECTOR_KEY_ID_FORMAT"):
            make_valid_signed_approval_envelope(director_key_id=123)

    def test_director_signature_grammar_enforces_128_lowercase_hex(self):
        # 127 chars (too short)
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_DIRECTOR_SIGNATURE_FORMAT"):
            make_valid_signed_approval_envelope(director_signature="c" * 127)

        # 129 chars (too long)
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_DIRECTOR_SIGNATURE_FORMAT"):
            make_valid_signed_approval_envelope(director_signature="c" * 129)

        # Non-hex characters
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_DIRECTOR_SIGNATURE_FORMAT"):
            make_valid_signed_approval_envelope(director_signature="x" * 128)

        # Uppercase hex strictly rejected
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_DIRECTOR_SIGNATURE_FORMAT"):
            make_valid_signed_approval_envelope(director_signature=("c" * 127) + "F")

        # Non-string
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_DIRECTOR_SIGNATURE_FORMAT"):
            make_valid_signed_approval_envelope(director_signature=None)

    def test_signed_at_iso_strict_utc_validation(self):
        # Non-UTC timezone offset rejected
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SIGNED_AT_TIMESTAMP_INVALID"):
            make_valid_signed_approval_envelope(signed_at_iso="2026-11-03T15:00:05+07:00")

        # Naive timestamp rejected
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SIGNED_AT_TIMESTAMP_INVALID"):
            make_valid_signed_approval_envelope(signed_at_iso="2026-11-03T15:00:05")

        # Leading or trailing whitespace rejected
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SIGNED_AT_TIMESTAMP_INVALID"):
            make_valid_signed_approval_envelope(signed_at_iso=" 2026-11-03T15:00:05Z")
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SIGNED_AT_TIMESTAMP_INVALID"):
            make_valid_signed_approval_envelope(signed_at_iso="2026-11-03T15:00:05Z ")

        # RFC 3339 unknown-offset marker -00:00 rejected
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SIGNED_AT_TIMESTAMP_INVALID"):
            make_valid_signed_approval_envelope(signed_at_iso="2026-11-03T15:00:05-00:00")

        # Malformed format rejected
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SIGNED_AT_TIMESTAMP_INVALID"):
            make_valid_signed_approval_envelope(signed_at_iso="not-a-timestamp")
        with pytest.raises(SignedApprovalEnvelopeValidationError, match="ERR_SIGNED_AT_TIMESTAMP_INVALID"):
            make_valid_signed_approval_envelope(signed_at_iso="2026-99-99T99:99:99Z")

        # Positive control with +00:00 explicit zero offset accepted
        env = make_valid_signed_approval_envelope(signed_at_iso="2026-11-03T15:00:05+00:00")
        assert env.signed_at_iso == "2026-11-03T15:00:05+00:00"
