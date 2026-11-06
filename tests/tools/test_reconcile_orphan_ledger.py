"""Tests for Offline Reconciler Core for Signer Oracle Orphan Ledgers.

Plan 07B7 Phase 2, Blocker C, Prerequisite C-2.
Isolated tests rooted strictly in pytest temporary directories (tmp_path).
Zero production ledger access, zero host state, zero network/IPC.
"""

from __future__ import annotations

import fcntl
import os
import stat
import sys
from typing import Dict, Generator
from unittest.mock import patch

import pytest

from src.core.contracts.trial_authorization_contracts import canonical_json_bytes
from src.services.durable_nonce_ledger import (
    OracleExecutionRecord,
    OracleExecutionTerminalRecord,
)
from src.tools.reconcile_orphan_ledger import (
    APPROVED_EXECUTION_TERMINALS_FILE,
    APPROVED_VERIFIED_EXECUTIONS_FILE,
    AssessmentResult,
    CrashBoundary,
    IncidentEvidenceBundle,
    ReconciliationDisposition,
    ReconcilerDisabledError,
    ReconcilerDurabilityError,
    ReconcilerLedgerCorruptionError,
    ReconcilerLedgerSecurityError,
    ReconcilerTerminalConflictError,
    _test_only_append_oracle_terminal_record,
    append_oracle_terminal_record_descriptor_relative,
    assess_orphan_reconciliation,
    identify_orphans,
    main,
    read_terminal_records_from_dir_fd,
    read_verified_executions_from_dir_fd,
    validate_directory_descriptor,
    validate_filename_strictly,
    validate_terminal_bindings,
    is_strictly_within_approved_temporary_root,
)

# ------------------------------------------------------------------------------
# Test Fixtures (Temporary Directory Backend)
# ------------------------------------------------------------------------------

@pytest.fixture
def nonces_dir_fd(tmp_path) -> Generator[int, None, None]:
    """Provides an isolated directory descriptor with 0700 permissions."""
    os.chmod(str(tmp_path), 0o700)
    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield fd
    finally:
        os.close(fd)


def write_test_ledger(path, content: bytes) -> None:
    path.write_bytes(content)
    os.chmod(str(path), 0o600)


def make_verified_record(
    execution_id: str = "exec-test-001",
    authorization_id: str = "auth-test-001",
    proposal_revision: str = "R1",
    target_project_uid: str = "proj-test-uid",
    target_timeline_uid: str = "tl-test-uid",
    verification_status: str = "VERIFIED",
    verified_at_iso: str = "2026-11-05T12:00:00Z",
) -> OracleExecutionRecord:
    return OracleExecutionRecord(
        schema_version=1,
        execution_id=execution_id,
        authorization_id=authorization_id,
        proposal_revision=proposal_revision,
        target_project_uid=target_project_uid,
        target_timeline_uid=target_timeline_uid,
        verification_status=verification_status,
        verified_at_iso=verified_at_iso,
    )


def make_terminal_record(
    execution_id: str = "exec-test-001",
    authorization_id: str = "auth-test-001",
    terminal_state: str = "ABORTED",
    proposal_revision: str = "R1",
    target_project_uid: str = "proj-test-uid",
    target_timeline_uid: str = "tl-test-uid",
    action_payload_digest_sha256: str = "a" * 64,
    recorded_at_iso: str = "2026-11-05T12:05:00Z",
) -> OracleExecutionTerminalRecord:
    return OracleExecutionTerminalRecord(
        schema_version=1,
        execution_id=execution_id,
        authorization_id=authorization_id,
        terminal_state=terminal_state,
        proposal_revision=proposal_revision,
        target_project_uid=target_project_uid,
        target_timeline_uid=target_timeline_uid,
        action_payload_digest_sha256=action_payload_digest_sha256,
        recorded_at_iso=recorded_at_iso,
    )


# ------------------------------------------------------------------------------
# 1. Valid Orphan Classification Tests
# ------------------------------------------------------------------------------

