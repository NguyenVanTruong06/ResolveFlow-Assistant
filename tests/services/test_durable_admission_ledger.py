"""Unit tests for Durable Admission Ledger Core.

Verifies isolated unified ledger component in strict temporary directories:
- Record construction, validation, nominal types, UUID checks, and workorder grammar.
- Deterministic RFC 8785 canonical serialization.
- Descriptor-relative open, read, append, and fsync behavior.
- Independent replay rejection for workorder_id, execution_id, and preflight_run_id.
- Fail-closed torn-tail, malformed JSON, and corrupt ledger handling.
- Poison-on-error and poison-assertion behaviors.
- Flock serialization and lock contention.
- Descriptor-observed mode 0600, nlink == 1, regular-file, and symlink rejection.
- Proof that all operations occur strictly under pytest tmp_path fixtures.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
import unittest.mock as mock
import pytest

from src.services.durable_admission_ledger import (
    AdmissionLedgerCorruptionError,
    AdmissionLedgerLockError,
    AdmissionLedgerPersistenceError,
    AdmissionLedgerPoisonError,
    AdmissionLedgerSecurityError,
    AdmissionRecordValidationError,
    AdmissionReplayError,
    ConsumedAdmissionRecord,
    DurableAdmissionLedger,
    LEDGER_FILENAME,
    parse_ledger_bytes,
    parse_strict_json,
)

VALID_ADMISSION_ID: str = "a1b2c3d4-e5f6-4a1b-8c2d-3e4f5a6b7c8d"
VALID_EXECUTION_ID: str = "11111111-2222-4333-8444-555555555555"
VALID_PREFLIGHT_RUN_ID: str = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
VALID_WORKORDER_ID: str = "G07-07B7-GATE6-EXEC-R1"
VALID_TIMESTAMP: str = "2026-11-05T12:00:00Z"


def make_valid_record(
    *,
    admission_id: str = VALID_ADMISSION_ID,
    execution_id: str = VALID_EXECUTION_ID,
    preflight_run_id: str = VALID_PREFLIGHT_RUN_ID,
    workorder_id: str = VALID_WORKORDER_ID,
    admitted_at_iso: str = VALID_TIMESTAMP,
) -> ConsumedAdmissionRecord:
    """Helper creating a valid ConsumedAdmissionRecord with defaults."""
    return ConsumedAdmissionRecord(
        admission_id=admission_id,
        execution_id=execution_id,
        preflight_run_id=preflight_run_id,
        workorder_id=workorder_id,
        admitted_at_iso=admitted_at_iso,
    )


# ==============================================================================
# Record DTO & Validation Tests
# ==============================================================================

def test_record_creation_valid() -> None:
    rec = make_valid_record()
    assert rec.admission_id == VALID_ADMISSION_ID
    assert rec.execution_id == VALID_EXECUTION_ID
    assert rec.preflight_run_id == VALID_PREFLIGHT_RUN_ID
    assert rec.workorder_id == VALID_WORKORDER_ID
    assert rec.admitted_at_iso == VALID_TIMESTAMP

    # Verify dictionary representation
    d = rec.to_dict()
    assert set(d.keys()) == {
        "admission_id",
        "execution_id",
        "preflight_run_id",
        "workorder_id",
        "admitted_at_iso",
    }

    # Verify JSON line ending
    line = rec.to_json_line()
    assert line.endswith(b"\n")
    assert not line[:-1].endswith(b"\n")


def test_record_validation_invalid_types() -> None:
    # Pass non-str types for each field
    for field_name in ("admission_id", "execution_id", "preflight_run_id", "workorder_id", "admitted_at_iso"):
        kwargs = {
            "admission_id": VALID_ADMISSION_ID,
            "execution_id": VALID_EXECUTION_ID,
            "preflight_run_id": VALID_PREFLIGHT_RUN_ID,
            "workorder_id": VALID_WORKORDER_ID,
            "admitted_at_iso": VALID_TIMESTAMP,
        }
        kwargs[field_name] = 12345
        with pytest.raises(AdmissionRecordValidationError) as exc_info:
            ConsumedAdmissionRecord(**kwargs)
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"

        kwargs[field_name] = True
        with pytest.raises(AdmissionRecordValidationError) as exc_info:
            ConsumedAdmissionRecord(**kwargs)
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"


def test_record_validation_invalid_uuid() -> None:
    for field_name in ("admission_id", "execution_id", "preflight_run_id"):
        kwargs = {
            "admission_id": VALID_ADMISSION_ID,
            "execution_id": VALID_EXECUTION_ID,
            "preflight_run_id": VALID_PREFLIGHT_RUN_ID,
            "workorder_id": VALID_WORKORDER_ID,
            "admitted_at_iso": VALID_TIMESTAMP,
        }
        # Uppercase hex
        kwargs[field_name] = VALID_ADMISSION_ID.upper()
        with pytest.raises(AdmissionRecordValidationError) as exc_info:
            ConsumedAdmissionRecord(**kwargs)
        assert exc_info.value.code == "ERR_INVALID_UUID"

        # Not UUIDv4 (wrong version digit)
        kwargs[field_name] = "a1b2c3d4-e5f6-1a1b-8c2d-3e4f5a6b7c8d"
        with pytest.raises(AdmissionRecordValidationError) as exc_info:
            ConsumedAdmissionRecord(**kwargs)
        assert exc_info.value.code == "ERR_INVALID_UUID"

        # Malformed format
        kwargs[field_name] = "not-a-valid-uuid"
        with pytest.raises(AdmissionRecordValidationError) as exc_info:
            ConsumedAdmissionRecord(**kwargs)
        assert exc_info.value.code == "ERR_INVALID_UUID"


def test_record_validation_invalid_workorder_id() -> None:
    invalid_workorders = [
        "G07-07B7-GATE6-EXEC-R0",        # R0 rejected
        "G07-07B7-GATE6-EXEC-R-1",       # negative rejected
        "G07-07B7-GATE6-EXEC-r1",        # lowercase rejected
        "G07-07B7-GATE6-EXEC-",          # missing revision
        "G07-07B7-GATE5-EXEC-R1",        # wrong gate prefix
        "G07-07B7-GATE6-EXEC-R1 ",       # trailing whitespace
        " G07-07B7-GATE6-EXEC-R1",       # leading whitespace
    ]
    for bad_wo in invalid_workorders:
        with pytest.raises(AdmissionRecordValidationError) as exc_info:
            make_valid_record(workorder_id=bad_wo)
        assert exc_info.value.code == "ERR_WORKORDER_ID_FORMAT"


def test_record_validation_invalid_timestamp() -> None:
    invalid_timestamps = [
        "2026-11-05T12:00:00",           # missing Z
        "2026-11-05T12:00:00+00:00",     # non-Z representation
        "2026-11-05T12:00:00-05:00",     # non-UTC offset
        "2026-11-05 12:00:00Z",          # missing T separator
        "invalid-date-string",
        "2026-11-05T12:00:00Z ",         # trailing whitespace
        " 2026-11-05T12:00:00Z",         # leading whitespace
    ]
    for bad_ts in invalid_timestamps:
        with pytest.raises(AdmissionRecordValidationError) as exc_info:
            make_valid_record(admitted_at_iso=bad_ts)
        assert exc_info.value.code == "ERR_INVALID_TIMESTAMP"


def test_record_closed_world_fields() -> None:
    valid_dict = make_valid_record().to_dict()

    # Missing field
    for missing_key in valid_dict.keys():
        bad_dict = dict(valid_dict)
        del bad_dict[missing_key]
        with pytest.raises(AdmissionRecordValidationError) as exc_info:
            ConsumedAdmissionRecord.from_dict(bad_dict)
        assert exc_info.value.code == "ERR_MISSING_FIELD"

    # Unexpected field
    extra_dict = dict(valid_dict)
    extra_dict["extra_key"] = "extra_value"
    with pytest.raises(AdmissionRecordValidationError) as exc_info:
        ConsumedAdmissionRecord.from_dict(extra_dict)
    assert exc_info.value.code == "ERR_UNEXPECTED_FIELD"


def test_record_duplicate_json_keys() -> None:
    # Construct raw JSON bytes with duplicate key
    raw_json = b'{"admission_id":"a1b2c3d4-e5f6-4a1b-8c2d-3e4f5a6b7c8d","admission_id":"a1b2c3d4-e5f6-4a1b-8c2d-3e4f5a6b7c8d","admitted_at_iso":"2026-11-05T12:00:00Z","execution_id":"11111111-2222-4333-8444-555555555555","preflight_run_id":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee","workorder_id":"G07-07B7-GATE6-EXEC-R1"}\n'
    with pytest.raises(AdmissionLedgerCorruptionError) as exc_info:
        parse_ledger_bytes(raw_json)
    assert exc_info.value.code == "ERR_ADMISSION_LEDGER_CORRUPT"
    assert "Duplicate JSON key" in str(exc_info.value)


# ==============================================================================
# Ledger Component & Storage Tests
# ==============================================================================

@pytest.fixture
def temp_ledger_dir(tmp_path: Path):
    """Provides an isolated directory descriptor rooted in tmp_path."""
    ledger_dir = tmp_path / "ledger_storage"
    ledger_dir.mkdir(mode=0o700, parents=True)
    dir_fd = os.open(str(ledger_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield dir_fd, ledger_dir
    finally:
        os.close(dir_fd)


def test_ledger_initialization_invalid_dir_fd(tmp_path: Path) -> None:
    # Negative fd
    with pytest.raises(AdmissionLedgerSecurityError) as exc_info:
        DurableAdmissionLedger(-1)
    assert exc_info.value.code == "ERR_INVALID_DIR_FD"

    # String fd
    with pytest.raises(AdmissionLedgerSecurityError) as exc_info:
        DurableAdmissionLedger("not-an-int")  # type: ignore[arg-type]
    assert exc_info.value.code == "ERR_INVALID_DIR_FD"

    # Regular file fd (not a directory)
    regular_file = tmp_path / "regular.txt"
    regular_file.write_text("hello", encoding="utf-8")
    file_fd = os.open(str(regular_file), os.O_RDONLY | os.O_CLOEXEC)
    try:
        with pytest.raises(AdmissionLedgerSecurityError) as exc_info:
            DurableAdmissionLedger(file_fd)
        assert exc_info.value.code == "ERR_DIR_FD_NOT_DIR"
    finally:
        os.close(file_fd)


def test_ledger_empty_ledger(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)
    records = ledger.read_all_records()
    assert records == []


def test_ledger_single_append_and_recovery(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec = make_valid_record()
    ledger.consume_admission(rec)

    # Read back through ledger
    records = ledger.read_all_records()
    assert len(records) == 1
    assert records[0] == rec

    # Verify file properties on disk
    file_path = ledger_dir / LEDGER_FILENAME
    assert file_path.exists()
    st = file_path.stat()
    assert stat.S_ISREG(st.st_mode)
    assert stat.S_IMODE(st.st_mode) == 0o600
    assert st.st_nlink == 1


def test_ledger_multiple_appends_and_recovery(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec1 = make_valid_record(
        admission_id="10000000-0000-4000-8000-000000000001",
        execution_id="20000000-0000-4000-8000-000000000001",
        preflight_run_id="30000000-0000-4000-8000-000000000001",
        workorder_id="G07-07B7-GATE6-EXEC-R1",
    )
    rec2 = make_valid_record(
        admission_id="10000000-0000-4000-8000-000000000002",
        execution_id="20000000-0000-4000-8000-000000000002",
        preflight_run_id="30000000-0000-4000-8000-000000000002",
        workorder_id="G07-07B7-GATE6-EXEC-R2",
    )

    ledger.consume_admission(rec1)
    ledger.consume_admission(rec2)

    records = ledger.read_all_records()
    assert len(records) == 2
    assert records[0] == rec1
    assert records[1] == rec2


def test_ledger_replay_workorder_id(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec1 = make_valid_record(workorder_id="G07-07B7-GATE6-EXEC-R1")
    ledger.consume_admission(rec1)

    # Attempt to consume duplicate workorder_id with different other IDs
    rec2 = make_valid_record(
        admission_id="10000000-0000-4000-8000-000000000002",
        execution_id="20000000-0000-4000-8000-000000000002",
        preflight_run_id="30000000-0000-4000-8000-000000000002",
        workorder_id="G07-07B7-GATE6-EXEC-R1",  # duplicate!
    )
    with pytest.raises(AdmissionReplayError) as exc_info:
        ledger.consume_admission(rec2)
    assert exc_info.value.code == "ERR_GATE5_WORKORDER_ALREADY_CONSUMED"


def test_ledger_replay_execution_id(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec1 = make_valid_record(execution_id="20000000-0000-4000-8000-000000000001")
    ledger.consume_admission(rec1)

    rec2 = make_valid_record(
        admission_id="10000000-0000-4000-8000-000000000002",
        execution_id="20000000-0000-4000-8000-000000000001",  # duplicate!
        preflight_run_id="30000000-0000-4000-8000-000000000002",
        workorder_id="G07-07B7-GATE6-EXEC-R2",
    )
    with pytest.raises(AdmissionReplayError) as exc_info:
        ledger.consume_admission(rec2)
    assert exc_info.value.code == "ERR_GATE5_WORKORDER_REPLAYED"


def test_ledger_replay_preflight_run_id(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec1 = make_valid_record(preflight_run_id="30000000-0000-4000-8000-000000000001")
    ledger.consume_admission(rec1)

    rec2 = make_valid_record(
        admission_id="10000000-0000-4000-8000-000000000002",
        execution_id="20000000-0000-4000-8000-000000000002",
        preflight_run_id="30000000-0000-4000-8000-000000000001",  # duplicate!
        workorder_id="G07-07B7-GATE6-EXEC-R2",
    )
    with pytest.raises(AdmissionReplayError) as exc_info:
        ledger.consume_admission(rec2)
    assert exc_info.value.code == "ERR_GATE4_EVIDENCE_REPLAYED"


def test_ledger_replay_admission_id(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec1 = make_valid_record(admission_id="10000000-0000-4000-8000-000000000001")
    ledger.consume_admission(rec1)

    rec2 = make_valid_record(
        admission_id="10000000-0000-4000-8000-000000000001",  # duplicate!
        execution_id="20000000-0000-4000-8000-000000000002",
        preflight_run_id="30000000-0000-4000-8000-000000000002",
        workorder_id="G07-07B7-GATE6-EXEC-R2",
    )
    with pytest.raises(AdmissionReplayError) as exc_info:
        ledger.consume_admission(rec2)
    assert exc_info.value.code == "ERR_ADMISSION_ID_REPLAYED"


def test_ledger_check_replay_helper(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec = make_valid_record(
        workorder_id="G07-07B7-GATE6-EXEC-R1",
        execution_id="20000000-0000-4000-8000-000000000001",
        preflight_run_id="30000000-0000-4000-8000-000000000001",
    )
    ledger.consume_admission(rec)

    # Calling check_replay with clean IDs passes without error
    ledger.check_replay(
        workorder_id="G07-07B7-GATE6-EXEC-R2",
        execution_id="20000000-0000-4000-8000-000000000002",
        preflight_run_id="30000000-0000-4000-8000-000000000002",
    )

    # Replay on workorder_id
    with pytest.raises(AdmissionReplayError) as exc_info:
        ledger.check_replay(
            workorder_id="G07-07B7-GATE6-EXEC-R1",
            execution_id="20000000-0000-4000-8000-000000000002",
            preflight_run_id="30000000-0000-4000-8000-000000000002",
        )
    assert exc_info.value.code == "ERR_GATE5_WORKORDER_ALREADY_CONSUMED"

    # Replay on execution_id
    with pytest.raises(AdmissionReplayError) as exc_info:
        ledger.check_replay(
            workorder_id="G07-07B7-GATE6-EXEC-R2",
            execution_id="20000000-0000-4000-8000-000000000001",
            preflight_run_id="30000000-0000-4000-8000-000000000002",
        )
    assert exc_info.value.code == "ERR_GATE5_WORKORDER_REPLAYED"

    # Replay on preflight_run_id
    with pytest.raises(AdmissionReplayError) as exc_info:
        ledger.check_replay(
            workorder_id="G07-07B7-GATE6-EXEC-R2",
            execution_id="20000000-0000-4000-8000-000000000002",
            preflight_run_id="30000000-0000-4000-8000-000000000001",
        )
    assert exc_info.value.code == "ERR_GATE4_EVIDENCE_REPLAYED"


def test_ledger_torn_tail_missing_newline(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec = make_valid_record()
    line_without_newline = rec.to_json_bytes()  # no trailing newline!

    file_path = ledger_dir / LEDGER_FILENAME
    file_path.write_bytes(line_without_newline)
    file_path.chmod(0o600)

    with pytest.raises(AdmissionLedgerCorruptionError) as exc_info:
        ledger.read_all_records()
    assert exc_info.value.code == "ERR_ADMISSION_LEDGER_CORRUPT"
    assert "torn tail" in str(exc_info.value).lower()

    # Verify that corruption poisons the ledger
    assert ledger.is_poisoned is True
    with pytest.raises(AdmissionLedgerPoisonError) as poison_exc:
        ledger.read_all_records()
    assert poison_exc.value.code == "ERR_ADMISSION_LEDGER_POISONED"


def test_ledger_corrupted_json_in_middle(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec = make_valid_record()
    corrupt_content = rec.to_json_line() + b'{"incomplete_json": true\n'

    file_path = ledger_dir / LEDGER_FILENAME
    file_path.write_bytes(corrupt_content)
    file_path.chmod(0o600)

    with pytest.raises(AdmissionLedgerCorruptionError) as exc_info:
        ledger.read_all_records()
    assert exc_info.value.code == "ERR_ADMISSION_LEDGER_CORRUPT"
    assert ledger.is_poisoned is True


def test_ledger_empty_line_detected(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec = make_valid_record()
    corrupt_content = rec.to_json_line() + b"\n"  # extra newline -> empty line

    file_path = ledger_dir / LEDGER_FILENAME
    file_path.write_bytes(corrupt_content)
    file_path.chmod(0o600)

    with pytest.raises(AdmissionLedgerCorruptionError) as exc_info:
        ledger.read_all_records()
    assert exc_info.value.code == "ERR_ADMISSION_LEDGER_CORRUPT"
    assert "empty line" in str(exc_info.value).lower()
    assert ledger.is_poisoned is True


def test_ledger_duplicate_id_in_file_fails_parse(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec1 = make_valid_record()
    rec2 = make_valid_record()  # identical to rec1
    duplicate_content = rec1.to_json_line() + rec2.to_json_line()

    file_path = ledger_dir / LEDGER_FILENAME
    file_path.write_bytes(duplicate_content)
    file_path.chmod(0o600)

    with pytest.raises(AdmissionLedgerCorruptionError) as exc_info:
        ledger.read_all_records()
    assert exc_info.value.code == "ERR_ADMISSION_LEDGER_CORRUPT"
    assert "duplicate" in str(exc_info.value).lower()
    assert ledger.is_poisoned is True


def test_ledger_lock_contention(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    file_path = ledger_dir / LEDGER_FILENAME
    file_path.write_bytes(b"")
    file_path.chmod(0o600)

    # Acquire exclusive non-blocking lock in a separate descriptor
    contention_fd = os.open(str(file_path), os.O_RDWR | os.O_CLOEXEC)
    try:
        fcntl.flock(contention_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # consume_admission should raise AdmissionLedgerLockError
        rec = make_valid_record()
        with pytest.raises(AdmissionLedgerLockError) as exc_info:
            ledger.consume_admission(rec)
        assert exc_info.value.code == "ERR_ADMISSION_LEDGER_LOCKED"
    finally:
        fcntl.flock(contention_fd, fcntl.LOCK_UN)
        os.close(contention_fd)


def test_ledger_write_failure_poisons_instance(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec = make_valid_record()
    with mock.patch("src.services.durable_admission_ledger._write_all", side_effect=OSError("Disk write simulated failure")):
        with pytest.raises(AdmissionLedgerPersistenceError) as exc_info:
            ledger.consume_admission(rec)
        assert exc_info.value.code == "ERR_ADMISSION_LEDGER_PERSISTENCE_FAILED"

    assert ledger.is_poisoned is True
    with pytest.raises(AdmissionLedgerPoisonError) as poison_exc:
        ledger.consume_admission(rec)
    assert poison_exc.value.code == "ERR_ADMISSION_LEDGER_POISONED"


def test_ledger_fsync_failure_poisons_instance(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec = make_valid_record()
    with mock.patch("os.fsync", side_effect=OSError("Fsync simulated EIO")):
        with pytest.raises(AdmissionLedgerPersistenceError) as exc_info:
            ledger.consume_admission(rec)
        assert exc_info.value.code == "ERR_ADMISSION_LEDGER_PERSISTENCE_FAILED"

    assert ledger.is_poisoned is True


def test_ledger_security_unsafe_mode(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    file_path = ledger_dir / LEDGER_FILENAME
    file_path.write_bytes(b"")
    # Set unsafe permissions (0644 instead of 0600)
    file_path.chmod(0o644)

    with pytest.raises(AdmissionLedgerSecurityError) as exc_info:
        ledger.read_all_records()
    assert exc_info.value.code == "ERR_UNSAFE_FILE_MODE"

    rec = make_valid_record()
    with pytest.raises(AdmissionLedgerSecurityError) as exc_info2:
        ledger.consume_admission(rec)
    assert exc_info2.value.code == "ERR_UNSAFE_FILE_MODE"


def test_ledger_security_multiple_links(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    file_path = ledger_dir / LEDGER_FILENAME
    file_path.write_bytes(b"")
    file_path.chmod(0o600)

    # Create a second hardlink to induce st_nlink == 2
    hardlink_path = ledger_dir / "hardlink_copy.jsonl"
    os.link(str(file_path), str(hardlink_path))

    try:
        with pytest.raises(AdmissionLedgerSecurityError) as exc_info:
            ledger.read_all_records()
        assert exc_info.value.code == "ERR_LINK_COUNT_ANOMALY"
    finally:
        hardlink_path.unlink()


def test_ledger_security_symlink_rejection(temp_ledger_dir, tmp_path: Path) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    # Create target file outside and symlink to it
    target_file = tmp_path / "outside_target.jsonl"
    target_file.write_bytes(b"")
    target_file.chmod(0o600)

    symlink_path = ledger_dir / LEDGER_FILENAME
    os.symlink(str(target_file), str(symlink_path))

    with pytest.raises(AdmissionLedgerSecurityError) as exc_info:
        ledger.read_all_records()
    assert exc_info.value.code == "ERR_SYMLINK_DETECTED"


def test_ledger_security_non_regular_file(temp_ledger_dir) -> None:
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    # Create directory with name consumed_admissions.jsonl
    sub_dir = ledger_dir / LEDGER_FILENAME
    sub_dir.mkdir(mode=0o700)

    with pytest.raises(AdmissionLedgerSecurityError) as exc_info:
        ledger.read_all_records()
    assert exc_info.value.code in ("ERR_NOT_REGULAR", "ERR_CANNOT_OPEN_LEDGER")


def test_all_filesystem_fixtures_under_tmp_path(temp_ledger_dir, tmp_path: Path) -> None:
    """Proof assertion: verifies that all fixture paths reside strictly inside tmp_path."""
    dir_fd, ledger_dir = temp_ledger_dir

    # Verify that ledger_dir is a resolved child of tmp_path
    resolved_ledger_dir = ledger_dir.resolve()
    resolved_tmp_path = tmp_path.resolve()

    assert resolved_tmp_path in resolved_ledger_dir.parents
    assert not str(resolved_ledger_dir).startswith("/var/lib/resolveflow")
    assert not str(resolved_ledger_dir).startswith("/etc/resolveflow")
    assert not str(resolved_ledger_dir).startswith("/run/resolveflow")


def test_ledger_rejects_subclass_with_overridden_serializer(temp_ledger_dir) -> None:
    """Regression test for BLOCKER-B-ADMISSION-LEDGER-CORE-R1-01:

    Verifies that consume_admission() enforces exact nominal identity
    (type(record) is ConsumedAdmissionRecord) and rejects any subclass
    attempting to override serialization methods, ensuring zero disk mutation.
    """
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    class MaliciousSubclassWithOverriddenSerializer(ConsumedAdmissionRecord):
        def to_json_line(self) -> bytes:
            return b'{"bypassed": true, "malicious_override": true}\n'

        def to_json_bytes(self) -> bytes:
            return b'{"bypassed": true, "malicious_override": true}'

        def to_dict(self) -> dict[str, str]:
            return {"bypassed": "true"}

    subclass_instance = MaliciousSubclassWithOverriddenSerializer(
        admission_id=VALID_ADMISSION_ID,
        execution_id=VALID_EXECUTION_ID,
        preflight_run_id=VALID_PREFLIGHT_RUN_ID,
        workorder_id=VALID_WORKORDER_ID,
        admitted_at_iso=VALID_TIMESTAMP,
    )

    with pytest.raises(AdmissionRecordValidationError) as exc_info:
        ledger.consume_admission(subclass_instance)
    assert exc_info.value.code == "ERR_TYPE_MISMATCH"
    assert "Expected exact ConsumedAdmissionRecord" in str(exc_info.value)
    assert "MaliciousSubclassWithOverriddenSerializer" in str(exc_info.value)

    # Prove zero writes occurred on disk
    file_path = ledger_dir / LEDGER_FILENAME
    assert not file_path.exists()


def test_ledger_rejects_record_mutated_with_setattr_before_consume(temp_ledger_dir) -> None:
    """Regression test for BLOCKER-B-ADMISSION-LEDGER-CORE-R2-01:

    Verifies that mutating a record after construction via object.__setattr__
    with invalid data is caught by the snapshot re-validation at consume entry,
    raising AdmissionRecordValidationError and ensuring zero disk mutation.
    """
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec = make_valid_record()
    # Mutate field after construction via object.__setattr__
    object.__setattr__(rec, "workorder_id", "MALFORMED_WORKORDER_R0")

    with pytest.raises(AdmissionRecordValidationError) as exc_info:
        ledger.consume_admission(rec)
    assert exc_info.value.code == "ERR_WORKORDER_ID_FORMAT"

    # Mutate with bad UUID
    rec2 = make_valid_record()
    object.__setattr__(rec2, "execution_id", "bad-uuid")
    with pytest.raises(AdmissionRecordValidationError) as exc_info2:
        ledger.consume_admission(rec2)
    assert exc_info2.value.code == "ERR_INVALID_UUID"

    # Mutate with non-str type
    rec3 = make_valid_record()
    object.__setattr__(rec3, "preflight_run_id", 99999)
    with pytest.raises(AdmissionRecordValidationError) as exc_info3:
        ledger.consume_admission(rec3)
    assert exc_info3.value.code == "ERR_TYPE_MISMATCH"

    # Prove zero writes occurred on disk
    file_path = ledger_dir / LEDGER_FILENAME
    assert not file_path.exists()


def test_ledger_snapshot_isolates_against_post_consume_mutation(temp_ledger_dir) -> None:
    """Regression test for BLOCKER-B-ADMISSION-LEDGER-CORE-R2-01:

    Verifies that modifying the caller's record instance via object.__setattr__
    after consume_admission() does not alter the persisted ledger record on disk.
    """
    dir_fd, ledger_dir = temp_ledger_dir
    ledger = DurableAdmissionLedger(dir_fd)

    rec = make_valid_record(workorder_id="G07-07B7-GATE6-EXEC-R1")
    ledger.consume_admission(rec)

    # Mutate the caller's object in memory after consumption
    object.__setattr__(rec, "workorder_id", "G07-07B7-GATE6-EXEC-R999")

    # Read back from disk; must reflect original immutable snapshot
    persisted = ledger.read_all_records()
    assert len(persisted) == 1
    assert persisted[0].workorder_id == "G07-07B7-GATE6-EXEC-R1"


