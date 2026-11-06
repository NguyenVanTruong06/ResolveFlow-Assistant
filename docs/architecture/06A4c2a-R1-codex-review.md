# G06-06A4c2a-R1 — Codex review và grouped integration, 2026-10-29

Report SHA256 đã đọc: `0218204e4ea522ecfb783971003f78d3b4ab2a940cef65d60107131a97d685b2`.

**Kết luận:** các nhóm residual R1 đã được kiểm tra đạt trong phạm vi backend fixtures. **Chưa công bố worker production-ready hoặc đóng consumer** vì còn một lỗi tương thích với producer thật. Gộp sửa compatibility vào task UI/guards tiếp theo, không tạo vòng R2 riêng.

## Verification

Codex chạy bốn file finite: **105 passed in 2.39s, exit 0**. Đọc source và các regressions mới: required proof không fallback, exact style và budget ba chiều, strict ledger ID/count/overflow, required result budget, context ownership, post-format source mutation/cancel/exception hooks. Các helper và loops này giữ hướng thiết kế đã chốt; không thay pure core.

Test post-render mutation lần này gọi formatter thật trước khi phát hiện file đổi, khác với test pre-change trước đó. Có positive real-QThread và metadata fixture controls, không suy ra desktop/inference mới.

## Integration blocker được tái hiện bằng producer thật

`_freeze_context_signature` áp nonblank policy đệ quy cho mọi string/container. Nhưng `ReviewPage._extract_context_signature` trả tuple16 hợp lệ có `vision_model=''` khi visual model không chọn/visual disabled; empty endpoint cũng có thể xuất hiện. Đây là field data hợp lệ, không phải proof bị thiếu.

Codex gọi chính producer này với context chuẩn, rồi dựng `PromptPreparationRequest(copy_project_overview, expected_ui_context_signature=signature)`:

```json
{"producer":"ReviewPage._extract_context_signature","signature_length":16,"has_valid_blank_vision_model":true,"constructor_accepted":false,"error":"Context signature chuỗi không được rỗng hoặc chỉ có khoảng trắng"}
```

Artifact: `.bridge/ux-evidence/G06-06A4c2a-R1-CODEX-REVIEW/production-signature-probe.json`. Root proof vẫn phải required; nested empty data phải được giữ lossless thay vì coi thiếu toàn proof. Không bypass gate bằng signature giả/nonblank hoặc đổi Source settings defaults để né lỗi.

## Quyết định gộp công việc

**G06-06A4c2b-I — Signature compatibility + overview UI + macro Slot guards + finite desktop smoke**, tối đa ba production modules: worker, ReviewPage, DirectorState. A sửa compatibility và test actual producer; B nối action/pending/owned artifact/lifecycle; C chặn macro ở parser/direct boundaries; D finite offscreen suite rồi một smoke desktop component nếu xanh. Một report cuối, không gửi riêng từng phần và không chờ Codex giữa các phần đã được giao.

Đây là scope gộp theo yêu cầu user giảm vòng lặp, không mở matcher/EditPlan/persistence hoặc sửa core formatter. Sau hoàn thành còn cần review integrated evidence; toàn G06 vẫn mở.
