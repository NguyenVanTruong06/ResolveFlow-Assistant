# G07-07A5-D: THIẾT KẾ RANH GIỚI BÀN GIAO TIỀN KIỂM BƯỚC 4 & THẨM QUYỀN ĐÍCH
*(Step 4 Readiness Handoff & Target Authority Architectural Design)*

> **Kế thừa & Căn cứ kỹ thuật:**
> - Quyết định nghiệm thu Codex: `docs/architecture/07A4-R2-code-acceptance.md` (`ACCEPTED_WITH_RECORDED_LIMITS`).
> - Quyết định thiết kế ràng buộc Codex: `docs/architecture/07A4-codex-decisions.md` (C01–C08).
> - Nghiệm thu hạ tầng backend: `docs/architecture/07A3-code-acceptance.md` & `07A3-implementation-decisions.md`.
> - Trạng thái Gate hiện tại: **Gate G07 / Milestone M1 CHƯA HOÀN TẤT**; `ready_to_execute=False` được bảo toàn tuyệt đối; chưa kích hoạt timeline mutation hay gọi DaVinci Resolve API.

- **TASK_ID:** `G07-07A5-D`
- **WORKER_LEVEL:** `HIGH`
- **MODE:** `DESIGN_ONLY_STEP4_READINESS_HANDOFF`
- **MỤC TIÊU KIẾN TRÚC:** Đặc tả source-grounded toàn diện ranh giới chuyển tiếp từ kịch bản được người dùng phê duyệt tại Bước 3 (`ReviewPage`) sang tiền kiểm khả dụng chuyên sâu tại Bước 4 (`DirectorWindow` / `BuildPage` / `PreflightWorker` / `source_catalog_readiness`). Phân định rạch ròi dữ liệu nào đã có thẩm quyền từ Bước 3, dữ liệu nào bắt buộc phải truy vấn từ DaVinci Resolve hoặc đo đạc đĩa tại Bước 4, quy tắc xử lý stale/rebind, vòng đời bộ nhớ đệm, ranh giới đột biến tương lai với biên bản nhận chứng (receipt) và nhật ký (journal), cùng ma trận nghiệm thu hữu hạn.
- **RÀNG BUỘC PHẠM VI:** Thiết kế thuần kiến trúc (DESIGN ONLY). Chính xác 0 dòng sửa đổi trong `src/`, 0 sửa đổi test sản phẩm, 0 thực thi pytest/app/Resolve/AI model/mạng ngoài. Không commit/push/clean/stash git. Đề xuất duy nhất một lát cắt code kế tiếp (tối đa 3 production modules).

---

## 1. CALLGRAPH THỰC TẾ & ĐỐI CHIẾU MÃ NGUỒN (REAL CALLGRAPH TRACE)

Đối chiếu trực tiếp mã nguồn hiện hành tại repository, đánh nhãn phân định minh bạch giữa các khối mã đã kiểm chứng thực tế (`[CODE_VERIFIED]`), các khối đề xuất kiến trúc mới (`[PROPOSED]`), và các thành phần giả định/chưa kiểm chứng (`[UNVERIFIED]`):

