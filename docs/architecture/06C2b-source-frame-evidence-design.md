# Thiết Kế Cung Cấp Bằng Chứng Khung Hình Video Nguồn — G06-06C2b-D

> **CODEX BINDING DECISIONS & TRẠNG THÁI TRIỂN KHAI — 2026-10-30:**  
> - Đã được nghiệm thu định hướng kèm hiệu đính bắt buộc theo `docs/architecture/06C2b-codex-decisions.md`.  
> - **G06-06C2b-I / G06-06C2b-R1 (ĐÃ SỬA CHỮA HOÀN TẤT & CHỜ NGHIỆM THU — REPAIRED_PENDING_REVIEW):** Lát cắt đầu tiên gồm đúng 2 pure modules: `src/core/geometry/source_frame_evidence_contracts.py` (DTO `VideoStreamHeader` đúng 6 trường siêu dữ liệu container báo cáo) và `src/core/geometry/source_frame_evidence_parser.py` (hàm `parse_ffprobe_video_header`). Đã hoàn thiện toàn bộ 13 counterexamples của Codex (Group 1: Explicit null vs Missing key; Group 2: Scope tiêu thụ disposition; Group 3: Safe diagnostics chống vượt giới hạn 4300 chữ số và chống gọi `__repr__` độc hại; 172/172 tests PASS trong 0.50s).  
> - **Tiếp nối:** Thiết kế thu thập siêu dữ liệu tệp cục bộ và kết nối consumer tại `docs/architecture/06C2c-local-metadata-acquisition-design.md`.  
> - **Hiệu đính kiến trúc bắt buộc (Binding Corrections):**  
>   1. **Header rate không chứng minh CFR:** Theo định nghĩa chính thức của FFmpeg (`AVStream.r_frame_rate` là "just a guess"), việc `r_frame_rate == avg_frame_rate` hoặc chênh lệch nhỏ hơn bất kỳ ngưỡng nào (kể cả $10^{-5}$ hay $0.001$) **KHÔNG ĐƯỢC** coi là bằng chứng CFR (`is_cfr = True`). Ngược lại, sai lệch header cũng không tự khẳng định `CONFIRMED_VFR`. Header parser giữ nguyên hai tỷ lệ độc lập và không đưa ra kết luận CFR.  
>   2. **Không đồng nhất packet với frame:** Phủ quyết quy tắc `total_frames = packet_count` hoặc `variance(packet duration) == 0 => confirmed CFR`. ffprobe phân định rõ packet và frame; quét packet không tự là bằng chứng hiển thị frame (presentation evidence).  
>   3. **Phân tách rạch ròi:** Phân định rõ giữa các tham số toán học tối thiểu mà bộ lượng tử hóa yêu cầu (`source_fps: RationalFPS`, `timeline_fps: RationalFPS`, `total_source_frames: int`, `is_cfr: bool`) với các trường định danh tệp, đường dẫn, mã băm hay provenance trong tương lai.  
>   4. **Loại bỏ các con số giả định chưa đo lường:** Không đưa ra các tuyên bố chưa qua đo đạc thực tế trên phần cứng đích (như "<50ms", "15–45 phút", "40–120GB", "trôi sau 1–2 phút", "100% coverage"). `av_read_frame` là thao tác đọc packet, không phải giải mã toàn bộ khung hình.  
> - **Các thành phần tiếp tục HOÃN LẠI (DEFERRED):** Tiến trình gọi ffprobe thực tế (`SourceMediaProbeSupplier`), xác minh frame toàn vẹn bitstream, bộ nhớ đệm trên đĩa, adapter tự động chuyển sang `quantize_cfr_video_span`, whole timeline composer, và DaVinci Resolve Scripting API.

---

## 1. Bản Đồ Truy Vết Codebase Thực Tế & Lỗ Hổng Siêu Dữ Liệu Nguồn (Actual Callgraph & Gaps)

Nhằm đảm bảo thiết kế phản ánh chính xác 100% hiện trạng mã nguồn ChunDVC và không giả định những cơ chế không tồn tại, bảng dưới đây đối chiếu chi tiết giữa hiện trạng đã kiểm chứng (`[CODE_VERIFIED]`) và các đề xuất thiết kế bằng chứng (`[PROPOSED]`):

