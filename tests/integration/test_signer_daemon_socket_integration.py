"""Integration tests for Trial Authorization Signer Daemon socket server.

Verifies:
- Length-prefixed AF_UNIX protocol framing (1..65536 bytes)
- Framing edge cases: oversized frame, empty frame, truncated frame
- Public endpoint: SignerPublicPingRequest -> SignerPublicPingResponse
- Oracle endpoint: Linux SO_PEERCRED authentication enforcement
- Oracle verification lifecycle: full verified flow with Ed25519 response signature
- Replay rejection: terminal execution ID tracking in DurableSignerLedger
- Tampering rejection: HMAC invalid, digest mismatch, coordinate mismatch, expiry
- Safety tripwires: zero production path access, zero Resolve import
"""

import ast
import datetime
import hashlib
import json
import os
import socket
import struct
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple, Union

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.core.contracts.oracle_ipc_contracts import (
    DEFAULT_SOCKET_TIMEOUT,
    ERR_CONCURRENCY_LIMIT_EXCEEDED,
    ERR_EMPTY_FRAME,
    ERR_EXECUTION_ALREADY_TERMINAL,
    ERR_EXECUTION_NOT_FOUND,
    ERR_FRAME_TOO_LARGE,
    ERR_IDENTITY_BINDING_MISMATCH,
    ERR_INVALID_FRAME,
    ERR_PEER_UNAUTHORIZED,
    ERR_TERMINAL_STATE_CONFLICT,
    ERR_TERMINAL_STATE_INVALID,
    ERR_UNEXPECTED_FIELD,
    OracleProtocolError,
    OracleTerminalNotificationRequest,
    OracleTerminalNotificationResponse,
    OracleVerificationRequest,
    OracleVerificationResponse,
    SignerPublicChallengeRequest,
    SignerPublicChallengeResponse,
    SignerPublicConsentRequest,
    SignerPublicConsentResponse,
    SignerPublicPingRequest,
    SignerPublicPingResponse,
    VALID_TERMINAL_STATES,
    decode_framed_message,
    decode_oracle_request,
    encode_framed_message,
)
from src.services.durable_nonce_ledger import (
    AuthorizationIdentityRecord,
    DurableSignerLedger,
    OracleExecutionRecord,
    OracleExecutionTerminalRecord,
)
from src.core.contracts.edit_plan_proposal_contracts import edit_plan_proposal_to_dict
from src.core.contracts.signed_approval_envelope import SignedApprovalEnvelope
from src.core.contracts.signed_operator_consent_envelope import (
    CONFIRMATION_PHRASE_LITERAL,
    FRESHNESS_ATTESTATION_LITERAL,
    SignedOperatorConsentEnvelope,
)
from src.core.contracts.trial_authorization_contracts import (
    TrialExecutionAuthorization,
    canonical_action_payload_bytes,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
)
from src.core.timeline_plan_execution_binder import (
    compute_canonical_plan_digest,
)
from src.services.director_approval_signer import DirectorApprovalSigner
from src.services.signer_keystore_service import (
    HMAC_KEY_FILENAME,
    ORACLE_KEY_FILENAME,
    SignerKeystoreService,
)
from src.services.trial_authorization_signer_daemon import (
    SignerDaemonTeardownError,
    TrialAuthorizationSignerDaemon,
)
from src.services.trial_authorization_signer_service import (
    check_and_advance_revocation_epoch_anchor_for_local_test,
    compute_canonical_action_payload_digest_for_admission,
    verify_signed_revocation_manifest,
)
from tests.integration.test_gate3_signer_dispatcher_integration import (
    make_signed_revocation_manifest_bytes,
    make_test_execution_plan,
    make_test_proposal_and_approval,
)


