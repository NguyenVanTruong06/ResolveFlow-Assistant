# Codex review — G01-01B2-M-I

## Quyết định
ACCEPTED phần số học source mapping. Codex chạy lại:
`QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/core/contracts/test_time_contracts.py tests/test_timeline_mapper.py -q`
Kết quả: 37 passed in 0.80s, exit 0.

Đã đối chiếu src_to_timeline: dùng pts_to_frame với FPS phân số hiện hữu; giữ nguyên guard 1ms, padding, công thức speed offset và rec_in. Không xác minh Resolve thật/VFR; không chạy lại bộ DTO 61 test do không thuộc thay đổi này.

Điểm cần đóng ở bước tiếp: annotation mới quảng cáo str nhưng guard so sánh str với float gây TypeError. Bỏ str khỏi annotation, không thêm coercion/range policy mới. Không mở lại phần lượng tử hóa đã đạt.
Test speed mới thực tế chỉ exercise 2.0; mô tả “đã test 1.5” cần đối chiếu với test hiện hữu hoặc hạ tuyên bố. Test purity mới kiểm tra một số trường, không được gọi đó là bằng chứng snapshot toàn bộ state.

Hai docs 01B còn thông tin cũ: API apply_lut_to_playhead_clip không tồn tại, bảng vẫn đề xuất InsertResult.status và đổi legacy LUT error thành failed dù quyết định Codex đã hoãn/cấm. Giao G01-01B3-S-D để sửa docs và chốt semantics trước khi triển khai status.
