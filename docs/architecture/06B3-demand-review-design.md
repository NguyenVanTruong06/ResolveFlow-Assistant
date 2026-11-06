# G06-06B3-D — Thiết Kế Nhu Cầu Tài Nguyên Từ Kịch Bản & Consumer Review

> HIỆN HÀNH 2026-10-29: 06B3-I1 ACCEPTED_WITH_RECORDED_LIMITS theo06B3-I1-gate-acceptance.md; next06B3-I2-P1 worker/strict storage read. Review UI chưa nối.

**Tài liệu thiết kế kiến trúc chuẩn hóa (Design Document)**  
**Trạng thái:** `DESIGN_READY` (HIGH Worker Level, pure architecture design)  
**Phạm vi:** Nguồn nhu cầu explicit từ kịch bản, hợp đồng DTO, field mapping, duration policy, advisory sidecar, UI entrypoint Review, off-thread catalog integration và acceptance test matrix.  
**Không phụ thuộc:** Không sửa `src/` hoặc `tests/`, không gọi Resolve API, không đổi recipe schema hiện hành (06C deferred), không khôi phục ZIP 3.18 GiB.

---

## 1. Source-Grounded Callgraph

### 1.1. Luồng Dữ Liệu Hiện Hữu (CODE_VERIFIED)

```
[Bên Ngoài / Người Dùng]
   │
   ├─► Dán JSON Web Prompt vào ReviewPage (Clipboard / txt_json_input)
   │     └─► src/ui/director/pages/review_page.py:4781 (_apply_json_script)
   │     └─► src/ui/director/pages/review_page.py:4720 (_paste_to_slot("slot1"))
   │     └─► src/ui/director/pages/review_page.py:4746 (_on_dropzone_json_recognized)
   │
   ▼
[Bóc Tách & Nhận Diện Cấu Trúc JSON]
   │  src/ui/director/director_state.py:430 (parse_web_prompt_json)
   │  src/ui/director/director_state.py:373 (_extract_all_json_objects)
   │  Chặn Macro Overview: contains_project_overview_draft (review_page.py:4807)
   │  Chặn Batch Draft: contains_batch_draft_plan (director_state.py:267, review_page.py:4818)
   │  Phân loại: _is_video_plan_dict (director_state.py:356) -> _summarize_video_plan (director_state.py:393)
   │
   ▼
[Nạp Vào Trạng Thái ReviewPage & DirectorState]
   │  src/ui/director/pages/review_page.py:4804 (_apply_json_data)
   │     ├─► self._video_plan_data = raw_plan (review_page.py:4880, 4902, 4932)
   │     ├─► self._video_plan_summary = summary (review_page.py:4881, 4903, 4933)
   │     ├─► Cập nhật UI: lbl_slot_vid_status.setText("✓ Đã nạp (X cảnh · Y meme · Z sfx)") (review_page.py:4907)
   │     ├─► Đồng bộ mini timeline: self.mini_timeline.set_blocks(...) (review_page.py:4899, 4918)
   │     └─► Phát tín hiệu: config_changed.emit(), json_applied.emit(data, summary) (review_page.py:4937-4938)
   │  src/ui/director/pages/review_page.py:5061 (save_to_config -> config.video_plan_data)
   │  src/ui/director/pages/review_page.py:4986 (load_from_config <- config.video_plan_data)
   │
   ▼
[Hạ Tầng Dựng Phim Hiện Tại (Downstream Execution)]
   │  src/ui/director/director_controller.py:69 (DirectorController.build_pipeline_kwargs):
   │     story_plan = cfg.video_plan_data or (cfg.web_prompt_pasted_data.get("data") if cfg.web_prompt_pasted_data else None)
   │     kwargs["story_arrangement_override"] = story_plan (director_controller.py:112)
   │  src/ui/director/director_controller.py:121 (start_pipeline -> PipelineWorker)
   │
   ▼
[Hạ Tầng Legacy Đã Khảo Sát (CẢNH BÁO: DEFERRED NGOÀI SLICE NÀY)]
   │  src/ui/auto/auto_window.py:5068 (_on_copilot_plan_applied):
   │     Nhận CopilotDirectorPlan với broll_inserts, sfx_inserts
   │  src/ui/auto/auto_window.py:5155 (SourceResolver.audit_plan / auto_resolve_missing_assets):
   │     resolver = SourceResolver(available_videos=self.selected_files)
   │     plan = resolver.auto_resolve_missing_assets(plan)
   │  src/core/source_resolver.py:180-255 (auto_resolve_missing_assets):
   │     ⚠️ ĐƯỜNG DẪN LEGACY NÀY VẪN REACHABLE TẠI AUTO_WINDOW VÀ CÓ FALLBACK TRONG KHO.
   │     ⚠️ ĐƯỢC GHI NHẬN DEFERRED NGOÀI SLICE I1.
   │     ⚠️ Adapter mới (G06-06B3-I1) TUYỆT ĐỐI KHÔNG gọi SourceResolver hoặc GlobalAssetPool.
   │     ⚠️ Zero-mutation chỉ bảo đảm cho luồng mới; không tuyên bố toàn bộ app đã sạch.
```

### 1.2. Luồng Kiến Trúc Đề Xuất (PROPOSED: Zero-Mutation Advisory Bridge)

