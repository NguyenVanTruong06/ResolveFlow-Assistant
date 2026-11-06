# G07-07B5-D-R30 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review Section 2.6 R30, đối chiếu workorder, report, review R29 và các hợp đồng authorization/ledger trong tài liệu. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network; không sửa `src/` hay `tests/`.

## Kiểm tra tính toàn vẹn hồ sơ

Các SHA-256 được bàn giao khớp tệp hiện tại:

- Task R30: `.bridge/task.md` — `eb11d27897e6f8c5ee3a6a8ea908e96413ce0755f7e1f0d7fadeb5bd596bc39c`
- Review R29: `docs/architecture/07B5-D-R29-code-review.md` — `b65d88184b63a87e9288cefc9519212ef9a020f2b23de57e4dcbc87471443c2a`
- Thiết kế R30: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `b5d6c3539ef496549a6251fd888096b45f2dd6c237a7d70f1005fd454c6e489f`
- Báo cáo R30: `.bridge/report.md` — `5372aaf247313026ec4a93d16f322dd7502fb98b8a4439f87e85613b6ef889c0`
- Validator được dẫn chiếu: `scratch/test_r30_validator.py` — `9415c2b8ba6fde43fa97a9a366780739a94a00d757da1b4e9f1348accc581ac2`

R30 có tiến bộ rõ rệt: đã định nghĩa payload 16 khóa, ledger trên đĩa và HMAC authorization theo bộ trường đầy đủ. Tuy nhiên, prose ở Section 2.3 và API/pseudocode về trust ownership chưa nhất quán, nên các bảo đảm chính chưa được chứng minh end-to-end.

## Findings chặn nghiệm thu

### R30-01 — Section 2.3 vẫn tính action digest từ `PlannedTimelineAction`, không phải payload 16 khóa đã ký

Section 2.6 R30 định nghĩa `action_payload_digest_sha256` là SHA-256 của payload dispatch chuẩn tắc gồm 16 khóa, trong đó có 5 trường identity envelope (dòng 915–932). Parser sau đó so digest của toàn bộ `ACTION_PENDING.action_params` với digest trong authorization (dòng 938–941).

Nhưng quy trình admission tại Section 2.3 vẫn chỉ yêu cầu “Recompute Action Digest từ `PlannedTimelineAction`” rồi đối chiếu cùng trường digest (dòng 617–628, nhất là 622). Tài liệu không chỉ ra phép biến đổi từ `PlannedTimelineAction` sang đúng payload 16 khóa, cũng không nói khi nào các trường `authorization_id`, `execution_id`, `plan_idempotency_key`, target UIDs và `role/schema_version` được gắn vào trước khi HMAC ký. Vì vậy digest ở 2.3 vẫn có thể là digest của DTO action khác với digest mà 2.6 bắt buộc ở WAL; tuyên bố byte-for-byte chưa được phản ánh trong luồng chuẩn tắc.

**Yêu cầu:** Sửa Section 2.3 thành quy trình cụ thể: lắp ráp payload cuối cùng theo đúng schema 16 khóa, quy định mapping từng field từ proposal/plan/authorization và thời điểm tạo execution identity, rồi gọi duy nhất `canonical_action_payload_bytes()` để tính digest trước khi ký toàn bộ auth HMAC. Không giữ hai định nghĩa “action digest” song song. Thêm fixtures byte chuẩn tắc chứng minh payload tạo ở admission bằng chính xác payload ghi vào PENDING; thêm positive và negative probes cho thiếu/thừa khóa, identity binding và sai khác từng field.

### R30-02 — `TrialKeyring` và factory vẫn cho phép caller chọn trust anchor

R30 bỏ tham số `trusted_key` dạng bytes, nhưng pseudocode vẫn định nghĩa `TrialKeyring(keys=..., revoked=...)` nhận mapping tùy ý (dòng 1260–1273), và factory nhận một tham số `keyring` do caller truyền vào theo chữ ký workorder/report. Caller có thể tạo `TrialKeyring({attacker_key_id: attacker_key})`, tự ký authorization rồi gọi factory với keyring đó. Việc `get_key()` tra trong object này không chứng minh đó là keyring được cấp bởi Coordinator/TrialAuthorizationMinter.

