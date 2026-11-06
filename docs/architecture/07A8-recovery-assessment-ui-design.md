# G07-07A8-D-R1: THIẾT KẾ GIAO DIỆN TRÌNH BÀY ĐỐI SOÁT & PHỤC HỒI CHỈ ĐỌC
*(Read-Only Recovery Assessment & Operator Guidance UI Architectural Design — Revision 1)*

> **Kế thừa & Căn cứ kỹ thuật:**
> - Quyết định rà soát Codex: `docs/architecture/07A8-codex-review.md` (`CHANGES_REQUIRED`, yêu cầu sửa 3 điểm kiến trúc cốt lõi).
> - Quyết định nghiệm thu mã nguồn Codex: `docs/architecture/07A7-I-code-acceptance.md` (`ACCEPTED_WITH_RECORDED_LIMITS`, 15 suites, 217 tests passed in 23.81s, exit 0).
> - Thiết kế kiến trúc đối soát nền tảng: `docs/architecture/07A7-recovery-reconciliation-design.md` (SHA-256: `cf83721d68a0081038392e5cfb2eea56a9a6aa355a3a1302b95d030a43266cca`).
> - Hợp đồng DTO & Dịch vụ thực tế đã nghiệm thu:
>   - `src/core/contracts/recovery_assessment_contracts.py` (Frozen DTOs, bounded sanitization).
>   - `src/services/recovery_classifier_service.py` (Thuật toán phân loại 8 trạng thái tất định).
>   - `src/services/recovery_coordinator.py` (Bộ điều phối thăm dò OS advisory lock probe, fail-closed idempotency).
> - Quyết định kiến trúc Resolve Gateway đơn luồng: Luồng DaVinci Resolve IPC (`fusionscript`) bắt buộc gắn với luồng chủ sở hữu (Owner/Main UI Thread); tuyệt đối cấm gọi IPC Resolve từ worker thread tùy ý.
> - Trạng thái Gate hiện tại: **Gate G07 / Milestone M1 CHƯA HOÀN TẤT**; `ready_to_execute=False` được bảo toàn tuyệt đối; 0 timeline mutation, 0 executor creation.
> - **RÀNG BUỘC THIẾT KẾ THUẦN TÚY (DESIGN ONLY):** Chính xác 0 dòng sửa đổi trong `src/`, 0 sửa đổi test sản phẩm, 0 thực thi pytest/app/Resolve/AI model/mạng ngoài. Không commit/push/clean/stash git. Giữ nguyên toàn bộ dirty/untracked user work.

- **TASK_ID:** `G07-07A8-D-R1`
- **WORKER_LEVEL:** `HIGH`
- **MODE:** `DESIGN_ONLY_RECOVERY_UI_THREAD_AND_STATUS_REPAIR`
- **MỤC TIÊU HIỆU ĐÍNH KIẾN TRÚC:**
  1. **Tuân thủ đơn luồng Resolve (Resolve Thread Affinity):** Định tuyến việc lấy mẫu thẩm quyền Resolve qua luồng chủ sở hữu / gateway tuần tự hóa trên luồng chính GUI. Tạo bản chụp bất biến (`ResolveTimelineAuthority` snapshot) và chuyển vào worker ngoài luồng. `RecoveryAssessmentWorker` tuyệt đối không gọi `fusionscript` hoặc `query_active_timeline_authority()` trực tiếp. Khi không lấy được thẩm quyền an toàn, hiển thị trạng thái chưa sẵn sàng (`authority_available=False`) và phân loại bảo thủ.
  2. **Phân biệt rạch ròi lỗi truy cập I/O với biến dạng dữ liệu (Artifact Corruption vs I/O Failure):** Lỗi quyền (`EACCES`), lỗi I/O phần cứng/mạng (`EIO`), hoặc gián đoạn đọc không cấu thành bằng chứng hồ sơ bị biến dạng. Thiết lập trạng thái hiển thị thất bại giới hạn trần (`ASSESSMENT_UNAVAILABLE_READ_ERROR`) riêng biệt; tuyệt đối không gắn nhãn cách ly forensic nếu chưa có bằng chứng cấu trúc tệp bị hỏng.
  3. **Thao tác UI thuần túy chỉ đọc, không ngụ ý thực thi cấm (Read-Only Actions & Honest Status Copy):** Thao tác Làm mới (Refresh / F5) tuyệt đối không chiếm giữ khóa cố vấn (non-retaining lock probe). Loại bỏ toàn bộ các tuyên bố vô căn cứ như "phiên thực thi bình thường" hay "timeline nguyên vẹn 100%"; làm rõ biên nhận và nhật ký chỉ là hồ sơ quá khứ của ứng dụng. Mọi xác nhận của người vận hành trong tương lai chỉ mang tính chất thông tin ghi nhận trên UI, không phát lệnh thi công hay sửa đổi timeline DaVinci Resolve.

---

## 1. ĐỐI CHIẾU CALLGRAPH UI HIỆN TRẠNG & ĐỊNH TUYẾN ĐƠN LUỒNG RESOLVE

### 1.1. Hạ Tầng Mã Nguồn Hiện Hữu (`[CODE_VERIFIED]`)

