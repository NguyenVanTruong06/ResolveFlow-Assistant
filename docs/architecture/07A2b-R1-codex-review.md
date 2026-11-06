# G07-07A2b-R1 — Codex review: hai residual hữu hạn trước desktop

Ngày2026-11-02. **CHANGES_REQUIRED — chỉ hai lỗi nêu dưới đây**, không mở lại những sửa đã đạt.

Report SHA `1a3d653d84884b14890cd6d4ac8a6ef6262a8ef32bcb0291a314861e2c7c0cb9` khớp. Fresh Codex6files: **63 passed /15.72s /exit0**. Bản sao probeR1 ghi riêng reviewdir xác nhận pending5fields/duplicate/progress/Cancel/drop/shutdown admission/closedflag/queryretention đúng kỳ vọng. Backend07A2a hashes frozen đúng; R08 đã có real QThread barrier cho preflight, ghi nhận đạt.

Evidence `.bridge/ux-evidence/G07-07A2b-R1-CODEX-REVIEW/`: pytest_run.log,probe_review.py/probes.json, residual_probes.py/residual-probes.json. Residual harness exit0 chỉ nghĩa chạy xong; hai negative expectations chưa đạt.

## L1 — P1: cached Director không mở lại hoạt động được

Callgraph thật: EdgeDockWindow.open_director (src/ui/lux/shell/edge_dock.py:810–815) giữ `_director_window` và `.show()` lại cùng instance. DirectorWindow.closeEvent đặt _is_closed=True và shutdown helper đặt _shutdown_requested=True; không có resume/show transition. Probe bước2 →close→show cùnginstance→go_to_step(1): window visible nhưng vẫn bước2, hai flags vẫn True. Đây là nhánh normal reopen đã nêu trong R1-C, không scope mới.

Cần phân biệt normal window close với global app Quit. Chỉ khi normal close đã hoàn tất và tất cả3families physical idle mới cho resume admission khi show lại; app Quit pending/allowed/scheduled không được reset. Bảo toàn user state/drafts; phục hồi child Review/Source lifecycle flags đã set bởi shutdown nếu thật sự idle, không chỉ xóa cờ parent rồi để child prompt/analysis bị khóa.

## L2 — P2: guard disconnect mọi finished listeners của LibraryJob

SourceShutdownGuard._check_idle gọi `job.finished.disconnect()` không tham số. Probe observer khác nối sau guard callback không được chạy (`[]`), thay vì ['observer']. Guard chỉ được disconnect connection/callback do mình tạo; không đụng consumer khác hoặc callback active_jobs của LibraryJob. Giữ handler refs/connection tokens và cleanup riêng. Đợi unknown query vẫn fail-safe như R1 đã đạt.

## Evidence closure + desktop được giao cùng lượt

R11 test hiện dùng DummyLibraryJob(QObject), không phải real LibraryJob QThread như docstring nói; cần bổ sung real LibraryJob held/native finished và sửa nhãn bằng chứng. R12 geometry isValid chưa chứng minh nút nằm trong viewport; desktop phải đo containment/hit-test và chụp ảnh app-region.

Giao R2 code đúng2modules (DirectorWindow,SourceShutdownGuard) + hai tests hiện có; BuildPage/backend frozen. Khi test hữu hạn đạt thì tự chạy4ca desktop ở session riêng, không đợi thêm vòng cấp quyền. Không tự thêm executor/model/Resolve hoặc sửa rộng nếu desktop phát hiện vấn đề ngoài scope.
