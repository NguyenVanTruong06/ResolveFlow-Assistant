# G07-07B5-D-R29 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6 R29, đối chiếu workorder, report, review R28 và authorization/persistence contracts đã mô tả trong tài liệu. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network; không sửa `src/` hay `tests/`.

## Kiểm tra tính toàn vẹn hồ sơ

Các SHA-256 được bàn giao khớp tệp hiện tại:

- Task R29: `.bridge/task.md` — `ef2c72a39722c5cff6bd3f8077a9206c982061a6f3527115cc170937590799b7`
- Review R28: `docs/architecture/07B5-D-R28-code-review.md` — `6fa350b0542902be2dfaae4fa3527df70e9a659fbf016549041273f6c74ebf36`
- Thiết kế R29: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `45542ef0b80996edb3ebf13d7e38c31a640579da91db249d068d799802cbecd4`
- Báo cáo R29: `.bridge/report.md` — `7093bf318c07c85735b400b7137b9a7232b2169a3a6576f26af6b81d8543ee8b`
- Validator được dẫn chiếu: `scratch/test_r29_validator.py` — `1b2eeb8e8c610acf86c2dd92d4fd0360a6f82ec46f59078f142bf3054f93c523`

R29 nêu đúng ba hướng sửa của R28, nhưng pseudocode trong Section 2.6 chưa chứng minh các thuộc tính bảo mật và độ bền mà phần mô tả tuyên bố.

## Findings chặn nghiệm thu

### R29-01 — Digest của action được cấp quyền chưa định nghĩa cùng payload với `ACTION_PENDING.action_params`

Ở mục 2.3, `action_payload_digest_sha256` được mô tả là digest của `PlannedTimelineAction`, và TrialAdmissionGuard tính lại digest từ đối tượng đó (các dòng 544, 622). Trong khi đó, R29-01 yêu cầu tính `SHA256(canonical_serialize(ACTION_PENDING.action_params))` rồi so sánh trực tiếp với cùng digest (các dòng 920–921, 2178–2183). `ACTION_PENDING.action_params` còn phải mang identity envelope 5 trường theo mục 2.6.2, và payload WAL có thể chứa trường biểu diễn journal/dispatch không thuộc DTO `PlannedTimelineAction`.

Tài liệu không định nghĩa phép chiếu/canonical envelope chung chứng minh hai đầu vào giống nhau byte-for-byte, cũng không quy định việc cấp quyền ký chính xác action params cuối cùng sau khi thêm identity envelope. Vì vậy, hợp đồng có thể từ chối payload hợp lệ do digest khác, hoặc các thành phần có thể tự chọn projection khác nhau và làm suy yếu binding.

**Yêu cầu:** Chọn và đặc tả duy nhất một payload digest contract, gồm schema version, chính xác key set, identity envelope và mọi trường action. Xác định thời điểm tạo payload cuối cùng so với cấp HMAC. Dùng cùng serializer/fixture ở producer, minter, admission, WAL parser và proof completion. Bổ sung ví dụ byte chuẩn tắc và probe thay từng trường, thêm/bớt identity fields, cũng như positive control chứng minh digest được phát hành khớp pending chính xác.

### R29-02 — Bằng chứng “durable ledger” trong pseudocode chỉ nằm trong RAM và restart gate không xác thực ledger entry

`complete_pre_terminal_mutation_with_proof()` ghi nhận proof bằng phép gán `self._consumed_ledger[(execution_id, action_index)] = ...` (dòng 2190–2197), không có thao tác persistence, khóa liên tiến trình, atomic write, `fsync`, hay read-back. Sau restart, `execute_restart_repair()` lại truy vấn cùng dict RAM (dòng 2353–2364), nên bằng chứng biến mất khi tiến trình mới được tạo. Điều này không thực hiện được yêu cầu R28-02 về chứng cứ bền vững độc lập.

Ngay cả nếu coi dict là viết tắt cho một storage service, pseudocode chỉ kiểm tra entry tồn tại và `ledger_entry.execution_id == auth.execution_id`. Nó không xác minh HMAC dù phần report nói có; không đối chiếu `action_index` trong entry, `authorization_id`, target UIDs, payload digest, receipt ID/nonce, verified outcome hay trạng thái consume-once. Một record sai identity hoặc bị sửa có thể qua gate nếu dùng cùng execution ID.

