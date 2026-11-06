"""Focused unit tests for AuthoritativeApprovalStore (G07-07B7-I2).

Verifies:
- Round-trip equality and byte-for-byte preservation of complete signed envelopes
- Strict rejection of non-exact DTO types (mappings, lookalikes, subclasses)
- Strict rejection of malformed approval IDs
- Duplicate approval ID publication and conflicting content rejection (immutable storage)
- Directory and record file symlink rejection
- Wrong file type, insecure directory and file permissions rejection
- Bounded read size enforcement (> 64 KiB rejected)
- Malformed JSON, duplicate JSON keys, schema mismatch, and tampered record rejection
- Concurrent same-ID publication (exactly 1 succeeds, no overwrite or corruption)
- Injected failure handling (EINTR retry, EIO on write, fsync, link, dir fsync, and close)
- Safe context manager lifecycle and descriptor cleanup
"""

from __future__ import annotations

import concurrent.futures
import errno
import fcntl
import os
import stat
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

from src.core.contracts.edit_plan_approval_contracts import AppOwnedApprovalRecord
from src.core.contracts.signed_approval_envelope import SignedApprovalEnvelope
from src.services.authoritative_approval_store import (
    CLOSE_OUTCOME_UNKNOWN,
    MAX_RECORD_BYTES,
    ApprovalRecordAlreadyExistsError,
    ApprovalStoreCorruptionError,
    ApprovalStoreDurabilityError,
    ApprovalStoreError,
    ApprovalStoreInitializationError,
    ApprovalStoreSecurityError,
    AuthoritativeApprovalStore,
)


def make_valid_approval_record(approval_id: str = "appr_valid_001") -> AppOwnedApprovalRecord:
    """Creates a valid sample AppOwnedApprovalRecord."""
    return AppOwnedApprovalRecord(
        approval_id=approval_id,
        proposal_id="prop_01j7b9c4d2e1f8a3b5c7d9e0f1",
        proposal_revision="a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
        approved_at_iso="2026-11-03T15:00:00Z",
        accepted_omission_ids=("omit_1", "omit_2"),
        approval_scope="ENTIRE_PROPOSAL",
    )


def make_valid_signed_approval_envelope(
    approval_id: str = "appr_valid_001", **overrides: Any
) -> SignedApprovalEnvelope:
    """Helper creating a standard valid SignedApprovalEnvelope."""
    kwargs = {
        "schema_version": 1,
        "approval_record": make_valid_approval_record(approval_id),
        "plan_digest_sha256": "1111111122222222333333334444444455555555666666667777777788888888",
        "proposal_content_digest_sha256": "2222222233333333444444445555555566666666777777778888888899999999",
        "director_key_id": "dir_key_2026_primary",
        "director_signature": "e" * 128,
        "signed_at_iso": "2026-11-03T15:00:05Z",
    }
    kwargs.update(overrides)
    return SignedApprovalEnvelope(**kwargs)


