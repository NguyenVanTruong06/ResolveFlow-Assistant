# G06-06B-D: Thiết Kế Khảo Sát Tra Cứu & Khớp Tài Nguyên Ngoại Tuyến (Offline Asset Matching Design)

> HIỆN HÀNH 2026-10-29: 06B1 ACCEPTED_WITH_RECORDED_LIMITS; authoritative acceptance06B1-gate-acceptance.md. Next06B2 per06B2-codex-decisions.md.

> **Mã công việc:** `G06-06B-D` / `G06-06B1-I`  
> **Cấp độ thực hiện:** Worker `HIGH`  
> **Trạng thái:** `IMPLEMENTED_AND_VERIFIED` (Lát cắt `G06-06B1-I` đã hoàn thành và kiểm thử 67/67 tests xanh trong finite suite).  
> **Tài liệu tham chiếu:** `docs/architecture/06B-codex-decisions.md`, `src/core/contracts/asset_matching_contracts.py`, `src/core/offline_asset_matcher.py`, `src/core/contracts/asset_dto.py`.

---

## 1. Callgraph Thực Tế Codebase (`file:hàm:dòng`)

Khảo sát toàn diện hệ thống quản lý và sử dụng tài nguyên (assets) hiện hữu trong codebase ChunDVC để phân định ranh giới giữa kho media đa tầng, chỉ mục tìm kiếm và các consumer AI/UI.

### 1.1. Storage Core & Quản lý Kho Đa Tầng (`[CODE_VERIFIED]`)
- **`src/core/storage_manager.py:371` — `StorageManager.assets(self) -> List[Dict[str, Any]]`**  
  *Trạng thái:* `[CODE_VERIFIED]`.  
  *Chức năng:* Trả về danh sách asset dictionary đầy đủ metadata từ registry (`meta/registry.json` khi online) hoặc snapshot (`snapshot-{lib_id}.json` khi offline). Tự động tính toán `file_path`, xác định `availability` ("online" | "offline"), phân giải companion paths (`preview_path`, `thumbnail_path`) thuộc sở hữu của pack, suy luận `source_kind` ("managed" | "linked").
- **`src/core/storage_manager.py:512` — `StorageManager.ui_assets(self) -> List[UIAssetView]`**  
  *Trạng thái:* `[CODE_VERIFIED]`.  
  *Chức năng:* Chuyển đổi toàn bộ output từ `assets()` sang view runtime `UIAssetView` qua adapter `to_ui_asset()` trong `asset_dto.py`.
- **`src/core/storage_manager.py:357` — `StorageManager.taxonomy(self) -> Dict[str, str]`**  
  *Trạng thái:* `[CODE_VERIFIED]`.  
  *Chức năng:* Trả về mapping danh mục hiện hành (mặc định 7 danh mục chuẩn: `broll`, `sfx`, `bgm`, `overlays`, `memes`, `luts`, `templates` cộng thêm các danh mục custom hợp lệ).
- **`src/core/storage_manager.py:333` — `StorageManager.registry(self) -> Dict[str, Any]`**  
  *Trạng thái:* `[CODE_VERIFIED]`.  
  *Chức năng:* Đọc atomic `registry.json` chứa `packs` và `assets`. Bắt buộc kho phải `online` (`require_online()`).
- **`src/core/contracts/asset_dto.py:24` — `CANONICAL_STORAGE_CATEGORIES: Set[str]`**  
  *Trạng thái:* `[CODE_VERIFIED]`.  
  *Chức năng:* Tập hợp 7 danh mục chuẩn hóa: `{"broll", "sfx", "bgm", "overlays", "memes", "luts", "templates"}`.
- **`src/core/contracts/asset_dto.py:93` — `class PersistedAssetRecord(TypedDict)`**  
  *Trạng thái:* `[CODE_VERIFIED]`.  
  *Chức năng:* Schema bền vững nghiêm ngặt của asset trong `registry.json`. Bắt buộc `schema_version=1`, `id`, `name`, `source_kind`, `category`, `size_bytes`.

