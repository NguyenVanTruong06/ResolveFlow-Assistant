# KẾ HOẠCH TRIỂN KHAI BƯỚC 1: DỊCH VỤ LÕI (CORE SERVICES)

> **Nhánh git:** `ui-redesign/buoc-1`  
> **Nguyên tắc:** Lượt 1 đề xuất kiến trúc chi tiết, chưa chỉnh sửa bất kỳ file mã nguồn nào của ứng dụng.  
> **Tài liệu tham chiếu:** [`docs/UX_REDESIGN.md`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/docs/UX_REDESIGN.md) (mục 5.5, 6.1, 6.2), [`docs/BUOC0_DOI_CHIEU.md`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/docs/BUOC0_DOI_CHIEU.md), [`docs/ui_mockup.html`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/docs/ui_mockup.html).

---

## 1. HIỂU YÊU CẦU

Bước 1 xây dựng tầng hạ tầng dịch vụ nền (`src/services/`) hoàn toàn độc lập với GUI (`QtWidgets` bị cấm tuyệt đối). Tầng này chịu trách nhiệm cách ly hoàn toàn các tác vụ nặng khỏi Main UI Thread:
1. `ResolveGateway`: Gom và thực thi tuần tự mọi lời gọi DaVinci Resolve API trên một `QThread` chuyên dụng, tự phục hồi kết nối mỗi 3 giây.
2. `ResolveStateService`: Lắng nghe trang hiện tại (`GetCurrentPage`) và timecode playhead theo chu kỳ 700ms, chỉ phát signal khi có biến động.
3. `InsertService`: Hợp nhất 7 đường chèn tài nguyên vào DaVinci thành một điểm truy cập duy nhất, chuyển Title Worker sang `QProcess` không đồng bộ, và cung cấp tính năng Hoàn tác (`undo`) an toàn trong vòng 30 giây.
4. `SearchIndex`: Xây dựng cấu trúc chỉ mục bộ nhớ trong siêu tốc (<16ms cho 600+ asset), chuẩn hóa alias chuyển động, hỗ trợ tiền tố lọc danh mục và tính điểm frecency lưu tại `~/.cache/chundvc/frecency.json`.

---

## 2. HIỆN TRẠNG CODE VÀ CÁC ĐIỂM LIÊN QUAN

Dưới đây là các vị trí mã nguồn hiện có sẽ được đóng gói hoặc tích hợp hành vi vào dịch vụ mới:

1. **Kết nối DaVinci Resolve IPC**:
   - [`src/core/resolve_bridge.py:100-180`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_bridge.py#L100-L180): Hàm `ResolveBridge.get_resolve()` dò tìm `bmd` và `fusionscript`.
   - [`src/core/resolve_api.py:38-95`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L38-L95): Hàm `DaVinciResolveAPI.connect()`.
   - *Hiện trạng:* Mỗi nơi tự gọi kết nối độc lập, dễ gây xung đột COM/IPC khi gọi đồng thời từ nhiều luồng.
2. **Thuật toán chèn Title không cắt gọt (Zero-Ripple Nested V2)**:
   - [`src/core/resolve_api.py:1821-1996`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L1821-L1996): `import_preset_nested_v2` trả về `track`, `start`, `duration`, `item_name`.
   - [`src/core/title_insert_worker.py:178`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/title_insert_worker.py#L178): Tiến trình con gọi import title.
   - [`src/core/resolve_api.py:2044`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L2044): Đang gọi `subprocess.run(cmd, timeout=15)` trên Main Thread gây giật đơ UI.
3. **Fast-Paste vào Fusion (`Ctrl+V`)**:
   - [`src/core/fast_paste.py:146-215`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/fast_paste.py#L146-L215): Thao tác clipboard và giả lập hotkey Windows/Linux.
4. **Từ khóa chuyển động và từ khóa song ngữ**:
   - [`src/core/motion_analyzer.py:21-76`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/motion_analyzer.py#L21-L76): `BILINGUAL_TAG_MAP` dính lỗi gộp `"mo dan"` vào `fade_out`.
   - [`src/core/motion_analyzer.py:109`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/motion_analyzer.py#L109): `if clean_q in clean_item or clean_item in clean_q:` làm sai lệch kết quả từ ngắn.
5. **Đọc dữ liệu tài nguyên từ đĩa**:
   - [`src/core/asset_indexer.py:150-250`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/asset_indexer.py#L150-L250): Hàm `scan_all_assets()` quét 580+ tài nguyên nhưng chưa có bộ nhớ RAM index tính điểm.

---

## 3. PHƯƠNG ÁN ĐỀ XUẤT CHI TIẾT

### 3.1. Danh sách file tạo mới & thứ tự thực hiện

```text
src/services/
├── __init__.py               # Đánh dấu package
├── resolve_gateway.py        # 1. Quản lý kết nối & hàng đợi thực thi tuần tự (QThread)
├── resolve_state.py          # 2. Poller 700ms theo dõi GetCurrentPage & Playhead
├── insert_service.py         # 3. Điều phối chèn tài nguyên (QProcess Title + SFX/LUT/FastPaste) + Undo
└── search_index.py           # 4. Inverted Index, Alias, Chấm điểm 7 tín hiệu, Frecency Cache
```

File kiểm thử tương ứng tại `tests/services/`:
```text
tests/services/
├── __init__.py
├── test_search_index.py           # Kiểm tra tốc độ, phân loại prefix, alias, sửa lỗi từ ngắn
├── test_insert_service.py         # Mock Resolve: chèn auto track trống, new track, undo 30s
└── test_resolve_gateway_state.py  # Gateway queue tuần tự, retry 3s, StateService khử trùng lặp signal
```

Cập nhật phụ thuộc:
- `requirements.txt`: Bổ sung `python-xlib>=0.33` (chuẩn bị cho global hotkey Linux X11 ở Bước 5).

---

### 3.2. Thiết kế chi tiết từng Module

#### 1. `src/services/resolve_gateway.py`
- **Class `ResolveGateway(QObject)`**:
  - Sở hữu một `QThread` riêng chạy nền (`self._worker_thread`).
  - Sử dụng hàng đợi tuần tự (`queue.Queue`) chứa các tác vụ `(task_id, fn, args, kwargs)`.
  - Phương thức `submit(fn, *args, **kwargs) -> str`: Trả về `task_id` ngay lập tức (Non-blocking).
  - Tín hiệu Qt:
    - `task_finished = Signal(str, object)`: Bắn `task_id` kèm kết quả về Main Thread.
    - `task_failed = Signal(str, str)`: Bắn `task_id` kèm thông điệp lỗi.
    - `connection_changed = Signal(bool)`: Báo trạng thái kết nối tới DaVinci Resolve.
  - Tự động kiểm tra sức khỏe kết nối (Heartbeat / Retry) mỗi 3 giây nếu mất kết nối.
  - **Quy tắc nghiêm ngặt**: Chỉ import `PySide6.QtCore`, cấm tuyệt đối `QtWidgets` / `QtGui`.

#### 2. `src/services/resolve_state.py`
- **Class `ResolveStateService(QObject)`**:
  - Nhận tham chiếu tới `ResolveGateway`.
  - Sở hữu `QTimer` chu kỳ 700ms chạy trên Main Thread, đẩy lệnh kiểm tra sang Gateway qua `submit()`.
  - Các thuộc tính theo dõi:
    - `current_page: str` (ví dụ `"edit"`, `"fusion"`, `"color"`, `"fairlight"`).
    - `current_playhead: str` (timecode dạng `"01:00:14:08"`).
    - `is_connected: bool`.
  - Tín hiệu Qt:
    - `page_changed = Signal(str)`: **CHỈ PHÁT KHI TRANG ĐỔI THẬT SỰ** (so sánh giá trị cũ/mới).
    - `playhead_changed = Signal(str)`: Phát khi playhead di chuyển.
    - `connection_changed = Signal(bool)`: Đồng bộ với Gateway.
  - Tự động ngưng timer khi Gateway báo `connection_changed(False)` để tránh spam lệnh rác khi DaVinci tắt.

#### 3. `src/services/insert_service.py`
- **Dataclass `InsertResult`**:
  ```python
  @dataclass
  class InsertResult:
      ok: bool
      track: str            # 'V2', 'A3'...
      start_tc: str         # '01:00:04:12'
      duration_sec: float
      item_name: str
      item_ref: Any = None  # Reference hoặc UID
      error: str = ""
      inserted_at: float = 0.0 # timestamp time.time()
  ```
- **Class `InsertService(QObject)`**:
  - Chế độ chèn:
    - `"auto"`: Tự tìm track trống an toàn không ripple (V2/V3... cho video, A2/A3... cho audio). Nếu hết track thì báo lỗi rõ ràng hoặc tự tạo track mới theo cấu hình.
    - `"new_track"`: Ép buộc tạo track mới ở trên cùng và chèn vào đó.
    - `"fusion_paste"`: Dán trực tiếp vào trang Fusion thông qua `fast_paste.py` (Ctrl+V).
  - Tích hợp Title Worker qua `QProcess`:
    - Thay thế hoàn toàn `subprocess.run` bằng `QProcess` bất đồng bộ.
    - Lắng nghe tín hiệu `readyReadStandardOutput` và `finished` của `QProcess`.
    - Trả kết quả `InsertResult` cho giao diện mà không làm đơ khung hình 1 giây nào.
  - Tích hợp các đường chèn khác:
    - SFX Audio: Đẩy lệnh sang `ResolveGateway` thực hiện import media pool và append track audio.
    - LUT: Áp dụng qua Gateway lên clip tại playhead.
    - Kinetic Subtitles: Tích hợp với `KineticEngine` khi người dùng kích hoạt.
  - **Cơ chế Hoàn tác (`undo(result: InsertResult) -> bool`)**:
    - Kiểm tra `time.time() - result.inserted_at <= 30.0`. Nếu quá 30 giây -> Từ chối hoàn tác với thông báo: *"Đã hết thời hạn hoàn tác (30s)"*.
    - Thông qua Gateway, kiểm tra xem tại track và vị trí `start_tc` đó clip có còn mang tên `result.item_name` hay không.
    - Nếu đúng -> Gọi hàm API chính thống `timeline.DeleteClips([item], False)` (`ripple=False` - Lift Delete an toàn).
    - Nếu clip đã bị dời đi hoặc bị xóa trước đó -> Trả về lỗi chi tiết, không đoán mò.

#### 4. `src/services/search_index.py`
- **Class `SearchIndex`**:
  - Khởi tạo nạp toàn bộ danh mục từ `AssetIndexer.scan_all_assets()`.
  - Bộ từ điển đồng nghĩa (Alias Map):
    - Tách hoàn toàn `"mo dan"` ra khỏi `fade_out` (gán `"toi dan"`, `"bien mat"` cho `fade_out`; giữ `"mo dan"` cho `fade_in`).
    - Bổ sung các chuyển động chuẩn: `glitch`, `bounce`, `typewriter`, `shake`, `zoom_in`, `zoom_out`...
  - Hỗ trợ tiền tố danh mục:
    - `c:` -> Titles / Chữ
    - `t:` -> Transitions / Chuyển cảnh
    - `s:` -> SFX / Âm thanh hiệu ứng
    - `m:` -> Memes / Video vui
    - `l:` -> LUTs / Màu sắc
    - `f:` -> Overlays / Generators / Lớp phủ & Sticker
  - **Hệ thống chấm điểm 7 tín hiệu (chuẩn mục 5.5)**:
    1. Trùng khớp nguyên vẹn Alias: `+100` điểm
    2. Khớp tiền tố của tên item: `+80` điểm
    3. Khớp tiền tố của một token trong tên hoặc tag: `+60` điểm
    4. Khớp tiền tố alias (>=3 ký tự): `+50` điểm
    5. Khớp chuỗi con mờ (Subsequence match >=3 ký tự): `+30` điểm
    6. Điểm tin cậy chuyển động: `conf * 20` (khi khớp alias chuyển động)
    7. Ưu tiên theo trang DaVinci hiện tại (`page_boost`): `+25` điểm
    8. Điểm tần suất sử dụng (Frecency): `0 -> +30` điểm
  - **BỎ HẲN** chiều so khớp ngược `clean_item in clean_q` để triệt tiêu lỗi nhận diện sai từ ngắn ("up", "cut").
  - Lưu và đọc bộ nhớ Frecency tại `~/.cache/chundvc/frecency.json` (Atomic write, tối đa 500 entry).

---

## 4. PHẢN BIỆN THIẾT KẾ (CRITICAL REVIEW)

Sau khi đối chiếu kỹ thuật PySide6 và thực tế DaVinci Resolve API:

1. **Về việc trả `Future` trong `ResolveGateway.submit()` (Mục 6.2)**:
   - *Tài liệu viết:* `submit(fn) -> Future`, trả kết quả qua signal hoặc callback.
   - *Phản biện:* Trong mô hình Event-Driven của Qt, việc dùng `concurrent.futures.Future` rất dễ cám dỗ lập trình viên gọi `future.result()` trên Main UI Thread, dẫn đến việc giao diện bị treo cứng (deadlock/freeze).
   - *Giải pháp:* Thiết kế chuẩn Qt là `submit(fn, on_success=None, on_error=None) -> str (task_id)`. Gateway quản lý worker thông qua `Signal/Slot`. Khi worker xong, nó phát tín hiệu chứa kết quả về Main Thread và gọi callback trực tiếp trong môi trường an toàn của Main Thread.
2. **Vấn đề tranh chấp IPC khi chạy song song `ResolveGateway` và `QProcess` Title Worker**:
   - *Vấn đề:* `title_insert_worker.py` chạy qua tiến trình con độc lập kết nối vào `fusionscript`. Nếu `ResolveStateService` vẫn tiếp tục poll mỗi 700ms trong lúc worker đang chèn timeline, hai bên có thể xung đột socket IPC DaVinci.
   - *Giải pháp:* `InsertService` phải có cơ chế phối hợp: Khi bắt đầu chèn bằng `QProcess`, yêu cầu `ResolveStateService.pause_polling()`, sau khi worker chèn xong và thoát thì mới `resume_polling()`.
3. **Kiểm tra an toàn khi Hoàn tác (`undo`)**:
   - *Vấn đề:* Người dùng có thể vô tình trim, cắt đôi hoặc di chuyển clip ngay sau khi vừa chèn xong.
   - *Giải pháp:* Không chỉ dựa vào `timecode` ban đầu. Gateway phải duyệt track đó, tìm item có `startFrame` khớp hoặc xê dịch dưới 1 frame và `item.GetName()` phải trùng khớp 100% với tên đã ghi nhận thì mới xóa. Nếu người dùng đã sửa đổi clip, trả về cảnh báo an toàn thay vì cố xóa.

---

## 5. PHƯƠNG ÁN THAY THẾ (ALTERNATIVES)

| Mục | Phương án A (Thiết kế ban đầu) | Phương án B (Đề xuất tối ưu hơn) | Đánh giá / Khuyến nghị |
|---|---|---|---|
| **Cơ chế trả kết quả Gateway** | `Future` đối tượng | Qt Signals (`task_finished`) + Callback an toàn | **Chọn B**: Tránh triệt để nguy cơ block UI thread do vô tình gọi `.result()`. |
| **Lưu trữ Frecency Cache** | File JSON ghi đè thông thường | Atomic Write (ghi vào `.tmp` rồi rename) | **Chọn B**: Chống hỏng file JSON nếu app bị tắt đột ngột lúc đang dựng phim. |
| **Quản lý QProcess Worker** | Khởi tạo tiến trình mới mỗi lần chèn | Quản lý vòng đời tiến trình qua `QProcess` có pool | **Chọn A cho Bước 1**: Đơn giản, giải phóng bộ nhớ sạch sẽ sau khi chèn xong; chuyển sang pool ở bước sau nếu cần tối ưu ms. |

---

## 6. RỦI RO & BIỆN PHÁP PHÒNG NGỪA

1. **Rủi ro rò rỉ hoặc kéo nhầm `QtWidgets` vào `src/services/`**:
   - *Biện pháp:* Thêm test tự động kiểm tra `sys.modules`:
     ```python
     def test_no_qtwidgets_in_services():
         import src.services.resolve_gateway
         import src.services.insert_service
         import src.services.search_index
         assert "PySide6.QtWidgets" not in sys.modules
     ```
2. **Rủi ro ảnh hưởng tới các tab và cửa sổ cũ (`AutoWindow`, `TabAssets`)**:
   - *Biện pháp:* Các service mới hoàn toàn nằm độc lập trong `src/services/`. Không sửa một dòng nào trong `auto_window.py` hay `tab_assets.py`. Toàn bộ code cũ hoạt động 100% như trước.
3. **Rủi ro timeout khi tìm kiếm trên máy cấu hình yếu**:
   - *Biện pháp:* Inverted index được khởi tạo 1 lần duy nhất khi ứng dụng nạp (`pre-indexed`). Mọi truy vấn tìm kiếm diễn ra trên mảng bit / hashmap của RAM nên thời gian tìm kiếm luôn dưới 5ms (vượt xa chỉ tiêu 16ms).

---

## 7. ƯỚC LƯỢNG THỰC HIỆN

- **Số file tạo mới:** 4 file dịch vụ (`src/services/`) + 3 file kiểm thử (`tests/services/`).
- **Số file cập nhật:** 1 file (`requirements.txt` - thêm `python-xlib`).
- **Độ khó:** 3.5 / 5 (Xử lý hàng đợi tuần tự đa luồng Qt, đồng bộ QProcess bất đồng bộ, thuật toán tìm kiếm tối ưu RAM).
- **Môi trường thử nghiệm:**
  - Bộ test suite `tests/services/` chạy độc lập với mock DaVinci Resolve (không cần bật DaVinci vẫn test pass 100%).
  - Kiểm tra thực tế trên DaVinci Resolve khi chèn preset thật.

---

## 8. CÂU HỎI CHO CHỦ DỰ ÁN

Hiện tại mọi thông số kỹ thuật và logic đã được đối chiếu rõ ràng từ Bước 0. Không có câu hỏi nghẽn nào cản trở kỹ thuật.
Sau khi bạn duyệt bản kế hoạch này, mình sẽ bắt tay vào **LƯỢT 2: Viết code và chạy test nghiệm thu** theo đúng trình tự.
