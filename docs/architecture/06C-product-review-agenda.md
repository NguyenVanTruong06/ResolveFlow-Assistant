# Agenda Trao Đổi Định Hướng AI: ChunDVC Dựng Thô & Kịch Bản Thông Minh

> **CHECKPOINT ĐÃ HOÀN TẤT — 2026-10-29:** D01/D02/D03 đều USER_APPROVED tại `06C-product-decisions.md`: AI đề xuất toàn bộ, user duyệt/sửa một lượt; B-roll/SFX thiếu có đề xuất bỏ qua và cảnh báo trong lượt duyệt chung; brief tự do + preset, nạp reference local riêng, URL chỉ lưu và chưa tự xem. Các lựa chọn cũ bên dưới là lịch sử khi mâu thuẫn quyết định đã chốt, không yêu cầu user chọn lại.

> **Mục tiêu buổi trao đổi:** Thống nhất với User về luồng tương tác AI, ranh giới tự động hóa và mức độ kiểm soát của người dựng trước khi triển khai tính năng liên quan đến creative brief, phong cách dựng, và lựa chọn tài nguyên.  
> **Nguyên tắc cốt lõi:** Tiết kiệm thời gian xem nguồn cho editor, bảo vệ dữ liệu cục bộ (hạn chế gửi dữ liệu lên cloud), hỗ trợ gợi ý ý tưởng dựng; **không hứa hẹn tạo video hoàn thiện chỉ với 1 click**.

---

## 1. ChunDVC Hiện Đã Có Gì & Đang Ở Đâu?

### Những gì hệ thống ĐÃ LÀM ĐƯỢC (`CODE_VERIFIED`):
1. **Lắng nghe & bóc băng thoại:** Tích hợp Whisper cục bộ bóc băng âm thanh, đồng bộ timeline nguồn.
2. **Nhìn mẫu cảnh quay cục bộ:** VLM trích xuất mẫu khung hình (sample frames) lưu cache nội bộ, hỗ trợ mô tả bối cảnh trực quan mà không cần upload toàn bộ video nặng lên cloud.
3. **Lập kịch bản phân đoạn:** Hỗ trợ sinh prompt (Web/Local/API) theo từng lô (Batch Draft) hoặc kịch bản tổng (ReviewPage Slot 1 & Slot 2).
4. **Cố vấn tìm tài nguyên:** Hệ thống tra cứu metadata từ kho lưu trữ offline (`StorageManager`), đề xuất B-roll/SFX phù hợp theo từ khóa và bối cảnh (Advisory Drawer với điểm khớp từ điển `NN/100` — đây là **lexical matching score** dựa trên từ khóa/tag trong kho, không phải xác suất chọn đúng, không phải độ tin cậy % hay quality score).

### Những GIỚI HẠN HIỆN TẠI cần biết:
- **Lấy mẫu khung hình thưa:** Chỉ lấy vài frame tiêu biểu mỗi clip, chưa xem được toàn bộ chuyển động liên tục.
- **Không tự đọc URL ngoài & Không thêm crawler mới:** Không tự cào dữ liệu từ link TikTok/YouTube/Instagram tham khảo; không thêm crawler, model vision trả phí hay matching service mới.
- **Chưa nối tự động sang Timeline Resolve:** Khâu cố vấn (`advisory`) hiện mới hiển thị danh sách ứng viên trên bảng giao diện; chưa có luồng người dùng duyệt chọn để tự động đẩy lệnh dựng sang DaVinci Resolve (đang ở bước thiết kế 06C/G07).

---

## 2. Tình Huống Thực Tế: 100 Clip Điện Thoại & Yêu Cầu "Cute / 60 Giây"

Giả sử bạn nhận một job dựng thực tế:
- **Đầu vào:** 100 clip khách quay bằng điện thoại (rung lắc, dọc/ngang lẫn lộn, nhiều góc quay hỏng) + Kho tài nguyên dùng chung (B-roll, âm thanh, nhạc nền có sẵn trên máy) + 1 video mẫu khách gửi làm tham khảo phong cách.
- **Yêu cầu khách hàng (Brief):** Phong cách dễ thương (cute), thời lượng đúng 60 giây, cấm dùng meme phản cảm/chế nhạo, và bắt buộc phải giữ lại câu thoại thương hiệu mở đầu ("Chào mừng đến với tiệm bánh X").

Trong tình huống này, hệ thống AI cần phân biệt rõ 5 nhóm yếu tố:
1. **Footage riêng của khách (Project Footage):** Chỉ được dùng trong dự án này, cần ưu tiên giữ khoảnh khắc đẹp, lọc bỏ đoạn rung/lỗi, tuyệt đối không gửi clip thô lên cloud công cộng.
2. **Kho tài nguyên dùng chung (Shared Stock/SFX):** Nhạc nền vui nhộn, hiệu ứng âm thanh leng keng (SFX cute), hình dán overlay chung từ kho offline đã quản lý.
3. **Tài nguyên tham khảo độc lập (Reference-Only Assets):** Video clip mẫu hoặc video phong cách mà khách hàng gửi chỉ dùng để trích xuất văn bản brief/ý tưởng/nhịp điệu; **HOÀN TOÀN TÁCH BIỆT KHỎI FOOTAGE VÀ KHÔNG ĐƯỢC PHÉP CẮT DỰNG LÊN TIMELINE SẢN PHẨM**.
4. **Ràng buộc cứng (Bắt buộc / Cấm):** Bắt buộc giữ câu slogan thương hiệu; cấm chèn meme troll hay âm thanh giật gân.
5. **Tài nguyên còn thiếu:** Ý tưởng kịch bản cần cảnh "bánh nở trong lò" nhưng trong 100 clip khách quay không có, kho máy cũng chưa có.

