# Local vision của AI Director — trạng thái ngày 2026-10-26

Đã có đường suy luận thị giác thật qua Ollama local. Đây là bước phân tích các cửa sổ hình ảnh để hỗ trợ rough-cut; chưa phải nghiệm thu toàn bộ quy trình dựng hàng trăm clip/ba giờ footage.

## Sử dụng trên máy hiện tại

1. Mở AI Director, thêm footage nguồn.
2. Bấm **Mô tả ảnh**, chọn `qwen3-vl:2b-instruct`. Luồng này tự chuẩn bị frame theo cửa sổ; không bắt buộc quét Whisper trước.
3. Nút **Lấy mẫu hình ảnh** tạo bảng ảnh xem nhanh độc lập. Nút **Ảnh** mở thư mục cache.
4. Sang bước Duyệt & Kịch bản và copy Prompt Dựng Video: mô tả ảnh có clip ID, mốc nguồn và cảnh báo vùng chưa phân tích/cần duyệt.
5. Bấm chuột phải nút Mô tả ảnh để phân tích lại nếu muốn bỏ qua kết quả model đã cache.

Nếu chưa chạy server, mở terminal và chạy:

```sh
/media/vanntruongg/Documents/.resolveflow-runtime/start-ollama.sh
```

Đây là launcher Linux cho máy hiện tại, không phải bộ cài Windows. Server chỉ bind `127.0.0.1:11434`, có `OLLAMA_NO_CLOUD=1`; inference không tải footage lên internet. Không thêm dịch vụ tự khởi động hệ thống.

Vị trí cài thực tế:

- Ollama `0.40.11`: `/home/vanntruongg/.local/share/ResolveFlow/ollama-runtime-v0.40.11/`.
- `.resolveflow-runtime/ollama` trên ổ Documents là symlink tới bộ chạy trên.
- Weights: `/media/vanntruongg/Documents/.resolveflow-runtime/models/`.
- Model giữ lại: `qwen3-vl:2b-instruct`, digest `afc393e3572c07db0bc84aa460d928a16c75f3fbb9ca9e0dccc4fbd2dafb573e`.
- Bản thinking `qwen3-vl:2b` tải thử trong phiên này đã được gỡ; không xóa model của người dùng.
- Runtime CUDA khoảng 1,3 GiB; model khoảng 1,8 GiB. Đã chia hai vị trí do Documents bị đầy trong lúc thực nghiệm. Không xóa footage/tài nguyên để lấy chỗ.

Sau kiểm chứng, model được unload để trả VRAM cho Resolve; server nhẹ vẫn có thể tiếp nhận request mới. Lần gọi đầu sau khi unload sẽ mất thêm thời gian tải model.

## Dữ liệu và các ràng buộc

- `VisualIndex` schema v3 giữ ảnh tổng quan và cửa sổ temporal riêng. Sampling/model chạy ở worker, không chạy trên GUI thread.
- Frame temporal lấy qua PyAV, lưu cả `requested_time_sec` và `time_sec` từ PTS decoder. Không gắn timestamp seek giả thành bằng chứng.
- Mặc định: cửa sổ 8 giây, 4 ảnh/cửa sổ, tối đa 24 cửa sổ/clip. Clip dài hơn ngân sách được lấy mẫu phân bố từ đầu tới cuối và ghi coverage partial, không báo đã hiểu toàn bộ.
- Tối đa 2 vùng chưa rõ được tăng mẫu một lần, tới 8 ảnh/vùng. Đây là ngân sách khởi đầu, chưa benchmark quy mô 3 giờ.
- Model nhận một cửa sổ/request; app cấp start/end cố định và ID ảnh. Schema JSON giới hạn đầu ra; validator chặn thời gian vượt video, NaN/Infinity, evidence ngoài đoạn/không tồn tại và provenance do model giả mạo.
- Hành động cần ít nhất hai mốc ảnh phân biệt. Dấu hiệu đổi cảnh từ ảnh nhỏ là heuristic; có thể báo dư. Vùng có dấu hiệu đổi cảnh được đưa needs_review, bỏ observed_actions để tránh kết luận diễn biến liên tục không có bằng chứng.
- Nhiều frame chỉ giúp có căn cứ theo thời gian; không bảo đảm model nhìn đúng. Mô tả vẫn có thể nhầm màu áo, chữ hoặc bỏ sót chi tiết.
- Cache key phân tích gồm fingerprint nguồn, sampling/window settings, model digest, endpoint, analyzer/prompt version và chính sách tăng mẫu/generation. Thiếu digest thì không tái dùng machine result như bản bất biến.
- Đổi cấu hình chỉ vô hiệu mô tả máy; giữ user notes. Nguồn bị thay nội dung thì metadata phiên bản cũ được lưu vào history, không áp ghi chú cũ vào video mới.
- Hủy sau response không công bố analysis hoàn tất; lỗi ghi đĩa không tạo cache thành công giả. JSON ghi atomic. Cache v1/v2 còn giữ ghi chú user nhưng cần duyệt lại; machine cache cũ thiếu bằng chứng không dùng như quan sát đã xác minh.
- Cache mặc định `~/.resolveflow_cache/visual`; có thể đặt `RESOLVEFLOW_VISUAL_CACHE_DIR` khi khởi động ứng dụng để chuyển sang ổ khác. Whisper cache độc lập.

