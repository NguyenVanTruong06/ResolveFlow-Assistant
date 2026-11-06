# Thiết Kế Hình Học Khung Hình & Lượng Tử Hóa Timeline — G06-06C2-D

> **CODEX BINDING REVIEW & HIỆN TRẠNG TRIỂN KHAI — 2026-10-30:**  
> - **G06-06C2a (NGHIỆM THU — ACCEPTED_WITH_RECORDED_LIMITS):** Đã nghiệm thu theo `06C2a-gate-acceptance.md`. Gồm 2 pure modules: `QuantizedCFRSpan` và `quantize_cfr_video_span` (Formula B source-frame-first, exact Fraction speed, 98 tests pass, 28/28 probes pass). Giới hạn ghi nhận: caller-supplied math only, không chứng minh file nguồn trên đĩa.  
> - **G06-06C2b-I / G06-06C2b-R1 (ĐÃ SỬA CHỮA HOÀN TẤT & CHỜ NGHIỆM THU — REPAIRED_PENDING_REVIEW):** Lát cắt pure parser đầu tiên theo `06C2b-codex-decisions.md`: `src/core/geometry/source_frame_evidence_contracts.py` (`VideoStreamHeader` 6 fields) và `src/core/geometry/source_frame_evidence_parser.py` (`parse_ffprobe_video_header`). Đã đóng hoàn toàn 13 counterexamples của Codex (Group 1: Explicit null vs Missing key; Group 2: Scope tiêu thụ disposition; Group 3: Safe diagnostics chống vượt giới hạn 4300 chữ số và chống gọi `__repr__` độc hại; 172/172 tests PASS trong 0.50s). Parser thuần túy giữ reported metadata, không suy diễn cờ CFR từ header rate.  
> - **G06-06C2c-D (THIẾT KẾ THU THẬP TIÊU ĐỀ CỤC BỘ & KẾT NỐI CONSUMER — DESIGN_READY):** Đã hoàn thiện thiết kế kiến trúc tại `docs/architecture/06C2c-local-metadata-acquisition-design.md`, bao gồm callgraph thực tế, so sánh reuse vs adapter mới, giao thức subprocess có giới hạn trần chống tràn RAM/deadlock, rào chắn bảo vệ độ tươi mới nguồn TOCTOU hai đầu stat, và lộ trình vá lỗi import mồ côi `get_video_metadata` trong `ClipManifest`.  
> - **Các thành phần tiếp tục HOÃN LẠI (DEFERRED):** Tiến trình gọi ffprobe thực tế (`SourceMediaProbeSupplier`), xác minh frame toàn vẹn bitstream, cache trên đĩa, adapter tự động chuyển sang `quantize_cfr_video_span`, whole timeline composer, và DaVinci Resolve Scripting API.
> - **Đính chính kiến trúc quan trọng:**
>   1. `detect_vfr_status` chỉ là heuristic; khi probe lỗi trả về `is_vfr=False` với `r_fps=0, avg_fps=0` thì KHÔNG ĐƯỢC coi là bằng chứng CFR. Phải phân định rõ trạng thái unknown/failed probe với confirmed CFR và VFR.
>   2. Trong `TimelineMapper` hiện tại, các phép tính source bounds đã dùng Fraction ở một số bước (`_to_finite_fraction`), nhưng phần record duration (`round(src_dur / speed)` float), `pad_sec=0.3`, clamp min 1 frame và QSettings `speed_mode` vẫn là các giới hạn legacy. Không sửa mapper ở lát cắt này.
>   3. Lệnh Scripting API của Resolve (`InsertMedia`, `AppendToTimeline`) tách biệt source start/end và recordFrame; không ánh xạ gộp timeline absolute TC vào startFrame/endFrame như lý thuyết cũ. Mọi API semantics phải kiểm chứng thực tế tại G07.
>   4. Bug import `get_video_metadata` không tồn tại trong `_probe_and_create_clip_entry` là prerequisite được ghi nhận cho evidence supplier ở lát cắt sau; không tự ý sửa `clip_manifest.py` hay `ffmpeg_utils.py` trong lát cắt pure geometry.

> **CẤP ĐỘ THIẾT KẾ:** Worker `HIGH` — Pure Architecture Design (Design Only, Zero Code, Zero I/O).  
> **Nhiệm vụ:** Thiết kế chuẩn tắc cho bài toán hình học khung hình (Frame Geometry), lượng tử hóa Công thức B (Source-Frame-First) cho kịch bản dựng video có bằng chứng CFR, phân định ranh giới bộ ánh xạ hiện hữu và đề xuất duy nhất một lát cắt triển khai tối thiểu ($\le 2$ pure modules).  
> **Kế thừa & Ràng buộc:** Kế thừa trực tiếp `EditPlanProposal` từ `G06-06C1b1-R1` (đã nghiệm thu tại `06C1b1-gate-acceptance.md`), `AppOwnedApprovalRecord` từ `G06-06C1b2-R1` (69 tests PASS) và các quyết định sản phẩm [D01, D02, D03](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/docs/architecture/06C-product-decisions.md).  
> **Trạng thái thực hiện:** `DESIGN_ONLY` — Không can thiệp mã nguồn production, không chạy DaVinci Resolve, không gọi network/ffprobe/subprocesses.

