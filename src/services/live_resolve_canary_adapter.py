"""
src/services/live_resolve_canary_adapter.py
Live Resolve Canary Adapter Implementation Slice (Gate 3 - G07-07B7-LIVE-IMPL Revision R1).

Architectural Reference:
  - docs/architecture/07B7-live-canary-readiness-plan.md (Revision R7, Sections 1.1, 4, 5.2, 5.3, 8, 9)
  - docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md (Sections 2.3, 2.6)
  - docs/architecture/07B7-LIVE-IMPL-code-review.md (Codex Findings LIVE-IMPL-01..06)
  - Workorder: .bridge/task.md (G07-07B7-LIVE-IMPL)

Safety Commitments:
  - Separate authority boundary: Distinct module, does not mutate or weaken IsolatedResolveTrialAdapter,
    ResolveExecutionEngine, _validate_mock_gateway_admission(), or ENGINE_MODE.
  - Zero live Resolve execution, zero IPC, zero network, zero AI in this implementation slice.
  - Two-tier tripwire enforcement (07B7 §5.2.1):
      Tier 1 (Admission Gate): Project name regex, uppercase 16-hex challenge nonce binding,
                               exact project UID equality, exact timeline UID equality.
      Tier 2 (Pre-IPC Boundary): Re-queries fresh active project UID, active timeline UID, and
                                  timeline GetStartFrame() immediately before any dispatch call.
  - Canonical single-action binding: Exactly 1 action (INSERT_CLIP) on Video Track 1, 120 frames @ 24 fps,
    source_frame_start=0, timeline_frame_start=0, authorized_record_frame=auth.observed_timeline_start_frame+0.
  - 16-key canonical payload digest verification gate before dispatch.
  - Source media item binding (LIVE-IMPL-03): Disallow fake-item fallback; strictly verify Resolve-owned
    media pool item canonical file path, content digest, and clip name against authorized payload;
    bind physical readback to authorized clip identity.
  - Three discrete terminal branches (07B5 §2.6, 07B7 §5.2.2):
      Branch 1: Verified success -> WAL FINISH(COMPLETED) -> Receipt COMPLETED -> Ledger EXHAUSTED(COMPLETED) -> Lock RELEASED.
      Branch 2: Proven clean failure (Pre-dispatch abort OR 07B5 Two-Stage Independent Proof) ->
                WAL FINISH(ABORTED) -> Receipt ABORTED -> Ledger EXHAUSTED(ABORTED) -> Lock RELEASED.
      Branch 3: In-doubt / ambiguous failure -> NO WAL FINISH -> Receipt EXECUTING -> Ledger ACTIVE ->
                Lock IN_DOUBT_LOCKED -> ZERO REDISPATCH.
  - Clean-proof terminalization authority (LIVE-IMPL-01): Consumes only coordinator-verified capability
    produced after descriptor-backed record loading, RFC 8785 HMAC/schema verification, and full binding
    to current authorization, action, and pending journal entry.
  - Zero redispatch on crash & restart-repair state machine (LIVE-IMPL-02, 07B7 §5.3):
      Pre-existing ACTIVE or RESERVED ledger rejects normal dispatch fail-closed.
      Dedicated execute_restart_repair() reconciles every crash boundary without dispatching.
  - Authoritative Two-Stage Proof Verification (LIVE-IMPL-04, 07B5 §3.3.4, 07B7 §5.2.2):
      Stage 1: Raw proof 22-field validation, strict UTC grammar, 300s TTL, unconditional action_id equality,
               full identity, source, track, and geometry projection validation, canonical RFC 8785 HMAC,
               and consume-once atomic publication of 16-field DurableProofRecord.
      Stage 2: Restart repair verification of 16-field DurableProofRecord with mandatory authorization,
               unconditional action_id binding, without re-applying 300s TTL to consumed_at_iso.
  - Mandatory durable proof ledger (LIVE-IMPL-05): Coordinator-owned, descriptor-backed, atomic publication,
    consume-once destination creation without overwrite, directory fsync, and bounded reader.
  - Descriptor-backed durable receipt contract (LIVE-IMPL-06, 07B5 §2.6 R26-04):
      Relative no-follow create/publish/read via directory descriptor, bounded reader and write-all loop,
      file and directory fsync, and 100% full deserialized DTO equality readback.
"""

from __future__ import annotations

import dataclasses
import ctypes
import errno
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Union

from src.core.contracts.execution_receipt_contracts import (
    ExecutionReceipt,
    JournalEntry,
    receipt_from_json,
)
from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    AuthorizationContract,
    PlannedTimelineAction,
    ReadbackReceiptVerificationError,
    TargetExclusionLockError,
    TargetLockReleaseError,
    TrialExecutionAuthorization,
    TrialExecutionReceipt,
    WholePlanReconciliationError,
    assemble_canonical_action_payload,
    canonical_action_payload_bytes,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
    CANONICAL_ACTION_PAYLOAD_KEYS,
)
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)
from src.core.contracts.oracle_ipc_contracts import (
    OracleVerificationRequest,
    OracleVerificationResponse,
)
from src.services.durable_dispatch_journal import (
    DurableDispatchJournal,
    DispatchJournalError,
)
from src.core.timeline_plan_execution_binder import (
    TimelineExecutionPlan,
    compute_canonical_plan_digest,
)

_CANONICAL_ED25519_KEY_TYPE = type(Ed25519PublicKey.from_public_bytes(b"\x00" * 32))

# =====================================================================
# Constants & Validation Rules (07B7 §4.1, §4.2, §5.2.1, 07B5 §2.6)
# =====================================================================

RE_PROJECT_NAME = re.compile(r"^RF_TRIAL_[0-9A-Z_]{8,32}$")
RE_CHALLENGE_NONCE = re.compile(r"^[0-9A-F]{16}$")
RE_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
RE_UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

CANONICAL_ACTION_TYPE = "INSERT_CLIP"
CANONICAL_TRACK_TYPE = "video"
CANONICAL_TRACK_INDEX = 1
CANONICAL_SOURCE_FRAME_START = 0
CANONICAL_SOURCE_FRAME_DURATION = 120
CANONICAL_TIMELINE_FRAME_START = 0
CANONICAL_TIMELINE_FRAME_DURATION = 120
CANONICAL_FPS_NUMERATOR = 24
CANONICAL_FPS_DENOMINATOR = 1

MAX_RECEIPT_BYTES = 5 * 1024 * 1024  # 5 MiB cap (07B5 §2.6 R26-04, LIVE-IMPL-06)
MAX_PROOF_RECORD_BYTES = 1024 * 1024  # 1 MiB cap for durable proof record
RAW_PROOF_FRESHNESS_TTL_SECONDS = 300.0  # 300s TTL for Stage 1 raw proof only

# Exact 22 keys for raw readback proof (07B5 §2.6 Subsection 9, Lines 2358-2430)
EXACT_22_PROOF_KEYS: frozenset[str] = frozenset({
    "proof_schema_version",
    "observer_identity",
    "source_file_path",
    "target_project_uid",
    "target_timeline_uid",
    "target_track_type",
    "key_id",
    "proof_nonce",
    "action_id",
    "action_payload_digest_sha256",
    "proof_hmac_sha256",
    "action_index",
    "record_in_frame",
    "record_out_frame",
    "scanned_frame_start",
    "scanned_frame_end",
    "source_in_frame",
    "source_out_frame",
    "target_track_index",
    "target_item_found",
    "adjacent_occupancy_clean",
    "observation_timestamp_iso",
})

# Exact 16 keys for DurableProofRecord (07B5 §2.6 Subsection 10, 07B7 §5.2.2 Stage 2)
EXACT_16_RECORD_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "proof_record_id",
    "execution_id",
    "action_index",
    "authorization_id",
    "action_payload_digest_sha256",
    "target_project_uid",
    "target_timeline_uid",
    "scanned_frame_start",
    "scanned_frame_end",
    "verified_outcome",
    "raw_proof_id",
    "raw_proof_digest_sha256",
    "consumed_at_iso",
    "signer_key_id",
    "record_hmac_sha256",
})

# Canonical 16 keys for action dispatch payload (07B5 §2.6, 07B7 §5.2.2, LIVE-IMPL-R3-02)
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

def canonical_canary_plan_digest_sha256(plan: TimelineExecutionPlan) -> str:
    """
    Call-through to binder-owned compute_canonical_plan_digest (Workorder R7 §2.3).
    Computes the canonical SHA-256 digest of the actual TimelineExecutionPlan (RFC 8785).
    """
    return compute_canonical_plan_digest(plan)

# Exact 8 keys for TrialExecutionReceipt (07B5 §2.6, LIVE-IMPL-06, LIVE-IMPL-R1-05)
EXACT_RECEIPT_KEYS: frozenset[str] = frozenset({
    "trial_receipt_version",
    "receipt_id",
    "execution_id",
    "authorization_id",
    "target_project_uid",
    "target_timeline_uid",
    "plan_idempotency_key",
    "execution_receipt",
})

# Exact 6 keys for execution_receipt dict inside TrialExecutionReceipt (LIVE-IMPL-R1-05)
EXACT_EXECUTION_RECEIPT_KEYS: frozenset[str] = frozenset({
    "execution_id",
    "state",
    "completed_at_iso",
    "last_error_code",
    "last_error_message",
    "published_at_iso",
})


# =====================================================================
# Structured Exception Hierarchy
# =====================================================================

class LiveCanaryAdapterError(WholePlanReconciliationError):
    """Base error for LiveResolveCanaryAdapter operations."""
    pass


class TargetProjectMismatchError(LiveCanaryAdapterError):
    """Raised when active project identity does not match authorized project UID."""
    pass


class TargetTimelineMismatchError(LiveCanaryAdapterError):
    """Raised when active timeline identity does not match authorized timeline UID."""
    pass


class TargetTimelineShiftedError(LiveCanaryAdapterError):
    """Raised when active timeline GetStartFrame() has drifted before dispatch."""
    pass


class LiveAdmissionTripwireError(LiveCanaryAdapterError):
    """Raised when canary admission checks (project name, nonce, action geometry) fail."""
    pass


class PhysicalReadbackVerificationError(LiveCanaryAdapterError):
    """Raised when post-dispatch timeline readback fails geometry, item count, or media checks."""
    pass


class InDoubtLatchError(LiveCanaryAdapterError):
    """Raised when post-dispatch ambiguity requires latching IN_DOUBT_LOCKED."""
    pass


class ReceiptDurabilityError(LiveCanaryAdapterError):
    """Raised when receipt publication or directory durability fsync fails."""
    pass


# =====================================================================
# Helper: Safe Write-All and Descriptor Bounded Reader
# =====================================================================

def _write_all(fd: int, data: bytes) -> None:
    """Handles partial writes cleanly until all bytes are written."""
    view = memoryview(data)
    total_written = 0
    while total_written < len(data):
        written = os.write(fd, view[total_written:])
        if written == 0:
            raise OSError("write() returned 0 bytes")
        total_written += written


