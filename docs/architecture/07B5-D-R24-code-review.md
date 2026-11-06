# G07-07B5-D-R24 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6 và pseudocode R24; đối chiếu hợp đồng Journal/Receipt hiện có. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.

## Kiểm tra tính toàn vẹn hồ sơ

Các hash bàn giao khớp với tệp hiện tại:

- Task R24: `.bridge/task.md` — `28844f67e166113612daa37df9082b0f181676ec70993766e9d07f8f1dd6d63e`
- Review R23: `docs/architecture/07B5-D-R23-code-review.md` — `cb0aaa291593c1958fb638a961db1a81d48967da33279429e6f9ceab534d705d`
- Thiết kế R24: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `a7c6a6d9239381de39021f7eb4459dba5e9f2bb4489d339709134bbc1498059b`
- Báo cáo R24: `.bridge/report.md` — `bcd8a78db02beb45d9f8f38a76db7ce3bcbf8deaa4a778ec88c22bf7152d1efb`

R24 đã cải thiện đáng kể việc giữ payload action bất biến giữa pending/failure, tách descriptor `receipts/`, và thêm helper ghi đủ byte. Các điểm dưới đây vẫn chặn nghiệm thu vì ảnh hưởng tới giữ khóa, tính hợp lệ của WAL, hoặc fail-closed proof handling.

## Findings chặn nghiệm thu

### R24-01 — Lỗi ghi metadata làm rò descriptor và nhả OS lock ngoài ý muốn

`acquire_target_lock()` chỉ đưa `fd` vào `_lock_fds` sau `ftruncate`, `_write_all`, và `fsync` (`07B5-isolated-resolve-acceptance-gate-design.md:1528-1566`). Nếu bất kỳ thao tác nào sau `flock(LOCK_EX)` thất bại, không có `except/finally` đóng descriptor hoặc lưu owner; khóa bị giữ bởi descriptor mồ côi trong tiến trình nhưng coordinator không thể nhận diện/giải phóng nó.

Ngược lại, `release_target_lock()` xóa entry khỏi `_lock_fds` trước khi ghi/fsync metadata và luôn `os.close(fd)` trong `finally` (`:1571-1589`). Nếu metadata persistence lỗi, descriptor vẫn bị đóng và kernel tự nhả `flock`; hàm phát lỗi nhưng target không còn bị khóa. Điều này trái với invariant rằng lỗi persistence phải giữ target `IN_DOUBT` và khóa.

**Yêu cầu:** Ghi rõ ownership state machine cho descriptor. Sau khi lock thành công, mọi lỗi metadata phải giữ descriptor được đăng ký trong owner hoặc chuyển sang trạng thái giữ khóa có thể phục hồi; không pop/close cho tới khi thao tác release được xác nhận. Bổ sung trình tự lỗi cho flock, write, fsync, unlock và close; chỉ báo `lock_released=True` khi đã xác nhận thực sự nhả khóa.

### R24-02 — FINISH repair phát ra note không thuộc allowlist của consumer nguồn

Restart repair ghi `FINISH.note="HALTED_VIA_RESTART_REPAIR"` (`:2435-2452`), còn giao dịch hoàn tất ghi `HALTED_AFTER_ROLLBACK` hoặc `HALTED_ON_ACTION_FAILURE` (`:2251-2314`). Consumer `ResolveExecutionEngine._validate_journal_wal_state_machine()` chỉ cho phép các terminal notes `COMPLETED_ALL_ACTIONS`, `HALTED_ON_ACTION_FAILURE`, `CANCELLED_AT_PRE_DISPATCH_BOUNDARY`, `HALTED_OCCUPANCY_SANITY_CHECK_FAILED`, và in-doubt note `HALTED_IN_DOUBT_OBSERVER_TIMEOUT`; các note còn lại bị từ chối (`src/services/resolve_execution_engine.py:1153-1162, 1200-1213`). Hơn nữa, nhánh engine này cố ý từ chối terminal FINISH như trạng thái xung đột với replay in-doubt (`:1211-1213`).

