# ChunDVC — G05-05A-D-R1 Thiết Kế Kiến Trúc: Chốt Identity & Tương Thích Cache Audio

- **Mã tài liệu:** `docs/architecture/05A-audio-cache-identity-design.md`
- **Giai đoạn lộ trình:** Gate G05 — Source Inventory, Speech-to-Text & Unified Cache Orchestration
- **Nhiệm vụ:** `G05-05A-D-R1 — Chốt identity và tương thích cache audio`
- **Cấp độ Worker:** `HIGH`
- **Tài liệu thẩm định tham chiếu:** `docs/architecture/05-codex-review.md` (Quyết định ràng buộc của Tech Lead Codex)
- **Ràng buộc:** `DESIGN ONLY` — Không sửa đổi code sản xuất, không chạy pytest/app/Resolve/model/network/benchmark/media scan.

---

## 1. NGUYÊN TẮC THIẾT KẾ & ĐỊNH DANH CACHE AUDIO (IDENTITY ARCHITECTURE)

### 1.1 Phân định hai tầng: Ứng viên nhanh (Sampled Candidate) vs Xác minh toàn vẹn (Full-Content Verification)

Để giải quyết mâu thuẫn cốt lõi:
- **GUI Thread cần phản hồi tức thì (<10ms):** Khi người dùng mở thư mục chứa 100 video (tổng dung lượng 50–100GB), giao diện `SourcePage` không thể đọc tuần tự hàng chục GB trên đĩa để tính SHA256 toàn bộ file vì sẽ làm đơ (freeze) giao diện người dùng.
- **Worker Thread cần độ tin cậy tuyệt đối:** Trước khi quyết định bỏ qua khâu chạy Whisper AI nặng (tiết kiệm hàng chục phút GPU/CPU), hệ thống bắt buộc phải chứng minh chắc chắn file video trên đĩa đúng là file đã được phân tích trước đó, kể cả khi file bị chỉnh sửa ở giữa thân (middle-frame edits) hoặc bị đổi tên.

Do đó, kiến trúc định danh cache Audio được tách bạch thành **2 tầng rõ ràng**:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ TẦNG 1: TRA CỨU ỨNG VIÊN NHANH (SAMPLED CANDIDATE LOOKUP)                   │
│ - Môi trường chạy: GUI Thread / SourceScanWorker / probe_video_file.        │
│ - Tốc độ: < 1ms / file.                                                     │
│ - Thuật toán: Stat cơ bản (st_size, st_mtime) + Sampled Fingerprint         │
│   (sha256 của file_size + 1MB đầu + 1MB cuối).                              │
│ - Quyền hạn: CHỈ dùng để hiển thị trạng thái ứng viên (Provisional /        │
│   Candidate Hit) trên UI danh sách clip ("Có khả năng đã có cache").         │
│ - GIỚI HẠN: KHÔNG cấp quyền tự động bỏ qua Whisper khi dựng thật!           │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼ (Chuyển sang Worker khi chạy thật)
┌─────────────────────────────────────────────────────────────────────────────┐
│ TẦNG 2: XÁC MINH NỘI DUNG TOÀN PHẦN (FULL-CONTENT VERIFICATION)            │
│ - Môi trường chạy: DUY NHẤT trong Background Worker Thread (PipelineWorker) │
│ - Tốc độ: Phụ thuộc I/O đĩa (đọc streaming 1MB/block, ~200-500MB/s NVMe).   │
│ - Thuật toán: Streaming SHA256 toàn bộ nội dung file (Full-File SHA256)     │
│   kèm kiểm chứng Pre/Post Stat Consistency.                                 │
│ - Quyền hạn: CẤP QUYỀN CHÍNH THỨC (Verified Cache Hit) để bỏ qua nạp       │
│   mô hình Whisper và tái sử dụng 100% transcript cũ.                        │
│ - Hỗ trợ: Hủy an toàn (Cancellation), kiểm soát TOCTOU, không copy media.   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Thuật toán băm toàn phần có kiểm soát TOCTOU (Time-Of-Check to Time-Of-Use)

