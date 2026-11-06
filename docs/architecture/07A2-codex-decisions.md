# G07-07A2 — Quyết định ràng buộc sau review thiết kế

Ngày 2026-11-02. **APPROVED_WITH_BINDING_CORRECTIONS**. 07A1 đã nghiệm thu; 07A2 chưa triển khai. Quyết định này ưu tiên hơn các đề xuất trái chiều trong 07A2-readiness-design.md. Không cần một vòng thiết kế lại riêng.

## 1. Bằng chứng thực tế và sửa nhận định

1. Lấy mẫu 100 packet/PTS, dù đầu/giữa/cuối, không chứng minh CFR cho toàn clip. Không sinh VERIFIED_CFR bằng phép lấy mẫu này. Metadata header và sample hash không phải chứng cứ đầy đủ về cadence hay nội dung nguồn.
2. StorageManager.assets(raise_on_error=True) có thể trả snapshot offline hợp lệ; strict không đồng nghĩa live. Kiểm tra state/context hiện hành riêng, rồi ActionGuard cho master thực.
3. wait(2000) không bảo đảm QThread đã kết thúc. SourceShutdownGuard trong src/services/source_shutdown.py chỉ theo dõi SourcePage registry và LibraryJob, chưa biết preflight. Không đưa worker mới vào app khi Quit chưa giữ được nó.
4. Module kiểm tra filesystem/catalog là Qt-free nhưng có IO; không ghi là pure. ActionGuard là hàm check_action_availability, không phải class API mới.

## 2. Lát cắt nhỏ được giao ngay: G07-07A2a-I

Đúng hai production modules mới: src/core/source_catalog_readiness.py và src/ui/director/workers/preflight_worker.py. Core chứa owned request/result DTO và bộ kiểm tra metadata; worker là adapter QThread. Không sửa DirectorWindow, ReviewPage, Controller hoặc SourceShutdownGuard trong lát cắt này. Không auto-start, không UI wiring.

Kết quả phân biệt job completion, checks_passed và ready_to_execute. ready_to_execute luôn False trong lát cắt này, kể cả nguồn/catalog đều khả dụng. Source content binding, frame cadence/geometry và executor vẫn chưa xác minh. Không media byte reads/hash, ffprobe, decoder, Resolve, model hay network. Không tạo policy full SHA audit-only/64KB. Nội dung nguồn G05/G06 chưa có trusted full-SHA trong proposal để đối chiếu, nên stat/sample identity không được gọi là content verified.

## 3. Hợp đồng dữ liệu và thẩm quyền

ReadinessRequest(request_id, generation, proposal, approval) owns/revalidates proposal và đủ sáu trường approval bằng các contracts hiện có. Không tạo approval mới hoặc thời gian mới. Cả nominal DTO cũng phải revalidate; sai input trả controlled ValueError trước IO. Giữ raw IDs, không trim/coerce; generation strict int không bool, >=0. Proposal revision và approval binding phải khớp.

inspect_source_and_catalog_readiness trả SourceCatalogReadinessResult owned/frozen với request_id, generation, proposal_id, proposal_revision, approval_id; status completed/cancelled/failed; checks_passed, ready_to_execute=False; source/asset observations và diagnostics. Không runtime objects/exception/proxy trong result; expose phần nội dung/cadence chưa xác minh rõ ràng. Kết quả là quan sát tại thời điểm kiểm tra, không execution token.

Nguồn lấy duy nhất source_binding.clips của proposal (clip_id/path/duration thực); thiếu path/file hoặc không regular/nonempty/readable thì kiểm tra không đạt. Không lấy ảnh/video reference làm nguồn, không đọc byte media, không scan hay copy footage. Chỉ cần metadata/fstat/access chứng minh mức kiểm tra đã thực hiện; không hứa xác minh nội dung hoặc khả dụng vĩnh viễn.

Catalog chỉ đọc khi assignments không rỗng. Zero assignments → catalog not_required, không gọi storage factory/config/registry. Có assignments → storage hiện hành ONLINE/READ_ONLY/FULL được đọc; OFFLINE/ERROR không cấp quyền từ snapshot. assets(raise_on_error=True), exact asset ID, source_kind và provenance được proposal cung cấp phải khớp; ID trùng/mất/staging/ngoại tuyến bị từ chối. Không copy metadata approval đè catalog để làm qua gate. Gọi ActionGuard intent insert với record gốc, dùng resolved_path master thực; preview/reference không thay master. Giới hạn catalog 100000 records, vượt trần fail-closed, không truncate.

D02: giữ nguyên omissions đã duyệt. Assignment mất file không tự chuyển omission; trả lỗi cần sửa proposal/duyệt lại. Primary footage thiếu luôn lỗi.

## 4. Snapshot và IO boundaries

Owned request construction không IO; default StorageManager chỉ tạo trong worker run khi cần catalog. IO providers có DI để test. Core không import Qt/worker module. Có thể tái dùng read_matching_storage_context từ asset_matching_worker tại adapter Qt, chuyển plain metadata vào core. Chặn loaded config khác disk config.

Ghi nhận/recheck stat stamps nguồn/master và live library context (root/library ID, marker, registry identity/stamps) trước kết quả hoàn tất. Lỗi đọc/chuyển root/marker/catalog thay đổi trả failed/stale, không trả thành công từ dữ liệu cũ. Không parse/đọc toàn metadata ở GUI. Helpers/path checks hiện có được tái sử dụng, không sửa StorageManager/ActionGuard. Cancel checkpoints trước/sau factory/provider, mỗi phần tử và ngay trước terminal; IO call có thể chưa trả thì không tuyên bố timeout đã dừng thread.

## 5. Vòng đời và bước tiếp theo

PreflightWorker dùng terminal = Signal(object), giữ native QThread.finished cho kết thúc vật lý; exactly one terminal từ outer lifecycle handling, kể cả dependency lỗi hoặc cancel. Request immutable; progress theo số source/assignment đã kiểm tra, không báo phần trăm render giả. Không terminate, không nested QEventLoop, không wait deadline rồi bỏ reference.

Sau backend được nghiệm thu mới giao 07A2b nối DirectorWindow và tích hợp app Quit/close retention trong phạm vi hữu hạn. Lúc đó mới có nút kiểm tra nguồn/tài nguyên; real Build, source-content/CFR evidence và execution/recovery vẫn các slice sau. G08/G09/G10 giữ queued.