### 1.1. Bảng Đối Chiếu Hiện Trạng Codebase

| Ký hiệu / Hàm / Đối tượng | Tệp mã nguồn & Dòng | Phân loại | Hiện trạng thực tế trong codebase | Ranh giới kỹ thuật & Lỗ hổng xác thực |
| :--- | :--- | :--- | :--- | :--- |
| `ClipEntry` | `src/core/clip_manifest.py:60-75` | `[CODE_VERIFIED]` | Data class biểu diễn clip nguồn: `clip_id`, `content_hash`, `file_name`, `file_size`, `duration_sec` (float), `fps` (float), `start_tc_frames` (int), `shot_time`, `current_path`. | Thuộc tính `fps` là số thực (`float`), **chưa có** `time_base`, `r_frame_rate`, `nb_frames`, hay cờ xác nhận `is_cfr`. Trong `from_dict`: `fps = float(data.get("fps", 30.0))` tự fallback về 30.0 nếu thiếu. |
| `compute_content_hash` | `src/core/clip_manifest.py:21-49` | `[CODE_VERIFIED]` | Tính hash tệp: `sha256(file_size + 1MB đầu + 1MB cuối)`. | Là **Partial Hash** (chỉ đọc tối đa 2MB), dùng để phát hiện nhanh tệp di chuyển/đổi tên, **không chứng minh** toàn vẹn nội dung của toàn bộ tệp video lớn. |
| `compute_manifest_id` | `src/core/clip_manifest.py:52-57` | `[CODE_VERIFIED]` | Lấy 8 ký tự đầu SHA-256 của danh sách `clip_id:content_hash;`. | Chỉ là định danh phiên của danh sách clip, **không phải** bằng chứng khung hình nguyên bản (Native Frame Proof). |
| `_probe_and_create_clip_entry` | `src/core/clip_manifest.py:320-353` | `[CODE_VERIFIED]` | Dòng 333 cố gắng gọi `from src.core.ffmpeg_utils import get_video_metadata; meta = get_video_metadata(file_path)`. | `get_video_metadata` **hoàn toàn không tồn tại** trong `ffmpeg_utils.py`! Khối `try...except Exception:` luôn nuốt `ImportError` âm thầm và fallback về `dur=0.0, fps=30.0, start_tc=0`. Mọi clip probe qua hàm này đều bị gán sai 30fps! |
| `detect_vfr_status` | `src/core/ffmpeg_utils.py:113-196` | `[CODE_VERIFIED]` | Gọi `ffprobe -show_streams -select_streams v:0`, đọc `r_frame_rate` và `avg_frame_rate`, kiểm tra `abs(r_fps - avg_fps) > 0.5`. | Là **Heuristic thô sơ**; nếu ffprobe lỗi hoặc không có video stream, hàm trả về `is_vfr: False, r_fps: 0.0, avg_fps: 0.0`. Ghi đè vào global `_VIDEO_META_CACHE` của `resolve_api.py`. |
| `parse_fractional_rate` | `src/core/ffmpeg_utils.py:99-111` | `[CODE_VERIFIED]` | Chuyển đổi chuỗi dạng "30000/1001" sang `float` qua `parts = rate_str.split('/')`. | Ép kiểu về `float` làm mất tính chính xác hữu tỷ của chuẩn NTSC/SMPTE, gây trôi frame khi tính toán thời lượng dài. |
| `compute_full_file_sha256` | `src/core/cache_manager.py:302-360` | `[CODE_VERIFIED]` | Streaming 1MB khối để tính SHA-256 toàn diện tệp, đối chiếu `stat_before` và `fstat_after` (TOCTOU protection), hỗ trợ `cancelled_fn`. | Bằng chứng toàn vẹn nội dung của tệp nguồn trên đĩa, không phụ thuộc đường dẫn hay tên tệp. |
| `validate_file_identity` | `src/core/cache_manager.py:124-170` | `[CODE_VERIFIED]` | Đối chiếu nghiêm ngặt: regular file, `file_size`, `mtime_ns`, `ctime_ns`, `dev`, `ino` so với `provenance_dict`. | Rào chắn kiểm tra độ tươi mới (freshness) trước khi đọc cache, phát hiện tệp bị sửa đổi hoặc tráo đổi. |
| `VideoStreamHeader` | `src/core/geometry/source_frame_evidence_contracts.py` | `[CODE_VERIFIED]` | DTO frozen đúng 6 trường siêu dữ liệu container báo cáo: `stream_index`, `codec_name`, `r_frame_rate`, `avg_frame_rate`, `reported_nb_frames`, `time_base`. | Báo cáo trung thực thông số container; không mang cờ verified hay is_cfr. |
| `parse_ffprobe_video_header` | `src/core/geometry/source_frame_evidence_parser.py` | `[CODE_VERIFIED]` | Hàm thuần túy phân tích cú pháp dict JSON ffprobe, chọn stream video hợp lệ (loại trừ cover art), bounded text/streams, fail-closed an toàn. | Thuần túy đọc metadata; zero I/O, zero subprocess. |

