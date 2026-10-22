"""
tests/ui/test_asset_library_model_view.py
Comprehensive unit tests for BƯỚC 2: Model/View Asset Library Architecture.

Verifies:
- AssetListModel roles, data access, and async thumbnail updates
- AssetFilterProxyModel category, subcategory, favorite, and SearchIndex ranking
- AssetCardDelegate rendering 16:9 cards and audio waveforms
- AssetGridView responsive column calculations, drag & drop mime generation
- TabAssets virtualization integration (no massive AssetCard widget creation)
"""
import os
import pytest
from PySide6.QtCore import Qt, QSize, QModelIndex, QRect
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem
from PySide6.QtGui import QPixmap, QImage, QPainter

from src.ui.lux.library.asset_model import (
    AssetListModel, IdRole, NameRole, KindRole, DurationRole,
    ThumbnailRole, IsFavRole, CategoryRole, SubCategoryRole, AssetObjRole
)
from src.ui.lux.library.asset_filter import AssetFilterProxyModel
from src.ui.lux.library.asset_delegate import AssetCardDelegate
from src.ui.lux.library.asset_grid import AssetGridView, create_asset_mime_data
from src.services.search_index import SearchIndex





@pytest.fixture
def sample_raw_assets():
    return [
        {
            "id": "t_hormozi",
            "name": "Alex Hormozi Pop Title",
            "category": "text",
            "sub": "Kinetic",
            "duration": 4.0,
            "motion": "bounce",
            "tags": ["pop", "bounce", "chu", "noi bat"]
        },
        {
            "id": "tr_shake",
            "name": "Camera Shake Transition",
            "category": "trans",
            "sub": "Glitch",
            "duration": 1.2,
            "motion": "shake",
            "tags": ["shake", "rung giat", "chuyen canh"]
        },
        {
            "id": "s_whoosh",
            "name": "Fast Whoosh SFX",
            "category": "sfx",
            "sub": "Whoosh",
            "duration": 0.8,
            "motion": "",
            "tags": ["whoosh", "vut", "am thanh"]
        },
        {
            "id": "b_lofi",
            "name": "Chill Lofi Beat",
            "category": "bgm",
            "sub": "Chill Vlog",
            "duration": 65.0,
            "motion": "",
            "tags": ["lofi", "chill", "nhac nen"]
        },
        {
            "id": "l_teal",
            "name": "Teal & Orange Cinematic",
            "category": "lut",
            "sub": "Điện ảnh",
            "duration": 0.0,
            "motion": "",
            "tags": ["teal", "orange", "dien anh", "mau"]
        }
    ]


def test_asset_model_roles_and_data(qapp, sample_raw_assets):
    model = AssetListModel(sample_raw_assets, favs={"t_hormozi"})
    assert model.rowCount() == len(sample_raw_assets)

    idx_0 = model.index(0, 0)
    assert model.data(idx_0, IdRole) == "t_hormozi"
    assert model.data(idx_0, NameRole) == "Alex Hormozi Pop Title"
    assert model.data(idx_0, KindRole) == "Chữ"
    assert model.data(idx_0, DurationRole) == 4.0
    assert model.data(idx_0, CategoryRole) == "text"
    assert model.data(idx_0, IsFavRole) is True

    # Test toggling favorite
    is_now_fav = model.toggle_favorite("t_hormozi")
    assert is_now_fav is False
    assert model.data(idx_0, IsFavRole) is False

    is_now_fav = model.toggle_favorite("t_hormozi")
    assert is_now_fav is True
    assert model.data(idx_0, IsFavRole) is True


def test_asset_model_async_thumbnail_update(qapp, sample_raw_assets):
    model = AssetListModel(sample_raw_assets)
    idx_0 = model.index(0, 0)  # Text preset: có sẵn pixmap phong cách
    assert model.data(idx_0, ThumbnailRole) is not None

    idx_1 = model.index(1, 0)  # Transition: chưa có thumbnail, trả về None
    assert model.data(idx_1, ThumbnailRole) is None

    # Create dummy pixmap and deliver it via update_thumbnail for tr_shake
    dummy_pix = QPixmap(100, 100)
    dummy_pix.fill(Qt.GlobalColor.magenta)

    data_changed_emitted = []
    model.dataChanged.connect(lambda top_left, bottom_right, roles: data_changed_emitted.append((top_left.row(), roles)))

    model.update_thumbnail("tr_shake", dummy_pix)

    assert len(data_changed_emitted) == 1
    assert data_changed_emitted[0][0] == 1
    assert ThumbnailRole in data_changed_emitted[0][1]
    assert model.data(idx_1, ThumbnailRole) is not None