```
+-----------------------------------------------------------------------------------------------------------------------+
|                                    BƯỚC 3: REVIEW APPROVAL & GETTER (CODE_VERIFIED)                                   |
+-----------------------------------------------------------------------------------------------------------------------+
 [ReviewPage]
      |
      |--> (1) _on_proposal_approved(proposal_id, revision) [src/ui/director/pages/review_page.py:7025-7196] [CODE_VERIFIED]
      |         |-- Current-Input Fingerprint: self._build_current_input_fingerprint() [line 7041] [CODE_VERIFIED]
      |         |-- Graph Revalidation: build_edit_plan_proposal(source_evidence_by_clip=...) [line 7095] [CODE_VERIFIED]
      |         |-- Geometry Guard: assemble_plan_frame_geometry() != GEOMETRY_BLOCKED [line 7151] [CODE_VERIFIED]
      |         |-- Create Approval: create_app_owned_approval_record() [line 7169] [CODE_VERIFIED]
      |         +-- Immutable Binding Check: verify_proposal_approval_binding() [line 7175] [CODE_VERIFIED]
      |
      +--> (2) get_approved_proposal_and_record() [src/ui/director/pages/review_page.py:7209-7294] [CODE_VERIFIED]
                |-- Zero-IO / 1 context supplier read [line 7250] [CODE_VERIFIED]
                |-- Stale & Fingerprint check [line 7259] [CODE_VERIFIED]
                |-- Revision match check [line 7277] [CODE_VERIFIED]
                +-- Returns defensive tuple: (normalize_edit_plan_proposal(prop), AppOwnedApprovalRecord(...)) [line 7287]

+-----------------------------------------------------------------------------------------------------------------------+
|                                    BƯỚC 4: TIỀN KIỂM KHẢ DỤNG HIỆN HÀNH (CODE_VERIFIED)                              |
+-----------------------------------------------------------------------------------------------------------------------+
 [DirectorWindow]
      |
      |--> (3) _on_btn_primary_clicked() [src/ui/director/director_window.py:530] [CODE_VERIFIED]
      |         |-- Khi current_step == 4 -> gọi _start_readiness_preflight() [line 542] [CODE_VERIFIED]
      |
      |--> (4) _start_readiness_preflight() [src/ui/director/director_window.py:548] [CODE_VERIFIED]
      |         |-- Query payload: approved_payload = page_review.get_approved_proposal_and_record() [line 580] [CODE_VERIFIED]
      |         |-- Tạo candidate request: ReadinessRequest(..., verify_source_execution=False, timeline_fps=None) [line 598]
      |         |   <-- [HIỆN TRẠNG: verify_source_execution=False, timeline_fps=None; CHƯA BẬT DEEP MODE!]
      |         |-- Worker instantiation: PreflightWorker(request=request, parent=self) [line 604] [CODE_VERIFIED]
      |         |-- Register candidate: _register_preflight_candidate(worker, request) [line 612, 660] [CODE_VERIFIED]
      |         |   Ghi nhận 5-tuple: (req_id, gen, prop_id, rev, appr_id) [line 667] [CODE_VERIFIED]
      |         +-- Connect signals: terminal -> _on_readiness_terminal, finished -> _on_readiness_worker_finished
      |
      v
 [PreflightWorker (QThread)] [src/ui/director/workers/preflight_worker.py:40-350] [CODE_VERIFIED]
      |
      |--> (5) run() [line 175] [CODE_VERIFIED]
                |-- Inspector call: inspect_source_and_catalog_readiness(self._request, ...) [line 203] [CODE_VERIFIED]
                |-- Normalize & validate scalar 5-tuple binding [line 217-227] [CODE_VERIFIED]
                |-- Detailed observations validation (sources, catalogs) [line 230-301] [CODE_VERIFIED]
                |-- Deep mode validation (khi verify_source_execution=True) [line 302-325] [CODE_VERIFIED]:
                |     |-- validate_execution_evidence_binding() [line 307] [CODE_VERIFIED]
                |     +-- assembled_source_geometry.status == 'GEOMETRY_VERIFIED' [line 319] [CODE_VERIFIED]
                +-- Emit exactly 1 terminal signal: self.terminal.emit(normalized_result) [line 152] [CODE_VERIFIED]

+-----------------------------------------------------------------------------------------------------------------------+
|                                    HẠ TẦNG THẨM TRA NGUỒN & HÌNH HỌC (CODE_VERIFIED)                                  |
+-----------------------------------------------------------------------------------------------------------------------+
 [source_catalog_readiness.py] [src/core/source_catalog_readiness.py:749-1903] [CODE_VERIFIED]
      |
      |--> (6) inspect_source_and_catalog_readiness(request, ...) [line 749] [CODE_VERIFIED]
                |-- Phase 1: Thẩm tra siêu dữ liệu nguồn ban đầu & 5-stat [line 850-1040] [CODE_VERIFIED]
                |-- Phase 2: Thẩm tra catalog tài nguyên qua StorageManager & ActionGuard [line 1045-1480] [CODE_VERIFIED]
                |-- Phase 3: Tái kiểm tra 5-stat nguồn & master tài nguyên (TOCTOU guard) [line 1481-1685] [CODE_VERIFIED]
                |-- Phase 3.5: Deep Source Evidence (khi request.verify_source_execution=True) [line 1692-1858] [CODE_VERIFIED]:
                |     |-- Gọi SourceEvidenceService.acquire_evidence_for_batch() [line 1737] [CODE_VERIFIED]
                |     |-- Đối chiếu ràng buộc: validate_execution_evidence_binding() [line 1768] [CODE_VERIFIED]
                |     |-- Recheck 5-stat nguồn & catalog authorities sau quét dài [line 1779-1839] [CODE_VERIFIED]
                |     +-- Lắp ráp hình học: assemble_plan_frame_geometry(prop, ev_table, timeline_fps) [line 1845] [CODE_VERIFIED]
                +-- Khóa cứng ready_to_execute = False trong mọi trường hợp [line 1866] [CODE_VERIFIED]

 [SourceEvidenceService] [src/core/geometry/source_evidence_service.py:941-1457] [CODE_VERIFIED]
      |
      |--> (7) acquire_evidence_for_batch(batch_req) [line 971] [CODE_VERIFIED]
                |-- Tra cứu RAM cache hoàn chỉnh theo khóa 8-tuple [line 949, 1085] [CODE_VERIFIED]:
                |   Khóa: (file_path, dev, ino, size, mtime_ns, ctime_ns, stream_index, profile_policy)
                |-- Cache Miss: Quét header video -> Băm Full SHA-256 (1MiB chunks) -> Quét decoded frames [CODE_VERIFIED]
                +-- Post-stat verification -> Ghi cache nếu đạt VERIFIED_CFR [line 1250-1280] [CODE_VERIFIED]

+-----------------------------------------------------------------------------------------------------------------------+
|                                    ĐÍCH DAVINCI RESOLVE & HIỂN THỊ GIAO DIỆN                                         |
+-----------------------------------------------------------------------------------------------------------------------+
 [DaVinci Resolve Gateway] [src/core/resolve_bridge.py & resolve_api.py] [CODE_VERIFIED / PROPOSED]
      |
      |-- ResolveBridge / ResolveAutomation: Kết nối fusionscript [resolve_bridge.py:147, resolve_api.py:44] [CODE_VERIFIED]
      |-- Query timeline hiện hành: project.GetCurrentTimeline() [resolve_bridge.py:232] [CODE_VERIFIED]
      |-- Legacy query FPS: timeline.GetSetting("timelineFrameRate") [resolve_api.py:1047, 2179] [CODE_VERIFIED]
      |   <-- [CẢNH BÁO CODE HIỆN TRẠNG: fallback float lỏng lẻo "or 30.0" hoặc "24.0" tại resolve_api.py:1045, 2182!]
      +-- [PROPOSED]: ResolveTimelineAuthorityGateway - Truy vấn chuẩn hóa timeline_fps (RationalFPS), target tracks,
          project_name, timeline_name với chính sách ZERO FLOAT FALLBACK.

 [BuildPage (Giao diện Bước 4)] [src/ui/director/pages/build_page.py:91-380] [CODE_VERIFIED / PROPOSED]
      |
      |-- set_readiness_busy(busy) [line 274] [CODE_VERIFIED]
      |-- show_readiness_result(result) [line 284] [CODE_VERIFIED]:
      |     |-- assert ready_to_execute is False [line 290] [CODE_VERIFIED]
      |     |-- Hiển thị source_observations & catalog_observations (max 200 dòng text) [line 313-372] [CODE_VERIFIED]
      |     +-- [PROPOSED]: Mở rộng hiển thị cột Bằng chứng Byte/CFR và Khung hình Đích khi chạy deep preflight.
      +-- clear_readiness_result(message) [line 373] [CODE_VERIFIED]
```

