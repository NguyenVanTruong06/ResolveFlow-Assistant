"""Durable Signer Nonce and Oracle Execution Ledger.

Descriptor-anchored append-only WAL ledgers for:
1. Consumed Authorization Nonces (consumed_nonces.jsonl)
2. Oracle Verified Executions (oracle_verified_executions.jsonl)

Enforces:
- Strict POSIX descriptor-anchored open (O_NOFOLLOW | O_DIRECTORY | O_CLOEXEC)
- Atomic single-use nonce consumption and multi-dimensional collision prevention
- Unique terminal execution ID tracking (re-verification rejection)
- Durability ordering: write record -> fsync(file_fd) -> fsync(dir_fd)
- Single-close semantics and fail-closed torn tail / corruption detection
"""

from __future__ import annotations

import datetime
import errno
import fcntl
import json
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from src.core.contracts.trial_authorization_contracts import canonical_json_bytes
from src.services.trial_authorization_signer_service import (
    ConsumedAuthorizationNonceRecord,
    SignerLedgerCorruptionError,
    SignerLedgerDurabilityError,
    SignerLedgerError,
    SignerLedgerSecurityError,
    SignerNonceAlreadyConsumedError,
    parse_iso_utc_timestamp,
)

DEFAULT_NONCES_DIR: str = "/var/lib/resolveflow/signer/nonces"
DEFAULT_CONSUMED_NONCES_FILE: str = "consumed_nonces.jsonl"
DEFAULT_ORACLE_EXECUTIONS_FILE: str = "oracle_verified_executions.jsonl"
DEFAULT_AUTH_IDENTITIES_FILE: str = "authorization_identities.jsonl"
DEFAULT_ORACLE_TERMINALS_FILE: str = "oracle_execution_terminals.jsonl"

PRODUCTION_SIGNER_UID: int = 901
PRODUCTION_SIGNER_GID: int = 901


# ------------------------------------------------------------------------------
# Authorization Identity Record DTO (DEP02B-R3-01)
# ------------------------------------------------------------------------------
AUTH_IDENTITY_RECORD_KEYS: frozenset[str] = frozenset({
    "action_payload_digest_sha256",
    "authorization_id",
    "director_key_id",
    "issued_at_iso",
    "operator_key_id",
    "proposal_revision",
    "schema_version",
})


@dataclass(frozen=True)
class AuthorizationIdentityRecord:
    """Immutable record durably binding an authorization ID to Director and Operator identities."""

    schema_version: int = 1
    authorization_id: str = ""
    director_key_id: str = ""
    operator_key_id: str = ""
    action_payload_digest_sha256: str = ""
    proposal_revision: str = ""
    issued_at_iso: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise SignerLedgerError(f"ERR_RECORD_FIELD_INVALID: schema_version must be int 1, got {self.schema_version!r}")
        for field in (
            "authorization_id",
            "director_key_id",
            "operator_key_id",
            "action_payload_digest_sha256",
            "proposal_revision",
            "issued_at_iso",
        ):
            val = getattr(self, field)
            if type(val) is not str or not val or val != val.strip():
                raise SignerLedgerError(f"ERR_RECORD_FIELD_INVALID: {field} must be a non-empty string without whitespace")
        parse_iso_utc_timestamp(self.issued_at_iso, "issued_at_iso")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_payload_digest_sha256": self.action_payload_digest_sha256,
            "authorization_id": self.authorization_id,
            "director_key_id": self.director_key_id,
            "issued_at_iso": self.issued_at_iso,
            "operator_key_id": self.operator_key_id,
            "proposal_revision": self.proposal_revision,
            "schema_version": self.schema_version,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AuthorizationIdentityRecord:
        if not isinstance(data, dict):
            raise SignerLedgerError(f"ERR_RECORD_SCHEMA_INVALID: expected dict, got {type(data).__name__}")
        if frozenset(data.keys()) != AUTH_IDENTITY_RECORD_KEYS:
            raise SignerLedgerError(
                f"ERR_RECORD_SCHEMA_INVALID: record keys must match {sorted(AUTH_IDENTITY_RECORD_KEYS)}"
            )
        return cls(
            schema_version=data["schema_version"],
            authorization_id=data["authorization_id"],
            director_key_id=data["director_key_id"],
            operator_key_id=data["operator_key_id"],
            action_payload_digest_sha256=data["action_payload_digest_sha256"],
            proposal_revision=data["proposal_revision"],
            issued_at_iso=data["issued_at_iso"],
        )


