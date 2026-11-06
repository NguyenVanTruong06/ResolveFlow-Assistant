# G07-07A2a — Nghiệm thu backend kiểm tra nguồn/catalog

Ngày 2026-11-02. **ACCEPTED_WITH_RECORDED_LIMITS**. Đóng lát cắt 07A2a sau R3; chưa đóng G07.

Report SHA-256 đã khớp: `4135170e2e699c5d951d0bdff93373ff6c68a12bd2a4182fdb5ed73d65ff0d78`.

Codex chạy lại nguyên nhóm5files đã giao: **95 passed / 3.10s / exit0**. Chạy lại matrix F01–F12 trong bản sao ghi evidence riêng, exit0; assertions **12/12 đạt**. Với F12, Codex xác định đúng boundary final-master `_safe_stat_identity` thay vì dựa giả định số lần stat. Không thay src/tests trong review.

Evidence: `.bridge/ux-evidence/G07-07A2a-R3-CODEX-REVIEW/` gồm pytest_run.log, matrix_harness.py, matrix.log, matrix-results.json, matrix-assertions.json, review-evidence.json. Đây là focused/offscreen/QThread verification, không desktop app acceptance.

## Scope đã đạt

- Source-only không buộc kho; live catalog/ActionGuard/master path đúng và không auto-omit.
- Request/approval binding và terminal scalar identity; normalizer sử dụng thực tại worker, clone observations độc lập.
- Exact source path/ID, số lượng và uniqueness; chosen asset/slot/source_kind theo demand; predicates/recheck/error/status không còn lọt graph success.
- Cancel catalog/final source/master/final progress; exactly-one logical terminal và native finished tách biệt, constructor0IO/default factories off-thread theo focused tests.
- Không sửa UI/controller/shutdown guard; không media-byte read/hash/ffprobe/models/network/Resolve mutation.

## Giới hạn giữ nguyên

Result chỉ là quan sát metadata tại lần kiểm tra; ready_to_execute luôn False. Không chứng minh full content identity từ analysis/approval, cadence toàn clip/CFR, source frame geometry execution, Resolve target identity, timeline mutation hoặc recovery. Không phải execution token và không bật lại phase0/2. Các phần này thuộc các slice G07 sau, không reopen backend scope khi chưa có defect liên quan.

07A2b được giao nối UI cùng global Quit/close retention. G08/G09/G10 vẫn queued. Không shutdown.
