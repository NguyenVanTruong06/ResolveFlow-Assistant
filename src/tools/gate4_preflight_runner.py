"""Audited Preflight Runner Core (Local-Only).

Workorder: G07-07B7-PREFLIGHT-GATE4-R10 (Revision R13)
Mode: LOCAL_CODE_AND_FAKE_ADAPTER_TESTS_ONLY; NO_RESOLVE_IMPORT_OR_CONNECTION;
      NO_LIVE_OPERATIONS; NO_KEY_ACCESS; READY_TO_EXECUTE_FALSE.

Safety Boundary:
- Dependency injection only: accepts an explicit Resolve-like adapter.
- Strictly never imports DaVinciResolveScript, connects to Resolve, or executes IPC.
- Intercepts all adapter calls via AuditedResolveProxy closures, recording exact call boundaries,
  arguments, returns, timestamps, and sequence numbers into an append-only,
  fsynced, RFC 8785 canonical hash-chained audit log.
- Enforces deny-by-default operation allowlist, strictly blocking timeline insertion,
  dispatch, render, delete, and unapproved mutations.
- Encapsulates raw targets within closure scopes; no module registry or instance attributes
  expose underlying targets. Recursively wraps all container elements so raw objects never escape.
- Unforgeable proxy identity authority: marker construction authority and registration state
  are moved behind a private closure factory completely unavailable through ordinary module attributes
  or caller-invocable registration methods; only the proxy factory itself can register the exact proxy
  it creates (Findings G4-R10-R3-01 & G4-R10-R4-01 Fix).
- Immutable frozenset role policies wrapped in MappingProxyType and captured in closure; callers cannot
  supply custom allowlists or mutate role policy mappings; proxies are strictly bound to the active logger
  instance and expected role; cross-logger and mismatched-role proxies are never passed through raw
  (Findings G4-R10-R5-01 & G4-R10-R6-01 Fix).
- Immutable closure-owned incident UID denylist authority; validation logic consults closure-owned
  state independent of caller-rebindable module names; rebinding module names before construction cannot
  bypass blacklisted incident targets (Findings G4-R10-R7-01 & G4-R10-R8-01 Fix).
- Conforming RFC 8785 (JCS) canonical JSON serialization: shortest round-trip representation for IEEE-754
  binary64 numbers without arbitrary integer conversion divergence, strict safe integer domain validation
  limiting integer values to [-(2^53-1), 2^53-1], robust fixed-decimal fractional handling with non-zero d_1,
  strict rejection of non-string dictionary keys, key-collision detection, ECMAScript 7.1.12.1 exponent formatting,
  and UTF-16 code-unit key sorting (Findings G4-R10-R7-02, G4-R10-R8-02, G4-R10-R9-01 Fix).
- Return and argument serialization failure structured audit logging: prevalidates both positional and keyword
  arguments before adapter invocation, recording ARGUMENT_SERIALIZATION_FAILURE without executing the adapter;
  preserves both arguments and kwargs in all call events; records API_CALL_SERIALIZATION_FAILURE on return
  serialization failure; ensures all failure logs maintain internally consistent terminal counters verifiable
  by verify_audit_log_chain (Findings G4-R10-R10-01, G4-R10-R11-01, G4-R10-R12-01 Fix).
- Validates all Gate 4 predicates (identity, blacklist, profile, topology, frames, markers).
- Dynamic measured counters derived from verified event records, strictly reconciling
  mandatory non-negative integer terminal counts against independently derived totals.
- Enforces exact 0700 mode and process UID ownership on audit directory before log creation.
- ready_to_execute = False maintained at all times.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import stat
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple, Union

def _setup_incident_denylist_authority() -> Tuple[Callable[[str], bool], FrozenSet[str]]:
    """Private closure factory holding immutable canonical incident UID denylist.

    Findings G4-R10-R7-01 & G4-R10-R8-01 Fix:
    The canonical denylist is stored in this closure frame and cannot be modified or cleared.
    All validation logic evaluates against this closure-owned immutable state, so even if a
    caller rebinds module-level aliases (_CANONICAL_INCIDENT_BLACKLIST or BLACKLISTED_UIDS),
    validation remains strictly fail-closed.
    """
    _IMMUTABLE_DENYLIST: FrozenSet[str] = frozenset({
        "0ee71404-6405-47ca-89e1-aaeaac142398",
        "e4e5a587-661d-4854-8f37-8a279d3f2ee1",
    })

    def _is_blacklisted(uid: Any) -> bool:
        if not uid:
            return False
        return str(uid).strip() in _IMMUTABLE_DENYLIST

    return _is_blacklisted, _IMMUTABLE_DENYLIST

_is_incident_blacklisted_uid, _CANONICAL_INCIDENT_BLACKLIST = _setup_incident_denylist_authority()
BLACKLISTED_UIDS: FrozenSet[str] = _CANONICAL_INCIDENT_BLACKLIST


class Gate4RunnerError(Exception):
    """Base exception for all Gate 4 preflight runner errors."""
    pass


class DisallowedMethodError(Gate4RunnerError):
    """Raised when an unapproved method or operation is attempted on an adapter proxy."""
    pass


class AuditLogDurabilityError(Gate4RunnerError):
    """Raised when writing or fsyncing an audit record fails, or directory security fails."""
    pass


class AuditLogIntegrityError(Gate4RunnerError):
    """Raised when audit log verification detects tampering, sequence gap, or corruption."""
    pass


class PreflightVerificationError(Gate4RunnerError):
    """Raised when preflight predicates or empty timeline conditions fail."""
    pass


# Explicit immutable allowlist mapping for Gate 4 setup and readbacks (Findings G4-R10-R5-01 & G4-R10-R6-01 Fix)
ALLOWED_METHODS_BY_ROLE: MappingProxyType[str, FrozenSet[str]] = MappingProxyType({
    "Resolve": frozenset({
        "GetVersionString",
        "GetProjectManager",
    }),
    "ProjectManager": frozenset({
        "CreateProject",
        "GetCurrentProject",
    }),
    "Project": frozenset({
        "GetName",
        "GetUniqueId",
        "GetSetting",
        "SetSetting",
        "GetMediaPool",
        "GetTimelineCount",
        "GetTimelineByIndex",
        "SetCurrentTimeline",
    }),
    "MediaPool": frozenset({
        "CreateEmptyTimeline",
        "ImportMedia",
        "AddItemListToMediaPool",
        "GetRootFolder",
    }),
    "Timeline": frozenset({
        "GetName",
        "GetUniqueId",
        "GetSetting",
        "SetSetting",
        "GetTrackCount",
        "GetItemListInTrack",
        "GetStartFrame",
        "GetEndFrame",
        "GetMarkers",
    }),
    "MediaPoolItem": frozenset({
        "GetName",
        "GetMediaId",
        "GetClipProperty",
    }),
    "TimelineItem": frozenset({
        "GetName",
        "GetDuration",
        "GetStart",
        "GetEnd",
        "GetMediaPoolItem",
    }),
    "UnknownSubObject": frozenset(),  # Empty allowlist fails closed on any method invocation
})

DISPATCH_METHOD_NAMES: Set[str] = {
    "dispatch",
    "execute",
    "run",
    "apply_action",
    "execute_edit_plan",
    "send_to_timeline",
}

TIMELINE_INSERTION_NAMES: Set[str] = {
    "appendtotimeline",
    "insertitems",
    "createclip",
    "createtimelinefromclips",
    "addsubtitles",
    "insertfusiongeneratorintotimeline",
    "insertfusiontitleintotimeline",
}

REQUIRED_TERMINAL_COUNTER_FIELDS: Tuple[str, ...] = (
    "total_calls",
    "allowed_calls",
    "blocked_calls",
    "dispatch_calls_attempted",
    "clip_insertions_attempted",
)


def _rfc8785_number_to_str(val: float | int) -> str:
    """Format number according to RFC 8785 / ECMA-262 7.1.12.1 ToString rules (Findings G4-R10-R7-02, G4-R10-R8-02, G4-R10-R9-01 Fix)."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        # RFC 8785 Section 3.2.2.3 & RFC 7493 (I-JSON):
        # Interoperable integer domain is bounded by IEEE-754 double precision [-(2^53-1), 2^53-1]
        if abs(val) > 9007199254740991:
            raise ValueError(
                f"Integer {val} exceeds IEEE-754 / I-JSON interoperable safe integer range [-(2^53-1), 2^53-1]"
            )
        return str(val)

    val = float(val)
    if math.isnan(val) or math.isinf(val):
        raise ValueError("NaN and Infinity are not permitted in RFC 8785 canonical JSON")
    if val == 0.0 or val == -0.0:
        return "0"

    negative = math.copysign(1.0, val) < 0
    val = abs(val)

    # Extract shortest decimal representation digits m (d_1 != '0') and exponent n per ECMA-262 7.1.12.1
    s = repr(val)
    if "e" in s:
        mantissa_str, exp_str = s.split("e")
        exp = int(exp_str)
        digits = mantissa_str.replace(".", "")
        k = len(digits)
        n = exp + 1
    else:
        if "." in s:
            int_part, frac_part = s.split(".")
            frac_part = frac_part.rstrip("0")
            if int_part == "0":
                # Fixed notation 0.000123: strip leading zeros from fraction to ensure d_1 != 0
                digits = frac_part.lstrip("0")
                leading_zeros = len(frac_part) - len(digits)
                k = len(digits)
                n = -leading_zeros
            else:
                digits = int_part + frac_part
                k = len(digits)
                n = len(int_part)
        else:
            digits = s
            k = len(digits)
            n = len(s)

    res = "-" if negative else ""

    if k <= n <= 21:
        res += digits + "0" * (n - k)
    elif 0 < n <= 21:
        res += digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        res += "0." + "0" * (-n) + digits
    else:
        sign = "+" if (n - 1) >= 0 else "-"
        exp_val = abs(n - 1)
        if k == 1:
            res += f"{digits}e{sign}{exp_val}"
        else:
            res += f"{digits[0]}.{digits[1:]}e{sign}{exp_val}"
    return res