---

## 1. Bản Đồ Truy Vết Codebase Thực Tế & Bằng Chứng Siêu Dữ Liệu (Source-Grounded Trace)

Nhằm đảm bảo thiết kế gắn chặt với hiện trạng mã nguồn thực tế và không xây dựng trên các giả định không có thật, bảng dưới đây đối chiếu toàn bộ các đối tượng sản xuất (`[CODE_VERIFIED]`) và các đề xuất hình học (`[PROPOSED]`):

### 1.1. Bảng Đối Chiếu Thành Phần Codebase Thực Tế

| Ký hiệu / Đối tượng | Tệp mã nguồn & Dòng | Phân loại | Trạng thái hiện tại trong codebase | Ranh giới hình học & Khoảng trống thực tế |
| :--- | :--- | :--- | :--- | :--- |
| `EditPlanProposal.source_binding` | `src/core/contracts/edit_plan_proposal_contracts.py:47` | `[CODE_VERIFIED]` | Chứa `manifest_id: str` và danh sách `clips: list[dict]`. Mỗi clip có `clip_id`, `file_name`, `current_path`, `content_hash`, `duration_sec` (float), `fps` (float). | Lưu FPS dưới dạng số thực (`float`), **chưa có** `time_base`, `r_frame_rate`, `nb_frames`, hay cờ xác nhận `is_cfr`. |
| `EditPlanProposal.video_plan` | `src/core/contracts/edit_plan_proposal_contracts.py:48` | `[CODE_VERIFIED]` | Chứa `strategy_summary`, `global_hook`, `timeline_segments`, `broll_inserts`, `sfx_inserts`. | Điểm cắt trong `timeline_segments` (`start_sec`, `end_sec`, `speed`) lưu dạng giây số thực (`float`/`int`). |
| `global_hook` | `src/core/contracts/edit_plan_proposal_contracts.py:210` | `[CODE_VERIFIED]` | Optional mapping chứa `clip_id`, `start_sec`, `end_sec`, `hook_title`. | Tọa độ tính bằng giây; chưa định rõ vị trí placement độc lập ở đầu timeline hay trích lại từ A-Roll segment. |
| `kinetic_plan` | `src/core/contracts/edit_plan_proposal_contracts.py:49` | `[CODE_VERIFIED]` | Optional mapping Slot 2 chứa `preset_style`, `lines` với danh sách `words` (`word`, `start_sec`, `end_sec`). | Mốc thời gian từ phụ đề tính bằng giây thực; chưa lượng tử hóa theo frame timeline và chưa tính co giãn speed. |
| `AppOwnedApprovalRecord` | `src/core/contracts/edit_plan_approval_contracts.py:139` | `[CODE_VERIFIED]` | Frozen DTO gồm đúng 6 trường (`approval_id`, `proposal_id`, `proposal_revision`, `approved_at_iso`, `accepted_omission_ids`, `approval_scope`). | Ràng buộc bất biến với `proposal_revision` SHA-256. Mọi thay đổi điểm cắt hình học đều làm approval chuyển `STALE`. |
| `verify_proposal_approval_binding` | `src/core/contracts/edit_plan_approval_contracts.py:240` | `[CODE_VERIFIED]` | Hàm xác minh tính gắn kết giữa proposal và approval (re-validation + hash check + omission IDs). | Pure verification, zero I/O, fail-closed an toàn. |
| `ClipEntry` | `src/core/clip_manifest.py:61-90` | `[CODE_VERIFIED]` | Data class biểu diễn clip nguồn: `clip_id`, `content_hash`, `file_name`, `file_size`, `duration_sec` (float), `fps` (float), `start_tc_frames` (int), `current_path`. | Trong `from_dict`: `fps = float(data.get("fps", 30.0))` $\rightarrow$ **fallback 30.0 ngầm định** nếu thiếu dữ liệu! |
| `_probe_and_create_clip_entry` | `src/core/clip_manifest.py:320-353` | `[CODE_VERIFIED]` | Dòng 333 cố gắng gọi `from src.core.ffmpeg_utils import get_video_metadata`. | `get_video_metadata` **không tồn tại** trong `ffmpeg_utils`! Khối `try...except` luôn bắt `ImportError` âm thầm và fallback về `dur=0.0, fps=30.0, start_tc=0`. |
| `_VIDEO_META_CACHE` | `src/core/resolve_api.py:1839` | `[CODE_VERIFIED]` | Bộ nhớ đệm dict `_VIDEO_META_CACHE` lưu `duration`, `is_vertical`, `width`, `height`. | Hoàn toàn **không lưu** `fps`, `start_tc_frames`, hay `r_frame_rate`. |
| `detect_vfr_status` | `src/core/ffmpeg_utils.py:113-170` | `[CODE_VERIFIED]` | Chạy ffprobe đọc `r_frame_rate` và `avg_frame_rate` của `v:0`, kiểm tra `abs(r_fps - avg_fps) > 0.5`. | Có sẵn nhưng chưa được tích hợp vào `ClipManifest` hay luồng thẩm định kịch bản. |
| `RationalFPS` | `src/core/contracts/time_contracts.py:25-183` | `[CODE_VERIFIED]` | Frozen DTO phân số tối giản `numerator/denominator`, hỗ trợ NTSC aliases (`24000/1001`, `30000/1001`), không fallback ngầm. | Cung cấp nền tảng toán học phân số hoàn hảo, loại bỏ trôi frame của float. |
| `pts_to_frame` / `frame_to_pts` | `src/core/contracts/time_contracts.py:205-263` | `[CODE_VERIFIED]` | Quy đổi PTS giây sang Frame nguyên và ngược lại; sử dụng round-half-to-even chính xác qua `divmod`. | Hỗ trợ tọa độ âm, không tự ý clamp về 0. |
| `TimeRangeFrames` | `src/core/contracts/time_contracts.py:269-314` | `[CODE_VERIFIED]` | Khoảng khung hình nửa mở $[start\_frame, end\_frame)$ bất biến, hỗ trợ `contains` và `overlaps`. | Ranh giới toán học chuẩn cho cả miền nguồn (source) và miền đích (record). |
| `TimelineMapper.add_segment` | `src/core/timeline_mapper.py:162-255` | `[CODE_VERIFIED]` | Bộ ánh xạ timeline hiện hữu: dùng single `self.fps`, áp dụng `pad_sec = 0.3`, clamp `max(0, ...)`, min 1 frame, float round `round(src_dur / speed)`. | Mang tính legacy: phụ thuộc QSettings `speed_mode`, dùng chung một FPS cho cả source và timeline, làm tròn float trung gian. |
| `SourceFrameEvidence` | `src/core/geometry/frame_geometry_contracts.py` | `[PROPOSED]` | DTO thuần túy mang bằng chứng hình học gốc đã xác thực: `clip_id`, `source_fps: RationalFPS`, `total_frames: int`, `time_base: Fraction`, `is_cfr: bool`, `start_tc_frames: int`. | Đầu vào bắt buộc cho thuật toán lượng tử hóa Công thức B, loại bỏ triệt để fallback ngầm định. |
| `compute_timeline_frame_geometry` | `src/core/geometry/frame_geometry_calculator.py` | `[PROPOSED]` | Hàm tính toán hình học thuần túy (pure function): lượng tử hóa Công thức B, phân định origin 0 vs Start TC, fail-closed rõ ràng. | Module độc lập $\le 2$ pure files, không sửa đè `TimelineMapper` hiện tại. |

