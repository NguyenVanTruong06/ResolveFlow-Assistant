# G06-06C2a-I — Review: REQUEST_CHANGES

2026-10-30. Report SHA256 verified: `31fd1165c6f036da2b221e56789a951f5bc445f42e01ae30ab9a95964ba8e2f4`.

Codex chạy đúng finite suite: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/geometry/test_frame_geometry_calculator.py tests/core/contracts/test_time_contracts.py -q` → **30 passed in 0.30s, exit 0**. Các ví dụ Formula B, exact ties, speed và raw EOF trong suite đạt. Không chạy app/model/Resolve hay full suite; chưa nghiệm thu C2a.

## Hai nhánh của cùng lỗi boundary cần đóng gộp

1. **FPS validation trước arithmetic/copy** — `frame_geometry_calculator.py:78,94` và `frame_geometry_contracts.py:47,55`: kiểm tra `<= 0` trước strict type validation các thành viên. Dùng một RationalFPS đã bị thay nội dung qua `object.__setattr__` (nominal type vẫn đúng): numerator='30' hoặc denominator=None làm rò TypeError; numerator=True/denominator=1.0 làm lỗi từ time_contracts lọt ra thiếu mã geometry. Calculator còn dùng caller FPS để tính trước khi có owned validated copy.
2. **Direct DTO source_range** — `frame_geometry_contracts.py:73–86`: kiểm tra bounds trước strict int member validation. start_frame='0', end_frame=None làm rò TypeError; start_frame=False/end_frame=30.0 trả ValueError thiếu mã geometry. Đây là residual của invariant direct-construction/typed-member revalidation trong workorder, không phải lỗi phép tính trên dữ liệu hợp lệ.

Evidence: `.bridge/ux-evidence/G06-06C2a-CODEX-REVIEW/probes.json` chứa **14 counterexamples**, chưa ca nào đạt yêu cầu controlled ValueError kèm mã tương ứng. Không diễn giải thành 14 lỗi thuật toán riêng biệt. Tạo regression tests từ các probes, dùng cùng validation/copy policy cho calculator và DTO trong đúng hai modules hiện tại; không sửa time_contracts.

## Corrections evidence nhỏ trong cùng bản vá

Purity test hiện khai báo `real_now = datetime.now` nhưng không hook/call-spy datetime.now và không spy QSettings. Report khẳng định spy hai nguồn này là rộng hơn bằng chứng. Hiệu đính mô tả theo các spies thực tế (open/stat/socket/time.time) và source/import review; không cần bổ sung runtime Qt. Không mở một task thiết kế mới cho correction này.

## Phạm vi tiếp theo

`G06-06C2a-R1`, HIGH: hai module hiện tại + test mới hiện tại + docs/report. Giữ nguyên API/sáu fields, public pts_to_frame, Formula B, error semantics cho dữ liệu hợp lệ, missing/VFR precedence. Không whole timeline, evidence supplier, UI, Resolve, storage hoặc G07. Chưa nghiệm thu gate cho tới khi finite tests và probes đạt.
