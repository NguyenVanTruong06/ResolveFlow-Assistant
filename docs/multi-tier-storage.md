# ChunDVC: Kho đa tầng và kiểm duyệt nhập pack

Ngày 2026-10-26. Phạm vi: kho media, không tự di chuyển model Ollama, cache Whisper hay project Resolve.

## Hợp đồng lưu trữ
- Người dùng chọn Storage Root bằng hộp thoại; không chọn mặc định thư mục hệ thống để tải media.
- Root có `.chundvc_meta/library.json` chứa UUID. Config nhỏ tại user app-data lưu root + UUID; kiểm tra marker trước mọi lần ghi. Mất marker/ổ: Offline, không tạo lại root hoặc fallback media về máy.
- `.chundvc_meta/registry.json`, `taxonomy.json`: JSON UTF-8 atomic; snapshot registry nhỏ trên máy, giới hạn 16MB, không chứa media. Khi offline vẫn duyệt asset với availability=offline.
- `01_core/`: mẫu/legacy media nhập rõ ràng; `02_cloud_downloads/packs/<name>_<version>_<id>/`: pack độc lập; `.chundvc_meta/staging/<job>/`: download/extract/review; `03_local_linked/links.json`: đường dẫn liên kết, không symlink/copy footage.
- Manifest pack lưu asset UUID ổn định, đường dẫn tương đối, category/subcategory/tags, type template (text/trans), tên và byte size. JSON có thể dùng lại từ web; app không cần server.
- Canonical storage categories: broll,sfx,bgm,overlays,memes,luts,templates. Adapter UI chuyển overlays→overlay, memes→meme, luts→lut, templates→text/trans; custom category dùng ID do người dùng tạo và label riêng.

## Luồng
Chọn root → tải/copy vào staging tại root → kiểm tra file/ZIP + manifest → bảng model/view cho review → sửa hàng hoặc chọn nhiều hàng để gán category/sub/tags/exclude → commit manifest và rename staging cùng volume → publish registry atomic → cập nhật snapshot → signal assets_changed → Studio/EdgeDock cập nhật source model/search index.

File online chưa có category đáng tin: MP4 mặc định broll, không mặc định meme. WAV/MP3 nhìn folder nhạc để đoán bgm, còn lại sfx. .cube=luts; .setting/.drfx=templates. Preview MP4 trong folder transition chỉ preview, mặc định exclude để tránh chèn như footage. User được sửa dự đoán; file hướng dẫn mặc định exclude.

## An toàn / dung lượng
- Dự trữ tối thiểu256MiB (có thể cấu hình trong config). Kiểm tra free space trước copy/extract và từng chunk1MiB, kể cả khi server không công bố kích thước. Không dùng gdown fallback không kiểm soát trong luồng nhập kho.
- ZIP từ chối absolute/drive paths, '..', symlink, duplicate/case-collision, số file quá100k. Giới hạn manifest2MiB, registry snapshot16MiB. Không extractall, không đọc media cả file vào RAM.
- Không ghi vào linked folder, không tự tạo thumbnail trong nguồn. Offline giữ metadata, cấm preview/insert khi media không có thật.
- Commit dưới lock, không overwrite pack có sẵn; transaction có manifest riêng để phục hồi bằng refresh nếu app dừng sau rename trước registry. Cancel không publish, dọn staging thuộc job; mất ổ thì giữ job để xử lý sau, không ghi elsewhere.
- Migration: copy có checksum xác minh, guard dung lượng mỗi chunk; chỉ đổi config sau hoàn tất. Giữ nguồn nếu lỗi/cancel. Không xóa bundled assets/model/project. Tùy chọn dọn nguồn chỉ cho managed library sau xác minh và lựa chọn rõ ràng, không xóa linked targets. Nguồn legacy người dùng chọn được giữ nguyên để bảo toàn ứng dụng; UI nói rõ.

## Giới hạn vận hành
Không thể bảo đảm ổ không bao giờ đầy khi ứng dụng khác đồng thời ghi dữ liệu. Reserve/chunk guard + xử lý ENOSPC ngăn báo thành công sai; không tự tải media về ổ hệ thống. Cloud folder Drive tải từng file có kiểm soát; folder riêng tư/quota/auth lỗi báo rõ. Không tự giải nén định dạng RAR/7z chưa hỗ trợ.

## Schema đang triển khai (v1)

