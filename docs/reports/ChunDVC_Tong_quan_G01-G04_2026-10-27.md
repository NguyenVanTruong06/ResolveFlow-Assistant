# ChunDVC — Báo cáo tổng thể G01–G04

**Ngày tổng hợp:** 27/10/2026  
**Phục vụ:** Người dùng review kết quả bốn giai đoạn đầu và phạm vi còn lại.  
**Phạm vi:** G01, G02, G03, G04; G00 chỉ là bước khảo sát/hotfix tiền đề. G05 đang thực hiện, không nằm trong nghiệm thu của báo cáo này.

> **Kết luận:** Bốn giai đoạn đã được nghiệm thu trong phạm vi được giao. ChunDVC có nền dữ liệu thống nhất hơn, đường nhập pack có kiểm duyệt, đồng bộ kho Dock/Studio và nhiều thao tác tài nguyên được bảo vệ trước khi thực thi. Đây chưa phải nghiệm thu toàn bộ sản phẩm hoặc chứng minh dựng tự động hàng trăm video thành công.

## 1. Tóm tắt để review nhanh

| Giai đoạn | Mục tiêu | Kết quả được nghiệm thu | Giới hạn chính |
|---|---|---|---|
| **G01 — Hợp đồng dữ liệu** | Thống nhất dữ liệu asset, thời gian, bằng chứng kết quả và cấu hình | DTO/adapter; lượng tử hóa frame bằng phân số; mutation contract cho LUT; tương thích cấu hình Whisper | Chưa chuyển mọi producer/consumer sang toàn bộ hợp đồng mới |
| **G02 — Kho lưu trữ** | Nhận diện kho ngoài, chặn ghi khi không an toàn, tách publish/snapshot | Health/write guard; xử lý lỗi snapshot sau publish; inventory chỉ đọc có giới hạn | Chưa nghiệm thu journal, sửa orphan, sharding hoặc di chuyển/relink toàn diện |
| **G03 — Nạp tài nguyên** | Download → staging → inspection → review → commit/cancel | Giới hạn HTTP/Drive/ZIP; manifest không tin cậy; category nháp; duplicate warning; queue partial | Chưa chứng minh mọi tình huống Drive thật, mất điện/rút ổ hoặc pack cực lớn |
| **G04 — Thư viện & thao tác** | Dock/Studio dùng cùng kho; search, preview và thao tác trung thực | Đồng bộ hai chiều; search đầy đủ; guard insert/drag/preview/export; Inspector thật; media và Title cơ bản qua gateway | Full Text styling, FastPaste thành công trên OS, hardware/cross-platform và full workflow còn ở giai đoạn sau |

**Ý nghĩa với người dùng:** Quy trình “nhập pack → phân loại → duyệt trên Dock/Studio → xem trước → chèn tài nguyên được hỗ trợ” đã có các kiểm tra và bằng chứng cụ thể. Quy trình “phân tích hàng giờ nguồn → AI chọn cảnh/asset → dựng hoàn chỉnh” còn cần G05–G07 và nghiệm thu G09.

## 2. Cách đọc bằng chứng

- **Nghiệm thu code:** Có hồ sơ review và kết quả pytest tập trung đã được ghi nhận ở từng bước.
- **Desktop component:** Đã chạy widget/thành phần thật với Qt Multimedia; không tự động chứng minh toàn bộ ứng dụng hoặc thao tác OS gốc.
- **Resolve thật:** Có kết quả đọc lại timeline/node graph và ảnh viewer cho các ca cụ thể trên project thử nghiệm sở hữu.
- **Chưa xác minh:** Không được coi là PASS chỉ vì unit test hoặc trạng thái player là `PlayingState`.

**Lượt lập báo cáo này chỉ đọc hồ sơ hiện có và tạo tài liệu; không chạy lại pytest, ứng dụng hay Resolve.** Các số test phía dưới là kết quả lịch sử được ghi trong hồ sơ nghiệm thu, không phải phép đo mới trên toàn bộ workspace hiện tại. Không cộng các bộ test vì chúng giao nhau qua nhiều vòng sửa.

Nguồn chốt trạng thái là [STATUS](../../task/architecture-2026-10-26/STATUS.md), [ROADMAP](../architecture/ROADMAP.md) và biên bản đóng gate. Một số tài liệu thiết kế còn tiêu đề `DESIGN_READY`, `PROPOSED` hoặc đoạn backlog cũ; phải đọc cùng quyết định/nghiệm thu về sau, không dùng riêng nhãn đầu tài liệu để suy ra trạng thái cuối.

## 3. G01 — Chuẩn hóa hợp đồng dữ liệu

