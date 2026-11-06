# G04-04C6b-D — Codex decisions (2026-10-27)

ACCEPTED_WITH_BINDING_CORRECTIONS. Đã đối chiếu hover_preview, asset_model, asset_cache.scan_folder_cached và AssetIndexer.scan_transitions. Không chạy tests/app trong review thiết kế.

## Quyết định 3 câu hỏi
1. Cho phép fallback master cho audio/video được hỗ trợ sau xác thực; không áp ngưỡng MB tùy tiện. Debounce và một player chỉ giảm số lượt khởi động đồng thời, không bảo đảm RAM/GPU/decoder performance của master lớn. Hiệu năng chưa đo giữ UNVERIFIED.
2. Proxy ngoài authority hiện có DEFERRED. Không bắt buộc SHA-256 hoặc database mới để giải quyết ngay; mapping ownership cần thiết kế riêng. Linked source/companion và file-backed generated có quyền riêng, không đồng nhất “ngoài StorageRoot” với cấm toàn bộ.
3. Core guard chọn candidate/fallback, UI chỉ phát resolved_path. Chỉ fallback sau identity/metadata gates thành công; root/ID mismatch, staging, origin-only hay provider failure không được bị nuốt bằng fallback builtin/master.

## Binding corrections
- API guard nhận Mapping; AssetObjRole trả dict hoặc domain object. Hover phải shallow-normalize mà giữ provenance/native refs và paths; không gọi extract_asset_info làm DTO quyền nguồn. Đừng import asset_grid vào hover vì asset_grid đã import hover (cycle).
- Bundled root là assets thực của app, không chỉ assets/templates; bao gồm SFX/BGM/memes/overlays. Root lấy theo vị trí code/config ứng dụng đáng tin, không cwd hay origin_root từ asset. Unit test inject/patch boundary này rõ ràng. Legacy transition demo từ indexer chưa có source_kind nên nhận diện dựa paths bundled, không buộc nguồn mới để chữa test.
- Linked companion không phải mọi file cùng folder: producer hiện dò cùng stem với master .mp4/.mov/.gif. Verify anchor external_path, conflict với file_path và candidate same-parent/same-stem sau containment/realpath; không cho arbitrary preview path do metadata. Legacy/generated master hợp lệ giữ behavior, preview riêng chỉ đồng nhất master hoặc companion có quan hệ đã mô tả; builtin cần containment trong bundled assets, không bare isfile.
- Managed preview có thể khác master và master mất: chỉ allow nếu identity/current root, safe relative ownership và candidate containment đạt. Không gọi insert/drag để quyết định preview (native guidance và procedural sẽ trả allowed mà chưa kiểm file). Known managed không được dùng builtin exemption để né root mismatch.
- Canvas không dựa category text: chỉ domain TextStylePreset thật và không có managed ownership; missing managed text phải theo preview authority, không giả canvas success.
- status trên preview success biểu thị action capability, không sửa asset availability hay suy ra master online. Không invent state mới.
- Thiết kế có nhãn CODE_VERIFIED cho adapter _wrap_record_to_studio_asset không đúng tên hiện tại (_dict_to_studio_asset); sửa citation. StorageManager không tự sinh proxy không có nghĩa mọi record không thể có preview_path: item=dict(asset) giữ trường có sẵn. Không gọi read-file-media threat proof hoặc crash nguyên nhân chỉ từ isfile; ghi đúng mức path mismatch/authority gap.

Phê duyệt slice G04-04C6b-I đúng 2 modules action_guard.py + hover_preview.py. Inspector/legacy và offline proxy mapping vẫn deferred. Scope kết thúc ở focused tests, desktop chứng minh sau; không mở lại drag/insert accepted ngoài regression.
