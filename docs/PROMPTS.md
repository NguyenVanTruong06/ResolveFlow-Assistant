# ChunDVC — Prompt triển khai giao diện mới theo từng bước

Cách dùng:

1. Chép 3 file vào repo ChunDVC:
   - `docs/UX_REDESIGN.md` (tài liệu thiết kế, file `UX_REDESIGN.md` Claude gửi kèm)
   - `docs/ui_mockup.html` (mockup chạy được, mở bằng trình duyệt)
   - `docs/PROMPTS.md` (chính file này)
2. Mỗi lần chỉ dán **một** bước cho Antigravity, theo đúng thứ tự 0 → 7.
3. Antigravity sẽ **đề xuất trước** (file `docs/plans/buoc-N.md`), chưa sửa code. Đọc đề xuất, gửi cho Claude check nếu cần.
4. Đồng ý thì gửi một trong các câu sau:
   - `OK, làm theo kế hoạch.`
   - `OK, làm theo kế hoạch, nhưng: <điều chỉnh>.`
   - `Chưa đồng ý mục <số>. Đề xuất lại mục đó theo hướng: <ý của bạn>.`
5. Làm xong, Antigravity gửi báo cáo. Gửi báo cáo cho Claude check rồi mới sang bước sau.

Bước 0 chỉ đọc, không sửa code. Nó dùng để Antigravity đối chiếu thiết kế với code thật, phát hiện chỗ Claude hiểu sai dự án trước khi động vào bất cứ thứ gì.

---

## QUY TẮC CHUNG (áp dụng cho mọi bước)

### ĐỀ XUẤT TRƯỚC, LÀM SAU (bắt buộc, trừ Bước 0 vốn chỉ đọc)

Mỗi bước chia làm 2 lượt. **Lượt 1 không được sửa bất kỳ file code nào.**

**Lượt 1: Đề xuất.** Đọc tài liệu + code liên quan, rồi viết `docs/plans/buoc-N.md` gồm:

1. **Hiểu yêu cầu**: tóm tắt bước này trong 3–5 dòng bằng lời của bạn.
2. **Hiện trạng code**: các file/hàm sẽ bị ảnh hưởng (file:dòng), cách chúng đang chạy.
3. **Phương án đề xuất**: cách làm cụ thể, danh sách file tạo/sửa, thứ tự làm.
4. **Phản biện thiết kế**: chỗ nào trong UX_REDESIGN.md hoặc mockup bạn thấy sai, thừa, khó làm trong PySide6, hoặc có cách tốt hơn. Ghi rõ lý do. Được phép và được khuyến khích không đồng ý với thiết kế.
5. **Phương án thay thế**: với mỗi điểm phản biện, đưa 1–2 cách khác kèm ưu/nhược.
6. **Rủi ro**: điều gì có thể làm hỏng tính năng đang chạy, cách tránh.
7. **Ước lượng**: số file, độ khó, phần nào cần thử trên DaVinci thật.
8. **Câu hỏi cho chủ dự án**: chỉ những câu thật sự cần trả lời mới làm tiếp được.

Rồi **DỪNG LẠI**, trả lời trong chat bằng bản tóm tắt ngắn của file kế hoạch và chờ.

**Lượt 2: Làm.** Chỉ bắt đầu khi chủ dự án gửi "OK, làm theo kế hoạch" (có thể kèm chỉnh sửa). Làm đúng kế hoạch đã duyệt; gặp chỗ phải lệch khỏi kế hoạch thì dừng và hỏi, không tự quyết.

### Quy tắc kỹ thuật