---

## 2. ĐẶC TẢ PHÂN ĐỊNH THẨM QUYỀN (AUTHORITY SPECIFICATION: STEP 3 VS. STEP 4)

Kiến trúc phân chia ranh giới thẩm quyền nhằm loại bỏ hoàn toàn việc suy diễn dữ liệu (heuristics), ngăn chặn lỗ hổng TOCTOU (Time-of-Check to Time-of-Use), và đảm bảo tính bất biến tuyệt đối của hồ sơ phê duyệt.

```
                      RANH GIỚI THẨM QUYỀN (AUTHORITY BOUNDARY)
+----------------------------------------------------+----------------------------------------------------+
|             BƯỚC 3: THẨM QUYỀN BIÊN TẬP            |              BƯỚC 4: THẨM QUYỀN ĐÍCH               |
|            (EDITORIAL REVIEW AUTHORITY)            |             (TARGET & EXECUTION READINESS)         |
+----------------------------------------------------+----------------------------------------------------+
| NGUỒN CUNG CẤP:                                    | NGUỒN CUNG CẤP:                                    |
| - Người dùng tương tác trên ReviewPage             | - DaVinci Resolve Runtime API (fusionscript)       |
| - ProposalBuilder (pure in-memory graph)           | - Hệ thống tệp tin đĩa vật lý (action-time I/O)    |
| - PreapprovalEvidenceWorker (nếu có quét trước)    | - Cấu hình đích Bước 4 do người dùng xác nhận      |
+----------------------------------------------------+----------------------------------------------------+
| CÁC TRƯỜNG ĐƯỢC CHỐT TRƯỚC PHÊ DUYỆT (IMMUTABLE):  | CÁC TRƯỜNG CHỈ XÁC LẬP TẠI BƯỚC 4 (ACTION-TIME):   |
| 1. proposal_id & proposal_revision                 | 1. authoritative_timeline_fps (RationalFPS)        |
| 2. approval_id & approved_at_iso                   | 2. target_project_name & target_timeline_id        |
| 3. accepted_omission_ids                           | 3. target_video_track_index & target_audio_tracks   |
| 4. approval_scope = 'ENTIRE_PROPOSAL'              | 4. action_time_source_freshness (5-stat re-verify) |
| 5. selected_candidate_ids & user_choices           | 5. catalog_master_freshness (5-stat re-verify)     |
| 6. expected_full_sha256 (từng clip nếu đã quét)    | 6. assembled_plan_frame_geometry (Formula B)       |
| 7. stream_index (từng clip nếu đã quét)            | 7. pre_mutation_receipt & execution_journal        |
| 8. complete_scan & profile_policy                  | 8. ready_to_execute = False (khóa cứng)            |
+----------------------------------------------------+----------------------------------------------------+
```

### 2.1. Thẩm quyền Bước 3 (Chốt trước khi phê duyệt - Bất biến)
- **`proposal_id` và `proposal_revision`**: Định danh duy nhất và chuỗi băm phiên bản kịch bản tính toán bởi `compute_proposal_revision`. Bất kỳ thay đổi nào trên clip, nhãn, thời lượng, lựa chọn ứng viên, văn bản kinetic đều làm biến đổi revision.
- **`approval_id`, `approved_at_iso`, `accepted_omission_ids`, `approval_scope`**: Bộ hồ sơ phê duyệt do ứng dụng sở hữu (`AppOwnedApprovalRecord`). `approval_scope` bắt buộc là `"ENTIRE_PROPOSAL"`.
- **`expected_full_sha256` và `stream_index`**: Được đóng gói nguyên khối vào từng clip trong `source_binding.clips` nếu người dùng đã thực hiện quét tiền phê duyệt (`PreapprovalEvidenceWorker`).
  - `expected_full_sha256`: Chuỗi 64 ký tự hex chữ thường chuẩn tắc tính toán trên toàn bộ byte tệp (1MiB chunks). **Tuyệt đối không lấy `manifest.content_hash` (băm mẫu G05) làm băm toàn phần.**
  - `stream_index`: Số nguyên không âm (`int >= 0`), chỉ định stream video toàn cục đã được giải mã và kiểm chứng CFR.
- **Tính chất**: Hoàn toàn đóng băng sau khi bấm "Phê duyệt". `get_approved_proposal_and_record()` trả về defensive copy; 0 I/O; không tự sửa đổi proposal đã duyệt.

### 2.2. Thẩm quyền Bước 4 (Chỉ xác lập tại Bước 4 - Action-time)
- **`authoritative_timeline_fps`**: Tần số khung hình thực tế của timeline đang mở trong DaVinci Resolve.
  - Phải được đọc trực tiếp từ `timeline.GetSetting("timelineFrameRate")`.
  - Phải được ánh xạ thành `RationalFPS` chuẩn tắc (ví dụ: `24000/1001` cho 23.976 fps, `30000/1001` cho 29.97 fps, `24/1`, `25/1`, `30/1`, `50/1`, `60000/1001`, `60/1`).
  - **Không bao giờ suy diễn từ footage nguồn đầu tiên.**
  - **Không bao giờ fallback về giá trị thả nổi mặc định `24.0` hoặc `30.0`.**