def test_identify_orphans_classifies_correctly():
    rec1 = make_verified_record(execution_id="exec-001")
    rec2 = make_verified_record(execution_id="exec-002")
    rec3 = make_verified_record(execution_id="exec-003")

    verified = {rec1.execution_id: rec1, rec2.execution_id: rec2, rec3.execution_id: rec3}
    term2 = make_terminal_record(execution_id="exec-002", terminal_state="COMPLETED")
    terminals = {term2.execution_id: term2}

    orphans = identify_orphans(verified, terminals)
    assert len(orphans) == 2
    assert [o.execution_id for o in orphans] == ["exec-001", "exec-003"]


def test_identify_orphans_empty_when_all_terminal():
    rec1 = make_verified_record(execution_id="exec-001")
    verified = {rec1.execution_id: rec1}
    term1 = make_terminal_record(execution_id="exec-001", terminal_state="ABORTED")
    terminals = {term1.execution_id: term1}

    orphans = identify_orphans(verified, terminals)
    assert orphans == []


def test_identify_orphans_no_ttl_or_time_dependency():
    """Verifies that old verified records are not dropped or auto-expired."""
    old_rec = make_verified_record(execution_id="exec-ancient", verified_at_iso="2020-01-01T00:00:00Z")
    verified = {old_rec.execution_id: old_rec}
    terminals: Dict[str, OracleExecutionTerminalRecord] = {}

    orphans = identify_orphans(verified, terminals)
    assert len(orphans) == 1
    assert orphans[0].execution_id == "exec-ancient"


# ------------------------------------------------------------------------------
# 2. Malformed / Torn / Duplicate / Conflicting Ledgers Tests
# ------------------------------------------------------------------------------

def test_verified_ledger_torn_tail_rejected(tmp_path, nonces_dir_fd):
    rec = make_verified_record()
    file_path = tmp_path / APPROVED_VERIFIED_EXECUTIONS_FILE
    write_test_ledger(file_path, rec.canonical_bytes())  # Missing trailing newline!

    with pytest.raises(ReconcilerLedgerCorruptionError, match="ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED"):
        read_verified_executions_from_dir_fd(nonces_dir_fd)


def test_terminal_ledger_torn_tail_rejected(tmp_path, nonces_dir_fd):
    rec = make_terminal_record()
    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE
    write_test_ledger(file_path, rec.canonical_bytes())  # Missing trailing newline!

    with pytest.raises(ReconcilerLedgerCorruptionError, match="ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED"):
        read_terminal_records_from_dir_fd(nonces_dir_fd)


def test_empty_line_corruption_detected(tmp_path, nonces_dir_fd):
    rec = make_verified_record()
    file_path = tmp_path / APPROVED_VERIFIED_EXECUTIONS_FILE
    write_test_ledger(file_path, b"\n" + rec.canonical_bytes() + b"\n")

    with pytest.raises(ReconcilerLedgerCorruptionError, match="ERR_CORRUPT_RECORD"):
        read_verified_executions_from_dir_fd(nonces_dir_fd)


def test_json_parse_error_detected(tmp_path, nonces_dir_fd):
    file_path = tmp_path / APPROVED_VERIFIED_EXECUTIONS_FILE
    write_test_ledger(file_path, b'{"invalid json"\n')

    with pytest.raises(ReconcilerLedgerCorruptionError, match="ERR_JSON_PARSE_ERROR"):
        read_verified_executions_from_dir_fd(nonces_dir_fd)


def test_duplicate_json_key_rejected_in_verified_ledger(tmp_path, nonces_dir_fd):
    """Regression test for Finding 03: reject duplicate object keys fail-closed."""
    dup_key_json = (
        b'{"schema_version": 1, "execution_id": "exec-001", "execution_id": "exec-001", '
        b'"authorization_id": "auth-001", "proposal_revision": "R1", '
        b'"target_project_uid": "p", "target_timeline_uid": "t", '
        b'"verification_status": "VERIFIED", "verified_at_iso": "2026-11-05T12:00:00Z"}\n'
    )
    file_path = tmp_path / APPROVED_VERIFIED_EXECUTIONS_FILE
    write_test_ledger(file_path, dup_key_json)

    with pytest.raises(ReconcilerLedgerCorruptionError, match="ERR_JSON_DUPLICATE_KEY"):
        read_verified_executions_from_dir_fd(nonces_dir_fd)


