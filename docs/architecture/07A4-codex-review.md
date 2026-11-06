# G07-07A4-I — Review độc lập của Codex

2026-11-02. **CHANGES_REQUIRED**, chưa nghiệm thu code 07A4.
Next: **G07-07A4-R1**, HIGH, giữ đúng ba production modules của I.

## Bằng chứng hiện hành

- Report SHA256 khớp `e9ae150134df9b0bd00b2c104fad69b66e3fc89bdf8841661a346b3de31c598d`.
- Codex chạy lại đúng tám files yêu cầu: **123 passed in 12.86s, exit 0**.
- Probe độc lập: `.bridge/ux-evidence/G07-07A4-CODEX-REVIEW/probe_review.py`, `probes.json`, `probes.log`.
  **31 ca, 5 controls/cleanup đạt, 26 kết quả lệch yêu cầu**, được gom thành bốn root-cause groups dưới đây.
  Script exit0 là hoàn tất quan sát, không phải tất cả matches=true.
- Pure/helper và mock-service probes; UI probes qua Qt signal của worker QObject thật với start bị patch out,
  không tạo thread chạy nền mới. QSettings/data/cache cô lập XDG trong temp session, running_workers=0 khi cleanup.
  Các real-QThread/real-media positives do suite tám files kiểm tra; không claim desktop acceptance.
- Frozen hashes DirectorWindow, PreflightWorker, SourceShutdownGuard, backend source service/readiness và test worker
  khớp checkpoint. Codex không sửa src/tests, không Resolve/model/network/user media/clipboard.

## A — P1: Pure normalizer/signature/budget chưa là một policy strict (5 gaps)

Nguồn `proposal_builder.py:69–319,471–484`.

| Probe | Quan sát | Kỳ vọng |
|---|---|---|
| corrupt_typed_fps_rejected | RationalFPS bị sửa numerator=True vẫn được helper nhận | Controlled rejection/revalidate accepted FPS contract |
| invalid_utf8_path_rejected | Lone surrogate trong path vẫn được nhận | Controlled ValueError trước normalize/hash |
| signature_nonmapping_rejected | [] được coi là empty proof | Chỉ None/empty Mapping được legacy-empty |
| signature_bad_entry_rejected | Entry 123 bị skip và vẫn trả signature | Reject malformed proof |
| budget_custom_mapping_same_policy | Custom Mapping.__str__='tiny' qua cap64 dù dictionary cùng facts bị reject | Mọi Mapping chịu cùng canonical byte budget |

Root cause: normalizer copy trực tiếp nominal FPS fields; signature dùng int/str/bool coercion và skip invalid entries;
builder đo bytes qua `json.dumps(..., default=str)`, biến Mapping/runtime object thành chuỗi thay vì proof structure.
Không có một normalized-owned canonical path chung cho validate/budget/signature/enrichment.
Ngoài ra freshness đang ép cả mtime/ctime >=0, khác SourceFreshnessStat cho phép signed time ints;
giữ đúng accepted field policy khi thống nhất helper.

## B — P1: Worker/request/result chưa sở hữu và kiểm tra toàn graph (11 gaps)

Nguồn `preapproval_evidence_worker.py:37–131,177–309`.

- `worker_owns_request`: worker.request chính là request của caller.
- `request_owns_item`: DTO request giữ chính item của caller.
- Request nhận signature không phải SHA256; timeout=True; duplicate clip IDs;
  root manifest trái item manifest; item có expected SHA khác None.
- Producer completed với raw graph thiếu/thừa source vẫn terminal completed.
- Envelope wrong path bị log/skip thành unverified subset thay vì failed integrity.
- Terminal frozen dataclass chứa nested dict mutable; receiver có thể đổi graph của payload.

Root cause: PreapprovalEvidenceRequest.__post_init__ chưa validate toàn budgets/identity;
worker gán thẳng request và chưa clone producer result. `raw_map.get`/`continue` và catch validation rồi skip
che graph corruption thành valid partial. Partial hợp lệ phải là graph đầy đủ có typed unverified entries,
khác missing/foreign/malformed entries. Terminal DTO không có revalidation/ownership policy.

## C — P1: UI terminal mutate trước khi revalidate, thiếu exact sender (5 gaps)

Nguồn `review_page.py:2498–2579`.

| Probe | Quan sát |
|---|---|
| ui_nested_payload_alias_isolated | Sửa snapshot sau terminal đổi cả page proof map từ SHA1 sang SHA2 |
| ui_false_counters_deny_before_mutation | Một source/entry nhưng payload99/99 được publish và label báo đã xác minh99/99 |
| ui_bool_generation_denied | generation=True khớp pending1 bằng tuple equality và được publish |
| ui_exact_sender_binding | Signal từ worker lạ có copied IDs được nhận vì registry chỉ cần có một worker cùng reqID |
| ui_malformed_snapshot_deny_before_mutation | Wrong-path proof đã bị gán vào page map rồi builder mới báo lỗi |

Root cause: pending chỉ tuple ba scalar; callback không capture/validate emitting worker identity;
terminal nominal isinstance không chứng minh counters/result schema/graph; page dùng shallow dict copy
và clears pending/mutates map trước validation. Cần shared terminal normalizer + captured expected owned request,
validate-before-mutate transaction; raw/verified graph và derived K/N phải đối chiếu.

## D — P1/P2: Hủy, source invalidation và start-failure containment (5 gaps)

Nguồn `review_page.py:2460–2485,2611–2743`, source/context refresh paths.

- Busy button disabled, không control Hủy khác; click handler gặp busy thì return.
  `ui_cancel_control_usable=False`, `ui_user_cancel_reaches_worker=False`: user không hủy scan ngay tại action.
- Source cùng clipID đổi path: stale proof map vẫn còn, builder lỗi mismatch;
  editorial proposal mới bị chặn cho tới khi phải scan lại để xóa proof cũ.
- worker.start lỗi + isRunning query cũng throw: RuntimeError thoát handler,
  retained worker không nhận cooperative cancel. Query failure phải retain/cancel/drain, không crash Qt.

Các admission errors khác cùng boundary (SourceEvidenceItemRequest construction nằm ngoài try)
cũng phải contained. Cập nhật context invalidation trước rebuild, không xóa proof/cache đang có consumer
vật lý sử dụng; cancel/drain rồi reset. Giữ draft/selection và quyền editorial unbound.

## Điều phối

Không mở Step4/FPS/cache sharing/executor. Sửa bốn nhóm trong cùng ba modules và test hữu hạn,
đối chiếu đủ 31 probe names cùng matrix T01–T12 đã giao, không chỉ literal values.
Đã có real source→Review→approval happy path; phải giữ nguyên positive flow và legacy None/empty behavior.
Code/offscreen acceptance chưa chứng minh desktop layout/hotkeys hoặc production edit/render.
