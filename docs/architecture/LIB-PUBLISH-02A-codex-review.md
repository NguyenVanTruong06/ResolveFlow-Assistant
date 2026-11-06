# LIB-PUBLISH-01-R1-THEN-02A — Codex review 2026-10-29

## Kết luận tách theo deliverable
- Companion residual R1/R2/R3: ACCEPTED_WITH_RECORDED_LIMITS trong scope đã giao. Source đã thêm explicit-master role collision, casefold ambiguity và runtime resolved containment; fresh 7 focused files 120 passed in 23.56s, exit0 (115 ingestion/DTO/UI +5 exporter tests). Không full suite/model/Resolve/desktop.
- Bản lưu hiện có: ARCHIVE_READY_WITH_VERIFIED_DIGEST. Codex tính lại cả 3,415,854,045 bytes: SHA256 f27aefb09759a4304599703fbaf4d281b28a67846fdb4891eae3d5cf8d4389ad, MD5 aa00d288fbf7b3632f14d8ebd62b897b. ZIP có6601entries,0duplicate,3,823,361,286 bytes uncompressed. Checksums liệt kê6594originals; catalog JSON có24text/4transition. Worker restore report6594/6594 matched và CRCpass; Codex không chạy lại toàn bộ decompression/hash từng original, đã đối chiếu current-source coverage không thấy omitted/stale paths.
- Remote: UPLOAD_BLOCKED_PERMISSION. Codex dùng Drive connector tạo folder release trong đúng root, Google trả403 insufficientParentPermissions; chưa có folder hoặc file upload thành công. Đích root vẫn nguyên. Tài khoản kết nối hoangphudakha@gmail.com. User đã được hỏi cấp Editor hoặc để Anti thử browser có session owner/editor; không lặp lại cùng failed write khi quyền chưa đổi.
- Cleanup: NOT_STARTED. Nguồn, user metadata/registry và G06 checkpoint giữ nguyên.

## Hiệu đính report
Report phân bố source folder chưa chính xác. Inventory thực: assets6535, presets24, drive_assets23, recipes12 =6594. Không phải assets6558/presets23/drive_assets1. Actual archive giữ đủ Drive LUTs/Picture/Help/powergrade; đây là lỗi thống kê trong lời báo cáo, không phải thiếu files theo coverage check.

## Giới hạn exporter cần ghi trung thực
5tests hiện có không bao phủ mọi claim trong docstring: chưa có assert low-space/no-overwrite/corrupt restore path/owned cleanup. check_mount_space dùng is_dir+statvfs không phân biệt mountpoint bị tháo; probe tmp directory thông thường vẫn accepted. export_legacy_library mở ZIP bằng mode w, mkdir exist_ok=True; không được reuse như transaction/retirement tool hoặc ghi lại vào run hiện có. Source survey chưa lưu ctime_ns/fstat; full source identity-before-retirement phải được kiểm tra riêng. Các giới hạn này không thay đổi digest của archive đã xác minh; không cần rebuild bản lưu tốt chỉ để sửa tool. Sau cleanup catalogs không được hardcode test thực tế luôn có24/4.

## Chỉ đạo kế tiếp
LIB-PUBLISH-02B — xác minh upload bằng browser nếu có session được cấp, dọn catalogs và retire source có allowlist/hash. Giữ hai bản đã verify trên ổ ngoài (ZIP + restore_verification originals). Nếu remote vẫn blocked, chỉ move/retire nguồn khỏi runtime với rollback cục bộ bảo toàn; tuyệt đối không xóa hai bản dự phòng. Không đổi title engine thành dummy preset; kho rỗng dùng empty state thật. Không fake PUBLISHED hoặc one-click install: ZIP hiện là backup, không phải installable pack.