Config nhỏ trên máy: `$XDG_DATA_HOME/ChunDVC/storage/storage.json` (Linux), `%LOCALAPPDATA%/ChunDVC/storage/storage.json` (Windows). Nếu Linux không đặt XDG_DATA_HOME thì dùng `~/.local/share`.

```json
{"root":"/mnt/ExternalDrive/ChunDVC_Library","library_id":"uuid","reserve_bytes":268435456}
```

`pack_manifest.json` do wizard ghi sau khi người dùng xác nhận:

```json
{
  "schema_version": 1,
  "pack_id": "uuid",
  "name": "Kho Review",
  "version": "1.0",
  "assets": [{
    "id": "stable-uuid",
    "relative_path": "city/morning.mp4",
    "name": "morning",
    "category": "broll",
    "subcategory": "city",
    "tags": ["Review Công Nghệ", "Chill Vlog"],
    "excluded": false,
    "size_bytes": 123456,
    "template_type": "text",
    "library_path": "02_cloud_downloads/packs/Kho_Review_1.0_uuid/city/morning.mp4"
  }]
}
```

`template_type` chỉ có ý nghĩa với templates (`text`/`trans`). Registry có `packs[]`, `assets[]`; asset linked thêm `source_id` và `external_path`, không copy media. Các đường managed tương đối nên ID và taxonomy được giữ khi migration. Manifest cũ `manifest.json` vẫn được đọc nếu có; override theo `assets[].relative_path`, không tin đường dẫn tuyệt đối từ gói ngoài.

## Sử dụng
1. Studio → Kho Online → **Kho Chính / Ổ ngoài / Liên kết**. Chọn hoặc tạo thư mục trên ổ muốn dùng. Chưa chọn kho thì các nút tải/commit không chạy.
2. **Tải & Cài Đặt** hoặc thả ZIP/.rfpack → wizard. Chọn **Quét & kiểm tra**. Nguồn cũng có thể là thư mục hoặc URL HTTP(S)/Google Drive; dữ liệu được staging ngay tại kho.
3. Nhấp đúp category/sub/tags hoặc chọn nhiều hàng → **Gán cho hàng chọn**. Tạo category với ID lowercase như `product_review`, tên tiếng Việt tùy ý. Bỏ ✓ để exclude.
4. **Nhập vào kho** → registry cập nhật → Studio phát `library_assets_changed` → Dock và SearchIndex làm mới. Dock kiểm tra trạng thái kho mỗi2,5giây; Offline giữ snapshot metadata, chặn insert/preview/drag tài nguyên offline.
5. Di chuyển kho: chọn thư mục đích trống. Mặc định giữ bản cũ; có checkbox dọn media **kho managed** sau checksum và chuyển config. Không tự xóa source legacy/linked hoặc model AI. Không chọn thư mục đích nằm trong/bao ngoài nguồn, kể cả qua symlink.
6. Liên kết kho500GB: chọn **Liên kết thư mục có sẵn** và category; chỉ đọc tên/file size để lập index, không đọc/copy500GB. Gỡ liên kết trên Kho Online chỉ bỏ metadata.

## Ranh giới còn lại
- Test phát triển dùng thư mục/ZIP nhỏ, mock network, Qt offscreen. Chưa tải Google Drive thật hoặc thử rút ổ vật lý trên Windows/Linux.
- Không có cơ chế đăng nhập Drive riêng tư; lỗi quyền/quota không được coi là thành công. Không fallback sang download không có write guard trong luồng kho mới.
- Migration chỉ áp dụng kho này và thư mục legacy người dùng chọn. Các môi trường AI/Whisper/proxy có cơ chế cache riêng, chưa đổi đường dẫn trong công việc này.
- Snapshot metadata giới hạn16MiB; linked/source có tối đa100kfiles cho một job. Kho vượt giới hạn cần chia nhỏ; chưa shard registry hay benchmark500GB/100kfile.
- Nếu ổ bị rút giữa job, staging có thể còn trên ổ để xử lý sau khi kết nối lại. Không tự xóa dữ liệu ở nơi khác. Nếu app bị kill giữa rename và cập nhật registry, `pack_manifest.json` vẫn giữ bản mô tả; chưa tự phục hồi registry sau crash cứng.
- API PackManager/CloudPackInstallWorker cũ vẫn giữ để tương thích; các nút nhập/tải trong Asset Hub đã đi qua wizard mới. Không tự di chuyển bundled `assets/` trên lần mở app.
