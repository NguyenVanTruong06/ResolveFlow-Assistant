# G05-05A-I — Codex code review (2026-10-27)

CHANGES_REQUIRED. Fresh focused suite: 26 passed in 4.21s, exit0. Test xanh chưa bao phủ các invariant dưới đây. Không mở rộng sang UI/Visual.

## P1 — Identity reuse/publication chưa dùng đủ evidence
compute_full_file_sha256 có provenance dev/ino/ctime nhưng supplied identity ở get_cached_scan/save_scan_result và run-local worker chỉ kiểm size+mtime. Probe tmp thực: hash A, thay bằng B cùng size và restore mtime qua os.replace -> save v3 theo identity A vẫn thành công, verified get với identity A vẫn hit. Cần helper thống nhất strict stable-file evidence gồm regular, dev/ino (khi khả dụng), size, mtime_ns, ctime_ns; dùng nhất quán read/reuse/save. Full hash cần đối chiếu cả descriptor dev/ino/ctime ở pre/post với path, không chỉ path-before/path-after. Đây không yêu cầu file-lock tuyệt đối; vẫn ghi nhận concurrent mutation ngoài thời điểm kiểm tra.

## P1 — Rehash sau analysis có thể chứng thực nhầm transcript
PipelineWorker block save (~819): nếu identity thiếu, tính hash ở cuối analysis rồi publish transcript với hash mới. Nhánh preflight mutation loại identity mà không capture identity mới trước extraction/transcription; vì vậy file có thể đổi trong analysis và kết quả cũ được gắn vào digest cuối. Hash failure/cancel cuối còn rơi vào save(... verified_identity=None), có thể ghi legacy khi đã hủy. Chính sách fix tối thiểu: source mismatch/lost identity sau preflight -> abort clip/job rõ ràng trước extraction/transcription; không tự reload/retry trong cùng pass. Cấm rehash cuối để cấp identity. Thiếu/unstable identity -> không publish bất kỳ cache nào; cancellation -> interruption trước load/transcribe/save. Nếu cần re-run, để người dùng chạy lại. Điều này phù hợp task đã cho phép fail-safe.

## P1 — Payload verified thiếu validation tối thiểu
Probe tmp: clip_dur=NaN và raw_subtitles=[123] được save rồi get verify_full=True trả hit. bool/Inf/âm cũng lọt duration number check; schema3.0 bằng3. V3 chỉ nhận schema int3 (không bool/float), duration finite positive không bool, raw_subtitles list of valid segment mappings theo shape producer thật (finite timestamps start>=0/end>start, text str; [] vẫn hợp lệ). Những optional fields worker thực sự dereference cần validate kiểu/finite hoặc bỏ cache, không tự biến malformed thành transcript rỗng. JSON allow_nan=False khi publish; đọc reject constants. Không bắt buộc invent fields producer không có.

## Test gap cần đóng trong đúng vòng này
Current cancellation test đặt is_interrupted=True trước run, không chứng minh cancel giữa stream/late save. Hash-count spy chỉ worker alias không thấy core hash nên chưa chứng minh tổng full reads. Chưa có phase2 runtime assertions. Bổ sung các ca trực tiếp production paths; không dựa inspect-source assertions.


## Review R1 — 2026-10-27
Fresh focused4files:62passed5.57s exit0. Nhận các sửa: identity đầy đủ chặn replacement khi dùng provenance nguyên bản, v3 finite/schema checks, bỏ rehash cuối analysis, cancellation publication và phase2 disk test. CHANGES_REQUIRED chỉ cho residual hữu hạn sau; không mở lại toàn thiết kế.

1. validate_file_identity hiện coi ctime/dev/ino là tùy chọn do provenance.get(...)=None thì bỏ check, mặc dù platform có các field. Probe tmp: sửa A thành B cùng size rồi restore mtime; provenance rút gọn chỉ file_size/mtime_ns vẫn trả True; save_scan_result với digest A + provenance rút gọn vẫn publish v3. Malformed identity phải fail-closed, không được hạ cấp silently. Khi platform cung cấp field thì evidence phải có đúng kiểu và match; unsupported platform cần representation explicit từ hash helper. Digest phải 64hex thật trước key lookup/save.
2. validate_v3_payload gọi math.isfinite trực tiếp trên int. clip_dur=10**400 (JSON hợp lệ, dưới limit digits mặc định) làm OverflowError rò ra validator và public verified read/save. Probe đã xác nhận. Dùng numeric predicate an toàn cho duration/timestamps/intervals, loại bool và overflow thành False; không cần refactor schema cũ.
3. Worker preflight khi hash trả(None,None) mà không cancel vẫn append clips_needing_whisper rồi tiếp tục load_model; chỉ per-clip mới missing_verified_identity abort. Cần abort ngay preflight,0model/extract/transcribe/save. Task đã yêu cầu thiếu/failed identity dừng trước xử lý nặng, không nên chờ loop.

Lát cắt R2 đúng2modules, test residual trực tiếp và cùng focused command; không UI/Visual/model thật.


## Quyết định cuối G05-05A-R2 — 2026-10-27
CODE_ACCEPTED_WITH_RECORDED_LIMITS cho lát cắt cache audio. Codex đọc helper identity/payload và preflight abort, chạy lại đúng4files:79passed6.72s exit0. Probe độc lập tmp xác nhận reduced provenance bị từ chối, stale digest không publish, bigint bị reject không throw. Các ca R1 về replacement với evidence đầy đủ, bỏ rehash cuối analysis, cancel, legacy không auto-promote tiếp tục được bao phủ bởi nhóm test.

Không có model/GPU/nguồn thật/desktop trong phiên review. Chưa chứng minh hiệu năng3giờ, mọi race filesystem hay mọi payload tùy ý; không lặp lại từ ngữ “an toàn tuyệt đối” trong report. Đọc full file có chi phí IO, stat không phải mandatory lock. Legacy v1/v2 cần một lần phân tích lại trong worker để có v3; bản cũ được giữ. Default fast UI cache vẫn là candidate; UI dual-stage/provenance phase in-memory/Visual invalidation là residual, không được coi gate G05 đã xong. Không cộng test giao nhau. Chỉ mở lại05A khi bug tái hiện/consumer mới làm lộ contract lỗi. Giao G05-05B-D tiếp theo, không shutdown.
