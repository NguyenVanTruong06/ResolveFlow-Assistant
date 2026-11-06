# Thiết Kế Hình Học Kịch Bản Dựng Toàn Bản (06C2d Plan Geometry Design)

> **Tài liệu kiến trúc (Chuẩn bị theo yêu cầu G06-06C2c2-EVIDENCE-R1-THEN-06C2d-D):**  
> **Cấp độ Worker:** HIGH — Design Only (Zero Code, Zero I/O)  
> **Nhiệm vụ:** G06-06C2d-D (Part B)  
> **Ngày lập:** 2026-10-30  
> **Kế thừa bắt buộc:**  
> - Các quyết định sản phẩm bất biến: D01 (Toàn bộ đề xuất do AI lập + 1 lần người dùng duyệt trước khi dựng), D02 (Bỏ sót B-roll/SFX phải có review tường minh, cấm ẩn lỗi nguồn chính), D03 (Brief/preset/local media miễn phí, URL chỉ lưu trữ).  
> - Toán học lượng tử hóa đơn phân đoạn: Formula B (`QuantizedCFRSpan`, `quantize_cfr_video_span` từ G06-06C2a đã nghiệm thu).  
> - Hợp đồng tiêu đề luồng video: `VideoStreamHeader` (G06-06C2b đã nghiệm thu).  
> - Hợp đồng đề xuất & phê duyệt: `EditPlanProposal` (G06-06C1b1-R1), `AppOwnedApprovalRecord` (G06-06C1b2-R1).  
> - Thu thập tiêu đề cục bộ: `acquire_local_video_header` (G06-06C2c1) & Async consumer nhánh 2c `persist=False` (G06-06C2c2).  

---

## 1. Bản Đồ Truy Vết Codebase Thực Tế & Ranh Giới Ký Hiệu (Source-Grounded Trace)

Nhằm đảm bảo thiết kế tuyệt đối trung thực với hiện trạng mã nguồn, bảng dưới đây đối chiếu toàn bộ các đối tượng sản xuất (`[CODE_VERIFIED]`) và các đề xuất mở rộng (`[PROPOSED]`):

### 1.1 Bảng Đối Chiếu Thực Tế

