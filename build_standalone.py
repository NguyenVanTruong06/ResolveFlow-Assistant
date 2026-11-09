#!/usr/bin/env python3
# ==============================================================================
# ResolveFlow-Assistant - Multiplatform Standalone Application Builder
# ==============================================================================
import os
import sys
import shutil
import argparse
import subprocess
import importlib.util
from pathlib import Path
from typing import List, Tuple

# Ensure UTF-8 console output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

APP_NAME = "ResolveFlow-Assistant"
APP_VERSION = "1.0.21"
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ENTRY_POINT = os.path.join("src", "main.py")

def get_data_separator() -> str:
    """Return add-data separator: ';' on Windows, ':' on POSIX."""
    return ";" if sys.platform.startswith("win") else ":"

def get_resource_specs() -> List[Tuple[str, str]]:
    """Return data directories only if they exist on disk."""
    candidate_specs = [
        ("presets", "presets"),
        ("recipes", "recipes"),
        ("resources", "resources"),
        ("assets", "assets"),
        ("src/resolve_scripts", "src/resolve_scripts"),
    ]
    return [(src, dst) for src, dst in candidate_specs if os.path.exists(src)]

def get_hidden_imports() -> List[str]:
    """Return complete set of importable third-party and production modules."""
    return [
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtNetwork",
        "PySide6.QtSvg",
        "faster_whisper",
        "ctranslate2",
        "tokenizers",
        "onnxruntime",
        "soundfile",
        "numpy",
        "cv2",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "pydantic",
        "pydantic_core",
        "cryptography",
        "ffmpeg",
        "src.core",
        "src.core.action_guard",
        "src.core.ai_director",
        "src.core.asset_cache",
        "src.core.asset_indexer",
        "src.core.asset_manager",
        "src.core.audio",
        "src.core.audio_beat",
        "src.core.audio_normalizer",
        "src.core.audio_processor",
        "src.core.audit_reporter",
        "src.core.autocut",
        "src.core.batch_draft_contracts",
        "src.core.batch_evidence_adapter",
        "src.core.batch_planner",
        "src.core.branding",
        "src.core.broll_sfx",
        "src.core.cache_manager",
        "src.core.chronological_sorter",
        "src.core.clip_manifest",
        "src.core.cloud_downloader",
        "src.core.collections_manager",
        "src.core.connection",
        "src.core.connection.base",
        "src.core.connection.bridge",
        "src.core.connection.direct",
        "src.core.connection.manual",
        "src.core.contextual_suggester",
        "src.core.contracts",
        "src.core.contracts.asset_dto",
        "src.core.contracts.asset_matching_contracts",
        "src.core.contracts.dispatcher_admission_contracts",
        "src.core.contracts.edit_plan_approval_contracts",
        "src.core.contracts.edit_plan_contracts",
        "src.core.contracts.edit_plan_proposal_contracts",
        "src.core.contracts.execution_receipt_contracts",
        "src.core.contracts.oracle_ipc_contracts",
        "src.core.contracts.plan_demand_contracts",
        "src.core.contracts.recovery_assessment_contracts",
        "src.core.contracts.signed_approval_envelope",
        "src.core.contracts.signed_execution_workorder_contracts",
        "src.core.contracts.signed_operator_consent_envelope",
        "src.core.contracts.signed_preflight_contracts",
        "src.core.contracts.status_contracts",
        "src.core.contracts.time_contracts",
        "src.core.contracts.trial_authorization_contracts",
        "src.core.edit_policy",
        "src.core.evidence_grabber",
        "src.core.fast_paste",
        "src.core.fcpxml_generator",
        "src.core.ffmpeg_utils",
        "src.core.folder_scanner",
        "src.core.fusion",
        "src.core.fusion.common",
        "src.core.fusion.lua",
        "src.core.fusion.overlays",
        "src.core.fusion.titles",
        "src.core.fusion.transitions",
        "src.core.fusion.validate",
        "src.core.geometry",
        "src.core.geometry.frame_geometry_calculator",
        "src.core.geometry.frame_geometry_contracts",
        "src.core.geometry.local_metadata_acquisition",
        "src.core.geometry.plan_geometry_assembler",
        "src.core.geometry.plan_geometry_contracts",
        "src.core.geometry.source_evidence_service",
        "src.core.geometry.source_frame_evidence_contracts",
        "src.core.geometry.source_frame_evidence_parser",
        "src.core.geometry_checker",
        "src.core.kinetic",
        "src.core.kinetic.agent",
        "src.core.kinetic.kinetic_dataset_mapper",
        "src.core.kinetic.kinetic_engine",
        "src.core.llm_director",
        "src.core.lut_generator",
        "src.core.marketing_engine",
        "src.core.matching_service",
        "src.core.motion_analyzer",
        "src.core.offline_asset_matcher",
        "src.core.pack_ingest",
        "src.core.pack_manager",
        "src.core.plan_demand_adapter",
        "src.core.preset_capture",
        "src.core.project_overview_contracts",
        "src.core.proposal_builder",
        "src.core.proxy_manager",
        "src.core.queue",
        "src.core.recipe_manager",
        "src.core.resolve_api",
        "src.core.resolve_bridge",
        "src.core.review_state",
        "src.core.scene_activity",
        "src.core.sfx_generator",
        "src.core.shotbook_orchestrator",
        "src.core.smart_cutout",
        "src.core.source_catalog_readiness",
        "src.core.source_resolver",
        "src.core.starter_pack",
        "src.core.starter_pack_downloader",
        "src.core.storage_inventory",
        "src.core.storage_manager",
        "src.core.story_arranger",
        "src.core.story_copilot",
        "src.core.story_pipeline",
        "src.core.story_planner",
        "src.core.text_preset",
        "src.core.thumbnail_generator",
        "src.core.timeline_mapper",
        "src.core.timeline_plan_execution_binder",
        "src.core.title_insert_worker",
        "src.core.transcriber",
        "src.core.transcript_quality",
        "src.core.transition_preset",
        "src.core.validator",
        "src.core.vision_reframer",
        "src.core.visual_describer",
        "src.core.visual_index",
        "src.core.visual_sampling",
        "src.core.vlog_hook",
        "src.core.whisper_cleaner",
        "src.core.xml_exporter",
        "src.main",
        "src.resolve_scripts.ChunDVC_Bridge",
        "src.services",
        "src.services.audio_router",
        "src.services.authoritative_approval_store",
        "src.services.autocut_service",
        "src.services.autocut_worker",
        "src.services.director_approval_signer",
        "src.services.dispatcher_admission_coordinator_core",
        "src.services.dispatcher_admission_verifier_core",
        "src.services.durable_admission_ledger",
        "src.services.durable_dispatch_journal",
        "src.services.durable_nonce_ledger",
        "src.services.durable_trial_target_ledger",
        "src.services.execution_journal_service",
        "src.services.execution_receipt_service",
        "src.services.global_hotkey",
        "src.services.insert_service",
        "src.services.isolated_resolve_trial_adapter",
        "src.services.live_canary_dispatch_service",
        "src.services.live_resolve_canary_adapter",
        "src.services.multi_screen_service",
        "src.services.recovery_classifier_service",
        "src.services.recovery_coordinator",
        "src.services.resolve_execution_engine",
        "src.services.resolve_gateway",
        "src.services.resolve_state",
        "src.services.search_index",
        "src.services.signer_keystore_service",
        "src.services.source_shutdown",
        "src.services.trial_authorization_signer_daemon",
        "src.services.trial_authorization_signer_service",
        "src.services.trial_keyring",
        "src.services.trusted_canary_dispatch_service",
        "src.services.update_service",
        "src.tools.gate4_preflight_runner",
        "src.tools.reconcile_orphan_ledger",
        "src.tools.resolveflow_operator_console",
        "src.ui",
        "src.ui.auto.auto_window",
        "src.ui.bubble.floating_bubble",
        "src.ui.director",
        "src.ui.director.director_controller",
        "src.ui.director.director_state",
        "src.ui.director.director_window",
        "src.ui.director.pages",
        "src.ui.director.pages.build_page",
        "src.ui.director.pages.review_page",
        "src.ui.director.pages.source_page",
        "src.ui.director.pages.style_page",
        "src.ui.director.widgets",
        "src.ui.director.widgets.advanced_panel",
        "src.ui.director.widgets.asset_advisory_drawer",
        "src.ui.director.widgets.common_proposal_review_widget",
        "src.ui.director.widgets.mini_timeline_view",
        "src.ui.director.widgets.progress_panel",
        "src.ui.director.widgets.recovery_assessment_dialog",
        "src.ui.director.widgets.stepper",
        "src.ui.director.workers",
        "src.ui.director.workers.asset_matching_worker",
        "src.ui.director.workers.kinetic_preparation_worker",
        "src.ui.director.workers.preapproval_evidence_worker",
        "src.ui.director.workers.preflight_worker",
        "src.ui.director.workers.prompt_preparation_worker",
        "src.ui.director.workers.recovery_assessment_worker",
        "src.ui.director.workers.review_analysis_worker",
        "src.ui.director.workers.source_analysis_worker",
        "src.ui.icons",
        "src.ui.lux",
        "src.ui.lux.context",
        "src.ui.lux.context.context_profiles",
        "src.ui.lux.context.context_router",
        "src.ui.lux.context.special_blocks",
        "src.ui.lux.drag_drop_tracker",
        "src.ui.lux.feedback",
        "src.ui.lux.feedback.toast",
        "src.ui.lux.inspector",
        "src.ui.lux.inspector.editors",
        "src.ui.lux.inspector.inspector_host",
        "src.ui.lux.library",
        "src.ui.lux.library.action_bar",
        "src.ui.lux.library.asset_delegate",
        "src.ui.lux.library.asset_filter",
        "src.ui.lux.library.asset_grid",
        "src.ui.lux.library.asset_model",
        "src.ui.lux.library.category_nav",
        "src.ui.lux.library.hover_preview",
        "src.ui.lux.library.lut_preview_generator",
        "src.ui.lux.library.text_preview_generator",
        "src.ui.lux.library.transition_preview_generator",
        "src.ui.lux.lux_effects",
        "src.ui.lux.macro_card",
        "src.ui.lux.palette",
        "src.ui.lux.palette.command_palette",
        "src.ui.lux.palette.command_registry",
        "src.ui.lux.palette.palette_model",
        "src.ui.lux.shell",
        "src.ui.lux.shell.dock_header",
        "src.ui.lux.shell.dock_rail",
        "src.ui.lux.shell.dock_settings_dialog",
        "src.ui.lux.shell.dock_state",
        "src.ui.lux.shell.dock_status",
        "src.ui.lux.shell.edge_dock",
        "src.ui.lux.shell.edge_peek_handle",
        "src.ui.lux.shell.tray",
        "src.ui.lux.window_anchor",
        "src.ui.studio.studio_window",
        "src.ui.tabs",
        "src.ui.tabs.cloud_pack_worker",
        "src.ui.tabs.tab_asset_hub",
        "src.ui.tabs.tab_assets",
        "src.ui.theme",
        "src.ui.widgets.copilot_dialog",
        "src.ui.widgets.drive_dialog",
        "src.ui.widgets.mini_timeline",
        "src.ui.widgets.pack_ingest_wizard",
        "src.ui.widgets.section_card",
        "src.ui.widgets.step_progress_widget",
        "src.ui.widgets.update_toast",
        "src.utils",
        "src.utils.path_utils",
        "src.utils.subprocess_utils",
        "src.utils.windows_shortcuts",
    ]

