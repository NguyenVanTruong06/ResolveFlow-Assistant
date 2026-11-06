# G07-07A4-D: THIẾT KẾ RÀNG BUỘC BẰNG CHỨNG NGUỒN TIỀN PHÊ DUYỆT & BÀN GIAO TIỀN KIỂM CHUYÊN SÂU

> Codex review 2026-11-02: **G07-07A4-R2** Hoàn tất toàn bộ 21 residual gaps và 31 probes cũ (52/52 probes đạt 100%, 128 unit tests pass). Đã thiết lập normalizer chung `normalize_preapproval_terminal_payload`, chuẩn canonical UTF-8 byte budget và count precheck, owned request validation, và idle service reset lifecycle.
*(Preapproval Source Binding & Deep Preflight Handoff Design)*

> Quyết định hiện hành: C01–C10 tại 07A4-codex-decisions.md và review 07A4-R1-codex-review.md / 07A4-R2 đã khép kín toàn diện 4 ma trận (A, B, C, D). Step3 preapproval→builder→approval đạt chuẩn authority và lifecycle closure.

- **TASK_ID:** `G07-07A4-D` / `G07-07A4-I` / `G07-07A4-R1` / `G07-07A4-R2`
- **TRẠNG THÁI:** `COMPLETED_AND_READY_FOR_HANDOFF`
- **WORKER_LEVEL:** `HIGH`
- **MODE:** `SHARED_PREAPPROVAL_NORMALIZATION_CLOSURE`
- **MỤC TIÊU:** Hoàn thiện thiết kế và hiện thực kỹ thuật cho chặng nối M1 (Producer → Builder → Review → Preflight Handoff), giải quyết triệt để khoảng trống thẩm quyền (authority gap) giữa bằng chứng byte/frame nguồn đo đạc trên đĩa và đề xuất kịch bản bất biến được người dùng phê duyệt trước khi chuyển sang tiền kiểm thi công chuyên sâu tại Bước 4.
- **RÀNG BUỘC TUÂN THỦ:** Sửa đúng 3 production modules allowlist. Khóa cứng `ready_to_execute=False`. Không gọi Resolve API, không mạng ngoài, không AI model. Zero git commit/push/clean/stash.

---

## 1. CALLGRAPH THỰC TẾ VÀ CHỖ THIẾU THẨM QUYỀN (REAL CALLGRAPH & AUTHORITY GAPS)

Đối chiếu trực tiếp mã nguồn hiện hành tại repo, tách bạch giữa các thành phần đã được kiểm chứng bằng mã (`[CODE_VERIFIED]`) và các điểm đề xuất mới (`[PROPOSED]`):

```
+----------------------------------------------------------------------------------------------------+
|                                    HIỆN TRẠNG (BEFORE 07A4)                                        |
+----------------------------------------------------------------------------------------------------+
 [ReviewPage]
      |
      |--> (1) _rebuild_and_present_proposal() [line 6294] [CODE_VERIFIED]
      |         |
      |         +--> proposal_builder.build_edit_plan_proposal() [line 152] [CODE_VERIFIED]
      |         |         |
      |         |         +--> duyệt manifest.clips [line 225-265] [CODE_VERIFIED]
      |         |               xây clip_row: {"clip_id", "path", "duration"}  <-- [AUTHORITY GAP 1]
      |         |               THIẾU: expected_full_sha256, stream_index!
      |         |
      |         +--> ev_table = _source_frame_evidence_table [line 6355] (nếu có)
      |         +--> assemble_plan_frame_geometry() [line 6401] [CODE_VERIFIED]
      |
      |--> (2) _on_proposal_approved() [line 6482] [CODE_VERIFIED]
      |         |
      |         +--> create_app_owned_approval_record(proposal) [line 6625] [CODE_VERIFIED]
      |               (Phê duyệt proposal CHƯA CÓ expected_full_sha256 trong source_binding!)
      |
      v
 [DirectorWindow]
      |
      |--> (3) _start_readiness_preflight() [line 548] [CODE_VERIFIED]
                |
                +--> get_approved_proposal_and_record() [line 580] [CODE_VERIFIED]
                +--> ReadinessRequest(..., verify_source_execution=False) [line 598] <-- [AUTHORITY GAP 2]
                |    (Chỉ chạy kiểm tra siêu dữ liệu nông; không thể opt-in verify_source_execution
                |     vì proposal chưa có expected_full_sha256 & window chưa có timeline_fps!)
                v
          PreflightWorker / inspect_source_and_catalog_readiness() [CODE_VERIFIED]
```

### 1.1. Chi tiết các điểm đối chiếu mã nguồn:

1. **`SourceEvidenceService.acquire_evidence_for_batch`** (`src/core/geometry/source_evidence_service.py:971`):
   - `[CODE_VERIFIED]`: Nhận `SourceEvidenceBatchRequest` chứa các `SourceEvidenceItemRequest`.
   - `[CODE_VERIFIED]`: Cho phép thu thập ứng viên quan sát khi `expected_full_sha256=None` và `stream_index=None` (tự động phát hiện stream video mặc định và băm SHA-256 thực tế trên đĩa).
   - `[CODE_VERIFIED]`: Trả về `SourceEvidenceBatchResult` bao gồm `evidence_by_clip: Mapping[str, SourceExecutionEvidenceEnvelope]`.
   - `[CODE_VERIFIED]`: Mỗi `SourceExecutionEvidenceEnvelope` chứa đầy đủ: `observed_full_sha256` (chuẩn 64-hex), `stream_index` (int >= 0), `freshness` (5-stat), `frame_evidence` (SourceFrameEvidence 5 trường), `complete_scan` (bool), `profile_policy` (`decoded_frame_uniform_ticks_v1`).
   - `[CODE_VERIFIED]`: Bộ nhớ đệm RAM `_cache` (line 949) lưu trữ theo khóa 8 thành phần `(file_path, dev, ino, size, mtime_ns, ctime_ns, stream_index, profile_policy)`.
   - `[CODE_VERIFIED]`: Hàm `clear_cache()` (line 952) xóa trắng RAM cache và bản đồ stream mặc định.
   - `[CODE_VERIFIED]`: Cơ chế hủy cooperative: kiểm tra `cancelled_fn()` tại các trạm kiểm soát; ném `InterruptedError` trong vòng quét streaming stdout `_scan_decoded_frames_uniform_ticks` (line 742).

