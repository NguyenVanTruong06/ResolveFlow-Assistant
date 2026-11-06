# G06-06B2-D: Thiết Kế Tích Hợp Consumer Khớp Tài Nguyên Ngoại Tuyến (Offline Matching Consumer Design)

> HIỆN HÀNH 2026-10-29: G06-06B2-I ACCEPTED_WITH_RECORDED_LIMITS theo06B2-gate-acceptance.md; Codex97tests/0.38s/exit0. Next06B3-D design-only demand/Review; UI integration và adapter chưa code.

- **Mã công việc:** `G06-06B2-D` / `G06-06B2-I`
- **Cấp độ thực hiện:** Worker `HIGH`
- **Trạng thái:** `IMPLEMENTED_AND_VERIFIED` (Lát cắt `G06-06B2-I` hoàn tất)
- **Tài liệu tham chiếu:**
  * `docs/architecture/06B-offline-asset-matching-design.md` & `docs/architecture/06B-codex-decisions.md`.
  * `docs/architecture/06B1-gate-acceptance.md` (06B1 accepted trong recorded limits).
  * `docs/architecture/06B2-codex-decisions.md` (Quyết định ràng buộc của Codex cho 06B2).
  * `src/core/matching_service.py` & `tests/core/test_matching_service.py`.

---

## 1. Đối Chiếu Callgraph Thực Tế Codebase (`file:hàm:dòng`)

Khảo sát dòng chảy dữ liệu thực tế giữa Producer (nơi cung cấp danh mục metadata), Originator (nơi phát sinh nhu cầu tra cứu kịch bản), và Consumer (nơi tiếp nhận kết quả tư vấn khớp tài nguyên).

```mermaid
flowchart TD
    subgraph Producer ["1. Storage Authority (Producer)"]
        SM_assets["StorageManager.assets()<br/>[CODE_VERIFIED: src/core/storage_manager.py:371]"]
        SM_tax["StorageManager.taxonomy()<br/>[CODE_VERIFIED: src/core/storage_manager.py:357]"]
        SM_reg["StorageManager.registry()<br/>[CODE_VERIFIED: src/core/storage_manager.py:333]"]
    end

    subgraph Service ["2. Per-Batch Consumer Bridge"]
        ServiceNode["OfflineAssetMatchingService<br/>[CODE_VERIFIED: src/core/matching_service.py:38]"]
        Matcher["OfflineAssetMatcher<br/>[CODE_VERIFIED: src/core/offline_asset_matcher.py:174]"]
    end

    subgraph Originator ["3. Planning & Copilot Context (Originator - DEFERRED)"]
        BatchDraft["ValidatedSegmentCut / ValidatedBatchDraftEntry<br/>[CODE_VERIFIED: src/core/batch_draft_contracts.py:115]"]
        StoryCopilot["CopilotMediaInsert / StoryCopilot<br/>[CODE_VERIFIED: src/core/story_copilot.py:40]"]
    end

    subgraph Guard ["4. Action-Time Authority (Pre-Flight Execution - ĐÃ CÓ TỪ G04)"]
        ActionGuard["ActionGuard.check_action_availability()<br/>[CODE_VERIFIED: src/core/action_guard.py:202]"]
        ResolveGW["ResolveGateway (Timeline Mutation)<br/>[CODE_VERIFIED: G04]"]
    end

    SM_assets -->|Callable catalog_provider| ServiceNode
    ServiceNode -->|Khởi tạo Per-Batch Snapshot| Matcher
    Originator -.->|OfflineAssetMatchRequest (DEFERRED)| ServiceNode
    ServiceNode -.->|OfflineAssetMatchResult (DEFERRED)| Originator
    Originator -.->|Gán stable asset_id vào Plan| Guard
    ActionGuard -->|Kiểm tra đĩa thực tế tại action-time| ResolveGW
```

### 1.1. Storage Authority Producer (`[CODE_VERIFIED]`)
- **`src/core/storage_manager.py:371` — `StorageManager.assets(self) -> List[Dict[str, Any]]`**  
  *Thẩm quyền:* Runtime provider thật cho metadata tài nguyên trên máy. Đọc từ `meta/registry.json` (khi online) hoặc `snapshot-{lib_id}.json` (khi offline). Thực hiện kiểm tra I/O và đường dẫn tệp.  
  *Ranh giới:* `StorageManager.assets()` không tự chứng minh toàn bộ các bản ghi là schema-valid hoặc snapshot là atomic transaction. Missing file có thể được gắn cờ `offline`. Matcher 06B1 tự thẩm định schema qua `validate_asset_dict` và cách ly an toàn bad rows.
