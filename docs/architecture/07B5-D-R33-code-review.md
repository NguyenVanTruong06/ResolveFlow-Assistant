# G07-07B5-D-R33 — Codex Static Design Review

**Ngày:** 2026-11-03  
**Quyết định:** CHANGES_REQUIRED  
**Phạm vi:** Static review Sections 2.3 và 2.6, đối chiếu workorder R33, report, review R32 và validator source. Không chạy validator, pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network; không sửa src/ hay tests/.

## Kiểm tra tính toàn vẹn hồ sơ

Các SHA-256 bàn giao khớp tệp hiện tại:

- Workorder R33: .bridge/task.md — d72baf9d00d73b556fd6188752ab87524c0a98511771b0ed7e0a629558d21acf
- Review R32: docs/architecture/07B5-D-R32-code-review.md — 9b0aab2268ca9a9ff503b7dc7ebdc716c279b73661d3335474f375e49110586a
- Review R31: docs/architecture/07B5-D-R31-code-review.md — 94f8ca9a768e740cd93a6d0fe9b7342dea0dd9aff96c440d26cae61c64c16ffc
- Thiết kế R33: docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md — c3757176b263aee28e356cfb9d23e2b2dcc40c487cceb41fef839af300451958
- Báo cáo R33: .bridge/report.md — 4103c94382a8c02f2c260339d4506945241c0ea422d34c124cb9311b45662a7f
- Validator R33: scratch/test_r33_validator.py — 1a42750783c442dd0c3f5d1c64add102180807a1d2f2fd5fe63fb15bad191567

R33 đóng một số mâu thuẫn R32 ở cấp mô tả, nhưng chi tiết pseudocode chưa bảo đảm các tuyên bố bảo mật và độ bền. Một số DTO/writer/reader hiện còn không thể cùng tuân theo một schema.

## Findings chặn nghiệm thu

### R33-01 — Keystore path và key files chưa được kiểm tra an toàn như report tuyên bố

TrialKeyringAuthority kiểm tra thư mục bằng os.path.isdir/os.stat rồi liệt kê và mở tệp theo path (:1392-1416). Các hàm này đi theo symlink; không có directory descriptor neo, O_NOFOLLOW, fstat regular-file check, UID/owner check hoặc đối chiếu inode trước/sau. Quan trọng hơn, pseudocode không hề kiểm tra mode của từng key file là 0600, dù report tuyên bố đã kiểm tra. Nó chỉ kiểm tra quyền group/other của thư mục bằng st_mode & 0o077.

Một symlink trong keystore có thể khiến authority đọc key từ ngoài thư mục được bảo vệ; key file có quyền quá rộng cũng được chấp nhận. Đây là ranh giới trust anchor của toàn bộ HMAC.

**Yêu cầu:** Mở thư mục keystore bằng descriptor với O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC; xác nhận S_ISDIR, owner UID và mode yêu cầu. Mở từng key/revocation file tương đối bằng openat + O_NOFOLLOW, xác nhận S_ISREG, owner, mode 0600, kích thước hữu hạn và ổn định fstat. Bổ sung symlink, hardlink, owner/mode sai, file growth và path replacement probes.

### R33-02 — Proof verifier chưa xác thực HMAC hoặc allowlist 22 trường

Coordinator.verify_and_consume_proof() gọi normalize_and_validate_readback_proof() (:2095-2129). Nhưng hàm normalizer chỉ kiểm tra mapping, một số chuỗi, bốn số nguyên và timestamp (:1959-2002); không định nghĩa hoặc kiểm tra tập khóa chính xác 22 trường, không kiểm tra primitive types/bounds của các trường còn lại, và không xác minh proof_hmac_sha256. Trong thiết kế, proof_hmac_sha256 chỉ xuất hiện trong các nội dung cũ của fixtures, không có phép tính/đối chiếu HMAC trong entrypoint hiện hành.

