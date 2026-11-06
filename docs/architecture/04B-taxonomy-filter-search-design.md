# THIẾT KẾ KIẾN TRÚC G04-04B-D: TAXONOMY, BỘ LỌC ĐA TẦNG VÀ SEARCH INVALIDATION

- **Mã định danh:** `G04-04B-D`
- **Trạng thái:** `IMPLEMENTED_PENDING_CODEX_REVIEW` (Lát cắt G04-04B1-I đã triển khai và đạt 34/34 tests PASS; 04B2 deferred)
- **Cấp độ Worker:** `HIGH`
- **Tác giả:** Antigravity (Worker)
- **Tech Lead thẩm định:** Codex Tech Lead
- **Ngày lập:** 2026-10-26
- **Mục tiêu:** Chốt chuẩn hóa taxonomy, bảng chân trị bộ lọc đa tầng, cơ chế cập nhật chỉ mục tìm kiếm (Search Invalidation), và giải quyết các điểm nghẽn hiệu năng/hiển thị giữa Dock (`EdgeDockWindow`) và Studio (`TabAssets`).

---

## 1. CALLGRAPH ĐỐI CHIẾU MÃ NGUỒN THỰC TẾ (FILE:HÀM:DÒNG)

Tất cả các dòng mã được đối chiếu trực tiếp từ mã nguồn thực tế tại thời điểm hiện tại:

```
[CODE_VERIFIED]  Mã nguồn hiện có đang chạy trong dự án
[PROPOSED]       Đặc tả thay đổi/bổ sung được đề xuất trong thiết kế
[UNVERIFIED]     Logic phán đoán chưa qua đo kiểm thực tế
```

### 1.1. Tầng Adapter dữ liệu Asset DTO
- `to_ui_asset(record)` [`src/core/contracts/asset_dto.py:420–492`][CODE_VERIFIED]:
  - Dòng 440–456: Ánh xạ `category` lưu trữ sang `tab` UI:
    - `overlays` $\to$ `tab = "overlay"`
    - `memes` $\to$ `tab = "meme"`
    - `luts` $\to$ `tab = "lut"`
    - `templates`: nếu `template_type in ("trans", "transition")` $\to$ `tab = "trans"`; ngược lại $\to$ `tab = "text"`
    - Custom category: `tab = category`
  - Dòng 459–460: Gán `item["tab"] = tab; item["category"] = tab` (Đồng nhất `tab` và `category` cho UI consumers).
  - Dòng 463–468: Ưu tiên `subcategory` $\to$ fallback `sub` $\to$ mặc định `'Tất cả'`.
  - Dòng 470: Gán `item["storage_managed"] = True`.
  - Dòng 473–484: Chuẩn hóa duration an toàn (legacy tolerance: 0, -1, NaN, Inf, overflow $\to$ `None`).
  - Dòng 487–490: Tránh lặp hậu tố `' · Offline'`.

### 1.2. Tầng Model danh sách ảo hóa (Virtual List Model)
- `extract_asset_info(item)` [`src/ui/lux/library/asset_model.py:36–109`][CODE_VERIFIED]:
  - Dòng 38–50 (Dict item): Đọc `asset_id`, `name`, `cat = tab or category or 'text'`, `pack_name`, `sub`, `dur`, `tags`, `motion`.
  - Dòng 51–74 (Object item): Hỗ trợ `TextStylePreset` (`cat="text"`), `TransitionStylePreset` (`cat="trans"`), hoặc thuộc tính `tab`/`category`.
  - Dòng 76–92: Ánh xạ sang nhãn hiển thị `kind` tiếng Việt ("Chữ", "Chuyển cảnh", "SFX", "Meme", "Lớp phủ", "LUT", "Sticker"...).
  - Dòng 94–108: Trả về dict chuẩn cho các Role trong `AssetListModel`.
- `AssetListModel` [`src/ui/lux/library/asset_model.py:111–288`][CODE_VERIFIED]:
  - Dòng 20–33: Khai báo các custom Roles: `IdRole (UserRole+1)`, `NameRole (+2)`, `KindRole (+3)`, `DurationRole (+4)`, `ThumbnailRole (+5)`, `IsFavRole (+6)`, `PreviewPathRole (+7)`, `CategoryRole (+8)`, `SubCategoryRole (+9)`, `AssetObjRole (+10)`, `MotionRole (+11)`, `FilePathRole (+12)`, `TagsRole (+13)`, `PackNameRole (+14)`.
  - Dòng 219–224 (`set_assets`): `beginResetModel()`, nạp `_assets = [extract_asset_info(it)...]`, lập hash map `_id_to_row = {id: idx}`, `endResetModel()`.
  - Dòng 226–230 (`index_of_asset_id`): Tra cứu $O(1)$ qua `_id_to_row` trả về `QModelIndex` gốc.

### 1.3. Tầng Bộ lọc Proxy (QSortFilterProxyModel)
- `normalize_taxonomy(text)` [`src/ui/lux/library/asset_filter.py:23–27`][CODE_VERIFIED]:
  - Chuẩn hóa Unicode NFC, lower-case, strip khoảng trắng.