```
+-----------------------------------------------------------------------------------------------------------------------+
|                                    HẠ TẦNG ĐÃ NGHIỆM THU (CODE_VERIFIED in G07-07A7-I)                                 |
+-----------------------------------------------------------------------------------------------------------------------+
 [src/core/contracts/recovery_assessment_contracts.py] [CODE_VERIFIED]
      |-- DTOs: RecoveryAssessment, CandidateCompensatingAction, AmbiguousActionDetail, TargetIdentityMatchReport
      |-- Hằng số an toàn: MAX_TORN_TAIL_PREVIEW_BYTES (256), MAX_PARAMS_SUMMARY_CHARS (512), MAX_EXPLANATION_CHARS (2048)
      +-- Hàm làm sạch: sanitize_diagnostics_string (loại bỏ NUL, ANSI escape, unprintable chars, chống UI injection)

 [src/services/recovery_classifier_service.py] [CODE_VERIFIED]
      +-- RecoveryClassifierService.classify(): Phân loại tất định 8 trạng thái, trích xuất LIFO candidate compensations

 [src/services/recovery_coordinator.py] [CODE_VERIFIED]
      |-- RecoveryCoordinator(base_dir): probe_advisory_lock, verify_idempotency_index, assess_execution
      +-- Zero-Mutation Guarantee: 100% tệp đĩa (receipt, journal, lock, idempotency) nguyên vẹn sau khi đối soát

 [src/core/resolve_bridge.py] [CODE_VERIFIED]
      +-- query_active_timeline_authority(): Đọc project_id, timeline_id, timeline_name, RationalFPS (single-thread bound)

 [src/ui/director/director_window.py] [CODE_VERIFIED]
      |-- _query_resolve_timeline_authority(): Gọi ResolveBridge trên luồng chủ sở hữu (Owner/Main GUI Thread)
      +-- Quản lý vòng đời worker: _preflight_workers, shutdown_analysis_and_preflight_workers()

 [src/ui/director/pages/build_page.py] [CODE_VERIFIED]
      |-- BuildPage: Bước 4 trong quy trình Director (BuildSummaryCard, QTableWidget table_readiness, execution lock banner)
      +-- show_readiness_result(result, target_authority): Render 5-6 tầng quan sát, ready_to_execute=False bắt buộc
```

### 1.2. Luồng Gọi Giao Diện & Ranh Giới Đơn Luồng Đề Xuất (`[PROPOSED]`)

```
+-----------------------------------------------------------------------------------------------------------------------+
|                           G07-07A8-D-R1: ĐỊNH TUYẾN ĐƠN LUỒNG & GIAO DIỆN CHỈ ĐỌC (PROPOSED)                          |
+-----------------------------------------------------------------------------------------------------------------------+
 [Luồng Chủ Sở Hữu: Main GUI Thread / DirectorWindow / BuildPage]
      |
      |-- 1. Khi người vận hành bấm "Kiểm tra phiên thi công trước" (btn_inspect_recovery) hoặc F5:
      |         |-- Gọi _query_resolve_timeline_authority() TRÊN LUỒNG CHỦ SỞ HỮU (Owner Thread)
      |         |-- Thu nhận bản chụp bất biến: authority_snapshot (ResolveTimelineAuthority: ok, project_id, timeline_id)
      |         |   (Nếu Resolve chưa bật hoặc lỗi IPC -> authority_snapshot = ResolveTimelineAuthority(ok=False, ...))
      |         v
      |-- 2. Khởi tạo RecoveryAssessmentWorker(base_dir, execution_id, authority_snapshot):
      |         |-- Chuyển authority_snapshot sang worker dưới dạng DTO bất biến (Frozen/Immutable)
      |         +-- Khởi chạy worker.start()
      |
      v
 [Luồng Phụ Ngoài GUI: RecoveryAssessmentWorker (QThread tách biệt, TUYỆT ĐỐI KHÔNG GỌI RESOLVE IPC)]
      |
      |-- Phương thức run():
      |         |-- 1. Trích xuất (active_project_id, active_timeline_id) từ authority_snapshot đã nhận
      |         |      (Nếu authority_snapshot.ok == False -> truyền None, None để phân loại bảo thủ)
      |         |-- 2. Khởi tạo RecoveryCoordinator(base_dir)
      |         |-- 3. Gọi coordinator.assess_execution(execution_id, active_project_id, active_timeline_id)
      |         |      + Thăm dò OS advisory lock non-blocking: kiểm tra xong nhả khóa (LOCK_UN) và đóng fd ngay lập tức.
      |         |      + Đọc và phân loại tất định dựa trên hồ sơ đĩa.
      |         |-- 4. Bắt lỗi ngoại lệ:
      |         |      + Nếu gặp PermissionError (EACCES) hoặc OSError (EIO) khi đọc tệp -> phát assessment_io_error(str)
      |         |      + Nếu hoàn tất phân loại -> phát assessment_completed(RecoveryAssessment)
      |         +-- 5. Kết thúc worker cleanly.
      |
      v
 [Luồng Chủ Sở Hữu: Main GUI Thread / RecoveryAssessmentDialog]
      |
      |-- Nhận signal assessment_completed(RecoveryAssessment) hoặc assessment_io_error(str):
      |         |-- Cập nhật Header Banner, Thẻ Danh tính Đích, Bảng Ứng viên Bù trừ, Khung Dòng rách
      |         |-- Nếu là lỗi I/O -> hiển thị Khung Báo lỗi Truy cập Tệp (Assessment Unavailable Box), KHÔNG báo hỏng forensic
      |         +-- Đảm bảo toàn bộ tương tác đều là CHỈ ĐỌC; 0 NÚT EXECUTE / ZERO MUTATION
```

---

## 2. KIẾN TRÚC KHUNG HIỂN THỊ BOUNDED (BOUNDED UI SPECIFICATION)

