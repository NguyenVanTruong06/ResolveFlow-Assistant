# G07-07B5-D-R25 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6/pseudocode R25, đối chiếu hợp đồng Journal và Receipt hiện có. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.

## Kiểm tra tính toàn vẹn hồ sơ

Các hash bàn giao khớp với tệp hiện tại:

- Task R25: `.bridge/task.md` — `d111c00cffbc8f2497799ca59fc23a2aa71cf8661687d169a6e33f0de6d351fa`
- Review R24: `docs/architecture/07B5-D-R24-code-review.md` — `904d136d7b80f05ffd7bea0cc1a8130017ea789345074f6b92825d12d82d349a`
- Thiết kế R25: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `ebd54fbb3facb9cda11b83827bd2d4359c3fb2b563a23d34ac694410024b6d79`
- Báo cáo R25: `.bridge/report.md` — `e1e69be9ee71903c517c706312803f4f442db639dcdf39a4521ce49d53770705`

R25 đã cải thiện ownership của lock khi lỗi metadata, thêm payload equality khi parse `ACTION_FAILED`, ràng buộc snapshot với WAL mới đọc, và dùng serializer Journal nguồn. Các điểm còn lại dưới đây vẫn chặn nghiệm thu vì proof hoặc WAL có thể không đại diện cho đúng action/target, hoặc recovery có thể nhả khóa với Receipt chưa được sửa chữa.

## Findings chặn nghiệm thu

### R25-01 — Readback proof chưa bind action index và hình học vào action đang pending

`verify_readback_proof()` kiểm tra `action_index >= 0`, track type/index hợp lệ, và frame bounds có thứ tự (`07B5-isolated-resolve-acceptance-gate-design.md:1446-1484`). Phần cross-record binding sau đó chỉ đối chiếu authorization ID, execution ID, plan key, project/timeline UID và payload digest (`:1530-1543`). Nó không yêu cầu `action_index` bằng action index trong `fresh_wal.pending_action_record`, cũng không bind `target_track_type`, `target_track_index`, `scanned_frame_start` và `scanned_frame_end` với các giá trị đã ủy quyền trong auth/WAL.

`complete_pre_terminal_mutation_with_proof()` chỉ xác nhận trạng thái `PENDING`, rồi xác minh proof với auth; FINISH lấy `failed_action_index` trực tiếp từ proof (`:1734-1742, 1767-1785`). Với proof có HMAC hợp lệ nhưng chỉ mục/track/frame khác action đang chờ, coordinator vẫn có thể ghi nhận kết quả proof cho sai action/geometry.

**Yêu cầu:** Truyền action đang chờ vào verifier hoặc thực hiện binding ngay trong coordinator trước `consume_once`: so sánh action index, track type/index, record frame và end frame chính xác với auth cùng pending action. Nếu canary chỉ có một action, bắt buộc index bằng `0`. Không tiêu thụ proof và không ghi WAL khi bất kỳ trường nào không khớp.

### R25-02 — FINISH writer thiếu identity envelope; parser không kiểm tra identity trên mọi record

Thiết kế yêu cầu đủ năm identity fields (`authorization_id`, `execution_id`, `target_project_uid`, `target_timeline_uid`, `plan_idempotency_key`) trong `action_params` của **mọi** record (Section 2.6, khoảng dòng 901 trở đi). Nhưng FINISH trong completion chỉ ghi `authorization_id`, `execution_id` và proof metadata (`:1767-1785`); FINISH do restart repair ghi chỉ có `authorization_id`, `execution_id`, `final_receipt_state` (`:1905-1917`). Cả hai thiếu project UID, timeline UID và plan key.

Parser `_read_and_parse_wal()` không kiểm tra identity envelope ở từng parsed entry: record đầu tiên được bỏ qua sau kiểm tra stage (`:1597-1601`), và nhánh FINISH chỉ xét note/final state (`:1661-1693`). Do đó các bản ghi thiếu hoặc mang identity khác vẫn có thể được chấp nhận trong khi pseudocode tuyên bố ràng buộc identity xuyên suốt WAL.

**Yêu cầu:** Dùng helper identity-envelope validator trên từng `JournalEntry` trước chuyển trạng thái FSA, kể cả START, FINISH và mọi record thao tác. Mọi FINISH writer phải phát đủ năm trường; fixture cần chứng minh thiếu/sai từng trường bị từ chối trước receipt mutation hoặc lock release.

### R25-03 — Restart repair nhả khóa mà không đồng bộ hoặc xác minh Receipt ở nhiều terminal state

Trong nhánh `FINISHED_SUCCESS`, receipt chỉ được cập nhật khi state hiện là `EXECUTING`; state khác không bị kiểm tra là `COMPLETED` hay xung đột, không có read-back, rồi khóa vẫn được nhả (`:1877-1903`). Với `FINISHED_FAILED`, `FINISHED_ROLLED_BACK`, và `FINISHED_CANCELLED_CLEAN`, pseudocode chỉ gọi `release_target_lock()` và trả `receipt_state="ABORTED"` cho cả ba (`:1949-1957`); không đọc, cập nhật, hoặc read-back Receipt. Nhánh `FINISHED_ROLLED_BACK` vì vậy có thể báo ABORTED dù Receipt chưa được chuyển sang ROLLED_BACK.