| Đối Tượng & Ký Hiệu | Tệp & Vị Trí Mã Nguồn | Phân Loại | Hiện Trạng Thực Tế | Ranh Giới Hình Học & Khoảng Trống |
| :--- | :--- | :--- | :--- | :--- |
| `EditPlanProposal` | `src/core/contracts/edit_plan_proposal_contracts.py:22` | `[CODE_VERIFIED]` | Frozen dataclass chứa `proposal_id`, `manifest_id`, `source_binding`, `video_plan`, `kinetic_plan`, `proposed_asset_assignments`, `proposed_omissions`. | Điểm cắt trong `video_plan.timeline_segments` (`start_sec`, `end_sec`, `speed`) lưu dạng giây số thực (`float`/`int`). Chưa có tọa độ frame timeline. |
| `video_plan.timeline_segments` | `src/core/contracts/edit_plan_proposal_contracts.py:269-337` | `[CODE_VERIFIED]` | Danh sách phân đoạn: `clip_id`, `start_sec` ($\ge 0.0$), `end_sec` ($> start\_sec$), `speed` ($> 0$, tùy chọn). | Mỗi phân đoạn là một yêu cầu cắt thời gian nguồn; chưa phân định ranh giới frame nguồn nguyên bản và chưa có vị trí record cursor. |
| `video_plan.global_hook` | `src/core/contracts/edit_plan_proposal_contracts.py:255-268` | `[CODE_VERIFIED]` | Optional mapping: `clip_id`, `start_sec`, `end_sec`, `hook_title`. | Chỉ chứa thông số cắt nguồn; **chưa xác định vị trí đặt trên timeline** (là intro cut độc lập ở đầu hay reference trích xuất từ A-Roll). |
| `kinetic_plan` | `src/core/contracts/edit_plan_proposal_contracts.py:340-360` | `[CODE_VERIFIED]` | Optional mapping Slot 2 chứa `preset_style`, `lines` với danh sách `words` (`word`, `start_sec`, `end_sec`). | Mốc thời gian phụ đề tính theo giây gốc của giọng nói; chưa được lượng tử hóa theo frame timeline và chưa tính co giãn speed của video cut. |
| `AppOwnedApprovalRecord` | `src/core/contracts/edit_plan_approval_contracts.py:118` | `[CODE_VERIFIED]` | Frozen dataclass ràng buộc với `proposal_revision` SHA-256. | Hồ sơ phê duyệt một lượt do ứng dụng làm chủ. **Phê duyệt không biến siêu dữ liệu unverified thành verified**. Mọi sửa đổi kịch bản làm revision đổi $\rightarrow$ approval bị STALE. |
| `QuantizedCFRSpan` | `src/core/geometry/frame_geometry_contracts.py:144` | `[CODE_VERIFIED]` | Frozen DTO gồm 6 trường: `source_fps`, `timeline_fps`, `source_range`, `total_source_frames`, `record_duration_frames`, `applied_speed`. | Đơn vị tính toán đơn lẻ theo Formula B; chỉ áp dụng cho một phân đoạn cắt khi đã có đầy đủ bằng chứng CFR. |
| `quantize_cfr_video_span` | `src/core/geometry/frame_geometry_calculator.py:52` | `[CODE_VERIFIED]` | Hàm thuần túy lượng tử hóa Formula B (Source-Frame-First) qua `pts_to_frame` (round-half-to-even) và phân số `Fraction`. | Fail-closed nghiêm ngặt: yêu cầu `is_cfr=True`, `source_fps`, `total_source_frames`, ném `ERR_ZERO_FRAME_SEGMENT`, `ERR_SOURCE_BOUNDS_OVERFLOW`. |
| `VideoStreamHeader` | `src/core/geometry/source_frame_evidence_contracts.py:57` | `[CODE_VERIFIED]` | Frozen DTO 6 trường do ffprobe báo cáo (`stream_index`, `codec_name`, `r_frame_rate`, `avg_frame_rate`, `reported_nb_frames`, `time_base`). | **LÀ HEADER_REPORTED METADATA**, KHÔNG PHẢI BẰNG CHỨNG CFR ĐÃ KIỂM ĐỊNH. Không chứa cờ `is_cfr` hay số frame giải mã thực tế. |
| `ClipEntry` | `src/core/clip_manifest.py:61-90` | `[CODE_VERIFIED]` | 10 trường: `clip_id`, `file_name`, `duration_sec`, `fps`, `start_tc_frames`, `shot_time`, `shot_time_source`, `content_hash`, `file_size`, `current_path`. | Lưu siêu dữ liệu do container báo cáo. Không có trường `is_cfr`, không có `time_base`, không chứng minh tính toàn vẹn bitstream. |
| `SourceFrameEvidence` | `src/core/geometry/plan_geometry_contracts.py` | `[PROPOSED]` | DTO thuần túy đóng gói bằng chứng khung hình đã kiểm định cho một clip nguồn: `clip_id`, `source_fps`, `total_frames`, `evidence_status`. | Cổng kiểm định đầu vào bắt buộc; ngăn chặn việc tự ý gán nhãn `is_cfr=True` cho siêu dữ liệu container chưa qua kiểm chứng. |
| `assemble_plan_frame_geometry` | `src/core/geometry/plan_geometry_assembler.py` | `[PROPOSED]` | Hàm lắp ráp hình học kịch bản toàn bản: tái sử dụng `quantize_cfr_video_span`, quản lý record cursor liên tục, kiểm soát hook ambiguity. | Hàm pure Python duy nhất lắp ráp toàn bộ kịch bản sang hệ tọa độ timeline. |

### 1.2 Đính Chính Phần Lỗi Thời Trong `06C2-frame-geometry-design.md`
Tài liệu `06C2-frame-geometry-design.md` trước đây có một số nhận định đã lỗi thời so với hiện trạng thực tế sau các bước C2b, C2c1 và C2c2:
1. **Lỗi thời về lộ trình module:** Tài liệu cũ đề xuất gộp parser, supplier và timeline mapper vào một lát cắt lớn. Trên thực tế, kiến trúc đã tách thành các lát cắt nhỏ độc lập: C2b (pure parser), C2c1 (bounded local acquisition), C2c2 (async consumer trong `PromptPreparationWorker`). Lát cắt C2d này là bước hoàn thiện hợp đồng hình học toàn bản dựng (Whole-Plan Geometry Contract) thuần túy trước khi chạm UI.
2. **Lỗi thời về giả định CFR:** Tài liệu cũ từng để ngỏ khả năng suy diễn CFR từ `r_frame_rate == avg_frame_rate`. Sau C2b và C2c review, Codex đã chốt dứt khoát: **Header rate bằng nhau KHÔNG chứng minh CFR**. Tệp MP4/MOV VFR hoặc lỗi timecode vẫn có thể báo cáo header rate giống nhau.
3. **Lỗi thời về sửa đổi `TimelineMapper`:** Không sửa đổi `TimelineMapper` hiện hữu hay đưa tham số QSettings vào hình học chuẩn tắc. `TimelineMapper` cũ được giữ nguyên cho legacy UI path; module hình học mới là pure pipeline riêng biệt.

