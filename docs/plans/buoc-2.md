# Kế hoạch BƯỚC 2: Thư viện tài nguyên dạng Model/View (`src/ui/lux/library/`)

## 1. Hiểu yêu cầu (3–5 dòng)
Bước 2 chuyển đổi toàn bộ cơ chế hiển thị danh mục đạo cụ từ việc tạo hàng trăm QWidget `AssetCard` trong `QScrollArea` + `QGridLayout` sang kiến trúc chuẩn công nghiệp Qt Model/View (`QListView` + `QAbstractListModel` + `QSortFilterProxyModel` + `QStyledItemDelegate`). Hệ thống mới ảo hóa hiển thị cho hơn 600 tài nguyên thật, lọc tức thì dưới 16ms nhờ `SearchIndex` (từ Bước 1), tự vẽ thẻ tỉ lệ 16:9 với waveform cho âm thanh, và tái sử dụng duy nhất một `QMediaPlayer` cho hover preview sau 150ms mà không gây rò rỉ RAM hay giật khung hình khi cuộn.

---

## 2. Hiện trạng code (file:dòng)
1. **`src/ui/tabs/tab_assets.py`**:
   - `AssetCard(QFrame)` (L1929–L2750): Lớp card cũ. Mỗi card khởi tạo hàng loạt child widget (QLabel thumbnail, QMovie GIF, QPushButton yêu thích, QMediaPlayer/QAudio riêng, QTimer...), tiêu tốn tài nguyên hệ thống rất lớn.
   - `TabAssets.scroll_grid` & `TabAssets.grid` (L3714–L3730): `QScrollArea` + `QGridLayout` chứa trực tiếp các instances của `AssetCard`.
   - `TabAssets._populate_cards()` (L4769–L4795): Tạo hàng loạt `AssetCard` cho toàn bộ `self.all_assets` (~613 mục), đẩy vào `self.card_widgets`.
   - `TabAssets._filter_cards()` (L4797–L4868): Lặp qua toàn bộ `card_widgets`, gọi `setVisible()` và `grid.addWidget()` lặp đi lặp lại khi người dùng gõ phím tìm kiếm hoặc đổi filter, gây giật lag nghiêm trọng (UI freeze).
   - `TabAssets._on_card_selected()` (L4920+): Hàm xử lý khi chọn thẻ, cập nhật Inspector ở cột 4 và thiết lập trạng thái `selected_asset`.
2. **`src/core/asset_cache.py`** (L1–L150): Quản lý cache thumbnail bất đồng bộ qua `ThreadPoolExecutor`.
3. **`src/services/search_index.py`**: Index đảo và bộ chấm điểm đa tầng đã hoàn thành ở Bước 1, cho tốc độ tìm kiếm < 1ms trên 613 tài nguyên thật.
4. **`src/services/insert_service.py`**: Dịch vụ chèn tài nguyên (auto/new_track/fusion_paste) đã có sẵn từ Bước 1.

---

## 3. Phương án đề xuất

### 3.1. Danh sách file tạo mới (`src/ui/lux/library/`)
1. **`src/ui/lux/library/asset_model.py`**:
   - Lớp `AssetListModel(QAbstractListModel)`:
     - Lưu trữ danh sách phẳng các tài nguyên (`StudioAsset` hoặc asset dict).
     - Định nghĩa các Custom Roles: `IdRole`, `NameRole`, `CategoryRole`, `SubCategoryRole`, `DurationRole`, `ThumbnailRole`, `IsFavRole`, `PreviewPathRole`, `AssetObjRole`, `MotionRole`, `KindRole`.
     - `rowCount()`, `data()`: Đọc dữ liệu theo role với độ trễ $O(1)$.
     - `update_thumbnail(asset_id, pixmap)`: Cập nhật pixmap đã load bất đồng bộ và phát `dataChanged(index, index, [ThumbnailRole])`.
     - `toggle_favorite(asset_id)`: Lưu vào `FavoritesManager` và phát `dataChanged`.