### 1.2. Thẩm Quyền Tại Thời Điểm Thao Tác (`[CODE_VERIFIED]`)
- **`src/core/action_guard.py:202` — `check_action_availability(asset, intent="insert", ...) -> ActionGuardResult`**  
  *Trạng thái:* `[CODE_VERIFIED]`.  
  *Chức năng:* Cổng thẩm định an toàn trước khi chèn vào timeline Resolve hoặc xuất file. Thẩm định realpath, symlink traversal, kiểm tra `availability` ("online" | "offline" | "missing" | "staging"), kiểm tra cấm file staging và gắn nhãn `is_native_guidance` cho transition native của DaVinci Resolve.

### 1.3. Chỉ Mục Tìm Kiếm Inverted Memory (`[CODE_VERIFIED]`)
- **`src/services/search_index.py:122` — `class SearchIndex`**  
  *Trạng thái:* `[CODE_VERIFIED]`.  
  *Chức năng:* Chỉ mục tìm kiếm đảo ngược (in-memory inverted index), xử lý <16ms cho hàng ngàn assets.  
  *Các hàm then chốt:*
  + `search_index.py:99`: `strip_accents(text: str) -> str` (Chuẩn hóa bỏ dấu tiếng Việt sang ASCII không dấu).
  + `search_index.py:23`: `ALIAS_MAP: Dict[str, List[str]]` (Mapping từ khóa chuyển động/cảm xúc Anh - Việt: `shake`, `zoom_in`, `fade_in`, `glitch`, `bounce`, `rapid_cuts`,...).
  + `search_index.py:89`: `PREFIX_MAP: Dict[str, Set[str]]` (Prefix lọc nhanh: `c:`, `t:`, `s:`, `m:`, `l:`, `f:`).
  + `search_index.py:230`: `search(self, query: str, category: Optional[str] = None, limit: int = 50) -> List[Tuple[Dict[str, Any], float]]` (Chấm điểm 7 tín hiệu: exact title +40, word match +30, tag +20, alias +15, subsequence +10, frecency +5..+15, page boost +25).

### 1.4. Các Consumer & Codebase Cũ Cần Được Nhận Diện Rõ (`[CODE_VERIFIED]`)
- **`src/core/broll_sfx.py:144` — `class GlobalAssetPool`**  
  *Trạng thái:* `[CODE_VERIFIED — LEGACY]`.  
  *Thực tế:* Sử dụng `os.walk` quét thư mục đĩa `./assets` dựa vào basename (`f.lower()`). Không biết đến `StorageManager`, không dùng canonical asset ID, bypass hoàn toàn catalog registry và action guard.  
  *Đánh giá:* **KHÔNG ĐƯỢC DÙNG** `GlobalAssetPool` làm nền tảng cho 06B.
- **`src/core/story_copilot.py:40` — `class CopilotMediaInsert(BaseModel)`**  
  *Trạng thái:* `[CODE_VERIFIED — UNIFIED BOUNDARY]`.  
  *Thực tế:* Chứa trường `asset_file: str` (tên file thô như `do_mixi_ao_that_day.mp4`).  
  *Đánh giá:* Cần adapter ánh xạ giữa `asset_file` thô (từ AI prompt) sang canonical `asset_id` bền vững từ `StorageManager`.
- **`src/core/asset_indexer.py:9` — `class AssetIndexer`**  
  *Trạng thái:* `[CODE_VERIFIED — LEGACY]`.  
  *Thực tế:* Quét trực tiếp thư mục `assets/` qua QSettings và singleton cache. Không đồng bộ với `StorageManager`.
- **`src/ui/tabs/tab_assets.py:4395` — `TabAssets` Integration**  
  *Trạng thái:* `[CODE_VERIFIED]`.  
  *Thực tế:* Khởi tạo `SearchIndex(self.all_assets)` trực tiếp trên dữ liệu từ `StorageManager.ui_assets()`.

---

## 2. Danh Mục Chuẩn Hóa & Metadata Manifest