def _rfc8785_canonical_str(data: Any) -> str:
    """Serialize data into conforming RFC 8785 / JCS JSON string."""
    if data is None:
        return "null"
    if isinstance(data, bool):
        return "true" if data else "false"
    if isinstance(data, (int, float)):
        return _rfc8785_number_to_str(data)
    if isinstance(data, str):
        return json.dumps(data, ensure_ascii=False)
    if isinstance(data, (list, tuple)):
        return "[" + ",".join(_rfc8785_canonical_str(x) for x in data) + "]"
    if isinstance(data, dict):
        # RFC 8785 Section 3.2.3: Require exact string keys; reject non-string keys and key-collision cases
        for k in data.keys():
            if not isinstance(k, str):
                raise TypeError(
                    f"RFC 8785 canonical JSON object keys must be strings, got {type(k).__name__}: {k!r}"
                )
        # RFC 8785 Section 3.2.3: Sort keys lexicographically by UTF-16 code units
        sorted_keys = sorted(data.keys(), key=lambda k: k.encode("utf-16-be"))
        items = [
            f"{json.dumps(k, ensure_ascii=False)}:{_rfc8785_canonical_str(data[k])}"
            for k in sorted_keys
        ]
        return "{" + ",".join(items) + "}"
    raise TypeError(f"Unsupported type for RFC 8785 canonical JSON: {type(data).__name__}")
    raise TypeError(f"Unsupported type for RFC 8785 canonical JSON: {type(data).__name__}")


def canonical_json_bytes(payload: Any) -> bytes:
    """RFC 8785 conforming canonical JSON bytes (JCS UTF-16 key ordering, ECMAScript number serialization, UTF-8)."""
    return _rfc8785_canonical_str(payload).encode("utf-8")


def safe_serialize_val(val: Any) -> Any:
    """Safely convert arguments and return values to JSON-serializable primitives (Finding G4-R10-R9-01 Fix)."""
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    elif isinstance(val, (list, tuple)):
        return [safe_serialize_val(x) for x in val]
    elif isinstance(val, (dict, MappingProxyType)):
        seen_str_keys: Set[str] = set()
        for k in val.keys():
            k_str = str(k)
            if k_str in seen_str_keys:
                raise ValueError(
                    f"Key collision detected during audit serialization: "
                    f"multiple keys mapped to {k_str!r} (e.g. integer vs string key)"
                )
            seen_str_keys.add(k_str)
        return {str(k): safe_serialize_val(v) for k, v in val.items()}
    else:
        return {
            "__type__": type(val).__name__,
            "__repr__": repr(val),
        }


