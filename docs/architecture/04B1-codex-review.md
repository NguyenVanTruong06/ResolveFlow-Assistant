# G04-04B1-I — ACCEPTED_WITH_SMALL_COMPATIBILITY_CORRECTION

Codex đọc search/proxy và integration fixtures126assets, pack/sub/tag/category kết quả ngoài top50, activequery add/rename/delete, defaults/positive/ranking. Chạy4files quy định: **34 passed in19.10s, final exit0**. Search đầy đủ và facet intersection đạt code/offscreen evidence; chưa desktopverified.

Một compatibility correction hữu hạn: code hiện `if limit is None or limit <= 0: return results` đổi limit âm từ legacy Python slicing `results[:-1]` sang all. Workorder đã yêu cầu không đổi argument behavior ngoài0. Chốt `limit is None or limit == 0` unlimited; negative limits giữ slicing cũ, không thêm error/type validation. Thêm regression -1/-2. Không đổi default50/positive/proxylimit0 hoặc làm lại integration architecture.

Cho phép sau khi correction + focused tests đạt, Worker chỉ làm DESIGN G04-04C-D (preview/suggestion/offline guards). Chưa cho code04C. Selection linear-loop optimization vẫn deferred, không cần tạo refactor riêng trước safety UX.

## Review R1 — ACCEPTED
Codex đối chiếu Optional[int], limit is None or ==0, regressions -1/-2 giữslicing. Chạy đúng4files workorder: **34 passed in24.14s, final exit0**. 04B1/R1 code accepted; không phải benchmark hoặc desktopverified. Search-limit bug đóng, không rerun/refactor khi chưa có thay đổi mới.