2. **`src/ui/lux/library/asset_filter.py`**:
   - Lớp `AssetFilterProxyModel(QSortFilterProxyModel)`:
     - Nhận `SearchIndex` (Bước 1), `current_category`, `current_sub_category`, `filter_mode` ("all", "fav", "recent").
     - Khi có `search_query`: Chạy qua `SearchIndex.search()`, lưu map `asset_id -> score`.
     - Ghi đè `filterAcceptsRow()`: Lọc theo danh mục/nhóm con hoặc danh sách kết quả tìm kiếm.
     - Ghi đè `lessThan()`: Sắp xếp theo điểm số của `SearchIndex` giảm dần (giữ thứ tự ưu tiên chính xác nhất ở đầu danh sách).
3. **`src/ui/lux/library/asset_delegate.py`**:
   - Lớp `AssetCardDelegate(QStyledItemDelegate)`:
     - `paint(painter, option, index)`:
       * Vẽ thẻ 16:9 với góc bo tròn 8px bằng `QPainterPath`.
       * Vẽ thumbnail: Vẽ `QPixmap` nếu đã nạp; nếu là Audio/SFX/BGM vẽ waveform giả lập theo seed hash màu xanh cyan (`#22d3ee`) hoặc tím (`#c084fc`); nếu chưa có ảnh vẽ placeholder gradient sang trọng.
       * Vẽ huy hiệu loại ở góc trên trái (icon nhỏ + nhãn loại gọn gàng).
       * Vẽ thời lượng ở góc dưới phải (badge mờ bo tròn 4px).
       * Vẽ tim đỏ nếu `is_fav=True`.
       * Vẽ dòng tên ở đáy: Cắt bằng dấu ba chấm `...` bằng `QFontMetrics.elidedText()`.
       * Trạng thái chọn: Vẽ viền tím `#8b5cf6` (2px), nền nổi nhẹ. Hover: Viền sáng mờ nhẹ.
4. **`src/ui/lux/library/asset_grid.py`**:
   - Lớp `AssetGridView(QListView)`:
     - `setViewMode(QListView.IconMode)`, `setResizeMode(QListView.Adjust)`, `setUniformItemSizes(True)`, `setLayoutMode(QListView.Batched)`.
     - Tự động tính số cột trong `resizeEvent`:
       `cols = max(2, floor((w + 8) / (min_card_w + 8)))`, `min_card_w = 180` (140 khi w < 360).
     - Xử lý chuột:
       * Click: Phát `asset_selected(asset)`.
       * Double-click: Gọi `InsertService.insert()` hoặc phát `asset_activated(asset)`.
       * Right-click: Hiện context menu (Yêu thích, Dán vào Fusion, Mở thư mục).
       * Kéo thả: Đóng gói `QMimeData` chuẩn để kéo thả vào DaVinci Resolve.
5. **`src/ui/lux/library/hover_preview.py`**:
   - Lớp `SharedHoverPreview(QObject)`:
     - Quản lý **MỘT** instance `QMediaPlayer` + `QVideoSink` duy nhất tái sử dụng.
     - Đặt một floating widget đè lên ô card đang hover sau debounce 150ms.
     - Khi chuột rời hoặc view cuộn: Dừng phát (`stop()`) và ẩn widget ngay lập tức.
     - Âm thanh: Chỉ phát khi người dùng bật tùy chọn "Nghe khi rê".
6. **`src/ui/lux/library/__init__.py`**: Export các lớp chính cho toàn hệ thống.

### 3.2. Tích hợp vào `src/ui/tabs/tab_assets.py`
- Thay thế `self.scroll_grid` bằng `self.asset_grid = AssetGridView()`.
- Gắn `AssetListModel` và `AssetFilterProxyModel` vào `self.asset_grid`.
- Đấu nối signal `asset_grid.asset_selected` gọi `self._on_card_selected(asset_id)` để cập nhật đầy đủ Inspector ở Cột 4.
- Giữ nguyên toàn bộ cấu trúc Rail, Cột danh mục, Inspector cũ và các signal công khai để không làm ảnh hưởng đến các thành phần khác.
- Giữ lớp `AssetCard` cũ làm fallback/tương thích để các test suite hiện hữu không bị lỗi import.

