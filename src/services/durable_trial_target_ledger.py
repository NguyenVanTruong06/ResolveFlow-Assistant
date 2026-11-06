"""
src/services/durable_trial_target_ledger.py
Durable Target Ledger and Locking Recovery Coordinator for Isolated Resolve Gate (G07-07B6-I3).

Architectural Reference:
  - docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md (Section 2.3.1, 2.5, 2.6)
  - docs/architecture/07B6-mock-only-implementation-plan.md (Section 3.3, Slice I3)
  - Workorder: .bridge/task.md (G07-07B6-I3)

Safety Commitments:
  - Mock-only ledger/coordinator implementation.
  - Strict internal ownership of TrialKeyringAuthority; rejection of external keyring injection.
  - Advisory OS exclusive locks (fcntl.flock) and atomic publish (tmp -> fsync -> replace -> dir fsync).
  - Irreversible one-use target ledger state machine (NONE -> RESERVED -> ACTIVE -> EXHAUSTED).
  - Pre-admission gate blocking locked-out timelines before any os.open call.
  - Orderly lock release: flock(LOCK_UN) first, os.close(fd) second.
  - On unlock failure: retain live descriptor and kernel exclusion.
  - On close failure: latch CLOSE_OUTCOME_UNKNOWN, clear fd to None, never retry close on that numeric FD.
  - Bounded challenge TTL ([1.0, 300.0] seconds, strictly float/int, rejecting bool/nan/inf).
  - Challenge-bound operator reconciliation with cryptographic HMAC verification over accepted canonical bytes.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import math
import os
import re
import secrets
import stat
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    AuthorizationContract,
    MAX_JSON_BYTES,
    OperatorReconciliationAuthorization,
    ReadbackReceiptVerificationError,
    ReconciliationChallenge,
    StrictJsonComplexityError,
    StrictJsonParseError,
    TargetExclusionLockError,
    TargetLockReleaseError,
    TrialExecutionAuthorization,
    TrialExecutionReceipt,
    WholePlanReconciliationError,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
    parse_strict_canonical_json,
    validate_trial_execution_authorization,
)
from src.services.trial_keyring import OperatorKeyRecord, TrialKeyringAuthority

# --- Constants ---
LEDGER_SCHEMA_VERSION: int = 2
MIN_CHALLENGE_TTL_SECONDS: float = 1.0
MAX_CHALLENGE_TTL_SECONDS: float = 300.0
ALLOWED_LEDGER_STATES: frozenset[str] = frozenset({"RESERVED", "ACTIVE", "EXHAUSTED"})
RE_HEX_64: re.Pattern = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_LEDGER_KEYS: frozenset[str] = frozenset({
    "ledger_version",
    "target_project_uid",
    "target_timeline_uid",
    "authorization_id",
    "execution_id",
    "approval_id",
    "proposal_id",
    "proposal_revision",
    "plan_idempotency_key",
    "state",
    "created_at_iso",
    "updated_at_iso",
})

OPTIONAL_LEDGER_KEYS: frozenset[str] = frozenset({
    "terminal_receipt_id",
    "terminal_reason",
})

ALL_LEDGER_KEYS: frozenset[str] = REQUIRED_LEDGER_KEYS | OPTIONAL_LEDGER_KEYS


def get_ledger_filename(target_project_uid: str, target_timeline_uid: str) -> str:
    """Derives deterministic ledger filename from canonical target identity."""
    ident = f"{target_project_uid}:{target_timeline_uid}".encode("utf-8")
    h = hashlib.sha256(ident).hexdigest()
    return f"target_{h}.trial.json"


def get_lock_filename(timeline_uid: str) -> str:
    """Derives deterministic lock filename from timeline UID using SHA-256."""
    h = hashlib.sha256(timeline_uid.encode("utf-8")).hexdigest()
    return f"timeline_{h}.lock"


OPERATOR_RECONCILIATION_AUTH_SCHEMA_VERSION: str = "1.0"


def canonical_bytes_for_operator_reconciliation_auth(
    authorization: OperatorReconciliationAuthorization,
) -> bytes:
    """
    Computes deterministic, versioned, injective RFC 8785 canonical JSON bytes for
    OperatorReconciliationAuthorization claims, excluding signature_hmac (I3-R1-02).
    """
    if not isinstance(authorization, OperatorReconciliationAuthorization):
        raise WholePlanReconciliationError(
            "ERR_INVALID_AUTHORIZATION_OBJECT: authorization must be an instance of OperatorReconciliationAuthorization"
        )

    if type(authorization.timeline_uid) is not str or not authorization.timeline_uid.strip():
        raise WholePlanReconciliationError("ERR_INVALID_TIMELINE_UID: timeline_uid must be non-empty str")
    if type(authorization.incident_id) is not str or not authorization.incident_id.strip():
        raise WholePlanReconciliationError("ERR_INVALID_INCIDENT_ID: incident_id must be non-empty str")
    if type(authorization.challenge_nonce) is not str or not authorization.challenge_nonce.strip():
        raise WholePlanReconciliationError("ERR_INVALID_CHALLENGE_NONCE: challenge_nonce must be non-empty str")
    if (
        isinstance(authorization.challenge_expires_at, bool)
        or not isinstance(authorization.challenge_expires_at, (int, float))
        or not math.isfinite(authorization.challenge_expires_at)
    ):
        raise WholePlanReconciliationError("ERR_INVALID_CHALLENGE_EXPIRES_AT: challenge_expires_at must be a finite float")
    if type(authorization.operator_key_id) is not str or not authorization.operator_key_id.strip():
        raise WholePlanReconciliationError("ERR_INVALID_OPERATOR_KEY_ID: operator_key_id must be non-empty str")
    if type(authorization.operator_identity) is not str or not authorization.operator_identity.strip():
        raise WholePlanReconciliationError("ERR_INVALID_OPERATOR_IDENTITY: operator_identity must be non-empty str")
    if type(authorization.operator_confirmation_statement) is not str or not authorization.operator_confirmation_statement.strip():
        raise WholePlanReconciliationError("ERR_INVALID_CONFIRMATION_STATEMENT: operator_confirmation_statement must be non-empty str")
    if type(authorization.confirmed_unlocked) is not bool:
        raise WholePlanReconciliationError("ERR_INVALID_CONFIRMED_UNLOCKED: confirmed_unlocked must be bool")

    payload: Dict[str, Any] = {
        "auth_schema_version": OPERATOR_RECONCILIATION_AUTH_SCHEMA_VERSION,
        "challenge_expires_at": repr(float(authorization.challenge_expires_at)),
        "challenge_nonce": authorization.challenge_nonce,
        "confirmed_unlocked": authorization.confirmed_unlocked,
        "incident_id": authorization.incident_id,
        "operator_confirmation_statement": authorization.operator_confirmation_statement,
        "operator_identity": authorization.operator_identity,
        "operator_key_id": authorization.operator_key_id,
        "timeline_uid": authorization.timeline_uid,
    }
    return canonical_json_bytes(payload)


# --- Data Transfer Objects ---

@dataclass
class TargetLockEntry:
    """Records active or locked-out state for a target timeline exclusion lock."""
    fd: Optional[int]
    filename: str
    state: str  # 'LOCKED', 'RELEASING', 'RELEASED', 'IN_DOUBT_LOCKED', 'CLOSE_OUTCOME_UNKNOWN'
    dev: int
    ino: int
    incident_id: Optional[str] = None
    active_challenge_nonce: Optional[str] = None
    challenge_expires_at: Optional[float] = None


@dataclass(frozen=True)
class DurableTrialTargetLedger:
    """Immutable DTO representing the durable target ledger persisted on disk."""
    ledger_version: int
    target_project_uid: str
    target_timeline_uid: str
    authorization_id: str
    execution_id: str
    approval_id: str
    proposal_id: str
    proposal_revision: str
    plan_idempotency_key: str
    state: str
    created_at_iso: str
    updated_at_iso: str
    terminal_receipt_id: Optional[str] = None
    terminal_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if type(self.ledger_version) is not int or isinstance(self.ledger_version, bool):
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_LEDGER_VERSION: ledger_version must be int (bool rejected), got {type(self.ledger_version).__name__}"
            )
        if self.ledger_version == 1:
            raise ReadbackReceiptVerificationError(
                "ERR_LEGACY_LEDGER_VERSION: ledger_version 1 is no longer supported (fail-closed, requires version 2)"
            )
        if self.ledger_version != LEDGER_SCHEMA_VERSION:
            raise ReadbackReceiptVerificationError(
                f"ERR_UNSUPPORTED_LEDGER_VERSION: ledger_version must be int {LEDGER_SCHEMA_VERSION}, got {self.ledger_version}"
            )
        for k in (
            "target_project_uid",
            "target_timeline_uid",
            "authorization_id",
            "execution_id",
            "approval_id",
            "proposal_id",
            "plan_idempotency_key",
        ):
            val = getattr(self, k)
            if type(val) is not str or not val.strip():
                raise ReadbackReceiptVerificationError(f"ERR_INVALID_LEDGER_FIELD: {k} must be non-empty str")
            if "\0" in val:
                raise ReadbackReceiptVerificationError(f"ERR_INVALID_LEDGER_FIELD: {k} contains forbidden NUL byte")

        if type(self.proposal_revision) is not str or not RE_HEX_64.match(self.proposal_revision):
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_PROPOSAL_REVISION: proposal_revision must be 64-hex lowercase str, got {self.proposal_revision!r}"
            )

        if self.state not in ALLOWED_LEDGER_STATES:
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_LEDGER_STATE: state {self.state!r} not in {sorted(ALLOWED_LEDGER_STATES)}"
            )

        parse_iso_utc_timestamp(self.created_at_iso, "created_at_iso")
        parse_iso_utc_timestamp(self.updated_at_iso, "updated_at_iso")

        if self.state == "EXHAUSTED":
            if not self.terminal_receipt_id or type(self.terminal_receipt_id) is not str or not self.terminal_receipt_id.strip():
                raise ReadbackReceiptVerificationError(
                    "ERR_LEDGER_EXHAUSTED_MISSING_RECEIPT: state EXHAUSTED requires non-empty terminal_receipt_id"
                )
            if self.terminal_reason is not None and (type(self.terminal_reason) is not str or not self.terminal_reason.strip()):
                raise ReadbackReceiptVerificationError(
                    "ERR_INVALID_TERMINAL_REASON: terminal_reason must be non-empty str if present"
                )
        else:
            if self.terminal_receipt_id is not None:
                raise ReadbackReceiptVerificationError(
                    f"ERR_UNEXPECTED_TERMINAL_RECEIPT: terminal_receipt_id must be None when state is {self.state}"
                )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes ledger DTO to dictionary matching schema version 1."""
        d: Dict[str, Any] = {
            "ledger_version": self.ledger_version,
            "target_project_uid": self.target_project_uid,
            "target_timeline_uid": self.target_timeline_uid,
            "authorization_id": self.authorization_id,
            "execution_id": self.execution_id,
            "approval_id": self.approval_id,
            "proposal_id": self.proposal_id,
            "proposal_revision": self.proposal_revision,
            "plan_idempotency_key": self.plan_idempotency_key,
            "state": self.state,
            "created_at_iso": self.created_at_iso,
            "updated_at_iso": self.updated_at_iso,
        }
        if self.terminal_receipt_id is not None:
            d["terminal_receipt_id"] = self.terminal_receipt_id
        if self.terminal_reason is not None:
            d["terminal_reason"] = self.terminal_reason
        return d

    def to_json_bytes(self) -> bytes:
        """Serializes to deterministic RFC 8785 canonical JSON bytes."""
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DurableTrialTargetLedger:
        """Constructs and validates DTO from dictionary with strict schema checks."""
        if not isinstance(data, dict):
            raise ReadbackReceiptVerificationError("ERR_INVALID_LEDGER_PAYLOAD: root must be a JSON object")

        keys = set(data.keys())
        missing = REQUIRED_LEDGER_KEYS - keys
        if missing:
            raise ReadbackReceiptVerificationError(
                f"ERR_LEDGER_SCHEMA_MISSING_FIELD: missing required fields: {sorted(missing)}"
            )

        unknown = keys - ALL_LEDGER_KEYS
        if unknown:
            raise ReadbackReceiptVerificationError(
                f"ERR_LEDGER_SCHEMA_UNKNOWN_FIELD: unknown fields: {sorted(unknown)}"
            )

        raw_version = data.get("ledger_version")
        if type(raw_version) is not int or isinstance(raw_version, bool):
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_LEDGER_VERSION: ledger_version must be int (bool rejected), got {type(raw_version).__name__}"
            )
        if raw_version == 1:
            raise ReadbackReceiptVerificationError(
                "ERR_LEGACY_LEDGER_VERSION: ledger_version 1 is no longer supported (fail-closed, requires version 2)"
            )
        if raw_version != LEDGER_SCHEMA_VERSION:
            raise ReadbackReceiptVerificationError(
                f"ERR_UNSUPPORTED_LEDGER_VERSION: ledger_version must be int {LEDGER_SCHEMA_VERSION}, got {raw_version}"
            )

        raw_rev = data.get("proposal_revision")
        if type(raw_rev) is not str or not RE_HEX_64.match(raw_rev):
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_PROPOSAL_REVISION: proposal_revision must be 64-hex lowercase str, got {raw_rev!r}"
            )

        return cls(
            ledger_version=data["ledger_version"],
            target_project_uid=data["target_project_uid"],
            target_timeline_uid=data["target_timeline_uid"],
            authorization_id=data["authorization_id"],
            execution_id=data["execution_id"],
            approval_id=data["approval_id"],
            proposal_id=data["proposal_id"],
            proposal_revision=raw_rev,
            plan_idempotency_key=data["plan_idempotency_key"],
            state=data["state"],
            created_at_iso=data["created_at_iso"],
            updated_at_iso=data["updated_at_iso"],
            terminal_receipt_id=data.get("terminal_receipt_id"),
            terminal_reason=data.get("terminal_reason"),
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> DurableTrialTargetLedger:
        """Parses and validates canonical JSON bytes through two-layer parser."""
        data = parse_strict_canonical_json(raw_bytes, max_bytes=MAX_JSON_BYTES)
        return cls.from_dict(data)


class CoordinatorInitCleanupError(AcceptanceGateError):
    """Raised when coordinator initialization fails and subsequent cleanup encounters errors."""

    def __init__(
        self,
        message: str,
        unclosed_descriptors: Optional[Dict[str, int]] = None,
        unclosed_ledger: Optional[Any] = None,
        cause: Optional[BaseException] = None,
        cleanup_errors: Optional[List[str]] = None,
    ) -> None:
        super().__init__(message)
        self.unclosed_descriptors = unclosed_descriptors or {}
        self.unclosed_ledger = unclosed_ledger
        self.cause = cause
        self.cleanup_errors = cleanup_errors or []

    def retry_cleanup(self) -> List[str]:
        """Retries closing any retained unclosed descriptors or ledger."""
        errors: List[str] = []
        for name, fd in list(self.unclosed_descriptors.items()):
            try:
                os.close(fd)
                del self.unclosed_descriptors[name]
            except OSError as ce:
                errors.append(f"{name}: {ce}")
        if self.unclosed_ledger is not None:
            try:
                self.unclosed_ledger.close()
                self.unclosed_ledger = None
            except Exception as le:
                errors.append(f"_durable_ledger: {le}")
        return errors


# --- Durable Ledger Service ---

class DurableTrialTargetLedgerService:
    """
    Disk-backed durable ledger service managing one-use target state persistence.
    Provides atomic updates (temp -> fsync -> os.replace -> parent dir fsync)
    and advisory OS exclusion locks (fcntl.flock) for read/modify/write serialization.
    """

    def __init__(self, ledgers_dir: str) -> None:
        if not isinstance(ledgers_dir, str) or not ledgers_dir.strip():
            raise AcceptanceGateError("ERR_INVALID_LEDGERS_DIR: ledgers_dir must be a non-empty string")

        self._ledgers_dir = ledgers_dir
        self._lock = threading.RLock()

        dir_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            self._dir_fd: Optional[int] = os.open(ledgers_dir, dir_flags)
        except OSError as e:
            if e.errno == errno.ENOENT:
                raise AcceptanceGateError(f"ERR_LEDGERS_DIR_NOT_FOUND: {ledgers_dir}: {e}")
            raise AcceptanceGateError(f"ERR_LEDGERS_DIR_CANNOT_OPEN: {ledgers_dir}: {e}")

        st = os.fstat(self._dir_fd)
        if not stat.S_ISDIR(st.st_mode):
            os.close(self._dir_fd)
            self._dir_fd = None
            raise AcceptanceGateError(f"ERR_LEDGERS_DIR_NOT_DIRECTORY: {ledgers_dir}")

        self.recover_and_scan_ledger()

    @property
    def ledgers_dir(self) -> str:
        return self._ledgers_dir

    def recover_and_scan_ledger(self) -> Dict[str, DurableTrialTargetLedger]:
        """
        Scans ledgers directory, validates all existing ledger files,
        and cleans up any abandoned temporary files.
        """
        if self._dir_fd is None:
            raise AcceptanceGateError("ERR_LEDGER_SERVICE_CLOSED: directory descriptor is None")

        results: Dict[str, DurableTrialTargetLedger] = {}
        with self._lock:
            try:
                entries = sorted(os.listdir(self._dir_fd))
            except OSError as e:
                raise AcceptanceGateError(f"ERR_LEDGERS_DIR_CANNOT_LIST: {self._ledgers_dir}: {e}")

            file_flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )

            for fname in entries:
                if fname.startswith(".tmp_"):
                    # I3-04: Check if an active writer holds the target lock before unlinking
                    parts = fname[5:].split("_")
                    if parts and len(parts[0]) == 64:
                        ident_hash = parts[0]
                        lock_fname = f".target_{ident_hash}.ledger.lock"
                        lock_flags = (
                            os.O_RDWR
                            | os.O_CREAT
                            | getattr(os, "O_NOFOLLOW", 0)
                            | getattr(os, "O_CLOEXEC", 0)
                        )
                        try:
                            lfd = os.open(lock_fname, lock_flags, 0o600, dir_fd=self._dir_fd)
                        except OSError as e:
                            raise AcceptanceGateError(f"ERR_SCAN_LOCK_FAILED: {lock_fname}: {e}") from e

                        try:
                            try:
                                fcntl.flock(lfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except (OSError, IOError):
                                # An active writer currently holds the target lock! DO NOT unlink!
                                continue

                            # Lock acquired: no live writer holds this target, temp file is orphaned
                            try:
                                os.unlink(fname, dir_fd=self._dir_fd)
                            except OSError as ue:
                                if ue.errno != errno.ENOENT:
                                    raise AcceptanceGateError(
                                        f"ERR_TEMP_CLEANUP_FAILED: cannot unlink orphaned temp {fname}: {ue}"
                                    ) from ue
                            finally:
                                try:
                                    fcntl.flock(lfd, fcntl.LOCK_UN)
                                except OSError:
                                    pass
                        finally:
                            try:
                                os.close(lfd)
                            except OSError:
                                pass
                    continue

                if not fname.endswith(".trial.json"):
                    continue

                ffd: Optional[int] = None
                try:
                    ffd = os.open(fname, file_flags, dir_fd=self._dir_fd)
                    st = os.fstat(ffd)
                    if not stat.S_ISREG(st.st_mode):
                        raise ReadbackReceiptVerificationError(f"ERR_LEDGER_NOT_REGULAR: {fname}")
                    if st.st_size > MAX_JSON_BYTES:
                        raise ReadbackReceiptVerificationError(
                            f"ERR_LEDGER_FILE_TOO_LARGE: {fname} size {st.st_size} exceeds limit"
                        )
                    content = os.read(ffd, st.st_size)
                    ledger = DurableTrialTargetLedger.from_json_bytes(content)

                    # Validate filename matches derived target identity
                    expected_fname = get_ledger_filename(ledger.target_project_uid, ledger.target_timeline_uid)
                    if fname != expected_fname:
                        raise WholePlanReconciliationError(
                            f"ERR_LEDGER_FILENAME_IDENTITY_MISMATCH: file '{fname}' != expected '{expected_fname}'"
                        )
                    results[expected_fname] = ledger
                except (StrictJsonParseError, StrictJsonComplexityError, ReadbackReceiptVerificationError) as e:
                    # Malformed record cannot be treated as empty ledger
                    raise ReadbackReceiptVerificationError(f"ERR_CORRUPT_LEDGER_RECORD: {fname}: {e}") from e
                finally:
                    if ffd is not None:
                        try:
                            os.close(ffd)
                        except OSError:
                            pass
        return results

    def _lock_ledger(self, ident_hash: str) -> int:
        """Acquires exclusive OS advisory lock on the ledger lockfile."""
        if self._dir_fd is None:
            raise AcceptanceGateError("ERR_LEDGER_SERVICE_CLOSED")
        lock_fname = f".target_{ident_hash}.ledger.lock"
        lock_flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        lock_fd = os.open(lock_fname, lock_flags, 0o600, dir_fd=self._dir_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            return lock_fd
        except BaseException:
            try:
                os.close(lock_fd)
            except OSError:
                pass
            raise

    def _unlock_ledger(self, lock_fd: int) -> None:
        """Unlocks and closes the ledger lockfile."""
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(lock_fd)
            except OSError:
                pass

    def get_ledger(self, target_project_uid: str, target_timeline_uid: str) -> Optional[DurableTrialTargetLedger]:
        """Reads durable target ledger if present, validating schema and identity."""
        if self._dir_fd is None:
            raise AcceptanceGateError("ERR_LEDGER_SERVICE_CLOSED")

        ident = f"{target_project_uid}:{target_timeline_uid}".encode("utf-8")
        ident_hash = hashlib.sha256(ident).hexdigest()
        fname = f"target_{ident_hash}.trial.json"

        with self._lock:
            lock_fd = self._lock_ledger(ident_hash)
            try:
                file_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                try:
                    ffd = os.open(fname, file_flags, dir_fd=self._dir_fd)
                except OSError as e:
                    if e.errno == errno.ENOENT:
                        return None
                    raise WholePlanReconciliationError(f"ERR_LEDGER_CANNOT_OPEN: {fname}: {e}")

                try:
                    st = os.fstat(ffd)
                    if not stat.S_ISREG(st.st_mode):
                        raise ReadbackReceiptVerificationError(f"ERR_LEDGER_NOT_REGULAR: {fname}")
                    if st.st_size > MAX_JSON_BYTES:
                        raise ReadbackReceiptVerificationError(f"ERR_LEDGER_TOO_LARGE: {fname}")
                    content = os.read(ffd, st.st_size)
                    ledger = DurableTrialTargetLedger.from_json_bytes(content)
                    if (
                        ledger.target_project_uid != target_project_uid
                        or ledger.target_timeline_uid != target_timeline_uid
                    ):
                        raise WholePlanReconciliationError("ERR_LEDGER_IDENTITY_MISMATCH")
                    return ledger
                finally:
                    os.close(ffd)
            finally:
                self._unlock_ledger(lock_fd)

    def _write_ledger_atomic_unlocked(self, ledger: DurableTrialTargetLedger) -> None:
        """Publishes ledger atomically relative to the anchored directory descriptor (I3-02)."""
        if self._dir_fd is None:
            raise AcceptanceGateError("ERR_LEDGER_SERVICE_CLOSED")

        ident = f"{ledger.target_project_uid}:{ledger.target_timeline_uid}".encode("utf-8")
        ident_hash = hashlib.sha256(ident).hexdigest()
        final_fname = f"target_{ident_hash}.trial.json"
        tmp_fname = f".tmp_{ident_hash}_{secrets.token_hex(8)}.json"

        payload = ledger.to_json_bytes()

        tmp_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        tmp_fd = os.open(tmp_fname, tmp_flags, 0o600, dir_fd=self._dir_fd)
        try:
            written = 0
            while written < len(payload):
                n = os.write(tmp_fd, payload[written:])
                if n == 0:
                    raise OSError("ERR_ZERO_BYTES_WRITTEN: Complete write loop failed")
                written += n
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)

        # I3-02: Retain descriptor anchoring via src_dir_fd/dst_dir_fd across filesystem renames
        os.replace(
            tmp_fname,
            final_fname,
            src_dir_fd=self._dir_fd,
            dst_dir_fd=self._dir_fd,
        )
        os.fsync(self._dir_fd)

    def write_ledger_atomic(self, ledger: DurableTrialTargetLedger) -> None:
        """
        Public serialized atomic write. Acquires target lock, verifies irreversible state machine
        and identity matching, preventing rewinds from EXHAUSTED (I3-03).
        """
        if not isinstance(ledger, DurableTrialTargetLedger):
            raise ReadbackReceiptVerificationError("ERR_INVALID_LEDGER_OBJECT: ledger must be DurableTrialTargetLedger")

        ident = f"{ledger.target_project_uid}:{ledger.target_timeline_uid}".encode("utf-8")
        ident_hash = hashlib.sha256(ident).hexdigest()

        with self._lock:
            lock_fd = self._lock_ledger(ident_hash)
            try:
                existing = self._get_ledger_unlocked(
                    ledger.target_project_uid, ledger.target_timeline_uid, ident_hash
                )
                if existing is not None:
                    if existing.state == "EXHAUSTED":
                        raise WholePlanReconciliationError(
                            f"ERR_TARGET_ALREADY_EXHAUSTED: cannot overwrite terminal EXHAUSTED ledger for '{ledger.target_timeline_uid}'"
                        )
                    if (
                        existing.target_project_uid != ledger.target_project_uid
                        or existing.target_timeline_uid != ledger.target_timeline_uid
                    ):
                        raise WholePlanReconciliationError("ERR_LEDGER_IDENTITY_MISMATCH: target project or timeline UID mismatch")
                    if existing.execution_id != ledger.execution_id:
                        raise WholePlanReconciliationError(
                            f"ERR_TARGET_EXECUTION_MISMATCH: existing execution_id '{existing.execution_id}' != '{ledger.execution_id}'"
                        )
                    if existing.authorization_id != ledger.authorization_id:
                        raise WholePlanReconciliationError(
                            f"ERR_LEDGER_IDENTITY_MISMATCH: existing authorization_id '{existing.authorization_id}' != '{ledger.authorization_id}'"
                        )
                    if existing.approval_id != ledger.approval_id:
                        raise WholePlanReconciliationError(
                            f"ERR_LEDGER_IDENTITY_MISMATCH: existing approval_id '{existing.approval_id}' != '{ledger.approval_id}'"
                        )
                    if existing.proposal_id != ledger.proposal_id:
                        raise WholePlanReconciliationError(
                            f"ERR_LEDGER_IDENTITY_MISMATCH: existing proposal_id '{existing.proposal_id}' != '{ledger.proposal_id}'"
                        )
                    if existing.proposal_revision != ledger.proposal_revision:
                        raise WholePlanReconciliationError(
                            f"ERR_LEDGER_IDENTITY_MISMATCH: existing proposal_revision '{existing.proposal_revision}' != '{ledger.proposal_revision}'"
                        )
                    if existing.plan_idempotency_key != ledger.plan_idempotency_key:
                        raise WholePlanReconciliationError(
                            f"ERR_LEDGER_IDENTITY_MISMATCH: existing plan_idempotency_key '{existing.plan_idempotency_key}' != '{ledger.plan_idempotency_key}'"
                        )
                    if existing.created_at_iso != ledger.created_at_iso:
                        raise WholePlanReconciliationError(
                            f"ERR_LEDGER_IDENTITY_MISMATCH: existing created_at_iso '{existing.created_at_iso}' != '{ledger.created_at_iso}'"
                        )
                    if existing.state == "RESERVED" and ledger.state not in ("RESERVED", "ACTIVE", "EXHAUSTED"):
                        raise WholePlanReconciliationError(
                            f"ERR_INVALID_LEDGER_TRANSITION: cannot transition from RESERVED to '{ledger.state}'"
                        )
                    if existing.state == "ACTIVE" and ledger.state not in ("ACTIVE", "EXHAUSTED"):
                        raise WholePlanReconciliationError(
                            f"ERR_INVALID_LEDGER_TRANSITION: cannot rewind from ACTIVE to '{ledger.state}'"
                        )
                else:
                    if ledger.state != "RESERVED":
                        raise WholePlanReconciliationError(
                            f"ERR_INVALID_LEDGER_TRANSITION: initial ledger state must be RESERVED, got '{ledger.state}'"
                        )

                self._write_ledger_atomic_unlocked(ledger)
            finally:
                self._unlock_ledger(lock_fd)

    def reserve_target(
        self,
        target_project_uid: str,
        target_timeline_uid: str,
        authorization_id: str,
        execution_id: str,
        approval_id: str,
        proposal_id: str,
        proposal_revision: str,
        plan_idempotency_key: str,
    ) -> DurableTrialTargetLedger:
        """Atomically transitions ledger from NONE -> RESERVED."""
        ident = f"{target_project_uid}:{target_timeline_uid}".encode("utf-8")
        ident_hash = hashlib.sha256(ident).hexdigest()

        with self._lock:
            lock_fd = self._lock_ledger(ident_hash)
            try:
                existing = self._get_ledger_unlocked(target_project_uid, target_timeline_uid, ident_hash)
                if existing is not None:
                    if existing.state == "EXHAUSTED":
                        raise WholePlanReconciliationError(
                            f"ERR_TARGET_LEDGER_EXHAUSTED: target '{target_timeline_uid}' is permanently exhausted"
                        )
                    raise WholePlanReconciliationError(
                        f"ERR_TARGET_ALREADY_RESERVED: target '{target_timeline_uid}' already exists in state '{existing.state}'"
                    )

                now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                new_ledger = DurableTrialTargetLedger(
                    ledger_version=LEDGER_SCHEMA_VERSION,
                    target_project_uid=target_project_uid,
                    target_timeline_uid=target_timeline_uid,
                    authorization_id=authorization_id,
                    execution_id=execution_id,
                    approval_id=approval_id,
                    proposal_id=proposal_id,
                    proposal_revision=proposal_revision,
                    plan_idempotency_key=plan_idempotency_key,
                    state="RESERVED",
                    created_at_iso=now_iso,
                    updated_at_iso=now_iso,
                )
                self._write_ledger_atomic_unlocked(new_ledger)
                return new_ledger
            finally:
                self._unlock_ledger(lock_fd)

    def activate_target(
        self,
        target_project_uid: str,
        target_timeline_uid: str,
        execution_id: str,
    ) -> DurableTrialTargetLedger:
        """Atomically transitions ledger from RESERVED -> ACTIVE."""
        ident = f"{target_project_uid}:{target_timeline_uid}".encode("utf-8")
        ident_hash = hashlib.sha256(ident).hexdigest()

        with self._lock:
            lock_fd = self._lock_ledger(ident_hash)
            try:
                existing = self._get_ledger_unlocked(target_project_uid, target_timeline_uid, ident_hash)
                if existing is None:
                    raise WholePlanReconciliationError(
                        f"ERR_TARGET_LEDGER_NOT_FOUND: target '{target_timeline_uid}' must be reserved before activation"
                    )
                if existing.state == "EXHAUSTED":
                    raise WholePlanReconciliationError(
                        f"ERR_TARGET_LEDGER_EXHAUSTED: target '{target_timeline_uid}' is permanently exhausted"
                    )
                if existing.state == "ACTIVE":
                    if existing.execution_id == execution_id:
                        return existing
                    raise WholePlanReconciliationError(
                        f"ERR_TARGET_EXECUTION_MISMATCH: active target bound to '{existing.execution_id}', got '{execution_id}'"
                    )
                if existing.state != "RESERVED":
                    raise WholePlanReconciliationError(
                        f"ERR_INVALID_LEDGER_TRANSITION: cannot activate target from state '{existing.state}'"
                    )
                if existing.execution_id != execution_id:
                    raise WholePlanReconciliationError(
                        f"ERR_TARGET_EXECUTION_MISMATCH: reserved target bound to '{existing.execution_id}', got '{execution_id}'"
                    )

                now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                updated = DurableTrialTargetLedger(
                    ledger_version=existing.ledger_version,
                    target_project_uid=existing.target_project_uid,
                    target_timeline_uid=existing.target_timeline_uid,
                    authorization_id=existing.authorization_id,
                    execution_id=existing.execution_id,
                    approval_id=existing.approval_id,
                    proposal_id=existing.proposal_id,
                    proposal_revision=existing.proposal_revision,
                    plan_idempotency_key=existing.plan_idempotency_key,
                    state="ACTIVE",
                    created_at_iso=existing.created_at_iso,
                    updated_at_iso=now_iso,
                )
                self._write_ledger_atomic_unlocked(updated)
                return updated
            finally:
                self._unlock_ledger(lock_fd)

    def exhaust_target(
        self,
        target_project_uid: str,
        target_timeline_uid: str,
        execution_id: str,
        terminal_receipt_id: str,
        terminal_reason: str = "COMPLETED",
    ) -> DurableTrialTargetLedger:
        """Atomically transitions ledger to terminal EXHAUSTED state."""
        if not isinstance(terminal_receipt_id, str) or not terminal_receipt_id.strip():
            raise ReadbackReceiptVerificationError(
                "ERR_LEDGER_EXHAUSTED_MISSING_RECEIPT: terminal_receipt_id must be non-empty str"
            )

        ident = f"{target_project_uid}:{target_timeline_uid}".encode("utf-8")
        ident_hash = hashlib.sha256(ident).hexdigest()

        with self._lock:
            lock_fd = self._lock_ledger(ident_hash)
            try:
                existing = self._get_ledger_unlocked(target_project_uid, target_timeline_uid, ident_hash)
                if existing is None:
                    raise WholePlanReconciliationError(
                        f"ERR_TARGET_LEDGER_NOT_FOUND: target '{target_timeline_uid}' not found in ledger"
                    )
                if existing.state == "EXHAUSTED":
                    raise WholePlanReconciliationError(
                        f"ERR_TARGET_ALREADY_EXHAUSTED: target '{target_timeline_uid}' is already permanently exhausted"
                    )
                if existing.state not in ("RESERVED", "ACTIVE"):
                    raise WholePlanReconciliationError(
                        f"ERR_INVALID_LEDGER_TRANSITION: cannot exhaust target from state '{existing.state}'"
                    )
                if existing.execution_id != execution_id:
                    raise WholePlanReconciliationError(
                        f"ERR_TARGET_EXECUTION_MISMATCH: target bound to '{existing.execution_id}', got '{execution_id}'"
                    )

                now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                updated = DurableTrialTargetLedger(
                    ledger_version=existing.ledger_version,
                    target_project_uid=existing.target_project_uid,
                    target_timeline_uid=existing.target_timeline_uid,
                    authorization_id=existing.authorization_id,
                    execution_id=existing.execution_id,
                    approval_id=existing.approval_id,
                    proposal_id=existing.proposal_id,
                    proposal_revision=existing.proposal_revision,
                    plan_idempotency_key=existing.plan_idempotency_key,
                    state="EXHAUSTED",
                    created_at_iso=existing.created_at_iso,
                    updated_at_iso=now_iso,
                    terminal_receipt_id=terminal_receipt_id,
                    terminal_reason=terminal_reason,
                )
                self._write_ledger_atomic_unlocked(updated)
                return updated
            finally:
                self._unlock_ledger(lock_fd)

    def _get_ledger_unlocked(
        self, target_project_uid: str, target_timeline_uid: str, ident_hash: str
    ) -> Optional[DurableTrialTargetLedger]:
        """Internal read when lockfile is already held."""
        fname = f"target_{ident_hash}.trial.json"
        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            ffd = os.open(fname, file_flags, dir_fd=self._dir_fd)
        except OSError as e:
            if e.errno == errno.ENOENT:
                return None
            raise WholePlanReconciliationError(f"ERR_LEDGER_CANNOT_OPEN: {fname}: {e}")

        try:
            st = os.fstat(ffd)
            if not stat.S_ISREG(st.st_mode):
                raise ReadbackReceiptVerificationError(f"ERR_LEDGER_NOT_REGULAR: {fname}")
            if st.st_size > MAX_JSON_BYTES:
                raise ReadbackReceiptVerificationError(f"ERR_LEDGER_TOO_LARGE: {fname}")
            content = os.read(ffd, st.st_size)
            ledger = DurableTrialTargetLedger.from_json_bytes(content)
            if (
                ledger.target_project_uid != target_project_uid
                or ledger.target_timeline_uid != target_timeline_uid
            ):
                raise WholePlanReconciliationError("ERR_LEDGER_IDENTITY_MISMATCH")
            return ledger
        finally:
            os.close(ffd)

    def close(self) -> None:
        """Closes directory descriptor safely."""
        with self._lock:
            if self._dir_fd is not None:
                try:
                    os.close(self._dir_fd)
                finally:
                    self._dir_fd = None


# --- Recovery Coordinator ---

class RecoveryCoordinator:
    """
    Coordinates target exclusion locks, internal keyring authority ownership,
    signed trial authorization binding, challenge-bound operator reconciliation,
    and durable target ledger transitions.
    """

    MIN_CHALLENGE_TTL_SECONDS: float = MIN_CHALLENGE_TTL_SECONDS
    MAX_CHALLENGE_TTL_SECONDS: float = MAX_CHALLENGE_TTL_SECONDS

    def __init__(
        self,
        journals_dir: str,
        receipts_dir: str,
        locks_dir: str,
        ledgers_dir: str,
        keystore_path: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        # B6-PLAN-01 / I3-G7: Enforce strict internal keyring ownership; forbid caller injections
        if args or kwargs or "keyring" in kwargs:
            raise AcceptanceGateError(
                "ERR_KEYRING_INJECTION_FORBIDDEN: caller cannot supply keyring instance; "
                "must be owned internally via keystore_path"
            )

        self._journals_dir = journals_dir
        self._receipts_dir = receipts_dir
        self._locks_dir = locks_dir
        self._ledgers_dir = ledgers_dir
        self._keystore_path = keystore_path

        self._journals_dir_fd: Optional[int] = None
        self._receipts_dir_fd: Optional[int] = None
        self._locks_dir_fd: Optional[int] = None
        self._durable_ledger: Optional[DurableTrialTargetLedgerService] = None

        self._coordinator_lock = threading.RLock()
        self._active_operation_threads: Dict[int, int] = {}
        self._target_locks: Dict[str, TargetLockEntry] = {}
        self._lock_state: Dict[str, str] = {}
        self._bound_auth: Optional[AuthorizationContract] = None

        self._closed = False
        self._closing = False
        self._close_failed = False

        # Load keyring authority internally
        self._keyring: TrialKeyringAuthority = TrialKeyringAuthority(keystore_path)

        dir_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )

        try:
            self._journals_dir_fd = os.open(journals_dir, dir_flags)
            self._receipts_dir_fd = os.open(receipts_dir, dir_flags)
            self._locks_dir_fd = os.open(locks_dir, dir_flags)
            self._durable_ledger = DurableTrialTargetLedgerService(ledgers_dir)
        except Exception as init_err:
            unclosed_descriptors: Dict[str, int] = {}
            cleanup_errors: List[str] = []

            for fd_attr in ("_journals_dir_fd", "_receipts_dir_fd", "_locks_dir_fd"):
                fd = getattr(self, fd_attr)
                if fd is not None:
                    try:
                        os.close(fd)
                        setattr(self, fd_attr, None)
                    except OSError as ce:
                        cleanup_errors.append(f"{fd_attr}: {ce}")
                        unclosed_descriptors[fd_attr] = fd

            if self._durable_ledger is not None:
                try:
                    self._durable_ledger.close()
                    self._durable_ledger = None
                except Exception as le:
                    cleanup_errors.append(f"_durable_ledger: {le}")

            self._closed = True
            if cleanup_errors:
                raise CoordinatorInitCleanupError(
                    f"ERR_COORDINATOR_INIT_FAILED_WITH_CLEANUP_ERRORS: {init_err}; cleanup errors: {'; '.join(cleanup_errors)}",
                    unclosed_descriptors=unclosed_descriptors,
                    unclosed_ledger=self._durable_ledger,
                    cause=init_err,
                    cleanup_errors=cleanup_errors,
                ) from init_err

            if isinstance(init_err, AcceptanceGateError):
                raise
            raise WholePlanReconciliationError(f"ERR_COORDINATOR_INIT_FAILED: {init_err}") from init_err

    @property
    def keyring(self) -> TrialKeyringAuthority:
        """Returns the internally owned keyring authority."""
        return self._keyring

    @property
    def durable_ledger(self) -> Optional[DurableTrialTargetLedgerService]:
        """Returns the durable target ledger service."""
        return self._durable_ledger

    @property
    def bound_auth(self) -> Optional[AuthorizationContract]:
        """Returns the currently bound authorization contract if any."""
        return self._bound_auth

    @property
    def is_closed(self) -> bool:
        """Returns True if coordinator is closed."""
        return self._closed

    def _validate_timeline_uid(self, timeline_uid: str) -> None:
        """Validates timeline UID before path or lock derivation."""
        if not isinstance(timeline_uid, str) or not timeline_uid.strip():
            raise TargetExclusionLockError("ERR_INVALID_TIMELINE_UID: timeline_uid must be non-empty string")
        if "\0" in timeline_uid:
            raise TargetExclusionLockError("ERR_INVALID_TIMELINE_UID: timeline_uid contains forbidden NUL byte")

    def _check_not_closed(self) -> None:
        """Universal operation gate verifying coordinator is active and descriptors are anchored."""
        if self._closed or self._closing or self._close_failed:
            raise WholePlanReconciliationError(
                "ERR_COORDINATOR_CLOSED_OR_CLOSING: coordinator instance is closed, closing, or in failed-close state"
            )
        if (
            self._locks_dir_fd is None
            or self._journals_dir_fd is None
            or self._receipts_dir_fd is None
        ):
            raise WholePlanReconciliationError("ERR_DIRECTORY_DESCRIPTOR_UNANCHORED: directory descriptor is None")

    def acquire_target_lock(self, timeline_uid: str) -> TargetLockEntry:
        """
        Public gated acquisition of target exclusion lock.
        Enforces pre-admission checks under coordinator lock BEFORE any os.open call.
        Acquires exclusive OS advisory lock (fcntl.flock) and records stable inode identity.
        """
        self._validate_timeline_uid(timeline_uid)
        self._check_not_closed()

        with self._coordinator_lock:
            self._check_not_closed()
            tid = threading.get_ident()
            self._active_operation_threads[tid] = self._active_operation_threads.get(tid, 0) + 1
            try:
                # Pre-admission gate (I3-G1): Strictly reject locked-out states with ZERO descriptor touches
                existing_entry = self._target_locks.get(timeline_uid)
                existing_state = self._lock_state.get(timeline_uid)
                if existing_entry is not None or existing_state is not None:
                    curr_state = existing_entry.state if existing_entry else existing_state
                    if curr_state == "CLOSE_OUTCOME_UNKNOWN":
                        raise TargetExclusionLockError(
                            f"ERR_TIMELINE_LOCKED_OUT_AMBIGUOUS_CLOSE: timeline '{timeline_uid}' is in terminal "
                            f"CLOSE_OUTCOME_UNKNOWN state; fresh lock acquisition strictly blocked until explicit safe reconciliation"
                        )
                    if curr_state == "IN_DOUBT_LOCKED":
                        raise TargetExclusionLockError(
                            f"ERR_TIMELINE_LOCKED_OUT_IN_DOUBT: timeline '{timeline_uid}' has unresolved in-doubt "
                            f"target lock; fresh lock acquisition blocked"
                        )
                    if curr_state == "LOCKED":
                        raise TargetExclusionLockError(
                            f"ERR_TARGET_ALREADY_LOCKED: timeline '{timeline_uid}' is already locked by this coordinator instance"
                        )
                    if curr_state not in ("RELEASED", None):
                        raise TargetExclusionLockError(
                            f"ERR_TARGET_UNRESOLVED_STATE: timeline '{timeline_uid}' is in unresolved state '{curr_state}'; "
                            f"fresh lock acquisition blocked"
                        )

                lock_name = get_lock_filename(timeline_uid)
                lock_flags = (
                    os.O_RDWR
                    | os.O_CREAT
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )

                fd = os.open(lock_name, lock_flags, 0o600, dir_fd=self._locks_dir_fd)
                try:
                    st = os.fstat(fd)
                    if not stat.S_ISREG(st.st_mode):
                        raise TargetExclusionLockError(f"ERR_LOCKFILE_NOT_REGULAR: {lock_name} is not a regular file")
                    if st.st_uid != os.getuid():
                        raise TargetExclusionLockError(
                            f"ERR_LOCKFILE_INVALID_OWNER: {lock_name} uid {st.st_uid} != {os.getuid()}"
                        )
                    # I3-06: Strict mode check: lockfile must be 0600 (no group, world, or exec permissions)
                    if (st.st_mode & 0o077) != 0 or (st.st_mode & 0o111) != 0:
                        raise TargetExclusionLockError(
                            f"ERR_LOCKFILE_INSECURE_PERMS: {lock_name} mode must be 0600, got {oct(st.st_mode)}"
                        )

                    # OS-level contention check via fcntl.flock (I3-G2)
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except (OSError, IOError) as e:
                        raise TargetExclusionLockError(
                            f"ERR_TARGET_LOCKED: timeline '{timeline_uid}' already locked by another coordinator: {e}"
                        ) from e

                    entry = TargetLockEntry(
                        fd=fd,
                        filename=lock_name,
                        state="LOCKED",
                        dev=st.st_dev,
                        ino=st.st_ino,
                        incident_id=None,
                        active_challenge_nonce=None,
                        challenge_expires_at=None,
                    )
                    self._target_locks[timeline_uid] = entry
                    self._lock_state[timeline_uid] = "LOCKED"
                    return entry

                except BaseException:
                    if timeline_uid not in self._target_locks:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                    raise

            finally:
                self._active_operation_threads[tid] -= 1
                if self._active_operation_threads[tid] == 0:
                    del self._active_operation_threads[tid]

    def release_target_lock(self, timeline_uid: str) -> None:
        """
        Public gated release of target exclusion lock.
        Strict release order: flock(LOCK_UN) first, os.close(fd) second.
        On flock unlock failure: preserves live descriptor and kernel exclusion (I3-G3).
        On close failure: latches CLOSE_OUTCOME_UNKNOWN, clears fd to None, never retries close (I3-G4).
        """
        self._validate_timeline_uid(timeline_uid)
        self._check_not_closed()

        with self._coordinator_lock:
            self._check_not_closed()
            tid = threading.get_ident()
            self._active_operation_threads[tid] = self._active_operation_threads.get(tid, 0) + 1
            try:
                if timeline_uid not in self._target_locks:
                    if self._lock_state.get(timeline_uid) in (None, "RELEASED"):
                        return
                    raise TargetLockReleaseError(f"ERR_TIMELINE_NOT_LOCKED: timeline '{timeline_uid}' is not locked")

                entry = self._target_locks[timeline_uid]

                # Ambiguous close state must not be closed again (I3-G4)
                if entry.state == "CLOSE_OUTCOME_UNKNOWN":
                    raise TargetLockReleaseError(
                        f"ERR_CLOSE_OUTCOME_UNKNOWN: timeline '{timeline_uid}' close previously failed; "
                        f"descriptor relinquished to prevent cross-coordinator collisions"
                    )

                # Step 1: flock(LOCK_UN)
                if entry.state in ("LOCKED", "IN_DOUBT_LOCKED"):
                    entry.state = "RELEASING"
                    try:
                        assert entry.fd is not None, "fd must not be None when in LOCKED/IN_DOUBT_LOCKED state"
                        fcntl.flock(entry.fd, fcntl.LOCK_UN)
                    except OSError as e:
                        # I3-G3: Do not call os.close(fd)! Retain live descriptor and kernel lock
                        entry.state = "IN_DOUBT_LOCKED"
                        self._lock_state[timeline_uid] = "IN_DOUBT_LOCKED"
                        raise TargetLockReleaseError(f"ERR_TARGET_LOCK_UNLOCK_FAILED: {timeline_uid}: {e}") from e

                # Step 2: Attempt os.close() exactly once (I3-G4)
                failed_fd = entry.fd
                try:
                    assert entry.fd is not None, "fd must not be None before close attempt"
                    os.close(entry.fd)
                except OSError as e:
                    # Ambiguous close: single-close latch, clear entry.fd = None, generate incident_id
                    entry.state = "CLOSE_OUTCOME_UNKNOWN"
                    self._lock_state[timeline_uid] = "CLOSE_OUTCOME_UNKNOWN"
                    entry.fd = None
                    entry.incident_id = secrets.token_hex(16)
                    entry.active_challenge_nonce = None
                    entry.challenge_expires_at = None
                    raise TargetLockReleaseError(
                        f"ERR_TARGET_LOCK_CLOSE_FAILED: timeline '{timeline_uid}' fd={failed_fd}: {e}; "
                        f"transitioned to CLOSE_OUTCOME_UNKNOWN (incident={entry.incident_id})"
                    ) from e

                # Step 3: Success
                entry.state = "RELEASED"
                entry.fd = None
                self._lock_state[timeline_uid] = "RELEASED"
                del self._target_locks[timeline_uid]

            finally:
                self._active_operation_threads[tid] -= 1
                if self._active_operation_threads[tid] == 0:
                    del self._active_operation_threads[tid]

    def has_target_lock(self, timeline_uid: str) -> bool:
        """Returns True if this coordinator instance holds an active target lock on timeline."""
        self._validate_timeline_uid(timeline_uid)
        self._check_not_closed()
        with self._coordinator_lock:
            entry = self._target_locks.get(timeline_uid)
            return entry is not None and entry.state == "LOCKED" and entry.fd is not None

    def mark_timeline_in_doubt(self, timeline_uid: str, incident_id: str) -> None:
        """Transitions target lock into IN_DOUBT_LOCKED state."""
        self._validate_timeline_uid(timeline_uid)
        if not isinstance(incident_id, str) or not incident_id.strip():
            raise WholePlanReconciliationError("ERR_INVALID_INCIDENT_ID: incident_id must be non-empty str")
        self._check_not_closed()
        with self._coordinator_lock:
            if timeline_uid in self._target_locks:
                entry = self._target_locks[timeline_uid]
                entry.state = "IN_DOUBT_LOCKED"
                entry.incident_id = incident_id
            self._lock_state[timeline_uid] = "IN_DOUBT_LOCKED"

    def request_reconciliation_challenge(
        self, timeline_uid: str, ttl_seconds: float = 60.0
    ) -> ReconciliationChallenge:
        """
        Issues a cryptographically random single-use reconciliation challenge with bounded TTL.
        Rejects bool, non-finite, out-of-range TTLs without modifying active challenge state (I3-G5).
        """
        self._validate_timeline_uid(timeline_uid)
        self._check_not_closed()

        with self._coordinator_lock:
            self._check_not_closed()
            if timeline_uid not in self._target_locks:
                raise WholePlanReconciliationError(f"ERR_TIMELINE_NOT_IN_REGISTRY: {timeline_uid}")

            entry = self._target_locks[timeline_uid]
            if entry.state != "CLOSE_OUTCOME_UNKNOWN":
                raise WholePlanReconciliationError(
                    f"ERR_CANNOT_CHALLENGE_NON_AMBIGUOUS_STATE: timeline '{timeline_uid}' is in state '{entry.state}'"
                )

            # Strict TTL validation (I3-G5): int or float, not bool
            if type(ttl_seconds) not in (int, float) or isinstance(ttl_seconds, bool):
                raise WholePlanReconciliationError(
                    f"ERR_INVALID_CHALLENGE_TTL_TYPE: ttl_seconds must be a finite float or int, not bool (got {type(ttl_seconds).__name__})"
                )

            # Non-finite check
            if not math.isfinite(ttl_seconds):
                raise WholePlanReconciliationError(
                    f"ERR_CHALLENGE_TTL_NON_FINITE: ttl_seconds must be finite, received {ttl_seconds}"
                )

            # Bounds check: [1.0, 300.0] inclusive
            if ttl_seconds < self.MIN_CHALLENGE_TTL_SECONDS:
                raise WholePlanReconciliationError(
                    f"ERR_CHALLENGE_TTL_TOO_LOW: ttl_seconds must be >= {self.MIN_CHALLENGE_TTL_SECONDS}, received {ttl_seconds}"
                )
            if ttl_seconds > self.MAX_CHALLENGE_TTL_SECONDS:
                raise WholePlanReconciliationError(
                    f"ERR_CHALLENGE_TTL_EXCEEDS_MAX: ttl_seconds must be <= {self.MAX_CHALLENGE_TTL_SECONDS}, received {ttl_seconds}"
                )

            if not entry.incident_id:
                entry.incident_id = secrets.token_hex(16)

            nonce = secrets.token_hex(16)
            now = time.time()
            expires_at = now + float(ttl_seconds)
            entry.active_challenge_nonce = nonce
            entry.challenge_expires_at = expires_at

            return ReconciliationChallenge(
                timeline_uid=timeline_uid,
                incident_id=entry.incident_id,
                challenge_nonce=nonce,
                issued_at=now,
                expires_at=expires_at,
            )

    def reconcile_ambiguous_timeline(
        self,
        authorization: OperatorReconciliationAuthorization,
        timeline_uid: Optional[str] = None,
    ) -> None:
        """
        Reconciles ambiguous timeline in CLOSE_OUTCOME_UNKNOWN state using operator HMAC authorization (I3-G6).
        Resolves operator key exclusively through internal keyring authority.
        Never touches or attempts close on old descriptor number.
        """
        self._check_not_closed()

        with self._coordinator_lock:
            self._check_not_closed()
            if not isinstance(authorization, OperatorReconciliationAuthorization):
                raise WholePlanReconciliationError(
                    "ERR_INVALID_AUTHORIZATION_OBJECT: authorization must be an instance of OperatorReconciliationAuthorization"
                )

            target_timeline_uid = timeline_uid if timeline_uid is not None else authorization.timeline_uid
            if authorization.timeline_uid != target_timeline_uid:
                raise WholePlanReconciliationError(
                    f"ERR_AUTHORIZATION_TIMELINE_MISMATCH: auth timeline '{authorization.timeline_uid}' != target '{target_timeline_uid}'"
                )

            if target_timeline_uid not in self._target_locks:
                raise WholePlanReconciliationError(f"ERR_TIMELINE_NOT_IN_REGISTRY: {target_timeline_uid}")

            entry = self._target_locks[target_timeline_uid]
            if entry.state != "CLOSE_OUTCOME_UNKNOWN":
                raise WholePlanReconciliationError(
                    f"ERR_CANNOT_RECONCILE_NON_AMBIGUOUS_STATE: timeline '{target_timeline_uid}' is in state '{entry.state}', not CLOSE_OUTCOME_UNKNOWN"
                )

            # 1. Strict boolean check for confirmed_unlocked
            if type(authorization.confirmed_unlocked) is not bool or not authorization.confirmed_unlocked:
                raise WholePlanReconciliationError(
                    "ERR_UNLOCKED_CONFIRMATION_INVALID: confirmed_unlocked must be strictly bool True"
                )

            # 2. Incident ID binding
            if authorization.incident_id != entry.incident_id:
                raise WholePlanReconciliationError(
                    f"ERR_AUTHORIZATION_INCIDENT_MISMATCH: auth incident '{authorization.incident_id}' != entry incident '{entry.incident_id}'"
                )

            # 3. Challenge nonce check (single-use anti-replay)
            if entry.active_challenge_nonce is None or authorization.challenge_nonce != entry.active_challenge_nonce:
                raise WholePlanReconciliationError(
                    "ERR_CHALLENGE_NONCE_MISMATCH_OR_REPLAY: challenge nonce is invalid, stale, or already used"
                )

            # 4. Challenge expiration check
            now = time.time()
            if entry.challenge_expires_at is None or now > entry.challenge_expires_at:
                # I3-05: Rejected attempt strictly preserves challenge state; nonce consumed only after success
                raise WholePlanReconciliationError("ERR_CHALLENGE_EXPIRED: reconciliation challenge has expired")

            # 5. Challenge expires_at exact equality (I3-R1-02)
            if authorization.challenge_expires_at != entry.challenge_expires_at:
                raise WholePlanReconciliationError(
                    f"ERR_EXPIRATION_TIMESTAMP_MISMATCH: auth challenge_expires_at {authorization.challenge_expires_at} != entry {entry.challenge_expires_at}"
                )

            # 6. Operator identity non-empty
            if not isinstance(authorization.operator_identity, str) or not authorization.operator_identity.strip():
                raise WholePlanReconciliationError("ERR_EMPTY_OPERATOR_IDENTITY: operator_identity must be non-empty str")

            # 7. Confirmation statement exact match
            expected_statement = "CONFIRMED_TIMELINE_UNLOCKED_OUT_OF_BAND"
            if authorization.operator_confirmation_statement != expected_statement:
                raise WholePlanReconciliationError(
                    f"ERR_CONFIRMATION_STATEMENT_MISMATCH: expected '{expected_statement}', got '{authorization.operator_confirmation_statement}'"
                )

            # 8. Operator key resolution via internal keyring authority
            op_record = self._keyring.get_operator_key(authorization.operator_key_id)

            # Purpose separation check (R45-01)
            if op_record.key_purpose != "TIMELINE_AMBIGUOUS_RECONCILIATION":
                raise WholePlanReconciliationError(
                    f"ERR_UNAUTHORIZED_KEY_PURPOSE: key purpose '{op_record.key_purpose}' is not authorized for timeline reconciliation"
                )

            # Identity binding check (R45-01)
            if authorization.operator_identity != op_record.permitted_operator_identity:
                raise WholePlanReconciliationError(
                    f"ERR_OPERATOR_IDENTITY_KEY_MISMATCH: authorization claims operator '{authorization.operator_identity}', "
                    f"but key is strictly bound to '{op_record.permitted_operator_identity}'"
                )

            # 9. Cryptographic HMAC-SHA256 signature verification over canonical payload bytes (I3-R1-02)
            canonical_payload = canonical_bytes_for_operator_reconciliation_auth(authorization)
            expected_sig = hmac.new(op_record.key_bytes, canonical_payload, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected_sig.lower(), authorization.signature_hmac.lower()):
                raise WholePlanReconciliationError("ERR_OPERATOR_SIGNATURE_INVALID: invalid cryptographic HMAC signature")

            # Single-use challenge invalidation
            entry.active_challenge_nonce = None
            entry.challenge_expires_at = None

            # Verify entry.fd is already None; old descriptor number is never touched
            assert entry.fd is None, "ambiguous entry.fd must be None"

            entry.state = "RELEASED"
            self._lock_state[target_timeline_uid] = "RELEASED"
            del self._target_locks[target_timeline_uid]

    def bind_signed_authorization(
        self, signed_auth: TrialExecutionAuthorization
    ) -> AuthorizationContract:
        """
        Public gated binding of signed trial execution authorization (I3-G7).
        Validates all 32 signed fields, resolves key via internal keyring authority,
        enforces constant-time HMAC-SHA256 comparison, execution ID / nonce equality,
        and validity window chronometry.
        """
        self._check_not_closed()

        with self._coordinator_lock:
            self._check_not_closed()
            tid = threading.get_ident()
            self._active_operation_threads[tid] = self._active_operation_threads.get(tid, 0) + 1
            try:
                if not isinstance(signed_auth, TrialExecutionAuthorization):
                    raise ReadbackReceiptVerificationError(
                        "ERR_INVALID_AUTH_OBJECT: signed_auth must be an instance of TrialExecutionAuthorization"
                    )

                # Authoritative field validation via contracts boundary
                validate_trial_execution_authorization(signed_auth)

                # Resolve trial key exclusively through internal keyring authority
                trusted_key = self._keyring.get_key(signed_auth.key_id)

                # Obtain canonical serialization of the 32 HMAC fields
                canon_bytes = canonical_bytes_for_auth_hmac(signed_auth)

                # Constant-time HMAC-SHA256 verification
                calc_hmac = hmac.new(trusted_key, canon_bytes, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(calc_hmac.lower(), signed_auth.authorization_hmac.lower()):
                    raise ReadbackReceiptVerificationError(
                        "ERR_SIGNED_AUTH_HMAC_INVALID: HMAC mismatch on 32-field upstream canonical payload"
                    )

                # Nonce equality enforcement
                if signed_auth.execution_id != signed_auth.token_nonce:
                    raise WholePlanReconciliationError(
                        f"ERR_EXECUTION_NONCE_MISMATCH: execution_id '{signed_auth.execution_id}' != token_nonce '{signed_auth.token_nonce}'"
                    )

                # Chronological validity window check (issued_at <= now <= valid_until)
                issued_dt = parse_iso_utc_timestamp(signed_auth.issued_at_iso, "issued_at_iso")
                valid_dt = parse_iso_utc_timestamp(signed_auth.valid_until_iso, "valid_until_iso")
                now = datetime.now(timezone.utc)
                if now < issued_dt or now > valid_dt:
                    raise ReadbackReceiptVerificationError(
                        f"ERR_AUTH_VALIDITY_WINDOW_EXPIRED: current time {now.isoformat()} outside [{issued_dt.isoformat()}, {valid_dt.isoformat()}]"
                    )

                # Frame geometry validation
                rec_frame = signed_auth.authorized_record_frame
                dur_frames = signed_auth.authorized_timeline_frame_duration
                if type(rec_frame) is not int or isinstance(rec_frame, bool):
                    raise ReadbackReceiptVerificationError("ERR_AUTH_RECORD_FRAME_INVALID: must be int not bool")
                if rec_frame < 0:
                    raise ReadbackReceiptVerificationError("ERR_AUTH_RECORD_FRAME_NEGATIVE: must be >= 0")
                if type(dur_frames) is not int or isinstance(dur_frames, bool):
                    raise ReadbackReceiptVerificationError("ERR_AUTH_DURATION_FRAMES_INVALID: must be int not bool")
                if dur_frames < 1:
                    raise ReadbackReceiptVerificationError("ERR_AUTH_DURATION_FRAMES_BELOW_MIN: must be >= 1")
                if rec_frame + dur_frames > 100_000_000:
                    raise ReadbackReceiptVerificationError(
                        "ERR_AUTH_GEOMETRY_OVERFLOW: record_frame + duration_frames exceeds 100,000,000"
                    )

                contract = AuthorizationContract(
                    authorization_id=signed_auth.authorization_id,
                    approval_id=signed_auth.approval_record_id,
                    execution_id=signed_auth.execution_id,
                    plan_idempotency_key=signed_auth.plan_idempotency_key,
                    target_project_uid=signed_auth.target_project_uid,
                    target_timeline_uid=signed_auth.target_timeline_uid,
                    track_type=signed_auth.authorized_track_type,
                    track_index=signed_auth.authorized_track_index,
                    record_frame=rec_frame,
                    duration_frames=dur_frames,
                    action_payload_digest_sha256=signed_auth.action_payload_digest_sha256,
                    key_id=signed_auth.key_id,
                )
                self._bound_auth = contract
                return contract

            finally:
                self._active_operation_threads[tid] -= 1
                if self._active_operation_threads[tid] == 0:
                    del self._active_operation_threads[tid]

    def close(self) -> None:
        """
        Closes coordinator instance, releasing all held resources.
        Preserves unclosed descriptors or failed close states visibly without silent swallowing (I3-01).
        """
        if self._closed:
            return

        self._closing = True
        with self._coordinator_lock:
            cleanup_errors: List[str] = []

            # Release held target locks: flock(LOCK_UN) first, os.close(fd) second (I3-01)
            for timeline_uid, entry in list(self._target_locks.items()):
                if entry.fd is not None:
                    unlock_ok = False
                    if entry.state in ("LOCKED", "IN_DOUBT_LOCKED"):
                        try:
                            fcntl.flock(entry.fd, fcntl.LOCK_UN)
                            unlock_ok = True
                        except OSError as e:
                            # I3-01: DO NOT close fd! Retain live descriptor and kernel exclusion
                            entry.state = "IN_DOUBT_LOCKED"
                            self._lock_state[timeline_uid] = "IN_DOUBT_LOCKED"
                            cleanup_errors.append(f"flock_un({timeline_uid}): {e}")

                    if unlock_ok:
                        failed_fd = entry.fd
                        try:
                            os.close(entry.fd)
                            entry.state = "RELEASED"
                            entry.fd = None
                            self._lock_state[timeline_uid] = "RELEASED"
                            del self._target_locks[timeline_uid]
                        except OSError as e:
                            entry.state = "CLOSE_OUTCOME_UNKNOWN"
                            entry.fd = None
                            entry.incident_id = secrets.token_hex(16)
                            self._lock_state[timeline_uid] = "CLOSE_OUTCOME_UNKNOWN"
                            cleanup_errors.append(f"close({timeline_uid}, fd={failed_fd}): {e}")

            # Close durable ledger service
            if self._durable_ledger is not None:
                try:
                    self._durable_ledger.close()
                except Exception as e:
                    cleanup_errors.append(f"_durable_ledger: {e}")

            # Close anchored directory descriptors
            for name in ("_journals_dir_fd", "_receipts_dir_fd", "_locks_dir_fd"):
                fd = getattr(self, name, None)
                if fd is not None:
                    try:
                        os.close(fd)
                        setattr(self, name, None)
                    except OSError as ce:
                        cleanup_errors.append(f"{name}: {ce}")

            if cleanup_errors:
                self._close_failed = True
                raise AcceptanceGateError(f"ERR_COORDINATOR_CLOSE_FAILED: {'; '.join(cleanup_errors)}")

            self._closed = True
            self._closing = False
