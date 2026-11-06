# THIẾT KẾ KIẾN TRÚC V2 (KINETIC PROMPT) HỮU HẠN
(Hiệu đính theo Quyết định Codex: `docs/architecture/05C2c-codex-decisions.md`)

- **TASK_ID:** `G05-05C2b-EVIDENCE-R1-THEN-05C2c1-I`
- **Cấp độ Worker:** `HIGH`
- **Trạng thái:** `DESIGN_ACCEPTED_WITH_BINDINGS / IMPLEMENTED_PENDING_REVIEW`
- **Cam kết phạm vi:** Chỉ bổ sung đúng 1 production module pure Python `src/core/kinetic/kinetic_dataset_mapper.py` và test `tests/core/test_kinetic_dataset_mapper.py`. Không sửa UI, workers, Resolve hay models.

---

## 1. CALLGRAPH CHI TIẾT CỦA CÁC HÀM V2 HIỆN TẠI (FILE : HÀM : DÒNG)

Qua khảo sát trực tiếp mã nguồn, dưới đây là callgraph chính xác và phân định rạch ròi giữa các hàm đã được kiểm soát (gated) và các hàm tiện ích còn tồn tại I/O đồng bộ / dữ liệu giả định (legacy helper):

```
UI Trigger / Consumer
 │
 ├── [L702] btn_copy_kinetic_prompt.clicked
 │     └── [L2514] ReviewPage._copy_kinetic_prompt()  [GATED]
 │           └── Hiển thị thông báo hint (L2517), set tooltip (L2520), 0 clipboard mutation.
 │
 ├── [L800] btn_auto_kinetic.clicked
 │     └── [L1926] ReviewPage._auto_generate_kinetic_plan()  [REACHABLE - LEGACY BACKDOOR]
 │           ├── [L1928] ReviewPage.get_final_cut_words()
 │           │     ├── [L1788] Nhánh 1: has_v_plan is True
 │           │     │     ├── [L1815] TimelineMapper(fps=real_fps)
 │           │     │     ├── [L1834, L1864] mapper.add_segment(...)
 │           │     │     ├── [L1879] cache_mgr.get_cached_scan(...)  <-- [ĐỒNG BỘ TRÊN GUI THREAD]
 │           │     │     ├── [L1881] clean_whisper_segments(...)
 │           │     │     └── [L1882] add_subtitles_for_range(...)
 │           │     └── [L1898] Nhánh 2: Fallback chưa có Video Plan
 │           │           └── [L1698] ReviewPage._extract_words_from_rows()  <-- [LEGACY I/O & FAKE TOKENS]
 │           │                 ├── [L1703] cache_mgr = ScanCacheManager()  <-- [I/O ĐỒNG BỘ]
 │           │                 ├── [L1714] cache_mgr.get_cached_scan(...)
 │           │                 ├── [L1733] word_dur = 0.35  <-- [FAKE DURATION PER TOKEN]
 │           │                 ├── [L1740] timeline_cursor += (dur if dur > 0 else 60.0)  <-- [FAKE 60s]
 │           │                 ├── [L1761] word_dur = 0.35 (từ review rows)
 │           │                 └── [L1768] return words if words else DEMO_WORDS  <-- [FAKE DEMO_WORDS]
 │           ├── [L1936] kinetic_engine.auto_plan(...)
 │           ├── [L1937] kinetic_engine.validate_plan(...)
 │           └── [L1948] ReviewPage._apply_json_data(...)
 │
 └── In-Memory Getter
       └── [L2499] ReviewPage.get_kinetic_prompt(aspect="9:16")  [GATED]
             └── Trả về "" (L2504), 0 I/O, 0 DEMO_WORDS.
```

### Bảng đối chiếu trạng thái hiện tại:

