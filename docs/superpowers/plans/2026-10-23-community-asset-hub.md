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
- [x] **Bước 1.1**: Viết test ban đầu `tests/test_pack_manager.py`.
- [x] **Bước 1.2**: Chạy pytest để xác nhận test thất bại (Red).
- [x] **Bước 1.3**: Hiện thực `PackManifest` (Pydantic model) và lớp `PackManager` trong `src/core/pack_manager.py`.
- [x] **Bước 1.4**: Chạy lại pytest xác nhận test pass (Green).
- [x] **Bước 1.5**: Commit code Task 1 (`ece5046`).

---

### Nhiệm Vụ 2: Nâng Cấp `AssetIndexer` Hỗ Trợ Nối Thư Mục Ngoài (Multi-Root) & Cache Tức Thì
- [x] **Bước 2.1**: Viết test `tests/test_linked_folders.py`.
- [x] **Bước 2.2**: Chạy pytest xác nhận test thất bại (Red).
- [x] **Bước 2.3**: Chỉnh sửa `src/core/asset_indexer.py`.
- [x] **Bước 2.4**: Chạy lại pytest xác nhận test pass (Green).
- [x] **Bước 2.5**: Commit code Task 2 (`a6f77c8`).

---

### Nhiệm Vụ 3: Luồng Chạy Ngầm Tải Gói Cloud & Danh Mục Tuyển Chọn
- [x] **Bước 3.1**: Định nghĩa danh mục mẫu `COMMUNITY_PACKS_CATALOG` trong `src/core/pack_manager.py`.
- [x] **Bước 3.2**: Hiện thực `CloudPackInstallWorker(QThread)` trong `src/ui/tabs/cloud_pack_worker.py`.
- [x] **Bước 3.3**: Viết test `tests/test_cloud_pack_worker.py` kiểm thử xử lý tín hiệu và hủy tải an toàn.
- [x] **Bước 3.4**: Commit code Task 3 (`2f403a7`).

---

### Nhiệm Vụ 4: Xây Dựng Giao Diện Tab "Kho Online & Quản Lý Nguồn" Trong Studio
- [x] **Bước 4.1**: Tạo widget `AssetHubWidget` trong `src/ui/tabs/tab_asset_hub.py`.
- [x] **Bước 4.2**: Chỉnh sửa `src/ui/tabs/tab_assets.py`.
- [x] **Bước 4.3**: Chỉnh sửa `src/ui/lux/shell/edge_dock.py`.
- [x] **Bước 4.4**: Commit code Task 4 (`941ff29`).

---

### Nhiệm Vụ 5: Kiểm Thử Tích Hợp Toàn Diện (End-to-End Acceptance) & Minh Chứng Trực Quan
- [x] **Bước 5.1**: Viết kịch bản kiểm thử toàn diện `tests/test_asset_hub_e2e.py`.
- [x] **Bước 5.2**: Chạy kiểm thử tự động toàn diện (8 passed).
- [x] **Bước 5.3**: Chụp ảnh giao diện Studio mới (`test_studio_asset_hub.png`) với tab quản lý nguồn trực quan.
- [x] **Bước 5.4**: Commit hoàn thiện tính năng và báo cáo nghiệm thu cho người dùng (`562fe4a`).
