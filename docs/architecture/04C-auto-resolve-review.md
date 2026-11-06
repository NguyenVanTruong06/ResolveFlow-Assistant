# AUTO-RESOLVE-UI — REAL RESOLVE INTEGRATION ACCEPTED IN PART; EVIDENCE CORRECTION REQUIRED

Codex đọc saved script/task log và xem ảnh case2_audio_auto_placed_on_a3.png, case1_2_grid_hover_active.png. Không chạy lại script (cómutation Resolve).

## Bằng chứng được nhận
- Có Resolve thật và project cô lập trong ảnh/log; actual StudioWindow +InsertService +Resolve integration qua programmatic button.click(), khôngmockgateway. Audio A2busy->A3 Start86400/Duration48, đúng1clip theoasserts. Đây real-on-screen component integration, chưa main.py/input-driver end-to-end.
- Video không bịtrim3s: thực tế167frames, không phải72. Không chốt fullnatural168frame preservation: log167 và script accept167/168; cần ghi rõ discrepancy1frame, không rewrite sốthực.
- ClipV1 nodegraph có identityLUT đượcassert; baseline timelinegraph/otherclip unchanged khôngđượcscriptassert hoặc log, chưa chứngminh claim 'timeline sạch'.
- Native clipboard +zero-newclip assertions có thật; log nói không cósharedV1cut nên nearestcut behavior NOT_TESTED; macroinstalled NOT_TESTED.
- Deniedmissingfile toast/Recent/video count unchanged cóassert; chỉvideo count đượcđếm trongcase5, khônghứa mọiMutation hayaudio state unchanged.

## Ca1 false-positive do harness
Log dòng21: Hover active Overlayvisible=None, Playerplaying=None; dòng27 Subsequenthoverplaying=None. Script chỉlog hai giátrị, khôngassertplaying/overlay; missing/invalid first_index vẫn markPASS. Scriptchip mousePressEvent(None) hoặc gọi privatehandler; source model stale sau direct all_assets insert. Ảnh hover hiển thịgrid title, warning tài nguyên không tồn tại trongkho; không chứngminh hovervideo. Filterstop cóthểPASS vì player/overlay chưa từng được tạo. Ca1 chưađạt. Không quy thànhbugproduction khi harness chưa chọn đúngasset.

## Safety của harness phải sửa trước reuse
Script chỉWARNING khi project không đúng tên, rồi vẫn SetCurrentTimecode/SetLUT. Phải hardabort trước mutation nếu exact expected project/timeline khôngmatch; không chỉsubstring. Snapshot/fixture restore trongfinally. Khônggiữ codeharness unsafe cho tươnglai.

Task R1: sửa saved acceptance harness/assertions/report, chạy bổsung ca1 vàread-onlyLUTgraph baseline/smoke mainapp. Không rerun tấtcảaudio/LUT/mutations đểđổi green; bổsung exactframe diagnostic read-only vàactualinput/mainentry smoke cầnthiết. Không codeproduction, khôngshutdown.

## Review AUTO-EVIDENCE-R1
Codex đọc savedscript/evidence_r1_execution.log và xem r1_case1_2_grid_hover_active_playing.png. Positive assertions bâygiờ cósuggestionselected đúngID, overlayvisible/PlayingState, filterstop vàsubsequentplayingTrue. Nhận desktop on-screen Qt-input component integration, không claim toànmainentry. Hardabort exactproject/timeline đượcghi trước mutations.
Audio A3/start86400/duration48/1clip đượcgiữ evidence lượttrước. LUT readback targetV1 identityLUT, V3 vàtimelinegraph=[] đượcnhận làstate lúcđọc; không nói cótemporal beforebaseline nếu chưalưu. Nativeguidance/zeroclip PASS, sharedcut/macroinstalled NOT_TESTED.
Exactduration FAIL confirmed: ffprobe vàMediaPool168frames, Timeline167frames; code append endFrame=dur_frames-1. Chốt narrow fix ởResolveAutomation.insert_media_at_playhead, không sửa append sites khác/speed/audio/timecontracts. Một mới placedclip phải168frames vàdurationlimit3s phải72frames; 1-frame explicitclip phải1frame. Khôngsửa oldclip167 đểgiảafterPASS.
