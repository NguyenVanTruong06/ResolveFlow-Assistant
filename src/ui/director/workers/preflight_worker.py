"""src/ui/director/workers/preflight_worker.py
Bộ thực thi luồng nền QThread cho tiền kiểm khả dụng nguồn và thư viện tài nguyên (G07 Preflight Worker).

RÀNG BUỘC KỸ THUẬT:
- Adapter QThread độc lập với GUI, không tham chiếu widget, không gọi DaVinci Resolve.
- Constructor 0 I/O, 0 factory call, 0 model/network.
- Native QThread.finished giữ nguyên cho vòng đời C++; terminal = Signal(object) phát SourceCatalogReadinessResult.
- Outer try/except/finally đảm bảo phát chính xác 1 lần terminal khi hoàn tất/hủy/lỗi.
- Hỗ trợ cooperative cancel đa tầng; không terminate(); không wait(2000) rồi bỏ rơi worker.
- Sở hữu độc quyền ReadinessRequest (defensive copy tại constructor), miễn nhiễm hoàn toàn với mutation từ caller.
- Từ chối kết quả mạo danh (forged inspector result), kiểm tra kiểu generation nghiêm ngặt (loại trừ bool), từ chối empty-success.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from PySide6.QtCore import QObject, QThread, Signal

from src.core.source_catalog_readiness import (
    AssetAssignmentObservation,
    ReadinessRequest,
    SourceCatalogReadinessResult,
    SourceClipObservation,
    inspect_source_and_catalog_readiness,
    normalize_readiness_result,
    validate_execution_evidence_binding,
)
from src.ui.director.workers.asset_matching_worker import read_matching_storage_context

_UNSET = object()


class PreflightWorker(QThread):
    """Worker QThread tiền kiểm khả dụng nguồn và catalog tài nguyên trên luồng nền."""

    terminal = Signal(object)  # Phát đúng 1 SourceCatalogReadinessResult
    progress = Signal(int, str)  # (percent: int 0..100, message: str)

    def __init__(
        self,
        request: ReadinessRequest,
        *,
        inspector: Optional[Callable[..., SourceCatalogReadinessResult]] = None,
        storage_factory: Optional[Callable[[], Any]] = None,
        storage_context_reader: Any = _UNSET,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)

        # 1. Thẩm định tham số đầu vào (Constructor thuần in-memory, 0 I/O)
        if not isinstance(request, ReadinessRequest):
            raise ValueError(
                f"ERR_INVALID_REQUEST: request phải là thể hiện của ReadinessRequest, nhận: {type(request).__name__}"
            )

        if inspector is not None and not callable(inspector):
            raise ValueError("ERR_INVALID_INSPECTOR: inspector nếu được truyền phải là callable.")

        if storage_factory is not None and not callable(storage_factory):
            raise ValueError("ERR_INVALID_FACTORY: storage_factory nếu được truyền phải là callable.")

        if storage_context_reader is not _UNSET and storage_context_reader is not None and not callable(storage_context_reader):
            raise ValueError("ERR_INVALID_CONTEXT_READER: storage_context_reader nếu được truyền phải là callable hoặc None.")

        # 2. Sở hữu request độc quyền thông qua khởi tạo ReadinessRequest mới (defensive clone)
        # Bất kỳ đột biến nào từ caller sau thời điểm này (đổi generation, xóa thuộc tính approval)
        # đều không ảnh hưởng đến worker.
        self._request: ReadinessRequest = ReadinessRequest(
            request_id=request.request_id,
            generation=request.generation,
            proposal=request.proposal,
            approval=request.approval,
            verify_source_execution=request.verify_source_execution,
            timeline_fps=request.timeline_fps,
        )

        # 3. Lưu giữ các danh tính bất biến và bản đồ kỳ vọng
        self._request_id: str = self._request.request_id
        self._generation: int = self._request.generation
        self._proposal_id: str = self._request.proposal.proposal_id
        self._proposal_revision: str = self._request.approval.proposal_revision
        self._approval_id: str = self._request.approval.approval_id
        self._verify_source_execution: bool = self._request.verify_source_execution
        self._timeline_fps: Optional[Any] = self._request.timeline_fps

        # Bản đồ clip nguồn kỳ vọng: {clip_id: path}
        source_clips = ()
        if hasattr(self._request.proposal, "source_binding") and isinstance(self._request.proposal.source_binding, (Mapping, dict)):
            source_clips = self._request.proposal.source_binding.get("clips", ()) or ()
        expected_source_map: dict[str, str] = {}
        for c in source_clips:
            if isinstance(c, (Mapping, dict)) and "clip_id" in c:
                cid = str(c.get("clip_id", ""))
                cpath = str(c.get("path") or c.get("current_path") or "")
                expected_source_map[cid] = cpath
        self._expected_source_map: dict[str, str] = expected_source_map
        self._expected_source_clip_ids: frozenset[str] = frozenset(expected_source_map.keys())
        self._expected_source_count: int = len(expected_source_map)
        self._source_clips_raw: tuple[Mapping[str, Any], ...] = tuple(source_clips)
        self._proposal_manifest_id: str = getattr(self._request.proposal, "manifest_id", "")

        # Bản đồ tài nguyên kỳ vọng: {demand_id: (asset_id, source_kind, slot)}
        raw_assignments = getattr(self._request.proposal, "proposed_asset_assignments", ()) or ()
        expected_assignment_map: dict[str, tuple[str, str, str]] = {}
        for a in raw_assignments:
            if isinstance(a, (Mapping, dict)) and "demand_id" in a:
                did = str(a.get("demand_id", ""))
                aid = str(a.get("asset_id", ""))
                skind = str(a.get("source_kind", ""))
                slot = str(a.get("slot", ""))
                expected_assignment_map[did] = (aid, skind, slot)
        self._expected_assignment_map: dict[str, tuple[str, str, str]] = expected_assignment_map
        self._expected_assignment_demand_ids: frozenset[str] = frozenset(expected_assignment_map.keys())
        self._expected_assignment_count: int = len(expected_assignment_map)

        self._inspector: Callable[..., SourceCatalogReadinessResult] = inspector or inspect_source_and_catalog_readiness
        self._storage_factory: Optional[Callable[[], Any]] = storage_factory
        if storage_context_reader is _UNSET:
            self._storage_context_reader: Optional[Callable[[Any], Any]] = read_matching_storage_context
        else:
            self._storage_context_reader = storage_context_reader

        # Cờ cooperative cancel và cờ bảo vệ phát tín hiệu duy nhất
        self._cancel_requested: bool = False
        self._terminal_emitted: bool = False

    def cancel(self) -> None:
        """Yêu cầu dừng cooperative; an toàn idempotent, có hiệu lực ngay cả trước khi start()."""
        self._cancel_requested = True
        self.requestInterruption()

    def _is_cancelled(self) -> bool:
        """Kiểm tra tổng hợp cờ cancel nội bộ và Qt interruption request."""
        return self._cancel_requested or self.isInterruptionRequested()

    def _emit_terminal(self, outcome: SourceCatalogReadinessResult) -> None:
        """Bảo đảm phát chính xác 1 lần duy nhất tín hiệu terminal."""
        if not self._terminal_emitted:
            self._terminal_emitted = True
            self.terminal.emit(outcome)

    def _make_cancelled_result(self, message: str = "Tiến trình tiền kiểm đã bị hủy theo yêu cầu.") -> SourceCatalogReadinessResult:
        """Tạo kết quả bị hủy với ràng buộc đúng theo scalar identities đã sở hữu."""
        return SourceCatalogReadinessResult(
            request_id=self._request_id,
            generation=self._generation,
            proposal_id=self._proposal_id,
            proposal_revision=self._proposal_revision,
            approval_id=self._approval_id,
            status="cancelled",
            checks_passed=False,
            ready_to_execute=False,
            source_observations=(),
            catalog_observations=(),
            diagnostics=(message,),
        )

    def _make_failed_result(self, error_message: str) -> SourceCatalogReadinessResult:
        """Tạo kết quả thất bại với ràng buộc đúng theo scalar identities đã sở hữu."""
        return SourceCatalogReadinessResult(
            request_id=self._request_id,
            generation=self._generation,
            proposal_id=self._proposal_id,
            proposal_revision=self._proposal_revision,
            approval_id=self._approval_id,
            status="failed",
            checks_passed=False,
            ready_to_execute=False,
            source_observations=(),
            catalog_observations=(),
            diagnostics=(f"ERR_PREFLIGHT_WORKER_FAILURE: {error_message}",),
        )

    def run(self) -> None:
        """Điểm thực thi chính trên luồng nền QThread."""
        # Checkpoint 0: Kiểm tra hủy trước khi bắt đầu
        if self._is_cancelled():
            self._emit_terminal(self._make_cancelled_result())
            return

        try:
            # Relay tiến độ với kiểm tra cancel tại mỗi nhịp
            def _on_progress(percent: int, message: str) -> None:
                if not self._is_cancelled():
                    self.progress.emit(percent, message)

            # Lazy storage factory: Chỉ khởi tạo khi inspector yêu cầu và chạy trên worker thread
            def _wrapped_storage_factory() -> Any:
                if self._is_cancelled():
                    raise InterruptedError("Tiến trình đã bị hủy trước khi nạp kho lưu trữ.")
                if self._storage_factory is not None:
                    return self._storage_factory()
                from src.core.storage_manager import StorageManager
                return StorageManager()

            # Checkpoint 1: Trước khi gọi inspector
            if self._is_cancelled():
                self._emit_terminal(self._make_cancelled_result())
                return

            # Thực thi inspector
            result = self._inspector(
                self._request,
                storage_provider=_wrapped_storage_factory,
                storage_context_reader=self._storage_context_reader,
                is_cancelled=self._is_cancelled,
                progress_callback=_on_progress,
            )

            # Checkpoint 2: Sau khi inspector trả về, nếu đã bị cancel thì discard kết quả (không success)
            if self._is_cancelled():
                self._emit_terminal(self._make_cancelled_result())
                return

            # 1. Chuẩn hóa, thẩm định nghiêm ngặt và clone độc quyền kết quả (shared normalizer)
            normalized_result = normalize_readiness_result(result)

            # 2. Đối chiếu danh tính scalar với request đã sở hữu
            if (
                normalized_result.request_id != self._request_id
                or normalized_result.generation != self._generation
                or normalized_result.proposal_id != self._proposal_id
                or normalized_result.proposal_revision != self._proposal_revision
                or normalized_result.approval_id != self._approval_id
            ):
                raise ValueError("ERR_FORGED_INSPECTOR_RESULT: Kết quả inspector không khớp binding của request đã sở hữu.")

            # 3. Đối chiếu đồ thị quan sát chi tiết CHỈ KHI completed và checks_passed=True
            if normalized_result.status == "completed" and normalized_result.checks_passed is True:
                # 3.1 Nguồn: số lượng, không trùng clip_id, khớp path, các cờ predicate, recheck_passed, stat_identity, error_code rỗng
                if len(normalized_result.source_observations) != len(self._expected_source_map):
                    raise ValueError(
                        f"ERR_UNEXPECTED_SOURCE_COUNT: số lượng clip quan sát ({len(normalized_result.source_observations)}) "
                        f"không khớp số lượng kỳ vọng ({len(self._expected_source_map)})"
                    )
                seen_clips: set[str] = set()
                for s_obs in normalized_result.source_observations:
                    if s_obs.clip_id in seen_clips:
                        raise ValueError(f"ERR_DUPLICATE_SOURCE_ROW: clip_id trùng lặp trong source_observations: {s_obs.clip_id!r}")
                    seen_clips.add(s_obs.clip_id)

                    if s_obs.clip_id not in self._expected_source_map:
                        raise ValueError(f"ERR_UNEXPECTED_SOURCE_CLIP_ID: clip_id {s_obs.clip_id!r} không thuộc kịch bản.")

                    expected_path = self._expected_source_map[s_obs.clip_id]
                    if s_obs.path != expected_path:
                        raise ValueError(
                            f"ERR_SOURCE_PATH_MISMATCH: path {s_obs.path!r} của clip {s_obs.clip_id!r} "
                            f"không khớp path kỳ vọng {expected_path!r}."
                        )

                    if not (s_obs.exists and s_obs.is_regular_file and s_obs.is_non_empty and s_obs.is_readable and s_obs.recheck_passed):
                        raise ValueError(f"ERR_SOURCE_PREDICATE_FAILED: Một hoặc nhiều cờ kiểm tra nguồn của clip {s_obs.clip_id!r} là False.")

                    if s_obs.stat_identity is None or not isinstance(s_obs.stat_identity, tuple) or len(s_obs.stat_identity) != 5:
                        raise ValueError(f"ERR_SOURCE_STAT_IDENTITY_MISSING: stat_identity của clip {s_obs.clip_id!r} không hợp lệ.")

                    if s_obs.error_code != "":
                        raise ValueError(f"ERR_SOURCE_ERROR_CODE_NONEMPTY: error_code của clip {s_obs.clip_id!r} không rỗng: {s_obs.error_code!r}")

                # 3.2 Catalog: số lượng, không trùng demand_id, khớp asset/kind/slot, các cờ predicate, recheck_passed, guard status online, master path, error_code rỗng
                if len(normalized_result.catalog_observations) != len(self._expected_assignment_map):
                    raise ValueError(
                        f"ERR_UNEXPECTED_CATALOG_COUNT: số lượng catalog quan sát ({len(normalized_result.catalog_observations)}) "
                        f"không khớp số lượng assignments ({len(self._expected_assignment_map)})"
                    )
                seen_demands: set[str] = set()
                for c_obs in normalized_result.catalog_observations:
                    if c_obs.demand_id in seen_demands:
                        raise ValueError(f"ERR_DUPLICATE_CATALOG_ROW: demand_id trùng lặp trong catalog_observations: {c_obs.demand_id!r}")
                    seen_demands.add(c_obs.demand_id)

                    if c_obs.demand_id not in self._expected_assignment_map:
                        raise ValueError(f"ERR_UNEXPECTED_DEMAND_ID: demand_id {c_obs.demand_id!r} không thuộc kịch bản.")

                    exp_aid, exp_skind, exp_slot = self._expected_assignment_map[c_obs.demand_id]
                    if (c_obs.asset_id, c_obs.source_kind, c_obs.slot) != (exp_aid, exp_skind, exp_slot):
                        raise ValueError(
                            f"ERR_CATALOG_MAPPING_MISMATCH: demand {c_obs.demand_id!r} có (asset_id, source_kind, slot) = "
                            f"{(c_obs.asset_id, c_obs.source_kind, c_obs.slot)!r}, kỳ vọng {(exp_aid, exp_skind, exp_slot)!r}."
                        )

                    if not (c_obs.found_in_catalog and c_obs.provenance_matched and c_obs.guard_allowed and c_obs.recheck_passed):
                        raise ValueError(f"ERR_CATALOG_PREDICATE_FAILED: Một hoặc nhiều cờ kiểm tra catalog của demand {c_obs.demand_id!r} là False.")

                    if c_obs.duplicate_in_catalog is not False:
                        raise ValueError(f"ERR_CATALOG_DUPLICATE: duplicate_in_catalog của demand {c_obs.demand_id!r} không phải False.")

                    if c_obs.guard_status != "online":
                        raise ValueError(f"ERR_GUARD_STATUS_NOT_ONLINE: guard_status của demand {c_obs.demand_id!r} là {c_obs.guard_status!r}, kỳ vọng 'online'.")

                    if not c_obs.resolved_master_path or not c_obs.resolved_master_path.strip():
                        raise ValueError(f"ERR_MASTER_PATH_EMPTY: resolved_master_path của demand {c_obs.demand_id!r} để trống.")

                    if c_obs.master_stat_identity is None or not isinstance(c_obs.master_stat_identity, tuple) or len(c_obs.master_stat_identity) != 5:
                        raise ValueError(f"ERR_MASTER_STAT_IDENTITY_MISSING: master_stat_identity của demand {c_obs.demand_id!r} không hợp lệ.")

                    if c_obs.error_code != "":
                        raise ValueError(f"ERR_CATALOG_ERROR_CODE_NONEMPTY: error_code của demand {c_obs.demand_id!r} không rỗng: {c_obs.error_code!r}")

                # 3.3 Deep Source Evidence & Geometry verification khi opt-in verify_source_execution=True
                if self._verify_source_execution:
                    if normalized_result.source_execution_evidence is None:
                        raise ValueError("ERR_MISSING_EXECUTION_EVIDENCE: deep mode yêu cầu source_execution_evidence không được là None.")
                    
                    binding_errors = validate_execution_evidence_binding(
                        evidence_by_clip=normalized_result.source_execution_evidence,
                        source_clips_raw=self._source_clips_raw,
                        source_observations=normalized_result.source_observations,
                        proposal_manifest_id=self._proposal_manifest_id,
                    )
                    if binding_errors:
                        raise ValueError(f"ERR_EVIDENCE_BINDING_FAILED: {binding_errors[0]}")

                    if normalized_result.source_geometry is None:
                        raise ValueError("ERR_MISSING_SOURCE_GEOMETRY: deep mode yêu cầu source_geometry không được là None.")

                    if normalized_result.source_geometry.status != "GEOMETRY_VERIFIED":
                        raise ValueError(
                            f"ERR_GEOMETRY_NOT_VERIFIED: checks_passed=True nhưng source_geometry.status là "
                            f"{normalized_result.source_geometry.status!r}"
                        )
                    if normalized_result.source_geometry.proposal_id != self._proposal_id:
                        raise ValueError("ERR_GEOMETRY_PROPOSAL_MISMATCH: source_geometry.proposal_id không khớp.")
                    if normalized_result.source_geometry.proposal_revision != self._proposal_revision:
                        raise ValueError("ERR_GEOMETRY_REVISION_MISMATCH: source_geometry.proposal_revision không khớp.")
                    if normalized_result.source_geometry.timeline_fps != self._timeline_fps:
                        raise ValueError("ERR_GEOMETRY_FPS_MISMATCH: source_geometry.timeline_fps không khớp timeline_fps của request.")

            # Checkpoint 3: Trước khi phát tín hiệu terminal cuối cùng
            if self._is_cancelled():
                self._emit_terminal(self._make_cancelled_result())
                return

            self._emit_terminal(normalized_result)

        except Exception as exc:
            # Nếu người dùng đã yêu cầu hủy trong khi xảy ra ngoại lệ, ưu tiên cancelled
            if self._is_cancelled():
                self._emit_terminal(self._make_cancelled_result())
                return

            err_msg = str(exc) or type(exc).__name__
            self._emit_terminal(self._make_failed_result(err_msg))

        finally:
            # Đảm bảo phát terminal nếu có sự cố ngoài dự kiến chưa phát
            if not self._terminal_emitted:
                self._emit_terminal(self._make_failed_result("Tiến trình luồng ngầm kết thúc mà không phát kết quả."))
