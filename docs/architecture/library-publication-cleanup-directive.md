# Chỉ đạo bổ sung — xuất toàn bộ tài nguyên cũ và dọn nguồn mẫu

Ngày 2026-10-29. User: “dọn luôn nguồn test hiện tại trong code … meme Việt Nam với text … để 1 file rồi đẩy lên” và “LUT … nói chung có cái dữ liệu cũ nào thì đẩy lên luôn”. Đây là ủy quyền trực tiếp mở rộng công việc xuất kho và dọn dữ liệu mẫu; không cần xin lại cùng quyền. Không phải yêu cầu xóa toàn bộ code, unit tests hoặc dữ liệu dự án cá nhân.

## Kết quả cuối bắt buộc
- Một bản lưu hoàn chỉnh: /media/vanntruongg/Ventoy/ChunDVC_Publish/runs/<owned_run>/zip/ChunDVC_Legacy_Library_v1.0.zip.
- Bao gồm tài nguyên cũ: meme Việt Nam và các meme mẫu khác; text styles/titles; LUT; SFX; BGM; overlays; transitions; thumbnails/previews và dependency; các mẫu asset từ drive_assets; catalog/manifest/preset JSON và sample recipes liên quan. Giữ nhãn/tag tiếng Việt, ID gốc và ánh xạ source path. Không lấy .env, credentials, chat, cache Whisper/Vision, model weights hoặc project footage của người dùng.
- Export dữ liệu hardcoded bằng schema thực (TextStylePreset model dump đầy đủ), không chỉ chụp tên/text/thumbnail; giữ toàn bộ tham số font/color/outline/animation/timing/layout/tags. Có thể thêm .setting dẫn xuất, nhưng không thay JSON gốc hoặc giả định file-backed macro có cùng hành vi procedural preset. Các mục mock không có media thật chỉ lưu catalog metadata/unresolved report, không tạo WAV/MP4/LUT giả để đủ bộ.
- Upload bản mới vào root Drive 1b6zuDHXoCRXBfzI-Q18weYZ2hsNsd1Hq, giữ nguyên các file/link/quyền hiện có. Xác minh upload/readback/checksum trước công bố PUBLISHED. Nếu Worker không có connector, Codex có thể upload sau khi nhận đường dẫn file và checksum; không báo upload thành công khi chưa có kết quả công cụ.
- Sau verified export/restore, retire đúng các tài nguyên sample trong repo và loại bỏ tự seed/fallback catalog trong runtime. App khởi động với kho trống vẫn an toàn, hướng dẫn nhập/tải pack thật, không âm thầm tự tải lại mẫu. Khôi phục mẫu qua explicit import/restore, không runtime repo search.

## Hai deliverable khác nhau
File ZIP duy nhất là BẢN LƯU/PHÂN PHỐI TOÀN KHO, có thể chứa nhiều pack cài độc lập + bản gốc/catalog phục hồi. Không quảng cáo nó là một asset-pack cài trực tiếp nếu importer chưa hỗ trợ container nhiều pack. Nếu dùng outer bundle: có bundle_manifest.json ghi artifact_kind=library_backup, inventory, checksums và README restore; importer phải từ chối/hướng dẫn rõ thay vì quét các bản gốc/preview thành B-roll. Cấu trúc finalized ở workorder 02 theo inventory thật; một ZIP là ưu tiên của user, không dùng nó để nới vô hạn caps download/extract hoặc gây nhân đôi media không cần thiết.

## Bằng chứng nguồn cần xử lý
- src/core/text_preset.py: BUILTIN_PRESETS là catalog; PresetManager.__init__ gọi ensure_default_presets, ghi lại JSON mặc định; list_presets fallback BUILTIN_PRESETS; get_preset fallback BUILTIN_PRESETS[0]. Chỉ xóa files vẫn tái sinh hoặc gây IndexError. Phải xử lý các consumer/fallback này cùng empty-state tests, giữ model/generator/manager và dữ liệu custom.
- src/main.py: load_all_library_assets đưa BUILTIN_PRESETS và BUILTIN_TRANSITIONS vào Dock.
- src/ui/tabs/tab_assets.py: constructor và refresh nạp lại BUILTIN_PRESETS, BUILTIN_TRANSITIONS, MOCKUP_LUTS; meme/SFX/overlay rỗng fallback MOCKUP_*; các chỗ selection [0] phải an toàn khi catalog rỗng. Không thay fallback bằng asset giả hoặc hàng loạt QWidget.
- assets/templates/titles có cả text mẫu và macro gói ngoài: phân nguồn trước cleanup, không xóa tùy tiện dependency của pack thật.
- presets/text_styles/custom hoặc các file người dùng sửa không tự coi là generated chỉ vì cùng folder. Snapshot/backup không đồng nghĩa được xóa mọi thứ.

## Trình tự và an toàn hữu hạn
1. LIB-PUBLISH-01-I đang active: companion contract, inventory mở rộng, tiny ZIP proof. Chưa xóa nguồn hay sửa các module runtime trên.
2. LIB-PUBLISH-02 sau review: dựng bản lưu đầy đủ + các pack có thể cài, kiểm tra every-file size/SHA256, test ZIP integrity và khôi phục vào thư mục sở hữu độc lập; thẩm định đủ metadata text và roles. Đo actual size/free space/declared caps trước tạo file nặng; không fallback ổ hệ thống. Không tự xóa file trùng theo tên.
3. Tách cleanup runtime thành lát cắt có danh sách source files/IDs chính xác dựa inventory, test empty/restart/custom/import cả Dock và Studio. Không đại tu engine. Data cần cho tests chuyển vào fixtures nhỏ do tests sở hữu, không để production cứ nhúng sample chỉ để làm tests xanh. Focused tests và ảnh desktop thật khởi động kho trống/explicit import.
4. Chỉ retire source sau khi có bản ZIP kiểm tra được và bản originals dự phòng nằm ngoài app. Việc dọn repo dùng allowlist file + digest/source stat không đổi; file khác checksum giữ nguyên và báo changed. Giữ originals ngoài app cho đến khi remote checksum được xác minh, vì upload có thể bị chặn quyền. Không gọi git clean/reset hoặc xóa cả assets/ bằng glob rộng. Không đụng data/custom_collections.json, registry/storage thật, linked external media hoặc modified user presets.
5. Report phải phân biệt ARCHIVE_READY, UPLOAD_BLOCKED/PUBLISHED_VERIFIED, SOURCE_RETIRED, RUNTIME_CLEANED; không gộp thành ALL_DONE nếu một phần chưa làm. G06 checkpoint giữ nguyên; chưa tự resume khi phần thư viện đang active. Không shutdown.
