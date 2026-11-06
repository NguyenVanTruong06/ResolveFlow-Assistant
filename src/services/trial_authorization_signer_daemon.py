"""Trial Authorization Signer Daemon.

Production Signer Service Daemon hosting:
1. Public Signer AF_UNIX Client Endpoint (/run/resolveflow/signer_client/signer_public.sock)
   - Handles SignerPublicPingRequest, SignerPublicChallengeRequest, and SignerPublicConsentRequest.
   - Issues canonical single-use TrialExecutionAuthorization bound to Gate 3 admissions.
2. Authenticated Oracle AF_UNIX Endpoint (/run/resolveflow/oracle/signer_oracle.sock)
   - Authenticates Dispatcher peer credentials (UID 902 required via SO_PEERCRED).
   - Validates fresh signed revocation manifest and monotonic epoch anchor.
   - Enforces single-use terminal execution verification against DurableSignerLedger.
   - Signs RFC 8785 canonical 14-claim responses with Ed25519 private key.

Adheres strictly to docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md:
- Inert constructor / import (zero host, Resolve, key, socket, or thread activity until start())
- Descriptor-anchored directory verification (mode 02750 under DAC)
- Persistent descriptor-anchored singleton flock acquisition (mode 0600, never unlinked)
- Ordered stale socket cleanup (only daemon's own matching socket; never unlinks foreign nodes)
- Descriptor-anchored socket binding via /proc/self/fd/{dir_fd}/{sock_name} and mode 0660 assertion
- Bounded concurrency with admission semaphore and graceful worker draining on shutdown
- Strict peer authentication with fail-closed deny on credential errors
- Fail-closed ledger poisoning on durability errors
"""

from __future__ import annotations

import datetime
import errno
import fcntl
import hashlib
import json
import os
import socket
import stat
import struct
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from src.core.contracts.edit_plan_proposal_contracts import EditPlanProposal
from src.core.contracts.oracle_ipc_contracts import (
    DEFAULT_SOCKET_TIMEOUT,
    ERR_AUTHORIZATION_REVOKED,
    ERR_CONCURRENCY_LIMIT_EXCEEDED,
    ERR_DAEMON_ALREADY_RUNNING,
    ERR_DIRECTOR_KEY_REVOKED,
    ERR_EMPTY_FRAME,
    ERR_EXECUTION_ALREADY_TERMINAL,
    ERR_EXECUTION_NOT_FOUND,
    ERR_FRAME_TOO_LARGE,
    ERR_IDENTITY_BINDING_MISMATCH,
    ERR_INVALID_FRAME,
    ERR_OPERATOR_KEY_REVOKED,
    ERR_PEER_UNAUTHORIZED,
    ERR_REVOCATION_VERIFICATION_FAILED,
    ERR_SCHEMA_VERSION_INVALID,
    ERR_TERMINAL_STATE_CONFLICT,
    ERR_TERMINAL_STATE_INVALID,
    ERR_TRIAL_KEY_REVOKED,
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
    CHALLENGE_REQUEST_ALLOWED_FIELDS,
    CONSENT_REQUEST_ALLOWED_FIELDS,
    PING_REQUEST_ALLOWED_FIELDS,
    decode_framed_message,
    decode_oracle_request,
    encode_framed_message,
    parse_strict_json,
)
from fractions import Fraction
from src.core.contracts.signed_approval_envelope import SignedApprovalEnvelope
from src.core.contracts.signed_operator_consent_envelope import SignedOperatorConsentEnvelope
from src.core.contracts.edit_plan_approval_contracts import approval_record_to_dict
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.execution_receipt_contracts import (
    SourceFileDigestBinding,
    TargetTrackReservation,
)
from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames
from src.core.contracts.trial_authorization_contracts import (
    TrialExecutionAuthorization,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
)
from src.core.timeline_plan_execution_binder import (
    PlannedTimelineAction,
    TargetSpec,
    TimelineExecutionPlan,
)
from src.services.durable_nonce_ledger import (
    AuthorizationIdentityRecord,
    DurableSignerLedger,
    OracleExecutionRecord,
    OracleExecutionTerminalRecord,
)
from src.services.signer_keystore_service import SignerKeystoreService
from src.services.trial_authorization_signer_service import (
    ConsumedAuthorizationNonceRecord,
    LocalTestChallengeAuthority,
    LocalTestChallengeIssuanceRequest,
    RevocationAnchorAbsentError,
    RevocationEpochTamperingError,
    RevocationManifestVerificationError,
    RevocationManifestVerificationResult,
    RevocationRollbackError,
    SignerAdmissionVerificationError,
    TrialAuthorizationSignerService,
    TrialSignerError,
    load_verify_and_anchor_revocation_manifest_for_local_test,
    load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test,
    parse_iso_utc_timestamp,
)

SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)

PRODUCTION_SIGNER_UID: int = 901
PRODUCTION_DISPATCHER_UID: int = 902
PRODUCTION_SIGNER_GID: int = 901
PRODUCTION_DISPATCHER_GID: int = 902
PRODUCTION_CLIENTS_GID: int = 903

MAX_CONCURRENT_CLIENTS: int = 16


class SignerDaemonTeardownError(TrialSignerError):
    """Raised when daemon shutdown or teardown fails or cannot safely complete (DEP02B-R3-02)."""


