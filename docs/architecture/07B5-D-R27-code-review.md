# G07-07B5-D-R27 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6 / pseudocode R27, đối chiếu workorder, report, review R26 và hợp đồng Receipt/Journal hiện có. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network; không sửa `src/` hay `tests/`.

## Kiểm tra tính toàn vẹn hồ sơ

Các SHA-256 bàn giao khớp với tệp hiện tại:

- Task R27: `.bridge/task.md` — `8425fe0cf8180e7d0f59705262564dc428f62b458c349c3c27143a77cfc5bc17`
- Review R26: `docs/architecture/07B5-D-R26-code-review.md` — `f9bb8ab0a52636ea5823c62943aa4017522947bc8c8a13818f804958985ee2a9`
- Thiết kế R27: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `c614655e7cb9eee67b15474c811914dc6db630c64328436763309d2038605ae5`
- Báo cáo R27: `.bridge/report.md` — `9c6cc090dc048090b584b293d7a7c9055fe01f5c63fbaeaa51efd6ece4eeaa0c`

R27 đã xử lý phần lớn findings R26: bổ sung TTL proof, receipt reader theo descriptor, payload equality khi commit, trường `approval_id` riêng và lifecycle temp có `published`. Review vẫn tìm thấy các lỗ hổng về binding hình học, stage grammar và tiêu chí DTO/bounded cleanup; vì vậy chưa thể nghiệm thu.

## Findings chặn nghiệm thu

### R27-01 — Geometry chưa được khai báo trong AuthorizationContract hoặc đối chiếu với geometry đã ký

Pseudocode `AuthorizationContract` tại `07B5-isolated-resolve-acceptance-gate-design.md:1223-1232` vẫn chỉ khai báo IDs, digest và `planned_actions_count`; không có `record_frame` hoặc `duration_frames`. Tuy nhiên R27 tuyên bố hai trường này là bắt buộc trong AuthorizationContract và `ACTION_PENDING`. Trong verifier (`:1635-1649`), expected interval chỉ được đọc từ `pending_entry.action_params`, rồi so với proof; không có phép đối chiếu pending geometry với geometry của authorization.

Do đó, một pending record có geometry sai nhưng tự nhất quán với proof vẫn qua binding này, dù geometry đó chưa được chứng thư ủy quyền ký. Ngoài ra, `action_index` pending được kiểm tra hiện diện nhưng không có primitive type gate tại `:1625-1629`; JSON `false` có thể so sánh bằng với index `0` trong Python.

**Yêu cầu:** Thêm geometry vào DTO AuthorizationContract và canonical signed payload với tên trường nhất quán; xác minh pending geometry khớp chính xác authorization trước khi chấp nhận proof. Gate strict `int` thuần cho pending action index và mọi geometry; không dùng equality có thể coi `bool` là `int`. Bổ sung probes cho pending/Auth mismatch, thiếu auth geometry, bool index, và proof bao phủ pending nhưng không bao phủ geometry đã ký. Tất cả phải fail trước consume/WAL mutation.

### R27-02 — FSA chỉ ép thứ tự index cho COMMITTED, không khóa index của PENDING/FAILED; START count thiếu type gate

Nhánh START so sánh `start_planned != auth.planned_actions_count` nhưng không xác minh `type(start_planned) is int` và loại trừ bool (`:1706-1714`). Vì vậy `True == 1` có thể làm count bool vượt qua khi kế hoạch có một action. Nhánh ACTION_PENDING chuyển trạng thái mà không xác minh `action_index` bằng số action đã hoàn tất (`:1717-1721`). Nhánh ACTION_FAILED cũng không xác minh index tiếp theo trước khi chấp nhận FINISH (`:1757-1784`). Chỉ nhánh ACTION_COMMITTED có strict type/order gate (`:1743-1750`).

Một WAL có START/action index sai kiểu hoặc ACTION_FAILED gắn vào index không phải action kế tiếp vẫn có thể được phân loại terminal, dù nhánh commit được kiểm tra chặt hơn.

**Yêu cầu:** Áp dụng strict primitive gate và `action_index == len(committed_indices)` tại ACTION_PENDING, ACTION_COMMITTED và ACTION_FAILED; xác minh count START là `int` thuần, trong miền hợp lệ và bằng auth. Quy định rõ việc ACTION_FAILED có làm kết thúc toàn plan hay có thể tiếp tục; FSA phải nhất quán với quy tắc đó. Thêm fixtures bool count, bool index, failed index nhảy cóc/trùng, và pending sai thứ tự.

### R27-03 — Restart read-back chưa xác minh “toàn bộ DTO” như workorder yêu cầu

