"""
tests/core/test_story_copilot_creative_binding.py
=============================================================================
Finite Unit Test Suite cho StoryCopilot Creative Context Binding (G06-06C3-S2a):
1. None/empty byte-for-byte legacy parity.
2. Canonical escaped JSON section rendering & exact lossless json.loads roundtrip.
3. Multiline text, quotes, backslashes, Vietnamese accents, scientific floats.
4. Static surrounding instructions (metadata only, no file read/fetch, user notes).
5. Independent boundary normalization and invalid type/field rejection (ValueError).
6. Strict budget enforcement: complete creative block in every candidate and minimum skeleton;
   fail-closed ValueError when min_prompt exceeds max_prompt_bytes; exact equality accepted.
7. Unbounded mode (max_prompt_bytes=None) legacy behavior preserved.
=============================================================================
"""

import os
import json
import pytest

from src.core.contracts.edit_plan_contracts import (
    CreativeContext,
    ReferenceItem,
    normalize_creative_context,
    creative_context_to_dict,
)
from src.core.story_copilot import StoryCopilot


def test_video_prompt_example_requires_manifest_clip_ids(sample_clips_data):
    prompt = StoryCopilot.generate_copilot_prompt(
        "Demo", sample_clips_data, manifest_id="manifest_test"
    )
    example = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])

    assert example["manifest_id"] == "manifest_test"
    assert example["global_hook"]["clip_id"]
    assert example["timeline_segments"][0]["clip_id"]


def test_video_prompt_example_fits_a_short_source_clip(sample_clips_data):
    short_clip = {**sample_clips_data[0], "duration": 2.5}
    prompt = StoryCopilot.generate_copilot_prompt("Demo", [short_clip], hook_duration="30_60s")
    example = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])
    segment = example["timeline_segments"][0]

    assert 0 <= segment["start_sec"] < segment["end_sec"] <= 2.5
    assert example["global_hook"] is None


@pytest.fixture
def sample_clips_data():
    return [
        {
            "clip_id": "C01",
            "name": "clip_01_intro.mp4",
            "chapter": "01_MoDau",
            "duration": 25.0,
            "duration_known": True,
            "source_availability": "available",
            "speech_status": "dialogue_present",
            "audio_analysis_status": "completed",
            "visual_analysis_status": "described",
            "dialogue_intervals": [{"start": 1.0, "end": 10.0, "text": "Chào mừng đến với chuyến hành trình Đà Lạt!"}],
            "transcript": "Chào mừng đến với chuyến hành trình Đà Lạt!",
            "visual_segments": [
                {
                    "start_sec": 0.0,
                    "end_sec": 5.0,
                    "description": "Góc quay flycam rừng thông bạt ngàn trong sương sớm",
                    "observed_actions": ["bay lượn"],
                    "analysis_status": "described",
                    "origin": "vlm",
                }
            ],
        },
        {
            "clip_id": "C02",
            "name": "clip_02_food.mp4",
            "chapter": "02_AmThuc",
            "duration": 40.0,
            "duration_known": True,
            "source_availability": "available",
            "speech_status": "dialogue_present",
            "audio_analysis_status": "completed",
            "visual_analysis_status": "described",
            "dialogue_intervals": [{"start": 2.0, "end": 15.0, "text": "Món bánh ướt lòng gà nóng hổi ngon tuyệt vời."}],
            "transcript": "Món bánh ướt lòng gà nóng hổi ngon tuyệt vời.",
            "visual_segments": [
                {
                    "start_sec": 0.0,
                    "end_sec": 6.0,
                    "description": "Cận cảnh đĩa bánh ướt bốc khói nghi ngút",
                    "observed_actions": ["gắp thức ăn"],
                    "analysis_status": "described",
                    "origin": "vlm",
                }
            ],
        },
    ]


def test_none_and_empty_context_preserves_exact_legacy_prompt_bytes(sample_clips_data):
    """None, tham số mặc định, dict rỗng {}, và CreativeContext() mặc định phải tạo prompt khớp 100% từng byte."""
    p_default = StoryCopilot.generate_copilot_prompt(
        project_name="LegacyTest",
        clips_data=sample_clips_data,
        story_intent="travel_vlog",
    )
    p_none = StoryCopilot.generate_copilot_prompt(
        project_name="LegacyTest",
        clips_data=sample_clips_data,
        story_intent="travel_vlog",
        creative_context=None,
    )
    p_empty_dict = StoryCopilot.generate_copilot_prompt(
        project_name="LegacyTest",
        clips_data=sample_clips_data,
        story_intent="travel_vlog",
        creative_context={},
    )
    p_empty_obj = StoryCopilot.generate_copilot_prompt(
        project_name="LegacyTest",
        clips_data=sample_clips_data,
        story_intent="travel_vlog",
        creative_context=CreativeContext(),
    )

    assert p_default == p_none
    assert p_none == p_empty_dict
    assert p_empty_dict == p_empty_obj
    assert "YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO" not in p_none


