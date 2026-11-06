# ChunDVC — Lộ trình chuẩn hóa kiến trúc

> HIỆN HÀNH 2026-11-06: Gate G07 ĐÃ ĐÓNG (Ratified B2-M1 Single-Owner Governance); Gate G08 ĐÃ HOÀN THÀNH (NLE Obsidian Studio UI Polish — 119/119 UI tests passed); Gate G10 ĐÃ HOÀN THÀNH (v1.0.0 Public Release Ready — standalone packaging & English documentation complete).

> LỊCH SỬ 2026-11-02: G07-07A7-I ACCEPTED_WITH_RECORDED_LIMITS; Codex 217 tests/23.81s/exit0. Active next: G07-07A8-D DESIGN_ONLY for read-only recovery assessment UI/guidance. No recovery execution or Resolve mutation; ready_to_execute=False; G07/M1 remains open.

> HIỆN HÀNH 2026-10-29: active duy nhất LIB-PUBLISH-02B HIGH; companion fixes accepted trong scope, Codex120tests/23.56s/exit0. Archive3.18GiB SHA256 independently verified, Drive403 insufficientParentPermissions (chưa upload). Next verified-browser upload và empty-library/runtime cleanup + source retirement có checksum/rollback, giữ2localbackups; G06 checkpoint, no shutdown. Xem LIB-PUBLISH-02A-codex-review.md. Các entries active cũ bên dưới là lịch sử.

> HIỆN HÀNH 2026-10-29: active duy nhất LIB-PUBLISH-01-R1-THEN-02A HIGH tại .bridge/task.md. Codex111tests/24.34s/exit0 nhưng3residual probes confirmed; sửa2modules rồi full archive/restore proof và upload mới nếu đủ quyền. Chưa cleanup source/runtime, G06 giữ checkpoint. Xem LIB-PUBLISH-01-codex-review.md. Các ghi chú active cũ bên dưới là lịch sử.

Cập nhật2026-10-29: Ưu tiên người dùng mới, active duy nhất LIB-PUBLISH-01-I HIGH (manifest master/preview, giữ companions qua commit/runtime, inventory và pack mẫu). Tiếp theo sau review: LIB-PUBLISH-02 xuất kho thật và upload release vào Drive root 1b6zuDHXoCRXBfzI-Q18weYZ2hsNsd1Hq. G06-06A4b-I giữ checkpoint, chưa nghiệm thu/không chạy song song. G01–G05/A1/A2/A3/A4a giữ accepted trong limits. Không G06 closed/G07/shutdown.

## Mục tiêu và cách làm
Chuẩn hóa từng luồng thực tế của công cụ rough-cut offline, giữ ứng dụng sử dụng được sau mỗi thay đổi. Không viết lại toàn bộ repo hoặc chuyển hàng loạt module. Dùng JSON cho kho/metadata; PySide6 model/view cho UI; ResolveGateway/bridge cho Resolve. Mỗi giai đoạn chia tiếp thành các đơn vị có thể nghiệm thu riêng.

| Giai đoạn | Phần nhỏ | Kết quả bắt buộc | Điều kiện bắt đầu |
|---|---|---|---|
| G00 | Bản đồ hiện trạng, nguồn dữ liệu và call graph | Báo cáo có file:hàm:dòng, backlog ưu tiên, baseline có bằng chứng | Đang giao |
| G01 | Asset IDs/taxonomy; schema/version; status/error/time contracts | Hợp đồng dữ liệu + adapter tương thích, bộ mẫu JSON | G00 được review |
| G02 | Path/offline; registry/snapshot; transaction/migration | Một cơ chế quản lý kho có phục hồi lỗi, không fallback media nặng | G01 |
| G03 | Download/staging; inspection; review/commit/cancel | Một luồng nạp pack được kiểm duyệt, bounded IO và lifecycle rõ | G02 |
| G04 | Source model; search/filter; selection/preview | Studio/Dock cùng dữ liệu và trạng thái, custom tags dùng được | G03 |
| G05 | Source inventory; Whisper; visual; cache/orchestration | Một hành động phân tích hình + thoại theo timestamp, tái dùng cache | G01; ổn định kho trước |
| G06 | Bounded batch planning; tóm tắt phân cấp; matching offline; edit-plan JSON | Kế hoạch dựng tham chiếu source/asset IDs, có evidence và ngân sách | G04 + G05 |
| G07 | Validate/preflight; Resolve execution; recovery | Đã đóng (Ratified B2-M1 Single-Owner Governance) | G06 |
| G08 | Dock/Studio/Director lifecycle; audio; shell UX | Đã hoàn thành (NLE Obsidian Studio UI Polish) | Kiểm tra sau G04, chốt sau G07 |
| G09 | Nghiệm thu thực; đo hiệu năng; đóng gói | Checklist hữu hạn + benchmark theo lô + hướng dẫn cài/chạy | Các giai đoạn code đã đạt |
| G10 | Tài liệu tiếng Anh; giới thiệu GitHub; vệ sinh repo và phát hành beta | Đã hoàn thành (v1.0.0 Public Release Ready) | G09 nghiệm thu; xem [kế hoạch G10](10-public-release-github-plan.md) |

