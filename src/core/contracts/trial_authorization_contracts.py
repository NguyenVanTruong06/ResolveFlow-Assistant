"""Trial Authorization Contracts (G07-07B6-I1)

Defines immutable authorization DTOs, strict bounded JSON parsing and canonical
serialization, the 16-field action payload assembler, and the canonical bytes function
for the 32 HMAC-covered authorization fields.

Safety Boundary:
- Mock-only contract layer. No live Resolve, IPC, FusionScript, or network operations.
- Pure contract operations with frozen data structures and strict type gates.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple


# --- Complexity & Parser Constants ---
MAX_JSON_BYTES: int = 1_048_576          # 1 MiB raw byte input bound
DEFAULT_PRE_PARSE_MAX_BYTES: int = 65_536 # 64 KiB default bound for trial messages
MAX_NESTING_DEPTH: int = 16              # Max nesting depth (prevents stack exhaustion)
MAX_TOTAL_NODES: int = 2048              # Max total nodes in document graph
MAX_CONTAINER_LENGTH: int = 512          # Max items per container (512 OK, 513 REJECT)
MAX_STRING_SCALARS: int = 8192           # Max Unicode scalar count per string
MAX_INTEGER_DIGITS: int = 19             # Max digits per int (int64 range)

MIN_INT64: int = -9_223_372_036_854_775_808
MAX_INT64: int = 9_223_372_036_854_775_807

HEX_CHARS: frozenset[str] = frozenset("0123456789abcdefABCDEF")
ISO_UTC_TIMESTAMP_REGEX: re.Pattern = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)

# Exact 16 keys required for canonical action dispatch payload
CANONICAL_ACTION_PAYLOAD_KEYS: frozenset[str] = frozenset({
    "action_index",
    "action_type",
    "authorization_id",
    "duration_frames",
    "execution_id",
    "plan_idempotency_key",
    "record_frame",
    "role",
    "schema_version",
    "source_frame_duration",
    "source_frame_start",
    "source_path",
    "target_project_uid",
    "target_timeline_uid",
    "track_index",
    "track_type",
})


# --- Exception Hierarchy ---
class AcceptanceGateError(Exception):
    """Base exception for all acceptance gate contract errors."""
    pass


class StrictJsonParseError(AcceptanceGateError, ValueError):
    """Grammar or structural parse failure in strict canonical JSON."""
    pass


class StrictJsonComplexityError(AcceptanceGateError, ValueError):
    """Complexity limit exceeded in JSON structure (depth, size, length, nodes)."""
    pass


class TargetExclusionLockError(AcceptanceGateError):
    """Error during target exclusion lock acquisition or validation."""
    pass


class WholePlanReconciliationError(AcceptanceGateError):
    """Identity or state mismatch requiring whole-plan reconciliation."""
    pass


class ReadbackReceiptVerificationError(AcceptanceGateError):
    """Payload schema mismatch, invalid type gate, or verification failure."""
    pass


class TargetLockReleaseError(AcceptanceGateError):
    """Failure releasing target exclusion lock at operating system kernel."""
    pass


class ReceiptCleanupError(AcceptanceGateError):
    """Error cleaning up obsolete or superseded receipt entries."""
    pass


class ReceiptDurabilityError(AcceptanceGateError):
    """Error persisting durable receipt to filesystem."""
    pass


# --- Strict Bounded JSON Parsing & Canonical Serialization ---
@dataclass
class _ValidationState:
    total_nodes: int = 0


def validate_json_structure_pre_parse(raw_bytes: bytes, max_bytes: int = DEFAULT_PRE_PARSE_MAX_BYTES) -> str:
    """
    Grammar-correct lexical scanner checking bounds BEFORE calling json.loads.
    - Rejects payloads exceeding max_bytes.
    - Decodes UTF-8 and catches invalid UTF-8 sequences.
    - Enforces max string scalars (8192), recognizes valid surrogate pairs as 1 scalar, rejects lone surrogates.
    - Enforces max nesting depth (16).
    - Enforces container item counts without off-by-one (512 OK, 513 REJECT).
    - Enforces total node count (2048).
    - Forbids floating point numbers, non-finite constants (NaN, Infinity), and NUL characters.
    - Enforces integer digit bound (19 digits).
    """
    if len(raw_bytes) > max_bytes:
        raise StrictJsonComplexityError(
            f"ERR_JSON_SIZE_EXCEEDED: payload size {len(raw_bytes)} bytes exceeds limit of {max_bytes} bytes"
        )

    try:
        raw_str = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJsonParseError(f"ERR_CANONICAL_UTF8_DECODE: invalid UTF-8 byte sequence: {exc}") from exc

    current_depth = 0
    total_nodes = 0

    container_counts: list[int] = []
    container_has_value: list[bool] = []

    in_string = False
    escape_next = False
    decoded_string_scalars = 0
    current_num_digits = 0
    in_number = False

    idx = 0
    n = len(raw_str)

    while idx < n:
        ch = raw_str[idx]

        # 1. INSIDE STRING
        if in_string:
            if escape_next:
                if ch == "u":
                    if idx + 4 >= n:
                        raise StrictJsonParseError("ERR_CANONICAL_TRUNCATED_ESCAPE: truncated \\u escape at EOF")
                    hex_str = raw_str[idx + 1:idx + 5]
                    try:
                        code_point = int(hex_str, 16)
                    except ValueError as exc:
                        raise StrictJsonParseError(f"ERR_CANONICAL_INVALID_HEX: invalid hex escape: {hex_str}") from exc

                    # Surrogate range check (D800 - DFFF)
                    if 0xD800 <= code_point <= 0xDBFF:
                        # High surrogate: must be followed by low surrogate \uDC00 - \uDFFF
                        if idx + 10 < n and raw_str[idx + 5:idx + 7] == "\\u":
                            low_hex = raw_str[idx + 7:idx + 11]
                            try:
                                low_point = int(low_hex, 16)
                            except ValueError:
                                low_point = 0
                            if 0xDC00 <= low_point <= 0xDFFF:
                                # Valid surrogate pair -> 1 complete Unicode scalar
                                decoded_string_scalars += 1
                                idx += 10
                                escape_next = False
                                if decoded_string_scalars > MAX_STRING_SCALARS:
                                    raise StrictJsonComplexityError(
                                        f"ERR_JSON_STRING_TOO_LONG: string exceeds {MAX_STRING_SCALARS} scalars"
                                    )
                                idx += 1
                                continue
                        raise StrictJsonParseError(
                            f"ERR_CANONICAL_LONE_SURROGATE: lone high surrogate \\u{hex_str} detected"
                        )
                    elif 0xDC00 <= code_point <= 0xDFFF:
                        raise StrictJsonParseError(
                            f"ERR_CANONICAL_LONE_SURROGATE: lone low surrogate \\u{hex_str} detected"
                        )
                    else:
                        decoded_string_scalars += 1

                    idx += 4
                elif ch in '"\\/bfnrt':
                    decoded_string_scalars += 1
                else:
                    raise StrictJsonParseError(f"ERR_CANONICAL_INVALID_ESCAPE: invalid escape character \\{ch}")

                escape_next = False
                if decoded_string_scalars > MAX_STRING_SCALARS:
                    raise StrictJsonComplexityError(
                        f"ERR_JSON_STRING_TOO_LONG: string exceeds {MAX_STRING_SCALARS} scalars"
                    )
            elif ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = False
                total_nodes += 1
                if total_nodes > MAX_TOTAL_NODES:
                    raise StrictJsonComplexityError(
                        f"ERR_JSON_EXCESSIVE_NODES: total nodes exceeds {MAX_TOTAL_NODES}"
                    )
                if container_counts and not container_has_value[-1]:
                    container_counts[-1] = 1
                    container_has_value[-1] = True
            elif ch == "\0":
                raise StrictJsonParseError("ERR_CANONICAL_NUL_CHAR: NUL character inside string")
            else:
                decoded_string_scalars += 1
                if decoded_string_scalars > MAX_STRING_SCALARS:
                    raise StrictJsonComplexityError(
                        f"ERR_JSON_STRING_TOO_LONG: string exceeds {MAX_STRING_SCALARS} scalars"
                    )

            idx += 1
            continue

        # 2. OUTSIDE STRING
        if ch == '"':
            in_string = True
            escape_next = False
            decoded_string_scalars = 0
            in_number = False
        elif ch in "{[":
            current_depth += 1
            if current_depth > MAX_NESTING_DEPTH:
                raise StrictJsonComplexityError(
                    f"ERR_JSON_EXCESSIVE_DEPTH: nesting depth ({current_depth}) exceeds {MAX_NESTING_DEPTH}"
                )
            total_nodes += 1
            if total_nodes > MAX_TOTAL_NODES:
                raise StrictJsonComplexityError(
                    f"ERR_JSON_EXCESSIVE_NODES: total nodes exceeds {MAX_TOTAL_NODES}"
                )
            container_counts.append(0)
            container_has_value.append(False)
            in_number = False
        elif ch in "}]":
            if current_depth <= 0 or not container_counts:
                raise StrictJsonParseError("ERR_JSON_UNBALANCED_BRACKETS: unbalanced closing bracket")
            current_depth -= 1
            final_count = container_counts.pop()
            container_has_value.pop()
            if final_count > MAX_CONTAINER_LENGTH:
                raise StrictJsonComplexityError(
                    f"ERR_JSON_CONTAINER_TOO_LARGE: container contains {final_count} items, exceeding {MAX_CONTAINER_LENGTH}"
                )
            if container_counts and not container_has_value[-1]:
                container_counts[-1] = 1
                container_has_value[-1] = True
            in_number = False
        elif ch == ",":
            if not container_counts:
                raise StrictJsonParseError("ERR_JSON_DANGLING_COMMA: comma outside container")
            container_counts[-1] += 1
            if container_counts[-1] > MAX_CONTAINER_LENGTH:
                raise StrictJsonComplexityError(
                    f"ERR_JSON_CONTAINER_TOO_LARGE: container exceeds {MAX_CONTAINER_LENGTH} items"
                )
            in_number = False
        elif ch in "-0123456789":
            if not in_number:
                in_number = True
                current_num_digits = 0
                total_nodes += 1
                if total_nodes > MAX_TOTAL_NODES:
                    raise StrictJsonComplexityError(
                        f"ERR_JSON_EXCESSIVE_NODES: total nodes exceeds {MAX_TOTAL_NODES}"
                    )
                if container_counts and not container_has_value[-1]:
                    container_counts[-1] = 1
                    container_has_value[-1] = True
            if ch in "0123456789":
                current_num_digits += 1
                if current_num_digits > MAX_INTEGER_DIGITS:
                    raise StrictJsonComplexityError(
                        f"ERR_JSON_INTEGER_TOKEN_TOO_LARGE: integer token exceeds {MAX_INTEGER_DIGITS} digits"
                    )
        elif ch in ".eE":
            raise StrictJsonParseError("ERR_CANONICAL_FLOAT_FORBIDDEN: float numbers forbidden in canonical JSON")
        elif ch.isspace() or ch == ":":
            in_number = False
        else:
            token_rem = raw_str[idx:idx + 10]
            if token_rem.startswith("true"):
                total_nodes += 1
                if container_counts and not container_has_value[-1]:
                    container_counts[-1] = 1
                    container_has_value[-1] = True
                idx += 3
            elif token_rem.startswith("false"):
                total_nodes += 1
                if container_counts and not container_has_value[-1]:
                    container_counts[-1] = 1
                    container_has_value[-1] = True
                idx += 4
            elif token_rem.startswith("null"):
                total_nodes += 1
                if container_counts and not container_has_value[-1]:
                    container_counts[-1] = 1
                    container_has_value[-1] = True
                idx += 3
            elif token_rem.lower().startswith("nan") or token_rem.lower().startswith("infinity"):
                raise StrictJsonParseError(
                    f"ERR_CANONICAL_NON_FINITE_CONSTANT: non-finite constant forbidden at offset {idx}"
                )

            if total_nodes > MAX_TOTAL_NODES:
                raise StrictJsonComplexityError(
                    f"ERR_JSON_EXCESSIVE_NODES: total nodes ({total_nodes}) exceeds {MAX_TOTAL_NODES}"
                )
            in_number = False

        idx += 1

    if in_string:
        raise StrictJsonParseError("ERR_JSON_UNTERMINATED_STRING: unterminated string at EOF")
    if current_depth != 0:
        raise StrictJsonParseError(f"ERR_JSON_UNCLOSED_CONTAINER: {current_depth} unclosed container(s) at EOF")

    return raw_str


def _reject_json_constant(val: str) -> None:
    raise StrictJsonParseError(f"ERR_CANONICAL_NON_FINITE_CONSTANT: non-finite constant forbidden: {val!r}")


def reject_duplicate_json_keys(ordered_pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """Hook for json.loads object_pairs_hook that detects and rejects duplicate JSON keys."""
    result: Dict[str, Any] = {}
    for key, value in ordered_pairs:
        if key in result:
            raise StrictJsonParseError(f"ERR_DUPLICATE_JSON_KEY: duplicate JSON key detected: {key!r}")
        result[key] = value
    return result


def _validate_decoded_graph(
    data: Any,
    path: str = "root",
    depth: int = 0,
    state: Optional[_ValidationState] = None,
) -> None:
    """Recursive post-decode graph validation ensuring no invalid types or range violations."""
    if state is None:
        state = _ValidationState()

    if depth > MAX_NESTING_DEPTH:
        raise StrictJsonComplexityError(f"ERR_JSON_EXCESSIVE_DEPTH: depth ({depth}) exceeds {MAX_NESTING_DEPTH} at {path}")

    state.total_nodes += 1
    if state.total_nodes > MAX_TOTAL_NODES:
        raise StrictJsonComplexityError(f"ERR_JSON_EXCESSIVE_NODES: total nodes exceeds {MAX_TOTAL_NODES} at {path}")

    if type(data) is bool:
        return
    elif type(data) is int:
        if not (MIN_INT64 <= data <= MAX_INT64):
            raise StrictJsonComplexityError(f"ERR_JSON_INTEGER_OUT_OF_RANGE: int {data} outside 64-bit range at {path}")
        return
    elif type(data) is float:
        raise StrictJsonParseError(f"ERR_CANONICAL_FLOAT_FORBIDDEN: float forbidden at {path}")
    elif data is None:
        return
    elif type(data) is str:
        if len(data) > MAX_STRING_SCALARS:
            raise StrictJsonComplexityError(f"ERR_JSON_STRING_TOO_LONG: string exceeds {MAX_STRING_SCALARS} at {path}")
        if "\0" in data:
            raise StrictJsonParseError(f"ERR_CANONICAL_NUL_CHAR: NUL character at {path}")
        try:
            data.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise StrictJsonParseError(f"ERR_CANONICAL_SURROGATE: lone surrogate at {path}: {exc}") from exc
        return
    elif isinstance(data, (list, tuple)):
        if len(data) > MAX_CONTAINER_LENGTH:
            raise StrictJsonComplexityError(f"ERR_JSON_CONTAINER_TOO_LARGE: list exceeds {MAX_CONTAINER_LENGTH} at {path}")
        for idx, item in enumerate(data):
            _validate_decoded_graph(item, f"{path}[{idx}]", depth + 1, state)
        return
    elif isinstance(data, dict):
        if len(data) > MAX_CONTAINER_LENGTH:
            raise StrictJsonComplexityError(f"ERR_JSON_CONTAINER_TOO_LARGE: dict exceeds {MAX_CONTAINER_LENGTH} at {path}")
        for k, v in data.items():
            if type(k) is not str:
                raise StrictJsonParseError(f"ERR_CANONICAL_KEY_TYPE: key at {path} must be str")
            if len(k) > MAX_STRING_SCALARS:
                raise StrictJsonComplexityError(f"ERR_JSON_KEY_TOO_LONG: key exceeds {MAX_STRING_SCALARS} at {path}.{k}")
            if "\0" in k:
                raise StrictJsonParseError(f"ERR_CANONICAL_KEY_NUL: key contains NUL at {path}.{k}")
            try:
                k.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise StrictJsonParseError(f"ERR_CANONICAL_KEY_SURROGATE: lone surrogate in key: {exc}") from exc
            _validate_decoded_graph(v, f"{path}.{k}", depth + 1, state)
        return
    else:
        raise StrictJsonParseError(f"ERR_CANONICAL_UNSUPPORTED_TYPE: unsupported type at {path}: {type(data).__name__}")


def canonical_json_bytes(payload: Dict[str, Any]) -> bytes:
    """Deterministic UTF-8 RFC 8785 canonical JSON serialization."""
    _validate_decoded_graph(payload)
    encoded_str = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return encoded_str.encode("utf-8")


def canonical_json_dumps(data: Any) -> bytes:
    """Alias for canonical_json_bytes with validation."""
    if isinstance(data, dict):
        return canonical_json_bytes(data)
    _validate_decoded_graph(data)
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def parse_strict_canonical_json(raw_bytes: bytes, max_bytes: int = DEFAULT_PRE_PARSE_MAX_BYTES) -> Dict[str, Any]:
    """
    Two-layer defense-in-depth strict canonical JSON parser:
    Layer 1: Pre-parse lexical grammar check preventing parser-bomb attacks.
    Layer 2: json.loads with duplicate-key rejection and post-decode graph validation.
    """
    raw_str = validate_json_structure_pre_parse(raw_bytes, max_bytes=max_bytes)

    try:
        data = json.loads(
            raw_str,
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
            parse_float=lambda val: _reject_json_constant(f"float:{val}"),
        )
    except (json.JSONDecodeError, RecursionError, ValueError, OverflowError) as exc:
        raise StrictJsonParseError(f"ERR_CANONICAL_PARSE_FAILED: JSON decode failed: {exc}") from exc

    if not isinstance(data, dict):
        raise StrictJsonParseError(f"ERR_JSON_NOT_DICT: root must be dict, got {type(data).__name__}")

    _validate_decoded_graph(data)
    return data


def parse_iso_utc_timestamp(ts_str: str, field_name: str) -> datetime:
    """
    Parses and validates an ISO 8601 timestamp strictly in UTC (I1-R1-01, I1-R2-01).
    Requires explicit UTC representation ('Z' or '+00:00' with zero offset).
    Rejects naive timestamps, malformed format, leading/trailing whitespace, non-zero timezone offsets,
    and the RFC 3339 unknown-offset marker '-00:00'.
    Returns timezone-aware UTC datetime normalized to timezone.utc.
    """
    if type(ts_str) is not str or not ts_str:
        raise ReadbackReceiptVerificationError(f"ERR_AUTH_TYPE_GATE: {field_name} must be non-empty str")

    # Reject leading/trailing whitespace strictly (no silent trimming - I1-R2-01)
    if ts_str != ts_str.strip():
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_TIMESTAMP_WHITESPACE: {field_name} '{ts_str}' contains forbidden leading or trailing whitespace"
        )

    # Reject RFC 3339 unknown local offset marker '-00:00' (I1-R2-01)
    if ts_str.endswith("-00:00"):
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_TIMESTAMP_UNKNOWN_OFFSET: {field_name} '{ts_str}' uses unknown-offset marker '-00:00' which is forbidden; must be 'Z' or '+00:00'"
        )

    # Must end with exact accepted UTC suffix 'Z' or '+00:00'
    if not (ts_str.endswith("Z") or ts_str.endswith("+00:00")):
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_TIMESTAMP_NOT_UTC: {field_name} '{ts_str}' must have explicit zero-offset UTC suffix ('Z' or '+00:00')"
        )

    # Validate complete ISO-8601 UTC grammar before datetime parsing
    if not ISO_UTC_TIMESTAMP_REGEX.match(ts_str):
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_TIMESTAMP_MALFORMED: {field_name} '{ts_str}' does not match strict ISO-8601 UTC grammar"
        )

    try:
        dt = datetime.fromisoformat(ts_str)
    except (ValueError, TypeError) as exc:
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_TIMESTAMP_MALFORMED: {field_name} '{ts_str}' is not a valid ISO 8601 timestamp: {exc}"
        ) from exc

    if dt.tzinfo is None:
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_TIMESTAMP_NAIVE: {field_name} '{ts_str}' must not be timezone-naive"
        )

    offset = dt.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_TIMESTAMP_NON_ZERO_OFFSET: {field_name} '{ts_str}' must have zero UTC offset"
        )

    return dt.astimezone(timezone.utc)


# --- Authoritative Authorization Validation Boundary (I1-01, I1-R1-01) ---

def validate_trial_execution_authorization(auth: TrialExecutionAuthorization) -> None:
    """
    Authoritative validation boundary for all 32 signed fields and signature member.
    Enforces exact primitive types (strictly int, not bool), non-empty strings,
    64-char hex digests, physical frame/FPS ranges, scope restrictions,
    and normalized UTC instant chronological validation (I1-R1-01).
    """
    if not isinstance(auth, TrialExecutionAuthorization):
        raise ReadbackReceiptVerificationError(
            "ERR_INVALID_AUTH_OBJECT: auth must be an instance of TrialExecutionAuthorization"
        )

    # 1. Exact Primitive Integer Type Gates (excluding bool)
    int_fields = (
        "target_timeline_fps_numerator",
        "target_timeline_fps_denominator",
        "observed_timeline_start_frame",
        "authorized_record_frame",
        "authorized_track_index",
        "authorized_timeline_frame_start",
        "authorized_timeline_frame_duration",
        "authorized_source_frame_start",
        "authorized_source_frame_duration",
    )
    for k in int_fields:
        if not hasattr(auth, k):
            raise ReadbackReceiptVerificationError(f"ERR_AUTH_MISSING_FIELD: missing required field {k}")
        val = getattr(auth, k)
        if type(val) is not int or isinstance(val, bool):
            raise ReadbackReceiptVerificationError(f"ERR_AUTH_TYPE_GATE: {k} must be int not bool (got {type(val).__name__})")

    # 2. Integer Range Gates
    if auth.target_timeline_fps_numerator <= 0:
        raise ReadbackReceiptVerificationError("ERR_AUTH_RANGE_GATE: target_timeline_fps_numerator must be > 0")
    if auth.target_timeline_fps_denominator <= 0:
        raise ReadbackReceiptVerificationError("ERR_AUTH_RANGE_GATE: target_timeline_fps_denominator must be > 0")
    if auth.observed_timeline_start_frame < 0:
        raise ReadbackReceiptVerificationError("ERR_AUTH_RANGE_GATE: observed_timeline_start_frame must be >= 0")
    if auth.authorized_record_frame < 0:
        raise ReadbackReceiptVerificationError("ERR_AUTH_RANGE_GATE: authorized_record_frame must be >= 0")
    if auth.authorized_track_index < 1:
        raise ReadbackReceiptVerificationError("ERR_AUTH_RANGE_GATE: authorized_track_index must be >= 1")
    if auth.authorized_timeline_frame_start < 0:
        raise ReadbackReceiptVerificationError("ERR_AUTH_RANGE_GATE: authorized_timeline_frame_start must be >= 0")
    if auth.authorized_timeline_frame_duration <= 0:
        raise ReadbackReceiptVerificationError("ERR_AUTH_RANGE_GATE: authorized_timeline_frame_duration must be > 0")
    if auth.authorized_source_frame_start < 0:
        raise ReadbackReceiptVerificationError("ERR_AUTH_RANGE_GATE: authorized_source_frame_start must be >= 0")
    if auth.authorized_source_frame_duration <= 0:
        raise ReadbackReceiptVerificationError("ERR_AUTH_RANGE_GATE: authorized_source_frame_duration must be > 0")

    # 3. Frame Boundary Binding Constraint (07B5 Section 2.3)
    expected_record_frame = auth.observed_timeline_start_frame + auth.authorized_timeline_frame_start
    if auth.authorized_record_frame != expected_record_frame:
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_FRAME_BINDING: authorized_record_frame ({auth.authorized_record_frame}) must equal "
            f"observed_timeline_start_frame ({auth.observed_timeline_start_frame}) + "
            f"authorized_timeline_frame_start ({auth.authorized_timeline_frame_start})"
        )

    # 4. Scope Restrictions (Canary single action)
    if getattr(auth, "authorized_action_type", None) != "INSERT_CLIP":
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_SCOPE_GATE: authorized_action_type must be 'INSERT_CLIP'"
        )
    if getattr(auth, "authorized_track_type", None) != "video":
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_SCOPE_GATE: authorized_track_type must be 'video'"
        )

    # 5. Non-Empty String Type Gates
    str_fields = (
        "authorization_id",
        "key_id",
        "approval_record_id",
        "proposal_id",
        "proposal_revision",
        "plan_idempotency_key",
        "target_project_uid",
        "target_project_name",
        "target_timeline_uid",
        "target_timeline_name",
        "source_file_path",
        "execution_id",
        "issued_at_iso",
        "valid_until_iso",
        "token_nonce",
        "authorization_hmac",
    )
    for k in str_fields:
        if not hasattr(auth, k):
            raise ReadbackReceiptVerificationError(f"ERR_AUTH_MISSING_FIELD: missing required field {k}")
        val = getattr(auth, k)
        if type(val) is not str or not val.strip():
            raise ReadbackReceiptVerificationError(f"ERR_AUTH_TYPE_GATE: {k} must be non-empty str")

    # 6. Cryptographic 64-char Hex Digest Formats
    digest_fields = (
        "operator_gesture_digest_sha256",
        "approval_record_digest_sha256",
        "proposal_content_digest_sha256",
        "plan_digest_sha256",
        "action_payload_digest_sha256",
        "source_file_sha256",
    )
    for k in digest_fields:
        if not hasattr(auth, k):
            raise ReadbackReceiptVerificationError(f"ERR_AUTH_MISSING_FIELD: missing required field {k}")
        val = getattr(auth, k)
        if type(val) is not str or len(val) != 64 or not HEX_CHARS.issuperset(val):
            raise ReadbackReceiptVerificationError(
                f"ERR_AUTH_DIGEST_FORMAT: {k} must be a 64-character hex string (got {val!r})"
            )

    # 7. Execution Identity & Nonce Binding
    if auth.execution_id != auth.token_nonce:
        raise WholePlanReconciliationError(
            f"ERR_EXECUTION_NONCE_MISMATCH: auth.execution_id '{auth.execution_id}' != auth.token_nonce '{auth.token_nonce}'"
        )

    # 8. Timestamp Chronological Sanity & Explicit UTC Instant Validation (I1-R1-01)
    issued_dt = parse_iso_utc_timestamp(auth.issued_at_iso, "issued_at_iso")
    valid_dt = parse_iso_utc_timestamp(auth.valid_until_iso, "valid_until_iso")
    if valid_dt <= issued_dt:
        raise ReadbackReceiptVerificationError(
            f"ERR_AUTH_TIMESTAMP_INVALID: valid_until_iso '{auth.valid_until_iso}' ({valid_dt.isoformat()}) "
            f"must be strictly after issued_at_iso '{auth.issued_at_iso}' ({issued_dt.isoformat()})"
        )


# --- Frozen Contract DTOs ---

@dataclass(frozen=True)
class OperatorConsentGesture:
    """Hồ sơ xác nhận ý chí chủ động của người điều hành dưới phiên làm việc OS hiện hành."""
    operator_id: str                      # OS session user label
    consent_timestamp_iso: str            # ISO 8601 UTC timestamp
    confirmation_phrase: str              # 'CONFIRM_CANARY_TRIAL_EXECUTION'
    freshness_attestation: str            # Attestation of fresh/empty timeline
    challenge_id: str                     # Challenge token
    client_nonce: str                     # UUIDv4 nonce preventing replay

    def __post_init__(self) -> None:
        if type(self.operator_id) is not str or not self.operator_id.strip():
            raise ReadbackReceiptVerificationError("ERR_GESTURE_TYPE_GATE: operator_id must be non-empty str")
        if type(self.confirmation_phrase) is not str or self.confirmation_phrase != "CONFIRM_CANARY_TRIAL_EXECUTION":
            raise ReadbackReceiptVerificationError("ERR_GESTURE_PHRASE: confirmation_phrase must be 'CONFIRM_CANARY_TRIAL_EXECUTION'")
        if type(self.client_nonce) is not str or not self.client_nonce.strip():
            raise ReadbackReceiptVerificationError("ERR_GESTURE_TYPE_GATE: client_nonce must be non-empty str")
        parse_iso_utc_timestamp(self.consent_timestamp_iso, "consent_timestamp_iso")

    def compute_gesture_digest(self) -> str:
        """Compute canonical SHA-256 digest of operator consent gesture."""
        payload = {
            "challenge_id": self.challenge_id,
            "client_nonce": self.client_nonce,
            "confirmation_phrase": self.confirmation_phrase,
            "consent_timestamp_iso": self.consent_timestamp_iso,
            "freshness_attestation": self.freshness_attestation,
            "operator_id": self.operator_id,
        }
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    @property
    def operator_identity(self) -> str:
        return self.operator_id

    @property
    def gesture_id(self) -> str:
        return self.challenge_id

    @property
    def confirmed_at_iso(self) -> str:
        return self.consent_timestamp_iso


@dataclass(frozen=True)
class PlannedTimelineAction:
    """Planned single action on DaVinci Resolve timeline."""
    action_id: str
    action_index: int
    action_type: str
    track_type: str
    track_index: int
    record_frame: int
    duration_frames: int
    source_path: str
    source_frame_start: int
    source_frame_duration: int
    role: str

    def __post_init__(self) -> None:
        # Strict primitive type gates on geometry/track
        for k in ("action_index", "track_index", "record_frame", "duration_frames", "source_frame_start", "source_frame_duration"):
            val = getattr(self, k)
            if type(val) is not int or isinstance(val, bool):
                raise ReadbackReceiptVerificationError(f"ERR_ACTION_TYPE_GATE: {k} must be int not bool")
        for k in ("action_id", "action_type", "track_type", "source_path", "role"):
            val = getattr(self, k)
            if type(val) is not str or not val.strip():
                raise ReadbackReceiptVerificationError(f"ERR_ACTION_TYPE_GATE: {k} must be non-empty str")
        if self.action_index < 0:
            raise ReadbackReceiptVerificationError("ERR_ACTION_RANGE: action_index must be >= 0")
        if self.track_index < 1:
            raise ReadbackReceiptVerificationError("ERR_ACTION_RANGE: track_index must be >= 1")
        if self.duration_frames <= 0 or self.source_frame_duration <= 0:
            raise ReadbackReceiptVerificationError("ERR_ACTION_RANGE: durations must be > 0")

    @property
    def target_track_type(self) -> str:
        return self.track_type

    @property
    def target_track_index(self) -> int:
        return self.track_index

    @property
    def source_file_path(self) -> str:
        return self.source_path


@dataclass(frozen=True)
class TrialExecutionAuthorization:
    """
    Chứng thư ủy quyền can thiệp dùng 1 lần, ràng buộc mật mã toàn bộ ý chí con người và mục tiêu Resolve.
    Contains exactly 32 HMAC-covered fields plus the separate authorization_hmac signature member.
    """
    # 1. Định Danh Chứng Thư & Khóa (2 fields)
    authorization_id: str
    key_id: str

    # 2. Ràng Buộc Ý Chí Con Người & Đề Xuất Sáng Tạo (9 fields)
    operator_gesture_digest_sha256: str
    approval_record_id: str
    approval_record_digest_sha256: str
    proposal_id: str
    proposal_revision: str
    proposal_content_digest_sha256: str
    plan_idempotency_key: str
    plan_digest_sha256: str
    action_payload_digest_sha256: str

    # 3. Ràng Buộc Mục Tiêu Vật Lý Resolve (6 fields)
    target_project_uid: str
    target_project_name: str
    target_timeline_uid: str
    target_timeline_name: str
    target_timeline_fps_numerator: int
    target_timeline_fps_denominator: int

    # 4. Ràng Buộc Khung Hình Gốc Quan Sát & Khung Hình Ủy Quyền (2 fields)
    observed_timeline_start_frame: int
    authorized_record_frame: int

    # 5. Ràng Buộc Tập Tin Nguồn (2 fields)
    source_file_path: str
    source_file_sha256: str

    # 6. Phạm Vi Hành Động Hạn Chế Duy Nhất (7 fields)
    authorized_action_type: str
    authorized_track_type: str
    authorized_track_index: int
    authorized_timeline_frame_start: int
    authorized_timeline_frame_duration: int
    authorized_source_frame_start: int
    authorized_source_frame_duration: int

    # 7. Vòng Đời & Toàn Vẹn Mật Mã (4 signed fields + 1 signature field)
    execution_id: str
    issued_at_iso: str
    valid_until_iso: str
    token_nonce: str

    # Signature member (EXCLUDED from the 32 HMAC-covered fields during signing)
    authorization_hmac: str

    def __post_init__(self) -> None:
        validate_trial_execution_authorization(self)


@dataclass(frozen=True)
class TrialIdentityEnvelope:
    """Identity envelope embedded into trial WAL entries."""
    authorization_id: str
    execution_id: str
    target_project_uid: str
    target_timeline_uid: str
    plan_idempotency_key: str


@dataclass(frozen=True)
class AuthorizationContract:
    """Contract representing a coordinator-validated authorization ready for execution."""
    authorization_id: str
    approval_id: str
    execution_id: str
    plan_idempotency_key: str
    target_project_uid: str
    target_timeline_uid: str
    track_type: str
    track_index: int
    record_frame: int
    duration_frames: int
    action_payload_digest_sha256: str
    key_id: str


@dataclass(frozen=True)
class TrialExecutionReceipt:
    """Biên bản nhận chứng thử nghiệm cô lập trên đĩa (Persisted Trial Receipt DTO)."""
    trial_receipt_version: int
    receipt_id: str
    execution_id: str
    authorization_id: str
    target_project_uid: str
    target_timeline_uid: str
    plan_idempotency_key: str
    execution_receipt: Any = None


@dataclass(frozen=True)
class ReconciliationChallenge:
    """Thử thách hòa giải trạng thái mơ hồ của dòng thời gian."""
    timeline_uid: str
    incident_id: str
    challenge_nonce: str
    issued_at: float
    expires_at: float


@dataclass(frozen=True)
class OperatorReconciliationAuthorization:
    """Ủy quyền hòa giải đã được ký của điều hành viên."""
    timeline_uid: str
    incident_id: str
    challenge_nonce: str
    challenge_expires_at: float
    operator_key_id: str
    operator_identity: str
    operator_confirmation_statement: str
    confirmed_unlocked: bool
    signature_hmac: str


# --- Canonical Payload Assembler & HMAC Serializer ---

def canonical_bytes_for_auth_hmac(auth: TrialExecutionAuthorization) -> bytes:
    """
    Serializes exactly the 32 HMAC-covered fields in alphabetical order.
    The authorization_hmac signature member is STRICTLY EXCLUDED.
    Validates all claims through the authoritative validation boundary first.
    """
    validate_trial_execution_authorization(auth)

    payload: Dict[str, Any] = {
        "action_payload_digest_sha256": auth.action_payload_digest_sha256,
        "approval_record_digest_sha256": auth.approval_record_digest_sha256,
        "approval_record_id": auth.approval_record_id,
        "authorization_id": auth.authorization_id,
        "authorized_action_type": auth.authorized_action_type,
        "authorized_record_frame": auth.authorized_record_frame,
        "authorized_source_frame_duration": auth.authorized_source_frame_duration,
        "authorized_source_frame_start": auth.authorized_source_frame_start,
        "authorized_timeline_frame_duration": auth.authorized_timeline_frame_duration,
        "authorized_timeline_frame_start": auth.authorized_timeline_frame_start,
        "authorized_track_index": auth.authorized_track_index,
        "authorized_track_type": auth.authorized_track_type,
        "execution_id": auth.execution_id,
        "issued_at_iso": auth.issued_at_iso,
        "key_id": auth.key_id,
        "observed_timeline_start_frame": auth.observed_timeline_start_frame,
        "operator_gesture_digest_sha256": auth.operator_gesture_digest_sha256,
        "plan_digest_sha256": auth.plan_digest_sha256,
        "plan_idempotency_key": auth.plan_idempotency_key,
        "proposal_content_digest_sha256": auth.proposal_content_digest_sha256,
        "proposal_id": auth.proposal_id,
        "proposal_revision": auth.proposal_revision,
        "source_file_path": auth.source_file_path,
        "source_file_sha256": auth.source_file_sha256,
        "target_project_name": auth.target_project_name,
        "target_project_uid": auth.target_project_uid,
        "target_timeline_fps_denominator": auth.target_timeline_fps_denominator,
        "target_timeline_fps_numerator": auth.target_timeline_fps_numerator,
        "target_timeline_name": auth.target_timeline_name,
        "target_timeline_uid": auth.target_timeline_uid,
        "token_nonce": auth.token_nonce,
        "valid_until_iso": auth.valid_until_iso,
    }
    return canonical_json_bytes(payload)


def canonical_action_payload_bytes(params: Mapping[str, Any]) -> bytes:
    """
    Single canonical serializer for Canonical Action Dispatch Payload (16 keys).
    Enforces strict primitive type and range gates across all 16 fields.
    """
    if not isinstance(params, (dict, Mapping)):
        raise ReadbackReceiptVerificationError("ERR_ACTION_PAYLOAD_NOT_DICT: payload must be a mapping")

    param_keys = frozenset(params.keys())
    if param_keys != CANONICAL_ACTION_PAYLOAD_KEYS:
        diff_missing = sorted(CANONICAL_ACTION_PAYLOAD_KEYS - param_keys)
        diff_extra = sorted(param_keys - CANONICAL_ACTION_PAYLOAD_KEYS)
        raise ReadbackReceiptVerificationError(
            f"ERR_ACTION_PAYLOAD_SCHEMA_MISMATCH: missing={diff_missing}, extra={diff_extra}"
        )

    # Integer type gates (strict: must be int, not bool)
    int_fields = (
        "action_index",
        "duration_frames",
        "record_frame",
        "source_frame_duration",
        "source_frame_start",
        "track_index",
    )
    for k in int_fields:
        val = params[k]
        if type(val) is not int or isinstance(val, bool):
            raise ReadbackReceiptVerificationError(f"ERR_ACTION_PAYLOAD_TYPE_GATE: {k} must be int not bool")

    # Range gates
    if params["action_index"] < 0:
        raise ReadbackReceiptVerificationError("ERR_ACTION_PAYLOAD_RANGE: action_index must be >= 0")
    if params["track_index"] < 1:
        raise ReadbackReceiptVerificationError("ERR_ACTION_PAYLOAD_RANGE: track_index must be >= 1")
    if params["source_frame_duration"] <= 0 or params["duration_frames"] <= 0:
        raise ReadbackReceiptVerificationError("ERR_ACTION_PAYLOAD_RANGE: frame durations must be > 0")

    # String type gates (must be non-empty str)
    str_fields = (
        "action_type",
        "authorization_id",
        "execution_id",
        "plan_idempotency_key",
        "role",
        "schema_version",
        "source_path",
        "target_project_uid",
        "target_timeline_uid",
        "track_type",
    )
    for k in str_fields:
        val = params[k]
        if not isinstance(val, str) or not val.strip():
            raise ReadbackReceiptVerificationError(f"ERR_ACTION_PAYLOAD_TYPE_GATE: {k} must be non-empty str")

    sorted_dict = {k: params[k] for k in sorted(params.keys())}
    return json.dumps(sorted_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def assemble_canonical_action_payload(
    action: PlannedTimelineAction,
    auth: TrialExecutionAuthorization,
    execution_id: str,
) -> Dict[str, Any]:
    """
    Sole assembler binding PlannedTimelineAction and signed TrialExecutionAuthorization into
    the canonical 16-key dispatch payload.
    Enforces authoritative validation on auth and action before any binding or serialization.
    """
    if not isinstance(auth, TrialExecutionAuthorization):
        raise ReadbackReceiptVerificationError(
            "ERR_INVALID_AUTH_OBJECT: auth must be an instance of TrialExecutionAuthorization"
        )
    validate_trial_execution_authorization(auth)

    if not isinstance(action, PlannedTimelineAction):
        raise ReadbackReceiptVerificationError(
            "ERR_INVALID_ACTION_OBJECT: action must be an instance of PlannedTimelineAction"
        )

    # Invariant: execution_id binding
    if execution_id != auth.execution_id:
        raise WholePlanReconciliationError(
            f"ERR_EXECUTION_IDENTITY_MISMATCH: execution_id '{execution_id}' does not match auth.execution_id '{auth.execution_id}'"
        )
    if auth.execution_id != auth.token_nonce:
        raise WholePlanReconciliationError(
            f"ERR_EXECUTION_NONCE_MISMATCH: auth.execution_id '{auth.execution_id}' != auth.token_nonce '{auth.token_nonce}'"
        )

    # Invariant: action geometric and target binding against auth (with strict type checks)
    if action.action_index < 0:
        raise ReadbackReceiptVerificationError("ERR_ACTION_INDEX_NEGATIVE: action_index must be >= 0")
    if action.action_type != auth.authorized_action_type:
        raise ReadbackReceiptVerificationError(
            f"ERR_ACTION_TYPE_MISMATCH: action {action.action_type!r} != auth {auth.authorized_action_type!r}"
        )
    if action.track_type != auth.authorized_track_type:
        raise ReadbackReceiptVerificationError(
            f"ERR_TRACK_TYPE_MISMATCH: action {action.track_type!r} != auth {auth.authorized_track_type!r}"
        )
    if type(action.track_index) is not int or isinstance(action.track_index, bool) or action.track_index != auth.authorized_track_index:
        raise ReadbackReceiptVerificationError(
            f"ERR_TRACK_INDEX_MISMATCH: action {action.track_index} != auth {auth.authorized_track_index}"
        )
    if action.source_path != auth.source_file_path:
        raise ReadbackReceiptVerificationError(
            f"ERR_SOURCE_PATH_MISMATCH: action {action.source_path!r} != auth {auth.source_file_path!r}"
        )
    if type(action.source_frame_start) is not int or isinstance(action.source_frame_start, bool) or action.source_frame_start != auth.authorized_source_frame_start:
        raise ReadbackReceiptVerificationError(
            f"ERR_SOURCE_START_MISMATCH: action {action.source_frame_start} != auth {auth.authorized_source_frame_start}"
        )
    if type(action.source_frame_duration) is not int or isinstance(action.source_frame_duration, bool) or action.source_frame_duration != auth.authorized_source_frame_duration:
        raise ReadbackReceiptVerificationError(
            f"ERR_SOURCE_DURATION_MISMATCH: action {action.source_frame_duration} != auth {auth.authorized_source_frame_duration}"
        )
    if type(action.duration_frames) is not int or isinstance(action.duration_frames, bool) or action.duration_frames != auth.authorized_timeline_frame_duration:
        raise ReadbackReceiptVerificationError(
            f"ERR_TIMELINE_DURATION_MISMATCH: action {action.duration_frames} != auth {auth.authorized_timeline_frame_duration}"
        )
    if type(action.record_frame) is not int or isinstance(action.record_frame, bool) or action.record_frame != auth.authorized_record_frame:
        raise ReadbackReceiptVerificationError(
            f"ERR_RECORD_FRAME_MISMATCH: action {action.record_frame} != auth {auth.authorized_record_frame}"
        )

    payload: Dict[str, Any] = {
        "action_index": action.action_index,
        "action_type": action.action_type,
        "authorization_id": auth.authorization_id,
        "duration_frames": action.duration_frames,
        "execution_id": execution_id,
        "plan_idempotency_key": auth.plan_idempotency_key,
        "record_frame": action.record_frame,
        "role": action.role,
        "schema_version": "1.0",
        "source_frame_duration": action.source_frame_duration,
        "source_frame_start": action.source_frame_start,
        "source_path": action.source_path,
        "target_project_uid": auth.target_project_uid,
        "target_timeline_uid": auth.target_timeline_uid,
        "track_index": action.track_index,
        "track_type": action.track_type,
    }

    # Validate type and range gates immediately
    canonical_action_payload_bytes(payload)
    return payload
