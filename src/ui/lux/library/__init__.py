"""
src/ui/lux/library package
Model/View architecture for high-performance virtualized asset library.
"""
from src.ui.lux.library.asset_model import (
    AssetListModel,
    IdRole,
    NameRole,
    KindRole,
    DurationRole,
    ThumbnailRole,
    IsFavRole,
    PreviewPathRole,
    CategoryRole,
    SubCategoryRole,
    AssetObjRole,
    MotionRole,
    FilePathRole,
    TagsRole,
)
from src.ui.lux.library.asset_filter import AssetFilterProxyModel
from src.ui.lux.library.asset_delegate import AssetCardDelegate
from src.ui.lux.library.asset_grid import AssetGridView, create_asset_mime_data
from src.ui.lux.library.hover_preview import SharedHoverPreview
from src.ui.lux.library.category_nav import CategoryNavWidget
from src.ui.lux.library.action_bar import DockActionBar

__all__ = [
    "AssetListModel",
    "AssetFilterProxyModel",
    "AssetCardDelegate",
    "AssetGridView",
    "SharedHoverPreview",
    "CategoryNavWidget",
    "DockActionBar",
    "create_asset_mime_data",
    "IdRole",
    "NameRole",
    "KindRole",
    "DurationRole",
    "ThumbnailRole",
    "IsFavRole",
    "PreviewPathRole",
    "CategoryRole",
    "SubCategoryRole",
    "AssetObjRole",
    "MotionRole",
    "FilePathRole",
    "TagsRole",
]
