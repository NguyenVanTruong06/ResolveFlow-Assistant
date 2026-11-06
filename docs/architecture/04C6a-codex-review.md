# G04-04C6a-I — Codex review (2026-10-27)

Kết luận: CHANGES_REQUIRED, chưa nghiệm thu. Đã đọc production và tests; không sửa production trong lượt review.

## Bằng chứng trực tiếp
- Chạy đúng 5 file focused theo report: process exit 139 (Fatal Python error: Segmentation fault) sau progress 48% và một phần tiếp theo, không có final pytest summary. Stack có thread asset_cache.extract_thumbnail/_worker đang import cv2 và Qt main thread. Đây là dấu vết, chưa đủ xác định nguyên nhân crash. Không xác nhận lại claim 148 passed trên cây hiện tại.
- Probe pure guard với tempfile nguồn thật: linked external_path/file_path + origin_library_id/origin_root hợp lệ => insert=False và drag=False, ERR_CONFLICTING_SOURCE_METADATA. StorageManager.assets() dòng 393–394 gắn origin cho mọi record kể cả linked; đây là regression producer-consumer, còn làm đổi insert ngoài slice.
- Probe category='templates', tab='trans', file_path tồn tại => drag=True OK_LEGACY_MEDIA: cat=category or tab che mất tab transition, có thể xuất demo MP4.
- Probe runtime object giả tên lớp TextStylePreset => drag=True OK_PROCEDURAL_DRAG_SETTING: nhánh OR raw_type vi phạm điều kiện runtime type thực. UI hiện còn isinstance gate nên probe này chưa chứng minh đã export giả, nhưng core capability sai.
- Temp path export ghép asset_id thẳng vào path, không reserve exclusive. ID chứa separator/traversal có thể thoát temp hoặc gây cleanup ngoài ownership khi path resolve được. Không đủ bằng chứng ownership để xóa chỉ dựa tên UUID 8 ký tự. Cần tempfile API exclusive tên không lấy ID user.

## Phạm vi tiếp theo
G04-04C6a-R1 sửa cùng 2 modules và tests. Preview/legacy deferred. Không cần quay lại thiết kế hoặc mở Resolve. Test harness phải cô lập thumbnail I/O không thuộc drag assertions, teardown Qt sạch và ghi lại final exit status; không che crash bằng retry đến xanh.

## Review R1 — 2026-10-27
Codex chạy lại exact 5 files: 151 passed, 5 DeprecationWarnings in 3.00s, exit0. Không tái diễn segfault trong lượt này; thumbnail isolation là test-boundary correction, chưa chứng minh nguyên nhân gốc mọi crash Qt/cv2 ngoài production. Linked explicit origin, transition tab/category, isinstance và tempfile exclusive đã sửa theo scope.

CHANGES_REQUIRED giới hạn cuối ở inference scope: probe source_kind missing + origin_library_id/root cũ + file thật trả OK_LEGACY_MEDIA allowed; cùng origin + TextStylePreset trả OK_PROCEDURAL_DRAG_SETTING allowed. Origin-only không thể tự coi legacy/procedural khi không có bằng chứng linked. Ngoài ra effective_source_kind inference áp cả insert, khiến legacy insert chỉ có library_path/file thật bị ERR_ORIGIN_UNVERIFIED, trái ràng buộc drag-only của workorder trước. Không mở lại các hạng mục đã đạt; giao R2 core-only.

## Nghiệm thu R2 — 2026-10-27
ACCEPTED trong scope grid drag. Codex đối chiếu block inference và regression tests, chạy đúng 5 focused files: 159 passed, 5 Qt deprecation warnings in 3.07s, exit0. Inference bổ sung chỉ còn ở drag; origin-only bị chặn trước procedural/legacy, linked giữ external authority, explicit conflicting metadata bị từ chối. Các sửa R1 về transition/type/temp và UI lifecycle tiếp tục nằm trong tập kiểm thử đạt.

Đóng workorder 04C6a; không yêu cầu lặp test hoặc mở lại nếu không có bug mới. Bằng chứng là automated/offscreen MIME và event behavior, chưa phải native desktop drop vào Resolve. Crash không tái diễn ở focused run sau isolation, không coi là chứng minh đã sửa mọi lỗi Qt/cv2 production. Temp export thành công được giữ để consumer đọc muộn; cleanup policy dài hạn còn residual. Preview, Inspector/legacy drag và Text/Fusion insertion chưa được nghiệm thu bởi scope này. Tiếp theo 04C6b-D chốt preview authority.
