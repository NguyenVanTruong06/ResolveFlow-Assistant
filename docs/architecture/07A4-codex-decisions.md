# G07-07A4-D — Quyết định thiết kế của Codex

2026-11-02. **DESIGN_ACCEPTED_WITH_BINDING_CORRECTIONS**.
Report SHA256 `65d98441fe89091b1ead342a9915663f418cfc81ca51ebe262357e0a2030bf96` đã đọc/đối chiếu mã.
Không chạy pytest/app/Resolve trong review thiết kế. Backend 07A3 và guard hashes giữ nguyên checkpoint.
Giao **G07-07A4-I**, HIGH, đúng ba modules đã đề xuất; không thêm vòng design-only.
Quyết định này ưu tiên những chỗ trái chiều trong thiết kế 07A4.

## C01 — Phạm vi lát cắt: Bước 3, chưa phải toàn M1

Cho phép `proposal_builder.py`, `preapproval_evidence_worker.py` mới, `review_page.py`.
Consumer thực của slice: explicit scan ở Review → off-thread proof → pure builder → proposal byte-bound →
user whole-approval → getter trả cặp proposal/record có binding đúng.
DirectorWindow/PreflightWorker/BuildPage/SourceShutdownGuard frozen.
Chưa bật `verify_source_execution=True` tại Bước 4, chưa chia sẻ cache giữa hai bước,
chưa có timeline FPS authority hay executor. Không tuyên bố toàn M1 đã khép kín.

## C02 — Q1: Cho duyệt biên tập chưa có đủ proof

**Chấp thuận** quyền editorial approval khi proof thiếu/unverified, theo behavior hiện hữu.
UI nói rõ chỉ duyệt nội dung, chưa đủ tiền kiểm dựng; ready_to_execute=False và phase 0/2 vẫn khóa.
Không thêm field `UNVERIFIED` vào status enum hiện có; đây là nhãn UI/derived state.
Valid partial proof có thể bind các clip đã xác minh; số K/N và clip chưa xác minh phải rõ.
Bad/mismatched proof không được silent skip để biến thành unbound success.

Quan trọng: `_rebuild_and_present_proposal` hiện chặn geometry khi có `_source_frame_evidence_table` mà thiếu FPS,
và `_on_proposal_approved` cũng chặn. Do vậy new preapproval proof map phải tách khỏi active geometry table.
Trong slice I **không tự ghi đè `_source_frame_evidence_table`** bằng kết quả scan.
Không bỏ các guard geometry được supplied chủ động từ trước. Có byte proof không có nghĩa đã kiểm tra geometry đích.

## C03 — Q2: FPS dự định không phải FPS timeline Resolve đã xác minh

Không chấp nhận claim CODE_VERIFIED về `ProjectConfig.target_fps`/`OutputPreset.target_fps`:
`DirectorConfig` và `_get_current_director_context` hiện không cung cấp nguồn đó.
User-selected intended timeline FPS có thể được thiết kế ở lượt sau, nhưng phải là explicit rational value,
và M2 phải đối chiếu với target thật. Không suy từ clip đầu tiên/placeholder24/preset chưa có.
Trong slice I: không thêm FPS UI/field config, không đòi FPS để thu byte/native-frame proof,
không bật deep Step4. Geometry/FPS/UI rollout còn deferred, không dùng lý do thiếu FPS để phá C02.

## C04 — Q3: Cache owner theo ReviewPage trong slice I

DirectorWindow-owned/shared Step3↔4 cache cần sửa thêm DirectorWindow/PreflightWorker, ngoài ba-file scope.
Slice I dùng service RAM do **ReviewPage sở hữu cho phiên của page**, reuse giữa các explicit scans cùng identity.
Chỉ một preapproval worker vật lý dùng service tại một thời điểm; không giả định service thread-safe.
Constructor service zero IO; mọi media/stat/probe trong worker. Clear/replace chỉ khi physical idle,
hoặc sau cancel+drain nếu source set/manifest thay. Không clear cache trong lúc worker đang dùng.
Hide/đóng rồi mở lại cùng cached instance không tự scan, có thể giữ cache tới khi explicit action kế tiếp;
actual destruction/session reset mới giải phóng. Không vừa quy định clear-on-close vừa hứa reopen hit.
Persistent cache và forwarding Step4 deferred; cache hit vẫn stat, không claim zero disk IO/100% hit/0 thời gian.

## C05 — Không dùng snippet enrichment lỏng trong thiết kế

Builder tiếp tục pure, không import Qt/worker/operational SourceEvidenceService hoặc gọi IO.
Thêm keyword-only `source_evidence_by_clip=None`; None hoặc empty mapping bảo toàn kết quả legacy.
Boundary mới là **plain normalized proof snapshots**, không runtime object/Resolve proxy.
Đặt helper pure dùng chung trong `proposal_builder.py` để worker/UI/builder dùng cùng policy.
Không tạo thêm production module chỉ chứa DTO.
UI chỉ nhận proof map từ worker/service do app sở hữu; không nạp expected SHA/proof từ AI JSON,
user recipe, prompt metadata hoặc sample content_hash của manifest để lách producer đo đạc.