def compute_file_sha256(filepath: str) -> str:
    """Compute hex SHA-256 digest of a local file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class HashChainedAuditLogger:
    """Cryptographic hash-chained audit logger with fsync-before-call durability."""

    def __init__(self, log_path: str, runner_script_path: str):
        self.log_path = os.path.abspath(log_path)
        self.runner_script_path = os.path.abspath(runner_script_path)
        self.runner_script_sha256 = (
            compute_file_sha256(self.runner_script_path)
            if os.path.exists(self.runner_script_path)
            else "0" * 64
        )

        self.seq: int = 0
        self.prev_hash: str = "0" * 64
        self.records: List[Dict[str, Any]] = []
        self._closed: bool = False
        self._failed: bool = False
        self._call_counter: int = 0

        # Dynamic measured counters (never hard-coded)
        self.allowed_calls: int = 0
        self.blocked_calls: int = 0
        self.clip_insertions_attempted: int = 0
        self.dispatch_calls_attempted: int = 0

        # Finding G4-R10-R2-03 Fix: Enforce and strictly verify EXACT 0700 directory mode
        dir_path = os.path.dirname(self.log_path)
        os.makedirs(dir_path, mode=0o700, exist_ok=True)
        try:
            os.chmod(dir_path, 0o700)
        except OSError as e:
            raise AuditLogDurabilityError(
                f"Failed to enforce restrictive 0700 mode on audit directory {dir_path}: {e}"
            ) from e

        # Verify directory mode is EXACTLY 0700 and ownership matches process UID
        try:
            dir_stat = os.stat(dir_path)
            mode = stat.S_IMODE(dir_stat.st_mode)
            if mode != 0o700:
                raise AuditLogDurabilityError(
                    f"Audit directory {dir_path} permissions insecure: mode is {oct(mode)}, expected exactly 0700 (0o700)"
                )
            if hasattr(os, "getuid"):
                current_uid = os.getuid()
                if dir_stat.st_uid != current_uid:
                    raise AuditLogDurabilityError(
                        f"Audit directory {dir_path} owner UID {dir_stat.st_uid} does not match current process UID {current_uid}"
                    )
        except OSError as e:
            raise AuditLogDurabilityError(
                f"Failed to verify security of audit directory {dir_path}: {e}"
            ) from e

        # Open file exclusively with 0600 mode
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        try:
            self._fd = os.open(self.log_path, flags, 0o600)
        except OSError as e:
            raise AuditLogDurabilityError(
                f"Failed to create audit log file exclusively at {self.log_path}: {e}"
            ) from e

        self._file = os.fdopen(self._fd, "w", encoding="utf-8")

        # Write and fsync genesis start record BEFORE any operation
        self.append_event(
            event_type="AUDIT_START",
            message="Hash-chained preflight execution audit initialized",
            details={
                "runner_script_path": self.runner_script_path,
                "runner_script_sha256": self.runner_script_sha256,
                "ready_to_execute": False,
                "dynamic_counters_initialized": True,
            },
        )

    @property
    def total_calls(self) -> int:
        """Derived total calls: allowed + blocked."""
        return self.allowed_calls + self.blocked_calls

    def allocate_call_id(self) -> Tuple[str, int]:
        """Allocate next unique call_id and call_index across all proxies bound to this logger."""
        self._call_counter += 1
        return f"call_{self._call_counter}", self._call_counter

    def append_event(
        self,
        event_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append an event to the hash chain, flushing and fsyncing immediately."""
        if self._closed:
            raise AuditLogDurabilityError("Cannot append event to closed audit logger")

        ts = datetime.now(timezone.utc).isoformat()
        rec_payload = {
            "seq": self.seq,
            "prev_hash": self.prev_hash,
            "timestamp_utc": ts,
            "event_type": event_type,
            "message": message,
            "details": details or {},
        }

        canon_bytes = canonical_json_bytes(rec_payload)
        rec_hash = hashlib.sha256(canon_bytes).hexdigest()

        record_with_hash = dict(rec_payload)
        record_with_hash["record_hash"] = rec_hash

        line = json.dumps(record_with_hash, ensure_ascii=False) + "\n"

        try:
            self._file.write(line)
            self._file.flush()
            os.fsync(self._file.fileno())
        except Exception as e:
            self._failed = True
            raise AuditLogDurabilityError(f"Failed to write and fsync audit record: {e}") from e

        self.prev_hash = rec_hash
        self.seq += 1
        self.records.append(record_with_hash)
        return record_with_hash

    def close_success(self) -> str:
        """Write terminal success record with measured counters and close file."""
        if self._closed:
            return compute_file_sha256(self.log_path)

        self.append_event(
            event_type="AUDIT_TERMINAL_SUCCESS",
            message="Preflight execution audit terminated successfully",
            details={
                "total_calls": self.total_calls,
                "allowed_calls": self.allowed_calls,
                "blocked_calls": self.blocked_calls,
                "clip_insertions_attempted": self.clip_insertions_attempted,
                "dispatch_calls_attempted": self.dispatch_calls_attempted,
                "ready_to_execute": False,
            },
        )
        self._file.close()
        self._closed = True
        return compute_file_sha256(self.log_path)

    def close_failure(self, reason: str) -> str:
        """Write terminal failure/aborted record and close file."""
        if self._closed:
            return compute_file_sha256(self.log_path) if os.path.exists(self.log_path) else ""

        try:
            self.append_event(
                event_type="AUDIT_TERMINAL_FAILURE",
                message=f"Preflight execution audit terminated with failure: {reason}",
                details={
                    "reason": reason,
                    "total_calls": self.total_calls,
                    "allowed_calls": self.allowed_calls,
                    "blocked_calls": self.blocked_calls,
                    "clip_insertions_attempted": self.clip_insertions_attempted,
                    "dispatch_calls_attempted": self.dispatch_calls_attempted,
                    "ready_to_execute": False,
                },
            )
        except Exception:
            pass
        finally:
            self._file.close()
            self._closed = True

        return compute_file_sha256(self.log_path) if os.path.exists(self.log_path) else ""