### 1.2. Đánh Giá Hiện Trạng Siêu Dữ Liệu Thực Tế

Từ bằng chứng mã nguồn nêu trên, kết luận dứt khoát:
1. **Dữ liệu hiện có thực tế:** Chỉ có `duration_sec` (`float`) và `fps` (`float`) trong `source_binding.clips` và `ClipEntry`. Tuy nhiên, `fps` thường là giá trị fallback `30.0` do lỗi import trong `_probe_and_create_clip_entry` hoặc do `from_dict` thiết lập.
2. **Dữ liệu hoàn toàn chưa có:** Chưa có `time_base` dạng phân số, chưa có số khung hình nguyên bản `nb_frames`/`frame_count`, chưa có bằng chứng kiểm định `is_cfr` (Constant Frame Rate) gắn kèm với clip.
3. **Cảnh báo kiến trúc:** Chuỗi `manifest_id` (chỉ là 8 ký tự SHA-256 của `clip_id:content_hash`) hay việc clip có mặt trong `source_binding` **KHÔNG PHẢI** là bằng chứng xác thực rằng clip đó có đủ thông tin hình học khung hình gốc (Native Frame Proof). Không thể tự suy đoán một clip là CFR 30fps chỉ vì nó có mặt trong manifest!

---

## 2. Chính Sách Hình Học Video — Công Thức B (Formula B Policy)

Chính sách lượng tử hóa cho video cuts đã được chốt và thống nhất: **Công Thức B (Source-Frame-First)** trên cơ sở số học phân số chuẩn xác và làm tròn half-even, chỉ áp dụng khi có đầy đủ bằng chứng CFR và FPS phân số hợp lệ.

### 2.1. Định Nghĩa Toán Học Chuẩn Tắc (Exact Mathematical Formulation)

Cho một phân đoạn video $S$ cắt từ clip nguồn $C$ với các tham số:
- $src\_in\_sec, src\_out\_sec \in \mathbb{Q}$ (thời điểm bắt đầu và kết thúc trên clip nguồn, tính bằng giây).
- $source\_fps \in \text{RationalFPS}$ (tốc độ khung hình nguồn, phân số $N_s / D_s$).
- $timeline\_fps \in \text{RationalFPS}$ (tốc độ khung hình timeline dựng, phân số $N_t / D_t$).
- $applied\_speed \in \mathbb{Q}^+$ (tốc độ phát sau khi áp dụng chính sách, số hữu tỉ $> 0$).

#### Bước 1: Lượng tử hóa ranh giới nguồn nửa mở $[src\_in\_f, src\_out\_f)$
Lượng tử hóa trực tiếp từ giây nguồn sang khung hình nguồn nguyên bản sử dụng hàm `pts_to_frame` với thuật toán làm tròn đến số chẵn gần nhất (round-half-to-even):
$$src\_in\_f = \text{pts\_to\_frame}(src\_in\_sec, source\_fps) = \text{\_round\_half\_to\_even}\left( \text{numerator}(src\_in\_sec \times source\_fps), \text{denominator}(src\_in\_sec \times source\_fps) \right)$$
$$src\_out\_f = \text{pts\_to\_frame}(src\_out\_sec, source\_fps) = \text{\_round\_half\_to\_even}\left( \text{numerator}(src\_out\_sec \times source\_fps), \text{denominator}(src\_out\_sec \times source\_fps) \right)$$
$$source\_duration\_frames = src\_out\_f - src\_in\_f$$

