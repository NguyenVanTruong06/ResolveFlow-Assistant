# Thiết Kế Thu Thập Siêu Dữ Liệu Tệp Cục Bộ — G06-06C2c-D (Hiệu Chỉnh Cơ Học Theo Quyết Định Ràng Buộc)

> **CODEX CODE GATE 2026-10-30:** C2c1 CODE_ACCEPTED_WITH_RECORDED_LIMITS/LIVE_PENDING, xem `06C2c1-code-acceptance.md`. Fresh150tests/0.52s/exit0 +15probesPASS; active C2c1-LIVE evidence only, no GUI/manifest wiring or CFR proof.

> **CODEX BINDING DECISIONS 2026-10-30 (`docs/architecture/06C2c-codex-decisions.md`):**
> 1. **BÁC BỎ TÍCH HỢP ĐỒNG BỘ VÀO ClipManifest:** `ClipManifest.get_or_create` được gọi trực tiếp trên Qt GUI Main Thread (`auto_window.py:4513, 5113`, `review_page.py:5516`). Chạy probe subprocess đồng bộ dù có timeout 3–5s trên mỗi clip vẫn gây nghẽn GUI (GUI freeze); timeout không phải là kiến trúc bất đồng bộ (async architecture). Lát cắt C2c1 chỉ xây dựng một helper I/O đồng bộ bị chặn trần cho worker/CLI caller độc lập. Tuyệt đối KHÔNG đấu nối vào `ClipManifest` hay GUI trong giai đoạn này.
> 2. **LỖ HỔNG IMPORT MỒ CÔI LÀ ĐIỀU KIỆN TIÊN QUYẾT BỊ HOÃN (DEFERRED PREREQUISITE):** Việc import `get_video_metadata` mồ côi tại `clip_manifest.py:333` được ghi nhận rõ là khiếm khuyết kế thừa cần sửa trong lát cắt async consumer riêng, KHÔNG sửa trong lát cắt này.
> 3. **CHÍNH XÁC HÓA CALLGRAPH TỪ SOURCE:** Tên hàm chính xác trong `auto_window.py:4513` là `AutoWindow._build_copilot_clips_data` (không phải `_get_copilot_clips_data`).
> 4. **CƠ CHẾ HỦY & QUẢN LÝ READER TOÀN DIỆN:** Bắt buộc có callback `cancelled_fn: Optional[Callable[[], bool]]` tại 4 giai đoạn (trước spawn, trong giám sát, sau đọc pipes, trước return). Reader threads không được dùng daemon status; phải đóng pipe, wait/reap tiến trình con và join reader thread dứt điểm. Nếu cleanup thất bại, ném `ERR_PROBE_CLEANUP_FAILED` chained từ lỗi gốc.
> 5. **BỔ SUNG `ctime_ns` VÀO `SourceFreshnessStat`:** Gồm đúng 5 trường: `file_size_bytes`, `mtime_ns`, `ctime_ns`, `device_id`, `inode_number`. Ghi nhận trung thực: đối soát hai đầu stat là best-effort freshness, không thể loại trừ 100% mọi race condition phần cứng/OS, không phải bằng chứng toàn vẹn nội dung hay proof CFR.
> 6. **GIAO THỨC SUBPROCESS & PROTOCOL WHITELIST:** ffprobe sử dụng `-protocol_whitelist file` và `-i ABSOLUTE_PATH` với `shell=False`, `stdin=DEVNULL`. Không dựa vào cú pháp `--` chưa được ffprobe document chính thức.
> 7. **HOÃN BỘ NHỚ ĐỆM & KHÔNG THAY ĐỔI SCHEMA:** Không đưa in-memory hay disk cache vào slice này; không có cờ `allow_audio_only`; không sửa `ClipEntry` schema; không tự động backfill manifest cũ.

---

## 1. Bản Đồ Truy Vết Codebase Thực Tế & Lỗ Hổng Gọi (Actual Callgraph & Verified Gaps)

