"""Offline Reconciler Core for Signer Oracle Orphan Ledgers.

Plan 07B7 Phase 2, Blocker C, Prerequisite C-2.
Reviewable, non-operational offline reconciler core for identifying candidate
orphan records in the Signer Oracle ledger, performing deterministic
non-mutating assessment, and descriptor-relative persistence against caller-supplied
directory file descriptors.

STRICT SAFETY CONSTRAINTS & DESIGN INVARIANTS:
1. Module entry point (CLI) is unconditionally disabled and fails closed with
   ERR_C1_CHARTER_NOT_ADOPTED before parsing arguments, opening files, or making
   any state change.
2. Public persistence API (append_oracle_terminal_record_descriptor_relative) is
   unconditionally disabled and fails closed with ERR_C1_CHARTER_NOT_ADOPTED while
   Prerequisite C-1 remains open.
3. Test-only persistence backend (_test_only_append_oracle_terminal_record) is
   strictly bound to pytest temporary directories (/tmp, pytest) and refuses any
   production descriptor or non-pytest execution.
4. No path-based production defaults; no code that opens or references
   /var/lib/resolveflow, /run/resolveflow, or /etc/resolveflow.
5. Strict filename validation: only approved basenames allowed; path separators,
   slash, and directory traversal ('..') are strictly rejected before open.
6. Strict JSON parsing: duplicate object keys are rejected fail-closed with
   ERR_JSON_DUPLICATE_KEY.
7. Exact record equality for terminal idempotency: duplicate records with the same
   execution ID must match all fields (terminal_state, authorization_id, proposal_revision,
   target UIDs, digest); any divergence is rejected as ERR_TERMINAL_STATE_CONFLICT.
8. Decoupled from Dispatcher: does NOT read, write, or unlock Dispatcher target
   exclusion, WAL, target_ledger, receipts, or in-doubt tombstones.
9. Pure and deterministic assessment: no wall-clock dependencies, no TTL, no GC,
   no background daemons, no retries, no automatic rollback.
10. Descriptor-relative test persistence: caller supplies open directory descriptor.
    Enforces single-link regular files, exact modes/ownership, exclusive flock,
    on-disk state re-read under lock, fsync(file_fd), fsync(dir_fd), and fail-closed
    handling on conflict.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.contracts.trial_authorization_contracts import canonical_json_bytes
from src.services.durable_nonce_ledger import (
    ORACLE_EXECUTION_RECORD_KEYS,
    ORACLE_TERMINAL_RECORD_KEYS,
    VALID_LEDGER_TERMINAL_STATES,
    OracleExecutionRecord,
    OracleExecutionTerminalRecord,
)
from src.services.trial_authorization_signer_service import (
    SignerLedgerCorruptionError,
    SignerLedgerDurabilityError,
    SignerLedgerError,
    SignerLedgerSecurityError,
)

# ------------------------------------------------------------------------------
# Approved Basenames Allowlist
# ------------------------------------------------------------------------------

APPROVED_VERIFIED_EXECUTIONS_FILE: str = "oracle_verified_executions.jsonl"
APPROVED_EXECUTION_TERMINALS_FILE: str = "oracle_execution_terminals.jsonl"

APPROVED_LEDGER_BASENAMES: frozenset[str] = frozenset({
    APPROVED_VERIFIED_EXECUTIONS_FILE,
    APPROVED_EXECUTION_TERMINALS_FILE,
})


# ------------------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------------------

class ReconcilerError(Exception):
    """Base error for offline orphan ledger reconciler."""


class ReconcilerDisabledError(ReconcilerError):
    """Raised when the reconciler CLI or operational interface is invoked without adopted charter."""


class ReconcilerLedgerError(ReconcilerError):
    """Base error for ledger operations within the reconciler."""


class ReconcilerLedgerCorruptionError(ReconcilerLedgerError):
    """Raised on torn tails, JSON syntax errors, duplicate keys, or structural corruption."""


class ReconcilerLedgerSecurityError(ReconcilerLedgerError):
    """Raised on permission, symlink, link count, path traversal, or identity binding violations."""


class ReconcilerTerminalConflictError(ReconcilerLedgerSecurityError):
    """Raised when attempting to terminalize an execution that already has a conflicting terminal record."""


class ReconcilerDurabilityError(ReconcilerLedgerError):
    """Raised when write, fsync, or flock fails to guarantee ledger durability."""


# ------------------------------------------------------------------------------
# Enums and Incident Evidence Dataclasses
# ------------------------------------------------------------------------------

class ReconciliationDisposition(str, Enum):
    """Deterministic disposition outcome for an orphan execution record."""

    ABORTED = "ABORTED"
    IN_DOUBT = "IN_DOUBT"
    NO_ACTION = "NO_ACTION"


class CrashBoundary(str, Enum):
    """Accepted crash boundary classifications per Blocker C Design R3 §3.2."""

    BOUNDARY_1 = "BOUNDARY_1"
    BOUNDARY_2 = "BOUNDARY_2"
    BOUNDARY_3 = "BOUNDARY_3"
    BOUNDARY_4 = "BOUNDARY_4"
    BOUNDARY_5A1 = "BOUNDARY_5A1"
    BOUNDARY_5A2 = "BOUNDARY_5A2"
    BOUNDARY_5B = "BOUNDARY_5B"


@dataclass(frozen=True)
class IncidentEvidenceBundle:
    """Explicit incident evidence supplied for offline assessment.

    CRITICAL SAFETY INVARIANT:
    A plain in-memory structure does NOT prove authenticity or direct physical
    observation. Because Prerequisite C-1 remains strictly OPEN in this phase,
    all authoritative assessments fail closed with NO_ACTION.
    Non-authoritative simulated assessments can be evaluated for planning,
    but cannot authorize persistence.
    """

    evidence_id: str
    has_authority_charter: bool = False
    is_authenticated: bool = False
    crash_boundary: Optional[str] = None
    dispatcher_intent_present: Optional[bool] = None
    dispatcher_wal_action_pending: Optional[bool] = None
    physical_observation_empty: Optional[bool] = None
    physical_observation_signed: bool = False
    corroborating_evidence_valid: bool = False
    disputed: bool = False
    daemon_active: bool = False
    notes: str = ""


@dataclass(frozen=True)
class AssessmentResult:
    """Deterministic result of orphan reconciliation assessment."""

    execution_id: str
    disposition: ReconciliationDisposition
    reason_code: str
    reason_details: str
    evidence_id: Optional[str] = None
    is_authoritative: bool = False


# ------------------------------------------------------------------------------
# Strict JSON & Path Parsers
# ------------------------------------------------------------------------------

def _strict_pairs_hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """Rejects duplicate object keys fail-closed per Finding 03."""
    res: Dict[str, Any] = {}
    for k, v in pairs:
        if k in res:
            raise ReconcilerLedgerCorruptionError(
                f"ERR_JSON_DUPLICATE_KEY: duplicate object key '{k}' detected in ledger record"
            )
        res[k] = v
    return res


def parse_strict_json(line_bytes: bytes, context: str = "") -> Dict[str, Any]:
    """Parses JSON bytes with duplicate-key rejection."""
    try:
        text = line_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ReconcilerLedgerCorruptionError(f"ERR_UTF8_DECODE_ERROR in {context}: {e}") from e

    try:
        return json.loads(text, object_pairs_hook=_strict_pairs_hook)
    except ReconcilerLedgerCorruptionError:
        raise
    except Exception as e:
        raise ReconcilerLedgerCorruptionError(f"ERR_JSON_PARSE_ERROR in {context}: {e}") from e


def validate_filename_strictly(filename: str) -> str:
    """Validates filename against approved basenames and prevents path traversal (Finding 02)."""
    if not filename or not isinstance(filename, str):
        raise ReconcilerLedgerSecurityError("ERR_SECURITY_VIOLATION: filename must be a non-empty string")

    # Reject path separators, slashes, backslashes, and directory traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ReconcilerLedgerSecurityError(
            f"ERR_SECURITY_VIOLATION: filename '{filename}' contains prohibited path traversal components"
        )

    if os.path.basename(filename) != filename:
        raise ReconcilerLedgerSecurityError(
            f"ERR_SECURITY_VIOLATION: filename '{filename}' is not a pure basename"
        )

    if filename not in APPROVED_LEDGER_BASENAMES:
        raise ReconcilerLedgerSecurityError(
            f"ERR_SECURITY_VIOLATION: unapproved ledger filename '{filename}'; "
            f"must be one of {sorted(APPROVED_LEDGER_BASENAMES)}"
        )

    return filename


def is_strictly_within_approved_temporary_root(path_str: str) -> bool:
    """Verifies that path_str resolves to a path strictly beneath an approved temporary root.

    Enforces directory boundaries using Path.is_relative_to, rejecting lookalike prefixes
    (e.g. /tmp-other, /var/tmp-other) and unrelated persistent paths containing substrings
    (e.g. /home/user/pytest-data).
    """
    try:
        resolved = Path(path_str).resolve()
        resolved_str = str(resolved)

        # Categorically reject production directories
        if (
            "/var/lib/resolveflow" in resolved_str
            or "/run/resolveflow" in resolved_str
            or "/etc/resolveflow" in resolved_str
        ):
            return False

        approved_roots = [
            Path("/tmp").resolve(),
            Path("/var/tmp").resolve(),
            Path(tempfile.gettempdir()).resolve(),
        ]

        for root in approved_roots:
            try:
                # Must be relative to root and must not be the root directory itself
                if resolved.is_relative_to(root) and resolved != root:
                    return True
            except (ValueError, TypeError):
                continue

        return False
    except Exception:
        return False


# ------------------------------------------------------------------------------
# Descriptor-Relative Ledger Readers & Validators
# ------------------------------------------------------------------------------

def validate_directory_descriptor(
    dir_fd: int,
    expected_mode: Optional[int] = 0o700,
    expected_uid: Optional[int] = None,
    expected_gid: Optional[int] = None,
) -> None:
    """Validates that a caller-supplied file descriptor refers to a secure directory.

    Enforces:
    - Descriptor is open and stat-able via fstat.
    - Descriptor is a directory and not a symlink.
    - Matches expected_mode if specified (default 0o700).
    - Matches expected_uid / expected_gid if specified.
    """
    try:
        st = os.fstat(dir_fd)
    except OSError as e:
        raise ReconcilerLedgerSecurityError(f"ERR_DESCRIPTOR_INVALID: fstat failed on dir_fd: {e}") from e

    if stat.S_ISLNK(st.st_mode):
        raise ReconcilerLedgerSecurityError("ERR_SECURITY_VIOLATION: directory descriptor is a symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise ReconcilerLedgerSecurityError("ERR_SECURITY_VIOLATION: descriptor is not a directory")

    if expected_mode is not None:
        mode = stat.S_IMODE(st.st_mode)
        if mode != expected_mode:
            raise ReconcilerLedgerSecurityError(
                f"ERR_INVALID_MODE: directory descriptor mode {oct(mode)}, expected {oct(expected_mode)}"
            )

    if expected_uid is not None and st.st_uid != expected_uid:
        raise ReconcilerLedgerSecurityError(
            f"ERR_INVALID_OWNER: directory owned by UID {st.st_uid}, expected {expected_uid}"
        )
    if expected_gid is not None and st.st_gid != expected_gid:
        raise ReconcilerLedgerSecurityError(
            f"ERR_INVALID_GROUP: directory owned by GID {st.st_gid}, expected {expected_gid}"
        )


def _read_and_validate_file_content(
    dir_fd: int,
    filename: str,
    expected_file_mode: Optional[int] = 0o600,
    expected_uid: Optional[int] = None,
    expected_gid: Optional[int] = None,
) -> bytes:
    """Reads ledger content descriptor-relatively, enforcing single-link regular file checks."""
    validate_directory_descriptor(dir_fd, expected_mode=None)
    validated_filename = validate_filename_strictly(filename)

    try:
        fd = os.open(validated_filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    except FileNotFoundError:
        # File does not exist yet (treated as empty ledger)
        return b""
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ReconcilerLedgerSecurityError(f"ERR_SECURITY_VIOLATION: file '{validated_filename}' is a symlink: {e}") from e
        raise ReconcilerLedgerSecurityError(f"ERR_CANNOT_OPEN_FILE: failed to open '{validated_filename}': {e}") from e

    try:
        st = os.fstat(fd)
        if stat.S_ISLNK(st.st_mode):
            raise ReconcilerLedgerSecurityError(f"ERR_SECURITY_VIOLATION: file '{validated_filename}' is a symlink")
        if not stat.S_ISREG(st.st_mode):
            raise ReconcilerLedgerSecurityError(f"ERR_SECURITY_VIOLATION: file '{validated_filename}' is not a regular file")
        if st.st_nlink != 1:
            raise ReconcilerLedgerSecurityError(
                f"ERR_SECURITY_VIOLATION: file '{validated_filename}' has link count {st.st_nlink}, expected 1"
            )
        if expected_file_mode is not None:
            mode = stat.S_IMODE(st.st_mode)
            if mode != expected_file_mode:
                raise ReconcilerLedgerSecurityError(
                    f"ERR_INVALID_MODE: file '{validated_filename}' mode {oct(mode)}, expected {oct(expected_file_mode)}"
                )
        if expected_uid is not None and st.st_uid != expected_uid:
            raise ReconcilerLedgerSecurityError(
                f"ERR_INVALID_OWNER: file '{validated_filename}' owned by UID {st.st_uid}, expected {expected_uid}"
            )
        if expected_gid is not None and st.st_gid != expected_gid:
            raise ReconcilerLedgerSecurityError(
                f"ERR_INVALID_GROUP: file '{validated_filename}' owned by GID {st.st_gid}, expected {expected_gid}"
            )

        content = bytearray()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            content.extend(chunk)
        return bytes(content)
    finally:
        os.close(fd)


def read_verified_executions_from_dir_fd(
    dir_fd: int,
    filename: str = APPROVED_VERIFIED_EXECUTIONS_FILE,
    expected_file_mode: Optional[int] = 0o600,
    expected_uid: Optional[int] = None,
    expected_gid: Optional[int] = None,
) -> Dict[str, OracleExecutionRecord]:
    """Reads and validates oracle_verified_executions.jsonl descriptor-relatively.

    Enforces strict duplicate key rejection, schema validation, and unique execution IDs.
    """
    raw = _read_and_validate_file_content(
        dir_fd, filename, expected_file_mode=expected_file_mode, expected_uid=expected_uid, expected_gid=expected_gid
    )
    if not raw:
        return {}

    if raw[-1:] != b"\n":
        raise ReconcilerLedgerCorruptionError(
            "ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: missing trailing newline in verified executions ledger"
        )

    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]

    verified_records: Dict[str, OracleExecutionRecord] = {}
    for i, line in enumerate(lines):
        if not line:
            raise ReconcilerLedgerCorruptionError(
                f"ERR_CORRUPT_RECORD: empty line at index {i} in verified executions ledger"
            )

        data = parse_strict_json(line, context=f"verified executions line {i}")

        try:
            rec = OracleExecutionRecord.from_dict(data)
        except SignerLedgerError as e:
            raise ReconcilerLedgerCorruptionError(f"ERR_RECORD_SCHEMA_INVALID: line {i}: {e}") from e

        if rec.execution_id in verified_records:
            raise ReconcilerLedgerSecurityError(
                f"ERR_LEDGER_COLLISION: duplicate execution_id '{rec.execution_id}' in verified executions ledger"
            )
        verified_records[rec.execution_id] = rec

    return verified_records


def _parse_terminal_records_from_bytes(raw: bytes) -> Dict[str, OracleExecutionTerminalRecord]:
    """Parses terminal ledger bytes enforcing strict JSON, torn tails, and exact-equality idempotency.

    REMEDIATION FOR BLOCKER-C-RECONCILER-CORE-R2-02:
    Duplicate execution IDs must match ALL fields identically (exact canonical bytes equality)
    to qualify as idempotent replay. Any difference in terminal_state or identity bindings
    fails closed with ReconcilerTerminalConflictError.
    """
    if not raw:
        return {}

    if raw[-1:] != b"\n":
        raise ReconcilerLedgerCorruptionError(
            "ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: missing trailing newline in terminals ledger"
        )

    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]

    terminal_records: Dict[str, OracleExecutionTerminalRecord] = {}
    for i, line in enumerate(lines):
        if not line:
            raise ReconcilerLedgerCorruptionError(f"ERR_CORRUPT_RECORD: empty line at index {i} in terminals ledger")

        data = parse_strict_json(line, context=f"terminals line {i}")

        try:
            rec = OracleExecutionTerminalRecord.from_dict(data)
        except SignerLedgerError as e:
            raise ReconcilerLedgerCorruptionError(f"ERR_RECORD_SCHEMA_INVALID: terminal line {i}: {e}") from e

        if rec.execution_id in terminal_records:
            existing = terminal_records[rec.execution_id]
            # Exact equality check: all fields must match
            if (
                existing.terminal_state == rec.terminal_state
                and existing.authorization_id == rec.authorization_id
                and existing.proposal_revision == rec.proposal_revision
                and existing.target_project_uid == rec.target_project_uid
                and existing.target_timeline_uid == rec.target_timeline_uid
                and existing.action_payload_digest_sha256 == rec.action_payload_digest_sha256
                and existing.schema_version == rec.schema_version
            ):
                # Exact identical replay
                continue
            raise ReconcilerTerminalConflictError(
                f"ERR_TERMINAL_STATE_CONFLICT: execution_id '{rec.execution_id}' has conflicting terminal records: "
                f"existing={existing.to_dict()}, candidate={rec.to_dict()}"
            )

        terminal_records[rec.execution_id] = rec

    return terminal_records


def read_terminal_records_from_dir_fd(
    dir_fd: int,
    filename: str = APPROVED_EXECUTION_TERMINALS_FILE,
    expected_file_mode: Optional[int] = 0o600,
    expected_uid: Optional[int] = None,
    expected_gid: Optional[int] = None,
) -> Dict[str, OracleExecutionTerminalRecord]:
    """Reads and validates oracle_execution_terminals.jsonl descriptor-relatively."""
    raw = _read_and_validate_file_content(
        dir_fd, filename, expected_file_mode=expected_file_mode, expected_uid=expected_uid, expected_gid=expected_gid
    )
    return _parse_terminal_records_from_bytes(raw)


def validate_terminal_bindings(
    verified_records: Dict[str, OracleExecutionRecord],
    terminal_records: Dict[str, OracleExecutionTerminalRecord],
) -> None:
    """Validates that every terminal record binds strictly to an existing verified record."""
    for exec_id, term_rec in terminal_records.items():
        if exec_id not in verified_records:
            raise ReconcilerLedgerCorruptionError(
                f"ERR_ORACLE_TERMINAL_UNVERIFIED: terminal record '{exec_id}' has no matching verified execution record"
            )
        ver_rec = verified_records[exec_id]
        if (
            ver_rec.authorization_id != term_rec.authorization_id
            or ver_rec.proposal_revision != term_rec.proposal_revision
            or ver_rec.target_project_uid != term_rec.target_project_uid
            or ver_rec.target_timeline_uid != term_rec.target_timeline_uid
        ):
            raise ReconcilerLedgerCorruptionError(
                f"ERR_ORACLE_TERMINAL_IDENTITY_MISMATCH: terminal record '{exec_id}' does not match "
                f"verified execution identity (auth={ver_rec.authorization_id}/{term_rec.authorization_id}, "
                f"rev={ver_rec.proposal_revision}/{term_rec.proposal_revision}, "
                f"proj={ver_rec.target_project_uid}/{term_rec.target_project_uid}, "
                f"tl={ver_rec.target_timeline_uid}/{term_rec.target_timeline_uid})"
            )


# ------------------------------------------------------------------------------
# Candidate Orphan Identification
# ------------------------------------------------------------------------------

def identify_orphans(
    verified_records: Dict[str, OracleExecutionRecord],
    terminal_records: Dict[str, OracleExecutionTerminalRecord],
) -> List[OracleExecutionRecord]:
    """Identifies candidate orphan records by execution ID only."""
    orphans: List[OracleExecutionRecord] = []
    for exec_id in sorted(verified_records.keys()):
        if exec_id not in terminal_records:
            orphans.append(verified_records[exec_id])
    return orphans


# ------------------------------------------------------------------------------
# Deterministic Non-Mutating Reconciliation Assessment Core
# ------------------------------------------------------------------------------

def assess_orphan_reconciliation(
    orphan: OracleExecutionRecord,
    evidence: Optional[IncidentEvidenceBundle] = None,
    allow_simulation: bool = False,
) -> AssessmentResult:
    """Produces a deterministic, non-mutating reconciliation assessment for an orphan.

    - Authoritative Mode (default, allow_simulation=False):
      Because Prerequisite C-1 remains STRICTLY OPEN (outcome
      NO_DECISION_RECORDED_PENDING_FULL_QUORUM), no operational disposition can be
      issued. Always returns NO_ACTION with ERR_C1_CHARTER_NOT_ADOPTED and
      is_authoritative=False.
    - Simulation Mode (allow_simulation=True):
      Permits dry-run evaluation of incident evidence against the accepted §3.2
      crash matrix for diagnostic and planning purposes only.
      The output is explicitly marked with is_authoritative=False and cannot
      feed the persistence API.
    """
    # 1. Authoritative Mode Enforcement
    if not allow_simulation:
        return AssessmentResult(
            execution_id=orphan.execution_id,
            disposition=ReconciliationDisposition.NO_ACTION,
            reason_code="ERR_C1_CHARTER_NOT_ADOPTED",
            reason_details="Administrative reconciliation authority charter has not been formally adopted; authoritative reconciliation is strictly prohibited",
            evidence_id=evidence.evidence_id if evidence else None,
            is_authoritative=False,
        )

    # 2. Simulation Mode Evaluation (Dry-run only)
    if evidence is None:
        return AssessmentResult(
            execution_id=orphan.execution_id,
            disposition=ReconciliationDisposition.NO_ACTION,
            reason_code="ERR_EVIDENCE_ABSENT",
            reason_details="No incident evidence supplied for simulation assessment",
            evidence_id=None,
            is_authoritative=False,
        )

    if not evidence.has_authority_charter:
        return AssessmentResult(
            execution_id=orphan.execution_id,
            disposition=ReconciliationDisposition.NO_ACTION,
            reason_code="ERR_C1_CHARTER_NOT_ADOPTED",
            reason_details="Simulation input lacks asserted authority charter",
            evidence_id=evidence.evidence_id,
            is_authoritative=False,
        )

    if not evidence.is_authenticated:
        return AssessmentResult(
            execution_id=orphan.execution_id,
            disposition=ReconciliationDisposition.NO_ACTION,
            reason_code="ERR_EVIDENCE_UNAUTHENTICATED",
            reason_details="Simulation input lacks asserted cryptographic authentication",
            evidence_id=evidence.evidence_id,
            is_authoritative=False,
        )

    if evidence.daemon_active:
        return AssessmentResult(
            execution_id=orphan.execution_id,
            disposition=ReconciliationDisposition.NO_ACTION,
            reason_code="ERR_DAEMON_ACTIVE",
            reason_details="Active daemon detected; reconciliation requires stopped daemons",
            evidence_id=evidence.evidence_id,
            is_authoritative=False,
        )

    if evidence.disputed:
        return AssessmentResult(
            execution_id=orphan.execution_id,
            disposition=ReconciliationDisposition.NO_ACTION,
            reason_code="ERR_EVIDENCE_DISPUTED",
            reason_details="Supplied incident evidence is marked disputed; requires forensic escalation",
            evidence_id=evidence.evidence_id,
            is_authoritative=False,
        )

    # Prohibit ABORTED if WAL action pending was true
    if evidence.dispatcher_wal_action_pending is True:
        return AssessmentResult(
            execution_id=orphan.execution_id,
            disposition=ReconciliationDisposition.IN_DOUBT,
            reason_code="MUTATION_AMBIGUOUS_ACTION_PENDING",
            reason_details="Dispatcher journal contains ACTION_PENDING; mutation outcome cannot be proven zero, assigned IN_DOUBT",
            evidence_id=evidence.evidence_id,
            is_authoritative=False,
        )

    # Boundary 1: Crash after Oracle fsync, before Dispatcher intent fsync
    if (
        evidence.crash_boundary == CrashBoundary.BOUNDARY_1.value
        and evidence.dispatcher_intent_present is False
        and evidence.dispatcher_wal_action_pending is False
        and evidence.corroborating_evidence_valid is True
    ):
        return AssessmentResult(
            execution_id=orphan.execution_id,
            disposition=ReconciliationDisposition.ABORTED,
            reason_code="ZERO_MUTATION_PROVEN_PRE_INTENT",
            reason_details="Conclusive simulated evidence proves Dispatcher never recorded intent (Boundary 1); simulated disposition ABORTED",
            evidence_id=evidence.evidence_id,
            is_authoritative=False,
        )

    # Boundary 2: Crash after RESERVED, before WAL ACTION_PENDING
    if (
        evidence.crash_boundary == CrashBoundary.BOUNDARY_2.value
        and evidence.dispatcher_intent_present is True
        and evidence.dispatcher_wal_action_pending is False
        and evidence.corroborating_evidence_valid is True
    ):
        if evidence.physical_observation_empty is True and evidence.physical_observation_signed is True:
            return AssessmentResult(
                execution_id=orphan.execution_id,
                disposition=ReconciliationDisposition.ABORTED,
                reason_code="ZERO_MUTATION_PROVEN_PRE_ACTION_PHYSICAL_CONFIRMED",
                reason_details="Conclusive simulated evidence proves zero WAL action pending and signed physical attestation confirms empty timeline (Boundary 2); simulated disposition ABORTED",
                evidence_id=evidence.evidence_id,
                is_authoritative=False,
            )
        else:
            return AssessmentResult(
                execution_id=orphan.execution_id,
                disposition=ReconciliationDisposition.IN_DOUBT,
                reason_code="PHYSICAL_ATTESTATION_MISSING_BOUNDARY_2",
                reason_details="Boundary 2 requires signed physical observation of empty timeline to prove zero mutation; assigned IN_DOUBT",
                evidence_id=evidence.evidence_id,
                is_authoritative=False,
            )

    # Boundary 3, 4, 5B: Ambiguous mutation outcomes -> IN_DOUBT
    if evidence.crash_boundary in (
        CrashBoundary.BOUNDARY_3.value,
        CrashBoundary.BOUNDARY_4.value,
        CrashBoundary.BOUNDARY_5B.value,
    ):
        return AssessmentResult(
            execution_id=orphan.execution_id,
            disposition=ReconciliationDisposition.IN_DOUBT,
            reason_code="MUTATION_AMBIGUOUS_CRASH_BOUNDARY",
            reason_details=f"Crash boundary '{evidence.crash_boundary}' represents ambiguous mutation; simulated disposition IN_DOUBT",
            evidence_id=evidence.evidence_id,
            is_authoritative=False,
        )

    if evidence.physical_observation_empty is False:
        return AssessmentResult(
            execution_id=orphan.execution_id,
            disposition=ReconciliationDisposition.IN_DOUBT,
            reason_code="TARGET_MUTATED_OR_NON_EMPTY",
            reason_details="Physical observation confirms target timeline is not empty; cannot abort, assigned IN_DOUBT",
            evidence_id=evidence.evidence_id,
            is_authoritative=False,
        )

    return AssessmentResult(
        execution_id=orphan.execution_id,
        disposition=ReconciliationDisposition.NO_ACTION,
        reason_code="ERR_INSUFFICIENT_EVIDENCE",
        reason_details="Evidence bundle does not meet deterministic criteria for disposition assignment; fail-closed to NO_ACTION",
        evidence_id=evidence.evidence_id,
        is_authoritative=False,
    )


# ------------------------------------------------------------------------------
# Public Persistence API (Unconditionally Disabled While C-1 is Open)
# ------------------------------------------------------------------------------

def append_oracle_terminal_record_descriptor_relative(
    dir_fd: int,
    record: OracleExecutionTerminalRecord,
    verified_records: Dict[str, OracleExecutionRecord],
    existing_terminal_records: Optional[Dict[str, OracleExecutionTerminalRecord]] = None,
    filename: str = APPROVED_EXECUTION_TERMINALS_FILE,
    expected_dir_mode: Optional[int] = 0o700,
    expected_file_mode: Optional[int] = 0o600,
    expected_uid: Optional[int] = None,
    expected_gid: Optional[int] = None,
) -> bool:
    """Public persistence API for committing Oracle execution terminal records.

    REMEDIATION FOR BLOCKER-C-RECONCILER-CORE-R2-01:
    Unconditionally disabled while Prerequisite C-1 remains open.
    Fails closed with ERR_C1_CHARTER_NOT_ADOPTED. No public caller can mutate
    ledgers. Operational activation requires a future approved workorder.
    """
    raise ReconcilerDisabledError(
        "ERR_C1_CHARTER_NOT_ADOPTED: administrative reconciliation authority charter "
        "is strictly open; public persistence API execution is unconditionally prohibited"
    )


# ------------------------------------------------------------------------------
# Isolated Test-Only Persistence Backend (Pytest Temporary Directory Bound)
# ------------------------------------------------------------------------------

def _test_only_append_oracle_terminal_record(
    dir_fd: int,
    record: OracleExecutionTerminalRecord,
    verified_records: Dict[str, OracleExecutionRecord],
    existing_terminal_records: Optional[Dict[str, OracleExecutionTerminalRecord]] = None,
    filename: str = APPROVED_EXECUTION_TERMINALS_FILE,
    expected_dir_mode: Optional[int] = 0o700,
    expected_file_mode: Optional[int] = 0o600,
    expected_uid: Optional[int] = None,
    expected_gid: Optional[int] = None,
) -> bool:
    """Internal test-only storage backend strictly bound to pytest temporary directories.

    REMEDIATION FOR BLOCKER-C-RECONCILER-CORE-R2-01 & R2-02:
    1. Test-Environment Binding: Refuses execution unless running under active pytest.
    2. Path Invariant Verification: Resolves descriptor via /proc/self/fd on Linux.
       Rejects production paths (/var/lib/resolveflow, /run/resolveflow, /etc/resolveflow).
       Enforces that dir_fd is rooted inside a temporary directory (/tmp, /var/tmp, pytest).
    3. Serialized Exclusivity: Acquires fcntl.flock(fd, LOCK_EX | LOCK_NB) immediately.
    4. On-Disk Exact-Record Re-read Under Lock:
       Re-reads on-disk state. Duplicate execution IDs must match ALL record fields
       identically (exact equality) for idempotent replay; any divergence raises
       ReconcilerTerminalConflictError.
    5. Single-link regular file validation, fsync(file_fd), and fsync(dir_fd) durability.
    """
    # 1. Test Environment Verification
    if "PYTEST_CURRENT_TEST" not in os.environ and "pytest" not in sys.modules:
        raise ReconcilerDisabledError(
            "ERR_NOT_IN_TEST_ENVIRONMENT: test-only persistence backend can only be called from pytest"
        )

    # 2. Verify dir_fd is strictly inside an isolated temporary directory (Finding R4-01 Boundary Check)
    try:
        proc_path = os.readlink(f"/proc/self/fd/{dir_fd}")
    except OSError as e:
        raise ReconcilerLedgerSecurityError(
            f"ERR_SECURITY_VIOLATION: unable to resolve directory descriptor path via /proc/self/fd/{dir_fd}: {e}; "
            "test backend strictly requires verifiable descriptor path confinement"
        ) from e

    if (
        "/var/lib/resolveflow" in proc_path
        or "/run/resolveflow" in proc_path
        or "/etc/resolveflow" in proc_path
    ):
        raise ReconcilerLedgerSecurityError(
            f"ERR_SECURITY_VIOLATION: production path '{proc_path}' strictly prohibited in test backend"
        )

    if not is_strictly_within_approved_temporary_root(proc_path):
        raise ReconcilerLedgerSecurityError(
            f"ERR_SECURITY_VIOLATION: descriptor path '{proc_path}' is not strictly within an approved temporary root"
        )

    # 3. Descriptor & Filename Security Validation
    validate_directory_descriptor(
        dir_fd, expected_mode=expected_dir_mode, expected_uid=expected_uid, expected_gid=expected_gid
    )
    validated_filename = validate_filename_strictly(filename)

    # 4. Verified Record Identity Binding Check
    if record.execution_id not in verified_records:
        raise ReconcilerLedgerSecurityError(
            f"ERR_EXECUTION_NOT_FOUND: execution_id '{record.execution_id}' has no verified execution record"
        )

    ver_rec = verified_records[record.execution_id]
    if (
        ver_rec.authorization_id != record.authorization_id
        or ver_rec.proposal_revision != record.proposal_revision
        or ver_rec.target_project_uid != record.target_project_uid
        or ver_rec.target_timeline_uid != record.target_timeline_uid
    ):
        raise ReconcilerLedgerSecurityError(
            f"ERR_IDENTITY_BINDING_MISMATCH: terminal record does not match verified execution for '{record.execution_id}'"
        )

    # 5. Open terminal file descriptor-relatively
    try:
        fd = os.open(
            validated_filename,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            expected_file_mode if expected_file_mode is not None else 0o600,
            dir_fd=dir_fd,
        )
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ReconcilerLedgerSecurityError(f"ERR_SECURITY_VIOLATION: file '{validated_filename}' is a symlink: {e}") from e
        raise ReconcilerLedgerSecurityError(f"ERR_CANNOT_OPEN_FILE: failed to open '{validated_filename}': {e}") from e

    try:
        # 6. Acquire Exclusive File Lock
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as e:
            raise ReconcilerDurabilityError(
                f"ERR_LOCK_CONTENTION: failed to acquire exclusive lock on '{validated_filename}': {e}"
            ) from e

        # Validate file properties under lock
        st = os.fstat(fd)
        if stat.S_ISLNK(st.st_mode):
            raise ReconcilerLedgerSecurityError(f"ERR_SECURITY_VIOLATION: file '{validated_filename}' is a symlink")
        if not stat.S_ISREG(st.st_mode):
            raise ReconcilerLedgerSecurityError(f"ERR_SECURITY_VIOLATION: file '{validated_filename}' is not a regular file")
        if st.st_nlink != 1:
            raise ReconcilerLedgerSecurityError(
                f"ERR_SECURITY_VIOLATION: file '{validated_filename}' has link count {st.st_nlink}, expected 1"
            )
        if expected_file_mode is not None:
            mode = stat.S_IMODE(st.st_mode)
            if mode != expected_file_mode:
                raise ReconcilerLedgerSecurityError(
                    f"ERR_INVALID_MODE: file '{validated_filename}' mode {oct(mode)}, expected {oct(expected_file_mode)}"
                )
        if expected_uid is not None and st.st_uid != expected_uid:
            raise ReconcilerLedgerSecurityError(
                f"ERR_INVALID_OWNER: file '{validated_filename}' owned by UID {st.st_uid}, expected {expected_uid}"
            )
        if expected_gid is not None and st.st_gid != expected_gid:
            raise ReconcilerLedgerSecurityError(
                f"ERR_INVALID_GROUP: file '{validated_filename}' owned by GID {st.st_gid}, expected {expected_gid}"
            )

        # 7. Re-read on-disk terminal records under exclusive lock (Exact Record Equality)
        os.lseek(fd, 0, os.SEEK_SET)
        raw_on_disk = bytearray()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw_on_disk.extend(chunk)

        on_disk_terminals = _parse_terminal_records_from_bytes(bytes(raw_on_disk))

        # Check for idempotent replay vs conflicting terminal disposition
        if record.execution_id in on_disk_terminals:
            existing = on_disk_terminals[record.execution_id]
            # Exact record equality: all fields must match
            if (
                existing.terminal_state == record.terminal_state
                and existing.authorization_id == record.authorization_id
                and existing.proposal_revision == record.proposal_revision
                and existing.target_project_uid == record.target_project_uid
                and existing.target_timeline_uid == record.target_timeline_uid
                and existing.action_payload_digest_sha256 == record.action_payload_digest_sha256
                and existing.schema_version == record.schema_version
            ):
                # Idempotent replay: exact identical record already committed
                if existing_terminal_records is not None:
                    existing_terminal_records[record.execution_id] = existing
                return False
            raise ReconcilerTerminalConflictError(
                f"ERR_TERMINAL_STATE_CONFLICT: execution_id '{record.execution_id}' already has conflicting "
                f"terminal record on disk: existing={existing.to_dict()}, candidate={record.to_dict()}"
            )

        # 8. Append canonical record at EOF
        os.lseek(fd, 0, os.SEEK_END)
        payload_line = record.canonical_bytes() + b"\n"

        written = 0
        while written < len(payload_line):
            try:
                n = os.write(fd, payload_line[written:])
                if n <= 0:
                    raise ReconcilerDurabilityError("ERR_SHORT_WRITE: os.write returned 0")
                written += n
            except InterruptedError:
                continue
            except OSError as e:
                raise ReconcilerDurabilityError(f"ERR_LEDGER_WRITE_FAILED: {e}") from e

        # 9. Durability Ordering: fsync(file_fd) followed by fsync(dir_fd)
        try:
            os.fsync(fd)
            os.fsync(dir_fd)
        except OSError as e:
            raise ReconcilerDurabilityError(f"ERR_LEDGER_FSYNC_FAILED: {e}") from e

        # Update in-memory map if provided
        if existing_terminal_records is not None:
            existing_terminal_records[record.execution_id] = record
        return True

    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)


# ------------------------------------------------------------------------------
# Disabled CLI Entry Point
# ------------------------------------------------------------------------------

def main() -> None:
    """Unconditionally disabled CLI entry point for offline orphan ledger reconciler.

    Stops fail-closed with ERR_C1_CHARTER_NOT_ADOPTED before parsing any arguments,
    opening any files, or performing any state change.
    No CLI option or flag can bypass this stop.
    """
    sys.stderr.write(
        "ERR_C1_CHARTER_NOT_ADOPTED: administrative reconciliation authority charter "
        "has not been adopted (Prerequisite C-1 strictly open); offline reconciler CLI "
        "execution is unconditionally prohibited.\n"
    )
    raise ReconcilerDisabledError(
        "ERR_C1_CHARTER_NOT_ADOPTED: administrative reconciliation authority charter "
        "has not been adopted; offline reconciler CLI execution is unconditionally prohibited"
    )


if __name__ == "__main__":
    main()
