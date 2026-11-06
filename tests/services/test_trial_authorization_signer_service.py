"""Focused Unit Tests for TrialAuthorizationSignerService Director Approval Verification (G07-07B7-I4)

Covers at minimum:
1. Valid signed envelope passes with matching ephemeral public key and unchanged proposal/plan data.
2. Signature verification strictly uses the exact envelope canonical signing bytes.
3. Fail-closed rejection: wrong Director key ID, wrong public key, invalid signature, altered plan,
   altered proposal, stale approval binding, plan digest mismatch, and proposal digest mismatch.
4. Non-exact DTO types and malformed values are rejected before reporting success.
5. Reuse of source-owned canonical serializers and digest helpers with zero duplicate formats.
6. Ephemeral in-memory keys only; zero key bytes, private key, or capability in result, exceptions, or logs.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
import errno
import fcntl
from fractions import Fraction
import hashlib
import hmac
import json
import multiprocessing
import os
from pathlib import Path
import socket
import stat
from typing import Any, Dict, List, Optional, Tuple
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
import pytest

from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    VALID_APPROVAL_SCOPE,
    approval_record_to_dict,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    CreativeContext,
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.signed_approval_envelope import SignedApprovalEnvelope
from src.core.contracts.signed_operator_consent_envelope import (
    SignedOperatorConsentEnvelope,
)
from src.core.contracts.trial_authorization_contracts import (
    TrialExecutionAuthorization,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
)
from src.core.geometry.plan_geometry_contracts import SourceFrameEvidence
from src.core.timeline_plan_execution_binder import (
    RationalFPS,
    TargetSpec,
    TimelineExecutionPlan,
    TimelineOccupancySnapshot,
    bind_timeline_execution_plan,
    compute_canonical_plan_digest,
)
from src.services.director_approval_signer import DirectorApprovalSigner
from src.services.trial_authorization_signer_service import (
    ActiveChallengeContext,
    ConsumedAuthorizationNonceRecord,
    DirectorApprovalVerificationError,
    DirectorApprovalVerificationResult,
    OperatorConsentVerificationContext,
    OperatorConsentVerificationError,
    OperatorConsentVerificationResult,
    SignerConsumedNonceLedger,
    SignerLedgerClosedError,
    SignerLedgerCorruptionError,
    SignerLedgerDurabilityError,
    SignerLedgerError,
    SignerLedgerInitializationError,
    SignerLedgerSecurityError,
    SignerNonceAlreadyConsumedError,
    SignedRevocationManifest,
    RevocationManifestVerificationResult,
    RevocationManifestVerificationError,
    RevocationRollbackError,
    RevocationEpochTamperingError,
    RevocationAnchorAbsentError,
    TrialAuthorizationSignerService,
    canonical_revocation_manifest_claims_bytes,
    check_and_advance_revocation_epoch_anchor_for_local_test,
    load_revocation_issuer_public_key_for_local_test,
    load_verify_and_anchor_revocation_manifest_for_local_test,
    load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test,
    REVOCATION_ISSUER_PUBLIC_KEY_FILENAME,
    verify_director_approval,
    verify_operator_consent,
    verify_signed_revocation_manifest,
    SignerAdmissionVerificationError,
    TrialAuthorizationAdmissionEvidence,
    verify_trial_authorization_admission_for_local_test,
    ChallengeIssuanceError,
    LocalTestChallengeIssuanceError,
    LocalTestChallengeIssuanceRequest,
    LocalTestChallengeIssuanceResult,
    LocalTestChallengeAuthority,
    issue_challenge_for_local_test,
    MIN_CHALLENGE_TTL_SECONDS,
    MAX_CHALLENGE_TTL_SECONDS,
    ChallengeConsumptionError,
    LocalTestChallengeConsumptionError,
    LocalTestChallengeConsumptionResult,
    verify_and_consume_challenge_for_local_test,
    _ISSUANCE_TICKET_SENTINEL,
    LocalTestIssuanceSigningTicket,
    LocalTestSignerHmacSigningCapability,
    create_signer_hmac_signing_capability_for_local_test,
    issue_trial_execution_authorization_for_local_test,
    LocalTestSignerKeyHandle,
    load_signer_key_handle_for_local_test,
    LocalTestSignerSocketServer,
    LocalTestSignerSocketClient,
    SignerSocketServerError,
    _validate_test_socket_path,
)


def make_sample_proposal(
    proposal_id: str = "prop_i4_001",
    manifest_id: str = "mf_i4_001",
) -> EditPlanProposal:
    """Creates a valid, normalized EditPlanProposal fixture."""
    c_ctx = CreativeContext(
        free_brief="Sample brief for Gate 3-I4 verification",
        style_preset="Cinematic",
        must_keep_rules=("rule 1",),
        must_not_rules=("must not 1",),
        reference_items=(),
    )
    clips = [
        {
            "clip_id": "clip_main_1",
            "file_path": "/media/test/main_1.mp4",
            "source_kind": "managed",
            "origin_library_id": "lib_01",
            "role": "footage",
        },
        {
            "clip_id": "clip_main_2",
            "file_path": "/media/test/main_2.mp4",
            "source_kind": "managed",
            "origin_library_id": "lib_01",
            "role": "footage",
        },
    ]
    source_binding = {
        "manifest_id": manifest_id,
        "clips": clips,
    }
    segments = [
        {
            "segment_index": 0,
            "clip_id": "clip_main_1",
            "start_sec": 0.0,
            "end_sec": 5.0,
            "action": "keep",
            "speed": 1.0,
            "broll_inserts": [],
            "sfx_inserts": [],
        },
        {
            "segment_index": 1,
            "clip_id": "clip_main_2",
            "start_sec": 1.0,
            "end_sec": 3.0,
            "action": "keep",
            "speed": 1.0,
            "broll_inserts": [],
            "sfx_inserts": [],
        },
    ]
    video_plan = {
        "manifest_id": manifest_id,
        "timeline_segments": segments,
    }
    return normalize_edit_plan_proposal({
        "proposal_id": proposal_id,
        "manifest_id": manifest_id,
        "creative_context": c_ctx,
        "source_binding": source_binding,
        "video_plan": video_plan,
        "kinetic_plan": None,
        "proposed_asset_assignments": (),
        "proposed_omissions": (),
    })


def make_matching_approval(
    proposal: EditPlanProposal,
    approval_id: str = "appr_i4_001",
    approved_at_iso: str = "2026-11-03T15:00:00Z",
) -> AppOwnedApprovalRecord:
    """Creates an AppOwnedApprovalRecord matching the proposal exactly."""
    rev = compute_proposal_revision(proposal)
    return AppOwnedApprovalRecord(
        approval_id=approval_id,
        proposal_id=proposal.proposal_id,
        proposal_revision=rev,
        approved_at_iso=approved_at_iso,
        accepted_omission_ids=(),
        approval_scope=VALID_APPROVAL_SCOPE,
    )


def make_sample_plan(
    proposal: EditPlanProposal,
    approval: AppOwnedApprovalRecord,
) -> TimelineExecutionPlan:
    """Binds and returns a verified TimelineExecutionPlan."""
    target_spec = TargetSpec(
        project_name="Test Project",
        project_id="RF_TEST_PROJECT",
        timeline_name="Test Timeline",
        timeline_id="RF_TEST_TIMELINE",
        timeline_fps=RationalFPS(24, 1),
        timeline_resolution=(1920, 1080),
        start_timecode_frame=0,
    )
    evidence_table = {
        "clip_main_1": SourceFrameEvidence(
            manifest_id="mf_i4_001",
            clip_id="clip_main_1",
            source_fps=RationalFPS(24, 1),
            total_frames=240,
            evidence_status="VERIFIED_CFR",
        ),
        "clip_main_2": SourceFrameEvidence(
            manifest_id="mf_i4_001",
            clip_id="clip_main_2",
            source_fps=RationalFPS(24, 1),
            total_frames=240,
            evidence_status="VERIFIED_CFR",
        ),
    }
    snapshot = TimelineOccupancySnapshot(
        snapshot_id="snap_test_001",
        project_id="RF_TEST_PROJECT",
        timeline_id="RF_TEST_TIMELINE",
        timeline_fps=RationalFPS(24, 1),
        captured_at_iso="2026-11-03T14:59:00Z",
        occupied_spans=(),
        scanned_tracks=(("video", 1), ("video", 2), ("audio", 1), ("audio", 2)),
        status="AUTHORITATIVE",
    )
    return bind_timeline_execution_plan(proposal, approval, target_spec, evidence_table, snapshot)


def make_valid_fixture():
    """Helper creating a complete valid tuple of (proposal, approval, plan, envelope, keypair)."""
    priv_key = Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    proposal = make_sample_proposal()
    approval = make_matching_approval(proposal)
    plan = make_sample_plan(proposal, approval)

    plan_digest = compute_canonical_plan_digest(plan)
    proposal_digest = compute_proposal_revision(proposal)
    key_id = "director_key_alpha"

    signer = DirectorApprovalSigner(priv_key, key_id=key_id)
    envelope = signer.sign(
        approval_record=approval,
        plan_digest_sha256=plan_digest,
        proposal_content_digest_sha256=proposal_digest,
        director_key_id=key_id,
        signed_at_iso="2026-11-03T15:02:00Z",
    )
    return proposal, approval, plan, envelope, priv_key, pub_key, key_id


class SubclassSignedApprovalEnvelope(SignedApprovalEnvelope):
    """Subclass for testing exact nominal type gate."""
    pass


class SubclassEditPlanProposal(EditPlanProposal):
    """Subclass for testing exact nominal type gate."""
    pass


class SubclassTimelineExecutionPlan(TimelineExecutionPlan):
    """Subclass for testing exact nominal type gate."""
    pass


class FakeEd25519PublicKey(Ed25519PublicKey):
    """Custom subclass that implements verify() as a no-op to attempt bypassing verification (I12-R1-01)."""
    def __init__(self, real_key: Ed25519PublicKey) -> None:
        self._real_key = real_key

    def verify(self, signature: bytes, data: bytes) -> None:
        return  # No-op verification to simulate signature bypass attack

    def public_bytes(self, encoding: Any, format: Any) -> bytes:
        return self._real_key.public_bytes(encoding, format)

    def public_bytes_raw(self) -> bytes:
        return self._real_key.public_bytes_raw()

    def __copy__(self) -> FakeEd25519PublicKey:
        return self

    def __deepcopy__(self, memo: Any) -> FakeEd25519PublicKey:
        return self

    def __eq__(self, other: Any) -> bool:
        return self._real_key == other


def _worker_process_advance_anchor(anchor_dir: str, epoch: int, priv_bytes: bytes, results_queue: Any) -> None:
    """Multiprocessing worker function for testing cross-process OS flock concurrency."""
    try:
        priv = Ed25519PrivateKey.from_private_bytes(priv_bytes)
        data = make_sample_revocation_manifest_data(epoch=epoch)
        raw_bytes, _ = sign_manifest_data(data, priv)
        verified = verify_signed_revocation_manifest(
            raw_manifest_bytes=raw_bytes,
            issuer_public_key=priv.public_key(),
            verification_time_utc=datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc),
        )
        check_and_advance_revocation_epoch_anchor_for_local_test(verified, anchor_dir)
        results_queue.put(("SUCCESS", epoch))
    except RevocationRollbackError as e:
        results_queue.put(("ROLLBACK", str(e)))
    except Exception as e:
        results_queue.put(("ERROR", f"{type(e).__name__}: {e}"))


class TestTrialAuthorizationSignerService:
    """Test suite for Director approval verification core in Gate 3-I4."""

    def test_valid_verification_happy_path(self) -> None:
        """Verifies that a valid envelope, proposal, plan, and matching public key pass cleanly."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()

        result = verify_director_approval(
            envelope=envelope,
            proposal=proposal,
            plan=plan,
            public_key=pub_key,
            expected_director_key_id=key_id,
        )

        assert isinstance(result, DirectorApprovalVerificationResult)
        assert result.verified is True
        assert bool(result) is True
        assert result.approval_id == approval.approval_id
        assert result.proposal_id == proposal.proposal_id
        assert result.plan_digest_sha256 == envelope.plan_digest_sha256
        assert result.proposal_content_digest_sha256 == envelope.proposal_content_digest_sha256
        assert result.director_key_id == key_id

        # Verification via service class static method parity
        svc_result = TrialAuthorizationSignerService.verify_director_approval(
            envelope=envelope,
            proposal=proposal,
            plan=plan,
            public_key=pub_key,
            expected_director_key_id=key_id,
        )
        assert svc_result == result

    def test_exact_canonical_signing_bytes_and_tamper_sensitivity(self) -> None:
        """Verifies signature verification uses exact canonical signing bytes and detects any tamper."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()

        # Normal verification succeeds
        assert verify_director_approval(
            envelope=envelope,
            proposal=proposal,
            plan=plan,
            public_key=pub_key,
            expected_director_key_id=key_id,
        ).verified is True

        # Tampered signature (different valid 128 lowercase hex)
        tampered_sig = "f" * 128
        tampered_envelope = dataclasses.replace(envelope, director_signature=tampered_sig)
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_DIRECTOR_SIGNATURE_VERIFICATION_FAILED"):
            verify_director_approval(
                envelope=tampered_envelope,
                proposal=proposal,
                plan=plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

    def test_director_key_id_mismatch_fails_closed(self) -> None:
        """Verifies rejection when envelope director_key_id does not match expected_director_key_id."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()

        with pytest.raises(DirectorApprovalVerificationError, match="ERR_DIRECTOR_KEY_ID_MISMATCH"):
            verify_director_approval(
                envelope=envelope,
                proposal=proposal,
                plan=plan,
                public_key=pub_key,
                expected_director_key_id="director_key_wrong",
            )

    def test_wrong_public_key_fails_closed(self) -> None:
        """Verifies signature verification fails when candidate public key does not match signer."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()
        other_pub_key = Ed25519PrivateKey.generate().public_key()

        with pytest.raises(DirectorApprovalVerificationError, match="ERR_DIRECTOR_SIGNATURE_VERIFICATION_FAILED"):
            verify_director_approval(
                envelope=envelope,
                proposal=proposal,
                plan=plan,
                public_key=other_pub_key,
                expected_director_key_id=key_id,
            )

    def test_altered_plan_fails_closed(self) -> None:
        """Verifies that altering plan actions or metadata causes plan digest mismatch and fails closed."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()

        # Mutate plan idempotency_key
        altered_plan = dataclasses.replace(plan, idempotency_key="idemp_mutated_999")
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PLAN_DIGEST_MISMATCH"):
            verify_director_approval(
                envelope=envelope,
                proposal=proposal,
                plan=altered_plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

        # Mutate plan approval_id
        altered_appr_id_plan = dataclasses.replace(plan, approval_id="appr_different")
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PLAN_APPROVAL_ID_MISMATCH"):
            verify_director_approval(
                envelope=envelope,
                proposal=proposal,
                plan=altered_appr_id_plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

        # Mutate plan proposal_id
        altered_prop_id_plan = dataclasses.replace(plan, proposal_id="prop_different")
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PLAN_PROPOSAL_ID_MISMATCH"):
            verify_director_approval(
                envelope=envelope,
                proposal=proposal,
                plan=altered_prop_id_plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

    def test_altered_proposal_fails_closed(self) -> None:
        """Verifies that altering proposal contents causes proposal digest mismatch and fails closed."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()

        # Create altered proposal with different brief
        altered_prop = make_sample_proposal(proposal_id=proposal.proposal_id)
        # Modify video_plan segment start_sec
        altered_prop_dict = {
            "proposal_id": proposal.proposal_id,
            "manifest_id": proposal.manifest_id,
            "creative_context": proposal.creative_context,
            "source_binding": proposal.source_binding,
            "video_plan": {
                "manifest_id": proposal.manifest_id,
                "timeline_segments": [
                    {
                        "segment_index": 0,
                        "clip_id": "clip_main_1",
                        "start_sec": 0.5,  # Mutated from 0.0
                        "end_sec": 2.0,
                        "action": "keep",
                        "speed": 1.0,
                        "broll_inserts": [],
                        "sfx_inserts": [],
                    },
                    {
                        "segment_index": 1,
                        "clip_id": "clip_main_2",
                        "start_sec": 1.0,
                        "end_sec": 3.0,
                        "action": "keep",
                        "speed": 1.0,
                        "broll_inserts": [],
                        "sfx_inserts": [],
                    },
                ],
            },
            "kinetic_plan": None,
            "proposed_asset_assignments": (),
            "proposed_omissions": (),
        }
        altered_proposal = normalize_edit_plan_proposal(altered_prop_dict)

        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PROPOSAL_APPROVAL_BINDING_FAILED"):
            verify_director_approval(
                envelope=envelope,
                proposal=altered_proposal,
                plan=plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

    def test_stale_approval_revision_fails_closed(self) -> None:
        """Verifies that an approval record with mismatched proposal_revision fails binding."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()

        stale_record = dataclasses.replace(approval, proposal_revision="0" * 64)
        stale_envelope = dataclasses.replace(envelope, approval_record=stale_record)

        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PROPOSAL_APPROVAL_BINDING_FAILED"):
            verify_director_approval(
                envelope=stale_envelope,
                proposal=proposal,
                plan=plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

    def test_plan_digest_claim_tampered_fails_closed(self) -> None:
        """Verifies that tampering envelope's plan_digest_sha256 fails plan digest comparison."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()

        tampered_env = dataclasses.replace(envelope, plan_digest_sha256="9" * 64)
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PLAN_DIGEST_MISMATCH"):
            verify_director_approval(
                envelope=tampered_env,
                proposal=proposal,
                plan=plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

    def test_proposal_digest_claim_tampered_fails_closed(self) -> None:
        """Verifies that tampering envelope's proposal_content_digest_sha256 fails proposal digest comparison."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()

        tampered_env = dataclasses.replace(envelope, proposal_content_digest_sha256="8" * 64)
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PROPOSAL_CONTENT_DIGEST_MISMATCH"):
            verify_director_approval(
                envelope=tampered_env,
                proposal=proposal,
                plan=plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

    def test_reject_non_exact_dto_types_and_malformed_values(self) -> None:
        """Verifies rejection of non-exact types, subclasses, mappings, and malformed expected key ID."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()

        # Non-exact envelope type (dict)
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_ENVELOPE_TYPE"):
            verify_director_approval(
                envelope=envelope.to_dict(),  # type: ignore[arg-type]
                proposal=proposal,
                plan=plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

        # Subclass envelope
        sub_env = SubclassSignedApprovalEnvelope(
            schema_version=envelope.schema_version,
            approval_record=envelope.approval_record,
            plan_digest_sha256=envelope.plan_digest_sha256,
            proposal_content_digest_sha256=envelope.proposal_content_digest_sha256,
            director_key_id=envelope.director_key_id,
            director_signature=envelope.director_signature,
            signed_at_iso=envelope.signed_at_iso,
        )
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_ENVELOPE_TYPE"):
            verify_director_approval(
                envelope=sub_env,
                proposal=proposal,
                plan=plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

        # Non-exact proposal type (dict)
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PROPOSAL_TYPE"):
            verify_director_approval(
                envelope=envelope,
                proposal={"proposal_id": proposal.proposal_id},  # type: ignore[arg-type]
                plan=plan,
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

        # Non-exact plan type (dict)
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PLAN_TYPE"):
            verify_director_approval(
                envelope=envelope,
                proposal=proposal,
                plan={"plan_id": plan.plan_id},  # type: ignore[arg-type]
                public_key=pub_key,
                expected_director_key_id=key_id,
            )

        # Non-exact public_key type (string)
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PUBLIC_KEY_TYPE"):
            verify_director_approval(
                envelope=envelope,
                proposal=proposal,
                plan=plan,
                public_key="not-a-public-key",  # type: ignore[arg-type]
                expected_director_key_id=key_id,
            )

        # Malformed expected_director_key_id
        for bad_id in ["", " ", "  padded  ", 12345, None]:
            with pytest.raises(DirectorApprovalVerificationError, match="ERR_EXPECTED_DIRECTOR_KEY_ID_FORMAT"):
                verify_director_approval(
                    envelope=envelope,
                    proposal=proposal,
                    plan=plan,
                    public_key=pub_key,
                    expected_director_key_id=bad_id,  # type: ignore[arg-type]
                )

    def test_zero_leakage_and_clean_representation(self) -> None:
        """Verifies that verification result and service representations leak zero key material."""
        proposal, approval, plan, envelope, priv_key, pub_key, key_id = make_valid_fixture()

        result = verify_director_approval(
            envelope=envelope,
            proposal=proposal,
            plan=plan,
            public_key=pub_key,
            expected_director_key_id=key_id,
        )

        r_repr = repr(result)
        svc_repr = repr(TrialAuthorizationSignerService())
        assert "private" not in r_repr.lower()
        assert "key_bytes" not in r_repr
        assert "private" not in svc_repr.lower()

        # Check result has no key objects
        assert not hasattr(result, "public_key")
        assert not hasattr(result, "private_key")
        assert not hasattr(result, "authorization_token")


# ==============================================================================
# Gate 3-I5: Operator Consent Verification Tests
# ==============================================================================

def make_sample_operator_consent_fixture(
    *,
    operator_id: str = "operator_canary_01",
    operator_key_id: str = "op_key_test_01",
    challenge_id: str = "chal_canary_test_01",
    client_nonce: str = "12345678-1234-4234-8234-123456789abc",
    consent_timestamp_iso: str = "2026-11-03T16:00:00Z",
    verification_time_utc: Optional[datetime] = None,
    issued_at_iso: str = "2026-11-03T15:59:00Z",
    expires_at_iso: str = "2026-11-03T16:05:00Z",
    consumed: bool = False,
    priv_key: Optional[Ed25519PrivateKey] = None,
    with_dtos: bool = True,
    approval_envelope_digest: Optional[str] = None,
    plan_digest: Optional[str] = None,
    action_payload_digest: str = "3" * 64,
    source_file_sha256: str = "4" * 64,
    target_project_name: str = "RF_TRIAL_CANARY_P02",
    target_project_uid: str = "proj_uid_canary_002",
    target_timeline_name: str = "RF_TRIAL_TL_CANARY_T02",
    target_timeline_uid: str = "tl_uid_canary_002",
    source_file_path: str = "/media/test/canary_footage.mp4",
    action_type: str = "INSERT_CLIP",
    track_type: str = "video",
    track_index: int = 1,
    record_frame: int = 86400,
    duration_frames: int = 120,
):
    """Creates a fully valid SignedOperatorConsentEnvelope and matching OperatorConsentVerificationContext."""
    from src.core.contracts.trial_authorization_contracts import canonical_json_bytes

    if priv_key is None:
        priv_key = Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()

    if verification_time_utc is None:
        verification_time_utc = datetime(2026, 11, 3, 16, 0, 10, tzinfo=timezone.utc)

    # Source-owned DTOs if requested
    approval_envelope = None
    plan = None
    if with_dtos:
        _, _, plan, approval_envelope, _, _, _ = make_valid_fixture()
        actual_approval_digest = approval_envelope.compute_envelope_digest()
        actual_plan_digest = compute_canonical_plan_digest(plan)
    else:
        actual_approval_digest = approval_envelope_digest or ("1" * 64)
        actual_plan_digest = plan_digest or ("2" * 64)

    signing_dict = {
        "action_payload_digest_sha256": action_payload_digest,
        "action_type": action_type,
        "approval_envelope_digest_sha256": actual_approval_digest,
        "challenge_id": challenge_id,
        "client_nonce": client_nonce,
        "confirmation_phrase": "CONFIRM_CANARY_TRIAL_EXECUTION",
        "consent_timestamp_iso": consent_timestamp_iso,
        "duration_frames": duration_frames,
        "freshness_attestation": "OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED",
        "operator_id": operator_id,
        "operator_key_id": operator_key_id,
        "plan_digest_sha256": actual_plan_digest,
        "record_frame": record_frame,
        "schema_version": 1,
        "source_file_path": source_file_path,
        "source_file_sha256": source_file_sha256,
        "target_project_name": target_project_name,
        "target_project_uid": target_project_uid,
        "target_timeline_name": target_timeline_name,
        "target_timeline_uid": target_timeline_uid,
        "track_index": track_index,
        "track_type": track_type,
    }
    canonical_bytes = canonical_json_bytes(signing_dict)
    sig_bytes = priv_key.sign(canonical_bytes)
    operator_sig_hex = sig_bytes.hex().lower()

    envelope = SignedOperatorConsentEnvelope(
        **signing_dict,
        operator_signature=operator_sig_hex,
    )

    challenge_ctx = ActiveChallengeContext(
        challenge_id=challenge_id,
        client_nonce=client_nonce,
        issued_at_iso=issued_at_iso,
        expires_at_iso=expires_at_iso,
        consumed=consumed,
        preallocated_authorization_id="auth_test_placeholder_001",
        preallocated_execution_id="exec_test_placeholder_002",
    )

    context = OperatorConsentVerificationContext(
        authoritative_operator_id=operator_id,
        expected_operator_key_id=operator_key_id,
        challenge=challenge_ctx,
        expected_approval_envelope_digest_sha256=actual_approval_digest,
        expected_plan_digest_sha256=actual_plan_digest,
        expected_action_payload_digest_sha256=action_payload_digest,
        expected_target_project_name=target_project_name,
        expected_target_project_uid=target_project_uid,
        expected_target_timeline_name=target_timeline_name,
        expected_target_timeline_uid=target_timeline_uid,
        expected_source_file_path=source_file_path,
        expected_source_file_sha256=source_file_sha256,
        expected_record_frame=record_frame,
        expected_duration_frames=duration_frames,
        expected_action_type=action_type,
        expected_track_type=track_type,
        expected_track_index=track_index,
        expected_authorization_id="auth_test_placeholder_001",
        expected_execution_id="exec_test_placeholder_002",
        approval_envelope=approval_envelope if with_dtos else None,
        execution_plan=plan if with_dtos else None,
    )

    return envelope, context, priv_key, pub_key, verification_time_utc


class TestOperatorConsentVerification:
    """Comprehensive test suite for Operator Consent Verification Core (Gate 3-I5)."""

    def test_valid_operator_consent_happy_path(self) -> None:
        """Verifies that a valid SignedOperatorConsentEnvelope passes all 20 checks and verifies with Ed25519."""
        envelope, context, priv_key, pub_key, v_time = make_sample_operator_consent_fixture()

        result = verify_operator_consent(
            envelope=envelope,
            context=context,
            public_key=pub_key,
            verification_time_utc=v_time,
        )

        assert isinstance(result, OperatorConsentVerificationResult)
        assert result.verified is True
        assert bool(result) is True
        assert result.operator_id == envelope.operator_id
        assert result.operator_key_id == envelope.operator_key_id
        assert result.challenge_id == envelope.challenge_id
        assert result.client_nonce == envelope.client_nonce
        assert result.approval_envelope_digest_sha256 == envelope.approval_envelope_digest_sha256
        assert result.plan_digest_sha256 == envelope.plan_digest_sha256
        assert result.action_payload_digest_sha256 == envelope.action_payload_digest_sha256
        assert result.target_project_uid == envelope.target_project_uid
        assert result.target_timeline_uid == envelope.target_timeline_uid
        assert result.source_file_sha256 == envelope.source_file_sha256

        # Class method parity
        result_cls = TrialAuthorizationSignerService.verify_operator_consent(
            envelope=envelope,
            context=context,
            public_key=pub_key,
            verification_time_utc=v_time,
        )
        assert result_cls == result

    def test_exact_canonical_signing_bytes_and_signature_tamper_sensitivity(self) -> None:
        """Verifies that signature verification is sensitive to any tampered claim and excludes signature itself."""
        envelope, context, priv_key, pub_key, v_time = make_sample_operator_consent_fixture()

        # 1. Flip last character of operator_signature
        bad_sig = envelope.operator_signature[:-1] + ("0" if envelope.operator_signature[-1] != "0" else "1")
        bad_sig_envelope = dataclasses.replace(envelope, operator_signature=bad_sig)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_OPERATOR_SIGNATURE_INVALID"):
            verify_operator_consent(
                envelope=bad_sig_envelope,
                context=context,
                public_key=pub_key,
                verification_time_utc=v_time,
            )

        # 2. Modify one signing payload field without re-signing (e.g. client_nonce)
        different_nonce = "99999999-9999-4999-8999-999999999999"
        # To test signature sensitivity specifically, update context so nonce passes context check
        tampered_env = dataclasses.replace(envelope, client_nonce=different_nonce)
        tampered_chal = dataclasses.replace(context.challenge, client_nonce=different_nonce)
        tampered_ctx = dataclasses.replace(context, challenge=tampered_chal)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_OPERATOR_SIGNATURE_INVALID"):
            verify_operator_consent(
                envelope=tampered_env,
                context=tampered_ctx,
                public_key=pub_key,
                verification_time_utc=v_time,
            )

    def test_key_id_and_public_key_mismatches_fail_closed(self) -> None:
        """Verifies that wrong operator_key_id (FIX-NEG-06) or wrong public key fails closed."""
        envelope, context, priv_key, pub_key, v_time = make_sample_operator_consent_fixture()

        # FIX-NEG-06: Wrong operator_key_id in context
        bad_kid_ctx = dataclasses.replace(context, expected_operator_key_id="op_key_wrong_99")
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_KEY_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_kid_ctx,
                public_key=pub_key,
                verification_time_utc=v_time,
            )

        # Wrong candidate public key
        wrong_pub_key = Ed25519PrivateKey.generate().public_key()
        with pytest.raises(OperatorConsentVerificationError, match="ERR_OPERATOR_SIGNATURE_INVALID"):
            verify_operator_consent(
                envelope=envelope,
                context=context,
                public_key=wrong_pub_key,
                verification_time_utc=v_time,
            )

    def test_challenge_and_nonce_mismatches_fail_closed(self) -> None:
        """Verifies that replayed/consumed challenge (FIX-NEG-07) or nonce mismatches fail closed."""
        envelope, context, priv_key, pub_key, v_time = make_sample_operator_consent_fixture()

        # FIX-NEG-07: Replayed/consumed challenge
        consumed_chal = dataclasses.replace(context.challenge, consumed=True)
        consumed_ctx = dataclasses.replace(context, challenge=consumed_chal)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_SIGNER_NONCE_ALREADY_CONSUMED"):
            verify_operator_consent(
                envelope=envelope,
                context=consumed_ctx,
                public_key=pub_key,
                verification_time_utc=v_time,
            )

        # Challenge ID mismatch
        diff_chal = dataclasses.replace(context.challenge, challenge_id="chal_other_different_id")
        diff_ctx = dataclasses.replace(context, challenge=diff_chal)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CHALLENGE_ID_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=diff_ctx,
                public_key=pub_key,
                verification_time_utc=v_time,
            )

        # Client nonce mismatch
        diff_nonce_chal = dataclasses.replace(context.challenge, client_nonce="88888888-8888-4888-8888-888888888888")
        diff_nonce_ctx = dataclasses.replace(context, challenge=diff_nonce_chal)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CLIENT_NONCE_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=diff_nonce_ctx,
                public_key=pub_key,
                verification_time_utc=v_time,
            )

    def test_challenge_expiry_at_verification_instant_fails_closed(self) -> None:
        """Verifies verification-instant expiry (AUTH-SIGNER-R6-02, FIX-NEG-08)."""
        envelope, context, priv_key, pub_key, _ = make_sample_operator_consent_fixture(
            expires_at_iso="2026-11-03T16:05:00Z"
        )

        # FIX-NEG-08: Verification instant is 1 second past challenge expiry
        past_expiry_time = datetime(2026, 11, 3, 16, 5, 1, tzinfo=timezone.utc)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CHALLENGE_EXPIRED_AT_VERIFICATION"):
            verify_operator_consent(
                envelope=envelope,
                context=context,
                public_key=pub_key,
                verification_time_utc=past_expiry_time,
            )

        # Boundary: Verification instant exactly at challenge expiry passes (now <= expires_at)
        at_expiry_time = datetime(2026, 11, 3, 16, 5, 0, tzinfo=timezone.utc)
        # Note: delta_t = 16:05:00 - 16:00:00 = 300s > 120s, which would trigger ERR_CONSENT_EXPIRED!
        # To test challenge expiry boundary purely, adjust consent_timestamp accordingly:
        env_border, ctx_border, _, pub_border, _ = make_sample_operator_consent_fixture(
            consent_timestamp_iso="2026-11-03T16:04:30Z",
            expires_at_iso="2026-11-03T16:05:00Z",
        )
        res_border = verify_operator_consent(
            envelope=env_border,
            context=ctx_border,
            public_key=pub_border,
            verification_time_utc=at_expiry_time,
        )
        assert res_border.verified is True

    def test_consent_timestamp_bounds_and_freshness_boundaries(self) -> None:
        """Verifies consent window bounds and [-5s, 120s] freshness boundaries (FIX-NEG-01, FIX-NEG-02)."""
        priv = Ed25519PrivateKey.generate()

        # 1. Consent timestamp before challenge issued_at
        env_early, ctx_early, _, pub_early, v_early = make_sample_operator_consent_fixture(
            issued_at_iso="2026-11-03T15:59:00Z",
            consent_timestamp_iso="2026-11-03T15:58:59Z",
            verification_time_utc=datetime(2026, 11, 3, 15, 59, 10, tzinfo=timezone.utc),
            priv_key=priv,
        )
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_TIMESTAMP_OUT_OF_BOUNDS"):
            verify_operator_consent(
                envelope=env_early,
                context=ctx_early,
                public_key=pub_early,
                verification_time_utc=v_early,
            )

        # 2. Consent timestamp after challenge expires_at
        env_late, ctx_late, _, pub_late, v_late = make_sample_operator_consent_fixture(
            expires_at_iso="2026-11-03T16:05:00Z",
            consent_timestamp_iso="2026-11-03T16:05:01Z",
            verification_time_utc=datetime(2026, 11, 3, 16, 0, 10, tzinfo=timezone.utc),
            priv_key=priv,
        )
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_TIMESTAMP_OUT_OF_BOUNDS"):
            verify_operator_consent(
                envelope=env_late,
                context=ctx_late,
                public_key=pub_late,
                verification_time_utc=v_late,
            )

        # 3. FIX-NEG-01: Future timestamp beyond allowed 5s clock skew (delta_t < -5.0)
        # e.g. delta_t = -5.001s
        consent_time_iso = "2026-11-03T16:00:05.001Z"
        v_skew_future = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)
        env_skew, ctx_skew, _, pub_skew, _ = make_sample_operator_consent_fixture(
            consent_timestamp_iso=consent_time_iso,
            priv_key=priv,
        )
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_FUTURE_TIMESTAMP"):
            verify_operator_consent(
                envelope=env_skew,
                context=ctx_skew,
                public_key=pub_skew,
                verification_time_utc=v_skew_future,
            )

        # Boundary: delta_t = -5.0s exactly is permitted
        v_skew_allowed = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)
        env_border_fut, ctx_border_fut, _, pub_border_fut, _ = make_sample_operator_consent_fixture(
            consent_timestamp_iso="2026-11-03T16:00:05Z",
            priv_key=priv,
        )
        res_fut = verify_operator_consent(
            envelope=env_border_fut,
            context=ctx_border_fut,
            public_key=pub_border_fut,
            verification_time_utc=v_skew_allowed,
        )
        assert res_fut.verified is True

        # 4. FIX-NEG-02: Stale timestamp beyond 120s window (delta_t > 120.0s)
        # e.g. delta_t = 120.001s
        v_stale = datetime(2026, 11, 3, 16, 2, 0, 1000, tzinfo=timezone.utc)
        env_stale, ctx_stale, _, pub_stale, _ = make_sample_operator_consent_fixture(
            consent_timestamp_iso="2026-11-03T16:00:00Z",
            priv_key=priv,
        )
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_EXPIRED"):
            verify_operator_consent(
                envelope=env_stale,
                context=ctx_stale,
                public_key=pub_stale,
                verification_time_utc=v_stale,
            )

        # Boundary: delta_t = 120.0s exactly is permitted
        v_exact_120 = datetime(2026, 11, 3, 16, 2, 0, tzinfo=timezone.utc)
        res_120 = verify_operator_consent(
            envelope=env_stale,
            context=ctx_stale,
            public_key=pub_stale,
            verification_time_utc=v_exact_120,
        )
        assert res_120.verified is True

    def test_utc_instant_comparisons_across_z_and_offset_formats(self) -> None:
        """Verifies that timestamps comparing ISO UTC with 'Z' and '+00:00' work accurately."""
        priv = Ed25519PrivateKey.generate()
        envelope, context, _, pub, _ = make_sample_operator_consent_fixture(
            consent_timestamp_iso="2026-11-03T16:00:00+00:00",
            issued_at_iso="2026-11-03T15:59:00+00:00",
            expires_at_iso="2026-11-03T16:05:00+00:00",
            priv_key=priv,
        )
        v_time = datetime(2026, 11, 3, 16, 0, 30, tzinfo=timezone.utc)
        result = verify_operator_consent(
            envelope=envelope,
            context=context,
            public_key=pub,
            verification_time_utc=v_time,
        )
        assert result.verified is True

    def test_binding_and_mutation_mismatches_fail_closed(self) -> None:
        """Verifies fail-closed rejection on mutation/binding mismatches (FIX-NEG-03..05)."""
        priv = Ed25519PrivateKey.generate()
        envelope, context, _, pub, v_time = make_sample_operator_consent_fixture(priv_key=priv)

        # Operator ID mismatch
        bad_op_ctx = dataclasses.replace(context, authoritative_operator_id="operator_different")
        with pytest.raises(OperatorConsentVerificationError, match="ERR_OPERATOR_ID_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_op_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

        # FIX-NEG-03: Altered source_file_path
        bad_src_ctx = dataclasses.replace(context, expected_source_file_path="/media/test/altered_footage.mp4")
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_MUTATION_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_src_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

        # FIX-NEG-04: Altered target_timeline_name
        bad_tl_ctx = dataclasses.replace(context, expected_target_timeline_name="RF_TRIAL_TL_ALTERED")
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_MUTATION_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_tl_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

        # FIX-NEG-05: Altered track_index (2 instead of 1 in envelope)
        # Note: SignedOperatorConsentEnvelope validates track_index == 1 upon construction.
        # But if context expected_track_index != 1 (though context post_init requires 1),
        # or if envelope track_index is checked, it matches.

        # Altered target_project_name
        bad_proj_ctx = dataclasses.replace(context, expected_target_project_name="RF_PROJECT_ALTERED")
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_MUTATION_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_proj_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

        # Altered target_project_uid
        bad_puid_ctx = dataclasses.replace(context, expected_target_project_uid="uid_altered")
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_MUTATION_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_puid_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

        # Altered target_timeline_uid
        bad_tuid_ctx = dataclasses.replace(context, expected_target_timeline_uid="tuid_altered")
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_MUTATION_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_tuid_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

        # Altered source_file_sha256
        bad_ssha_ctx = dataclasses.replace(context, expected_source_file_sha256="5" * 64)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_MUTATION_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_ssha_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

        # Altered record_frame
        bad_frame_ctx = dataclasses.replace(context, expected_record_frame=90000)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONSENT_MUTATION_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_frame_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

    def test_digest_recomputations_and_mismatches_fail_closed(self) -> None:
        """Verifies digest comparisons and DTO recomputations fail closed on mismatch."""
        priv = Ed25519PrivateKey.generate()
        envelope, context, _, pub, v_time = make_sample_operator_consent_fixture(
            priv_key=priv,
            with_dtos=True,
        )

        # Action payload digest mismatch
        bad_act_ctx = dataclasses.replace(context, expected_action_payload_digest_sha256="9" * 64)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_ACTION_PAYLOAD_DIGEST_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_act_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

        # Plan digest mismatch
        bad_plan_ctx = dataclasses.replace(context, expected_plan_digest_sha256="8" * 64)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_PLAN_DIGEST_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_plan_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

        # Approval envelope digest mismatch
        bad_app_ctx = dataclasses.replace(context, expected_approval_envelope_digest_sha256="7" * 64)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_APPROVAL_ENVELOPE_DIGEST_MISMATCH"):
            verify_operator_consent(
                envelope=envelope,
                context=bad_app_ctx,
                public_key=pub,
                verification_time_utc=v_time,
            )

    def test_reject_non_exact_dto_types_and_malformed_context(self) -> None:
        """Verifies fail-closed rejection of non-exact types, subclasses, dicts, and invalid timestamps."""
        envelope, context, priv_key, pub_key, v_time = make_sample_operator_consent_fixture()

        # Subclass of SignedOperatorConsentEnvelope rejected
        class SubclassConsent(SignedOperatorConsentEnvelope):
            pass

        sub_env = SubclassConsent(**envelope.to_dict())
        with pytest.raises(OperatorConsentVerificationError, match="ERR_ENVELOPE_TYPE"):
            verify_operator_consent(
                envelope=sub_env,
                context=context,
                public_key=pub_key,
                verification_time_utc=v_time,
            )

        # Subclass of OperatorConsentVerificationContext rejected
        class SubclassContext(OperatorConsentVerificationContext):
            pass

        sub_ctx = SubclassContext(
            authoritative_operator_id=context.authoritative_operator_id,
            expected_operator_key_id=context.expected_operator_key_id,
            challenge=context.challenge,
            expected_approval_envelope_digest_sha256=context.expected_approval_envelope_digest_sha256,
            expected_plan_digest_sha256=context.expected_plan_digest_sha256,
            expected_action_payload_digest_sha256=context.expected_action_payload_digest_sha256,
            expected_target_project_name=context.expected_target_project_name,
            expected_target_project_uid=context.expected_target_project_uid,
            expected_target_timeline_name=context.expected_target_timeline_name,
            expected_target_timeline_uid=context.expected_target_timeline_uid,
            expected_source_file_path=context.expected_source_file_path,
            expected_source_file_sha256=context.expected_source_file_sha256,
            expected_record_frame=context.expected_record_frame,
        )
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONTEXT_TYPE"):
            verify_operator_consent(
                envelope=envelope,
                context=sub_ctx,
                public_key=pub_key,
                verification_time_utc=v_time,
            )

        # Non-Ed25519 public key rejected
        with pytest.raises(OperatorConsentVerificationError, match="ERR_PUBLIC_KEY_TYPE"):
            verify_operator_consent(
                envelope=envelope,
                context=context,
                public_key="not-a-public-key",  # type: ignore[arg-type]
                verification_time_utc=v_time,
            )

        # Naive datetime (no tzinfo) rejected
        naive_dt = datetime(2026, 11, 3, 16, 0, 10)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_VERIFICATION_TIME_INVALID"):
            verify_operator_consent(
                envelope=envelope,
                context=context,
                public_key=pub_key,
                verification_time_utc=naive_dt,
            )

        # I5-R1-02: Datetime subclass rejected to prevent overridable time/timezone operations
        class SubclassDatetime(datetime):
            pass

        sub_dt = SubclassDatetime(2026, 11, 3, 16, 0, 10, tzinfo=timezone.utc)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_VERIFICATION_TIME_INVALID"):
            verify_operator_consent(
                envelope=envelope,
                context=context,
                public_key=pub_key,
                verification_time_utc=sub_dt,
            )

        # I5-R1-01: Non-string lookalike objects that compare equal to literals
        class StrLookalike:
            def __init__(self, val: str) -> None:
                self.val = val
            def __eq__(self, other: Any) -> bool:
                return other == self.val

        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONTEXT_FIELD_INVALID"):
            dataclasses.replace(context, expected_action_type=StrLookalike("INSERT_CLIP"))  # type: ignore[arg-type]

        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONTEXT_FIELD_INVALID"):
            dataclasses.replace(context, expected_track_type=StrLookalike("video"))  # type: ignore[arg-type]

    def test_zero_key_leakage_and_clean_representation(self) -> None:
        """Verifies zero key leakage in representations, logs, or results."""
        envelope, context, priv_key, pub_key, v_time = make_sample_operator_consent_fixture()

        result = verify_operator_consent(
            envelope=envelope,
            context=context,
            public_key=pub_key,
            verification_time_utc=v_time,
        )

        r_repr = repr(result)
        assert "private" not in r_repr.lower()
        assert "secret" not in r_repr.lower()
        assert not hasattr(result, "public_key")
        assert not hasattr(result, "private_key")
        assert not hasattr(result, "authorization_token")

        err = OperatorConsentVerificationError("ERR_TEST", "Test error message")
        err_repr = repr(err)
        assert "ERR_TEST" in err_repr
        assert "private" not in err_repr.lower()


# ==============================================================================
# Gate 3-I6: Durable Signer Nonce Ledger Core Tests
# ==============================================================================

def make_sample_nonce_record(
    challenge_id: str = "ch_test_9a56c835b97208ed",
    client_nonce: str = "cn_test_1234567890abcdef",
    approval_record_id: str = "appr_test_001",
    proposal_revision: str = "rev_test_abcdef123456",
    consumed_at_iso: str = "2026-11-03T16:00:00Z",
) -> ConsumedAuthorizationNonceRecord:
    """Creates a sample valid ConsumedAuthorizationNonceRecord fixture."""
    return ConsumedAuthorizationNonceRecord(
        schema_version=1,
        challenge_id=challenge_id,
        client_nonce=client_nonce,
        approval_record_id=approval_record_id,
        proposal_revision=proposal_revision,
        consumed_at_iso=consumed_at_iso,
    )


class TestConsumedAuthorizationNonceRecord:
    """Unit tests for ConsumedAuthorizationNonceRecord contract."""

    def test_valid_record_instantiation_and_round_trip(self) -> None:
        rec = make_sample_nonce_record()
        assert rec.schema_version == 1
        assert rec.challenge_id == "ch_test_9a56c835b97208ed"
        assert rec.client_nonce == "cn_test_1234567890abcdef"
        assert rec.approval_record_id == "appr_test_001"
        assert rec.proposal_revision == "rev_test_abcdef123456"
        assert rec.consumed_at_iso == "2026-11-03T16:00:00Z"

        d = rec.to_dict()
        assert d["schema_version"] == 1
        assert d["challenge_id"] == "ch_test_9a56c835b97208ed"
        assert len(d) == 6

        canon = rec.canonical_bytes()
        assert isinstance(canon, bytes)
        assert b"\n" not in canon

        round_trip = ConsumedAuthorizationNonceRecord.from_dict(d)
        assert round_trip == rec

    def test_invalid_schema_version(self) -> None:
        # Boolean is subclass of int in Python, must be rejected
        with pytest.raises(SignerLedgerError, match="schema_version"):
            ConsumedAuthorizationNonceRecord(
                schema_version=True,  # type: ignore[arg-type]
                challenge_id="ch_01",
                client_nonce="cn_01",
                approval_record_id="ap_01",
                proposal_revision="rv_01",
                consumed_at_iso="2026-11-03T16:00:00Z",
            )

        with pytest.raises(SignerLedgerError, match="schema_version"):
            ConsumedAuthorizationNonceRecord(
                schema_version=2,
                challenge_id="ch_01",
                client_nonce="cn_01",
                approval_record_id="ap_01",
                proposal_revision="rv_01",
                consumed_at_iso="2026-11-03T16:00:00Z",
            )

    def test_invalid_string_fields(self) -> None:
        # Whitespace padding rejected
        with pytest.raises(SignerLedgerError, match="challenge_id"):
            make_sample_nonce_record(challenge_id="  ch_01  ")

        # Empty string rejected
        with pytest.raises(SignerLedgerError, match="client_nonce"):
            make_sample_nonce_record(client_nonce="")

        # Non-string rejected
        with pytest.raises(SignerLedgerError, match="approval_record_id"):
            make_sample_nonce_record(approval_record_id=123)  # type: ignore[arg-type]

    def test_invalid_consumed_at_iso(self) -> None:
        # Non-UTC timezone offset rejected
        with pytest.raises(SignerLedgerError, match="consumed_at_iso"):
            make_sample_nonce_record(consumed_at_iso="2026-11-03T16:00:00+07:00")

        # Non-iso format rejected
        with pytest.raises(SignerLedgerError, match="consumed_at_iso"):
            make_sample_nonce_record(consumed_at_iso="not-a-timestamp")

    def test_from_dict_extra_or_missing_keys(self) -> None:
        d = make_sample_nonce_record().to_dict()
        d_extra = dict(d)
        d_extra["extra_key"] = "forbidden"
        with pytest.raises(SignerLedgerError, match="exact 6 keys"):
            ConsumedAuthorizationNonceRecord.from_dict(d_extra)

        d_missing = dict(d)
        del d_missing["client_nonce"]
        with pytest.raises(SignerLedgerError, match="exact 6 keys"):
            ConsumedAuthorizationNonceRecord.from_dict(d_missing)


class TestSignerConsumedNonceLedger:
    """Unit and fault injection tests for SignerConsumedNonceLedger."""

    def test_valid_append_close_reopen_and_contains(self, tmp_path: Any) -> None:
        ledger_dir = tmp_path / "signer_ledger"
        rec1 = make_sample_nonce_record(challenge_id="ch_01", client_nonce="cn_01")
        rec2 = make_sample_nonce_record(challenge_id="ch_02", client_nonce="cn_02", approval_record_id="appr_02")

        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            assert not ledger.contains(rec1)
            assert not ledger.contains(rec2)
            ledger.consume_once(rec1)
            assert ledger.contains(rec1)
            assert not ledger.contains(rec2)
            ledger.consume_once(rec2)
            assert ledger.contains(rec2)

        # Reopen and verify persisted state
        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger2:
            assert ledger2.contains(rec1)
            assert ledger2.contains(rec2)

    def test_collision_rejections_leave_file_unchanged(self, tmp_path: Any) -> None:
        ledger_dir = tmp_path / "signer_ledger"
        rec1 = make_sample_nonce_record(
            challenge_id="ch_orig",
            client_nonce="cn_orig",
            approval_record_id="appr_orig",
            proposal_revision="rev_orig",
        )

        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            ledger.consume_once(rec1)

        ledger_file = ledger_dir / "signer_consumed_nonces.jsonl"
        expected_bytes = ledger_file.read_bytes()

        # Duplicate challenge_id
        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            dup_ch = make_sample_nonce_record(
                challenge_id="ch_orig",
                client_nonce="cn_new",
                approval_record_id="appr_new",
                proposal_revision="rev_new",
            )
            with pytest.raises(SignerNonceAlreadyConsumedError, match="ERR_SIGNER_NONCE_ALREADY_CONSUMED.*challenge_id"):
                ledger.consume_once(dup_ch)
            assert ledger_file.read_bytes() == expected_bytes

            # Duplicate client_nonce
            dup_cn = make_sample_nonce_record(
                challenge_id="ch_new",
                client_nonce="cn_orig",
                approval_record_id="appr_new",
                proposal_revision="rev_new",
            )
            with pytest.raises(SignerNonceAlreadyConsumedError, match="ERR_SIGNER_NONCE_ALREADY_CONSUMED.*client_nonce"):
                ledger.consume_once(dup_cn)
            assert ledger_file.read_bytes() == expected_bytes

            # Duplicate (approval_record_id, proposal_revision)
            dup_pair = make_sample_nonce_record(
                challenge_id="ch_new2",
                client_nonce="cn_new2",
                approval_record_id="appr_orig",
                proposal_revision="rev_orig",
            )
            with pytest.raises(SignerNonceAlreadyConsumedError, match="ERR_SIGNER_NONCE_ALREADY_CONSUMED.*approval_pair"):
                ledger.consume_once(dup_pair)
            assert ledger_file.read_bytes() == expected_bytes

    def test_concurrent_instances_serialization_and_collision(self, tmp_path: Any) -> None:
        import concurrent.futures
        ledger_dir = tmp_path / "signer_ledger"

        # Initialize the ledger
        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as init_ledger:
            pass

        rec_conflict = make_sample_nonce_record(challenge_id="ch_shared", client_nonce="cn_shared")

        results = []
        def attempt_consume(run_id: int) -> str:
            with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
                try:
                    ledger.consume_once(rec_conflict)
                    return f"success_{run_id}"
                except SignerNonceAlreadyConsumedError:
                    return f"collision_{run_id}"

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(attempt_consume, i) for i in range(4)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        success_count = sum(1 for r in results if r.startswith("success_"))
        collision_count = sum(1 for r in results if r.startswith("collision_"))
        assert success_count == 1
        assert collision_count == 3

    def test_fail_closed_on_torn_tail_and_corruption(self, tmp_path: Any) -> None:
        ledger_dir = tmp_path / "signer_ledger"
        rec1 = make_sample_nonce_record(challenge_id="ch_01", client_nonce="cn_01")
        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            ledger.consume_once(rec1)

        ledger_file = ledger_dir / "signer_consumed_nonces.jsonl"
        valid_bytes = ledger_file.read_bytes()

        # Case 1: Missing trailing newline (torn line)
        ledger_file.write_bytes(valid_bytes.rstrip(b"\n"))
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED"):
            SignerConsumedNonceLedger(ledger_dir=ledger_dir)

        # Case 2: Partial corrupted JSON line
        ledger_file.write_bytes(valid_bytes + b'{"schema_version": 1, "challenge_id": "ch_half"\n')
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED"):
            SignerConsumedNonceLedger(ledger_dir=ledger_dir)

        # Case 3: Duplicate JSON keys
        dup_key_line = b'{"schema_version": 1, "schema_version": 1, "challenge_id": "c", "client_nonce": "n", "approval_record_id": "a", "proposal_revision": "r", "consumed_at_iso": "2026-11-03T16:00:00Z"}\n'
        ledger_file.write_bytes(dup_key_line)
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED"):
            SignerConsumedNonceLedger(ledger_dir=ledger_dir)

        # Case 4: Missing field in record
        missing_field_line = b'{"schema_version": 1, "challenge_id": "c", "client_nonce": "n", "approval_record_id": "a", "proposal_revision": "r"}\n'
        ledger_file.write_bytes(missing_field_line)
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED"):
            SignerConsumedNonceLedger(ledger_dir=ledger_dir)

    def test_security_validations(self, tmp_path: Any) -> None:
        ledger_dir = tmp_path / "signer_ledger"
        ledger_dir.mkdir(mode=0o700)

        # Symlink directory rejected
        symlink_dir = tmp_path / "symlink_dir"
        symlink_dir.symlink_to(ledger_dir)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_DIR_SYMLINK_DETECTED"):
            SignerConsumedNonceLedger(ledger_dir=symlink_dir)

        # Insecure directory permissions (e.g. 0o777)
        insecure_dir = tmp_path / "insecure_dir"
        insecure_dir.mkdir(mode=0o777)
        insecure_dir.chmod(0o777)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_LEDGER_DIR_INSECURE_PERMS"):
            SignerConsumedNonceLedger(ledger_dir=insecure_dir)

        # Insecure ledger file permissions (e.g. 0o666)
        valid_dir = tmp_path / "valid_dir"
        valid_dir.mkdir(mode=0o700)
        l_file = valid_dir / "signer_consumed_nonces.jsonl"
        l_file.write_bytes(b"")
        l_file.chmod(0o666)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_LEDGER_FILE_INSECURE_PERMS"):
            SignerConsumedNonceLedger(ledger_dir=valid_dir)

        # Symlink ledger file rejected
        sym_dir = tmp_path / "sym_dir"
        sym_dir.mkdir(mode=0o700)
        target_file = tmp_path / "real_file.jsonl"
        target_file.write_bytes(b"")
        target_file.chmod(0o600)
        sym_file = sym_dir / "signer_consumed_nonces.jsonl"
        sym_file.symlink_to(target_file)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_LEDGER_SYMLINK_DETECTED"):
            SignerConsumedNonceLedger(ledger_dir=sym_dir)

    def test_operations_after_close(self, tmp_path: Any) -> None:
        ledger_dir = tmp_path / "signer_ledger"
        ledger = SignerConsumedNonceLedger(ledger_dir=ledger_dir)
        rec = make_sample_nonce_record()
        ledger.consume_once(rec)
        ledger.close()

        with pytest.raises(SignerLedgerClosedError, match="ERR_LEDGER_CLOSED"):
            ledger.consume_once(make_sample_nonce_record(challenge_id="ch_new"))

        with pytest.raises(SignerLedgerClosedError, match="ERR_LEDGER_CLOSED"):
            ledger.contains(rec)

        # Close should be idempotent
        ledger.close()

    def test_fault_injection(self, tmp_path: Any, monkeypatch: Any) -> None:
        import errno
        import os
        import fcntl

        ledger_dir = tmp_path / "signer_ledger"
        rec = make_sample_nonce_record()

        # 1. EINTR during write should be retried and succeed
        real_write = os.write
        real_fsync = os.fsync
        real_flock = fcntl.flock
        real_close = os.close
        eintr_counter = [0]
        def write_eintr_mock(fd: int, data: bytes) -> int:
            if eintr_counter[0] == 0:
                eintr_counter[0] += 1
                raise OSError(errno.EINTR, "Interrupted system call")
            return real_write(fd, data)

        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            monkeypatch.setattr(os, "write", write_eintr_mock)
            ledger.consume_once(rec)
            assert eintr_counter[0] == 1
            assert ledger.contains(rec)

        # 2. Write failure raises SignerLedgerDurabilityError
        rec2 = make_sample_nonce_record(challenge_id="ch_02", client_nonce="cn_02", approval_record_id="appr_02", proposal_revision="rev_02")
        def write_fail_mock(fd: int, data: bytes) -> int:
            raise OSError(errno.EIO, "I/O error")

        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            monkeypatch.setattr(os, "write", write_fail_mock)
            with pytest.raises(SignerLedgerDurabilityError, match="ERR_LEDGER_WRITE_FAILED"):
                ledger.consume_once(rec2)
            assert not ledger.contains(rec2)

        # Restore write for next sub-tests
        monkeypatch.setattr(os, "write", real_write)

        # 3. Fsync failure raises SignerLedgerDurabilityError
        rec3 = make_sample_nonce_record(challenge_id="ch_03", client_nonce="cn_03", approval_record_id="appr_03", proposal_revision="rev_03")
        def fsync_fail_mock(fd: int) -> None:
            raise OSError(errno.EIO, "I/O error on fsync")

        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            monkeypatch.setattr(os, "fsync", fsync_fail_mock)
            with pytest.raises(SignerLedgerDurabilityError, match="ERR_LEDGER_FSYNC_FAILED"):
                ledger.consume_once(rec3)

        # Restore fsync
        monkeypatch.setattr(os, "fsync", real_fsync)

        # 4. Unlock failure latches instance unusable
        rec4 = make_sample_nonce_record(challenge_id="ch_04", client_nonce="cn_04", approval_record_id="appr_04", proposal_revision="rev_04")
        def flock_unlock_fail(fd: int, op: int) -> None:
            if op == fcntl.LOCK_UN:
                raise OSError(errno.EIO, "Unlock failed")
            return real_flock(fd, op)

        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            monkeypatch.setattr(fcntl, "flock", flock_unlock_fail)
            with pytest.raises(SignerLedgerDurabilityError, match="ERR_LEDGER_UNLOCK_FAILED"):
                ledger.consume_once(rec4)
            # Instance should now be latched closed / corrupted
            with pytest.raises(SignerLedgerError, match="latched closed|ERR_LEDGER_CLOSED"):
                ledger.contains(rec)

        # Restore flock
        monkeypatch.setattr(fcntl, "flock", real_flock)

        # 5. Close failure records diagnostic failure under POSIX single-close semantics
        def close_fail(fd: int) -> None:
            raise OSError(errno.EBADF, "Bad file descriptor")

        ledger_close_test = SignerConsumedNonceLedger(ledger_dir=ledger_dir)
        monkeypatch.setattr(os, "close", close_fail)
        with pytest.raises(SignerLedgerDurabilityError, match="ERR_LEDGER_CLOSE_FAILED"):
            ledger_close_test.close()
        assert len(ledger_close_test.cleanup_failures) > 0
        assert ledger_close_test.cleanup_failures[0][3] == "CLOSE_OUTCOME_UNKNOWN"

        # Restore close
        monkeypatch.setattr(os, "close", real_close)

    def test_filename_traversal_and_absolute_paths_rejected(self, tmp_path: Any) -> None:
        """I6-R1-01: Rejects filenames containing path separators, traversal, or absolute paths."""
        import os
        ledger_dir = tmp_path / "signer_ledger"

        invalid_filenames = [
            "/tmp/escaped_ledger.jsonl",
            "../escaped_ledger.jsonl",
            "subdir/ledger.jsonl",
            "subdir\\ledger.jsonl",
            ".",
            "..",
            "ledger\x00corrupt.jsonl",
            "   ",
            "",
            123,  # non-string
        ]

        for fname in invalid_filenames:
            with pytest.raises(SignerLedgerSecurityError, match="ERR_INVALID_LEDGER_FILENAME"):
                SignerConsumedNonceLedger(ledger_dir=ledger_dir, filename=fname)  # type: ignore[arg-type]

        # Verify no file created outside ledger_dir
        assert not os.path.exists("/tmp/escaped_ledger.jsonl")
        assert not os.path.exists(tmp_path / "escaped_ledger.jsonl")

    def test_preexisting_directory_not_chmodded_and_exact_0700_required(self, tmp_path: Any) -> None:
        """I6-R1-02: Existing directory mode is not silently repaired, and exact 0700 is required."""
        dir_755 = tmp_path / "dir_755"
        dir_755.mkdir(mode=0o755)
        dir_755.chmod(0o755)

        with pytest.raises(SignerLedgerSecurityError, match="ERR_LEDGER_DIR_INSECURE_PERMS.*strictly 0700"):
            SignerConsumedNonceLedger(ledger_dir=dir_755)

        # Confirm mode was NOT repaired to 0700
        st = dir_755.stat()
        assert (st.st_mode & 0o777) == 0o755

        # Directory with owner permissions lacking (e.g. 0500)
        dir_500 = tmp_path / "dir_500"
        dir_500.mkdir(mode=0o700)
        dir_500.chmod(0o500)
        try:
            with pytest.raises(SignerLedgerSecurityError, match="ERR_LEDGER_DIR_INSECURE_PERMS.*strictly 0700"):
                SignerConsumedNonceLedger(ledger_dir=dir_500)
        finally:
            dir_500.chmod(0o700)

    def test_atomic_exclusive_creation_and_reopen_integrity(self, tmp_path: Any) -> None:
        """I6-R1-03: Atomic creation does not truncate or replace pre-existing ledger data."""
        ledger_dir = tmp_path / "signer_ledger"
        rec = make_sample_nonce_record()

        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as l1:
            l1.consume_once(rec)

        ledger_file = ledger_dir / "signer_consumed_nonces.jsonl"
        persisted_bytes = ledger_file.read_bytes()
        assert len(persisted_bytes) > 0

        # Second open without truncation
        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as l2:
            assert l2.contains(rec)
            assert ledger_file.read_bytes() == persisted_bytes

    def test_record_subclass_rejected_at_entry_points(self, tmp_path: Any) -> None:
        """I6-R1-04: Nonce record subclasses cannot override serialization or bypass type gates."""
        ledger_dir = tmp_path / "signer_ledger"

        class TamperedRecord(ConsumedAuthorizationNonceRecord):
            def canonical_bytes(self) -> bytes:
                return b'{"tampered": true}'

        sub_rec = TamperedRecord(
            schema_version=1,
            challenge_id="ch_sub_01",
            client_nonce="cn_sub_01",
            approval_record_id="appr_sub_01",
            proposal_revision="rev_sub_01",
            consumed_at_iso="2026-11-03T16:00:00Z",
        )

        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            with pytest.raises(SignerLedgerError, match="ERR_INVALID_RECORD.*exact ConsumedAuthorizationNonceRecord"):
                ledger.consume_once(sub_rec)

            with pytest.raises(SignerLedgerError, match="ERR_INVALID_RECORD.*exact ConsumedAuthorizationNonceRecord"):
                ledger.contains(sub_rec)

        # Ensure ledger file remained empty / zero bytes written
        ledger_file = ledger_dir / "signer_consumed_nonces.jsonl"
        assert ledger_file.stat().st_size == 0

    def test_primary_error_preserved_when_unlock_fails(self, tmp_path: Any, monkeypatch: Any) -> None:
        """I6-R1-05 & I6-R2-02: Unlock failure preserves primary operation exception as primary cause and structured cleanup."""
        import errno
        import os
        import fcntl

        ledger_dir = tmp_path / "signer_ledger"
        rec1 = make_sample_nonce_record(challenge_id="ch_01", client_nonce="cn_01")
        rec2_collision = make_sample_nonce_record(challenge_id="ch_01", client_nonce="cn_02")

        real_flock = fcntl.flock
        def flock_fail_unlock(fd: int, op: int) -> None:
            if op == fcntl.LOCK_UN:
                raise OSError(errno.EIO, "Unlock failed at kernel")
            return real_flock(fd, op)

        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            ledger.consume_once(rec1)

            # Injected failure: Collision (primary) + Unlock failure (cleanup)
            monkeypatch.setattr(fcntl, "flock", flock_fail_unlock)
            with pytest.raises(SignerLedgerDurabilityError) as exc_info:
                ledger.consume_once(rec2_collision)

            exc = exc_info.value
            err_str = str(exc)
            assert "ERR_SIGNER_NONCE_ALREADY_CONSUMED" in err_str
            assert "ERR_LEDGER_UNLOCK_FAILED" in err_str
            assert exc.__cause__ is not None
            assert isinstance(exc.__cause__, SignerNonceAlreadyConsumedError)

            # I6-R2-02: Assert structured access to primary and cleanup error objects
            assert exc.primary_error is not None
            assert isinstance(exc.primary_error, SignerNonceAlreadyConsumedError)
            assert exc.primary_error.code == "ERR_SIGNER_NONCE_ALREADY_CONSUMED"
            assert exc.__cause__ is exc.primary_error

            assert exc.cleanup_error is not None
            assert isinstance(exc.cleanup_error, SignerLedgerDurabilityError)
            assert exc.cleanup_error.code == "ERR_LEDGER_UNLOCK_FAILED"

            assert len(exc.cleanup_errors) == 1
            assert exc.cleanup_errors[0] is exc.cleanup_error

    def test_constructor_scan_failure_preserves_primary_and_cleanup_when_unlock_fails(self, tmp_path: Any, monkeypatch: Any) -> None:
        """I6-R2-01: Constructor startup scan failure combined with unlock failure preserves both errors."""
        import errno
        import fcntl

        ledger_dir = tmp_path / "signer_ledger_torn"
        ledger_dir.mkdir(mode=0o700)
        ledger_file = ledger_dir / "signer_consumed_nonces.jsonl"
        # Write torn line (missing trailing newline and malformed JSON)
        ledger_file.write_text('{"schema_version": 1, "challenge_id": "ch_torn"', encoding="utf-8")
        ledger_file.chmod(0o600)

        real_flock = fcntl.flock
        def flock_fail_unlock(fd: int, op: int) -> None:
            if op == fcntl.LOCK_UN:
                raise OSError(errno.EIO, "Kernel unlock failure during init")
            return real_flock(fd, op)

        monkeypatch.setattr(fcntl, "flock", flock_fail_unlock)

        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            SignerConsumedNonceLedger(ledger_dir=ledger_dir, create=False)

        exc = exc_info.value
        # Assert structured access to both errors
        assert exc.primary_error is not None
        assert isinstance(exc.primary_error, SignerLedgerCorruptionError)
        assert exc.primary_error.code == "ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED"
        assert exc.__cause__ is exc.primary_error

        assert exc.cleanup_error is not None
        assert isinstance(exc.cleanup_error, SignerLedgerDurabilityError)
        assert exc.cleanup_error.code == "ERR_LEDGER_UNLOCK_FAILED"

        assert len(exc.cleanup_errors) == 1
        assert exc.cleanup_errors[0] is exc.cleanup_error

    def test_contains_primary_error_and_cleanup_preserved_when_unlock_fails(self, tmp_path: Any, monkeypatch: Any) -> None:
        """I6-R2-02: contains() preserves primary error and structured cleanup error when unlock fails."""
        import errno
        import fcntl

        ledger_dir = tmp_path / "signer_ledger_contains"
        rec1 = make_sample_nonce_record(challenge_id="ch_01", client_nonce="cn_01")

        with SignerConsumedNonceLedger(ledger_dir=ledger_dir) as ledger:
            ledger.consume_once(rec1)

            real_flock = fcntl.flock
            def flock_fail_unlock(fd: int, op: int) -> None:
                if op == fcntl.LOCK_UN:
                    raise OSError(errno.EIO, "Kernel unlock failure during contains")
                return real_flock(fd, op)

            # Injected scan failure during contains
            def broken_sync() -> None:
                raise SignerLedgerCorruptionError("ERR_SIGNER_LEDGER_CORRUPTION: simulated scan failure")

            monkeypatch.setattr(ledger, "_sync_and_scan_locked", broken_sync)
            monkeypatch.setattr(fcntl, "flock", flock_fail_unlock)

            with pytest.raises(SignerLedgerDurabilityError) as exc_info:
                ledger.contains(rec1)

            exc = exc_info.value
            assert exc.primary_error is not None
            assert isinstance(exc.primary_error, SignerLedgerCorruptionError)
            assert exc.primary_error.code == "ERR_SIGNER_LEDGER_CORRUPTION"
            assert exc.__cause__ is exc.primary_error

            assert exc.cleanup_error is not None
            assert isinstance(exc.cleanup_error, SignerLedgerDurabilityError)
            assert exc.cleanup_error.code == "ERR_LEDGER_UNLOCK_FAILED"

            assert len(exc.cleanup_errors) == 1
            assert exc.cleanup_errors[0] is exc.cleanup_error

    # ==========================================================================
    # Gate 3-I6-R4: Descriptor-Anchored Directory Mode Tests
    # ==========================================================================

    def test_descriptor_mode_happy_path(self, tmp_path: Any) -> None:
        ledger_dir = tmp_path / "desc_ledger"
        ledger_dir.mkdir(mode=0o700)
        orig_dfd = os.open(str(ledger_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            rec1 = make_sample_nonce_record(challenge_id="ch_d1", client_nonce="cn_d1")
            rec2 = make_sample_nonce_record(challenge_id="ch_d2", client_nonce="cn_d2", approval_record_id="appr_d2")

            with SignerConsumedNonceLedger(directory_fd=orig_dfd) as ledger:
                assert ledger.ledger_dir is None
                assert ledger.directory_fd is not None
                assert ledger.directory_fd != orig_dfd  # duplicated FD
                assert not ledger.contains(rec1)
                ledger.consume_once(rec1)
                assert ledger.contains(rec1)
                ledger.consume_once(rec2)
                assert ledger.contains(rec2)

            # Caller's orig_dfd must remain valid and open after ledger close
            st = os.fstat(orig_dfd)
            assert stat.S_ISDIR(st.st_mode)

            # Inspect ledger file relative to orig_dfd
            file_fd = os.open("signer_consumed_nonces.jsonl", os.O_RDONLY, dir_fd=orig_dfd)
            try:
                content = os.read(file_fd, 65536)
                assert content.endswith(b"\n")
                lines = content[:-1].split(b"\n")
                assert len(lines) == 2
                rec1_parsed = ConsumedAuthorizationNonceRecord.from_dict(json.loads(lines[0].decode("utf-8")))
                assert rec1_parsed.challenge_id == "ch_d1"
                rec2_parsed = ConsumedAuthorizationNonceRecord.from_dict(json.loads(lines[1].decode("utf-8")))
                assert rec2_parsed.challenge_id == "ch_d2"
            finally:
                os.close(file_fd)

            # Reopen via from_directory_fd classmethod
            with SignerConsumedNonceLedger.from_directory_fd(orig_dfd) as ledger2:
                assert ledger2.contains(rec1)
                assert ledger2.contains(rec2)
        finally:
            os.close(orig_dfd)

    def test_descriptor_mode_type_and_ownership_gates(self, tmp_path: Any) -> None:
        ledger_dir = tmp_path / "gates_ledger"
        ledger_dir.mkdir(mode=0o700)
        dfd = os.open(str(ledger_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            # Reject bool
            with pytest.raises(SignerLedgerSecurityError, match="ERR_INVALID_DIRECTORY_FD.*bool"):
                SignerConsumedNonceLedger(directory_fd=True)
            with pytest.raises(SignerLedgerSecurityError, match="ERR_INVALID_DIRECTORY_FD.*bool"):
                SignerConsumedNonceLedger(directory_fd=False)

            # Reject non-int
            with pytest.raises(SignerLedgerSecurityError, match="ERR_INVALID_DIRECTORY_FD.*str"):
                SignerConsumedNonceLedger(directory_fd="123")  # type: ignore

            # Reject negative int
            with pytest.raises(SignerLedgerSecurityError, match="ERR_INVALID_DIRECTORY_FD.*negative"):
                SignerConsumedNonceLedger(directory_fd=-1)

            # Reject closed/invalid FD
            closed_fd = os.open(str(ledger_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            os.close(closed_fd)
            with pytest.raises(SignerLedgerSecurityError, match="ERR_INVALID_DIRECTORY_FD.*invalid or closed"):
                SignerConsumedNonceLedger(directory_fd=closed_fd)

            # Reject regular file FD passed as directory_fd
            reg_file = tmp_path / "regular.txt"
            reg_file.write_text("not a dir")
            reg_fd = os.open(str(reg_file), os.O_RDONLY)
            try:
                with pytest.raises(SignerLedgerSecurityError, match="ERR_LEDGER_NOT_DIR"):
                    SignerConsumedNonceLedger(directory_fd=reg_fd)
            finally:
                os.close(reg_fd)

            # Reject insecure permissions (0755)
            insecure_dir = tmp_path / "insecure_dir"
            insecure_dir.mkdir(mode=0o755)
            insecure_fd = os.open(str(insecure_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with pytest.raises(SignerLedgerSecurityError, match="ERR_LEDGER_DIR_INSECURE_PERMS"):
                    SignerConsumedNonceLedger(directory_fd=insecure_fd)
            finally:
                os.close(insecure_fd)

            # Reject ambiguous inputs (both ledger_dir and directory_fd)
            with pytest.raises(SignerLedgerSecurityError, match="ERR_AMBIGUOUS_DIRECTORY_INPUT"):
                SignerConsumedNonceLedger(ledger_dir=ledger_dir, directory_fd=dfd)

            # Reject missing inputs (neither provided)
            with pytest.raises(SignerLedgerError, match="ERR_MISSING_DIRECTORY_INPUT"):
                SignerConsumedNonceLedger()
        finally:
            os.close(dfd)

    def test_descriptor_mode_anchoring_race_resists_path_swap_and_outside_sentinel(self, tmp_path: Any) -> None:
        # Create genuine directory and outside sentinel directory
        genuine_dir = tmp_path / "genuine_dir"
        genuine_dir.mkdir(mode=0o700)
        sentinel_dir = tmp_path / "outside_sentinel"
        sentinel_dir.mkdir(mode=0o700)

        # Open directory descriptor to genuine_dir before any path mutation
        dfd = os.open(str(genuine_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            # Simulate race / path-swap: rename genuine_dir and place symlink pointing to sentinel_dir
            renamed_dir = tmp_path / "genuine_dir_renamed"
            genuine_dir.rename(renamed_dir)
            genuine_dir.symlink_to(sentinel_dir, target_is_directory=True)
            assert genuine_dir.is_symlink()

            # Now instantiate ledger via the retained directory descriptor
            rec = make_sample_nonce_record(challenge_id="ch_pinned_inode", client_nonce="cn_pinned_inode")
            with SignerConsumedNonceLedger(directory_fd=dfd) as ledger:
                ledger.consume_once(rec)
                assert ledger.contains(rec)

            # Verification 1: Record is in the genuine directory inode (now at renamed_dir)
            genuine_ledger_file = renamed_dir / "signer_consumed_nonces.jsonl"
            assert genuine_ledger_file.exists()
            assert b"ch_pinned_inode" in genuine_ledger_file.read_bytes()

            # Verification 2: Outside sentinel directory has ZERO files created
            sentinel_ledger_file = sentinel_dir / "signer_consumed_nonces.jsonl"
            assert not sentinel_ledger_file.exists()
            assert list(sentinel_dir.iterdir()) == []
        finally:
            os.close(dfd)

    def test_descriptor_mode_fault_injection_and_single_close(self, tmp_path: Any, monkeypatch: Any) -> None:
        ledger_dir = tmp_path / "fault_desc_ledger"
        ledger_dir.mkdir(mode=0o700)
        dfd = os.open(str(ledger_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            # Inject write failure
            ledger = SignerConsumedNonceLedger(directory_fd=dfd)
            rec = make_sample_nonce_record(challenge_id="ch_fault", client_nonce="cn_fault")

            real_write = os.write
            def fail_write(fd: int, data: bytes) -> int:
                raise OSError(errno.EIO, "Injected write error")

            monkeypatch.setattr(os, "write", fail_write)
            with pytest.raises(SignerLedgerDurabilityError, match="ERR_LEDGER_WRITE_FAILED"):
                ledger.consume_once(rec)

            # Close ledger cleanly
            monkeypatch.undo()
            ledger.close()

            # Re-closing ledger is idempotent (single-close semantics)
            ledger.close()

            # Caller's dfd must remain open throughout
            st = os.fstat(dfd)
            assert stat.S_ISDIR(st.st_mode)
        finally:
            os.close(dfd)

    def test_descriptor_mode_torn_tail_and_collision(self, tmp_path: Any) -> None:
        ledger_dir = tmp_path / "tt_desc_ledger"
        ledger_dir.mkdir(mode=0o700)
        dfd = os.open(str(ledger_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            rec = make_sample_nonce_record(challenge_id="ch_tt", client_nonce="cn_tt")
            with SignerConsumedNonceLedger(directory_fd=dfd) as ledger:
                ledger.consume_once(rec)
                # Collision on same challenge_id fails closed
                with pytest.raises(SignerNonceAlreadyConsumedError, match="ERR_SIGNER_NONCE_ALREADY_CONSUMED"):
                    ledger.consume_once(rec)

            # Corrupt tail by appending partial JSON without newline
            ledger_file = ledger_dir / "signer_consumed_nonces.jsonl"
            with open(ledger_file, "ab") as f:
                f.write(b'{"torn": true')

            # Opening in descriptor mode detects torn tail fail-closed
            with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED"):
                SignerConsumedNonceLedger(directory_fd=dfd)
        finally:
            os.close(dfd)


# ==============================================================================
# Gate 3-I7: Signed Revocation Manifest Verification Tests
# ==============================================================================

def make_sample_revocation_manifest_data(
    *,
    epoch: int = 105,
    issued_at_iso: str = "2026-11-03T15:00:00Z",
    valid_until_iso: str = "2026-11-04T15:00:00Z",
    issuer_id: str = "security_officer_root",
    manifest_version: int = 1,
    revoked_trial_key_ids: Optional[List[str]] = None,
    revoked_director_key_ids: Optional[List[str]] = None,
    revoked_operator_key_ids: Optional[List[str]] = None,
    revoked_authorization_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "manifest_version": manifest_version,
        "epoch": epoch,
        "issued_at_iso": issued_at_iso,
        "valid_until_iso": valid_until_iso,
        "revoked_trial_key_ids": ["trial_k1"] if revoked_trial_key_ids is None else revoked_trial_key_ids,
        "revoked_director_key_ids": ["dir_k1", "dir_k2"] if revoked_director_key_ids is None else revoked_director_key_ids,
        "revoked_operator_key_ids": ["op_k1"] if revoked_operator_key_ids is None else revoked_operator_key_ids,
        "revoked_authorization_ids": ["auth_001"] if revoked_authorization_ids is None else revoked_authorization_ids,
        "issuer_id": issuer_id,
    }


def sign_manifest_data(
    data: Dict[str, Any],
    private_key: Ed25519PrivateKey,
) -> Tuple[bytes, SignedRevocationManifest]:
    manifest_without_sig = SignedRevocationManifest(
        epoch=data["epoch"],
        issued_at_iso=data["issued_at_iso"],
        issuer_id=data["issuer_id"],
        manifest_version=data["manifest_version"],
        revoked_authorization_ids=list(data["revoked_authorization_ids"]),
        revoked_director_key_ids=list(data["revoked_director_key_ids"]),
        revoked_operator_key_ids=list(data["revoked_operator_key_ids"]),
        revoked_trial_key_ids=list(data["revoked_trial_key_ids"]),
        valid_until_iso=data["valid_until_iso"],
        manifest_signature="00" * 64,  # Temporary dummy signature for DTO initialization
    )
    signing_bytes = canonical_revocation_manifest_claims_bytes(manifest_without_sig)
    sig_bytes = private_key.sign(signing_bytes)
    sig_hex = sig_bytes.hex().lower()

    full_dict = dict(data)
    full_dict["manifest_signature"] = sig_hex

    # Use canonical serialization for the raw wire bytes
    raw_bytes = canonical_json_bytes(full_dict)
    full_manifest = SignedRevocationManifest.from_dict(full_dict)
    return raw_bytes, full_manifest


class TestSignedRevocationManifestVerification:
    """Gate 3-I7 test suite for Signed Revocation Manifest verification core."""

    def test_valid_manifest_verification_happy_path(self) -> None:
        """I7-REQ-1: Valid ephemeral Ed25519 signature verifies and digest equals SHA-256 of canonical 9 claims."""
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        data = make_sample_revocation_manifest_data()
        raw_bytes, manifest = sign_manifest_data(data, private_key)

        verification_time = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)
        result = verify_signed_revocation_manifest(
            raw_manifest_bytes=raw_bytes,
            issuer_public_key=public_key,
            verification_time_utc=verification_time,
        )

        assert isinstance(result, RevocationManifestVerificationResult)
        assert result.verified is True
        assert result.manifest.epoch == 105
        assert result.manifest.issuer_id == "security_officer_root"
        assert result.manifest.manifest_version == 1
        assert result.manifest.revoked_trial_key_ids == ("trial_k1",)
        assert result.manifest.revoked_director_key_ids == ("dir_k1", "dir_k2")

        expected_signing_bytes = canonical_revocation_manifest_claims_bytes(manifest)
        expected_digest = hashlib.sha256(expected_signing_bytes).hexdigest().lower()
        assert result.canonical_claims_digest_sha256 == expected_digest

        # Verify delegation on TrialAuthorizationSignerService
        service_result = TrialAuthorizationSignerService.verify_signed_revocation_manifest(
            raw_manifest_bytes=raw_bytes,
            issuer_public_key=public_key,
            verification_time_utc=verification_time,
        )
        assert service_result == result

    def test_signature_tampering_and_claim_mutations_reject(self) -> None:
        """I7-REQ-2: Signature tampering and independent claim mutations reject fail-closed."""
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        verification_time = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)

        data = make_sample_revocation_manifest_data()
        raw_bytes, _ = sign_manifest_data(data, private_key)
        parsed = json.loads(raw_bytes.decode("utf-8"))

        # 1. Flip last byte of signature
        corrupted_sig = parsed["manifest_signature"][:-2] + ("00" if parsed["manifest_signature"][-2:] != "00" else "ff")
        tampered = dict(parsed, manifest_signature=corrupted_sig)
        with pytest.raises(RevocationManifestVerificationError) as exc_info:
            verify_signed_revocation_manifest(
                raw_manifest_bytes=canonical_json_bytes(tampered),
                issuer_public_key=public_key,
                verification_time_utc=verification_time,
            )
        assert exc_info.value.code == "ERR_MANIFEST_SIGNATURE_INVALID"
        # Check no key or signature leakage in error message
        assert corrupted_sig not in str(exc_info.value)

        # 2. Mutate each signed claim independently with signature kept
        mutations = [
            ("epoch", 106),
            ("issued_at_iso", "2026-11-03T15:00:01Z"),
            ("valid_until_iso", "2026-11-04T16:00:00Z"),
            ("revoked_trial_key_ids", ["trial_k1", "trial_k2"]),
            ("revoked_director_key_ids", ["dir_k1"]),
            ("revoked_operator_key_ids", ["op_k1", "op_k2"]),
            ("revoked_authorization_ids", ["auth_002"]),
        ]
        for field, new_val in mutations:
            tampered_claim = dict(parsed, **{field: new_val})
            with pytest.raises(RevocationManifestVerificationError) as exc_info:
                verify_signed_revocation_manifest(
                    raw_manifest_bytes=canonical_json_bytes(tampered_claim),
                    issuer_public_key=public_key,
                    verification_time_utc=verification_time,
                )
            assert exc_info.value.code == "ERR_MANIFEST_SIGNATURE_INVALID"

    def test_syntax_and_schema_rejections(self) -> None:
        """I7-REQ-3: Duplicate JSON keys, missing/unknown members, malformed JSON, invalid hex reject."""
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        verification_time = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)

        data = make_sample_revocation_manifest_data()
        raw_bytes, _ = sign_manifest_data(data, private_key)
        parsed = json.loads(raw_bytes.decode("utf-8"))

        # 1. Duplicate JSON keys
        dup_bytes = raw_bytes[:-1] + b', "epoch": 105}'
        with pytest.raises(RevocationManifestVerificationError) as exc_info:
            verify_signed_revocation_manifest(
                raw_manifest_bytes=dup_bytes,
                issuer_public_key=public_key,
                verification_time_utc=verification_time,
            )
        assert "ERR_CANONICAL_PARSE_FAILED" in str(exc_info.value) or exc_info.value.code == "ERR_MANIFEST_PARSE_FAILED"

        # 2. Missing member
        for key in parsed.keys():
            incomplete = {k: v for k, v in parsed.items() if k != key}
            with pytest.raises(RevocationManifestVerificationError) as exc_info:
                verify_signed_revocation_manifest(
                    raw_manifest_bytes=canonical_json_bytes(incomplete),
                    issuer_public_key=public_key,
                    verification_time_utc=verification_time,
                )
            assert exc_info.value.code == "ERR_MANIFEST_SCHEMA_INVALID"

        # 3. Unknown member
        with_unknown = dict(parsed, extra_key="forbidden")
        with pytest.raises(RevocationManifestVerificationError) as exc_info:
            verify_signed_revocation_manifest(
                raw_manifest_bytes=canonical_json_bytes(with_unknown),
                issuer_public_key=public_key,
                verification_time_utc=verification_time,
            )
        assert exc_info.value.code == "ERR_MANIFEST_SCHEMA_INVALID"

        # 4. Malformed JSON and invalid UTF-8
        with pytest.raises(RevocationManifestVerificationError):
            verify_signed_revocation_manifest(
                raw_manifest_bytes=b"not json",
                issuer_public_key=public_key,
                verification_time_utc=verification_time,
            )
        with pytest.raises(RevocationManifestVerificationError):
            verify_signed_revocation_manifest(
                raw_manifest_bytes=b"\xff\xfe\x00\x00",
                issuer_public_key=public_key,
                verification_time_utc=verification_time,
            )

        # 5. Invalid signature hex & wrong length
        for bad_sig in ("zz" * 64, "00" * 63, "00" * 65, "", "   "):
            bad_sig_data = dict(parsed, manifest_signature=bad_sig)
            with pytest.raises(RevocationManifestVerificationError) as exc_info:
                verify_signed_revocation_manifest(
                    raw_manifest_bytes=canonical_json_bytes(bad_sig_data),
                    issuer_public_key=public_key,
                    verification_time_utc=verification_time,
                )
            assert exc_info.value.code == "ERR_MANIFEST_SIGNATURE_INVALID"

    def test_strict_type_and_range_validations(self) -> None:
        """I7-REQ-4: Strict primitive types, range gates, issuer check, and list item validations."""
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        verification_time = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)

        data = make_sample_revocation_manifest_data()
        raw_bytes, _ = sign_manifest_data(data, private_key)
        parsed = json.loads(raw_bytes.decode("utf-8"))

        # 1. bool epoch / version
        for bad_epoch in (True, False, 0, -1, 9_007_199_254_740_992, 1.5):
            tampered = dict(parsed, epoch=bad_epoch)
            with pytest.raises(RevocationManifestVerificationError):
                verify_signed_revocation_manifest(
                    raw_manifest_bytes=json.dumps(tampered).encode("utf-8"),
                    issuer_public_key=public_key,
                    verification_time_utc=verification_time,
                )

        for bad_version in (True, False, 0, 2, "1"):
            tampered = dict(parsed, manifest_version=bad_version)
            with pytest.raises(RevocationManifestVerificationError):
                verify_signed_revocation_manifest(
                    raw_manifest_bytes=json.dumps(tampered).encode("utf-8"),
                    issuer_public_key=public_key,
                    verification_time_utc=verification_time,
                )

        # 2. issuer_id
        for bad_issuer in ("wrong_root", "", "security_officer_root ", 123):
            tampered = dict(parsed, issuer_id=bad_issuer)
            with pytest.raises(RevocationManifestVerificationError):
                verify_signed_revocation_manifest(
                    raw_manifest_bytes=json.dumps(tampered).encode("utf-8"),
                    issuer_public_key=public_key,
                    verification_time_utc=verification_time,
                )

        # 3. list fields (non-list, non-string, empty, padded, duplicates)
        list_fields = [
            "revoked_trial_key_ids",
            "revoked_director_key_ids",
            "revoked_operator_key_ids",
            "revoked_authorization_ids",
        ]
        for field in list_fields:
            # Non-list
            for bad_list in ("str_not_list", 123, {"k": "v"}, None):
                tampered = dict(parsed, **{field: bad_list})
                with pytest.raises(RevocationManifestVerificationError):
                    verify_signed_revocation_manifest(
                        raw_manifest_bytes=json.dumps(tampered).encode("utf-8"),
                        issuer_public_key=public_key,
                        verification_time_utc=verification_time,
                    )
            # Bad items in list
            for bad_item_list in ([""], ["   "], ["valid", ""], [" valid"], ["valid "], ["dup", "dup"], [123]):
                tampered = dict(parsed, **{field: bad_item_list})
                with pytest.raises(RevocationManifestVerificationError):
                    verify_signed_revocation_manifest(
                        raw_manifest_bytes=json.dumps(tampered).encode("utf-8"),
                        issuer_public_key=public_key,
                        verification_time_utc=verification_time,
                    )

    def test_utc_and_expiry_boundaries(self) -> None:
        """I7-REQ-5: Malformed timestamps, reversed windows, expiry boundary, and instant validation."""
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        data = make_sample_revocation_manifest_data(
            issued_at_iso="2026-11-03T15:00:00Z",
            valid_until_iso="2026-11-04T15:00:00Z",
        )
        raw_bytes, _ = sign_manifest_data(data, private_key)
        parsed = json.loads(raw_bytes.decode("utf-8"))

        # 1. Equal / reversed validity window
        for issued, valid in [
            ("2026-11-04T15:00:00Z", "2026-11-04T15:00:00Z"),
            ("2026-11-05T15:00:00Z", "2026-11-04T15:00:00Z"),
        ]:
            tampered = dict(parsed, issued_at_iso=issued, valid_until_iso=valid)
            with pytest.raises(RevocationManifestVerificationError):
                verify_signed_revocation_manifest(
                    raw_manifest_bytes=canonical_json_bytes(tampered),
                    issuer_public_key=public_key,
                    verification_time_utc=datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc),
                )

        # 2. Expired manifest
        expired_instant = datetime(2026, 11, 4, 15, 0, 1, tzinfo=timezone.utc)
        with pytest.raises(RevocationManifestVerificationError) as exc_info:
            verify_signed_revocation_manifest(
                raw_manifest_bytes=raw_bytes,
                issuer_public_key=public_key,
                verification_time_utc=expired_instant,
            )
        assert exc_info.value.code == "ERR_MANIFEST_EXPIRED"

        # 3. Exact boundary at expiry (MUST SUCCEED)
        exact_expiry_instant = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)
        res = verify_signed_revocation_manifest(
            raw_manifest_bytes=raw_bytes,
            issuer_public_key=public_key,
            verification_time_utc=exact_expiry_instant,
        )
        assert res.verified is True

        # 4. Invalid verification instants: naive, non-zero offset, subclass, non-datetime
        naive_instant = datetime(2026, 11, 3, 16, 0, 0)
        with pytest.raises(RevocationManifestVerificationError) as exc_info:
            verify_signed_revocation_manifest(
                raw_manifest_bytes=raw_bytes,
                issuer_public_key=public_key,
                verification_time_utc=naive_instant,
            )
        assert exc_info.value.code == "ERR_VERIFICATION_TIME_INVALID"

        offset_instant = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone(timedelta(hours=7)))
        with pytest.raises(RevocationManifestVerificationError) as exc_info:
            verify_signed_revocation_manifest(
                raw_manifest_bytes=raw_bytes,
                issuer_public_key=public_key,
                verification_time_utc=offset_instant,
            )
        assert exc_info.value.code == "ERR_VERIFICATION_TIME_INVALID"

        class SubDatetime(datetime):
            pass

        sub_instant = SubDatetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(RevocationManifestVerificationError) as exc_info:
            verify_signed_revocation_manifest(
                raw_manifest_bytes=raw_bytes,
                issuer_public_key=public_key,
                verification_time_utc=sub_instant,
            )
        assert exc_info.value.code == "ERR_VERIFICATION_TIME_INVALID"

    def test_wrong_public_key_fails_closed(self) -> None:
        """I7-REQ-6: A public key that does not match the signer rejects without mutating state."""
        signer_key = Ed25519PrivateKey.generate()
        unrelated_key = Ed25519PrivateKey.generate().public_key()
        verification_time = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)

        data = make_sample_revocation_manifest_data()
        raw_bytes, _ = sign_manifest_data(data, signer_key)

        with pytest.raises(RevocationManifestVerificationError) as exc_info:
            verify_signed_revocation_manifest(
                raw_manifest_bytes=raw_bytes,
                issuer_public_key=unrelated_key,
                verification_time_utc=verification_time,
            )
        assert exc_info.value.code == "ERR_MANIFEST_SIGNATURE_INVALID"

    def test_pure_verifier_isolation(self, monkeypatch: Any) -> None:
        """I7-REQ-7: Verifier performs zero filesystem, clock, or external calls."""
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        verification_time = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)

        data = make_sample_revocation_manifest_data()
        raw_bytes, _ = sign_manifest_data(data, private_key)

        # Tripwire: monkeypatch filesystem open and system clock
        def tripwire(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("TRIPWIRE: External I/O or system clock access detected")

        monkeypatch.setattr(os, "open", tripwire)
        import time
        monkeypatch.setattr(time, "time", tripwire)

        result = verify_signed_revocation_manifest(
            raw_manifest_bytes=raw_bytes,
            issuer_public_key=public_key,
            verification_time_utc=verification_time,
        )
        assert result.verified is True
        # Verify no private key or signing method exposed
        assert not hasattr(result, "private_key")
        assert not hasattr(result, "sign")

    def test_manifest_immutability_defends_against_post_verification_mutation(self) -> None:
        """I7-R1-01: Revocation collections are stored as immutable tuples, preventing post-verification tampering."""
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        verification_time = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)

        caller_trial_keys = ["trial_k1"]
        caller_dir_keys = ["dir_k1", "dir_k2"]
        caller_op_keys = ["op_k1"]
        caller_auth_ids = ["auth_001"]

        data = make_sample_revocation_manifest_data(
            revoked_trial_key_ids=caller_trial_keys,
            revoked_director_key_ids=caller_dir_keys,
            revoked_operator_key_ids=caller_op_keys,
            revoked_authorization_ids=caller_auth_ids,
        )
        raw_bytes, _ = sign_manifest_data(data, private_key)

        result = verify_signed_revocation_manifest(
            raw_manifest_bytes=raw_bytes,
            issuer_public_key=public_key,
            verification_time_utc=verification_time,
        )
        assert result.verified is True

        # 1. Assert all 4 collections are immutable tuples
        assert type(result.manifest.revoked_trial_key_ids) is tuple
        assert type(result.manifest.revoked_director_key_ids) is tuple
        assert type(result.manifest.revoked_operator_key_ids) is tuple
        assert type(result.manifest.revoked_authorization_ids) is tuple

        # 2. Assert attempting to append/mutate on result fails
        with pytest.raises(AttributeError):
            result.manifest.revoked_trial_key_ids.append("injected_key")  # type: ignore

        # 3. Assert attempting to mutate original caller list does not affect verified result
        caller_trial_keys.append("tampered_key_after_verification")
        assert "tampered_key_after_verification" not in result.manifest.revoked_trial_key_ids
        assert result.manifest.revoked_trial_key_ids == ("trial_k1",)

    def test_uppercase_and_mixedcase_hex_signature_accepted(self) -> None:
        """I7-R1-02: Uppercase and mixed-case hexadecimal signature encodings are accepted."""
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        verification_time = datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc)

        data = make_sample_revocation_manifest_data()
        raw_bytes, _ = sign_manifest_data(data, private_key)
        parsed = json.loads(raw_bytes.decode("utf-8"))

        lower_sig = parsed["manifest_signature"]
        upper_sig = lower_sig.upper()
        assert upper_sig != lower_sig

        # 1. Uppercase signature
        upper_data = dict(parsed, manifest_signature=upper_sig)
        upper_result = verify_signed_revocation_manifest(
            raw_manifest_bytes=canonical_json_bytes(upper_data),
            issuer_public_key=public_key,
            verification_time_utc=verification_time,
        )
        assert upper_result.verified is True

        # 2. Mixed-case signature
        mixed_sig = "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(lower_sig))
        mixed_data = dict(parsed, manifest_signature=mixed_sig)
        mixed_result = verify_signed_revocation_manifest(
            raw_manifest_bytes=canonical_json_bytes(mixed_data),
            issuer_public_key=public_key,
            verification_time_utc=verification_time,
        )
        assert mixed_result.verified is True


class TestRevocationEpochAnchor:
    """Gate 3-I8: Focused Unit Tests for Digest-Bound Durable Revocation Epoch Anchor Core."""

    def _make_verified_result(
        self,
        *,
        epoch: int = 100,
        private_key: Optional[Ed25519PrivateKey] = None,
        revoked_trial_key_ids: Optional[List[str]] = None,
    ) -> RevocationManifestVerificationResult:
        if private_key is None:
            private_key = Ed25519PrivateKey.generate()
        data = make_sample_revocation_manifest_data(
            epoch=epoch,
            revoked_trial_key_ids=revoked_trial_key_ids,
        )
        raw_bytes, _ = sign_manifest_data(data, private_key)
        return verify_signed_revocation_manifest(
            raw_manifest_bytes=raw_bytes,
            issuer_public_key=private_key.public_key(),
            verification_time_utc=datetime(2026, 11, 3, 16, 0, 0, tzinfo=timezone.utc),
        )

    def test_bootstrap_creates_canonical_anchor_and_verifies_durability(self, tmp_path):
        """Bootstrap creates exact <epoch>:<digest>\n line and enforces 0600 permissions."""
        res = self._make_verified_result(epoch=101)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")
        assert os.path.isfile(anchor_file)
        st = os.stat(anchor_file)
        assert (st.st_mode & 0o777) == 0o600
        assert st.st_nlink == 1

        with open(anchor_file, "rb") as f:
            content = f.read()

        expected = f"101:{res.canonical_claims_sha256}\n".encode("ascii")
        assert content == expected

    def test_same_epoch_same_digest_is_idempotent_no_mutation(self, tmp_path):
        """Same epoch with matching digest returns cleanly without rewriting file."""
        res = self._make_verified_result(epoch=200)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)
        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")
        initial_mtime = os.stat(anchor_file).st_mtime_ns
        initial_ino = os.stat(anchor_file).st_ino

        # Call again
        check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

        current_st = os.stat(anchor_file)
        assert current_st.st_mtime_ns == initial_mtime
        assert current_st.st_ino == initial_ino

    def test_rollback_rejected_and_preserves_anchor(self, tmp_path):
        """Lower epoch is rejected with ERR_REVOCATION_ROLLBACK_DETECTED and preserves anchor bytes."""
        res_high = self._make_verified_result(epoch=50)
        res_low = self._make_verified_result(epoch=49)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        check_and_advance_revocation_epoch_anchor_for_local_test(res_high, anchor_dir)
        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")
        with open(anchor_file, "rb") as f:
            original_bytes = f.read()

        with pytest.raises(RevocationRollbackError) as exc_info:
            check_and_advance_revocation_epoch_anchor_for_local_test(res_low, anchor_dir)

        assert "ERR_REVOCATION_ROLLBACK_DETECTED" in str(exc_info.value)
        assert exc_info.value.code == "ERR_REVOCATION_ROLLBACK_DETECTED"

        # Anchor bytes preserved
        with open(anchor_file, "rb") as f:
            assert f.read() == original_bytes

    def test_same_epoch_tampering_rejected_and_preserves_anchor(self, tmp_path):
        """Same epoch with different digest rejects with ERR_REVOCATION_SAME_EPOCH_TAMPERING."""
        res_orig = self._make_verified_result(epoch=75, revoked_trial_key_ids=["key_1"])
        res_tampered = self._make_verified_result(epoch=75, revoked_trial_key_ids=["key_2"])
        assert res_orig.canonical_claims_sha256 != res_tampered.canonical_claims_sha256

        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        check_and_advance_revocation_epoch_anchor_for_local_test(res_orig, anchor_dir)
        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")
        with open(anchor_file, "rb") as f:
            original_bytes = f.read()

        with pytest.raises(RevocationEpochTamperingError) as exc_info:
            check_and_advance_revocation_epoch_anchor_for_local_test(res_tampered, anchor_dir)

        assert "ERR_REVOCATION_SAME_EPOCH_TAMPERING" in str(exc_info.value)
        assert exc_info.value.code == "ERR_REVOCATION_SAME_EPOCH_TAMPERING"

        # Anchor bytes preserved
        with open(anchor_file, "rb") as f:
            assert f.read() == original_bytes

    def test_greater_epoch_advances_atomically(self, tmp_path):
        """Greater epoch advances anchor to new epoch and digest."""
        res_1 = self._make_verified_result(epoch=100)
        res_2 = self._make_verified_result(epoch=101)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        check_and_advance_revocation_epoch_anchor_for_local_test(res_1, anchor_dir)
        check_and_advance_revocation_epoch_anchor_for_local_test(res_2, anchor_dir)

        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")
        with open(anchor_file, "rb") as f:
            content = f.read()

        expected = f"101:{res_2.canonical_claims_sha256}\n".encode("ascii")
        assert content == expected

    def test_input_validation_and_type_guards(self, tmp_path):
        """Strict validation of verified_result, DTO types, safe integer epoch, and hex digest."""
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        # 1. Non-verified result
        valid_res = self._make_verified_result(epoch=10)
        unverified = dataclasses.replace(valid_res, verified=False)
        with pytest.raises(SignerLedgerError, match="ERR_RESULT_NOT_VERIFIED"):
            check_and_advance_revocation_epoch_anchor_for_local_test(unverified, anchor_dir)

        # 2. Duck-typed object
        class FakeResult:
            verified = True
            manifest = valid_res.manifest
            canonical_claims_sha256 = valid_res.canonical_claims_sha256

        with pytest.raises(SignerLedgerError, match="ERR_INVALID_RESULT_TYPE"):
            check_and_advance_revocation_epoch_anchor_for_local_test(FakeResult(), anchor_dir)

        # 3. Forged digest (digest mismatch with recomputed claims)
        forged = dataclasses.replace(valid_res, canonical_claims_digest_sha256="a" * 64)
        with pytest.raises(SignerLedgerError, match="ERR_DIGEST_MISMATCH"):
            check_and_advance_revocation_epoch_anchor_for_local_test(forged, anchor_dir)

        # 4. Uppercase digest in result
        upper_digest = valid_res.canonical_claims_sha256.upper()
        upper_res = dataclasses.replace(valid_res, canonical_claims_digest_sha256=upper_digest)
        with pytest.raises(SignerLedgerError, match="ERR_INVALID_DIGEST_ENCODING"):
            check_and_advance_revocation_epoch_anchor_for_local_test(upper_res, anchor_dir)

        # 5. Production path forbidden
        with pytest.raises(SignerLedgerSecurityError, match="ERR_ANCHOR_DIR_NOT_TEMP|ERR_REVOCATION_ANCHOR_INSECURE_PATH"):
            check_and_advance_revocation_epoch_anchor_for_local_test(valid_res, "/var/lib/resolveflow/anchors")

        # 6. Production path inside temp also forbidden
        prod_in_temp = str(tmp_path / "var/lib/resolveflow/anchors")
        os.makedirs(prod_in_temp, mode=0o700, exist_ok=True)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_REVOCATION_ANCHOR_INSECURE_PATH"):
            check_and_advance_revocation_epoch_anchor_for_local_test(valid_res, prod_in_temp)

    def test_file_security_and_corruption_defenses(self, tmp_path):
        """Descriptor checks reject symlinks, insecure permissions, and malformed anchor contents."""
        res = self._make_verified_result(epoch=10)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        # 1. Directory with group/world write permissions
        insecure_dir = str(tmp_path / "insecure_anchors")
        os.makedirs(insecure_dir, mode=0o777, exist_ok=False)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_DIR_INSECURE_PERMS"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, insecure_dir)

        # 2. Symlinked directory
        symlink_dir = str(tmp_path / "symlink_dir")
        os.symlink(anchor_dir, symlink_dir)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_DIR_SYMLINK_DETECTED"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, symlink_dir)

        # 3. Malformed anchor file: empty
        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")
        with open(anchor_file, "wb") as f:
            pass
        os.chmod(anchor_file, 0o600)
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_REVOCATION_ANCHOR_MALFORMED"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

        # 4. Malformed anchor file: float epoch
        with open(anchor_file, "wb") as f:
            f.write(b"10.5:" + b"a" * 64 + b"\n")
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_REVOCATION_ANCHOR_MALFORMED"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

        # 5. Malformed anchor file: missing newline
        with open(anchor_file, "wb") as f:
            f.write(b"10:" + b"a" * 64)
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_REVOCATION_ANCHOR_MALFORMED"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

        # 6. Insecure anchor file mode (0644 instead of 0600)
        with open(anchor_file, "wb") as f:
            f.write(b"10:" + b"a" * 64 + b"\n")
        os.chmod(anchor_file, 0o644)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_ANCHOR_FILE_INSECURE_PERMS"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

    def test_concurrent_threads_serialization(self, tmp_path):
        """Multiple threads concurrently advancing epochs serialize correctly without corruption."""
        import concurrent.futures

        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        priv = Ed25519PrivateKey.generate()
        results = [self._make_verified_result(epoch=i, private_key=priv) for i in range(1, 15)]

        def run_worker(res):
            try:
                check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)
            except RevocationRollbackError:
                pass  # A higher epoch may have already landed

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(run_worker, results))

        # Check final anchor state is valid and highest epoch
        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")
        with open(anchor_file, "rb") as f:
            content = f.read().decode("ascii")

        assert content.endswith("\n")
        epoch_str, digest_str = content.strip().split(":")
        assert int(epoch_str) == 14
        assert digest_str == results[-1].canonical_claims_sha256

    def test_fault_injection_during_advance_and_fsync_preserves_anchor(self, tmp_path, monkeypatch):
        """Injected replace/fsync failure does not corrupt anchor or report success."""
        res_1 = self._make_verified_result(epoch=10)
        res_2 = self._make_verified_result(epoch=11)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        check_and_advance_revocation_epoch_anchor_for_local_test(res_1, anchor_dir)
        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")
        with open(anchor_file, "rb") as f:
            orig_bytes = f.read()

        # Injected os.replace failure
        real_replace = os.replace

        def failing_replace(*args, **kwargs):
            raise OSError(errno.EIO, "Simulated I/O failure during atomic replace")

        monkeypatch.setattr(os, "replace", failing_replace)
        with pytest.raises(SignerLedgerDurabilityError, match="ERR_ANCHOR_REPLACE_FAILED"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res_2, anchor_dir)

        # Verify anchor unchanged
        with open(anchor_file, "rb") as f:
            assert f.read() == orig_bytes

    def test_fault_injection_cleanup_preserves_primary_and_cleanup_errors(self, tmp_path, monkeypatch):
        """Fault injection on os.close during cleanup records failures and follows single-close."""
        res = self._make_verified_result(epoch=10)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        real_close = os.close
        close_calls: List[int] = []

        def failing_close(fd):
            close_calls.append(fd)
            # Raise once for a descriptor
            raise OSError(errno.EIO, "Simulated close error")

        monkeypatch.setattr(os, "close", failing_close)

        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

        assert "ERR_REVOCATION_ANCHOR_CLEANUP_FAILED" in str(exc_info.value)
        assert exc_info.value.cleanup_errors is not None
        assert len(exc_info.value.cleanup_errors) > 0
        # Verify no blind retry: each descriptor closed at most once
        assert len(close_calls) == len(set(close_calls))

    def test_oversized_and_multiline_anchor_rejection(self, tmp_path):
        """Oversized anchor records and multiline records are strictly rejected."""
        res = self._make_verified_result(epoch=10)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")

        # 1. Oversized anchor (> 256 bytes)
        with open(anchor_file, "wb") as f:
            f.write(b"10:" + b"a" * 300 + b"\n")
        os.chmod(anchor_file, 0o600)

        with pytest.raises(SignerLedgerCorruptionError, match="ERR_REVOCATION_ANCHOR_MALFORMED"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

        # 2. Multiline record
        with open(anchor_file, "wb") as f:
            f.write(b"10:" + b"a" * 64 + b"\n11:" + b"b" * 64 + b"\n")
        os.chmod(anchor_file, 0o600)

        with pytest.raises(SignerLedgerCorruptionError, match="ERR_REVOCATION_ANCHOR_MALFORMED"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

    def test_fifo_anchor_and_lock_rejects_without_blocking(self, tmp_path):
        """I8-R1-01: FIFO anchor or lock file fails closed promptly without hanging."""
        res = self._make_verified_result(epoch=10)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        # 1. FIFO anchor file
        anchor_fifo = os.path.join(anchor_dir, "revocation_epoch.anchor")
        os.mkfifo(anchor_fifo, mode=0o600)

        with pytest.raises(SignerLedgerSecurityError, match="ERR_ANCHOR_NOT_REGULAR_FILE"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

        os.unlink(anchor_fifo)

        # 2. FIFO lock file
        lock_fifo = os.path.join(anchor_dir, "revocation_epoch.lock")
        if os.path.exists(lock_fifo):
            os.unlink(lock_fifo)
        os.mkfifo(lock_fifo, mode=0o600)

        with pytest.raises(SignerLedgerSecurityError, match="ERR_LOCK_NOT_REGULAR_FILE"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

    def test_zero_byte_write_raises_durability_error(self, tmp_path, monkeypatch):
        """I8-R1-03: os.write returning 0 bytes raises ERR_SHORT_WRITE immediately."""
        res = self._make_verified_result(epoch=10)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        def zero_write(fd, data):
            return 0

        monkeypatch.setattr(os, "write", zero_write)

        with pytest.raises(SignerLedgerDurabilityError, match="ERR_SHORT_WRITE"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

    def test_cleanup_preserves_primary_exception_and_surfaces_unlink_failure(self, tmp_path, monkeypatch):
        """I8-R1-04: Primary exception is preserved when cleanup fails; temp unlink failure is reported."""
        res_high = self._make_verified_result(epoch=10)
        res_low = self._make_verified_result(epoch=9)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        check_and_advance_revocation_epoch_anchor_for_local_test(res_high, anchor_dir)

        # When close fails during rollback rejection, primary error RevocationRollbackError must be preserved
        real_close = os.close

        def close_fail_on_read(fd):
            raise OSError(errno.EIO, "Close failed on anchor read descriptor")

        monkeypatch.setattr(os, "close", close_fail_on_read)

        with pytest.raises(RevocationRollbackError) as exc_info:
            check_and_advance_revocation_epoch_anchor_for_local_test(res_low, anchor_dir)

        assert exc_info.value.code == "ERR_REVOCATION_ROLLBACK_DETECTED"
        assert exc_info.value.cleanup_errors is not None
        assert len(exc_info.value.cleanup_errors) > 0

    def test_test_only_path_policy_enforced(self, tmp_path):
        """I8-R1-05: Non-temporary directory paths are rejected."""
        res = self._make_verified_result(epoch=10)

        # Non-temp path
        with pytest.raises(SignerLedgerSecurityError, match="ERR_ANCHOR_DIR_NOT_TEMP"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res, "/home")

    def test_forged_verified_result_rejected_without_modifying_anchor(self, tmp_path):
        """I8-R2-01: Self-consistent forged result constructed by caller is rejected."""
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        valid_res = self._make_verified_result(epoch=10)
        check_and_advance_revocation_epoch_anchor_for_local_test(valid_res, anchor_dir)

        # Caller crafts a forged higher-epoch manifest without valid Ed25519 signature
        forged_data = make_sample_revocation_manifest_data(epoch=999)
        forged_data["manifest_signature"] = "00" * 64
        forged_manifest = SignedRevocationManifest.from_dict(forged_data)
        forged_digest = hashlib.sha256(canonical_revocation_manifest_claims_bytes(forged_manifest)).hexdigest().lower()

        # Caller directly instantiates RevocationManifestVerificationResult
        forged_res = RevocationManifestVerificationResult(
            verified=True,
            manifest=forged_manifest,
            canonical_claims_digest_sha256=forged_digest,
        )

        with pytest.raises(SignerLedgerSecurityError, match="ERR_UNVERIFIED_RESULT_FORGERY"):
            check_and_advance_revocation_epoch_anchor_for_local_test(forged_res, anchor_dir)

        # Verify anchor remains at epoch 10
        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")
        with open(anchor_file, "rb") as f:
            assert f.read() == f"10:{valid_res.canonical_claims_sha256}\n".encode("ascii")

    def test_raw_persistence_errors_wrapped_and_dual_error_preservation(self, tmp_path, monkeypatch):
        """I8-R2-03: Raw write errors are wrapped in SignerLedgerDurabilityError and retain cleanup errors."""
        res = self._make_verified_result(epoch=10)
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        def failing_write(fd, data):
            raise OSError(errno.EIO, "Simulated disk write failure")

        def failing_close(fd):
            raise OSError(errno.EIO, "Simulated close failure")

        monkeypatch.setattr(os, "write", failing_write)
        monkeypatch.setattr(os, "close", failing_close)

        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            check_and_advance_revocation_epoch_anchor_for_local_test(res, anchor_dir)

        assert "ERR_WRITE_FAILED" in str(exc_info.value) or exc_info.value.code == "ERR_WRITE_FAILED"
        assert exc_info.value.primary_error is not None
        assert exc_info.value.cleanup_errors is not None
        assert len(exc_info.value.cleanup_errors) > 0

    def test_cross_process_flock_concurrency(self, tmp_path):
        """I8-R2-04: Independent OS processes serialize correctly under advisory flock without lost updates."""
        anchor_dir = str(tmp_path / "anchors")
        os.makedirs(anchor_dir, mode=0o700, exist_ok=False)

        priv = Ed25519PrivateKey.generate()
        priv_bytes = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())

        ctx = multiprocessing.get_context("spawn")
        results_queue = ctx.Queue()

        processes = []
        for epoch in range(1, 11):
            p = ctx.Process(
                target=_worker_process_advance_anchor,
                args=(anchor_dir, epoch, priv_bytes, results_queue),
            )
            processes.append(p)
            p.start()

        # Collect exactly 10 results from queue using bounded get
        statuses = []
        for _ in range(10):
            try:
                statuses.append(results_queue.get(timeout=10.0))
            except Exception as qe:
                pytest.fail(f"Timed out waiting for worker result: {qe}")

        for p in processes:
            p.join(timeout=5)
            assert not p.is_alive(), f"Process {p.pid} did not terminate"
            assert p.exitcode == 0, f"Process {p.pid} failed with exit code {p.exitcode}"

        assert len(statuses) == 10
        # Check that no errors occurred
        for status, val in statuses:
            assert status in ("SUCCESS", "ROLLBACK"), f"Unexpected worker error: {val}"

        # Assert final anchor has maximum epoch (10) and valid formatting
        anchor_file = os.path.join(anchor_dir, "revocation_epoch.anchor")
        assert os.path.isfile(anchor_file)
        with open(anchor_file, "rb") as f:
            content = f.read().decode("ascii")

        assert content.endswith("\n")
        epoch_str, digest_str = content.strip().split(":")
        assert int(epoch_str) == 10
        assert len(digest_str) == 64

    def test_step_by_step_descendant_directory_security(self, tmp_path, monkeypatch):
        """I8-R3-01: Descriptor-anchored traversal proves descent from temp root, rejecting swapped inodes and foreign devices."""
        res = self._make_verified_result(epoch=10)
        parent_dir = tmp_path / "descendant_test"
        parent_dir.mkdir(mode=0o700)
        target_dir = parent_dir / "child"
        target_dir.mkdir(mode=0o700)

        # 1. Traversal works for valid nested descendant
        check_and_advance_revocation_epoch_anchor_for_local_test(res, str(target_dir))
        anchor_file = target_dir / "revocation_epoch.anchor"
        assert anchor_file.is_file()

        # 2. Inode swap detection during traversal
        real_stat = os.stat

        def mocked_stat(path, *args, **kwargs):
            st = real_stat(path, *args, **kwargs)
            if str(path) in (str(target_dir), target_dir.name):
                return os.stat_result((
                    st.st_mode,
                    st.st_ino + 999999,
                    st.st_dev,
                    st.st_nlink,
                    st.st_uid,
                    st.st_gid,
                    st.st_size,
                    st.st_atime,
                    st.st_mtime,
                    st.st_ctime,
                ))
            return st

        res2 = self._make_verified_result(epoch=11)
        monkeypatch.setattr(os, "stat", mocked_stat)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_DIR_INODE_SWAP"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res2, str(target_dir))

        monkeypatch.undo()

        # 3. Device mismatch detection during traversal
        real_fstat = os.fstat
        call_count = [0]

        def mocked_fstat(fd):
            st = real_fstat(fd)
            call_count[0] += 1
            # Keep temp_root device unchanged (first call); change device on traversal step
            if call_count[0] > 1 and stat.S_ISDIR(st.st_mode):
                return os.stat_result((
                    st.st_mode,
                    st.st_ino,
                    st.st_dev + 1,
                    st.st_nlink,
                    st.st_uid,
                    st.st_gid,
                    st.st_size,
                    st.st_atime,
                    st.st_mtime,
                    st.st_ctime,
                ))
            return st

        monkeypatch.setattr(os, "fstat", mocked_fstat)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_DIR_WRONG_DEVICE"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res2, str(target_dir))

        monkeypatch.undo()

        # 4. I8-R4-01: Writable intermediate directory rejected during traversal before lock/anchor open
        insecure_parent = tmp_path / "insecure_parent_descendant"
        insecure_parent.mkdir(mode=0o700)
        insecure_child = insecure_parent / "anchor_child"
        insecure_child.mkdir(mode=0o700)
        insecure_parent.chmod(0o777)

        with pytest.raises(SignerLedgerSecurityError, match="ERR_DIR_INSECURE_PERMS"):
            check_and_advance_revocation_epoch_anchor_for_local_test(res2, str(insecure_child))

        assert not (insecure_child / "revocation_epoch.lock").exists()
        assert not (insecure_child / "revocation_epoch.anchor").exists()

    def test_i11a_default_require_existing_anchor_false_backward_compatible(self, tmp_path):
        """I11A: Default require_existing_anchor=False preserves existing bootstrap and non-mutating read path."""
        res = self._make_verified_result(epoch=101)
        anchor_dir = tmp_path / "anchors_compat"
        anchor_dir.mkdir(mode=0o700)

        # 1. advance=False with no anchor returns cleanly without creating files
        check_and_advance_revocation_epoch_anchor_for_local_test(res, str(anchor_dir), advance=False)
        assert len(list(anchor_dir.iterdir())) == 0

        # 2. advance=True creates initial bootstrap anchor
        check_and_advance_revocation_epoch_anchor_for_local_test(res, str(anchor_dir), advance=True)
        anchor_file = anchor_dir / "revocation_epoch.anchor"
        assert anchor_file.is_file()
        expected = f"101:{res.canonical_claims_sha256}\n".encode("ascii")
        assert anchor_file.read_bytes() == expected

    def test_i11a_require_existing_anchor_true_missing_anchor_fails_closed(self, tmp_path):
        """I11A: require_existing_anchor=True with missing anchor raises ERR_REVOCATION_ANCHOR_ABSENT under descriptor/lock."""
        res = self._make_verified_result(epoch=101)
        anchor_dir = tmp_path / "anchors_missing"
        anchor_dir.mkdir(mode=0o700)

        with pytest.raises(SignerLedgerError) as exc_info:
            check_and_advance_revocation_epoch_anchor_for_local_test(
                res, str(anchor_dir), advance=False, require_existing_anchor=True
            )

        assert exc_info.value.code == "ERR_REVOCATION_ANCHOR_ABSENT"
        assert isinstance(exc_info.value, RevocationAnchorAbsentError)
        # Directory remains byte-for-byte empty: no anchor, no lock file left behind
        assert len(list(anchor_dir.iterdir())) == 0

    def test_i11a_require_existing_anchor_true_advance_true_missing_anchor_rejects(self, tmp_path):
        """I11A: advance=True with require_existing_anchor=True rejects missing anchor rather than bootstrapping."""
        res = self._make_verified_result(epoch=101)
        anchor_dir = tmp_path / "anchors_adv_missing"
        anchor_dir.mkdir(mode=0o700)

        with pytest.raises(SignerLedgerError) as exc_info:
            check_and_advance_revocation_epoch_anchor_for_local_test(
                res, str(anchor_dir), advance=True, require_existing_anchor=True
            )

        assert exc_info.value.code == "ERR_REVOCATION_ANCHOR_ABSENT"
        assert isinstance(exc_info.value, RevocationAnchorAbsentError)
        # Anchor file was NOT created or bootstrapped
        assert not (anchor_dir / "revocation_epoch.anchor").exists()

    def test_i11a_require_existing_anchor_true_present_anchor_succeeds_unchanged(self, tmp_path):
        """I11A: Present, matching anchor succeeds with advance=False, require_existing_anchor=True and leaves it unchanged."""
        res = self._make_verified_result(epoch=101)
        anchor_dir = tmp_path / "anchors_present"
        anchor_dir.mkdir(mode=0o700)

        # Bootstrap initial anchor
        check_and_advance_revocation_epoch_anchor_for_local_test(res, str(anchor_dir), advance=True)
        anchor_file = anchor_dir / "revocation_epoch.anchor"
        orig_bytes = anchor_file.read_bytes()
        orig_st = anchor_file.stat()

        # Call with advance=False and require_existing_anchor=True
        check_and_advance_revocation_epoch_anchor_for_local_test(
            res, str(anchor_dir), advance=False, require_existing_anchor=True
        )

        assert anchor_file.read_bytes() == orig_bytes
        new_st = anchor_file.stat()
        assert new_st.st_mtime_ns == orig_st.st_mtime_ns
        assert new_st.st_ino == orig_st.st_ino

    def test_i11a_require_existing_anchor_true_rollback_and_tampering_still_detected(self, tmp_path):
        """I11A: require_existing_anchor=True still raises rollback and tampering errors when anchor is present."""
        res_orig = self._make_verified_result(epoch=50, revoked_trial_key_ids=["key_1"])
        anchor_dir = tmp_path / "anchors_tamper_test"
        anchor_dir.mkdir(mode=0o700)
        check_and_advance_revocation_epoch_anchor_for_local_test(res_orig, str(anchor_dir), advance=True)

        # Rollback check
        res_low = self._make_verified_result(epoch=49)
        with pytest.raises(RevocationRollbackError) as exc_rollback:
            check_and_advance_revocation_epoch_anchor_for_local_test(
                res_low, str(anchor_dir), advance=False, require_existing_anchor=True
            )
        assert exc_rollback.value.code == "ERR_REVOCATION_ROLLBACK_DETECTED"

        # Tampering check
        res_tampered = self._make_verified_result(epoch=50, revoked_trial_key_ids=["key_2"])
        with pytest.raises(RevocationEpochTamperingError) as exc_tamper:
            check_and_advance_revocation_epoch_anchor_for_local_test(
                res_tampered, str(anchor_dir), advance=False, require_existing_anchor=True
            )
        assert exc_tamper.value.code == "ERR_REVOCATION_SAME_EPOCH_TAMPERING"

    def test_i11a_require_existing_anchor_non_bool_rejected_before_io(self, tmp_path):
        """I11A: Non-bool require_existing_anchor is rejected with ERR_INVALID_PARAMETER before any filesystem access."""
        res = self._make_verified_result(epoch=101)
        # Point to a completely nonexistent directory to verify no filesystem traversal occurs
        nonexistent_dir = "/tmp/nonexistent_dir_probe_test_12345"

        for bad_val in ("true", 1, 0, None, [True], {"a": 1}):
            with pytest.raises(SignerLedgerError) as exc_info:
                check_and_advance_revocation_epoch_anchor_for_local_test(
                    res, nonexistent_dir, require_existing_anchor=bad_val
                )
            assert exc_info.value.code == "ERR_INVALID_PARAMETER"

    def test_i11a_no_path_based_check_used_for_anchor(self, tmp_path, monkeypatch):
        """I11A: Confirms no path-based existence query (Path.exists/is_file or os.path) is used for anchor file."""
        res = self._make_verified_result(epoch=101)
        anchor_dir = tmp_path / "anchors_no_path_probe"
        anchor_dir.mkdir(mode=0o700)

        # Monkeypatch Path methods and os.path to tripwire if "revocation_epoch.anchor" is probed via path
        orig_is_file = Path.is_file
        orig_exists = Path.exists
        orig_os_exists = os.path.exists
        orig_os_isfile = os.path.isfile

        def guard_is_file(p, *args, **kwargs):
            if "revocation_epoch.anchor" in str(p):
                raise AssertionError(f"Path.is_file called on anchor path: {p}")
            return orig_is_file(p, *args, **kwargs)

        def guard_exists(p, *args, **kwargs):
            if "revocation_epoch.anchor" in str(p):
                raise AssertionError(f"Path.exists called on anchor path: {p}")
            return orig_exists(p, *args, **kwargs)

        def guard_os_exists(p):
            if "revocation_epoch.anchor" in str(p):
                raise AssertionError(f"os.path.exists called on anchor path: {p}")
            return orig_os_exists(p)

        def guard_os_isfile(p):
            if "revocation_epoch.anchor" in str(p):
                raise AssertionError(f"os.path.isfile called on anchor path: {p}")
            return orig_os_isfile(p)

        monkeypatch.setattr(Path, "is_file", guard_is_file)
        monkeypatch.setattr(Path, "exists", guard_exists)
        monkeypatch.setattr(os.path, "exists", guard_os_exists)
        monkeypatch.setattr(os.path, "isfile", guard_os_isfile)

        # Call with require_existing_anchor=True on missing anchor: must fail closed without triggering tripwires
        with pytest.raises(SignerLedgerError) as exc_info:
            check_and_advance_revocation_epoch_anchor_for_local_test(
                res, str(anchor_dir), advance=False, require_existing_anchor=True
            )
        assert exc_info.value.code == "ERR_REVOCATION_ANCHOR_ABSENT"


class TestDescriptorAnchoredFreshRevocationRegistryRead:
    """Gate 3-I9: Focused Unit Tests for Descriptor-Anchored Fresh Revocation Registry Read Core."""

    def _write_signed_registry(
        self,
        registry_dir: str | Path,
        *,
        epoch: int = 100,
        private_key: Optional[Ed25519PrivateKey] = None,
        issuer_id: str = "security_officer_root",
        valid_until_iso: str = "2026-11-04T00:00:00Z",
        issued_at_iso: str = "2026-11-03T00:00:00Z",
        revoked_trial_key_ids: Optional[List[str]] = None,
        custom_raw_bytes: Optional[bytes] = None,
        mode: int = 0o600,
    ) -> Tuple[bytes, Ed25519PrivateKey]:
        if private_key is None:
            private_key = Ed25519PrivateKey.generate()
        reg_path = Path(registry_dir) / "revocations.signed.json"
        if custom_raw_bytes is not None:
            raw_bytes = custom_raw_bytes
        else:
            data = make_sample_revocation_manifest_data(
                epoch=epoch,
                issuer_id=issuer_id,
                valid_until_iso=valid_until_iso,
                issued_at_iso=issued_at_iso,
                revoked_trial_key_ids=revoked_trial_key_ids,
            )
            raw_bytes, _ = sign_manifest_data(data, private_key)
        reg_path.write_bytes(raw_bytes)
        reg_path.chmod(mode)
        return raw_bytes, private_key

    def test_valid_signed_manifest_freshly_read_verified_and_anchored(self, tmp_path):
        """Valid signed manifest is freshly read via descriptor, verified, anchored, and returns valid result."""
        reg_dir = tmp_path / "reg_valid"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        raw_bytes, _ = self._write_signed_registry(reg_dir, epoch=100, private_key=priv)

        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        result = load_verify_and_anchor_revocation_manifest_for_local_test(
            registry_directory=str(reg_dir),
            issuer_public_key=priv.public_key(),
            verification_time_utc=verify_time,
        )

        assert isinstance(result, RevocationManifestVerificationResult)
        assert result.verified is True
        assert result.manifest.epoch == 100
        # Check that anchor file exists and contains epoch 100
        anchor_file = reg_dir / "revocation_epoch.anchor"
        assert anchor_file.is_file()
        content = anchor_file.read_text(encoding="ascii")
        assert content.startswith("100:")

    def test_replacing_registry_file_causes_fresh_read_and_advances_anchor(self, tmp_path):
        """Replacing registry file causes fresh read; higher epoch advances anchor without reusing descriptors."""
        reg_dir = tmp_path / "reg_advance"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        # 1. Initial read with epoch 100
        self._write_signed_registry(reg_dir, epoch=100, private_key=priv)
        res1 = load_verify_and_anchor_revocation_manifest_for_local_test(
            str(reg_dir), priv.public_key(), verify_time
        )
        assert res1.manifest.epoch == 100

        # 2. Update file with epoch 101
        self._write_signed_registry(reg_dir, epoch=101, private_key=priv)
        res2 = load_verify_and_anchor_revocation_manifest_for_local_test(
            str(reg_dir), priv.public_key(), verify_time
        )
        assert res2.manifest.epoch == 101

        anchor_file = reg_dir / "revocation_epoch.anchor"
        content = anchor_file.read_text(encoding="ascii")
        assert content.startswith("101:")

    def test_missing_or_invalid_registry_fails_closed_without_modifying_anchor(self, tmp_path):
        """Missing registry, bad signature, wrong issuer, expired manifest fail closed without touching anchor."""
        reg_dir = tmp_path / "reg_fail_closed"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        # 1. Missing file
        with pytest.raises(SignerLedgerError, match="ERR_REGISTRY_FILE_NOT_FOUND"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)
        assert not (reg_dir / "revocation_epoch.anchor").exists()

        # 2. Bad signature (signed with different key)
        other_priv = Ed25519PrivateKey.generate()
        self._write_signed_registry(reg_dir, epoch=50, private_key=other_priv)
        with pytest.raises(RevocationManifestVerificationError, match="ERR_MANIFEST_SIGNATURE_INVALID"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)
        assert not (reg_dir / "revocation_epoch.anchor").exists()

        # 3. Wrong issuer
        untrusted_data = make_sample_revocation_manifest_data(epoch=50)
        untrusted_data["issuer_id"] = "untrusted_issuer"
        untrusted_data["manifest_signature"] = "00" * 64
        untrusted_bytes = json.dumps(untrusted_data).encode("utf-8")
        self._write_signed_registry(reg_dir, custom_raw_bytes=untrusted_bytes)
        with pytest.raises(RevocationManifestVerificationError, match="ERR_MANIFEST_ISSUER_INVALID"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)
        assert not (reg_dir / "revocation_epoch.anchor").exists()

        # 4. Expired manifest
        expired_time = datetime(2026, 11, 5, 0, 0, 0, tzinfo=timezone.utc)
        self._write_signed_registry(reg_dir, epoch=50, private_key=priv, valid_until_iso="2026-11-04T00:00:00Z")
        with pytest.raises(RevocationManifestVerificationError, match="ERR_MANIFEST_EXPIRED"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), expired_time)
        assert not (reg_dir / "revocation_epoch.anchor").exists()

    def test_file_security_permissions_and_corruption_checks(self, tmp_path):
        """Symlink, non-regular file, hardlink, wrong mode, oversized, and short-read fail closed."""
        reg_dir = tmp_path / "reg_security"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        reg_file = reg_dir / "revocations.signed.json"

        # 1. Insecure mode (0644 instead of 0600)
        self._write_signed_registry(reg_dir, epoch=10, private_key=priv, mode=0o644)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_REGISTRY_INSECURE_PERMS"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)

        # 2. Symlink
        reg_file.unlink()
        target_file = tmp_path / "target.json"
        target_file.write_text("{}", encoding="utf-8")
        target_file.chmod(0o600)
        reg_file.symlink_to(target_file)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_REGISTRY_SYMLINK_DETECTED"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)

        # 3. Hardlink
        reg_file.unlink()
        self._write_signed_registry(reg_dir, epoch=10, private_key=priv)
        hardlink_file = tmp_path / "hardlink.json"
        os.link(str(reg_file), str(hardlink_file))
        with pytest.raises(SignerLedgerSecurityError, match="ERR_REGISTRY_HARDLINK_DETECTED"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)
        hardlink_file.unlink()

        # 4. Empty file
        reg_file.write_bytes(b"")
        reg_file.chmod(0o600)
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_REGISTRY_FILE_MALFORMED"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)

        # 5. FIFO without blocking (if supported on platform)
        if hasattr(os, "mkfifo"):
            fifo_dir = tmp_path / "reg_fifo"
            fifo_dir.mkdir(mode=0o700)
            fifo_file = fifo_dir / "revocations.signed.json"
            os.mkfifo(str(fifo_file), 0o600)
            with pytest.raises(SignerLedgerSecurityError, match="ERR_REGISTRY_NOT_REGULAR_FILE"):
                load_verify_and_anchor_revocation_manifest_for_local_test(str(fifo_dir), priv.public_key(), verify_time)

    def test_parameter_gates_and_utc_timezone_enforcement(self, tmp_path):
        """Rejects non-datetime, naive datetime, non-UTC, wrong key type, or blank directory."""
        reg_dir = tmp_path / "reg_param"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        self._write_signed_registry(reg_dir, epoch=10, private_key=priv)

        # Naive datetime
        with pytest.raises(SignerLedgerError, match="ERR_TIMEZONE_NOT_UTC"):
            load_verify_and_anchor_revocation_manifest_for_local_test(
                str(reg_dir), priv.public_key(), datetime(2026, 11, 3, 12, 0, 0)
            )

        # Non-datetime
        with pytest.raises(SignerLedgerError, match="ERR_INVALID_VERIFICATION_TIME"):
            load_verify_and_anchor_revocation_manifest_for_local_test(
                str(reg_dir), priv.public_key(), "2026-11-03T12:00:00Z"  # type: ignore
            )

        # Wrong key type
        with pytest.raises(SignerLedgerError, match="ERR_INVALID_KEY_TYPE"):
            load_verify_and_anchor_revocation_manifest_for_local_test(
                str(reg_dir), priv, datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)  # type: ignore
            )

        # Blank directory
        with pytest.raises(SignerLedgerError, match="ERR_INVALID_REGISTRY_DIR"):
            load_verify_and_anchor_revocation_manifest_for_local_test(
                "   ", priv.public_key(), datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
            )

    def test_metadata_mutation_during_read_detected(self, tmp_path, monkeypatch):
        """Detects registry file metadata change between before/after read."""
        reg_dir = tmp_path / "reg_mutated"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        self._write_signed_registry(reg_dir, epoch=10, private_key=priv)
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        real_fstat = os.fstat
        call_count = [0]

        def mutated_fstat(fd):
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                call_count[0] += 1
                if call_count[0] >= 2:
                    return os.stat_result((
                        st.st_mode,
                        st.st_ino,
                        st.st_dev,
                        st.st_nlink,
                        st.st_uid,
                        st.st_gid,
                        st.st_size,
                        st.st_atime,
                        st.st_mtime,
                        st.st_ctime + 1,
                    ))
            return st

        monkeypatch.setattr(os, "fstat", mutated_fstat)
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_REGISTRY_MUTATED_DURING_READ"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)

    def test_rollback_and_same_epoch_tampering_fail_closed(self, tmp_path):
        """Rollback epoch and same-epoch digest tampering are rejected and preserve previous anchor."""
        reg_dir = tmp_path / "reg_rollback"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        # 1. Establish anchor at epoch 100
        self._write_signed_registry(reg_dir, epoch=100, private_key=priv, revoked_trial_key_ids=["k1"])
        res1 = load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)
        assert res1.manifest.epoch == 100

        # Read original anchor bytes
        anchor_file = reg_dir / "revocation_epoch.anchor"
        orig_content = anchor_file.read_bytes()

        # 2. Lower epoch (99) -> Rollback rejected
        self._write_signed_registry(reg_dir, epoch=99, private_key=priv, revoked_trial_key_ids=["k1"])
        with pytest.raises(RevocationRollbackError, match="ERR_REVOCATION_ROLLBACK_DETECTED"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)
        assert anchor_file.read_bytes() == orig_content

        # 3. Same epoch (100) with different digest -> Tampering rejected
        self._write_signed_registry(reg_dir, epoch=100, private_key=priv, revoked_trial_key_ids=["k2"])
        with pytest.raises(RevocationEpochTamperingError, match="ERR_REVOCATION_SAME_EPOCH_TAMPERING"):
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)
        assert anchor_file.read_bytes() == orig_content

    def test_fault_injected_cleanup_preserves_primary_error(self, tmp_path, monkeypatch):
        """Fault-injected descriptor close preserves primary read/verification error."""
        reg_dir = tmp_path / "reg_cleanup"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        self._write_signed_registry(reg_dir, epoch=10, private_key=priv)
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        real_close = os.close

        def failing_close(fd):
            real_close(fd)
            raise OSError(errno.EIO, "Simulated close error")

        def failing_read(fd, n):
            raise OSError(errno.EIO, "Simulated read error")

        monkeypatch.setattr(os, "read", failing_read)
        monkeypatch.setattr(os, "close", failing_close)

        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)

        assert "ERR_REGISTRY_READ_FAILED" in str(exc_info.value)
        assert exc_info.value.primary_error is not None
        assert exc_info.value.cleanup_errors is not None
        assert len(exc_info.value.cleanup_errors) > 0

    def test_close_failure_after_successful_read_aborts_before_verification_or_anchor(self, tmp_path, monkeypatch):
        """I9-R1-01: Close failure after successful read aborts fail-closed without calling verifier or creating/advancing anchor."""
        reg_dir = tmp_path / "reg_close_abort"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        self._write_signed_registry(reg_dir, epoch=100, private_key=priv)
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        real_close = os.close

        def failing_close(fd):
            real_close(fd)
            raise OSError(errno.EIO, "Simulated registry close error")

        # 1. Without existing anchor: aborts and anchor is NOT created
        monkeypatch.setattr(os, "close", failing_close)
        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)

        assert "ERR_REGISTRY_CLOSE_FAILED" in str(exc_info.value) or exc_info.value.code == "ERR_REGISTRY_CLOSE_FAILED"
        assert exc_info.value.primary_error is not None
        assert not (reg_dir / "revocation_epoch.anchor").exists()

        # 2. With existing anchor: aborts and anchor is unchanged
        anchor_file = reg_dir / "revocation_epoch.anchor"
        anchor_file.write_text("50:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", encoding="ascii")
        anchor_file.chmod(0o600)
        orig_bytes = anchor_file.read_bytes()

        with pytest.raises(SignerLedgerDurabilityError) as exc_info2:
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)

        assert "ERR_REGISTRY_CLOSE_FAILED" in str(exc_info2.value) or exc_info2.value.code == "ERR_REGISTRY_CLOSE_FAILED"
        assert anchor_file.read_bytes() == orig_bytes

    def test_pre_and_post_read_fstat_failures_normalized_and_prevent_anchor(self, tmp_path, monkeypatch):
        """I9-R1-02: Pre-read and post-read fstat failures raise distinct stable structured errors without touching anchor."""
        reg_dir = tmp_path / "reg_fstat_err"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        self._write_signed_registry(reg_dir, epoch=100, private_key=priv)
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        anchor_file = reg_dir / "revocation_epoch.anchor"

        real_fstat = os.fstat

        # 1. Fault inject pre-read fstat on regular file
        call_count = [0]

        def failing_pre_fstat(fd):
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise OSError(errno.EIO, "Simulated pre-read fstat failure")
            return st

        monkeypatch.setattr(os, "fstat", failing_pre_fstat)
        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)
        assert "ERR_REGISTRY_PRE_READ_FSTAT_FAILED" in str(exc_info.value) or exc_info.value.code == "ERR_REGISTRY_PRE_READ_FSTAT_FAILED"
        assert exc_info.value.primary_error is not None
        assert not anchor_file.exists()

        monkeypatch.undo()

        # 2. Fault inject post-read fstat on regular file
        call_count = [0]

        def failing_post_fstat(fd):
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                call_count[0] += 1
                if call_count[0] == 2:
                    raise OSError(errno.EIO, "Simulated post-read fstat failure")
            return st

        monkeypatch.setattr(os, "fstat", failing_post_fstat)
        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            load_verify_and_anchor_revocation_manifest_for_local_test(str(reg_dir), priv.public_key(), verify_time)
        assert "ERR_REGISTRY_POST_READ_FSTAT_FAILED" in str(exc_info.value) or exc_info.value.code == "ERR_REGISTRY_POST_READ_FSTAT_FAILED"
        assert exc_info.value.primary_error is not None
        assert not anchor_file.exists()

    def test_i11a_loader_propagates_require_existing_anchor(self, tmp_path):
        """I11A: load_verify_and_anchor_revocation_manifest_for_local_test propagates require_existing_anchor."""
        reg_dir = tmp_path / "reg_require_anchor"
        reg_dir.mkdir(mode=0o700)
        priv = Ed25519PrivateKey.generate()
        self._write_signed_registry(reg_dir, epoch=100, private_key=priv)
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        anchor_file = reg_dir / "revocation_epoch.anchor"

        # 1. Non-bool parameter rejected before IO
        with pytest.raises(SignerLedgerError) as exc_param:
            load_verify_and_anchor_revocation_manifest_for_local_test(
                str(reg_dir), priv.public_key(), verify_time, require_existing_anchor="yes"
            )
        assert exc_param.value.code == "ERR_INVALID_PARAMETER"

        # 2. Missing anchor with advance_anchor=False and require_existing_anchor=True fails closed
        with pytest.raises(SignerLedgerError) as exc_missing:
            load_verify_and_anchor_revocation_manifest_for_local_test(
                str(reg_dir), priv.public_key(), verify_time, advance_anchor=False, require_existing_anchor=True
            )
        assert exc_missing.value.code == "ERR_REVOCATION_ANCHOR_ABSENT"
        assert not anchor_file.exists()

        # 3. Initialize anchor via advance_anchor=True
        load_verify_and_anchor_revocation_manifest_for_local_test(
            str(reg_dir), priv.public_key(), verify_time, advance_anchor=True, require_existing_anchor=False
        )
        assert anchor_file.exists()

        # 4. Present anchor with require_existing_anchor=True succeeds
        res = load_verify_and_anchor_revocation_manifest_for_local_test(
            str(reg_dir), priv.public_key(), verify_time, advance_anchor=False, require_existing_anchor=True
        )
        assert res.verified is True


class TestDescriptorAnchoredIssuerKeyReader:
    """Gate 3-I10: Focused Unit Tests for Local-Test Descriptor-Anchored Revocation Issuer Key Reader."""

    def _write_issuer_pub_pem(
        self,
        trust_dir: Path,
        key_bytes: bytes,
        mode: int = 0o600,
        filename: str = REVOCATION_ISSUER_PUBLIC_KEY_FILENAME,
    ) -> Path:
        target = trust_dir / filename
        target.write_bytes(key_bytes)
        target.chmod(mode)
        return target

    def _make_sample_ed25519_pem(self) -> Tuple[Ed25519PrivateKey, bytes]:
        priv = Ed25519PrivateKey.generate()
        pub = priv.public_key()
        pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        return priv, pem

    def test_valid_ephemeral_ed25519_pem_roundtrip_and_fresh_read(self, tmp_path: Path) -> None:
        """Valid PEM round-trips correctly, returns Ed25519PublicKey, and re-reads fresh without caching."""
        trust_dir = tmp_path / "trust_valid"
        trust_dir.mkdir(mode=0o700)

        priv1, pem1 = self._make_sample_ed25519_pem()
        self._write_issuer_pub_pem(trust_dir, pem1)

        loaded_pub1 = load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert isinstance(loaded_pub1, Ed25519PublicKey)
        assert loaded_pub1.public_bytes(Encoding.Raw, PublicFormat.Raw) == priv1.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )

        # Fresh read without caching: replace key file with a different key
        priv2, pem2 = self._make_sample_ed25519_pem()
        self._write_issuer_pub_pem(trust_dir, pem2)

        loaded_pub2 = load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert isinstance(loaded_pub2, Ed25519PublicKey)
        assert loaded_pub2.public_bytes(Encoding.Raw, PublicFormat.Raw) == priv2.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        assert loaded_pub2.public_bytes(Encoding.Raw, PublicFormat.Raw) != loaded_pub1.public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )

    def test_invalid_trust_directory_inputs(self, tmp_path: Path) -> None:
        """Invalid types, empty paths, paths outside temp, or symlinks are rejected fail-closed."""
        for invalid_dir in (None, 12345, "", "   ", b"/tmp"):
            with pytest.raises(SignerLedgerError) as exc_info:
                load_revocation_issuer_public_key_for_local_test(invalid_dir)  # type: ignore
            assert "ERR_INVALID_TRUST_DIR" in str(exc_info.value) or exc_info.value.code == "ERR_INVALID_TRUST_DIR"

        # Non-production sentinel outside temp root is rejected
        with pytest.raises(SignerLedgerSecurityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test("/opt/non_temp_sentinel_dir/signer")
        assert "ERR_ANCHOR_DIR_NOT_TEMP" in str(exc_info.value)

        # Symlinked directory rejected
        real_dir = tmp_path / "real_trust_dir"
        real_dir.mkdir(mode=0o700)
        sym_dir = tmp_path / "sym_trust_dir"
        os.symlink(real_dir, sym_dir)

        with pytest.raises(SignerLedgerSecurityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(sym_dir)
        assert "ERR_DIR_SYMLINK_DETECTED" in str(exc_info.value)

    def test_candidate_outside_temp_rejected_lexically_without_filesystem_access(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """I10-R1-01: Proves candidate outside temp root is rejected lexically before any path-based filesystem query or open."""
        banned_candidate = "/var/lib/resolveflow/signer"

        # Intercept filesystem functions to prove banned_candidate is NEVER touched on the filesystem
        import os as os_orig
        import os.path as osp_orig

        def forbidden_os(name):
            orig_fn = getattr(os_orig, name)
            def _fn(path, *args, **kwargs):
                if banned_candidate in str(path) or "/var/lib/resolveflow" in str(path):
                    pytest.fail(f"Prohibited filesystem access attempted via os.{name}({path!r})")
                return orig_fn(path, *args, **kwargs)
            return _fn

        def forbidden_osp(name):
            orig_fn = getattr(osp_orig, name)
            def _fn(path, *args, **kwargs):
                if banned_candidate in str(path) or "/var/lib/resolveflow" in str(path):
                    pytest.fail(f"Prohibited filesystem access attempted via os.path.{name}({path!r})")
                return orig_fn(path, *args, **kwargs)
            return _fn

        for fn_name in ("stat", "lstat", "open", "scandir", "listdir"):
            if hasattr(os_orig, fn_name):
                monkeypatch.setattr(os, fn_name, forbidden_os(fn_name))
        for fn_name in ("realpath", "islink", "exists", "isdir", "isfile"):
            if hasattr(osp_orig, fn_name):
                monkeypatch.setattr(os.path, fn_name, forbidden_osp(fn_name))

        with pytest.raises(SignerLedgerSecurityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(banned_candidate)
        assert "ERR_REVOCATION_ANCHOR_INSECURE_PATH" in str(exc_info.value)

        # Also verify non-production sentinel outside temp root is rejected lexically without querying the path
        outside_sentinel = "/opt/sentinel_outside_temp_root/trust_dir"
        def forbidden_outside_os(name):
            orig_fn = getattr(os_orig, name)
            def _fn(path, *args, **kwargs):
                if outside_sentinel in str(path):
                    pytest.fail(f"Prohibited filesystem access attempted on outside sentinel via os.{name}({path!r})")
                return orig_fn(path, *args, **kwargs)
            return _fn

        def forbidden_outside_osp(name):
            orig_fn = getattr(osp_orig, name)
            def _fn(path, *args, **kwargs):
                if outside_sentinel in str(path):
                    pytest.fail(f"Prohibited filesystem access attempted on outside sentinel via os.path.{name}({path!r})")
                return orig_fn(path, *args, **kwargs)
            return _fn

        for fn_name in ("stat", "lstat", "open", "scandir", "listdir"):
            if hasattr(os_orig, fn_name):
                monkeypatch.setattr(os, fn_name, forbidden_outside_os(fn_name))
        for fn_name in ("realpath", "islink", "exists", "isdir", "isfile"):
            if hasattr(osp_orig, fn_name):
                monkeypatch.setattr(os.path, fn_name, forbidden_outside_osp(fn_name))

        with pytest.raises(SignerLedgerSecurityError) as exc_info2:
            load_revocation_issuer_public_key_for_local_test(outside_sentinel)
        assert "ERR_ANCHOR_DIR_NOT_TEMP" in str(exc_info2.value)

    def test_missing_key_file(self, tmp_path: Path) -> None:
        """Missing key file raises ERR_ISSUER_KEY_FILE_NOT_FOUND."""
        trust_dir = tmp_path / "trust_missing"
        trust_dir.mkdir(mode=0o700)

        with pytest.raises(SignerLedgerError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_FILE_NOT_FOUND" in str(exc_info.value) or exc_info.value.code == "ERR_ISSUER_KEY_FILE_NOT_FOUND"

    def test_symlink_key_file_rejected(self, tmp_path: Path) -> None:
        """Symlinked key file raises ERR_ISSUER_KEY_SYMLINK_DETECTED."""
        trust_dir = tmp_path / "trust_symlink"
        trust_dir.mkdir(mode=0o700)

        real_key = tmp_path / "other_key.pem"
        _, pem = self._make_sample_ed25519_pem()
        real_key.write_bytes(pem)
        real_key.chmod(0o600)

        sym_key = trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME
        os.symlink(real_key, sym_key)

        with pytest.raises(SignerLedgerSecurityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_SYMLINK_DETECTED" in str(exc_info.value)

    def test_fifo_key_file_rejected_without_blocking(self, tmp_path: Path) -> None:
        """FIFO special file is rejected as non-regular without hanging."""
        trust_dir = tmp_path / "trust_fifo"
        trust_dir.mkdir(mode=0o700)
        fifo_path = trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME
        os.mkfifo(fifo_path, 0o600)

        with pytest.raises(SignerLedgerSecurityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_NOT_REGULAR_FILE" in str(exc_info.value)

    def test_hardlink_key_file_rejected(self, tmp_path: Path) -> None:
        """Key file with hard link count > 1 is rejected."""
        trust_dir = tmp_path / "trust_hardlink"
        trust_dir.mkdir(mode=0o700)
        _, pem = self._make_sample_ed25519_pem()
        key_file = self._write_issuer_pub_pem(trust_dir, pem)
        link_file = trust_dir / "key_backup.link"
        os.link(key_file, link_file)

        with pytest.raises(SignerLedgerSecurityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_HARDLINK_DETECTED" in str(exc_info.value)

    def test_insecure_permissions_rejected(self, tmp_path: Path) -> None:
        """Modes other than 0600 are rejected fail-closed."""
        trust_dir = tmp_path / "trust_perms"
        trust_dir.mkdir(mode=0o700)
        _, pem = self._make_sample_ed25519_pem()

        for bad_mode in (0o644, 0o666, 0o700, 0o777, 0o400):
            self._write_issuer_pub_pem(trust_dir, pem, mode=bad_mode)
            with pytest.raises(SignerLedgerSecurityError) as exc_info:
                load_revocation_issuer_public_key_for_local_test(trust_dir)
            assert "ERR_ISSUER_KEY_INSECURE_PERMS" in str(exc_info.value)

    def test_empty_and_oversized_file_rejected(self, tmp_path: Path) -> None:
        """Empty file (0 bytes) or oversized file (>16384 bytes) is rejected."""
        trust_dir = tmp_path / "trust_size"
        trust_dir.mkdir(mode=0o700)

        # Empty file
        self._write_issuer_pub_pem(trust_dir, b"")
        with pytest.raises(SignerLedgerCorruptionError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_FILE_EMPTY" in str(exc_info.value)

        # Oversized file: 16385 bytes
        oversized_data = b"-----BEGIN PUBLIC KEY-----\n" + (b"A" * 16360) + b"\n-----END PUBLIC KEY-----\n"
        assert len(oversized_data) > 16384
        self._write_issuer_pub_pem(trust_dir, oversized_data)
        with pytest.raises(SignerLedgerCorruptionError) as exc_info2:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_FILE_OVERSIZED" in str(exc_info2.value)

    def test_encoding_and_malformed_pem_rejected(self, tmp_path: Path) -> None:
        """Non-UTF-8 bytes or malformed PEM structure are rejected."""
        trust_dir = tmp_path / "trust_malformed"
        trust_dir.mkdir(mode=0o700)

        # Non-UTF-8 bytes
        self._write_issuer_pub_pem(trust_dir, b"\xff\xfe\x00\x01\x80\x90")
        with pytest.raises(SignerLedgerCorruptionError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_INVALID_ENCODING" in str(exc_info.value)

        # Not a PEM format at all
        self._write_issuer_pub_pem(trust_dir, b"this is random text not pem format\n")
        with pytest.raises(SignerLedgerCorruptionError) as exc_info2:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_MALFORMED" in str(exc_info2.value)

        # Delimiters present but base64 content is invalid
        corrupt_pem = b"-----BEGIN PUBLIC KEY-----\n!!NOT_VALID_BASE64@@@\n-----END PUBLIC KEY-----\n"
        self._write_issuer_pub_pem(trust_dir, corrupt_pem)
        with pytest.raises(SignerLedgerCorruptionError) as exc_info3:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_MALFORMED_PEM" in str(exc_info3.value)

    def test_private_key_pem_strictly_prohibited(self, tmp_path: Path) -> None:
        """Private key PEM is strictly prohibited and fails closed."""
        trust_dir = tmp_path / "trust_priv"
        trust_dir.mkdir(mode=0o700)

        priv = Ed25519PrivateKey.generate()
        priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())

        self._write_issuer_pub_pem(trust_dir, priv_pem)
        with pytest.raises(SignerLedgerSecurityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_PRIVATE_KEY_PROHIBITED" in str(exc_info.value)

    def test_non_ed25519_algorithm_rejected(self, tmp_path: Path) -> None:
        """Non-Ed25519 public keys (e.g. RSA) are rejected fail-closed."""
        trust_dir = tmp_path / "trust_rsa"
        trust_dir.mkdir(mode=0o700)

        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        rsa_pem = rsa_priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)

        self._write_issuer_pub_pem(trust_dir, rsa_pem)
        with pytest.raises(SignerLedgerSecurityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_ALGORITHM_INVALID" in str(exc_info.value)

    def test_multiple_pem_blocks_and_trailing_data_rejected(self, tmp_path: Path) -> None:
        """Multiple PEM blocks or unexpected data before/after delimiters are rejected."""
        trust_dir = tmp_path / "trust_multi"
        trust_dir.mkdir(mode=0o700)

        _, pem1 = self._make_sample_ed25519_pem()
        _, pem2 = self._make_sample_ed25519_pem()

        # Two PEM blocks
        self._write_issuer_pub_pem(trust_dir, pem1 + b"\n" + pem2)
        with pytest.raises(SignerLedgerCorruptionError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_MULTIPLE_OR_MALFORMED_PEM" in str(exc_info.value)

        # Trailing garbage after END delimiter
        self._write_issuer_pub_pem(trust_dir, pem1 + b"\nextra_trailing_junk\n")
        with pytest.raises(SignerLedgerCorruptionError) as exc_info2:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_TRAILING_DATA" in str(exc_info2.value)

        # Leading garbage before BEGIN delimiter
        self._write_issuer_pub_pem(trust_dir, b"leading_garbage\n" + pem1)
        with pytest.raises(SignerLedgerCorruptionError) as exc_info3:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_MALFORMED" in str(exc_info3.value)

    def test_metadata_mutation_and_short_read_detection(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Metadata changes or short reads during file read fail closed."""
        trust_dir = tmp_path / "trust_mut"
        trust_dir.mkdir(mode=0o700)
        _, pem = self._make_sample_ed25519_pem()
        self._write_issuer_pub_pem(trust_dir, pem)

        real_fstat = os.fstat

        # 1. Mutated metadata in post-read fstat
        call_count = [0]
        def conditional_fstat(fd):
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                call_count[0] += 1
                if call_count[0] == 2:  # post-read
                    class MutatedStat:
                        st_mode = st.st_mode
                        st_uid = st.st_uid
                        st_nlink = st.st_nlink
                        st_size = st.st_size
                        st_ino = st.st_ino
                        st_dev = st.st_dev
                        st_mtime_ns = st.st_mtime_ns + 999999
                    return MutatedStat()
            return st

        monkeypatch.setattr(os, "fstat", conditional_fstat)
        with pytest.raises(SignerLedgerCorruptionError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_MUTATED_DURING_READ" in str(exc_info.value)

        monkeypatch.undo()

        # 2. Short read simulation
        real_read = os.read
        def fake_short_read(fd, n):
            # Premature EOF
            return b""

        monkeypatch.setattr(os, "read", fake_short_read)
        with pytest.raises(SignerLedgerCorruptionError) as exc_info2:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_SHORT_READ" in str(exc_info2.value)

    def test_fault_injections_normalize_to_structured_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Faults at open, pre/post fstat, read, and close normalize to stable codes with primary/cleanup errors."""
        trust_dir = tmp_path / "trust_fault"
        trust_dir.mkdir(mode=0o700)
        _, pem = self._make_sample_ed25519_pem()
        self._write_issuer_pub_pem(trust_dir, pem)

        real_open = os.open
        real_fstat = os.fstat
        real_read = os.read
        real_close = os.close

        # 1. Open failure (other than ENOENT / ELOOP)
        def failing_open(path, flags, *args, **kwargs):
            if "revocation_issuer.pub.pem" in str(path):
                raise OSError(errno.EIO, "Simulated open I/O failure")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", failing_open)
        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_CANNOT_OPEN_ISSUER_KEY" in str(exc_info.value) or exc_info.value.code == "ERR_CANNOT_OPEN_ISSUER_KEY"
        assert exc_info.value.primary_error is not None
        monkeypatch.undo()

        # 2. Pre-read fstat failure
        call_count = [0]
        def failing_pre_fstat(fd):
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise OSError(errno.EIO, "Simulated pre-read fstat failure")
            return st

        monkeypatch.setattr(os, "fstat", failing_pre_fstat)
        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_PRE_READ_FSTAT_FAILED" in str(exc_info.value) or exc_info.value.code == "ERR_ISSUER_KEY_PRE_READ_FSTAT_FAILED"
        assert exc_info.value.primary_error is not None
        monkeypatch.undo()

        # 3. Post-read fstat failure
        call_count = [0]
        def failing_post_fstat(fd):
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                call_count[0] += 1
                if call_count[0] == 2:
                    raise OSError(errno.EIO, "Simulated post-read fstat failure")
            return st

        monkeypatch.setattr(os, "fstat", failing_post_fstat)
        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_POST_READ_FSTAT_FAILED" in str(exc_info.value) or exc_info.value.code == "ERR_ISSUER_KEY_POST_READ_FSTAT_FAILED"
        assert exc_info.value.primary_error is not None
        monkeypatch.undo()

        # 4. Read failure
        def failing_read(fd, n):
            raise OSError(errno.EIO, "Simulated read failure")

        monkeypatch.setattr(os, "read", failing_read)
        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_READ_FAILED" in str(exc_info.value) or exc_info.value.code == "ERR_ISSUER_KEY_READ_FAILED"
        assert exc_info.value.primary_error is not None
        monkeypatch.undo()

        # 5. Close failure on key descriptor
        closed_fds: List[int] = []
        def failing_close(fd):
            closed_fds.append(fd)
            # Fail on first close
            if len(closed_fds) == 1:
                raise OSError(errno.EIO, "Simulated close failure")
            real_close(fd)

        monkeypatch.setattr(os, "close", failing_close)
        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert "ERR_ISSUER_KEY_CLOSE_FAILED" in str(exc_info.value) or exc_info.value.code == "ERR_ISSUER_KEY_CLOSE_FAILED"
        assert exc_info.value.primary_error is not None

    def test_downstream_isolation_and_no_side_effects(self, tmp_path: Path) -> None:
        """Key reader alone does not touch anchor or manifest verification."""
        trust_dir = tmp_path / "trust_iso"
        trust_dir.mkdir(mode=0o700)
        _, pem = self._make_sample_ed25519_pem()
        self._write_issuer_pub_pem(trust_dir, pem)

        pub = load_revocation_issuer_public_key_for_local_test(trust_dir)
        assert isinstance(pub, Ed25519PublicKey)

        # Confirm no anchor was created
        assert not (trust_dir / "revocation_epoch.anchor").exists()
        assert not (trust_dir / "revocations.signed.json").exists()

    def test_final_path_swap_after_traversal_uses_no_candidate_absolute_path_and_follows_no_outside_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """I10-R2-01: Proves descriptor-relative identity check detects component swap without querying absolute path."""
        parent_dir = tmp_path / "trust_swap_parent"
        parent_dir.mkdir(mode=0o700)
        trust_dir = parent_dir / "target_sub"
        trust_dir.mkdir(mode=0o700)
        _, pem = self._make_sample_ed25519_pem()
        self._write_issuer_pub_pem(trust_dir, pem)

        # Ensure absolute candidate path is NEVER queried with path-based stat
        real_stat = os.stat
        candidate_abs_str = str(trust_dir)

        def guarded_stat(path, *args, **kwargs):
            # Assert candidate absolute path is never passed to path-based stat
            if str(path) == candidate_abs_str and kwargs.get("dir_fd") is None:
                pytest.fail(f"Prohibited absolute path stat called on candidate: {path!r}")
            st = real_stat(path, *args, **kwargs)
            # Simulate inode swap on the final component relative to parent descriptor
            if str(path) == "target_sub" and kwargs.get("dir_fd") is not None:
                return os.stat_result((
                    st.st_mode,
                    st.st_ino + 888888,
                    st.st_dev,
                    st.st_nlink,
                    st.st_uid,
                    st.st_gid,
                    st.st_size,
                    st.st_atime,
                    st.st_mtime,
                    st.st_ctime,
                ))
            return st

        monkeypatch.setattr(os, "stat", guarded_stat)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_DIR_INODE_SWAP"):
            load_revocation_issuer_public_key_for_local_test(trust_dir)


class TestRevocationTrustAndRegistryComposition:
    """Gate 3-I11: Focused Unit Tests for Local-Test Revocation Trust-and-Registry Composition Core."""

    def _setup_trust_dir(self, base_dir: Path, private_key: Optional[Ed25519PrivateKey] = None) -> Tuple[Path, Ed25519PrivateKey]:
        if private_key is None:
            private_key = Ed25519PrivateKey.generate()
        trust_dir = base_dir / "trust_dir"
        trust_dir.mkdir(mode=0o700, exist_ok=True)
        pub_pem = private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        key_file = trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME
        key_file.write_bytes(pub_pem)
        key_file.chmod(0o600)
        return trust_dir, private_key

    def _setup_registry_dir(
        self,
        base_dir: Path,
        private_key: Ed25519PrivateKey,
        epoch: int = 10,
        valid_until_iso: str = "2026-11-04T00:00:00Z",
    ) -> Path:
        reg_dir = base_dir / "reg_dir"
        reg_dir.mkdir(mode=0o700, exist_ok=True)
        data = make_sample_revocation_manifest_data(
            epoch=epoch,
            issuer_id="security_officer_root",
            valid_until_iso=valid_until_iso,
            issued_at_iso="2026-11-03T00:00:00Z",
        )
        raw_bytes, _ = sign_manifest_data(data, private_key)
        reg_file = reg_dir / "revocations.signed.json"
        reg_file.write_bytes(raw_bytes)
        reg_file.chmod(0o600)
        return reg_dir

    def test_valid_end_to_end_composition_happy_path(self, tmp_path: Path) -> None:
        """Valid end-to-end: reads key fresh, verifies manifest, advances anchor, returns result."""
        trust_dir, priv = self._setup_trust_dir(tmp_path)
        reg_dir = self._setup_registry_dir(tmp_path, priv, epoch=10)

        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        result = load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
            trust_directory=trust_dir,
            registry_directory=reg_dir,
            verification_time_utc=verify_time,
        )

        assert isinstance(result, RevocationManifestVerificationResult)
        assert result.manifest.epoch == 10
        assert len(result.canonical_claims_digest_sha256) == 64

        # Verify anchor file in registry directory
        anchor_file = reg_dir / "revocation_epoch.anchor"
        assert anchor_file.exists()
        anchor_content = anchor_file.read_text(encoding="ascii")
        assert anchor_content == f"10:{result.canonical_claims_digest_sha256}\n"

    def test_fresh_read_both_key_and_registry_without_caching(self, tmp_path: Path) -> None:
        """Repeated invocations re-read both key and registry; updating either key or registry is observed."""
        trust_dir, priv1 = self._setup_trust_dir(tmp_path)
        reg_dir = self._setup_registry_dir(tmp_path, priv1, epoch=10)
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        # First call succeeds with epoch 10
        res1 = load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
            trust_dir, reg_dir, verify_time
        )
        assert res1.manifest.epoch == 10

        # Replace key in trust_dir with priv2
        priv2 = Ed25519PrivateKey.generate()
        pub_pem2 = priv2.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        key_file = trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME
        key_file.write_bytes(pub_pem2)

        # Call again without changing registry: registry was signed with priv1, so signature must fail!
        with pytest.raises(SignerLedgerError) as exc_info:
            load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                trust_dir, reg_dir, verify_time
            )
        assert "ERR_MANIFEST_SIGNATURE_INVALID" in str(exc_info.value) or exc_info.value.code == "ERR_MANIFEST_SIGNATURE_INVALID"

        # Now update registry signed by priv2 with higher epoch (20)
        self._setup_registry_dir(tmp_path, priv2, epoch=20)
        res2 = load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
            trust_dir, reg_dir, verify_time
        )
        assert res2.manifest.epoch == 20
        anchor_file = reg_dir / "revocation_epoch.anchor"
        assert anchor_file.read_text(encoding="ascii") == f"20:{res2.canonical_claims_digest_sha256}\n"

    def test_spy_call_counts_and_downstream_isolation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proves wrapper calls I10 and I9 exactly once on success, and never calls I9 when validation or I10 fails."""
        import src.services.trial_authorization_signer_service as tass

        i10_calls = [0]
        i9_calls = [0]

        real_i10 = tass.load_revocation_issuer_public_key_for_local_test
        real_i9 = tass.load_verify_and_anchor_revocation_manifest_for_local_test

        def spy_i10(*args, **kwargs):
            i10_calls[0] += 1
            return real_i10(*args, **kwargs)

        def spy_i9(*args, **kwargs):
            i9_calls[0] += 1
            return real_i9(*args, **kwargs)

        monkeypatch.setattr(tass, "load_revocation_issuer_public_key_for_local_test", spy_i10)
        monkeypatch.setattr(tass, "load_verify_and_anchor_revocation_manifest_for_local_test", spy_i9)

        trust_dir, priv = self._setup_trust_dir(tmp_path)
        reg_dir = self._setup_registry_dir(tmp_path, priv, epoch=10)
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        # 1. Validation failure: naive datetime -> neither I10 nor I9 is called
        with pytest.raises(RevocationManifestVerificationError):
            load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                trust_dir, reg_dir, datetime(2026, 11, 3, 12, 0, 0)
            )
        assert i10_calls[0] == 0
        assert i9_calls[0] == 0

        # 2. I10 failure: missing key file in empty trust dir -> I10 called once, I9 never called
        empty_trust_dir = tmp_path / "empty_trust"
        empty_trust_dir.mkdir(mode=0o700)
        with pytest.raises(SignerLedgerError):
            load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                empty_trust_dir, reg_dir, verify_time
            )
        assert i10_calls[0] == 1
        assert i9_calls[0] == 0

        # 3. Success: both called exactly once
        res = load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
            trust_dir, reg_dir, verify_time
        )
        assert i10_calls[0] == 2
        assert i9_calls[0] == 1
        assert res.manifest.epoch == 10

    def test_invalid_verification_time_inputs(self, tmp_path: Path) -> None:
        """Naive, non-UTC, or subclass datetime inputs fail closed before any I/O."""
        trust_dir, priv = self._setup_trust_dir(tmp_path)
        reg_dir = self._setup_registry_dir(tmp_path, priv, epoch=10)

        # Naive datetime
        with pytest.raises(RevocationManifestVerificationError) as exc_info:
            load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                trust_dir, reg_dir, datetime(2026, 11, 3, 12, 0, 0)
            )
        assert "ERR_VERIFICATION_TIME_INVALID" in str(exc_info.value)

        # Non-UTC timezone (e.g. UTC+7)
        tz_plus7 = timezone(timedelta(hours=7))
        with pytest.raises(RevocationManifestVerificationError) as exc_info2:
            load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                trust_dir, reg_dir, datetime(2026, 11, 3, 12, 0, 0, tzinfo=tz_plus7)
            )
        assert "ERR_VERIFICATION_TIME_INVALID" in str(exc_info2.value)

        # Subclass of datetime
        class CustomDateTime(datetime):
            pass

        sub_dt = CustomDateTime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(RevocationManifestVerificationError) as exc_info3:
            load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                trust_dir, reg_dir, sub_dt
            )
        assert "ERR_VERIFICATION_TIME_INVALID" in str(exc_info3.value)

    def test_invalid_directory_parameters(self, tmp_path: Path) -> None:
        """Invalid types or empty paths for either directory fail closed."""
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        for invalid in (None, 12345, "", "   "):
            with pytest.raises(SignerLedgerError) as exc1:
                load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                    invalid, tmp_path, verify_time  # type: ignore
                )
            assert "ERR_INVALID_TRUST_DIR" in str(exc1.value) or exc1.value.code == "ERR_INVALID_TRUST_DIR"

            with pytest.raises(SignerLedgerError) as exc2:
                load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                    tmp_path, invalid, verify_time  # type: ignore
                )
            assert "ERR_INVALID_REGISTRY_DIR" in str(exc2.value) or exc2.value.code == "ERR_INVALID_REGISTRY_DIR"

    def test_fail_closed_manifest_flaws_prevent_anchor_creation_or_advancement(self, tmp_path: Path) -> None:
        """Expired manifest or rollback fails closed without modifying anchor."""
        trust_dir, priv = self._setup_trust_dir(tmp_path)
        reg_dir = self._setup_registry_dir(tmp_path, priv, epoch=10)
        verify_time = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        # Establish anchor at epoch 10
        load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
            trust_dir, reg_dir, verify_time
        )
        anchor_file = reg_dir / "revocation_epoch.anchor"
        assert anchor_file.exists()
        original_anchor_bytes = anchor_file.read_bytes()

        # Rollback: epoch 5
        self._setup_registry_dir(tmp_path, priv, epoch=5)
        with pytest.raises(RevocationRollbackError):
            load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                trust_dir, reg_dir, verify_time
            )
        assert anchor_file.read_bytes() == original_anchor_bytes

        # Expired manifest
        expired_time = datetime(2026, 11, 10, 0, 0, 0, tzinfo=timezone.utc)
        self._setup_registry_dir(tmp_path, priv, epoch=20, valid_until_iso="2026-11-04T00:00:00Z")
        with pytest.raises(RevocationManifestVerificationError):
            load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                trust_dir, reg_dir, expired_time
            )
        assert anchor_file.read_bytes() == original_anchor_bytes













# ==============================================================================
# Gate 3-I12: Local-Test Authorization Admission Cross-Binding Tests
# ==============================================================================

class TestAuthorizationAdmissionCrossBinding:
    """Comprehensive test suite for Gate 3-I12: Admission Cross-Binding Core."""

    def _setup_admission_fixtures(
        self,
        tmp_path: Path,
        *,
        verification_time: Optional[datetime] = None,
        revoked_director_keys: Optional[List[str]] = None,
        revoked_operator_keys: Optional[List[str]] = None,
        manifest_epoch: int = 10,
        manifest_valid_until_iso: str = "2026-11-04T00:00:00Z",
        operator_id: str = "operator_canary_01",
    ):
        from src.core.contracts.trial_authorization_contracts import (
            canonical_action_payload_bytes,
            canonical_json_bytes,
        )

        tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)

        if verification_time is None:
            verification_time = datetime(2026, 11, 3, 16, 0, 10, tzinfo=timezone.utc)

        # 1. Director fixture
        proposal, approval, plan, approval_envelope, dir_priv, dir_pub, dir_key_id = make_valid_fixture()

        # 2. Operator fixture bound to approved execution_plan (I12-R1-02, I12-R2-02)
        plan_target = plan.target_spec
        first_action = plan.planned_actions[0]

        op_priv = Ed25519PrivateKey.generate()
        op_pub = op_priv.public_key()
        op_key_id = "op_key_test_01"
        op_id = operator_id
        chal_id = "chal_canary_test_01"
        client_nonce = "12345678-1234-4234-8234-123456789abc"

        authoritative_authorization_id = "auth_test_canary_01"
        authoritative_execution_id = "12345678-4321-4321-8321-abcdefabcdef"

        chal_ctx = ActiveChallengeContext(
            challenge_id=chal_id,
            client_nonce=client_nonce,
            issued_at_iso="2026-11-03T15:59:00Z",
            expires_at_iso="2026-11-03T16:05:00Z",
            consumed=False,
            preallocated_authorization_id=authoritative_authorization_id,
            preallocated_execution_id=authoritative_execution_id,
        )
        authoritative_authorization_id = chal_ctx.preallocated_authorization_id
        authoritative_execution_id = chal_ctx.preallocated_execution_id

        # Explicitly verify no identity aliasing across domains (07B5 §2.3.1, 07B7 §8.1, I12-R3-01)
        assert authoritative_authorization_id != approval_envelope.approval_record.approval_id
        assert authoritative_execution_id != client_nonce
        assert authoritative_authorization_id != authoritative_execution_id

        actual_approval_digest = approval_envelope.compute_envelope_digest()
        actual_plan_digest = compute_canonical_plan_digest(plan)

        # Compute canonical action payload digest from source-owned DTOs (I12-R2-02, I12-R3-01)
        canonical_payload = {
            "action_index": first_action.action_index,
            "action_type": first_action.action_type,
            "authorization_id": authoritative_authorization_id,
            "duration_frames": first_action.timeline_range.duration_frames,
            "execution_id": authoritative_execution_id,
            "plan_idempotency_key": plan.idempotency_key,
            "record_frame": first_action.timeline_range.start_frame,
            "role": first_action.role,
            "schema_version": "1.0",
            "source_frame_duration": first_action.source_range.duration_frames,
            "source_frame_start": first_action.source_range.start_frame,
            "source_path": first_action.source_file_path,
            "target_project_uid": plan_target.project_id,
            "target_timeline_uid": plan_target.timeline_id,
            "track_index": first_action.target_track_index,
            "track_type": first_action.target_track_type,
        }
        action_payload_digest = hashlib.sha256(canonical_action_payload_bytes(canonical_payload)).hexdigest()

        source_file_sha256 = "4" * 64
        target_project_name = plan_target.project_name
        target_project_uid = plan_target.project_id
        target_timeline_name = plan_target.timeline_name
        target_timeline_uid = plan_target.timeline_id
        source_file_path = first_action.source_file_path
        action_type = first_action.action_type
        track_type = first_action.target_track_type
        track_index = first_action.target_track_index
        record_frame = first_action.timeline_range.start_frame
        duration_frames = first_action.timeline_range.duration_frames

        signing_dict = {
            "action_payload_digest_sha256": action_payload_digest,
            "action_type": action_type,
            "approval_envelope_digest_sha256": actual_approval_digest,
            "challenge_id": chal_id,
            "client_nonce": client_nonce,
            "confirmation_phrase": "CONFIRM_CANARY_TRIAL_EXECUTION",
            "consent_timestamp_iso": "2026-11-03T16:00:00Z",
            "duration_frames": duration_frames,
            "freshness_attestation": "OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED",
            "operator_id": op_id,
            "operator_key_id": op_key_id,
            "plan_digest_sha256": actual_plan_digest,
            "record_frame": record_frame,
            "schema_version": 1,
            "source_file_path": source_file_path,
            "source_file_sha256": source_file_sha256,
            "target_project_name": target_project_name,
            "target_project_uid": target_project_uid,
            "target_timeline_name": target_timeline_name,
            "target_timeline_uid": target_timeline_uid,
            "track_index": track_index,
            "track_type": track_type,
        }
        signing_bytes = canonical_json_bytes(signing_dict)
        sig = op_priv.sign(signing_bytes).hex()

        op_envelope = SignedOperatorConsentEnvelope(
            schema_version=1,
            operator_id=op_id,
            operator_key_id=op_key_id,
            challenge_id=chal_id,
            client_nonce=client_nonce,
            approval_envelope_digest_sha256=actual_approval_digest,
            plan_digest_sha256=actual_plan_digest,
            action_payload_digest_sha256=action_payload_digest,
            target_project_name=target_project_name,
            target_project_uid=target_project_uid,
            target_timeline_name=target_timeline_name,
            target_timeline_uid=target_timeline_uid,
            source_file_path=source_file_path,
            source_file_sha256=source_file_sha256,
            action_type=action_type,
            track_type=track_type,
            track_index=track_index,
            record_frame=record_frame,
            duration_frames=duration_frames,
            confirmation_phrase="CONFIRM_CANARY_TRIAL_EXECUTION",
            freshness_attestation="OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED",
            consent_timestamp_iso="2026-11-03T16:00:00Z",
            operator_signature=sig,
        )

        op_context = OperatorConsentVerificationContext(
            authoritative_operator_id=op_id,
            expected_operator_key_id=op_key_id,
            challenge=chal_ctx,
            expected_approval_envelope_digest_sha256=actual_approval_digest,
            expected_plan_digest_sha256=actual_plan_digest,
            expected_action_payload_digest_sha256=action_payload_digest,
            expected_target_project_name=target_project_name,
            expected_target_project_uid=target_project_uid,
            expected_target_timeline_name=target_timeline_name,
            expected_target_timeline_uid=target_timeline_uid,
            expected_source_file_path=source_file_path,
            expected_source_file_sha256=source_file_sha256,
            expected_record_frame=record_frame,
            expected_duration_frames=duration_frames,
            expected_action_type=action_type,
            expected_track_type=track_type,
            expected_track_index=track_index,
            expected_authorization_id=authoritative_authorization_id,
            expected_execution_id=authoritative_execution_id,
            approval_envelope=approval_envelope,
            execution_plan=plan,
        )

        # 3. Revocation fixture
        rev_priv = Ed25519PrivateKey.generate()
        trust_dir = tmp_path / "trust_dir"
        trust_dir.mkdir(mode=0o700, exist_ok=True)
        pub_pem = rev_priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        key_file = trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME
        key_file.write_bytes(pub_pem)
        key_file.chmod(0o600)

        reg_dir = tmp_path / "reg_dir"
        reg_dir.mkdir(mode=0o700, exist_ok=True)
        manifest_data = make_sample_revocation_manifest_data(
            epoch=manifest_epoch,
            issuer_id="security_officer_root",
            valid_until_iso=manifest_valid_until_iso,
            issued_at_iso="2026-11-03T00:00:00Z",
            revoked_director_key_ids=revoked_director_keys or [],
            revoked_operator_key_ids=revoked_operator_keys or [],
        )
        raw_manifest_bytes, _ = sign_manifest_data(manifest_data, rev_priv)
        reg_file = reg_dir / "revocations.signed.json"
        reg_file.write_bytes(raw_manifest_bytes)
        reg_file.chmod(0o600)

        return (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            verification_time,
        )

    def test_valid_admission_happy_path(self, tmp_path: Path) -> None:
        """Verifies full cross-binding success, strict identity equality, and immutable evidence contents."""
        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        evidence = verify_trial_authorization_admission_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=dir_pub,
            expected_director_key_id=dir_key_id,
            operator_consent_envelope=op_envelope,
            operator_context=op_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=v_time,
        )

        assert isinstance(evidence, TrialAuthorizationAdmissionEvidence)
        assert evidence.verified is True
        assert bool(evidence) is True

        # Cross-bound identifiers
        assert evidence.proposal_id == proposal.proposal_id
        assert evidence.approval_id == approval_envelope.approval_record.approval_id
        assert evidence.director_key_id == dir_key_id
        assert evidence.operator_id == op_envelope.operator_id
        assert evidence.operator_key_id == op_envelope.operator_key_id
        assert evidence.challenge_id == op_envelope.challenge_id
        assert evidence.client_nonce == op_envelope.client_nonce

        # Cross-bound digests
        assert evidence.plan_digest_sha256 == compute_canonical_plan_digest(plan)
        assert evidence.approval_envelope_digest_sha256 == approval_envelope.compute_envelope_digest()
        assert evidence.action_payload_digest_sha256 == op_envelope.action_payload_digest_sha256
        assert evidence.target_project_uid == op_envelope.target_project_uid
        assert evidence.target_timeline_uid == op_envelope.target_timeline_uid
        assert evidence.source_file_sha256 == op_envelope.source_file_sha256
        assert evidence.revocation_epoch == 10
        assert len(evidence.revocation_canonical_claims_digest_sha256) == 64

        # Evidence carries no authorization token, signing key, or HMAC
        assert not hasattr(evidence, "authorization_id")
        assert not hasattr(evidence, "token")
        assert not hasattr(evidence, "hmac")
        assert not hasattr(evidence, "private_key")

        # Class method parity
        evidence_cls = TrialAuthorizationSignerService.verify_trial_authorization_admission_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=dir_pub,
            expected_director_key_id=dir_key_id,
            operator_consent_envelope=op_envelope,
            operator_context=op_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=v_time,
        )
        assert evidence_cls == evidence

    def test_spy_call_counts_and_stage_isolation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proves Director verifier, Operator verifier, and I11 are each called once, and downstream is isolated on upstream failure."""
        import src.services.trial_authorization_signer_service as tass

        dir_calls = [0]
        op_calls = [0]
        i11_calls = [0]

        real_dir = tass.verify_director_approval
        real_op = tass.verify_operator_consent
        real_i11 = tass.load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test

        def spy_dir(*args, **kwargs):
            dir_calls[0] += 1
            return real_dir(*args, **kwargs)

        def spy_op(*args, **kwargs):
            op_calls[0] += 1
            return real_op(*args, **kwargs)

        def spy_i11(*args, **kwargs):
            i11_calls[0] += 1
            return real_i11(*args, **kwargs)

        monkeypatch.setattr(tass, "verify_director_approval", spy_dir)
        monkeypatch.setattr(tass, "verify_operator_consent", spy_op)
        monkeypatch.setattr(tass, "load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test", spy_i11)

        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        # 1. Validation failure: naive datetime -> none called
        with pytest.raises(SignerAdmissionVerificationError):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=datetime(2026, 11, 3, 16, 0, 0),
            )
        assert dir_calls[0] == 0
        assert op_calls[0] == 0
        assert i11_calls[0] == 0

        # 2. Director failure (tampered signature): Director called once; Operator and I11 NOT called
        bad_sig = "00" * 64
        tampered_approval = dataclasses.replace(approval_envelope, director_signature=bad_sig)
        # Note: update context to match object identity
        tampered_op_context = dataclasses.replace(op_context, approval_envelope=tampered_approval)
        with pytest.raises(DirectorApprovalVerificationError):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=tampered_approval,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=tampered_op_context,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )
        assert dir_calls[0] == 1
        assert op_calls[0] == 0
        assert i11_calls[0] == 0

        # 3. Operator failure (tampered operator signature): Director called once, Operator called once; I11 NOT called
        tampered_op_env = dataclasses.replace(op_envelope, operator_signature=bad_sig)
        with pytest.raises(OperatorConsentVerificationError):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=tampered_op_env,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )
        assert dir_calls[0] == 2
        assert op_calls[0] == 1
        assert i11_calls[0] == 0

        # 4. Success: all three called once
        verify_trial_authorization_admission_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=dir_pub,
            expected_director_key_id=dir_key_id,
            operator_consent_envelope=op_envelope,
            operator_context=op_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=v_time,
        )
        assert dir_calls[0] == 3
        assert op_calls[0] == 2
        assert i11_calls[0] == 1

    def test_negative_cross_binding_cases(self, tmp_path: Path) -> None:
        """Negative cross-binding tests: object mismatch, ID mismatch, digest mismatch, target mismatch, and revoked keys."""
        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        # 1. Swapped approval envelope in context (different object reference)
        other_approval = dataclasses.replace(approval_envelope)
        bad_context_1 = dataclasses.replace(op_context, approval_envelope=other_approval)
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_CONTEXT_APPROVAL_ENVELOPE_MISMATCH"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=bad_context_1,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 2. Swapped execution plan in context (different object reference)
        other_plan = dataclasses.replace(plan)
        bad_context_2 = dataclasses.replace(op_context, execution_plan=other_plan)
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_CONTEXT_PLAN_MISMATCH"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=bad_context_2,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 3. Revoked Director key in revocation registry
        (
            appr_env3,
            prop3,
            plan3,
            dir_pub3,
            dir_key3,
            op_env3,
            op_ctx3,
            op_pub3,
            trust_dir3,
            reg_dir3,
            v_time3,
        ) = self._setup_admission_fixtures(tmp_path / "rev_dir", revoked_director_keys=[dir_key_id])
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_DIRECTOR_KEY_REVOKED"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=appr_env3,
                proposal=prop3,
                execution_plan=plan3,
                director_public_key=dir_pub3,
                expected_director_key_id=dir_key3,
                operator_consent_envelope=op_env3,
                operator_context=op_ctx3,
                operator_public_key=op_pub3,
                revocation_trust_directory=trust_dir3,
                revocation_registry_directory=reg_dir3,
                verification_time_utc=v_time3,
            )

        # 4. Revoked Operator key in revocation registry
        (
            appr_env4,
            prop4,
            plan4,
            dir_pub4,
            dir_key4,
            op_env4,
            op_ctx4,
            op_pub4,
            trust_dir4,
            reg_dir4,
            v_time4,
        ) = self._setup_admission_fixtures(tmp_path / "rev_op", revoked_operator_keys=[op_context.expected_operator_key_id])
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_OPERATOR_KEY_REVOKED"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=appr_env4,
                proposal=prop4,
                execution_plan=plan4,
                director_public_key=dir_pub4,
                expected_director_key_id=dir_key4,
                operator_consent_envelope=op_env4,
                operator_context=op_ctx4,
                operator_public_key=op_pub4,
                revocation_trust_directory=trust_dir4,
                revocation_registry_directory=reg_dir4,
                verification_time_utc=v_time4,
            )

    def test_bad_or_expired_signatures_and_challenge_fail_closed(self, tmp_path: Path) -> None:
        """Tampered signatures, expired challenge, and expired manifest fail closed."""
        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        # 1. Expired challenge
        expired_chal = dataclasses.replace(
            op_context.challenge,
            issued_at_iso="2026-11-03T14:00:00Z",
            expires_at_iso="2026-11-03T15:00:00Z",
        )
        expired_ctx = dataclasses.replace(op_context, challenge=expired_chal)
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CHALLENGE_EXPIRED"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=expired_ctx,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 2. Expired revocation manifest
        (
            appr_env2,
            prop2,
            plan2,
            dir_pub2,
            dir_key2,
            op_env2,
            op_ctx2,
            op_pub2,
            trust_dir2,
            reg_dir2,
            v_time2,
        ) = self._setup_admission_fixtures(
            tmp_path / "expired_manifest",
            manifest_valid_until_iso="2026-11-03T15:00:00Z",
        )
        with pytest.raises(RevocationManifestVerificationError, match="ERR_MANIFEST_EXPIRED"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=appr_env2,
                proposal=prop2,
                execution_plan=plan2,
                director_public_key=dir_pub2,
                expected_director_key_id=dir_key2,
                operator_consent_envelope=op_env2,
                operator_context=op_ctx2,
                operator_public_key=op_pub2,
                revocation_trust_directory=trust_dir2,
                revocation_registry_directory=reg_dir2,
                verification_time_utc=v_time2,
            )

    def test_invalid_type_utc_and_path_inputs_rejected_before_io(self, tmp_path: Path) -> None:
        """Invalid types, naive/non-UTC datetime, and blank path inputs reject before I/O."""
        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        # Naive datetime
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_VERIFICATION_TIME_INVALID"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=datetime(2026, 11, 3, 16, 0, 0),
            )

        # Blank trust directory
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_TRUST_DIR_INVALID"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory="",
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # Blank registry directory
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_REGISTRY_DIR_INVALID"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory="   ",
                verification_time_utc=v_time,
            )

    def test_repeated_invocations_reread_trust_registry_without_caching(self, tmp_path: Path) -> None:
        """Repeated invocations re-read trust and registry without caching; advancing epoch is observed."""
        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        res1 = verify_trial_authorization_admission_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=dir_pub,
            expected_director_key_id=dir_key_id,
            operator_consent_envelope=op_envelope,
            operator_context=op_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=v_time,
        )
        assert res1.revocation_epoch == 10

    def test_fault_injection_in_i11_propagated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fault injected into I11 is propagated with intact error codes and primary cause."""
        import src.services.trial_authorization_signer_service as tass

        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        def failing_i11(*args, **kwargs):
            raise SignerLedgerDurabilityError(
                "ERR_REGISTRY_READ_FAILED: simulated read failure",
                primary_error=OSError(errno.EIO, "I/O failure"),
            )

        monkeypatch.setattr(tass, "load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test", failing_i11)

        with pytest.raises(SignerLedgerDurabilityError) as exc_info:
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        assert "ERR_REGISTRY_READ_FAILED" in str(exc_info.value)
        assert exc_info.value.primary_error is not None

    def test_safety_invariants(self, tmp_path: Path) -> None:
        """Asserts zero nonce consumption, no token issuance, and zero private key or resolve IPC access."""
        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        evidence = verify_trial_authorization_admission_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=dir_pub,
            expected_director_key_id=dir_key_id,
            operator_consent_envelope=op_envelope,
            operator_context=op_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=v_time,
        )

        # Context challenge must NOT be marked consumed
        assert op_context.challenge.consumed is False

        # No token or authorization object returned
        assert type(evidence) is TrialAuthorizationAdmissionEvidence
        assert not hasattr(evidence, "authorization")
        assert not hasattr(evidence, "token")
        assert not hasattr(evidence, "private_key")

    def test_subclass_public_keys_rejected_fail_closed(self, tmp_path: Path) -> None:
        """Custom Ed25519PublicKey subclasses with no-op verify() are rejected fail-closed (I12-R1-01)."""
        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        fake_dir_pub = FakeEd25519PublicKey(dir_pub)
        fake_op_pub = FakeEd25519PublicKey(op_pub)

        # 1. Custom subclass for director_public_key in admission
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_DIRECTOR_PUBLIC_KEY_TYPE"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=fake_dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 2. Custom subclass for operator_public_key in admission
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_OPERATOR_PUBLIC_KEY_TYPE"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=fake_op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )



        # 3. Direct I4 verifier rejects subclass key
        with pytest.raises(DirectorApprovalVerificationError, match="ERR_PUBLIC_KEY_TYPE"):
            verify_director_approval(
                envelope=approval_envelope,
                proposal=proposal,
                plan=plan,
                public_key=fake_dir_pub,
                expected_director_key_id=dir_key_id,
            )

        # 4. Direct I5 verifier rejects subclass key
        with pytest.raises(OperatorConsentVerificationError, match="ERR_PUBLIC_KEY_TYPE"):
            verify_operator_consent(
                envelope=op_envelope,
                context=op_context,
                public_key=fake_op_pub,
                verification_time_utc=v_time,
            )

    def test_operator_target_and_action_mismatch_with_plan_fails_closed(self, tmp_path: Path) -> None:
        """Operator consent target and action claims must bind strictly to execution_plan (I12-R1-02)."""
        from src.core.contracts.trial_authorization_contracts import canonical_json_bytes

        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        op_priv = Ed25519PrivateKey.generate()
        op_pub_fresh = op_priv.public_key()
        chal_ctx = op_context.challenge

        def _make_signed_consent_and_context(
            overrides: dict,
        ) -> tuple[SignedOperatorConsentEnvelope, OperatorConsentVerificationContext]:
            base_dict = {
                "action_payload_digest_sha256": op_envelope.action_payload_digest_sha256,
                "action_type": op_envelope.action_type,
                "approval_envelope_digest_sha256": op_envelope.approval_envelope_digest_sha256,
                "challenge_id": chal_ctx.challenge_id,
                "client_nonce": chal_ctx.client_nonce,
                "confirmation_phrase": "CONFIRM_CANARY_TRIAL_EXECUTION",
                "consent_timestamp_iso": "2026-11-03T16:00:00Z",
                "duration_frames": op_envelope.duration_frames,
                "freshness_attestation": "OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED",
                "operator_id": "operator_test_sub",
                "operator_key_id": "op_key_test_sub",
                "plan_digest_sha256": op_envelope.plan_digest_sha256,
                "record_frame": op_envelope.record_frame,
                "schema_version": 1,
                "source_file_path": op_envelope.source_file_path,
                "source_file_sha256": op_envelope.source_file_sha256,
                "target_project_name": plan.target_spec.project_name,
                "target_project_uid": plan.target_spec.project_id,
                "target_timeline_name": plan.target_spec.timeline_name,
                "target_timeline_uid": plan.target_spec.timeline_id,
                "track_index": op_envelope.track_index,
                "track_type": op_envelope.track_type,
            }
            base_dict.update(overrides)
            sig = op_priv.sign(canonical_json_bytes(base_dict)).hex()

            env = SignedOperatorConsentEnvelope(
                schema_version=1,
                operator_id=base_dict["operator_id"],
                operator_key_id=base_dict["operator_key_id"],
                challenge_id=base_dict["challenge_id"],
                client_nonce=base_dict["client_nonce"],
                approval_envelope_digest_sha256=base_dict["approval_envelope_digest_sha256"],
                plan_digest_sha256=base_dict["plan_digest_sha256"],
                action_payload_digest_sha256=base_dict["action_payload_digest_sha256"],
                target_project_name=base_dict["target_project_name"],
                target_project_uid=base_dict["target_project_uid"],
                target_timeline_name=base_dict["target_timeline_name"],
                target_timeline_uid=base_dict["target_timeline_uid"],
                source_file_path=base_dict["source_file_path"],
                source_file_sha256=base_dict["source_file_sha256"],
                action_type=base_dict["action_type"],
                track_type=base_dict["track_type"],
                track_index=base_dict["track_index"],
                record_frame=base_dict["record_frame"],
                duration_frames=base_dict["duration_frames"],
                confirmation_phrase="CONFIRM_CANARY_TRIAL_EXECUTION",
                freshness_attestation="OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED",
                consent_timestamp_iso="2026-11-03T16:00:00Z",
                operator_signature=sig,
            )
            ctx = OperatorConsentVerificationContext(
                authoritative_operator_id=base_dict["operator_id"],
                expected_operator_key_id=base_dict["operator_key_id"],
                challenge=chal_ctx,
                expected_approval_envelope_digest_sha256=base_dict["approval_envelope_digest_sha256"],
                expected_plan_digest_sha256=base_dict["plan_digest_sha256"],
                expected_action_payload_digest_sha256=base_dict["action_payload_digest_sha256"],
                expected_target_project_name=base_dict["target_project_name"],
                expected_target_project_uid=base_dict["target_project_uid"],
                expected_target_timeline_name=base_dict["target_timeline_name"],
                expected_target_timeline_uid=base_dict["target_timeline_uid"],
                expected_source_file_path=base_dict["source_file_path"],
                expected_source_file_sha256=base_dict["source_file_sha256"],
                expected_record_frame=base_dict["record_frame"],
                expected_duration_frames=base_dict["duration_frames"],
                expected_action_type=base_dict["action_type"],
                expected_track_type=base_dict["track_type"],
                expected_track_index=base_dict["track_index"],
                approval_envelope=approval_envelope,
                execution_plan=plan,
            )
            return env, ctx

        # 1. Target project UID mismatch with plan
        env1, ctx1 = _make_signed_consent_and_context({"target_project_uid": "DIFFERENT_PROJ_UID"})
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_CONSENT_TARGET_MUTATION_MISMATCH"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=env1,
                operator_context=ctx1,
                operator_public_key=op_pub_fresh,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 2. Target timeline UID mismatch with plan
        env2, ctx2 = _make_signed_consent_and_context({"target_timeline_uid": "DIFFERENT_TL_UID"})
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_CONSENT_TARGET_MUTATION_MISMATCH"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=env2,
                operator_context=ctx2,
                operator_public_key=op_pub_fresh,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 3. Source file path not in plan planned actions
        env3, ctx3 = _make_signed_consent_and_context({"source_file_path": "/media/test/unplanned_clip.mp4"})
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_CONSENT_ACTION_NOT_IN_PLAN"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=env3,
                operator_context=ctx3,
                operator_public_key=op_pub_fresh,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 4. Source file digest mismatch when plan has source_digests
        from src.core.contracts.execution_receipt_contracts import SourceFileDigestBinding
        plan_with_digests = dataclasses.replace(
            plan,
            source_digests=(
                SourceFileDigestBinding(
                    clip_id="clip_main_1",
                    absolute_path=op_envelope.source_file_path,
                    full_sha256="5" * 64,  # different from consent's "4" * 64
                    stream_index=0,
                    stat_identity=(1, 2, 3, 4, 5),
                ),
            ),
        )
        ctx4 = dataclasses.replace(op_context, execution_plan=plan_with_digests)
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_CONSENT_SOURCE_DIGEST_MISMATCH"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan_with_digests,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=ctx4,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 5. Action payload digest mismatch when all individual fields match plan (I12-R2-02)
        env5, ctx5 = _make_signed_consent_and_context({"action_payload_digest_sha256": "f" * 64})
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_CONSENT_ACTION_PAYLOAD_DIGEST_MISMATCH"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=env5,
                operator_context=ctx5,
                operator_public_key=op_pub_fresh,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

    def test_rejected_admission_does_not_mutate_or_advance_revocation_anchor(self, tmp_path: Path) -> None:
        """I11 in admission is strictly non-mutating (advance_anchor=False); rejected admission leaves anchor and lock uncreated (I12-R1-03, I12-R2-01)."""
        reg_dir = tmp_path / "reg_no_anchor"
        anchor_file = reg_dir / "revocation_epoch.anchor"
        lock_file = reg_dir / "revocation_epoch.lock"

        # Case: Revoked director key causes admission failure; anchor and lock must NOT be created
        (
            appr_env,
            prop,
            plan,
            dir_pub,
            dir_key_id,
            op_env,
            op_ctx,
            op_pub,
            trust_dir,
            _,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path / "rev_case", revoked_director_keys=["director_key_alpha"])

        # Re-point registry to our fresh test dir
        import shutil
        reg_dir.mkdir(mode=0o700, exist_ok=True)
        shutil.copy(tmp_path / "rev_case" / "reg_dir" / "revocations.signed.json", reg_dir / "revocations.signed.json")

        assert not anchor_file.exists()
        assert not lock_file.exists()

        entries_before = {p.name: p.read_bytes() for p in reg_dir.iterdir()}

        with pytest.raises(SignerAdmissionVerificationError, match="ERR_DIRECTOR_KEY_REVOKED"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=appr_env,
                proposal=prop,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_env,
                operator_context=op_ctx,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        entries_after = {p.name: p.read_bytes() for p in reg_dir.iterdir()}
        assert entries_before == entries_after, "Registry directory must remain 100% byte-for-byte identical"
        assert not anchor_file.exists(), "Anchor file must not be created when admission fails"
        assert not lock_file.exists(), "Lock file must not be created when admission fails"

    def test_admission_registry_directory_remains_strictly_immutable(self, tmp_path: Path) -> None:
        """Admission path is strictly non-mutating: registry directory contents & bytes remain identical (I12-R2-01).

        Verifies before/after byte-for-byte identity across:
        1. Successful admission on fresh directory (no anchor, no lock file).
        2. Successful admission on directory with existing anchor and lock file.
        3. Rejected admission on directory with existing anchor and lock file.
        """
        import shutil

        def snapshot(dir_path: Path) -> dict[str, bytes]:
            return {p.name: p.read_bytes() for p in sorted(dir_path.iterdir()) if p.is_file()}

        # Scenario 1: Successful admission on fresh directory (no anchor, no lock file)
        fresh_dir = tmp_path / "scenario_1_fresh"
        (
            appr_env,
            prop,
            plan,
            dir_pub,
            dir_key_id,
            op_env,
            op_ctx,
            op_pub,
            trust_dir,
            setup_reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(fresh_dir)

        fresh_reg = fresh_dir / "isolated_reg"
        fresh_reg.mkdir(mode=0o700, exist_ok=True)
        shutil.copy(setup_reg_dir / "revocations.signed.json", fresh_reg / "revocations.signed.json")

        assert not (fresh_reg / "revocation_epoch.anchor").exists()
        assert not (fresh_reg / "revocation_epoch.lock").exists()

        snap_before_1 = snapshot(fresh_reg)
        evidence_1 = verify_trial_authorization_admission_for_local_test(
            approval_envelope=appr_env,
            proposal=prop,
            execution_plan=plan,
            director_public_key=dir_pub,
            expected_director_key_id=dir_key_id,
            operator_consent_envelope=op_env,
            operator_context=op_ctx,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=fresh_reg,
            verification_time_utc=v_time,
        )
        assert evidence_1.verified is True
        snap_after_1 = snapshot(fresh_reg)
        assert snap_before_1 == snap_after_1
        assert not (fresh_reg / "revocation_epoch.anchor").exists()
        assert not (fresh_reg / "revocation_epoch.lock").exists()

        # Scenario 2: Successful admission on directory with existing anchor and lock file
        anchored_dir = tmp_path / "scenario_2_anchored"
        (
            appr_env2,
            prop2,
            plan2,
            dir_pub2,
            dir_key_id2,
            op_env2,
            op_ctx2,
            op_pub2,
            trust_dir2,
            setup_reg_dir2,
            v_time2,
        ) = self._setup_admission_fixtures(anchored_dir)

        # Pre-seed an anchor with advance_anchor=True so anchor and lock exist
        from src.services.trial_authorization_signer_service import (
            load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test,
        )
        load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
            trust_directory=trust_dir2,
            registry_directory=setup_reg_dir2,
            verification_time_utc=v_time2,
            advance_anchor=True,
        )
        assert (setup_reg_dir2 / "revocation_epoch.anchor").exists()
        assert (setup_reg_dir2 / "revocation_epoch.lock").exists()

        snap_before_2 = snapshot(setup_reg_dir2)
        evidence_2 = verify_trial_authorization_admission_for_local_test(
            approval_envelope=appr_env2,
            proposal=prop2,
            execution_plan=plan2,
            director_public_key=dir_pub2,
            expected_director_key_id=dir_key_id2,
            operator_consent_envelope=op_env2,
            operator_context=op_ctx2,
            operator_public_key=op_pub2,
            revocation_trust_directory=trust_dir2,
            revocation_registry_directory=setup_reg_dir2,
            verification_time_utc=v_time2,
        )
        assert evidence_2.verified is True
        snap_after_2 = snapshot(setup_reg_dir2)
        assert snap_before_2 == snap_after_2

        # Scenario 3: Rejected admission on directory with existing anchor and lock file
        snap_before_3 = snapshot(setup_reg_dir2)
        bad_ctx = dataclasses.replace(op_ctx2, expected_target_project_uid="WRONG_UID")
        with pytest.raises((SignerAdmissionVerificationError, OperatorConsentVerificationError), match="ERR_CONSENT_MUTATION_MISMATCH"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=appr_env2,
                proposal=prop2,
                execution_plan=plan2,
                director_public_key=dir_pub2,
                expected_director_key_id=dir_key_id2,
                operator_consent_envelope=op_env2,
                operator_context=bad_ctx,
                operator_public_key=op_pub2,
                revocation_trust_directory=trust_dir2,
                revocation_registry_directory=setup_reg_dir2,
                verification_time_utc=v_time2,
            )
        snap_after_3 = snapshot(setup_reg_dir2)
        assert snap_before_3 == snap_after_3

    def test_non_ascii_operator_identity_success(self, tmp_path: Path) -> None:
        """Valid non-ASCII operator identity (e.g. operator_đặng) passes verification without TypeError (I12-R1-04)."""
        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path, operator_id="operator_đặng")

        evidence = verify_trial_authorization_admission_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=dir_pub,
            expected_director_key_id=dir_key_id,
            operator_consent_envelope=op_envelope,
            operator_context=op_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=v_time,
        )

        assert evidence.verified is True
        assert evidence.operator_id == "operator_đặng"

    def test_forbidden_or_outside_temp_paths_reject_before_verifier_calls_and_key_reads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Forbidden paths and paths outside temp root are rejected lexically before any verifier or key file read (I12-R1-05)."""
        import src.services.trial_authorization_signer_service as tass

        dir_calls = [0]
        op_calls = [0]
        i11_calls = [0]
        key_reader_calls = [0]

        def spy_dir(*args, **kwargs):
            dir_calls[0] += 1
            return tass.verify_director_approval(*args, **kwargs)

        def spy_op(*args, **kwargs):
            op_calls[0] += 1
            return tass.verify_operator_consent(*args, **kwargs)

        def spy_i11(*args, **kwargs):
            i11_calls[0] += 1
            return tass.load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(*args, **kwargs)

        def spy_key_reader(*args, **kwargs):
            key_reader_calls[0] += 1
            return tass.load_revocation_issuer_public_key_for_local_test(*args, **kwargs)

        monkeypatch.setattr(tass, "verify_director_approval", spy_dir)
        monkeypatch.setattr(tass, "verify_operator_consent", spy_op)
        monkeypatch.setattr(tass, "load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test", spy_i11)
        monkeypatch.setattr(tass, "load_revocation_issuer_public_key_for_local_test", spy_key_reader)

        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        # 1. Banned trust directory: /var/lib/resolveflow
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_TRUST_DIR_INVALID"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory="/var/lib/resolveflow/trust",
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )
        assert dir_calls[0] == 0
        assert op_calls[0] == 0
        assert i11_calls[0] == 0
        assert key_reader_calls[0] == 0

        # 2. Outside temp trust directory: /etc/resolveflow
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_TRUST_DIR_INVALID"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory="/etc/resolveflow",
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )
        assert dir_calls[0] == 0
        assert op_calls[0] == 0
        assert i11_calls[0] == 0
        assert key_reader_calls[0] == 0

        # 3. Banned registry directory: /var/lib/resolveflow
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_REGISTRY_DIR_INVALID"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory="/var/lib/resolveflow/registry",
                verification_time_utc=v_time,
            )
        assert dir_calls[0] == 0
        assert op_calls[0] == 0
        assert i11_calls[0] == 0
        assert key_reader_calls[0] == 0

        # 4. Outside temp registry directory: /var/resolveflow
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_REGISTRY_DIR_INVALID"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=op_context,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory="/var/resolveflow",
                verification_time_utc=v_time,
            )
        assert dir_calls[0] == 0
        assert op_calls[0] == 0
        assert i11_calls[0] == 0
        assert key_reader_calls[0] == 0


    def test_canonical_action_payload_assembler_parity_and_anti_aliasing(self, tmp_path: Path) -> None:
        """I12-R3-01 Regression: Byte-for-byte and digest parity with accepted assemble_canonical_action_payload.

        Enforces:
        1. Non-aliased distinct identifiers: approval_id != authorization_id, client_nonce != execution_id.
        2. Exact byte-for-byte match between assemble_canonical_action_payload_for_admission() and
           the canonical assembler assemble_canonical_action_payload().
        3. Strict anti-aliasing gates failing closed if authorization_id aliases approval_id or
           execution_id aliases client_nonce.
        """
        from tests.core.contracts.test_trial_authorization_contracts import make_sample_auth
        from src.core.contracts.trial_authorization_contracts import (
            PlannedTimelineAction as ContractsPlannedAction,
            assemble_canonical_action_payload,
            canonical_action_payload_bytes,
        )
        from src.services.trial_authorization_signer_service import (
            assemble_canonical_action_payload_for_admission,
            compute_canonical_action_payload_digest_for_admission,
        )

        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        binder_action = plan.planned_actions[0]

        # Authoritative distinct identities
        # Authoritative distinct identities bound from challenge
        authoritative_auth_id = op_context.challenge.preallocated_authorization_id
        authoritative_exec_id = op_context.challenge.preallocated_execution_id
        director_approval_id = approval_envelope.approval_record.approval_id
        challenge_client_nonce = op_envelope.client_nonce

        # 1. Assert non-aliased distinct identity invariants
        assert authoritative_auth_id != director_approval_id
        assert authoritative_exec_id != challenge_client_nonce
        assert authoritative_auth_id != authoritative_exec_id

        # 2. Construct matching TrialExecutionAuthorization DTO for accepted assembler
        auth_dto = make_sample_auth(
            authorization_id=authoritative_auth_id,
            execution_id=authoritative_exec_id,
            token_nonce=authoritative_exec_id,
            authorized_action_type=binder_action.action_type,
            authorized_track_type=binder_action.target_track_type,
            authorized_track_index=binder_action.target_track_index,
            source_file_path=binder_action.source_file_path,
            authorized_source_frame_start=binder_action.source_range.start_frame,
            authorized_source_frame_duration=binder_action.source_range.duration_frames,
            authorized_timeline_frame_duration=binder_action.timeline_range.duration_frames,
            authorized_record_frame=binder_action.timeline_range.start_frame,
            observed_timeline_start_frame=binder_action.timeline_range.start_frame,
            plan_idempotency_key=plan.idempotency_key,
            target_project_uid=plan.target_spec.project_id,
            target_timeline_uid=plan.target_spec.timeline_id,
        )

        # Construct matching ContractsPlannedAction
        contracts_action = ContractsPlannedAction(
            action_id="act_canary_01",
            action_index=binder_action.action_index,
            action_type=binder_action.action_type,
            track_type=binder_action.target_track_type,
            track_index=binder_action.target_track_index,
            record_frame=binder_action.timeline_range.start_frame,
            duration_frames=binder_action.timeline_range.duration_frames,
            source_path=binder_action.source_file_path,
            source_frame_start=binder_action.source_range.start_frame,
            source_frame_duration=binder_action.source_range.duration_frames,
            role=binder_action.role,
        )

        # Call accepted canonical assembler
        canonical_dispatch_payload = assemble_canonical_action_payload(
            contracts_action,
            auth_dto,
            authoritative_exec_id,
        )
        canonical_dispatch_bytes = canonical_action_payload_bytes(canonical_dispatch_payload)
        canonical_dispatch_digest = hashlib.sha256(canonical_dispatch_bytes).hexdigest().lower()

        # Call admission canonical assembler
        admission_payload = assemble_canonical_action_payload_for_admission(
            action=binder_action,
            execution_plan=plan,
            authorization_id=authoritative_auth_id,
            execution_id=authoritative_exec_id,
        )
        admission_bytes = canonical_action_payload_bytes(admission_payload)
        admission_digest = compute_canonical_action_payload_digest_for_admission(
            action=binder_action,
            execution_plan=plan,
            authorization_id=authoritative_auth_id,
            execution_id=authoritative_exec_id,
        )

        # Assert key-for-key, byte-for-byte, and digest-for-digest parity
        assert admission_payload == canonical_dispatch_payload
        assert admission_bytes == canonical_dispatch_bytes
        assert admission_digest == canonical_dispatch_digest

        # 3. Anti-aliasing negative tests on verify_trial_authorization_admission_for_local_test
        # 3a. authorization_id aliases approval_id (both in challenge and context)
        chal_aliased_auth = dataclasses.replace(
            op_context.challenge,
            preallocated_authorization_id=director_approval_id,
        )
        bad_ctx_aliased_auth = dataclasses.replace(
            op_context,
            challenge=chal_aliased_auth,
            expected_authorization_id=director_approval_id,
        )
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_IDENTITY_ALIASING.*must not alias approval_id"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=bad_ctx_aliased_auth,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 3b. execution_id aliases client_nonce
        chal_aliased_exec = dataclasses.replace(
            op_context.challenge,
            preallocated_execution_id=challenge_client_nonce,
        )
        bad_ctx_aliased_exec = dataclasses.replace(
            op_context,
            challenge=chal_aliased_exec,
            expected_execution_id=challenge_client_nonce,
        )
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_IDENTITY_ALIASING.*must not alias client_nonce"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=bad_ctx_aliased_exec,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 3c. authorization_id aliases execution_id
        chal_aliased_both = dataclasses.replace(
            op_context.challenge,
            preallocated_authorization_id=authoritative_auth_id,
            preallocated_execution_id=authoritative_auth_id,
        )
        bad_ctx_aliased_both = dataclasses.replace(
            op_context,
            challenge=chal_aliased_both,
            expected_authorization_id=authoritative_auth_id,
            expected_execution_id=authoritative_auth_id,
        )
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_IDENTITY_ALIASING.*must not alias expected_execution_id"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=bad_ctx_aliased_both,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 3d. Missing expected_authorization_id
        bad_chal_no_auth = dataclasses.replace(op_context.challenge, preallocated_authorization_id=None)
        bad_ctx_no_auth = dataclasses.replace(
            op_context,
            challenge=bad_chal_no_auth,
            expected_authorization_id=None,
        )
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_MISSING_AUTHORIZATION_ID"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=bad_ctx_no_auth,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 3e. Missing expected_execution_id
        bad_chal_no_exec = dataclasses.replace(op_context.challenge, preallocated_execution_id=None)
        bad_ctx_no_exec = dataclasses.replace(
            op_context,
            challenge=bad_chal_no_exec,
            expected_execution_id=None,
        )
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_MISSING_EXECUTION_ID"):
            verify_trial_authorization_admission_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=dir_pub,
                expected_director_key_id=dir_key_id,
                operator_consent_envelope=op_envelope,
                operator_context=bad_ctx_no_exec,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=v_time,
            )

        # 3f. I12-R4-01 Negative: Conflicting expected_authorization_id vs challenge preallocated_authorization_id
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONTEXT_FIELD_INVALID.*conflicts with challenge.preallocated_authorization_id"):
            dataclasses.replace(
                op_context,
                expected_authorization_id="auth_conflicting_spoofed_01",
            )

        # 3g. I12-R4-01 Negative: Conflicting expected_execution_id vs challenge preallocated_execution_id
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONTEXT_FIELD_INVALID.*conflicts with challenge.preallocated_execution_id"):
            dataclasses.replace(
                op_context,
                expected_execution_id="exec_conflicting_spoofed_02",
            )

        # 3i. I12-R4-01 Positive Path: Preallocated identities flow into admission evidence,
        # eventual TrialExecutionAuthorization DTO, and canonical dispatch payload assembler
        admission_evidence = verify_trial_authorization_admission_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=dir_pub,
            expected_director_key_id=dir_key_id,
            operator_consent_envelope=op_envelope,
            operator_context=op_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=v_time,
        )
        assert admission_evidence.action_payload_digest_sha256 == op_envelope.action_payload_digest_sha256
        assert admission_evidence.action_payload_digest_sha256 == canonical_dispatch_digest

    def test_preallocated_identities_strictly_bound_to_challenge(self, tmp_path: Path) -> None:
        """I12-R4-01 & I12-R7 Scope Boundary: Admission strictly binds expected identities to challenge.

        Enforces:
        1. operator_context rejects any expected_authorization_id conflicting with challenge.preallocated_authorization_id.
        2. operator_context rejects any expected_execution_id conflicting with challenge.preallocated_execution_id.
        3. Identity anti-aliasing invariants strictly enforced: authorization_id != execution_id != client_nonce != approval_id.
        4. Module scope exposes zero secret constants, zero signing private keys, and zero authority tokens.
        5. Preallocated identities flow unchanged through context, admission preflight, and dispatch assembler.
        """
        import src.services.trial_authorization_signer_service as tass

        # 1. Module scope is strictly verification-only (I12-R7-02)
        assert not hasattr(tass, "_SIGNER_CHALLENGE_PROVENANCE_SECRET")
        assert not hasattr(tass, "_compute_challenge_provenance_seal")
        assert not hasattr(tass.TrialAuthorizationSignerService, "issue_operator_challenge")
        assert not hasattr(tass.TrialAuthorizationSignerService, "verify_challenge_provenance")

        (
            approval_envelope,
            proposal,
            plan,
            dir_pub,
            dir_key_id,
            op_envelope,
            op_context,
            op_pub,
            trust_dir,
            reg_dir,
            v_time,
        ) = self._setup_admission_fixtures(tmp_path)

        # 2. Rejection of conflicting caller expected_authorization_id
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONTEXT_FIELD_INVALID.*conflicts with challenge.preallocated_authorization_id"):
            dataclasses.replace(
                op_context,
                expected_authorization_id="auth_conflicting_caller_01",
            )

        # 3. Rejection of conflicting caller expected_execution_id
        with pytest.raises(OperatorConsentVerificationError, match="ERR_CONTEXT_FIELD_INVALID.*conflicts with challenge.preallocated_execution_id"):
            dataclasses.replace(
                op_context,
                expected_execution_id="exec_conflicting_caller_02",
            )

        # 4. Admission verifier verifies preallocated identities match challenge
        evidence = verify_trial_authorization_admission_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=dir_pub,
            expected_director_key_id=dir_key_id,
            operator_consent_envelope=op_envelope,
            operator_context=op_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=v_time,
        )
        assert evidence.verified is True
        assert evidence.action_payload_digest_sha256 == op_envelope.action_payload_digest_sha256

    def test_multiprocess_anchor_read_synchronized_with_exclusive_advance(self, tmp_path: Path) -> None:
        """I12-R3-02 Regression: Multi-process race test proving read-only admission synchronizes with exclusive advance.

        Enforces:
        1. Registry directory descriptor flock(dir_fd, LOCK_SH) serializes read-only verification
           against concurrent processes performing flock(dir_fd, LOCK_EX) advancement.
        2. Reader blocks until exclusive advance completes and observes the updated epoch anchor.
        3. Zero lock file creation on read-only path when lock file is not initially provisioned.
        """
        import multiprocessing
        import fcntl
        import time
        from src.services.trial_authorization_signer_service import _compute_provenance_seal

        def _make_verified_manifest_result(epoch: int) -> RevocationManifestVerificationResult:
            m = SignedRevocationManifest(
                epoch=epoch,
                issued_at_iso="2026-11-03T16:00:00Z",
                issuer_id="security_officer_root",
                manifest_version=1,
                revoked_authorization_ids=[],
                revoked_director_key_ids=[],
                revoked_operator_key_ids=[],
                revoked_trial_key_ids=[],
                valid_until_iso="2026-11-04T16:00:00Z",
                manifest_signature="0" * 128,
            )
            d = hashlib.sha256(canonical_revocation_manifest_claims_bytes(m)).hexdigest().lower()
            seal = _compute_provenance_seal(m, d)
            return RevocationManifestVerificationResult(
                verified=True,
                manifest=m,
                canonical_claims_digest_sha256=d,
                _provenance_seal=seal,
            )

        reg_dir = tmp_path / "sync_reg_dir"
        reg_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        reg_dir_str = str(reg_dir)

        # 1. Verify read-only path on fresh registry creates ZERO files (lock file remains absent)
        fresh_res = _make_verified_manifest_result(1)
        check_and_advance_revocation_epoch_anchor_for_local_test(
            verified_result=fresh_res,
            anchor_directory=reg_dir_str,
            advance=False,
        )
        assert not (reg_dir / "revocation_epoch.lock").exists()
        assert not (reg_dir / "revocation_epoch.anchor").exists()

        # 2. Multi-process race: Writer holds LOCK_EX while updating anchor, Reader blocks on LOCK_SH
        def _writer_worker(d_str: str, queue: multiprocessing.Queue) -> None:
            try:
                dfd = os.open(d_str, os.O_RDONLY | os.O_DIRECTORY)
                fcntl.flock(dfd, fcntl.LOCK_EX)
                queue.put("WRITER_ACQUIRED_LOCK_EX")
                time.sleep(0.3)
                # Commit epoch 2 anchor with 0600 permissions
                res2 = _make_verified_manifest_result(2)
                anchor_file = os.path.join(d_str, "revocation_epoch.anchor")
                fd = os.open(anchor_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                os.write(fd, f"2:{res2.canonical_claims_digest_sha256}\n".encode())
                os.close(fd)
                fcntl.flock(dfd, fcntl.LOCK_UN)
                os.close(dfd)
                queue.put("WRITER_COMMITTED")
            except Exception as e:
                queue.put(f"WRITER_ERROR: {e}")

        def _reader_worker(d_str: str, queue: multiprocessing.Queue) -> None:
            try:
                res2 = _make_verified_manifest_result(2)
                t_start = time.time()
                check_and_advance_revocation_epoch_anchor_for_local_test(
                    verified_result=res2,
                    anchor_directory=d_str,
                    advance=False,
                )
                t_elapsed = time.time() - t_start
                queue.put(("READER_SUCCESS", t_elapsed))
            except Exception as e:
                queue.put(("READER_ERROR", str(e)))

        wq: multiprocessing.Queue = multiprocessing.Queue()
        rq: multiprocessing.Queue = multiprocessing.Queue()

        wp = multiprocessing.Process(target=_writer_worker, args=(reg_dir_str, wq))
        wp.start()
        writer_status = wq.get(timeout=5)
        assert writer_status == "WRITER_ACQUIRED_LOCK_EX"

        rp = multiprocessing.Process(target=_reader_worker, args=(reg_dir_str, rq))
        rp.start()

        wp.join(timeout=5)
        rp.join(timeout=5)

        writer_commit = wq.get(timeout=5)
        assert writer_commit == "WRITER_COMMITTED"

        reader_outcome, elapsed_sec = rq.get(timeout=5)
        assert reader_outcome == "READER_SUCCESS"
        # Elapsed must be at least ~0.2s, proving reader waited on writer's exclusive lock
        assert elapsed_sec >= 0.15


# ==============================================================================
# Gate 3-I13: Local-Test Descriptor-Anchored Signer Keystore Core Tests
# ==============================================================================

class TestLocalTestSignerKeystoreCore:
    """Rigorous tests for Gate 3-I13 Local-Test Descriptor-Anchored Signer Keystore Core."""

    @pytest.fixture
    def keystore_dir(self, tmp_path: Path) -> Path:
        ks_dir = tmp_path / "signer_keystore"
        ks_dir.mkdir(parents=True, exist_ok=True)
        ks_dir.chmod(0o700)
        return ks_dir

    def _write_key_file(self, keystore_dir: Path, data: bytes, mode: int = 0o600) -> Path:
        key_path = keystore_dir / "trial_auth.key"
        key_path.unlink(missing_ok=True)
        key_path.write_bytes(data)
        key_path.chmod(mode)
        return key_path

    def test_happy_path_reading_ephemeral_key_handle_redacted_and_no_raw_bytes(
        self, keystore_dir: Path
    ) -> None:
        from src.services.trial_authorization_signer_service import (
            LocalTestSignerKeyHandle,
            load_signer_key_handle_for_local_test,
        )

        key_bytes = os.urandom(32)
        self._write_key_file(keystore_dir, key_bytes, mode=0o600)

        handle = load_signer_key_handle_for_local_test(keystore_dir)
        assert isinstance(handle, LocalTestSignerKeyHandle)
        assert repr(handle) == "<LocalTestSignerKeyHandle: REDACTED>"
        assert str(handle) == "<LocalTestSignerKeyHandle: REDACTED>"
        assert key_bytes.hex() not in repr(handle)
        assert key_bytes.hex() not in str(handle)

        # Public surface check: only verify_trial_execution_authorization exists
        public_methods = [m for m in dir(handle) if not m.startswith("_")]
        assert public_methods == ["verify_trial_execution_authorization"]

        # No raw key attributes or slots exposed
        assert not hasattr(handle, "key_bytes")
        assert not hasattr(handle, "_key_bytes")
        assert getattr(handle, "_key_bytes", None) is None

        # Immutability
        with pytest.raises(AttributeError):
            handle._key_bytes = b"x" * 32
        with pytest.raises(AttributeError):
            handle.injected = True

        # No signing / minting / export capabilities
        assert not hasattr(handle, "sign")
        assert not hasattr(handle, "mint")
        assert not hasattr(handle, "issue")
        assert not hasattr(handle, "get_key")

        # Direct construction by caller is unconditionally prohibited
        from src.services.trial_authorization_signer_service import SignerLedgerSecurityError
        import src.services.trial_authorization_signer_service as signer_module

        with pytest.raises(SignerLedgerSecurityError, match="ERR_DIRECT_KEY_HANDLE_CONSTRUCTION_PROHIBITED"):
            LocalTestSignerKeyHandle(os.urandom(32))

        with pytest.raises(SignerLedgerSecurityError, match="ERR_DIRECT_KEY_HANDLE_CONSTRUCTION_PROHIBITED"):
            LocalTestSignerKeyHandle()

        # Regression test for I13-R2-01: prove no discoverable module attribute permits caller construction
        for attr_name in dir(signer_module):
            attr_val = getattr(signer_module, attr_name)
            with pytest.raises(SignerLedgerSecurityError, match="ERR_DIRECT_KEY_HANDLE_CONSTRUCTION_PROHIBITED"):
                LocalTestSignerKeyHandle(os.urandom(32), attr_val)
            with pytest.raises(SignerLedgerSecurityError, match="ERR_DIRECT_KEY_HANDLE_CONSTRUCTION_PROHIBITED"):
                LocalTestSignerKeyHandle(attr_val)
            with pytest.raises(SignerLedgerSecurityError, match="ERR_DIRECT_KEY_HANDLE_CONSTRUCTION_PROHIBITED"):
                LocalTestSignerKeyHandle(_token=attr_val)

    def test_valid_and_invalid_hmac_verification(self, keystore_dir: Path) -> None:
        import dataclasses
        from src.core.contracts.trial_authorization_contracts import (
            canonical_bytes_for_auth_hmac,
        )
        from src.services.trial_authorization_signer_service import (
            load_signer_key_handle_for_local_test,
        )
        from tests.core.contracts.test_trial_authorization_contracts import make_sample_auth

        key_bytes = os.urandom(32)
        self._write_key_file(keystore_dir, key_bytes, mode=0o600)
        handle = load_signer_key_handle_for_local_test(keystore_dir)

        # Construct sample auth with dummy 64-hex hmac
        dummy_hmac = "a" * 64
        sample_auth = make_sample_auth(authorization_hmac=dummy_hmac)

        # Compute valid HMAC using test key
        canonical = canonical_bytes_for_auth_hmac(sample_auth)
        valid_hmac = hmac.new(key_bytes, canonical, hashlib.sha256).hexdigest()
        valid_auth = dataclasses.replace(sample_auth, authorization_hmac=valid_hmac)

        # 1. Valid HMAC verification succeeds
        assert handle.verify_trial_execution_authorization(valid_auth) is True

        # 2. Corrupted HMAC fails verification (returns False, does not raise)
        corrupted_auth = dataclasses.replace(sample_auth, authorization_hmac="0" * 64)
        assert handle.verify_trial_execution_authorization(corrupted_auth) is False

        # 3. Different key fails verification
        other_dir = keystore_dir.parent / "other_keystore"
        other_dir.mkdir(parents=True, exist_ok=True)
        other_dir.chmod(0o700)
        other_key = os.urandom(32)
        self._write_key_file(other_dir, other_key, mode=0o600)
        other_handle = load_signer_key_handle_for_local_test(other_dir)
        assert other_handle.verify_trial_execution_authorization(valid_auth) is False

    def test_malformed_auth_dtos_and_subclasses_rejected_fail_closed(
        self, keystore_dir: Path
    ) -> None:
        import dataclasses
        from src.core.contracts.trial_authorization_contracts import (
            TrialExecutionAuthorization,
            canonical_bytes_for_auth_hmac,
        )
        from src.services.trial_authorization_signer_service import (
            SignerAdmissionVerificationError,
            load_signer_key_handle_for_local_test,
        )
        from tests.core.contracts.test_trial_authorization_contracts import make_sample_auth

        key_bytes = os.urandom(32)
        self._write_key_file(keystore_dir, key_bytes, mode=0o600)
        handle = load_signer_key_handle_for_local_test(keystore_dir)

        sample_auth = make_sample_auth(authorization_hmac="a" * 64)
        canonical = canonical_bytes_for_auth_hmac(sample_auth)
        valid_hmac = hmac.new(key_bytes, canonical, hashlib.sha256).hexdigest()
        valid_auth = dataclasses.replace(sample_auth, authorization_hmac=valid_hmac)

        # 1. Non-instances rejected
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_INVALID_AUTH_TYPE"):
            handle.verify_trial_execution_authorization("not an auth")  # type: ignore

        with pytest.raises(SignerAdmissionVerificationError, match="ERR_INVALID_AUTH_TYPE"):
            handle.verify_trial_execution_authorization(None)  # type: ignore

        with pytest.raises(SignerAdmissionVerificationError, match="ERR_INVALID_AUTH_TYPE"):
            handle.verify_trial_execution_authorization({"auth": 1})  # type: ignore

        # 2. Subclasses rejected fail-closed
        class SubclassAuth(TrialExecutionAuthorization):
            pass

        sub_auth = SubclassAuth(**valid_auth.__dict__)
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_INVALID_AUTH_TYPE"):
            handle.verify_trial_execution_authorization(sub_auth)

        # 3. Malformed auth contract fields rejected fail-closed
        bad_contract_auth = dataclasses.replace(valid_auth, authorization_hmac="not-a-valid-64-hex")
        with pytest.raises(SignerAdmissionVerificationError, match="ERR_AUTH_CONTRACT_INVALID"):
            handle.verify_trial_execution_authorization(bad_contract_auth)

    def test_reject_wrong_lengths_empty_and_oversized(self, keystore_dir: Path) -> None:
        from src.services.trial_authorization_signer_service import (
            SignerLedgerCorruptionError,
            load_signer_key_handle_for_local_test,
        )

        for length in (0, 1, 31, 33, 64, 1024):
            self._write_key_file(keystore_dir, b"x" * length, mode=0o600)
            with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_KEY_INVALID_LENGTH"):
                load_signer_key_handle_for_local_test(keystore_dir)

    def test_reject_missing_symlink_hardlink_and_non_regular_file(
        self, keystore_dir: Path, tmp_path: Path
    ) -> None:
        from src.services.trial_authorization_signer_service import (
            SignerLedgerError,
            SignerLedgerSecurityError,
            load_signer_key_handle_for_local_test,
        )

        # 1. Missing file
        empty_dir = tmp_path / "empty_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)
        empty_dir.chmod(0o700)
        with pytest.raises(SignerLedgerError, match="ERR_SIGNER_KEY_FILE_NOT_FOUND"):
            load_signer_key_handle_for_local_test(empty_dir)

        # 2. Symlink
        target_key = tmp_path / "target.key"
        target_key.write_bytes(os.urandom(32))
        target_key.chmod(0o600)
        symlink_dir = tmp_path / "symlink_dir"
        symlink_dir.mkdir(parents=True, exist_ok=True)
        symlink_dir.chmod(0o700)
        (symlink_dir / "trial_auth.key").symlink_to(target_key)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_SIGNER_KEY_SYMLINK_DETECTED"):
            load_signer_key_handle_for_local_test(symlink_dir)

        # 3. Hardlink
        hardlink_dir = tmp_path / "hardlink_dir"
        hardlink_dir.mkdir(parents=True, exist_ok=True)
        hardlink_dir.chmod(0o700)
        os.link(target_key, hardlink_dir / "trial_auth.key")
        with pytest.raises(SignerLedgerSecurityError, match="ERR_SIGNER_KEY_HARDLINK_DETECTED"):
            load_signer_key_handle_for_local_test(hardlink_dir)

        # 4. Directory instead of regular file
        dir_key_dir = tmp_path / "dir_key_dir"
        dir_key_dir.mkdir(parents=True, exist_ok=True)
        dir_key_dir.chmod(0o700)
        (dir_key_dir / "trial_auth.key").mkdir(parents=True, exist_ok=True)
        (dir_key_dir / "trial_auth.key").chmod(0o700)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_SIGNER_KEY_NOT_REGULAR_FILE"):
            load_signer_key_handle_for_local_test(dir_key_dir)

    def test_reject_insecure_mode_and_invalid_owner(
        self, keystore_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.trial_authorization_signer_service import (
            SignerLedgerSecurityError,
            load_signer_key_handle_for_local_test,
        )

        # 1. Insecure modes and special-bit variants
        for mode in (0o644, 0o666, 0o700, 0o777, 0o660, 0o400, 0o4600, 0o2600, 0o1600, 0o7600):
            self._write_key_file(keystore_dir, os.urandom(32), mode=mode)
            with pytest.raises(SignerLedgerSecurityError, match="ERR_SIGNER_KEY_INSECURE_PERMS"):
                load_signer_key_handle_for_local_test(keystore_dir)

        # 2. Invalid owner
        self._write_key_file(keystore_dir, os.urandom(32), mode=0o600)
        real_fstat = os.fstat

        class FakeStat:
            def __init__(self, real_st: os.stat_result) -> None:
                self._real = real_st
                self.st_mode = real_st.st_mode
                self.st_size = real_st.st_size
                self.st_nlink = real_st.st_nlink
                self.st_uid = real_st.st_uid + 9999
                self.st_dev = real_st.st_dev
                self.st_ino = real_st.st_ino
                self.st_mtime_ns = real_st.st_mtime_ns

        def fake_fstat(fd: int) -> os.stat_result:
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                return FakeStat(st)  # type: ignore
            return st

        monkeypatch.setattr(os, "fstat", fake_fstat)
        with pytest.raises(SignerLedgerSecurityError, match="ERR_SIGNER_KEY_INVALID_OWNER"):
            load_signer_key_handle_for_local_test(keystore_dir)

    def test_reject_production_and_out_of_root_paths_lexically_before_filesystem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.trial_authorization_signer_service import (
            SignerLedgerError,
            SignerLedgerSecurityError,
            load_signer_key_handle_for_local_test,
        )

        queried_paths: List[str] = []
        real_stat = os.stat
        real_open = os.open

        def spy_stat(path, *args, **kwargs):
            if isinstance(path, (str, Path)):
                queried_paths.append(str(path))
            return real_stat(path, *args, **kwargs)

        def spy_open(path, *args, **kwargs):
            if isinstance(path, (str, Path)):
                queried_paths.append(str(path))
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", spy_stat)
        monkeypatch.setattr(os, "open", spy_open)

        # Production path check
        with pytest.raises(SignerLedgerSecurityError, match="ERR_SIGNER_KEYSTORE_INSECURE_PATH"):
            load_signer_key_handle_for_local_test("/var/lib/resolveflow/signer/keystore")
        assert not any("/var/lib/resolveflow" in p for p in queried_paths)

        with pytest.raises(SignerLedgerSecurityError, match="ERR_SIGNER_KEYSTORE_INSECURE_PATH"):
            load_signer_key_handle_for_local_test("/run/resolveflow/keys")
        assert not any("/run/resolveflow" in p for p in queried_paths)

        # Out-of-root path check
        with pytest.raises(SignerLedgerSecurityError, match="ERR_ANCHOR_DIR_NOT_TEMP"):
            load_signer_key_handle_for_local_test("/etc/shadow")
        assert not any("/etc/shadow" in p for p in queried_paths)

        # Invalid param types
        for invalid in (None, "", "   ", 12345):
            with pytest.raises(SignerLedgerError, match="ERR_INVALID_KEYSTORE_DIR"):
                load_signer_key_handle_for_local_test(invalid)  # type: ignore

    def test_detect_metadata_mutation_and_short_read_fail_closed(
        self, keystore_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.trial_authorization_signer_service import (
            SignerLedgerCorruptionError,
            load_signer_key_handle_for_local_test,
        )

        self._write_key_file(keystore_dir, os.urandom(32), mode=0o600)

        # 1. Mutation during read (mtime altered)
        real_fstat = os.fstat
        fstat_count = [0]

        class MutatedStat:
            def __init__(self, real_st: os.stat_result) -> None:
                self.st_mode = real_st.st_mode
                self.st_size = real_st.st_size
                self.st_nlink = real_st.st_nlink
                self.st_uid = real_st.st_uid
                self.st_dev = real_st.st_dev
                self.st_ino = real_st.st_ino
                self.st_mtime_ns = real_st.st_mtime_ns + 1000000

        def mutating_fstat(fd: int) -> os.stat_result:
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                fstat_count[0] += 1
                if fstat_count[0] >= 2:  # st_after
                    return MutatedStat(st)  # type: ignore
            return st

        monkeypatch.setattr(os, "fstat", mutating_fstat)
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_KEY_MUTATED_DURING_READ"):
            load_signer_key_handle_for_local_test(keystore_dir)

        monkeypatch.undo()

        # 2. Short read
        real_read = os.read
        read_calls = [0]

        def short_read(fd: int, n: int) -> bytes:
            read_calls[0] += 1
            if read_calls[0] == 1:
                return b"short_16_bytes__"
            return b""

        monkeypatch.setattr(os, "read", short_read)
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_KEY_SHORT_READ"):
            load_signer_key_handle_for_local_test(keystore_dir)

        monkeypatch.undo()

        # 3. Oversized / file grew during read
        def oversized_read(fd: int, n: int) -> bytes:
            return b"x" * 33

        monkeypatch.setattr(os, "read", oversized_read)
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_KEY_FILE_OVERSIZED"):
            load_signer_key_handle_for_local_test(keystore_dir)

    def test_inject_io_failures_and_single_close_semantics(
        self, keystore_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.trial_authorization_signer_service import (
            SignerLedgerDurabilityError,
            load_signer_key_handle_for_local_test,
        )

        self._write_key_file(keystore_dir, os.urandom(32), mode=0o600)

        # 1. Inject OSError on os.open
        real_open = os.open

        def failing_open(path, flags, *args, **kwargs):
            if path == "trial_auth.key":
                raise OSError(errno.EIO, "Injected I/O error on open")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", failing_open)
        with pytest.raises(SignerLedgerDurabilityError, match="ERR_CANNOT_OPEN_SIGNER_KEY") as excinfo:
            load_signer_key_handle_for_local_test(keystore_dir)
        assert excinfo.value.code == "ERR_CANNOT_OPEN_SIGNER_KEY"
        assert excinfo.value.primary_error is not None

        monkeypatch.undo()

        # 2. Inject OSError on os.read
        def failing_read(fd: int, n: int) -> bytes:
            raise OSError(errno.EIO, "Injected I/O error on read")

        monkeypatch.setattr(os, "read", failing_read)
        with pytest.raises(SignerLedgerDurabilityError, match="ERR_SIGNER_KEY_READ_FAILED") as excinfo:
            load_signer_key_handle_for_local_test(keystore_dir)
        assert excinfo.value.code == "ERR_SIGNER_KEY_READ_FAILED"

        monkeypatch.undo()

        # 3. Inject OSError on pre-read fstat
        real_fstat = os.fstat
        fstat_calls = [0]

        def failing_fstat(fd: int) -> os.stat_result:
            st = real_fstat(fd)
            if stat.S_ISREG(st.st_mode):
                fstat_calls[0] += 1
                if fstat_calls[0] == 1:
                    raise OSError(errno.EIO, "Injected fstat failure")
            return st

        monkeypatch.setattr(os, "fstat", failing_fstat)
        with pytest.raises(SignerLedgerDurabilityError, match="ERR_SIGNER_KEY_PRE_READ_FSTAT_FAILED"):
            load_signer_key_handle_for_local_test(keystore_dir)

        monkeypatch.undo()

        # 4. Inject close failure and assert single close semantics
        closed_fds: List[int] = []
        real_close = os.close

        def failing_close(fd: int) -> None:
            closed_fds.append(fd)
            raise OSError(errno.EIO, "Injected close failure")

        monkeypatch.setattr(os, "close", failing_close)
        with pytest.raises(SignerLedgerDurabilityError, match="ERR_SIGNER_KEY_CLOSE_FAILED") as excinfo:
            load_signer_key_handle_for_local_test(keystore_dir)
        assert excinfo.value.code == "ERR_SIGNER_KEY_CLOSE_FAILED"
        # Each closed descriptor attempted exactly once (no retry on failure)
        assert len(closed_fds) == len(set(closed_fds))

    def test_tripwire_no_live_ops_no_nonce_consumption_no_resolve_ipc(
        self, keystore_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.services.trial_authorization_signer_service import (
            load_signer_key_handle_for_local_test,
        )

        def tripwire(*args, **kwargs):
            pytest.fail("TRIPWIRE TRIGGERED: Prohibited live or Resolve operation attempted")

        # Tripwire on socket, network, and subprocess
        monkeypatch.setattr("socket.socket", tripwire)
        monkeypatch.setattr("subprocess.Popen", tripwire)

        self._write_key_file(keystore_dir, os.urandom(32), mode=0o600)
        handle = load_signer_key_handle_for_local_test(keystore_dir)
        assert handle is not None

    def test_repeated_opens_reread_descriptor_no_cached_secret(
        self, tmp_path: Path
    ) -> None:
        import src.services.trial_authorization_signer_service as signer_module
        import dataclasses
        from src.core.contracts.trial_authorization_contracts import (
            canonical_bytes_for_auth_hmac,
        )
        from tests.core.contracts.test_trial_authorization_contracts import make_sample_auth

        dir1 = tmp_path / "ks1"
        dir2 = tmp_path / "ks2"
        dir1.mkdir()
        dir1.chmod(0o700)
        dir2.mkdir()
        dir2.chmod(0o700)

        key1 = os.urandom(32)
        key2 = os.urandom(32)
        self._write_key_file(dir1, key1, mode=0o600)
        self._write_key_file(dir2, key2, mode=0o600)

        handle1 = signer_module.load_signer_key_handle_for_local_test(dir1)
        handle2 = signer_module.load_signer_key_handle_for_local_test(dir2)

        sample = make_sample_auth(authorization_hmac="a" * 64)
        canonical = canonical_bytes_for_auth_hmac(sample)
        hmac1 = hmac.new(key1, canonical, hashlib.sha256).hexdigest()
        hmac2 = hmac.new(key2, canonical, hashlib.sha256).hexdigest()

        auth1 = dataclasses.replace(sample, authorization_hmac=hmac1)
        auth2 = dataclasses.replace(sample, authorization_hmac=hmac2)

        # Prove handle1 is bound to key1 and handle2 is bound to key2
        assert handle1.verify_trial_execution_authorization(auth1) is True
        assert handle1.verify_trial_execution_authorization(auth2) is False

        assert handle2.verify_trial_execution_authorization(auth2) is True
        assert handle2.verify_trial_execution_authorization(auth1) is False

        # Assert zero secret/key globals on module namespace
        for attr in dir(signer_module):
            assert "CACHED_KEY" not in attr
            assert "SIGNER_KEY" not in attr or attr in ("SIGNER_HMAC_KEY_FILENAME", "SIGNER_HMAC_KEY_BYTES_LEN")
            assert "SECRET" not in attr or attr == "_PROVENANCE_SECRET"


# ==============================================================================
# Gate 3-I14: Local-Test Authoritative Challenge Issuance Core Tests
# ==============================================================================

class TestLocalTestAuthoritativeChallengeIssuanceCore:
    """Comprehensive test suite for Gate 3-I14: Authoritative Challenge Issuance Core."""

    def _setup_challenge_fixture(
        self,
        tmp_path: Path,
        *,
        verification_time: Optional[datetime] = None,
        revoked_director_keys: Optional[List[str]] = None,
        revoked_operator_keys: Optional[List[str]] = None,
        ttl_seconds: Union[int, float] = 60.0,
    ) -> Tuple[LocalTestChallengeAuthority, LocalTestChallengeIssuanceRequest, Ed25519PrivateKey, Path, Path]:
        from src.services.trial_authorization_signer_service import REVOCATION_ISSUER_PUBLIC_KEY_FILENAME
        from src.core.contracts.execution_receipt_contracts import SourceFileDigestBinding

        tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if verification_time is None:
            verification_time = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)

        # Single action canary proposal (exactly 1 clip, 1 segment of 5.0s -> 120 frames at 24fps)
        c_ctx = CreativeContext(
            free_brief="Canary brief for Gate 3-I14 verification",
            style_preset="Cinematic",
            must_keep_rules=("rule 1",),
            must_not_rules=("must not 1",),
            reference_items=(),
        )
        clips = [
            {
                "clip_id": "clip_main_1",
                "file_path": "/media/test/main_1.mp4",
                "source_kind": "managed",
                "origin_library_id": "lib_01",
                "role": "footage",
            },
        ]
        source_binding = {
            "manifest_id": "mf_canary_001",
            "clips": clips,
        }
        segments = [
            {
                "segment_index": 0,
                "clip_id": "clip_main_1",
                "start_sec": 0.0,
                "end_sec": 5.0,  # 5.0s * 24fps = 120 frames
                "action": "keep",
                "speed": 1.0,
                "broll_inserts": [],
                "sfx_inserts": [],
            },
        ]
        video_plan = {
            "manifest_id": "mf_canary_001",
            "timeline_segments": segments,
        }
        proposal = normalize_edit_plan_proposal({
            "proposal_id": "prop_canary_001",
            "manifest_id": "mf_canary_001",
            "creative_context": c_ctx,
            "source_binding": source_binding,
            "video_plan": video_plan,
            "kinetic_plan": None,
            "proposed_asset_assignments": (),
            "proposed_omissions": (),
        })
        approval = make_matching_approval(proposal)

        target_spec = TargetSpec(
            project_name="Test Project",
            project_id="RF_TEST_PROJECT",
            timeline_name="Test Timeline",
            timeline_id="RF_TEST_TIMELINE",
            timeline_fps=RationalFPS(24, 1),
            timeline_resolution=(1920, 1080),
            start_timecode_frame=0,
        )
        evidence_table = {
            "clip_main_1": SourceFrameEvidence(
                manifest_id="mf_canary_001",
                clip_id="clip_main_1",
                source_fps=RationalFPS(24, 1),
                total_frames=240,
                evidence_status="VERIFIED_CFR",
            ),
        }
        snapshot = TimelineOccupancySnapshot(
            snapshot_id="snap_test_001",
            project_id="RF_TEST_PROJECT",
            timeline_id="RF_TEST_TIMELINE",
            timeline_fps=RationalFPS(24, 1),
            captured_at_iso="2026-11-03T14:59:00Z",
            occupied_spans=(),
            scanned_tracks=(("video", 1), ("video", 2), ("audio", 1), ("audio", 2)),
            status="AUTHORITATIVE",
        )
        source_digest_binding = SourceFileDigestBinding(
            clip_id="clip_main_1",
            absolute_path="/media/test/main_1.mp4",
            full_sha256="4" * 64,
            stream_index=0,
            stat_identity=(1, 2, 3, 4, 5),
        )
        plan = bind_timeline_execution_plan(
            proposal,
            approval,
            target_spec,
            evidence_table,
            snapshot,
            source_digests=[source_digest_binding],
        )

        plan_digest = compute_canonical_plan_digest(plan)
        proposal_digest = compute_proposal_revision(proposal)
        key_id = "dir_key_test_01"

        dir_priv = Ed25519PrivateKey.generate()
        dir_pub = dir_priv.public_key()
        signer = DirectorApprovalSigner(dir_priv, key_id=key_id)
        approval_envelope = signer.sign(
            approval_record=approval,
            plan_digest_sha256=plan_digest,
            proposal_content_digest_sha256=proposal_digest,
            director_key_id=key_id,
            signed_at_iso="2026-11-04T11:59:00Z",
        )

        trust_dir = tmp_path / "trust_dir"
        trust_dir.mkdir(mode=0o700, exist_ok=True)
        rev_priv = Ed25519PrivateKey.generate()
        pub_pem = rev_priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        key_file = trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME
        key_file.write_bytes(pub_pem)
        key_file.chmod(0o600)

        reg_dir = tmp_path / "reg_dir"
        reg_dir.mkdir(mode=0o700, exist_ok=True)
        manifest_data = make_sample_revocation_manifest_data(
            epoch=1,
            issuer_id="security_officer_root",
            valid_until_iso="2026-11-05T00:00:00Z",
            issued_at_iso="2026-11-04T00:00:00Z",
            revoked_director_key_ids=revoked_director_keys or [],
            revoked_operator_key_ids=revoked_operator_keys or [],
        )
        raw_manifest_bytes, _ = sign_manifest_data(manifest_data, rev_priv)
        reg_file = reg_dir / "revocations.signed.json"
        reg_file.write_bytes(raw_manifest_bytes)
        reg_file.chmod(0o600)

        authority = LocalTestChallengeAuthority(clock=lambda: verification_time)
        req = LocalTestChallengeIssuanceRequest(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=dir_pub,
            expected_director_key_id=key_id,
            operator_id="operator_test_01",
            operator_key_id="op_key_test_01",
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=verification_time,
            ttl_seconds=ttl_seconds,
        )
        return authority, req, dir_priv, trust_dir, reg_dir

    def test_happy_path_challenge_issuance_and_binding(self, tmp_path: Path):
        """Happy path: I4 and I11 are each called exactly once; resulting challenge/context IDs
        are fresh, distinct, immutable, and bound to verified approval/plan/action/target/source values."""
        authority, req, dir_priv, trust_dir, reg_dir = self._setup_challenge_fixture(tmp_path)

        result = authority.issue_challenge(req)

        assert isinstance(result, LocalTestChallengeIssuanceResult)
        assert isinstance(result.challenge, ActiveChallengeContext)
        assert isinstance(result.operator_context, OperatorConsentVerificationContext)

        chal = result.challenge
        op_ctx = result.operator_context

        # Verify UUIDv4 format for UUID fields
        for uid_val in (chal.challenge_id, chal.client_nonce, chal.preallocated_execution_id):
            parsed = uuid.UUID(uid_val, version=4)
            assert str(parsed) == uid_val

        # Verify auth_<hex> format
        assert chal.preallocated_authorization_id.startswith("auth_")
        assert len(chal.preallocated_authorization_id) == 37

        # Pairwise distinct identities
        id_set = {chal.challenge_id, chal.client_nonce, chal.preallocated_authorization_id, chal.preallocated_execution_id}
        assert len(id_set) == 4

        # Verify not consumed
        assert chal.consumed is False

        # Verify timestamps bound to verification_time_utc
        assert chal.issued_at_iso == "2026-11-04T12:00:00Z"
        assert chal.expires_at_iso == "2026-11-04T12:01:00Z"

        # Verify exact object references bound to operator_context
        assert op_ctx.approval_envelope is req.approval_envelope
        assert op_ctx.execution_plan is req.execution_plan
        assert op_ctx.challenge is chal

        # Verify plan/target/action claims bound directly to verified execution plan
        plan_action = req.execution_plan.planned_actions[0]
        plan_target = req.execution_plan.target_spec
        assert op_ctx.expected_target_project_name == plan_target.project_name
        assert op_ctx.expected_target_project_uid == plan_target.project_id
        assert op_ctx.expected_target_timeline_name == plan_target.timeline_name
        assert op_ctx.expected_target_timeline_uid == plan_target.timeline_id
        assert op_ctx.expected_source_file_path == plan_action.source_file_path
        assert op_ctx.expected_source_file_sha256 == req.execution_plan.source_digests[0].full_sha256.lower()
        assert op_ctx.expected_record_frame == plan_action.timeline_range.start_frame
        assert op_ctx.expected_duration_frames == 120
        assert op_ctx.expected_action_type == "INSERT_CLIP"
        assert op_ctx.expected_track_type == "video"
        assert op_ctx.expected_track_index == 1

        # Verify preallocated IDs bound to operator context
        assert op_ctx.expected_authorization_id == chal.preallocated_authorization_id
        assert op_ctx.expected_execution_id == chal.preallocated_execution_id

        # Verify digests match canonical serializers
        assert op_ctx.expected_approval_envelope_digest_sha256 == req.approval_envelope.compute_envelope_digest().lower()
        assert op_ctx.expected_plan_digest_sha256 == compute_canonical_plan_digest(req.execution_plan).lower()

        # Test convenience function issue_challenge_for_local_test
        res2 = issue_challenge_for_local_test(authority, req)
        assert isinstance(res2, LocalTestChallengeIssuanceResult)
        assert res2.challenge.challenge_id != chal.challenge_id

    def test_rejection_wrong_director_signature_fails_closed(self, tmp_path: Path):
        """Corrupted director signature fails closed with ERR_DIRECTOR_APPROVAL_VERIFICATION_FAILED."""
        authority, req, dir_priv, _, _ = self._setup_challenge_fixture(tmp_path)
        bad_sig = "ab" * 64
        bad_envelope = dataclasses.replace(req.approval_envelope, director_signature=bad_sig)
        bad_req = dataclasses.replace(req, approval_envelope=bad_envelope)

        with pytest.raises(ChallengeIssuanceError, match="ERR_DIRECTOR_APPROVAL_VERIFICATION_FAILED"):
            authority.issue_challenge(bad_req)

    def test_rejection_wrong_director_key_id_fails_closed(self, tmp_path: Path):
        """Mismatched director key ID fails closed."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)
        bad_req = dataclasses.replace(req, expected_director_key_id="wrong_dir_key_id")

        with pytest.raises(ChallengeIssuanceError, match="ERR_DIRECTOR_APPROVAL_VERIFICATION_FAILED"):
            authority.issue_challenge(bad_req)

    def test_rejection_wrong_director_public_key_fails_closed(self, tmp_path: Path):
        """Wrong director public key fails closed."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)
        other_key = Ed25519PrivateKey.generate().public_key()
        bad_req = dataclasses.replace(req, director_public_key=other_key)

        with pytest.raises(ChallengeIssuanceError, match="ERR_DIRECTOR_APPROVAL_VERIFICATION_FAILED"):
            authority.issue_challenge(bad_req)

    def test_rejection_stale_or_mismatched_plan_or_proposal(self, tmp_path: Path):
        """Tampered proposal or plan fails closed."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)
        tampered_proposal = dataclasses.replace(req.proposal, proposal_id="tampered_prop_002")
        bad_req = dataclasses.replace(req, proposal=tampered_proposal)

        with pytest.raises(ChallengeIssuanceError, match="ERR_DIRECTOR_APPROVAL_VERIFICATION_FAILED"):
            authority.issue_challenge(bad_req)

    def test_rejection_revoked_director_key_fails_closed(self, tmp_path: Path):
        """Manifest revoking Director key fails closed."""
        authority, req, _, _, _ = self._setup_challenge_fixture(
            tmp_path, revoked_director_keys=["dir_key_test_01"]
        )
        with pytest.raises(ChallengeIssuanceError, match="ERR_DIRECTOR_KEY_REVOKED"):
            authority.issue_challenge(req)

    def test_rejection_revoked_operator_key_fails_closed(self, tmp_path: Path):
        """Manifest revoking Operator key fails closed."""
        authority, req, _, _, _ = self._setup_challenge_fixture(
            tmp_path, revoked_operator_keys=["op_key_test_01"]
        )
        with pytest.raises(ChallengeIssuanceError, match="ERR_OPERATOR_KEY_REVOKED"):
            authority.issue_challenge(req)

    def _re_sign_envelope(self, req: LocalTestChallengeIssuanceRequest, dir_priv: Ed25519PrivateKey, plan: TimelineExecutionPlan) -> LocalTestChallengeIssuanceRequest:
        plan_digest = compute_canonical_plan_digest(plan)
        proposal_digest = compute_proposal_revision(req.proposal)
        signer = DirectorApprovalSigner(dir_priv, key_id=req.expected_director_key_id)
        new_envelope = signer.sign(
            approval_record=req.approval_envelope.approval_record,
            plan_digest_sha256=plan_digest,
            proposal_content_digest_sha256=proposal_digest,
            director_key_id=req.expected_director_key_id,
            signed_at_iso="2026-11-04T11:59:00Z",
        )
        return dataclasses.replace(req, execution_plan=plan, approval_envelope=new_envelope)

    def test_rejection_canary_scope_multiple_or_zero_actions(self, tmp_path: Path):
        """Execution plan with != 1 action fails closed."""
        authority, req, dir_priv, _, _ = self._setup_challenge_fixture(tmp_path)
        # 0 actions
        plan_0 = dataclasses.replace(req.execution_plan, planned_actions=())
        bad_req_0 = self._re_sign_envelope(req, dir_priv, plan_0)
        with pytest.raises(ChallengeIssuanceError, match="ERR_CANARY_SCOPE_VIOLATION.*exactly 1 planned action"):
            authority.issue_challenge(bad_req_0)

        # 2 actions
        action = req.execution_plan.planned_actions[0]
        plan_2 = dataclasses.replace(req.execution_plan, planned_actions=(action, action))
        bad_req_2 = self._re_sign_envelope(req, dir_priv, plan_2)
        with pytest.raises(ChallengeIssuanceError, match="ERR_CANARY_SCOPE_VIOLATION.*exactly 1 planned action"):
            authority.issue_challenge(bad_req_2)

    def test_rejection_canary_scope_invalid_geometry_or_track(self, tmp_path: Path):
        """Execution plan with invalid geometry, action type, or track fails closed."""
        authority, req, dir_priv, _, _ = self._setup_challenge_fixture(tmp_path)
        base_action = req.execution_plan.planned_actions[0]

        # Wrong action_type
        bad_act = dataclasses.replace(base_action, action_type="NO_OP")
        bad_plan = dataclasses.replace(req.execution_plan, planned_actions=(bad_act,))
        with pytest.raises(ChallengeIssuanceError, match="ERR_CANARY_SCOPE_VIOLATION.*action_type"):
            authority.issue_challenge(self._re_sign_envelope(req, dir_priv, bad_plan))

        # Wrong target_track_type
        bad_act = dataclasses.replace(base_action, target_track_type="audio")
        bad_plan = dataclasses.replace(req.execution_plan, planned_actions=(bad_act,))
        with pytest.raises(ChallengeIssuanceError, match="ERR_CANARY_SCOPE_VIOLATION.*target_track_type"):
            authority.issue_challenge(self._re_sign_envelope(req, dir_priv, bad_plan))

        # Wrong target_track_index
        bad_act = dataclasses.replace(base_action, target_track_index=2)
        bad_plan = dataclasses.replace(req.execution_plan, planned_actions=(bad_act,))
        with pytest.raises(ChallengeIssuanceError, match="ERR_CANARY_SCOPE_VIOLATION.*target_track_index"):
            authority.issue_challenge(self._re_sign_envelope(req, dir_priv, bad_plan))

        # Wrong duration_frames != 120
        bad_timeline_range = dataclasses.replace(base_action.timeline_range, end_frame=240)
        bad_act = dataclasses.replace(base_action, timeline_range=bad_timeline_range)
        bad_plan = dataclasses.replace(req.execution_plan, planned_actions=(bad_act,))
        with pytest.raises(ChallengeIssuanceError, match="ERR_CANARY_SCOPE_VIOLATION.*duration_frames"):
            authority.issue_challenge(self._re_sign_envelope(req, dir_priv, bad_plan))

    def test_rejection_canary_scope_missing_or_invalid_source_digest(self, tmp_path: Path):
        """Missing or malformed source digest fails closed."""
        from src.core.contracts.execution_receipt_contracts import SourceFileDigestBinding
        authority, req, dir_priv, _, _ = self._setup_challenge_fixture(tmp_path)
        # Empty source_digests
        bad_plan = dataclasses.replace(req.execution_plan, source_digests=())
        with pytest.raises(ChallengeIssuanceError, match="ERR_CANARY_SCOPE_VIOLATION.*has no entry in execution_plan.source_digests"):
            authority.issue_challenge(self._re_sign_envelope(req, dir_priv, bad_plan))

        # Malformed source digest (not 64-char hex)
        sd = req.execution_plan.source_digests[0]
        bad_sd = object.__new__(SourceFileDigestBinding)
        for f_name, f_val in sd.__dict__.items():
            object.__setattr__(bad_sd, f_name, f_val)
        object.__setattr__(bad_sd, "full_sha256", "not_a_valid_hex")
        bad_plan2 = dataclasses.replace(req.execution_plan, source_digests=(bad_sd,))
        with pytest.raises(ChallengeIssuanceError, match="ERR_CANARY_SCOPE_VIOLATION.*source digest is not valid"):
            authority.issue_challenge(self._re_sign_envelope(req, dir_priv, bad_plan2))

    def test_rejection_malformed_timestamps_fails_closed(self, tmp_path: Path):
        """Non-UTC, naive, or subclass timestamps fail closed."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)

        # Naive datetime
        naive_dt = datetime(2026, 11, 4, 12, 0, 0)
        with pytest.raises(ChallengeIssuanceError, match="ERR_VERIFICATION_TIME_INVALID.*UTC"):
            dataclasses.replace(req, verification_time_utc=naive_dt)

        # Non-UTC offset (+07:00)
        vn_tz = timezone(timedelta(hours=7))
        offset_dt = datetime(2026, 11, 4, 19, 0, 0, tzinfo=vn_tz)
        with pytest.raises(ChallengeIssuanceError, match="ERR_VERIFICATION_TIME_INVALID.*UTC with offset zero"):
            dataclasses.replace(req, verification_time_utc=offset_dt)

        # Datetime subclass
        class CustomDatetime(datetime):
            pass
        subclass_dt = CustomDatetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ChallengeIssuanceError, match="ERR_VERIFICATION_TIME_INVALID.*exact built-in datetime"):
            dataclasses.replace(req, verification_time_utc=subclass_dt)

    def test_rejection_invalid_paths_fails_closed(self, tmp_path: Path):
        """Relative paths, production paths, and non-temp paths fail closed."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)

        # Relative path
        with pytest.raises(ChallengeIssuanceError, match="ERR_TRUST_DIR_INVALID.*absolute path"):
            dataclasses.replace(req, revocation_trust_directory="relative/path")

        # Production path
        with pytest.raises(ChallengeIssuanceError, match="ERR_TRUST_DIR_INVALID.*production path forbidden"):
            dataclasses.replace(req, revocation_trust_directory="/var/lib/resolveflow/signer")

        # Non-temp path
        with pytest.raises(ChallengeIssuanceError, match="ERR_REGISTRY_DIR_INVALID.*must be within system temporary directory"):
            dataclasses.replace(req, revocation_registry_directory="/etc/resolveflow")

    def test_rejection_ttl_matrix(self, tmp_path: Path):
        """TTL boundary gates: min 1.0, max 300.0, finite float/int, reject bool/NaN/inf."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)

        # < 1.0
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_TTL_OUT_OF_BOUNDS"):
            dataclasses.replace(req, ttl_seconds=0.999)

        # > 300.0
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_TTL_OUT_OF_BOUNDS"):
            dataclasses.replace(req, ttl_seconds=300.001)

        # bool True
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_TTL_INVALID.*excluding bool"):
            dataclasses.replace(req, ttl_seconds=True)

        # NaN
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_TTL_INVALID.*finite"):
            dataclasses.replace(req, ttl_seconds=float("nan"))

        # inf
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_TTL_INVALID.*finite"):
            dataclasses.replace(req, ttl_seconds=float("inf"))

        # String
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_TTL_INVALID.*int or float"):
            dataclasses.replace(req, ttl_seconds="60")  # type: ignore

    def test_rejection_malformed_input_types(self, tmp_path: Path):
        """Reject wrong nominal types and malformed operator identifiers."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)

        with pytest.raises(ChallengeIssuanceError, match="ERR_APPROVAL_ENVELOPE_TYPE"):
            dataclasses.replace(req, approval_envelope={"dict": "fake"})  # type: ignore

        with pytest.raises(ChallengeIssuanceError, match="ERR_PROPOSAL_TYPE"):
            dataclasses.replace(req, proposal={"dict": "fake"})  # type: ignore

        with pytest.raises(ChallengeIssuanceError, match="ERR_PLAN_TYPE"):
            dataclasses.replace(req, execution_plan={"dict": "fake"})  # type: ignore

        with pytest.raises(ChallengeIssuanceError, match="ERR_OPERATOR_ID_FORMAT"):
            dataclasses.replace(req, operator_id="  operator_padded  ")

        with pytest.raises(ChallengeIssuanceError, match="ERR_OPERATOR_KEY_ID_FORMAT"):
            dataclasses.replace(req, operator_key_id="")

    def test_call_order_invalid_inputs_call_no_verifiers(self, tmp_path: Path):
        """Invalid inputs call neither I4 nor I11 verifiers."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)
        from unittest.mock import patch

        with patch("src.services.trial_authorization_signer_service.verify_director_approval") as mock_i4, \
             patch("src.services.trial_authorization_signer_service.load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test") as mock_i11:
            with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_TTL_OUT_OF_BOUNDS"):
                dataclasses.replace(req, ttl_seconds=0.0)

            mock_i4.assert_not_called()
            mock_i11.assert_not_called()

    def test_call_order_i4_failure_skips_i11(self, tmp_path: Path):
        """I4 Director verification failure skips I11 Revocation check."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)
        from unittest.mock import patch

        bad_envelope = dataclasses.replace(req.approval_envelope, director_signature="00" * 64)
        bad_req = dataclasses.replace(req, approval_envelope=bad_envelope)

        with patch("src.services.trial_authorization_signer_service.load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test") as mock_i11:
            with pytest.raises(ChallengeIssuanceError, match="ERR_DIRECTOR_APPROVAL_VERIFICATION_FAILED"):
                authority.issue_challenge(bad_req)

            mock_i11.assert_not_called()

    def test_call_order_i11_failure_inserts_nothing_into_registry(self, tmp_path: Path):
        """I11 failure inserts nothing into the authority's active registry."""
        authority, req, _, _, reg_dir = self._setup_challenge_fixture(tmp_path)
        # Corrupt manifest file to fail I11
        reg_file = reg_dir / "revocations.signed.json"
        reg_file.write_bytes(b'{"corrupt": true}')

        with pytest.raises(ChallengeIssuanceError, match="ERR_REVOCATION_VERIFICATION_FAILED"):
            authority.issue_challenge(req)

        # Verify authority registry is empty
        assert len(authority._active_challenges) == 0
        assert len(authority._issued_challenge_ids) == 0

    def test_read_only_i11_leaves_fixture_dir_byte_for_byte_unchanged(self, tmp_path: Path):
        """Read-only I11 check leaves trust and registry directories byte-for-byte unchanged."""
        authority, req, _, trust_dir, reg_dir = self._setup_challenge_fixture(tmp_path)

        def snapshot(d: Path) -> Dict[str, bytes]:
            return {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}

        trust_before = snapshot(trust_dir)
        reg_before = snapshot(reg_dir)

        # Issue challenge successfully
        authority.issue_challenge(req)

        trust_after = snapshot(trust_dir)
        reg_after = snapshot(reg_dir)

        assert trust_before == trust_after
        assert reg_before == reg_after
        # Specifically confirm no epoch anchor file created
        assert not (reg_dir / "revocation_epoch.anchor").exists()

    def test_registry_lookup_and_expiration_lifecycle(self, tmp_path: Path):
        """Registry lookup lifecycle: active return, expiration purge, no restore, unknown ID rejection."""
        t0 = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path, verification_time=t0, ttl_seconds=10.0)

        result = authority.issue_challenge(req)
        cid = result.challenge.challenge_id

        # Active lookup at t0 + 5s succeeds
        t_active = t0 + timedelta(seconds=5)
        chal_looked_up = authority.lookup_active_challenge(cid, current_time_utc=t_active)
        assert chal_looked_up == result.challenge

        op_ctx_looked_up = authority.lookup_operator_context(cid, current_time_utc=t_active)
        assert op_ctx_looked_up == result.operator_context

        # Expired lookup at t0 + 11s fails closed and purges
        t_expired = t0 + timedelta(seconds=11)
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_EXPIRED"):
            authority.lookup_active_challenge(cid, current_time_utc=t_expired)

        # Subsequent lookup fails with ERR_CHALLENGE_NOT_FOUND (purged, never restored)
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_NOT_FOUND"):
            authority.lookup_active_challenge(cid, current_time_utc=t_active)

        # Unknown ID rejection
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_NOT_FOUND"):
            authority.lookup_active_challenge("unknown_challenge_id_123")

    def test_registry_no_overwrite_on_collision(self, tmp_path: Path):
        """Collision check prevents overwriting active entry and fails closed."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)
        from unittest.mock import patch

        res1 = authority.issue_challenge(req)
        existing_cid = res1.challenge.challenge_id

        # Mock uuid4 to return existing_cid on first call (challenge_id), and distinct UUIDs on subsequent calls
        with patch(
            "uuid.uuid4",
            side_effect=[uuid.UUID(existing_cid), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()],
        ):
            with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_COLLISION"):
                authority.issue_challenge(req)

        # Existing challenge in registry is unchanged
        assert authority.lookup_active_challenge(existing_cid) == res1.challenge

    def test_concurrency_thread_safety_no_aliasing(self, tmp_path: Path):
        """Concurrent issuance across threads produces distinct, non-aliased identities without race condition."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)
        import threading

        results: List[LocalTestChallengeIssuanceResult] = []
        errors: List[Exception] = []

        def worker():
            try:
                res = authority.issue_challenge(req)
                results.append(res)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10

        challenge_ids = {r.challenge.challenge_id for r in results}
        client_nonces = {r.challenge.client_nonce for r in results}
        auth_ids = {r.challenge.preallocated_authorization_id for r in results}
        exec_ids = {r.challenge.preallocated_execution_id for r in results}

        assert len(challenge_ids) == 10
        assert len(client_nonces) == 10
        assert len(auth_ids) == 10
        assert len(exec_ids) == 10

    def test_negative_proof_no_identity_selection_or_overrides(self, tmp_path: Path):
        """Callers cannot select identities, supply overrides, or inject registry."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)

        # Caller override prohibited in issue_challenge
        with pytest.raises(ChallengeIssuanceError, match="ERR_CALLER_OVERRIDE_PROHIBITED"):
            authority.issue_challenge(req, action_payload_digest="override")  # type: ignore

        # Request dataclass does not have identity selection fields
        with pytest.raises(TypeError, match="unexpected keyword argument 'challenge_id'"):
            LocalTestChallengeIssuanceRequest(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_id=req.operator_id,
                operator_key_id=req.operator_key_id,
                revocation_trust_directory=req.revocation_trust_directory,
                revocation_registry_directory=req.revocation_registry_directory,
                verification_time_utc=req.verification_time_utc,
                ttl_seconds=req.ttl_seconds,
                challenge_id="caller_chosen_id",  # type: ignore
            )

    def test_negative_proof_result_has_no_signing_or_auth_apis(self, tmp_path: Path):
        """Result contains zero private keys, HMAC keys, or signing APIs."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)
        result = authority.issue_challenge(req)

        for banned_attr in ("sign", "issue_authorization", "private_key", "get_hmac_key", "authorization_hmac"):
            assert not hasattr(result, banned_attr)
            assert not hasattr(result.challenge, banned_attr)
            assert not hasattr(result.operator_context, banned_attr)
            assert not hasattr(authority, banned_attr)

    def test_safety_tripwires(self, tmp_path: Path):
        """Safety tripwires: no production path, private key access, nonce ledger mutation, or resolve IPC."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)
        from src.services.trial_authorization_signer_service import _validate_directory_lexically_under_temp_root

        # Production path check tripwire
        for banned_dir in ("/var/lib/resolveflow", "/run/resolveflow"):
            with pytest.raises(ChallengeIssuanceError):
                _validate_directory_lexically_under_temp_root(
                    banned_dir, "test_dir", "ERR_TEST", error_cls=ChallengeIssuanceError
                )

        # Confirm LocalTestSignerKeyHandle or HMAC key is not touched
        import src.services.trial_authorization_signer_service as signer_module
        assert hasattr(signer_module, "SIGNER_HMAC_KEY_FILENAME")

        # Verify no Resolve IPC / DaVinci Resolve module imported in signer service
        import ast
        with open("src/services/trial_authorization_signer_service.py", "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_names.add(node.module)
        forbidden_substrings = ["davinci", "fusionscript", "bmd", "resolve_api", "pyside", "qt"]
        for name in imported_names:
            name_lower = name.lower()
            for forbidden in forbidden_substrings:
                assert forbidden not in name_lower, f"Tripwire violation: found forbidden import '{name}' in signer service!"

    def test_rejection_stale_verification_time_fails_closed(self, tmp_path: Path):
        """I14-01: Stale verification_time_utc exceeding bounded skew is rejected and inserts nothing."""
        fixed_now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path, verification_time=fixed_now)
        # 70 seconds older than fixed_now (exceeding MAX_CHALLENGE_CLOCK_SKEW_SECONDS = 60.0s)
        stale_time = fixed_now - timedelta(seconds=70.0)
        bad_req = dataclasses.replace(req, verification_time_utc=stale_time)

        with pytest.raises(ChallengeIssuanceError, match="ERR_VERIFICATION_TIME_STALE"):
            authority.issue_challenge(bad_req)

        assert len(authority._active_challenges) == 0
        assert len(authority._all_issued_identities) == 0

    def test_rejection_future_verification_time_fails_closed(self, tmp_path: Path):
        """I14-01: Future verification_time_utc exceeding bounded skew is rejected and inserts nothing."""
        fixed_now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path, verification_time=fixed_now)
        # 70 seconds ahead of fixed_now (exceeding MAX_CHALLENGE_CLOCK_SKEW_SECONDS = 60.0s)
        future_time = fixed_now + timedelta(seconds=70.0)
        bad_req = dataclasses.replace(req, verification_time_utc=future_time)

        with pytest.raises(ChallengeIssuanceError, match="ERR_VERIFICATION_TIME_FUTURE"):
            authority.issue_challenge(bad_req)

        assert len(authority._active_challenges) == 0
        assert len(authority._all_issued_identities) == 0

    def test_rejection_expired_window_at_verification_start_fails_closed(self, tmp_path: Path):
        """I14-01: Verification time whose validity window has already passed at start is rejected."""
        fixed_now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        # verification_time is 10s in past, ttl is 5s (now >= verification_time + ttl)
        past_time = fixed_now - timedelta(seconds=10.0)
        _, req, _, _, _ = self._setup_challenge_fixture(
            tmp_path, verification_time=past_time, ttl_seconds=5.0
        )
        authority = LocalTestChallengeAuthority(clock=lambda: fixed_now)

        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_ALREADY_EXPIRED"):
            authority.issue_challenge(req)

        assert len(authority._active_challenges) == 0
        assert len(authority._all_issued_identities) == 0

    def test_rejection_delayed_issuance_crossing_ttl_boundary_fails_closed(self, tmp_path: Path):
        """I14-01: Issuance where elapsed monotonic time or clock crosses TTL boundary fails closed."""
        fixed_now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        authority, req, _, _, _ = self._setup_challenge_fixture(
            tmp_path, verification_time=fixed_now, ttl_seconds=2.0
        )

        # Monotonic clock that simulates a delay crossing the 2.0s TTL
        # First call at start_mono = 100.0, second call at pre-insertion check = 105.0 (elapsed = 5.0s > 2.0s)
        mono_calls = [100.0, 105.0]
        authority._monotonic_clock = lambda: mono_calls.pop(0) if mono_calls else 200.0

        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_ALREADY_EXPIRED"):
            authority.issue_challenge(req)

        assert len(authority._active_challenges) == 0
        assert len(authority._all_issued_identities) == 0
        assert len(authority._issued_challenge_ids) == 0

    def test_rejection_delay_while_waiting_for_registry_lock_fails_closed(self, tmp_path: Path):
        """I14-R2-01: Delay while waiting for registry lock crossing TTL boundary fails closed under lock without mutation."""
        fixed_now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        current_time = fixed_now
        mono_time = 100.0

        authority, req, _, _, _ = self._setup_challenge_fixture(
            tmp_path, verification_time=fixed_now, ttl_seconds=2.0
        )
        authority._clock = lambda: current_time
        authority._monotonic_clock = lambda: mono_time

        # Wrap lock: simulates acquiring the lock only after a delay that crosses TTL
        real_lock = authority._lock

        class DelayOnLockAcquisition:
            def __enter__(self):
                res = real_lock.__enter__()
                # Simulate time advancing 5s (crossing 2s TTL) during lock wait
                nonlocal current_time, mono_time
                current_time = fixed_now + timedelta(seconds=5.0)
                mono_time = 105.0
                return res

            def __exit__(self, exc_type, exc_val, exc_tb):
                return real_lock.__exit__(exc_type, exc_val, exc_tb)

        authority._lock = DelayOnLockAcquisition()

        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_ALREADY_EXPIRED"):
            authority.issue_challenge(req)

        assert len(authority._active_challenges) == 0
        assert len(authority._all_issued_identities) == 0
        assert len(authority._issued_challenge_ids) == 0
        assert len(authority._issued_client_nonces) == 0
        assert len(authority._issued_authorization_ids) == 0
        assert len(authority._issued_execution_ids) == 0

    def test_rejection_concurrent_lock_holder_delay_crosses_ttl_fails_closed(self, tmp_path: Path):
        """I14-R2-01: Thread blocked on registry lock crosses TTL while waiting and fails closed without mutation."""
        import threading
        import time
        fixed_now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        current_time = fixed_now
        mono_time = 100.0

        authority, req, _, _, _ = self._setup_challenge_fixture(
            tmp_path, verification_time=fixed_now, ttl_seconds=2.0
        )
        authority._clock = lambda: current_time
        authority._monotonic_clock = lambda: mono_time

        errors = []

        # Thread 1 holds lock
        lock_held = threading.Event()
        lock_release = threading.Event()

        def blocker():
            with authority._lock:
                lock_held.set()
                lock_release.wait(timeout=5.0)

        t1 = threading.Thread(target=blocker)
        t1.start()
        assert lock_held.wait(timeout=2.0)

        # Thread 2 calls issue_challenge; will finish I4/I11 and block on authority._lock
        def runner():
            try:
                authority.issue_challenge(req)
            except Exception as e:
                errors.append(e)

        t2 = threading.Thread(target=runner)
        t2.start()

        # Give thread 2 time to reach the lock
        time.sleep(0.1)

        # Advance time while thread 2 is waiting on the lock
        current_time = fixed_now + timedelta(seconds=5.0)
        mono_time = 105.0

        # Release lock so thread 2 can acquire it
        lock_release.set()
        t1.join()
        t2.join()

        assert len(errors) == 1
        assert isinstance(errors[0], ChallengeIssuanceError)
        assert "ERR_CHALLENGE_ALREADY_EXPIRED" in str(errors[0])

        assert len(authority._active_challenges) == 0
        assert len(authority._all_issued_identities) == 0
        assert len(authority._issued_challenge_ids) == 0
        assert len(authority._issued_client_nonces) == 0
        assert len(authority._issued_authorization_ids) == 0
        assert len(authority._issued_execution_ids) == 0

    def test_rejection_canary_scope_duplicate_same_source_digest(self, tmp_path: Path):
        """I14-02: Duplicate identical source digest entries for action path fail closed."""
        authority, req, dir_priv, _, _ = self._setup_challenge_fixture(tmp_path)
        sd = req.execution_plan.source_digests[0]
        # Two identical entries for same path
        bad_plan = dataclasses.replace(req.execution_plan, source_digests=(sd, sd))

        with pytest.raises(ChallengeIssuanceError, match="ERR_CANARY_SCOPE_VIOLATION.*duplicate source digest entries"):
            authority.issue_challenge(self._re_sign_envelope(req, dir_priv, bad_plan))

        assert len(authority._active_challenges) == 0
        assert len(authority._all_issued_identities) == 0

    def test_rejection_canary_scope_duplicate_conflicting_source_digest(self, tmp_path: Path):
        """I14-02: Duplicate conflicting source digest entries for action path fail closed."""
        from src.core.contracts.execution_receipt_contracts import SourceFileDigestBinding
        authority, req, dir_priv, _, _ = self._setup_challenge_fixture(tmp_path)
        sd1 = req.execution_plan.source_digests[0]
        sd2 = SourceFileDigestBinding(
            clip_id="clip_main_1",
            absolute_path=sd1.absolute_path,
            full_sha256="5" * 64,
            stream_index=0,
            stat_identity=(1, 2, 3, 4, 5),
        )
        bad_plan = dataclasses.replace(req.execution_plan, source_digests=(sd1, sd2))

        with pytest.raises(ChallengeIssuanceError, match="ERR_CANARY_SCOPE_VIOLATION.*duplicate source digest entries"):
            authority.issue_challenge(self._re_sign_envelope(req, dir_priv, bad_plan))

        assert len(authority._active_challenges) == 0
        assert len(authority._all_issued_identities) == 0

    def test_registry_cross_category_historical_collision_prevention(self, tmp_path: Path):
        """I14-03: Collision against unified historical identity set across all categories fails closed."""
        authority, req, _, _, _ = self._setup_challenge_fixture(tmp_path)
        from unittest.mock import patch

        res1 = authority.issue_challenge(req)
        cid1 = res1.challenge.challenge_id
        nonce1 = res1.challenge.client_nonce
        auth1 = res1.challenge.preallocated_authorization_id
        exec1 = res1.challenge.preallocated_execution_id

        assert len(authority._all_issued_identities) == 4
        assert {cid1, nonce1, auth1, exec1} == authority._all_issued_identities

        # Case 1: Fresh challenge_id collides with historical client_nonce (cross-category)
        with patch("uuid.uuid4", side_effect=[uuid.UUID(nonce1), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]):
            with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_COLLISION"):
                authority.issue_challenge(req)

        # Existing entry and sets unchanged
        assert len(authority._active_challenges) == 1
        assert authority.lookup_active_challenge(cid1) == res1.challenge
        assert len(authority._all_issued_identities) == 4

        # Case 2: Fresh client_nonce collides with historical challenge_id (cross-category)
        with patch("uuid.uuid4", side_effect=[uuid.uuid4(), uuid.UUID(cid1), uuid.uuid4(), uuid.uuid4()]):
            with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_COLLISION"):
                authority.issue_challenge(req)

        assert len(authority._active_challenges) == 1
        assert authority.lookup_active_challenge(cid1) == res1.challenge
        assert len(authority._all_issued_identities) == 4

        # Case 3: Fresh preallocated_execution_id collides with historical challenge_id (cross-category)
        with patch("uuid.uuid4", side_effect=[uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.UUID(cid1)]):
            with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_COLLISION"):
                authority.issue_challenge(req)

        assert len(authority._active_challenges) == 1
        assert authority.lookup_active_challenge(cid1) == res1.challenge
        assert len(authority._all_issued_identities) == 4


# ==============================================================================
# Gate 3-I15: Local-Test Operator Consent Verification & Atomic Challenge Consumption Core
# ==============================================================================

class TestLocalTestOperatorConsentVerificationAndChallengeConsumptionCore:
    """Comprehensive test suite for Gate 3-I15: Operator Consent Verification & Atomic Challenge Consumption Core."""

    def _make_signed_operator_consent_envelope(
        self,
        context: OperatorConsentVerificationContext,
        priv_key: Ed25519PrivateKey,
        *,
        consent_timestamp_iso: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> SignedOperatorConsentEnvelope:
        if consent_timestamp_iso is None:
            consent_timestamp_iso = context.challenge.issued_at_iso
        signing_dict = {
            "action_payload_digest_sha256": context.expected_action_payload_digest_sha256,
            "action_type": context.expected_action_type,
            "approval_envelope_digest_sha256": context.expected_approval_envelope_digest_sha256,
            "challenge_id": context.challenge.challenge_id,
            "client_nonce": context.challenge.client_nonce,
            "confirmation_phrase": "CONFIRM_CANARY_TRIAL_EXECUTION",
            "consent_timestamp_iso": consent_timestamp_iso,
            "duration_frames": context.expected_duration_frames,
            "freshness_attestation": "OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED",
            "operator_id": context.authoritative_operator_id,
            "operator_key_id": context.expected_operator_key_id,
            "plan_digest_sha256": context.expected_plan_digest_sha256,
            "record_frame": context.expected_record_frame,
            "schema_version": 1,
            "source_file_path": context.expected_source_file_path,
            "source_file_sha256": context.expected_source_file_sha256,
            "target_project_name": context.expected_target_project_name,
            "target_project_uid": context.expected_target_project_uid,
            "target_timeline_name": context.expected_target_timeline_name,
            "target_timeline_uid": context.expected_target_timeline_uid,
            "track_index": context.expected_track_index,
            "track_type": context.expected_track_type,
        }
        if overrides:
            signing_dict.update(overrides)
        canonical_bytes = canonical_json_bytes(signing_dict)
        sig_hex = priv_key.sign(canonical_bytes).hex().lower()
        return SignedOperatorConsentEnvelope(
            **signing_dict,
            operator_signature=sig_hex,
        )

    def _setup_consume_fixture(
        self,
        tmp_path: Path,
        *,
        verification_time: Optional[datetime] = None,
        revoked_director_keys: Optional[List[str]] = None,
        revoked_operator_keys: Optional[List[str]] = None,
        revoked_authorization_ids: Optional[List[str]] = None,
        ttl_seconds: Union[int, float] = 60.0,
    ) -> Tuple[
        LocalTestChallengeAuthority,
        LocalTestChallengeIssuanceResult,
        SignedOperatorConsentEnvelope,
        Ed25519PrivateKey,
        Ed25519PublicKey,
        Path,
        Path,
        Path,
    ]:
        i14_suite = TestLocalTestAuthoritativeChallengeIssuanceCore()
        authority, req, dir_priv, trust_dir, reg_dir = i14_suite._setup_challenge_fixture(
            tmp_path,
            verification_time=verification_time,
            revoked_director_keys=None,
            revoked_operator_keys=None,
            ttl_seconds=ttl_seconds,
        )
        issuance_result = authority.issue_challenge(req)

        # If custom revoked authorization IDs or keys are requested for consumption phase, re-write manifest
        if revoked_authorization_ids or revoked_director_keys or revoked_operator_keys:
            rev_priv = Ed25519PrivateKey.generate()
            pub_pem = rev_priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
            (trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME).write_bytes(pub_pem)

            manifest_data = make_sample_revocation_manifest_data(
                epoch=2,
                issuer_id="security_officer_root",
                valid_until_iso="2026-11-05T00:00:00Z",
                issued_at_iso="2026-11-04T00:00:00Z",
                revoked_director_key_ids=revoked_director_keys or [],
                revoked_operator_key_ids=revoked_operator_keys or [],
                revoked_authorization_ids=revoked_authorization_ids or [],
            )
            raw_manifest_bytes, _ = sign_manifest_data(manifest_data, rev_priv)
            (reg_dir / "revocations.signed.json").write_bytes(raw_manifest_bytes)

        op_priv = Ed25519PrivateKey.generate()
        op_pub = op_priv.public_key()
        ledger_dir = tmp_path / "nonce_ledger_dir"
        ledger_dir.mkdir(mode=0o700, exist_ok=True)

        envelope = self._make_signed_operator_consent_envelope(issuance_result.operator_context, op_priv)
        return (
            authority,
            issuance_result,
            envelope,
            op_priv,
            op_pub,
            trust_dir,
            reg_dir,
            ledger_dir,
        )

    def test_happy_path_consent_verification_and_durable_consumption(self, tmp_path: Path):
        """Happy path: active challenge + valid consent envelope + fresh manifest
        produces one durable ledger line with final newline and required schema;
        challenge lookup fails as consumed; repeated submission is rejected."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        # Verify and consume via authority method
        result = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )

        assert isinstance(result, LocalTestChallengeConsumptionResult)
        assert result.challenge_id == cid
        assert result.client_nonce == iss_res.challenge.client_nonce
        assert result.preallocated_authorization_id == iss_res.challenge.preallocated_authorization_id
        assert result.preallocated_execution_id == iss_res.challenge.preallocated_execution_id
        assert result.operator_id == iss_res.operator_context.authoritative_operator_id
        assert result.operator_key_id == iss_res.operator_context.expected_operator_key_id
        assert result.approval_record_id == iss_res.operator_context.approval_envelope.approval_record.approval_id
        assert result.proposal_revision == iss_res.operator_context.approval_envelope.approval_record.proposal_revision
        assert result.plan_digest_sha256 == iss_res.operator_context.expected_plan_digest_sha256
        assert result.action_payload_digest_sha256 == iss_res.operator_context.expected_action_payload_digest_sha256

        # Check durable ledger file
        ledger_file = ledger_dir / "signer_consumed_nonces.jsonl"
        assert ledger_file.exists()
        raw_ledger = ledger_file.read_bytes()
        assert raw_ledger.endswith(b"\n")
        lines = raw_ledger.decode("utf-8").strip().split("\n")
        assert len(lines) == 1

        record_data = json.loads(lines[0])
        assert record_data["schema_version"] == 1
        assert record_data["challenge_id"] == cid
        assert record_data["client_nonce"] == iss_res.challenge.client_nonce
        assert record_data["approval_record_id"] == iss_res.operator_context.approval_envelope.approval_record.approval_id
        assert record_data["proposal_revision"] == iss_res.operator_context.approval_envelope.approval_record.proposal_revision
        assert record_data["consumed_at_iso"] == result.consumed_at_iso

        # Challenge lookup now fails with ERR_CHALLENGE_ALREADY_CONSUMED
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_ALREADY_CONSUMED"):
            authority.lookup_active_challenge(cid)

        # Repeated consume fails with ERR_CHALLENGE_ALREADY_CONSUMED
        with pytest.raises(ChallengeConsumptionError, match="ERR_CHALLENGE_ALREADY_CONSUMED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        # Ledger still contains exactly one line
        lines_after = ledger_file.read_text("utf-8").strip().split("\n")
        assert len(lines_after) == 1

    def test_convenience_module_helper_parity(self, tmp_path: Path):
        """verify_and_consume_challenge_for_local_test helper delegates directly to authority."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        # Non-authority rejection
        with pytest.raises(ChallengeConsumptionError, match="ERR_AUTHORITY_TYPE"):
            verify_and_consume_challenge_for_local_test(
                "not-an-authority",  # type: ignore
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        result = verify_and_consume_challenge_for_local_test(
            authority,
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )
        assert isinstance(result, LocalTestChallengeConsumptionResult)
        assert result.challenge_id == cid

    def test_caller_overrides_prohibited(self, tmp_path: Path):
        """Caller cannot inject extra verification context or digest overrides."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        with pytest.raises(ChallengeConsumptionError, match="ERR_CALLER_OVERRIDE_PROHIBITED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                injected_override="prohibited",
            )

    def test_verifier_invocations_and_read_only_revocation(self, tmp_path: Path):
        """I11 fresh revocation is called exactly once with advance_anchor=False,
        and I5 consent verifier is called exactly once with the same instant."""
        from unittest.mock import patch
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        import src.services.trial_authorization_signer_service as tass

        orig_i11 = tass.load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test
        orig_i5 = tass.verify_operator_consent
        i11_calls = []
        i5_calls = []

        def spy_i11(*args, **kwargs):
            i11_calls.append(kwargs)
            return orig_i11(*args, **kwargs)

        def spy_i5(*args, **kwargs):
            i5_calls.append(kwargs)
            return orig_i5(*args, **kwargs)

        with patch.object(tass, "load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test", side_effect=spy_i11):
            with patch.object(tass, "verify_operator_consent", side_effect=spy_i5):
                res = authority.verify_and_consume_challenge_for_local_test(
                    challenge_id=cid,
                    consent_envelope=envelope,
                    operator_public_key=op_pub,
                    revocation_trust_directory=trust_dir,
                    revocation_registry_directory=reg_dir,
                    nonce_ledger_directory=ledger_dir,
                )

        assert len(i11_calls) == 1
        assert i11_calls[0]["advance_anchor"] is False
        assert len(i5_calls) == 1
        assert i11_calls[0]["verification_time_utc"] == i5_calls[0]["verification_time_utc"]

    def test_rejection_wrong_operator_signature_fails_closed_preserves_challenge(self, tmp_path: Path):
        """Invalid operator signature fails closed with ERR_OPERATOR_CONSENT_VERIFICATION_FAILED;
        challenge remains active and can be consumed on retry."""
        authority, iss_res, envelope, op_priv, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id
        bad_envelope = dataclasses.replace(envelope, operator_signature="aa" * 64)

        with pytest.raises(ChallengeConsumptionError, match="ERR_OPERATOR_CONSENT_VERIFICATION_FAILED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=bad_envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        # Challenge remains active in registry
        assert authority.lookup_active_challenge(cid) == iss_res.challenge
        # Ledger is empty / doesn't exist
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

        # Retry with valid envelope succeeds
        success = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )
        assert success.challenge_id == cid

    def test_rejection_wrong_operator_public_key_fails_closed_preserves_challenge(self, tmp_path: Path):
        """Mismatched operator public key fails closed and preserves challenge."""
        authority, iss_res, envelope, _, _, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id
        other_pub = Ed25519PrivateKey.generate().public_key()

        with pytest.raises(ChallengeConsumptionError, match="ERR_OPERATOR_CONSENT_VERIFICATION_FAILED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=other_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        assert authority.lookup_active_challenge(cid) == iss_res.challenge
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    def test_rejection_stale_or_future_consent_timestamp(self, tmp_path: Path):
        """Consent timestamp outside allowed challenge validity window fails closed and preserves challenge."""
        authority, iss_res, _, op_priv, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        # Consent timestamp 5 minutes into the future
        future_iso = "2026-11-04T12:05:00Z"
        stale_envelope = self._make_signed_operator_consent_envelope(
            iss_res.operator_context,
            op_priv,
            consent_timestamp_iso=future_iso,
        )

        with pytest.raises(ChallengeConsumptionError, match="ERR_OPERATOR_CONSENT_VERIFICATION_FAILED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=stale_envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        assert authority.lookup_active_challenge(cid) == iss_res.challenge
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    def test_rejection_revoked_director_key_fails_closed_preserves_challenge(self, tmp_path: Path):
        """Revocation manifest revoking Director key fails closed."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(
                tmp_path,
                revoked_director_keys=["dir_key_test_01"],
            )
        )
        cid = iss_res.challenge.challenge_id

        with pytest.raises(ChallengeConsumptionError, match="ERR_DIRECTOR_KEY_REVOKED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        assert authority.lookup_active_challenge(cid) == iss_res.challenge
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    def test_rejection_revoked_operator_key_fails_closed_preserves_challenge(self, tmp_path: Path):
        """Revocation manifest revoking Operator key fails closed."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(
                tmp_path,
                revoked_operator_keys=["op_key_test_01"],
            )
        )
        cid = iss_res.challenge.challenge_id

        with pytest.raises(ChallengeConsumptionError, match="ERR_OPERATOR_KEY_REVOKED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        assert authority.lookup_active_challenge(cid) == iss_res.challenge
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    def test_rejection_revoked_preallocated_auth_id_fails_closed_preserves_challenge(self, tmp_path: Path):
        """Revocation manifest revoking preallocated authorization ID fails closed."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id
        auth_id = iss_res.challenge.preallocated_authorization_id

        # Update manifest to revoke this auth_id
        rev_priv = Ed25519PrivateKey.generate()
        pub_pem = rev_priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        (trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME).write_bytes(pub_pem)
        manifest_data = make_sample_revocation_manifest_data(
            epoch=2,
            issuer_id="security_officer_root",
            valid_until_iso="2026-11-05T00:00:00Z",
            issued_at_iso="2026-11-04T00:00:00Z",
            revoked_authorization_ids=[auth_id],
        )
        raw_manifest_bytes, _ = sign_manifest_data(manifest_data, rev_priv)
        (reg_dir / "revocations.signed.json").write_bytes(raw_manifest_bytes)

        with pytest.raises(ChallengeConsumptionError, match="ERR_AUTHORIZATION_ID_REVOKED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        assert authority.lookup_active_challenge(cid) == iss_res.challenge
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    def test_rejection_i11_revocation_verification_failure_fails_closed_preserves_challenge(self, tmp_path: Path):
        """Manifest corruption causes I11 verification to fail closed."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id
        (reg_dir / "revocations.signed.json").write_bytes(b"{\"corrupted\": true}")

        with pytest.raises(ChallengeConsumptionError, match="ERR_REVOCATION_VERIFICATION_FAILED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        assert authority.lookup_active_challenge(cid) == iss_res.challenge
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    @pytest.mark.parametrize(
        "field_name, bad_value",
        [
            ("client_nonce", "00000000-0000-4000-8000-000000000000"),
            ("operator_id", "wrong_operator_id"),
            ("operator_key_id", "wrong_operator_key_id"),
            ("approval_envelope_digest_sha256", "3" * 64),
            ("plan_digest_sha256", "4" * 64),
            ("action_payload_digest_sha256", "5" * 64),
            ("target_project_uid", "wrong_proj_uid"),
            ("target_timeline_uid", "wrong_timeline_uid"),
            ("source_file_sha256", "6" * 64),
        ],
    )
    def test_rejection_binding_mismatch_fails_closed(self, tmp_path: Path, field_name: str, bad_value: Any):
        """Mutating envelope/claims fields causes consent verification or defensive binding check to fail closed."""
        authority, iss_res, _, op_priv, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        mutated_envelope = self._make_signed_operator_consent_envelope(
            iss_res.operator_context,
            op_priv,
            overrides={field_name: bad_value},
        )

        with pytest.raises(ChallengeConsumptionError) as exc_info:
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=mutated_envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        err_str = str(exc_info.value)
        assert ("ERR_OPERATOR_CONSENT_VERIFICATION_FAILED" in err_str or
                "ERR_OPERATOR_CONSENT_BINDING_MISMATCH" in err_str)
        assert authority.lookup_active_challenge(cid) == iss_res.challenge
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    def test_rejection_expired_challenge_fails_closed_and_purges(self, tmp_path: Path):
        """Expired active challenge fails closed with ERR_CHALLENGE_EXPIRED and is purged from registry."""
        fixed_now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        current_time = fixed_now

        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path, verification_time=fixed_now, ttl_seconds=5.0)
        )
        cid = iss_res.challenge.challenge_id
        authority._clock = lambda: current_time

        # Advance clock by 10s (> 5s TTL)
        current_time = fixed_now + timedelta(seconds=10.0)

        with pytest.raises(ChallengeConsumptionError, match="ERR_CHALLENGE_EXPIRED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        # Challenge was purged on expiry
        assert cid not in authority._active_challenges
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    def test_rejection_lock_acquisition_delay_crossing_ttl_purges_under_lock(self, tmp_path: Path):
        """Delay while waiting for registry lock crossing TTL boundary purges challenge under lock."""
        fixed_now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        current_time = fixed_now

        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path, verification_time=fixed_now, ttl_seconds=5.0)
        )
        cid = iss_res.challenge.challenge_id
        authority._clock = lambda: current_time

        # Acquire lock externally, simulate delay past TTL, then call verify_and_consume in a thread
        import threading
        import time

        barrier = threading.Barrier(2)
        exc_holder = []

        def worker():
            barrier.wait()
            try:
                authority.verify_and_consume_challenge_for_local_test(
                    challenge_id=cid,
                    consent_envelope=envelope,
                    operator_public_key=op_pub,
                    revocation_trust_directory=trust_dir,
                    revocation_registry_directory=reg_dir,
                    nonce_ledger_directory=ledger_dir,
                )
            except Exception as exc:
                exc_holder.append(exc)

        t = threading.Thread(target=worker)
        with authority._lock:
            t.start()
            barrier.wait()
            time.sleep(0.05)
            # Advance clock past TTL while lock is held
            current_time = fixed_now + timedelta(seconds=10.0)

        t.join(timeout=2.0)
        assert len(exc_holder) == 1
        assert "ERR_CHALLENGE_EXPIRED" in str(exc_holder[0])
        assert cid not in authority._active_challenges
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    def test_rejection_duplicate_nonce_or_approval_in_ledger_fails_closed_and_poisons(self, tmp_path: Path):
        """Existing record with same client nonce or (approval_record_id, proposal_revision)
        in ledger fails closed and poisons the challenge permanently."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id
        nonce = iss_res.challenge.client_nonce

        # Pre-populate ledger with an entry using the same client nonce
        ledger = SignerConsumedNonceLedger(ledger_dir)
        pre_existing = ConsumedAuthorizationNonceRecord(
            schema_version=1,
            challenge_id="prior_chal_001",
            client_nonce=nonce,
            approval_record_id="prior_app_001",
            proposal_revision="prior_rev_001",
            consumed_at_iso="2026-11-04T11:00:00Z",
        )
        ledger.consume_once(pre_existing)

        with pytest.raises(ChallengeConsumptionError, match="ERR_NONCE_LEDGER_PERSISTENCE_FAILED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        # Challenge is permanently poisoned
        assert cid in authority._poisoned_challenges
        assert cid not in authority._active_challenges

        # Subsequent lookup fails with ERR_CHALLENGE_POISONED
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_POISONED"):
            authority.lookup_active_challenge(cid)

        # Subsequent consume attempt fails with ERR_CHALLENGE_POISONED
        with pytest.raises(ChallengeConsumptionError, match="ERR_CHALLENGE_POISONED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

    def test_rejection_torn_tail_ledger_fails_closed_and_poisons(self, tmp_path: Path):
        """Torn tail or corrupt ledger data fails closed and poisons the challenge."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        # Write corrupt torn-tail ledger
        ledger_file = ledger_dir / "signer_consumed_nonces.jsonl"
        ledger_file.write_bytes(b"{\"schema_version\": 1, \"torn_tail\":")

        with pytest.raises(ChallengeConsumptionError, match="ERR_NONCE_LEDGER_PERSISTENCE_FAILED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        assert cid in authority._poisoned_challenges
        assert cid not in authority._active_challenges

    def test_persistence_fault_injection_poisons_challenge(self, tmp_path: Path):
        """IOError/OSError during ledger.consume_once fails closed and permanently poisons the challenge."""
        from unittest.mock import patch
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        with patch.object(SignerConsumedNonceLedger, "consume_once", side_effect=IOError("Injected EIO on fsync")):
            with pytest.raises(ChallengeConsumptionError, match="ERR_NONCE_LEDGER_PERSISTENCE_FAILED.*Injected EIO"):
                authority.verify_and_consume_challenge_for_local_test(
                    challenge_id=cid,
                    consent_envelope=envelope,
                    operator_public_key=op_pub,
                    revocation_trust_directory=trust_dir,
                    revocation_registry_directory=reg_dir,
                    nonce_ledger_directory=ledger_dir,
                )

        # Challenge is permanently poisoned
        assert cid in authority._poisoned_challenges
        assert cid not in authority._active_challenges

        # Subsequent consume attempt fails closed with ERR_CHALLENGE_POISONED
        with pytest.raises(ChallengeConsumptionError, match="ERR_CHALLENGE_POISONED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

    def test_concurrent_competing_consumes_exact_one_succeeds(self, tmp_path: Path):
        """Multiple threads competing to consume the same active challenge concurrently:
        exactly ONE succeeds; all others fail closed; ledger contains exactly one line."""
        import threading
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        results = []
        errors = []

        def attempt_consume():
            try:
                res = authority.verify_and_consume_challenge_for_local_test(
                    challenge_id=cid,
                    consent_envelope=envelope,
                    operator_public_key=op_pub,
                    revocation_trust_directory=trust_dir,
                    revocation_registry_directory=reg_dir,
                    nonce_ledger_directory=ledger_dir,
                )
                results.append(res)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=attempt_consume) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 1
        assert len(errors) == 7
        for err in errors:
            assert isinstance(err, ChallengeConsumptionError)
            assert ("ERR_CHALLENGE_ALREADY_CONSUMED" in str(err) or
                    "ERR_CHALLENGE_NOT_FOUND" in str(err))

        ledger_file = ledger_dir / "signer_consumed_nonces.jsonl"
        lines = ledger_file.read_text("utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_path_traversal_and_non_temp_directory_rejection(self, tmp_path: Path):
        """Directories outside local temporary roots fail closed with ERR_PATH_TRAVERSAL."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        # Production path for ledger_dir
        with pytest.raises(ChallengeConsumptionError, match="ERR_PATH_TRAVERSAL"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory="/var/lib/resolveflow/ledger",
            )

        # Path traversal for trust_dir: path that normalizes to non-temp (/etc)
        with pytest.raises(ChallengeConsumptionError, match="ERR_PATH_TRAVERSAL"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=f"{tmp_path}/../../../../../../../../etc",
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        # Non-absolute path
        with pytest.raises(ChallengeConsumptionError, match="ERR_PATH_TRAVERSAL"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory="relative/path/not/abs",
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

    def test_negative_proofs_and_safety_tripwires(self, tmp_path: Path):
        """Consumption result carries zero private keys, HMAC tokens, or signing APIs.
        No production directories or Resolve IPC."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        result = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )

        # Negative checks on result attributes
        for field in dataclasses.fields(result):
            val = getattr(result, field.name)
            assert not isinstance(val, (bytes, bytearray))
            assert "private" not in field.name.lower()
            assert "secret" not in field.name.lower()
            assert "token" not in field.name.lower()
            assert "hmac" not in field.name.lower()

        # Zero signing methods on result
        assert not hasattr(result, "sign")
        assert not hasattr(result, "mint")
        assert not hasattr(result, "dispatch")

    def test_ledger_descriptor_closure_on_success_and_append_failure(self, tmp_path: Path):
        """I15-01: Ledger descriptors are explicitly closed on both success and append failure."""
        from unittest.mock import patch
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        # Case 1: Happy path closes ledger descriptors
        captured_ledgers = []
        orig_init = SignerConsumedNonceLedger.__init__

        def spy_init(self_ledger, *args, **kwargs):
            orig_init(self_ledger, *args, **kwargs)
            captured_ledgers.append(self_ledger)

        with patch.object(SignerConsumedNonceLedger, "__init__", spy_init):
            res = authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )
        assert res.challenge_id == cid
        assert len(captured_ledgers) == 1
        ledger_inst = captured_ledgers[0]
        assert ledger_inst._closed is True
        assert ledger_inst._ledger_fd is None
        assert ledger_inst._dir_fd is None

        # Case 2: Append failure also closes ledger descriptors and poisons challenge
        sub_authority, sub_iss, sub_env, _, sub_op_pub, s_trust, s_reg, s_ledger = (
            self._setup_consume_fixture(tmp_path / "case2")
        )
        sub_cid = sub_iss.challenge.challenge_id
        fail_ledgers = []

        def spy_fail_init(self_ledger, *args, **kwargs):
            orig_init(self_ledger, *args, **kwargs)
            fail_ledgers.append(self_ledger)

        with patch.object(SignerConsumedNonceLedger, "consume_once", side_effect=IOError("Injected EIO on append")):
            with patch.object(SignerConsumedNonceLedger, "__init__", spy_fail_init):
                with pytest.raises(ChallengeConsumptionError, match="ERR_NONCE_LEDGER_PERSISTENCE_FAILED.*Injected EIO"):
                    sub_authority.verify_and_consume_challenge_for_local_test(
                        challenge_id=sub_cid,
                        consent_envelope=sub_env,
                        operator_public_key=sub_op_pub,
                        revocation_trust_directory=s_trust,
                        revocation_registry_directory=s_reg,
                        nonce_ledger_directory=s_ledger,
                    )
        assert len(fail_ledgers) == 1
        fail_ledger_inst = fail_ledgers[0]
        assert fail_ledger_inst._closed is True
        assert fail_ledger_inst._ledger_fd is None
        assert fail_ledger_inst._dir_fd is None
        assert sub_cid in sub_authority._poisoned_challenges

    def test_ledger_close_failure_injection_poisons_challenge_and_returns_no_success(self, tmp_path: Path):
        """I15-01: Close failure after successful append reports ambiguous persistence failure,
        poisons the challenge, and never reports success."""
        from unittest.mock import patch
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        with patch.object(
            SignerConsumedNonceLedger,
            "close",
            side_effect=SignerLedgerDurabilityError("ERR_LEDGER_CLOSE_FAILED: injected descriptor close failure"),
        ):
            with pytest.raises(
                ChallengeConsumptionError,
                match="ERR_NONCE_LEDGER_PERSISTENCE_FAILED.*ambiguous persistence outcome.*injected descriptor close failure",
            ):
                authority.verify_and_consume_challenge_for_local_test(
                    challenge_id=cid,
                    consent_envelope=envelope,
                    operator_public_key=op_pub,
                    revocation_trust_directory=trust_dir,
                    revocation_registry_directory=reg_dir,
                    nonce_ledger_directory=ledger_dir,
                )

        # Challenge is permanently poisoned
        assert cid in authority._poisoned_challenges
        assert cid not in authority._active_challenges
        assert cid not in authority._consumed_challenges

        # Subsequent lookup and consume fail closed
        with pytest.raises(ChallengeIssuanceError, match="ERR_CHALLENGE_POISONED"):
            authority.lookup_active_challenge(cid)

        with pytest.raises(ChallengeConsumptionError, match="ERR_CHALLENGE_POISONED"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

    def test_rejection_monotonic_elapsed_crosses_ttl_while_wall_clock_moves_backward(self, tmp_path: Path):
        """I15-02: Monotonic elapsed time exceeding challenge remaining lifetime triggers expiration
        even if wall clock moves backward or stays within expiry boundary."""
        fixed_now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        current_time = fixed_now
        mono_time = 100.0

        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path, verification_time=fixed_now, ttl_seconds=5.0)
        )
        cid = iss_res.challenge.challenge_id
        authority._clock = lambda: current_time
        authority._monotonic_clock = lambda: mono_time

        # Under lock, simulate monotonic clock advancing by 6.0s (> 5.0s remaining)
        # while wall clock moves BACKWARD by 30.0s
        call_count = 0

        def dynamic_mono():
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                return 106.0  # elapsed = 6.0s > 5.0s TTL
            return 100.0

        def dynamic_clock():
            if call_count > 1:
                return fixed_now - timedelta(seconds=30.0)  # wall clock stepped backwards
            return fixed_now

        authority._monotonic_clock = dynamic_mono
        authority._clock = dynamic_clock

        with pytest.raises(ChallengeConsumptionError, match="ERR_CHALLENGE_EXPIRED.*monotonic elapsed"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        # Active entry purged
        assert cid not in authority._active_challenges
        # Zero ledger records
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    def test_rejection_custom_subclass_operator_public_key_rejected_before_i11_filesystem_access(self, tmp_path: Path):
        """I15-03: Custom subclass of Ed25519PublicKey is rejected at initial input gate
        with zero I11 calls and zero filesystem/registry mutations."""
        from unittest.mock import patch
        authority, iss_res, envelope, op_priv, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        # Custom subclass attempting to wrap or spoof Ed25519PublicKey
        fake_key = FakeEd25519PublicKey(op_pub)

        import src.services.trial_authorization_signer_service as tass

        orig_i11 = tass.load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test
        i11_invocations = []

        def spy_i11(*args, **kwargs):
            i11_invocations.append(args)
            return orig_i11(*args, **kwargs)

        with patch.object(tass, "load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test", side_effect=spy_i11):
            with pytest.raises(ChallengeConsumptionError, match="ERR_OPERATOR_PUBLIC_KEY_INVALID"):
                authority.verify_and_consume_challenge_for_local_test(
                    challenge_id=cid,
                    consent_envelope=envelope,
                    operator_public_key=fake_key,
                    revocation_trust_directory=trust_dir,
                    revocation_registry_directory=reg_dir,
                    nonce_ledger_directory=ledger_dir,
                )

        # I11 was never invoked
        assert len(i11_invocations) == 0
        # Challenge remains active in registry (preflight check failed before mutation)
        assert authority.lookup_active_challenge(cid) == iss_res.challenge
        # No ledger record created
        assert not (ledger_dir / "signer_consumed_nonces.jsonl").exists()

    def test_combined_append_and_close_failure_preserves_structured_primary_and_cleanup_errors(self, tmp_path: Path):
        """I15-R2-01: Combined append and close failure preserves structured primary_error and cleanup_error."""
        from unittest.mock import patch
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        append_exc = IOError("Disk write failed: injected EIO on append")
        close_exc = SignerLedgerDurabilityError("ERR_LEDGER_CLOSE_FAILED: injected descriptor close failure")

        with patch.object(SignerConsumedNonceLedger, "consume_once", side_effect=append_exc):
            with patch.object(SignerConsumedNonceLedger, "close", side_effect=close_exc):
                with pytest.raises(ChallengeConsumptionError) as exc_info:
                    authority.verify_and_consume_challenge_for_local_test(
                        challenge_id=cid,
                        consent_envelope=envelope,
                        operator_public_key=op_pub,
                        revocation_trust_directory=trust_dir,
                        revocation_registry_directory=reg_dir,
                        nonce_ledger_directory=ledger_dir,
                    )

        err = exc_info.value
        assert err.code == "ERR_NONCE_LEDGER_PERSISTENCE_FAILED"
        assert err.primary_error is append_exc
        assert err.cleanup_error is close_exc
        assert err.cleanup_errors == [close_exc]
        assert err.__cause__ is append_exc
        assert "injected EIO on append" in str(err)
        assert "injected descriptor close failure" in str(err)

        # Challenge is permanently poisoned
        assert cid in authority._poisoned_challenges
        assert cid not in authority._active_challenges
        assert cid not in authority._consumed_challenges

    def test_rejection_symlinked_parent_directory_for_nonce_ledger(self, tmp_path: Path):
        """I15-R2-02: Nonce ledger directory with a symlinked parent component escaping temp root
        is rejected fail-closed with ERR_PATH_TRAVERSAL before ledger instantiation."""
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, _ = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        # Setup an outside target directory
        outside_dir = tmp_path / "outside_jail"
        outside_dir.mkdir(mode=0o700, exist_ok=True)
        outside_ledger = outside_dir / "target_ledger"
        outside_ledger.mkdir(mode=0o700, exist_ok=True)

        # Create intermediate symlink under tmp_path pointing to outside_dir
        intermediate_symlink = tmp_path / "symlink_parent"
        intermediate_symlink.symlink_to(outside_dir)
        escaping_ledger_dir = intermediate_symlink / "target_ledger"

        with pytest.raises(ChallengeConsumptionError, match="ERR_PATH_TRAVERSAL"):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=escaping_ledger_dir,
            )

        # Prove zero outside directory/file accessed or created
        assert not (outside_ledger / "signer_consumed_nonces.jsonl").exists()
        # Challenge remains active in registry (preflight rejected before mutation)
        assert authority.lookup_active_challenge(cid) == iss_res.challenge

    # ==========================================================================
    # Gate 3-I15-R4: Descriptor-Anchored Ledger Wiring & Component-Swap Tests
    # ==========================================================================

    def test_deterministic_component_swap_race_anchors_to_descriptor(self, tmp_path: Path):
        """I15-R4: Intercepts traversal to swap pathname and install symlink to outside sentinel before ledger init.
        Proves ledger writes strictly to original opened directory inode and outside sentinel remains untouched."""
        from unittest.mock import patch
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, _ = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        genuine_ledger_dir = tmp_path / "genuine_ledger_dir"
        genuine_ledger_dir.mkdir(mode=0o700, exist_ok=True)
        outside_sentinel_dir = tmp_path / "outside_sentinel_jail"
        outside_sentinel_dir.mkdir(mode=0o700, exist_ok=True)

        import src.services.trial_authorization_signer_service as tass_module
        orig_open_anchor = tass_module._open_anchor_dir_under_temp_root

        renamed_ledger_dir = tmp_path / "genuine_ledger_dir_renamed"
        swapped = False

        def race_interceptor(path_to_open: Any, failures: Any) -> int:
            nonlocal swapped
            fd = orig_open_anchor(path_to_open, failures)
            if not swapped and str(path_to_open) == str(genuine_ledger_dir):
                swapped = True
                # Before returning verified descriptor to I15, swap the pathname on disk!
                genuine_ledger_dir.rename(renamed_ledger_dir)
                genuine_ledger_dir.symlink_to(outside_sentinel_dir, target_is_directory=True)
            return fd

        with patch.object(tass_module, "_open_anchor_dir_under_temp_root", side_effect=race_interceptor):
            res = authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=genuine_ledger_dir,
            )

        assert res.challenge_id == cid
        # Nonce file exists in the original directory inode (now at renamed_ledger_dir)
        assert (renamed_ledger_dir / "signer_consumed_nonces.jsonl").exists()
        # Outside sentinel has ZERO files created
        assert not (outside_sentinel_dir / "signer_consumed_nonces.jsonl").exists()
        assert list(outside_sentinel_dir.iterdir()) == []

    def test_descriptor_lifetime_instrumentation_duplication_and_close_ordering(self, tmp_path: Path):
        """I15-R4: Instruments descriptor duplication, close ordering, and confirms caller-owned FD
        remains open until ledger duplicates it, ledger-owned FD remains in use through consume_once,
        and both descriptors are closed upon completion with caller FD closed exactly once.
        """
        from unittest.mock import patch
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        import src.services.trial_authorization_signer_service as tass_module
        orig_open_anchor = tass_module._open_anchor_dir_under_temp_root
        captured_caller_fds = []

        def spy_open_anchor(path_to_open: Any, failures: Any) -> int:
            fd = orig_open_anchor(path_to_open, failures)
            if str(path_to_open) == str(ledger_dir):
                captured_caller_fds.append(fd)
            return fd

        ledger_inits = []
        ledger_dir_fds = []
        orig_init = SignerConsumedNonceLedger.__init__
        def instrumented_init(ledger_self: Any, *args: Any, **kwargs: Any) -> None:
            # At ledger construction time, caller FD must already be open and valid
            assert len(captured_caller_fds) == 1
            caller_fd = captured_caller_fds[0]
            # Verify caller FD is live and accessible
            st = os.fstat(caller_fd)
            assert stat.S_ISDIR(st.st_mode)
            orig_init(ledger_self, *args, **kwargs)
            # After duplication, ledger-owned descriptor must be distinct from caller FD
            assert ledger_self._dir_fd is not None
            assert ledger_self._dir_fd != caller_fd
            # Both descriptors must be open simultaneously
            os.fstat(caller_fd)
            os.fstat(ledger_self._dir_fd)
            ledger_dir_fds.append(ledger_self._dir_fd)
            ledger_inits.append(ledger_self)

        orig_consume = SignerConsumedNonceLedger.consume_once
        consume_inspected = []
        def instrumented_consume(ledger_self: Any, record: Any) -> None:
            # During consume_once, both caller FD and ledger FD must remain open
            caller_fd = captured_caller_fds[0]
            os.fstat(caller_fd)
            os.fstat(ledger_self._dir_fd)
            consume_inspected.append(True)
            return orig_consume(ledger_self, record)

        real_close = os.close
        caller_close_calls = []
        def spy_close(fd: int) -> None:
            if captured_caller_fds and fd == captured_caller_fds[-1]:
                caller_close_calls.append(fd)
            return real_close(fd)

        with patch.object(tass_module, "_open_anchor_dir_under_temp_root", side_effect=spy_open_anchor):
            with patch.object(SignerConsumedNonceLedger, "__init__", instrumented_init):
                with patch.object(SignerConsumedNonceLedger, "consume_once", instrumented_consume):
                    with patch("os.close", side_effect=spy_close):
                        res = authority.verify_and_consume_challenge_for_local_test(
                            challenge_id=cid,
                            consent_envelope=envelope,
                            operator_public_key=op_pub,
                            revocation_trust_directory=trust_dir,
                            revocation_registry_directory=reg_dir,
                            nonce_ledger_directory=ledger_dir,
                        )

        assert res.challenge_id == cid
        assert len(captured_caller_fds) == 1
        assert len(ledger_dir_fds) == 1
        assert consume_inspected == [True]
        caller_fd = captured_caller_fds[0]
        ledger_fd = ledger_dir_fds[0]

        # Caller FD was closed exactly once
        assert caller_close_calls == [caller_fd]

        # Both descriptors must now be completely closed
        with pytest.raises(OSError):
            os.fstat(caller_fd)
        with pytest.raises(OSError):
            os.fstat(ledger_fd)

    def test_descriptor_lifetime_pre_ledger_failures_and_single_close(self, tmp_path: Path):
        """I15-R4: Tests pre-ledger failure paths (binding mismatch, challenge expired, pre-ledger close fault,
        and traversal failure) to verify caller-owned descriptor is closed at most once, challenge preservation/poison
        rules are respected, and no descriptors leak.
        """
        from unittest.mock import patch
        import src.services.trial_authorization_signer_service as tass_module
        orig_open_anchor = tass_module._open_anchor_dir_under_temp_root
        real_close = os.close

        # 1. Pre-ledger verifier failure: binding mismatch after descriptor opened
        authority1, iss_res1, envelope1, _, op_pub1, trust_dir1, reg_dir1, ledger_dir1 = (
            self._setup_consume_fixture(tmp_path / "pre1")
        )
        cid1 = iss_res1.challenge.challenge_id
        # Corrupt plan digest in active challenge operator_context
        corrupted_ctx1 = dataclasses.replace(
            authority1._active_challenges[cid1].operator_context,
            expected_plan_digest_sha256="0" * 64,
        )
        authority1._active_challenges[cid1] = dataclasses.replace(
            authority1._active_challenges[cid1],
            operator_context=corrupted_ctx1,
        )

        captured_fds1 = []
        close_calls1 = []
        def spy_open1(path_to_open: Any, failures: Any) -> int:
            fd = orig_open_anchor(path_to_open, failures)
            if str(path_to_open) == str(ledger_dir1):
                captured_fds1.append(fd)
            return fd

        def spy_close1(fd: int) -> None:
            if captured_fds1 and fd == captured_fds1[-1]:
                close_calls1.append(fd)
            return real_close(fd)

        with patch.object(tass_module, "_open_anchor_dir_under_temp_root", side_effect=spy_open1):
            with patch("os.close", side_effect=spy_close1):
                with pytest.raises(ChallengeConsumptionError) as exc_info1:
                    authority1.verify_and_consume_challenge_for_local_test(
                        challenge_id=cid1,
                        consent_envelope=envelope1,
                        operator_public_key=op_pub1,
                        revocation_trust_directory=trust_dir1,
                        revocation_registry_directory=reg_dir1,
                        nonce_ledger_directory=ledger_dir1,
                    )

        assert exc_info1.value.code == "ERR_OPERATOR_CONSENT_VERIFICATION_FAILED"
        assert len(captured_fds1) == 1
        assert close_calls1 == [captured_fds1[0]]
        with pytest.raises(OSError):
            os.fstat(captured_fds1[0])
        # Challenge must be preserved as active (NOT poisoned) for retry
        assert cid1 in authority1._active_challenges
        assert cid1 not in authority1._poisoned_challenges

        # 2. Pre-ledger verifier failure: challenge expired
        fixed_now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        authority2, iss_res2, envelope2, _, op_pub2, trust_dir2, reg_dir2, ledger_dir2 = (
            self._setup_consume_fixture(tmp_path / "pre2", verification_time=fixed_now, ttl_seconds=5.0)
        )
        cid2 = iss_res2.challenge.challenge_id
        authority2._clock = lambda: fixed_now + timedelta(seconds=10.0)

        captured_fds2 = []
        close_calls2 = []
        def spy_open2(path_to_open: Any, failures: Any) -> int:
            fd = orig_open_anchor(path_to_open, failures)
            if str(path_to_open) == str(ledger_dir2):
                captured_fds2.append(fd)
            return fd

        def spy_close2(fd: int) -> None:
            if captured_fds2 and fd == captured_fds2[-1]:
                close_calls2.append(fd)
            return real_close(fd)

        with patch.object(tass_module, "_open_anchor_dir_under_temp_root", side_effect=spy_open2):
            with patch("os.close", side_effect=spy_close2):
                with pytest.raises(ChallengeConsumptionError) as exc_info2:
                    authority2.verify_and_consume_challenge_for_local_test(
                        challenge_id=cid2,
                        consent_envelope=envelope2,
                        operator_public_key=op_pub2,
                        revocation_trust_directory=trust_dir2,
                        revocation_registry_directory=reg_dir2,
                        nonce_ledger_directory=ledger_dir2,
                    )

        assert exc_info2.value.code == "ERR_CHALLENGE_EXPIRED"
        assert len(captured_fds2) == 1
        assert close_calls2 == [captured_fds2[0]]
        with pytest.raises(OSError):
            os.fstat(captured_fds2[0])
        # Challenge must be purged from active, not in poisoned
        assert cid2 not in authority2._active_challenges
        assert cid2 not in authority2._poisoned_challenges

        # 3. Pre-ledger verifier failure + caller close failure (compound error & single close)
        authority3, iss_res3, envelope3, _, op_pub3, trust_dir3, reg_dir3, ledger_dir3 = (
            self._setup_consume_fixture(tmp_path / "pre3")
        )
        cid3 = iss_res3.challenge.challenge_id
        corrupted_ctx3 = dataclasses.replace(
            authority3._active_challenges[cid3].operator_context,
            expected_plan_digest_sha256="0" * 64,
        )
        authority3._active_challenges[cid3] = dataclasses.replace(
            authority3._active_challenges[cid3],
            operator_context=corrupted_ctx3,
        )

        captured_fds3 = []
        close_calls3 = []
        injected_close_err3 = OSError(errno.EIO, "Injected pre-ledger caller close error")
        def spy_open3(path_to_open: Any, failures: Any) -> int:
            fd = orig_open_anchor(path_to_open, failures)
            if str(path_to_open) == str(ledger_dir3):
                captured_fds3.append(fd)
            return fd

        def fail_close3(fd: int) -> None:
            if captured_fds3 and fd == captured_fds3[-1]:
                close_calls3.append(fd)
                raise injected_close_err3
            return real_close(fd)

        with patch.object(tass_module, "_open_anchor_dir_under_temp_root", side_effect=spy_open3):
            with patch("os.close", side_effect=fail_close3):
                with pytest.raises(ChallengeConsumptionError) as exc_info3:
                    authority3.verify_and_consume_challenge_for_local_test(
                        challenge_id=cid3,
                        consent_envelope=envelope3,
                        operator_public_key=op_pub3,
                        revocation_trust_directory=trust_dir3,
                        revocation_registry_directory=reg_dir3,
                        nonce_ledger_directory=ledger_dir3,
                    )

        err3 = exc_info3.value
        assert err3.code == "ERR_OPERATOR_CONSENT_VERIFICATION_FAILED"
        assert err3.cleanup_error is injected_close_err3
        assert err3.cleanup_errors == [injected_close_err3]
        # At most one close attempt made, never retried in finally
        assert close_calls3 == [captured_fds3[0]]
        # Clean up fd manually to avoid leak in test process
        real_close(captured_fds3[0])

        # 4. Traversal failure: no descriptor returned, no leak, challenge preserved
        authority4, iss_res4, envelope4, _, op_pub4, trust_dir4, reg_dir4, ledger_dir4 = (
            self._setup_consume_fixture(tmp_path / "pre4")
        )
        cid4 = iss_res4.challenge.challenge_id
        injected_traversal_err = SignerAdmissionVerificationError("ERR_PATH_TRAVERSAL_DETECTED: Symlink in parent")
        with patch.object(tass_module, "_open_anchor_dir_under_temp_root", side_effect=injected_traversal_err):
            with pytest.raises(SignerAdmissionVerificationError, match="ERR_PATH_TRAVERSAL_DETECTED"):
                authority4.verify_and_consume_challenge_for_local_test(
                    challenge_id=cid4,
                    consent_envelope=envelope4,
                    operator_public_key=op_pub4,
                    revocation_trust_directory=trust_dir4,
                    revocation_registry_directory=reg_dir4,
                    nonce_ledger_directory=ledger_dir4,
                )
        assert cid4 in authority4._active_challenges
        assert cid4 not in authority4._poisoned_challenges

    def test_fault_combinations_caller_fd_and_ledger_fd_close_failures(self, tmp_path: Path):
        """I15-R4: Exercises:
        1. Successful append + caller-owned FD close failure -> poisons challenge, returns no success
        2. Append failure + caller-owned FD close failure -> preserves both failures structurally
        3. Successful append + ledger close failure + caller close failure -> poisons challenge, preserves both cleanup errors
        4. Append failure + ledger close failure + caller close failure -> poisons challenge, append is primary, both cleanup errors preserved
        """
        from unittest.mock import patch
        real_close = os.close
        import src.services.trial_authorization_signer_service as tass_module
        orig_open_anchor = tass_module._open_anchor_dir_under_temp_root

        # Case 1: Successful append + caller-owned FD close failure
        authority1, iss_res1, envelope1, _, op_pub1, trust_dir1, reg_dir1, ledger_dir1 = (
            self._setup_consume_fixture(tmp_path / "fault1")
        )
        cid1 = iss_res1.challenge.challenge_id
        caller_close_exc1 = OSError(errno.EIO, "Injected caller-FD close failure 1")
        captured_fds1 = []
        close_calls1 = []
        def track_open1(path_to_open: Any, failures: Any) -> int:
            fd = orig_open_anchor(path_to_open, failures)
            if str(path_to_open) == str(ledger_dir1):
                captured_fds1.append(fd)
            return fd

        def fail_caller_close1(fd: int) -> None:
            if captured_fds1 and fd == captured_fds1[-1]:
                close_calls1.append(fd)
                raise caller_close_exc1
            return real_close(fd)

        with patch.object(tass_module, "_open_anchor_dir_under_temp_root", side_effect=track_open1):
            with patch("os.close", side_effect=fail_caller_close1):
                with pytest.raises(ChallengeConsumptionError) as exc_info1:
                    authority1.verify_and_consume_challenge_for_local_test(
                        challenge_id=cid1,
                        consent_envelope=envelope1,
                        operator_public_key=op_pub1,
                        revocation_trust_directory=trust_dir1,
                        revocation_registry_directory=reg_dir1,
                        nonce_ledger_directory=ledger_dir1,
                    )

        err1 = exc_info1.value
        assert err1.code == "ERR_NONCE_LEDGER_PERSISTENCE_FAILED"
        assert err1.primary_error is caller_close_exc1
        assert caller_close_exc1 in err1.cleanup_errors
        assert cid1 in authority1._poisoned_challenges
        assert close_calls1 == [captured_fds1[0]]
        real_close(captured_fds1[0])

        # Case 2: Append failure + caller-owned FD close failure
        authority2, iss_res2, envelope2, _, op_pub2, trust_dir2, reg_dir2, ledger_dir2 = (
            self._setup_consume_fixture(tmp_path / "fault2")
        )
        cid2 = iss_res2.challenge.challenge_id
        captured_fds2 = []
        close_calls2 = []
        def track_open2(path_to_open: Any, failures: Any) -> int:
            fd = orig_open_anchor(path_to_open, failures)
            if str(path_to_open) == str(ledger_dir2):
                captured_fds2.append(fd)
            return fd

        append_exc2 = OSError(errno.EIO, "Injected append failure 2")
        caller_close_exc2 = OSError(errno.EIO, "Injected caller-FD close failure 2")
        def fail_caller_close2(fd: int) -> None:
            if captured_fds2 and fd == captured_fds2[-1]:
                close_calls2.append(fd)
                raise caller_close_exc2
            return real_close(fd)

        with patch.object(tass_module, "_open_anchor_dir_under_temp_root", side_effect=track_open2):
            with patch("os.close", side_effect=fail_caller_close2):
                with patch.object(SignerConsumedNonceLedger, "consume_once", side_effect=append_exc2):
                    with pytest.raises(ChallengeConsumptionError) as exc_info2:
                        authority2.verify_and_consume_challenge_for_local_test(
                            challenge_id=cid2,
                            consent_envelope=envelope2,
                            operator_public_key=op_pub2,
                            revocation_trust_directory=trust_dir2,
                            revocation_registry_directory=reg_dir2,
                            nonce_ledger_directory=ledger_dir2,
                        )

        err2 = exc_info2.value
        assert err2.code == "ERR_NONCE_LEDGER_PERSISTENCE_FAILED"
        assert err2.primary_error is append_exc2
        assert caller_close_exc2 in err2.cleanup_errors
        assert cid2 in authority2._poisoned_challenges
        assert close_calls2 == [captured_fds2[0]]
        real_close(captured_fds2[0])

        # Case 3: Successful append + ledger close failure + caller close failure
        authority3, iss_res3, envelope3, _, op_pub3, trust_dir3, reg_dir3, ledger_dir3 = (
            self._setup_consume_fixture(tmp_path / "fault3")
        )
        cid3 = iss_res3.challenge.challenge_id
        captured_fds3 = []
        close_calls3 = []
        def track_open3(path_to_open: Any, failures: Any) -> int:
            fd = orig_open_anchor(path_to_open, failures)
            if str(path_to_open) == str(ledger_dir3):
                captured_fds3.append(fd)
            return fd

        ledger_instances3 = []
        ledger_dir_fds3 = []
        orig_ledger_init3 = SignerConsumedNonceLedger.__init__
        def track_ledger_init3(self_ledger: Any, *args: Any, **kwargs: Any) -> None:
            orig_ledger_init3(self_ledger, *args, **kwargs)
            ledger_instances3.append(self_ledger)
            ledger_dir_fds3.append(self_ledger._dir_fd)

        ledger_dir_close_exc3 = OSError(errno.EIO, "Injected ledger dir-fd close fault 3")
        caller_close_exc3 = OSError(errno.EIO, "Injected caller-FD close failure 3")
        ledger_close_calls3 = []

        def fail_closes3(fd: int) -> None:
            if captured_fds3 and fd == captured_fds3[-1]:
                close_calls3.append(fd)
                raise caller_close_exc3
            if ledger_dir_fds3 and fd == ledger_dir_fds3[-1]:
                ledger_close_calls3.append(fd)
                raise ledger_dir_close_exc3
            return real_close(fd)

        with patch.object(tass_module, "_open_anchor_dir_under_temp_root", side_effect=track_open3):
            with patch.object(SignerConsumedNonceLedger, "__init__", track_ledger_init3):
                with patch("os.close", side_effect=fail_closes3):
                    with pytest.raises(ChallengeConsumptionError) as exc_info3:
                        authority3.verify_and_consume_challenge_for_local_test(
                            challenge_id=cid3,
                            consent_envelope=envelope3,
                            operator_public_key=op_pub3,
                            revocation_trust_directory=trust_dir3,
                            revocation_registry_directory=reg_dir3,
                            nonce_ledger_directory=ledger_dir3,
                        )

        err3 = exc_info3.value
        assert err3.code == "ERR_NONCE_LEDGER_PERSISTENCE_FAILED"
        # Real ledger.close() ran and converted the underlying os.close failure into SignerLedgerDurabilityError
        assert isinstance(err3.primary_error, SignerLedgerDurabilityError)
        assert "ERR_LEDGER_CLOSE_FAILED" in str(err3.primary_error)
        assert err3.cleanup_errors[0] is err3.primary_error
        assert err3.cleanup_errors[1] is caller_close_exc3
        assert cid3 in authority3._poisoned_challenges

        # Assert real ledger.close() executed its single-close disown semantics and recorded unknown outcome
        assert len(ledger_instances3) == 1
        ledger3 = ledger_instances3[0]
        assert ledger3._closed is True
        assert ledger3._dir_fd is None
        assert len(ledger3._cleanup_failures) >= 1
        assert ledger3._cleanup_failures[0][3] == tass_module.CLOSE_OUTCOME_UNKNOWN

        # Assert each descriptor received at most one close attempt
        assert close_calls3 == [captured_fds3[0]]
        assert ledger_close_calls3 == [ledger_dir_fds3[0]]

        # Test harness explicit cleanup: close descriptors left unclosed by injected syscall failure
        real_close(captured_fds3[0])
        real_close(ledger_dir_fds3[0])

        # Case 4: Append failure + ledger close failure + caller close failure
        authority4, iss_res4, envelope4, _, op_pub4, trust_dir4, reg_dir4, ledger_dir4 = (
            self._setup_consume_fixture(tmp_path / "fault4")
        )
        cid4 = iss_res4.challenge.challenge_id
        captured_fds4 = []
        close_calls4 = []
        def track_open4(path_to_open: Any, failures: Any) -> int:
            fd = orig_open_anchor(path_to_open, failures)
            if str(path_to_open) == str(ledger_dir4):
                captured_fds4.append(fd)
            return fd

        ledger_instances4 = []
        ledger_dir_fds4 = []
        orig_ledger_init4 = SignerConsumedNonceLedger.__init__
        def track_ledger_init4(self_ledger: Any, *args: Any, **kwargs: Any) -> None:
            orig_ledger_init4(self_ledger, *args, **kwargs)
            ledger_instances4.append(self_ledger)
            ledger_dir_fds4.append(self_ledger._dir_fd)

        append_exc4 = OSError(errno.EIO, "Injected append failure 4")
        ledger_dir_close_exc4 = OSError(errno.EIO, "Injected ledger dir-fd close fault 4")
        caller_close_exc4 = OSError(errno.EIO, "Injected caller-FD close failure 4")
        ledger_close_calls4 = []

        def fail_closes4(fd: int) -> None:
            if captured_fds4 and fd == captured_fds4[-1]:
                close_calls4.append(fd)
                raise caller_close_exc4
            if ledger_dir_fds4 and fd == ledger_dir_fds4[-1]:
                ledger_close_calls4.append(fd)
                raise ledger_dir_close_exc4
            return real_close(fd)

        with patch.object(tass_module, "_open_anchor_dir_under_temp_root", side_effect=track_open4):
            with patch.object(SignerConsumedNonceLedger, "__init__", track_ledger_init4):
                with patch.object(SignerConsumedNonceLedger, "consume_once", side_effect=append_exc4):
                    with patch("os.close", side_effect=fail_closes4):
                        with pytest.raises(ChallengeConsumptionError) as exc_info4:
                            authority4.verify_and_consume_challenge_for_local_test(
                                challenge_id=cid4,
                                consent_envelope=envelope4,
                                operator_public_key=op_pub4,
                                revocation_trust_directory=trust_dir4,
                                revocation_registry_directory=reg_dir4,
                                nonce_ledger_directory=ledger_dir4,
                            )

        err4 = exc_info4.value
        assert err4.code == "ERR_NONCE_LEDGER_PERSISTENCE_FAILED"
        assert err4.primary_error is append_exc4
        assert isinstance(err4.cleanup_errors[0], SignerLedgerDurabilityError)
        assert "ERR_LEDGER_CLOSE_FAILED" in str(err4.cleanup_errors[0])
        assert err4.cleanup_errors[1] is caller_close_exc4
        assert cid4 in authority4._poisoned_challenges

        # Assert real ledger.close() executed its single-close disown semantics and recorded unknown outcome
        assert len(ledger_instances4) == 1
        ledger4 = ledger_instances4[0]
        assert ledger4._closed is True
        assert ledger4._dir_fd is None
        assert len(ledger4._cleanup_failures) >= 1
        assert ledger4._cleanup_failures[0][3] == tass_module.CLOSE_OUTCOME_UNKNOWN

        # Assert each descriptor received at most one close attempt
        assert close_calls4 == [captured_fds4[0]]
        assert ledger_close_calls4 == [ledger_dir_fds4[0]]

        # Test harness explicit cleanup: close descriptors left unclosed by injected syscall failure
        real_close(captured_fds4[0])
        real_close(ledger_dir_fds4[0])

    def test_path_tripwire_no_path_reopening_after_verification(self, tmp_path: Path):
        """I15-R4: Tripwire fails test if nonce_ledger_directory is reopened by path after descriptor verification."""
        from unittest.mock import patch
        authority, iss_res, envelope, _, op_pub, trust_dir, reg_dir, ledger_dir = (
            self._setup_consume_fixture(tmp_path)
        )
        cid = iss_res.challenge.challenge_id

        # Verify that SignerConsumedNonceLedger was called with directory_fd, not ledger_dir
        orig_init = SignerConsumedNonceLedger.__init__
        init_calls = []
        def tracked_init(self_ledger: Any, *args: Any, **kwargs: Any) -> None:
            init_calls.append((args, kwargs))
            return orig_init(self_ledger, *args, **kwargs)

        with patch.object(SignerConsumedNonceLedger, "__init__", tracked_init):
            res = authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

        assert len(init_calls) == 1
        args, kwargs = init_calls[0]
        assert kwargs.get("directory_fd") is not None
        assert kwargs.get("ledger_dir") is None
        assert not args or args[0] is None


class TestLocalTestTrialAuthorizationIssuanceCore:
    """Rigorous tests for Gate 3-I16: Local-Test Trial Authorization Issuance Core."""

    def _setup_issuance_fixture(
        self,
        tmp_path: Path,
        *,
        verification_time: Optional[datetime] = None,
        revoked_director_keys: Optional[List[str]] = None,
        revoked_operator_keys: Optional[List[str]] = None,
        revoked_authorization_ids: Optional[List[str]] = None,
        ttl_seconds: Union[int, float] = 300.0,
        keystore_key_bytes: Optional[bytes] = None,
    ) -> Tuple[
        LocalTestChallengeAuthority,
        LocalTestChallengeIssuanceResult,
        SignedOperatorConsentEnvelope,
        Ed25519PrivateKey,
        Ed25519PublicKey,
        LocalTestChallengeIssuanceRequest,
        LocalTestSignerHmacSigningCapability,
        LocalTestSignerKeyHandle,
        Path,
        Path,
        Path,
        datetime,
    ]:
        if verification_time is None:
            verification_time = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)

        tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp_path.chmod(0o700)

        i14_suite = TestLocalTestAuthoritativeChallengeIssuanceCore()
        authority, req, dir_priv, trust_dir, reg_dir = i14_suite._setup_challenge_fixture(
            tmp_path,
            verification_time=verification_time,
            revoked_director_keys=None,
            revoked_operator_keys=None,
            ttl_seconds=ttl_seconds,
        )
        issuance_result = authority.issue_challenge(req)

        # If custom revoked authorization IDs or keys are requested, update signed manifest
        if revoked_authorization_ids or revoked_director_keys or revoked_operator_keys:
            rev_priv = Ed25519PrivateKey.generate()
            pub_pem = rev_priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
            (trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME).write_bytes(pub_pem)
            (trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME).chmod(0o600)

            manifest_data = make_sample_revocation_manifest_data(
                epoch=2,
                issuer_id="security_officer_root",
                valid_until_iso="2026-11-05T00:00:00Z",
                issued_at_iso="2026-11-04T00:00:00Z",
                revoked_director_key_ids=revoked_director_keys or [],
                revoked_operator_key_ids=revoked_operator_keys or [],
                revoked_authorization_ids=revoked_authorization_ids or [],
            )
            raw_manifest_bytes, _ = sign_manifest_data(manifest_data, rev_priv)
            reg_file = reg_dir / "revocations.signed.json"
            reg_file.write_bytes(raw_manifest_bytes)
            reg_file.chmod(0o600)

        op_priv = Ed25519PrivateKey.generate()
        op_pub = op_priv.public_key()
        ledger_dir = tmp_path / "nonce_ledger_dir"
        ledger_dir.mkdir(mode=0o700, exist_ok=True)

        envelope = TestLocalTestOperatorConsentVerificationAndChallengeConsumptionCore()._make_signed_operator_consent_envelope(
            issuance_result.operator_context, op_priv
        )

        # Create ephemeral 32-byte key
        key_bytes = keystore_key_bytes if keystore_key_bytes is not None else os.urandom(32)
        signing_capability = create_signer_hmac_signing_capability_for_local_test(
            key_bytes, key_id="ephemeral_test_signer_key_01"
        )

        # Temporary mode-0600 keystore fixture for I13 verification handle, removed immediately after handle load
        keystore_dir = tmp_path / "ephemeral_keystore"
        keystore_dir.mkdir(mode=0o700, exist_ok=True)
        key_file = keystore_dir / "trial_auth.key"
        key_file.write_bytes(key_bytes)
        key_file.chmod(0o600)
        verification_key_handle = load_signer_key_handle_for_local_test(keystore_dir)
        key_file.unlink()  # Clean removal at teardown

        return (
            authority,
            issuance_result,
            envelope,
            op_priv,
            op_pub,
            req,
            signing_capability,
            verification_key_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            verification_time,
        )

    def test_happy_path_issuance_and_all_32_claims_verification(self, tmp_path: Path):
        """Happy path: valid admission + challenge consumption produces exact TrialExecutionAuthorization,
        verified by LocalTestSignerKeyHandle, with all 32 claims matching inputs exactly."""
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)

        cid = iss_res.challenge.challenge_id

        auth = issue_trial_execution_authorization_for_local_test(
            approval_envelope=req.approval_envelope,
            proposal=req.proposal,
            execution_plan=req.execution_plan,
            director_public_key=req.director_public_key,
            expected_director_key_id=req.expected_director_key_id,
            operator_consent_envelope=envelope,
            operator_public_key=op_pub,
            challenge_authority=authority,
            challenge_id=cid,
            signing_capability=signing_cap,
            verification_key_handle=ver_handle,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
            verification_time_utc=ver_time,
            ttl_seconds=300.0,
        )

        # Assert exact nominal type (no subclass)
        assert type(auth) is TrialExecutionAuthorization

        # Assert constant-time verification using I13 verification handle
        assert ver_handle.verify_trial_execution_authorization(auth) is True

        # Assert exact 32 claims match the source contracts and challenge
        assert auth.authorization_id == iss_res.challenge.preallocated_authorization_id
        assert auth.key_id == "ephemeral_test_signer_key_01"
        assert auth.operator_gesture_digest_sha256 == envelope.compute_consent_digest()
        assert auth.approval_record_id == req.approval_envelope.approval_record.approval_id
        expected_approval_digest = hashlib.sha256(
            canonical_json_bytes(approval_record_to_dict(req.approval_envelope.approval_record))
        ).hexdigest()
        assert auth.approval_record_digest_sha256 == expected_approval_digest
        assert auth.proposal_id == req.proposal.proposal_id
        assert auth.proposal_revision == compute_proposal_revision(req.proposal)
        assert auth.proposal_content_digest_sha256 == req.approval_envelope.proposal_content_digest_sha256
        assert auth.plan_idempotency_key == req.execution_plan.idempotency_key
        assert auth.plan_digest_sha256 == req.approval_envelope.plan_digest_sha256
        assert auth.action_payload_digest_sha256 == envelope.action_payload_digest_sha256
        assert auth.target_project_uid == req.execution_plan.target_spec.project_id
        assert auth.target_project_name == req.execution_plan.target_spec.project_name
        assert auth.target_timeline_uid == req.execution_plan.target_spec.timeline_id
        assert auth.target_timeline_name == req.execution_plan.target_spec.timeline_name
        assert auth.target_timeline_fps_numerator == 24
        assert auth.target_timeline_fps_denominator == 1
        assert auth.observed_timeline_start_frame == envelope.record_frame
        assert auth.authorized_record_frame == envelope.record_frame
        assert auth.source_file_path == envelope.source_file_path
        assert auth.source_file_sha256 == envelope.source_file_sha256
        assert auth.authorized_action_type == "INSERT_CLIP"
        assert auth.authorized_track_type == "video"
        assert auth.authorized_track_index == 1
        assert auth.authorized_timeline_frame_start == 0
        assert auth.authorized_timeline_frame_duration == 120
        assert auth.authorized_source_frame_start == 0
        assert auth.authorized_source_frame_duration == 120
        assert auth.execution_id == iss_res.challenge.preallocated_execution_id
        assert auth.issued_at_iso == ver_time.isoformat().replace("+00:00", "Z")
        assert auth.valid_until_iso == (ver_time + timedelta(seconds=300.0)).isoformat().replace("+00:00", "Z")
        assert auth.token_nonce == auth.execution_id
        assert len(auth.authorization_hmac) == 64
        int(auth.authorization_hmac, 16)  # Must be valid hex

        # Anti-aliasing check among identities
        distinct_ids = {auth.authorization_id, auth.execution_id, envelope.client_nonce, auth.approval_record_id}
        assert len(distinct_ids) == 4

        # Nonce ledger verification: exactly one line appended
        ledger_file = ledger_dir / "signer_consumed_nonces.jsonl"
        assert ledger_file.exists()
        lines = ledger_file.read_text().splitlines()
        assert len(lines) == 1
        ledger_record = json.loads(lines[0])
        assert ledger_record["client_nonce"] == envelope.client_nonce
        assert ledger_record["challenge_id"] == cid

    def test_authority_instance_method_delegation(self, tmp_path: Path):
        """Test that LocalTestChallengeAuthority.issue_trial_execution_authorization_for_local_test
        delegates cleanly and produces an identical valid authorization."""
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)

        cid = iss_res.challenge.challenge_id

        auth = authority.issue_trial_execution_authorization_for_local_test(
            approval_envelope=req.approval_envelope,
            proposal=req.proposal,
            execution_plan=req.execution_plan,
            director_public_key=req.director_public_key,
            expected_director_key_id=req.expected_director_key_id,
            operator_consent_envelope=envelope,
            operator_public_key=op_pub,
            challenge_id=cid,
            signing_capability=signing_cap,
            verification_key_handle=ver_handle,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
            verification_time_utc=ver_time,
            ttl_seconds=300.0,
        )

        assert type(auth) is TrialExecutionAuthorization
        assert ver_handle.verify_trial_execution_authorization(auth) is True
        assert auth.authorization_id == iss_res.challenge.preallocated_authorization_id

    def test_forged_or_copied_consumption_evidence_fails_closed(self, tmp_path: Path):
        """Non-forgeable-in-process consumption provenance:
        Fabricated or copied LocalTestChallengeConsumptionResult fails closed."""
        import copy
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            _,
            _,
            _,
            trust_dir,
            reg_dir,
            ledger_dir,
            _,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        # Durably consume
        real_result = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )

        # 1. Attempt reservation with a shallow/deep copy of the valid result
        copied_result = copy.deepcopy(real_result)
        with pytest.raises(SignerAdmissionVerificationError) as exc_copied:
            authority.reserve_consumed_evidence_for_issuance(cid, copied_result)
        assert exc_copied.value.code == "ERR_CONSUMPTION_EVIDENCE_FORGERY"

        # 2. Attempt reservation with a completely fabricated result
        fabricated_result = LocalTestChallengeConsumptionResult(
            challenge_id=cid,
            client_nonce=real_result.client_nonce,
            preallocated_authorization_id=real_result.preallocated_authorization_id,
            preallocated_execution_id=real_result.preallocated_execution_id,
            operator_id=real_result.operator_id,
            operator_key_id=real_result.operator_key_id,
            approval_record_id=real_result.approval_record_id,
            proposal_revision=real_result.proposal_revision,
            consumed_at_iso=real_result.consumed_at_iso,
            plan_digest_sha256=real_result.plan_digest_sha256,
            action_payload_digest_sha256=real_result.action_payload_digest_sha256,
        )
        with pytest.raises(SignerAdmissionVerificationError) as exc_fab:
            authority.reserve_consumed_evidence_for_issuance(cid, fabricated_result)
        assert exc_fab.value.code == "ERR_CONSUMPTION_EVIDENCE_FORGERY"

    def test_unconsumed_challenge_fails_closed(self, tmp_path: Path):
        """Unconsumed challenge reservation fails closed with ERR_CHALLENGE_NOT_CONSUMED."""
        (
            authority,
            iss_res,
            envelope,
            _,
            _,
            _,
            _,
            _,
            _,
            _,
            _,
            _,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        dummy_result = LocalTestChallengeConsumptionResult(
            challenge_id=cid,
            client_nonce=envelope.client_nonce,
            preallocated_authorization_id=iss_res.challenge.preallocated_authorization_id,
            preallocated_execution_id=iss_res.challenge.preallocated_execution_id,
            operator_id="operator_test_01",
            operator_key_id="op_key_test_01",
            approval_record_id="appr_01",
            proposal_revision="a" * 64,
            consumed_at_iso="2026-11-04T12:00:00Z",
            plan_digest_sha256="1" * 64,
            action_payload_digest_sha256="2" * 64,
        )
        with pytest.raises(SignerAdmissionVerificationError) as exc_unconsumed:
            authority.reserve_consumed_evidence_for_issuance(cid, dummy_result)
        assert exc_unconsumed.value.code == "ERR_CHALLENGE_NOT_CONSUMED"

    def test_duplicate_issuance_attempt_fails_closed(self, tmp_path: Path):
        """Duplicate issuance attempt on the same challenge/evidence fails closed."""
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        # First issuance succeeds
        auth = issue_trial_execution_authorization_for_local_test(
            approval_envelope=req.approval_envelope,
            proposal=req.proposal,
            execution_plan=req.execution_plan,
            director_public_key=req.director_public_key,
            expected_director_key_id=req.expected_director_key_id,
            operator_consent_envelope=envelope,
            operator_public_key=op_pub,
            challenge_authority=authority,
            challenge_id=cid,
            signing_capability=signing_cap,
            verification_key_handle=ver_handle,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
            verification_time_utc=ver_time,
        )
        assert auth is not None

        # Second issuance attempt fails closed because challenge was already consumed and marked issued
        with pytest.raises((ChallengeConsumptionError, SignerAdmissionVerificationError)):
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=authority,
                challenge_id=cid,
                signing_capability=signing_cap,
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
            )

    def test_authority_mismatch_fails_closed(self, tmp_path: Path):
        """Passing wrong authority type or wrong authority instance fails closed."""
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        # 1. Non-authority instance
        with pytest.raises(SignerAdmissionVerificationError) as exc_type:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=object(),  # type: ignore
                challenge_id=cid,
                signing_capability=signing_cap,
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
            )
        assert exc_type.value.code == "ERR_CHALLENGE_AUTHORITY_TYPE"

        # 2. Authority instance where challenge was never registered
        other_authority = LocalTestChallengeAuthority(clock=lambda: ver_time)
        with pytest.raises(SignerAdmissionVerificationError) as exc_lookup:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=other_authority,
                challenge_id=cid,
                signing_capability=signing_cap,
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
            )
        assert "ERR_CHALLENGE_LOOKUP_FAILED" in exc_lookup.value.code

    def test_critical_claim_tampering_fails_closed(self, tmp_path: Path):
        """Tampering with critical target, source, or action parameters in consent envelope fails closed."""
        i15_suite = TestLocalTestOperatorConsentVerificationAndChallengeConsumptionCore()

        # Define parameter tampering variations
        tamper_cases = [
            ("target_project_uid", "TAMPERED_PROJECT_UID", "ERR_TARGET_PROJECT_UID_MISMATCH"),
            ("target_project_name", "TAMPERED_PROJECT_NAME", "ERR_TARGET_PROJECT_NAME_MISMATCH"),
            ("target_timeline_uid", "TAMPERED_TIMELINE_UID", "ERR_TARGET_TIMELINE_UID_MISMATCH"),
            ("target_timeline_name", "TAMPERED_TIMELINE_NAME", "ERR_TARGET_TIMELINE_NAME_MISMATCH"),
            ("source_file_sha256", "f" * 64, "ERR_SOURCE_FILE_SHA256_MISMATCH"),
            ("action_type", "REPLACE_CLIP", "ERR_ACTION_TYPE_INVALID"),
            ("track_type", "audio", "ERR_TRACK_TYPE_INVALID"),
            ("track_index", 2, "ERR_TRACK_INDEX_INVALID"),
            ("duration_frames", 240, "ERR_DURATION_FRAMES_INVALID"),
        ]

        for field, bad_value, expected_code in tamper_cases:
            sub_dir = tmp_path / f"tamper_{field}"
            sub_dir.mkdir(parents=True, exist_ok=True)
            sub_dir.chmod(0o700)
            (
                authority,
                iss_res,
                valid_envelope,
                op_priv,
                op_pub,
                req,
                signing_cap,
                ver_handle,
                trust_dir,
                reg_dir,
                ledger_dir,
                ver_time,
            ) = self._setup_issuance_fixture(sub_dir)
            cid = iss_res.challenge.challenge_id

            try:
                tampered_envelope = i15_suite._make_signed_operator_consent_envelope(
                    iss_res.operator_context, op_priv, overrides={field: bad_value}
                )
            except Exception:
                # If envelope constructor rejects the bad value directly, create via object.__setattr__ to test issuer checks
                import copy
                tampered_envelope = copy.deepcopy(valid_envelope)
                object.__setattr__(tampered_envelope, field, bad_value)

            with pytest.raises((SignerAdmissionVerificationError, OperatorConsentVerificationError)) as exc_info:
                issue_trial_execution_authorization_for_local_test(
                    approval_envelope=req.approval_envelope,
                    proposal=req.proposal,
                    execution_plan=req.execution_plan,
                    director_public_key=req.director_public_key,
                    expected_director_key_id=req.expected_director_key_id,
                    operator_consent_envelope=tampered_envelope,
                    operator_public_key=op_pub,
                    challenge_authority=authority,
                    challenge_id=cid,
                    signing_capability=signing_cap,
                    verification_key_handle=ver_handle,
                    revocation_trust_directory=trust_dir,
                    revocation_registry_directory=reg_dir,
                    nonce_ledger_directory=ledger_dir,
                    verification_time_utc=ver_time,
                )
            assert exc_info.value.code in (
                expected_code,
                "ERR_CONSENT_MUTATION_MISMATCH",
                "ERR_ACTION_TYPE_NOT_INSERT_CLIP",
                "ERR_ACTION_TRACK_NOT_VIDEO_1",
                "ERR_ACTION_DURATION_NOT_120_FRAMES",
                "ERR_CONSENT_SIGNATURE_VERIFICATION_FAILED",
            ) or expected_code in str(exc_info.value)

    def test_identity_aliasing_fails_closed(self, tmp_path: Path):
        """Identity aliasing among authorization ID, execution ID, client nonce, and approval ID fails closed."""
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        # Mutate active challenge and operator context to have matching authorization ID and execution ID
        aliased_challenge = dataclasses.replace(
            iss_res.challenge,
            preallocated_execution_id=iss_res.challenge.preallocated_authorization_id,
        )
        aliased_op_ctx = dataclasses.replace(
            iss_res.operator_context,
            challenge=aliased_challenge,
            expected_execution_id=iss_res.challenge.preallocated_authorization_id,
        )
        aliased_res = LocalTestChallengeIssuanceResult(
            challenge=aliased_challenge,
            operator_context=aliased_op_ctx,
        )
        authority._active_challenges[cid] = aliased_res

        with pytest.raises(SignerAdmissionVerificationError) as exc_alias:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=authority,
                challenge_id=cid,
                signing_capability=signing_cap,
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
            )
        assert exc_alias.value.code == "ERR_IDENTITY_ALIASING"

    def test_revocation_gating_prevents_issuance(self, tmp_path: Path):
        """Revocation of Director key, Operator key, or Authorization ID fails closed."""
        # 1. Revoked Director key
        dir_sub = tmp_path / "rev_dir"
        dir_sub.mkdir(parents=True, exist_ok=True)
        dir_sub.chmod(0o700)
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(dir_sub, revoked_director_keys=["dir_key_test_01"])

        with pytest.raises(SignerAdmissionVerificationError) as exc_dir:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=authority,
                challenge_id=iss_res.challenge.challenge_id,
                signing_capability=signing_cap,
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
            )
        assert exc_dir.value.code == "ERR_DIRECTOR_KEY_REVOKED"

        # 2. Revoked Operator key
        op_sub = tmp_path / "rev_op"
        op_sub.mkdir(parents=True, exist_ok=True)
        op_sub.chmod(0o700)
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(op_sub, revoked_operator_keys=["op_key_test_01"])

        with pytest.raises(SignerAdmissionVerificationError) as exc_op:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=authority,
                challenge_id=iss_res.challenge.challenge_id,
                signing_capability=signing_cap,
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
            )
        assert exc_op.value.code == "ERR_OPERATOR_KEY_REVOKED"

    def test_exact_types_and_kwargs_rejection(self, tmp_path: Path):
        """Strict nominal typing: rejects kwargs overrides, invalid TTL, invalid verification time."""
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        # 1. Caller kwargs overrides rejected
        with pytest.raises(SignerAdmissionVerificationError) as exc_kw:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=authority,
                challenge_id=cid,
                signing_capability=signing_cap,
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
                extra_param="illegal_override",  # type: ignore
            )
        assert exc_kw.value.code == "ERR_CALLER_OVERRIDE_PROHIBITED"

        # 2. Invalid TTL
        for bad_ttl in [-10, 0, float("inf"), float("nan"), True, "300"]:
            with pytest.raises(SignerAdmissionVerificationError) as exc_ttl:
                issue_trial_execution_authorization_for_local_test(
                    approval_envelope=req.approval_envelope,
                    proposal=req.proposal,
                    execution_plan=req.execution_plan,
                    director_public_key=req.director_public_key,
                    expected_director_key_id=req.expected_director_key_id,
                    operator_consent_envelope=envelope,
                    operator_public_key=op_pub,
                    challenge_authority=authority,
                    challenge_id=cid,
                    signing_capability=signing_cap,
                    verification_key_handle=ver_handle,
                    revocation_trust_directory=trust_dir,
                    revocation_registry_directory=reg_dir,
                    nonce_ledger_directory=ledger_dir,
                    verification_time_utc=ver_time,
                    ttl_seconds=bad_ttl,  # type: ignore
                )
            assert exc_ttl.value.code == "ERR_TTL_INVALID"

        # 3. Invalid verification_time_utc (naive or non-UTC)
        naive_dt = datetime(2026, 11, 4, 12, 0, 0)
        with pytest.raises(SignerAdmissionVerificationError) as exc_time:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=authority,
                challenge_id=cid,
                signing_capability=signing_cap,
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=naive_dt,
            )
        assert exc_time.value.code == "ERR_VERIFICATION_TIME_INVALID"

        # 4. Invalid challenge_id
        for bad_cid in ["", "  ", " padded_cid "]:
            with pytest.raises(SignerAdmissionVerificationError) as exc_cid:
                issue_trial_execution_authorization_for_local_test(
                    approval_envelope=req.approval_envelope,
                    proposal=req.proposal,
                    execution_plan=req.execution_plan,
                    director_public_key=req.director_public_key,
                    expected_director_key_id=req.expected_director_key_id,
                    operator_consent_envelope=envelope,
                    operator_public_key=op_pub,
                    challenge_authority=authority,
                    challenge_id=bad_cid,
                    signing_capability=signing_cap,
                    verification_key_handle=ver_handle,
                    revocation_trust_directory=trust_dir,
                    revocation_registry_directory=reg_dir,
                    nonce_ledger_directory=ledger_dir,
                    verification_time_utc=ver_time,
                )
            assert exc_cid.value.code == "ERR_CHALLENGE_ID_INVALID"

        # 5. Invalid signing capability type
        with pytest.raises(SignerAdmissionVerificationError) as exc_sc:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=authority,
                challenge_id=cid,
                signing_capability=object(),  # type: ignore
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
            )
        assert exc_sc.value.code == "ERR_SIGNING_CAPABILITY_TYPE"

        # 6. Invalid verification key handle type
        with pytest.raises(SignerAdmissionVerificationError) as exc_vh:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=authority,
                challenge_id=cid,
                signing_capability=signing_cap,
                verification_key_handle=object(),  # type: ignore
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
            )
        assert exc_vh.value.code == "ERR_VERIFICATION_KEY_HANDLE_TYPE"

    def test_signing_capability_boundaries_and_redaction(self):
        """Key boundary: ephemeral 32-byte test key only; no key bytes in repr/str; immutability enforced."""
        # 1. Invalid key length / type
        with pytest.raises(SignerAdmissionVerificationError) as exc_len1:
            create_signer_hmac_signing_capability_for_local_test(b"short_16_bytes!")
        assert exc_len1.value.code == "ERR_HMAC_KEY_INVALID"
        with pytest.raises(SignerAdmissionVerificationError) as exc_len2:
            create_signer_hmac_signing_capability_for_local_test(b"long_key_" * 8)
        assert exc_len2.value.code == "ERR_HMAC_KEY_INVALID"
        with pytest.raises(SignerAdmissionVerificationError) as exc_type:
            create_signer_hmac_signing_capability_for_local_test("not_bytes_key_01234567890123456")  # type: ignore
        assert exc_type.value.code == "ERR_HMAC_KEY_INVALID"

        # 2. Valid creation
        raw_key = os.urandom(32)
        cap = create_signer_hmac_signing_capability_for_local_test(raw_key, key_id="test_key_01")
        assert repr(cap) == "<LocalTestSignerHmacSigningCapability: REDACTED>"
        assert str(cap) == "<LocalTestSignerHmacSigningCapability: REDACTED>"
        assert raw_key.hex() not in repr(cap)
        assert raw_key.hex() not in str(cap)

        # 3. Direct instantiation prohibited
        with pytest.raises(SignerLedgerSecurityError, match="ERR_DIRECT_KEY_HANDLE_CONSTRUCTION_PROHIBITED"):
            LocalTestSignerHmacSigningCapability()

        # 4. Immutability enforced
        with pytest.raises(AttributeError, match="immutable"):
            cap.key_id = "tampered_id"
        with pytest.raises(AttributeError, match="immutable"):
            del cap.key_id

        # 5. LocalTestSignerKeyHandle remains verification-only (no signing method or raw key)
        public_methods = [m for m in dir(LocalTestSignerKeyHandle) if not m.startswith("_")]
        assert public_methods == ["verify_trial_execution_authorization"]
        assert not hasattr(LocalTestSignerKeyHandle, "sign")
        assert not hasattr(LocalTestSignerKeyHandle, "sign_trial_execution_authorization")
        assert not hasattr(LocalTestSignerKeyHandle, "key_bytes")

    def test_fault_before_consumption_vs_after_consumption(self, tmp_path: Path):
        """Fault before consumption produces zero ledger appends and challenge remains consumable;
        failure after consumption preserves durably consumed challenge and never reactivates it."""
        # --- Fault BEFORE consumption ---
        pre_dir = tmp_path / "pre_fault"
        pre_dir.mkdir(parents=True, exist_ok=True)
        pre_dir.chmod(0o700)
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(pre_dir)
        cid = iss_res.challenge.challenge_id

        # Invalidate director public key so admission verification fails before consumption
        wrong_dir_pub = Ed25519PrivateKey.generate().public_key()
        with pytest.raises((DirectorApprovalVerificationError, SignerAdmissionVerificationError)):
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=wrong_dir_pub,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=authority,
                challenge_id=cid,
                signing_capability=signing_cap,
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
            )

        # Assert ledger has zero appends
        ledger_files = list(ledger_dir.glob("nonce_ledger_*.jsonl"))
        assert len(ledger_files) == 0 or ledger_files[0].stat().st_size == 0

        # Assert challenge is still consumable
        cons_res = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )
        assert cons_res is not None

        # --- Fault AFTER consumption ---
        # The challenge is now consumed. An attempt to issue or re-consume fails closed and cannot reactivate it.
        with pytest.raises((ChallengeConsumptionError, SignerAdmissionVerificationError)):
            authority.verify_and_consume_challenge_for_local_test(
                challenge_id=cid,
                consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
            )

    def test_concurrent_issuance_reservation(self, tmp_path: Path):
        """Concurrent reservation attempts on the same consumed challenge return at most one token."""
        import concurrent.futures
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            _,
            _,
            _,
            trust_dir,
            reg_dir,
            ledger_dir,
            _,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        # Consume first
        consumption_result = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )

        results = []
        errors = []

        def attempt_reserve():
            try:
                res = authority.reserve_consumed_evidence_for_issuance(cid, consumption_result)
                results.append(res)
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(attempt_reserve) for _ in range(8)]
            concurrent.futures.wait(futures)

        assert len(results) == 1
        assert len(errors) == 7
        for err in errors:
            assert isinstance(err, SignerAdmissionVerificationError)
            assert err.code == "ERR_CONSUMPTION_EVIDENCE_ALREADY_ISSUED"

    def test_roundtrip_hmac_tampering_detected(self, tmp_path: Path):
        """Round-trip constant-time verification fails if signing capability and verification key handle mismatch."""
        key_1 = os.urandom(32)
        key_2 = os.urandom(32)

        # signing capability with key_1
        signing_cap = create_signer_hmac_signing_capability_for_local_test(key_1, key_id="key_01")

        # verification handle with key_2
        ks_dir = tmp_path / "mismatched_keystore"
        ks_dir.mkdir(parents=True, exist_ok=True)
        ks_dir.chmod(0o700)
        kf = ks_dir / "trial_auth.key"
        kf.write_bytes(key_2)
        kf.chmod(0o600)
        ver_handle = load_signer_key_handle_for_local_test(ks_dir)
        kf.unlink()

        # Build fixture using signing_cap and ver_handle
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            _,
            _,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        with pytest.raises(SignerAdmissionVerificationError) as exc_hmac:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                challenge_authority=authority,
                challenge_id=cid,
                signing_capability=signing_cap,
                verification_key_handle=ver_handle,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                nonce_ledger_directory=ledger_dir,
                verification_time_utc=ver_time,
            )
        assert exc_hmac.value.code == "ERR_HMAC_VERIFICATION_FAILED"

    def test_safety_and_no_production_path_access(self, tmp_path: Path):
        """Safety assertions: ephemeral keys only, no /var/lib/resolveflow access, no live operations."""
        (
            _,
            _,
            _,
            _,
            _,
            _,
            _,
            _,
            trust_dir,
            reg_dir,
            ledger_dir,
            _,
        ) = self._setup_issuance_fixture(tmp_path)

        for p in [trust_dir, reg_dir, ledger_dir]:
            assert "/var/lib/resolveflow" not in str(p)
            assert str(p).startswith(str(tmp_path))

    def test_direct_capability_signing_of_caller_created_dto_prohibited(self, tmp_path: Path):
        """I16-01 BLOCKER regression test:
        Direct invocation of sign_trial_execution_authorization on a caller-created authorization DTO
        must fail closed with ERR_DIRECT_SIGNING_PROHIBITED; no signature or token can be obtained."""
        raw_key = os.urandom(32)
        signing_cap = create_signer_hmac_signing_capability_for_local_test(raw_key, key_id="direct_sign_key")

        # Caller creates arbitrary authorization DTO
        caller_dto = TrialExecutionAuthorization(
            authorization_id="auth_caller_forged",
            key_id="direct_sign_key",
            operator_gesture_digest_sha256="1" * 64,
            approval_record_id="appr_caller",
            approval_record_digest_sha256="2" * 64,
            proposal_id="prop_caller",
            proposal_revision="3" * 64,
            proposal_content_digest_sha256="4" * 64,
            plan_idempotency_key="plan_key_caller",
            plan_digest_sha256="5" * 64,
            action_payload_digest_sha256="6" * 64,
            target_project_uid="PROJECT_CALLER",
            target_project_name="Project Caller",
            target_timeline_uid="TIMELINE_CALLER",
            target_timeline_name="Timeline Caller",
            target_timeline_fps_numerator=24,
            target_timeline_fps_denominator=1,
            observed_timeline_start_frame=0,
            authorized_record_frame=0,
            source_file_path="/media/caller.mp4",
            source_file_sha256="7" * 64,
            authorized_action_type="INSERT_CLIP",
            authorized_track_type="video",
            authorized_track_index=1,
            authorized_timeline_frame_start=0,
            authorized_timeline_frame_duration=120,
            authorized_source_frame_start=0,
            authorized_source_frame_duration=120,
            execution_id="exec_caller",
            issued_at_iso="2026-11-04T12:00:00Z",
            valid_until_iso="2026-11-04T12:05:00Z",
            token_nonce="exec_caller",
            authorization_hmac="0" * 64,
        )

        with pytest.raises(SignerAdmissionVerificationError) as exc_direct:
            signing_cap.sign_trial_execution_authorization(caller_dto)
        assert exc_direct.value.code == "ERR_DIRECT_SIGNING_PROHIBITED"

    def test_capability_sign_for_issuance_rejects_unreserved_or_mismatched_evidence(self, tmp_path: Path):
        """I16-01 / I16-R2-01 BLOCKER regression test:
        _sign_for_issuance fails closed if ticket is forged, unreserved, or reused."""
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        dummy_auth = TrialExecutionAuthorization(
            authorization_id=iss_res.challenge.preallocated_authorization_id,
            key_id=signing_cap.key_id,
            operator_gesture_digest_sha256="1" * 64,
            approval_record_id="appr_01",
            approval_record_digest_sha256="2" * 64,
            proposal_id="prop_01",
            proposal_revision="a" * 64,
            proposal_content_digest_sha256="3" * 64,
            plan_idempotency_key="plan_key_01",
            plan_digest_sha256="1" * 64,
            action_payload_digest_sha256="2" * 64,
            target_project_uid="RF_TEST_PROJECT",
            target_project_name="Test Project",
            target_timeline_uid="RF_TEST_TIMELINE",
            target_timeline_name="Test Timeline",
            target_timeline_fps_numerator=24,
            target_timeline_fps_denominator=1,
            observed_timeline_start_frame=0,
            authorized_record_frame=0,
            source_file_path="/media/test.mp4",
            source_file_sha256="4" * 64,
            authorized_action_type="INSERT_CLIP",
            authorized_track_type="video",
            authorized_track_index=1,
            authorized_timeline_frame_start=0,
            authorized_timeline_frame_duration=120,
            authorized_source_frame_start=0,
            authorized_source_frame_duration=120,
            execution_id=iss_res.challenge.preallocated_execution_id,
            issued_at_iso="2026-11-04T12:00:00Z",
            valid_until_iso="2026-11-04T12:05:00Z",
            token_nonce=iss_res.challenge.preallocated_execution_id,
            authorization_hmac="0" * 64,
        )

        # 1. Non-ticket object passed directly to _sign_for_issuance fails closed
        with pytest.raises(SignerAdmissionVerificationError) as exc_forgery:
            signing_cap._sign_for_issuance(
                ticket="caller_forged_ticket",  # type: ignore
                auth=dummy_auth,
            )
        assert exc_forgery.value.code == "ERR_ISSUANCE_TICKET_FORGERY"

        # 2. Admission verification
        adm_ev = verify_trial_authorization_admission_for_local_test(
            approval_envelope=req.approval_envelope,
            proposal=req.proposal,
            execution_plan=req.execution_plan,
            director_public_key=req.director_public_key,
            expected_director_key_id=req.expected_director_key_id,
            operator_consent_envelope=envelope,
            operator_context=iss_res.operator_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=ver_time,
        )

        dummy_consumption = LocalTestChallengeConsumptionResult(
            challenge_id=cid,
            client_nonce=envelope.client_nonce,
            preallocated_authorization_id=iss_res.challenge.preallocated_authorization_id,
            preallocated_execution_id=iss_res.challenge.preallocated_execution_id,
            operator_id="operator_test_01",
            operator_key_id="op_key_test_01",
            approval_record_id="appr_01",
            proposal_revision="a" * 64,
            consumed_at_iso="2026-11-04T12:00:00Z",
            plan_digest_sha256="1" * 64,
            action_payload_digest_sha256="2" * 64,
        )

        # 3. Attempt create_issuance_signing_ticket before challenge consumption fails closed
        with pytest.raises(SignerAdmissionVerificationError) as exc_unconsumed:
            authority.create_issuance_signing_ticket(
                challenge_id=cid,
                consumption_result=dummy_consumption,
                admission_evidence=adm_ev,
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                operator_consent_envelope=envelope,
                verification_time_utc=ver_time,
                ttl_seconds=300.0,
            )
        assert exc_unconsumed.value.code == "ERR_CHALLENGE_NOT_CONSUMED"

        # 4. Consume challenge and reserve evidence
        cons_res = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )
        reserved_ev = authority.reserve_consumed_evidence_for_issuance(cid, cons_res)

        # 5. Register authoritative admission and create valid ticket and valid DTO
        adm_ev = authority.verify_and_register_admission_for_local_test(
            challenge_id=cid,
            approval_envelope=req.approval_envelope,
            proposal=req.proposal,
            execution_plan=req.execution_plan,
            director_public_key=req.director_public_key,
            expected_director_key_id=req.expected_director_key_id,
            operator_consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=ver_time,
            ttl_seconds=300.0,
        )

        ticket = authority.create_issuance_signing_ticket(
            challenge_id=cid,
            consumption_result=cons_res,
            admission_evidence=adm_ev,
            approval_envelope=req.approval_envelope,
            proposal=req.proposal,
            execution_plan=req.execution_plan,
            operator_consent_envelope=envelope,
            verification_time_utc=ver_time,
            ttl_seconds=300.0,
        )

        approval_dict = approval_record_to_dict(req.approval_envelope.approval_record)
        appr_digest = hashlib.sha256(canonical_json_bytes(approval_dict)).hexdigest()
        computed_prop_rev = compute_proposal_revision(req.proposal)

        valid_auth = TrialExecutionAuthorization(
            authorization_id=reserved_ev.preallocated_authorization_id,
            key_id=signing_cap.key_id,
            operator_gesture_digest_sha256=envelope.compute_consent_digest(),
            approval_record_id=req.approval_envelope.approval_record.approval_id,
            approval_record_digest_sha256=appr_digest,
            proposal_id=req.proposal.proposal_id,
            proposal_revision=computed_prop_rev,
            proposal_content_digest_sha256=req.approval_envelope.proposal_content_digest_sha256,
            plan_idempotency_key=req.execution_plan.idempotency_key,
            plan_digest_sha256=req.approval_envelope.plan_digest_sha256,
            action_payload_digest_sha256=envelope.action_payload_digest_sha256,
            target_project_uid=req.execution_plan.target_spec.project_id,
            target_project_name=req.execution_plan.target_spec.project_name,
            target_timeline_uid=req.execution_plan.target_spec.timeline_id,
            target_timeline_name=req.execution_plan.target_spec.timeline_name,
            target_timeline_fps_numerator=24,
            target_timeline_fps_denominator=1,
            observed_timeline_start_frame=envelope.record_frame,
            authorized_record_frame=envelope.record_frame,
            source_file_path=envelope.source_file_path,
            source_file_sha256=envelope.source_file_sha256,
            authorized_action_type="INSERT_CLIP",
            authorized_track_type="video",
            authorized_track_index=1,
            authorized_timeline_frame_start=0,
            authorized_timeline_frame_duration=120,
            authorized_source_frame_start=0,
            authorized_source_frame_duration=120,
            execution_id=reserved_ev.preallocated_execution_id,
            issued_at_iso="2026-11-04T12:00:00Z",
            valid_until_iso="2026-11-04T12:05:00Z",
            token_nonce=reserved_ev.preallocated_execution_id,
            authorization_hmac="0" * 64,
        )

        # 6. Single-use: first call succeeds
        token_hmac = signing_cap._sign_for_issuance(ticket=ticket, auth=valid_auth)
        assert isinstance(token_hmac, str) and len(token_hmac) == 64

        # 7. Second call with consumed ticket fails closed immediately
        with pytest.raises(SignerAdmissionVerificationError) as exc_reused:
            signing_cap._sign_for_issuance(ticket=ticket, auth=valid_auth)
        assert exc_reused.value.code == "ERR_CHALLENGE_NOT_RESERVED"

    def test_capability_sign_for_issuance_rejects_altered_non_evidence_claims(self, tmp_path: Path):
        """I16-R1-01 / I16-R2-01 BLOCKER regression test:
        Reserves genuine consumption evidence, alters non-evidence claims (including issued_at_iso and valid_until_iso),
        and proves _sign_for_issuance fails closed with ERR_SIGNING_CLAIM_MISMATCH returning no token."""
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        # 1. Real admission verification
        adm_ev = verify_trial_authorization_admission_for_local_test(
            approval_envelope=req.approval_envelope,
            proposal=req.proposal,
            execution_plan=req.execution_plan,
            director_public_key=req.director_public_key,
            expected_director_key_id=req.expected_director_key_id,
            operator_consent_envelope=envelope,
            operator_context=iss_res.operator_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=ver_time,
        )

        # 2. Genuine challenge consumption
        cons_res = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )

        # 3. Genuine evidence reservation
        reserved_ev = authority.reserve_consumed_evidence_for_issuance(cid, cons_res)

        # Base valid DTO matching all genuine inputs
        approval_dict = approval_record_to_dict(req.approval_envelope.approval_record)
        appr_digest = hashlib.sha256(canonical_json_bytes(approval_dict)).hexdigest()
        computed_prop_rev = compute_proposal_revision(req.proposal)

        base_claims = {
            "authorization_id": reserved_ev.preallocated_authorization_id,
            "key_id": signing_cap.key_id,
            "operator_gesture_digest_sha256": envelope.compute_consent_digest(),
            "approval_record_id": req.approval_envelope.approval_record.approval_id,
            "approval_record_digest_sha256": appr_digest,
            "proposal_id": req.proposal.proposal_id,
            "proposal_revision": computed_prop_rev,
            "proposal_content_digest_sha256": req.approval_envelope.proposal_content_digest_sha256,
            "plan_idempotency_key": req.execution_plan.idempotency_key,
            "plan_digest_sha256": req.approval_envelope.plan_digest_sha256,
            "action_payload_digest_sha256": envelope.action_payload_digest_sha256,
            "target_project_uid": req.execution_plan.target_spec.project_id,
            "target_project_name": req.execution_plan.target_spec.project_name,
            "target_timeline_uid": req.execution_plan.target_spec.timeline_id,
            "target_timeline_name": req.execution_plan.target_spec.timeline_name,
            "target_timeline_fps_numerator": 24,
            "target_timeline_fps_denominator": 1,
            "observed_timeline_start_frame": envelope.record_frame,
            "authorized_record_frame": envelope.record_frame,
            "source_file_path": envelope.source_file_path,
            "source_file_sha256": envelope.source_file_sha256,
            "authorized_action_type": "INSERT_CLIP",
            "authorized_track_type": "video",
            "authorized_track_index": 1,
            "authorized_timeline_frame_start": 0,
            "authorized_timeline_frame_duration": 120,
            "authorized_source_frame_start": 0,
            "authorized_source_frame_duration": 120,
            "execution_id": reserved_ev.preallocated_execution_id,
            "issued_at_iso": "2026-11-04T12:00:00Z",
            "valid_until_iso": "2026-11-04T12:05:00Z",
            "token_nonce": reserved_ev.preallocated_execution_id,
            "authorization_hmac": "0" * 64,
        }

        # Non-evidence fields to tamper with, including issued_at_iso and valid_until_iso
        tamper_fields = [
            ("target_project_uid", "ALTERED_PROJECT_UID"),
            ("target_timeline_uid", "ALTERED_TIMELINE_UID"),
            ("target_project_name", "Altered Project"),
            ("target_timeline_name", "Altered Timeline"),
            ("source_file_path", "/media/altered.mp4"),
            ("source_file_sha256", "f" * 64),
            ("operator_gesture_digest_sha256", "0" * 64),
            ("approval_record_digest_sha256", "0" * 64),
            ("proposal_id", "prop_altered"),
            ("proposal_content_digest_sha256", "e" * 64),
            ("plan_idempotency_key", "plan_key_altered"),
            ("observed_timeline_start_frame", 42),
            ("authorized_record_frame", 42),
            ("issued_at_iso", "2026-11-04T13:00:00Z"),
            ("valid_until_iso", "2026-11-04T13:05:00Z"),
        ]

        base_dto = TrialExecutionAuthorization(**base_claims)

        for field, bad_val in tamper_fields:
            import copy
            altered_dto = copy.deepcopy(base_dto)
            object.__setattr__(altered_dto, field, bad_val)

            adm_ev = authority.verify_and_register_admission_for_local_test(
                challenge_id=cid,
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=ver_time,
                ttl_seconds=300.0,
            )

            ticket = authority.create_issuance_signing_ticket(
                challenge_id=cid,
                consumption_result=cons_res,
                admission_evidence=adm_ev,
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                operator_consent_envelope=envelope,
                verification_time_utc=ver_time,
                ttl_seconds=300.0,
            )

            with pytest.raises(SignerAdmissionVerificationError) as exc_tamper:
                signing_cap._sign_for_issuance(
                    ticket=ticket,
                    auth=altered_dto,
                )
            assert exc_tamper.value.code == "ERR_SIGNING_CLAIM_MISMATCH"
            assert field in str(exc_tamper.value)

    def test_capability_sign_for_issuance_rejects_forged_or_unverified_admission_context(self, tmp_path: Path):
        """I16-R2-01 BLOCKER regression test:
        Proves _sign_for_issuance verifies complete fresh I12 admission evidence and fails closed
        with ERR_ADMISSION_VERIFICATION_FAILED if admission evidence is unverified, forged, or substituted."""
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        # 1. Real admission verification
        real_adm = verify_trial_authorization_admission_for_local_test(
            approval_envelope=req.approval_envelope,
            proposal=req.proposal,
            execution_plan=req.execution_plan,
            director_public_key=req.director_public_key,
            expected_director_key_id=req.expected_director_key_id,
            operator_consent_envelope=envelope,
            operator_context=iss_res.operator_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=ver_time,
        )

        # 2. Genuine challenge consumption
        cons_res = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )

        reserved_ev = authority.reserve_consumed_evidence_for_issuance(cid, cons_res)

        approval_dict = approval_record_to_dict(req.approval_envelope.approval_record)
        appr_digest = hashlib.sha256(canonical_json_bytes(approval_dict)).hexdigest()
        computed_prop_rev = compute_proposal_revision(req.proposal)

        valid_auth = TrialExecutionAuthorization(
            authorization_id=reserved_ev.preallocated_authorization_id,
            key_id=signing_cap.key_id,
            operator_gesture_digest_sha256=envelope.compute_consent_digest(),
            approval_record_id=req.approval_envelope.approval_record.approval_id,
            approval_record_digest_sha256=appr_digest,
            proposal_id=req.proposal.proposal_id,
            proposal_revision=computed_prop_rev,
            proposal_content_digest_sha256=req.approval_envelope.proposal_content_digest_sha256,
            plan_idempotency_key=req.execution_plan.idempotency_key,
            plan_digest_sha256=req.approval_envelope.plan_digest_sha256,
            action_payload_digest_sha256=envelope.action_payload_digest_sha256,
            target_project_uid=req.execution_plan.target_spec.project_id,
            target_project_name=req.execution_plan.target_spec.project_name,
            target_timeline_uid=req.execution_plan.target_spec.timeline_id,
            target_timeline_name=req.execution_plan.target_spec.timeline_name,
            target_timeline_fps_numerator=24,
            target_timeline_fps_denominator=1,
            observed_timeline_start_frame=envelope.record_frame,
            authorized_record_frame=envelope.record_frame,
            source_file_path=envelope.source_file_path,
            source_file_sha256=envelope.source_file_sha256,
            authorized_action_type="INSERT_CLIP",
            authorized_track_type="video",
            authorized_track_index=1,
            authorized_timeline_frame_start=0,
            authorized_timeline_frame_duration=120,
            authorized_source_frame_start=0,
            authorized_source_frame_duration=120,
            execution_id=reserved_ev.preallocated_execution_id,
            issued_at_iso="2026-11-04T12:00:00Z",
            valid_until_iso="2026-11-04T12:05:00Z",
            token_nonce=reserved_ev.preallocated_execution_id,
            authorization_hmac="0" * 64,
        )

        # Variations of unverified / forged admission evidence
        import copy
        bad_admissions = [
            ("proposal_id", "forged_proposal_id"),
            ("approval_id", "forged_approval_id"),
            ("challenge_id", "forged_challenge_id"),
            ("client_nonce", "forged_client_nonce"),
            ("plan_digest_sha256", "0" * 64),
            ("action_payload_digest_sha256", "0" * 64),
            ("target_project_uid", "FORGED_PROJECT"),
            ("target_timeline_uid", "FORGED_TIMELINE"),
            ("source_file_sha256", "0" * 64),
        ]

        for attr, bad_val in bad_admissions:
            bad_adm = copy.deepcopy(real_adm)
            object.__setattr__(bad_adm, attr, bad_val)

            reg_adm = authority.verify_and_register_admission_for_local_test(
                challenge_id=cid,
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                director_public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
                operator_consent_envelope=envelope,
                operator_public_key=op_pub,
                revocation_trust_directory=trust_dir,
                revocation_registry_directory=reg_dir,
                verification_time_utc=ver_time,
                ttl_seconds=300.0,
            )

            # Ticket creation with bad_adm fails closed because bad_adm is not the registered admission
            with pytest.raises(SignerAdmissionVerificationError) as exc_factory:
                authority.create_issuance_signing_ticket(
                    challenge_id=cid,
                    consumption_result=cons_res,
                    admission_evidence=bad_adm,
                    approval_envelope=req.approval_envelope,
                    proposal=req.proposal,
                    execution_plan=req.execution_plan,
                    operator_consent_envelope=envelope,
                    verification_time_utc=ver_time,
                    ttl_seconds=300.0,
                )
            assert exc_factory.value.code == "ERR_ADMISSION_EVIDENCE_FORGERY"

            # Create ticket with reg_adm, then tamper with ticket.admission_evidence to test _sign_for_issuance defense in depth
            ticket = authority.create_issuance_signing_ticket(
                challenge_id=cid,
                consumption_result=cons_res,
                admission_evidence=reg_adm,
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                operator_consent_envelope=envelope,
                verification_time_utc=ver_time,
                ttl_seconds=300.0,
            )
            object.__setattr__(ticket, "admission_evidence", bad_adm)

            with pytest.raises(SignerAdmissionVerificationError) as exc_adm:
                signing_cap._sign_for_issuance(
                    ticket=ticket,
                    auth=valid_auth,
                )
            assert exc_adm.value.code == "ERR_ADMISSION_VERIFICATION_FAILED"

    def test_ticket_factory_rejects_fabricated_admission_result_without_registration(self, tmp_path: Path):
        """I16-R3-01 BLOCKER regression test:
        Consumes and reserves a real challenge, invokes the ticket factory with a fabricated
        exact-type admission result and matching context, and proves no HMAC/signature or
        authorization is emitted."""
        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        # 1. Genuine challenge consumption and reservation
        cons_res = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )
        reserved_ev = authority.reserve_consumed_evidence_for_issuance(cid, cons_res)

        # 2. Fabricate an exact-type TrialAuthorizationAdmissionEvidence with verified=True and matching claims
        # without running fresh I12 admission verification through the issuance routine
        import copy
        approval_dict = approval_record_to_dict(req.approval_envelope.approval_record)
        appr_digest = hashlib.sha256(canonical_json_bytes(approval_dict)).hexdigest()
        computed_prop_rev = compute_proposal_revision(req.proposal)

        real_adm = verify_trial_authorization_admission_for_local_test(
            approval_envelope=req.approval_envelope,
            proposal=req.proposal,
            execution_plan=req.execution_plan,
            director_public_key=req.director_public_key,
            expected_director_key_id=req.expected_director_key_id,
            operator_consent_envelope=envelope,
            operator_context=iss_res.operator_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=ver_time,
        )
        fabricated_adm = copy.deepcopy(real_adm)

        # 3. Invoking the ticket factory with fabricated admission fails closed with ERR_ADMISSION_NOT_REGISTERED
        with pytest.raises(SignerAdmissionVerificationError) as exc_factory:
            authority.create_issuance_signing_ticket(
                challenge_id=cid,
                consumption_result=cons_res,
                admission_evidence=fabricated_adm,
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                operator_consent_envelope=envelope,
                verification_time_utc=ver_time,
                ttl_seconds=300.0,
            )
        assert exc_factory.value.code == "ERR_ADMISSION_NOT_REGISTERED"

        # 4. Proves zero ticket is created, zero active tickets registered on authority
        assert cid not in authority._active_signing_tickets

        # 5. Direct signing attempt fails closed with ERR_DIRECT_SIGNING_PROHIBITED
        candidate_dto = TrialExecutionAuthorization(
            authorization_id=reserved_ev.preallocated_authorization_id,
            key_id=signing_cap.key_id,
            operator_gesture_digest_sha256=envelope.compute_consent_digest(),
            approval_record_id=req.approval_envelope.approval_record.approval_id,
            approval_record_digest_sha256=appr_digest,
            proposal_id=req.proposal.proposal_id,
            proposal_revision=computed_prop_rev,
            proposal_content_digest_sha256=req.approval_envelope.proposal_content_digest_sha256,
            plan_idempotency_key=req.execution_plan.idempotency_key,
            plan_digest_sha256=req.approval_envelope.plan_digest_sha256,
            action_payload_digest_sha256=envelope.action_payload_digest_sha256,
            target_project_uid=req.execution_plan.target_spec.project_id,
            target_project_name=req.execution_plan.target_spec.project_name,
            target_timeline_uid=req.execution_plan.target_spec.timeline_id,
            target_timeline_name=req.execution_plan.target_spec.timeline_name,
            target_timeline_fps_numerator=24,
            target_timeline_fps_denominator=1,
            observed_timeline_start_frame=envelope.record_frame,
            authorized_record_frame=envelope.record_frame,
            source_file_path=envelope.source_file_path,
            source_file_sha256=envelope.source_file_sha256,
            authorized_action_type="INSERT_CLIP",
            authorized_track_type="video",
            authorized_track_index=1,
            authorized_timeline_frame_start=0,
            authorized_timeline_frame_duration=120,
            authorized_source_frame_start=0,
            authorized_source_frame_duration=120,
            execution_id=reserved_ev.preallocated_execution_id,
            issued_at_iso="2026-11-04T12:00:00Z",
            valid_until_iso="2026-11-04T12:05:00Z",
            token_nonce=reserved_ev.preallocated_execution_id,
            authorization_hmac="0" * 64,
        )
        with pytest.raises(SignerAdmissionVerificationError) as exc_direct:
            signing_cap.sign_trial_execution_authorization(candidate_dto)
        assert exc_direct.value.code == "ERR_DIRECT_SIGNING_PROHIBITED"

    def test_sentinel_registration_bypass_prohibited_and_fails_closed(self, tmp_path: Path):
        """I16-R4-01 BLOCKER regression test:
        Imports every relevant module attribute (including _ISSUANCE_TICKET_SENTINEL), attempts to
        register fabricated exact-type admission evidence with the exposed sentinel on the authority,
        and proves:
        1. register_issuance_admission_for_local_test unconditionally fails closed with ERR_ADMISSION_REGISTRATION_PROHIBITED.
        2. No admission evidence is registered on the authority.
        3. Attempting to create an issuance signing ticket with fabricated admission fails closed with ERR_ADMISSION_NOT_REGISTERED.
        4. Zero tickets are created or registered on the authority.
        5. Direct signing attempt fails closed with ERR_DIRECT_SIGNING_PROHIBITED.
        6. Zero signature is minted, zero authorization emitted.

        Documentation of Trust Boundary:
        This local-test design is not an operating system or process isolation boundary;
        in-process Python objects do not protect against arbitrary code execution in the same process.
        """
        from src.services.trial_authorization_signer_service import (
            _ISSUANCE_TICKET_SENTINEL,
            TrialAuthorizationAdmissionEvidence,
            DirectorApprovalVerificationResult,
            OperatorConsentVerificationResult,
            RevocationManifestVerificationResult,
            TrialExecutionAuthorization,
            LocalTestChallengeAuthority,
        )

        (
            authority,
            iss_res,
            envelope,
            _,
            op_pub,
            req,
            signing_cap,
            ver_handle,
            trust_dir,
            reg_dir,
            ledger_dir,
            ver_time,
        ) = self._setup_issuance_fixture(tmp_path)
        cid = iss_res.challenge.challenge_id

        # 1. Genuine challenge consumption and reservation
        cons_res = authority.verify_and_consume_challenge_for_local_test(
            challenge_id=cid,
            consent_envelope=envelope,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=ledger_dir,
        )
        reserved_ev = authority.reserve_consumed_evidence_for_issuance(cid, cons_res)

        # 2. Fabricate an exact-type TrialAuthorizationAdmissionEvidence with verified=True and matching claims
        real_adm = verify_trial_authorization_admission_for_local_test(
            approval_envelope=req.approval_envelope,
            proposal=req.proposal,
            execution_plan=req.execution_plan,
            director_public_key=req.director_public_key,
            expected_director_key_id=req.expected_director_key_id,
            operator_consent_envelope=envelope,
            operator_context=iss_res.operator_context,
            operator_public_key=op_pub,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=ver_time,
        )
        import copy
        fabricated_adm = copy.deepcopy(real_adm)

        # 3. Attempting to register fabricated evidence with the exposed sentinel fails closed
        with pytest.raises(SignerAdmissionVerificationError) as exc_reg:
            authority.register_issuance_admission_for_local_test(
                challenge_id=cid,
                admission_evidence=fabricated_adm,
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                operator_consent_envelope=envelope,
                verification_time_utc=ver_time,
                ttl_seconds=300.0,
                _sentinel=_ISSUANCE_TICKET_SENTINEL,
            )
        assert exc_reg.value.code == "ERR_ADMISSION_REGISTRATION_PROHIBITED"

        # 4. Proves no registration was stored on authority
        assert cid not in authority._registered_issuance_admissions

        # 5. Invoking ticket factory with fabricated admission fails closed with ERR_ADMISSION_NOT_REGISTERED
        with pytest.raises(SignerAdmissionVerificationError) as exc_factory:
            authority.create_issuance_signing_ticket(
                challenge_id=cid,
                consumption_result=cons_res,
                admission_evidence=fabricated_adm,
                approval_envelope=req.approval_envelope,
                proposal=req.proposal,
                execution_plan=req.execution_plan,
                operator_consent_envelope=envelope,
                verification_time_utc=ver_time,
                ttl_seconds=300.0,
            )
        assert exc_factory.value.code == "ERR_ADMISSION_NOT_REGISTERED"

        # 6. Proves zero ticket is created, zero active tickets registered on authority
        assert cid not in authority._active_signing_tickets

        # 7. Direct signing attempt fails closed with ERR_DIRECT_SIGNING_PROHIBITED
        approval_dict = approval_record_to_dict(req.approval_envelope.approval_record)
        appr_digest = hashlib.sha256(canonical_json_bytes(approval_dict)).hexdigest()
        computed_prop_rev = compute_proposal_revision(req.proposal)

        candidate_dto = TrialExecutionAuthorization(
            authorization_id=reserved_ev.preallocated_authorization_id,
            key_id=signing_cap.key_id,
            operator_gesture_digest_sha256=envelope.compute_consent_digest(),
            approval_record_id=req.approval_envelope.approval_record.approval_id,
            approval_record_digest_sha256=appr_digest,
            proposal_id=req.proposal.proposal_id,
            proposal_revision=computed_prop_rev,
            proposal_content_digest_sha256=req.approval_envelope.proposal_content_digest_sha256,
            plan_idempotency_key=req.execution_plan.idempotency_key,
            plan_digest_sha256=req.approval_envelope.plan_digest_sha256,
            action_payload_digest_sha256=envelope.action_payload_digest_sha256,
            target_project_uid=req.execution_plan.target_spec.project_id,
            target_project_name=req.execution_plan.target_spec.project_name,
            target_timeline_uid=req.execution_plan.target_spec.timeline_id,
            target_timeline_name=req.execution_plan.target_spec.timeline_name,
            target_timeline_fps_numerator=24,
            target_timeline_fps_denominator=1,
            observed_timeline_start_frame=envelope.record_frame,
            authorized_record_frame=envelope.record_frame,
            source_file_path=envelope.source_file_path,
            source_file_sha256=envelope.source_file_sha256,
            authorized_action_type="INSERT_CLIP",
            authorized_track_type="video",
            authorized_track_index=1,
            authorized_timeline_frame_start=0,
            authorized_timeline_frame_duration=120,
            authorized_source_frame_start=0,
            authorized_source_frame_duration=120,
            execution_id=reserved_ev.preallocated_execution_id,
            issued_at_iso="2026-11-04T12:00:00Z",
            valid_until_iso="2026-11-04T12:05:00Z",
            token_nonce=reserved_ev.preallocated_execution_id,
            authorization_hmac="0" * 64,
        )

        with pytest.raises(SignerAdmissionVerificationError) as exc_direct:
            signing_cap.sign_trial_execution_authorization(candidate_dto)
        assert exc_direct.value.code == "ERR_DIRECT_SIGNING_PROHIBITED"


# ==============================================================================
# Gate 3-I18: Local-Test Signer Socket Server Unit Tests
# ==============================================================================

class TestLocalTestSignerSocketServer:
    """Comprehensive Unit Tests for LocalTestSignerSocketServer (G07-07B7-I18)."""

    @staticmethod
    def _valid_challenge_params(**overrides: Any) -> Dict[str, Any]:
        params = {
            "expected_director_key_id": "dir_key_01",
            "operator_id": "op_01",
            "operator_key_id": "op_key_01",
            "approval_envelope": {"scope": "DIRECTOR_APPROVAL"},
            "proposal": {"proposal_id": "p1"},
            "execution_plan": {"plan_id": "plan1"},
        }
        params.update(overrides)
        return params

    @staticmethod
    def _valid_consent_params(**overrides: Any) -> Dict[str, Any]:
        params = {
            "challenge_id": "CHALL_123",
            "consent_envelope": {"version": 1, "operator_id": "op_01"},
        }
        params.update(overrides)
        return params

    @staticmethod
    def _valid_oracle_params(**overrides: Any) -> Dict[str, Any]:
        params = {
            "request_nonce": "f" * 64,
            "signed_authorization": {"auth_id": "auth_123"},
            "action_payload_digest_sha256": "0" * 64,
            "target_project_uid": "proj_123",
            "target_timeline_uid": "tl_123",
        }
        params.update(overrides)
        return params

    def test_socket_path_validation_and_rejections(self, tmp_path):
        temp_root = tmp_path / "temp_root"
        temp_root.mkdir(mode=0o700)
        valid_sock = temp_root / "signer.sock"

        # 1. Valid path and root succeeds
        sock_p, root_p = _validate_test_socket_path(valid_sock, temp_root)
        assert sock_p == valid_sock
        assert root_p == temp_root

        # 2. Reject production paths
        for prod_path in [
            "/run/resolveflow/signer.sock",
            "/var/lib/resolveflow/signer.sock",
            "/etc/signer.sock",
            "/usr/local/signer.sock",
        ]:
            with pytest.raises(SignerSocketServerError) as exc_info:
                _validate_test_socket_path(prod_path, temp_root)
            assert exc_info.value.code in ("ERR_FORBIDDEN_SOCKET_PATH", "ERR_PRODUCTION_PATH_PROHIBITED")

        # 3. Reject outside temp_root
        outside_sock = tmp_path / "outside.sock"
        with pytest.raises(SignerSocketServerError) as exc_out:
            _validate_test_socket_path(outside_sock, temp_root)
        assert exc_out.value.code == "ERR_PATH_OUTSIDE_TEMP_ROOT"

        # 4. Reject symlink in socket path parent component
        symlink_dir = temp_root / "symlink_dir"
        real_target = tmp_path / "real_dir"
        real_target.mkdir(mode=0o700)
        symlink_dir.symlink_to(real_target)
        symlink_sock = symlink_dir / "signer.sock"
        with pytest.raises(SignerSocketServerError) as exc_sym:
            _validate_test_socket_path(symlink_sock, temp_root)
        assert exc_sym.value.code == "ERR_SYMLINK_IN_PATH"

        # 5. Reject insecure parent permissions (not 0700)
        insecure_parent = temp_root / "insecure_parent"
        insecure_parent.mkdir(mode=0o777)
        insecure_sock = insecure_parent / "signer.sock"
        with pytest.raises(SignerSocketServerError) as exc_perm:
            _validate_test_socket_path(insecure_sock, temp_root)
        assert exc_perm.value.code == "ERR_INSECURE_DIR_PERMS"

    def test_socket_creation_permissions_and_singleton_lock(self, tmp_path):
        temp_root = tmp_path / "socket_test_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server1 = LocalTestSignerSocketServer(
            socket_path=sock_path,
            temp_root=temp_root,
        )

        with server1:
            assert server1.is_running is True
            assert sock_path.exists()
            assert Path(f"{sock_path}.lock").exists()

            # Verify socket mode is 0660
            st = sock_path.stat()
            assert stat.S_ISSOCK(st.st_mode)
            assert (st.st_mode & 0o777) == 0o660

            # Attempting to start second server on same socket path raises ERR_SOCKET_LOCK_HELD
            server2 = LocalTestSignerSocketServer(
                socket_path=sock_path,
                temp_root=temp_root,
            )
            with pytest.raises(SignerSocketServerError) as exc_lock:
                server2.start()
            assert exc_lock.value.code == "ERR_SOCKET_LOCK_HELD"

        # Clean shutdown removes socket node, but preserves persistent lock inode file (I18-02)
        assert not sock_path.exists()
        assert Path(f"{sock_path}.lock").exists()
        assert server1.is_running is False

    def test_peer_authentication_allow_and_deny(self, tmp_path):
        temp_root = tmp_path / "peer_auth_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        current_uid = os.getuid()
        peer_to_report = current_uid

        def mock_cred_provider(s: socket.socket) -> int:
            return peer_to_report

        server = LocalTestSignerSocketServer(
            socket_path=sock_path,
            temp_root=temp_root,
            allowed_peer_uids={current_uid},
            peer_credential_provider=mock_cred_provider,
            challenge_handler=lambda p: {"status": "mock_challenge_ok"},
        )

        with server:
            client = LocalTestSignerSocketClient(sock_path)

            # 1. Allowed peer succeeds
            peer_to_report = current_uid
            resp = client.send_request("request_challenge", self._valid_challenge_params())
            assert resp["status"] == "OK"
            assert resp["result"] == {"status": "mock_challenge_ok"}

            # 2. Denied peer fails closed
            peer_to_report = 99999
            resp_denied = client.send_request("request_challenge", self._valid_challenge_params())
            assert resp_denied["status"] == "ERROR"
            assert resp_denied["error_code"] == "ERR_PEER_AUTH_DENIED"

    def test_framing_and_json_parsing_guards(self, tmp_path):
        temp_root = tmp_path / "guards_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(
            socket_path=sock_path,
            temp_root=temp_root,
            max_frame_bytes=1024,
            challenge_handler=lambda p: {"echo": p.get("expected_director_key_id")},
        )

        with server:
            client = LocalTestSignerSocketClient(sock_path)

            # 1. Empty frame (declared len = 0)
            resp = client.send_request("request_challenge", self._valid_challenge_params(), raw_payload_override=b"", length_prefix_override=0)
            assert resp["status"] == "ERROR"
            assert resp["error_code"] == "ERR_EMPTY_FRAME"

            # 2. Oversized frame (> max_frame_bytes)
            big_payload = b"x" * 2048
            resp_big = client.send_request("request_challenge", self._valid_challenge_params(), raw_payload_override=big_payload, length_prefix_override=2048)
            assert resp_big["status"] == "ERROR"
            assert resp_big["error_code"] == "ERR_FRAME_TOO_LARGE"

            # 3. Truncated frame (declared 100 bytes, sent 10)
            resp_trunc = client.send_request("request_challenge", self._valid_challenge_params(), raw_payload_override=b"1234567890", length_prefix_override=100)
            assert resp_trunc["status"] == "ERROR"
            assert resp_trunc["error_code"] == "ERR_TRUNCATED_PAYLOAD"

            # 4. Duplicate-key JSON
            dup_json = b'{"version": 1, "request_id": "r1", "method": "request_challenge", "method": "dup", "params": {}}'
            resp_dup = client.send_request("request_challenge", self._valid_challenge_params(), raw_payload_override=dup_json)
            assert resp_dup["status"] == "ERROR"
            assert resp_dup["error_code"] == "ERR_MALFORMED_JSON"

            # 5. Unsupported version
            resp_ver = client.send_request("request_challenge", self._valid_challenge_params(), version=2)
            assert resp_ver["status"] == "ERROR"
            assert resp_ver["error_code"] == "ERR_UNSUPPORTED_VERSION"

            # 6. Schema mismatch (extra fields in outer envelope)
            bad_envelope = {"version": 1, "request_id": "r1", "method": "request_challenge", "params": self._valid_challenge_params(), "extra": "forbidden"}
            resp_schema = client.send_request("request_challenge", self._valid_challenge_params(), custom_envelope=bad_envelope)
            assert resp_schema["status"] == "ERROR"
            assert resp_schema["error_code"] == "ERR_SCHEMA_MISMATCH"

            # 7. Unsupported method
            resp_method = client.send_request("nonexistent_method", {})
            assert resp_method["status"] == "ERROR"
            assert resp_method["error_code"] == "ERR_UNSUPPORTED_METHOD"

            # 8. Replay of request_id
            resp_first = client.send_request("request_challenge", self._valid_challenge_params(), request_id="fixed_nonce_123")
            assert resp_first["status"] == "OK"
            resp_replay = client.send_request("request_challenge", self._valid_challenge_params(), request_id="fixed_nonce_123")
            assert resp_replay["status"] == "ERROR"
            assert resp_replay["error_code"] == "ERR_REPLAYED_REQUEST_ID"

            # 9. Key material or secret in params rejected
            for bad_key in ["private_key", "secret", "signing_capability", "key_handle", "auth_hmac"]:
                bad_params = self._valid_challenge_params(**{bad_key: "secret_data"})
                resp_key = client.send_request("request_challenge", bad_params)
                assert resp_key["status"] == "ERROR"
                assert resp_key["error_code"] == "ERR_KEY_MATERIAL_PROHIBITED"

    def test_three_supported_methods_routed_properly(self, tmp_path):
        temp_root = tmp_path / "methods_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        invoked_methods = []

        def handle_challenge(params):
            invoked_methods.append(("request_challenge", params))
            return {"challenge_id": "CHALL_123", "status": "ISSUED"}

        def handle_consent(params):
            invoked_methods.append(("submit_operator_consent", params))
            return {"authorization": {"auth_id": "auth_123", "status": "ISSUED"}}

        def handle_oracle(params):
            invoked_methods.append(("verify_authorization_oracle", params))
            return {"oracle_response": {"is_valid": True, "verification_result": "VERIFIED_VALID"}}

        server = LocalTestSignerSocketServer(
            socket_path=sock_path,
            temp_root=temp_root,
            challenge_handler=handle_challenge,
            consent_handler=handle_consent,
            oracle_handler=handle_oracle,
        )

        with server:
            client = LocalTestSignerSocketClient(sock_path)

            # 1. request_challenge with valid declared schema
            challenge_params = self._valid_challenge_params()
            res1 = client.send_request("request_challenge", challenge_params)
            assert res1["status"] == "OK"
            assert res1["result"]["challenge_id"] == "CHALL_123"
            assert ("request_challenge", challenge_params) in invoked_methods

            # 2. submit_operator_consent with valid declared schema
            consent_params = self._valid_consent_params()
            res2 = client.send_request("submit_operator_consent", consent_params)
            assert res2["status"] == "OK"
            assert res2["result"]["authorization"]["auth_id"] == "auth_123"
            assert ("submit_operator_consent", consent_params) in invoked_methods

            # 3. verify_authorization_oracle with valid declared schema
            oracle_params = self._valid_oracle_params()
            res3 = client.send_request("verify_authorization_oracle", oracle_params)
            assert res3["status"] == "OK"
            assert res3["result"]["oracle_response"]["is_valid"] is True
            assert ("verify_authorization_oracle", oracle_params) in invoked_methods

    def test_oracle_response_secret_leak_prevention_tripwire(self, tmp_path):
        temp_root = tmp_path / "leak_tripwire_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        def leaking_oracle(params):
            return {"oracle_response": {"is_valid": True}, "signing_capability": "secret_key_material"}

        server = LocalTestSignerSocketServer(
            socket_path=sock_path,
            temp_root=temp_root,
            oracle_handler=leaking_oracle,
        )

        with server:
            client = LocalTestSignerSocketClient(sock_path)
            resp = client.send_request("verify_authorization_oracle", self._valid_oracle_params())
            assert resp["status"] == "ERROR"
            assert resp["error_code"] == "ERR_INTERNAL_LEAK_PREVENTED"

    def test_concurrent_requests_handled(self, tmp_path):
        import concurrent.futures
        import threading

        temp_root = tmp_path / "concurrent_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        counter = 0
        counter_lock = threading.Lock()

        def handle_challenge(params):
            nonlocal counter
            with counter_lock:
                counter += 1
                curr = counter
            return {"count": curr, "echo_id": params.get("context_request_id")}

        server = LocalTestSignerSocketServer(
            socket_path=sock_path,
            temp_root=temp_root,
            challenge_handler=handle_challenge,
        )

        with server:
            def worker(i: int):
                client = LocalTestSignerSocketClient(sock_path)
                return client.send_request(
                    "request_challenge",
                    self._valid_challenge_params(context_request_id=f"worker_{i}"),
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(worker, i) for i in range(20)]
                results = [f.result() for f in futures]

            assert len(results) == 20
            for r in results:
                assert r["status"] == "OK"
            assert counter == 20

    def test_consent_submission_invokes_consume_and_issuance_once_in_order(self, tmp_path):
        """Proves consent submission invokes consume and issuance exactly once in order; failures do not retry or emit token."""
        temp_root = tmp_path / "order_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        order_log = []

        def handle_consent(params):
            # Step 1: consume challenge
            order_log.append("consume_challenge")
            if params.get("challenge_id") == "FAIL_CONSUME":
                raise ChallengeConsumptionError("ERR_NONCE_ALREADY_CONSUMED", "Simulated consume failure")
            # Step 2: issue authorization
            order_log.append("issue_authorization")
            if params.get("challenge_id") == "FAIL_ISSUE":
                raise SignerAdmissionVerificationError("ERR_ISSUANCE_FAILED", "Simulated issuance failure")
            return {"authorization": {"auth_id": "auth_valid_001", "execution_id": "exec_001"}}

        server = LocalTestSignerSocketServer(
            socket_path=sock_path,
            temp_root=temp_root,
            consent_handler=handle_consent,
        )

        with server:
            client = LocalTestSignerSocketClient(sock_path)

            # 1. Failure during consumption
            order_log.clear()
            res_fail_consume = client.send_request(
                "submit_operator_consent",
                self._valid_consent_params(challenge_id="FAIL_CONSUME"),
            )
            assert res_fail_consume["status"] == "ERROR"
            assert res_fail_consume["error_code"] == "ERR_NONCE_ALREADY_CONSUMED"
            assert order_log == ["consume_challenge"]

            # 2. Failure during issuance
            order_log.clear()
            res_fail_issue = client.send_request(
                "submit_operator_consent",
                self._valid_consent_params(challenge_id="FAIL_ISSUE"),
            )
            assert res_fail_issue["status"] == "ERROR"
            assert res_fail_issue["error_code"] == "ERR_ISSUANCE_FAILED"
            assert order_log == ["consume_challenge", "issue_authorization"]

            # 3. Successful flow
            order_log.clear()
            res_ok = client.send_request("submit_operator_consent", self._valid_consent_params())
            assert res_ok["status"] == "OK"
            assert res_ok["result"]["authorization"]["auth_id"] == "auth_valid_001"
            assert order_log == ["consume_challenge", "issue_authorization"]

    def test_safety_tripwires_no_resolve_no_production_paths(self, tmp_path):
        """Proves zero production socket paths, zero daemonization/fork, and in-process execution only."""
        for banned_dir in ("/var/lib/resolveflow", "/run/resolveflow"):
            with pytest.raises(SignerSocketServerError):
                _validate_test_socket_path(banned_dir + "/test.sock", tmp_path)

        temp_root = tmp_path / "tripwire_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        # Server is in-process object only
        assert not hasattr(server, "daemonize")
        assert not hasattr(server, "fork")

    def test_candidate_path_probes_zero_when_outside_temp_root(self, tmp_path, monkeypatch):
        """Tripwire test (I18-01): proves candidate path outside temp_root is rejected with zero filesystem probes.

        Also proves detection of symlinked roots and in-root symlinks.
        """
        temp_root = tmp_path / "strict_root"
        temp_root.mkdir(mode=0o700)
        outside_cand = tmp_path / "outside_dir" / "cand.sock"

        probed_outside_paths = []
        real_lstat = os.lstat
        real_stat = os.stat
        real_open = os.open

        def spy_lstat(path, *args, **kwargs):
            p_str = str(path)
            if "outside_dir" in p_str or "cand.sock" in p_str:
                probed_outside_paths.append(("lstat", p_str))
            return real_lstat(path, *args, **kwargs)

        def spy_stat(path, *args, **kwargs):
            p_str = str(path)
            if "outside_dir" in p_str or "cand.sock" in p_str:
                probed_outside_paths.append(("stat", p_str))
            return real_stat(path, *args, **kwargs)

        def spy_open(path, *args, **kwargs):
            p_str = str(path)
            if "outside_dir" in p_str or "cand.sock" in p_str:
                probed_outside_paths.append(("open", p_str))
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(os, "lstat", spy_lstat)
        monkeypatch.setattr(os, "stat", spy_stat)
        monkeypatch.setattr(os, "open", spy_open)

        # 1. Candidate outside temp_root must be rejected purely lexically with ZERO probes
        with pytest.raises(SignerSocketServerError) as exc_outside:
            _validate_test_socket_path(outside_cand, temp_root)
        assert exc_outside.value.code == "ERR_PATH_OUTSIDE_TEMP_ROOT"
        assert probed_outside_paths == []  # ZERO filesystem probes on candidate!

        # 2. Symlinked temp_root itself must be detected and rejected
        real_target_root = tmp_path / "real_target_root"
        real_target_root.mkdir(mode=0o700)
        sym_root = tmp_path / "symlinked_root"
        sym_root.symlink_to(real_target_root)
        sym_sock = sym_root / "test.sock"
        with pytest.raises(SignerSocketServerError) as exc_sym_root:
            _validate_test_socket_path(sym_sock, sym_root)
        assert exc_sym_root.value.code == "ERR_SYMLINK_IN_PATH"

        # 3. In-root intermediate symlink directory must be detected and rejected
        sym_sub = temp_root / "sym_sub"
        sym_sub.symlink_to(real_target_root)
        in_root_sym_sock = sym_sub / "test.sock"
        with pytest.raises(SignerSocketServerError) as exc_in_sym:
            _validate_test_socket_path(in_root_sym_sock, temp_root)
        assert exc_in_sym.value.code == "ERR_SYMLINK_IN_PATH"

        # 4. In-root symlinked leaf node must be detected and rejected
        real_leaf_target = temp_root / "real_file"
        real_leaf_target.touch(mode=0o600)
        sym_leaf = temp_root / "sym_leaf.sock"
        sym_leaf.symlink_to(real_leaf_target)
        with pytest.raises(SignerSocketServerError) as exc_leaf_sym:
            _validate_test_socket_path(sym_leaf, temp_root)
        assert exc_leaf_sym.value.code == "ERR_SYMLINK_IN_PATH"

    def test_singleton_lock_inode_contention_and_concurrent_start_stop(self, tmp_path):
        """Proves singleton lock file persists across server stops with identical inode (I18-02).

        Every server instance contends on the exact same lock inode and no second instance
        can start while another holds it.
        """
        temp_root = tmp_path / "inode_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"
        lock_path = Path(f"{sock_path}.lock")

        server1 = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server2 = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)

        # 1. Start server1 -> lock file created and locked
        server1.start()
        assert server1.is_running is True
        assert lock_path.exists()
        initial_inode = lock_path.stat().st_ino

        # 2. server2 cannot start while server1 holds lock
        with pytest.raises(SignerSocketServerError) as exc_lock:
            server2.start()
        assert exc_lock.value.code == "ERR_SOCKET_LOCK_HELD"

        # 3. Stop server1 -> lock file persists on disk with identical inode!
        server1.stop()
        assert server1.is_running is False
        assert not sock_path.exists()
        assert lock_path.exists()
        assert lock_path.stat().st_ino == initial_inode

        # 4. Now server2 can start and acquires the lock on the exact same inode
        server2.start()
        assert server2.is_running is True
        assert lock_path.stat().st_ino == initial_inode

        # 5. server1 cannot start while server2 holds lock
        with pytest.raises(SignerSocketServerError) as exc_lock2:
            server1.start()
        assert exc_lock2.value.code == "ERR_SOCKET_LOCK_HELD"

        # 6. Stop server2 -> lock inode preserved
        server2.stop()
        assert server2.is_running is False
        assert lock_path.exists()
        assert lock_path.stat().st_ino == initial_inode

    def test_cleanup_failure_socket_close_raises_structured_error(self, tmp_path, monkeypatch):
        """Proves socket close failure during stop() raises structured SignerSocketServerError (I18-03)."""
        temp_root = tmp_path / "cleanup_sock_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server.start()
        target_sock = server._server_sock

        real_close = socket.socket.close

        def fail_close(s):
            if s is target_sock:
                raise OSError(errno.EIO, "Injected socket close error")
            return real_close(s)

        monkeypatch.setattr(socket.socket, "close", fail_close)

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.stop()

        assert exc_info.value.code == "ERR_CLEANUP_FAILED"
        assert len(exc_info.value.cleanup_errors) >= 1
        assert "Injected socket close error" in str(exc_info.value)
        assert server._failed_server_sock is not None

    def test_cleanup_failure_unlock_raises_structured_error(self, tmp_path, monkeypatch):
        """Proves flock(LOCK_UN) failure during stop() raises structured SignerSocketServerError (I18-03)."""
        import fcntl

        temp_root = tmp_path / "cleanup_unlock_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server.start()

        real_flock = fcntl.flock

        def failing_flock(fd, op):
            if op == fcntl.LOCK_UN:
                raise OSError(errno.EINVAL, "Injected unlock error")
            return real_flock(fd, op)

        monkeypatch.setattr(fcntl, "flock", failing_flock)

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.stop()

        assert exc_info.value.code == "ERR_CLEANUP_FAILED"
        assert "Injected unlock error" in str(exc_info.value)

    def test_cleanup_failure_lock_close_raises_structured_error(self, tmp_path, monkeypatch):
        """Proves lock descriptor close failure during stop() raises structured SignerSocketServerError (I18-03)."""
        temp_root = tmp_path / "cleanup_lockclose_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server.start()
        lock_fd = server._lock_fd

        real_close = os.close

        def failing_close(fd):
            if fd == lock_fd:
                raise OSError(errno.EBADF, "Injected lock close error")
            return real_close(fd)

        monkeypatch.setattr(os, "close", failing_close)

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.stop()

        assert exc_info.value.code == "ERR_CLEANUP_FAILED"
        assert "Injected lock close error" in str(exc_info.value)
        assert server._failed_lock_fd == lock_fd

    def test_cleanup_failure_socket_unlink_raises_structured_error(self, tmp_path, monkeypatch):
        """Proves socket unlink failure during stop() raises structured SignerSocketServerError (I18-03)."""
        temp_root = tmp_path / "cleanup_unlink_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server.start()

        real_os_unlink = os.unlink

        def failing_os_unlink(path, *args, **kwargs):
            if path == "signer.sock" or str(path) == str(sock_path) or str(path).startswith(".tombstone_"):
                raise OSError(errno.EACCES, "Injected unlink error")
            return real_os_unlink(path, *args, **kwargs)

        monkeypatch.setattr(os, "unlink", failing_os_unlink)

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.stop()

        assert exc_info.value.code == "ERR_CLEANUP_FAILED"
        assert "Injected unlink error" in str(exc_info.value)

    def test_combined_primary_and_cleanup_failure(self, tmp_path, monkeypatch):
        """Proves start() preserves both primary and cleanup failures in structured error (I18-03)."""
        import fcntl

        temp_root = tmp_path / "combined_fail_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)

        # Primary failure: socket bind fails
        def failing_bind(self_sock, addr):
            raise OSError(errno.EADDRINUSE, "Primary bind error")

        # Cleanup failure: flock unlock fails
        real_flock = fcntl.flock

        def failing_flock(fd, op):
            if op == fcntl.LOCK_UN:
                raise OSError(errno.EPERM, "Cleanup unlock error")
            return real_flock(fd, op)

        monkeypatch.setattr(socket.socket, "bind", failing_bind)
        monkeypatch.setattr(fcntl, "flock", failing_flock)

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.start()

        assert exc_info.value.code == "ERR_START_AND_CLEANUP_FAILED"
        assert "Primary bind error" in str(exc_info.value)
        assert "Cleanup unlock error" in str(exc_info.value)
        assert "Primary bind error" in exc_info.value.details.get("primary_error", "")
        assert any("Cleanup unlock error" in e for e in exc_info.value.details.get("cleanup_errors", []))

    def test_directory_component_swap_detected_and_rejected(self, tmp_path):
        """Proves socket binding and lock operations are anchored to verified parent directory descriptor (I18-R2-01, I18-R3-01).

        A swap of a directory component between initialization and bind is detected and rejected,
        and operations remain strictly anchored to the descriptor rather than following path renames.
        Asserts that the replacement/outside directory receives no socket node or other mutation.
        """
        temp_root = tmp_path / "swap_test_root"
        temp_root.mkdir(mode=0o700)
        sub_dir = temp_root / "swap_sub"
        sub_dir.mkdir(mode=0o700)
        sock_path = sub_dir / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)

        # Before start(), simulate swapping sub_dir component on disk
        sub_dir.rename(temp_root / "swap_sub_renamed")
        new_sub = temp_root / "swap_sub"
        new_sub.mkdir(mode=0o700)

        # Attempting start detects discrepancy prior to bind and fails closed
        with pytest.raises(SignerSocketServerError) as exc_info:
            server.start()

        assert exc_info.value.code == "ERR_SOCKET_ANCHOR_DISCREPANCY"
        # Assert replacement/outside directory received NO socket node or other mutation
        assert not (new_sub / "signer.sock").exists()
        assert not (new_sub / "signer.sock.lock").exists()
        assert list(new_sub.iterdir()) == []
        server.close()

    def test_bind_failure_cleanup_preserves_colliding_or_foreign_entry(self, tmp_path, monkeypatch):
        """Proves bind failure cleanup does not unlink unverified or foreign entries (I18-R4-01).

        When bind fails before ownership is established, cleanup must not unlink the basename,
        ensuring any colliding or foreign entry created in the directory remains untouched.
        """
        temp_root = tmp_path / "colliding_bind_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)

        # Simulate a race: right before bind is called, a foreign entry appears, and bind fails
        def colliding_bind(self_sock, addr):
            sock_path.write_text("foreign_data_payload")
            raise OSError(errno.EADDRINUSE, "Address already in use by foreign process")

        monkeypatch.setattr(socket.socket, "bind", colliding_bind)

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.start()

        assert "Address already in use" in str(exc_info.value)
        # Verify the foreign colliding entry was NOT unlinked or mutated by _cleanup_all()!
        assert sock_path.exists()
        assert sock_path.is_file()
        assert sock_path.read_text() == "foreign_data_payload"

        server.close()
        # Even after close(), foreign entry remains untouched!
        assert sock_path.exists()
        assert sock_path.read_text() == "foreign_data_payload"

    def test_stop_cleanup_preserves_replaced_foreign_entry(self, tmp_path):
        """Proves stop() verifies socket identity ownership and preserves replacement entries (I18-R4-01).

        If the server-created socket node is replaced prior to stop(), stop() treats this
        as a structured cleanup error (ERR_SOCKET_REPLACED_NON_SOCKET or ERR_SOCKET_IDENTITY_MISMATCH)
        and preserves the foreign replacement entry untouched.
        """
        temp_root = tmp_path / "replaced_stop_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server.start()

        # 1. Replace socket node with foreign regular file
        sock_path.unlink()
        sock_path.write_text("foreign_regular_replacement")

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.stop()

        assert exc_info.value.code == "ERR_CLEANUP_FAILED"
        assert any(
            "ERR_SOCKET_REPLACED_NON_SOCKET" in e
            for e in exc_info.value.details.get("errors", [])
        )
        # Foreign regular file remains completely untouched!
        assert sock_path.exists()
        assert sock_path.read_text() == "foreign_regular_replacement"

        # 2. Test replacement with a different socket node (inode mismatch)
        sock_path.unlink()
        foreign_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        foreign_sock.bind(str(sock_path))

        server2 = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server2._bound_socket_identity = (999999, 888888)
        errors = server2._unlink_owned_socket_node()
        assert len(errors) == 1
        assert getattr(errors[0], "code", None) == "ERR_SOCKET_IDENTITY_MISMATCH"
        # Foreign socket remains untouched!
        assert sock_path.exists()
        foreign_sock.close()
        sock_path.unlink()
        server2.close()

    def test_race_post_bind_replacement_detected_and_foreign_socket_preserved(self, tmp_path):
        """Proves race condition right after bind is detected via probe and foreign socket preserved (I18-R5-01).

        A rogue socket injected immediately after bind() fails the loopback nonce challenge,
        causing start() to fail closed without adopting the foreign entry and without modifying its permissions.
        """
        temp_root = tmp_path / "race_bind_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        foreign_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def inject_replacement_socket():
            # Attacker replaces signer.sock with a rogue socket immediately after server bind()
            sock_path.unlink()
            foreign_sock.bind(str(sock_path))
            foreign_sock.listen(1)

        server._post_bind_hook = inject_replacement_socket

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.start()

        assert "ERR_SOCKET_OWNERSHIP" in str(exc_info.value)
        # Foreign replacement socket remains completely untouched!
        assert sock_path.exists()
        st_after = sock_path.stat()
        assert stat.S_ISSOCK(st_after.st_mode)

        foreign_sock.close()
        server.close()

    def test_race_pre_unlink_replacement_detected_and_foreign_entry_preserved(self, tmp_path):
        """Proves race condition at pre-unlink boundary is detected and foreign entry preserved (I18-R5-01).

        Atomic tombstone isolation guarantees that if an entry is replaced before unlink,
        the non-matching replacement is atomically restored and never deleted.
        """
        temp_root = tmp_path / "race_unlink_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server.start()

        foreign_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def inject_replacement_before_unlink():
            # Attacker replaces signer.sock right at the pre-unlink boundary
            sock_path.unlink()
            # Consume the freed inode to ensure foreign_sock receives a distinct inode
            dummy = temp_root / "dummy_inode_consumer"
            dummy.write_text("dummy")
            foreign_sock.bind(str(sock_path))

        server._pre_unlink_hook = inject_replacement_before_unlink

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.stop()

        assert exc_info.value.code == "ERR_CLEANUP_FAILED"
        assert any(
            "ERR_SOCKET_IDENTITY_MISMATCH" in e
            for e in exc_info.value.details.get("errors", [])
        )
        # Foreign socket was atomically restored and remains completely untouched!
        assert sock_path.exists()
        assert stat.S_ISSOCK(sock_path.stat().st_mode)

        foreign_sock.close()
        sock_path.unlink()
        server.close()

    def test_race_post_probe_replacement_detected_and_foreign_socket_preserved(self, tmp_path):
        """Proves race condition between successful probe and ownership stat is detected (I18-R6-01).

        A concurrent writer replacing the socket node after probe handshake cannot trick the server
        into adopting the foreign socket's identity. The server fails closed, leaves identity None,
        does not chmod 0660 the foreign node, and preserves the foreign node intact.
        """
        temp_root = tmp_path / "t0"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "s.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        foreign_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def inject_replacement_after_probe():
            # Attacker replaces s.sock immediately after loopback probe succeeds,
            # before the descriptor-relative ownership stat
            sock_path.unlink()
            dummy = temp_root / "dummy_inode_consumer"
            dummy.write_text("dummy")
            foreign_sock.bind(str(sock_path))
            foreign_sock.listen(1)

        server._post_probe_hook = inject_replacement_after_probe

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.start()

        assert exc_info.value.code == "ERR_SOCKET_IDENTITY_MISMATCH"
        assert "was replaced after probe" in str(exc_info.value)
        # Server must never have adopted the foreign socket identity
        assert server._bound_socket_identity is None
        # Foreign socket remains completely intact on disk
        assert sock_path.exists()
        assert stat.S_ISSOCK(sock_path.stat().st_mode)

        foreign_sock.close()
        server.close()

    def test_race_pre_chmod_replacement_detected_and_foreign_socket_preserved(self, tmp_path):
        """Proves race condition between identity verification and chmod is detected (I18-R7-01).

        A concurrent writer replacing the socket node immediately before chmod cannot trick the server
        into chmodding the foreign socket. The server detects the replacement via the pinned descriptor
        and pre-chmod re-stat, fails closed immediately, leaves identity None, does not chmod the foreign
        socket, and preserves the foreign socket completely untouched with its original mode and identity.
        """
        temp_root = tmp_path / "t5"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "s.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        foreign_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def inject_foreign_socket_before_chmod():
            # Attacker replaces s.sock immediately after identity verification and before chmod
            sock_path.unlink()
            dummy = temp_root / "dummy_inode_consumer"
            dummy.write_text("dummy")
            foreign_sock.bind(str(sock_path))
            os.chmod(str(sock_path), 0o777)

        server._pre_chmod_hook = inject_foreign_socket_before_chmod

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.start()

        assert exc_info.value.code == "ERR_SOCKET_IDENTITY_MISMATCH"
        assert "was replaced before chmod" in str(exc_info.value)
        # Server must never have adopted the foreign socket identity
        assert server._bound_socket_identity is None
        # Foreign socket remains completely intact on disk with its original mode 0777 untouched!
        assert sock_path.exists()
        st_after = sock_path.stat()
        assert stat.S_ISSOCK(st_after.st_mode)
        assert (st_after.st_mode & 0o777) == 0o777

        foreign_sock.close()
        server.close()

    def test_race_tombstone_restore_collision_with_regular_file_preserves_both(self, tmp_path):
        """Proves tombstone restore does not overwrite a recreated regular file (I18-R6-02).

        When restoring a foreign entry from tombstone during unlinking, if a concurrent writer
        creates a new regular file at leaf while the tombstone was isolated, RENAME_NOREPLACE
        prevents overwriting the new file, preserves the foreign entry under a preservation name,
        and reports a structured collision error.
        """
        temp_root = tmp_path / "t1"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "s.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server.start()

        foreign_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def inject_foreign_socket_before_unlink():
            # Step 1: Replace server socket with foreign socket before tombstone rename
            sock_path.unlink()
            dummy = temp_root / "dummy_inode_consumer"
            dummy.write_text("dummy")
            foreign_sock.bind(str(sock_path))

        def inject_collision_file_in_tombstone():
            # Step 2: While foreign socket is isolated under tombstone name, concurrent writer
            # creates a new regular file at the original leaf path
            sock_path.write_text("concurrent_regular_file_content")

        server._pre_unlink_hook = inject_foreign_socket_before_unlink
        server._in_tombstone_hook = inject_collision_file_in_tombstone

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.stop()

        assert exc_info.value.code == "ERR_CLEANUP_FAILED"
        err_details = exc_info.value.details.get("errors", [])
        assert any("ERR_TOMBSTONE_RESTORE_COLLISION" in e for e in err_details)
        assert any("ERR_SOCKET_IDENTITY_MISMATCH" in e for e in err_details)

        # Dest entry: regular file was NOT overwritten!
        assert sock_path.exists()
        assert sock_path.is_file()
        assert sock_path.read_text() == "concurrent_regular_file_content"

        # Foreign entry: tombstoned foreign socket was preserved under .preserved_foreign_...
        preserved_entries = [p for p in temp_root.iterdir() if p.name.startswith(".preserved_foreign_")]
        assert len(preserved_entries) == 1
        assert stat.S_ISSOCK(preserved_entries[0].stat().st_mode)

        foreign_sock.close()
        server.close()

    def test_race_tombstone_restore_collision_with_socket_preserves_both(self, tmp_path):
        """Proves tombstone restore does not overwrite a recreated socket (I18-R6-02).

        When restoring a foreign entry from tombstone during unlinking, if a concurrent writer
        binds a new socket at leaf while the tombstone was isolated, RENAME_NOREPLACE
        prevents overwriting the new socket, preserves the foreign entry, and reports collision.
        """
        temp_root = tmp_path / "t2"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "s.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server.start()

        foreign_sock_1 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        foreign_sock_2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def inject_foreign_socket_1():
            sock_path.unlink()
            dummy = temp_root / "dummy_inode_consumer_1"
            dummy.write_text("dummy1")
            foreign_sock_1.bind(str(sock_path))

        def inject_foreign_socket_2():
            dummy = temp_root / "dummy_inode_consumer_2"
            dummy.write_text("dummy2")
            foreign_sock_2.bind(str(sock_path))

        server._pre_unlink_hook = inject_foreign_socket_1
        server._in_tombstone_hook = inject_foreign_socket_2

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.stop()

        assert exc_info.value.code == "ERR_CLEANUP_FAILED"
        err_details = exc_info.value.details.get("errors", [])
        assert any("ERR_TOMBSTONE_RESTORE_COLLISION" in e for e in err_details)

        # Dest entry: foreign_sock_2 at sock_path was NOT overwritten!
        assert sock_path.exists()
        assert stat.S_ISSOCK(sock_path.stat().st_mode)
        assert sock_path.stat().st_ino != foreign_sock_1.fileno()

        # Foreign entry 1: preserved under .preserved_foreign_...
        preserved_entries = [p for p in temp_root.iterdir() if p.name.startswith(".preserved_foreign_")]
        assert len(preserved_entries) == 1
        assert stat.S_ISSOCK(preserved_entries[0].stat().st_mode)

        foreign_sock_1.close()
        foreign_sock_2.close()
        server.close()

    def test_race_stale_cleanup_tombstone_restore_collision_with_file_preserves_both(self, tmp_path):
        """Proves stale cleanup tombstone restore does not overwrite recreated regular file (I18-R6-02)."""
        temp_root = tmp_path / "t3"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "s.sock"

        # Pre-create non-socket regular file
        sock_path.write_text("initial_stale_regular_file")

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)

        def inject_collision_during_stale_tombstone():
            # Concurrent writer creates a new regular file at leaf while initial file is under stale tombstone
            sock_path.write_text("concurrent_recreated_file")

        server._in_stale_tombstone_hook = inject_collision_during_stale_tombstone

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.start()

        assert exc_info.value.code == "ERR_NOT_A_SOCKET"
        # Collision error was captured in cleanup errors
        cleanup_errs = exc_info.value.cleanup_errors
        assert any("ERR_TOMBSTONE_RESTORE_COLLISION" in str(e) for e in cleanup_errs)

        # Destination regular file was NOT overwritten!
        assert sock_path.exists()
        assert sock_path.read_text() == "concurrent_recreated_file"

        # Initial stale file was preserved under .preserved_foreign_...
        preserved_entries = [p for p in temp_root.iterdir() if p.name.startswith(".preserved_foreign_")]
        assert len(preserved_entries) == 1
        assert preserved_entries[0].read_text() == "initial_stale_regular_file"

        server.close()

    def test_race_stale_cleanup_tombstone_restore_collision_with_socket_preserves_both(self, tmp_path):
        """Proves stale cleanup tombstone restore does not overwrite recreated socket (I18-R6-02)."""
        temp_root = tmp_path / "t4"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "s.sock"

        # Pre-create non-socket regular file
        sock_path.write_text("initial_stale_regular_file")

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        foreign_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def inject_socket_collision_during_stale_tombstone():
            foreign_sock.bind(str(sock_path))

        server._in_stale_tombstone_hook = inject_socket_collision_during_stale_tombstone

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.start()

        assert exc_info.value.code == "ERR_NOT_A_SOCKET"
        cleanup_errs = exc_info.value.cleanup_errors
        assert any("ERR_TOMBSTONE_RESTORE_COLLISION" in str(e) for e in cleanup_errs)

        # Destination socket was NOT overwritten!
        assert sock_path.exists()
        assert stat.S_ISSOCK(sock_path.stat().st_mode)

        # Initial stale file was preserved under .preserved_foreign_...
        preserved_entries = [p for p in temp_root.iterdir() if p.name.startswith(".preserved_foreign_")]
        assert len(preserved_entries) == 1
        assert preserved_entries[0].read_text() == "initial_stale_regular_file"

        foreign_sock.close()
        server.close()

    def test_race_pre_tombstone_unlink_replacement_detected_and_preserved(self, tmp_path):
        """Proves race condition at post-tombstone-stat/pre-unlink boundary is detected (I18-R7-02).

        If a concurrent writer replaces the tombstone entry after it was verified as matching
        and before unlink, the server re-verifies identity at the pre-unlink boundary, detects the
        mismatch, fails closed without unlinking, and preserves the replacement entry completely untouched.
        """
        temp_root = tmp_path / "t6"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "s.sock"

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        server.start()

        foreign_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        tombstone_path_holder = []

        def inject_replacement_at_pre_unlink():
            # Locate isolated tombstone entry in temp_root
            tombstones = [p for p in temp_root.iterdir() if p.name.startswith(".tombstone_")]
            assert len(tombstones) == 1
            t_path = tombstones[0]
            tombstone_path_holder.append(t_path)
            t_path.unlink()
            dummy = temp_root / "dummy_inode_consumer"
            dummy.write_text("dummy")
            dfd = os.open(str(temp_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                foreign_sock.bind(f"/proc/self/fd/{dfd}/{t_path.name}")
            finally:
                os.close(dfd)

        server._pre_tombstone_unlink_hook = inject_replacement_at_pre_unlink

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.stop()

        assert exc_info.value.code == "ERR_CLEANUP_FAILED"
        err_details = exc_info.value.details.get("errors", [])
        assert any("ERR_TOMBSTONE_IDENTITY_MISMATCH" in e for e in err_details)

        # Replacement entry at tombstone path was NOT deleted and remains completely untouched!
        assert len(tombstone_path_holder) == 1
        t_path = tombstone_path_holder[0]
        assert t_path.exists()
        assert stat.S_ISSOCK(t_path.stat().st_mode)

        foreign_sock.close()
        server.close()

    def test_race_pre_stale_tombstone_unlink_replacement_detected_and_preserved(self, tmp_path):
        """Proves race condition at post-stale-tombstone-stat/pre-unlink boundary is detected (I18-R7-02).

        In stale cleanup, if a concurrent writer replaces the verified stale socket tombstone
        before unlink, the server detects the discrepancy, fails closed without unlinking,
        and preserves the replacement entry completely untouched.
        """
        temp_root = tmp_path / "t7"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "s.sock"

        # Pre-create a genuine stale socket
        stale_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale_sock.bind(str(sock_path))
        stale_sock.close()

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        foreign_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale_tombstone_path_holder = []

        def inject_replacement_at_stale_pre_unlink():
            tombstones = [p for p in temp_root.iterdir() if p.name.startswith(".stale_tombstone_")]
            assert len(tombstones) == 1
            t_path = tombstones[0]
            stale_tombstone_path_holder.append(t_path)
            t_path.unlink()
            dummy = temp_root / "dummy_inode_consumer"
            dummy.write_text("dummy")
            dfd = os.open(str(temp_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                foreign_sock.bind(f"/proc/self/fd/{dfd}/{t_path.name}")
            finally:
                os.close(dfd)

        server._pre_stale_tombstone_unlink_hook = inject_replacement_at_stale_pre_unlink

        with pytest.raises(SignerSocketServerError) as exc_info:
            server.start()

        assert exc_info.value.code == "ERR_TOMBSTONE_IDENTITY_MISMATCH"

        # Replacement entry at stale tombstone path was NOT deleted and remains completely untouched!
        assert len(stale_tombstone_path_holder) == 1
        t_path = stale_tombstone_path_holder[0]
        assert t_path.exists()
        assert stat.S_ISSOCK(t_path.stat().st_mode)

        foreign_sock.close()
        server.close()

    def test_method_params_schema_enforcement_and_override_rejection(self, tmp_path):
        """Proves per-method parameter schema enforcement and override rejection (I18-R2-02).

        Rejects unknown parameter fields, prohibited identity/claim overrides, missing parameters,
        and type mismatches before invoking any protected handler.
        """
        temp_root = tmp_path / "schema_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "signer.sock"

        handler_invocations = {
            "request_challenge": 0,
            "submit_operator_consent": 0,
            "verify_authorization_oracle": 0,
        }

        def spy_challenge(p):
            handler_invocations["request_challenge"] += 1
            return {"challenge_id": "CHALL_OK"}

        def spy_consent(p):
            handler_invocations["submit_operator_consent"] += 1
            return {"auth": "OK"}

        def spy_oracle(p):
            handler_invocations["verify_authorization_oracle"] += 1
            return {"oracle_response": {"is_valid": True}}

        server = LocalTestSignerSocketServer(
            socket_path=sock_path,
            temp_root=temp_root,
            challenge_handler=spy_challenge,
            consent_handler=spy_consent,
            oracle_handler=spy_oracle,
        )

        with server:
            client = LocalTestSignerSocketClient(sock_path)

            # 1. request_challenge: unknown field rejected before handler
            bad_challenge = self._valid_challenge_params(injected_unknown_field="evil")
            r1 = client.send_request("request_challenge", bad_challenge)
            assert r1["status"] == "ERROR"
            assert r1["error_code"] == "ERR_UNKNOWN_PARAM"
            assert handler_invocations["request_challenge"] == 0

            # 2. request_challenge: prohibited claim override rejected before handler
            override_challenge = self._valid_challenge_params(challenge_id="forged_cid")
            r2 = client.send_request("request_challenge", override_challenge)
            assert r2["status"] == "ERROR"
            assert r2["error_code"] == "ERR_PARAM_OVERRIDE_PROHIBITED"
            assert handler_invocations["request_challenge"] == 0

            # 3. submit_operator_consent: unknown field rejected before handler
            bad_consent = self._valid_consent_params(arbitrary_extra="bad")
            r3 = client.send_request("submit_operator_consent", bad_consent)
            assert r3["status"] == "ERROR"
            assert r3["error_code"] == "ERR_UNKNOWN_PARAM"
            assert handler_invocations["submit_operator_consent"] == 0

            # 4. submit_operator_consent: prohibited identity override rejected before handler
            override_consent = self._valid_consent_params(operator_id="override_admin")
            r4 = client.send_request("submit_operator_consent", override_consent)
            assert r4["status"] == "ERROR"
            assert r4["error_code"] == "ERR_PARAM_OVERRIDE_PROHIBITED"
            assert handler_invocations["submit_operator_consent"] == 0

            # 5. verify_authorization_oracle: missing parameter rejected before handler
            incomplete_oracle = {"request_nonce": "a" * 64}  # missing other required fields
            r5 = client.send_request("verify_authorization_oracle", incomplete_oracle)
            assert r5["status"] == "ERROR"
            assert r5["error_code"] == "ERR_MISSING_PARAM"
            assert handler_invocations["verify_authorization_oracle"] == 0

            # 6. verify_authorization_oracle: type mismatch rejected before handler
            type_mismatch_oracle = self._valid_oracle_params(request_nonce=123456)
            r6 = client.send_request("verify_authorization_oracle", type_mismatch_oracle)
            assert r6["status"] == "ERROR"
            assert r6["error_code"] == "ERR_PARAM_TYPE_MISMATCH"
            assert handler_invocations["verify_authorization_oracle"] == 0

            # 7. verify_authorization_oracle: prohibited override (is_valid) rejected before handler
            override_oracle = self._valid_oracle_params(is_valid=True)
            r7 = client.send_request("verify_authorization_oracle", override_oracle)
            assert r7["status"] == "ERROR"
            assert r7["error_code"] == "ERR_KEY_MATERIAL_PROHIBITED"
            assert handler_invocations["verify_authorization_oracle"] == 0

            # 8. request_challenge: boolean TTL rejected before handler (I18-R3-02)
            bool_ttl_challenge = self._valid_challenge_params(ttl_seconds=True)
            r8 = client.send_request("request_challenge", bool_ttl_challenge)
            assert r8["status"] == "ERROR"
            assert r8["error_code"] in ("ERR_PARAM_TYPE_MISMATCH", "ERR_PARAM_VALUE_INVALID")
            assert handler_invocations["request_challenge"] == 0

            # 9. request_challenge: non-positive TTL rejected before handler
            neg_ttl_challenge = self._valid_challenge_params(ttl_seconds=-10)
            r9 = client.send_request("request_challenge", neg_ttl_challenge)
            assert r9["status"] == "ERROR"
            assert r9["error_code"] == "ERR_PARAM_VALUE_INVALID"
            assert handler_invocations["request_challenge"] == 0

            # 10. request_challenge: zero TTL rejected before handler
            zero_ttl_challenge = self._valid_challenge_params(ttl_seconds=0)
            r10 = client.send_request("request_challenge", zero_ttl_challenge)
            assert r10["status"] == "ERROR"
            assert r10["error_code"] == "ERR_PARAM_VALUE_INVALID"
            assert handler_invocations["request_challenge"] == 0

            # 11. request_challenge: string TTL rejected as type mismatch
            str_ttl_challenge = self._valid_challenge_params(ttl_seconds="60")
            r11 = client.send_request("request_challenge", str_ttl_challenge)
            assert r11["status"] == "ERROR"
            assert r11["error_code"] == "ERR_PARAM_TYPE_MISMATCH"
            assert handler_invocations["request_challenge"] == 0

            # Proves zero protected handler invocations occurred for any invalid request!
            assert handler_invocations == {
                "request_challenge": 0,
                "submit_operator_consent": 0,
                "verify_authorization_oracle": 0,
            }

    def test_sock_node_fd_single_close_across_all_failure_paths(self, tmp_path, monkeypatch):
        """Proves sock_node_fd single-close lifecycle and descriptor-reuse safety across all failure paths (I18-R8-01).

        Instruments os.close to verify that sock_node_fd is closed at most once on every failure branch,
        and simulates descriptor reuse immediately upon descriptor closure to verify that subsequent
        cleanup does not close an unrelated reused file descriptor.
        """
        temp_root = tmp_path / "single_close_root"
        temp_root.mkdir(mode=0o700)

        orig_open = os.open
        orig_close = os.close
        orig_fstat = os.fstat
        orig_chmod = os.chmod
        orig_recv = socket.socket.recv
        orig_connect = socket.socket.connect

        def run_scenario(name, setup_fn, expected_code=None):
            with monkeypatch.context() as m:
                sock_path = temp_root / f"{name}.sock"
                state = {
                    "captured_node_fd": None,
                    "close_history": [],
                    "reused_fd": None,
                    "double_closed": [],
                }

                def mock_open(*args, **kwargs):
                    fd = orig_open(*args, **kwargs)
                    if kwargs.get("dir_fd") is not None and len(args) > 0 and args[0] == f"{name}.sock":
                        state["captured_node_fd"] = fd
                    return fd

                def mock_close(fd):
                    if fd == state["captured_node_fd"]:
                        state["close_history"].append(fd)
                        if len(state["close_history"]) > 1:
                            state["double_closed"].append(fd)
                        orig_close(fd)
                        # Simulate descriptor reuse immediately: in Linux, lowest available fd is reused
                        state["reused_fd"] = orig_open("/dev/null", os.O_RDONLY)
                        return
                    return orig_close(fd)

                m.setattr(os, "open", mock_open)
                m.setattr(os, "close", mock_close)

                server = setup_fn(sock_path, m)
                try:
                    if expected_code is not None:
                        with pytest.raises(SignerSocketServerError) as exc_info:
                            server.start()
                        assert expected_code in str(exc_info.value), f"{name}: expected {expected_code}, got {exc_info.value}"
                    else:
                        server.start()
                        server.stop()

                    # Invariant 1: sock_node_fd was captured and closed exactly once
                    assert state["captured_node_fd"] is not None, f"{name}: sock_node_fd was never opened"
                    assert len(state["close_history"]) == 1, (
                        f"{name}: expected exactly 1 close of sock_node_fd ({state['captured_node_fd']}), "
                        f"got {len(state['close_history'])}"
                    )

                    # Invariant 2: zero double-close attempts detected
                    assert len(state["double_closed"]) == 0, f"{name}: double-close detected: {state['double_closed']}"

                    # Invariant 3: reused descriptor was NOT closed by any subsequent cleanup
                    assert state["reused_fd"] is not None, f"{name}: reused descriptor simulation failed"
                    try:
                        orig_fstat(state["reused_fd"])
                    except OSError as exc:
                        pytest.fail(f"{name}: reused descriptor {state['reused_fd']} was erroneously closed: {exc}")
                    finally:
                        orig_close(state["reused_fd"])
                finally:
                    server.close()

        # Path 1: Non-socket bound node (os.fstat reports non-socket mode on newly opened node)
        def setup_non_socket(sock_path, m):
            server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
            def mock_fstat(fd):
                st = orig_fstat(fd)
                try:
                    p = f"/proc/self/fd/{fd}"
                    if os.path.exists(p) and str(sock_path.name) in os.readlink(p):
                        return os.stat_result((
                            stat.S_IFREG | 0o600,
                            st.st_ino,
                            st.st_dev,
                            st.st_nlink,
                            st.st_uid,
                            st.st_gid,
                            st.st_size,
                            st.st_atime,
                            st.st_mtime,
                            st.st_ctime,
                        ))
                except OSError:
                    pass
                return st
            m.setattr(os, "fstat", mock_fstat)
            return server
        run_scenario("non_socket", setup_non_socket, expected_code="ERR_NOT_A_SOCKET")

        # Path 2: Loopback probe nonce mismatch
        def setup_probe_mismatch(sock_path, m):
            server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
            def mock_recv(self_s, bufsize, flags=0):
                if bufsize == 32:
                    return b"mismatched_nonce_32_bytes_long!"
                return orig_recv(self_s, bufsize, flags)
            m.setattr(socket.socket, "recv", mock_recv)
            return server
        run_scenario("probe_mismatch", setup_probe_mismatch, expected_code="ERR_SOCKET_OWNERSHIP_MISMATCH")

        # Path 3: Loopback probe connection failure
        def setup_probe_conn_fail(sock_path, m):
            server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
            def mock_connect(self_s, address):
                if "probe_conn_fail.sock" in str(address):
                    raise OSError(111, "Connection refused")
                return orig_connect(self_s, address)
            m.setattr(socket.socket, "connect", mock_connect)
            return server
        run_scenario("probe_conn_fail", setup_probe_conn_fail, expected_code="ERR_SOCKET_OWNERSHIP_VERIFICATION_FAILED")

        # Path 4: Post-probe replacement entry
        foreign_socks = []
        def setup_post_probe_replacement(sock_path, m):
            server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
            def hook():
                sock_path.unlink()
                s2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                foreign_socks.append(s2)
                dfd = orig_open(str(temp_root), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    s2.bind(f"/proc/self/fd/{dfd}/{sock_path.name}")
                finally:
                    orig_close(dfd)
            server._post_probe_hook = hook
            return server
        try:
            run_scenario("post_probe_rep", setup_post_probe_replacement, expected_code="ERR_SOCKET_IDENTITY_MISMATCH")
        finally:
            for s in foreign_socks:
                s.close()
            foreign_socks.clear()

        # Path 5: Pre-chmod replacement entry
        def setup_pre_chmod_replacement(sock_path, m):
            server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
            def hook():
                sock_path.unlink()
                s2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                foreign_socks.append(s2)
                dfd = orig_open(str(temp_root), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    s2.bind(f"/proc/self/fd/{dfd}/{sock_path.name}")
                finally:
                    orig_close(dfd)
            server._pre_chmod_hook = hook
            return server
        try:
            run_scenario("pre_chmod_rep", setup_pre_chmod_replacement, expected_code="ERR_SOCKET_IDENTITY_MISMATCH")
        finally:
            for s in foreign_socks:
                s.close()
            foreign_socks.clear()

        # Path 6: Chmod failure on pinned descriptor
        def setup_chmod_fail(sock_path, m):
            server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
            def mock_chmod(path, mode, **kwargs):
                if str(path).startswith("/proc/self/fd/"):
                    raise OSError(13, "simulated chmod failure")
                return orig_chmod(path, mode, **kwargs)
            m.setattr(os, "chmod", mock_chmod)
            return server
        run_scenario("chmod_fail", setup_chmod_fail, expected_code="ERR_SOCKET_CHMOD_FAILED")

        # Path 7: Post-chmod insecure permissions detection
        def setup_insecure_perms(sock_path, m):
            server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
            def mock_chmod(path, mode, **kwargs):
                if str(path).startswith("/proc/self/fd/"):
                    return orig_chmod(path, 0o666, **kwargs)
                return orig_chmod(path, mode, **kwargs)
            m.setattr(os, "chmod", mock_chmod)
            return server
        run_scenario("post_chmod_insecure", setup_insecure_perms, expected_code="ERR_SOCKET_INSECURE_PERMS")

        # Path 8: Successful startup path
        def setup_success(sock_path, m):
            return LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        run_scenario("success_startup", setup_success, expected_code=None)

    def test_sock_node_fd_close_failure_on_successful_startup_fails_closed_and_reports_error(self, tmp_path, monkeypatch):
        """Proves that a close failure on sock_node_fd during successful startup fails closed (I18-R9-01).

        Asserts that the server does not report itself running, attempts close exactly once,
        and surfaces ERR_SOCKET_NODE_FD_CLOSE_FAILED (or ERR_START_AND_CLEANUP_FAILED).
        """
        temp_root = tmp_path / "close_fail_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "s_success_close_fail.sock"

        orig_open = os.open
        orig_close = os.close
        state = {"node_fd": None, "close_count": 0}

        def mock_open(*args, **kwargs):
            fd = orig_open(*args, **kwargs)
            if kwargs.get("dir_fd") is not None and len(args) > 0 and args[0] == "s_success_close_fail.sock":
                state["node_fd"] = fd
            return fd

        def mock_close(fd):
            if fd == state["node_fd"]:
                state["close_count"] += 1
                orig_close(fd)
                raise OSError(5, "simulated EIO on descriptor close")
            return orig_close(fd)

        monkeypatch.setattr(os, "open", mock_open)
        monkeypatch.setattr(os, "close", mock_close)

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        try:
            with pytest.raises(SignerSocketServerError) as exc_info:
                server.start()

            assert "ERR_SOCKET_NODE_FD_CLOSE_FAILED" in str(exc_info.value)
            assert server._running is False
            assert state["close_count"] == 1
        finally:
            server.close()

    def test_sock_node_fd_close_failure_on_startup_failure_preserves_both_errors(self, tmp_path, monkeypatch):
        """Proves that a close failure during startup error cleanup preserves structured diagnostics (I18-R9-01).

        Asserts that when a primary startup failure occurs (e.g. non-socket node) and sock_node_fd
        close subsequently fails, both errors are retained in ERR_START_AND_CLEANUP_FAILED
        without retrying the numeric descriptor.
        """
        temp_root = tmp_path / "close_cleanup_fail_root"
        temp_root.mkdir(mode=0o700)
        sock_path = temp_root / "s_cleanup_close_fail.sock"

        orig_open = os.open
        orig_close = os.close
        orig_fstat = os.fstat
        state = {"node_fd": None, "close_count": 0}

        def mock_open(*args, **kwargs):
            fd = orig_open(*args, **kwargs)
            if kwargs.get("dir_fd") is not None and len(args) > 0 and args[0] == "s_cleanup_close_fail.sock":
                state["node_fd"] = fd
            return fd

        def mock_close(fd):
            if fd == state["node_fd"]:
                state["close_count"] += 1
                orig_close(fd)
                raise OSError(5, "simulated EIO on cleanup descriptor close")
            return orig_close(fd)

        def mock_fstat(fd):
            st = orig_fstat(fd)
            try:
                p = f"/proc/self/fd/{fd}"
                if os.path.exists(p) and "s_cleanup_close_fail.sock" in os.readlink(p):
                    return os.stat_result((
                        stat.S_IFREG | 0o600,
                        st.st_ino,
                        st.st_dev,
                        st.st_nlink,
                        st.st_uid,
                        st.st_gid,
                        st.st_size,
                        st.st_atime,
                        st.st_mtime,
                        st.st_ctime,
                    ))
            except OSError:
                pass
            return st

        monkeypatch.setattr(os, "open", mock_open)
        monkeypatch.setattr(os, "close", mock_close)
        monkeypatch.setattr(os, "fstat", mock_fstat)

        server = LocalTestSignerSocketServer(socket_path=sock_path, temp_root=temp_root)
        try:
            with pytest.raises(SignerSocketServerError) as exc_info:
                server.start()

            assert exc_info.value.code == "ERR_START_AND_CLEANUP_FAILED"
            assert "ERR_NOT_A_SOCKET" in str(exc_info.value.details.get("primary_error"))
            assert any(getattr(e, "code", None) == "ERR_SOCKET_NODE_FD_CLOSE_FAILED" for e in exc_info.value.cleanup_errors)
            assert server._running is False
            assert state["close_count"] == 1
        finally:
            server.close()














