# G07-07B5-D-R21 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6 và pseudocode của R21; đối chiếu hợp đồng Journal/Receipt hiện có. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.

## Kiểm tra tính toàn vẹn hồ sơ

Các hash bàn giao khớp với tệp trên workspace:

- Task R21: `.bridge/task.md` — `5fede9765890e65ab1550530080fa377ff74697b48ee2be698827a2c029cdeb7`
- Review R20: `docs/architecture/07B5-D-R20-code-review.md` — `1e6e3d199a80404d7115e108ce137d77ebb4b955c026a32707449a6233787acc`
- Thiết kế R21: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `61e202aa1e70145e98ababa607fcd5ebadf6c1a2912eb592c93034a95a724b20`
- Báo cáo R21: `.bridge/report.md` — `c3d6743144fc9b416b13287c8ee6d682b3454a2122d0a22390492bb9e805e7db`

R21 cải thiện quyền sở hữu giao dịch và snapshot MAC, nhưng pseudocode vẫn có các đường ghi WAL/Receipt không tương thích schema, restart repair chưa chứng minh trạng thái trên đĩa, và một số cổng an toàn dựa trên dữ liệu/đồng hồ không đáng tin cậy.

## Findings chặn nghiệm thu

### R21-01 — Bản ghi outcome/FINISH do Coordinator tạo không hợp lệ theo Journal contract

Trong pseudocode `execute_pre_terminal_completion_transaction()` (khoảng dòng 1927–1963), `rec1` đặt `action_type="NO_OP"` cho `ROLLBACK_DONE` hoặc `ACTION_FAILED`, chỉ ghi 5 trường identity trong `action_params`, và không tạo các trường stage-specific. `ROLLBACK_DONE` cần `resolve_item_id`, track metadata và bằng chứng rollback; `ACTION_FAILED` cần chỉ mục lỗi, mã lỗi, trạng thái mutation và payload gắn với `ACTION_PENDING`. `rec2` đặt `final_receipt_state` ở top level, trong khi `STAGE_ALLOWED_KEYS["FINISH"]` không cho phép khóa này; contract yêu cầu dữ liệu đó nằm trong `action_params`. Các record này vì vậy bị `validate_raw_journal_record()` từ chối hoặc không thể được FSA/ledger đối soát hợp lệ.

Đối chiếu nguồn: `src/core/contracts/execution_receipt_contracts.py:452-548, 561-648`; pseudocode R21 khoảng `1934-1963` và restart FINISH khoảng `2074-2089`.

**Yêu cầu:** Tạo `JournalEntry` bằng schema và serializer hiện hành; xây dựng stage payload đầy đủ, chiếu chính xác từ pending/commit/rollback evidence đã xác thực, và đặt FINISH fields đúng trong `action_params`. Bổ sung ví dụ byte-for-byte cho các record mà Coordinator append, rồi kiểm tra chúng qua cùng validator/FSA được dùng khi replay.

### R21-02 — Receipt update bỏ qua state machine và có thể ghi trạng thái không tồn tại

Pseudocode gán `final_state="FAILED"` cho nhánh `ACTION_FAILED` rồi ghi JSON `{execution_id, state, updated_at}` trực tiếp. `FAILED` không thuộc `VALID_RECEIPT_STATES`; trạng thái hợp lệ gồm `INITIALIZED`, `EXECUTING`, `COMPLETED`, `ROLLED_BACK`, `ABORTED` (`execution_receipt_contracts.py:44-50`). Cách ghi trực tiếp cũng bỏ qua `ExecutionReceiptService.transition_state()` và thay toàn bộ receipt hiện hữu bằng một object ba trường (`07B5...design.md` khoảng `1980-1994`, `2104-2117`, `2131-2138`). Nó làm mất các trường receipt và idempotency/target metadata; `O_TRUNC` có thể để lại receipt rỗng hoặc cụt khi crash. `os.write()` cũng không kiểm tra short write, và không có fsync thư mục sau thao tác thay thế vì hiện không dùng atomic replace.