def test_canonical_json_rendering_and_lossless_roundtrip(sample_clips_data):
    """Nonempty context kết xuất khối Markdown riêng biệt và json.loads giải mã lại 100% dữ liệu gốc."""
    cc = normalize_creative_context({
        "free_brief": "Đoạn mở đầu cần nhấn mạnh sự yên bình của buổi sớm mai.\n\"Hãy giữ lại tiếng thở và tiếng bước chân trên sỏi.\"\\Ghi chú đặc biệt.",
        "style_preset": "cinematic_nostalgic",
        "target_duration_sec": 180.5,
        "must_keep_rules": [
            "Ưu tiên nhịp dựng chậm rãi theo tiếng gió thông.",
            "Bảo toàn trọn vẹn câu thoại của nhân vật khi giới thiệu món ăn.",
        ],
        "must_not_rules": [
            "Tuyệt đối không dùng sound effect 'vine_boom' trong các cảnh tĩnh lặng.",
        ],
        "reference_items": [
            {
                "reference_id": "ref_local_01",
                "local_path": "/home/user/footage/cinematic_reference.mp4",
                "media_type": "video",
                "role": "reference_only",
                "user_note": "Tông màu lạnh và ánh sáng ngược",
            },
            {
                "reference_id": "ref_web_02",
                "local_path": "/home/user/images/sample_reference.png",
                "media_type": "image",
                "role": "reference_only",
                "user_note": "Bố cục chia ba cho cảnh hoàng hôn",
            },
        ],
        "reference_urls": [
            "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
        ],
    })

    prompt = StoryCopilot.generate_copilot_prompt(
        project_name="DaLatVlog",
        clips_data=sample_clips_data,
        story_intent="travel_vlog",
        creative_context=cc,
    )

    # 1. Có tiêu đề phần ngữ cảnh sáng tạo
    assert "## 🎨 YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO (CREATIVE CONTEXT):" in prompt

    # 2. Hướng dẫn bao quanh về metadata và không phân tích file trực tiếp
    assert "Đường dẫn tệp cục bộ và liên kết URL chỉ là siêu dữ liệu (metadata)" in prompt
    assert "hệ thống chưa xem, tải về hay phân tích nội dung tệp/liên kết thực tế" in prompt
    assert "ghi chú là do người dùng tự cung cấp, không phải quan sát thị giác" in prompt

    # 3. Trích xuất khối json từ prompt
    start_marker = "```json\n"
    end_marker = "\n```"
    s_idx = prompt.find("## 🎨 YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO")
    assert s_idx != -1
    json_start = prompt.find(start_marker, s_idx) + len(start_marker)
    json_end = prompt.find(end_marker, json_start)
    json_block = prompt[json_start:json_end]

    # 4. json.loads giải mã lại chính xác
    parsed_dict = json.loads(json_block)
    roundtrip_cc = normalize_creative_context(parsed_dict)
    assert roundtrip_cc == cc

    # 5. Kiểm tra chi tiết tiếng Việt, dấu ngoặc kép, gạch chéo ngược
    assert roundtrip_cc.style_preset == "cinematic_nostalgic"
    assert roundtrip_cc.target_duration_sec == 180.5
    assert '"Hãy giữ lại tiếng thở và tiếng bước chân trên sỏi."' in roundtrip_cc.free_brief
    assert "\\Ghi chú đặc biệt." in roundtrip_cc.free_brief
    assert len(roundtrip_cc.reference_items) == 2
    assert roundtrip_cc.reference_items[0].reference_id == "ref_local_01"
    assert roundtrip_cc.reference_items[1].media_type == "image"
    assert roundtrip_cc.reference_urls == ("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",)


