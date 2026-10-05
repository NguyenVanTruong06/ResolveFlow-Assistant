import os
import pytest

from src.core.story_copilot import (
    StoryCopilot,
    CopilotHookPlan,
    CopilotSegmentPlan,
    CopilotDirectorPlan
)


def test_generate_copilot_prompt():
    clips = [
        {
            "name": "DJI_0084.MP4",
            "chapter": "01_DiChuyen",
            "duration": 45.0,
            "audio_peak_sec": 14.2,
            "visual_motion": "cao",
            "speech": "Hôm nay chúng ta cùng đi khám phá cây tùng cổ thụ nhé!"
        },
        {
            "name": "DJI_0106.MP4",
            "chapter": "02_CauKhi",
            "duration": 60.0,
            "audio_peak_sec": 30.5,
            "visual_motion": "rất cao",
            "speech": "Ôi không thể tin được cây cầu khỉ này rung lắc quá suýt té!"
        }
    ]

    prompt = StoryCopilot.generate_copilot_prompt(
        project_name="Vlog_Kham_Pha",
        clips_data=clips,
        story_intent="vlog_hook"
    )

    assert "CHỦ TỊCH / CHUYÊN GIA MARKETING" in prompt or "TỔNG ĐẠO DIỄN" in prompt
    assert "DJI_0084.MP4" in prompt
    assert "DJI_0106.MP4" in prompt
    assert "02_CauKhi" in prompt
    assert "global_hook" in prompt
    assert "timeline_segments" in prompt


def test_parse_copilot_response_valid():
    sample_response = """
    Dưới đây là kịch bản tôi đã tối ưu cho vlog của bạn:

    ```json
    {
      "strategy_summary": "Tập trung vào cảm giác hồi hộp qua cầu khỉ để giữ chân người xem 5s đầu",
      "target_platform": "YouTube / Shorts",
      "global_hook": {
        "clip_index": 2,
        "clip_name": "DJI_0106.MP4",
        "start_sec": 28.0,
        "end_sec": 32.5,
        "hook_title": "ĐIỀU KINH HOÀNG GIỮA RỪNG SÂU",
        "reason": "Cao trào âm thanh hét lớn và hình ảnh rung lắc mạnh",
        "punch_in": true
      },
      "timeline_segments": [
        {
          "clip_index": 1,
          "clip_name": "DJI_0084.MP4",
          "chapter_name": "01_DiChuyen",
          "start_sec": 0.0,
          "end_sec": 15.0,
          "action": "keep",
          "speed": 1.0,
          "role": "intro",
          "note": "Xuất phát"
        },
        {
          "clip_index": 2,
          "clip_name": "DJI_0106.MP4",
          "chapter_name": "02_CauKhi",
          "start_sec": 10.0,
          "end_sec": 45.0,
          "action": "keep",
          "speed": 1.0,
          "role": "climax",
          "note": "Vượt cầu khỉ"
        }
      ],
      "viral_headlines": [
        "Đừng Thử Đi Cầu Khỉ Này Một Mình!",
        "Chuyến Đi Khám Phá Rừng Bí Ẩn"
      ],
      "call_to_action": "Bấm Đăng ký kênh để cùng mình đi tiếp tập 2 nhé!",
      "seo_hashtags": ["#Vlog", "#DuLich", "#CauKhi"]
    }
    ```
    """

    plan = StoryCopilot.parse_copilot_response(sample_response)
    assert plan is not None
    assert "cầu khỉ" in plan.strategy_summary
    assert plan.global_hook is not None
    assert plan.global_hook.clip_index == 2
    assert plan.global_hook.hook_title == "ĐIỀU KINH HOÀNG GIỮA RỪNG SÂU"
    assert len(plan.timeline_segments) == 2
    assert plan.timeline_segments[0].role == "intro"
    assert plan.timeline_segments[1].role == "climax"
    assert len(plan.viral_headlines) == 2


def test_parse_copilot_response_invalid():
    with pytest.raises(ValueError):
        StoryCopilot.parse_copilot_response("Không có json nào ở đây cả.")


