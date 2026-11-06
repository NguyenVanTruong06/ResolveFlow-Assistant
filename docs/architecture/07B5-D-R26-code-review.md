# G07-07B5-D-R26 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6 / pseudocode R26, đối chiếu workorder, report và các hợp đồng Journal/Receipt hiện có. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network; không sửa `src/` hay `tests/`.

## Kiểm tra tính toàn vẹn hồ sơ

Các SHA-256 được bàn giao khớp với tệp hiện tại:

- Task R26: `.bridge/task.md` — `d9737d3d464af6dc20443b2b9e43bddf87ed239a3a2e74e24c05552de09ddbb1`
- Review R25: `docs/architecture/07B5-D-R25-code-review.md` — `6988137b2bbc8023b969ac9384d0baddbe519e4b1e521e67e7e200e8cda58eb3`
- Thiết kế R26: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `5b9ebb5cecd0bb4a27d6df9948aaebe29495f84b3cf2c9b7840b991958f4b6fd`
- Báo cáo R26: `.bridge/report.md` — `74667d269728266ce746f6597b799f7870c30854ae8cb13af76a59c91f77e4d2`

R26 đã bổ sung proof normalization bằng JSON clone, identity envelope cho mọi record, reader WAL có giới hạn qua descriptor, và nhiều nhánh receipt repair. Các điểm dưới đây vẫn chặn phê duyệt vì pseudocode còn cho phép bằng chứng không gắn đúng action, hoặc có thể nhả lock dù Receipt chưa được xác minh đầy đủ.

## Findings chặn nghiệm thu

### R26-01 — Geometry proof dùng alias/default thay vì geometry bắt buộc đã được ủy quyền

`verify_readback_proof()` lấy `record_frame` và `duration_frames` bằng `.get(..., 0)` / `.get(..., 1)` tại `07B5-isolated-resolve-acceptance-gate-design.md:1621-1625`. Trong khi đó, schema authority ở Section 2.6 dùng `authorized_record_frame`; action payload nguồn được mô tả với các trường như `source_frame_start` và `source_frame_duration` (ví dụ `:587`, và action binding ở `:932-936`). Không có kiểm tra rằng `record_frame`/`duration_frames` thực sự tồn tại, là `int` thuần, nằm trong giới hạn, hay tương ứng với geometry đã ký.

Nếu trường bị thiếu hoặc tên trường không khớp, verifier âm thầm kiểm tra proof bao phủ `[0, 1)` thay vì khoảng đã duyệt. HMAC hợp lệ cho proof không sửa được sai lệch giữa proof geometry với action geometry.

**Yêu cầu:** Dùng một schema geometry chuẩn duy nhất cho auth và `ACTION_PENDING`; lấy expected start/duration từ các trường bắt buộc, không dùng fallback. Kiểm tra primitive type, miền giá trị và phép cộng có giới hạn trước khi so sánh; bind proof tới đúng track/frame interval và action digest. Thêm probes cho thiếu từng geometry field, alias-only field, `bool`, số âm, bigint overflow, và proof cover `[0,1)` nhưng không cover authorized interval. Mọi trường hợp phải bị từ chối trước `consume_once`/WAL append.

### R26-02 — WAL parser không xác minh payload `ACTION_COMMITTED` bằng payload pending

Trong `_read_and_parse_wal()`, nhánh `ACTION_COMMITTED` chỉ đọc `action_index`, thêm vào `committed_indices`, rồi thay `last_committed` (`:1693-1700`). Không có kiểm tra action type/track, full key/value equality, canonical-byte equality, hay digest giữa `ACTION_PENDING` và `ACTION_COMMITTED`. Ngược lại, nhánh `ACTION_FAILED` có các phép so sánh này (`:1702-1723`). Hợp đồng consumer nguồn thực hiện full payload equality cho commit (`src/services/resolve_execution_engine.py:1334-1353`).

