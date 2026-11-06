# G06-06A4c-I — Codex review, 2026-10-29

**CHANGES_REQUESTED** trong một production module hiện tại. Chưa nghiệm thu pure formatter, chưa mở consumer UI/worker. 06A4b và các gate trước giữ nghiệm thu.

Report đã đọc, SHA256 đối chiếu: `f4e7ef11369c7beeefae1f720b216de29e8f90a7b29e54254d5bfdd3bf961bd9`.

## Đã đạt

- Module pure chỉ stdlib và core draft contracts. Partial/missing và all-batches/source coverage tách biệt; zero drafts/overflow có mã riêng; equality đúng; không truncate summary, không IO/Qt/Resolve.
- Codex chạy đúng finite suite: `tests/core/test_project_overview_contracts.py tests/core/test_batch_draft_contracts.py`: **43 passed in 0.31s, exit 0**.
- Digest integrity helper được dùng với `expected_source_ranges`. Đây là kiểm tra tự nhất quán theo context app cung cấp, không chứng minh approval/freshness của ledger; giữ boundary này cho consumer sau.

## A — P1: mất geometry và serialization không an toàn

`project_overview_contracts.py:347–350` dùng `.1f/.3f`. Một range hợp lệ `[0.0001,0.0004]` được render thành `0.000s -> 0.000s`. Vi phạm lossless bounds đã chốt. Bản đồ nguồn chính thức phải serialize số chính xác theo giá trị DTO, không làm tròn; human display phụ không thay authority data.

`:387–388` ghép manifest/snapshot ID vào JSON bằng f-string. Probe ID hợp lệ chứa quote/backslash/newline: renderer trả ok=True nhưng fenced JSON không parse được. Summary bọc triple quotes, metadata/warnings ghép thẳng vào text cũng chưa phải structured data có escape như contract. Cần serialize context và output example bằng `json.dumps(..., ensure_ascii=False, allow_nan=False)` hoặc tương đương; không sửa/strip ID để tránh lỗi.

`:403` encode UTF-8 chưa có controlled path: `project_name='\ud800'` làm `UnicodeEncodeError` thoát public renderer. Chuỗi không encode UTF-8 được phải bị từ chối `ERR_OVERVIEW_INVALID_CONTEXT`; không trả prompt lỗi hay silently thay ký tự.

## B — P1: nested validation và ownership còn dựa vào frozen/type hints

- `dataclasses.replace(valid_entry, batch_id=[])` rồi render -> `TypeError: unhashable type: 'list'` tại membership trước type gate (:262).
- `segments=None` hoặc `source_ranges=None` -> TypeError từ `d.to_dict()` trước helper (:302). Kiểm tra entry type không đủ.
- Context `__post_init__` (:110–117) dùng `tuple(value or ())`: explicit `unaccounted_clips=False` bị biến thành `()`, render thành công và có thể công bố `source_coverage_complete=True`. `received_drafts=False` bị phân loại nhầm NO_DRAFTS. False/0/empty mapping không phải tuple rỗng hợp lệ.
- Context chỉ sở hữu outer tuple. Với draft có `segments=list(valid_entry.segments)`, render ban đầu thành công. Caller clear list sau khi tạo context làm cùng context thất bại; context vẫn giữ nguyên entry alias. Phải own/reconstruct immutable nested DTO hợp lệ; không deepcopy runtime object tùy ý.
- Render recheck range mới kiểm float/time, chưa toàn bộ clip/index/partition/window types theo binding. Kiểm tra constructor không thay thế render-boundary validation với DTO sai/corrupt; vẫn không sửa core BatchSourceRange đã nghiệm thu.

## Bằng chứng tái hiện

`.bridge/ux-evidence/G06-06A4c-I-CODEX-REVIEW/residual-probes.json` gồm positive valid control và các ca trên. Toàn bộ probe pure, không app/Resolve, không sửa production. 43 tests xanh không bao phủ các residual này; report khẳng định zero unhandled exceptions quá rộng.

## Phạm vi sửa hữu hạn

G06-06A4c-R1: chỉ `project_overview_contracts.py`, test hiện có và design docs. Giữ tên API/result/schema policy, byte equality/overflow, no truncation, no source mutation. Gom lossless JSON serialization và full boundary/ownership vào một lần sửa; test positives và exact counterexamples trước khi chạy lại hai file finite. Hoàn thiện tags MEASURED_SOURCE_BOUNDS/AI_DRAFT_STRATEGY/USER_CHOICES, chú thích source bounds không re-stat, và chỉ dẫn output dùng received IDs trong cùng formatter. Không desktop/inference, không UI/worker integration, không mở lại A4b/core draft hoặc publication.
