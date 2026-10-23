# Kế Hoạch Triển Khai: Hệ Sinh Thái Kho Đạo Cụ Cộng Đồng (ResolveFlow Community Asset Hub)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

---

## 1. Danh Sách Tệp Tác Động (File Structure & Responsibilities)

| Tệp | Trách nhiệm |
| :--- | :--- |
| `src/core/pack_manager.py` *(Tạo mới)* | Đóng gói `.rfpack`, đọc manifest, xác thực bảo mật (chống zip-slip), giải nén vào `assets/`, quản lý danh mục Cloud Pack. |
| `src/core/asset_indexer.py` *(Chỉnh sửa)* | Nâng cấp hỗ trợ Multi-Root: quét đồng thời `assets/` và các thư mục ngoài được lưu trong `QSettings`, tối ưu cache `mtime`. |
| `src/ui/tabs/cloud_pack_worker.py` *(Tạo mới)* | QThread chạy ngầm tải gói từ Google Drive qua `GoogleDriveDownloader`, báo % tiến trình và gọi cài đặt tự động. |
| `src/ui/tabs/tab_asset_hub.py` *(Tạo mới)* | Giao diện Tab "Kho Online & Quản Lý Nguồn" trong Studio: 3 khu vực Cloud Packs, Thư mục máy tính ngoài, và Kéo thả .rfpack. |
| `src/ui/tabs/tab_assets.py` *(Chỉnh sửa)* | Tích hợp Tab chuyển đổi ở góc trên: [Kho Đạo Cụ] <-> [Kho Online & Nối Thư Mục], đồng bộ nạp nóng khi có asset mới. |
| `src/ui/lux/shell/edge_dock.py` *(Chỉnh sửa)* | Bổ sung hỗ trợ kéo thả tệp `.rfpack` trực tiếp vào thanh Dock để tự động cài đặt. |
| `tests/test_pack_manager.py` *(Tạo mới)* | Unit test cho PackManager: tạo pack, đọc manifest, cài đặt an toàn, chặn tấn công zip-slip. |
| `tests/test_linked_folders.py` *(Tạo mới)* | Unit test cho quét thư mục ngoài và bộ nhớ đệm `mtime`. |
| `tests/test_asset_hub_e2e.py` *(Tạo mới)* | Integration test toàn diện luồng cài pack -> quét -> chèn vào DaVinci Resolve timeline. |

---

## 2. Kế Hoạch Triển Khai Chi Tiết Từng Nhiệm Vụ (Bite-sized Tasks)

### Nhiệm Vụ 1: Xây Dựng Bộ Máy Đóng Gói & Cài Đặt `PackManager` (`src/core/pack_manager.py`)
- [ ] **Bước 1.1**: Viết test ban đầu `tests/test_pack_manager.py`:
  - Test tạo file `.rfpack` với `manifest.json`.
  - Test đọc thông tin manifest từ `.rfpack` mà không cần giải nén toàn bộ.
  - Test giải nén gói vào thư mục đích.
  - Test bảo mật: từ chối các file có đường dẫn `../` (chống zip-slip).
- [ ] **Bước 1.2**: Chạy pytest để xác nhận test thất bại (Red):
  - Lệnh: `.venv/bin/pytest tests/test_pack_manager.py`
- [ ] **Bước 1.3**: Hiện thực `PackManifest` (Pydantic model) và lớp `PackManager` trong `src/core/pack_manager.py`:
  - `create_pack(output_path, manifest, category_files_map) -> str`
  - `inspect_pack(pack_path) -> PackManifest`
  - `install_pack(pack_path, target_base_dir) -> Dict[str, int]` (trả về số lượng file đã cài theo từng danh mục).
  - Tích hợp kiểm tra an toàn đường dẫn `os.path.commonpath`.
- [ ] **Bước 1.4**: Chạy lại pytest xác nhận test pass (Green):
  - Lệnh: `.venv/bin/pytest tests/test_pack_manager.py`
- [ ] **Bước 1.5**: Commit code Task 1.

---

### Nhiệm Vụ 2: Nâng Cấp `AssetIndexer` Hỗ Trợ Nối Thư Mục Ngoài (Multi-Root) & Cache Tức Thì
- [ ] **Bước 2.1**: Viết test `tests/test_linked_folders.py`:
  - Test thêm thư mục ngoài tạm thời (`tmp_path / "MyMemes"`, `tmp_path / "MySFX"`).
  - Test `AssetIndexer` quét và nhận diện file từ cả 2 nguồn (nội bộ và ngoài).
  - Test thuộc tính `is_external=True` và bảo toàn đường dẫn tuyệt đối gốc.
  - Test cache `mtime`: lần quét 2 không duyệt lại nếu `mtime` thư mục không đổi.
- [ ] **Bước 2.2**: Chạy pytest xác nhận test thất bại (Red):
  - Lệnh: `.venv/bin/pytest tests/test_linked_folders.py`
