# G05-05B-I2 — Codex review (2026-10-27)

CHANGES_REQUIRED trong2modules UI hiện tại. Fresh finite6files85passed5.27s exit0. Nhận route source/review không start_pipeline(phase1), buildphase0 còn và logical terminal không unlock SourcePage ngay. Test mới9ca chưa bao phủ contract counters thật và ownership đầy đủ.

## 1. Sai contract summary và duplicate timeline-success
Worker emit nested counters {total_clips,audio:{cached,new,...},visual:{cached,new,...}}, nhưng DirectorWindow._on_source_analysis_completed đọc total/audio_cached/audio_transcribed/... flat. Probe counters2clip thật ->dialog1 báo0clip/0of0; dialog2 báo“Dựng timeline hoàn tất:completed” dù chỉsourceanalysis. Cuối hàm còn block pipeline popup sót. Phải đọc đúng schema, một summary/sourcejob, không timelineclaim; cancelled dựa terminal message/stagecounts, không suy ra unique cancelledclips bằng cộng audio/visual trùng.
Logical terminal đang enable btn_review_scan dù thread chưafinished; giữ mọi busycontrols tới physicalfinished, không chỉ mainbuttons.

## 2. Cleanup không theo sender/job và không dispose
_on_source_thread_finished gọi _cleanup_source_worker() dùng active ID tại thời điểm nhận. _cleanup_source_worker(specificoldid) vẫn clear active mới/unlock vô điều kiện, không validate đúng worker hoặc token. Registrypop chưa gọi deleteLater, worker không parent=None chưa có ownerdisposal. Gắn closure sender/worker/token khi connect; cleanup stale chỉ dọn đúngworkerold, không đụng activeB. Terminal logical xử lý đúng1lần/job, ignore stale/shutdown; physicalfinished mới release ownership/deleteLater sauthreadstop. Startfailure cũng cleanup đúnginstance.

## 3. Chặn worker đang tồn tại và trạng thái config
start_source_analysis chỉ chặn sourceactive, không _shutdown_pending hay inventory/visual/describe đangactive. Có thể gọi unifiedscan lúc VisualScanWorker đangghi cùngindex hoặc lúc inventory cònthêmclip. _set_analysis_controls_locked(False) enable visual/describe vô điều kiện cũng sai khi workerkhác cònrunning. Task yêu cầu không chạy song song: rejectstart khi worker registry cònjobkhác/chờshutdown (không đợi/blockGUI), giữ nút/tooltip lýdo. Không cancelngầm jobcũ đểtranhquyền.
Runtime results theo path không gắn snapshot model/lang/fingerprint; đổimodel hoặcnguồnrefresh cóthểgiữnhãn verified cũ. Lưu provenance runtime tối thiểu hoặc invalidate rows có chủ đích khi snapshot/inputthayđổi, không fullhashGUI. Director completion muốnrefreshReview phải so settings snapshot thật, không chỉ path; nếu chưa đủevidence thì khôngclaim Reviewupdated, giữ consumerhardening deferred.

Các test9ca đặt tên click nhưng gọi start_source_analysis trực tiếp; bổ sung QTest click và nestedcounter fixtures từworker, không thayproducer thànhflatđểtestxanh. Close test hiện chỉset_is_closed=True chứ chưaclosepending thực. Không mới features, không Vision inference/Reviewconsumerrewrite.


## Quyết định G05-05B-I2-R1 — 2026-10-27
CODE_ACCEPTED_WITH_RECORDED_LIMITS. Fresh finite6files85passed4.95s exit0. Codex đọc nested counters/duplicate guard/snapshot checks và worker-specific closure cleanup; probe actual nested2clip ->1summary, đúng2clip,0timelineclaim. Nhận ownership tới physicalfinished và guards không start khi registry cònjob; kiểm thử offscreen có QTest routes, closepending và staleAcleanupB. Không yêu cầu vòng refactor mới trong I2.

Chưa nghiệm thu desktop thực/model inference/3giờnguồn hoặc Review/build end-to-end. Default cache alias của Review vẫn candidate, renamed hit thiếualias và in-memory provenance downstream còn deferred. Scan hiện làm audio STT+sample frames, không tự hiểu vật thể/hành động; description chưa tích hợp. Các giới hạn nativecancel/cleanup best-effort giữ nguyên. Giao componentdesktop smoke fixture cô lập, không mutate Resolve hay tải/nạp model; sau smoke mới chốt bước description/downstream. Worker Medium, khôngshutdown.
