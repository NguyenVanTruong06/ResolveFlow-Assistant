# G07-07A3 — Binding corrections trước implementation

2026-11-02. DESIGN_CHANGES_REQUIRED, chưacho sửa src. 07A2b đã accepted trong limits, không quay lại harness/UI. Thiết kế07A3 hiện có phantomfields/API và chínhsách chưađượcduyệt; sửa duy nhất doc theo các ràng buộc sau.

## C01 — Đúng schema/API thực
SourceFrameEvidence nằm ở src/core/geometry/plan_geometry_contracts.py:63,đúng5fields: manifest_id,clip_id,source_fps,total_frames,evidence_status. Không có source_digest,duration_frames,rational_fps,stream_index. Trạng thái chuẩn: VERIFIED_CFR,HEADER_REPORTED_ONLY,VERIFIED_VFR. Không tự thêm PROBABLE_CFR/DETECTED_VFR vào contractđãđạt.
VideoStreamHeader ở source_frame_evidence_contracts.py có đúng6fields: stream_index,codec_name,r_frame_rate,avg_frame_rate,reported_nb_frames,time_base. LocalHeaderAcquisitionResult/filefreshness và acquisition API dùng actualfields/signatures.
Có thể đề xuất app-owned envelope mới PROPOSED để giữ digest/path/provenance/legacyframeevidence; không giả cácfieldđã tồn tại hoặc sửa schema cũtrongD.

## C02 — Full content,provenance và approval
ClipManifest.compute_content_hash thực tế size+1MiBhead+1MiBtail,không64KB. Hash sample không fallback thành trustedfullhash vìfilelớn.
Tái dùng cache_manager.compute_full_file_sha256 (fullstream1MiB,pre/poststat+fstat,cancel) và validate_file_identity. Metadata identity đầy đủ: dev,ino,size,mtime_ns,ctime_ns; bỏcacheauthority chỉ4fields/path+mtime hoặc size+mtime.
Bảngmới phải tách observed_now_digest và expected_digest anchored trước approval. Proposal.source_binding genericJSON được revisionhash baođủ; nhưng proposal_builder hiện chỉchuyển clipID/path/duration. Trace rõproducer→builder→revision→getter→preflight. Digest đo SAU duyệt không tự là digest userđãduyệt. Missingtrusted binding vẫnblocked; rebuild/review theoD01,khoan mutate frozenapprovedproposal/6fieldapprovalrecord.
Cache candidate/stablefsproof khác fullhash đọc lại; nói rõreuse/coldread/invalidation/TOCTOU. Không khẳngđịnhcache4stat antoàn saurestart,khônginventpolicy fullhashchỉsmallfile.

## C03 — Cadence verification,và unsupported
Header/sample hay equalityavg/r khôngverifiedCFR. AllpacketPTS vàallframepresentation evidence không được đồngnhất nếuchưa chứngminh. Chốt một operational verification protocol: bao phủthứgì,chọnstream/missingPTS/Bframes/EOF/lastframe/count/timebase/deadline/limits/cancel xử lýra sao. Không cần hỗtrợmọicodec ngay nhưngunknown/boundedpartial khôngpass. Headeronly phải giữHEADER_REPORTED_ONLY.
Không thêm waiver 'user chấp nhận rủi roVFR' để bypass existingCFRcalculator. VFR/unknown hiệnblockedtheocontract; cần hỗtrợVFRsau thìđềxuấtmappolicy riêng. CodecProRes/DNxHR khôngđồngnghĩa đãCFR; hướngdẫn phải nói conversionCFR rõ nếu cần,không tựtranscode.

## C04 — FormulaB giữ nguyên
Actual quantize_cfr_video_span(src_in_sec,src_out_sec,*,source_fps,timeline_fps,total_source_frames,is_cfr,applied_speed=1).
src_in_f=pts_to_frame(src_in_sec,source_fps);src_out_f=pts_to_frame(src_out_sec,source_fps).
exact_record_sec=(src_out_f-src_in_f)/source_fps/applied_speed;
record_dur_f=pts_to_frame(exact_record_sec,timeline_fps).
Không round trực tiếp raw durationsec*tlfps để thay source-first. KiểmsourceEOF trướcquantization,half-open,noepsilon. assemble_plan_frame_geometry trả existing geometry statuses,không tự invent errorcodes/fields.

## C05 — Route proof,tránhphantom executor
_insert_video/_insert_audio/_insert_title_worker/_insert_lut là actual methods nhưng không đồngnghĩa đã consume toàn approvedDirectorplan. _insert_transition hiện copyclipboard+sharedcut+guidance,0transitionmutation; không claim nativecrossdissolve automatic.
Kinetic có existing kinetic_engine.build_pages/page_to_comp/place_on_timeline vàlegacy caller src/ui/auto/auto_window.py:4923+,cần trace thật trước kếtluậnchưa cómapping. Nêu legacy render khảdụng khác approvedGatewayready,nothingexecutedtrongD.

## C06 — D01/D02 khôngplaceholder hoặc blanketdefer
Approvedkinetic/featureunsupported không được âmthầmskip hoặc chènplaceholder. Chặn rõhoặcuserduyệtlạiproposalsupported;D02 chỉoptionalBroll/SFXomissions. Không tựchuyển featureexecution sangG08 (G08 làUI/lifecycle);G07 scope/limits phải nêu rõ hỗtrợgì vàkhôngclaim toànplanđãrender. BGM/Text/LUT không tựmặcđịnhđượcduyệt chỉvìconfigcheckbox cógiátrị.

## C07 — Lộtrình4milestones,thẩmquyền thật trướcmutation
Giữ tốiđa4milestones. Dryrun cótargetspec chưa là realtargetUID. Không tạoUIDdanhnghĩa rồicoilàProjectUID;actualGatewayquery/readback sau vẫn cầnstrongmatching.
M3phảicóminimumownership/journal/checkpoints/exactreceipt trước mutation đầu tiên;M4hardening/recovery/liveacceptance,khônghoãn toànbộsafetyjournal tới sauđãdựng.
Firstslice<=3modules vàoperationalproducer+realfixturetest,khôngpureDTOtrống. Request phải giữexplicit manifestID/clipID/path/streambinding; tuplepaths khôngđủ giữIDs/trùngbasenames. M1gồmUIgeometrydisplay thìphảicó actualUIconsumer trongscope hoặcđánhdeferred;3modulelist hiệnkhôngsửaBuildPage thìkhôngclaimUIđãhiểnthị.
Không tựhạ4096proposalclipcap xuống200:200làUIrendercap,không hardcapnguồnmới nếuchưa códecision. Timeout/budgetconfig cầnjustification,noheavy3hbenchmarks/claimchưađo.