- Mockup `docs/ui_mockup.html` là **tham chiếu hành vi và kích thước**, không phải code để chép. Code thật viết bằng PySide6. Khi mockup và `docs/UX_REDESIGN.md` khác nhau, theo `UX_REDESIGN.md`.
- Bấm nút "Đặc tả kỹ thuật" trong mockup để xem các con số (px, ms, quy tắc).
- Nếu repo có git: trước khi sửa, tạo branch `ui-redesign/buoc-N`. Cuối bước, commit một lần với message `ui-redesign: buoc N - <tên bước>`.
- Không xóa tính năng đang chạy trong bước 1–6. Code mới chạy song song với code cũ; chỉ bước 7 mới xóa.
- `src/services/` không được import `QtWidgets`.
- Mọi lời gọi DaVinci Resolve API đi qua `ResolveGateway` (từ bước 1 trở đi). Không gọi API Resolve trực tiếp trong widget.
- Không dùng `subprocess.run` chặn luồng UI. Dùng `QProcess` hoặc chạy trong thread.
- Mỗi bước phải có test chạy được bằng `pytest`. Phần cần Resolve thật thì dùng mock.
- Gặp chỗ thiết kế không khớp với code thật: **dừng lại và ghi vào báo cáo**, không tự đoán.
- Cuối bước, báo cáo theo mẫu:

```
BÁO CÁO BƯỚC N
1. File đã tạo / sửa (đường dẫn + 1 dòng mô tả)
2. Kết quả pytest (số test pass / fail)
3. Cách chạy thử bằng tay (lệnh + thao tác)
4. Kết quả các mục NGHIỆM THU (đạt / chưa đạt + lý do)
5. Chỗ làm khác thiết kế và lý do
6. Rủi ro hoặc câu hỏi cần chủ dự án quyết
```

---

## BƯỚC 0 — Đối chiếu thiết kế với code (chỉ đọc)

```
Làm theo QUY TẮC CHUNG trong docs/PROMPTS.md. Đây là BƯỚC 0: CHỈ ĐỌC, KHÔNG SỬA FILE NÀO.

Đọc docs/UX_REDESIGN.md và mở docs/ui_mockup.html (đọc cả phần <script>).
Thiết kế này được viết dựa trên một bản tóm tắt code, chưa đọc code thật. Nhiệm vụ của bạn là đối chiếu với code thật và tìm chỗ sai.

Kiểm tra và trả lời từng mục:
1. Kiến trúc: main.py có thật khởi tạo FloatingBubble, AutoWindow, StudioWindow như thiết kế mô tả không? app.py còn được dùng ở đâu (import, test)?
2. Chèn tài nguyên: liệt kê mọi đường code đang chèn vào DaVinci (title worker, fast_paste, insert_sfx_to_track, import FCPXML, apply LUT, render...). Mỗi đường: file:dòng, chạy trên thread nào, có chặn UI không. Có đường nào thiết kế bỏ sót không?
3. Thuật toán chèn track trống (NEW-NESTED-NO-RIPPLE-V2): xác nhận nó trả về được track + timecode đã chèn không. InsertService.undo() trong thiết kế cần thông tin này. Nếu không trả về được, đề xuất cách lấy.
4. Resolve API có cho đọc timecode playhead và GetCurrentPage() nhanh không? Thử đo thời gian 1 lần gọi nếu có Resolve đang chạy; nếu không, ghi "chưa đo".
5. Có xóa được một timeline item đã chèn qua API không (cần cho Hoàn tác)? Ghi rõ hàm API.
6. Tìm kiếm: so bảng alias trong mockup (biến ALIAS) với BILINGUAL_TAG_MAP thật; liệt kê chỗ khác.
7. Danh mục tài nguyên thật (đếm theo AssetIndexer) so với các danh mục trong thiết kế: thiếu / thừa loại nào?
8. Preview: tài nguyên nào đã có file video preview, tài nguyên nào chỉ có thumbnail tĩnh, tài nguyên .setting nào chưa có gì.
9. Cửa sổ: thử cờ Qt.WindowDoesNotAcceptFocus trên EdgeDrawerWindow hiện tại (đọc code, không sửa) — có chỗ nào trong code đang cần drawer nhận focus (ô nhập, combobox)?
10. Test hiện có: test nào phụ thuộc vào widget ẩn trong tab_assets.py hoặc app.py? Liệt kê.
11. Bất kỳ chỗ nào khác bạn thấy thiết kế hiểu sai code.

Ghi kết quả vào docs/BUOC0_DOI_CHIEU.md theo đúng 11 mục trên, mỗi mục kèm file:dòng.
Cuối file thêm mục "ĐỀ XUẤT SỬA THIẾT KẾ": danh sách thay đổi cụ thể cho UX_REDESIGN.md.
```