Ưu tiên có thể thay đổi nếu G00 tìm được P0 có bằng chứng: Codex ghi quyết định và thu hẹp task sửa trước. Không chạy đồng thời các task cùng sửa file lõi.

## Chu kỳ cho từng phần nhỏ
1. **D — Thiết kế:** đọc đường gọi thực, chỉ ra vấn đề có bằng chứng, ghi sơ đồ dữ liệu/state, chủ sở hữu module, API/schema, tương thích và test cần thiết. Không sửa code ứng dụng.
2. **I — Triển khai:** Codex phát task cụ thể sau review D. Antigravity sửa đúng phạm vi đã chốt, thêm regression có ý nghĩa và chạy nhóm test liên quan. Không tự mở rộng refactor.
3. **R — Nghiệm thu:** Codex kiểm tra diff và ca hồi quy; có phần phụ thuộc thiết bị thì chuyển WAIT_USER_TEST. Báo cáo chưa đủ bằng chứng là CHƯA XÁC MINH, không bắt Worker lặp toàn bộ kiểm tra.
4. **Đóng:** ghi kết quả, giới hạn và residual issues. Chỉ mở lại nếu có bug tái hiện hoặc contract downstream bị ảnh hưởng.

Mỗi lần sửa chỉ thực hiện một phần nhỏ, thông thường 1–3 module production có quan hệ trực tiếp. Nếu cần sửa nhiều call sites, nêu danh sách và lý do trước. Không đặt chỉ tiêu số dòng để ép thiết kế.

## Bằng chứng hiện có
- UX code đã qua nhiều vòng review, nhưng desktop/hotplug/global hotkey/drag-drop chưa nghiệm thu hoàn toàn.
- Kho đa tầng vừa được Codex triển khai, báo cáo `.bridge/storage-report.md`: nhóm102 testpass; sau guard manifest nhóm23 storage/UIpass. Đây là các tập giao nhau, không cộng thành125 test.
- Vision local đã chạy thử GPU với2 clip development; chưa chứng minh hiểu hành động phức tạp hay xử lý3 giờ footage.
- Không coi các báo cáo này thay thế việc đối chiếu code hiện tại hoặc coi repo sạch. Workspace có dirty và untracked từ nhiều phiên.

## Quyền sở hữu đề xuất để review ở G01
- `src/core/`: schema, taxonomy, thuật toán/validation không phụ thuộc QWidget; không gọi UI để báo lỗi.
- `src/services/`: điều phối IO/job/cache/Resolve gateway, chuyển kết quả có cấu trúc cho UI.
- `src/ui/`: model/view/delegate, tương tác và hiển thị trạng thái; không tự ghi registry hoặc gọi Resolve bỏ qua service.
- Đây là hướng đích, không phải lệnh di chuyển toàn bộ file ngay. Các API hiện hữu phải được adapter bảo vệ khi thay đổi.

## Hộp thư / nhật ký
- Task hiện hành: `.bridge/task.md` (chỉ một task active).
- Worker báo cáo: `.bridge/report.md`, phải có TASK_ID và trạng thái DESIGN_READY / IMPLEMENTED / BLOCKED.
- Artifacts lâu dài: `docs/architecture/` và `task/architecture-2026-10-26/`.
- Tracker: `task/architecture-2026-10-26/STATUS.md`.
- Antigravity không tự mở giai đoạn tiếp hoặc sửa STATUS/task hiện hành. Codex cập nhật sau review.

## Quy tắc nghiệm thu
Chỉ chạy `.venv/bin/pytest` với danh sách file/node IDs liên quan. Không full suite. Không cài dependency, tải model, di chuyển dữ liệu thật, xóa cache, mutate Resolve thật hoặc publish khi chỉ được giao khảo sát/thiết kế. Phần triển khai sau này cũng không được tự làm việc ngoài phạm vi task.

