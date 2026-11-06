# Kho xuất bản ChunDVC — quyết định Codex 2026-10-29

## Thẩm quyền và thứ tự công việc
Người dùng yêu cầu sửa phân biệt master/preview, chuẩn hóa kho để tải online và cho phép Antigravity thực hiện trên Google Drive. Đây là ưu tiên mới; G06-06A4b-I được giữ checkpoint, chưa nghiệm thu/không hủy. Không sửa hai luồng cùng lúc. Worker HIGH.

Lộ trình hữu hạn: LIB-PUBLISH-01-I (attachment contract + round-trip pack nhỏ + inventory) → Codex review → LIB-PUBLISH-02 (xuất các pack thật, kiểm tra ZIP/import, upload và readback). Không thêm vòng design riêng; không thay thế toàn bộ hệ thống thư viện. Chưa cho phép đổi/xóa/gộp kho nguồn hàng loạt.

## Nguồn đã kiểm tra
- pack_ingest.py: guess mặc định broll; inspect_tree tạo record cho từng file; inspect_job chưa nhận liên kết preview/thumbnail; commit chỉ giữ chosen masters + manifest và xóa file khác trong payload.
- asset_dto.py: persisted allowlist chưa chứa các đường dẫn companion. UI có preview_path/thumbnail_path.
- storage_manager.py: assets dựng file_path từ library_path nhưng chưa dựng companion path.
- asset_indexer.py: bundled transitions ghép .setting với PNG/MP4 theo pack/stem; không tương đương generic pack ingestion.
- Repo có assets/templates/transitions: 2024 MP4, 979 setting; assets/luts:47 cube. Đây là số file khảo sát, không phải số asset độc nhất hoặc checksum-dedup.
- Kho app cấu hình hiện tại: /media/vanntruongg/Ventoy/ChunDVC_Library_Test; 2 pack test/14 assets tại thời điểm khảo sát. Tuyệt đối không tự thay storage.json/registry thật.

## Contract bổ sung, tương thích schema v1
Persist optional preview_relative_path và thumbnail_relative_path: đường dẫn POSIX tương đối với GỐC PACK, không phải thư mục master. Giữ relative_path của master và library_path do commit tạo. Các trường companion không phải runtime absolute paths.
Manifest nhận canonical fields trên; alias preview_path/thumbnail_path chỉ tương thích nếu là relative path hợp lệ. Hai dạng cùng hiện diện phải bằng nhau; khác nhau/sai kiểu/absolute/escape phải từ chối trước publish. Không tin file_path, library_path, origin_root hoặc external URL do manifest gửi.
Companions là phụ thuộc của master, không phải asset độc lập. Đường dẫn preview/thumbnail hợp lệ không tự cấp quyền timeline insert/drag. preview chỉ chạy qua guard hiện hữu. Template trans vẫn native guidance, không carrier/video giả.
Reserved previews/, thumbnails/ và tutorials/docs không tự trở thành B-roll khi không có manifest hợp lệ. Không dựa riêng vào tên đuôi hoặc tên hiệu ứng để chứng minh quan hệ. Dữ liệu không rõ phân loại phải được báo trong inventory, không đoán thành stock footage.

## Thư mục xuất bản và Drive
Thư mục local đã chọn: /media/vanntruongg/Ventoy/ChunDVC_Publish/; mỗi run có thư mục riêng runs/<timestamp_uuid>/{packs,zip,reports}. Không nằm trong StorageRoot hoặc repo assets/. Kiểm tra mount, dung lượng và ownership trước tạo/copy; ổ mất thì dừng, không fallback về home/tmp. Không đổi storage root. Nguồn chỉ đọc.
Đích Drive người dùng cung cấp: https://drive.google.com/drive/folders/1b6zuDHXoCRXBfzI-Q18weYZ2hsNsd1Hq ; ID 1b6zuDHXoCRXBfzI-Q18weYZ2hsNsd1Hq, tên đọc được “Kho Tài Nguyên ChunnDVC”. Codex xác minh đọc/list được; connector chưa trả bằng chứng canEdit/canAddChildren, chưa upload.
Root đang có 8 nhóm thư mục, trong đó 03_Chuyen_Canh_Transitions và 04_Fusion_Macros_DaVinci chứa ZIP gốc. Giữ nguyên các ID/link/quyền chia sẻ và nội dung này. Không động 07_Plugin_Tools_3D hoặc 08_Premiere_Pro_Projects như thể là asset ChunDVC.
Sau gate local, tạo thư mục release mới bên trong đúng root, upload ZIP mới; không ghi đè/xóa bản gốc. Có checkpoint upload theo SHA256/file ID để resume không nhân bản. Không tự bật public sharing. Readback metadata + tải lại/so checksum khi cần trước ghi PUBLISHED. Lỗi quyền/mất connector thì LOCAL_READY/UPLOAD_BLOCKED, không báo upload giả và không xin mật khẩu/token.

## Giới hạn
Đóng gói JSON/ZIP không chứng minh native transition render được hoặc có quyền phân phối. Bảo toàn license/attribution sẵn có và dependencies của pack, không tự công bố thành public. Kho empty/ambiguous/duplicate chỉ báo đúng bằng chứng. Không mở lại G05, không Resolve mutation, không shutdown.


## Chỉ đạo mở rộng cùng ngày — toàn bộ data cũ và cleanup
User bổ sung xuất tất cả tài nguyên cũ (bao gồm LUT, meme VN, text, SFX/BGM/overlay/transition), một file ZIP lên Drive và dọn nguồn sample trong app. Binding bổ sung: docs/architecture/library-publication-cleanup-directive.md. Đây là output bắt buộc bước 02 sau companion gate, không bị giới hạn ở tiny sample. Task 01 inventory mở rộng, không xóa media/catalog trước backup/checksum/restore verification. G06 giữ checkpoint.
