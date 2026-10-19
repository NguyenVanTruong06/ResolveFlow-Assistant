# Spec: Geometry & Layout Conflict Checker (Phase 2)

> **Mã đặc tả:** `SPEC-2026-10-19-SEECUT-PHASE2`  
> **Dự án:** ResolveFlow Assistant (tích hợp tư duy SeeCut)  
> **Trạng thái:** Chờ duyệt (Pending Review)

---

## 1. Mục Tiêu & Bối Cảnh (Context & Goal)

Trong video tự động (đặc biệt là video ngắn 9:16 cho TikTok, YouTube Shorts, Facebook/Instagram Reels và video 16:9), việc tự động đặt phụ đề (Subtitles), cửa sổ phóng to/thu nhỏ (PiP), và các đồ họa minh chứng rất dễ gặp các vấn đề xung đột bố cục:
1. **Phụ đề đè mặt người nói (Subtitle-Face Collision):** Người nói ngồi gần máy quay hoặc khung hình cận cảnh khiến mặt/cằm nằm ở nửa dưới, phụ đề đặt mặc định ở đáy ($Y \approx 0.80 - 0.90$) đè trực tiếp lên mặt.
2. **Cửa sổ PiP che khuất chủ thể (PiP-Speaker Occlusion):** Cửa sổ Picture-in-Picture của minh chứng web vô tình đặt trúng góc mà người nói đang đứng.
3. **Lẹm vào vùng giao diện nền tảng (Platform UI Safe Zone Violation):** Các nút Like, Comment, Share của TikTok/Reels ở mép phải ($X > 0.85$) và thanh caption ở đáy ($Y > 0.82$) che khuất chữ phụ đề hoặc sticker.

**Mục tiêu Phase 2:** Xây dựng module `src/core/geometry_checker.py` cung cấp thuật toán hình học 2D AABB cực nhanh ($< 0.01$s) để tự động phát hiện mọi xung đột không gian và dịch chuyển thông minh vị trí phụ đề, góc PiP nhằm đảm bảo video xuất ra DaVinci Resolve luôn chuyên nghiệp, thoáng đãng và không bị che lấp.

---

## 2. Kiến Trúc Dữ Liệu & Khái Niệm Cốt Lõi

### 2.1. Hệ Tọa Độ Chuẩn Hóa (Normalized Coordinate Space)
Mọi đối tượng được biểu diễn trên không gian 2D chuẩn hóa $[0.0, 1.0] \times [0.0, 1.0]$:
- Gốc $(0.0, 0.0)$ ở góc trên bên trái (Top-Left).
- Điểm $(1.0, 1.0)$ ở góc dưới bên phải (Bottom-Right).
- Tâm màn hình là $(0.5, 0.5)$.

### 2.2. Lớp `BoundingBox` (Hộp Bao Hình Học 2D)
```python
class BoundingBox(BaseModel):
    x_min: float = Field(..., ge=0.0, le=1.0)
    y_min: float = Field(..., ge=0.0, le=1.0)
    x_max: float = Field(..., ge=0.0, le=1.0)
    y_max: float = Field(..., ge=0.0, le=1.0)
```
- Các hàm tính toán:
  - `intersects(other: BoundingBox) -> bool`: Kiểm tra 2 hộp có giao nhau hay không.
  - `intersection_area(other: BoundingBox) -> float`: Diện tích phần chồng lấn.
  - `iou(other: BoundingBox) -> float`: Chỉ số Intersection over Union.
  - `clamp(container: BoundingBox) -> BoundingBox`: Kẹp hộp con nằm hoàn toàn trong hộp cha (Safe Zone).

### 2.3. Vùng An Toàn Nền Tảng (`SafeZonePreset`)
- **`TIKTOK_9_16`:**
  - Vùng hiển thị an toàn: `x_min=0.06, y_min=0.12, x_max=0.84, y_max=0.82` (tránh thanh tìm kiếm trên, nút like/comment phải, caption dưới).
- **`YOUTUBE_SHORTS_9_16`:**
  - `x_min=0.06, y_min=0.10, x_max=0.84, y_max=0.82`.
- **`REELS_9_16`:**
  - `x_min=0.06, y_min=0.10, x_max=0.86, y_max=0.80`.
- **`STANDARD_16_9` (Action/Title Safe):**
  - Title Safe (10% viền): `x_min=0.10, y_min=0.10, x_max=0.90, y_max=0.90`.

---

## 3. Các Chiến Lược Giải Quyết Xung Đột (Resolution Strategies)