# ------------------------------------------------------------------------------
# Oracle Execution Record DTO
# ------------------------------------------------------------------------------
ORACLE_EXECUTION_RECORD_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "execution_id",
    "authorization_id",
    "proposal_revision",
    "target_project_uid",
    "target_timeline_uid",
    "verification_status",
    "verified_at_iso",
})


@dataclass(frozen=True)
class OracleExecutionRecord:
    """Immutable record tracking an Oracle-verified execution instance."""

    schema_version: int = 1
    execution_id: str = ""
    authorization_id: str = ""
    proposal_revision: str = ""
    target_project_uid: str = ""
    target_timeline_uid: str = ""
    verification_status: str = ""
    verified_at_iso: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise SignerLedgerError(f"ERR_RECORD_FIELD_INVALID: schema_version must be int 1, got {self.schema_version!r}")
        for field in (
            "execution_id",
            "authorization_id",
            "proposal_revision",
            "target_project_uid",
            "target_timeline_uid",
            "verification_status",
            "verified_at_iso",
        ):
            val = getattr(self, field)
            if type(val) is not str or not val or val != val.strip():
                raise SignerLedgerError(f"ERR_RECORD_FIELD_INVALID: {field} must be a non-empty string without whitespace")
        parse_iso_utc_timestamp(self.verified_at_iso, "verified_at_iso")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "authorization_id": self.authorization_id,
            "execution_id": self.execution_id,
            "proposal_revision": self.proposal_revision,
            "schema_version": self.schema_version,
            "target_project_uid": self.target_project_uid,
            "target_timeline_uid": self.target_timeline_uid,
            "verification_status": self.verification_status,
            "verified_at_iso": self.verified_at_iso,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OracleExecutionRecord:
        if not isinstance(data, dict):
            raise SignerLedgerError(f"ERR_RECORD_SCHEMA_INVALID: expected dict, got {type(data).__name__}")
        if frozenset(data.keys()) != ORACLE_EXECUTION_RECORD_KEYS:
            raise SignerLedgerError(
                f"ERR_RECORD_SCHEMA_INVALID: record keys must match {sorted(ORACLE_EXECUTION_RECORD_KEYS)}"
            )
        return cls(
            schema_version=data["schema_version"],
            execution_id=data["execution_id"],
            authorization_id=data["authorization_id"],
            proposal_revision=data["proposal_revision"],
            target_project_uid=data["target_project_uid"],
            target_timeline_uid=data["target_timeline_uid"],
            verification_status=data["verification_status"],
            verified_at_iso=data["verified_at_iso"],
        )


# ------------------------------------------------------------------------------
# Oracle Execution Terminal Record DTO
# ------------------------------------------------------------------------------
ORACLE_TERMINAL_RECORD_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "execution_id",
    "authorization_id",
    "terminal_state",
    "proposal_revision",
    "target_project_uid",
    "target_timeline_uid",
    "action_payload_digest_sha256",
    "recorded_at_iso",
})

VALID_LEDGER_TERMINAL_STATES: frozenset[str] = frozenset({
    "COMPLETED",
    "ABORTED",
    "IN_DOUBT",
})


@dataclass(frozen=True)
class OracleExecutionTerminalRecord:
    """Immutable record tracking an Oracle execution terminal transition."""

    schema_version: int = 1
    execution_id: str = ""
    authorization_id: str = ""
    terminal_state: str = ""
    proposal_revision: str = ""
    target_project_uid: str = ""
    target_timeline_uid: str = ""
    action_payload_digest_sha256: str = ""
    recorded_at_iso: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise SignerLedgerError(f"ERR_RECORD_FIELD_INVALID: schema_version must be int 1, got {self.schema_version!r}")
        for field in (
            "execution_id",
            "authorization_id",
            "terminal_state",
            "proposal_revision",
            "target_project_uid",
            "target_timeline_uid",
            "action_payload_digest_sha256",
            "recorded_at_iso",
        ):
            val = getattr(self, field)
            if type(val) is not str or not val or val != val.strip():
                raise SignerLedgerError(f"ERR_RECORD_FIELD_INVALID: {field} must be a non-empty string without whitespace")
        if self.terminal_state not in VALID_LEDGER_TERMINAL_STATES:
            raise SignerLedgerError(
                f"ERR_RECORD_FIELD_INVALID: terminal_state must be one of {sorted(VALID_LEDGER_TERMINAL_STATES)}, got {self.terminal_state!r}"
            )
        parse_iso_utc_timestamp(self.recorded_at_iso, "recorded_at_iso")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_payload_digest_sha256": self.action_payload_digest_sha256,
            "authorization_id": self.authorization_id,
            "execution_id": self.execution_id,
            "proposal_revision": self.proposal_revision,
            "recorded_at_iso": self.recorded_at_iso,
            "schema_version": self.schema_version,
            "target_project_uid": self.target_project_uid,
            "target_timeline_uid": self.target_timeline_uid,
            "terminal_state": self.terminal_state,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OracleExecutionTerminalRecord:
        if not isinstance(data, dict):
            raise SignerLedgerError(f"ERR_RECORD_SCHEMA_INVALID: expected dict, got {type(data).__name__}")
        if frozenset(data.keys()) != ORACLE_TERMINAL_RECORD_KEYS:
            raise SignerLedgerError(
                f"ERR_RECORD_SCHEMA_INVALID: record keys must match {sorted(ORACLE_TERMINAL_RECORD_KEYS)}"
            )
        return cls(
            schema_version=data["schema_version"],
            execution_id=data["execution_id"],
            authorization_id=data["authorization_id"],
            terminal_state=data["terminal_state"],
            proposal_revision=data["proposal_revision"],
            target_project_uid=data["target_project_uid"],
            target_timeline_uid=data["target_timeline_uid"],
            action_payload_digest_sha256=data["action_payload_digest_sha256"],
            recorded_at_iso=data["recorded_at_iso"],
        )