def test_asset_filter_category_and_search(qapp, sample_raw_assets):
    model = AssetListModel(sample_raw_assets)
    search_idx = SearchIndex(sample_raw_assets)
    proxy = AssetFilterProxyModel(search_idx)
    proxy.setSourceModel(model)

    assert proxy.rowCount() == 5

    # Filter by category text
    proxy.set_category("text")
    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0), IdRole) == "t_hormozi"

    # Filter by category sfx
    proxy.set_category("sfx")
    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0), IdRole) == "s_whoosh"

    # Reset category
    proxy.set_category("all")
    assert proxy.rowCount() == 5

    # Search with SearchIndex: "shake" -> tr_shake should be top
    proxy.set_search_query("shake")
    assert proxy.rowCount() >= 1
    assert proxy.data(proxy.index(0, 0), IdRole) == "tr_shake"

    # Clear search
    proxy.set_search_query("")
    assert proxy.rowCount() == 5


def test_asset_filter_modes(qapp, sample_raw_assets):
    model = AssetListModel(sample_raw_assets, favs={"tr_shake"})
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)

    # Favorite filter mode
    proxy.set_filter_mode("fav")
    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0), IdRole) == "tr_shake"

    # Recent filter mode
    proxy.set_filter_mode("recent")
    proxy.set_recent_ids(["b_lofi", "s_whoosh"])
    assert proxy.rowCount() == 2
    matched_ids = {proxy.data(proxy.index(i, 0), IdRole) for i in range(proxy.rowCount())}
    assert matched_ids == {"b_lofi", "s_whoosh"}


def test_asset_delegate_paint_no_crash(qapp, sample_raw_assets):
    model = AssetListModel(sample_raw_assets, favs={"t_hormozi"})
    delegate = AssetCardDelegate()

    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 194, 136)

    # Verify sizeHint
    sh = delegate.sizeHint(opt, model.index(0, 0))
    assert sh.width() == 194
    assert sh.height() > 100

    # Test paint on QImage buffer
    img = QImage(200, 150, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.black)
    painter = QPainter(img)

    try:
        # Paint title card
        delegate.paint(painter, opt, model.index(0, 0))

        # Paint audio card (procedural waveform)
        delegate.paint(painter, opt, model.index(2, 0))

        # Paint selected card
        from PySide6.QtWidgets import QStyle
        opt.state |= QStyle.StateFlag.State_Selected
        delegate.paint(painter, opt, model.index(1, 0))
    finally:
        painter.end()


def test_asset_grid_column_calculation_and_drag_mime(qapp, sample_raw_assets):
    grid = AssetGridView()
    model = AssetListModel(sample_raw_assets)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)
    grid.setModel(proxy)

    # Simulate wide window (600px width -> cols = (600+8)/(180+8) = 3 cols)
    grid.resize(600, 400)
    grid._recalculate_grid_geometry()
    gs = grid.gridSize()
    assert gs.width() > 0
    assert gs.height() > 0

    # Test drag mime data generation
    raw_title = sample_raw_assets[0]
    mime = create_asset_mime_data(raw_title, sample_text="ChunDVC Test")
    assert mime is not None
    assert mime.hasText() or mime.hasUrls()


def test_tab_assets_virtualization_integration(qapp):
    """
    Verifies that TabAssets embeds AssetGridView, connects signals,
    and no longer instantiates hundreds of AssetCard widgets on startup.
    """
    from src.ui.tabs.tab_assets import TabAssets

    tab = TabAssets()

    # AssetGridView must be active and present
    assert hasattr(tab, "asset_grid")
    assert hasattr(tab, "asset_model")
    assert hasattr(tab, "asset_filter")
    assert isinstance(tab.asset_grid, AssetGridView)

    # Verify widget overhead reduction: initial card cache is None!
    assert tab._card_widgets_cache is None, "AssetCard widgets must NOT be created initially on startup!"

    # Rail navigation updates AssetFilterProxyModel
    tab._filter_by_rail("lut")
    assert tab.current_rail_tab == "lut"
    assert tab.asset_filter._category == "lut"

    tab._filter_by_rail("text")
    assert tab.current_rail_tab == "text"
    assert tab.asset_filter._category == "text"

    # Searching updates filter immediately
    tab.txt_search.setText("hormozi")
    tab._refresh_grid()
    assert tab.asset_filter._search_query == "hormozi"
    assert tab.asset_filter.rowCount() >= 1

    # Inspector selection updates cleanly
    tab.asset_grid.asset_selected.emit(tab.all_presets[0])
    assert tab.selected_asset is not None