Khung giao diện phục hồi được thiết kế theo nguyên tắc **bảo mật theo chiều sâu, giới hạn trần hiển thị, văn bản thuần (PlainText) chống chèn mã, và bất biến hoàn toàn đối với trạng thái hệ thống**:

```
+---------------------------------------------------------------------------------------------------+
|  [!] ĐỐI SOÁT PHỤC HỒI PHIÊN THI CÔNG (READ-ONLY INSPECTION)                             [Đóng X] |
+---------------------------------------------------------------------------------------------------+
|  HEADER BANNER (Mã màu theo Crash State):                                                         |
|  ● Trạng thái: STATE_COMMITTED_CANDIDATE_COMPENSATION                                             |
|  Khuyến nghị: CANDIDATE_COMPENSATION_FOR_OPERATOR_REVIEW                                          |
|  "Ứng dụng đã ghi nhận 2 thao tác commit trước khi dừng. Yêu cầu kiểm tra trực quan trên Resolve."|
+---------------------------------------------------------------------------------------------------+
|  THÔNG TIN HỒ SƠ & ĐỐI CHIẾU DANH TÍNH RESOLVE (TARGET IDENTITY MATCHING):                       |
|  - Mã phiên (Execution ID): exec_20261102_a1b2...   | Khóa Idempotency: 7f8a9b0c1d2e... (Khớp)   |
|  - Quyền sở hữu OS Lock:    RẢNH (Free)             | PID ghi nhận chẩn đoán: 12345 (Tham khảo)  |
|  - Dự án (Project):        Receipt: Proj_Alpha     | Lấy mẫu Resolve: Proj_Alpha (KHỚP ✓)       |
|  - Timeline:               Receipt: TL_Main        | Lấy mẫu Resolve: TL_Main (KHỚP ✓)          |
|  [Trạng thái danh tính: ĐÍCH DANH CHUẨN TẮC - KHÔNG PHÁT HIỆN TRÔI LỆCH (No Drift)]              |
+---------------------------------------------------------------------------------------------------+
|  DANH SÁCH HÀNH ĐỘNG ỨNG VIÊN BÙ TRỪ (CANDIDATE COMPENSATIONS - LIFO ORDER): (2 mục)             |
|  +-----+---------------+----------------+-------+-------------+---------------------------------+ |
|  | Seq | Loại thao tác | Resolve Item ID| Track | Clip Name   | Tóm tắt tham số (Sanitized)     | |
|  +-----+---------------+----------------+-------+-------------+---------------------------------+ |
|  |  4  | INSERT_CLIP   | item_9982      | V1-T1 | broll_02.mp4| start=120, end=240, track=1    | |
|  |  2  | INSERT_CLIP   | item_9981      | V1-T1 | intro_01.mp4| start=0, end=120, track=1      | |
|  +-----+---------------+----------------+-------+-------------+---------------------------------+ |
|  * LƯU Ý BẢO MẬT: Bảng trên là dữ liệu ghi nhận trong quá khứ của ứng dụng.                      |
|    Không bảo đảm timeline DaVinci Resolve hiện tại vẫn còn chứa các clip này.                    |
+---------------------------------------------------------------------------------------------------+
|  CHẨN ĐOÁN DÒNG CUỐI / DỊ THƯỜNG (DIAGNOSTICS & TAIL OBSERVATION):                                |
|  - Dòng cuối nhật ký: Hợp lệ (ACTION_COMMITTED) | Tổng số bản ghi nhật ký: 5 dòng                |
|  - Dòng rách (Torn-tail): Không phát hiện dị thường                                              |
+---------------------------------------------------------------------------------------------------+
|  [ 🔒 THI CÔNG BÙ TRỪ ĐANG KHÓA ]             [ Làm mới (F5) ]                 [ Đóng giao diện ] |
+---------------------------------------------------------------------------------------------------+
```

### 2.1. Chi tiết các Thành phần Giao diện & Ràng buộc Kỹ thuật

1. **Header Banner (`QFrame` có mã màu động):**
   - Đặt nền màu theo trạng thái phân loại sự cố:
     - Xanh lục nhạt (`#064e3b` / viền `#059669`): `STATE_CLEAN_TERMINAL`.
     - Vàng hổ phách (`#78350f` / viền `#d97706`): `STATE_INITIALIZED_NO_JOURNAL`, `STATE_COMMITTED_CANDIDATE_COMPENSATION`, `STATE_TORN_TAIL_DETECTED`.
     - Đỏ sẫm (`#7f1d1d` / viền `#dc2626`): `STATE_AMBIGUOUS_IN_DOUBT`, `STATE_ARTIFACT_CORRUPTED_OR_MISSING`.
     - Xám than trung tính (`#374151` / viền `#6b7280`): `ASSESSMENT_UNAVAILABLE_READ_ERROR` (Lỗi I/O, không phải hỏng dữ liệu).
     - Tím chàm (`#312e81` / viền `#6366f1`): `STATE_CLEAN_ACTIVE_LOCKED` (tiến trình khác đang giữ khóa).
   - Nhãn khuyến nghị (`lbl_recommendation`): In hoa, font đậm 12pt, `setTextFormat(Qt.TextFormat.PlainText)`.
   - Nhãn giải thích (`lbl_explanation`): Tối đa 2048 ký tự, tự động ngắt dòng (`wordWrap=True`), bắt buộc dùng `PlainText` để chống HTML/ANSI injection.

