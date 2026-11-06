"""ResolveFlow Operator Console (G07-AUTH-SIGNER-GATE3-I18).

Implements the bounded local-test human operator review and consent-signing console
defined in docs/architecture/07B7-independent-authorization-signing-workflow-plan.md §§3.1, 5.2.

Documentation of Safety Boundaries:
- Local-test and human verification interface only: operates strictly with ephemeral test keys.
- Does NOT load, persist, decrypt, or log production/private keys.
- Does NOT call DaVinci Resolve, execute live actions, or perform live dispatch.
- Injectable I/O functions ensure 100% deterministic, testable behavior without interactive terminals.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Callable, Dict, Optional, Protocol, Union

from src.core.contracts.signed_operator_consent_envelope import (
    CONFIRMATION_PHRASE_LITERAL,
    FRESHNESS_ATTESTATION_LITERAL,
    RE_CANONICAL_UUID_V4,
    RE_CHALLENGE_ID,
    RE_LOWER_HEX_64,
    SignedOperatorConsentEnvelope,
    SignedOperatorConsentEnvelopeValidationError,
)
from src.core.contracts.trial_authorization_contracts import (
    canonical_json_bytes,
    parse_iso_utc_timestamp,
)


class OperatorConsoleError(Exception):
    """Base exception for operator console errors."""

    def __init__(self, message: str, code: str = "ERR_OPERATOR_CONSOLE") -> None:
        super().__init__(message)
        self.code = code


class OperatorConsoleValidationError(OperatorConsoleError):
    """Raised when context validation or input attestation fails."""

    def __init__(self, message: str, code: str = "ERR_OPERATOR_CONSOLE_VALIDATION") -> None:
        super().__init__(message, code=code)


class OperatorConsoleAbortError(OperatorConsoleError):
    """Raised when operator explicitly aborts or supplies invalid confirmation phrase."""

    def __init__(self, message: str, code: str = "ERR_OPERATOR_CONSOLE_ABORT") -> None:
        super().__init__(message, code=code)


class OperatorConsentSigner(Protocol):
    """Protocol for injected narrow operator-consent signing capability."""

    def sign_operator_consent(self, payload_bytes: bytes) -> str:
        """Signs canonical RFC 8785 operator consent signing payload bytes, returning 128-char hex."""
        ...


@dataclass(frozen=True)
class OperatorReviewContext:
    """Authoritative review context encapsulating bound challenge, approval, and plan claims.

    Documentation of Trust Model:
    This context is constructed solely from authoritative verified artifacts (challenge, approval, plan).
    The console builds SignedOperatorConsentEnvelope solely from these claims; caller claim overrides
    are strictly prohibited.
    """

    challenge_id: str
    client_nonce: str
    expires_at_iso: str
    target_project_name: str
    target_project_uid: str
    target_timeline_name: str
    target_timeline_uid: str
    action_type: str
    track_type: str
    track_index: int
    record_frame: int
    duration_frames: int
    source_file_path: str
    source_file_sha256: str
    approval_envelope_digest_sha256: str
    plan_digest_sha256: str
    action_payload_digest_sha256: str
    operator_id: str
    operator_key_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.challenge_id, str) or not RE_CHALLENGE_ID.match(self.challenge_id):
            raise OperatorConsoleValidationError(
                f"ERR_INVALID_CHALLENGE_ID: challenge_id must match {RE_CHALLENGE_ID.pattern}",
                code="ERR_INVALID_CHALLENGE_ID",
            )
        if not isinstance(self.client_nonce, str) or not RE_CANONICAL_UUID_V4.match(self.client_nonce):
            raise OperatorConsoleValidationError(
                f"ERR_INVALID_CLIENT_NONCE: client_nonce must be canonical lowercase UUIDv4",
                code="ERR_INVALID_CLIENT_NONCE",
            )
        if not isinstance(self.expires_at_iso, str):
            raise OperatorConsoleValidationError(
                "ERR_INVALID_EXPIRES_AT: expires_at_iso must be string",
                code="ERR_INVALID_EXPIRES_AT",
            )
        parse_iso_utc_timestamp(self.expires_at_iso, "expires_at_iso")

        for name, val in [
            ("target_project_name", self.target_project_name),
            ("target_project_uid", self.target_project_uid),
            ("target_timeline_name", self.target_timeline_name),
            ("target_timeline_uid", self.target_timeline_uid),
            ("action_type", self.action_type),
            ("track_type", self.track_type),
            ("source_file_path", self.source_file_path),
            ("operator_id", self.operator_id),
            ("operator_key_id", self.operator_key_id),
        ]:
            if not isinstance(val, str) or not val.strip():
                raise OperatorConsoleValidationError(
                    f"ERR_FIELD_EMPTY: {name} must be non-empty string",
                    code="ERR_FIELD_EMPTY",
                )

        for name, val in [
            ("approval_envelope_digest_sha256", self.approval_envelope_digest_sha256),
            ("plan_digest_sha256", self.plan_digest_sha256),
            ("action_payload_digest_sha256", self.action_payload_digest_sha256),
            ("source_file_sha256", self.source_file_sha256),
        ]:
            if not isinstance(val, str) or not RE_LOWER_HEX_64.match(val):
                raise OperatorConsoleValidationError(
                    f"ERR_INVALID_DIGEST: {name} must be 64-char lowercase hex",
                    code="ERR_INVALID_DIGEST",
                )

        if type(self.track_index) is not int or self.track_index < 1:
            raise OperatorConsoleValidationError(
                "ERR_INVALID_TRACK_INDEX: track_index must be int >= 1",
                code="ERR_INVALID_TRACK_INDEX",
            )
        if type(self.record_frame) is not int or self.record_frame < 0:
            raise OperatorConsoleValidationError(
                "ERR_INVALID_RECORD_FRAME: record_frame must be int >= 0",
                code="ERR_INVALID_RECORD_FRAME",
            )
        if type(self.duration_frames) is not int or self.duration_frames < 1:
            raise OperatorConsoleValidationError(
                "ERR_INVALID_DURATION_FRAMES: duration_frames must be int >= 1",
                code="ERR_INVALID_DURATION_FRAMES",
            )


def format_challenge_review_display(context: OperatorReviewContext) -> str:
    """Renders the complete, bound review context for human operator visual inspection."""
    lines = [
        "==================================================================",
        "          RESOLVEFLOW CANARY TRIAL - OPERATOR REVIEW CONSOLE      ",
        "==================================================================",
        f"Challenge ID:          {context.challenge_id}",
        f"Client Nonce:          {context.client_nonce}",
        f"Challenge Expiry UTC:  {context.expires_at_iso}",
        "------------------------------------------------------------------",
        "Target DaVinci Resolve Project & Timeline:",
        f"  Project Name:        {context.target_project_name}",
        f"  Project UID:         {context.target_project_uid}",
        f"  Timeline Name:       {context.target_timeline_name}",
        f"  Timeline UID:        {context.target_timeline_uid}",
        "------------------------------------------------------------------",
        "Planned Canary Mutation Action:",
        f"  Action Type:         {context.action_type}",
        f"  Track Type / Index:  {context.track_type} (Index: {context.track_index})",
        f"  Timeline Frames:     Start {context.record_frame} -> Duration {context.duration_frames}",
        f"  Source Media Path:   {context.source_file_path}",
        f"  Source SHA-256:      {context.source_file_sha256}",
        "------------------------------------------------------------------",
        "Authoritative Cryptographic Digest Bindings:",
        f"  Approval Digest:     {context.approval_envelope_digest_sha256}",
        f"  Plan Digest:         {context.plan_digest_sha256}",
        f"  Action Payload:      {context.action_payload_digest_sha256}",
        "------------------------------------------------------------------",
        f"Operator Identity:     {context.operator_id} (Key ID: {context.operator_key_id})",
        "==================================================================",
    ]
    return "\n".join(lines)


def review_and_sign_operator_consent(
    context: OperatorReviewContext,
    signer: Union[OperatorConsentSigner, Callable[[bytes], str]],
    *,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    clock: Optional[Callable[[], datetime]] = None,
) -> SignedOperatorConsentEnvelope:
    """Interactively reviews bound canary challenge, checks operator attestations, and produces signed consent envelope.

    Args:
        context: Authoritative OperatorReviewContext containing bound claims.
        signer: Injected narrow signer capability accepting canonical payload bytes and returning 128-char hex signature.
        input_func: Injectable input reader for deterministic test automation.
        output_func: Injectable output writer for terminal display verification.
        clock: Optional injectable UTC clock returning datetime.

    Returns:
        Exact, verified SignedOperatorConsentEnvelope binding the authoritative claims.

    Raises:
        OperatorConsoleValidationError: If challenge is expired or context invalid.
        OperatorConsoleAbortError: If attestation or confirmation phrase does not match.
    """
    if type(context) is not OperatorReviewContext:
        raise OperatorConsoleValidationError(
            f"ERR_INVALID_CONTEXT_TYPE: expected OperatorReviewContext, got {type(context).__name__}",
            code="ERR_INVALID_CONTEXT_TYPE",
        )

    # 1. Freshness / expiry check
    now_dt = clock() if clock is not None else datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    expires_at = parse_iso_utc_timestamp(context.expires_at_iso, "expires_at_iso")
    if now_dt >= expires_at:
        raise OperatorConsoleValidationError(
            f"ERR_CHALLENGE_EXPIRED: challenge expired at {context.expires_at_iso}, current time is {now_dt.isoformat()}",
            code="ERR_CHALLENGE_EXPIRED",
        )

    # 2. Render review display to operator
    output_func(format_challenge_review_display(context))

    # 3. Collect and verify explicit visual attestation
    attestation_prompt = (
        f"Please enter visual attestation ({FRESHNESS_ATTESTATION_LITERAL}):\n> "
    )
    entered_attestation = input_func(attestation_prompt).strip()
    if entered_attestation != FRESHNESS_ATTESTATION_LITERAL:
        raise OperatorConsoleAbortError(
            f"ERR_INVALID_VISUAL_ATTESTATION: expected exact '{FRESHNESS_ATTESTATION_LITERAL}', got '{entered_attestation}'",
            code="ERR_INVALID_VISUAL_ATTESTATION",
        )

    # 4. Collect and verify explicit confirmation phrase
    confirmation_prompt = (
        f"Please confirm canary trial execution ({CONFIRMATION_PHRASE_LITERAL}):\n> "
    )
    entered_confirmation = input_func(confirmation_prompt).strip()
    if entered_confirmation != CONFIRMATION_PHRASE_LITERAL:
        raise OperatorConsoleAbortError(
            f"ERR_INVALID_CONFIRMATION_PHRASE: expected exact '{CONFIRMATION_PHRASE_LITERAL}', got '{entered_confirmation}'",
            code="ERR_INVALID_CONFIRMATION_PHRASE",
        )

    # 5. Build authoritative signing payload (schema_version 1, 22 signing keys)
    consent_timestamp_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    signing_payload_dict: Dict[str, Any] = {
        "action_payload_digest_sha256": context.action_payload_digest_sha256,
        "action_type": context.action_type,
        "approval_envelope_digest_sha256": context.approval_envelope_digest_sha256,
        "challenge_id": context.challenge_id,
        "client_nonce": context.client_nonce,
        "confirmation_phrase": CONFIRMATION_PHRASE_LITERAL,
        "consent_timestamp_iso": consent_timestamp_iso,
        "duration_frames": context.duration_frames,
        "freshness_attestation": FRESHNESS_ATTESTATION_LITERAL,
        "operator_id": context.operator_id,
        "operator_key_id": context.operator_key_id,
        "plan_digest_sha256": context.plan_digest_sha256,
        "record_frame": context.record_frame,
        "schema_version": 1,
        "source_file_path": context.source_file_path,
        "source_file_sha256": context.source_file_sha256,
        "target_project_name": context.target_project_name,
        "target_project_uid": context.target_project_uid,
        "target_timeline_name": context.target_timeline_name,
        "target_timeline_uid": context.target_timeline_uid,
        "track_index": context.track_index,
        "track_type": context.track_type,
    }

    canonical_signing_bytes = canonical_json_bytes(signing_payload_dict)

    # 6. Sign using injected narrow signer capability
    if hasattr(signer, "sign_operator_consent") and callable(getattr(signer, "sign_operator_consent")):
        signature_hex = signer.sign_operator_consent(canonical_signing_bytes)
    elif callable(signer):
        signature_hex = signer(canonical_signing_bytes)
    else:
        raise OperatorConsoleValidationError(
            f"ERR_INVALID_SIGNER: signer must be callable or implement sign_operator_consent",
            code="ERR_INVALID_SIGNER",
        )

    if not isinstance(signature_hex, str) or not re.match(r"^[0-9a-f]{128}$", signature_hex):
        raise OperatorConsoleValidationError(
            "ERR_INVALID_SIGNATURE_OUTPUT: signer returned non-hex or invalid length signature",
            code="ERR_INVALID_SIGNATURE_OUTPUT",
        )

    # 7. Assemble and validate complete SignedOperatorConsentEnvelope
    envelope_dict = dict(signing_payload_dict)
    envelope_dict["operator_signature"] = signature_hex

    try:
        envelope = SignedOperatorConsentEnvelope.from_dict(envelope_dict)
    except SignedOperatorConsentEnvelopeValidationError as exc:
        raise OperatorConsoleValidationError(
            f"ERR_ENVELOPE_VALIDATION_FAILED: {exc}",
            code="ERR_ENVELOPE_VALIDATION_FAILED",
        ) from exc

    # Confirm critical claim cross-bindings
    assert envelope.challenge_id == context.challenge_id
    assert envelope.client_nonce == context.client_nonce
    assert envelope.target_project_uid == context.target_project_uid
    assert envelope.target_timeline_uid == context.target_timeline_uid
    assert envelope.action_payload_digest_sha256 == context.action_payload_digest_sha256
    assert envelope.plan_digest_sha256 == context.plan_digest_sha256
    assert envelope.approval_envelope_digest_sha256 == context.approval_envelope_digest_sha256

    return envelope