def get_excludes() -> List[str]:
    """Exclude dev and testing packages from standalone bundle."""
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

def get_pyinstaller_args() -> List[str]:
    """Return PyInstaller CLI arguments guarding data directories."""
    sep = get_data_separator()
    args = [
        "--name", APP_NAME,
        "--noconfirm",
        "--clean",
        "--windowed",
    ]
    ico = os.path.join("assets", "branding", "chundvc.ico")
    if os.path.exists(ico):
        args.extend(["--icon", ico])
    for src, dst in get_resource_specs():
        if os.path.exists(src):
            args.extend(["--add-data", f"{src}{sep}{dst}"])
    for imp in get_hidden_imports():
        args.extend(["--hidden-import", imp])
    for exc in get_excludes():
        args.extend(["--exclude-module", exc])
    return args

def generate_spec_file(spec_path: str = "resolveflow.spec") -> str:
    """Generate resolveflow.spec synchronized with build configuration."""
    candidate_data = [
        ("presets", "presets"),
        ("recipes", "recipes"),
        ("resources", "resources"),
        ("assets", "assets"),
        ("src/resolve_scripts", "src/resolve_scripts"),
    ]
    hidden_imports_list = get_hidden_imports()
    excludes_list = get_excludes()

    template = """# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas_fw, binaries_fw, hidden_fw = collect_all('faster_whisper')
datas_ct2, binaries_ct2, hidden_ct2 = collect_all('ctranslate2')
datas_ort, binaries_ort, hidden_ort = collect_all('onnxruntime')

candidate_data_dirs = {candidate_data_dirs}
added_files = [
    (src, dst) for src, dst in candidate_data_dirs if os.path.exists(src)
] + datas_fw + datas_ct2 + datas_ort

all_binaries = binaries_fw + binaries_ct2 + binaries_ort

hidden_imports = {hidden_imports} + hidden_fw + hidden_ct2 + hidden_ort

a = Analysis(
    [os.path.join('src', 'main.py')],
    pathex=[],
    binaries=all_binaries,
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={excludes},
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='{app_name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join('assets', 'branding', 'chundvc.ico') if os.path.exists(os.path.join('assets', 'branding', 'chundvc.ico')) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='{app_name}',
)
"""
    spec_content = template.format(
        candidate_data_dirs=repr(candidate_data),
        hidden_imports=repr(hidden_imports_list),
        excludes=repr(excludes_list),
        app_name=APP_NAME,
    )
    if spec_path:
        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(spec_content)
    return spec_content