2. **Thẻ Đối chiếu Danh tính Đích (`TargetIdentityCard`):**
   - Hiển thị song song thông tin từ `Receipt` và bản chụp thẩm quyền từ `authority_snapshot` được lấy an toàn từ luồng chủ sở hữu.
   - Xử lý các kịch bản danh tính:
     - **Không trôi lệch (`drift_detected == False` & `authority_available == True`):** Hiển thị nhãn xanh: *"ĐÍCH DANH KHỚP CHUẨN TẮC"*.
     - **Phát hiện trôi lệch (`drift_detected == True`):** Người dùng đã chuyển sang Project/Timeline khác trong Resolve. Hiển thị banner đỏ:
       *"CẢNH BÁO: Timeline đang mở trong Resolve không khớp với Timeline phiên sự cố. Mọi so sánh hoặc xem xét ứng viên bù trừ bị vô hiệu hóa."*
     - **Thẩm quyền chưa sẵn sàng (`authority_available == False`):** Resolve chưa mở hoặc lỗi truy vấn từ luồng chủ sở hữu. Hiển thị banner xám:
       *"Thẩm quyền DaVinci Resolve hiện chưa sẵn sàng. Không thể đối chiếu danh tính timeline; hệ thống đánh giá ở chế độ bảo thủ không có thẩm quyền đích."*

3. **Bảng Ứng viên Bù trừ (`table_candidate_compensations`):**
   - Thứ tự LIFO (thao tác mới nhất nằm trên cùng).
   - Cột: `Seq` (STT), `Loại thao tác` (Action Type), `Resolve Item ID`, `Track`, `Tên Clip`, `Tóm tắt tham số`.
   - Giới hạn trần hiển thị: Tối đa `MAX_UI_ROWS = 100` dòng. Vượt quá sẽ hiển thị nhãn: `"Hiển thị 100/tổng số N ứng viên"`.
   - Cờ ô: `ItemIsEditable = False`, `setTextFormat(Qt.TextFormat.PlainText)`.
   - Lưu ý bắt buộc dưới chân bảng: *"LƯU Ý BẢO MẬT: Bảng trên là dữ liệu ghi nhận trong quá khứ của ứng dụng. Không bảo đảm timeline DaVinci Resolve hiện tại vẫn còn chứa các clip này."*

4. **Khung Cảnh báo Bất định (`InDoubtWarningBox`):**
   - Chỉ hiển thị khi `classified_state == "STATE_AMBIGUOUS_IN_DOUBT"`.
   - Nội dung cảnh báo:
     *"RANH GIỚI BẤT ĐỊNH: Tiến trình dừng ngay sau lệnh ACTION_PENDING. Hệ thống không thể chứng minh từ đĩa xem clip đã được Resolve chèn hay chưa. TUYỆT ĐỐI CẤM TỰ ĐỘNG CHẠY LẠI HOẶC XÓA MÙ. Người vận hành bắt buộc phải đối chiếu trực quan bằng mắt trên DaVinci Resolve."*

5. **Khung Chẩn đoán Dòng rách (`TornTailDiagnosticBox`):**
   - Chỉ hiển thị khi `torn_tail_detected == True`.
   - Hiển thị: số dòng phát hiện, số byte ngắt quãng (`torn_tail_byte_count`), mã băm SHA-256 hex (`torn_tail_sha256`).
   - Bản xem trước an toàn: `torn_tail_preview_escaped` (tối đa 256 ký tự).
   - Tuyệt đối cấm giải mã raw binary trực tiếp lên widget mà không qua hàm làm sạch chuỗi.

6. **Khung Báo Lỗi Truy Cập Tệp Giới Hạn Trần (`AssessmentUnavailableBox` - MỚI):**
   - Hiển thị khi worker gặp lỗi hệ điều hành `PermissionError (EACCES)` hoặc `OSError (EIO)`.
   - Tách biệt hoàn toàn với lỗi hỏng dữ liệu cấu trúc.
   - Nội dung hiển thị:
     *"KHÔNG THỂ ĐỌC HỒ SƠ LƯU TRỮ: Gặp lỗi quyền truy cập hệ thống tệp hoặc lỗi I/O (EACCES/EIO). Không đủ bằng chứng để xác định hồ sơ có bị biến dạng hay không. Trạng thái phiên hiện KHÔNG XÁC ĐỊNH (Unavailable). Vui lòng kiểm tra quyền truy cập tệp và thiết bị lưu trữ trước khi làm mới."*

---

## 3. PHÂN ĐỊNH TRỰC QUAN CÁC NHÓM TRẠNG THÁI VẬN HÀNH & NGUYÊN TẮC BẢO MẬT

Để người vận hành không bị nhầm lẫn giữa các mức độ rủi ro, hệ thống phân chia rõ ràng các nhóm trạng thái hiển thị:

