# G07-07A1 — Nghiệm thu cổng phê duyệt hiện hành

Ngày 2026-11-02. **ACCEPTED_WITH_RECORDED_LIMITS** cho 07A1, chưa đóng G07.

Báo cáo đã đối chiếu SHA-256: `f5b0dd928bd8a6e856b32aa2ee6a055702dc3351cdf2184f118aae2e7ba0c909`.
Codex chạy lại đúng ba file test: **43 passed / 16.46s / exit 0**. Probe độc lập chạy lại exit 0; bằng chứng ở `.bridge/ux-evidence/G07-07A1-R1-CODEX-REVIEW/` (review-evidence.json, probes.json, probe_execution.log, probe_review.py).

- Positive thực: S3 tạo proposal → user approval → owned getter → Director context → vào Step 4.
- Getter trả None an toàn khi typed payload/required approval member hỏng; giữ drafts.
- Lý do từ chối/chưa khả dụng hiển thị qua nhãn và tooltip.
- Worker state query lỗi giữ worker, từ chối dispatch. Thiếu LLM worker trả thất bại trung thực, JSON rỗng.
- Director phase 0/2 vẫn khóa; không thay manual insert. Phê duyệt nội dung không cấp quyền thi công.

Giới hạn: bằng chứng mới là focused/offscreen; chưa chứng minh readiness nguồn/catalog/CFR, timeline execution hoặc recovery. Không tuyên bố G07 hoàn tất.

Thiết kế 07A2 được chấp thuận có ràng buộc tại [07A2-codex-decisions.md](07A2-codex-decisions.md). Lát cắt kế tiếp chỉ backend inspection, không bật Build/UI tự động.