---

## BƯỚC 1 — Dịch vụ lõi

```
Làm theo QUY TẮC CHUNG trong docs/PROMPTS.md. Đây là BƯỚC 1: Dịch vụ lõi.
BẮT ĐẦU BẰNG LƯỢT 1 (ĐỀ XUẤT): viết docs/plans/buoc-1.md theo 8 mục trong QUY TẮC CHUNG, chưa sửa code, rồi dừng chờ duyệt.
Đọc trước: docs/UX_REDESIGN.md mục 5.5, 6.1, 6.2; docs/BUOC0_DOI_CHIEU.md (nếu có chỗ mâu thuẫn, theo BUOC0).
Mockup tham chiếu: hàm search(), scoreOf(), insertAsset() trong <script> của docs/ui_mockup.html.

Tạo src/services/ gồm:
1. resolve_gateway.py — ResolveGateway(QObject) sống trên QThread riêng.
   - submit(fn) đưa lệnh vào hàng đợi, chạy TUẦN TỰ, trả kết quả qua signal (hoặc callback gọi về main thread).
   - Dùng lại logic kết nối trong src/core/resolve_bridge.py và resolve_api.connect().
   - Mất kết nối: phát connectionChanged(False), tự thử lại mỗi 3s.
2. resolve_state.py — ResolveStateService: mỗi 700ms hỏi GetCurrentPage() + timecode playhead qua gateway.
   - Chỉ phát pageChanged khi trang đổi. Phát playheadChanged(tc). Dừng poll khi mất kết nối.
3. insert_service.py — InsertService.insert(asset, mode) -> InsertResult(ok, track, start_tc, item_ref, error).
   - Gom các đường chèn có sẵn (danh sách từ BƯỚC 0 mục 2). Title worker gọi qua QProcess, không dùng subprocess.run.
   - mode: "auto" (track trống an toàn), "new_track", "fusion_paste".
   - undo(result): chỉ xóa nếu trong 30s và item tại vị trí đó còn đúng tên. Nếu API không hỗ trợ xóa (BƯỚC 0 mục 5), trả về lỗi rõ ràng, không đoán.
4. search_index.py — SearchIndex:
   - Dựng index từ AssetIndexer + bảng alias + motion_tags.
   - Chấm điểm đúng bảng mục 5.5. BỎ chiều khớp "clean_item in clean_q". Tách "mo dan" khỏi fade_out (dùng "toi dan", "bien mat").
   - Hỗ trợ tiền tố c: t: s: m: l: f:.
   - record_use(asset_id) lưu frecency ở ~/.cache/chundvc/frecency.json.
   - set_page_boost(categories) để cộng +25 cho danh mục của trang hiện tại.
5. Thêm python-xlib vào requirements.txt (dùng ở bước 5).

KHÔNG sửa tab_assets.py, auto_window.py, app.py, panel_edge_rail.py. UI cũ chạy như trước.

Test trong tests/services/:
- search: "rung giat" → item shake đứng đầu; "up" không khớp với "zoom_out"/"rung"; "mo dan" → chỉ item fade_in; "s: rung" chỉ trả âm thanh; tìm 600 tài nguyên thật < 16ms/lần.
- insert: dùng Resolve giả; track V2 bận → chèn V3; hết track → mode auto trả lỗi; undo sau 31s bị từ chối.
- state: page không đổi thì không phát signal.

NGHIỆM THU:
[ ] pytest tests/services/ pass hết
[ ] python -c "import src.services.resolve_gateway" không kéo theo QtWidgets
[ ] Thời gian tìm kiếm trung bình được in trong báo cáo
```

---

## BƯỚC 2 — Thư viện tài nguyên dạng Model/View

