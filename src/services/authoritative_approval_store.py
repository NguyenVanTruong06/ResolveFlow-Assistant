"""Authoritative Approval Store (G07-07B7-I2).

Append-only, immutable local filesystem store for structurally validated SignedApprovalEnvelope records.

Safety Boundary:
- Pure persistence slice: records exact complete-envelope bytes and retrieves by approval_id.
- Does not verify Director digital signatures or confer dispatch authorization.
- Uses directory descriptor anchoring, restrictive permissions (0700 dir, 0600 file), atomic
  no-overwrite publication (exclusive temp -> fsync -> linkat -> dir fsync), and cross-process
  advisory locking (fcntl.flock).
- Zero Resolve IPC, zero keys accessed, zero network operations.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import stat
import threading
import uuid
from typing import Any, Dict, List, Optional

from src.core.contracts.signed_approval_envelope import (
    SignedApprovalEnvelope,
    SignedApprovalEnvelopeValidationError,
)
from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    StrictJsonComplexityError,
    StrictJsonParseError,
    parse_strict_canonical_json,
)

MAX_RECORD_BYTES: int = 65_536  # 64 KiB strict bounded size for approval records
EXPECTED_DIR_MODE_MASK: int = 0o077   # Mode 0700: rejects group/other read/write/execute
EXPECTED_FILE_MODE_MASK: int = 0o077  # Mode 0600: rejects group/other read/write/execute

APPROVAL_FILE_PREFIX: str = "approval_"
APPROVAL_FILE_SUFFIX: str = ".envelope.json"
LOCK_FILE_NAME: str = ".store.lock"

CLOSE_OUTCOME_UNKNOWN: str = "CLOSE_OUTCOME_UNKNOWN"


class ApprovalStoreError(AcceptanceGateError, ValueError):
    """Base exception for all authoritative approval store errors."""
    pass


class ApprovalRecordAlreadyExistsError(ApprovalStoreError):
    """Raised when attempting to overwrite or repeat write an existing immutable approval record."""
    pass


class ApprovalStoreSecurityError(ApprovalStoreError):
    """Raised when directory or file symlinks, permissions, or ownership violate security policy."""
    pass


class ApprovalStoreCorruptionError(ApprovalStoreError):
    """Raised when persisted bytes are corrupt, tampered with, or fail schema verification."""
    pass


class ApprovalStoreDurabilityError(ApprovalStoreError):
    """Raised when file write, fsync, or atomic publication encounters durability failures."""
    pass


class ApprovalStoreInitializationError(ApprovalStoreError):
    """Raised when store initialization fails, preserving original error, cleanup errors, and diagnostic state.

    WARNING: Descriptors listed in `ambiguous_close_descriptors` (or exposed via `cleanup_failures`)
    failed during close(). Under POSIX semantics, an `os.close()` failure does NOT prove that the
    descriptor remains open; the kernel may have already deallocated or reused the numeric descriptor.
    Their outcome is explicitly marked as CLOSE_OUTCOME_UNKNOWN. Callers and operators MUST NEVER
    attempt to retry os.close() on these numeric descriptor values, as doing so risks closing a newly
    allocated descriptor belonging to another thread or subsystem.
    """

    def __init__(
        self,
        message: str,
        *,
        primary_cause: BaseException,
        cleanup_error: Optional[BaseException] = None,
        ambiguous_close_descriptors: Optional[List[tuple[str, int, Exception, str]]] = None,
        store_instance: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.primary_cause = primary_cause
        self.cleanup_error = cleanup_error
        self.ambiguous_close_descriptors = ambiguous_close_descriptors or []
        self.store_instance = store_instance

    @property
    def unclosed_descriptors(self) -> List[tuple[str, int, Exception, str]]:
        """Deprecated alias for ambiguous_close_descriptors.

        WARNING: Outcomes are CLOSE_OUTCOME_UNKNOWN under POSIX semantics. Never retry closing.
        """
        return self.ambiguous_close_descriptors


class AuthoritativeApprovalStore:
    """Local, append-only, tamper-evident store for SignedApprovalEnvelope records.

    Provides atomic publication and safe retrieval anchored by an operating system directory descriptor.
    """

    def __init__(self, store_dir: str, *, create: bool = True) -> None:
        if not isinstance(store_dir, str) or not store_dir.strip():
            raise ApprovalStoreError("ERR_INVALID_STORE_DIR: store_dir must be a non-empty string")

        self._store_dir = os.path.abspath(store_dir)
        self._lock = threading.RLock()
        self._dir_fd: Optional[int] = None
        self._lock_fd: Optional[int] = None
        self._closed: bool = False
        self._lock_corrupted: bool = False
        self._cleanup_failures: List[tuple[str, int, Exception, str]] = []

        try:
            if create and not os.path.exists(self._store_dir):
                try:
                    os.makedirs(self._store_dir, mode=0o700, exist_ok=True)
                    os.chmod(self._store_dir, 0o700)
                except OSError as ce:
                    raise ApprovalStoreError(f"ERR_CANNOT_CREATE_STORE_DIR: failed creating {self._store_dir}: {ce}") from ce

            # Check symlink on path before opening
            if os.path.islink(self._store_dir):
                raise ApprovalStoreSecurityError(f"ERR_DIR_SYMLINK_DETECTED: store_dir '{self._store_dir}' is a symlink")

            dir_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )

            try:
                self._dir_fd = os.open(self._store_dir, dir_flags)
            except OSError as e:
                if e.errno == errno.ELOOP or (e.errno == errno.ENOTDIR and os.path.islink(self._store_dir)):
                    raise ApprovalStoreSecurityError(f"ERR_DIR_SYMLINK_DETECTED: store_dir '{self._store_dir}' is a symlink: {e}") from e
                if e.errno == errno.ENOTDIR:
                    raise ApprovalStoreSecurityError(f"ERR_STORE_NOT_DIR: store_dir '{self._store_dir}' is not a directory: {e}") from e
                raise ApprovalStoreError(f"ERR_STORE_CANNOT_OPEN: cannot open store directory '{self._store_dir}': {e}") from e

            # Validate directory descriptor attributes
            st = os.fstat(self._dir_fd)
            if not stat.S_ISDIR(st.st_mode):
                raise ApprovalStoreSecurityError(f"ERR_STORE_NOT_DIR: store_dir '{self._store_dir}' is not a directory")
            if hasattr(os, "getuid") and st.st_uid != os.getuid():
                raise ApprovalStoreSecurityError(f"ERR_STORE_INVALID_OWNER: store_dir uid {st.st_uid} != current uid {os.getuid()}")
            if (st.st_mode & EXPECTED_DIR_MODE_MASK) != 0:
                raise ApprovalStoreSecurityError(
                    f"ERR_STORE_INSECURE_PERMS: store_dir mode must be 0700, got {oct(st.st_mode)}"
                )

            # Open internal lock descriptor for cross-process mutual exclusion
            lock_flags = (
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                self._lock_fd = os.open(LOCK_FILE_NAME, lock_flags, 0o600, dir_fd=self._dir_fd)
            except OSError as le:
                if le.errno in (errno.EISDIR, errno.ENOTDIR):
                    raise ApprovalStoreSecurityError(
                        f"ERR_LOCK_NOT_REGULAR: lock file '{LOCK_FILE_NAME}' is not a regular file: {le}"
                    ) from le
                if le.errno == errno.ELOOP:
                    raise ApprovalStoreSecurityError(
                        f"ERR_LOCK_SYMLINK_DETECTED: lock file '{LOCK_FILE_NAME}' is a symlink: {le}"
                    ) from le
                raise ApprovalStoreError(f"ERR_LOCK_OPEN_FAILED: failed opening lock file: {le}") from le

            lock_st = os.fstat(self._lock_fd)
            if not stat.S_ISREG(lock_st.st_mode):
                raise ApprovalStoreSecurityError(f"ERR_LOCK_NOT_REGULAR: lock file '{LOCK_FILE_NAME}' is not a regular file")
            if lock_st.st_nlink != 1:
                raise ApprovalStoreSecurityError(
                    f"ERR_LOCK_HARD_LINK_DETECTED: lock file '{LOCK_FILE_NAME}' has link count {lock_st.st_nlink} != 1"
                )
            if hasattr(os, "getuid") and lock_st.st_uid != os.getuid():
                raise ApprovalStoreSecurityError(f"ERR_LOCK_INVALID_OWNER: lock uid {lock_st.st_uid} != {os.getuid()}")
            if (lock_st.st_mode & 0o777) != 0o600:
                raise ApprovalStoreSecurityError(
                    f"ERR_LOCK_INSECURE_PERMS: lock file '{LOCK_FILE_NAME}' mode must be strictly 0600, got {oct(lock_st.st_mode & 0o777)}"
                )
        except BaseException as init_exc:
            cleanup_exc: Optional[Exception] = None
            try:
                self.close()
            except Exception as ce:
                cleanup_exc = ce

            if cleanup_exc is not None:
                msg = (
                    f"ERR_STORE_INIT_FAILED_WITH_CLEANUP_ERROR: initialization failed with {init_exc}; "
                    f"subsequent descriptor cleanup failed: {cleanup_exc}"
                )
                err = ApprovalStoreInitializationError(
                    msg,
                    primary_cause=init_exc,
                    cleanup_error=cleanup_exc,
                    ambiguous_close_descriptors=list(self._cleanup_failures),
                    store_instance=self,
                )
                err.__cause__ = init_exc
                raise err from init_exc
            raise

    @property
    def store_dir(self) -> str:
        """Returns the verified absolute path of the store directory."""
        return self._store_dir

    @property
    def cleanup_failures(self) -> List[tuple[str, int, Exception, str]]:
        """Exposes diagnostic information about descriptors whose close() encountered errors.

        Entries are tuples of (descriptor_name, fd, os_error, outcome).
        Under POSIX single-close semantics, outcome is strictly CLOSE_OUTCOME_UNKNOWN.
        Callers and operators MUST NEVER retry closing these numeric descriptor values.
        """
        return list(self._cleanup_failures)

    def _ensure_open_locked(self) -> None:
        """Checks open state; caller MUST already hold self._lock."""
        if self._closed or self._dir_fd is None or self._lock_fd is None:
            raise ApprovalStoreError("ERR_STORE_CLOSED: operation attempted on closed AuthoritativeApprovalStore")
        if self._lock_corrupted:
            raise ApprovalStoreError("ERR_STORE_LOCK_CORRUPTED: store lock state is corrupted; store is latched closed")

    def _record_filename(self, approval_id: str) -> str:
        """Derives a safe, collision-resistant opaque record basename from approval_id SHA-256."""
        if type(approval_id) is not str or not approval_id or approval_id != approval_id.strip():
            raise ApprovalStoreError(
                f"ERR_INVALID_APPROVAL_ID: approval_id must be a non-empty string without whitespace padding, got {approval_id!r}"
            )
        record_hash = hashlib.sha256(approval_id.encode("utf-8")).hexdigest()
        return f"{APPROVAL_FILE_PREFIX}{record_hash}{APPROVAL_FILE_SUFFIX}"

    def put(self, envelope: SignedApprovalEnvelope) -> None:
        """Persists a complete SignedApprovalEnvelope with atomic no-replace guarantees.

        Requirements:
        - Rejects any type that is not strictly SignedApprovalEnvelope (including mappings and subclasses).
        - Existing records are strictly immutable; duplicate IDs fail closed without altering stored bytes.
        - Single-close descriptor semantics: never retries failed close on numeric descriptor.
        - Strict lock release error checking: latch store closed and surface failure on unlock errors.
        """
        if type(envelope) is not SignedApprovalEnvelope:
            raise TypeError(
                f"ERR_INVALID_TYPE: expected exact SignedApprovalEnvelope, got {type(envelope).__name__}"
            )

        approval_id = envelope.approval_record.approval_id
        final_fname = self._record_filename(approval_id)
        raw_bytes = envelope.canonical_envelope_bytes()

        if len(raw_bytes) > MAX_RECORD_BYTES:
            raise ApprovalStoreCorruptionError(
                f"ERR_BYTE_BUDGET_EXCEEDED: serialized envelope size ({len(raw_bytes)} bytes) exceeds bound {MAX_RECORD_BYTES}"
            )

        with self._lock:
            self._ensure_open_locked()
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            except OSError as fe:
                self._lock_corrupted = True
                self._closed = True
                raise ApprovalStoreDurabilityError(f"ERR_LOCK_ACQUIRE_FAILED: failed acquiring exclusive lock: {fe}") from fe

            tmp_fname = f".tmp_{uuid.uuid4().hex}_{os.getpid()}.tmp"
            tmp_fd: Optional[int] = None
            tmp_created = False
            published = False
            primary_exc: Optional[BaseException] = None
            cleanup_errors: List[str] = []

            try:
                # 1. Pre-check: existing record rejection (fail-closed before writing temp)
                try:
                    os.stat(final_fname, dir_fd=self._dir_fd)
                    raise ApprovalRecordAlreadyExistsError(
                        f"ERR_APPROVAL_RECORD_ALREADY_EXISTS: record for approval_id '{approval_id}' already exists"
                    )
                except FileNotFoundError:
                    pass  # File does not exist yet, proceed with atomic publication

                # 2. Exclusive temporary file creation inside anchored directory
                open_flags = (
                    os.O_CREAT
                    | os.O_EXCL
                    | os.O_WRONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                try:
                    tmp_fd = os.open(tmp_fname, open_flags, 0o600, dir_fd=self._dir_fd)
                    tmp_created = True
                except OSError as oe:
                    raise ApprovalStoreDurabilityError(f"ERR_TEMP_CREATE_FAILED: failed creating temp file: {oe}") from oe

                # 3. Complete write loop with EINTR retry
                total_written = 0
                to_write = len(raw_bytes)
                while total_written < to_write:
                    try:
                        written = os.write(tmp_fd, raw_bytes[total_written:])
                        if written == 0:
                            raise ApprovalStoreDurabilityError("ERR_WRITE_SHORT: zero bytes written to temp file")
                        total_written += written
                    except OSError as we:
                        if we.errno == errno.EINTR:
                            continue
                        raise ApprovalStoreDurabilityError(f"ERR_WRITE_FAILED: error writing temp file: {we}") from we

                # 4. Flush file data and metadata to disk
                try:
                    os.fsync(tmp_fd)
                except OSError as fe:
                    raise ApprovalStoreDurabilityError(f"ERR_FILE_FSYNC_FAILED: failed fsync on temp file: {fe}") from fe

                # Close temp file descriptor prior to linkat (single-close semantics: disown immediately)
                fd_to_close = tmp_fd
                tmp_fd = None
                try:
                    os.close(fd_to_close)
                except OSError as ce:
                    raise ApprovalStoreDurabilityError(f"ERR_TEMP_CLOSE_FAILED: failed closing temp file: {ce}") from ce

                # 5. Atomic no-overwrite publication using descriptor-relative linkat
                try:
                    os.link(tmp_fname, final_fname, src_dir_fd=self._dir_fd, dst_dir_fd=self._dir_fd)
                    published = True
                except FileExistsError as fee:
                    raise ApprovalRecordAlreadyExistsError(
                        f"ERR_APPROVAL_RECORD_ALREADY_EXISTS: destination file already exists for approval_id '{approval_id}'"
                    ) from fee
                except OSError as le:
                    raise ApprovalStoreDurabilityError(f"ERR_LINK_FAILED: atomic link publication failed: {le}") from le

                # 6. Unlink temp file post-publication; fail closed if unlinking fails
                try:
                    os.unlink(tmp_fname, dir_fd=self._dir_fd)
                except OSError as ue:
                    raise ApprovalStoreDurabilityError(
                        f"ERR_TEMP_UNLINK_FAILED: record published but unlinking temp file '{tmp_fname}' failed: {ue}"
                    ) from ue

                # 7. Fsync parent directory descriptor to guarantee directory entry persistence
                try:
                    os.fsync(self._dir_fd)
                except OSError as de:
                    raise ApprovalStoreDurabilityError(f"ERR_DIR_FSYNC_FAILED: failed fsync on store directory: {de}") from de

            except BaseException as exc:
                primary_exc = exc
                if not published and tmp_created:
                    if tmp_fd is not None:
                        fd_cleanup = tmp_fd
                        tmp_fd = None  # Single-close semantics
                        try:
                            os.close(fd_cleanup)
                        except OSError as ce:
                            cleanup_errors.append(f"close(tmp_fd={fd_cleanup}): {ce}")
                    try:
                        os.unlink(tmp_fname, dir_fd=self._dir_fd)
                    except OSError as ue:
                        if ue.errno != errno.ENOENT:
                            cleanup_errors.append(f"unlink(tmp_fname): {ue}")
            finally:
                if tmp_fd is not None:
                    fd_cleanup = tmp_fd
                    tmp_fd = None  # Single-close semantics
                    try:
                        os.close(fd_cleanup)
                    except OSError as ce:
                        cleanup_errors.append(f"fallback_close(tmp_fd={fd_cleanup}): {ce}")

                if not published and tmp_created:
                    try:
                        os.unlink(tmp_fname, dir_fd=self._dir_fd)
                    except OSError:
                        pass

                # Strict unlock OS flock with fail-closed latching
                unlock_err: Optional[Exception] = None
                if self._lock_fd is not None:
                    try:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                    except OSError as ue:
                        self._lock_corrupted = True
                        self._closed = True
                        unlock_err = ApprovalStoreDurabilityError(
                            f"ERR_LOCK_RELEASE_FAILED: failed releasing store lock: {ue}"
                        )

                if primary_exc is not None:
                    if cleanup_errors or unlock_err is not None:
                        err_parts = [str(primary_exc)]
                        if cleanup_errors:
                            err_parts.append(f"cleanup failures: {'; '.join(cleanup_errors)}")
                        if unlock_err is not None:
                            err_parts.append(str(unlock_err))
                        raise ApprovalStoreDurabilityError("; ".join(err_parts)) from primary_exc
                    raise primary_exc

                if cleanup_errors:
                    if unlock_err is not None:
                        raise ApprovalStoreDurabilityError(
                            f"Cleanup failures: {'; '.join(cleanup_errors)}; {unlock_err}"
                        )
                    raise ApprovalStoreDurabilityError(f"Cleanup failures: {'; '.join(cleanup_errors)}")

                if unlock_err is not None:
                    raise unlock_err

    def get(self, approval_id: str) -> Optional[SignedApprovalEnvelope]:
        """Retrieves and verifies a SignedApprovalEnvelope by approval_id.

        Returns a fresh SignedApprovalEnvelope instance, or None if no record exists.
        Fails closed on symlinks, invalid permissions, malformed JSON, duplicate keys, or corruption.
        Single-close descriptor semantics and strict lock release error checking.
        """
        final_fname = self._record_filename(approval_id)

        with self._lock:
            self._ensure_open_locked()
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_SH)
            except OSError as fe:
                self._lock_corrupted = True
                self._closed = True
                raise ApprovalStoreDurabilityError(f"ERR_LOCK_ACQUIRE_FAILED: failed acquiring shared lock: {fe}") from fe

            primary_exc: Optional[BaseException] = None
            reader_cleanup_err: Optional[Exception] = None
            envelope: Optional[SignedApprovalEnvelope] = None

            try:
                open_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                ffd: Optional[int] = None
                raw_bytes = bytearray()
                try:
                    try:
                        ffd = os.open(final_fname, open_flags, dir_fd=self._dir_fd)
                    except FileNotFoundError:
                        return None
                    except OSError as oe:
                        if oe.errno == errno.ELOOP:
                            raise ApprovalStoreSecurityError(f"ERR_SYMLINK_DETECTED: {final_fname} is a symlink: {oe}") from oe
                        raise ApprovalStoreError(f"ERR_OPEN_FAILED: failed opening record file '{final_fname}': {oe}") from oe

                    # 1. Descriptor-based initial fstat validation
                    try:
                        st_initial = os.fstat(ffd)
                    except OSError as se:
                        raise ApprovalStoreError(f"ERR_STAT_FAILED: failed fstat on opened record descriptor: {se}") from se

                    if stat.S_ISLNK(st_initial.st_mode):
                        raise ApprovalStoreSecurityError(f"ERR_SYMLINK_DETECTED: record file '{final_fname}' is a symlink")
                    if not stat.S_ISREG(st_initial.st_mode):
                        raise ApprovalStoreSecurityError(f"ERR_NOT_REGULAR_FILE: record file '{final_fname}' is not a regular file")
                    if hasattr(os, "getuid") and st_initial.st_uid != os.getuid():
                        raise ApprovalStoreSecurityError(
                            f"ERR_FILE_INVALID_OWNER: record file '{final_fname}' uid {st_initial.st_uid} != current uid {os.getuid()}"
                        )
                    if (st_initial.st_mode & EXPECTED_FILE_MODE_MASK) != 0:
                        raise ApprovalStoreSecurityError(
                            f"ERR_FILE_INSECURE_PERMS: record file '{final_fname}' mode must be 0600, got {oct(st_initial.st_mode)}"
                        )
                    if st_initial.st_size > MAX_RECORD_BYTES:
                        raise ApprovalStoreCorruptionError(
                            f"ERR_FILE_TOO_LARGE: record file '{final_fname}' size {st_initial.st_size} exceeds bound {MAX_RECORD_BYTES}"
                        )

                    # 2. Bounded descriptor-relative read loop bounded by remaining + 1
                    while True:
                        remaining = MAX_RECORD_BYTES - len(raw_bytes)
                        if remaining <= 0:
                            read_size = 1
                        else:
                            read_size = min(4096, remaining + 1)

                        try:
                            chunk = os.read(ffd, read_size)
                        except OSError as re:
                            if re.errno == errno.EINTR:
                                continue
                            raise ApprovalStoreError(f"ERR_READ_FAILED: failed reading record file: {re}") from re

                        if not chunk:
                            break

                        raw_bytes.extend(chunk)
                        if len(raw_bytes) > MAX_RECORD_BYTES:
                            raise ApprovalStoreCorruptionError(
                                f"ERR_RECORD_TOO_LARGE: bytes read ({len(raw_bytes)}) exceeds bound {MAX_RECORD_BYTES}"
                            )

                    # 3. Post-read fstat validation: verify device/inode/size stability through EOF
                    try:
                        st_final = os.fstat(ffd)
                    except OSError as se:
                        raise ApprovalStoreError(f"ERR_STAT_FAILED: failed post-read fstat: {se}") from se

                    if st_final.st_ino != st_initial.st_ino or st_final.st_dev != st_initial.st_dev:
                        raise ApprovalStoreSecurityError(
                            f"ERR_INODE_INSTABILITY: record file descriptor inode/device changed during read"
                        )
                    if st_final.st_size != st_initial.st_size or len(raw_bytes) != st_final.st_size:
                        raise ApprovalStoreCorruptionError(
                            f"ERR_SIZE_INSTABILITY: initial stat size ({st_initial.st_size}), read bytes ({len(raw_bytes)}), final stat size ({st_final.st_size})"
                        )

                    # 4. Explicit close with durability failure reporting (single-close semantics: disown immediately)
                    fd_to_close = ffd
                    ffd = None
                    try:
                        os.close(fd_to_close)
                    except OSError as ce:
                        raise ApprovalStoreDurabilityError(f"ERR_RECORD_CLOSE_FAILED: failed closing record descriptor: {ce}") from ce

                finally:
                    if ffd is not None:
                        fd_to_close = ffd
                        ffd = None
                        try:
                            os.close(fd_to_close)
                        except OSError as ce:
                            reader_cleanup_err = ApprovalStoreDurabilityError(
                                f"ERR_RECORD_FALLBACK_CLOSE_FAILED: failed closing record descriptor ({fd_to_close}): {ce}"
                            )

                # 5. Strict canonical parsing with duplicate key rejection
                try:
                    parsed_dict = parse_strict_canonical_json(bytes(raw_bytes), max_bytes=MAX_RECORD_BYTES)
                except (StrictJsonParseError, StrictJsonComplexityError, ValueError) as pe:
                    raise ApprovalStoreCorruptionError(
                        f"ERR_PARSE_FAILED: record '{final_fname}' is not valid canonical JSON: {pe}"
                    ) from pe

                # 6. Strict SignedApprovalEnvelope DTO reconstruction
                try:
                    envelope = SignedApprovalEnvelope.from_dict(parsed_dict)
                except (SignedApprovalEnvelopeValidationError, ValueError) as ve:
                    raise ApprovalStoreCorruptionError(
                        f"ERR_SCHEMA_FAILED: record '{final_fname}' fails envelope schema validation: {ve}"
                    ) from ve

                # 7. Semantic bindings: approval_id match and exact byte-for-byte fidelity
                if envelope.approval_record.approval_id != approval_id:
                    raise ApprovalStoreCorruptionError(
                        f"ERR_APPROVAL_ID_MISMATCH: stored record id '{envelope.approval_record.approval_id}' != queried '{approval_id}'"
                    )

                if envelope.canonical_envelope_bytes() != bytes(raw_bytes):
                    raise ApprovalStoreCorruptionError(
                        "ERR_BYTE_FIDELITY_MISMATCH: reconstructed envelope canonical bytes do not match stored bytes exactly"
                    )

            except BaseException as exc:
                primary_exc = exc
            finally:
                # Strict unlock OS flock with fail-closed latching
                unlock_err: Optional[Exception] = None
                if self._lock_fd is not None:
                    try:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                    except OSError as ue:
                        self._lock_corrupted = True
                        self._closed = True
                        unlock_err = ApprovalStoreDurabilityError(
                            f"ERR_LOCK_RELEASE_FAILED: failed releasing store lock: {ue}"
                        )

                if primary_exc is not None:
                    err_parts = [str(primary_exc)]
                    if reader_cleanup_err is not None:
                        err_parts.append(f"cleanup failure: {reader_cleanup_err}")
                    if unlock_err is not None:
                        err_parts.append(str(unlock_err))
                    if len(err_parts) > 1:
                        raise ApprovalStoreDurabilityError("; ".join(err_parts)) from primary_exc
                    raise primary_exc

                if reader_cleanup_err is not None:
                    if unlock_err is not None:
                        raise ApprovalStoreDurabilityError(f"{reader_cleanup_err}; {unlock_err}") from reader_cleanup_err
                    raise reader_cleanup_err

                if unlock_err is not None:
                    raise unlock_err

            return envelope

    def close(self) -> None:
        """Closes all retained descriptors exception-safely using single-close semantics."""
        with self._lock:
            if self._closed and self._lock_fd is None and self._dir_fd is None:
                return

            self._closed = True

            cleanup_errors: List[str] = []
            if self._lock_fd is not None:
                fd = self._lock_fd
                self._lock_fd = None  # Single-close semantics: immediately disown before calling os.close
                try:
                    os.close(fd)
                except OSError as ce:
                    self._cleanup_failures.append(("lock_fd", fd, ce, CLOSE_OUTCOME_UNKNOWN))
                    cleanup_errors.append(f"lock_fd({fd}): {ce}")

            if self._dir_fd is not None:
                fd = self._dir_fd
                self._dir_fd = None  # Single-close semantics: immediately disown before calling os.close
                try:
                    os.close(fd)
                except OSError as ce:
                    self._cleanup_failures.append(("dir_fd", fd, ce, CLOSE_OUTCOME_UNKNOWN))
                    cleanup_errors.append(f"dir_fd({fd}): {ce}")

            if cleanup_errors:
                raise ApprovalStoreDurabilityError(
                    f"ERR_STORE_CLOSE_FAILED: descriptor cleanup failed: {'; '.join(cleanup_errors)}"
                )

    def __enter__(self) -> AuthoritativeApprovalStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