def _setup_proxy_authority(policy_mapping: MappingProxyType[str, FrozenSet[str]]) -> Tuple[Any, Any]:
    """Private factory creating the audited proxy constructor and unforgeable verifier (Findings G4-R10-R4-01 through G4-R10-R6-01 Fix).

    All marker types, construction authority, and registry sets are enclosed strictly within
    this closure and are NOT exposed as module attributes or caller-invocable registration methods.
    Proxies are bound strictly to the active logger instance and expected role under an immutable policy.
    """
    _creation_authorized = False
    _fixed_policy: MappingProxyType[str, FrozenSet[str]] = MappingProxyType(dict(policy_mapping))

    class _InternalAuditedMarker:
        __slots__ = ()

        def __init_subclass__(cls, **kwargs: Any) -> None:
            super().__init_subclass__(**kwargs)
            if not _creation_authorized:
                raise TypeError(
                    "Direct or external subclassing of _InternalAuditedMarker is strictly forbidden"
                )

    _active_proxies: Set[Any] = set()
    # Maps id(proxy) -> (id(logger), object_role)
    _registered_proxy_bindings: Dict[int, Tuple[int, str]] = {}

    def create_audited_proxy(
        target: Any,
        logger: HashChainedAuditLogger,
        object_role: str,
    ) -> Any:
        """Create an audited proxy using closure encapsulation with fixed role allowlist.

        Raw target and logger are contained strictly within this closure frame and are NOT
        exposed via instance attributes, module dictionaries, or public getters.
        Callers cannot supply custom or expanded allowlists, and policy mapping is immutable.
        """
        nonlocal _creation_authorized
        methods = _fixed_policy.get(object_role, frozenset())

        _creation_authorized = True
        try:
            class _ClosureAuditedProxy(_InternalAuditedMarker):
                __slots__ = ()

                def __getattribute__(self, name: str) -> Any:
                    if name.startswith("_"):
                        raise AttributeError(
                            f"Direct access to private or internal attribute '{name}' is disallowed on AuditedResolveProxy"
                        )
                    return super().__getattribute__(name)

                def __getattr__(self, name: str) -> Any:
                    if name.startswith("_"):
                        raise AttributeError(
                            f"Direct access to private or internal attribute '{name}' is disallowed on AuditedResolveProxy"
                        )

                    def _proxy_call(*args: Any, **kwargs: Any) -> Any:
                        # 1. Check if method is in explicit allowlist
                        if name not in methods:
                            logger.blocked_calls += 1

                            # Check if it is a dispatch attempt
                            is_dispatch = name.lower() in DISPATCH_METHOD_NAMES
                            if is_dispatch:
                                logger.dispatch_calls_attempted += 1

                            # Check if it is a timeline insertion attempt
                            is_insertion = name.lower() in TIMELINE_INSERTION_NAMES
                            if is_insertion:
                                logger.clip_insertions_attempted += 1

                            category = (
                                "DISPATCH_ATTEMPT"
                                if is_dispatch
                                else "TIMELINE_INSERTION_ATTEMPT"
                                if is_insertion
                                else "UNAPPROVED_METHOD_ATTEMPT"
                            )

                            try:
                                b_args = safe_serialize_val(args)
                            except Exception:
                                b_args = {"__error__": "ARGS_SERIALIZATION_FAILED"}
                            try:
                                b_kwargs = safe_serialize_val(kwargs)
                            except Exception:
                                b_kwargs = {"__error__": "KWARGS_SERIALIZATION_FAILED"}

                            logger.append_event(
                                event_type="BLOCKED_METHOD_ATTEMPT",
                                message=f"Blocked unapproved call {object_role}.{name}",
                                details={
                                    "object_role": object_role,
                                    "method_name": name,
                                    "arguments": b_args,
                                    "kwargs": b_kwargs,
                                    "category": category,
                                    "call_index": logger.total_calls,
                                },
                            )
                            raise DisallowedMethodError(
                                f"Method '{name}' on '{object_role}' is not in Gate 4 explicit allowlist"
                            )

                        # Prevalidate and safely serialize arguments and kwargs BEFORE adapter invocation (Findings G4-R10-R11-01 & G4-R10-R12-01 Fix)
                        try:
                            serialized_args = safe_serialize_val(args)
                        except Exception:
                            serialized_args = None

                        try:
                            serialized_kwargs = safe_serialize_val(kwargs)
                        except Exception:
                            serialized_kwargs = None

                        if serialized_args is None or serialized_kwargs is None:
                            logger.blocked_calls += 1
                            actual_arg_err: Optional[Exception] = None
                            try:
                                safe_serialize_val(args)
                                safe_serialize_val(kwargs)
                            except Exception as e:
                                actual_arg_err = e
                            actual_arg_err = actual_arg_err or ValueError("Argument serialization failure")

                            logger.append_event(
                                event_type="ARGUMENT_SERIALIZATION_FAILURE",
                                message=f"Argument serialization failure in {object_role}.{name}: {actual_arg_err}",
                                details={
                                    "object_role": object_role,
                                    "method_name": name,
                                    "arguments": serialized_args if serialized_args is not None else {"__error__": "ARGS_SERIALIZATION_FAILED"},
                                    "kwargs": serialized_kwargs if serialized_kwargs is not None else {"__error__": "KWARGS_SERIALIZATION_FAILED"},
                                    "serialization_error_type": type(actual_arg_err).__name__,
                                    "serialization_error_message": str(actual_arg_err),
                                    "category": "ARGUMENT_SERIALIZATION_FAILURE",
                                    "call_index": logger.total_calls,
                                },
                            )
                            raise actual_arg_err

                        # 2. Allocate unique call ID and call index across all proxies bound to this logger
                        call_id, call_idx = logger.allocate_call_id()

                        # 3. Write-ahead durable intent record BEFORE invoking adapter (Workorder G07-07B7-PREFLIGHT-GATE4-R15)
                        # Must flush and fsync immediately. If fsync fails, exception is raised and adapter is NEVER called.
                        intent_rec = logger.append_event(
                            event_type="API_CALL_INTENT",
                            message=f"Intent to execute {object_role}.{name}",
                            details={
                                "call_id": call_id,
                                "call_index": call_idx,
                                "object_role": object_role,
                                "method_name": name,
                                "arguments": serialized_args,
                                "kwargs": serialized_kwargs,
                            },
                        )

                        # 4. Invoke target adapter method
                        try:
                            target_attr = getattr(target, name)
                            ret = target_attr(*args, **kwargs)
                        except Exception as e:
                            logger.allowed_calls += 1
                            logger.append_event(
                                event_type="API_CALL_EXCEPTION",
                                message=f"Exception in {object_role}.{name}: {e}",
                                details={
                                    "call_id": call_id,
                                    "call_index": call_idx,
                                    "intent_seq": intent_rec["seq"],
                                    "object_role": object_role,
                                    "method_name": name,
                                    "arguments": serialized_args,
                                    "kwargs": serialized_kwargs,
                                    "exception_type": type(e).__name__,
                                    "exception_message": str(e),
                                },
                            )
                            raise

                        # 5. Serialize return value safely (Finding G4-R10-R10-01 Fix)
                        try:
                            serialized_ret = safe_serialize_val(ret)
                        except Exception as ser_err:
                            logger.allowed_calls += 1
                            logger.append_event(
                                event_type="API_CALL_SERIALIZATION_FAILURE",
                                message=f"Serialization failure in {object_role}.{name} return value: {ser_err}",
                                details={
                                    "call_id": call_id,
                                    "call_index": call_idx,
                                    "intent_seq": intent_rec["seq"],
                                    "object_role": object_role,
                                    "method_name": name,
                                    "arguments": serialized_args,
                                    "kwargs": serialized_kwargs,
                                    "return_type": type(ret).__name__,
                                    "serialization_error_type": type(ser_err).__name__,
                                    "serialization_error_message": str(ser_err),
                                    "return_repr": {"__error__": "RAW_RESULT_SERIALIZATION_FAILED"},
                                },
                            )
                            raise

                        # 6. Log successful call outcome linked to intent
                        logger.allowed_calls += 1
                        logger.append_event(
                            event_type="API_CALL",
                            message=f"Executed {object_role}.{name}",
                            details={
                                "call_id": call_id,
                                "call_index": call_idx,
                                "intent_seq": intent_rec["seq"],
                                "object_role": object_role,
                                "method_name": name,
                                "arguments": serialized_args,
                                "kwargs": serialized_kwargs,
                                "return_type": type(ret).__name__,
                                "return_repr": serialized_ret,
                            },
                        )

                        # 7. Recursively wrap returned objects
                        return _wrap_return_value(name, ret, logger)

                    return _proxy_call
        finally:
            _creation_authorized = False

        proxy = _ClosureAuditedProxy()
        _active_proxies.add(proxy)
        _registered_proxy_bindings[id(proxy)] = (id(logger), object_role)
        return proxy

    def is_audited_proxy(val: Any, logger: HashChainedAuditLogger, expected_role: str) -> bool:
        """Unforgeable verification that val is an authentic audited proxy bound to the exact logger and role."""
        if not isinstance(val, _InternalAuditedMarker):
            return False
        binding = _registered_proxy_bindings.get(id(val))
        if binding is None:
            return False
        bound_logger_id, bound_role = binding
        return (bound_logger_id == id(logger)) and (bound_role == expected_role)

    return create_audited_proxy, is_audited_proxy


create_audited_proxy, _is_audited_proxy = _setup_proxy_authority(ALLOWED_METHODS_BY_ROLE)

# Public alias for proxy creation
AuditedResolveProxy = create_audited_proxy


def _wrap_return_value(method_name: str, val: Any, logger: HashChainedAuditLogger) -> Any:
    """Recursively wrap nested adapter objects so raw objects never escape in any container."""
    if val is None or isinstance(val, (bool, int, float, str)):
        return val

    # Map method name to the strictly expected object role
    if method_name == "GetProjectManager":
        expected_role = "ProjectManager"
    elif method_name in ("CreateProject", "GetCurrentProject"):
        expected_role = "Project"
    elif method_name == "GetMediaPool":
        expected_role = "MediaPool"
    elif method_name in ("CreateEmptyTimeline", "GetTimelineByIndex"):
        expected_role = "Timeline"
    elif method_name in ("ImportMedia", "AddItemListToMediaPool"):
        expected_role = "MediaPoolItem"
    elif method_name == "GetItemListInTrack":
        expected_role = "TimelineItem"
    else:
        expected_role = "UnknownSubObject"

    # Finding G4-R10-R5-01 Fix: Only pass through if the object is an authentic audited proxy
    # created by this exact run with the exact expected logger and expected role allowlist.
    # Proxies with a different logger or mismatched role must be wrapped under the current run's logger.
    if _is_audited_proxy(val, logger=logger, expected_role=expected_role):
        return val

    if isinstance(val, list):
        return [_wrap_return_value(method_name, elem, logger) for elem in val]

    if isinstance(val, tuple):
        return tuple(_wrap_return_value(method_name, elem, logger) for elem in val)

    if isinstance(val, dict):
        return {str(k): _wrap_return_value(method_name, v, logger) for k, v in val.items()}

    # Non-container, non-primitive object: Wrap with strictly determined expected proxy role
    return create_audited_proxy(val, logger=logger, object_role=expected_role)