- **Định danh đích Resolve**: `project_name`, `timeline_name`, `timeline_id` (hoặc con trỏ timeline duy nhất). Đảm bảo lệnh thi công không đưa nhầm vào project khác hoặc timeline phụ.
- **Phân bổ Track đích (Target Track Allocation)**: Chỉ định rõ track video nhận footage (ví dụ V1), track video nhận subtitle/overlay (ví dụ V2), track audio nhận thoại và nhạc nền (A1, A2). Phải kiểm tra trạng thái khóa track (`GetIsTrackLocked`) và sự hiện diện của clip xung đột trong dải thời gian dự kiến chèn.
- **Bằng chứng Freshness tại thời điểm thi công (Action-Time 5-Stat)**: Bộ 5 thông số `(dev, ino, size, mtime_ns, ctime_ns)` được đo lại off-thread ngay trước và sau khi quét bằng chứng thi công để triệt tiêu race condition trên hệ thống tệp.

### 2.3. Quy tắc vô hiệu hóa & yêu cầu phê duyệt lại (Invalidation Matrix)
Bất kỳ sự sai lệch nào giữa thực tế đo đạc tại Bước 4 và thẩm quyền đã chốt tại Bước 3 đều phải ngay lập tức **hủy bỏ trạng thái readiness**, khóa nút thi công, và điều hướng hành vi phù hợp:

| Tình huống sai lệch thẩm quyền | Phát hiện tại | Hành động hệ thống | Trạng thái phê duyệt Bước 3 | Yêu cầu người dùng |
|---|---|---|---|---|
| **Người dùng sửa lựa chọn tại Bước 1, 2, 3** (đổi footage, đổi style, đổi ứng viên) | `ReviewPage.get_approved_proposal_and_record` (fingerprint mismatch) | Từ chối cấp approval; BuildPage xóa readiness (`clear_readiness_result`) | **HỦY BỎ** (`_current_approval_record = None`) | Quay lại Bước 3 xem kịch bản mới và bấm Phê duyệt lại. |
| **Kịch bản duyệt thiếu `expected_full_sha256`** (Duyệt biên tập thuần túy, chưa quét Bước 3) | `PreflightWorker.__init__` hoặc `inspect_source_and_catalog_readiness` | Nếu deep mode được yêu cầu: báo `ERR_MISSING_EXPECTED_DIGEST`, `checks_passed=False` | **GIỮ NGUYÊN BIÊN TẬP** (Hợp lệ về mặt kịch bản, nhưng chưa đủ điều kiện thi công) | Chạy quét bằng chứng tại Bước 3 hoặc chấp nhận deep scan tự động tại Bước 4 để xác lập expected SHA và phê duyệt lại. |
| **Tệp nguồn trên đĩa bị sửa sau khi duyệt** (5-stat hoặc SHA-256 khác với expected) | `inspect_source_and_catalog_readiness` (Deep Phase 3.5) | Báo `ERR_EVIDENCE_BINDING_FAILED` / `ERR_SOURCE_MUTATED`, `checks_passed=False` | **VÔ HIỆU HÓA** | Chuyển về Bước 3, hiển thị cảnh báo tệp bị biến động, quét lại và phê duyệt lại. |
| **DaVinci Resolve chưa mở hoặc chưa mở Timeline** | Gateway truy vấn Bước 4 | Báo `ERR_RESOLVE_NO_ACTIVE_TIMELINE`, không khởi tạo worker tiền kiểm đích | **GIỮ NGUYÊN** | Mở DaVinci Resolve, mở đúng timeline của dự án và nhấn thử lại. |
| **FPS của Timeline Resolve lệch với FPS kịch bản** | Gateway / `plan_geometry_assembler` | Báo `ERR_TIMELINE_FPS_MISMATCH`, hình học bị `GEOMETRY_MISMATCH` | **GIỮ NGUYÊN** | Điều chỉnh Project/Timeline Settings trong Resolve cho khớp, hoặc cập nhật cấu hình dựng. |
| **Track đích trên Resolve bị khóa hoặc xung đột** | Gateway tiền kiểm track đích | Báo `ERR_TARGET_TRACK_LOCKED` hoặc `ERR_TARGET_TRACK_OCCUPIED` | **GIỮ NGUYÊN** | Mở khóa track hoặc chọn track trống trên Resolve trước khi tiếp tục. |

---

## 3. VÒNG ĐỜI NGUỒN, BỘ NHỚ ĐỆM & ĐIỀU KIỆN TRANH CHẤP (LIFETIME, CACHING & RACE CONDITIONS)

### 3.1. Vòng đời Bộ nhớ đệm (Cache Lifetime Architecture)
Hiện hành tại `07A4-R2`: `ReviewPage` sở hữu một `SourceEvidenceService` độc quyền trong phiên giao diện Bước 3 (`_preapproval_evidence_service`), bộ nhớ đệm RAM được giải phóng (`clear_cache()`) khi danh sách nguồn hoặc manifest thay đổi và khi worker vật lý đã hoàn toàn rảnh (`analysis_workers_idle`).

Để Bước 4 có thể tái sử dụng kết quả băm SHA-256 và giải mã khung hình đắt đỏ mà Bước 3 đã thực hiện (tránh việc đọc đĩa 2 lần cho cùng một tệp video dung lượng lớn):
1. **Chia sẻ thể hiện dịch vụ qua điều phối viên `DirectorWindow`**:
   - `DirectorWindow` khởi tạo và quản lý thể hiện `SourceEvidenceService` dùng chung cấp phiên làm việc (`_shared_source_evidence_service`).
   - Cả `ReviewPage` (cho Bước 3 preapproval) và `PreflightWorker` (cho Bước 4 deep preflight) đều sử dụng chung kho đệm RAM của service này.
2. **Khóa bộ nhớ đệm chuẩn tắc 8-thành phần (`_cache` key)**:
   ```python
   CacheKey = Tuple[
       str,  # file_path (đường dẫn chuẩn hóa os.path.realpath)
       int,  # dev (st_dev từ os.stat)
       int,  # ino (st_ino từ os.stat)
       int,  # size (st_size từ os.stat)
       int,  # mtime_ns (st_mtime_ns từ os.stat)
       int,  # ctime_ns (st_ctime_ns từ os.stat)
       int,  # stream_index (chỉ số video stream toàn cục)
       str,  # profile_policy (ví dụ: 'decoded_frame_uniform_ticks_v1')
   ]
   ```