### 2.1. Danh Mục Chuẩn Hóa (Canonical Categories)
Hệ thống khớp tài nguyên ngoại tuyến 06B tuân thủ tuyệt đối định nghĩa danh mục trong `src/core/contracts/asset_dto.py`:
1. `broll`: Cảnh quay minh họa (B-Roll footage thực tế).
2. `sfx`: Hiệu ứng âm thanh ngắn (Sound Effects: whoosh, pop, ding, transition hit).
3. `bgm`: Nhạc nền (Background Music loop/track).
4. `overlays`: Lớp phủ đồ họa (Vignette, Film Grain, Letterbox, Camera Frame, Staging graphics).
5. `memes`: Video meme hài hước, phản ứng nhân vật, green screen ngắn.
6. `luts`: Bảng tra cứu màu sắc (`.cube` files cho DaVinci Resolve color grading).
7. `templates`: Mẫu Fusion (`.setting` cho Titles, Lower Thirds, Transitions, Generators).
8. `custom_id`: Các danh mục tùy biến do người dùng tạo qua `StorageManager.add_category`, thỏa mãn regex `^[a-z][a-z0-9_]{0,47}$` và không nằm trong `RESERVED_CATEGORY_IDS`.

### 2.2. Phân Định Rõ Ràng Ranh Giới Dữ Liệu
- **Project Source Footage vs Asset Catalog:**  
  *Source Footage* thuộc sở hữu của dự án (người dùng quay, nằm trong `ClipManifest.clips`), có timestamp, frame bounds và audio transcription.  
  *Asset Catalog* là kho tài nguyên tái sử dụng (B-roll stock, SFX, Meme), được định danh bởi `asset_id` toàn cục trong `StorageManager.registry()`.  
  *Nguyên tắc:* Matcher 06B chỉ tra cứu trong Asset Catalog, tuyệt đối không xâm lấn hoặc sửa đổi cấu trúc Source Footage của project.
- **Master File vs Preview Companion:**  
  *Master File:* Đường dẫn file gốc (`library_path` hoặc `external_path`), chỉ file này mới có giá trị để dựng hình tại DaVinci Resolve timeline.  
  *Preview Companion:* `preview_relative_path` (clip MP4 nén nhẹ xem trước) và `thumbnail_relative_path` (ảnh thumbnail).  
  *Nguyên tắc:* Matcher 06B **tuyệt đối không** đưa `preview_path` vào plan như một clip B-roll master. Preview chỉ dùng để hiển thị trên UI.
- **Native Transition Guidance vs Insertable Media:**  
  Các hiệu ứng chuyển cảnh native của Resolve (như `Cross Dissolve`, `Dip to Color`) không phải là file media độc lập. Khi match ra các preset này, matcher phải đánh dấu `is_native_guidance=True`, không sinh lệnh chèn media file.
- **Preset JSON vs Media File:**  
  Preset JSON cấu hình kịch bản (Title styling, subtitle animations) không phải là file media nhúng trực tiếp vào track video.

---

## 3. Bản Hợp Đồng Khớp Tài Nguyên Thuần Khiết (Proposed Pure Matching Contracts)

Toàn bộ các dataclasses dưới đây là **Pure Python 3.12 Standard Library** (không phụ thuộc PySide6, ResolveGateway, Network, hay DB bên ngoài).

### 3.1. Yêu Cầu Tra Cứu (`OfflineAssetMatchRequest`)
```python
@dataclass(frozen=True)
class OfflineAssetMatchRequest:
    """Yêu cầu tra cứu tài nguyên từ AI Rough-Cut Planner hoặc Director State."""
    request_id: str                          # Mã yêu cầu duy nhất (prep_xxx)
    category: str                            # Danh mục chuẩn hoặc alias UI (overlay/meme/lut/text/trans)
    query_text: str                          # Từ khóa tìm kiếm hoặc nhãn mô tả
    scene_context: str = ""                  # Ngữ cảnh phân đoạn (nếu có)
    emotion_or_mood: str = ""                # Cảm xúc gợi ý (nếu có)
    template_type: Optional[str] = None      # "text" | "trans" (chỉ áp dụng cho templates)
    subcategory_constraint: Optional[str] = None # Giới hạn danh mục con (VD: "whoosh", "cinematic")
    min_duration_sec: Optional[float] = None # Thời lượng tối thiểu (nếu có)
    max_duration_sec: Optional[float] = None # Thời lượng tối đa (nếu có)
    top_k: int = 5                           # Giới hạn số lượng ứng viên tối đa trả về (bounded 1..20)
    allow_offline: bool = False              # Cho phép trả về asset đang offline (kèm requires_reconnect)
```

