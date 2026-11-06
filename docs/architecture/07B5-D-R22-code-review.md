# G07-07B5-D-R22 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6 và pseudocode R22, đối chiếu hợp đồng Receipt/Journal hiện có. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.

## Kiểm tra tính toàn vẹn hồ sơ

Các hash bàn giao khớp với tệp trên workspace:

- Task R22: `.bridge/task.md` — `51cdfe0df2cdad39972c2d5fd397d69773018a24d46a1c415c562d3a542e553e`
- Review R21: `docs/architecture/07B5-D-R21-code-review.md` — `953ffe0a6faa11bc1ec11a1712fd5795268417fe8a54cabcf6d1c34c84f3b063`
- Thiết kế R22: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `3efe778c61ed9553677d33ba44e1698bd377f2fcbd9ebf0231b800b6b9ad1a4b`
- Báo cáo R22: `.bridge/report.md` — `9271ea611d86b32dafa5ff55871efb2a93b3e8e72e4fdde8b7c6763fe9bc6405`

R22 đã xử lý đúng hướng các findings R21 về `VALID_RECEIPT_STATES`, rollback trước commit, clock ownership và canonical snapshot MAC. Tuy nhiên pseudocode chi tiết vẫn chưa thực thi nhất quán một số cam kết mới, đặc biệt ở đường ghi Receipt/Journal và descriptor anchoring.

## Findings chặn nghiệm thu

### R22-01 — Outcome records chưa giữ nguyên action payload đã được bind trong WAL

Trong `execute_pre_terminal_completion_transaction()` (khoảng dòng 1994-2064), `ROLLBACK_DONE` chỉ ghi identity, rollback metadata và receipt/nonce; nó không mang nguyên action payload đã ghi trong `ACTION_PENDING`/`ACTION_COMMITTED`. Nhánh `ACTION_FAILED` tự tạo payload mới với `failed_action_index=0`, `mutation_clean_rejected=True` và thông báo “rejected cleanly before mutation”, thay vì chiếu payload pending đã được xác thực. `validate_raw_journal_record()` chỉ kiểm schema envelope/kiểu trường; nó không chứng minh stage payload tương ứng hoặc tính đúng của khai báo “clean reject”. Replay hiện có yêu cầu payload giữa `ACTION_PENDING` và outcome khớp 1:1 (`src/services/resolve_execution_engine.py`, validator WAL), nên các bản ghi mới không thể được xem là nguồn sự thật nhất quán. Một cờ do Coordinator tự đặt cũng không chứng minh kết quả quan sát nói rằng item vắng mặt.

Vị trí: thiết kế khoảng `1994-2067`; đối chiếu action payload validation trong `src/services/resolve_execution_engine.py` và schema `src/core/contracts/execution_receipt_contracts.py`.

**Yêu cầu:** Outcome phải kế thừa chính xác projection đầy đủ của payload action đã commit/pending theo schema từng stage. `ACTION_FAILED` chỉ được phát khi proof đã xác thực ràng buộc đúng execution/action và chứng minh điều kiện thất bại tương ứng; không tự đặt `mutation_clean_rejected=True`. Nếu không thể chứng minh outcome cụ thể, giữ `IN_DOUBT` và khóa.

### R22-02 — Receipt replacement vẫn tạo JSON không phải `ExecutionReceipt`

`_durable_atomic_replace_receipt()` (khoảng dòng `1832-1862`) ghi object chỉ gồm `execution_id`, `state`, `updated_at_iso`. Đây không phải serialized `ExecutionReceipt`: các trường bắt buộc và metadata hiện hữu bị bỏ mất. Vì vậy thao tác atomic replace sẽ thay receipt hợp lệ bằng artifact không thể khôi phục qua `receipt_from_json()`/`ExecutionReceiptService.load_receipt()`, dù các bước fsync thành công. Đây là lỗi dữ liệu bền vững, không được giải quyết chỉ bằng `os.replace`.

Đối chiếu nguồn: `src/core/contracts/execution_receipt_contracts.py:258-360`; `src/services/execution_receipt_service.py:384-455`.

**Yêu cầu:** Đọc và parse receipt hiện hành dưới lock, transition nguyên object bằng hợp đồng `ExecutionReceiptService` (hoặc hàm sở hữu cùng schema/state graph), serialize toàn bộ DTO chuẩn tắc, rồi atomic replace. Trước khi công bố thành công, đọc lại và xác minh artifact vừa ghi; mismatch phải giữ khóa.

### R22-03 — Directory descriptors được mở nhưng không được dùng làm authority cho file operations

R22 mô tả `openat`/descriptor anchoring, nhưng pseudocode vẫn ghép `lock_path` và `wal_path` rồi gọi `os.open(path, ...)` không có `dir_fd` (`1540-1544`, `1632-1638`, `2070-2071`, `2185-2187`). `_read_and_parse_wal()` còn gọi `os.path.exists()` trước khi mở (`1634-1637`), tạo race giữa check và open. Receipt temp/target cũng dùng full paths với `os.open()`/`os.replace()` (`1837-1859`). `O_NOFOLLOW` ở file cuối không neo thư mục cha và không loại bỏ thay thế directory path.

