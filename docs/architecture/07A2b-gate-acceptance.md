# G07-07A2b — Nghiệm thu scoped UI + component evidence

2026-11-02. ACCEPTED_WITH_RECORDED_LIMITS. Đóng07A2b; chưa đóngG07 và chưa có timeline execution.
Report SHA-256 86d7be5232fb7b2955f3652d395538aa85d02eaacde993f606f1b25e0f02de70 đã khớp.

## Bằng chứng
- Code đã nghiệm thu: Codex68focused producttests/17.11s/exit0 từ review trước; hai residual cachedreopen/targeteddisconnection đã độc lập xác nhận. Src hashes vẫn nguyên.
- Lượt này Codex chạy lại tests/task/test_desktop_verify_07a2b.py: 8passed/6.66s/exit0. Không chạy lại suite sản phẩm vì src frozen.
- Đã đối chiếu distinct normal/fault/early-failure metrics và source harness. Normal0 vớiD1–D4; fault2 với activefault1worker vàcleanup; earlyfailure1 không nhận vơ activefault. Cả3mode running_after0,cleanup_okTrue,session_removedTrue,owned_count==native_finished_count.
- Ảnh Step4 1200x750 đã xem: đúngStep4,summary1clip khớp source,controls/readiness table rõ. Ảnh đúngWIDGET_RENDER,physical_screen_verified=False; source/harness/metrics ở .bridge/ux-evidence/G07-07A2b-EVIDENCE-FINAL/.
- Fresh review/evaluation ở .bridge/ux-evidence/G07-07A2b-EVIDENCE-CODEX-REVIEW/.

## Giới hạn
Component Qt/Xcb verification,không physicalscreen/human acceptance. D3harness chứng minh controlled Cancel/lifecycle; staleapproval còn dựa focusedoffscreenR04/R06,không nâng thành desktopstaleproof riêng. source_data_untouched trongmetrics là khai báo harness,isolation pathrouting đãreview,không phải hash inventory toànuserdisk. Không benchmark sốlượnglớn/crossplatform.
ready_to_execute luônFalse. Availability không là fullcontent/CFR/target/executionproof. Phase0/2 vẫnkhóa;manualinsertđộclập. Không reopen UI code trừ defect có bằngchứng liênquan.
Next: 07A3 thiết kế source/content/frame evidence cần corrections trước implementation; xem07A3-codex-decisions.md.