- `CANONICAL_CATEGORIES` & `get_canonical_category(cat)` [`src/ui/lux/library/asset_filter.py:30–50`][CODE_VERIFIED]:
  - Bảng alias ánh xạ tên danh mục tiếng Việt/tiếng Anh về ID chuẩn (ví dụ "âm thanh" $\to$ "sfx", "màu" $\to$ "lut", "chuyển cảnh" $\to$ "trans").
  - Nếu không thuộc alias đã biết (custom category), trả về chuỗi NFC lower nguyên bản.
- `AssetFilterProxyModel` [`src/ui/lux/library/asset_filter.py:52–261`][CODE_VERIFIED]:
  - Dòng 83–91 (`set_category`): Khi đổi `_category`, reset `_sub_category = "all"`, `_tag = None`, `_pack_filter = None`. **GIỮ NGUYÊN `_search_query`**.
  - Dòng 92–102 (`set_sub_category`): Nếu sub là `"all"` hoặc `"Tất cả"`, reset `_tag = None`.
  - Dòng 103–108 (`set_pack_filter`): Lọc explicit theo `pack_name`.
  - Dòng 113–122 (`set_tag`): Lọc explicit theo `tag`.
  - Dòng 151–173 (`_apply_search`): Gọi `self._search_index.search(self._search_query)`. Lưu `_search_scores` và `_matched_ids`, gọi `invalidate()` và `sort(0, DescendingOrder)`.
  - Dòng 174–243 (`filterAcceptsRow`): Kiểm tra 6 tầng điều kiện:
    1. Query: `asset_id in self._matched_ids`
    2. Filter Mode (`all` / `fav` / `recent` / `coll:`)
    3. Category: `get_canonical_category(self._category) == get_canonical_category(cat)`
    4. Explicit Pack Filter: `pack_norm == self._pack_filter`
    5. Subcategory: `pack:` prefix $\to$ match pack; khác $\to$ `sub_norm == target_sub OR target_sub in tags_norm`
    6. Tag: `tag_norm in tags_norm OR tag_norm == sub_norm`
  - Dòng 245–261 (`lessThan`): Ưu tiên sắp xếp theo điểm search score giảm dần; mặc định theo số thứ tự hàng ban đầu.

### 1.4. Tầng Chỉ mục Tìm kiếm (Inverted Search Index)
- `SearchIndex` [`src/services/search_index.py:122–356`][CODE_VERIFIED]:
  - Dòng 21–69: Bảng alias chuyển động `ALIAS_MAP` (shake, zoom_in, fade_in, glitch...).
  - Dòng 89–96: `PREFIX_MAP` cho tiền tố tìm kiếm (`c:`, `t:`, `s:`, `m:`, `l:`, `f:`).
  - Dòng 172–244 (`set_assets`): Xử lý sơ bộ token, strip dấu tiếng Việt, trích xuất motion alias, gắn `_cat_norm`.
  - Dòng 246–296 (`search(query, limit_categories=None, limit=50)`): Tìm kiếm theo 7 tín hiệu chấm điểm (`_score_item`), sắp xếp score giảm dần, cắt theo `limit` (mặc định 50).

### 1.5. Tầng Giao diện Điều hướng Category Nav (Dock)
- `CategoryNavWidget` [`src/ui/lux/library/category_nav.py:43–368`][CODE_VERIFIED]:
  - Hàng 1 (Category): Các nút bấm danh mục chính (`cat_scroll`).
  - Hàng 2 (Subcategory): Các nút bấm nhóm con / tag (`sub_scroll`). Ẩn nếu $\le 1$ sub.
  - Dòng 133–150 (`set_categories`): Nhận danh sách categories, nếu `_current_cat` vẫn còn hợp lệ thì bảo toàn; render lại subcategories.
  - Dòng 222–240 (`select_category`): Cập nhật style, icon, đổi `_current_sub = "Tất cả"`, phát signal `category_changed`.

### 1.6. Tầng Vỏ Dock Shell (`EdgeDockWindow`)
- `_build_effective_categories(profile)` [`src/ui/lux/shell/edge_dock.py:431–556`][CODE_VERIFIED]:
  - Hợp nhất: profile categories base + B-roll (nếu có asset B-roll) + custom categories từ `StorageManager.taxonomy()` + custom categories từ assets.
  - Dòng 538–555: Tổng hợp `subs` cho từng category bằng cách duyệt qua `_all_assets`: nhặt `asset.sub` (hoặc `subcategory`) VÀ `asset.tags` đưa vào danh sách `definition["subs"]`.
- `set_assets(assets)` [`src/ui/lux/shell/edge_dock.py:558–614`][CODE_VERIFIED]:
  - Cập nhật model, palette, search_index, tái tạo category_nav qua `_build_effective_categories`.
  - Dòng 579–602: Bảo toàn chế độ `proxy_cat == "all"` khi đang tìm kiếm/lọc toàn cục.
  - Dòng 608–614: Bảo toàn `current_sub` nếu vẫn nằm trong tập `valid_subs` của category hiện tại, ngược lại reset về `"Tất cả"`.