**Yêu cầu:** Dùng `os.open(name, ..., dir_fd=self._locks_dir_fd/_journals_dir_fd)` cho mọi lock/WAL/temp entry; dùng `os.replace(src, dst, src_dir_fd=..., dst_dir_fd=...)` hoặc giao thức tương đương. Loại bỏ `exists()` làm bằng chứng; xác minh descriptor cha và `fstat` file, đọc đủ tới EOF bằng vòng lặp có kiểm tra growth/identity. Mọi lookup, append, replace và recovery phải dùng cùng anchored directory authority.

### R22-04 — Restart repair không xử lý trạng thái `FINISHED_SUCCESS`

FSA cho phép `COMMITTED -> FINISH` và phân loại `FINISHED_SUCCESS` (`1048-1053`). Nếu crash sau khi FINISH đã fsync nhưng trước receipt update/lock release, `execute_restart_repair()` chỉ xử lý `FINISHED_ROLLED_BACK`, `FINISHED_FAILED` và `FINISHED_CANCELLED_CLEAN` (`2215-2250`). Không có nhánh cho `FINISHED_SUCCESS`; hàm rơi xuống `ERR_UNHANDLED_REPAIR_STATE`, giữ khóa vĩnh viễn dù WAL đã có terminal success hợp lệ. Crash matrix cần có trường hợp này vì nó nằm giữa WAL terminalization và receipt synchronization.

**Yêu cầu:** Định nghĩa repair `FINISHED_SUCCESS` với đối chiếu receipt/authorization/ledger và đúng transition sang `COMPLETED`; hoặc ghi rõ vì sao state này không thuộc coordinator và chuyển quyền sở hữu an toàn. Không release lock chỉ dựa trên state enum; kiểm tra WAL tới EOF, receipt hiện hành và idempotency/target ledger phải cùng khớp.

### R22-05 — WAL revalidation chưa thực hiện bounded, stable descriptor read như đã cam kết

`_read_and_parse_wal()` mở file rồi dùng một lần `os.read(fd, st_size + 1024)` (`1637-1647`). Một `read()` không được đảm bảo trả toàn bộ yêu cầu; code không lặp đến EOF, không kiểm tra descriptor sau đọc (dev/ino/size), và không có giới hạn số vòng/byte trong chính đoạn pseudocode này. Dữ liệu có thể tăng sau `fstat`, hoặc short read bị nhầm thành dữ liệu đầy đủ. Ngoài ra `execute_pre_terminal_completion_transaction()` tính `next_seq` bằng lọc `[l for l in f if l.strip()]` (`1995-1998`), làm mất framing/empty-line evidence sau khi đã tuyên bố parser strict.

**Yêu cầu:** Một helper đọc duy nhất qua anchored fd, bounded theo `MAX_WAL_BYTES`, loop đến EOF, kiểm tra exact byte count cùng `fstat` trước/sau, newline framing và hash; dùng snapshot trả về từ lần đọc đó để tính seq, không mở/parse lại bằng text filter. Bất kỳ growth, short read hoặc drift nào phải fail-closed trước consume/write.

### R22-06 — Tên file lock vẫn có thể va chạm giữa UID hợp lệ và UID được hash

`_safe_timeline_lock_filename()` giữ nguyên UID khớp `^[0-9a-zA-Z_\-]{1,64}$` thành `{uid}.lock`, nhưng UID không khớp regex được đổi thành `timeline_{sha256[:32]}.lock` (`1529-1534`). Một UID hợp lệ có thể chính là `timeline_<32 hex>`, trùng tên lock được tạo cho một UID bất hợp lệ khác có cùng digest-prefix. Khi đó hai timeline khác nhau chia sẻ cùng OS lock; behavior có thể gây khóa nhầm và identity confusion. Slug 128-bit prefix cũng không được kiểm tra collision registry.

**Yêu cầu:** Dùng một namespace duy nhất cho tất cả UID, ví dụ `timeline_<sha256(full canonical UID)>.lock`, sau strict UTF-8/type validation; không có nhánh dùng UID thô làm tên file. Nếu cần xử lý collision, lưu/đối chiếu đầy đủ UID trong metadata bền vững dưới lock và fail-closed khi mismatch.

## Những điểm R22 đã cải thiện

- FINISH chuyển `final_receipt_state` vào `action_params` và outcome records có các trường top-level theo allowlist.
- Receipt state được ánh xạ sang `ABORTED`/`ROLLED_BACK`, loại bỏ `FAILED` khỏi danh mục state.
- Thêm atomic replace, short-write loop và fsync thư mục cho receipt update ở mức mô tả.
- Coordinator sở hữu đồng hồ hiện tại; FSA cấm `PENDING -> ROLLBACK_PENDING`; snapshot MAC dùng JSON chuẩn tắc có schema version.
- Restart repair có bước đọc lại và thẩm định WAL trước khi sửa, theo thiết kế.

## Quyết định

R22 **chưa được nghiệm thu**. Các findings R22-01 đến R22-05 chặn tính nhất quán của WAL/Receipt và khả năng phục hồi; R22-06 chặn tính duy nhất của target lock. Giữ `ready_to_execute=False`; không mở G07-07B6-I hoặc bất kỳ live Resolve work nào. Workorder tiếp theo nên là **G07-07B5-D-R23**, DESIGN ONLY, đóng các findings bằng pseudocode thống nhất với DTO nguồn và một crash matrix bao phủ cả `FINISHED_SUCCESS`.

Đây là static review; không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.
