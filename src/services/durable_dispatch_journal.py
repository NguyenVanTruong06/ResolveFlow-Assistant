"""
Durable Dispatch Journal & Write-Ahead State Machine (Gate 6 DEP-03).

Architecture Basis:
- docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md (§5.2.1-5.2.3, §7.4)
- Workorder: .bridge/task.md (G07-07B7-GATE6-DEP03-R2)
- Remediation: docs/architecture/07B7-GATE6-DEP03-R2-code-review.md (DEP03-R2-05, DEP03-R2-06)
- Remediation: docs/architecture/07B7-GATE6-DEP03-R3-code-review.md (DEP03-R3-03)

Boundary:
- IMPLEMENTATION_AND_LOCAL_TEMP_TESTS_ONLY
- NO_LIVE_DISPATCH; NO_RESOLVE_IMPORT_OR_CONNECTION
- NO_HOST_PROVISIONING; NO_SYSTEMD_LIFECYCLE
- NO_PRODUCTION_KEY_OR_STATE_ACCESS; NO_NETWORK; NO_LIVE_OPERATIONS
- READY_TO_EXECUTE_FALSE

Implements descriptor-anchored fail-closed persistence:
1. 4-Phase Atomic Replacement Protocol (§5.2.1):
   unique same-dir tmp with O_EXCL mode 0600 -> write & fsync -> descriptor-relative atomic replace -> dir fsync
2. Strict Node Mode and Ownership Policy (DEP03-R3-03):
   Descriptor-observed exact modes: 0700 for directories (.locks) and 0600 for files.
   Permissive/unsafe modes fail closed before any read, append, or mutation without chmod/overwrite.
3. Pre-mutation Durability Ordering (§5.2.2):
   Step 1: Write-Ahead Audit Intent fsync
   Step 2: Durable Target Ledger Reservation fsync
   Step 3: Durable WAL Reservation fsync
   Step 4: Target exclusion lock via fcntl.flock
4. Terminal Finalization (§5.2.2 Step 8):
   Branch 1: COMPLETED (WAL committed + finish + receipt + ledger EXHAUSTED)
   Branch 2: ABORTED (WAL aborted + finish + receipt + ledger EXHAUSTED)
   Branch 3: IN_DOUBT (Target ledger IN_DOUBT_LOCKED + persistent .in_doubt marker)
5. Crash Recovery (§5.2.3):
   Reconstruct in-doubt state on restart for any unmatched ACTION_PENDING
6. Stable Inode Target Lock (§5.2.2, DEP03-R2-06):
   Lock files remain on disk with stable inode; never unlinked during normal release
"""

from __future__ import annotations

import datetime
import errno
import fcntl
import hashlib
import json
import os
import secrets
import stat
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from src.core.contracts.oracle_ipc_contracts import (
    VALID_TERMINAL_STATES,
    parse_strict_json,
)

# ------------------------------------------------------------------------------
# Exception Hierarchy
# ------------------------------------------------------------------------------
class DispatchJournalError(Exception):
    """Base exception for Durable Dispatch Journal errors."""


class DispatchJournalSecurityError(DispatchJournalError):
    """Raised when path validation, file mode, ownership, or boundary constraints fail."""


class DispatchJournalCorruptionError(DispatchJournalError):
    """Raised when journal state, WAL, or target ledger is structurally corrupt or has torn tails."""


class DispatchJournalLockError(DispatchJournalError):
    """Raised when target exclusion lock cannot be acquired or contention is detected."""


class DispatchJournalInDoubtError(DispatchJournalError):
    """Raised when attempting an operation on an in-doubt target or unrecovered state."""


class DispatchJournalPoisonError(DispatchJournalError):
    """Raised when journal enters an unrecoverable poisoned state."""


# ------------------------------------------------------------------------------
# Descriptor-Anchored POSIX Filesystem Helpers
# ------------------------------------------------------------------------------
def _write_all(fd: int, data: bytes) -> None:
    """Writes all bytes in data to file descriptor fd."""
    view = memoryview(data)
    total_written = 0
    while total_written < len(data):
        written = os.write(fd, view[total_written:])
        if written == 0:
            raise OSError("os.write returned 0 bytes")
        total_written += written


def _safe_close(fd: Optional[int]) -> None:
    """Closes a file descriptor safely, ignoring OSError."""
    if fd is not None and fd >= 0:
        try:
            os.close(fd)
        except OSError:
            pass