def test_duplicate_json_key_rejected_in_terminal_ledger(tmp_path, nonces_dir_fd):
    """Regression test for Finding 03: reject duplicate object keys in terminal ledger."""
    dup_key_json = (
        b'{"schema_version": 1, "execution_id": "exec-001", "terminal_state": "ABORTED", '
        b'"terminal_state": "COMPLETED", "authorization_id": "auth-001", "proposal_revision": "R1", '
        b'"target_project_uid": "p", "target_timeline_uid": "t", '
        b'"action_payload_digest_sha256": "' + b"a" * 64 + b'", "recorded_at_iso": "2026-11-05T12:00:00Z"}\n'
    )
    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE
    write_test_ledger(file_path, dup_key_json)

    with pytest.raises(ReconcilerLedgerCorruptionError, match="ERR_JSON_DUPLICATE_KEY"):
        read_terminal_records_from_dir_fd(nonces_dir_fd)


def test_duplicate_execution_id_in_verified_ledger_rejected(tmp_path, nonces_dir_fd):
    rec = make_verified_record(execution_id="exec-dup-001")
    line = rec.canonical_bytes() + b"\n"
    file_path = tmp_path / APPROVED_VERIFIED_EXECUTIONS_FILE
    write_test_ledger(file_path, line + line)

    with pytest.raises(ReconcilerLedgerSecurityError, match="ERR_LEDGER_COLLISION"):
        read_verified_executions_from_dir_fd(nonces_dir_fd)


def test_terminal_record_unverified_execution_rejected():
    verified = {"exec-001": make_verified_record(execution_id="exec-001")}
    terminals = {"exec-unverified": make_terminal_record(execution_id="exec-unverified")}

    with pytest.raises(ReconcilerLedgerCorruptionError, match="ERR_ORACLE_TERMINAL_UNVERIFIED"):
        validate_terminal_bindings(verified, terminals)


def test_terminal_record_identity_mismatch_rejected():
    verified = {"exec-001": make_verified_record(execution_id="exec-001", authorization_id="auth-A")}
    terminals = {"exec-001": make_terminal_record(execution_id="exec-001", authorization_id="auth-B")}

    with pytest.raises(ReconcilerLedgerCorruptionError, match="ERR_ORACLE_TERMINAL_IDENTITY_MISMATCH"):
        validate_terminal_bindings(verified, terminals)


def test_terminal_ledger_conflicting_state_rejected(tmp_path, nonces_dir_fd):
    term1 = make_terminal_record(execution_id="exec-001", terminal_state="COMPLETED")
    term2 = make_terminal_record(execution_id="exec-001", terminal_state="ABORTED")

    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE
    write_test_ledger(file_path, term1.canonical_bytes() + b"\n" + term2.canonical_bytes() + b"\n")

    with pytest.raises(ReconcilerTerminalConflictError, match="ERR_TERMINAL_STATE_CONFLICT"):
        read_terminal_records_from_dir_fd(nonces_dir_fd)


def test_terminal_ledger_same_state_conflicting_identity_rejected(tmp_path, nonces_dir_fd):
    """Regression test for Finding R2-02: duplicate terminal record with same state but different identity fails closed."""
    term1 = make_terminal_record(execution_id="exec-001", terminal_state="ABORTED", target_project_uid="proj-1")
    term2 = make_terminal_record(execution_id="exec-001", terminal_state="ABORTED", target_project_uid="proj-2")

    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE
    write_test_ledger(file_path, term1.canonical_bytes() + b"\n" + term2.canonical_bytes() + b"\n")

    with pytest.raises(ReconcilerTerminalConflictError, match="ERR_TERMINAL_STATE_CONFLICT"):
        read_terminal_records_from_dir_fd(nonces_dir_fd)


def test_terminal_ledger_exact_idempotent_replay_accepted(tmp_path, nonces_dir_fd):
    """Regression test for Finding R2-02: duplicate terminal record is accepted if and only if all fields match exactly."""
    term1 = make_terminal_record(execution_id="exec-001", terminal_state="ABORTED")
    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE
    write_test_ledger(file_path, term1.canonical_bytes() + b"\n" + term1.canonical_bytes() + b"\n")

    terminals = read_terminal_records_from_dir_fd(nonces_dir_fd)
    assert len(terminals) == 1
    assert terminals["exec-001"].terminal_state == "ABORTED"


