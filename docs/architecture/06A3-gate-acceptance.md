# G06-06A3 — Nghiệm thu batch consumer, 2026-10-28

**CODE_AND_COMPONENT_DESKTOP_ACCEPTED_WITH_RECORDED_LIMITS.** Đóng phạm vi batch prepare/view/copy (I/R1/R2/R3/FINAL). Đây là nghiệm thu 06A3, không phải đóng toàn bộ G06 hoặc toàn bộ06A hierarchy.

## Bằng chứng đã review

- Codex chạy lại đúng finite6files: **298 passed in50.22s, exit0**. Không full suite. Required success envelope cho prepare_batches/copy_batch đã strict request_id/generation/intent, V1 compatibility riêng. Các source/snapshot/content/terminal ownership regressions trước được giữ.
- Đọc report, metrics.json và desktop_execution.log của Worker; trực tiếp xem case1 widget và case2 desktop screenshots. ReviewPage production trong visible QMainWindow trên DISPLAY=:1, QTest buttons/combobox, worker+A1/A2 thật; đây là COMPONENT_DESKTOP_VERIFIED, không DirectorWindow/Resolve end-to-end.
- Latest normal run exit0, metrics5cases PASS: prepare3batches, distinct1clip/items3, unaccounted1; prepare không ghi clipboard; batch2 copy5743UTF8bytes và digest khớp, batch1 copy lại; source tamper denied và invalidate; cooperative shutdown registry0; V1/V2 controls hiện.
- Latest fault run exit2 có chủ đích, cleanup0running/owned session removed. Normal cleanup metrics:0running, patches_restored=True,clipboard_restored=True,owned_session_removed=True. Không rerun desktop/model trong Codex review.

## Giới hạn bằng chứng / phần còn lại

- SEEDED_CACHE_UI_TEST: video dài thật1201s/noaudio + seed cache v3 negative; không STT/VLM inference mới. Clip ngắn/unaccounted dùng request metadata duration0 có chủ ý để test unknown branch; không phải kết luận detector không đọc được file1s.
- Các default demo dialogue rows bên trái trong component screenshot không phải transcript của fixtures noaudio; không sử dụng làm evidence batch. Full-app Source→Director integration chưa chứng minh từ ảnh này.
- Desktop ca4 gọi shutdown_analysis_workers, không đóng cửa sổ đang pending qua actual closeEvent và không barrier đổi selection/context. Ghi COOPERATIVE_SHUTDOWN_VERIFIED; close/stale-selection là code/offscreen tested, chưa desktop-tested trong run này. Fault có start request rồi raise; không coi đó proof worker đã blocked tại IO vì blocking_prepare trong harness không được cài vào worker.
- Harness cleanup dùng owned session/patch restore nhưng còn HOME env override và so clipboard digest với tập mọi test contents; không tái dùng nguyên harness như chuẩn safety chung. Future desktop harness giữ nguyên HOME/CODEX_HOME, inject cache/manifest/QSettings và XDG riêng; chỉ restore clipboard khi còn đúng last test-owned content để tôn trọng user đổi clipboard. Không yêu cầu lặp các desktop cases đã chứng minh chỉ để nâng cấp harness.
- UI summary spacing/readonly preview usability có thể tinh chỉnh G08; không mở shell redesign trong06A3. Không performance benchmark nhiều giờ footage hoặc chứng minh byte cap tương ứng token limit AI web.
- Batch prompt là source-only local draft; không auto-upload/model, chưa response receiver/merge hierarchy/ledger, chưa asset matching06B, EditPlan06C hoặc Resolve execution07. Source metadata recheck không loại mọi race sau check và không biến sampled hashes thành full-file proof.

## Tiếp theo

G06-06A4-D: thiết kế batch draft receiver và evidence→batch→project handoff, có provenance, budget và progress trạng thái rõ. Một lát cắt triển khai nhỏ sau design review. Không mở lại A1/A2/A3 nếu không có bug mới tái hiện. Không ALL_DONE/G07/shutdown.
