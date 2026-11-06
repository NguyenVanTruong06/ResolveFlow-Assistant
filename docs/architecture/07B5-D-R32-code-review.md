# G07-07B5-D-R32 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** CHANGES_REQUIRED  
**Phạm vi:** Static review Sections 2.3 và 2.6, đối chiếu workorder R32, report, review R31 và validator source. Không chạy validator, pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network; không sửa src/ hay tests/.

## Kiểm tra tính toàn vẹn hồ sơ

Các SHA-256 chính do R32 bàn giao khớp với tệp hiện tại:

- Workorder: .bridge/task.md — 5c6133ed8e15aad765ef8acf02b2e7e37e5795c7e82d42f40a49df1ef818efe0
- Review R31: docs/architecture/07B5-D-R31-code-review.md — 94f8ca9a768e740cd93a6d0fe9b7342dea0dd9aff96c440d26cae61c64c16ffc
- Thiết kế R32: docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md — 1ede7d672ba6be1a8ae1d0c32abc4ee4e837359b21e6a34936875ad4f8211fad
- Báo cáo R32: .bridge/report.md — f92dbe9e031c86b805fdc0e09acc97b6395ea798dfb5109904b401325cfa3283
- Validator: scratch/test_r32_validator.py — a7159746a1c3872a7af15269f78217058434632338dcc6ea410b3d653579aa8a

R32 đã cải thiện assembler, dùng O_EXCL trên tên đích và bổ sung luồng cấp capability. Tuy nhiên, các cơ chế “sealed/internal” hiện vẫn có thể được dựng từ API công khai; một nhánh còn bỏ qua proof normalizer; và hợp đồng execution identity trong task không khớp thiết kế.

## Findings chặn nghiệm thu

### R32-01 — Execution identity trong workorder không khớp hợp đồng assembler

Workorder R32 yêu cầu execution_id == auth.execution_id (.bridge/task.md:5). Nhưng TrialExecutionAuthorization tại Section 2.3 không có trường execution_id; assembler ở docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md:668-672 lại kiểm tra execution_id == auth.token_nonce. Section 2.6 tiếp tục ánh xạ AuthorizationContract.execution_id = signed_auth.token_nonce (:2004-2009). Báo cáo cũng mô tả mapping sang token_nonce.

Đây là mâu thuẫn giữa acceptance contract và implementation pseudocode, không thể giải quyết bằng cách gọi token_nonce là execution identity ở một nơi mà task yêu cầu trường khác. Nó khiến Minter, Admission, WAL và durable ledger có thể dùng hai khái niệm ID khác nhau.

**Yêu cầu:** Chốt một mapping có thẩm quyền duy nhất. Hoặc thêm execution_id vào authorization được ký và dùng nó xuyên suốt; hoặc sửa workorder để xác định rõ token_nonce chính là execution ID, với UUID/schema và mọi tham chiếu nhất quán. Thêm positive/negative fixtures cho mapping và mismatch.

### R32-02 — SealedKeyringAuthority không có seal đáng tin cậy và vẫn nhận authority do caller cấp

Section 2.6 khai báo _INTERNAL_KEYRING_SEAL_MAGIC ở module scope, cho phép gọi công khai SealedKeyringAuthority.load_internal_authority(keys) với mapping tùy ý (:1382-1396). Constructor của RecoveryCoordinator còn nhận authority từ caller và chấp nhận mọi instance qua isinstance (:1936-1957), nên subclass cũng qua cổng. Giá trị seal không phải capability bí mật nếu caller có thể gọi factory công khai; đồng thời khóa mặc định là literal trong source (:1956-1957), không phải keyring được nạp từ keystore tin cậy.

Validator lặp lại đúng đường bypass này: tạo authority từ keys tùy ý rồi truyền vào Coordinator (scratch/test_r32_validator.py:758-773). Do đó probe không chứng minh việc chống key injection; nó chứng minh caller có thể tạo trust anchor của riêng mình.

**Yêu cầu:** Coordinator phải tự nạp authority từ keystore tin cậy do process sở hữu; bỏ tham số authority công khai và factory nhận arbitrary keys. Không dùng module constant làm seal. Nếu cần dependency injection cho test, dùng interface test-only không thể đi vào production boundary. Kiểm tra cả subclass, factory public, monkeypatch và khóa mặc định có thể đoán.

### R32-03 — Verified capability có thể giả mạo; ledger writer vẫn nhận trust material từ caller

VerifiedReadbackEvidence là dataclass công khai với trường token constructor-visible; verify_capability() chỉ so sánh token với _INTERNAL_CAPABILITY_MAGIC ở module scope (:1416-1437). Caller có thể đọc hằng số và tự tạo object hợp lệ. Probe chỉ thử chuỗi "caller_invented_token" (scratch/test_r32_validator.py:790-803), không thử token thật lấy từ module hay gọi public mint factory.