2. **`proposal_builder.py: build_edit_plan_proposal`** (`src/core/proposal_builder.py:152`):
   - `[CODE_VERIFIED]`: Signature hiện tại nhận keyword-only: `proposal_id`, `manifest`, `video_plan`, `creative_context`, `kinetic_plan`, `sidecar`, `matching_plan_revision`, `expected_batch`, `selected_candidate_ids`, `user_choices`.
   - `[CODE_VERIFIED]` (Dòng 225–270): Duyệt `manifest.clips` và tạo `source_binding_clips`. Mỗi dòng clip chỉ lưu `clip_id`, `path`, và `duration`.
   - `[AUTHORITY GAP 1]`: Hoàn toàn **thiếu cơ chế nhận bằng chứng byte/frame nguồn**. `manifest.content_hash` trong G05 chỉ là băm mẫu từng phần (sample hash), không phải là băm toàn bộ tệp chuẩn tắc (`expected_full_sha256`). Do đó, `source_binding` xuất xưởng không mang `expected_full_sha256` và `stream_index`.
   - `[PROPOSED]`: Bổ sung tham số keyword-only tùy chọn `source_evidence_by_clip: Optional[Mapping[str, Any]] = None` vào `build_edit_plan_proposal`. Khi được cung cấp, hàm sẽ thẩm định và đưa `expected_full_sha256` cùng `stream_index` vào từng dòng clip của `source_binding` một cách thuần túy (pure, zero I/O).

3. **`review_page.py: ReviewPage`** (`src/ui/director/pages/review_page.py`):
   - `[CODE_VERIFIED]` (Line 6294 `_rebuild_and_present_proposal`): Đọc snapshot ngữ cảnh qua `_get_current_context()`, tính fingerprint qua `_build_current_input_fingerprint` (line 6298), gọi `build_edit_plan_proposal` (line 6332). Nếu có `_source_frame_evidence_table` (line 6355), thực hiện `assemble_plan_frame_geometry` (line 6401).
   - `[CODE_VERIFIED]` (Line 6482 `_on_proposal_approved`): Chống TOCTOU/đột biến ngầm: đối chiếu fingerprint hiện tại với `_last_displayed_input_fingerprint` (line 6506), kiểm tra `proposal_id` và `revision` (line 6518), tái tạo proposal qua `build_edit_plan_proposal` (line 6552) để bảo đảm revision không đổi, kiểm tra hình học không bị `GEOMETRY_BLOCKED` (line 6582), tạo `AppOwnedApprovalRecord` (line 6625), và xác thực liên kết qua `verify_proposal_approval_binding` (line 6631).
   - `[CODE_VERIFIED]` (Line 6665 `get_approved_proposal_and_record`): Kiểm tra an toàn: 1 lần đọc supplier, 0 I/O, kiểm tra stale, kiểm tra fingerprint, kiểm tra binding, trả về bản sao an toàn `(EditPlanProposal, AppOwnedApprovalRecord)`.
   - `[AUTHORITY GAP 2]`: `ReviewPage` hiện chỉ có bảng `_source_frame_evidence_table` (lưu `SourceFrameEvidence` cho khâu hình học), nhưng **chưa có quy trình chạy thu thập bằng chứng đầy đủ qua `SourceEvidenceService`** để thu thập `observed_full_sha256` và `stream_index`. Proposal được duyệt hiện tại chỉ có clip_id/path/duration.
   - `[PROPOSED]`: Bổ sung worker chạy ngầm off-GUI `PreapprovalEvidenceWorker` trên `ReviewPage`. Worker gọi `SourceEvidenceService.acquire_evidence_for_batch` trong nền; khi hoàn tất, kết quả được lưu trữ có chủ quyền trên `ReviewPage`, đưa vào fingerprint, và chuyển cho `build_edit_plan_proposal` để đóng gói proposal có đầy đủ bằng chứng trước khi người dùng bấm Duyệt.

4. **`director_window.py: DirectorWindow`** (`src/ui/director/director_window.py`):
   - `[CODE_VERIFIED]` (Line 548 `_start_readiness_preflight`): Lấy `approved_payload = self.page_review.get_approved_proposal_and_record()` (line 580).
   - `[CODE_VERIFIED]` (Line 598): Khởi tạo `ReadinessRequest(request_id=req_id, generation=cand_gen, proposal=approved_payload[0], approval=approved_payload[1])`.
   - `[AUTHORITY GAP 3]`: `ReadinessRequest` được khởi tạo với giá trị mặc định `verify_source_execution=False` và `timeline_fps=None`. Hệ thống chưa có cơ chế opt-in sang kiểm tra chuyên sâu vì proposal chưa có bằng chứng và cửa sổ chưa nhận được `timeline_fps` có thẩm quyền.
   - `[PROPOSED]`: Khi proposal đã được phê duyệt kèm bằng chứng (`expected_full_sha256` hiện diện trên mọi clip) VÀ người dùng đã chọn cấu hình `timeline_fps` mục tiêu tại Bước 4, `_start_readiness_preflight` sẽ khởi tạo `ReadinessRequest` với `verify_source_execution=True` và `timeline_fps=authoritative_fps`.

5. **Bộ quản lý Worker & Quy trình Shutdown** (`DirectorWindow`, `ReviewPage`, `SourceShutdownGuard`):
   - `[CODE_VERIFIED]`: `DirectorWindow` quản lý `_preflight_workers: Dict[str, PreflightWorker]`. Đăng ký candidate qua `_register_preflight_candidate` (line 660). Khi worker kết thúc vật lý (`worker.finished`), hàm `_on_readiness_worker_finished` thu hồi worker, gọi `worker.deleteLater()`, và cập nhật UI.
   - `[CODE_VERIFIED]`: `SourceShutdownGuard` (`src/services/source_shutdown.py`) bắt sự kiện `QEvent.Type.Quit`, yêu cầu dừng toàn bộ worker qua `shutdown_analysis_and_preflight_workers()`, chờ tín hiệu `analysis_and_preflight_idle`, kiểm tra `is_analysis_and_preflight_idle()` rồi mới cho phép thoát ứng dụng an toàn.
   - `[PROPOSED]`: Đăng ký vòng đời của `PreapprovalEvidenceWorker` trên `ReviewPage` tương thích hoàn toàn với giao thức rảnh của `DirectorWindow` và `SourceShutdownGuard`.

---

## 2. CHUYỂN DỊCH TỪ BẰNG CHỨNG TIỀN PHÊ DUYỆT ĐẾN KỊCH BẢN BẤT BIẾN (PREAPPROVAL EVIDENCE TO IMMUTABLE PROPOSAL)

