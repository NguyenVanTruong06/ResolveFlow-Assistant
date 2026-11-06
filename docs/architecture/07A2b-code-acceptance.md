# G07-07A2b — Nghiệm thu code và giới hạn bằng chứng giao diện

> Cập nhật2026-11-02: Evidence closure đã nghiệm thu trong phạm vi component tại 07A2b-gate-acceptance.md. Nội dung pending bên dưới là lịch sử trước closure.

2026-11-02. CODE_ACCEPTED_WITH_RECORDED_LIMITS; EVIDENCE_CLOSURE_PENDING. Không sửa tiếp production07A2b; chưa đóng toànG07 hoặc xác nhận physical desktop acceptance.

Report SHA-256: 9997077deb47e2b594bada5a07736b868c965ce5165d31215c4b0ebf863c0452 đã khớp.
Codex chạy đúng6files: 68 passed /17.11s /exit0. Hai residuals cached-reopen và own-finished-handler cleanup chạy lại độc lập đạt; pending/duplicate/Cancel/drop/admission/queryretention giữ xanh. Backend hashes frozen; BuildPage không đổi.

Evidence độc lập: .bridge/ux-evidence/G07-07A2b-R2-CODEX-REVIEW/ (pytest_run.log,code-residual-probes.json,prior-probes.json,harness-probes.json). Code đạt scoped readonly Step4 inspection +physical ownership through close/Quit; ready_to_execute=False,0Resolve.

## Phân loại ảnh và dữ liệu
Ảnh đã xem: d1_1200x750.png là Step1; d1_checked.png là Step4 ở1280x800. WIDGET_RENDER là component rendering evidence, không physical screen capture. Summary 'Chưa chọn clip' trong khi table1source cho thấy fixture seed chưa đồng bộ; chưa có bằng chứng đó là bug app.

## Chỉ còn harness/evidence closure
1. check_containment dùng contains OR intersects: partial child vẫnTrue (Codex probe xác nhận). Cần fullcontainment/hit-test, đúngStep4 ởhai kích thước, cảCancel khi busy.
2. Fault dùng lại barrier_d3 đãset: native fault worker đãfinished trước exception (Codex actualQThread probe xác nhận). Dùng fresh unset Event,assert held vàisRunning ngay trước raise.
3. Cleanup wait2000 rồi clearregistry, sau đó đếm lenregistry là self-fulfilling. Cần independent owned worker refs/native finished evidence; chưaphysicalidle thì giữowner/session.
4. Normal/fault ghi chung metrics/ảnh; chỉ còn normalmetrics. Tách per-mode outputs; path/settings/source fixture routing phải được kiểm chứng, không hardcode untouched như phépđo.

Next task fixes harness/tests/docs only rồi thiết kế07A3; src frozen. Content identity/CFR/target/dry-run/execution/recovery chưa được chứng minh bởi availability.
