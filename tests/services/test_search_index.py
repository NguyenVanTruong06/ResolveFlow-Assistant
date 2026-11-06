"""
tests/services/test_search_index.py
Tests for SearchIndex service:
- "rung giat" -> shake item at top
- "up" does not match "zoom_out" or "rung"
- "mo dan" -> only matches fade_in (not fade_out)
- "s: rung" -> only returns audio (sfx)
- search on 600+ real assets takes < 16ms/query
"""
import os
import time
import pytest
from src.services.search_index import SearchIndex, strip_accents


@pytest.fixture
def sample_assets():
    return [
        {
            "id": "shake_01",
            "name": "RF Camera Shake",
            "category": "transitions",
            "motion": "shake",
            "confidence": 0.95,
            "tags": ["transition", "camera shake", "rung"],
        },
        {
            "id": "sfx_shake",
            "name": "Earthquake Rumbling Shake",
            "category": "sfx",
            "motion": "shake",
            "confidence": 0.9,
            "tags": ["sfx", "rumble", "rung giật"],
        },
        {
            "id": "zoom_out_01",
            "name": "RF Zoom Out Blur",
            "category": "transitions",
            "motion": "zoom_out",
            "confidence": 0.85,
            "tags": ["zoom out", "scale down"],
        },
        {
            "id": "fade_in_01",
            "name": "RF Fade In Dissolve",
            "category": "transitions",
            "motion": "fade_in",
            "confidence": 0.9,
            "tags": ["fade in", "mờ dần", "hiện dần"],
        },
        {
            "id": "fade_out_01",
            "name": "RF Fade Out Dark",
            "category": "transitions",
            "motion": "fade_out",
            "confidence": 0.9,
            "tags": ["fade out", "tối dần", "biến mất"],
        },
        {
            "id": "text_slide_up",
            "name": "ChunDVC Slide Up Title",
            "category": "titles",
            "motion": "slide_up",
            "confidence": 0.9,
            "tags": ["slide up", "title"],
        },
    ]


def test_search_alias_shake(sample_assets, tmp_path):
    frecency_file = str(tmp_path / "frecency.json")
    idx = SearchIndex(sample_assets, frecency_path=frecency_file)

    results = idx.search("rung giat")
    assert len(results) > 0
    top_hit = results[0]["asset"]
    assert top_hit["motion"] == "shake"
    assert "rung" in results[0]["why"].lower() or "shake" in top_hit["name"].lower()


def test_search_short_word_up_no_false_positive(sample_assets, tmp_path):
    frecency_file = str(tmp_path / "frecency.json")
    idx = SearchIndex(sample_assets, frecency_path=frecency_file)

    results = idx.search("up")
    # "up" should match "RF Zoom Out Blur"? NO!
    # "up" should match "RF Camera Shake"? NO!
    for r in results:
        asset_name = r["asset"]["name"]
        assert asset_name != "RF Zoom Out Blur", "Short word 'up' should not match 'zoom_out'"
        assert asset_name != "RF Camera Shake", "Short word 'up' should not match 'shake'"

    # But it CAN match "ChunDVC Slide Up Title" because "up" is a token
    assert any(r["asset"]["id"] == "text_slide_up" for r in results)


def test_search_mo_dan_only_fade_in(sample_assets, tmp_path):
    frecency_file = str(tmp_path / "frecency.json")
    idx = SearchIndex(sample_assets, frecency_path=frecency_file)

    results = idx.search("mo dan")
    matched_ids = [r["asset"]["id"] for r in results]
    assert "fade_in_01" in matched_ids, "'mo dan' must match fade_in"
    assert "fade_out_01" not in matched_ids, "'mo dan' must NOT match fade_out"


def test_search_prefix_sfx_filter(sample_assets, tmp_path):
    frecency_file = str(tmp_path / "frecency.json")
    idx = SearchIndex(sample_assets, frecency_path=frecency_file)

    # Search with prefix 's: rung' -> Only SFX category
    results = idx.search("s: rung")
    assert len(results) > 0
    for r in results:
        assert r["asset"]["category"] in ("sfx", "audio", "sound")
    assert results[0]["asset"]["id"] == "sfx_shake"


def test_search_frecency_and_page_boost(sample_assets, tmp_path):
    frecency_file = str(tmp_path / "frecency.json")
    idx = SearchIndex(sample_assets, frecency_path=frecency_file)

    # Record use for zoom_out
    idx.record_use("zoom_out_01")
    idx.record_use("zoom_out_01")
    idx.record_use("zoom_out_01")

    # Set page boost for titles
    idx.set_page_boost(["titles"])

    # Search empty -> Should rank by boost & frecency
    ranked = idx.search("")
    assert len(ranked) == len(sample_assets)
    # text_slide_up has +25 boost, zoom_out_01 has +30 frecency
    top_ids = [r["asset"]["id"] for r in ranked[:2]]
    assert "zoom_out_01" in top_ids or "text_slide_up" in top_ids