### 3.1. Chiến Lược 1: Nâng Cao Độ Phụ Đề (Subtitle Elevation)
- Mặc định phụ đề nằm ở vùng đáy màn hình: `y_min=0.80, y_max=0.92`.
- Khi người nói có `face_box.y_max > 0.70` (mặt lấn xuống vùng đáy):
  - Phụ đề được nâng lên vùng ngực/bụng an toàn: `y_min = face_box.y_max + 0.04` (nếu còn chỗ phía dưới) HOẶC chuyển lên ngực trên `y = face_box.y_min - subtitle_height - 0.04` (nếu mặt ở quá thấp).
  - Tự động ghi lại `offset_y` để truyền vào `FCPXMLGenerator` điều chỉnh thuộc tính vị trí Text+.

### 3.2. Chiến Lược 2: Đảo Góc PiP Thông Minh (Smart PiP Corner Flipping)
- Khi có Evidence, PiP cần 1 góc để người nói co lại ($0.35\times$).
- 4 góc ứng viên:
  1. `bottom_right`: `x: 0.60 -> 0.95, y: 0.60 -> 0.95`
  2. `bottom_left`: `x: 0.05 -> 0.40, y: 0.60 -> 0.95`
  3. `top_right`: `x: 0.60 -> 0.95, y: 0.05 -> 0.40`
  4. `top_left`: `x: 0.05 -> 0.40, y: 0.05 -> 0.40`
- Thuật toán chấm điểm góc tối ưu:
  - Loại bỏ các góc có diện tích chồng lấn với `face_box` > 0.
  - Ưu tiên góc ở dưới (`bottom_right` hoặc `bottom_left`).
  - Nếu mặt người nói ở nửa phải $\rightarrow$ Chọn `bottom_left`.
  - Nếu mặt người nói ở nửa trái $\rightarrow$ Chọn `bottom_right`.
  - Nếu mặt người nói ở chính giữa đáy $\rightarrow$ Chọn `top_right`.

### 3.3. Chiến Lược 3: Kẹp Biên An Toàn (Safe Zone Clamping)
- Mọi văn bản và phần tử đồ họa được kẹp (`clamp`) sao cho không chạm vào mép viền cấm của TikTok/Shorts.

---

## 4. Đặc Tả Chi Tiết Module `src/core/geometry_checker.py`

### 4.1. Lớp `GeometryConflictChecker`
```python
class GeometryConflictChecker:
    def __init__(self, platform: str = "tiktok", aspect_ratio: str = "9:16"):
        self.platform = platform
        self.aspect_ratio = aspect_ratio
        self.safe_zone = self._get_safe_zone(platform, aspect_ratio)

    def check_subtitle_face_collision(
        self,
        face_box: BoundingBox,
        subtitle_box: Optional[BoundingBox] = None
    ) -> Tuple[bool, BoundingBox]:
        """Phát hiện va chạm giữa mặt và phụ đề, trả về (has_collision, adjusted_subtitle_box)."""

    def resolve_pip_placement(
        self,
        face_box: BoundingBox,
        pip_scale: float = 0.35
    ) -> Tuple[float, float, str]:
        """Tính toán (pan_x, pan_y, corner_name) tối ưu tránh đè mặt người nói."""

    def audit_shotbook_plan(
        self,
        shotbook_plan: ShotbookPlan,
        face_detections: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[ShotbookPlan, List[Dict[str, Any]]]:
        """Rà soát và khử toàn bộ xung đột trên ShotbookPlan."""
```

---

## 5. Kế Hoạch Kiểm Thử (Verification Plan)

1. **Unit Test `test_bounding_box.py`:**
   - Kiểm tra `intersects`, `intersection_area`, `iou`, `clamp` với các tọa độ chuẩn.
2. **Unit Test `test_geometry_checker.py`:**
   - Kiểm tra va chạm mặt người ở dưới đáy $\rightarrow$ Phụ đề tự động dời lên vị trí an toàn.
   - Kiểm tra mặt người bên trái $\rightarrow$ PiP tự dời sang góc dưới phải (`pan_x > 0`).
   - Kiểm tra mặt người bên phải $\rightarrow$ PiP tự dời sang góc dưới trái (`pan_x < 0`).
   - Kiểm tra mặt người ở giữa đáy $\rightarrow$ PiP tự dời lên góc trên.
   - Kiểm tra kẹp phụ đề không bị lẹm nút Like/Share của TikTok.
3. **Hồi quy toàn bộ test suite:** Đảm bảo 343 tests hiện có tiếp tục PASS 100%.
