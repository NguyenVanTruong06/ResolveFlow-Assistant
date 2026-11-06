# G06-06A4b-R2 — Codex review, 2026-10-29

Kết luận: **CHANGES_REQUESTED — một residual policy nhỏ**. Giữ kết quả component desktop đã đạt; không mở lại core, không yêu cầu chạy lại desktop. Chưa đóng toàn bộ 06A4b hoặc G06.

## Bằng chứng đã đối chiếu

- Báo cáo được đọc và SHA256 khớp `4f50afb2d9e4f794590a5a644e35170fcc3a5878e3ca66fb9f49291c24dc771e`.
- Codex chạy lại đúng nhóm hữu hạn của R2: **204 passed in 40.87s, exit 0**. Không chạy full suite.
- Source xác nhận positive echo được cho phép khi associate=True, raw SHA256 lấy từ pending, và current/expected old digest được so vô điều kiện trước publish. Các nhánh này đạt review.
- Đã đọc normal/fault metrics, kiểm tra harness và xem ảnh 03/06 tại `.bridge/ux-evidence/G06-06A4b-R2-THEN-AUTO-UI/`. Component xcb DISPLAY=:1 nhận B01 không echo, B02 echo; replacement/no-op và slot guard đạt trong phạm vi harness. Có 3 lô, mới nhận 2/3; không phải project plan hoàn tất.
- Normal exit 0, fault exit 2, metrics báo không còn worker và session được xóa. Fault được kích hoạt **sau** các ca workflow, không chứng minh shutdown giữa worker đang chạy. Bộ đếm cleanup của harness lấy trước bước wait dự phòng; không dùng làm bằng chứng late-worker recovery. Đây là giới hạn bằng chứng, không mở một vòng sửa harness mới.
- Seeded cache và component widget render không chứng minh inference mới, toàn Director hay Resolve mutation. Không suy rộng các invariants quá assertions thực tế của harness; offscreen tests là bằng chứng bổ sung cho Slots 1/2.

## Residual duy nhất phải đóng

`src/ui/director/pages/review_page.py:3380–3388` vẫn khôi phục `allowed_binding_modes` thiếu/sai kiểu từ `pending_expected['expected_binding_mode']`. Workorder R2 yêu cầu thiếu captured policy phải từ chối. Báo cáo “0 fallback” vì vậy chưa đúng hoàn toàn, dù đã bỏ fallback từ result.

Probe Codex chạy actual prepare/intake QThread bằng test positive hai lô, chỉ bỏ `allowed_binding_modes` ngay trước terminal slot; không đổi result, raw hash hay legacy pending field. Luồng vẫn nhận hai lô và giữ positive assertions:

```json
{"intake_accepted_without_required_policy":true,"ledger_entries_after":[1,2],"owned_legacy_mode":"user_associated_current_snapshot"}
```

Artifact: `.bridge/ux-evidence/G06-06A4b-R2-CODEX-REVIEW/pending-policy-probe.json`. Đây là lệch hợp đồng pending nội bộ, không phải bằng chứng người ngoài có thể sửa pending hoặc vượt mọi authority gate.

## Quyết định tiến độ

Giao **G06-06A4b-FINAL-THEN-06A4c-D**: bỏ duy nhất legacy policy fallback, thêm negative test có positive control, chạy nhóm UI hữu hạn. Khi xanh, Worker làm ngay thiết kế bounded project overview/hierarchy trong cùng workorder. Không chờ thêm approval ở giữa, không chạy lại desktop, không đổi core/schema/geometry/Resolve. Codex sẽ đóng intake sau khi nhận bằng chứng residual này đã xanh; thiết kế phần tiếp theo chưa được phép triển khai production.
