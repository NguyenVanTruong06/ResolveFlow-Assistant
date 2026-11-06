# G04-04C4-I — CHANGES_REQUESTED

Codex đọc convertedStudio branches vàservice dispatch/audio/video/nativetrans ordering; chạy đúng4files: **79 passed in29.32s, final exit0**. Payload origin giữ, repo fallback/directAPI vàtwolegacy mutation signals đãloại ở convertedflows. Chưadesktopverified.

Hai concrete regression probes dùng actual TabAssets._insert_at_playhead với service spy, khôngQt/Resolve/storage mutation:
1. Combo nhãnthật `Audio Track 2 (A2 - SFX)` từ _update_inspector_details -> **DEFAULT_REAL_UI_AUDIO=timeline_track 2**. Headless test defaultkhôngcombo vẫnpass nhưng desktopdefault fixedA2 làm bỏ `_find_free_audio_track` và hồi quy bugA2busy đãchốt04A. BGM default `Audio Track 4/5 (A4/A5 - BGM)` tươngtựbịparsefixedA4.
2. B-roll durationNone +pathmp4 -> **UNKNOWN_VIDEO_DURATION_SENT=3.0**. _insert_video duration>0 truyền duration_sec xuốngAPI, cóthể cắtclip thành3s; task đãcấm invent3s khiunknown. TrướcUIvideodirect không truyền durationlimit.

R1 chỉTabAssets.py: audio actualcombo default phảiauto/prefNone, explicittrack giữopt-in, unknownvideo duration0/no-limit theoexistingservice sentinel. Tests dùng realcombo populated theo actual audio Inspector, không chỉheadless thiếuwidget. Có negative/invalidduration tests tránhfloat exception. Desktop gate giữpending, chưa gửishutdown.

## Review R1 — CODE ACCEPTED
Codex đối chiếu combo audio first item userData auto/prefNone và legacy labels; video helper ưu tiên duration_sec, unknown ->0/no-limit, positive finite giữ. Tests có actual Inspector combo setup, A2busy->A3/recordFrame108, unknown video qua actual service nhận duration_secNone. Chạy đúng4files: **84 passed in30.60s, final exit0**.
04C4/R1 CODE ACCEPTED trong converted media scope, chưa desktopverified. Không lặp test hoặc sửa thêm cornercase khi chưa có bug thực; next AUTO_DESKTOP_TEST theo quyền user, không WAIT_USER_TEST. Text/directFusion/drag/hover availability còn deferred, không toànG04 ALL_DONE.