class TestAuthoritativeApprovalStore:
    """Test suite for AuthoritativeApprovalStore."""

    def test_round_trip_equality_and_byte_preservation(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            env = make_valid_signed_approval_envelope("appr_round_trip_01")
            store.put(env)

            retrieved = store.get("appr_round_trip_01")
            assert retrieved is not None
            assert retrieved == env
            assert retrieved.canonical_envelope_bytes() == env.canonical_envelope_bytes()
            assert retrieved.compute_envelope_digest() == env.compute_envelope_digest()

    def test_get_non_existent_returns_none(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            assert store.get("non_existent_approval_id") is None

    def test_reject_non_exact_dto_types(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            # Rejects None
            with pytest.raises(TypeError, match="ERR_INVALID_TYPE"):
                store.put(None)  # type: ignore

            # Rejects dict mapping
            env = make_valid_signed_approval_envelope("appr_mapping")
            with pytest.raises(TypeError, match="ERR_INVALID_TYPE"):
                store.put(env.to_dict())  # type: ignore

            # Rejects subclass
            class SubclassEnvelope(SignedApprovalEnvelope):
                pass

            sub_env = SubclassEnvelope(
                schema_version=env.schema_version,
                approval_record=env.approval_record,
                plan_digest_sha256=env.plan_digest_sha256,
                proposal_content_digest_sha256=env.proposal_content_digest_sha256,
                director_key_id=env.director_key_id,
                director_signature=env.director_signature,
                signed_at_iso=env.signed_at_iso,
            )
            with pytest.raises(TypeError, match="ERR_INVALID_TYPE"):
                store.put(sub_env)

    def test_reject_malformed_approval_ids(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            # Empty string
            with pytest.raises(ApprovalStoreError, match="ERR_INVALID_APPROVAL_ID"):
                store.get("")

            # Whitespace only
            with pytest.raises(ApprovalStoreError, match="ERR_INVALID_APPROVAL_ID"):
                store.get("   ")

            # Whitespace padded
            with pytest.raises(ApprovalStoreError, match="ERR_INVALID_APPROVAL_ID"):
                store.get("  appr_padded  ")

            # Non-string
            with pytest.raises(ApprovalStoreError, match="ERR_INVALID_APPROVAL_ID"):
                store.get(12345)  # type: ignore

    def test_duplicate_publication_and_conflicting_content_immutable(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            env1 = make_valid_signed_approval_envelope("appr_immutable_01")
            store.put(env1)

            # Duplicate same content
            with pytest.raises(ApprovalRecordAlreadyExistsError, match="ERR_APPROVAL_RECORD_ALREADY_EXISTS"):
                store.put(env1)

            # Conflicting content with same approval_id
            env1_conflict = make_valid_signed_approval_envelope(
                "appr_immutable_01",
                plan_digest_sha256="9" * 64,
            )
            with pytest.raises(ApprovalRecordAlreadyExistsError, match="ERR_APPROVAL_RECORD_ALREADY_EXISTS"):
                store.put(env1_conflict)

            # Verify existing record is 100% untouched
            stored = store.get("appr_immutable_01")
            assert stored == env1
            assert stored.plan_digest_sha256 == env1.plan_digest_sha256

    def test_symlink_directory_rejected(self, tmp_path):
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir(mode=0o700)
        symlink_dir = tmp_path / "symlink_dir"
        os.symlink(real_dir, symlink_dir)

        with pytest.raises(ApprovalStoreSecurityError, match="ERR_DIR_SYMLINK_DETECTED"):
            AuthoritativeApprovalStore(str(symlink_dir))

    def test_insecure_directory_permissions_rejected(self, tmp_path):
        insecure_dir = tmp_path / "insecure_store"
        insecure_dir.mkdir(mode=0o777)
        os.chmod(str(insecure_dir), 0o777)

        with pytest.raises(ApprovalStoreSecurityError, match="ERR_STORE_INSECURE_PERMS"):
            AuthoritativeApprovalStore(str(insecure_dir))

        # Group-writable 0o770 rejected
        os.chmod(str(insecure_dir), 0o770)
        with pytest.raises(ApprovalStoreSecurityError, match="ERR_STORE_INSECURE_PERMS"):
            AuthoritativeApprovalStore(str(insecure_dir))

    def test_record_file_symlink_rejected(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            env = make_valid_signed_approval_envelope("appr_symlink_target")
            fname = store._record_filename("appr_symlink_target")

            # Create decoy target and symlink
            decoy_file = tmp_path / "decoy.json"
            decoy_file.write_bytes(env.canonical_envelope_bytes())
            os.chmod(str(decoy_file), 0o600)

            os.symlink(str(decoy_file), os.path.join(store_dir, fname))

            # get() must detect symlink and reject
            with pytest.raises(ApprovalStoreSecurityError, match="ERR_SYMLINK_DETECTED"):
                store.get("appr_symlink_target")

    def test_record_file_insecure_permissions_rejected(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            env = make_valid_signed_approval_envelope("appr_perms_test")
            store.put(env)

            fname = store._record_filename("appr_perms_test")
            fpath = os.path.join(store_dir, fname)

            # Change permissions to world-readable
            os.chmod(fpath, 0o666)

            with pytest.raises(ApprovalStoreSecurityError, match="ERR_FILE_INSECURE_PERMS"):
                store.get("appr_perms_test")

    def test_record_file_not_regular_rejected(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            fname = store._record_filename("appr_dir_test")
            # Create a directory where the file should be
            os.mkdir(os.path.join(store_dir, fname), mode=0o700)

            with pytest.raises(ApprovalStoreSecurityError, match="ERR_NOT_REGULAR_FILE"):
                store.get("appr_dir_test")

    def test_bounded_read_file_too_large_rejected(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            fname = store._record_filename("appr_oversized")
            fpath = os.path.join(store_dir, fname)

            # Write file larger than MAX_RECORD_BYTES
            with open(fpath, "wb") as f:
                f.write(b"a" * (MAX_RECORD_BYTES + 10))
            os.chmod(fpath, 0o600)

            with pytest.raises(ApprovalStoreCorruptionError, match="ERR_FILE_TOO_LARGE"):
                store.get("appr_oversized")

    def test_malformed_json_rejected(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            fname = store._record_filename("appr_malformed")
            fpath = os.path.join(store_dir, fname)

            with open(fpath, "wb") as f:
                f.write(b'{"invalid_json": ')
            os.chmod(fpath, 0o600)

            with pytest.raises(ApprovalStoreCorruptionError, match="ERR_PARSE_FAILED"):
                store.get("appr_malformed")

    def test_duplicate_json_keys_rejected(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            fname = store._record_filename("appr_dup_keys")
            fpath = os.path.join(store_dir, fname)

            # Write JSON with duplicate key
            dup_json = b'{"schema_version":1,"schema_version":1,"approval_record":{}}'
            with open(fpath, "wb") as f:
                f.write(dup_json)
            os.chmod(fpath, 0o600)

            with pytest.raises(ApprovalStoreCorruptionError, match="ERR_PARSE_FAILED"):
                store.get("appr_dup_keys")

    def test_schema_mismatch_rejected(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            fname = store._record_filename("appr_bad_schema")
            fpath = os.path.join(store_dir, fname)

            # Valid JSON but missing required fields
            with open(fpath, "wb") as f:
                f.write(b'{"schema_version":1,"unexpected_field":"value"}')
            os.chmod(fpath, 0o600)

            with pytest.raises(ApprovalStoreCorruptionError, match="ERR_SCHEMA_FAILED"):
                store.get("appr_bad_schema")

    def test_tampered_approval_id_or_bytes_rejected(self, tmp_path):
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            env = make_valid_signed_approval_envelope("appr_target_a")
            # Write under filename for target_b
            fname_b = store._record_filename("appr_target_b")
            fpath_b = os.path.join(store_dir, fname_b)

            with open(fpath_b, "wb") as f:
                f.write(env.canonical_envelope_bytes())
            os.chmod(fpath_b, 0o600)

            # Stored id 'appr_target_a' != queried 'appr_target_b'
            with pytest.raises(ApprovalStoreCorruptionError, match="ERR_APPROVAL_ID_MISMATCH"):
                store.get("appr_target_b")

    def test_concurrent_same_id_publication(self, tmp_path):
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_concurrent_01")

        def worker():
            with AuthoritativeApprovalStore(store_dir) as s:
                s.put(env)

        num_threads = 10
        successes = 0
        already_exists_errors = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker) for _ in range(num_threads)]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    fut.result()
                    successes += 1
                except ApprovalRecordAlreadyExistsError:
                    already_exists_errors += 1

        assert successes == 1
        assert already_exists_errors == num_threads - 1

        with AuthoritativeApprovalStore(store_dir) as store:
            stored = store.get("appr_concurrent_01")
            assert stored == env

    def test_injected_eintr_retries_and_succeeds(self, tmp_path):
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_eintr_test")

        real_write = os.write
        interrupted = False

        def mock_write(fd, data):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise OSError(errno.EINTR, "Interrupted system call")
            return real_write(fd, data)

        with patch("os.write", side_effect=mock_write):
            with AuthoritativeApprovalStore(store_dir) as store:
                store.put(env)
                assert store.get("appr_eintr_test") == env

    def test_injected_write_failure_fails_closed(self, tmp_path):
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_write_err")

        with patch("os.write", side_effect=OSError(errno.EIO, "Injected disk write error")):
            with AuthoritativeApprovalStore(store_dir) as store:
                with pytest.raises(ApprovalStoreDurabilityError, match="ERR_WRITE_FAILED"):
                    store.put(env)
                assert store.get("appr_write_err") is None

    def test_injected_file_fsync_failure_fails_closed(self, tmp_path):
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_fsync_err")

        real_fsync = os.fsync
        call_count = 0

        def mock_fsync(fd):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError(errno.EIO, "Injected fsync failure")
            return real_fsync(fd)

        with patch("os.fsync", side_effect=mock_fsync):
            with AuthoritativeApprovalStore(store_dir) as store:
                with pytest.raises(ApprovalStoreDurabilityError, match="ERR_FILE_FSYNC_FAILED"):
                    store.put(env)
                assert store.get("appr_fsync_err") is None

    def test_injected_link_failure_fails_closed(self, tmp_path):
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_link_err")

        with patch("os.link", side_effect=OSError(errno.EIO, "Injected link failure")):
            with AuthoritativeApprovalStore(store_dir) as store:
                with pytest.raises(ApprovalStoreDurabilityError, match="ERR_LINK_FAILED"):
                    store.put(env)
                assert store.get("appr_link_err") is None

    def test_injected_dir_fsync_failure_fails_closed(self, tmp_path):
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_dir_fsync_err")

        real_fsync = os.fsync
        call_count = 0

        def mock_fsync(fd):
            nonlocal call_count
            call_count += 1
            # First fsync is for file, second fsync is for directory
            if call_count == 2:
                raise OSError(errno.EIO, "Injected dir fsync failure")
            return real_fsync(fd)

        with patch("os.fsync", side_effect=mock_fsync):
            with AuthoritativeApprovalStore(store_dir) as store:
                with pytest.raises(ApprovalStoreDurabilityError, match="ERR_DIR_FSYNC_FAILED"):
                    store.put(env)

    def test_injected_close_failure_raises_structured_error(self, tmp_path):
        store_dir = str(tmp_path / "store")
        store = AuthoritativeApprovalStore(store_dir)
        target_lock_fd = store._lock_fd
        assert target_lock_fd is not None

        real_close = os.close

        def mock_close(fd):
            if fd == target_lock_fd:
                raise OSError(errno.EIO, "Injected close failure")
            return real_close(fd)

        with patch("os.close", side_effect=mock_close):
            with pytest.raises(ApprovalStoreError, match="ERR_STORE_CLOSE_FAILED"):
                store.close()

        # Single-close semantics: descriptor is disowned immediately and store is closed
        assert store._lock_fd is None
        assert store._closed is True

    def test_operations_on_closed_store_rejected(self, tmp_path):
        store_dir = str(tmp_path / "store")
        store = AuthoritativeApprovalStore(store_dir)
        store.close()

        env = make_valid_signed_approval_envelope("appr_closed")
        with pytest.raises(ApprovalStoreError, match="ERR_STORE_CLOSED"):
            store.put(env)

        with pytest.raises(ApprovalStoreError, match="ERR_STORE_CLOSED"):
            store.get("appr_closed")

    def test_i2_01_injected_unlink_failure_after_publication_fails_closed(self, tmp_path):
        """I2-01: Verifies that unlink failure after publication fails closed with ERR_TEMP_UNLINK_FAILED."""
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_unlink_err")

        real_unlink = os.unlink

        def mock_unlink(path, *args, **kwargs):
            if isinstance(path, str) and path.startswith(".tmp_"):
                raise OSError(errno.EIO, "Injected temp unlink failure")
            return real_unlink(path, *args, **kwargs)

        with patch("os.unlink", side_effect=mock_unlink):
            with AuthoritativeApprovalStore(store_dir) as store:
                with pytest.raises(ApprovalStoreDurabilityError, match="ERR_TEMP_UNLINK_FAILED"):
                    store.put(env)

    def test_i2_01_injected_write_failure_with_cleanup_failure_reports_both(self, tmp_path):
        """I2-01: Verifies that cleanup failures during write errors are preserved and reported."""
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_write_cleanup_err")

        def mock_unlink(path, *args, **kwargs):
            raise OSError(errno.EIO, "Injected unlink cleanup failure")

        with patch("os.write", side_effect=OSError(errno.EIO, "Injected write error")):
            with patch("os.unlink", side_effect=mock_unlink):
                with AuthoritativeApprovalStore(store_dir) as store:
                    with pytest.raises(ApprovalStoreDurabilityError) as exc_info:
                        store.put(env)
                    err_msg = str(exc_info.value)
                    assert "ERR_WRITE_FAILED" in err_msg
                    assert "cleanup failures" in err_msg

    def test_i2_02_lock_file_insecure_mode_rejected_not_repaired(self, tmp_path):
        """I2-02: Verifies that pre-existing lock file with insecure mode is rejected, not fchmod repaired."""
        store_dir = str(tmp_path / "store")
        os.makedirs(store_dir, mode=0o700)
        lock_path = os.path.join(store_dir, ".store.lock")
        with open(lock_path, "wb") as f:
            f.write(b"")
        os.chmod(lock_path, 0o666)

        with pytest.raises(ApprovalStoreSecurityError, match="ERR_LOCK_INSECURE_PERMS"):
            AuthoritativeApprovalStore(store_dir)

    def test_i2_02_lock_file_hard_link_rejected(self, tmp_path):
        """I2-02: Verifies that a hard-linked lock file (st_nlink != 1) is rejected."""
        store_dir = str(tmp_path / "store")
        os.makedirs(store_dir, mode=0o700)
        lock_path = os.path.join(store_dir, ".store.lock")
        with open(lock_path, "wb") as f:
            f.write(b"")
        os.chmod(lock_path, 0o600)
        hardlink_path = os.path.join(store_dir, "hardlink_to_lock")
        os.link(lock_path, hardlink_path)

        with pytest.raises(ApprovalStoreSecurityError, match="ERR_LOCK_HARD_LINK_DETECTED"):
            AuthoritativeApprovalStore(store_dir)

    def test_i2_02_lock_file_not_regular_rejected(self, tmp_path):
        """I2-02: Verifies that non-regular lock file is rejected."""
        store_dir = str(tmp_path / "store")
        os.makedirs(store_dir, mode=0o700)
        lock_path = os.path.join(store_dir, ".store.lock")
        os.mkdir(lock_path, mode=0o700)

        with pytest.raises(ApprovalStoreSecurityError, match="ERR_LOCK_NOT_REGULAR"):
            AuthoritativeApprovalStore(store_dir)

    def test_i2_03_record_file_descriptor_close_failure_in_get(self, tmp_path):
        """I2-03: Verifies descriptor close failure in get() raises ApprovalStoreDurabilityError."""
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            env = make_valid_signed_approval_envelope("appr_get_close_err")
            store.put(env)

            real_close = os.close

            def mock_close(fd):
                if fd != store._lock_fd and fd != store._dir_fd:
                    raise OSError(errno.EIO, "Injected record close failure")
                return real_close(fd)

            with patch("os.close", side_effect=mock_close):
                with pytest.raises(ApprovalStoreDurabilityError, match="ERR_RECORD_CLOSE_FAILED"):
                    store.get("appr_get_close_err")

    def test_i2_03_record_file_size_instability_during_read(self, tmp_path):
        """I2-03: Verifies that size instability between initial stat and read is rejected."""
        store_dir = str(tmp_path / "store")
        with AuthoritativeApprovalStore(store_dir) as store:
            env = make_valid_signed_approval_envelope("appr_size_instability")
            store.put(env)

            real_fstat = os.fstat
            call_count = 0

            def mock_fstat(fd):
                nonlocal call_count
                st = real_fstat(fd)
                if fd != store._lock_fd and fd != store._dir_fd:
                    call_count += 1
                    # Return modified st_size on second fstat call
                    if call_count == 2:
                        return os.stat_result((
                            st.st_mode,
                            st.st_ino,
                            st.st_dev,
                            st.st_nlink,
                            st.st_uid,
                            st.st_gid,
                            st.st_size + 5,
                            int(st.st_atime),
                            int(st.st_mtime),
                            int(st.st_ctime),
                        ))
                return st

            with patch("os.fstat", side_effect=mock_fstat):
                with pytest.raises(ApprovalStoreCorruptionError, match="ERR_SIZE_INSTABILITY"):
                    store.get("appr_size_instability")

    def test_i2_r2_01_flock_unlock_failure_in_put_latches_store_and_raises(self, tmp_path):
        """I2-R2-01: Verifies flock unlock failure in put() latches the store closed and raises structured error."""
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_unlock_put_err")

        real_flock = fcntl.flock

        def mock_flock(fd, operation):
            if operation == fcntl.LOCK_UN:
                raise OSError(errno.EIO, "Injected flock unlock failure")
            return real_flock(fd, operation)

        with patch("fcntl.flock", side_effect=mock_flock):
            with AuthoritativeApprovalStore(store_dir) as store:
                with pytest.raises(ApprovalStoreDurabilityError, match="ERR_LOCK_RELEASE_FAILED"):
                    store.put(env)

                # Store must be latched against further operations
                assert store._lock_corrupted is True
                assert store._closed is True
                with pytest.raises(ApprovalStoreError, match="ERR_STORE_CLOSED"):
                    store.get("appr_unlock_put_err")

    def test_i2_r2_01_flock_unlock_failure_in_get_latches_store_and_raises(self, tmp_path):
        """I2-R2-01: Verifies flock unlock failure in get() latches the store closed and raises structured error."""
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_unlock_get_err")

        with AuthoritativeApprovalStore(store_dir) as store:
            store.put(env)

        real_flock = fcntl.flock

        def mock_flock(fd, operation):
            if operation == fcntl.LOCK_UN:
                raise OSError(errno.EIO, "Injected flock unlock failure")
            return real_flock(fd, operation)

        with patch("fcntl.flock", side_effect=mock_flock):
            with AuthoritativeApprovalStore(store_dir) as store:
                with pytest.raises(ApprovalStoreDurabilityError, match="ERR_LOCK_RELEASE_FAILED"):
                    store.get("appr_unlock_get_err")

                # Store must be latched against further operations
                assert store._lock_corrupted is True
                assert store._closed is True
                with pytest.raises(ApprovalStoreError, match="ERR_STORE_CLOSED"):
                    store.put(env)

    def test_i2_r2_02_single_close_semantics_never_closes_reused_descriptor(self, tmp_path):
        """I2-R2-02: Verifies that after a close error, the descriptor number is disowned and never closed again."""
        store_dir = str(tmp_path / "store")
        store = AuthoritativeApprovalStore(store_dir)
        old_lock_fd = store._lock_fd
        assert old_lock_fd is not None

        closed_fds: list[int] = []
        real_close = os.close

        def mock_close(fd):
            closed_fds.append(fd)
            if fd == old_lock_fd and len([x for x in closed_fds if x == old_lock_fd]) == 1:
                raise OSError(errno.EIO, "Simulated ambiguous close error")
            return real_close(fd)

        with patch("os.close", side_effect=mock_close):
            with pytest.raises(ApprovalStoreDurabilityError, match="ERR_STORE_CLOSE_FAILED"):
                store.close()

        # Under single-close semantics, store._lock_fd was disowned immediately
        assert store._lock_fd is None

        # Simulate a newly opened unrelated descriptor in the process
        dummy_file = tmp_path / "unrelated_file.txt"
        dummy_file.write_text("safe content")
        unrelated_fd = os.open(str(dummy_file), os.O_RDONLY)

        try:
            # Calling close() again must NOT attempt to close the old_lock_fd number again
            store.close()
            # Verify the unrelated_fd was never closed by the store
            # os.fstat on unrelated_fd must succeed
            st = os.fstat(unrelated_fd)
            assert stat.S_ISREG(st.st_mode)
        finally:
            real_close(unrelated_fd)

    def test_i2_r3_01_reader_read_error_combined_with_cleanup_close_failure(self, tmp_path):
        """I2-R3-01: Verifies both read error and fallback close failure are preserved and reported."""
        store_dir = str(tmp_path / "store")
        env = make_valid_signed_approval_envelope("appr_read_close_err")
        with AuthoritativeApprovalStore(store_dir) as store:
            store.put(env)

        store = AuthoritativeApprovalStore(store_dir)
        store_lock_fd = store._lock_fd
        store_dir_fd = store._dir_fd

        real_read = os.read
        real_close = os.close
        close_attempts: list[int] = []

        def mock_read(fd, n):
            raise OSError(errno.EIO, "Injected reader failure during read")

        def mock_close(fd):
            close_attempts.append(fd)
            # Fail close only for reader descriptor
            if fd != store_lock_fd and fd != store_dir_fd:
                raise OSError(errno.EIO, "Injected close failure in reader fallback")
            return real_close(fd)

        try:
            with patch("os.read", side_effect=mock_read):
                with patch("os.close", side_effect=mock_close):
                    with pytest.raises(ApprovalStoreDurabilityError) as exc_info:
                        store.get("appr_read_close_err")

                    err_msg = str(exc_info.value)
                    assert "ERR_READ_FAILED" in err_msg
                    assert "ERR_RECORD_FALLBACK_CLOSE_FAILED" in err_msg
                    # Verify descriptor was attempted closed at most once (single-close)
                    reader_closes = [x for x in close_attempts if x != store_lock_fd and x != store_dir_fd]
                    assert len(reader_closes) == 1
        finally:
            store.close()

    def test_i2_r3_02_constructor_init_error_combined_with_cleanup_close_failure(self, tmp_path):
        """I2-R3-02, I2-R4-01: Verifies constructor retains init error as primary and exposes diagnostic cleanup handle.

        Asserts that close-failed descriptors are marked explicitly with CLOSE_OUTCOME_UNKNOWN in
        ambiguous_close_descriptors, and documents that numeric descriptors must never be retried.
        """
        store_dir = str(tmp_path / "store")
        os.makedirs(store_dir, mode=0o700)
        lock_path = os.path.join(store_dir, ".store.lock")
        with open(lock_path, "wb") as f:
            f.write(b"")
        os.chmod(lock_path, 0o666)  # Insecure lock permissions triggers ERR_LOCK_INSECURE_PERMS

        real_close = os.close

        def mock_close(fd):
            raise OSError(errno.EIO, "Injected close error during constructor cleanup")

        with patch("os.close", side_effect=mock_close):
            with pytest.raises(ApprovalStoreInitializationError) as exc_info:
                AuthoritativeApprovalStore(store_dir)

            err = exc_info.value
            assert isinstance(err, ApprovalStoreInitializationError)
            assert isinstance(err.primary_cause, ApprovalStoreSecurityError)
            assert "ERR_LOCK_INSECURE_PERMS" in str(err.primary_cause)
            assert err.cleanup_error is not None
            assert "ERR_STORE_CLOSE_FAILED" in str(err.cleanup_error)
            assert err.store_instance is not None
            assert err.store_instance._closed is True
            # I2-R4-01: Verify ambiguous_close_descriptors collection and explicit CLOSE_OUTCOME_UNKNOWN status
            assert len(err.ambiguous_close_descriptors) > 0
            for desc_name, fd, exc, outcome in err.ambiguous_close_descriptors:
                assert desc_name in ("dir_fd", "lock_fd")
                assert isinstance(fd, int)
                assert fd > 0
                assert isinstance(exc, OSError)
                assert outcome == CLOSE_OUTCOME_UNKNOWN
            # Backwards compatibility alias check
            assert err.unclosed_descriptors == err.ambiguous_close_descriptors
            assert err.store_instance.cleanup_failures == err.ambiguous_close_descriptors