### Đã làm và tác dụng

| Nhóm | Thay đổi chính | Tác dụng |
|---|---|---|
| Asset DTO | `PersistedAssetRecord`, `UIAssetView`, validation và adapter tại `asset_dto.py` | Tách bản ghi được lưu JSON khỏi đối tượng runtime; giảm lệch trường giữa kho và UI |
| Path & metadata | Kiểm tra đường dẫn tương đối an toàn, đường dẫn linked tuyệt đối; kiểu dữ liệu, category ID, duration | Chặn bản ghi sai/traversal; bảo toàn nhãn và tag tiếng Việt |
| Serialization | Allowlist; loại runtime handle khỏi bản ghi lưu; không sửa dữ liệu đầu vào | Tránh serialize Qt/Resolve proxy hoặc lỗi deepcopy đối tượng runtime |
| Thời gian | `RationalFPS`, `Fraction`, `pts_to_frame`; tích hợp `TimelineMapper` | Làm tròn nửa frame nhất quán, tránh sai số float ảnh hưởng điểm cắt |
| Kết quả LUT | `MutationEvidence` và adapter dựa trên counters | Không báo thành công chỉ vì có message/flag; phân biệt đã áp, bỏ qua, lỗi, áp một phần, chưa đủ bằng chứng |
| Whisper settings | Primary hợp lệ → legacy hợp lệ → mặc định, theo từng key | Giữ cấu hình cũ và lựa chọn runtime ngay cả khi ghi settings lỗi |

Ví dụ lỗi đã đóng: giá trị toán học **4,5 frame** phải half-even về **4**, thay vì float lệch thành `4.500000000000001` rồi trả 5; **7,5 frame** về **8**. `InsertResult` hiện hành vẫn giữ hợp đồng 10 trường để tránh phá caller.

### Bằng chứng tiêu biểu và phần chưa làm

- Time/TimelineMapper: **37 tests PASS** theo hồ sơ chốt G01.
- Mutation contract: **67 tests PASS**; nhóm tích hợp LUT: **116 tests PASS**.
- Whisper settings compatibility: **36 tests PASS**, exit 0.
- Không đồng nghĩa đã chuyển toàn bộ codebase sang DTO mới. Các trục trạng thái tiến trình, chất lượng/coverage và availability chưa được triển khai đồng loạt chỉ từ G01.

**Tài liệu:** [Quyết định G01](../architecture/01-codex-decisions.md), [Data contracts](../architecture/01-data-contracts.md), [Time/status slices](../architecture/01B-implementation-slices.md), [Settings inventory](../architecture/01-settings-inventory.md).

## 4. G02 — Kho đa tầng, ngoại tuyến và giao dịch

### Cấu trúc và nguyên tắc

```text
ChunDVC_Library/
├── .chundvc_meta/          # Identity, registry, taxonomy, staging
├── 01_core/               # Tài nguyên cốt lõi
├── 02_cloud_downloads/
│   └── packs/             # Các pack đã publish
└── 03_local_linked/        # Khai báo nguồn liên kết, không nhân bản footage
```

StorageRoot dành cho kho tài nguyên. Cache phân tích nguồn, thumbnail, model AI và project là các vùng khác; **chọn ổ ngoài không có nghĩa mọi cache/model tự di chuyển sang ổ đó**.

### Những ranh giới đã được sửa

1. **Sức khỏe kho:** Phân biệt `online`, `read_only`, `full`, `offline`, `error`. Truy vấn trạng thái không tự tạo thư mục/ghi đĩa. Guard ghi kiểm tra identity/cấu trúc, quyền và dung lượng dự phòng; lỗi truy cập không được cho qua âm thầm.
2. **Offline:** Cho duyệt metadata/snapshot theo cơ chế hiện có; không tự tải media nặng sang ổ hệ thống để thay ổ ngoài bị mất.
3. **Publish và snapshot:** Registry trên kho ngoài là nguồn có thẩm quyền. Khi pack đã publish mà snapshot cục bộ lỗi, trả cảnh báo “snapshot chưa đồng bộ”, không báo cả giao dịch thất bại rồi nhập trùng lại.
4. **Inventory chỉ đọc:** Phát hiện staging, pack chưa đăng ký, entry không hợp lệ và xung đột. Registry hỏng không được suy thành “mọi pack chưa đăng ký”. Có lock thì báo bận, không tự xóa lock.
5. **Giới hạn tài nguyên:** Đọc metadata có trần, deadline/cancel và báo partial/error trung thực; không quét linked footage hay hash hàng trăm GB để kiểm kê kho.

### Bằng chứng và giới hạn

