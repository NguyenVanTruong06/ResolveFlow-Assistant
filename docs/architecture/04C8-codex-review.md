# G04-04C8-I — Codex review (2026-10-27)

CHANGES_REQUIRED (lifecycle nhỏ còn thiếu, không mở lại toàn thiết kế). Codex exact5focusedtargets49passed,21Qt warnings,6.87s,exit0. Đã đối chiếu guarded set_asset/start/replay/openexternal, removal alias/simulation và browse disabled.

Blocker: _playback_epoch chỉ tăng, không được đọc trong callback/connection. _ensure_player nối signals trực tiếp vào bound methods. Test stale chỉ gọi _on_position_changed khi inactive, không phải A queued callback sau B active theo workorder. Probe real QWidget: giữ old_position_callback rồi đặt B active/epoch2, old_callback(99000) -> B_position=99000. Probe inactive + late PlayingState -> nút đổi thành Tạm dừng. _on_player_error không gate và có thể stop B bởi lỗi A; duration/position chỉ reset ở constructor, không set_asset. Đây là lifecycle defect đã có source/probe, không phải suy đoán concurrency.

Residual UX trong cùng state reset: guard reason bị bỏ qua, denied chỉ Không khả dụng, không phân biệt offline/staging; error_string cũng không được hiển thị. Bổ sung thông báo Việt/tooltip state giữ nguyên sau late callbacks. Không đổi core guard/AudioInspector/directFusion. Task04C8-R1 exact1module +focusedtests; không desktop hoặc wholechecklist.

## Nghiệm thu R1 — 2026-10-27
ACCEPTED trong scope code Video Inspector. Codex đọc _start_session/_disconnect_session và slots, recheck guard/state reset; chạy exact5focusedtargets50passed,21Qtdeprecationwarnings,6.77s,exit0. Test mới capture actual connected closures của A, bắn sau B active, epoch được so sánh trong wrapper và slot; test repeated play/pause/resume/replay và tooltip reasons đạt. Không còn chỉ tăngcounter hoặc test inactive thay cho Bactive.

Bằng chứng mô tả callback cũ đã capture và behavior được test, không bảo đảm mọi backend late event tự mang mediaidentity (Qt signals không có mediaID). Không dùng claim100%stalecallback cho toàn native backend. Codeaccepted không chứng minh decoder/render/QMetaObject disconnect trên realplayerdesktop; giao smoke hữu hạn 04C8-AUTO-UI, không rerun suite. Audioengine/directTextFusion và relink còn deferred, khôngshutdown.

## Desktop component acceptance — 2026-10-27
ACCEPTED / COMPONENT_DESKTOP_VERIFIED. Codex đọc task/desktop_verify_04c8.py, desktop_verify_04c8.log và report; xem trực tiếp case3_3_asset_b_playing_smpte.png có frame SMPTE thật trong Inspector. Log source A/B đúng, sink frames/color và desktop pixel evidence, actual QTest button play/pause/resume/replay, singleton và4ownedconnections, selectionreset/noautoplay, directwidgethide/show, denied missing/staging/browse-disabled, owned-session cleanupSUCCESS vàFINAL_PROCESS_EXIT0. Không rerun evidence/tests sau khi code không đổi.

Giới hạn ghi đúng: harness component, không main.pyE2E/fullStudio tab-switch/parent-containerhide riêng (script gọi widget.hide), không physicalspeaker/audiohotplug/nativeResolveDrop. Staging trên widget đã có player chứng minh no-play/reuse, không chứng minh no-player-first-use trong ca này; unitlazyproof giữ phạm vi riêng. Connection count4 cộng code+unit tests không chứng minh mọi native backendrace. Không nâng claims100%. Gate04C8 đóng trongscope. Next04C9-D Inspector audio thật; directTextFusion/legacy/relink và G05–G09 vẫn còn, khôngshutdown.