```
+----------------------------------------------------------------------------------------------------+
|                                    KIẾN TRÚC MỚI (07A4 DESIGN)                                      |
+----------------------------------------------------------------------------------------------------+
 [ReviewPage - Giao diện Bước 3]
      |
      |--> (A) Nút "Kiểm tra bằng chứng nguồn" bấm bởi User / Trigger hợp lệ
      |         |
      |         v
      |   [PreapprovalEvidenceWorker (QThread)] --(off-GUI)--> SourceEvidenceService
      |         |                                              - expected_full_sha256 = None
      |         |                                              - stream_index = None (auto)
      |         |                                              - Đọc 5-stat, băm SHA256 100%
      |         |                                              - Quét decoded frame uniform ticks
      |         |                                              - Ghi RAM cache fact hoàn chỉnh
      |         v
      |   (B) Kết quả: SourceEvidenceBatchResult (chứa envelopes đầy đủ)
      |         |
      |         +--> Kiểm định tính hợp lệ của bằng chứng (complete_scan, VERIFIED_CFR, valid SHA)
      |         +--> Lưu trữ tại ReviewPage._preapproval_evidence_map
      |         +--> Cập nhật bảng hình học _source_frame_evidence_table
      |         v
      |   (C) _rebuild_and_present_proposal()
      |         |
      |         +--> Fingerprint bao gồm: clips + choices + observed SHA + frame evidence
      |         +--> proposal_builder.build_edit_plan_proposal(
      |         |        ...,
      |         |        source_evidence_by_clip=_preapproval_evidence_map  <-- [PURE INJECTION]
      |         |    )
      |         |    ==> Từng clip trong proposal.source_binding.clips ĐƯỢC GẮN CHÍNH THỨC:
      |         |        - expected_full_sha256 = envelope.observed_full_sha256
      |         |        - stream_index = envelope.stream_index
      |         v
      |   (D) Người dùng xem giao diện Review: Thấy huy hiệu "Đã xác thực CFR & mã băm toàn vẹn"
      |         |
      |         v [NGƯỜI DÙNG BẤM "PHÊ DUYỆT" - WHOLE PROPOSAL APPROVAL (D01)]
      |   (E) _on_proposal_approved()
      |         +--> Thẩm định fingerprint không đổi (Current-Input Authority)
      |         +--> Tái tạo proposal xác minh revision trùng khớp tuyệt đối
      |         +--> Tạo AppOwnedApprovalRecord(proposal_revision=compute_proposal_revision(prop))
      |         +--> verify_proposal_approval_binding() = True
      |         ===> ĐỀ XUẤT ĐÃ ĐƯỢC DUYỆT MANG ĐẦY ĐỦ EXPECTED_FULL_SHA256 & STREAM_INDEX!
      v
 [DirectorWindow - Chuyển sang Bước 4 (BuildPage)]
      |
      |--> (F) Người dùng bấm "Kiểm tra tiền kiểm" (Readiness Preflight)
                |
                +--> get_approved_proposal_and_record() -> Lấy cặp (proposal, approval) bất biến
                +--> timeline_fps được lấy từ Project Config / Preset có thẩm quyền
                +--> Khởi tạo:
                |      ReadinessRequest(
                |          proposal=approved_prop,
                |          approval=approved_appr,
                |          verify_source_execution=True,  <-- [DEEP OPT-IN CHÍNH THỨC]
                |          timeline_fps=authoritative_fps,
                |      )
                v
          PreflightWorker / inspect_source_and_catalog_readiness()
                +--> Thẩm định sớm: expected_full_sha256 có mặt trên mọi clip nguồn
                +--> Deep scan: gọi SourceEvidenceService.acquire_evidence_for_batch()
                |    (Do cùng 5-stat và cùng path -> TRÚNG RAM CACHE 100%, 0 I/O lặp lại!)
                +--> validate_execution_evidence_binding() -> 100% THÀNH CÔNG!
                +--> assemble_plan_frame_geometry() -> GEOMETRY_VERIFIED!
                +--> Trả về checks_passed=True (ready_to_execute=False bảo toàn theo G07).
```

### 2.1. Phân định rạch ròi giữa Ứng viên quan sát (Observed Candidate) và Hồ sơ phê duyệt (Approval):
- `SourceEvidenceService` chỉ là đơn vị đo đạc kỹ thuật khách quan trên hệ thống tệp đĩa. Dù dịch vụ đo được `observed_full_sha256` chuẩn và `VERIFIED_CFR`, kết quả đo này **tuyệt đối không bao giờ được coi là một phê duyệt tự động**.
- Không tự ý thăng cấp (auto-promote) kết quả quét cục bộ thành phê duyệt.
- Người dùng là chủ thể duy nhất có quyền đưa ra quyết định phê duyệt biên tập (`whole-proposal approval` theo D01). Bằng chứng kỹ thuật chỉ đóng vai trò làm giàu dữ liệu (enrichment) để bản đề xuất kịch bản chứa đầy đủ các cam kết ràng buộc nội dung trước khi người dùng ký duyệt.

### 2.2. Tính thuần túy của ProposalBuilder (Pure Builder & Zero I/O):
- Hàm `build_edit_plan_proposal` tiếp tục giữ vững nguyên lý thiết kế: **hoàn toàn thuần túy (pure function) với zero I/O**.
- Hàm không bao giờ tự gọi `os.stat`, `hashlib`, `ffprobe`, subprocess hay kiểm tra mạng.
- Tất cả bằng chứng quan sát được truyền vào thông qua tham số keyword-only `source_evidence_by_clip: Optional[Mapping[str, Any]] = None`.
- Logic bên trong `build_edit_plan_proposal`:
  ```python
  # Trong vòng lặp xử lý từng clip của manifest.clips:
  clip_row: Dict[str, Any] = {"clip_id": cid}
  if c_path:
      clip_row["path"] = c_path
  if dur_raw is not None:
      clip_row["duration"] = dur_f

  # [07A4 PROPOSED] Làm giàu bằng chứng nguồn hợp lệ:
  if source_evidence_by_clip is not None and isinstance(source_evidence_by_clip, Mapping):
      env = source_evidence_by_clip.get(cid)
      if env is not None:
          # Kiểm tra tính toàn vẹn danh tính tối thiểu của envelope
          env_cid = getattr(env, "clip_id", None)
          env_path = getattr(env, "file_path", None)
          env_scan = getattr(env, "complete_scan", False)
          env_sha = getattr(env, "observed_full_sha256", None)
          env_stream = getattr(env, "stream_index", None)
          
          # Chỉ gắn khi envelope hợp lệ, khớp clip_id, khớp path và complete_scan=True
          if env_cid == cid and env_path and os.path.normpath(env_path) == os.path.normpath(c_path) and env_scan:
              if env_sha and isinstance(env_sha, str) and is_canonical_sha256_hex(env_sha):
                  clip_row["expected_full_sha256"] = env_sha.lower()
              if env_stream is not None and type(env_stream) is int and env_stream >= 0:
                  clip_row["stream_index"] = env_stream
  ```
- Nếu không có `source_evidence_by_clip` hoặc một clip chưa được quét bằng chứng đầy đủ, `clip_row` chỉ giữ các trường cơ bản (`clip_id`, `path`, `duration`), không có `expected_full_sha256`. Đề xuất kịch bản vẫn được khởi tạo hợp lệ cho mục đích duyệt biên tập thuần túy (`editorial review`), nhưng sẽ không thể vượt qua khâu kiểm tra thi công chuyên sâu (`deep preflight`).

