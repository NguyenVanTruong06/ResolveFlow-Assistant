# G07-07A3 — Implementation decisions sau design R1

2026-11-02. DESIGN_ACCEPTED_WITH_BINDING_CORRECTIONS. Giao07A3-I, không thêm vòng design-only. Quyết định này ưu tiên các chỗ trái chiều còn sót trong07A3-execution-readiness-design.md và kế thừa07A3-codex-decisions.md.

## 1. Bằng chứng chốt giao thức
Codex tiny fixture B-frame MP4: audio global0,video global1,30decodedframes. PacketPTS [0,1536,512,1024,...] khôngmonotone; decoded framePTS [0,512,1024,...] đều. Selector v:1 trả0frames trongfilechỉ1video,selector global1 trả30. Video1s trongcontainer cóaudio2s,formatduration2s khônglàvideoEOF.
Evidence .bridge/ux-evidence/G07-07A3-D-R1-CODEX-REVIEW/.
Nguồnprimary: [FFprobe documentation](https://ffmpeg.org/ffprobe.html#Main-options) phân biệt show_packets/show_frames; [stream specifiers](https://ffmpeg.org/ffprobe.html#Stream-specifiers) phân biệt globalindex và index within video streams. Đây không phải proofCFR tự thân; policy phải kiểm tra dữliệu decoded frame đầyđủ.

## 2. Verification profile đầu: decoded_frame_uniform_ticks_v1
Dùng toàn selected decoded video frames, không packet FIFO. Stream index là globalindex đãđọc từheader; defaultNone đểacquirer chọnvideo,không default0 luôn. B-frame video hợp lệ không bị gọiVFR chỉvìpacket order. Không lấy total_packets làtotal_frames.
Profile đầu chỉ chứngnhận khi cleanEOF/process exit0/khôngdecodeerror,>=2frames,allframe presentationPTS integer cóđủ,strictlyincreasing và constantpositive delta ticks;time_base hợp lệ;lastframe duration chứngminh được cùngnhịp;reported_nb_frames nếu cóphảikhớp decodedcount. FPS suy từdelta*time_base bằngFraction;không dùng expected timelineFPS làm sourceFPS.
Nonuniform/missingtimestamp/ambiguous/coarsequantization/insufficientduration không tự là VERIFIED_VFR: giữHEADER_REPORTED_ONLY+diagnostic,khôngCFR. Firstslice không cần phátVERIFIED_VFR nếuchưachứngminhphânloạiđó. Timeout/overflow/cancel/decodererror/partialEOF khôngpass. Không so lastvideoPTS với toànformatduration hoặc audioEOF.
Đây là profile hẹp; khôngclaim hỗtrợmọiformat/timebase. Khôngđổi FormulaB hoặc VFRbypass.

## 3. Trust và scope
SourceExecutionEvidenceEnvelope táchobserved digest khỏi expected digest. Expected digest chỉ lấy từ exact source_binding.clips[clip_id].expected_full_sha256 trong owned proposal; không từ kếtquảproducer/cache đểtựlàmbinding. Builder hiệnCHƯA đưa fieldnày vào: sửa claim CODE_VERIFIED trái chiều; pre-approval producer→builder→review handoff còn phầnM1tiếp theo. Missingexpected thìUNBOUND/chưaexecutionverified,khôngmutateapprovedproposal.
FullSHA dùngG05helper,no samplefallback. SourceFreshnessStat đối chiếu5fields xuyên header/hash/frames/postcheck,khôngghépdata từhai versionfile.
Cache slice đầu RAM per SourceEvidenceService instance/run, filelevel completeproof keyed path+5stat+globalstream+policy. Rebind manifest/clip/expectedSHA mỗi request;khôngcache bool approvalmatch. ReusestillstatIO,0fullmediaread/0probe ởhit. Persistent/crossrestart cache deferred,khôngclaimđãxong hoặcđụng Whispercache.
ready_to_execute luônFalse: target/executor/recovery chưa có. Metadata UI/default workflow giữ nguyên, không tự deep scan mọi click.

## 4. Consumer backward compatibility
ReadinessRequest thêm keyword-only verify_source_execution=False,timeline_fps=None; metadata default giữ4positionalargs cũ và0deepIO. True yêu cầuRationalFPS hợp lệ. Worker/publicentry sởhữu/copy đủnewfields,binding khôngdrop.
SourceCatalogReadinessResult additive optional fields cuối source_execution_evidence=None và source_geometry=None. Normalize/deepclone/terminal phảigiữ cảhai; schemas geometry cũ khôngđổi.
Explicit deepmode gọiservice vàassemble_plan_frame_geometry;checks_passed chỉTrue khi metadata đạt,expectedhash match từngsource,frameproofCFR đủ,geometry đạt;ready vẫnFalse. Missingbinding/cadence/geometryfailed khôngsuccess. UIkhôngthayđổi vàcurrentDirector requestsdefaultmetadata;deepAPIoperational quaQThreadtests,UIpreapprovalhandoff chưaclaim.
Không sửa SourceFrameEvidence5fields hoặc AppOwnedApprovalRecord6fields.

## 5. Small bounded implementation
Đúng3files service mới+source_catalog_readiness+preflight_worker. Producer thật(hash/header/fullframe bounded scan),typedownedartifacts và opt-in consumer. Không chỉpureDTO,nhưng cũng khôngclaimtoànM1done khiUI/builder chưahandoff. Sửa docs sai trongcùngI,khôngdesignroundmới.
