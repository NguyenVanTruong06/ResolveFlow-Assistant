# Codex review — G02-02C1-R1

CHƯA NGHIỆM THU. Codex60testPASS0.55s exit0. Registry invalidrecord và scandir errors đã sửa.
Residual thuộc task R1:
- Probe manifest open PermissionError -> complete+invalid_entry; entry.stat errors tương tự. Report IO incomplete chưa áp dụng tới entry.
- Probe schema_version=1.0 -> complete+unregistered_pack. Cần int không bool.
- Read oversized manifest continue trước cộng total_metadata_read, các lượt rejected read không đếm trần. Marker recheck thiếu postcheck remaining; health allowance chưa định nghĩa.
- Manifest asset loop thiếu cancel/deadline checks đã giao.
Giao R2 đóng checklist này, không mở API/UI/recovery.