Một entry đủ các semantic fields: manifest_id, clip_id, file_path, stream_index, observed_full_sha256,
freshness năm int theo SourceFreshnessStat, frame evidence (hai IDs, rational source FPS, total_frames, evidence_status),
complete_scan bool, profile_policy. Canonical rational primitive form phải được ghi rõ trong implementation/docs.
Chỉ complete strict-True + exact profile + VERIFIED_CFR + valid five-stat/frame proof + canonical full SHA
khớp active source manifest/ID/path mới được đưa vào verified subset.
Builder nhận verified subset; extra source IDs, wrong identity, malformed supplied entry → controlled ValueError.
Missing subset entry có chủ ý → clip vẫn unbound. **Cặp SHA/stream được gắn nguyên khối**, không gắn nửa cặp.
Không tin expected SHA echo từ candidate hoặc sample content_hash; expected trong proposal lấy từ observed fact đã thẩm định.
Strict raw/typed validation, count<=4096 trước iteration, required fields, immutable owned graph, UTF-8/finite numeric bounds.
Signature metadata deterministic/hashlib in-memory được phép; cấm hash/read media trong builder.
Proof metadata view có trần canonical UTF-8 16MiB (không bao gồm media); kiểm tra bounded trước clone/serialize lớn,
không truncate facts để vừa budget. Đây là byte budget, không hạ product cap4096 xuống một clip-count tùy tiện.

## C06 — Hai signature khác nhau, không vòng lặp tự làm stale

1. Pending scan input signature: ordered source IDs/paths/manifest và current editorial input fingerprint
   (plan/brief/choices/context), **không phụ thuộc kết quả proof đang tạo**.
2. Display/approval fingerprint: fingerprint hiện hành + canonical owned proof signature.

Success signature không chứa request_id/generation/clock/progress/diagnostic text;
cùng proof facts phải deterministic và không làm mất approval vô cớ khi rescan giống hệt.
Proof khác/input khác → invalidate approval theo revision/fingerprint trước khi trình lại; không auto-approve.
Mọi chỗ tái gọi builder (display/re-approve/getter revalidation nếu có) phải dùng cùng owned proof map.
Không fallback mất pending record/binding hoặc kết quả cũ khi supplier lỗi.

## C07 — Action-time và approve không được giả làm disk watcher

Worker recheck source version trước khi trả kết quả; UI terminal validate owned result và active context rồi mới mutate.
Approve/getter tiếp tục zero disk IO; approval là consent trên byte-bound snapshot, không chứng nhận đĩa còn nguyên hiện tại.
Thay đổi nguồn phát hiện qua UI/context → invalidate proof/approval; thay đổi vật lý không hiện trong UI
phải bị off-thread action-time preflight ở Step4 bắt sau này, không sửa expected SHA để khớp đĩa.
Không claim phát hiện tức thì mọi disk mutation khi bấm Approve mà không có IO/watcher.

## C08 — Physical lifecycle qua protocol thật, không method phantom

ReviewPage hiện có `shutdown_analysis_workers`, `is_analysis_idle`, `is_analysis_loading`, `analysis_workers_idle`.
DirectorWindow `shutdown_analysis_and_preflight_workers` gọi đúng các method này;
SourceShutdownGuard đi qua DirectorWindow. Thêm registry mới vào các query/cancel/idle-emission paths đó,
không chỉ tạo `shutdown_source_evidence_workers` rồi coi guard tự gọi được.
Giữ registry tới native finished; terminal chỉ xử lý dữ liệu, không giải phóng owner sớm.
Cancelled/error/stale/start failure/query exception: exactly-one terminal, physical retention đúng,
không QThread.terminate/nested event loop/disconnect-all, không auto-worker khi reopen.

## C09 — Hành động explicit, thông báo thật

Thêm nút/status trực tiếp trong ReviewPage, không sửa CommonProposalReviewWidget ngoài scope.
Không auto-scan khi nhập JSON/chọn card/đổi tab/reopen.
Hiển thị verified K/N, chưa xác minh/failed/timeout; HEADER_REPORTED_ONLY không tự gọi là VFR.
Không ghi “sẵn sàng dựng/tiền kiểm 100%”, vì FPS/target/geometry/catalog action-time chưa đủ.
Preserve user JSON/drafts/selection; failure/cancel không xóa last-good khác context/generation.

## C10 — Nghiệm thu hữu hạn, không runtime trong D

I phải có pure-builder, real QThread, Review approval/close/quit tests với valid/negative controls,
ít nhất một tiny real service→worker→Review→approval getter trace. Không GUI/Resolve desktop acceptance trong I.
Large-footage/NAS performance vẫn UNMEASURED; không benchmark/đổi deadline vô hạn.
Docs sửa ngay trong I các claims cache/FPS/full M1/TOCTOU vừa nêu, không thêm design-only round.
