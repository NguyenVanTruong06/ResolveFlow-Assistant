# G06 — Phạm vi mở gate, 2026-10-28

> HIỆN HÀNH 2026-11-02: G07-07A7-I ACCEPTED_WITH_RECORDED_LIMITS; Codex 217 tests/23.81s/exit0. Active next: G07-07A8-D DESIGN_ONLY for read-only recovery assessment UI/guidance. No recovery execution or Resolve mutation; ready_to_execute=False; G07/M1 remains open.

> HIỆN HÀNH 2026-10-29: active duy nhất LIB-PUBLISH-02B HIGH; companion fixes accepted trong scope, Codex120tests/23.56s/exit0. Archive3.18GiB SHA256 independently verified, Drive403 insufficientParentPermissions (chưa upload). Next verified-browser upload và empty-library/runtime cleanup + source retirement có checksum/rollback, giữ2localbackups; G06 checkpoint, no shutdown. Xem LIB-PUBLISH-02A-codex-review.md. Các entries active cũ bên dưới là lịch sử.

> HIỆN HÀNH 2026-10-29: active duy nhất LIB-PUBLISH-01-R1-THEN-02A HIGH tại .bridge/task.md. Codex111tests/24.34s/exit0 nhưng3residual probes confirmed; sửa2modules rồi full archive/restore proof và upload mới nếu đủ quyền. Chưa cleanup source/runtime, G06 giữ checkpoint. Xem LIB-PUBLISH-01-codex-review.md. Các ghi chú active cũ bên dưới là lịch sử.

Trạng thái hiện hành 2026-10-29: G01–G05/A1/A2/A3 và06A4a guard acceptedwithinlimits. Người dùng ưu tiên kho online: active duy nhất LIB-PUBLISH-01-I HIGH tại .bridge/task.md. G06-06A4b-I được giữ checkpoint tại task/architecture-2026-10-26/06A4b-before-library-publication-2026-10-29.md, chưa nghiệm thu/không chạy song song. Không synthesis/diskledger/matching/EditPlan/G07/shutdown. Activeentriescũ bên dưới là lịch sử.

Người dùng đã quay lại và yêu cầu tiếp tục; điểm dừng sau G05 đã được thu hồi cho công việc tiếp theo. Không còn lệnh shutdown đang chờ thực hiện. Không tự tắt máy sau G06 nếu chưa có chỉ đạo mới.

## Mục tiêu và thứ tự

1. **06A — Lập lô có giới hạn và tóm tắt phân cấp:** evidence window/segment → clip → batch/chapter → project. Giữ global source IDs, manifest authority, ngân sách và coverage/omission. Queue phục vụ luồng này cần được thiết kế ở mức tối thiểu, không xây framework job toàn app.
2. **06B — Matching asset offline:** lấy catalog/taxonomy JSON từ kho thật, tìm theo metadata có provenance, trả asset IDs và lý do. Không mặc định dùng sample media trong repo như stock của user.
3. **06C — Edit-plan JSON và handoff:** adapter/validation tương thích plan hiện hành, source/asset references và bounds rõ; export/import JSON và dry-run decision, chưa thực thi Resolve. Mutation/preflight/recovery là G07.

Đây là ba nhóm phạm vi, không phải ba task triển khai đồng thời. Sau thiết kế chỉ giao một lát cắt có acceptance hữu hạn, ưu tiên pure bounded batch planning nếu callgraph cho phép.

## Ghi nhận sơ bộ từ code hiện tại

- src/core/queue.py có BatchJob/BatchProcessorQueue và danh sách trạng thái/progress trong memory; không thấy scheduler/checkpoint/resume trong file. Cần Worker tìm consumer thực trước khi đánh giá ảnh hưởng.
- src/core/story_pipeline.py: MasterStoryPipeline.process_chapter_group tạo events/subtitles và hook candidates; không thể coi tên “chapter” là bằng chứng đã có hierarchical summary/budget planner.
- src/core/broll_sfx.py: GlobalAssetPool mặc định assets/ và basename indexes, có project asset scanning. Cần truy vết sang StorageManager catalog/guard, không đoán rằng đã unified.
- src/core/story_copilot.py có CopilotDirectorPlan/CopilotMediaInsert, source refs và filename-based asset fields hiện hành. Không đổi schema hàng loạt trước khi chốt adapter/consumer.

Đây là source inspection, không test/runtime/benchmark mới. D không sửa các điểm trên.

## Binding kế thừa

JSON/offline/local mặc định, không DB server/embedding hoặc paid API bắt buộc. Copy prompt thủ công sang AI web vẫn là tùy chọn có chủ đích; app không tự upload source. Byte budget32768 ở consumer V1 không phải token guarantee.

Giữ G05 fullSHA/cache identity, provenance speech/visual, negative cache, source/frame/FPS/word timestamps và async UI guards. Không summary từ phần đầu rồi tuyên bố hiểu toàn video; unknown/partial/offline phải được giữ. Không bịa IDs/file/duration/confidence hoặc chèn preview/carrier.

Gate thiết kế này không mở lại G02 migration/recovery hay G04 shell backlog. Chỉ sửa prerequisite có bug tái hiện và được Codex giao riêng. No shutdown/Resolve mutation/test full suite. Workspace dirty/untracked được bảo toàn.


## Review bindings
Quyết định triển khai hiện hành tại docs/architecture/06-codex-decisions.md. First slice chỉ pure partition + exact evidence-envelope bytes, không summary/model/ledger. Các pseudo-contract06B/06C chưa được approve để code.

## Active workorder — 2026-10-28
G06-06A3-D HIGH (design only), thay thế active A2 sửa lỗi. A2 CODE_ACCEPTED_WITH_RECORDED_LIMITS theo06A2-R2-codex-review.md; không mở lại A1/G05. Consumer preview/copy batch additive và exact full rendered prompt bytes cần design trước I. No ledger/model/matching/Resolve/shutdown.

## Active — 2026-10-28 sau06A3-D review
G06-06A3-I HIGH thay D. Implement đúng3modules source-only batch UI + async prepare/copy, exact full-render budget, giữ V1/V2. Bindings06A3-codex-decisions.md. Không mở matcher/ledger/hierarchy/Resolve/shutdown.

## Active hiện hành — 2026-10-28 sau06A3 acceptance
06A4-D batch draft receiver/project hierarchy, design only; không code trongD hoặc tự mở06B/06C/G07/shutdown.

## Active06A4a-I — 2026-10-28
06A4-codex-decisions.md chốt two-module routing guard trước intake, no epsilon/clamp/proofphantom. Không tự mở06B/06C/G07/shutdown.