def test_convert_plan_to_resolve_timeline(tmp_path):
    # Tạo giả lập 2 file
    f1 = tmp_path / "clip1.mp4"
    f2 = tmp_path / "clip2.mp4"
    f1.write_bytes(b"content1")
    f2.write_bytes(b"content2")

    plan = CopilotDirectorPlan(
        strategy_summary="Test strategy",
        global_hook=CopilotHookPlan(
            clip_index=2,
            clip_name="clip2.mp4",
            start_sec=10.0,
            end_sec=14.0,
            hook_title="HOOK TỎA SÁNG",
            reason="Lý do đỉnh cao"
        ),
        timeline_segments=[
            CopilotSegmentPlan(
                clip_index=1,
                clip_name="clip1.mp4",
                chapter_name="Ch1",
                start_sec=0.0,
                end_sec=10.0,
                role="intro",
                note="Mở đầu"
            ),
            CopilotSegmentPlan(
                clip_index=2,
                clip_name="clip2.mp4",
                chapter_name="Ch2",
                start_sec=5.0,
                end_sec=25.0,
                role="climax",
                note="Cao trào"
            )
        ],
        broll_inserts=[
            {"timeline_sec": 5.0, "duration_sec": 2.0, "asset_type": "broll", "description": "Flycam sông núi"}
        ],
        sfx_inserts=[
            {"timeline_sec": 0.0, "duration_sec": 1.0, "asset_type": "sfx", "description": "Whoosh"}
        ],
        call_to_action="Đăng ký kênh nhé!"
    )

    path_map = {1: str(f1), 2: str(f2)}
    events, subs, markers = StoryCopilot.convert_plan_to_resolve_timeline(plan, path_map)

    # Event 0 là Hook (4s + 0.6s pad: từ 0.0 đến 4.6)
    assert len(events) == 3
    assert events[0]["is_hook"] is True
    assert events[0]["rec_in"] == 0.0
    assert abs(events[0]["rec_out"] - 4.6) < 1e-3
    assert events[0]["video_path"] == str(f2)

    # Event 1 là Ch1 (10.3s: từ 4.6 đến 14.9)
    assert abs(events[1]["rec_in"] - 4.6) < 1e-3
    assert abs(events[1]["rec_out"] - 14.9) < 1e-3
    assert events[1]["video_path"] == str(f1)

    # Event 2 là Ch2 (20.6s: từ 14.9 đến 35.5)
    assert abs(events[2]["rec_in"] - 14.9) < 1e-3
    assert abs(events[2]["rec_out"] - 35.5) < 1e-3
    assert events[2]["video_path"] == str(f2)

    # Markers
    assert any("GLOBAL HOOK" in m["name"] for m in markers)
    assert any("CTA" in m["name"] for m in markers)
    assert any("B-ROLL TRACK 2" in m["name"] for m in markers)
    assert any("SFX TRACK" in m["name"] for m in markers)


def test_find_clip_path(tmp_path):
    f1 = tmp_path / "DJI_20260831090044_0102_D.MP4"
    f2 = tmp_path / "SubFolder" / "DJI_20260830113907_0076_D.MP4"
    f2.parent.mkdir(parents=True, exist_ok=True)
    f1.write_bytes(b"1")
    f2.write_bytes(b"2")

    path_map = {
        1: str(f1),
        "dji_20260831090044_0102_d.mp4": str(f1),
        "dji_20260831090044_0102_d": str(f1),
        "dji_20260830113907_0076_d.mp4": str(f2),
        "dji_20260830113907_0076_d": str(f2),
    }

    # 1. Match by exact name
    res1 = StoryCopilot._find_clip_path(clip_index=99, clip_name="DJI_20260831090044_0102_D.MP4", video_paths_by_index=path_map)
    assert res1 == str(f1)

    # 2. Match by case-insensitive name in subfolder
    res2 = StoryCopilot._find_clip_path(clip_index=None, clip_name="dji_20260830113907_0076_d.mp4", video_paths_by_index=path_map)
    assert res2 == str(f2)

    # 3. Match by stem (no extension)
    res3 = StoryCopilot._find_clip_path(clip_index=None, clip_name="DJI_20260830113907_0076_D", video_paths_by_index=path_map)
    assert res3 == str(f2)

    # 4. Match with markdown backticks
    res4 = StoryCopilot._find_clip_path(clip_index=None, clip_name="`DJI_20260831090044_0102_D.MP4`", video_paths_by_index=path_map)
    assert res4 == str(f1)

    # 5. Match with string formatted index like "Clip 1" or "#1"
    res5 = StoryCopilot._find_clip_path(clip_index="Clip 1", clip_name="", video_paths_by_index=path_map)
    assert res5 == str(f1)

    # 6. Fallback to index if name is empty
    res6 = StoryCopilot._find_clip_path(clip_index=1, clip_name="", video_paths_by_index=path_map)
    assert res6 == str(f1)


