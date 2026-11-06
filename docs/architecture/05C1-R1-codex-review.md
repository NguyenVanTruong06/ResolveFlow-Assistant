# G05-05C1-R1 — Codex review (2026-10-27)
**NEEDS_FIX — G05-05C1-R2**, chưa nghiệm thu C1.

## Đã kiểm chứng
Fresh finite suite năm file: **54 passed in 4.28s, exit0**.
Đã đọc worker/ReviewPage/Director và các regression mới. Nhận các thay đổi: lazy provider init, outer error terminal, no-audio không fallback transcript cũ khi runtime hợp lệ, phân biệt cache miss với silent, nested visual signature/coverage và xóa mock-detection production.

## Residual từ chính checklist R1
### A. Source invalidation vẫn publish dữ liệu cũ (P1)
Probe file tmp, VisualIndex DI xóa file trong load(): pre_stat có nhưng post-check dùng if exists -> bỏ qua khi file đã mất; output speech_status=transcribed và lời thoại OLD SOURCE vẫn được publish.
Probe DI sửa file thành dài hơn trong load(): speech_status/visual_status=source_changed, nhưng subtitle_rows và visual_observations cũ vẫn đầy đủ. ReviewPage gom rows/observations mà không xét source validity nên dữ liệu cũ vẫn lên UI.
Regression test_source_stat_pre_post_and_same_basename_two_folders chỉ thử missing trước đọc và hai basename; không thực hiện xóa/sửa giữa read như docstring/report nói.

### B. Transcript malformed/runtime evidence còn suy empty (P1)
Probe cache raw_subtitles=None -> completed_empty/is_silent=True. Probe runtime audio_status=new thiếu raw_subtitles, snapshot.video_paths=('WRONG_PATH',) nhưng Whisper model/lang khớp -> cũng completed_empty/is_silent=True. Runtime-valid gate chỉ xét hai trường Whisper, không xét source paths/full request. R1 đã yêu cầu phân biệt list[] với missing/sai kiểu.
Provider exception hiện outer abort cả request; không emit clip lỗi và không tiếp tục clip sau, trái per-clip inventory contract. Cần giữ outer fail-safe nhưng isolate lỗi từng clip.

### C. Snapshot/lifecycle chưa đúng phạm vi đã nhận (P1)
ReviewPage chỉ dict(runtime_results), giữ nested dict/list và tuple(self._clip_infos) giữ mutable objects. Test immutability không truyền snapshot -> loader không dùng runtime, nên pass nhờ cache có cùng text; chưa kiểm tra snapshot mà test tuyên bố.
Callback clip/terminal thiếu close-pending gate; physical finished chỉ remove worker, chưa cleanup pending request. Failure terminal return ngay bỏ cả good payload trước đó; partial/error state của từng clip không hiện hữu trên UI ngoài memory thoáng qua.

## Scope chốt
R2 chỉ đóng A/B/C, không thêm tính năng/benchmark/live. Chủ yếu worker+ReviewPage, Director chỉ nếu cần close gate/wiring. Giữ candidate vs verified rõ; producer hiện không cung cấp source fingerprint của runtime completion, không bịa historical identity, không ép sửa core ngoài scope. Prompt authority C2 vẫn deferred.
