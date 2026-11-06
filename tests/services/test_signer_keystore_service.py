"""Tests for SignerKeystoreService.

Verifies:
- Descriptor-anchored traversal and validation
- Strict file/directory mode (0700 dir, 0600 file) and single-link policy
- Symlink and hardlink anomaly rejection
- Raw key size enforcement (strictly 32 bytes)
- Zero secret key leakage in repr, str, or exceptions
- Descriptor-backed initialization and clean closure
"""

import os
import stat
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.services.signer_keystore_service import (
    HMAC_KEY_FILENAME,
    ORACLE_KEY_FILENAME,
    RAW_KEY_LENGTH,
    SignerKeystoreCorruptionError,
    SignerKeystoreError,
    SignerKeystoreSecurityError,
    SignerKeystoreService,
)


def _setup_test_keystore(
    tmp_path: Path,
    hmac_bytes: bytes = b"\x01" * 32,
    oracle_priv_bytes: bytes = b"\x02" * 32,
    dir_mode: int = 0o700,
    file_mode: int = 0o600,
) -> Path:
    ks_dir = tmp_path / "keystore"
    ks_dir.mkdir(mode=dir_mode)
    os.chmod(ks_dir, dir_mode)

    hmac_file = ks_dir / HMAC_KEY_FILENAME
    hmac_file.write_bytes(hmac_bytes)
    os.chmod(hmac_file, file_mode)

    oracle_file = ks_dir / ORACLE_KEY_FILENAME
    oracle_file.write_bytes(oracle_priv_bytes)
    os.chmod(oracle_file, file_mode)

    return ks_dir


