# G07-07B5-D-R20 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6 và pseudocode. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.

## Kiểm tra tính toàn vẹn hồ sơ

Các hash bàn giao khớp với tệp trên workspace:

- Task R20: `.bridge/task.md` — `7b33d5227fc39ad656a94f72c78ce1ffad44c14b467e4f03b2cf069ec80037e6`
- Review R19: `docs/architecture/07B5-D-R19-code-review.md` — `e6648132d163a9ca3b0ed532213c7c228a7c01a0b98843d6f1e0a51c5d9e8204`
- Thiết kế R20: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `5411ae1559889e92a0f8f22e433b6a752392c2e8f0dce31805d2796e1509b4c6`
- Báo cáo R20: `.bridge/report.md` — `3ca67956b90f341b362c97880091197e27aa2b9041ce1a77205ca310b1d686ae`

R20 cải thiện quyền sở hữu keyring/authority ở mức coordinator, thêm snapshot type, mô tả ghi outcome + FINISH + Journal, và mở rộng crash matrix. Tuy nhiên, pseudocode thực thi còn các đường cho caller giả lập commit, lock không có tính loại trừ liên tiến trình, snapshot token có thể bị sao chép, và parser chưa thực thi grammar fail-closed như mô tả.

## Findings chặn nghiệm thu

### R20-01 — Completion API vẫn nhận writer callbacks và “current WAL evidence” từ caller

`RecoveryCoordinator.execute_pre_terminal_completion_transaction()` nhận `current_wal_bytes_on_disk`, `current_wal_dev`, `current_wal_ino`, `wal_durable_appender` và `journal_durable_updater` từ caller (khoảng dòng 2111–2124). Phần “revalidation” chỉ hash các bytes và so sánh các số caller cung cấp với snapshot (khoảng dòng 2165–2175); hàm không tự mở WAL qua anchored descriptor để đọc lại. Caller vì vậy có thể đưa bản bytes/dev/ino đã lưu từ snapshot, đồng thời truyền `lambda *_: None` cho cả hai callback. Sau `consume_once`, coordinator vẫn đánh dấu giao dịch thành công, gọi `release_target_lock()` và trả `wal_finish_appended=True`, `journal_state_updated=True` (khoảng dòng 2272–2295), dù không có artifact nào được ghi.

**Yêu cầu:** Coordinator phải tự đọc lại WAL trên đĩa bằng descriptor neo, tự append đúng `JournalEntry` qua `ExecutionJournalService`/writer được sở hữu, tự transition `ExecutionReceipt`, và xác nhận kết quả bền vững trước khi trả thành công hoặc nhả khóa. Không nhận callback ghi, bytes/stat hiện hành hay kết quả ghi từ caller. Ghi negative case với no-op/throwing callback không thể được dùng để đạt success; tốt hơn là bỏ callback khỏi API.

### R20-02 — `TargetLock` trong coordinator chỉ là cờ RAM, không phải exclusion lock

`acquire_target_lock()` chỉ kiểm tra/gán `_active_lock_target` và tạo token trong object instance (khoảng dòng 1955–1959); `release_target_lock()` chỉ đặt hai thuộc tính về `None` (dòng 1961–1963). Không có `fcntl.flock`, descriptor lock hay cơ chế liên tiến trình trong hai phương thức này, dù toàn bộ giao thức dựa vào `TargetLock(LOCK_EX)` để ngăn execution/recovery khác chạm cùng timeline.

Hai `RecoveryCoordinator` hoặc hai process độc lập do đó đều có thể thấy trạng thái cục bộ “lock free” và cùng thực hiện parse/consume/terminalize. `consume_once` chống dùng lại proof nhưng không thay thế exclusion trên target và không ngăn hai coordinator thao tác đồng thời lên Resolve hoặc Journal.

**Yêu cầu:** Xác định và dùng target-scoped OS advisory lock bền vững với thứ tự khóa đã được chốt; giữ descriptor/ownership xuyên parse → verify → consume → commit → recovery. Fail-closed nếu không chiếm được lock; mọi release phải đóng đúng descriptor sở hữu. Thêm cross-process contention và restart lock probes.

### R20-03 — Snapshot token bị lộ trong DTO và có thể sao chép để sửa các trường được tin

`WalParseSnapshot` chứa `_coordinator_secret_token` trực tiếp như trường dữ liệu (khoảng dòng 1331–1347), và `parse_wal_to_snapshot()` trả object đó cho caller. Transaction chỉ so token này với `_active_lock_session_token` (dòng 2140–2145). Caller đang giữ snapshot có thể đọc token rồi khởi tạo hoặc `dataclasses.replace()` một snapshot khác với cùng token nhưng sửa `automaton_state`, `last_stage`, `committed_resolve_item_id` hoặc fingerprint. `frozen=True` ngăn gán thuộc tính sau tạo, không ngăn tạo bản sao mới. Một token bí mật không còn bí mật nếu được nhúng nguyên dạng vào DTO gửi ra ngoài.