Parser vì vậy có thể chấp nhận WAL trong đó pending mô tả một clip/geometry nhưng committed record mô tả payload khác; restart repair sau đó có thể dựa vào lịch sử không nhất quán. Parser cũng chỉ kiểm tra số lượng commit ở FINISH (`:1749-1754`), không xác minh chỉ mục nguyên thủy và dãy chỉ mục đúng thứ tự `0..planned_actions_count-1`; START được chấp nhận chỉ bằng stage ở `:1681-1684`, không đối chiếu `total_actions` với `auth.planned_actions_count`.

**Yêu cầu:** Áp dụng cùng phép kiểm tra full projection/canonical bytes cho `ACTION_COMMITTED` như `ACTION_FAILED`; kiểm tra action index là `int` thuần và khớp pending, thứ tự kế hoạch không trùng/không hổng; xác minh START control envelope và số action khớp authorization. Thêm probes sửa payload commit, track, index, duplicate/gapped index và START count; tất cả phải fail-closed trước repair hoặc lock release.

### R26-03 — Restart repair đối chiếu nhầm `approval_id` với `authorization_id` và bỏ qua read-back ở nhánh idempotent

Restart repair so sánh `curr_rcpt.approval_id != auth.authorization_id` tại `:2009-2010`. Section 2.6 trước đó phân biệt `approval_id` (phê duyệt proposal) với `authorization_id` (ủy quyền canary/HMAC); đây là hai định danh khác namespace và không được đồng nhất. So sánh này có thể từ chối receipt hợp lệ hoặc khiến ràng buộc receipt không phản ánh approval record đã bind.

Ngoài ra, khi Receipt đã ở trạng thái đích, nhánh `if curr_rcpt.state == target_receipt_state` chỉ chạy `pass` dù comment nói “verify read-back” (`:2012-2015`). Ở nhánh vừa replace, read-back chỉ kiểm tra `verified_rcpt.state` (`:2042-2049`), không đối chiếu toàn bộ DTO/identity với bản ghi mong đợi. Sau đó cả hai đường đều release lock (`:2056-2063`).

**Yêu cầu:** Đối chiếu `approval_id` với approval ID được authorization/proposal digest bind một cách tường minh; không so sánh với `authorization_id`. Trong mọi nhánh—đã-terminal idempotent lẫn vừa ghi—đọc lại Receipt qua descriptor và xác minh toàn bộ DTO cần thiết (execution, approval, target/project, plan/idempotency và trạng thái), không chỉ state. Chỉ release sau khi read-back đầy đủ khớp; thêm probes cho approval ID khác namespace, Receipt idempotent bị thay identity, và read-back cùng state nhưng khác field.

### R26-04 — Receipt descriptor reads dùng một `os.read()` nên không chứng minh đã đọc hết file hoặc giới hạn kích thước thực

Completion và restart repair đọc Receipt bằng `os.read(fd, MAX_RECEIPT_BYTES)` duy nhất (`:1874-1878`, `:2002-2007`), và read-back cũng dùng một lần đọc (`:1907-1915`, `:2042-2049`). Một `read()` có thể trả short read trước EOF. Nếu artifact lớn hơn giới hạn, thao tác chỉ lấy prefix; parser không biết còn byte phía sau descriptor, nên có thể chấp nhận prefix hợp lệ mà không xác minh phần đuôi. Không có `fstat` trước/sau, xác nhận regular file, kiểm tra size thực và ổn định descriptor như reader WAL.

**Yêu cầu:** Tạo helper receipt descriptor reader riêng: `fstat` xác nhận regular file và size `<= MAX_RECEIPT_BYTES`, đọc loop tới EOF với byte cap, phát hiện growth/short read, so sánh dev/ino/size trước-sau, rồi parse đúng toàn bộ byte đã đọc. Dùng helper cho mọi lần đọc receipt và read-back; oversized, short-read hoặc drift phải giữ lock. Bổ sung probes cho short read, file đúng trần, trần +1, file growth và suffix sau JSON hợp lệ.

