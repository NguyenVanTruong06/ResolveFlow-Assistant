# G07-07B5-D-R23 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6 và pseudocode R23; đối chiếu hợp đồng Receipt/Journal hiện có. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.

## Kiểm tra tính toàn vẹn hồ sơ

Các hash bàn giao khớp với tệp trên workspace:

- Task R23: `.bridge/task.md` — `4d4e5bf7509470f35bb7b057ae6db7f413974d9c02fe967c3dbd3748b2188d84`
- Review R22: `docs/architecture/07B5-D-R22-code-review.md` — `14e29f01b1f00f07200fe2965eba3591055e296c149435344061144cf0b1a513`
- Thiết kế R23: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `1aeec35c080bac23bd87d5f56dc9a94a65c7bf42487c49870cb23c1601e2844a`
- Báo cáo R23: `.bridge/report.md` — `d3b6b18037e7e6cdbbdc6b0dfaef431bbd06995f4e505e7d7e51ec454d6f73ca`

R23 đóng được các vấn đề R22 về hash tên lock thống nhất, bounded WAL reader, và thêm nhánh `FINISHED_SUCCESS`. Tuy nhiên, còn các mâu thuẫn giữa projection với replay contract, sai authority/path lưu Receipt, và các nhánh ghi/phục hồi chưa chứng minh commit hoàn tất.

## Findings chặn nghiệm thu

### R23-01 — Bổ sung proof metadata vào `action_params` phá vỡ payload equality đã được ràng buộc

R23 yêu cầu sao chép nguyên `action_params` từ `ACTION_PENDING`/`ACTION_COMMITTED`, rồi thêm `error_code`, `proof_id`, `observation_nonce`, `verified_item_absent` hoặc rollback metadata vào cùng dictionary (`07B5...design.md:1086-1097, 2111-2174`). Nhưng source replay so sánh chính xác key set, từng value và canonical bytes giữa `ACTION_PENDING` với `ACTION_FAILED` (`src/services/resolve_execution_engine.py:1396-1415`). Việc thêm bất kỳ trường proof nào khiến so sánh này thất bại. `validate_raw_journal_record()` chỉ kiểm tra allowlist envelope và primitive types; nó không miễn trừ hoặc định nghĩa phép chiếu payload này.

**Yêu cầu:** Chốt một schema duy nhất tương thích replay: giữ action payload bất biến nguyên vẹn ở vùng được so sánh, và đặt evidence trong trường/stage riêng có allowlist, hoặc cập nhật rõ consumer contract với phép so sánh projection xác định. Cung cấp fixture chứng minh cùng một WAL được chấp nhận bởi producer, strict parser, coordinator và consumer; không thể tuyên bố vừa “payload equality byte-for-byte” vừa thêm metadata vào payload được so sánh.

### R23-02 — Readback proof vẫn chưa được validate theo schema đầy đủ và canonical serializer dùng không nhất quán

Nhánh receipt proof chỉ yêu cầu một tập nhỏ gồm `receipt_id`, nonce, hai boolean, `key_id` và HMAC; chỉ đối chiếu `execution_id` cùng `target_timeline_uid` với auth (`khoảng dòng 2044-2076`). Nó không enforce exact allowlist/full required receipt schema đã định nghĩa trước đó, cũng không đối chiếu các binding như `authorization_id`, `target_project_uid`, `action_index`, `action_payload_digest_sha256` và geometry trước khi dùng proof để hoàn tất một action cụ thể. HMAC được tính bằng `json.dumps(..., sort_keys=True, separators=...)` với default `ensure_ascii=True` và `allow_nan=True`, khác canonical byte contract đã chốt (`canonical_json_bytes`, UTF-8, `ensure_ascii=False`, `allow_nan=False`).

**Yêu cầu:** Type-gate proof thành đúng DTO/graph sở hữu, enforce exact schema và mọi binding của phiên/action/target, rồi gọi một serializer canonical dùng chung để ký lẫn verify. Reject mọi extra/missing field, bool coercion, NaN/Infinity, invalid UTF-8 và stale/cross-action receipt trước `consume_once`.

### R23-03 — Receipt được tìm trong `journals/`, trái với vị trí lưu thực tế `receipts/`

`RecoveryCoordinator` chỉ mở `_journals_dir_fd`; `_durable_atomic_replace_receipt()` tạo `receipt_filename = f"{execution_id}.receipt.json"` rồi mở bằng `dir_fd=self._journals_dir_fd` (`07B5...design.md:1404-1406, 1820-1859`). Nhưng `ExecutionReceiptService` đặt receipt tại `receipts_dir/{execution_id}.receipt.json`, tách biệt với `ExecutionJournalService.journals_dir/{execution_id}.journal.jsonl` (`src/services/execution_receipt_service.py:155-163`; `src/services/execution_journal_service.py:82-88`). Vì vậy pseudocode không đọc/cập nhật receipt authority hiện hành; nó sẽ báo thiếu hoặc thao tác nhầm artifact trong thư mục Journal.