## Bằng chứng thực nghiệm

File chi tiết trong `.bridge/vision-evidence/`:

- `experiment-gpu.json`: request context, raw model response, output đã chuẩn hóa và thời gian chạy bản cuối.
- `cache-check.json`: hai clip gọi lại không phát sinh `/api/chat`, khoảng 0,01–0,08 giây trong lần đo.
- `runtime.json`: bản ghi model thực sự dùng GPU, khoảng 2,07 GB VRAM trong lúc đo.
- Các file `experiment-cpu.json`, `experiment-gpu-thinking.json`, `experiment-gpu-disk-full.json` giữ các lượt thất bại; không dùng để chứng minh thành công.

| Clip mẫu thật | Đối chiếu thủ công | Kết quả bản cuối |
|---|---|---|
| `02_facepalm_toang_reaction.mp4` | Thẻ chữ đỏ, biểu tượng đầu lâu, nền tối; không có người/hành động | Model nhận ra chữ/biểu tượng, không thêm action; khoảng 7,6 giây cho cửa sổ 0–2,5s. Có sai khác dấu chữ nhỏ |
| `may_dung_co_mong_tuong.mp4` | Người áo đỏ, khoác tối/mảng vàng, người phía sau; chuyển sang thẻ chữ | Model nhận ra người, mô tả màu áo còn thiếu/sai; app phát hiện dấu hiệu chuyển cảnh và trả needs_review, observed_actions rỗng. Chỉ thử cửa sổ 4–8s, không kết luận cả clip; khoảng 7,6 giây |

Những lần đầu chạy CPU timeout 120 giây; bản thinking dùng hết ngân sách trước khi trả content. Thử instruct ban đầu còn lỗi mốc thời gian; đã thay bằng schema có range do app cấp. Một lượt gặp đầy đĩa và không lưu được cache; đã phân bổ lại runtime rồi chạy lại thành công.

Kiểm thử tập trung cuối: 77 passed ở mỗi thứ tự của hai file UI, exit 0; không tái hiện segfault trong nhóm đã chạy. Chưa có bằng chứng đủ để nói đã chứng minh hết mọi nguyên nhân crash Qt trong app.

## Chưa nghiệm thu

- Chưa có bộ footage thao tác đồ vật đa dạng có chuẩn đối chiếu, như nhấc cốc/rót nước/mở hộp.
- Chưa kiểm tra click desktop thật trên máy người dùng; chỉ render offscreen ở kích thước tối thiểu 1024x640 và test worker/lifecycle.
- Chưa có prompt phân cấp/chia lô hoàn chỉnh cho hàng trăm clip, chưa benchmark 3 giờ, chưa tích hợp Asset Registry thiết kế trước đó hoặc dựng end-to-end trên Resolve thật.

Tài liệu chính thức đã dùng: [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs), [Qwen3-VL tags](https://ollama.com/library/qwen3-vl/tags), [Ollama releases](https://github.com/ollama/ollama/releases).
