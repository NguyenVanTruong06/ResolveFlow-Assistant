"""Dispatcher Admission Pre-Validation Coordinator Core.

Implements the isolated pre-validation-and-consumption coordinator enforcing the
accepted Pre-Validation Priority (BLOCKER-B-ADMISSION-SERVER-R1-01 /
GATE4-AUTH-DESIGN-R6-01) between DispatcherAdmissionVerifierCore and
DurableAdmissionLedger, as specified in Workorder
G07-07B7-GATE6-PHASE2-BLOCKER-B-GATE5-ADMISSION-PREVALIDATION-COORDINATOR-CORE-R1
and docs/architecture/07B7-GATE6-PHASE2-BLOCKER-B-GATE5-ADMISSION-SERVER-DESIGN-R1.md §4.1–§4.2.

STRICT SAFETY CONSTRAINTS & DESIGN INVARIANTS:
1. Pre-Validation Priority: Every wire-frame bound, strict JSON duplicate-key check,
   DTO schema/type check, cryptographic signature, semantic cross-binding, technical
   predicate, validity window, and operational readiness check MUST execute in memory
   BEFORE opening, locking, reading, or mutating DurableAdmissionLedger (consumed_admissions.jsonl),
   preventing inadvertent burning of valid Gate 4 preflight evidence or Gate 5 workorders.
2. Operational Evaluation Fails Closed: require_operational_readiness=True by default
   MUST ALWAYS fail closed (ERR_OPERATOR_CHALLENGE_UNRESOLVED, operational_admission_permitted=False,
   ledger_committed=False) without touching any ledger.
3. Permanent Non-Emission of ADMITTED: The module MUST NEVER emit "ADMITTED" or
   construct/return DispatcherAdmissionResponse(status="ADMITTED").
4. Zero daemon/listener, zero socket/network, zero Resolve SDK calls, and zero
   Ed25519 private-key generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import types
from typing import Any, Mapping, Optional, Tuple, Union
import uuid

from src.core.contracts.dispatcher_admission_contracts import (
    DispatcherAdmissionContractError,
    DispatcherAdmissionRequest,
    MAX_PAYLOAD_SIZE,
    MIN_PAYLOAD_SIZE,
)
from src.core.contracts.trial_authorization_contracts import AcceptanceGateError
from src.services.dispatcher_admission_verifier_core import (
    ALL_UNRESOLVED_PREREQUISITES,
    AdmissionVerificationFacts,
    DispatcherAdmissionVerifierCore,
)
from src.services.durable_admission_ledger import (
    AdmissionLedgerError,
    AdmissionRecordValidationError,
    ConsumedAdmissionRecord,
    DurableAdmissionLedger,
    RE_CANONICAL_UUID_V4,
)

# ------------------------------------------------------------------------------
# Closed Coordination Status Constants (Explicitly Excludes "ADMITTED")
# ------------------------------------------------------------------------------

COORDINATION_STATUS_PAYLOAD_INVALID: str = "PAYLOAD_INVALID"
COORDINATION_STATUS_VERIFICATION_FAILED: str = "VERIFICATION_FAILED"
COORDINATION_STATUS_LEDGER_REJECTED: str = "LEDGER_REJECTED"
COORDINATION_STATUS_VALID_LEDGER_SKIPPED: str = "SIGNATURES_AND_BINDINGS_VALID_LEDGER_SKIPPED"
COORDINATION_STATUS_NON_OPERATIONAL_LEDGER_COMMITTED: str = "NON_OPERATIONAL_PREVALIDATION_AND_LEDGER_COMMITTED"

ALLOWED_COORDINATION_STATUSES: frozenset[str] = frozenset({
    COORDINATION_STATUS_PAYLOAD_INVALID,
    COORDINATION_STATUS_VERIFICATION_FAILED,
    COORDINATION_STATUS_LEDGER_REJECTED,
    COORDINATION_STATUS_VALID_LEDGER_SKIPPED,
    COORDINATION_STATUS_NON_OPERATIONAL_LEDGER_COMMITTED,
})


# ------------------------------------------------------------------------------
# Error Hierarchy
# ------------------------------------------------------------------------------

class DispatcherAdmissionCoordinatorError(AcceptanceGateError, ValueError):
    """Raised when coordinator configuration, nominal types, or safety invariants fail."""

    def __init__(self, code: str, detail: str = "") -> None:
        message = f"[{code}] {detail}" if detail else f"[{code}]"
        super().__init__(message)
        self.code: str = code
        self.detail: str = detail


# ------------------------------------------------------------------------------
# Immutable Coordination Facts DTO
# ------------------------------------------------------------------------------

@dataclass(frozen=True)
class AdmissionCoordinationFacts:
    """Immutable factual report of pre-validation and ledger coordination.

    SAFETY INVARIANTS:
    - Reports deterministic verification facts and replay ledger disposition.
    - NEVER emits or returns "ADMITTED".
    - operational_admission_permitted is permanently False.
    - verified_claims is frozen as an immutable MappingProxyType.
    """

    is_valid: bool
    ledger_committed: bool
    status: str
    error_code: Optional[str]
    error_detail: Optional[str]
    verified_claims: Mapping[str, Any]
    consumed_record: Optional[ConsumedAdmissionRecord]
    unresolved_prerequisites: Tuple[str, ...]
    operational_admission_permitted: bool = False

    def __post_init__(self) -> None:
        # 1. Exact nominal boolean type enforcement (rejects int/subclasses/None)
        if type(self.is_valid) is not bool:
            raise DispatcherAdmissionCoordinatorError(
                "ERR_TYPE_MISMATCH",
                f"is_valid must be bool, got {type(self.is_valid).__name__}",
            )
        if type(self.ledger_committed) is not bool:
            raise DispatcherAdmissionCoordinatorError(
                "ERR_TYPE_MISMATCH",
                f"ledger_committed must be bool, got {type(self.ledger_committed).__name__}",
            )
        if type(self.operational_admission_permitted) is not bool:
            raise DispatcherAdmissionCoordinatorError(
                "ERR_TYPE_MISMATCH",
                f"operational_admission_permitted must be bool, got {type(self.operational_admission_permitted).__name__}",
            )

        # 2. Strict Safety Invariants
        if self.operational_admission_permitted is not False:
            raise DispatcherAdmissionCoordinatorError(
                "ERR_SAFETY_INVARIANT_VIOLATED",
                "operational_admission_permitted must permanently be False in coordinator core",
            )
        if self.status not in ALLOWED_COORDINATION_STATUSES:
            raise DispatcherAdmissionCoordinatorError(
                "ERR_SAFETY_INVARIANT_VIOLATED",
                f"status '{self.status}' is not permitted in coordination facts",
            )
        if (
            type(self.unresolved_prerequisites) is not tuple
            or len(self.unresolved_prerequisites) == 0
            or not all(type(x) is str and bool(x.strip()) for x in self.unresolved_prerequisites)
        ):
            raise DispatcherAdmissionCoordinatorError(
                "ERR_SAFETY_INVARIANT_VIOLATED",
                "unresolved_prerequisites must be a non-empty tuple of non-empty non-whitespace strings",
            )
        if self.ledger_committed and (
            not self.is_valid
            or type(self.consumed_record) is not ConsumedAdmissionRecord
            or self.status != COORDINATION_STATUS_NON_OPERATIONAL_LEDGER_COMMITTED
        ):
            raise DispatcherAdmissionCoordinatorError(
                "ERR_SAFETY_INVARIANT_VIOLATED",
                "inconsistent ledger_committed state: requires is_valid=True, type(consumed_record)=ConsumedAdmissionRecord, and status=NON_OPERATIONAL_PREVALIDATION_AND_LEDGER_COMMITTED",
            )
        if (not self.ledger_committed) and self.consumed_record is not None:
            raise DispatcherAdmissionCoordinatorError(
                "ERR_SAFETY_INVARIANT_VIOLATED",
                "consumed_record must be None when ledger_committed is False",
            )

        # 3. Freeze verified_claims immutably
        claims_input = self.verified_claims if self.verified_claims is not None else {}
        if not isinstance(claims_input, Mapping):
            raise DispatcherAdmissionCoordinatorError(
                "ERR_TYPE_MISMATCH",
                f"verified_claims must be a Mapping, got {type(claims_input).__name__}",
            )
        object.__setattr__(
            self,
            "verified_claims",
            types.MappingProxyType(dict(claims_input)),
        )


# ------------------------------------------------------------------------------
# Dispatcher Admission Coordinator Core
# ------------------------------------------------------------------------------

class DispatcherAdmissionCoordinatorCore:
    """Pre-validation and replay ledger coordinator for Dispatcher admission.

    Guarantees strict Pre-Validation Priority:
    Every structural, schema, signature, semantic, and validity constraint is verified
    in memory before the durable admission replay ledger is ever accessed.
    """

    def __init__(
        self,
        verifier_core: DispatcherAdmissionVerifierCore,
        ledger: Optional[DurableAdmissionLedger] = None,
    ) -> None:
        if type(verifier_core) is not DispatcherAdmissionVerifierCore:
            raise DispatcherAdmissionCoordinatorError(
                "ERR_TYPE_MISMATCH",
                f"verifier_core must be exact type DispatcherAdmissionVerifierCore, got {type(verifier_core).__name__}",
            )
        if ledger is not None and type(ledger) is not DurableAdmissionLedger:
            raise DispatcherAdmissionCoordinatorError(
                "ERR_TYPE_MISMATCH",
                f"ledger must be exact type DurableAdmissionLedger or None, got {type(ledger).__name__}",
            )
        self._verifier_core: DispatcherAdmissionVerifierCore = verifier_core
        self._ledger: Optional[DurableAdmissionLedger] = ledger

    @property
    def verifier_core(self) -> DispatcherAdmissionVerifierCore:
        return self._verifier_core

    @property
    def ledger(self) -> Optional[DurableAdmissionLedger]:
        return self._ledger

    def evaluate_request(
        self,
        request: Any,
        *,
        current_time: Optional[datetime] = None,
        expected_candidate_sha256: Optional[str] = None,
        admission_id: Optional[str] = None,
        require_operational_readiness: bool = True,
        allow_non_operational_ledger_commit_for_testing: bool = False,
    ) -> AdmissionCoordinationFacts:
        """Evaluates an admission request with strict pre-validation priority.

        Args:
            request: Must be DispatcherAdmissionRequest (validated by verifier_core).
            current_time: Optional UTC timezone-aware datetime.
            expected_candidate_sha256: Optional expected candidate digest claim.
            admission_id: Optional explicit UUIDv4 admission identifier.
            require_operational_readiness: If True (default), fails closed on operational prerequisites.
            allow_non_operational_ledger_commit_for_testing: If True (test only), commits valid request to ledger.

        Returns:
            AdmissionCoordinationFacts detailing outcome.
        """
        # 1. Parameter Type Checks & Mode Compatibility
        if type(require_operational_readiness) is not bool:
            return AdmissionCoordinationFacts(
                is_valid=False,
                ledger_committed=False,
                status=COORDINATION_STATUS_VERIFICATION_FAILED,
                error_code="ERR_TYPE_MISMATCH",
                error_detail="require_operational_readiness must be bool",
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
                operational_admission_permitted=False,
            )
        if type(allow_non_operational_ledger_commit_for_testing) is not bool:
            return AdmissionCoordinationFacts(
                is_valid=False,
                ledger_committed=False,
                status=COORDINATION_STATUS_VERIFICATION_FAILED,
                error_code="ERR_TYPE_MISMATCH",
                error_detail="allow_non_operational_ledger_commit_for_testing must be bool",
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
                operational_admission_permitted=False,
            )

        if require_operational_readiness and allow_non_operational_ledger_commit_for_testing:
            return AdmissionCoordinationFacts(
                is_valid=False,
                ledger_committed=False,
                status=COORDINATION_STATUS_VERIFICATION_FAILED,
                error_code="ERR_INCOMPATIBLE_EXECUTION_MODE",
                error_detail="require_operational_readiness and allow_non_operational_ledger_commit_for_testing cannot both be True",
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
                operational_admission_permitted=False,
            )

        # 2. Timestamp Validation
        if current_time is None:
            tau = datetime.now(timezone.utc)
        else:
            if (
                type(current_time) is not datetime
                or current_time.tzinfo is None
                or current_time.utcoffset() != timedelta(0)
            ):
                return AdmissionCoordinationFacts(
                    is_valid=False,
                    ledger_committed=False,
                    status=COORDINATION_STATUS_VERIFICATION_FAILED,
                    error_code="ERR_INVALID_TIMESTAMP",
                    error_detail="current_time must be a timezone-aware UTC datetime",
                    verified_claims={},
                    consumed_record=None,
                    unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
                    operational_admission_permitted=False,
                )
            tau = current_time

        # 3. Optional admission_id Validation Up Front
        if admission_id is not None and (
            type(admission_id) is not str or not RE_CANONICAL_UUID_V4.fullmatch(admission_id)
        ):
            return AdmissionCoordinationFacts(
                is_valid=False,
                ledger_committed=False,
                status=COORDINATION_STATUS_VERIFICATION_FAILED,
                error_code="ERR_INVALID_UUID",
                error_detail="admission_id must be a canonical lowercase UUIDv4",
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
                operational_admission_permitted=False,
            )

        # 4. In-Memory Pre-Validation (BLOCKER-B-ADMISSION-SERVER-R1-01)
        # ZERO ledger access before verification succeeds!
        vf = self._verifier_core.verify(
            request=request,
            current_time=tau,
            expected_candidate_sha256=expected_candidate_sha256,
            require_operational_readiness=require_operational_readiness,
        )

        unresolved = vf.unresolved_prerequisites if vf.unresolved_prerequisites else ALL_UNRESOLVED_PREREQUISITES

        if not vf.is_valid:
            return AdmissionCoordinationFacts(
                is_valid=False,
                ledger_committed=False,
                status=COORDINATION_STATUS_VERIFICATION_FAILED,
                error_code=vf.error_code,
                error_detail=vf.error_detail,
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=unresolved,
                operational_admission_permitted=False,
            )

        # 5. Ledger Consumption Dispatch
        if not allow_non_operational_ledger_commit_for_testing:
            return AdmissionCoordinationFacts(
                is_valid=True,
                ledger_committed=False,
                status=COORDINATION_STATUS_VALID_LEDGER_SKIPPED,
                error_code=None,
                error_detail=None,
                verified_claims=vf.verified_claims,
                consumed_record=None,
                unresolved_prerequisites=unresolved,
                operational_admission_permitted=False,
            )

        # allow_non_operational_ledger_commit_for_testing is True
        if self._ledger is None:
            return AdmissionCoordinationFacts(
                is_valid=False,
                ledger_committed=False,
                status=COORDINATION_STATUS_LEDGER_REJECTED,
                error_code="ERR_ADMISSION_LEDGER_MISSING",
                error_detail="No DurableAdmissionLedger configured on coordinator",
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=unresolved,
                operational_admission_permitted=False,
            )

        resolved_admission_id = admission_id if admission_id is not None else str(uuid.uuid4())
        admitted_at_iso = tau.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        try:
            execution_id = vf.verified_claims.get("execution_id")
            preflight_run_id = vf.verified_claims.get("preflight_run_id")
            workorder_id = vf.verified_claims.get("workorder_id")
            if execution_id is None or preflight_run_id is None or workorder_id is None:
                raise AdmissionRecordValidationError(
                    "ERR_TYPE_MISMATCH",
                    "Missing required verified claims for ConsumedAdmissionRecord",
                )
            record = ConsumedAdmissionRecord(
                admission_id=resolved_admission_id,
                execution_id=execution_id,
                preflight_run_id=preflight_run_id,
                workorder_id=workorder_id,
                admitted_at_iso=admitted_at_iso,
            )
            self._ledger.consume_admission(record)
        except AdmissionLedgerError as exc:
            return AdmissionCoordinationFacts(
                is_valid=False,
                ledger_committed=False,
                status=COORDINATION_STATUS_LEDGER_REJECTED,
                error_code=exc.code,
                error_detail=exc.detail,
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=unresolved,
                operational_admission_permitted=False,
            )

        return AdmissionCoordinationFacts(
            is_valid=True,
            ledger_committed=True,
            status=COORDINATION_STATUS_NON_OPERATIONAL_LEDGER_COMMITTED,
            error_code=None,
            error_detail=None,
            verified_claims=vf.verified_claims,
            consumed_record=record,
            unresolved_prerequisites=unresolved,
            operational_admission_permitted=False,
        )

    def evaluate_wire_payload(
        self,
        payload_bytes: Any,
        *,
        current_time: Optional[datetime] = None,
        expected_candidate_sha256: Optional[str] = None,
        admission_id: Optional[str] = None,
        require_operational_readiness: bool = True,
        allow_non_operational_ledger_commit_for_testing: bool = False,
    ) -> AdmissionCoordinationFacts:
        """Parses and evaluates a raw wire payload with strict framing and JSON validation.

        Args:
            payload_bytes: Must be bytes or bytearray within [1, 65536].
            current_time: Optional UTC timezone-aware datetime.
            expected_candidate_sha256: Optional expected candidate digest claim.
            admission_id: Optional explicit UUIDv4 admission identifier.
            require_operational_readiness: If True (default), fails closed on operational prerequisites.
            allow_non_operational_ledger_commit_for_testing: If True (test only), commits valid request to ledger.

        Returns:
            AdmissionCoordinationFacts detailing outcome.
        """
        if not isinstance(payload_bytes, (bytes, bytearray)):
            return AdmissionCoordinationFacts(
                is_valid=False,
                ledger_committed=False,
                status=COORDINATION_STATUS_PAYLOAD_INVALID,
                error_code="ERR_INVALID_FRAME",
                error_detail=f"payload_bytes must be bytes or bytearray, got {type(payload_bytes).__name__}",
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
                operational_admission_permitted=False,
            )

        if not (MIN_PAYLOAD_SIZE <= len(payload_bytes) <= MAX_PAYLOAD_SIZE):
            return AdmissionCoordinationFacts(
                is_valid=False,
                ledger_committed=False,
                status=COORDINATION_STATUS_PAYLOAD_INVALID,
                error_code="ERR_FRAME_BOUNDS",
                error_detail=f"payload size {len(payload_bytes)} outside [{MIN_PAYLOAD_SIZE}, {MAX_PAYLOAD_SIZE}]",
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
                operational_admission_permitted=False,
            )

        try:
            req = DispatcherAdmissionRequest.from_json_bytes(bytes(payload_bytes))
        except DispatcherAdmissionContractError as exc:
            return AdmissionCoordinationFacts(
                is_valid=False,
                ledger_committed=False,
                status=COORDINATION_STATUS_PAYLOAD_INVALID,
                error_code=exc.code,
                error_detail=exc.detail,
                verified_claims={},
                consumed_record=None,
                unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
                operational_admission_permitted=False,
            )

        return self.evaluate_request(
            req,
            current_time=current_time,
            expected_candidate_sha256=expected_candidate_sha256,
            admission_id=admission_id,
            require_operational_readiness=require_operational_readiness,
            allow_non_operational_ledger_commit_for_testing=allow_non_operational_ledger_commit_for_testing,
        )
