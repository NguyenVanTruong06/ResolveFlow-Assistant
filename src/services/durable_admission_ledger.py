"""Isolated Unified Admission Replay Ledger Core.

Implements the single compound replay consumption ledger `consumed_admissions.jsonl`
specified in Plan 07B7 Phase 2 Hardening Blocker B (Workorder
G07-07B7-GATE6-PHASE2-BLOCKER-B-GATE5-ADMISSION-LEDGER-CORE-R1 and
docs/architecture/07B7-GATE6-PHASE2-BLOCKER-B-GATE5-ADMISSION-SERVER-DESIGN-R1.md §4.2).

Safety Boundary:
- Pure descriptor-relative ledger core operating strictly over a caller-supplied,
  already-opened directory file descriptor (`dir_fd`).
- Strictly no production path derivation or fallback (/var/lib/resolveflow,
  /etc/resolveflow, /run/resolveflow, /proc, ~, etc.).
- Strictly no CLI, socket, daemon, listener, IPC, service registration, or network code.
- Strictly no trust-root loading, signature verification, token issuance,
  or DaVinci Resolve interaction.
- Strict closed-world validation, RFC 8785 canonical serialization, and fail-closed
  torn-tail handling.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import fcntl
import json
import os
import re
import stat
import threading
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
)

LEDGER_FILENAME: str = "consumed_admissions.jsonl"

RE_CANONICAL_UUID_V4: re.Pattern = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
RE_WORKORDER_ID: re.Pattern = re.compile(
    r"^G07-07B7-GATE6-EXEC-R[1-9][0-9]*$"
)

REQUIRED_RECORD_FIELDS: frozenset[str] = frozenset({
    "admission_id",
    "execution_id",
    "preflight_run_id",
    "workorder_id",
    "admitted_at_iso",
})


# ------------------------------------------------------------------------------
# Exception Hierarchy
# ------------------------------------------------------------------------------

class AdmissionLedgerError(AcceptanceGateError, Exception):
    """Base exception for all admission ledger errors."""

    def __init__(self, code: str, detail: Optional[str] = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail or ""


class AdmissionRecordValidationError(AdmissionLedgerError, ValueError):
    """Raised when record fields fail schema, nominal type, or format validation."""
    pass


class AdmissionReplayError(AdmissionLedgerError):
    """Raised when an identifier in a candidate record has already been consumed."""
    pass


class AdmissionLedgerCorruptionError(AdmissionLedgerError):
    """Raised when the ledger contains torn tails, malformed JSON, or corrupt data."""
    pass


class AdmissionLedgerLockError(AdmissionLedgerError):
    """Raised when an exclusive lock on the ledger cannot be acquired."""
    pass


class AdmissionLedgerSecurityError(AdmissionLedgerError):
    """Raised on mode, link-count, descriptor, or node type security violations."""
    pass


class AdmissionLedgerPersistenceError(AdmissionLedgerError):
    """Raised when append or fsync fails to commit a record to durable storage."""
    pass


class AdmissionLedgerPoisonError(AdmissionLedgerError):
    """Raised when attempting operations on a poisoned ledger instance."""
    pass


# ------------------------------------------------------------------------------
# Strict JSON & Record Parsing Helpers
# ------------------------------------------------------------------------------

def parse_strict_json(raw_bytes: bytes) -> Dict[str, Any]:
    """Parses UTF-8 JSON bytes rejecting duplicate keys fail-closed.

    Raises:
        AdmissionRecordValidationError: on non-bytes input.
        AdmissionLedgerCorruptionError: on invalid UTF-8, malformed JSON, duplicate keys, or non-dict root.
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise AdmissionRecordValidationError(
            "ERR_TYPE_MISMATCH", f"Expected bytes, got {type(raw_bytes).__name__}"
        )
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdmissionLedgerCorruptionError(
            "ERR_ADMISSION_LEDGER_CORRUPT", f"Non-UTF8 payload: {exc}"
        ) from exc

    def _pairs_hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        for k, v in pairs:
            if k in d:
                raise AdmissionLedgerCorruptionError(
                    "ERR_ADMISSION_LEDGER_CORRUPT", f"Duplicate JSON key: {k!r}"
                )
            d[k] = v
        return d

    try:
        data = json.loads(text, object_pairs_hook=_pairs_hook)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise AdmissionLedgerCorruptionError(
            "ERR_ADMISSION_LEDGER_CORRUPT", f"Malformed JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise AdmissionLedgerCorruptionError(
            "ERR_ADMISSION_LEDGER_CORRUPT", f"JSON root must be a dict, got {type(data).__name__}"
        )
    return data


