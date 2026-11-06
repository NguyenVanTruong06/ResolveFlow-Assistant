# G06-06A4a-I — Codex review, 2026-10-28

**CHANGES_REQUESTED — G06-06A4a-R1.** Fresh prescribed focused group **120passed in4.03s, exit0**. Root/malformed-marker rejection, early target-slot guard và last-good-state boundaries đã có; giữ nguyên các phần này.

Hai counterexamples pure:

1. JSON hợp lệ chứa cả timeline_segments và subtitles được parse_web_prompt_json nhận thành dual_plan. Nhánh này cố ý gán video_plan và kinetic_plan cùng single_obj (shared reference, không cycle). contains_batch_draft_plan dùng global visited_ids, gặp object lần2 trả True, nên final UI guard từ chối một Dual Plan hợp lệ. Probe: LEGIT_DUAL_PARSE=True/dual_plan; SHARED_ALIAS=True; GUARD_REJECTS_LEGIT_DUAL=True. Đây là regression producer→consumer thật, không phải graph giả.
2. List container rộng được append toàn bộ vào queue trước check visits. Probe CountedList1000dicts, max_visits=2: result True nhưng đã enumerate1000entries. Budget hiện chỉ hạn dequeue, chưa hạn work/queue allocation; không đúng bounded-inspection contract.

Sửa duy nhất helper trong director_state.py + targeted tests: phân biệt current-path cycle với completed/shared DAG node; giới hạn enumeration/enqueue/processing đều trước mở rộng branch. Fail-closed khi budget cạn, không đổi thành False. Giữ các UI guards, V1/V2 unchanged, không mở receiver. Actual parser shared object phải qua final UI boundary thành công và giữ đủ cả hai slots; true cycle/deep/mixedbatch vẫn reject.

Docs tiếp tục chốt next receiver: geometry phải tuple per A1 item (nhiều clips/partitions trong một batch), không scalar source_in/out cho cả PreparedBatchItem; pure core không import UI DTO. No AI proof phantom/circular prompt snapshot digest. Đây là corrections trong cùng R1, không thêm D/test fullsuite.

Evidence .bridge/ux-evidence/G06-06A4a-I-CODEX-REVIEW/. Không production/test edits hoặc desktop/model/Resolve trong Codex review. Chưa nghiệm thu guard; no shutdown.