def test_convert_plan_with_name_matching_and_assets(tmp_path):
    f1 = tmp_path / "DJI_20260831090044_0102_D.MP4"
    f2 = tmp_path / "DJI_20260830113907_0076_D.MP4"
    f1.write_bytes(b"f1")
    f2.write_bytes(b"f2")

    plan_json = """
    {
      "strategy_summary": "Documentary test plan",
      "global_hook": {
        "clip_index": 999,
        "clip_name": "DJI_20260831090044_0102_D.MP4",
        "start_sec": 3.0,
        "end_sec": 7.0,
        "hook_title": "100% SỨC KHỎE VÀ CÁI KẾT?",
        "reason": "Test hook",
        "punch_in": true
      },
      "timeline_segments": [
        {
          "clip_index": 888,
          "clip_name": "DJI_20260830113907_0076_D.MP4",
          "chapter_name": "01_Phuot_Xe_May",
          "start_sec": 0.0,
          "end_sec": 20.0,
          "action": "keep",
          "speed": 1.0,
          "role": "intro",
          "note": "Khởi hành"
        }
      ],
      "broll_inserts": [
        {
          "timeline_sec": 5.0,
          "duration_sec": 2.0,
          "asset_file": "cat_vibing_head.mp4",
          "description": "Mèo chill"
        }
      ],
      "sfx_inserts": [
        {
          "timeline_sec": 4.0,
          "duration_sec": 0.5,
          "asset_file": "whoosh.wav",
          "description": "Whoosh sound"
        }
      ]
    }
    """

    plan = StoryCopilot.parse_copilot_response(plan_json)
    path_map = {
        "dji_20260831090044_0102_d.mp4": str(f1),
        "dji_20260830113907_0076_d.mp4": str(f2)
    }

    events, subs, markers = StoryCopilot.convert_plan_to_resolve_timeline(plan, path_map)

    # 1 Hook + 1 Segment (and possibly assets if available in repo assets)
    main_events = [e for e in events if e.get("track", 1) == 1]
    assert len(main_events) == 2
    assert main_events[0]["video_path"] == str(f1)
    assert main_events[0]["is_hook"] is True
    assert main_events[1]["video_path"] == str(f2)
    assert len(subs) >= 1
    assert subs[0]["text"] == "100% SỨC KHỎE VÀ CÁI KẾT?"


def test_user_large_docu_vlog_plan_resolution(tmp_path):
    clip_names = [
        "DJI_20260831090044_0102_D.MP4",
        "DJI_20260830113907_0076_D.MP4",
        "DJI_20260830121454_0078_D.MP4",
        "DJI_20260830121828_0079_D.MP4",
        "DJI_20260830133720_0080_D.MP4",
        "DJI_20260830174401_0081_D.MP4",
        "DJI_20260830174529_0082_D.MP4",
        "DJI_20260830174816_0083_D.MP4",
        "DJI_20260830175112_0084_D.MP4",
        "DJI_20260830191127_0086_D.MP4"
    ]

    path_map = {}
    for idx, name in enumerate(clip_names, 1):
        f = tmp_path / name
        f.write_bytes(b"data")
        path_map[idx] = str(f)
        path_map[name.lower()] = str(f)
        path_map[os.path.splitext(name)[0].lower()] = str(f)

    # Simulated Copilot Plan with 1 hook and multiple segments where clip_index might be arbitrary
    plan_data = {
        "strategy_summary": "Phim tài liệu Nam Cát Tiên 2N1D",
        "global_hook": {
            "clip_index": 13,
            "clip_name": "DJI_20260831090044_0102_D.MP4",
            "start_sec": 3.0,
            "end_sec": 7.0,
            "hook_title": "100% SỨC KHỎE VÀ CÁI KẾT?",
            "reason": "Test hook",
            "punch_in": True
        },
        "timeline_segments": [
            {
                "clip_index": 54,
                "clip_name": "DJI_20260830113907_0076_D.MP4",
                "chapter_name": "01_Phuot",
                "start_sec": 0.0,
                "end_sec": 20.0,
                "action": "keep",
                "speed": 1.0,
                "role": "intro",
                "note": "Khởi hành"
            },
            {
                "clip_index": 56,
                "clip_name": "DJI_20260830121454_0078_D.MP4",
                "chapter_name": "01_Phuot",
                "start_sec": 0.0,
                "end_sec": 35.0,
                "action": "keep",
                "speed": 1.0,
                "role": "dialogue",
                "note": "Hỏi đường"
            },
            {
                "clip_index": 57,
                "clip_name": "DJI_20260830121828_0079_D.MP4",
                "chapter_name": "01_Phuot",
                "start_sec": 0.0,
                "end_sec": 30.0,
                "action": "keep",
                "speed": 1.0,
                "role": "dialogue",
                "note": "Chào Cát Tiên"
            },
            {
                "clip_index": 58,
                "clip_name": "DJI_20260830133720_0080_D.MP4",
                "chapter_name": "01_Phuot",
                "start_sec": 5.0,
                "end_sec": 85.0,
                "action": "timelapse",
                "speed": 2.0,
                "role": "broll",
                "note": "Cung đường"
            },
            {
                "clip_index": 19,
                "clip_name": "DJI_20260830174401_0081_D.MP4",
                "chapter_name": "02_Cho_Que",
                "start_sec": 0.0,
                "end_sec": 25.0,
                "action": "keep",
                "speed": 1.0,
                "role": "dialogue",
                "note": "Ghé chợ quê"
            }
        ],
        "broll_inserts": [
            {
                "timeline_sec": 12.0,
                "duration_sec": 2.5,
                "asset_file": "cat_vibing_head.mp4",
                "description": "Chill B-roll"
            }
        ],
        "sfx_inserts": [
            {
                "timeline_sec": 3.0,
                "duration_sec": 0.5,
                "asset_file": "whoosh.wav",
                "description": "Chuyển cảnh"
            }
        ],
        "call_to_action": "Đăng ký kênh để đón xem tập 2!"
    }

    import json
    plan = StoryCopilot.parse_copilot_response(json.dumps(plan_data))
    events, subs, markers = StoryCopilot.convert_plan_to_resolve_timeline(plan, path_map)

    # Must resolve 1 hook + 5 segments = 6 main track events without missing any!
    v1_events = [e for e in events if e.get("track", 1) == 1]
    assert len(v1_events) == 6
    assert v1_events[0]["is_hook"] is True
    assert v1_events[0]["video_path"].endswith("DJI_20260831090044_0102_D.MP4")
    assert v1_events[1]["video_path"].endswith("DJI_20260830113907_0076_D.MP4")
    assert v1_events[2]["video_path"].endswith("DJI_20260830121454_0078_D.MP4")
    assert v1_events[3]["video_path"].endswith("DJI_20260830121828_0079_D.MP4")
    assert v1_events[4]["video_path"].endswith("DJI_20260830133720_0080_D.MP4")
    assert v1_events[5]["video_path"].endswith("DJI_20260830174401_0081_D.MP4")

    # Time cursor continuity check
    assert v1_events[0]["rec_in"] == 0.0
    assert v1_events[1]["rec_in"] == v1_events[0]["rec_out"]
    assert v1_events[2]["rec_in"] == v1_events[1]["rec_out"]
    assert v1_events[3]["rec_in"] == v1_events[2]["rec_out"]
    assert v1_events[4]["rec_in"] == v1_events[3]["rec_out"]
    assert v1_events[5]["rec_in"] == v1_events[4]["rec_out"]


