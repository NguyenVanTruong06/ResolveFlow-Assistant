# G06-06A3-R1 — Codex review, 2026-10-28

**CHANGES_REQUESTED: G06-06A3-R2.** Fresh finite6files: **213 passed in45.93s, exit0**. Các sửa inode/ctime/device, empty baseline, selected-index type, selection change, busy logical/physical và manifest duration SSOT đã có. Không yêu cầu làm lại các phần đã sửa.

## Residual1 — Frozen DTO chưa là validation / immutable ownership

prompt_preparation_worker.py:validate_and_normalize_snapshot trả raw ngay khi isinstance PreparedBatchSnapshot. Probe dataclasses.replace(snapshot, baseline_sources=(), max_prompt_bytes=True) vẫn được validator nhận nguyên object. Nested BaselineSourceEntry cũng đi qua không kiểm tra. Type annotation/frozen không tự chứng minh validity.

unaccounted_clips/adapter_issues chỉ tuple(raw list), còn giữ dict caller; signatures Any cũng không freeze/validate. Probe sau normalize: sửa raw.adapter_issues[0].message → snapshot đổi thành AFTER_CALLER_MUTATION. Đây là alias thực, trái immutable owned snapshot. GUI vẫn lưu mutable dict riêng, request giữ reference, normalization chưa là boundary duy nhất.

## Residual2 — Byte equality chưa kiểm chứng prepared content/context

Copy có đo bytes nhưng không đối chiếu rendered content digest hoặc prepared content identity. Probe valid raw snapshot rendered_prompt='ORIGINAL', rendered_bytes8; thay bằng 'TAMPERED'8bytes, giữ signature cũ → worker success/copy TAMPERED.

Snapshot project='P', manifest='M', current request project='OTHER_PROJECT', manifest_metadata.manifest_id='OTHER', paths vẫn cùngfile → worker success. Chỉ baseline path equality chưa đủ snapshot/project/manifest/context authority đã yêu cầu trong R1. UI context check hữu ích nhưng không thay worker validation boundary, nhất là stale snapshot cùng paths.

## Residual3 — Qt terminal tin result.intent

ReviewPage._on_prompt_preparation_finished vẫn lấy intent từ result thay vì expected owned request. Probe active intent prepare_batches, matching active request/context, result.intent='copy_batch' + prompt='INJECTED_COPY' → clipboard bị đổi. Đây là vi phạm prepare0clipboard. Callback cũng chưa đối chiếu snapshot identity/batch_id/generation/content với expected copy record trước write; giữ kiểm tra selected index đã sửa nhưng chưa đủ.

## Chỉ đạo

R2 chỉ worker + ReviewPage và focused tests, không cần đổi wrapper/core lần nữa. Một canonical validation path cho dict và DTO; owned frozen nested values; prepared render digest và snapshot identity tách A1 content_signature/A2 evidence signature/UI context; independent expected request bindings; callback validate-before-mutate/clipboard. Không claim chữ ký tự chứng minh file hoặc chống malicious signer; đây là kiểm tra tính nhất quán dữ liệu giữa các stage sở hữu trong app. Không thêm cryptography service/ledger/framework.

Evidence .bridge/ux-evidence/G06-06A3-R1-CODEX-REVIEW/. No desktop/model/Resolve, không sửa production/tests trong Codex review. Chưa nghiệm thu code/desktop, chưa G06 đóng, không shutdown.
