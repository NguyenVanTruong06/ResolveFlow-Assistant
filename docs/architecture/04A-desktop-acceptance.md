# G04-04A — Nghiệm thu desktop thư viện và đồng bộ

Trạng thái: ACCEPTED trong phạm vi đồng bộ thư viện desktop. Codex đã chạy44 focused tests, exit0; người dùng xác nhận các luồng desktop dưới đây. Chuyển trang Resolve thật vẫn CHƯA TEST. Đây là gate riêng04A, không đóng toànG04.

Dùng Storage Root thử nghiệm đã có, pack_test_1.zip/pack_test_2.zip hoặc2ZIP nhỏ của bạn. Không cần cài lại model, migration, sửa JSON, rút ổ hay thao tác timeline Resolve. Nếu dùng lại cùngZIP, bản sao độc lập là hành vi duplicate advisory đã chốt; không cần xóa pack cũ để chuẩn bị. Ghi tên pack/asset mới để tránh nhầm bản cũ.

1. **Dock -> Studio đang mở:** mở Studio trước, rồi nhập một ZIP nhỏ từ Dock, commit. Studio tự xuất hiện asset/pack mới mà không bấm Quét/Đồng Bộ; tìm đúng asset, tên/thumbnail/preview metadata không mất nếu pack có. Không cần chèn Resolve. Ghi PASS/FAIL và surface.
2. **Studio đang ẩn:** ẩn Studio, nhập ZIP nhỏ thứ2 từ Dock, mở lại Studio. Asset mới có sẵn, không phải đóng/mở app, không lặp danh sách hoặc nhiều thông báo cùng một thao tác. Lặp ẩn/hiện một lần để xác nhận UI vẫn dùng được.
3. **Studio -> Dock và custom category:** nhập một pack nhỏ từ Studio, gán vào custom category đã có hoặc tạo category mới; commit. Dock tự tìm thấy asset mới, không cần restart/rescan. Đổi category rồi quay lại; nếu Resolve đang mở sẵn thì đổi trang Edit/Color/Fairlight và kiểm tra chip custom còn, không cần sửa timeline. Nếu không mở Resolve thì chỉ kiểm tra hai chiều và ghi phần đổi trang CHƯA TEST, không giả PASS. Khi filter không chứa asset đang chọn, Inspector không tiếp tục thao tác asset ẩn.

Báo PASS/FAIL từng mục, pack/category dùng và thông báo/bước tái hiện nếu lỗi. Chỉ cần ảnh khi lỗi trực quan. Không cần ép lỗi retry bằng dữ liệu thật; các ca Qt slot failure và root switching đã được test tmp/mocks. Không đổi Storage Root thật hoặc rút ổ để làm checklist.

Worker dừng code/test; chưa mở04B/04C và không tự ghi toànG04 ALL_DONE. Giữ mức High nếu sau này cần sửa logic; không có yêu cầu đổi model để chạy checklist này.

## Kết quả người dùng / Worker báo cáo
- Dock -> Studio đang mở: PASS, pack_test_1.zip, B-Roll/chip pack/thumbnail xuất hiện tự động, không Quét/Đồng bộ.
- Studio ẩn -> mở lại: PASS, pack_test_2.zip và pack trước hiển thị, không trùng lặp.
- Studio -> Dock và custom category: PASS phần đồng bộ/phân loại. Chuyển trang Edit/Color/Fairlight trong Resolve: CHƯA TEST do Resolve không mở.

Codex đối chiếu .bridge/report.md và xác nhận nghiệm thu 04A trong phạm vi này dựa bằng chứng do người dùng cung cấp. Không tự chạy lại desktop hoặc pytest trong lượt đóng gate; số44test là kết quả Codex đã xác minh ở lượt reviewR2. Phần chuyển trang Resolve thật giữ checklist nghiệm thu G08/G09, mở lại sửa chỉ khi có lỗi tái hiện.