Nhằm đảm bảo thiết kế phản ánh chính xác 100% hiện trạng mã nguồn ChunDVC và không suy đoán những cơ chế không tồn tại, bảng dưới đây đối chiếu chi tiết giữa hiện trạng đã kiểm chứng (`[CODE_VERIFIED]`) và các đề xuất kiến trúc (`[PROPOSED]`):

### 1.1. Bảng Đối Chiếu Hiện Trạng Codebase

| Tệp mã nguồn & Dòng | Ký hiệu / Hàm / Đối tượng | Phân loại | Hiện trạng thực tế trong codebase | Ranh giới kỹ thuật & Lỗ hổng cần xử lý |
| :--- | :--- | :--- | :--- | :--- |
| `src/core/ffmpeg_utils.py:39-56` | `get_ffprobe_path()` | `[CODE_VERIFIED]` | Tìm binary `ffprobe` theo thứ tự: `shutil.which("ffprobe")`, fallback `sys.prefix/bin/ffprobe`, fallback `static_ffmpeg.add_paths()`. | **Side-effect toàn cục:** Dòng 49 gọi `static_ffmpeg.add_paths()` làm biến đổi trực tiếp `os.environ["PATH"]`. Không được gọi bừa bãi chỉ để kiểm tra đường dẫn. |
| `src/core/ffmpeg_utils.py:68-96` | `run_ffmpeg(args, timeout)` | `[CODE_VERIFIED]` | Chạy subprocess ffmpeg bằng `subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, ...)`. | **Không giới hạn buffer (Unbounded read):** Đọc toàn bộ stdout/stderr vào RAM trước khi kiểm tra; nếu tiến trình flood log thì nguy cơ tràn bộ nhớ. |
| `src/core/ffmpeg_utils.py:113-196` | `detect_vfr_status(video_path)` | `[CODE_VERIFIED]` | Gọi `ffprobe -show_streams -select_streams v:0` không có timeout, parse JSON và so sánh `abs(r_fps - avg_fps) > 0.5`. Ghi vào `_VIDEO_META_CACHE`. | **Heuristic thô sơ & không an toàn:** Dùng `resolve_api._VIDEO_META_CACHE` toàn cục; không kiểm tra stat freshness; coi `r_fps == avg_fps` là CFR. |
| `src/core/clip_manifest.py:320-353` | `_probe_and_create_clip_entry(file_path, clip_id, content_hash)` | `[CODE_VERIFIED]` | Dòng 333 gọi: `from src.core.ffmpeg_utils import get_video_metadata; meta = get_video_metadata(file_path)`. | **Lỗ hổng Import Mồ Côi (Orphan Import Bug):** Hàm `get_video_metadata` **hoàn toàn không tồn tại** trong `ffmpeg_utils.py`! Khối `try...except Exception:` tại dòng 339 nuốt `ImportError`, khiến clip luôn nhận `dur=0.0, fps=30.0, start_tc=0`. Mọi clip thêm vào manifest đều bị fallback ngầm 30fps sai lệch! |
| `src/core/clip_manifest.py:257-292` | `ClipManifest.create(file_paths, ...)` | `[CODE_VERIFIED]` | Duyệt danh sách đường dẫn và gọi `_probe_and_create_clip_entry` đồng bộ. | Được gọi trực tiếp trên **Qt Main Thread (GUI)** tại nhiều vị trí. |
| `src/core/clip_manifest.py:294-318` | `ClipManifest.get_or_create(...)` | `[CODE_VERIFIED]` | Tải manifest từ cache nếu khớp hash danh sách, nếu không thì gọi `ClipManifest.create`. | Được gọi trực tiếp trên **Qt Main Thread (GUI)**. |
| `src/ui/auto/auto_window.py:4513` | `AutoWindow._build_copilot_clips_data` | `[CODE_VERIFIED]` | Gọi `manifest = ClipManifest.get_or_create(self.selected_files, ...)` trực tiếp trong luồng xử lý UI của cửa sổ Auto. | **Chạy trên GUI Main Thread:** Nếu việc trích xuất metadata gọi subprocess đồng bộ kéo dài, giao diện người dùng sẽ bị đóng băng (GUI freeze). Do đó Codex đã bác bỏ việc đấu nối synchronous helper trực tiếp vào call path này. |
| `src/ui/auto/auto_window.py:5113` | `AutoWindow._apply_json_data` | `[CODE_VERIFIED]` | Gọi `manifest = ClipManifest.get_or_create(self.selected_files, ...)` khi người dùng áp dụng kịch bản JSON. | **Chạy trên GUI Main Thread:** Cần đảm bảo việc probe siêu dữ liệu có thời gian thực thi bị chặn cứng (hard timeout) hoặc được chuẩn bị trước trong worker. |
| `src/ui/director/pages/review_page.py:5516` | `ReviewPage._apply_json_data` | `[CODE_VERIFIED]` | Gọi `self._manifest = ClipManifest.get_or_create(raw_paths, ...)` khi apply kịch bản. | **Chạy trên GUI Main Thread.** |
| `src/core/autocut.py:363-504` | `get_media_metadata(video_path)` | `[CODE_VERIFIED]` | Thử `ffmpeg.probe()`, nếu hỏng thử `subprocess.run` với `capture_output=True, timeout=5`, nếu hỏng thử PyAV `av.open`. Mặc định fallback 30fps. | **Trộn lẫn đa thư viện & Unbounded buffer:** `capture_output=True` có thể đọc hàng chục MB; ép fps về `float` làm mất phân số hữu tỷ; fallback ngầm về 30.0 fps vi phạm hợp đồng fail-closed. |
| `src/core/audio.py:70-113` | `AudioExtractor.get_audio_duration(file_path)` | `[CODE_VERIFIED]` | Đọc `_VIDEO_META_CACHE`, sau đó gọi `ffmpeg.probe(norm_path)`, lấy `format.duration`. | Phụ thuộc thư viện bên ngoài `ffmpeg-python` (`import ffmpeg`), dùng cache toàn cục không quản lý độ tươi mới của tệp. |
| `src/ui/director/workers/source_analysis_worker.py:1-100` | `SourceAnalysisWorker` | `[CODE_VERIFIED]` | Worker nền kế thừa `QThread`, thực hiện STT và visual sampling. Dòng 73-90 dùng `_default_get_duration` gọi `AudioExtractor` rồi `autocut.get_media_metadata`. | **Chạy hoàn toàn trên Background QThread:** Độc lập với UI, an toàn cho các tác vụ I/O bounded kéo dài. |
| `src/core/geometry/local_metadata_acquisition.py` | `acquire_local_video_header(...)` | `[PROPOSED]` | Adapter I/O mới: kiểm tra file cục bộ regular, subprocess bounded với streaming pipe, kiểm tra stat freshness trước/sau, gọi `parse_ffprobe_video_header`. | **Thiết kế mới:** Zero external dependency, bounded memory/time, fail-closed, cách ly hoàn toàn khỏi GUI. |

