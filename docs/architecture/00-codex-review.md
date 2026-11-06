# Codex review — G00-D

Kết luận: chấp nhận bản đồ khảo sát để tiếp tục, với các điều chỉnh dưới đây. G00 không phải nghiệm thu runtime vì Worker không chạy app/test.

## Phát hiện được đối chiếu code
- EdgeDockWindow.dropEvent hiện gọi PackManager.install_pack(fn) không chỉ định StorageRoot: đúng, cần sửa sớm.
- TabAssets._import_files_or_folders (tên thật, không phải _on_import_files_or_folders) vẫn copy vào assets và một số nhánh còn copy sang thư mục DaVinci: đúng. Generic import phải đi qua review; thao tác cài Effects rõ ràng là luồng riêng.
- INSTALLED_PACKS_FILE phụ thuộc cwd: đúng về code. Xếp nợ tương thích/P1; chưa có bằng chứng mất dữ liệu để coi độc lập là P0. Không xóa file hoặc bỏ API legacy trong hotfix UI.

## Những khẳng định cần thu hẹp
- G00 chỉ chứng minh CODE_VERIFIED/CODE_ONLY, không chứng minh chạy GPU, đổi thiết bị, đồng bộ UI hoặc testpass hiện tại. Số liệu cũ phải ghi HISTORICAL.
- Có nhiều QSettings domain không chứng minh mất config; G01 cần inventory keys + migration/read fallback, không thay domain hàng loạt.
- Nhiều asset types có adapters là hiện trạng, không tự động là bug. StudioAsset là class thường, không phải dataclass. Cần xác định nơi thật sự vi phạm contract trước khi chọn schema mới.
- Category storage số nhiều/UI số ít đã có adapter StorageManager.ui_assets. Chuẩn hóa là mục tiêu thiết kế, chưa được chứng minh là lỗi lọc hiện hành.
- refresh_storage dùng QTimer.start(2500), không phải QTimer.singleShot(2500,...).
- Dòng tham chiếu trong audit có chỗ lệch; dùng tên hàm và kiểm lại rg khi triển khai.

## Quyết định và thứ tự
G00-D đóng ở mức khảo sát. Ưu tiên G00-H1-I: đóng hai đường import UI ghi sai nơi, dựa trên thiết kế kho/wizard đã triển khai trong docs/multi-tier-storage.md. Không cần tạo framework/schema mới để nối đường gọi đúng.
Sau hotfix được review mới giao G01-D; chưa giao G01-I, chưa phê duyệt tên src/core/schema.py hoặc migration QSettings.
G02/G03 vẫn chịu trách nhiệm transaction recovery và chính sách API legacy, không bị coi là đã hoàn tất nhờ hotfix này.