| Nhóm trạng thái | Trạng thái kỹ thuật | Mã màu giao diện | Thông điệp hiển thị chuẩn tắc cho người vận hành | Quyền hạn tương tác UI |
| :--- | :--- | :--- | :--- | :--- |
| **Nhóm 1: Khóa cố vấn đang bị chiếm** | `STATE_CLEAN_ACTIVE_LOCKED` | Tím chàm (`#6366f1`) | *"Khóa cố vấn hệ điều hành đang bị chiếm giữ bởi tiến trình PID: .... Đang có tiến trình khác giữ quyền sở hữu phiên. CẤM CAN THIỆP."* *(Lưu ý: Không suy diễn tiến trình đang chạy bình thường hay bị treo).* | Toàn bộ bảng điều khiển khóa. Chỉ cho phép bấm "Đóng". |
| **Nhóm 2: Hồ sơ sạch theo biên nhận** | `STATE_CLEAN_TERMINAL`, `STATE_INITIALIZED_NO_JOURNAL` | Xanh lục (`#10b981`) / Vàng nhạt | *"Hồ sơ ứng dụng ghi nhận phiên đã kết thúc sạch sẽ (TERMINAL) hoặc chưa ghi nhật ký. Lưu ý: Biên nhận và nhật ký chỉ là lịch sử ứng dụng ghi nhận trong quá khứ, KHÔNG PHẢI là bằng chứng xác thực độc lập rằng timeline DaVinci Resolve hiện tại còn nguyên vẹn 100% hoặc chưa bị chỉnh sửa."* | Nút "Đóng", Nút "Làm mới (F5)". Bảng ứng viên rỗng. |
| **Nhóm 3A: Biến dạng dữ liệu cấu trúc (Forensic Quarantine)** | `STATE_ARTIFACT_CORRUPTED_OR_MISSING` | Đỏ sẫm (`#dc2626`) | *"Hồ sơ lưu trữ bị biến dạng cấu trúc (JSON hỏng, thiếu trường bắt buộc, hoặc chỉ mục idempotency không khớp). Đóng băng hiện trường để điều tra forensic. CẤM TẠO LẠI HOẶC CHẠY ĐÈ."* | Khóa toàn bộ thao tác. Hiển thị đường dẫn tệp bị lỗi phục vụ chẩn đoán kỹ thuật. |
| **Nhóm 3B: Thất bại do truy cập I/O / Quyền đọc (Assessment Unavailable)** | `ASSESSMENT_UNAVAILABLE_READ_ERROR` | Xám than trung tính (`#6b7280`) | *"Không thể đọc hồ sơ do lỗi quyền truy cập hệ thống tệp (EACCES) hoặc lỗi I/O (EIO). Chưa có bằng chứng cấu trúc tệp bị hỏng. Trạng thái phiên hiện KHÔNG XÁC ĐỊNH. Khóa thi công; vui lòng kiểm tra quyền tệp."* | Khóa thao tác thi công. Cho phép bấm "Làm mới (F5)" hoặc "Đóng". |
| **Nhóm 4: Ranh giới bất định** | `STATE_AMBIGUOUS_IN_DOUBT` | Đỏ cam (`#ea580c`) | *"Không thể xác minh từ đĩa trạng thái bước cuối (ACTION_PENDING). Bắt buộc người vận hành đối chiếu trực quan bằng mắt trên DaVinci Resolve."* | Cảnh báo nổi bật. Khóa cứng mọi nút tự động hóa hoặc retry. |
| **Nhóm 5: Đã commit một phần** | `STATE_COMMITTED_CANDIDATE_COMPENSATION`, `STATE_TORN_TAIL_DETECTED` | Vàng hổ phách (`#d97706`) | *"Ứng dụng ghi nhận N thao tác đã commit trước khi dừng. Đây là danh sách ứng viên bù trừ để xem xét. Không cam kết timeline Resolve hiện tại còn giữ nguyên các clip."* | Hiển thị bảng ứng viên LIFO. Khóa nút thi công bù trừ (`ready_to_execute=False`). |

---

## 4. QUẢN LÝ VÒNG ĐỜI GIAO DIỆN CHỈ ĐỌC, VÔ HIỆU HÓA STALE & TRÔI DANH TÍNH

### 4.1. Kiến Trúc Luồng Phân Tách An Toàn (Serialized Gateway & Off-GUI Worker)
1. **Bảo toàn Ranh giới Đơn Luồng Resolve (Resolve Thread Affinity):**
   - Mọi tương tác với API DaVinci Resolve (thông qua `query_active_timeline_authority`) bắt buộc phải được kích hoạt trên luồng chủ sở hữu được cấp phép (Main UI Thread trong ngữ cảnh Qt Director).
   - Trước khi khởi động worker, luồng giao diện lấy mẫu thẩm quyền Resolve và đóng gói thành một đối tượng bất biến `authority_snapshot` (`ResolveTimelineAuthority`).
   - `RecoveryAssessmentWorker` kế thừa `QThread`, chỉ nhận các tham số nguyên thủy và DTO bất biến:
     ```python
     class RecoveryAssessmentWorker(QThread):
         assessment_completed = pyqtSignal(object)  # RecoveryAssessment
         assessment_io_error = pyqtSignal(str)      # Diagnostic message

         def __init__(
             self,
             base_dir: Path,
             execution_id: str,
             authority_snapshot: Optional[ResolveTimelineAuthority],
             parent: Optional[QObject] = None,
         ):
             super().__init__(parent)
             self._base_dir = base_dir
             self._execution_id = execution_id
             self._authority_snapshot = authority_snapshot
     ```
   - Trong `run()`:
     - Worker **TUYỆT ĐỐI KHÔNG IMPORT HOẶC GỌI BẤT KỲ HÀM NÀO CỦA RESOLVE IPC**.
     - Nếu `self._authority_snapshot` hợp lệ (`ok=True`), trích xuất `active_project_id = self._authority_snapshot.project_id`, `active_timeline_id = self._authority_snapshot.timeline_id`.
     - Nếu `self._authority_snapshot` là `None` hoặc `ok=False`, thiết lập cả hai là `None` để bộ phân loại thực hiện đánh giá bảo thủ không có thẩm quyền đích (`authority_available=False`).
     - Khởi tạo `coordinator = RecoveryCoordinator(self._base_dir)`.
     - Bắt `PermissionError` (EACCES) và `OSError` (EIO) riêng biệt để phát `assessment_io_error`.
     - Gọi `coordinator.assess_execution(...)` và phát `assessment_completed`.