```
[ReviewPage: self._video_plan_data Đã Nạp]
   │
   ├─► User bấm Action rõ ràng: "🔍 Gợi ý tài nguyên" (btn_match_assets trên card_slot_video)
   │     └─► PROPOSED: review_page.py:_on_match_assets_clicked
   │
   ▼
[Chuyển Hóa Nhu Cầu Thành Batch Yêu Cầu (Pure Demand Adapter)]
   │  PROPOSED: src/core/plan_demand_adapter.py (extract_plan_demands / build_match_requests)
   │  - Đọc broll_inserts và sfx_inserts từ raw dict (hoặc CopilotDirectorPlan)
   │  - Validate kiểu dữ liệu, bounds, sinh correlation IDs ổn định ("broll:0", "sfx:0")
   │  - Ánh xạ category có nghĩa; reject non-canonical (green_screen -> unsupported)
   │  - Giới hạn tối đa 256 requests; phần dư đánh dấu ERR_BATCH_CAP_OVERFLOW
   │
   ▼
[Thực Thi Off-Thread Không Gây Block GUI (AssetMatchingWorker)]
   │  PROPOSED: src/ui/director/workers/asset_matching_worker.py (QThread)
   │  - Nhận AssetDemandBatch immutable + matching_generation
   │  - Khởi tạo fresh StorageManager trên worker thread:
   │       catalog_provider = lambda: StorageManager().assets()
   │  - Khởi tạo OfflineAssetMatchingService(catalog_provider=catalog_provider, alias_groups=None)
   │  - Gọi service.match_batch(requests) -> Tuple[OfflineAssetMatchResult, ...] (1 catalog call)
   │  - Đóng gói kết quả thành AssetMatchingSidecar (chứa needs, candidates, unmatched reasons)
   │  - Bắn terminal signal: matching_completed(sidecar, generation)
   │
   ▼
[Hiển Thị Sidecar Khuyến Nghị Tại ReviewPage (Consumer View)]
   │  PROPOSED: review_page.py:_on_matching_completed(sidecar, generation)
   │  - Kiểm tra generation == self._matching_generation (chống đè stale response)
   │  - Lưu self._matching_sidecar = sidecar
   │  - 0 MUTATION: self._video_plan_data giữ nguyên 100% không đổi
   │  - Render danh sách needs + candidates kèm điểm relevance & availability
   │  - ActionGuard (G04) áp dụng kiểm định độc lập tại action-time khi người dùng chọn chèn.
```

---

## 2. Nguồn Nhu Cầu Explicit & Bản Đồ Trường (Field Mapping)

### 2.1. Nguồn Nhu Cầu Thực Tế Trong Kịch Bản

1. **`broll_inserts` & `sfx_inserts`:**
   - Trong `src/core/story_copilot.py:40-48`, lớp `CopilotMediaInsert` xác định các trường:
     * `timeline_sec: float`: Vị trí chèn trên timeline của bản dựng (giây).
     * `duration_sec: float`: Thời lượng chèn khuyến nghị (mặc định 2.5s cho B-Roll, 1.0s cho SFX).
     * `src_in: float`: Điểm bắt đầu cắt trong clip gốc (mặc định 0.0s).
     * `asset_file: str`: Tên tệp do AI đề xuất hoặc gợi ý từ prompt cũ (vd: `"do_mixi_ao_that_day.mp4"`).
     * `asset_type: str`: Loại tài nguyên (`"meme"`, `"broll"`, `"green_screen"`, `"sfx"`).
     * `description: str`: Mô tả ngữ nghĩa lý do chèn (vd: `"Meme ngơ ngác khi bị troll"`).
     * `suggested_file: str`: Alias tương thích ngược với kịch bản cũ.
   - Khi kịch bản được nạp qua `parse_web_prompt_json`, nó nằm dưới dạng `dict` trong `self._video_plan_data` chứa hai danh sách `data.get("broll_inserts", [])` và `data.get("sfx_inserts", [])`.

2. **Ranh Giới Rõ Ràng Với `ValidatedSegmentCut`:**
   - `ValidatedSegmentCut` (`src/core/batch_draft_contracts.py:100-112`) chỉ chứa hình học cắt ghép cảnh: `start_sec`, `end_sec`, `role`, `note`, `duration_sec`.
   - `ValidatedSegmentCut` **KHÔNG PHẢI** là nhu cầu SFX hay BGM. Không được tự tiện suy luận mỗi cut thành một âm thanh "Whoosh" hoặc một phân đoạn nhạc nền.
   - Nhu cầu tài nguyên chỉ được tạo ra khi có khai báo explicit trong `broll_inserts` hoặc `sfx_inserts`.

### 2.2. Phân Định Search Hint vs Exact Asset Identity

- **Legacy `asset_file`:**
  - Chuỗi `asset_file` trong kịch bản chỉ là **advisory search hint** do AI điền dựa trên mẫu văn bản (hoặc hallucination). Nó **KHÔNG PHẢI** là định danh tài nguyên (Asset ID), và **TUYỆT ĐỐI KHÔNG PHẢI** là đường dẫn tệp thực tế trên đĩa.
  - Tuyệt đối cấm dùng `asset_file` để bypass qua `GlobalAssetPool.resolve_meme()` hoặc `os.path.exists()` như hạ tầng legacy.
  - Cả `description` và `asset_file` chỉ được dùng làm chất liệu trích xuất từ khóa tìm kiếm (`query`).