- Hồ sơ chốt G02 ghi **80 tests inventory/state PASS**, exit 0.
- Có fault tests cho lỗi I/O, marker đổi, snapshot lỗi và các ranh giới registry/rename.
- Inventory **không tự sửa, xóa hoặc đăng ký orphan**. Có manifest hợp lệ chưa đủ chứng minh người dùng đã duyệt pack.
- Journal/recovery mutation, phân mảnh registry và migration/relink toàn diện chưa được nghiệm thu. Không suy từ mock thành khả năng chịu mọi lần rút ổ/mất điện thật.
- Giới hạn metadata JSON hiện tại còn là ràng buộc dung lượng kho; chưa có benchmark chứng minh thư viện quy mô lớn vận hành đạt yêu cầu.

**Tài liệu:** [Storage transactions](../architecture/02-storage-transactions.md), [Registry/snapshot](../architecture/02B-registry-snapshot-design.md), [Recovery/inventory](../architecture/02C-recovery-design.md).

## 5. G03 — Download và nhập pack có kiểm duyệt

### Luồng đã chốt

```text
ZIP / HTTP / Google Drive / Starter SFX+LUT
    → staging trên StorageRoot
    → kiểm tra đường dẫn, dung lượng, nội dung và manifest
    → review phân loại, tag, category nháp, exclude, cảnh báo
    → người dùng Commit hoặc Cancel
    → publish registry + cập nhật giao diện / cảnh báo snapshot nếu cần
```

| Phần | Đã có trong phạm vi nghiệm thu |
|---|---|
| HTTP | Pack-only tối đa **10 GiB**; kiểm tra cả header lẫn stream; strict status 200; phát hiện HTML/login/quota qua prefix xuyên nhiều chunk; chặn rỗng/tải thiếu |
| Google Drive | Budget opt-in **10 GiB**, cộng dồn folder; chặn collision/path không an toàn; đóng response và quản lý file tải dở |
| ZIP | Preflight và kiểm tra trước ghi; tối đa **20 GiB giải nén**, **100.000 file**, manifest **2 MiB** |
| Starter | Sinh SFX/LUT vào staging rồi review; kiểm tra identity/ownership, không ghi trực tiếp vào `assets/` qua đường UI cũ |
| Manifest | Coi là dữ liệu không tin cậy; bounded read, schema/path/duplicate checks; metadata lạ có cảnh báo và fallback dự đoán an toàn |
| Category tùy biến | Draft trong RAM; chỉ persist khi commit; hủy không lưu category nháp |
| Pack trùng | Cảnh báo bản sao độc lập; không tự ghi đè hoặc xóa pack trước |
| Queue | Đã nhập gói 1 rồi hủy gói 2 thì báo **1/2**, dừng queue, phục hồi guard/nút |

Trần 10 GiB là chính sách **asset pack**, không tự áp chung vào source footage dài của AI Director. Các trần này giúp chặn thao tác vượt chính sách; không phải bảo đảm tuyệt đối ổ đĩa không thể đầy trong mọi tình huống cạnh tranh hoặc lỗi phần cứng.

### Nghiệm thu desktop và giới hạn

Người dùng/Worker xác nhận **4/4 ca desktop PASS** với hai ZIP thật: hủy draft không lưu; commit category thành công; cảnh báo pack trùng; queue partial 1/2 và reset guard. Hồ sơ gate ghi thêm **31 focused tests PASS**, exit 0.

Chưa nghiệm thu đầy đủ Drive auth/quota/CAPTCHA thật; mất điện/rút ổ; Starter BGM/Overlay/Meme; schema preview-role toàn diện; mọi đường tải tiềm ẩn trong code legacy. Không coi kiểm thử giới hạn bằng fixture là đã tải/giải nén thực tế đủ 10–20 GiB.

**Tài liệu:** [Biên bản G03](../architecture/03-gate-acceptance.md), [Checklist và kết quả desktop](../architecture/03-desktop-acceptance.md), [Ingestion architecture](../architecture/03-ingestion.md).

## 6. G04 — Thư viện thống nhất và thao tác được bảo vệ

### 6.1. Dock/Studio, taxonomy và search

- Dock và Studio nhận cập nhật managed library hai chiều; Studio đang ẩn vẫn nhận dữ liệu và mở lại không nhân đôi pack.
- Receiver snapshot giữ dữ liệu bundled, tránh quét lại toàn bộ; coordinator giữ latest pending/retry khi receiver lỗi, không echo vòng lặp.
- Poll dùng StorageRoot cấu hình hiện tại; chỉ advance checkpoint sau refresh thành công để không mất retry.
- Custom category được giữ qua Context Profile; subcategory đã mất được reset để tránh lưới rỗng giả.
- Proxy search lấy đầy đủ matches, không loại kết quả thứ 51 trở đi do default limit 50.
- Thanh category hỗ trợ cuộn, mũi tên và đưa nút đang chọn vào tầm nhìn ở Dock hẹp.