```
Làm theo QUY TẮC CHUNG trong docs/PROMPTS.md. Đây là BƯỚC 2: Library model/view.
BẮT ĐẦU BẰNG LƯỢT 1 (ĐỀ XUẤT): viết docs/plans/buoc-2.md theo 8 mục trong QUY TẮC CHUNG, chưa sửa code, rồi dừng chờ duyệt.
Đọc trước: docs/UX_REDESIGN.md mục 3.1, 3.4, 6.1 (thư mục ui/lux/library/), 6.4.
Mockup tham chiếu: lưới thẻ trong Dock (mở mockup, rê chuột vào mép phải). Hàm previewHtml(), renderGrid(), ResizeObserver tính số cột.

Tạo src/ui/lux/library/:
1. asset_model.py — QAbstractListModel trên AssetRepository/AssetIndexer. Role: name, kind, duration, thumb, is_fav, preview_path.
2. asset_filter.py — QSortFilterProxyModel lọc theo danh mục, nhóm con, ô tìm (gọi SearchIndex của bước 1, giữ thứ tự điểm).
3. asset_delegate.py — QStyledItemDelegate tự vẽ thẻ: preview 16:9 bo 8px, huy hiệu loại góc trên trái, thời lượng góc dưới phải, 1 dòng tên cắt "…", tim nếu yêu thích. Viền tím khi chọn.
   - KHÔNG hiện mã nội bộ (kinetic, glitch_cyber...). KHÔNG có nút "+".
   - Âm thanh: vẽ waveform thay thumbnail.
4. asset_grid.py — QListView IconMode, setUniformItemSizes(True), LayoutMode Batched.
   Số cột: cols = max(2, floor((w + 8) / (min_card_w + 8))), min_card_w = 180 (140 khi rộng < 360).
   Click = chọn; double-click = InsertService.insert; chuột phải = menu (Yêu thích, Dán vào Fusion, Mở thư mục); kéo = mime data như hiện tại.
5. hover_preview.py — MỘT QMediaPlayer + QVideoSink dùng chung, hiện đè lên ô đang hover sau 150ms, dừng khi rời. Tài nguyên chưa có video preview: dùng canvas procedural của MacroCard; chỉ có thumbnail: không phát gì.
6. Thumbnail async: kết quả từ asset_cache trả về bằng model.dataChanged(index), không gán QPixmap vào QLabel.

Tích hợp: trong TabAssets, thay khu lưới cũ (scroll_grid + hàng trăm AssetCard) bằng asset_grid mới. GIỮ NGUYÊN rail, cột danh mục, Inspector cũ. Signal chọn thẻ phải cập nhật Inspector cũ như trước.

NGHIỆM THU:
[ ] 580+ tài nguyên thật: cuộn mượt, gõ tìm không giật (đo thời gian lọc, ghi vào báo cáo)
[ ] Không còn tạo AssetCard hàng loạt (đếm số widget con trước/sau, ghi vào báo cáo)
[ ] Inspector cũ vẫn cập nhật khi chọn thẻ; nút Chèn tại playhead cũ vẫn chạy
[ ] Hover 150ms mới phát preview; chỉ một thẻ phát tại một thời điểm
[ ] Ảnh chụp lưới ở rộng 420px và 1200px đính kèm báo cáo
```

---

## BƯỚC 3 — Dock mới (Edge Rail + Drawer)