def _read_bytes_bounded(fd: int, max_bytes: int) -> bytes:
    """
    Descriptor-backed bounded reader (07B5 R26-04, LIVE-IMPL-06).
    Validates S_ISREG, checks st_size <= max_bytes, reads chunked,
    and enforces st_dev/st_ino/st_size invariant before and after reading.
    """
    st_initial = os.fstat(fd)
    if not stat.S_ISREG(st_initial.st_mode):
        raise ReadbackReceiptVerificationError("ERR_DESCRIPTOR_NOT_REGULAR_FILE: descriptor is not a regular file")
    if st_initial.st_size > max_bytes:
        raise ReadbackReceiptVerificationError(
            f"ERR_DESCRIPTOR_FILE_TOO_LARGE: file size {st_initial.st_size} exceeds cap {max_bytes}"
        )

    os.lseek(fd, 0, os.SEEK_SET)
    chunks: List[bytes] = []
    total_read = 0
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        total_read += len(chunk)
        if total_read > max_bytes:
            raise ReadbackReceiptVerificationError("ERR_DESCRIPTOR_READ_OVER_MAX_BYTES: read exceeded maximum byte cap")
        chunks.append(chunk)

    st_final = os.fstat(fd)
    if (
        st_final.st_dev != st_initial.st_dev
        or st_final.st_ino != st_initial.st_ino
        or st_final.st_size != st_initial.st_size
    ):
        raise ReadbackReceiptVerificationError("ERR_DESCRIPTOR_STAT_INVARIANT_VIOLATION: file mutated during bounded read")

    return b"".join(chunks)


# =====================================================================
# Durable Proof Record DTO (16 fields - 07B5 / 07B7 §5.2.2 Stage 2)
# =====================================================================

@dataclass(frozen=True)
class DurableProofRecord:
    """
    Authoritative 16-field durable proof record persisted to disk
    upon consumption of an independently verified raw proof (07B5 §2.6, 07B7 §5.2.2).
    """
    schema_version: str
    proof_record_id: str
    execution_id: str
    action_index: int
    authorization_id: str
    action_payload_digest_sha256: str
    target_project_uid: str
    target_timeline_uid: str
    scanned_frame_start: int
    scanned_frame_end: int
    verified_outcome: str
    raw_proof_id: str
    raw_proof_digest_sha256: str
    consumed_at_iso: str
    signer_key_id: str
    record_hmac_sha256: str

    def canonical_bytes_for_hmac(self) -> bytes:
        """Serializes first 15 fields using canonical RFC 8785 JSON bytes for HMAC verification."""
        fields_15: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "proof_record_id": self.proof_record_id,
            "execution_id": self.execution_id,
            "action_index": self.action_index,
            "authorization_id": self.authorization_id,
            "action_payload_digest_sha256": self.action_payload_digest_sha256,
            "target_project_uid": self.target_project_uid,
            "target_timeline_uid": self.target_timeline_uid,
            "scanned_frame_start": self.scanned_frame_start,
            "scanned_frame_end": self.scanned_frame_end,
            "verified_outcome": self.verified_outcome,
            "raw_proof_id": self.raw_proof_id,
            "raw_proof_digest_sha256": self.raw_proof_digest_sha256,
            "consumed_at_iso": self.consumed_at_iso,
            "signer_key_id": self.signer_key_id,
        }
        return canonical_json_bytes(fields_15)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DurableProofRecord:
        if not isinstance(data, dict):
            raise ReadbackReceiptVerificationError(f"ERR_DURABLE_PROOF_NOT_DICT: got {type(data).__name__}")
        if set(data.keys()) != EXACT_16_RECORD_KEYS:
            diff_m = EXACT_16_RECORD_KEYS - set(data.keys())
            diff_e = set(data.keys()) - EXACT_16_RECORD_KEYS
            raise ReadbackReceiptVerificationError(
                f"ERR_DURABLE_PROOF_SCHEMA_MISMATCH: missing={diff_m}, extra={diff_e}"
            )
        # Type & bound validation for all 16 fields without type coercion (LIVE-IMPL-R3-03)
        int_fields = ("action_index", "scanned_frame_start", "scanned_frame_end")
        for k in int_fields:
            val = data[k]
            if type(val) is not int or isinstance(val, bool):
                raise ReadbackReceiptVerificationError(
                    f"ERR_DURABLE_PROOF_TYPE_MISMATCH: field '{k}' must be int, got {type(val).__name__}"
                )
            if k == "action_index" and val < 0:
                raise ReadbackReceiptVerificationError("ERR_DURABLE_PROOF_BOUNDS: action_index must be >= 0")
            if k in ("scanned_frame_start", "scanned_frame_end") and val < 0:
                raise ReadbackReceiptVerificationError(f"ERR_DURABLE_PROOF_BOUNDS: {k} must be >= 0")
        if data["scanned_frame_end"] < data["scanned_frame_start"]:
            raise ReadbackReceiptVerificationError("ERR_DURABLE_PROOF_BOUNDS: scanned_frame_end < scanned_frame_start")

        str_fields = EXACT_16_RECORD_KEYS - set(int_fields)
        for k in str_fields:
            val = data[k]
            if type(val) is not str:
                raise ReadbackReceiptVerificationError(
                    f"ERR_DURABLE_PROOF_TYPE_MISMATCH: field '{k}' must be str, got {type(val).__name__}"
                )
            if not val.strip():
                raise ReadbackReceiptVerificationError(
                    f"ERR_DURABLE_PROOF_EMPTY_FIELD: field '{k}' cannot be empty or whitespace"
                )

        return cls(
            schema_version=data["schema_version"],
            proof_record_id=data["proof_record_id"],
            execution_id=data["execution_id"],
            action_index=data["action_index"],
            authorization_id=data["authorization_id"],
            action_payload_digest_sha256=data["action_payload_digest_sha256"],
            target_project_uid=data["target_project_uid"],
            target_timeline_uid=data["target_timeline_uid"],
            scanned_frame_start=data["scanned_frame_start"],
            scanned_frame_end=data["scanned_frame_end"],
            verified_outcome=data["verified_outcome"],
            raw_proof_id=data["raw_proof_id"],
            raw_proof_digest_sha256=data["raw_proof_digest_sha256"],
            consumed_at_iso=data["consumed_at_iso"],
            signer_key_id=data["signer_key_id"],
            record_hmac_sha256=data["record_hmac_sha256"],
        )


# =====================================================================
# Fake Resolve Client Hierarchy for Testing and Duck-Typing
# =====================================================================

class FakeTimelineItem:
    """Mock timeline item on a track with GetStart, GetDuration, GetEnd, GetName, GetMediaPoolItem."""

    def __init__(
        self,
        start: int = 0,
        duration: int = 120,
        name: Optional[str] = None,
        media_pool_item: Optional[Any] = None,
        end: Optional[int] = None,
        unique_id: str = "canary_item_uid_001",
    ) -> None:
        self._start = start
        self._duration = duration
        if name is not None:
            self._name = name
        elif media_pool_item is not None and hasattr(media_pool_item, "GetName"):
            self._name = media_pool_item.GetName()
        else:
            self._name = "clip_01.mov"
        self._media_pool_item = media_pool_item
        self._end = end if end is not None else (start + duration)
        self._unique_id = unique_id

    def GetStart(self) -> int:
        return self._start

    def GetDuration(self) -> int:
        return self._duration

    def GetEnd(self) -> int:
        return self._end

    def GetName(self) -> str:
        return self._name

    def GetUniqueId(self) -> str:
        return self._unique_id

    def GetMediaPoolItem(self) -> Optional[Any]:
        return self._media_pool_item

    @property
    def name(self) -> str:
        return self._name

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def media_pool_item(self) -> Optional[Any]:
        return self._media_pool_item


class FakeTimeline:
    """Mock DaVinci Resolve Timeline supporting GetUniqueId, GetStartFrame, GetItemListInTrack."""

    def __init__(
        self,
        timeline_uid: str = "",
        start_frame: int = 0,
        items_by_track: Optional[Dict[Tuple[str, int], List[FakeTimelineItem]]] = None,
        name: str = "Canary Timeline",
        unique_id: Optional[str] = None,
    ) -> None:
        self._timeline_uid = unique_id or timeline_uid
        self._name = name
        self._start_frame = start_frame
        self._items_by_track: Dict[Tuple[str, int], List[FakeTimelineItem]] = (
            items_by_track if items_by_track is not None else {}
        )

    def GetUniqueId(self) -> str:
        return self._timeline_uid

    def GetName(self) -> str:
        return self._name

    def GetStartFrame(self) -> int:
        return self._start_frame

    def SetStartFrame(self, start_frame: int) -> None:
        self._start_frame = start_frame

    def GetItemListInTrack(self, track_type: str, track_index: int) -> List[FakeTimelineItem]:
        return list(self._items_by_track.get((track_type, track_index), []))

    def SetItemListInTrack(
        self, track_type: str, track_index: int, items: List[FakeTimelineItem]
    ) -> None:
        self._items_by_track[(track_type, track_index)] = list(items)

    def set_track_items(self, track_type: str, track_index: int, items: List[FakeTimelineItem]) -> None:
        self.SetItemListInTrack(track_type, track_index, items)


class FakeMediaPoolItem:
    """Mock MediaPoolItem with GetClipProperty('File Path') and GetName()."""

    def __init__(self, file_path: str = "/media/canary/clip_01.mov", name: Optional[str] = None) -> None:
        self._file_path = file_path
        self._name = name if name is not None else os.path.basename(file_path)

    def GetClipProperty(self, property_name: str = "File Path") -> str:
        if property_name == "File Path":
            return self._file_path
        if property_name == "Clip Name":
            return self._name
        return ""

    def GetName(self) -> str:
        return self._name

    @property
    def file_path(self) -> str:
        return self._file_path

    @property
    def name(self) -> str:
        return self._name


class FakeFolder:
    """Mock MediaPool Folder supporting GetClipList."""

    def __init__(self, clips: Optional[List[Any]] = None) -> None:
        self._clips = clips or []

    def GetClipList(self) -> List[Any]:
        return list(self._clips)


class FakeMediaPool:
    """Mock MediaPool supporting AppendToTimeline and GetRootFolder."""

    def __init__(
        self,
        target_timeline: Optional[FakeTimeline] = None,
        configured_exception: Optional[Exception] = None,
        clips: Optional[List[Any]] = None,
    ) -> None:
        self._target_timeline = target_timeline
        self.append_specs_received: List[Dict[str, Any]] = []
        self.should_raise_on_append: Optional[Exception] = configured_exception
        default_clip = FakeMediaPoolItem("/media/canary/clip_01.mov")
        self._clips = clips if clips is not None else [default_clip]
        self._root_folder = FakeFolder(self._clips)

    def GetRootFolder(self) -> FakeFolder:
        return self._root_folder

    def GetClipList(self) -> List[Any]:
        return list(self._clips)

    def AppendToTimeline(self, clip_infos: List[Dict[str, Any]]) -> List[FakeTimelineItem]:
        if self.should_raise_on_append is not None:
            raise self.should_raise_on_append

        self.append_specs_received.extend(clip_infos)
        created_items: List[FakeTimelineItem] = []
        for info in clip_infos:
            track_idx = info.get("trackIndex", 1)
            rec_frame = info.get("recordFrame", 0)
            media_item = info.get("mediaPoolItem")
            start = info.get("startFrame", 0)
            end = info.get("endFrame", 120)
            duration = end - start
            item_name = media_item.GetName() if (media_item and hasattr(media_item, "GetName")) else "clip_01.mov"
            item = FakeTimelineItem(
                start=rec_frame,
                duration=duration,
                name=item_name,
                media_pool_item=media_item,
                unique_id="canary_item_uid_001",
            )
            created_items.append(item)
        return created_items


