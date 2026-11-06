# BẢNG KÊ CẤU HÌNH QSETTINGS (01-SETTINGS-INVENTORY)

- **Mã thiết kế:** `G01-D-R1`
- **Phiên bản:** `1.1.0-draft`
- **Tác giả:** Antigravity (Worker) · **Review:** Codex (Tech Lead)
- **Trạng thái:** `DESIGN_READY`
- **Phân loại bằng chứng:** `CODE_VERIFIED` trên mã nguồn hiện hành bằng `rg`/`grep` đối chiếu trực tiếp `file:hàm:dòng`.
- **Phạm vi kiểm kê (Scope of Audit):** Giới hạn chính xác trong 10 modules thuộc 8 nhóm chức năng đã kiểm chứng mã nguồn: `source_page.py`, `review_page.py`, `hover_preview.py`, `dock_header.py`, `dock_settings_dialog.py`, `edge_dock.py`, `floating_bubble.py`, `asset_indexer.py`, `multi_screen_service.py`, `timeline_mapper.py`. Tài liệu này **không** khẳng định bao phủ toàn bộ repository ("toàn diện") khi chưa kiểm toán các module khác.

---

## 1. BẢNG TỔNG KÊ CÁC DOMAIN VÀ KEYS ĐÃ XÁC MINH MÃ NGUỒN

Hệ thống hiện ghi nhận cấu hình tại 2 domain chính thức và 1 domain scoped theo class:

### Domain A: `QSettings("ChunDVC", "ResolveFlow")` (Domain chuẩn)