## Quyết định sau G00
Đã xác nhận Dock drop và Studio generic import bỏ qua wizard. Ưu tiên hotfix hữu hạn G00-H1-I trước G01-D. Review chi tiết: docs/architecture/00-codex-review.md. Không phê duyệt gộp QSettings hoặc thay toàn bộ asset schema chỉ từ khảo sát.

## Review G00-H1-R1
Ba probe P1 đã đạt và Codex chạy31testpass. Residual nhỏ: partial toast gọi show_info không tồn tại; giao sửa đúng call+regression, sau đó làm G01-D trong cùng workorder. Chưa cho G01-I hoặc thay QSettings/schema production. Review: docs/architecture/00-H1-codex-review.md.


## Chỉ đạo người dùng mới nhất — Điểm dừng sau G05 (2026-10-27)
Tiếp tục hoàn tất và nghiệm thu G05 trong phạm vi đã chốt, gồm Review/Prompt và các kiểm tra còn cần thiết; không dừng giữa G05 chỉ vì chỉ đạo nghỉ sau gate. Khi G05 đã thực sự đạt, ghi trạng thái G05_COMPLETE_WAIT_USER_RESUME rồi dừng giao việc/thực thi. KHÔNG tự mở G06/G07/G08/G09 hoặc lấy backlog các gate sau làm việc tiếp. Người dùng sẽ cho tiếp tục sau khi quota hồi; không tự suy đoán thời điểm quota hồi, không tự resume theo timer. Giới hạn chưa kiểm chứng phải ghi trung thực; blocker thật thì báo rõ, không đánh dấu G05 xong giả. Không ghi ALL_DONE cho toàn roadmap, không tắt máy.


## Codex review G05-05C1 — 2026-10-27
Fresh finite suite 46 passed in 3.62s exit0; chưa nghiệm thu do probes duration lỗi không terminal, no_audio runtime lấy transcript cache cũ, cache miss đếm silent và visual endpoint mismatch không stale. Review: docs/architecture/05C1-codex-review.md. Active G05-05C1-R1 HIGH tại .bridge/task.md; sửa đúng ba modules, không C2/G06 hay live/model mới. Hoàn tất G05 mới nghỉ; không shutdown.


## Codex review C1-R1 — 2026-10-27
Fresh54testsPASS4.28s exit0. NEEDS_FIX: source mất giữa read bị skip post-stat; source đổi vẫn publish oldrows; null/missingruntime subtitles suy silent; snapshot shallow copy/test không chạy runtimebranch. Review: docs/architecture/05C1-R1-codex-review.md. Active G05-05C1-R2 HIGH, chỉ residual R1; chưa C2/G06, G05 chưa đóng. Không shutdown.


## Dừng sau review C1-R2 — 2026-10-27
WAIT_USER_CONTINUE. Codex fresh63testsPASS5.78s exit0; nhận sửa source invalidation/transcript types/per-clip isolation/close guards. Chưa nghiệm thu C1 toàn phần: malformed snapshot xóa UI trước ValueError; all-missing source terminal completed; synchronous chronological sorter có đường ffprobe GUI. Review docs/architecture/05C1-R2-codex-review.md. Không giao workorder mới; chỉ tiếp tục khi người dùng yêu cầu rõ. G05 chưa đóng, không shutdown.


## User RESUME / C1-R3 — 2026-10-27
Người dùng nhắn “Tiếp Tục”; dừng sau báo cáo C1-R2 đã được gỡ. Active duy nhất G05-05C1-R3 HIGH, đúng hai modules worker/ReviewPage; đóng validation/UI state, source availability terminal và GUI sorting I/O. Thứ tự raw paths giữ callerorder, không tự probe media để sort; không khẳng định giữ mọi chronology cũ. Không chạy test mới khi chỉ giao workorder; số63pass5.78s là review trước. G05 chưa đóng, C2 sau review; nghỉ sau G05/không shutdown vẫn giữ.


## C1 CODE ACCEPTED / C2-D — 2026-10-27
Codex fresh82testsPASS6.88s exit0; ba residual R3 đóng theo finite scope. Review: docs/architecture/05C1-R3-codex-review.md. Không claim mọi Review route zeroGUIIO hoặc executionauthority: getters Prompt/words còn corecache/sorterIO. Active duy nhất G05-05C2-D HIGH (designonly, chưa code); G05 chưa đóng, còn Prompt/integration/Whisperreal. Nghỉ sauG05; khôngshutdown.