# ------------------------------------------------------------------------------
# 3. Path Traversal & Filename Security Tests (Finding 02)
# ------------------------------------------------------------------------------

def test_filename_path_traversal_rejected():
    with pytest.raises(ReconcilerLedgerSecurityError, match="ERR_SECURITY_VIOLATION"):
        validate_filename_strictly("../escape.jsonl")

    with pytest.raises(ReconcilerLedgerSecurityError, match="ERR_SECURITY_VIOLATION"):
        validate_filename_strictly("/etc/passwd")

    with pytest.raises(ReconcilerLedgerSecurityError, match="ERR_SECURITY_VIOLATION"):
        validate_filename_strictly("subdir/oracle_verified_executions.jsonl")


def test_unapproved_filename_rejected():
    with pytest.raises(ReconcilerLedgerSecurityError, match="unapproved ledger filename"):
        validate_filename_strictly("custom_ledger.jsonl")


# ------------------------------------------------------------------------------
# 4. Authoritative Assessment Fail-Closed & Simulation Tests (Finding 01)
# ------------------------------------------------------------------------------

def test_authoritative_assessment_always_fails_closed_without_charter():
    """Regression test for Finding 01: authoritative assessment must fail closed with NO_ACTION."""
    orphan = make_verified_record()
    evidence = IncidentEvidenceBundle(
        evidence_id="evi-001",
        has_authority_charter=True,
        is_authenticated=True,
        crash_boundary=CrashBoundary.BOUNDARY_1.value,
        dispatcher_intent_present=False,
        dispatcher_wal_action_pending=False,
        corroborating_evidence_valid=True,
    )
    # Default authoritative mode
    res = assess_orphan_reconciliation(orphan, evidence, allow_simulation=False)
    assert res.disposition == ReconciliationDisposition.NO_ACTION
    assert res.reason_code == "ERR_C1_CHARTER_NOT_ADOPTED"
    assert res.is_authoritative is False


def test_simulation_assessment_boundary_1_aborted():
    orphan = make_verified_record()
    evidence = IncidentEvidenceBundle(
        evidence_id="evi-sim-b1",
        has_authority_charter=True,
        is_authenticated=True,
        crash_boundary=CrashBoundary.BOUNDARY_1.value,
        dispatcher_intent_present=False,
        dispatcher_wal_action_pending=False,
        corroborating_evidence_valid=True,
    )
    res = assess_orphan_reconciliation(orphan, evidence, allow_simulation=True)
    assert res.disposition == ReconciliationDisposition.ABORTED
    assert res.reason_code == "ZERO_MUTATION_PROVEN_PRE_INTENT"
    assert res.is_authoritative is False


def test_simulation_assessment_boundary_2_aborted_with_signed_physical_observation():
    orphan = make_verified_record()
    evidence = IncidentEvidenceBundle(
        evidence_id="evi-sim-b2",
        has_authority_charter=True,
        is_authenticated=True,
        crash_boundary=CrashBoundary.BOUNDARY_2.value,
        dispatcher_intent_present=True,
        dispatcher_wal_action_pending=False,
        corroborating_evidence_valid=True,
        physical_observation_empty=True,
        physical_observation_signed=True,
    )
    res = assess_orphan_reconciliation(orphan, evidence, allow_simulation=True)
    assert res.disposition == ReconciliationDisposition.ABORTED
    assert res.reason_code == "ZERO_MUTATION_PROVEN_PRE_ACTION_PHYSICAL_CONFIRMED"