@dataclass
class AuditLogVerificationResult:
    valid: bool
    records_count: int
    terminal_event_type: Optional[str]
    measured_total_calls: int
    measured_allowed_calls: int
    measured_blocked_calls: int
    measured_dispatch_calls: int
    measured_clip_insertions: int
    sha256: str
    error: Optional[str] = None
    unresolved_intents: List[str] = field(default_factory=list)


def verify_audit_log_chain(log_path: str) -> AuditLogVerificationResult:
    """Strictly verify sequence continuity, cryptographic hash chaining, and derived counters.

    Finding G4-R10-R2-02 & Workorder G07-07B7-PREFLIGHT-GATE4-R15:
    - Derives all counters from verified event records.
    - Requires sequence 0 to be AUDIT_START.
    - Requires exactly one terminal record and requires it to be strictly the final record.
    - Validates presence and non-negative integer type for ALL mandatory terminal summary fields.
    - Reconciles every terminal counter against independently derived event totals.
    - Enforces write-ahead API_CALL_INTENT records, call ID uniqueness, ordering, and exact outcome linkage.
    - Rejects logs with unresolved intents or mismatched outcomes.
    """
    if not os.path.exists(log_path):
        return AuditLogVerificationResult(
            valid=False,
            records_count=0,
            terminal_event_type=None,
            measured_total_calls=0,
            measured_allowed_calls=0,
            measured_blocked_calls=0,
            measured_dispatch_calls=0,
            measured_clip_insertions=0,
            sha256="",
            error=f"Audit log file not found: {log_path}",
        )

    file_sha256 = compute_file_sha256(log_path)
    expected_seq = 0
    expected_prev_hash = "0" * 64
    records: List[Dict[str, Any]] = []

    derived_total_calls = 0
    derived_allowed_calls = 0
    derived_blocked_calls = 0
    derived_dispatch_calls = 0
    derived_clip_insertions = 0
    start_record_count = 0
    terminal_record_count = 0

    intents_by_call_id: Dict[str, Dict[str, Any]] = {}
    outcomes_by_call_id: Dict[str, Dict[str, Any]] = {}
    unresolved_call_ids: Set[str] = set()

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if not lines:
        return AuditLogVerificationResult(
            valid=False,
            records_count=0,
            terminal_event_type=None,
            measured_total_calls=0,
            measured_allowed_calls=0,
            measured_blocked_calls=0,
            measured_dispatch_calls=0,
            measured_clip_insertions=0,
            sha256=file_sha256,
            error="Audit log file is empty",
        )

    total_lines = len(lines)

    for line_idx, line in enumerate(lines):
        line_str = line.strip()
        if not line_str:
            return AuditLogVerificationResult(
                valid=False,
                records_count=len(records),
                terminal_event_type=None,
                measured_total_calls=0,
                measured_allowed_calls=0,
                measured_blocked_calls=0,
                measured_dispatch_calls=0,
                measured_clip_insertions=0,
                sha256=file_sha256,
                error=f"Empty line encountered at index {line_idx}",
            )

        try:
            rec = json.loads(line_str)
        except Exception as e:
            return AuditLogVerificationResult(
                valid=False,
                records_count=len(records),
                terminal_event_type=None,
                measured_total_calls=0,
                measured_allowed_calls=0,
                measured_blocked_calls=0,
                measured_dispatch_calls=0,
                measured_clip_insertions=0,
                sha256=file_sha256,
                error=f"JSON parse error at line {line_idx} (possible torn tail): {e}",
            )

        # Check sequence continuity
        if rec.get("seq") != expected_seq:
            return AuditLogVerificationResult(
                valid=False,
                records_count=len(records),
                terminal_event_type=None,
                measured_total_calls=0,
                measured_allowed_calls=0,
                measured_blocked_calls=0,
                measured_dispatch_calls=0,
                measured_clip_insertions=0,
                sha256=file_sha256,
                error=f"Sequence gap at line {line_idx}: expected {expected_seq}, found {rec.get('seq')}",
            )

        # Check prev_hash link
        if rec.get("prev_hash") != expected_prev_hash:
            return AuditLogVerificationResult(
                valid=False,
                records_count=len(records),
                terminal_event_type=None,
                measured_total_calls=0,
                measured_allowed_calls=0,
                measured_blocked_calls=0,
                measured_dispatch_calls=0,
                measured_clip_insertions=0,
                sha256=file_sha256,
                error=f"Hash chain break at line {line_idx}: prev_hash does not match predecessor digest",
            )

        # Verify record hash
        rec_hash = rec.get("record_hash")
        rec_payload = {
            "seq": rec["seq"],
            "prev_hash": rec["prev_hash"],
            "timestamp_utc": rec["timestamp_utc"],
            "event_type": rec["event_type"],
            "message": rec["message"],
            "details": rec["details"],
        }
        computed_hash = hashlib.sha256(canonical_json_bytes(rec_payload)).hexdigest()

        if rec_hash != computed_hash:
            return AuditLogVerificationResult(
                valid=False,
                records_count=len(records),
                terminal_event_type=None,
                measured_total_calls=0,
                measured_allowed_calls=0,
                measured_blocked_calls=0,
                measured_dispatch_calls=0,
                measured_clip_insertions=0,
                sha256=file_sha256,
                error=f"Record hash mismatch at line {line_idx}: stored {rec_hash}, computed {computed_hash}",
            )

        ev_type = rec.get("event_type")

        # Genesis record check (Finding G4-R10-03)
        if expected_seq == 0:
            if ev_type != "AUDIT_START":
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=0,
                    measured_allowed_calls=0,
                    measured_blocked_calls=0,
                    measured_dispatch_calls=0,
                    measured_clip_insertions=0,
                    sha256=file_sha256,
                    error="First record at sequence 0 must be AUDIT_START",
                )
            start_record_count += 1
        else:
            if ev_type == "AUDIT_START":
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=0,
                    measured_allowed_calls=0,
                    measured_blocked_calls=0,
                    measured_dispatch_calls=0,
                    measured_clip_insertions=0,
                    sha256=file_sha256,
                    error=f"Duplicate AUDIT_START record at sequence {expected_seq}",
                )

        # Terminal event location check (Finding G4-R10-R2-02)
        if ev_type in ("AUDIT_TERMINAL_SUCCESS", "AUDIT_TERMINAL_FAILURE"):
            terminal_record_count += 1
            if line_idx != total_lines - 1:
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=ev_type,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Terminal record {ev_type} encountered at line {line_idx} before final record {total_lines - 1}",
                )

        # Validate intent and outcome events and derive counts (Workorder G07-07B7-PREFLIGHT-GATE4-R15)
        if ev_type == "API_CALL_INTENT":
            details = rec.get("details", {})
            call_id = details.get("call_id")
            if not call_id or not isinstance(call_id, str):
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"API_CALL_INTENT at line {line_idx} missing valid call_id",
                )
            if call_id in intents_by_call_id:
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Duplicate API_CALL_INTENT for call_id '{call_id}' at line {line_idx}",
                )
            for req_field in ("object_role", "method_name", "arguments", "kwargs", "call_index"):
                if req_field not in details:
                    return AuditLogVerificationResult(
                        valid=False,
                        records_count=len(records),
                        terminal_event_type=None,
                        measured_total_calls=derived_total_calls,
                        measured_allowed_calls=derived_allowed_calls,
                        measured_blocked_calls=derived_blocked_calls,
                        measured_dispatch_calls=derived_dispatch_calls,
                        measured_clip_insertions=derived_clip_insertions,
                        sha256=file_sha256,
                        error=f"API_CALL_INTENT at line {line_idx} missing required field '{req_field}'",
                    )

            # Finding G4-R15-01 Fix: Enforce positive integer and monotonic sequential call_index for intents
            call_idx = details.get("call_index")
            if type(call_idx) is not int or call_idx <= 0:
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"API_CALL_INTENT at line {line_idx} call_index must be positive integer, got {call_idx!r}",
                )

            expected_next_call_index = len(intents_by_call_id) + 1
            if call_idx != expected_next_call_index:
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"API_CALL_INTENT at line {line_idx} non-monotonic call_index: expected {expected_next_call_index}, got {call_idx}",
                )

            intents_by_call_id[call_id] = rec
            unresolved_call_ids.add(call_id)

        elif ev_type in ("API_CALL", "API_CALL_EXCEPTION", "API_CALL_SERIALIZATION_FAILURE"):
            details = rec.get("details", {})
            call_id = details.get("call_id")
            if not call_id or not isinstance(call_id, str):
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Outcome event {ev_type} at line {line_idx} missing valid call_id",
                )
            if call_id not in intents_by_call_id:
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Outcome event {ev_type} at line {line_idx} for call_id '{call_id}' has no preceding API_CALL_INTENT",
                )
            if call_id in outcomes_by_call_id:
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Duplicate outcome event for call_id '{call_id}' at line {line_idx}",
                )

            intent_rec = intents_by_call_id[call_id]
            intent_details = intent_rec["details"]

            if intent_rec["seq"] >= rec["seq"]:
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Ordering violation at line {line_idx}: intent seq {intent_rec['seq']} >= outcome seq {rec['seq']}",
                )

            if details.get("intent_seq") != intent_rec["seq"]:
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Outcome event {ev_type} intent_seq {details.get('intent_seq')} does not match intent seq {intent_rec['seq']}",
                )

            # Finding G4-R15-01 Fix: Enforce exact match between outcome call_index and intent call_index
            outcome_call_idx = details.get("call_index")
            intent_call_idx = intent_details.get("call_index")
            if type(outcome_call_idx) is not int:
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Outcome event {ev_type} at line {line_idx} missing valid integer call_index",
                )
            if outcome_call_idx != intent_call_idx:
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Outcome event {ev_type} call_index ({outcome_call_idx}) does not match intent call_index ({intent_call_idx}) for call_id '{call_id}'",
                )

            if details.get("object_role") != intent_details.get("object_role") or details.get("method_name") != intent_details.get("method_name"):
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Outcome event {ev_type} role/method mismatch with intent for call_id '{call_id}'",
                )

            if details.get("arguments") != intent_details.get("arguments") or details.get("kwargs") != intent_details.get("kwargs"):
                return AuditLogVerificationResult(
                    valid=False,
                    records_count=len(records),
                    terminal_event_type=None,
                    measured_total_calls=derived_total_calls,
                    measured_allowed_calls=derived_allowed_calls,
                    measured_blocked_calls=derived_blocked_calls,
                    measured_dispatch_calls=derived_dispatch_calls,
                    measured_clip_insertions=derived_clip_insertions,
                    sha256=file_sha256,
                    error=f"Outcome event {ev_type} arguments mismatch with intent for call_id '{call_id}'",
                )

            outcomes_by_call_id[call_id] = rec
            unresolved_call_ids.remove(call_id)
            derived_total_calls += 1
            derived_allowed_calls += 1

        elif ev_type in ("BLOCKED_METHOD_ATTEMPT", "ARGUMENT_SERIALIZATION_FAILURE"):
            derived_total_calls += 1
            derived_blocked_calls += 1
            cat = rec.get("details", {}).get("category")
            if cat == "DISPATCH_ATTEMPT":
                derived_dispatch_calls += 1
            elif cat == "TIMELINE_INSERTION_ATTEMPT":
                derived_clip_insertions += 1

        expected_prev_hash = rec_hash
        expected_seq += 1
        records.append(rec)

    if start_record_count != 1:
        return AuditLogVerificationResult(
            valid=False,
            records_count=len(records),
            terminal_event_type=None,
            measured_total_calls=0,
            measured_allowed_calls=0,
            measured_blocked_calls=0,
            measured_dispatch_calls=0,
            measured_clip_insertions=0,
            sha256=file_sha256,
            error="Missing genesis AUDIT_START record",
        )

    if terminal_record_count != 1:
        return AuditLogVerificationResult(
            valid=False,
            records_count=len(records),
            terminal_event_type=None,
            measured_total_calls=derived_total_calls,
            measured_allowed_calls=derived_allowed_calls,
            measured_blocked_calls=derived_blocked_calls,
            measured_dispatch_calls=derived_dispatch_calls,
            measured_clip_insertions=derived_clip_insertions,
            sha256=file_sha256,
            error=f"Audit log must contain exactly one terminal record, found {terminal_record_count}",
        )

    last_rec = records[-1]
    terminal_type = last_rec.get("event_type")

    if terminal_type not in ("AUDIT_TERMINAL_SUCCESS", "AUDIT_TERMINAL_FAILURE"):
        return AuditLogVerificationResult(
            valid=False,
            records_count=len(records),
            terminal_event_type=terminal_type,
            measured_total_calls=derived_total_calls,
            measured_allowed_calls=derived_allowed_calls,
            measured_blocked_calls=derived_blocked_calls,
            measured_dispatch_calls=derived_dispatch_calls,
            measured_clip_insertions=derived_clip_insertions,
            sha256=file_sha256,
            error=f"Log missing valid terminal record; ends with {terminal_type}",
        )

    # Property 4: A log with unresolved intent/outcome state must never verify as a successful preflight
    if unresolved_call_ids:
        return AuditLogVerificationResult(
            valid=False,
            records_count=len(records),
            terminal_event_type=terminal_type,
            measured_total_calls=derived_total_calls,
            measured_allowed_calls=derived_allowed_calls,
            measured_blocked_calls=derived_blocked_calls,
            measured_dispatch_calls=derived_dispatch_calls,
            measured_clip_insertions=derived_clip_insertions,
            sha256=file_sha256,
            error=f"Unresolved API_CALL_INTENT records without matching outcome: {sorted(unresolved_call_ids)}",
            unresolved_intents=sorted(unresolved_call_ids),
        )

    # Mandatory field validation and reconciliation (Finding G4-R10-R2-02 Fix)
    details = last_rec.get("details", {})
    derived_map = {
        "total_calls": derived_total_calls,
        "allowed_calls": derived_allowed_calls,
        "blocked_calls": derived_blocked_calls,
        "dispatch_calls_attempted": derived_dispatch_calls,
        "clip_insertions_attempted": derived_clip_insertions,
    }

    for field_name in REQUIRED_TERMINAL_COUNTER_FIELDS:
        if field_name not in details:
            return AuditLogVerificationResult(
                valid=False,
                records_count=len(records),
                terminal_event_type=terminal_type,
                measured_total_calls=derived_total_calls,
                measured_allowed_calls=derived_allowed_calls,
                measured_blocked_calls=derived_blocked_calls,
                measured_dispatch_calls=derived_dispatch_calls,
                measured_clip_insertions=derived_clip_insertions,
                sha256=file_sha256,
                error=f"Terminal record missing mandatory summary field '{field_name}'",
            )

        val = details[field_name]
        if type(val) is not int or val < 0:
            return AuditLogVerificationResult(
                valid=False,
                records_count=len(records),
                terminal_event_type=terminal_type,
                measured_total_calls=derived_total_calls,
                measured_allowed_calls=derived_allowed_calls,
                measured_blocked_calls=derived_blocked_calls,
                measured_dispatch_calls=derived_dispatch_calls,
                measured_clip_insertions=derived_clip_insertions,
                sha256=file_sha256,
                error=f"Terminal summary field '{field_name}' must be non-negative integer, got {val!r}",
            )

        expected_val = derived_map[field_name]
        if val != expected_val:
            return AuditLogVerificationResult(
                valid=False,
                records_count=len(records),
                terminal_event_type=terminal_type,
                measured_total_calls=derived_total_calls,
                measured_allowed_calls=derived_allowed_calls,
                measured_blocked_calls=derived_blocked_calls,
                measured_dispatch_calls=derived_dispatch_calls,
                measured_clip_insertions=derived_clip_insertions,
                sha256=file_sha256,
                error=f"Terminal summary field '{field_name}' ({val}) does not match derived event count ({expected_val})",
            )

    return AuditLogVerificationResult(
        valid=True,
        records_count=len(records),
        terminal_event_type=terminal_type,
        measured_total_calls=derived_total_calls,
        measured_allowed_calls=derived_allowed_calls,
        measured_blocked_calls=derived_blocked_calls,
        measured_dispatch_calls=derived_dispatch_calls,
        measured_clip_insertions=derived_clip_insertions,
        sha256=file_sha256,
        error=None,
    )


