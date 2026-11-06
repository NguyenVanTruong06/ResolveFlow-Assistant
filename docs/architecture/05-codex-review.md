# G05-D — Codex review (2026-10-27)

Trạng thái: ACCEPTED_WITH_BINDING_CORRECTIONS cho bản đồ hiện trạng; đề xuất 05A-I chưa được duyệt triển khai. Task tiếp là G05-05A-D-R1, chỉ chốt identity/cache compatibility.

## Đối chiếu code
- cache_manager.compute_file_checksum băm cả path, size, mtime và đầu/cuối 64KiB, trả 24 hex; đổi path/mtime làm đổi khóa chính. Tuy nhiên get_cached_scan còn fallback theo filename/size, nên đây là cơ chế có thể gây miss, chưa đủ bằng chứng kết luận là nguyên nhân duy nhất của báo cáo user.
- Fallback filename/size hiện có không xác minh nội dung; có nhánh còn nhận bản thiếu file_size. Cùng tên/cùng size không chứng minh cùng footage. Đổi thuật toán khóa nhưng giữ fallback này không giải quyết tin cậy cache.
- clip_manifest.compute_content_hash chỉ đọc toàn bộ nếu <=2MiB; file lớn chỉ băm size+1MiB đầu/cuối. Hai file cùng đầu/cuối/size khác giữa có thể cho cùng giá trị một cách tất định. Không được gọi là zero collision hoặc khẳng định sửa nội dung bất kỳ đều miss.
- VisualIndex.load vẫn yêu cầu fingerprint size/mtime và producer có thể ghi theo path identity. Chỉ truyền content_hash mới không tự chuyển Visual sang cache ổn định khi rename/touch.

## Quyết định ràng buộc
1. Không thay ngay compute_content_hash của ClipManifest hoặc đổi clip/manifest IDs. Sampled hash hiện có giữ tương thích; gọi đúng là sampled fingerprint, không bằng chứng toàn bộ nội dung.
2. Ưu tiên thiết kế audio cache verified reuse trước; không ghép UI trạng thái kép và Visual invalidation vào cùng task. Cache mới phải có identity algorithm/version và config/provenance rõ, không trộn schema cũ chỉ vì cùng tên trường content_hash.
3. Chính sách đích đề nghị: SHA256 toàn bộ nội dung đọc streaming để xác minh reuse độc lập đường dẫn; IO phải có cancel và worker boundary, không băm video nhiều GB trên GUI thread mỗi lần recheck. Không copy media. Chi phí đọc toàn file là tradeoff phải công bố, chưa benchmark. Nếu đề xuất sampled accelerator thì chỉ tìm candidate, không đủ tự cấp verified cache hit.
4. Legacy chỉ tái sử dụng khi có bằng chứng hợp lệ. Có thể giữ exact legacy key tại đúng path/stat cùng validation hiện hữu theo mức tin cậy đã công bố; không biến nó thành full-content verified hoặc promote thành cache mới nếu thiếu bằng chứng nguồn. Filename/size-only không được auto-hit. Cache không xác minh được để nguyên và báo miss/unverified, không xóa hoặc giả vờ migration không mất hit.
5. Giữ cache directory hiện hữu trong lát cắt; env override chỉ bổ sung sau khi đối chiếu precedence explicit arg > env > default và các consumer thật, không đổi root Visual tiện tay.
6. Visual description mặc định explicit opt-in, nhưng một hành động unified sau này có thể bao audio/sampling và description khi user chọn, model vision available và inference được kiểm chứng. GPU vắng mặt không đồng nghĩa Ollama không chạy CPU; service alive không đồng nghĩa model loaded/capable. Lỗi inference có trạng thái/warning thật, không che mọi lỗi thành awaiting.
7. Bỏ các số 1–2ms, JSON<50KB, RAM1.8GB, VRAM3.5GB/cache250MB như hard guarantees chưa đo/enforce. 100*24*4=9600 frame, không phải6000; tuần tự Python không bảo đảm model Ollama đã unload VRAM. Mọi con số chỉ là dự toán/đề xuất trần có cách enforce, chưa là CODE_VERIFIED.
8. Coverage complete phải phân biệt sampled/analyzed, không từ vài ranges minh họa kết luận phủ hết clip. Schema DTO mới chỉ PROPOSED, không tự triển khai schema4 hoặc gọi API chưa có là thật.

Điểm dừng hữu hạn: một bản thiết kế cache identity/legacy migration có test gate chính xác; sau review giao một implementation slice. Không khảo sát lại toàn bộ G05, không mở thêm nhánh UI/vision/model.