## C2 design review / C2a-I — 2026-10-27
DESIGN_ACCEPTED_WITH_BINDING_CORRECTIONS tại docs/architecture/05C2-codex-decisions.md. Không pytest trongdesignreview; formatterprobe emptyinventory/noassets travelvlog6861UTF8bytes, khôngphải3000byteskeleton. Active G05-05C2a-I HIGH chỉ clip_manifest/story_copilot; boundedmodeopt-in32768bytes, verifiedaudioAPIthực, visualauthoritylimits; chưaUIasync/exportguard. G05 chưađóng, nghỉsauG05/khôngshutdown.


## C2a-I review / C2a-R1 — 2026-10-27
Fresh54testsPASS7.31s exit0; NEEDS_FIX. Probes cached{} bịverified/completedempty, schema99negative transcript đượcverified; stalemachinecaptionlọtPromptvàmấtorigin;budgetmin6701bytes khôngwarning/omission. Review docs/architecture/05C2a-codex-review.md. Active G05-05C2a-R1 HIGH,2coremodules,khôngUI/newfeatures. G05chưađóng,nghỉsauG05/khôngshutdown.


## C2a-R1 review / R2 — 2026-10-27
Fresh68testsPASS3.26s exit0,old3probesđạt;chưaofficialacceptance:validv3statuserror+subs vẫnverified/transcribed vànoaudioTrue bịcompletedempty. Review docs/architecture/05C2a-R1-codex-review.md. Active G05-05C2a-R2 HIGH chỉ1productionmoduleclip_manifest;formatterkhôngmởlại,UIchưa. G05chưađóng,nghỉsauG05,khôngshutdown.


## C2a accepted / C2b-I — 2026-10-27
Fresh77testsPASS3.13s exit0;producererror/noaudio/contradictionprecedenceđạt. Review docs/architecture/05C2a-R2-codex-review.md. Active G05-05C2b-I HIGH3modules:PromptPreparationWorker/ReviewPage/Director,explicitverifiedaudio+32768bytes,Copy/RunAIguarded,closephysicalfinished. V2pendinggate vàintegration/Whisperrealcòn,khôngclaimG05done. NghỉsauG05,khôngshutdown.


## C2b-I review / R1 — 2026-10-27
Fresh128testsPASS31.76s exit0;NEEDS_FIX3groups:signaturebỏendpoint/budgets,formattermutationvẫnsuccess,readyduplicatecontroller2calls. Manifesthandoff chưaapply vàdefaultsharedcache cầnisolate. Review docs/architecture/05C2b-codex-review.md. ActiveG05-05C2b-R1 HIGH3modules;khôngmởcore/formatter/V2feature. G05chưađóng,nghỉsauG05/khôngshutdown.


## C2b-R1 review / R2 — 2026-10-27
Fresh134testsPASS45.45s exit0;nhậnold3groups,NEEDS_FIX2residual: BphysicalfinishtrướcA->allidlebuttondisabled;InfcontextuncaughtOverflowError. Review docs/architecture/05C2b-R1-codex-review.md. ActiveR2ReviewPage/Directorconditionalonly;fixturedefaultmanifestrootcầntmpisolation,khôngclaim0usercachetouch. G05chưađóng,nghỉsauG05/khôngshutdown.


## C2b codeaccepted / DesktopV1+V2design — 2026-10-27
Codexfresh172testsPASS70.78s exit0;probesBbeforeAallidlerestore vàInf/bool/float/bigintrejectionđạt. Review docs/architecture/05C2b-R2-codex-review.md. ActiveG05-05C2b-AUTO-UI-THEN-05C2c-D HIGH,harnessownedsession0productionedits,MOCKcontroller-onlydesktopproof+V2design. G05chưađóng,V2/speechrealintegrationcòn;nghỉsauG05,khôngshutdown.


## V1 desktop review / V2 design bindings — 2026-10-27
Nhận4functionalV1cases vớiWIDGET_RENDER/seededcache/mockcontrollerlimits;khôngroot-screen/fullapp/Whisperproof. Harnesschỉtextclipboard/cleanupchưaidle ->EvidenceR1faultprobeonly;V2designbỏ60%/phrasefallback/GUIwait/inventedUIDconfidence,32768byteskhông8Ktokens. Review docs/architecture/05C2c-codex-decisions.md. Active EvidenceR1+puremapperC2c1 HIGH,1newproductionmodule;chưaV2worker/UI/G05closed,nghỉsauG05/khôngshutdown.