def _setup_daemon_fixtures(
    tmp_path: Path,
    oracle_allowed_uids: Tuple[int, ...] = (os.geteuid(),),
    revocation_trust_dir: Optional[Path] = None,
    revocation_registry_dir: Optional[Path] = None,
    sec_officer_public_key: Optional[Ed25519PublicKey] = None,
) -> Tuple[TrialAuthorizationSignerDaemon, SignerKeystoreService, DurableSignerLedger, str, str]:
    ks_dir = tmp_path / "keystore"
    ks_dir.mkdir(mode=0o700)
    os.chmod(ks_dir, 0o700)

    hmac_key_bytes = b"\x42" * 32
    hmac_file = ks_dir / HMAC_KEY_FILENAME
    hmac_file.write_bytes(hmac_key_bytes)
    os.chmod(hmac_file, 0o600)

    oracle_priv = Ed25519PrivateKey.generate()
    oracle_file = ks_dir / ORACLE_KEY_FILENAME
    oracle_file.write_bytes(oracle_priv.private_bytes_raw())
    os.chmod(oracle_file, 0o600)

    keystore = SignerKeystoreService(ks_dir, enforce_ownership=False)

    ledger_dir = tmp_path / "nonces"
    ledger_dir.mkdir(mode=0o700)
    os.chmod(ledger_dir, 0o700)
    ledger = DurableSignerLedger(ledger_dir, enforce_ownership=False)

    oracle_dir = tmp_path / "oracle"
    oracle_dir.mkdir(mode=0o700)

    public_dir = tmp_path / "public"
    public_dir.mkdir(mode=0o700)

    if revocation_trust_dir is None:
        rev_trust = tmp_path / "revocation_trust"
        rev_trust.mkdir(mode=0o700, exist_ok=True)
        os.chmod(rev_trust, 0o700)
    else:
        rev_trust = revocation_trust_dir

    if revocation_registry_dir is None:
        rev_reg = tmp_path / "revocation_reg"
        rev_reg.mkdir(mode=0o700, exist_ok=True)
        os.chmod(rev_reg, 0o700)
    else:
        rev_reg = revocation_registry_dir

    if sec_officer_public_key is None:
        sec_priv = Ed25519PrivateKey.generate()
        sec_pub = sec_priv.public_key()
        sec_pub_pem = sec_pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        issuer_file = rev_trust / "revocation_issuer.pub.pem"
        if not issuer_file.exists():
            issuer_file.write_bytes(sec_pub_pem)
            os.chmod(issuer_file, 0o600)

        man_file = rev_reg / "revocations.signed.json"
        if not man_file.exists():
            fixed_now = datetime.datetime.now(datetime.timezone.utc)
            manifest_bytes = make_signed_revocation_manifest_bytes(
                sec_priv,
                epoch=1,
                issued_at_iso=(fixed_now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                valid_until_iso=(fixed_now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            man_file.write_bytes(manifest_bytes)
            os.chmod(man_file, 0o600)
            res = verify_signed_revocation_manifest(
                manifest_bytes,
                issuer_public_key=sec_pub,
                verification_time_utc=fixed_now,
            )
            check_and_advance_revocation_epoch_anchor_for_local_test(res, rev_reg, advance=True)
    else:
        sec_pub = sec_officer_public_key

    daemon = TrialAuthorizationSignerDaemon.create_for_test(
        keystore_service=keystore,
        nonce_ledger=ledger,
        oracle_dir=oracle_dir,
        public_dir=public_dir,
        oracle_allowed_uids=oracle_allowed_uids,
        revocation_trust_dir=rev_trust,
        revocation_registry_dir=rev_reg,
        sec_officer_public_key=sec_pub,
        enforce_dac_ownership=False,
    )
    daemon.start()

    oracle_sock_path = str(oracle_dir / "signer_oracle.sock")
    public_sock_path = str(public_dir / "signer_public.sock")

    return daemon, keystore, ledger, oracle_sock_path, public_sock_path


def _send_and_recv_framed(sock_path: str, payload_bytes: bytes, timeout: float = 5.0) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(sock_path)
        client.sendall(encode_framed_message(payload_bytes))
        return decode_framed_message(client)


def _build_valid_authorization_and_request(
    keystore: SignerKeystoreService,
    *,
    execution_id: str = "exec_canary_01",
    authorization_id: Optional[str] = None,
    action_payload_digest: str = "a" * 64,
    target_project: str = "RF_TRIAL_AEC8F0A7A365D5F5",
    target_timeline: str = "RF_TRIAL_TL_AEC8F0A7A365D5F5",
    proposal_revision: str = "b" * 64,
    valid_until_iso: str = "2026-11-06T12:00:00Z",
    ledger: Optional[DurableSignerLedger] = None,
    director_key_id: str = "dir_normal_01",
    operator_key_id: str = "op_normal_01",
) -> Tuple[TrialExecutionAuthorization, OracleVerificationRequest]:
    if authorization_id is None:
        actual_auth_id = "auth_test_01" if execution_id == "exec_canary_01" else f"auth_{execution_id}"
    else:
        actual_auth_id = authorization_id

    # 1. Construct candidate authorization claims without valid hmac
    auth_claims = {
        "authorization_id": actual_auth_id,
        "key_id": "trial_auth_key_01",
        "operator_gesture_digest_sha256": "1" * 64,
        "approval_record_id": "appr_test_01",
        "approval_record_digest_sha256": "2" * 64,
        "proposal_id": "prop_test_01",
        "proposal_revision": proposal_revision,
        "proposal_content_digest_sha256": "3" * 64,
        "plan_idempotency_key": "plan_key_01",
        "plan_digest_sha256": "4" * 64,
        "action_payload_digest_sha256": action_payload_digest,
        "target_project_uid": target_project,
        "target_project_name": "Canary Project",
        "target_timeline_uid": target_timeline,
        "target_timeline_name": "Canary Timeline",
        "target_timeline_fps_numerator": 24,
        "target_timeline_fps_denominator": 1,
        "observed_timeline_start_frame": 0,
        "authorized_record_frame": 0,
        "source_file_path": "/media/storage/canary.mov",
        "source_file_sha256": "5" * 64,
        "authorized_action_type": "INSERT_CLIP",
        "authorized_track_type": "video",
        "authorized_track_index": 1,
        "authorized_timeline_frame_start": 0,
        "authorized_timeline_frame_duration": 24,
        "authorized_source_frame_start": 0,
        "authorized_source_frame_duration": 24,
        "execution_id": execution_id,
        "issued_at_iso": "2026-11-04T12:00:00Z",
        "valid_until_iso": valid_until_iso,
        "token_nonce": execution_id,
        "authorization_hmac": "0" * 64,
    }
    candidate_dto = TrialExecutionAuthorization(**auth_claims)

    # 2. Sign canonical claims using keystore HMAC key
    claims_to_sign = canonical_bytes_for_auth_hmac(candidate_dto)
    auth_hmac = keystore.sign_authorization_hmac(claims_to_sign)

    auth_claims["authorization_hmac"] = auth_hmac
    final_auth_dto = TrialExecutionAuthorization(**auth_claims)

    # 3. Durably record authorization identity mapping if ledger provided (DEP02B-R3-01)
    if ledger is not None:
        ledger.record_authorization_identity(
            AuthorizationIdentityRecord(
                schema_version=1,
                authorization_id=final_auth_dto.authorization_id,
                director_key_id=director_key_id,
                operator_key_id=operator_key_id,
                action_payload_digest_sha256=final_auth_dto.action_payload_digest_sha256,
                proposal_revision=final_auth_dto.proposal_revision,
                issued_at_iso=final_auth_dto.issued_at_iso,
            )
        )

    request = OracleVerificationRequest(
        schema_version=2,
        request_id=str(uuid.uuid4()),
        request_nonce=hashlib.sha256(b"req_nonce_1").hexdigest(),
        requested_at_iso="2026-11-04T12:01:00Z",
        signed_authorization=final_auth_dto,
        action_payload_digest_sha256=action_payload_digest,
        target_project_uid=target_project,
        target_timeline_uid=target_timeline,
        proposal_revision=proposal_revision,
    )
    return final_auth_dto, request


def _build_valid_terminal_request(
    auth: TrialExecutionAuthorization,
    terminal_state: str = "COMPLETED",
) -> OracleTerminalNotificationRequest:
    return OracleTerminalNotificationRequest(
        schema_version=2,
        request_id=str(uuid.uuid4()),
        request_nonce=hashlib.sha256(f"term_nonce_{terminal_state}".encode("utf-8")).hexdigest(),
        execution_id=auth.execution_id,
        authorization_id=auth.authorization_id,
        terminal_state=terminal_state,
        proposal_revision=auth.proposal_revision,
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        action_payload_digest_sha256=auth.action_payload_digest_sha256,
        requested_at_iso="2026-11-04T12:02:00Z",
    )


class TestPublicPingSocketFlow:

    def test_public_ping_pong(self, tmp_path: Path):
        daemon, ks, ledger, _, public_sock = _setup_daemon_fixtures(tmp_path)
        try:
            ping_nonce = hashlib.sha256(b"ping_test").hexdigest()
            req = SignerPublicPingRequest(
                schema_version=2,
                ping_nonce=ping_nonce,
            )
            raw_resp = _send_and_recv_framed(public_sock, req.canonical_bytes())
            resp = SignerPublicPingResponse.from_json_bytes(raw_resp)

            assert resp.schema_version == 2
            assert resp.ping_nonce == ping_nonce
            assert resp.status == "PONG"
            assert resp.daemon_status == "OPERATIONAL"
        finally:
            daemon.stop()
            ks.close()
            ledger.close()


class TestFramingAndTransportProtocols:

    def test_encode_rejects_oversized_frame(self):
        huge_payload = b"x" * 65537
        with pytest.raises(OracleProtocolError) as exc_info:
            encode_framed_message(huge_payload)
        assert exc_info.value.code == ERR_FRAME_TOO_LARGE

    def test_encode_rejects_empty_frame(self):
        with pytest.raises(OracleProtocolError) as exc_info:
            encode_framed_message(b"")
        assert exc_info.value.code == ERR_EMPTY_FRAME

    def test_decode_truncated_frame(self, tmp_path: Path):
        daemon, ks, ledger, _, public_sock = _setup_daemon_fixtures(tmp_path)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(public_sock)
                # Send 4-byte header stating 100 bytes, but send only 10 bytes then close
                header = struct.pack(">I", 100)
                client.sendall(header + b"1234567890")
                client.shutdown(socket.SHUT_WR)

                # Expect daemon to return error frame or close connection
                try:
                    resp_bytes = decode_framed_message(client)
                    resp_json = json.loads(resp_bytes.decode("utf-8"))
                    assert resp_json.get("error") in ("ERR_INVALID_FRAME", "ERR_FRAME_TRUNCATED")
                except (EOFError, OracleProtocolError):
                    pass
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_public_endpoint_rejects_duplicate_json_keys(self, tmp_path: Path):
        daemon, ks, ledger, _, public_sock = _setup_daemon_fixtures(tmp_path)
        try:
            # Send raw JSON frame with duplicate keys: {"schema_version": 2, "ping_nonce": "a", "ping_nonce": "b"}
            payload = b'{"schema_version": 2, "ping_nonce": "a", "ping_nonce": "b"}'
            resp_bytes = _send_and_recv_framed(public_sock, payload)
            resp = json.loads(resp_bytes.decode("utf-8"))
            assert resp.get("error") in (ERR_UNEXPECTED_FIELD, ERR_INVALID_FRAME)
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_public_endpoint_rejects_unknown_envelope_fields(self, tmp_path: Path):
        daemon, ks, ledger, _, public_sock = _setup_daemon_fixtures(tmp_path)
        try:
            # Send ping request with extra unexpected top-level field
            payload = b'{"schema_version": 2, "ping_nonce": "valid_nonce_123", "unrecognized_field": 42}'
            resp_bytes = _send_and_recv_framed(public_sock, payload)
            resp = json.loads(resp_bytes.decode("utf-8"))
            assert resp.get("error") == ERR_UNEXPECTED_FIELD
        finally:
            daemon.stop()
            ks.close()
            ledger.close()


class TestOracleEndpointPeerAuthentication:

    def test_unauthorized_peer_rejected(self, tmp_path: Path):
        # Configure daemon with UID 99999 (not current user)
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(
            tmp_path, oracle_allowed_uids=(99999,)
        )
        try:
            _, req = _build_valid_authorization_and_request(ks, ledger=ledger)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(oracle_sock)
                client.sendall(encode_framed_message(req.canonical_bytes()))
                raw_resp = decode_framed_message(client)
                data = json.loads(raw_resp.decode("utf-8"))
                assert data["error"] == ERR_PEER_UNAUTHORIZED
        finally:
            daemon.stop()
            ks.close()
            ledger.close()


class TestOracleVerificationLifecycle:

    def test_successful_verification_flow(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            _, req = _build_valid_authorization_and_request(ks, ledger=ledger)
            raw_resp = _send_and_recv_framed(oracle_sock, req.canonical_bytes())
            resp = OracleVerificationResponse.from_json_bytes(raw_resp)

            assert resp.schema_version == 2
            assert resp.request_id == req.request_id
            assert resp.request_nonce == req.request_nonce
            assert resp.verification_result == "VERIFIED_VALID"
            assert resp.is_valid is True
            assert resp.reason_code == "OK"

            # Verify Ed25519 signature over the 14 response claims
            claim_bytes = resp.canonical_claims_bytes()
            assert ks.verify_oracle_signature(claim_bytes, resp.oracle_signature) is True
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_replay_verification_rejected(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            _, req = _build_valid_authorization_and_request(ks, execution_id="exec_replay_test", ledger=ledger)
            # First verification succeeds
            resp1 = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req.canonical_bytes())
            )
            assert resp1.verification_result == "VERIFIED_VALID"

            # Second verification with exact same execution_id fails closed
            resp2 = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req.canonical_bytes())
            )
            assert resp2.verification_result == "VERIFICATION_FAILED"
            assert resp2.is_valid is False
            assert resp2.reason_code == "ERR_EXECUTION_ALREADY_VERIFIED"

            # The failure response itself must still be signed by Oracle Ed25519 key
            assert ks.verify_oracle_signature(resp2.canonical_claims_bytes(), resp2.oracle_signature) is True
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_hmac_tampering_rejected(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            auth, req = _build_valid_authorization_and_request(ks, execution_id="exec_hmac_test", ledger=ledger)
            # Corrupt the HMAC signature
            tampered_hmac = "f" * 64
            tampered_auth = TrialExecutionAuthorization(
                **{**auth.__dict__, "authorization_hmac": tampered_hmac}
            )
            tampered_req = OracleVerificationRequest(
                **{**req.__dict__, "signed_authorization": tampered_auth}
            )

            resp = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, tampered_req.canonical_bytes())
            )
            assert resp.verification_result == "VERIFICATION_FAILED"
            assert resp.is_valid is False
            assert resp.reason_code == "ERR_HMAC_SIGNATURE_INVALID"
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_coordinate_and_digest_mismatch_rejected(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            _, req = _build_valid_authorization_and_request(ks, execution_id="exec_coord_test", ledger=ledger)

            # 1. Action payload digest mismatch
            mismatched_req = OracleVerificationRequest(
                **{**req.__dict__, "action_payload_digest_sha256": "f" * 64}
            )
            resp1 = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, mismatched_req.canonical_bytes())
            )
            assert resp1.reason_code == "ERR_ACTION_PAYLOAD_DIGEST_MISMATCH"

            # 2. Target project UID mismatch
            mismatched_req2 = OracleVerificationRequest(
                **{**req.__dict__, "target_project_uid": "FOREIGN_PROJECT_UID"}
            )
            resp2 = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, mismatched_req2.canonical_bytes())
            )
            assert resp2.reason_code == "ERR_TARGET_COORDINATE_MISMATCH"

            # 3. Proposal revision mismatch
            mismatched_req3 = OracleVerificationRequest(
                **{**req.__dict__, "proposal_revision": "e" * 64}
            )
            resp3 = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, mismatched_req3.canonical_bytes())
            )
            assert resp3.reason_code == "ERR_PROPOSAL_REVISION_MISMATCH"
        finally:
            daemon.stop()
            ks.close()
            ledger.close()