def test_simulation_assessment_boundary_2_unsigned_yields_in_doubt():
    orphan = make_verified_record()
    evidence = IncidentEvidenceBundle(
        evidence_id="evi-sim-b2-unsigned",
        has_authority_charter=True,
        is_authenticated=True,
        crash_boundary=CrashBoundary.BOUNDARY_2.value,
        dispatcher_intent_present=True,
        dispatcher_wal_action_pending=False,
        corroborating_evidence_valid=True,
        physical_observation_empty=True,
        physical_observation_signed=False,
    )
    res = assess_orphan_reconciliation(orphan, evidence, allow_simulation=True)
    assert res.disposition == ReconciliationDisposition.IN_DOUBT
    assert res.reason_code == "PHYSICAL_ATTESTATION_MISSING_BOUNDARY_2"


def test_simulation_assessment_wal_action_pending_prohibits_aborted():
    orphan = make_verified_record()
    evidence = IncidentEvidenceBundle(
        evidence_id="evi-sim-wal-pending",
        has_authority_charter=True,
        is_authenticated=True,
        crash_boundary=CrashBoundary.BOUNDARY_1.value,
        dispatcher_wal_action_pending=True,
        corroborating_evidence_valid=True,
    )
    res = assess_orphan_reconciliation(orphan, evidence, allow_simulation=True)
    assert res.disposition == ReconciliationDisposition.IN_DOUBT
    assert res.reason_code == "MUTATION_AMBIGUOUS_ACTION_PENDING"


def test_simulation_assessment_daemon_active_fails_closed():
    orphan = make_verified_record()
    evidence = IncidentEvidenceBundle(
        evidence_id="evi-sim-daemon",
        has_authority_charter=True,
        is_authenticated=True,
        daemon_active=True,
    )
    res = assess_orphan_reconciliation(orphan, evidence, allow_simulation=True)
    assert res.disposition == ReconciliationDisposition.NO_ACTION
    assert res.reason_code == "ERR_DAEMON_ACTIVE"


def test_simulation_assessment_disputed_fails_closed():
    orphan = make_verified_record()
    evidence = IncidentEvidenceBundle(
        evidence_id="evi-sim-disputed",
        has_authority_charter=True,
        is_authenticated=True,
        disputed=True,
    )
    res = assess_orphan_reconciliation(orphan, evidence, allow_simulation=True)
    assert res.disposition == ReconciliationDisposition.NO_ACTION
    assert res.reason_code == "ERR_EVIDENCE_DISPUTED"


def test_simulation_assessment_boundaries_3_4_5b_in_doubt():
    orphan = make_verified_record()
    for b in [CrashBoundary.BOUNDARY_3, CrashBoundary.BOUNDARY_4, CrashBoundary.BOUNDARY_5B]:
        evidence = IncidentEvidenceBundle(
            evidence_id=f"evi-{b.value}",
            has_authority_charter=True,
            is_authenticated=True,
            crash_boundary=b.value,
        )
        res = assess_orphan_reconciliation(orphan, evidence, allow_simulation=True)
        assert res.disposition == ReconciliationDisposition.IN_DOUBT


# ------------------------------------------------------------------------------
# 5. Public Persistence API Unconditional Fail-Closed Tests (Finding R2-01)
# ------------------------------------------------------------------------------

def test_public_append_api_unconditionally_disabled(nonces_dir_fd):
    """Regression test for Finding R2-01: public persistence API must fail closed unconditionally."""
    ver_rec = make_verified_record()
    term_rec = make_terminal_record()
    verified = {ver_rec.execution_id: ver_rec}

    with pytest.raises(ReconcilerDisabledError, match="ERR_C1_CHARTER_NOT_ADOPTED"):
        append_oracle_terminal_record_descriptor_relative(
            dir_fd=nonces_dir_fd,
            record=term_rec,
            verified_records=verified,
        )


# ------------------------------------------------------------------------------
# 6. Test-Only Persistence Backend Isolation & Concurrency Tests (Finding R2-01, R2-02, R3-01)
# ------------------------------------------------------------------------------

def test_test_only_append_readlink_oserror_fails_closed(tmp_path, nonces_dir_fd):
    """Regression test for Finding R3-01: inability to resolve descriptor path via readlink fails closed."""
    ver_rec = make_verified_record()
    term_rec = make_terminal_record()
    verified = {ver_rec.execution_id: ver_rec}

    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE

    with patch("os.readlink", side_effect=OSError("Permission denied on /proc")):
        with pytest.raises(ReconcilerLedgerSecurityError, match="ERR_SECURITY_VIOLATION: unable to resolve directory descriptor path"):
            _test_only_append_oracle_terminal_record(
                dir_fd=nonces_dir_fd,
                record=term_rec,
                verified_records=verified,
            )

    # Prove no file was created or written
    assert not file_path.exists()