```
Làm theo QUY TẮC CHUNG trong docs/PROMPTS.md. Đây là BƯỚC 3: Dock mới.
BẮT ĐẦU BẰNG LƯỢT 1 (ĐỀ XUẤT): viết docs/plans/buoc-3.md theo 8 mục trong QUY TẮC CHUNG, chưa sửa code, rồi dừng chờ duyệt.
Đọc trước: docs/UX_REDESIGN.md mục 3 (toàn bộ, đặc biệt 3.2, 3.3, 3.5, 3.6); docs/BUOC0_DOI_CHIEU.md mục 9.
Mockup tham chiếu: toàn bộ #dock — trạng thái rail/peek/mở/rộng/ghim, header, chip danh mục, thanh hành động, sheet "Tùy chỉnh" (phím Tab), nhãn khi kéo thả.

Tạo src/ui/lux/shell/edge_dock.py thay EdgeDrawerWindow (giữ file cũ, thêm cờ cấu hình để chọn bản cũ/mới):
1. Máy trạng thái: Ẩn / Rail 44px / Peek 56px / Mở / Ghim / Rộng 760px. Mặc định: Rail 44px.
   - Rê vào rail >80ms → Peek; >450ms → Mở. Rời >600ms → Thu, TRỪ KHI: ghim, ô tìm có chữ, sheet mở, đang kéo, menu mở.
   - Mở 200ms OutCubic, thu 160ms InCubic. Chỉ đổi geometry, không dựng lại widget.
2. Chiều rộng khi mở = clamp(320, 30% rộng cửa sổ Resolve, 420). Dock bám khung cửa sổ Resolve (ResolveWindowAnchor), không bám màn hình. Cửa sổ Resolve hẹp hơn 900px → Dock nổi ra ngoài mép phải khung Resolve.
3. FOCUS (quan trọng nhất): Qt.WindowDoesNotAcceptFocus + WA_ShowWithoutActivating. Chỉ nhận focus khi click ô tìm hoặc ô nhập trong sheet. Sau MỌI thao tác chèn và khi bấm Esc: trả focus về Resolve bằng hàm focus trong fast_paste.py.
4. Nội dung (dock_header.py, category_nav.py, action_bar.py, library_view.py, inspector/inspector_host.py):
   - Header 44px: chip trang · ô tìm · nút rộng · ghim · thu.
   - Chip danh mục ngang cuộn được + nhóm con. Bỏ rail 78px có chữ bị cắt.
   - Lưới từ bước 2.
   - Thanh hành động dính đáy: 1 dòng thông tin + 1 nút chính + "Tùy chỉnh (Tab)" + "⋯" (Dán Fusion, Sao chép code, Cài vào Fusion, Hút preset, Yêu thích, Mở thư mục).
   - Thanh trạng thái 22px: kết nối · tên timeline · timecode playhead (từ ResolveStateService).
   - Inspector là SHEET: < 560px trượt từ đáy cao 62%; 560–1099px trượt từ phải 320px. Nội dung dùng lại các trình chỉnh của Inspector cũ, chia theo loại (chữ, âm thanh, LUT, macro).
5. Kéo thả: nhãn theo con trỏ "Thả để chèn tại playhead <TC>"; trên Dock đổi thành "Thả trên DaVinci để chèn" và thả = hủy.
6. tray.py: QSystemTrayIcon (Mở Dock, Mở Director, Cài đặt, Thoát). FloatingBubble thành tùy chọn trong Cài đặt, MẶC ĐỊNH TẮT.
7. Phím khi Dock mở và không gõ chữ: Enter = nút chính, Tab = sheet, Esc = đóng sheet / trả focus.

Chưa làm đổi ngữ cảnh theo trang (bước 4). Tạm dùng hồ sơ Edit.

NGHIỆM THU (làm trên DaVinci thật, ghi kết quả từng dòng):
[ ] Mở Dock, click 1 thẻ, rồi bấm Space trong Resolve → Resolve play/pause được
[ ] Double-click thẻ → chèn vào track trống, focus về Resolve, phím J/K/L dùng được ngay
[ ] Ở 420px không có chữ nào bị cắt kiểu "uyển cả", "éo Da'"
[ ] Resolve thu nhỏ còn ~1000px: Dock co về 320px, không che hết viewer
[ ] Đang gõ ô tìm rồi rê chuột ra ngoài → Dock không tự thu
[ ] Ảnh chụp: rail, peek, mở 420, rộng 760, sheet đáy, sheet phải
```

---

## BƯỚC 4 — Tự đổi theo trang DaVinci