- `_on_model_filter_changed()` [`src/ui/lux/shell/edge_dock.py:649–668`][CODE_VERIFIED]:
  - Dòng 657–667: Khi số lượng hiển thị thay đổi, kiểm tra nếu `_selected_asset` bị lọc mất khỏi grid hiển thị thì dọn sạch `_selected_asset = None`, đóng sheet và xóa khỏi `action_bar`.
- `_on_suggested_asset(asset_id)` [`src/ui/lux/shell/edge_dock.py:1227–1296`][CODE_VERIFIED]:
  - Dòng 1278: Cập nhật Inspector & Action Bar, **KHÔNG** auto-insert vào timeline.
  - Dòng 1284–1288: Tự động kích hoạt audio preview qua `hover_controller.preview_asset(ad)` khi asset là âm thanh.
  - Dòng 1253: Kiểm tra query qua `self.header.search_text` (lưu ý: `DockHeader.search_text` hiện là `@property str`, không phải method).

### 1.7. Tầng Kho Đạo Cụ Studio (`TabAssets`)
- `_sync_storage_rail(taxonomy)` [`src/ui/tabs/tab_assets.py:6126–6143`][CODE_VERIFIED]:
  - Tạo các nút rail bên trái cho các custom categories từ taxonomy và assets mà chưa có nút.
- `_rebuild_categories_column()` [`src/ui/tabs/tab_assets.py:4866–5175`][CODE_VERIFIED]:
  - Dựng cột nhóm con ở giữa.
  - Đối với tab `fav`: dựng nút `Tất cả yêu thích` và danh sách custom collections `coll:{cname}`.
  - Đối với các tab khác: dựng các nhóm con chuẩn + quét nạp `asset.sub` và `asset.tags` của managed assets + dựng các nút `pack:{p_name}` cho các gói cài đặt.
- `_refresh_grid()` [`src/ui/tabs/tab_assets.py:5267–5300`][CODE_VERIFIED]:
  - Cập nhật `asset_filter`: `set_category(tab)`, `set_sub_category(cat)`, `set_filter_mode(filter_mode)`, `set_search_query(query)`.
  - Dòng 5289–5295: Bỏ chọn `selected_asset` nếu bị lọc mất khỏi grid.

---

## 2. BẢNG ĐỐI CHIẾU CHUYỂN ĐỔI DỮ LIỆU (FIXTURE JSON TRACE)

### 2.1. Fixture 1: Canonical Templates (`text` / `trans`) và `overlays`
Dữ liệu đầu vào từ storage registry (`registry.json`):
```json
[
  {"id": "tpl_01", "name": "Title Neon", "category": "templates", "template_type": "text", "subcategory": "Neon", "source_kind": "managed", "size_bytes": 1024},
  {"id": "tpl_02", "name": "Zoom Transition", "category": "templates", "template_type": "trans", "subcategory": "Zoom", "source_kind": "managed", "size_bytes": 2048},
  {"id": "ovl_01", "name": "Film Grain 4K", "category": "overlays", "subcategory": "Grain", "source_kind": "managed", "size_bytes": 4096}
]
```

**Trace đường đi:**
1. **Qua DTO Adapter (`to_ui_asset`)**:
   - `tpl_01`: `category="templates", template_type="text"` $\to$ `tab="text"`, `category="text"`, `sub="Neon"`.
   - `tpl_02`: `category="templates", template_type="trans"` $\to$ `tab="trans"`, `category="trans"`, `sub="Zoom"`.
   - `ovl_01`: `category="overlays"` $\to$ `tab="overlay"`, `category="overlay"`, `sub="Grain"`.
2. **Qua Model (`AssetListModel` & `extract_asset_info`)**:
   - `tpl_01`: `CategoryRole = "text"`, `SubCategoryRole = "Neon"`, `KindRole = "Chữ"`.
   - `tpl_02`: `CategoryRole = "trans"`, `SubCategoryRole = "Zoom"`, `KindRole = "Chuyển cảnh"`.
   - `ovl_01`: `CategoryRole = "overlay"`, `SubCategoryRole = "Grain"`, `KindRole = "Lớp phủ"`.
3. **Hiển thị trên UI (Nav / Rail)**:
   - Dock: `CategoryNavWidget` đưa `tpl_01` vào chip danh mục `Chữ` (`text`), `tpl_02` vào `Chuyển cảnh` (`trans`), `ovl_01` vào `Lớp phủ` (`overlay`).
   - Studio: `TabAssets` đưa tương ứng vào các nút rail `text`, `trans`, `overlay`.