# ------------------------------------------------------------------------------
# Durable Ledger Implementation
# ------------------------------------------------------------------------------
class DurableSignerLedger:
    """Descriptor-anchored append-only WAL ledger for signer nonces and executions."""

    def __init__(
        self,
        ledger_dir: Optional[Union[str, Path]] = None,
        *,
        directory_fd: Optional[int] = None,
        consumed_nonces_filename: str = DEFAULT_CONSUMED_NONCES_FILE,
        oracle_executions_filename: str = DEFAULT_ORACLE_EXECUTIONS_FILE,
        auth_identities_filename: str = DEFAULT_AUTH_IDENTITIES_FILE,
        oracle_terminals_filename: str = DEFAULT_ORACLE_TERMINALS_FILE,
        expected_uid: int = PRODUCTION_SIGNER_UID,
        expected_gid: int = PRODUCTION_SIGNER_GID,
        enforce_ownership: bool = True,
    ) -> None:
        if ledger_dir is not None and directory_fd is not None:
            raise SignerLedgerSecurityError("ERR_AMBIGUOUS_DIRECTORY_INPUT: specify ledger_dir or directory_fd, not both")
        if ledger_dir is None and directory_fd is None:
            raise SignerLedgerError("ERR_MISSING_DIRECTORY_INPUT: directory or descriptor required")

        self._expected_uid = expected_uid
        self._expected_gid = expected_gid
        self._enforce_ownership = enforce_ownership
        self._consumed_filename = consumed_nonces_filename
        self._executions_filename = oracle_executions_filename
        self._auth_identities_filename = auth_identities_filename
        self._terminals_filename = oracle_terminals_filename
        self._ledger_dir: Optional[str] = str(ledger_dir) if ledger_dir is not None else None

        self._lock = threading.RLock()
        self._dir_fd: Optional[int] = None
        self._nonce_fd: Optional[int] = None
        self._exec_fd: Optional[int] = None
        self._auth_id_fd: Optional[int] = None
        self._terminal_fd: Optional[int] = None
        self._closed: bool = False
        self._poisoned: bool = False

        # In-memory indices
        self._challenge_ids: Set[str] = set()
        self._client_nonces: Set[str] = set()
        self._approval_pairs: Set[Tuple[str, str]] = set()
        self._verified_execution_ids: Set[str] = set()
        self._verified_executions: Dict[str, OracleExecutionRecord] = {}
        self._terminal_records: Dict[str, OracleExecutionTerminalRecord] = {}
        self._auth_identities: Dict[str, AuthorizationIdentityRecord] = {}

        try:
            if directory_fd is not None:
                if isinstance(directory_fd, bool) or not isinstance(directory_fd, int) or directory_fd < 0:
                    raise SignerLedgerSecurityError("ERR_INVALID_DIRECTORY_FD: must be a non-negative integer")
                self._dir_fd = os.dup(directory_fd)
                os.set_inheritable(self._dir_fd, False)
                fcntl.fcntl(self._dir_fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
                self._validate_dir(self._dir_fd)
            else:
                self._dir_fd = self._open_dir(ledger_dir)

            self._open_and_recover_ledgers()
        except Exception:
            self.close()
            raise

    def _open_dir(self, dir_path: Union[str, Path]) -> int:
        path_str = str(dir_path)
        try:
            fd = os.open(path_str, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as e:
            if e.errno == errno.ELOOP or (e.errno == errno.ENOTDIR and os.path.islink(path_str)):
                raise SignerLedgerSecurityError(f"ERR_SECURITY_VIOLATION: ledger directory is a symlink: {e}") from e
            raise SignerLedgerSecurityError(f"ERR_CANNOT_OPEN_LEDGER_DIR: failed to open '{path_str}': {e}") from e

        try:
            self._validate_dir(fd)
            return fd
        except Exception:
            os.close(fd)
            raise

    def _validate_dir(self, fd: int) -> None:
        st = os.fstat(fd)
        if stat.S_ISLNK(st.st_mode):
            raise SignerLedgerSecurityError("ERR_SECURITY_VIOLATION: ledger directory is a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise SignerLedgerSecurityError("ERR_SECURITY_VIOLATION: ledger path is not a directory")
        mode = stat.S_IMODE(st.st_mode)
        if mode != 0o700:
            raise SignerLedgerSecurityError(f"ERR_INVALID_MODE: ledger dir mode {oct(mode)}, expected 0700")
        if self._enforce_ownership:
            if st.st_uid != self._expected_uid:
                raise SignerLedgerSecurityError(f"ERR_INVALID_OWNER: ledger dir owned by UID {st.st_uid}, expected {self._expected_uid}")
            if st.st_gid != self._expected_gid:
                raise SignerLedgerSecurityError(f"ERR_INVALID_GROUP: ledger dir owned by GID {st.st_gid}, expected {self._expected_gid}")

    def _open_file(self, filename: str) -> int:
        assert self._dir_fd is not None
        try:
            fd = os.open(
                filename,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=self._dir_fd,
            )
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise SignerLedgerSecurityError(f"ERR_SECURITY_VIOLATION: file '{filename}' is a symlink: {e}") from e
            raise SignerLedgerSecurityError(f"ERR_CANNOT_OPEN_FILE: failed to open '{filename}': {e}") from e
        try:
            st = os.fstat(fd)
            if stat.S_ISLNK(st.st_mode):
                raise SignerLedgerSecurityError(f"ERR_SECURITY_VIOLATION: file '{filename}' is a symlink")
            if not stat.S_ISREG(st.st_mode):
                raise SignerLedgerSecurityError(f"ERR_SECURITY_VIOLATION: file '{filename}' is not a regular file")
            mode = stat.S_IMODE(st.st_mode)
            if mode != 0o600:
                raise SignerLedgerSecurityError(f"ERR_INVALID_MODE: file '{filename}' mode {oct(mode)}, expected 0600")
            if self._enforce_ownership:
                if st.st_uid != self._expected_uid:
                    raise SignerLedgerSecurityError(f"ERR_INVALID_OWNER: file '{filename}' owned by UID {st.st_uid}, expected {self._expected_uid}")
                if st.st_gid != self._expected_gid:
                    raise SignerLedgerSecurityError(f"ERR_INVALID_GROUP: file '{filename}' owned by GID {st.st_gid}, expected {self._expected_gid}")
            # Exclusive flock on file
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except Exception:
            os.close(fd)
            raise

    def _open_and_recover_ledgers(self) -> None:
        self._nonce_fd = self._open_file(self._consumed_filename)
        self._exec_fd = self._open_file(self._executions_filename)
        self._auth_id_fd = self._open_file(self._auth_identities_filename)
        self._terminal_fd = self._open_file(self._terminals_filename)

        self._recover_nonces()
        self._recover_executions()
        self._recover_auth_identities()
        self._recover_terminal_records()

    def _recover_nonces(self) -> None:
        assert self._nonce_fd is not None
        os.lseek(self._nonce_fd, 0, os.SEEK_SET)
        content = bytearray()
        while True:
            chunk = os.read(self._nonce_fd, 65536)
            if not chunk:
                break
            content.extend(chunk)

        if not content:
            return

        if content[-1:] != b"\n":
            raise SignerLedgerCorruptionError("ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: missing trailing newline")

        lines = bytes(content).split(b"\n")
        # last element after trailing \n is empty
        if lines and lines[-1] == b"":
            lines = lines[:-1]

        for i, line in enumerate(lines):
            if not line:
                raise SignerLedgerCorruptionError(f"ERR_CORRUPT_RECORD: empty line at index {i}")
            try:
                data = json.loads(line.decode("utf-8"))
            except Exception as e:
                raise SignerLedgerCorruptionError(f"ERR_JSON_PARSE_ERROR: line {i}: {e}") from e

            rec = ConsumedAuthorizationNonceRecord.from_dict(data)
            pair = (rec.approval_record_id, rec.proposal_revision)
            if rec.challenge_id in self._challenge_ids or rec.client_nonce in self._client_nonces or pair in self._approval_pairs:
                raise SignerNonceAlreadyConsumedError(f"ERR_LEDGER_RECOVERY_COLLISION: duplicate nonce record at line {i}")

            self._challenge_ids.add(rec.challenge_id)
            self._client_nonces.add(rec.client_nonce)
            self._approval_pairs.add(pair)

    def _recover_executions(self) -> None:
        assert self._exec_fd is not None
        os.lseek(self._exec_fd, 0, os.SEEK_SET)
        content = bytearray()
        while True:
            chunk = os.read(self._exec_fd, 65536)
            if not chunk:
                break
            content.extend(chunk)

        if not content:
            return

        if content[-1:] != b"\n":
            raise SignerLedgerCorruptionError("ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: missing trailing newline in executions ledger")

        lines = bytes(content).split(b"\n")
        if lines and lines[-1] == b"":
            lines = lines[:-1]

        for i, line in enumerate(lines):
            if not line:
                raise SignerLedgerCorruptionError(f"ERR_CORRUPT_RECORD: empty line at index {i}")
            try:
                data = json.loads(line.decode("utf-8"))
            except Exception as e:
                raise SignerLedgerCorruptionError(f"ERR_JSON_PARSE_ERROR: execution line {i}: {e}") from e

            rec = OracleExecutionRecord.from_dict(data)
            if rec.execution_id in self._verified_execution_ids:
                raise SignerLedgerSecurityError(f"ERR_LEDGER_RECOVERY_COLLISION: duplicate execution_id {rec.execution_id!r}")
            self._verified_execution_ids.add(rec.execution_id)
            self._verified_executions[rec.execution_id] = rec

    def _recover_auth_identities(self) -> None:
        assert self._auth_id_fd is not None
        os.lseek(self._auth_id_fd, 0, os.SEEK_SET)
        content = bytearray()
        while True:
            chunk = os.read(self._auth_id_fd, 65536)
            if not chunk:
                break
            content.extend(chunk)

        if not content:
            return

        if content[-1:] != b"\n":
            raise SignerLedgerCorruptionError("ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: missing trailing newline in auth identities ledger")

        lines = bytes(content).split(b"\n")
        if lines and lines[-1] == b"":
            lines = lines[:-1]

        for i, line in enumerate(lines):
            if not line:
                raise SignerLedgerCorruptionError(f"ERR_CORRUPT_RECORD: empty line at index {i}")
            try:
                data = json.loads(line.decode("utf-8"))
            except Exception as e:
                raise SignerLedgerCorruptionError(f"ERR_JSON_PARSE_ERROR: auth identity line {i}: {e}") from e

            rec = AuthorizationIdentityRecord.from_dict(data)
            if rec.authorization_id in self._auth_identities:
                raise SignerLedgerSecurityError(f"ERR_LEDGER_RECOVERY_COLLISION: duplicate authorization_id {rec.authorization_id!r}")
            self._auth_identities[rec.authorization_id] = rec

    def _recover_terminal_records(self) -> None:
        assert self._terminal_fd is not None
        os.lseek(self._terminal_fd, 0, os.SEEK_SET)
        content = bytearray()
        while True:
            chunk = os.read(self._terminal_fd, 65536)
            if not chunk:
                break
            content.extend(chunk)

        if not content:
            return

        if content[-1:] != b"\n":
            raise SignerLedgerCorruptionError(
                "ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: missing trailing newline in terminals ledger"
            )

        lines = bytes(content).split(b"\n")
        if lines and lines[-1] == b"":
            lines = lines[:-1]

        for i, line in enumerate(lines):
            if not line:
                raise SignerLedgerCorruptionError(f"ERR_CORRUPT_RECORD: empty line at index {i} in terminals ledger")
            try:
                data = json.loads(line.decode("utf-8"))
            except Exception as e:
                raise SignerLedgerCorruptionError(f"ERR_JSON_PARSE_ERROR: terminal line {i}: {e}") from e

            rec = OracleExecutionTerminalRecord.from_dict(data)
            if rec.execution_id in self._terminal_records:
                raise SignerLedgerSecurityError(
                    f"ERR_LEDGER_RECOVERY_COLLISION: duplicate terminal record for execution_id '{rec.execution_id}'"
                )

            # A terminal transition requires an existing verified execution record
            if rec.execution_id not in self._verified_executions:
                raise SignerLedgerCorruptionError(
                    f"ERR_ORACLE_TERMINAL_UNVERIFIED: terminal execution_id '{rec.execution_id}' has no verified execution record"
                )

            ver_rec = self._verified_executions[rec.execution_id]
            if (
                ver_rec.authorization_id != rec.authorization_id
                or ver_rec.proposal_revision != rec.proposal_revision
                or ver_rec.target_project_uid != rec.target_project_uid
                or ver_rec.target_timeline_uid != rec.target_timeline_uid
            ):
                raise SignerLedgerCorruptionError(
                    f"ERR_ORACLE_TERMINAL_IDENTITY_MISMATCH: terminal record does not match verified execution for '{rec.execution_id}'"
                )

            auth_id_rec = self._auth_identities.get(rec.authorization_id)
            if auth_id_rec is None:
                raise SignerLedgerCorruptionError(
                    f"ERR_ORACLE_TERMINAL_IDENTITY_MISMATCH: missing authorization identity for authorization_id '{rec.authorization_id}'"
                )
            if (
                auth_id_rec.action_payload_digest_sha256 != rec.action_payload_digest_sha256
                or auth_id_rec.proposal_revision != rec.proposal_revision
            ):
                raise SignerLedgerCorruptionError(
                    f"ERR_ORACLE_TERMINAL_IDENTITY_MISMATCH: terminal record does not match auth identity for '{rec.authorization_id}'"
                )

            self._terminal_records[rec.execution_id] = rec

    @property
    def is_poisoned(self) -> bool:
        with self._lock:
            return self._poisoned

    def record_consumed_nonce(self, record: ConsumedAuthorizationNonceRecord) -> None:
        """Atomically records consumed nonce with fsync durability.

        Raises:
            SignerNonceAlreadyConsumedError: if challenge_id, client_nonce, or (approval_record_id, proposal_revision) was already consumed.
            SignerLedgerDurabilityError: if ledger is poisoned or write/fsync fails.
        """
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned due to prior write/fsync failure")
            if self._closed or self._nonce_fd is None:
                raise SignerLedgerError("ERR_LEDGER_CLOSED")

            pair = (record.approval_record_id, record.proposal_revision)
            if record.challenge_id in self._challenge_ids:
                raise SignerNonceAlreadyConsumedError(f"ERR_NONCE_ALREADY_CONSUMED: challenge_id '{record.challenge_id}'")
            if record.client_nonce in self._client_nonces:
                raise SignerNonceAlreadyConsumedError(f"ERR_NONCE_ALREADY_CONSUMED: client_nonce '{record.client_nonce}'")
            if pair in self._approval_pairs:
                raise SignerNonceAlreadyConsumedError(
                    f"ERR_NONCE_ALREADY_CONSUMED: approval pair ('{pair[0]}', '{pair[1]}')"
                )

            payload_line = record.canonical_bytes() + b"\n"

            try:
                # Write-all loop
                written = 0
                while written < len(payload_line):
                    try:
                        n = os.write(self._nonce_fd, payload_line[written:])
                        if n <= 0:
                            raise SignerLedgerDurabilityError("ERR_SHORT_WRITE: os.write returned 0")
                        written += n
                    except InterruptedError:
                        continue
                    except OSError as e:
                        raise SignerLedgerDurabilityError(f"ERR_LEDGER_WRITE_FAILED: {e}") from e

                # fsync file then fsync dir
                try:
                    os.fsync(self._nonce_fd)
                    assert self._dir_fd is not None
                    os.fsync(self._dir_fd)
                except OSError as e:
                    raise SignerLedgerDurabilityError(f"ERR_LEDGER_FSYNC_FAILED: {e}") from e

                # Update in-memory indices only after durable fsync
                self._challenge_ids.add(record.challenge_id)
                self._client_nonces.add(record.client_nonce)
                self._approval_pairs.add(pair)
            except Exception:
                self._poisoned = True
                raise

    def record_oracle_execution(self, record: OracleExecutionRecord) -> None:
        """Atomically records Oracle-verified execution instance with fsync durability.

        Raises:
            SignerLedgerSecurityError: if execution_id was already verified.
            SignerLedgerDurabilityError: if ledger is poisoned or write/fsync fails.
        """
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned due to prior write/fsync failure")
            if self._closed or self._exec_fd is None:
                raise SignerLedgerError("ERR_LEDGER_CLOSED")

            if record.execution_id in self._verified_execution_ids:
                raise SignerLedgerSecurityError(
                    f"ERR_EXECUTION_ALREADY_VERIFIED: execution_id '{record.execution_id}' has already been verified"
                )

            payload_line = record.canonical_bytes() + b"\n"

            try:
                written = 0
                while written < len(payload_line):
                    try:
                        n = os.write(self._exec_fd, payload_line[written:])
                        if n <= 0:
                            raise SignerLedgerDurabilityError("ERR_SHORT_WRITE: os.write returned 0")
                        written += n
                    except InterruptedError:
                        continue
                    except OSError as e:
                        raise SignerLedgerDurabilityError(f"ERR_LEDGER_WRITE_FAILED: {e}") from e

                try:
                    os.fsync(self._exec_fd)
                    assert self._dir_fd is not None
                    os.fsync(self._dir_fd)
                except OSError as e:
                    raise SignerLedgerDurabilityError(f"ERR_LEDGER_FSYNC_FAILED: {e}") from e

                self._verified_execution_ids.add(record.execution_id)
                self._verified_executions[record.execution_id] = record
            except Exception:
                self._poisoned = True
                raise

    def record_oracle_terminal_state(self, record: OracleExecutionTerminalRecord) -> bool:
        """Atomically records Oracle execution terminal transition with fsync durability.

        Returns:
            True if newly recorded; False if already recorded with identical terminal_state (idempotent).

        Raises:
            SignerLedgerSecurityError: if execution is unverified, identity binding mismatches,
                or a conflicting terminal state was already recorded.
            SignerLedgerDurabilityError: if ledger is poisoned or write/fsync fails.
        """
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned due to prior write/fsync failure")
            if self._closed or self._terminal_fd is None:
                raise SignerLedgerError("ERR_LEDGER_CLOSED")

            if record.execution_id not in self._verified_executions:
                raise SignerLedgerSecurityError(
                    f"ERR_EXECUTION_NOT_FOUND: execution_id '{record.execution_id}' was never verified"
                )

            ver_rec = self._verified_executions[record.execution_id]
            if (
                ver_rec.authorization_id != record.authorization_id
                or ver_rec.proposal_revision != record.proposal_revision
                or ver_rec.target_project_uid != record.target_project_uid
                or ver_rec.target_timeline_uid != record.target_timeline_uid
            ):
                raise SignerLedgerSecurityError(
                    f"ERR_IDENTITY_BINDING_MISMATCH: terminal record does not match verified execution for '{record.execution_id}'"
                )

            auth_id_rec = self._auth_identities.get(record.authorization_id)
            if auth_id_rec is None:
                raise SignerLedgerSecurityError(
                    f"ERR_IDENTITY_BINDING_MISMATCH: missing authorization identity for authorization_id '{record.authorization_id}'"
                )
            if (
                auth_id_rec.action_payload_digest_sha256 != record.action_payload_digest_sha256
                or auth_id_rec.proposal_revision != record.proposal_revision
            ):
                raise SignerLedgerSecurityError(
                    f"ERR_IDENTITY_BINDING_MISMATCH: terminal record does not match auth identity for '{record.authorization_id}'"
                )

            if record.execution_id in self._terminal_records:
                existing = self._terminal_records[record.execution_id]
                if existing.terminal_state == record.terminal_state:
                    # Idempotent acknowledgement - do not append duplicate record
                    return False
                raise SignerLedgerSecurityError(
                    f"ERR_TERMINAL_STATE_CONFLICT: execution_id '{record.execution_id}' is already terminal ({existing.terminal_state}), cannot transition to '{record.terminal_state}'"
                )

            payload_line = record.canonical_bytes() + b"\n"

            try:
                written = 0
                while written < len(payload_line):
                    try:
                        n = os.write(self._terminal_fd, payload_line[written:])
                        if n <= 0:
                            raise SignerLedgerDurabilityError("ERR_SHORT_WRITE: os.write returned 0")
                        written += n
                    except InterruptedError:
                        continue
                    except OSError as e:
                        raise SignerLedgerDurabilityError(f"ERR_LEDGER_WRITE_FAILED: {e}") from e

                try:
                    os.fsync(self._terminal_fd)
                    assert self._dir_fd is not None
                    os.fsync(self._dir_fd)
                except OSError as e:
                    raise SignerLedgerDurabilityError(f"ERR_LEDGER_FSYNC_FAILED: {e}") from e

                self._terminal_records[record.execution_id] = record
                return True
            except Exception:
                self._poisoned = True
                raise

    def record_authorization_identity(self, record: AuthorizationIdentityRecord) -> None:
        """Atomically records authorization identity binding with fsync durability (DEP02B-R3-01).

        Raises:
            SignerLedgerSecurityError: if authorization_id was already recorded.
            SignerLedgerDurabilityError: if ledger is poisoned or write/fsync fails.
        """
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned due to prior write/fsync failure")
            if self._closed or self._auth_id_fd is None:
                raise SignerLedgerError("ERR_LEDGER_CLOSED")

            if record.authorization_id in self._auth_identities:
                raise SignerLedgerSecurityError(
                    f"ERR_AUTHORIZATION_ID_ALREADY_RECORDED: authorization_id '{record.authorization_id}' has already been recorded"
                )

            payload_line = record.canonical_bytes() + b"\n"

            try:
                written = 0
                while written < len(payload_line):
                    try:
                        n = os.write(self._auth_id_fd, payload_line[written:])
                        if n <= 0:
                            raise SignerLedgerDurabilityError("ERR_SHORT_WRITE: os.write returned 0")
                        written += n
                    except InterruptedError:
                        continue
                    except OSError as e:
                        raise SignerLedgerDurabilityError(f"ERR_LEDGER_WRITE_FAILED: {e}") from e

                try:
                    os.fsync(self._auth_id_fd)
                    assert self._dir_fd is not None
                    os.fsync(self._dir_fd)
                except OSError as e:
                    raise SignerLedgerDurabilityError(f"ERR_LEDGER_FSYNC_FAILED: {e}") from e

                self._auth_identities[record.authorization_id] = record
            except Exception:
                self._poisoned = True
                raise

    def get_authorization_identity(self, authorization_id: str) -> Optional[AuthorizationIdentityRecord]:
        """Retrieves durable identity binding for an authorization ID, or None if absent."""
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned")
            return self._auth_identities.get(authorization_id)

    def get_oracle_execution(self, execution_id: str) -> Optional[OracleExecutionRecord]:
        """Retrieves durable verified execution record by execution ID, or None if unverified."""
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned")
            return self._verified_executions.get(execution_id)

    def get_oracle_terminal_state(self, execution_id: str) -> Optional[OracleExecutionTerminalRecord]:
        """Retrieves durable terminal transition record by execution ID, or None if non-terminal."""
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned")
            return self._terminal_records.get(execution_id)

    def is_execution_terminal(self, execution_id: str) -> bool:
        """Checks if an execution ID has reached a durable terminal state."""
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned")
            return execution_id in self._terminal_records

    def is_execution_verified(self, execution_id: str) -> bool:
        """Checks if an execution ID has already reached terminal verification."""
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned")
            return execution_id in self._verified_execution_ids

    def is_nonce_consumed(self, challenge_id: str, client_nonce: str, approval_pair: Tuple[str, str]) -> bool:
        """Checks if any nonce coordinate has already been consumed."""
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned")
            return (
                challenge_id in self._challenge_ids
                or client_nonce in self._client_nonces
                or approval_pair in self._approval_pairs
            )

    def has_challenge_id(self, challenge_id: str) -> bool:
        """Checks if challenge_id has already been recorded in ledger."""
        with self._lock:
            if self._poisoned:
                raise SignerLedgerDurabilityError("ERR_LEDGER_POISONED: ledger is permanently poisoned")
            return challenge_id in self._challenge_ids

    def close(self) -> None:
        """Closes all open file descriptors cleanly."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

            if self._nonce_fd is not None:
                try:
                    os.close(self._nonce_fd)
                except OSError:
                    pass
                self._nonce_fd = None

            if self._exec_fd is not None:
                try:
                    os.close(self._exec_fd)
                except OSError:
                    pass
                self._exec_fd = None

            if self._auth_id_fd is not None:
                try:
                    os.close(self._auth_id_fd)
                except OSError:
                    pass
                self._auth_id_fd = None

            if self._terminal_fd is not None:
                try:
                    os.close(self._terminal_fd)
                except OSError:
                    pass
                self._terminal_fd = None

            if self._dir_fd is not None:
                try:
                    os.close(self._dir_fd)
                except OSError:
                    pass
                self._dir_fd = None

    @property
    def ledger_dir(self) -> Optional[str]:
        return self._ledger_dir

    def __enter__(self) -> DurableSignerLedger:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __repr__(self) -> str:
        status = "OPEN" if not self._closed else "CLOSED"
        return (
            f"<DurableSignerLedger status={status} nonces={len(self._challenge_ids)} "
            f"auths={len(self._auth_identities)} executions={len(self._verified_execution_ids)} "
            f"terminals={len(self._terminal_records)}>"
        )
