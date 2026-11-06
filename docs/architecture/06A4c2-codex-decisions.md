# G06-06A4c2 — Codex design decisions, 2026-10-29

**APPROVED_WITH_BINDING_CORRECTIONS**. Report đã đọc và hash khớp `94d962f163d713a45932ee14c5302a32f5c85101f126f9c6d690f47080afe1e1`. Review này chỉ đối chiếu source/docs; không chạy pytest/app/Resolve. Pure formatter giữ nghiệm thu tại `06A4c-core-acceptance.md`.

## 1. UX và thứ tự thực hiện

Chọn explicit async copy một lần bấm. Overview áp dụng toàn snapshot, nên đổi selected batch đơn thuần không làm stale. Chưa triển khai nút ngay: lát cắt **06A4c2a-I** chỉ worker và artifact do app sở hữu, một production module. UI/copy và macro guards sẽ là consumer sau review worker. Không phải một vòng thiết kế mới.

## 2. Owned artifact thay cho result tự chứng minh

Trace 7 của thiết kế chỉ phát hiện text bị sửa nhưng hash chưa sửa. Nếu prompt/hash/bytes cùng bị thay, ledger signature vẫn đúng và thiết kế hiện tại có thể chấp nhận prompt khác. Cần một **PreparedProjectOverviewArtifact** immutable được worker lưu riêng trước terminal success; payload signal là bản truyền riêng. Getter gắn với đúng worker/request. Consumer sau lấy expected content từ artifact của worker mà pending đã sở hữu, không dựng artifact từ dict terminal hoặc dùng hash dict làm expected hash.

Worker cung cấp `validate_project_overview_result_against_artifact(result, artifact)` để consumer dùng lại kiểm nội dung/envelope. Điều này bảo vệ ranh giới message/callback của app, không phải chữ ký chống kẻ đã kiểm soát Python process.

## 3. Ledger signature và app capture

Không chỉ gọi `str()` hoặc tin type frozen. Canonical signature của current snapshot phải bao quát semantic entry content, IDs, source ranges, binding mode, raw/draft digests và cấu trúc received set, không chỉ cặp ID/digest rồi bỏ qua entry bị sửa không cập nhật digest. Loại timestamp audit `received_at` để không vô hiệu hóa do audit-only edit. Core formatter vẫn kiểm semantic digest/geometry; signature không thay validator.

Thêm helper public trong worker module `compute_project_overview_ledger_signature(snapshot_id, entries) -> str` cho UI tương lai dùng cùng policy. Canonical JSON/UTF-8/domain version, raw IDs giữ nguyên, strict validation trước sort/hash, invalid -> ValueError. Không encode bằng delimiter join. Missing/added/deleted/replaced entries thay signature. Old snapshot entries phải được UI lọc trước; helper không silently nhận entry snapshot khác.

## 4. Nguồn thực tế và pre/post recheck

`prepare_batches` tạo `baseline_sources` từ các paths có stat evidence (:1685–1697), không chỉ từ planned_clip_ids. Vì vậy không thể nói baseline và unaccounted là hai tập loại trừ nhau.

Worker overview kiểm **mọi captured baseline** trước và sau format: regular file/readable, size/mtime_ns/ctime_ns/inode/dev; ordered normalized paths khớp request. Không bỏ một baseline vì clip tương ứng chưa có duration/draft. Không tự tìm/stat thêm đường dẫn từ unaccounted IDs hoặc tải manifest/cache để suy path. Một unaccounted record ngoài baseline không tự chặn partial; baseline đã có mà đổi/mất thì từ chối trung thực. Không rehash media/inference, không source IO trên GUI.

## 5. Request/adapter và chi phí GUI

Snapshot SSOT thật là `PreparedBatchSnapshot`, `PreparedBatchItem`, `BaselineSourceEntry`, `UnaccountedClipEntry`; không có các classes OwnedPreparedBatchSnapshot/PreparedBatchRecord/PreparedBatchBaselineSource/UnaccountedClipRecord được design gắn CODE_VERIFIED. Slots dữ liệu thật là `_video_plan_data`, `_kinetic_plan_data`, không phải `_plan_v1_loaded`/`_plan_v2_loaded`.

Bỏ claim GUI capture <1ms chưa đo. Adapter dùng accessor đúng dict/DTO, không `getattr(...) or str(u.get(...))`: empty reason/message hợp lệ và None/sai kiểu phải reject, không ép thành chuỗi. Core constructor sở hữu nested ranges nên không gọi nó thao tác O(1). Heavy render/semantic validation/source stat chạy worker. Current request giữ các expected identity/context fields đã có; overview context, snapshot geometry/unaccounted/style/budget phải được đối soát, không có hai SSOT độc lập có thể lệch nhau.

## 6. Macro JSON là prerequisite trước UI exposure

Source `parse_web_prompt_json(..., target_slot='slot1'/'slot2')` hiện nhận object đầu tiên sau batch guard mà không kiểm macro kind (:360–375). Macro plain không có batch_id nên guard hiện tại không đủ. Sửa riêng intake validator không bảo vệ Slots 1/2 và không phải prerequisite chính xác.

Trước khi mở nút copy overview, cần bounded guard cho `kind='project_overview_draft'` ở parser/direct ReviewPage slot boundaries, giữ last-good V1/V2/config và thông báo chưa hỗ trợ importer. Bảo toàn legacy batch payload không có kind; không tự áp schema batch_draft_v1 bắt buộc. Ghi prerequisite này trong backlog consumer kế tiếp; **không sửa director_state/core validator/UI ở 06A4c2a-I**. Worker-only không đưa thêm macro-copy route ra UI.

## 7. Artifact và lifecycle

Artifact chỉ publish sau normalize/cross-binding/ledger proof, render thành công, source post-check và cancel check. Fail/overflow/cancel không có artifact. Bảo toàn _emit_terminal exactly-once và finished vật lý; getter không được trả artifact nếu worker đã cancel. Additive intent không làm chạy phase chung đọc cache/model. Future UI phải so request/generation/current snapshot/context/ledger, strict envelope/content với artifact thật, rồi mới một setText; không fallback owned pending/proof từ result, không nested loop.

## 8. Slice và acceptance hữu hạn

Giao **G06-06A4c2a-I**, chỉ `prompt_preparation_worker.py` + tests mới/existing có liên quan + docs. Test actual QThread, success partial/extra unaccounted; strict missing bindings trước IO; context/snapshot geometry disagreement; source mutation sau format; cancel/no artifact; owned-artifact vs forged same-envelope prompt/hash/bytes; immutable captures và existing intake compatibility.

Chưa runtime/desktop trong D review, chưa số đo hiệu năng/inference mới. UI/busy/clipboard/macros được thiết kế nhưng chưa code; 06B/06C/G07, publication, persistence và shutdown ngoài phạm vi.