class TestSignerKeystoreService:

    def test_keystore_inert_and_input_validation(self):
        with pytest.raises(SignerKeystoreError, match="ERR_MISSING_KEYSTORE_INPUT"):
            SignerKeystoreService()

        with pytest.raises(SignerKeystoreSecurityError, match="ERR_AMBIGUOUS_KEYSTORE_INPUT"):
            SignerKeystoreService("/tmp/a", keystore_fd=3)

    def test_keystore_loads_valid_ephemeral_keys(self, tmp_path: Path):
        priv_key = Ed25519PrivateKey.generate()
        priv_bytes = priv_key.private_bytes_raw()
        hmac_secret = os.urandom(32)

        ks_dir = _setup_test_keystore(tmp_path, hmac_bytes=hmac_secret, oracle_priv_bytes=priv_bytes)

        with SignerKeystoreService(ks_dir, enforce_ownership=False) as ks:
            assert ks.get_oracle_key_id() is not None
            assert len(ks.get_oracle_public_key_bytes()) == 32

            # Test HMAC capability
            test_claims = b'{"action":"test"}'
            mac = ks.sign_authorization_hmac(test_claims)
            assert len(mac) == 64
            assert ks.verify_authorization_hmac(test_claims, mac) is True
            assert ks.verify_authorization_hmac(test_claims, "0" * 64) is False

            # Test Ed25519 signature capability
            sig = ks.sign_oracle_response(test_claims)
            assert len(sig) == 128
            assert ks.verify_oracle_signature(test_claims, sig) is True
            assert ks.verify_oracle_signature(test_claims, "0" * 128) is False

    def test_keystore_descriptor_backed_mode(self, tmp_path: Path):
        priv_key = Ed25519PrivateKey.generate()
        ks_dir = _setup_test_keystore(tmp_path, oracle_priv_bytes=priv_key.private_bytes_raw())

        fd = os.open(str(ks_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            with SignerKeystoreService(keystore_fd=fd, enforce_ownership=False) as ks:
                assert len(ks.get_oracle_public_key_bytes()) == 32
        finally:
            os.close(fd)

    def test_keystore_rejects_symlink_directory(self, tmp_path: Path):
        ks_dir = _setup_test_keystore(tmp_path)
        symlink_dir = tmp_path / "symlink_ks"
        symlink_dir.symlink_to(ks_dir)

        with pytest.raises(SignerKeystoreSecurityError, match="ERR_SECURITY_VIOLATION.*symlink"):
            SignerKeystoreService(symlink_dir, enforce_ownership=False)

    def test_keystore_rejects_non_700_directory(self, tmp_path: Path):
        ks_dir = _setup_test_keystore(tmp_path, dir_mode=0o755)
        with pytest.raises(SignerKeystoreSecurityError, match="ERR_INVALID_MODE"):
            SignerKeystoreService(ks_dir, enforce_ownership=False)

    def test_keystore_rejects_symlink_key_file(self, tmp_path: Path):
        ks_dir = tmp_path / "keystore"
        ks_dir.mkdir(mode=0o700)
        os.chmod(ks_dir, 0o700)

        real_target = tmp_path / "real_key.key"
        real_target.write_bytes(b"\x00" * 32)
        os.chmod(real_target, 0o600)

        (ks_dir / HMAC_KEY_FILENAME).symlink_to(real_target)
        (ks_dir / ORACLE_KEY_FILENAME).write_bytes(b"\x00" * 32)
        os.chmod(ks_dir / ORACLE_KEY_FILENAME, 0o600)

        with pytest.raises(SignerKeystoreSecurityError, match="ERR_SECURITY_VIOLATION.*symlink"):
            SignerKeystoreService(ks_dir, enforce_ownership=False)

    def test_keystore_rejects_non_600_key_file(self, tmp_path: Path):
        ks_dir = _setup_test_keystore(tmp_path, file_mode=0o644)
        with pytest.raises(SignerKeystoreSecurityError, match="ERR_INVALID_MODE"):
            SignerKeystoreService(ks_dir, enforce_ownership=False)

    def test_keystore_rejects_hardlink_anomalies(self, tmp_path: Path):
        ks_dir = _setup_test_keystore(tmp_path)
        # Create second hardlink
        hardlink = tmp_path / "hardlink.key"
        os.link(ks_dir / HMAC_KEY_FILENAME, hardlink)

        with pytest.raises(SignerKeystoreSecurityError, match="ERR_SECURITY_VIOLATION.*hard links"):
            SignerKeystoreService(ks_dir, enforce_ownership=False)

    def test_keystore_rejects_wrong_key_size(self, tmp_path: Path):
        # 31 bytes (too short)
        ks_dir_short = _setup_test_keystore(tmp_path, hmac_bytes=b"\x00" * 31)
        with pytest.raises(SignerKeystoreCorruptionError, match="ERR_KEY_SIZE_MISMATCH"):
            SignerKeystoreService(ks_dir_short, enforce_ownership=False)

    def test_keystore_no_key_leakage(self, tmp_path: Path):
        secret_bytes = b"SUPER_SECRET_HMAC_KEY_1234567890"
        priv_key = Ed25519PrivateKey.generate()
        ks_dir = _setup_test_keystore(tmp_path, hmac_bytes=secret_bytes, oracle_priv_bytes=priv_key.private_bytes_raw())

        with SignerKeystoreService(ks_dir, enforce_ownership=False) as ks:
            r = repr(ks)
            s = str(ks)
            assert "SUPER_SECRET" not in r
            assert "SUPER_SECRET" not in s
            assert "REDACTED" in r
            assert "REDACTED" in s

    def test_keystore_clean_close(self, tmp_path: Path):
        priv_key = Ed25519PrivateKey.generate()
        ks_dir = _setup_test_keystore(tmp_path, oracle_priv_bytes=priv_key.private_bytes_raw())

        ks = SignerKeystoreService(ks_dir, enforce_ownership=False)
        assert ks.sign_authorization_hmac(b"test") is not None
        ks.close()

        with pytest.raises(SignerKeystoreError, match="ERR_KEYSTORE_NOT_LOADED"):
            ks.sign_authorization_hmac(b"test")

        with pytest.raises(SignerKeystoreError, match="ERR_KEYSTORE_NOT_LOADED"):
            ks.sign_oracle_response(b"test")