3. **Nguyên tắc Cache Hit**:
   - **Cache hit hoàn chỉnh**: Khi tra cứu đúng 8-tuple, trả về ngay lập tức bản ghi đã băm và kiểm chứng (`_CachedEvidenceFact`).
   - **I/O tại cache hit**: Chỉ tốn **duy nhất 1 lời gọi `os.stat`** để thẩm tra 5-stat freshness; **0 byte đọc dữ liệu media, 0 tiến trình ffprobe, 0 tính toán SHA-256**.
   - Nếu tệp bị ghi đè, sửa đổi mtime hoặc kích thước -> 5-stat thay đổi -> Cache miss tự nhiên -> Buộc phải quét lại từ đầu.
4. **Xóa bộ nhớ đệm an toàn (Thread-Safe & Drain-Safe Clear)**:
   - Tuyệt đối không xóa đệm khi đang có worker (Bước 3 hoặc Bước 4) đang chạy.
   - Chỉ gọi `clear_cache()` khi hệ thống rảnh tổng hợp (`is_analysis_and_preflight_idle() == True`) và xảy ra một trong các biến cố:
     - Người dùng thêm/xóa/đổi clip nguồn tại Bước 1.
     - Người dùng nạp manifest mới.
     - Đóng cửa sổ ứng dụng (`closeEvent` / shutdown sequence).

### 3.2. Kiểm soát Điều kiện Tranh chấp (Race Conditions & Concurrency Controls)
1. **Nguồn trên đĩa bị biến động trong khi đang quét (Mutation during scan)**:
   - `inspect_source_and_catalog_readiness` thực hiện vòng kiểm tra 5-stat 2 lần: trước khi quét (`Phase 1`) và sau khi quét (`Phase 3`, `Phase 3.5`).
   - Nếu `curr_id != s_obs.stat_identity`, hệ thống gán nhãn `recheck_passed=False` kèm lỗi `ERR_SOURCE_MUTATED_DURING_SCAN` hoặc `ERR_SOURCE_MUTATED_DURING_DEEP_SCAN`.
   - Kết quả: `checks_passed=False`, tiến trình bị chặn đứng.
2. **Catalog / Storage Registry bị biến động**:
   - Hàm `_recheck_catalog_authorities` giám sát `storage_marker`, `registry_file`, và cấu hình đĩa. Nếu bất kỳ thành phần nào bị sửa đổi trong lúc quét -> `ERR_CATALOG_REGISTRY_MUTATED` -> `checks_passed=False`.
3. **Người dùng bấm Hủy (Cooperative Cancellation)**:
   - Worker hỗ trợ cờ hủy `_cancel_requested = True`.
   - Các checkpoint kiểm tra `_cancel_check()` định kỳ: trước khi nạp catalog, sau khi nạp factory, trước khi gọi service, và trong từng khung hình streaming qua `SourceEvidenceService`.
   - Khi bị hủy: Dịch vụ dừng tiến trình con qua `process.terminate()`, ném `InterruptedError`, worker phát kết quả `status="cancelled"`, UI phục hồi trạng thái rảnh mà không lưu lại kết quả rác.
4. **Không tin tưởng Proxy & Không dùng Giá trị Mặc định Giả**:
   - Bất kỳ proxy nào chưa được xác minh toàn vẹn đều không được đưa vào tiền kiểm thi công.
   - Tuyệt đối cấm các mẫu code suy diễn: `fps = getattr(timeline, "fps", 24.0)` hoặc `expected_sha = manifest.content_hash`. Mọi giá trị phải đến từ producer đo đạc thực tế hoặc Resolve API chính thức.

---

## 4. THIẾT KẾ TRẠNG THÁI TIỀN KIỂM & BẢO TOÀN KHÓA THI CÔNG (READINESS STATES & EXECUTION LOCK)

Hệ thống phân tách rạch ròi 4 tầng trạng thái độc lập, không gộp lẫn giữa trạng thái biên tập và khả năng thi công vật lý:

```
+-------------------------------------------------------------------------------------------------------------+
|                                  BỐN TẦNG TRẠNG THÁI READINESS (SEPARATED STATES)                           |
+-------------------------------------------------------------------------------------------------------------+

 1. TẦNG BIÊN TẬP (Editorial Approval State):
    - NO_PROPOSAL: Chưa tạo kịch bản.
    - PROPOSAL_DRAFT: Kịch bản nháp đang chỉnh sửa / chưa duyệt.
    - EDITORIAL_APPROVED_UNVERIFIED: Người dùng đã duyệt nội dung, nhưng chưa có bằng chứng byte/CFR nguồn.
    - EDITORIAL_APPROVED_VERIFIED: Người dùng đã duyệt nội dung kèm đầy đủ bằng chứng byte/CFR chuẩn tắc.
    - STALE_INVALIDATED: Dữ liệu nguồn hoặc kịch bản thay đổi sau khi duyệt -> Vô hiệu hóa.

 2. TẦNG BẰNG CHỨNG NGUỒN (Deep Source Evidence State):
    - EVIDENCE_UNSCANNED: Chưa quét nguồn chuyên sâu.
    - EVIDENCE_SCANNING: Đang băm SHA-256 và giải mã khung hình off-thread.
    - EVIDENCE_VERIFIED_CFR: Toàn bộ nguồn khớp SHA-256, chuẩn CFR, không lỗi giải mã.
    - EVIDENCE_HEADER_ONLY_REFUSED: Tệp chỉ có metadata container, thiếu kiểm chứng frame -> Từ chối.
    - EVIDENCE_VFR_REJECTED: Phát hiện video biến thiên tốc độ khung hình (VFR) -> Từ chối thi công.
    - EVIDENCE_MISMATCH_FAILED: SHA-256 thực tế trên đĩa khác với SHA-256 đã chốt trong proposal.

 3. TẦNG ĐÍCH RESOLVE (Target Readiness State):
    - TARGET_DISCONNECTED: Chưa kết nối được DaVinci Resolve qua fusionscript.
    - TARGET_NO_PROJECT: Resolve đang mở nhưng chưa nạp dự án nào.
    - TARGET_NO_TIMELINE: Dự án đang mở nhưng chưa có timeline nào active.
    - TARGET_FPS_VERIFIED: Timeline đang mở có FPS chuẩn xác minh được bằng RationalFPS.
    - TARGET_FPS_MISMATCH: FPS của timeline Resolve khác với FPS của kịch bản đã duyệt.
    - TARGET_TRACKS_RESERVED: Các track đích (V1, V2, A1, A2) hợp lệ, không bị khóa, không xung đột.

 4. TẦNG SẴN SÀNG THI CÔNG (Execution Readiness State):
    - EXECUTION_LOCKED: Khóa thi công (Mặc định).
    - PREFLIGHT_BUSY: Đang thực thi tiền kiểm off-thread.
    - CHECKS_PASSED_LOCKED: Toàn bộ kiểm tra nguồn, catalog và target đều đạt, nhưng VẪN KHÓA THI CÔNG
      (do Milestone M1 chưa mở executor).
    - EXECUTION_BLOCKED: Có lỗi kiểm tra -> Khóa hoàn toàn.
```