### 1.2. Phân Tích Lỗ Hổng Thực Tế Của Codebase Legacy
1. **Lỗi Import Mồ Côi (Orphan Import Bug):**  
   Tại `src/core/clip_manifest.py:333`, lệnh import `from src.core.ffmpeg_utils import get_video_metadata` là nguyên nhân gốc rễ khiến dữ liệu metadata thực tế từ ffprobe bị bỏ qua. Khối `except Exception:` tại dòng 339 đã che giấu `ImportError`, làm các clip nguồn được khởi tạo từ luồng này bị gán `fps = 30.0` và `duration_sec = 0.0`.
2. **Nguy Cơ Lẫn Lộn Giữa Định Danh File Và Bằng Chứng Khung Hình:**  
   - `content_hash` trong `ClipEntry` chỉ đọc tối đa 2MB (1MB đầu + 1MB cuối). Hai video khác nhau có thể trùng `content_hash` nếu phần đầu/cuối giống nhau.
   - `manifest_id` (8 ký tự hex) chỉ xác định phiên danh mục. Không thể suy ra số frame hay cờ CFR từ `manifest_id`.
   - Đường dẫn file (`current_path`) và tên file (`file_name`) có thể bị rename, move hoặc thay thế nội dung (source replaced) mà không thay đổi tên.
3. **Quy Tắc Bắt Buộc:**  
   Không được phép sửa trực tiếp `clip_manifest.py` hay `ffmpeg_utils.py` trong lát cắt hiện hành. Lỗ hổng import được ghi nhận chính xác như một prerequisite để module thu thập bằng chứng mới thiết lập kênh đọc dữ liệu độc lập.

---

## 2. Ma Trận Dữ Liệu Bắt Buộc Để Gọi `quantize_cfr_video_span` (Data Contract Matrix)

Cần phân định rạch ròi giữa **các tham số toán học mà hàm `quantize_cfr_video_span` yêu cầu** và **các trường siêu dữ liệu do container ffprobe báo cáo**:

