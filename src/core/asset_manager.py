import os
import sys
import shutil
from pathlib import Path
from typing import Optional, Union, Tuple, List
from src.utils.path_utils import PosixPathAdapter


class AssetManager:
    """
    Trình quản lý tập trung việc cài đặt và đồng bộ hóa tài nguyên
    (LUTs .cube, Fusion Templates .setting cho Titles, Transitions, Generators)
    chuẩn hóa cho DaVinci Resolve trên Linux, macOS và Windows.
    """

    SUBDIR_NAME = "ResolveFlow"

    @classmethod
    def get_fusion_template_dir(cls, category: str = "Titles") -> Path:
        """
        Lấy đường dẫn thư mục cài đặt Fusion Templates của DaVinci Resolve:
        - Linux: ~/.local/share/DaVinciResolve/Fusion/Templates/Edit/{Category}/ResolveFlow/
        - macOS: ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Templates/Edit/{Category}/ResolveFlow/
        - Windows: %APPDATA%/Blackmagic Design/DaVinci Resolve/Support/Fusion/Templates/Edit/{Category}/ResolveFlow/
        """
        cat_clean = category.capitalize()
        if sys.platform.startswith("linux"):
            base = Path.home() / ".local" / "share" / "DaVinciResolve" / "Fusion" / "Templates" / "Edit" / cat_clean
        elif sys.platform.startswith("darwin"):
            base = Path.home() / "Library" / "Application Support" / "Blackmagic Design" / "DaVinci Resolve" / "Fusion" / "Templates" / "Edit" / cat_clean
        else:
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                base = Path(appdata) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Templates" / "Edit" / cat_clean
            else:
                base = Path.home() / "AppData" / "Roaming" / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Templates" / "Edit" / cat_clean

        target = base / cls.SUBDIR_NAME
        target.mkdir(parents=True, exist_ok=True)
        return target

    @classmethod
    def get_lut_install_dir(cls) -> Path:
        """
        Lấy đường dẫn thư mục cài đặt LUT của DaVinci Resolve:
        - Linux: Ưu tiên /var/BlackmagicDesign/DaVinci Resolve/Support/LUT/ResolveFlow/
                 (nếu có quyền ghi), fallback sang ~/.local/share/DaVinciResolve/LUT/ResolveFlow/
        - macOS: /Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/ResolveFlow/
        - Windows: C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/LUT/ResolveFlow/
        """
        if sys.platform.startswith("linux"):
            # 1. Thử đường dẫn hệ thống chuẩn /var
            var_lut = Path("/var/BlackmagicDesign/DaVinci Resolve/Support/LUT")
            try:
                # Kiểm tra quyền ghi tại thư mục cha hoặc tạo thư mục
                if var_lut.exists() and os.access(str(var_lut), os.W_OK):
                    target = var_lut / cls.SUBDIR_NAME
                    target.mkdir(parents=True, exist_ok=True)
                    return target
                elif var_lut.parent.exists() and os.access(str(var_lut.parent), os.W_OK):
                    var_lut.mkdir(parents=True, exist_ok=True)
                    target = var_lut / cls.SUBDIR_NAME
                    target.mkdir(parents=True, exist_ok=True)
                    return target
            except Exception:
                pass

            # 2. Thử /opt/resolve/LUT nếu writable
            opt_lut = Path("/opt/resolve/LUT")
            if opt_lut.exists() and os.access(str(opt_lut), os.W_OK):
                target = opt_lut / cls.SUBDIR_NAME
                target.mkdir(parents=True, exist_ok=True)
                return target

            # 3. Fallback an toàn vào user share
            user_lut = Path.home() / ".local" / "share" / "DaVinciResolve" / "LUT" / cls.SUBDIR_NAME
            user_lut.mkdir(parents=True, exist_ok=True)
            return user_lut

        elif sys.platform.startswith("darwin"):
            sys_lut = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT")
            if sys_lut.exists() and os.access(str(sys_lut), os.W_OK):
                target = sys_lut / cls.SUBDIR_NAME
            else:
                target = Path.home() / "Library" / "Application Support" / "Blackmagic Design" / "DaVinci Resolve" / "LUT" / cls.SUBDIR_NAME
            target.mkdir(parents=True, exist_ok=True)
            return target
        else:
            # Windows
            prog_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
            target = Path(prog_data) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "LUT" / cls.SUBDIR_NAME
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception:
                appdata = os.environ.get("APPDATA", "")
                target = Path(appdata) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "LUT" / cls.SUBDIR_NAME
                target.mkdir(parents=True, exist_ok=True)
            return target

    @classmethod
    def install_setting_file(cls, src_path: Union[str, Path], category: str = "Titles", custom_name: Optional[str] = None) -> Path:
        """Cài đặt một tệp Fusion .setting vào đúng thư mục Edit Templates."""
        src = PosixPathAdapter.resolve_path(src_path)
        dest_dir = cls.get_fusion_template_dir(category)
        filename = custom_name if custom_name else src.name
        if not filename.startswith("ResolveFlow_"):
            filename = f"ResolveFlow_{filename}"
        dest = dest_dir / filename
        shutil.copy2(src, dest)
        return dest

    @classmethod
    def install_lut_file(cls, src_path: Union[str, Path], custom_name: Optional[str] = None) -> Path:
        """Cài đặt một tệp LUT .cube vào đúng thư mục LUT của DaVinci Resolve."""
        src = PosixPathAdapter.resolve_path(src_path)
        dest_dir = cls.get_lut_install_dir()
        filename = custom_name if custom_name else src.name
        dest = dest_dir / filename
        shutil.copy2(src, dest)
        return dest
