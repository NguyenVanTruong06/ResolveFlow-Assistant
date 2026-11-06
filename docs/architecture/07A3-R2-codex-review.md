# G07-07A3-R2 — Review độc lập, 2026-11-02

**Quyết định: CHANGES_REQUIRED.** Xác nhận đầy đủ 48 ca sửa đã báo cáo; còn ba điểm của checklist R2.
Task tiếp: **G07-07A3-R3**, HIGH, thu hẹp còn hai production modules service/readiness.
PreflightWorker giữ frozen trong R3. Chưa nghiệm thu 07A3/M1/G07; không mở tính năng mới.

## Bằng chứng mới và phần đã đạt

- Report SHA-256 khớp: `14ec15ce771baea36b5a6d435f5e8c0217358cdfc9f41b8a83bbb1c627a88447`.
- Codex chạy lại năm files quy định: **205 passed in 4.13s, exit 0**.
- Copy nguyên hai script review cũ sang thư mục mới, chạy lại độc lập:
  **23/23 original probes + 25/25 residual probes đạt**, exit 0.
- Dữ liệu trong `.bridge/ux-evidence/G07-07A3-R2-CODEX-REVIEW/`:
  `pytest_run.log`, `probes.json`, `residuals.json` và hai scripts/log tương ứng.
- Kiểm tra phần còn lại trong checklist R2 bằng `final_checklist.py`:
  **14 ca: 3 controls/deny đạt, 11 kết quả lệch yêu cầu**. Xem `final_checklist.json`/`.log`.
  Script exit 0 là hoàn tất thu thập, không phải tất cả assertions sản phẩm đạt.
- Không sửa src/tests của Worker, không Resolve/model/network, chỉ temp fixtures và child scripts riêng.

Đã xác nhận những sửa R2: strict numeric frame data; 16KiB string record bị chặn; reader exception/error output
không còn CFR; cache explicit không làm sai default; public invalid budget/item bị chặn; result clone envelope;
validator dùng chung core/worker chặn path/stream/SHA/freshness sai; master/registry đổi trong deep scan bị chặn.
Tài liệu đã chuyển sang IMPLEMENTED_PENDING_REVIEW và ghi deferred đúng.

## 1. P1 — JSON grammar vẫn có đường accept dữ liệu sai

Nguồn `source_evidence_service.py:_feed_text` (khoảng dòng 480–657).

| Ca | Quan sát | Đúng phải là |
|---|---|---|
| grammar_positive | VERIFIED_CFR | VERIFIED_CFR |
| missing_colon | `{"frames" [...]}` vẫn VERIFIED_CFR | HEADER_REPORTED_ONLY |
| trailing_array_comma | `{"frames":[{...},{...},{...},]}` vẫn VERIFIED_CFR | HEADER_REPORTED_ONLY |
| leading_garbage | `garbage {"frames":[...]}` vẫn VERIFIED_CFR | HEADER_REPORTED_ONLY |
| nonrecord_array_value | Bị từ chối | Giữ hành vi deny |

Root cause: parser vẫn là các flags/dấu ngoặc; không có trạng thái bắt buộc colon, đầu input,
và phân biệt sau comma phải có record với vị trí cho phép đóng array. Phần cuối `_feed_text`
bỏ qua ký tự không khớp thay vì reject. Đây là yêu cầu “JSON grammar/clean EOF” đã giao ở R1/R2,
không thêm schema mới. Cần một grammar/state machine hoàn chỉnh cho envelope `frames`, giữ bounded per-record.

## 2. P1 — Public precheck/required fields vẫn hạ cấp im lặng

Nguồn `source_evidence_service.py:acquire_evidence_for_batch` (khoảng dòng 890–926).

- `oversized_public_precheck_visits`: CountedList 4097 items được iterate/clone **4097 lần**,
  rồi mới ValueError ở constructor sau. Yêu cầu <=4096 precheck phải xảy ra trước iteration, visits=0.
- Xóa từng instance field `timeout_per_file_sec`, `max_decoded_frames`, `stdout_budget_bytes`,
  `stderr_budget_bytes`: public entry vẫn thành công và gọi hai stats, không controlled ValueError.
- Xóa từng instance item field `stream_index`, `expected_full_sha256`: cũng vẫn thành công/hai stats.
  Missing explicit stream/SHA bị biến thành None/default, làm mất ý nghĩa yêu cầu nguồn ban đầu.

Root cause: owned clone đang làm `getattr(..., DEFAULT/None)`; dataclass còn có class default nên
`hasattr/getattr` có thể không phát hiện instance field đã mất. Constructor hợp lệ luôn ghi đủ các field,
kể cả explicit None. Cần kiểm tra field hiện diện trong instance trước clone, không dùng default để sửa request hỏng.
Khác với việc cho phép None có chủ ý trong một object hợp lệ.

## 3. P1 — Config/context swap trong deep scan chưa bị chặn

Nguồn `source_catalog_readiness.py:inspect_source_and_catalog_readiness`, pha 3.3 trước deep
và recheck mới sau deep (khoảng dòng 1550–1636 / 1739+).

Positive catalog/source-only binding đều đạt. Controlled producer đổi **file config thật trên đĩa**
sang root/library khác trong pha deep, giữ master/marker/registry cũ nguyên vẹn:
`config_swapped_during_deep` vẫn **checks_passed=True, diagnostics=[]**.

Root cause: recheck config và storage_context_reader chỉ chạy ở pha 3.3 trước deep;
sau deep mới thêm marker/registry/master. Yêu cầu R2 D nêu rõ config/storage context cần thiết theo policy cũ.
Cần dùng lại toàn bộ final catalog authority check sau pha deep, không chỉ thêm từng file đã có trong probe.
Giữ metadata default zero new media reads và cancellation trong loops.

## Ranh giới sửa còn lại

R3 chỉ sửa `source_evidence_service.py`, `source_catalog_readiness.py` và tests core liên quan;
PreflightWorker/geometry/UI giữ nguyên. Không rollback các 48 ca đã xanh.
14 ca cuối và root-cause matrix của ba điểm trên là phạm vi sửa hữu hạn; không yêu cầu fuzz toàn repo,
không benchmark media lớn, không mở preapproval/UI/executor hay thay approval/proposal schema.
Sau khi đạt mới xem xét nghiệm thu backend 07A3 và mở handoff M1 kế tiếp.