```
Làm theo QUY TẮC CHUNG trong docs/PROMPTS.md. Đây là BƯỚC 4: Context router.
BẮT ĐẦU BẰNG LƯỢT 1 (ĐỀ XUẤT): viết docs/plans/buoc-4.md theo 8 mục trong QUY TẮC CHUNG, chưa sửa code, rồi dừng chờ duyệt.
Đọc trước: docs/UX_REDESIGN.md mục 4 (toàn bộ).
Mockup tham chiếu: bấm các trang ở đáy (Media, Cut, Edit, Fusion, Color, Fairlight, Deliver), xem Dock đổi. Thử rê chuột trong Dock rồi đổi trang → hiện chip "Fusion ›" chờ. Bấm chip trang để khóa hồ sơ. Biến PROFILES, hàm onResolvePage(), applyPending(), applyProfile(), renderSpecial().

Tạo src/ui/lux/context/:
1. context_profiles.py — dataclass ContextProfile(page, label, color, categories, primary_action, special_block) cho 7 trang, đúng bảng mục 4.1.
2. context_router.py — nghe ResolveStateService.pageChanged:
   - Hoãn đổi khi: chuột trong Dock, ô tìm có chữ, sheet mở, đang kéo. Hiện chip "<Trang> ›" trong header; áp dụng khi chuột rời Dock hoặc bấm chip.
   - Crossfade nội dung 140ms. Không đổi chiều rộng.
   - Mỗi hồ sơ nhớ danh mục, nhóm con, vị trí cuộn.
   - Bấm chip trang → menu: "Theo DaVinci (tự động)" + "Khóa: <trang>".
   - Mất kết nối: giữ hồ sơ cuối, chấm trạng thái xám, nút chính thành "Kết nối lại".
3. Vạch màu 2px mép trái Dock và chấm trong chip theo màu trang (Edit tím, Fusion cyan, Fairlight hồng, Color hổ phách, Cut/Deliver/Media xám). Nút chính luôn tím.
4. Khối đặc biệt từng trang:
   - Media: số clip đang chọn + "Phân tích & dựng thô" (mở Director) + Tạo proxy.
   - Cut: hàng 12 Pad âm thanh, phím 1–9, 0, -, = khi Dock mở.
   - Edit: thanh gợi ý theo câu thoại tại playhead (ContextualSuggester), ẩn được.
   - Fusion: nút chính "Dán vào Fusion" (fast_paste); khối "Hút thành preset" (preset_capture).
   - Color: lưới LUT; nút chính áp cho clip; menu ⋯ có "Áp cho cả timeline".
   - Fairlight: chuẩn hóa loudness (−14/−16/−23 LUFS, chọn track) + Pad.
   - Deliver: 4 thẻ preset + hàng đợi render có % thật (GetRenderJobStatus mỗi 1s qua gateway).
5. SearchIndex.set_page_boost() gọi mỗi khi đổi hồ sơ.

NGHIỆM THU:
[ ] Đổi trang trong Resolve → Dock đổi trong < 1s
[ ] Đang rê chuột trong Dock rồi đổi trang → không nhảy, hiện chip chờ; rời chuột mới đổi
[ ] Fusion: double-click macro → dán vào Fusion đúng, focus về Resolve
[ ] Deliver: render chạy, thanh % cập nhật
[ ] Quay lại Edit → đúng danh mục và vị trí cuộn cũ
```

---

## BƯỚC 5 — Quick Palette + phím tắt toàn cục

