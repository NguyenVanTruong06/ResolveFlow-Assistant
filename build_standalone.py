#!/usr/bin/env bash
import os
import sys
import shutil
import argparse
import subprocess
from pathlib import Path
from typing import List, Tuple

# Đảm bảo mã hóa UTF-8 trên Windows console
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

APP_NAME = "ResolveFlow-Assistant"
APP_VERSION = "1.0.0"
ENTRY_POINT = os.path.join("src", "main.py")

def get_data_separator() -> str:
    """Trả về dấu phân cách add-data cho PyInstaller: ';' trên Windows, ':' trên Linux/macOS."""
    return ";" if sys.platform.startswith("win") else ":"

def get_resource_specs() -> List[Tuple[str, str]]:
    """Danh sách các thư mục tài nguyên cần đóng gói kèm ứng dụng."""
    return [
        ("presets", "presets"),
        ("recipes", "recipes"),
        ("src/resolve_scripts", "src/resolve_scripts"),
        ("assets", "assets"),
    ]

def get_hidden_imports() -> List[str]:
    """Danh sách các hidden imports cần thiết cho PySide6, Faster-Whisper, ctranslate2 và core modules."""
    return [
        # AI & Audio engines
        "faster_whisper",
        "ctranslate2",
        "tokenizers",
        "onnxruntime",
        # Data & Image processing
        "pydantic",
        "pydantic_core",
        "ffmpeg",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "numpy",
        # PySide6 Qt6 modules
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtSvg",
        # Core & Engine modules
        "src.core.ai_director",
        "src.core.audio",
        "src.core.audio_beat",
        "src.core.audio_normalizer",
        "src.core.audio_processor",
        "src.core.audit_reporter",
        "src.core.autocut",
        "src.core.broll_sfx",
        "src.core.cache_manager",
        "src.core.cloud_downloader",
        "src.core.fcpxml_generator",
        "src.core.ffmpeg_utils",
        "src.core.folder_scanner",
        "src.core.geometry_checker",
        "src.core.proxy_manager",
        "src.core.queue",
        "src.core.recipe_manager",
        "src.core.resolve_api",
        "src.core.resolve_bridge",
        "src.core.story_arranger",
        "src.core.story_copilot",
        "src.core.story_planner",
        "src.core.text_preset",
        "src.core.transcriber",
        "src.core.validator",
        "src.core.vision_reframer",
        "src.core.vlog_hook",
        "src.core.connection.base",
        "src.core.connection.direct",
        # UI modules
        "src.ui.app",
        "src.ui.auto.auto_window",
        "src.ui.studio.studio_window",
        "src.ui.bubble.floating_bubble",
        "src.ui.components.mini_timeline",
        "src.ui.theme",
        # Utils
        "src.utils.path_utils",
    ]

def get_excludes() -> List[str]:
    """Các thư viện dev / testing bị loại trừ để tối ưu dung lượng phân phối."""
    return [
        "pytest",
        "_pytest",
        "unittest",
        "test",
        "tests",
        "tkinter",
        "IPython",
        "jupyter",
        "matplotlib",
    ]