---

## 2. Lắp Ráp Kịch Bản Dựng & Quản Lý Cursor Timeline

### 2.1 Các Hệ Quy Chiếu Tọa Độ Độc Lập
Thiết kế phân định rạch ròi 4 miền không gian tọa độ:

```
[Miền 1: Thời Gian Giây Nguồn (Source Seconds)]
  EditPlanProposal: [src_in_sec, src_out_sec)  (float / Fraction)
                           │
                           ▼ (quantize_cfr_video_span: pts_to_frame half-even)
[Miền 2: Khung Hình Nguồn Nguyên Bản (Source Native Frames)]
  QuantizedCFRSpan.source_range: [src_in_f, src_out_f)  (strict int, 0 <= src_in_f < src_out_f <= total_source_frames)
                           │
                           ▼ (Formula B: exact Fraction rescale sang timeline_fps / speed)
[Miền 3: Thời Lượng Record Timeline (Record Duration Frames)]
  QuantizedCFRSpan.record_duration_frames: rec_dur_f  (strict int > 0)
                           │
                           ▼ (Plan Assembly: Tích lũy record cursor liên tục)
[Miền 4: Tọa Độ Timeline Dựng (Timeline Record Cursor)]
  Phân đoạn i:   rec_in_f = cursor,   rec_out_f = cursor + rec_dur_f
  Cursor mới:    cursor_next = rec_out_f
  Khoảng record: [rec_in_f, rec_out_f) tại timeline_fps (Origin 0 hoặc Timeline Start TC)
```

### 2.2 Quy Tắc Quản Lý Record Cursor & Tính Liên Tục (Cursor Continuity)
1. **Khởi tạo:** `cursor_frames = 0` (hệ tọa độ Origin 0).
2. **Lắp ráp tuần tự A-Roll (`timeline_segments`):**
   - Với mỗi phân đoạn $S_i$ trong `timeline_segments`:
     - Gọi trực tiếp `quantize_cfr_video_span(S_i.start_sec, S_i.end_sec, source_fps=ev.source_fps, timeline_fps=timeline_fps, total_source_frames=ev.total_frames, is_cfr=ev.is_cfr, applied_speed=S_i.speed)`.
     - Nhận `QuantizedCFRSpan`.
     - Gán: `rec_in_f = cursor_frames`, `rec_out_f = cursor_frames + span.record_duration_frames`.
     - Cập nhật cursor: `cursor_frames = rec_out_f`.
     - Tạo `PlanTimelineSegmentGeometry`.
3. **Bảo toàn tính liên tục không kẽ hở (Gapless A-Roll):**
   - Điểm bắt đầu của phân đoạn $i+1$ luôn bằng đúng điểm kết thúc của phân đoạn $i$: $rec\_in\_f_{i+1} == rec\_out\_f_i$.
   - Tuyệt đối cấm chèn khoảng trống (gap) hoặc chồng lấn (overlap) giữa các phân đoạn A-Roll chính.
4. **Tái sử dụng duy nhất một bộ tính toán:**
   - Tuyệt đối **KHÔNG** viết bộ lượng tử hóa thứ hai. Toàn bộ phép toán khung hình đều thực thi qua `quantize_cfr_video_span`.
   - Cấm áp dụng epsilon làm tròn, cấm clamp âm thầm về 0 hoặc EOF, cấm ép min 1 frame nếu toán học sinh ra 0 frame, và cấm giả định 30fps.

### 2.3 Ngữ Nghĩa Của `global_hook` & Xử Lý Nhập Nhằng (Hook Ambiguity)
Trong `video_plan`, trường `global_hook` (nếu có) khai báo một đoạn điểm nhấn (`clip_id`, `start_sec`, `end_sec`).
Codebase hiện tại tồn tại sự nhập nhằng về ngữ nghĩa sản phẩm:
- **Khả năng A (Intro Cut):** Hook được đặt ở đầu timeline ($[0, hook\_dur\_f)$), sau đó mới đến các phân đoạn trong `timeline_segments`. Nếu đoạn hook này lại được lặp lại trong `timeline_segments`, timeline sẽ bị trùng khung hình thực tế.
- **Khả năng B (Highlight Reference):** Hook chỉ là đánh dấu ngữ nghĩa (semantic reference) chỉ ra đoạn video hấp dẫn nhất nằm bên trong một phân đoạn A-Roll đã có, không tạo thêm cut độc lập.

