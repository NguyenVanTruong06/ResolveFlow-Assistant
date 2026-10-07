import os
from pathlib import Path

class AssetIndexer:
    def __init__(self, base_dir: str = "assets"):
        self.base_dir = Path(base_dir)
        self.sfx_dir = self.base_dir / "sfx"
        self.luts_dir = self.base_dir / "luts"
        self.memes_dir = self.base_dir / "broll_memes" / "memes"
        self.titles_dir = self.base_dir / "templates" / "titles"
        self.transitions_dir = self.base_dir / "templates" / "transitions"
        self.overlays_dir = self.base_dir / "broll_memes" / "overlays"
        
        self.ensure_dirs()

    def ensure_dirs(self):
        self.sfx_dir.mkdir(parents=True, exist_ok=True)
        self.luts_dir.mkdir(parents=True, exist_ok=True)
        self.memes_dir.mkdir(parents=True, exist_ok=True)
        self.titles_dir.mkdir(parents=True, exist_ok=True)
        self.transitions_dir.mkdir(parents=True, exist_ok=True)
        self.overlays_dir.mkdir(parents=True, exist_ok=True)

    def _generate_id(self, path: Path) -> str:
        # Use a deterministic hash for stable IDs
        import hashlib
        return hashlib.md5(str(path).encode()).hexdigest()[:8]

    def _find_thumbnail(self, path: Path) -> str | None:
        png_path = path.with_suffix('.png')
        if png_path.exists():
            return str(png_path)
        jpg_path = path.with_suffix('.jpg')
        if jpg_path.exists():
            return str(jpg_path)
            
        # If it's a video file, try extracting first frame using ffmpeg
        if path.suffix.lower() in ('.mp4', '.mov', '.webm', '.mkv'):
            import shutil, subprocess
            ffmpeg = shutil.which('ffmpeg')
            if ffmpeg:
                try:
                    cmd = [ffmpeg, '-y', '-ss', '00:00:00.5', '-i', str(path), '-vframes', '1', '-vf', 'scale=320:-1', str(png_path)]
                    res = subprocess.run(cmd, capture_output=True, timeout=5)
                    if res.returncode == 0 and png_path.exists():
                        return str(png_path)
                except Exception:
                    pass
        return None

    def _scan_dir(self, directory: Path, extensions: list[str], find_thumb: bool = False) -> list[dict]:
        results = []
        if not directory.exists():
            return results
            
        target_exts = [ext.lower() for ext in extensions]
        
        for file_path in directory.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() in target_exts:
                item = {
                    "id": self._generate_id(file_path),
                    "name": file_path.stem,
                    "file_path": str(file_path),
                    "thumbnail_path": None
                }
                if find_thumb:
                    item["thumbnail_path"] = self._find_thumbnail(file_path)
                results.append(item)
        return results

    def scan_sfx(self) -> list[dict]:
        return self._scan_dir(self.sfx_dir, ['.wav'])

    def scan_luts(self) -> list[dict]:
        return self._scan_dir(self.luts_dir, ['.cube'])

    def scan_memes(self) -> list[dict]:
        return self._scan_dir(self.memes_dir, ['.mp4', '.mov'], find_thumb=True)

    def scan_overlays(self) -> list[dict]:
        return self._scan_dir(self.overlays_dir, ['.mp4', '.mov', '.png'], find_thumb=True)

    def scan_titles(self) -> list[dict]:
        return self._scan_dir(self.titles_dir, ['.setting'], find_thumb=True)

    def scan_transitions(self) -> list[dict]:
        return self._scan_dir(self.transitions_dir, ['.setting'], find_thumb=True)
