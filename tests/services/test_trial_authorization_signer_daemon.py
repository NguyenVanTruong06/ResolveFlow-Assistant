"""Unit tests for TrialAuthorizationSignerDaemon and DurableSignerLedger.

Verifies:
- Daemon import and constructor inertness (no file, socket, or thread activity)
- Lifecycle: start, stop, singleton lock acquisition and persistence
- Singleton lock contention: fail-closed with ERR_DAEMON_ALREADY_RUNNING
- Stale socket cleanup vs foreign node preservation
- Descriptor-anchored directory initialization
- Durable ledger nonce consumption, execution recording, and WAL recovery
- Rejection of corrupt or torn ledger records
"""

import fcntl
import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Tuple

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.contracts.oracle_ipc_contracts import (
    ERR_DAEMON_ALREADY_RUNNING,
    ERR_EXECUTION_NOT_FOUND,
    ERR_IDENTITY_BINDING_MISMATCH,
    ERR_INVALID_FRAME,
    ERR_LEDGER_POISONED,
    ERR_SCHEMA_VERSION_INVALID,
    ERR_TERMINAL_STATE_CONFLICT,
    ERR_TERMINAL_STATE_INVALID,
    ERR_UNEXPECTED_FIELD,
    OracleProtocolError,
    OracleTerminalNotificationRequest,
    OracleTerminalNotificationResponse,
    OracleVerificationRequest,
    VALID_TERMINAL_STATES,
    decode_oracle_request,
)
from src.services.durable_nonce_ledger import (
    AuthorizationIdentityRecord,
    DurableSignerLedger,
    OracleExecutionRecord,
    OracleExecutionTerminalRecord,
    SignerLedgerCorruptionError,
    SignerLedgerDurabilityError,
    SignerLedgerSecurityError,
    SignerNonceAlreadyConsumedError,
)
from src.services.signer_keystore_service import (
    HMAC_KEY_FILENAME,
    ORACLE_KEY_FILENAME,
    SignerKeystoreService,
)
from src.services.trial_authorization_signer_daemon import (
    PRODUCTION_CLIENTS_GID,
    PRODUCTION_DISPATCHER_GID,
    PRODUCTION_DISPATCHER_UID,
    PRODUCTION_SIGNER_UID,
    TrialAuthorizationSignerDaemon,
)
from src.services.trial_authorization_signer_service import (
    ConsumedAuthorizationNonceRecord,
    TrialSignerError,
)


def _setup_test_fixtures(tmp_path: Path) -> Tuple[SignerKeystoreService, DurableSignerLedger, Path, Path]:
    ks_dir = tmp_path / "keystore"
    ks_dir.mkdir(mode=0o700)
    os.chmod(ks_dir, 0o700)

    hmac_file = ks_dir / HMAC_KEY_FILENAME
    hmac_file.write_bytes(b"\x01" * 32)
    os.chmod(hmac_file, 0o600)

    oracle_file = ks_dir / ORACLE_KEY_FILENAME
    priv_key = Ed25519PrivateKey.generate()
    oracle_file.write_bytes(priv_key.private_bytes_raw())
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

    return keystore, ledger, oracle_dir, public_dir


class TestSignerDaemonInertness:

    def test_daemon_init_is_inert(self, tmp_path: Path):
        daemon = TrialAuthorizationSignerDaemon(
            oracle_dir=tmp_path / "oracle",
            public_dir=tmp_path / "public",
        )
        assert daemon.is_running is False
        assert not (tmp_path / "oracle").exists()
        assert not (tmp_path / "public").exists()

    def test_start_without_dependencies_fails(self, tmp_path: Path):
        daemon = TrialAuthorizationSignerDaemon(
            oracle_dir=tmp_path / "oracle",
            public_dir=tmp_path / "public",
        )
        with pytest.raises(TrialSignerError, match="ERR_KEYSTORE_REQUIRED"):
            daemon.start()


class TestSignerDaemonLifecycle:

    def test_start_stop_clean(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        try:
            daemon = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                enforce_dac_ownership=False,
            )

            daemon.start()
            assert daemon.is_running is True

            oracle_sock = oracle_dir / "signer_oracle.sock"
            oracle_lock = oracle_dir / "signer_oracle.lock"
            public_sock = public_dir / "signer_public.sock"
            public_lock = public_dir / "signer_public.lock"

            assert oracle_sock.exists()
            assert oracle_lock.exists()
            assert public_sock.exists()
            assert public_lock.exists()

            daemon.stop()
            assert daemon.is_running is False

            # Sockets must be unlinked
            assert not oracle_sock.exists()
            assert not public_sock.exists()

            # Lock files must NEVER be unlinked
            assert oracle_lock.exists()
            assert public_lock.exists()
        finally:
            ks.close()
            ledger.close()

    def test_double_start_rejected(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        try:
            daemon = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                enforce_dac_ownership=False,
            )
            daemon.start()
            with pytest.raises(TrialSignerError, match="ERR_DAEMON_ALREADY_STARTED"):
                daemon.start()
            daemon.stop()
        finally:
            ks.close()
            ledger.close()

    def test_singleton_lock_contention(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        try:
            daemon1 = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                enforce_dac_ownership=False,
            )
            daemon1.start()

            daemon2 = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                enforce_dac_ownership=False,
            )
            with pytest.raises(TrialSignerError, match=ERR_DAEMON_ALREADY_RUNNING):
                daemon2.start()

            # Daemon 1 must remain healthy and running
            assert daemon1.is_running is True
            assert (oracle_dir / "signer_oracle.sock").exists()

            daemon1.stop()
        finally:
            ks.close()
            ledger.close()

    def test_lock_file_persistence_and_restart(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        try:
            daemon1 = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                enforce_dac_ownership=False,
            )
            daemon1.start()
            daemon1.stop()

            # Restart daemon on the same directories with pre-existing lock files
            daemon2 = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                enforce_dac_ownership=False,
            )
            daemon2.start()
            assert daemon2.is_running is True
            assert (oracle_dir / "signer_oracle.sock").exists()
            daemon2.stop()
        finally:
            ks.close()
            ledger.close()