2. **Thăm Dò Khóa Cố Vấn Không Chiếm Giữ (Non-Retaining Advisory Lock Probe):**
   - Thao tác kiểm tra khóa cố vấn trong `RecoveryCoordinator.probe_advisory_lock()` được thiết kế hoàn toàn không chiếm giữ khóa:
     - Mở tệp với cờ đọc/ghi an toàn.
     - Thử khóa thăm dò không chặn: `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)` trên POSIX (hoặc `msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)` trên Windows).
     - Nếu khóa thành công (nghĩa là không có tiến trình nào khác đang giữ khóa): **LẬP TỨC NHẢ KHÓA** (`fcntl.flock(fd, fcntl.LOCK_UN)`) và đóng `fd` ngay lập tức.
     - Tuyệt đối không giữ khóa sau khi hàm thăm dò trả về.
     - Thao tác "Làm mới (Refresh)" hoặc F5 chỉ lặp lại việc thăm dò không chiếm giữ này, bảo đảm không bao giờ gây khóa chặn các tiến trình hợp lệ khác.

### 4.2. Cơ Chế Vô Hiệu Hóa Đánh Giá Lỗi Thời (Stale Assessment Invalidation)
- Một bản đánh giá `RecoveryAssessment` được xem là **ảnh chụp tại một thời điểm (point-in-time snapshot)**.
- Khi người vận hành bấm nút "Làm mới (Refresh)" (hoặc phím F5):
  1. Hộp thoại thu hồi bản đánh giá hiện tại (`self._current_assessment = None`).
  2. Vô hiệu hóa tạm thời bảng dữ liệu cũ và hiển thị trạng thái đang đối soát.
  3. Lấy lại bản chụp thẩm quyền Resolve mới nhất trên luồng chính (`_query_resolve_timeline_authority()`).
  4. Khởi chạy worker mới để nạp và phân loại lại từ đĩa.
- Nếu người vận hành chuyển sang Timeline khác trên DaVinci Resolve trong lúc hộp thoại đang mở:
  - Khi làm mới, sự trôi lệch (`drift_detected=True`) sẽ lập tức được hiển thị với banner cảnh báo màu đỏ, ngăn chặn mọi hiểu lầm về đối chiếu timeline.

### 4.3. Phân Định Lỗi Hệ Thống Tệp: I/O Tạm Thời vs Biến Dạng Cấu Trúc
- **Lỗi I/O / Quyền truy cập (`PermissionError / OSError`):**
  - Xảy ra khi tiến trình bị từ chối quyền đọc (`EACCES`), hệ thống tệp mạng bị ngắt kết nối (`EIO`), hoặc tệp đang bị OS lock độc quyền tạm thời.
  - Đây **KHÔNG PHẢI** là bằng chứng hồ sơ bị biến dạng trên đĩa.
  - Phản ứng hệ thống: Phát tín hiệu `assessment_io_error`, hiển thị Khung Báo Lỗi Truy Cập Tệp (`AssessmentUnavailableBox`) với mã màu xám trung tính, yêu cầu người vận hành kiểm tra phân quyền. Trạng thái là `ASSESSMENT_UNAVAILABLE`, fail-closed, không cho phép thi công đè.
- **Biến Dạng Dữ Liệu Cấu Trúc Thật Sự (`STATE_ARTIFACT_CORRUPTED_OR_MISSING`):**
  - Tệp đọc được nhưng cấu trúc JSON bị hỏng cú pháp (`JSONDecodeError`), thiếu các khóa bắt buộc theo schema, hoặc chỉ mục idempotency không khớp với bản ghi receipt.
  - Phản ứng hệ thống: Kích hoạt nhóm 3A (Đỏ sẫm), yêu cầu đóng băng hiện trường và cách ly forensic.

---

## 5. BẢO MẬT GIAO DIỆN, CHỐNG UI INJECTION & XÁC NHẬN PHI THỰC THI

### 5.1. Phòng Vệ Chống Tấn Công Chèn Mã Giao Diện (Anti-UI Injection)
1. **Ép Buộc Định Dạng Văn Bản Thuần (Strict Plain Text Policy):**
   - Mọi widget hiển thị chuỗi chẩn đoán hoặc dữ liệu lấy từ tệp nhật ký/biên nhận (`lbl_explanation`, `lbl_recommendation`, các ô trong `table_candidate_compensations`, `lbl_torn_tail_preview`, `lbl_io_error`) bắt buộc phải thiết lập:
     ```python
     widget.setTextFormat(Qt.TextFormat.PlainText)
     ```
   - Ngăn chặn hoàn toàn việc thẻ HTML (ví dụ `<script>`, `<iframe>`, `<b>`) hoặc ký tự điều khiển ANSI escape can thiệp vào bộ dựng giao diện của Qt.
2. **Ẩn Dấu Đường Dẫn Nhạy Cảm (Path Sanitization):**
   - Đường dẫn tệp video nguồn hoặc catalog asset được rút gọn hiển thị tên tệp cơ sở (basename) kèm thư mục cha gần nhất (ví dụ: `.../Footage/interview_01.mp4`).

