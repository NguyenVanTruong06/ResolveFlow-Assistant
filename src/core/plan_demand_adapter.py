"""
src/core/plan_demand_adapter.py
=============================================================================
Adapter bóc tách nhu cầu tài nguyên từ kịch bản Slot 1 (Video Plan) và
lắp ráp báo cáo tư vấn ngoại tuyến (AssetMatchingSidecar):
- Pure stdlib + DTO contracts; không phụ thuộc UI / StorageManager / Resolve / IO.
- Không tự gọi Service hay Provider; phương thức pure static / class methods.
- Chặn các known wrapper envelopes (dual_plan, kinetic_plan, generic_plan, video_plan với data, etc.).
- Bắt an toàn OverflowError cho số quá lớn trước khi gọi float/math.isfinite.
- Không áp đặt arbitrary cap 10**18; số hữu hạn dương (e.g. 1e19) được chấp nhận.
- Trích xuất canonical order: broll_inserts rồi sfx_inserts, bảo toàn chỉ số.
- Mã hóa demand_id chuẩn JSON compact array [plan_revision, slot, index].
- Quy tắc query: description -> safe asset_file -> suggested_file (chỉ khi asset_file khuyết/trắng).
- Thứ tự ưu tiên phân loại: invalid -> unsupported_category -> empty_query -> eligible/overflow.
- Bảo vệ cấu trúc: kiểm tra độ dài mảng <= 4096; giới hạn batch tối đa 256 requests.
- Lắp ráp sidecar: kiểm tra đối soát 1-1 chặt chẽ, revalidate toàn diện graph/results/candidates qua shared validators,
  hỗ trợ out-of-order results, bảo đảm tổng các bộ đếm phân hoạch bằng đúng total_demands.
=============================================================================
"""

import json
import math
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src.core.contracts.asset_dto import is_valid_custom_category_id
from src.core.contracts.asset_matching_contracts import (
    OfflineAssetCandidate,
    OfflineAssetMatchRequest,
    OfflineAssetMatchResult,
    validate_match_request,
)
from src.core.contracts.plan_demand_contracts import (
    AssetDemandBatch,
    AssetDemandItem,
    DemandMatchEntry,
    AssetMatchingSidecar,
    VALID_SLOTS,
    VALID_STATUSES,
    validate_asset_demand_item,
    validate_demand_batch,
    validate_demand_match_result,
    validate_safe_numeric,
    _is_utf8_string,
)
from src.core.offline_asset_matcher import (
    KNOWN_EXTENSIONS,
    UI_CATEGORY_ALIASES,
)

CANONICAL_CATEGORIES: Tuple[str, ...] = (
    "broll", "sfx", "bgm", "overlays", "memes", "luts", "templates"
)

MAX_ENTRIES_PRECHECK: int = 4096
MAX_BATCH_SIZE: int = 256

KNOWN_WRAPPER_TYPES: Tuple[str, ...] = (
    "dual_plan", "kinetic_plan", "generic_plan", "project_overview",
    "project_overview_draft", "batch_draft"
)


def _safe_numeric(val: Any, allow_zero: bool = False) -> Tuple[bool, Optional[float]]:
    """
    Chuyển đổi an toàn giá trị số, bắt tràn số (OverflowError) trước khi gọi math.isfinite.
    Trả về (True, float_val) nếu hợp lệ; (False, None) nếu sai kiểu/overflow/non-finite/âm.
    """
    if val is None or type(val) is bool:
        return False, None
    if type(val) in (int, float):
        try:
            f_val = float(val)
        except (OverflowError, ValueError):
            return False, None

        if not math.isfinite(f_val):
            return False, None

        if allow_zero:
            if f_val < 0.0:
                return False, None
        else:
            if f_val <= 0.0:
                return False, None

        return True, f_val
    return False, None


def _is_safe_filename(hint: str) -> bool:
    """Kiểm tra chuỗi tệp có phải tên đơn an toàn không (không chứa đường dẫn, ổ đĩa, URI, traversal)."""
    if not hint or "\0" in hint:
        return False
    if "/" in hint or "\\" in hint or ":" in hint:
        return False
    if hint in (".", ".."):
        return False
    return True