def verify_hidden_imports() -> bool:
    """Verify that every src.* module in get_hidden_imports() exists in repository."""
    hidden = get_hidden_imports()
    src_mods = [m for m in hidden if m.startswith("src.")]
    missing = []
    for mod in src_mods:
        spec = importlib.util.find_spec(mod)
        if spec is None:
            missing.append(mod)

    print("=" * 72)
    print(f"🔍 [VERIFY-IMPORTS] Checking {len(src_mods)} internal 'src.*' modules:")
    print("=" * 72)
    if missing:
        print(f"❌ Found {len(missing)} missing modules:")
        for m in missing:
            print(f"   - {m}")
        return False

    print(f"✔ 100% ({len(src_mods)}/{len(src_mods)}) 'src.*' modules verified in repository!")
    return True

def post_build_setup(dist_app_dir: str):
    """Create assets/data directory skeleton and launchers."""
    os.makedirs(dist_app_dir, exist_ok=True)

    # 1. Assets skeleton
    sub_assets = ["sfx", "bgm", "luts", "memes", "overlays", "titles", "transitions"]
    for sub in sub_assets:
        p = os.path.join(dist_app_dir, "assets", sub)
        os.makedirs(p, exist_ok=True)
    os.makedirs(os.path.join(dist_app_dir, "data"), exist_ok=True)

    # Copy preset and recipe directories
    for src_dir in ["presets", "recipes"]:
        dst_dir = os.path.join(dist_app_dir, src_dir)
        if os.path.isdir(src_dir) and not os.path.exists(dst_dir):
            shutil.copytree(src_dir, dst_dir)

    # 2. Linux launcher
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

        # Sao chép icon vào dist
        icon_src = os.path.join(PROJECT_ROOT, "assets", "branding", "chundvc_512.png")
        if os.path.exists(icon_src):
            shutil.copy2(icon_src, os.path.join(dist_app_dir, "chundvc.png"))
            shutil.copy2(icon_src, os.path.join(dist_app_dir, "icon.png"))

        desktop_file = os.path.join(dist_app_dir, "chundvc.desktop")
        desktop_content = """[Desktop Entry]
Type=Application
Name=ChunDVC
GenericName=Trợ lý Dựng Phim DaVinci Resolve AI
Comment=ChunDVC - Trợ lý Dựng Phim AI & Kho Đạo Cụ cho DaVinci Resolve
Exec=bash -c "cd \\$(dirname %k) && ./launch_resolveflow.sh"
Icon=chundvc
Terminal=false
Categories=AudioVideo;Video;AudioVideoEditing;
StartupWMClass=ChunDVC
"""
        with open(desktop_file, "w", encoding="utf-8") as f:
            f.write(desktop_content)
        # Bản sao resolveflow-assistant.desktop tương thích ngược
        with open(os.path.join(dist_app_dir, "resolveflow-assistant.desktop"), "w", encoding="utf-8") as f:
            f.write(desktop_content)

    # 3. Windows launcher
    elif sys.platform.startswith("win"):
        bat_file = os.path.join(dist_app_dir, "Launch_ChunDVC.bat")
        bat_content = f"""@echo off
title ChunDVC v{APP_VERSION}
cd /d "%~dp0"
start "" "%~dp0{APP_NAME}.exe" %*
"""
        with open(bat_file, "w", encoding="utf-8") as f:
            f.write(bat_content)
        with open(os.path.join(dist_app_dir, "Launch_ResolveFlow.bat"), "w", encoding="utf-8") as f:
            f.write(bat_content)

        # Icon cho shortcut Start Menu / Desktop
        ico_src = os.path.join(PROJECT_ROOT, "assets", "branding", "chundvc.ico")
        if os.path.exists(ico_src):
            shutil.copy2(ico_src, os.path.join(dist_app_dir, "chundvc.ico"))

        # Trình cài đặt 1-click: copy vào %LOCALAPPDATA%\\Programs\\ChunDVC + tạo shortcut Start Menu/Desktop
        installer_bat = os.path.join(dist_app_dir, "Install_ChunDVC_Windows.bat")
        with open(installer_bat, "w", encoding="utf-8") as f:
            f.write(WINDOWS_INSTALLER_BAT)