### 5.2. Nguyên Tắc Xác Nhận Phi Thực Thi của Người Vận Hành (Non-Executable Operator Intent)
- **Bản chất của các xác nhận từ người vận hành:**
  - Nếu trong tương lai có thêm hộp kiểm (checkbox) hoặc nút bấm xác nhận như *"Tôi đã kiểm tra trực quan trên Resolve"*:
  - Hành động này **CHỈ MANG TÍNH CHẤT THÔNG TIN VÀ GHI NHẬN TRÊN GIAO DIỆN (INFORMATIONAL ONLY)**.
  - Tuyệt đối không nối bất kỳ hàm thực thi nào, không tạo đối tượng `TimelineMutationExecutor`, không gọi các API đột biến Resolve (`InsertClips`, `DeleteClips`), và **KHÔNG THỂ THAY ĐỔI CỜ `ready_to_execute=False`**.
- **Cấm hoàn toàn các nút đột biến:**
  - Nút "Thực thi hoàn tác / Chèn bù trừ": Hoàn toàn không tồn tại hoặc bị khóa cứng (disabled vĩnh viễn) trên giao diện.
  - Nhãn trạng thái hiển thị rõ ràng: `🔒 Thi công timeline đang khóa (ready_to_execute=False)`.

---

## 6. MA TRẬN KIỂM CHỨNG HỮU HẠN GIAO DIỆN (FINITE UI ACCEPTANCE MATRIX)

Ma trận 12 kịch bản kiểm chứng giao diện độc lập (`TC-UI-REC-01` đến `TC-UI-REC-12`):

| Mã kiểm thử | Kịch bản kiểm chứng giao diện | Đầu vào thử nghiệm | Trạng thái hiển thị kỳ vọng trên UI | Ràng buộc quyền hạn & an toàn |
| :--- | :--- | :--- | :--- | :--- |
| **TC-UI-REC-01** | **Hiển thị Clean Terminal** | `STATE_CLEAN_TERMINAL`, lock: False, candidate: 0 | Banner xanh lục, giải thích phiên sạch; ghi rõ receipt là hồ sơ quá khứ, không cam kết timeline Resolve hiện tại. | 0 nút bù trừ; ready_to_execute=False |
| **TC-UI-REC-02** | **Hiển thị Active Process Locked** | `STATE_CLEAN_ACTIVE_LOCKED`, lock: True, PID: 12345 | Banner tím, cảnh báo tiến trình khác đang giữ khóa OS; không tuyên bố tiến trình chạy bình thường. | Khóa mọi điều khiển; chỉ có nút Đóng |
| **TC-UI-REC-03** | **Hiển thị Candidate Compensations** | `STATE_COMMITTED_CANDIDATE_COMPENSATION`, 2 clips | Banner vàng, bảng hiển thị đúng 2 dòng LIFO; ghi chú cảnh báo clip có thể không còn trên timeline. | Nút bù trừ bị khóa cứng (disabled); cấm Resolve |
| **TC-UI-REC-04** | **Hiển thị In-Doubt Ambiguity** | `STATE_AMBIGUOUS_IN_DOUBT`, 1 pending entry | Banner đỏ cam, khung cảnh báo bất định nổi bật, yêu cầu nhìn Resolve; cấm chạy lại kịch bản. | Khóa toàn bộ thao tác tự động; cấm retry |
| **TC-UI-REC-05** | **Hiển thị Torn-Tail Diagnostics** | `STATE_TORN_TAIL_DETECTED`, preview escaped | Hiển thị byte count, SHA-256 hex, đoạn preview an toàn (<=256 ký tự). | PlainText format; không lỗi render; 0 crash |
| **TC-UI-REC-06** | **Hiển thị Artifacts Corrupted** | `STATE_ARTIFACT_CORRUPTED_OR_MISSING` (JSON/Idemp hỏng) | Banner đỏ sẫm, yêu cầu đóng băng forensic; cấm chạy lại kịch bản. | Khóa hoàn toàn kịch bản thi công |
| **TC-UI-REC-07** | **Cảnh báo Target Identity Drift** | `drift_detected=True`, Resolve mở timeline khác | Cảnh báo đỏ: Lệch danh tính timeline; hiển thị rõ cả 2 timeline IDs. | Khóa cứng mọi đề xuất bù trừ |
| **TC-UI-REC-08** | **Resolve Chưa Sẵn Sàng (Unavailable)** | `authority_available=False` trong snapshot | Hiển thị Resolve chưa bật/chưa mở timeline; đánh giá bảo thủ không có thẩm quyền đích. | Báo trạng thái chưa sẵn sàng |
| **TC-UI-REC-09** | **Phòng vệ UI Injection (PlainText)** | Dữ liệu chứa `<script>alert(1)</script>\x00\x1b[31m` | Chuỗi hiển thị nguyên văn literal, không chạy script, không đổi màu ANSI. | An toàn tuyệt đối 100% |
| **TC-UI-REC-10** | **Giới hạn Trần Bảng (Max Rows Cap)** | 150 candidate actions | Bảng chỉ render đúng 100 dòng; nhãn: "Hiển thị 100/tổng số 150 dòng". | 0 tràn bộ nhớ; 0 giật lag giao diện |
| **TC-UI-REC-11** | **Xử Lý Lỗi I/O Hệ Điều Hành (EACCES/EIO)** | Worker gặp `PermissionError` hoặc `OSError` | Hiển thị Khung Báo Lỗi Truy Cập Tệp (Xám than); KHÔNG tuyên bố hồ sơ bị biến dạng forensic. | Khóa thi công; cho phép Đóng hoặc Thử lại |
| **TC-UI-REC-12** | **Đơn Luồng Resolve & Khóa Không Chiếm Giữ** | Kiểm tra luồng gọi Resolve & thăm dò lock | Resolve API chỉ được gọi trên Main UI Thread; Worker 0 Resolve imports; lock probe không giữ khóa sau thăm dò. | Bảo toàn đơn luồng Resolve; 0 retain lock |

