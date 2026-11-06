# G06-06A2-D — Codex review / binding decisions, 2026-10-28

**DESIGN_ACCEPTED_WITH_BINDING_CORRECTIONS.** Cho phép06A2-I đúng1module mới +tests; không thêm vòngD.

Đã đọc thiết kế/report và đối chiếu build_clips_data, VisualIndex.validate_segment, VisualDescriber và A1. Không pytest/runtime/inference trong design review.

## Ba câu hỏi chính sách

1. **Không clamp.** Bác đề xuất0.2s/0.5s drift. Adapter không sửa evidence để qua A1 bounds. Finite out-of-bounds/point-at-end bị loại khỏi usable windows với structuredissue/reference/range gốc; wrongtype/nonfinite/overflow làValueError. Không bỏ lặng lẽ, không làm trôi câu hay point.
2. **Hash context tùy chọn.** full_content_sha256/analysis_signature được cấp qua context_metadata keyed clip_id. Không IO/rehash, không sampledhash làmfullSHA. Signature adapter là hash dữ liệu/context được cấp, không chứng minh sourceverified hoặc cho phép persistentcache reuse ngoài caller guard.
3. **Giữ point đúng schema.** User note thật thường có time_sec, end_sec absent/None, observation_kind='point', analysis_status='needs_review'. Không chỉ hỗ trợ start==end. Giữ point0<=t<duration, namespaceID/refs rõ và A1 phân phối boundary sang phải.

## Corrections bắt buộc trong cùng taskI

- clips_data là mixed authority/availability, không26fields “đều verified”. fullSHA/analysis_signature là supplementalcontext, không producer fields mặc định. duration_known False không tự khiến A1unaccounted khi manifest duration biết; manifest vẫn là SSOT, adapter ghi mismatch/unknownissues, không sửa manifest.
- completed_empty default has_audio=None; không tự khẳng định True. no_audio_track chỉFalse nếu audio_authority verified và khôngerror/contradiction. candidate/unavailable không tạo thoại/noaudio verified.
- Source missing/changed/error/unavailable thắng toàn bộ oldevidence kể cả usernotes. Audioerror chỉ chặn thoại, không bỏ validvisual khi sourceavailable. Missing producer row (shorthoặcclipkhác) ->unscanned/unknown vớiissue, không drop/reindex.
- Origin giữ literal ('user','local_model:...', ...), không rewrite thành'vision'. Visual dùng analysis_status thật (described/needs_review/unknown/error/cancelled/stale), không default'analyzed' để cấp authority. Topstale/unverified/error chặn machine, usernote hợp lệ vẫn giữ khi sourcevalid. Quarantined text không vào usablewindows.
- speech_word_count=len(text.split()) chỉ whitespaceestimate, phải có auditlabel rõ; khôngWhisperwordindex/humanaccuracy. V2 không dùng countnày.
- Giữ original visual windowID ở audit/reference. Nhiều segments cùng producerwindowID là hợp lệ; outputnamespace theo modality+originalrecordindex, không reindex saufilter. Không biến speech thành visualaction hoặc description thành kết luận hành vi mới.
- Không adapter truncate/sampling ưu tiên hay maxwindow-drop để làm vừa32768; limit là expliciterror. Pass untruncated vàoA1, propagatebudget/refinement requirement. Ví dụdense trong design không được hứa A1 tự đổi300/600s để luônfit; A1 chỉ dùng config đã cấp.
- Firstconsumer runtime vẫn PROPOSED: PromptPreparationWorker là integrationpoint tương lai, không thay thế singleprompt trong06A2-I. No UI/ledger/summarizer/model wiring.

## Chốt triển khai

Public API giữ adapt_clips_data_to_batch_analysis_cache(manifest,clips_data,config=None,context_metadata=None). AdapterConfig chỉ resourcebounds; bỏ clamping/prefer-user flags. Frozen AdaptedBatchSnapshot chứa analysis_cache, issues, audit_by_clip, context_signature và word_count_method='whitespace_estimate'. Exact input/filter/validation/test contract tại task/architecture-2026-10-26/06A2-pure-evidence-adapter.md.

06A1 vẫn accepted39tests, không sửa để tiệnadapter. G06chưađóng, khôngshutdown/G07.
