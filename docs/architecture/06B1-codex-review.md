# G06-06B1-I — Review và acceptance R1

2026-10-29. Report `9ed117b1e6c4e17e8c4490b443fe6fda89c9059a21a3ee60078cb08d152f7fe3` đã đọc, hash khớp. **CORRECTIONS_REQUIRED**, chưa accept 06B1. G06-06A giữ ACCEPTED_WITH_RECORDED_LIMITS.

Codex chạy trực tiếp finite suite hai file quy định: **67 passed in 0.43s, exit 0**. Đọc cả hai production modules và các tests. Các test xanh chưa bao hết upfront acceptance. Bằng chứng probe: `.bridge/ux-evidence/G06-06B1-CODEX-REVIEW/probes.json` (positive fixture thực sự qua validate_asset_dict). Không sửa production/test trong review.

## Sáu nhóm cần sửa cùng một lượt

### A — Request boundary (P1)
`OfflineAssetMatchRequest.__post_init__` dùng math.isnan/isinf trên bigint: `min_duration_sec=10**400` thoát OverflowError thay ValueError. Lone surrogate query được nhận dù hợp đồng UTF-8. `match()` chỉ isinstance, không revalidate: DTO bị thay top_k=False hoặc query_text=[] trả unmatched thay controlled ValueError. Frozen không là proof.

R1: dùng chung validation ở constructor và match entry, trước category lookup/tokenization/catalog-empty shortcut. Validate tất cả trường request theo contract, UTF-8, bool/NaN/Inf/bigint, template constraints. Không normalize mất raw identity/query. Các input sai phải ValueError, không accidental Python exceptions; positive aliases/durations/score giữ nguyên.

### B — Intake persisted/runtime và ownership (P1)
`__init__` gọi validate_asset_dict trên nguyên runtime row: `_raw_obj=object()` khiến schema lỗi, trái policy ignore runtime extras. Ngược lại origin_library_id=['library'] không được validate, đi nguyên reference vào frozen candidate. Caller có thể mutate output provenance. Runtime master fields được sử dụng cũng cần validation riêng; fields không dùng không được động tới handle.

R1: tạo persisted validation view từ allowlist hiện hữu, giữ mọi known persisted field kể cả invalid (không strip để cứu record). Validate consumed runtime fields riêng, đặc biệt optional origin_library_id (absent/None hoặc nonblank UTF-8 str), availability và master path khi được dùng. Ignore `_raw_obj`/unused extras không deepcopy, repr, stringify hoặc gọi handle. Chỉ giữ owned immutable consumed metadata. Bản ghi sai quarantine, good rows còn nguyên; không sửa asset_dto. Bảo toàn raw ID/name/provenance, không str-coerce malformed data. UTF-8 gate consumed text để output/warnings serialize an toàn.

### C — Master/companion coordinates (P1)
`_has_valid_master_reference` so library_path (library-relative) trực tiếp với preview_relative_path (pack-relative), bỏ qua relative_path cùng hệ tọa độ. Probe managed relative_path='previews/demo.mp4', library_path='02_cloud_downloads/packs/p/previews/demo.mp4', preview_relative_path='previews/demo.mp4' vẫn matched. Builtin có relative_path/library_path bằng preview_relative_path cũng matched.

R1: so master và known preview/thumbnail references khi cùng hệ tọa độ, chuẩn hóa lexical separators/path components bằng stdlib, không IO. Managed so relative_path với pack-relative companions; builtin/generated so applicable references; linked explicit external master==runtime companion phải exclude khi quan hệ metadata chứng minh được. Không so basename đơn thuần, không chặn mọi MP4 hoặc mọi master có companion hợp lệ. Builtin/generated chỉ library_path (không file_path/relative_path) chưa đủ master theo workorder gốc. Không mở procedural fallback. Tests positive: distinct master .setting/.mp4 + preview, same basename khác dirs/packs, linked, transition guidance.

### D — Availability và hạch toán (P2)
Constructor chỉ nhận online/offline. Probe schema-valid availability='missing' bị tính rejected=1, ERR_CATALOG_INVALID. Workorder đã chốt bốn enum: missing/staging là trạng thái hợp lệ nhưng ineligible, không phải schema hỏng.

R1: validate đủ online/offline/missing/staging; missing/staging luôn filter out (kể cả allow_offline=True), không tăng rejected_count chỉ vì trạng thái. Catalog hợp lệ chỉ missing/staging trả ERR_NO_MATCH_FOUND; absent/invalid availability vẫn quarantine và có thể ERR_CATALOG_INVALID. Offline relevant vẫn ERR_ASSETS_OFFLINE; unknown duration vẫn hard-exclude khi có bounds. Không sửa upstream StorageManager phân loại trạng thái trong slice này.

### E — Scoring và alias determinism (P2)
Query 'Sunset.mp4' với name 'Sunset' trả 25 thay 65 do không strip known query extension. Alias keys 'Mood' và 'mood' normalize cùng key, last wins: cùng mapping đảo thứ tự làm query calm/tag rain đổi unmatched thành matched.

R1: exact compare dùng normalized name/query stems đối với known extensions, vẫn so raw-normalized asset ID độc lập (không strip ID); không thay token weights. Reject normalized alias-key collision bằng controlled ValueError, không last-wins; UTF-8 validate keys/terms. Default empty aliases, limits64/32 giữ nguyên. Bảo toàn exact tag-only15, alias-only15, cap100, threshold15, tie-break hiện hữu; dùng assert exact score thay >= ở fixtures có oracle rõ.

### F — Bounded deterministic diagnostics (P2)
Probe chỉ100 invalid rows có ID2000chars tạo 100 warnings/~246990 UTF-8 bytes; hiện không có cap. Row indexes làm warning tuple phụ thuộc catalog order, trái upfront shuffled-input invariance.

R1 binding cụ thể: toàn bộ warnings tối đa64 entries và tổng16384 UTF-8 bytes (tính cả marker lược bớt); truncate codepoint-safe, diagnostic ordering độc lập input order; stable reason/ID summaries, không dump raw object/path/text lớn. Tổng rejected_record_count luôn exact dù warnings lược bớt; marker Việt minh bạch có lược bớt. Duplicate-ID toàn nhóm vẫn quarantine và cùng counts khi shuffle. Chỉ bound returned diagnostics, không tuyên bố matcher CPU/memory bounded cho mọi metadata kích thước.

## Gate hữu hạn sau R1
Hai production modules hiện hữu + tests matcher + design/report. Gom cả sáu nhóm và positive controls thành một report. Chạy đúng hai test files matcher và asset_dto, không UI/full suite/inference/Drive/Resolve. Log red→green có tên case thật; cập nhật docs đúng behavior. Không đổi APIs/weights, không nới asset_dto schema, không tích hợp consumer. Khi đạt toàn bộ checklist, review đóng 06B1 rồi giao design06B2; không chia thêm task từng field hay mở lại06A. No shutdown, Worker HIGH.
