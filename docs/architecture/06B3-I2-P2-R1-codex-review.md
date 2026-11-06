# G06-06B3-I2-P2-R1 — Three residual branches, no scope expansion

2026-10-29. Report `fb0fb9575963a2ba426630be6e611a9d7d49e023d7400f892b62df96d449acd8` read/hash verified. Fresh7files: **260 passed in53.56s, exit0**. Đã sửa actual asset_file/duration_sec signature, đồng bộ defaults/options helper, worker identity, foreign revision, busy và ctor rollback cơ bản. Drawer stale/plaintext đã đúng, không mở lại drawer/worker/core.

Chưa accept P2 vì ba residual thuộc bindings gốc. Evidence `.bridge/ux-evidence/G06-06B3-I2-P2-R1-CODEX-REVIEW/probes.json`: controlled GUI facade gọi production methods, không desktop và không xóa QThread đang chạy thật.

## A — Active manifest bị raw manifest che (P1)
Builder chỉ băm placeholder_batch.manifest_id. Khi raw plan manifest_id='mA', active _manifest.manifest_id đổi mA->mB thì revision vẫn nguyên, batchmanifest mA. Terminal recompute vẫn pass, nên context hiện hành chưa thực sự được bind.

Chốt material chứa riêng raw plan manifest ID, active in-memory manifest ID và effective batch manifest ID. None hợp lệ khác với explicit valid ID, không dùng fallback làm mất trạng thái activeNone. Nếu cả raw/active đều là nonblank valid IDs nhưng mismatch ->ValueError controlled trước dispatch/generation/cancel và deny terminal. Nếu raw thiếu/None thì effective dùng active ID nếu có, không mutate raw plan; pending.manifest_id lấy batch.manifest_id, không dùng authority khác với batch. Validate types/UTF8 và budget cả raw+active IDs. Không disk/get_or_create trên GUI. Precheck source-array lengths<=4096 trước preflight iterator (hiện cap chỉ tới adapter sau loop).

## B — Sidecar result và storage context chưa revalidate (P1)
Handler so entry.demand==expected nhưng không inspect entry.result:
- Valid sidecar/demand IDs của pending nhưng result.query_text='foreign query' vẫn publish.
- Result mang candidates=(object(),), is_matched=True vẫn publish.
- Typed MatchingStorageContext bị đổi root=[] vẫn publish (nominal isinstance chưa đủ).

Revalidate pending batch qua existing helper trước dùng; mỗi entry đúng class, demand graph đúng, eligible có đúng1actual result, noneligible resultNone. Gọi existing PlanDemandAdapter.assemble(pending_batch, eligible_results) để validate exact request/result/candidates và dùng canonical sidecar đã rebuild sau khi đối chiếu graph/IDs/manifest. Không copy validator hoặc chỉ gọi equality/post_init outer sidecar. Context root/library_id nonblankUTF8 trước last-good/model mutation. Outcome fields strict và status-consistency vẫn phải đúng, error/cancel không có sidecar/context; expected bindings không lấy từ untrusted output. Invalid ->0publish, controlled banner/no slot escape. Không sandbox malicious arbitrary Python callbacks.

## C — Running thread branch dựa flag started sai (P1)
Flag started chỉ set sau start() return. Controlled worker start sets runningTrue rồi raise: code pop registry, deleteLaterTrue, cancelFalse. Nếu actual thread đã chạy, việc này vi phạm retain-until-finished policy. Probe dùng fake lifecycle recorder, không cố gây crash thật.

Trong except, quyết định bằng actual isRunning()/lifecycle evidence, không chỉ started flag. Running ->keep owned registry/ref, cancel, giữ busy, native finished mới remove/delete. Non-running ->rollback riêng owned request, clear pending và restore aggregate flags/button, last-good preserved. Không rollback state của request khác hoặc decrement generation. Test ctor failure, start-before-running failure và start-after-running failure, physical finish boundary và other-jobs aggregate loading.

## R2 gate
Đúng một production module ReviewPage + UI tests; không rewrite builder đã sửa tốt hoặc mở drawer/worker/StorageManager. Tests phải dùng actual typed sidecar results cho B, manifest rawNone/currentNone/mismatch và compare both IDs cho A, controlled running recorder hoặc QThread có release/finally an toàn cho C. Same finite7files, one report A–C. Desktop chỉ giao sau code gate đạt. Correct report counts per test file từ actual pytest collection, không suy từ số functions hoặc tổng mong muốn; core strict loader constant tên MAX_JSON, không API MAX_JSON_BYTES mới.