class TestSignerDaemonStaleSocketHandling:

    def test_stale_owned_socket_cleaned_up(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        try:
            # Simulate a stale socket left over by previous process
            import socket as py_socket
            stale_sock_path = str(oracle_dir / "signer_oracle.sock")
            s = py_socket.socket(py_socket.AF_UNIX, py_socket.SOCK_STREAM)
            s.bind(stale_sock_path)
            s.close()
            assert os.path.exists(stale_sock_path)

            daemon = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                enforce_dac_ownership=False,
            )
            daemon.start()
            assert daemon.is_running is True
            daemon.stop()
        finally:
            ks.close()
            ledger.close()

    def test_foreign_socket_symlink_preserved_and_rejected(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        try:
            symlink_target = tmp_path / "foreign_target"
            symlink_target.write_text("evil")
            sock_link = oracle_dir / "signer_oracle.sock"
            sock_link.symlink_to(symlink_target)

            daemon = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                enforce_dac_ownership=False,
            )
            with pytest.raises(TrialSignerError, match="ERR_FOREIGN_SOCKET_NODE"):
                daemon.start()

            # Foreign node MUST NOT be unlinked
            assert sock_link.is_symlink()
        finally:
            ks.close()
            ledger.close()

    def test_foreign_lock_symlink_preserved_and_rejected(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        try:
            symlink_target = tmp_path / "foreign_lock"
            symlink_target.write_text("evil")
            lock_link = oracle_dir / "signer_oracle.lock"
            lock_link.symlink_to(symlink_target)

            daemon = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                enforce_dac_ownership=False,
            )
            with pytest.raises(TrialSignerError, match="ERR_FOREIGN_LOCK_NODE"):
                daemon.start()

            # Foreign lock symlink MUST NOT be unlinked
            assert lock_link.is_symlink()
        finally:
            ks.close()
            ledger.close()


