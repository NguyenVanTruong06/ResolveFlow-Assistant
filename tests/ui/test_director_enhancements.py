"""
Unit tests for Director Prompt Enhancements and Offline Kinetic Generation
"""

import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication
from src.ui.director.pages.review_page import ReviewPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_review_page_get_video_prompt_with_cached_data(qapp):
    page = ReviewPage()

    clip_mock_1 = MagicMock()
    clip_mock_1.path = "/test/01_Forest/DJI_20260830113907_0001_D.MP4"
    clip_mock_1.filename = "DJI_20260830113907_0001_D.MP4"
    clip_mock_1.duration_sec = 0.0  # Sẽ lấy từ cache clip_dur

    clip_mock_2 = MagicMock()
    clip_mock_2.path = "/test/02_Bridge/DJI_20260830121454_0002_D.MP4"
    clip_mock_2.filename = "DJI_20260830121454_0002_D.MP4"
    clip_mock_2.duration_sec = 0.0

    mock_cache = {
        clip_mock_1.path: {
            "clip_dur": 29.5,
            "raw_subtitles": [
                {"start": 1.0, "end": 3.0, "text": "Hãy subscribe cho kênh Ghiền Mì Gõ"}
            ]
        },
        clip_mock_2.path: {
            "clip_dur": 55.8,
            "raw_subtitles": [
                {"start": 2.0, "end": 6.5, "text": "Đi qua cầu khỉ này nè"},
                {"start": 10.0, "end": 18.0, "text": "Cẩn thận trượt té nha mọi người"}
            ]
        }
    }

    with patch("src.core.cache_manager.ScanCacheManager.get_cached_scan", side_effect=lambda p, m, l: mock_cache.get(p)):
        page.load_transcripts_from_cache([clip_mock_2, clip_mock_1])
        prompt = page.get_video_prompt()

        # Kiểm tra độ dài thực tế (không phải 60.0s)
        assert "29.5s" in prompt
        assert "55.8s" in prompt

        # Clip 1 bị lọc sạch hallucination -> B-roll không lời
        assert "🏞️ B-Roll" in prompt
        assert "TOÀN BỘ CLIP LÀ B-ROLL" in prompt

        # Clip 2 có lời thoại thật kèm timestamp
        assert "💬 Thoại" in prompt
        assert "Đi qua cầu khỉ này nè" in prompt
        assert "Cẩn thận trượt té nha mọi người" in prompt

        # Thứ tự được sắp xếp chronologically (Clip 1 lúc 11:39 đứng trước Clip 2 lúc 12:14)
        idx_1 = prompt.find("DJI_20260830113907_0001_D.MP4")
        idx_2 = prompt.find("DJI_20260830121454_0002_D.MP4")
        assert idx_1 != -1 and idx_2 != -1
        assert idx_1 < idx_2


def test_auto_generate_kinetic_plan_offline(qapp):
    page = ReviewPage()
    rows = [
        ["00:00:00", "Hello bạn nha", 0, ""],
        ["00:00:02", "Hôm nay mình đi khám phá rừng chè", 0, ""],
        ["00:00:06", "Cảnh ở đây đẹp quá trời!", 0, ""]
    ]
    page.set_review_rows(rows)

    # Click nút tự tạo kịch bản Kinetic offline
    page._auto_generate_kinetic_plan()

    assert page._kinetic_plan_data is not None
    assert "pages" in page._kinetic_plan_data
    assert len(page._kinetic_plan_data["pages"]) > 0

    # Kiểm tra tính liên tục của pages
    expected = 0
    for p in page._kinetic_plan_data["pages"]:
        a, b = p["word_range"]
        assert a == expected
        assert b >= a
        expected = b + 1
    assert expected == len(page._extract_words_from_rows())

    # Trạng thái badge hiển thị thành công 0đ token
    assert "0đ Token" in page.lbl_slot_kin_status.text()


