# G04-04C9-I — Codex review (2026-10-27)

CHANGES_REQUIRED trong phạm vi cùng2modules. Codex exact5focusedfiles56passed3.28s exit0; đã đọc AudioInspectorWidget/sessioncallbacks/set_asset và hoveraudio start signals.

1. Probe QWidget+settings cô lập: set_asset(duration='bad') ->ValueError, duration=inf ->OverflowError. duration metadata chuyển float/int trực tiếp trướcguard, khiến lựa chọn asset có thể crash. Metadata chỉ gợi ý, không đủ lý do báo playback thực hoặc chặn UI.
2. Probe SharedHoverPreview fake output isMuted=True, enabled=True/volume0.7: _play_audio vẫn audio_preview_starting emit1lần. Video/publicvideo cũng chỉcheckvolume. Trái promise mutedhover không stopInspector (AudioRouter có thể safemute output khi mấtdevice).
3. _start_session gọi player.play rồi gán waveform.is_playing=True/labelDừng vôđiềukiện; player loading/rejectedplay cóthể chưaPlayingState, currentcodecerror cũng cóthể bịoverwrite bởi dòngsauplay. Connecting error/duration sau setSource còn bỏ lỡ synchronousevents. Đây là sourcecontract gap với UIstate theoactualplayer; cần targetedfake boundarytests, chưadecoderdesktopproof.

Arbitration tests hiện chủyếu callbackstub/manualemit signal; cần ít nhấtactualSharedHoverPreview normal guardedentry->signal->AudioInspector wired path, mutednegative và validpositive. Report CODE/DECODER_VERIFIED từ fake/offscreen chưa đủ decoder evidence; sửa claim, physicalaudibility vẫnUNVERIFIED. Giao04C9-R1, chưa cầndesktop hoặc fulltests.

## Nghiệm thu R1 — 2026-10-27
ACCEPTED trong scope code AudioInspector + Studio hover arbitration. Codex đọc safe duration parse, audible predicate và session loading/error/state branches; exact5focusedfiles70passed3.84s exit0. Metadata malformed/nonfinite nofloat/intcrash, mutedoutput khôngemitstart, callbacks nối trước source và UIplaying theoactualstate. Regression fake synchronouserrors/loadingstate và actualTabAssets wiring thuộc suite đạt.

Không mở lại acceptedcode nếu chưacóbugmới. Offscreen/fakeplayer khôngDECODER_VERIFIED/physicalspeakerproof; source review và tests không bảođảm mọi backendrace. _safe_parse_duration cap24h là metadatahint hiện có, không trim media/durationlimit playback. Partialscope arbitration là cặp Studio grid/AudioInspector, không globalplayers. Desktop thật giao04C9-AUTO-UI, giữnguyêncoreguard/VideoInspector/Inspectordrag. DirectTextFusion/legacy/relink vàG05–G09 cònviệc, khôngshutdown.

## Desktop evidence acceptance — 2026-10-27
ACCEPTED / COMPONENT_DESKTOP_VERIFIED / BACKEND_PLAYBACK_VERIFIED trong phạmvi harness. Codex đọc harness và desktop_verify_04c9.log, xem ảnh02_audio_a_playing_real_backend.png: UIaudition thật/progress, không sóngPCM. Source/log assertions xác nhận QtbackendPlaying/position/duration, QTestpause/resume, method-triggerreplay, assetBswitch/end/hide, deniedstaging/malformedmetadata, AudioRouter registration/defaultdevice; arbitration là componentpair được harness tựwire, khôngfullTabAssets/main.pyE2E (productionwiring cóunittests riêng). Khôngrerunacceptedcode/evidence.

Limits: noPCMbuffer/frequencymeasurement, chưaphysicalaudibility/hotplug; sourcefile440/880Hz khôngchứngminh outputfrequency. Nạpstaging/denied trênwidget đãcóplayer chứngminh no-newplay khôngfirst-use0player. Screenshot làwidget evidence khôngchứngminhphysicalinput. HarnesscleanuplogSUCCESS dùngignore_errors nên chỉnhận cleanupattempt/WorkerreportedSUCCESS, khôngkhẳngđịnh toànfilesystem sạch; finalprocess0 làWorkerreport+successpath, persistentlog chưacóFINAL_PROCESS_EXITtáchriêng. Cácgiớihạn này khôngcầnmở lại desktopgate đãcóassertions đủscope; recordlog/cleanupexistence check nêncải thiện harnesslần tới.

Gate04C9 đóng trongscope code+componentbackend. Next04C10-D chốtText/Fusionactionroutes. Relink/legacy/fullmain/hardware/G05–G09 vẫn còn, khôngshutdown.