### Bảo toàn bất biến `ready_to_execute = False`
Trong toàn bộ tài liệu kiến trúc, giao diện DTO (`SourceCatalogReadinessResult`, `ReadinessRequest`, `BuildResult`), và mã nguồn hiện hành:
- **`ready_to_execute` BẮT BUỘC LUÔN LÀ `False`**.
- Không bất kỳ luồng thực thi nào, kể cả khi `checks_passed=True`, được phép trả về `ready_to_execute=True` cho đến khi:
  1. Một lát cắt thi công chính thức (Executor Slice - G07-M2/M3) được thiết kế và phê duyệt.
  2. Toàn bộ cơ chế biên bản nhận chứng (receipt), nhật ký thao tác (journal), và phục hồi rollback được chứng minh bằng kiểm thử độc lập.

---

## 5. RANH GIỚI ĐỘT BIẾN TƯƠNG LAI & ĐƠN THREAD RESOLVE API (MUTATION BOUNDARY & RESOLVE THREAD AFFINITY)

Mặc dù trong nhiệm vụ `G07-07A5-D` tuyệt đối không thực hiện bất kỳ lệnh đột biến nào, tài liệu này thiết lập sẵn các chuẩn mực kiến trúc bắt buộc cho các milestone thi công sau (G07-M2, M3, M4).

### 5.1. Kế hoạch Thi công Bất biến (Immutable Execution Plan)
Khi chuyển từ kết quả tiền kiểm đạt chuẩn sang thi công:
- Sinh ra một đối tượng bất biến duy nhất: `ExecutableTimelinePlan`.
- Chứa danh sách các thao tác nguyên tử (atomic edit actions):
  - `InsertVideoClipAction(clip_id, path, stream_index, source_in_frame, source_out_frame, timeline_start_frame, track_index)`
  - `InsertAudioClipAction(...)`
  - `InsertKineticTitleAction(title_params, timeline_start_frame, duration_frames, track_index)`
- Toàn bộ tọa độ khung hình được tính toán chính xác bằng **Công thức Hình học B (Formula B - Exact Frame Geometry)**:
  $$\text{TimelineDurationFrames} = \text{round}\left(\text{DurationSeconds} \times \text{AuthoritativeTimelineFPS}\right)$$
  với việc bù trừ trôi khung hình (cadence drift compensation) trên từng clip liên tục.

### 5.2. Biên bản Nhận chứng Tiền Đột biến & Nhật ký Thao tác (Pre-Mutation Receipt & Journal)
Trước khi gửi bất kỳ lệnh API nào làm biến đổi timeline trong DaVinci Resolve, hệ thống bắt buộc phải thực hiện thủ tục ghi đĩa bền vững (durable disk write):

```
       QUY TRÌNH THI CÔNG AN TOÀN TRÁNH CORRUPTION (SAFE MUTATION LIFECYCLE)
+---------------------------------------------------------------------------------------+
| 1. LẬP BIÊN BẢN NHẬN CHỨNG (Pre-Mutation Receipt)                                     |
|    - Ghi tệp JSON: `receipts/<execution_id>.receipt.json`                             |
|    - Chứa: execution_id, proposal_id, approval_id, timeline_id, SHA-256 các tệp nguồn, |
|      danh sách track đích, thời điểm bắt đầu (ISO UTC).                               |
|    - Bắt buộc fsync() xuống đĩa; nếu ghi thất bại -> HỦY THI CÔNG NGAY LẬP TỨC.        |
+---------------------------------------------------------------------------------------+
                                           |
                                           v
+---------------------------------------------------------------------------------------+
| 2. GHI NHẬT KÝ THAO TÁC THEO TỪNG BƯỚC (Atomic Journaling)                            |
|    - Ghi tệp JSONL: `journals/<execution_id>.journal.jsonl`                           |
|    - Cấu trúc từng dòng:                                                              |
|      {"seq": 1, "action": "INSERT_CLIP", "clip_id": "c1", "status": "PENDING"}       |
|    - Thực thi lệnh Resolve API tương ứng.                                             |
|    - Ghi nhận trạng thái hoàn thành:                                                  |
|      {"seq": 1, "action": "INSERT_CLIP", "timeline_item_id": "...", "status": "DONE"} |
+---------------------------------------------------------------------------------------+
                                           |
                                           v
+---------------------------------------------------------------------------------------+
| 3. PHỤC HỒI & HOÀN TÁC KHI THẤT BẠI (Rollback & Partial Failure Handling)             |
|    - Nếu xảy ra lỗi giữa chừng (ví dụ: mất kết nối Resolve, lỗi import comp):         |
|      + Quét ngược journal từ dòng cuối cùng về đầu.                                   |
|      + Gọi hàm xóa các TimelineItem đã được thêm vào timeline.                        |
|      + Đánh dấu trạng thái thi công trong receipt là "ROLLED_BACK".                   |
|      + Không bao giờ để lại timeline ở trạng thái biến dạng một nửa (half-baked).     |
+---------------------------------------------------------------------------------------+
```