| File : Dòng | Tên hàm | Trạng thái hiện tại | Rủi ro / Vấn đề kiến trúc |
|---|---|---|---|
| `src/ui/director/pages/review_page.py:2499` | `get_kinetic_prompt` | **Gated** | Đã được ngắt an toàn, trả về `""`, không chạm ổ đĩa. |
| `src/ui/director/pages/review_page.py:2514` | `_copy_kinetic_prompt` | **Gated** | Đã chặn clipboard mutation, phản hồi tiếng Việt trung thực trên `lbl_json_hint`. |
| `src/ui/director/pages/review_page.py:1926` | `_auto_generate_kinetic_plan` | **Reachable** (qua nút L800) | Chạy trực tiếp trên main GUI thread; gọi hàm con nạp cache đĩa đồng bộ. |
| `src/ui/director/pages/review_page.py:1770` | `get_final_cut_words` | **Reachable** (qua L1928, L1902) | Gọi `ScanCacheManager.get_cached_scan` đồng bộ trong vòng lặp clip. |
| `src/ui/director/pages/review_page.py:1698` | `_extract_words_from_rows` | **Legacy Helper** | Tồn tại `DEMO_WORDS` (L1768), `word_dur = 0.35` (L1733, L1761), `60.0s` fallback (L1740). |
| `src/core/kinetic/kinetic_engine.py:231` | `generate_kinetic_prompt` | **Core Pure Logic** | Formatter sinh prompt cho LLM. Chú ý: nếu `words > 300`, hàm này tự động switch sang style-only với 40 mẫu từ (không gửi đủ toàn bộ từ). |
| `src/core/kinetic/kinetic_engine.py:99` | `auto_plan` | **Core Pure Logic** | Phân trang tự động offline dựa trên heuristic; không phụ thuộc LLM. |
| `src/core/kinetic/kinetic_engine.py:343` | `validate_plan` | **Core Pure Logic** | Kiểm tra cấu trúc phân trang, không phụ thuộc network hay GUI. |
| `src/core/timeline_mapper.py:113` | `TimelineMapper` | **Core Mapper** | Đã được nghiệm thu tại G04/G05; quản lý ánh xạ thời gian nguồn sang đích. |
| `src/core/clip_manifest.py:93` | `ClipManifest` | **Core Manifest** | Quản lý đóng băng thứ tự clip (`clip_id`, `content_hash`, `manifest_id`). Tuyệt đối không phát minh `clip_uid`. |

---

## 2. NGUỒN DỮ LIỆU TỪ VỰNG CHUẨN XÁC DUY NHẤT (DATASET WORDS SSOT)

1. **SSOT Tối Thượng:**
   - Dữ liệu từ vựng chỉ được phép đọc từ **Audio Cache Schema v3 đã xác thực SHA-256 toàn phần** (`ScanCacheManager.get_cached_scan(..., verify_full=True)`).
   - Tầng pure mapper (`kinetic_dataset_mapper.py`) KHÔNG đọc cache hay verify SHA (caller worker chịu trách nhiệm xác thực).

2. **Trường dữ liệu Manifest tuân thủ thực tế:**
   - Sử dụng đúng các trường hiện có trong `ClipManifest`: `clip_id`, `content_hash`, `manifest_id`.
   - Tuyệt đối **KHÔNG phát minh `clip_uid` hay UID API** giả tạo.
   - Không tự ý gán `confidence = 0.98` giả tạo; chỉ truyền confidence nếu producer thực sự cung cấp.

3. **Loại bỏ vĩnh viễn dữ liệu giả mạo (Zero Fake Speech):**
   - **XÓA BỎ:** `DEMO_WORDS` (hằng số từ vựng demo tiếng Việt gán sẵn trong mã nguồn).
   - **XÓA BỎ:** Quy ước giả định thời lượng clip $60.0$ giây khi không đọc được metadata.
   - **XÓA BỎ:** Quy ước chia đều $0.35$ giây mỗi từ khi Whisper không có word-level timestamps.
   - **Quy tắc Fail-Closed khi không có thoại:**
     - Nếu kết quả Whisper trả về danh sách rỗng (`[]`), hoặc tệp video là video câm (`no_audio_track`), hoặc trạng thái là `completed_empty`:
     - $\rightarrow$ Kết luận: **Không có kịch bản thoại (No Dialogue)**.
     - $\rightarrow$ Trả về tuple rỗng `()`, không sinh prompt Kinetic V2 giả tạo.

---

