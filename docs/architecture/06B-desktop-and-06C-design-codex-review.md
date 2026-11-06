# Review 06B desktop và 06C design — 2026-10-29

Report: G06-06B-AUTO-UI-THEN-06C-D, SHA256 `228fb2517550e9017f7c7cebe1bc60cb07aafd4066c88c26602b0d1196ce5ea1`.

## Kết luận

P2 code vẫn accepted trong recorded limits. Đã đọc harness, normal/fault metrics và xem ảnh desktop thật. Functional component evidence được nhận trong phạm vi các assertions thực có; chưa đóng yêu cầu hiển thị đọc được của 06B. Thiết kế 06C cần chỉnh trước khi triển khai. Không chạy lại pytest, app, inference hay Resolve trong review này; 273 tests/54.48s là evidence kế thừa từ lượt code review trước.

## A — Desktop evidence

Normal metrics exit 0; fault exit 2, active-worker barrier được kiểm tra trong harness; finally release gate/cancel và chỉ xóa session khi worker idle. Không có yêu cầu chạy lại toàn bộ sáu ca.

Ảnh `case1_happy_path_SCREEN_CAPTURE.png` cho thấy production DirectorWindow đang mở, nhưng bảng gợi ý bị ép chiều cao. `case1_happy_path_DRAWER_RENDER.png` chỉ 532x45 px: không đọc được hai dòng candidate/score. Đây là khoảng trống visibility có bằng chứng, không thể nhận claim desktop UX đầy đủ chỉ từ model.rowCount và chuỗi model.data.

Chỉ yêu cầu một targeted run chụp happy + error có bảng đọc được: resize/maximize cửa sổ production, dùng cuộn UI thật nếu có. Không chỉnh setFixedHeight/CSS/hide widget trong harness để giả layout đã sửa. Nếu vẫn bị ép ở vùng desktop khả dụng, báo rõ production layout blocker, không sửa production trong task này.

Hiệu đính limits: fixture MP4/WAV là file header giả phục vụ metadata, không chứng minh playback; manifest GUI là fixture SimpleNamespace với clips rỗng, không source validation. Case5 đo assets() bằng 0, không đo factory/context-reader bằng 0. GUI/worker IO thread tracing chưa đo. Clipboard untouched là audit zero-write trong harness, không phải hook giám sát toàn ứng dụng. Các giới hạn này được ghi nhận, không mở thêm task đo lường.

## B — Corrections bắt buộc cho thiết kế 06C

1. **Callgraph và authority:** Có tên/line sai: ReviewPage ở 382, ClipManifest ở 93, AssetDemandBatch ở 332; TimelineMapper có add_segment, không add_cut; ClipEntry.current_path, không ClipItem. Đối chiếu SourceResolver.resolve_plan với auto_resolve_missing_assets riêng, không gom hai hành vi. Matching plan_revision chỉ hash material nhu cầu, không full timeline/Slot2/brief và không authorization toàn plan. Phải có thiết kế binding handoff toàn phần độc lập, không thay matcher đã accepted.
2. **Mixed FPS:** Công thức source_duration_frames/speed trực tiếp thành timeline frames sai khi source FPS khác timeline FPS. TimelineMapper hiện quy đổi bằng FPS riêng, có padding/clamp/min-one-frame/speed policy; không mô tả nó như đã có source-native frame mapping nghiêm ngặt. Nêu rõ semantics giây và hai domain, công thức rational, điểm lượng tử hóa; nguồn FPS 30, timeline 24, cut 10–20s ở 1x phải là source [300,600), timeline [0,240). Thiếu metadata thì unresolved/blocked, không bịa total frames/FPS hoặc sửa mapper trong design. Bỏ speed bounds mâu thuẫn chưa có quyết định. Fixture cũ timeline_sec=12 nhưng frame=60 tại 30000/1001 không hợp lệ.
3. **Pure validation không cấp quyền execution:** Không stat/isfile, codec, Resolve hay chứng minh current availability trong pure layer. Metadata valid khác G07 preflight ready. Thiếu manifest không vừa fail-closed vừa cho phép thực thi rough-cut; giữ legacy payload cho đọc/migration, chặn cấp quyền execution khi chưa liên kết có bằng chứng. Offline/missing/unassigned không tự omit hay tự dựng phần còn lại; kế hoạch bỏ qua phải explicit, được duyệt và trả lại phạm vi thay đổi.
4. **Advisory không phải assignment:** Sidecar/candidate không cung cấp file path có thẩm quyền hay bằng chứng user chọn. Candidate origin/availability là snapshot, không đủ để Resolve import. Không giả lập asset_id->library_path hoặc dựng source path từ JSON AI; app-owned catalog resolution và action-time guard/preflight ở owner phù hợp. Xóa khuyến nghị auto-accept điểm>=90 chưa được user duyệt. Native transition chỉ guidance thao tác thủ công, không 'Resolve sẽ áp dụng' qua API. Source kinds hiện có managed/linked/builtin/generated; nêu supported subset nếu chỉ nhận hai loại.
5. **Hierarchy/schema:** Batch draft và macro overview vẫn không executable, không tự combine/compile thành final plan. Giữ Slot1/Slot2 guards, legacy JSON nguyên vẹn; version mới là PROPOSED chứ chưa mặc định v3.0 được phê duyệt. Five fixtures phải tự nhất quán, IDs/hash đúng kiểu, ranges/FPS/insert times khớp, phân biệt illustration với accepted input. Chỉ đề xuất một first slice pure hữu hạn, không tự triển khai hoặc chuyển sang G07.

## C — Product checkpoint theo chỉ đạo user mới

User xác nhận tiếp tục công việc hiện tại, nhưng muốn cùng Codex chốt trước phần định hình AI: brief khách hàng/cá nhân, cute/kịch tính/hài hước, footage riêng vs stock/meme dùng chung, tham khảo URL/video và lựa chọn tài nguyên/fallback.

Chuẩn bị một agenda ngắn kèm ví dụ review điện thoại cute/không meme. Phân rõ đã có, proposed, cần user chọn và deferred. Không tự mở crawling kênh, auto-download, cloud vision, auto-pick hay generation; không đổi cấu hình prompt/JSON sản phẩm trước cuộc trao đổi. Khi report revision tới, Codex review rồi cùng user chốt định hướng trước khi cấp implementation liên quan. Không shutdown.