### 1.2. Phân Tích Hiện Trạng Gọi Từ GUI Thread & Bác Bỏ Tích Hợp Đồng Bộ
Khảo sát mã nguồn thực tế xác định: `ClipManifest.create` và `ClipManifest.get_or_create` hiện đang được gọi đồng bộ trực tiếp từ các hàm xử lý sự kiện giao diện của `AutoWindow` (`auto_window.py:4513, 5113`) và `ReviewPage` (`review_page.py:5516`).
Nếu đấu nối adapter I/O mới chạy probe subprocess đồng bộ ngay trong `ClipManifest.create`, kể cả với giới hạn timeout 3–5 giây cho mỗi clip:
- Một danh mục chứa 20–50 clip sẽ khiến giao diện người dùng (Qt Main Thread) bị đóng băng hoàn toàn từ 60 đến 250 giây.
- **Quyết định ràng buộc của Codex:** **Timeout không phải là một kiến trúc bất đồng bộ (Timeout is not an async architecture).** Do đó, **tuyệt đối không đấu nối subprocess helper vào `ClipManifest` ở giai đoạn này**. Lát cắt hiện tại chỉ cung cấp một helper I/O đồng bộ bị chặn trần cho worker nền hoặc CLI caller; việc sửa chữa call path legacy của `ClipManifest` và lỗ hổng import mồ côi `get_video_metadata` được bảo lưu làm điều kiện tiên quyết (deferred prerequisite) cho lát cắt thiết kế async consumer riêng tiếp theo.

