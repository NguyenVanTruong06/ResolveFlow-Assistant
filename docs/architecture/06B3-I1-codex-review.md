# G06-06B3-I1 — CORRECTIONS_REQUIRED

2026-10-29. Report `c5aff88df21339410872e2c2ac35912b056a79bbe012e794d071447f4a7e3d34` hash verified. Codex đọc cả hai production modules và tests; fresh finite suite bốn files theo task: **109 passed in 0.55s, exit0**. Chưa nghiệm thu adapter. Cụ thể `.bridge/ux-evidence/G06-06B3-I1-CODEX-REVIEW/probes.json` có12 counterexamples, không phải giả định. Review không sửa production/tests.

## A — Numeric intake (P1)
extract gọi math.isfinite(int) trước guard overflow: duration_sec=10**400 ném OverflowError, không tạo invalid demand giữ good siblings. MAX_BIGINT_BOUND=10**18 là cap mới không được giao: finite duration1e19 bị invalid dù policy chỉ finitepositive/no clamp.

Sửa bằng safe numeric conversion/type gate bắt overflow trước isfinite, giữ nguyên finite>=0 timeline và finite>0 duration. Loại cap1e18. Absent vẫnNone, null/bool/NaN/Inf/string/overflow vẫn invalid demand. Test cả timeline/duration, good sibling và positive1e19; không mở source bounds hoặc placement execution.

## B — Raw input và query precedence (P2)
Known Dual wrapper {'type':'dual_plan','video_plan':...} hiện trả emptybatch0demands, trái raw-video-plan boundary đã giao. Không unwrap hoặc coi wrapper như empty success. Reject known dual/kinetic/batch/overview envelopes/markers có type gate an toàn; raw empty{} vẫn hợp lệ, không reject normal unrelated metadata.

asset_file='___', suggested_file='fallback.mp4' hiện eligible query='fallback'. Quyết định gốc chỉ fallback sang suggested_file khi asset_file blank/khuyết. Nonblank filename nhưng stem thành rỗng phải empty_query, không thay bằng hint khác. Giữ description-first và unsafe selected filename invalid; good blank asset_file + suggested_file vẫn eligible. Không mutate raw hints/plan.

## C — Batch/demand/request authority (P1)
assemble chỉ isinstance(batch), len(results), map requests; không validate consumed batch/items hoặc correspondence. Các probe hiện được nhận:
- replace(batch,plan_revision='B') với demandIDs thuộc'A' -> sidecar'B'.
- duplicate một demand -> total2 với một request/result; bỏ demands -> total0 dù request/result vẫn có.
- status='anything' -> total1 nhưng sum các counters0.
- demands=(None,) -> AttributeError.

Revalidate context và toàn graph trước xuất sidecar: revision/manifest UTF-8/type, tuple/items/bounds, status enum/reason, slot/index, canonical ID encoding [revision,slot,index], unique IDs, canonical slot/index order, request cap256, eligible<->requests bijection và category/query echoes. Shared request validator06B1; không duplicate-coerce request hoặc tin frozen. Tất cả malformed graph ->ValueError controlled, không drop/orphan/duplicate count. Mỗi status/reason/fields consistent theo extractor; nếu constructor đã reject sớm thì vẫn giữ assembly boundary cho typed object bị thay consumed fields. Test ordinary valid constructors/replace cũng như mutation, không mở sandbox arbitrary Python magic methods.

## D — Result/candidate validation và ownership (P1)
request_id=[] trên typed result -> TypeError tại dict lookup. is_matched=0 (int) được accept; typed result candidates bị đổi thành(object(),) vẫn được xuất dù is_matched=False. Frozen DTO hiện hữu không là proof.

Trước hash/lookup/property: resultID/category/query UTF-8 đúng loại, strict is_matched bool iff candidates nonempty, reason consistency, candidates tuple gồm OfflineAssetCandidate hợp lệ, warnings tuple UTF-8 strings, rejected count strict nonnegativeint. Validate consumed candidate scalar types/finite duration/score int0..100/flags bool/source availability/string metadata, matching category với result/request; candidate typegate trước attribute access. Optional provenance fields giữ Optional contract, không ép có thêm authority evidence mà candidate06B1 không đòi. Không paths/executable flags mới hoặc lookup filesystem. Không export caller-owned mutable lists trong result/candidate; reject malformed sequence/object thay vì deepcopy handle. Kết quả sidecar phải an toàn để UI đọc/counters phân hoạch, không phải mới có quyền mutation.

## R1 gate hữu hạn
Giữ đúng2productionmodules mới và adaptertests; không sửa accepted06B1/B2/StorageManager/UI. Gom A–D một report, TDD counterexamples và positive controls, finite bốn files command gốc. Không tăng arbitrary cap/default hoặc sửa test để nới policy. Sau xanh review mới giao thiết kế lifecycle/worker; chưa UI/I2/06C/G07. Claims report cũ 'strict revalidation/partition guaranteed' chưa đúng; cập nhật trung thực sau code thực hiện.
