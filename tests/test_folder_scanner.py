import os
import pytest
from src.core.folder_scanner import (
    natural_sort_key,
    is_valid_media_file,
    detect_role_hint,
    scan_project_folder,
    collect_video_paths,
    ProjectFolderStructure
)


def test_natural_sort_key():
    items = ["part10.mp4", "part1.mp4", "part2.mp4", "part20.mp4", "part3.mp4"]
    sorted_items = sorted(items, key=natural_sort_key)
    assert sorted_items == ["part1.mp4", "part2.mp4", "part3.mp4", "part10.mp4", "part20.mp4"]


def test_is_valid_media_file():
    assert is_valid_media_file("video.mp4") is True
    assert is_valid_media_file("clip.MOV") is True
    assert is_valid_media_file("audio.wav") is True
    assert is_valid_media_file(".DS_Store") is False
    assert is_valid_media_file("thumbs.db") is False
    assert is_valid_media_file("doc.txt") is False


def test_detect_role_hint():
    assert detect_role_hint("01_Intro") == "intro"
    assert detect_role_hint("01_Mo_Dau") == "intro"
    assert detect_role_hint("02_Trai_Nghiem_Thuc_Te") == "build"
    assert detect_role_hint("03_Cao_Trao") == "climax"
    assert detect_role_hint("04_Ket_Luan") == "outro"
    assert detect_role_hint("B-Roll_HaNoi") == "broll"
    assert detect_role_hint("Footage_Minh_Hoa") == "broll"


def test_scan_project_folder(tmp_path):
    # Tạo cấu trúc thư mục giả lập
    proj_dir = tmp_path / "My_Vlog_Project"
    proj_dir.mkdir()

    # Thư mục 01_Intro
    intro_dir = proj_dir / "01_Intro"
    intro_dir.mkdir()
    (intro_dir / "clip_1.mp4").write_text("dummy")
    (intro_dir / "clip_2.mp4").write_text("dummy")

    # Thư mục 02_TraiNghiem
    body_dir = proj_dir / "02_TraiNghiem"
    body_dir.mkdir()
    (body_dir / "scene_1.mov").write_text("dummy")
    (body_dir / "scene_2.mov").write_text("dummy")

    # Thư mục B-Roll
    broll_dir = proj_dir / "B-Roll_HaNoi"
    broll_dir.mkdir()
    (broll_dir / "broll_1.mp4").write_text("dummy")
    (broll_dir / ".DS_Store").write_text("junk")

    # Quét thư mục
    structure = scan_project_folder(str(proj_dir))
    assert isinstance(structure, ProjectFolderStructure)
    assert structure.total_files == 5
    assert len(structure.groups) == 3

    # Kiểm tra group B-Roll
    broll_group = next(g for g in structure.groups if g.is_broll)
    assert broll_group.name == "B-Roll_HaNoi"
    assert len(broll_group.video_paths) == 1
    assert len(structure.broll_video_paths) == 1

    # Kiểm tra group Intro
    intro_group = next(g for g in structure.groups if g.role_hint == "intro")
    assert len(intro_group.video_paths) == 2

    # Kiểm tra tree output
    tree = structure.summary_tree()
    assert "My_Vlog_Project" in tree
    assert "01_Intro" in tree
    assert "B-Roll" in tree


def test_collect_video_paths_with_mixed_input(tmp_path):
    f1 = tmp_path / "v1.mp4"
    f2 = tmp_path / "v2.mp4"
    f1.write_text("dummy")
    f2.write_text("dummy")

    folder = tmp_path / "SubFolder"
    folder.mkdir()
    f3 = folder / "v3.mp4"
    f3.write_text("dummy")

    videos, proj = collect_video_paths([str(f1), str(folder), str(f2)])
    assert len(videos) == 3
    assert str(f1) in videos
    assert str(f2) in videos
    assert str(f3) in videos
