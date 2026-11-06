# G06-06A2-R2 — Nghiệm thu adapter evidence pure

Ngày review: 2026-10-28. **CODE_ACCEPTED_WITH_RECORDED_LIMITS** cho G06-06A2 và các bản sửa I/R1/R2 trong phạm vi đã giao.

## Bằng chứng hiện tại

- Codex chạy đúng hai file test: PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_batch_evidence_adapter.py tests/core/test_batch_planner.py -q → **94 passed in 0.74s, exit 0** (55 adapter +39 planner).
- Đối chiếu code R2 với review R1: canonical observation_kind='segment' được nhận, timing aliases dùng normalized values, coverage Mapping được chuyển thành owned data, known nested metadata validation trước source gating, generated signatures chứa manifest identity và declared source/audio/visual statuses.
- Probe độc lập dùng trực tiếp pure VisualIndex.validate_segment + coverage MappingProxyType → adapter → A1 tạo hai windows (speech + machine range); nullable start_sec=None fallback start=1 đúng; speech ' exact ' giữ nguyên whitespace.
- Probe explicit audio_authority=None và audio_analysis_status=None đều ra unscanned/has_audio=None/0 speech windows, không nâng quyền. Enum bogus của hai field đều bị ValueError. Không mở lại residual null đã được xử lý an toàn.
- Supplied signature vẫn là audit reference, generated signature là fingerprint dữ liệu; identity/source availability không suy ra từ sampled hash. No-audio False khác completed-empty None, source unavailable loại evidence, user notes còn được giữ khi machine stale và nguồn hợp lệ.

Log Codex: .bridge/ux-evidence/G06-06A2-R2-CODEX-REVIEW/.

## Giới hạn nghiệm thu

Đây là pure in-memory adapter + contract integration với A1, chưa consumer runtime/GUI batch workflow, không kiểm thử file nguồn thật hoặc tái xác minh nguồn ở adapter. G05 caller vẫn sở hữu stat/fullSHA/authority và final source checks. Signature không cấp quyền persistent cache reuse. Whitespace word count là estimate, không dùng làm word timestamps V2.

Hard cap32768 bytes của A1 chỉ dành cho evidence envelope do A1 render; chưa bảo đảm prompt V1 hoàn chỉnh với instructions/context vừa giới hạn. Không truncate evidence để tuyên bố đã đạt. Dense indivisible evidence vẫn có thể trả explicit budget error. Chưa queue/ledger/resume/hierarchical summary/matching/EditPlan/Resolve execution.

Các kiểm thử không chứng minh UI/desktop/performance ở quy mô 3 giờ footage. Không yêu cầu desktop test cho module pure này. Không chạy full tests/app/model/Resolve, không sửa production/tests trong lượt review.

## Chuyển bước

G06-06A3-D: thiết kế một consumer batch thực sự cho Review/Prompt với explicit batch preview/copy, async ownership và full-render byte authority. Chỉ design; duy nhất một lát cắt thực thi nhỏ sau review. G01–G05 và A1 giữ nghiệm thu; G06 chưa đóng, không shutdown.