class TrialAuthorizationSignerDaemon:
    """Production Signer Service Daemon."""

    def __init__(
        self,
        *,
        keystore_service: Optional[SignerKeystoreService] = None,
        nonce_ledger: Optional[DurableSignerLedger] = None,
        oracle_dir: Optional[Union[str, Path]] = None,
        oracle_dir_fd: Optional[int] = None,
        oracle_sock_name: str = "signer_oracle.sock",
        oracle_lock_name: str = "signer_oracle.lock",
        public_dir: Optional[Union[str, Path]] = None,
        public_dir_fd: Optional[int] = None,
        public_sock_name: str = "signer_public.sock",
        public_lock_name: str = "signer_public.lock",
        revocation_trust_dir: Optional[Union[str, Path]] = None,
        revocation_registry_dir: Optional[Union[str, Path]] = None,
        **forbidden_kwargs: Any,
    ) -> None:
        """Inert production constructor: strictly enforces production DAC and peer invariants."""
        if forbidden_kwargs:
            first_key = sorted(forbidden_kwargs.keys())[0]
            raise TrialSignerError(
                f"ERR_PRODUCTION_SECURITY_INVARIANT: argument '{first_key}' cannot be configured on production constructor; use create_for_test() exclusively for test harnesses"
            )

        self._init_common(
            keystore_service=keystore_service,
            nonce_ledger=nonce_ledger,
            oracle_dir=oracle_dir,
            oracle_dir_fd=oracle_dir_fd,
            oracle_sock_name=oracle_sock_name,
            oracle_lock_name=oracle_lock_name,
            public_dir=public_dir,
            public_dir_fd=public_dir_fd,
            public_sock_name=public_sock_name,
            public_lock_name=public_lock_name,
            revocation_trust_dir=revocation_trust_dir,
            revocation_registry_dir=revocation_registry_dir,
            sec_officer_public_key=None,
            expected_daemon_uid=PRODUCTION_SIGNER_UID,
            expected_oracle_gid=PRODUCTION_DISPATCHER_GID,
            expected_public_gid=PRODUCTION_CLIENTS_GID,
            oracle_allowed_uids=(PRODUCTION_DISPATCHER_UID,),
            enforce_dac_ownership=True,
            for_test=False,
        )

    def _init_common(
        self,
        *,
        keystore_service: Optional[SignerKeystoreService],
        nonce_ledger: Optional[DurableSignerLedger],
        oracle_dir: Optional[Union[str, Path]],
        oracle_dir_fd: Optional[int],
        oracle_sock_name: str,
        oracle_lock_name: str,
        public_dir: Optional[Union[str, Path]],
        public_dir_fd: Optional[int],
        public_sock_name: str,
        public_lock_name: str,
        revocation_trust_dir: Optional[Union[str, Path]],
        revocation_registry_dir: Optional[Union[str, Path]],
        sec_officer_public_key: Optional[Ed25519PublicKey],
        expected_daemon_uid: int,
        expected_oracle_gid: int,
        expected_public_gid: int,
        oracle_allowed_uids: Tuple[int, ...],
        enforce_dac_ownership: bool,
        for_test: bool,
    ) -> None:
        self._for_test = for_test
        self._keystore = keystore_service
        self._ledger = nonce_ledger

        self._oracle_dir = oracle_dir
        self._oracle_dir_fd_in = oracle_dir_fd
        self._oracle_sock_name = oracle_sock_name
        self._oracle_lock_name = oracle_lock_name
        self._oracle_allowed_uids = oracle_allowed_uids

        self._public_dir = public_dir
        self._public_dir_fd_in = public_dir_fd
        self._public_sock_name = public_sock_name
        self._public_lock_name = public_lock_name

        self._revocation_trust_dir = revocation_trust_dir
        self._revocation_registry_dir = revocation_registry_dir
        self._sec_officer_public_key = sec_officer_public_key

        self._expected_daemon_uid = expected_daemon_uid
        self._expected_oracle_gid = expected_oracle_gid
        self._expected_public_gid = expected_public_gid
        self._enforce_dac_ownership = enforce_dac_ownership

        # Gate 3 challenge authority instance
        self._challenge_authority = LocalTestChallengeAuthority()

        # Runtime state
        self._started: bool = False
        self._running: bool = False
        self._stopping: bool = False
        self._lock = threading.RLock()

        # Concurrency bounds (DEP02B-06)
        self._oracle_semaphore = threading.Semaphore(MAX_CONCURRENT_CLIENTS)
        self._public_semaphore = threading.Semaphore(MAX_CONCURRENT_CLIENTS)
        self._active_worker_threads: Set[threading.Thread] = set()
        self._worker_threads_lock = threading.Lock()

        # Oracle endpoint handles
        self._oracle_dir_fd: Optional[int] = None
        self._oracle_lock_fd: Optional[int] = None
        self._oracle_sock: Optional[socket.socket] = None
        self._oracle_sock_path: Optional[str] = None
        self._oracle_sock_dev_ino: Optional[Tuple[int, int]] = None
        self._oracle_thread: Optional[threading.Thread] = None

        # Public endpoint handles
        self._public_dir_fd: Optional[int] = None
        self._public_lock_fd: Optional[int] = None
        self._public_sock: Optional[socket.socket] = None
        self._public_sock_path: Optional[str] = None
        self._public_sock_dev_ino: Optional[Tuple[int, int]] = None
        self._public_thread: Optional[threading.Thread] = None

        self._cleanup_failures: List[Tuple[str, Exception]] = []
        self._active_challenge_requests: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def create_for_test(
        cls,
        *,
        keystore_service: SignerKeystoreService,
        nonce_ledger: DurableSignerLedger,
        oracle_dir: Optional[Union[str, Path]] = None,
        oracle_dir_fd: Optional[int] = None,
        oracle_sock_name: str = "signer_oracle.sock",
        oracle_lock_name: str = "signer_oracle.lock",
        public_dir: Optional[Union[str, Path]] = None,
        public_dir_fd: Optional[int] = None,
        public_sock_name: str = "signer_public.sock",
        public_lock_name: str = "signer_public.lock",
        oracle_allowed_uids: Tuple[int, ...] = (os.geteuid(),),
        revocation_trust_dir: Optional[Union[str, Path]] = None,
        revocation_registry_dir: Optional[Union[str, Path]] = None,
        sec_officer_public_key: Optional[Ed25519PublicKey] = None,
        expected_daemon_uid: Optional[int] = None,
        expected_oracle_gid: Optional[int] = None,
        expected_public_gid: Optional[int] = None,
        enforce_dac_ownership: bool = False,
    ) -> TrialAuthorizationSignerDaemon:
        """Explicit test-only constructor completely isolated from the production constructor."""
        inst = cls.__new__(cls)
        inst._init_common(
            keystore_service=keystore_service,
            nonce_ledger=nonce_ledger,
            oracle_dir=oracle_dir,
            oracle_dir_fd=oracle_dir_fd,
            oracle_sock_name=oracle_sock_name,
            oracle_lock_name=oracle_lock_name,
            public_dir=public_dir,
            public_dir_fd=public_dir_fd,
            public_sock_name=public_sock_name,
            public_lock_name=public_lock_name,
            oracle_allowed_uids=oracle_allowed_uids,
            revocation_trust_dir=revocation_trust_dir,
            revocation_registry_dir=revocation_registry_dir,
            sec_officer_public_key=sec_officer_public_key,
            expected_daemon_uid=expected_daemon_uid if expected_daemon_uid is not None else os.geteuid(),
            expected_oracle_gid=expected_oracle_gid if expected_oracle_gid is not None else os.getegid(),
            expected_public_gid=expected_public_gid if expected_public_gid is not None else os.getegid(),
            enforce_dac_ownership=enforce_dac_ownership,
            for_test=True,
        )
        return inst

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running and not self._stopping

    @property
    def is_stopping(self) -> bool:
        with self._lock:
            return self._stopping

    def start(self) -> None:
        """Starts daemon listeners and begins serving requests."""
        with self._lock:
            if self._started:
                raise TrialSignerError("ERR_DAEMON_ALREADY_STARTED")
            if self._keystore is None:
                raise TrialSignerError("ERR_KEYSTORE_REQUIRED: keystore service must be provided")
            if self._ledger is None:
                raise TrialSignerError("ERR_LEDGER_REQUIRED: durable nonce ledger must be provided")

            if not self._for_test:
                if self._revocation_registry_dir is None or self._revocation_trust_dir is None:
                    raise TrialSignerError("ERR_PRODUCTION_SECURITY_INVARIANT: revocation_registry_dir and revocation_trust_dir are required in production")

            try:
                # 1. Setup Oracle Endpoint
                self._setup_endpoint(
                    endpoint_type="oracle",
                    dir_path=self._oracle_dir,
                    dir_fd_in=self._oracle_dir_fd_in,
                    sock_name=self._oracle_sock_name,
                    lock_name=self._oracle_lock_name,
                    expected_gid=self._expected_oracle_gid,
                )

                # 2. Setup Public Endpoint
                self._setup_endpoint(
                    endpoint_type="public",
                    dir_path=self._public_dir,
                    dir_fd_in=self._public_dir_fd_in,
                    sock_name=self._public_sock_name,
                    lock_name=self._public_lock_name,
                    expected_gid=self._expected_public_gid,
                )

                self._running = True
                self._started = True

                # Spawn listener threads
                self._oracle_thread = threading.Thread(
                    target=self._run_listener,
                    args=("oracle", self._oracle_sock, self._handle_oracle_client, self._oracle_semaphore),
                    daemon=True,
                    name="SignerOracleListener",
                )
                self._public_thread = threading.Thread(
                    target=self._run_listener,
                    args=("public", self._public_sock, self._handle_public_client, self._public_semaphore),
                    daemon=True,
                    name="SignerPublicListener",
                )
                self._oracle_thread.start()
                self._public_thread.start()

            except Exception:
                self.stop()
                raise

    def _setup_endpoint(
        self,
        *,
        endpoint_type: str,
        dir_path: Optional[Union[str, Path]],
        dir_fd_in: Optional[int],
        sock_name: str,
        lock_name: str,
        expected_gid: int,
    ) -> None:
        # A. Open directory descriptor (DEP02B-03)
        if dir_fd_in is not None:
            dir_fd = os.dup(dir_fd_in)
            os.set_inheritable(dir_fd, False)
            fcntl.fcntl(dir_fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
        elif dir_path is not None:
            path_str = str(dir_path)
            dir_fd = os.open(path_str, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        else:
            raise TrialSignerError(f"ERR_MISSING_DIR: directory or descriptor required for {endpoint_type}")

        # Validate directory descriptor
        st_dir = os.fstat(dir_fd)
        if stat.S_ISLNK(st_dir.st_mode):
            os.close(dir_fd)
            raise TrialSignerError(f"ERR_SECURITY_VIOLATION: {endpoint_type} directory is a symlink")
        if not stat.S_ISDIR(st_dir.st_mode):
            os.close(dir_fd)
            raise TrialSignerError(f"ERR_SECURITY_VIOLATION: {endpoint_type} path is not a directory")

        if self._enforce_dac_ownership:
            if st_dir.st_uid != self._expected_daemon_uid:
                os.close(dir_fd)
                raise TrialSignerError(
                    f"ERR_INVALID_OWNER: {endpoint_type} dir owned by UID {st_dir.st_uid}, expected {self._expected_daemon_uid}"
                )
            if st_dir.st_gid != expected_gid:
                os.close(dir_fd)
                raise TrialSignerError(
                    f"ERR_INVALID_GROUP: {endpoint_type} dir owned by GID {st_dir.st_gid}, expected {expected_gid}"
                )
            dir_mode = stat.S_IMODE(st_dir.st_mode)
            if dir_mode != 0o2750:
                os.close(dir_fd)
                raise TrialSignerError(
                    f"ERR_INVALID_MODE: {endpoint_type} dir mode {oct(dir_mode)}, expected strictly 02750"
                )

        # B. Acquire singleton lock (Validation-First, zero pre-mutation)
        try:
            lock_fd = os.open(
                lock_name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=dir_fd,
            )
        except OSError as e:
            os.close(dir_fd)
            if e.errno == errno.ELOOP:
                raise TrialSignerError(f"ERR_FOREIGN_LOCK_NODE: {lock_name} is a symlink: {e}") from e
            raise TrialSignerError(f"ERR_CANNOT_OPEN_LOCK_FILE: {e}") from e

        try:
            st_lock = os.fstat(lock_fd)
            if stat.S_ISLNK(st_lock.st_mode):
                raise TrialSignerError(f"ERR_FOREIGN_LOCK_NODE: {lock_name} is a symlink")
            if not stat.S_ISREG(st_lock.st_mode):
                raise TrialSignerError(f"ERR_FOREIGN_LOCK_NODE: {lock_name} is not a regular file")
            if st_lock.st_nlink != 1:
                raise TrialSignerError(f"ERR_FOREIGN_LOCK_NODE: {lock_name} has {st_lock.st_nlink} hardlinks")
            if self._enforce_dac_ownership:
                if st_lock.st_uid != self._expected_daemon_uid:
                    raise TrialSignerError(f"ERR_FOREIGN_LOCK_NODE: {lock_name} owned by UID {st_lock.st_uid}")
                if st_lock.st_gid != expected_gid:
                    raise TrialSignerError(f"ERR_FOREIGN_LOCK_NODE: {lock_name} owned by GID {st_lock.st_gid}")

            lock_mode = stat.S_IMODE(st_lock.st_mode)
            if lock_mode != 0o600:
                raise TrialSignerError(f"ERR_INVALID_LOCK_MODE: {lock_name} mode is {oct(lock_mode)}, expected 0600")

            # Nonblocking exclusive flock
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as e:
                raise TrialSignerError(
                    f"ERR_DAEMON_ALREADY_RUNNING: could not acquire exclusive singleton lock on {lock_name}: {e}"
                ) from e
        except Exception:
            os.close(lock_fd)
            os.close(dir_fd)
            raise

        # C. Stale socket check & cleanup
        try:
            st_sock = os.stat(sock_name, dir_fd=dir_fd, follow_symlinks=False)
            exists = True
        except FileNotFoundError:
            exists = False

        if exists:
            # Foreign node protection: verify it is strictly daemon-owned UNIX socket
            if stat.S_ISLNK(st_sock.st_mode):
                os.close(lock_fd)
                os.close(dir_fd)
                raise TrialSignerError(f"ERR_FOREIGN_SOCKET_NODE: existing entry '{sock_name}' is a symlink")
            if not stat.S_ISSOCK(st_sock.st_mode):
                os.close(lock_fd)
                os.close(dir_fd)
                raise TrialSignerError(f"ERR_FOREIGN_SOCKET_NODE: existing entry '{sock_name}' is not a socket")
            if self._enforce_dac_ownership:
                if st_sock.st_uid != self._expected_daemon_uid:
                    os.close(lock_fd)
                    os.close(dir_fd)
                    raise TrialSignerError(f"ERR_FOREIGN_SOCKET_NODE: existing socket '{sock_name}' owned by UID {st_sock.st_uid}")
                if st_sock.st_gid != expected_gid:
                    os.close(lock_fd)
                    os.close(dir_fd)
                    raise TrialSignerError(f"ERR_FOREIGN_SOCKET_NODE: existing socket '{sock_name}' owned by GID {st_sock.st_gid}")

            # Safe to unlink stale socket
            os.unlink(sock_name, dir_fd=dir_fd)

        # D. Bind socket descriptor-relatively (DEP02B-03)
        proc_sock_path = f"/proc/self/fd/{dir_fd}/{sock_name}"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setblocking(True)
        try:
            sock.bind(proc_sock_path)
            os.chmod(proc_sock_path, 0o660)

            # Post-bind verification strictly via directory descriptor
            st_post = os.stat(sock_name, dir_fd=dir_fd, follow_symlinks=False)
            if not stat.S_ISSOCK(st_post.st_mode):
                raise TrialSignerError(f"ERR_POST_BIND_VERIFICATION_FAILED: '{sock_name}' is not a socket")
            if self._enforce_dac_ownership:
                if st_post.st_uid != self._expected_daemon_uid:
                    raise TrialSignerError(f"ERR_POST_BIND_VERIFICATION_FAILED: UID {st_post.st_uid} != {self._expected_daemon_uid}")
                if st_post.st_gid != expected_gid:
                    raise TrialSignerError(f"ERR_POST_BIND_VERIFICATION_FAILED: GID {st_post.st_gid} != {expected_gid}")
                actual_mode = stat.S_IMODE(st_post.st_mode)
                if actual_mode != 0o660:
                    raise TrialSignerError(f"ERR_POST_BIND_VERIFICATION_FAILED: mode {oct(actual_mode)} != 0660")

            sock.listen(16)
        except Exception:
            sock.close()
            try:
                st_fail = os.stat(sock_name, dir_fd=dir_fd, follow_symlinks=False)
                if stat.S_ISSOCK(st_fail.st_mode):
                    os.unlink(sock_name, dir_fd=dir_fd)
            except OSError:
                pass
            os.close(lock_fd)
            os.close(dir_fd)
            raise

        # Save handles based on endpoint
        if endpoint_type == "oracle":
            self._oracle_dir_fd = dir_fd
            self._oracle_lock_fd = lock_fd
            self._oracle_sock = sock
            self._oracle_sock_path = proc_sock_path
            self._oracle_sock_dev_ino = (st_post.st_dev, st_post.st_ino)
        else:
            self._public_dir_fd = dir_fd
            self._public_lock_fd = lock_fd
            self._public_sock = sock
            self._public_sock_path = proc_sock_path
            self._public_sock_dev_ino = (st_post.st_dev, st_post.st_ino)

    def _run_listener(
        self,
        name: str,
        sock: Optional[socket.socket],
        handler: Any,
        semaphore: threading.Semaphore,
    ) -> None:
        if sock is None:
            return
        try:
            sock.settimeout(1.0)
        except OSError:
            return
        while self._running and not self._stopping:
            try:
                client_sock, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            # DEP02B-06: Concurrency admission check
            if not semaphore.acquire(blocking=False):
                try:
                    err_msg = encode_framed_message(
                        json.dumps({
                            "schema_version": 2,
                            "error": ERR_CONCURRENCY_LIMIT_EXCEEDED,
                            "detail": "Daemon connection concurrency limit reached",
                        }).encode("utf-8")
                    )
                    client_sock.sendall(err_msg)
                except OSError:
                    pass
                finally:
                    try:
                        client_sock.close()
                    except OSError:
                        pass
                continue

            def worker_wrapper(cs: socket.socket, sem: threading.Semaphore) -> None:
                curr_t = threading.current_thread()
                with self._worker_threads_lock:
                    self._active_worker_threads.add(curr_t)
                try:
                    if not self._running or self._stopping:
                        try:
                            cs.close()
                        except OSError:
                            pass
                        return
                    handler(cs)
                finally:
                    sem.release()
                    with self._worker_threads_lock:
                        self._active_worker_threads.discard(curr_t)

            client_thread = threading.Thread(
                target=worker_wrapper,
                args=(client_sock, semaphore),
                daemon=True,
                name=f"{name}_handler_{time.time()}",
            )
            client_thread.start()

    def _handle_oracle_client(self, client_sock: socket.socket) -> None:
        try:
            if not self._running or self._stopping:
                return
            client_sock.settimeout(DEFAULT_SOCKET_TIMEOUT)

            # Peer credentials verification (DEP02B-05)
            try:
                cred_bytes = client_sock.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
                pid, uid, gid = struct.unpack("3i", cred_bytes)
            except Exception as e:
                # Always deny on credential retrieval failure
                err_resp = encode_framed_message(
                    json.dumps({
                        "schema_version": 2,
                        "error": ERR_PEER_UNAUTHORIZED,
                        "detail": f"Failed to retrieve peer credentials: {e}",
                    }).encode("utf-8")
                )
                try:
                    client_sock.sendall(err_resp)
                except OSError:
                    pass
                return

            allowed_uids = self._oracle_allowed_uids if self._for_test else (PRODUCTION_DISPATCHER_UID,)
            if uid not in allowed_uids:
                err_resp = encode_framed_message(
                    json.dumps({
                        "schema_version": 2,
                        "error": ERR_PEER_UNAUTHORIZED,
                        "detail": f"Peer UID {uid} not in authorized set {allowed_uids}",
                    }).encode("utf-8")
                )
                try:
                    client_sock.sendall(err_resp)
                except OSError:
                    pass
                return

            # Read framed request
            raw_frame = decode_framed_message(client_sock)
            parsed_req = decode_oracle_request(raw_frame)

            if isinstance(parsed_req, OracleTerminalNotificationRequest):
                response = self._process_oracle_terminal_notification(parsed_req)
            elif isinstance(parsed_req, OracleVerificationRequest):
                response = self._process_oracle_verification(parsed_req)
            else:
                raise OracleProtocolError(ERR_UNEXPECTED_FIELD, f"Unsupported request type: {type(parsed_req)}")

            # Send framed response
            resp_bytes = encode_framed_message(response.canonical_bytes())
            client_sock.sendall(resp_bytes)
        except Exception as exc:
            try:
                err_code = getattr(exc, "code", None)
                if not err_code:
                    msg = str(exc)
                    if ":" in msg and msg.split(":", 1)[0].strip().startswith("ERR_"):
                        err_code = msg.split(":", 1)[0].strip()
                    else:
                        err_code = "ERR_REQUEST_FAILED"
                err_resp = encode_framed_message(
                    json.dumps({
                        "schema_version": 2,
                        "error": err_code,
                        "detail": str(exc),
                    }).encode("utf-8")
                )
                client_sock.sendall(err_resp)
            except OSError:
                pass
        finally:
            try:
                client_sock.close()
            except OSError:
                pass

    def _check_key_revocation(
        self,
        *,
        director_key_id: Optional[str] = None,
        operator_key_id: Optional[str] = None,
        authorization_id: Optional[str] = None,
        trial_key_id: Optional[str] = None,
        verification_time_utc: datetime.datetime,
        require_existing_anchor: bool = True,
    ) -> None:
        """Verifies revocation manifest and ensures keys/authorizations are not revoked."""
        if self._revocation_registry_dir is None:
            if not self._for_test:
                raise TrialSignerError("ERR_REVOCATION_VERIFICATION_FAILED: revocation registry required in production")
            return

        try:
            if self._sec_officer_public_key is not None:
                rev_res = load_verify_and_anchor_revocation_manifest_for_local_test(
                    registry_directory=self._revocation_registry_dir,
                    issuer_public_key=self._sec_officer_public_key,
                    verification_time_utc=verification_time_utc,
                    advance_anchor=False,
                    require_existing_anchor=require_existing_anchor,
                )
            elif self._revocation_trust_dir is not None:
                rev_res = load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                    trust_directory=self._revocation_trust_dir,
                    registry_directory=self._revocation_registry_dir,
                    verification_time_utc=verification_time_utc,
                    advance_anchor=False,
                    require_existing_anchor=require_existing_anchor,
                )
            else:
                raise TrialSignerError("ERR_REVOCATION_VERIFICATION_FAILED: no issuer key available")

            manifest = rev_res.manifest
            if not director_key_id or not str(director_key_id).strip():
                raise TrialSignerError("ERR_REVOCATION_VERIFICATION_FAILED: director_key_id is required")
            if not operator_key_id or not str(operator_key_id).strip():
                raise TrialSignerError("ERR_REVOCATION_VERIFICATION_FAILED: operator_key_id is required")
            if director_key_id in manifest.revoked_director_key_ids:
                raise TrialSignerError(f"ERR_DIRECTOR_KEY_REVOKED: key '{director_key_id}' is revoked")
            if operator_key_id in manifest.revoked_operator_key_ids:
                raise TrialSignerError(f"ERR_OPERATOR_KEY_REVOKED: key '{operator_key_id}' is revoked")
            if authorization_id and authorization_id in manifest.revoked_authorization_ids:
                raise TrialSignerError(f"ERR_AUTHORIZATION_REVOKED: authorization '{authorization_id}' is revoked")
            if trial_key_id and trial_key_id in manifest.revoked_trial_key_ids:
                raise TrialSignerError(f"ERR_TRIAL_KEY_REVOKED: trial key '{trial_key_id}' is revoked")
        except TrialSignerError:
            raise
        except Exception as exc:
            raise TrialSignerError(f"ERR_REVOCATION_VERIFICATION_FAILED: {exc}") from exc

    def _process_oracle_verification(self, req: OracleVerificationRequest) -> OracleVerificationResponse:
        assert self._keystore is not None
        assert self._ledger is not None

        with self._lock:
            if not self._running or self._stopping:
                raise TrialSignerError("ERR_DAEMON_NOT_RUNNING: daemon is stopping or stopped")

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        valid_until_iso = (now_utc + datetime.timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")

        auth = req.signed_authorization
        auth_claims_bytes = canonical_bytes_for_auth_hmac(auth)
        auth_digest = hashlib.sha256(auth_claims_bytes).hexdigest().lower()

        # DEP02B-R3-01: Authoritative durable identity mapping recovery from ledger
        auth_identity = self._ledger.get_authorization_identity(auth.authorization_id)
        if auth_identity is None:
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code="ERR_REVOCATION_VERIFICATION_FAILED",
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        if not auth_identity.director_key_id or not auth_identity.operator_key_id:
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code="ERR_REVOCATION_VERIFICATION_FAILED",
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        # Bind mapping to authorization claims
        if (
            auth_identity.action_payload_digest_sha256 != auth.action_payload_digest_sha256
            or auth_identity.proposal_revision != auth.proposal_revision
        ):
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code="ERR_ACTION_PAYLOAD_SCHEMA_MISMATCH",
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        dir_key_id = auth_identity.director_key_id
        op_key_id = auth_identity.operator_key_id

        # Check 1: Revocation Verification & Anti-Rollback Epoch Check (DEP02B-02 / DEP02B-R2-01 / DEP02B-R3-01)
        try:
            self._check_key_revocation(
                authorization_id=auth.authorization_id,
                trial_key_id=auth.key_id,
                director_key_id=dir_key_id,
                operator_key_id=op_key_id,
                verification_time_utc=now_utc,
                require_existing_anchor=True,
            )
        except TrialSignerError as exc:
            err_code = str(exc).split(":")[0].strip()
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code=err_code,
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        # Check 2: Terminal execution check & Re-verification check
        if self._ledger.is_execution_terminal(auth.execution_id):
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code=ERR_EXECUTION_ALREADY_TERMINAL,
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        if self._ledger.is_execution_verified(auth.execution_id):
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code="ERR_EXECUTION_ALREADY_VERIFIED",
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        # Check 3: HMAC signature validity
        if not self._keystore.verify_authorization_hmac(auth_claims_bytes, auth.authorization_hmac):
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code="ERR_HMAC_SIGNATURE_INVALID",
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        # Check 4: Action payload digest match
        if req.action_payload_digest_sha256 != auth.action_payload_digest_sha256:
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code="ERR_ACTION_PAYLOAD_DIGEST_MISMATCH",
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        # Check 5: Target project and timeline UID match
        if (req.target_project_uid != auth.target_project_uid) or (req.target_timeline_uid != auth.target_timeline_uid):
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code="ERR_TARGET_COORDINATE_MISMATCH",
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        # Check 6: Proposal revision match
        if req.proposal_revision != auth.proposal_revision:
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code="ERR_PROPOSAL_REVISION_MISMATCH",
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        # Check 7: Expiry validation
        auth_valid_until_dt = parse_iso_utc_timestamp(auth.valid_until_iso, "valid_until_iso")
        if now_utc > auth_valid_until_dt:
            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFICATION_FAILED",
                is_valid=False,
                reason_code="ERR_AUTHORIZATION_EXPIRED",
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

        # Success path: record verified execution in ledger atomically under lock (DEP02B-R4-01)
        # Atomic ledger recording & response signing under lock (DEP02B-R5-01)
        with self._lock:
            if not self._running or self._stopping:
                raise TrialSignerError("ERR_DAEMON_NOT_RUNNING: daemon is stopping or stopped")

            if self._ledger.is_execution_terminal(auth.execution_id):
                return self._build_oracle_response(
                    req=req,
                    auth_digest=auth_digest,
                    verification_result="VERIFICATION_FAILED",
                    is_valid=False,
                    reason_code=ERR_EXECUTION_ALREADY_TERMINAL,
                    now_iso=now_iso,
                    valid_until_iso=valid_until_iso,
                )

            if self._ledger.is_execution_verified(auth.execution_id):
                return self._build_oracle_response(
                    req=req,
                    auth_digest=auth_digest,
                    verification_result="VERIFICATION_FAILED",
                    is_valid=False,
                    reason_code="ERR_EXECUTION_ALREADY_VERIFIED",
                    now_iso=now_iso,
                    valid_until_iso=valid_until_iso,
                )

            self._ledger.record_oracle_execution(
                OracleExecutionRecord(
                    schema_version=1,
                    execution_id=auth.execution_id,
                    authorization_id=auth.authorization_id,
                    proposal_revision=auth.proposal_revision,
                    target_project_uid=auth.target_project_uid,
                    target_timeline_uid=auth.target_timeline_uid,
                    verification_status="VERIFIED_VALID",
                    verified_at_iso=now_iso,
                )
            )

            return self._build_oracle_response(
                req=req,
                auth_digest=auth_digest,
                verification_result="VERIFIED_VALID",
                is_valid=True,
                reason_code="OK",
                now_iso=now_iso,
                valid_until_iso=valid_until_iso,
            )

    def _process_oracle_terminal_notification(
        self, req: OracleTerminalNotificationRequest
    ) -> OracleTerminalNotificationResponse:
        assert self._keystore is not None
        assert self._ledger is not None

        with self._lock:
            if not self._running or self._stopping:
                raise TrialSignerError("ERR_DAEMON_NOT_RUNNING: daemon is stopping or stopped")

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        if self._ledger.is_poisoned:
            return self._build_oracle_terminal_response(
                req=req,
                status="FAILED",
                reason_code="ERR_LEDGER_POISONED",
                recorded_at_iso=now_iso,
            )

        ver_rec = self._ledger.get_oracle_execution(req.execution_id)
        if ver_rec is None:
            return self._build_oracle_terminal_response(
                req=req,
                status="REJECTED",
                reason_code=ERR_EXECUTION_NOT_FOUND,
                recorded_at_iso=now_iso,
            )

        # Validate identity binding against verified execution
        if (
            ver_rec.authorization_id != req.authorization_id
            or ver_rec.proposal_revision != req.proposal_revision
            or ver_rec.target_project_uid != req.target_project_uid
            or ver_rec.target_timeline_uid != req.target_timeline_uid
        ):
            return self._build_oracle_terminal_response(
                req=req,
                status="REJECTED",
                reason_code=ERR_IDENTITY_BINDING_MISMATCH,
                recorded_at_iso=now_iso,
            )

        # Validate identity binding against durable authorization identity record
        auth_id_rec = self._ledger.get_authorization_identity(req.authorization_id)
        if auth_id_rec is None or (
            auth_id_rec.action_payload_digest_sha256 != req.action_payload_digest_sha256
            or auth_id_rec.proposal_revision != req.proposal_revision
        ):
            return self._build_oracle_terminal_response(
                req=req,
                status="REJECTED",
                reason_code=ERR_IDENTITY_BINDING_MISMATCH,
                recorded_at_iso=now_iso,
            )

        # Check existing terminal state
        existing_term = self._ledger.get_oracle_terminal_state(req.execution_id)
        if existing_term is not None:
            if existing_term.terminal_state == req.terminal_state:
                return self._build_oracle_terminal_response(
                    req=req,
                    status="TERMINAL_ALREADY_RECORDED",
                    reason_code="OK",
                    recorded_at_iso=now_iso,
                )
            else:
                return self._build_oracle_terminal_response(
                    req=req,
                    status="REJECTED",
                    reason_code=ERR_TERMINAL_STATE_CONFLICT,
                    recorded_at_iso=now_iso,
                )

        # Atomic commit to durable ledger under self._lock
        with self._lock:
            if not self._running or self._stopping:
                raise TrialSignerError("ERR_DAEMON_NOT_RUNNING: daemon is stopping or stopped")

            if self._ledger.is_poisoned:
                return self._build_oracle_terminal_response(
                    req=req,
                    status="FAILED",
                    reason_code="ERR_LEDGER_POISONED",
                    recorded_at_iso=now_iso,
                )

            # Re-check under lock
            existing_term = self._ledger.get_oracle_terminal_state(req.execution_id)
            if existing_term is not None:
                if existing_term.terminal_state == req.terminal_state:
                    return self._build_oracle_terminal_response(
                        req=req,
                        status="TERMINAL_ALREADY_RECORDED",
                        reason_code="OK",
                        recorded_at_iso=now_iso,
                    )
                else:
                    return self._build_oracle_terminal_response(
                        req=req,
                        status="REJECTED",
                        reason_code=ERR_TERMINAL_STATE_CONFLICT,
                        recorded_at_iso=now_iso,
                    )

            term_record = OracleExecutionTerminalRecord(
                schema_version=1,
                execution_id=req.execution_id,
                authorization_id=req.authorization_id,
                terminal_state=req.terminal_state,
                proposal_revision=req.proposal_revision,
                target_project_uid=req.target_project_uid,
                target_timeline_uid=req.target_timeline_uid,
                action_payload_digest_sha256=req.action_payload_digest_sha256,
                recorded_at_iso=now_iso,
            )

            try:
                self._ledger.record_oracle_terminal_state(term_record)
            except Exception as exc:
                err_code = str(exc).split(":")[0].strip()
                if "POISONED" in err_code:
                    return self._build_oracle_terminal_response(
                        req=req,
                        status="FAILED",
                        reason_code="ERR_LEDGER_POISONED",
                        recorded_at_iso=now_iso,
                    )
                raise

            return self._build_oracle_terminal_response(
                req=req,
                status="ACCEPTED",
                reason_code="OK",
                recorded_at_iso=now_iso,
            )

    def _build_oracle_terminal_response(
        self,
        *,
        req: OracleTerminalNotificationRequest,
        status: str,
        reason_code: str,
        recorded_at_iso: str,
    ) -> OracleTerminalNotificationResponse:
        assert self._keystore is not None

        response_id = str(uuid.uuid4())
        claims_dict = {
            "execution_id": req.execution_id,
            "reason_code": reason_code,
            "recorded_at_iso": recorded_at_iso,
            "request_id": req.request_id,
            "request_nonce": req.request_nonce,
            "response_id": response_id,
            "schema_version": 2,
            "status": status,
            "terminal_state": req.terminal_state,
        }
        claims_bytes = canonical_json_bytes(claims_dict)
        signature = self._keystore.sign_oracle_response(claims_bytes)

        return OracleTerminalNotificationResponse(
            schema_version=2,
            response_id=response_id,
            request_id=req.request_id,
            request_nonce=req.request_nonce,
            execution_id=req.execution_id,
            terminal_state=req.terminal_state,
            status=status,
            reason_code=reason_code,
            recorded_at_iso=recorded_at_iso,
            oracle_signature=signature,
        )

    def _build_oracle_response(
        self,
        *,
        req: OracleVerificationRequest,
        auth_digest: str,
        verification_result: str,
        is_valid: bool,
        reason_code: str,
        now_iso: str,
        valid_until_iso: str,
    ) -> OracleVerificationResponse:
        assert self._keystore is not None

        response_id = str(uuid.uuid4())
        claims_dict = {
            "action_payload_digest_sha256": req.action_payload_digest_sha256,
            "authorization_digest_sha256": auth_digest,
            "is_valid": is_valid,
            "proposal_revision": req.proposal_revision,
            "reason_code": reason_code,
            "request_id": req.request_id,
            "request_nonce": req.request_nonce,
            "response_id": response_id,
            "schema_version": 2,
            "target_project_uid": req.target_project_uid,
            "target_timeline_uid": req.target_timeline_uid,
            "valid_until_iso": valid_until_iso,
            "verification_result": verification_result,
            "verified_at_iso": now_iso,
        }
        claims_bytes = canonical_json_bytes(claims_dict)
        signature = self._keystore.sign_oracle_response(claims_bytes)

        return OracleVerificationResponse(
            schema_version=2,
            response_id=response_id,
            request_id=req.request_id,
            request_nonce=req.request_nonce,
            authorization_digest_sha256=auth_digest,
            action_payload_digest_sha256=req.action_payload_digest_sha256,
            target_project_uid=req.target_project_uid,
            target_timeline_uid=req.target_timeline_uid,
            proposal_revision=req.proposal_revision,
            verification_result=verification_result,
            is_valid=is_valid,
            reason_code=reason_code,
            verified_at_iso=now_iso,
            valid_until_iso=valid_until_iso,
            oracle_signature=signature,
        )

    # --------------------------------------------------------------------------
    # Public Client Endpoint Handling (DEP02B-01)
    # --------------------------------------------------------------------------
    def _handle_public_client(self, client_sock: socket.socket) -> None:
        try:
            if not self._running or self._stopping:
                return
            client_sock.settimeout(DEFAULT_SOCKET_TIMEOUT)
            raw_frame = decode_framed_message(client_sock)
            data = parse_strict_json(raw_frame)
            keys = set(data.keys())

            if keys == PING_REQUEST_ALLOWED_FIELDS:
                req = SignerPublicPingRequest.from_dict(data)
                resp = SignerPublicPingResponse(
                    schema_version=2,
                    ping_nonce=req.ping_nonce,
                    status="PONG",
                    daemon_status="OPERATIONAL",
                )
                client_sock.sendall(encode_framed_message(resp.canonical_bytes()))

            elif "expected_director_key_id" in keys and keys <= CHALLENGE_REQUEST_ALLOWED_FIELDS:
                req_ch = SignerPublicChallengeRequest.from_dict(data)
                resp_ch = self._process_public_challenge_request(req_ch)
                client_sock.sendall(encode_framed_message(resp_ch.canonical_bytes()))

            elif "consent_envelope" in keys and keys <= CONSENT_REQUEST_ALLOWED_FIELDS:
                req_cons = SignerPublicConsentRequest.from_dict(data)
                resp_cons = self._process_public_consent_request(req_cons)
                client_sock.sendall(encode_framed_message(resp_cons.canonical_bytes()))

            else:
                raise OracleProtocolError(
                    ERR_UNEXPECTED_FIELD,
                    f"Unrecognized or malformed public request envelope with keys: {sorted(keys)}",
                )
        except Exception as exc:
            try:
                err_code = getattr(exc, "code", None)
                if not err_code:
                    msg = str(exc)
                    if ":" in msg and msg.split(":", 1)[0].strip().startswith("ERR_"):
                        err_code = msg.split(":", 1)[0].strip()
                    else:
                        err_code = "ERR_REQUEST_FAILED"
                client_sock.sendall(
                    encode_framed_message(
                        json.dumps({
                            "schema_version": 2,
                            "error": err_code,
                            "detail": str(exc),
                        }).encode("utf-8")
                    )
                )
            except OSError:
                pass
        finally:
            try:
                client_sock.close()
            except OSError:
                pass

    def _deserialize_execution_plan(self, data: Union[TimelineExecutionPlan, Dict[str, Any]]) -> TimelineExecutionPlan:
        if isinstance(data, TimelineExecutionPlan):
            return data
        if not isinstance(data, dict):
            raise TrialSignerError(f"ERR_INVALID_PLAN_TYPE: expected dict or TimelineExecutionPlan, got {type(data).__name__}")

        ts_data = data.get("target_spec", {})
        if isinstance(ts_data, TargetSpec):
            target_spec = ts_data
        elif isinstance(ts_data, dict):
            fps_raw = ts_data.get("timeline_fps", (24, 1))
            if isinstance(fps_raw, RationalFPS):
                fps = fps_raw
            elif isinstance(fps_raw, str) and "/" in fps_raw:
                p = fps_raw.split("/")
                fps = RationalFPS(int(p[0]), int(p[1]))
            elif isinstance(fps_raw, (list, tuple)):
                fps = RationalFPS(int(fps_raw[0]), int(fps_raw[1]))
            elif isinstance(fps_raw, (int, float)):
                fps = RationalFPS(int(fps_raw), 1)
            else:
                fps = RationalFPS(24, 1)

            res_raw = ts_data.get("timeline_resolution", (1920, 1080))
            target_spec = TargetSpec(
                project_name=ts_data.get("project_name", ""),
                project_id=ts_data.get("project_id", ""),
                timeline_name=ts_data.get("timeline_name", ""),
                timeline_id=ts_data.get("timeline_id", ""),
                timeline_fps=fps,
                timeline_resolution=tuple(res_raw),
                start_timecode_frame=ts_data.get("start_timecode_frame", 0),
            )
        else:
            raise TrialSignerError("ERR_INVALID_TARGET_SPEC: target_spec must be dict or TargetSpec")

        actions: List[PlannedTimelineAction] = []
        for a in data.get("planned_actions", []):
            if isinstance(a, PlannedTimelineAction):
                actions.append(a)
                continue
            sr = a.get("source_range") or a.get("source_range_frames") or (0, 0)
            tr = a.get("timeline_range") or a.get("timeline_range_frames") or (0, 0)
            sr_frames = sr if isinstance(sr, TimeRangeFrames) else TimeRangeFrames(sr[0], sr[1])
            tr_frames = tr if isinstance(tr, TimeRangeFrames) else TimeRangeFrames(tr[0], tr[1])
            speed = a.get("applied_speed", Fraction(1, 1))
            if isinstance(speed, str) and "/" in speed:
                sp = speed.split("/")
                speed = Fraction(int(sp[0]), int(sp[1]))
            elif isinstance(speed, (int, float)):
                speed = Fraction(speed)

            actions.append(
                PlannedTimelineAction(
                    action_index=a.get("action_index", 0),
                    action_type=a.get("action_type", "INSERT_CLIP"),
                    clip_id=a.get("clip_id", ""),
                    role=a.get("role", "footage"),
                    source_file_path=a.get("source_file_path", ""),
                    source_range=sr_frames,
                    timeline_range=tr_frames,
                    target_track_type=a.get("target_track_type", "video"),
                    target_track_index=a.get("target_track_index", 1),
                    applied_speed=speed,
                    action_params=dict(a.get("action_params", {})),
                    action_idempotency_hash=a.get("action_idempotency_hash", ""),
                )
            )

        reservations: List[TargetTrackReservation] = []
        for r in data.get("track_reservations", []):
            if isinstance(r, TargetTrackReservation):
                reservations.append(r)
            elif isinstance(r, dict):
                reservations.append(
                    TargetTrackReservation(
                        track_type=r.get("track_type", "video"),
                        track_index=r.get("track_index", 1),
                        reserved_start_frame=r.get("reserved_start_frame", 0),
                        reserved_end_frame=r.get("reserved_end_frame", 0),
                        intended_usage=r.get("intended_usage", ""),
                    )
                )

        digests: List[SourceFileDigestBinding] = []
        for d in data.get("source_digests", []):
            if isinstance(d, SourceFileDigestBinding):
                digests.append(d)
            elif isinstance(d, dict):
                stat_id = d.get("stat_identity", (0, 0, 0, 0, 0))
                digests.append(
                    SourceFileDigestBinding(
                        clip_id=d.get("clip_id", ""),
                        absolute_path=d.get("absolute_path", ""),
                        full_sha256=d.get("full_sha256", ""),
                        stream_index=d.get("stream_index", 0),
                        stat_identity=tuple(stat_id),
                    )
                )

        tot_dur = data.get("total_timeline_duration_sec", Fraction(0, 1))
        if isinstance(tot_dur, str) and "/" in tot_dur:
            dp = tot_dur.split("/")
            tot_dur = Fraction(int(dp[0]), int(dp[1]))
        elif isinstance(tot_dur, (int, float)):
            tot_dur = Fraction(tot_dur)

        return TimelineExecutionPlan(
            plan_id=data.get("plan_id", ""),
            idempotency_key=data.get("idempotency_key", ""),
            proposal_id=data.get("proposal_id", ""),
            proposal_revision=data.get("proposal_revision", ""),
            approval_id=data.get("approval_id", ""),
            approved_at_iso=data.get("approved_at_iso", ""),
            target_spec=target_spec,
            occupancy_snapshot_id=data.get("occupancy_snapshot_id"),
            binding_status=data.get("binding_status", "BOUND_VERIFIED"),
            planned_actions=tuple(actions),
            planned_actions_count=len(actions),
            track_reservations=tuple(reservations),
            source_digests=tuple(digests),
            catalog_digests=tuple(data.get("catalog_digests", ())),
            accepted_omissions=tuple(data.get("accepted_omissions", ())),
            total_timeline_frames=data.get("total_timeline_frames", 0),
            total_timeline_duration_sec=tot_dur,
            issues=tuple(data.get("issues", ())),
            created_at_iso=data.get("created_at_iso", ""),
            schema_version=data.get("schema_version", 1),
        )

    def _process_public_challenge_request(self, req_in: Union[SignerPublicChallengeRequest, Dict[str, Any]]) -> SignerPublicChallengeResponse:
        with self._lock:
            if not self._running or self._stopping:
                raise TrialSignerError("ERR_DAEMON_NOT_RUNNING: daemon is stopping or stopped")
        req = req_in if isinstance(req_in, SignerPublicChallengeRequest) else SignerPublicChallengeRequest.from_dict(req_in)

        appr_env = SignedApprovalEnvelope.from_dict(req.approval_envelope) if isinstance(req.approval_envelope, dict) else req.approval_envelope
        prop = req.proposal if isinstance(req.proposal, EditPlanProposal) else normalize_edit_plan_proposal(req.proposal)
        plan = self._deserialize_execution_plan(req.execution_plan)

        if not req.director_public_key_pem:
            raise TrialSignerError("ERR_DIRECTOR_KEY_REQUIRED: director_public_key_pem required")

        dir_pub_key = serialization.load_pem_public_key(req.director_public_key_pem.encode("utf-8"))
        if not isinstance(dir_pub_key, Ed25519PublicKey):
            raise TrialSignerError("ERR_INVALID_KEY_TYPE: director_public_key_pem must be Ed25519")

        # 1. Verify Director Approval
        TrialAuthorizationSignerService.verify_director_approval(
            envelope=appr_env,
            proposal=prop,
            plan=plan,
            public_key=dir_pub_key,
            expected_director_key_id=req.expected_director_key_id,
        )

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        trust_dir = str(self._revocation_trust_dir or self._public_dir or "/tmp")
        reg_dir = str(self._revocation_registry_dir or self._public_dir or "/tmp")

        # 2. Check revocation
        if self._revocation_registry_dir is not None:
            self._check_key_revocation(
                director_key_id=req.expected_director_key_id,
                operator_key_id=req.operator_key_id,
                verification_time_utc=now_utc,
                require_existing_anchor=True,
            )

        # 3. Issue challenge on authority
        challenge_record = self._challenge_authority.issue_challenge(
            approval_envelope=appr_env,
            proposal=prop,
            execution_plan=plan,
            expected_director_key_id=req.expected_director_key_id,
            operator_id=req.operator_id,
            operator_key_id=req.operator_key_id,
            director_public_key=dir_pub_key,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=now_utc,
            ttl_seconds=req.ttl_seconds,
        )
        ch = challenge_record.challenge

        with self._lock:
            if not self._running or self._stopping:
                raise TrialSignerError("ERR_DAEMON_NOT_RUNNING: daemon is stopping or stopped")
            self._active_challenge_requests[ch.challenge_id] = {
                "approval_envelope": appr_env,
                "proposal": prop,
                "execution_plan": plan,
                "director_public_key": dir_pub_key,
                "expected_director_key_id": req.expected_director_key_id,
                "ttl_seconds": req.ttl_seconds,
            }

        return SignerPublicChallengeResponse(
            schema_version=2,
            request_id=req.request_id,
            status="CHALLENGE_ISSUED",
            challenge_id=ch.challenge_id,
            client_nonce=ch.client_nonce,
            preallocated_authorization_id=ch.preallocated_authorization_id,
            preallocated_execution_id=ch.preallocated_execution_id,
            issued_at_iso=ch.issued_at_iso,
            expires_at_iso=ch.expires_at_iso,
        )

    def _process_public_consent_request(self, req_in: Union[SignerPublicConsentRequest, Dict[str, Any]]) -> SignerPublicConsentResponse:
        assert self._keystore is not None
        assert self._ledger is not None

        with self._lock:
            if not self._running or self._stopping:
                raise TrialSignerError("ERR_DAEMON_NOT_RUNNING: daemon is stopping or stopped")
        req = req_in if isinstance(req_in, SignerPublicConsentRequest) else SignerPublicConsentRequest.from_dict(req_in)
        consent_env = SignedOperatorConsentEnvelope.from_dict(req.consent_envelope)

        if not req.operator_public_key_pem:
            raise TrialSignerError("ERR_OPERATOR_KEY_REQUIRED: operator_public_key_pem required")

        op_pub_key = serialization.load_pem_public_key(req.operator_public_key_pem.encode("utf-8"))
        if not isinstance(op_pub_key, Ed25519PublicKey):
            raise TrialSignerError("ERR_INVALID_KEY_TYPE: operator_public_key_pem must be Ed25519")

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        trust_dir = str(self._revocation_trust_dir or self._public_dir or "/tmp")
        reg_dir = str(self._revocation_registry_dir or self._public_dir or "/tmp")

        with self._lock:
            ch_data = self._active_challenge_requests.get(req.challenge_id)
        if ch_data is None:
            if self._ledger.has_challenge_id(req.challenge_id) or (
                hasattr(self._challenge_authority, "_consumed_challenges")
                and req.challenge_id in self._challenge_authority._consumed_challenges
            ):
                raise TrialSignerError(f"ERR_CHALLENGE_ALREADY_CONSUMED: challenge '{req.challenge_id}' has already been consumed")
            raise TrialSignerError(f"ERR_CHALLENGE_NOT_FOUND: challenge '{req.challenge_id}' not found")

        appr_env = ch_data["approval_envelope"]
        prop = ch_data["proposal"]
        plan = ch_data["execution_plan"]
        dir_pub_key = ch_data["director_public_key"]
        expected_dir_key_id = ch_data["expected_director_key_id"]

        # 1. Check revocation against both Director and Operator key IDs
        if self._revocation_registry_dir is not None:
            self._check_key_revocation(
                director_key_id=expected_dir_key_id,
                operator_key_id=consent_env.operator_key_id,
                verification_time_utc=now_utc,
                require_existing_anchor=True,
            )

        # 2. Admission verification and authority registration
        adm_ev = self._challenge_authority.verify_and_register_admission_for_local_test(
            challenge_id=req.challenge_id,
            approval_envelope=appr_env,
            proposal=prop,
            execution_plan=plan,
            director_public_key=dir_pub_key,
            expected_director_key_id=expected_dir_key_id,
            operator_consent_envelope=consent_env,
            operator_public_key=op_pub_key,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            verification_time_utc=now_utc,
            ttl_seconds=float(ch_data["ttl_seconds"]),
        )

        # 3. Consume challenge on authority
        cons_result = self._challenge_authority.verify_and_consume_challenge_for_local_test(
            challenge_id=req.challenge_id,
            consent_envelope=consent_env,
            operator_public_key=op_pub_key,
            revocation_trust_directory=trust_dir,
            revocation_registry_directory=reg_dir,
            nonce_ledger_directory=self._ledger._ledger_dir,
        )

        # 4. Reserve evidence
        reserved_ev = self._challenge_authority.reserve_consumed_evidence_for_issuance(
            req.challenge_id, cons_result
        )

        # 5. Create internal signing ticket
        ticket = self._challenge_authority.create_issuance_signing_ticket(
            challenge_id=req.challenge_id,
            consumption_result=cons_result,
            admission_evidence=adm_ev,
            approval_envelope=appr_env,
            proposal=prop,
            execution_plan=plan,
            operator_consent_envelope=consent_env,
            verification_time_utc=now_utc,
            ttl_seconds=ch_data["ttl_seconds"],
        )

        # 6. Build canonical TrialExecutionAuthorization
        auth_id = reserved_ev.preallocated_authorization_id
        exec_id = reserved_ev.preallocated_execution_id
        appr_dict = approval_record_to_dict(appr_env.approval_record)
        appr_digest = hashlib.sha256(canonical_json_bytes(appr_dict)).hexdigest()
        computed_prop_rev = compute_proposal_revision(prop)

        fps_num = int(plan.target_spec.timeline_fps.numerator)
        fps_den = int(plan.target_spec.timeline_fps.denominator)
        rec_frame = consent_env.record_frame
        issued_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        valid_iso = (now_utc + datetime.timedelta(seconds=min(req.ttl_seconds, 300.0))).strftime("%Y-%m-%dT%H:%M:%SZ")

        temp_auth = TrialExecutionAuthorization(
            authorization_id=auth_id,
            key_id="signer_hmac_production",
            operator_gesture_digest_sha256=consent_env.compute_consent_digest(),
            approval_record_id=appr_env.approval_record.approval_id,
            approval_record_digest_sha256=appr_digest,
            proposal_id=prop.proposal_id,
            proposal_revision=computed_prop_rev,
            proposal_content_digest_sha256=appr_env.proposal_content_digest_sha256,
            plan_idempotency_key=plan.idempotency_key,
            plan_digest_sha256=appr_env.plan_digest_sha256,
            action_payload_digest_sha256=consent_env.action_payload_digest_sha256,
            target_project_uid=consent_env.target_project_uid,
            target_project_name=consent_env.target_project_name,
            target_timeline_uid=consent_env.target_timeline_uid,
            target_timeline_name=consent_env.target_timeline_name,
            target_timeline_fps_numerator=fps_num,
            target_timeline_fps_denominator=fps_den,
            observed_timeline_start_frame=rec_frame,
            authorized_record_frame=rec_frame,
            source_file_path=consent_env.source_file_path,
            source_file_sha256=consent_env.source_file_sha256,
            authorized_action_type="INSERT_CLIP",
            authorized_track_type="video",
            authorized_track_index=1,
            authorized_timeline_frame_start=0,
            authorized_timeline_frame_duration=consent_env.duration_frames,
            authorized_source_frame_start=0,
            authorized_source_frame_duration=consent_env.duration_frames,
            execution_id=exec_id,
            issued_at_iso=issued_iso,
            valid_until_iso=valid_iso,
            token_nonce=exec_id,
            authorization_hmac="0" * 64,
        )

        claims_to_sign = canonical_bytes_for_auth_hmac(temp_auth)

        # Atomic commit & sign boundary (DEP02B-R4-01)
        with self._lock:
            if not self._running or self._stopping:
                raise TrialSignerError("ERR_DAEMON_STOPPING: daemon is stopping; cannot commit or sign authorization")

            # 5. Record consumed nonce in durable nonce ledger
            self._ledger.record_consumed_nonce(
                ConsumedAuthorizationNonceRecord(
                    schema_version=1,
                    challenge_id=req.challenge_id,
                    client_nonce=cons_result.client_nonce,
                    approval_record_id=appr_env.approval_record.approval_id,
                    proposal_revision=computed_prop_rev,
                    consumed_at_iso=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )

            # 6. Sign canonical claims using keystore HMAC key
            auth_hmac = self._keystore.sign_authorization_hmac(claims_to_sign)

            final_auth = TrialExecutionAuthorization(
                **{**temp_auth.__dict__, "authorization_hmac": auth_hmac}
            )

            if not self._keystore.verify_authorization_hmac(claims_to_sign, auth_hmac):
                raise TrialSignerError("ERR_HMAC_ROUNDTRIP_FAILED: internal signature verification failure")

            # 7. Record authoritative identity binding in durable ledger (DEP02B-R3-01)
            self._ledger.record_authorization_identity(
                AuthorizationIdentityRecord(
                    schema_version=1,
                    authorization_id=final_auth.authorization_id,
                    director_key_id=expected_dir_key_id,
                    operator_key_id=consent_env.operator_key_id,
                    action_payload_digest_sha256=final_auth.action_payload_digest_sha256,
                    proposal_revision=final_auth.proposal_revision,
                    issued_at_iso=final_auth.issued_at_iso,
                )
            )

            self._challenge_authority._consume_issuance_signing_ticket(ticket)
            self._active_challenge_requests.pop(req.challenge_id, None)

        return SignerPublicConsentResponse(
            schema_version=2,
            request_id=req.request_id,
            status="AUTHORIZATION_ISSUED",
            signed_authorization=final_auth.__dict__,
        )

    def stop(self) -> None:
        """Gracefully stops daemon listeners, drains active workers, unlinks owned sockets, and closes descriptors."""
        with self._lock:
            if not self._started:
                return
            if not self._running and not self._stopping:
                return
            self._running = False
            self._stopping = True

            # Close listener sockets under lock to prevent any new connections
            oracle_sock = self._oracle_sock
            self._oracle_sock = None
            public_sock = self._public_sock
            self._public_sock = None

            if oracle_sock is not None:
                try:
                    oracle_sock.close()
                except OSError:
                    pass
            if public_sock is not None:
                try:
                    public_sock.close()
                except OSError:
                    pass

        # Release self._lock before joining threads! (DEP02B-R2-04)
        if self._oracle_thread is not None and self._oracle_thread.is_alive():
            self._oracle_thread.join(timeout=2.0)
            self._oracle_thread = None

        if self._public_thread is not None and self._public_thread.is_alive():
            self._public_thread.join(timeout=2.0)
            self._public_thread = None

        with self._worker_threads_lock:
            workers = list(self._active_worker_threads)
        for w in workers:
            w.join(timeout=2.0)

        with self._worker_threads_lock:
            remaining = [w for w in self._active_worker_threads if w.is_alive()]
        if remaining:
            # DEP02B-R3-02: Do NOT close descriptors or report successful shutdown while workers remain.
            # Retain resources needed by remaining workers and raise structured teardown error.
            raise SignerDaemonTeardownError(
                f"ERR_DAEMON_DRAIN_TIMEOUT: {len(remaining)} worker thread(s) failed to drain within timeout; descriptors retained"
            )

        # All workers have cleanly exited. Proceed to final teardown under lock.
        with self._lock:
            # Unlink ONLY owned sockets
            self._cleanup_socket_node("oracle", self._oracle_dir_fd, self._oracle_sock_name, self._oracle_sock_dev_ino)
            self._cleanup_socket_node("public", self._public_dir_fd, self._public_sock_name, self._public_sock_dev_ino)

            # Close lock descriptors (locks remain on disk, never unlinked)
            if self._oracle_lock_fd is not None:
                try:
                    os.close(self._oracle_lock_fd)
                except OSError as e:
                    self._cleanup_failures.append(("oracle_lock_close", e))
                self._oracle_lock_fd = None

            if self._public_lock_fd is not None:
                try:
                    os.close(self._public_lock_fd)
                except OSError as e:
                    self._cleanup_failures.append(("public_lock_close", e))
                self._public_lock_fd = None

            # Close directory descriptors
            if self._oracle_dir_fd is not None:
                try:
                    os.close(self._oracle_dir_fd)
                except OSError as e:
                    self._cleanup_failures.append(("oracle_dir_close", e))
                self._oracle_dir_fd = None

            if self._public_dir_fd is not None:
                try:
                    os.close(self._public_dir_fd)
                except OSError as e:
                    self._cleanup_failures.append(("public_dir_close", e))
                self._public_dir_fd = None

            self._started = False
            self._stopping = False

            if self._cleanup_failures:
                first_failure = self._cleanup_failures[0][1]
                raise SignerDaemonTeardownError(f"ERR_DAEMON_TEARDOWN_FAILED: {first_failure}")

    def _cleanup_socket_node(
        self,
        endpoint_type: str,
        dir_fd: Optional[int],
        sock_name: str,
        expected_dev_ino: Optional[Tuple[int, int]],
    ) -> None:
        if dir_fd is None or expected_dev_ino is None:
            return

        try:
            st = os.stat(sock_name, dir_fd=dir_fd, follow_symlinks=False)
            if not stat.S_ISSOCK(st.st_mode):
                self._cleanup_failures.append(
                    (f"{endpoint_type}_socket_unlink", RuntimeError(f"Entry '{sock_name}' is not a socket"))
                )
                return

            if (st.st_dev, st.st_ino) != expected_dev_ino:
                self._cleanup_failures.append(
                    (f"{endpoint_type}_socket_unlink", RuntimeError(f"Entry '{sock_name}' inode changed"))
                )
                return

            os.unlink(sock_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        except OSError as e:
            self._cleanup_failures.append((f"{endpoint_type}_socket_unlink", e))
