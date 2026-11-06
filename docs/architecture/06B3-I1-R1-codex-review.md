# G06-06B3-I1-R1 — Residual closure, chưa nghiệm thu

2026-10-29. Report `678331c573065a9c57986ca3206dc1ddce812b929bd248836fcf982e92c5ad79` đã đọc/hash verified. Codex fresh finite suite bốn files: **121 passed in0.56s, exit0**. Numeric extraction A đã sửa; wrapper Dual và query stem precedence cũ đã sửa; các case duplicate/dropped IDs có guard. Không mở lại các phần đã đạt. Chưa accept toàn adapter vì các nhánh validation C/D vẫn chưa triển khai đủ.

Evidence `.bridge/ux-evidence/G06-06B3-I1-R1-CODEX-REVIEW/probes.json`. Review không sửa production/tests. Root cause chung: __post_init__ và assemble có các bản validation riêng, cả hai đều thiếu một phần; tests chỉ kiểm tra vài tên probe. Một test bad-result-ID dùng FakeResult nên chỉ chứng minh nominal type gate, không chứng minh typed result có malformed ID.

## R2-A — Shared validation cho batch/demand/request (P1)
- Typed demand timeline_sec=True hoặc eligible rejection_reason='ERR_DEMAND_INVALID' vẫn assemble thành công.
- Typed request request_id=[] gây TypeError trong dict lookup; top_k=False vẫn được assemble dù shared validator06B1 đã có.
- Unknown status bị chặn khi nó làm lệch request count, nhưng cùng unknown status + requests=() vẫn xuất sidecar total1/partition_sum0. Không phải status revalidation thực.

Sửa bằng một nguồn validation dùng chung cho constructor và assemble: validate item/context/graph trước encode/hash/getattr; shared request validator trước map. Validate toàn consumed item scalar/Optional fields, enum/status-reason, UTF-8, finite placement metadata, slot/index và canonical ID relation/revision/order/bijection/limits theo bindings. Không dùng len/bijection làm thay thế status/type gate. Noneligible items vẫn có metadata hợp lệ tùy status, không bắt invalid demand có query/category giả. Candidate result vẫn là advisory, không thêm cryptographic/raw-plan proof.

## R2-B — Complete candidate boundary và owned output (P1)
Code khác report: report nói candidate chỉ online/offline và duration>0, source thật cho missing/staging/unknown và duration>=0. Probe các states đó vẫn matched1; duration0 matched1. duration10**400 gây OverflowError tại math.isfinite. asset_id surrogate, matched_signals list và pack_name list đều lọt vào sidecar, trái UTF-8/owned immutable boundary.

Giới hạn states candidate chỉ online/offline, không thay state enum của catalog. Duration None hoặc finite positive, numeric conversion guarded, không bool/overflow. Validate tất cả consumed candidate strings/provenance theo Optional contract, tuple[str] matched_signals, scalar flags/score/source/category và existing result consistency/warnings/counts. UTF-8 gate trước output; mutable list/dict/object reject ValueError, không sanitize ID thành ID mới hoặc deepcopy runtime handle. Actual matcher-generated candidates và optional provenance None tiếp tục hợp lệ; không đòi thêm persisted schema hoặc authority IO.

## R2-C — Known raw-video envelope (P2)
`{'type':'video_plan','data':{'broll_inserts':[... ]}}` vẫn trả empty batch; đây chính là wrapper hiện hành mà Review phải unwrap trước adapter. Chặn shape type=video_plan + data envelope, cùng actual known discriminator kind='project_overview_draft' và $schema='chundvc_batch_draft_v1' khi hiện hữu; typegate strings trước membership. Không chặn mọi raw plan có metadata type='video_plan' nếu nó đã chứa trực tiếp inserts. Raw{} vẫn positive.

## Closure / next design
Gom ba residual families trong đúng hai modules; test bằng actual typed DTO bị sửa bằng replace/copy/setattr, không dùng FakeResult thay case cần revalidation. Parameterize input families, shared validators để tránh constructor/assembly drift. Report exact tests/source behavior, không '100%' khi fields chưa verify.

Sau R2 locally xanh cho phép design-only I2 về worker/Review wiring: owned plan revision, fresh StorageManager off-thread, actual error propagation, plan/root freshness, one-terminal vs physical finish, UI advisory. Chưa implement I2 hoặc tự accept gate. Không thêm phantom signals/source authority/performance claims. Accepted06B1/B2 và Drive/backups giữ nguyên.