Ngoài ra, DurableConsumedReceiptLedger.record_consumed_evidence() vẫn public và nhận key_resolver, signer_key_id, auth do caller truyền (:1624-1630). Điều này trái với workorder yêu cầu writer nội bộ, không nhận HMAC key/trust authority từ caller. Một forged capability cộng với key resolver do caller kiểm soát có thể tạo ledger record mà HMAC chỉ xác nhận chính dữ liệu do caller ký.

**Yêu cầu:** Chuyển mint/consume vào một API nội bộ duy nhất do Coordinator sở hữu; ledger tự giữ authority/key resolver, không nhận callback/key ID tùy ý từ caller. Capability cần ràng buộc bằng cơ chế không thể tạo qua constructor/factory công khai và phải bind đầy đủ proof digest, semantic outcome, auth/execution/action identity. Bổ sung probe dùng đúng token thật, dataclasses.replace, subclass, gọi trực tiếp ledger và key resolver giả.

### R32-04 — Một đường ghi proof bỏ qua normalize_and_verify_readback_proof() và outcome vẫn do caller điều khiển

Luồng complete_pre_terminal_mutation_with_proof() có gọi verify_readback_proof() (:2148-2158), nhưng tham số verified_outcome vẫn được caller truyền vào với default (:2144-2149) rồi chép trực tiếp vào capability (:2162-2172). Luồng khác RecoveryCoordinator.verify_and_record_proof() (scratch/test_r32_validator.py:522-566) chỉ canonicalize raw mapping và suy outcome từ một vài boolean (target_item_found, occupancy_clean, geometry_matches); không gọi normalize_and_verify_readback_proof() hoặc verifier 22 trường trước khi mint capability. Cả hai API cùng tồn tại, với độ mạnh xác minh khác nhau.

Vì restart repair dùng outcome sạch để cho phép terminalize FAILED, đường thứ hai có thể biến các field do caller cung cấp thành bằng chứng HMAC hợp lệ mà không xác minh schema, HMAC, identity, nonce, freshness và semantics như workorder yêu cầu.

**Yêu cầu:** Chỉ giữ một entrypoint Coordinator-owned. Entry point phải normalize và xác minh đầy đủ proof trước khi suy outcome; bỏ verified_outcome khỏi input. Tính raw proof digest trên đúng canonical bytes đã được verifier xác nhận. Thêm probes cho thiếu trường, trường lạ, proof HMAC sai, nonce/timestamp cũ, identity mismatch và booleans mâu thuẫn.

### R32-05 — O_EXCL loại race ghi đè nhưng chưa đặc tả phục hồi tệp record dở dang sau crash

Ledger tạo trực tiếp tên record bằng O_CREAT | O_EXCL, sau đó mới serialize/ghi/fsync nội dung (:1638-1679; validator có cùng trình tự tại scratch/test_r32_validator.py:348-419). Nếu process chết sau khi tạo record nhưng trước khi ghi đủ hoặc fsync, tên đích vẫn có thể tồn tại với nội dung rỗng/một phần. Retry sẽ nhận ERR_PROOF_ALREADY_CONSUMED; restart reader sẽ gặp JSON hỏng. Hiện chưa có trạng thái reservation, cách phân biệt record đã consume với reservation dở dang, hay repair có thẩm quyền.

**Yêu cầu:** Đặc tả crash protocol cho từng ranh giới sau O_EXCL: giữ record dở dang fail-closed và yêu cầu quarantine/operator reconciliation, hoặc dùng reservation riêng có ownership/expiry và publish protocol an toàn. Không tự xóa hay ghi đè record chưa xác minh. Bổ sung fault probes cho crash sau create, giữa short writes, sau file fsync và trước/sau directory fsync.

## Finding hồ sơ — R32 workorder trỏ sai SHA của review R31

R32 .bridge/task.md:4 ghi SHA của review R31 là b65d8818..., trong khi tệp review R31 hiện tại và phần forensic ledger của .bridge/report.md đều cho 94f8ca9a.... Hash thực tế được xác minh ở đầu review này là 94f8ca9a768e740cd93a6d0fe9b7342dea0dd9aff96c440d26cae61c64c16ffc. Cần sửa tham chiếu để audit chain không dựa vào một artifact khác hoặc hash không khớp.

## Quyết định và bước tiếp theo

R32 **chưa được nghiệm thu**. Đóng R32-01 đến R32-05 và finding hồ sơ trong một lần hiệu chỉnh thiết kế có ma trận probe bao phủ đúng các đường API công khai và các điểm crash. Đồng bộ report, task, Section 2.3/2.6 và validator với cùng một identity mapping, trust boundary và proof-verification pipeline.

Giữ DESIGN ONLY, ready_to_execute=False, Gate G07 và M1/M2/M3 ở trạng thái OPEN. Không sửa src/ hoặc tests/; không mở live Resolve, adapter hoặc thực thi. Việc ghi nhận 4/4 prototype suites trong report là báo cáo của worker; review này không chạy lại validator và không xem đó là bằng chứng độc lập.