### 2.3. Quy Tắc Ánh Xạ Category (Category Mapping Policy)

Dựa trên taxonomy chuẩn của `StorageManager.CATEGORIES` (`memes`, `broll`, `sfx`, `overlays`, `templates`, `luts`, `bgm`) và `UI_CATEGORY_ALIASES` từ `06B1`:

| Raw `asset_type` trong plan | Thuộc tính Insert | Category Target (`OfflineAssetMatchRequest`) | Ghi chú / Trạng thái |
| :--- | :--- | :--- | :--- |
| `"meme"` | `broll_inserts` | `"memes"` | Chuẩn hóa qua alias `UI_CATEGORY_ALIASES["meme"] = "memes"` |
| `"broll"` | `broll_inserts` | `"broll"` | Khớp trực tiếp category chuẩn của StorageManager |
| `"overlay"` | `broll_inserts` | `"overlays"` | Chuẩn hóa qua alias `UI_CATEGORY_ALIASES["overlay"] = "overlays"` |
| `"sfx"` | `sfx_inserts` | `"sfx"` | Khớp trực tiếp category chuẩn của StorageManager |
| `"green_screen"` | `broll_inserts` | **TỪ CHỐI / UNSUPPORTED** | Không gộp bừa vào `overlays` hay `memes`. Đánh dấu `status = "unsupported_category"`, không gửi request. |
| Khác / Không rõ | Bất kỳ | **TỪ CHỐI / NEEDS_REVIEW** | Đánh dấu `status = "unsupported_category"`, không tạo request giả định. |

> [!IMPORTANT]
> **Chính sách Green Screen & Non-Canonical Types:**
> Kỹ thuật green screen yêu cầu Chroma Keyer (Delta Keyer / Ultra Keyer) trên trang Fusion, chưa nằm trong phạm vi rough-cut tư vấn thuần 06B. Việc gộp green screen vào overlays hay memes sẽ làm hỏng trải nghiệm người dùng vì tài nguyên trả về đòi hỏi bước xử lý tách nền phức tạp. Do đó, hệ thống giữ nguyên need này với lý do `ERR_UNSUPPORTED_CATEGORY` để người dùng xử lý thủ công.

### 2.4. Bảng Ánh Xạ Chi Tiết: Raw Plan -> Owned Demand -> Match Request

```
Raw Plan Entry (dict)
   │
   ├─► timeline_sec: float ──────────────────────────► timeline_sec (float >= 0.0)
   ├─► duration_sec: float ──────────────────────────► desired_duration_sec (float > 0.0)
   ├─► asset_type: str ──────────────────────────────► raw_type (str)
   │                                                         │
   │                                                         ├─► [Nếu hợp lệ]: category (str)
   │                                                         └─► [Nếu sai]: marked unsupported
   │
   ├─► description / asset_file: str ────────────────► search_query (str, UTF-8 stripped)
   │                                                         │
   │                                                         ├─► [Nếu có chữ]: query (str)
   │                                                         └─► [Nếu rỗng]: marked empty_query
   │
   └─► Mảng nguồn & Index (i) ───────────────────────► correlation_id (JSON array [plan_revision, slot, i])
                                                             │
                                                             ▼
                                                OfflineAssetMatchRequest(
                                                    request_id=correlation_id,
                                                    category=category,
                                                    query_text=search_query,
                                                    scene_context="",
                                                    emotion_or_mood="",
                                                    template_type=template_type,
                                                    subcategory_constraint=None,
                                                    min_duration_sec=None,    # Không tự ép hard bound
                                                    max_duration_sec=None,    # Không tự ép hard bound
                                                    top_k=top_k,
                                                    allow_offline=allow_offline
                                                )
```

---

## 3. Chính Sách Thời Lượng, Dữ Liệu & Giới Hạn (Duration, Types & Bounds)

### 3.1. Phân Biệt Desired Insertion Duration vs Source Media Bounds

1. **`desired_duration_sec` (Thời lượng chèn mong muốn):**
   - Là thông số bố cục timeline (ví dụ: AI đề xuất clip B-Roll xuất hiện trong 2.5 giây trên Video Track 2).
   - Đây là thuộc tính của **Timeline Placement**, hoàn toàn **KHÔNG PHẢI** là ràng buộc về độ dài tối đa của tệp gốc trên đĩa.
   - Nếu metadata số bị khuyết trong plan, giá trị mặc định là `None`, không tự gán 0 hay 2.5/1.0, không tự clamp 0.05..3600.
2. **`min_duration_sec` / `max_duration_sec` (Ràng buộc cứng tệp nguồn):**
   - Một đoạn âm thanh SFX "whoosh" có thể dài 0.8 giây hoặc một tiếng sấm rền dài 5 giây; cả hai đều có thể dùng cho điểm chèn SFX 1.0 giây.
   - Một tệp video B-Roll phong cảnh trong kho thường dài 10 - 30 giây. Trình dựng phim sẽ cắt lấy 2.5 giây (`src_in` đến `src_in + 2.5s`).
   - Nếu tự động gán `max_duration_sec = 2.5` vào `OfflineAssetMatchRequest`, matcher sẽ **loại bỏ toàn bộ** các clip B-Roll chất lượng cao dài > 2.5s vì vi phạm hard constraint.
   - **Quy tắc chốt:** Requests luôn đặt `min_duration_sec = None` và `max_duration_sec = None`. Không tự thêm `strict_source_duration` vào raw schema.