### 3.2. Ứng Viên Khớp Được (`OfflineAssetCandidate`)
```python
@dataclass(frozen=True)
class OfflineAssetCandidate:
    """Một ứng viên tài nguyên được chấm điểm và xếp hạng từ kho thật."""
    asset_id: str                            # Canonical ID duy nhất trong StorageManager (VD: "ast_mixi_ao_that")
    name: str                                # Tên hiển thị chuẩn của asset
    category: str                            # Danh mục chuẩn hóa (broll, sfx, memes,...)
    subcategory: str                         # Phân loại con (nếu có)
    source_kind: str                         # "managed" | "linked" | "builtin" | "generated"
    availability: str                        # "online" | "offline"
    match_score: int                         # Điểm số phù hợp từ 0 đến 100 (deterministic)
    matched_signals: Tuple[str, ...]         # Các tín hiệu khớp: ("exact_name", "name_token", "tag_token",...)
    duration_sec: Optional[float]            # Thời lượng của asset (giây)
    pack_id: Optional[str]                   # ID của pack chứa asset
    pack_name: str                           # Tên hiển thị của pack
    source_id: Optional[str] = None          # ID nguồn liên kết (với linked)
    origin_library_id: Optional[str] = None  # ID kho gốc
    template_type: Optional[str] = None      # "text" | "trans" (cho templates)
    is_native_guidance: bool = False         # True nếu là transition native của DaVinci Resolve
    requires_reconnect: bool = False         # True nếu asset đang offline cần kết nối lại
```
*Lưu ý an toàn:* Output của Candidate tuyệt đối **không chứa** `file_path`, `preview_path` hoặc executable flags.

### 3.3. Kết Quả Tra Cứu (`OfflineAssetMatchResult`)
```python
@dataclass(frozen=True)
class OfflineAssetMatchResult:
    """Kết quả hoàn chỉnh của một lượt tra cứu tài nguyên ngoại tuyến."""
    request_id: str                          # Mã yêu cầu đối chiếu
    category: str                            # Danh mục chuẩn hóa đã được resolve
    query_text: str                          # Truy vấn thô ban đầu
    candidates: Tuple[OfflineAssetCandidate, ...] # Danh sách ứng viên đã xếp hạng (top_k bounded)
    is_matched: bool                         # True nếu có ít nhất 1 ứng viên hợp lệ
    unmatched_reason: Optional[str] = None   # Mã lý do: ERR_CATALOG_EMPTY, ERR_CATALOG_INVALID, ERR_ASSETS_OFFLINE, ERR_NO_MATCH_FOUND
    suggested_action: Optional[str] = None   # Gợi ý cho người dùng tiếng Việt
    warnings: Tuple[str, ...] = ()           # Cảnh báo các bản ghi bị cách ly trong kho
    rejected_record_count: int = 0           # Số lượng bản ghi bị loại bỏ trong kho
```

### 3.4. Thuật Toán Chấm Điểm & Tie-Break Quyết Định (Deterministic Scoring)
1. **Lọc Cứng (Hard Eligibility Filters):**
   - Phải là bản ghi hợp lệ theo `validate_asset_dict` và có tham chiếu master hợp lệ.
   - Danh mục phải khớp `category` đã được resolve (chuyển đổi UI aliases: overlay->overlays, meme->memes, lut->luts, text/trans->templates).
   - Nếu `template_type` được chỉ định, bản ghi template phải khớp chính xác `template_type`.
   - Nếu có `subcategory_constraint`, so sánh chính xác sau chuẩn hóa không dấu.
   - **Ràng buộc thời lượng (Decision 5):** Khi có `min_duration_sec` hoặc `max_duration_sec`, các bản ghi có thời lượng chưa biết (`duration_sec=None`) **BẮT BUỘC BỊ LOẠI BỎ**. Khi không có min/max, bản ghi có `duration_sec=None` được giữ lại.
   - **Ràng buộc khả dụng (Decision 3):** Bản ghi `staging` hoặc `missing` luôn bị loại bỏ. Bản ghi `offline` mặc định bị loại trừ; khi `allow_offline=True`, bản ghi offline được giữ và gắn cờ `requires_reconnect=True`.