def test_format_prompts_selection():
    clips = [{"name": "clip1.mp4", "duration": 10.0, "speech": "Test speech"}]
    
    prompt_vlog = StoryCopilot.generate_copilot_prompt("P", clips, story_intent="travel_vlog")
    assert "TRAVEL VLOG" in prompt_vlog or "DAILY VLOG" in prompt_vlog

    prompt_tiktok = StoryCopilot.generate_copilot_prompt("P", clips, story_intent="tiktok_short")
    assert "TIKTOK" in prompt_tiktok or "SHORTS" in prompt_tiktok

    prompt_podcast = StoryCopilot.generate_copilot_prompt("P", clips, story_intent="podcast_summary")
    assert "PODCAST" in prompt_podcast or "TALKING HEAD" in prompt_podcast


def test_run_ollama_inference_mock(monkeypatch):
    import io
    import json

    valid_json = json.dumps({
        "strategy_summary": "Test Ollama strategy",
        "target_platform": "TikTok",
        "timeline_segments": [
            {"clip_index": 1, "clip_name": "test.mp4", "start_sec": 0.0, "end_sec": 5.0}
        ]
    })
    
    mock_response_data = json.dumps({"response": valid_json}).encode("utf-8")

    class MockResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return mock_response_data

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=120.0: MockResponse())

    plan, raw = StoryCopilot.run_ollama_inference("test prompt")
    assert plan.strategy_summary == "Test Ollama strategy"
    assert len(plan.timeline_segments) == 1


def test_run_cloud_api_inference_mock(monkeypatch):
    import io
    import json

    valid_json = json.dumps({
        "strategy_summary": "Test Cloud strategy",
        "target_platform": "YouTube",
        "timeline_segments": [
            {"clip_index": 1, "clip_name": "test.mp4", "start_sec": 0.0, "end_sec": 10.0}
        ]
    })

    # Mock DeepSeek / OpenAI format
    mock_data = json.dumps({
        "choices": [{"message": {"content": valid_json}}]
    }).encode("utf-8")

    class MockResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return mock_data

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=120.0: MockResponse())

    plan, raw = StoryCopilot.run_cloud_api_inference("test prompt", api_key="sk-test-123", provider="deepseek")
    assert plan.strategy_summary == "Test Cloud strategy"
    assert len(plan.timeline_segments) == 1


