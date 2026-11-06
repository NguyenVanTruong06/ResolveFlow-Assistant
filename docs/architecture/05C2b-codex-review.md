# G05-05C2b-I — Codex review
Ngày:2026-10-27
**NEEDS_FIX — G05-05C2b-R1**

## Bằng chứng
Đã đọc report/worker/ReviewPage/Director và8tests.
Fresh finite6files:128 passed in31.76s exit0.
Command: QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/test_prompt_preparation_routing.py tests/ui/test_review_page_visual_consumption.py tests/ui/test_director.py tests/ui/test_source_analysis_routing.py tests/ui/test_source_analysis_visual_describe.py tests/core/test_prompt_authority_consumption.py -q

## P1-A: Full context
Probe _extract_context_signature: đổi endpoint/window_sec/max_refinements/target_duration/max_prompt_bytes vẫnchữkýsame cả5ca. Frames/maxwindows cũngthiếu.
Director dùng _runtime_endpoint phantom thay _runtime_vision_endpoint, hardcodebudgets8/2/10/1. Supplierthrowfallbackoldcontext vàpreparethiếuvalidation làgap.

## P1-B: Final source và manifesthandoff
Actualworker DIprobe: formatter mutatefile saupoststat ->True/success+PROMPT_FROM_OLD_SOURCE. Finalguard phảiSAUformatter vàmọirequiredpath,khôngchỉclips_data cóthểđãfiltershort.
Workerrequest.manifest_metadata chưađượcdùng;defaultmanifest_cache_dirNone/projectnamechung cònsharedcache. Resultmanifest_metadata khôngapplyReview; _apply_json_data cònget_or_create globalGUI khi_manifestchưacó. Chưaclaimrequestscoping/IDhandoffđãđạt.

## P1-C: Exactlyonce
Probe slotDirectorready2lầnsameID+samepreparedtext ->controllercallcount2. Slotkhôngactive/consumedreqIDcheck. Reviewcallbackchưaconsume;busybuttonchỉđổilabel,khônglocktớiphysicalfinished.

## Gate
R1đúng3modules,khôngcore/formatter/V2newfeature. ChưaC2b/G05accepted;khôngshutdown. Nhận basicworker/zeroIOgetter/V2gate/oldtests nhưngkhôngcoi128passlàfullcontractpass.
