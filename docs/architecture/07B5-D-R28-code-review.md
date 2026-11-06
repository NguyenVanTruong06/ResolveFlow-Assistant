# G07-07B5-D-R28 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6 / pseudocode R28, đối chiếu workorder, report, review R27 và hợp đồng Authorization/Receipt/Journal. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network; không sửa `src/` hay `tests/`.

## Kiểm tra tính toàn vẹn hồ sơ

Các SHA-256 bàn giao khớp với tệp hiện tại:

- Task R28: `.bridge/task.md` — `b8a9f3dd6a3eb47c0375bc5e125bf27cf07fad55de3440e58a80e533f2642ddb`
- Review R27: `docs/architecture/07B5-D-R27-code-review.md` — `e859305a39e9da3aa25e1ff3a024749d7db619eb9e028ebcf30f725e5c7c06c6`
- Thiết kế R28: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `2b620126d4ce3a9a7e9e0a2968ff2747dc0aa4c09fbb329e0e80fcabf939aba0`
- Báo cáo R28: `.bridge/report.md` — `7e31cbac54d9e2be5c5b32ed8a5d5e3b3f522d8e4e657771eebdbe7e3f1b15ef`

R28 đã đóng các điểm R27 về geometry/Auth cross-match, FSA tuần tự, read-back DTO, receipt cap và lỗi cleanup. Tuy vậy, còn các đường phục hồi/ghi chứng từ có thể chấp nhận WAL chưa được chứng thực hoặc payload chưa được bind với digest đã ký.

## Findings chặn nghiệm thu

### R28-01 — Proof completion không hash đối chiếu toàn bộ ACTION_PENDING với digest đã ký trước khi ghi

`verify_readback_proof()` xác minh proof digest bằng `digest == auth.action_payload_digest_sha256` (`07B5-isolated-resolve-acceptance-gate-design.md:1782-1794`) và cross-match action index, track, geometry với `pending_entry` (`:1796-1834`). Nhưng không tính `SHA256(canonical_serialize(pending_entry.action_params))` để xác nhận chính payload pending là payload đã ký.

Trong `_read_and_parse_wal()`, ACTION_PENDING chỉ được kiểm tra thứ tự index và lưu lại (`:1894-1905`). Digest của payload được kiểm tra ở ACTION_COMMITTED và ACTION_FAILED (`:1932-1934`, `:1965-1967`), nhưng `complete_pre_terminal_mutation_with_proof()` lấy snapshot PENDING, xác minh proof rồi append ACTION_FAILED + FINISH và cập nhật receipt; pseudocode không parse lại WAL sau append trước khi release. Vì thế pending payload có thể chứa trường khác (ví dụ source path/action parameter) nhưng cùng index/track/geometry; proof vẫn mang digest của auth, còn code không chứng minh digest của pending khớp auth trước mutation. Kết quả là có thể terminalize một payload không được ủy quyền.

**Yêu cầu:** Trước `consume_once` và trước mọi WAL append, canonicalize/hash toàn bộ `pending_entry.action_params` theo đúng contract digest đã ký và yêu cầu khớp `auth.action_payload_digest_sha256`; đảm bảo phép chiếu/digest dùng cùng quy tắc giữa producer, authorization và Journal. Thêm negative probes sửa từng trường payload không phải geometry trong PENDING, giữ proof/Auth digest hợp lệ, xác nhận 0 proof consumption, 0 append, 0 receipt transition và lock vẫn giữ.

### R28-02 — Restart repair có thể tin ACTION_FAILED trong WAL mà không xác minh bằng chứng độc lập

FSA cho phép ACTION_FAILED chuyển sang `FAILED` sau khi kiểm tra payload/note (`:1941-1974`). Restart repair ánh xạ `FAILED -> ABORTED` (`:2228-2237`), rồi append FINISH cho trạng thái `FAILED` (`:2241-2269`), đồng bộ Receipt và cuối cùng release lock (`:2360-2367`). Nhánh repair không xác minh proof HMAC, trạng thái `DurableConsumedReceiptLedger`, `receipt_id`/nonce đã tiêu thụ, hay live readback tương ứng với ACTION_FAILED.

