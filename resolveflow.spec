# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Thu thập đầy đủ dữ liệu, nhị phân và hidden imports cho faster_whisper, ctranslate2, onnxruntime
# Đặc biệt bao gồm silero_vad_v6.onnx và các tài nguyên VAD nằm trong faster_whisper/assets/
datas_fw, binaries_fw, hidden_fw = collect_all('faster_whisper')
datas_ct2, binaries_ct2, hidden_ct2 = collect_all('ctranslate2')
datas_ort, binaries_ort, hidden_ort = collect_all('onnxruntime')

added_files = [
    ('presets', 'presets'),
    ('recipes', 'recipes'),
    ('src/resolve_scripts', 'src/resolve_scripts'),
    ('assets', 'assets'),
] + datas_fw + datas_ct2 + datas_ort

all_binaries = binaries_fw + binaries_ct2 + binaries_ort

hidden_imports = [
    'faster_whisper',
    'ctranslate2',
    'tokenizers',
    'onnxruntime',
    'pydantic',
    'pydantic_core',
    'ffmpeg',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'PIL.ImageFont',
    'numpy',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtMultimedia',
    'PySide6.QtSvg',
    'src.core.ai_director',
    'src.core.audio',
    'src.core.audio_beat',
    'src.core.audio_normalizer',
    'src.core.audio_processor',
    'src.core.audit_reporter',
    'src.core.autocut',
    'src.core.broll_sfx',
    'src.core.cache_manager',
    'src.core.cloud_downloader',
    'src.core.fcpxml_generator',
    'src.core.ffmpeg_utils',
    'src.core.folder_scanner',
    'src.core.geometry_checker',
    'src.core.proxy_manager',
    'src.core.queue',
    'src.core.recipe_manager',
    'src.core.resolve_api',
    'src.core.resolve_bridge',
    'src.core.story_arranger',
    'src.core.story_copilot',
    'src.core.story_planner',
    'src.core.text_preset',
    'src.core.transcriber',
    'src.core.validator',
    'src.core.vision_reframer',
    'src.core.vlog_hook',
    'src.core.connection.base',
    'src.core.connection.direct',
    'src.ui.app',
    'src.ui.auto.auto_window',
    'src.ui.studio.studio_window',
    'src.ui.bubble.floating_bubble',
    'src.ui.components.mini_timeline',
    'src.ui.theme',
    'src.utils.path_utils',
] + hidden_fw + hidden_ct2 + hidden_ort

a = Analysis(
    [os.path.join('src', 'main.py')],
    pathex=[],
    binaries=all_binaries,
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', '_pytest', 'unittest', 'test', 'tests', 'tkinter', 'IPython', 'jupyter', 'matplotlib'],
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
    name='ResolveFlow-Assistant',
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ResolveFlow-Assistant',
)