> [!IMPORTANT]
> **Ràng buộc bảo toàn ranh giới nguồn (Fail-Closed Boundaries):**
> 1. $src\_in\_sec < src\_out\_sec$ là điều kiện tiên quyết.
> 2. Nếu sau lượng tử hóa mà $source\_duration\_frames \le 0$, hệ thống **BẮT BUỘC ném lỗi `ERR_ZERO_FRAME_SEGMENT`**. Tuyệt đối **CẤM** tự tiện ép $+1$ frame hay co giãn điểm cắt để che giấu!
> 3. Nếu $src\_out\_f > total\_source\_frames$ của clip (khi có bằng chứng `SourceFrameEvidence`), hệ thống **BẮT BUỘC ném lỗi `ERR_SOURCE_BOUNDS_OVERFLOW`**. Tuyệt đối **CẤM** tự ý clamp âm thầm về cuối clip.

#### Bước 2: Tái tỷ lệ khoảng thời lượng sang khung hình Timeline Record ($rec\_dur\_f$)
Tái tỷ lệ số khung hình nguồn sang số khung hình timeline dựng bằng số học phân số thuần túy (Fraction arithmetic), hoàn toàn không sử dụng số thực trung gian:
$$\text{exact\_timeline\_span} = \frac{source\_duration\_frames \times \text{as\_fraction}(timeline\_fps)}{\text{as\_fraction}(source\_fps) \times applied\_speed} = \frac{source\_duration\_frames \times (N_t / D_t)}{(N_s / D_s) \times applied\_speed}$$

Lượng tử hóa kết quả sang số khung hình timeline nguyên bằng round-half-to-even:
$$rec\_dur\_f = \text{\_round\_half\_to\_even}\left( \text{numerator}(\text{exact\_timeline\_span}), \text{denominator}(\text{exact\_timeline\_span}) \right)$$

Nếu $rec\_dur\_f \le 0$ (do speed quá lớn so với khoảng cắt), hệ thống ném lỗi `ERR_ZERO_RECORD_FRAMES`.

#### Bước 3: Xác lập tọa độ Record Timeline
Tách bạch tuyệt đối hai hệ quy chiếu trên timeline dựng:
1. **Tọa độ Offset Timeline (Origin 0):** Bắt đầu từ 0 tại điểm đầu của project timeline.
   $$rec\_in\_f = cursor\_frames$$
   $$rec\_out\_f = rec\_in\_f + rec\_dur\_f$$
   $$cursor\_frames_{\text{next}} = rec\_out\_f$$
   Khoảng nửa mở record timeline: $[rec\_in\_f, rec\_out\_f)$.
2. **Tọa độ Timecode Tuyệt Đối DaVinci Resolve (Absolute Timeline TC):**
   Nếu timeline của DaVinci Resolve có Start Timecode quy định (ví dụ `01:00:00:00` tại 24fps tương ứng $86,400$ frames):
   $$resolve\_in\_tc\_f = timeline\_start\_tc\_frames + rec\_in\_f$$
   $$resolve\_out\_tc\_f = timeline\_start\_tc\_frames + rec\_out\_f$$

---

### 2.2. Bốn Ví Dụ Tính Toán Số Học Chuẩn (Worked Examples)

#### Ví dụ 1: Nguồn 30 FPS $\rightarrow$ Timeline 24 FPS, Cắt 1.000s đến 2.017s, Tốc độ 1.0x (Phản ví dụ kinh điển)
- $source\_fps = 30/1$, $timeline\_fps = 24/1$, $applied\_speed = 1/1$.
- $src\_in\_sec = 1.000 = 1/1 \implies src\_in\_f = \text{pts\_to\_frame}(1, 30) = 30$.
- $src\_out\_sec = 2.017 = 2017/1000$:
  - Phân số tích: $\frac{2017}{1000} \times \frac{30}{1} = \frac{60510}{1000} = \frac{6051}{100} = 60.51$.
  - Chia lấy dư: $6051 = 60 \times 100 + 51$. Số dư $r = 51$.
  - $2 \times r = 102 > 100 \implies$ Làm tròn lên: $src\_out\_f = 61$.
- $source\_duration\_frames = 61 - 30 = 31$ khung hình nguồn.
- Ranh giới nguồn nửa mở: $[30, 61)$.
- Tái tỷ lệ sang timeline 24 FPS:
  $$\text{exact\_timeline\_span} = \frac{31 \times (24/1)}{(30/1) \times 1} = \frac{744}{30} = \frac{124}{5} = 24.8$$
  - Chia lấy dư: $124 = 24 \times 5 + 4$. Số dư $r = 4$.
  - $2 \times r = 8 > 5 \implies$ Làm tròn lên: $rec\_dur\_f = 25$.