### 3.2. Xử Lý Truy Vấn Rỗng & Whitespace

- Nếu cả `description`, `asset_file` và `suggested_file` đều rỗng, hoặc chỉ chứa ký tự khoảng trắng sau khi strip:
  * Adapter **KHÔNG ĐƯỢC PHÉP** tự ý bịa từ khóa (ví dụ: không được tự điền "funny meme" hay "sfx click").
  * Adapter **KHÔNG ĐƯỢC PHÉP** gọi repository fallback ngầm.
  * Bản ghi nhu cầu được phân loại trực tiếp là `empty_query` với lý do `ERR_DEMAND_QUERY_EMPTY`.
  * Không tạo `OfflineAssetMatchRequest` cho bản ghi này (bảo vệ Service và Matcher khỏi lỗi `ValueError`).

### 3.3. Kiểu Dữ Liệu Khắt Khe & Correlation ID Ổn Định

1. **Strict Types:**
   - `timeline_sec`: Bắt buộc là số hữu hạn (`float` hoặc `int`), loại bỏ `bool`, `str`, `NaN`, `Inf`, bigint overflow. Giá trị `>= 0.0`. Khuyết -> `None`.
   - `desired_duration_sec`: Bắt buộc là số hữu hạn, `> 0.0`. Khuyết -> `None`. Bất kỳ giá trị không hợp lệ nào (kể cả null) làm demand thành `invalid` (`ERR_DEMAND_INVALID`).
   - `query`: Chuỗi ký tự UTF-8 hợp lệ, không chứa lone surrogates.
2. **Stable Correlation ID:**
   - Cấu trúc: JSON compact array `[plan_revision, source_slot, source_index]` (separators=(',', ':'), ensure_ascii=True).
   - Đảm bảo tính đơn nhất (unique), loại bỏ nguy cơ delimiter injection, ổn định tuyệt đối và gắn với `plan_revision`.
3. **Finite Cap 256 & Chính Sách Tràn Lô (Overflow Policy):**
   - Giới hạn cứng của Service: `MAX_BATCH_SIZE = 256`.
   - Nếu tổng số nhu cầu hợp lệ `N > 256`:
     * 256 nhu cầu đầu tiên (`0` đến `255`) được đưa vào batch để gửi sang `OfflineAssetMatchingService`.
     * Các nhu cầu từ `256` trở đi **KHÔNG BỊ ÂM THẦM VỨT BỎ**. Chúng được giữ lại trong cấu trúc sidecar với trạng thái `status = "overflow"` và lý do `ERR_BATCH_CAP_OVERFLOW`.
     * Các demand `invalid`, `unsupported`, `empty_query` không chiếm quota 256 requests.

---

## 4. Hợp Đồng Advisory Sidecar (Zero Plan Mutation Contract)

### 4.1. Cấu Trúc DTO Của Sidecar Báo Cáo

Tạo hợp đồng DTO bất biến (`frozen=True`) độc lập, không phụ thuộc clock, không xâm lấn schema của kịch bản hiện tại:

```python
@dataclass(frozen=True)
class AssetDemandItem:
    """Nhu cầu tài nguyên độc lập được bóc tách từ một mục kịch bản."""
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

@dataclass(frozen=True)
class AssetDemandBatch:
    """Lô nhu cầu kịch bản kèm danh sách requests đã sẵn sàng để gửi sang Service."""
    plan_revision: str
    manifest_id: Optional[str]
    demands: Tuple[AssetDemandItem, ...]
    requests: Tuple[OfflineAssetMatchRequest, ...]

@dataclass(frozen=True)
class DemandMatchEntry:
    """Kết quả khớp tư vấn cho một nhu cầu tài nguyên."""
    demand: AssetDemandItem
    result: Optional[OfflineAssetMatchResult] = None

    @property
    def is_matched(self) -> bool:
        return bool(self.result and self.result.is_matched)

    @property
    def candidates(self) -> Tuple[OfflineAssetCandidate, ...]:
        return self.result.candidates if self.result else ()

    @property
    def unmatched_reason(self) -> Optional[str]:
        return self.result.unmatched_reason if self.result else self.demand.rejection_reason

@dataclass(frozen=True)
class AssetMatchingSidecar:
    """Báo cáo tư vấn tài nguyên tổng thể gắn kèm kịch bản (không sửa kịch bản)."""
    plan_revision: str
    manifest_id: Optional[str]
    entries: Tuple[DemandMatchEntry, ...]

    @property
    def total_demands(self) -> int:
        return len(self.entries)

    @property
    def matched_count(self) -> int:
        return sum(1 for e in self.entries if e.is_matched)

    @property
    def unmatched_count(self) -> int:
        return sum(1 for e in self.entries if e.demand.status == "eligible" and not e.is_matched)

    @property
    def invalid_count(self) -> int:
        return sum(1 for e in self.entries if e.demand.status == "invalid")

    @property
    def empty_query_count(self) -> int:
        return sum(1 for e in self.entries if e.demand.status == "empty_query")

    @property
    def unsupported_count(self) -> int:
        return sum(1 for e in self.entries if e.demand.status == "unsupported")

    @property
    def overflow_count(self) -> int:
        return sum(1 for e in self.entries if e.demand.status == "overflow")
```

