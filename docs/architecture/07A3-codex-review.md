# G07-07A3-I — Review độc lập của Codex

Ngày 2026-11-02. Quyết định: **CHANGES_REQUIRED**, chưa nghiệm thu G07-07A3-I/M1.
Task sửa duy nhất: **G07-07A3-R1**, Worker HIGH, giữ nguyên ba module production.

## Bằng chứng hiện hành

- Report được đối chiếu SHA-256: `db800f5dafdd9a7240a268b225b5f8cf112eabaa4ce91d4287146d71cc9afe40`.
- Codex chạy lại đúng năm test files: **197 passed in 3.83s, exit 0**.
- Log: `.bridge/ux-evidence/G07-07A3-CODEX-REVIEW/pytest_run.log`.
- Probe độc lập: cùng thư mục, `probe_review.py`, `probes.json`, `probes.log`.
  **23 ca gồm hai positive controls đạt và 21 kết quả lệch yêu cầu**, được gom thành bốn nhóm dưới đây.
  Probe script exit 0 có nghĩa đã thu thập đủ quan sát; KHÔNG có nghĩa sản phẩm đạt.
- Fake ffprobe là child process thật có đầu ra kiểm soát; fixture và media đều trong TemporaryDirectory.
  Positive decoded-frame control đạt VERIFIED_CFR; positive deep consumer đạt checks_passed=True.
  Các negative cases dùng cùng fixture và chỉ đổi điều kiện đang thẩm định.
- Frozen DirectorWindow, SourceShutdownGuard và BuildPage đối chiếu khớp hash checkpoint.
  Không chỉnh production/test của Worker, không gọi Resolve/model/network, không đụng kho người dùng.

## A — P1: Frame proof và bounded pipe chưa đúng

Nguồn: `src/core/geometry/source_evidence_service.py:325–701`.

| Probe | Quan sát | Yêu cầu |
|---|---|---|
| missing_final_duration | VERIFIED_CFR | HEADER_REPORTED_ONLY |
| decoder_error_exit_zero | VERIFIED_CFR dù stderr có lỗi giải mã | Không complete/CFR |
| truncated_json_exit_zero | VERIFIED_CFR dù mất dấu đóng JSON | Không complete/CFR |
| two_frames_exceed_cap_one | VERIFIED_CFR cho 2 frames khi cap=1 | Chặn trước frame vượt cap |
| short_write_deadline_bounded | Child ghi một đoạn rồi giữ pipe 0.8s; deadline 0.05s nhưng caller chờ ~0.81s | Main loop vẫn quan sát deadline/cancel |

Root causes: `last_frame_duration` không reset ở từng frame nên mượn duration frame trước;
parser chỉ đếm dấu ngoặc mà không chứng minh JSON kết thúc hợp lệ; dữ liệu stderr không được xét ở nhánh exit 0;
cap chỉ kiểm tra từ frame thứ ba; `select` rồi `BufferedReader.read(65536)` vẫn có thể chặn đợi đủ byte/EOF.
Nhánh đọc remaining lặp parser, không có đầy đủ cùng guards. `clean_eof` được gán nhưng không là cổng chứng nhận.

Đóng cả nhóm bằng một đường parser streaming có giới hạn; complete chỉ sau EOF cấu trúc hợp lệ,
exit 0, không lỗi/overflow/reader exception, child reaped và readers joined.
Giữ nguyên decoded-frame policy, native FPS, B-frames/global selector và Formula B.

## B — P1/P2: Cancel, deadline, constructor và cache

Nguồn: `source_evidence_service.py:707–1044`.

- Hủy ngay khi post-stat trả về: terminal cancelled nhưng **cache mới có một entry**.
  Thiếu checkpoint ngay trước publish/cache; không được ghi proof mới sau cancel.
