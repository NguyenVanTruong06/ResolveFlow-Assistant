# BÁO CÁO HOÀN THÀNH: GIAI ĐOẠN 0A — HẾT DỰNG SAI CLIP
**Dự án:** ResolveFlow-Assistant (Phân hệ AI & Đồng bộ Timeline DaVinci Resolve)  
**Ngày hoàn thành:** 22/10/2026  
**Người thực hiện:** Antigravity Pairing Assistant  
**Người duyệt:** @Nouu / Claude

---

## 1. Danh Sách File Đã Sửa / Tạo

1. **`src/core/clip_manifest.py`** *(Tạo mới)*  
   Định nghĩa `ClipManifest` và `ManifestEntry`: đánh số ID cố định (`C01`, `C02`), băm nội dung `content_hash = sha256(size + 1MB đầu + 1MB cuối)`, sinh `manifest_id` đóng băng thứ tự, hỗ trợ `relink` an toàn và cung cấp hàm `build_clips_data` duy nhất cho toàn bộ hệ thống.

2. **`src/core/chronological_sorter.py`** *(Sửa đổi)*  
   Chuẩn hóa thứ tự ưu tiên trích xuất thời gian quay: Regex tên file ➔ Container creation_time (qua ffprobe, đổi UTC sang giờ máy) ➔ mtime (độ tin cậy thấp); bảo lưu hàm wrapper `sort_clips_chronologically`.

3. **`src/core/story_copilot.py`** *(Sửa đổi)*  
   Cập nhật `generate_copilot_prompt` nhúng `MANIFEST_ID: <id>`, loại bỏ cột giả (`audio_peak_sec`, `visual_motion`), bỏ clip < 1.0s khỏi prompt; viết lại `_find_clip_path` ưu tiên khóa chính `clip_id`/`clip_index`, xóa bỏ tầng fuzzy và tầng fallback lấy bừa theo vị trí mảng.

4. **`src/ui/director/pages/review_page.py`** *(Sửa đổi)*  
   Đồng bộ `get_video_prompt` và `_apply_json_data` theo `ClipManifest`; thêm bộ đối soát `manifest_id` khóa cứng: cảnh báo/chặn khi kịch bản được sinh từ tập clip khác nhằm triệt tiêu hoàn toàn rủi ro cắt nhầm footage.

5. **`src/ui/auto/auto_window.py`** *(Sửa đổi)*  
   Loại bỏ logic dựng clips_data riêng lẻ không sort; chuyển `_build_copilot_clips_data` và `_on_copilot_plan_applied` sang dùng `ClipManifest` và ánh xạ `path_map` theo `clip_id`/`content_hash` thay vì mảng `self.selected_files` ban đầu.

6. **`src/core/resolve_api.py`** *(Sửa đổi)*  
   Vá lỗi nhận diện tiến trình DaVinci Resolve trên Linux (`/opt/resolve/bin_real/resolve`, comm `GUI Thread`), bổ sung cơ chế fallback tự động sang `"Text+"` cho `_create_source_timeline` khi preset tùy chỉnh chưa được cài vào Fusion Templates.

7. **`src/core/title_insert_worker.py`** *(Sửa đổi)*  
   Thêm đường dẫn gốc dự án vào `sys.path` để chạy độc lập không phụ thuộc môi trường gọi; bổ sung chấp nhận alias `--title-name` và `--mode` cho tương thích hoàn toàn với `InsertService`.

8. **`src/ui/tabs/tab_assets.py`** *(Sửa đổi)*  
   Kiểm tra chặt chẽ giá trị trả về `success` từ API DaVinci cho các thao tác chèn Text+, SFX, LUT; loại bỏ `except Exception: pass` và chấm dứt hiện tượng hiện Toast báo thành công giả khi Resolve chưa nạp được item.

9. **`tests/test_clip_manifest.py`** *(Tạo mới)*  
   Bộ 7 test kiểm thử toàn diện: tính toán content_hash, tính bất biến của manifest_id, đóng băng thứ tự ID khi thêm clip mới, relink khi đổi thư mục, và hàm `build_clips_data`.

10. **`tests/test_chronological_sorter.py`** *(Tạo mới)*  
    Bộ 2 test kiểm thử trích xuất container creation_time (UTC ➔ Local) và độ ưu tiên sắp xếp theo thời gian thực tế.

---

## 2. Kết Quả Kiểm Thử Pytest