Hai nhánh restart được ghi là “full DTO verification”, nhưng thực tế chỉ kiểm tra `execution_id`, `approval_id` và `state` (`:2072-2081`, `:2116-2124`). Workorder R27 yêu cầu thêm `idempotency_key`, `project_id`, `timeline_id`; chính contract `ExecutionReceipt` có các trường này (`src/core/contracts/execution_receipt_contracts.py:258-280`). Nếu Receipt bị thay bằng DTO cùng ba giá trị đang kiểm tra nhưng khác project/timeline hoặc idempotency key, pseudocode vẫn có thể release target lock.

**Yêu cầu:** Trên nhánh idempotent và replace, so sánh read-back với toàn bộ `updated_rcpt` đã mong đợi, hoặc kiểm tra đầy đủ execution/approval/idempotency/project/timeline/state cùng các trường immutable khác. So sánh `verified_rcpt == expected_receipt` là lựa chọn rõ ràng cho nhánh replace; nhánh idempotent phải xác lập expected DTO từ receipt đã đọc dưới lock rồi xác minh các binding của authorization. Chỉ release sau khi phép so sánh toàn DTO thành công. Bổ sung probes cho từng trường identity lệch riêng lẻ trong read-back.

### R27-04 — Giới hạn receipt không nhất quán giữa policy, fixture và contract nguồn

Section 2.6 khai báo `MAX_RECEIPT_BYTES = 5 MiB` (`:898`) và reader mặc định nhận hằng số đó (`:1446`); contract nguồn cũng đặt `MAX_RECEIPT_BYTES = 5 MiB` (`src/core/contracts/execution_receipt_contracts.py:29-33`). Nhưng report R27 và fixture `WAL-R27-01bm` mô tả trần là **1 MiB** (`:2243`). Đây là hai policy khác nhau cho cùng tệp receipt, nên boundary acceptance không xác định được giới hạn thực.

Ngoài ra, `_read_receipt_bytes_bounded()` đọc chunk cố định 64 KiB trong khi còn gần trần (`:1454-1461`), nên với `max_bytes` không chia hết cho chunk, một lần đọc có thể cấp phát quá giới hạn trước khi phát hiện. Reader cũng chưa strict-validate `max_bytes`.

**Yêu cầu:** Chọn một giới hạn duy nhất, đồng bộ constant, workorder, report, fixture và contract; giữ `max_bytes` là int thuần trong miền hợp lệ. Đọc tối đa `min(chunk_size, remaining + 1)` để phát hiện byte vượt trần mà không cấp phát quá mức. Thêm tests ở đúng giới hạn, +1 byte, giá trị `max_bytes` sai kiểu/bool và trần theo constant đã chốt.

### R27-05 — Cleanup temp file nuốt lỗi unlink nhưng vẫn tuyên bố dọn sạch

Hai khối `finally` chỉ bật `published` sau replace và fsync thư mục, nhưng lỗi `os.unlink(tmp_filename, ...)` khi `not published` bị bắt rồi bỏ qua (`:1954-1964`, `:2104-2114`). Nếu dọn temp thất bại vì EACCES/EIO, transaction không ghi nhận cleanup failure; report/fixture vẫn tuyên bố không để sót `.tmp.*`. Điều này làm trạng thái lưu trữ và thông báo lỗi không trung thực, đồng thời không thể xác nhận yêu cầu cleanup.

**Yêu cầu:** Nếu unlink thất bại, phát lỗi cleanup có cấu trúc, giữ target lock/IN_DOUBT và giữ nguyên lỗi persistence gốc làm nguyên nhân phụ; không trả success hoặc khẳng định temp đã được dọn. Xác định riêng trường hợp replace đã thành công nhưng directory fsync thất bại: không thể “thu hồi” replace bằng unlink tên temp đã biến mất, nên phải báo trạng thái publish durability uncertain và giữ lock. Bổ sung fault probes cho unlink EIO/EACCES và lỗi directory fsync sau replace.

## Những phần R27 đã cải thiện

- Proof timestamp được parse thành datetime UTC và kiểm tra future skew / TTL trước khi HMAC acceptance hoàn tất (`:1582-1593`); fixture stale proof đã có.
- Commit branch có key/value/canonical equality, digest và chỉ mục tuần tự (`:1723-1755`); START count được đối chiếu với authorization.
- Receipt reader kiểm tra regular file, giới hạn size, đọc loop và so khớp dev/ino/size trước-sau (`:1446-1469`); được dùng ở receipt read/read-back.
- `approval_id` hiện tách riêng khỏi `authorization_id`, và receipt read-back được thực hiện ở cả nhánh idempotent lẫn replace.

## Quyết định và bước tiếp theo

R27 **chưa được nghiệm thu**. Đóng findings R27-01 đến R27-05 trong một hiệu chỉnh thiết kế bounded, cập nhật pseudocode/fixtures và thống nhất receipt byte cap. Giữ `ready_to_execute=False`; Gate G07 và M1/M2/M3 tiếp tục OPEN. Không mở adapter/live Resolve, không sửa `src/` hoặc `tests/`, và không chạy pytest/app/Resolve/IPC/FusionScript/AI/network trong workorder thiết kế tiếp theo.