**Yêu cầu:** Đặc tả API storage bền vững cụ thể cho `consume_once` và `read_verified`, gồm serialization/schema, authenticated integrity, uniqueness/atomicity theo đúng identity tuple, lock ordering, fsync và lỗi crash ở từng ranh giới. Restart phải tải record từ storage sau khi khởi tạo process mới và xác thực toàn bộ binding với auth, action pending và proof; thiếu/hỏng/không xác minh được phải giữ `IN_DOUBT_LOCKED` mà không append FINISH, sửa receipt hoặc nhả khóa. Thêm restart thật sự với coordinator instance mới, record bị sửa, cross-action/cross-target replay và lỗi fsync.

### R29-03 — Factory nhận secret HMAC từ caller và dùng schema authorization hẹp, không dùng canonical signed contract đã định nghĩa

Factory `AuthorizationContract.from_signed_authorization(signed_auth, trusted_key)` nhận `trusted_key` trực tiếp từ caller rồi tự xác minh chữ ký trên 10 trường (dòng 1288–1309). Caller có thể tự chọn key, dựng một `signed_auth` tùy ý và ký bằng key đó; factory không chứng minh key thuộc TrialKeyring/issuer đáng tin. Đồng thời, mục 2.3 đã định nghĩa canonical HMAC payload lớn hơn, có `key_id`, source/proposal/approval digests, target/FPS, thời hạn và nonce (dòng 580–614), nhưng factory dùng serializer và schema riêng chỉ gồm 10 trường. Tài liệu còn khai báo lại một `TrialExecutionAuthorization` phiên bản rút gọn tại dòng 1260, với tên/fields khác schema ở mục 2.3 (`approval_id` so với `approval_record_id`).

Do đó factory có thể xác thực một chữ ký hợp lệ theo schema cục bộ nhưng không chứng minh được chứng thư đầy đủ đã được issuer cấp và ký theo giao thức chuẩn. Đây là trust-boundary bypass, không chỉ là khác biệt DTO.

**Yêu cầu:** Xóa tham số key do caller cung cấp. Coordinator/admission authority phải resolve `key_id` từ TrialKeyring nội bộ và áp dụng đúng `canonical_bytes_for_auth_hmac()` cùng schema/version duy nhất ở mục 2.3. Dùng một định nghĩa `TrialExecutionAuthorization` duy nhất và quy tắc ánh xạ rõ `approval_record_id` sang `AuthorizationContract.approval_id`. Factory phải nhận chứng thư đã parse/clone/validate theo strict schema, xác minh toàn bộ chữ ký, rồi ánh xạ geometry; không cho phép caller override key hoặc geometry. Bổ sung probe caller-supplied key, key_id không tồn tại/đã thu hồi, và sửa từng trường bị bỏ khỏi factory HMAC.

## Những phần R29 đã cải thiện

- `ACTION_PENDING` digest gate được thêm vào parser và completion path.
- Restart repair có ý định chặn `FAILED` nếu không có chứng cứ độc lập.
- Geometry được ánh xạ trực tiếp từ signed authorization vào frozen contract.
- Các R27 findings về FSA tuần tự, full DTO read-back, 5 MiB receipt cap và structured cleanup tiếp tục được giữ trong thiết kế.

Các điểm này là tiến bộ thiết kế, nhưng chưa đủ để nghiệm thu cho tới khi payload digest có định nghĩa thống nhất, evidence ledger thực sự bền vững/được xác thực và trust anchor của factory không do caller cung cấp.

## Quyết định và bước tiếp theo

R29 **chưa được nghiệm thu**. Đóng findings R29-01 đến R29-03 trong một hiệu chỉnh thiết kế hữu hạn, đồng bộ phần normative prose với pseudocode và fixtures/crash matrix. Giữ `ready_to_execute=False`; Gate G07 và M1/M2/M3 tiếp tục OPEN. Không mở adapter/live Resolve, không sửa `src/` hoặc `tests/`, và không chạy pytest/app/Resolve/IPC/FusionScript/AI/network trong workorder thiết kế tiếp theo.
