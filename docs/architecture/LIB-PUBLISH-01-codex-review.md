# LIB-PUBLISH-01-I — Codex review 2026-10-29

Kết luận: CHANGES_REQUESTED, 3 residual cụ thể; không mở rộng refactor. Báo cáo đã nhận đúng SHA256 96da256cd28a8c2951707f3d0a8f63d61ee154aca1bf35e0ceebb2d48675a70b. Việc chuyển báo cáo Anti → Codex được xác nhận, khác với nghiệm thu implementation.

## Bằng chứng mới
Codex chạy đúng 6 file focused: 111 passed in 24.34s, exit 0. Không chạy full suite, model, desktop hoặc Resolve. Sample ZIP SHA256 6aaf30ddca4ba2e19a2288ba8d77d29493137dcca3eb5fefd88f088ce773dd6c khớp, zip.testzip()=None,10 entries (manifest+3 masters+6 companions). Inventory đọc được 6582 entries/3818468927 bytes; đây là proposed classification, không phải checksum-verified dedup. Hardcoded catalog inventory mới có counts/IDs, chưa có full model dump. Recipes chưa được thống kê đầy đủ. Chưa có backup toàn kho hoặc upload/cleanup.

## R1 — Vai trò master/companion bị mất dữ liệu danh sách (P1)
pack_ingest.py:388–397,443–452,507–509 chỉ cấm template extension rồi loại mọi referenced path khỏi records. Probe: manifest có effect.setting dùng city.mp4 làm preview, đồng thời khai city.mp4 là broll master; prepare trả duy nhất effect.setting, asset độc lập bị âm thầm biến mất. Cần phân loại explicit masters trước khi gắn dependencies, cấm overlap bất kể extension/order, không silently drop. Shared companion không khai master độc lập vẫn hợp lệ.

## R2 — Casefold ambiguity chưa được chặn cho companions (P1)
Map ambiguity đã có cho master, nhưng preview/thumbnail lookup không kiểm tra. Probe local folder chứa previews/A.mp4 và previews/a.mp4; prepare vẫn chấp nhận ref previews/a.mp4. Phải reject conflict của companion cho cả exact và casefold lookup, không lựa bản theo enumerate order. ZIP entry collision guard không thay cho folder inspection guard.

## R3 — Runtime containment chỉ lexical, không theo file thật (P1)
storage_manager.py:403–423 dùng prev_full.is_relative_to(pack_root), phép này luôn đúng với join lexical đã sạch, không chặn symlink. Probe publish hợp lệ rồi thay preview bằng symlink ngoài kho: assets() vẫn cung cấp preview_path, resolve() ra ngoài owning pack. Thumbnail có cùng đường xử lý. Cần validate owning pack + canonical physical containment khi online, omit runtime companion không an toàn, giữ master và relative metadata để báo/khôi phục; không làm một companion lỗi xóa sạch toàn bộ danh sách. Offline giữ lexical metadata như test hiện hữu nhưng không coi filesystem hiện tại là proof.

## Giới hạn phát biểu cần hiệu đính
Pure asset DTO chỉ lexical, không có filesystem IO nên không thể tuyên bố DTO xác minh symlink. Các docstring tests không chứng minh những nhánh chưa assert (ví dụ exclude tất cả masters/shared/private, rollback) đã được test. Không cần thiết kế mới; bổ sung probes đúng 3 nhóm và giữ tests xanh.

## Giao bước tiếp theo
LIB-PUBLISH-01-R1-THEN-02A: A đóng 3 residual trong đúng pack_ingest.py/storage_manager.py + tests; B sau A xanh tạo một bản lưu nguồn toàn kho + catalog đầy đủ, checksum/restore proof và upload mới nếu công cụ/quyền cho phép. Chưa retire media hoặc runtime catalogs, chưa G06 resume. Code/backup và cloud publication phải có trạng thái riêng, không đánh đồng.
