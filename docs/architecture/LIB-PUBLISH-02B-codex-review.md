# LIB-PUBLISH-02B — Codex review 2026-10-29

Report SHA256 reçu/khớp: 2047cdef3c730fae8140706f3c5b2be7ca6e0aa2a4e90fc84dbf884080b66f67.

## Phần được xác nhận
- Catalog demo mặc định đã rỗng; PresetManager không seed24JSON, fallback kỹ thuật vẫn có, không trở thành library card. Fresh đúng8focusedfiles:70passed/7.80s/exit0. Giữ review trong scope, không fulltests/Resolve/inference.
- Nguồn thực tế đã retire theo journal; Codex đối chiếu current paths và backup counterpart existence/size, artifacts tại .bridge/ux-evidence/LIB-PUBLISH-02B-CODEX-REVIEW/retirement-audit.json. Không gọi lại --apply/rollback trên kho thật. ZIP và restored originals vẫn ở Ventoy; không suy ra remote upload.
- Remote giữ UPLOAD_BLOCKED_PERMISSION từ403 đã xác minh; không có thông tin quyền thay đổi, không lặp write attempt. User cần Editor hoặc browser session có quyền.

## Chưa nghiệm thu công cụ destructive — 4 probes tái hiện
Các probes chỉ dùng TemporaryDirectory riêng, không tác động dữ liệu thật. Xem retirement-probes.json.
1. Apply so duy nhất size/mtime, dù plan có ino/dev/ctime. Thay source bằng file inode khác, cùngsize/mtime: replacement_deleted=True. Không đạt guard user replacement.
2. Bản counterpart biến mất sau plan: execute vẫn unlink source (source_deleted_without_counterpart=True). Không recheck backup/volume trước mutation.
3. Journal chỉ ghi cuối vòng lặp. Nếu journal parent không thể tạo: source đã bị xóa rồi FileExistsError, chưa có journal. Không crash-safe/partial recovery đúng hợp đồng.
4. Rollback copy2 trực tiếp trước xác minh checksum: corrupted counterpart được ghi vào destination rồi mới báo lỗi (corrupt_destination_written=True). Cũng thiếu atomic no-overwrite nếu destination được tạo giữa exists-check và copy.

Không kết luận actual retirement mất dữ liệu: audit local hiện có backups; nhưng không được khai công cụ apply/rollback đã an toàn đầy đủ hoặc tái dùng nó trước bản vá.

## Giới hạn runtime evidence
tools/verify_empty_and_import_runtime.py ép QT_QPA_PLATFORM=offscreen, không screenshot desktop. Nó ghi registry thủ công và WAV bytes giả, không đi qua PackIngestService/wizard và không kiểm tra decode/preview. QSettings config không cô lập trước QApplication; windows cleanup không nằm trong finally. Log này là OFFSCREEN_COMPONENT_SMOKE, không phải desktop+real pack round-trip. Không cần lặp inference/Resolve; chỉ sửa harness và chạy 1 smoke hữu hạn với pack thật đã có.

## Bước tiếp theo hữu hạn
LIB-PUBLISH-02B-R1: chỉ retire tool + runtime harness + focused tests, không production UI/core scope expansion. A khóa replacement/backup/journal/rollback, B evidence smoke truthful. Cấm mọi apply/rollback thật hoặc xóa thêm sources/backups. Source retirement giữ kết quả hiện tại, G06 checkpoint, cloud permissionpending. Chốt sau khi probes xanh; không thêm feature mới.
