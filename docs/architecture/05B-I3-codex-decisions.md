# G05-05B-I3-D — Quyết định Codex (2026-10-27)

DESIGN_ACCEPTED_WITH_BINDING_CORRECTIONS. HIGH đã được xác nhận qua bàn giao người dùng/Worker, không cần hỏi lại. Không giữ model gate cũ; task tiếp HIGH.

## Phần A Evidence R2
Nhận shared_cleanup cho targeted run và fault-injection cùng realworker boundary: metrics normal exit0 và expected fault exit2, registryempty/running0/sessioncleaned. Đã đọc harness, không thấy terminate. Nhận explicit SCREEN_CAPTURE_UNVERIFIED_COMPOSITE_X11 và PALETTE_READABILITY_PRODUCTION_RESIDUAL_CONFIRMED; ảnh widget đã xem vẫn chữ trắng/nền sáng, chưa readable. Không coi UX lỗi màu đã sửa. Ghi backlog G08: dialog summary đọc được trên dark theme. Không yêu cầu vòng evidence nữa; smoke component chỉ trong limits seeded/audio probe/real frames/replay/controlled cancel, không inference/main E2E.

## Những chỗ design chưa khớp code
1. describe_with_ollama hiện chỉ commit ở cuối clip. InterruptedError trong loop thoát không checkpoint; chưa có partial persistence/resume per-window. Không thể đạt bằng chỉ sửa worker+2UI.
2. prepare_windows đang giữ overview qua {**cached}, nhưng scan rebuild mới ghi dict overview và chỉ giữ usernotes, có thể làm mất windows/analysis/descriptions khi cùng source mà đổi overview config. Cần đóng independence này trước Stage3.
3. prepare_windows_with_speech_hints chưa tồn tại. Hiện prepare_windows chỉ chia đều ngân sách; STT gaps là vùng không có lời được nhận diện, không chứng minh im lặng hay toàn bộ hoạt động chỉ ở đó. Không nói biết chính xác khoảng không thoại/đảm bảo mô tả đầy đủ.
4. Signature hiện dùng fingerprint/temporal/model digest/prompt/refinement. Cache shortcut cho cảpartial/needs_review khôngerrors; khi thêm checkpoints phải phân biệt completed selected windows với pending/error. Không reuse incomplete checkpoint như complete clip.
5. urllib timeout30 là socket-operation/request setting, không wall-clock deadline bảo đảm kết thúc/cancel<=30s. Không hứa dừng Ollama GPU task khi client cancel. Giữ stdlib và cooperative boundaries, công bố timeout hữu hạn từng call chứ không total SLA.
6. Paths thật: ScanCacheManager.cache_dir (~/.resolveflow_cache), VisualIndex.cache_dir (~/.resolveflow_cache/visual), index JSON trực tiếp/{identity}.json; không mặc nhiên audio/ hoặc metadata/ migration. Model origin thật local_model:<name>, không đổi sangollama theo ví dụ.
7. Core output descriptions được validate không có nghĩa accuracy đã nghiệm thu. needs_review/unknown có evidence khác described; coverage sampled/analyzed/fullydescribed tách nhau, total0 khôngcomplete; bounded sampling không hiểu mọi frame.

## Chốt ba chính sách
- Speechless priority: DEFERRED trong first slice; giữ uniform bounded temporal windows có sẵn, vẫn tự lấy mẫu clip không thoại. Có thể thiết kế speech hints sau khi provenance/coverage rõ; bỏ tiết kiệm80%/5–15phút chưa đo.
- HTTP: giữ urllib/socket timeouts hiện hành, không rewrite async; không total-cancel30s guarantee.
- Review: hướng riêng “Cảnh quay” hợp lý, nhưng chưa code panel/Prompt/G06 trong core slice; ghi dependency downstream cần nghiệm thu trước đóngG05.

## Phân rã thực thi
I3a HIGH: đúng visual_index.py + visual_describer.py, giữ overview/temporal independence, checkpoints/resume selected windows với signatures, truthful coverage/cancel. I3b tiếp: config/worker opt-in + UI/summary sau coreaccepted. Downstream Review/Prompt riêng, chưa loại khỏi G05; không hứa chỉ3modules là xong hết.