class FakeProject:
    """Mock DaVinci Resolve Project supporting GetUniqueId, GetName, GetCurrentTimeline, GetMediaPool."""

    def __init__(
        self,
        project_uid: str = "",
        project_name: str = "",
        current_timeline: Optional[FakeTimeline] = None,
        name: Optional[str] = None,
        unique_id: Optional[str] = None,
        timeline: Optional[FakeTimeline] = None,
        media_pool: Optional[Any] = None,
    ) -> None:
        self._project_uid = unique_id or project_uid
        self._project_name = name or project_name
        self._current_timeline = timeline or current_timeline
        if media_pool is not None:
            self._media_pool = media_pool
            if hasattr(media_pool, "_target_timeline") and media_pool._target_timeline is None:
                media_pool._target_timeline = self._current_timeline
        else:
            self._media_pool = FakeMediaPool(self._current_timeline) if self._current_timeline else None

    def GetUniqueId(self) -> str:
        return self._project_uid

    def GetName(self) -> str:
        return self._project_name

    def GetCurrentTimeline(self) -> Optional[FakeTimeline]:
        return self._current_timeline

    def SetCurrentTimeline(self, timeline: Optional[FakeTimeline]) -> None:
        self._current_timeline = timeline
        if timeline is not None:
            self._media_pool = FakeMediaPool(timeline)

    def GetMediaPool(self) -> Optional[FakeMediaPool]:
        return self._media_pool


class FakeProjectManager:
    """Mock ProjectManager supporting GetCurrentProject."""

    def __init__(self, project: Optional[FakeProject] = None) -> None:
        self._project = project

    def GetCurrentProject(self) -> Optional[FakeProject]:
        return self._project

    def SetCurrentProject(self, project: Optional[FakeProject]) -> None:
        self._project = project


class FakeResolveClient:
    """Mock Resolve root object implementing GetProjectManager."""

    def __init__(self, project: Optional[FakeProject] = None) -> None:
        self._project_manager = FakeProjectManager(project)

    def GetProjectManager(self) -> FakeProjectManager:
        return self._project_manager


def _atomic_link_or_rename_noreplace(
    dir_fd: int,
    tmp_name: str,
    dest_name: str,
) -> None:
    """
    Publishes an already completed and fsynced temporary file atomically with consume-once
    no-replace semantics. Uses os.link() relative to dir_fd. If os.link fails with
    ENOSYS or EXDEV, attempts Linux renameat2(..., RENAME_NOREPLACE).
    If destination already exists, raises ReadbackReceiptVerificationError('ERR_PROOF_ALREADY_CONSUMED').
    If atomic no-replace publication is unavailable, fails closed without exposing a partial destination.
    """
    try:
        os.link(
            tmp_name,
            dest_name,
            src_dir_fd=dir_fd,
            dst_dir_fd=dir_fd,
        )
        return
    except (FileExistsError, OSError) as link_err:
        if getattr(link_err, "errno", None) == errno.EEXIST or isinstance(link_err, FileExistsError):
            raise ReadbackReceiptVerificationError(
                f"ERR_PROOF_ALREADY_CONSUMED: destination '{dest_name}' already exists on disk"
            ) from link_err
        if getattr(link_err, "errno", None) not in (errno.ENOSYS, errno.EXDEV) and not isinstance(link_err, AttributeError):
            raise

    # Fallback to libc renameat2 with RENAME_NOREPLACE (flags=1) on the completed, fsynced temp file
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if hasattr(libc, "renameat2"):
            libc.renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            libc.renameat2.restype = ctypes.c_int
            res = libc.renameat2(
                dir_fd,
                tmp_name.encode("utf-8"),
                dir_fd,
                dest_name.encode("utf-8"),
                1,  # RENAME_NOREPLACE
            )
            if res == 0:
                return
            err = ctypes.get_errno()
            if err == errno.EEXIST:
                raise ReadbackReceiptVerificationError(
                    f"ERR_PROOF_ALREADY_CONSUMED: destination '{dest_name}' already exists on disk"
                )
            raise OSError(err, os.strerror(err))
        raise NotImplementedError("libc does not have renameat2")
    except Exception as fallback_err:
        if isinstance(fallback_err, ReadbackReceiptVerificationError):
            raise
        # Fail closed without creating or exposing a partially written final file!
        raise AcceptanceGateError(
            f"ERR_ATOMIC_NO_REPLACE_UNSUPPORTED: atomic no-replace publication primitive unavailable: {fallback_err}"
        ) from fallback_err


# =====================================================================
# LiveResolveCanaryAdapter Implementation
# =====================================================================