### 4.2. Cam Kết Bất Biến Với Plan Hiện Hành

1. **Zero Slot Mutation:**
   - Trường `self._video_plan_data` trong `ReviewPage` và `DirectorConfig.video_plan_data` được bảo toàn nguyên vẹn 100% byte-for-byte.
   - Không ghi đè trường `asset_file` trong `broll_inserts` hay `sfx_inserts`.
   - Không tự ý chọn ứng viên điểm cao nhất (top 1) để thay thế nội dung kịch bản.
2. **Tách Biệt 06C Migration:**
   - Việc chuyển đổi kịch bản sang cấu trúc có `asset_id` thuộc nhiệm vụ `G06-06C` (Director Plan Recipe Integration). 06B3 không được lén lút sửa schema của kịch bản hiện hành.
3. **Authority Store & ActionGuard:**
   - Candidate chỉ chứa tham chiếu định danh (`asset_id`, `name`, `score`, `offline_flag`, `matched_signals`), không chứa quyền thực thi hay đường dẫn tệp trực tiếp cho bản dựng.
   - Tệp vật lý và metadata nguồn chỉ tồn tại trong `StorageManager`.
   - Lớp bảo vệ `ActionGuard` (đã có từ G04) tiếp tục chịu trách nhiệm thẩm định an toàn tại thời điểm người dùng kích hoạt hành động chèn vào timeline.
4. **Native Transitions:**
   - Các gợi ý chuyển cảnh chỉ mang tính chất hướng dẫn clipboard / Effects Library; không hứa hẹn gọi API áp dụng tự động tại cut.
   - Không tải dummy placeholder, không tải ngầm từ mạng.

---

## 5. Đề Xuất UI Entrypoint Tại ReviewPage

### 5.1. Vị Trí Giao Diện & Bố Cục

Trong `src/ui/director/pages/review_page.py`, tại khối `card_slot_video` (dòng 957 - 1001):
- Hiện tại, thanh tiêu đề `h_slot_vid_top` chứa:
  * `lbl_slot_vid_title`: `"🎬 1. Video Plan (V1)"`
  * `btn_paste_slot1`: `"📋 Dán vào Slot 1"`
  * `lbl_slot_vid_status`: `"✓ Đã nạp (X cảnh · Y meme · Z sfx)"`
- **Đề xuất UI Action:** Thêm nút bấm rõ ràng và tinh gọn:
  * `btn_match_assets`: `"🔍 Gợi ý tài nguyên"`
  * Đặt ngay bên cạnh `btn_paste_slot1` trong `h_slot_vid_top`.
  * Trạng thái mặc định: `setEnabled(False)`.
  * Khi nạp kịch bản có `broll_count + sfx_count > 0`: `setEnabled(True)`.
- **Khu vực hiển thị kết quả (Sidecar Drawer / Panel):**
  * Thêm một khung nhỏ có thể thu gọn (`QFrame`) ngay dưới `lbl_slot_vid_status`: `frame_matching_sidecar`.
  * Mặc định ẩn (`setVisible(False)`).
  * Khi người dùng bấm nút: hiển thị tiến trình không khóa giao diện (`"Đang tìm 12 tài nguyên trong kho..."`).
  * Khi hoàn tất: hiển thị badge tổng kết:
    * `"✓ 10/12 đã khớp"` (xanh lá `#4ade80`)
    * `"✕ 2 chưa tìm thấy"` (vàng cam `#f59e0b`)
    * Kèm nút `"Xem chi tiết"` mở rộng danh sách đối chiếu trung thực:
      - Nhu cầu #1: [Meme] "Meme ngơ ngác" ➔ Khớp: `do_mixi_ao_that_day` (85đ)
      - Nhu cầu #2: [SFX] "Tiếng nổ bất ngờ" ➔ Khớp: `vine_boom` (90đ)
      - Nhu cầu #3: [Green Screen] "Nổ tung" ➔ ⚠ Không hỗ trợ (green_screen)

```
┌────────────────────────────────────────────────────────────────────────┐
│ 🎬 1. Video Plan (V1)        [🔍 Gợi ý tài nguyên]  [📋 Dán vào Slot 1] │
│ ✓ Đã nạp (6 phân đoạn · 4 meme · 2 sfx)                                │
│ ────────────────────────────────────────────────────────────────────── │
│ 📦 Tài nguyên kho: 5/6 đã khớp · 1 không hỗ trợ                        │
│   • [Meme 1] "Mắt chữ O mồm chữ A" ➔ ClipMixi_NgoNgac (75đ)            │
│   • [Meme 2] "Cười lăn lộn"        ➔ PewPew_CuoiLon (80đ)              │
│   • [SFX 1]  "Tiếng nổ ngắn"       ➔ Vine_Boom (90đ)                   │
│   • [B-Roll] "Green screen vỗ tay" ➔ ⚠ Cần duyệt (green_screen)        │
└────────────────────────────────────────────────────────────────────────┘
```

### 5.2. Nguyên Tắc Thiết Kế UI & Tận Dụng Lifecycle

1. **Không tạo công cụ tìm kiếm thứ hai:** Không xây dựng thanh search độc lập hay dialog phức tạp cạnh tranh với Asset Hub.
2. **Không thao tác Resolve từ Review:** Không tự ý chèn clip vào DaVinci Resolve khi đang ở bước xem gợi ý.
3. **Tận dụng Lifecycle Hiện Hữu:** Sử dụng cơ chế monotonic generation (`self._matching_generation += 1`) tương tự như `_prompt_generation` (dòng 412) và `_kinetic_generation` (dòng 427) trong `ReviewPage`.