| Tên Key | Kiểu dữ liệu | Mặc định | Module đọc (`file:line:hàm`) | Module ghi (`file:line:hàm`) | Mục đích / Ý nghĩa kỹ thuật |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `dock_edge` | `str` (`"left"` \| `"right"`) | `"right"` | `src/ui/lux/shell/edge_dock.py:156` (`__init__`), `src/ui/lux/shell/dock_settings_dialog.py:447` (`_load_settings`) | `src/ui/lux/shell/edge_dock.py:720` (`set_dock_edge`), `src/ui/lux/shell/dock_settings_dialog.py:485` (`_on_edge_changed`) | Vị trí neo thanh dock bên trái hoặc bên phải màn hình. |
| `auto_collapse_on_leave` | `bool` | `False` | `src/ui/lux/shell/edge_dock.py:159` (`__init__`), `src/ui/lux/shell/dock_settings_dialog.py:459` (`_load_settings`) | `src/ui/lux/shell/edge_dock.py:734` (`set_auto_collapse_on_leave`), `src/ui/lux/shell/dock_settings_dialog.py:497` (`_on_auto_collapse_toggled`) | Tự động thu gọn thanh dock khi trỏ chuột rời khỏi dock. |
| `auto_open_director` | `bool` | `True` | `src/ui/lux/shell/dock_settings_dialog.py:453` (`_load_settings`) | `src/ui/lux/shell/dock_settings_dialog.py:489` (`_on_auto_director_toggled`) | Tự động mở cửa sổ AI Director khi khởi chạy hoặc chuyển trang. |
| `pinned_by_default` | `bool` | `False` | `src/ui/lux/shell/dock_settings_dialog.py:456` (`_load_settings`) | `src/ui/lux/shell/dock_settings_dialog.py:493` (`_on_pinned_toggled`) | Trạng thái ghim dock mặc định khi khởi động ứng dụng. |
| `bubble_y` | `int` | `None` (tính dynamic giữa màn hình) | `src/ui/lux/shell/edge_dock.py:606` (`_show_bubble_at_edge`) | `src/ui/bubble/floating_bubble.py:650` (`mouseReleaseEvent`) | Tọa độ Y của quả cầu nổi 56px khi thu gọn dock sát mép màn hình. |
| `library/hover_audio_enabled` | `bool` | `True` | `src/ui/lux/library/hover_preview.py:74` (`__init__`), `src/ui/lux/shell/dock_header.py:220` (`_init_ui`), `src/ui/lux/shell/dock_settings_dialog.py:462` (`_load_settings`) | `src/ui/lux/library/hover_preview.py:114` (`set_audio_preview_enabled`), `src/ui/lux/shell/dock_header.py:397` (`_on_audio_toggled`), `src/ui/lux/shell/dock_settings_dialog.py:503` (`_on_hover_audio_toggled`) | Bật/tắt tự động phát âm thanh khi di chuột qua card SFX/BGM. |
| `library/hover_audio_volume` | `float` | `0.75` | `src/ui/lux/library/hover_preview.py:75` (`__init__`), `src/ui/lux/shell/dock_settings_dialog.py:465` (`_load_settings`) | `src/ui/lux/library/hover_preview.py:120` (`set_volume`), `src/ui/lux/shell/dock_settings_dialog.py:509` (`_on_volume_changed`) | Âm lượng xem trước âm thanh khi di chuột (thang 0.0 - 1.0). |
| `display/studio_screen_index` | `int` | Dynamic theo primary | `src/services/multi_screen_service.py:177` (`get_studio_screen_index`) | `src/services/multi_screen_service.py:180` (`set_studio_screen_index`) | Chỉ số màn hình hiển thị Studio trong môi trường đa màn hình. |
| `display/dock_screen_index` | `int` | `0` | `src/services/multi_screen_service.py:184` (`get_dock_screen_index`) | `src/services/multi_screen_service.py:187` (`set_dock_screen_index`) | Chỉ số màn hình hiển thị EdgeDock trong môi trường đa màn hình. |
| `display/auto_open_studio_on_secondary` | `bool` | `True` | `src/services/multi_screen_service.py:191` (`get_auto_open_studio_on_secondary`) | `src/services/multi_screen_service.py:194` (`set_auto_open_studio_on_secondary`) | Tự động mở Studio trên màn hình phụ khi phát hiện 2 màn hình trở lên. |
| `timeline/speed_mode` | `str` (`"off"` \| `"timeremap"`) | `"off"` | `src/core/timeline_mapper.py:26` (`get_speed_mode`), `src/core/fcpxml_generator.py:1047` | `src/core/timeline_mapper.py:42` (`set_speed_mode`) | Chế độ xử lý tốc độ clip trên timeline (`"off"` hoặc `"timeremap"`). |
| `ai/api_key` | `str` | `""` (Secret - Không dump nội dung) | `src/ui/director/pages/review_page.py:965` (`_load_settings`) | `src/ui/director/pages/review_page.py:984` (`_save_api_key`) | Khóa API dịch vụ đám mây (Gemini, Claude, DeepSeek, OpenAI). |
| `ai/cloud_provider` | `str` | `"gemini"` | `src/ui/director/pages/review_page.py:969` (`_load_settings`) | `src/ui/director/pages/review_page.py:1006` (`_save_cloud_provider`) | Nhà cung cấp AI đám mây đang kích hoạt. |
| `ai/ollama_model` | `str` | `"llama3:latest"` | `src/ui/director/pages/review_page.py:974` (`_load_settings`) | `src/ui/director/pages/review_page.py:1011` (`_save_ollama_model`) | Tên model ngôn ngữ chạy qua Ollama cục bộ. |
| `ai/ollama_endpoint` | `str` | `"http://localhost:11434"` | `src/ui/director/pages/review_page.py:978` (`_load_settings`), `src/ui/director/pages/source_page.py:1035` (`_ask_visual_model`) | `src/ui/director/pages/review_page.py:1016` (`_save_ollama_endpoint`) | Endpoint kết nối dịch vụ Ollama cục bộ. |
| `ai/vision_model` | `str` | `"qwen3-vl:2b-instruct"` | `src/ui/director/pages/source_page.py:1030` (`_ask_visual_model`) | `src/ui/director/pages/source_page.py:1034` (`_ask_visual_model`) | Tên model thị giác cục bộ (Ollama) dùng phân tích footage. |

---

### Domain B: `QSettings("ResolveFlow", "ResolveFlowAssistant")` (Phân mảnh lịch sử)

| Tên Key | Kiểu dữ liệu | Mặc định | Module đọc (`file:line:hàm`) | Module ghi (`file:line:hàm`) | Mục đích / Hiện trạng |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `whisper_model` | `str` | `""` (fallback sang `DirectorConfig.whisper_model`) | `src/ui/director/pages/source_page.py:518` (`__init__`) | `src/ui/director/pages/source_page.py:785` (`_on_whisper_model_changed`) | Tên model Whisper bóc băng giọng nói (ví dụ: `base`, `small`, `medium`). |
| `whisper_lang` | `str` | `"vi"` | `src/ui/director/pages/source_page.py:521` (`__init__`) | `src/ui/director/pages/source_page.py:793` (`_on_whisper_lang_changed`) | Ngôn ngữ bóc băng chính (mặc định tiếng Việt). |