def test_final_cut_transcript_export_with_video_plan(qapp):
    page = ReviewPage()

    clip_mock = MagicMock()
    clip_mock.path = "/test/01_Forest/DJI_0001.MP4"
    clip_mock.filename = "DJI_0001.MP4"
    clip_mock.duration_sec = 60.0

    mock_cache = {
        clip_mock.path: {
            "clip_dur": 60.0,
            "raw_subtitles": [
                {
                    "start": 5.0,
                    "end": 8.0,
                    "text": "Đoạn này bị cắt bỏ ngoài video",
                    "words": [
                        {"word": "Đoạn", "start": 5.0, "end": 5.5},
                        {"word": "này", "start": 5.5, "end": 6.0},
                        {"word": "bị", "start": 6.0, "end": 6.5},
                        {"word": "cắt", "start": 6.5, "end": 7.0},
                    ]
                },
                {
                    "start": 10.0,
                    "end": 14.0,
                    "text": "Khám phá rừng chè tuyệt đẹp",
                    "words": [
                        {"word": "Khám", "start": 10.0, "end": 10.5},
                        {"word": "phá", "start": 10.5, "end": 11.0},
                        {"word": "rừng", "start": 11.0, "end": 11.5},
                        {"word": "chè", "start": 11.5, "end": 12.0},
                        {"word": "tuyệt", "start": 12.0, "end": 12.5},
                        {"word": "đẹp", "start": 12.5, "end": 13.0},
                    ]
                }
            ]
        }
    }

    with patch("src.core.cache_manager.ScanCacheManager.get_cached_scan", side_effect=lambda p, m, l: mock_cache.get(p)):
        page._clip_infos = [clip_mock]

        # 1. Nạp Video Plan (Slot 1) chỉ lấy đoạn từ 10.0s đến 14.0s
        video_plan = {
            "type": "video_plan",
            "data": {
                "timeline_segments": [
                    {
                        "clip_index": 1,
                        "clip_name": "DJI_0001.MP4",
                        "start_sec": 10.0,
                        "end_sec": 14.0,
                        "role": "story"
                    }
                ]
            }
        }
        page._apply_json_data(video_plan, "Nạp Video Plan")

        # 2. Bóc tách từ vựng thuộc Final Cut
        final_words = page.get_final_cut_words()
        assert len(final_words) == 6
        assert [w["word"] for w in final_words] == ["Khám", "phá", "rừng", "chè", "tuyệt", "đẹp"]

        # Timestamp trên timeline cuối (được căn chỉnh chuẩn xác theo 0.3s padding ở Phase 0B)
        assert round(final_words[0]["start"], 2) == 0.3
        assert round(final_words[-1]["end"], 2) == 3.3

        # 3. Xuất transcript chuỗi
        transcript_str = page.export_final_cut_transcript()
        assert "[0] Khám (0.30s - 0.80s)" in transcript_str
        assert "[5] đẹp (2.80s - 3.30s)" in transcript_str
        assert "Đoạn này bị cắt" not in transcript_str

        # 4. Tự động sinh Kinetic cho Final Cut
        page._auto_generate_kinetic_plan()
        assert page._kinetic_plan_data is not None
        assert "pages" in page._kinetic_plan_data
        assert "0đ Token" in page.lbl_slot_kin_status.text()
        assert "Bản Dựng Cuối" in page._kinetic_plan_summary


def test_kinetic_profanity_censor(qapp):
    from src.core.kinetic.kinetic_engine import auto_plan

    words = [
        {"word": "Đoạn", "start": 0.0, "end": 0.4},
        {"word": "này", "start": 0.4, "end": 0.8},
        {"word": "vãi", "start": 0.8, "end": 1.2},
        {"word": "lồn", "start": 1.2, "end": 1.6},
        {"word": "thật", "start": 1.6, "end": 2.0},
    ]

    plan = auto_plan(words, aspect="9:16", max_words=3)
    all_overrides = {}
    for p in plan["pages"]:
        for w in p.get("words", []):
            if "text_override" in w:
                all_overrides[w["i"]] = w["text_override"]

    # Từ 'lồn' ở chỉ số 3 phải được censor thành 'l**'
    assert 3 in all_overrides
    assert all_overrides[3] == "l**"

