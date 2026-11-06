# G06-06A2-R1 — Codex review, 2026-10-28

**CHANGES_REQUESTED: G06-06A2-R2.** Fresh finite suite: **81 passed in 0.57s, exit 0**. Những lỗi default available/described, text stripping, opaque signature passthrough và missing supplied signature đã được sửa. Không yêu cầu làm lại chúng.

Ba residual dưới đây vẫn thuộc hợp đồng adapter đã giao. Không mở scope runtime hoặc sửa G05/A1.

## A — Fixture chưa khớp producer thật / authority fallback

`VisualIndex.validate_segment` (src/core/visual_index.py:102) trả observation_kind='segment' cho range, 'point' cho note điểm. Adapter ở block validation chỉ nhận point/interval/range; gọi trực tiếp pure producer với range [3,4] và đưa kết quả vào adapter → ValueError ERR_INVALID_DATA_TYPE: observation_kind không hợp lệ: 'segment'. Test hiện dùng interval nên bỏ sót integration break. Review Codex cũng cần hiệu chỉnh ví dụ vocabulary trước: schema producer thật ưu tiên hơn ví dụ interval; không sửa producer để chiều adapter.

Nhánh fallback audio hiện trả raw_speech_status khi audio_authority='bogus' → speech_status='transcribed', dù 0 verified speech windows. Giao R1 đã yêu cầu không passthrough unknown authority/status thành transcribed. Phải có controlled schema rejection hoặc truthful unscanned/scan_error + issue, không success label giả.

## B — Pre-gating validation còn lỗ hổng

Probes in-memory:
- Interval {start_sec:None,start:1,end:2,text:'speech'} qua validation nhưng dispatch chọn start_sec theo key presence → TypeError float(None). Validation và extraction phải dùng cùng normalized result.
- visual_coverage={complete:'yes',total_windows:True,sampled_windows:NaN} được nhận. Mapping chứa object() gây TypeError tại json.dumps. Top-level Mapping check chưa là semantic allowlist validation. Dùng schema thật, allow_nan=False là defense cuối, không phải thay validation.
- visual_stale_excluded_count=None qua validation rồi int(None) → TypeError. Optional absent khác present invalid; thống nhất policy, không TypeError.
- source_missing + quarantined_visual_segments=[123] hoặc visual segment origin={} / analysis_status=[] vẫn được nhận. Các known fields này phải validate trước gating theo R1, không bỏ qua do nguồn mất. window_id sai kiểu cũng chưa validate.
- MappingProxyType/nested coverage cần được chuẩn hóa thành owned plain semantic data trước hash và frozen copy sau đó; không giữ unknown runtime refs, không arbitrary deepcopy/repr/str.

## C — Signature vẫn thiếu identity/status

Giữ windows giống nhau, đổi visual_analysis_status complete→partial: context_signature không đổi. Đổi manifest clip content_hash/file_name/current_path: context_signature cũng không đổi (row không khai path). R1 đã yêu cầu manifest/source identity và declared authority/status thuộc hash, không chỉ output windows. Generated per-clip hash phải bao gồm các semantic inputs này ở mọi branch, vẫn không IO/full-file proof. Không yêu cầu hash mọi extra field vô nghĩa.

## Acceptance hữu hạn

Một production module + test module như R1. Thêm regression trực tiếp producer validate_segment→adapter→A1, normalized aliases, nested malformed data dưới source-skip, valid MappingProxyType coverage, metadata/status hash delta. Giữ các 81 tests sau hiệu chỉnh fixture/expectation sai schema. Chưa desktop/inference/runtime wiring; không shutdown. Bằng chứng Codex tại .bridge/ux-evidence/G06-06A2-R1-CODEX-REVIEW/.