def test_scientific_floats_and_large_ints_roundtrip(sample_clips_data):
    """Bảo toàn chính xác giá trị và kiểu dữ liệu của trường số target_duration_sec (1e-07, 1e20, int 10**20)."""
    test_cases = [
        (1e-07, float),
        (1e20, float),
        (100000000000000000000, int),  # 10**20
    ]

    for dur_val, expected_type in test_cases:
        cc_dict = {
            "free_brief": "Kiểm tra trường số target_duration_sec chuẩn tắc",
            "style_preset": "cinematic",
            "target_duration_sec": dur_val,
            "must_keep_rules": ["Quy tắc số 1"],
        }
        prompt = StoryCopilot.generate_copilot_prompt(
            project_name="NumericTest",
            clips_data=sample_clips_data,
            story_intent="travel_vlog",
            creative_context=cc_dict,
        )

        # Trích xuất khối json từ prompt
        start_marker = "```json\n"
        end_marker = "\n```"
        s_idx = prompt.find("## 🎨 YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO")
        assert s_idx != -1
        json_start = prompt.find(start_marker, s_idx) + len(start_marker)
        json_end = prompt.find(end_marker, json_start)
        json_block = prompt[json_start:json_end]

        parsed = json.loads(json_block)
        actual_val = parsed["target_duration_sec"]

        # Thẩm định cả giá trị và kiểu dữ liệu chính xác (3/3 probe pass)
        assert type(actual_val) is expected_type, f"Mong đợi kiểu {expected_type.__name__}, nhận: {type(actual_val).__name__} cho giá trị {dur_val}"
        assert actual_val == dur_val, f"Giá trị không khớp: {actual_val} != {dur_val}"


def test_reference_only_zero_io_network_and_resolve_tripwires(sample_clips_data, monkeypatch):
    """Tripwires đảm bảo quá trình sinh prompt không bao giờ đọc tệp tham khảo hay gọi mạng."""
    ref_file_1 = "/controlled/mock_reference/sample_style.mp4"
    ref_file_2 = "/controlled/mock_reference/color_board.png"
    ref_url_1 = "https://example.com/reference_vlog"
    ref_url_2 = "https://vimeo.com/987654321"

    declared_ref_paths = {os.path.normpath(ref_file_1), os.path.normpath(ref_file_2)}
    declared_ref_urls = {ref_url_1, ref_url_2}

    # Tripwire 1: builtins.open không bao giờ được gọi trên tệp tham khảo
    orig_open = open
    def tripwire_open(file, *args, **kwargs):
        try:
            p = os.path.normpath(str(file))
            if p in declared_ref_paths:
                raise AssertionError(f"TRIPWIRE VIOLATION: builtins.open gọi trên tệp tham khảo: {p}")
        except Exception as e:
            if "TRIPWIRE VIOLATION" in str(e):
                raise
        return orig_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tripwire_open)

    # Tripwire 2: os.stat / os.path.exists không bao giờ được gọi trên tệp tham khảo
    orig_stat = os.stat
    def tripwire_stat(path, *args, **kwargs):
        p = os.path.normpath(str(path))
        if p in declared_ref_paths:
            raise AssertionError(f"TRIPWIRE VIOLATION: os.stat gọi trên tệp tham khảo: {p}")
        return orig_stat(path, *args, **kwargs)

    monkeypatch.setattr("os.stat", tripwire_stat)

    orig_exists = os.path.exists
    def tripwire_exists(path):
        p = os.path.normpath(str(path))
        if p in declared_ref_paths:
            raise AssertionError(f"TRIPWIRE VIOLATION: os.path.exists gọi trên tệp tham khảo: {p}")
        return orig_exists(path)

    monkeypatch.setattr("os.path.exists", tripwire_exists)

    # Tripwire 3: urllib.request không bao giờ được gọi
    import urllib.request
    def tripwire_urlopen(url, *args, **kwargs):
        raise AssertionError(f"TRIPWIRE VIOLATION: urllib.request.urlopen được gọi: {url}")

    monkeypatch.setattr("urllib.request.urlopen", tripwire_urlopen)

    cc = normalize_creative_context({
        "free_brief": "Tripwire test brief",
        "reference_items": [
            {
                "reference_id": "ref_1",
                "local_path": ref_file_1,
                "media_type": "video",
                "role": "reference_only",
                "user_note": "Ghi chú tham khảo video",
            },
            {
                "reference_id": "ref_2",
                "local_path": ref_file_2,
                "media_type": "image",
                "role": "reference_only",
                "user_note": "Ghi chú tham khảo ảnh",
            },
        ],
        "reference_urls": [ref_url_1, ref_url_2],
    })

    prompt = StoryCopilot.generate_copilot_prompt(
        project_name="TripwireTest",
        clips_data=sample_clips_data,
        story_intent="travel_vlog",
        creative_context=cc,
    )

    assert "ref_1" in prompt
    assert "ref_2" in prompt
    assert ref_url_1 in prompt
    assert ref_url_2 in prompt