---

## 2. So Sánh Phương Án: Tái Sử Dụng vs Xây Dựng Bounded IO Adapter Mới (Comparison & Single Slice)

### 2.1. Đánh Giá Các Phương Án Kỹ Thuật

| Tiêu chí | Phương Án 1: Sửa & Tái Dùng `ffmpeg_utils.py` hoặc `autocut.py` | Phương Án 2 (Đã Chọn): Bounded IO Adapter Độc Lập (`local_metadata_acquisition.py`) |
| :--- | :--- | :--- |
| **Phạm vi ảnh hưởng (Blast Radius)** | **Rất Rộng / Rủi Ro Cao:** `ffmpeg_utils` và `autocut` đang được import chéo bởi hơn 15 modules trong toàn bộ hệ thống (audio processor, fcpxml generator, chronological sorter, EDL, UI...). Bất kỳ thay đổi hợp đồng lỗi nào cũng có thể gây hồi quy ngoài ý muốn. | **Cô Lập & Tối Thiểu:** Tạo duy nhất 1 module I/O chuyên trách trong `src/core/geometry/local_metadata_acquisition.py`, không can thiệp GUI hay manifest legacy. |
| **Tính fail-closed & An toàn bộ nhớ** | **Kém:** `autocut.get_media_metadata` tự ý fallback về `fps=30.0`, sử dụng `capture_output=True` không chặn trần bộ nhớ stream, phụ thuộc PyAV và `ffmpeg-python`. | **Tuyệt Đối:** Đọc streaming song song chặn trần cứng (512KB stdout, 64KB stderr), kill process nếu tràn, phân tích cú pháp nghiêm ngặt qua `VideoStreamHeader`, không fallback 30fps ngầm. |
| **Bảo vệ toàn vẹn nguồn (TOCTOU & Freshness)** | **Không có:** Cả `ffmpeg_utils` và `autocut` đều không kiểm tra `stat` trước/sau khi gọi ffprobe, không phát hiện được trường hợp tệp bị thay thế hoặc ghi đè trong lúc probe. | **Có:** Thu thập `stat_before` và `stat_after` (`st_mtime_ns`, `st_ctime_ns`, `st_size`, `st_ino`, `st_dev`), đối soát hai đầu để phát hiện biến động nguồn. |
| **Độc lập nền tảng & Khả năng kiểm thử** | **Khó:** `static_ffmpeg.add_paths()` can thiệp môi trường hệ thống; khó mock subprocess toàn diện mà không ảnh hưởng các luồng khác. | **Dễ Dàng:** Không phụ thuộc thư viện ngoài, hỗ trợ mock `subprocess.Popen` hoàn toàn độc lập, kiểm thử 100% các nhánh lỗi mà không cần chạy ffprobe thật. |

### 2.2. Lát Cắt Triển Khai Thực Tế (Actual Implementation Slice: G06-06C2b-FINAL-THEN-06C2c1-I)
Phạm vi sản xuất được thu hẹp về đúng 2 tệp mã nguồn:
1. **Module Sản Xuất 1 (Guard Fix):** `src/core/geometry/source_frame_evidence_parser.py`  
   Đóng kín rào chắn kiểu dữ liệu sentinel cho `_parse_reported_nb_frames`, từ chối đối tượng độc hại mà không gọi `__eq__`/`__repr__`/`__str__`.
