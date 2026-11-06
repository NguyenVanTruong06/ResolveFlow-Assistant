"""
src/core/contracts/plan_demand_contracts.py
=============================================================================
Hợp đồng dữ liệu bất biến (Frozen DTOs) cho Adapter nhu cầu tài nguyên kịch bản:
- AssetDemandItem: Từng nhu cầu tài nguyên độc lập từ kịch bản Slot 1.
- AssetDemandBatch: Tập hợp các nhu cầu và requests tra cứu kèm revision.
- DemandMatchEntry: Kết quả ghép đôi nhu cầu với kết quả matcher tư vấn.
- AssetMatchingSidecar: Báo cáo tư vấn toàn diện, đếm phân hoạch tách biệt.

Quy định ràng buộc (Codex decisions 06B3, 06B3-I1-R1 & 06B3-I1-R2):
- Tuyệt đối bất biến (frozen=True), sở hữu tuple độc lập, không tham chiếu dict ngoài.
- Thẩm định toàn diện (Shared validation) dùng chung cho cả Constructor và Assemble:
  * Validate item scalar/Optional fields, status/reason, UTF-8, safe numeric (chặn overflow, bool, negative).
  * Validate OfflineAssetMatchRequest qua validate_match_request trước khi hash/map.
  * Validate OfflineAssetCandidate: chỉ online/offline, duration None hoặc >0 finite, tuple signals UTF-8.
  * Thẩm định quan hệ bijection 1-1 giữa eligible demands và requests.
  * Ràng buộc demand_id chuẩn JSON compact array [plan_revision, slot, index].
  * Đẳng thức phân hoạch tách biệt: matched + unmatched + invalid + empty_query + unsupported + overflow == total.
- Không phụ thuộc PySide6 / UI / StorageManager / Resolve / Network / Clock / Random.
=============================================================================
"""

import json
import math
from dataclasses import dataclass
from typing import Any, Optional, Set, Tuple

from src.core.contracts.asset_matching_contracts import (
    OfflineAssetCandidate,
    OfflineAssetMatchRequest,
    OfflineAssetMatchResult,
    validate_match_request,
)

VALID_STATUSES: Tuple[str, ...] = (
    "eligible", "invalid", "unsupported", "empty_query", "overflow"
)

VALID_SLOTS: Tuple[str, ...] = (
    "broll_inserts", "sfx_inserts"
)

VALID_AVAILABILITIES: Tuple[str, ...] = (
    "online", "offline"
)

VALID_SOURCE_KINDS: Tuple[str, ...] = (
    "managed", "linked", "builtin", "generated"
)

MAX_ENTRIES_PRECHECK: int = 4096
MAX_BATCH_SIZE: int = 256


def _is_utf8_string(val: Any) -> bool:
    """Kiểm tra chuỗi UTF-8 hợp lệ, không chứa lone surrogates."""
    if not isinstance(val, str):
        return False
    try:
        val.encode("utf-8")
        return True
    except (UnicodeEncodeError, UnicodeError):
        return False


def validate_safe_numeric(val: Any, field_name: str, *, allow_zero: bool) -> Optional[float]:
    """
    Thẩm định an toàn giá trị số, bắt tràn số (OverflowError) và phân biệt boolean.
    Ném ValueError có kiểm soát nếu giá trị không hợp lệ.
    """
    if val is None:
        return None
    if type(val) is bool:
        raise ValueError(f"Trường '{field_name}' không được nhận giá trị kiểu boolean.")
    if type(val) not in (int, float):
        raise ValueError(f"Trường '{field_name}' bắt buộc phải là số thực hoặc số nguyên.")

    try:
        f_val = float(val)
    except (OverflowError, ValueError):
        raise ValueError(f"Trường '{field_name}' bị tràn số (overflow).")

    if not math.isfinite(f_val):
        raise ValueError(f"Trường '{field_name}' phải là số hữu hạn (không nhận NaN hoặc Inf).")

    if allow_zero:
        if f_val < 0.0:
            raise ValueError(f"Trường '{field_name}' phải là số không âm (>= 0.0).")
    else:
        if f_val <= 0.0:
            raise ValueError(f"Trường '{field_name}' phải là số dương (> 0.0).")

    return f_val


