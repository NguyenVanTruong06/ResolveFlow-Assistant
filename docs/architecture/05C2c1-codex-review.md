# G05 EvidenceR1 + C2c1-I — Codex review
Ngày:2026-10-27
**NEEDS_FIX — G05-05C2c1-R1**. Chưa V2worker/UI.

## Fresh verification
Đọcreport,mapper380lines,tests,harness vàmetricsfaultprobe. Chạy:
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_kinetic_dataset_mapper.py tests/core/contracts/test_time_contracts.py tests/test_timeline_mapper.py -q
**52 passed in1.05s exit0**. Khôngfullsuite/desktoprerun/model/Resolve.

## Phần đã nhận
PuremodulekhôngQt/IO;Fraction+half-even,clippedintersection/0framedrop,duplicateinstances/gaps,validinputtests. Faultmetrics ghiactualworkerboundary,0running/sessionremoved/exit2;nhậnbằngchứngđúngrunđó,khôngcoiallbranchesan toàn.

## P1 mapper — strict contracts còn thiếu
Probe extract_source_words(None/False/0/{}/'') đều trả(),do if not raw_subtitles trướctypegate;malformedkhôngđượcgiảno-dialogue.
Probe SourceWord.clip_id=OTHER_CLIP đặttrongmappingkeyC01,WordPlacementrecframes0.5/24.5 ->MappedWord.clip_id=OTHER_CLIP,record_start_frame=0.5(float).
Probe placementMISSING_CLIPkhôngcókeytrongsource_words_by_clip ->words()/warnings() thayvìerror;nhầmidentity bịgiấunhưsilent.
Frozen dataclasskhôngvalidation:map APIphảivalidaterecordframeintnotbool,IDs/sourceworddomainvàmappingkey;khôngtrustcaller.

## Harness residual
Restore dùng classmethod(orig_bound_method),probe call sau đó ->TypeError multiplevaluescache_dir. patches_restored=Truehiệnđượchardcodekhôngverify.
SafeMimeSnapshotgiữfullMIME nhưngownershiphashchỉcurr_text;sametext+differentHTMLvẫncóthểoverwriteclipboarduser. Yêucầuexactallformatsbyteschưađạt.
Teardownvẫnclose/deleteLater windows kểcảstill_running>0:chưađúngownershipfailurepolicy. Khôngbắtlặp4casetrước;fixharness+1faultprobe/DIownershipchecks.

R1đúng1productionmapper+tests/harness/docs. KhôngUIworker/engine/timecontractrefactor,newfeature hoặcall-doneG05. NghỉsauG05/khôngshutdown.