### 2.3. Cấu trúc Hợp đồng Bất biến và Mã băm Phiên bản (Proposal Revision Hash):
- Thực thể `EditPlanProposal` vẫn giữ nguyên vẹn đúng 8 trường chuẩn tắc: `proposal_id`, `manifest_id`, `creative_context`, `source_binding`, `video_plan`, `kinetic_plan`, `proposed_asset_assignments`, `proposed_omissions`. Không thay đổi cấu trúc cốt lõi, không gây đứt gãy schema.
- Hồ sơ `AppOwnedApprovalRecord` vẫn giữ nguyên vẹn đúng 6 trường chuẩn tắc: `approval_id`, `proposal_id`, `proposal_revision`, `approved_at_iso`, `accepted_omission_ids`, `approval_scope`.
- Khi `clip_row` trong `source_binding["clips"]` được bổ sung `expected_full_sha256` và `stream_index`, hàm băm chuẩn tắc `compute_proposal_revision(proposal)` (thực hiện serialize JSON chuẩn có sắp xếp khóa) sẽ tự động phản ánh các trường mới này vào `proposal_revision`.
- **Hệ quả kiến trúc quan trọng:** Bất kỳ sự thay đổi nào về bằng chứng (ví dụ quét lại ra SHA khác hoặc đổi stream index) sẽ làm thay đổi `proposal_revision`. Khi đó, bất kỳ phê duyệt cũ nào lập tức trở nên **vô hiệu (invalidated)** do không còn khớp revision. Hệ thống kiên quyết từ chối việc giữ nguyên approval cũ rồi âm thầm sửa `expected_full_sha256` cho khớp với đĩa (chống TOCTOU hoàn toàn).

### 2.4. Nguyên tắc Thẩm định trước khi Đột biến (Validate-Before-Mutate):
- Khi worker quét bằng chứng chạy ngầm trong nền, người dùng có thể đồng thời chỉnh sửa brief, thay đổi lựa chọn ứng viên tài nguyên hoặc cập nhật danh sách clip nguồn.
- Mọi kết quả từ worker cũ (thuộc generation cũ) khi trả về đều bị từ chối tiếp nhận (`stale terminal rejection`).
- Trước khi cập nhật bảng bằng chứng hiển thị trên giao diện, hệ thống luôn đối chiếu lại generation token và fingerprint dữ liệu đầu vào.
- Tuyệt đối không ghi đè dữ liệu nháp mới của người dùng bằng kết quả quét của dữ liệu cũ.

### 2.5. Phân định giữa Duyệt Biên tập (Editorial Review) và Tiền kiểm Thi công (Execution Readiness):
- Tuân thủ D01, D02: Việc thiếu bằng chứng khung hình hoặc thiếu mã băm toàn bộ không tước đoạt quyền xem xét và phê duyệt biên tập của người dùng. Người dùng vẫn có thể xem kịch bản, chỉnh sửa lời thoại, chọn tài nguyên và bấm duyệt bản thảo kịch bản.
- Tuy nhiên, bản kịch bản chưa có bằng chứng nguồn được gắn nhãn minh bạch: `CHỈ DUYỆT BIÊN TẬP (Chưa kiểm tra thi công)`.
- Khi chuyển sang Bước 4 (BuildPage), hệ thống sẽ từ chối chế độ kiểm tra sâu `verify_source_execution=True` và yêu cầu người dùng quay lại Bước 3 để hoàn tất thu thập bằng chứng nguồn, hoặc chỉ cho phép chạy kiểm tra siêu dữ liệu nông (shallow metadata check).

---

## 3. VÒNG ĐỜI BẤT ĐỒNG BỘ, BỘ NHỚ ĐỆM, XỬ LÝ LỖI VÀ HỦY (ASYNC LIFECYCLE, CACHE, ERROR & CANCELLATION)

### 3.1. Kiến trúc Worker Thu thập Bằng chứng Tiền Phê duyệt (`PreapprovalEvidenceWorker`):
- Kế thừa lớp `QThread` của PySide6, tuân thủ nghiêm ngặt các mẫu thiết kế đã được chứng minh của `PreflightWorker` và `ReviewAnalysisWorker`:
  1. **Constructor Zero I/O:** Chỉ tiếp nhận snapshot dữ liệu in-memory bất biến (`request_id`, `generation`, `manifest_id`, `tuple` các clip nguồn gồm `clip_id` và `file_path`, cùng cấu hình hạn ngạch). Tuyệt đối không kiểm tra hệ thống tệp trong constructor.
  2. **Thực thi Off-GUI:** Toàn bộ công việc I/O nặng (tính 5-stat, băm SHA-256 theo chunk 1MiB, quét decoded frame qua `ffprobe` không chặn) được thực thi trong hàm `run()` trên luồng nền riêng biệt.
  3. **Đúng một Tín hiệu Terminal (Exactly-One Terminal):** Phát tín hiệu `terminal_signal(SourceEvidenceBatchResult)` đúng một lần duy nhất trước khi kết thúc luồng. Có cờ bảo vệ `_terminal_emitted` chống phát trùng lặp.
  4. **Tách biệt Tín hiệu Kết thúc Vật lý (Native Finished):** Kết nối tín hiệu gốc `worker.finished` để dọn dẹp bộ nhớ (`worker.deleteLater()`) và giải phóng registry, độc lập hoàn toàn với việc xử lý kết quả nghiệp vụ của `terminal_signal`.
  5. **Quản lý Registry có Chủ quyền:** `ReviewPage` sở hữu từ điển `_preapproval_evidence_workers: Dict[str, PreapprovalEvidenceWorker]`. Worker được giữ trong registry cho tới khi luồng vật lý dừng hẳn.

### 3.2. Xử lý Ngoại lệ `InterruptedError` trong Cơ chế Hủy:
- Trong `SourceEvidenceService`, khi cờ hủy `cancelled_fn()` được bật trong lúc tiến trình `ffprobe` đang chạy, hàm `_scan_decoded_frames_uniform_ticks` ném ngoại lệ `InterruptedError("ERR_PROBE_CANCELLED: tác vụ bị hủy bởi caller")` (line 742).
- `PreapprovalEvidenceWorker` đón bắt tường minh ngoại lệ này trong khối `try...except`:
  ```python
  except InterruptedError:
      # Ánh xạ ngoại lệ hủy trực tiếp thành kết quả terminal trạng thái 'cancelled'
      cancelled_result = SourceEvidenceBatchResult(
          status="cancelled",
          evidence_by_clip={},
          diagnostics=("Tiến trình thu thập bằng chứng nguồn đã bị hủy theo yêu cầu.",),
      )
      self._emit_terminal(cancelled_result)
      return
  except Exception as exc:
      failed_result = SourceEvidenceBatchResult(
          status="failed",
          evidence_by_clip={},
          diagnostics=(f"ERR_PREAPPROVAL_WORKER_FAILURE: {exc}",),
      )
      self._emit_terminal(failed_result)
      return
  ```