def validate_asset_demand_item(item: Any) -> None:
    """Thẩm định tính toàn vẹn và nhất quán của một AssetDemandItem."""
    if not isinstance(item, AssetDemandItem):
        raise ValueError(f"Phần tử bắt buộc phải là AssetDemandItem, nhận được: {type(item).__name__}.")

    if not _is_utf8_string(item.demand_id) or not item.demand_id.strip():
        raise ValueError("demand_id bắt buộc là chuỗi UTF-8 không rỗng.")
    if item.source_slot not in VALID_SLOTS:
        raise ValueError(f"source_slot ({item.source_slot}) bắt buộc phải là một trong {VALID_SLOTS}.")
    if type(item.source_index) is not int or item.source_index < 0:
        raise ValueError("source_index bắt buộc là số nguyên >= 0.")
    if item.status not in VALID_STATUSES:
        raise ValueError(f"status ({item.status}) bắt buộc phải là một trong {VALID_STATUSES}.")

    # Thẩm định tính nhất quán giữa status và rejection_reason
    if item.status == "eligible":
        if item.rejection_reason is not None:
            raise ValueError("rejection_reason bắt buộc phải là None khi status='eligible'.")
    elif item.status == "invalid":
        if item.rejection_reason != "ERR_DEMAND_INVALID":
            raise ValueError("rejection_reason bắt buộc phải là 'ERR_DEMAND_INVALID' khi status='invalid'.")
    elif item.status == "unsupported":
        if item.rejection_reason != "ERR_UNSUPPORTED_CATEGORY":
            raise ValueError("rejection_reason bắt buộc phải là 'ERR_UNSUPPORTED_CATEGORY' khi status='unsupported'.")
    elif item.status == "empty_query":
        if item.rejection_reason != "ERR_DEMAND_QUERY_EMPTY":
            raise ValueError("rejection_reason bắt buộc phải là 'ERR_DEMAND_QUERY_EMPTY' khi status='empty_query'.")
    elif item.status == "overflow":
        if item.rejection_reason != "ERR_BATCH_CAP_OVERFLOW":
            raise ValueError("rejection_reason bắt buộc phải là 'ERR_BATCH_CAP_OVERFLOW' khi status='overflow'.")

    # Thẩm định metadata placement
    if item.timeline_sec is not None:
        validate_safe_numeric(item.timeline_sec, "timeline_sec", allow_zero=True)
    if item.desired_duration_sec is not None:
        validate_safe_numeric(item.desired_duration_sec, "desired_duration_sec", allow_zero=False)

    # Thẩm định các trường chuỗi
    if item.raw_type is not None:
        if not isinstance(item.raw_type, str) or not _is_utf8_string(item.raw_type):
            raise ValueError("raw_type phải là chuỗi UTF-8.")
    if item.category is not None:
        if not isinstance(item.category, str) or not _is_utf8_string(item.category):
            raise ValueError("category phải là chuỗi UTF-8.")
    if not isinstance(item.raw_query_hint, str) or not _is_utf8_string(item.raw_query_hint):
        raise ValueError("raw_query_hint phải là chuỗi UTF-8.")
    if not isinstance(item.query_text, str) or not _is_utf8_string(item.query_text):
        raise ValueError("query_text phải là chuỗi UTF-8.")
    if item.query_source is not None:
        if not isinstance(item.query_source, str) or not _is_utf8_string(item.query_source):
            raise ValueError("query_source phải là chuỗi UTF-8.")


