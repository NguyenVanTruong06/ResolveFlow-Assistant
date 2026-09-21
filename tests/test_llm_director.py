import pytest
import json
from unittest.mock import patch, MagicMock
from urllib.error import URLError
from src.core.llm_director import LLMSemanticSelector, LLMSelectionError

@pytest.fixture
def sample_subtitles():
    return [
        {"start": 0.0, "end": 2.0, "text": "Câu 1"},
        {"start": 2.5, "end": 4.5, "text": "Câu 2"},
        {"start": 5.0, "end": 7.0, "text": "Câu 3"},
        {"start": 7.5, "end": 9.5, "text": "Câu 4"}
    ]

def test_no_api_key_raises_error(sample_subtitles):
    selector = LLMSemanticSelector(api_key=None)
    with pytest.raises(LLMSelectionError, match="API Key is not provided"):
        selector.select_segments(sample_subtitles, target_duration=4.0, mode="viral_shorts")

@patch("urllib.request.urlopen")
def test_valid_llm_response(mock_urlopen, sample_subtitles):
    mock_response = MagicMock()
    # Tổng thời lượng của index 0 và 2 là 2.0 + 2.0 = 4.0s
    mock_response.read.return_value = json.dumps({
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": '{"kept_indices": [0, 2], "reasoning": "Keep important hooks"}'}
                    ]
                }
            }
        ]
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    selector = LLMSemanticSelector(api_key="fake-key", provider="gemini")
    kept_indices, reasoning = selector.select_segments(sample_subtitles, target_duration=4.0, mode="viral_shorts")
    
    assert kept_indices == [0, 2]
    assert reasoning == "Keep important hooks"

@patch("urllib.request.urlopen")
def test_invalid_json_raises_error(mock_urlopen, sample_subtitles):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": '```json\n{"invalid json" \n```'}
                    ]
                }
            }
        ]
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    selector = LLMSemanticSelector(api_key="fake-key", provider="gemini")
    with pytest.raises(LLMSelectionError, match="Không thể parse JSON từ LLM"):
        selector.select_segments(sample_subtitles, target_duration=4.0, mode="viral_shorts")

@patch("urllib.request.urlopen")
def test_out_of_range_indices_raises_error(mock_urlopen, sample_subtitles):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": '{"kept_indices": [0, 10], "reasoning": ""}'}
                    ]
                }
            }
        ]
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    selector = LLMSemanticSelector(api_key="fake-key", provider="gemini")
    with pytest.raises(LLMSelectionError, match="nằm ngoài range của batch"):
        selector.select_segments(sample_subtitles, target_duration=4.0, mode="viral_shorts")

@patch("urllib.request.urlopen")
def test_duration_deviation_raises_error(mock_urlopen, sample_subtitles):
    mock_response = MagicMock()
    # Chọn cả 4 segment => tổng 8 giây, trong khi target là 4 giây (lệch > 30%)
    mock_response.read.return_value = json.dumps({
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": '{"kept_indices": [0, 1, 2, 3], "reasoning": ""}'}
                    ]
                }
            }
        ]
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    selector = LLMSemanticSelector(api_key="fake-key", provider="gemini")
    with pytest.raises(LLMSelectionError, match="lệch quá 30% so với target_duration"):
        selector.select_segments(sample_subtitles, target_duration=4.0, mode="viral_shorts")

@patch("urllib.request.urlopen")
def test_timeout_raises_error(mock_urlopen, sample_subtitles):
    mock_urlopen.side_effect = URLError("timeout")
    selector = LLMSemanticSelector(api_key="fake-key", provider="gemini")
    with pytest.raises(LLMSelectionError, match="Lỗi kết nối hoặc timeout"):
        selector.select_segments(sample_subtitles, target_duration=4.0, mode="viral_shorts")