## EvidenceR1/C2c1 review — 2026-10-27
Fresh52testsPASS1.05s exit0;NEEDS_FIX:rawfalsytypes->silent,floatframes/wrongclipaccepted,missingclipkeyhidden. Faultmetrics0running/sessionremoved nhậnrunđó;descriptorrestoreprobeTypeError vàownershiptext-onlychưaan toàn. Review docs/architecture/05C2c1-codex-review.md. Active G05-05C2c1-R1 HIGH1productionmapper+harness/tests;G05chưađóng,nghỉsauG05/khôngshutdown.


## C2c1-R1 accepted / C2c2-I — 2026-10-27
Fresh73testsPASS1.35s exit0;strictmappertypes/identityđạt. Harnessdescriptor/fullMIME/faultmetricsđốichiếuacceptedwithlimits,khôngrerundesktop. Review docs/architecture/05C2c1-R1-codex-review.md. Active G05-05C2c2-I HIGH2modules newKineticWorker+ReviewPage;strictverifiedwordtimestamps/no0.35s/FPSfallback/DEMO,style-only>300rõ vàAutoPlanfulldataset. G05chưađóng,V2integration/speechrealcòn;nghỉsauG05,khôngshutdown.


## Codex review G05-05C2c2-I — 2026-10-27
Fresh 128 passed in 78.91s, process exit 0. REVIEWED_NEEDS_FIX: boundary probes tái hiện frame7 thay frame8, plan thiếu ID vẫn success, cancel/source mutation trong formatter vẫn success, producer status error bị bỏ qua, getter trả prompt/words cũ, validation mutate sớm và GUI manifest IO. Review: docs/architecture/05C2c2-codex-review.md. Active duy nhất G05-05C2c2-R1 HIGH, đúng2modules worker+ReviewPage; finite regressions, không tính năng mới/desktop/inference/G06. G05 chưa đóng; sau nghiệm thu thật nghỉ G05_COMPLETE_WAIT_USER_RESUME, không shutdown.


## Codex review G05-05C2c2-R1 — 2026-10-28
Fresh sáu suites: 137 passed in111.65s, process exit0. Frame8 và post-formatter source/cancel gate đã sửa; REVIEWED_THREE_RESIDUALS: FPS bị V1 sanitizer strip, explicit rational chuyển float làm mất tail word, numeric NaN/producer error masking, plan path fingerprint và validation sau mutation. Probe/log tại .bridge/ux-evidence/G05-05C2c2-R1-CODEX-REVIEW/. Active G05-05C2c2-R2 HIGH đúng2modules, không feature/design mới. Chưa nghiệm thu V2/G05; sau G05 thật đạt nghỉ G05_COMPLETE_WAIT_USER_RESUME, không G06, không shutdown.


## Codex review G05-05C2c2-R2 — 2026-10-28
Fresh142tests PASS118.71s process exit0. FPS rational/finite numeric và producer precedence được nhận trong scope. Chưa nghiệm thu toàn V2: helper fingerprint ReviewPage gây NameError với global_hook:null hợp lệ, round6 collision plan34/35frames và TypeError mixed keys. Review docs/architecture/05C2c2-R2-codex-review.md; evidence .bridge/ux-evidence/G05-05C2c2-R2-CODEX-REVIEW/. Active G05-05C2c2-R3 HIGH đúng1module review_page.py, không worker/core/design mới. Chưa desktop V2/G05 closed; sau G05 thật đạt nghỉ G05_COMPLETE_WAIT_USER_RESUME, không G06/shutdown.


## Codex nghiệm thu V2 code / Final verification — 2026-10-28
G05-05C2c2-R3 V2_CODE_ACCEPTED_WITH_RECORDED_LIMITS: fresh146tests113.15s exit0, null-safe fingerprint phân biệt34/35frames, malformed reject0mutation. Review docs/architecture/05C2c2-R3-codex-review.md. Active duy nhất G05-FINAL-VERIFY HIGH, zero production edits: real local speech/cache replay + Source/Review/Prompt + V2 desktop trong một đợt hữu hạn. FFmpeg flite và cached-small folder đã thấy, chưa load/inference proof. G05 chưa đóng; báo cáo verification chờ Codex review, sau đó G05_COMPLETE_WAIT_USER_RESUME và giao shutdown theo user; không G06/shutdown sớm.