Vì thế pseudocode vừa tạo các nhãn repair không được consumer nhận diện, vừa chưa xác định đường đọc chuẩn tắc cho terminal trial WAL. `validate_raw_journal_record()` chỉ bảo đảm schema/allowlist cấu trúc, không làm cho note mới trở thành note hợp lệ về ngữ nghĩa.

**Yêu cầu:** Chốt một contract terminal dùng chung với consumer, gồm allowlist note, ý nghĩa `final_receipt_state`, và quy tắc phân biệt terminal replay với in-doubt reconciliation. Nếu trial WAL có grammar riêng, phải định nghĩa consumer/dispatcher riêng và chứng minh không đưa terminal WAL vào parser in-doubt hiện có. Cập nhật mọi producer, parser, restart repair và fixture theo cùng bảng trạng thái.

### R24-03 — “Full schema proof validation” chưa fail-closed trên kiểu proof và các trường ký

Pseudocode gọi `proof.keys()` trước khi xác nhận proof là một `dict`/DTO được sở hữu (`:2118-2126`). Sau đó chỉ type-gate một số trường (`schema_version`, chỉ mục, count, hai boolean); các trường như `receipt_hmac_sha256`, `key_id`, timestamp, nonce, IDs, digest và frame bounds không được kiểm tra kiểu/định dạng đầy đủ trước khi dùng (`:2165-2194`). Ví dụ `receipt_hmac_sha256=None` gây lỗi tại `.lower()`, `key_id` không hashable có thể gây `TypeError` ở `_keyring.get()`, và object không có `.keys()` thoát khỏi đường trả kết quả `IN_DOUBT`. Equality số học cũng có thể cho `False == 0` hoặc float nguyên bằng int nếu trường bound chưa được type-gate.

Ngoài ra, proof vẫn là object caller-owned được đọc nhiều lần; chưa có bước snapshot/clone bất biến trước schema check, HMAC verify và `consume_once`, nên mutation đồng thời có thể làm các bước dùng các giá trị khác nhau.

**Yêu cầu:** Yêu cầu exact built-in `dict` hoặc DTO đã chuẩn hóa; clone/đóng băng một lần trước validation. Áp dụng allowlist và primitive type/format/range gates cho đủ 22 trường trước mọi lookup hoặc `.lower()`. Chuẩn hóa lỗi dữ liệu thành kết quả fail-closed có kiểm soát, không để `TypeError`, `AttributeError`, `UnicodeError` hay `OverflowError` thoát ra khỏi coordinator.

### R24-04 — WAL replay không đối chiếu action payload và terminal fields với nguồn authority

`_read_and_parse_wal()` lưu `last_pending_rec`, nhưng khi gặp `ACTION_FAILED` chỉ chuyển state sang `FAILED` (`:1748-1751, 1781-1789`); không so sánh toàn bộ `action_params` của pending và failed, cũng không ràng buộc payload với action đã được authorization ký. Nhánh `COMMITTED`/`ROLLBACK_DONE` chủ yếu kiểm tra `resolve_item_id` (`:1792-1810`). `FINISH` trong các state này chỉ đổi state (`:1792-1822`), không thẩm định note, `final_receipt_state`, allowlist action params, hay nhất quán giữa receipt và WAL.

Do đó, exact-copy khi *ghi* `ACTION_FAILED` không bảo vệ recovery khi WAL bị sửa hoặc bị dựng lại: parser có thể chấp nhận payload khác, rồi restart repair terminalize receipt dựa trên state name. Điều này đi ngược yêu cầu kiểm chứng replay equality và repair chỉ từ lịch sử đã thẩm định đầy đủ.

**Yêu cầu:** Trong parser, enforce full key/value/canonical-byte equality giữa pending với failed, và giữa committed với rollback outcome; xác minh mọi action field với authorization/plan digest; kiểm tra action index/cardinality; xác minh toàn bộ FINISH envelope/outcome fields theo state. Chỉ trả snapshot sau khi toàn bộ FSA và projection checks hợp lệ tới EOF.

### R24-05 — Restart repair xác thực snapshot MAC nhưng bỏ qua binding snapshot với WAL vừa đọc