def _extract_stem(filename: str) -> str:
    """Bỏ phần mở rộng media/template đã biết, đổi dấu gạch dưới thành khoảng trắng và trim."""
    stem = filename
    lower_s = stem.lower()
    for ext in KNOWN_EXTENSIONS:
        if lower_s.endswith(ext):
            stem = stem[:-len(ext)]
            break
    return stem.replace("_", " ").strip()


class PlanDemandAdapter:
    """Adapter chuyển đổi kịch bản thô sang lô tra cứu và lắp ráp kết quả tư vấn."""

    @classmethod
    def extract(
        cls,
        plan: Mapping[str, Any],
        *,
        plan_revision: str,
        top_k: int = 5,
        allow_offline: bool = False
    ) -> AssetDemandBatch:
        """
        Bóc tách kịch bản Video Plan (Slot 1) thành AssetDemandBatch.
        
        Quy tắc:
        - Thẩm định options (plan_revision, top_k, allow_offline) ngay cả khi plan rỗng.
        - Chặn các known wrapper envelopes (dual_plan, kinetic_plan, generic_plan, video_plan với data, project_overview_draft, batch_draft).
        - Giữ nguyên raw manifest_id nếu có; kiểm tra UTF-8 và non-empty.
        - Kiểm tra cấu trúc broll_inserts và sfx_inserts (list/tuple, tổng <= 4096).
        - Duyệt canonical: broll_inserts rồi sfx_inserts.
        - Mỗi phần tử không hợp lệ trở thành invalid demand, không làm mất anh em.
        - Giới hạn cứng 256 requests cho các demand eligible đầu tiên; phần dư đánh dấu overflow.
        """
        # 1. Thẩm định options đầu vào
        if not _is_utf8_string(plan_revision) or not plan_revision.strip():
            raise ValueError("plan_revision bắt buộc là chuỗi UTF-8 không rỗng và không chứa lone surrogates.")

        if type(top_k) is not int or top_k < 1 or top_k > 20:
            raise ValueError("top_k bắt buộc là số nguyên trong khoảng [1, 20] (không nhận bool).")

        if type(allow_offline) is not bool:
            raise ValueError("allow_offline bắt buộc là giá trị boolean (không nhận kiểu khác).")

        if not isinstance(plan, Mapping):
            raise ValueError("plan bắt buộc phải là Mapping (dict).")

        # 2. Chặn các known wrapper envelopes (typegate marker trước membership)
        if "type" in plan:
            val_type = plan["type"]
            if isinstance(val_type, str):
                ptype = val_type.strip().lower()
                if ptype in KNOWN_WRAPPER_TYPES:
                    raise ValueError(f"Không nhận wrapper envelope '{val_type}'. Chỉ nhận raw Video Plan Mapping.")
                if ptype == "video_plan" and "data" in plan:
                    raise ValueError("Không nhận wrapper envelope type='video_plan' có trường 'data'. Chỉ nhận raw Video Plan Mapping.")

        if "kind" in plan:
            val_kind = plan["kind"]
            if isinstance(val_kind, str) and val_kind.strip().lower() == "project_overview_draft":
                raise ValueError("Không nhận envelope kind='project_overview_draft'. Chỉ nhận raw Video Plan Mapping.")

        if "$schema" in plan:
            val_schema = plan["$schema"]
            if isinstance(val_schema, str) and ("batch_draft" in val_schema.lower() or val_schema.strip() == "chundvc_batch_draft_v1"):
                raise ValueError(f"Không nhận envelope $schema='{val_schema}'. Chỉ nhận raw Video Plan Mapping.")

        if "schema" in plan:
            val_schema = plan["schema"]
            if isinstance(val_schema, str) and ("batch_draft" in val_schema.lower() or val_schema.strip() == "chundvc_batch_draft_v1"):
                raise ValueError(f"Không nhận envelope schema='{val_schema}'. Chỉ nhận raw Video Plan Mapping.")

        if "video_plan" in plan and "kinetic_plan" in plan:
            raise ValueError("Không nhận dual plan envelope. Chỉ nhận raw Video Plan Mapping.")

        if "batch_draft" in plan or "batch_id" in plan or "batches" in plan:
            raise ValueError("Không nhận batch draft envelope. Chỉ nhận raw Video Plan Mapping.")

        # 3. Thẩm định manifest_id nếu có
        manifest_id: Optional[str] = None
        if "manifest_id" in plan:
            mid_val = plan["manifest_id"]
            if mid_val is not None:
                if not _is_utf8_string(mid_val) or not mid_val.strip():
                    raise ValueError("manifest_id nếu có bắt buộc là chuỗi UTF-8 không rỗng.")
                manifest_id = mid_val

        # 4. Thẩm định cấu trúc mảng chèn
        raw_brolls: Sequence[Any] = ()
        if "broll_inserts" in plan:
            b_val = plan["broll_inserts"]
            if not isinstance(b_val, (list, tuple)):
                raise ValueError("broll_inserts bắt buộc phải là list hoặc tuple.")
            raw_brolls = b_val

        raw_sfx: Sequence[Any] = ()
        if "sfx_inserts" in plan:
            s_val = plan["sfx_inserts"]
            if not isinstance(s_val, (list, tuple)):
                raise ValueError("sfx_inserts bắt buộc phải là list hoặc tuple.")
            raw_sfx = s_val

        total_entries = len(raw_brolls) + len(raw_sfx)
        if total_entries > MAX_ENTRIES_PRECHECK:
            raise ValueError(f"Tổng số mục chèn ({total_entries}) vượt quá giới hạn cấu trúc cho phép ({MAX_ENTRIES_PRECHECK}).")

        demands: List[AssetDemandItem] = []
        requests: List[OfflineAssetMatchRequest] = []
        eligible_count: int = 0

        # 5. Duyệt canonical: broll_inserts rồi sfx_inserts
        slots_to_process = (
            ("broll_inserts", raw_brolls),
            ("sfx_inserts", raw_sfx),
        )

        for slot_name, slot_entries in slots_to_process:
            for idx, entry in enumerate(slot_entries):
                # Mã hóa demand_id chuẩn JSON compact array
                demand_id = json.dumps([plan_revision, slot_name, idx], ensure_ascii=True, separators=(",", ":"))

                # Phân loại non-mapping entry
                if not isinstance(entry, Mapping):
                    demands.append(AssetDemandItem(
                        demand_id=demand_id,
                        source_slot=slot_name,
                        source_index=idx,
                        status="invalid",
                        rejection_reason="ERR_DEMAND_INVALID"
                    ))
                    continue

                # Thẩm định các trường consumed có mặt
                is_malformed: bool = False

                for str_field in ("asset_type", "description", "asset_file", "suggested_file"):
                    if str_field in entry:
                        val = entry[str_field]
                        if not isinstance(val, str) or not _is_utf8_string(val):
                            is_malformed = True
                            break

                timeline_sec: Optional[float] = None
                if not is_malformed and "timeline_sec" in entry:
                    t_val = entry["timeline_sec"]
                    t_ok, t_num = _safe_numeric(t_val, allow_zero=True)
                    if t_ok:
                        timeline_sec = t_num
                    else:
                        is_malformed = True

                desired_duration_sec: Optional[float] = None
                if not is_malformed and "duration_sec" in entry:
                    d_val = entry["duration_sec"]
                    d_ok, d_num = _safe_numeric(d_val, allow_zero=False)
                    if d_ok:
                        desired_duration_sec = d_num
                    else:
                        is_malformed = True

                if is_malformed:
                    demands.append(AssetDemandItem(
                        demand_id=demand_id,
                        source_slot=slot_name,
                        source_index=idx,
                        status="invalid",
                        rejection_reason="ERR_DEMAND_INVALID"
                    ))
                    continue

                # Precedence: invalid -> unsupported_category -> empty_query -> eligible/overflow
                raw_type: Optional[str] = entry.get("asset_type")
                target_category: Optional[str] = None
                template_type: Optional[str] = None
                rejection_reason: Optional[str] = None
                status: Optional[str] = None

                # 5.1. Phân loại Category
                if raw_type is None or not raw_type.strip():
                    status = "unsupported"
                    rejection_reason = "ERR_UNSUPPORTED_CATEGORY"
                else:
                    rt_clean = raw_type.strip()
                    rt_lower = rt_clean.lower()
                    if rt_lower == "green_screen":
                        status = "unsupported"
                        rejection_reason = "ERR_UNSUPPORTED_CATEGORY"
                    elif rt_lower in UI_CATEGORY_ALIASES:
                        target_category = UI_CATEGORY_ALIASES[rt_lower]
                        if rt_lower == "text":
                            template_type = "text"
                        elif rt_lower == "trans":
                            template_type = "trans"
                    elif rt_lower in CANONICAL_CATEGORIES:
                        target_category = rt_lower
                    elif is_valid_custom_category_id(rt_clean):
                        target_category = rt_clean
                    else:
                        status = "unsupported"
                        rejection_reason = "ERR_UNSUPPORTED_CATEGORY"

                # 5.2. Phân loại Query (Description -> asset_file -> suggested_file)
                query_text: str = ""
                raw_query_hint: str = ""
                query_source: Optional[str] = None

                desc_val = entry.get("description")
                if desc_val is not None and desc_val.strip():
                    query_text = desc_val.strip()
                    raw_query_hint = desc_val
                    query_source = "description"
                else:
                    file_val = entry.get("asset_file")
                    if file_val is not None and file_val.strip():
                        # asset_file có nội dung non-blank
                        raw_hint = file_val.strip()
                        raw_query_hint = file_val
                        if not _is_safe_filename(raw_hint):
                            status = "invalid"
                            rejection_reason = "ERR_DEMAND_INVALID"
                        else:
                            stem = _extract_stem(raw_hint)
                            if stem:
                                query_text = stem
                                query_source = "asset_file"
                            else:
                                # asset_file non-blank nhưng stem rỗng -> KHÔNG fallback suggested_file
                                if status is None:
                                    status = "empty_query"
                                    rejection_reason = "ERR_DEMAND_QUERY_EMPTY"
                    else:
                        # asset_file khuyết hoặc blank -> mới được fallback sang suggested_file
                        sugg_val = entry.get("suggested_file")
                        if sugg_val is not None and sugg_val.strip():
                            raw_hint = sugg_val.strip()
                            raw_query_hint = sugg_val
                            if not _is_safe_filename(raw_hint):
                                status = "invalid"
                                rejection_reason = "ERR_DEMAND_INVALID"
                            else:
                                stem = _extract_stem(raw_hint)
                                if stem:
                                    query_text = stem
                                    query_source = "suggested_file"
                                else:
                                    if status is None:
                                        status = "empty_query"
                                        rejection_reason = "ERR_DEMAND_QUERY_EMPTY"
                        else:
                            if status is None:
                                status = "empty_query"
                                rejection_reason = "ERR_DEMAND_QUERY_EMPTY"

                # 5.3. Quota & Request Construction
                if status is None:
                    # Eligible candidate for matching
                    if eligible_count < MAX_BATCH_SIZE:
                        status = "eligible"
                        req = OfflineAssetMatchRequest(
                            request_id=demand_id,
                            category=target_category,  # type: ignore
                            query_text=query_text,
                            scene_context="",
                            emotion_or_mood="",
                            template_type=template_type,
                            subcategory_constraint=None,
                            min_duration_sec=None,
                            max_duration_sec=None,
                            top_k=top_k,
                            allow_offline=allow_offline
                        )
                        requests.append(req)
                        eligible_count += 1
                    else:
                        status = "overflow"
                        rejection_reason = "ERR_BATCH_CAP_OVERFLOW"

                demands.append(AssetDemandItem(
                    demand_id=demand_id,
                    source_slot=slot_name,
                    source_index=idx,
                    status=status,
                    raw_type=raw_type,
                    category=target_category,
                    timeline_sec=timeline_sec,
                    desired_duration_sec=desired_duration_sec,
                    raw_query_hint=raw_query_hint,
                    query_text=query_text,
                    query_source=query_source,
                    rejection_reason=rejection_reason
                ))

        return AssetDemandBatch(
            plan_revision=plan_revision,
            manifest_id=manifest_id,
            demands=tuple(demands),
            requests=tuple(requests)
        )

    @classmethod
    def assemble(
        cls,
        batch: AssetDemandBatch,
        results: Sequence[OfflineAssetMatchResult]
    ) -> AssetMatchingSidecar:
        """
        Lắp ráp AssetDemandBatch và danh sách OfflineAssetMatchResult thành AssetMatchingSidecar.
        
        Quy tắc:
        - Revalidate toàn diện batch qua validate_demand_batch: demands, requests, bijection, IDs matching plan_revision.
        - Thẩm định nghiêm ngặt kết quả qua validate_demand_match_result:
          * Kiểu dữ liệu, strict bool is_matched, candidate attributes (chỉ online/offline, duration None hoặc >0).
        - Kiểm tra khớp 1-to-1 giữa eligible requests và results.
        - Chặn kết quả thiếu, thừa, trùng lặp, hoặc không thuộc batch.
        - Chặn lệch category hoặc query_text giữa request và result.
        - Hỗ trợ kết quả trả về không theo thứ tự; ghép lại đúng canonical order của demands.
        - Noneligible demands có result=None.
        """
        # 1. Thẩm định cấu trúc batch và revalidate bằng shared validator
        validate_demand_batch(batch)

        # 2. Thẩm định danh sách results
        if not isinstance(results, (list, tuple)):
            raise ValueError("results bắt buộc phải là list hoặc tuple.")

        expected_count = len(batch.requests)
        if len(results) != expected_count:
            raise ValueError(f"Số lượng results ({len(results)}) không khớp số lượng requests mong đợi ({expected_count}).")

        # Ánh xạ requests theo request_id (đã được validate_demand_batch kiểm định tính độc nhất)
        requests_by_id: Dict[str, OfflineAssetMatchRequest] = {req.request_id: req for req in batch.requests}
        results_by_id: Dict[str, OfflineAssetMatchResult] = {}

        for res in results:
            if not isinstance(res, OfflineAssetMatchResult):
                raise ValueError(f"Mỗi phần tử trong results bắt buộc phải là OfflineAssetMatchResult, nhận được: {type(res).__name__}.")

            # Typegate request_id trước khi tra cứu trong dict để tránh TypeError unhashable
            if not isinstance(res.request_id, str) or not _is_utf8_string(res.request_id):
                raise ValueError("request_id trong result bắt buộc phải là chuỗi UTF-8.")

            rid = res.request_id
            if rid not in requests_by_id:
                raise ValueError(f"Kết quả chứa request_id không xác định hoặc không thuộc batch: {rid}")

            if rid in results_by_id:
                raise ValueError(f"Trùng lặp request_id trong results: {rid}")

            req = requests_by_id[rid]

            # Thẩm định toàn vẹn kết quả và candidates qua shared validator
            validate_demand_match_result(res, req)

            results_by_id[rid] = res

        # Ghép entries theo đúng thứ tự chuẩn tắc ban đầu của batch.demands
        entries: List[DemandMatchEntry] = []
        for demand in batch.demands:
            if demand.status == "eligible":
                res = results_by_id[demand.demand_id]
                entries.append(DemandMatchEntry(demand=demand, result=res))
            else:
                entries.append(DemandMatchEntry(demand=demand, result=None))

        return AssetMatchingSidecar(
            plan_revision=batch.plan_revision,
            manifest_id=batch.manifest_id,
            entries=tuple(entries)
        )
