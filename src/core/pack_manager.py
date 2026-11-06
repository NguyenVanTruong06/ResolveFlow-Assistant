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
    "macros": Path("templates") / "titles",
    "macro": Path("templates") / "titles",
    "generators": Path("templates") / "Generators",
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

    INSTALLED_PACKS_FILE = Path("data") / "installed_packs.json"

    COMMUNITY_PACK_ALIASES: Dict[str, List[str]] = {
        "acidbite_elements_sfx": ["acidbite", "elements", "sfx"],
        "davinci_speed_ramp_transitions": ["speed_ramp", "speedramp", "seamless"],
        "davinci_gr44_seamless_transitions": ["gr44", "gr-44", "gr_44"],
        "fusion_skin_retouching_macros": ["skin_retouching", "retouching", "skin"],
        "davinci_unfold_paper_animator": ["unfold_paper", "paper_animator", "unfold", "paper"],
        "secret_sauce_cinema_luts": ["secret_sauce", "secret sauce", "secret"],
        "iphone_cinematic_luts": ["iphone", "apple_log", "apple log", "applelog"],
        "green_screen_memes_109": ["109", "green_screen", "greenscreen", "tong_hop_meme", "meme_109"],
        "dehancer_pro_ofx_plugin": ["dehancer"],
    }

    @classmethod
    def get_installed_packs(cls, verify_files: bool = True) -> Dict[str, Any]:
        """Lấy danh sách các gói đã cài đặt trên máy từ data/installed_packs.json.
        Khi verify_files=True (mặc định), các gói có danh sách installed_paths nhưng toàn bộ
        tệp trên ổ cứng đã bị xóa (ví dụ thư mục tạm pytest hoặc dọn kho) sẽ được lọc bỏ.
        """
        if not cls.INSTALLED_PACKS_FILE.exists():
            return {}
        try:
            with open(cls.INSTALLED_PACKS_FILE, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception:
            return {}
        if not isinstance(raw_data, dict):
            return {}
        if not verify_files:
            return raw_data
        verified: Dict[str, Any] = {}
        for pack_id, entry in raw_data.items():
            if not isinstance(entry, dict):
                continue
            paths = entry.get("installed_paths")
            if isinstance(paths, list) and len(paths) > 0:
                if not any(Path(p).exists() for p in paths):
                    continue
            verified[pack_id] = entry
        return verified

    @classmethod
    def is_pack_installed(cls, pack_id: str) -> bool:
        """Kiểm tra một gói đã cài đặt vào máy và tệp vẫn còn tồn tại trên ổ đĩa hay chưa."""
        if not pack_id:
            return False
        installed = cls.get_installed_packs(verify_files=True)
        if pack_id in installed:
            return True
        target = str(pack_id).strip().lower()
        aliases = [a.lower() for a in cls.COMMUNITY_PACK_ALIASES.get(target, [])]

        for installed_id, entry in installed.items():
            iid = str(installed_id).strip().lower()
            if target == iid or target in iid or iid in target:
                return True
            entry_name = str(entry.get("name", "")).strip().lower()
            entry_folder = str(entry.get("folder_name", "")).strip().lower()
            if target == entry_name or target in entry_name or entry_name in target:
                return True
            if aliases and any(a in entry_name or a in entry_folder for a in aliases):
                return True

        # Dự phòng: Kiểm tra thêm trong StorageManager (registry packs)
        try:
            from src.core.storage_manager import StorageManager
            sm = StorageManager()
            if sm.online:
                reg = sm.registry()
                for p in reg.get('packs', []):
                    pid = str(p.get('id', '')).strip().lower()
                    pname = str(p.get('name', '')).strip().lower()
                    folder_rel = str(p.get('folder', '')).strip()
                    pfolder = folder_rel.lower()

                    matched = False
                    if target == pid or target in pid or pid in target:
                        matched = True
                    elif target == pname or target in pname or pname in target:
                        matched = True
                    elif aliases and any(a in pname or a in pfolder for a in aliases):
                        matched = True

                    if matched and folder_rel and (sm.root / folder_rel).exists():
                        # Tự động đồng bộ vào installed_packs.json để cache lâu dài
                        pack_files = [str(f) for f in (sm.root / folder_rel).glob("**/*") if f.is_file()][:10]
                        cls.record_installed_pack(pack_id, folder_name=p.get('name', pack_id), installed_paths=pack_files)
                        return True
        except Exception:
            pass
        return False

    @classmethod
    def record_installed_pack(
        cls,
        manifest_or_id: Any,
        folder_name: Optional[str] = None,
        installed_paths: Optional[List[str]] = None
    ) -> None:
        """Ghi nhận một gói đã cài đặt thành công vào hệ thống."""
        installed = cls.get_installed_packs()
        if isinstance(manifest_or_id, PackManifest):
            pack_id = manifest_or_id.id
            entry = {
                "id": pack_id,
                "name": manifest_or_id.name,
                "version": manifest_or_id.version,
                "author": manifest_or_id.author,
                "folder_name": folder_name or manifest_or_id.name,
                "installed_paths": installed_paths or [],
                "installed_at": datetime.datetime.now().isoformat()
            }
        else:
            pack_id = str(manifest_or_id)
            entry = {
                "id": pack_id,
                "name": folder_name or pack_id,
                "version": "1.0.0",
                "folder_name": folder_name or pack_id,
                "installed_paths": installed_paths or [],
                "installed_at": datetime.datetime.now().isoformat()
            }
        installed[pack_id] = entry
        cls.INSTALLED_PACKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(cls.INSTALLED_PACKS_FILE, "w", encoding="utf-8") as f:
                json.dump(installed, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @classmethod
    def uninstall_pack(cls, pack_id: str) -> bool:
        """Gỡ bỏ toàn bộ tệp và thư mục của một gói đã cài đặt."""
        installed = cls.get_installed_packs()
        if pack_id not in installed:
            return False
        entry = installed[pack_id]
        paths = entry.get("installed_paths", [])
        for p in paths:
            try:
                p_obj = Path(p)
                if p_obj.is_file():
                    p_obj.unlink()
                # Thử xóa thư mục cha nếu trống
                parent_dir = p_obj.parent
                if parent_dir.is_dir() and not any(parent_dir.iterdir()):
                    parent_dir.rmdir()
            except Exception:
                pass
        del installed[pack_id]
        try:
            with open(cls.INSTALLED_PACKS_FILE, "w", encoding="utf-8") as f:
                json.dump(installed, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return True

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

    @classmethod
    def install_pack(
        cls,
        pack_path: str,
        target_base_dir: str = "assets",
        pack_manifest: Optional[PackManifest] = None,
        isolate_pack_folder: bool = True
    ) -> Dict[str, int]:
        """
        Giải nén và cài đặt gói .rfpack vào thư mục tài nguyên của ResolveFlow.
        Tự động phân nhóm theo thư mục con riêng biệt của từng gói để chống lẫn lộn.
        Có cơ chế bảo vệ chống Zip-Slip (Path Traversal).
        """
        import re
        pack = Path(pack_path)
        if not pack.is_file():
            raise FileNotFoundError(f"Không tìm thấy gói cài đặt: {pack_path}")

        base_dest = Path(target_base_dir).resolve()
        base_dest.mkdir(parents=True, exist_ok=True)

        manifest = pack_manifest
        if manifest is None:
            try:
                manifest = cls.inspect_pack(pack_path)
            except Exception:
                manifest = None

        pack_name = manifest.name if manifest else pack.stem
        safe_pack_folder = re.sub(r'[\\/*?:"<>|]', "", pack_name).strip() if isolate_pack_folder else ""

        installed_counts: Dict[str, int] = {}
        all_installed_files: List[str] = []

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

            # 2. Tiến hành giải nén an toàn theo danh mục vào thư mục con riêng của gói
            for member in zf.infolist():
                if member.is_dir() or member.filename == "manifest.json":
                    continue

                parts = Path(member.filename).parts
                if not parts:
                    continue

                cat_prefix = parts[0].lower()
                filename = parts[-1]
                ext = Path(filename).suffix.lower()
                lower_full_path = member.filename.lower()
                lower_fn = filename.lower()

                # Ánh xạ danh mục thông minh:
                # 1) Kiểm tra tài liệu hướng dẫn / tutorial / text help
                if any(k in lower_full_path for k in ["tutorial", "help/", "readme", "instruction", "only studio", "license"]):
                    sub_dir = Path("docs") / "packs"
                    cat_key = "docs"
                # 2) Kiểm tra video/ảnh demo preview chuyển cảnh
                elif any(k in lower_full_path for k in [
                    "preview transition", "transitions preview", "transition preview",
                    "transitions/preview", "transition/preview", "preview transitions"
                ]) or ("trans" in lower_full_path and any(w in lower_fn for w in ["bounce", "spin", "pan", "zoom", "fisheye", "persp", "flight", "warp", "slide", "roll"])):
                    sub_dir = Path("templates") / "transitions" / "previews"
                    cat_key = "transitions"
                # 3) Nếu cat_prefix khớp danh mục chuẩn ResolveFlow
                elif cat_prefix in CATEGORY_DIR_MAP:
                    sub_dir = CATEGORY_DIR_MAP[cat_prefix]
                    cat_key = cat_prefix
                # 4) Nhận diện theo định dạng tệp chuẩn DaVinci / Media:
                elif ext in [".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac"]:
                    sub_dir = Path("sfx")
                    cat_key = "sfx"
                elif ext in [".cube"]:
                    sub_dir = Path("luts")
                    cat_key = "luts"
                elif ext in [".drx", ".dpx"]:
                    sub_dir = Path("luts") / "powergrades"
                    cat_key = "luts"
                elif ext in [".drfx", ".setting"]:
                    if "trans" in lower_full_path:
                        sub_dir = Path("templates") / "transitions"
                        cat_key = "transitions"
                    else:
                        sub_dir = Path("templates") / "titles"
                        cat_key = "titles"
                elif ext in [".mp4", ".mov", ".webm", ".avi", ".wmv"]:
                    if any(k in lower_full_path for k in ["overlay", "light_leak", "light leak", "dust", "film_grain"]):
                        sub_dir = Path("broll_memes") / "overlays"
                        cat_key = "overlays"
                    elif "trans" in lower_full_path or "transition" in lower_full_path:
                        sub_dir = Path("templates") / "transitions" / "previews"
                        cat_key = "transitions"
                    else:
                        sub_dir = Path("broll_memes") / "memes"
                        cat_key = "memes"
                elif ext in [".png", ".jpg", ".jpeg"]:
                    if "trans" in lower_full_path or "transition" in lower_full_path:
                        sub_dir = Path("templates") / "transitions" / "previews"
                        cat_key = "transitions"
                    elif "title" in lower_full_path or "text" in lower_full_path:
                        sub_dir = Path("templates") / "titles"
                        cat_key = "titles"
                    elif "lut" in lower_full_path:
                        sub_dir = Path("luts")
                        cat_key = "luts"
                    elif "meme" in lower_full_path:
                        sub_dir = Path("broll_memes") / "memes"
                        cat_key = "memes"
                    else:
                        sub_dir = Path("broll_memes") / "overlays"
                        cat_key = "overlays"
                else:
                    sub_dir = CATEGORY_DIR_MAP.get(cat_prefix, Path("broll_memes") / "memes")
                    cat_key = cat_prefix

                # Phân lập thư mục theo gói
                if safe_pack_folder:
                    dest_dir = base_dest / sub_dir / safe_pack_folder
                else:
                    dest_dir = base_dest / sub_dir

                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = dest_dir / filename

                # Ghi nội dung tệp
                with zf.open(member) as src_file, open(dest_file, "wb") as dst_file:
                    dst_file.write(src_file.read())

                installed_counts[cat_key] = installed_counts.get(cat_key, 0) + 1
                all_installed_files.append(str(dest_file))

        # Ghi nhận trạng thái gói đã cài
        cls.record_installed_pack(
            manifest_or_id=manifest if manifest else pack.stem,
            folder_name=safe_pack_folder or pack.stem,
            installed_paths=all_installed_files
        )

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


# Danh mục các bộ Pack cộng đồng tuyển chọn trực tiếp từ Google Drive Kho ChunnDVC
COMMUNITY_PACKS_CATALOG: List[PackManifest] = [
    PackManifest(
        id="acidbite_elements_sfx",
        name="Kho Âm Hiệu Điện Ảnh AcidBite Elements SFX",
        version="1.0.0",
        author="ChunnDVC Vault",
        description="147 âm thanh Whoosh, Hit, Impact, Cinematic sound 24-bit WAV cao cấp chuẩn phòng thu điện ảnh.",
        categories=["sfx"],
        item_count=147,
        size_mb=962.3,
        download_url="https://drive.google.com/file/d/1kva0qYbZF1tOJ7P_DObbkP7b_HPN-7V1/view?usp=sharing",
        tags=["sfx", "whoosh", "cinematic", "impact", "wav"]
    ),
    PackManifest(
        id="davinci_speed_ramp_transitions",
        name="Gói Chuyển Cảnh Speed Ramp Seamless Transitions",
        version="1.0.0",
        author="ChunnDVC Vault",
        description="Bộ chuyển cảnh mượt mà Speed Ramp chuẩn DaVinci Resolve (.drfx) cài 1-click tự nhận diện.",
        categories=["transitions"],
        item_count=12,
        size_mb=97.5,
        download_url="https://drive.google.com/file/d/16zqeF1TClWTAC-53MaTtrYu3odfeb18K/view?usp=sharing",
        tags=["transitions", "drfx", "speedramp", "seamless", "davinci"]
    ),
    PackManifest(
        id="davinci_gr44_seamless_transitions",
        name="Gói Chuyển Cảnh GR-44 v1.4 & Sound FX",
        version="1.4.0",
        author="ChunnDVC Vault",
        description="Bộ chuyển cảnh chuyển động GR-44 cho DaVinci Resolve Studio & Free kèm 119 file âm thanh SFX.",
        categories=["transitions", "sfx"],
        item_count=1130,
        size_mb=288.1,
        download_url="https://drive.google.com/file/d/12mouRJ7wbsJIe4bhABnoxo-MhOHw6wU9/view?usp=sharing",
        tags=["transitions", "gr44", "sfx", "motion", "davinci"]
    ),
    PackManifest(
        id="fusion_skin_retouching_macros",
        name="Gói Macro Tút Da Mịn Mặt DaVinci (Skin Retouching)",
        version="1.0.0",
        author="ChunnDVC Vault",
        description="Macro Fusion chuyên dụng làm mịn da, xóa mụn tự động trực tiếp trên DaVinci Resolve (.setting, .drfx).",
        categories=["titles"],
        item_count=6,
        size_mb=0.9,
        download_url="https://drive.google.com/file/d/11Sk70Y_kfhpb1uU14kp_EJsDL3NzW-12/view?usp=sharing",
        tags=["fusion", "macro", "skin", "retouch", "beauty"]
    ),
    PackManifest(
        id="davinci_unfold_paper_animator",
        name="Hiệu Ứng Lật Mở Giấy Unfold Paper Animator",
        version="1.0.0",
        author="ChunnDVC Vault",
        description="Hiệu ứng xếp giấy, lật mở giấy Stop-motion hoạt họa độc đáo cho DaVinci Resolve kèm 70 texture giấy thực.",
        categories=["transitions", "overlays"],
        item_count=74,
        size_mb=17.1,
        download_url="https://drive.google.com/file/d/1Tkr6b0yaNrF9e-jpAIN3pIgC7J8F1Mg5/view?usp=sharing",
        tags=["paper", "stopmotion", "texture", "drfx", "davinci"]
    ),
    PackManifest(
        id="secret_sauce_cinema_luts",
        name="Bộ Bảng Màu Điện Ảnh Secret Sauce LUTs",
        version="1.0.0",
        author="ChunnDVC Vault",
        description="8 bảng màu điện ảnh cao cấp: Ferrari Red, HongKong Blue, Amsterdam Silver... chuẩn .cube 33x.",
        categories=["luts"],
        item_count=8,
        size_mb=21.2,
        download_url="https://drive.google.com/file/d/15S4dumr_su0OJGY7nuf-MQ4RB3FwIRwC/view?usp=sharing",
        tags=["luts", "color", "cinema", "ferrari", "cube"]
    ),
    PackManifest(
        id="iphone_cinematic_luts",
        name="Bộ LUTs Quay iPhone & Apple Log",
        version="1.0.0",
        author="ChunnDVC Vault",
        description="8 bảng màu tối ưu chuẩn xác cho video quay bằng iPhone và hệ màu Apple Log.",
        categories=["luts"],
        item_count=8,
        size_mb=9.7,
        download_url="https://drive.google.com/file/d/17Nl7vS-KcPrrMJquAg2Rs11_oXDfuUtZ/view?usp=sharing",
        tags=["iphone", "applelog", "lut", "mobile", "cube"]
    ),
    PackManifest(
        id="green_screen_memes_109",
        name="Kho 109 Video Meme Phông Xanh (Green Screen)",
        version="1.0.0",
        author="ChunnDVC Vault",
        description="109 video phông xanh hài hước, countdown, meme trend... Dễ dàng tách nền 1-click với 3D Keyer.",
        categories=["memes"],
        item_count=109,
        size_mb=307.7,
        download_url="https://drive.google.com/file/d/1D7ay6vf6c1GOg4JTxuRMoCesg-GQCmbB/view?usp=sharing",
        tags=["meme", "greenscreen", "countdown", "viral", "keyer"]
    ),
    PackManifest(
        id="dehancer_pro_ofx_plugin",
        name="Plugin Giả Lập Phim Nhựa Dehancer Pro OFX",
        version="7.2.1",
        author="ChunnDVC Vault",
        description="Plugin mô phỏng màu film nhựa Kodak & Fuji 35mm đỉnh cao chuyên dụng cho DaVinci Resolve (Win & Mac).",
        categories=["luts"],
        item_count=2,
        size_mb=661.7,
        download_url="https://drive.google.com/file/d/17Ysow6gRJITEOEbgnkU-xHk9cVDLn_ad/view?usp=sharing",
        tags=["dehancer", "ofx", "plugin", "film", "grain"]
    )
]
