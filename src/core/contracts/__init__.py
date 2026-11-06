"""
src/core/contracts
Hợp đồng dữ liệu ChunDVC thuần Python (Zero Qt / Clean Architecture).
"""
from src.core.contracts.asset_dto import (
    CANONICAL_STORAGE_CATEGORIES,
    UI_CATEGORIES,
    PersistedAssetRecord,
    UIAssetView,
    validate_asset_dict,
    to_persisted_record,
    to_ui_asset,
)
from src.core.contracts.time_contracts import (
    RationalFPS,
    pts_to_frame,
    frame_to_pts,
    TimeRangeFrames,
)

__all__ = [
    "CANONICAL_STORAGE_CATEGORIES",
    "UI_CATEGORIES",
    "PersistedAssetRecord",
    "UIAssetView",
    "validate_asset_dict",
    "to_persisted_record",
    "to_ui_asset",
    "RationalFPS",
    "pts_to_frame",
    "frame_to_pts",
    "TimeRangeFrames",
]
