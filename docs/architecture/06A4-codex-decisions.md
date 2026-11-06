# G06-06A4-D — Codex review / binding decisions, 2026-10-28

**ACCEPTED_DIRECTION_WITH_BINDING_CORRECTIONS.** Cho phép duy nhất first slice **G06-06A4a-I: Safe Batch JSON Slot Guard** trong director_state.py + ReviewPage. Chưa cấp quyền viết full batch receiver/ledger/summary. Sửa thiết kế các điểm dưới trong cùng task I; không thêm vòng D riêng.

## Quyết định chính sách

1. Dedicated batch intake được duyệt hướng kiến trúc; generic V1/V2 paste phải reject batch drafts trước target-slot classification và trước mutation. Guard chỉ bảo vệ routing, không có nghĩa draft đã valid/received. Receiver chưa có thì thông báo đúng chức năng hiện tại, không bảo user bấm nút chưa tồn tại.
2. Nhận draft không auto replace Slot1/2. Explicit master synthesis/publish là scope sau; không tạo nút Master giả khi chưa có implementation.
3. In-memory MVP được duyệt cho receiver tương lai. Ledger disk deferred; session_root trong harness là temp fixture, không phải project durable owner/path đã chốt. Phải có project/cache owner cấu hình thực trước persistence.

## Điểm đúng và corrections

- CODE_VERIFIED: parse_web_prompt_json target_slot explicit đang chạy trước type detection; batch có timeline_segments thành video_plan. Auto/dual/nested wrappers cũng có đường vào; chỉ guard root sau target branch sẽ không đủ. _apply_json_data gán _video_plan_data, direct parsed payload có thể bypass parser; cần guard final boundary trước manifest/slot/signals.
- StoryCopilot parser manual construction lấy clip_index default1, không truyền clip_id vào DTO dù DTO có field clip_id optional. Vì vậy draft clip_id-only có nguy cơ sai source; không sửa toàn parser/converter trong guard slice.
- Bác min duration0.1s/epsilon0.05s/clamping: giữ exact0<=start<end và within approved source interval, no epsilon; short legitimate intervals không tự bị loại chỉ vì dưới0.1. Schema sai/nonfinite/overflow controlled reject, không cast/bool hoặc action coercion.
- PreparedBatchItem hiện có clip_ids và rendered prompt, KHÔNG có typed source intervals/window refs; thiết kế truy cập item.source_in_sec trực tiếp là phantom. Full receiver cần immutable prepared geometry được producer đưa sang, hoặc một decoded authoritative envelope có provenance/schema/digest được chốt. Không dùng whole ClipManifest bounds thay partition, không trusting response timestamps tự làm expected geometry. Nếu cần worker là module thứ4 cho vertical slice thì nêu justification; không giấu dependency bằng pure core import UI/Qt.
- Prompt06A3 hiện yêu cầu manifest_id,batch_id,summary,timeline_segments nhưng không cung cấp snapshot_id/source_window_ref và không echo speed field bắt buộc. Không bắt AI trả proof chưa từng thấy. Chốt migration/binding cho future receiver: expected snapshot app-owned, optional response echo chỉ valid khi được cung cấp; thiếu echo không được claim chống stale across-repartition. Nếu thêm proof vào prompt, tránh circular snapshot_id chứa render_digest trong khi rendered prompt lại chứa snapshot_id. Không append proof ngoài budget.
- source_window_ref optional nếu nguồn không có eligible windows (noaudio/unscanned/user point), nếu có phải validate ref thuộc expected batch/source. Không invent W_* IDs hay coi cuts là observations thật.
- Overview trimming note40chars/drop cuts không được tự coi là coverage đầy đủ. Explicit refs/omission/retained raw drafts, final UTF8 cap; unresolved oversize phải bounded hierarchy/refinement requirement. No silent semantic summary. Summary/overview không implement trong slice guard.
- Ví dụ1201s defaultwindow600 có3partitions (0..600,600..1200,1200..1201), không2. Muốn2 cần explicit config khác; không mock trái A1 default.

## Scope finite

06A4a-I: shared pure batch-plan detector + parser reject routing + final UI boundary guard + focused tests, không batch_draft_contracts module rỗng/scaffold. Bounded inspection của actual parsed plan containers, không substring search làm false-positive note chứa chữ batch_id. Batch marker malformed/missing-value vẫn không được rơi vào V1/V2. Mixed payload có batch không partial apply phần còn lại. Giữ V1/V2 legitimate behavior. Codex review này chỉ đọc source/docs, không pytest/app/Resolve/inference; no shutdown.