2. **Module Sản Xuất 2 (Bounded IO Helper):** `src/core/geometry/local_metadata_acquisition.py`  
   Module độc lập thực thi subprocess ffprobe bị chặn trần tài nguyên (512KB/64KB), giám sát monotonic deadline, hỗ trợ callback hủy ở 4 giai đoạn, thu hồi tiến trình và reader dứt điểm, bảo vệ freshness hai đầu stat.
3. **Module Kiểm Thử:**  
   `tests/core/geometry/test_source_frame_evidence_parser.py` và `tests/core/geometry/test_local_metadata_acquisition.py` (ma trận kiểm thử hữu hạn 100% mocked).
4. **Không Thay Đổi:** `src/core/clip_manifest.py`, `ffmpeg_utils.py`, `autocut.py`, UI components, và Resolve API.

### 2.3. Hợp Đồng Dữ Liệu Chuẩn Tắc (Exact Final Public API)

```python
# src/core/geometry/local_metadata_acquisition.py

@dataclass(frozen=True)
class SourceFreshnessStat:
    """Ảnh chụp trạng thái hệ thống tệp của nguồn video trước và sau khi probe."""
    file_size_bytes: int
    mtime_ns: int
    ctime_ns: int
    device_id: int
    inode_number: int


@dataclass(frozen=True)
class LocalHeaderAcquisitionResult:
    """Kết quả thu thập siêu dữ liệu tiêu đề cục bộ từ container ffprobe (HEADER_REPORTED)."""
    file_path: str
    video_header: VideoStreamHeader
    format_duration_sec: Optional[Fraction]
    freshness: SourceFreshnessStat


def acquire_local_video_header(
    file_path: Union[str, Path],
    *,
    ffprobe_path: Optional[Union[str, Path]] = None,
    selected_stream_index: Optional[int] = None,
    timeout_sec: float = 5.0,
    cancelled_fn: Optional[Callable[[], bool]] = None,
) -> LocalHeaderAcquisitionResult:
    """Đọc tiêu đề tệp video cục bộ qua ffprobe một cách an toàn và có giới hạn chặn trần.
    
    Quy định:
    - Bắt buộc kiểm tra tham số chặt chẽ: file_path nonblank, không NUL, không URI mạng;
      reject symlink cuối (kể cả dangling), directory, FIFO, socket, device.
    - timeout_sec: số thực/nguyên trong khoảng (0, 60], chặn trần int lớn trước khi chuyển đổi float.
    - cancelled_fn: callable trả về bool; kiểm tra trước spawn, trong supervision, sau reads, trước return.
    - Đọc đồng thời pipes với chặn trần: MAX_PROBE_STDOUT_BYTES = 512KB, MAX_PROBE_STDERR_BYTES = 64KB.
    - Thu hồi dứt điểm: kill, close pipes, join reader threads. Thất bại ném ERR_PROBE_CLEANUP_FAILED chained.
    - Phân tích JSON nghiêm ngặt: cấm duplicate keys, cấm NaN/Infinity, cấm non-UTF8.
    - Parse qua parse_ffprobe_video_header đã nghiệm thu; báo cáo HEADER_REPORTED thuần túy.
    """
    ...
```

### 2.4. Trạng Thái Kết Nối Consumer: Bảo Lưu Điều Kiện Tiên Quyết
Do `ClipManifest` được gọi trên GUI Main Thread, việc sửa chữa dòng import mồ côi `get_video_metadata` tại `clip_manifest.py:333` **chính thức bị hoãn** cho tới khi có một consumer bất đồng bộ (chẳng hạn một background QThread worker như `SourceAnalysisWorker` hoặc một async task manager) được chỉ định làm chủ sở hữu (async owner). Tuyệt đối không can thiệp vào `ClipEntry` schema hoặc tự tạo cờ lỗi trong manifest tại lát cắt này.

---

