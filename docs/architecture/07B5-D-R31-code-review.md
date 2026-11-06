# G07-07B5-D-R31 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Sections 2.3 and 2.6 R31, đối chiếu workorder, report, review R30 và các hợp đồng/pseudocode liên quan. Không chạy validator, pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network; không sửa `src/` hay `tests/`.

## Kiểm tra tính toàn vẹn hồ sơ

Các SHA-256 bàn giao khớp tệp hiện tại:

- Task R31: `.bridge/task.md` — `a82d559aa6c3909ddf903c63b73432ad5d6807ea64cb7de93633751550e3027b`
- Review R30: `docs/architecture/07B5-D-R30-code-review.md` — `4fa80de1c2e4a4ccb8304ece3e79f9ceedbd93ea01ffdfc29a37a7e22c3f6a94`
- Thiết kế R31: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `5f91c9e717ec88994e94000c6573be82f8616a831c8288e8383df0c7ced630d4`
- Báo cáo R31: `.bridge/report.md` — `4c0f2b48aa978c160ad30eb7ba23538d3c15cf847b7ea4f0e5be7270bf061e6d`
- Validator được dẫn chiếu: `scratch/test_r31_validator.py` — `246f96c327afe80ca6dc9dfac398aa048f73ee088174a946fbd9f57c1b58e264`

R31 xử lý đúng các chủ đề R30 ở mức mô tả, nhưng pseudocode chưa thực thi đầy đủ các bảo đảm được nêu. Các residual dưới đây vẫn ảnh hưởng trực tiếp đến action binding, trust anchor và consume-once evidence.

## Findings chặn nghiệm thu

### R31-01 — Payload assembler chưa nhận `PlannedTimelineAction` và có hai định nghĩa chuẩn tắc không đồng nhất

Workorder yêu cầu `assemble_canonical_action_payload(action: PlannedTimelineAction, auth, execution_id)`. Pseudocode Section 2.3 lại nhận các scalar `action_index`, `action_type`, `role` (dòng 628–634); Section 2.6 lặp lại assembler với cùng scalar API (dòng 1393–1400). Không có hàm nào trong hai nơi lấy trực tiếp các giá trị từ một `PlannedTimelineAction` và chứng minh chúng khớp plan đã được duyệt trước khi digest được ký.

Ngoài ra, canonical serializer cũng được định nghĩa hai lần: Section 2.3 chỉ kiểm tra key set rồi serialize (dòng 654–659), còn bản Section 2.6 thêm primitive type gates (dòng 1367–1388). Hai mô tả có thể cho cùng bytes với payload hợp lệ, nhưng không phải một contract implementation duy nhất như R31 tuyên bố. `execution_id` còn là tham số riêng của assembler trong khi factory ánh xạ `AuthorizationContract.execution_id = signed_auth.token_nonce` (dòng 1991); chưa có invariant tại assembler bắt buộc đối số này bằng giá trị execution identity đã bind.

**Yêu cầu:** Định nghĩa một assembler duy nhất nhận `PlannedTimelineAction` cùng authorization/identity context, trích xuất tất cả 11 action fields từ action đã duyệt, áp dụng type/range gates và kiểm tra mọi field đã ký; bắt buộc execution ID bằng identity mapping được xác lập ở factory. Giữ đúng một định nghĩa canonical serializer hoặc dẫn chiếu tới cùng một định nghĩa chuẩn tắc, không lặp hai pseudocode có validation khác nhau. Cập nhật Minter, Admission, WAL writer/parser và fixtures dùng cùng API/bytes.

### R31-02 — TrialKeyring vẫn có thể do caller cấp qua constructor của RecoveryCoordinator

R31 loại keyring khỏi `bind_signed_authorization()`, nhưng `RecoveryCoordinator.__init__(..., keyring: TrialKeyring)` vẫn nhận keyring do caller truyền vào và gán thẳng `self._keyring = keyring` (dòng 1935–1940). Bản thân `TrialKeyring.__init__(keys, revoked)` nhận mapping tùy ý (dòng 1331–1335). Caller vì vậy vẫn có thể tự dựng keyring, tạo chữ ký bằng key của mình, rồi khởi tạo Coordinator với keyring đó. Điều này giữ nguyên trust-anchor injection mà R30-02/R31 yêu cầu loại bỏ.