2. **Chấm Điểm Mềm (Soft Ranking Signals):**
   - Khớp chính xác truy vấn với name hoặc stem hoặc asset_id (+40, tối đa 1 lần).
   - Khớp token trong name (+25, tối đa 1 lần).
   - Khớp token trong tags (+15 mỗi token query/context duy nhất xuất hiện trong tags, tối đa +30).
   - Khớp qua `alias_groups` được cấu hình (+15, tối đa 1 lần).
   - Khớp token trong subcategory (+10, tối đa 1 lần).
   - Tổng điểm được clamp tối đa 100. Ngưỡng tối thiểu để trở thành ứng viên là **15 điểm** (loại bỏ mọi điểm < 15).
3. **Quy Tắc Phân Xử Hòa Điểm (Tie-Breaking Rule — Decision 4, 10):**
   Tuyệt đối **không cộng điểm** hoặc ưu tiên ngầm cho `managed` so với `linked`. Thứ tự sắp xếp được xác định tất định 100%:
   $$\text{Order} = (\text{match\_score DESC}, \text{availability == 'offline' (Online trước)}, \text{normalized\_name ASC}, \text{raw\_asset\_id ASC})$$

---

## 4. Tính Khả Dụng & Ranh Giới Thẩm Quyền (Availability & Authority Boundaries)

### 4.1. Phân Biệt Giữa Advisory Match và Action-Time Authority
- **G06B (Advisory Offline Matcher):**  
  Matcher hoạt động như một cố vấn thông minh trong quá trình lập kế hoạch (Rough-Cut Planning). Matcher quét metadata từ `StorageManager.assets()`, chấm điểm và đề xuất `asset_id` phù hợp nhất. Matcher **KHÔNG** ghi vào timeline DaVinci Resolve, **KHÔNG** copy file media, và **KHÔNG** xác nhận quyền thực thi file.
- **G07 (Action-Time Preflight & Timeline Mutation):**  
  Tại thời điểm thực thi lệnh chèn clip vào DaVinci Resolve (Action-Time), hệ thống bắt buộc phải gọi `check_action_availability(asset, intent="insert")` từ `src/core/action_guard.py`. ActionGuard sẽ:
  + Kiểm tra `os.stat` và `os.path.realpath` thực tế trên đĩa ngay trước milli-giây chèn.
  + Từ chối nếu file đã bị xóa, ổ đĩa bị ngắt kết nối (unplug), hoặc bị symlink ra ngoài root an toàn.
  + Ghi nhận trạng thái trung thực vào Action Log.

### 4.2. Xử Lý Khi Kho Rỗng hoặc Không Khớp (Zero-Placeholder Policy)
- Khi kho media rỗng (`StorageManager.assets() == []`) hoặc không tìm thấy asset nào đạt ngưỡng điểm tối thiểu (ngưỡng cắt `match_score >= 20.0`):
  + **KHÔNG ĐƯỢC PHÉP:** Tự ý tạo dummy MP4/WAV rỗng.
  + **KHÔNG ĐƯỢC PHÉP:** Tự ý quét thư mục code dự án để tìm file demo placeholder.
  + **KHÔNG ĐƯỢC PHÉP:** Tự ý gọi API tải ngầm từ Internet khi chưa có sự đồng ý của người dùng.
  + **BẮT BUỘC:** Trả về `OfflineAssetMatchResult(is_matched=False, unmatched_reason="ERR_NO_SUITABLE_ASSET", suggested_action="Không tìm thấy tài nguyên phù hợp trong kho hiện tại. Vui lòng nạp thêm gói tài nguyên qua Trình nạp Pack.")`.