### 6.2. Guard và chèn media

`ActionGuard` phân biệt managed/linked/builtin/generated và quyền theo intent. File preview không tự cấp quyền chèn master. Missing/offline/staging hoặc identity sai phải bị từ chối trước dispatch; không tìm file khác trong repo để che lỗi.

| Loại thao tác | Hành vi đã nghiệm thu |
|---|---|
| SFX/BGM | Chèn bằng `recordFrame` tại Playhead; auto tìm track trống; giữ lựa chọn track cụ thể/tạo track mới; tránh double dispatch |
| Video | Duration chưa biết không tự trim 3 giây; live đã kiểm chứng 7 giây = **168 frame**, 3 giây = **72 frame**, và 1 frame ở 24 fps |
| LUT | Áp vào clip tại Playhead, không grade cả timeline; clip chồng cần xác định track rõ ràng; lỗi/skipped không báo đã thay đổi |
| Native transition | Hướng dẫn dùng Effects, copy tên, hỗ trợ định vị cut theo khả năng; không chèn demo MP4/carrier giả. **Chưa tự gắn native transition vào cut qua API** |
| Title cơ bản | Async qua InsertService/Gateway, khóa pending theo request; timeout quan sát không tự retry; kiểm tra project/timeline identity; Undo theo exact clip UID |

### 6.3. Preview, kéo thả và clipboard

- Gợi ý chỉ chọn asset, không tự phát audio. Hover vẫn là hành động preview có lifecycle riêng.
- Grid preview dừng khi filter/reset/removal/hide/close; lazy player; callback đến muộn không tự phát lại asset cũ.
- Inspector video bỏ alias fallback và LIVE/progress giả; Play action-time guard; đổi asset/hide dừng và dọn session.
- Inspector audio dùng player/output thật, progress theo playback thật; chọn thẻ không autoplay; audio arbitration với hover và theo dõi output mặc định qua router.
- Kéo thả chỉ xuất payload hợp lệ; không tạo WAV/CUBE/MP4/TXT giả; native transition drag bị chặn thay vì giả lập thành clip.
- CopyFusion chỉ cho nguồn procedural/file-backed được xác thực; đọc UTF-8 có giới hạn **5 MiB**; lỗi không chủ động thay clipboard bằng TextPlus giả và không ghi Recent như thể đã chèn.

### 6.4. Bằng chứng desktop/Resolve nổi bật

| Bằng chứng | Kết quả và phạm vi |
|---|---|
| Đồng bộ thư viện | Người dùng/Worker báo PASS Dock → Studio mở, Studio ẩn → mở lại, Studio → Dock |
| Preview component | Có video frame giải mã thật và ảnh desktop, Play/Pause/Resume/Replay, filter/hide và denied lifecycle |
| Audio component | Backend Qt Multimedia phát thật, position/duration tiến triển, router nhận OS default; **chưa chứng minh âm thanh vật lý/hotplug ngoài đời** |
| Title cuối cùng | Project thử nghiệm sở hữu; 24 fps, start **86400**, end **86472**, duration **72 frame**; viewer hiển thị “ChunDVC — Kiểm tra Title” với Liberation Sans Bold |
| Test chốt G04 | Codex ghi nhận chạy lại ba file core bridge/service text/UI text: **44 PASS**, exit 0; không phải toàn suite G04 |

Lỗi Title 71/72 frame đã được sửa và kiểm tra lại. Bằng chứng Title cuối nằm tại [metrics](../../.bridge/ux-evidence/G04-04C11-FINAL-R1/verification_metrics.json) và [ảnh Resolve viewer](../../.bridge/ux-evidence/G04-04C11-FINAL-R1/02_resolve_viewer_rendered_72frames.png).

**Tài liệu:** [Biên bản G04](../architecture/04-gate-acceptance.md), [Phạm vi đóng gate](../architecture/04-gate-closure-scope.md), [Nghiệm thu sync 04A](../architecture/04A-gate-acceptance.md), [Text insert](../architecture/04C11-text-insert-gateway-design.md).

## 7. Phần còn lại — không nằm trong tuyên bố hoàn thành