Đối chiếu nguồn: `src/core/contracts/execution_receipt_contracts.py:44-50, 258-360`; `src/services/execution_receipt_service.py:406-455`.

**Yêu cầu:** Ánh xạ kết quả WAL sang state hợp lệ theo state machine, transition nguyên vẹn `ExecutionReceipt` qua service sở hữu, và xác định crash-consistent thứ tự commit receipt/idempotency/target state. Không dùng `O_TRUNC` để thay receipt; yêu cầu atomic replace, file fsync và parent-directory fsync, xử lý short writes, giữ target lock khi bất kỳ bước nào chưa bền vững.

### R21-03 — Restart repair không xác minh WAL hiện tại trước khi append hoặc nhả khóa

`execute_restart_repair()` xác minh MAC và identity của snapshot, nhưng nhánh `ROLLED_BACK`/`FAILED` chỉ mở WAL, đọc theo `st_size + 1024`, tính `next_seq`, rồi append FINISH; nhánh `FINISHED_*` ghi receipt và nhả khóa mà không đọc WAL. Không đối chiếu `dev/ino/size/SHA-256` với snapshot, không xác thực lại framing/EOF/FSA/stage payload, không kiểm tra trạng thái receipt/consumed ledger trước khi tuyên bố repair thành công. Một WAL bị thay đổi, torn hoặc có stage sau FINISH giữa lúc snapshot được tạo và lúc repair chạy có thể dẫn đến append sai hoặc mở khóa dựa trên stale state. Trường hợp `FINISHED_CANCELLED_CLEAN` trả `journal_state_updated=True` nhưng nhánh không cập nhật Journal.

Vị trí: pseudocode R21 khoảng `2031-2057`, `2059-2125`, `2128-2161`.

**Yêu cầu:** Trước mọi thao tác repair, Coordinator phải đọc lại WAL qua descriptor an toàn, xác minh fingerprint và chạy toàn bộ strict parser/FSA tới EOF dưới cùng OS target lock. Sau đó đối chiếu receipt, idempotency/target ledger và proof-consumption state; chỉ append đúng phần còn thiếu theo trạng thái đã xác minh. `journal_state_updated` phải phản ánh thao tác thực tế, và mọi mismatch/IO ambiguity phải giữ khóa.

### R21-04 — Freshness verification dùng đồng hồ giả lập, không phải thời gian hiện hành

Nhánh LiveResolve proof truyền `proof.observation_completed_monotonic + 0.1` và hằng số epoch `1700000000.0` cho verifier (khoảng dòng `1868-1872`). Giá trị monotonic được suy ra từ chính proof, còn epoch cố định không thể kiểm tra độ tươi tại thời điểm giao dịch. Một proof cũ có thể được xác minh theo một “now” do dữ liệu proof quyết định thay vì clock thuộc quyền Coordinator.

**Yêu cầu:** Coordinator sở hữu clock providers; chụp monotonic và UTC hiện tại đúng một lần tại boundary xác minh, kiểm tra request-dispatch ≤ observation-completed ≤ now, TTL, UTC freshness và session binding. Không nhận hoặc tổng hợp `now` từ proof/caller; clock lỗi hoặc timestamp không nhất quán phải fail-closed, giữ khóa.

### R21-05 — OS lock và WAL path không được neo an toàn; timeline UID có thể thành đường dẫn

Lock path được tạo trực tiếp bằng `os.path.join(self.locks_dir, f"{target_timeline_uid}.lock")`, rồi mở với `O_RDWR | O_CREAT` (khoảng `1551-1558`). UID chứa `/`, `..`, hoặc ký tự đặc biệt có thể thoát thư mục lock; lockfile symlink cũng không bị chặn bằng `O_NOFOLLOW`/`fstat`. Các lần mở WAL để đọc/append và receipt cũng dùng đường dẫn ghép chuỗi không neo descriptor (`1850-1854`, `1965-1967`, `1981-1989`, `2055-2067`, `2091-2093`). Điều này làm suy yếu bảo đảm OS lock và cho phép TOCTOU/path substitution đúng tại ranh giới phục hồi.

