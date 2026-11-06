# Codex review — G00-H1-R2 / G01-D

## A. Partial toast
Call show_message đúng API. Probe trực tiếp widget/service/registry trên dữ liệu tạm: nguồn1commit, nguồn2cancel → processed2, registry.packs1, toast “Đã nhập1/2gói (đã dừng hàng đợi).” Phần sửa production được chấp nhận.
Tuy nhiên Codex chạy node test_commit_pack1_then_cancel_pack2_reports_partial trong phiên này FAILED, exit1, processed chỉ1 thay vì2 (4.64s). Test helper chờ cố định100×20ms, lỗi assert bên trong exec bị handler ngoài bắt; chưa chứng minh nguyên nhân chính xác. Probe dùng processEvents+sleep nhỏ+deadline và đợi job/worker hoàn tất chạy đúng. Cần ổn định test và đưa lỗi thật ra assertion, không được bỏ qua fail hoặc tăng timeout mù.

## B. G01-D chưa phê duyệt triển khai
1. Inventory có keys/APIs không tồn tại: audio_hover_preview, linked_directories, speed_mode không đúng keys thực; insert_asset()/map_cuts_to_timeline() và InsertResult.success/inserted_items không đúng public API hiện hành.
2. Asset contract bắt tất cả ID hex32/pack_id/library_path làm hỏng builtins/linked. SourceFootage identity mới từ path/mtime chưa phân biệt ID với revision và khác ClipManifest hiện tại.
3. TypedDict chỉ là typing, không tự validate hoặc loại _raw_obj khỏi json.dumps. total=False không bảo đảm trường bắt buộc. Claim10kassets<50ms/100%compat không có benchmark.
4. analysis_status thiếu partial/needs_review/cancelled, có nguy cơ mất bằng chứng thị giác. Unknown duration có0/None/-1 mâu thuẫn. Ép mọi source timestamp thành integer frame sẽ làm mất PTS/VFR; fps float23.976 không thay thế24000/1001.
5. normalize_taxonomy hiện NFC+strip+lower, không bỏ dấu/ký tự đặc biệt. Không gộp search folding với canonical identity hoặc làm custom IDs va chạm.
6. Thiếu JSON fixtures và migration/version failure policy cụ thể; slice01A chưa chỉ ra module chứa DTO/serialization adapter;01B bám API không tồn tại nên chưa thực thi được.

Quyết định: giữ TypedDict+functional adapters là hướng có thể chọn, chưa chốt implementation. Giao G01-D-R1 sửa đúng3tài liệu và1helpertest; không viết schema hoặc đổi QSettings production. G00 hotfix production đóng, test stability còn theo dõi ởR1.