- **Kết quả:** $source\_duration = 31$ frames tại 30fps $\implies rec\_dur\_f = \mathbf{25\text{ frames}}$ tại 24fps.  
  (Nếu cursor ban đầu $= 0$, record range là $[0, 25)$).

#### Ví dụ 2: Nguồn 30 FPS $\rightarrow$ Timeline 24 FPS, Cắt 10.000s đến 20.000s, Tốc độ 1.0x
- $src\_in\_sec = 10.0 \implies src\_in\_f = 10 \times 30 = 300$.
- $src\_out\_sec = 20.0 \implies src\_out\_f = 20 \times 30 = 600$.
- $source\_duration\_frames = 600 - 300 = 300$ khung hình nguồn. Ranh giới nguồn: $[300, 600)$.
- Tái tỷ lệ: $\text{exact\_span} = \frac{300 \times 24}{30} = 240.0$.
- **Kết quả:** $rec\_dur\_f = \mathbf{240\text{ frames}}$ tại 24fps.

#### Ví dụ 3: Trường Hợp Chính Giữa Exact Tie (NTSC Rational & Half-Even)
- Clip nguồn $29.97\text{ FPS}$ ($30000/1001$), Timeline $23.976\text{ FPS}$ ($24000/1001$), Speed $1.0$.
- Xét một phân số có ranh giới rơi vào đúng chính giữa: $\text{exact\_span} = 24.5 = 49/2$.
  - $49 = 24 \times 2 + 1$. Số dư $r = 1$, $2 \times r = 2 == den$.
  - Thuật toán round-half-to-even kiểm tra thương $q = 24$ là số chẵn $\implies$ Làm tròn về **$24$**.
- Xét trường hợp $\text{exact\_span} = 25.5 = 51/2$:
  - $51 = 25 \times 2 + 1$. Thương $q = 25$ là số lẻ $\implies$ Làm tròn lên số chẵn kế tiếp là **$26$**.
- Không có bất kỳ hiện tượng trôi frame hoặc thiên lệch về dương (positive bias).

#### Ví dụ 4: Biến Đổi Tốc Độ (Speed Case: 1.5x)
- Nguồn 30 FPS, Timeline 24 FPS, Cắt 1.0s đến 3.0s ($src\_duration = 60$ frames nguồn), Speed $1.5 = 3/2$:
- Tái tỷ lệ:
  $$\text{exact\_timeline\_span} = \frac{60 \times (24/1)}{(30/1) \times (3/2)} = \frac{1440}{45} = 32.0$$
- **Kết quả:** $rec\_dur\_f = \mathbf{32\text{ frames}}$ tại 24fps.

---

## 3. Ranh Giới Bộ Ánh Xạ Hiện Hữu & Xử Lý Unsupported

### 3.1. Ranh Giới `TimelineMapper` Hiện Hữu & Nguyên Tắc Không Sửa Đè
`TimelineMapper` hiện tại (`src/core/timeline_mapper.py`) chứa nhiều giả định cục bộ nhằm phục vụ giao diện xem trước (Mini Timeline) và xuất XML trước đây:
1. Tự động cộng thêm đệm padding $0.3\text{s}$ ở hai đầu (`pad_sec = 0.3`).
2. Tự động ép về $1.0\text{x}$ nếu cấu hình `speed_mode` trong QSettings là `"off"`.
3. Tự động làm tròn số thực `round(src_dur_f / applied_speed)`.
4. Ép buộc độ dài tối thiểu 1 frame (`if src_out_f <= src_in_f: src_out_f = src_in_f + 1`).
5. Chỉ nhận một tham số FPS duy nhất cho cả timeline và toàn bộ clip nguồn!

> [!CAUTION]
> **Quyết định kiến trúc:**  
> Tuyệt đối **KHÔNG** sửa đè trực tiếp `TimelineMapper` hiện tại trong giai đoạn này để tránh phá vỡ giao diện Director Window và Mini Timeline đang hoạt động.  
> Thay vào đó, xây dựng một module bộ điều hợp hình học thuần túy (`Boundary Geometry Adapter`) riêng biệt, tái sử dụng các hợp đồng chuẩn của `time_contracts.py` (`RationalFPS`, `pts_to_frame`, `TimeRangeFrames`).

### 3.2. Chính Sách Xử Lý Clip Không Đủ Dữ Liệu hoặc VFR (Variable Frame Rate)
Hệ thống xử lý fail-closed nghiêm ngặt theo các nguyên tắc sau:
1. **Thiếu Bằng Chứng Khung Hình Gốc (Missing Frame Evidence):**
   - Nếu clip trong kịch bản chỉ có `fps: 30.0` dạng float mặc định mà không có `total_source_frames` hay cờ `is_cfr` được xác thực rõ ràng, hàm `quantize_cfr_video_span` ném lỗi `ERR_MISSING_SOURCE_EVIDENCE`.
   - Tuyệt đối không tự tiện coi fallback 30fps hay `from_dict` là chuẩn.