---

## 6. Cơ Chế Off-Thread Catalog & Quản Trị Bất Đồng Bộ

### 6.1. Nguyên Tắc Tách Rời Tuyệt Đối Khỏi GUI Thread

- **Cấm I/O trên GUI Thread:** Việc đọc catalog từ đĩa, kiểm tra `os.stat`, và phân loại trạng thái offline tiêu tốn từ vài chục đến hàng trăm milli-giây tùy vào dung lượng ổ cứng. Mọi thao tác này bắt buộc phải diễn ra trên luồng nền (`QThread`).
- **Worker Factory:**
  ```python
  def _worker_catalog_provider() -> list:
      # Chạy 100% trên worker thread
      from src.core.storage_manager import StorageManager
      return StorageManager().assets()
  ```
  Nhờ Dependency Injection của `OfflineAssetMatchingService`, Service nhận provider này và chỉ gọi đúng **1 lần duy nhất** cho toàn bộ batch.

### 6.2. Phân Biệt Ngoại Lệ Provider vs Empty Catalog

1. **Lỗi Provider (`StorageOfflineError`, `OSError`, `PermissionError`):**
   - Không được bắt lỗi rồi biến thành mảng rỗng `[]`.
   - Service để ngoại lệ lan truyền nguyên bản (`propagate`).
   - Worker bắt ngoại lệ này, ghi nhận thông điệp lỗi chính xác và phát tín hiệu `matching_failed(error_message, generation)`.
   - Giao diện Review hiển thị cảnh báo lỗi truy cập kho (ví dụ: `"Ổ đĩa kho tài nguyên đang bị ngắt kết nối"`), không báo nhầm là "Kho trống".
2. **Catalog Trống Thực Sự (Empty Catalog):**
   - Provider chạy thành công và trả về `[]`.
   - Matcher tạo kết quả bình thường với `is_matched = False`, `reason = ERR_CATALOG_EMPTY`, `match_score = 0`.

### 6.3. Cooperative Cancellation & Hủy An Toàn

- **Kiểm tra gián đoạn hợp tác:** Worker thường xuyên kiểm tra `self.isInterruptionRequested()`:
  * Trước khi trích xuất demands.
  * Trước khi gọi service matching.
  * Sau khi hoàn tất matching trước khi bắn tín hiệu về GUI.
- **Giới hạn I/O không thể ngắt:** Cuộc gọi `StorageManager().assets()` thực thi mã Python đọc JSON/stat đồng bộ, không thể ngắt giữa chừng ở cấp hệ điều hành. Worker tôn trọng việc đọc này hoàn tất rồi mới kiểm tra cờ hủy.
- **CẤM `QThread.terminate()`:** Tuyệt đối không dùng phương thức cưỡng chế dừng luồng vì sẽ gây rò rỉ bộ nhớ C++ của Qt và dẫn đến deadlock ứng dụng.
- **Chính Sách Chống Đè Dữ Liệu Cũ (Stale Response Rejection):**
  * Mỗi lần kích hoạt matching, GUI tăng `self._matching_generation += 1`.
  * Nếu người dùng dán kịch bản mới hoặc đóng trang trong lúc worker đang chạy:
    - Khi worker kết thúc và phát tín hiệu, handler tại GUI đối soát:
      ```python
      if generation != self._matching_generation:
          logger.info("Bỏ qua kết quả matching cũ (gen %d != current %d)", generation, self._matching_generation)
          return
      ```
    - Kết quả của kịch bản cũ lập tức bị hủy bỏ, đảm bảo không bao giờ đè lên kịch bản mới.

---

## 7. Ma Trận Kiểm Thử Nghiệm Thu (Acceptance Test Matrix — 12 Ca)

