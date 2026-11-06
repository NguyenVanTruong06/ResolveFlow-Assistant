# Thiết Kế Consumer Tiêu Đề Bất Đồng Bộ (06C2c2 Async Metadata Consumer Design)

> **Tài liệu kiến trúc (Đã cập nhật tiến độ thực tế theo G06-06C2c2-FINAL-THEN-LIVE):**  
> **Cấp độ Worker:** HIGH  
> **Nhiệm vụ:** G06-06C2c2-FINAL-THEN-LIVE  
> **Ngày:** 2026-10-30  
> **Trạng thái:** ĐÃ HOÀN TẤT TRIỂN KHAI & KIỂM CHỨNG THỰC TẾ (IMPLEMENTED & LIVE VERIFIED)  

---

## 1. Callgraph Thực Tế (CODE_VERIFIED: ĐÃ TRIỂN KHAI)

Dựa trên khảo sát và kiểm chứng thực tế tại kho mã, callgraph thu thập siêu dữ liệu đã hoàn tất triển khai:

```
[Main GUI Thread - PySide6]
  auto_window.py:4513 (_build_copilot_clips_data)
    └── ClipManifest.get_or_create() ---------------------> [CODE_VERIFIED: SYNCHRONOUS GUI - KHÔNG THÊM HEADER PROBE]
  auto_window.py:5113 (_apply_json_data)
    └── ClipManifest.get_or_create() ---------------------> [CODE_VERIFIED: SYNCHRONOUS GUI - KHÔNG THÊM HEADER PROBE]
  review_page.py:5516 (_apply_json_data)
    └── ClipManifest.get_or_create() ---------------------> [CODE_VERIFIED: SYNCHRONOUS GUI - KHÔNG THÊM HEADER PROBE]

[Background QThread Worker]
  prompt_preparation_worker.py:1180 (run)
    ├── 2a. req.manifest_metadata (dòng 2195) ────────────> [CODE_VERIFIED: Reconstruct ClipManifest từ request dict, 0 new probe]
    ├── 2b. ClipManifest.load(cache_dir=req.manifest_cache_dir) (dòng 2217) -> [CODE_VERIFIED: Replay cache, 0 new probe]
    └── 2c. ClipManifest.create(persist=False) (dòng 2229) -> [CODE_VERIFIED: ĐÃ TRIỂN KHAI HOÀN TẤT - Nhánh này gọi metadata_provider nền]
              └── metadata_provider = _worker_metadata_provider
                    └── acquire_local_video_header() [src/core/geometry/local_metadata_acquisition.py]
                          ├── Bounded ffprobe (timeout 3.0s, 512KB stdout, timeout/cancel)
                          └── SourceFreshnessStat (5 trường đối soát bảo vệ TOCTOU)

[Legacy Callgraph Lưu Ý]
  clip_manifest.py:274 / 325: chronological_sorter.get_clip_shot_time gọi ffprobe cũ để đọc creation_time khi tên file không khớp pattern ngày tháng.
  -> Các điểm gọi GUI KHÔNG được tuyên bố là zero-subprocess toàn cục; lát cắt này bảo đảm KHÔNG THÊM probe tiêu đề mới vào luồng GUI.
```

### Bảng Chi Tiết Điểm Gọi & Phân Luồng