---

### Domain C: `QSettings("ChunDVC", self.settings_scope)` (Scoped trong `AssetIndexer`)

Mặc định `settings_scope = "ResolveFlow"`.

| Tên Key | Kiểu dữ liệu | Mặc định | Module đọc (`file:line:hàm`) | Module ghi (`file:line:hàm`) | Mục đích |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `library/linked_folders` | `List[str]` | `[]` | `src/core/asset_indexer.py:60` (`get_linked_folders`) | `src/core/asset_indexer.py:85` (`add_linked_folder`), `src/core/asset_indexer.py:98` (`remove_linked_folder`) | Danh sách đường dẫn tuyệt đối các thư mục ngoài máy tính được kết nối vào kho. |

---

## 2. PHÂN TÍCH RỦI RO PHÂN MẢNH VÀ XUNG ĐỘT

1. **Phân mảnh cấu hình tại `SourcePage`:**
   - Cấu hình Whisper (`whisper_model`, `whisper_lang`) được lưu tại Domain B: `("ResolveFlow", "ResolveFlowAssistant")`.
   - Cấu hình Vision Model (`ai/vision_model`) được lưu tại Domain A: `("ChunDVC", "ResolveFlow")`.
   - Cả hai nằm trong cùng một màn hình `SourcePage` nhưng sinh ra hai tệp cấu hình riêng biệt trên hệ điều hành (`~/.config/ChunDVC/ResolveFlow.conf` và `~/.config/ResolveFlow/ResolveFlowAssistant.conf` trên Linux).
2. **Không có xung đột tên Key (No Key Collisions):**
   - Các key ở hai domain không trùng tên nhau. Key của Domain B là `whisper_model`, `whisper_lang`; Domain A không có các key này.
3. **Nguyên tắc kỹ thuật: KHÔNG XÓA HOẶC RENAME NGAY LẬP TỨC:**
   - Việc đổi tên key hoặc xóa file config cũ sẽ làm mất thiết lập của người dùng từ các phiên bản trước.
   - Task `G01-D-R1` **CHỈ THIẾT KẾ**, tuyệt đối không chạy migration hoặc sửa code production.

---

## 3. THIẾT KẾ CƠ CHẾ ĐỌC TƯƠNG THÍCH (READ-FALLBACK & IMPORT-ONCE POLICY)

### 3.1. Thuật toán kiểm tra Precedence (Ưu tiên) và Type Validity (Mã nguồn thực tế Slice 01C)

Không dùng kiểm tra `bool(value)` hoặc `if val:` thông thường vì các giá trị hợp lệ có thể bị nhầm lẫn. Cơ chế kiểm tra dựa trên `QSettings.contains(key)` và kiểm tra hợp lệ kiểu chuỗi không rỗng/không toàn khoảng trắng.

Triển khai thực tế trong `src/ui/director/pages/source_page.py`:
- Primary domain: `QSettings("ChunDVC", "ResolveFlow")`
- Legacy domain: `QSettings("ResolveFlow", "ResolveFlowAssistant")`
- Keys: `whisper_model`, `whisper_lang`

```python
# MÃ NGUỒN THỰC TẾ (src/ui/director/pages/source_page.py)
PRIMARY_SETTINGS_ORG = "ChunDVC"
PRIMARY_SETTINGS_APP = "ResolveFlow"
LEGACY_SETTINGS_ORG = "ResolveFlow"
LEGACY_SETTINGS_APP = "ResolveFlowAssistant"

def _is_valid_whisper_setting_value(val: Any) -> bool:
    if not isinstance(val, str) or isinstance(val, bool):
        return False
    return len(val.strip()) > 0

def _import_legacy_whisper_setting(primary: QSettings, key: str, val: str) -> bool:
    try:
        primary.setValue(key, val)
        primary.sync()
        status = primary.status()
        if status != QSettings.Status.NoError:
            logger.warning("Không thể import cấu hình '%s' sang primary domain (status=%s)", key, status)
            return False
        return True
    except Exception as exc:
        logger.warning("Ngoại lệ khi import cấu hình '%s' sang primary domain: %s", key, exc)
        return False

def _read_whisper_setting(
    key: str,
    default: str,
    primary: Optional[QSettings] = None,
    legacy: Optional[QSettings] = None,
) -> str:
    p = primary if primary is not None else _get_primary_settings()
    l = legacy if legacy is not None else _get_legacy_settings()

    # 1. Kiểm tra Primary
    try:
        if p.contains(key):
            val = p.value(key)
            if _is_valid_whisper_setting_value(val):
                return val
    except Exception as exc:
        logger.warning("Lỗi đọc cấu hình '%s' từ primary settings: %s", key, exc)

    # 2. Fallback sang Legacy
    try:
        if l.contains(key):
            val = l.value(key)
            if _is_valid_whisper_setting_value(val):
                _import_legacy_whisper_setting(p, key, val)
                return val
    except Exception as exc:
        logger.warning("Lỗi đọc cấu hình '%s' từ legacy settings: %s", key, exc)

    # 3. Fallback về default (không ghi default vào domain nào)
    return default

def _write_whisper_setting(
    key: str,
    val: str,
    primary: Optional[QSettings] = None,
) -> bool:
    p = primary if primary is not None else _get_primary_settings()
    try:
        p.setValue(key, val)
        p.sync()
        status = p.status()
        if status != QSettings.Status.NoError:
            logger.warning("Lỗi ghi cấu hình '%s' vào primary settings (status=%s)", key, status)
            return False
        return True
    except Exception as exc:
        logger.warning("Ngoại lệ khi ghi cấu hình '%s' vào primary settings: %s", key, exc)
        return False
```