## 3. Giao Thức Tiến Trình Con Bị Chặn Trần (Bounded Subprocess Protocol)

### 3.1. Rào Chắn Đầu Vào Tệp Cục Bộ (Local Regular File Only)
1. **Từ chối URL & Giao thức mạng:** Bắt buộc là đường dẫn hệ thống tệp cục bộ. Mọi chuỗi chứa `://` (như `http://`, `rtsp://`, `file://`) bị từ chối ngay lập tức với `ERR_INVALID_SOURCE_PATH`.
2. **Từ chối Symlink và Non-Regular Files:**
   - Dùng `os.lstat` để phát hiện symlink ngay ở thành phần cuối (kể cả symlink trỏ tệp thật hay dangling symlink), từ chối với `ERR_INVALID_SOURCE_PATH`.
   - Kiểm tra `stat.S_ISREG(st.st_mode)`: từ chối thư mục, FIFO, socket, ký tự/khối thiết bị.
   - Bắt `FileNotFoundError` khi tệp nguồn vắng mặt ban đầu với `ERR_SOURCE_FILE_NOT_FOUND`.

### 3.2. Thực Thi Subprocess & Whitelist Giao Thức
1. Lệnh gọi subprocess sử dụng mảng argv tách bạch, `shell=False`, `stdin=subprocess.DEVNULL`:
   ```python
   cmd = [
       bin_path,
       "-v", "error",
       "-print_format", "json",
       "-show_format",
       "-show_streams",
       "-protocol_whitelist", "file",
       "-i", canonical_path,
   ]
   ```
2. **Bảo mật giao thức:** Cờ `-protocol_whitelist file` ngăn chặn ffmpeg/ffprobe tự động kích hoạt các giao thức mạng nội dung (như http, tcp, concat, subfile) khi đọc container phức tạp.
3. **Không dựa vào `--`:** Do ffprobe không tài liệu hóa chính thức cú pháp `--`, ta sử dụng cờ `-i <CANONICAL_ABSOLUTE_PATH>` để cô lập đường dẫn đầu vào khỏi các cờ tùy chọn.

### 3.3. Định Vị Binary Không Gây Ô Nhiễm Hệ Thống
1. Sử dụng đường dẫn do caller chỉ định hoặc `shutil.which("ffprobe")`.
2. Thiếu binary $\rightarrow$ ném `FileNotFoundError("ERR_FFPROBE_NOT_FOUND")`.
3. Tuyệt đối không tự động cài đặt, không gọi `static_ffmpeg.add_paths()`, không biến đổi `os.environ["PATH"]`.

### 3.4. Đọc Streaming Hai Chiều Có Giới Hạn Cứng
1. Hai hằng số giới hạn: `MAX_PROBE_STDOUT_BYTES = 512 * 1024` (512KB) và `MAX_PROBE_STDERR_BYTES = 64 * 1024` (64KB).
2. Streaming reader đọc theo từng khối `READ_CHUNK_BYTES = 4096`. Cho phép dung lượng đúng bằng trần (cap equality allowed); vượt trần dù chỉ 1 byte sẽ dừng đọc ngay trước khi ghép byte vi phạm và kích hoạt hủy tiến trình với `ERR_PROBE_METADATA_LIMIT`.

### 3.5. Quản Lý Vòng Đời & Thu Hồi Dứt Điểm (Reader Ownership & Cleanup Lifecycle)
1. Luồng đọc (`_BoundedPipeReader`) **không được phép là daemon thread** (`daemon=False`), đảm bảo quyền sở hữu tài nguyên rõ ràng.
2. Vòng lặp giám sát sử dụng đồng hồ đơn điệu `time.monotonic()`, định kỳ kiểm tra `cancelled_fn()` và timeout mà không bận rộn (sleep thích ứng).
3. Khi xảy ra timeout, hủy, hoặc lỗi:
   - Tiến trình con được `terminate()`, chờ hữu hạn 0.5s; nếu chưa dừng sẽ `kill()` và chờ 0.5s.
   - Đóng các pipe stream để unblock các luồng reader đang kẹt I/O.
   - Join dứt điểm cả hai luồng reader trong giới hạn thời gian finite.
   - Nếu việc dọn dẹp không thể hoàn tất (tiến trình không kết thúc hoặc reader không join được), ném `RuntimeError("ERR_PROBE_CLEANUP_FAILED")` được chain từ nguyên nhân lỗi ban đầu (`raise cleanup_err from primary_failure`).