class TestSignerDaemonDescriptorAnchoredMode:

    def test_descriptor_backed_start(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        oracle_fd = os.open(str(oracle_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        public_fd = os.open(str(public_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            daemon = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir_fd=oracle_fd,
                public_dir_fd=public_fd,
                enforce_dac_ownership=False,
            )
            daemon.start()
            assert daemon.is_running is True
            daemon.stop()
            assert daemon.is_running is False
        finally:
            os.close(oracle_fd)
            os.close(public_fd)
            ks.close()
            ledger.close()


class TestDurableNonceLedger:

    def test_nonce_consumption_and_replay(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        record = ConsumedAuthorizationNonceRecord(
            challenge_id="ch_1",
            client_nonce="cn_1",
            approval_record_id="app_1",
            proposal_revision="rev_1",
            consumed_at_iso="2026-11-04T12:00:00Z",
        )

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            assert ledger.is_nonce_consumed("ch_1", "cn_1", ("app_1", "rev_1")) is False
            ledger.record_consumed_nonce(record)
            assert ledger.is_nonce_consumed("ch_1", "cn_1", ("app_1", "rev_1")) is True

            # Duplicate consumption must fail
            with pytest.raises(SignerNonceAlreadyConsumedError, match="ERR_NONCE_ALREADY_CONSUMED"):
                ledger.record_consumed_nonce(record)

        # Reopening ledger recovers state from WAL
        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger2:
            assert ledger2.is_nonce_consumed("ch_1", "cn_1", ("app_1", "rev_1")) is True
            with pytest.raises(SignerNonceAlreadyConsumedError, match="ERR_NONCE_ALREADY_CONSUMED"):
                ledger2.record_consumed_nonce(record)

    def test_oracle_execution_recording_and_replay(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        record = OracleExecutionRecord(
            schema_version=1,
            execution_id="exec_1",
            authorization_id="auth_1",
            proposal_revision="rev_1",
            target_project_uid="proj_1",
            target_timeline_uid="tl_1",
            verification_status="VERIFIED_VALID",
            verified_at_iso="2026-11-04T12:00:00Z",
        )

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            assert ledger.is_execution_verified("exec_1") is False
            ledger.record_oracle_execution(record)
            assert ledger.is_execution_verified("exec_1") is True

            # Duplicate execution must fail
            with pytest.raises(SignerLedgerSecurityError, match="ERR_EXECUTION_ALREADY_VERIFIED"):
                ledger.record_oracle_execution(record)

        # Reopening ledger recovers state from WAL
        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger2:
            assert ledger2.is_execution_verified("exec_1") is True
            with pytest.raises(SignerLedgerSecurityError, match="ERR_EXECUTION_ALREADY_VERIFIED"):
                ledger2.record_oracle_execution(record)

    def test_authorization_identity_recording_and_recovery(self, tmp_path: Path):
        """DEP02B-R3-01: Verifies authorization identity record persistence, integrity, and restart recovery."""
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        record = AuthorizationIdentityRecord(
            schema_version=1,
            authorization_id="auth_persisted_01",
            director_key_id="dir_production_01",
            operator_key_id="op_production_01",
            action_payload_digest_sha256="a" * 64,
            proposal_revision="b" * 64,
            issued_at_iso="2026-11-04T12:00:00Z",
        )

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            assert ledger.get_authorization_identity("auth_persisted_01") is None
            ledger.record_authorization_identity(record)
            rec = ledger.get_authorization_identity("auth_persisted_01")
            assert rec is not None
            assert rec.authorization_id == "auth_persisted_01"
            assert rec.director_key_id == "dir_production_01"
            assert rec.operator_key_id == "op_production_01"
            assert rec.action_payload_digest_sha256 == "a" * 64

            # Duplicate recording must be rejected
            with pytest.raises(SignerLedgerSecurityError, match="ERR_AUTHORIZATION_ID_ALREADY_RECORDED"):
                ledger.record_authorization_identity(record)

        # Reopen ledger from disk to test durable recovery (simulating daemon restart)
        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger2:
            rec2 = ledger2.get_authorization_identity("auth_persisted_01")
            assert rec2 is not None
            assert rec2.authorization_id == "auth_persisted_01"
            assert rec2.director_key_id == "dir_production_01"
            assert rec2.operator_key_id == "op_production_01"
            assert rec2.action_payload_digest_sha256 == "a" * 64
            assert rec2.proposal_revision == "b" * 64

            # Duplicate rejection persists across restart
            with pytest.raises(SignerLedgerSecurityError, match="ERR_AUTHORIZATION_ID_ALREADY_RECORDED"):
                ledger2.record_authorization_identity(record)

    def test_torn_ledger_file_detected(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        # Write a torn line (no trailing newline)
        nonce_file = ledger_dir / "consumed_nonces.jsonl"
        nonce_file.write_bytes(b'{"partial": true')
        os.chmod(nonce_file, 0o600)

        with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED"):
            DurableSignerLedger(ledger_dir, enforce_ownership=False)


class TestSignerDaemonProductionInvariants:

    def test_production_init_rejects_weakened_dac(self, tmp_path: Path):
        with pytest.raises(TrialSignerError, match="ERR_PRODUCTION_SECURITY_INVARIANT"):
            TrialAuthorizationSignerDaemon(
                oracle_dir=tmp_path / "oracle",
                public_dir=tmp_path / "public",
                enforce_dac_ownership=False,
            )

    def test_production_init_rejects_custom_oracle_allowed_uids(self, tmp_path: Path):
        with pytest.raises(TrialSignerError, match="ERR_PRODUCTION_SECURITY_INVARIANT"):
            TrialAuthorizationSignerDaemon(
                oracle_dir=tmp_path / "oracle",
                public_dir=tmp_path / "public",
                oracle_allowed_uids=(1234,),
            )

    def test_production_init_rejects_custom_daemon_uid(self, tmp_path: Path):
        with pytest.raises(TrialSignerError, match="ERR_PRODUCTION_SECURITY_INVARIANT"):
            TrialAuthorizationSignerDaemon(
                oracle_dir=tmp_path / "oracle",
                public_dir=tmp_path / "public",
                expected_daemon_uid=1234,
            )

    def test_production_init_rejects_custom_oracle_gid(self, tmp_path: Path):
        with pytest.raises(TrialSignerError, match="ERR_PRODUCTION_SECURITY_INVARIANT"):
            TrialAuthorizationSignerDaemon(
                oracle_dir=tmp_path / "oracle",
                public_dir=tmp_path / "public",
                expected_oracle_gid=1234,
            )

    def test_production_init_rejects_custom_public_gid(self, tmp_path: Path):
        with pytest.raises(TrialSignerError, match="ERR_PRODUCTION_SECURITY_INVARIANT"):
            TrialAuthorizationSignerDaemon(
                oracle_dir=tmp_path / "oracle",
                public_dir=tmp_path / "public",
                expected_public_gid=1234,
            )

    def test_production_init_rejects_for_test(self, tmp_path: Path):
        with pytest.raises(TrialSignerError, match="ERR_PRODUCTION_SECURITY_INVARIANT"):
            TrialAuthorizationSignerDaemon(
                oracle_dir=tmp_path / "oracle",
                public_dir=tmp_path / "public",
                for_test=True,
            )


class TestSignerDaemonDACModeAndOwnership:

    def test_rejects_non_02750_mode(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        # Directory has 0o700, not 0o2750
        try:
            daemon = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                expected_daemon_uid=os.geteuid(),
                expected_oracle_gid=os.getegid(),
                expected_public_gid=os.getegid(),
                enforce_dac_ownership=True,
            )
            with pytest.raises(TrialSignerError, match="ERR_INVALID_MODE"):
                daemon.start()
        finally:
            ks.close()
            ledger.close()

    def test_rejects_wrong_owner_uid(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        try:
            daemon = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                expected_daemon_uid=os.geteuid() + 999,
                expected_oracle_gid=os.getegid(),
                expected_public_gid=os.getegid(),
                enforce_dac_ownership=True,
            )
            with pytest.raises(TrialSignerError, match="ERR_INVALID_OWNER"):
                daemon.start()
        finally:
            ks.close()
            ledger.close()

    def test_rejects_wrong_group_gid(self, tmp_path: Path):
        ks, ledger, oracle_dir, public_dir = _setup_test_fixtures(tmp_path)
        try:
            daemon = TrialAuthorizationSignerDaemon.create_for_test(
                keystore_service=ks,
                nonce_ledger=ledger,
                oracle_dir=oracle_dir,
                public_dir=public_dir,
                expected_daemon_uid=os.geteuid(),
                expected_oracle_gid=os.getegid() + 999,
                expected_public_gid=os.getegid(),
                enforce_dac_ownership=True,
            )
            with pytest.raises(TrialSignerError, match="ERR_INVALID_GROUP"):
                daemon.start()
        finally:
            ks.close()
            ledger.close()


class TestDurableNonceLedgerPoisoning:

    def test_ledger_poisoned_on_write_error(self, tmp_path: Path, monkeypatch):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        record = ConsumedAuthorizationNonceRecord(
            schema_version=1,
            challenge_id="ch_err",
            client_nonce="cn_err",
            approval_record_id="app_err",
            proposal_revision="rev_err",
            consumed_at_iso="2026-11-04T12:00:00Z",
        )

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            def fail_write(*args, **kwargs):
                raise OSError("Simulated write failure")
            monkeypatch.setattr(os, "write", fail_write)

            with pytest.raises(SignerLedgerDurabilityError):
                ledger.record_consumed_nonce(record)

            assert ledger.is_poisoned is True

            # Subsequent attempts fail-closed with ERR_LEDGER_POISONED
            monkeypatch.undo()
            with pytest.raises(SignerLedgerDurabilityError, match=ERR_LEDGER_POISONED):
                ledger.record_consumed_nonce(record)

            with pytest.raises(SignerLedgerDurabilityError, match=ERR_LEDGER_POISONED):
                ledger.is_nonce_consumed("ch_err", "cn_err", ("app_err", "rev_err"))

            with pytest.raises(SignerLedgerDurabilityError, match=ERR_LEDGER_POISONED):
                ledger.record_oracle_execution(
                    OracleExecutionRecord(
                        schema_version=1,
                        execution_id="exec_err",
                        authorization_id="auth_err",
                        proposal_revision="rev_err",
                        target_project_uid="p1",
                        target_timeline_uid="t1",
                        verification_status="VERIFIED_VALID",
                        verified_at_iso="2026-11-04T12:00:00Z",
                    )
                )

            with pytest.raises(SignerLedgerDurabilityError, match=ERR_LEDGER_POISONED):
                ledger.is_execution_verified("exec_err")

    def test_ledger_poisoned_on_fsync_error(self, tmp_path: Path, monkeypatch):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        record = ConsumedAuthorizationNonceRecord(
            schema_version=1,
            challenge_id="ch_fsync",
            client_nonce="cn_fsync",
            approval_record_id="app_fsync",
            proposal_revision="rev_fsync",
            consumed_at_iso="2026-11-04T12:00:00Z",
        )

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            def fail_fsync(*args, **kwargs):
                raise OSError("Simulated fsync failure")
            monkeypatch.setattr(os, "fsync", fail_fsync)

            with pytest.raises(SignerLedgerDurabilityError):
                ledger.record_consumed_nonce(record)

            assert ledger.is_poisoned is True
            monkeypatch.undo()

            with pytest.raises(SignerLedgerDurabilityError, match=ERR_LEDGER_POISONED):
                ledger.record_consumed_nonce(record)


class TestOracleTerminalDTOsAndContracts:
    """Verifies DTO canonical round-trip, validation, and discrimination for terminal contracts."""

    def _make_valid_terminal_request_dict(self) -> dict:
        return {
            "schema_version": 2,
            "request_id": "c1f2e3d4-1111-4000-8000-000000000001",
            "request_nonce": "a" * 64,
            "execution_id": "exec_test_01",
            "authorization_id": "auth_test_01",
            "terminal_state": "COMPLETED",
            "proposal_revision": "b" * 64,
            "target_project_uid": "proj_uid_1",
            "target_timeline_uid": "tl_uid_1",
            "action_payload_digest_sha256": "c" * 64,
            "requested_at_iso": "2026-11-04T12:00:00Z",
        }

    def test_terminal_request_round_trip(self):
        d = self._make_valid_terminal_request_dict()
        req = OracleTerminalNotificationRequest.from_dict(d)
        assert req.schema_version == 2
        assert req.execution_id == "exec_test_01"
        assert req.terminal_state == "COMPLETED"
        assert req.to_dict() == d
        json_bytes = req.canonical_bytes()
        req2 = OracleTerminalNotificationRequest.from_json_bytes(json_bytes)
        assert req2 == req

    def test_terminal_request_rejects_duplicate_json_keys(self):
        raw = b'{"schema_version": 2, "terminal_state": "COMPLETED", "terminal_state": "ABORTED"}'
        with pytest.raises(OracleProtocolError, match="ERR_UNEXPECTED_FIELD"):
            OracleTerminalNotificationRequest.from_json_bytes(raw)

    def test_terminal_request_rejects_unexpected_field(self):
        d = self._make_valid_terminal_request_dict()
        d["extra_field"] = "unexpected"
        with pytest.raises(OracleProtocolError, match="ERR_UNEXPECTED_FIELD"):
            OracleTerminalNotificationRequest.from_dict(d)

    def test_terminal_request_rejects_missing_field(self):
        d = self._make_valid_terminal_request_dict()
        del d["terminal_state"]
        with pytest.raises(OracleProtocolError, match=ERR_INVALID_FRAME):
            OracleTerminalNotificationRequest.from_dict(d)

    def test_terminal_request_rejects_invalid_types_and_values(self):
        d = self._make_valid_terminal_request_dict()
        d["schema_version"] = True
        with pytest.raises(OracleProtocolError, match=ERR_SCHEMA_VERSION_INVALID):
            OracleTerminalNotificationRequest.from_dict(d)

        d = self._make_valid_terminal_request_dict()
        d["request_id"] = "not-a-valid-uuid"
        with pytest.raises(OracleProtocolError, match="ERR_INVALID_UUID"):
            OracleTerminalNotificationRequest.from_dict(d)

        d = self._make_valid_terminal_request_dict()
        d["request_nonce"] = "not-64-hex"
        with pytest.raises(OracleProtocolError, match="ERR_INVALID_NONCE"):
            OracleTerminalNotificationRequest.from_dict(d)

        d = self._make_valid_terminal_request_dict()
        d["requested_at_iso"] = "2026-11-04 12:00:00"  # Not UTC ISO Z
        with pytest.raises(OracleProtocolError, match="ERR_INVALID_TIMESTAMP"):
            OracleTerminalNotificationRequest.from_dict(d)

    def test_terminal_request_rejects_invalid_terminal_state(self):
        d = self._make_valid_terminal_request_dict()
        d["terminal_state"] = "RUNNING"
        with pytest.raises(OracleProtocolError, match=ERR_TERMINAL_STATE_INVALID):
            OracleTerminalNotificationRequest.from_dict(d)

        for valid_state in VALID_TERMINAL_STATES:
            d["terminal_state"] = valid_state
            req = OracleTerminalNotificationRequest.from_dict(d)
            assert req.terminal_state == valid_state

    def test_terminal_response_round_trip(self):
        d = {
            "schema_version": 2,
            "response_id": "c1f2e3d4-1111-4000-8000-000000000002",
            "request_id": "c1f2e3d4-1111-4000-8000-000000000001",
            "request_nonce": "a" * 64,
            "execution_id": "exec_test_01",
            "terminal_state": "COMPLETED",
            "status": "ACCEPTED",
            "reason_code": "OK",
            "recorded_at_iso": "2026-11-04T12:00:00Z",
            "oracle_signature": "e" * 128,
        }
        resp = OracleTerminalNotificationResponse.from_dict(d)
        assert resp.status == "ACCEPTED"
        assert resp.to_dict() == d
        resp2 = OracleTerminalNotificationResponse.from_json_bytes(resp.canonical_bytes())
        assert resp2 == resp

    def test_terminal_response_rejects_invalid_terminal_state(self):
        d = {
            "schema_version": 2,
            "response_id": "c1f2e3d4-1111-4000-8000-000000000002",
            "request_id": "c1f2e3d4-1111-4000-8000-000000000001",
            "request_nonce": "a" * 64,
            "execution_id": "exec_test_01",
            "terminal_state": "RUNNING",
            "status": "ACCEPTED",
            "reason_code": "OK",
            "recorded_at_iso": "2026-11-04T12:00:00Z",
            "oracle_signature": "e" * 128,
        }
        with pytest.raises(OracleProtocolError, match=ERR_TERMINAL_STATE_INVALID):
            OracleTerminalNotificationResponse.from_dict(d)

    def test_terminal_response_rejects_invalid_status(self):
        d = {
            "schema_version": 2,
            "response_id": "c1f2e3d4-1111-4000-8000-000000000002",
            "request_id": "c1f2e3d4-1111-4000-8000-000000000001",
            "request_nonce": "a" * 64,
            "execution_id": "exec_test_01",
            "terminal_state": "COMPLETED",
            "status": "SUCCESS",  # Invalid status (must be ACCEPTED, TERMINAL_ALREADY_RECORDED, REJECTED, FAILED)
            "reason_code": "OK",
            "recorded_at_iso": "2026-11-04T12:00:00Z",
            "oracle_signature": "e" * 128,
        }
        with pytest.raises(OracleProtocolError, match="ERR_INVALID_STATUS"):
            OracleTerminalNotificationResponse.from_dict(d)

    def test_terminal_response_rejects_inconsistent_status_and_reason(self):
        # ACCEPTED with error reason
        d1 = {
            "schema_version": 2,
            "response_id": "c1f2e3d4-1111-4000-8000-000000000002",
            "request_id": "c1f2e3d4-1111-4000-8000-000000000001",
            "request_nonce": "a" * 64,
            "execution_id": "exec_test_01",
            "terminal_state": "COMPLETED",
            "status": "ACCEPTED",
            "reason_code": "ERR_SOMETHING",
            "recorded_at_iso": "2026-11-04T12:00:00Z",
            "oracle_signature": "e" * 128,
        }
        with pytest.raises(OracleProtocolError, match="ERR_INVALID_REASON"):
            OracleTerminalNotificationResponse.from_dict(d1)

        # REJECTED with OK reason
        d2 = {
            "schema_version": 2,
            "response_id": "c1f2e3d4-1111-4000-8000-000000000002",
            "request_id": "c1f2e3d4-1111-4000-8000-000000000001",
            "request_nonce": "a" * 64,
            "execution_id": "exec_test_01",
            "terminal_state": "COMPLETED",
            "status": "REJECTED",
            "reason_code": "OK",
            "recorded_at_iso": "2026-11-04T12:00:00Z",
            "oracle_signature": "e" * 128,
        }
        with pytest.raises(OracleProtocolError, match="ERR_INVALID_REASON"):
            OracleTerminalNotificationResponse.from_dict(d2)

        # REJECTED with non-ERR_ reason
        d3 = {
            "schema_version": 2,
            "response_id": "c1f2e3d4-1111-4000-8000-000000000002",
            "request_id": "c1f2e3d4-1111-4000-8000-000000000001",
            "request_nonce": "a" * 64,
            "execution_id": "exec_test_01",
            "terminal_state": "COMPLETED",
            "status": "REJECTED",
            "reason_code": "UNKNOWN_ERROR",
            "recorded_at_iso": "2026-11-04T12:00:00Z",
            "oracle_signature": "e" * 128,
        }
        with pytest.raises(OracleProtocolError, match="ERR_INVALID_REASON"):
            OracleTerminalNotificationResponse.from_dict(d3)

    def test_terminal_response_rejects_tampered_signature(self):
        d = {
            "schema_version": 2,
            "response_id": "c1f2e3d4-1111-4000-8000-000000000002",
            "request_id": "c1f2e3d4-1111-4000-8000-000000000001",
            "request_nonce": "a" * 64,
            "execution_id": "exec_test_01",
            "terminal_state": "COMPLETED",
            "status": "ACCEPTED",
            "reason_code": "OK",
            "recorded_at_iso": "2026-11-04T12:00:00Z",
            "oracle_signature": "short_sig",
        }
        with pytest.raises(OracleProtocolError, match="ERR_INVALID_SIGNATURE"):
            OracleTerminalNotificationResponse.from_dict(d)

    def test_decode_oracle_request_discrimination(self):
        req_term_dict = self._make_valid_terminal_request_dict()
        term_bytes = json.dumps(req_term_dict).encode("utf-8")
        parsed = decode_oracle_request(term_bytes)
        assert isinstance(parsed, OracleTerminalNotificationRequest)
        assert parsed.execution_id == "exec_test_01"

        ambiguous = b'{"schema_version": 2, "request_id": "test"}'
        with pytest.raises(OracleProtocolError, match="ERR_UNEXPECTED_FIELD"):
            decode_oracle_request(ambiguous)


class TestDurableNonceLedgerTerminals:
    """Verifies durable append-only terminal tracking, idempotency, and recovery."""

    def _setup_verified_execution(
        self, ledger: DurableSignerLedger, execution_id: str = "exec_01"
    ) -> Tuple[OracleExecutionRecord, AuthorizationIdentityRecord]:
        auth_id = f"auth_{execution_id}"
        rev = "a" * 64
        digest = "b" * 64
        proj = "proj_01"
        tl = "tl_01"

        auth_rec = AuthorizationIdentityRecord(
            schema_version=1,
            authorization_id=auth_id,
            director_key_id="dir_01",
            operator_key_id="op_01",
            action_payload_digest_sha256=digest,
            proposal_revision=rev,
            issued_at_iso="2026-11-04T12:00:00Z",
        )
        ledger.record_authorization_identity(auth_rec)

        exec_rec = OracleExecutionRecord(
            schema_version=1,
            execution_id=execution_id,
            authorization_id=auth_id,
            proposal_revision=rev,
            target_project_uid=proj,
            target_timeline_uid=tl,
            verification_status="VERIFIED_VALID",
            verified_at_iso="2026-11-04T12:01:00Z",
        )
        ledger.record_oracle_execution(exec_rec)
        return exec_rec, auth_rec

    def test_ledger_terminal_recording_and_idempotence(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            exec_rec, auth_rec = self._setup_verified_execution(ledger, "exec_t1")

            assert ledger.is_execution_verified("exec_t1") is True
            assert ledger.is_execution_terminal("exec_t1") is False

            term_rec = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=exec_rec.execution_id,
                authorization_id=exec_rec.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision=exec_rec.proposal_revision,
                target_project_uid=exec_rec.target_project_uid,
                target_timeline_uid=exec_rec.target_timeline_uid,
                action_payload_digest_sha256=auth_rec.action_payload_digest_sha256,
                recorded_at_iso="2026-11-04T12:02:00Z",
            )

            # First recording returns True (newly appended)
            assert ledger.record_oracle_terminal_state(term_rec) is True
            assert ledger.is_execution_terminal("exec_t1") is True

            term_file = ledger_dir / "oracle_execution_terminals.jsonl"
            lines_after_first = term_file.read_bytes().splitlines()
            assert len(lines_after_first) == 1

            # Second identical recording returns False (idempotent, no duplicate write)
            assert ledger.record_oracle_terminal_state(term_rec) is False
            lines_after_second = term_file.read_bytes().splitlines()
            assert len(lines_after_second) == 1

    def test_ledger_terminal_rejects_unverified_execution(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            term_rec = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id="exec_unverified",
                authorization_id="auth_unverified",
                terminal_state="ABORTED",
                proposal_revision="a" * 64,
                target_project_uid="proj_01",
                target_timeline_uid="tl_01",
                action_payload_digest_sha256="b" * 64,
                recorded_at_iso="2026-11-04T12:02:00Z",
            )
            with pytest.raises(SignerLedgerSecurityError, match=ERR_EXECUTION_NOT_FOUND):
                ledger.record_oracle_terminal_state(term_rec)

    def test_ledger_terminal_rejects_identity_mismatches(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            exec_rec, auth_rec = self._setup_verified_execution(ledger, "exec_mismatch")

            # Mismatched authorization_id
            mismatch_auth = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=exec_rec.execution_id,
                authorization_id="wrong_auth",
                terminal_state="COMPLETED",
                proposal_revision=exec_rec.proposal_revision,
                target_project_uid=exec_rec.target_project_uid,
                target_timeline_uid=exec_rec.target_timeline_uid,
                action_payload_digest_sha256=auth_rec.action_payload_digest_sha256,
                recorded_at_iso="2026-11-04T12:02:00Z",
            )
            with pytest.raises(SignerLedgerSecurityError, match=ERR_IDENTITY_BINDING_MISMATCH):
                ledger.record_oracle_terminal_state(mismatch_auth)

            # Mismatched proposal_revision
            mismatch_rev = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=exec_rec.execution_id,
                authorization_id=exec_rec.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision="f" * 64,
                target_project_uid=exec_rec.target_project_uid,
                target_timeline_uid=exec_rec.target_timeline_uid,
                action_payload_digest_sha256=auth_rec.action_payload_digest_sha256,
                recorded_at_iso="2026-11-04T12:02:00Z",
            )
            with pytest.raises(SignerLedgerSecurityError, match=ERR_IDENTITY_BINDING_MISMATCH):
                ledger.record_oracle_terminal_state(mismatch_rev)

            # Mismatched target_project_uid
            mismatch_proj = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=exec_rec.execution_id,
                authorization_id=exec_rec.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision=exec_rec.proposal_revision,
                target_project_uid="wrong_proj",
                target_timeline_uid=exec_rec.target_timeline_uid,
                action_payload_digest_sha256=auth_rec.action_payload_digest_sha256,
                recorded_at_iso="2026-11-04T12:02:00Z",
            )
            with pytest.raises(SignerLedgerSecurityError, match=ERR_IDENTITY_BINDING_MISMATCH):
                ledger.record_oracle_terminal_state(mismatch_proj)

            # Mismatched payload digest
            mismatch_digest = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=exec_rec.execution_id,
                authorization_id=exec_rec.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision=exec_rec.proposal_revision,
                target_project_uid=exec_rec.target_project_uid,
                target_timeline_uid=exec_rec.target_timeline_uid,
                action_payload_digest_sha256="0" * 64,
                recorded_at_iso="2026-11-04T12:02:00Z",
            )
            with pytest.raises(SignerLedgerSecurityError, match=ERR_IDENTITY_BINDING_MISMATCH):
                ledger.record_oracle_terminal_state(mismatch_digest)

    def test_ledger_terminal_rejects_missing_authorization_identity(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            # Record verified execution WITHOUT recording authorization identity
            exec_rec = OracleExecutionRecord(
                schema_version=1,
                execution_id="exec_no_auth_id",
                authorization_id="auth_unregistered",
                proposal_revision="a" * 64,
                target_project_uid="proj_01",
                target_timeline_uid="tl_01",
                verification_status="VERIFIED_VALID",
                verified_at_iso="2026-11-04T12:01:00Z",
            )
            ledger.record_oracle_execution(exec_rec)

            term_rec = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=exec_rec.execution_id,
                authorization_id=exec_rec.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision=exec_rec.proposal_revision,
                target_project_uid=exec_rec.target_project_uid,
                target_timeline_uid=exec_rec.target_timeline_uid,
                action_payload_digest_sha256="b" * 64,
                recorded_at_iso="2026-11-04T12:02:00Z",
            )
            with pytest.raises(SignerLedgerSecurityError, match=ERR_IDENTITY_BINDING_MISMATCH):
                ledger.record_oracle_terminal_state(term_rec)

    def test_ledger_terminal_recovery_rejects_missing_authorization_identity(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        # 1. Manually write verified execution to executions WAL
        exec_file = ledger_dir / "oracle_verified_executions.jsonl"
        exec_rec = OracleExecutionRecord(
            schema_version=1,
            execution_id="exec_orphan",
            authorization_id="auth_orphan",
            proposal_revision="a" * 64,
            target_project_uid="proj_01",
            target_timeline_uid="tl_01",
            verification_status="VERIFIED_VALID",
            verified_at_iso="2026-11-04T12:01:00Z",
        )
        exec_file.write_bytes(exec_rec.canonical_bytes() + b"\n")
        os.chmod(exec_file, 0o600)

        # 2. Manually write terminal record without corresponding auth identity WAL record
        term_file = ledger_dir / "oracle_execution_terminals.jsonl"
        term_rec = OracleExecutionTerminalRecord(
            schema_version=1,
            execution_id=exec_rec.execution_id,
            authorization_id=exec_rec.authorization_id,
            terminal_state="COMPLETED",
            proposal_revision=exec_rec.proposal_revision,
            target_project_uid=exec_rec.target_project_uid,
            target_timeline_uid=exec_rec.target_timeline_uid,
            action_payload_digest_sha256="b" * 64,
            recorded_at_iso="2026-11-04T12:02:00Z",
        )
        term_file.write_bytes(term_rec.canonical_bytes() + b"\n")
        os.chmod(term_file, 0o600)

        # 3. Recovery must fail closed with SignerLedgerCorruptionError
        with pytest.raises(SignerLedgerCorruptionError, match="ERR_ORACLE_TERMINAL_IDENTITY_MISMATCH"):
            DurableSignerLedger(ledger_dir, enforce_ownership=False)

    def test_ledger_terminal_rejects_conflicting_state(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            exec_rec, auth_rec = self._setup_verified_execution(ledger, "exec_conflict")

            term_completed = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=exec_rec.execution_id,
                authorization_id=exec_rec.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision=exec_rec.proposal_revision,
                target_project_uid=exec_rec.target_project_uid,
                target_timeline_uid=exec_rec.target_timeline_uid,
                action_payload_digest_sha256=auth_rec.action_payload_digest_sha256,
                recorded_at_iso="2026-11-04T12:02:00Z",
            )
            ledger.record_oracle_terminal_state(term_completed)

            term_aborted = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=exec_rec.execution_id,
                authorization_id=exec_rec.authorization_id,
                terminal_state="ABORTED",
                proposal_revision=exec_rec.proposal_revision,
                target_project_uid=exec_rec.target_project_uid,
                target_timeline_uid=exec_rec.target_timeline_uid,
                action_payload_digest_sha256=auth_rec.action_payload_digest_sha256,
                recorded_at_iso="2026-11-04T12:03:00Z",
            )
            with pytest.raises(SignerLedgerSecurityError, match=ERR_TERMINAL_STATE_CONFLICT):
                ledger.record_oracle_terminal_state(term_aborted)

    def test_ledger_terminal_recovery_all_states(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        states = ["COMPLETED", "ABORTED", "IN_DOUBT"]
        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            for s in states:
                exec_rec, auth_rec = self._setup_verified_execution(ledger, f"exec_{s.lower()}")
                rec = OracleExecutionTerminalRecord(
                    schema_version=1,
                    execution_id=exec_rec.execution_id,
                    authorization_id=exec_rec.authorization_id,
                    terminal_state=s,
                    proposal_revision=exec_rec.proposal_revision,
                    target_project_uid=exec_rec.target_project_uid,
                    target_timeline_uid=exec_rec.target_timeline_uid,
                    action_payload_digest_sha256=auth_rec.action_payload_digest_sha256,
                    recorded_at_iso="2026-11-04T12:02:00Z",
                )
                ledger.record_oracle_terminal_state(rec)

        # Restart and assert recovery
        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger2:
            for s in states:
                exec_id = f"exec_{s.lower()}"
                assert ledger2.is_execution_terminal(exec_id) is True
                term_rec = ledger2.get_oracle_terminal_state(exec_id)
                assert term_rec is not None
                assert term_rec.terminal_state == s

    def test_ledger_terminal_detects_torn_tail(self, tmp_path: Path):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            exec_rec, auth_rec = self._setup_verified_execution(ledger, "exec_torn")
            term_rec = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=exec_rec.execution_id,
                authorization_id=exec_rec.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision=exec_rec.proposal_revision,
                target_project_uid=exec_rec.target_project_uid,
                target_timeline_uid=exec_rec.target_timeline_uid,
                action_payload_digest_sha256=auth_rec.action_payload_digest_sha256,
                recorded_at_iso="2026-11-04T12:02:00Z",
            )
            ledger.record_oracle_terminal_state(term_rec)

        # Corrupt terminals file by truncating trailing newline
        term_file = ledger_dir / "oracle_execution_terminals.jsonl"
        data = term_file.read_bytes()
        term_file.write_bytes(data[:-1])  # remove trailing newline

        with pytest.raises(SignerLedgerCorruptionError, match="ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED"):
            DurableSignerLedger(ledger_dir, enforce_ownership=False)

    def test_ledger_terminal_poisoned_on_write_error(self, tmp_path: Path, monkeypatch):
        ledger_dir = tmp_path / "nonces"
        ledger_dir.mkdir(mode=0o700)
        os.chmod(ledger_dir, 0o700)

        with DurableSignerLedger(ledger_dir, enforce_ownership=False) as ledger:
            exec_rec, auth_rec = self._setup_verified_execution(ledger, "exec_fail")
            term_rec = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=exec_rec.execution_id,
                authorization_id=exec_rec.authorization_id,
                terminal_state="COMPLETED",
                proposal_revision=exec_rec.proposal_revision,
                target_project_uid=exec_rec.target_project_uid,
                target_timeline_uid=exec_rec.target_timeline_uid,
                action_payload_digest_sha256=auth_rec.action_payload_digest_sha256,
                recorded_at_iso="2026-11-04T12:02:00Z",
            )

            orig_write = os.write

            def fail_terminal_write(fd, payload):
                if fd == ledger._terminal_fd:
                    raise OSError("Simulated terminal write failure")
                return orig_write(fd, payload)

            monkeypatch.setattr(os, "write", fail_terminal_write)

            with pytest.raises(SignerLedgerDurabilityError):
                ledger.record_oracle_terminal_state(term_rec)

            assert ledger.is_poisoned is True
            monkeypatch.undo()

            with pytest.raises(SignerLedgerDurabilityError, match=ERR_LEDGER_POISONED):
                ledger.record_oracle_terminal_state(term_rec)
