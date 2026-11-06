"""Focused unit tests for Dispatcher Admission Pre-Validation Coordinator Core.

Workorder: G07-07B7-GATE6-PHASE2-BLOCKER-B-GATE5-ADMISSION-PREVALIDATION-COORDINATOR-CORE-R1
Mode: LOCAL_CODE_AND_TEMP_UNIT_TESTS_ONLY; PURE_IN_MEMORY_PREVALIDATION;
      NO_SOCKET_OR_DAEMON; NO_ADMITTED_EMISSION; NO_KEY_GENERATION_OR_TRUST_ROOT_ACCESS;
      READY_TO_EXECUTE_FALSE.

Safety Boundary:
- Pure unit tests using synthetic in-memory public keys and temporary directories.
- Strictly ZERO Ed25519 private key generation (Ed25519PrivateKey.generate is forbidden).
- Uses deterministic_test_verifier with synthetic raw 32-byte public keys (b"D"*32, etc.).
- Strictly no socket, IPC, daemon, or DaVinci Resolve calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import os
import stat
import types
from typing import Any, Mapping, Optional, Tuple
import uuid

import pytest

from src.core.contracts.dispatcher_admission_contracts import (
    DispatcherAdmissionContractError,
    DispatcherAdmissionRequest,
    MAX_PAYLOAD_SIZE,
    MIN_PAYLOAD_SIZE,
)
from src.services.dispatcher_admission_coordinator_core import (
    ALLOWED_COORDINATION_STATUSES,
    COORDINATION_STATUS_LEDGER_REJECTED,
    COORDINATION_STATUS_NON_OPERATIONAL_LEDGER_COMMITTED,
    COORDINATION_STATUS_PAYLOAD_INVALID,
    COORDINATION_STATUS_VALID_LEDGER_SKIPPED,
    COORDINATION_STATUS_VERIFICATION_FAILED,
    AdmissionCoordinationFacts,
    DispatcherAdmissionCoordinatorCore,
    DispatcherAdmissionCoordinatorError,
)
from src.services.dispatcher_admission_verifier_core import (
    ALL_UNRESOLVED_PREREQUISITES,
    AdmissionVerificationFacts,
    CANARY_PERMITTED_COMMAND,
    CANARY_TARGET_PROJECT_UID,
    CANARY_TARGET_TIMELINE_UID,
    DispatcherAdmissionVerifierCore,
)
from src.services.durable_admission_ledger import (
    AdmissionLedgerError,
    ConsumedAdmissionRecord,
    DurableAdmissionLedger,
    LEDGER_FILENAME,
    RE_CANONICAL_UUID_V4,
)
from tests.services.test_dispatcher_admission_verifier_core import (
    CANDIDATE_DIGEST,
    PLAN_DIGEST,
    TAU_REFERENCE,
    TEST_DIR_PUB,
    TEST_OP_PUB,
    TEST_TL_PUB,
    TEST_VER_PUB,
    build_synthetic_envelopes,
    deterministic_test_verifier,
    make_test_key_mapping,
    make_valid_request,
)


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def verifier_core() -> DispatcherAdmissionVerifierCore:
    km = make_test_key_mapping()
    return DispatcherAdmissionVerifierCore(
        key_mapping=km,
        verifier=deterministic_test_verifier,
    )


@pytest.fixture
def ledger_env(tmp_path: Any):
    dir_fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    ledger = DurableAdmissionLedger(dir_fd)
    try:
        yield tmp_path, dir_fd, ledger
    finally:
        os.close(dir_fd)


@pytest.fixture
def coordinator(verifier_core: DispatcherAdmissionVerifierCore, ledger_env: Any) -> DispatcherAdmissionCoordinatorCore:
    _, _, ledger = ledger_env
    return DispatcherAdmissionCoordinatorCore(verifier_core=verifier_core, ledger=ledger)


# ==============================================================================
# 1. Safety Invariant & Nominal Subclass Guards
# ==============================================================================

def test_coordination_facts_invariants_operational_admission_permitted() -> None:
    """Facts DTO raises ERR_SAFETY_INVARIANT_VIOLATED if operational_admission_permitted is not False."""
    with pytest.raises(DispatcherAdmissionCoordinatorError) as exc_info:
        AdmissionCoordinationFacts(
            is_valid=True,
            ledger_committed=False,
            status=COORDINATION_STATUS_VALID_LEDGER_SKIPPED,
            error_code=None,
            error_detail=None,
            verified_claims={},
            consumed_record=None,
            unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
            operational_admission_permitted=True,  # VIOLATION
        )
    assert exc_info.value.code == "ERR_SAFETY_INVARIANT_VIOLATED"


def test_coordination_facts_invariants_prohibits_admitted_status() -> None:
    """Facts DTO strictly rejects 'ADMITTED' status with ERR_SAFETY_INVARIANT_VIOLATED."""
    with pytest.raises(DispatcherAdmissionCoordinatorError) as exc_info:
        AdmissionCoordinationFacts(
            is_valid=True,
            ledger_committed=False,
            status="ADMITTED",  # VIOLATION: Never allowed!
            error_code=None,
            error_detail=None,
            verified_claims={},
            consumed_record=None,
            unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
            operational_admission_permitted=False,
        )
    assert exc_info.value.code == "ERR_SAFETY_INVARIANT_VIOLATED"


def test_coordination_facts_invariants_type_enforcement() -> None:
    """Facts DTO rejects non-bool values for boolean fields."""
    for bad_bool in ("True", 1, None, [True]):
        with pytest.raises(DispatcherAdmissionCoordinatorError) as exc_info:
            AdmissionCoordinationFacts(
                is_valid=bad_bool,  # type: ignore
                ledger_committed=False,
                status=COORDINATION_STATUS_VALID_LEDGER_SKIPPED,
                error_code=None,
                error_detail=None,
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
                operational_admission_permitted=False,
            )
        assert exc_info.value.code == "ERR_TYPE_MISMATCH"


def test_coordination_facts_invariants_unresolved_prerequisites() -> None:
    """Facts DTO strictly enforces non-empty tuple of non-empty non-whitespace strings."""
    class SubTuple(tuple):
        pass

    invalid_cases = [
        (),  # empty tuple
        ("",),  # empty string element
        ("   ",),  # whitespace-only string element
        (123,),  # non-string element
        ("VALID_PREREQ", ""),  # mixed with empty string
        ("VALID_PREREQ", "   "),  # mixed with whitespace string
        ("VALID_PREREQ", 123),  # mixed with int
        ("VALID_PREREQ", None),  # mixed with None
        ["VALID_PREREQ"],  # list instead of tuple
        SubTuple(("VALID_PREREQ",)),  # tuple subclass rejected
    ]

    for bad_unresolved in invalid_cases:
        with pytest.raises(DispatcherAdmissionCoordinatorError) as exc_info:
            AdmissionCoordinationFacts(
                is_valid=True,
                ledger_committed=False,
                status=COORDINATION_STATUS_VALID_LEDGER_SKIPPED,
                error_code=None,
                error_detail=None,
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=bad_unresolved,  # type: ignore
                operational_admission_permitted=False,
            )
        assert exc_info.value.code == "ERR_SAFETY_INVARIANT_VIOLATED"


def test_coordination_facts_invariants_ledger_committed_consistency() -> None:
    """Facts DTO requires consumed_record and valid status when ledger_committed=True."""
    # 1. ledger_committed=True but consumed_record is None
    with pytest.raises(DispatcherAdmissionCoordinatorError) as exc_info:
        AdmissionCoordinationFacts(
            is_valid=True,
            ledger_committed=True,
            status=COORDINATION_STATUS_NON_OPERATIONAL_LEDGER_COMMITTED,
            error_code=None,
            error_detail=None,
            verified_claims={},
            consumed_record=None,  # VIOLATION
            unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
            operational_admission_permitted=False,
        )
    assert exc_info.value.code == "ERR_SAFETY_INVARIANT_VIOLATED"

    # 2. ledger_committed=False but consumed_record is present
    dummy_record = ConsumedAdmissionRecord(
        admission_id=str(uuid.uuid4()),
        execution_id=str(uuid.uuid4()),
        preflight_run_id=str(uuid.uuid4()),
        workorder_id="G07-07B7-GATE6-EXEC-R1",
        admitted_at_iso="2026-11-05T08:20:00Z",
    )
    with pytest.raises(DispatcherAdmissionCoordinatorError) as exc_info:
        AdmissionCoordinationFacts(
            is_valid=True,
            ledger_committed=False,
            status=COORDINATION_STATUS_VALID_LEDGER_SKIPPED,
            error_code=None,
            error_detail=None,
            verified_claims={},
            consumed_record=dummy_record,  # VIOLATION
            unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
            operational_admission_permitted=False,
        )
    assert exc_info.value.code == "ERR_SAFETY_INVARIANT_VIOLATED"


def test_coordinator_init_nominal_type_guards(verifier_core: DispatcherAdmissionVerifierCore) -> None:
    """Coordinator __init__ enforces exact nominal types on verifier_core and ledger."""
    # Subclass of verifier_core rejected
    class SubclassVerifier(DispatcherAdmissionVerifierCore):
        pass

    km = make_test_key_mapping()
    sub_verifier = SubclassVerifier(km, verifier=deterministic_test_verifier)
    with pytest.raises(DispatcherAdmissionCoordinatorError) as exc_info:
        DispatcherAdmissionCoordinatorCore(verifier_core=sub_verifier)
    assert exc_info.value.code == "ERR_TYPE_MISMATCH"

    # Foreign type rejected
    with pytest.raises(DispatcherAdmissionCoordinatorError) as exc_info:
        DispatcherAdmissionCoordinatorCore(verifier_core="not_a_verifier")  # type: ignore
    assert exc_info.value.code == "ERR_TYPE_MISMATCH"

    # Foreign ledger rejected
    with pytest.raises(DispatcherAdmissionCoordinatorError) as exc_info:
        DispatcherAdmissionCoordinatorCore(verifier_core=verifier_core, ledger="not_a_ledger")  # type: ignore
    assert exc_info.value.code == "ERR_TYPE_MISMATCH"


def test_coordinator_mode_compatibility_checks(coordinator: DispatcherAdmissionCoordinatorCore) -> None:
    """evaluate_request rejects incompatible execution modes and non-bool parameters."""
    req = make_valid_request()

    # Both flags True -> fail closed before verifier or ledger
    facts = coordinator.evaluate_request(
        req,
        require_operational_readiness=True,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert facts.is_valid is False
    assert facts.ledger_committed is False
    assert facts.status == COORDINATION_STATUS_VERIFICATION_FAILED
    assert facts.error_code == "ERR_INCOMPATIBLE_EXECUTION_MODE"

    # Non-bool flags -> fail closed
    facts2 = coordinator.evaluate_request(
        req,
        require_operational_readiness="yes",  # type: ignore
    )
    assert facts2.is_valid is False
    assert facts2.error_code == "ERR_TYPE_MISMATCH"


# ==============================================================================
# 2. Default Operational Mode Fails Closed
# ==============================================================================

def test_default_operational_mode_fails_closed(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """Default mode (require_operational_readiness=True) always fails closed without ledger mutation."""
    tmp_path, _, _ = ledger_env
    req = make_valid_request()

    facts = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        # require_operational_readiness defaults to True
    )

    assert facts.is_valid is False
    assert facts.ledger_committed is False
    assert facts.operational_admission_permitted is False
    assert facts.status == COORDINATION_STATUS_VERIFICATION_FAILED
    assert facts.error_code == "ERR_OPERATOR_CHALLENGE_UNRESOLVED"
    assert facts.consumed_record is None
    assert "B2_SAME_UID_ATTESTATION_UNRESOLVED" in facts.unresolved_prerequisites

    # Ledger file must NOT exist
    ledger_file = tmp_path / LEDGER_FILENAME
    assert not ledger_file.exists()


# ==============================================================================
# 3. Pre-Validation Priority (Non-Burning Priority)
# ==============================================================================

def test_prevalidation_priority_invalid_signature_does_not_burn_ledger(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """An invalid Tech Lead signature fails verification and does not burn preflight or workorder."""
    tmp_path, _, _ = ledger_env
    req = make_valid_request()
    # Tamper signature
    bad_workorder = req.execution_workorder_record
    object.__setattr__(bad_workorder, "tech_lead_signature", "f" * 128)

    facts = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts.is_valid is False
    assert facts.ledger_committed is False
    assert facts.status == COORDINATION_STATUS_VERIFICATION_FAILED
    assert facts.error_code == "ERR_GATE5_SIGNATURE_INVALID"
    assert not (tmp_path / LEDGER_FILENAME).exists()

    # Verify that a subsequent valid evaluation with the SAME execution/preflight/workorder SUCCEEDS!
    valid_req = make_valid_request()
    commit_facts = coordinator.evaluate_request(
        valid_req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert commit_facts.is_valid is True
    assert commit_facts.ledger_committed is True
    assert commit_facts.status == COORDINATION_STATUS_NON_OPERATIONAL_LEDGER_COMMITTED


def test_prevalidation_priority_expired_workorder_does_not_burn_ledger(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """An expired Gate 5 workorder fails before touching the ledger."""
    tmp_path, _, _ = ledger_env
    # Workorder expired at 08:15:00Z, while TAU_REFERENCE is 08:20:00Z (and Gate 4 is valid until 08:45:00Z)
    req = make_valid_request(e4_valid_until_iso="2026-11-05T08:15:00Z")

    facts = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts.is_valid is False
    assert facts.ledger_committed is False
    assert facts.status == COORDINATION_STATUS_VERIFICATION_FAILED
    assert facts.error_code == "ERR_GATE5_WORKORDER_EXPIRED"
    assert not (tmp_path / LEDGER_FILENAME).exists()


def test_prevalidation_priority_candidate_hash_mismatch_does_not_burn_ledger(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """A candidate hash mismatch fails before touching the ledger."""
    tmp_path, _, _ = ledger_env
    req = make_valid_request()

    facts = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256="0" * 64,  # MISMATCH
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts.is_valid is False
    assert facts.ledger_committed is False
    assert facts.status == COORDINATION_STATUS_VERIFICATION_FAILED
    assert facts.error_code == "ERR_GATE5_CANDIDATE_HASH_MISMATCH"
    assert not (tmp_path / LEDGER_FILENAME).exists()


def test_prevalidation_priority_unsatisfied_predicate_does_not_burn_ledger(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """Unsatisfied Gate 4 predicate fails before touching the ledger."""
    tmp_path, _, _ = ledger_env
    req = make_valid_request()
    orig_to_dict = req.preflight_evidence_envelope.to_dict
    def bad_to_dict():
        d = orig_to_dict()
        d["empty_timeline_verified"] = False
        return d
    object.__setattr__(req.preflight_evidence_envelope, "to_dict", bad_to_dict)

    facts = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts.is_valid is False
    assert facts.ledger_committed is False
    assert facts.status == COORDINATION_STATUS_VERIFICATION_FAILED
    assert facts.error_code == "ERR_GATE4_PREDICATE_UNSATISFIED"
    assert not (tmp_path / LEDGER_FILENAME).exists()


def test_prevalidation_priority_target_mismatch_does_not_burn_ledger(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """Target mismatch (ERR_GATE5_TARGET_MISMATCH) fails before touching ledger; subsequent valid evaluation commits."""
    tmp_path, _, _ = ledger_env
    mismatched_req = make_valid_request()
    rogue_uid = "00000000-0000-4000-8000-000000000099"
    object.__setattr__(mismatched_req.operator_consent_envelope, "target_project_uid", rogue_uid)
    # Recompute operator_signature so signature verification passes in Step 5 and reaches Step 6 (cross-binding)
    new_sig = hashlib.sha512(
        TEST_OP_PUB + mismatched_req.operator_consent_envelope.canonical_signing_payload_bytes()
    ).hexdigest()
    object.__setattr__(mismatched_req.operator_consent_envelope, "operator_signature", new_sig)

    facts = coordinator.evaluate_request(
        mismatched_req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts.is_valid is False
    assert facts.ledger_committed is False
    assert facts.status == COORDINATION_STATUS_VERIFICATION_FAILED
    assert facts.error_code == "ERR_GATE5_TARGET_MISMATCH"
    assert not (tmp_path / LEDGER_FILENAME).exists()

    # Verify that a subsequent valid evaluation with the SAME execution/preflight/workorder SUCCEEDS!
    valid_req = make_valid_request()
    commit_facts = coordinator.evaluate_request(
        valid_req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert commit_facts.is_valid is True
    assert commit_facts.ledger_committed is True
    assert commit_facts.status == COORDINATION_STATUS_NON_OPERATIONAL_LEDGER_COMMITTED
    assert commit_facts.consumed_record is not None
    assert commit_facts.consumed_record.workorder_id == "G07-07B7-GATE6-EXEC-R1"


def test_prevalidation_priority_invalid_admission_id(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """Invalid admission_id fails before touching the verifier or ledger."""
    tmp_path, _, _ = ledger_env
    req = make_valid_request()

    facts = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        admission_id="not-a-valid-uuid",
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts.is_valid is False
    assert facts.ledger_committed is False
    assert facts.status == COORDINATION_STATUS_VERIFICATION_FAILED
    assert facts.error_code == "ERR_INVALID_UUID"
    assert not (tmp_path / LEDGER_FILENAME).exists()


def test_prevalidation_priority_naive_timestamp(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """Naive (non-timezone-aware) timestamp fails before touching ledger."""
    tmp_path, _, _ = ledger_env
    req = make_valid_request()
    naive_dt = datetime(2026, 11, 5, 8, 20, 0)  # tzinfo=None

    facts = coordinator.evaluate_request(
        req,
        current_time=naive_dt,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts.is_valid is False
    assert facts.ledger_committed is False
    assert facts.error_code == "ERR_INVALID_TIMESTAMP"
    assert not (tmp_path / LEDGER_FILENAME).exists()


# ==============================================================================
# 4. Non-Operational Test Commit Mode & Claims Immutability
# ==============================================================================

def test_non_operational_test_commit_writes_record_and_freezes_claims(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """Non-operational test commit mode successfully writes 1 record and freezes verified_claims."""
    tmp_path, _, _ = ledger_env
    req = make_valid_request()
    chosen_admission_id = "00000000-0000-4000-8000-000000000099"

    facts = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        admission_id=chosen_admission_id,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts.is_valid is True
    assert facts.ledger_committed is True
    assert facts.operational_admission_permitted is False
    assert facts.status == COORDINATION_STATUS_NON_OPERATIONAL_LEDGER_COMMITTED
    assert facts.status != "ADMITTED"
    assert facts.error_code is None
    assert facts.consumed_record is not None
    assert facts.consumed_record.admission_id == chosen_admission_id
    assert facts.consumed_record.execution_id == "00000000-0000-4000-8000-000000000004"
    assert facts.consumed_record.preflight_run_id == "00000000-0000-4000-8000-000000000003"
    assert facts.consumed_record.workorder_id == "G07-07B7-GATE6-EXEC-R1"

    # Immutability of verified_claims: item assignment must raise TypeError
    with pytest.raises(TypeError):
        facts.verified_claims["tamper"] = "value"  # type: ignore

    # Check ledger file content: exactly 1 line
    ledger_file = tmp_path / LEDGER_FILENAME
    assert ledger_file.exists()
    lines = ledger_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert chosen_admission_id in lines[0]


def test_valid_ledger_skipped_when_commit_flag_is_false(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """When allow_non_operational_ledger_commit_for_testing is False, returns VALID_LEDGER_SKIPPED."""
    tmp_path, _, _ = ledger_env
    req = make_valid_request()

    facts = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=False,
    )

    assert facts.is_valid is True
    assert facts.ledger_committed is False
    assert facts.status == COORDINATION_STATUS_VALID_LEDGER_SKIPPED
    assert facts.consumed_record is None
    assert not (tmp_path / LEDGER_FILENAME).exists()


# ==============================================================================
# 5. Replay Rejection Codes
# ==============================================================================

def test_replay_duplicate_workorder_id_rejected(
    coordinator: DispatcherAdmissionCoordinatorCore,
) -> None:
    """Duplicate workorder_id causes ERR_GATE5_WORKORDER_ALREADY_CONSUMED."""
    # Commit first request
    req1 = make_valid_request(
        e4_execution_id="00000000-0000-4000-8000-000000000001",
    )
    facts1 = coordinator.evaluate_request(
        req1,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert facts1.ledger_committed is True

    # Second request with SAME workorder_id but different execution_id
    req2 = make_valid_request(
        e4_execution_id="00000000-0000-4000-8000-000000000002",
    )
    facts2 = coordinator.evaluate_request(
        req2,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert facts2.is_valid is False
    assert facts2.ledger_committed is False
    assert facts2.status == COORDINATION_STATUS_LEDGER_REJECTED
    assert facts2.error_code == "ERR_GATE5_WORKORDER_ALREADY_CONSUMED"


def test_replay_duplicate_execution_id_rejected(
    coordinator: DispatcherAdmissionCoordinatorCore,
) -> None:
    """Duplicate execution_id causes ERR_GATE5_WORKORDER_REPLAYED."""
    exec_id = "00000000-0000-4000-8000-000000000001"
    # Commit first request
    req1 = make_valid_request(e4_execution_id=exec_id)
    # Workorder id G07-07B7-GATE6-EXEC-R1
    facts1 = coordinator.evaluate_request(
        req1,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert facts1.ledger_committed is True

    # Second request with different workorder_id but SAME execution_id
    # We can craft a synthetic envelope with workorder_id G07-07B7-GATE6-EXEC-R2
    env1, env2, env3, env4_orig = build_synthetic_envelopes(e4_execution_id=exec_id)
    # Re-sign with workorder_id G07-07B7-GATE6-EXEC-R2
    from src.core.contracts.signed_execution_workorder_contracts import SignedExecutionWorkorderRecord
    env4_init = SignedExecutionWorkorderRecord(
        schema_version=1,
        workorder_id="G07-07B7-GATE6-EXEC-R2",
        execution_id=exec_id,
        tech_lead_id=env4_orig.tech_lead_id,
        tech_lead_key_id=env4_orig.tech_lead_key_id,
        permitted_command=env4_orig.permitted_command,
        plan_digest_sha256=env4_orig.plan_digest_sha256,
        action_payload_digest_sha256=env4_orig.action_payload_digest_sha256,
        target_project_uid=env4_orig.target_project_uid,
        target_timeline_uid=env4_orig.target_timeline_uid,
        reviewed_candidate_sha256=env4_orig.reviewed_candidate_sha256,
        issued_at_iso=env4_orig.issued_at_iso,
        valid_until_iso=env4_orig.valid_until_iso,
        tech_lead_signature="0" * 128,
    )
    sig = hashlib.sha512(TEST_TL_PUB + env4_init.canonical_signing_payload_bytes()).hexdigest()
    env4_new = SignedExecutionWorkorderRecord(
        schema_version=1,
        workorder_id="G07-07B7-GATE6-EXEC-R2",
        execution_id=exec_id,
        tech_lead_id=env4_orig.tech_lead_id,
        tech_lead_key_id=env4_orig.tech_lead_key_id,
        permitted_command=env4_orig.permitted_command,
        plan_digest_sha256=env4_orig.plan_digest_sha256,
        action_payload_digest_sha256=env4_orig.action_payload_digest_sha256,
        target_project_uid=env4_orig.target_project_uid,
        target_timeline_uid=env4_orig.target_timeline_uid,
        reviewed_candidate_sha256=env4_orig.reviewed_candidate_sha256,
        issued_at_iso=env4_orig.issued_at_iso,
        valid_until_iso=env4_orig.valid_until_iso,
        tech_lead_signature=sig,
    )
    req2 = DispatcherAdmissionRequest(
        schema_version=1,
        request_id=str(uuid.uuid4()),
        approval_envelope=env1,
        operator_consent_envelope=env2,
        preflight_evidence_envelope=env3,
        execution_workorder_record=env4_new,
    )

    facts2 = coordinator.evaluate_request(
        req2,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert facts2.is_valid is False
    assert facts2.ledger_committed is False
    assert facts2.status == COORDINATION_STATUS_LEDGER_REJECTED
    assert facts2.error_code == "ERR_GATE5_WORKORDER_REPLAYED"


def test_replay_duplicate_preflight_run_id_rejected(
    coordinator: DispatcherAdmissionCoordinatorCore,
) -> None:
    """Duplicate preflight_run_id causes ERR_GATE4_EVIDENCE_REPLAYED."""
    # Commit first request
    req1 = make_valid_request(
        e4_execution_id="00000000-0000-4000-8000-000000000001",
    )
    facts1 = coordinator.evaluate_request(
        req1,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert facts1.ledger_committed is True

    # Second request with new workorder and new execution_id, but SAME preflight envelope
    env1, env2, env3, env4_orig = build_synthetic_envelopes(e4_execution_id="00000000-0000-4000-8000-000000000002")
    from src.core.contracts.signed_execution_workorder_contracts import SignedExecutionWorkorderRecord
    env4_init = SignedExecutionWorkorderRecord(
        schema_version=1,
        workorder_id="G07-07B7-GATE6-EXEC-R2",
        execution_id="00000000-0000-4000-8000-000000000002",
        tech_lead_id=env4_orig.tech_lead_id,
        tech_lead_key_id=env4_orig.tech_lead_key_id,
        permitted_command=env4_orig.permitted_command,
        plan_digest_sha256=env4_orig.plan_digest_sha256,
        action_payload_digest_sha256=env4_orig.action_payload_digest_sha256,
        target_project_uid=env4_orig.target_project_uid,
        target_timeline_uid=env4_orig.target_timeline_uid,
        reviewed_candidate_sha256=env4_orig.reviewed_candidate_sha256,
        issued_at_iso=env4_orig.issued_at_iso,
        valid_until_iso=env4_orig.valid_until_iso,
        tech_lead_signature="0" * 128,
    )
    sig = hashlib.sha512(TEST_TL_PUB + env4_init.canonical_signing_payload_bytes()).hexdigest()
    env4_new = SignedExecutionWorkorderRecord(
        schema_version=1,
        workorder_id="G07-07B7-GATE6-EXEC-R2",
        execution_id="00000000-0000-4000-8000-000000000002",
        tech_lead_id=env4_orig.tech_lead_id,
        tech_lead_key_id=env4_orig.tech_lead_key_id,
        permitted_command=env4_orig.permitted_command,
        plan_digest_sha256=env4_orig.plan_digest_sha256,
        action_payload_digest_sha256=env4_orig.action_payload_digest_sha256,
        target_project_uid=env4_orig.target_project_uid,
        target_timeline_uid=env4_orig.target_timeline_uid,
        reviewed_candidate_sha256=env4_orig.reviewed_candidate_sha256,
        issued_at_iso=env4_orig.issued_at_iso,
        valid_until_iso=env4_orig.valid_until_iso,
        tech_lead_signature=sig,
    )
    req2 = DispatcherAdmissionRequest(
        schema_version=1,
        request_id=str(uuid.uuid4()),
        approval_envelope=env1,
        operator_consent_envelope=env2,
        preflight_evidence_envelope=env3,  # SAME preflight_run_id
        execution_workorder_record=env4_new,
    )

    facts2 = coordinator.evaluate_request(
        req2,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert facts2.is_valid is False
    assert facts2.ledger_committed is False
    assert facts2.status == COORDINATION_STATUS_LEDGER_REJECTED
    assert facts2.error_code == "ERR_GATE4_EVIDENCE_REPLAYED"


def test_replay_duplicate_admission_id_rejected(
    coordinator: DispatcherAdmissionCoordinatorCore,
) -> None:
    """Duplicate explicit admission_id causes ERR_ADMISSION_ID_REPLAYED."""
    reused_adm_id = "00000000-0000-4000-8000-000000000077"
    # Commit first
    req1 = make_valid_request(
        e4_execution_id="00000000-0000-4000-8000-000000000001",
    )
    facts1 = coordinator.evaluate_request(
        req1,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        admission_id=reused_adm_id,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert facts1.ledger_committed is True

    # Second completely distinct request, but passing SAME admission_id
    # We craft fresh envelopes with different execution_id, preflight_run_id, workorder_id
    from src.core.contracts.signed_execution_workorder_contracts import SignedExecutionWorkorderRecord
    from src.core.contracts.signed_preflight_contracts import SignedPreflightEvidenceEnvelope
    env1, env2, env3_orig, env4_orig = build_synthetic_envelopes(
        e4_execution_id="00000000-0000-4000-8000-000000000002"
    )
    env3_init = SignedPreflightEvidenceEnvelope(
        schema_version=1,
        preflight_run_id="00000000-0000-4000-8000-000000000009",
        verifier_id=env3_orig.verifier_id,
        verifier_key_id=env3_orig.verifier_key_id,
        verified_at_iso=env3_orig.verified_at_iso,
        valid_until_iso=env3_orig.valid_until_iso,
        target_project_uid=env3_orig.target_project_uid,
        target_project_name=env3_orig.target_project_name,
        target_timeline_uid=env3_orig.target_timeline_uid,
        target_timeline_name=env3_orig.target_timeline_name,
        target_timeline_fps_numerator=env3_orig.target_timeline_fps_numerator,
        target_timeline_fps_denominator=env3_orig.target_timeline_fps_denominator,
        observed_start_frame=env3_orig.observed_start_frame,
        project_and_timeline_active=True,
        empty_timeline_verified=True,
        topology_verified=True,
        incident_denylist_verified=True,
        media_pool_source_verified=True,
        source_file_sha256=env3_orig.source_file_sha256,
        audit_chain_head_sha256=env3_orig.audit_chain_head_sha256,
        verifier_signature="0" * 128,
    )
    sig3 = hashlib.sha512(TEST_VER_PUB + env3_init.canonical_signing_payload_bytes()).hexdigest()
    env3_new = SignedPreflightEvidenceEnvelope(
        schema_version=1,
        preflight_run_id="00000000-0000-4000-8000-000000000009",
        verifier_id=env3_orig.verifier_id,
        verifier_key_id=env3_orig.verifier_key_id,
        verified_at_iso=env3_orig.verified_at_iso,
        valid_until_iso=env3_orig.valid_until_iso,
        target_project_uid=env3_orig.target_project_uid,
        target_project_name=env3_orig.target_project_name,
        target_timeline_uid=env3_orig.target_timeline_uid,
        target_timeline_name=env3_orig.target_timeline_name,
        target_timeline_fps_numerator=env3_orig.target_timeline_fps_numerator,
        target_timeline_fps_denominator=env3_orig.target_timeline_fps_denominator,
        observed_start_frame=env3_orig.observed_start_frame,
        project_and_timeline_active=True,
        empty_timeline_verified=True,
        topology_verified=True,
        incident_denylist_verified=True,
        media_pool_source_verified=True,
        source_file_sha256=env3_orig.source_file_sha256,
        audit_chain_head_sha256=env3_orig.audit_chain_head_sha256,
        verifier_signature=sig3,
    )
    env4_init = SignedExecutionWorkorderRecord(
        schema_version=1,
        workorder_id="G07-07B7-GATE6-EXEC-R2",
        execution_id="00000000-0000-4000-8000-000000000002",
        tech_lead_id=env4_orig.tech_lead_id,
        tech_lead_key_id=env4_orig.tech_lead_key_id,
        permitted_command=env4_orig.permitted_command,
        plan_digest_sha256=env4_orig.plan_digest_sha256,
        action_payload_digest_sha256=env4_orig.action_payload_digest_sha256,
        target_project_uid=env4_orig.target_project_uid,
        target_timeline_uid=env4_orig.target_timeline_uid,
        reviewed_candidate_sha256=env4_orig.reviewed_candidate_sha256,
        issued_at_iso=env4_orig.issued_at_iso,
        valid_until_iso=env4_orig.valid_until_iso,
        tech_lead_signature="0" * 128,
    )
    sig4 = hashlib.sha512(TEST_TL_PUB + env4_init.canonical_signing_payload_bytes()).hexdigest()
    env4_new = SignedExecutionWorkorderRecord(
        schema_version=1,
        workorder_id="G07-07B7-GATE6-EXEC-R2",
        execution_id="00000000-0000-4000-8000-000000000002",
        tech_lead_id=env4_orig.tech_lead_id,
        tech_lead_key_id=env4_orig.tech_lead_key_id,
        permitted_command=env4_orig.permitted_command,
        plan_digest_sha256=env4_orig.plan_digest_sha256,
        action_payload_digest_sha256=env4_orig.action_payload_digest_sha256,
        target_project_uid=env4_orig.target_project_uid,
        target_timeline_uid=env4_orig.target_timeline_uid,
        reviewed_candidate_sha256=env4_orig.reviewed_candidate_sha256,
        issued_at_iso=env4_orig.issued_at_iso,
        valid_until_iso=env4_orig.valid_until_iso,
        tech_lead_signature=sig4,
    )
    req2 = DispatcherAdmissionRequest(
        schema_version=1,
        request_id=str(uuid.uuid4()),
        approval_envelope=env1,
        operator_consent_envelope=env2,
        preflight_evidence_envelope=env3_new,
        execution_workorder_record=env4_new,
    )

    facts2 = coordinator.evaluate_request(
        req2,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        admission_id=reused_adm_id,  # REPLAY
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert facts2.is_valid is False
    assert facts2.ledger_committed is False
    assert facts2.status == COORDINATION_STATUS_LEDGER_REJECTED
    assert facts2.error_code == "ERR_ADMISSION_ID_REPLAYED"


# ==============================================================================
# 6. Ledger Failure Propagation
# ==============================================================================

def test_missing_ledger_when_commit_requested(
    verifier_core: DispatcherAdmissionVerifierCore,
) -> None:
    """When coordinator has ledger=None and commit is requested, returns ERR_ADMISSION_LEDGER_MISSING."""
    coord_no_ledger = DispatcherAdmissionCoordinatorCore(verifier_core=verifier_core, ledger=None)
    req = make_valid_request()

    facts = coord_no_ledger.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts.is_valid is False
    assert facts.ledger_committed is False
    assert facts.status == COORDINATION_STATUS_LEDGER_REJECTED
    assert facts.error_code == "ERR_ADMISSION_LEDGER_MISSING"


def test_held_flock_propagates_locked_error(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """When consumed_admissions.jsonl is locked by another fd, returns ERR_ADMISSION_LEDGER_LOCKED."""
    tmp_path, dir_fd, _ = ledger_env
    # Create the ledger file and lock it
    ledger_file_path = tmp_path / LEDGER_FILENAME
    ledger_file_path.write_bytes(b"")
    lock_fd = os.open(str(ledger_file_path), os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        req = make_valid_request()
        facts = coordinator.evaluate_request(
            req,
            current_time=TAU_REFERENCE,
            expected_candidate_sha256=CANDIDATE_DIGEST,
            require_operational_readiness=False,
            allow_non_operational_ledger_commit_for_testing=True,
        )

        assert facts.is_valid is False
        assert facts.ledger_committed is False
        assert facts.status == COORDINATION_STATUS_LEDGER_REJECTED
        assert facts.error_code == "ERR_ADMISSION_LEDGER_LOCKED"
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def test_corrupt_ledger_and_poisoning_propagation(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """Corrupt ledger yields ERR_ADMISSION_LEDGER_CORRUPT, followed by poisoning on subsequent call."""
    tmp_path, _, _ = ledger_env
    ledger_file = tmp_path / LEDGER_FILENAME
    # Write corrupted non-JSON line and set mode 0600
    ledger_file.write_bytes(b'{"admission_id": "corrupt_non_canonical\n')
    ledger_file.chmod(0o600)

    req = make_valid_request()
    facts1 = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts1.is_valid is False
    assert facts1.ledger_committed is False
    assert facts1.status == COORDINATION_STATUS_LEDGER_REJECTED
    assert facts1.error_code == "ERR_ADMISSION_LEDGER_CORRUPT"

    # Subsequent call on the now-poisoned ledger instance must report poisoned error
    facts2 = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )
    assert facts2.is_valid is False
    assert facts2.ledger_committed is False
    assert facts2.status == COORDINATION_STATUS_LEDGER_REJECTED
    assert facts2.error_code in ("ERR_ADMISSION_LEDGER_POISONED", "ERR_LEDGER_POISONED")


def test_consumed_admission_record_validation_error_mapped_to_ledger_rejected(
    coordinator: DispatcherAdmissionCoordinatorCore,
    monkeypatch: pytest.MonkeyPatch,
    ledger_env: Any,
) -> None:
    """When ConsumedAdmissionRecord rejects malformed or missing verified claims, coordinator returns LEDGER_REJECTED."""
    tmp_path, _, _ = ledger_env
    req = make_valid_request()

    # Case 1: Malformed workorder_id in verified claims triggers AdmissionRecordValidationError in ConsumedAdmissionRecord
    malformed_claims = {
        "execution_id": str(uuid.uuid4()),
        "preflight_run_id": str(uuid.uuid4()),
        "workorder_id": "INVALID-WORKORDER-ID",  # Fails RE_WORKORDER_ID
        "permitted_command": CANARY_PERMITTED_COMMAND,
    }
    fake_vf1 = AdmissionVerificationFacts(
        is_valid=True,
        status="SIGNATURES_AND_BINDINGS_VALID",
        error_code=None,
        error_detail=None,
        verified_claims=malformed_claims,
        unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
        operational_admission_permitted=False,
    )
    monkeypatch.setattr(coordinator._verifier_core, "verify", lambda **kwargs: fake_vf1)

    facts1 = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts1.is_valid is False
    assert facts1.ledger_committed is False
    assert facts1.status == COORDINATION_STATUS_LEDGER_REJECTED
    assert facts1.error_code == "ERR_WORKORDER_ID_FORMAT"
    assert facts1.consumed_record is None
    assert not (tmp_path / LEDGER_FILENAME).exists()

    # Case 2: Missing required verified claims (e.g. preflight_run_id missing)
    missing_claims = {
        "execution_id": str(uuid.uuid4()),
        "workorder_id": "G07-07B7-GATE6-EXEC-R1",
    }
    fake_vf2 = AdmissionVerificationFacts(
        is_valid=True,
        status="SIGNATURES_AND_BINDINGS_VALID",
        error_code=None,
        error_detail=None,
        verified_claims=missing_claims,
        unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
        operational_admission_permitted=False,
    )
    monkeypatch.setattr(coordinator._verifier_core, "verify", lambda **kwargs: fake_vf2)

    facts2 = coordinator.evaluate_request(
        req,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts2.is_valid is False
    assert facts2.ledger_committed is False
    assert facts2.status == COORDINATION_STATUS_LEDGER_REJECTED
    assert facts2.error_code == "ERR_TYPE_MISMATCH"
    assert facts2.consumed_record is None
    assert not (tmp_path / LEDGER_FILENAME).exists()


# ==============================================================================
# 7. Wire Payload Evaluation
# ==============================================================================

def test_evaluate_wire_payload_valid_commit(
    coordinator: DispatcherAdmissionCoordinatorCore,
    ledger_env: Any,
) -> None:
    """Valid wire payload evaluates, verifies, and commits through evaluate_wire_payload."""
    tmp_path, _, _ = ledger_env
    req = make_valid_request()
    wire_bytes = req.to_json_bytes()

    facts = coordinator.evaluate_wire_payload(
        wire_bytes,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
        require_operational_readiness=False,
        allow_non_operational_ledger_commit_for_testing=True,
    )

    assert facts.is_valid is True
    assert facts.ledger_committed is True
    assert facts.status == COORDINATION_STATUS_NON_OPERATIONAL_LEDGER_COMMITTED
    assert (tmp_path / LEDGER_FILENAME).exists()


def test_evaluate_wire_payload_bounds_and_type_guards(
    coordinator: DispatcherAdmissionCoordinatorCore,
) -> None:
    """Wire payload enforces bytes/bytearray type and [1, 65536] bounds."""
    # Non-bytes
    facts_str = coordinator.evaluate_wire_payload("not_bytes")  # type: ignore
    assert facts_str.is_valid is False
    assert facts_str.status == COORDINATION_STATUS_PAYLOAD_INVALID
    assert facts_str.error_code == "ERR_INVALID_FRAME"

    # Empty payload (0 bytes < 1)
    facts_empty = coordinator.evaluate_wire_payload(b"")
    assert facts_empty.is_valid is False
    assert facts_empty.status == COORDINATION_STATUS_PAYLOAD_INVALID
    assert facts_empty.error_code == "ERR_FRAME_BOUNDS"

    # Oversized payload (> 65536 bytes)
    facts_oversized = coordinator.evaluate_wire_payload(b"x" * 65537)
    assert facts_oversized.is_valid is False
    assert facts_oversized.status == COORDINATION_STATUS_PAYLOAD_INVALID
    assert facts_oversized.error_code == "ERR_FRAME_BOUNDS"


def test_evaluate_wire_payload_duplicate_json_key(
    coordinator: DispatcherAdmissionCoordinatorCore,
) -> None:
    """Duplicate JSON key in wire payload returns ERR_DUPLICATE_KEY without ledger touch."""
    dup_key_json = b'{"schema_version": 1, "schema_version": 1}'
    facts = coordinator.evaluate_wire_payload(dup_key_json)
    assert facts.is_valid is False
    assert facts.status == COORDINATION_STATUS_PAYLOAD_INVALID
    assert facts.error_code == "ERR_DUPLICATE_KEY"


def test_evaluate_wire_payload_malformed_json(
    coordinator: DispatcherAdmissionCoordinatorCore,
) -> None:
    """Malformed JSON in wire payload returns PAYLOAD_INVALID without ledger touch."""
    malformed = b'{"schema_version": 1, invalid_json'
    facts = coordinator.evaluate_wire_payload(malformed)
    assert facts.is_valid is False
    assert facts.status == COORDINATION_STATUS_PAYLOAD_INVALID