Entry point cũng không đối chiếu các field đã có trong proof như action_payload_digest_sha256, source_file_path/source frames, record geometry, proof_nonce và observer identity với authorization/action/pending entry. Tham số action và pending_entry chỉ được dùng một phần; pending digest được kiểm tra nhưng chưa chứng minh action.action_index khớp pending.action_index. Vì vậy outcome có thể được suy ra từ dữ liệu không được xác thực đầy đủ, trái với claim “22-field schema + HMAC”.

**Yêu cầu:** Tạo một normalizer duy nhất với exact 22-key allowlist, type/range gates cho từng field, canonical UTC, proof HMAC verification qua keyring tin cậy, nonce/replay/freshness checks và binding đầy đủ với auth, action, pending entry. Tạo immutable normalized snapshot một lần rồi chỉ dùng snapshot đó sau khi xác minh. Probe mọi trường thiếu/thừa, HMAC sai, mapping thay đổi đồng thời, identity/geometry/source mismatch và nonce replay.

### R33-03 — Ledger writer vẫn nhận signing key và auth từ đối số, capability chỉ được kiểm tra ở caller

DurableConsumedReceiptLedger._record_consumed_evidence() nhận signing_key và auth trực tiếp (:1654-1660), tự ký record bằng signing_key (:1710-1725), nhưng không gọi Coordinator._verify_capability(). Việc Coordinator gọi private method sau khi tự verify (:2160-2167) không làm cho writer này chỉ có thể được gọi qua Coordinator; dấu gạch dưới là quy ước tên, không phải cổng thẩm quyền. Caller truy cập coordinator._durable_ledger vẫn có thể gọi writer trực tiếp với evidence tự tạo và key tự chọn, tạo record/chiếm consume key hoặc làm hỏng trạng thái ledger.

**Yêu cầu:** Ledger không nhận signing key, key resolver, auth hoặc signer identity từ caller. Đưa capability verification và record signing vào một boundary sở hữu authority duy nhất; writer chỉ nhận capability đã xác minh và tự giải quyết key/identity từ Coordinator-owned state. Không tuyên bố private method là ranh giới bảo mật. Thêm probe gọi trực tiếp writer và thay signing key/auth.

### R33-04 — Startup scanner có thể xóa reservation/temp của writer còn sống

recover_and_scan_ledger() chạy lúc Coordinator khởi tạo (:2078-2079), nhưng không có global scan lock hay protocol xác nhận owner đã chết. Scanner xóa mọi tmp_* ngay (:1762-1767) và quarantine reserve_* nếu chưa thấy proof đích (:1768-1779), không đọc hoặc kiểm tra PID/timestamp trong reservation. Một Coordinator thứ hai khởi động trong lúc tiến trình đầu đang ghi sẽ xóa temp hoặc reservation đang hoạt động; sau đó tiến trình khác có thể reserve cùng key và publish cạnh tranh. Điều này phá mutual exclusion mà reservation định cung cấp.

Writer còn bỏ qua lỗi unlink/fsync khi giải phóng reservation (:1751-1756) rồi vẫn trả thành công. Do đó caller có thể được báo consume thành công dù trạng thái giải phóng chưa bền vững.

**Yêu cầu:** Tuần tự hóa startup recovery với writers bằng khóa cấp OS chung; chứng minh reservation không còn owner sống trước khi quarantine; không xóa temp đang được writer giữ. Mọi lỗi unlink/fsync phải giữ trạng thái in-doubt và trả lỗi có cấu trúc, không báo thành công. Thêm hai-process probes: writer đang ở từng barrier trong khi process thứ hai khởi động, crash từng bước, lỗi fsync/unlink và retry.

### R33-05 — Publish và quarantine còn path-based, check-then-act và có thể ghi đè