---

## 7. ĐỀ XUẤT DUY NHẤT LÁT CẮT CODE KẾ TIẾP: G07-07A8-I
*(Proposed Next Code Implementation Slice: Read-Only Recovery Assessment Presentation)*

Để hiện thực hóa giao diện trình bày đối soát phục hồi chỉ đọc mà **tuyệt đối không gây đột biến timeline Resolve, không vi phạm đơn luồng Resolve**, và **bảo toàn 100% ranh giới an toàn**, đề xuất duy nhất một lát cắt code tiếp theo:

### 7.1. Định Danh & Phạm Vi Cho Phép (Scope Allowlist)
- **Tên lát cắt:** `G07-07A8-I`
- **Chế độ:** `READ_ONLY_RECOVERY_PRESENTATION_ONLY`
- **Phạm vi cho phép (Đúng 3 production modules giao diện và worker chỉ đọc):**
  1. `src/ui/director/widgets/recovery_assessment_dialog.py` (MỚI): 
     - Hộp thoại `RecoveryAssessmentDialog` (kế thừa `QDialog` hoặc `QWidget` độc lập):
     - Chịu trách nhiệm render toàn bộ DTO `RecoveryAssessment` theo cấu trúc Bounded Layout tại Mục 2.
     - Sử dụng `setTextFormat(Qt.TextFormat.PlainText)` cho toàn bộ các trường chẩn đoán.
     - Hiển thị bảng ứng viên bù trừ LIFO, khung cảnh báo bất định, khung chẩn đoán dòng rách an toàn, và khung lỗi truy cập tệp I/O.
  2. `src/ui/director/workers/recovery_assessment_worker.py` (MỚI):
     - `QThread` chuyên biệt thực thi việc đọc hồ sơ và phân loại phục hồi ngoài luồng GUI:
     - Nhận `authority_snapshot` bất biến từ luồng chính; **TUYỆT ĐỐI KHÔNG GỌI FUSIONSCRIPT HAY RESOLVE IPC**.
     - Gọi `RecoveryCoordinator.assess_execution()` và phát signal `assessment_completed(RecoveryAssessment)` hoặc `assessment_io_error(str)`.
     - Tuyệt đối không thực hiện bất kỳ thao tác ghi đĩa hay đột biến nào.
  3. `src/ui/director/pages/build_page.py` (CẬP NHẬT):
     - Bổ sung nút bấm `"Kiểm tra phiên thi công cũ"` (`btn_inspect_recovery`) trên thẻ Bước 4.
     - Khi bấm nút: Lấy mẫu thẩm quyền Resolve trên luồng chính (`_query_resolve_timeline_authority()`), đóng gói thành `authority_snapshot`, khởi tạo `RecoveryAssessmentWorker` off-GUI, và mở `RecoveryAssessmentDialog` hiển thị kết quả.
     - Bảo toàn nguyên vẹn 100% các thành phần kiểm tra tiền kiểm `show_readiness_result()` hiện hữu.
- **Ràng buộc an toàn bất biến:**
  - **TUYỆT ĐỐI 0 LỆNH RESOLVE MUTATION, 0 LỆNH DELETE CLIPS.**
  - `ready_to_execute=False` được bảo toàn nguyên vẹn 100%.
  - Nút "Thực thi bù trừ" hoặc "Rollback timeline": **HOÀN TOÀN KHÔNG TỒN TẠI TRONG LÁT CẮT NÀY**.
  - Không sửa đổi receipt, journal, lock file, hoặc idempotency index để "thử nghiệm giao diện".
  - Giữ nguyên toàn bộ dirty/untracked user work.

---

## 8. KẾT LUẬN & TRẠNG THÁI BÀN GIAO CHO CODEX

Tài liệu thiết kế kiến trúc giao diện hiệu đính `07A8-recovery-assessment-ui-design.md` (Revision 1) đã khắc phục toàn diện và triệt để cả 3 điểm lưu ý từ Tech Lead Codex:
1. **Tuân thủ đơn luồng Resolve:** Định tuyến lấy mẫu thẩm quyền Resolve trên luồng chính qua gateway tuần tự hóa; chuyển bản chụp bất biến sang worker ngoài luồng; cấm hoàn toàn worker gọi fusionscript IPC.
2. **Phân biệt rạch ròi lỗi truy cập I/O:** Tách biệt lỗi quyền/I/O (`EACCES/EIO`) với lỗi hỏng cấu trúc; không quy chụp thành biến dạng forensic; thiết lập trạng thái và thông điệp thất bại giới hạn trần (`ASSESSMENT_UNAVAILABLE_READ_ERROR`).
3. **Thao tác thuần túy chỉ đọc & chuẩn hóa ngôn từ:** Khóa thăm dò không chiếm giữ (non-retaining lock probe); loại bỏ tuyên bố "chạy bình thường" và "timeline nguyên vẹn 100%"; khẳng định biên nhận/nhật ký chỉ là hồ sơ quá khứ; xác nhận của người vận hành là thông tin thuần túy phi thực thi.

Tài liệu sẵn sàng bàn giao cho Tech Lead Codex thẩm định và phê duyệt cho lát cắt `G07-07A8-I`.