def post_build_setup(dist_app_dir: str):
    """
    Tạo cấu trúc thư mục khung assets/data và sinh launcher khởi chạy chuẩn trên từng hệ điều hành.
    """
    os.makedirs(dist_app_dir, exist_ok=True)

    # 1. Tạo khung thư mục assets
    sub_assets = ["sfx", "bgm", "luts", "memes", "overlays", "titles", "transitions"]
    for sub in sub_assets:
        p = os.path.join(dist_app_dir, "assets", sub)
        os.makedirs(p, exist_ok=True)
    os.makedirs(os.path.join(dist_app_dir, "data"), exist_ok=True)

    # Copy các file preset/recipe nếu chưa có
    for src_dir in ["presets", "recipes"]:
        dst_dir = os.path.join(dist_app_dir, src_dir)
        if os.path.isdir(src_dir) and not os.path.exists(dst_dir):
            shutil.copytree(src_dir, dst_dir)

    # 2. Sinh launcher cho Linux
    if sys.platform.startswith("linux"):
        launcher_sh = os.path.join(dist_app_dir, "launch_resolveflow.sh")
        sh_content = f"""#!/usr/bin/env bash
# ==============================================================================
# {APP_NAME} v{APP_VERSION} - Standalone Linux Launcher
# ==============================================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"

export QT_QPA_PLATFORM="${{QT_QPA_PLATFORM:-xcb}}"
export QT_MEDIA_BACKEND="${{QT_MEDIA_BACKEND:-ffmpeg}}"
export RESOLVE_SCRIPT_API="${{RESOLVE_SCRIPT_API:-/opt/resolve/Developer/Scripting}}"

if [ -z "$RESOLVE_SCRIPT_LIB" ]; then
    for cand in \\
        "/opt/resolve/libs/Fusion/fusionscript.so" \\
        "/opt/resolve/libs/Fusion/libfusionscript.so" \\
        "/opt/resolve/libs/fusionscript.so" \\
        "/opt/resolve/libs/libfusionscript.so"; do
        if [ -f "$cand" ]; then
            export RESOLVE_SCRIPT_LIB="$cand"
            break
        fi
    done
    export RESOLVE_SCRIPT_LIB="${{RESOLVE_SCRIPT_LIB:-/opt/resolve/libs/Fusion/fusionscript.so}}"
fi

if [ -d "/opt/resolve/libs" ]; then
    export LD_LIBRARY_PATH="/opt/resolve/libs:${{SCRIPT_DIR}}:${{LD_LIBRARY_PATH}}"
else
    export LD_LIBRARY_PATH="${{SCRIPT_DIR}}:${{LD_LIBRARY_PATH}}"
fi

exec "${{SCRIPT_DIR}}/{APP_NAME}" "$@"
"""
        with open(launcher_sh, "w", encoding="utf-8") as f:
            f.write(sh_content)
        try:
            os.chmod(launcher_sh, 0o755)
        except Exception:
            pass

        # Sinh file desktop entry
        desktop_file = os.path.join(dist_app_dir, "resolveflow-assistant.desktop")
        desktop_content = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=AI-Powered Rough Cut & Asset Studio for DaVinci Resolve
Exec=bash -c "cd $(dirname %k) && ./launch_resolveflow.sh"
Icon=video-x-generic
Terminal=false
Categories=AudioVideo;Video;AudioVideoEditing;
"""
        with open(desktop_file, "w", encoding="utf-8") as f:
            f.write(desktop_content)

    # 3. Sinh launcher cho Windows
    elif sys.platform.startswith("win"):
        bat_file = os.path.join(dist_app_dir, "Launch_ResolveFlow.bat")
        bat_content = f"""@echo off
