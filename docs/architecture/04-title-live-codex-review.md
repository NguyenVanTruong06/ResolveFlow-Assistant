# G04 final Title live evidence — Codex review (2026-10-27)

DESKTOP_PARTIAL / CHANGES_REQUIRED trong đúngcaTitlehiệntại. Đãđọc report/metrics/harness vàxemảnh03_case2_resolve_viewer_rendered.png. NhậnboundedpositiveUI/Gatewaydispatch,1clipadd/inflightblocked,typedtextreadback/comp/UndoUID vàdeniedsetting/staging trongscope. Khôngmởlạicáccaseđãđạt.

Khôngnhận4/4PASS haytitleRenderPASS:
- Actualduration71frames, requested3.0s@24fps72frames. Đây làliveconfirmedoff1 theoendFrame=dur-1, khôngexact3s. Recordunitcode120pass giữhistorical khôngthaylivemetrics.
- ẢnhResolveviewerđen với 'Font Not Found: Liberation Sans Black'; dòngchữnằmtrongStudioLivePreviewcanvas khôngphảiviewerResolve đãrender. NodeinputsStoredText/MediaOutconnected khôngđủproofvisibleTitle.
- Harnesscase2 chỉcheckUID/comp/textstoredkhôngcheckrender/duration; finalallpassed chấpnhậnPARTIAL thànhDESKTOP_TESTED_PASS, làmoverclaim. Cầnstrictverdict, khôngđổimụcđíchtest đểchoPASS.
- Readbackkhônggọi _ensure_media_out_connected đểrepaircomp rồiclaimproductionconnected; dùngFindMainInput.GetConnectedOutput read-only.

Active04C11-FINAL-R1scopeONLY1bridgeendFramefix+focusedregressionvàtargetedTitlelive. Chọnfontfamily/stylepairđãcótrênmáy+Resolveđểtest (khônginstallfont/chỉnhhệthống). NếuproductionvớipairsupportedvẫnrenderfailthìreproBLOCKED/FAIL, khôngfakeviewer. Undo/denied trướcgiữkhôngrerunwholechecklist. G04khôngđóngsainhưngkhôngnewfeatures, saucađạtchốtG04+G05.


## Quyết định cuối — G04-04C11-FINAL-R1 (2026-10-27)
ACCEPTED trong phạm vi Title cơ bản. Review source endFrame=dur, metrics exact 72 frame @24fps và ảnh Resolve viewer hiển thị chữ với Liberation Sans Bold. Codex chạy lại ba file focused: 44 passed in 5.46s, exit 0. Hai lỗi live 71/72 frame và thiếu bằng chứng render đã đóng trong cấu hình thử nghiệm; không suy luận mọi font/style hỗ trợ. Xem 04-gate-acceptance.md: G04 ACCEPTED_WITH_RECORDED_LIMITS, tiếp G05-D. Các kết luận CHANGES_REQUIRED trước là lịch sử đã được bản này giải quyết trong đúng phạm vi.
