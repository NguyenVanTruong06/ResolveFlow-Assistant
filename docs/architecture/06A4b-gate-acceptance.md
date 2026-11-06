# 06A4b — Nghiệm thu dedicated batch draft intake, 2026-10-29

**ACCEPTED_WITH_RECORDED_LIMITS** trong phạm vi intake RAM của snapshot/lô. Không phải nghiệm thu toàn G06 hoặc project master plan.

Report cuối được đọc và SHA256 khớp `361f8bf4920bada73356a12e2fc3d814d44b1a0173b2b4b7ccda928b815452d7`.

## Cơ sở nghiệm thu

- Source `review_page.py:3380–3391` yêu cầu tuple policy không rỗng và enum hợp lệ, không đọc legacy field để fallback. Positive echo vẫn được cho phép đúng captured policy.
- Test `test_review_page_intake_invalid_binding_policy_rejected_zero_helper` có positive control gọi helper thật và 8 ca policy invalid, giữ legacy mode hợp lệ để chứng minh gate cần kiểm tra. Ca invalid dừng trước helper và không publish.
- Codex chạy mới `tests/ui/test_batch_draft_intake.py tests/ui/test_review_page_batch_prompt.py`: **124 passed in 4.69s, exit 0**. Không chạy full repository suite. Con số 212 trong report là claim nhóm kiểm thử mở rộng của Worker, không thay thế kết quả kiểm tra mới này và không gọi là full repo validation.
- Kế thừa review core/geometry và R2: exact raw proof, context/envelope binding, source ranges, positive echo, no-op sau integrity, replacement opt-in và chống deleted-entry resurrection. Không mở lại các hợp đồng đã đạt.
- Component desktop của R2 được giữ trong giới hạn tại `06A4b-R2-codex-review.md`: 3 lô chuẩn bị, 2 lô được nhận, replacement và generic slot guard; seeded cache, không inference mới. Fault sau workflow không chứng minh fault giữa active worker. Không chạy lại desktop cho thay đổi policy nội bộ.

## Giới hạn và phần tiếp theo

Ledger vẫn chỉ RAM, key `(snapshot_id, batch_id)`, giữ entries snapshot cũ nhưng lookup theo snapshot hiện tại. Chưa có durable resume, project synthesis, offline matching, EditPlan assembly hoặc Resolve mutation. “Đã nhận bản thảo” không có nghĩa video/AI hiểu đủ nguồn.

Bước tiếp theo: thiết kế 06A4c được duyệt có binding corrections tại `06A4c-codex-decisions.md`. Chỉ triển khai pure core formatter trước; chưa UI/worker/clipboard route. Publication `PUBLISHED_VERIFIED` và hai backup ngoài app giữ nguyên.