2. **Video Tốc Độ Khung Hình Biến Thiên (VFR — Smartphone / Screen Recording):**
   - `detect_vfr_status` trong codebase chỉ là hàm heuristic ngoại vi; khi probe gặp lỗi trả về `is_vfr=False` với `r_fps=0, avg_fps=0` thì **TUYỆT ĐỐI KHÔNG ĐƯỢC COI LÀ BẰNG CHỨNG CFR**.
   - Bắt buộc phân biệt 3 trạng thái: `CONFIRMED_CFR` (is_cfr=True), `CONFIRMED_VFR` (is_cfr=False $\rightarrow$ ném lỗi `ERR_VFR_UNSUPPORTED`), và `UNKNOWN_EVIDENCE` (is_cfr=None $\rightarrow$ ném lỗi `ERR_MISSING_SOURCE_EVIDENCE`).
   - Không hỗ trợ ngầm VFR; không cam kết hỗ trợ mọi clip quay từ điện thoại di động mà không qua bước chuyển mã (transcode) về CFR.
3. **Phân Định Trách Nhiệm Cung Cấp Bằng Chứng & Ghi Nhận Lỗi Import:**
   - Lỗi import `get_video_metadata` không tồn tại trong `_probe_and_create_clip_entry` là một **prerequisite được ghi nhận** cho module evidence supplier ở tầng I/O ngoại vi sau này; không tự ý sửa `clip_manifest.py` hay `ffmpeg_utils.py` ở lát cắt pure geometry này.
   - Core contracts và Geometry Calculator giữ nguyên tính chất pure 100%, không chứa bất kỳ lệnh subprocess hay I/O nào.

### 3.3. Giới Hạn Đồng Bộ Âm Thanh (Audio Sync) & Ranh Giới Resolve Scripting API
1. **Audio Sync:** DaVinci Resolve xử lý track âm thanh theo tần số lấy mẫu (thường là 48,000 Hz) độc lập với video frames. Khi thực hiện cắt dựng theo Formula B, video được bảo toàn chính xác theo ranh giới khung hình nguyên tử. Tuy nhiên, audio đi kèm có thể bị lệch một vài mili-giây so với điểm cắt video nếu không được cắt tại ranh giới subframe. Hơn nữa, phụ đề từ vựng (word boundaries tính bằng mili-giây) không tự động khớp hoàn hảo với video frames nếu có co giãn tốc độ (speed). Các vấn đề này cần được kiểm chứng thực tế trên DaVinci Resolve tại `G07`, không cam kết "tự động đồng bộ hoàn hảo" trên lý thuyết.
2. **Resolve API Frame Semantics:** Lệnh Scripting API của DaVinci Resolve (`AppendToTimeline`, `InsertMedia`) tiếp nhận các tham số tách biệt: vị trí cắt nguồn (`startFrame`, `endFrame` trên source media) và vị trí đặt trên timeline (`recordFrame`). Không có việc ánh xạ trực tiếp gộp timeline absolute TC vào `startFrame`/`endFrame`. Các cơ chế này đòi hỏi phải được kiểm chứng thực tế tại `G07`. Pure converter trong lát cắt này chỉ thực hiện toán học phân đoạn, không gọi hay tuyên bố hỗ trợ API khi chưa được đối chiếu thực tế.

---

## 4. Ngữ Nghĩa Kịch Bản & Thẩm Định Khô (Plan Semantics & Dry-Run Geometry)

### 4.1. Phân Biệt Bốn Hệ Tọa Độ Thời Gian
Quá trình thẩm định khô phân định rõ ràng 4 miền giá trị:
1. `stored_source_seconds`: Thời gian lưu trong proposal snapshot (`float`/`int` giây, không làm tròn 6 chữ số).
2. `native_source_frames`: Chỉ số khung hình nguyên $[src\_in\_f, src\_out\_f)$ trên trục thời gian của clip gốc tại $source\_fps$.
3. `record_timeline_frames`: Chỉ số khung hình nguyên $[rec\_in\_f, rec\_out\_f)$ trên timeline dự án bắt đầu từ origin 0 tại $timeline\_fps$.
4. `absolute_resolve_tc`: Timecode thực tế trên DaVinci Resolve timeline (bắt đầu từ Start Timecode của Timeline, ví dụ `01:00:00:00`).

### 4.2. Truy Vết Đoạn Mở Đầu Toàn Cục (`global_hook`)
`global_hook` trong `video_plan` có thể xuất hiện dưới 2 dạng:
1. **Dạng Trích Lại (Reference Hook):** Hook là một trích đoạn từ một phân đoạn A-Roll đã có trong `timeline_segments` (chỉ đóng vai trò gợi ý tiêu đề hoặc highlight). Trong trường hợp này, adapter không chèn thêm frame vào timeline để tránh lặp video.
2. **Dạng Độc Lập (Standalone Hook Cut):** Hook được định nghĩa như phân đoạn đầu tiên của video. Adapter phải đặt hook tại vị trí bắt đầu của record timeline ($rec\_in\_f = 0$), chiếm thời lượng $hook\_dur\_f$, và đẩy toàn bộ các phân đoạn `timeline_segments` tiếp theo tịnh tiến tương ứng ($rec\_in = hook\_dur\_f$).
3. **Quy tắc bảo vệ:** Tuyệt đối không bỏ mất hoặc nhân đôi phân đoạn một cách im lặng. Nếu phát hiện xung đột cấu trúc, phải phát sinh issue cảnh báo.

### 4.3. Định Vị Tài Nguyên B-Roll & SFX và Ranh Giới Omission
- **Vị trí trên Record Timeline:** Trường `timeline_sec` của B-Roll hoặc SFX được lượng tử hóa sang khung hình timeline dựng:
  $$rec\_start\_f = \text{pts\_to\_frame}(timeline\_sec, timeline\_fps)$$
