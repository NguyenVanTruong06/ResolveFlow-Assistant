# G05-05C2b-R1 — Codex review (2026-10-27)
**NEEDS_FIX_TWO_RESIDUALS — G05-05C2b-R2**

## Fresh verification
Đọc report,3modules,6regression R1.
Finite6files command giữC2b:
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/test_prompt_preparation_routing.py tests/ui/test_review_page_visual_consumption.py tests/ui/test_director.py tests/ui/test_source_analysis_routing.py tests/ui/test_source_analysis_visual_describe.py tests/core/test_prompt_authority_consumption.py -q
**134 passed in45.45s exit0**. Khôngfullsuite/realmodel/desktop/Resolve.

## Nhận các sửa
Fullsignature16fields+runtimeSourceattrs;finalpoststatmọipaths sauformat;requestmanifesthandoff;duplicateIDconsume trướccontroller. Khôngmởlại các phần này nếukhôngregression.

## Residual1 — Busy control kẹt theo thứ tự worker kết thúc
ProbeQt widget với registryA/B vàactiveB:
- Bphysicalfinishedfirst ->disabled,owners[A] (đúng khioldA cònchạy).
- Aphysicalfinishedlast ->disabled,owners[],is_analysis_idle=True (SAI: nút khôngbao giờmởlại).
Handler chỉrestore nếu req_id==activeB vàregistryempty; Bfinish lúcAcònrunningthìskip; Afinish saukhôngactive nênskiptiếp.
RegressionR1 dùng phantomAfinished lúcBrunning,khôngcheckthứtựBtrướcA thật. Đây là residualchínhyêucầurestorephysicalbusy.

## Residual2 — Context numeric exception
Probe _sanitize_and_validate_context với validpaths và max_windows=float('inf') hoặc max_prompt_bytes=float('inf') ->uncaughtOverflowError.
Intconversion chỉcatch ValueError/TypeError;khôngfinite/range/boolvalidation,signaturehelper cũngfallbackdefaults/exceptionchưaan toàn. Cácinputbadcontexttesthiệnchỉcase thiếuđườngdẫn. Khôngnêncoerceunknownconfig thànhmatchingoldrequest.
Director Sourceattrread exception hiện log nhưngtrảpartialdefaults;phảifailclosednếuproducercontextđọc lỗi thayvìgiảdefaults.

## Giới hạn evidence/test isolation
Testmanifestisolation đặt manifest_cache_dir=None vàworkerdefaultexpanduserhome; chưacófixturechuyểnroot nàyvềtmp. Suite review đãchạytheocommand;khôngclaim0usercachetouch. Khôngxóacacheuser. R2chỉsửafixturecôlậpdefaultpathvềtmp,khôngđổiproductionpathpolicy.

R2khôngnewfeature/core/V2. G05chưađóng,khôngshutdown.