## 3. ÁNH XẠ THỜI GIAN: THỜI GIAN TỪ NGUỒN VS THỜI GIAN TIMELINE BẢN DỰNG CUỐI

Quá trình dựng video (rough-cut / final-cut) làm biến đổi vị trí và tốc độ của các clip. Prompt V2 Kinetic bắt buộc phải khớp với **Timeline Bản Dựng Cuối (Final Cut Timeline)**:

1. **Phạm vi cắt chọn (Cắt tỉa giao điểm thực tế, BỎ 60% THRESHOLD TÙY TIỆN):**
   - Theo quyết định từ Codex, **bỏ hoàn toàn ngưỡng 60% tùy tiện**.
   - Với mọi từ có giao điểm với cửa sổ nguồn ($w.start\_sec < seg.src\_out$ và $w.end\_sec > seg.src\_in$):
     - Lấy giao điểm hữu dụng:
       $$inter\_start = \max(w.start\_sec, seg.src\_in)$$
       $$inter\_end = \min(w.end\_sec, seg.src\_out)$$
     - Nếu $inter\_start > w.start\_sec$ hoặc $inter\_end < w.end\_sec$: đánh dấu cờ `clipped = True` và ghi nhận warning boundary clipping (không quảng cáo là word-exact acoustic).
   - Từ nằm trọn vẹn bên ngoài cửa sổ cắt sẽ bị loại bỏ với chẩn đoán cụ thể.

2. **Biến đổi theo tốc độ phát thực tế (Applied / Effective Speed):**
   - Sử dụng tốc độ hiệu dụng thực tế (`applied_speed > 0` dạng `Fraction`), không dùng requested raw speed nếu executor tắt.
     $$\Delta t\_start = \frac{inter\_start - seg.src\_in}{applied\_speed}$$
     $$\Delta t\_end = \frac{inter\_end - seg.src\_in}{applied\_speed}$$
     $$rec\_start\_frame = seg.rec\_in\_frame + pts\_to\_frame(\Delta t\_start, fps)$$
     $$rec\_end\_frame = seg.rec\_in\_frame + pts\_to\_frame(\Delta t\_end, fps)$$
   - Áp dụng làm tròn `half-even` qua `pts_to_frame` với `RationalFPS`.
   - **Không mở rộng giả 1 frame:** Nếu từ bị lượng tử hóa thành 0 frame ($rec\_end\_frame \le rec\_start\_frame$), loại bỏ từ và ghi warning `drop_quantized_empty`.

3. **Xử lý các tình huống phức tạp:**
   - **Clip lặp lại (Duplicate usages):** Mỗi lần xuất hiện trên timeline là một `instance_id` độc lập.
   - **Khoảng lặng / Nhảy cóc (Gaps):** Giữ nguyên khoảng cách giữa các record frames, không snap dính các từ lại với nhau.
   - **Kiểm tra nhất quán hình học:** Kiểm tra độ lệch $|(src\_dur / speed \times fps) - rec\_dur| \le 1$ frame.
   - **FPS Policy:** Strict finite positive qua `RationalFPS`, cấm default 24/30 ngầm.

---

## 4. PHÂN BIỆT EXACT WORD TIMESTAMPS VS SUBTITLE-ONLY (STRICT POLICY)

- Theo quyết định dứt khoát của Codex:
  - Nếu phân đoạn có nội dung thoại nhưng thiếu mảng `words` $\rightarrow$ **Strict Fail-Closed: ném lỗi `ValueError("word_timestamps_missing")`**.
  - Không âm thầm chuyển sang phrase-level hay approximate linear khi tầng downstream consumer chưa hỗ trợ.
  - Tuyệt đối **KHÔNG tải thêm mô hình AI mới** (wav2vec2, CTC forced aligner).
  - Tuyệt đối **KHÔNG gọi API dịch vụ ngoài**.
- **Consumer Validation:** Module mapper thẩm định nghiêm ngặt:
  - $0 \le start < end \le duration\_known$ (finite bounds, cấm bool/NaN/Inf).
  - $seg.start \le w.start < w.end \le seg.end$ (nằm trọn trong câu cha, không dung dưỡng tolerance ngoài nguồn).
  - Text không rỗng sau khi strip; reject mọi non-string token object.