def validate_demand_candidate(cand: Any, expected_category: str) -> None:
    """Thẩm định tính toàn vẹn và bất biến của một OfflineAssetCandidate."""
    if not isinstance(cand, OfflineAssetCandidate):
        raise ValueError(f"Mỗi candidate bắt buộc là OfflineAssetCandidate, nhận được: {type(cand).__name__}.")

    if not isinstance(cand.asset_id, str) or not _is_utf8_string(cand.asset_id) or not cand.asset_id.strip():
        raise ValueError("asset_id của candidate bắt buộc là chuỗi UTF-8 không rỗng.")
    if not isinstance(cand.name, str) or not _is_utf8_string(cand.name) or not cand.name.strip():
        raise ValueError("name của candidate bắt buộc là chuỗi UTF-8 không rỗng.")
    if not isinstance(cand.category, str) or not _is_utf8_string(cand.category) or cand.category != expected_category:
        raise ValueError(f"Category của candidate ('{cand.category}') không khớp category của result ('{expected_category}').")
    if not isinstance(cand.subcategory, str) or not _is_utf8_string(cand.subcategory):
        raise ValueError("subcategory của candidate bắt buộc là chuỗi UTF-8.")

    if cand.source_kind not in VALID_SOURCE_KINDS:
        raise ValueError(f"source_kind của candidate ('{cand.source_kind}') không hợp lệ. Chỉ nhận {VALID_SOURCE_KINDS}.")
    if cand.availability not in VALID_AVAILABILITIES:
        raise ValueError(f"availability của candidate ('{cand.availability}') không hợp lệ. Chỉ chấp nhận 'online' hoặc 'offline'.")

    if type(cand.match_score) is not int or cand.match_score < 0 or cand.match_score > 100:
        raise ValueError("match_score của candidate bắt buộc là số nguyên trong khoảng [0, 100].")

    if not isinstance(cand.matched_signals, tuple) or not all(isinstance(s, str) and _is_utf8_string(s) for s in cand.matched_signals):
        raise ValueError("matched_signals của candidate bắt buộc là tuple của các chuỗi UTF-8.")

    if not isinstance(cand.pack_name, str) or not _is_utf8_string(cand.pack_name):
        raise ValueError("pack_name của candidate bắt buộc là chuỗi UTF-8.")

    if cand.pack_id is not None:
        if not isinstance(cand.pack_id, str) or not _is_utf8_string(cand.pack_id):
            raise ValueError("pack_id của candidate nếu có bắt buộc là chuỗi UTF-8.")
    if cand.source_id is not None:
        if not isinstance(cand.source_id, str) or not _is_utf8_string(cand.source_id):
            raise ValueError("source_id của candidate nếu có bắt buộc là chuỗi UTF-8.")
    if cand.origin_library_id is not None:
        if not isinstance(cand.origin_library_id, str) or not _is_utf8_string(cand.origin_library_id):
            raise ValueError("origin_library_id của candidate nếu có bắt buộc là chuỗi UTF-8.")
    if cand.template_type is not None:
        if not isinstance(cand.template_type, str) or not _is_utf8_string(cand.template_type) or cand.template_type not in ("text", "trans"):
            raise ValueError("template_type của candidate nếu có chỉ được nhận 'text' hoặc 'trans'.")

    if type(cand.is_native_guidance) is not bool:
        raise ValueError("is_native_guidance của candidate bắt buộc là bool.")
    if type(cand.requires_reconnect) is not bool:
        raise ValueError("requires_reconnect của candidate bắt buộc là bool.")

    if cand.duration_sec is not None:
        validate_safe_numeric(cand.duration_sec, "duration_sec", allow_zero=False)