```
Làm theo QUY TẮC CHUNG trong docs/PROMPTS.md. Đây là BƯỚC 5: Palette.
BẮT ĐẦU BẰNG LƯỢT 1 (ĐỀ XUẤT): viết docs/plans/buoc-5.md theo 8 mục trong QUY TẮC CHUNG, chưa sửa code, rồi dừng chờ duyệt.
Đọc trước: docs/UX_REDESIGN.md mục 5 (toàn bộ), 6.3.
Mockup tham chiếu: bấm Ctrl+K trong mockup. Thử "rung giat", "zoom", "swoosh", "s: rung", ">render", phím ↑↓ Enter Shift+Enter Alt+1..9 Tab Ctrl+Enter Esc. Ô trống hiện Gần đây / Yêu thích / Gợi ý theo trang. Hàm openPalette(), renderPalette(), runPal(), COMMANDS.

Tạo:
1. src/services/global_hotkey.py
   - Linux X11: python-xlib XGrabKey trên root window, đọc sự kiện trong QThread, phát signal về main thread. Grab kèm các tổ hợp NumLock/CapsLock.
   - Windows: RegisterHotKey qua ctypes + QAbstractNativeEventFilter.
   - Mặc định Ctrl+Alt+Space (Palette), Ctrl+Alt+Shift+Space (chế độ lệnh). Đổi được trong Cài đặt; báo lỗi nếu tổ hợp đã bị chiếm.
   - Chỉ hiện Palette khi cửa sổ trước là DaVinci Resolve hoặc ChunDVC.
   - Wayland: không grab; thêm lệnh CLI `chundvc --palette` gửi tín hiệu qua QLocalSocket tới instance đang chạy.
2. src/ui/lux/palette/command_palette.py, palette_model.py, command_registry.py
   - Cửa sổ frameless 640px; cao 56px (trống) tới tối đa 420px (7 dòng × 48px + chân 28px). Căn giữa ngang theo cửa sổ Resolve, mép trên ở 22% chiều cao. Nền #131316 94%, viền #33333a, bo 12px.
   - Hiện 120ms (fade + dịch 8px), ẩn 80ms. Con trỏ ở ô nhập ngay khi hiện.
   - Mỗi dòng: thumbnail 64×36 (dòng đang chọn phát preview sau 150ms), tên, loại · lý do khớp (ví dụ "rung 92%"), gợi ý phím.
   - Phím đúng bảng mục 5.3. Enter chèn qua InsertService rồi đóng và trả focus Resolve.
   - Tiền tố ">" là lệnh; Director và Deliver đăng ký lệnh vào command_registry.
3. src/ui/lux/feedback/toast.py — toast 4s góc dưới cửa sổ Resolve: "Đã chèn <tên> · <track> · <TC>  [Hoàn tác]". Lỗi: viền đỏ + 1 câu lý do + 1 nút sửa ("Tạo track mới").

NGHIỆM THU (trên DaVinci thật):
[ ] Từ lúc bấm phím tới lúc clip nằm trên timeline < 2 giây (đo 10 lần, ghi trung bình)
[ ] "rung giat" Enter → hiệu ứng shake ở playhead, track trống an toàn
[ ] Sau Enter, phím Space trong Resolve dùng được ngay
[ ] Hoàn tác trong toast xóa đúng clip vừa chèn
[ ] Phím tắt không bật Palette khi đang ở trình duyệt hoặc ứng dụng khác
```

---

## BƯỚC 6 — Director (gộp AutoCut + Copilot)