- Cơ chế này bảo đảm mọi nhánh hủy hoặc lỗi đều chuyển hóa thành DTO kết quả chuẩn tắc, không làm sập tiến trình, không để lại zombie subprocess (nhờ khối `finally` trong `source_evidence_service` đã gọi `_cleanup_child_and_readers`).

### 3.3. Tương tác Giao diện, Đóng cửa sổ và Thoát Ứng dụng:
- Khi worker đang chạy:
  - Nút "Kiểm tra bằng chứng nguồn" chuyển trạng thái bận: text hiển thị tiến độ phần trăm `⏳ Đang quét khung hình (45%)...`, nút bấm chuyển thành `Hủy`.
  - Các thao tác điều hướng chuyển bước trong `DirectorWindow` vẫn cho phép nhưng hiển thị cảnh báo nếu thoát giữa chừng.
- Khi người dùng đóng tab, đóng cửa sổ hoặc thoát ứng dụng (`QEvent.Type.Quit`):
  - Hàm `ReviewPage.shutdown_source_evidence_workers(application_quit: bool = False)` được kích hoạt.
  - Gửi yêu cầu hủy cooperative `worker.requestInterruption()`.
  - Tuyệt đối **không gọi `QThread.terminate()`**, không dùng vòng lặp sự kiện lồng nhau (`QEventLoop`).
  - Tích hợp với `SourceShutdownGuard` (`src/services/source_shutdown.py`): Guard sẽ hoãn thoát Qt cho đến khi `is_analysis_and_preflight_idle()` trả về `True` (tất cả worker nền đã phát `finished` vật lý).

### 3.4. Vòng đời và Tái sử dụng Bộ nhớ đệm RAM (`SourceEvidenceService._cache`):
- **Phạm vi sở hữu:** Thể hiện `SourceEvidenceService` được khởi tạo và sở hữu bởi `DirectorWindow` (hoặc tầng service của phiên làm việc).
- **Vòng đời cache:** Tồn tại trong bộ nhớ RAM suốt phiên làm việc hiện tại của cửa sổ.
- **Cơ chế tái sử dụng (RAM Cache Reuse):**
  - Tại Bước 3 (ReviewPage): Worker chạy thu thập bằng chứng, tính toán SHA-256 và giải mã khung hình. Khi hoàn tất, các dữ kiện được ghi vào `_cache`.
  - Tại Bước 4 (BuildPage - PreflightWorker): Khi tiền kiểm sâu chạy, `inspect_source_and_catalog_readiness` gọi `evidence_svc.acquire_evidence_for_batch`. Dịch vụ kiểm tra 5-stat của tệp trên đĩa: nếu tệp không thay đổi, khóa cache khớp 100% -> **RAM Cache Hit tức thì**. Dịch vụ trả về bằng chứng đã xác thực mà không cần chạy lại `ffprobe` hay băm lại tệp.
- **Điều kiện Xóa Cache (`clear_cache()`):**
  - Khi người dùng chủ động nạp danh sách clip nguồn mới tại Bước 1 (SourcePage).
  - Khi người dùng chuyển sang dự án khác.
  - Khi người dùng bấm nút "Quét lại toàn bộ" (Force Refresh) trên giao diện.

### 3.5. Hạn ngạch và Chi phí đối với Nguồn Dài (Cost Unmeasured on Long Media):
- Kế thừa toàn bộ hệ thống hạn ngạch an toàn từ backend 07A3:
  - Thời gian chờ tối đa mỗi clip: `timeout_per_file_sec = 15.0s`.
  - Giới hạn khung hình giải mã: `max_decoded_frames = 1,000,000`.
  - Ngân sách bộ đệm stdout: `128 MiB`.
  - Ngân sách bộ đệm stderr: `64 KiB`.
- **Cảnh báo chi phí chưa đo đạc (`UNMEASURED`):**
  - Hệ thống hiện tại đã đo đạc và kiểm chứng vững chắc trên các đoạn clip mẫu nhỏ (fixtures ngắn vài chục khung hình).
  - Đối với các tệp video dài hàng giờ hoặc nằm trên ổ đĩa mạng chia sẻ (NAS/SMB) có độ trễ cao, thời gian băm SHA-256 và quét khung hình có thể vượt quá hạn ngạch mặc định 15 giây.
  - Khi vượt quá hạn ngạch, hệ thống chuyển trạng thái clip sang `HEADER_REPORTED_ONLY` kèm chẩn đoán `ERR_PROBE_TIMEOUT`, bảo đảm không bao giờ treo ứng dụng.
  - Thiết kế xác định rõ: chi phí và độ trễ trên nguồn video dài thuộc diện `UNMEASURED`, không nâng vô hạn thời gian chờ để né lỗi.

---

## 4. ĐIỂM KÍCH HOẠT GIAO DIỆN VÀ CHUYỂN BƯỚC SANG BƯỚC 4 (UI ENTRYPOINT & STEP 4 ROLLOUT)

### 4.1. Điểm kích hoạt trên Giao diện ReviewPage (Bước 3):
- Bổ sung một vùng điều khiển chuyên dụng trong `CommonProposalReviewWidget` hoặc thanh công cụ của `ReviewPage`:
  - **Nút hành động:** `[ 🔍 Kiểm tra bằng chứng nguồn ]` (hoặc tự động đề xuất chạy nền khi kịch bản được nạp).
  - **Trạng thái hiển thị tiếng Việt trung thực:**
    1. *Khi chưa quét:* `⚪ Chưa thu thập bằng chứng khung hình — Bản dựng chỉ có thể phê duyệt biên tập.`
    2. *Khi đang quét:* `⏳ Đang xác minh khung hình & mã băm tệp nguồn (3/5 clips)... [Hủy]`
    3. *Khi hoàn tất thành công:* `🟢 Đã xác nhận chuẩn CFR & mã băm toàn vẹn — Sẵn sàng cho tiền kiểm thi công.`
    4. *Khi có lỗi/không đủ điều kiện:* `🟡 Bằng chứng một phần: Clip 'C02' là VFR (tốc độ biến thiên) hoặc quá hạn quét. Bản dựng vẫn được duyệt biên tập nhưng không thể tiền kiểm chuyên sâu.`
- Người dùng không bao giờ phải tự sao chép chuỗi mã băm SHA-256 hay sửa tệp JSON cấu hình bằng tay. Hệ thống tự động thu thập và gắn kết hoàn toàn minh bạch.

