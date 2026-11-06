# G07-07A4-R1 — Review độc lập, 2026-11-02

**CHANGES_REQUIRED.** Xác nhận 31 probes cũ đã sửa đúng; chưa nghiệm thu toàn checklist 07A4.
Next: **G07-07A4-R2**, HIGH, cùng ba production modules. Không mở Step4/FPS/executor.

## Fresh evidence

- Report SHA256 khớp `acb37dd2f44b09cd626a1267807474a28624ffdc9bce2c43c45c6492d702c4ec`.
- Codex chạy lại tám files quy định: **128 passed in 12.85s, exit0**.
- Copy nguyên probe script review I chạy độc lập: **31/31 matches**, exit0.
- Kiểm tra các nhánh còn lại của workorder R1: **21 ca,3 positive/cleanup controls đạt,18 gaps**.
  Evidence `.bridge/ux-evidence/G07-07A4-R1-CODEX-REVIEW/`:
  `pytest_run.log`, `probe_review.py`/`probes.json`/`.log`, `residual_probes.py`/`residuals.json`/`.log`.
  Script exit0 là hoàn tất quan sát, không có nghĩa mọi matches=true.
- Không src/test edits bởi Codex. Isolated XDG/temp fixtures, worker.run đồng bộ hoặc start patched-out,
  running_workers=0 khi cleanup; real QThread/media positives nằm trong suite. Không desktop/Resolve/model/network claim.

## A — Canonical policy chưa chung cho budget/signature (3 gaps)

Nguồn `proposal_builder.py:280–325,427–440,493–507`.

- `signature_uses_byte_budget`: cap64 bytes nhưng signature vẫn hash snapshot lớn hơn cap, không ValueError.
- `signature_count_precheck`: Mapping4097 entries bị enumerate4097 lần trước validation,
  thay vì reject trước iteration.
- `canonical_utf8_equality_accepted`: snapshot có path tiếng Việt, budget đúng canonical UTF-8 bytes,
  builder vẫn reject vì json.dumps mặc định ensure_ascii=True tính escaped ASCII lớn hơn.

Builder có guard riêng; signature vẫn không có cùng limits. `_to_canonical_mapping_dict` generic recursion,
str(key), dataclass traversal không thay thế shared fixed-schema normalized representation.
R1 đã yêu cầu một đường validate/own/canonicalize/budget/hash, equality accepted, không stringify/coercion.

## B — Request/backend limits và producer normalization (8 gaps)

Nguồn `preapproval_evidence_worker.py:63–200,244–404`.

| Probe | Quan sát | Kỳ vọng |
|---|---|---|
| request_bigint_timeout_controlled | OverflowError | Controlled ValueError |
| request_timeout_backend_upper_bound | timeout301 được nhận | Backend max300, reject trước factory |
| request_stdout_backend_lower_bound | stdout1 được nhận | Backend min1024, reject trước factory |
| invalid_budget_before_factory | factory đã gọi1 lần rồi mới fail batch request | factory0 |
| deleted_request_field_not_defaulted | Xóa max_decoded_frames bị class default lấp vào | Reject missing member |
| request_accessor_cannot_change_internal | object.__setattr__ trên worker.request làm internal items từ1 về0 | Accessor không lộ mutable alias |
| unknown_producer_status_failed | Unknown nominal batch status vẫn terminal completed | Failed integrity |
| malformed_cfr_not_partial_completed | CFR envelope có SHA='bad' bị log/skip thành completed0/N | Failed integrity |

Positive legitimate HEADER_REPORTED_ONLY graph vẫn completed0/1, cần giữ.
Cloning request là cải thiện thật, nhưng public accessor trả internal object; DTO bounds vẫn chỉ >0,
và worker validate backend bounds sau factory. Nominal result chưa được rehydrate/revalidate toàn schema;
catch validation rồi skip vẫn che corruption thành partial.

## C — Thiếu shared terminal normalizer/raw-vs-verified binding (6 gaps)

Nguồn `review_page.py:2513–2650`, `PreapprovalEvidenceTerminalPayload` không có validation.

- `terminal_missing_raw_denied`: raw map={} với verified snapshot hợp lệ vẫn được publish.
- `terminal_raw_verified_digest_disagreement_denied`: raw SHA2 nhưng verified SHA1 vẫn được publish.
- `terminal_legacy_pending_downgrade_denied`: pending tuple3 thiếu source specs vẫn được nhận và total99 bỏ qua.
- `terminal_current_generation_gate`: page generation đã tăng, payload/pending cũ vẫn được publish.
- `terminal_bad_diagnostics_contained`: typed payload bị sửa diagnostics=5 làm TypeError thoát slot.
- `terminal_bad_diagnostics_atomic`: cùng lỗi đó đã gán proof map trước khi TypeError.

Positive valid terminal/raw graph đạt. Sender check và map alias isolation cũ đã tốt,
nhưng raw graph vẫn bị bỏ qua, fallback tuple3 hạ cấp authority, không validate generation hiện hành.
R1 yêu cầu normalizer chung tại worker và UI, đối soát derived verified subset/counters,
và whole boundary containment trước mutate; việc thêm vài if chưa đóng yêu cầu này.

## D — Cache reset theo source context chưa có (1 gap)

`source_reset_clears_idle_service`: khi source path đổi và page đã physical idle,
proof map được xóa nhưng Review-owned service vẫn giữ nguyên, không clear hay replace.
Probe chấp nhận cả clear lẫn replace; thực tế không có bên nào.
Đóng cùng source invalidation path đã giao; active service thì chỉ reset sau cancel/drain.

## Quyết định

Không phủ nhận 128tests/31probes đã xanh. Các18gaps này là nhánh R1 A–D còn thiếu,
không feature mới và không fuzz toàn repository. Sửa bằng shared bounded canonical proof policy,
backend-aligned request normalization, shared complete terminal normalization và source-session reset.
Backend07A3/Director guards vẫn frozen/accepted; ready_to_execute=False; chưa UI desktop acceptance.
