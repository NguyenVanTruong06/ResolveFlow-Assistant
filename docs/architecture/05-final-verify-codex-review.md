# G05-FINAL-VERIFY — Codex review, 2026-10-28

**FUNCTIONAL_EVIDENCE_PARTIALLY_ACCEPTED / TWO_INTEGRATION_BLOCKERS. G05 chưa đóng.**

## Bằng chứng được nhận và giới hạn

Đã đọc report, metrics.json, metrics_fault_injection.json, desktop_execution.log, harness và ảnh case3_v2_desktop_widget.png. Real Small local engine nhận16word, cached speech được Review/V1 dùng, V2 preview/final/AutoPlan4pages/Transcript có kết quả. Không chạy lại Whisper hoặc146tests trong lượt review. Đây là inference CPU với synthetic English và VideoPlan fixture, không accuracy tiếng Việt/human/3h.

Không thể nhận tuyên bố end-to-end nguyên bản/100% toàn bộ: harness có hai can thiệp che lỗi sản phẩm.

## P1-A — Clip không audio không được lưu cache

source_analysis_worker.py:419 nhánh has_audio=False chỉ gán audio_status=no_audio_track; không lấy duration/lưu negative v3 evidence. Harness dòng477–486 tự save_scan_result cho B trước downstream, nên đã lấp lỗ hổng ngoài đường sản phẩm.

Codex probe: tạo MP4 xanh1s24fps không audio bằng FFmpeg trong TemporaryDirectory, dùng SourceAnalysisWorker thật/audio-only, real probe/hash/cache. Source terminal success/completed, audio_status=no_audio_track; get_cached_scan verify_full=True trảNone; đưa chính source/manager sang KineticPreparationWorker thì terminal False/not_analyzed. Không model/inference, không seeded cache. Chi tiết .bridge/ux-evidence/G05-FINAL-VERIFY-CODEX-REVIEW/probe-results.json.

Đây là blocker cho user có cảnh không thoại. Sửa producer lưu no-audio evidence verified v3 với actual finite positive duration, không cho V2 bỏ qua cache/authority guards.

## P1-B — Decode Whisper chỉ chạy nhờ patch trong harness

Harness run_verification patch global av.open để bỏ keyword metadata_errors. Thư viện faster_whisper.audio.decode_audio thực gọi av.open(...metadata_errors='ignore'); PyAV trên máy19.0.1 không nhận keyword này.

Codex probe unpatched: WAV PCM16 mono16k0.1s, gọi decode_audio không model -> TypeError: open() got an unexpected keyword argument 'metadata_errors'. Do ResolveTranscriber.transcribe truyền path vào backend, app nguyên bản cũng đi qua nhánh này. Proof real inference hiện chỉ được nhận dưới compatibility patch, không đủ proof app nguyên bản hoạt động.

Sửa tại facade transcriber, không monkeypatch av.open toàn tiến trình/site-packages, không download/downgrade dependency. Có sẵn transcript_quality.read_wav_mono cho WAV do app trích xuất; có thể dùng input ndarray16kfloat32 hợp lệ với backend, giữ timing/progress/metadata, từ chối dữ liệu sai rõ ràng. Worker chọn giải pháp nhỏ có regression.

## Bằng chứng harness cần hiệu đính trong cùng lượt xác minh

- Ca2 chỉ chuyển Review và kiểm model counts, không chạy lại Source scan; 0 full rehash không được đo và trái verify_full. Chỉ claim 0 model reload/transcribe hiện được quan sát.
- Case4 tạo requestB; chỉ chứng minh supersede ID, chưa cô lập fingerprint change với cùng requestA. Close gọi sau khi mọi worker đã idle, chưa close-pending.
- Safety: setup/patch trước try, cleanup chỉ pump0.4s rồi rmtree(ignore_errors=True), metrics success được ghi trước finally và thiếu measured running/session removal. Clipboard restore khi current!=initial sẽ đè clipboard mới của user; không phải ownership guard. Faultmetrics exit2 chưa tự chứng minh các cleanup counters.
- Metrics compute_type=int8 là requested config; facade CPU épfloat32. No-audio cache B do harness seed phải công bố. 16recognized words không phải16words chính xác/human-verified.

## Quyết định hữu hạn

Giao G05-FINAL-R1: đúng2production modules source_analysis_worker.py/transcriber.py và sửa harness. Regression focused + một targeted integration run không patch/seed và boundary/safety-only probe. Không lặp146tests hay các functional cases đã nhận, không feature/design mới. Nếu đạt và evidence trung thực thì review đóng gate; sau đó mới shutdown theo user.