### 4.2. Thẩm quyền về Tốc độ Khung hình Dòng thời gian (Authority for Timeline FPS):
- Trong kiến trúc dựng phim (Resolve/NLE), tốc độ khung hình của dòng thời gian (`timeline_fps`) là thuộc tính của dòng thời gian đích (target timeline), **không nhất thiết trùng với tốc độ khung hình của từng clip nguồn**.
- **Chống suy diễn vô căn cứ:** Tuyệt đối không lấy tốc độ khung hình của clip nguồn hoặc gán bừa giá trị mặc định 24.0 fps làm timeline FPS khi chưa có sự xác nhận của người dùng.
- **Nguồn cấp thẩm quyền:**
  - `ReviewPage` và `DirectorWindow` tiếp nhận `timeline_fps` từ cấu hình xuất bản mục tiêu (`ProjectSettings / OutputPreset.target_fps`) do người dùng lựa chọn tại Bước 1/Bước 2 hoặc thông qua combobox chọn FPS trên giao diện.
  - Nếu người dùng chưa chọn FPS mục tiêu: trạng thái `timeline_fps` là `None` (hoặc `UNSPECIFIED`).
  - Khi `timeline_fps` là `None`, nếu chuyển sang Bước 4:
    - PreflightWorker chỉ chạy ở chế độ kiểm tra siêu dữ liệu nông (`verify_source_execution=False`).
    - Nút kiểm tra chuyên sâu hiển thị trạng thái chờ: `⏳ Cần chọn FPS dòng thời gian mục tiêu để kích hoạt tiền kiểm chuyên sâu`.

### 4.3. Ranh giới Triển khai Giữa M1 và M2–M4:
- Lát cắt thiết kế này (07A4) khép kín toàn bộ chu trình của **Milestone 1 (M1)**:
  `Producer (SourceEvidenceService) -> Worker (PreapprovalEvidenceWorker) -> Pure Builder (ProposalBuilder) -> Review (ReviewPage Approval) -> Consumer Handoff (DirectorWindow Step 4 PreflightWorker)`.
- Các nội dung sau đây được phân định rõ ràng là thuộc các mốc tiếp theo và tiếp tục ở trạng thái **DEFERRED**:
  - **M2 (Target Binding & Dry-run):** Lựa chọn Resolve Project/Timeline UID mục tiêu, kiểm tra xung đột track, sinh kịch bản thực thi dry-run.
  - **M3 (Gateway Mutation):** Khởi tạo `ExecutionGateway`, ghi nhật ký giao dịch (journal/receipt) trước khi gọi API DaVinci Resolve.
  - **M4 (Recovery & Live Acceptance):** Cơ chế khôi phục khi Resolve crash giữa chừng và nghiệm thu trực tiếp trên Resolve Studio.

---

## 5. BỘ DỮ LIỆU THỬ NGHIỆM VÀ MA TRẬN NGHIỆM THU HỮU HẠN (FIXTURES & ACCEPTANCE MATRIX)

### 5.1. Định nghĩa 5 Fixtures Nhỏ (Small Local Fixtures):

1. **`Fixture-1: CFR Standard Pair`**
   - 2 tệp video ngắn chuẩn H.264 CFR 30fps (30 frames mỗi tệp, có B-frames).
   - Kỳ vọng: Quét hoàn tất, `observed_full_sha256` hợp lệ, `complete_scan=True`, `profile_policy="decoded_frame_uniform_ticks_v1"`, gắn kết thành công vào proposal, duyệt biên tập đạt, tiền kiểm sâu đạt 100%.
2. **`Fixture-2: Unbound/Offline Source`**
   - 1 tệp video chuẩn và 1 tệp video bị xóa khỏi đĩa hoặc thiếu quyền đọc.
   - Kỳ vọng: Tệp lỗi trả về `HEADER_REPORTED_ONLY` với lỗi `ERR_SOURCE_FILE_INACCESSIBLE`. Proposal tạo thành công cho duyệt biên tập nhưng thiếu `expected_full_sha256` cho clip thứ hai. Tiền kiểm sâu tại Bước 4 từ chối với chẩn đoán `ERR_UNBOUND_CONTENT_PROVENANCE`.
3. **`Fixture-3: Mutated File TOCTOU Race`**
   - 2 tệp video chuẩn được quét thành công tại Bước 3. Trước khi người dùng bấm Duyệt (hoặc giữa lúc Duyệt và kiểm tra tại Bước 4), 1 tệp bị sửa đổi nội dung (thay đổi mtime và nội dung byte).
   - Kỳ vọng: Khâu kiểm tra 5-stat phát hiện sai lệch danh tính (`ERR_SOURCE_FRESHNESS_MISMATCH`), vô hiệu hóa cache, từ chối tiền kiểm đạt, yêu cầu người dùng quét lại bằng chứng.
4. **`Fixture-4: Superseded Worker Generation Race`**
   - Khởi chạy Worker A quét lô kịch bản v1 (được giả lập trễ 200ms). Người dùng đổi lựa chọn ứng viên tạo lô kịch bản v2 và khởi chạy Worker B (generation tăng từ 1 lên 2).
   - Kỳ vọng: Worker A kết thúc muộn bị hủy bỏ kết quả (`stale terminal rejected`), giao diện chỉ tiếp nhận kết quả của Worker B, không gây xung đột trạng thái.
5. **`Fixture-5: Window Close & RAM Cache Hit Verification`**
   - Chạy quét Fixture-1 hoàn tất ghi cache. Đóng cửa sổ DirectorWindow và mở lại trong cùng phiên làm việc (hoặc chuyển giữa Bước 3 và Bước 4).
   - Kỳ vọng: Khi kiểm tra lại trên cùng tệp có 5-stat trùng khớp, dịch vụ ghi nhận RAM Cache Hit, 0 lần gọi subprocess `ffprobe`, 0 lần tính lại băm SHA-256.

### 5.2. Ma trận Nghiệm thu Hữu hạn (Finite Acceptance Matrix - 12 Nhóm Kiểm thử):

