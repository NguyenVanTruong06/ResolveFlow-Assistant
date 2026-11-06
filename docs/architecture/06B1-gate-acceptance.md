# G06-06B1 — ACCEPTED_WITH_RECORDED_LIMITS

2026-10-29. Report R2+design `857d4f6fb9e4c1b35a5f1bf255cd096017fc14b59dcfc0f988b0590a5f34afcc` đã đọc và hash xác minh. Đóng gate **06B1**; không phải đóng toàn G06/06B.

## Bằng chứng
- Codex đọc contracts/matcher và tests, chạy fresh `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_offline_asset_matcher.py tests/core/contracts/test_asset_dto.py -q`: **82 passed in 0.48s, exit0**.
- Reprobe residuals: surrogate id/source_id quarantine, good row sống; bad category/schema_version diagnostics UTF-8 an toàn và shuffle-stable; non-request full/partial namespace và None/dict/list/object đều ValueError cả kho rỗng/không rỗng. Positive `_raw_obj` ignored và exact stem score65 giữ nguyên.
- Artifact `.bridge/ux-evidence/G06-06B1-R2-CODEX-REVIEW/verification.json`; Worker finite log `.bridge/ux-evidence/G06-06B1-R2-THEN-06B2-D/pytest_run.log`.

## Phạm vi nghiệm thu
Hai pure modules `src/core/contracts/asset_matching_contracts.py`, `src/core/offline_asset_matcher.py`; owned consumed metadata, schema/quarantine/duplicates, lexical ranking, advisory references/path-free output, four-state eligibility, hard duration filters, bounded deterministic diagnostics và strict request boundary theo bindings06B. Ba nhánh cuối A/B/F đã đóng, không mở thêm polish06B1 trong workorder kế tiếp.

## Giới hạn giữ nguyên
Chưa UI/worker/recipe integration; fixtures in-memory, không desktop/Resolve/media/inference/Drive trong lần review này. Metadata availability không chứng minh file tồn tại/quyền thực thi; current ActionGuard vẫn bắt buộc ở action-time. Scoring lexical không semantic vision/confidence. Unknown duration không qua duration constraints. Native transitions chỉ guidance, không có API tự gắn tại cut trong phạm vi này. Không benchmark CPU/token/latency hoặc khả năng phân biệt preview không có metadata provenance. Không nhận arbitrary custom Python objects có magic methods làm dữ liệu JSON tin cậy. Catalog metadata rất lớn chưa có hard CPU/memory budget toàn payload, chỉ bound số rows/alias/output diagnostics theo contract.

Next: 06B2 snapshot-to-matcher service theo `06B2-codex-decisions.md`; 06C/G07 và LIB-PUBLISH-03 chưa giao. 06A/Drive PUBLISHED_VERIFIED/backups không mở lại; không shutdown.