4. **Kết quả hiển thị trên Proxy (`AssetFilterProxyModel`)**:
   - Khi chọn tab `text` $\to$ hiển thị `tpl_01`, ẩn `tpl_02` và `ovl_01`.
   - Alias "chữ" hoặc "title" đều trỏ về canonical `"text"`, không bị nhân bản hay xung đột.

---

### 2.2. Fixture 2: Custom Categories Trùng Nhãn (Label) Nhưng Khác ID
Trường hợp người dùng có 2 dự án cùng đặt tên hiển thị là "Dự án", nhưng có ID kỹ thuật khác nhau:
- Taxonomy trong storage: `{"project_alpha": "Dự án", "project_beta": "Dự án"}`
- Dữ liệu assets:
```json
[
  {"id": "clip_a1", "name": "Cảnh quay A1", "category": "project_alpha", "subcategory": "Ngoại cảnh", "source_kind": "managed", "size_bytes": 5000},
  {"id": "clip_b1", "name": "Cảnh quay B1", "category": "project_beta", "subcategory": "Phỏng vấn", "source_kind": "managed", "size_bytes": 6000}
]
```

**Trace đường đi:**
1. **Qua DTO Adapter (`to_ui_asset`)**:
   - `clip_a1`: `category="project_alpha"` $\to$ `tab="project_alpha"`, `category="project_alpha"`.
   - `clip_b1`: `category="project_beta"` $\to$ `tab="project_beta"`, `category="project_beta"`.
2. **Qua Model (`AssetListModel`)**:
   - `CategoryRole`: `"project_alpha"` và `"project_beta"`.
3. **Hiển thị trên UI**:
   - Dock (`_build_effective_categories`): Lọc theo `existing_ids` (tập hợp `set[str]`), do `project_alpha != project_beta` nên tạo thành 2 chip danh mục riêng biệt:
     - Nút 1: `id="project_alpha"`, nhãn hiển thị `"Dự án"`
     - Nút 2: `id="project_beta"`, nhãn hiển thị `"Dự án"`
     - **KHÔNG BỊ GỘP** vì logic loại trùng căn cứ theo `id`, không theo nhãn hiển thị.
   - Studio (`_sync_storage_rail`): Tạo 2 nút rail riêng `self.rail_btns["project_alpha"]` và `self.rail_btns["project_beta"]`.
4. **Lọc trên Proxy (`AssetFilterProxyModel`)**:
   - `get_canonical_category("project_alpha")` trả về `"project_alpha"`.
   - `get_canonical_category("project_beta")` trả về `"project_beta"`.
   - Khi người dùng bấm vào nút Dự án thứ nhất (`project_alpha`), chỉ `clip_a1` hiển thị. `clip_b1` bị ẩn hoàn toàn.

---

### 2.3. Fixture 3: Custom Subcategory và Tags Tiếng Việt Có Dấu / Không Dấu
```json
[
  {"id": "vlog_01", "name": "Bún chả Hà Nội", "category": "broll", "subcategory": "Đời thường", "tags": ["Ẩm thực", "hà nội", "food"], "source_kind": "managed", "size_bytes": 10000}
]
```

**Trace đường đi:**
1. **Qua DTO Adapter (`to_ui_asset`)**:
   - `tab="broll"`, `sub="Đời thường"`, `tags=["Ẩm thực", "hà nội", "food"]`.
   - Bảo toàn 100% tiếng Việt nguyên bản (Unicode NFC).
2. **Qua Model (`AssetListModel`)**:
   - `SubCategoryRole = "Đời thường"`, `TagsRole = ["Ẩm thực", "hà nội", "food"]`.
3. **Hiển thị trên UI**:
   - Dock: Hàng 2 hiển thị các chip: `[ Tất cả ]`, `[ Đời thường ]`, `[ Ẩm thực ]`, `[ hà nội ]`, `[ food ]`.
   - Studio: Cột giữa liệt kê danh mục con `Đời thường` và các tag tương ứng.
4. **Lọc trên Proxy (`AssetFilterProxyModel.filterAcceptsRow`)**:
   - Người dùng bấm chip `[ Đời thường ]`: `target_sub = "đời thường"`. `sub_norm ("đời thường") == target_sub` $\to$ **ACCEPT**.
   - Người dùng bấm chip `[ Ẩm thực ]`: `target_sub = "ẩm thực"`. `target_sub in tags_norm` $\to$ **ACCEPT**.
   - Tìm kiếm không dấu qua ô search: Nhập `"ha noi"` $\to$ `SearchIndex` dùng `strip_accents` tokenize `"bun cha ha noi"` và `"am thuc ha noi food"`, match token score 60+ $\to$ **ACCEPT**.

---

### 2.4. Fixture 4: Gói Đa Danh Mục (Cùng 1 Pack Chứa Cả B-roll và SFX)
```json
[
  {"id": "p_asset_1", "name": "Drone Biển", "category": "broll", "pack_name": "Summer Pack", "source_kind": "managed", "size_bytes": 12000},
  {"id": "p_asset_2", "name": "Tiếng Sóng Biển", "category": "sfx", "pack_name": "Summer Pack", "source_kind": "managed", "size_bytes": 3000}
]
```

