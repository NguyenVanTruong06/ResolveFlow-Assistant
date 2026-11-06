# G06-06A2-I — Codex review, 2026-10-28

**CHANGES_REQUESTED — G06-06A2-R1.** Không nghiệm thu code adapter, chưa mở integration.

Đã đọc report, module và tests, đối chiếu build_clips_data, VisualIndex/VisualDescriber và A1. Fresh finite suite: 62 passed in 0.70s, exit 0. Tests hiện tại chưa bao phủ các counterexamples dưới đây; không phủ nhận kết quả 62 tests.

## A — Authority phải có bằng chứng, không default nâng quyền

- batch_evidence_adapter.py:371 mặc định source_availability='available'. Xóa field khỏi row speech/visual hợp lệ vẫn trả 2 windows.
- :558–562 segment thiếu analysis_status được nâng thành described; :542–544 thiếu origin thành unknown nhưng vẫn usable. Parent status='pending' cũng lọt vì chỉ dùng denylist (:540).
- :595–607 machine thiếu end_sec nhưng tự khai observation_kind='point' vượt kiểm tra range và được cấp point window.

Sửa theo positive gates: source chỉ available khi khai báo available; machine cần parent status có bằng chứng thật và origin/status explicit hợp lệ. Không string-coerce origin/status, không phát minh described/needs_review. Giữ user notes có xuất xứ hợp lệ khi machine stale và nguồn available; missing field không chứng minh source available. Machine thiếu range phải bị loại có issue, không chuyển thành point để qua bounds.

## B — Validation trước gating và giữ nguyên evidence

- :230 cho thiếu/null manifest_id trong row dù hợp đồng bắt exact match.
- :371–373 source_availability=None gây AttributeError (.upper); dialogue interval thiếu start gây TypeError tại :491. Phải ValueError có kiểm soát cho schema sai, kể cả khi stale/source unavailable/unknown duration.
- duration_known='yes', visual_samples=[], path khác manifest.current_path đều được nhận. Các flags/counts/strings/coverage/segment origin/status/objects/actions/evidence_times và quarantine thiếu validation theo known fields.
- description=None thành literal 'None' qua :576; text/description bị .strip() tại :476/:576, trái yêu cầu verbatim. Chỉ dùng strip để xác định blank, output giữ raw string chính xác; tuyệt đối không str(None/object).
- Manifest duration âm đang thành unknown thay vì reject theo A1; file_name/content_hash chưa validate như A1. AdapterIssue numeric bigint có thể OverflowError tại :68 (public DTO cũng cần controlled validation).

Validation theo allowlist semantics đã giao, bỏ qua runtime unknown fields không repr/deepcopy/str. Không bắt buộc mọi optional field phải xuất hiện; field hiện diện phải đúng kiểu/shape, required identity/timing phải có. Source missing/changed vẫn chặn toàn bộ evidence sau validation. Numeric finite out-of-bounds tiếp tục structured issue không clamp; schema thiếu/sai kiểu là ValueError.

## C — Audit và signature mất nội dung cần thiết

- :705–727 chỉ hash usable windows/summary. Đổi visual_coverage, visual_analyzed_coverage, visual_stale_excluded_count, segment.objects hoặc producer.window_id không đổi context_signature. Original window refs/objects/actions/evidence_times không có trong audit dù đã yêu cầu.
- Nhánh missing row :318–341 và source unavailable :381–404 bỏ supplied_signature khỏi canonical data: context_metadata analysis_signature A→B không làm đổi snapshot signature ở missing-row probe.
- :734 dùng supplied opaque signature thay generated analysis_signature. Khi giữ supplied='opaque' và đổi text, consumer vẫn nhận cùng analysis_signature. Phải generated semantic signature trong analysis_cache; supplied chỉ là reference riêng trong audit và là thành phần của generated hash.

Hash semantic allowlist đã validate, gồm manifest/source identity metadata, declared authority/status, coverage hai domain riêng, provenance/record refs và supplied context/config theo mọi nhánh. Không IO/file verification, không tạo coverage ratio hay complete giả. Audit chỉ giữ usable text nếu contract cần; text stale/quarantine tuyệt đối không lộ qua audit/issues/messages. Rejected content có thể chỉ giữ digest/reference. Freeze nested audit thật sự, không caller alias.

## Bằng chứng / giới hạn

Counterexamples chạy in-memory trên pure adapter, không Qt/model/Resolve/IO nguồn. Fresh suite log và probe log tại .bridge/ux-evidence/G06-06A2-I-CODEX-REVIEW/. Không sửa production/tests trong review. A1 accepted giữ nguyên; 06B/06C/runtime wiring tiếp tục deferred. Không shutdown.