4. Ghi nhận trung thực: Các lệnh gọi I/O mức hệ điều hành (như `os.lstat` trên NFS hoặc quá trình fork/exec của OS) không thể bị ngắt hoàn toàn bởi watchdog của ứng dụng trong mọi trường hợp.

### 3.6. Ứng Xử Đa Nền Tảng
- Trên Windows (`win32`): Sử dụng `creationflags = CREATE_NO_WINDOW` (`0x08000000`).
- Trên Linux/POSIX: Không truyền creationflags; quản lý tiến trình qua tín hiệu chuẩn.

---

## 4. Bảo Vệ Độ Tươi Mới Của Nguồn (Source Freshness & TOCTOU Protection)

### 4.1. Rào Chắn Hai Đầu `stat_before` và `stat_after`
1. Chụp `stat_before` qua `os.lstat(path_str)`: ghi lại `file_size_bytes`, `mtime_ns`, `ctime_ns`, `device_id`, `inode_number`.
2. Sau khi ffprobe hoàn thành: chụp lại `stat_after`.
3. Kiểm tra tệp có bị chuyển thành symlink, bị xóa, bị đổi thành non-regular file, hoặc bất kỳ trường nào trong 5 trường trên bị thay đổi $\rightarrow$ Ném `ValueError("ERR_SOURCE_MUTATED")`.

### 4.2. Ý Nghĩa Kỹ Thuật: Những Gì Rào Chắn Này Chứng Minh & Không Chứng Minh
- **Những gì được chứng minh (Best-effort Freshness):** Chứng minh tệp không bị biến đổi về kích thước, thời gian sửa đổi, thời gian đổi trạng thái, và định danh inode/device giữa hai thời điểm kiểm tra.
- **Những gì KHÔNG được chứng minh:** Không chứng minh tệp không bị tráo đổi tạm thời rồi hoàn tác trong tích tắc giữa hai mốc stat; không thay thế cho full SHA-256 digest; không chứng minh tính toàn vẹn của toàn bộ bitstream nội dung; và tuyệt đối không chứng minh CFR.

### 4.3. Chính Sách Bộ Nhớ Đệm: Hoãn Triệt Để
- Không đưa bất kỳ bộ nhớ đệm nào (dù là in-memory mapping hay disk cache) vào lát cắt này. Khóa cache dựa trên `(path, size, mtime)` là quá yếu và tiềm ẩn nguy cơ phình to bộ nhớ không kiểm soát. Việc thiết kế cache được hoãn lại cho tới khi có kiến trúc consumer/policy rõ ràng.
- Duy trì chế độ đọc thuần túy (read-only), không bao giờ ghi tệp tạm hay transcode ngầm vào thư mục media của người dùng.

---

## 5. Ranh Giới Bằng Chứng & Tiêu Thụ Siêu Dữ Liệu (Evidence & Consumer Boundary)

### 5.1. Phân Loại Phẩm Cấp Bằng Chứng: `HEADER_REPORTED`
Toàn bộ siêu dữ liệu từ adapter này được phân loại là **`HEADER_REPORTED`** (siêu dữ liệu do container container báo cáo).
- `format_duration_sec`: Là `Fraction > 0` hoặc `None`, thể hiện thời lượng container, không phải thời lượng dòng video đã giải mã.
- `video_header`: Là `VideoStreamHeader` nguyên bản 6 trường từ C2b.

