# G05-05C2c2-R2 — Codex review, 2026-10-28

Trạng thái: WORKER_RESIDUALS_A_B_ACCEPTED / UI_FINGERPRINT_NEEDS_FIX. Chưa nghiệm thu toàn bộ V2.

## Fresh verification

Đọc report, worker, UI sanitizer/fingerprint/request và regression tests. Chạy đúng sáu suites: **142 passed in118.71s, process exit0**. Log tại .bridge/ux-evidence/G05-05C2c2-R2-CODEX-REVIEW/focused-tests.log.

R2 đã bảo toàn FPS context (probe trả24), dùng as_fraction cho mapper, validate finite speed/time và duyệt producer statuses độc lập. Regression tail word/FPS, NaN, producer error được giữ. Không giao lại các phần này và không yêu cầu sửa thêm core.

## Một cụm lỗi còn lại tại helper fingerprint UI

_canonicalize_plan_primitives tại review_page.py:2257–2282 chưa phải fingerprint chính xác/type-safe:

1. **Null hợp lệ gây crash:** helper kiểm isinstance(obj, Fraction) nhưng module không import Fraction; None không có nhánh riêng nên cũng đi tới đó. Plan có global_hook=None làm request_kinetic_preparation ném NameError khỏi slot. global_hook Optional=None là schema thật của CopilotPlan trong story_copilot.py:56, không phải input giả lập ngoài domain.
2. **Hai plan khác frame bị xem như nhau:** round(float,6) xóa thông tin. Hai plan 24fps, start0, end1.1374999 và1.1375001, pad0.3, clipdur2 cho mapper rec_out lần lượt34 và35 frame nhưng signatures bằng nhau. Kết quả cũ có thể được chấp nhận khi plan đổi tại tie boundary; đây chính là lỗi stale authority đã yêu cầu chặn.
3. **Validation có thể thoát slot:** dict với key hỗn hợp int/str làm sorted(obj.items()) ném TypeError; build v2_sig nằm ngoài try. Probe request với plan {'manifest_id':'M',1:'bad'} thoát TypeError. Generation chưa đổi (0 delta), nhưng vẫn phải return None an toàn và bảo toàn job hiện hữu. Fallback str(obj) cũng không đáp ứng primitive-only.

Probe chạy offscreen bằng TemporaryDirectory, QSettings cô lập và ReviewPage/TimelineMapper thật, không worker/inference/network/Resolve. probe-results.json lưu các giá trị thực; một probe thăm dò trước đó exit1 vì NameError được ghi nhận, lần tổng hợp kiểm soát đạt exit0. Không coi đây là desktop evidence.

## Quyết định

Giao G05-05C2c2-R3, đúng **một production module review_page.py**, chỉ sửa canonical primitive fingerprint và ranh giới try của validation/signature; không sửa worker đã đạt R2 hay core. Null phải được hỗ trợ, số phải giữ exact value/type, key/unsupported object phải reject an toàn trước mutation. Regression hữu hạn cho ba ca trên. Code đạt mới giao V2 desktop; G05 chưa đóng, sau G05 thật đạt nghỉ theo user, không G06/shutdown.