| File & Hàm | Dòng | Phân Luồng | Trạng Thái Hiện Tại | Đánh Giá & Quyết Định |
| :--- | :--- | :--- | :--- | :--- |
| `src/core/clip_manifest.py:_probe_and_create_clip_entry` | 333–375 | Chung | Đã hỗ trợ `metadata_provider`, `cancelled_fn`, `persist` | **ĐÃ TRIỂN KHAI**: Bảo tồn nhánh legacy khi `metadata_provider is None` (dur=0.0, fps=30.0) để tương thích ngược. Khi có provider, tiêu thụ trọn vẹn kết quả probe, từ chối dữ liệu thiếu/sai lệch với 15/15 probes. |
| `src/ui/auto/auto_window.py:_build_copilot_clips_data` | 4513 | Main GUI | Gọi đồng bộ `ClipManifest.get_or_create` | **Bảo lưu không đổi**. Tiếp tục chạy với `metadata_provider=None`, tuyệt đối không chèn subprocess probe mới gây đơ giao diện. |
| `src/ui/auto/auto_window.py:_apply_json_data` | 5113 | Main GUI | Gọi đồng bộ `ClipManifest.get_or_create` | **Bảo lưu không đổi**. Tránh gián đoạn phản hồi nút bấm người dùng. |
| `src/ui/director/pages/review_page.py:_apply_json_data` | 5516 | Main GUI | Gọi đồng bộ `ClipManifest.get_or_create` | **Bảo lưu không đổi**. Không block GUI event loop. |
| `src/ui/director/workers/prompt_preparation_worker.py:run` | 2229 (nhánh 2c) | **Background QThread** | Gọi `ClipManifest.create` khi chưa có cache/metadata | **CHỦ SỞ HỮU ĐÃ TRIỂN KHAI (OFF-GUI OWNER)**. Nhánh 2c cung cấp `metadata_provider` nền với `persist=False`, bảo vệ TOCTOU freshness, hủy cooperative trước và sau khi render. |
| `src/ui/director/workers/source_analysis_worker.py:run` | 440, 465 | Background QThread | Tự probe audio/duration qua `_audio_probe_fn` | Không quản lý manifest; giữ nguyên phạm vi để không làm rối loạn G05 visual/audio cache. |

---

## 2. Chủ Sở Hữu Bất Đồng Bộ (Off-GUI Owner) & Handoff Bị Chặn Trần

### 2.1 Lựa Chọn Chủ Sở Hữu & Nhánh Thực Thi
Chọn **`PromptPreparationWorker`** làm Off-GUI Owner:
1. `PromptPreparationWorker.run()` khởi đầu từ dòng ~1180, chạy hoàn toàn trên background QThread.
2. Tại công đoạn 2 (dòng 2188–2230), worker xử lý manifest:
   - **Nhánh 2a (dòng 2195):** Khi có `req.manifest_metadata`, tái tạo manifest cũ, giữ nguyên IDs và thứ tự, thực hiện 0 lần probe tiêu đề mới.
   - **Nhánh 2b (dòng 2217):** Khi có `req.manifest_cache_dir`, nạp từ đĩa nếu khớp đường dẫn, thực hiện 0 lần probe tiêu đề mới.
   - **Nhánh 2c (dòng 2229):** Khi cả 2a và 2b đều không có (`manifest is None`), tạo manifest mới trong bộ nhớ với `ClipManifest.create(..., metadata_provider=provider, cancelled_fn=self.is_cancelled, persist=False)`.
3. Worker sử dụng callable `self.is_cancelled` (phương thức trả về strict bool), không truyền trực tiếp biến bool `_is_cancelled`.

### 2.2 Giao Thức Handoff Siêu Dữ Liệu & Không Ghi Đĩa Bừa Bãi
- `req.manifest_cache_dir` không đảm bảo là thư mục phân lập per-request. Do đó, nhánh 2c mới bắt buộc thiết lập `persist=False`, chỉ tạo in-memory manifest mà không ghi đè vào cache đĩa dùng chung hoặc tự ý nối đường dẫn tùy tiện.
- Khi `metadata_provider` được cung cấp:
  - Gọi `acquire_local_video_header` với `timeout_sec=3.0` mỗi clip.
  - Ngoại lệ từ provider (timeout, file missing, invalid media, source mutation, cleanup failure) **KHÔNG ĐƯỢC NUỐT**: lập tức dừng và phát tín hiệu terminal `source_error` hoặc `source_changed`. Tuyệt đối không lưu manifest rỗng hoặc gán kết quả thành công giả mạo.

---

## 3. Ánh Xạ Thuộc Tính, Xử Lý Ẩn Số & Tính Toàn Vẹn Nguồn

### 3.1 Quy Tắc Ánh Xạ Dữ Liệu
Không thay đổi cấu trúc schema của `ClipEntry` (bảo toàn tính tương thích JSON):

1. **Thời lượng (`duration_sec`):**
   - Lấy từ `format_duration_sec` (kiểu `Fraction` từ `LocalHeaderAcquisitionResult`).
   - Chuyển đổi: `float(res.format_duration_sec)` nếu có, hữu hạn và $> 0$; nếu tràn số hoặc không hợp lệ $\rightarrow$ gán `0.0`.
   - Nếu container không báo thời lượng (`format_duration_sec is None`): Gán `0.0` (ghi nhận rõ ràng là chưa biết / unknown, không đoán mò).