Writer kiểm tra os.path.exists(dest_path) trước khi lấy reservation (:1665-1679), sau đó publish bằng os.replace với absolute paths (:1744-1749), không dùng src_dir_fd/dst_dir_fd. Scanner cũng dùng os.path.exists, shutil.move và path-based unlink (:1760-1807) dù ledger đã mở self._dir_fd. Việc thay ledger directory sau khi open có thể khiến các syscall path-based thao tác trên directory khác với descriptor đã neo. os.replace còn thay thế bất kỳ destination xuất hiện sau existence check; reservation chỉ bảo vệ các writer cùng tuân thủ protocol, không bảo vệ trước race hoặc file được tạo ngoài protocol.

**Yêu cầu:** Bỏ existence check trước khi mở; dùng descriptor neo duy nhất cho mọi lookup, create, publish, unlink và quarantine. Publish phải có primitive no-replace hoặc protocol bảo đảm atomic uniqueness của destination. Quarantine cần fsync cả directory nguồn và đích, không nuốt lỗi, và xác nhận đối tượng/inode đang chuyển đúng là đối tượng đã kiểm tra.

### R33-06 — DurableProofRecord DTO không khớp writer, serializer hoặc reader

DTO ở Section 2.6 khai báo schema_version, authorization_id, target_project_uid, target_timeline_uid, action_payload_digest_sha256, scanned_frame_start/end, consumed_at_iso, proof_nonce và record_hmac_sha256 (:1597-1634). Nhưng writer tạo DurableProofRecord với proof_id, timeline_uid, scan_start_frame/end, consumed_at_utc, signer_key_id và record_signature_hmac (:1713-1725). Các keyword này không thuộc DTO đã khai báo; ngược lại nhiều trường bắt buộc của DTO không được cung cấp. Reader lại đọc consumed_at_utc, signer_key_id, record_signature_hmac (:1828-1853). Theo pseudocode hiện tại, writer không thể dựng DTO đã khai báo và serializer/reader không dùng cùng schema.

**Yêu cầu:** Chọn một schema version duy nhất cho DurableProofRecord; cập nhật dataclass, canonical serializer, HMAC signed-fields, JSON writer, strict reader, restart repair và fixtures đồng bộ 1:1. Thêm probe round-trip write/read và từ chối thiếu/thừa trường; mọi danh tính authority cần được ký và đối soát khi restart.

## Finding nhất quán tài liệu — Section 2.6 vẫn còn hợp đồng R30 cũ

Section 2.3/R33 khai báo authorization HMAC 32 trường và execution_id được ký. Tuy vậy, Section 2.6 mục 5 vẫn ghi tiêu đề “31-Field Upstream HMAC”, mô tả TrialExecutionAuthorization 31 trường và ánh xạ execution_id = signed_auth.token_nonce (:1062-1078). Đoạn này mâu thuẫn với Section 2.3 mới và Coordinator.bind_signed_authorization() ở :2185-2193, vốn xác thực 32 trường và kiểm tra execution_id == token_nonce.

**Yêu cầu:** Cập nhật hoặc đánh dấu thay thế rõ ràng mọi hợp đồng R30/R31 còn hiệu lực trong Section 2.6. Quét toàn tài liệu để bảo đảm chỉ còn một định nghĩa field count, identity mapping và keyring trust flow.

## Quyết định và bước tiếp theo

R33 **chưa được nghiệm thu**. Đóng R33-01 đến R33-06 cùng finding nhất quán tài liệu; đồng bộ pseudocode DTO, serializer, verifier và recovery scanner để các đường ghi/đọc thực sự cùng một hợp đồng. Cập nhật probe matrix cho symlink/TOCTOU, proof HMAC, caller bypass, concurrent startup recovery, crash barriers và DTO round-trip.

Giữ DESIGN ONLY, ready_to_execute=False, Gate G07 và M1/M2/M3 ở trạng thái OPEN. Không sửa src/ hoặc tests/; không mở adapter/live Resolve hoặc thực thi. Report ghi nhận 5/5 validator suites; review này chỉ xác minh nội dung tĩnh và không chạy lại validator.