WINDOWS_INSTALLER_BAT = r"""@echo off
chcp 65001 >nul
setlocal
set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\Programs\ChunDVC"
echo [ChunDVC] Dang cai dat vao "%DEST%" ...
taskkill /F /IM ResolveFlow-Assistant.exe >nul 2>&1
robocopy "%SRC%." "%DEST%" /E /NFL /NDL /NJH /NJS /NP /XF Install_ChunDVC_Windows.bat >nul
if %ERRORLEVEL% GEQ 8 (
  echo [ChunDVC] Loi sao chep tep. & pause & exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws=New-Object -ComObject WScript.Shell;" ^
  "foreach($d in @([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('Desktop'))){" ^
  "$s=$ws.CreateShortcut((Join-Path $d 'ChunDVC.lnk'));" ^
  "$s.TargetPath=Join-Path $env:LOCALAPPDATA 'Programs\ChunDVC\ResolveFlow-Assistant.exe';" ^
  "$s.WorkingDirectory=Join-Path $env:LOCALAPPDATA 'Programs\ChunDVC';" ^
  "$s.IconLocation=(Join-Path $env:LOCALAPPDATA 'Programs\ChunDVC\chundvc.ico')+',0';" ^
  "$s.Description='ChunDVC';$s.Save()}"
echo [ChunDVC] Hoan tat! Mo ChunDVC tu Start Menu hoac Desktop.
start "" "%DEST%\ResolveFlow-Assistant.exe"
endlocal
"""