def validate_demand_match_result(res: Any, expected_request: OfflineAssetMatchRequest) -> None:
    """Thẩm định tính toàn vẹn và đối soát của một OfflineAssetMatchResult."""
    if not isinstance(res, OfflineAssetMatchResult):
        raise ValueError(f"Mỗi kết quả bắt buộc là OfflineAssetMatchResult, nhận được: {type(res).__name__}.")

    if not isinstance(res.request_id, str) or not _is_utf8_string(res.request_id) or res.request_id != expected_request.request_id:
        raise ValueError(f"request_id trong result ('{res.request_id}') không khớp request_id trong request ('{expected_request.request_id}').")
    if not isinstance(res.category, str) or not _is_utf8_string(res.category) or res.category != expected_request.category:
        raise ValueError(f"Category trong result ('{res.category}') không khớp category trong request ('{expected_request.category}').")
    if not isinstance(res.query_text, str) or not _is_utf8_string(res.query_text) or res.query_text != expected_request.query_text:
        raise ValueError(f"query_text trong result ('{res.query_text}') không khớp query_text trong request ('{expected_request.query_text}').")

    if type(res.is_matched) is not bool:
        raise ValueError("is_matched trong result bắt buộc phải là kiểu bool thuần (không nhận int/số).")

    if not isinstance(res.candidates, tuple):
        raise ValueError("candidates trong result bắt buộc phải là tuple.")

    if res.is_matched != (len(res.candidates) > 0):
        raise ValueError(f"is_matched ({res.is_matched}) không nhất quán với số lượng candidates ({len(res.candidates)}).")

    if res.is_matched and res.unmatched_reason is not None:
        raise ValueError("unmatched_reason bắt buộc phải là None khi is_matched=True.")
    if not res.is_matched:
        if not isinstance(res.unmatched_reason, str) or not _is_utf8_string(res.unmatched_reason) or not res.unmatched_reason.strip():
            raise ValueError("unmatched_reason bắt buộc phải là chuỗi UTF-8 không rỗng khi is_matched=False.")

    if not isinstance(res.warnings, tuple) or not all(isinstance(w, str) and _is_utf8_string(w) for w in res.warnings):
        raise ValueError("warnings trong result bắt buộc phải là tuple của các chuỗi UTF-8.")

    if type(res.rejected_record_count) is not int or res.rejected_record_count < 0:
        raise ValueError("rejected_record_count trong result bắt buộc phải là số nguyên >= 0.")

    for cand in res.candidates:
        validate_demand_candidate(cand, res.category)


def validate_demand_batch(batch: Any) -> None:
    """Thẩm định cấu trúc, bijection và tính toàn vẹn của một AssetDemandBatch."""
    if not isinstance(batch, AssetDemandBatch):
        raise ValueError(f"batch bắt buộc phải là AssetDemandBatch, nhận được: {type(batch).__name__}.")

    if not _is_utf8_string(batch.plan_revision) or not batch.plan_revision.strip():
        raise ValueError("plan_revision bắt buộc là chuỗi UTF-8 không rỗng.")
    if batch.manifest_id is not None:
        if not _is_utf8_string(batch.manifest_id) or not batch.manifest_id.strip():
            raise ValueError("manifest_id nếu có bắt buộc là chuỗi UTF-8 không rỗng.")

    if not isinstance(batch.demands, tuple):
        raise ValueError("demands bắt buộc là tuple của AssetDemandItem.")
    if len(batch.demands) > MAX_ENTRIES_PRECHECK:
        raise ValueError(f"Số lượng demands ({len(batch.demands)}) vượt quá giới hạn cấu trúc ({MAX_ENTRIES_PRECHECK}).")

    if not isinstance(batch.requests, tuple):
        raise ValueError("requests bắt buộc là tuple của OfflineAssetMatchRequest.")
    if len(batch.requests) > MAX_BATCH_SIZE:
        raise ValueError(f"Số lượng requests ({len(batch.requests)}) vượt quá giới hạn batch ({MAX_BATCH_SIZE}).")

    seen_ids: Set[str] = set()
    expected_eligible_demands: list[AssetDemandItem] = []
    last_slot: str = "broll_inserts"
    last_index: int = -1

    for d in batch.demands:
        validate_asset_demand_item(d)

        expected_id = json.dumps([batch.plan_revision, d.source_slot, d.source_index], ensure_ascii=True, separators=(",", ":"))
        if d.demand_id != expected_id:
            raise ValueError(f"demand_id '{d.demand_id}' không khớp mã hóa chuẩn tắc mong đợi '{expected_id}' cho revision '{batch.plan_revision}'.")

        if d.demand_id in seen_ids:
            raise ValueError(f"Trùng lặp demand_id trong batch: {d.demand_id}")
        seen_ids.add(d.demand_id)

        if d.source_slot == "broll_inserts":
            if last_slot == "sfx_inserts":
                raise ValueError("Thứ tự demands vi phạm chuẩn tắc: broll_inserts không được xuất hiện sau sfx_inserts.")
            if d.source_index != last_index + 1:
                raise ValueError(f"source_index trong broll_inserts vi phạm tính liên tục: {d.source_index} != {last_index + 1}.")
            last_index = d.source_index
        elif d.source_slot == "sfx_inserts":
            if last_slot == "broll_inserts":
                last_slot = "sfx_inserts"
                last_index = -1
            if d.source_index != last_index + 1:
                raise ValueError(f"source_index trong sfx_inserts vi phạm tính liên tục: {d.source_index} != {last_index + 1}.")
            last_index = d.source_index

        if d.status == "eligible":
            expected_eligible_demands.append(d)

    seen_req_ids: Set[str] = set()
    for req in batch.requests:
        validate_match_request(req)
        if req.request_id in seen_req_ids:
            raise ValueError(f"Trùng lặp request_id trong batch.requests: {req.request_id}")
        seen_req_ids.add(req.request_id)

    if len(expected_eligible_demands) != len(batch.requests):
        raise ValueError(
            f"Số lượng eligible demands ({len(expected_eligible_demands)}) không khớp số lượng requests ({len(batch.requests)})."
        )

    for ed, req in zip(expected_eligible_demands, batch.requests):
        if req.request_id != ed.demand_id:
            raise ValueError(f"request_id '{req.request_id}' không khớp demand_id '{ed.demand_id}'.")
        if req.category != ed.category:
            raise ValueError(f"category trong request ('{req.category}') không khớp demand ('{ed.category}').")
        if req.query_text != ed.query_text:
            raise ValueError(f"query_text trong request ('{req.query_text}') không khớp demand ('{ed.query_text}').")