- [ ] **Bước 2.3**: Chỉnh sửa `src/core/asset_indexer.py`:
  - Thêm phương thức: `get_linked_folders() -> List[str]`, `add_linked_folder(path: str)`, `remove_linked_folder(path: str)`.
  - Tích hợp lưu trữ vào `QSettings("ChunDVC", "ResolveFlow")`.
  - Cập nhật hàm `scan_all()`, `scan_sfx()`, `scan_memes()`, `scan_overlays()`, `scan_titles()`, `scan_transitions()` quét cả danh sách thư mục ngoài.
- [ ] **Bước 2.4**: Chạy lại pytest xác nhận test pass (Green):
  - Lệnh: `.venv/bin/pytest tests/test_linked_folders.py`
- [ ] **Bước 2.5**: Commit code Task 2.

---

### Nhiệm Vụ 3: Luồng Chạy Ngầm Tải Gói Cloud & Danh Mục Tuyển Chọn
- [ ] **Bước 3.1**: Định nghĩa danh mục mẫu `COMMUNITY_PACKS_CATALOG` trong `src/core/pack_manager.py`:
  - Gói Meme TikTok Hot (Meme video + audio).
  - Gói SFX Điện Ảnh & Vlog (Whoosh, Impact, UI, Glitch).
  - Gói Chữ Kinetic Phong Cách MrBeast / Alex Hormozi (.setting).
  - Gói LUTs Phim Cổ Điển & Cyberpunk.
- [ ] **Bước 3.2**: Hiện thực `CloudPackInstallWorker(QThread)` trong `src/ui/tabs/cloud_pack_worker.py`:
  - Nhận URL Google Drive hoặc ID tài nguyên.
  - Dùng `GoogleDriveDownloader` tải ngầm tệp nén vào thư mục cache tạm.
  - Tự động gọi `PackManager.install_pack()` khi tải hoàn tất.
  - Phát tín hiệu: `progress_updated(percent, speed, status)`, `install_finished(success, message, item_counts)`.
- [ ] **Bước 3.3**: Viết test `tests/test_cloud_pack_worker.py` kiểm thử xử lý tín hiệu và hủy tải an toàn.
- [ ] **Bước 3.4**: Commit code Task 3.

---

### Nhiệm Vụ 4: Xây Dựng Giao Diện Tab "Kho Online & Quản Lý Nguồn" Trong Studio
- [ ] **Bước 4.1**: Tạo widget `AssetHubWidget` trong `src/ui/tabs/tab_asset_hub.py`:
  - **Khối 1 (Cloud Packs)**: Grid/Card view các bộ Starter Pack có sẵn với nút `[Tải & Cài Đặt]`, thanh ProgressBar, và ô dán link Google Drive tùy ý.
  - **Khối 2 (Linked Folders)**: Danh sách các thư mục ngoài đang kết nối, kèm dung lượng, số tệp, nút `[➕ Thêm thư mục]`, nút `[🗑️ Gỡ]`, nút `[🔄 Quét lại]`.
  - **Khối 3 (RFPack)**: Khu vực kéo thả cài đặt `.rfpack` + Nút `[📤 Xuất bộ sưu tập]`.
- [ ] **Bước 4.2**: Chỉnh sửa `src/ui/tabs/tab_assets.py`:
  - Thêm thanh chuyển tab đẹp mắt ở header Studio:
    - Nút 1: `🎬 Kho Đạo Cụ` (Hiển thị 4 cột truyền thống để duyệt & chèn).
    - Nút 2: `☁️ Kho Online & Nối Thư Mục` (Hiển thị `AssetHubWidget`).
  - Kết nối tín hiệu: Khi cài pack hoặc thêm thư mục xong, tự động gọi `AssetIndexer.clear_cache()` và làm mới lưới tài nguyên ngay lập tức.
- [ ] **Bước 4.3**: Chỉnh sửa `src/ui/lux/shell/edge_dock.py`:
  - Kích hoạt sự kiện `dragEnterEvent` và `dropEvent` cho phép kéo thả file `.rfpack` trực tiếp vào Dock.
- [ ] **Bước 4.4**: Commit code Task 4.

---

### Nhiệm Vụ 5: Kiểm Thử Tích Hợp Toàn Diện (End-to-End Acceptance) & Minh Chứng Trực Quan
- [ ] **Bước 5.1**: Viết kịch bản kiểm thử toàn diện `tests/test_asset_hub_e2e.py`:
  - Nối 1 thư mục video ngoài chứa tệp test.
  - Kiểm tra `AssetIndexer` quét được tệp và phân loại đúng.
  - Thử nghiệm chèn tệp từ thư mục ngoài vào DaVinci Resolve Timeline tại playhead.
  - Tạo 1 file `.rfpack` test, kéo cài đặt và xác nhận tệp xuất hiện trong Studio.
- [ ] **Bước 5.2**: Chạy kiểm thử tự động toàn diện:
  - Lệnh: `.venv/bin/pytest tests/test_pack_manager.py tests/test_linked_folders.py tests/test_asset_hub_e2e.py -v`
- [ ] **Bước 5.3**: Chụp ảnh giao diện Studio mới (`test_studio_asset_hub.png`) với tab quản lý nguồn trực quan.
- [ ] **Bước 5.4**: Commit hoàn thiện tính năng và báo cáo nghiệm thu cho người dùng.
