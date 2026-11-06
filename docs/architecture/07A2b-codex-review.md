# G07-07A2b-I — Codex review, 2026-11-02

**CHANGES_REQUIRED**, backend07A2a và07A1 giữ accepted, không mở lại. Report SHA `5b889992b8ba5eda7f82f9382ad3ae4ad9bb2c9b7e496c2d94c6c94248ce6119` khớp;3production hashes đúng;2backend hashes không đổi so acceptance.

Codex chạy lại đúng6files: **51 passed /11.31s /exit0**. Fresh independent slot-boundary Qt probes exit0 tại `.bridge/ux-evidence/G07-07A2b-CODEX-REVIEW/` (probe_review.py,probes.json,probe_execution.log,pytest_run.log). Các probes đăng ký worker chưa start để kiểm tra callback/state boundaries, KHÔNG được mô tả như desktop hay live-thread close proof. Existing suite có real-thread positive; một số shutdown tests chỉ gọi trực tiếp finished handler, cần bổ sung native-thread test.

## A — P1: UI terminal chưa bind outcome / exactly once

DirectorWindow._on_readiness_terminal kiểm tra worker/request generation và current approval, nhưng KHÔNG so outcome.request_id/generation/proposal_id/revision/approval_id với pending request. Probe normalized result OTHER_REQUEST/OTHER_PROPOSAL/OTHER_APPROVAL vẫn được publish checks_passed=True. Terminal chưa có consumed/pending state: terminal failed thứ2 ghi đè completed đầu trong khi registry còn giữ worker. Current getter không thay thế binding của kết quả.

## B — P1/P2: busy UI và callback routing

- `_on_readiness_progress` tìm worker.request, nhưng PreflightWorker chỉ có private _request; progress của actual worker bị bỏ qua (45% không xuất hiện).
- Stale terminal gọi BuildPage.clear_readiness_result, hàm này ẩn/vô hiệu Cancel dù preflight registry còn busy. Registry/controls vẫn giữ là tốt, nhưng mất lối hủy job.
- dropEvent luôn add_paths sau go_to_step(1); go_to_step đã từ chối khi preflight busy nhưng add_paths vẫn chạy (probe1call).
- Native-finished-without-terminal chưa tạo feedback fail trung thực; start/constructor failures chỉ log, không báo status phù hợp. Initial workorder yêu cầu feedback và aggregate physical state, không UI progress giả.

## C — P1: shutdown admission và fail-open query

- shutdown_analysis_and_preflight_workers không đặt admission-closed state. Sau shutdown request, _start_readiness_preflight vẫn tạo/start job mới (probe constructor1/start1), tạo khe hở khi app Quit đã xếp singleShot quit.
- SourceShutdownGuard discovery `except Exception: pass` quanh LibraryJob.isRunning làm mất owner. Probe job query throws: retained=False, app.quit=1. Phải retain+wait unknown, không suy ra không có việc.
- `_on_aggregate_workers_idle` đặt _is_closed=True trước queued closeEvent thực; giữ trạng thái closing riêng đến lúc close được chấp nhận, chỉ emit closed một lần.
- Shutdown Director hiện outer try cho3families; exception ở family1 bỏ lỡ cancel2families còn lại. Cần cancel độc lập và query aggregate physical idle.

## Hướng sửa hữu hạn

Same3files, R1 gộp A/B/C. Pending expected tuple app-owned; normalize và đối chiếu đủ5outcome fields trước publish; one-terminal consumption tách physical registry. Busy/cancel view tách khỏi clear table, progress lấy record coordinator thay phantom worker.request. Close/Quit đóng cổng start trước collect/cancel, keep strong refs kể cả query failures. Tests R01–R12 dùng actual start registration và real QThread barriers cho physical claims. UI plain text và noIO tripwires theo scope cũ; chưa desktop hoặc executor.