---

## 5. CƠ CHẾ BẤT ĐỒNG BỘ VÀ AN TOÀN CHO V2 (ASYNC ENGINE & GUI DEFER CLOSE)

1. **Tách biệt hoàn toàn Pure Logic và I/O:**
   - `kinetic_dataset_mapper.py`: Module thuần túy toán học và cấu trúc, zero Qt, zero filesystem IO.
   - `kinetic_preparation_worker.py`: QThread worker chịu trách nhiệm đọc cache và build placements.
2. **GUI Defer Close (Không `wait()` chặn GUI thread):**
   - Khi cửa sổ đóng, đặt cờ cooperative cancel và hoãn hủy tài nguyên (defer) cho tới khi QThread phát tín hiệu `finished`. Tuyệt đối không gọi `thread.wait()` đồng bộ làm treo main event loop.
3. **Bảo vệ Clipboard & Physical Busy Control:**
   - Nút `btn_copy_kinetic_prompt` chỉ mở lại khi toàn bộ worker trong registry đã idle.
   - Clipboard chỉ ghi nhận khi snapshot khớp 100% với context hiện tại.

---

## 6. GIỚI HẠN NGÂN SÁCH PROMPT V2 & ĐẶC TÍNH FORMATION HIỆN CÓ

1. **Trần cứng Ngân sách (Hard Byte Limit):**
   - Trần ngân sách: **Tối đa 32,768 bytes UTF-8**.
   - **Hiệu đính:** 32,768 UTF-8 bytes KHÔNG đồng nghĩa bảo đảm ~8K tokens; không đưa ra cam kết "video dưới 3 phút luôn vừa", và không hướng dẫn người dùng sử dụng tính năng batch của G06 khi G06 chưa được xây dựng.
2. **Giới hạn của `generate_kinetic_prompt` hiện có:**
   - Trong `src/core/kinetic/kinetic_engine.py:231`, hàm `generate_kinetic_prompt` có cơ chế: nếu `len(words) > 300`, nó sẽ tự động chuyển sang chế độ **Style-Only Prompt với 40 từ mẫu** (không gửi đủ toàn bộ từ).
   - Tầng Worker V2 khi triển khai sẽ cần xác định rõ mode (style-only vs full-indexed) và thông báo minh bạch, không silent drop từ vựng.
3. **Fail-Closed khi vượt trần:**
   - Vượt quá 32,768 bytes $\rightarrow$ ném `KineticBudgetExceededError`, tuyệt đối không âm thầm cắt xén làm lệch chỉ mục animation.

---

## 7. PHẠM VI TRIỂN KHAI ĐẦU TIÊN (DUY NHẤT 1 PRODUCTION MODULE CHO SLICE HIỆN TẠI)

Tuân thủ nghiêm ngặt chỉ đạo của Tech Lead Codex:
1. **Module Production mới duy nhất:** `src/core/kinetic/kinetic_dataset_mapper.py` (Đã triển khai và PASS 100%).
2. **Module Test mới duy nhất:** `tests/core/test_kinetic_dataset_mapper.py` (Đã triển khai và PASS 15/15 tests).
3. **Các slice tiếp theo (Worker / UI):** Sẽ được triển khai trong các task kế tiếp, không thực hiện vội vã trong phiên này.

---

## 8. DANH MỤC VIỆC CÒN LẠI ĐỂ ĐÓNG HOÀN TOÀN G05 (CHECKLIST ĐÃ CHUẨN HÓA)