| Hạng mục | Nơi theo dõi | Điều kiện để coi hoàn thành |
|---|---|---|
| Whisper + hình ảnh + cache + Review/Prompt | **G05 đang làm** | Đóng các consumer và nghiệm thu hữu hạn; không suy rằng đã đủ cho batch 3 giờ |
| Matching offline và recipe/edit-plan | G06 | Asset/source IDs hợp lệ, ngân sách và fallback rõ, kiểm thử end-to-end |
| Full Text styling, stroke/curve/font; FastPaste positive; cài template | G07 | Chứng minh render/dispatch/OS focus đúng; Title cơ bản không thay thế các phần này |
| Relink/migration, journal/repair/sharding | G02 residual | Thiết kế và nghiệm thu riêng trước consumer cần dùng |
| Shell/focus, Resolve page, hotkey/anchor, lifecycle liên cửa sổ | G08 | Kiểm tra toàn ứng dụng trong môi trường đích |
| Physical audio/hotplug, native OS drop, Windows/Wayland | G09 | Kiểm thử thiết bị/OS thật, không suy từ offscreen |
| Hàng trăm video, 3 giờ nguồn; hiệu năng/RAM/GPU; đóng gói | G09 | Benchmark tái lập được và workflow thật với dữ liệu đại diện |

**Chỉ đạo hiện hành:** Hoàn tất và nghiệm thu G05 rồi dừng ở `G05_COMPLETE_WAIT_USER_RESUME`; G06–G09 chờ người dùng cho tiếp tục sau khi quota hồi. Không tắt máy, không ghi `ALL_DONE` cho toàn roadmap.

## 8. Đánh giá Tech Lead để người dùng quyết định

**Điểm đã cải thiện rõ:** Các thao tác thường dùng có đường gọi tập trung hơn; quyền nguồn được giữ tới lúc hành động; lỗi thiếu file/ổ không còn dễ biến thành “thành công” giả; nhập pack có bước duyệt và kết quả một phần trung thực. Bằng chứng đã đi từ unit test tới component desktop và một số thao tác Resolve thật.

**Rủi ro cần giữ trong kế hoạch:** Kho lớn và phục hồi sự cố vẫn chưa trọn vẹn; module UI lớn/đường legacy cần kiểm soát tiếp; main workflow, hệ điều hành khác và phần cứng chưa được nghiệm thu toàn diện. Số test PASS không đủ thay thế các bằng chứng đó.

**Điểm cần chỉnh trong hồ sơ:** Một số tài liệu thiết kế còn đoạn trạng thái cũ, ví dụ backlog ZIP trong `03-ingestion.md` không đồng bộ với phần giới hạn đã triển khai phía trên. Báo cáo này ưu tiên biên bản đóng gate và tracker; không tự sửa các tài liệu khác hoặc mở thêm công việc trong lượt tổng hợp.

### Checklist review đề xuất

- [ ] Phạm vi bốn giai đoạn và các giới hạn trên có phản ánh đúng điều cậu mong muốn không?
- [ ] Luồng nhập/duyệt/chèn tài nguyên hiện tại có đủ rõ để dùng thử hằng ngày không?
- [ ] Chấp nhận native transition là guidance và Title mới ở mức cơ bản, với phần nâng cao còn backlog?
- [ ] Có lỗi đang gặp thực tế nào trái với phạm vi đã nghiệm thu? Nếu có, ghi thao tác tái hiện/ảnh thay vì chạy lại mọi test.
- [ ] Giữ kế hoạch hoàn tất G05 rồi nghỉ; chưa mở rộng sang các gate sau.

## 9. Chỉ mục hồ sơ gốc

| Mục đích tra cứu | Hồ sơ |
|---|---|
| Trạng thái nhiệm vụ | [STATUS](../../task/architecture-2026-10-26/STATUS.md) |
| Lộ trình và chỉ đạo nghỉ sau G05 | [ROADMAP](../architecture/ROADMAP.md) |
| G01 contracts/compatibility | [Quyết định](../architecture/01-codex-decisions.md), [Time/status](../architecture/01B-implementation-slices.md), [Settings](../architecture/01-settings-inventory.md) |
| G02 storage/transactions/inventory | [Storage](../architecture/02-storage-transactions.md), [Snapshot](../architecture/02B-registry-snapshot-design.md), [Recovery](../architecture/02C-recovery-design.md) |
| G03 code và desktop | [Gate acceptance](../architecture/03-gate-acceptance.md), [Desktop acceptance](../architecture/03-desktop-acceptance.md) |
| G04 code/live và backlog | [Gate acceptance](../architecture/04-gate-acceptance.md), [Closure scope](../architecture/04-gate-closure-scope.md) |

*Báo cáo tổng hợp để review, không phải release certificate. Không thay đổi code sản xuất, task Worker hiện hành hoặc trạng thái nghiệm thu G05.*