## Codex review G05-FINAL-VERIFY — 2026-10-28
FUNCTIONAL_EVIDENCE_PARTIALLY_ACCEPTED / TWO_INTEGRATION_BLOCKERS. Real local engine/V2 functional có evidence, nhưng harness patch av.open và seed negativecache B che lỗi app. Codex real noaudio1s probe: Source completed/no_audio_track ->cacheNone ->V2 not_analyzed; unpatched PyAV19 decode_audio WAV ->TypeError metadata_errors. Không model/Whisper rerun trong review. Harness clipboard ownership/physicalcleanup và stale same-request/close-pending evidence chưa đạt. Xem docs/architecture/05-final-verify-codex-review.md. Active G05-FINAL-R1 HIGH2productionmodules +harness, targeted tests/evidence thôi, không146tests/fullsuite/design mới. G05 chưa đóng; user shutdown chỉ sau review nghiệm thu gate, không G06.


## Đóng G05 — 2026-10-28
ACCEPTED_WITH_RECORDED_LIMITS / G05_COMPLETE_WAIT_USER_RESUME. Xem 05-gate-acceptance.md cho evidence và giới hạn; không code/test mới hoặc tự mở G06. User đã cho phép shutdown sau nghiệm thu; .bridge/task.md chỉ còn G05-SHUTDOWN, chưa claim máy đã tắt.


## User resumed — Mở G06-D (2026-10-28)
Điểm dừng sau G05 đã được user thu hồi. Scope docs/architecture/06-codex-scope.md; active G06-D HIGH, một thiết kế source-grounded và đề xuất một slice nhỏ, không triển khai hàng loạt. Không G05 shutdown/test lại/G07 hay backlog ngoài phạm vi.


## G06-D review — 2026-10-28
Direction accepted với binding corrections, không chốt schema06B/06C. First slice06A1I đúng1new module pure vàtestmới, chỉ chạy tests/core/test_batch_planner.py khi implement. Bytebudget phải đo exact owned rendering, không header4200magic/token guarantee; sampledhash khác fullSHA; ledger/resume thực deferred. Không mở design mới/phạmviUI hoặc shutdown.


## G06-06A1-I review — 2026-10-28
REVIEWED_NEEDS_FIX. Counterexamples và finite correction tại docs/architecture/06A1-codex-review.md. Giao06A1-R1 đúngmodule hiện hành, không thêm feature/design hay fullsuite; G06 chưa đóng.


## 06A1-R1 review — 2026-10-28
A/B acceptedinscope; residualC+windowbounds tại 06A1-R1-codex-review.md. Giao06A1-R2 mộtmodule, không vòngdesign/newfeature. Chỉtestfilebatchplanner, không fullsuite.


## 06A1-R2 accepted — 2026-10-28
A1contract acceptedwithlimits theo06A1-R2-codex-review.md. GiaoG06-06A2-D hẹp,0production/test/modelruntime; mộtproducer actual build_clips_data verified, field/provenance matrix và đềxuất mộtfirstslice adapter. Không tái thiết kếwholeG06.


## 06A2-D review — 2026-10-28
Designacceptedwithbindings, no test/inference run. Chốt no-clamp +optionalcontextSHA +actualuserpoint; fixorigin/status/authority/coverage and wordcount estimate labels. Giao06A2-I đúng1modulepure+tests, chưa thaySinglePrompt path.

## 06A2-I code review — 2026-10-28
CHANGES_REQUESTED tại 06A2-I-codex-review.md. Fresh 62 tests pass/0.70s/exit0; probes authority/schema/signatures chưa đạt. R1 chỉ sửa adapter + tests, chưa runtime/06B/06C/G07/shutdown.

## 06A2-R1 review — 2026-10-28
CHANGES_REQUESTED theo 06A2-R1-codex-review.md. Fresh81tests/0.57s/exit0; R2 active chỉ adapter/tests, actual producer segment compatibility + nested validation + identity/status fingerprints. Chưa runtime/G07/shutdown.

## 06A2 accepted / 06A3-D — 2026-10-28
Adapter schema thật, authority và semantic context đã nghiệm thu trong pure scope. Giao thiết kế batch preview/copy reachable, giữ V1/V2 hiện hành; không code trong D.

## 06A3-D approved / I assigned — 2026-10-28
Prefix/suffix SSOT approved, no6000-byte wrapper promise hoặc repo demo stock. Source identities rechecked by worker on copy without rescan. Design review không chạy tests/runtime.

