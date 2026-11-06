# Codex review — 01C-T và G02-D

01C-T ACCEPTED: Codex đọc test lỗi status/sync và chạy 36 focused tests PASS in2.67s, exit0. 01C đóng trong phạm vi SourcePage Whisper settings; chưa kiểm chứng desktop thật.
G02-D ACCEPTED_WITH_BINDING_CORRECTIONS để giao02A:
- Năm giá trị storage là trạng thái tổng hợp có precedence, không phải năm trục trực giao. READ_ONLY/FULL vẫn cho đọc; online không được loại READ_ONLY như thiết kế hiện tại.
- os.access chỉ là tín hiệu quyền, không chứng minh write sẽ thành công hoặc phát hiện mọi mount read-only/ACL; mọi ghi thật vẫn phải xử lý OSError. Không tạo file probe mỗi block 1MiB.
- Không đọc/parse toàn registry trong health check: ensure_space gọi mỗi chunk. Corrupt registry là registry error ở boundary riêng; không lồng scan nặng hoặc UI IO vào online.
- Giữ online/require_online là legacy identity/readability gate trong02A, không delegate toàn bộ health vào hot path. get_state mới và ensure_space là phạm vi đầu tiên. Chưa bảo đảm mọi tagging/lock writer đã qua health guard.
- Registry sharding chưa được duyệt: số 180–250bytes/asset và <500KB/1000packs là ước lượng, chưa benchmark. Giữ JSON schema hiện có; không dùng ngưỡng10000asset chưa đo để tự đổi schema.
- Recovery startup không auto-register pack hoặc xóa orphan theo “không có process” đơn thuần;02C cần transaction evidence, ownership, lock, root identity và user intent rõ trước implementation.
- Linked config trong03_local_linked không phải quyền xóa footage external; cleanup chỉ xóa dữ liệu managed được xác minh. Models có thể portable/configurable; path cache/model trong doc là ví dụ cần đối chiếu code, không universal requirement.
Giao02A health/guard nhỏ trước;02B/02C tiếp tục deferred.