- **Miền Nguồn của Asset:** Trường `src_in_sec` và `duration_sec` của tài nguyên B-Roll/SFX thuộc về miền thời gian của chính tệp tài nguyên đó, được tính toán theo FPS của asset (nếu là video) hoặc sample rate (nếu là audio), hoàn toàn tách biệt với clip A-Roll chính.
- **Ranh Giới Omission:** Omission chỉ áp dụng cho B-Roll và SFX bổ trợ nằm trong danh sách `accepted_omission_ids` đã được người dùng phê duyệt theo D02. Omission **TUYỆT ĐỐI KHÔNG** được dùng để bỏ qua lỗi thiếu footage chính, lỗi tràn ranh giới clip A-Roll, hoặc lỗi lệch manifest.

### 4.4. Hình Học Phụ Đề Chữ Kinetic (Slot 2)
- Dữ liệu `kinetic_plan` có quan hệ toàn vẹn với `proposal_revision` (sửa phụ đề $\rightarrow$ đổi revision $\rightarrow$ mất approval).
- Tuy nhiên, thuật toán hình học video **KHÔNG ĐƯỢC TỰ Ý** làm phẳng (flatten) hay co giãn lại (retime) các mốc thời gian của từng từ (`words`) một cách ngầm định. Việc đồng bộ phụ đề chữ với nhịp cắt video sẽ do bộ giải thuật kinetic chuyên trách xử lý ở lát cắt sau.

### 4.5. Thẩm Định Quy Tắc Bắt Buộc (`must_keep_rules`)
- Sự hiện diện của một chuỗi quy tắc (ví dụ: `"Giữ đoạn giới thiệu thương hiệu"`) trong `CreativeContext.must_keep_rules` không chứng minh rằng đoạn đó đã được giữ trên timeline.
- Thẩm định khô phải đối chiếu semantic giữa quy tắc và danh sách phân đoạn thực tế: Phân đoạn chứa nội dung bắt buộc phải tồn tại trong `timeline_segments` và có thời lượng hợp lệ ($rec\_dur\_f > 0$).

### 4.6. Chính Sách Khi Hình Học Đòi Hỏi Thay Đổi Kịch Bản (Mutation vs Re-approval)
- Nếu quá trình lượng tử hóa hoặc kiểm tra phát hiện phân đoạn vượt quá thời lượng clip nguồn và cần cắt bớt (trim), adapter **TUYỆT ĐỐI KHÔNG TỰ TIỆN SỬA ĐỀ XUẤT** rồi áp dụng approval cũ.
- Bất kỳ sự thay đổi nào về điểm cắt trong proposal đều tạo ra một snapshot mới với `proposal_revision` mới $\implies$ Bản ghi phê duyệt `AppOwnedApprovalRecord` cũ lập tức trở thành **`STALE`** $\implies$ Đòi hỏi người dùng phải duyệt lại một lượt qua giao diện theo D01/D02.

---

## 5. Một Lát Cắt Triển Khai Tối Thiểu & Tiêu Chí Nghiệm Thu Hữu Hạn

Để đảm bảo quy trình phát triển theo đúng chuẩn mực TDD và không mở rộng phạm vi (scope creep), đề xuất DUY NHẤT một lát cắt code tối thiểu gồm $\le 2$ pure modules cho bước `G06-06C2-I`:

### 5.1. Hai Module Pure Python Đề Xuất

```
src/core/geometry/
├── __init__.py
├── frame_geometry_contracts.py      # Module 1: DTOs & Constants hình học bất biến
└── frame_geometry_calculator.py     # Module 2: Thuật toán Formula B pure function
```

#### Module 1: `src/core/geometry/frame_geometry_contracts.py`
Định nghĩa các DTOs bất biến (Frozen dataclasses):
1. `SourceFrameEvidence`:
   ```python
   @dataclass(frozen=True)
   class SourceFrameEvidence:
       clip_id: str
       source_fps: RationalFPS
       total_frames: int
       time_base: Fraction
       is_cfr: bool
       start_tc_frames: int = 0
   ```
2. `QuantizedSegmentGeometry`:
   ```python
   @dataclass(frozen=True)
   class QuantizedSegmentGeometry:
       segment_index: int
       clip_id: str
       # Miền nguồn:
       src_range: TimeRangeFrames     # [src_in_f, src_out_f)
       source_duration_frames: int
       # Miền timeline:
       rec_range: TimeRangeFrames     # [rec_in_f, rec_out_f)
       rec_duration_frames: int
       # Tọa độ DaVinci Resolve Timecode:
       resolve_tc_range: TimeRangeFrames  # [tc_in_f, tc_out_f)
       applied_speed: Fraction
       role: str = "build"
   ```
3. `TimelineGeometryResult`:
   ```python
   @dataclass(frozen=True)
   class TimelineGeometryResult:
       proposal_id: str
       proposal_revision: str
       timeline_fps: RationalFPS
       total_timeline_frames: int
       segments: Tuple[QuantizedSegmentGeometry, ...]
       warnings: Tuple[str, ...]
       is_valid: bool
       error_code: Optional[str] = None
   ```

