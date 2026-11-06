# G07-07A3 — Nghiệm thu backend source/frame evidence

Ngày 2026-11-02. **ACCEPTED_WITH_RECORDED_LIMITS** sau G07-07A3-R3.
Đóng lát cắt backend 07A3, không đồng nghĩa toàn M1 hoặc Gate G07 hoàn tất.
Task tiếp theo: **G07-07A4-D — Preapproval source binding & deep preflight handoff**, HIGH, design only.

## Bằng chứng Codex xác minh mới

- Report SHA-256 khớp `0ac98dd27f5d32d09e379f698699a4bc59559d165ad5a686fa37d4d9d23e3e87`.
- Chạy lại đúng năm files quy định: **207 passed in 4.34s, exit 0**.
- Copy nguyên scripts review trước vào thư mục mới và thực thi:
  **23/23 original + 25/25 residual + 14/14 final checklist = 62/62 matches**, đều exit 0.
- Evidence: `.bridge/ux-evidence/G07-07A3-R3-CODEX-REVIEW/`;
  `pytest_run.log`, `probes.json`, `residuals.json`, `final_checklist.json` cùng scripts/log tương ứng.
  Probe scripts thu thập kết quả; nghiệm thu dựa trên từng matches và positive controls, không chỉ exit code.
- Code review ba điểm R3: grammar states bắt buộc colon/record/comma/EOF;
  UTF-8 incremental strict và bounded records; required instance fields/count kiểm tra trước clone;
  helper final catalog authority được gọi trước và sau deep scan với baseline cũ.
- SHA PreflightWorker và test worker giữ nguyên R2; DirectorWindow/SourceShutdownGuard/BuildPage
  đối chiếu khớp checkpoint frozen. Codex không chỉnh production/test, không Resolve/model/network/user media.

## Phạm vi code được nghiệm thu

1. `src/core/geometry/source_evidence_service.py`: request/result owned; full SHA-256 qua helper G05;
   header và full decoded-frame scan profile `decoded_frame_uniform_ticks_v1`;
   source five-stat across phases; resource/cancel/deadline guards và RAM complete-proof cache.
2. `src/core/source_catalog_readiness.py`: metadata default tương thích; deep opt-in;
   expected SHA lấy từ owned proposal; shared evidence binding validator;
   source/catalog authority recheck và actual Formula B assembler.
3. `src/ui/director/workers/preflight_worker.py`: request owned, off-thread producer/consumer,
   semantic terminal binding, exact-one terminal và native finished.
   File này được sửa trong I/R1/R2, giữ frozen ở R3.

| Điều kiện | Kết luận trong phạm vi đo |
|---|---|
| Tiny H264/B-frame, audio global 0/video global 1 | Native FPS/decoded count/full hash đúng; không nhầm packet order hoặc container/audio EOF |
| Missing timing/last duration, count mismatch, decoder error, invalid JSON, resource cap | Không chứng nhận CFR/complete sai trong các fixtures đã kiểm tra |
| Cache selection/default, identity/version và caller rebind | Không re-probe trên hit đúng; không dùng explicit stream làm default discovery |
| Strict typed input/required fields/count/owned graph | Regression probes chặn trước I/O và cách ly aliases |
| Core/worker path/stream/SHA/manifest/five-stat binding | Forged/missing/unbound proof bị từ chối; valid positive proof đạt |
| Source/master/registry/config đổi trong deep scan | Final recheck chặn success |
| Readiness | **ready_to_execute=False** trong mọi kết quả; không unlock legacy phase 0/2 |

## Giới hạn ghi nhận và điểm chưa triển khai

- **Backend API**, chưa phải UI rollout. `DirectorWindow._start_readiness_preflight` vẫn tạo request metadata mặc định.
  `build_edit_plan_proposal` hiện chỉ tạo clip binding gồm ID/path/duration, chưa nhận full SHA/stream proof từ producer.
  Chưa có preapproval handoff; không tự gán observed digest vào một proposal đã được duyệt.
- Cache chỉ RAM theo service instance/run, không persistent/cross-restart. Không thay cache Whisper/G05.
- Profile đầu hẹp: decoded uniform integer presentation ticks, complete EOF và last-duration proof;
  thiếu bằng chứng giữ HEADER_REPORTED_ONLY, không đoán CFR, không tự transcode/waiver VFR.
- Đã đo tiny local fixtures trên Linux; media dài/hàng trăm clips/ổ ngoài chậm và Windows chưa được benchmark/acceptance.
  Default deadline/caps có thể từ chối nguồn lớn; không tuyên bố nguồn ba giờ sẽ hoàn tất trong ngân sách mặc định.
- Cancellation đang được chứng minh tại các checkpoints/tests worker; không có cam kết hard real-time cho mỗi filesystem call.
  Một số metadata recheck loops còn cancellation theo pha thay vì từng stat.
  Consumer rollout phải xét mức hủy/ownership này trước khi mở thi công.
  Private frame scanner có thể raise InterruptedError khi hủy; PreflightWorker chuyển thành terminal cancelled.
  Consumer preapproval mới phải xử lý cooperative cancellation tương đương, không giả định mọi nhánh chỉ trả DTO.
- Chưa có target binding, dry-run executable plan, Gateway plan executor, receipt/journal/recovery của G07 M2–M4.
  Không có mutation Resolve trong nghiệm thu này, không claim production edit/render hay toàn G07 đã xong.

## Quyết định điều phối

Không mở thêm vòng sửa grammar/probe cho các ca đã đóng, không fuzz toàn repo.
Tiếp tục phần M1 còn thiếu bằng thiết kế nối bằng chứng **trước** whole-proposal approval,
giữ current consent/revision/availability tách biệt. G07-07A4-D phải đề xuất một slice triển khai có callgraph thật,
không fake approval để gọi API post-approval, không tự sửa approved proposal, không bật executor.