**Quy tắc xử lý kiến trúc:**
- Bộ lắp ráp hình học **KHÔNG TỰ Ý ĐOÁN MÒ** hành vi của hook.
- Nếu `global_hook` được khai báo mà không có trường chỉ định vị trí rõ ràng (`placement_mode`), bộ lắp ráp ghi nhận cảnh báo/lỗi `ERR_HOOK_PLACEMENT_AMBIGUITY` trong danh sách `issues` của kết quả kiểm định.
- Khi `placement_mode == "intro_cut"`: Hook được lượng tử hóa thành phân đoạn đầu tiên tại $[0, hook\_dur\_f)$, đẩy cursor của `timeline_segments` bắt đầu từ $hook\_dur\_f$.
- Khi `placement_mode == "highlight_ref"`: Hook được đối soát xem có nằm trọn trong một phân đoạn của `timeline_segments` hay không; không tăng cursor timeline.

### 2.4 Tách Biệt Tuyệt Đối Giữa Video Chính & Tài Sản Phụ (D02 Invariant)
1. **Lỗi nguồn chính KHÔNG PHẢI là D02 omission:**
   - D02 cho phép người dùng xem xét và chấp nhận bỏ sót các tài sản bổ trợ (B-roll, SFX, stickers).
   - Nếu một clip nguồn chính (A-Roll) trong `timeline_segments` bị thiếu file, hỏng cấu trúc hoặc lỗi hình học $\rightarrow$ đó là **LỖI CHẶN NGHIÊM TRỌNG (BLOCKING PRIMARY ERROR)**, tuyệt đối không được chuyển thành omission hợp lệ!
2. **Tọa độ tài sản phụ (B-roll, SFX):**
   - B-roll inserts và SFX inserts được định vị trên các layer/track phụ (V2, A2...). Tọa độ của chúng tính theo Record Timeline.
   - B-roll/SFX không làm thay đổi hay xê dịch bất kỳ điểm cắt nào của luồng video chính A-Roll.
3. **Phụ đề động (Kinetic Words):**
   - Thời gian của các từ trong `kinetic_plan` gắn với âm thanh gốc. Khi video cut co giãn speed, phụ đề kinetic phải được tái tỷ lệ tường minh hoặc giữ nguyên theo track âm thanh tương ứng, không được âm thầm làm lệch nhịp hiển thị từ.
4. **Ràng buộc ngôn ngữ tự nhiên (`must_keep_rules` / `must_not_rules`):**
   - Các chỉ thị trong `creative_context.must_keep_rules` và `creative_context.must_not_rules` không thể được chứng minh là đã thỏa mãn chỉ bằng việc kiểm tra schema hay chuỗi ký tự.
   - Bộ hình học ghi nhận các mục này vào `unresolved_reviews` để yêu cầu giao diện trình bày bằng chứng kiểm tra cho người dùng duyệt.

---

## 3. Rào Chắn Bằng Chứng Khung Hình (Frame Evidence Gate)

### 3.1 Bản Chất Của Siêu Dữ Liệu Container Hiện Tại
Cần tuyên bố dứt khoát về mặt kiến trúc:
1. `VideoStreamHeader` (6 trường ffprobe) và `ClipEntry` (10 trường manifest) **LÀ SIÊU DỮ LIỆU ĐƯỢC CONTAINER BÁO CÁO (HEADER_REPORTED METADATA)**.
2. `HEADER_REPORTED` **KHÔNG PHẢI LÀ BẰNG CHỨNG CFR ĐÃ KIỂM ĐỊNH (NOT VERIFIED CFR)**, không chứng minh số lượng khung hình thực tế có thể giải mã trong luồng video và không đại diện cho danh tính nội dung hình ảnh.
3. **Điều cấm tuyệt đối:** Cấm đặt cờ `is_cfr = True` chỉ vì:
   - `r_frame_rate == avg_frame_rate` trong header container.
   - Container có trường `reported_nb_frames`.
   - Video fixture tạo bằng ffmpeg với tùy chọn `-r`.
   - Một cờ `is_cfr: true` do AI tự bịa trong chuỗi JSON trả về.

### 3.2 Ranh Giới Bằng Chứng & Phạm Vi Ngân Sách Tương Lai
Bằng chứng CFR thực tế yêu cầu dịch vụ kiểm định giải mã chuyên sâu.
Cần khẳng định rõ giới hạn kiến trúc:
- Sự bất biến của chuỗi khoảng cách $\Delta PTS$ của packet hay các thuộc tính truy vấn từ Resolve MediaPool không tự động chứng minh nhịp hiển thị khung hình giải mã (presentation cadence).
- Dịch vụ giải mã kiểm định hoặc bộ chuẩn hóa VFR đầy đủ thuộc phạm vi ngân sách kỹ thuật được lập riêng trong G07 hoặc các giai đoạn sau; lát cắt hiện tại tuyệt đối không thực hiện giải mã đầy đủ bitstream hay can thiệp bitstream.