## 06A3-I review — 2026-10-28
Code chưa accepted; R1 đóng A/B/C thực tế, không scope expansion. Evidence .bridge/ux-evidence/G06-06A3-I-CODEX-REVIEW/.

## 06A3-R1 review — 2026-10-28
R2 được giao trong .bridge/task.md, không thêm design gate. Các lỗi cũ inode/selection/manifestduration đã sửa; chưa nghiệm thu từ testpass khi residual authority còn fail.

## 06A3-R2 review — 2026-10-28
Một residual chung fail-open optional binding, R3 nhỏ cùng2modules. Không mở lại DTO/content/manifest fixes đã đạt.

## 06A3-R3 review — 2026-10-28
06A3-R3-codex-review.md: main bindings verified, one small envelope omission. Gộp final gate+desktop task, không feature expansion hoặc newdesign.

## 06A3 accepted — 2026-10-28
Review limits tách component desktop/cached fixtures/offscreen/source checks. Giao06A4-D, không code trongD; không mở lại A1/A2/A3 nếu không có bug mới tái hiện.

## 06A4-D review — 2026-10-28
Duyệt hướng receiver/hierarchy nhưng firstslice thu hẹp đóng overwrite risk trước. Không phantom PreparedBatchItem source bounds hoặc snapshotID echo; không epsilon/clamp. No pytest/runtime trongdesignreview.

## 06A4a-I review — 2026-10-28
Guard main rejection paths implemented nhưng regression legitimate combinedplan cầnfix. R1 scopehelper hẹp, no redesign.

## 06A4a accepted — 2026-10-28
Xem06A4a-gate-acceptance.md; guard không đồng nghĩa drafts đãnhận. Giao06A4b-I verticalslice với exactranges/appownedbinding vàcurrentmemoryledger, không Slot1 autoassignment.

## Bổ sung G10 theo người dùng — 2026-11-02

G10 QUEUED sau G09: chuẩn hóa README và tài liệu tiếng Anh, giới thiệu GitHub (About/topics/screenshots/release notes), dọn repo có inventory và xác minh artifact/publication. README tiếng Anh ban đầu đã cập nhật ở lượt này; không coi là phát hành. Không đổi active G06-06C3-S3-R3, không thêm task runtime song song. Xem [phạm vi và checklist G10](10-public-release-github-plan.md).


## S3 code accepted; G07 design preparation — 2026-11-02
See [S3 code acceptance](06C3-S3-code-acceptance.md). Codex131focusedtests20.12s/exit0 and final probes pass. No G06 productcode changes remain assigned. Active G06-EVIDENCE-CLOSE-THEN-G07-D closes viewport/setup-finally harness evidence then prepares G07 design; G07 implementation still requires full G06 closure/design review. Do not expand G06 into G07 execution or G08 layout refactoring.


## G06 closed / G07 opened — 2026-11-02
[G06 accepted with recorded limits](06-gate-acceptance.md): planning/matching/creative binding/current approval complete, evidence closure verified. Active G07-07A1-I implements the current editorial approval receiver and blocks the legacy Director phase0/2 build bypass in3existingmodules, noResolve/IO. [Binding G07 decisions](07-codex-decisions.md) correct proposed phantom fields/APIs, targetUID/name confusion and FormulaB. Real preflight/execution follows a later bounded slice. G08 retains horizontal/adaptive UI;G09scale/platform;G10publicrelease docs/GitHub hygiene queued.


## G07-07A1 review — 2026-11-02
Main currentapproval getter/readonlyStep4/legacybuild denial implemented and actualS3positive observed. Code acceptance pending finiteclosure in [07A1-codex-review.md](07A1-codex-review.md);active G07-07A1-R1-THEN-07A2-D. No source/catalogpreflight orResolveexecution yet. G06acceptance unchanged.


## G07 current — 2026-11-02
[07A1 acceptance](07A1-gate-acceptance.md) supersedes prior pending entry. Active G07-07A2a-I: two-module backend metadata inspection/QThread wrapper per [07A2 decisions](07A2-codex-decisions.md). UI and global Quit retention deferred together to 07A2b; real source-content/CFR/execution readiness remains unverified. G07 incomplete; G08–G10 remain queued.


## Codex review G07-07A2a-I — 2026-11-01T19:49:03.547510+00:00
Fresh71tests2.85s exit0; CHANGES_REQUIRED vì catalog factory/identity/config failures báo đạt, request/result alias làm 0 terminal khi caller mutation, stat/cancel gaps. Giao G07-07A2a-R1 gộp A/B/C, same2modules; UI/07A2b chưa mở. Evidence .bridge/ux-evidence/G07-07A2a-CODEX-REVIEW/; binding docs/architecture/07A2a-codex-review.md.