def install_windows_shortcuts(app_dir: str = None, programs_dir: str = None,
                              desktop_dir: str = None, desktop: bool = True,
                              dry_run: bool = False) -> dict:
    """Đăng ký ChunDVC vào Windows Start Menu (Apps) và Desktop."""
    from src.utils.windows_shortcuts import install_shortcuts
    if app_dir is None:
        dist_dir = os.path.join(PROJECT_ROOT, "dist", APP_NAME)
        app_dir = dist_dir if os.path.isdir(dist_dir) else PROJECT_ROOT
    return install_shortcuts(app_dir, programs_dir=programs_dir, desktop_dir=desktop_dir,
                             desktop=desktop, dry_run=dry_run)

def run_dry_run() -> bool:
    """Perform dry-run checks and validation without building."""
    sep = get_data_separator()
    print("=" * 72)
    print(f"🚀 {APP_NAME} v{APP_VERSION} Standalone Build Configuration (Dry-Run)")
    print("=" * 72)
    print(f"• Platform: {sys.platform} ('Windows' if sys.platform.startswith('win') else 'Linux/POSIX')")
    print(f"• Add-data separator: '{sep}'")

    entry_ok = os.path.isfile(ENTRY_POINT)
    print(f"• Entry Point: {ENTRY_POINT} ['OK' if entry_ok else 'MISSING']")
    if not entry_ok:
        print(f"  ❌ Missing entry point {ENTRY_POINT}!")
        return False

    print("• Resource Directories:")
    resources = get_resource_specs()
    all_res_ok = True
    for src, dst in resources:
        exists = os.path.exists(src)
        status = "OK" if exists else "NOT FOUND"
        print(f"  - {src} -> {dst} [{status}]")
        if not exists and src not in ["assets"]:
            all_res_ok = False

    spec_file = "resolveflow.spec"
    spec_ok = os.path.isfile(spec_file)
    print(f"• Spec file: {spec_file} ['OK' if spec_ok else 'NOT FOUND']")
    print(f"• Distribution target: dist/{APP_NAME}/")

    hidden_count = len(get_hidden_imports())
    exclude_count = len(get_excludes())
    print(f"• Hidden imports count: {hidden_count}")
    print(f"• Excluded modules count: {exclude_count}")

    imports_ok = verify_hidden_imports()
    if not imports_ok:
        return False

    print("-" * 72)
    print("PyInstaller command line preview:")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", spec_file]
    print("   " + " ".join(cmd))
    print("=" * 72)
    print("🎉 [DRY-RUN SUCCESS] All configurations and resources are valid 100%!")
    return True