- Cache hit chạy lại header acquisition: hai request cùng file tạo **hai lần gọi header**, trái yêu cầu 0 probe trên hit.
- Constructor mặc định gọi `shutil.which` một lần, trái yêu cầu constructor 0 I/O.
- Deadline có từ đầu file nhưng full hash chỉ nhận external cancellation, không có callback bao gồm deadline;
  việc băm có thể tiếp tục quá hạn trước khi scanner được gọi. Đây là đối chiếu code, chưa benchmark media lớn.

Dời tra executable sang lần miss vận hành; lookup cache từ identity và selection đã có bằng chứng,
rebind IDs/expected SHA theo caller, không tái dùng approval-match boolean. Deadline áp dụng xuyên header/hash/scan/publish.
Không sửa G05 hash helper hoặc làm persistent cache.

## C — P1: Typed input chưa được sở hữu/thẩm định lại

Nguồn: `source_evidence_service.py:146–204,278–310,723+`; `source_catalog_readiness.py:124–145,545+`.

- `batch_request_owns_item`: request giữ nguyên instance item của caller.
- Item typed bị sửa stream_index=True vẫn được batch constructor nhận.
- Request typed bị sửa max_decoded_frames=True vẫn thực hiện hai lần stat ở public service entry.
- Deep request có stream_index=True được nhận và chạy hai metadata stats trước khi ValueError ở batch creation.
- Envelope freshness và batch result envelope hiện chỉ được giữ tham chiếu; normalizer cũng tái dùng freshness.
  Việc dùng frozen dataclass không thay thế defensive rehydration tại boundary.

Yêu cầu clone/revalidate đủ fields trước I/O, kiểm tra bounded sequence trước iteration,
giữ strict raw types/UTF-8/budgets, không coercion để che dữ liệu sai.
Chỉ deep mode mới áp dụng schema mới vào proposal; metadata default giữ tương thích.
Chuẩn hóa graph owned tại result boundary; không import Qt/Resolve vào core.

## D — P1: Deep consumer/terminal chưa ràng buộc đủ proof

Nguồn: `source_catalog_readiness.py:1487–1605`; `preflight_worker.py:299–323`.

Các ca sau đều nhận **checks_passed=True** trong probe dù phải từ chối:

1. complete_scan=False nhưng frame_evidence ghi VERIFIED_CFR.
2. Evidence file_path trỏ sang file khác.
3. Evidence stream_index=9 trong khi proposal yêu cầu stream 0.
4. freshness=None.
5. profile_policy khác profile đã chốt.
6. Producer batch status='failed' nhưng có map.
7. Source đổi sau metadata recheck, trong giai đoạn deep scan; trả envelope freshness cũ.
8. PreflightWorker nhận inspector result có proof incomplete và vẫn phát terminal checks_passed=True.

Deep success phải kiểm tra graph bijection và từng source: exact caller IDs/manifest/path/stream,
expected SHA từ owned proposal, observed match, complete profile CFR, valid five-stat evidence,
geometry proposal/revision/FPS đúng. Tái kiểm tra nguồn/catalog sau pha deep dài trước khi publish;
không tự cập nhật approval hoặc bỏ tài nguyên D02. Worker dùng cùng semantic binding validator,
không chỉ kiểm tra có key trong map. Normalization phải cách ly nested mutable aliases.

Phụ cùng nhánh lỗi: `PlanGeometryIssue` có field **issue_code**, không có issue_type.
Hiện geometry BLOCKED bị che thành thông báo AttributeError thay vì lý do nghiệp vụ.

## Phạm vi nghiệm thu giữ nguyên

Không phủ nhận các positive fixtures thực tế/197 test xanh. Chúng chưa bao phủ đầy đủ T03/T05/T07/T08/T09/T11.
ready_to_execute=False, khóa phase 0/2, không UI rollout và không Resolve executor vẫn được bảo toàn.
Không mở G08 hay thay geometry/approval schema. Chỉ sửa checklist hữu hạn A–D rồi báo lại một lần.
