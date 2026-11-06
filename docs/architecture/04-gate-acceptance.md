# G04 — Nghiệm thu gate (2026-10-27)

Trạng thái: ACCEPTED_WITH_RECORDED_LIMITS. G04 đóng trong phạm vi đã giao; toàn bộ roadmap chưa ALL_DONE.

## Bằng chứng cuối cùng
Codex đối chiếu source `src/core/resolve_bridge.py`: carrier Title truyền endFrame=dur. Chạy lại đúng ba file test core bridge, service text và UI text: 44 passed in 5.46s, exit 0. Không chạy full suite; không cộng số test giữa các vòng vì có giao nhau.

Đã xem ảnh Resolve thật ` .bridge/ux-evidence/G04-04C11-FINAL-R1/02_resolve_viewer_rendered_72frames.png` và đối chiếu verification_metrics.json cùng báo cáo Worker. Viewer hiển thị rõ “ChunDVC — Kiểm tra Title”, không có cảnh báo font trong ảnh. Project sở hữu ChunDVC_G04_Acceptance_1793037559, timeline Timeline_TitleFinal_1793060151; 24fps, start=86400, end=86472, duration=72 frame đúng 3s. Font thử nghiệm Liberation Sans Bold; MediaOut được đọc kiểm chứng, không sửa node bằng harness để tạo PASS. Đây là bằng chứng live của Worker được Codex review, không phải Codex tự chạy lại Resolve trong phiên review.

Các ca live trước giữ nghiệm thu theo phạm vi đã ghi: một dispatch/anti-duplicate/Recent, Undo theo exact UID và từ chối Undo lần hai, guidance .setting và staging không mutation. Không chạy lại toàn bộ checklist.

## Phạm vi đã nghiệm thu
- Đồng bộ managed library Dock/Studio, taxonomy tùy biến, phục hồi filter/selection và search không mất kết quả thứ 51 trở đi.
- Guard ở insert/drag/preview/export, giữ metadata nguồn, không fallback file repo hoặc tạo media giả khi thiếu quyền/file.
- Preview grid và Inspector video/audio: explicit action, lazy player, lifecycle/session callbacks và arbitration theo các test và component desktop đã ghi.
- Studio media đi qua service/gateway; Title cơ bản async, identity/Undo và thời lượng/render live trong cấu hình đã kiểm chứng.

Lịch sử chi tiết và giới hạn từng phần nằm trong các review 04A/04B/04C hiện hành. Nghiệm thu component không thay thế kiểm thử toàn ứng dụng.

## Backlog giữ nguyên, không bỏ yêu cầu
| Hạng mục | Gate chịu trách nhiệm | Giới hạn hiện tại |
|---|---|---|
| Whisper + hình ảnh + tái dùng cache nguồn | G05 | Giao thiết kế tiếp; chưa chứng minh batch 3 giờ |
| Matching và recipe/edit-plan | G06 | Chưa nghiệm thu end-to-end |
| Full Text styling, stroke/curve, font family/style availability; FastPaste positive; cài template lên đĩa | G07 | Title cơ bản đã đạt; chưa chứng minh mọi font/kiểu; CopyFusion không chứng minh focus/OS paste |
| Relink/migration/recovery/journal/sharding còn thiếu | G02 residual, lập lịch trước consumer cần nó ở G05–G07 | Chưa coi là đã triển khai; không tự mở rộng task đang chạy |
| Shell/focus, trang Resolve, hotkey/anchor và lifecycle liên cửa sổ | G08 | Một số kiểm tra mới ở component |
| Full main.py workflow, native OS drop, hotplug thực, physical audio, Windows/Wayland, batch/performance/package | G09 | Không suy luận PASS từ offscreen hoặc PlayingState |

Chỉ mở lại G04 nếu có bug tái hiện hoặc contract downstream bị ảnh hưởng. Không sinh thêm tính năng G04 để trì hoãn G05. Worker HIGH; không tắt máy, chưa ALL_DONE.
