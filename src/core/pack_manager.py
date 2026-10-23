"""
ResolveFlow Pack Manager Engine.
Xử lý đóng gói, kiểm tra manifest, xác thực bảo mật và giải nén các gói đạo cụ .rfpack cho cộng đồng.
"""

import os
import json
import zipfile
import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field


class SecurityError(Exception):
    """Lỗi an ninh khi giải nén tệp (chống zip-slip / path traversal)."""
    pass


class PackManifest(BaseModel):
    """Thông tin mô tả tệp gói ResolveFlow (.rfpack)."""
    id: str
    name: str
    version: str = "1.0.0"
    author: str = "Community"
    description: str = ""
    categories: List[str] = Field(default_factory=list)
    item_count: int = 0
    created_at: str = Field(default_factory=lambda: datetime.datetime.now().isoformat())
    download_url: Optional[str] = None
    size_mb: Optional[float] = None
    tags: List[str] = Field(default_factory=list)


# Ánh xạ danh mục trong pack sang cấu trúc thư mục nội bộ của ResolveFlow
CATEGORY_DIR_MAP = {
    "meme": Path("broll_memes") / "memes",
    "memes": Path("broll_memes") / "memes",
    "sfx": Path("sfx"),
    "titles": Path("templates") / "titles",
    "title": Path("templates") / "titles",
    "text": Path("templates") / "titles",
    "transitions": Path("templates") / "transitions",
    "transition": Path("templates") / "transitions",
    "trans": Path("templates") / "transitions",
    "overlays": Path("broll_memes") / "overlays",
    "overlay": Path("broll_memes") / "overlays",
    "luts": Path("luts"),
    "lut": Path("luts"),
    "music": Path("music"),
    "bgm": Path("music"),
}


class PackManager:
    """Bộ máy quản lý đóng gói & cài đặt gói tài nguyên ResolveFlow."""

    @staticmethod
    def create_pack(
        output_path: str,
        manifest: PackManifest,
        category_files: Dict[str, List[str]]
    ) -> str:
        """
        Đóng gói các tệp tài nguyên thành 1 file .rfpack duy nhất.
        """
        out = Path(output_path)
        if not out.name.endswith(".rfpack"):
            out = out.with_suffix(".rfpack")
        out.parent.mkdir(parents=True, exist_ok=True)

        # Tính tổng số item nếu chưa gán
        total_items = sum(len(files) for files in category_files.values())
        manifest.item_count = total_items
        manifest.categories = list(category_files.keys())

        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Ghi manifest.json vào gốc
            manifest_json = manifest.model_dump_json(indent=2)
            zf.writestr("manifest.json", manifest_json)

            # Ghi các tệp tài nguyên theo thư mục danh mục
            for cat, file_paths in category_files.items():
                cat_clean = cat.strip().lower()
                for fp in file_paths:
                    p = Path(fp)
                    if p.is_file():
                        arc_name = f"{cat_clean}/{p.name}"
                        zf.write(p, arcname=arc_name)

        return str(out.resolve())

    @staticmethod
    def inspect_pack(pack_path: str) -> PackManifest:
        """
        Đọc và phân tích thông tin tệp gói .rfpack mà không cần giải nén toàn bộ.
        """
        p = Path(pack_path)
        if not p.is_file():
            raise FileNotFoundError(f"Không tìm thấy gói: {pack_path}")

        with zipfile.ZipFile(p, "r") as zf:
            if "manifest.json" not in zf.namelist():
                # Fallback manifest cho các file zip thông thường
                items = [n for n in zf.namelist() if not n.endswith("/")]
                return PackManifest(
                    id=p.stem,
                    name=p.stem.replace("_", " ").title(),
                    version="1.0.0",
                    description=f"Gói tệp nén chứa {len(items)} tài nguyên",
                    item_count=len(items)
                )

            manifest_bytes = zf.read("manifest.json")
            manifest_dict = json.loads(manifest_bytes.decode("utf-8"))
            return PackManifest.model_validate(manifest_dict)

    @staticmethod
    def install_pack(pack_path: str, target_base_dir: str = "assets") -> Dict[str, int]:
        """
        Giải nén và cài đặt gói .rfpack vào thư mục tài nguyên của ResolveFlow.
        Có cơ chế bảo vệ chống Zip-Slip (Path Traversal).
        """
        pack = Path(pack_path)
        if not pack.is_file():
            raise FileNotFoundError(f"Không tìm thấy gói cài đặt: {pack_path}")

        base_dest = Path(target_base_dir).resolve()
        base_dest.mkdir(parents=True, exist_ok=True)

        installed_counts: Dict[str, int] = {}

        with zipfile.ZipFile(pack, "r") as zf:
            # 1. Quét kiểm tra an toàn toàn bộ file trước khi giải nén (Anti Zip-Slip)
            for member in zf.infolist():
                if member.is_dir() or member.filename == "manifest.json":
                    continue
                # Chuẩn hóa đường dẫn đích
                target_check = (base_dest / member.filename).resolve()
                try:
                    common = os.path.commonpath([str(base_dest), str(target_check)])
                    if common != str(base_dest):
                        raise SecurityError(
                            f"Phát hiện đường dẫn độc hại trong gói: {member.filename}"
                        )
                except ValueError:
                    raise SecurityError(f"Đường dẫn vượt ngoài thư mục đích: {member.filename}")

            # 2. Tiến hành giải nén an toàn theo danh mục
            for member in zf.infolist():
                if member.is_dir() or member.filename == "manifest.json":
                    continue

                parts = Path(member.filename).parts
                if not parts:
                    continue

                cat_prefix = parts[0].lower()
                filename = parts[-1]

                # Ánh xạ danh mục
                sub_dir = CATEGORY_DIR_MAP.get(cat_prefix, Path("broll_memes") / "memes")
                dest_dir = base_dest / sub_dir
                dest_dir.mkdir(parents=True, exist_ok=True)

                dest_file = dest_dir / filename

                # Ghi nội dung tệp
                with zf.open(member) as src_file, open(dest_file, "wb") as dst_file:
                    dst_file.write(src_file.read())

                installed_counts[cat_prefix] = installed_counts.get(cat_prefix, 0) + 1

        return installed_counts

    @staticmethod
    def fetch_community_catalog(remote_url: Optional[str] = None) -> List[PackManifest]:
        """
        Tải danh mục gói mới nhất từ máy chủ từ xa (GitHub/Cloud).
        Nếu mất kết nối mạng hoặc chưa cấu hình URL, tự động dùng danh mục tích hợp sẵn.
        """
        import urllib.request
        from PySide6.QtCore import QSettings

        settings = QSettings("ChunDVC", "ResolveFlow")
        url = remote_url or str(settings.value(
            "hub/catalog_url",
            "https://raw.githubusercontent.com/chundvc/resolveflow-packs/main/catalog.json"
        ))

        if url and url.startswith("http"):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ResolveFlow/1.0"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        packs_list = data if isinstance(data, list) else data.get("packs", [])
                        if packs_list:
                            return [PackManifest.model_validate(item) for item in packs_list]
            except Exception:
                pass  # Tự động fallback danh mục cục bộ

        return [p.model_copy() for p in COMMUNITY_PACKS_CATALOG]