@dataclass(frozen=True)
class AssetDemandItem:
    """Nhu cầu tài nguyên độc lập bóc tách từ một mục kịch bản (Slot 1)."""
    demand_id: str
    source_slot: str
    source_index: int
    status: str
    raw_type: Optional[str] = None
    category: Optional[str] = None
    timeline_sec: Optional[float] = None
    desired_duration_sec: Optional[float] = None
    raw_query_hint: str = ""
    query_text: str = ""
    query_source: Optional[str] = None
    rejection_reason: Optional[str] = None

    def __post_init__(self):
        validate_asset_demand_item(self)


@dataclass(frozen=True)
class AssetDemandBatch:
    """Lô nhu cầu kịch bản kèm danh sách requests đã sẵn sàng để gửi sang Service."""
    plan_revision: str
    manifest_id: Optional[str]
    demands: Tuple[AssetDemandItem, ...]
    requests: Tuple[OfflineAssetMatchRequest, ...]

    def __post_init__(self):
        validate_demand_batch(self)


@dataclass(frozen=True)
class DemandMatchEntry:
    """Mục kết quả đối soát giữa một nhu cầu tài nguyên và kết quả Matcher tư vấn."""
    demand: AssetDemandItem
    result: Optional[OfflineAssetMatchResult] = None

    def __post_init__(self):
        if not isinstance(self.demand, AssetDemandItem):
            raise ValueError("demand bắt buộc là thể hiện của AssetDemandItem.")
        validate_asset_demand_item(self.demand)

        if self.demand.status != "eligible":
            if self.result is not None:
                raise ValueError("Nhu cầu không eligible (status != 'eligible') bắt buộc phải có result=None.")
        else:
            if self.result is not None:
                if not isinstance(self.result, OfflineAssetMatchResult):
                    raise ValueError("result bắt buộc là thể hiện của OfflineAssetMatchResult.")
                if self.result.request_id != self.demand.demand_id:
                    raise ValueError(f"request_id trong result ('{self.result.request_id}') không khớp demand_id ('{self.demand.demand_id}').")
                if self.result.category != self.demand.category:
                    raise ValueError(f"category trong result ('{self.result.category}') không khớp demand ('{self.demand.category}').")
                if self.result.query_text != self.demand.query_text:
                    raise ValueError(f"query_text trong result ('{self.result.query_text}') không khớp demand ('{self.demand.query_text}').")

    @property
    def is_matched(self) -> bool:
        """True nếu có kết quả và kết quả xác nhận đã khớp (có >= 1 candidate)."""
        return bool(self.result and self.result.is_matched)

    @property
    def candidates(self) -> Tuple[OfflineAssetCandidate, ...]:
        """Danh sách ứng viên khả thi từ kết quả Matcher (rỗng nếu không khớp hoặc không eligible)."""
        return self.result.candidates if self.result else ()

    @property
    def unmatched_reason(self) -> Optional[str]:
        """Lý do không khớp từ Matcher hoặc lý do từ chối từ bước trích xuất nhu cầu."""
        if self.result:
            return self.result.unmatched_reason
        return self.demand.rejection_reason