### 5.3. Ràng buộc Đơn Thread DaVinci Resolve API (Single-Thread Affinity Rule)
- **Thực tế kỹ thuật**: Thư viện `fusionscript` (Blackmagic Design API) dựa trên RPC socket/shm nội bộ kết nối tới daemon của Resolve. Thư viện này **KHÔNG THREAD-SAFE**.
- **Ràng buộc kiến trúc**:
  1. Tuyệt đối **không gọi Resolve API từ nhiều luồng đồng thời**.
  2. Mọi thao tác truy vấn (ở Bước 4) hoặc đột biến (ở tương lai) bắt buộc phải được điều phối qua một **Executor Thread duy nhất** (Dedicated Serialized Worker Thread) hoặc gọi tuần tự trên Main GUI Thread khi hệ thống hoàn toàn khóa giao diện.
  3. Tất cả các tác vụ nặng về I/O đĩa (băm SHA-256, ffprobe, scan decoded frames) **chạy trên luồng nền khác** (`QThread`), tách biệt hoàn toàn khỏi luồng giao tiếp Resolve API để không bao giờ làm đóng băng giao diện hoặc nghẽn kênh RPC của Resolve.

---

## 6. MA TRẬN NGHIỆM THU HỮU HẠN (FINITE ACCEPTANCE MATRIX)

Ma trận kiểm chứng 12 kịch bản biên từ điều kiện lý tưởng đến lỗi nghiêm trọng của ranh giới Bước 4:

| Mã test | Tên kịch bản | Điều kiện kích hoạt | Kỳ vọng đầu ra | Tiêu chí vượt qua (Pass Criteria) | Trạng thái khóa |
|---|---|---|---|---|---|
| **TC-01** | **Happy Path (Full Preapproval)** | Kịch bản có đủ expected_full_sha256; nguồn & catalog nguyên vẹn; Resolve mở đúng timeline; FPS khớp. | `checks_passed=True`, `status='completed'`, đầy đủ `source_observations`, `source_execution_evidence`, `source_geometry`. | 100% clip khớp SHA & CFR; geometry VERIFIED; bảng hiển thị xanh; không lỗi diagnostic. | `ready_to_execute=False` |
| **TC-02** | **Cache Hit Re-Preflight** | Chạy lại kiểm tra chuyên sâu lần 2 trên cùng kịch bản và tệp nguồn không đổi. | Kết quả TC-01 được trả về tức thì; 0 lần gọi băm SHA-256; 0 lần gọi ffprobe decode. | Kiểm tra hit log của `SourceEvidenceService`; thời gian thực thi < 200ms; stat check đạt. | `ready_to_execute=False` |
| **TC-03** | **Editorial Approved, Missing Proof** | Kịch bản duyệt biên tập thuần túy (không quét Bước 3); yêu cầu deep preflight. | `checks_passed=False`, `status='completed'`, diagnostic báo thiếu bằng chứng kỳ vọng. | Báo rõ `ERR_MISSING_EXECUTION_EVIDENCE` hoặc yêu cầu cấp expected SHA; không bị crash. | `ready_to_execute=False` |
| **TC-04** | **Missing Source File** | Tệp video nguồn bị xóa hoặc đổi tên sau khi phê duyệt. | `checks_passed=False`, clip bị thiếu có `exists=False`, `error_code='ERR_SOURCE_NOT_FOUND'`. | Bảng hiển thị đỏ dòng clip bị mất; chẩn đoán nêu rõ đường dẫn tệp không tồn tại. | `ready_to_execute=False` |
| **TC-05** | **Source File Mutated (Stale)** | Tệp video nguồn bị thay thế nội dung hoặc chạm mtime sau khi phê duyệt. | `checks_passed=False`, phát hiện `curr_id != s_obs.stat_identity` hoặc hash mismatch. | Báo lỗi `ERR_SOURCE_MUTATED_DURING_SCAN` hoặc `ERR_EVIDENCE_BINDING_FAILED`; chặn thi công. | `ready_to_execute=False` |
| **TC-06** | **Variable Frame Rate (VFR)** | Tệp nguồn có khung hình biến thiên, không đạt tiêu chuẩn uniform integer ticks. | `checks_passed=False`, clip có trạng thái `HEADER_REPORTED_ONLY` hoặc VFR diagnostic. | Không đoán mò CFR; từ chối lắp ráp geometry; thông báo tệp không đủ điều kiện dựng chuẩn. | `ready_to_execute=False` |
| **TC-07** | **No Active Timeline in Resolve** | Resolve đang mở nhưng người dùng đang ở Media Pool / không mở timeline trên Edit page. | `checks_passed=False`, Gateway báo `ERR_RESOLVE_NO_ACTIVE_TIMELINE`. | Không cố gọi `GetSetting`; hiển thị hướng dẫn người dùng mở timeline trên Edit page. | `ready_to_execute=False` |
| **TC-08** | **Timeline FPS Mismatch** | Kịch bản thiết kế cho 30.0 fps, nhưng timeline hiện hành trong Resolve cấu hình 24.0 fps. | `checks_passed=False`, hình học báo `ERR_GEOMETRY_MISMATCH`. | Không tự convert ngầm; chỉ rõ sai lệch: `Timeline FPS (24/1) != Proposal Planned FPS (30/1)`. | `ready_to_execute=False` |
| **TC-09** | **Target Track Collision** | Track V2 dự kiến đặt phụ đề đang có clip khác đè lên hoặc bị bật khóa (Lock). | `checks_passed=False`, Gateway báo `ERR_TARGET_TRACK_COLLISION` hoặc `TRACK_LOCKED`. | Chặn thi công trước khi phát lệnh insert; không ghi đè mất mát dữ liệu của người dùng. | `ready_to_execute=False` |
| **TC-10** | **User Cancellation Mid-Scan** | Người dùng bấm "✕ Hủy kiểm tra" khi worker đang quét video dung lượng lớn. | `status='cancelled'`, `checks_passed=False`, worker dừng an toàn off-thread. | Quá trình ffmpeg/ffprobe bị ngắt; không rò rỉ tiến trình nền; UI phục hồi về trạng thái rảnh. | `ready_to_execute=False` |
| **TC-11** | **Receipt Write Failure** | Ổ đĩa bị đầy (ENOSPC) hoặc thư mục receipts bị chặn quyền ghi (EACCES). | Tiến trình thi công dừng ngay lập tức tại chặng 1; 0 lệnh Resolve API được phát ra. | Báo lỗi `ERR_RECEIPT_PERSISTENCE_FAILED`; timeline trong Resolve nguyên vẹn 100%. | `ready_to_execute=False` |
| **TC-12** | **Mutation Partial Failure & Rollback** | Giả lập lỗi nạp Fusion comp ở clip thứ 3 trong kịch bản 5 clip. | Hệ thống đọc ngược journal, gọi lệnh xóa clip 1 và 2, trả timeline về trạng thái ban đầu. | Timeline sạch hoàn toàn; receipt ghi trạng thái `ROLLED_BACK`; không để lại rác trên project. | `ready_to_execute=False` |

