# G06-D — Codex review và binding decisions, 2026-10-28

**ACCEPTED_DIRECTION_WITH_BINDING_CORRECTIONS.** Cho phép G06-06A1-I đúng1module pure mới + tests. Các schema/weights/API06B/06C vẫn là PROPOSED, chưa được cấp quyền triển khai.

Đọc report, toàn thiết kế06, queue/chapter/source contracts và đối chiếu source. Không pytest/app/inference trong lượt design review. Không claim các benchmark <16ms/<1s trong thiết kế đã được đo.

## 1. Ba quyết định chính sách

1. **Byte hard / time soft:** tối đa32768UTF8bytes cho đúng chuỗi kết xuất mà planner sở hữu, không token guarantee. Target600s mềm, hard900s mặc định, tối đa20distinctclipIDs/batch. Không dùng base header ước chừng4200bytes hoặc cắt text để giả đạt trần. Chưa thể claim full V1 prompt luôn vừa: formatter tương lai phải tính lại full prompt.
2. **Long clip:** tự partition metadata, giữ manifest_id/clip_id toàn cục và original source bounds; không cắt/copy media. Default window600s để ví dụ1200s→2partitions nhất quán. Window nhỏ hơn là config explicit; không vừa default120s vừa hứa1200s chỉ2partitions.
3. **Ledger:** kiến trúc checkpoint JSON nguyên tử hợp lý ở bước sau, nhưng DEFERRED khỏi06A1. Chưa chốt path .cache tương đối; phải do cache/project owner cấu hình, không repo/StorageRoot/ScanCache schema tự ý. Không claim “resume hoàn hảo” hoặc “chỉ invalidate B2” khi key chứa global manifest_id thay đổi.

## 2. Sửa các lệch thiết kế trước hoặc cùng triển khai

- SourceAnalysisWorker không có _process_audio_stage như bảng CODE_VERIFIED; sửa trỏ run()/nhánh thật. “SourceAnalysisWorker là nguồn evidence duy nhất” quá hẹp: G05 còn VisualIndex/user notes/verified consumer snapshots, provenance phải được bảo toàn.
- ClipEntry.content_hash là sampled hash size+đầu/cuối; không phải full_content_sha256 cache v3. Pure planner không xác minh file, không gắn nhãn nguồn đã verified chỉ từ sampled hash. Giữ riêng hai loại hash và context analysis do caller đã xác minh.
- PackIngest tạo UUID5 hex IDs, không bắt asset_id có prefix ast_. Canonical STORAGE categories là broll/sfx/bgm/overlays/memes/luts/templates; meme/overlay/lut/text/trans là UI aliases/template_type. Availability còn missing/staging. Không hardcode nhầm các domain này trong06B.
- Asset matcher advisory không phải mỗi ứng viên đều gọi action_guard/IO. Action-time authority vẫn bắt buộc trước thao tác. Source preference managed/linked/builtin không được lấn át relevance; scoring cụ thể06B chưa approve. Builtin không tự đồng nghĩa demo hay stock hợp lệ.
- Không chia content15/70/15 rồi coi đã hiểu toàn video. Summary phải có coverage/omission và source refs. 06A1 không tóm tắt ngữ nghĩa hay sampling/truncate text; một indivisible item quá byte budget phải explicit error, để formatter/refinement sau xử lý.
- Unknown duration không được ném vào timed batch cuối bằng0s. Trả unaccounted clip + reason. Missing analysis với duration biết vẫn có thể partition metadata, nhưng giữ unscanned/unknown, không silent giả. Speech failed không tự đồng nghĩa file nguồn hỏng hoặc mất toàn bộ visual evidence.
- EditPlanV1 không được defaultFPS24; sampled clip_hash không chứng minh source unchanged. Frames/seconds không được là hai SSOT mâu thuẫn, non1speed cần effective policy. Chốt adapter/consumer06C sau, không thay Copilot/DirectorConfig ngay.

## 3. First slice: pure bounded partition, không formatter/model orchestration

Public names được giữ từ đề xuất: BatchBudgetConfig, EvidenceWindowSummary, ClipBatchItem, BoundedBatch, ProjectBatchPlan, partition_manifest_into_batches, estimate_batch_prompt_bytes. Thêm render_batch_prompt làm single source of truth cho bytes. Exact contract tại task/architecture-2026-10-26/06A1-pure-batch-partition.md; contract này ưu tiên hơn pseudocode/fixtures mâu thuẫn của design.

Input là ClipManifest object + normalized in-memory analysis mapping theo clip_id, không cache manager/path. Module chỉ stdlib, TYPE_CHECKING nếu cần ClipManifest annotation. Không gọi create/load/sort/hash/probe nguồn. Data classes frozen, nested records immutable, config/type/finite strict; không clock/random/I/O/Qt.

Mỗi item có source_in_sec/source_out_sec rõ, partition_index/total_partitions theo clip. Nguồn có duration biết được cover chính xác không gap/overlap, giữ thứ tự manifest. Batch ID ordinal là định danh trong plan, không bằng chứng content-cache reuse qua mọi manifest revision. Planner chỉ hash payload/config được cấp, không hash file; signature không phải xác nhận đã phân tích.

Kết xuất canonical JSON evidence envelope + prefix/suffix caller cấp, đo UTF8 thực kể cả escaping/metadata. estimated_prompt_bytes là tên API legacy-proposed nhưng phải bằng len(render_batch_prompt(batch).encode('utf-8')). Không tự tính bằng số ký tự hoặc header magic; tránh self-reference của count/signature khi serialize. Đây là envelope06A1, không StoryCopilot prompt đầy đủ.

## 4. Scope / điểm dừng

Worker cập nhật thiết kế06 cho đúng các binding này trong cùng taskI, không thêm vòngD. Chỉ src/core/batch_planner.py và tests/core/test_batch_planner.py mới; không sửaG01–G05/queue/UI/formatter/matcher/Resolve. Chạy đúng test mới, không146/116/fullsuite/model. G06 chưa đóng, user đã resume; không shutdown.