| Tên Trường Dữ Liệu | Kiểu Dữ Liệu Chuẩn Tắc | Nguồn Thu Thập | Vai Trò Trong Bộ Tính Toán Hình Học | Phân Định Kiến Trúc |
| :--- | :--- | :--- | :--- | :--- |
| `source_fps` | `RationalFPS` (e.g. `RationalFPS(30000, 1001)`) | Bằng chứng khung hình nguồn đã xác minh | Tỷ lệ khung hình nguồn nguyên bản để lượng tử hóa điểm cắt giây `[src_in, src_out)` thành frame nguyên qua `pts_to_frame`. | **Bắt buộc cho converter.** Không được tự động lấy từ `r_frame_rate` hay `avg_frame_rate` nếu chưa qua kiểm chứng bitstream. |
| `timeline_fps` | `RationalFPS` (e.g. `RationalFPS(24, 1)`) | Cấu hình sequence timeline đích | Tỷ lệ khung hình timeline để tái tỷ lệ thời lượng record qua `pts_to_frame`. | **Bắt buộc cho converter.** Do caller cung cấp. |
| `total_source_frames` | `int` (> 0) | Bằng chứng số frame nguyên bản | Ranh giới chặn trần EOF ($0 \le src\_in < src\_out \le total\_frames$). Chống cắt tràn frame ngoài file. | **Bắt buộc cho converter.** `reported_nb_frames` từ container chỉ là báo cáo tham khảo; chưa phải số frame thực tế. |
| `is_cfr` | `bool` (bắt buộc `True`) | Quy trình kiểm định bitstream/cadence | Khẳng định tốc độ khung hình không đổi. Điều kiện tiên quyết để áp dụng Công thức B. | **Bắt buộc cho converter.** Không tự suy từ header rate. |
| `applied_speed` | `Union[int, float, Fraction]` (> 0) | Kịch bản dựng kịch bản | Hệ số co giãn tốc độ phân đoạn. | **Bắt buộc cho converter.** Mặc định 1x. |
| `stream_index` | `int` ($\ge 0$) | ffprobe JSON stream index | Xác định stream video được chọn trong tệp nguồn đa luồng. | Thuộc VideoStreamHeader; không truyền vào converter. |
| `codec_name` | `str` (e.g. `"h264"`, `"prores"`) | ffprobe JSON codec_name | Xác định codec để hỗ trợ thông báo hoặc transcode khuyến nghị. | Thuộc VideoStreamHeader; không truyền vào converter. |
| `time_base` | `Fraction` (e.g. `Fraction(1, 90000)`) | ffprobe JSON time_base | Cơ sở thời gian stream để giải mã PTS trong tương lai. | Thuộc VideoStreamHeader; không truyền vào converter. |
| `full_content_sha256` | `str` (64 ký tự hex) | `cache_manager.compute_full_file_sha256` | Định danh nội dung tệp toàn diện. | Thuộc tầng provenance tương lai; không truyền vào converter. |

---

## 3. Chính Sách Bằng Chứng CFR & Ranh Giới FFmpeg (Honest CFR Policy)

### 3.1. Phân Định Giữa Header Guesses Và Bằng Chứng Khung Hình Thực Tế
Theo tài liệu chính thức của thư viện FFmpeg (`libavformat/avformat.h`):
> `r_frame_rate`: *"Real base framerate of the stream. This is the lowest framerate with which all timestamps can be represented accurately... It is just a guess! For example, if the time base is 1/90000 and all frames have either approximately 3600 or 1800 timer ticks, then r_frame_rate will be 50/1."*

Do đó:
1. **Không có ngưỡng epsilon nào chứng minh CFR:**  
   Việc `r_frame_rate == avg_frame_rate` hoặc khoảng cách $|r\_rate - avg\_rate| < \epsilon$ (dù là $10^{-5}$ hay $0.001$) **KHÔNG THỂ** khẳng định video là CFR. Nhiều video VFR quay từ điện thoại di động vẫn có header ghi `30/1` cho cả hai trường.
2. **Không tự suy CONFIRMED_VFR từ sai lệch header:**  
   Sự sai lệch giữa `r_frame_rate` và `avg_frame_rate` cũng không đủ để khẳng định video là VFR, vì trong nhiều container CFR tiêu chuẩn (như container phát sóng hoặc clip có telecine cadence), `avg_frame_rate` có thể bị tính toán sai lệch do dropped frames hoặc container padding.
3. **Phân định rõ Packet và Frame:**  
   Số lượng packet (`packet_count`) không mặc định bằng số lượng frame hiển thị (`frame_count`). Một packet có thể chứa nhiều frame hoặc ngược lại (tùy codec). Bằng chứng kiểm tra độ biến thiên packet PTS cũng không bảo đảm toàn bộ nội dung của tệp là CFR nếu chỉ quét một phần hữu hạn.
4. **Quy tắc An Toàn Fail-Closed:**  
   Trong lát cắt `VideoStreamHeader`, hệ thống **bảo tồn độc lập cả hai tỷ lệ** `r_frame_rate` và `avg_frame_rate` dưới dạng `RationalFPS` (hoặc `None`), và **hoàn toàn không đưa ra bất kỳ kết luận nào về CFR**. Khi chưa có module kiểm định frame thực tế, trạng thái CFR vẫn là chưa xác minh (`UNVERIFIED`).

---