- **`src/core/storage_manager.py:357` — `StorageManager.taxonomy(self) -> Dict[str, str]`**  
  *Thẩm quyền:* Cung cấp mapping danh mục hiện hành (7 danh mục chuẩn: `broll`, `sfx`, `bgm`, `overlays`, `memes`, `luts`, `templates` cùng các custom categories).  
  *Lưu ý kiến trúc:* `taxonomy()` trả về category IDs và display labels, **KHÔNG PHẢI từ điển đồng nghĩa (synonyms) hay từ điển cảm xúc**. Các nhóm alias (`alias_groups`) là từ vựng tường minh do caller truyền vào theo quy chuẩn 06B1.
- **`src/core/storage_manager.py:333` — `StorageManager.registry(self) -> Dict[str, Any]`**  
  *Thẩm quyền:* Cung cấp cấu trúc registry JSON nguyên bản kèm `packs` và `assets`.

### 1.2. Planning Originators (`[CODE_VERIFIED]`)
- **`src/core/batch_draft_contracts.py:115` — `class ValidatedSegmentCut` & `ValidatedBatchDraftEntry`**  
  *Thẩm quyền:* Đại diện cho một đoạn cắt timeline trong lô kịch bản thô (Rough-Cut Batch), chứa `clip_id`, `start_sec`, `end_sec`, `role`, `note` và thuộc tính tính toán `duration_sec` (`end_sec - start_sec`). Không chứa các trường `source_in_sec` hay `source_out_sec`.  
  *Trạng thái tích hợp:* Hiện chưa có schema yêu cầu nhu cầu (demand schema) từ `ValidatedSegmentCut` sang matching request. Do đó, **`batch_matching_adapter.py` giữ trạng thái DEFERRED**, không tự ý bịa đặt query từ role/geometry.
- **`src/core/story_copilot.py:40` — `class CopilotMediaInsert(BaseModel)`**  
  *Thực tế hiện tại:* Sử dụng trường lịch sử `asset_file: str` (tên tệp thô do LLM đề xuất như `"pewpew_nhin_lau_lam_roi.mp4"`), `asset_type: str`, `description: str`, `timeline_sec`, `duration_sec`.  
  *Ranh giới chuyển đổi:* Chưa phải là request authority mới. Giữ nguyên schema hiện hữu, việc chuyển đổi sang `OfflineAssetMatchRequest` là `[PROPOSED]` cho các giai đoạn sau.

### 1.3. Action-Time Pre-Flight Guard (`[CODE_VERIFIED — ĐÃ HIỆN HỮU TỪ G04]`)
- **`src/core/action_guard.py:202` — `check_action_availability(asset, intent="insert")`**  
  *Thẩm quyền thực thi:* Đã có sẵn và hoạt động từ G04 (không đợi G07).  
  *Nguyên tắc bất biến:* Kết quả của `OfflineAssetMatcher` chỉ là **TƯ VẤN (Advisory)**; `ActionGuard` mới là **CỔNG THỰC THI (Action-Time)** kiểm tra `os.stat` và tính toàn vẹn của tệp thực tế trên đĩa cứng ngay trước khi chèn vào timeline DaVinci Resolve.  
  *Native Transitions:* Chỉ mang tính chất hướng dẫn (guidance), DaVinci Resolve scripting API không có phương thức tự động áp transition built-in tại điểm cắt trong phạm vi này; người dùng áp dụng qua clipboard/Effects Library guidance.

---

## 2. Điểm Vào Dịch Vụ: `OfflineAssetMatchingService` (`[CODE_VERIFIED]`)

Để không phụ thuộc I/O cứng và tương thích hoàn toàn với worker thread trong tương lai, `OfflineAssetMatchingService` được triển khai tại `src/core/matching_service.py`:

```python
class OfflineAssetMatchingService:
    """Cầu nối điều phối tra cứu tài nguyên ngoại tuyến tương thích Worker."""

    def __init__(
        self,
        *,
        catalog_provider: Callable[[], Sequence[Dict[str, Any]]],
        alias_groups: Optional[Mapping[str, Sequence[str]]] = None,
    ):
        ...

    def match_batch(
        self,
        requests: Sequence[OfflineAssetMatchRequest],
    ) -> Tuple[OfflineAssetMatchResult, ...]:
        ...

    def match_request(
        self,
        request: OfflineAssetMatchRequest,
    ) -> OfflineAssetMatchResult:
        ...
```

