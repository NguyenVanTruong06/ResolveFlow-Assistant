"""
tests/services/test_trial_keyring.py
Authoritative Test Suite for Purpose-Separated Keyring Authority (Workorder G07-07B6-I2).

Covers all 5 acceptance groups:
  - I2-G1: Keystore and Key File Security & Descriptor Cleanup
  - I2-G2: Purpose Separation and Key Isolation
  - I2-G3: Exact Operator Descriptor Schema & Controlled Errors
  - I2-G4: Duplicate JSON Member Rejection & Hook Evaluation
  - I2-G5: Key Material Entropy Boundaries & DTO Immutability

Safety & Boundaries:
  - Tests use temporary local directories with strict 0700/0600 modes.
  - Zero Resolve calls, zero IPC, zero FusionScript, zero network, zero AI models.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, Dict, Generator

import pytest

from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    ReadbackReceiptVerificationError,
    WholePlanReconciliationError,
)
from src.services.trial_keyring import (
    ALLOWED_OPKEY_PURPOSES,
    MAX_KEYFILE_SIZE,
    MAX_OPERATOR_KEY_BYTES,
    MIN_KEYFILE_SIZE,
    MIN_OPERATOR_KEY_BYTES,
    REQUIRED_OPKEY_SCHEMA_KEYS,
    DescriptorCleanupFailure,
    OperatorKeyRecord,
    TrialKeyringAuthority,
    reject_opkey_duplicate_json_keys,
)

# Test Fixture Constants
VALID_HEX_32 = "a" * 64
VALID_HEX_64 = "b" * 128
VALID_PURPOSE = "TIMELINE_AMBIGUOUS_RECONCILIATION"
VALID_IDENTITY = "operator_alice_secops"


def make_valid_opkey_data(
    key_hex: str = VALID_HEX_32,
    identity: str = VALID_IDENTITY,
    purpose: str = VALID_PURPOSE,
) -> Dict[str, Any]:
    return {
        "key_hex": key_hex,
        "permitted_operator_identity": identity,
        "purpose": purpose,
    }


@pytest.fixture
def secure_keystore_dir() -> Generator[str, None, None]:
    """Creates a temporary keystore directory with strict 0700 permissions."""
    with tempfile.TemporaryDirectory() as td:
        os.chmod(td, 0o700)
        yield td


def write_secure_file(path: str, content: bytes) -> None:
    """Writes a file and ensures strict 0600 mode."""
    with open(path, "wb") as f:
        f.write(content)
    os.chmod(path, 0o600)


def count_open_fds() -> int:
    """Returns the count of open file descriptors for the current process."""
    return len(os.listdir("/proc/self/fd"))


# ==============================================================================
# Acceptance Group I2-G1: Keystore and Key File Security
# ==============================================================================


class TestI2G1KeystoreSecurity:
    """Verifies physical and filesystem security checks on keystore directory and files."""

    def test_nonexistent_keystore_path_fails(self) -> None:
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority("/nonexistent/keystore/path/12345")
        assert "ERR_KEYSTORE_CANNOT_OPEN" in str(exc_info.value)

    def test_empty_keystore_path_fails(self) -> None:
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority("")
        assert "ERR_KEYSTORE_CANNOT_OPEN" in str(exc_info.value)

    def test_keystore_is_regular_file_fails(self, secure_keystore_dir: str) -> None:
        file_path = os.path.join(secure_keystore_dir, "not_a_dir.key")
        write_secure_file(file_path, b"x" * 32)
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(file_path)
        assert "ERR_KEYSTORE_CANNOT_OPEN" in str(exc_info.value) or "ERR_KEYSTORE_NOT_DIR" in str(exc_info.value)

    def test_keystore_insecure_permissions_fail(self, secure_keystore_dir: str) -> None:
        for insecure_mode in (0o755, 0o777, 0o750, 0o707):
            os.chmod(secure_keystore_dir, insecure_mode)
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)
            assert "ERR_KEYSTORE_INSECURE_PERMS" in str(exc_info.value)
        os.chmod(secure_keystore_dir, 0o700)

    def test_keystore_invalid_owner_fails(self, secure_keystore_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        # Mock os.getuid to return a different UID than current owner
        current_uid = os.getuid()
        monkeypatch.setattr(os, "getuid", lambda: current_uid + 9999)
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_KEYSTORE_INVALID_OWNER" in str(exc_info.value)

    def test_keyfile_insecure_permissions_fail(self, secure_keystore_dir: str) -> None:
        key_path = os.path.join(secure_keystore_dir, "test.key")
        write_secure_file(key_path, b"k" * 32)

        for insecure_mode in (0o644, 0o666, 0o755, 0o700):
            os.chmod(key_path, insecure_mode)
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)
            assert "ERR_KEYFILE_INSECURE_PERMS" in str(exc_info.value)

    def test_keyfile_invalid_owner_fails(self, secure_keystore_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        key_path = os.path.join(secure_keystore_dir, "test.key")
        write_secure_file(key_path, b"k" * 32)

        orig_fstat = os.fstat

        def mock_fstat(fd: int) -> os.stat_result:
            real_st = orig_fstat(fd)
            # If regular file, fake a different UID
            if stat.S_ISREG(real_st.st_mode):
                # Construct fake stat result with different uid
                fake_stat = os.stat_result(
                    (
                        real_st.st_mode,
                        real_st.st_ino,
                        real_st.st_dev,
                        real_st.st_nlink,
                        real_st.st_uid + 1000,  # Wrong owner
                        real_st.st_gid,
                        real_st.st_size,
                        real_st.st_atime,
                        real_st.st_mtime,
                        real_st.st_ctime,
                    )
                )
                return fake_stat
            return real_st

        monkeypatch.setattr(os, "fstat", mock_fstat)
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_KEYFILE_INVALID_OWNER" in str(exc_info.value)

    def test_keyfile_hardlink_detected_fails(self, secure_keystore_dir: str) -> None:
        key_path = os.path.join(secure_keystore_dir, "original.key")
        hardlink_path = os.path.join(secure_keystore_dir, "link.key")
        write_secure_file(key_path, b"h" * 32)
        os.link(key_path, hardlink_path)

        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_KEYFILE_HARDLINK_DETECTED" in str(exc_info.value)

    def test_keyfile_symlink_detected_fails(self, secure_keystore_dir: str) -> None:
        target_path = os.path.join(secure_keystore_dir, "target.key")
        symlink_path = os.path.join(secure_keystore_dir, "symlink.key")
        write_secure_file(target_path, b"s" * 32)
        os.symlink(target_path, symlink_path)

        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_KEYFILE_SYMLINK_DETECTED" in str(exc_info.value) or "ERR_KEYFILE_CANNOT_OPEN" in str(
            exc_info.value
        )

    def test_keystore_dir_symlink_detected_fails(self, secure_keystore_dir: str) -> None:
        parent_dir = str(Path(secure_keystore_dir).parent)
        symlink_dir = os.path.join(parent_dir, "symlink_keystore")
        try:
            os.symlink(secure_keystore_dir, symlink_dir)
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(symlink_dir)
            assert "ERR_KEYSTORE_SYMLINK_DETECTED" in str(exc_info.value) or "ERR_KEYSTORE_CANNOT_OPEN" in str(
                exc_info.value
            )
        finally:
            if os.path.islink(symlink_dir):
                os.unlink(symlink_dir)

    def test_keyfile_size_out_of_bounds_fails(self, secure_keystore_dir: str) -> None:
        # Too small (< 32 bytes)
        small_path = os.path.join(secure_keystore_dir, "small.key")
        write_secure_file(small_path, b"x" * 31)
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_KEYFILE_INVALID_SIZE" in str(exc_info.value)
        os.unlink(small_path)

        # Too large (> 4096 bytes)
        large_path = os.path.join(secure_keystore_dir, "large.key")
        write_secure_file(large_path, b"y" * 4097)
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_KEYFILE_INVALID_SIZE" in str(exc_info.value)

    def test_keyfile_short_read_fails(self, secure_keystore_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        key_path = os.path.join(secure_keystore_dir, "test.key")
        write_secure_file(key_path, b"z" * 32)

        orig_read = os.read

        def mock_read(fd: int, n: int) -> bytes:
            real_bytes = orig_read(fd, n)
            # Shorten the read
            return real_bytes[:10]

        monkeypatch.setattr(os, "read", mock_read)
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_KEYFILE_SHORT_READ" in str(exc_info.value)

    def test_keyfile_unstable_metadata_fails(self, secure_keystore_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
        key_path = os.path.join(secure_keystore_dir, "test.key")
        write_secure_file(key_path, b"u" * 32)

        call_count = 0
        orig_fstat = os.fstat

        def mock_fstat(fd: int) -> os.stat_result:
            nonlocal call_count
            real_st = orig_fstat(fd)
            if stat.S_ISREG(real_st.st_mode):
                call_count += 1
                if call_count >= 2:
                    # Modify size in second fstat
                    return os.stat_result(
                        (
                            real_st.st_mode,
                            real_st.st_ino,
                            real_st.st_dev,
                            real_st.st_nlink,
                            real_st.st_uid,
                            real_st.st_gid,
                            real_st.st_size + 1,
                            real_st.st_atime,
                            real_st.st_mtime,
                            real_st.st_ctime,
                        )
                    )
            return real_st

        monkeypatch.setattr(os, "fstat", mock_fstat)
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_KEYFILE_UNSTABLE" in str(exc_info.value)

    def test_keyfile_special_file_fifo_fails(self, secure_keystore_dir: str) -> None:
        fifo_path = os.path.join(secure_keystore_dir, "named_pipe.key")
        os.mkfifo(fifo_path, 0o600)
        try:
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)
            assert "ERR_KEYFILE_NOT_REGULAR" in str(exc_info.value) or "ERR_KEYFILE_CANNOT_OPEN" in str(exc_info.value)
        finally:
            os.unlink(fifo_path)

    def test_keystore_unknown_file_fails(self, secure_keystore_dir: str) -> None:
        bad_path = os.path.join(secure_keystore_dir, "unrecognized_entry.txt")
        write_secure_file(bad_path, b"x" * 32)
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_KEYSTORE_UNKNOWN_FILE" in str(exc_info.value)

    def test_descriptor_cleanup_on_failure(self, secure_keystore_dir: str) -> None:
        """Verifies that all descriptors (directory and files) are closed on error."""
        # 1. Directory permission failure
        os.chmod(secure_keystore_dir, 0o755)
        before_fds = count_open_fds()
        with pytest.raises(AcceptanceGateError):
            TrialKeyringAuthority(secure_keystore_dir)
        after_fds = count_open_fds()
        assert after_fds <= before_fds, "FD leak detected on directory permission failure"
        os.chmod(secure_keystore_dir, 0o700)

        # 2. File permission failure
        key_path = os.path.join(secure_keystore_dir, "bad_perm.key")
        write_secure_file(key_path, b"x" * 32)
        os.chmod(key_path, 0o644)
        before_fds = count_open_fds()
        with pytest.raises(AcceptanceGateError):
            TrialKeyringAuthority(secure_keystore_dir)
        after_fds = count_open_fds()
        assert after_fds <= before_fds, "FD leak detected on key file permission failure"
        os.unlink(key_path)

        # 3. Invalid JSON failure in .opkey.json
        opkey_path = os.path.join(secure_keystore_dir, "bad_json.opkey.json")
        write_secure_file(opkey_path, b"{" + b" " * 31)
        before_fds = count_open_fds()
        with pytest.raises(AcceptanceGateError):
            TrialKeyringAuthority(secure_keystore_dir)
        after_fds = count_open_fds()
        assert after_fds <= before_fds, "FD leak detected on invalid JSON failure"

    def test_i2_01_primary_file_validation_failure_plus_ffd_close_failure(
        self, secure_keystore_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Finding I2-01: Verifies primary error and ffd close failure are both preserved and inspectable."""
        key_path = os.path.join(secure_keystore_dir, "bad_perm.key")
        write_secure_file(key_path, b"x" * 32)
        os.chmod(key_path, 0o644)

        orig_close = os.close
        mock_active = True
        close_calls = 0

        def mock_close(fd: int) -> None:
            nonlocal close_calls
            orig_close(fd)
            if mock_active:
                close_calls += 1
                # Fail on first close (ffd)
                if close_calls == 1:
                    raise OSError(errno.EIO, "Simulated ffd close failure")

        monkeypatch.setattr(os, "close", mock_close)
        try:
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)

            # 1. Primary error remains observable
            assert "ERR_KEYFILE_INSECURE_PERMS" in str(exc_info.value)

            # 2. Structured cleanup failure attached and inspectable
            assert hasattr(exc_info.value, "cleanup_failures")
            failures = exc_info.value.cleanup_failures
            assert len(failures) == 1
            cf = failures[0]
            assert isinstance(cf, DescriptorCleanupFailure)
            assert cf.descriptor_type == "file_fd"
            assert cf.target_path == "bad_perm.key"
            assert cf.close_outcome == "CLOSE_OUTCOME_UNKNOWN"
            assert "Simulated ffd close failure" in cf.error_message

            # 3. Exception notes attached
            if hasattr(exc_info.value, "__notes__"):
                assert any("CLEANUP_FAILURE" in n and "bad_perm.key" in n for n in exc_info.value.__notes__)
        finally:
            mock_active = False

    def test_i2_01_primary_file_validation_failure_plus_dir_fd_close_failure(
        self, secure_keystore_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Finding I2-01: Verifies primary error and dir_fd close failure are both preserved and inspectable."""
        key_path = os.path.join(secure_keystore_dir, "bad_perm.key")
        write_secure_file(key_path, b"x" * 32)
        os.chmod(key_path, 0o644)

        orig_close = os.close
        mock_active = True
        close_calls = 0

        def mock_close(fd: int) -> None:
            nonlocal close_calls
            orig_close(fd)
            if mock_active:
                close_calls += 1
                # Fail on second close (dir_fd)
                if close_calls == 2:
                    raise OSError(errno.EIO, "Simulated dir_fd close failure")

        monkeypatch.setattr(os, "close", mock_close)
        try:
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)

            # 1. Primary error remains observable
            assert "ERR_KEYFILE_INSECURE_PERMS" in str(exc_info.value)

            # 2. Structured cleanup failure attached and inspectable
            assert hasattr(exc_info.value, "cleanup_failures")
            failures = exc_info.value.cleanup_failures
            assert len(failures) == 1
            cf = failures[0]
            assert isinstance(cf, DescriptorCleanupFailure)
            assert cf.descriptor_type == "dir_fd"
            assert cf.target_path == secure_keystore_dir
            assert cf.close_outcome == "CLOSE_OUTCOME_UNKNOWN"
            assert "Simulated dir_fd close failure" in cf.error_message

            # 3. Exception notes attached
            if hasattr(exc_info.value, "__notes__"):
                assert any("CLEANUP_FAILURE" in n and secure_keystore_dir in n for n in exc_info.value.__notes__)
        finally:
            mock_active = False

    def test_i2_01_primary_failure_plus_both_ffd_and_dir_fd_close_failures(
        self, secure_keystore_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Finding I2-01: Verifies multiple close failures are aggregated in order on the primary error."""
        key_path = os.path.join(secure_keystore_dir, "bad_perm.key")
        write_secure_file(key_path, b"x" * 32)
        os.chmod(key_path, 0o644)

        orig_close = os.close
        mock_active = True
        close_calls = 0

        def mock_close(fd: int) -> None:
            nonlocal close_calls
            orig_close(fd)
            if mock_active:
                close_calls += 1
                # Fail on both closes
                raise OSError(errno.EIO, f"Simulated close failure #{close_calls}")

        monkeypatch.setattr(os, "close", mock_close)
        try:
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)

            assert "ERR_KEYFILE_INSECURE_PERMS" in str(exc_info.value)
            assert hasattr(exc_info.value, "cleanup_failures")
            failures = exc_info.value.cleanup_failures
            assert len(failures) == 2
            assert failures[0].descriptor_type == "file_fd"
            assert failures[0].target_path == "bad_perm.key"
            assert failures[0].close_outcome == "CLOSE_OUTCOME_UNKNOWN"
            assert failures[1].descriptor_type == "dir_fd"
            assert failures[1].target_path == secure_keystore_dir
            assert failures[1].close_outcome == "CLOSE_OUTCOME_UNKNOWN"
        finally:
            mock_active = False

    def test_i2_01_close_failure_without_error_raises_cleanup_error(
        self, secure_keystore_dir: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Finding I2-01: Verifies that if keystore is valid but dir close fails, it raises structured cleanup error."""
        write_secure_file(os.path.join(secure_keystore_dir, "valid.key"), b"x" * 32)

        orig_close = os.close
        mock_active = True
        close_calls = 0

        def mock_close(fd: int) -> None:
            nonlocal close_calls
            orig_close(fd)
            if mock_active:
                close_calls += 1
                # Fail on second close (the dir_fd close)
                if close_calls >= 2:
                    raise OSError(errno.EIO, "Simulated dir close failure")

        monkeypatch.setattr(os, "close", mock_close)
        try:
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)
            assert "ERR_KEYSTORE_CLOSE_FAILED" in str(exc_info.value)
            assert hasattr(exc_info.value, "cleanup_failures")
            assert len(exc_info.value.cleanup_failures) == 1
            cf = exc_info.value.cleanup_failures[0]
            assert cf.descriptor_type == "dir_fd"
            assert cf.close_outcome == "CLOSE_OUTCOME_UNKNOWN"
        finally:
            mock_active = False


# ==============================================================================
# Acceptance Group I2-G2: Purpose Separation and Key Isolation
# ==============================================================================


class TestI2G2PurposeSeparation:
    """Verifies strict isolation between trial signing keys and operator reconciliation credentials."""

    def test_trial_key_resolution_success(self, secure_keystore_dir: str) -> None:
        trial_key_bytes = b"trial_signing_secret_key_32bytes"
        write_secure_file(os.path.join(secure_keystore_dir, "trial_01.key"), trial_key_bytes)

        keyring = TrialKeyringAuthority(secure_keystore_dir)
        assert keyring.get_key("trial_01") == trial_key_bytes
        assert keyring.keystore_path == secure_keystore_dir

    def test_operator_key_resolution_success(self, secure_keystore_dir: str) -> None:
        opkey_data = make_valid_opkey_data(key_hex=VALID_HEX_32)
        write_secure_file(
            os.path.join(secure_keystore_dir, "op_01.opkey.json"),
            json.dumps(opkey_data).encode("utf-8"),
        )

        keyring = TrialKeyringAuthority(secure_keystore_dir)
        record = keyring.get_operator_key("op_01")
        assert record.key_id == "op_01"
        assert record.key_bytes == bytes.fromhex(VALID_HEX_32)
        assert record.permitted_operator_identity == VALID_IDENTITY
        assert record.key_purpose == VALID_PURPOSE

    def test_trial_key_rejected_by_operator_lookup(self, secure_keystore_dir: str) -> None:
        write_secure_file(os.path.join(secure_keystore_dir, "trial_01.key"), b"x" * 32)
        keyring = TrialKeyringAuthority(secure_keystore_dir)

        with pytest.raises(WholePlanReconciliationError) as exc_info:
            keyring.get_operator_key("trial_01")
        assert "ERR_TRIAL_KEY_NOT_PERMITTED_FOR_RECONCILIATION" in str(exc_info.value)

    def test_operator_key_rejected_by_trial_lookup(self, secure_keystore_dir: str) -> None:
        opkey_data = make_valid_opkey_data()
        write_secure_file(
            os.path.join(secure_keystore_dir, "op_01.opkey.json"),
            json.dumps(opkey_data).encode("utf-8"),
        )
        keyring = TrialKeyringAuthority(secure_keystore_dir)

        with pytest.raises(ReadbackReceiptVerificationError) as exc_info:
            keyring.get_key("op_01")
        assert "ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED" in str(exc_info.value)

    def test_unknown_key_id_lookups_fail_closed(self, secure_keystore_dir: str) -> None:
        write_secure_file(os.path.join(secure_keystore_dir, "trial_01.key"), b"x" * 32)
        keyring = TrialKeyringAuthority(secure_keystore_dir)

        # Trial lookup for unknown key
        with pytest.raises(ReadbackReceiptVerificationError) as exc_info:
            keyring.get_key("nonexistent_key")
        assert "ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED" in str(exc_info.value)

        # Operator lookup for unknown key
        with pytest.raises(WholePlanReconciliationError) as exc_info:
            keyring.get_operator_key("nonexistent_key")
        assert "ERR_UNKNOWN_OPERATOR_KEY_ID" in str(exc_info.value)

    def test_invalid_key_id_arguments_fail(self, secure_keystore_dir: str) -> None:
        keyring = TrialKeyringAuthority(secure_keystore_dir)

        for invalid_id in (None, "", "   ", 123):  # type: ignore[arg-type]
            with pytest.raises(ReadbackReceiptVerificationError) as exc_info:
                keyring.get_key(invalid_id)
            assert "ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED" in str(exc_info.value)

            with pytest.raises(AcceptanceGateError) as exc_info2:
                keyring.get_operator_key(invalid_id)
            assert "ERR_INVALID_KEY_ID" in str(exc_info2.value)

            with pytest.raises(AcceptanceGateError) as exc_info3:
                keyring.revoke_key(invalid_id)
            assert "ERR_INVALID_KEY_ID" in str(exc_info3.value)

    def test_revocations_json_static_revocation(self, secure_keystore_dir: str) -> None:
        write_secure_file(os.path.join(secure_keystore_dir, "trial_01.key"), b"x" * 32)
        write_secure_file(
            os.path.join(secure_keystore_dir, "op_01.opkey.json"),
            json.dumps(make_valid_opkey_data()).encode("utf-8"),
        )
        revocations = {
            "revoked_key_ids": ["trial_01", "op_01", "other_key"],
        }
        write_secure_file(
            os.path.join(secure_keystore_dir, "revocations.json"),
            json.dumps(revocations, indent=2).encode("utf-8"),
        )

        keyring = TrialKeyringAuthority(secure_keystore_dir)
        assert keyring.is_revoked("trial_01")
        assert keyring.is_revoked("op_01")
        assert keyring.is_revoked("other_key")
        assert not keyring.is_revoked("unknown_key")

        # Trial lookup fails
        with pytest.raises(ReadbackReceiptVerificationError) as exc_info:
            keyring.get_key("trial_01")
        assert "ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED" in str(exc_info.value)

        # Operator lookup fails
        with pytest.raises(AcceptanceGateError) as exc_info2:
            keyring.get_operator_key("op_01")
        assert "ERR_OPERATOR_KEY_REVOKED" in str(exc_info2.value)

    def test_dynamic_key_revocation(self, secure_keystore_dir: str) -> None:
        write_secure_file(os.path.join(secure_keystore_dir, "trial_01.key"), b"x" * 32)
        write_secure_file(
            os.path.join(secure_keystore_dir, "op_01.opkey.json"),
            json.dumps(make_valid_opkey_data()).encode("utf-8"),
        )
        keyring = TrialKeyringAuthority(secure_keystore_dir)

        # Initially valid
        assert keyring.get_key("trial_01") == b"x" * 32
        assert keyring.get_operator_key("op_01").key_id == "op_01"

        # Revoke trial key dynamically
        keyring.revoke_key("trial_01")
        with pytest.raises(ReadbackReceiptVerificationError) as exc_info:
            keyring.get_key("trial_01")
        assert "ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED" in str(exc_info.value)

        # Revoke operator key dynamically
        keyring.revoke_key("op_01")
        with pytest.raises(AcceptanceGateError) as exc_info2:
            keyring.get_operator_key("op_01")
        assert "ERR_OPERATOR_KEY_REVOKED" in str(exc_info2.value)

    def test_malformed_revocations_json_fails_closed(self, secure_keystore_dir: str) -> None:
        cases = [
            (b"invalid json syntax {", "ERR_REVOCATIONS_INVALID_JSON"),
            (b'["not", "a", "json", "object", "root"]', "ERR_REVOCATIONS_SCHEMA_NOT_OBJECT"),
            (b'{"missing_revoked_key_ids_key": []}', "ERR_REVOCATIONS_SCHEMA_MISSING_FIELD"),
            (b'{"revoked_key_ids": "not_a_list_string"}', "ERR_REVOCATIONS_INVALID_FIELD_TYPE"),
            (b'{"revoked_key_ids": [123, 456]}', "ERR_REVOCATIONS_INVALID_ENTRY"),
            (b'{"revoked_key_ids": [""]}', "ERR_REVOCATIONS_INVALID_ENTRY"),
            (b'{"revoked_key_ids": ["   "]}', "ERR_REVOCATIONS_INVALID_ENTRY"),
        ]
        rev_path = os.path.join(secure_keystore_dir, "revocations.json")

        for payload, expected_err in cases:
            # Pad to at least 32 bytes if necessary
            if len(payload) < 32:
                payload += b" " * (32 - len(payload))
            write_secure_file(rev_path, payload)
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)
            assert expected_err in str(exc_info.value), f"Failed for payload: {payload!r}"

    def test_key_id_collision_detected_and_rejected(self, secure_keystore_dir: str) -> None:
        # Create trial key 'shared_id.key' and operator key 'shared_id.opkey.json'
        write_secure_file(os.path.join(secure_keystore_dir, "shared_id.key"), b"x" * 32)
        write_secure_file(
            os.path.join(secure_keystore_dir, "shared_id.opkey.json"),
            json.dumps(make_valid_opkey_data()).encode("utf-8"),
        )
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_KEY_ID_COLLISION" in str(exc_info.value)


# ==============================================================================
# Acceptance Group I2-G3: Exact Operator Descriptor Schema
# ==============================================================================


class TestI2G3OperatorDescriptorSchema:
    """Verifies strict schema enforcement on *.opkey.json descriptors."""

    def test_missing_required_fields_fails(self, secure_keystore_dir: str) -> None:
        for missing_key in REQUIRED_OPKEY_SCHEMA_KEYS:
            data = make_valid_opkey_data()
            del data[missing_key]
            raw_bytes = json.dumps(data).encode("utf-8")
            if len(raw_bytes) < 32:
                raw_bytes += b" " * (32 - len(raw_bytes))

            p = os.path.join(secure_keystore_dir, "op_bad.opkey.json")
            write_secure_file(p, raw_bytes)
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)
            assert "ERR_OPKEY_SCHEMA_MISSING_FIELD" in str(exc_info.value)
            os.unlink(p)

    def test_unknown_extra_fields_fail(self, secure_keystore_dir: str) -> None:
        data = make_valid_opkey_data()
        data["extra_unauthorized_field"] = "bad"
        p = os.path.join(secure_keystore_dir, "op_extra.opkey.json")
        write_secure_file(p, json.dumps(data).encode("utf-8"))

        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_SCHEMA_UNKNOWN_FIELD" in str(exc_info.value)

    def test_invalid_field_types_fail(self, secure_keystore_dir: str) -> None:
        bad_types = [
            ("key_hex", 123456789),
            ("key_hex", ["not", "a", "string"]),
            ("permitted_operator_identity", 999),
            ("permitted_operator_identity", {"id": "alice"}),
            ("purpose", 0),
            ("purpose", ["TIMELINE_AMBIGUOUS_RECONCILIATION"]),
        ]
        p = os.path.join(secure_keystore_dir, "op_type.opkey.json")

        for key, bad_val in bad_types:
            data = make_valid_opkey_data()
            data[key] = bad_val
            write_secure_file(p, json.dumps(data).encode("utf-8"))
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)
            assert "ERR_OPKEY_INVALID_FIELD_TYPE" in str(exc_info.value)
            os.unlink(p)

    def test_empty_operator_identity_fails(self, secure_keystore_dir: str) -> None:
        for empty_val in ("", "   ", "\t\n"):
            data = make_valid_opkey_data(identity=empty_val)
            p = os.path.join(secure_keystore_dir, "op_empty_id.opkey.json")
            write_secure_file(p, json.dumps(data).encode("utf-8"))
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)
            assert "ERR_OPKEY_EMPTY_OPERATOR_IDENTITY" in str(exc_info.value)
            os.unlink(p)

    def test_unauthorized_purpose_fails(self, secure_keystore_dir: str) -> None:
        for unauthorized in ("TRIAL_SIGNING", "ADMIN_GLOBAL", "ARBITRARY_MUTATION", "read_only"):
            data = make_valid_opkey_data(purpose=unauthorized)
            p = os.path.join(secure_keystore_dir, "op_bad_purpose.opkey.json")
            write_secure_file(p, json.dumps(data).encode("utf-8"))
            with pytest.raises(AcceptanceGateError) as exc_info:
                TrialKeyringAuthority(secure_keystore_dir)
            assert "ERR_OPKEY_UNAUTHORIZED_PURPOSE" in str(exc_info.value)
            os.unlink(p)

    def test_root_not_object_fails(self, secure_keystore_dir: str) -> None:
        p = os.path.join(secure_keystore_dir, "op_array.opkey.json")
        write_secure_file(p, b'["an", "array", "not", "an", "object", "schema"]')
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_SCHEMA_NOT_OBJECT" in str(exc_info.value)


# ==============================================================================
# Acceptance Group I2-G4: Duplicate JSON Member Rejection
# ==============================================================================


class TestI2G4DuplicateJsonMembers:
    """Verifies that duplicate member names in operator key descriptors are strictly rejected."""

    def test_duplicate_purpose_conflicting_fails(self, secure_keystore_dir: str) -> None:
        json_str = (
            '{\n'
            '  "key_hex": "' + VALID_HEX_32 + '",\n'
            '  "permitted_operator_identity": "' + VALID_IDENTITY + '",\n'
            '  "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION",\n'
            '  "purpose": "UNAUTHORIZED_PURPOSE"\n'
            '}\n'
        )
        p = os.path.join(secure_keystore_dir, "dup_purpose.opkey.json")
        write_secure_file(p, json_str.encode("utf-8"))
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_DUPLICATE_FIELD" in str(exc_info.value)

    def test_duplicate_purpose_identical_fails(self, secure_keystore_dir: str) -> None:
        json_str = (
            '{\n'
            '  "key_hex": "' + VALID_HEX_32 + '",\n'
            '  "permitted_operator_identity": "' + VALID_IDENTITY + '",\n'
            '  "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION",\n'
            '  "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION"\n'
            '}\n'
        )
        p = os.path.join(secure_keystore_dir, "dup_purpose_ident.opkey.json")
        write_secure_file(p, json_str.encode("utf-8"))
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_DUPLICATE_FIELD" in str(exc_info.value)

    def test_duplicate_key_hex_fails(self, secure_keystore_dir: str) -> None:
        json_str = (
            '{\n'
            '  "key_hex": "' + VALID_HEX_32 + '",\n'
            '  "key_hex": "' + VALID_HEX_64 + '",\n'
            '  "permitted_operator_identity": "' + VALID_IDENTITY + '",\n'
            '  "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION"\n'
            '}\n'
        )
        p = os.path.join(secure_keystore_dir, "dup_hex.opkey.json")
        write_secure_file(p, json_str.encode("utf-8"))
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_DUPLICATE_FIELD" in str(exc_info.value)

    def test_duplicate_key_hex_identical_fails(self, secure_keystore_dir: str) -> None:
        json_str = (
            '{\n'
            '  "key_hex": "' + VALID_HEX_32 + '",\n'
            '  "key_hex": "' + VALID_HEX_32 + '",\n'
            '  "permitted_operator_identity": "' + VALID_IDENTITY + '",\n'
            '  "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION"\n'
            '}\n'
        )
        p = os.path.join(secure_keystore_dir, "dup_hex_ident.opkey.json")
        write_secure_file(p, json_str.encode("utf-8"))
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_DUPLICATE_FIELD" in str(exc_info.value)

    def test_duplicate_identity_fails(self, secure_keystore_dir: str) -> None:
        json_str = (
            '{\n'
            '  "key_hex": "' + VALID_HEX_32 + '",\n'
            '  "permitted_operator_identity": "alice",\n'
            '  "permitted_operator_identity": "bob",\n'
            '  "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION"\n'
            '}\n'
        )
        p = os.path.join(secure_keystore_dir, "dup_id.opkey.json")
        write_secure_file(p, json_str.encode("utf-8"))
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_DUPLICATE_FIELD" in str(exc_info.value)

    def test_duplicate_identity_identical_fails(self, secure_keystore_dir: str) -> None:
        json_str = (
            '{\n'
            '  "key_hex": "' + VALID_HEX_32 + '",\n'
            '  "permitted_operator_identity": "' + VALID_IDENTITY + '",\n'
            '  "permitted_operator_identity": "' + VALID_IDENTITY + '",\n'
            '  "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION"\n'
            '}\n'
        )
        p = os.path.join(secure_keystore_dir, "dup_id_ident.opkey.json")
        write_secure_file(p, json_str.encode("utf-8"))
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_DUPLICATE_FIELD" in str(exc_info.value)

    def test_bad_descriptor_with_duplicate_not_loaded(self, secure_keystore_dir: str) -> None:
        # Keystore with 1 valid trial key, and 1 bad operator key with duplicate field
        write_secure_file(os.path.join(secure_keystore_dir, "trial_good.key"), b"x" * 32)
        json_str = (
            '{\n'
            '  "key_hex": "' + VALID_HEX_32 + '",\n'
            '  "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION",\n'
            '  "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION",\n'
            '  "permitted_operator_identity": "' + VALID_IDENTITY + '"\n'
            '}\n'
        )
        write_secure_file(os.path.join(secure_keystore_dir, "op_bad.opkey.json"), json_str.encode("utf-8"))
        # Entire keystore loading must fail closed; bad descriptor is never loaded
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_DUPLICATE_FIELD" in str(exc_info.value)

    def test_direct_hook_invocation(self) -> None:
        # Happy path
        pairs = [("a", 1), ("b", 2), ("c", 3)]
        assert reject_opkey_duplicate_json_keys(pairs) == {"a": 1, "b": 2, "c": 3}

        # Duplicate key
        dup_pairs = [("a", 1), ("b", 2), ("a", 99)]
        with pytest.raises(AcceptanceGateError) as exc_info:
            reject_opkey_duplicate_json_keys(dup_pairs)
        assert "ERR_OPKEY_DUPLICATE_FIELD" in str(exc_info.value)


# ==============================================================================
# Acceptance Group I2-G5: Key Material Entropy Boundaries & DTO Immutability
# ==============================================================================


class TestI2G5KeyMaterialBoundaries:
    """Verifies cryptographic entropy limits (32..64 bytes) and OperatorKeyRecord immutability."""

    def test_odd_length_hex_fails(self, secure_keystore_dir: str) -> None:
        odd_hex = "a" * 63
        data = make_valid_opkey_data(key_hex=odd_hex)
        p = os.path.join(secure_keystore_dir, "odd_hex.opkey.json")
        write_secure_file(p, json.dumps(data).encode("utf-8"))
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_MALFORMED_HEX" in str(exc_info.value)

    def test_non_hex_characters_fail(self, secure_keystore_dir: str) -> None:
        non_hex = ("a" * 60) + "zzzz"
        data = make_valid_opkey_data(key_hex=non_hex)
        p = os.path.join(secure_keystore_dir, "non_hex.opkey.json")
        write_secure_file(p, json.dumps(data).encode("utf-8"))
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_MALFORMED_HEX" in str(exc_info.value)

    def test_entropy_31_bytes_fails(self, secure_keystore_dir: str) -> None:
        hex_31 = "c" * 62  # 31 bytes
        data = make_valid_opkey_data(key_hex=hex_31)
        p = os.path.join(secure_keystore_dir, "hex_31.opkey.json")
        write_secure_file(p, json.dumps(data).encode("utf-8"))
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_ENTROPY_INSUFFICIENT" in str(exc_info.value)

    def test_entropy_32_bytes_succeeds(self, secure_keystore_dir: str) -> None:
        hex_32 = "c" * 64  # 32 bytes
        data = make_valid_opkey_data(key_hex=hex_32)
        p = os.path.join(secure_keystore_dir, "hex_32.opkey.json")
        write_secure_file(p, json.dumps(data).encode("utf-8"))

        keyring = TrialKeyringAuthority(secure_keystore_dir)
        rec = keyring.get_operator_key("hex_32")
        assert len(rec.key_bytes) == 32

    def test_entropy_64_bytes_succeeds(self, secure_keystore_dir: str) -> None:
        hex_64 = "d" * 128  # 64 bytes
        data = make_valid_opkey_data(key_hex=hex_64)
        p = os.path.join(secure_keystore_dir, "hex_64.opkey.json")
        write_secure_file(p, json.dumps(data).encode("utf-8"))

        keyring = TrialKeyringAuthority(secure_keystore_dir)
        rec = keyring.get_operator_key("hex_64")
        assert len(rec.key_bytes) == 64

    def test_entropy_65_bytes_fails(self, secure_keystore_dir: str) -> None:
        hex_65 = "e" * 130  # 65 bytes
        data = make_valid_opkey_data(key_hex=hex_65)
        p = os.path.join(secure_keystore_dir, "hex_65.opkey.json")
        write_secure_file(p, json.dumps(data).encode("utf-8"))
        with pytest.raises(AcceptanceGateError) as exc_info:
            TrialKeyringAuthority(secure_keystore_dir)
        assert "ERR_OPKEY_ENTROPY_EXCEEDS_MAX" in str(exc_info.value)

    def test_operator_key_record_immutability(self) -> None:
        record = OperatorKeyRecord(
            key_id="op_rec_01",
            key_bytes=b"k" * 32,
            permitted_operator_identity="secops_alice",
            key_purpose=VALID_PURPOSE,
        )
        with pytest.raises(FrozenInstanceError):
            record.key_id = "modified"  # type: ignore[misc]

        with pytest.raises(FrozenInstanceError):
            record.key_bytes = b"modified"  # type: ignore[misc]

        with pytest.raises(FrozenInstanceError):
            record.permitted_operator_identity = "modified"  # type: ignore[misc]

        with pytest.raises(FrozenInstanceError):
            record.key_purpose = "modified"  # type: ignore[misc]

    def test_operator_key_record_validation(self) -> None:
        # Invalid key_id
        with pytest.raises(AcceptanceGateError) as exc_info:
            OperatorKeyRecord("", b"k" * 32, VALID_IDENTITY, VALID_PURPOSE)
        assert "ERR_INVALID_KEY_ID" in str(exc_info.value)

        # Invalid key_bytes type
        with pytest.raises(AcceptanceGateError) as exc_info2:
            OperatorKeyRecord("op1", "not_bytes", VALID_IDENTITY, VALID_PURPOSE)  # type: ignore[arg-type]
        assert "ERR_OPKEY_INVALID_FIELD_TYPE" in str(exc_info2.value)

        # Insufficient entropy
        with pytest.raises(AcceptanceGateError) as exc_info3:
            OperatorKeyRecord("op1", b"k" * 31, VALID_IDENTITY, VALID_PURPOSE)
        assert "ERR_OPKEY_ENTROPY_INSUFFICIENT" in str(exc_info3.value)

        # Excess entropy
        with pytest.raises(AcceptanceGateError) as exc_info4:
            OperatorKeyRecord("op1", b"k" * 65, VALID_IDENTITY, VALID_PURPOSE)
        assert "ERR_OPKEY_ENTROPY_EXCEEDS_MAX" in str(exc_info4.value)

        # Empty identity
        with pytest.raises(AcceptanceGateError) as exc_info5:
            OperatorKeyRecord("op1", b"k" * 32, "   ", VALID_PURPOSE)
        assert "ERR_OPKEY_EMPTY_OPERATOR_IDENTITY" in str(exc_info5.value)

        # Unauthorized purpose
        with pytest.raises(AcceptanceGateError) as exc_info6:
            OperatorKeyRecord("op1", b"k" * 32, VALID_IDENTITY, "UNAUTHORIZED_PURPOSE")
        assert "ERR_OPKEY_UNAUTHORIZED_PURPOSE" in str(exc_info6.value)