`execute_restart_repair()` xác thực MAC và các ID của snapshot (`:2383-2398`), rồi đọc lại WAL (`:2400-2417`) nhưng không so sánh `st_dev`, `st_ino`, `st_size`, `wal_hash`, record count hoặc state vừa đọc với các trường tương ứng trong snapshot. Nhánh transaction tiền terminal có các so sánh đó (`:2081-2091`), nhưng restart path thì không.

Một snapshot hợp lệ nhưng cũ vì vậy vẫn có thể được dùng làm đầu vào cho repair của WAL đã thay đổi sau snapshot. Việc đọc lại WAL tránh dùng dữ liệu cũ trực tiếp nhưng không chứng minh snapshot capability đang ủy quyền đúng trạng thái vừa được sửa.

**Yêu cầu:** Dưới target lock, đối chiếu fingerprint đầy đủ và state/record count của lần đọc mới với snapshot trước khi append FINISH hoặc cập nhật receipt. Nếu restart repair được phép dùng snapshot mới tạo nội bộ thay vì caller snapshot, thể hiện rõ API đó và không nhận snapshot cũ từ caller.

### R24-06 — UUID validator âm thầm chấp nhận whitespace quanh execution ID

`_validate_execution_id()` dùng `execution_id.strip()` rồi xác thực và trả về giá trị đã chuẩn hóa (`:1010-1018`). Vì vậy chuỗi UUID có leading/trailing whitespace được chấp nhận thay vì bị từ chối. Path được tạo từ ID đã strip/lower, trong khi các binding WAL so với `auth.execution_id` gốc; dữ liệu định danh vì thế có thể có hai biểu diễn khác nhau giữa tên file và nội dung.

**Yêu cầu:** Từ chối nếu `cleaned != execution_id`; không trim identity tại ranh giới bảo mật. Quy định rõ có chấp nhận chữ hoa UUID hay không và dùng một biểu diễn canonical nhất quán cho filename, authorization, receipt và mọi bản ghi.

### R24-07 — WAL append không dùng serializer Journal nguồn như thiết kế cam kết

Thiết kế tuyên bố mọi Journal line được tuần tự hóa qua `journal_entry_to_json_line(entry)` (`:897`), nhưng các đường append mới dùng `json.dumps(rec, sort_keys=True) + "\n"` (`:2319-2320, 2452`). Đây là byte format khác: thứ tự khóa bị sort, mặc định `ensure_ascii=True`, và separators/indentation khác serializer Journal hiện hành (`src/core/contracts/execution_receipt_contracts.py:731+`). `validate_raw_journal_record()` xác nhận cấu trúc sau parse, không chứng minh byte serialization tuân thủ hợp đồng writer.

**Yêu cầu:** Tạo `JournalEntry` theo schema, serialize qua `journal_entry_to_json_line()` cho mọi outcome và restart append; hoặc ban hành migration/versioned WAL byte contract và cập nhật toàn bộ reader/writer. Fixture phải so sánh bytes thực tế với serializer được chọn.

## Những điểm R24 đã cải thiện

- Payload `ACTION_FAILED.action_params` được sao chép nguyên từ pending; proof metadata được chuyển khỏi payload so sánh.
- Receipt được mở và thay thế qua descriptor riêng của `receipts/`, có bounded read, atomic replace, fsync thư mục và read-back.
- Đã có helper `_write_all()` xử lý `EINTR`, short write và zero write; các append mới gọi fsync theo từng record.
- Có thêm kiểm tra schema key-set và nhiều ràng buộc danh tính cho readback receipt.

## Quyết định và bước tiếp theo

R24 **chưa được nghiệm thu**. Findings R24-01 đến R24-07 cần được xử lý trước khi mở bước live/Resolve tiếp theo. Giữ `ready_to_execute=False`; không mở G07-07B6-I hoặc G07-07B7-LIVE từ kết quả review này.

Workorder đề xuất: **G07-07B5-D-R25**, DESIGN ONLY, sửa contract/flow cho các finding trên và bổ sung fixtures source-aligned về lock failure, proof malformed/mutation, terminal note, replay payload mismatch, snapshot drift, UUID raw identity và exact Journal bytes. Không sửa `src/` hoặc `tests/`, không chạy pytest/app/Resolve/IPC/FusionScript/AI/network cho tới khi có workorder triển khai riêng.
