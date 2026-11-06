# G07-07B5-D-R19 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6, các pseudocode và quan hệ với hợp đồng kiến trúc đã nêu. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.

## Kiểm tra tính toàn vẹn

Các hash R19 được bàn giao khớp với tệp trên workspace:

- Task: `.bridge/task.md` — `b6d30fc4e5b7364693a06c692f9b29a19b95c216b66e1243cf7a3d38af69fd9c`
- Review R18: `docs/architecture/07B5-D-R18-code-review.md` — `6d3a72d8800a72b35cbebcdbaa568181c2039a03899036e9b9a75a67df836940`
- Thiết kế: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `992493cf4e679397d4735156f175e5c68583c8399b2f1df11c2dc261ab7ac70f`
- Báo cáo: `.bridge/report.md` — `5dca705fcf86e46c168cc97ba40e9464c1465da2246a6cc5c0f9f65236b885fa`

R19 đã đóng đúng các nhóm R18 về scan giới hạn, WAL line framing, scalar validation, đồng hồ monotonic theo cặp, bigint bounds và primitive `consume_once`. Ba vấn đề dưới đây còn chặn nghiệm thu vì API hoàn tất vẫn cho caller thay thế thẩm quyền tin cậy và có thể báo terminal trước khi các artifact terminal thực sự bền vững.

## Findings chặn nghiệm thu

### R19-01 — API giao dịch vẫn nhận trust anchors và danh tính từ caller

`execute_pre_terminal_completion_transaction()` nhận `secret_key`, `trusted_key_id`, `live_authority`, `live_request`, `live_result`, `coordinator_monotonic_now`, `current_time_epoch`, cùng các ID/UID/frame dưới dạng tham số (Section 2.6.12, khoảng dòng 2153–2175). `LiveObservationAuthority` có constructor công khai nhận secret và session ID (khoảng dòng 1795–1800). Do đó caller có thể tự tạo authority với khóa/session do mình chọn rồi truyền authority, request và result khớp nhau vào giao dịch. Nhánh receipt cũng xác minh bằng `secret_key` và `trusted_key_id` do caller cung cấp. Việc bỏ secret khỏi `validate_wal_to_eof()` không khép ranh giới nếu helper tiếp theo lại nhận các trust anchors này.

Hơn nữa, `wal_automaton_state`, `wal_last_stage` và các identity/frame inputs không phải một kết quả parse bất biến có nguồn gốc; hàm không đối chiếu `wal_last_stage` với state hoặc với WAL đã đọc. Bởi vậy caller có thể cung cấp trạng thái “PENDING” và context riêng mà không chứng minh chúng đến từ cùng WAL/auth đang được giữ khóa.

**Yêu cầu:** Để `RecoveryCoordinator` sở hữu duy nhất authority, trust providers, clock và target lock. Không nhận secret, expected key ID, authority instance, trạng thái WAL rời rạc hay identity kỳ vọng do caller quyết định. Dùng một coordinator-owned transaction method nhận `TrialExecutionAuthorization` và một typed immutable parse result có nguồn gốc từ lần đọc WAL hiện hành; authority và receipt key chỉ được resolve từ provider tin cậy nội bộ. Thêm negative probes cho caller tự tạo `LiveObservationAuthority`, tự chọn receipt key, sửa state/last_stage/UID và không thể đạt terminal success.

### R19-02 — Transaction helper có thể trả thành công mà không terminalize Journal/WAL hoặc nhả khóa

Tài liệu nói bước 3 phải ghi WAL **và** `ExecutionReceipt`/Journal; bước 4 chỉ nhả lock sau khi cả hai bền vững. Nhưng chữ ký helper chỉ nhận `wal_appender_callback` (khoảng dòng 2175), rồi gọi callback với `(terminal_stage, outcome_status)` (khoảng dòng 2253–2259). Callback không bị ràng buộc để ghi hoặc fsync, không cập nhật Journal/receipt, và không ghi `FINISH`. Caller có thể đưa no-op callback; helper vẫn trả `(True, "TERMINALIZED_...")`. Nó cũng không sở hữu lock và không thực hiện bước release đã nêu.