@dataclass(frozen=True)
class AssetMatchingSidecar:
    """Báo cáo tư vấn toàn diện đồng hành cùng kịch bản (không làm biến đổi kịch bản)."""
    plan_revision: str
    manifest_id: Optional[str]
    entries: Tuple[DemandMatchEntry, ...]

    def __post_init__(self):
        if not _is_utf8_string(self.plan_revision) or not self.plan_revision.strip():
            raise ValueError("plan_revision bắt buộc là chuỗi UTF-8 không rỗng.")
        if self.manifest_id is not None:
            if not _is_utf8_string(self.manifest_id) or not self.manifest_id.strip():
                raise ValueError("manifest_id nếu có bắt buộc là chuỗi UTF-8 không rỗng.")
        if not isinstance(self.entries, tuple):
            raise ValueError("entries bắt buộc là tuple của DemandMatchEntry.")

        seen_demands: Set[str] = set()
        for e in self.entries:
            if not isinstance(e, DemandMatchEntry):
                raise ValueError("Mỗi phần tử trong entries bắt buộc phải là DemandMatchEntry.")
            did = e.demand.demand_id
            if did in seen_demands:
                raise ValueError(f"Trùng lặp demand_id trong sidecar entries: {did}")
            seen_demands.add(did)

        # Ràng buộc đẳng thức phân hoạch tách biệt tuyệt đối
        partition_sum = (
            self.matched_count + self.unmatched_count + self.invalid_count +
            self.empty_query_count + self.unsupported_count + self.overflow_count
        )
        if partition_sum != self.total_demands:
            raise ValueError(
                f"Tổng các bộ đếm phân hoạch ({partition_sum}) không bằng total_demands ({self.total_demands})."
            )

    @property
    def total_demands(self) -> int:
        """Tổng số nhu cầu bóc tách từ kịch bản."""
        return len(self.entries)

    @property
    def matched_count(self) -> int:
        """Số nhu cầu đã khớp thành công có ứng viên khả thi."""
        return sum(1 for e in self.entries if e.is_matched)

    @property
    def unmatched_count(self) -> int:
        """Số nhu cầu hợp lệ gửi đi tra cứu nhưng matcher không tìm thấy ứng viên."""
        return sum(1 for e in self.entries if e.demand.status == "eligible" and not e.is_matched)

    @property
    def invalid_count(self) -> int:
        """Số nhu cầu bị từ chối do dữ liệu đầu vào hỏng / sai định dạng."""
        return sum(1 for e in self.entries if e.demand.status == "invalid")

    @property
    def empty_query_count(self) -> int:
        """Số nhu cầu bị từ chối do không có từ khóa tìm kiếm (description và filename đều rỗng)."""
        return sum(1 for e in self.entries if e.demand.status == "empty_query")

    @property
    def unsupported_count(self) -> int:
        """Số nhu cầu bị từ chối do danh mục không được hỗ trợ (vd: green_screen hoặc kiểu lạ)."""
        return sum(1 for e in self.entries if e.demand.status == "unsupported")

    @property
    def overflow_count(self) -> int:
        """Số nhu cầu vượt quá giới hạn tối đa 256 của batch."""
        return sum(1 for e in self.entries if e.demand.status == "overflow")