## 4. Thiết Kế Thu Thập Dữ Liệu & Ranh Giới Bộ Nhớ Đệm (Acquisition & Cache-Boundary)

### 4.1. Thực Thi Ngoại Vi An Toàn (Subprocess Boundary — DEFERRED)
Mặc dù việc gọi tiến trình con ffprobe chưa được triển khai trong lát cắt pure parser hiện tại, các ranh giới thiết kế được xác lập cho các lát cắt sau:
1. **Chạy ngoài UI Thread:** Tiến trình ffprobe phải được thực thi trong worker ngoài GUI, không chặn vòng lặp sự kiện của PySide6.
2. **Giới hạn thời gian (Finite Timeout):** Giới hạn thời gian hữu hạn cho mỗi tiến trình con kèm cơ chế kiểm tra `cancelled_fn()`.
3. **Giới hạn bộ đệm phân tích cú pháp (Caps Output Buffer):**  
   Bộ đệm văn bản được giới hạn `MAX_METADATA_TEXT_BYTES = 1024` cho từng trường văn bản và `MAX_METADATA_STREAMS = 256` cho danh sách streams nhằm loại trừ nguy cơ tràn bộ nhớ.
4. **Không can thiệp tệp nguồn:** Chế độ truy cập tệp là **Read-Only** 100%. Tuyệt đối không ghi đè, đổi tên, hay tạo tệp tạm cùng thư mục với media của người dùng.

### 4.2. Ranh Giới Bộ Nhớ Đệm Bằng Chứng (Evidence Cache Boundary — DEFERRED)
1. **Ràng buộc định danh:**  
   Bộ nhớ đệm trong tương lai phải được gắn kết với `full_content_sha256`, `selected_stream_index`, phiên bản phân tích cú pháp (`probe_schema_version`), và phạm vi kiểm định (`completion_scope`).
2. **Kiểm tra độ tươi mới (Freshness Check):**  
   Sử dụng `validate_file_identity(file_path, provenance)` từ `cache_manager.py` để phát hiện tệp nguồn bị thay thế hoặc sửa đổi. Nếu thông số tệp biến động, cache bị coi là vô hiệu (`STALE`).
3. **Không tạo cache repository-local mới lúc này:**  
   Không triển khai cơ chế ghi đĩa cache trong lát cắt pure parser; việc lưu cache sẽ được xem xét ở lát cắt supplier ngoại vi.

---

## 5. Cấu Trúc DTO & Hiện Thực Lát Cắt `G06-06C2b-I`

### 5.1. DTO `VideoStreamHeader` Chuẩn Tắc (Đã Triển Khai)
Đã triển khai trong `src/core/geometry/source_frame_evidence_contracts.py`:

```python
@dataclass(frozen=True)
class VideoStreamHeader:
    """Siêu dữ liệu tiêu đề (header) của một stream video do container ffprobe báo cáo."""
    stream_index: int                       # strict int >= 0
    codec_name: Optional[str]               # nonblank bounded UTF-8 str without NUL
    r_frame_rate: Optional[RationalFPS]     # rational N/D hoặc None
    avg_frame_rate: Optional[RationalFPS]   # rational N/D hoặc None
    reported_nb_frames: Optional[int]       # strict int > 0 hoặc None
    time_base: Optional[Fraction]           # rational N/D hoặc None
```

### 5.2. Hàm Phân Tích Cú Pháp `parse_ffprobe_video_header` (Đã Triển Khai)
Đã triển khai trong `src/core/geometry/source_frame_evidence_parser.py`:
- Kiểm tra `payload` Mapping, `streams` list/tuple $\le 256$ items.
- Xác thực cấu trúc toàn bộ stream entries: `index >= 0` không trùng lặp, `codec_type` hợp lệ.
- Lọc stream video hợp lệ: `codec_type == 'video'` và không phải `attached_pic=1` (loại trừ cover art). Video MJPEG thật không có attached picture vẫn được chấp nhận.
- Cơ chế chọn stream xác định: Nếu không chỉ định `selected_stream_index`, chọn stream video có `index` nhỏ nhất. Nếu chỉ định rõ, bắt buộc khớp chính xác video stream hợp lệ; từ chối fallback sang video khác nếu chỉ định audio hay cover art.
- Phân tích chi tiết r_frame_rate, avg_frame_rate, reported_nb_frames, time_base, codec_name **chỉ trên stream đã chọn**.