def test_test_only_append_production_path_rejected(tmp_path, nonces_dir_fd):
    """Regression test for Finding R3-01: descriptor path pointing to production is strictly rejected."""
    ver_rec = make_verified_record()
    term_rec = make_terminal_record()
    verified = {ver_rec.execution_id: ver_rec}

    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE

    with patch("os.readlink", return_value="/var/lib/resolveflow/signer/nonces"):
        with pytest.raises(ReconcilerLedgerSecurityError, match="production path.*strictly prohibited"):
            _test_only_append_oracle_terminal_record(
                dir_fd=nonces_dir_fd,
                record=term_rec,
                verified_records=verified,
            )

    assert not file_path.exists()


def test_test_only_append_non_tmp_path_rejected(tmp_path, nonces_dir_fd):
    """Regression test for Finding R3-01: descriptor path outside temporary directory is rejected."""
    ver_rec = make_verified_record()
    term_rec = make_terminal_record()
    verified = {ver_rec.execution_id: ver_rec}

    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE

    with patch("os.readlink", return_value="/home/user/workspace"):
        with pytest.raises(ReconcilerLedgerSecurityError, match="not strictly within an approved temporary root"):
            _test_only_append_oracle_terminal_record(
                dir_fd=nonces_dir_fd,
                record=term_rec,
                verified_records=verified,
            )

    assert not file_path.exists()


def test_test_only_append_lookalike_tmp_prefixes_rejected(tmp_path, nonces_dir_fd):
    """Regression test for Finding R4-01: reject lookalike prefixes and persistent pytest substrings."""
    ver_rec = make_verified_record()
    term_rec = make_terminal_record()
    verified = {ver_rec.execution_id: ver_rec}

    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE

    lookalikes = [
        "/tmp-other/data",
        "/var/tmp-other/data",
        "/home/user/pytest-data",
        "/tmp",  # Root itself cannot be the ledger dir
    ]

    for bad_path in lookalikes:
        with patch("os.readlink", return_value=bad_path):
            with pytest.raises(ReconcilerLedgerSecurityError, match="not strictly within an approved temporary root"):
                _test_only_append_oracle_terminal_record(
                    dir_fd=nonces_dir_fd,
                    record=term_rec,
                    verified_records=verified,
                )

    assert not file_path.exists()


def test_is_strictly_within_approved_temporary_root_unit():
    """Unit test for Finding R4-01: strict directory boundary containment."""
    # Lookalike prefixes
    assert not is_strictly_within_approved_temporary_root("/tmp-other")
    assert not is_strictly_within_approved_temporary_root("/tmp-other/child")
    assert not is_strictly_within_approved_temporary_root("/var/tmp-other")
    assert not is_strictly_within_approved_temporary_root("/var/tmp-other/child")

    # Unrelated paths containing pytest substring
    assert not is_strictly_within_approved_temporary_root("/home/user/pytest-data")
    assert not is_strictly_within_approved_temporary_root("/var/data/pytest_dir")

    # Root directories themselves (must be child, not root)
    assert not is_strictly_within_approved_temporary_root("/tmp")
    assert not is_strictly_within_approved_temporary_root("/var/tmp")

    # Production directories
    assert not is_strictly_within_approved_temporary_root("/var/lib/resolveflow/signer/nonces")
    assert not is_strictly_within_approved_temporary_root("/run/resolveflow")

    # Valid subdirectories
    assert is_strictly_within_approved_temporary_root("/tmp/pytest-of-user/test0")
    assert is_strictly_within_approved_temporary_root("/tmp/isolated_test_dir")
    assert is_strictly_within_approved_temporary_root("/var/tmp/isolated_test_dir")