2. **Tốc độ khung hình (`fps`):**
   - Ưu tiên `res.video_header.avg_frame_rate`, sau đó là `res.video_header.r_frame_rate`.
   - Gọi phương thức `.as_float()` của `RationalFPS` (không gọi `float(RationalFPS)`), kiểm tra `math.isfinite(fps) and fps > 0.0`; nếu hợp lệ thì lấy, ngược lại gán `0.0`.
   - Tuyệt đối không fallback về `30.0` trong nhánh có provider.
3. **Không tự suy diễn CFR:**
   - Dữ liệu thu được là `HEADER_REPORTED`. Tuyệt đối không gán `is_cfr=True` hoặc tự nhận là bằng chứng khung hình gốc (native frame proof).

### 3.2 Rào Chắn Bảo Vệ Nguồn & Hủy Bỏ
- Đối soát 5 trường `SourceFreshnessStat` (`file_size_bytes`, `mtime_ns`, `ctime_ns`, `device_id`, `inode_number`) với bảng `st_pres` đã thu thập lúc tiền kiểm worker. Nếu có bất kỳ sự sai lệch nào $\rightarrow$ ném ngay `ValueError(ERR_SOURCE_CHANGED)` và fail closed.
- Khi `self.is_cancelled()` trả về `True`: Ném `InterruptedError(ERR_PROBE_CANCELLED)` tại các ranh giới kiểm tra (trước/sau acquisition, trước khi tạo ClipEntry, trước khi trả về), emit duy nhất 1 tín hiệu terminal `cancelled`.

---

## 4. Chiến Lược Cache, Tránh Quét Lại & Quản Lý Hủy

1. **Bảo Tồn Nhánh Tái Sử Dụng 2a và 2b:**
   - Tái sử dụng `req.manifest_metadata` và `req.manifest_cache_dir` với 0 lần gọi header probe mới, không xóa hoặc ghi đè cache G05.
2. **In-Memory Worker Manifest (`persist=False`):**
   - Nhánh 2c sinh manifest trong bộ nhớ cho phiên prompt preparation, không gọi `save()`, không tạo file `.json` thừa trên đĩa.
3. **Hủy Dứt Điểm Không Để Zombie Process:**
   - Khi worker nhận yêu cầu hủy, `acquire_local_video_header` lập tức kích hoạt cơ chế terminate/kill/wait tiến trình con ffprobe và đóng sạch file descriptors.

---

## 5. Kết Quả Triển Khai Thực Tế (Implemented & Verified)

### 5.1 Hai Modules Sản Xuất Đã Hoàn Tất
1. `src/core/clip_manifest.py`:
   - Bổ sung keyword-only arguments: `metadata_provider=None, cancelled_fn=None, persist=True` vào `create` và `_probe_and_create_clip_entry`.
   - Kiểm tra kiểu nghiêm ngặt: `callable(metadata_provider)`, `callable(cancelled_fn)`, strict bool cho `persist` và kết quả của `cancelled_fn()`.
   - Kiểm tra toàn vẹn bắt buộc 15/15 trường dữ liệu thông qua hàm `_get_required_member` và sentinel `_MISSING_MEMBER`: từ chối mọi DTO giả mạo, thiếu trường hoặc sai lệch kiểu.
   - Bảo tồn nhánh legacy khi `metadata_provider is None` (giữ fallback 30 cho legacy GUI callers).
   - Khi `persist=False`: Trả manifest in-memory mà không gọi `save()`.
2. `src/ui/director/workers/prompt_preparation_worker.py`:
   - Trong nhánh 2c (dòng 2229): Xây dựng `_worker_metadata_provider` bọc `acquire_local_video_header` với `timeout_sec=3.0`, `cancelled_fn=self.is_cancelled`.
   - Đối soát freshness với `st_pres`, xử lý lỗi fail-closed, gọi `ClipManifest.create(..., persist=False)`.
   - Bổ sung chốt chặn hủy cooperative sau công đoạn render prompt: nếu `self._is_cancelled` được kích hoạt thì phát ngay `self._emit_terminal(False, "cancelled", {})` mà không công bố prompt.

