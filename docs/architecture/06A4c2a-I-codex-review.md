# G06-06A4c2a-I — Codex review, 2026-10-29

**CHANGES_REQUESTED** trong cùng worker module. Chưa mở UI/clipboard. Report SHA256 đã đối chiếu: `117fe6c02a51b96b9847749e94b8d169d81440d3ffc91e608414bbca6678cec5`.

## Đã đạt và giới hạn bằng chứng

Codex đọc worker/helpers/tests, chạy đúng finite suite: **90 passed in 2.20s, exit 0**. Nhánh overview có artifact riêng, helper so sánh transport với artifact, pre/post baseline loops và cancel gates. Tamper prompt rồi tự tính lại hash bị so với artifact thay vì chỉ tin result. Core formatter giữ nghiệm thu.

Đếm từ log thực: worker16, core overview37, core draft17, intake20 =90. Bảng report18/40/16 cho ba file cuối không đúng. Pha đỏ mô tả sửa fixture chưa chứng minh production regression đỏ→xanh. Không bác bỏ test xanh, nhưng không suy rộng thành mọi acceptance đã exercise.

## A — Required bindings còn fail-open

Actual QThread probes với snapshot/context/entry hợp lệ cho thấy những ca sau vẫn success và publish artifact:

- Không có `expected_ui_context_signature`.
- Snapshot mất `ui_context_signature` nhưng request có expected UI signature.
- Request/context budget32767 trong khi snapshot budget32768.
- Request style rỗng trong khi context style travel_vlog (nhánh `if req.style_intent` bỏ qua đối chiếu).
- request_id rỗng.

Các expected UI signature checks đang có điều kiện/fallback; artifact còn dùng `snapshot.ui_context_signature or snapshot.evidence_context_signature`. Capture proof không được thay bằng evidence signature khi thiếu UI proof. New intent phải giữ một policy rõ ràng, không mở lại legacy routes.

## B — Public ledger signature chưa strict trước thao tác nguy hiểm

`compute_project_overview_ledger_signature` lookup batch ID trước type gate; `dataclasses.replace(entry,batch_id=[])` ném TypeError thay ValueError. batch_id=None lại trả hash. segments=None ném TypeError từ to_dict; total_selected_duration_sec=10**400 ném OverflowError; bool duration/count lại trả signature. Helper tương lai chạy trên GUI phải fail-closed có kiểm soát theo schema, không leak hoặc silently accept các dạng này. Semantic digest mismatch có thể hash để phát hiện khác biệt; shape/type invalid không được nhận chỉ vì json.dumps chấp nhận bool/null.

## C — Owned proof và test lifecycle

- `validate_project_overview_result_against_artifact` coi max_prompt_bytes optional; bỏ field khỏi result hợp lệ vẫn True. Budget đã chốt là required envelope field.
- Actual worker probe với UI signature nested list cho thấy request và artifact đều giữ alias của caller. Caller append list sau worker hoàn thành làm artifact.context_signature đổi. Frozen outer artifact chưa tạo owned immutable proof.
- Test `test_worker_overview_source_pre_and_post_recheck` chỉ xóa file trước run, rồi sửa size trước run thứ hai. Chưa exercise post-render mutation. `test_worker_overview_cancel_lifecycle` chỉ cancel trước start và có context thiếu batches; chưa chứng minh in-flight cancellation/exception cleanup. Cần bổ sung đúng controlled boundaries, không cần desktop.

Artifact `.bridge/ux-evidence/G06-06A4c2a-I-CODEX-REVIEW/residual-probes.json` chứa positive actual-QThread success, các bypass, signature errors, missing-budget acceptance, mutable-proof probe và count theo log.

## Lát cắt sửa

**G06-06A4c2a-R1**, production chỉ `prompt_preparation_worker.py`, tests hiện có và docs. Chốt required UI signature không fallback, exact style và budget ba chiều, strict ledger field gate, own context signatures và mandatory result budget. Thêm actual post-format mutation/cancel tests với positive controls và finally cleanup. Không UI/macro guards/formatter changes hoặc full repository suite.
