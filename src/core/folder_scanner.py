"""
Folder Scanner & Hierarchical Project Structure Engine.
Quét đệ quy và phân loại thư mục dự án đa tầng (Chapters, A-Roll, B-Roll, Footages).
Tự động định hình Cấu trúc Kịch bản / Hồi truyện và nạp Kho Cảnh Chèn (B-Roll Pool).
"""

import os
import re
from typing import List, Dict, Any, Optional, Tuple, Union
from pydantic import BaseModel, Field


VALID_MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".flv", ".wmv",
    ".wav", ".mp3", ".m4a", ".aac", ".flac"
}

JUNK_FILE_PATTERNS = {
    ".ds_store", "thumbs.db", "desktop.ini", ".git", ".idea", ".vscode"
}

BROLL_KEYWORDS = [
    "broll", "b_roll", "b-roll", "footage", "canh_quay", "canhquay", "insert",
    "cutaway", "overlay", "minhhoa", "minh_hoa", "canh_chen", "canhchen"
]

INTRO_KEYWORDS = [
    "intro", "modau", "mo_dau", "hook", "01", "part1", "part_1",
    "phan1", "phan_1", "act1", "act_1", "mo_bai", "mobai"
]

OUTRO_KEYWORDS = [
    "outro", "ketluan", "ket_luan", "ketthuc", "ket_thuc", "chot",
    "cta", "end", "final", "ket_bai", "ketbai"
]

CLIMAX_KEYWORDS = [
    "climax", "caotrao", "cao_trao", "dinhdiem", "dinh_diem", "highlight"
]

BUILD_KEYWORDS = [
    "body", "thanbai", "than_bai", "dienbien", "dien_bien", "trainghiem",
    "trai_nghiem", "khampha", "kham_pha", "review", "detail", "chitiet", "chi_tiet"
]


