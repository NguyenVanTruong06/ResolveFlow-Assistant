"""
Unit tests for Chronological Sorter
"""

import pytest
from src.core.chronological_sorter import get_chronological_sort_key, sort_clips_chronologically


def test_sort_clips_chronologically_dji_format():
    clips = [
        "DJI_20260831134202_0084_D.MP4",
        "DJI_20260830113907_0076_D.MP4",
        "DJI_20260831114657_0010_D.MP4",
        "DJI_20260830174401_0081_D.MP4"
    ]
    sorted_clips = sort_clips_chronologically(clips)
    assert sorted_clips[0] == "DJI_20260830113907_0076_D.MP4"
    assert sorted_clips[1] == "DJI_20260830174401_0081_D.MP4"
    assert sorted_clips[2] == "DJI_20260831114657_0010_D.MP4"
    assert sorted_clips[3] == "DJI_20260831134202_0084_D.MP4"


def test_sort_clips_chronologically_iso_format():
    clips = [
        "video_2026-08-31_14-00-00.mov",
        "video_2026-08-30_09-30-00.mov"
    ]
    sorted_clips = sort_clips_chronologically(clips)
    assert sorted_clips[0] == "video_2026-08-30_09-30-00.mov"
    assert sorted_clips[1] == "video_2026-08-31_14-00-00.mov"
