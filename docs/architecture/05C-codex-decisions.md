# G05-05C-D — Codex decisions (2026-10-27)

DESIGN_ACCEPTED_WITH_BINDING_CORRECTIONS. Không thêm vòng khảo sát tổng thể; giao first implementation slice đã hiệu chỉnh.

## Phần A harness
Nhận metrics QSettings fileName trong namespace tạm, workerboundary reached và cleanup registryempty/running0/sessioncleaned/transportrestored. fault_caught=False trong metrics: đây là lỗi bên worker được worker terminalize, không chứng minh external driver exception đã bị catch; report phải nói đúng boundary. RESOLVEFLOW_CACHE_DIR/RESOLVEFLOW_ANALYSIS_CACHE chưa được ScanCacheManager mặc định tiêu thụ (constructor chỉ explicit cache_dir hoặc ~/.resolveflow_cache), nên env đó không chứng minh audio-cache isolation. Future harness phải inject explicit cache manager; không claim zero toàn bộusercachetouch từ một filenameQSettings. Không bắt rerunVLM/anotherharnesscycle; giữ giới hạn và sửa mô tả docs.

## Findings và corrections
- Đúng: ClipManifest.build_clips_data và StoryCopilot đã đọc visual_segments/status/coverage, không phải chưa có visual promptconsumer.
- load_transcripts_from_cache vẫn giữ tất cảpaths trong _clip_infos, chỉ không tạo rows cho empty speech. Không nói clip bịxóa100%khỏisystem. Có bug cụthể: ifrows mới gọiset_review_rows, set_review_rows([]) cũngreturn; switch sang silent/missingcache giữ lời thoại cũ/demo. Firstslice phải clearoldrows/empty-state đúng.
- speech_status=no_dialogue_detected và visualcoverage đã có trong build_clips_data. Không sửa ClipManifest chỉ để thêm logic đã tồn tại; IDs/sampledfingerprint giữ nguyên.
- VisualIndex hiện authority fingerprint+signature/evidence, không tự có fullSHAverified_source nhưaudio. Không tuyên bố nó đã có hash verification. UI metadata chỉcandidate; export authority/source revalidation táchbước sau, không fullhash GUI.
- Giữ origin literal user và local_model:<name>, analysis_status đúng producer; không tự đổi thành user_annotated/insufficient hoặc gán confidence high/medium/low khi producer không cung cấp.
- Giữ completed window observations của partial/cancel trênReview cócảnhbáo, không lọc bỏ cảclipchỉvìjobcancelled. Không thêm placeholder speech rows (sẽđimất vào cut/transcript/prompt), không gánB-roll.
- Policy UI: visual panel Model/View riêng cạnh hoặc dưới speech trong khu Review hiệncó, không rewrite speechwidgets/cộtAI prompt. Tỷ lệ60/40 chỉdefault, defer2chiềuhighlight/keep-cutvisualcontrols khi chưacontract.
- Budgetprompt: đã có3summary/8distributed details; giữ behavior trước, chưa thêmhierarchicalLLMcall/scene-scoring khôngbằngchứng. G05C2 sẽhardening promptprovenance/budgets/exportguard, không gộp firstslice.
- Splitfirstslice3modules mới: ReviewPage, DirectorWindow, new read-only ReviewAnalysisWorker. Async metadataIO/ownership cầnworker; ClipManifest chưađụng.

GateG05 còn Review/Prompt integrationacceptance + speechfixture/unifiedWhisperrealverification; longbatch/hardwareG09 không giảđãđạt. HIGH, khôngshutdown.