**Trace đường đi:**
1. **Qua Model**:
   - `p_asset_1`: `CategoryRole="broll"`, `PackNameRole="Summer Pack"`.
   - `p_asset_2`: `CategoryRole="sfx"`, `PackNameRole="Summer Pack"`.
2. **Hiển thị và Lọc**:
   - Trong tab `broll`: Chỉ `p_asset_1` hiển thị. Nút pack `pack:Summer Pack` trong Studio chỉ lọc ra `p_asset_1`.
   - Trong tab `sfx`: Chỉ `p_asset_2` hiển thị. Nút pack `pack:Summer Pack` trong Studio chỉ lọc ra `p_asset_2`.
   - Chế độ tìm kiếm toàn cục (`category="all"`): Tìm `"Summer Pack"` hiển thị cả 2 asset thuộc cả 2 danh mục cạnh nhau.

---

### 2.5. Fixture 5: Favorites, Recent và Custom Collections
```json
[
  {"id": "fav_01", "name": "Gõ chữ Đánh máy", "category": "text", "source_kind": "builtin", "size_bytes": 500}
]
```
- Khi `fav_01` được thêm vào Favorites: `self._favs.add("fav_01")`.
- Khi `fav_01` được thêm vào collection `Vlog Project`: `CollectionsManager.add_asset_to_collection("Vlog Project", "fav_01")`.
- **Lọc trên Proxy**:
  - Khi `_category == "fav"` và `_sub_category == "all"`: Kiểm tra `is_fav == True` $\to$ hiển thị.
  - Khi `_sub_category == "coll:Vlog Project"`: Đọc danh sách ID trong collection qua `CollectionsManager.get_collection_assets("Vlog Project")`, kiểm tra `fav_01 in coll_assets` $\to$ hiển thị.

---

## 3. BẢNG CHÂN TRỊ ĐA TẦNG VÀ QUY TẮC RESET BỘ LỌC

### 3.1. Công thức Logic Tổng Quát
Hàng được chấp nhận hiển thị (`filterAcceptsRow == True`) khi và chỉ khi thỏa mãn đồng thời:
$$\text{ACCEPT} = C \land S \land T \land P \land Q \land M$$

Trong đó:
1. **$C$ (Category Match):**
   - Nếu `_category in ("all", "fav", "recent")`: $C = \text{True}$.
   - Ngược lại: $C = \big(\text{get\_canonical\_category}(\text{\_category}) == \text{get\_canonical\_category}(\text{asset.category})\big)$.
2. **$S$ (Subcategory Match):**
   - Nếu `_sub_category in ("all", "Tất cả", "fav:all")`: $S = \text{True}$.
   - Nếu `_sub_category.startswith("pack:")`: $S = \big(\text{norm}(\text{asset.pack}) == \text{norm}(\text{\_sub\_category}[5:])\big)$.
   - Ngược lại: $S = \big(\text{norm}(\text{asset.sub}) == \text{norm}(\text{\_sub\_category}) \lor \text{norm}(\text{\_sub\_category}) \in \text{asset.tags\_norm}\big)$.
3. **$T$ (Tag Match):**
   - Nếu `_tag is None`: $T = \text{True}$.
   - Ngược lại: $T = \big(\text{\_tag} \in \text{asset.tags\_norm} \lor \text{\_tag} == \text{norm}(\text{asset.sub})\big)$.
4. **$P$ (Explicit Pack Match):**
   - Nếu `_pack_filter is None`: $P = \text{True}$.
   - Ngược lại: $P = \big(\text{norm}(\text{asset.pack}) == \text{\_pack\_filter}\big)$.
5. **$Q$ (Search Query Match):**
   - Nếu `_search_query == ""`: $Q = \text{True}$.
   - Ngược lại: $Q = \big(\text{asset.id} \in \text{\_matched\_ids}\big)$ (hoặc fallback `query.lower() in asset.name.lower()`).
6. **$M$ (Mode Match):**
   - Nếu `_filter_mode == "all"`: $M = \text{True}$.
   - Nếu `_filter_mode == "fav"` hoặc `_category == "fav"`:
     - Nếu `_sub_category.startswith("coll:")`: $M = \big(\text{asset.id} \in \text{collection\_ids}\big)$.
     - Ngược lại: $M = \text{asset.is\_fav}$.
   - Nếu `_filter_mode == "recent"`: $M = \big(\text{asset.id} \in \text{\_recent\_ids}\big)$.