#### Module 2: `src/core/geometry/frame_geometry_calculator.py`
Chứa hàm thuần túy tính toán toàn bộ hình học timeline:
```python
def compute_timeline_frame_geometry(
    proposal: EditPlanProposal,
    evidence_map: Mapping[str, SourceFrameEvidence],
    timeline_fps: RationalFPS,
    timeline_start_tc_frames: int = 0,
) -> TimelineGeometryResult:
    """Tính toán toàn bộ hình học khung hình timeline từ proposal và evidence map.
    
    Quy tắc:
    - Zero I/O, zero network, zero Resolve API, zero system clock.
    - Duyệt qua toàn bộ timeline_segments, lượng tử hóa theo Formula B.
    - Kiểm tra tính liên tục của cursor: rec_in[k+1] == rec_out[k].
    - Phát hiện sớm zero-frame cuts, bounds overflow, VFR hoặc missing evidence.
    - Trả về TimelineGeometryResult bất biến có đầy đủ bằng chứng kiểm tra.
    """
```

### 5.2. Tiêu Chí Nghiệm Thu Hữu Hạn & Bộ Kiểm Thử (Finite Acceptance Gate)

Bộ kiểm thử tập trung tại: `tests/core/geometry/test_frame_geometry_calculator.py`.

| Nhóm Kiểm Thử | Tình huống kiểm thử cụ thể | Kết quả kỳ vọng |
| :--- | :--- | :--- |
| **Formula B Standard** | Nguồn 30fps $\rightarrow$ Timeline 24fps, cut 1.0s đến 2.017s, speed 1.0x | $src\_range = [30, 61)$, $source\_dur = 31$, $rec\_dur = 25$ frames. |
| **Formula B Long Cut** | Nguồn 30fps $\rightarrow$ Timeline 24fps, cut 10.0s đến 20.0s, speed 1.0x | $src\_range = [300, 600)$, $source\_dur = 300$, $rec\_dur = 240$ frames. |
| **Exact Tie-Breaking** | Phân số NTSC có exact tie $r = den / 2$ | Làm tròn chính xác half-to-even: 24.5 $\rightarrow$ 24; 25.5 $\rightarrow$ 26. |
| **Speed Variations** | Cut 60 frames nguồn, speed 1.5x và speed 2.0x | Tính đúng $rec\_dur = 32$ frames (tại 1.5x) và $24$ frames (tại 2.0x). |
| **Cursor Continuity** | Mạch 3 segments liên tiếp | $rec\_in[k+1] == rec\_out[k]$ tuyệt đối, không có khe hở (gap = 0). |
| **Zero Frame Gate** | $src\_in\_sec$ và $src\_out\_sec$ quá gần nhau khiến $src\_dur\_f == 0$ | Ném lỗi `ERR_ZERO_FRAME_SEGMENT`, cấm tự ý ép $+1$ frame. |
| **Overflow Gate** | $src\_out\_f > total\_frames$ khai báo trong `SourceFrameEvidence` | Ném lỗi `ERR_SOURCE_BOUNDS_OVERFLOW`, cấm tự ý clamp span. |
| **Missing Evidence** | `clip_id` trong segments không có trong `evidence_map` | Trả về `error_code='ERR_MISSING_SOURCE_EVIDENCE'`, `is_valid=False`. |
| **VFR Rejection** | Clip trong evidence có cờ `is_cfr=False` | Trả về `error_code='ERR_VFR_UNSUPPORTED'`, `is_valid=False`. |
| **Start TC Offset** | Timeline start TC là `01:00:00:00` (86400 frames tại 24fps) | `resolve_tc_range` tịnh tiến đúng 86400 frames so với `rec_range`. |
| **Purity Spies** | Kiểm tra mock `open`, `stat`, `socket`, `datetime.now` | Zero filesystem I/O, zero network, zero system clock. |

---

## 6. Lời Nhắc Ranh Giới & Phụ Thuộc Lộ Trình (Deferred Reminders & Roadmap)

1. **Các thành phần vẫn HOÃN LẠI (DEFERRED):**
   - **Giao diện duyệt một lượt (Single Approval UI trên ReviewPage):** Chưa được nối mã. Hiện chỉ có hợp đồng dữ liệu pure `AppOwnedApprovalRecord`.
   - **Tiếp nhận ngữ cảnh sáng tạo & Reference Intake (D03):** Chưa nối vào giao diện `DirectorWindow` hay `ReviewPage`.
   - **Nhà cung cấp bằng chứng ngoại vi (`SourceMediaProbeSupplier`):** Chưa tạo module chạy ffprobe/ffmpeg đọc CFR và rational FPS thật.
2. **Quan hệ phụ thuộc:**
   - `G06-06C1b2-R1` (Hiện tại): Hoàn tất pure approval contracts và thiết kế geometry.
   - `G06-06C2-I` (Bước kế tiếp): Triển khai duy nhất 2 module pure geometry (`frame_geometry_contracts.py` và `frame_geometry_calculator.py`).
   - `G06-06C3` (Sau C2): Nối giao diện duyệt chung D01/D02 trên `ReviewPage`.
   - `G07`: Preflight và thi công DaVinci Resolve Scripting API thật. **Tuyệt đối không tự ý mở G07 trong giai đoạn G06.**