## Codex G07-07A2a-R1 review — 2026-11-01T20:02:57.301503+00:00
82 fresh tests2.97s exit0;15/15 old expectations independently confirmed. Only R1-unimplemented result graph/ownership, public pair revalidation and loop-cancel residuals remain. Active G07-07A2a-R2, same2modules; no UI yet. Review docs/architecture/07A2a-R1-codex-review.md; evidence .bridge/ux-evidence/G07-07A2a-R1-CODEX-REVIEW/.


## Codex 07A2a-R2 review — 2026-11-01T20:12:44.838408+00:00
88fresh tests2.95s exit0;15old+8residual expectations pass. Same R2 requirements incompletely implemented: frozenset-only graph, shared row identity, final-loop cancel. Active07A2a-R3 only, finite F01–F12; see docs/architecture/07A2a-R2-codex-review.md. No UI/Resolve.


## Codex 07A2a accepted / mở07A2b — 2026-11-01T20:26:35.792528+00:00
Fresh95tests3.10s exit0;12/12 F01–F12 matrix independently asserted, including actual final-master cancellation boundary. 07A2a accepted within recorded metadata-only limits; no more backend repair task. Active07A2b-I explicit Step4 inspection+global Quit retention in3UI/shutdown modules,backend frozen; no Resolve execution. See07A2a-gate-acceptance.md/07A2b-codex-decisions.md.


## Codex07A2b review — 2026-11-01T20:50:31.928264+00:00
Fresh51tests11.31s exit0. Independent slot/owner probes confirmed missingoutcome binding, duplicate overwrite, ignoredprogress, hiddenCancel whilebusy, dropbypass, newjobaftershutdown, earlyclosedflag and LibraryJobqueryfail-open. Active07A2b-R1 finiteR01–R12 same3files;07A2a accepted/backend frozen. Evidence .bridge/ux-evidence/G07-07A2b-CODEX-REVIEW/;review docs/architecture/07A2b-codex-review.md. No desktop/executor yet.


## Codex07A2b-R1 review — 2026-11-01T21:13:08.027303+00:00
Fresh63tests15.72s exit0;R1slotprobes closed. Two confirmed residuals only: cachedDirector normal reopen stays shutdown (real Dock receiver),guard blanket finished.disconnect removes observers. Active07A2b-R2-THEN-AUTO-UI:2module fixes then isolated4case desktop+fault probe authorized. Backend/BuildPage frozen;review07A2b-R1-codex-review.md.


## Codex07A2b codeaccepted / evidenceclosure+nextdesign — 2026-11-02T00:19:22.495623+00:00
68fresh tests17.11s exit0;cachedreopen/ownsignalcleanup andoldprobes passed. Codeaccepted withinmetadata-onlylimits,srcfrozen. Harness closure: realheldfault/independentcleanup/fullcontainment/Step4images/distinctmode metrics. Active07A2b-EVIDENCE-FINAL-THEN-07A3-D;afterevidence6sectiondesign and<=4remainingG07milestones. Noexecutor/G08–G10/shutdown.


## Codex07A2b closure /07A3 designreview — 2026-11-02T00:33:36.051047+00:00
07A2b acceptedwithinrecordedcomponentlimits;8freshharnesstests6.66s exit0,normal0/fault2/early1metricsreviewed,separateoutput,nativecountsconsistent,Step4imagechecked. Noadditionalprodrepair. 07A3 designcorrectionsrequired:actualschema/fullhash5stat/approvalbinding/CFRprotocol/FormulaB/routes/noVFRwaiver/noplaceholder. Active07A3-D-R1 docs-only;noimplementationyet,4milestonemapretained.


## Codex07A3 designapproval /implementation — 2026-11-02T00:47:04.806871+00:00
R1design acceptedwithbindingcorrections,notpacket-as-frame. Tinyrealfixture CFR30frames withBframes2 proves packetPTSnonmonotone,globalvideo1,v:1zero,video1s/format2s. Active07A3-I:3backendmodules,decodedframeprofile+fullhash+opt-inconsumer;defaultavailability/UIunchanged,readyFalse. CacheRAMfirst;preapprovalhandoff/persistentcache/target/executorstillpending. Noadditionaldesignround;see07A3-implementation-decisions.md.
