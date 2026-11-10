# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas_fw, binaries_fw, hidden_fw = collect_all('faster_whisper')
datas_ct2, binaries_ct2, hidden_ct2 = collect_all('ctranslate2')
datas_ort, binaries_ort, hidden_ort = collect_all('onnxruntime')

candidate_data_dirs = [('presets', 'presets'), ('recipes', 'recipes'), ('resources', 'resources'), ('assets', 'assets'), ('src/resolve_scripts', 'src/resolve_scripts')]
added_files = [
    (src, dst) for src, dst in candidate_data_dirs if os.path.exists(src)
] + datas_fw + datas_ct2 + datas_ort

all_binaries = binaries_fw + binaries_ct2 + binaries_ort

hidden_imports = ['PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtMultimedia', 'PySide6.QtNetwork', 'PySide6.QtSvg', 'faster_whisper', 'ctranslate2', 'tokenizers', 'onnxruntime', 'soundfile', 'numpy', 'cv2', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont', 'pydantic', 'pydantic_core', 'cryptography', 'ffmpeg', 'src.core', 'src.core.action_guard', 'src.core.ai_director', 'src.core.asset_cache', 'src.core.asset_indexer', 'src.core.asset_manager', 'src.core.audio', 'src.core.audio_beat', 'src.core.audio_normalizer', 'src.core.audio_processor', 'src.core.audit_reporter', 'src.core.autocut', 'src.core.batch_draft_contracts', 'src.core.batch_evidence_adapter', 'src.core.batch_planner', 'src.core.branding', 'src.core.broll_sfx', 'src.core.cache_manager', 'src.core.chronological_sorter', 'src.core.clip_manifest', 'src.core.cloud_downloader', 'src.core.collections_manager', 'src.core.connection', 'src.core.connection.base', 'src.core.connection.bridge', 'src.core.connection.direct', 'src.core.connection.manual', 'src.core.contextual_suggester', 'src.core.contracts', 'src.core.contracts.asset_dto', 'src.core.contracts.asset_matching_contracts', 'src.core.contracts.dispatcher_admission_contracts', 'src.core.contracts.edit_plan_approval_contracts', 'src.core.contracts.edit_plan_contracts', 'src.core.contracts.edit_plan_proposal_contracts', 'src.core.contracts.execution_receipt_contracts', 'src.core.contracts.oracle_ipc_contracts', 'src.core.contracts.plan_demand_contracts', 'src.core.contracts.recovery_assessment_contracts', 'src.core.contracts.signed_approval_envelope', 'src.core.contracts.signed_execution_workorder_contracts', 'src.core.contracts.signed_operator_consent_envelope', 'src.core.contracts.signed_preflight_contracts', 'src.core.contracts.status_contracts', 'src.core.contracts.time_contracts', 'src.core.contracts.trial_authorization_contracts', 'src.core.edit_policy', 'src.core.evidence_grabber', 'src.core.fast_paste', 'src.core.fcpxml_generator', 'src.core.ffmpeg_utils', 'src.core.folder_scanner', 'src.core.fusion', 'src.core.fusion.common', 'src.core.fusion.lua', 'src.core.fusion.overlays', 'src.core.fusion.titles', 'src.core.fusion.transitions', 'src.core.fusion.validate', 'src.core.geometry', 'src.core.geometry.frame_geometry_calculator', 'src.core.geometry.frame_geometry_contracts', 'src.core.geometry.local_metadata_acquisition', 'src.core.geometry.plan_geometry_assembler', 'src.core.geometry.plan_geometry_contracts', 'src.core.geometry.source_evidence_service', 'src.core.geometry.source_frame_evidence_contracts', 'src.core.geometry.source_frame_evidence_parser', 'src.core.geometry_checker', 'src.core.kinetic', 'src.core.kinetic.agent', 'src.core.kinetic.kinetic_dataset_mapper', 'src.core.kinetic.kinetic_engine', 'src.core.llm_director', 'src.core.lut_generator', 'src.core.marketing_engine', 'src.core.matching_service', 'src.core.motion_analyzer', 'src.core.offline_asset_matcher', 'src.core.pack_ingest', 'src.core.pack_manager', 'src.core.plan_demand_adapter', 'src.core.preset_capture', 'src.core.project_overview_contracts', 'src.core.proposal_builder', 'src.core.proxy_manager', 'src.core.queue', 'src.core.recipe_manager', 'src.core.resolve_api', 'src.core.resolve_bridge', 'src.core.review_state', 'src.core.scene_activity', 'src.core.sfx_generator', 'src.core.shotbook_orchestrator', 'src.core.smart_cutout', 'src.core.source_catalog_readiness', 'src.core.source_resolver', 'src.core.starter_pack', 'src.core.starter_pack_downloader', 'src.core.storage_inventory', 'src.core.storage_manager', 'src.core.story_arranger', 'src.core.story_copilot', 'src.core.story_pipeline', 'src.core.story_planner', 'src.core.text_preset', 'src.core.thumbnail_generator', 'src.core.timeline_mapper', 'src.core.timeline_plan_execution_binder', 'src.core.title_insert_worker', 'src.core.transcriber', 'src.core.transcript_quality', 'src.core.transition_preset', 'src.core.validator', 'src.core.vision_reframer', 'src.core.visual_describer', 'src.core.visual_index', 'src.core.visual_sampling', 'src.core.vlog_hook', 'src.core.whisper_cleaner', 'src.core.xml_exporter', 'src.main', 'src.resolve_scripts.ChunDVC_Bridge', 'src.services', 'src.services.audio_router', 'src.services.authoritative_approval_store', 'src.services.autocut_service', 'src.services.autocut_worker', 'src.services.director_approval_signer', 'src.services.dispatcher_admission_coordinator_core', 'src.services.dispatcher_admission_verifier_core', 'src.services.durable_admission_ledger', 'src.services.durable_dispatch_journal', 'src.services.durable_nonce_ledger', 'src.services.durable_trial_target_ledger', 'src.services.execution_journal_service', 'src.services.execution_receipt_service', 'src.services.global_hotkey', 'src.services.insert_service', 'src.services.isolated_resolve_trial_adapter', 'src.services.live_canary_dispatch_service', 'src.services.live_resolve_canary_adapter', 'src.services.local_ai_setup_service', 'src.services.multi_screen_service', 'src.services.recovery_classifier_service', 'src.services.recovery_coordinator', 'src.services.resolve_execution_engine', 'src.services.resolve_gateway', 'src.services.resolve_state', 'src.services.search_index', 'src.services.signer_keystore_service', 'src.services.source_shutdown', 'src.services.trial_authorization_signer_daemon', 'src.services.trial_authorization_signer_service', 'src.services.trial_keyring', 'src.services.trusted_canary_dispatch_service', 'src.services.update_service', 'src.tools.gate4_preflight_runner', 'src.tools.reconcile_orphan_ledger', 'src.tools.resolveflow_operator_console', 'src.ui', 'src.ui.auto.auto_window', 'src.ui.bubble.floating_bubble', 'src.ui.director', 'src.ui.director.director_controller', 'src.ui.director.director_state', 'src.ui.director.director_window', 'src.ui.director.pages', 'src.ui.director.pages.build_page', 'src.ui.director.pages.review_page', 'src.ui.director.pages.source_page', 'src.ui.director.pages.style_page', 'src.ui.director.widgets', 'src.ui.director.widgets.advanced_panel', 'src.ui.director.widgets.ai_setup_dialog', 'src.ui.director.widgets.asset_advisory_drawer', 'src.ui.director.widgets.common_proposal_review_widget', 'src.ui.director.widgets.mini_timeline_view', 'src.ui.director.widgets.progress_panel', 'src.ui.director.widgets.recovery_assessment_dialog', 'src.ui.director.widgets.stepper', 'src.ui.director.workers', 'src.ui.director.workers.asset_matching_worker', 'src.ui.director.workers.kinetic_preparation_worker', 'src.ui.director.workers.preapproval_evidence_worker', 'src.ui.director.workers.preflight_worker', 'src.ui.director.workers.prompt_preparation_worker', 'src.ui.director.workers.recovery_assessment_worker', 'src.ui.director.workers.review_analysis_worker', 'src.ui.director.workers.source_analysis_worker', 'src.ui.icons', 'src.ui.lux', 'src.ui.lux.context', 'src.ui.lux.context.context_profiles', 'src.ui.lux.context.context_router', 'src.ui.lux.context.special_blocks', 'src.ui.lux.drag_drop_tracker', 'src.ui.lux.feedback', 'src.ui.lux.feedback.toast', 'src.ui.lux.inspector', 'src.ui.lux.inspector.editors', 'src.ui.lux.inspector.inspector_host', 'src.ui.lux.library', 'src.ui.lux.library.action_bar', 'src.ui.lux.library.asset_delegate', 'src.ui.lux.library.asset_filter', 'src.ui.lux.library.asset_grid', 'src.ui.lux.library.asset_model', 'src.ui.lux.library.category_nav', 'src.ui.lux.library.hover_preview', 'src.ui.lux.library.lut_preview_generator', 'src.ui.lux.library.text_preview_generator', 'src.ui.lux.library.transition_preview_generator', 'src.ui.lux.lux_effects', 'src.ui.lux.macro_card', 'src.ui.lux.palette', 'src.ui.lux.palette.command_palette', 'src.ui.lux.palette.command_registry', 'src.ui.lux.palette.palette_model', 'src.ui.lux.shell', 'src.ui.lux.shell.dock_header', 'src.ui.lux.shell.dock_rail', 'src.ui.lux.shell.dock_settings_dialog', 'src.ui.lux.shell.dock_state', 'src.ui.lux.shell.dock_status', 'src.ui.lux.shell.edge_dock', 'src.ui.lux.shell.edge_peek_handle', 'src.ui.lux.shell.tray', 'src.ui.lux.window_anchor', 'src.ui.studio.studio_window', 'src.ui.tabs', 'src.ui.tabs.cloud_pack_worker', 'src.ui.tabs.tab_asset_hub', 'src.ui.tabs.tab_assets', 'src.ui.theme', 'src.ui.widgets.app_header_actions', 'src.ui.widgets.copilot_dialog', 'src.ui.widgets.drive_dialog', 'src.ui.widgets.mini_timeline', 'src.ui.widgets.pack_ingest_wizard', 'src.ui.widgets.section_card', 'src.ui.widgets.step_progress_widget', 'src.ui.widgets.update_toast', 'src.utils', 'src.utils.path_utils', 'src.utils.subprocess_utils', 'src.utils.windows_shortcuts'] + hidden_fw + hidden_ct2 + hidden_ort

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
    name='ResolveFlow-Assistant',
)