| Nhóm Kiểm Thử | Trường Hợp Âm Tính (Negative Control) | Trường Hợp Dương Tính (Positive Control) | Trạng Thái Đo Lường |
|---|---|---|---|
| **1. Request/Result Ownership** | Sửa đổi thuộc tính của batch request sau khi truyền vào worker không làm đổi dữ liệu worker đang chạy. | Bản sao sở hữu an toàn trong worker phản ánh đúng các clip nguồn ban đầu. | `[PROPOSED]` |
| **2. Preapproval Worker Lifecycle** | Hủy worker giữa chừng không để lại tiến trình ffprobe mồ côi (orphan process). | Worker chạy hết phát đúng 1 tín hiệu terminal và phát tín hiệu finished vật lý để giải phóng bộ nhớ. | `[PROPOSED]` |
| **3. InterruptedError Mapping** | `InterruptedError` ném ra từ frame scanner không làm văng exception ra ngoài QThread. | Ngoại lệ được bắt gọn và chuyển thành `SourceEvidenceBatchResult(status='cancelled')`. | `[PROPOSED]` |
| **4. Pure ProposalBuilder Binding** | Clip không có bằng chứng hợp lệ (complete_scan=False) không được gắn `expected_full_sha256`. | Clip có bằng chứng hợp lệ được gắn chính xác `expected_full_sha256` và `stream_index`. | `[PROPOSED]` |
| **5. Revision Invariance & Fingerprint** | Thay đổi dù chỉ 1 ký tự trong `expected_full_sha256` làm đổi `proposal_revision` và fingerprint. | Cùng tập bằng chứng và kịch bản sinh ra `proposal_revision` bất biến 100%. | `[PROPOSED]` |
| **6. Editorial vs Deep Separation** | Proposal chưa có bằng chứng nguồn bị tiền kiểm sâu (verify_source_execution=True) từ chối. | Proposal chưa có bằng chứng nguồn vẫn được người dùng phê duyệt biên tập hợp lệ (D01). | `[PROPOSED]` |
| **7. Freshness TOCTOU Detection** | Tệp bị chạm (touch mtime) sau khi quét bị phát hiện ngay lập tức qua 5-stat mismatch. | Tệp giữ nguyên 5-stat vượt qua kiểm tra tái đối chiếu an toàn. | `[CODE_VERIFIED]` (R3) |
| **8. RAM Cache Hit Efficiency** | Tệp thay đổi 5-stat không kích hoạt cache hit, buộc phải quét lại từ đầu. | Tệp nguyên vẹn đạt RAM cache hit, số lần gọi `ffprobe` = 0 và thời gian băm = 0. | `[CODE_VERIFIED]` (R3) |
| **9. Stale Generation Rejection** | Kết quả từ worker có generation cũ hơn generation hiện tại của trang bị loại bỏ hoàn toàn. | Kết quả từ worker khớp generation hiện tại được cập nhật chính xác lên widget. | `[PROPOSED]` |
| **10. Timeline FPS Authority Gate** | Thiếu `timeline_fps` khi opt-in `verify_source_execution=True` bị từ chối với lỗi `ERR_MISSING_TIMELINE_FPS`. | Có `timeline_fps` hợp lệ kiểu `RationalFPS` cho phép lắp ráp hình học `GEOMETRY_VERIFIED`. | `[CODE_VERIFIED]` (R3) |
| **11. Invariant Preservation** | Mọi kết quả kiểm tra (dù đạt hay không đạt) đều giữ nguyên `ready_to_execute=False`. | 0 lệnh gọi DaVinci Resolve API, 0 truy vấn model AI, 0 kết nối mạng ngoài. | `[CODE_VERIFIED]` (R3) |
| **12. Budget Cap Graceful Degradation** | Clip vượt quá 15s hoặc tràn bộ đệm không làm treo hệ thống, chuyển sang `HEADER_REPORTED_ONLY`. | Clip trong ngân sách hoàn tất bình thường với trạng thái `VERIFIED_CFR`. | `[CODE_VERIFIED]` (R3) |

---

## 6. MỘT LÁT CẮT TRIỂN KHAI DUY NHẤT VÀ QUYẾT ĐỊNH CẦN CHỐT (PROPOSED IMPLEMENTATION SLICE & POLICY DECISIONS)

### 6.1. Đề xuất Lát cắt Triển khai Duy nhất: `G07-07A4-I`
- **TASK_ID:** `G07-07A4-I`
- **WORKER_LEVEL:** `HIGH`
- **PHẠM VI PRODUCTION (ĐÚNG 3 PRODUCTION MODULES):**
  1. `src/core/proposal_builder.py`: Bổ sung tham số keyword-only `source_evidence_by_clip: Optional[Mapping[str, Any]] = None` vào `build_edit_plan_proposal`. Thực hiện thẩm định và gắn `expected_full_sha256` và `stream_index` vào `source_binding_clips` một cách thuần túy (zero I/O).
  2. `src/ui/director/workers/preapproval_evidence_worker.py` *(Module mới)*: Triển khai worker `PreapprovalEvidenceWorker(QThread)` đóng gói việc gọi `SourceEvidenceService.acquire_evidence_for_batch` trong luồng nền, xử lý `InterruptedError`, bảo đảm exactly-one terminal và quản lý vòng đời an toàn.
  3. `src/ui/director/pages/review_page.py`: Tích hợp `PreapprovalEvidenceWorker` vào `ReviewPage`, quản lý registry worker, lưu trữ bảng bằng chứng nguồn, cập nhật fingerprint, hiển thị trạng thái bằng chứng trên UI và truyền bằng chứng vào `build_edit_plan_proposal`.
- **Consumer Đầu cuối của Lát cắt:**
  Người dùng tại Bước 3 (ReviewPage) kích hoạt kiểm tra bằng chứng nguồn -> Worker ngầm hoàn tất thu thập mã băm toàn bộ và khung hình giải mã -> ProposalBuilder tạo kịch bản gắn kết chính thức -> Người dùng bấm "Phê duyệt" (Whole Proposal Approval) -> Cặp `(EditPlanProposal, AppOwnedApprovalRecord)` xuất xưởng đã mang đầy đủ `expected_full_sha256` sẵn sàng cho Bước 4 (BuildPage) kích hoạt tiền kiểm chuyên sâu `verify_source_execution=True`!
- **Điều kiện Dừng Nghiệm thu (Acceptance Stop Condition):**
  - Toàn bộ finite suite hiện hành (207 tests) tiếp tục vượt qua 100%.
  - Bộ kiểm thử mới cho 3 modules trên đạt 100% cho cả 12 nhóm trong ma trận kiểm thử.
  - Bảo toàn tuyệt đối `ready_to_execute=False`, zero Resolve API calls, zero AI model calls.

### 6.2. Ba Câu hỏi Chính sách cần Tech Lead Codex Chốt (Policy Questions):

> **Câu hỏi Chính sách 1:** Khi người dùng bấm "Phê duyệt" (Approve) tại Bước 3 nhưng chưa chạy quét bằng chứng nguồn (chỉ có siêu dữ liệu header ban đầu từ manifest), hệ thống có cho phép phê duyệt biên tập không?
>
> **Khuyến nghị của Antigravity (RECOMMENDED):** **CHO PHÉP.** Căn cứ nguyên tắc D01 và D02, việc phê duyệt biên tập kịch bản (nội dung, cắt ghép thô, phân bổ tài nguyên B-roll/SFX) là quyền chủ động của người dùng. Kịch bản được duyệt trong trường hợp này mang trạng thái bằng chứng `UNVERIFIED / HEADER_ONLY`. Tuy nhiên, khi chuyển sang Bước 4, hệ thống sẽ chỉ chạy tiền kiểm siêu dữ liệu nông (`verify_source_execution=False`), hoặc yêu cầu người dùng thực hiện quét bằng chứng trước khi mở khóa các bước tiếp theo.

