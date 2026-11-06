# Review G04-04A2b-I — CHANGES_REQUESTED

Codex đọc Dock signal/refresh, Studio wrapper/single emit, main coordinator/poll và8tests. Chạy đúng4files quy định: **40 passed in34.18s, final exit0**. Luồng thông thường đạt bằng chứng offscreen: cập nhật visible/hidden, lazy first-open, noecho, wrapper và nút sync. Bộ80 do Worker báo không phải Codex rerun; chưadesktopverified.

## Một residual có bằng chứng: receiver failure qua Qt signal không có retry
Read-only probe dùng actual create_studio_manager với QObject Dock signal và Studio fake, apply_storage_snapshot ném RuntimeError. Kết quả: `EMIT_RETURNED_NORMALLY SLOT_ERRORS=['probe receiver failure']`, exit0. Qt đưa exception slot qua sys.excepthook, không raise ngược emit.
Do vậy dock.refresh_storage_assets vẫn return bình thường sau emit; production refresh_storage advance signature dù Studio fail. Signature không đổi thì không retry; Studio có thể stale đến mutation tiếp. Không coi try/except quanh Dock refresh là proof Studio thành công. First-open snapshot exception hiện cũng chỉ log, chưa có retry.

## Test gap
- test_refresh_exception_does_not_advance_signature tự viết try/except trong test, không gọi production tick. Nó không chứng minh checkpoint/retry của chương trình.
- signature test chỉ tính signature, không assert actual tick update cả2surface.

Task sửa một module main.py: bắt lỗi tại slot boundary, giữ latest pending snapshot và retry receiver kể cả signature không đổi, không giả global atomicity. Chỉ một pending payload và một Qt timer, không rescan/download/worker mới; no echo. Extract helper tick đủ nhỏ để tests gọi đúng production logic. Poll checkpoint là Dock refresh checkpoint; trạng thái pending Studio tách riêng, không nói toàn pipeline đã thành công khi pending còn. Không cần sửa core/G03/receiver đãaccepted.

## Review R1 — retry behavior ACCEPTED; root-config residual R2
Codex chạy đúng4files: **43 passed in37.30s, final exit0**. Đã đọc _StudioSyncCoordinator: slot boundary bắt lỗi, latest pending, timer parent Dock, retry apply và first-open apply failure, production poll helper và tests. R1 retry được chấp nhận trong phạm vi đã giao; chưa desktopverified.

Phát hiện hồi quy cấu hình trong start_app: `sm=StorageManager()` được tạo một lần và lambda timer dùng mãi instance đó. _config chỉ đọc ở constructor. Nếu Settings dùng manager khác configure root B, poll tiếp tục theo root A, không có invalidation để refresh cả2surface.
Probe với TemporaryDirectory, config chung, managerA.configure(A); lấy signature; managerB.configure(B); signature(managerA) vẫn bằng cũ: `CACHED_SIGNATURE_CHANGED=False; CACHED_ROOT=A FRESH_ROOT=B`. Không chạm dữ liệu thật. Đây là chứng cứ code+probe P1, không phải vấn đề performance giả định.
Giao G04-04A2b-R2 chỉ main.py + regression cho production poll nhận current config mỗi tick. Không mở lại retry hoặc receiver toàn bộ.

## Review R2 — CODE ACCEPTED
Codex đọc nhánh poll_storage_status(sm=None), start_app timer gọi fresh branch, và regression chuyển root thực qua tmp config. Giữ explicit sm callers; rootA -> manager khác cấu hình rootB -> poll production fresh -> cả Dock và Studio cóB, không còn managedA, tick tiếp no-op; offline/reconnectB exercise.
Chạy đúng4files theo task: **44 passed in40.64s, final exit0**. R2 cùng wiring04A2b CODE ACCEPTED trong phạm vi code/offscreen. Không yêu cầu thêm code hoặc lặp nhóm test khi chưa có bug mới.
Chuyển WAIT_USER_TEST hữu hạn theo docs/architecture/04A-desktop-acceptance.md. Toàn G04 chưa đóng: 04B search/filter và04C media guards còn deferred. Rút ổ/powerloss, Drive/auth/quota, telemetry teardown và Resolve insertion chưa nghiệm thu bởi gate này.
