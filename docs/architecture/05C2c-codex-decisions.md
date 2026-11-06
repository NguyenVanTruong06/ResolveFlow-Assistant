# G05 desktopV1 & V2 design — Codex review
Ngày:2026-10-27
Trạng thái: **V1_FUNCTIONAL_SMOKE_ACCEPTED_WITH_LIMITS / V2_DESIGN_ACCEPTED_WITH_BINDINGS**
Khôngpytest/model/Resolve tronglượtreviewnày.

## A. Bằng chứng đã đối chiếu
Đọc metrics.json, desktop_execution.log,harness; xemảnhcase1.
Metrics4caPASS:CopyV1pending/signal1/prompt13426bytes,RunAIfakecontrollerexact1,endpointchangedstale-reject,A/Ballidlerestore. Dùngseededv3cache,khôngWhisperinference.
Ảnh do widget.grab(),khôngroot-screen-screenshot;ảnhcase1cónútCopyV1success nhưngspeechrowsvẫndemo trướcReviewload. Khôngsuyra speechpaneltừfixture/sourceanalysisend-to-end.
Ca3 là endpointchange,khôngphảisourcefilemutation. Ca4 setclosependingflagtrực tiếp,khôngthựccloseEvent. Nhậnđúngfunctionalboundariesnày;khôngclaimfullapp/realLLM/Resolve.

## A residual: Harness safety chưađạt claim
Clipboard chỉorig_clipboard_text/setText,khôngMIME;restore bằngsubstring SmokeProject/THÔNG TIN DỰ ÁN cóthểoverwriteclipboardmớiuser. Setup/patch/fixturesnằmtrướctry;finallyclose/deleteLater rồirmtree(ignore_errors=True),chưachờmọiworkerphysicalstopvàkhôngkiểmcleanupresult. Đây làgapso_task,khônghạthấpfunctional4ca.
GiaoEvidenceR1fixharness+1faultprobeonly,khôngbắtlặp4ca/172tests.

## B. Binding V2
- Giữactualmanifest fields clip_id/content_hash/manifest_id,khôngphátminhclip_uid/UID API;confidence chỉnếuthựcproducer có vàhợplệ,khôngauto0.98.
- Defaultstrictwordtimestamps;subtitle-only ->word_timestamps_missing,khôngsilentlyphrase-level/approximate khiengineconsumerchưa hỗtrợ. No0.35/token/60s/DEMO_WORDS.
- Bỏ arbitrary60%overlap. TheohelperTimelineMapperhiệnhữu:nonemptyword∩sourcewindow thìclipintersection vàmap bằngeffectiveplacement;reportclippedboundary,khôngnóiword-exactacoustic.
- Speed dùngapplied/effectivegeometry củaeditplan/TimelineMapper,khôngrequestedrawspeed nếuexecutoroff. Puremappingkhôngđọccache/ffprobe/QSettings;IOởworker.
- Source seconds/FPS/frame policy Fraction+pts_to_frame half-evennhưG01. NoFPSdefault24/30,khôngtựthêmgapkhiexecutor khôngevidencegap.
- GUIclosecancel/deferQThread.finished,khôngwait() blockGUI.
- 32768UTF8byteskhông~8Ktokensguarantee. No“dưới3phútluônvừa”,khônghướngdẫnngườidùngtínhnăngbatchG06chưacó.
- Existinggenerate_kinetic_prompt>300words tựchuyểnstyle-only và40samplewords: khôngclaimnógửiđủallwords. WorkerV2phảirõ mode/style-onlyvsfull-indexed,khôngsilentdrop;chốtởsliceUIworker saucore.
- G05FINALphảidừng G05_COMPLETE_WAIT_USER_RESUME,khôngchuyểnG06 nhưdesignchecklistđangghi.
- Sửa tênchecklist: C1Reviewloader,C2acoreconsumer/formatter;Whisperv3 thuộc05A,khôngC2a.

## Nextslice
EvidenceR1harnesssafety+purekineticdatasetmapperI,đúng1newproductionmodule. Worker/UIV2slice sau,khôngdesignloopmới. G05chưađóng,khôngshutdown.
