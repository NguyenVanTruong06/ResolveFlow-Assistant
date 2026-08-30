import os
import pytest
from src.core.recipe_manager import Recipe, RecipeManager

def test_recipe_defaults(tmp_path):
    mgr = RecipeManager(base_dir=str(tmp_path / "recipes"))
    recipes = mgr.list_recipes()
    assert len(recipes) >= 3
    
    ids = [r.id for r in recipes]
    assert "podcast_pro" in ids
    assert "tiktok_viral_reels" in ids
    assert "vlog_hook_speedramp" in ids

def test_save_load_delete_recipe(tmp_path):
    mgr = RecipeManager(base_dir=str(tmp_path / "recipes"))
    r = Recipe(
        id="custom_interview",
        name="Phỏng vấn chuyên sâu",
        workflow_mode="podcast",
        model_size="medium",
        language="Tiếng Việt",
        ai_mode="clean_talk",
        remove_bad_takes=True,
        enable_punch_in=True,
        enable_reframe=False,
        enable_broll=False,
        enable_sfx=False,
        enable_subtitles=True,
        text_preset_id="clean_outline",
        split_mode="characters",
        split_limit=38,
        run_cut=True,
        silence_db=-38.0,
        min_duration=0.4
    )
    
    saved_file = mgr.save_recipe(r)
    assert os.path.exists(saved_file)
    
    loaded = mgr.get_recipe("custom_interview")
    assert loaded is not None
    assert loaded.name == "Phỏng vấn chuyên sâu"
    assert loaded.model_size == "medium"
    
    assert mgr.delete_recipe("custom_interview") is True
    assert mgr.get_recipe("custom_interview") is None
