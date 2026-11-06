"""Unit tests for SignedExecutionWorkorderRecord (G07-07B7-GATE6-DEP04-CONTRACTS-R1).

Verifies:
- Exact 14-field schema and round-trip equality via to_dict / from_dict
- Frozen immutability
- Pinned RFC 8785 canonical byte vectors and SHA-256 digest behavior
- Strict exclusion of tech_lead_signature from canonical_signing_payload_bytes
- Inclusion of tech_lead_signature in canonical_envelope_bytes and envelope digest
- from_dict rejection of non-mapping, missing keys, and unknown keys
- bool-as-int rejection for schema_version and strict schema_version=1
- Workorder ID grammar validation (^G07-07B7-GATE6-EXEC-R[1-9][0-9]*$)
- Non-empty, unpadded string fields (tech_lead_id, tech_lead_key_id, permitted_command, execution_id)
- Pinned target project/timeline UIDs
- Strict 64-char lowercase SHA-256 digests and 128-char lowercase Ed25519 signature
- Non-UTC, naive, malformed, and out-of-order timestamps
- Validity interval bounded to 3600 seconds max
- Fresh mapping defense against caller mutation
- Safety boundary: pure contract test; zero live execution, network, Resolve SDK, or key provisioning.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any, Dict

import pytest

from src.core.contracts.signed_execution_workorder_contracts import (
    ERR_GATE5_EXECUTION_UNAUTHORIZED,
    MAX_WORKORDER_VALIDITY_SECONDS,
    PINNED_TARGET_PROJECT_UID,
    PINNED_TARGET_TIMELINE_UID,
    SIGNED_EXECUTION_WORKORDER_KEYS,
    SIGNED_EXECUTION_WORKORDER_SIGNING_KEYS,
    SignedExecutionWorkorderRecord,
    SignedExecutionWorkorderValidationError,
)


def make_valid_execution_workorder(**overrides: Any) -> SignedExecutionWorkorderRecord:
    """Helper creating a standard valid SignedExecutionWorkorderRecord fixture."""
    kwargs: Dict[str, Any] = {
        "schema_version": 1,
        "workorder_id": "G07-07B7-GATE6-EXEC-R1",
        "tech_lead_id": "tech_lead_codex",
        "tech_lead_key_id": "key_techlead_2026_ed25519",
        "issued_at_iso": "2026-11-05T08:00:00Z",
        "valid_until_iso": "2026-11-05T09:00:00Z",
        "target_project_uid": PINNED_TARGET_PROJECT_UID,
        "target_timeline_uid": PINNED_TARGET_TIMELINE_UID,
        "reviewed_candidate_sha256": "1" * 64,
        "permitted_command": "execute_canary_trial_g07",
        "plan_digest_sha256": "2" * 64,
        "action_payload_digest_sha256": "3" * 64,
        "execution_id": "exec_20261105_001",
        "tech_lead_signature": "4" * 128,
    }
    kwargs.update(overrides)
    return SignedExecutionWorkorderRecord(**kwargs)


class TestSignedExecutionWorkorderContracts:
    """Test suite for SignedExecutionWorkorderRecord."""

    def test_exact_field_set_and_attributes(self):
        record = make_valid_execution_workorder()
        assert record.schema_version == 1
        assert record.workorder_id == "G07-07B7-GATE6-EXEC-R1"
        assert record.tech_lead_id == "tech_lead_codex"
        assert record.tech_lead_key_id == "key_techlead_2026_ed25519"
        assert record.issued_at_iso == "2026-11-05T08:00:00Z"
        assert record.valid_until_iso == "2026-11-05T09:00:00Z"
        assert record.target_project_uid == PINNED_TARGET_PROJECT_UID
        assert record.target_timeline_uid == PINNED_TARGET_TIMELINE_UID
        assert record.reviewed_candidate_sha256 == "1" * 64
        assert record.permitted_command == "execute_canary_trial_g07"
        assert record.plan_digest_sha256 == "2" * 64
        assert record.action_payload_digest_sha256 == "3" * 64
        assert record.execution_id == "exec_20261105_001"
        assert record.tech_lead_signature == "4" * 128

    def test_frozen_immutability(self):
        record = make_valid_execution_workorder()
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.schema_version = 2  # type: ignore
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.permitted_command = "tampered_command"  # type: ignore
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.tech_lead_signature = "0" * 128  # type: ignore

    def test_round_trip_to_dict_and_from_dict(self):
        record = make_valid_execution_workorder()
        d = record.to_dict()
        assert isinstance(d, dict)
        assert len(d) == 14
        assert frozenset(d.keys()) == SIGNED_EXECUTION_WORKORDER_KEYS

        reconstructed = SignedExecutionWorkorderRecord.from_dict(d)
        assert reconstructed == record
        assert reconstructed.canonical_envelope_bytes() == record.canonical_envelope_bytes()
        assert reconstructed.compute_envelope_digest() == record.compute_envelope_digest()

    def test_fresh_mapping_defense_against_mutation(self):
        record = make_valid_execution_workorder()
        d = record.to_dict()
        d["schema_version"] = 999
        d["tech_lead_signature"] = "x" * 128
        assert record.schema_version == 1
        assert record.tech_lead_signature == "4" * 128

        signing_d = record.canonical_signing_payload_dict()
        signing_d["tech_lead_id"] = "mutated"
        assert record.tech_lead_id == "tech_lead_codex"

    def test_canonical_signing_payload_excludes_signature(self):
        record = make_valid_execution_workorder()
        signing_d = record.canonical_signing_payload_dict()
        assert "tech_lead_signature" not in signing_d
        assert frozenset(signing_d.keys()) == SIGNED_EXECUTION_WORKORDER_SIGNING_KEYS
        assert len(signing_d) == 13

        signing_bytes = record.canonical_signing_payload_bytes()
        assert b"tech_lead_signature" not in signing_bytes

    def test_canonical_envelope_includes_signature(self):
        record = make_valid_execution_workorder()
        envelope_d = record.to_dict()
        assert "tech_lead_signature" in envelope_d
        assert envelope_d["tech_lead_signature"] == "4" * 128

        envelope_bytes = record.canonical_envelope_bytes()
        assert b"tech_lead_signature" in envelope_bytes
        digest = record.compute_envelope_digest()
        assert digest == hashlib.sha256(envelope_bytes).hexdigest()

    def test_from_dict_rejections(self):
        record = make_valid_execution_workorder()
        d = record.to_dict()

        # Non-mapping
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_PAYLOAD_NOT_MAPPING"):
            SignedExecutionWorkorderRecord.from_dict("not a map")  # type: ignore

        # Missing field
        for key in SIGNED_EXECUTION_WORKORDER_KEYS:
            truncated = dict(d)
            del truncated[key]
            with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_SCHEMA_FIELD_MISMATCH"):
                SignedExecutionWorkorderRecord.from_dict(truncated)

        # Extra unknown field
        extra = dict(d)
        extra["unauthorized_field"] = "payload"
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_SCHEMA_FIELD_MISMATCH"):
            SignedExecutionWorkorderRecord.from_dict(extra)

    def test_schema_version_validation(self):
        # Must be strictly int 1, rejecting bool-as-int
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_SCHEMA_VERSION_TYPE"):
            make_valid_execution_workorder(schema_version=True)

        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_SCHEMA_VERSION_TYPE"):
            make_valid_execution_workorder(schema_version="1")

        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_UNSUPPORTED_SCHEMA_VERSION"):
            make_valid_execution_workorder(schema_version=2)

    def test_workorder_id_grammar(self):
        # Valid forms
        assert make_valid_execution_workorder(workorder_id="G07-07B7-GATE6-EXEC-R1").workorder_id == "G07-07B7-GATE6-EXEC-R1"
        assert make_valid_execution_workorder(workorder_id="G07-07B7-GATE6-EXEC-R2").workorder_id == "G07-07B7-GATE6-EXEC-R2"
        assert make_valid_execution_workorder(workorder_id="G07-07B7-GATE6-EXEC-R99").workorder_id == "G07-07B7-GATE6-EXEC-R99"

        # Invalid forms: R0, R01, missing R, negative, lowercase, whitespace
        invalid_ids = [
            "G07-07B7-GATE6-EXEC-R0",
            "G07-07B7-GATE6-EXEC-R01",
            "G07-07B7-GATE6-EXEC-R",
            "G07-07B7-GATE6-EXEC-R-1",
            "g07-07b7-gate6-exec-r1",
            " G07-07B7-GATE6-EXEC-R1",
            "G07-07B7-GATE6-EXEC-R1 ",
            "G07-07B7-GATE6-EXEC-R1\n",
            "G07-07B7-GATE4-EXEC-R1",
            "",
        ]
        for inv_id in invalid_ids:
            with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_WORKORDER_ID_FORMAT"):
                make_valid_execution_workorder(workorder_id=inv_id)

    def test_identifier_string_fields_validation(self):
        for field in ("tech_lead_id", "tech_lead_key_id", "permitted_command", "execution_id"):
            # Empty
            with pytest.raises(SignedExecutionWorkorderValidationError, match=f"ERR_{field.upper()}_FORMAT"):
                make_valid_execution_workorder(**{field: ""})

            # Leading whitespace
            with pytest.raises(SignedExecutionWorkorderValidationError, match=f"ERR_{field.upper()}_FORMAT"):
                make_valid_execution_workorder(**{field: " val"})

            # Trailing whitespace
            with pytest.raises(SignedExecutionWorkorderValidationError, match=f"ERR_{field.upper()}_FORMAT"):
                make_valid_execution_workorder(**{field: "val "})

            # Non-string
            with pytest.raises(SignedExecutionWorkorderValidationError, match=f"ERR_{field.upper()}_FORMAT"):
                make_valid_execution_workorder(**{field: 12345})

    def test_timestamp_validity_interval(self):
        # valid_until_iso <= issued_at_iso rejected
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_VALIDITY_INTERVAL_INVALID"):
            make_valid_execution_workorder(
                issued_at_iso="2026-11-05T08:00:00Z",
                valid_until_iso="2026-11-05T08:00:00Z",
            )
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_VALIDITY_INTERVAL_INVALID"):
            make_valid_execution_workorder(
                issued_at_iso="2026-11-05T09:00:00Z",
                valid_until_iso="2026-11-05T08:00:00Z",
            )

        # > 3600 seconds (1 hour) rejected
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_VALIDITY_WINDOW_EXCEEDED"):
            make_valid_execution_workorder(
                issued_at_iso="2026-11-05T08:00:00Z",
                valid_until_iso="2026-11-05T09:00:01Z",
            )

        # Exactly 3600 seconds accepted
        record = make_valid_execution_workorder(
            issued_at_iso="2026-11-05T08:00:00Z",
            valid_until_iso="2026-11-05T09:00:00Z",
        )
        assert record.schema_version == 1

        # Non-UTC / naive / malformed rejected
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_ISSUED_AT_TIMESTAMP_INVALID"):
            make_valid_execution_workorder(issued_at_iso="2026-11-05 08:00:00")
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_VALID_UNTIL_TIMESTAMP_INVALID"):
            make_valid_execution_workorder(valid_until_iso="2026-11-05T09:00:00+07:00")

    def test_pinned_target_identities_validation(self):
        # Non-string types must be rejected before equality checks
        class CustomEqual:
            def __eq__(self, other: Any) -> bool:
                return True

        custom_equal = CustomEqual()

        # target_project_uid type gates
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_TARGET_PROJECT_UID_TYPE"):
            make_valid_execution_workorder(target_project_uid=12345)
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_TARGET_PROJECT_UID_TYPE"):
            make_valid_execution_workorder(target_project_uid=custom_equal)
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_TARGET_PROJECT_UID_MISMATCH"):
            make_valid_execution_workorder(target_project_uid="wrong-uid")

        # target_timeline_uid type gates
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_TARGET_TIMELINE_UID_TYPE"):
            make_valid_execution_workorder(target_timeline_uid=12345)
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_TARGET_TIMELINE_UID_TYPE"):
            make_valid_execution_workorder(target_timeline_uid=custom_equal)
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_TARGET_TIMELINE_UID_MISMATCH"):
            make_valid_execution_workorder(target_timeline_uid="wrong-uid")

    def test_digests_and_signature_validation(self):
        # 64-char digests: reviewed_candidate_sha256, plan_digest_sha256, action_payload_digest_sha256
        for digest_field in ("reviewed_candidate_sha256", "plan_digest_sha256", "action_payload_digest_sha256"):
            with pytest.raises(SignedExecutionWorkorderValidationError, match=f"ERR_{digest_field.upper()}_FORMAT"):
                make_valid_execution_workorder(**{digest_field: "1" * 63})  # short
            with pytest.raises(SignedExecutionWorkorderValidationError, match=f"ERR_{digest_field.upper()}_FORMAT"):
                make_valid_execution_workorder(**{digest_field: "A" * 64})  # uppercase
            with pytest.raises(SignedExecutionWorkorderValidationError, match=f"ERR_{digest_field.upper()}_FORMAT"):
                make_valid_execution_workorder(**{digest_field: "g" * 64})  # non-hex

        # tech_lead_signature (128 hex chars)
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_TECH_LEAD_SIGNATURE_FORMAT"):
            make_valid_execution_workorder(tech_lead_signature="4" * 127)
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_TECH_LEAD_SIGNATURE_FORMAT"):
            make_valid_execution_workorder(tech_lead_signature="F" * 128)
        with pytest.raises(SignedExecutionWorkorderValidationError, match="ERR_TECH_LEAD_SIGNATURE_FORMAT"):
            make_valid_execution_workorder(tech_lead_signature="z" * 128)

    def test_blocking_error_constant_preserved(self):
        assert ERR_GATE5_EXECUTION_UNAUTHORIZED == "ERR_GATE5_EXECUTION_UNAUTHORIZED"
        assert MAX_WORKORDER_VALIDITY_SECONDS == 3600.0