### 3.2. Bảng Chân Trị Chi Tiết
| Điều kiện $C$ | Điều kiện $S$ | Điều kiện $T$ | Điều kiện $P$ | Điều kiện $Q$ | Điều kiện $M$ | Kết quả `filterAcceptsRow` | Ghi chú hành vi |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---|
| **True** | **True** | **True** | **True** | **True** | **True** | **TRUE** (Hiển thị) | Khớp toàn bộ các tiêu chí đang chọn |
| **False** | *bất kỳ* | *bất kỳ* | *bất kỳ* | *bất kỳ* | *bất kỳ* | **FALSE** | Khác category/tab chính |
| **True** | **False** | *bất kỳ* | *bất kỳ* | *bất kỳ* | *bất kỳ* | **FALSE** | Không khớp nhóm con hoặc tag từ chip |
| **True** | **True** | **False** | *bất kỳ* | *bất kỳ* | *bất kỳ* | **FALSE** | Không khớp tag explicit |
| **True** | **True** | **True** | **False** | *bất kỳ* | *bất kỳ* | **FALSE** | Không thuộc pack chỉ định |
| **True** | **True** | **True** | **True** | **False** | *bất kỳ* | **FALSE** | Bị loại bởi Search Query |
| **True** | **True** | **True** | **True** | **True** | **False** | **FALSE** | Không thuộc Fav/Recent/Collection |

### 3.3. Quy Tắc Reset Có Mục Tiêu (Targeted Reset Rules)
1. **Khi người dùng đổi Category (Chuyển Tab chính):**
   - `_category` cập nhật sang ID mới.
   - `_sub_category` tự động reset về `"all"`.
   - `_tag` tự động reset về `None`.
   - `_pack_filter` tự động reset về `None`.
   - **BẢO TOÀN `_search_query`**: Truy vấn tìm kiếm của người dùng không bị xóa, cho phép tiếp tục tìm từ khóa đó trong danh mục mới.
2. **Khi người dùng chọn lại `Tất cả` / `all` trên thanh Subcategory:**
   - `_sub_category` đặt về `"all"`.
   - `_tag` được xóa về `None`.
   - `_pack_filter` được xóa về `None`.
   - `_search_query` được bảo toàn.
3. **Khi Background Snapshot cập nhật (`apply_storage_snapshot` hoặc `dock.set_assets`):**
   - Nếu `_category` hiện tại vẫn tồn tại trong danh mục hợp lệ: **GIỮ NGUYÊN `_category`**.
   - Nếu `_sub_category` hiện tại vẫn còn hợp lệ trong tập subs của category đó: **GIỮ NGUYÊN `_sub_category`**.
   - Chỉ reset `_sub_category = "all"` khi và chỉ khi subcategory cũ đã bị xóa khỏi kho / không còn asset nào thuộc sub đó.
   - Tuyệt đối không xóa vô điều kiện `_search_query` hay reset về tab đầu tiên khi nạp dữ liệu nền.

---

## 4. CƠ CHẾ SEARCH INVALIDATION & ĐỒNG BỘ HÓA

### 4.1. Vòng Đời Cập Nhật Chỉ Mục (Lifecycle Invalidation)
Khi kho lưu trữ có biến động (thêm pack, xóa asset, đổi tên, offline kho, chuyển Storage Root):
1. `StorageManager().ui_assets()` sinh danh sách asset snapshot mới.
2. `EdgeDockWindow.set_assets(new_assets)` hoặc `TabAssets.apply_storage_snapshot(new_assets)` được kích hoạt.
3. **Cập nhật `SearchIndex`:** Gọi `search_index.set_assets(new_assets)`:
   - Làm rỗng `_assets` trong RAM.
   - Tiền xử lý lại toàn bộ token, loại bỏ dấu tiếng Việt, trích xuất alias chuyển động cho từng asset.
4. **Cập nhật `AssetListModel`:** Gọi `asset_model.set_assets(new_assets)`:
   - Phát tín hiệu `beginResetModel()`.
   - Cập nhật lại danh sách metadata và bảng băm `_id_to_row`.
   - Phát tín hiệu `endResetModel()`.
5. **Cập nhật `AssetFilterProxyModel`:**
   - Nếu có `_search_query` đang hoạt động: Gọi ngay `_apply_search()`.
   - Chạy `search_index.search(self._search_query)` trên tập asset mới để cập nhật lại `_matched_ids` và `_search_scores`.
   - Gọi `self.invalidate()` và sắp xếp lại theo score.

### 4.2. Khắc Phục Lỗi Giới Hạn Tìm Kiếm (Search Limit Truncation)
- **Hiện trạng [CODE_VERIFIED]:**
  - Tại `src/services/search_index.py:250`, hàm `search` có tham số mặc định: `limit: int = 50`.
  - Tại `src/ui/lux/library/asset_filter.py:167`, hàm `_apply_search()` gọi:
    ```python
    results = self._search_index.search(self._search_query)
    ```
    (Không truyền `limit`, nhận giá trị mặc định là 50).