def test_text_preset_rich_previews_and_inspector_canvas(qapp):
    from src.ui.lux.library.text_preview_generator import (
        draw_text_preset_preview, get_text_preset_pixmap
    )
    from src.ui.lux.inspector.editors import TextPresetEditor, TextPresetPreviewCanvas

    # 1. Test generator functions for diverse preset types
    preset_ids = [
        "kinetic_hormozi", "karaoke_pop", "box_highlight", "highlighter_swipe",
        "glow_neon", "vhs_retro", "paper_cutout", "rgb_glitch", "gradient_fill",
        "mrbeast_impact_yellow", "luxury_gold_serif", "comic_manga_burst"
    ]

    for pid in preset_ids:
        pix = get_text_preset_pixmap(pid, name=pid, width=320, height=180)
        assert pix is not None
        assert not pix.isNull()
        assert pix.width() == 320
        assert pix.height() == 180

    # 2. Test TextPresetEditor has interactive TextPresetPreviewCanvas
    asset = {
        "id": "kinetic_hormozi",
        "name": "Alex Hormozi Title",
        "category": "text",
        "duration": 3.0
    }
    editor = TextPresetEditor(asset)
    assert hasattr(editor, "preview_canvas")
    assert isinstance(editor.preview_canvas, TextPresetPreviewCanvas)
    assert editor.preview_canvas.preset_id == "kinetic_hormozi"
    assert editor.preview_canvas.custom_text == "ResolveFlow Studio"

    # Typing updates custom text in preview canvas
    editor.txt_content.setText("TIỀN ĐÂU?")
    assert editor.preview_canvas.custom_text == "TIỀN ĐÂU?"


def test_shared_hover_preview_audio_enabled_and_controls(qapp, tmp_path):
    from src.ui.lux.library.hover_preview import SharedHoverPreview
    from src.ui.lux.library.asset_grid import AssetGridView
    from src.ui.lux.shell.dock_header import DockHeaderWidget
    from src.ui.lux.shell.dock_settings_dialog import DockSettingsDialog
    from PySide6.QtCore import QSettings

    # 1. Test SharedHoverPreview properties and persistence
    grid = AssetGridView()
    hover = grid.hover_controller
    assert isinstance(hover, SharedHoverPreview)

    # Hover audio is enabled by default as requested
    hover.set_audio_preview_enabled(True)
    assert hover.is_audio_preview_enabled is True

    # Volume setting and clamping
    hover.set_volume(0.8)
    assert abs(hover.volume - 0.8) < 1e-4

    hover.set_volume(1.5)
    assert hover.volume == 1.0

    hover.set_volume(-0.5)
    assert hover.volume == 0.0

    hover.set_volume(0.75)

    # Mute toggle
    hover.set_audio_preview_enabled(False)
    assert hover.is_audio_preview_enabled is False

    hover.set_audio_preview_enabled(True)
    assert hover.is_audio_preview_enabled is True

    # 2. Test DockHeaderWidget audio toggle button
    header = DockHeaderWidget()
    assert hasattr(header, "btn_audio")

    toggled_states = []
    header.audio_preview_toggled.connect(lambda s: toggled_states.append(s))

    # Click toggles state
    header._audio_enabled = True
    header.btn_audio.click()
    assert len(toggled_states) == 1
    assert toggled_states[-1] is False
    assert "ĐANG TẮT" in header.btn_audio.toolTip()

    header.btn_audio.click()
    assert len(toggled_states) == 2
    assert toggled_states[-1] is True
    assert "ĐANG BẬT" in header.btn_audio.toolTip()

    # 3. Test DockSettingsDialog controls
    dlg = DockSettingsDialog()
    assert hasattr(dlg, "chk_hover_audio")
    assert hasattr(dlg, "slider_volume")

    dialog_audio_states = []
    dialog_volume_states = []
    dlg.hover_audio_changed.connect(lambda s: dialog_audio_states.append(s))
    dlg.hover_volume_changed.connect(lambda v: dialog_volume_states.append(v))

    dlg.chk_hover_audio.setChecked(False)
    assert len(dialog_audio_states) >= 1
    assert dialog_audio_states[-1] is False

    dlg.slider_volume.setValue(90)
    assert len(dialog_volume_states) >= 1
    assert abs(dialog_volume_states[-1] - 0.90) < 1e-4
    assert dlg.lbl_vol_val.text() == "90%"

