import pytest
from src.core.contextual_suggester import ContextualSuggester, SuggestedAsset
from src.ui.tabs.tab_assets import StudioAsset


@pytest.fixture
def mock_assets():
    return [
        # SFX
        StudioAsset(id="sfx_vine_boom", name="Vine Boom Bass", tab="sfx", category="giat_minh", sub="giat_minh", badge_icon="🔊"),
        StudioAsset(id="sfx_laugh", name="Funny Comedy Laugh", tab="sfx", category="hai_huoc", sub="hai_huoc", badge_icon="🔊"),
        StudioAsset(id="sfx_whoosh", name="Fast Whoosh Swish", tab="sfx", category="chuyen_canh", sub="chuyen_canh", badge_icon="🔊"),
        StudioAsset(id="sfx_ding", name="Bell Ding Pop", tab="sfx", category="diem_nhan", sub="diem_nhan", badge_icon="🔊"),
        StudioAsset(id="sfx_keyboard", name="Keyboard Typing Click", tab="sfx", category="cong_nghe", sub="cong_nghe", badge_icon="🔊"),
        
        # Memes
        StudioAsset(id="meme_do_mixi", name="Độ Mixi Ối Dồi Ôi", tab="meme", category="vietnam_viral", sub="vietnam_viral", badge_icon="🎭"),
        StudioAsset(id="meme_shock", name="Surprised Cat Shocked", tab="meme", category="reaction", sub="reaction", badge_icon="🎭"),
        
        # Titles
        StudioAsset(id="txt_mrbeast", name="MrBeast Impact Yellow", tab="text", category="giat_gan", sub="Tiêu đề", badge_icon="🔤"),
        StudioAsset(id="txt_comic", name="Comic Manga Burst", tab="text", category="hai_huoc", sub="Tiêu đề", badge_icon="🔤"),
        StudioAsset(id="txt_tech", name="Tech Review 4K", tab="text", category="cong_nghe", sub="Tiêu đề", badge_icon="🔤"),
        StudioAsset(id="txt_clean", name="Clean Minimal Pill", tab="text", category="minimal", sub="Tiêu đề", badge_icon="🔤"),

        # Transitions
        StudioAsset(id="trans_slide", name="Smooth Slide Left", tab="trans", category="chuyen_canh", sub="Chuyển cảnh", badge_icon="🎬"),

        # LUTs
        StudioAsset(id="lut_cyberpunk", name="Cyberpunk Neon Blue", tab="lut", category="cong_nghe", sub="Màu", badge_icon="🎨"),
    ]


def test_suggest_humor_keywords(mock_assets):
    suggester = ContextualSuggester(assets=mock_assets)
    results = suggester.suggest_for_text("Toang thật rồi ông giáo ạ, ảo thật đấy haha buồn cười vãi")
    
    assert len(results) > 0
    asset_ids = [r.asset.id for r in results]
    assert "sfx_laugh" in asset_ids or "meme_do_mixi" in asset_ids or "txt_comic" in asset_ids
    
    # Check reason tag
    humor_reasons = [r.reason for r in results if "Hài hước" in r.reason or "hai_huoc" in r.reason.lower()]
    assert len(humor_reasons) > 0


def test_suggest_shock_keywords(mock_assets):
    suggester = ContextualSuggester(assets=mock_assets)
    results = suggester.suggest_for_text("Thật không thể tin nổi một sai lầm cực sốc và kinh hoàng")
    
    assert len(results) > 0
    asset_ids = [r.asset.id for r in results]
    assert "sfx_vine_boom" in asset_ids or "meme_shock" in asset_ids or "txt_mrbeast" in asset_ids
    
    shock_reasons = [r.reason for r in results if "Giật mình" in r.reason or "Sốc" in r.reason or "giat_minh" in r.reason.lower()]
    assert len(shock_reasons) > 0


def test_suggest_transition_keywords(mock_assets):
    suggester = ContextualSuggester(assets=mock_assets)
    results = suggester.suggest_for_text("Tiếp theo chúng ta sẽ cùng bước sang phần 2 nhé")
    
    assert len(results) > 0
    asset_ids = [r.asset.id for r in results]
    assert "sfx_whoosh" in asset_ids or "trans_slide" in asset_ids


def test_suggest_tech_keywords(mock_assets):
    suggester = ContextualSuggester(assets=mock_assets)
    results = suggester.suggest_for_text("Cài đặt phần mềm AI và lập trình hệ thống máy tính")
    
    assert len(results) > 0
    asset_ids = [r.asset.id for r in results]
    assert "sfx_keyboard" in asset_ids or "txt_tech" in asset_ids or "lut_cyberpunk" in asset_ids


def test_suggest_shotbook_role_hook(mock_assets):
    suggester = ContextualSuggester(assets=mock_assets)
    # Neutral text but role is hook
    results = suggester.suggest_for_text("Video này dành cho bạn", role="hook")
    
    assert len(results) > 0
    asset_ids = [r.asset.id for r in results]
    # Hook should prioritize attention/ding or whoosh
    assert any(a in asset_ids for a in ["sfx_ding", "sfx_whoosh", "txt_mrbeast"])


def test_empty_or_neutral_text_fallback(mock_assets):
    suggester = ContextualSuggester(assets=mock_assets)
    results = suggester.suggest_for_text("")
    assert isinstance(results, list)