class LiveResolveCanaryAdapter:
    """
    Live Resolve Canary Adapter (Gate 3 - G07-07B7-LIVE-IMPL Revision R1).

    Enforces the single-action canary execution envelope against an injected
    Resolve scripting client interface with two-tier tripwires, canonical action
    binding, physical timeline read-back, durable WAL state machine, and three
    discrete terminal branches.
    """

    def __init__(
        self,
        *,
        journal: DurableDispatchJournal,
        resolve_client: Any,
        pinned_oracle_public_key: Ed25519PublicKey,
        proof_ledger_dir: Optional[str] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if not isinstance(journal, DurableDispatchJournal):
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_JOURNAL: journal must be an instance of DurableDispatchJournal, got {type(journal).__name__}"
            )
        if resolve_client is None:
            raise LiveAdmissionTripwireError(
                "ERR_RESOLVE_CLIENT_REQUIRED: resolve_client must not be None"
            )
        if type(pinned_oracle_public_key) is not _CANONICAL_ED25519_KEY_TYPE:
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_ORACLE_KEY_TYPE: pinned_oracle_public_key must be exact backend Ed25519PublicKey, got {type(pinned_oracle_public_key)}"
            )
        try:
            raw_key_bytes = pinned_oracle_public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            if len(raw_key_bytes) != 32:
                raise ValueError("Raw public key length is not 32 bytes")
            self._pinned_oracle_public_key = Ed25519PublicKey.from_public_bytes(raw_key_bytes)
        except Exception as exc:
            raise ReadbackReceiptVerificationError(f"ERR_INVALID_ORACLE_KEY_ENCODING: {exc}") from exc

        if clock is not None and not callable(clock):
            raise TypeError("ERR_INVALID_CLOCK: clock must be a callable returning datetime")
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))

        self._journal = journal
        self._resolve_client = resolve_client
        self._proof_ledger_dir = proof_ledger_dir
        self._oracle_response: Optional[OracleVerificationResponse] = None
        self._oracle_request: Optional[OracleVerificationRequest] = None
        self._expected_request_id: Optional[str] = None
        self._expected_request_nonce: Optional[str] = None

        self._proof_dir_fd: Optional[int] = None
        if proof_ledger_dir is not None and isinstance(proof_ledger_dir, str) and proof_ledger_dir.strip():
            os.makedirs(proof_ledger_dir, exist_ok=True)
            dir_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            self._proof_dir_fd = os.open(proof_ledger_dir, dir_flags)
        self._consumed_raw_proof_ids: Set[str] = set()

    def close(self) -> None:
        """Closes proof directory descriptor safely."""
        if getattr(self, "_proof_dir_fd", None) is not None:
            try:
                os.close(self._proof_dir_fd)
            finally:
                self._proof_dir_fd = None

    @property
    def journal(self) -> DurableDispatchJournal:
        return self._journal

    @property
    def pinned_oracle_public_key(self) -> Ed25519PublicKey:
        return self._pinned_oracle_public_key

    @property
    def resolve_client(self) -> Any:
        return self._resolve_client

    @property
    def proof_ledger_dir(self) -> Optional[str]:
        return self._proof_ledger_dir

    @property
    def clock(self) -> Callable[[], datetime]:
        return self._clock

    @property
    def oracle_response(self) -> Optional[OracleVerificationResponse]:
        return self._oracle_response

    @property
    def oracle_request(self) -> Optional[OracleVerificationRequest]:
        return self._oracle_request

    def set_oracle_verification_response(
        self,
        response: OracleVerificationResponse,
        request: Optional[OracleVerificationRequest] = None,
        *,
        expected_request_id: Optional[str] = None,
        expected_request_nonce: Optional[str] = None,
    ) -> None:
        """Sets the active Oracle verification response for upcoming action execution."""
        if not isinstance(response, OracleVerificationResponse):
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_ORACLE_RESPONSE_TYPE: expected OracleVerificationResponse, got {type(response).__name__}"
            )
        self._oracle_response = response
        self._oracle_request = request
        self._expected_request_id = expected_request_id or (request.request_id if request else None)
        self._expected_request_nonce = expected_request_nonce or (request.request_nonce if request else None)

    def verify_oracle_verification_response(
        self,
        oracle_response: OracleVerificationResponse,
        authorization: TrialExecutionAuthorization,
        plan: TimelineExecutionPlan,
        action: PlannedTimelineAction,
        *,
        request: Optional[OracleVerificationRequest] = None,
        expected_request_id: Optional[str] = None,
        expected_request_nonce: Optional[str] = None,
        max_age_seconds: float = 60.0,
        **kwargs: Any,
    ) -> bool:
        """
        Verifies OracleVerificationResponse cryptographic provenance and binds all claims (07B7 §3.2, §3.5).
        - Validates Ed25519 signature against canonical claims payload using pinned Oracle public key.
        - Enforces VERIFIED_VALID and is_valid is True.
        - Enforces request identity & nonce correlation.
        - Enforces timestamp validity, freshness, and age bound against internal trusted clock.
        - Recomputes action payload digest, nominal plan digest, and authorization digest.
        - Enforces exact binding across project UID, timeline UID, proposal revision, and timestamps.
        """
        if "now_utc" in kwargs:
            raise TypeError("ERR_CALLER_CONTROLLED_CLOCK_PROHIBITED: now_utc cannot be supplied per-call")
        if not isinstance(oracle_response, OracleVerificationResponse):
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_ORACLE_RESPONSE_TYPE: expected OracleVerificationResponse, got {type(oracle_response).__name__}"
            )
        if not isinstance(authorization, TrialExecutionAuthorization):
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_AUTH_OBJECT: expected TrialExecutionAuthorization, got {type(authorization).__name__}"
            )
        if not isinstance(action, PlannedTimelineAction):
            raise ReadbackReceiptVerificationError(
                f"ERR_INVALID_ACTION_OBJECT: expected PlannedTimelineAction, got {type(action).__name__}"
            )
        if plan is None:
            raise TypeError("ERR_PLAN_REQUIRED: plan must be provided and must be TimelineExecutionPlan")
        if not isinstance(plan, TimelineExecutionPlan):
            raise TypeError(
                f"ERR_INVALID_PLAN_TYPE: expected TimelineExecutionPlan, got {type(plan).__name__}"
            )

        # 1. Cryptographic Ed25519 signature verification over canonical claims bytes
        try:
            sig_bytes = bytes.fromhex(oracle_response.oracle_signature)
            claims_bytes = oracle_response.canonical_claims_bytes()
            self._pinned_oracle_public_key.verify(sig_bytes, claims_bytes)
        except (InvalidSignature, ValueError) as exc:
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_SIGNATURE_INVALID: Oracle response signature verification failed: {exc}"
            ) from exc

        # 2. Result gate: must be strictly VERIFIED_VALID and is_valid True
        if oracle_response.verification_result != "VERIFIED_VALID" or oracle_response.is_valid is not True:
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_VERIFICATION_REJECTED: result={oracle_response.verification_result!r}, "
                f"is_valid={oracle_response.is_valid!r}, reason={oracle_response.reason_code!r}"
            )

        # 3. Request identity and nonce correlation check (07B7 §3.5 Rule 3)
        if request is not None:
            if not isinstance(request, OracleVerificationRequest):
                raise TypeError(
                    f"ERR_INVALID_REQUEST_TYPE: request must be OracleVerificationRequest, got {type(request).__name__}"
                )
            req_id = request.request_id
            req_nonce = request.request_nonce
        elif expected_request_id is not None and expected_request_nonce is not None:
            req_id = expected_request_id
            req_nonce = expected_request_nonce
        else:
            raise ReadbackReceiptVerificationError(
                "ERR_ORACLE_REQUEST_CORRELATION_MISSING: either 'request' (OracleVerificationRequest) or both 'expected_request_id' and 'expected_request_nonce' must be supplied"
            )

        if not hmac.compare_digest(oracle_response.request_id, req_id):
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_REQUEST_ID_MISMATCH: oracle response request_id '{oracle_response.request_id}' != expected '{req_id}'"
            )
        if not hmac.compare_digest(oracle_response.request_nonce.lower(), req_nonce.lower()):
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_REQUEST_NONCE_MISMATCH: oracle response request_nonce '{oracle_response.request_nonce}' != expected '{req_nonce}'"
            )

        # 4. Validity timestamp ordering, freshness, and expiry (07B7 §3.5 Rule 8)
        try:
            dt_ver = datetime.fromisoformat(oracle_response.verified_at_iso.replace("Z", "+00:00"))
            dt_val = datetime.fromisoformat(oracle_response.valid_until_iso.replace("Z", "+00:00"))
            if dt_val <= dt_ver:
                raise ValueError("valid_until_iso must be strictly after verified_at_iso")
        except Exception as exc:
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_TIMESTAMP_INVALID: {exc}"
            ) from exc

        # Read internal trusted clock (DEP04-ADAPTER-DECOUPLE-R2-01)
        current_time = self._clock()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        if current_time > dt_val:
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_RESPONSE_EXPIRED: response valid_until_iso is {oracle_response.valid_until_iso}, current time is {current_time.isoformat()}"
            )
        age_seconds = (current_time - dt_ver).total_seconds()
        if age_seconds > max_age_seconds:
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_RESPONSE_STALE: response age {age_seconds:.2f}s exceeds maximum allowed {max_age_seconds}s"
            )
        if age_seconds < -5.0:
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_RESPONSE_FUTURE: response verified_at_iso is {-age_seconds:.2f}s in the future"
            )

        # 5. Target Project & Timeline UID bindings
        if oracle_response.target_project_uid != authorization.target_project_uid:
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_TARGET_PROJECT_MISMATCH: oracle project '{oracle_response.target_project_uid}' != auth '{authorization.target_project_uid}'"
            )
        if oracle_response.target_timeline_uid != authorization.target_timeline_uid:
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_TARGET_TIMELINE_MISMATCH: oracle timeline '{oracle_response.target_timeline_uid}' != auth '{authorization.target_timeline_uid}'"
            )

        # 6. Proposal revision binding
        if oracle_response.proposal_revision != authorization.proposal_revision:
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_PROPOSAL_REVISION_MISMATCH: oracle proposal_revision '{oracle_response.proposal_revision}' != auth '{authorization.proposal_revision}'"
            )

        # 7. Recompute Action Payload Digest from nominal objects
        try:
            payload = assemble_canonical_action_payload(action, authorization, execution_id=authorization.execution_id)
            calc_action_digest = hashlib.sha256(canonical_action_payload_bytes(payload)).hexdigest().lower()
        except Exception as exc:
            raise ReadbackReceiptVerificationError(
                f"ERR_ACTION_DIGEST_COMPUTATION_FAILED: {exc}"
            ) from exc

        if not hmac.compare_digest(calc_action_digest, authorization.action_payload_digest_sha256.lower()):
            raise ReadbackReceiptVerificationError(
                f"ERR_ACTION_DIGEST_MISMATCH: recomputed action digest {calc_action_digest} != auth {authorization.action_payload_digest_sha256}"
            )
        if not hmac.compare_digest(calc_action_digest, oracle_response.action_payload_digest_sha256.lower()):
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_ACTION_DIGEST_MISMATCH: recomputed action digest {calc_action_digest} != oracle {oracle_response.action_payload_digest_sha256}"
            )

        # 8. Recompute Plan Digest (strictly required nominal TimelineExecutionPlan)
        calc_plan_digest = compute_canonical_plan_digest(plan).lower()
        if not hmac.compare_digest(calc_plan_digest, authorization.plan_digest_sha256.lower()):
            raise ReadbackReceiptVerificationError(
                f"ERR_PLAN_DIGEST_MISMATCH: recomputed plan digest {calc_plan_digest} != auth {authorization.plan_digest_sha256}"
            )

        # 9. Recompute Authorization Digest
        try:
            if hasattr(authorization, "canonical_claims_dict"):
                auth_claims_bytes = canonical_json_bytes(authorization.canonical_claims_dict())
            else:
                auth_claims_bytes = canonical_bytes_for_auth_hmac(authorization)
            calc_auth_digest = hashlib.sha256(auth_claims_bytes).hexdigest().lower()
        except Exception as exc:
            raise ReadbackReceiptVerificationError(
                f"ERR_AUTH_DIGEST_COMPUTATION_FAILED: {exc}"
            ) from exc

        if not hmac.compare_digest(calc_auth_digest, oracle_response.authorization_digest_sha256.lower()):
            raise ReadbackReceiptVerificationError(
                f"ERR_ORACLE_AUTH_DIGEST_MISMATCH: recomputed auth digest {calc_auth_digest} != oracle {oracle_response.authorization_digest_sha256}"
            )

        return True

    def _bind_and_verify_authorization(
        self,
        authorization: TrialExecutionAuthorization,
        action: PlannedTimelineAction,
        plan: TimelineExecutionPlan,
        oracle_response: Optional[OracleVerificationResponse] = None,
        oracle_request: Optional[OracleVerificationRequest] = None,
        expected_request_id: Optional[str] = None,
        expected_request_nonce: Optional[str] = None,
    ) -> None:
        """Verifies authorization cryptographically via OracleVerificationResponse."""
        if action is None:
            raise ReadbackReceiptVerificationError(
                "ERR_ACTION_REQUIRED: action must be provided for authorization verification"
            )
        resp = oracle_response or self._oracle_response
        if resp is None:
            raise ReadbackReceiptVerificationError(
                "ERR_ORACLE_RESPONSE_REQUIRED: OracleVerificationResponse must be provided to execute_live_canary_action or set on adapter"
            )
        req = oracle_request or self._oracle_request
        req_id = expected_request_id or self._expected_request_id
        req_nonce = expected_request_nonce or self._expected_request_nonce

        self.verify_oracle_verification_response(
            oracle_response=resp,
            authorization=authorization,
            plan=plan,
            action=action,
            request=req,
            expected_request_id=req_id,
            expected_request_nonce=req_nonce,
        )

    def execute_action(
        self,
        action: PlannedTimelineAction,
        authorization: TrialExecutionAuthorization,
        plan: TimelineExecutionPlan,
        canary_clip_item: Optional[Any] = None,
        oracle_response: Optional[OracleVerificationResponse] = None,
        oracle_request: Optional[OracleVerificationRequest] = None,
        expected_request_id: Optional[str] = None,
        expected_request_nonce: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Convenience alias delegating directly to execute_live_canary_action."""
        if "now_utc" in kwargs:
            raise TypeError("ERR_CALLER_CONTROLLED_CLOCK_PROHIBITED: now_utc cannot be supplied per-call")
        return self.execute_live_canary_action(
            action=action,
            authorization=authorization,
            plan=plan,
            canary_clip_item=canary_clip_item,
            oracle_response=oracle_response,
            oracle_request=oracle_request,
            expected_request_id=expected_request_id,
            expected_request_nonce=expected_request_nonce,
            **kwargs,
        )

    def execute_live_canary_action(
        self,
        action: PlannedTimelineAction,
        authorization: TrialExecutionAuthorization,
        plan: TimelineExecutionPlan,
        canary_clip_item: Optional[Any] = None,
        oracle_response: Optional[OracleVerificationResponse] = None,
        oracle_request: Optional[OracleVerificationRequest] = None,
        expected_request_id: Optional[str] = None,
        expected_request_nonce: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Executes single supervised live canary action according to 07B7 plan.
        Normal dispatch strictly enforces write-ahead journal sequence (07B7 §5.2.2):
        Step 1: write_audit_intent
        Step 2: reserve_target
        Step 3: write_action_pending
        Step 4: acquire_target_lock
        Step 5: Tier 2 Pre-IPC re-query tripwire (clean abort if drifted)
        Step 6: Resolve AppendToTimeline mutation
        Step 7: Physical timeline readback verification
        Step 8: commit_terminal (COMPLETED or ABORTED) and release_target_lock (or force_in_doubt_locked)
        """
        if "now_utc" in kwargs:
            raise TypeError("ERR_CALLER_CONTROLLED_CLOCK_PROHIBITED: now_utc cannot be supplied per-call")
        if plan is None:
            raise TypeError("ERR_PLAN_REQUIRED: plan must be an instance of TimelineExecutionPlan")
        if not isinstance(plan, TimelineExecutionPlan):
            raise TypeError(
                f"ERR_INVALID_PLAN_TYPE: plan must be an instance of TimelineExecutionPlan, got {type(plan).__name__}"
            )
        if not isinstance(authorization, TrialExecutionAuthorization):
            raise ReadbackReceiptVerificationError(
                "ERR_INVALID_AUTH_OBJECT: authorization must be an instance of TrialExecutionAuthorization"
            )
        if not isinstance(action, PlannedTimelineAction):
            raise ReadbackReceiptVerificationError(
                "ERR_INVALID_ACTION_OBJECT: action must be an instance of PlannedTimelineAction"
            )

        # Cryptographic signed authorization verification via Oracle response (07B7 §3.2, §3.5)
        self._bind_and_verify_authorization(
            authorization=authorization,
            action=action,
            plan=plan,
            oracle_response=oracle_response,
            oracle_request=oracle_request,
            expected_request_id=expected_request_id,
            expected_request_nonce=expected_request_nonce,
        )

        timeline_uid = authorization.target_timeline_uid
        project_uid = authorization.target_project_uid
        execution_id = authorization.execution_id
        action_digest = authorization.action_payload_digest_sha256

        # Phase 1A: Check pre-existing target lock or in-doubt in Dispatcher journal
        if self._journal.is_target_locked_or_in_doubt(project_uid, timeline_uid):
            raise WholePlanReconciliationError(
                f"ERR_TARGET_LOCKED_OR_IN_DOUBT: target '{project_uid}:{timeline_uid}' is already locked or in-doubt in Dispatcher journal"
            )

        if self._journal.is_execution_id_committed(execution_id):
            raise WholePlanReconciliationError(
                f"ERR_EXECUTION_ALREADY_COMMITTED: execution_id '{execution_id}' already committed in Dispatcher journal"
            )

        # Phase 1B: Admission gate & media pool item binding
        self._validate_tier1_admission(authorization=authorization, action=action, plan=plan)
        clip_item = self._resolve_canary_clip_item(canary_clip_item, authorization)
        self._validate_media_pool_item_binding(clip_item, authorization)

        # Phase 1C: Strict Pre-Mutation Durability Ordering (07B7 §5.2.2 Steps 1-4)
        # Step 1: Write-Ahead Audit Intent fsync (§5.2.2 Step 1)
        self._journal.write_audit_intent(
            execution_id=execution_id,
            action_payload_digest_sha256=action_digest,
            target_project_uid=project_uid,
            target_timeline_uid=timeline_uid,
        )

        # Step 2: Durable Target Ledger Reservation fsync (§5.2.2 Step 2)
        self._journal.reserve_target(
            execution_id=execution_id,
            target_project_uid=project_uid,
            target_timeline_uid=timeline_uid,
        )

        # Step 3: Durable WAL Reservation fsync (§5.2.2 Step 3)
        self._journal.write_action_pending(
            execution_id=execution_id,
            action_payload_digest_sha256=action_digest,
            target_project_uid=project_uid,
            target_timeline_uid=timeline_uid,
        )

        # Step 4: Target exclusion lock via non-blocking fcntl.flock (§5.2.2 Step 4)
        self._journal.acquire_target_lock(project_uid, timeline_uid)

        # Phase 2: Tier 2 Pre-IPC Boundary Gate (Fresh Re-query Tripwires) (§5.2.2 Step 5)
        tier2_drift_err: Optional[Exception] = None
        try:
            active_project, active_timeline = self._validate_tier2_pre_ipc(authorization)
        except (TargetProjectMismatchError, TargetTimelineMismatchError, TargetTimelineShiftedError) as drift_e:
            tier2_drift_err = drift_e

        if tier2_drift_err is not None:
            # Branch 2: Pre-Dispatch Clean Abort (§5.2.2 Branch 2)
            # Zero mutation has occurred! Commit ABORTED terminal state in journal and release lock.
            receipt_data = {
                "schema_version": 2,
                "execution_id": execution_id,
                "terminal_state": "ABORTED",
                "target_project_uid": project_uid,
                "target_timeline_uid": timeline_uid,
                "reason": f"PRE_IPC_DRIFT_ABORT: {tier2_drift_err}",
                "aborted_at_iso": self._clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            try:
                self._journal.commit_terminal(
                    execution_id=execution_id,
                    terminal_state="ABORTED",
                    target_project_uid=project_uid,
                    target_timeline_uid=timeline_uid,
                    receipt_data=receipt_data,
                )
                self._journal.release_target_lock(project_uid, timeline_uid)
            except Exception as commit_exc:
                try:
                    self._journal.force_in_doubt_locked(
                        target_project_uid=project_uid,
                        target_timeline_uid=timeline_uid,
                        execution_id=execution_id,
                        reason=f"ERR_ABORT_COMMIT_FAILED: {commit_exc}",
                    )
                except Exception:
                    pass
                raise commit_exc from tier2_drift_err
            raise tier2_drift_err

        # Phase 3: IPC Dispatch (Single Action AppendToTimeline) (§5.2.2 Step 6)
        incident_id = f"inc_{secrets.token_hex(16)}"
        append_spec = {
            "mediaPoolItem": clip_item,
            "startFrame": authorization.authorized_source_frame_start,
            "endFrame": authorization.authorized_source_frame_start + authorization.authorized_source_frame_duration,
            "trackIndex": authorization.authorized_track_index,
            "recordFrame": authorization.authorized_record_frame,
        }

        try:
            media_pool = active_project.GetMediaPool()
            created_items = media_pool.AppendToTimeline([append_spec])
            if created_items is None or (isinstance(created_items, (list, tuple)) and len(created_items) == 0):
                raise ReadbackReceiptVerificationError("AppendToTimeline returned empty or None")
        except Exception as dispatch_exc:
            # Branch 3: Ambiguous Failure / In-Doubt Latch (§5.2.2 Branch 3)
            # Target lock is NOT released! Retain lock!
            try:
                self._journal.force_in_doubt_locked(
                    target_project_uid=project_uid,
                    target_timeline_uid=timeline_uid,
                    execution_id=execution_id,
                    reason=f"incident_id={incident_id}: ERR_IPC_DISPATCH_FAILED: {dispatch_exc}",
                )
            except Exception:
                pass
            return {
                "status": "TERMINAL_ACTION_FAILED_IN_DOUBT",
                "receipt_state": "IN_DOUBT",
                "lock_retained": True,
                "target_lock_state": "IN_DOUBT_LOCKED",
                "ledger_state": "IN_DOUBT_LOCKED",
                "terminalized": False,
                "incident_id": incident_id,
                "execution_id": execution_id,
            }

        # Phase 4: Physical Timeline Read-Back Verification (§5.2.2 Step 7)
        try:
            resolve_item_id = self._perform_physical_readback_observation(active_timeline, authorization)
        except Exception as readback_exc:
            # Branch 3: In-Doubt Latch (§5.2.2 Branch 3)
            # Target lock is NOT released! Retain lock!
            try:
                self._journal.force_in_doubt_locked(
                    target_project_uid=project_uid,
                    target_timeline_uid=timeline_uid,
                    execution_id=execution_id,
                    reason=f"incident_id={incident_id}: ERR_READBACK_FAILED: {readback_exc}",
                )
            except Exception:
                pass
            raise readback_exc

        # Phase 5: Branch 1 Terminalization (Verified Success) (§5.2.2 Step 8 Branch 1)
        receipt_data = {
            "schema_version": 2,
            "execution_id": execution_id,
            "terminal_state": "COMPLETED",
            "target_project_uid": project_uid,
            "target_timeline_uid": timeline_uid,
            "action_id": action.action_id,
            "resolve_item_id": resolve_item_id,
            "completed_at_iso": self._clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        # Step 8a: Commit terminal record in journal
        try:
            self._journal.commit_terminal(
                execution_id=execution_id,
                terminal_state="COMPLETED",
                target_project_uid=project_uid,
                target_timeline_uid=timeline_uid,
                receipt_data=receipt_data,
            )
            # Step 8e: Release target lock
            self._journal.release_target_lock(project_uid, timeline_uid)
        except Exception as commit_terminal_exc:
            try:
                self._journal.force_in_doubt_locked(
                    target_project_uid=project_uid,
                    target_timeline_uid=timeline_uid,
                    execution_id=execution_id,
                    reason=f"incident_id={incident_id}: ERR_TERMINAL_COMMIT_FAILED: {commit_terminal_exc}",
                )
            except Exception:
                pass
            return {
                "status": "TERMINAL_ACTION_FAILED_IN_DOUBT",
                "receipt_state": "IN_DOUBT",
                "lock_retained": True,
                "target_lock_state": "IN_DOUBT_LOCKED",
                "ledger_state": "IN_DOUBT_LOCKED",
                "terminalized": False,
                "incident_id": incident_id,
                "execution_id": execution_id,
            }

        return {
            "status": "COMPLETED",
            "receipt_state": "COMPLETED",
            "ledger_state": "EXHAUSTED",
            "ledger_terminal_reason": "COMPLETED",
            "wal_final_receipt_state": "COMPLETED",
            "lock_released": True,
            "terminalized": True,
            "branch": 1,
            "execution_id": execution_id,
            "receipt_id": f"rcpt_{execution_id}",
            "timeline_uid": timeline_uid,
            "action_id": action.action_id,
            "action_idempotency_hash": action.action_id,
        }

    def _perform_physical_readback_observation(
        self,
        active_timeline: Any,
        authorization: TrialExecutionAuthorization,
    ) -> str:
        """
        Executes physical readback verification against active timeline (LIVE-IMPL-03, LIVE-IMPL-R1-04).
        Requires exact item count, start, duration, end, clip name matching os.path.basename(source_path),
        media pool item file path matching source_path, and genuine item unique ID.
        Returns the genuine resolve_item_id.
        """
        readback_items = active_timeline.GetItemListInTrack("video", CANONICAL_TRACK_INDEX)
        if readback_items is None or len(readback_items) != 1:
            item_count = len(readback_items) if readback_items is not None else 0
            raise PhysicalReadbackVerificationError(
                f"ERR_PHYSICAL_READBACK_COUNT_MISMATCH: Expected exactly 1 item on Video Track 1, found {item_count}"
            )

        item = readback_items[0]
        item_start = getattr(item, "GetStart", lambda: None)()
        item_duration = getattr(item, "GetDuration", lambda: None)()
        item_end = getattr(item, "GetEnd", lambda: None)()

        if item_start != authorization.authorized_record_frame:
            raise PhysicalReadbackVerificationError(
                f"ERR_PHYSICAL_READBACK_START_MISMATCH: Clip start {item_start} != authorized {authorization.authorized_record_frame}"
            )
        if item_duration != CANONICAL_TIMELINE_FRAME_DURATION:
            raise PhysicalReadbackVerificationError(
                f"ERR_PHYSICAL_READBACK_DURATION_MISMATCH: Clip duration {item_duration} != authorized {CANONICAL_TIMELINE_FRAME_DURATION}"
            )
        expected_end = authorization.authorized_record_frame + CANONICAL_TIMELINE_FRAME_DURATION
        if item_end != expected_end:
            raise PhysicalReadbackVerificationError(
                f"ERR_PHYSICAL_READBACK_END_MISMATCH: Clip end {item_end} != expected {expected_end}"
            )

        # Name check: must match os.path.basename(source_file_path) exactly (remove permissive canary_clip prefix)
        item_name = getattr(item, "GetName", lambda: None)()
        if not item_name and hasattr(item, "name"):
            item_name = item.name
        expected_name = os.path.basename(authorization.source_file_path)
        if not item_name or item_name != expected_name:
            raise PhysicalReadbackVerificationError(
                f"ERR_PHYSICAL_READBACK_MEDIA_NAME_MISMATCH: readback item name '{item_name}' != authorized '{expected_name}'"
            )

        # Media item and file path check
        item_media = getattr(item, "GetMediaPoolItem", lambda: None)()
        if item_media is None:
            raise PhysicalReadbackVerificationError(
                "ERR_PHYSICAL_READBACK_MEDIA_ITEM_MISSING: readback item has no associated MediaPoolItem"
            )
        media_path = getattr(item_media, "GetClipProperty", lambda k: None)("File Path")
        if not media_path and hasattr(item_media, "file_path"):
            media_path = item_media.file_path
        if not media_path or os.path.abspath(media_path) != os.path.abspath(authorization.source_file_path):
            raise PhysicalReadbackVerificationError(
                f"ERR_PHYSICAL_READBACK_MEDIA_PATH_MISMATCH: readback media path '{media_path}' != authorized '{authorization.source_file_path}'"
            )

        # Unique ID check: must be non-empty string without synthetic fallback
        resolve_item_id = None
        if hasattr(item, "GetUniqueId") and callable(item.GetUniqueId):
            resolve_item_id = item.GetUniqueId()
        if not resolve_item_id and hasattr(item, "unique_id"):
            resolve_item_id = item.unique_id
        if not resolve_item_id or str(resolve_item_id).strip() == "":
            raise PhysicalReadbackVerificationError(
                "ERR_PHYSICAL_READBACK_ITEM_ID_MISSING: readback item has no valid unique identifier"
            )

        return str(resolve_item_id)

    # ======================================================================
    # Source Media Pool Item Binding Gate (LIVE-IMPL-03)
    # ======================================================================

    def _resolve_canary_clip_item(
        self,
        canary_clip_item: Optional[Any],
        authorization: TrialExecutionAuthorization,
    ) -> Any:
        """
        Retrieves or discovers the Resolve-owned Media Pool item matching the authorized source.
        Disallows fake fallback out of thin air (LIVE-IMPL-03).
        """
        expected_path = os.path.abspath(authorization.source_file_path)

        if canary_clip_item is not None:
            return canary_clip_item

        # Query Media Pool on active project
        pm = self._resolve_client.GetProjectManager()
        proj = pm.GetCurrentProject() if pm else None
        mp = proj.GetMediaPool() if proj else None

        if mp is not None:
            # Check root folder clip list
            root_folder = getattr(mp, "GetRootFolder", lambda: None)()
            clips = getattr(root_folder, "GetClipList", lambda: [])() if root_folder else []
            if not clips:
                clips = getattr(mp, "GetClipList", lambda: [])()
            for clip in clips:
                cpath = getattr(clip, "GetClipProperty", lambda k: None)("File Path") or getattr(clip, "file_path", None)
                if cpath and os.path.abspath(cpath) == expected_path:
                    return clip

        raise ReadbackReceiptVerificationError(
            f"ERR_MISSING_CANARY_CLIP_ITEM: Resolve-owned canary clip item matching source path "
            f"'{authorization.source_file_path}' not found in Media Pool; fake fallback forbidden (LIVE-IMPL-03)"
        )

    def _validate_media_pool_item_binding(
        self,
        canary_clip_item: Any,
        authorization: TrialExecutionAuthorization,
    ) -> None:
        """
        Validates canary_clip_item binding against authorized source file path, name, and digest (07B7 §4.4, LIVE-IMPL-03, LIVE-IMPL-R1-04).
        Enforces:
        1. Source file exists on disk and is a regular file.
        2. Media pool item has 'File Path' property matching authorized source_file_path exactly.
        3. Media pool item name matches os.path.basename(source_file_path) exactly.
        4. Source file SHA-256 digest matches authorization.source_file_sha256 unconditionally.
        """
        expected_path = os.path.abspath(authorization.source_file_path)
        expected_name = os.path.basename(expected_path)

        if not os.path.isfile(expected_path):
            raise ReadbackReceiptVerificationError(
                f"ERR_MEDIA_SOURCE_FILE_NOT_FOUND: source file '{expected_path}' does not exist or is not a regular file on disk"
            )

        expected_source_sha = getattr(authorization, "source_file_sha256", None)
        if not expected_source_sha:
            raise ReadbackReceiptVerificationError(
                "ERR_MEDIA_SOURCE_SHA_MISSING: authorization does not contain source_file_sha256"
            )

        try:
            with open(expected_path, "rb") as sf:
                actual_digest = hashlib.sha256(sf.read()).hexdigest()
            if not hmac.compare_digest(actual_digest.lower(), expected_source_sha.lower()):
                raise ReadbackReceiptVerificationError(
                    f"ERR_MEDIA_ITEM_DIGEST_MISMATCH: file digest '{actual_digest}' != authorized '{expected_source_sha}'"
                )
        except OSError as read_err:
            raise ReadbackReceiptVerificationError(
                f"ERR_MEDIA_SOURCE_FILE_UNREADABLE: cannot verify source file digest: {read_err}"
            ) from read_err

        clip_path = getattr(canary_clip_item, "GetClipProperty", lambda k: None)("File Path")
        if not clip_path and hasattr(canary_clip_item, "file_path"):
            clip_path = canary_clip_item.file_path

        if not clip_path:
            raise ReadbackReceiptVerificationError(
                "ERR_CLIP_PROPERTY_UNAVAILABLE: 'File Path' property unavailable on canary_clip_item"
            )

        if os.path.abspath(clip_path) != expected_path:
            raise ReadbackReceiptVerificationError(
                f"ERR_MEDIA_ITEM_PATH_MISMATCH: canary_clip_item path '{clip_path}' != authorized '{expected_path}'"
            )

        clip_name = getattr(canary_clip_item, "GetName", lambda: None)()
        if not clip_name and hasattr(canary_clip_item, "name"):
            clip_name = canary_clip_item.name

        if not clip_name or clip_name != expected_name:
            raise ReadbackReceiptVerificationError(
                f"ERR_MEDIA_ITEM_NAME_MISMATCH: canary_clip_item name '{clip_name}' != expected '{expected_name}'"
            )

    # ======================================================================
    # Tier 1 Admission Gate Validation
    # ======================================================================

    def _validate_tier1_admission(
        self,
        authorization: TrialExecutionAuthorization,
        action: PlannedTimelineAction,
        plan: Optional[TimelineExecutionPlan] = None,
    ) -> None:
        """
        Pure Pre-Journal Validation Phase & Tier 1 Admission Gate (07B7 §5.2.1).
        Verifies project name regex, uppercase challenge nonce binding, direct project UID equality,
        direct timeline UID equality, single INSERT_CLIP action on Video Track 1 (120 frames @ 24 fps),
        16-key canonical payload digest match, and source-aligned plan digest & identity bridge (Workorder R7).
        """
        if plan is None:
            raise WholePlanReconciliationError(
                "ERR_PLAN_REQUIRED: Authoritative TimelineExecutionPlan is required for canary admission"
            )

        if not isinstance(plan, TimelineExecutionPlan):
            raise WholePlanReconciliationError(
                "ERR_INVALID_PLAN_TYPE: plan must be an instance of TimelineExecutionPlan"
            )

        # Plan identity check against authorization
        if plan.idempotency_key != authorization.plan_idempotency_key:
            raise WholePlanReconciliationError(
                f"ERR_PLAN_IDEMPOTENCY_KEY_MISMATCH: plan idempotency key '{plan.idempotency_key}' != authorized '{authorization.plan_idempotency_key}'"
            )

        if plan.project_uid != authorization.target_project_uid:
            raise WholePlanReconciliationError(
                f"ERR_PLAN_PROJECT_UID_MISMATCH: plan project UID '{plan.project_uid}' != authorized '{authorization.target_project_uid}'"
            )

        if plan.timeline_uid != authorization.target_timeline_uid:
            raise WholePlanReconciliationError(
                f"ERR_PLAN_TIMELINE_UID_MISMATCH: plan timeline UID '{plan.timeline_uid}' != authorized '{authorization.target_timeline_uid}'"
            )

        if len(plan.planned_actions) != 1:
            raise WholePlanReconciliationError(
                f"ERR_PLAN_ACTION_COUNT_MISMATCH: expected 1 action, found {len(plan.planned_actions)}"
            )

        # Stable identity bridge: action.action_id must equal binder planned_action.action_idempotency_hash (Workorder R7 §2.2)
        binder_action = plan.planned_actions[0]
        if binder_action.action_idempotency_hash != action.action_id:
            raise WholePlanReconciliationError(
                f"ERR_ACTION_ID_PLAN_HASH_MISMATCH: action.action_id '{action.action_id}' != binder planned_action.action_idempotency_hash '{binder_action.action_idempotency_hash}'"
            )

        # Authoritatively validate action and its action_id against signed authorization via plan_digest_sha256 (07B5 §2.6, Workorder R7 §2.3)
        calc_plan_digest = compute_canonical_plan_digest(plan)
        if not hmac.compare_digest(calc_plan_digest.lower(), authorization.plan_digest_sha256.lower()):
            raise WholePlanReconciliationError(
                "ERR_PLAN_DIGEST_MISMATCH: canonical plan digest does not match signed authorization plan_digest_sha256"
            )
        project_name = authorization.target_project_name
        if not RE_PROJECT_NAME.match(project_name):
            raise LiveAdmissionTripwireError(
                f"ERR_PROJECT_NAME_REGEX_MISMATCH: Project name '{project_name}' does not match regex ^RF_TRIAL_[0-9A-Z_]{{8,32}}$"
            )

        challenge_nonce = authorization.challenge_nonce
        if not RE_CHALLENGE_NONCE.match(challenge_nonce):
            raise LiveAdmissionTripwireError(
                f"ERR_CHALLENGE_NONCE_FORMAT_INVALID: Challenge nonce '{challenge_nonce}' is not 16 uppercase hex chars"
            )

        if challenge_nonce not in project_name:
            raise LiveAdmissionTripwireError(
                f"ERR_CHALLENGE_NONCE_NOT_IN_PROJECT_NAME: Challenge nonce '{challenge_nonce}' missing from project name '{project_name}'"
            )

        pm = self._resolve_client.GetProjectManager()
        if pm is None:
            raise LiveAdmissionTripwireError("ERR_PROJECT_MANAGER_UNAVAILABLE: GetProjectManager returned None")

        curr_project = pm.GetCurrentProject()
        if curr_project is None:
            raise LiveAdmissionTripwireError("ERR_CURRENT_PROJECT_NONE: GetCurrentProject returned None")

        active_project_uid = curr_project.GetUniqueId()
        if active_project_uid != authorization.target_project_uid:
            raise TargetProjectMismatchError(
                f"ERR_TARGET_PROJECT_UID_MISMATCH: active project UID '{active_project_uid}' != authorized '{authorization.target_project_uid}'"
            )

        curr_timeline = curr_project.GetCurrentTimeline()
        if curr_timeline is None:
            raise LiveAdmissionTripwireError("ERR_CURRENT_TIMELINE_NONE: GetCurrentTimeline returned None")

        active_timeline_uid = curr_timeline.GetUniqueId()
        if active_timeline_uid != authorization.target_timeline_uid:
            raise TargetTimelineMismatchError(
                f"ERR_TARGET_TIMELINE_UID_MISMATCH: active timeline UID '{active_timeline_uid}' != authorized '{authorization.target_timeline_uid}'"
            )

        if action.action_type != CANONICAL_ACTION_TYPE:
            raise LiveAdmissionTripwireError(
                f"ERR_CANONICAL_ACTION_TYPE_MISMATCH: action_type '{action.action_type}' != '{CANONICAL_ACTION_TYPE}'"
            )

        if authorization.authorized_track_type != CANONICAL_TRACK_TYPE or action.track_type != CANONICAL_TRACK_TYPE:
            raise LiveAdmissionTripwireError(
                f"ERR_CANONICAL_TRACK_TYPE_MISMATCH: track_type != '{CANONICAL_TRACK_TYPE}'"
            )

        if authorization.authorized_track_index != CANONICAL_TRACK_INDEX:
            raise LiveAdmissionTripwireError(
                f"ERR_TRACK_INDEX_NOT_CANONICAL: track_index {authorization.authorized_track_index} != {CANONICAL_TRACK_INDEX}"
            )

        if (
            authorization.authorized_source_frame_start != CANONICAL_SOURCE_FRAME_START
            or authorization.authorized_source_frame_duration != CANONICAL_SOURCE_FRAME_DURATION
        ):
            raise LiveAdmissionTripwireError("ERR_CANONICAL_SOURCE_DURATION_MISMATCH: source duration != 120")

        if (
            action.duration_frames != CANONICAL_TIMELINE_FRAME_DURATION
            or authorization.authorized_timeline_frame_start != CANONICAL_TIMELINE_FRAME_START
        ):
            raise LiveAdmissionTripwireError("ERR_CANONICAL_SOURCE_DURATION_MISMATCH: timeline geometry must be 0..120")

        if (
            authorization.target_timeline_fps_numerator != CANONICAL_FPS_NUMERATOR
            or authorization.target_timeline_fps_denominator != CANONICAL_FPS_DENOMINATOR
        ):
            raise LiveAdmissionTripwireError("ERR_TIMELINE_FPS_INVALID: FPS must be 24/1")

        expected_record_frame = authorization.observed_timeline_start_frame + CANONICAL_TIMELINE_FRAME_START
        if authorization.authorized_record_frame != expected_record_frame:
            raise LiveAdmissionTripwireError(
                f"ERR_RECORD_FRAME_CALCULATION_MISMATCH: record_frame {authorization.authorized_record_frame} != {expected_record_frame}"
            )

        payload = assemble_canonical_action_payload(action, authorization, execution_id=authorization.execution_id)
        calc_digest = hashlib.sha256(canonical_action_payload_bytes(payload)).hexdigest()
        if not hmac.compare_digest(calc_digest.lower(), authorization.action_payload_digest_sha256.lower()):
            raise WholePlanReconciliationError("ERR_ACTION_DIGEST_MISMATCH: canonical payload digest does not match authorization")



    # ======================================================================
    # Tier 2 Pre-IPC Boundary Gate Validation
    # ======================================================================

    def _validate_tier2_pre_ipc(
        self, authorization: TrialExecutionAuthorization
    ) -> Tuple[Any, Any]:
        """
        Tier 2 Pre-IPC Boundary Gate (07B7 §5.2.1).
        Re-queries fresh active project UID, active timeline UID, and timeline GetStartFrame()
        immediately before dispatch. Any mismatch triggers Branch 2A (Pre-Dispatch Clean Abort).
        """
        pm = self._resolve_client.GetProjectManager()
        active_project = pm.GetCurrentProject() if pm else None
        if active_project is None or active_project.GetUniqueId() != authorization.target_project_uid:
            raise TargetProjectMismatchError("ERR_TARGET_PROJECT_UID_DRIFT_PRE_IPC")

        active_timeline = active_project.GetCurrentTimeline()
        if active_timeline is None or active_timeline.GetUniqueId() != authorization.target_timeline_uid:
            raise TargetTimelineMismatchError("ERR_TARGET_TIMELINE_UID_DRIFT_PRE_IPC")

        fresh_start_frame = active_timeline.GetStartFrame()
        if fresh_start_frame != authorization.observed_timeline_start_frame:
            raise TargetTimelineShiftedError(
                f"ERR_TIMELINE_START_FRAME_DRIFT_PRE_IPC: start_frame shifted from "
                f"{authorization.observed_timeline_start_frame} to {fresh_start_frame}"
            )

        return active_project, active_timeline

    # ======================================================================
    # Terminal Branch Handlers
    # ======================================================================

    def _handle_pre_dispatch_clean_abort(
        self,
        execution_id: str,
        timeline_uid: str,
        project_uid: str,
        authorization: TrialExecutionAuthorization,
        error_code: str,
        error_message: str,
    ) -> None:
        """Executes Branch 2A: Pre-Dispatch Clean Abort."""
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            self._journal.commit_terminal(
                execution_id=execution_id,
                terminal_state="ABORTED",
                target_project_uid=project_uid,
                target_timeline_uid=timeline_uid,
                receipt_data={
                    "execution_id": execution_id,
                    "state": "ABORTED",
                    "completed_at_iso": now_iso,
                    "last_error_code": error_code,
                    "last_error_message": error_message,
                },
            )
        except Exception:
            pass
        try:
            self._journal.release_target_lock(project_uid, timeline_uid)
        except Exception:
            pass

    def _handle_in_doubt_dispatch_failure(
        self,
        execution_id: str,
        timeline_uid: str,
        action: PlannedTimelineAction,
        payload: Dict[str, Any],
        incident_id: str,
        dispatch_exc: Exception,
        project_uid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes Branch 3 for IPC dispatch failure: latches IN_DOUBT_LOCKED."""
        target_project_uid = project_uid or getattr(action, "target_project_uid", "")
        try:
            self._journal.force_in_doubt_locked(
                target_project_uid=target_project_uid,
                target_timeline_uid=timeline_uid,
                execution_id=execution_id,
                reason=f"incident_id={incident_id}: ERR_IPC_DISPATCH_FAILED: {dispatch_exc}",
            )
        except Exception:
            pass

        return {
            "status": "TERMINAL_ACTION_FAILED_IN_DOUBT",
            "lock_retained": True,
            "target_lock_state": "IN_DOUBT_LOCKED",
            "ledger_state": "IN_DOUBT_LOCKED",
            "terminalized": False,
            "incident_id": incident_id,
            "execution_id": execution_id,
        }

    def _handle_in_doubt_readback_failure(
        self,
        execution_id: str,
        timeline_uid: str,
        action: PlannedTimelineAction,
        payload: Dict[str, Any],
        incident_id: str,
        readback_exc: Exception,
        project_uid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes Branch 3 for readback failure: latches IN_DOUBT_LOCKED."""
        target_project_uid = project_uid or getattr(action, "target_project_uid", "")
        try:
            self._journal.force_in_doubt_locked(
                target_project_uid=target_project_uid,
                target_timeline_uid=timeline_uid,
                execution_id=execution_id,
                reason=f"incident_id={incident_id}: ERR_READBACK_FAILED: {readback_exc}",
            )
        except Exception:
            pass

        raise readback_exc

    def _handle_in_doubt_commit_failure(
        self,
        execution_id: str,
        timeline_uid: str,
        action: PlannedTimelineAction,
        incident_id: str,
        commit_wal_exc: Exception,
        project_uid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes Branch 3 for WAL commit persistence failure."""
        target_project_uid = project_uid or getattr(action, "target_project_uid", "")
        try:
            self._journal.force_in_doubt_locked(
                target_project_uid=target_project_uid,
                target_timeline_uid=timeline_uid,
                execution_id=execution_id,
                reason=f"incident_id={incident_id}: ERR_ACTION_COMMIT_PERSISTENCE_FAILED: {commit_wal_exc}",
            )
        except Exception:
            pass
        raise AcceptanceGateError(f"ERR_ACTION_COMMIT_PERSISTENCE_FAILED: {commit_wal_exc}") from commit_wal_exc

    # ======================================================================
    # Stage 1: Raw Proof Verification & Durable Consumption (LIVE-IMPL-04, 05)
    # ======================================================================

    def verify_and_consume_proof(
        self,
        raw_proof: Mapping[str, Any],
        action: PlannedTimelineAction,
        pending_entry: Any,
        authorization: Optional[TrialExecutionAuthorization] = None,
    ) -> DurableProofRecord:
        """
        Stage 1: Authoritative raw proof verification and consumption (07B5 §3.3.4, 07B7 §5.2.2).
        Removed in decoupled adapter: HMAC proof verification requiring Signer keys has been removed (07B7 §3.5).
        """
        raise ReadbackReceiptVerificationError(
            "ERR_SIGNER_KEYRING_REMOVED: HMAC proof verification requiring Signer keys has been removed in decoupled adapter (07B7 §3.5)"
        )

    def verify_durable_proof_record(
        self,
        record: DurableProofRecord,
        action: PlannedTimelineAction,
        pending_entry: Any,
        authorization: Optional[TrialExecutionAuthorization] = None,
    ) -> bool:
        """
        Stage 2: Durable Proof Record Verification during restart repair (LIVE-IMPL-04, B7-R6-01, B7-R6-02).
        Removed in decoupled adapter: HMAC proof verification requiring Signer keys has been removed (07B7 §3.5).
        """
        raise ReadbackReceiptVerificationError(
            "ERR_SIGNER_KEYRING_REMOVED: HMAC proof verification requiring Signer keys has been removed in decoupled adapter (07B7 §3.5)"
        )

    def complete_with_clean_proof(
        self,
        proof_record: Union[DurableProofRecord, str],
        authorization: TrialExecutionAuthorization,
        action: Optional[PlannedTimelineAction] = None,
    ) -> Dict[str, Any]:
        """
        Executes Branch 2B terminalization when a clean independent proof is verified (LIVE-IMPL-01).
        Removed in decoupled adapter: RecoveryCoordinator has been removed (07B7 §3.5).
        """
        raise ReadbackReceiptVerificationError(
            "ERR_COORDINATOR_REMOVED: RecoveryCoordinator has been removed in decoupled adapter (07B7 §3.5)"
        )

    def execute_restart_repair(
        self,
        authorization: TrialExecutionAuthorization,
        action: Optional[PlannedTimelineAction] = None,
        plan: Optional[TimelineExecutionPlan] = None,
    ) -> Dict[str, Any]:
        """
        Executes fail-closed restart repair state machine strictly adhering to
        07B7 §5.3 Crash Boundary Table and Codex Findings LIVE-IMPL-02 / LIVE-IMPL-R1-02 / LIVE-IMPL-R2-02.
        Removed in decoupled adapter: RecoveryCoordinator has been removed; recovery is managed by DurableDispatchJournal (07B7 §3.5).
        """
        raise ReadbackReceiptVerificationError(
            "ERR_COORDINATOR_REMOVED: RecoveryCoordinator has been removed; recovery is managed by DurableDispatchJournal (07B7 §3.5)"
        )


    # ======================================================================
    # Descriptor-Backed Durable Proof Storage (LIVE-IMPL-05)
    # ======================================================================

    def _durable_proof_exists(self, proof_record_id: str) -> bool:
        """Checks if a durable proof record exists via directory descriptor."""
        dest_filename = f"proof_{proof_record_id}.proof.json"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(dest_filename, flags, dir_fd=self._proof_dir_fd)
            os.close(fd)
            return True
        except OSError as e:
            if e.errno == errno.ENOENT:
                return False
            return True

    def _persist_durable_proof_record(self, record: DurableProofRecord) -> None:
        """
        Atomically persists DurableProofRecord via descriptor-backed consume-once destination (LIVE-IMPL-05, LIVE-IMPL-R1-01).
        Fails if destination already exists (no overwrite). Fsyncs file and proof directory descriptor.
        Never falls back to os.replace.
        """
        dest_filename = f"proof_{record.proof_record_id}.proof.json"
        if self._durable_proof_exists(record.proof_record_id):
            raise ReadbackReceiptVerificationError(
                f"ERR_PROOF_ALREADY_CONSUMED: durable record '{dest_filename}' already exists on disk"
            )

        tmp_filename = f"proof_{record.proof_record_id}.tmp.{secrets.token_hex(8)}"
        record_bytes = canonical_json_bytes(record.to_dict())
        if len(record_bytes) > MAX_PROOF_RECORD_BYTES:
            raise AcceptanceGateError("ERR_PROOF_RECORD_EXCEEDS_CAP")

        create_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            fd = os.open(tmp_filename, create_flags, 0o600, dir_fd=self._proof_dir_fd)
            try:
                _write_all(fd, record_bytes)
                os.fsync(fd)
            finally:
                os.close(fd)

            # Atomic consume-once publication (LIVE-IMPL-R2-03)
            # Publishes the completed, fsynced temporary file without overwriting.
            _atomic_link_or_rename_noreplace(self._proof_dir_fd, tmp_filename, dest_filename)

            # Fsync directory descriptor (LIVE-IMPL-05)
            os.fsync(self._proof_dir_fd)
        except Exception as persist_err:
            if isinstance(persist_err, ReadbackReceiptVerificationError):
                raise
            raise AcceptanceGateError(f"ERR_PROOF_PERSIST_FAILED: {persist_err}") from persist_err
        finally:
            # Retain tmp_filename for controlled repair if atomic publication was unsupported;
            # otherwise unlink temporary file cleanly.
            exc = sys.exc_info()[1]
            is_unsupported = isinstance(exc, AcceptanceGateError) and "ERR_ATOMIC_NO_REPLACE_UNSUPPORTED" in str(exc)
            if not is_unsupported:
                try:
                    os.unlink(tmp_filename, dir_fd=self._proof_dir_fd)
                except OSError:
                    pass

    def _load_durable_proof_record_bounded(self, proof_record_id: str) -> DurableProofRecord:
        """Loads and deserializes DurableProofRecord using descriptor-backed bounded reader (LIVE-IMPL-05)."""
        dest_filename = f"proof_{proof_record_id}.proof.json"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            ffd = os.open(dest_filename, flags, dir_fd=self._proof_dir_fd)
        except OSError as e:
            if e.errno == errno.ENOENT:
                raise ReadbackReceiptVerificationError(f"ERR_PROOF_RECORD_NOT_FOUND: {dest_filename}")
            raise ReadbackReceiptVerificationError(f"ERR_PROOF_CANNOT_OPEN: {dest_filename}: {e}")

        try:
            raw_bytes = _read_bytes_bounded(ffd, max_bytes=MAX_PROOF_RECORD_BYTES)
        finally:
            os.close(ffd)

        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except Exception as parse_err:
            raise ReadbackReceiptVerificationError(f"ERR_PROOF_JSON_CORRUPT: {parse_err}") from parse_err

        return DurableProofRecord.from_dict(data)

    def _find_durable_proof_id_for_execution(self, execution_id: str, action_index: int) -> Optional[str]:
        """Finds durable proof record id on disk for a given execution_id and action_index."""
        try:
            entries = os.listdir(self._proof_ledger_dir)
        except OSError:
            return None

        for fname in entries:
            if fname.startswith("proof_") and fname.endswith(".proof.json"):
                record_id = fname[len("proof_") : -len(".proof.json")]
                try:
                    rec = self._load_durable_proof_record_bounded(record_id)
                    if rec.execution_id == execution_id and rec.action_index == action_index:
                        return record_id
                except Exception:
                    continue
        return None

    # ======================================================================
    # Descriptor-Backed Durable Receipt Publication (LIVE-IMPL-06)
    # ======================================================================

    def _publish_durable_trial_receipt(
        self,
        execution_id: str,
        authorization: TrialExecutionAuthorization,
        state: str,
        completed_at_iso: Optional[str] = None,
        last_error_code: str = "",
        last_error_message: str = "",
    ) -> TrialExecutionReceipt:
        """
        Publishes TrialExecutionReceipt atomically using relative descriptor-backed operations,
        write-all loop, file and directory fsync, and 100% full deserialized DTO readback (LIVE-IMPL-06).
        """
        receipts_dir = self._proof_ledger_dir or str(self._journal.state_dir)
        receipts_dir_fd = None

        if receipts_dir_fd is None:
            if not receipts_dir or not os.path.exists(receipts_dir):
                raise WholePlanReconciliationError("ERR_RECEIPTS_DIR_UNAVAILABLE: receipts directory not accessible")
            dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            receipts_dir_fd = os.open(receipts_dir, dir_flags)
            must_close_dir_fd = True
        else:
            must_close_dir_fd = False

        receipt_id = f"rcpt_{execution_id}"
        trial_receipt = TrialExecutionReceipt(
            trial_receipt_version=1,
            receipt_id=receipt_id,
            execution_id=execution_id,
            authorization_id=authorization.authorization_id,
            target_project_uid=authorization.target_project_uid,
            target_timeline_uid=authorization.target_timeline_uid,
            plan_idempotency_key=authorization.plan_idempotency_key,
            execution_receipt={
                "execution_id": execution_id,
                "state": state,
                "completed_at_iso": completed_at_iso,
                "last_error_code": last_error_code,
                "last_error_message": last_error_message,
                "published_at_iso": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
        )

        receipt_filename = f"{execution_id}.trial_receipt.json"
        tmp_filename = f"{receipt_filename}.tmp.{secrets.token_hex(16)}"
        receipt_data = canonical_json_bytes(asdict(trial_receipt))
        if len(receipt_data) > MAX_RECEIPT_BYTES:
            if must_close_dir_fd:
                os.close(receipts_dir_fd)
            raise AcceptanceGateError("ERR_RECEIPT_EXCEEDS_MAX_CAP")

        create_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )

        try:
            # 1. Open temp file via receipts directory descriptor
            fd = os.open(tmp_filename, create_flags, 0o600, dir_fd=receipts_dir_fd)
            try:
                # 2. Write-all loop to handle partial writes (LIVE-IMPL-06)
                _write_all(fd, receipt_data)
                # 3. Fsync file descriptor
                os.fsync(fd)
            finally:
                os.close(fd)

            # 4. Atomic replace using descriptor-relative arguments with basenames (LIVE-IMPL-R1-05)
            os.replace(
                tmp_filename,
                receipt_filename,
                src_dir_fd=receipts_dir_fd,
                dst_dir_fd=receipts_dir_fd,
            )

            # 5. Fsync directory descriptor to commit directory entry (LIVE-IMPL-06)
            try:
                os.fsync(receipts_dir_fd)
            except OSError as dir_fsync_err:
                # Post-replace failure: state is in-doubt, do not unlink!
                raise ReceiptDurabilityError(
                    f"ERR_RECEIPT_DIRECTORY_FSYNC_FAILED: {dir_fsync_err}; receipt replaced but directory fsync failed"
                ) from dir_fsync_err

        except Exception as pub_err:
            if not isinstance(pub_err, ReceiptDurabilityError):
                try:
                    os.unlink(tmp_filename, dir_fd=receipts_dir_fd)
                except Exception:
                    pass
            if must_close_dir_fd:
                os.close(receipts_dir_fd)
            if isinstance(pub_err, (AcceptanceGateError, ReceiptDurabilityError)):
                raise
            raise AcceptanceGateError(f"ERR_RECEIPT_ATOMIC_PUBLICATION_FAILED: {pub_err}") from pub_err

        # 6. Descriptor-backed bounded read-back & exact-key DTO equality verification (LIVE-IMPL-06, LIVE-IMPL-R1-05)
        read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            rfd = os.open(receipt_filename, read_flags, dir_fd=receipts_dir_fd)
            try:
                verified_bytes = _read_bytes_bounded(rfd, max_bytes=MAX_RECEIPT_BYTES)
            finally:
                os.close(rfd)
        finally:
            if must_close_dir_fd:
                os.close(receipts_dir_fd)

        try:
            verified_dict = json.loads(verified_bytes.decode("utf-8"))
        except Exception as parse_e:
            raise ReceiptDurabilityError(f"ERR_RECEIPT_JSON_CORRUPT: {parse_e}") from parse_e

        # Exact-key schema validation (reject extra or missing keys)
        if not isinstance(verified_dict, dict):
            raise ReceiptDurabilityError("ERR_RECEIPT_NOT_DICT")
        if set(verified_dict.keys()) != EXACT_RECEIPT_KEYS:
            diff_m = EXACT_RECEIPT_KEYS - set(verified_dict.keys())
            diff_e = set(verified_dict.keys()) - EXACT_RECEIPT_KEYS
            raise ReceiptDurabilityError(f"ERR_RECEIPT_SCHEMA_MISMATCH: missing={diff_m}, extra={diff_e}")

        exec_rcpt = verified_dict.get("execution_receipt")
        if not isinstance(exec_rcpt, dict):
            raise ReceiptDurabilityError("ERR_EXECUTION_RECEIPT_NOT_DICT")
        if set(exec_rcpt.keys()) != EXACT_EXECUTION_RECEIPT_KEYS:
            diff_m = EXACT_EXECUTION_RECEIPT_KEYS - set(exec_rcpt.keys())
            diff_e = set(exec_rcpt.keys()) - EXACT_EXECUTION_RECEIPT_KEYS
            raise ReceiptDurabilityError(f"ERR_EXECUTION_RECEIPT_SCHEMA_MISMATCH: missing={diff_m}, extra={diff_e}")

        # Complete deserialization and 100% full DTO equality verification including nested published_at_iso
        deserialized_receipt = TrialExecutionReceipt(
            trial_receipt_version=int(verified_dict["trial_receipt_version"]),
            receipt_id=str(verified_dict["receipt_id"]),
            execution_id=str(verified_dict["execution_id"]),
            authorization_id=str(verified_dict["authorization_id"]),
            target_project_uid=str(verified_dict["target_project_uid"]),
            target_timeline_uid=str(verified_dict["target_timeline_uid"]),
            plan_idempotency_key=str(verified_dict["plan_idempotency_key"]),
            execution_receipt=dict(exec_rcpt),
        )

        if deserialized_receipt != trial_receipt:
            raise ReceiptDurabilityError(
                "ERR_RECEIPT_DTO_EQUALITY_MISMATCH: deserialized receipt does not equal intended TrialExecutionReceipt"
            )

        return trial_receipt

    def _load_existing_trial_receipt(self, execution_id: str) -> Optional[TrialExecutionReceipt]:
        """Loads and deserializes existing TrialExecutionReceipt from disk, if present (LIVE-IMPL-R3-01)."""
        receipts_dir = self._proof_ledger_dir or str(self._journal.state_dir)
        receipts_dir_fd = None
        if receipts_dir_fd is None:
            if not receipts_dir or not os.path.exists(receipts_dir):
                return None
            dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                receipts_dir_fd = os.open(receipts_dir, dir_flags)
            except OSError:
                return None
            must_close_dir_fd = True
        else:
            must_close_dir_fd = False

        receipt_filename = f"{execution_id}.trial_receipt.json"
        read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            try:
                rfd = os.open(receipt_filename, read_flags, dir_fd=receipts_dir_fd)
            except OSError as open_err:
                if open_err.errno == errno.ENOENT:
                    return None
                raise ReceiptDurabilityError(f"ERR_CANNOT_OPEN_EXISTING_RECEIPT: {open_err}") from open_err

            try:
                raw_bytes = _read_bytes_bounded(rfd, max_bytes=MAX_RECEIPT_BYTES)
            finally:
                os.close(rfd)
        finally:
            if must_close_dir_fd:
                os.close(receipts_dir_fd)

        try:
            verified_dict = json.loads(raw_bytes.decode("utf-8"))
        except Exception as parse_e:
            raise ReceiptDurabilityError(f"ERR_RECEIPT_JSON_CORRUPT: {parse_e}") from parse_e

        if not isinstance(verified_dict, dict) or set(verified_dict.keys()) != EXACT_RECEIPT_KEYS:
            raise ReceiptDurabilityError("ERR_RECEIPT_SCHEMA_MISMATCH")

        exec_rcpt = verified_dict.get("execution_receipt")
        if not isinstance(exec_rcpt, dict) or set(exec_rcpt.keys()) != EXACT_EXECUTION_RECEIPT_KEYS:
            raise ReceiptDurabilityError("ERR_EXECUTION_RECEIPT_SCHEMA_MISMATCH")

        return TrialExecutionReceipt(
            trial_receipt_version=int(verified_dict["trial_receipt_version"]),
            receipt_id=str(verified_dict["receipt_id"]),
            execution_id=str(verified_dict["execution_id"]),
            authorization_id=str(verified_dict["authorization_id"]),
            target_project_uid=str(verified_dict["target_project_uid"]),
            target_timeline_uid=str(verified_dict["target_timeline_uid"]),
            plan_idempotency_key=str(verified_dict["plan_idempotency_key"]),
            execution_receipt=dict(exec_rcpt),
        )
