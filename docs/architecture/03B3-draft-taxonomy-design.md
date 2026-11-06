# ChunDVC — RAM Draft Taxonomy & Guarded Pack Commit

TASK_ID G03-03B3-I. IMPLEMENTED / VERIFIED_BY_CODEX trong phạm vi dưới đây.

## API và dữ liệu
- src/core/pack_ingest.py: validate_draft_taxonomy(value) -> dict[str,str] dùng is_valid_custom_category_id hiện có, yêu cầu Mapping ID/label chuỗi; strip nhãn <=100 ký tự, fallback ID.
- PackIngestService.commit(job, cancel, progress) giữ signature; optional job['draft_taxonomy'] mặc định {}. Return giữ pack_id/count/snapshot_synced/warnings.
- PackIngestWizard.effective_taxonomy() và CategoryDelegate provider đọc base+RAM draft. Existing base thắng, không đổi nhãn taxonomy có sẵn. Combo hiển thị nhãn kèm ID.
- _add_category không gọi storage.add_category. Cancel/reset bỏ draft; commit lỗi giữ draft và lựa chọn asset để retry; published success bỏ draft.
- Draft mapping chỉ ở job-level; không thêm vào asset registry hoặc DTO. StorageManager.add_category và các caller khác giữ nguyên.

## Publish/rollback
1. Core validate draft, capture root/library ID; validate staging/chosen files.
2. Dưới lock: recheck identity, đọc taxonomy/registry mới nhất. Với draft nonempty, taxonomy hỏng/symlink bị từ chối; không lấy default để ghi đè.
3. CID concurrent cùng nhãn được reuse; khác nhãn bị từ chối trước mutation. Merge chỉ new IDs.
4. Pre-serialize/check limits của registry/manifest/taxonomy, ensure_space và cancel checkpoint.
5. Ghi manifest/prune staging -> rename payload -> ghi merged taxonomy nếu có thay đổi -> publish registry.
6. Lỗi trước publish: thử restore taxonomy nếu vẫn bằng merged của job và đúng identity; thử rename folder về payload độc lập. Nếu restore/rename lỗi, giữ primary cause và báo “Chưa khôi phục hoàn toàn”, không báo success.
7. Snapshot sau publish best-effort: lỗi -> success kèm snapshot_synced=False/warnings; không rollback category/pack đã publish.

Đây là multi-file workflow có khóa và best-effort rollback, KHÔNG phải formal two-phase commit/ACID hoặc power-loss durability guarantee. SIGKILL giữa taxonomy write và registry publish có thể để lại category chưa dùng; journal/recovery chưa triển khai. Không overwrite taxonomy thay đổi bên ngoài khi rollback.

## Kiểm chứng
Test đỏ trước implementation: 11 failed,7 passed trên hai file test mới (sau sửa tên file UI để tránh pytest module-name collision).
Nhóm sau implementation:
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_pack_draft_taxonomy.py tests/test_storage_ingest.py tests/core/test_pack_ingest_transactions.py tests/ui/test_pack_ingest_wizard.py tests/ui/test_pack_draft_taxonomy_ui.py -q
63 passed in18.99s, exit0.

Bao phủ publish, validation, taxonomy/registry write faults, rollback lỗi kép, external taxonomy change, concurrent ID, snapshot error, empty draft no taxonomy write, corrupt taxonomy, cancel/identity change, UI cancel no writes, delegate choices, commit/retry giữ draft.
Chưa kiểm chứng desktop thật, rút ổ vật lý, SIGKILL/power loss hoặc Studio hardware-worker lifecycle. Không đụng network/models/Resolve.