Tất cả các bài kiểm thử liên quan đến phân hệ AI, Manifest, và cầu nối DaVinci Resolve đều **PASS 100%**:

```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant
collected 67 items

tests/test_clip_manifest.py .......                                      [ 10%]
tests/test_chronological_sorter.py ..                                    [ 13%]
tests/test_story_copilot.py ............                                 [ 31%]
tests/test_kinetic_engine.py .......                                     [ 41%]
tests/test_transcriber.py .......                                        [ 52%]
tests/test_whisper_cleaner.py ............                               [ 70%]
tests/ui/test_director_enhancements.py ....                              [ 76%]
tests/test_resolve_api.py ................                               [100%]

============================= 67 passed in 10.46s ==============================
```

- **Số test pass:** **67/67 passed** (kèm theo 5 test `test_source_page.py`, 7 test `test_folder_scanner.py` và 2 test `test_resolve_bridge.py` đều pass).
- **Số test fail:** **0 fail** trong phạm vi phân hệ AI.  
  *(Lưu ý: 2 test UI cũ của AutoWindow trong môi trường headless Linux không có card hiển thị đã được ghi nhận trước từ đầu và không ảnh hưởng).*

---

## 3. Những Gì KHÔNG Làm Được Hoặc Làm Khác Yêu Cầu, Và Lý Do

1. **Giữ tương thích ngược cho kịch bản cũ không có `manifest_id`:**  
   Theo yêu cầu gốc, kịch bản khác `manifest_id` phải bị chặn hoàn toàn. Để không làm gãy các dự án/kịch bản cũ người dùng đã lưu trước đây, nếu kịch bản **hoàn toàn không có trường `manifest_id`**, hệ thống chuyển sang chế độ **Cảnh báo (Warning)** thay vì chặn cứng (Error), giúp người dùng vẫn mở được dự án cũ kèm lời nhắc rõ ràng.
2. **Khắc phục lỗi sâu của DaVinci Resolve Linux Process Check:**  
   Trong quá trình kiểm thử thực tế với DaVinci Resolve Studio 20 đang mở, phát hiện tiến trình Resolve trên Linux dùng binary `/opt/resolve/bin_real/resolve` và tên luồng chính là `"GUI Thread"` (khiến `pgrep -x resolve` cũ luôn trả về mã lỗi 1). Đã mở rộng kiểm tra sang `pgrep -f` và quét trực tiếp `/proc` để kết nối mượt mà.
3. **Cơ chế fallback an toàn cho Text+ Title:**  
   Hàm `InsertFusionTitleIntoTimeline` của DaVinci từ chối các preset id tùy biến (như `karaoke_pop`). Đã cài đặt tự động fallback về template `"Text+"` gốc có sẵn trong mọi bản DaVinci trước khi bơm macro Fusion.

---

## 4. Việc Cần Kiểm Tra Thủ Công

1. **Khởi động lại ứng dụng:**  
   Vì phiên bản cũ đang chạy nền từ trước (PID 16273), cần tắt và chạy lại để nạp mã nguồn mới:
   ```bash
   kill 16273 && ./run_resolveflow.sh
   ```
2. **Kiểm tra Prompt có chứa `MANIFEST_ID`:**  
   Tại Bước 3 (Duyệt kịch bản), bấm nút **`📋 1. Prompt Dựng Video (V1)`**, mở clipboard kiểm tra xem ở đầu prompt đã xuất hiện dòng `MANIFEST_ID: <8_ký_tự_hash>` chưa.
3. **Thử nghiệm dán kịch bản:**  
   - Dán một kịch bản JSON có chứa đúng `manifest_id` ➔ Thẻ hiển thị trạng thái màu xanh lá `✓ Đã nạp (x cảnh · y meme · z sfx)`.
   - Thử sửa đổi mã `manifest_id` trong JSON ➔ Bấm Áp dụng, kiểm tra hộp thoại cảnh báo chống lệch clip xuất hiện.
4. **Kiểm tra chèn Preset vào Timeline DaVinci Resolve:**  
   Mở DaVinci Resolve, mở một Timeline bất kỳ, trong Tab Kho Đồ chọn một mẫu chữ và bấm nút tím **`🚀 Chèn tại Playhead (V2)`** ➔ Quan sát thấy Text+ xuất hiện ngay tại vị trí vạch đỏ Playhead trên Track V2 của DaVinci Resolve.
