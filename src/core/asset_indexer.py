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
        
        self.ensure_dirs()

    def ensure_dirs(self):
        self.sfx_dir.mkdir(parents=True, exist_ok=True)
        self.luts_dir.mkdir(parents=True, exist_ok=True)
        self.memes_dir.mkdir(parents=True, exist_ok=True)
        self.titles_dir.mkdir(parents=True, exist_ok=True)
        self.transitions_dir.mkdir(parents=True, exist_ok=True)

    def _generate_id(self, path: Path) -> str:
        # Use string hashing of the relative/absolute path for uniqueness
        return str(abs(hash(str(path))))

    def _find_thumbnail(self, path: Path) -> str | None:
        png_path = path.with_suffix('.png')
        if png_path.exists():
            return str(png_path)
        jpg_path = path.with_suffix('.jpg')
        if jpg_path.exists():
            return str(jpg_path)
        return None

    def _scan_dir(self, directory: Path, extensions: list[str], find_thumb: bool = False) -> list[dict]:
        results = []
        if not directory.exists():
            return results
            
        for ext in extensions:
            # case sensitive based on OS, but we can just use glob
            # rglob handles this somewhat, but on Linux it is case sensitive.
            # Using basic extension matching as requested.
            for file_path in directory.rglob(f"*{ext}"):
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
        return self._scan_dir(self.memes_dir, ['.mp4'])

    def scan_titles(self) -> list[dict]:
        return self._scan_dir(self.titles_dir, ['.setting'], find_thumb=True)

    def scan_transitions(self) -> list[dict]:
        return self._scan_dir(self.transitions_dir, ['.setting'], find_thumb=True)