---

## 5. Bảng Sự Thật & Bộ Fixtures Kiểm Thử Hữu Hạn (Truth Table & Fixtures Matrix)

Bảng ma trận kiểm thử bao quát toàn bộ các tình huống biên ngoại tuyến, chỉ sử dụng in-memory dictionaries đại diện cho metadata JSON, không đọc file zip 3.18 GiB trên đĩa.

| Case ID | Đầu Vào (Request & Inventory Mock) | Điều Kiện Biên | Kết Quả Mong Đợi (`OfflineAssetMatchResult`) | Ghi Chú Ranh Giới |
|---|---|---|---|---|
| **TT-01** | `query="whoosh"`, Catalog rỗng `[]` | Kho chưa cài đặt pack | `is_matched=False`, `candidates=()`, `unmatched_reason="ERR_CATALOG_EMPTY"`. | Zero dummy files, nhãn tiếng Việt rõ ràng. |
| **TT-02** | `query="whoosh"`, Catalog có 1 SFX Whoosh nhưng `availability="offline"`, `allow_offline=False` | Ổ cứng rời bị rút ra | `is_matched=False`, `candidates=()`, `unmatched_reason="ERR_ASSETS_OFFLINE"`. | Không chèn file offline khi chưa cho phép. |
| **TT-03** | `query="whoosh"`, Catalog có 1 SFX Whoosh nhưng `availability="offline"`, `allow_offline=True` | Chế độ lập dàn ý thô khi đi xa | `is_matched=True`, `candidates=(ast_sfx_whoosh,)`, `availability="offline"`. | Cho phép gán ID vào plan nhưng cảnh báo cần cắm ổ. |
| **TT-04** | `query="laugh"`, Catalog có 2 asset cùng tên "laugh.wav" thuộc Pack A (`ast_pack_a_01`) và Pack B (`ast_pack_b_02`) | Trùng tên tệp giữa các packs | Trả về 2 candidates phân biệt rõ ràng bởi `asset_id`, `pack_id`, không bị đè index. | Chấm dứt lỗi phụ thuộc basename thô. |
| **TT-05** | `query="cinematic broll"`, Asset chỉ có `preview_relative_path`, master `library_path` bị thiếu | Companion preview only | Asset bị gắn cờ `availability="missing"`, bị loại khỏi danh sách B-roll chèn. | Ngăn chặn đưa file MP4 preview vào timeline. |
| **TT-06** | `query="chớp sáng"`, Catalog có Template native transition DaVinci | Native guidance item | `candidates=(ast_trans_flash,)`, `is_native_guidance=True`. | Không tìm file media, hướng dẫn Resolve native transition. |
| **TT-07** | `query="hào hứng"`, Asset có tag `["vui nhộn", "upbeat", "hao hung"]` | Tiếng Việt có dấu và không dấu | `is_matched=True`, `matched_signals` chứa `"emotion_synonym"`, điểm >= 60.0. | Xử lý triệt để NFKD qua `strip_accents`. |
| **TT-08** | `query="tech logo"`, `category_constraint="custom_sponsor"` | Custom Category hợp lệ | Chỉ quét trong asset có `category="custom_sponsor"`, bỏ qua các category khác. | Hỗ trợ taxonomy mở rộng của người dùng. |
| **TT-09** | `query="background"`, `min_duration_sec=30.0`, Asset có `duration_sec=None` | Asset thiếu metadata thời lượng khi có ràng buộc min/max | Bị loại bỏ khỏi danh sách ứng viên theo Decision 5 (không đủ bằng chứng thời lượng). Khi không có min/max, asset thiếu duration vẫn được giữ lại. | Loại trừ an toàn theo Decision 5. |
| **TT-10** | `query="impact"`, 2 SFX có cùng điểm số `match_score=50.0`, 1 managed 1 linked | Trùng điểm xếp hạng (Tie) | Xắp xếp ổn định tất định 100% theo `(-match_score, 1 if offline else 0, normalized_name ASC, raw_asset_id ASC)`. Tuyệt đối không cộng managed bonus. | Đảm bảo tính tất định và trung lập nguồn gốc (Decision 4, 10). |