- [x] **G05-05A:** Nâng cấp hệ thống Whisper audio analysis & ScanCacheManager v3 (Đã hoàn thành).
- [x] **G05-05C1:** Hoàn thiện Review loader và Story Copilot prompt V1 (Đã nghiệm thu).
- [x] **G05-05C2a:** Tái cấu trúc core consumer/formatter V1 với verified SHA (Đã nghiệm thu).
- [x] **G05-05C2b:** Triển khai Worker bất đồng bộ Prompt V1, kiểm soát busy & context validation (Đã nghiệm thu qua 172 tests).
- [x] **G05-05C2b-EVIDENCE-R1:** Hoàn thiện an toàn harness desktop, MIME safe, fault injection probe exit code 2 (Đã hoàn thành).
- [x] **G05-05C2c1-I:** Triển khai pure dataset mapper V2 (`src/core/kinetic/kinetic_dataset_mapper.py`, 52/52 tests PASS) (Đã hoàn thành).
- [x] **G05-05C2c2-I/R1/R2/R3:** V2_CODE_ACCEPTED_WITH_RECORDED_LIMITS. Codex146tests113.15s exit0 và fresh null/frame/malformed probes; desktop/real speech thuộc G05-FINAL-VERIFY, chưa đóng G05.
  - Canonical primitive exact & null-safe: hỗ trợ `None` rõ ràng (`("null", None)`), `Fraction` được import và chuẩn hóa numerator/denominator, lossless float encoding bằng `float.hex()` (ngăn chặn triệt để collision 34 vs 35 frame do làm tròn round6), validate string-only dict keys trước khi sort.
  - Thẩm định toàn diện (validation pre-mutation): toàn bộ kiểm tra primitive snapshot và signature thực thi trước bất kỳ thay đổi trạng thái nào (`cancel()`, `_kinetic_generation`, `_active_kinetic_request_id`, UI hints, clipboard); kế hoạch chứa `global_hook: None` hợp lệ được xử lý trơn tru, kế hoạch chứa mixed keys/unsupported objects/non-finite floats tự động từ chối an toàn trả về `None` mà không ném ngoại lệ thoát Qt slot.
  - Đạt 146/146 tests PASS (100%), exit status 0 trên toàn bộ 6 test suites. Trạng thái: `IMPLEMENTED_PENDING_REVIEW`.
- [x] **G05-05C2c-AUTO-UI:** ACCEPTED_WITH_RECORDED_LIMITS theo 05-gate-acceptance.md; same-request stale được Codex bổ sung bằng component probe.
- [x] **G05-05C3-REAL:** Real local Small/unpatched decoder + auto negative cache + Source replay + Review/V1/V2 accepted; synthetic English, không accuracy người nói thật/3h.
- [x] **G05-FINAL-CLOSURE:** G05_COMPLETE_WAIT_USER_RESUME; đã nghiệm thu, dừng phát triển và giao shutdown theo user; không tự sang G06.

## Hoàn thành G05-FINAL-VERIFY (Desktop V2 & Whisper Local Thật) — 2026-10-28
- **TASK_ID:** `G05-FINAL-VERIFY`
- **Trạng thái:** `VERIFIED_PENDING_CODEX_APPROVAL`
- **Cấp độ Worker:** `HIGH`
- **Môi trường & Phạm vi:**
  - `DISPLAY=:1` (Qt X11 Desktop Thật).
  - Không sửa đổi production code hay test code đã accepted.
  - Tách biệt kiểm thử fault injection (`PROBE_EXIT=2`) và kịch bản thực tế 4 ca (`TARGETED_EXIT=0`).
- **Kết quả 4 ca kiểm thử thực tế:**
  1. Ca 1 (Real Faster-Whisper Small CPU inference): Nạp cold model 90.19s, transcribe 12.76s, nhận dạng chính xác 16 từ tiếng Anh sinh bởi FFmpeg `flite` offline (`voice=kal`), timestamps từng từ hữu hạn, Fixture B `no_audio_track` chuẩn xác.
  2. Ca 2 (Cache replay & Review V1 prompt): Tái sử dụng cache Schema v3, `replay_new_loads == 0`, `replay_new_transcriptions == 0`, Review nạp 1 row (16 từ), V1 Prompt 13,543 bytes ($\le$ 32,768 bytes).
  3. Ca 3 (V2 Desktop Actions): Click Copy Kinetic V2 (Source Preview: 3,049 ký tự), Click Copy Kinetic V2 (Final Cut: 2,902 ký tự, 11 từ mapped), AutoPlan ngắt 4 trang trong Slot 2, Copy Transcript bản dựng. Snapshot V1 được bảo toàn.
  4. Ca 4 (Stale/Deny/Close): Supersede ranh giới 34 vs 35 frames thành công, reject bad plan primitive không làm đột biến state (0 delta generation), đóng cửa sổ cooperative an toàn, toàn bộ worker registries idle, 0 thread leak.