---

## 3. Ba Nhóm Quyết Định Cần User Chốt (Kèm Khuyến Nghị Chưa Duyệt)

### Nhóm 1: Định hình Brief Sáng Tạo & Video Tham Khảo (Creative Brief & Reference)
- **Vấn đề:** Làm sao để AI hiểu được phong cách "cute", "nhanh", hay "sang trọng", và tiếp nhận video mẫu mà khách hàng gửi?
- **Các lựa chọn:**
  * *Lựa chọn A (Thủ công):* User tự nhập tóm tắt ngắn (văn bản) vào ô Creative Brief; nếu có video mẫu, User tự tải về và nạp vào máy như một clip nguồn để trích xuất text/transcript.
  * *Lựa chọn B (Bán tự động):* Có bảng tùy chọn preset phong cách dựng sẵn (Vlog Cute, Tech Review, Cinematic, Drama) kèm danh sách tag bắt buộc / tag cấm.
- **ĐÃ CHỐT D03 (USER_APPROVED):** Brief tự do + preset; ảnh/video tham khảo được nạp riêng với vai trò reference-only. Link chỉ lưu để đối chiếu, không tự xem/tải. Local caption/transcript không tự chứng minh đã hiểu toàn bộ phong cách dựng; khả năng phân tích reference phải được thiết kế và ghi rõ giới hạn trước triển khai.

### Nhóm 2: Ưu Tiên Cảnh Quay Dự Án & Quyền Chọn Tài Nguyên (Footage vs Asset Selection)
- **Vấn đề:** Khi ghép B-roll/SFX, mức độ tự động hóa giữa AI và người dựng được phân định như thế nào?
- **Lịch sử trao đổi (Không còn là mặc định):**
  * *Phương án thủ công cũ:* Bắt người dùng duyệt xác nhận từng dòng B-roll/SFX riêng lẻ.
  * *Phương án auto điểm cũ:* Tự động duyệt ứng viên điểm $\ge 90$.
- **ĐÃ CHỐT D01 (USER APPROVED — 2026-10-29):**
  > **"AI đề xuất trọn bản dựng và tài nguyên, tớ duyệt/sửa một lượt trước khi dựng."**
  * AI chuẩn bị toàn bộ bản dựng đề xuất kèm tài nguyên phù hợp từ metadata được cấp.
  * Không bắt user duyệt xác nhận từng dòng B-roll/SFX riêng lẻ như mặc định bắt buộc.
  * User có toàn quyền xem/sửa từng mục khi cần, rồi duyệt chung toàn bộ bản đề xuất một lượt trước khi thi công.
  * Tuyệt đối không tự động duyệt dựa trên ngưỡng điểm 90, không coi điểm matching $NN/100$ là xác suất đúng hay bằng chứng user đã duyệt.
  * Tạo đề xuất không phải là quyền thi công Resolve. Chi tiết hợp đồng và giao diện sẽ được thiết kế ở 06C/G07, không triển khai ở task layout hiện tại.

### Nhóm 3: Xử Lý Khi Thiếu Tài Nguyên: Dựng Thô (Rough-cut) hay Chặn Lại (Block)?
- **Vấn đề:** Kịch bản muốn có cảnh B-roll hoặc tiếng động mà trong kho không tìm thấy tài nguyên phù hợp, hệ thống nên làm gì?
- **Các lựa chọn:**
  * *Lựa chọn A (Chặn lại - Fail-closed):* Dừng lại báo lỗi đỏ, yêu cầu User tải bổ sung tài nguyên hoặc xóa bỏ dòng nhu cầu đó trong kịch bản mới cho dựng tiếp.
  * *Lựa chọn B (Dựng thô bỏ qua - Rough-cut Omission):* Cho phép tiếp tục dựng mạch chính A-Roll trên Resolve; những chỗ thiếu B-roll/SFX được để trống hoặc ghi chú để User chèn thủ công sau.
- **ĐÃ CHỐT D02 (USER_APPROVED):** AI đề xuất bỏ qua B-roll/SFX thiếu, ghi rõ cảnh báo và phạm vi ngay trong lượt duyệt chung D01; user đồng ý thì dựng thô mạch chính tiếp. Không thêm hộp thoại xác nhận từng mục, không tự bỏ qua nguồn chính/lỗi định danh hay yêu cầu bắt buộc của khách. Đây là hướng cần triển khai, chưa phải tính năng đã nghiệm thu.

---

> [!IMPORTANT]
> **Bước tiếp theo:** Ba quyết định đã chốt. Anti cập nhật thiết kế theo workorder G06-06C-PRODUCT-ALIGN-D, không tự triển khai/crawl/download/paid vision/media generation; Codex review rồi cấp một slice code hữu hạn. Không lặp lại câu hỏi D01/D02/D03.