| Mã Ca | Kịch Bản Kiểm Thử (Input) | Kết Quả Kỳ Vọng (Expected Outputs & Counters) | Tầng Kiểm Thử (Layer) |
| :--- | :--- | :--- | :--- |
| **TC-01** | `empty_catalog`: Provider trả về `[]` cho batch có 3 nhu cầu (2 meme, 1 sfx). | 3 results trả về, `is_matched=False`, `reason=ERR_CATALOG_EMPTY`, `score=0`. 0 crash. Sidecar ghi nhận `matched_count=0, unmatched_count=3`. | Pure Unit Test |
| **TC-02** | `master_preview_metadata`: Catalog chứa 1 asset chỉ có thumbnail/preview (không master), 1 asset có đủ master. Query khớp cả hai. | Chỉ asset có master được trả làm candidate. Asset thiếu master bị loại trừ qua quarantine warnings. `quarantined_warnings > 0`. | Pure Unit Test |
| **TC-03** | `offline_opt_in_default`: Kịch bản gửi demands với cờ mặc định `allow_offline=False`. Catalog có asset phù hợp nhưng đang `offline`. | Kết quả trả về `is_matched=False`, `reason=ERR_ASSETS_OFFLINE`. Nếu kích hoạt `allow_offline=True`, trả về candidate kèm `requires_reconnect=True`. | Pure Unit Test |
| **TC-04** | `explicit_custom_category`: Nhu cầu yêu cầu category mở rộng đã đăng ký trong taxonomy (ví dụ: `"motion_graphics"`). | Request hợp lệ, matcher lọc đúng danh mục mở rộng, không ném `ValueError`. | Pure Unit Test |
| **TC-05** | `missing_query_unsupported_type`: Insert 1 có description rỗng và filename rỗng; Insert 2 có `asset_type="green_screen"`. | Adapter không gửi 2 mục này sang Service (0 provider call nếu batch chỉ gồm 2 mục này). Sidecar ghi nhận 1 `empty_query` và 1 `unsupported_category`. | Pure Unit Test |
| **TC-06** | `desired_duration_vs_hardbounds`: Insert có `duration_sec=2.5`. Catalog có video B-Roll dài 15.0s và SFX dài 0.8s. | Cả hai asset đều được giữ làm ứng viên khả thi; adapter không tự gán `max_duration=2.5` làm triệt tiêu ứng viên. | Pure Unit Test |
| **TC-07** | `invalid_overflow_needs`: Kịch bản chứa 270 inserts hợp lệ. Giới hạn tối đa là 256. | 256 requests đầu tiên được gửi trong 1 batch duy nhất (1 provider call). 14 requests còn lại ghi nhận `ERR_BATCH_CAP_OVERFLOW`. Batch hoàn tất an toàn. | Pure Unit Test |
| **TC-08** | `provider_error_propagation`: Catalog provider ném `StorageOfflineError("Disk disconnected")`. | Service lan truyền nguyên bản ngoại lệ; worker bắt ngoại lệ và phát tín hiệu `matching_failed`. Không bị nuốt thành empty catalog. | Pure Unit Test |
| **TC-09** | `plan_changed_while_worker`: Worker đang chạy cho Plan A (`gen=1`). Người dùng dán Plan B (`gen=2`). Worker A hoàn tất phát tín hiệu với `gen=1`. | GUI phát hiện lệch generation (`1 != 2`), âm thầm loại bỏ kết quả cũ. `self._video_plan_data` của Plan B không bị suy chuyển. | Offscreen GUI Test |
| **TC-10** | `cancel_close_cleanup`: Worker đang thực thi thì ReviewPage bị đóng (`closeEvent`) hoặc người dùng hủy. | Gọi `requestInterruption()`, ngắt kết nối tín hiệu giao diện, worker tự giải phóng khi kết thúc, 0 rò rỉ thread, 0 crash. | Offscreen GUI Test |
| **TC-11** | `valid_result_zero_plan_mutation`: Batch 5 nhu cầu khớp thành công hoàn toàn. | Sidecar nhận đủ 5 kết quả. `self._video_plan_data` giữ nguyên vẹn 100% hash nội dung; không có trường `asset_file` nào bị ghi đè. | Offscreen GUI Test |
| **TC-12** | `duplicate_request_ids_rejection`: Adapter đảm bảo sinh correlation ID phân biệt tuyệt đối (`broll_000`, `broll_001`). Nếu cố tình chèn trùng ID: | Service ném `ValueError` ngay TRƯỚC khi gọi catalog provider (0 provider call). Bảo vệ tính toàn vẹn của batch. | Pure Unit Test |

---

## 8. Đề Xuất First Implementation Slice & Quyết Định Chính Sách

### 8.1. Đề Xuất Lát Cắt Triển Khai Đầu Tiên (First Implementation Slice)

Để đảm bảo nguyên tắc kiến trúc "Từng bước hữu hạn — Không xây module chết — Không sửa UI khi hợp đồng chưa vững", khuyến nghị chia lộ trình thành 2 lát cắt phụ thuộc, và **CHỈ ĐỀ XUẤT DUY NHẤT LÁT CẮT ĐẦU TIÊN** cho vòng code tiếp theo:

* **Lát Cắt 1 (ĐÃ THỰC THI & HOÀN THIỆN ĐẦY ĐỦ — G06-06B3-I1, R1 & R2):**  
  **`G06-06B3-I1-R2: Pure Plan Demand Adapter & Shared Boundary Enforcement`**
  - **Trạng thái:** `COMPLETED_AND_VERIFIED`.
  - **Mục tiêu:** Xây dựng cầu nối chuyển đổi từ kịch bản thô (`_video_plan_data`) sang danh sách `OfflineAssetMatchRequest` và cấu trúc báo cáo `AssetMatchingSidecar`, tích hợp cơ chế thẩm định ranh giới dùng chung (Shared Validation) giữa Constructor và Assemble:
    1. `src/core/contracts/plan_demand_contracts.py`: Định nghĩa các hàm `validate_safe_numeric`, `validate_asset_demand_item`, `validate_demand_candidate`, `validate_demand_match_result`, `validate_demand_batch`. Ràng buộc chặt chẽ: candidate availability CHỈ nhận `'online'` hoặc `'offline'`; duration_sec bắt buộc `None` hoặc số hữu hạn `> 0.0` (chặn `bool`, `0.0`, `NaN`, `Inf`, tràn số `10**400`); matched_signals và pack_name từ chối kiểu mutable list; yêu cầu UTF-8 không chứa lone surrogates; bảo đảm đẳng thức phân hoạch tuyệt đối `partition_sum == total_demands`.
    2. `src/core/plan_demand_adapter.py`: Sử dụng shared validation đồng bộ; chặn đứng các wrapper envelopes (`dual_plan`, `kinetic_plan`, `generic_plan`, `video_plan` có chứa trường `data`, `project_overview_draft`, `batch_draft`), kiểm soát chặt chẽ fallback `suggested_file` (chỉ fallback khi `asset_file` khuyết/trắng; stem rỗng trả về `empty_query`), và thẩm định kết quả/candidate nghiêm ngặt.
  - **Module kiểm thử:** `tests/core/test_plan_demand_adapter.py` (34 unit tests TDD bao phủ trọn vẹn toàn bộ 15 residual counterexamples từ probe của Tech Lead Codex).
  - **Bằng chứng finite suite:** 131/131 tests passed trong 0.61s (Exit code 0). Log lưu trữ tại `.bridge/ux-evidence/G06-06B3-I1-R2-THEN-I2-D/pytest_run.log`.