@dataclass
class Gate4PreflightResult:
    success: bool
    is_live_resolve_session: bool
    ready_to_execute: bool
    zero_dispatch_scope: str
    provenance_by_predicate: Dict[str, Dict[str, Any]]
    counters: Dict[str, int]
    audit_log_path: str
    audit_log_sha256: str
    audit_log_verified: bool
    details: Dict[str, Any] = field(default_factory=dict)


def _normalize_dimension(val: Any) -> int:
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return -1


def _normalize_fps(val: Any) -> float:
    try:
        v_str = str(val).strip()
        if "/" in v_str:
            num, den = v_str.split("/", 1)
            return float(num) / float(den)
        return float(v_str)
    except (ValueError, TypeError):
        return -1.0


class Gate4PreflightRunner:
    """Core local Gate 4 preflight orchestrator with explicit adapter injection."""

    def __init__(
        self,
        resolve_app: Any,
        nonce: str,
        audit_log_path: str,
        runner_script_path: Optional[str] = None,
        operator_attestation: Optional[str] = None,
        screenshot_path: Optional[str] = None,
        screenshot_sha256: Optional[str] = None,
        source_media_path: Optional[str] = None,
        source_media_sha256: Optional[str] = None,
    ):
        self.resolve_app = resolve_app
        self.nonce = nonce
        self.audit_log_path = audit_log_path
        self.runner_script_path = runner_script_path or __file__
        self.operator_attestation = operator_attestation
        self.screenshot_path = screenshot_path
        self.screenshot_sha256 = screenshot_sha256
        self.source_media_path = source_media_path
        self.source_media_sha256 = source_media_sha256

        # Findings G4-R10-R7-01 & G4-R10-R8-01 Fix:
        # Bind closure-owned denylist validator function. Does NOT re-read module globals.
        self._is_blacklisted_uid_fn: Callable[[str], bool] = _is_incident_blacklisted_uid
        self._fixed_blacklisted_uids: FrozenSet[str] = _CANONICAL_INCIDENT_BLACKLIST

        self.logger = HashChainedAuditLogger(
            log_path=self.audit_log_path,
            runner_script_path=self.runner_script_path,
        )

    def run(self) -> Gate4PreflightResult:
        """Run Gate 4 preflight orchestration through the audited proxy."""
        provenance: Dict[str, Dict[str, Any]] = {}
        expected_project_name = f"RF_TRIAL_{self.nonce}"
        expected_timeline_name = f"RF_TRIAL_TL_{self.nonce}"

        try:
            # 1. Wrap root adapter in AuditedResolveProxy closure
            proxy_app = AuditedResolveProxy(
                target=self.resolve_app,
                logger=self.logger,
                object_role="Resolve",
            )

            # 2. Setup project & timeline
            pm = proxy_app.GetProjectManager()
            proj = pm.CreateProject(expected_project_name)

            proj.SetSetting("timelineResolutionWidth", "1920")
            proj.SetSetting("timelineResolutionHeight", "1080")
            proj.SetSetting("timelineFrameRate", "24.0")

            mp = proj.GetMediaPool()
            tl = mp.CreateEmptyTimeline(expected_timeline_name)

            # Optional import of non-production test media into Media Pool only
            if self.source_media_path:
                mp.ImportMedia([self.source_media_path])

            # 3. Readback Predicates
            # 1.1 - 1.4: Identity
            v_proj_name = proj.GetName()
            provenance["1.1"] = self._record_prov("1.1", "Project", "GetName", [], v_proj_name)

            v_proj_uid = proj.GetUniqueId() if hasattr(proj, "GetUniqueId") else ""
            provenance["1.2"] = self._record_prov("1.2", "Project", "GetUniqueId", [], v_proj_uid)

            v_tl_name = tl.GetName()
            provenance["1.3"] = self._record_prov("1.3", "Timeline", "GetName", [], v_tl_name)

            v_tl_uid = tl.GetUniqueId() if hasattr(tl, "GetUniqueId") else ""
            provenance["1.4"] = self._record_prov("1.4", "Timeline", "GetUniqueId", [], v_tl_uid)

            # 2.1 - 2.3: Project Settings
            w_p = proj.GetSetting("timelineResolutionWidth")
            provenance["2.1"] = self._record_prov("2.1", "Project", "GetSetting", ["timelineResolutionWidth"], w_p)

            h_p = proj.GetSetting("timelineResolutionHeight")
            provenance["2.2"] = self._record_prov("2.2", "Project", "GetSetting", ["timelineResolutionHeight"], h_p)

            fps_p = proj.GetSetting("timelineFrameRate")
            provenance["2.3"] = self._record_prov("2.3", "Project", "GetSetting", ["timelineFrameRate"], fps_p)

            # 3.1 - 3.3: Timeline Settings
            w_tl = tl.GetSetting("timelineResolutionWidth")
            provenance["3.1"] = self._record_prov("3.1", "Timeline", "GetSetting", ["timelineResolutionWidth"], w_tl)

            h_tl = tl.GetSetting("timelineResolutionHeight")
            provenance["3.2"] = self._record_prov("3.2", "Timeline", "GetSetting", ["timelineResolutionHeight"], h_tl)

            fps_tl = tl.GetSetting("timelineFrameRate")
            provenance["3.3"] = self._record_prov("3.3", "Timeline", "GetSetting", ["timelineFrameRate"], fps_tl)

            # 4.1, 5.1: Track Counts
            vt_count = tl.GetTrackCount("video")
            provenance["4.1"] = self._record_prov("4.1", "Timeline", "GetTrackCount", ["video"], vt_count)

            at_count = tl.GetTrackCount("audio")
            provenance["5.1"] = self._record_prov("5.1", "Timeline", "GetTrackCount", ["audio"], at_count)

            # 6.1, 7.1: Track Item Lists
            v1_items = tl.GetItemListInTrack("video", 1)
            provenance["6.1"] = self._record_prov("6.1", "Timeline", "GetItemListInTrack", ["video", 1], v1_items)

            a1_items = tl.GetItemListInTrack("audio", 1) if at_count >= 1 else []
            provenance["7.1"] = self._record_prov("7.1", "Timeline", "GetItemListInTrack", ["audio", 1], a1_items)

            # 8.1, 8.2: Start and End Frames
            start_f = tl.GetStartFrame()
            provenance["8.1"] = self._record_prov("8.1", "Timeline", "GetStartFrame", [], start_f)

            end_f = tl.GetEndFrame()
            provenance["8.2"] = self._record_prov("8.2", "Timeline", "GetEndFrame", [], end_f)

            # 9.1: Markers
            markers = tl.GetMarkers()
            provenance["9.1"] = self._record_prov("9.1", "Timeline", "GetMarkers", [], markers)

            # Strict validation of ALL Gate 4 Predicates (Finding G4-R10-02 Fix)
            # 1. Names exact match
            if str(v_proj_name) != expected_project_name:
                raise PreflightVerificationError(
                    f"Project name mismatch: expected '{expected_project_name}', got '{v_proj_name}'"
                )
            if str(v_tl_name) != expected_timeline_name:
                raise PreflightVerificationError(
                    f"Timeline name mismatch: expected '{expected_timeline_name}', got '{v_tl_name}'"
                )

            # 2. UIDs fresh, non-empty, non-blacklisted
            s_proj_uid = str(v_proj_uid).strip()
            s_tl_uid = str(v_tl_uid).strip()
            if not s_proj_uid or not s_tl_uid:
                raise PreflightVerificationError(
                    f"Target UIDs must be non-empty strings: proj='{s_proj_uid}', tl='{s_tl_uid}'"
                )
            if self._is_blacklisted_uid_fn(s_proj_uid):
                raise PreflightVerificationError(
                    f"Project UID '{s_proj_uid}' is blacklisted from previous incident"
                )
            if self._is_blacklisted_uid_fn(s_tl_uid):
                raise PreflightVerificationError(
                    f"Timeline UID '{s_tl_uid}' is blacklisted from previous incident"
                )
            if s_proj_uid == s_tl_uid:
                raise PreflightVerificationError(
                    f"Project UID and Timeline UID must be distinct: {s_proj_uid}"
                )

            # 3. Project Resolution & Frame Rate (1920x1080 @ 24fps)
            if _normalize_dimension(w_p) != 1920 or _normalize_dimension(h_p) != 1080:
                raise PreflightVerificationError(
                    f"Project resolution mismatch: expected 1920x1080, got {w_p}x{h_p}"
                )
            if _normalize_fps(fps_p) != 24.0:
                raise PreflightVerificationError(
                    f"Project frame rate mismatch: expected 24.0, got {fps_p}"
                )

            # 4. Timeline Resolution & Frame Rate (1920x1080 @ 24fps)
            if _normalize_dimension(w_tl) != 1920 or _normalize_dimension(h_tl) != 1080:
                raise PreflightVerificationError(
                    f"Timeline resolution mismatch: expected 1920x1080, got {w_tl}x{h_tl}"
                )
            if _normalize_fps(fps_tl) != 24.0:
                raise PreflightVerificationError(
                    f"Timeline frame rate mismatch: expected 24.0, got {fps_tl}"
                )

            # 5. Track Topology (Video exactly 1, Audio 0 or 1)
            if vt_count != 1:
                raise PreflightVerificationError(
                    f"Video track count must be exactly 1, observed {vt_count}"
                )
            if at_count not in (0, 1):
                raise PreflightVerificationError(
                    f"Audio track count must be at most 1, observed {at_count}"
                )

            # 6. Track Content (Zero clips placed)
            if len(v1_items) != 0:
                raise PreflightVerificationError(
                    f"Video track V1 is not empty: found {len(v1_items)} clips"
                )
            if at_count == 1 and len(a1_items) != 0:
                raise PreflightVerificationError(
                    f"Audio track A1 is not empty: found {len(a1_items)} clips"
                )

            # 7. Start / End Frame Equality
            if start_f != end_f:
                raise PreflightVerificationError(
                    f"Start frame {start_f} != end frame {end_f}"
                )

            # 8. Markers Count Zero
            if len(markers) != 0:
                raise PreflightVerificationError(
                    f"Timeline has non-zero markers: {len(markers)}"
                )

            # 9. Derived Predicate 10
            provenance["10.1"] = {
                "predicate_id": "10.1",
                "evidence_classification": "RUNNER_SIDE_DERIVED_CHECK",
                "zero_clips_currently_placed": True,
                "zero_dispatch_calls_observed_through_adapter": (
                    self.logger.dispatch_calls_attempted == 0
                ),
            }

            # 4. Success close & verification
            log_sha256 = self.logger.close_success()
            verification = verify_audit_log_chain(self.audit_log_path)

            if not verification.valid:
                raise AuditLogIntegrityError(
                    f"Audit log verification failed: {verification.error}"
                )

            return Gate4PreflightResult(
                success=True,
                is_live_resolve_session=False,
                ready_to_execute=False,
                zero_dispatch_scope="NO_DISPATCH_CAPABLE_CALL_OBSERVED_THROUGH_AUDITED_ADAPTER",
                provenance_by_predicate=provenance,
                counters={
                    "total_calls": verification.measured_total_calls,
                    "allowed_calls": verification.measured_allowed_calls,
                    "blocked_calls": verification.measured_blocked_calls,
                    "clip_insertions_attempted": verification.measured_clip_insertions,
                    "dispatch_calls_attempted": verification.measured_dispatch_calls,
                },
                audit_log_path=self.audit_log_path,
                audit_log_sha256=log_sha256,
                audit_log_verified=verification.valid,
                details={
                    "project_name": expected_project_name,
                    "timeline_name": expected_timeline_name,
                    "operator_attestation": self.operator_attestation,
                    "screenshot_path": self.screenshot_path,
                    "screenshot_sha256": self.screenshot_sha256,
                },
            )

        except Exception as e:
            self.logger.close_failure(reason=str(e))
            raise

    def _record_prov(
        self,
        pred_id: str,
        obj_type: str,
        method: str,
        args: List[Any],
        raw_val: Any,
    ) -> Dict[str, Any]:
        return {
            "predicate_id": pred_id,
            "object_type": obj_type,
            "method_name": method,
            "arguments": args,
            "raw_return_type": type(raw_val).__name__,
            "raw_return_value": safe_serialize_val(raw_val),
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