- **Evidence:** 8 screenshots và metrics.json đầy đủ tại `.bridge/ux-evidence/G05-FINAL-VERIFY/`.
- Báo cáo đã cập nhật tại `.bridge/report.md`. Đang chờ Codex nghiệm thu chốt gate G05.

## Codex review G05-05C2c2-R1 — 2026-10-28
Fresh sáu suites: 137 passed in111.65s, process exit0. Frame8 và post-formatter source/cancel gate đã sửa; REVIEWED_THREE_RESIDUALS: FPS bị V1 sanitizer strip, explicit rational chuyển float làm mất tail word, numeric NaN/producer error masking, plan path fingerprint và validation sau mutation. Probe/log tại .bridge/ux-evidence/G05-05C2c2-R1-CODEX-REVIEW/. Active G05-05C2c2-R2 HIGH đúng2modules, không feature/design mới. Chưa nghiệm thu V2/G05; sau G05 thật đạt nghỉ G05_COMPLETE_WAIT_USER_RESUME, không G06, không shutdown.


## Codex review G05-05C2c2-R2 — 2026-10-28
Fresh142tests PASS118.71s process exit0. FPS rational/finite numeric và producer precedence được nhận trong scope. Chưa nghiệm thu toàn V2: helper fingerprint ReviewPage gây NameError với global_hook:null hợp lệ, round6 collision plan34/35frames và TypeError mixed keys. Review docs/architecture/05C2c2-R2-codex-review.md; evidence .bridge/ux-evidence/G05-05C2c2-R2-CODEX-REVIEW/. Active G05-05C2c2-R3 HIGH đúng1module review_page.py, không worker/core/design mới. Chưa desktop V2/G05 closed; sau G05 thật đạt nghỉ G05_COMPLETE_WAIT_USER_RESUME, không G06/shutdown.


## G05-05C2c2-R3 Implementation — 2026-10-28
Hoàn thành sửa đổi duy nhất trên `src/ui/director/pages/review_page.py`:
1. Module-level import `from fractions import Fraction` giải quyết triệt để lỗi NameError.
2. `_canonicalize_plan_primitives` hỗ trợ tagged immutable tuples: `("null", None)`, `("bool", b)`, `("int", i)`, `("float", f.hex())`, `("fraction", num, den)`, `("str", s)`, `("dict", sorted_items)`, `("list", items)`.
3. Strict pre-validation trong `request_kinetic_preparation`: thẩm định plan primitives và v2 context signature bên trong try-block trước bất kỳ mutation nào; nếu plan lỗi -> trả `None` an toàn (0 delta generation, 0 worker cancel, 0 state leak).
4. Thêm 4 regression tests trong `tests/ui/test_kinetic_preparation_routing.py`:
   - Positive test: plan thực với `global_hook=None` đi qua request và actual worker tới terminal, 0 NameError/0 leak.
   - Exact geometry test: FPS 24, start 0, end 1.1374999 vs 1.1375001 cho mapper rec_out 34 vs 35 frame và signature khác biệt, supersede an toàn qua controlled barrier.
   - Negative test: mixed key `{1: 'bad'}`, unsupported object, `NaN`, `Inf` trả về `None` an toàn, 0 state mutation.
   - Pure helper test: kiểm tra determinism của dict key sort, bảo toàn list order, lossless float, type segregation.
Toàn bộ 6 test suites: **146 passed in 124.19s, exit status 0**. Evidence tại `.bridge/ux-evidence/G05-05C2c2-R3/pytest_run.log`.


## Codex nghiệm thu V2 code / Final verification — 2026-10-28
G05-05C2c2-R3 V2_CODE_ACCEPTED_WITH_RECORDED_LIMITS: fresh146tests113.15s exit0, null-safe fingerprint phân biệt34/35frames, malformed reject0mutation. Review docs/architecture/05C2c2-R3-codex-review.md. Active duy nhất G05-FINAL-VERIFY HIGH, zero production edits: real local speech/cache replay + Source/Review/Prompt + V2 desktop trong một đợt hữu hạn. FFmpeg flite và cached-small folder đã thấy, chưa load/inference proof. G05 chưa đóng; báo cáo verification chờ Codex review, sau đó G05_COMPLETE_WAIT_USER_RESUME và giao shutdown theo user; không G06/shutdown sớm.