---

## 4. Phản biện thiết kế & Thách thức kỹ thuật

1. **Hiển thị Animation trên QListView**:
   - *Thiết kế mockup*: Dùng animation CSS keyframes quay liên tục trong HTML DOM.
   - *Thực tế PySide6*: Nếu dùng `QStyledItemDelegate` để render animation liên tục bằng repaint timer, toàn bộ `QListView` sẽ phải repaint ở tần số cao, gây ngốn CPU.
   - *Giải pháp*: Lưới delegate chỉ render tĩnh cực nhanh và tiết kiệm tài nguyên. Animation preview chỉ kích hoạt khi hover chuột qua 150ms thông qua `SharedHoverPreview` (1 player duy nhất nổi đè lên ô tương ứng).
2. **Vẽ Waveform cho âm thanh**:
   - *Thực tế*: Quét file audio thật để giải mã waveform của 100+ file âm thanh mỗi lần mở app sẽ làm nghẽn I/O ổ đĩa.
   - *Giải pháp*: Delegate tính toán waveform procedural cực nhanh từ seed hash của `asset.id` (thuật toán pseudo-random 20-30 vạch trong 0.01ms giống mockup HTML), cho cảm giác thị giác chân thực mà không tốn tài nguyên đĩa.
3. **Kích thước Card và Responsive Grid**:
   - Trong `QListView.IconMode`, kích thước item phải đồng nhất (`setUniformItemSizes(True)`). Do đó trong `resizeEvent`, ta tính toán `gridSize` và `itemSize` theo bề rộng thật của viewport (đã trừ scrollbar) để đảm bảo thẻ luôn lấp đầy không gian mà không sinh thanh cuộn ngang.

---

## 5. Phương án thay thế
- **Phương án A (Tạo widget trong QListWidget bằng setItemWidget)**:
  - *Nhược điểm*: Vẫn tạo ra hàng trăm QWidget thật, không giải quyết được gốc rễ bài toán giật lag và rò rỉ bộ nhớ.
- **Phương án B (QListView ảo hóa + QStyledItemDelegate tự vẽ - KHUYÊN DÙNG)**:
  - *Ưu điểm*: Chỉ vẽ những thẻ đang lọt vào khung nhìn (khoảng 10-20 thẻ). Số lượng widget thực tế = 1 (`QListView`). Cuộn 60fps mượt mà trên hàng nghìn tài nguyên.

---

## 6. Rủi ro & Cách khắc phục
- **Rủi ro 1**: Các unit test cũ (`tests/test_studio_window.py`, `tests/test_tab_assets.py`, `tests/test_tab_assets_smart_search.py`) đang import hoặc truy cập các thuộc tính của `TabAssets` hoặc `AssetCard`.
  - *Cách khắc phục*: Giữ nguyên các public methods và attributes của `TabAssets` (`rail_btns`, `_filter_by_rail`, `_on_card_selected`, `selected_asset`, `lbl_cats_head`, etc.) và giữ lại class definition của `AssetCard` để tương thích ngược 100%.
- **Rủi ro 2**: QMediaPlayer thiếu codec trên một số môi trường Linux tối giản.
  - *Cách khắc phục*: Bọc try/except khi khởi tạo `QMediaPlayer`, fallback mượt mà về hiển thị thumbnail tĩnh.

---

## 7. Ước lượng
- **Số file tạo mới**: 6 files (`src/ui/lux/library/*.py`) + 1 test file (`tests/ui/test_asset_library_model_view.py`).
- **Số file sửa đổi**: 1 file (`src/ui/tabs/tab_assets.py`).
- **Độ khó**: 3.5 / 5.
- **Kiểm thử DaVinci thật**: Kéo thả mime-data vào timeline và double click chèn (đã có mock an toàn trong test suite).

---

## 8. Câu hỏi cho chủ dự án
- Không có vướng mắc bế tắc. Kế hoạch đã tuân thủ chặt chẽ 100% tài liệu `UX_REDESIGN.md` và `PROMPTS.md`.