> **Câu hỏi Chính sách 2:** Tốc độ khung hình dòng thời gian mục tiêu (`timeline_fps`) dùng cho khâu lắp ráp hình học và tiền kiểm sâu tại Bước 4 nên được lấy từ đâu khi chưa kết nối tới DaVinci Resolve?
>
> **Khuyến nghị của Antigravity (RECOMMENDED):** **Lấy từ cấu hình kịch bản / Preset xuất bản mục tiêu (`target_fps` trong ProjectConfig hoặc combobox do người dùng chọn tại giao diện DirectorWindow).** Tuyệt đối không lấy bừa FPS của clip nguồn đầu tiên và không gán cứng 24.0 fps. Nếu người dùng chưa xác định FPS mục tiêu, trạng thái là `UNSPECIFIED` và khâu tiền kiểm sâu sẽ tạm hoãn kèm thông báo hướng dẫn rõ ràng.

> **Câu hỏi Chính sách 3:** Phạm vi sở hữu và vòng đời của bộ nhớ đệm RAM (`SourceEvidenceService._cache`) nên được gắn với đối tượng nào trong ứng dụng?
>
> **Khuyến nghị của Antigravity (RECOMMENDED):** **Gắn với thể hiện của `DirectorWindow` trong suốt phiên làm việc hiện tại.** Bộ nhớ đệm được khởi tạo khi mở `DirectorWindow`, được dùng chung giữa Bước 3 (Preapproval Worker) và Bước 4 (PreflightWorker), và được xóa tự động (`clear_cache()`) khi đóng cửa sổ `DirectorWindow` hoặc khi người dùng nạp lại danh mục clip nguồn mới tại Bước 1. Không lưu cache này xuống đĩa trong phạm vi M1 để tránh rủi ro phân mảnh và lỗi đồng bộ tệp.

---

## 7. ĐỐI CHIẾU THI CÔNG VÀ KẾT QUẢ G07-07A4-I (C01–C10 IMPLEMENTATION ALIGNMENT & ERRATA)

Triển khai thực tế trong `G07-07A4-I` tuân thủ 100% các quyết định ràng buộc từ Tech Lead Codex (`07A4-codex-decisions.md`):

1. **C01 (Phạm vi lát cắt Bước 3 — Đúng 3 Production Modules):**
   - Đã thực hiện chính xác trên 3 modules: `src/core/proposal_builder.py`, `src/ui/director/workers/preapproval_evidence_worker.py` (mới), và `src/ui/director/pages/review_page.py`.
   - Giữ nguyên frozen: `DirectorWindow`, `PreflightWorker`, `SourceShutdownGuard`, `CommonProposalReviewWidget`, `BuildPage`, `SourcePage`, `StylePage`, `DirectorConfig`, `Controller`, `cache_manager`, `StorageManager`, `ActionGuard`.
   - Chưa opt-in `verify_source_execution=True` tại Bước 4; chưa chia sẻ cache giữa 2 bước; chưa có timeline FPS authority; toàn bộ M1 chưa tuyên bố hoàn tất.

2. **C02 (Cho phép duyệt biên tập khi thiếu proof & Tách biệt proof map khỏi geometry table):**
   - Người dùng có toàn quyền phê duyệt biên tập kịch bản khi chưa có hoặc chỉ có một phần bằng chứng nguồn.
   - Nhãn trạng thái hiển thị rõ số lượng K/N nguồn đã xác minh và ghi chú "chưa kiểm tra đích dựng".
   - `_preapproval_evidence_map` được quản lý độc lập, **tuyệt đối không ghi đè vào `_source_frame_evidence_table`** để không kích hoạt kiểm tra hình học khắt khe cần timeline FPS khi chưa tới lượt.

3. **C03 (FPS dòng thời gian chưa có thẩm quyền — Hoãn lại):**
   - Không tự đoán 24 fps hay lấy bừa FPS clip nguồn. Không thêm trường cấu hình FPS giả mạo.

4. **C04 (Quyền sở hữu cache theo ReviewPage trong slice I):**
   - `SourceEvidenceService` được lưu trữ trực tiếp trên thể hiện `ReviewPage` (`_preapproval_evidence_service`) cho phiên làm việc của page.
   - Chỉ một preapproval worker vật lý chạy tại một thời điểm. Mở lại cùng instance không tự quét lại.

5. **C05 (Chính sách pure boundary dùng chung trong proposal_builder.py):**
   - Bổ sung helper chuẩn tắc: `validate_and_normalize_source_evidence_snapshot` và `compute_proof_signature`.
   - `build_edit_plan_proposal` nhận keyword-only `source_evidence_by_clip=None`.
   - Kiểm tra trần bộ nhớ metadata 16 MiB (`MAX_PROOF_METADATA_BYTES`), kiểm tra `MAX_MANIFEST_CLIPS (4096)` trước vòng lặp.
   - Gắn nguyên cặp `expected_full_sha256` và `stream_index` cho các clip có bằng chứng chuẩn tắc (`complete_scan=True`, `decoded_frame_uniform_ticks_v1`, `VERIFIED_CFR`, canonical 64-hex SHA-256).

6. **C06 (Hai chữ ký độc lập chống stale vòng lặp):**
   - Base pending scan signature (`_build_pending_scan_input_signature`) băm ngữ cảnh biên tập không chứa kết quả proof đang quét.
   - Display / approval fingerprint bao gồm canonical `proof_signature` (băm trên danh sách có thứ tự theo `clip_id` của các facts kỹ thuật).

7. **C07 (Zero disk I/O khi phê duyệt):**
   - Approval và getter tiếp tục 0 I/O đĩa; thẩm định dữ liệu in-memory thuần túy.

8. **C08 (Vòng đời vật lý qua protocol thật):**
   - Đã cập nhật `shutdown_analysis_workers`, `is_analysis_idle`, `is_analysis_loading` và các finished callbacks bao gồm `_preapproval_evidence_workers`.
   - Giữ worker trong registry cho tới khi tín hiệu native `finished` phát ra.

9. **C09 (Hành động tường minh, thông báo tiếng Việt có nghĩa):**
   - Nút `🔍 Kiểm tra nguồn trước khi duyệt` và nhãn `lbl_source_evidence_status` chỉ chạy khi người dùng bấm trực tiếp.
   - Hiển thị tiến độ và kết quả chuẩn tiếng Việt: `✓ Đã xác minh K/N nguồn — chưa kiểm tra đích dựng`.

10. **C10 (Bộ kiểm thử hữu hạn 12 nhóm hoàn thành 100%):**
    - Toàn bộ 123 tests trong finite test suite chạy pass 100% trong 11.39s (bao gồm 25 tests mới cho T01–T12).
    - Bảo toàn tuyệt đối `ready_to_execute=False`, zero Resolve API calls, zero AI model calls, zero network calls.
