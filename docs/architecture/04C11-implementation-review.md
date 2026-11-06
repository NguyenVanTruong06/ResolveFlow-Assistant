# G04-04C11-I — Codex review (2026-10-27)

CHANGES_REQUIRED. Codex exact6focusedfiles99passed18.59s exit0; đọc UIpending/servicegateway/bridge/Undo. Async/double signal/visiblebasiclimits cóimplementation, khôngnhậnclaim100%bindings.

Criticalboundaryproof:
- Bridge chỉcaptureGetName project/timeline vàrecheckGetName. Probe mockedAPItrực tiếpkhôngResolve: timelineA UIDtl-A -> timelineB UIDtl-B cùngtên trongget_carrier_clip; bridge vẫnAppend1lần vàreturnitem. Tênkhôngphảiidentity, cóthểmutatewrongtimeline.
- TitleUndo chỉso IDs khi cảstored/currenttruthy, mismatch/missingIDdowngrade âmthầm; khi storedclipUIDkhôngmatch vẫnfallbackname/start/duration rồiDeleteClips. Replacementclip khácUIDcùngtuple cóthểbịxóa; khôngđạtstrongidentity.
- PostAppendactivation LoadFusionCompByNameFalse bịbỏqua; emptycompnames/activecompNone/MediaOutconnecterrors bịswallow vàvẫnreturnsuccess, nênrawcarrier cóthểđượcbáo titlecreated. Sourcejobactualtrack dùngproxyobjectmembership thaystableID, cònfallbackV2nếukhôngtìmratrack; result/undo cóthểsaitrack khinextfree.
- Service `_task(resolve)` fallbackAutomation/globalcontext nếukhôngcóboundtimeline; khôngsamecontextguarantee. Bridgeidentityphảibound đếntaskcontext, khôngcheckA rồicallcontextB.

Task04C11-R1same3modules+tests, không rerunwholeUI/desktop/chuyểnFastPaste. KhiidentityAPIkhôngđủbằngchứng thìdeny/truthfulunsupported thayvìnameguess; fakefixtures sửa phảnánhAPIidentity đầyđủcho positives.

## R1 review — 2026-10-27
Codex exact6files112passed17.78s exit0. Same-name differentUID checks, clipUID-onlyUndo, compactivation/MediaOutcheck vàactualtrackUIDreadback đãđượcsửa; giữnguyênacceptedsubscope, khôngmởlạiUI/render/lifecycle.

Còn residual củaidentitygate hiệncó: `if initial_tl_id and cur_tl_id and ...` choUIDlostpass. Probe isolatedmock: expectedtimeline-A, initialtimeline-A; get_carrier_clip hook làm GetUniqueId trả'' trướcAppend; bridge ACCEPTED appends=1. ExpectedIDinitial comparisoncũngskipkhiinitialempty. Missingstoredproject/timelineIDtitleUndo cònskipcheck thayunsupported. Khôngphảifeaturemới; chốtmộtpatchR2onlybridge/service, UIkhôngsửa. `_get_safe_uid` khôngnênstrcoercenonstr/bool thànhidentityvalid.

SauR2pass: chuyển thẳngisolatedTitleliveacceptance theo freezeG04, khôngmởnewG04design/features. FastPastepositive/diskinstall/relink/legacy nângcấp backloggiaiđoạnsau. Khôngshutdown/ALL_DONEtoànroadmap.

## Nghiệm thu R2 code — 2026-10-27
ACCEPTED trongscope code. Codex đọc strict_get_safe_uid, expected/initial/preAppendchecks vàtitleUndo stored/currentIDs; chạy exact6focusedfiles120passed17.61s exit0. UIDlostbeforeAppendclosed, nonscalarcoercionremoved, missingstoredIDsUndounsupported. GiữnguyênR1subscopesđãđạt, khôngmởrộngreviewtínhnăng.

Next duy nhất04C11-AUTO-RESOLVE hữuhạnTitleliveownedtimeline, khôngnewG04features. GateG04freeze theo04-gate-closure-scope.md: saulivebáocáoaccepted/recordedlimits vàchuyểnG05. FastPastepositive/diskinstall/relink/fullstyle/hardware/mainE2Ebacklogphasekhác. KhôngALL_DONE/noshutdown.


## Quyết định cuối — G04-04C11-FINAL-R1 (2026-10-27)
ACCEPTED trong phạm vi Title cơ bản. Review source endFrame=dur, metrics exact 72 frame @24fps và ảnh Resolve viewer hiển thị chữ với Liberation Sans Bold. Codex chạy lại ba file focused: 44 passed in 5.46s, exit 0. Hai lỗi live 71/72 frame và thiếu bằng chứng render đã đóng trong cấu hình thử nghiệm; không suy luận mọi font/style hỗ trợ. Xem 04-gate-acceptance.md: G04 ACCEPTED_WITH_RECORDED_LIMITS, tiếp G05-D. Các kết luận CHANGES_REQUIRED trước là lịch sử đã được bản này giải quyết trong đúng phạm vi.