### R26-05 — Tạm Receipt không được dọn khi write/fsync thất bại trước replace

Ở cả completion (`:1884-1905`) và restart repair (`:2019-2040`), khối ghi temp chỉ `finally: os.close(tmp_fd)`. Việc `os.unlink(tmp_filename, ...)` chỉ nằm trong `except` bao quanh `os.replace`/directory fsync. Nếu `_write_all`, `os.fsync(tmp_fd)` hoặc `os.close` lỗi trước khi vào khối replace, temp artifact còn lại; điều này trái trực tiếp workorder R26 yêu cầu dọn temp khi có lỗi trước atomic replace.

**Yêu cầu:** Bọc toàn vòng đời temp bằng một `try/finally` có cờ `published`; luôn đóng fd và chỉ unlink đúng tên temp đã tạo bởi transaction nếu replace chưa publish thành công. Không swallow lỗi dọn dẹp làm mất lỗi gốc; ghi nhận cleanup failure và giữ target lock. Thêm fault probes cho lỗi partial write, fsync, close và replace, xác nhận file Receipt cũ nguyên vẹn, không có temp sót (hoặc failure được báo rõ), lock vẫn giữ.

### R26-06 — Proof timestamp chỉ kiểm tra cú pháp UTC, không kiểm tra freshness TTL

Verifier chỉ đối chiếu `observation_timestamp_iso` với regex canonical UTC (`:1574-1577`), rồi tiếp tục xác thực HMAC. Không parse thời gian, không so với đồng hồ Coordinator-owned, và không áp dụng TTL 300 giây. Trong khi boundary matrix vẫn yêu cầu từ chối proof cũ hơn 300 giây (`:2086`, `WAL-R26-01i`). Một proof HMAC hợp lệ đã hết hạn vẫn có thể được tiêu thụ để terminalize phiên.

**Yêu cầu:** Parse timestamp theo định dạng UTC chuẩn, lấy thời gian hiện tại từ clock do Coordinator sở hữu, từ chối timestamp tương lai ngoài sai số được định nghĩa và tuổi vượt `RECEIPT_FRESHNESS_TTL_SECONDS=300`; quy định rõ clock rollback/monotonic policy. Thêm probes ở đúng 300 giây, 300 giây + epsilon, timestamp tương lai, timestamp lỗi lịch và replay sau TTL. Không ghi WAL/consume proof khi stale.

## Những phần R26 đã cải thiện

- `verify_readback_proof()` đưa type gate, clone JSON và kiểm tra schema vào cùng `try/except`; các lỗi ngoài dự kiến được chuẩn hóa thành `ReadbackReceiptVerificationError` (`:1467-1489`, `:1640-1643`).
- Identity envelope được gọi trên từng record đã parse (`:1660-1672`) và các FINISH writer hiện phát đủ năm trường nhận dạng (`:1845-1864`, `:1981-1994`).
- WAL reader dùng descriptor, bounded loop, kiểm tra file growth/inode/size và bắt buộc newline kết thúc (`:1421-1444`, `:1650-1658`).
- Lock file và receipt temp dùng `O_NOFOLLOW`; temp dùng token ngẫu nhiên và `O_EXCL` (`:1884-1890`, `:2019-2025`).
- Restart repair có ánh xạ terminal state riêng và kiểm tra xung đột trước khi cập nhật (`:1958-1970`, `:2009-2054`).

## Quyết định và bước tiếp theo

R26 **chưa được nghiệm thu**. Đóng sáu findings R26-01 đến R26-06 trong một hiệu chỉnh thiết kế bounded; cập nhật pseudocode và fixtures tương ứng. Giữ `ready_to_execute=False`, Gate G07 và M1/M2/M3 ở trạng thái OPEN. Không mở triển khai adapter/live Resolve, không sửa `src/` hoặc `tests/`, và không chạy pytest/app/Resolve/IPC/FusionScript/AI/network trong workorder thiết kế tiếp theo.