### 3.3 Cách Xử Lý Trung Thực Của Plan Geometry (Honest Dry-Run)
Khi chạy thẩm định hình học kịch bản (Dry-Run):
1. Bộ lắp ráp nhận vào bảng bằng chứng `evidence_table: Mapping[str, SourceFrameEvidence]`.
2. Đối với mỗi clip nguồn trong `timeline_segments`:
   - Nếu clip chỉ có siêu dữ liệu từ `acquire_local_video_header` (`HEADER_REPORTED`), trường `evidence_status` của nó sẽ là `HEADER_REPORTED_ONLY`.
   - Hàm lắp ráp **KHÔNG GIẢ VỜ LÀ KỊCH BẢN ĐÃ SẴN SÀNG THI CÔNG**.
   - Nó trả về `PlanGeometryAssemblyResult` với `status = "GEOMETRY_UNRESOLVED"` kèm mã vấn đề `ERR_UNVERIFIED_CFR_EVIDENCE` cho clip đó.
   - Toàn bộ các phân đoạn liên quan đến clip đó sẽ không có executable frame geometry (`assembled_segments = ()`, totals = `None`).
3. **Ranh giới cô lập cho kiểm thử toán học:**
   - Để kiểm thử logic toán học lắp ráp nhiều phân đoạn, kiểm thử truyền vào các đối tượng `SourceFrameEvidence` được gắn cờ `evidence_status = "VERIFIED_CFR"` có kiểm soát trong fixture.

---

## 4. Hợp Đồng Đầu Ra & Tính Toàn Vẹn Phê Duyệt

### 4.1 Cấu Trúc Hợp Đồng Đầu Ra (`PlanGeometryAssemblyResult`)

Kết quả thẩm định và lắp ráp hình học kịch bản là một Frozen Dataclass thuần túy:

```python
@dataclass(frozen=True)
class PlanTimelineSegmentGeometry:
    """Hình học khung hình hoàn chỉnh cho một phân đoạn video trên timeline dựng."""
    segment_index: int                    # Thứ tự xuất xưởng (0, 1, 2...)
    origin: str                           # "timeline_segment" | "global_hook"
    source_segment_index: Optional[int]   # Dòng trong proposal (None chỉ khi origin == "global_hook")
    clip_id: str
    quantized_span: QuantizedCFRSpan
    timeline_range: TimeRangeFrames       # [rec_in_f, rec_out_f) tại timeline_fps
    # timeline_duration_frames và applied_speed là properties suy diễn từ span và range

@dataclass(frozen=True)
class PlanGeometryIssue:
    """Vấn đề hoặc lỗi hình học phát hiện trong quá trình lắp ráp."""
    severity: str                         # "BLOCKING" | "WARNING"
    issue_code: str                       # ERR_MISSING_SOURCE_EVIDENCE, ERR_ZERO_FRAME_SEGMENT, ...
    clip_id: Optional[str] = None
    segment_index: Optional[int] = None
    message: str = ""

@dataclass(frozen=True)
class UnresolvedReviewItem:
    """Mục cần sự xác nhận tường minh của người dùng (D01 / D02)."""
    category: str                         # "SUPPLEMENTAL_OMISSION" | "HOOK_PLACEMENT" | "SEMANTIC_MUST_KEEP" | "SEMANTIC_MUST_NOT"
    item_id: str
    description: str

@dataclass(frozen=True)
class PlanGeometryAssemblyResult:
    """Kết quả lắp ráp hình học kịch bản toàn bản."""
    status: str                           # "GEOMETRY_VERIFIED" | "GEOMETRY_UNRESOLVED" | "GEOMETRY_BLOCKED"
    proposal_id: str
    proposal_revision: str
    timeline_fps: RationalFPS
    assembled_segments: Tuple[PlanTimelineSegmentGeometry, ...]
    total_timeline_frames: Optional[int]
    total_timeline_duration_sec: Optional[Fraction]
    issues: Tuple[PlanGeometryIssue, ...]
    unresolved_reviews: Tuple[UnresolvedReviewItem, ...]
```

### 4.2 Tính Độc Lập Giữa Hình Học & Phê Duyệt (Approval Invariant)
1. **Phê duyệt không thay thế sự thật:**
   - Sự kiện người dùng nhấn duyệt (`AppOwnedApprovalRecord`) ghi nhận sự đồng thuận về mặt biên tập kịch bản.
   - Phê duyệt **TUYỆT ĐỐI KHÔNG BIẾN** siêu dữ liệu `HEADER_REPORTED` thành `VERIFIED_CFR`. Nếu bằng chứng bitstream chưa có, hình học vẫn ở trạng thái `GEOMETRY_UNRESOLVED`.
