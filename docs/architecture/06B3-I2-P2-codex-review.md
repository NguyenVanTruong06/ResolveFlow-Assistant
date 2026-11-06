# G06-06B3-I2-P2 — CORRECTIONS_REQUIRED

2026-10-29. Report `d943e9839c483c3fe0c89b4c1478f4a0c2c47f3c47811eaa437989e8dfbd2414` đã đọc/hash verified. Fresh7files command: **248 passed in56.03s, exit0**. Source đọc: matching builder/dispatch/terminal/finish/invalidation, drawer, worker context và tests. Chưa nghiệm thu P2, chưa desktop.

Evidence `.bridge/ux-evidence/G06-06B3-I2-P2-CODEX-REVIEW/probes.json`: production methods được gọi với controlled in-memory GUI facade/labels/drawer và isolated XDG; không phải full desktop. Các probes cho normal input thay đổi đã xác nhận signature mismatch; terminal/start fault injection không là claim production attack.

## A — Builder không lấy adapter làm SSOT (P1)
Source dùng str(asset_type), phantom duration/desired_duration_sec, search_query/prompt/raw_query_hint; bỏ asset_file/suggested_file và duration_sec thật. Probe city.mp4->dog.mp4 hoặc duration_sec2.5->9 đổi batch/query/placement nhưng revision giữ nguyên. Hash ghi allow_offline=True, dispatched request False. Long filename600000chars tạo canonical190bytes nhưng query600000chars qua budget.

Làm đúng decisions06B3-I2-P2: preflight typed consumed strings/structural counts, extract placeholder, material từ owned typed demands (both slots/order/index/raw_type/category/status/reason/actual query/hint/source/placement), manifest và same options actually dispatched/current in-memory context. Bỏ derived IDs để hash không recursive; fullSHA + final revision, no str/falsy coerce/round. Ignore extras thật, không đọc phantom fields. Invalid row giữ accounting theo adapter; structural/options/oversize failures controlled trước mutate generation/cancel/spawn. Size cap tính trên actual consumed material, không che filename/manifest hoặc option changes.

## B — Missing actor/payload authority tại GUI slot (P1)
Callback chỉ req_id,outcome; registry chứa req_id là đủ, không bind worker identity. Handler duck-types attributes, không revalidate batch/sidecar/context.
- Valid typed AssetMatchingOutcome revisionA mang valid sidecar của revision'foreign' vẫn publish.
- SimpleNamespace completed,generation=True,sidecar=None được báo '✓ Hoàn tất gợi ý'.
- storage_context=object() gây AttributeError thoát slot tại drawer.library_id.

Bind exact sender worker/request/pending generation (strictint) trước dùng outcome. Actual outcome nominal type+strict fields; completed phải sidecar hợp lệ khớp pending revision/manifest/demand/request graph (use accepted validators/assemble) và typed context valid khi nonempty. Reject missing/foreign/duplicate terminal, bad tuple/candidate/counters trước mọi last-good/model mutation. No fallbacks từ supplied sidecar/outcome cho expected fields khuyết; consume pending terminal đúng1lần, vẫn giữ registry đến physical finished. Error/cancel cũng phải exact binding. Safe Qt GUI-bound receipt, không leak exceptions vào Qt/sys.excepthook. Không sửa accepted core để nới UI gate.

## C — Busy và truthful stale state (P2)
_on_video_plan_changed bật btn True ngay khi matching registry vẫn1entry; probe enabled=True/running_registry1. Dispatch có busy guard nên không double worker, nhưng UI trái physical-busy policy. Mọi refresh/invalidation/finished phải dùng cùng button-state logic dựa registry+close/current context. Callback khác worker không pop hoặc mở active request.
Drawer.set_error luôn hide stale banner: nếu vẫn giữ rows của planA sau planB, lỗi jobB không được xóa dấu 'kết quả cũ' của A. Giữ stale marker hoặc clear old rows explicit, không green summary của plan cũ. Metadata labels plain text theo scope gốc. Không thêm actions/filter/render redesign.

## D — Failed constructor/start để lại trạng thái bị khóa (P1)
Dispatch thay generation/pending/button/loading rồi gọi AssetMatchingWorker và start ngoài try. Probe constructor RuntimeError thoát slot, buttonFalse/pendingTrue/loadingTrue trong khi không có worker. Add controlled start lifecycle: non-running failure rollback only own request state/registry, restore aggregate busy đúng other jobs và error banner, last-good preserved. Nếu actual thread đã chạy thì retain reference/cancel và cleanup khi finished, không force-delete. Never decrement/reuse generation hoặc rerun. Test ctor/start fault injection với actual ReviewPage, không chỉ fake slot.

## R1 finite gate
Gom A–D trong ReviewPage +drawer và tests; worker context pre/post tests giữ xanh, không reopen worker/storage/core. Existing tests xanh thiếu fields/signatures/gates này. Expand table-driven actual field/binding cases và UI state assertions, fixtures cleanup finally. Task kế tiếp R1, chưa desktop/06C/G07. Report correction: reader đang dùng strict helper16MiB, không16KiB; đừng claim current-root snapshot atomic hoặc full raw-plan identity từ matching material.
