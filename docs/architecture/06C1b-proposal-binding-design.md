# Thiết Kế Bounded Proposal Snapshot & Group Approval Binding (G06-06C1b-D / 06C1b1-I)

> **CẤP ĐỘ THIẾT KẾ:** Worker `HIGH` — ĐÃ ĐỒNG BỘ VỚI QUYẾT ĐỊNH CODEX TẠI [06C1b-codex-decisions.md](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/docs/architecture/06C1b-codex-decisions.md).  
> **Hiện trạng triển khai:** Module pure contracts `src/core/contracts/edit_plan_proposal_contracts.py` đã hoàn tất (49 tests PASS bao gồm hồi quy C1a).  
> **Kế thừa & Phục vụ:** Kế thừa trực tiếp `CreativeContext` và `ReferenceItem` từ `G06-06C1a-R1` (đã được Codex nghiệm thu tại [06C1a-gate-acceptance.md](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/docs/architecture/06C1a-gate-acceptance.md)).  
> **Mục tiêu:** Thiết lập hợp đồng dữ liệu cho bản đề xuất trọn gói (`EditPlanProposal`) bảo toàn toàn diện metadata Slot 1, Slot 2 và nguồn gốc, cùng cơ chế duyệt chung một lượt ứng dụng làm chủ theo các quyết định sản phẩm [D01, D02, D03](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/docs/architecture/06C-product-decisions.md).

---

## 1. Bản Đồ Truy Vết Thực Tế Codebase (Source-Grounded Producer / Consumer Trace)

Nhằm đảm bảo thiết kế gắn kết chính xác với kiến trúc hiện hữu và không phát sinh các lớp trừu tượng ảo, bảng dưới đây phân loại và định vị chi tiết các thành phần thực tế (`[CODE_VERIFIED]`) so với các thành phần được đề xuất ràng buộc (`[PROPOSED]`):

