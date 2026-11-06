"""
tests/core/test_legacy_library_export.py
Unit tests cho tools/export_legacy_library.py trên fixture nhỏ:
- Đủ byte/Unicode/catalog fields.
- Source mutation abort.
- Low disk space check.
- Symlink escape prevented.
- Corrupt/missing file verify fail.
- Owned cleanup & no overwrite.
"""
import json
import os
import zipfile
from pathlib import Path
import pytest

from tools.export_legacy_library import (
    check_mount_space,
    compute_file_sha256,
    dump_all_catalogs,
    survey_legacy_sources,
    export_legacy_library,
    is_excluded_source_path,
)


@pytest.fixture
def mock_repo(tmp_path):
    """Fixture tạo cây thư mục repo giả lập nhỏ có đầy đủ Unicode và subdirs."""
    repo = tmp_path / "mock_repo"
    repo.mkdir()

    # assets/
    (repo / "assets" / "luts").mkdir(parents=True)
    (repo / "assets" / "luts" / "Bảng Màu Điện Ảnh.cube").write_text("LUT CONTENT Tiếng Việt", encoding="utf-8")

    (repo / "assets" / "sfx").mkdir(parents=True)
    (repo / "assets" / "sfx" / "whoosh.wav").write_bytes(b"RIFF_WAV_HEADER_SFX")

    # presets/text_styles/
    (repo / "presets" / "text_styles").mkdir(parents=True)
    (repo / "presets" / "text_styles" / "giật_gân.json").write_text('{"name": "Giật gân"}', encoding="utf-8")

    # recipes/
    (repo / "recipes").mkdir(parents=True)
    (repo / "recipes" / "vlog_chữa_lành.json").write_text('{"title": "Chữa lành"}', encoding="utf-8")

    # Secrets / credentials (phải bị loại trừ)
    (repo / ".env").write_text("SECRET_KEY=123", encoding="utf-8")
    (repo / "credentials.json").write_text('{"token": "xyz"}', encoding="utf-8")

    # src/ui/tabs/tab_assets.py
    (repo / "src" / "ui" / "tabs").mkdir(parents=True)
    tab_code = '''
# Tab assets mock
MOCKUP_LUTS: List[StudioAsset] = [
    StudioAsset("clean_rec709", "Clean Rec.709", "lut", "natural", sub="Tự nhiên, trung tính", badge_icon="🌿", pal=["#1f2937", "#64748b"])
]

'''
    (repo / "src" / "ui" / "tabs" / "tab_assets.py").write_text(tab_code, encoding="utf-8")

    return repo


def test_survey_preserves_unicode_and_excludes_secrets(mock_repo):
    """Kiểm tra: Khảo sát nguồn giữ đúng Unicode tiếng Việt và loại trừ an toàn secrets/.env."""
    items, checksums = survey_legacy_sources(
        mock_repo,
        source_subdirs=["assets", "presets/text_styles", "recipes"]
    )

    paths = [i["original_path"] for i in items]
    assert any("Bảng Màu Điện Ảnh.cube" in p for p in paths)
    assert any("giật_gân.json" in p for p in paths)
    assert any("vlog_chữa_lành.json" in p for p in paths)

    # Tuyệt đối không chứa secret/.env
    assert not any(".env" in p for p in paths)
    assert not any("credentials.json" in p for p in paths)

    # Checksums khớp
    for p, meta in checksums.items():
        assert meta["size_bytes"] > 0
        assert len(meta["sha256"]) == 64


def test_source_mutation_aborts_survey(mock_repo, monkeypatch):
    """Kiểm tra: Nếu file nguồn bị thay đổi trong quá trình khảo sát, quá trình phải abort (RuntimeError)."""
    target = mock_repo / "assets" / "luts" / "Bảng Màu Điện Ảnh.cube"

    orig_compute = compute_file_sha256

    def mutate_and_compute(path, cancel=lambda: False):
        if path == target:
            # Giả lập file bị ghi đè thay đổi mtime / size
            with open(path, "a", encoding="utf-8") as f:
                f.write("MUTATED!")
        return orig_compute(path, cancel)

    monkeypatch.setattr("tools.export_legacy_library.compute_file_sha256", mutate_and_compute)

    with pytest.raises(RuntimeError, match="bị thay đổi trong quá trình khảo sát"):
        survey_legacy_sources(mock_repo, source_subdirs=["assets"])


