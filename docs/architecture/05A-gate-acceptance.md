# G05-05A — Nghiệm thu cache audio


## Quyết định cuối G05-05A-R2 — 2026-10-27
CODE_ACCEPTED_WITH_RECORDED_LIMITS cho lát cắt cache audio. Codex đọc helper identity/payload và preflight abort, chạy lại đúng4files:79passed6.72s exit0. Probe độc lập tmp xác nhận reduced provenance bị từ chối, stale digest không publish, bigint bị reject không throw. Các ca R1 về replacement với evidence đầy đủ, bỏ rehash cuối analysis, cancel, legacy không auto-promote tiếp tục được bao phủ bởi nhóm test.

Không có model/GPU/nguồn thật/desktop trong phiên review. Chưa chứng minh hiệu năng3giờ, mọi race filesystem hay mọi payload tùy ý; không lặp lại từ ngữ “an toàn tuyệt đối” trong report. Đọc full file có chi phí IO, stat không phải mandatory lock. Legacy v1/v2 cần một lần phân tích lại trong worker để có v3; bản cũ được giữ. Default fast UI cache vẫn là candidate; UI dual-stage/provenance phase in-memory/Visual invalidation là residual, không được coi gate G05 đã xong. Không cộng test giao nhau. Chỉ mở lại05A khi bug tái hiện/consumer mới làm lộ contract lỗi. Giao G05-05B-D tiếp theo, không shutdown.
