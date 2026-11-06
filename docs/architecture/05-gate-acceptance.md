# G05 — Nghiệm thu gate (2026-10-28)

**ACCEPTED_WITH_RECORDED_LIMITS** trong phạm vi đã giao.
**Điểm dừng: G05_COMPLETE_WAIT_USER_RESUME.** Không tự mở G06–G09; toàn roadmap chưa ALL_DONE.

## Phạm vi bàn giao

- Nhận diện nguồn và cache Whisper v3 theo full-content identity, validation và invalidation; transcript rỗng hợp lệ, không fallback tên file để giả cache hit.
- Source analysis bất đồng bộ: thoại, lấy mẫu hình ảnh và mô tả local VLM theo các stage đã nghiệm thu; counters/coverage/provenance và cancel/resume có giới hạn rõ ràng. Cache từng stage độc lập.
- Review nạp metadata qua worker, không quét nguồn trên GUI; V1 prompt có ngân sách byte và thẩm quyền dữ liệu; V2 dùng word timestamps thật, TimelineMapper/Fraction và fingerprint chính xác, không DEMO/0.35s mỗi từ giả.
- Kinetic source preview/Final Cut, Copy Prompt/Transcript và AutoPlan offline qua worker; fail-closed khi nguồn/plan/config thay đổi, physical worker ownership và close lifecycle.
- Hai blocker tích hợp cuối đã đóng: negative v3 cache cho no-audio clip do producer tự lưu; facade giải mã audio thành ndarray, không patch global av.open để chạy với PyAV19.

## Bằng chứng nghiệm thu cuối

Codex đọc hai production modules SourceAnalysisWorker/ResolveTranscriber, tests và harness/report/metrics/log desktop. Chạy lại đúng7 suites của FINAL-R1: **116 passed in2.65s, process exit0**. Log .bridge/ux-evidence/G05-GATE-CODEX-ACCEPTANCE/focused-tests.log. V2 R3 trước đó đã được Codex nghiệm thu146tests113.15s exit0; không cộng hai số này thành tổng test độc lập và không claim full suite đã chạy.

Codex probe mới, session/QSettings/nguồn/cache tạm cô lập:
- WAV16k mono PCM -> ndarrayfloat32 shape1600, av.open giữ nguyên.
- MP4 noaudio1s -> Source completed -> verified negativecache tự sinh; replay completed,0model calls; V2 no_dialogue thay not_analyzed; không harness seed.
- Actual KineticPreparationWorker bị chặn, đổi plan34->35frames nhưng KHÔNG tạo requestB: workerA trả success về receiver, activeID/generation giữ nguyên,0prompt_copied, clipboard không đổi, không nhận stale snapshot,0worker còn chạy. Đây là offscreen/component evidence, không desktop.
Kết quả .bridge/ux-evidence/G05-GATE-CODEX-ACCEPTANCE/probe-results.json.

Bằng chứng Worker được Codex review, không Codex chạy lại inference/desktop trong lượt nghiệm thu:

| Luồng | Bằng chứng thực | Giới hạn |
|---|---|---|
| STT local | Small CPU/float32, unpatched PyAV;16recognized English words; load99.70s, transcribe12.43s; toàn cold Source run186.82s | Giọng flite English tổng hợp, không accuracy tiếng Việt/người nói thật |
| Negative cache/replay | B không audio tự có v3; chạy lại Source0additional model loads/transcribes | FullSHA vẫn được kiểm chứng; không claim0hash/extraction khi không đo |
| Review/V1 | 1speech row từ cache thật; V1 prompt13507UTF8bytes | Không LLM/recipe thực được sinh trong run này |
| V2 | Preview3049chars, FinalCut2902chars;11mappedwords,4AutoPlanpages, Copy Transcript, V1snapshot giữ | VideoPlan là fixture offline; độ dài chars không phải token/byte guarantee |
| Close/cleanup | closeEvent với pending worker, registriesidle; targetedexit0, faultintentionalexit2; metrics0running/sessionremoved | Assertions/counters áp dụng cho các run đã lưu, không mọi đường lỗi/hardware |

Artifacts gốc: .bridge/ux-evidence/G05-FINAL-R1/metrics.json, metrics_fault_injection.json, desktop_execution.log và ảnh. Đã xem case1_source_analysis_widget.png: hai source có badge cache/no-audio rõ. Phần local VLM trước được kế thừa theo các review05B đã ghi ở ROADMAP, không chạy lại để đóng gate.

## Hiệu đính và giới hạn còn ghi nhận

- Harness FINAL-R1 vẫn tạo requestB trong case4 dù report/comment nói ngược; chỉ nhận ca đó là supersede ID. Probe độc lập của Codex bổ sung đúng same-request fingerprint rejection; không dựa claim sai để đóng gate.
- Clipboard verdict USER_CHANGED_KEPT là nhánh giữ nội dung không thuộc last-write snapshot; không đủ để suy ra người dùng chắc chắn đã sửa clipboard. Không claim đã restore clipboard trong targeted run.
- Không chứng minh độ chính xác nhận diện/thời gian từng từ với người nói thật, tiếng Việt đa giọng, performance hàng trăm clip/3giờ, mọi codec hoặc môi trường Windows/GPU. Không biến các mục này thành task mới trong G05.
- G06 edit-plan/matching/phân cấp, các backlog G02/G04 và shell/hardware gate sau vẫn chưa hoàn thành. G05 acceptance không có nghĩa toàn app hoàn thiện hay đã merge/release.

## Điểm dừng và tắt máy

Theo chỉ đạo user ngày2026-10-28: sau nghiệm thu G05 thì lưu bàn giao, dừng phát triển và giao Antigravity tắt máy bình thường. Workorder .bridge/task.md chỉ còn G05-SHUTDOWN, không code/test/model/task gate sau. Không ép bỏ dữ liệu chưa lưu hoặc force-kill. Trạng thái tắt máy được ghi riêng, không suy đoán rằng máy đã tắt chỉ vì đã giao lệnh. Chỉ user yêu cầu tiếp tục mới mở giai đoạn sau.


## User đã tiếp tục — 2026-10-28
Sau đợt shutdown được Worker ghi nhận, user yêu cầu tiếp tục. G05 vẫn đã nghiệm thu trong giới hạn trên; điểm dừng chờ user đã hết hiệu lực cho công việc tiếp theo. Active G06-D được Codex giao; không thực hiện lại G05-SHUTDOWN.
