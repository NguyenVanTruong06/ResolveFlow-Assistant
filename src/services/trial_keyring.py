"""
src/services/trial_keyring.py
Authoritative Purpose-Separated Keyring Authority for Isolated Resolve Gate (G07-07B6-I2).

Architectural Reference:
  - docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md (Section 2.6)
  - docs/architecture/07B6-mock-only-implementation-plan.md (Section 2.2, Section 3.2)
  - Workorder: .bridge/task.md (G07-07B6-I2)

Safety Commitments:
  - Loads cryptographic keys exclusively from a secure local keystore directory.
  - Descriptor-anchored directory access with O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC.
  - Strict mode and ownership validation: directory 0700, key files 0600, uid == getuid().
  - Rigorous file validation: regular file only, nlink == 1, 32..4096 bytes, stable metadata.
  - Purpose separation: trial-signing keys (*.key) strictly separated from operator-reconciliation keys (*.opkey.json).
  - Strict operator key schema: exact 3 fields, duplicate member rejection, 32..64 bytes entropy bounds.
  - Exception-safe descriptor lifecycle: closes all opened descriptors on failure, preserves original failure.
"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    ReadbackReceiptVerificationError,
    WholePlanReconciliationError,
)

# --- Architectural Constants ---
REQUIRED_OPKEY_SCHEMA_KEYS: FrozenSet[str] = frozenset(
    {"key_hex", "permitted_operator_identity", "purpose"}
)
ALLOWED_OPKEY_PURPOSES: FrozenSet[str] = frozenset(
    {"TIMELINE_AMBIGUOUS_RECONCILIATION"}
)
MIN_OPERATOR_KEY_BYTES: int = 32
MAX_OPERATOR_KEY_BYTES: int = 64
MIN_KEYFILE_SIZE: int = 32
MAX_KEYFILE_SIZE: int = 4096

HEX_PATTERN: re.Pattern[str] = re.compile(r"^[0-9a-fA-F]+$")


@dataclass(frozen=True)
class DescriptorCleanupFailure:
    """Records a failure to close a descriptor during keystore lifecycle cleanup."""

    descriptor_type: str  # "dir_fd" or "file_fd"
    target_path: str
    fd: int
    error_message: str
    close_outcome: str = "CLOSE_OUTCOME_UNKNOWN"


@dataclass(frozen=True)
class OperatorKeyRecord:
    """Authoritative immutable DTO representing an authorized operator credential."""

    key_id: str
    key_bytes: bytes
    permitted_operator_identity: str
    key_purpose: str

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not self.key_id.strip():
            raise AcceptanceGateError("ERR_INVALID_KEY_ID: key_id must be non-empty string")
        if not isinstance(self.key_bytes, bytes):
            raise AcceptanceGateError(
                f"ERR_OPKEY_INVALID_FIELD_TYPE: key_bytes must be bytes, got {type(self.key_bytes).__name__}"
            )
        if len(self.key_bytes) < MIN_OPERATOR_KEY_BYTES:
            raise AcceptanceGateError(
                f"ERR_OPKEY_ENTROPY_INSUFFICIENT: key_bytes length {len(self.key_bytes)} < {MIN_OPERATOR_KEY_BYTES} bytes"
            )
        if len(self.key_bytes) > MAX_OPERATOR_KEY_BYTES:
            raise AcceptanceGateError(
                f"ERR_OPKEY_ENTROPY_EXCEEDS_MAX: key_bytes length {len(self.key_bytes)} > {MAX_OPERATOR_KEY_BYTES} bytes"
            )
        if not isinstance(self.permitted_operator_identity, str) or not self.permitted_operator_identity.strip():
            raise AcceptanceGateError("ERR_OPKEY_EMPTY_OPERATOR_IDENTITY: permitted_operator_identity must be non-empty")
        if self.key_purpose not in ALLOWED_OPKEY_PURPOSES:
            raise AcceptanceGateError(
                f"ERR_OPKEY_UNAUTHORIZED_PURPOSE: purpose '{self.key_purpose}' not in {sorted(ALLOWED_OPKEY_PURPOSES)}"
            )


def reject_opkey_duplicate_json_keys(ordered_pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """
    JSON object_pairs_hook that detects and rejects duplicate JSON members before dictionary construction (R47-01).
    Fails closed with ERR_OPKEY_DUPLICATE_FIELD.
    """
    result: Dict[str, Any] = {}
    for key, value in ordered_pairs:
        if key in result:
            raise AcceptanceGateError(
                f"ERR_OPKEY_DUPLICATE_FIELD: Duplicate field detected in operator key descriptor: {key!r}"
            )
        result[key] = value
    return result


class TrialKeyringAuthority:
    """
    Loads cryptographic trial keys and operator credentials exclusively from a secure local
    filesystem keystore via descriptor-anchored fail-closed operations.
    """

    def __init__(self, keystore_path: str) -> None:
        if not isinstance(keystore_path, str) or not keystore_path.strip():
            raise AcceptanceGateError("ERR_KEYSTORE_CANNOT_OPEN: keystore_path must be a non-empty string")

        self._keystore_path: str = keystore_path
        self._keys: Dict[str, bytes] = {}
        self._operator_keys: Dict[str, OperatorKeyRecord] = {}
        self._revoked: Set[str] = set()

        dir_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )

        dir_fd: Optional[int] = None
        orig_exc: Optional[BaseException] = None
        cleanup_failures: List[DescriptorCleanupFailure] = []
        try:
            try:
                dir_fd = os.open(keystore_path, dir_flags)
            except OSError as e:
                if e.errno == errno.ELOOP or (e.errno == errno.ENOTDIR and os.path.islink(keystore_path)):
                    raise AcceptanceGateError(f"ERR_KEYSTORE_SYMLINK_DETECTED: {keystore_path} is a symlink: {e}")
                if e.errno == errno.ENOTDIR:
                    raise AcceptanceGateError(f"ERR_KEYSTORE_NOT_DIR: {keystore_path}: {e}")
                raise AcceptanceGateError(f"ERR_KEYSTORE_CANNOT_OPEN: {keystore_path}: {e}")

            st = os.fstat(dir_fd)
            if not stat.S_ISDIR(st.st_mode):
                raise AcceptanceGateError(f"ERR_KEYSTORE_NOT_DIR: {keystore_path}")
            if st.st_uid != os.getuid():
                raise AcceptanceGateError(f"ERR_KEYSTORE_INVALID_OWNER: uid {st.st_uid} != current uid {os.getuid()}")
            if (st.st_mode & 0o077) != 0:
                raise AcceptanceGateError(f"ERR_KEYSTORE_INSECURE_PERMS: mode must be 0700, got {oct(st.st_mode)}")

            try:
                entries = sorted(os.listdir(dir_fd))
            except OSError as e:
                raise AcceptanceGateError(f"ERR_KEYSTORE_CANNOT_LIST: {keystore_path}: {e}")

            file_flags = (
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )

            for fname in entries:
                if fname.startswith("."):
                    continue

                ffd: Optional[int] = None
                file_orig_exc: Optional[BaseException] = None
                try:
                    try:
                        ffd = os.open(fname, file_flags, dir_fd=dir_fd)
                    except OSError as e:
                        if e.errno == errno.ELOOP:
                            raise AcceptanceGateError(f"ERR_KEYFILE_SYMLINK_DETECTED: {fname} is a symlink: {e}")
                        raise AcceptanceGateError(f"ERR_KEYFILE_CANNOT_OPEN: {fname}: {e}")

                    fst = os.fstat(ffd)
                    if stat.S_ISLNK(fst.st_mode):
                        raise AcceptanceGateError(f"ERR_KEYFILE_SYMLINK_DETECTED: {fname} is a symlink")
                    if not stat.S_ISREG(fst.st_mode):
                        raise AcceptanceGateError(f"ERR_KEYFILE_NOT_REGULAR: {fname}")
                    if fst.st_nlink != 1:
                        raise AcceptanceGateError(f"ERR_KEYFILE_HARDLINK_DETECTED: {fname} has nlink={fst.st_nlink}")
                    if fst.st_uid != os.getuid():
                        raise AcceptanceGateError(
                            f"ERR_KEYFILE_INVALID_OWNER: {fname} uid {fst.st_uid} != {os.getuid()}"
                        )
                    if (fst.st_mode & 0o077) != 0 or (fst.st_mode & 0o111) != 0:
                        raise AcceptanceGateError(
                            f"ERR_KEYFILE_INSECURE_PERMS: {fname} mode must be 0600, got {oct(fst.st_mode)}"
                        )
                    if fst.st_size < MIN_KEYFILE_SIZE or fst.st_size > MAX_KEYFILE_SIZE:
                        raise AcceptanceGateError(
                            f"ERR_KEYFILE_INVALID_SIZE: {fname} size {fst.st_size} not in [{MIN_KEYFILE_SIZE}, {MAX_KEYFILE_SIZE}]"
                        )

                    content = os.read(ffd, fst.st_size)
                    if len(content) != fst.st_size:
                        raise AcceptanceGateError(
                            f"ERR_KEYFILE_SHORT_READ: {fname} expected {fst.st_size} bytes, read {len(content)}"
                        )

                    fst2 = os.fstat(ffd)
                    if fst2.st_size != fst.st_size or fst2.st_ino != fst.st_ino:
                        raise AcceptanceGateError(f"ERR_KEYFILE_UNSTABLE: {fname}")

                    # --- Dispatch by filename pattern ---
                    if fname.endswith(".opkey.json"):
                        kid = fname[:-11]
                        if not kid or kid.startswith("."):
                            raise AcceptanceGateError(f"ERR_KEYFILE_CANNOT_OPEN: invalid operator key filename {fname}")
                        if kid in self._operator_keys or kid in self._keys:
                            raise AcceptanceGateError(f"ERR_KEY_ID_COLLISION: key_id {kid!r} already loaded")

                        try:
                            data = json.loads(
                                content.decode("utf-8"),
                                object_pairs_hook=reject_opkey_duplicate_json_keys,
                            )
                        except AcceptanceGateError:
                            raise
                        except Exception as e:
                            raise AcceptanceGateError(f"ERR_OPKEY_INVALID_JSON: {fname}: {e}")

                        if not isinstance(data, dict):
                            raise AcceptanceGateError(f"ERR_OPKEY_SCHEMA_NOT_OBJECT: {fname} root must be a JSON object")

                        keys = set(data.keys())
                        missing = REQUIRED_OPKEY_SCHEMA_KEYS - keys
                        if missing:
                            raise AcceptanceGateError(
                                f"ERR_OPKEY_SCHEMA_MISSING_FIELD: {fname} missing required key: {sorted(missing)}"
                            )
                        unknown = keys - REQUIRED_OPKEY_SCHEMA_KEYS
                        if unknown:
                            raise AcceptanceGateError(
                                f"ERR_OPKEY_SCHEMA_UNKNOWN_FIELD: {fname} contains unknown key: {sorted(unknown)}"
                            )

                        for k in ("key_hex", "permitted_operator_identity", "purpose"):
                            if type(data[k]) is not str:
                                raise AcceptanceGateError(
                                    f"ERR_OPKEY_INVALID_FIELD_TYPE: {fname} field '{k}' must be string, got {type(data[k]).__name__}"
                                )

                        purpose = data["purpose"]
                        if purpose not in ALLOWED_OPKEY_PURPOSES:
                            raise AcceptanceGateError(
                                f"ERR_OPKEY_UNAUTHORIZED_PURPOSE: {fname} purpose '{purpose}' not in {sorted(ALLOWED_OPKEY_PURPOSES)}"
                            )

                        identity = data["permitted_operator_identity"].strip()
                        if not identity:
                            raise AcceptanceGateError(
                                f"ERR_OPKEY_EMPTY_OPERATOR_IDENTITY: {fname} identity must be non-empty"
                            )

                        key_hex = data["key_hex"]
                        if len(key_hex) % 2 != 0 or not HEX_PATTERN.fullmatch(key_hex):
                            raise AcceptanceGateError(f"ERR_OPKEY_MALFORMED_HEX: {fname} invalid hex formatting")

                        key_bytes = bytes.fromhex(key_hex)
                        if len(key_bytes) < MIN_OPERATOR_KEY_BYTES:
                            raise AcceptanceGateError(
                                f"ERR_OPKEY_ENTROPY_INSUFFICIENT: {fname} decoded key length {len(key_bytes)} < {MIN_OPERATOR_KEY_BYTES} bytes"
                            )
                        if len(key_bytes) > MAX_OPERATOR_KEY_BYTES:
                            raise AcceptanceGateError(
                                f"ERR_OPKEY_ENTROPY_EXCEEDS_MAX: {fname} decoded key length {len(key_bytes)} > {MAX_OPERATOR_KEY_BYTES} bytes"
                            )

                        self._operator_keys[kid] = OperatorKeyRecord(
                            key_id=kid,
                            key_bytes=key_bytes,
                            permitted_operator_identity=identity,
                            key_purpose=purpose,
                        )

                    elif fname.endswith(".key"):
                        kid = fname[:-4]
                        if not kid or kid.startswith("."):
                            raise AcceptanceGateError(f"ERR_KEYFILE_CANNOT_OPEN: invalid trial key filename {fname}")
                        if kid in self._keys or kid in self._operator_keys:
                            raise AcceptanceGateError(f"ERR_KEY_ID_COLLISION: key_id {kid!r} already loaded")
                        self._keys[kid] = content

                    elif fname == "revocations.json":
                        try:
                            data = json.loads(content.decode("utf-8"))
                        except Exception as e:
                            raise AcceptanceGateError(f"ERR_REVOCATIONS_INVALID_JSON: {fname}: {e}")

                        if not isinstance(data, dict):
                            raise AcceptanceGateError(f"ERR_REVOCATIONS_SCHEMA_NOT_OBJECT: {fname} root must be a JSON object")

                        if "revoked_key_ids" not in data:
                            raise AcceptanceGateError(f"ERR_REVOCATIONS_SCHEMA_MISSING_FIELD: {fname} missing revoked_key_ids")

                        revoked_list = data["revoked_key_ids"]
                        if not isinstance(revoked_list, list):
                            raise AcceptanceGateError(
                                f"ERR_REVOCATIONS_INVALID_FIELD_TYPE: {fname} revoked_key_ids must be a list"
                            )

                        for item in revoked_list:
                            if not isinstance(item, str) or not item.strip():
                                raise AcceptanceGateError(
                                    f"ERR_REVOCATIONS_INVALID_ENTRY: {fname} revoked key id must be non-empty string"
                                )
                            self._revoked.add(item)

                    else:
                        raise AcceptanceGateError(f"ERR_KEYSTORE_UNKNOWN_FILE: Unrecognized keystore entry: {fname}")

                except BaseException as fe:
                    file_orig_exc = fe
                    raise
                finally:
                    if ffd is not None:
                        try:
                            os.close(ffd)
                        except OSError as close_err:
                            cleanup_failures.append(
                                DescriptorCleanupFailure(
                                    descriptor_type="file_fd",
                                    target_path=fname,
                                    fd=ffd,
                                    error_message=str(close_err),
                                    close_outcome="CLOSE_OUTCOME_UNKNOWN",
                                )
                            )

        except BaseException as de:
            orig_exc = de
            raise
        finally:
            if dir_fd is not None:
                try:
                    os.close(dir_fd)
                except OSError as close_err:
                    cleanup_failures.append(
                        DescriptorCleanupFailure(
                            descriptor_type="dir_fd",
                            target_path=keystore_path,
                            fd=dir_fd,
                            error_message=str(close_err),
                            close_outcome="CLOSE_OUTCOME_UNKNOWN",
                        )
                    )

            if orig_exc is not None:
                setattr(orig_exc, "cleanup_failures", list(cleanup_failures))
                for cf in cleanup_failures:
                    note = (
                        f"CLEANUP_FAILURE: descriptor_type={cf.descriptor_type}, "
                        f"target_path={cf.target_path}, fd={cf.fd}, "
                        f"error={cf.error_message}, close_outcome={cf.close_outcome}"
                    )
                    if hasattr(orig_exc, "add_note"):
                        orig_exc.add_note(note)
            elif cleanup_failures:
                first_fail = cleanup_failures[0]
                err_code = (
                    "ERR_KEYSTORE_CLOSE_FAILED"
                    if first_fail.descriptor_type == "dir_fd"
                    else "ERR_KEYFILE_CLOSE_FAILED"
                )
                err = AcceptanceGateError(
                    f"{err_code}: cleanup failed for {first_fail.target_path}: {first_fail.error_message}"
                )
                setattr(err, "cleanup_failures", list(cleanup_failures))
                for cf in cleanup_failures:
                    note = (
                        f"CLEANUP_FAILURE: descriptor_type={cf.descriptor_type}, "
                        f"target_path={cf.target_path}, fd={cf.fd}, "
                        f"error={cf.error_message}, close_outcome={cf.close_outcome}"
                    )
                    if hasattr(err, "add_note"):
                        err.add_note(note)
                raise err

    @property
    def keystore_path(self) -> str:
        """Returns the verified keystore directory path."""
        return self._keystore_path

    def get_key(self, key_id: str) -> bytes:
        """
        Retrieves a standard trial-signing key by key_id.
        Fails closed if the key is revoked, missing, or belongs exclusively to the operator namespace.
        """
        if not isinstance(key_id, str) or not key_id.strip():
            raise ReadbackReceiptVerificationError("ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED: Invalid key_id")
        if key_id in self._revoked:
            raise ReadbackReceiptVerificationError(f"ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED: key '{key_id}' is revoked")
        if key_id not in self._keys:
            if key_id in self._operator_keys:
                raise ReadbackReceiptVerificationError(
                    f"ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED: key '{key_id}' is an operator credential, not a trial key"
                )
            raise ReadbackReceiptVerificationError(f"ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED: key '{key_id}' not found")
        return self._keys[key_id]

    def get_operator_key(self, key_id: str) -> OperatorKeyRecord:
        """
        Retrieves an operator-reconciliation key record by key_id.
        Fails closed with ERR_TRIAL_KEY_NOT_PERMITTED_FOR_RECONCILIATION if key_id is a trial key.
        Fails closed with ERR_OPERATOR_KEY_REVOKED if revoked.
        Fails closed with ERR_UNKNOWN_OPERATOR_KEY_ID if unknown.
        """
        if not isinstance(key_id, str) or not key_id.strip():
            raise AcceptanceGateError("ERR_INVALID_KEY_ID: key_id must be non-empty string")
        if key_id in self._revoked:
            raise AcceptanceGateError(f"ERR_OPERATOR_KEY_REVOKED: key '{key_id}' is revoked")
        if key_id not in self._operator_keys:
            if key_id in self._keys:
                raise WholePlanReconciliationError(
                    f"ERR_TRIAL_KEY_NOT_PERMITTED_FOR_RECONCILIATION: key '{key_id}' is a trial-signing key, not an authorized operator credential"
                )
            raise WholePlanReconciliationError(
                f"ERR_UNKNOWN_OPERATOR_KEY_ID: operator_key_id '{key_id}' not found in operator keystore"
            )
        return self._operator_keys[key_id]

    def revoke_key(self, key_id: str) -> None:
        """
        Revokes a key ID dynamically in-memory following the accepted design specification.
        Subsequent lookups for this key_id in either namespace fail closed immediately.
        """
        if not isinstance(key_id, str) or not key_id.strip():
            raise AcceptanceGateError("ERR_INVALID_KEY_ID: key_id must be non-empty string")
        self._revoked.add(key_id)

    def is_revoked(self, key_id: str) -> bool:
        """Returns True if the given key_id is marked as revoked."""
        return key_id in self._revoked