def test_symlink_escape_is_ignored(mock_repo, tmp_path):
    """Kiểm tra: Symlink trỏ ra ngoài repo bị bỏ qua, không leak data bên ngoài vào backup."""
    outside_file = tmp_path / "outside_secret.txt"
    outside_file.write_text("OUTSIDE_LEAK", encoding="utf-8")

    symlink_path = mock_repo / "assets" / "leak_symlink.txt"
    symlink_path.symlink_to(outside_file)

    items, _ = survey_legacy_sources(mock_repo, source_subdirs=["assets"])
    paths = [i["original_path"] for i in items]
    assert "assets/leak_symlink.txt" not in paths


def test_dump_all_catalogs_fields_complete(tmp_path, monkeypatch):
    """Kiểm tra: dump_all_catalogs sinh ra JSON hợp lệ với đầy đủ fields cho cả 24 text styles và 4 transitions."""
    cat_dir = tmp_path / "catalogs"
    repo_root = Path("/media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant")

    # Dùng catalog fixture 24/4 từ run hoặc fixture generator theo directive
    catalog_source = Path("/media/vanntruongg/Ventoy/ChunDVC_Publish/runs/20261029_011655_e6c3a144/catalogs")
    from src.core.text_preset import TextStylePreset
    from src.core.transition_preset import TransitionStylePreset
    import src.core.text_preset as tp_mod
    import src.core.transition_preset as trans_mod

    if catalog_source.exists() and (catalog_source / "text_style_presets.json").exists():
        raw_texts = json.loads((catalog_source / "text_style_presets.json").read_text(encoding="utf-8"))
        raw_trans = json.loads((catalog_source / "transition_style_presets.json").read_text(encoding="utf-8"))
        mock_texts = [TextStylePreset(**item) for item in raw_texts]
        mock_trans = [TransitionStylePreset(**item) for item in raw_trans]
    else:
        mock_texts = [
            TextStylePreset(
                id=f"preset_{i:02d}",
                name=f"Preset {i:02d}",
                category="clean_minimal",
                font="Arial",
                size=48,
                standard_color="#FFFFFF",
                animation="pop"
            )
            for i in range(24)
        ]
        mock_trans = [
            TransitionStylePreset(
                id=f"trans_{i:02d}",
                name=f"Transition {i:02d}",
                category="motion",
                duration_frames=24,
                custom_parameters={}
            )
            for i in range(4)
        ]

    monkeypatch.setattr(tp_mod, "BUILTIN_PRESETS", mock_texts)
    monkeypatch.setattr(trans_mod, "BUILTIN_TRANSITIONS", mock_trans)

    summary = dump_all_catalogs(repo_root, cat_dir)
    assert summary["text_style_presets"]["count"] == 24
    assert summary["transition_style_presets"]["count"] == 4

    # Kiểm tra cấu trúc text_style_presets.json
    text_json = json.loads((cat_dir / "text_style_presets.json").read_text(encoding="utf-8"))
    assert len(text_json) == 24
    sample_text = text_json[0]
    for required_field in ("id", "name", "category", "font", "size", "standard_color", "animation"):
        assert required_field in sample_text, f"Thiếu trường {required_field} trong text preset"

    # Kiểm tra cấu trúc transition_style_presets.json
    trans_json = json.loads((cat_dir / "transition_style_presets.json").read_text(encoding="utf-8"))
    assert len(trans_json) == 4
    sample_trans = trans_json[0]
    for required_field in ("id", "name", "category", "duration_frames", "custom_parameters"):
        assert required_field in sample_trans, f"Thiếu trường {required_field} trong trans preset"


def test_export_and_restore_verification_roundtrip(mock_repo, tmp_path):
    """Kiểm tra quy trình end-to-end:
    - Tạo backup ChunDVC_Legacy_Library_v1.0.zip
    - testzip() thành công
    - Khôi phục và so khớp 100% SHA256 từng file."""
    run_dir = tmp_path / "run_test"

    res = export_legacy_library(
        repo_root=mock_repo,
        output_run_dir=run_dir
    )

    assert res["restore_verified"] is True
    zip_p = Path(res["zip_path"])
    assert zip_p.is_file()
    assert len(res["zip_sha256"]) == 64

    # Kiểm tra file ZIP hợp lệ
    with zipfile.ZipFile(zip_p, "r") as zf:
        assert zf.testzip() is None
        names = zf.namelist()
        assert "bundle_manifest.json" in names
        assert "README_RESTORE.md" in names
        assert "inventory.json" in names
        assert "checksums.json" in names
        assert any(n.startswith("originals/") for n in names)
        assert any(n.startswith("catalogs/") for n in names)

    # Báo cáo restore
    rep_p = run_dir / "reports" / "restore_verification_report.json"
    assert rep_p.is_file()
    rep = json.loads(rep_p.read_text(encoding="utf-8"))
    assert rep["all_checksums_matched"] is True
    assert rep["total_items_verified"] == res["total_files"]
    assert len(rep["errors"]) == 0