Đây là cùng trust-boundary bypass R29-03 ở một lớp gián tiếp: secret không được truyền trực tiếp nhưng nguồn của secret vẫn do caller điều khiển. Nhãn “internal trusted keyring” trong docstring không phải cơ chế sở hữu hay xác thực authority.

**Yêu cầu:** Factory phải lấy keyring từ authority/Coordinator sở hữu nội bộ, không nhận object keyring tùy ý từ caller; hoặc dùng capability/private constructor mà chỉ authority tin cậy có thể cấp, với identity/issuer được xác minh. Đặc tả nơi load keyring, bảo vệ quyền truy cập và vòng đời thu hồi. Bổ sung negative probe truyền TrialKeyring tự dựng, subclass/duck-typed keyring và keyring của authority khác; tất cả phải bị từ chối trước khi tạo contract.

### R30-03 — DurableProofRecord xác thực việc “đã tiêu thụ” nhưng không bind kết quả proof đủ để kết luận FAILED là sạch

Schema `DurableProofRecord` được mô tả gồm identity, digest, scan bounds, timestamp, nonce và `record_hmac_sha256` (report mục 2, Section 2.6 R30). Không có trường `verified_outcome` hoặc cờ kết quả tương đương như `target_item_found=False` và `occupancy_clean=True`, cũng không có digest/ID của readback receipt gốc. Restart repair chỉ cần `read_and_verify_consumed_proof(...)` trả record rồi được phép append `FINISH` và chuyển receipt sang `ABORTED`.

HMAC trên record chứng minh record do keyring ký, nhưng schema hiện tại không chứng minh nội dung quan sát đã được xác minh là outcome cho phép kết luận action thất bại sạch. Scan bounds và nonce tự chúng không nói clip có hiện diện/occupancy có sạch hay không. Do đó record hợp lệ về mật mã vẫn có thể không đủ ngữ nghĩa để kết thúc `ACTION_FAILED` an toàn.

**Yêu cầu:** Ghi vào DurableProofRecord một outcome enum hẹp được tạo sau khi verifier hoàn tất (ví dụ `VERIFIED_TARGET_ABSENT_AND_OCCUPANCY_CLEAN`), digest và định danh receipt/observation gốc, cùng toàn bộ binding execution/action/authorization/target/payload. HMAC phải bao phủ canonical schema versioned của các trường này; restart phải kiểm tra outcome được phép, freshness/nonce-consumption và đối chiếu record với chính `ACTION_FAILED` trước mọi append/receipt transition/unlock. Bổ sung positive control cho đúng outcome và negative probes cho outcome ngược, thiếu outcome, proof khác action/target và record HMAC hợp lệ nhưng ngữ nghĩa không cho phép abort.

## Những phần R30 đã cải thiện

- Đã chuyển bỏ dict RAM sang dịch vụ ledger trên đĩa, có intent `O_EXCL`, temp file, fsync, replace, directory fsync và read-back.
- Đã mô tả restart bằng một Coordinator mới và xác minh HMAC ledger từ đĩa.
- Đã chuyển factory sang tra `key_id` và dùng bộ canonical fields đầy đủ của Section 2.3; đã nêu mapping approval và geometry.
- Đã bổ sung fixtures R30 cho schema payload, key revocation, HMAC 31 trường và ledger qua process restart.

Các điểm này chưa đủ để nghiệm thu cho đến khi digest payload được thống nhất xuyên Section 2.3, keyring không thể do caller tự cấp, và ledger chứng minh được outcome cụ thể dùng để terminalize `FAILED`.

## Quyết định và bước tiếp theo

R30 **chưa được nghiệm thu**. Đóng findings R30-01 đến R30-03 trong một hiệu chỉnh thiết kế hữu hạn, cập nhật luồng admission, API ownership, ledger schema, pseudocode, fixtures và crash matrix. Giữ `ready_to_execute=False`; Gate G07 và M1/M2/M3 tiếp tục OPEN. Không mở adapter/live Resolve, không sửa `src/` hoặc `tests/`, và không chạy pytest/app/Resolve/IPC/FusionScript/AI/network trong workorder thiết kế tiếp theo.
