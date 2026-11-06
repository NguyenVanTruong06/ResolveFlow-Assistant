"""Signer Keystore Service.

Implements descriptor-anchored, leak-free loading and management of:
1. HMAC-SHA256 Authorization Secret Key (trial_auth.key)
2. Ed25519 Oracle Signing Private Key (oracle_private.key)

Enforces strict POSIX descriptor validations: O_NOFOLLOW, O_CLOEXEC,
single-link policy, ownership/mode assertion (0700 dir, 0600 file),
bounded reads with EOF probes, and pre/post-read inode stability checks.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import os
import stat
from pathlib import Path
from typing import Optional, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from src.services.trial_authorization_signer_service import (
    TrialSignerError,
)

DEFAULT_KEYSTORE_DIR: str = "/var/lib/resolveflow/signer/keystore"
HMAC_KEY_FILENAME: str = "trial_auth.key"
ORACLE_KEY_FILENAME: str = "oracle_private.key"
RAW_KEY_LENGTH: int = 32

PRODUCTION_SIGNER_UID: int = 901
PRODUCTION_SIGNER_GID: int = 901


class SignerKeystoreError(TrialSignerError):
    """Base error for signer keystore operations."""


class SignerKeystoreSecurityError(SignerKeystoreError):
    """Raised on security violation (symlink, mode, ownership, hardlinks)."""


class SignerKeystoreCorruptionError(SignerKeystoreError):
    """Raised on corrupt or invalid key length / content."""


class SignerKeystoreService:
    """Descriptor-anchored keystore service.

    Holds HMAC and Ed25519 cryptographic material in memory with zero leakage.
    Provides narrow sign/verify capabilities only.
    """

    def __init__(
        self,
        keystore_dir: Optional[Union[str, Path]] = None,
        *,
        keystore_fd: Optional[int] = None,
        expected_uid: int = PRODUCTION_SIGNER_UID,
        expected_gid: int = PRODUCTION_SIGNER_GID,
        enforce_ownership: bool = True,
    ) -> None:
        """Initializes keystore service.

        Must not access default production paths during test/import.
        """
        if keystore_dir is not None and keystore_fd is not None:
            raise SignerKeystoreSecurityError("ERR_AMBIGUOUS_KEYSTORE_INPUT: specify keystore_dir or keystore_fd, not both")
        if keystore_dir is None and keystore_fd is None:
            raise SignerKeystoreError("ERR_MISSING_KEYSTORE_INPUT: keystore directory or descriptor required")

        self._expected_uid = expected_uid
        self._expected_gid = expected_gid
        self._enforce_ownership = enforce_ownership
        self._dir_fd: Optional[int] = None
        self._hmac_secret: Optional[bytes] = None
        self._oracle_private_key: Optional[Ed25519PrivateKey] = None
        self._oracle_public_key: Optional[Ed25519PublicKey] = None
        self._oracle_public_key_bytes: Optional[bytes] = None
        self._oracle_key_id: Optional[str] = None
        self._loaded: bool = False

        try:
            if keystore_fd is not None:
                if isinstance(keystore_fd, bool) or not isinstance(keystore_fd, int) or keystore_fd < 0:
                    raise SignerKeystoreSecurityError("ERR_INVALID_FD: keystore_fd must be a non-negative integer")
                self._dir_fd = os.dup(keystore_fd)
                os.set_inheritable(self._dir_fd, False)
                fcntl.fcntl(self._dir_fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
                self._validate_dir_descriptor(self._dir_fd)
            else:
                self._dir_fd = self._open_and_validate_keystore_dir(keystore_dir)

            self._load_keys()
            self._loaded = True
        except Exception:
            self.close()
            raise

    def _open_and_validate_keystore_dir(self, dir_path: Union[str, Path]) -> int:
        path_str = str(dir_path)
        try:
            fd = os.open(path_str, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as e:
            if e.errno == errno.ELOOP or (e.errno == errno.ENOTDIR and os.path.islink(path_str)):
                raise SignerKeystoreSecurityError(f"ERR_SECURITY_VIOLATION: keystore directory is a symlink: {e}") from e
            raise SignerKeystoreSecurityError(f"ERR_CANNOT_OPEN_KEYSTORE_DIR: failed to open '{path_str}': {e}") from e

        try:
            self._validate_dir_descriptor(fd)
            return fd
        except Exception:
            os.close(fd)
            raise

    def _validate_dir_descriptor(self, fd: int) -> None:
        try:
            st = os.fstat(fd)
        except OSError as e:
            raise SignerKeystoreSecurityError(f"ERR_FSTAT_FAILED: {e}") from e

        if stat.S_ISLNK(st.st_mode):
            raise SignerKeystoreSecurityError("ERR_SECURITY_VIOLATION: keystore directory is a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise SignerKeystoreSecurityError("ERR_SECURITY_VIOLATION: keystore path is not a directory")

        mode = stat.S_IMODE(st.st_mode)
        if mode != 0o700:
            raise SignerKeystoreSecurityError(f"ERR_INVALID_MODE: keystore dir mode is {oct(mode)}, expected strictly 0700")

        if self._enforce_ownership:
            if st.st_uid != self._expected_uid:
                raise SignerKeystoreSecurityError(
                    f"ERR_INVALID_OWNER: keystore dir owned by UID {st.st_uid}, expected {self._expected_uid}"
                )
            if st.st_gid != self._expected_gid:
                raise SignerKeystoreSecurityError(
                    f"ERR_INVALID_GROUP: keystore dir owned by GID {st.st_gid}, expected {self._expected_gid}"
                )

    def _load_key_file(self, filename: str, expected_len: int) -> bytes:
        assert self._dir_fd is not None
        try:
            key_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._dir_fd)
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise SignerKeystoreSecurityError(f"ERR_SECURITY_VIOLATION: key file '{filename}' is a symlink: {e}") from e
            raise SignerKeystoreSecurityError(f"ERR_KEY_FILE_OPEN_FAILED: failed opening '{filename}': {e}") from e

        try:
            st1 = os.fstat(key_fd)
            if stat.S_ISLNK(st1.st_mode):
                raise SignerKeystoreSecurityError(f"ERR_SECURITY_VIOLATION: key file '{filename}' is a symlink")
            if not stat.S_ISREG(st1.st_mode):
                raise SignerKeystoreSecurityError(f"ERR_SECURITY_VIOLATION: key file '{filename}' is not a regular file")
            if st1.st_nlink != 1:
                raise SignerKeystoreSecurityError(f"ERR_SECURITY_VIOLATION: key file '{filename}' has {st1.st_nlink} hard links (single-link policy)")
            mode = stat.S_IMODE(st1.st_mode)
            if mode != 0o600:
                raise SignerKeystoreSecurityError(f"ERR_INVALID_MODE: key file '{filename}' mode is {oct(mode)}, expected strictly 0600")
            if self._enforce_ownership:
                if st1.st_uid != self._expected_uid:
                    raise SignerKeystoreSecurityError(f"ERR_INVALID_OWNER: key file '{filename}' owned by UID {st1.st_uid}, expected {self._expected_uid}")
                if st1.st_gid != self._expected_gid:
                    raise SignerKeystoreSecurityError(f"ERR_INVALID_GROUP: key file '{filename}' owned by GID {st1.st_gid}, expected {self._expected_gid}")
            if st1.st_size != expected_len:
                raise SignerKeystoreCorruptionError(f"ERR_KEY_SIZE_MISMATCH: key file '{filename}' size {st1.st_size} != expected {expected_len}")

            # Bounded read with EOF probe (+1 byte)
            data = bytearray()
            to_read = expected_len + 1
            while len(data) < to_read:
                chunk = os.read(key_fd, to_read - len(data))
                if not chunk:
                    break
                data.extend(chunk)

            if len(data) != expected_len:
                raise SignerKeystoreCorruptionError(
                    f"ERR_KEY_SIZE_MISMATCH: read {len(data)} bytes from '{filename}', expected exactly {expected_len}"
                )

            st2 = os.fstat(key_fd)
            if (
                st2.st_ino != st1.st_ino
                or st2.st_dev != st1.st_dev
                or st2.st_size != st1.st_size
                or st2.st_mtime_ns != st1.st_mtime_ns
            ):
                raise SignerKeystoreSecurityError(f"ERR_CONCURRENT_MUTATION: key file '{filename}' mutated during read")

            return bytes(data)
        finally:
            os.close(key_fd)

    def _load_keys(self) -> None:
        self._hmac_secret = self._load_key_file(HMAC_KEY_FILENAME, RAW_KEY_LENGTH)
        raw_ed25519_bytes = self._load_key_file(ORACLE_KEY_FILENAME, RAW_KEY_LENGTH)

        try:
            self._oracle_private_key = Ed25519PrivateKey.from_private_bytes(raw_ed25519_bytes)
        except Exception as e:
            raise SignerKeystoreCorruptionError(f"ERR_INVALID_ED25519_KEY: cannot load Ed25519 private key: {e}") from e

        self._oracle_public_key = self._oracle_private_key.public_key()
        self._oracle_public_key_bytes = self._oracle_public_key.public_bytes_raw()
        self._oracle_key_id = hashlib.sha256(self._oracle_public_key_bytes).hexdigest()[:16]

    def sign_authorization_hmac(self, claims_bytes: bytes) -> str:
        """Signs RFC 8785 canonical authorization claims bytes with the HMAC secret key.

        Returns 64-hex lowercase HMAC-SHA256 digest string.
        """
        if not self._loaded or self._hmac_secret is None:
            raise SignerKeystoreError("ERR_KEYSTORE_NOT_LOADED: keystore is closed or not initialized")
        if not isinstance(claims_bytes, (bytes, bytearray)):
            raise SignerKeystoreError("claims_bytes must be bytes")
        return hmac.new(self._hmac_secret, claims_bytes, hashlib.sha256).hexdigest().lower()

    def verify_authorization_hmac(self, claims_bytes: bytes, expected_hmac: str) -> bool:
        """Verifies an HMAC signature against canonical claims bytes."""
        actual_hmac = self.sign_authorization_hmac(claims_bytes)
        return hmac.compare_digest(actual_hmac, expected_hmac.lower())

    def sign_oracle_response(self, claims_bytes: bytes) -> str:
        """Signs RFC 8785 canonical Oracle response claims bytes with the Ed25519 private key.

        Returns 128-hex lowercase Ed25519 signature string.
        """
        if not self._loaded or self._oracle_private_key is None:
            raise SignerKeystoreError("ERR_KEYSTORE_NOT_LOADED: keystore is closed or not initialized")
        if not isinstance(claims_bytes, (bytes, bytearray)):
            raise SignerKeystoreError("claims_bytes must be bytes")
        sig_bytes = self._oracle_private_key.sign(claims_bytes)
        return sig_bytes.hex().lower()

    def verify_oracle_signature(self, claims_bytes: bytes, signature_hex: str) -> bool:
        """Verifies Ed25519 signature over canonical claims bytes using loaded Oracle public key."""
        if not self._loaded or self._oracle_public_key is None:
            raise SignerKeystoreError("ERR_KEYSTORE_NOT_LOADED: keystore is closed or not initialized")
        try:
            sig_bytes = bytes.fromhex(signature_hex)
            if len(sig_bytes) != 64:
                return False
            self._oracle_public_key.verify(sig_bytes, claims_bytes)
            return True
        except (ValueError, InvalidSignature):
            return False

    def get_oracle_public_key(self) -> Ed25519PublicKey:
        """Returns the loaded Ed25519 Oracle public key object."""
        if not self._loaded or self._oracle_public_key is None:
            raise SignerKeystoreError("ERR_KEYSTORE_NOT_LOADED: keystore is closed or not initialized")
        return self._oracle_public_key

    def get_oracle_public_key_bytes(self) -> bytes:
        """Returns raw 32-byte Ed25519 Oracle public key."""
        if not self._loaded or self._oracle_public_key_bytes is None:
            raise SignerKeystoreError("ERR_KEYSTORE_NOT_LOADED: keystore is closed or not initialized")
        return self._oracle_public_key_bytes

    def get_oracle_key_id(self) -> str:
        """Returns the 16-hex key ID of the Oracle public key."""
        if not self._loaded or self._oracle_key_id is None:
            raise SignerKeystoreError("ERR_KEYSTORE_NOT_LOADED: keystore is closed or not initialized")
        return self._oracle_key_id

    def close(self) -> None:
        """Closes directory descriptor and wipes key material from instance."""
        if self._dir_fd is not None:
            try:
                os.close(self._dir_fd)
            except OSError:
                pass
            self._dir_fd = None
        self._hmac_secret = None
        self._oracle_private_key = None
        self._oracle_public_key = None
        self._oracle_public_key_bytes = None
        self._loaded = False

    def __enter__(self) -> SignerKeystoreService:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __repr__(self) -> str:
        status = "LOADED" if self._loaded else "CLOSED"
        key_id = self._oracle_key_id or "NONE"
        return f"<SignerKeystoreService status={status} oracle_key_id={key_id} [SECRET KEY MATERIAL REDACTED]>"

    def __str__(self) -> str:
        return self.__repr__()