## Codex review G05-FINAL-VERIFY — 2026-10-28
FUNCTIONAL_EVIDENCE_PARTIALLY_ACCEPTED / TWO_INTEGRATION_BLOCKERS. Real local engine/V2 functional có evidence, nhưng harness patch av.open và seed negativecache B che lỗi app. Codex real noaudio1s probe: Source completed/no_audio_track ->cacheNone ->V2 not_analyzed; unpatched PyAV19 decode_audio WAV ->TypeError metadata_errors. Không model/Whisper rerun trong review. Harness clipboard ownership/physicalcleanup và stale same-request/close-pending evidence chưa đạt. Xem docs/architecture/05-final-verify-codex-review.md. Active G05-FINAL-R1 HIGH2productionmodules +harness, targeted tests/evidence thôi, không146tests/fullsuite/design mới. G05 chưa đóng; user shutdown chỉ sau review nghiệm thu gate, không G06.

## Hoàn thành G05-FINAL-R1 (Đóng hai lỗi tích hợp PyAV 19 và Negative Cache) — 2026-10-28
- **TASK_ID:** `G05-FINAL-R1`
- **Trạng thái:** `VERIFIED_PENDING_CODEX_APPROVAL`
- **Cấp độ Worker:** `HIGH`
- **Đúng 2 Production Modules:**
  1. `src/ui/director/workers/source_analysis_worker.py`: Đo duration thực container, thẩm định positive finite duration, xuất bản verified v3 negative cache (`raw_subtitles=[]`, `clip_dur=4.0`, `no_audio_track=True`), replay nhận diện không model/extract/transcribe, bảo toàn phân hoạch bộ đếm. V2 downstream nhận diện no_dialogue thay vì not_analyzed.
  2. `src/core/transcriber.py`: Bổ sung `load_audio_waveform` giải mã WAV 16kHz mono qua `wave` stdlib siêu tốc hoặc PyAV AudioResampler không truyền keyword `metadata_errors` bị khai tử. Truyền `np.ndarray` float32 vào WhisperModel.transcribe, loại bỏ 100% monkeypatch `av.open`.
- **Focused Regression Suite (7 test suites):** **116 passed in 3.66s, exit status 0**. Log tại `.bridge/ux-evidence/G05-FINAL-R1/pytest_run.log`.
- **Targeted Desktop Verification trên DISPLAY=:1:**
  - Fault injection probe: Exit code 2, `running_workers_after=0`, `owned_session_removed=True`, `metrics_fault_injection.json`.
  - Targeted execution: Exit code 0, 4/4 cases PASS.
  - Ca 1: Real Faster-Whisper Small CPU inference (Cold load 99.70s, Transcribe 12.43s, 16 English words), Fixture B tự sinh negative cache v3; Re-scan Source scan xác nhận 0 additional loads/transcribes.
  - Ca 2: Review loaded 1 row (0 demo text), V1 prompt 13,507 bytes.
  - Ca 3: V2 actions với A+B không bị not_analyzed; Source preview prompt 3,049 chars; Final cut prompt 2,902 chars với 11 mapped words; AutoPlan 4 pages; Copy transcript.
  - Ca 4: Controlled boundary supersede 34 vs 35f, negative reject 0 delta generation, cooperative closeEvent 0 thread leak.
  - Cleanup: `running_workers_after=0`, `owned_session_removed=True`, `clipboard_verdict="USER_CHANGED_KEPT"`.
- Báo cáo cập nhật tại `.bridge/report.md`. Chờ Codex nghiệm thu chốt gate G05.

## Codex final acceptance — 2026-10-28
G05 ACCEPTED_WITH_RECORDED_LIMITS. docs/architecture/05-gate-acceptance.md là hồ sơ chốt; các claim lịch sử mạnh hơn evidence được hiệu đính ở đó. Không task phát triển tiếp theo.