2. **Vô hiệu hóa phê duyệt khi sửa điểm cắt (Edit Invalidation):**
   - Bất kỳ sự thay đổi nào đối với điểm cắt (`start_sec`, `end_sec`, `speed`) trong `video_plan` đều làm thay đổi `proposal_revision` SHA-256.
   - Khi `proposal_revision` thay đổi, bản ghi `AppOwnedApprovalRecord` cũ lập tức bị STALE và bị từ chối bởi `verify_proposal_approval_binding`.
3. **Thứ tự ưu tiên trạng thái:**
   - `GEOMETRY_BLOCKED` > `GEOMETRY_UNRESOLVED` > `GEOMETRY_VERIFIED`.
   - Nếu có bất kỳ lỗi BLOCKING hoặc UNRESOLVED ở nguồn chính, `assembled_segments = ()`, `total_timeline_frames = None`, `total_timeline_duration_sec = None` (không trả về prefix dở dang).
   - Nếu toàn bộ các hàng bị cắt tường minh (`action: "cut"`), kết quả là `GEOMETRY_VERIFIED` với `total_timeline_frames = 0` và `total_timeline_duration_sec = Fraction(0, 1)` (được xác định cụ thể, khác với None).

---

## 5. Ma Trận 5 Ca Kiểm Thử Cốt Lõi (Core Fixtures Matrix)

Năm ca kiểm thử chuẩn tắc bao phủ toàn bộ các khía cạnh toán học, bằng chứng và ranh giới lỗi:

| Mã Fixture | Tên & Kịch Bản Kiểm Thử | Dữ Liệu Đầu Vào | Kỳ Vọng Toán Học & Kết Quả | Mã Lỗi / Trạng Thái |
| :--- | :--- | :--- | :--- | :--- |
| **F01** | **Chuyển đổi tốc độ chéo Formula B (30fps $\rightarrow$ 24fps)** | **Positive Control:** `source_fps = 30/1`, `timeline_fps = 24/1`, `total_frames = 90`, `is_cfr = True`. Đoạn cắt: $[1.0, 2.017)$ giây, `speed = 1`.<br>**Denial Control:** `source_fps = 30000/1001`, `total_frames = 60` (max 2.002s), cắt $[1.0, 2.017)$. | **Positive:** $src\_in\_f = 30$, $src\_out\_f = 61 \rightarrow source\_dur = 31$ frames.<br>$\text{exact\_timeline\_span} = 31 / 30 \times 24 = 24.8$.<br>Làm tròn half-even $\rightarrow rec\_dur\_f = \mathbf{25}$ record frames.<br>**Denial:** Tràn thời lượng tối đa. | **Positive:** `GEOMETRY_VERIFIED`, Record range: $[0, 25)$<br>**Denial:** `GEOMETRY_BLOCKED`, `ERR_SOURCE_BOUNDS_OVERFLOW` |
| **F02** | **Lắp ráp nhiều phân đoạn liên tục (Multiple Contiguous Cuts & Speed)** | Clip A: 25fps, 100 frames, cắt $[0.0, 2.0)$, `speed = 1`.<br>Clip B: 25fps, 100 frames, cắt $[1.0, 3.0)$, `speed = 2`.<br>Clip C: 25fps, 100 frames, cắt $[0.0, 1.0)$, `action = "cut"`.<br>`timeline_fps = 25/1`. | Đoạn 1: 50 frames $\rightarrow$ record $[0, 50)$.<br>Đoạn 2: 25 frames $\rightarrow$ record $[50, 75)$.<br>Đoạn 3: cut $\rightarrow$ 0 frame.<br>Tổng timeline: 75 frames, cursor liên tục, 0 gap, 0 overlap. | `GEOMETRY_VERIFIED`<br>Total frames = 75.<br>(All-cut: 0 frames, 0 sec). |
| **F03** | **Từ chối siêu dữ liệu chỉ có Header Container (Header-Reported Denial)** | Clip có `VideoStreamHeader` hợp lệ và `ClipEntry` chuẩn nhưng `evidence_status = "HEADER_REPORTED_ONLY"`. | Không tự tiện gán `is_cfr=True`. Dừng lắp ráp các phân đoạn liên quan, trả về danh sách issues chứa lỗi thiếu bằng chứng, assembled rỗng (). | `GEOMETRY_UNRESOLVED`<br>`ERR_UNVERIFIED_CFR_EVIDENCE` |
| **F04** | **Bắt lỗi đoạn cắt 0 khung hình hoặc VFR trên nguồn chính** | Trường hợp A: Đoạn cắt $[1.0, 1.005)$ trên 24fps $\rightarrow pts\_to\_frame$ cho cả in và out đều là 24 ($source\_dur = 0$).<br>Trường hợp B: Clip nguồn chính bị gắn cờ `evidence_status = "VERIFIED_VFR"`. | Bắt lỗi nghiêm ngặt fail-closed. Không ép $+1$ frame, không chuyển thành omission D02 (vì là nguồn chính). | `GEOMETRY_BLOCKED`<br>A: `ERR_ZERO_FRAME_SEGMENT`<br>B: `ERR_VFR_UNSUPPORTED` |
| **F05** | **Xử lý ngữ nghĩa Global Hook (Hook Ambiguity & Modes)** | - Vắng mặt: bỏ qua.<br>- Có hook nhưng thiếu `placement_mode`: cảnh báo AMBIGUITY, đưa vào unresolved_reviews.<br>- `placement_mode == "intro_cut"`: chèn đầu tiên tại $[0, hook\_dur)$, A-Roll bắt đầu sau hook.<br>- `placement_mode == "highlight_ref"`: kiểm tra nằm trọn trong segment keep/speedup, không tăng cursor; nếu không nằm trọn -> BLOCKING. | Phân định rạch ròi giữa intro cut và semantic reference mà không tự ý đoán mò. | Thiếu mode: `GEOMETRY_UNRESOLVED` (`ERR_HOOK_PLACEMENT_AMBIGUITY`).<br>Không nằm trọn: `GEOMETRY_BLOCKED`. |

