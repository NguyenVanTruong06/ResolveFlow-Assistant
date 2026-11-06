# G04-04C11-D — Codex review (2026-10-27)

DESIGN_REVISION_REQUIRED, chưaphêduyệt2moduleimplementation. Đãđọc actual resolve_bridge.insert_title_at_playhead, TitleParams.from_dict/text_tool, InsertService.insert/dispatch và ResolveGateway.submit. Không chạytest/app/Resolve.

1. End-to-endparamloss chưađượcgiảiquyết: bridge thực dùng TitleParams.from_dict vẫnstripstroke/timing/box/glow/animationfields, vàstyleTITLES làgeneratorhệkhác vớiproceduralTextStylePreset. Truyềnextra_params đủđếnbridge khôngđồngnghĩa renderđúng. Design tựnêu loss rồikhẳngđịnh2modulespreserve làmâuthuẫn. Cầnexactfield->actualrenderednode input/expressionmappinghoặcdeclareunsupported chứkhôngsilentdefaultpreset. KhôngđụngUI trướcđườngrendercontractđãchốt.
2. UI pseudocode payload=to_asset_dict(custom_preset or selected_asset) làbypassauthority: mấtmanagedorigin/source/librarypath. Guard originalselectedasset nguyênmetadata, clonechỉcustomdata đãvalidate. _raw_obj cấp thực trênGUIkhôngđồngnghĩađượcgửiQt/Resolveproxy qua worker payload.
3. Timeout15s existingQEventLoopwrapper chỉngừngđợi, Gateway taskvẫnchạy. finallyreset_inserting rồiclicklại cóthểduplicate mặc'don'tautorerun'. Cầnoperationpending sốngđếnterminalcallback, lateoutcome gắnoriginalasset/requestID, unknownnotfailednomutation. UIasynccallbackđượcưu tiên, khôngnestedloop nếukhôngcần; khôngthêmcancelguarantee chưa API.
4. bridge.context() tạo independentResolvecontext từglobal connection, khôngnhậnresolveparamtask, returnTimelineItem thật. Phảiđốichiếugatewayboundcontext/activeproject-time/clipidentity, nofakegatewayDItest chỉassertbridgecalled. Item_ref schema đãcósẵn nhưngkhônghứa tấtcảproxythreadsafe/Undo30s nếuchưatestactualdeleteroute.
5. bridge tạo carrier thật đểImportFusionComp cho TITLE; cấmcarrierfake choNATIVETRANSITION khôngđồngnghĩa cấmmọi titlecarrier. Nhưng rawcarrierclip khôngthànhfinishedtitle nếuimport/renderfail, rollback phảiowned vàtruthfulpartialfailure. bridgeAppendToTimelineendFrame=dur-1 cũngchưađượclivefix04C5 chạm, ghiUNVERIFIEDtitle exactframes, khôngtrộmđổiframebounds chỉtừvideotest.
6. File-backed.setting truthfulguidance/zerojobs/nomutation đượcduyệt chofirstscope, ceaseinsert_title_requestedsignal giữdeclaration đượcduyệt. NoimplicitFusionpage/OSpaste choactionChèntạiPlayhead trongrouteconverted: actionintentexplicit, FastPastepositive vẫndeferred.

Giao04C11-D-R1 sửadesignchỉphầntransport/rendercapability/asyncpending; khôngpytest hoặccode. Đềxuấtmộtlátcắtthậtđủmodule, cóthể3modules hoặcpurecontract/renderfirst thayvìép2moduleclaimsaifidelity.

## Implementation review
G04-04C11-I CHANGES_REQUIRED: xem 04C11-implementation-review.md (99 focused tests pass; strong identity/Undo/comp evidence defects). Active task04C11-R1.