# Danh mục các bộ Starter Pack cộng đồng tuyển chọn
COMMUNITY_PACKS_CATALOG: List[PackManifest] = [
    PackManifest(
        id="tiktok_viral_memes_2025",
        name="Gói Meme TikTok Hot Trend 2025",
        version="2.0.0",
        author="ChunDVC Community",
        description="Tổng hợp hơn 150 clip meme cắt sẵn, chuẩn âm thanh, hài hước viral nhất TikTok & Facebook Reels.",
        categories=["meme", "sfx"],
        item_count=165,
        size_mb=128.5,
        tags=["tiktok", "viral", "meme", "hai_huoc"]
    ),
    PackManifest(
        id="youtube_vlog_sfx_essentials",
        name="Gói Âm Hiệu SFX Vlogger & Cinema",
        version="1.5.0",
        author="Audio Lab Pro",
        description="Bộ 80 âm thanh Whoosh, Pop, Click chuột, Ting chuông, Cảnh báo đỏ chuẩn truyền hình điện ảnh.",
        categories=["sfx"],
        item_count=82,
        size_mb=45.2,
        tags=["sfx", "vlog", "whoosh", "ting"]
    ),
    PackManifest(
        id="kinetic_titles_hormozi",
        name="Gói Chữ Động Kinetic Kiểu Alex Hormozi & MrBeast",
        version="1.0.0",
        author="DaVinci Fusion Lab",
        description="25 preset Fusion Text+ Kinetic tự động nảy chữ, đổi màu từ khóa, viền neon, hiệu ứng gõ phím.",
        categories=["titles"],
        item_count=25,
        size_mb=12.0,
        tags=["kinetic", "subtitle", "fusion", "mrbeast"]
    ),
    PackManifest(
        id="cinematic_vintage_luts",
        name="Gói Bảng Màu Điện Ảnh (Cinematic LUTs)",
        version="1.0.0",
        author="Colorist Hub",
        description="20 Cube LUTs tông màu phim nhựa Kodak, Cyberpunk Neon, Teal & Orange chuẩn điện ảnh.",
        categories=["luts"],
        item_count=20,
        size_mb=18.4,
        tags=["lut", "color", "kodak", "cinema"]
    )
]
