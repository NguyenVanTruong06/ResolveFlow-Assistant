# G06-06A1-I — Codex review, 2026-10-28

**REVIEWED_NEEDS_FIX.** Giữ hướng pure module; chưa nghiệm thu contract partition/budget.

Đã đọc report, toàn module619dòng và tests. Fresh command PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_batch_planner.py -q: **21 passed in0.29s, process exit0**. Không fullsuite/app/model. Probe stdlib/in-memory thêm được ghi tại .bridge/ux-evidence/G06-06A1-CODEX-REVIEW/probe-results.json.

## A — Hard budgets / exact output chưa đúng (P1)

- Rollover byte bug: single_candidate được đo với len(batches) trước khi flush. Sau flush item chuyển sang batch index kế tiếp nhưng không đo lại cap. Với11clips1s, max_clips_per_batch1 và limit414bytes:10batch414bytes, batchcuối415bytes vẫn trảsuccess. Chi tiết probe dùng limit đo từ batch10 của baseline nên không dựa magic overhead.
- Hard duration có tolerance +1e-6: hai clip0.5000004s, hard=target=window1s cho1batch1.0000008s, vượthard. Phải cộng/so sánh bằng số học rational/decimal-safe, không EPS nới trần.
- Renderer round(...,6) sửa source bounds/window times. Source_out1.1374999 xuấtJSON1.1375, làm mất tie/frame authority đã chốt. Fraction ở partition nhưng round lúc export vẫn sai. Dùng số lossless của contract hiện hành, không round6. Project name và sampled content_hash đang không được bảo toàn trong batch envelope/item như workorder; giữ metadata riêng, không lẫn fullSHA.

## B — Signature không đại diện nội dung/config (P1)

_compute_batch_content_signature chỉ ghép ID/time/status và fullSHA, bỏ speech_summary, visual_action, origin/is_sampled, analysis_signature và config. Probe đổi speech text A→B: render khác nhưng signature bằng nhau. Ghép prefix/suffix không framing cũng không phân biệt ranh giới. Cần canonical rendered payload + exact config/supplied context, loại self-reference count/signature; không coi hash này là proof file/analysis verified hay perfect resume.

## C — Validation/coercion/mutable alias (P1)

- manifest_id=None bị str() thành 'None' và accepted; duration/window text/count/flags cũng ép kiểu trước khi kiểm. start=False/end=True/is_sampled='false' thành window hợp lệ, sampledTrue.
- analysis entry=[] gây AttributeError; config big-int10**400 gây OverflowError. Contract yêu cầu ValueError có reason, không lỗi tình cờ hoặc nhận dữ liệu sai kiểu.
- EvidenceWindowSummary public constructor chưa validate text/flags; truyền speech_summary=['A'] giữ reference list. Sau khi plan trả về, caller append('B') làm rendered output đổi, recordedbytes631 nhưng actual635. Frozen outer dataclass chưa đủ immutable; validate cả Mapping lẫn DTO input.

## Quyết định

Giao G06-06A1-R1 đúng module hiện hành và tests, không design mới. Đóng ba nhóm bằng regression trực tiếp; giữ purity và các happy-path hiện có. Không schema mới/summary/ledger/UI/matcher. Chưa G06A2 cho đến khi contract cơ sở an toàn.
