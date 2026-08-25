# 📘 HƯỚNG DẪN CHI TIẾT CÁC PHIÊN BẢN (RESOLVEFLOW VERSION GUIDE)

Tài liệu này cung cấp hướng dẫn so sánh, lựa chọn và cách sử dụng chi tiết cho từng phiên bản của **ResolveFlow Assistant**.

---

## 🌟 PHIÊN BẢN HIỆN TẠI: V4.0 (AI VISUAL, AUDIO & DIRECTOR AUTOMATION SUITE)

### 1. Tính năng nổi bật của bản v4.0
* **Auto Speed-Ramp (Tua nhanh khoảng lặng 8x):** Thay vì cắt bỏ khoảng lặng, tool ép thời lượng các khoảng tĩnh xuống 8 lần (800% speed), biến các đoạn dừng chết thành cú chuyển cảnh Timelapse nghệ thuật kèm âm thanh Whoosh.
* **Auto Dynamic Re-framing (16:9 ➔ 9:16):** Tự động bám theo khuôn mặt/trọng tâm người nói để chuyển đổi video ngang 16:9 sang video dọc 9:16 mượt mà (Smooth Pan), không bị lệch người ra ngoài khung hình.
* **AI B-Roll Inserter (Track Video 2):** Quét các từ khóa thị giác đắt giá trong phụ đề, tự động đánh dấu vị trí B-Roll trên Timeline và xuất danh sách gợi ý tìm kiếm (`*_broll_suggestions.txt`).
* **Auto SFX Engine (Track Audio 2):** Tự động bố trí các điểm âm thanh hiệu ứng (Whoosh, Pop, Click, Ding) tại các vị trí chuyển cảnh và Auto Punch-in Zoom.
* **Đạo Diễn AI (AI Director v3.0 tích hợp):** Lọc sạch các đoạn nói vấp (Bad Takes), lọc từ đệm, hiệu ứng Auto Punch-in (Zoom luân phiên 1.15x), kịch bản Clean Talk & Viral Shorts 60s.
* **Đa Tầng Media (Multi-Track) & Phụ đề Karaoke Text+ FCPXML:** Hỗ trợ đầy đủ các track Video 1, Video 2, Audio 1, Audio 2 và phụ đề nảy chữ chuyên nghiệp cho cả **DaVinci Resolve Free** & **Studio**.

### 2. Hướng dẫn từng bước sử dụng bản v4.0
1. **Khởi động ứng dụng:**
   ```powershell
   python main.py
   ```
2. **Chọn tệp đầu vào:**
   * Bấm **"Chọn Video"** -> Chọn 1 hoặc nhiều video cùng lúc (giữ `Ctrl` hoặc `Shift`).
   * (Hoặc trên bản Studio: Bấm **"Tự lấy từ Resolve"**).
3. **Cài đặt chế độ v4.0:**
   * Tích chọn `⚡ Tua nhanh khoảng lặng thay vì cắt bỏ (Auto Speed-Ramp 8x)` nếu muốn tạo hiệu ứng chuyển cảnh Timelapse.
   * Bật tùy chọn `Auto Re-framing (Bám mặt sang video dọc 9:16)` nếu làm video Shorts/TikTok từ video quay ngang.
   * Tích chọn `Tự động gợi ý cảnh minh họa B-Roll` và `Tự động chèn âm thanh hiệu ứng SFX`.
4. **Bấm "KHỞI CHẠY TIẾN TRÌNH TỰ ĐỘNG HÓA v4.0":**
   * App sẽ tạo ra các tệp kết quả:
     * `*_cut.edl`: File cắt timeline kèm Markers (Tím = Timelapse, Đỏ = Bad Take, Hồng = B-Roll, Xanh = SFX).
     * `*_cut_karaoke.fcpxml`: File phụ đề động Karaoke Text+ (khung hình 16:9 hoặc 9:16).
     * `*_broll_suggestions.txt`: Danh sách từ khóa và thời gian đặt B-roll.
     * `*_cut.srt` và `*.srt`: Phụ đề tiêu chuẩn.
5. **Nhập vào DaVinci Resolve:**
   * Vào **File** > **Import** > **Timeline...** > Chọn file `.edl`.
   * Vào **File** > **Import** > **Timeline...** > Chọn file `.fcpxml`.

---

## 📦 PHIÊN BẢN V3.0 (AI DIRECTOR & SEMANTIC CUT)
* Đạo diễn kịch bản AI: Lọc nói vấp (Bad Takes) & từ đệm.
* Hiệu ứng Auto Punch-in (Zoom luân phiên 1.15x).
* Kịch bản Clean Talk, Viral Shorts 60s, Podcast Summary.

---

## 📦 PHIÊN BẢN V2.0 (MULTI-CLIP BATCH & KARAOKE SUITE)
* Hỗ trợ ghép hàng loạt video (Multi-Clip Stitching).
* Phụ đề Karaoke Text+ (FCPXML) phóng to và đổi màu từng từ.

---

## 📦 PHIÊN BẢN V1.0 (SINGLE-CLIP BASIC CUTTER)
* Xử lý 1 video đơn lẻ theo âm lượng sóng âm thô.