- **Hậu quả:** Khi một từ khóa tìm kiếm khớp với hơn 50 tài nguyên trong kho, `_matched_ids` chỉ ghi nhận đúng 50 ID đầu bảng. Toàn bộ các tài nguyên từ thứ 51 trở đi bị `filterAcceptsRow` loại bỏ hoàn toàn (`if asset_id not in self._matched_ids: return False`), khiến người dùng cuộn lưới không thấy hết kết quả hợp lệ!
- **Giải pháp đề xuất [PROPOSED]:**
  - Cho phép `SearchIndex.search` nhận `limit=0` hoặc `limit=None` để trả về toàn bộ kết quả khớp khi được gọi từ Proxy Model.
  - Trong `AssetFilterProxyModel._apply_search()`, truyền `limit=0` (hoặc `limit=None`) để nạp đủ 100% ID khớp vào `_matched_ids`.

### 4.3. Loại Bỏ Vòng Lặp Tuyến Tính Khi Kiểm Tra Lựa Chọn (Linear Scan Elimination)
- **Hiện trạng [CODE_VERIFIED]:**
  - Tại `src/ui/lux/shell/edge_dock.py:661`:
    ```python
    still_visible = any(self.proxy_model.index(r, 0).data(IdRole) == sel_id for r in range(count))
    ```
  - Tại `src/ui/tabs/tab_assets.py:5292`:
    ```python
    still_visible = any(self.asset_filter.index(r, 0).data(IdRole) == sel_id for r in range(count))
    ```
  - Cả hai vị trí trên đều duyệt tuyến tính $O(N)$ qua từng hàng của proxy model và gọi hàm Qt `index(r, 0).data(IdRole)`. Với danh sách hàng ngàn asset, điều này gây giật lag giao diện ở mỗi lần gõ phím tìm kiếm.
- **Giải pháp đề xuất [PROPOSED]:**
  - Tận dụng phương thức tra cứu bảng băm $O(1)$ đã có sẵn trên `AssetListModel`: `index_of_asset_id(sel_id)`.
  - Dùng ánh xạ chuẩn của Qt QSortFilterProxyModel: `proxy_idx = self.proxy_model.mapFromSource(src_idx)`.
  - Xác định hiển thị trong $O(1)$:
    ```python
    src_idx = self.asset_model.index_of_asset_id(sel_id)
    still_visible = False
    if src_idx.isValid():
        proxy_idx = self.proxy_model.mapFromSource(src_idx)
        still_visible = proxy_idx.isValid() and proxy_idx.row() >= 0
    ```
  - *(Cách làm này đã được áp dụng thành công và xác minh tại `TabAssets.apply_storage_snapshot` dòng 6226–6229).*

### 4.4. Quy Tắc Không Kích Hoạt Preview / Insert Tự Động
- **Hiện trạng [CODE_VERIFIED]:**
  - Tại `src/ui/lux/shell/edge_dock.py:1286–1288`, trong handler `_on_suggested_asset(self, asset_id: str)`:
    ```python
    if cat_lower in ("sfx", "audio", "bgm", "sound") ...:
        self.grid.hover_controller.preview_asset(ad)
    ```
- **Rủi ro:** Khi người dùng click gợi ý đề xuất ngữ cảnh, âm thanh tự động phát tiếng làm giật mình hoặc phát lặp không mong muốn.
- **Quyết định của Tech Lead Codex [CODEX_DECISION]:**
  - Chốt đề xuất UX: suggestion là locate/select/openInspector thụ động, không tự insert/play.
  - Tuy nhiên thay đổi này được **TÁCH SANG 04C** để test preview riêng biệt. Không sửa trong lát cắt 04B1-I.

---

## 5. DANH SÁCH ĐIỂM LỆCH KỸ THUẬT (DEFECTS & GAPS) KÈM BẰNG CHỨNG CODE

| STT | Mức độ | Hiện tượng & Bằng chứng Code | Ảnh hưởng thực tế | Quyết định xử lý |
|:---:|:---:|---|---|---|
| **1** | **P1** | **Cắt cụt kết quả tìm kiếm ở 50 phần tử:**<br/>`SearchIndex.search` đặt `limit=50` (`src/services/search_index.py:250`).<br/>`AssetFilterProxyModel._apply_search()` gọi không truyền limit (`src/ui/lux/library/asset_filter.py:167`). | Khi thư viện lớn, tìm kiếm bị mất kết quả từ phần tử thứ 51 trở đi, cuộn lưới ảo hóa không đủ; top 50 trước facets có thể làm category/pack rỗng giả. | **TRIỂN KHAI TẠI G04-04B1-I**: Cho phép `limit=0` hoặc `limit=None` để trả đủ kết quả cho proxy model. |
| **2** | **UX** | **Tự động phát preview âm thanh khi click suggestion:**<br/>`_on_suggested_asset` gọi `hover_controller.preview_asset(ad)` (`src/ui/lux/shell/edge_dock.py:1286–1288`). | Vi phạm nguyên tắc giao diện thụ động; gây phát âm thanh bất ngờ khi người dùng chỉ bấm xem gợi ý. | **TRÌ HOÃN SANG 04C**: Kiểm thử preview riêng rẽ. |
| **3** | **P2** | **Duyệt tuyến tính $O(N)$ kiểm tra selection:**<br/>`any(self.proxy_model.index(r, 0).data(...) == sel_id for r in range(count))` tại `edge_dock.py:661` và `tab_assets.py:5292`. | Tiêu tốn CPU không cần thiết ở mỗi nhịp gõ phím tìm kiếm khi thư viện có hàng nghìn asset. | **TRÌ HOÃN**: Tách optimization ra sau nếu cần; mapFromSource chỉ bỏ own loop, không bảo đảm toàn filter O(1). |
| **4** | **P2** | **Thiếu hiển thị pack filter trên thanh Dock:**<br/>`TabAssets` có nút lọc `pack:{p_name}`, nhưng `CategoryNavWidget` trên Dock chỉ tổng hợp `subs` và `tags`, chưa có cơ chế chip cho `pack_name`. | Người dùng trên Dock không thể lọc riêng theo từng Pack nếu pack đó không có subcategory riêng. | **TRÌ HOÃN SANG 04C** hoặc giai đoạn UX tiếp theo. |