---

## 6. Điều Kiện Tiên Quyết Thực Tế & Ranh Giới Phạm Vi (Prerequisites & Constraints)

1. **Trạng Thái Thư Viện Hiện Tại:**  
   Thư viện ChunDVC đã đạt trạng thái `PUBLISHED_VERIFIED` trên Google Drive dưới dạng một tệp lưu trữ toàn vẹn (full backup monolithic ZIP ~3.18 GiB). Tính năng tải và chia tách pack mô-đun 1-click (`LIB-PUBLISH-03`) **chưa được triển khai** trong phạm vi này.  
   *Hệ quả:* Matcher 06B phải hoạt động hoàn hảo và an toàn ngay cả khi kho media trên máy đang rỗng hoặc chỉ có một vài file liên kết thủ công. Tuyệt đối không tự ý giải nén file backup ZIP 3.18 GiB trong các bài test.
2. **Bảo Toàn Media Gốc Của Người Dùng:**  
   Không được tự động copy, di chuyển, symlink cưỡng bức footage của người dùng vào kho `StorageManager`.
3. **Phân Định Trách Nhiệm Giữa Các Module:**
   - **06A (ĐÃ XONG):** Phân lô kịch bản kẹp khung an toàn (`BatchPlanner`), thẩm định bản thảo lô (`BatchDraft`), và sao chép tổng quan toàn dự án (`ProjectOverview`).
   - **06B (BƯỚC NÀY):** Thiết kế Pure Matching Contracts & Offline Matcher Engine (Design-Only).
   - **06C (TIẾP THEO):** Lắp ráp bản vẽ đạo diễn hoàn chỉnh (`CopilotDirectorPlan` / `EditPlan`), tích hợp kết quả match vào cấu trúc kịch bản trước khi bàn giao sang G07.
   - **G07 (TƯƠNG LAI):** Thực thi các mutation trên timeline DaVinci Resolve thông qua `ResolveGateway`.

---

## 7. Đề Xuất Duy Nhất Một Implementation Slice Nhỏ

Để đảm bảo quy trình kiểm soát chất lượng TDD chặt chẽ và giảm thiểu bán kính rủi ro, đề xuất **DUY NHẤT** một lát cắt thực thi kế tiếp:

### Tên lát cắt: `G06-06B1-I: Pure Offline Asset Matcher Engine & Contracts`
- **Số lượng module:** Tối đa 2 production modules mới + 1 test file:
  1. `src/core/contracts/asset_matching_contracts.py`: Định nghĩa các pure dataclasses (`OfflineAssetMatchRequest`, `OfflineAssetCandidate`, `OfflineAssetMatchResult`).
  2. `src/core/offline_asset_matcher.py`: Triển khai lớp `OfflineAssetMatcher` thuần túy, nhận danh sách `List[Dict[str, Any]]` từ `StorageManager.assets()` và thực thi logic lọc, chuẩn hóa bỏ dấu tiếng Việt, chấm điểm 7 tín hiệu và tie-break.
  3. `tests/core/test_offline_asset_matcher.py`: Bộ 15-20 unit tests bao quát toàn bộ 10 ca trong Bảng sự thật (Truth Table), chạy dưới 1.5 giây trên offscreen/headless.
- **Ranh giới:** Không sửa đổi giao diện người dùng (Zero UI changes), không gọi DaVinci Resolve, không phụ thuộc mạng.

---

## 8. Các Quyết Định Ràng Buộc Từ Tech Lead Codex (Binding Decisions - ĐÃ TRIỂN KHAI)

Các câu hỏi chính sách đã được Tech Lead Codex phê duyệt và chốt quyết định tại `docs/architecture/06B-codex-decisions.md`:

### Quyết định 1: Authority Duy Nhất Cho Danh Mục (Decision 1 & 6)
- **Quy tắc:** Storage registry canonical categories (`overlays`, `memes`, `luts`, `templates`, v.v.) là thẩm quyền duy nhất.
- **Ánh xạ UI aliases:** UI aliases lịch sử được chuẩn hóa trước: `overlay` -> `overlays`, `meme` -> `memes`, `lut` -> `luts`, `text` -> `templates` (kèm `template_type="text"`), `trans` -> `templates` (kèm `template_type="trans"`). Nếu `category="text"` nhưng lại truyền `template_type="trans"` sẽ ném `ValueError` ngay tại Contract validation.

### Quyết định 2: Chính Sách Khả Dụng Offline (Decision 3)
- **Quy tắc:** `allow_offline=False` theo mặc định. Khi False, mọi asset có `availability == "offline"` bị loại trừ hoàn toàn (trả về mã `ERR_ASSETS_OFFLINE` nếu chỉ có asset offline).
- Khi `allow_offline=True`, candidate offline được giữ lại, gắn cờ `requires_reconnect=True`. Không hạ điểm tùy tiện, việc xếp hạng ưu tiên online trước offline được thực hiện tại tầng tie-break.

### Quyết định 3: Trung Lập Nguồn Gốc — Bỏ Hoàn Toàn Managed Bonus (Decision 4 & 10)
- **Quy tắc:** Tuyệt đối không cộng điểm thưởng `managed` (+5.0 bonus bị bãi bỏ hoàn toàn).
- Thứ tự phân xử hòa điểm (tie-break) là tất định 100%: `(-match_score, 1 if offline else 0, normalized_name, raw_asset_id)`.

---

## 9. Trạng Thái Triển Khai & Nghiệm Thu R1 (Implementation & R1 Closure)
- **Lát cắt `G06-06B1-R1` đã hoàn tất và đóng kín 6 nhóm contract gaps:**
  * **Group A (Request boundary):** Dùng chung validator toàn diện (`validate_match_request`) ở cả constructor và match entry, chặn lone surrogates UTF-8, chặn OverflowError từ bigint `10**400`, revalidate frozen DTO khi bị can thiệp.
  * **Group B (Intake & Ownership):** Trích xuất persisted view theo `PERSISTED_ALLOWLIST` trước khi chuyển sang `validate_asset_dict()`, bỏ qua `_raw_obj` mà không gọi deepcopy/repr; thẩm định riêng `origin_library_id` (chặn list/dict/bool) và kiểm tra UTF-8 toàn bộ văn bản tiêu thụ.
  * **Group C (Master Coordinates):** Chuẩn hóa tọa độ so sánh companion (pack-relative `relative_path` vs `preview_relative_path`/`thumbnail_relative_path`; loại trừ builtin/generated trùng companion hoặc thiếu file/relative path; loại trừ linked trùng companion runtime).
  * **Group D (Availability Enums):** Chấp nhận đầy đủ 4 giá trị chuẩn (`online`, `offline`, `missing`, `staging`). Trạng thái `missing`/`staging` là bản ghi hợp lệ (không tăng `rejected_record_count`), catalog chỉ gồm missing/staging trả về `ERR_NO_MATCH_FOUND`.
  * **Group E (Scoring & Determinism):** Stem cả query lẫn name cho tín hiệu exact match (+40), so sánh asset ID nguyên dạng; chặn xung đột normalized alias group key (`Mood` vs `mood`) bằng `ValueError` có kiểm soát ở mọi thứ tự; điểm số các tín hiệu độc lập và kết hợp đạt chuẩn oracle.
  * **Group F (Bounded Diagnostics):** Cảnh báo chẩn đoán được giới hạn tối đa 64 entries và 16,384 UTF-8 bytes kèm marker lược bớt; sắp xếp cảnh báo độc lập với thứ tự catalog (shuffle invariant); bảo toàn chính xác tổng số `rejected_record_count`.
- **Bằng chứng kiểm thử hữu hạn:**
  * Lệnh: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_offline_asset_matcher.py tests/core/contracts/test_asset_dto.py -q`
  * Kết quả: **79 passed in 0.45s (Exit Code: 0)**.
  * Evidence log: `.bridge/ux-evidence/G06-06B1-R1/pytest_run.log`.