def _safe_close(fd: Optional[int]) -> None:
    """Closes a file descriptor safely suppressing OSError."""
    if fd is not None and fd >= 0:
        try:
            os.close(fd)
        except OSError:
            pass


def _write_all(fd: int, data: bytes) -> None:
    """Writes all bytes to a file descriptor in a loop."""
    total = 0
    while total < len(data):
        n = os.write(fd, data[total:])
        if n <= 0:
            raise OSError("Zero or negative bytes written during append")
        total += n


def _read_all(fd: int) -> bytes:
    """Reads all remaining bytes from a file descriptor."""
    chunks: List[bytes] = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


# ------------------------------------------------------------------------------
# Compound Ledger Record DTO
# ------------------------------------------------------------------------------

@dataclass(frozen=True)
class ConsumedAdmissionRecord:
    """Frozen DTO representing a single compound admission entry in `consumed_admissions.jsonl`.

    Closed-world fields:
        admission_id: Canonical lowercase UUIDv4.
        execution_id: Canonical lowercase UUIDv4.
        preflight_run_id: Canonical lowercase UUIDv4.
        workorder_id: Workorder ID matching ^G07-07B7-GATE6-EXEC-R[1-9][0-9]*$.
        admitted_at_iso: Strict UTC ISO-8601 timestamp ending in 'Z'.
    """

    admission_id: str
    execution_id: str
    preflight_run_id: str
    workorder_id: str
    admitted_at_iso: str

    def __post_init__(self) -> None:
        # 1. Strict nominal type checks (all must be str; reject int, bool, None)
        for field_name in ("admission_id", "execution_id", "preflight_run_id", "workorder_id", "admitted_at_iso"):
            val = getattr(self, field_name)
            if type(val) is not str:
                raise AdmissionRecordValidationError(
                    "ERR_TYPE_MISMATCH",
                    f"{field_name} must be str, got {type(val).__name__}",
                )

        # 2. Canonical UUIDv4 checks
        for field_name in ("admission_id", "execution_id", "preflight_run_id"):
            val = getattr(self, field_name)
            if not RE_CANONICAL_UUID_V4.fullmatch(val):
                raise AdmissionRecordValidationError(
                    "ERR_INVALID_UUID",
                    f"{field_name} must be canonical lowercase UUIDv4, got {val!r}",
                )

        # 3. Workorder ID grammar check
        if not RE_WORKORDER_ID.fullmatch(self.workorder_id):
            raise AdmissionRecordValidationError(
                "ERR_WORKORDER_ID_FORMAT",
                f"workorder_id must match {RE_WORKORDER_ID.pattern}, got {self.workorder_id!r}",
            )

        # 4. Strict UTC ISO-8601 timestamp check
        if not self.admitted_at_iso.endswith("Z") or self.admitted_at_iso != self.admitted_at_iso.strip():
            raise AdmissionRecordValidationError(
                "ERR_INVALID_TIMESTAMP",
                f"admitted_at_iso must be strict UTC timestamp ending in 'Z', got {self.admitted_at_iso!r}",
            )
        try:
            parse_iso_utc_timestamp(self.admitted_at_iso, "admitted_at_iso")
        except Exception as exc:
            raise AdmissionRecordValidationError(
                "ERR_INVALID_TIMESTAMP",
                f"admitted_at_iso is not a valid ISO timestamp: {exc}",
            ) from exc

    def to_dict(self) -> Dict[str, str]:
        """Returns a plain mapping of the compound record."""
        return {
            "admission_id": self.admission_id,
            "admitted_at_iso": self.admitted_at_iso,
            "execution_id": self.execution_id,
            "preflight_run_id": self.preflight_run_id,
            "workorder_id": self.workorder_id,
        }

    def to_json_bytes(self) -> bytes:
        """Returns deterministic RFC 8785 canonical JSON bytes."""
        return canonical_json_bytes(self.to_dict())

    def to_json_line(self) -> bytes:
        """Returns the canonical JSON bytes terminated with a single newline."""
        return self.to_json_bytes() + b"\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConsumedAdmissionRecord:
        """Constructs and validates a record from a plain mapping enforcing closed-world fields."""
        if not isinstance(data, Mapping):
            raise AdmissionRecordValidationError(
                "ERR_TYPE_MISMATCH", f"Expected mapping, got {type(data).__name__}"
            )
        keys = set(data.keys())
        missing = REQUIRED_RECORD_FIELDS - keys
        if missing:
            raise AdmissionRecordValidationError(
                "ERR_MISSING_FIELD", f"Missing required fields: {sorted(missing)}"
            )
        extra = keys - REQUIRED_RECORD_FIELDS
        if extra:
            raise AdmissionRecordValidationError(
                "ERR_UNEXPECTED_FIELD", f"Unexpected fields in record: {sorted(extra)}"
            )
        return cls(
            admission_id=data["admission_id"],
            execution_id=data["execution_id"],
            preflight_run_id=data["preflight_run_id"],
            workorder_id=data["workorder_id"],
            admitted_at_iso=data["admitted_at_iso"],
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> ConsumedAdmissionRecord:
        """Parses strict JSON bytes and returns a validated ConsumedAdmissionRecord."""
        parsed = parse_strict_json(raw_bytes)
        return cls.from_dict(parsed)


# ------------------------------------------------------------------------------
# Strict Ledger Byte Parser
# ------------------------------------------------------------------------------

def parse_ledger_bytes(raw_bytes: bytes) -> List[ConsumedAdmissionRecord]:
    """Strictly parses append-only JSONL ledger bytes into a list of records.

    Fail-closed invariants:
    - Empty bytes -> returns empty list.
    - Missing trailing newline -> raises AdmissionLedgerCorruptionError (torn tail).
    - Empty line / consecutive newlines -> raises AdmissionLedgerCorruptionError.
    - Malformed JSON / invalid UTF-8 / duplicate keys -> raises AdmissionLedgerCorruptionError.
    - Missing or unexpected fields -> raises AdmissionLedgerCorruptionError.
    - Duplicate identifier across records (admission_id, execution_id, preflight_run_id, workorder_id)
      -> raises AdmissionLedgerCorruptionError.
    - Zero lines silently skipped, truncated, or repaired.
    """
    if len(raw_bytes) == 0:
        return []

    if not raw_bytes.endswith(b"\n"):
        raise AdmissionLedgerCorruptionError(
            "ERR_ADMISSION_LEDGER_CORRUPT", "Missing trailing newline (torn tail detected)"
        )

    lines = raw_bytes[:-1].split(b"\n")
    records: List[ConsumedAdmissionRecord] = []
    seen_admission_ids: Set[str] = set()
    seen_execution_ids: Set[str] = set()
    seen_preflight_ids: Set[str] = set()
    seen_workorder_ids: Set[str] = set()

    for line_num, line in enumerate(lines, start=1):
        if len(line) == 0:
            raise AdmissionLedgerCorruptionError(
                "ERR_ADMISSION_LEDGER_CORRUPT",
                f"Empty line detected in ledger at line {line_num}",
            )

        try:
            rec = ConsumedAdmissionRecord.from_json_bytes(line)
        except (AdmissionRecordValidationError, AdmissionLedgerCorruptionError) as exc:
            raise AdmissionLedgerCorruptionError(
                "ERR_ADMISSION_LEDGER_CORRUPT",
                f"Corrupt record at line {line_num}: {exc}",
            ) from exc

        # Duplicate ID check across the ledger
        if rec.admission_id in seen_admission_ids:
            raise AdmissionLedgerCorruptionError(
                "ERR_ADMISSION_LEDGER_CORRUPT",
                f"Duplicate admission_id {rec.admission_id!r} at line {line_num}",
            )
        if rec.execution_id in seen_execution_ids:
            raise AdmissionLedgerCorruptionError(
                "ERR_ADMISSION_LEDGER_CORRUPT",
                f"Duplicate execution_id {rec.execution_id!r} at line {line_num}",
            )
        if rec.preflight_run_id in seen_preflight_ids:
            raise AdmissionLedgerCorruptionError(
                "ERR_ADMISSION_LEDGER_CORRUPT",
                f"Duplicate preflight_run_id {rec.preflight_run_id!r} at line {line_num}",
            )
        if rec.workorder_id in seen_workorder_ids:
            raise AdmissionLedgerCorruptionError(
                "ERR_ADMISSION_LEDGER_CORRUPT",
                f"Duplicate workorder_id {rec.workorder_id!r} at line {line_num}",
            )

        seen_admission_ids.add(rec.admission_id)
        seen_execution_ids.add(rec.execution_id)
        seen_preflight_ids.add(rec.preflight_run_id)
        seen_workorder_ids.add(rec.workorder_id)
        records.append(rec)

    return records


# ------------------------------------------------------------------------------
# Descriptor-Observed Security Validation
# ------------------------------------------------------------------------------

def _validate_ledger_file_descriptor(fd: int, filename: str) -> os.stat_result:
    """Enforces descriptor-observed regular-file, mode 0600, and single-link policy."""
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise AdmissionLedgerSecurityError(
            "ERR_NOT_REGULAR", f"{filename} is not a regular file"
        )
    if st.st_nlink != 1:
        raise AdmissionLedgerSecurityError(
            "ERR_LINK_COUNT_ANOMALY", f"{filename} link count {st.st_nlink} != 1"
        )
    mode = stat.S_IMODE(st.st_mode)
    if mode != 0o600:
        raise AdmissionLedgerSecurityError(
            "ERR_UNSAFE_FILE_MODE", f"{filename} mode 0o{mode:o} != 0o600"
        )
    return st


# ------------------------------------------------------------------------------
# Durable Admission Ledger Core Component
# ------------------------------------------------------------------------------

class DurableAdmissionLedger:
    """Descriptor-anchored unified compound admission replay ledger.

    Operates strictly within an already-opened directory descriptor (`dir_fd`).
    Provides check-and-consume serialization via non-blocking `flock`, strict
    re-validation under lock, independent replay rejection across all identifiers,
    append with fsync, and fail-closed torn-tail / corruption poisoning.
    """

    FILENAME: str = LEDGER_FILENAME

    def __init__(self, dir_fd: int) -> None:
        if not isinstance(dir_fd, int) or dir_fd < 0:
            raise AdmissionLedgerSecurityError(
                "ERR_INVALID_DIR_FD",
                f"dir_fd must be a valid non-negative integer, got {dir_fd!r}",
            )

        try:
            st = os.fstat(dir_fd)
        except OSError as exc:
            raise AdmissionLedgerSecurityError(
                "ERR_DIR_FD_STAT_FAILED", f"Failed to stat dir_fd {dir_fd}: {exc}"
            ) from exc

        if not stat.S_ISDIR(st.st_mode):
            raise AdmissionLedgerSecurityError(
                "ERR_DIR_FD_NOT_DIR", f"dir_fd {dir_fd} is not a directory"
            )

        self._dir_fd = dir_fd
        self._lock = threading.Lock()
        self._is_poisoned: bool = False
        self._poison_reason: Optional[str] = None

    @property
    def is_poisoned(self) -> bool:
        """Returns True if the ledger has experienced unrecoverable error and is poisoned."""
        with self._lock:
            return self._is_poisoned

    @property
    def poison_reason(self) -> Optional[str]:
        """Returns the reason string if poisoned; None otherwise."""
        with self._lock:
            return self._poison_reason

    def _assert_not_poisoned(self) -> None:
        """Raises AdmissionLedgerPoisonError if the ledger instance is poisoned."""
        if self._is_poisoned:
            raise AdmissionLedgerPoisonError(
                "ERR_ADMISSION_LEDGER_POISONED",
                f"Ledger is permanently poisoned: {self._poison_reason}",
            )

    def _poison(self, reason: str) -> None:
        """Poisons the ledger instance fail-closed."""
        self._is_poisoned = True
        self._poison_reason = reason

    def read_all_records(self) -> List[ConsumedAdmissionRecord]:
        """Reads and validates all consumed admission records from disk.

        Raises:
            AdmissionLedgerPoisonError: if the ledger is poisoned.
            AdmissionLedgerSecurityError: on node mode/link-count security violations.
            AdmissionLedgerCorruptionError: on torn tails, malformed JSON, or duplicates.
        """
        with self._lock:
            self._assert_not_poisoned()

            flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.FILENAME, flags, dir_fd=self._dir_fd)
            except FileNotFoundError:
                return []
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise AdmissionLedgerSecurityError(
                        "ERR_SYMLINK_DETECTED", f"{self.FILENAME} cannot be traversed: {exc}"
                    ) from exc
                raise AdmissionLedgerSecurityError(
                    "ERR_CANNOT_OPEN_LEDGER", f"Failed to open {self.FILENAME}: {exc}"
                ) from exc

            try:
                _validate_ledger_file_descriptor(fd, self.FILENAME)
                raw_bytes = _read_all(fd)
            finally:
                _safe_close(fd)

            try:
                return parse_ledger_bytes(raw_bytes)
            except AdmissionLedgerCorruptionError as exc:
                self._poison(f"Corruption detected during read: {exc}")
                raise

    def check_replay(
        self,
        *,
        workorder_id: str,
        execution_id: str,
        preflight_run_id: str,
    ) -> None:
        """Checks whether any of the supplied identifiers have already been consumed.

        Raises:
            AdmissionReplayError: if any identifier was previously recorded:
                - workorder_id -> ERR_GATE5_WORKORDER_ALREADY_CONSUMED
                - execution_id -> ERR_GATE5_WORKORDER_REPLAYED
                - preflight_run_id -> ERR_GATE4_EVIDENCE_REPLAYED
            AdmissionLedgerCorruptionError: if the ledger on disk is corrupt.
            AdmissionLedgerPoisonError: if the ledger is poisoned.
        """
        records = self.read_all_records()
        for rec in records:
            if rec.workorder_id == workorder_id:
                raise AdmissionReplayError(
                    "ERR_GATE5_WORKORDER_ALREADY_CONSUMED",
                    f"workorder_id {workorder_id!r} has already been consumed",
                )
            if rec.execution_id == execution_id:
                raise AdmissionReplayError(
                    "ERR_GATE5_WORKORDER_REPLAYED",
                    f"execution_id {execution_id!r} has already been consumed",
                )
            if rec.preflight_run_id == preflight_run_id:
                raise AdmissionReplayError(
                    "ERR_GATE4_EVIDENCE_REPLAYED",
                    f"preflight_run_id {preflight_run_id!r} has already been consumed",
                )

    def consume_admission(self, record: ConsumedAdmissionRecord) -> None:
        """Atomically checks replay invariants and commits a compound record with fsync.

        Execution steps:
        1. Validates record argument.
        2. Opens or creates `consumed_admissions.jsonl` relative to `self._dir_fd` with mode 0600.
        3. Acquires non-blocking exclusive flock (`LOCK_EX | LOCK_NB`).
        4. Validates descriptor security policy (regular file, mode 0600, nlink == 1).
        5. Re-reads and strictly parses entire ledger under lock.
        6. Verifies replay invariants for all 4 identifiers (workorder_id, execution_id,
           preflight_run_id, admission_id).
        7. Appends single canonical JSON line at end-of-file.
        8. Flushes data with `os.fsync(fd)` and updates directory entry with `os.fsync(dir_fd)`.
        9. Releases flock.

        If write or fsync fails, the instance is immediately poisoned fail-closed.
        """
        if type(record) is not ConsumedAdmissionRecord:
            raise AdmissionRecordValidationError(
                "ERR_TYPE_MISMATCH",
                f"Expected exact ConsumedAdmissionRecord, got {type(record).__name__}",
            )

        # Reconstruct and validate a fresh immutable snapshot DTO from a one-time
        # copied field mapping at method entry (BLOCKER-B-ADMISSION-LEDGER-CORE-R2-01).
        # This protects against post-init mutation via object.__setattr__.
        snapshot_mapping = {
            "admission_id": record.admission_id,
            "execution_id": record.execution_id,
            "preflight_run_id": record.preflight_run_id,
            "workorder_id": record.workorder_id,
            "admitted_at_iso": record.admitted_at_iso,
        }
        # Re-validates all fields via ConsumedAdmissionRecord constructor (__post_init__)
        snapshot_record = ConsumedAdmissionRecord.from_dict(snapshot_mapping)

        # Bind immutable local values from the validated snapshot
        rec_admission_id = snapshot_record.admission_id
        rec_execution_id = snapshot_record.execution_id
        rec_preflight_run_id = snapshot_record.preflight_run_id
        rec_workorder_id = snapshot_record.workorder_id
        rec_admitted_at_iso = snapshot_record.admitted_at_iso

        with self._lock:
            self._assert_not_poisoned()

            open_flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0)
            )

            try:
                fd = os.open(self.FILENAME, open_flags, 0o600, dir_fd=self._dir_fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise AdmissionLedgerSecurityError(
                        "ERR_SYMLINK_DETECTED", f"{self.FILENAME} cannot be traversed: {exc}"
                    ) from exc
                raise AdmissionLedgerSecurityError(
                    "ERR_CANNOT_OPEN_LEDGER", f"Failed to open {self.FILENAME}: {exc}"
                ) from exc

            try:
                # 1. Non-blocking exclusive flock serialization
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (BlockingIOError, OSError) as exc:
                    raise AdmissionLedgerLockError(
                        "ERR_ADMISSION_LEDGER_LOCKED",
                        f"Failed to acquire exclusive lock on admission ledger: {exc}",
                    ) from exc

                # 2. Node mode and link count security validation
                _validate_ledger_file_descriptor(fd, self.FILENAME)

                # 3. Read existing contents from offset 0 under lock
                os.lseek(fd, 0, os.SEEK_SET)
                existing_bytes = _read_all(fd)

                # 4. Strict parse and replay check under lock
                try:
                    existing_records = parse_ledger_bytes(existing_bytes)
                except AdmissionLedgerCorruptionError as exc:
                    self._poison(f"Corruption detected in existing ledger during consume: {exc}")
                    raise

                for existing in existing_records:
                    if existing.workorder_id == rec_workorder_id:
                        raise AdmissionReplayError(
                            "ERR_GATE5_WORKORDER_ALREADY_CONSUMED",
                            f"workorder_id {rec_workorder_id!r} has already been consumed",
                        )
                    if existing.execution_id == rec_execution_id:
                        raise AdmissionReplayError(
                            "ERR_GATE5_WORKORDER_REPLAYED",
                            f"execution_id {rec_execution_id!r} has already been consumed",
                        )
                    if existing.preflight_run_id == rec_preflight_run_id:
                        raise AdmissionReplayError(
                            "ERR_GATE4_EVIDENCE_REPLAYED",
                            f"preflight_run_id {rec_preflight_run_id!r} has already been consumed",
                        )
                    if existing.admission_id == rec_admission_id:
                        raise AdmissionReplayError(
                            "ERR_ADMISSION_ID_REPLAYED",
                            f"admission_id {rec_admission_id!r} has already been consumed",
                        )

                # 5. Prepare canonical JSON line from validated snapshot values
                line_dict = {
                    "admission_id": rec_admission_id,
                    "admitted_at_iso": rec_admitted_at_iso,
                    "execution_id": rec_execution_id,
                    "preflight_run_id": rec_preflight_run_id,
                    "workorder_id": rec_workorder_id,
                }
                line_bytes = canonical_json_bytes(line_dict) + b"\n"
                os.lseek(fd, 0, os.SEEK_END)

                try:
                    _write_all(fd, line_bytes)
                    os.fsync(fd)
                    # Sync directory entry to ensure durable discovery
                    os.fsync(self._dir_fd)
                except Exception as exc:
                    self._poison(f"Write or fsync failure during append: {exc}")
                    raise AdmissionLedgerPersistenceError(
                        "ERR_ADMISSION_LEDGER_PERSISTENCE_FAILED",
                        f"Failed to durably commit admission record: {exc}",
                    ) from exc

                # 6. Unlock
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass

            finally:
                _safe_close(fd)