def test_test_only_append_success_and_exact_idempotency(tmp_path, nonces_dir_fd):
    ver_rec = make_verified_record()
    term_rec = make_terminal_record()
    verified = {ver_rec.execution_id: ver_rec}
    existing_terminals: Dict[str, OracleExecutionTerminalRecord] = {}

    # First append: newly recorded
    newly_recorded = _test_only_append_oracle_terminal_record(
        dir_fd=nonces_dir_fd,
        record=term_rec,
        verified_records=verified,
        existing_terminal_records=existing_terminals,
    )
    assert newly_recorded is True
    assert term_rec.execution_id in existing_terminals

    # Verify file content
    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE
    assert file_path.exists()
    content = file_path.read_bytes()
    assert content.endswith(b"\n")

    # Second append: exact duplicate is recognized as idempotent replay under flock
    second_recorded = _test_only_append_oracle_terminal_record(
        dir_fd=nonces_dir_fd,
        record=term_rec,
        verified_records=verified,
        existing_terminal_records=existing_terminals,
    )
    assert second_recorded is False
    assert file_path.read_bytes() == content


def test_test_only_append_conflict_on_identity_divergence(tmp_path, nonces_dir_fd):
    """Regression test for Finding R2-02: same execution_id & state but different project_uid fails closed."""
    ver_rec1 = make_verified_record(execution_id="exec-001", target_project_uid="proj-1")
    ver_rec2 = make_verified_record(execution_id="exec-001", target_project_uid="proj-2")
    term_rec1 = make_terminal_record(execution_id="exec-001", terminal_state="ABORTED", target_project_uid="proj-1")
    term_rec2 = make_terminal_record(execution_id="exec-001", terminal_state="ABORTED", target_project_uid="proj-2")

    # First commit
    _test_only_append_oracle_terminal_record(
        dir_fd=nonces_dir_fd,
        record=term_rec1,
        verified_records={"exec-001": ver_rec1},
    )

    # Second commit with divergent project_uid must fail closed with conflict
    with pytest.raises(ReconcilerTerminalConflictError, match="ERR_TERMINAL_STATE_CONFLICT"):
        _test_only_append_oracle_terminal_record(
            dir_fd=nonces_dir_fd,
            record=term_rec2,
            verified_records={"exec-001": ver_rec2},
        )


def test_test_only_append_conflict_on_state_divergence(tmp_path, nonces_dir_fd):
    ver_rec = make_verified_record()
    term_rec_aborted = make_terminal_record(terminal_state="ABORTED")
    term_rec_completed = make_terminal_record(terminal_state="COMPLETED")
    verified = {ver_rec.execution_id: ver_rec}

    _test_only_append_oracle_terminal_record(
        dir_fd=nonces_dir_fd,
        record=term_rec_aborted,
        verified_records=verified,
    )

    with pytest.raises(ReconcilerTerminalConflictError, match="ERR_TERMINAL_STATE_CONFLICT"):
        _test_only_append_oracle_terminal_record(
            dir_fd=nonces_dir_fd,
            record=term_rec_completed,
            verified_records=verified,
        )