def natural_sort_key(s: str) -> List[Union[int, str]]:
    """Sắp xếp chuỗi tự nhiên theo số (ví dụ: part1 < part2 < part10)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def is_valid_media_file(filename: str) -> bool:
    """Kiểm tra file có phải là định dạng media hợp lệ và không phải file rác."""
    base = os.path.basename(filename).lower()
    if base.startswith(".") or base in JUNK_FILE_PATTERNS:
        return False
    ext = os.path.splitext(base)[1].lower()
    return ext in VALID_MEDIA_EXTENSIONS


def detect_role_hint(folder_name: str) -> str:
    """Nhận diện vai trò kịch bản tiềm năng dựa vào tên thư mục."""
    low = folder_name.lower().replace(" ", "_").replace("-", "_")
    compact = re.sub(r'[\s_\-]+', '', folder_name).lower()
    
    # 1. B-Roll (Ưu tiên kiểm tra trước)
    for kw in BROLL_KEYWORDS:
        if kw in low or kw.replace("_", "").replace("-", "") in compact:
            return "broll"
            
    # 2. Outro / Chốt
    for kw in OUTRO_KEYWORDS:
        if kw in low or kw.replace("_", "").replace("-", "") in compact:
            return "outro"
            
    # 3. Intro / Mở đầu
    for kw in INTRO_KEYWORDS:
        if kw in low or kw.replace("_", "").replace("-", "") in compact:
            return "intro"
            
    # 4. Climax / Cao trào
    for kw in CLIMAX_KEYWORDS:
        if kw in low or kw.replace("_", "").replace("-", "") in compact:
            return "climax"
            
    # 5. Build / Thân bài
    for kw in BUILD_KEYWORDS:
        if kw in low or kw.replace("_", "").replace("-", "") in compact:
            return "build"
            
    return "build"


class FolderGroup(BaseModel):
    """Một nhóm / chương thư mục con chứa các file media."""
    name: str
    rel_path: str
    abs_path: str
    video_paths: List[str] = Field(default_factory=list)
    is_broll: bool = False
    chapter_order: int = 0
    role_hint: str = "build"


class ProjectFolderStructure(BaseModel):
    """Cấu trúc toàn bộ dự án phân cấp theo cây thư mục."""
    root_path: str
    root_name: str
    groups: List[FolderGroup] = Field(default_factory=list)
    all_video_paths: List[str] = Field(default_factory=list)
    main_video_paths: List[str] = Field(default_factory=list)
    broll_video_paths: List[str] = Field(default_factory=list)
    total_files: int = 0

    def summary_tree(self) -> str:
        """Sinh chuỗi biểu diễn trực quan dạng cây thư mục dự án."""
        lines = [
            f"📁 Dự án: {self.root_name} ({len(self.groups)} nhóm • {self.total_files} files)",
            f"   Đường dẫn gốc: {self.root_path}"
        ]
        for i, grp in enumerate(self.groups):
            is_last = (i == len(self.groups) - 1)
            prefix = "   └─" if is_last else "   ├─"
            role_desc = "🎥 B-Roll / Footage chèn Track 2" if grp.is_broll else f"Mạch chính [{grp.role_hint.upper()}]"
            lines.append(f"{prefix} 📂 [{grp.name}] ({len(grp.video_paths)} video) ➔ {role_desc}")
            for j, vpath in enumerate(grp.video_paths):
                sub_prefix = "       " if is_last else "   │   "
                v_is_last = (j == len(grp.video_paths) - 1)
                item_sym = "└─" if v_is_last else "├─"
                lines.append(f"{sub_prefix}{item_sym} 🎬 {os.path.basename(vpath)}")
        return "\n".join(lines)


def scan_project_folder(root_dir: str) -> ProjectFolderStructure:
    """
    Quét đệ quy thư mục dự án và phân nhóm theo từng thư mục con (Chương / Scene / B-Roll).
    """
    abs_root = os.path.abspath(root_dir)
    root_name = os.path.basename(abs_root) or abs_root

    # Kiểm tra các mục con trực tiếp (bỏ qua các thư mục hệ thống, asset, timeline bắt đầu bằng '.' hoặc '_')
    SYSTEM_IGNORE_DIRS = {"assets", "_assets", "sfx", "_sfx", "luts", "_luts", "presets", "_presets", "memes", "_memes", "_archive_xml", "_timeline_import"}
    entries = sorted(os.listdir(abs_root), key=natural_sort_key)
    subdirs = [
        e for e in entries 
        if os.path.isdir(os.path.join(abs_root, e)) 
        and not e.startswith((".", "_")) 
        and e.lower() not in SYSTEM_IGNORE_DIRS
    ]
    direct_files = [
        os.path.join(abs_root, e) for e in entries 
        if os.path.isfile(os.path.join(abs_root, e)) and is_valid_media_file(e)
    ]

    groups: List[FolderGroup] = []
    order_idx = 0

    # 1. Nếu có file nằm ngay ở root directory, gom thành nhóm Root / General
    if direct_files:
        role = detect_role_hint(root_name)
        is_broll = (role == "broll")
        groups.append(FolderGroup(
            name=root_name if not subdirs else "00_Root",
            rel_path=".",
            abs_path=abs_root,
            video_paths=direct_files,
            is_broll=is_broll,
            chapter_order=order_idx,
            role_hint=role
        ))
        order_idx += 1

    # 2. Duyệt qua từng thư mục con (Sub-chapters)
    for sdir in subdirs:
        sdir_path = os.path.join(abs_root, sdir)
        collected_files: List[str] = []
        
        # Quét đệ quy bên trong thư mục con này
        for cur_root, _, files in os.walk(sdir_path):
            sorted_files = sorted(files, key=natural_sort_key)
            for f in sorted_files:
                if is_valid_media_file(f):
                    full_f = os.path.join(cur_root, f)
                    try:
                        if os.path.getsize(full_f) > 0:
                            collected_files.append(full_f)
                    except OSError:
                        pass

        if collected_files:
            rel = os.path.relpath(sdir_path, abs_root)
            role = detect_role_hint(sdir)
            is_broll = (role == "broll")
            groups.append(FolderGroup(
                name=sdir,
                rel_path=rel,
                abs_path=sdir_path,
                video_paths=collected_files,
                is_broll=is_broll,
                chapter_order=order_idx,
                role_hint=role
            ))
            order_idx += 1

    # Phân loại All / Main / B-Roll
    all_videos: List[str] = []
    main_videos: List[str] = []
    broll_videos: List[str] = []

    for g in groups:
        for vp in g.video_paths:
            all_videos.append(vp)
            if g.is_broll:
                broll_videos.append(vp)
            else:
                main_videos.append(vp)

    return ProjectFolderStructure(
        root_path=abs_root,
        root_name=root_name,
        groups=groups,
        all_video_paths=all_videos,
        main_video_paths=main_videos if main_videos else all_videos,
        broll_video_paths=broll_videos,
        total_files=len(all_videos)
    )


def collect_video_paths(inputs: List[str]) -> Tuple[List[str], Optional[ProjectFolderStructure]]:
    """
    Xử lý danh sách đầu vào hỗn hợp (có thể là file hoặc thư mục hoặc cả hai).
    Trả về:
    - Danh sách video paths phẳng đã sắp xếp tự nhiên
    - Đối tượng ProjectFolderStructure nếu có thư mục dự án
    """
    if not inputs:
        return [], None

    # Nếu chỉ có 1 thư mục được chọn
    if len(inputs) == 1 and os.path.isdir(inputs[0]):
        proj = scan_project_folder(inputs[0])
        return proj.all_video_paths, proj

    # Nếu có nhiều đường dẫn hoặc file rời
    all_videos: List[str] = []
    has_dir = False
    
    for inp in inputs:
        inp_abs = os.path.abspath(inp)
        if os.path.isdir(inp_abs):
            has_dir = True
            sub_proj = scan_project_folder(inp_abs)
            all_videos.extend(sub_proj.all_video_paths)
        elif os.path.isfile(inp_abs) and is_valid_media_file(inp_abs):
            all_videos.append(inp_abs)

    # Loại bỏ trùng lặp giữ nguyên thứ tự
    unique_videos = list(dict.fromkeys(all_videos))

    if has_dir and len(inputs) == 1:
        proj = scan_project_folder(inputs[0])
        return unique_videos, proj

    return unique_videos, None