```
Làm theo QUY TẮC CHUNG trong docs/PROMPTS.md. Đây là BƯỚC 6: Director.
BẮT ĐẦU BẰNG LƯỢT 1 (ĐỀ XUẤT): viết docs/plans/buoc-6.md theo 8 mục trong QUY TẮC CHUNG, chưa sửa code, rồi dừng chờ duyệt.
Đọc trước: docs/UX_REDESIGN.md mục 2 (bảng sáp nhập, Director 4 bước).
Mockup tham chiếu: bấm nút ▶ trên rail (hoặc Ctrl+K gõ ">director"). Đi qua 4 bước Nguồn → Kiểu dựng → Duyệt & kịch bản → Dựng. Thử "Tùy chỉnh nâng cao ›", "Dùng Web Prompt miễn phí", nút Dựng timeline.

Tách src/ui/auto/auto_window.py thành src/ui/director/:
- director_window.py (khung + stepper + chân trang), director_controller.py (logic, gọi PipelineWorker/LLMDirectorWorker cũ — KHÔNG viết lại pipeline).
- pages/: source_page.py, style_page.py, review_page.py, build_page.py.

Yêu cầu:
1. Tối thiểu 1024×640px.
2. Stepper 4 bước. Chân trang luôn chỉ có MỘT nút chính (tím): "Tiếp: …" hoặc "Dựng timeline". Nút phụ: Quay lại, Lưu thành recipe.
3. Bước 1 Nguồn: vùng thả, 4 nguồn (Thư mục, Tệp video, Từ DaVinci, Google Drive), danh sách clip kèm trạng thái cache. Model Whisper + ngôn ngữ chuyển vào Cài đặt; chỉ hiện chip nhỏ "Whisper small · vi" ở thanh tiêu đề.
4. Bước 2 Kiểu dựng: giữ 4 thẻ (Podcast, Shorts, Vlog có Hook, Tự chỉnh) + segmented Loại footage, Nhịp dựng + thanh Mức cắt + chip tính năng. Toàn bộ checkbox nâng cao của TabAutoCut cũ chuyển vào bảng trượt bên phải 280px.
5. Bước 3 Duyệt & kịch bản: bảng câu thoại Giữ/Cắt (dùng ReviewState có sẵn, Ctrl+Z), mini timeline màu. Khối "Kịch bản AI": engine lấy từ Cài đặt, nút "Lên kịch bản AI" (nút phụ). Web Prompt miễn phí là phần mở rộng: "Sao chép prompt" + vùng dán nhận diện tự động khi Ctrl+V (bỏ ô JSON lớn).
6. Bước 4 Dựng: tóm tắt lựa chọn, công tắc "Phủ phụ đề Kinetic lên V2", Beat-sync, nhạc nền chọn từ Library › Nhạc nền. Nút chính "Dựng timeline" → cột tiến độ 260px bên phải hiện ra (ẩn khi rảnh) với 6 bước pipeline có thật.
7. Cài đặt: thêm mục Engine AI (Web Prompt / Local Ollama / Cloud API + key), Whisper model, ngôn ngữ.
8. Đăng ký lệnh Palette: ">cắt khoảng lặng", ">mở director".
9. TabCopilot, TabAutoCut cũ VẪN CÒN (bước 7 mới xóa); Director mới mở được từ tray, rail, Palette.

NGHIỆM THU:
[ ] Chạy trọn 1 video thật từ bước 1 tới timeline mới trong DaVinci, kết quả giống luồng cũ
[ ] Mọi tùy chọn của TabAutoCut cũ đều có chỗ trong Director (liệt kê bảng đối chiếu cũ → mới)
[ ] Web Prompt: dán JSON từ ChatGPT/Claude vào cửa sổ → nhận diện được Video Plan / Kinetic Plan
[ ] Ảnh chụp 4 bước + bảng nâng cao + cột tiến độ
```

---

## BƯỚC 7 — Dọn dẹp và gỡ code cũ

```
Làm theo QUY TẮC CHUNG trong docs/PROMPTS.md. Đây là BƯỚC 7: Dọn dẹp.
BẮT ĐẦU BẰNG LƯỢT 1 (ĐỀ XUẤT): viết docs/plans/buoc-7.md theo 8 mục trong QUY TẮC CHUNG, chưa sửa code, rồi dừng chờ duyệt.
Đọc trước: docs/UX_REDESIGN.md mục 2 (bảng sáp nhập); docs/BUOC0_DOI_CHIEU.md mục 10.

Chỉ làm sau khi bước 1–6 đã được chủ dự án dùng thật ít nhất vài buổi dựng.

1. Viết lại các test đang phụ thuộc widget ẩn (danh sách ở BUOC0 mục 10) để test qua services/ hoặc widget mới.
2. Xóa các widget ẩn chỉ để chống lỗi test trong tab_assets.py (group_presets, check_subtitle, combo_text_preset...).
3. Xóa: src/ui/app.py, src/ui/tabs/tab_sfx.py, phần LUT trong tab_export.py, src/ui/tabs/tab_copilot.py, src/ui/tabs/tab_autocut.py, StudioWindow cũ nếu đã được Library bản rộng thay thế, EdgeDrawerWindow cũ + cờ cấu hình chọn bản cũ.
4. Chuyển phần render còn lại của tab_export.py vào hồ sơ Deliver nếu còn sót.
5. Grep toàn repo: không còn import tới file đã xóa.
6. Cập nhật README: cách dùng Dock, Palette (phím tắt), Director.

NGHIỆM THU:
[ ] pytest toàn repo pass
[ ] App khởi động không lỗi trên Linux (X11) và Windows
[ ] Tổng số dòng trong src/ui/ trước / sau (ghi vào báo cáo)
[ ] Không còn chức năng nào chỉ tồn tại ở code cũ (liệt kê đối chiếu)
```
