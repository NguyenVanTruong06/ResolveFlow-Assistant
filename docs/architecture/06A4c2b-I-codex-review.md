# G06-06A4c2b-I — Grouped review, 2026-10-29

Report SHA256 đọc/đối chiếu: `36059b4ed21dfda09d27da65a717d60ad4021fb84adb65310d32a93d467ba406`.

**MAIN FLOW / COMPONENT EVIDENCE VERIFIED; UI boundary changes requested.** Không mở lại core/worker/macros đã đạt. Còn một nhóm validation ở API UI cần đóng trước chốt consumer. Gộp sửa nhóm này và thiết kế 06B trong workorder kế tiếp, không lặp desktop normal suite.

## Bằng chứng đã xác minh

- Codex chạy đúng sáu file: **141 passed in 60.63s, exit 0**. Đã đọc source UI/worker/guard và các tests mới. Bảng counts 6+11+35+52+20+17 khớp141.
- Compatibility dùng production tuple16/blank vision model đã sửa. Explicit action dispatch worker thật, lấy artifact riêng và so content transport với artifact; source pre/post và signature/counters kế thừa worker tests.
- Macro guard có bounded traversal/DAG/cycle, parser trước forced slots và direct Review/config guard; không dựa tên field chung mơ hồ như macro_strategy_summary để chặn mọi plan. Giữ dữ liệu V1/V2 trong các ca đã kiểm.
- Đọc normal/fault metrics, harness và xem `04_overview_copy_success.png`: component có nút đủ chữ, actual prepare3 batches/intake1 draft/copy5442 bytes, label1/3. Macro deny và stale zero-copy có metrics/ảnh riêng. Normal0/fault2, actual cleanup counters0 và owned session removed.
- Đây là WIDGET_RENDER/component, seeded scan cache, không speech/vision inference mới hoặc full Director/Resolve verification. Fault branch gọi start request rồi raise, chưa có barrier/assert worker đang active đúng thời điểm raise; chỉ ghi nhận cleanup sau yêu cầu start, không nâng thành chứng minh mọi in-flight exception schedule.

## UI preflight chưa controlled reject

`request_prompt_preparation` nhánh overview dựng/sort entries và gọi DTO/signature helper ngoài exception boundary. Probe dùng valid snapshot/context từ test production-signature fixture, chỉ thay ledger entry:

```json
{
  "malformed_counter": {"raised":"ValueError","generation_delta":0,"worker_count":0},
  "malformed_entry": {"raised":"AttributeError","generation_delta":0,"worker_count":0}
}
```

Counter là `total_selected_duration_sec=True`; entry còn lại là `object()`. Đây là corrupted RAM/API input, không phải bypass thành công của external JSON hay mất dữ liệu. Nhưng yêu cầu rõ của workorder là invalid trả None/no dispatch/no generation delta và không exception thoát callback UI. Hai ca hiện chưa đạt. `_update_overview_button_state` và terminal re-collect cũng cần dùng cùng controlled capture thay vì sort/unpack trước type gate.

Artifact: `.bridge/ux-evidence/G06-06A4c2b-I-CODEX-REVIEW/preflight-probes.json`; probe process exit0, không worker/source/clipboard mutation ngoài tmp fixture.

## Phạm vi đóng checklist cuối

Giao **G06-06A4c2b-R1-THEN-06B-D**: production chỉ ReviewPage để controlled preflight/capture, complete pending/artifact rejection tests trong UI boundary đã giao (không tạo trust framework mới). Giữ positive desktop evidence và core/worker/macros; test ba file targeted cần thiết. Khi xanh, làm ngay 06B design-only offline matching từ StorageManager catalog thật, không đổi media/kho hoặc tự tải ZIP backup. Một report cuối.
