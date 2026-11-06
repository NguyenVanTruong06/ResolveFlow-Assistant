# G06-06A3-R2 — Codex review, 2026-10-28

**CHANGES_REQUESTED: G06-06A3-R3 (binding gate nhỏ, không redesign).** Fresh finite6files **226passed in49.20s, exit0**. Probe xác nhận DTO invalid baseline/cap bị reject và prompt tamper bị reject; giữ nguyên các sửa R2 đã đạt.

Residual duy nhất còn chặn nghiệm thu là **expected bindings chưa bắt buộc ở cả worker lẫn terminal**:

- Worker copy checks `if req.expected_snapshot_id is not None ...` và tương tự batch/hash. Một snapshot hợp lệ nhưng request thiếu toàn bộ expected bindings vẫn success. R2 đã yêu cầu copy expectation độc lập là bắt buộc, không optional fallback.
- Qt terminal chỉ so current snapshot với expected và một số fields. Đổi result.generation=-99, result.snapshot_id='OTHER' hoặc result.manifest_id='OTHER' trong một result hợp lệ khác vẫn ghi VERIFIED_BATCH vào clipboard.
- Nếu `_pending_prompt_expected=None` nhưng active intent copy_batch, fallback `_active_prompt_intent` cùng các `if pending_expected` bỏ qua toàn bộ content checks. Probe prompt='UNBOUND_TEXT' được ghi clipboard.

Nguyên nhân chung: nhiều optional checks tạo fail-open khi thiếu ownership. R3 dùng một required binding gate cho batch success, không thêm crypto/DTO framework hoặc đổi A1/A2. Không đổi behavior V1 errors/cancel; failed terminal có thể không có complete success payload nhưng phải thuộc owned request và vẫn physical cleanup đúng.

Evidence .bridge/ux-evidence/G06-06A3-R2-CODEX-REVIEW/. Không sửa production/tests trong review, không desktop/model/Resolve/shutdown. Sau R3 đạt mới giao desktop smoke.