**Yêu cầu:** Chuyển UID/execution ID thành tên tệp an toàn bằng digest ổn định; mở thư mục gốc một lần bằng directory descriptor đã xác minh, dùng `openat` tương đối với `O_NOFOLLOW | O_CLOEXEC` (và `O_NONBLOCK` khi phù hợp), xác minh `S_ISREG`/`S_ISDIR`, dev/ino và quyền sở hữu. Giữ directory descriptors xuyên giao dịch; mọi symlink, inode drift hoặc loại file bất thường phải fail-closed.

### R21-06 — FSA cho phép bắt đầu rollback khi chưa có ACTION_COMMITTED

FSA chấp nhận `ROLLBACK_PENDING` từ trạng thái `PENDING` (R21 khoảng `1045-1047`, pseudocode khoảng `1723`). Tuy nhiên `PENDING` chỉ chứng minh ACTION_PENDING đã durable; chưa chứng minh đã có `ACTION_COMMITTED` hay `committed_resolve_item_id`. Cho phép bắt đầu rollback từ trạng thái này có thể tạo rollback không gắn với một item đã được ghi nhận, hoặc biến trạng thái dispatch chưa biết thành kết quả rollback có vẻ hoàn tất.

**Yêu cầu:** Chỉ cho phép `ROLLBACK_PENDING` sau `COMMITTED` với `resolve_item_id` đã được xác thực và ràng buộc chính xác với action. Nếu xử lý tình huống crash sau dispatch nhưng trước commit, giữ `IN_DOUBT` cho đến khi có observation/reconciliation độc lập; không cho WAL tự mở nhánh rollback như thể commit đã được chứng minh.

### R21-07 — Snapshot MAC dùng chuỗi phân cách không có framing

`_compute_snapshot_mac()` nối 13 giá trị bằng ký tự `|` (khoảng `1595-1601`). Nếu các trường chuỗi không cấm hoặc escape `|`, hai bộ trường khác nhau có thể tạo cùng payload trước HMAC. HMAC không sửa được nhập nhằng của serialization.

**Yêu cầu:** MAC trên canonical JSON bytes với schema/version cố định, hoặc encoding length-prefixed; kiểm tra kiểu và UTF-8 trước serialize. Có fixture chứng minh các chuỗi chứa delimiter không thể tạo cùng encoding cho hai snapshot khác nhau.

## Những điểm R21 đã cải thiện

- Bỏ các writer callback và khai báo rõ Coordinator sở hữu transaction, WAL append, Journal update và lock lifetime.
- Thêm OS `flock(LOCK_EX | LOCK_NB)` cùng giữ descriptor trong Coordinator.
- Thay plain token snapshot bằng MAC và nêu `compare_digest`.
- Mô tả allowlist FSA và nhánh restart repair riêng.
- Giữ `ready_to_execute=False`, mock-only, không có Resolve IPC/UI wiring trong phạm vi thiết kế.

## Quyết định

R21 **chưa được nghiệm thu**. Các điểm R21-01 đến R21-06 chặn tính nhất quán và an toàn của WAL/Receipt/restart; R21-07 cần được đóng trước khi snapshot được coi là capability không thể giả mạo. Giữ `ready_to_execute=False`; không mở G07-07B6-I hoặc bất kỳ live Resolve work nào. Workorder tiếp theo nên là **G07-07B5-D-R22**, DESIGN ONLY, đóng các findings trên bằng đối chiếu schema nguồn, crash matrix và ví dụ byte-level đầy đủ.

Đây là static review; không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.
