# G05-05B-I3a-I — Codex implementation review (2026-10-27)

CHANGES_REQUIRED trong core hiện tại. Codex chạy đúng hai file: 28 passed in 1.39s, exit0. Nhận overview rebuild giữ temporal cùng fingerprint, chuẩn total0, checkpoint và resume căn bản. Hai probe độc lập tmp/fake HTTP, không network/model:

1. CANCEL VIOLATION: fake /api/chat đặt cancelled=True rồi trả body hợp lệ. describe_with_ollama vẫn gọi index._write một lần sau cờ hủy, sau đó mới InterruptedError. Code bỏ check_cancel ngay sau HTTP, _commit_checkpoint chỉ check sau _write. Test test_cancellation_during_window_2_preserves_window_1_checkpoint thực tế đặt cancel ngay trong request đầu rồi kỳ vọng window đó vẫn được ghi, trái binding task “không publish kết quả tới sau flagcancel”. Chỉ checkpoint đã hoàn thành TRƯỚC hủy được giữ; response trả SAU hủy phải bỏ.
2. REFINEMENT SKIPPED: một window trả needs_review; fake refine_window tạo samples nhiều hơn. requests.insert đã xảy ra nhưng state window_id là needs_review, vòng sau gặp skip “if w_id in states ... needs_review” nên không inference trên bộ ảnh mới. Probe: refinement_created=1, inference_calls=1 thay vì2. Không phải vấn đề accuracy: code path refinement bị vô hiệu hóa.

Cần phân biệt reused checkpoint từ lần gọi trước với result vừa sinh và pending refinement trong queue. Pending refinement không được all_selected_done shortcut; budget/refinement attempts phải có evidence để resume không reset vô hạn hoặc bỏ mất refinement. Coverage sampling khác analysis progress; các cửa sổ refinement vẫn cùng window_id, không được đếm thành thêm cửa sổ nguồn.

Không sửa worker/UI/HTTP client/endpoint policy trong R1. Sau core accepted mới I3b. Model HIGH tiếp tục, không shutdown.

## Review G05-05B-I3a-R1 — 2026-10-27
Fresh two files:30passed1.35s exit0. Nhận check_cancel ngay sau HTTP và inference refinement trong cùng run đã hoạt động. Chưa nhận đầy đủ hai yêu cầu R1 ở boundary/resume:
1. _commit_checkpoint vẫn chỉ check_cancel SAU index._write. Probe đặt cancel trong index.load của precommit sau HTTP hợp lệ -> vẫn1write sau flag. Task R1 đã yêu cầu check ngay trước index._write, test boundary này chưa được thêm.
2. Base needs_review -> refinement tạo3samples, checkpoint lưu refinement_attempted=True nhưng không lưu pending refined request/samples. Probe cancel ngay SAU base checkpoint đã publish, trước refined inference: lần resume refinements_used=1/left0 và _is_window_completed coi base needs_review làdone, all_selected_done return;0newinference cho pending refinement. attempted/reserved chưa phải completed.
Cần explicit pending refinement và trạng thái budget reservation/consumption, giữ đủ evidence để resume đúng frame set; không reset budget hoặc đánh mất request. Không mở lại toàn thiết kế/UI. Report R1 vẫn TASK_ID I3a-I cũ: báo cáo R2 phải đúng ID và phiên bản bằng chứng.

## Review G05-05B-I3a-R2 — 2026-10-27
Fresh2files32passed1.36s exit0. Nhận check_cancel sát index._write và pending_samples resume một cửa sổ. Còn đúng hai yêu cầu đã giao trước nhưng chưa test:
- Max refinement budget qua resume: refinements_consumed chỉ đếm refinement_completed, không reserve pending. Probe2windows,max_refinements=1: lần1W0needs_review tạo pending rồi cancel; resume xử lý W0pending nhưng refinements_left vẫn1 nên W1needs_review cũng được refine. refinement reservations=['W00000','W00001'], vượt trần1. Pending không bị trừ oan như completed, nhưng vẫn phải giữ chỗ trong budget.
- _commit_checkpoint nằm trong generic inference try/except(OSError). Probe index._write luôn ném OSError: gọi2lần (lần2ghi error-checkpoint) thay vì abort sau1lần. Yêu cầu giữ bản checkpoint tốt và surface lỗi publication còn chưa đạt.
Đây là residual cùng contract R1/R2, không feature mới. Giao R3 đúng visual_describer.py +regressions; giữ các sửa đã đạt, không mở lại sampler/UI/design.

## Quyết định sau G05-05B-I3a-R3 — 2026-10-27
Fresh2files34passed1.40s exit0. Nhận đúng hai sửa R3 được probe: pending+completed reservation khi resume và publication lỗi propagate sau1write, không error-checkpointwrite lần2. Các sửa cancel/precommit/pending và overview independence trước tiếp tục giữ trong phạm vi tested. Không live Ollama/accuracy/performance proof.
Residual inference-error reservation: handler vẫn gán states[w_id]={window_id,status:error,refined:is_refinement}, làm mất refinement_pending/completed/pending_samples. Vì _is_window_completed và reservation không đếm refined=True riêng lẻ, lần resume sau error có thể lại giải phóng slot. R3 task đã nêu error-resume nhưng chỉ có tests A/C; cần đóng bằng một regression/patch nhỏ trước UI kích hoạt. Không thêm vòng core review riêng: đưa thành PhaseA bắt buộc trong I3b, testđạt mới làm PhaseB worker/UI.
Phạm vi I3b cho phép4modules có dependency rõ: visual_describer.py residual trên + worker/config + SourcePage opt-in/rows + Director summary. Đây không phải rewrite; không động visual_index/cache audio/Review/recipe. HIGH đã được user xác nhận, khôngshutdown. GateG05 vẫn chưa đóng.
