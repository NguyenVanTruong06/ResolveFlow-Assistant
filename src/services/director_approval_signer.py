"""Director Approval Signer Service (G07-07B7-I3)

Implements the Director-side approval signing component defined in
docs/architecture/07B7-independent-authorization-signing-workflow-plan.md §4.2 and §10.

Signs an AppOwnedApprovalRecord and its execution plan / proposal content digests
using an Ed25519 private key, producing a frozen SignedApprovalEnvelope containing
the canonical RFC 8785 digital signature.

Safety Boundary:
- Pure Director-side in-memory signing component.
- Accepts strictly exact AppOwnedApprovalRecord DTO instances.
- Reuses the canonical RFC 8785 JSON serializer and contracts without modifications.
- Never logs, returns, or leaks private key bytes or structures.
- No file I/O, no network, no socket/RPC, no Resolve IPC, no key generation at import time.
- Does not authorize execution; verification belongs to the downstream Signer daemon.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    approval_record_to_dict,
)
from src.core.contracts.signed_approval_envelope import (
    RE_LOWER_HEX_64,
    SignedApprovalEnvelope,
)
from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
)


class DirectorSignerError(AcceptanceGateError, ValueError):
    """Base exception for all Director approval signer errors."""
    pass


class DirectorSignerValidationError(DirectorSignerError):
    """Raised when signing inputs fail type, format, bounds, or semantic validation."""
    pass


class DirectorSignerKeyError(DirectorSignerError):
    """Raised when private key material or key configuration is invalid."""
    pass


class DirectorApprovalSigner:
    """Director-side component that signs approval records with Ed25519.

    Produces frozen SignedApprovalEnvelope instances binding the approval record,
    plan digest, proposal digest, and signing timestamp under canonical RFC 8785 encoding.
    """

    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        *,
        key_id: Optional[str] = None,
    ) -> None:
        """Initializes the signer with an in-memory Ed25519PrivateKey.

        Args:
            private_key: An instance of cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PrivateKey.
            key_id: Optional identifier for this key; if provided, must match director_key_id during sign().
        """
        if not isinstance(private_key, Ed25519PrivateKey):
            raise DirectorSignerKeyError(
                f"ERR_INVALID_PRIVATE_KEY: private_key must be an instance of Ed25519PrivateKey, got {type(private_key).__name__}"
            )

        if key_id is not None:
            if type(key_id) is not str or not key_id or key_id != key_id.strip():
                raise DirectorSignerValidationError(
                    f"ERR_DIRECTOR_KEY_ID_FORMAT: key_id must be a non-empty string without whitespace padding, got {key_id!r}"
                )

        self._private_key: Ed25519PrivateKey = private_key
        self._key_id: Optional[str] = key_id

    @property
    def key_id(self) -> Optional[str]:
        """Returns the configured key ID, or None if unbound."""
        return self._key_id

    @property
    def public_key(self) -> Ed25519PublicKey:
        """Returns the associated Ed25519PublicKey for verification purposes."""
        return self._private_key.public_key()

    def build_signing_payload_dict(
        self,
        *,
        approval_record: AppOwnedApprovalRecord,
        plan_digest_sha256: str,
        proposal_content_digest_sha256: str,
        director_key_id: str,
        signed_at_iso: str,
    ) -> Dict[str, Any]:
        """Validates all inputs and constructs the signing payload dictionary.

        Excludes director_signature strictly in compliance with SignedApprovalEnvelope.
        """
        # 1. approval_record: strictly AppOwnedApprovalRecord (no mappings, subclasses, or lookalikes)
        if type(approval_record) is not AppOwnedApprovalRecord:
            raise DirectorSignerValidationError(
                f"ERR_APPROVAL_RECORD_TYPE: approval_record must be exact AppOwnedApprovalRecord, got {type(approval_record).__name__}"
            )

        # 2. plan_digest_sha256: strictly 64 lowercase hex
        if type(plan_digest_sha256) is not str or not RE_LOWER_HEX_64.fullmatch(plan_digest_sha256):
            raise DirectorSignerValidationError(
                f"ERR_PLAN_DIGEST_FORMAT: plan_digest_sha256 must be a 64-character lowercase hex string, got {plan_digest_sha256!r}"
            )

        # 3. proposal_content_digest_sha256: strictly 64 lowercase hex
        if type(proposal_content_digest_sha256) is not str or not RE_LOWER_HEX_64.fullmatch(proposal_content_digest_sha256):
            raise DirectorSignerValidationError(
                f"ERR_PROPOSAL_DIGEST_FORMAT: proposal_content_digest_sha256 must be a 64-character lowercase hex string, got {proposal_content_digest_sha256!r}"
            )

        # 4. director_key_id: non-empty string without whitespace
        if type(director_key_id) is not str or not director_key_id or director_key_id != director_key_id.strip():
            raise DirectorSignerValidationError(
                f"ERR_DIRECTOR_KEY_ID_FORMAT: director_key_id must be a non-empty string without whitespace padding, got {director_key_id!r}"
            )

        # Enforce consistency with configured key_id if bound
        if self._key_id is not None and director_key_id != self._key_id:
            raise DirectorSignerValidationError(
                f"ERR_DIRECTOR_KEY_ID_MISMATCH: director_key_id '{director_key_id}' does not match configured signer key_id '{self._key_id}'"
            )

        # 5. signed_at_iso: strict UTC ISO-8601 validation
        try:
            parse_iso_utc_timestamp(signed_at_iso, "signed_at_iso")
        except Exception as exc:
            raise DirectorSignerValidationError(
                f"ERR_SIGNED_AT_TIMESTAMP_INVALID: signed_at_iso '{signed_at_iso}' is not a valid strict UTC ISO-8601 timestamp: {exc}"
            ) from exc

        return {
            "approval_record": approval_record_to_dict(approval_record),
            "director_key_id": director_key_id,
            "plan_digest_sha256": plan_digest_sha256,
            "proposal_content_digest_sha256": proposal_content_digest_sha256,
            "schema_version": 1,
            "signed_at_iso": signed_at_iso,
        }

    def compute_signing_bytes(
        self,
        *,
        approval_record: AppOwnedApprovalRecord,
        plan_digest_sha256: str,
        proposal_content_digest_sha256: str,
        director_key_id: str,
        signed_at_iso: str,
    ) -> bytes:
        """Computes the exact canonical RFC 8785 bytes to be signed."""
        payload_dict = self.build_signing_payload_dict(
            approval_record=approval_record,
            plan_digest_sha256=plan_digest_sha256,
            proposal_content_digest_sha256=proposal_content_digest_sha256,
            director_key_id=director_key_id,
            signed_at_iso=signed_at_iso,
        )
        return canonical_json_bytes(payload_dict)

    def sign(
        self,
        *,
        approval_record: AppOwnedApprovalRecord,
        plan_digest_sha256: str,
        proposal_content_digest_sha256: str,
        director_key_id: str,
        signed_at_iso: str,
    ) -> SignedApprovalEnvelope:
        """Signs an AppOwnedApprovalRecord and returns a frozen SignedApprovalEnvelope.

        Signs the canonical RFC 8785 payload bytes with Ed25519, yielding a 128-character
        lowercase hex signature.
        """
        signing_bytes = self.compute_signing_bytes(
            approval_record=approval_record,
            plan_digest_sha256=plan_digest_sha256,
            proposal_content_digest_sha256=proposal_content_digest_sha256,
            director_key_id=director_key_id,
            signed_at_iso=signed_at_iso,
        )

        raw_signature: bytes = self._private_key.sign(signing_bytes)
        signature_hex: str = raw_signature.hex().lower()

        envelope = SignedApprovalEnvelope(
            schema_version=1,
            approval_record=approval_record,
            plan_digest_sha256=plan_digest_sha256,
            proposal_content_digest_sha256=proposal_content_digest_sha256,
            director_key_id=director_key_id,
            director_signature=signature_hex,
            signed_at_iso=signed_at_iso,
        )

        # Invariant check: ensure envelope's canonical signing bytes match signed bytes exactly
        if envelope.canonical_signing_payload_bytes() != signing_bytes:
            raise DirectorSignerError(
                "ERR_CANONICAL_SIGNING_BYTE_MISMATCH: signed bytes do not equal envelope canonical signing payload bytes"
            )

        return envelope

    def __repr__(self) -> str:
        """Safe representation hiding private key bytes completely."""
        key_id_repr = f"'{self._key_id}'" if self._key_id is not None else "None"
        return f"<DirectorApprovalSigner key_id={key_id_repr}>"

    def __str__(self) -> str:
        return self.__repr__()