### 2.1. Quy Tắc Hoạt Động & Ranh Giới Kỹ Thuật
1. **Dependency Injection Tường Minh:**  
   Service nhận `catalog_provider` là một callable không đối số trả về sequence các dictionary asset. Không import trực tiếp `StorageManager` trong module, không gọi provider trong constructor (0 provider calls at init).
2. **Snapshot Per-Batch (Quyết Định 1):**  
   Mỗi lời gọi `match_batch` không rỗng sẽ gọi `catalog_provider()` **đúng một lần duy nhất**, tạo một thể hiện `OfflineAssetMatcher` sở hữu snapshot metadata đó và dùng chung cho toàn bộ requests trong batch. Batch tiếp theo sẽ gọi provider mới để phản ánh sự thay đổi trạng thái kho mà không dùng cache cũ.
3. **Thẩm Định Chặt Chẽ Trước Khi Gọi Provider:**  
   Giới hạn `len(requests) <= 256`. Nếu `requests` rỗng, trả về `()` ngay lập tức (0 provider call). Toàn bộ requests được revalidate qua `validate_match_request()` và kiểm tra trùng lặp `request_id` trước khi gọi provider; nếu có request sai kiểu (như `SimpleNamespace`) hoặc trùng ID, ném `ValueError` ngay từ đầu (0 provider call).
4. **Lan Truyền Ngoại Lệ Trung Thực:**  
   Nếu `catalog_provider()` ném ngoại lệ (như `OSError` khi ổ cứng bị rút), Service để ngoại lệ ném ra ngoài nguyên bản (propagate), tuyệt đối không nuốt lỗi để trả về thành công giả `ERR_CATALOG_EMPTY`.

---

## 3. Bảo Toàn Đường Dẫn & Ranh Giới Thẩm Quyền (Path Preservation & Authority)

| Tiêu Chí | Advisory Matching (06B) | Action-Time Execution (ActionGuard - G04/G07) |
|---|---|---|
| **Đầu ra dữ liệu** | `OfflineAssetCandidate` (Chỉ chứa `asset_id`, `category`, `match_score`, `is_native_guidance`, `requires_reconnect`). | Full path thực tế (`library_path`, `external_path`) được resolve bảo mật. |
| **Đường dẫn tệp thô** | **TUYỆT ĐỐI KHÔNG LỘ** `file_path`, `preview_path` ra kết quả tư vấn. | Truy xuất từ `StorageManager.assets()` tại thời điểm chèn. |
| **Tài nguyên Offline** | Cho phép tư vấn nếu `allow_offline=True` (gắn cờ `requires_reconnect=True`). | **CHẶN CỨNG (DENIED)** bởi `ActionGuard` nếu ổ đĩa vẫn chưa được kết nối. |
| **Preview Companion** | Bị loại bỏ hoàn toàn khỏi danh sách ứng viên (không cho phép làm master). | `ActionGuard` từ chối chèn file preview vào timeline video chính. |
| **Native Transition** | Gắn cờ `is_native_guidance=True` (hướng dẫn áp dụng chuyển cảnh DaVinci). | Không import media; hướng dẫn clipboard/Effects Library. |
| **Dummy / Placeholder** | **ZERO PLACEHOLDER:** Trả về `ERR_NO_MATCH_FOUND` kèm gợi ý tiếng Việt. | Báo lỗi rõ ràng trên UI Toast, không chèn clip rỗng. |

---

## 4. Năm Kịch Bản Dữ Liệu Kiểm Thử (Five Concrete Traces & Fixtures)

### Trace 1: Kho Tài Nguyên Rỗng (`Empty Catalog`)
- **Ngữ cảnh:** Provider trả về `[]`.
- **Yêu cầu khớp:** `OfflineAssetMatchRequest(request_id="req_01", category="sfx", query_text="whoosh")`.
- **Hành vi hệ thống:**
  * Matcher trả về: `is_matched=False`, `candidates=()`, `unmatched_reason="ERR_CATALOG_EMPTY"`.
  * `suggested_action`: `"Kho tài nguyên hiện đang trống. Vui lòng nạp thêm gói tài nguyên qua Trình nạp Pack."`.