def test_independent_boundary_normalization_and_validation(sample_clips_data):
    """Formatter kiểm tra tính hợp lệ của creative_context độc lập tại ranh giới công khai."""
    # bool không được coi là Mapping
    with pytest.raises(ValueError, match="creative_context phải là Mapping hoặc CreativeContext"):
        StoryCopilot.generate_copilot_prompt(
            project_name="Test",
            clips_data=sample_clips_data,
            creative_context=True,
        )

    # string không hợp lệ
    with pytest.raises(ValueError, match="creative_context phải là Mapping hoặc CreativeContext"):
        StoryCopilot.generate_copilot_prompt(
            project_name="Test",
            clips_data=sample_clips_data,
            creative_context="not a dict",
        )

    # dict chứa trường sai kiểu
    with pytest.raises(ValueError, match="creative_context không hợp lệ"):
        StoryCopilot.generate_copilot_prompt(
            project_name="Test",
            clips_data=sample_clips_data,
            creative_context={"free_brief": 12345},  # free_brief phải là str
        )

    # dict chứa unknown key
    with pytest.raises(ValueError, match="creative_context không hợp lệ"):
        StoryCopilot.generate_copilot_prompt(
            project_name="Test",
            clips_data=sample_clips_data,
            creative_context={"unknown_field": "disallowed"},
        )


def test_strict_budget_overflow_and_exact_equality(sample_clips_data):
    """Khung xương tối thiểu chứa đầy đủ khối creative_context; ném ValueError nếu max_prompt_bytes quá nhỏ."""
    cc = normalize_creative_context({
        "free_brief": "Chi tiết kịch bản cần bảo toàn đầy đủ trong ngân sách.",
        "must_keep_rules": ["Luật 1: Luôn giữ nguyên creative context"],
    })

    # Đo kích thước skeleton tối thiểu bằng cách truyền max_prompt_bytes cực nhỏ
    # để bắt ValueError chứa kích thước khung xương tối thiểu
    with pytest.raises(ValueError) as excinfo:
        StoryCopilot.generate_copilot_prompt(
            project_name="BudgetTest",
            clips_data=sample_clips_data,
            story_intent="travel_vlog",
            max_prompt_bytes=50,
            creative_context=cc,
        )
    msg = str(excinfo.value)
    assert "không đủ chứa khung xương kịch bản tối thiểu" in msg
    import re
    m = re.search(r"tối thiểu \((\d+) bytes\)", msg)
    assert m is not None
    min_skeleton_bytes = int(m.group(1))

    # Với budget đúng bằng min_skeleton_bytes -> thành công (equality budget accepted)
    p_exact = StoryCopilot.generate_copilot_prompt(
        project_name="BudgetTest",
        clips_data=sample_clips_data,
        story_intent="travel_vlog",
        max_prompt_bytes=min_skeleton_bytes,
        creative_context=cc,
    )
    assert len(p_exact.encode("utf-8")) <= min_skeleton_bytes
    assert "## 🎨 YÊU CẦU ĐẠO DIỄN VÀ NGỮ CẢNH SÁNG TẠO (CREATIVE CONTEXT):" in p_exact

    # Với budget = min_skeleton_bytes - 1 -> thất bại fail-closed
    with pytest.raises(ValueError, match="không đủ chứa khung xương kịch bản tối thiểu"):
        StoryCopilot.generate_copilot_prompt(
            project_name="BudgetTest",
            clips_data=sample_clips_data,
            story_intent="travel_vlog",
            max_prompt_bytes=min_skeleton_bytes - 1,
            creative_context=cc,
        )


def test_creative_block_never_truncated_in_candidate_renders(sample_clips_data):
    """Khối creative context không bao giờ bị cắt ngắn hay xem như không gian tự do khi giảm chi tiết clip."""
    long_rules = [f"Quy tắc thứ {i:02d}: Đảm bảo tính toàn vẹn của chỉ thị đạo diễn" for i in range(15)]
    cc = normalize_creative_context({
        "must_keep_rules": long_rules,
        "free_brief": "Brief chi tiết không được phép bị lược bỏ dù prompt bị co hẹp.",
    })

    # Đặt max_prompt_bytes vừa đủ cho content ladder bậc thấp (> min_prompt nhưng < unconstrained)
    prompt = StoryCopilot.generate_copilot_prompt(
        project_name="BudgetLadderTest",
        clips_data=sample_clips_data,
        story_intent="travel_vlog",
        max_prompt_bytes=16000,
        creative_context=cc,
    )

    # Toàn bộ 15 rules và brief vẫn phải còn nguyên vẹn trong prompt
    for rule in long_rules:
        assert rule in prompt
    assert "Brief chi tiết không được phép bị lược bỏ" in prompt