---

## 6. Lát Cắt Triển Khai Hoàn Tất (2 Pure Modules)

Lát cắt triển khai được giới hạn nghiêm ngặt trong **đúng 2 tệp mã nguồn thuần túy (pure Python, stdlib only, zero UI, zero subprocess)**:

1. **Module 1 — Hợp đồng DTOs:**  
   `src/core/geometry/plan_geometry_contracts.py`  
   - Chứa `SourceFrameEvidence` (với enum/status `VERIFIED_CFR`, `HEADER_REPORTED_ONLY`, `VERIFIED_VFR`).
   - Chứa `PlanTimelineSegmentGeometry`, `PlanGeometryIssue`, `UnresolvedReviewItem`, `PlanGeometryAssemblyResult`.
   - Toàn bộ là frozen dataclass bất biến, defensive copy, zero external imports.

2. **Module 2 — Bộ lắp ráp hình học thuần túy:**  
   `src/core/geometry/plan_geometry_assembler.py`  
   - Chứa hàm `assemble_plan_frame_geometry(proposal: EditPlanProposal, evidence_table: Mapping[str, SourceFrameEvidence], timeline_fps: RationalFPS) -> PlanGeometryAssemblyResult`.
   - Tái sử dụng `quantize_cfr_video_span` cho từng cut.
   - Tích lũy cursor tuần tự, kiểm tra tính toàn vẹn và xuất bản kết quả.
   - Không có tham số `default_hook_placement` ghi đè kịch bản ngoài proposal revision.

---

## 7. Danh Mục Các Bước Còn Lại Sau Lát Cắt Này (Remaining Path)

Sau khi lát cắt hình học kịch bản 2 module trên hoàn tất, các công đoạn tiếp theo để hoàn thiện G06 và chuyển sang G07 bao gồm:

1. **Intake Ngữ Cảnh Sáng Tạo (CreativeContext Intake Binding):**
   - Đảm bảo các chỉ thị phong cách, tỷ lệ khung hình và timeline FPS mong muốn từ người dùng được truyền đúng vào `EditPlanProposal`.
2. **Giao Diện Đánh Giá Chung Đề Xuất & Thiếu Sót (Common Review UI):**
   - Xây dựng giao diện trình bày đồng thời: danh sách phân đoạn video cắt, các tài sản phụ đề xuất (B-roll, SFX), danh sách tài sản bị bỏ sót (`proposed_omissions`), và các cảnh báo hình học chưa được xác nhận.
   - Cho phép người dùng duyệt một lượt (One User Review - D01).
3. **Phát Hành Bản Ghi Phê Duyệt Của Ứng Dụng (App-Owned Approval Issuance):**
   - Khi người dùng bấm duyệt trên giao diện, ứng dụng tính `proposal_revision` và sinh `AppOwnedApprovalRecord` theo đúng hợp đồng C1b2.
