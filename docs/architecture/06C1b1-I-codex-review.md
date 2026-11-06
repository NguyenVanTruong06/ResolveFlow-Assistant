# Review G06-06C1b1-I — CHANGES_REQUESTED

2026-10-29. Report SHA256 `ca645758c341322fb7649e8781f54b849791fb8dd633cf1e6017e05e4008cb53` verified.
Codex fresh focused suite: **49 passed in0.57s, exit0**, đúng proposal+creative context files. Không app/model/Resolve. Probe artifact: `.bridge/ux-evidence/G06-06C1b1-I-CODEX-REVIEW/probes.json`.

Positive controls: sửa global_hook đổi revision; sửa raw kinetic word sau normalize không ảnh hưởng owned snapshot. Publichelpers revalidate và export/hash giữ tám fields, no approval authority như scope.

## A — Raw schema và provenance chưa đúng workorder

- `_validate_and_own_video_plan` chỉ kiểm tra sum raw inserts khi cả hai là list/tuple; nếu một field=False/None/dict/string thì bỏ qua guard và generic JSON own vẫn nhận. Probe brollFalse vàsfxNone đều accepted. Khi field có mặt phải typegate list/tuple trước len và tổng<=4096, không đổi falsy thành empty.
- Assignment pack_id=[] hoặc source_id=23 được export/hash bình thường. Workorder yêu cầu khi có phải nonblankstr hoặcNone. Bổ sung đúng hai gates này và giữ origin_library_id rule hiện hữu; không épkiểu hoặc bỏ metadata sai để giả valid.

## B — Full JSON fidelity và node precheck

- `_own_json_value` bắt mọi mappingkey nonblank: extra={'':'valid JSON metadata'} bị ValueError. Empty/whitespace string keys là JSON hợp lệ, không phải ID. Contract phải giữ full raw Slot1/Slot2/source/supplementary metadata; chỉ string/UTF8/NUL gates, không áp IDnonblank cho generic keys. Root allowlist/required keys và domain IDs vẫn nghiêm ngặt.
- Generic containers chưa có len precheck trước iteration. CountedList100001 children trong video_plan.extra bị enumerate99983 lần trước khi nodecounter raise; workorder yêu cầu check cấu trúc trước loop/enqueue. Cần dùng minimum node cost/remaining budget để reject container chắc chắn không thể fit trước đọc children; equality vẫn accepted khi toàn snapshot fit. Giữ active_ancestors cho DAG vs cycle, không memo hóa để bỏ qua chi phí repeated occurrences.

## Acceptance hữu hạn

Đóng đủ A/B trong module và test file hiện tại, không tạo feature/parser/geometry mới. Tests cover raw/direct/typed helper behavior, both insert fields (badtype/equality/overflow precheck), optional provenance type, genericemptykeys roundtrip+hash và CountedList/CountedMapping impossible node container0iteration. Giữ các tests512KiB/equality, Unicode, DAG/depth, purity, ownership và C1a regression. Chưa nghiệm thuC1b1, chưa approval factory/UI/execution. Không mởD mới hoặc hỏi lại userD01–D03.