def test_test_only_append_lock_contention(tmp_path, nonces_dir_fd):
    ver_rec = make_verified_record()
    term_rec = make_terminal_record()
    verified = {ver_rec.execution_id: ver_rec}

    file_path = tmp_path / APPROVED_EXECUTION_TERMINALS_FILE
    file_path.touch(mode=0o600)

    contention_fd = os.open(str(file_path), os.O_RDWR)
    fcntl.flock(contention_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        with pytest.raises(ReconcilerDurabilityError, match="ERR_LOCK_CONTENTION"):
            _test_only_append_oracle_terminal_record(
                dir_fd=nonces_dir_fd,
                record=term_rec,
                verified_records=verified,
            )
    finally:
        fcntl.flock(contention_fd, fcntl.LOCK_UN)
        os.close(contention_fd)


def test_test_only_append_unverified_execution_rejected(nonces_dir_fd):
    term_rec = make_terminal_record(execution_id="exec-unverified")
    verified: Dict[str, OracleExecutionRecord] = {}

    with pytest.raises(ReconcilerLedgerSecurityError, match="ERR_EXECUTION_NOT_FOUND"):
        _test_only_append_oracle_terminal_record(
            dir_fd=nonces_dir_fd,
            record=term_rec,
            verified_records=verified,
        )


def test_test_only_append_identity_binding_mismatch_rejected(nonces_dir_fd):
    ver_rec = make_verified_record(execution_id="exec-001", target_project_uid="proj-actual")
    term_rec = make_terminal_record(execution_id="exec-001", target_project_uid="proj-tampered")
    verified = {ver_rec.execution_id: ver_rec}

    with pytest.raises(ReconcilerLedgerSecurityError, match="ERR_IDENTITY_BINDING_MISMATCH"):
        _test_only_append_oracle_terminal_record(
            dir_fd=nonces_dir_fd,
            record=term_rec,
            verified_records=verified,
        )


# ------------------------------------------------------------------------------
# 7. Write / Fsync Failure & Mode Validation Tests
# ------------------------------------------------------------------------------

def test_directory_descriptor_mode_validation(tmp_path):
    os.chmod(str(tmp_path), 0o755)  # Permissive mode
    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(ReconcilerLedgerSecurityError, match="ERR_INVALID_MODE"):
            validate_directory_descriptor(fd, expected_mode=0o700)
    finally:
        os.close(fd)


def test_multi_link_regular_file_rejected(tmp_path, nonces_dir_fd):
    ver_rec = make_verified_record()
    file_path = tmp_path / APPROVED_VERIFIED_EXECUTIONS_FILE
    write_test_ledger(file_path, ver_rec.canonical_bytes() + b"\n")

    # Create hard link
    hardlink_path = tmp_path / "hardlink.jsonl"
    os.link(str(file_path), str(hardlink_path))

    with pytest.raises(ReconcilerLedgerSecurityError, match="link count 2, expected 1"):
        read_verified_executions_from_dir_fd(nonces_dir_fd)


def test_symlink_file_rejected(tmp_path, nonces_dir_fd):
    real_file = tmp_path / "real.jsonl"
    write_test_ledger(real_file, b'{"test": 1}\n')
    symlink_path = tmp_path / APPROVED_VERIFIED_EXECUTIONS_FILE
    symlink_path.symlink_to(real_file)

    with pytest.raises(ReconcilerLedgerSecurityError, match="is a symlink"):
        read_verified_executions_from_dir_fd(nonces_dir_fd)


def test_append_write_failure_raises_durability_error(nonces_dir_fd):
    ver_rec = make_verified_record()
    term_rec = make_terminal_record()
    verified = {ver_rec.execution_id: ver_rec}

    with patch("os.write", side_effect=OSError("Disk full")):
        with pytest.raises(ReconcilerDurabilityError, match="ERR_LEDGER_WRITE_FAILED"):
            _test_only_append_oracle_terminal_record(
                dir_fd=nonces_dir_fd,
                record=term_rec,
                verified_records=verified,
            )


def test_append_fsync_failure_raises_durability_error(nonces_dir_fd):
    ver_rec = make_verified_record()
    term_rec = make_terminal_record()
    verified = {ver_rec.execution_id: ver_rec}

    with patch("os.fsync", side_effect=OSError("I/O error during fsync")):
        with pytest.raises(ReconcilerDurabilityError, match="ERR_LEDGER_FSYNC_FAILED"):
            _test_only_append_oracle_terminal_record(
                dir_fd=nonces_dir_fd,
                record=term_rec,
                verified_records=verified,
            )


# ------------------------------------------------------------------------------
# 8. Disabled CLI Stopping Before Argument or Filesystem Access Tests
# ------------------------------------------------------------------------------

def test_cli_entry_point_unconditionally_fails_closed():
    with pytest.raises(ReconcilerDisabledError, match="ERR_C1_CHARTER_NOT_ADOPTED"):
        main()


def test_cli_entry_point_fails_closed_regardless_of_arguments():
    test_argv = ["reconcile_orphan_ledger.py", "--force", "--reconcile", "--all", "/var/lib/resolveflow"]
    with patch.object(sys, "argv", test_argv):
        with pytest.raises(ReconcilerDisabledError, match="ERR_C1_CHARTER_NOT_ADOPTED"):
            main()