Điều này mâu thuẫn với trust boundary của Section 2.6, vốn nói WAL là local/unauthenticated và ACTION_FAILED sau ACTION_PENDING không tự chứng minh clean rejection hoặc absence. Một WAL bị sửa/giả có thể cung cấp ACTION_FAILED hợp lệ về cú pháp với note `ACTION_FAILED_CONFIRMED_CLEAN`; restart repair sau đó tự hoàn tất và nhả khóa mà không có chứng cứ đọc lại. Fixture `CR-DIS-05b` cũng yêu cầu forged ACTION_FAILED không có evidence phải giữ `IN_DOUBT`, nhưng pseudocode repair hiện không có gate đó.

**Yêu cầu:** Khi repair thấy `FAILED`, yêu cầu xác minh độc lập rằng evidence đã được Coordinator chấp nhận và consume bền vững cho đúng authorization/execution/action/payload, hoặc tạo fresh live observation theo trust protocol. Nếu bằng chứng không tồn tại, không khớp, đã tiêu thụ sai nonce, hay key không có sau restart, giữ lock và trả `TERMINAL_ACTION_FAILED_IN_DOUBT`; không append FINISH/đổi Receipt/nhả lock. Thêm crash-recovery probes cho giả mạo ACTION_FAILED, thiếu consumed-ledger record, ledger identity mismatch và evidence key unavailable.

### R28-03 — Geometry mới có trong AuthorizationContract nhưng chưa được bind rõ với HMAC authorization nguồn

`AuthorizationContract` R28 hiện có `record_frame` và `duration_frames` (`:1234-1245`), nhưng canonical HMAC serializer của `TrialExecutionAuthorization` ở phần trước (`:580-620`) vẫn ký các trường `authorized_record_frame` và `authorized_timeline_frame_duration`; không có serializer/constructor rule tại ranh giới này chứng minh `AuthorizationContract.record_frame == TrialExecutionAuthorization.authorized_record_frame` và `duration_frames == authorized_timeline_frame_duration`, hoặc chứng minh hai alias mới được đưa vào cùng bytes đã ký.

Verifier kiểm tra geometry của `AuthorizationContract` là int hợp lệ và so sánh PENDING với các trường đó (`:1674-1680`, `:1823-1827`), nhưng nếu `AuthorizationContract` được dựng/nhận riêng với giá trị khác chứng thư HMAC thì cross-match chỉ chứng minh PENDING khớp DTO chưa được chứng thực. R28 workorder nói geometry được đưa vào canonical signed payload; hồ sơ hiện chỉ cho thấy hai tên trường upstream tương đương, chưa cho thấy binding bắt buộc.

**Yêu cầu:** Định nghĩa một đường chuyển đổi duy nhất từ chứng thư đã HMAC-verify sang AuthorizationContract: gán `record_frame` từ `authorized_record_frame`, `duration_frames` từ `authorized_timeline_frame_duration`, xác minh các giá trị/quan hệ trước khi tạo contract, và cấm caller override. Hoặc cập nhật canonical HMAC payload để ký trực tiếp cả hai trường với schema version. Bổ sung probe thay đổi AuthorizationContract geometry sau khi HMAC verify và chứng minh bị từ chối trước proof/WAL/receipt mutation.

## Những phần R28 đã cải thiện

- Geometry đã được khai báo trên AuthorizationContract và kiểm tra kiểu/range; PENDING được đối chiếu với Auth trước khi proof được nhận (`:1234-1245`, `:1674-1680`, `:1810-1834`).
- FSA kiểm tra count START, index tuần tự ở PENDING/COMMITTED/FAILED, và coi FAILED là terminal (`:1882-2004`).
- Restart repair so sánh toàn bộ DTO trên cả nhánh idempotent lẫn replace (`:2287-2295`, `:2346-2353`).
- Receipt cap đã thống nhất 5 MiB; reader xác thực `max_bytes` và chỉ đọc tối đa `remaining + 1` để bắt overflow (`:898`, `:1611-1643`).
- Cleanup unlink có lỗi cấu trúc và phân biệt replace thành công nhưng directory fsync thất bại (`:2327-2344`).

## Quyết định và bước tiếp theo

R28 **chưa được nghiệm thu**. Đóng findings R28-01 đến R28-03 trong một hiệu chỉnh thiết kế bounded, cập nhật pseudocode và fixtures/crash matrix. Giữ `ready_to_execute=False`; Gate G07 và M1/M2/M3 tiếp tục OPEN. Không mở adapter/live Resolve, không sửa `src/` hoặc `tests/`, và không chạy pytest/app/Resolve/IPC/FusionScript/AI/network trong workorder thiết kế tiếp theo.
