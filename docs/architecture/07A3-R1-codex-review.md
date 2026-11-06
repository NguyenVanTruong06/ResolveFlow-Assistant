# G07-07A3-R1 — Review độc lập, 2026-11-02

**Quyết định: CHANGES_REQUIRED.** Nghiệm thu việc sửa 23 probe cũ trong phạm vi đã đo;
chưa nghiệm thu toàn G07-07A3/M1. Active tiếp theo: **G07-07A3-R2**, HIGH, cùng ba production modules.

## Bằng chứng mới

- Report SHA-256 khớp: `8293fa9a0efd9c1d562b427422034137f3ea8d140b4f3c8670b7bfbc2ae432ad`.
- Codex chạy lại năm files quy định: **200 passed in 4.17s, exit 0**.
- Copy nguyên probe script review trước vào thư mục evidence mới rồi chạy: **23/23 matches**, exit 0.
  Không thay expected để đạt kết quả. Xác nhận các lỗi cũ về last duration, truncated EOF,
  cancel post-stat, cache header re-probe và incomplete proof đã được sửa cho các ca đó.
- Review tiếp đúng bảng finite A–D của workorder R1: **25 ca residual, 5 đạt / 20 lệch yêu cầu**.
  Có controls parser/consumer/worker/catalog hợp lệ đạt; foreign manifest bị chặn đúng.
- Tất cả trong `.bridge/ux-evidence/G07-07A3-R1-CODEX-REVIEW/`:
  `pytest_run.log`, `probe_review.py`, `probes.json`, `residual_probes.py`, `residuals.json`, `residuals.log`.
- Probe scripts exit 0 nghĩa là đã thu thập xong quan sát, không đồng nghĩa tất cả `matches=true`.
- Không sửa src/tests; temp media/child scripts cô lập, không Resolve/model/network/user library.
  DirectorWindow/SourceShutdownGuard/BuildPage hash giữ nguyên checkpoint.

## A — Frame parser còn chứng nhận sai (7 negative cases)

Nguồn `source_evidence_service.py:450–577,647–659`.

| Probe | Thực tế | Yêu cầu R1 |
|---|---|---|
| fractional_pts | pts=0.2/1.2/2.2 và duration=1.8 bị int() thành CFR | Không chứng nhận integer timing giả |
| wrong_root_key | root `not_frames` cũng được VERIFIED_CFR | Chỉ danh sách frames đúng schema |
| missing_record_commas | Các object không có dấu phẩy vẫn VERIFIED_CFR | Reject JSON sai cú pháp |
| trailing_garbage | JSON + ` garbage` vẫn VERIFIED_CFR | Clean structural EOF, không trailing data |
| string_record_over_16k | Mỗi frame có string 20,000 bytes vẫn VERIFIED_CFR | Per-record cap 16KiB trên mọi byte/nhánh |
| unlisted_decoder_stderr | `[h264] Invalid NAL unit size (0 > 42)` + exit 0 vẫn VERIFIED_CFR | Không decoder error |
| stderr_reader_exception | Reader.exception=OSError nhưng vẫn VERIFIED_CFR | Reader failure không complete |

Máy đếm ngoặc hiện chỉ thêm flags; chưa là JSON grammar validation. Nhánh `in_string` continue
trước byte-budget check. `int()` vẫn silently truncates. Việc nhận lỗi stderr qua sáu mẫu chuỗi
không thay thế kiểm tra toàn bộ error output ở loglevel=error và reader completion/exception/overflow.
Đây là checklist A đã giao, không thêm định dạng/feature mới.

## B — Cache selection và deadline (2 negative cases)

Nguồn `source_evidence_service.py:896–930,1152–1223`.

- `explicit_stream_must_not_poison_default`: chạy explicit global stream 1 rồi default None
  nhận stream 1 từ cache, dù header selector None sẽ chọn stream 0. `_default_stream_map`
  bị ghi cả khi caller chọn explicit stream. Không được coi explicit selection là default-discovery proof.
- `poststat_deadline_cache_count`: fake monotonic đổi từ 0 sang 2s tại post-stat, timeout=1s:
  vẫn publish complete proof và cache count=1. Đã check cancel, chưa check deadline trước publish/cache.
  Hash cancellation wrapper đã bổ sung deadline là cải thiện được ghi nhận; cần giữ phân loại timeout riêng.

## C — Public entry và ownership (4 negative cases)

Nguồn `source_evidence_service.py:319–340,796–830`.

- `public_entry_mutated_item_preio`: typed item bị sửa stream_index=True sau construction vẫn thực hiện 2 stats.
- `public_entry_upper_budget_preio`: typed stdout budget=10**12 vẫn thực hiện 2 stats; entry chỉ check min,
  mất upper bound so với constructor.
- `public_entry_missing_items_controlled`: xóa required field items gây AttributeError, không controlled ValueError.
- `batch_result_owns_envelope`: result vẫn giữ nguyên envelope instance caller (`is` True),
  chưa deep-owned/revalidated graph. Constructor item clone đã tốt nhưng chưa đóng public method/result boundary.

Yêu cầu chính là capture một owned validated request/result qua cùng normalization policy trước I/O/use,
không chắp từng điều kiện chỉ để qua một giá trị probe. Giữ all-fields schema/caps/types hiện hành.

## D — Terminal binding và catalog cuối pha deep (7 negative cases)

Nguồn `source_catalog_readiness.py:1610–1659`; `preflight_worker.py:299–339`.

| Probe | Thực tế |
|---|---|
| consumer_wrong_five_stat | Envelope có ctime khác source vẫn checks_passed=True |
| worker_wrong_path | Inspector result đổi evidence path vẫn terminal checks_passed=True |
| worker_wrong_stream | Inspector result stream 1 trái request stream 0 vẫn pass |
| worker_wrong_digest | Inspector result observed SHA sai expected vẫn pass |
| worker_wrong_freshness | Inspector result five-stat khác source observations vẫn pass |
| deep_final_master | SFX master đổi trong deep source scan vẫn pass |
| deep_final_registry | Registry đổi trong deep source scan vẫn pass |

Consumer kiểm tra freshness không None nhưng không đối chiếu envelope five-stat với version nguồn
đã kiểm tra. Worker bổ sung complete/policy/non-None nhưng vẫn bỏ path/stream/SHA/freshness binding.
Final recheck sau deep chỉ xét source, chưa xét master/marker/registry/config catalog authority.
Các recheck cũ chạy trước deep nên không còn đủ cho phase mới kéo dài.

Sửa bằng validator chung trong allowlist, dùng cùng policy tại core và worker terminal;
không nhân thêm bộ if khác nhau. Dùng exact owned proposal và source/catalog observations,
không tin expected echo của producer, không approval mutation hoặc omission tự động.

## Ranh giới và kết luận

23 probe cũ đạt là bằng chứng thật, không rollback các sửa đúng. 20 residuals trên nằm trong checklist R1
đã giao (integer/grammar/record bounds/reader failure, default selection, deadline, strict entry/clone,
terminal binding và final catalog). Không mở scope mới hoặc test fuzz toàn repo.
ready_to_execute=False và UI/geometry contracts/Resolve locks giữ nguyên. Tài liệu thiết kế hiện còn header
DESIGN_READY/proposed và không phản ánh đủ triển khai; cập nhật trung thực cùng repair, không thêm vòng design-only.