---

## 7. ĐỀ XUẤT DUY NHẤT LÁT CẮT CODE KẾ TIẾP (PROPOSED NEXT CODE SLICE: G07-07A5-I)

Để hiện thực hóa ranh giới Bước 4 mà không làm phình to phạm vi hoặc phá vỡ các chặng kiểm soát, đề xuất duy nhất một lát cắt code tiếp theo:

### 7.1. Định danh & Phạm vi Lát cắt
- **TASK_ID:** `G07-07A5-I` (Step 4 Deep Readiness Wiring & Resolve Timeline FPS Authority).
- **CHẾ ĐỘ:** `IMPLEMENTATION`
- **SỐ LƯỢNG MODULES SẢN XUẤT:** **Đúng 3 modules** (Không vượt quá giới hạn cho phép):
  1. `src/ui/director/director_window.py`: Nối quyền sở hữu `SourceEvidenceService` dùng chung giữa Bước 3 và Bước 4; kích hoạt `verify_source_execution=True` và truyền `authoritative_timeline_fps` khi khởi tạo `ReadinessRequest`.
  2. `src/ui/director/pages/build_page.py`: Cập nhật bảng hiển thị `table_readiness` để thể hiện trạng thái kiểm chứng byte SHA-256, chuẩn CFR và tình trạng hình học kịch bản từ kết quả deep preflight.
  3. `src/core/resolve_bridge.py`: Bổ sung cổng truy vấn chuẩn tắc `query_active_timeline_authority()` trả về `(project_name, timeline_id, timeline_name, authoritative_fps: RationalFPS, video_track_count, audio_track_count)` với chính sách **ZERO FLOAT FALLBACK**.

### 7.2. Phụ thuộc & Điều kiện tiên quyết (Dependencies)
- Kế thừa toàn bộ 128 tests và cấu trúc normalizer từ `G07-07A4-R2`.
- Kế thừa bộ thẩm tra `inspect_source_and_catalog_readiness` và `SourceEvidenceService` từ `G07-07A3`.

### 7.3. Các thành phần tiếp tục HOÃN LẠI (Strictly DEFERRED)
- **HOÃN LẠI:** Trình thực thi đột biến timeline (`TimelineMutationExecutor`).
- **HOÃN LẠI:** Lệnh chèn clip thật vào DaVinci Resolve (`AppendToTimeline`, `ImportFusionComp`).
- **HOÃN LẠI:** Ghi biên bản nhận chứng (`receipts/`) và nhật ký thi công (`journals/`).
- **HOÃN LẠI:** Bật cờ `ready_to_execute = True` (Tiếp tục giữ cứng `False`).
- **HOÃN LẠI:** Khép lại Gate G07 hoặc hoàn tất Milestone M1.

### 7.4. Tiêu chí Nghiệm thu Lát cắt I (Acceptance Gates for G07-07A5-I)
1. 100% test suites hiện hành (8 files / 128 tests) tiếp tục vượt qua với Exit Code 0.
2. Bộ unit tests mới cho `query_active_timeline_authority` kiểm chứng đầy đủ các nhánh:
   - Resolve không chạy -> Báo lỗi kiểm soát.
   - Project chưa mở -> Báo lỗi kiểm soát.
   - Timeline chưa mở -> Báo lỗi kiểm soát.
   - FPS dạng chuỗi `"23.976"`, `"24"`, `"29.97"`, `"30"`, `"59.94"`, `"60"` -> Ánh xạ chính xác sang `RationalFPS`.
   - FPS lạ hoặc rỗng -> Từ chối, 0 fallback 24.0/30.0.
3. Kiểm thử tích hợp UI (offscreen Qt test):
   - Đảm bảo `DirectorWindow` cấp đúng `timeline_fps` và `verify_source_execution=True` cho `ReadinessRequest`.
   - Đảm bảo `BuildPage` hiển thị đúng các dòng kiểm tra deep source evidence.
   - Bảo toàn tuyệt đối `ready_to_execute == False`.