Điều này còn xung đột với WAL automaton: `ACTION_FAILED` chuyển sang `FAILED`, còn `ROLLBACK_DONE` chuyển sang `ROLLED_BACK`; chỉ `FINISH` mới tạo `FINISHED_FAILED`/`FINISHED_ROLLED_BACK`. Helper hiện chỉ append stage đầu tiên rồi báo hoàn tất. Crash hoặc lỗi ghi sau khi consume proof có thể để WAL/Journal ở các trạng thái khác nhau, trong khi API không chỉ rõ giữ lock và restart repair tương ứng.

**Yêu cầu:** Thay callback tùy ý bằng một transaction do coordinator sở hữu, ghi và fsync các stage cần thiết theo đúng grammar (bao gồm `FINISH` khi yêu cầu), cập nhật receipt/Journal và chỉ phát terminal success sau khi đọc lại/xác nhận durable states. Giao dịch phải giữ target lock khi bất kỳ write/fsync/transition nào thất bại hoặc không rõ kết quả. Định nghĩa restart repair cho từng ranh giới giữa ledger consume, WAL stage/FINISH, Journal transition và lock release; thêm positive proof no-op callback không thể giả thành công cùng fault probes tại mỗi ranh giới.

### R19-03 — Giao dịch chưa chứng minh binding WAL parse, proof và transaction là cùng một snapshot

`parse_and_validate_wal_to_eof()` trả tuple `(automaton_state, committed_resolve_item_id, last_stage)` (khoảng dòng 1942–1955), trong khi transaction nhận từng giá trị rời và không nhận snapshot digest, file identity/version, `execution_id` từ parse result hay descriptor đang giữ. Giữa parse và completion, WAL có thể đổi hoặc caller có thể ghép kết quả parse của WAL này với proof/context của phiên khác. Việc neo descriptor trong parser bảo vệ lần đọc, nhưng descriptor không được giữ hoặc truyền sang transaction để ràng buộc bước ghi.

**Yêu cầu:** Trả typed parse snapshot bất biến gắn `execution_id`, target/auth binding, WAL descriptor identity và digest/EOF position; transaction phải tiêu thụ đúng snapshot đó dưới cùng target lock và kiểm tra WAL chưa đổi trước khi append. Không cho caller dựng snapshot/state thủ công. Nếu giao thức dùng lock để ngăn writer, nêu rõ ai giữ lock xuyên parse–verify–consume–terminalize và chứng minh mọi error path giữ lock.

## Các điểm R19 đã sửa đúng

- `_strict_wal_number` và `_strict_wal_int` chặn bigint ngoài giới hạn và đổi overflow thành lỗi có kiểm soát; live result dùng strict gates cho scalar fields.
- `LiveObservationAuthority` giữ secret/session nội bộ; freshness kiểm tra monotonic ordering, scan duration và tổng tuổi theo coordinator clock.
- Scan track có giới hạn 5.000 item và timeout 15 giây; lỗi item bất thường không phát attestation.
- WAL framing bỏ đúng newline cuối và từ chối dòng rỗng/khoảng trắng.
- Ledger có `consume_once` cùng dual uniqueness và chính sách lỗi persistence giữ IN_DOUBT.
- Ranh giới DESIGN ONLY, `ready_to_execute=False`, mock-only và G07/M1/M2/M3 OPEN vẫn được giữ trong hồ sơ.

## Quyết định

R19 **chưa được nghiệm thu**. Giữ `ready_to_execute=False`; không bắt đầu G07-07B6-I hoặc bất kỳ live Resolve work nào. Lát tiếp theo nên là **G07-07B5-D-R20 — Coordinator-Owned Completion Transaction & Durable WAL/Journal Commit**, DESIGN ONLY, xử lý R19-01..R19-03 và bổ sung crash matrix receipt/ledger/WAL/Journal/lock đầy đủ.

Đây là review tĩnh; không chạy test hoặc runtime và không cấp quyền gọi Resolve thực.
