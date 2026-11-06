# G06-06A1-R1 — Codex review, 2026-10-28

**A/B ACCEPTED IN SCOPE; RESIDUAL VALIDATION/BOUNDS NEEDS FIX.** Chưa nghiệm thu toàn module.

Fresh test: PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_batch_planner.py -q -> **35 passed in0.42s, exit0**. Đọc report, module và tests. Không app/model/IO nguồn.

Probe cũ đã xanh: thay text đổi signature; source_out1.1374999 giữ nguyên khi export; rollover được reject với ValueError ở actual index; hai0.5000004s được tách thành hai batch không vượthard1.0. Giữ các sửa A/B, không giao lại hoặc mở rộng tính năng.

## Residual thuộc đúng hợp đồng strict input cũ

1. **Overflow ở đường Mapping:** raw window start/end=10**400 vẫn bị float() trước EvidenceWindowSummary validation, nên ném OverflowError, không controlledValueError. Constructor đã bắt overflow nhưng caller Mapping chưa dùng gate đó đúng thứ tự.
2. **Skipped clip che malformed fields:** duration0 + speech_status=False vẫn accepted/unaccounted; source_missing + has_audio='bad' cũng accepted. R1 đã yêu cầu validate mọi trường hiện diện trước fallback/skip. Vẫn cần validate normalized snapshot shape/types trước quyết định không lập timed item; valid missing analysis/unknown duration vẫn giữ accounting cũ.
3. **Normalization thay identity/metadata:** manifest_id=' M ' bị biến thành 'M'; project_name=None thành ''; file_name=None tự thành 'C01.mp4'. Contract yêu cầu raw strings hợp lệ giữ nguyên, None không thành text/filename giả. Blank ID được reject bằng strip-check nhưng không strip giá trị được giữ.
4. **Window bound vẫn có epsilon và one-past-end:** end1.000001 với clipdur1 được accepted do c_dur+1e-5. Point t=duration1 cũng được đưa vào item cuối dù rule là0<=t<duration. Positive ranges end==duration hợp lệ, nhưng end>duration không. Point ở partition boundary nằm bên phải như tests cũ vẫn giữ.

Counterexamples chạy pure/in-memory, no I/O, process exit0. Evidence .bridge/ux-evidence/G06-06A1-R1-CODEX-REVIEW/.

## Quyết định hữu hạn

G06-06A1-R2 chỉ hoàn thiện một cụm raw validation/window bounds trong module hiện hành; focused regression đúng các nhánh trên. Không sửa thuật toán signature/budget đã đạt, không design/ledger/summary/UI/matcher. Sau khi contract này được nghiệm thu mới giao06A2.
