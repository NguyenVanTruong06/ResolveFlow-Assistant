# -*- mode: python ; coding: utf-8 -*-
import os
import sys

block_cipher = None

added_files = [
    ('presets', 'presets'),
    ('recipes', 'recipes'),
    ('assets', 'assets'),
]

hidden_imports = [
    'faster_whisper',
    'ctranslate2',
    'pydantic',
    'pydantic_core',
    'ffmpeg',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'PIL.ImageFont',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'src.core.ai_director',
    'src.core.audio',
    'src.core.audit_reporter',
    'src.core.autocut',
    'src.core.broll_sfx',
    'src.core.cache_manager',
    'src.core.fcpxml_generator',
    'src.core.proxy_manager',
    'src.core.queue',
    'src.core.recipe_manager',
    'src.core.resolve_api',
    'src.core.text_preset',
    'src.core.transcriber',
    'src.core.validator',
    'src.core.vision_reframer',
    'src.core.vlog_hook',
    'src.ui.app'
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