**Yêu cầu:** Coordinator phải tự nạp keyring từ nguồn tin cậy nội bộ, không nhận object/key mapping qua public constructor; hoặc nhận một capability authority không thể caller tự dựng và kiểm chứng được nguồn cấp. Đặc tả ownership/lifecycle của keyring và bảo vệ API `TrialKeyring` khỏi caller-created instance. Thêm probe khởi tạo Coordinator với keyring giả, subclass hoặc duck-typed object; không được cho phép bind authorization.

### R31-03 — Ledger consume-once dùng check-then-replace, không có atomic `O_EXCL` trên record đích

`record_consumed_proof()` tuyên bố atomic consume-once bằng `O_CREAT | O_EXCL` (dòng 1622), nhưng thực tế chỉ mở tệp đích `O_RDONLY` để kiểm tra tồn tại rồi bắt `FileNotFoundError` (dòng 1623–1628). Hai tiến trình đồng thời có thể cùng thấy đích vắng mặt, cùng ghi temp files, rồi lần lượt `os.replace()` cùng tên đích (dòng 1657–1678); writer cuối ghi đè chứng cứ của writer trước. `O_EXCL` chỉ được dùng cho tên temp, không khóa/reserve tên ledger cuối.

**Yêu cầu:** Đặc tả primitive uniqueness nguyên tử cho record key `(execution_id, action_index)` dưới khóa bền vững hoặc tạo reservation bằng `O_CREAT | O_EXCL` tại tên đích và có protocol crash-recovery rõ ràng trước khi publish record. Không dùng existence check rồi replace như cơ chế consume-once. Bổ sung concurrency probe với hai process cùng consume một key, crash sau reservation, và retry sau khi record đã được publish; tối đa một record được chấp nhận và không được âm thầm overwrite.

### R31-04 — Writer ledger tự nhận `verified_outcome`, raw proof và HMAC key thay vì chỉ nhận evidence đã được xác minh

`DurableConsumedReceiptLedger.record_consumed_proof()` nhận trực tiếp `raw_proof`, `verified_outcome` và `trusted_key` từ caller (dòng 1605–1613). Hàm chỉ kiểm tra outcome thuộc enum (dòng 1618–1620), lấy `proof_id`, scan bounds rồi tự ký record; pseudocode không gọi `normalize_and_verify_readback_proof()` hoặc nhận một verified-proof capability do Coordinator tạo. Vì vậy HMAC ledger chứng minh record được ký bằng key đưa vào hàm, nhưng không tự chứng minh outcome đó bắt nguồn từ proof đã được xác minh. Đây là ranh giới đặc biệt quan trọng vì restart repair dựa vào outcome sạch để chuyển receipt sang `ABORTED` và nhả khóa.

**Yêu cầu:** Chuyển writer thành API nội bộ của Coordinator, không nhận raw key/outcome tùy ý. Chỉ cho phép ghi từ đối tượng verified, immutable do verifier/Coordinator phát hành sau khi đã kiểm tra schema 22 trường, HMAC, identity, nonce, freshness và semantics; ledger phải tự lấy key từ authority nội bộ. Digest raw proof cần dùng canonical serializer strict, versioned và cùng bytes với dữ liệu được normalize/verify. Thêm negative probes cho caller tự tạo verified outcome, outcome không khớp raw proof, raw proof bị sửa sau verify và key injection.

## Những phần R31 đã cải thiện

- Section 2.3 nay mô tả action digest trên payload 16 khóa và cập nhật bước recompute của Admission.
- Coordinator có phương thức `bind_signed_authorization()` thay cho factory nhận keyring trực tiếp.
- DurableProofRecord có outcome enum, raw proof ID/digest; restart repair kiểm tra outcome sạch trước khi kết thúc `FAILED`.
- R31 bổ sung fixtures về action payload, keyring, semantic outcome và HMAC proof record.

## Quyết định và bước tiếp theo

R31 **chưa được nghiệm thu**. Đóng findings R31-01 đến R31-04 trong một hiệu chỉnh thiết kế hữu hạn, đồng bộ API và pseudocode với các tuyên bố trong report, đồng thời cập nhật fixtures/crash matrix. Giữ `ready_to_execute=False`; Gate G07 và M1/M2/M3 tiếp tục OPEN. Không mở adapter/live Resolve, không sửa `src/` hoặc `tests/`, và không chạy pytest/app/Resolve/IPC/FusionScript/AI/network trong workorder thiết kế tiếp theo.