def build():
    parser = argparse.ArgumentParser(description=f"Package {APP_NAME} v{APP_VERSION} Standalone")
    parser.add_argument("--dry-run", action="store_true", help="Check config and resources without building")
    parser.add_argument("--verify-imports", action="store_true", help="Verify 100% of src.* hidden imports resolve")
    parser.add_argument("--clean", action="store_true", default=True, help="Clean PyInstaller cache before building")
    parser.add_argument("--gen-spec", action="store_true", help="Regenerate synchronized resolveflow.spec")
    args = parser.parse_args()

    if args.verify_imports:
        success = verify_hidden_imports()
        sys.exit(0 if success else 1)

    if args.gen_spec:
        generate_spec_file("resolveflow.spec")
        print("✔ Synchronized resolveflow.spec generated successfully.")
        sys.exit(0)

    if args.dry_run:
        success = run_dry_run()
        sys.exit(0 if success else 1)

    print("=" * 72)
    print(f"   Building {APP_NAME} v{APP_VERSION}...")
    print("=" * 72)

    try:
        import PyInstaller
        print(f"✔ Found PyInstaller: {PyInstaller.__version__}")
    except ImportError:
        print("ℹ Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "resolveflow.spec"]
    if args.clean:
        cmd.append("--clean")

    print("\n[BUILD] Running command:")
    print("   " + " ".join(cmd))
    res = subprocess.run(cmd)

    if res.returncode == 0:
        dist_app_dir = os.path.join("dist", APP_NAME)
        print("\n[POST-BUILD] Finalizing directory structure and launchers...")
        post_build_setup(dist_app_dir)
        print("\n🎉 [SUCCESS] Build succeeded!")
        print(f"  -> Output directory: {dist_app_dir}")
    else:
        print(f"\n❌ [ERROR] Build failed (Exit code: {res.returncode})")
        sys.exit(res.returncode)

if __name__ == "__main__":
    build()