Thuật toán băm streaming tại background worker được thiết kế với cơ chế bảo vệ tính ổn định của tệp:

```python
# Sơ đồ thuật toán Streaming SHA256 (Đề xuất cho Worker)
def compute_full_file_sha256(
    file_path: str,
    chunk_size: int = 1024 * 1024,  # 1MB per chunk
    cancelled_fn: Optional[Callable[[], bool]] = None
) -> Tuple[Optional[str], Optional[Dict[str, int]]]:
    """
    Tính SHA256 toàn phần streaming có pre/post stat và hỗ trợ cancel.
    Trả về (sha256_hex, stat_dict) hoặc (None, None) nếu lỗi/hủy/unstable.
    """
    # 1. Pre-stat kiểm tra trạng thái ban đầu
    try:
        st_pre = os.stat(file_path)
    except OSError:
        return None, None

    hasher = hashlib.sha256()
    bytes_read = 0

    # 2. Đọc tuần tự streaming qua buffer
    try:
        with open(file_path, "rb") as f:
            while True:
                if cancelled_fn and cancelled_fn():
                    return None, None  # Hủy an toàn, không tốn thêm I/O
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
                bytes_read += len(chunk)
    except OSError:
        return None, None

    # 3. Post-stat kiểm tra biến động đồng thời (Concurrency / TOCTOU check)
    try:
        st_post = os.stat(file_path)
    except OSError:
        return None, None

    # Nếu kích thước hoặc mtime bị thay đổi trong khi đang đọc -> tệp không ổn định
    if st_pre.st_size != st_post.st_size or st_pre.st_mtime_ns != st_post.st_mtime_ns:
        return None, None

    if bytes_read != st_post.st_size:
        return None, None

    return hasher.hexdigest(), {
        "size_bytes": st_post.st_size,
        "mtime_ns": st_post.st_mtime_ns
    }
```

> [!NOTE]
> **Thừa nhận giới hạn kỹ thuật (TOCTOU & Concurrent Mutation):**
> Cơ chế so sánh `st_pre` và `st_post` loại trừ được hầu hết các trường hợp tệp đang bị render dở hoặc đang được ghi đè từ phần mềm khác (DaVinci/Premiere). Tuy nhiên, trên hệ thống tệp POSIX/Linux thông thường không có lock độc quyền bắt buộc (mandatory locking), nếu có tiến trình khác ghi đè in-place đúng kích thước vào giữa tệp đúng thời điểm sau khi `st_post` đã đọc, hệ thống tệp không thể phát hiện nếu không có file locking cấp OS. ChunDVC ghi nhận đây là giới hạn nền tảng của OS filesystem và kiểm soát tối đa qua Pre/Post Stat Consistency.

### 1.3 Giữ nguyên bất biến cho ClipManifest
- Hàm `compute_content_hash` trong [`src/core/clip_manifest.py`](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/core/clip_manifest.py) và cấu trúc `ClipEntry` (`clip_id`, `content_hash`, `manifest_id`) được **GIỮ NGUYÊN 100%**, không sửa đổi.
- `ClipManifest.content_hash` (kích thước + 1MB đầu + 1MB cuối) được gọi chính xác theo đúng bản chất kỹ thuật: **Sampled Fingerprint**, dùng cho việc lập danh mục và đồng bộ prompt/timeline trong kịch bản dựng G06, không đánh đồng với Full-Content Verification của Audio Cache.

---

## 2. TRUY VẾT CALLSITES & CHỮ KÝ API MỚI (CALLSITE TRACE & API SIGNATURES)

### 2.1 Bản đồ Callsites Hiện Hữu
Hiện tại có 4 điểm tiêu thụ (consumers) cốt lõi của cache audio:
1. **`src/ui/director/pages/source_page.py:249` (trong `probe_video_file`):**
   - Đang gọi: `cached = cache_mgr.get_cached_scan(norm_path, whisper_model, whisper_lang)`.
   - Mục đích: Đọc nhanh để hiển thị chip UI "Đã có cache" hay "Chưa phân tích".
   - Ràng buộc: Chạy trên thread quét GUI, bắt buộc dùng chế độ `verify_full=False` (Fast Candidate).
