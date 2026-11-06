# G07-07B5-D-R18 — Codex Static Design Review

**Ngày:** 2026-11-02  
**Quyết định:** `CHANGES_REQUIRED`  
**Phạm vi:** Static review thiết kế và đối chiếu hợp đồng nguồn. Không chạy pytest, ứng dụng, Resolve, IPC, FusionScript, AI hoặc network.

## Kiểm tra tính toàn vẹn hồ sơ

Các hash do worker báo cáo khớp với tệp hiện có trên workspace:

- Review R17: `docs/architecture/07B5-D-R17-code-review.md` — `21424a946f145c80107756d5b51f1df358b22ee9008782d7e96e64b7d7b3f45a`
- Task R18: `.bridge/task.md` — `91a20fcd6cca155e1f53d5c74ec6b95f811487d5657716a73d4ab2b0ea0fb86e`
- Thiết kế: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` — `a0767546c95438636ce8a5e146fb18ceb3d7de1aa7e5d56124192e11d132ca38`
- Báo cáo: `.bridge/report.md` — `86893d4fff771f2c6da492de977abacb6bf8b644f06288d3fce87b5ee90ddbb2`

R18 đã sửa đúng hướng các vấn đề R17: UTC regex hiện chỉ nhận `Z`/`+00:00`; ledger tiêu thụ là dependency bắt buộc; request/result có nonce và binding danh tính; pseudocode có các crash case. Tuy nhiên, các điểm dưới đây làm suy yếu ranh giới provenance và khiến thứ tự crash-safe được mô tả không khớp với validator.

## Findings chặn nghiệm thu

### R18-01 — Caller có thể tự chọn khóa HMAC và đưa kết quả live vào WAL validator

Ở Section 2.6.12, chữ ký của `validate_wal_to_eof()` nhận trực tiếp `live_observation_request`, `live_resolve_observation`, `gateway_session_secret` và `gateway_session_id` dưới dạng tham số tùy chọn (khoảng dòng 1860–1877). Nhánh phục hồi sau đó gọi verifier bằng chính `gateway_session_secret` và `gateway_session_id` được truyền vào (khoảng dòng 2133–2155, 2201–2221). `verify_live_resolve_observation()` chỉ chứng minh rằng kết quả khớp HMAC theo secret được cung cấp; nó không chứng minh secret đến từ phiên ResolveGateway được sở hữu bởi recovery coordinator.

Vì vậy caller có thể tạo request/result giả, chọn secret của mình, ký result bằng `compute_gateway_observation_hmac()`, rồi truyền cả secret và kết quả tương ứng vào validator. Nonce không khắc phục được việc caller đồng thời kiểm soát request, result và key kiểm tra. Điều này trái với mục tiêu R17-01/R18 là không nhận bằng chứng do caller tự tạo.

**Yêu cầu:** Giữ khóa phiên trong ResolveGateway/coordinator sở hữu; không nhận secret hoặc `expected_gateway_session_id` từ caller của WAL validator. Coordinator phải tự phát request tới gateway và nhận kết quả qua đường sở hữu nội bộ có capability/session binding. Validator chỉ được nhận một bằng chứng opaque đã xác minh bởi authority tin cậy hoặc gọi coordinator-owned verifier không cho caller thay thế trust anchor. Thêm negative fixture chứng minh caller không thể cấp secret, gateway ID và DTO tùy ý để đạt trạng thái reconciled.

### R18-02 — Pseudocode tiêu thụ proof sau khi WAL đã terminalize, ngược thứ tự bắt buộc

R18 §2.6.4 quy định thứ tự `verify proof → fsync consumed ledger → append terminal WAL/Journal → release lock`. Nhưng `validate_wal_to_eof()` chỉ vào nhánh xác minh/tiêu thụ ở cuối sau khi đã đọc thấy `ROLLBACK_DONE`/`FINISH` hoặc `ACTION_FAILED`/`FINISH` (khoảng dòng 2095–2159 và 2166–2224). Bộ trạng thái còn yêu cầu các stage terminal đó trước khi chọn nhánh bằng chứng. Kết quả là pseudocode tiêu thụ proof sau terminal WAL, ngược với giao thức và crash matrix đang tuyên bố.

**Yêu cầu:** Tách đọc/parse WAL thuần túy khỏi giao dịch hoàn tất. Định nghĩa rõ trạng thái WAL tiền terminal tại thời điểm proof được xác minh; thực hiện `verify → atomic durable consume → append/fsync outcome WAL → update/fsync receipt/journal → release lock`. Khi restart, phân biệt từng ranh giới crash và chứng minh proof cũ không được phát lại. Không được để một validator vừa yêu cầu WAL terminal tồn tại vừa tuyên bố sẽ ghi terminal sau consumption.

### R18-03 — Durable ledger chưa có thao tác claim nguyên tử chống replay/race

Giao diện `DurableConsumedReceiptLedger` chỉ có `is_consumed()` và `record_consumed()` (§2.6.3, khoảng dòng 974–977). Các call site thực hiện kiểm tra rồi ghi riêng (ví dụ dòng 2136–2153). Giao diện không định nghĩa compare-and-consume nguyên tử, uniqueness constraint, khóa liên tiến trình, tính idempotent của lần retry, hay chính sách khi `record_consumed()` ghi một phần/fsync lỗi. `TargetLock` giảm race cho cùng target, nhưng không phải hợp đồng về uniqueness toàn ledger cho receipt ID và nonce; lỗi persistence cũng phải bảo đảm coordinator không kết luận reconciled hoặc nhả lock.

**Yêu cầu:** Thay hai thao tác thành một primitive nguyên tử, ví dụ `consume_once(receipt_id, nonce, execution_id, action_index, ...)`, với khóa bền vững/transaction, unique constraint cho cả receipt ID lẫn nonce, ghi nguyên tử và fsync; quy định trạng thái sau crash ở trước/trong/sau commit. Mọi lỗi hoặc kết quả không xác định phải giữ target lock và trả IN_DOUBT. Bổ sung concurrent duplicate, partial write, fsync failure và restart probes.

### R18-04 — Strict numeric gate vẫn có thể ném `OverflowError`; live result chưa strict-validate scalar types

`_strict_wal_number()` gọi `math.isnan(val)`, `math.isinf(val)` rồi `float(val)` cho mọi `int|float` (§2.6.12, khoảng dòng 1271–1280). Một bigint như `10**400` có thể phát sinh `OverflowError` trong phép chuyển đổi/kiểm tra thay vì lỗi kiểm soát fail-closed. Ngoài ra verifier live dùng so sánh trực tiếp thay vì `_strict_wal_int()`/`_strict_wal_bool()` cho `action_index`, `track_index`, frame bounds, `total_items_scanned`; `True == 1` và `1.0 == 1`, còn `False < 0` là false, nên các giá trị sai kiểu có thể lọt. `validate_wal_to_eof()` cũng nhận `current_time_epoch` từ caller và chuyển xuống verifier, trong khi verifier cho phép caller override `max_observation_ttl_seconds`; freshness policy vì vậy chưa có nguồn thời gian/ngưỡng độc lập do coordinator sở hữu.

**Yêu cầu:** Kiểm tra bigint theo giới hạn trước khi đổi sang float, bắt `OverflowError` thành lỗi xác thực có kiểm soát, và dùng primitive gates cho toàn bộ trường số/bool trong request/result trước equality, HMAC hoặc arithmetic. Thời gian hiện tại và TTL phải do policy/coordinator sở hữu, không nhận ngưỡng nới lỏng từ caller. Kiểm tra `gateway_session_secret` là bytes không rỗng và các chuỗi/digest/nonce có schema và giới hạn chính xác. Thêm probes cho `10**400`, bool, float integral, số âm và giá trị ngoài giới hạn.

### R18-05 — Dấu thời gian monotonic được ký nhưng không dùng để quyết định freshness

`LiveResolveObservationResult` mang `observation_monotonic_time`, nhưng `verify_live_resolve_observation()` chỉ tính tuổi từ timestamp UTC/wall clock (§2.6.12, khoảng dòng 1846–1857). Hàm không nhận thời gian monotonic hiện tại, không kiểm tra monotonic time là finite/đúng kiểu, và không ràng buộc freshness với `request_timestamp_utc`. Vì vậy R18 chưa thực hiện được tuyên bố provenance/freshness dựa trên đồng hồ monotonic; clock wall có thể nhảy hoặc lệch.

**Yêu cầu:** Định nghĩa một đồng hồ freshness có authority rõ ràng. Ưu tiên cặp monotonic do cùng gateway session sở hữu (request dispatch/start và observation completion), validate finite/type/ordering và TTL; dùng UTC để audit, không làm đồng hồ freshness duy nhất. Nếu không thể đối chiếu monotonic qua process boundary, bỏ field hoặc mô tả giới hạn và áp dụng giao thức khác có challenge-response gắn với thời gian coordinator. Thêm ca clock rollback/forward, observation trước request và hết TTL.

## Findings bổ sung cần đóng

### R18-06 — WAL parser âm thầm bỏ qua dòng rỗng

Parser tạo `non_empty_lines = [l for l in lines if l]` (khoảng dòng 1981–1986) thay vì từ chối dòng rỗng ở giữa tệp. Điều này không khớp yêu cầu framing/grammar nghiêm ngặt và có thể che dấu hỏng dữ liệu trong nhật ký. `seq` được so với index sau khi đã bỏ dòng, nên dòng rỗng không được phản ánh vào thứ tự đã kiểm tra.

**Yêu cầu:** Chỉ bỏ delimiter cuối cùng do newline kết thúc tệp; từ chối mọi record line rỗng, nhiều newline cuối, hoặc dòng whitespace. Bổ sung fixture cho dòng rỗng đầu/giữa/cuối.

### R18-07 — Quét track không có giới hạn và `total_items_scanned` không chứng minh quét đầy đủ

Gateway được yêu cầu liệt kê toàn bộ `GetItemListInTrack("video", 1)`, nhưng không có cap số item/thời gian hoặc quy tắc fail-closed khi vượt cap (§2.6.6). Verifier chỉ kiểm tra `total_items_scanned < 0` (khoảng dòng 1808–1809), không yêu cầu kiểu int thuần và không có dữ liệu độc lập để chứng minh count tương ứng với danh sách đã duyệt. Chỉ ký một count không chứng minh worker đã quét trọn track.

**Yêu cầu:** Đặt giới hạn hữu hạn cho số item/thời gian; vượt giới hạn phải trả observation incomplete và giữ IN_DOUBT, tuyệt đối không dùng kết quả scan một phần. Gateway phải tự tạo count từ số item thực đã enumerate, kết quả phải được HMAC bao phủ, verifier strict-check kiểu/range; nêu rõ trust boundary của worker và cách xử lý danh sách malformed/None/duplicate item.

## Những điểm R18 đã sửa đúng

- `RE_CANONICAL_UTC_TIMESTAMP` giới hạn biểu diễn vào `Z` hoặc `+00:00`, và `_strict_wal_utc_iso()` từ chối offset khác 0.
- `DurableConsumedReceiptLedger` không còn optional/default-empty.
- Request/result bao gồm request ID, challenge nonce và binding danh tính thao tác.
- Bảng fixtures có các trường hợp forged DTO, replay, shifted target, timestamp offset và crash quanh consumption.
- Ranh giới `ready_to_execute=False`, mock-only, không live implementation và G07/M1/M2/M3 OPEN được giữ nguyên trong hồ sơ.

## Quyết định và bước tiếp theo

R18 **chưa được nghiệm thu**. Giữ `ready_to_execute=False`; không bắt đầu G07-07B6-I hoặc bất kỳ live Resolve work nào. Workorder tiếp theo nên là **G07-07B5-D-R19 — Coordinator-Owned Observation Capability, Atomic Proof Consumption & WAL/Terminal Ordering**, DESIGN ONLY. R19 cần xử lý R18-01..R18-07, bổ sung bảng trạng thái receipt/ledger/WAL/lock cho từng crash boundary, và giữ nguyên mọi ràng buộc R14–R18.

Không chạy test/runtime trong review này; đây là đánh giá tĩnh dựa trên tài liệu và pseudocode. Review không cấp quyền gọi Resolve thực.