| Ký hiệu / Đối tượng | Tệp mã nguồn & Dòng | Trạng thái | Vai trò trong luồng Đề xuất & Duyệt | Khoảng trống cần kết nối sau (Deferred Wiring) |
| :--- | :--- | :--- | :--- | :--- |
| [`ReviewPage`](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/ui/director/pages/review_page.py#L382) | `src/ui/director/pages/review_page.py:382` | `[CODE_VERIFIED]` | UI Controller trung tâm quản lý tiếp nhận JSON, hiển thị Slot 1 / Slot 2, drawer cố vấn tài nguyên | Chưa có nút/sự kiện bấm "Duyệt Kịch Bản" để tạo approval |
| `_video_plan_data` | `src/ui/director/pages/review_page.py:407, 5584` | `[CODE_VERIFIED]` | Dữ liệu kịch bản Video Plan (Slot 1) lưu trong bộ nhớ giao diện | Dạng dict tự do, đã được chuẩn hóa thành `EditPlanProposal.video_plan` |
| `_kinetic_plan_data` | `src/ui/director/pages/review_page.py:5568, 5604` | `[CODE_VERIFIED]` | Dữ liệu kịch bản Chữ Kinetic (Slot 2) lưu trong bộ nhớ giao diện | Dạng dict tự do, đã được chuẩn hóa thành `EditPlanProposal.kinetic_plan` |
| `_apply_json_data()` | `src/ui/director/pages/review_page.py:5479-5620` | `[CODE_VERIFIED]` | Phân loại và nạp JSON vào Slot 1, Slot 2 hoặc Dual Plan, kiểm tra `manifest_id` | Chưa đóng gói thành `EditPlanProposal` trong luồng UI |
| `_last_good_matching_sidecar` | `src/ui/director/pages/review_page.py:2771` | `[CODE_VERIFIED]` | Lưu giữ kết quả tra cứu cố vấn tài nguyên ngoại tuyến (`AssetMatchingSidecar` từ 06B) | Cung cấp candidates qua `entry.result.candidates` |
| `AssetAdvisoryDrawer` | `src/ui/director/pages/review_page.py:1143` | `[CODE_VERIFIED]` | Widget hiển thị danh sách tài nguyên khớp và cảnh báo thiếu | Hiển thị danh sách, chưa kết nối trực tiếp vào giao diện duyệt chung D01/D02 |
| [`DirectorConfig`](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/ui/director/director_state.py#L58) | `src/ui/director/director_state.py:58-135` | `[CODE_VERIFIED]` | Cấu hình toàn cục dự án (`style:68`, `ai_engine_mode:97`, `video_plan_data:106`) | Cần bổ sung trường `creative_context: Optional[Dict]` và `approval_record: Optional[Dict]` |
| `_get_current_director_context()` | `src/ui/director/director_window.py:785-870` | `[CODE_VERIFIED]` | Gom ngữ cảnh giao diện phục vụ tạo prompt AI | Cần gom các trường `free_brief`, `references` thành `CreativeContext` chuẩn |
| [`PromptPreparationRequest`](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/ui/director/workers/prompt_preparation_worker.py#L1052) | `src/ui/director/workers/prompt_preparation_worker.py:1052` | `[CODE_VERIFIED]` | Value object bất biến chuẩn bị prompt AI, trần `max_prompt_bytes=32768` | Cần tiếp nhận `creative_context` đã chuẩn hóa từ C1a |
| [`CopilotDirectorPlan`](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/core/story_copilot.py#L51) | `src/core/story_copilot.py:51-63` | `[CODE_VERIFIED]` | Cấu trúc raw plan do AI đề xuất (`timeline_segments:57`, `broll_inserts:58`, `sfx_inserts:59`) | Là dữ liệu AI thô, không có thẩm quyền phê duyệt hay thực thi |
| [`ClipManifest`](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/core/clip_manifest.py#L93) | `src/core/clip_manifest.py:60-105` | `[CODE_VERIFIED]` | SSOT quản lý danh sách clip gốc (`manifest_id:95`, `ClipEntry.current_path:71`) | Đã sẵn sàng làm mỏ neo xác thực footage |
| [`CreativeContext`](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/core/contracts/edit_plan_contracts.py#L230) | `src/core/contracts/edit_plan_contracts.py:230` | `[CODE_VERIFIED]` | Ngữ cảnh sáng tạo bất biến: brief tự do, preset, rules, reference local (C1a) | Sẵn sàng làm đầu vào bất biến cho `EditPlanProposal` |
| `EditPlanProposal` | `src/core/contracts/edit_plan_proposal_contracts.py` | `[CODE_VERIFIED]` | Snapshot bất biến đóng gói toàn bộ Slot 1, Slot 2, nguồn, brief, gán tài nguyên và omission | First consumer sử dụng `CreativeContext`, phục vụ bước duyệt D01/D02 |
| `AppOwnedApprovalRecord` | `src/core/contracts/` (lát cắt sau) | `[PROPOSED_DEFERRED]` | Bản ghi phê duyệt do Desktop App tạo ra khi user bấm nút Duyệt qua UI | Hoãn lại ở lát cắt tiếp theo theo chỉ đạo Codex |

---

## 2. Hợp Đồng Bản Đề Xuất Bất Biến & Bị Chặn (`EditPlanProposal`)

### 2.1. Cấu Trúc Snapshot Chuẩn Tắc 8 Trường (Không Mất Metadata)
Theo hiệu đính của Codex tại `06C1b-codex-decisions.md`, việc rút gọn kịch bản thành một vài DTO phân đoạn nhỏ làm mất các trường quan trọng như `global_hook`, `strategy_summary`, `target_platform`, `broll_inserts`, `sfx_inserts` của Slot 1 và các trang, từ, kiểu dáng của Slot 2. Do đó, snapshot đề xuất được chuẩn hóa thành cấu trúc bất biến gồm đúng 8 trường:

```python
# [IMPLEMENTED DATA CONTRACT] — src/core/contracts/edit_plan_proposal_contracts.py

@dataclass(frozen=True)
class EditPlanProposal:
    """Snapshot bản đề xuất kịch bản trọn gói (Immutable Value Object)."""
    proposal_id: str
    manifest_id: str
    creative_context: CreativeContext
    source_binding: Mapping[str, object]
    video_plan: Mapping[str, object]
    kinetic_plan: Optional[Mapping[str, object]] = None
    proposed_asset_assignments: Tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    proposed_omissions: Tuple[Mapping[str, object], ...] = field(default_factory=tuple)
```

### 2.2. Các Hàm Công Khai (Public APIs)
1. `normalize_edit_plan_proposal(raw: Union[Mapping[str, object], EditPlanProposal]) -> EditPlanProposal`
2. `edit_plan_proposal_to_dict(proposal: EditPlanProposal) -> dict[str, object]`
3. `compute_proposal_revision(proposal: EditPlanProposal) -> str`

> [!IMPORTANT]
> **Tách biệt Revision khỏi Root DTO (Không Self-Hashing):**
> Root DTO `EditPlanProposal` không chứa trường `proposal_revision` hay `approval_record`. Mã băm revision được tính toán độc lập qua hàm `compute_proposal_revision(proposal)` trên dữ liệu canonical JSON UTF-8 của toàn bộ 8 trường.

### 2.3. Quy Chuẩn Định Dạng JSON Chuẩn Tắc & Trần Dung Lượng
1. **Biểu diễn số học không mất mát (Lossless Numeric Representation):** Thời gian `src_in_sec`, `src_out_sec`, `timeline_sec`, `duration_sec` được lưu giữ dưới dạng số thực hoặc số nguyên bản (`int` hoặc `float`). Cấm làm tròn 6 chữ số thập phân (`round(..., 6)`).
2. **Tuần tự hóa chuẩn tắc (Canonical JSON Serialization):**
   - Sắp xếp khóa từ điển theo thứ tự bảng mã: `sort_keys=True`.
   - Phân tách không khoảng trắng thừa: `separators=(",", ":")`.
   - Giữ nguyên tiếng Việt có dấu: `ensure_ascii=False`.
   - Loại trừ hoàn toàn `NaN` và `Infinity`: `allow_nan=False`.
3. **Giới hạn số lượng & Trần kích thước bộ nhớ (Intake Bounds):**
   - `MAX_SOURCE_CLIPS: int = 4096`
   - `MAX_TIMELINE_SEGMENTS: int = 256`
   - `MAX_RAW_INSERTS: int = 4096`
   - `MAX_PROPOSED_ASSIGNMENTS: int = 128`
   - `MAX_PROPOSED_OMISSIONS: int = 128`
   - `MAX_PROPOSAL_SNAPSHOT_BYTES: int = 524288` (512 KiB UTF-8).
   - `MAX_JSON_DEPTH: int = 32`
   - `MAX_JSON_NODES: int = 100000`

---

## 3. Quản Lý Tài Nguyên Đề Xuất & Ranh Giới Omission (D01 & D02)

### 3.1. Tiếp Nhận Tài Nguyên Từ 06B Sidecar & Tách Biệt Định Danh
1. **Tiếp nhận ứng viên hợp lệ:** Đọc ứng viên từ `entry.result.candidates` trong `AssetMatchingSidecar` (06B). Ứng dụng được phép tự động chọn ứng viên phù hợp nhất để đưa vào `proposed_asset_assignments`.
2. **Xuất xứ tài nguyên chuẩn tắc (Canonical Source Kinds & Provenance):**
   - `source_kind` chỉ chấp nhận một trong bốn giá trị: `"managed"`, `"linked"`, `"builtin"`, `"generated"`. Tuyệt đối không tự bịa enum `"user"` hay `"catalog"`.
   - Với `"managed"` và `"linked"`, trường `origin_library_id` là **BẮT BUỘC** (không được để trống) để phân biệt các kho tài nguyên khác nhau có cùng `asset_id`.
   - Các trường provenance bổ trợ `source_id` và `pack_id` khi xuất hiện trong assignment chỉ được mang kiểu non-blank `str` hoặc `None`; cấm tuyệt đối ép kiểu từ `[]`, `int`, `bool`, `{}` hoặc chuỗi rỗng/chỉ chứa khoảng trắng (phải fail-closed với `ValueError`).
   - Trạng thái `status` trong assignment nếu có chỉ được mang giá trị `"PROPOSED"`.

### 3.2. Ranh Giới Khối Dữ Liệu Slot 1 & Giới Hạn Cấu Trúc (Inserts & Metadata Fidelity)
1. **Type Gate cho Raw Inserts:**
   - Trong `video_plan`, nếu có trường `broll_inserts` hoặc `sfx_inserts`, trường đó bắt buộc phải có kiểu `list` hoặc `tuple`. Bất kỳ kiểu nào khác (`False`, `None`, `dict`, `str`, `range`...) đều bị từ chối bằng `ValueError`.
   - Nếu trường thiếu (missing), coi như độ dài 0 để tính trần nhưng **giữ nguyên raw payload** (không tự tiện chèn default key).
   - Tổng độ dài: $len(broll\_inserts) + len(sfx\_inserts) \le 4096$. Tràn trần bị từ chối ngay trước khi duyệt phần tử con (0 iterations).
2. **Trung thực siêu dữ liệu JSON chung (Generic JSON Fidelity):**
   - Các khóa trong generic mapping cho phép chuỗi rỗng `""` và chuỗi khoảng trắng (chuẩn JSON RFC 8259), chỉ cấm non-str, ký tự NUL (`\0`) và lone surrogates UTF-8.
   - Các khóa nghiệp vụ (root keys, `manifest_id`, `clip_id`, `demand_id`, `asset_id`...) vẫn nghiêm ngặt cấm chuỗi rỗng.
3. **Pre-check Node Budget trước khi lặp:**
   - Trước khi lặp container (`Mapping` hoặc `Sequence`), kiểm tra $count + len(container) \le MAX\_JSON\_NODES$ (100,000). Nếu chắc chắn tràn thì ném `ERR_JSON_NODES_EXCEEDED` lập tức, bảo đảm 0 iterations.
   - Các node chia sẻ trong DAG được tính đầy đủ theo số lần xuất hiện thực tế khi serialize.

### 3.3. Ranh Giới Nghiêm Ngặt Của Đề Xuất Bỏ Qua (Rough-Cut Omission Boundary — D02)
- **Phạm vi cho phép omission:** Chỉ áp dụng cho các nhu cầu B-roll và SFX bổ trợ mà kho tài nguyên hiện tại chưa đáp ứng được (`slot` bắt buộc là `"broll"` hoặc `"sfx"`).
- **Vùng cấm tuyệt đối của omission (Non-Omissible Constraints):**
  1. Thiếu hoặc sai lệch footage chính (`clip_id` không có trong `source_binding.clips`).
  2. Vi phạm ranh giới thời lượng clip ($start\_sec < 0$ hoặc $start\_sec \ge end\_sec$).
  3. Lệch `manifest_id` giữa root, `source_binding`, `video_plan`, và `kinetic_plan`.
  4. Vi phạm các quy tắc bắt buộc của khách hàng (`must_keep_rules` trong `CreativeContext`).
- **Tính duy nhất và tách biệt của Demand ID:**
  - `demand_id` là duy nhất trong danh sách assignments.
  - `demand_id` là duy nhất trong danh sách omissions.
  - Tập hợp `demand_id` trong assignments và omissions **HOÀN TOÀN TÁCH BIỆT (DISJOINT)**. Một nhu cầu không thể vừa được gán vừa bị bỏ qua!

---

### 3.4. Chuẩn Hóa Định Dạng Timestamp Phê Duyệt (ISO-8601 formatrecordv1)
Theo kết quả nghiệm thu và hiệu đính của Codex (G06-06C1b2-I / G06-06C1b2-R1), trường `approved_at_iso` trong `AppOwnedApprovalRecord` được siết chặt qua cơ chế kiểm tra hai lớp độc lập (Lexical Fullmatch + Calendar Validation), không phụ thuộc vào đồng hồ hệ thống:
1. **Lớp 1 — Lexical Pattern (`formatrecordv1`):**
   ```regex
   ^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$
   ```
   - Bắt buộc phân cách ngày và giờ bằng chữ `T`.
   - Phần phân số giây (fractional seconds) là tùy chọn: nếu có dấu `.` thì bắt buộc theo sau bởi đúng 1 đến 6 chữ số ASCII (`(?:\.\d{1,6})?`). Cấm tuyệt đối > 6 chữ số thập phân (ví dụ `.1234567Z` bị từ chối) hoặc dấu chấm lơ lửng không chữ số (`.` bị từ chối).
   - Timezone bắt buộc phải là `Z` hoặc `[+-]HH:MM` với khoảng giờ `00..23` và phút `00..59`. Cấm tuyệt đối timezone có giây (`+07:00:30`), thiếu dấu hai chấm (`+0700`), thiếu phút (`+07`) hoặc phút ngoài biên (`+07:60`).
   - Cấm khoảng trắng ở đầu hoặc cuối chuỗi (không tự ý trim/coercion).
2. **Lớp 2 — Calendar & Time Validation (`datetime.fromisoformat`):**
   - Xác thực ngày tháng có thật trong lịch (loại trừ ngày 30/02, 31/04...) và giờ hợp lệ.
   - Loại trừ hoàn toàn naive timestamp (không có timezone).
3. **Phạm vi bảo vệ đồng bộ:**
   - Áp dụng thống nhất cho Direct Constructor, Factory `create_app_owned_approval_record`, Export Revalidation `approval_record_to_dict`, và trả về `False` fail-closed an toàn trong `verify_proposal_approval_binding`.
   - Thay đổi `approval_id` hoặc `approved_at_iso` hợp lệ không làm mất binding (`True`) nếu toàn bộ nội dung proposal không thay đổi.

---

## 4. Vòng Đời Trạng Thái & Invalidation (State Lifecycle)

```
        ┌────────────────────────────────────────────────────────┐
        │                      PROPOSED                          │
        │ (Kịch bản mới tạo/chỉnh sửa, 0 quyền thi công Resolve) │
        └───────────────────────────┬────────────────────────────┘
                                    │ Người dùng bấm "Duyệt Kịch Bản"
                                    ▼
        ┌────────────────────────────────────────────────────────┐
        │                      APPROVED                          │
        │   (Có AppOwnedApprovalRecord khớp proposal_revision)   │
        └───────────────────────────┬────────────────────────────┘
                                    │ Phát hiện bất kỳ thay đổi nào
                                    ▼
        ┌────────────────────────────────────────────────────────┐
        │                       STALE                            │
        │ (Approval bị hủy, kịch bản quay về trạng thái PROPOSED)│
        └────────────────────────────────────────────────────────┘
```

#### Bảng Quy Tắc Invalidation:
| Sự kiện phát sinh | Hành vi kiểm tra băm | Trạng thái kịch bản | Ghi chú & Ranh giới bảo vệ |
| :--- | :--- | :--- | :--- |
| **Sửa Brief / Preset / Ref** | `creative_context` thay đổi $\rightarrow$ `proposal_revision` đổi | `APPROVED` $\rightarrow$ `STALE` | Hủy bỏ approval; yêu cầu người dùng duyệt lại |
| **Sửa điểm cắt A-Roll** | `timeline_segments` đổi $\rightarrow$ `proposal_revision` đổi | `APPROVED` $\rightarrow$ `STALE` | Bất kỳ thay đổi nào về `start_sec`, `end_sec`, thứ tự đều làm stale |
| **Đổi Footage / Manifest** | `manifest_id` hoặc `source_binding` thay đổi | `APPROVED` $\rightarrow$ `STALE` | Ngăn chặn áp dụng kịch bản cũ lên bộ footage mới |
| **Đổi Gán Tài Nguyên / Omission** | `proposed_asset_assignments` hoặc `omissions` đổi | `APPROVED` $\rightarrow$ `STALE` | Thay đổi tài nguyên đòi hỏi duyệt lại một lượt |
| **Sửa Slot 2 Kinetic kèm theo** | `kinetic_plan` thay đổi | `APPROVED` $\rightarrow$ `STALE` | Nếu Slot 2 nằm trong cùng proposal thì cũng làm mất hiệu lực |
| **Nạp lại dữ liệu y hệt (No-op)** | `proposal_revision` giống 100% | Giữ nguyên `APPROVED` | Không làm mất trạng thái duyệt nếu nội dung không đổi |
| **Nạp JSON lỗi / Mutation sai** | Schema validator ném lỗi | Giữ nguyên `last-good` | Fail-closed: Từ chối mutation, bảo toàn kịch bản hợp lệ trước đó |

---

## 5. Ranh Giới D03, Tách Biệt Tham Khảo & Biểu Diễn Thời Gian

### 5.1. Tách Biệt Tuyệt Đối Tệp Tham Khảo (Reference-Only Isolation)
Kế thừa nguyên vẹn hợp đồng `ReferenceItem` từ `G06-06C1a-R1`:
- Mọi tệp có `role="reference_only"` **TUYỆT ĐỐI KHÔNG ĐƯỢC PHÉP** xuất hiện trong danh sách `source_binding.clips`, `video_plan.timeline_segments`, hoặc `proposed_asset_assignments`.
- Trường `reference_urls` chỉ thuần túy lưu trữ chuỗi URL đối chiếu dữ liệu cho người dùng. **Zero network calls, zero auto-crawl, zero download**.

### 5.2. Ranh Giới Biểu Diễn Thời Gian
- Thời gian trong snapshot proposal được giữ nguyên vẹn dưới dạng số thực (`float`/`int` giây). Không làm tròn 6 chữ số thập phân.
- Lượng tử hóa khung hình Công thức B (Source-Frame-First) thuộc về tầng adapter tiếp theo.
- Snapshot proposal tuyệt đối **KHÔNG được gọi là `METADATA_VALID_READY_FOR_PREFLIGHT`**.

---

## 6. Bốn Fixture Chuẩn Tắc & Ma Trận Nghiệm Thu Hữu Hạn

### 6.1. Bốn Fixture JSON Chuẩn Tắc (Đầy Đủ Snapshot)

#### Fixture 1: `proposal_unapproved.json`
*Bản đề xuất hợp lệ đầy đủ 8 trường snapshot, bảo toàn Slot 1, Slot 2, source_binding, assignments và omissions:*
```json
{
  "proposal_id": "prop_20261029_001",
  "manifest_id": "m_test01",
  "creative_context": {
    "free_brief": "Video review 60s ẩm thực vui tươi",
    "style_preset": "cute_vlog",
    "target_duration_sec": 60.0,
    "must_keep_rules": ["Giữ đoạn mở đầu"],
    "must_not_rules": ["cấm meme phản cảm"],
    "reference_items": [
      {
        "reference_id": "ref_01",
        "local_path": "/media/refs/sample_ref.mp4",
        "media_type": "video",
        "role": "reference_only",
        "user_note": "Học nhịp dựng nhanh"
      }
    ],
    "reference_urls": ["https://example.com/ref_video"]
  },
  "source_binding": {
    "manifest_id": "m_test01",
    "clips": [
      {
        "clip_id": "C01",
        "file_name": "intro.mp4",
        "current_path": "/media/footage/intro.mp4",
        "content_hash": "hash_c01_abc123",
        "duration_sec": 10.5,
        "fps": 30.0
      }
    ]
  },
  "video_plan": {
    "manifest_id": "m_test01",
    "strategy_summary": "Chiến lược dựng 60s ẩm thực",
    "target_platform": "TikTok / Shorts",
    "global_hook": {
      "clip_id": "C01",
      "start_sec": 0.0,
      "end_sec": 3.0,
      "hook_title": "Món ăn độc lạ"
    },
    "timeline_segments": [
      {
        "segment_index": 0,
        "clip_id": "C01",
        "start_sec": 0.0,
        "end_sec": 5.0,
        "action": "keep",
        "speed": 1.0,
        "role": "hook",
        "note": "Giữ câu chào"
      }
    ],
    "broll_inserts": [],
    "sfx_inserts": []
  },
  "kinetic_plan": {
    "manifest_id": "m_test01",
    "preset_style": "karaoke_pop",
    "lines": [
      {
        "line_index": 0,
        "timeline_sec": 0.5,
        "duration_sec": 2.5,
        "text": "XIN CHÀO MỌI NGƯỜI",
        "words": [
          {"word": "XIN", "start_sec": 0.5, "end_sec": 0.8},
          {"word": "CHÀO", "start_sec": 0.8, "end_sec": 1.2}
        ]
      }
    ]
  },
  "proposed_asset_assignments": [
    {
      "slot": "sfx",
      "demand_id": "dem_sfx_01",
      "timeline_sec": 0.5,
      "duration_sec": 1.0,
      "asset_id": "sfx_pop_cute",
      "source_kind": "managed",
      "origin_library_id": "lib_default_sfx",
      "pack_id": "pack_vlog_01",
      "status": "PROPOSED"
    }
  ],
  "proposed_omissions": [
    {
      "slot": "broll",
      "demand_id": "dem_broll_01",
      "timeline_sec": 2.0,
      "duration_sec": 2.0,
      "reason": "ERR_CATALOG_EMPTY",
      "warning_message": "Không tìm thấy video bánh nướng trong kho"
    }
  ]
}
```

#### Fixture 2: `proposal_user_approved.json` (Schematic Envelope)
*Kịch bản đề xuất gắn kết với Hồ sơ Phê duyệt của ứng dụng (AppOwnedApprovalRecord — 6 trường chuẩn tắc):*
```json
{
  "proposal": "<full_EditPlanProposal_payload>",
  "approval_record": {
    "approval_id": "appr_20261029_999",
    "proposal_id": "prop_20261029_001",
    "proposal_revision": "197caa9fb5518e47bf9c33e729278570299e7834cc6aa545121401f88191978b",
    "approved_at_iso": "2026-10-29T20:30:00Z",
    "accepted_omission_ids": [
      "dem_broll_01"
    ],
    "approval_scope": "ENTIRE_PROPOSAL"
  },
  "verdict": "BOUNDED_PROPOSAL_APPROVED"
}
```

#### Fixture 3: `stale_approval_after_edit.json` (Schematic Invalidation)
*Biểu diễn kịch bản bị lệch mã băm SHA-256 sau khi sửa điểm cắt phân đoạn hoặc danh sách omission:*
```json
{
  "original_proposal_revision": "197caa9fb5518e47bf9c33e729278570299e7834cc6aa545121401f88191978b",
  "approval_record": {
    "approval_id": "appr_20261029_999",
    "proposal_id": "prop_20261029_001",
    "proposal_revision": "197caa9fb5518e47bf9c33e729278570299e7834cc6aa545121401f88191978b",
    "approved_at_iso": "2026-10-29T20:30:00Z",
    "accepted_omission_ids": [
      "dem_broll_01"
    ],
    "approval_scope": "ENTIRE_PROPOSAL"
  },
  "recomputed_proposal_revision": "9876543210fedcba9876543210fedcba0123456789abcdef0123456789abcdef",
  "verdict": "STALE_APPROVAL_INVALIDATED"
}
```

#### Fixture 4: `rejected_ai_approval_and_ref_leak.json`
*JSON vi phạm: Chứa cờ AI `user_approved: true` và cố tình đưa `ref_01` vào source clips:*
```json
{
  "proposal_id": "prop_forged_001",
  "manifest_id": "m_test01",
  "user_approved": true,
  "source_binding": {
    "manifest_id": "m_test01",
    "clips": [
      {"clip_id": "ref_01", "current_path": "/media/refs/sample_ref.mp4"}
    ]
  },
  "expected_rejections": [
    "ERR_UNKNOWN_KEY: raw mapping chứa unknown keys: ['user_approved']",
    "ERR_REFERENCE_LEAK: clip_id 'ref_01' là tài liệu tham khảo (reference_only)"
  ]
}
```

---

### 6.2. Ma Trận Nghiệm Thu Hữu Hạn Đã Được Kiểm Thử TDD (69 Tests Pass — G06-06C1b2-R1)

| Nhóm Kiểm Thử | Tình huống kiểm thử | Kết quả kỳ vọng | Kết quả thực tế |
| :--- | :--- | :--- | :--- |
| **Positive Control** | Snapshot proposal hợp lệ đầy đủ 8 trường | Tạo thành công `EditPlanProposal`, tính đúng `proposal_revision` | **PASS** (test_successful_proposal_creation_and_roundtrip) |
| **Sensitivity** | Thay đổi bất kỳ trường nào (brief, clip path, hook, speed, provenance) | `compute_proposal_revision()` thay đổi | **PASS** (test_recomputes_digest_when_any_content_changes) |
| **AI Forgery Check** | Cờ `user_approved: true` hoặc `approval_record` trong mapping | Ném lỗi `ERR_UNKNOWN_KEY` | **PASS** (test_rejects_unknown_root_keys_and_ai_forgery) |
| **Mixed Keys** | Mapping chứa khóa hỗn hợp int và str | Ném lỗi `ERR_UNKNOWN_KEY`, zero `TypeError` | **PASS** (test_mixed_unknown_keys_type_safe) |
| **Immutability** | Can thiệp vào `MappingProxyType` hoặc raw input | `TypeError` hoặc không đổi trạng thái proposal | **PASS** (test_ownership_and_defensive_copies) |
| **DAG vs Cycle** | Chia sẻ node dictionary (DAG) vs vòng lặp thực sự | DAG chấp nhận, cycle ném `ERR_CYCLE_DETECTED` | **PASS** (test_dag_sharing_nodes_allowed_cycles_rejected) |
| **Depth Bounds** | Cây lồng nhau sâu > 32 tầng | Ném lỗi `ERR_JSON_DEPTH_EXCEEDED` | **PASS** (test_max_depth_exceeded) |
| **Manifest Gate** | Lệch manifest giữa root, source_binding, video_plan, kinetic_plan | Ném lỗi `ERR_MANIFEST_MISMATCH` | **PASS** (test_manifest_id_mismatch_rejected) |
| **Clip Integrity** | `clip_id` trong segments không có trong source_binding | Ném lỗi `ERR_UNKNOWN_CLIP_ID` | **PASS** (test_unknown_clip_id_in_segments_rejected) |
| **Timeline Bounds** | $start\_sec < 0$, $start\_sec \ge end\_sec$, $speed \le 0$ | Ném lỗi `ERR_INVALID_TIMELINE_BOUNDS` / `ERR_INVALID_SPEED` | **PASS** (test_timeline_segments_bounds_validation) |
| **Source Kinds** | `source_kind="user"`, thiếu `origin_library_id` cho managed/linked | Ném lỗi `ERR_INVALID_SOURCE_KIND` / `ERR_MISSING_ORIGIN_LIBRARY_ID` | **PASS** (test_source_kind_and_origin_library_id) |
| **Demand Disjoint** | Trùng lặp demand_id hoặc giao thoa giữa assign và omit | Ném lỗi `ERR_DUPLICATE_DEMAND_ID` / `ERR_DEMAND_ASSIGNED_AND_OMITTED` | **PASS** (test_demand_id_uniqueness_and_disjointness) |
| **Ref Isolation** | Tệp `reference_only` rò rỉ vào footage hoặc assignment | Ném lỗi `ERR_REFERENCE_LEAK` | **PASS** (test_reference_item_cannot_leak_to_footage_or_assignments) |
| **Precheck Len** | Vượt trần counts trên `CountedList` | Ném lỗi `ERR_COUNT_OVERFLOW` với đúng 0 iterations | **PASS** (test_count_bounds_precheck_0_iterations) |
| **Byte Budget** | Đúng 524,288 bytes UTF-8 chấp nhận, 524,289 bytes từ chối | Equality accepted, +1 rejected với `ERR_BYTE_BUDGET_OVERFLOW` | **PASS** (test_exact_byte_budget_equality_accepted_overflow_rejected) |
| **Purity Spies** | Kiểm tra `open`, `stat`, `socket` | Zero IO, zero network | **PASS** (test_purity_zero_io_and_zero_network) |
| **R1 Inserts Gate** | `broll_inserts`/`sfx_inserts` sai kiểu (`False`, `None`, `dict`), missing, trần $\le 4096$ | Bắt buộc list/tuple; missing giữ nguyên raw; 4097 rejected 0 iterations | **PASS** (test_video_plan_inserts_type_gates_and_bounds) |
| **R1 Provenance** | `source_id`/`pack_id` sai kiểu (`[]`, `23`, `bool`, `""`) vs valid/None | Fail-closed ValueError; chấp nhận non-blank str hoặc None; nhạy hash | **PASS** (test_assignment_provenance_source_id_and_pack_id) |
| **R1 Generic Keys** | Khóa `""`, khoảng trắng, tiếng Việt, quotes trong metadata chung | Chấp nhận chuỗi rỗng/khoảng trắng theo JSON spec; domain IDs vẫn cấm rỗng | **PASS** (test_generic_metadata_keys_fidelity_and_empty_keys) |
| **R1 Node Precheck** | `CountedList` / `CountedMapping` > 100,000 nodes trong metadata | Ném `ERR_JSON_NODES_EXCEEDED` lập tức với đúng 0 children iterations | **PASS** (test_generic_node_container_precheck_0_iterations) |
| **R1 Node Equality** | Monkeypatch `MAX_JSON_NODES` bằng đúng 87 vs 86 | Equality accepted, overflow rejected tại đúng tầng node | **PASS** (test_node_cap_equality_and_overflow_with_precheck) |
| **Approval Factory** | Tạo `AppOwnedApprovalRecord` từ proposal hợp lệ, zero/multiple omissions | Đúng 6 fields, trích xuất demand IDs theo thứ tự, revision tự động, export roundtrip | **PASS** (test_create_app_owned_approval_record_*) |
| **Approval Bounds** | Revision 64-hex lowercase, ISO timestamp formatrecordv1 (regex + calendar), omissions $\le 128$ (precheck 0 iter) | Fail-closed ValueError; chặn 7-digit fraction, timezone giây/thiếu colon/thiếu phút, naive/date-only/whitespace/duplicate/overflow | **PASS** (test_approval_record_id_and_scope_validation, test_approved_at_iso_*, test_accepted_omission_ids_*) |
| **Approval Corrupt** | Corrupt timestamp qua `object.__setattr__` | Export ném `ValueError`, `verify_proposal_approval_binding` trả về `False` fail-closed | **PASS** (test_export_revalidation_and_binding_fail_closed_on_corrupt_timestamp) |
| **Approval Metadata** | Thay đổi `approval_id` hoặc `approved_at_iso` hợp lệ | Binding vẫn giữ `True` vì nội dung proposal bất biến | **PASS** (test_binding_persists_across_valid_alternative_approval_metadata) |
| **Approval Budget** | Đúng 528,384 bytes UTF-8 chấp nhận, +1 byte từ chối | Equality accepted, +1 rejected với `ERR_BYTE_BUDGET_OVERFLOW` | **PASS** (test_approval_record_byte_budget_equality_accepted_overflow_rejected) |
| **Binding Verify** | So khớp chính xác tuyệt đối proposal vs approval | True khi khớp; False có kiểm soát khi stale (sửa brief/preset/cuts/hook/words/assign/omits) | **PASS** (test_verify_proposal_approval_binding_*) |
| **Approval Purity** | Spy mock open, stat, socket, datetime.now, time.time | Zero IO, zero network, zero system clock | **PASS** (test_purity_zero_io_network_and_clock) |

---

## 7. Lộ Trình Kế Tiếp: `G06-06C2` & Các Lát Cắt Tiếp Theo

- **Lát cắt `G06-06C1b1` [HOÀN TẤT & ACCEPTED]:** Pure Snapshot contracts `EditPlanProposal`, canonical JSON, bounds, hardening gates và deterministic revision hashing.
- **Lát cắt hiện tại `G06-06C1b2-I` [HOÀN TẤT]:** Pure App-Owned Approval contracts `AppOwnedApprovalRecord`, factory, fresh export, 528 KiB budget và strict binding verification `verify_proposal_approval_binding()`.
- **Lát cắt `G06-06C2` [PROPOSED_DEFERRED]:** Adapter hình học và lượng tử hóa khung hình Công thức B (Source-Frame-First) cho video CFR.
- **Lát cắt `G06-06C3` [PROPOSED_DEFERRED]:** Giao diện duyệt một lượt D01/D02 trên `ReviewPage` và sinh approval event.
- **Lát cắt `G06-06C4` / `G07` [PROPOSED_DEFERRED]:** Preflight kiểm tra ổ đĩa và sẵn sàng thi công DaVinci Resolve.
