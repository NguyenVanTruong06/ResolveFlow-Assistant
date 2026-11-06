# Codex review — G02-02A-I

CHƯA NGHIỆM THU, cần G02-02A-R1.
Codex chạy bộ focused được giao: 58 passed in20.41s, exit0.

Probe thật trong TemporaryDirectory (không chạm kho user): configure root; rename sang moved; tạo symlink root->moved, cùng marker identity. get_state()=ERROR nhưng ensure_space(0) ALLOWED. Write boundary chưa chặn cấu trúc mà health đã từ chối.
Probe root config=123: get_state()=OFFLINE, trái policy configured root sai type->ERROR. Root stat PermissionError cũng đang bị catch chung thànhOFFLINE.
get_state đọc marker bằng read_text trực tiếp, bỏ MAX_JSON guard có ở read_json cũ. Health query cần đọc marker bounded, không nạp arbitrary file lớn. Không nối fullregistry scan.

Giữ giới hạn os.access là hint và legacy online/require_online; không mở sharding/recovery/migration. Sửa guard/query các điểm trên rồi chạy lại đúng nhóm58+regressions.