**Yêu cầu:** Mở và giữ riêng descriptor neo cho `receipts_dir`; dùng đúng `_receipt_path`/receipt service contract và ràng buộc `execution_id` an toàn trước mọi filename construction. Không chuyển Receipt vào `journals/` nếu chưa có migration/schema decision và cập nhật đồng bộ mọi consumer.

### R23-04 — File descriptor neo không ngăn path traversal nếu `execution_id` chưa được xác thực

Các thao tác `openat` tương đối dùng tên `f"{auth.execution_id}.journal.jsonl"` và `f"{execution_id}.receipt.json"` (`1603-1609`, `1843-1849`, `2335-2340`). `dir_fd` chỉ neo thư mục; nó không làm an toàn một pathname có dấu `/` hoặc `..`. `TrialExecutionAuthorization.execution_id` được mô tả là UUIDv4 nhưng DTO/pseudocode không thể hiện bước parse/validate UUID nghiêm ngặt trước khi ghép filename. Một caller cấp chuỗi chứa path component có thể khiến relative open đi ra ngoài directory FD.

**Yêu cầu:** Validate `execution_id` bằng parser UUIDv4 strict tại constructor và ranh giới public trước IO; filename chỉ được nhận một basename đã chuẩn hóa, cấm separator, dot components, NUL và Unicode malformed. Descriptor anchoring không thay thế filename validation.

### R23-05 — Restart repair chưa có terminal epilogue và không thể chứng minh đã nhả khóa an toàn

`execute_restart_repair()` kết thúc pseudocode ngay sau lời gọi `_durable_atomic_replace_receipt()` trong `try` (`khoảng dòng 2368-2372`). Không có nhánh `except`, read-back result, `release_target_lock()` hay `RecoveryTransactionResult(success=True, ...)` cho các state repair. Vì vậy ngay cả khi receipt update thành công, tài liệu không chỉ ra lúc nào lock được nhả hoặc terminal result được trả; khi lỗi xảy ra cũng không mô tả cách giữ owner/lock. Phần mô tả Section 11 nói có nhả khóa, nhưng pseudocode là đường thực thi chi tiết và đang thiếu bước này.

**Yêu cầu:** Hoàn thiện pseudocode cho từng state tới kết quả cuối: WAL revalidation, receipt transition/read-back, kiểm tra ledger/idempotency cần thiết, rồi mới release đúng descriptor và trả result. Tất cả exception trước commit cuối phải giữ ownership; không có đường thành công nào chỉ được suy ra từ việc helper được gọi.

### R23-06 — Append WAL vẫn dùng `os.write()` đơn lần và có thể ghi record một phần

Receipt temp file có vòng lặp short-write, nhưng append outcome/FINISH và restart FINISH dùng một `os.write(fd, line_bytes)` cho mỗi record (`khoảng dòng 2202-2211, 2335-2344`). `os.write()` có thể trả về số byte nhỏ hơn yêu cầu mà không ném lỗi. Code sau đó fsync và có thể ghi record kế tiếp, tạo torn JSONL nhưng tiếp tục coi append đã thành công. Điều này phá framing/seq và làm crash repair rơi vào trạng thái WAL hỏng.

**Yêu cầu:** Dùng helper `write_all()` bounded cho mọi record WAL, xử lý EINTR/short write/zero write, fsync sau từng ranh giới stage; nếu không ghi đủ byte thì giữ lock và trả IN_DOUBT. Bao gồm cả completion và restart-repair append paths.

## Những điểm R23 đã cải thiện

- Dùng cùng namespace `timeline_<sha256(uid)>.lock` cho timeline UID và ghi metadata định danh trong lock file.
- Thêm bounded chunk reader và kiểm tra file identity/size sau đọc.
- Chuyển receipt update sang `ExecutionReceipt` parse/replace/serialize/read-back theo DTO đầy đủ.
- Thêm repair cho `FINISHED_SUCCESS` và kiểm tra số committed actions so với planned count.
- Bảo toàn `ready_to_execute=False`, không mở Resolve execution hay UI wiring.

## Quyết định

R23 **chưa được nghiệm thu**. Findings R23-01 đến R23-06 chặn tính nhất quán của proof, WAL, Receipt và restart recovery. Giữ `ready_to_execute=False`; chưa mở G07-07B6-I hoặc bất kỳ live Resolve work nào. Workorder tiếp theo nên là **G07-07B5-D-R24**, DESIGN ONLY, xử lý các findings trên và bổ sung fixtures đối chiếu chính xác WAL consumer với đường dẫn Receipt nguồn.

Đây là static review; không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.