### 5.2. Cấm Tuyệt Đối Tự Suy Quyền CFR
- Tuyệt đối cấm suy luận `is_cfr = True` từ đẳng thức `r_frame_rate == avg_frame_rate`.
- Tuyệt đối cấm tự động dispatch sang các hàm hình học CFR hoặc tính toán số khung hình bằng cách nhân `duration * fps`.

---

## 6. Ma Trận Ca Kiểm Thử Hữu Hạn Đã Triển Khai (Finite Test Matrix)

Module kiểm thử `tests/core/geometry/test_local_metadata_acquisition.py` đã triển khai đầy đủ ma trận lỗi 100% mocked (49 tests PASS):
1. **Danh định NTSC & Tùy chọn luồng:** Video NTSC 30000/1001, duration Fraction, selected_stream_index, đường dẫn chứa khoảng trắng, tiếng Việt có dấu, leading dash.
2. **Từ chối đầu vào (0 Popen calls):** file_path sai kiểu/rỗng/NUL, URI mạng (`http://`, `rtsp://`), tệp không tồn tại, thư mục, symlink thật/dangling, FIFO pipe, timeout_sec sai kiểu/âm/0/>60/NaN/Inf/10**400, selected_stream_index âm/bool/str, binary vắng mặt.
3. **Cờ tiến trình & Nền tảng:** Cờ Windows `CREATE_NO_WINDOW`, POSIX flags, bảo toàn PATH và cwd không bị biến đổi.
4. **Hút pipe streaming & Chặn trần:** Dung lượng đúng bằng trần (512KB stdout, 64KB stderr) được chấp thuận; vượt trần đúng 1 byte bị ngắt tiến trình và ném `ERR_PROBE_METADATA_LIMIT`; stream ném ngoại lệ I/O.
5. **Vòng đời tiến trình & Timeout:** Tiến trình không spawn được, thoát mã lỗi != 0, quá hạn timeout_sec bị kill; dọn dẹp thất bại kích hoạt exception chaining với `ERR_PROBE_CLEANUP_FAILED`.
6. **Hủy ở cả 4 giai đoạn:** Trước khi spawn (0 Popen), trong supervision (kill/reap/join), sau khi đọc pipes, trước khi return; callback ném ngoại lệ được xử lý fail-closed.
7. **Phân tích cú pháp JSON nghiêm ngặt:** Stdout rỗng, non-UTF8, JSON hỏng cú pháp, JSON lồng nhau quá sâu (RecursionError), số nguyên vượt giới hạn parse string (>4300 digits), JSON chứa khóa trùng lặp, JSON chứa hằng số NaN/Infinity, container không có video hoặc chỉ có ảnh bìa.
8. **Phát hiện biến động nguồn (TOCTOU):** Tệp bị xóa, đổi thành symlink, đổi kích thước, đổi mtime, đổi ctime, đổi inode hoặc device_id trong quá trình probe.
9. **Kiểm tra constructor DTO & Hostile Protection:** Kiểm tra độc lập tại `SourceFreshnessStat` và `LocalHeaderAcquisitionResult` không I/O; đối tượng hostile với `__str__`/`__repr__` ném lỗi không làm crash chương trình; int cực lớn 10**5000 được xử lý an toàn mà không dính lỗi giới hạn chuỗi số nguyên.

---

## 7. Kết Luận Kiến Trúc & Bàn Giao (Architectural Conclusion)

Toàn bộ các yêu cầu từ `06C2c-codex-decisions.md` đã được phản ánh đầy đủ:
- Bác bỏ tích hợp đồng bộ vào GUI / ClipManifest.
- Bổ sung `ctime_ns`, callback hủy ở 4 giai đoạn, thu hồi và join reader có kiểm soát trần.
- Hoãn toàn bộ caching và không thay đổi schema của `ClipEntry`.
- Không tuyên bố đảm bảo tuyệt đối chống lại mọi race condition hệ điều hành hay coi header là bằng chứng CFR.
- Sẵn sàng bàn giao cho Tech Lead Codex nghiệm thu.
