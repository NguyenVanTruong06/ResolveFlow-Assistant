# ChunDVC — Kiểm tra desktop G03

Trạng thái: ACCEPTED — 4/4 desktop checks PASS theo báo cáo trực tiếp của người dùng và Worker; Codex không tự chạy lại desktop.
Chỉ dùng một Storage Root thử nghiệm và 2 ZIP nhỏ chứa media hợp lệ của bạn (PNG/WAV/clip ngắn); không cần thao tác timeline Resolve, rút ổ hay làm đầy đĩa.

1. **Wizard và hủy category draft:** kéo một ZIP vào Dock. Tạo category ID mới chưa từng dùng (ví dụ test_cancel_01), rồi Hủy. Mở lại wizard: category đó không được xuất hiện trong danh mục đã lưu. Nút nhập/giao diện vẫn dùng được.
2. **Commit category:** tạo category ID khác (test_commit_01), gán cho một asset, bấm Nhập. Xác nhận thông báo thành công, pack/asset được làm mới và category mới có trong combo ở lần mở wizard tiếp theo. Nếu category chưa có trên rail thư viện, báo riêng để xử lý ở G04; không suy rằng metadata đã mất.
3. **Pack trùng:** nhập lại cùng ZIP. Cảnh báo cùng tên/version cần xuất hiện ở vùng review; nút Xem cảnh báo mở nội dung đọc được. Có thể Hủy để không tạo thêm bản sao. Không được tự ghi đè/xóa pack trước.
4. **Queue partial:** kéo đồng thời 2 ZIP khác nhau, nhập gói1 rồi Hủy gói2. Chỉ gói1 được nhập; Dock báo1/2 và dừng, guard/nút kéo thả không bị kẹt. Kiểm tra thêm một lần mở Studio/Hub để thấy dữ liệu đã refresh.

Báo lại mỗi mục PASS/FAIL, tên surface, thông báo thực tế và các bước tái hiện nếu lỗi. Ảnh chỉ cần khi có vấn đề hiển thị.
Không cần tái hiện lỗi snapshot ENOSPC bằng ổ thật; nhánh này đã kiểm thử mock. Nếu tiện dùng một pack Google Drive công khai nhỏ thì ghi riêng kết quả, không phải điều kiện bắt buộc của4mục trên.
Chưa xác minh: physical unplug/powerloss, Drive auth/quota/CAPTCHA thật, Studio telemetry worker teardown, Resolve insertion. Những giới hạn này không được ghi thành đã kiểm thử.

## Kết quả nghiệm thu Gate G03
Người dùng và Worker xác nhận với hai ZIP thực tế:
- `/media/vanntruongg/Ventoy/pack_test_1.zip`
- `/media/vanntruongg/Ventoy/pack_test_2.zip`

| Mục | Kết quả được báo cáo |
|---|---|
| Hủy draft | PASS: test_cancel_01 không lưu taxonomy.json |
| Commit category | PASS: test_commit_01 được commit vào kho |
| Pack trùng | PASS: cảnh báo bản sao độc lập, không tự ghi đè/xóa |
| Queue partial | PASS: commit gói 1/hủy gói 2, Dock báo 1/2, dừng queue và reset guard |

Codex chấp nhận bằng chứng nghiệm thu desktop do người dùng cung cấp, đóng Gate G03 trong phạm vi đã giao. Không suy rộng sang các giới hạn chưa xác minh ở trên.
