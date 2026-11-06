# G07-07A2a-R2 — Codex review: còn thiếu thực thi yêu cầu đã chốt

Ngày 2026-11-02. **CHANGES_REQUIRED**. G07-07A1 vẫn accepted. Không mở lại các sửa đã nghiệm thu hành vi; chưa đóng 07A2a.

Report SHA `85968609c9950d1b7913835df379c35ecc18449c5f8ca0dbc68d64f67fa646e8` khớp. Codex chạy lại nhóm5files: **88 passed/2.95s/exit0**. Chạy bản sao harness, ghi ra review dir mới: **15 old expectations + 8 residual expectations đều đạt**. Đây là tiến bộ thực, không đồng nghĩa toàn yêu cầu R2 đã triển khai.

Evidence độc lập: `.bridge/ux-evidence/G07-07A2a-R2-CODEX-REVIEW/` gồm pytest_run.log, prior-probes.json/prior-residual-probes.json, probe_review.py/probes.json. Không sửa src/tests trong review.

## 1. P1: kết quả chưa khớp graph theo chính yêu cầu R2-A4

Worker chỉ so frozenset clip_id và demand_id; bỏ multiplicity, source path, predicates/recheck và chosen asset. Source results có duplicate row, path=/other/source.mp4, is_readable=False hoặc recheck_passed=False đều được phát completed/checks_passed=True. Catalog có đúng demand nhưng asset_id=DIFFERENT_ASSET, duplicate rows hoặc guard_allowed=False/status=missing cũng báo đạt. Đây là phép so graph đã yêu cầu, không thêm quyền execution hay API mới.

## 2. P1: defensive copy chỉ lớp vỏ

Worker tạo SourceCatalogReadinessResult mới nhưng tuple chứa cùng observation instances. Probe `emitted.source_observations[0] is producer.source_observations[0]` True; sửa producer row.path sau terminal khiến emitted.path thành 'producer mutation'. SourceCatalogReadinessResult.__post_init__ chỉ isinstance/tuple/str, không revalidate/own typed rows hoặc strict fields. Same_result_object=False ở outer shell không chứng minh owned nested graph.

## 3. P2: final recheck loops chưa cancel

Catalog cancel đã đóng. Final source/master loops vẫn thiếu checkpoint mỗi phần tử. Với5nguồn, yêu cầu cancel ngay stat đầu của final recheck, inspector vẫn stat4nguồn tiếp rồi mới trả cancelled. Cần cùng semantics stop-before-next-item đã chốt.

## Gate đóng hữu hạn

R3 chỉ hoàn thiện shared owned-result validation và exact row map/bijection, cộng2loop checkpoints. Không thêm design/IO/executor/framework. Matrix cuối liệt kê trong task R3; positive real inspector/default StorageManager và15+8expectations giữ xanh. Không lặp lại thử nghiệm ngoài matrix hoặc mở thêm subsystem. Chỉ sau matrix này đạt mới nối UI+Quit retention ở07A2b.