### 5.2 Bộ Kiểm Thử Đã Vượt Qua
- **Đơn vị lát cắt:** `tests/ui/director/test_prompt_preparation_metadata_slice.py` (16/16 passed, thời gian 5.92s trong Codex review, mã thoát 0). Tuyên bố zero locator calls áp dụng cho toàn bộ feature test dưới fixture cô lập, ngoại trừ ca kiểm thử đối chứng dương `test_case7_chronology_isolation_tripwire` cố ý gọi hàm gốc để chứng minh tripwire bắt được lỗi.
- **Kiểm thử an toàn Harness (TDD Safety):** `tests/task/test_verify_06c2c2_live_harness.py` (5/5 passed in 0.21s, mã thoát 0) bao phủ toàn bộ 2 lỗi probe biên của Codex (setup failure sau allocation kích hoạt cleanup, và cleanup thất bại không bao giờ trả exit 0).
- **Harness thực tế (Live Binary):** `task/verify_06c2c2_live.py`:
  - Chế độ bình thường (Normal): 4/4 kịch bản thực tế thành công với ffprobe binary thật, bao bọc hoàn toàn trong outer `try...finally`, dọn dẹp sạch sẽ tài nguyên (`CLEANUP_SUCCESS`), mã thoát 0.
  - Chế độ tiêm lỗi (Fault Injection `--probe-fault-injection`): Bắt ngoại lệ tại barrier lúc worker active, hủy cooperative, dọn dẹp sạch tài nguyên (`CLEANUP_SUCCESS`), mã thoát 2.
  - Bằng chứng số liệu: `.bridge/ux-evidence/G06-06C2c2-EVIDENCE-R1/` (`normal_metrics.json`, `normal_execution.log`, `fault_metrics.json`, `fault_execution.log`).

---

## 6. Chính Sách Kiến Trúc Đã Được Chốt & Chấp Nhận

1. **Phạm vi áp dụng `fps=0.0` & từ chối dữ liệu hỏng:**
   - DTO không hợp lệ hoặc thiếu trường bắt buộc bị từ chối dứt khoát bằng `ValueError("ERR_INVALID_METADATA_RESULT")`, không bao giờ được âm thầm gán thành `0.0`.
   - Chỉ có các trường tùy chọn thiếu (`None`) hoặc số vượt quá giới hạn hữu hạn mới được ánh xạ thành `0.0` một cách an toàn. Nhánh legacy `provider=None` giữ nguyên fallback `30.0` để bảo đảm an toàn cho các điểm gọi GUI cũ.
2. **Bảo tồn schema `ClipEntry`:**
   - Giữ nguyên chính xác 10 trường dữ liệu hiện hữu của `ClipEntry` (`clip_id`, `file_name`, `duration_sec`, `fps`, `start_tc_frames`, `shot_time`, `shot_time_source`, `content_hash`, `file_size`, `current_path`). Không bổ sung trường mới, không thêm cờ `is_cfr`.
3. **Giới hạn thời gian timeout:**
   - Áp dụng trần 3.0 giây cho mỗi clip trong `PromptPreparationWorker`. Lỗi timeout kích hoạt terminal fail-closed ngay lập tức.

---

## 7. Giới Hạn Ghi Nhận (Recorded Architectural Limits)

Theo phân cấp kiến trúc và quyết định từ Codex (Task `G06-06C2c2-FINAL-THEN-LIVE`):
1. **Bản chất siêu dữ liệu:**
   - Dữ liệu thu được từ ffprobe là `HEADER_REPORTED` (siêu dữ liệu do container video báo cáo).
   - `HEADER_REPORTED` **KHÔNG PHẢI** là CFR đã qua kiểm chứng (NOT verified CFR), không phải số lượng khung hình thực tế trong luồng video (NOT stream frame count) và không đại diện cho danh tính nội dung hình ảnh (NOT content identity).
2. **Kế thừa và tương thích ngược:**
   - Thứ tự sắp xếp niên đại kế thừa (legacy chronology) tại các điểm gọi GUI/default replay được giữ nguyên không đổi.
   - Quy trình phân tích âm thanh/hình ảnh tại G05 không bị xáo trộn.
3. **Phạm vi giai đoạn:**
   - Nhiệm vụ G06 và G07 chưa hoàn thành; lát cắt này chỉ đóng gói thành công tầng async metadata consumer cho `PromptPreparationWorker`.
   - Chưa triển khai giao diện người dùng mới (UI) hay các phép toán hình học nâng cao (geometry calculation) trên timeline.