**Yêu cầu:** Mỗi terminal state phải ánh xạ duy nhất sang Receipt state; dưới khóa, đọc và xác minh Receipt identity/state, chỉ cho phép transition hợp lệ hoặc trạng thái đích đã được xác minh idempotent. Thực hiện durable atomic replace và read-back cho mọi transition cần thiết. Mọi mismatch hoặc lỗi I/O phải giữ lock và trả IN_DOUBT; chỉ release sau khi WAL và Receipt cùng được xác minh.

### R25-04 — WAL reader vẫn có thể đọc vượt giới hạn và chấp nhận torn tail

`_read_and_parse_wal()` kiểm tra `st_size` ban đầu nhưng đọc bằng `content += chunk` mà không giới hạn tổng byte đọc hoặc kiểm tra `fstat` sau vòng đọc (`:1561-1575`). File tăng kích thước trong lúc đọc có thể vượt `MAX_JOURNAL_BYTES`; dữ liệu trả về còn gắn `file_size_bytes=st.st_size` cũ với hash/count tính từ content mới (`:1698-1709`). Phần framing dùng `split(b"\n")` và chỉ bỏ phần tử rỗng cuối nếu newline tồn tại, nên EOF thiếu newline vẫn được nhận nếu dòng cuối parse được (`:1576-1589`).

**Yêu cầu:** Dùng bounded descriptor reader giới hạn cả byte thực đọc, kiểm tra `st_dev/st_ino/st_size` trước-sau và `total_read == final_size`; từ chối growth/short read. Bắt buộc framing kết thúc bằng đúng newline theo contract, reject torn tail và dòng rỗng trước khi parse/FSA.

### R25-05 — Các file operation trong pseudocode chưa áp dụng nhất quán O_NOFOLLOW/no-overwrite

Lock file được mở với `O_RDWR | O_CREAT | O_CLOEXEC` nhưng thiếu `O_NOFOLLOW` và không `fstat` xác nhận regular file (`:1327-1333`), trái với quy tắc chung ở Section 7 yêu cầu `O_NOFOLLOW` và `S_ISREG` cho mọi file. Receipt temp dùng tên dự đoán được từ PID và mở với `O_CREAT | O_TRUNC`, không `O_EXCL`/`O_NOFOLLOW` (`:1803-1809, 1887-1890, 1930-1935`). Một symlink/temp file có sẵn có thể khiến thao tác truncate/write chạm dữ liệu ngoài artifact dự kiến.

**Yêu cầu:** Áp dụng `O_NOFOLLOW | O_CLOEXEC` và `fstat` regular-file cho lock, receipt, WAL và temp files. Tạo temp bằng tên ngẫu nhiên/exclusive (`O_CREAT | O_EXCL`), không truncate một tên có thể tồn tại; chỉ replace trong đúng anchored directory descriptor. Nêu rõ cleanup temp khi lỗi mà không xóa file không thuộc transaction.

### R25-06 — Deep-copy và schema boundary chưa nằm trọn trong fail-closed handler

`type(raw_proof) is dict` được kiểm tra, nhưng `copy.deepcopy(raw_proof)` và phép `set(proof.keys())` xảy ra ngoài `try/except` (`:1409-1418`); lỗi clone/kiểm tra key vì vậy không được chuẩn hóa thành `ReadbackReceiptVerificationError` như R25 cam kết. Các trường `observer_identity` và `receipt_status` được so sánh với literal mà không có `type(...) is str` gate (`:1509-1515`).

**Yêu cầu:** Đưa clone, key validation và toàn bộ primitive gates vào một fail-closed boundary; chỉ nhận graph JSON thuần có giới hạn kích thước/độ sâu/node, không thực thi `__deepcopy__` của object caller. Gate kiểu chuỗi tường minh cho mọi string field và chuẩn hóa mọi lỗi thành cùng lỗi kiểm chứng trước khi giữ nguyên lock IN_DOUBT.

## Những điểm R25 đã cải thiện

- Lock fd được đăng ký ngay sau `flock`; lỗi ghi metadata release giữ fd trong registry và giữ target ở `IN_DOUBT_LOCKED`.
- FINISH notes được giới hạn vào allowlist đã nêu; restart repair dùng `journal_entry_to_json_line()`.
- Parser có so sánh payload `ACTION_PENDING`/`ACTION_FAILED` và digest authority.
- Restart repair đã bổ sung so khớp fingerprint snapshot với WAL mới đọc.
- UUID execution ID có dạng lowercase canonical và từ chối whitespace thừa.

## Quyết định và bước tiếp theo

R25 **chưa được nghiệm thu**. Findings R25-01 đến R25-06 cần được giải quyết trước khi mở triển khai adapter hay bất kỳ live Resolve work nào. Giữ `ready_to_execute=False`; không mở G07-07B6-I hoặc G07-07B7-LIVE.

Workorder đề xuất: **G07-07B5-D-R26**, DESIGN ONLY, đóng binding proof-to-pending, identity envelope, receipt restart epilogue, descriptor-safe bounded WAL parsing, file no-follow/no-overwrite và fail-closed proof normalization. Bổ sung fixtures source-aligned cho wrong action index/geometry, thiếu/sai identity trên từng stage, receipt state drift sau restart, torn/growing WAL, symlink/temp collision và lỗi clone/schema. Không sửa `src/` hoặc `tests/`, không chạy pytest/app/Resolve/IPC/FusionScript/AI/network cho tới khi có workorder riêng.