def _open_dir_nofollow_traversal(target_path: Union[str, Path]) -> int:
    """
    Safely opens a directory descriptor using step-by-step O_NOFOLLOW traversal.
    Rejects any intermediate or leaf symlinks without using path resolution.
    """
    path_str = str(target_path)
    if os.path.islink(path_str):
        raise DispatchJournalSecurityError(
            f"ERR_STATE_DIR_SYMLINK: Target path is a symbolic link: {path_str}"
        )

    norm_path = os.path.normpath(path_str)
    if not os.path.isabs(norm_path):
        norm_path = os.path.abspath(norm_path)

    parts = [p for p in norm_path.split(os.sep) if p]

    # Open root directory
    try:
        cur_fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:
        raise DispatchJournalSecurityError(f"ERR_CANNOT_OPEN_ROOT: {exc}") from exc

    for part in parts:
        try:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=cur_fd,
            )
            os.close(cur_fd)
            cur_fd = next_fd
        except FileNotFoundError:
            # Create directory component securely
            try:
                os.mkdir(part, 0o700, dir_fd=cur_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                os.close(cur_fd)
                raise DispatchJournalSecurityError(
                    f"ERR_CANNOT_CREATE_DIR_COMPONENT: Component {part}: {exc}"
                ) from exc

            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=cur_fd,
                )
                os.close(cur_fd)
                cur_fd = next_fd
            except OSError as exc:
                os.close(cur_fd)
                raise DispatchJournalSecurityError(
                    f"ERR_CANNOT_OPEN_CREATED_COMPONENT: Component {part}: {exc}"
                ) from exc
        except OSError as exc:
            os.close(cur_fd)
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise DispatchJournalSecurityError(
                    f"ERR_STATE_DIR_SYMLINK: Traversed component {part} is a symlink or not a directory: {exc}"
                ) from exc
            raise DispatchJournalSecurityError(
                f"ERR_DIR_TRAVERSAL_FAILED: Component {part}: {exc}"
            ) from exc

    return cur_fd