---

## 6. Ma Trận Kiểm Thử Hữu Hạn Đã Đạt Chuẩn (Finite TDD Matrix)

Bộ kiểm thử tại `tests/core/geometry/test_source_frame_evidence_parser.py` (73 tests) kết hợp cùng `test_frame_geometry_calculator.py` (79 tests) đạt **152 tests passed trong 0.44s**:
1. **Positive Controls:** H.264 30/1 + 900 frames; HEVC NTSC 30000/1001 + 24000/1001 exact; sentinels (`"N/A"`, `"0/0"`, `"0"`, `"0/1"`) về `None`; toàn bộ trường tùy chọn `None`.
2. **Không Tự Suy CFR/VFR:** Cả hai trường hợp r_fps == avg_fps và r_fps != avg_fps được lưu độc lập; không có trường `is_cfr` hay `confidence`; các trường `packets`, `duration`, `nb_read_packets` bị bỏ qua.
3. **Lọc Stream & Lựa Chọn:** Đa stream (audio 0, cover art 1, video thật 2, video phụ 3) mặc định chọn stream 2; MJPEG thật hợp lệ; explicit selector chọn đúng; explicit selector trúng audio/cover art ném `ERR_NO_VIDEO_STREAM`; selector sai kiểu ném `ERR_INVALID_STREAM_SELECTION`.
4. **Kiểm Tra Ranh Giới & Lỗi Cấu Trúc:** Payload không phải Mapping / streams không phải list ném `ERR_INVALID_PROBE_PAYLOAD`; danh sách streams $> 256$ ném `ERR_PROBE_METADATA_LIMIT` ngay từ đầu (0 iterations); stream entry thiếu index, trùng index, index âm/bool, codec_type sai kiểu ném `ERR_INVALID_VIDEO_METADATA`.
5. **Xác Thực Giá Trị Trường:** Rate có mẫu số 0 với tử số dương ném `ERR_INVALID_VIDEO_METADATA`; rate số thực, ký tự dấu, khoảng trắng, Unicode digits ném lỗi; nb_frames âm/bool/float/leading zero ném lỗi; giới hạn 1024 bytes văn bản chấp nhận đúng bằng và từ chối 1025 bytes (`ERR_PROBE_METADATA_LIMIT`).
6. **Constructor & Tính Bất Biến:** `VideoStreamHeader` tự xác thực cả 6 trường; hỗ trợ `MappingProxyType`; không bị biến dị bởi payload caller; số nguyên cực lớn `10**18 + 12345` không bị mất mát float.
7. **Purity Spies & AST Check:** 0 filesystem IO, 0 network, 0 clock; kiểm tra tĩnh AST xác nhận không import module cấm.

---

## 7. Trạng Thái Các Quyết Định Kiến Trúc Codex (Q1–Q3 Status)

Căn cứ biên bản quyết định `docs/architecture/06C2b-codex-decisions.md`:
- **Q1 (Header Heuristic Threshold) — BỊ BÁC BỎ:** Tuyệt đối không sử dụng ngưỡng sai lệch rate trong header để gán `is_cfr = True`. Tầng header parser chỉ lưu reported metadata mà không xác nhận CFR.
- **Q2 (Cache Bằng Chứng Định Danh) — CHẤP NHẬN ĐỊNH HƯỚNG, HOÃN THI CÔNG:** Ràng buộc cache với toàn vẹn nội dung và độ tươi mới được phê duyệt cho lát cắt supplier ngoại vi sau này; không triển khai trong lát cắt pure parser.
- **Q3 (Xử Lý Clip UNKNOWN/VFR) — CHẤP NHẬN FAIL-CLOSED:** Khi chưa có bằng chứng CFR xác thực, bộ lượng tử hóa hình học từ chối xử lý (`ERR_MISSING_SOURCE_EVIDENCE`), không âm thầm loại bỏ phân đoạn chính theo D02. Transcoding là luồng công việc riêng có tác động lưu trữ, không hứa hẹn tự động chuyển đổi trong lát cắt này.
