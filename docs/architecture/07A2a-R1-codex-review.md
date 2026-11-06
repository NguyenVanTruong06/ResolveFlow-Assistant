# G07-07A2a-R1 — Review độc lập, 2026-11-02

**CHANGES_REQUIRED — finite residual closure only.** G07-07A1 accepted giữ nguyên. Không mở lại A/các lỗi đã đạt.

Report `b6d629ed277de03e6bb30369004f90ec2b655cba5c1b2cf2afc6dfe687d52a65` đã khớp. Codex chạy lại nguyên nhóm năm file: **82 passed / 2.97s / exit 0**. Hai modules production có SHA khớp report; UI/controller/shutdown giữ nguyên. Bộ probe cũ chạy trong bản sao ghi ra thư mục review mới: **15/15 expectations đúng** (prior-probe-assertions.json). Source-only/live assignment, None factory, marker error, loaded/disk mismatch, missing kind, request clone, caller mutation terminal, stat ctime và final-progress cancel được ghi nhận đã đóng.

Bằng chứng mới: `.bridge/ux-evidence/G07-07A2a-R1-CODEX-REVIEW/` gồm pytest_run.log, prior_probe_review.py/prior-probes.json/prior-probe-assertions.json và residual_probes.py/residual-probes.json. Probe exit0 là harness hoàn tất; các kết quả lỗi dưới đây vẫn tái hiện.

## R2-A — P1: phần B3/B4 của lệnh R1 chưa triển khai

`SourceCatalogReadinessResult.__post_init__` (source_catalog_readiness.py:177–186) chỉ kiểm tra status, ready flag, tuple hóa/ép chuỗi; không strict bool checks_passed, typed owned observations, invariants failed/cancelled, IDs/finite numbers. Dùng `or ()` còn silently nhận malformed falsy. SourceClipObservation/AssetAssignmentObservation không có validation/ownership.

Worker lưu `_expected_source_count/_expected_assignment_count` nhưng không dùng. Sau kiểm tra binding (`preflight_worker.py:183–210`) phát nguyên object producer. Các probe với đúng bindings và generation int nhận completed/checks_passed=True dù source_observations rỗng, clip_id sai hoặc chứa mutable dict. checks_passed='yes' và failed+checks_passed=True cũng được phát nguyên trạng. Đây là các yêu cầu exact graph/strict type/revalidation/copy đã giao ở R1, không phải mở scope mới.

## R2-B — P1: public inspector không revalidate pair

Entry `source_catalog_readiness.py:233–262` chỉ kiểm tra request_id/generation/hasattr; không reconstruct/revalidate normalized proposal và approval binding trước IO. Probe thay revision của nominal typed approval thành f*64: inspector vẫn stat nguồn hai lần rồi completed/checks_passed=True. Cần controlled ValueError, stat_calls=0. Dùng cơ chế ReadinessRequest đã có, không schema hay approver mới.

## R2-C — P2: cancellation trong loops chưa có

Catalog index (`:655`) và final source/master loops (`:887`, `:930`) thiếu checkpoints giữa từng phần tử. CountedList đặt cancel ngay khi yield item đầu: inspector trả cancelled cuối cùng nhưng đã enumerate đủ 100/100 records. Phải dừng trước phần tử tiếp, giữ physical thread ownership như hiện tại. Không cần deadline cứng hoặc terminate.

## Gate hữu hạn

Sửa đúng ba residual groups trên trong same2modules + same2testfiles; giữ default StorageManager thật integration đã thêm. Không lặp lại thiết kế/A authority, không UI/Resolve/hashing. R2 phải có regression assertions cho từng residual, positive payload từ inspector thật, exactly-one terminal + native finished trên QThread thật. Thay các fake-positive tests đang chấp nhận empty-success bằng positive đúng contract, không nới guard để giữ test. Stat ctime chỉ là observation, không bằng chứng mọi sửa đổi nội dung hay CFR.
