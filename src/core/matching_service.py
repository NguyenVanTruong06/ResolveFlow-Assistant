"""
src/core/matching_service.py
=============================================================================
Dịch Vụ Điều Phối Khớp Tài Nguyên Ngoại Tuyến Theo Lô (Per-Batch Matching Service).

RÀNG BUỘC KỸ THUẬT:
- Thuần Python 3.12 Standard Library (typing, collections.abc, dataclasses).
- Tích hợp dependency injection tường minh qua callable `catalog_provider`.
- TUYỆT ĐỐI KHÔNG import PySide6 / UI / StorageManager / ResolveGateway / FS I/O.
- Không giữ cache liên phiên, không global singleton, không thread locks.
- Per-batch snapshot: mỗi batch gọi provider đúng 1 lần, batch kế tiếp gọi fresh.
=============================================================================
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.contracts.asset_matching_contracts import (
    OfflineAssetMatchRequest,
    OfflineAssetMatchResult,
    validate_match_request,
)
from src.core.offline_asset_matcher import (
    OfflineAssetMatcher,
    _is_utf8_valid,
    _strip_accents,
)


MAX_BATCH_SIZE: int = 256


class OfflineAssetMatchingService:
    """
    Cầu nối điều phối tra cứu tài nguyên ngoại tuyến tương thích Worker.
    Nhận catalog snapshot thông qua dependency injection callable `catalog_provider`.
    """

    def __init__(
        self,
        *,
        catalog_provider: Callable[[], Sequence[Dict[str, Any]]],
        alias_groups: Optional[Mapping[str, Sequence[str]]] = None,
    ):
        if not callable(catalog_provider):
            raise ValueError("catalog_provider bắt buộc phải là một callable (hàm không đối số).")
        self._catalog_provider = catalog_provider

        # Thẩm định và sao chép bất biến từ điển alias ngoài (chống mutation từ caller)
        self._alias_groups: Optional[Dict[str, Tuple[str, ...]]] = None
        if alias_groups is not None:
            if not isinstance(alias_groups, Mapping):
                raise ValueError("alias_groups phải là một Mapping (dict).")
            if len(alias_groups) > 64:
                raise ValueError("alias_groups không được vượt quá 64 nhóm.")

            self._alias_groups = {}
            for g_key, terms in alias_groups.items():
                if not isinstance(g_key, str) or not g_key.strip() or not _is_utf8_valid(g_key):
                    raise ValueError("Key của alias_groups phải là chuỗi UTF-8 không rỗng.")
                norm_key = _strip_accents(g_key)
                if norm_key in self._alias_groups:
                    raise ValueError(f"Xung đột alias group key sau chuẩn hóa: '{g_key}'")

                if not isinstance(terms, (list, tuple)):
                    raise ValueError("Terms trong alias_groups phải là danh sách hoặc tuple.")
                if len(terms) > 32:
                    raise ValueError(f"Nhóm '{g_key}' trong alias_groups không được vượt quá 32 từ.")

                norm_terms = []
                for t in terms:
                    if not isinstance(t, str) or not t.strip() or not _is_utf8_valid(t):
                        raise ValueError("Từ trong alias_groups phải là chuỗi UTF-8 không rỗng.")
                    norm_terms.append(t)
                self._alias_groups[norm_key] = tuple(norm_terms)

    def match_batch(
        self,
        requests: Sequence[OfflineAssetMatchRequest],
    ) -> Tuple[OfflineAssetMatchResult, ...]:
        """
        Tra cứu hàng loạt cho một danh sách yêu cầu trong cùng một lô kịch bản (Batch).
        
        Quy tắc:
        1. Thẩm định cấu trúc batch và re-validate từng request TRƯỚC KHI gọi provider.
        2. Nếu requests rỗng -> trả về tuple rỗng () và KHÔNG gọi provider (0 call).
        3. Nếu requests không rỗng và hợp lệ -> gọi provider đúng 1 lần, khởi tạo 1 Matcher duy nhất
           dùng chung cho toàn bộ requests trong batch.
        4. Batch tiếp theo sẽ gọi provider mới, không lưu giữ matcher hay catalog cũ.
        """
        if not isinstance(requests, (list, tuple)):
            raise ValueError("requests bắt buộc phải là một Sequence (list hoặc tuple).")
        if len(requests) > MAX_BATCH_SIZE:
            raise ValueError(f"Số lượng requests trong một batch vượt quá giới hạn tối đa ({MAX_BATCH_SIZE}).")

        # Batch rỗng -> trả về () ngay lập tức, 0 provider call
        if len(requests) == 0:
            return ()

        # 1. Thẩm định toàn diện từng request và kiểm tra trùng lặp request_id TRƯỚC KHI gọi provider
        seen_ids: Set[str] = set()
        for idx, req in enumerate(requests):
            # Cổng thẩm định danh nghĩa và ngữ nghĩa trường: bắt lỗi SimpleNamespace, lone surrogate, bigint, v.v.
            validate_match_request(req)
            if req.request_id in seen_ids:
                raise ValueError(f"Trùng lặp request_id '{req.request_id}' trong cùng một batch (phần tử thứ {idx}).")
            seen_ids.add(req.request_id)

        # 2. Gọi provider đúng 1 lần cho batch hợp lệ
        # Nếu provider ném lỗi (OSError, RuntimeError, v.v.), cho phép ném ra ngoài nguyên bản (propagate)
        catalog = self._catalog_provider()

        # 3. Khởi tạo thể hiện Matcher gắn với snapshot catalog vừa nhận
        # Nếu catalog sai cấu trúc (không phải list/tuple hoặc >100k), Matcher ném ValueError
        matcher = OfflineAssetMatcher(catalog, alias_groups=self._alias_groups)

        # 4. Thực thi so khớp tuần tự và trả về tuple kết quả cùng thứ tự nguyên bản
        results: List[OfflineAssetMatchResult] = []
        for req in requests:
            res = matcher.match(req)
            results.append(res)

        return tuple(results)

    def match_request(
        self,
        request: OfflineAssetMatchRequest,
    ) -> OfflineAssetMatchResult:
        """
        Tra cứu một yêu cầu đơn lẻ.
        Sử dụng chung quy trình với `match_batch([request])`, gọi provider đúng 1 lần.
        """
        results = self.match_batch([request])
        return results[0]