def test_search_real_600_assets_performance(tmp_path):
    """Test performance on 600+ real assets from AssetIndexer, checking <16ms."""
    from src.core.asset_indexer import AssetIndexer

    indexer = AssetIndexer()
    all_dict = indexer.scan_all_assets()
    flattened = []
    for cat, items in all_dict.items():
        for it in items:
            it["category"] = cat
            flattened.append(it)

    frecency_file = str(tmp_path / "frecency.json")
    idx = SearchIndex(flattened, frecency_path=frecency_file)

    test_queries = ["rung giat", "whoosh", "s: ding", "c: neon", "mo dan", "pop", "tiktok", "vlog", "zoom"]
    durations = []

    for q in test_queries:
        t0 = time.perf_counter()
        results = idx.search(q)
        t1 = time.perf_counter()
        dur_ms = (t1 - t0) * 1000.0
        durations.append(dur_ms)
        assert dur_ms < 16.0, f"Query '{q}' took {dur_ms:.2f}ms, which is >= 16ms!"

    avg_ms = sum(durations) / len(durations)
    print(f"\n[BENCHMARK] Indexed {len(flattened)} real assets. Avg search latency: {avg_ms:.3f}ms")
    assert avg_ms < 16.0


def test_search_limit_default_positive_and_unlimited(tmp_path):
    """G04-04B1-I: Kiểm tra tham số limit: mặc định 50, dương N, và explicit 0/None trả toàn bộ matches > 100."""
    total_count = 125
    assets = [
        {
            "id": f"cinematic_shot_{i:03d}",
            "name": f"Cinematic Shot {i:03d}",
            "category": "broll",
            "tags": ["cinema", "film"],
        }
        for i in range(total_count)
    ]
    frecency_file = str(tmp_path / "frecency_limit.json")
    idx = SearchIndex(assets, frecency_path=frecency_file)

    # 1. Mặc định (không truyền limit): trả về đúng 50 kết quả
    default_results = idx.search("Cinematic")
    assert len(default_results) == 50, f"Kỳ vọng 50, nhận: {len(default_results)}"

    # 2. Limit dương N (ví dụ limit=20): trả về đúng 20 kết quả, thứ tự khớp 20 phần tử đầu của default
    limit_20 = idx.search("Cinematic", limit=20)
    assert len(limit_20) == 20
    assert [r["asset"]["id"] for r in limit_20] == [r["asset"]["id"] for r in default_results[:20]]

    # 3. Limit explicit 0: trả về toàn bộ 125 kết quả không bị cắt
    unlimited_0 = idx.search("Cinematic", limit=0)
    assert len(unlimited_0) == total_count, f"Kỳ vọng {total_count}, nhận: {len(unlimited_0)}"
    # 50 phần tử đầu của unlimited_0 phải trùng khớp 100% với default_results
    assert [r["asset"]["id"] for r in unlimited_0[:50]] == [r["asset"]["id"] for r in default_results]

    # 4. Limit None: cũng trả về toàn bộ 125 kết quả
    unlimited_none = idx.search("Cinematic", limit=None)
    assert len(unlimited_none) == total_count
    assert [r["asset"]["id"] for r in unlimited_none] == [r["asset"]["id"] for r in unlimited_0]

    # 5. G04-04B1-R1: Limit âm (-1, -2) bảo toàn ngữ nghĩa Python slicing results[:limit]
    neg_1 = idx.search("Cinematic", limit=-1)
    assert len(neg_1) == total_count - 1
    assert [r["asset"]["id"] for r in neg_1] == [r["asset"]["id"] for r in unlimited_0[:-1]]

    neg_2 = idx.search("Cinematic", limit=-2)
    assert len(neg_2) == total_count - 2
    assert [r["asset"]["id"] for r in neg_2] == [r["asset"]["id"] for r in unlimited_0[:-2]]

    # 6. Phối hợp với limit_categories và prefix filter khi limit=0
    mixed_assets = [
        {"id": "broll_01", "name": "Aerial Shot 01", "category": "broll"},
        {"id": "sfx_01", "name": "Aerial Sound 01", "category": "sfx"},
    ]
    idx_mixed = SearchIndex(mixed_assets, frecency_path=str(tmp_path / "frecency_mixed.json"))
    res_broll = idx_mixed.search("Aerial", limit_categories=["broll"], limit=0)
    assert len(res_broll) == 1
    assert res_broll[0]["asset"]["category"] == "broll"