**Yêu cầu:** Không trả capability secret trong snapshot. Dùng snapshot handle opaque được coordinator giữ trong registry nội bộ, hoặc MAC bao phủ canonical serialization của toàn bộ trường snapshot với kiểm tra constant-time; xác thực cả quan hệ state/last_stage và authorization binding. Snapshot clone hoặc mutation phải bị từ chối, kể cả khi giữ nguyên các trường nhận dạng.

### R20-04 — WAL parser vẫn âm thầm bỏ qua stage sai thứ tự và không kiểm semantic cancellation

Trong vòng lặp state machine (khoảng dòng 2059–2089), các nhánh `STARTED`, `PENDING`, `COMMITTED`, `ROLLBACK_IN_PROGRESS`, `ROLLED_BACK` và `FAILED` không có nhánh `else` từ chối stage không hợp lệ. Một stage hợp lệ theo schema nhưng sai vị trí có thể bị bỏ qua, sau đó parser vẫn đi tiếp và trả snapshot cho state cuối. Ví dụ `START -> RECOVERY_NOTE -> ACTION_PENDING` không bị từ chối ở `STARTED`; parser có thể kết thúc với `PENDING`. Ngoài ra `STARTED -> FINISH` luôn đặt `FINISHED_CANCELLED_CLEAN` mà không kiểm tra semantic cancellation tuple, trạng thái ABORTED, action type, committed count và allowlist đã yêu cầu trước đó.

**Yêu cầu:** FSA phải exhaustive: mỗi state chỉ chấp nhận đúng các transition đã liệt kê, tất cả stage khác lập tức fail-closed. `FINISH` trước pending chỉ hợp lệ khi toàn bộ cancellation tuple và envelope khớp chính xác. Thêm fixtures stage sai thứ tự ở mỗi state, recovery note trước FINISH, FINISH giả và stage hợp lệ nhưng không được phép trong context.

### R20-05 — Crash repair sau outcome/FINISH hiện không thể chạy qua transaction method

Parser có thể trả `ROLLED_BACK` sau `ROLLBACK_DONE`, `FAILED` sau `ACTION_FAILED`, và `FINISHED_*` sau FINISH (khoảng dòng 2079–2089). Nhưng `execute_pre_terminal_completion_transaction()` chỉ nhận `ROLLBACK_IN_PROGRESS` hoặc `PENDING` (dòng 2158–2163). Vì vậy các crash case mà R20 nêu sau khi outcome stage đã fsync nhưng trước FINISH, hoặc sau FINISH nhưng trước Journal update, không thể được sửa bởi chính transaction method: chúng bị trả `ERR_NOT_IN_PRE_TERMINAL_STATE`. Không thấy một repair method riêng trong pseudocode xử lý các trạng thái này.

**Yêu cầu:** Định nghĩa riêng, coordinator-owned restart repair cho `ROLLED_BACK`, `FAILED`, `FINISHED_ROLLED_BACK` và `FINISHED_FAILED`. Repair phải đối chiếu WAL với receipt/Journal và proof-consumption ledger, chỉ append phần còn thiếu theo quy tắc idempotent; không tiêu thụ lại proof và không dispatch Resolve. Bổ sung crash matrix cùng trạng thái cụ thể của WAL, Journal, consumed ledger và lock tại từng boundary.

## Những điểm R20 đã cải thiện

- Keyring và LiveObservationAuthority được mô tả là coordinator-owned; receipt key được tra theo `key_id` nội bộ.
- Snapshot có identity fields, file fingerprint và token; completion kiểm tra identity, automaton state và digest trước khi tiếp tục.
- Thứ tự intent `consume_once → outcome stage → FINISH → Journal → lock release` đã được nêu rõ; lỗi write được dự kiến giữ IN_DOUBT.
- R18 constraints về strict scalar, freshness monotonic, bounded scan, strict line framing và UTC được giữ lại.
- DESIGN ONLY, `ready_to_execute=False`, mock-only và G07/M1/M2/M3 OPEN vẫn được ghi nhận.

## Quyết định

R20 **chưa được nghiệm thu**. Giữ `ready_to_execute=False`; không bắt đầu G07-07B6-I hoặc bất kỳ live Resolve work nào. Bước tiếp theo nên là **G07-07B5-D-R21 — OS Target Lock, Non-forgeable Snapshot Capability & Exhaustive WAL Repair**, DESIGN ONLY, xử lý R20-01..R20-05 và mô tả repair có thể thực thi cho từng crash boundary.

Đây là review tĩnh; không chạy kiểm thử hoặc runtime và không cấp quyền gọi Resolve thực.