2. **`src/ui/auto/auto_window.py:372` & `503` (trong `PipelineWorker.run`):**
   - Đang gọi: `cached = cache_mgr.get_cached_scan(vp, self.model_size, self.language)`.
   - Mục đích: Quyết định có nạp mô hình Whisper lên GPU hay không, và khôi phục `raw_subtitles`.
   - Ràng buộc: Chạy trong background thread của `PipelineWorker`, là nơi DUY NHẤT cần kích hoạt `verify_full=True` (Full-Content Verification).
3. **`src/ui/director/pages/review_page.py:1088, 1119`:**
   - Đang gọi: `cache_mgr.get_cached_scan(cpath, whisper_model, whisper_lang)`.
   - Mục đích: Nạp text phụ đề để hiển thị lên bảng duyệt kịch bản (Review Page).
4. **`src/core/clip_manifest.py:387` (trong `create_clip_manifest_prompt_context`):**
   - Đang gọi: `cache_mgr.get_cached_scan(cpath, model, lang)`.
   - Mục đích: Đọc `raw_subtitles` để gắn vào ngữ cảnh prompt LLM.

### 2.2 Chữ ký API Mới Đề Xuất (Additive, Tương thích ngược 100%)

Tại [`src/core/cache_manager.py`](file:///media/vanntruongg/Documents/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/core/cache_manager.py):

```python
CACHE_SCHEMA_VERSION = 3  # Nâng từ version 2 lên version 3

class ScanCacheManager:
    def __init__(self, cache_dir: Optional[str] = None):
        # Thứ tự ưu tiên: Tham số truyền vào > Biến môi trường RESOLVEFLOW_CACHE_DIR > ~/.resolveflow_cache
        if cache_dir is None:
            env_dir = os.environ.get("RESOLVEFLOW_CACHE_DIR")
            if env_dir and env_dir.strip():
                cache_dir = os.path.abspath(env_dir.strip())
            else:
                cache_dir = os.path.join(os.path.expanduser("~"), ".resolveflow_cache")
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self._cached_dir_files: Optional[List[str]] = None

    def get_cached_scan(
        self,
        file_path: str,
        model_size: str = "small",
        language: str = "vi",
        verify_full: bool = False,
        cancelled_fn: Optional[Callable[[], bool]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Tra cứu dữ liệu quét Audio Cache có phân cấp bảo vệ:
        - verify_full=False (Mặc định cho GUI): Tra cứu nhanh bằng Stat và Sampled Fingerprint.
        - verify_full=True (Dành cho Worker): Thực hiện băm streaming SHA256 toàn bộ file,
          hỗ trợ cancel và kiểm chứng Pre/Post Stat.
        """
```

### 2.3 Cấu trúc Schema Cache v3 (`payload` chuẩn mới)

```json
{
  "cache_schema_version": 3,
  "identity_algorithm": "sha256_full_streaming_v1",
  "full_content_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "sampled_fingerprint": "a1b2c3d4e5f6...",
  "file_size": 154829104,
  "mtime_ns": 1729987200000000000,
  "last_known_path": "/media/footage/cam_a/DJI_0001.MP4",
  "model": "small",
  "language_requested": "vi",
  "detected_language": "vi",
  "language_probability": 0.982,
  "clip_dur": 74.5,
  "raw_subtitles": [
    {
      "start": 1.25,
      "end": 4.80,
      "text": "Chào mừng các bạn đã quay trở lại",
      "words": [{"word": "Chào", "start": 1.25, "end": 1.55}]
    }
  ],
  "silence_keep_intervals": [[0.0, 1.2], [4.85, 74.5]],
  "speedup_segments": [],
  "cut_segments": [],
  "audio_energy_db": null,
  "visual_activity": null,
  "provenance": {
    "engine": "faster-whisper",
    "engine_version": "ctranslate2",
    "vad_filtered": true,
    "completed_at": "2026-10-27T07:45:00Z"
  }
}
```

---

## 3. CHÍNH SÁCH TƯƠNG THÍCH VÀ BẢNG CHUYỂN ĐỔI HỮU HẠN (MIGRATION & COMPATIBILITY MATRIX)

Bảng đối chiếu toàn diện 8 kịch bản thực tế:

| STT | Kịch bản thực tế | Trạng thái Cache hiện có | Hành vi Tra cứu Nhanh (`verify_full=False`) | Hành vi Xác minh Worker (`verify_full=True`) | Quyết định Promote sang v3 |
| :---: | :--- | :--- | :--- | :--- | :---: |
| **1** | **Cache cũ đúng đường dẫn và stat (Exact Legacy Hit)** | v2 cũ (`{checksum}_{model}_{lang}.json`) tồn tại, file nguồn tại đúng path, size và mtime khớp. | **HIT (Candidate)**<br>Nạp dữ liệu hiển thị UI ngay trong <1ms. | **HIT (Legacy Verified)**<br>Chấp nhận reuse transcript cũ vì path và stat chưa từng bị đổi từ lúc ghi. | **CÓ** (Tính full SHA256 trong background khi dựng và nâng cấp ghi đè thành v3). |
| **2** | **File cũ bị đổi tên hoặc di chuyển thư mục (Renamed Old Cache)** | Chỉ có file v2 theo `name_{hash}` cũ; file nguồn ở path mới. | **MISS**<br>(Chặn đứng unsafe fallback chỉ dựa vào tên/size để tránh đụng độ). | **MISS**<br>Không đủ bằng chứng nguồn để tự động tin cậy; quét lại Whisper cho file ở vị trí mới. | **KHÔNG**<br>(Không đoán mò cache khi thiếu content proof). |
| **3** | **File v3 mới bị đổi tên hoặc di chuyển thư mục** | Đã có cache v3 (`sha256_{hash}_{model}_{lang}.json`) trên đĩa; file bị rename/move. | **HIT (Candidate)**<br>(Tìm thấy candidate qua `sampled_fingerprint`). | **HIT (Fully Verified)**<br>Worker băm full SHA256 khớp 100% $\implies$ Bỏ qua Whisper an toàn! | **KHÔNG CẦN**<br>(Đã là v3, chỉ cập nhật `last_known_path` trong memo). |
| **4** | **Trùng tên file + cùng kích thước nhưng khác nội dung (Generic Name Collision)** | Hai video khác nhau cùng tên `DJI_0001.MP4` và cùng kích thước 150,000,000 bytes. | **MISS**<br>(Chặn đứng hoàn toàn; không còn nhánh fallback mù chỉ dựa vào filename/size). | **MISS**<br>Full SHA256 không khớp $\implies$ Quét độc lập từng file, không bao giờ nhầm transcript! | **KHÔNG** |
| **5** | **File bị chỉnh sửa ở giữa thân >2MiB (Middle-Frame Edit)** | File render lại: 1MB đầu và 1MB cuối giữ nguyên, nhưng các frame ở giữa bị cắt sửa. | **HIT TẠM THỜI (Candidate)**<br>Sampled fingerprint khớp (do chỉ đọc 1MB đầu/cuối). | **MISS CHÍNH XÁC (Blocked)**<br>Worker băm full SHA256 phát hiện lệch hash $\implies$ Hủy candidate, chạy lại Whisper! | **KHÔNG** |
| **6** | **Thay đổi cấu hình nhận diện (Model hoặc Ngôn ngữ)** | File giữ nguyên nhưng đổi model từ `small` sang `large-v3`, hoặc đổi `vi` sang `en`. | **MISS**<br>(Khóa cache gắn chặt với `{model}_{lang}`). | **MISS**<br>Chạy lại Whisper với model/lang mới, tạo tệp cache v3 độc lập cho model mới. | **TẠO MỚI**<br>(Không xóa cache của model cũ). |
| **7** | **Tệp cache bị hỏng hoặc đọc dở dang (Corrupt / Partial JSON)** | Tệp JSON trên đĩa bị lỗi cú pháp, mất nguồn điện giữa chừng khi ghi hoặc thiếu trường bắt buộc. | **MISS**<br>(Bắt `json.JSONDecodeError`, ghi log cảnh báo ngắn). | **MISS**<br>Bỏ qua file hỏng; worker phân tích lại và ghi đè nguyên tử tệp cache sạch mới. | **GHI ĐÈ SẠCH** |
| **8** | **Lỗi I/O hoặc người dùng bấm Hủy (IO Error / Cancel during Hashing)** | Ổ đĩa bị ngắt kết nối hoặc người dùng bấm nút Hủy tiến trình trong khi worker đang băm. | **MISS / ABORT**<br>(Bắt `OSError` hoặc trả về `None` khi `cancelled_fn()=True`). | **ABORT AN TOÀN**<br>Thu hồi bộ nhớ, xóa tệp tạm, không ghi cache rác, không làm crash app. | **KHÔNG** |

### 3.4 Quy định phân biệt giữa Transcript Rỗng (`[]`) và Cache Thiếu (`None`)

Một trong những vấn đề nghiêm trọng của hệ thống cũ là không phân biệt được giữa:
- **Clip không có tiếng nói / B-roll:** Whisper đã chạy xong và xác nhận hợp lệ rằng clip **không có thoại** $\implies$ Kết quả chuẩn là mảng rỗng: `raw_subtitles: []`.
- **Clip chưa được phân tích hoặc bị lỗi:** Chưa từng chạy phân tích hoặc chạy gặp sự cố $\implies$ Kết quả là `None`.

**Quy tắc xử lý bất biến:**
1. Trong cache v3, nếu Whisper hoàn tất mà không phát hiện giọng nói, lưu trường `raw_subtitles: []` kèm `"status": "no_speech_detected"`.
2. Khi tra cứu:
   - Nếu `cached is None`: Clip chưa có cache $\implies$ Cần quét.
   - Nếu `isinstance(cached, dict)` và `cached.get("raw_subtitles") == []`: Clip **ĐÃ CÓ CACHE HỢP LỆ** (xác nhận không có thoại) $\implies$ **KHÔNG QUÉT LẠI WHISPER**! 
   - Trên UI hiển thị chip xám: `✓ Không có thoại (B-Roll)`.

---

## 4. KẾT QUẢ TRIỂN KHAI THỰC TẾ (IMPLEMENTED SLICE G05-05A-I)

Căn cứ quyết định ràng buộc của Codex tại `docs/architecture/05A-codex-decisions.md`, lát cắt triển khai `G05-05A-I` đã hoàn tất chính xác trên 2 production modules:

### 4.1 Chi tiết Thay Đổi Mã Nguồn Production:
1. **`src/core/cache_manager.py`:**
   - **`is_canonical_sha256_hex(val: Any) -> bool`:**
     * Kiểm tra chuỗi băm đúng 64 ký tự hex (`^[0-9a-fA-F]{64}$`). Từ chối chuỗi ngắn, chuỗi dài, ký tự non-hex hoặc khác kiểu ngay trước khi xử lý IO.
   - **`_is_safe_finite_number(val: Any, min_val=None, strictly_positive=False) -> bool`:**
     * Kiểm tra giá trị số hữu hạn an toàn trước bigint (> 1024 bits như `10**400`, `-10**400`), loại trừ hoàn toàn `OverflowError`.
     * Loại bỏ `bool`, `None`, `NaN`, `Inf`.
   - **`validate_file_identity(file_path: str, provenance: Any) -> bool`:**
     * Đối chiếu nghiêm ngặt: Regular file (`stat_mod.S_ISREG`), `file_size` (strict int >= 0), `mtime_ns` (strict int).
     * Khi platform cung cấp `ctime_ns`, `dev`, `ino`: provenance bắt buộc phải có đầy đủ các trường này dưới dạng strict int và khớp chính xác với `os.stat`. Khuyết, null, hoặc sai kiểu bị từ chối fail-closed ngay lập tức, không cho phép hạ cấp silently.
     * Ngăn chặn triệt để lỗ hổng file bị thay thế cùng size và khôi phục mtime (`os.utime`).
   - **`validate_v3_payload(payload: Any, ...) -> bool`:**
     * `cache_schema_version`: Phải là số nguyên `int` chính xác = 3 (loại bỏ `3.0`, `True`, `"3"`).
     * `algorithm_version == "sha256_full_v3"`.
     * `full_content_sha256`: Bắt buộc là canonical 64hex string.
     * `clip_dur`: Số dương hữu hạn (an toàn trước bigint/OverflowError, loại bỏ `bool`, `NaN`, `Inf`, `<= 0`).
     * `raw_subtitles`: Danh sách list. `[]` là hợp lệ. Nếu có phần tử: mỗi phần tử phải là Mapping với `start` (finite >= 0), `end` (finite > start), `text` (str). Không rò rỉ OverflowError khi gặp timestamp bigint.
     * Các trường tùy chọn (`silence_keep_intervals`, `speedup_segments`, `cut_segments`): Nếu có thì phải đúng kiểu và finite, sai kiểu từ chối hit/save ngay, không tự sinh default che lỗi.
     * Toàn bộ hàm bọc trong `try ... except Exception: return False` an toàn tuyệt đối.
   - **`_atomic_write_json(target_path, data, cancelled_fn=None)`:**
     * `json.dump(..., allow_nan=False)` ngăn chặn xuất bản các hằng số không hợp chuẩn JSON (`NaN`, `Infinity`).
     * Kiểm tra `cancelled_fn()` ngay trước `os.replace`; nếu có yêu cầu hủy, xóa file tạm và ném `InterruptedError`.
   - **`compute_full_file_sha256(file_path, cancelled_fn, chunk_size=1MiB)`:**
     * Đọc streaming từng khối 1MiB (1,048,576 bytes).
     * Kiểm tra `stat_mod.S_ISREG` (chỉ xử lý regular file).
     * Kiểm tra đối chiếu kép: `os.stat` đường dẫn và `os.fstat(f.fileno())` descriptor trước và sau khi stream.
     * So sánh tính nhất quán: `bytes_read == stat_before.st_size == stat_after.st_size == fstat_after.st_size`, `st_mtime_ns`, `st_ctime_ns`, `st_dev`, `st_ino`.
     * Kiểm tra `cancelled_fn()` trước khi mở, giữa từng khối chunk, và sau khi đọc.
     * Nếu bị hủy hoặc tệp biến động: trả về `(None, None)`, tuyệt đối không sinh hash fallback theo path hay dùng partial hash.
     * Trả về `(full_sha256_digest, provenance_dict)`.
   - **`ScanCacheManager`:**
     * `get_cached_scan(..., verify_full=False, cancelled_fn=None, verified_identity=None)`:
       + `verify_full=True`: Kiểm tra `is_canonical_sha256_hex` trước mọi thao tác IO. Sử dụng `json.load(f, parse_constant=_reject_nan_constants)` để từ chối `NaN`/`Inf`.
       + Kiểm tra `validate_file_identity` nếu có `verified_identity`.
       + Xác thực payload qua `validate_v3_payload`.
       + Fail-closed khi identity hoặc payload malformed mà không throw ra caller.
       + Triệt tiêu hoàn toàn nhánh `name_{hash}_{size}` fallback không an toàn.
     * `save_scan_result(..., verified_identity=None, cancelled_fn=None)`:
       + Khi có `verified_identity`: Kiểm tra `is_canonical_sha256_hex` và `validate_file_identity`. Tuyệt đối không fallback sang lưu legacy khi supplied identity bị malformed, mismatch, hoặc khi bị hủy.
       + Xác thực `validate_v3_payload`.
       + Ghi tệp `v3_{digest}_{model}_{lang}.json` nguyên tử kèm metadata xuất xứ `provenance`.
       + Ghi fast alias cho GUI.
2. **`src/ui/auto/auto_window.py` (Chỉ sửa trong `PipelineWorker`):**
   - **Tích hợp các điểm kiểm soát provenance & identity:**
     1. *Phase 2 restore (~dòng 335):* Gọi `cache_mgr.get_cached_scan(vp, ..., verify_full=True, cancelled_fn=lambda: self.is_interrupted)`. Cập nhật `self.clip_data_out_cache = clip_data_list`.
     2. *Preflight needs_whisper (~dòng 385):* Duyệt từng video, gọi `compute_full_file_sha256` kèm cancel callback. Nếu băm thất bại (`(None, None)`) và không cancel: dừng fail-safe lập tức ngay tại preflight (`missing_verified_identity`), **0 model load, 0 extract, 0 transcribe, 0 save**. Nếu cancel, chuyển vào `_handle_interrupted()`.
     3. *Per-clip loop (~dòng 550):* Tái sử dụng `identity` từ run-local. Gọi `validate_file_identity(video_path, prov)`. Nếu phát hiện tệp bị thay đổi sau preflight hoặc thiếu identity: dừng fail-safe tức thì với thông báo lỗi rõ ràng (`source_mutated_after_preflight`), 0 extract, 0 transcribe, 0 save.
     4. *Per-clip save (~dòng 825):* Bỏ hoàn toàn thao tác băm lại cuối analysis; truyền trực tiếp `verified_identity=identity` ban đầu và `cancelled_fn=lambda: self.is_interrupted` vào `save_scan_result`. Nếu nguồn bị thay đổi trong lúc transcribe, `save_scan_result` từ chối xuất bản.
   - **Đúng 1 lần hash/file/run:** Nhờ context run-local, mỗi file ổn định chỉ được tính full hash 1 lần duy nhất ở preflight.

---

## 5. CÁC ĐÁNH ĐỔI VÀ GIỚI HẠN GHI NHẬN (RECORDED LIMITS & TRADEOFFS)

1. **Tradeoff: Chấp nhận quét lại 1 lần cho Cache Legacy (One-Time Miss on Legacy Cache):**
   - Theo quyết định số 1 của Codex, `verify_full=True` không tự động phong cấp (auto-promote) cache v1/v2 vì full hash của file hiện tại không chứng minh transcript cũ được sinh từ chính nội dung đó.
   - Do đó, lần chạy đầu tiên trong worker với các file cũ sẽ quét lại 1 lần và ghi v3 mới; các tệp cache v2 cũ được giữ nguyên vẹn.
2. **Không quét size-bucket trong lát cắt này:**
   - Worker tìm trực tiếp theo `v3_{full_digest}_{model}_{lang}.json`. File đổi tên/di chuyển sẽ hit trực tiếp qua full digest này mà không cần duyệt quét thư mục.
3. **Giới hạn TOCTOU trên hệ thống tệp không khóa:**
   - Mặc dù hệ thống đã kiểm tra kép descriptor (`fstat`) và path (`stat`) trước/sau khi stream và trước khi lưu, việc sửa đổi file đồng thời trong tích tắc giữa save và Resolve import mà OS không có file lock độc quyền là giới hạn bản chất của filesystem POSIX.
4. **Phạm vi bảo toàn:**
   - Giữ nguyên toàn bộ `ClipManifest`, `SourcePage`, `VisualIndex`, logic render/cut/timeline và in-memory phase data.

---

## 6. KẾT QUẢ KIỂM THỬ HỒI QUY (79/79 TESTS PASSED)
- Lệnh chạy: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_audio_cache_identity.py tests/test_pipeline_worker_audio_cache.py tests/test_cache_manager.py tests/test_cache_proxy.py -q`
- Kết quả: **79 passed in 8.12s (Exit code 0)**.
- Chi tiết log: Lưu tại `.bridge/ux-evidence/G05-05A-R2/pytest_run.log`.
