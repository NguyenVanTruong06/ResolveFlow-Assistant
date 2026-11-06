"""Tests for ResolveFlow Operator Console (G07-AUTH-SIGNER-GATE3-I18)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.contracts.signed_operator_consent_envelope import (
    CONFIRMATION_PHRASE_LITERAL,
    FRESHNESS_ATTESTATION_LITERAL,
    SignedOperatorConsentEnvelope,
)
from src.tools.resolveflow_operator_console import (
    OperatorConsoleAbortError,
    OperatorConsoleValidationError,
    OperatorReviewContext,
    format_challenge_review_display,
    review_and_sign_operator_consent,
)


def make_valid_review_context(**overrides: Any) -> OperatorReviewContext:
    now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
    expires = now + timedelta(seconds=120)
    data: Dict[str, Any] = {
        "challenge_id": "CHALLENGE_2026_CANARY_01",
        "client_nonce": "a1b2c3d4-e5f6-47a8-b9c0-d1e2f3a4b5c6",
        "expires_at_iso": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target_project_name": "RF_TRIAL_CANARY_P02",
        "target_project_uid": "01j7b9c4-d2e1-48a3-b5c7-d9e0f1a2b3c4",
        "target_timeline_name": "RF_TRIAL_TL_CANARY_T02",
        "target_timeline_uid": "01j7b9c4-d2e1-48a3-b5c7-d9e0f1a2b3c5",
        "action_type": "INSERT_CLIP",
        "track_type": "video",
        "track_index": 1,
        "record_frame": 86400,
        "duration_frames": 120,
        "source_file_path": "/var/media/resolveflow/canary_sample.mov",
        "source_file_sha256": "4" * 64,
        "approval_envelope_digest_sha256": "1" * 64,
        "plan_digest_sha256": "2" * 64,
        "action_payload_digest_sha256": "3" * 64,
        "operator_id": "op_vanntruongg",
        "operator_key_id": "op_key_2026_ed25519",
    }
    data.update(overrides)
    return OperatorReviewContext(**data)


def make_ephemeral_signer(key: Ed25519PrivateKey):
    def _signer(payload_bytes: bytes) -> str:
        return key.sign(payload_bytes).hex()
    return _signer


class TestOperatorConsole:
    """Unit tests for ResolveFlow Operator Console."""

    def test_display_contains_complete_bound_context(self):
        ctx = make_valid_review_context()
        rendered = format_challenge_review_display(ctx)

        assert ctx.challenge_id in rendered
        assert ctx.client_nonce in rendered
        assert ctx.expires_at_iso in rendered
        assert ctx.target_project_name in rendered
        assert ctx.target_project_uid in rendered
        assert ctx.target_timeline_name in rendered
        assert ctx.target_timeline_uid in rendered
        assert ctx.action_type in rendered
        assert str(ctx.track_index) in rendered
        assert str(ctx.record_frame) in rendered
        assert str(ctx.duration_frames) in rendered
        assert ctx.source_file_path in rendered
        assert ctx.source_file_sha256 in rendered
        assert ctx.approval_envelope_digest_sha256 in rendered
        assert ctx.plan_digest_sha256 in rendered
        assert ctx.action_payload_digest_sha256 in rendered
        assert ctx.operator_id in rendered
        assert ctx.operator_key_id in rendered

    def test_happy_path_produces_valid_signed_envelope(self):
        ctx = make_valid_review_context()
        key = Ed25519PrivateKey.generate()
        signer = make_ephemeral_signer(key)

        inputs = [
            FRESHNESS_ATTESTATION_LITERAL,
            CONFIRMATION_PHRASE_LITERAL,
        ]
        input_queue = iter(inputs)
        output_lines: List[str] = []

        clock_now = datetime(2026, 11, 4, 12, 0, 10, tzinfo=timezone.utc)

        envelope = review_and_sign_operator_consent(
            context=ctx,
            signer=signer,
            input_func=lambda _: next(input_queue),
            output_func=output_lines.append,
            clock=lambda: clock_now,
        )

        assert isinstance(envelope, SignedOperatorConsentEnvelope)
        assert envelope.challenge_id == ctx.challenge_id
        assert envelope.client_nonce == ctx.client_nonce
        assert envelope.target_project_uid == ctx.target_project_uid
        assert envelope.target_timeline_uid == ctx.target_timeline_uid
        assert envelope.operator_id == ctx.operator_id
        assert envelope.freshness_attestation == FRESHNESS_ATTESTATION_LITERAL
        assert envelope.confirmation_phrase == CONFIRMATION_PHRASE_LITERAL

        # Verify cryptographic signature
        payload_bytes = envelope.canonical_signing_payload_bytes()
        sig_bytes = bytes.fromhex(envelope.operator_signature)
        key.public_key().verify(sig_bytes, payload_bytes)

        # Check rendered output
        joined_output = "\n".join(output_lines)
        assert ctx.challenge_id in joined_output

    def test_missing_or_incorrect_visual_attestation_aborts(self):
        ctx = make_valid_review_context()
        key = Ed25519PrivateKey.generate()
        signer = make_ephemeral_signer(key)
        fixed_now = datetime(2026, 11, 4, 12, 0, 30, tzinfo=timezone.utc)

        for bad_attestation in ["", "NO", "EMPTY_TIMELINE", "operator_visual_attestation"]:
            signer_calls: List[bytes] = []

            def tracking_signer(payload_bytes: bytes) -> str:
                signer_calls.append(payload_bytes)
                return signer(payload_bytes)

            with pytest.raises(OperatorConsoleAbortError) as exc_info:
                review_and_sign_operator_consent(
                    context=ctx,
                    signer=tracking_signer,
                    input_func=lambda _: bad_attestation,
                    output_func=lambda _: None,
                    clock=lambda: fixed_now,
                )
            assert exc_info.value.code == "ERR_INVALID_VISUAL_ATTESTATION"
            assert len(signer_calls) == 0

    def test_missing_or_incorrect_confirmation_phrase_aborts(self):
        ctx = make_valid_review_context()
        key = Ed25519PrivateKey.generate()
        signer = make_ephemeral_signer(key)
        fixed_now = datetime(2026, 11, 4, 12, 0, 30, tzinfo=timezone.utc)

        for bad_confirmation in ["", "yes", "CONFIRM", "confirm canary"]:
            signer_calls: List[bytes] = []

            def tracking_signer(payload_bytes: bytes) -> str:
                signer_calls.append(payload_bytes)
                return signer(payload_bytes)

            input_seq = iter([FRESHNESS_ATTESTATION_LITERAL, bad_confirmation])
            with pytest.raises(OperatorConsoleAbortError) as exc_info:
                review_and_sign_operator_consent(
                    context=ctx,
                    signer=tracking_signer,
                    input_func=lambda _: next(input_seq),
                    output_func=lambda _: None,
                    clock=lambda: fixed_now,
                )
            assert exc_info.value.code == "ERR_INVALID_CONFIRMATION_PHRASE"
            assert len(signer_calls) == 0

    def test_expired_challenge_fails_closed_before_prompting(self):
        ctx = make_valid_review_context(expires_at_iso="2026-11-04T12:00:00Z")
        key = Ed25519PrivateKey.generate()
        signer = make_ephemeral_signer(key)

        now_after_expiry = datetime(2026, 11, 4, 12, 0, 1, tzinfo=timezone.utc)
        with pytest.raises(OperatorConsoleValidationError) as exc_info:
            review_and_sign_operator_consent(
                context=ctx,
                signer=signer,
                input_func=lambda _: pytest.fail("Input should not be called for expired challenge"),
                output_func=lambda _: None,
                clock=lambda: now_after_expiry,
            )
        assert exc_info.value.code == "ERR_CHALLENGE_EXPIRED"

    def test_invalid_context_fields_rejected_fail_closed(self):
        # Bad client nonce (not UUIDv4)
        with pytest.raises(OperatorConsoleValidationError):
            make_valid_review_context(client_nonce="not-a-uuid")

        # Bad challenge ID
        with pytest.raises(OperatorConsoleValidationError):
            make_valid_review_context(challenge_id="bad id with spaces")

        # Bad digest length
        with pytest.raises(OperatorConsoleValidationError):
            make_valid_review_context(plan_digest_sha256="abc")

        # Empty project name
        with pytest.raises(OperatorConsoleValidationError):
            make_valid_review_context(target_project_name="")

    def test_tripwire_no_secrets_in_display_output(self):
        ctx = make_valid_review_context()
        output = format_challenge_review_display(ctx)

        # Confirm no private key markers or secrets exist
        assert "BEGIN PRIVATE KEY" not in output
        assert "PRIVATE" not in output
        assert "secret" not in output.lower()