class TestSafetyAndIsolationTripwires:

    def test_zero_production_path_access(self):
        # Assert production root paths are never created or modified by tests
        assert not os.path.exists("/run/resolveflow")
        assert not os.path.exists("/var/lib/resolveflow")

    def test_zero_resolve_import(self):
        # Assert none of the DEP-02B modules import DaVinci Resolve or fusionscript
        modules_to_check = [
            "src/services/trial_authorization_signer_daemon.py",
            "src/services/signer_keystore_service.py",
            "src/services/durable_nonce_ledger.py",
            "src/core/contracts/oracle_ipc_contracts.py",
        ]
        forbidden_substrings = ["davinci", "fusionscript", "bmd", "resolve_api"]

        for mod_path in modules_to_check:
            with open(mod_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=mod_path)
            imported_names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_names.add(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imported_names.add(node.module)

            for name in imported_names:
                name_lower = name.lower()
                for forbidden in forbidden_substrings:
                    assert forbidden not in name_lower, (
                        f"Tripwire violation: found forbidden import '{name}' in {mod_path}!"
                    )


def _execution_plan_to_dict(plan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "idempotency_key": plan.idempotency_key,
        "proposal_id": plan.proposal_id,
        "proposal_revision": plan.proposal_revision,
        "approval_id": plan.approval_id,
        "approved_at_iso": plan.approved_at_iso,
        "target_spec": {
            "project_name": plan.target_spec.project_name,
            "project_id": plan.target_spec.project_id,
            "timeline_name": plan.target_spec.timeline_name,
            "timeline_id": plan.target_spec.timeline_id,
            "timeline_fps": [plan.target_spec.timeline_fps.numerator, plan.target_spec.timeline_fps.denominator],
            "timeline_resolution": list(plan.target_spec.timeline_resolution),
            "start_timecode_frame": plan.target_spec.start_timecode_frame,
        },
        "occupancy_snapshot_id": plan.occupancy_snapshot_id,
        "binding_status": plan.binding_status,
        "planned_actions": [
            {
                "action_index": a.action_index,
                "action_type": a.action_type,
                "clip_id": a.clip_id,
                "role": a.role,
                "source_file_path": a.source_file_path,
                "source_range": [a.source_range.start_frame, a.source_range.end_frame],
                "timeline_range": [a.timeline_range.start_frame, a.timeline_range.end_frame],
                "target_track_type": a.target_track_type,
                "target_track_index": a.target_track_index,
                "applied_speed": f"{a.applied_speed.numerator}/{a.applied_speed.denominator}",
                "action_params": dict(a.action_params),
                "action_idempotency_hash": a.action_idempotency_hash,
            }
            for a in plan.planned_actions
        ],
        "planned_actions_count": plan.planned_actions_count,
        "track_reservations": [
            {
                "track_type": r.track_type,
                "track_index": r.track_index,
                "reserved_start_frame": r.reserved_start_frame,
                "reserved_end_frame": r.reserved_end_frame,
                "intended_usage": r.intended_usage,
            }
            for r in plan.track_reservations
        ],
        "source_digests": [
            {
                "clip_id": d.clip_id,
                "absolute_path": d.absolute_path,
                "full_sha256": d.full_sha256,
                "stream_index": d.stream_index,
                "stat_identity": list(d.stat_identity),
            }
            for d in plan.source_digests
        ],
        "catalog_digests": [],
        "accepted_omissions": [],
        "total_timeline_frames": plan.total_timeline_frames,
        "total_timeline_duration_sec": f"{plan.total_timeline_duration_sec.numerator}/{plan.total_timeline_duration_sec.denominator}",
        "issues": [],
        "created_at_iso": plan.created_at_iso,
        "schema_version": plan.schema_version,
    }


class TestPublicChallengeAndConsentFlow:

    def test_challenge_issuance_and_operator_consent_flow(self, tmp_path: Path):
        daemon, ks, ledger, _, public_sock = _setup_daemon_fixtures(tmp_path)
        try:
            # 1. Setup Director key and signed approval envelope
            dir_priv = Ed25519PrivateKey.generate()
            dir_pub_pem = dir_priv.public_key().public_bytes(
                encoding=Encoding.PEM,
                format=PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")
            dir_signer = DirectorApprovalSigner(dir_priv)

            prop, appr = make_test_proposal_and_approval()
            plan = make_test_execution_plan(prop, appr)
            plan_digest = compute_canonical_plan_digest(plan)
            proposal_digest = appr.proposal_revision

            now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            approval_env = dir_signer.sign(
                approval_record=appr,
                plan_digest_sha256=plan_digest,
                proposal_content_digest_sha256=proposal_digest,
                director_key_id="dir_key_test_01",
                signed_at_iso=now_iso,
            )

            # 2. Send SignerPublicChallengeRequest
            ch_req = SignerPublicChallengeRequest(
                schema_version=2,
                request_id=str(uuid.uuid4()),
                approval_envelope=approval_env.to_dict(),
                proposal=edit_plan_proposal_to_dict(prop),
                execution_plan=_execution_plan_to_dict(plan),
                expected_director_key_id="dir_key_test_01",
                director_public_key_pem=dir_pub_pem,
                operator_id="operator_01",
                operator_key_id="op_key_test_01",
                ttl_seconds=120.0,
            )
            ch_resp_bytes = _send_and_recv_framed(public_sock, ch_req.canonical_bytes())
            ch_resp = SignerPublicChallengeResponse.from_dict(json.loads(ch_resp_bytes.decode("utf-8")))

            assert ch_resp.status == "CHALLENGE_ISSUED"
            assert ch_resp.challenge_id
            assert ch_resp.client_nonce

            # 3. Setup Operator key and SignedOperatorConsentEnvelope
            chal_issued_dt = datetime.datetime.fromisoformat(ch_resp.issued_at_iso.replace("Z", "+00:00"))
            consent_time_iso = (chal_issued_dt + datetime.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

            op_priv = Ed25519PrivateKey.generate()
            op_pub_pem = op_priv.public_key().public_bytes(
                encoding=Encoding.PEM,
                format=PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")

            action = plan.planned_actions[0]
            action_digest = compute_canonical_action_payload_digest_for_admission(
                action=action,
                execution_plan=plan,
                authorization_id=ch_resp.preallocated_authorization_id,
                execution_id=ch_resp.preallocated_execution_id,
            )

            consent_payload_dict = {
                "action_payload_digest_sha256": action_digest,
                "action_type": action.action_type,
                "approval_envelope_digest_sha256": approval_env.compute_envelope_digest(),
                "challenge_id": ch_resp.challenge_id,
                "client_nonce": ch_resp.client_nonce,
                "confirmation_phrase": CONFIRMATION_PHRASE_LITERAL,
                "consent_timestamp_iso": consent_time_iso,
                "duration_frames": action.timeline_range.duration_frames,
                "freshness_attestation": FRESHNESS_ATTESTATION_LITERAL,
                "operator_id": "operator_01",
                "operator_key_id": "op_key_test_01",
                "plan_digest_sha256": plan_digest,
                "record_frame": action.timeline_range.start_frame,
                "schema_version": 1,
                "source_file_path": action.source_file_path,
                "source_file_sha256": plan.source_digests[0].full_sha256,
                "target_project_name": plan.target_spec.project_name,
                "target_project_uid": plan.target_spec.project_id,
                "target_timeline_name": plan.target_spec.timeline_name,
                "target_timeline_uid": plan.target_spec.timeline_id,
                "track_index": action.target_track_index,
                "track_type": action.target_track_type,
            }
            consent_signing_bytes = canonical_json_bytes(consent_payload_dict)
            op_sig_hex = op_priv.sign(consent_signing_bytes).hex().lower()

            full_consent_dict = dict(consent_payload_dict)
            full_consent_dict["operator_signature"] = op_sig_hex
            consent_env = SignedOperatorConsentEnvelope.from_dict(full_consent_dict)

            # 4. Submit operator consent
            consent_req = SignerPublicConsentRequest(
                schema_version=2,
                request_id=str(uuid.uuid4()),
                challenge_id=ch_resp.challenge_id,
                consent_envelope=consent_env.to_dict(),
                operator_public_key_pem=op_pub_pem,
                ttl_seconds=120.0,
            )
            cons_resp_bytes = _send_and_recv_framed(public_sock, consent_req.canonical_bytes())
            cons_resp = SignerPublicConsentResponse.from_dict(json.loads(cons_resp_bytes.decode("utf-8")))

            assert cons_resp.status == "AUTHORIZATION_ISSUED"
            auth_dict = cons_resp.signed_authorization
            assert auth_dict["authorization_id"] == ch_resp.preallocated_authorization_id
            assert auth_dict["execution_id"] == ch_resp.preallocated_execution_id

            # Verify the authorization signature using keystore
            auth_obj = TrialExecutionAuthorization(**auth_dict)
            auth_claims_bytes = canonical_bytes_for_auth_hmac(auth_obj)
            assert ks.verify_authorization_hmac(auth_claims_bytes, auth_obj.authorization_hmac) is True

            # 5. Duplicate submission must fail with nonce consumed
            dup_resp_bytes = _send_and_recv_framed(public_sock, consent_req.canonical_bytes())
            dup_resp = json.loads(dup_resp_bytes.decode("utf-8"))
            assert dup_resp.get("error") in ("ERR_CHALLENGE_ALREADY_CONSUMED", "ERR_NONCE_ALREADY_CONSUMED")
        finally:
            daemon.stop()
            ks.close()
            ledger.close()


class TestOracleRevocationEnforcement:

    def test_oracle_rejects_revoked_authorization_id(self, tmp_path: Path):
        # 1. Setup revocation registry and trusted issuer
        reg_dir = tmp_path / "revocation_reg"
        reg_dir.mkdir(mode=0o700)
        os.chmod(reg_dir, 0o700)

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        sec_priv = Ed25519PrivateKey.generate()
        manifest_bytes = make_signed_revocation_manifest_bytes(
            issuer_priv=sec_priv,
            epoch=1,
            revoked_authorization_ids=("auth_test_01",),
            issued_at_iso=(now_utc - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            valid_until_iso=(now_utc + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        manifest_file = reg_dir / "revocations.signed.json"
        manifest_file.write_bytes(manifest_bytes)
        os.chmod(manifest_file, 0o600)

        res = verify_signed_revocation_manifest(
            manifest_bytes,
            issuer_public_key=sec_priv.public_key(),
            verification_time_utc=now_utc,
        )
        check_and_advance_revocation_epoch_anchor_for_local_test(res, reg_dir, advance=True)

        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(
            tmp_path,
            revocation_registry_dir=reg_dir,
            sec_officer_public_key=sec_priv.public_key(),
        )
        try:
            auth, req = _build_valid_authorization_and_request(
                ks,
                target_project="RF_TRIAL_AEC8F0A7A365D5F5",
                target_timeline="RF_TRIAL_TL_AEC8F0A7A365D5F5",
                ledger=ledger,
            )
            # auth has authorization_id="auth_test_01" which is revoked in manifest
            resp = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req.canonical_bytes())
            )
            assert resp.is_valid is False
            assert resp.verification_result == "VERIFICATION_FAILED"
            assert resp.reason_code == "ERR_AUTHORIZATION_REVOKED"
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_oracle_rejects_revoked_trial_key_id(self, tmp_path: Path):
        reg_dir = tmp_path / "revocation_reg"
        reg_dir.mkdir(mode=0o700)
        os.chmod(reg_dir, 0o700)

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        sec_priv = Ed25519PrivateKey.generate()
        manifest_bytes = make_signed_revocation_manifest_bytes(
            issuer_priv=sec_priv,
            epoch=1,
            revoked_trial_key_ids=("trial_auth_key_01",),
            issued_at_iso=(now_utc - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            valid_until_iso=(now_utc + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        manifest_file = reg_dir / "revocations.signed.json"
        manifest_file.write_bytes(manifest_bytes)
        os.chmod(manifest_file, 0o600)

        res = verify_signed_revocation_manifest(
            manifest_bytes,
            issuer_public_key=sec_priv.public_key(),
            verification_time_utc=now_utc,
        )
        check_and_advance_revocation_epoch_anchor_for_local_test(res, reg_dir, advance=True)

        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(
            tmp_path,
            revocation_registry_dir=reg_dir,
            sec_officer_public_key=sec_priv.public_key(),
        )
        try:
            auth, req = _build_valid_authorization_and_request(
                ks,
                target_project="RF_TRIAL_AEC8F0A7A365D5F5",
                target_timeline="RF_TRIAL_TL_AEC8F0A7A365D5F5",
                ledger=ledger,
            )
            # auth has key_id="trial_auth_key_01" which is revoked
            resp = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req.canonical_bytes())
            )
            assert resp.is_valid is False
            assert resp.verification_result == "VERIFICATION_FAILED"
            assert resp.reason_code == "ERR_TRIAL_KEY_REVOKED"
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_oracle_rejects_revoked_director_key_id(self, tmp_path: Path):
        reg_dir = tmp_path / "revocation_reg"
        reg_dir.mkdir(mode=0o700)
        os.chmod(reg_dir, 0o700)

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        sec_priv = Ed25519PrivateKey.generate()
        manifest_bytes = make_signed_revocation_manifest_bytes(
            issuer_priv=sec_priv,
            epoch=1,
            revoked_director_key_ids=("dir_test_revoked_01",),
            issued_at_iso=(now_utc - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            valid_until_iso=(now_utc + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        manifest_file = reg_dir / "revocations.signed.json"
        manifest_file.write_bytes(manifest_bytes)
        os.chmod(manifest_file, 0o600)

        res = verify_signed_revocation_manifest(
            manifest_bytes,
            issuer_public_key=sec_priv.public_key(),
            verification_time_utc=now_utc,
        )
        check_and_advance_revocation_epoch_anchor_for_local_test(res, reg_dir, advance=True)

        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(
            tmp_path,
            revocation_registry_dir=reg_dir,
            sec_officer_public_key=sec_priv.public_key(),
        )
        try:
            auth, req = _build_valid_authorization_and_request(
                ks,
                target_project="RF_TRIAL_AEC8F0A7A365D5F5",
                target_timeline="RF_TRIAL_TL_AEC8F0A7A365D5F5",
                ledger=ledger,
                director_key_id="dir_test_revoked_01",
                operator_key_id="op_normal_01",
            )
            resp = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req.canonical_bytes())
            )
            assert resp.is_valid is False
            assert resp.verification_result == "VERIFICATION_FAILED"
            assert resp.reason_code == "ERR_DIRECTOR_KEY_REVOKED"
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_oracle_rejects_revoked_operator_key_id(self, tmp_path: Path):
        reg_dir = tmp_path / "revocation_reg"
        reg_dir.mkdir(mode=0o700)
        os.chmod(reg_dir, 0o700)

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        sec_priv = Ed25519PrivateKey.generate()
        manifest_bytes = make_signed_revocation_manifest_bytes(
            issuer_priv=sec_priv,
            epoch=1,
            revoked_operator_key_ids=("op_test_revoked_01",),
            issued_at_iso=(now_utc - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            valid_until_iso=(now_utc + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        manifest_file = reg_dir / "revocations.signed.json"
        manifest_file.write_bytes(manifest_bytes)
        os.chmod(manifest_file, 0o600)

        res = verify_signed_revocation_manifest(
            manifest_bytes,
            issuer_public_key=sec_priv.public_key(),
            verification_time_utc=now_utc,
        )
        check_and_advance_revocation_epoch_anchor_for_local_test(res, reg_dir, advance=True)

        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(
            tmp_path,
            revocation_registry_dir=reg_dir,
            sec_officer_public_key=sec_priv.public_key(),
        )
        try:
            auth, req = _build_valid_authorization_and_request(
                ks,
                target_project="RF_TRIAL_AEC8F0A7A365D5F5",
                target_timeline="RF_TRIAL_TL_AEC8F0A7A365D5F5",
                ledger=ledger,
                director_key_id="dir_normal_01",
                operator_key_id="op_test_revoked_01",
            )
            resp = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req.canonical_bytes())
            )
            assert resp.is_valid is False
            assert resp.verification_result == "VERIFICATION_FAILED"
            assert resp.reason_code == "ERR_OPERATOR_KEY_REVOKED"
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_oracle_rejects_missing_epoch_anchor(self, tmp_path: Path):
        reg_dir = tmp_path / "revocation_reg"
        reg_dir.mkdir(mode=0o700)
        os.chmod(reg_dir, 0o700)

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        sec_priv = Ed25519PrivateKey.generate()
        manifest_bytes = make_signed_revocation_manifest_bytes(
            issuer_priv=sec_priv,
            epoch=1,
            issued_at_iso=(now_utc - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            valid_until_iso=(now_utc + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        manifest_file = reg_dir / "revocations.signed.json"
        manifest_file.write_bytes(manifest_bytes)
        os.chmod(manifest_file, 0o600)

        # Deliberately DO NOT advance epoch anchor: anchor file does not exist!
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(
            tmp_path,
            revocation_registry_dir=reg_dir,
            sec_officer_public_key=sec_priv.public_key(),
        )
        try:
            auth, req = _build_valid_authorization_and_request(
                ks,
                target_project="RF_TRIAL_AEC8F0A7A365D5F5",
                target_timeline="RF_TRIAL_TL_AEC8F0A7A365D5F5",
                ledger=ledger,
            )
            resp = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req.canonical_bytes())
            )
            assert resp.is_valid is False
            assert resp.verification_result == "VERIFICATION_FAILED"
            assert resp.reason_code in ("ERR_REVOCATION_ANCHOR_ABSENT", "ERR_REVOCATION_VERIFICATION_FAILED")
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_oracle_rejects_unregistered_authorization_identity(self, tmp_path: Path):
        """DEP02B-R3-01: Verifies Oracle rejects authorization when durable identity record is absent."""
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            # Construct valid HMAC authorization WITHOUT registering in ledger
            auth, req = _build_valid_authorization_and_request(ks, ledger=None)
            resp = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req.canonical_bytes())
            )
            assert resp.is_valid is False
            assert resp.verification_result == "VERIFICATION_FAILED"
            assert resp.reason_code == "ERR_REVOCATION_VERIFICATION_FAILED"
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_oracle_restart_recovers_durable_identity_and_checks_revocation(self, tmp_path: Path):
        """DEP02B-R3-01: Verifies Oracle correctly restores identity binding across daemon restart."""
        reg_dir = tmp_path / "revocation_reg"
        reg_dir.mkdir(mode=0o700)
        os.chmod(reg_dir, 0o700)

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        sec_priv = Ed25519PrivateKey.generate()
        manifest_bytes = make_signed_revocation_manifest_bytes(
            issuer_priv=sec_priv,
            epoch=1,
            revoked_director_key_ids=("dir_restarted_revoked_01",),
            issued_at_iso=(now_utc - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            valid_until_iso=(now_utc + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        manifest_file = reg_dir / "revocations.signed.json"
        manifest_file.write_bytes(manifest_bytes)
        os.chmod(manifest_file, 0o600)

        res = verify_signed_revocation_manifest(
            manifest_bytes,
            issuer_public_key=sec_priv.public_key(),
            verification_time_utc=now_utc,
        )
        check_and_advance_revocation_epoch_anchor_for_local_test(res, reg_dir, advance=True)

        daemon1, ks, ledger1, oracle_sock, _ = _setup_daemon_fixtures(
            tmp_path,
            revocation_registry_dir=reg_dir,
            sec_officer_public_key=sec_priv.public_key(),
        )

        auth, req = _build_valid_authorization_and_request(
            ks,
            execution_id="exec_restart_01",
            target_project="RF_TRIAL_AEC8F0A7A365D5F5",
            target_timeline="RF_TRIAL_TL_AEC8F0A7A365D5F5",
            ledger=ledger1,
            director_key_id="dir_restarted_revoked_01",
            operator_key_id="op_normal_01",
        )

        # 2. Stop Daemon 1 and close ledger 1
        ledger_dir = Path(ledger1.ledger_dir)
        oracle_dir = Path(daemon1._oracle_dir)
        public_dir = Path(daemon1._public_dir)
        daemon1.stop()
        ledger1.close()

        # 3. Start a brand new Daemon 2 on the SAME directories (simulates crash/restart)
        ledger2 = DurableSignerLedger(
            ledger_dir=ledger_dir,
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            enforce_ownership=False,
        )
        daemon2 = TrialAuthorizationSignerDaemon.create_for_test(
            keystore_service=ks,
            nonce_ledger=ledger2,
            oracle_dir=oracle_dir,
            public_dir=public_dir,
            oracle_allowed_uids=(os.geteuid(),),
            revocation_registry_dir=reg_dir,
            sec_officer_public_key=sec_priv.public_key(),
            enforce_dac_ownership=False,
        )
        daemon2.start()

        try:
            # Query Oracle on daemon2: identity MUST be recovered from durable ledger!
            oracle_sock = str(oracle_dir / "signer_oracle.sock")
            resp = OracleVerificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req.canonical_bytes())
            )
            # Must detect revoked director key restored from disk
            assert resp.is_valid is False
            assert resp.verification_result == "VERIFICATION_FAILED"
            assert resp.reason_code == "ERR_DIRECTOR_KEY_REVOKED"
        finally:
            daemon2.stop()
            ks.close()
            ledger2.close()


class TestSignerDaemonConcurrencyLimit:

    def test_public_concurrency_limit_exceeded(self, tmp_path: Path):
        daemon, ks, ledger, _, public_sock = _setup_daemon_fixtures(tmp_path)
        active_sockets = []
        try:
            # Connect MAX_CONCURRENT_CLIENTS (16) sockets without sending complete frames
            for _ in range(16):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(3.0)
                s.connect(public_sock)
                active_sockets.append(s)

            # The 17th client connects and attempts to request: should receive ERR_CONCURRENCY_LIMIT_EXCEEDED
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client17:
                client17.settimeout(2.0)
                client17.connect(public_sock)
                resp_bytes = decode_framed_message(client17)
                resp = json.loads(resp_bytes.decode("utf-8"))
                assert resp.get("error") == ERR_CONCURRENCY_LIMIT_EXCEEDED
        finally:
            for s in active_sockets:
                try:
                    s.close()
                except OSError:
                    pass
            daemon.stop()
            ks.close()
            ledger.close()

    def test_daemon_stop_drains_workers(self, tmp_path: Path):
        daemon, ks, ledger, _, public_sock = _setup_daemon_fixtures(tmp_path)
        try:
            ping_req = SignerPublicPingRequest(ping_nonce="ping_drain_test")
            resp_bytes = _send_and_recv_framed(public_sock, ping_req.canonical_bytes())
            resp = SignerPublicPingResponse.from_dict(json.loads(resp_bytes.decode("utf-8")))
            assert resp.status == "PONG"
        finally:
            daemon.stop()
            assert daemon.is_running is False
            ks.close()
            ledger.close()

    def test_daemon_stop_drain_timeout_retains_descriptors(self, tmp_path: Path):
        """DEP02B-R3-02: Verifies stop() raises structured error and retains descriptors if workers fail to drain."""
        daemon, ks, ledger, _, public_sock = _setup_daemon_fixtures(tmp_path)
        blocker_socket = None
        try:
            # Connect a client and hold the connection open inside worker frame read
            blocker_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            blocker_socket.connect(public_sock)
            time.sleep(0.05)

            # Stopping while worker is blocked on frame read must trigger timeout and raise SignerDaemonTeardownError
            with pytest.raises(SignerDaemonTeardownError) as exc_info:
                daemon.stop()
            assert "ERR_DAEMON_DRAIN_TIMEOUT" in str(exc_info.value)

            # Invariants: descriptors must be retained, not torn down!
            assert daemon._public_dir_fd is not None
            assert daemon._oracle_dir_fd is not None
        finally:
            if blocker_socket is not None:
                try:
                    blocker_socket.close()
                except OSError:
                    pass
            # Now worker unblocks and exits; second stop() succeeds cleanly
            time.sleep(0.1)
            daemon.stop()
            assert daemon._public_dir_fd is None
            assert daemon._oracle_dir_fd is None
            ks.close()
            ledger.close()

    def test_oracle_commit_race_with_stop_rejects_terminal_recording(self, tmp_path: Path):
        """DEP02B-R4-01: Deterministic race test proving Oracle cannot commit execution after stop() begins."""
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            auth, req = _build_valid_authorization_and_request(
                ks,
                execution_id="exec_race_oracle_01",
                ledger=ledger,
            )

            precheck_done = threading.Event()
            proceed_to_commit = threading.Event()
            orig_check = daemon._check_key_revocation

            def hooked_check(*args, **kwargs):
                orig_check(*args, **kwargs)
                precheck_done.set()
                proceed_to_commit.wait(timeout=2.0)

            daemon._check_key_revocation = hooked_check

            # Send request in a separate thread so main thread can invoke stop()
            client_error = []
            def send_req():
                try:
                    raw_resp = _send_and_recv_framed(oracle_sock, req.canonical_bytes())
                    resp = OracleVerificationResponse.from_json_bytes(raw_resp)
                    if not resp.is_valid:
                        client_error.append(resp.reason_code)
                except Exception as e:
                    client_error.append(str(e))

            client_thread = threading.Thread(target=send_req)
            client_thread.start()

            # Wait until worker completes precheck
            assert precheck_done.wait(timeout=3.0)

            # Spawn stop() on a thread to begin shutdown transition
            stop_thread = threading.Thread(target=daemon.stop)
            stop_thread.start()
            time.sleep(0.05)

            # Release worker to proceed to the atomic commit block
            proceed_to_commit.set()

            client_thread.join(timeout=3.0)
            stop_thread.join(timeout=3.0)

            # Invariant: execution MUST NOT be verified in ledger
            assert ledger.is_execution_verified("exec_race_oracle_01") is False
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_oracle_committed_response_completes_atomically_before_stop_proceeds(self, tmp_path: Path):
        """DEP02B-R5-01: Proves positive Oracle response signing is atomic with terminal commit under lock.

        When terminal ledger append occurs, stop() cannot preempt response construction/signing,
        and stop() must wait until the signed response is fully completed before transitioning to stopping.
        """
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            auth, req = _build_valid_authorization_and_request(
                ks,
                execution_id="exec_race_oracle_atomic_01",
                ledger=ledger,
            )

            record_completed = threading.Event()
            proceed_to_sign_response = threading.Event()
            orig_record = ledger.record_oracle_execution

            def hooked_record(*args, **kwargs):
                res = orig_record(*args, **kwargs)
                record_completed.set()
                proceed_to_sign_response.wait(timeout=2.0)
                return res

            ledger.record_oracle_execution = hooked_record

            client_responses = []
            def send_req():
                try:
                    raw_resp = _send_and_recv_framed(oracle_sock, req.canonical_bytes())
                    resp = OracleVerificationResponse.from_json_bytes(raw_resp)
                    client_responses.append(resp)
                except Exception as e:
                    client_responses.append(e)

            client_thread = threading.Thread(target=send_req)
            client_thread.start()

            # Wait until worker has recorded execution in ledger, but is still inside daemon._lock
            assert record_completed.wait(timeout=3.0)

            # Spawn stop() in another thread
            stop_thread = threading.Thread(target=daemon.stop)
            stop_thread.start()
            time.sleep(0.05)

            # Invariant: daemon._stopping MUST STILL BE FALSE because worker holds daemon._lock!
            # stop() cannot transition to stopping or close sockets while worker is in the commit/sign critical section.
            assert daemon._stopping is False
            assert daemon._running is True

            # Now release worker to complete response construction and Ed25519 signing under lock
            proceed_to_sign_response.set()

            client_thread.join(timeout=3.0)
            stop_thread.join(timeout=3.0)

            # Invariants:
            # 1. Client received fully valid signed response
            assert len(client_responses) == 1
            resp = client_responses[0]
            assert isinstance(resp, OracleVerificationResponse)
            assert resp.is_valid is True
            assert resp.verification_result == "VERIFIED_VALID"
            assert resp.reason_code == "OK"

            # 2. Oracle signature is authentic
            assert ks.verify_oracle_signature(resp.canonical_claims_bytes(), resp.oracle_signature) is True

            # 3. Ledger records execution
            assert ledger.is_execution_verified("exec_race_oracle_atomic_01") is True

            # 4. Daemon has now stopped cleanly
            assert daemon._started is False
            assert daemon._running is False
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_consent_commit_race_with_stop_rejects_issuance(self, tmp_path: Path):
        """DEP02B-R4-01: Deterministic race test proving consent issuance cannot commit after stop() begins."""
        daemon, ks, ledger, _, public_sock = _setup_daemon_fixtures(tmp_path)
        try:
            dir_priv = Ed25519PrivateKey.generate()
            dir_pub_pem = dir_priv.public_key().public_bytes(
                encoding=Encoding.PEM,
                format=PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")
            dir_signer = DirectorApprovalSigner(dir_priv)

            prop, appr = make_test_proposal_and_approval()
            plan = make_test_execution_plan(prop, appr)
            plan_digest = compute_canonical_plan_digest(plan)
            proposal_digest = appr.proposal_revision

            now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            approval_env = dir_signer.sign(
                approval_record=appr,
                plan_digest_sha256=plan_digest,
                proposal_content_digest_sha256=proposal_digest,
                director_key_id="dir_key_test_01",
                signed_at_iso=now_iso,
            )

            ch_req = SignerPublicChallengeRequest(
                schema_version=2,
                request_id=str(uuid.uuid4()),
                approval_envelope=approval_env.to_dict(),
                proposal=edit_plan_proposal_to_dict(prop),
                execution_plan=_execution_plan_to_dict(plan),
                expected_director_key_id="dir_key_test_01",
                director_public_key_pem=dir_pub_pem,
                operator_id="operator_01",
                operator_key_id="op_key_test_01",
                ttl_seconds=120.0,
            )
            ch_resp_bytes = _send_and_recv_framed(public_sock, ch_req.canonical_bytes())
            ch_resp = SignerPublicChallengeResponse.from_dict(json.loads(ch_resp_bytes.decode("utf-8")))

            chal_issued_dt = datetime.datetime.fromisoformat(ch_resp.issued_at_iso.replace("Z", "+00:00"))
            consent_time_iso = (chal_issued_dt + datetime.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

            op_priv = Ed25519PrivateKey.generate()
            op_pub_pem = op_priv.public_key().public_bytes(
                encoding=Encoding.PEM,
                format=PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")

            action = plan.planned_actions[0]
            action_digest = compute_canonical_action_payload_digest_for_admission(
                action=action,
                execution_plan=plan,
                authorization_id=ch_resp.preallocated_authorization_id,
                execution_id=ch_resp.preallocated_execution_id,
            )

            consent_payload_dict = {
                "action_payload_digest_sha256": action_digest,
                "action_type": action.action_type,
                "approval_envelope_digest_sha256": approval_env.compute_envelope_digest(),
                "challenge_id": ch_resp.challenge_id,
                "client_nonce": ch_resp.client_nonce,
                "confirmation_phrase": CONFIRMATION_PHRASE_LITERAL,
                "consent_timestamp_iso": consent_time_iso,
                "duration_frames": action.timeline_range.duration_frames,
                "freshness_attestation": FRESHNESS_ATTESTATION_LITERAL,
                "operator_id": "operator_01",
                "operator_key_id": "op_key_test_01",
                "plan_digest_sha256": plan_digest,
                "record_frame": action.timeline_range.start_frame,
                "schema_version": 1,
                "source_file_path": action.source_file_path,
                "source_file_sha256": plan.source_digests[0].full_sha256,
                "target_project_name": plan.target_spec.project_name,
                "target_project_uid": plan.target_spec.project_id,
                "target_timeline_name": plan.target_spec.timeline_name,
                "target_timeline_uid": plan.target_spec.timeline_id,
                "track_index": action.target_track_index,
                "track_type": action.target_track_type,
            }
            consent_signing_bytes = canonical_json_bytes(consent_payload_dict)
            op_sig_hex = op_priv.sign(consent_signing_bytes).hex().lower()

            full_consent_dict = dict(consent_payload_dict)
            full_consent_dict["operator_signature"] = op_sig_hex
            consent_env = SignedOperatorConsentEnvelope.from_dict(full_consent_dict)

            # Hook admission verification to pause right after precheck
            precheck_done = threading.Event()
            proceed_to_commit = threading.Event()
            orig_adm = daemon._challenge_authority.verify_and_register_admission_for_local_test

            def hooked_adm(*args, **kwargs):
                res = orig_adm(*args, **kwargs)
                precheck_done.set()
                proceed_to_commit.wait(timeout=2.0)
                return res

            daemon._challenge_authority.verify_and_register_admission_for_local_test = hooked_adm

            consent_req = SignerPublicConsentRequest(
                schema_version=2,
                request_id=str(uuid.uuid4()),
                challenge_id=ch_resp.challenge_id,
                consent_envelope=consent_env.to_dict(),
                operator_public_key_pem=op_pub_pem,
                ttl_seconds=120.0,
            )

            client_result = []
            def send_consent():
                try:
                    cons_resp_bytes = _send_and_recv_framed(public_sock, consent_req.canonical_bytes())
                    resp_data = json.loads(cons_resp_bytes.decode("utf-8"))
                    client_result.append(resp_data)
                except Exception as e:
                    client_result.append(str(e))

            client_thread = threading.Thread(target=send_consent)
            client_thread.start()

            # Wait until worker completes admission precheck
            assert precheck_done.wait(timeout=3.0)

            # Trigger daemon stop() while worker is paused before commit block
            stop_thread = threading.Thread(target=daemon.stop)
            stop_thread.start()
            time.sleep(0.05)

            # Release worker into the atomic commit block
            proceed_to_commit.set()

            client_thread.join(timeout=3.0)
            stop_thread.join(timeout=3.0)

            # Invariant: zero ledger mutation, zero identity recorded
            assert ledger.has_challenge_id(ch_resp.challenge_id) is False
            assert ledger.get_authorization_identity(ch_resp.preallocated_authorization_id) is None
        finally:
            daemon.stop()
            ks.close()
            ledger.close()


class TestOracleTerminalNotificationSocketFlow:
    """Verifies Oracle AF_UNIX terminal-state notification protocol over real sockets."""

    def test_terminal_notification_all_states(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            for state in ["COMPLETED", "ABORTED", "IN_DOUBT"]:
                exec_id = f"exec_term_{state.lower()}"
                auth, ver_req = _build_valid_authorization_and_request(
                    ks,
                    execution_id=exec_id,
                    ledger=ledger,
                )

                # 1. Establish verified execution
                raw_ver_resp = _send_and_recv_framed(oracle_sock, ver_req.canonical_bytes())
                ver_resp = OracleVerificationResponse.from_json_bytes(raw_ver_resp)
                assert ver_resp.verification_result == "VERIFIED_VALID"
                assert ledger.is_execution_verified(exec_id) is True
                assert ledger.is_execution_terminal(exec_id) is False

                # 2. Send terminal notification
                term_req = _build_valid_terminal_request(auth, terminal_state=state)
                raw_term_resp = _send_and_recv_framed(oracle_sock, term_req.canonical_bytes())
                term_resp = OracleTerminalNotificationResponse.from_json_bytes(raw_term_resp)

                assert term_resp.schema_version == 2
                assert term_resp.execution_id == exec_id
                assert term_resp.terminal_state == state
                assert term_resp.status == "ACCEPTED"
                assert term_resp.reason_code == "OK"
                assert ks.verify_oracle_signature(term_resp.canonical_claims_bytes(), term_resp.oracle_signature) is True

                # 3. Assert durable state in ledger
                assert ledger.is_execution_terminal(exec_id) is True
                rec = ledger.get_oracle_terminal_state(exec_id)
                assert rec is not None
                assert rec.terminal_state == state
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_terminal_notification_peer_authentication(self, tmp_path: Path):
        # Daemon allows only UID 99999
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(
            tmp_path, oracle_allowed_uids=(99999,)
        )
        try:
            auth, _ = _build_valid_authorization_and_request(ks, execution_id="exec_peer_test", ledger=ledger)
            term_req = _build_valid_terminal_request(auth, terminal_state="COMPLETED")
            raw_resp = _send_and_recv_framed(oracle_sock, term_req.canonical_bytes())
            resp = json.loads(raw_resp.decode("utf-8"))
            assert resp.get("error") == ERR_PEER_UNAUTHORIZED
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_terminal_notification_unknown_execution(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            auth, _ = _build_valid_authorization_and_request(ks, execution_id="exec_unverified", ledger=ledger)
            term_req = _build_valid_terminal_request(auth, terminal_state="COMPLETED")
            raw_resp = _send_and_recv_framed(oracle_sock, term_req.canonical_bytes())
            resp = OracleTerminalNotificationResponse.from_json_bytes(raw_resp)

            assert resp.status == "REJECTED"
            assert resp.reason_code == ERR_EXECUTION_NOT_FOUND
            assert ks.verify_oracle_signature(resp.canonical_claims_bytes(), resp.oracle_signature) is True
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_terminal_notification_identity_binding_mismatches(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            auth, ver_req = _build_valid_authorization_and_request(ks, execution_id="exec_bind_mismatch", ledger=ledger)
            raw_ver_resp = _send_and_recv_framed(oracle_sock, ver_req.canonical_bytes())
            assert OracleVerificationResponse.from_json_bytes(raw_ver_resp).verification_result == "VERIFIED_VALID"

            # 1. Mismatched authorization_id
            req_bad_auth = OracleTerminalNotificationRequest(
                schema_version=2,
                request_id=str(uuid.uuid4()),
                request_nonce=hashlib.sha256(b"n1").hexdigest(),
                execution_id=auth.execution_id,
                authorization_id="wrong_auth_id",
                terminal_state="COMPLETED",
                proposal_revision=auth.proposal_revision,
                target_project_uid=auth.target_project_uid,
                target_timeline_uid=auth.target_timeline_uid,
                action_payload_digest_sha256=auth.action_payload_digest_sha256,
                requested_at_iso="2026-11-04T12:02:00Z",
            )
            resp = OracleTerminalNotificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req_bad_auth.canonical_bytes())
            )
            assert resp.status == "REJECTED"
            assert resp.reason_code == ERR_IDENTITY_BINDING_MISMATCH
            assert ks.verify_oracle_signature(resp.canonical_claims_bytes(), resp.oracle_signature) is True

            # 2. Mismatched proposal_revision
            req_bad_rev = OracleTerminalNotificationRequest(
                schema_version=2,
                request_id=str(uuid.uuid4()),
                request_nonce=hashlib.sha256(b"n2").hexdigest(),
                execution_id=auth.execution_id,
                authorization_id=auth.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision="f" * 64,
                target_project_uid=auth.target_project_uid,
                target_timeline_uid=auth.target_timeline_uid,
                action_payload_digest_sha256=auth.action_payload_digest_sha256,
                requested_at_iso="2026-11-04T12:02:00Z",
            )
            resp = OracleTerminalNotificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req_bad_rev.canonical_bytes())
            )
            assert resp.status == "REJECTED"
            assert resp.reason_code == ERR_IDENTITY_BINDING_MISMATCH

            # 3. Mismatched target_project_uid
            req_bad_proj = OracleTerminalNotificationRequest(
                schema_version=2,
                request_id=str(uuid.uuid4()),
                request_nonce=hashlib.sha256(b"n3").hexdigest(),
                execution_id=auth.execution_id,
                authorization_id=auth.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision=auth.proposal_revision,
                target_project_uid="wrong_project_uid",
                target_timeline_uid=auth.target_timeline_uid,
                action_payload_digest_sha256=auth.action_payload_digest_sha256,
                requested_at_iso="2026-11-04T12:02:00Z",
            )
            resp = OracleTerminalNotificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req_bad_proj.canonical_bytes())
            )
            assert resp.status == "REJECTED"
            assert resp.reason_code == ERR_IDENTITY_BINDING_MISMATCH

            # 4. Mismatched target_timeline_uid
            req_bad_tl = OracleTerminalNotificationRequest(
                schema_version=2,
                request_id=str(uuid.uuid4()),
                request_nonce=hashlib.sha256(b"n4").hexdigest(),
                execution_id=auth.execution_id,
                authorization_id=auth.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision=auth.proposal_revision,
                target_project_uid=auth.target_project_uid,
                target_timeline_uid="wrong_timeline_uid",
                action_payload_digest_sha256=auth.action_payload_digest_sha256,
                requested_at_iso="2026-11-04T12:02:00Z",
            )
            resp = OracleTerminalNotificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req_bad_tl.canonical_bytes())
            )
            assert resp.status == "REJECTED"
            assert resp.reason_code == ERR_IDENTITY_BINDING_MISMATCH

            # 5. Mismatched action_payload_digest_sha256
            req_bad_digest = OracleTerminalNotificationRequest(
                schema_version=2,
                request_id=str(uuid.uuid4()),
                request_nonce=hashlib.sha256(b"n5").hexdigest(),
                execution_id=auth.execution_id,
                authorization_id=auth.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision=auth.proposal_revision,
                target_project_uid=auth.target_project_uid,
                target_timeline_uid=auth.target_timeline_uid,
                action_payload_digest_sha256="0" * 64,
                requested_at_iso="2026-11-04T12:02:00Z",
            )
            resp = OracleTerminalNotificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, req_bad_digest.canonical_bytes())
            )
            assert resp.status == "REJECTED"
            assert resp.reason_code == ERR_IDENTITY_BINDING_MISMATCH
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_terminal_notification_idempotence_and_conflict(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            auth, ver_req = _build_valid_authorization_and_request(ks, execution_id="exec_idem_test", ledger=ledger)
            raw_ver_resp = _send_and_recv_framed(oracle_sock, ver_req.canonical_bytes())
            assert OracleVerificationResponse.from_json_bytes(raw_ver_resp).verification_result == "VERIFIED_VALID"

            # 1. Initial terminal transition -> ACCEPTED
            term_req = _build_valid_terminal_request(auth, terminal_state="COMPLETED")
            resp1 = OracleTerminalNotificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, term_req.canonical_bytes())
            )
            assert resp1.status == "ACCEPTED"
            assert resp1.reason_code == "OK"
            assert ks.verify_oracle_signature(resp1.canonical_claims_bytes(), resp1.oracle_signature) is True

            # 2. Repeated terminal transition with same state -> TERMINAL_ALREADY_RECORDED (idempotent)
            term_req2 = _build_valid_terminal_request(auth, terminal_state="COMPLETED")
            resp2 = OracleTerminalNotificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, term_req2.canonical_bytes())
            )
            assert resp2.status == "TERMINAL_ALREADY_RECORDED"
            assert resp2.reason_code == "OK"
            assert ks.verify_oracle_signature(resp2.canonical_claims_bytes(), resp2.oracle_signature) is True

            # 3. Conflicting terminal state -> REJECTED with ERR_TERMINAL_STATE_CONFLICT
            term_req3 = _build_valid_terminal_request(auth, terminal_state="ABORTED")
            resp3 = OracleTerminalNotificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, term_req3.canonical_bytes())
            )
            assert resp3.status == "REJECTED"
            assert resp3.reason_code == ERR_TERMINAL_STATE_CONFLICT
            assert ks.verify_oracle_signature(resp3.canonical_claims_bytes(), resp3.oracle_signature) is True
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_post_terminal_verification_rejected(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            auth, ver_req = _build_valid_authorization_and_request(ks, execution_id="exec_post_term", ledger=ledger)
            raw_ver_resp = _send_and_recv_framed(oracle_sock, ver_req.canonical_bytes())
            assert OracleVerificationResponse.from_json_bytes(raw_ver_resp).verification_result == "VERIFIED_VALID"

            # Terminate execution
            term_req = _build_valid_terminal_request(auth, terminal_state="COMPLETED")
            term_resp = OracleTerminalNotificationResponse.from_json_bytes(
                _send_and_recv_framed(oracle_sock, term_req.canonical_bytes())
            )
            assert term_resp.status == "ACCEPTED"

            # Post-terminal verification attempt must fail closed with ERR_EXECUTION_ALREADY_TERMINAL
            raw_post_ver = _send_and_recv_framed(oracle_sock, ver_req.canonical_bytes())
            post_ver = OracleVerificationResponse.from_json_bytes(raw_post_ver)
            assert post_ver.verification_result == "VERIFICATION_FAILED"
            assert post_ver.reason_code == ERR_EXECUTION_ALREADY_TERMINAL
            assert ks.verify_oracle_signature(post_ver.canonical_claims_bytes(), post_ver.oracle_signature) is True
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_concurrent_terminal_notifications(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            auth, ver_req = _build_valid_authorization_and_request(ks, execution_id="exec_conc_term", ledger=ledger)
            raw_ver = _send_and_recv_framed(oracle_sock, ver_req.canonical_bytes())
            assert OracleVerificationResponse.from_json_bytes(raw_ver).verification_result == "VERIFIED_VALID"

            results = []
            num_threads = 4

            def worker_thread():
                req = _build_valid_terminal_request(auth, terminal_state="COMPLETED")
                raw = _send_and_recv_framed(oracle_sock, req.canonical_bytes())
                resp = OracleTerminalNotificationResponse.from_json_bytes(raw)
                results.append(resp)

            threads = [threading.Thread(target=worker_thread) for _ in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

            assert len(results) == num_threads
            accepted_count = sum(1 for r in results if r.status == "ACCEPTED")
            idempotent_count = sum(1 for r in results if r.status == "TERMINAL_ALREADY_RECORDED")

            assert accepted_count == 1
            assert idempotent_count == num_threads - 1

            for r in results:
                assert r.reason_code == "OK"
                assert ks.verify_oracle_signature(r.canonical_claims_bytes(), r.oracle_signature) is True

            assert ledger.is_execution_terminal(auth.execution_id) is True
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_legacy_dep02b_records_remain_readable_and_nonterminal(self, tmp_path: Path):
        daemon, ks, ledger, oracle_sock, _ = _setup_daemon_fixtures(tmp_path)
        try:
            auth, ver_req = _build_valid_authorization_and_request(ks, execution_id="exec_legacy_01", ledger=ledger)
            raw_ver = _send_and_recv_framed(oracle_sock, ver_req.canonical_bytes())
            assert OracleVerificationResponse.from_json_bytes(raw_ver).verification_result == "VERIFIED_VALID"

            assert ledger.is_execution_verified("exec_legacy_01") is True
            assert ledger.is_execution_terminal("exec_legacy_01") is False
            assert ledger.get_oracle_terminal_state("exec_legacy_01") is None
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

        # Reopen ledger without daemon
        ledger_dir = tmp_path / "nonces"
        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger2:
            assert ledger2.is_execution_verified("exec_legacy_01") is True
            assert ledger2.is_execution_terminal("exec_legacy_01") is False
            assert ledger2.get_oracle_terminal_state("exec_legacy_01") is None