### Trace 2: Tài Nguyên Có Cả Master File Và Preview Companion (`Master + Companion`)
- **Ngữ cảnh:** Gói B-roll có master video `broll/drone_hanoi.mp4` và file nén xem trước `previews/drone_hanoi.mp4`.
- **Yêu cầu khớp:** `OfflineAssetMatchRequest(request_id="req_02", category="broll", query_text="drone hanoi")`.
- **Hành vi hệ thống:**
  * Matcher chỉ chấp nhận bản ghi master, loại trừ bản ghi companion preview.
  * Candidate trả về: `asset_id="ast_drone_hanoi"`, `category="broll"`, `match_score=65`.

### Trace 3: Tài Nguyên Đang Ngoại Tuyến (`Relevant Offline Asset`)
- **Ngữ cảnh:** Catalog snapshot ghi nhận `availability="offline"`.
- **Yêu cầu khớp A (`allow_offline=False`):**
  * Matcher trả về: `is_matched=False`, `unmatched_reason="ERR_ASSETS_OFFLINE"`.
- **Yêu cầu khớp B (`allow_offline=True`):**
  * Matcher trả về: `is_matched=True`, candidate mang cờ `requires_reconnect=True`.

### Trace 4: Danh Mục Tùy Biến (`Custom Category`)
- **Ngữ cảnh:** Người dùng tạo danh mục riêng `sponsor_bumpers` trong kho.
- **Yêu cầu khớp:** `OfflineAssetMatchRequest(request_id="req_04", category="sponsor_bumpers", query_text="techcombank")`.
- **Hành vi hệ thống:**
  * Matcher xác thực `sponsor_bumpers` là custom category hợp lệ (theo regex `^[a-z][a-z0-9_]{0,47}$`).
  * Chỉ lọc các asset có `category="sponsor_bumpers"`, trả về candidate chính xác.

### Trace 5: Thay Đổi Trạng Thái Kho Giữa Các Batch (`Fresh Payload Per Batch`)
- **Ngữ cảnh:** Batch 1 chạy khi kho có asset `Alpha Sunset`. Sau đó ổ cứng ngoài bị rút hoặc kho cập nhật chỉ còn `Beta Traffic`.
- **Hành vi hệ thống:**
  * Batch 2 gọi provider mới, nhận danh mục mới và phản ánh ngay lập tức: `Alpha Sunset` trở thành `ERR_NO_MATCH_FOUND`, trong khi `Beta Traffic` được khớp thành công.

---

## 5. Ba Quyết Định Ràng Buộc Đã Được Tech Lead Codex Chốt (`docs/architecture/06B2-codex-decisions.md`)

1. **Quyết Định 1: Per-Batch Snapshot, Không Dùng Cache Lâu Sống:**  
   Mỗi batch hợp lệ gọi `catalog_provider()` đúng 1 lần, tạo 1 matcher catalog-bound dùng chung cho toàn bộ requests trong batch. Batch tiếp theo gọi provider mới. Không sử dụng mtime/hash cache hay signal lắng nghe sớm.
2. **Quyết Định 2: Structured Unmatched, Không Tự Động Sửa Plan:**  
   Kết quả unmatched trả về nguyên cấu trúc kèm lý do và request ID. Tuyệt đối không tự xóa slot kịch bản, không gán `asset_id=None` vào recipe hiện hữu, không sinh visual placeholder hay file dummy.
3. **Quyết Định 3: Service Đồng Bộ, Tương Thích Luồng Worker:**  
   `OfflineAssetMatchingService` thuần túy đồng bộ (synchronous). Trách nhiệm điều phối đa luồng (QThread / Worker) thuộc về caller ở tầng UI trong tương lai; không nhúng trực tiếp cơ chế async/QThread vào service core.

---

## 6. Trạng Thái Triển Khai (Implementation Status)
- Lát cắt `G06-06B2-I` đã hoàn thành 100%:
  * Module production: `src/core/matching_service.py` (Lớp `OfflineAssetMatchingService`).
  * Module test: `tests/core/test_matching_service.py` (15 unit tests TDD).
  * Finite suite: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_matching_service.py tests/core/test_offline_asset_matcher.py tests/core/contracts/test_asset_dto.py -q` $\to$ **97 passed in 0.47s (Exit Code: 0)**.