---

## 6. LÁT CẮT THỰC THI DUY NHẤT: G04-04B1-I

Theo phê duyệt của Codex Tech Lead trong `docs/architecture/04B-codex-decisions.md`:

### **Lát cắt: `G04-04B1-I — Kết quả search đầy đủ trước lọc facets`**
- **Phạm vi mã nguồn production:** Bó hẹp chuẩn xác trong **đúng 2 modules**:
  1. [`src/services/search_index.py`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/src/services/search_index.py):
     - Sửa `SearchIndex.search`: nếu `limit is None` hoặc `limit == 0`, không cắt danh sách kết quả (trả về toàn bộ `results`). Negative limits (`limit < 0`) giữ nguyên ngữ nghĩa slicing Python `results[:limit]`.
     - Giữ nguyên mặc định `limit=50` và positive limits cho callers khác (CommandPalette, popup search) với stable score/rank/tie order.
  2. [`src/ui/lux/library/asset_filter.py`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/src/ui/lux/library/asset_filter.py):
     - Sửa `_apply_search()`: gọi `self._search_index.search(self._search_query, limit=0)` để nạp toàn bộ các asset khớp vào `_matched_ids` và `_search_scores`.
- **Ràng buộc:**
  - Tuyệt đối không chạm vào `edge_dock.py`, `tab_assets.py`, `studio_window.py`, `storage_manager.py`.
  - Suggestion audio playback và selection scan optimization được DEFERRED.

---

## 7. QUYẾT ĐỊNH CHÍNH SÁCH ĐÃ CHỐT VỚI TECH LEAD CODEX

1. **Vấn đề Search Limit cho Virtual Grid:**
   - *Quyết định:* Cho phép `limit=0` trả đầy đủ tập matches đã tính/sắp score; giữ default 50 và positive limits cho callers hiện tại. Proxy gọi explicit `limit=0`. Không thêm trần 1000.
2. **Xử lý âm thanh khi click Suggestion:**
   - *Quyết định:* Chốt đề xuất UX suggestion là thụ động, nhưng tách sang 04C để kiểm thử riêng.

---

## 8. BẰNG CHỨNG THỰC THI & KIỂM THỬ (EVIDENCE)

### 8.1. Bằng chứng mã nguồn (Code Changes)
1. `src/services/search_index.py`:
   ```python
   # Dòng 296–298
   if limit is None or limit == 0:
       return results
   return results[:limit]
   ```
2. `src/ui/lux/library/asset_filter.py`:
   ```python
   # Dòng 167
   results = self._search_index.search(self._search_query, limit=0)
   ```

### 8.2. Bằng chứng TDD (Test Evidence)
- **Unit Test:** `tests/services/test_search_index.py`:
  - `test_search_limit_default_positive_and_unlimited`: Kiểm thử 125 assets khớp từ khóa `"broll"`. Mặc định trả đúng 50, `limit=10` trả đúng 10, `limit=0` và `limit=None` trả đủ 125 với score/tie order nhất quán.
- **Integration Test:** `tests/ui/test_asset_search_completeness.py`:
  - `test_search_completeness_beyond_top_50`: Khẳng định cả 126 assets khớp hiển thị đầy đủ trên proxy (không bị cắt ở 50).
  - `test_search_with_category_and_pack_facets_outside_top_50`: Khẳng định asset nằm ngoài top 50 (hạng 51+) khi lọc theo category/pack không bị empty state giả.
  - `test_search_query_cleared_restores_natural_order`: Khẳng định khi xóa query thì proxy khôi phục đúng thứ tự ban đầu.
  - `test_search_dynamic_updates_under_active_query`: Khẳng định nạp index mới khi đang có query active thì ID mới/sửa/xóa được phản ánh chính xác không bị stale.

### 8.3. Kết quả chạy bộ kiểm thử quy định
```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_search_index.py tests/ui/test_asset_search_completeness.py tests/test_asset_filter_packs_and_collections.py tests/ui/test_studio_storage_snapshot.py -q
```
**Kết quả:** `34 passed in 18.04s` (Exit code 0).
