# G05-05B-I3-LIVE-LOCAL — Codex review (2026-10-27)

FUNCTIONAL_LOCAL_VLM_ACCEPTED_WITH_RECORDED_LIMITS, không đóngGateG05. Đã đọc report/metrics/harness và xem ground-truth1s/4.5s cùngDirector screenshot.
- Model qwen3-vl:2b-instruct, digest afc393e3572c07db0bc84aa460d928a16c75f3fbb9ca9e0dccc4fbd2dafb573e, actual local captions/new1, cold12.7066s theo Worker. Không phải benchmark tổng quát.
- Replay0/api/chat vàcached1 được spydelegating actualtransport; tags/show vẫn riêng. Missingmodelunavailable1 vàoverview còn làpositive bounded; caseC làcopy/pathmới nênoverviewnew1, không chứng minh same-filecached-frame retention tuyệt đối.
- Speech BLOCKED_NO_LOCAL_TTS được nhận là CHƯA KIỂM CHỨNG, không Whisper failure. TTS không phải dependency nhận diện thoại; cần speechfixture thật có ground truth hoặc bundled fixture thích hợp, không seed transcript.
- Fixture source ffmpeg tạo80x80squaređỏ và160x120rectanglexanh,640x360; không circle/1920x1080 như report. Ground truth filenames sai semantic. Caption nhận vật màu cơ bản, nhưng “red transforms to blue” chưa chứng minh hành động liên tục; chỉ2samples ở1.5/4.5s, không xác minh đường chuyển động. Nhận functionalinference, không nhận “mô tả chính xác toàn bộ”/accuracyPASS.
- Artifacts report paths không tồn tại: actual ground_truth_1s_red_circle.jpg/ground_truth_4s_blue_rect.jpg vàmodal_timestamp.png. Phải sửa tham chiếu, không rename history/fakeimages.
- Harness chỉ cô lập RESOLVEFLOW_VISUAL_CACHE_DIR sauQtimports, chưa cô lập QSettings/audio manager; checkbox/modelchange có thể ghi ai/vision_model vào settingsuser. Không có snapshotbackup thì không đoán giá trị cũ đểrestore. Stop futurewrites bằngprocess-isolated config trướcimports; report keys potentiallytouched.
- Finally mới closewindows+pump0.5 rồirmtree session, không verifyphysicalthreads/registry nhưshared_cleanupEvidenceR2 đãchốt. Phảireuse provenownedcleanup trongharnessmới. Không yêu cầu rerun12sGPUcold/replay chỉ để sửareport/cleanup; controlledfault probe đủ.
SauA harness sửa vàreporttruth, tiếp05C-DReview/Promptconsumer sourcegrounded. HIGH, khôngshutdown. PaletteG08/residualWhisper/longbatch chưabỏ.
