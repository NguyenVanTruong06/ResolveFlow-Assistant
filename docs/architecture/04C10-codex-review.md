# G04-04C10-I — Codex review (2026-10-27)

CHANGES_REQUIRED. Fresh exact6files140passed,16Qtdeprecationwarnings8.34s exit0. Read exportguard/UIbuilder/boundedread/customclone/copy/fastpaste/contextselectors.

1. Regression ngoàiintent: managedbranch tự suy library_path từ file_path/currentroot khi thiếu. Probe correctorigin+existingt.setting nhưng thiếu library_path/relative_path -> insert/drag/preview/fusion_export đềuallowed. Trướccontract managed phải có relativepath, khôngtựfabricatemetadata. UIbuilder cũng tựthêm library_path vào payload từ originroot. Khôngcầnsuyinference đểpositivefixturespass; producerStorageManager đãcófield. Restorestrictmetadata vàtestfixtures faithfulproducer.
2. Contextcopy _on_lux_fusion_paste gọi _on_card_selected ->RecentManager.add ở6131 trước export outcome. Vìvậy copy bịdeny (native/media/missing) vẫnghiRecent, trái gate noRecentchoexport. DirectcopynoghiaRecent đạt nhưngactualcontextentry chưađạt. Khôngđổipolicyselectionhistory toàngrid; chỉroutecopyselection cầnsideeffect-free hoặcrecord_recent=False códefaultcompat.
3. File-read tests cònthiếu exactlimit/invalidUTF8/whitespace/readfault/setTextfailure/fullMIME preservation, boundedpositivekhôngđủchứngminhboundaries. Builderpre-isfile khôngfstatopeneddescriptor; thêm regularfilecheck trên fd vàboundedread vẫnrequired, best-effortTOCTOUkhôngraceproof. KhôngđòiLua parser/securitysandbox.

Giao04C10-R1 trongcùng2modules+tests, khôngchuyểnTextInsert/FastPasteengine. Giữcácpositivecustomization/copycontent vàsuccesssemantics đãcó; nofullsuite/desktop/clipboarduser.

## Nghiệm thu R1 — 2026-10-27
ACCEPTED trong scope code guardedFusionexport. Codex đọc managedrelativecontract/contextselection record_recentFalse/boundedfdreader; chạy exact6focusedfiles150passed,16Qtdeprecationwarnings11.64s exit0. Regression4intentsmissingrelativepathfail, contextvalid/denied/stalehistory0, fstatregularread vàbyte/encoding/MIME/errorboundaries đạt. No need rerun suites khi code không đổi.

Không nâng thành native clipboard desktop/Resolvepaste/Luagrammarvalidation hoặcTOCTOUproof: open trướcfstat vẫnbest-effortfilesystemboundary, khôngsecuritysandbox. Clipboardnegative guarantee ởnothandling/0write paths; setTextfailure sauOSpartialwrite khônghứaatomicrollback mọiclipboardbackend. PositiveOSFastPaste vẫndeferred, deniedshortcircuitaccepted. Contextselection policy noRecent riêngroute giữnormaluserselectionhistory. TextInsert/diskinstall/globalmenu label/relink/legacy cònresidual. Next04C11-D textinsertion service/gateway design, khôngshutdown.