* **Lát Cắt 2 (PROPOSED — Giai Đoạn Tiếp Theo):**  
  **`G06-06B3-I2: ReviewPage Worker & Advisory UI Integration`**
  - Tạo `AssetMatchingWorker` (QThread).
  - Thêm nút `"🔍 Gợi ý tài nguyên"` và panel hiển thị sidecar trên `ReviewPage`.
  - Kiểm thử vòng đời GUI offscreen (TC-09, TC-10).
  - Lưu ý: Generation-only chưa chứng minh plan/root freshness; cần binding revision và catalog context ở lát cắt này.

### 8.2. Ba Câu Hỏi Chính Sách (Policy Questions)

#### Câu hỏi 1: Thứ tự ưu tiên trích xuất Search Query giữa `description` và `asset_file`
- **Tùy chọn A (Khuyến nghị):** Ưu tiên `description.strip()` trước nếu có chữ; nếu `description` rỗng, lấy phần stem của `asset_file` (đã bỏ phần mở rộng như `.mp4`, `.wav` và thay ký tự gạch dưới bằng khoảng trắng). Nếu cả hai đều rỗng hoặc toàn khoảng trắng, đánh dấu `ERR_DEMAND_QUERY_EMPTY`.
- **Tùy chọn B:** Luôn nối cả hai: `f"{description} {stem(asset_file)}"`.
- **Phân tích đánh đổi:** Tùy chọn A phản ánh chính xác chủ đích ngữ nghĩa của đạo diễn; việc nối cả hai (Tùy chọn B) thường làm loãng từ khóa với những tên file legacy ngẫu nhiên (ví dụ: `do_mixi_ao_that_day`), làm giảm điểm khớp của matcher.

#### Câu hỏi 2: Chính sách xử lý `asset_type="green_screen"` và các định dạng phi chuẩn
- **Tùy chọn A (Khuyến nghị):** Đánh dấu là `status = "unsupported_category"`, không gửi request sang Matcher, giữ nguyên trong báo cáo sidecar kèm giải thích để người dùng nhận diện.
- **Tùy chọn B:** Ép kiểu về `"overlays"` hoặc `"memes"`.
- **Phân tích đánh đổi:** Tùy chọn A giữ vững cam kết "Không gộp bừa". Rough-cut tự động chưa có công cụ Fusion Chroma Keyer, do đó việc trả về một video nền xanh không thể dùng ngay sẽ gây lỗi bố cục trên timeline.

#### Câu hỏi 3: Ràng buộc thời lượng nguồn (`min_duration` / `max_duration`)
- **Tùy chọn A (Khuyến nghị):** Giữ `desired_duration_sec` là metadata bố cục dòng thời gian trong sidecar; đặt `min_duration = None` và `max_duration = None` trong request tìm kiếm.
- **Tùy chọn B:** Gán `max_duration = desired_duration_sec * 1.5` để ép tệp trong kho không được quá dài.
- **Phân tích đánh đổi:** Tùy chọn A tối đa hóa khả năng tìm kiếm (recall). Hầu hết tài nguyên B-Roll trong kho được quay với độ dài 10 - 20s để người dựng tự cắt đoạn ưng ý. Áp dụng Tùy chọn B sẽ loại bỏ phần lớn tài nguyên chất lượng cao trong kho.

---

## 9. Tổng Kết Ràng Buộc & Bàn Giao

- **Trạng thái tài liệu:** Đã được Tech Lead Codex phê duyệt tại `docs/architecture/06B3-codex-decisions.md` và hoàn tất thực thi lát cắt 1 (`G06-06B3-I1`, `G06-06B3-I1-R1` & `G06-06B3-I1-R2`). Thiết kế chi tiết cho lát cắt 2 đã được lập tại `docs/architecture/06B3-I2-worker-review-design.md`.
- **Cam kết tác động:**
  * Đúng 2 production modules MỚI: `src/core/contracts/plan_demand_contracts.py` và `src/core/plan_demand_adapter.py`.
  * Đúng 1 test module MỚI: `tests/core/test_plan_demand_adapter.py` (34 tests pass 100%).
  * Finite test suite: 131/131 tests pass 100% trong 0.61s (Exit code 0).
  * 0 edits ngoài scope (không sửa 06B1/06B2, StorageManager, UI, story_copilot, SourceResolver, ActionGuard).
  * 0 Resolve API calls, 0 network/Drive downloads, 0 ZIP restoration.
  * Toàn bộ tệp dirty/untracked và ngôn ngữ tiếng Việt được bảo toàn 100%.