# ------------------------------------------------------------------------------
# DurableDispatchJournal
# ------------------------------------------------------------------------------
class DurableDispatchJournal:
    """
    Descriptor-Anchored Durable Dispatch Journal.
    Enforces write-ahead durability, atomic replacement protocol, crash recovery,
    strict node mode/ownership validation (DEP03-R3-03), and multi-process target exclusion
    via stable-inode locks (DEP03-R2-06).
    """

    AUDIT_LOG_NAME = "execution.log"
    WAL_FILE_NAME = "execution_journal.wal"
    TARGET_LEDGER_NAME = "target_ledger.v2.json"
    RECEIPT_FILE_NAME = "receipt.json"
    LOCKS_DIR_NAME = ".locks"

    def __init__(
        self,
        state_dir: Union[str, Path],
        *,
        enforce_ownership: bool = False,
        expected_uid: Optional[int] = None,
        expected_gid: Optional[int] = None,
    ) -> None:
        self._state_dir_path = Path(state_dir)
        self._enforce_ownership = enforce_ownership
        self._expected_uid = os.geteuid() if expected_uid is None else expected_uid
        self._expected_gid = expected_gid
        self._lock = threading.Lock()
        self._is_poisoned = False
        self._poison_reason: Optional[str] = None
        self._closed = False

        # Directory file descriptors (anchored via O_NOFOLLOW)
        self._state_dir_fd: Optional[int] = None
        self._locks_dir_fd: Optional[int] = None

        # Active lock descriptors: target_hash -> fd
        self._active_locks: Dict[str, int] = {}

        # In-memory recovered state
        self._target_state: Dict[str, Any] = {}
        self._wal_records: List[Dict[str, Any]] = []
        self._in_doubt_targets: Set[str] = set()
        self._committed_executions: Set[str] = set()

        try:
            self._initialize_descriptor_storage()
            self._recover_state()
        except Exception:
            self.close()
            raise

    def __enter__(self) -> DurableDispatchJournal:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @property
    def is_poisoned(self) -> bool:
        return self._is_poisoned

    @property
    def state_dir(self) -> Path:
        return self._state_dir_path

    def _assert_not_poisoned(self) -> None:
        if self._is_poisoned:
            raise DispatchJournalPoisonError(
                f"ERR_JOURNAL_POISONED: Journal is permanently poisoned: {self._poison_reason}"
            )

    def _poison(self, reason: str) -> None:
        self._is_poisoned = True
        self._poison_reason = reason

    def _validate_node_mode_and_ownership(
        self,
        fd: int,
        node_name: str,
        *,
        expect_dir: bool = False,
    ) -> os.stat_result:
        """
        Validates the descriptor-observed exact mode and ownership policy (DEP03-R3-03).
        Directories (.locks) must have mode 0700.
        All journal, WAL, audit, state, receipt, marker, and lock files must have mode 0600.
        Permissive or unsafe modes immediately fail closed.
        """
        st = os.fstat(fd)
        if expect_dir:
            if not stat.S_ISDIR(st.st_mode):
                raise DispatchJournalSecurityError(f"ERR_NOT_DIR: {node_name} is not a directory")
            mode = stat.S_IMODE(st.st_mode)
            if mode != 0o700:
                raise DispatchJournalSecurityError(
                    f"ERR_UNSAFE_DIR_MODE: Directory {node_name} mode 0o{mode:o} != 0o700"
                )
        else:
            if not stat.S_ISREG(st.st_mode):
                raise DispatchJournalSecurityError(f"ERR_NOT_REGULAR: {node_name} is not a regular file")
            if st.st_nlink != 1:
                raise DispatchJournalSecurityError(f"ERR_LINK_COUNT_ANOMALY: {node_name} link count {st.st_nlink} != 1")
            mode = stat.S_IMODE(st.st_mode)
            if mode != 0o600:
                raise DispatchJournalSecurityError(
                    f"ERR_UNSAFE_FILE_MODE: File {node_name} mode 0o{mode:o} != 0o600"
                )

        if self._enforce_ownership:
            if st.st_uid != self._expected_uid:
                raise DispatchJournalSecurityError(
                    f"ERR_WRONG_OWNER_UID: {node_name} uid {st.st_uid} != expected {self._expected_uid}"
                )
            if self._expected_gid is not None and st.st_gid != self._expected_gid:
                raise DispatchJournalSecurityError(
                    f"ERR_WRONG_GROUP_GID: {node_name} gid {st.st_gid} != expected {self._expected_gid}"
                )

        return st

    def _initialize_descriptor_storage(self) -> None:
        """Traverses and opens state directory descriptor and .locks directory descriptor."""
        self._state_dir_fd = _open_dir_nofollow_traversal(self._state_dir_path)

        st = os.fstat(self._state_dir_fd)
        if not stat.S_ISDIR(st.st_mode):
            raise DispatchJournalSecurityError(
                f"ERR_STATE_DIR_NOT_DIR: state_dir fd is not a directory: {self._state_dir_path}"
            )

        if self._enforce_ownership:
            if st.st_uid != self._expected_uid:
                raise DispatchJournalSecurityError(
                    f"ERR_WRONG_OWNER_UID: state_dir uid {st.st_uid} != expected {self._expected_uid}"
                )
            if self._expected_gid is not None and st.st_gid != self._expected_gid:
                raise DispatchJournalSecurityError(
                    f"ERR_WRONG_GROUP_GID: state_dir gid {st.st_gid} != expected {self._expected_gid}"
                )
            mode = stat.S_IMODE(st.st_mode)
            if mode not in (0o700, 0o2750, 0o750):
                raise DispatchJournalSecurityError(
                    f"ERR_INVALID_MODE: state_dir mode 0o{mode:o} is not permitted"
                )

        # Create / open .locks dir relative to state_dir_fd
        try:
            os.mkdir(self.LOCKS_DIR_NAME, 0o700, dir_fd=self._state_dir_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise DispatchJournalSecurityError(f"ERR_CANNOT_CREATE_LOCKS_DIR: {exc}") from exc

        try:
            self._locks_dir_fd = os.open(
                self.LOCKS_DIR_NAME,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._state_dir_fd,
            )
        except OSError as exc:
            raise DispatchJournalSecurityError(f"ERR_CANNOT_OPEN_LOCKS_DIR: {exc}") from exc

        # DEP03-R3-03: Validate exact mode 0700 on .locks directory
        self._validate_node_mode_and_ownership(self._locks_dir_fd, self.LOCKS_DIR_NAME, expect_dir=True)

        # Initial directory fsync
        os.fsync(self._state_dir_fd)

    def _atomic_publish_file(self, parent_dir_fd: int, filename: str, data: bytes) -> None:
        """
        Commits data using descriptor-anchored 4-Phase Atomic Replacement & fsync Protocol (§5.2.1).
        All opens, fsyncs, and replaces are strictly anchored to parent_dir_fd with O_NOFOLLOW.
        DEP03-R3-03: Pre-existing target files are validated for safe mode 0600 before replacement.
        """
        # Validate pre-existing file mode before replacement (DEP03-R3-03)
        try:
            target_check_fd = os.open(
                filename,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_dir_fd,
            )
            try:
                self._validate_node_mode_and_ownership(target_check_fd, filename)
            finally:
                _safe_close(target_check_fd)
        except FileNotFoundError:
            pass

        tmp_name = f".{filename}.tmp.{secrets.token_hex(16)}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        tmp_fd: Optional[int] = None

        try:
            # Phase 1: Open unique temp file relative to parent directory descriptor
            tmp_fd = os.open(tmp_name, flags, 0o600, dir_fd=parent_dir_fd)
            self._validate_node_mode_and_ownership(tmp_fd, tmp_name)

            # Phase 2: Payload write & file fsync
            _write_all(tmp_fd, data)
            os.fsync(tmp_fd)
            os.close(tmp_fd)
            tmp_fd = None

            # Phase 3: POSIX descriptor-relative atomic replacement
            os.replace(tmp_name, filename, src_dir_fd=parent_dir_fd, dst_dir_fd=parent_dir_fd)

            # Phase 4: Parent directory fsync via descriptor
            os.fsync(parent_dir_fd)
        except Exception as exc:
            if tmp_fd is not None:
                _safe_close(tmp_fd)
            try:
                os.unlink(tmp_name, dir_fd=parent_dir_fd)
            except OSError:
                pass
            if isinstance(exc, DispatchJournalSecurityError):
                raise
            self._poison(f"Atomic file publish failure for {filename}: {exc}")
            raise DispatchJournalPoisonError(
                f"ERR_ATOMIC_PUBLISH_FAILED: {filename}: {exc}"
            ) from exc

    def _append_wal_record(self, record_dict: Dict[str, Any]) -> None:
        """Appends a single JSON line record to execution_journal.wal with fsync via state_dir_fd."""
        self._assert_not_poisoned()
        assert self._state_dir_fd is not None, "state_dir_fd is not open"

        line_bytes = json.dumps(record_dict, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd: Optional[int] = None
        try:
            fd = os.open(self.WAL_FILE_NAME, flags, 0o600, dir_fd=self._state_dir_fd)
            # DEP03-R3-03: Validate exact file mode and ownership
            self._validate_node_mode_and_ownership(fd, self.WAL_FILE_NAME)

            _write_all(fd, line_bytes)
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.fsync(self._state_dir_fd)
            self._wal_records.append(record_dict)
        except Exception as exc:
            if fd is not None:
                _safe_close(fd)
            if isinstance(exc, DispatchJournalSecurityError):
                raise
            self._poison(f"WAL append failure: {exc}")
            raise DispatchJournalPoisonError(f"ERR_WAL_APPEND_FAILED: {exc}") from exc

    def _append_audit_intent(self, record_dict: Dict[str, Any]) -> None:
        """Appends an intent record to execution.log with fsync via state_dir_fd."""
        self._assert_not_poisoned()
        assert self._state_dir_fd is not None, "state_dir_fd is not open"

        line_bytes = json.dumps(record_dict, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd: Optional[int] = None
        try:
            fd = os.open(self.AUDIT_LOG_NAME, flags, 0o600, dir_fd=self._state_dir_fd)
            # DEP03-R3-03: Validate exact file mode and ownership
            self._validate_node_mode_and_ownership(fd, self.AUDIT_LOG_NAME)

            _write_all(fd, line_bytes)
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.fsync(self._state_dir_fd)
        except Exception as exc:
            if fd is not None:
                _safe_close(fd)
            if isinstance(exc, DispatchJournalSecurityError):
                raise
            self._poison(f"Audit append failure: {exc}")
            raise DispatchJournalPoisonError(f"ERR_AUDIT_APPEND_FAILED: {exc}") from exc

    def compute_target_hash(self, project_uid: str, timeline_uid: str) -> str:
        """Returns deterministic 64-hex hash of project_uid:timeline_uid."""
        return hashlib.sha256(f"{project_uid}:{timeline_uid}".encode("utf-8")).hexdigest().lower()

    def _read_file_bytes(self, parent_dir_fd: int, filename: str) -> Optional[bytes]:
        """Reads file bytes securely via descriptor and O_NOFOLLOW, verifying exact mode (DEP03-R3-03)."""
        try:
            fd = os.open(
                filename,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_dir_fd,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise DispatchJournalCorruptionError(f"ERR_OPEN_FAILED: {filename}: {exc}") from exc

        try:
            # DEP03-R3-03: Validate exact file mode 0600 and ownership
            self._validate_node_mode_and_ownership(fd, filename)

            chunks: List[bytes] = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            _safe_close(fd)

    def _recover_state(self) -> None:
        """Scans disk state via descriptors, recovers ledger and WAL, and detects in-doubt conditions (§5.2.3)."""
        with self._lock:
            assert self._locks_dir_fd is not None, "locks_dir_fd is not open"
            assert self._state_dir_fd is not None, "state_dir_fd is not open"

            # 1. Scan .locks for existing .in_doubt and .lock nodes using descriptor
            with os.scandir(self._locks_dir_fd) as it:
                for entry in it:
                    if entry.name.endswith(".in_doubt"):
                        entry_fd = os.open(
                            entry.name,
                            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=self._locks_dir_fd,
                        )
                        try:
                            self._validate_node_mode_and_ownership(entry_fd, entry.name)
                            thash = entry.name[:-len(".in_doubt")]
                            self._in_doubt_targets.add(thash)
                        finally:
                            _safe_close(entry_fd)
                    elif entry.name.endswith(".lock"):
                        entry_fd = os.open(
                            entry.name,
                            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=self._locks_dir_fd,
                        )
                        try:
                            self._validate_node_mode_and_ownership(entry_fd, entry.name)
                        finally:
                            _safe_close(entry_fd)

            # 2. Recover target_ledger.v2.json if present
            raw_ledger = self._read_file_bytes(self._state_dir_fd, self.TARGET_LEDGER_NAME)
            if raw_ledger is not None:
                try:
                    data = parse_strict_json(raw_ledger)
                    self._target_state = data
                    state_status = data.get("status")
                    t_hash = data.get("target_hash")
                    if t_hash and state_status in ("IN_DOUBT_LOCKED", "ACTIVE", "RESERVED"):
                        if state_status == "IN_DOUBT_LOCKED":
                            self._in_doubt_targets.add(t_hash)
                    if data.get("execution_id"):
                        self._committed_executions.add(data["execution_id"])
                except Exception as exc:
                    if isinstance(exc, DispatchJournalSecurityError):
                        raise
                    raise DispatchJournalCorruptionError(f"ERR_CORRUPT_TARGET_LEDGER: {exc}") from exc

            # 3. Recover execution_journal.wal if present
            raw_wal = self._read_file_bytes(self._state_dir_fd, self.WAL_FILE_NAME)
            unmatched_pending: Dict[str, Dict[str, Any]] = {}
            if raw_wal is not None:
                try:
                    lines = raw_wal.split(b"\n")
                    for line in lines:
                        sline = line.strip()
                        if not sline:
                            continue
                        rec = parse_strict_json(sline)
                        self._wal_records.append(rec)
                        exec_id = rec.get("execution_id")
                        rec_type = rec.get("record_type")
                        if exec_id:
                            self._committed_executions.add(exec_id)
                        if rec_type == "ACTION_PENDING":
                            unmatched_pending[exec_id] = rec
                        elif rec_type in ("ACTION_COMMITTED", "ACTION_ABORTED", "FINISH"):
                            unmatched_pending.pop(exec_id, None)
                except Exception as exc:
                    if isinstance(exc, DispatchJournalSecurityError):
                        raise
                    raise DispatchJournalCorruptionError(f"ERR_CORRUPT_WAL: {exc}") from exc

            # 4. If any ACTION_PENDING is unmatched, force IN_DOUBT_LOCKED (§5.2.3 Priority 2)
            for exec_id, pending_rec in unmatched_pending.items():
                proj_uid = pending_rec.get("target_project_uid", "")
                tl_uid = pending_rec.get("target_timeline_uid", "")
                if proj_uid and tl_uid:
                    thash = self.compute_target_hash(proj_uid, tl_uid)
                    self._in_doubt_targets.add(thash)
                    # Reconstruct .in_doubt marker if absent
                    marker_name = f"{thash}.in_doubt"
                    try:
                        marker_fd = os.open(
                            marker_name,
                            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=self._locks_dir_fd,
                        )
                        try:
                            self._validate_node_mode_and_ownership(marker_fd, marker_name)
                        finally:
                            _safe_close(marker_fd)
                    except FileNotFoundError:
                        marker_data = json.dumps({
                            "target_hash": thash,
                            "target_project_uid": proj_uid,
                            "target_timeline_uid": tl_uid,
                            "execution_id": exec_id,
                            "reason": "CRASH_RECOVERY_UNMATCHED_ACTION_PENDING",
                            "timestamp_iso": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }, sort_keys=True).encode("utf-8")
                        self._atomic_publish_file(self._locks_dir_fd, marker_name, marker_data)

    def is_target_locked_or_in_doubt(self, project_uid: str, timeline_uid: str) -> bool:
        """Returns True if the target has an active .in_doubt marker, is in-doubt locked, or active."""
        thash = self.compute_target_hash(project_uid, timeline_uid)
        with self._lock:
            if thash in self._in_doubt_targets:
                return True
            if self._locks_dir_fd is not None:
                marker_name = f"{thash}.in_doubt"
                try:
                    marker_fd = os.open(
                        marker_name,
                        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=self._locks_dir_fd,
                    )
                    try:
                        self._validate_node_mode_and_ownership(marker_fd, marker_name)
                        return True
                    finally:
                        _safe_close(marker_fd)
                except (FileNotFoundError, OSError):
                    pass
            if self._target_state.get("target_hash") == thash:
                status = self._target_state.get("status")
                if status in ("IN_DOUBT_LOCKED", "ACTIVE", "RESERVED", "EXHAUSTED"):
                    return True
            return False

    def is_execution_id_committed(self, execution_id: str) -> bool:
        """Returns True if execution_id has any prior record in ledger or WAL."""
        with self._lock:
            return execution_id in self._committed_executions

    def write_audit_intent(
        self,
        *,
        execution_id: str,
        action_payload_digest_sha256: str,
        target_project_uid: str,
        target_timeline_uid: str,
    ) -> None:
        """Step 1: Write-Ahead Audit Intent fsync (§5.2.2 Step 1)."""
        with self._lock:
            self._assert_not_poisoned()
            rec = {
                "record_type": "DISPATCH_ATTEMPT_INTENT",
                "execution_id": execution_id,
                "action_payload_digest_sha256": action_payload_digest_sha256,
                "target_project_uid": target_project_uid,
                "target_timeline_uid": target_timeline_uid,
                "timestamp_iso": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            self._append_audit_intent(rec)

    def reserve_target(
        self,
        *,
        execution_id: str,
        target_project_uid: str,
        target_timeline_uid: str,
    ) -> None:
        """Step 2: Durable Target Ledger Reservation fsync (§5.2.2 Step 2)."""
        with self._lock:
            self._assert_not_poisoned()
            assert self._state_dir_fd is not None, "state_dir_fd is not open"

            thash = self.compute_target_hash(target_project_uid, target_timeline_uid)
            if thash in self._in_doubt_targets:
                raise DispatchJournalInDoubtError(
                    f"ERR_TARGET_IN_DOUBT_LOCKED: Target {target_project_uid}:{target_timeline_uid} is locked in doubt"
                )
            if self._target_state:
                prev_status = self._target_state.get("status")
                if prev_status in ("RESERVED", "ACTIVE", "EXHAUSTED", "IN_DOUBT_LOCKED"):
                    raise DispatchJournalSecurityError(
                        f"ERR_TARGET_NOT_AVAILABLE: Target status is {prev_status}"
                    )
            if execution_id in self._committed_executions:
                raise DispatchJournalSecurityError(
                    f"ERR_EXECUTION_ALREADY_COMMITTED: Execution ID {execution_id} is already committed"
                )

            data = {
                "schema_version": 2,
                "target_hash": thash,
                "target_project_uid": target_project_uid,
                "target_timeline_uid": target_timeline_uid,
                "execution_id": execution_id,
                "status": "RESERVED",
                "reserved_at_iso": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            payload_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self._atomic_publish_file(self._state_dir_fd, self.TARGET_LEDGER_NAME, payload_bytes)
            self._target_state = data
            self._committed_executions.add(execution_id)

    def write_action_pending(
        self,
        *,
        execution_id: str,
        action_payload_digest_sha256: str,
        target_project_uid: str,
        target_timeline_uid: str,
    ) -> None:
        """Step 3: Durable WAL Reservation fsync (§5.2.2 Step 3)."""
        with self._lock:
            self._assert_not_poisoned()
            assert self._state_dir_fd is not None, "state_dir_fd is not open"

            seq = len(self._wal_records) + 1
            rec = {
                "schema_version": 1,
                "seq": seq,
                "record_type": "ACTION_PENDING",
                "execution_id": execution_id,
                "action_payload_digest_sha256": action_payload_digest_sha256,
                "target_project_uid": target_project_uid,
                "target_timeline_uid": target_timeline_uid,
                "timestamp_iso": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            self._append_wal_record(rec)

            # Update target state to ACTIVE
            self._target_state["status"] = "ACTIVE"
            self._target_state["action_pending_at_iso"] = rec["timestamp_iso"]
            payload_bytes = json.dumps(self._target_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self._atomic_publish_file(self._state_dir_fd, self.TARGET_LEDGER_NAME, payload_bytes)

    def acquire_target_lock(self, target_project_uid: str, target_timeline_uid: str) -> None:
        """Step 4: Target exclusion lock via non-blocking fcntl.flock (§5.2.2 Step 4, DEP03-R2-06, DEP03-R3-03)."""
        with self._lock:
            self._assert_not_poisoned()
            assert self._locks_dir_fd is not None, "locks_dir_fd is not open"

            thash = self.compute_target_hash(target_project_uid, target_timeline_uid)
            if thash in self._active_locks:
                return  # Already holding lock

            lock_name = f"{thash}.lock"
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            fd: Optional[int] = None
            try:
                fd = os.open(lock_name, flags, 0o600, dir_fd=self._locks_dir_fd)
                # DEP03-R3-03: Validate exact file mode 0600 and ownership on lock file
                self._validate_node_mode_and_ownership(fd, lock_name)

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._active_locks[thash] = fd
            except (BlockingIOError, OSError) as exc:
                if fd is not None:
                    _safe_close(fd)
                if isinstance(exc, DispatchJournalSecurityError):
                    raise
                raise DispatchJournalLockError(
                    f"ERR_TARGET_LOCK_CONTENTION: Could not acquire lock for target {target_project_uid}:{target_timeline_uid}: {exc}"
                ) from exc

    def release_target_lock(self, target_project_uid: str, target_timeline_uid: str) -> None:
        """
        Releases the target exclusion lock after verified clean terminal state.
        Preserves stable lock file inode: DO NOT unlink the .lock file (DEP03-R2-06).
        """
        with self._lock:
            thash = self.compute_target_hash(target_project_uid, target_timeline_uid)
            fd = self._active_locks.pop(thash, None)
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                _safe_close(fd)
            # DO NOT unlink .lock file! Retaining stable inode avoids inode split races.

    def commit_terminal(
        self,
        *,
        execution_id: str,
        terminal_state: str,
        target_project_uid: str,
        target_timeline_uid: str,
        receipt_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Step 8: Durable Finalization fsync (§5.2.2 Step 8)."""
        if terminal_state not in VALID_TERMINAL_STATES:
            raise DispatchJournalSecurityError(
                f"ERR_INVALID_TERMINAL_STATE: {terminal_state} not in {sorted(VALID_TERMINAL_STATES)}"
            )

        with self._lock:
            self._assert_not_poisoned()
            assert self._state_dir_fd is not None, "state_dir_fd is not open"
            assert self._locks_dir_fd is not None, "locks_dir_fd is not open"

            thash = self.compute_target_hash(target_project_uid, target_timeline_uid)
            now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            if terminal_state == "COMPLETED":
                # Branch 1: Verified Success
                seq1 = len(self._wal_records) + 1
                self._append_wal_record({
                    "schema_version": 1,
                    "seq": seq1,
                    "record_type": "ACTION_COMMITTED",
                    "execution_id": execution_id,
                    "timestamp_iso": now_iso,
                })
                seq2 = len(self._wal_records) + 1
                self._append_wal_record({
                    "schema_version": 1,
                    "seq": seq2,
                    "record_type": "FINISH",
                    "status": "COMPLETED",
                    "execution_id": execution_id,
                    "timestamp_iso": now_iso,
                })

                # Publish receipt
                if receipt_data:
                    receipt_bytes = json.dumps(receipt_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    self._atomic_publish_file(self._state_dir_fd, self.RECEIPT_FILE_NAME, receipt_bytes)

                # Target ledger: EXHAUSTED(COMPLETED)
                self._target_state["status"] = "EXHAUSTED"
                self._target_state["terminal_result"] = "COMPLETED"
                self._target_state["completed_at_iso"] = now_iso
                ledger_bytes = json.dumps(self._target_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
                self._atomic_publish_file(self._state_dir_fd, self.TARGET_LEDGER_NAME, ledger_bytes)

            elif terminal_state == "ABORTED":
                # Branch 2: Clean Abort
                seq1 = len(self._wal_records) + 1
                self._append_wal_record({
                    "schema_version": 1,
                    "seq": seq1,
                    "record_type": "ACTION_ABORTED",
                    "execution_id": execution_id,
                    "timestamp_iso": now_iso,
                })
                seq2 = len(self._wal_records) + 1
                self._append_wal_record({
                    "schema_version": 1,
                    "seq": seq2,
                    "record_type": "FINISH",
                    "status": "ABORTED",
                    "execution_id": execution_id,
                    "timestamp_iso": now_iso,
                })

                # Publish receipt
                if receipt_data:
                    receipt_bytes = json.dumps(receipt_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    self._atomic_publish_file(self._state_dir_fd, self.RECEIPT_FILE_NAME, receipt_bytes)

                # Target ledger: EXHAUSTED(ABORTED)
                self._target_state["status"] = "EXHAUSTED"
                self._target_state["terminal_result"] = "ABORTED"
                self._target_state["aborted_at_iso"] = now_iso
                ledger_bytes = json.dumps(self._target_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
                self._atomic_publish_file(self._state_dir_fd, self.TARGET_LEDGER_NAME, ledger_bytes)

            elif terminal_state == "IN_DOUBT":
                # Branch 3: In-Doubt
                # a) NO WAL FINISH IS WRITTEN!
                # b) Target ledger: IN_DOUBT_LOCKED
                self._target_state["status"] = "IN_DOUBT_LOCKED"
                self._target_state["terminal_result"] = "IN_DOUBT"
                self._target_state["in_doubt_at_iso"] = now_iso
                ledger_bytes = json.dumps(self._target_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
                self._atomic_publish_file(self._state_dir_fd, self.TARGET_LEDGER_NAME, ledger_bytes)

                # c) Persistent in-doubt marker file
                self._in_doubt_targets.add(thash)
                marker_name = f"{thash}.in_doubt"
                marker_data = json.dumps({
                    "target_hash": thash,
                    "target_project_uid": target_project_uid,
                    "target_timeline_uid": target_timeline_uid,
                    "execution_id": execution_id,
                    "reason": "TERMINAL_IN_DOUBT_CLASSIFICATION",
                    "timestamp_iso": now_iso,
                }, sort_keys=True).encode("utf-8")
                self._atomic_publish_file(self._locks_dir_fd, marker_name, marker_data)

    def force_in_doubt_locked(
        self,
        *,
        target_project_uid: str,
        target_timeline_uid: str,
        execution_id: str,
        reason: str,
    ) -> None:
        """Forces an in-doubt locked condition on target, creating descriptor-anchored .in_doubt marker."""
        with self._lock:
            assert self._state_dir_fd is not None, "state_dir_fd is not open"
            assert self._locks_dir_fd is not None, "locks_dir_fd is not open"

            thash = self.compute_target_hash(target_project_uid, target_timeline_uid)
            now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._in_doubt_targets.add(thash)

            # Update target state if present
            self._target_state["status"] = "IN_DOUBT_LOCKED"
            self._target_state["in_doubt_reason"] = reason
            self._target_state["in_doubt_at_iso"] = now_iso
            try:
                ledger_bytes = json.dumps(self._target_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
                self._atomic_publish_file(self._state_dir_fd, self.TARGET_LEDGER_NAME, ledger_bytes)
            except Exception:
                pass

            marker_name = f"{thash}.in_doubt"
            marker_data = json.dumps({
                "target_hash": thash,
                "target_project_uid": target_project_uid,
                "target_timeline_uid": target_timeline_uid,
                "execution_id": execution_id,
                "reason": reason,
                "timestamp_iso": now_iso,
            }, sort_keys=True).encode("utf-8")
            self._atomic_publish_file(self._locks_dir_fd, marker_name, marker_data)

    def close(self) -> None:
        """Closes all active locks and descriptor handles safely."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

            # Release flock and close active lock fds
            for thash, fd in list(self._active_locks.items()):
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                _safe_close(fd)
            self._active_locks.clear()

            # Close directory descriptors
            if self._locks_dir_fd is not None:
                _safe_close(self._locks_dir_fd)
                self._locks_dir_fd = None

            if self._state_dir_fd is not None:
                _safe_close(self._state_dir_fd)
                self._state_dir_fd = None