4. **Bàn Giao Sang DaVinci Resolve Scripting API (G07 Boundary):**
   - Chuyển đổi các phân đoạn `PlanTimelineSegmentGeometry` đã được phê duyệt sang các lệnh API của Resolve (`InsertMedia` / `AppendToTimeline`) với việc tách biệt rõ ràng giữa source in/out và recordFrame trên project timeline thực tế.

---

## 8. Cập Nhật Sửa Đổi Gộp (G06-06C2d-R1 Grouped Repairs)

Sau vòng thẩm định độc lập 15 probe cases của Tech Lead Codex (`06C2d-I-codex-review.md`), hai module hình học thuần túy đã được hoàn thiện triệt để:

### 8.1 Nhóm A — Sở Hữu & Tái Kiểm Tra Bằng Chứng (Evidence Boundary Authority)
- **Tái tạo sâu (`reconstruct_source_frame_evidence`):** Kiểm tra nominal type `SourceFrameEvidence`, sự hiện diện bắt buộc của cả 5 trường (`manifest_id`, `clip_id`, `source_fps`, `total_frames`, `evidence_status`). Không cho phép thiếu thuộc tính (ném `ERR_MALFORMED_EVIDENCE`), cấm chuỗi trạng thái lạ/None/list/dict (ném `ERR_INVALID_EVIDENCE_STATUS`).
- **Thẩm quyền bảng kê:** Toàn bộ bảng `evidence_table` được tái tạo ở lối vào trước khi duyệt kịch bản; khóa mapping phải là chuỗi UTF-8 hợp lệ; clip_id bắt buộc thuộc `proposal.source_binding.clips` (chặn clip lạ bằng `ERR_UNKNOWN_CLIP_ID`).

### 8.2 Nhóm B — Thẩm Định Hook Nghiêm Ngặt & Bảo Vệ Thẩm Quyền Nguồn
- **Đồng nhất thẩm quyền với footage:** Hook `clip_id` phải thuộc `source_binding.clips` và tuyệt đối không thuộc `reference_items` (`ERR_REFERENCE_CLIP_NOT_FOOTAGE`).
- **Kiểu số thô & ranh giới thời gian:** Bắt buộc `start_sec` và `end_sec` là int/float hữu hạn ($0 \le start < end$); cấm float-coercion chuỗi số hoặc bool. Cấm speed là bool/string/$\le 0$.
- **Bảo vệ tính mơ hồ (Ambiguity):** Chỉ khi hook có cấu trúc hợp lệ đầy đủ nhưng thiếu `placement_mode` mới đưa vào `UNRESOLVED` (`ERR_HOOK_PLACEMENT_AMBIGUITY`). Mọi sai sót về kiểu/cấu trúc/thời gian đều là `BLOCKING`.
- **So sánh phân số hữu tỉ cho `highlight_ref`:** Chuyển đổi chính xác sang `Fraction` để kiểm tra bao hàm trong phân đoạn keep/speedup, không dùng làm tròn float.

### 8.3 Nhóm C — Sở Hữu DTO Sâu, Ràng Buộc Bản Ghi & An Toàn Chuỗi
- **Deep Rehydration:** Tái tạo mới toàn bộ đối tượng con (`PlanTimelineSegmentGeometry`, `QuantizedCFRSpan`, `TimeRangeFrames`, `PlanGeometryIssue`, `UnresolvedReviewItem`) khi khởi tạo kết quả để triệt tiêu aliasing và chống mutation từ caller.
- **Ràng buộc GEOMETRY_VERIFIED:**
  - `total_timeline_frames` phải khớp đúng tổng thời lượng các phân đoạn và final cursor (`ERR_TOTAL_FRAMES_MISMATCH`).
  - Phân đoạn đầu tiên bắt buộc bắt đầu tại cursor 0 (`ERR_CURSOR_START_NONZERO`).
  - Thứ tự 0..N-1 liên tục, không hở/đè (`ERR_TIMELINE_DISCONTINUITY`).
  - Lệch `timeline_fps` giữa phân đoạn và timeline bị từ chối (`ERR_FPS_MISMATCH`).
  - All-cut timeline yêu cầu chính xác 0 frames và `Fraction(0, 1)` giây.
- **An toàn chuỗi & giới hạn:** Chuỗi được kiểm tra NUL (`\0`) và lone surrogates UTF-8; giới hạn trần 512KiB per string; danh sách segments $\le 257$, issues $\le 1024$, review items $\le 512$ trước khi duyệt.
- **Tính nhất quán trạng thái:** Có lỗi BLOCKING bắt buộc kết quả là `GEOMETRY_BLOCKED`; có WARNING bắt buộc không thể là `GEOMETRY_VERIFIED`.