### 3.2. Chính sách giải quyết xung đột và Rollback (Conflict & Rollback Policy)

1. **Xung đột giá trị (Conflict Resolution):**
   - Nếu một key xuất hiện ở cả Domain A và Domain B:
     + Nếu giá trị tại **Domain A (`ChunDVC/ResolveFlow`) hợp lệ kiểu**, Domain A có độ ưu tiên cao nhất (Primary Precedence) và giá trị tại Domain B sẽ bị bỏ qua.
     + Nếu giá trị tại Domain A bị lỗi kiểu (corrupted/invalid type), hệ thống được phép fallback sang đọc giá trị từ Domain B.
2. **Bảo toàn dữ liệu cũ (No Destructive Operation & Rollback Boundary):**
   - Trong quá trình `import-once`, key tại Domain B **KHÔNG BỊ XÓA** (`remove()`). Tệp cấu hình cũ được bảo toàn nguyên vẹn trên đĩa.
   - Lý do: Đảm bảo khả năng **Rollback hoàn toàn**. Nếu người dùng hạ cấp (downgrade) về phiên bản cũ hơn của ứng dụng, phiên bản cũ vẫn đọc được đầy đủ cấu hình cũ tại `("ResolveFlow", "ResolveFlowAssistant")`.
   - *Lưu ý kỹ thuật:* Khả năng rollback chỉ bảo toàn các giá trị đã có từ trước trong domain cũ; các thay đổi cấu hình mới phát sinh sau này trên Domain A không tự động đồng bộ ngược về domain cũ.
3. **An toàn khi lỗi ghi (Write Safety):**
   - Nếu ổ đĩa đầy hoặc lỗi quyền ghi tệp trong lúc `primary.setValue()`, giá trị đọc được từ Domain B vẫn được trả về để app hoạt động bình thường, không làm sập ứng dụng.

---

## 4. MA TRẬN PHÂN LOẠI QUYẾT ĐỊNH

| Quyết định | Trạng thái | Ghi chú |
| :--- | :--- | :--- |
| Giữ nguyên cấu hình hiện tại, không đổi tên QSettings trong runtime | `[APPROVED_BY_CODEX]` | Đã chốt theo review của Tech Lead Codex. |
| Phạm vi kiểm kê 10 module trong 8 nhóm chức năng đã xác minh mã nguồn | `[CURRENT]` | Đã đối chiếu bằng `rg` và `grep` trực tiếp. |
| Cơ chế Read-Fallback dùng `contains()`, Type Validator và check `status()` cho Whisper SourcePage | `[IMPLEMENTED]` | Đã triển khai trong Slice 01C (`_read_whisper_setting`, `_write_whisper_setting`). |
| Giữ nguyên key cũ tại Domain B sau `import-once` (không xóa) | `[IMPLEMENTED]` | Bảo toàn trên đĩa cho khả năng rollback. Thay đổi mới không mirror về legacy. |
| Phạm vi cấu hình ngoài SourcePage Whisper (Vision, ReviewPage, Shell...) | `[DEFERRED]` | Giữ nguyên hiện trạng, không thay đổi ngoài scope 01C; G02 chưa giao. |