title {APP_NAME} v{APP_VERSION}
cd /d "%~dp0"
start "" "%~dp0{APP_NAME}.exe" %*
"""
        with open(bat_file, "w", encoding="utf-8") as f:
            f.write(bat_content)

def run_dry_run():
    """Kiểm tra toàn bộ cấu hình, kiểm tra các tệp nguồn cần thiết và in thông số mà không build."""
    sep = get_data_separator()
    print("=" * 72)
    print(f"🚀 {APP_NAME} v{APP_VERSION} Standalone Build Configuration (Dry-Run)")
    print("=" * 72)
    print(f"• Hệ điều hành (OS Platform): {sys.platform} ({'Windows' if sys.platform.startswith('win') else 'Linux/POSIX'})")
    print(f"• Dấu phân cách add-data: '{sep}'")
    
    # Kiểm tra Entry Point
    entry_ok = os.path.isfile(ENTRY_POINT)
    print(f"• Điểm khởi chạy (Entry Point): {ENTRY_POINT} [{'OK' if entry_ok else 'THIẾU'}]")
    if not entry_ok:
        print(f"  ❌ Không tìm thấy tệp {ENTRY_POINT}!")
        return False

    # Kiểm tra Resource Directories
    print("• Thư mục tài nguyên (Resource Directories):")
    resources = get_resource_specs()
    all_res_ok = True
    for src, dst in resources:
        exists = os.path.exists(src)
        status = "OK" if exists else "CHƯA TỒN TẠI (SẼ TẠO)"
        print(f"  - {src} -> {dst} [{status}]")
        if not exists and src not in ["assets"]:
            all_res_ok = False

    # Kiểm tra Spec file
    spec_file = "resolveflow.spec"
    spec_ok = os.path.isfile(spec_file)
    print(f"• Tệp cấu hình PyInstaller Spec: {spec_file} [{'OK' if spec_ok else 'CHƯA CÓ'}]")

    print(f"• Thư mục đích phân phối: dist/{APP_NAME}/")
    print("• Cấu trúc thư mục khung sau build (Post-Build Skeleton):")
    print("  - assets/{sfx, bgm, luts, memes, overlays, titles, transitions}")
    print("  - data/")
    if sys.platform.startswith("linux"):
        print("  - launch_resolveflow.sh (Executable Shell Script)")
        print("  - resolveflow-assistant.desktop (XDG Desktop Entry)")
    else:
        print("  - Launch_ResolveFlow.bat (Windows Batch Launcher)")

    hidden_count = len(get_hidden_imports())
    exclude_count = len(get_excludes())
    print(f"• Tổng số Hidden Imports theo dõi: {hidden_count}")
    print(f"• Tổng số Module loại trừ: {exclude_count}")

    print("-" * 72)
    print("Lệnh PyInstaller thực thi thực tế khi build:")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", spec_file]
    print("   " + " ".join(cmd))
    print("=" * 72)
    print("🎉 [DRY-RUN THÀNH CÔNG] Tất cả cấu hình và đường dẫn tài nguyên hợp lệ 100%!")
    return True

def build():
    parser = argparse.ArgumentParser(description=f"Đóng gói {APP_NAME} v{APP_VERSION} Đa nền tảng")
    parser.add_argument("--dry-run", action="store_true", help="Kiểm tra cấu hình và tài nguyên mà không chạy build thật")
    parser.add_argument("--clean", action="store_true", default=True, help="Dọn dẹp cache PyInstaller trước khi build")
    args = parser.parse_args()

    if args.dry_run:
        success = run_dry_run()
        sys.exit(0 if success else 1)

    print("=" * 72)
    print(f"   Bắt đầu đóng gói {APP_NAME} v{APP_VERSION}...")
    print("=" * 72)

    try:
        import PyInstaller
        print(f"✔ Đã tìm thấy PyInstaller: {PyInstaller.__version__}")
    except ImportError:
        print("ℹ Đang cài đặt PyInstaller vào môi trường...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "resolveflow.spec"
    ]
    if args.clean:
        cmd.append("--clean")

    print("\n[BUILD] Đang thực thi lệnh build:")
    print("   " + " ".join(cmd))
    res = subprocess.run(cmd)

    if res.returncode == 0:
        dist_app_dir = os.path.join("dist", APP_NAME)
        print("\n[POST-BUILD] Đang hoàn thiện cấu trúc thư mục và launcher...")
        post_build_setup(dist_app_dir)
        print("\n🎉 [SUCCESS] Đóng gói thành công!")
        print(f"  -> Thư mục kết quả: {dist_app_dir}")
        if sys.platform.startswith("win"):
            print(f"  -> Tệp thực thi: {os.path.join(dist_app_dir, f'{APP_NAME}.exe')}")
            print(f"  -> Launcher: {os.path.join(dist_app_dir, 'Launch_ResolveFlow.bat')}")
        else:
            print(f"  -> Tệp thực thi: {os.path.join(dist_app_dir, APP_NAME)}")
            print(f"  -> Launcher: {os.path.join(dist_app_dir, 'launch_resolveflow.sh')}")
    else:
        print(f"\n❌ [ERROR] Lỗi trong quá trình build (Exit code: {res.returncode})")
        sys.exit(res.returncode)

if __name__ == "__main__":
    build()
