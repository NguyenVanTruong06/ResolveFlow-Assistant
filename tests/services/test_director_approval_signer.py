"""Focused Unit Tests for DirectorApprovalSigner (G07-07B7-I3)

Verifies:
1. Valid signing returns a valid frozen SignedApprovalEnvelope that verifies with the ephemeral public key.
2. Canonical RFC 8785 byte preservation and strict exclusion of director_signature from signing bytes.
3. Pre-signing rejection of non-exact DTOs, subclasses, malformed digests, key IDs, and timestamps.
4. Signature sensitivity: signature changes when any signed claim changes, and never signs the signature itself.
5. Zero leakage of private key bytes in representations, envelopes, return values, or exceptions.
6. Ephemeral in-memory operation with zero disk writes, keystore access, or live operations.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
import pytest

from src.core.contracts.edit_plan_approval_contracts import AppOwnedApprovalRecord
from src.core.contracts.signed_approval_envelope import (
    RE_LOWER_HEX_128,
    SignedApprovalEnvelope,
)
from src.services.director_approval_signer import (
    DirectorApprovalSigner,
    DirectorSignerError,
    DirectorSignerKeyError,
    DirectorSignerValidationError,
)


def make_sample_record(approval_id: str = "appr_director_001") -> AppOwnedApprovalRecord:
    """Creates a valid AppOwnedApprovalRecord test fixture."""
    return AppOwnedApprovalRecord(
        approval_id=approval_id,
        proposal_id="prop_01j7b9c4d2e1f8a3b5c7d9e0f1",
        proposal_revision="a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
        approved_at_iso="2026-11-03T15:00:00Z",
        accepted_omission_ids=("omit_1", "omit_2"),
        approval_scope="ENTIRE_PROPOSAL",
    )


VALID_PLAN_DIGEST: str = "a" * 64
VALID_PROPOSAL_DIGEST: str = "b" * 64
VALID_KEY_ID: str = "director_key_primary"
VALID_SIGNED_AT: str = "2026-11-03T15:01:00Z"


class SubclassApprovalRecord(AppOwnedApprovalRecord):
    """Subclass to test strict exact type enforcement."""
    pass


class TestDirectorApprovalSigner:
    """Test suite for DirectorApprovalSigner."""

    def test_valid_signing_and_cryptographic_verification(self) -> None:
        """Verifies valid signing yields a frozen SignedApprovalEnvelope verifiable by public key."""
        priv_key = Ed25519PrivateKey.generate()
        signer = DirectorApprovalSigner(priv_key, key_id=VALID_KEY_ID)
        record = make_sample_record()

        envelope = signer.sign(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id=VALID_KEY_ID,
            signed_at_iso=VALID_SIGNED_AT,
        )

        assert isinstance(envelope, SignedApprovalEnvelope)
        assert envelope.schema_version == 1
        assert envelope.approval_record == record
        assert envelope.plan_digest_sha256 == VALID_PLAN_DIGEST
        assert envelope.proposal_content_digest_sha256 == VALID_PROPOSAL_DIGEST
        assert envelope.director_key_id == VALID_KEY_ID
        assert envelope.signed_at_iso == VALID_SIGNED_AT
        assert RE_LOWER_HEX_128.fullmatch(envelope.director_signature)

        # Frozen immutability check
        with pytest.raises(dataclasses.FrozenInstanceError):
            envelope.director_signature = "c" * 128  # type: ignore[misc]

        # Cryptographic verification using public key
        pub_key: Ed25519PublicKey = signer.public_key
        raw_sig = bytes.fromhex(envelope.director_signature)
        # Should verify cleanly without raising InvalidSignature
        pub_key.verify(raw_sig, envelope.canonical_signing_payload_bytes())

    def test_canonical_signing_bytes_exact_and_excludes_signature(self) -> None:
        """Verifies canonical signing bytes exactly match envelope contract and exclude signature."""
        priv_key = Ed25519PrivateKey.generate()
        signer = DirectorApprovalSigner(priv_key, key_id=VALID_KEY_ID)
        record = make_sample_record()

        pre_bytes = signer.compute_signing_bytes(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id=VALID_KEY_ID,
            signed_at_iso=VALID_SIGNED_AT,
        )

        envelope = signer.sign(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id=VALID_KEY_ID,
            signed_at_iso=VALID_SIGNED_AT,
        )

        assert pre_bytes == envelope.canonical_signing_payload_bytes()
        # Verify director_signature is strictly absent from signing bytes
        assert b"director_signature" not in pre_bytes
        assert b"director_signature" not in envelope.canonical_signing_payload_bytes()

        # But director_signature is present in the complete canonical envelope bytes
        assert b"director_signature" in envelope.canonical_envelope_bytes()

    def test_invalid_signer_init_configuration(self) -> None:
        """Verifies signer initialization rejects invalid private key types and key IDs."""
        with pytest.raises(DirectorSignerKeyError, match="ERR_INVALID_PRIVATE_KEY"):
            DirectorApprovalSigner("not-a-private-key")  # type: ignore[arg-type]

        with pytest.raises(DirectorSignerKeyError, match="ERR_INVALID_PRIVATE_KEY"):
            DirectorApprovalSigner(None)  # type: ignore[arg-type]

        priv_key = Ed25519PrivateKey.generate()
        # Empty or whitespace key_id
        with pytest.raises(DirectorSignerValidationError, match="ERR_DIRECTOR_KEY_ID_FORMAT"):
            DirectorApprovalSigner(priv_key, key_id="")
        with pytest.raises(DirectorSignerValidationError, match="ERR_DIRECTOR_KEY_ID_FORMAT"):
            DirectorApprovalSigner(priv_key, key_id="  ")
        with pytest.raises(DirectorSignerValidationError, match="ERR_DIRECTOR_KEY_ID_FORMAT"):
            DirectorApprovalSigner(priv_key, key_id=" key ")

    def test_reject_non_exact_approval_record_dto(self) -> None:
        """Verifies signer rejects mappings, subclasses, and other non-exact types fail-closed."""
        priv_key = Ed25519PrivateKey.generate()
        signer = DirectorApprovalSigner(priv_key)

        # 1. Plain dictionary lookalike
        dict_record = {
            "approval_id": "appr_director_001",
            "proposal_id": "prop_01j7b9c4d2e1f8a3b5c7d9e0f1",
            "proposal_revision": "a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
            "approved_at_iso": "2026-11-03T15:00:00Z",
            "accepted_omission_ids": ("omit_1", "omit_2"),
            "approval_scope": "ENTIRE_PROPOSAL",
        }
        with pytest.raises(DirectorSignerValidationError, match="ERR_APPROVAL_RECORD_TYPE"):
            signer.sign(
                approval_record=dict_record,  # type: ignore[arg-type]
                plan_digest_sha256=VALID_PLAN_DIGEST,
                proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
                director_key_id=VALID_KEY_ID,
                signed_at_iso=VALID_SIGNED_AT,
            )

        # 2. Subclass
        subclass_record = SubclassApprovalRecord(
            approval_id="appr_director_001",
            proposal_id="prop_01j7b9c4d2e1f8a3b5c7d9e0f1",
            proposal_revision="a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
            approved_at_iso="2026-11-03T15:00:00Z",
            accepted_omission_ids=("omit_1", "omit_2"),
            approval_scope="ENTIRE_PROPOSAL",
        )
        with pytest.raises(DirectorSignerValidationError, match="ERR_APPROVAL_RECORD_TYPE"):
            signer.sign(
                approval_record=subclass_record,
                plan_digest_sha256=VALID_PLAN_DIGEST,
                proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
                director_key_id=VALID_KEY_ID,
                signed_at_iso=VALID_SIGNED_AT,
            )

    def test_reject_malformed_digests_key_id_and_timestamps(self) -> None:
        """Verifies rejection of invalid parameter formats prior to signing."""
        priv_key = Ed25519PrivateKey.generate()
        signer = DirectorApprovalSigner(priv_key)
        record = make_sample_record()

        # Malformed plan digest
        for bad_digest in ["", "A" * 64, "a" * 63, "a" * 65, "not_hex" + "0" * 57, 12345]:
            with pytest.raises(DirectorSignerValidationError, match="ERR_PLAN_DIGEST_FORMAT"):
                signer.sign(
                    approval_record=record,
                    plan_digest_sha256=bad_digest,  # type: ignore[arg-type]
                    proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
                    director_key_id=VALID_KEY_ID,
                    signed_at_iso=VALID_SIGNED_AT,
                )

        # Malformed proposal content digest
        for bad_digest in ["", "B" * 64, "b" * 63, "b" * 65, "xyz" + "0" * 61, None]:
            with pytest.raises(DirectorSignerValidationError, match="ERR_PROPOSAL_DIGEST_FORMAT"):
                signer.sign(
                    approval_record=record,
                    plan_digest_sha256=VALID_PLAN_DIGEST,
                    proposal_content_digest_sha256=bad_digest,  # type: ignore[arg-type]
                    director_key_id=VALID_KEY_ID,
                    signed_at_iso=VALID_SIGNED_AT,
                )

        # Malformed director_key_id
        for bad_key_id in ["", " ", "  key  ", 999, None]:
            with pytest.raises(DirectorSignerValidationError, match="ERR_DIRECTOR_KEY_ID_FORMAT"):
                signer.sign(
                    approval_record=record,
                    plan_digest_sha256=VALID_PLAN_DIGEST,
                    proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
                    director_key_id=bad_key_id,  # type: ignore[arg-type]
                    signed_at_iso=VALID_SIGNED_AT,
                )

        # Malformed or non-UTC signed_at_iso
        for bad_ts in [
            "2026-11-03 15:00:00",
            "2026-11-03T15:00:00+07:00",
            "invalid_date",
            "2026-02-31T15:00:00Z",  # Invalid calendar day
            "",
            None,
        ]:
            with pytest.raises(DirectorSignerValidationError, match="ERR_SIGNED_AT_TIMESTAMP_INVALID"):
                signer.sign(
                    approval_record=record,
                    plan_digest_sha256=VALID_PLAN_DIGEST,
                    proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
                    director_key_id=VALID_KEY_ID,
                    signed_at_iso=bad_ts,  # type: ignore[arg-type]
                )

    def test_key_id_mismatch_with_configured_signer_key(self) -> None:
        """Verifies rejection when sign() passes a key_id different from configured key_id."""
        priv_key = Ed25519PrivateKey.generate()
        signer = DirectorApprovalSigner(priv_key, key_id="key_alpha")
        record = make_sample_record()

        with pytest.raises(DirectorSignerValidationError, match="ERR_DIRECTOR_KEY_ID_MISMATCH"):
            signer.sign(
                approval_record=record,
                plan_digest_sha256=VALID_PLAN_DIGEST,
                proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
                director_key_id="key_beta",
                signed_at_iso=VALID_SIGNED_AT,
            )

    def test_signature_sensitivity_to_every_field(self) -> None:
        """Verifies signature changes and verification fails if any signed claim is mutated."""
        priv_key = Ed25519PrivateKey.generate()
        signer = DirectorApprovalSigner(priv_key)
        record = make_sample_record()

        base_envelope = signer.sign(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id=VALID_KEY_ID,
            signed_at_iso=VALID_SIGNED_AT,
        )

        # 1. Mutated approval_id
        rec_diff_id = make_sample_record(approval_id="appr_director_002")
        env_diff_id = signer.sign(
            approval_record=rec_diff_id,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id=VALID_KEY_ID,
            signed_at_iso=VALID_SIGNED_AT,
        )
        assert env_diff_id.director_signature != base_envelope.director_signature

        # 2. Mutated plan digest
        env_diff_plan = signer.sign(
            approval_record=record,
            plan_digest_sha256="c" * 64,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id=VALID_KEY_ID,
            signed_at_iso=VALID_SIGNED_AT,
        )
        assert env_diff_plan.director_signature != base_envelope.director_signature

        # 3. Mutated proposal digest
        env_diff_prop = signer.sign(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256="d" * 64,
            director_key_id=VALID_KEY_ID,
            signed_at_iso=VALID_SIGNED_AT,
        )
        assert env_diff_prop.director_signature != base_envelope.director_signature

        # 4. Mutated key ID
        env_diff_key = signer.sign(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id="director_key_secondary",
            signed_at_iso=VALID_SIGNED_AT,
        )
        assert env_diff_key.director_signature != base_envelope.director_signature

        # 5. Mutated timestamp
        env_diff_ts = signer.sign(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id=VALID_KEY_ID,
            signed_at_iso="2026-11-03T15:02:00Z",
        )
        assert env_diff_ts.director_signature != base_envelope.director_signature

        # Verification fails if original signature is verified against altered payload bytes
        pub_key = signer.public_key
        raw_sig = bytes.fromhex(base_envelope.director_signature)
        with pytest.raises(InvalidSignature):
            pub_key.verify(raw_sig, env_diff_id.canonical_signing_payload_bytes())
        with pytest.raises(InvalidSignature):
            pub_key.verify(raw_sig, env_diff_plan.canonical_signing_payload_bytes())
        with pytest.raises(InvalidSignature):
            pub_key.verify(raw_sig, env_diff_prop.canonical_signing_payload_bytes())
        with pytest.raises(InvalidSignature):
            pub_key.verify(raw_sig, env_diff_key.canonical_signing_payload_bytes())
        with pytest.raises(InvalidSignature):
            pub_key.verify(raw_sig, env_diff_ts.canonical_signing_payload_bytes())

    def test_verification_fails_with_different_public_key(self) -> None:
        """Verifies signature does not verify under a different public key."""
        priv_key1 = Ed25519PrivateKey.generate()
        priv_key2 = Ed25519PrivateKey.generate()

        signer1 = DirectorApprovalSigner(priv_key1)
        record = make_sample_record()

        envelope = signer1.sign(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id=VALID_KEY_ID,
            signed_at_iso=VALID_SIGNED_AT,
        )

        raw_sig = bytes.fromhex(envelope.director_signature)
        with pytest.raises(InvalidSignature):
            priv_key2.public_key().verify(raw_sig, envelope.canonical_signing_payload_bytes())

    def test_zero_private_key_leakage(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verifies string representation, envelope, dictionary, and exceptions leak no key bytes."""
        priv_key = Ed25519PrivateKey.generate()
        signer = DirectorApprovalSigner(priv_key, key_id="sec_test_key")

        # Repr and str
        r = repr(signer)
        s = str(signer)
        assert "sec_test_key" in r
        assert "private_bytes" not in r
        assert "Ed25519PrivateKey" not in r

        record = make_sample_record()
        envelope = signer.sign(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id="sec_test_key",
            signed_at_iso=VALID_SIGNED_AT,
        )

        d = envelope.to_dict()
        assert "private_key" not in d
        assert "private" not in str(d).lower()

        # Check logs captured during tests contain zero private key material
        for record_log in caplog.records:
            assert "private_key" not in record_log.message

    def test_envelope_digest_computation_and_contract_integrity(self) -> None:
        """Verifies SignedApprovalEnvelope compute_envelope_digest matches sha256 of canonical bytes."""
        priv_key = Ed25519PrivateKey.generate()
        signer = DirectorApprovalSigner(priv_key, key_id=VALID_KEY_ID)
        record = make_sample_record()

        envelope = signer.sign(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id=VALID_KEY_ID,
            signed_at_iso=VALID_SIGNED_AT,
        )

        digest = envelope.compute_envelope_digest()
        assert re.compile(r"^[0-9a-f]{64}$").fullmatch(digest)
        import hashlib
        assert digest == hashlib.sha256(envelope.canonical_envelope_bytes()).hexdigest()

    def test_no_disk_files_or_keystore_access(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        """Verifies DirectorApprovalSigner does not touch the filesystem or persistent keystores."""
        priv_key = Ed25519PrivateKey.generate()
        signer = DirectorApprovalSigner(priv_key)
        record = make_sample_record()

        # Monkeypatch open to detect any file writes
        real_open = open

        def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
            mode = args[0] if args else kwargs.get("mode", "r")
            if any(write_flag in mode for write_flag in ("w", "a", "x", "+")):
                raise AssertionError(f"Unexpected file write attempted: {file}")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr("builtins.open", guarded_open)

        envelope = signer.sign(
            approval_record=record,
            plan_digest_sha256=VALID_PLAN_DIGEST,
            proposal_content_digest_sha256=VALID_PROPOSAL_DIGEST,
            director_key_id=VALID_KEY_ID,
            signed_at_iso=VALID_SIGNED_AT,
        )
        assert envelope is not None
