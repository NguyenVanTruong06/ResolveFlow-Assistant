"""
"1-Click Download Starter Pack".

Reads assets/starter_pack_manifest.json, downloads every allowed-license entry,
extracts zip packs safely, pins sha256 on first download (trust-on-first-use),
and refuses a file whose hash later changes. Pure standard library.

    python -m resolveflow.starter_pack --dest ~/ResolveFlow/StarterPack
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Dict, Iterable, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_MANIFEST = os.path.join(PROJECT_ROOT, "assets", "starter_pack_manifest.json")
UA = "ResolveFlow-Assistant/1.0 (+starter-pack downloader)"
MAX_BYTES = 200 * 1024 * 1024
AUDIO_EXT = {".wav", ".ogg", ".mp3", ".flac", ".m4a", ".aif", ".aiff"}

Progress = Callable[[str, int, int], None]   # (entry_id, bytes_done, bytes_total)


def load_manifest(path: str = DEFAULT_MANIFEST) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _lock_path(manifest_path: str) -> str:
    return os.path.join(os.path.dirname(manifest_path), "starter_pack.lock.json")


def _download(url: str, dst: str, entry_id: str, progress: Optional[Progress], retries: int = 3) -> str:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                total = int(r.headers.get("Content-Length") or 0)
                if total > MAX_BYTES:
                    raise ValueError("%s is larger than %d MB" % (url, MAX_BYTES // 2 ** 20))
                ctype = (r.headers.get("Content-Type") or "").lower()
                if "text/html" in ctype:
                    raise ValueError("%s returned an HTML page, not a file (moved or login wall?)" % url)
                h = hashlib.sha256()
                done = 0
                with open(dst + ".part", "wb") as fh:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        done += len(chunk)
                        if done > MAX_BYTES:
                            raise ValueError("download exceeded size limit")
                        h.update(chunk)
                        fh.write(chunk)
                        if progress:
                            progress(entry_id, done, total)
            os.replace(dst + ".part", dst)
            return h.hexdigest()
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
            if isinstance(e, ValueError):
                break
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("Download failed for %s: %s" % (url, last))


def _safe_extract(zpath: str, dest: str, patterns: Iterable[str]) -> List[str]:
    out = []
    pats = list(patterns) or ["*"]
    root = os.path.realpath(dest)
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            base = os.path.basename(name)
            if base.startswith(".") or "__MACOSX" in name:
                continue
            if os.path.splitext(base)[1].lower() not in AUDIO_EXT:
                continue
            if not any(fnmatch.fnmatch(base.lower(), p.lower()) for p in pats):
                continue
            target = os.path.realpath(os.path.join(dest, name))
            if not target.startswith(root + os.sep):
                continue                                    # zip-slip guard
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(target)
    return out


def download_starter_pack(dest: str, manifest_path: str = DEFAULT_MANIFEST,
                          categories: Iterable[str] = ("sfx", "bgm"),
                          progress: Optional[Progress] = None) -> Dict[str, dict]:
    m = load_manifest(manifest_path)
    allowed = set(m["policy"]["allowed_licenses"])
    lock_file = _lock_path(manifest_path)
    lock = json.load(open(lock_file, encoding="utf-8")) if os.path.exists(lock_file) else {}
    report: Dict[str, dict] = {}
    for cat in categories:
        for e in m.get(cat, []):
            if e.get("type") not in ("file", "zip"):
                continue
            if e.get("license") not in allowed:
                report[e["id"]] = {"status": "skipped", "reason": "license %s not allowed" % e.get("license")}
                continue
            folder = os.path.join(dest, cat.upper(), e["id"])
            os.makedirs(folder, exist_ok=True)
            fname = os.path.basename(e["url"].split("?")[0]) or e["id"]
            tmpdir = tempfile.mkdtemp(prefix="rf_dl_")
            try:
                tmp = os.path.join(tmpdir, fname)
                digest = _download(e["url"], tmp, e["id"], progress)
                pinned = lock.get(e["url"])
                if pinned and pinned != digest:
                    report[e["id"]] = {"status": "rejected", "reason": "sha256 changed since first download"}
                    continue
                lock[e["url"]] = digest
                if e["type"] == "zip":
                    files = _safe_extract(tmp, folder, e.get("pick", ["*"]))
                else:
                    target = os.path.join(folder, fname)
                    shutil.move(tmp, target)
                    files = [target]
                with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as fh:
                    fh.write("%s\nAuthor: %s\nLicense: %s\nSource: %s\n" % (
                        e["label"], e.get("author", ""), e["license"], e.get("source_page", e["url"])))
                report[e["id"]] = {"status": "ok", "files": files, "sha256": digest}
            except Exception as ex:  # keep going: one dead link must not kill the pack
                report[e["id"]] = {"status": "error", "reason": str(ex)}
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
    with open(lock_file, "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=2)
    return report


def import_into_resolve(report: Dict[str, dict], bin_name: str = "ResolveFlow Starter Pack") -> int:
    """Optional: import every downloaded file into a Media Pool bin."""
    from .resolve_bridge import _find_or_create_bin, context
    ctx = context(require_timeline=False)
    b = _find_or_create_bin(ctx.media_pool, bin_name)
    ctx.media_pool.SetCurrentFolder(b)
    files = [f for r in report.values() if r.get("status") == "ok" for f in r["files"]]
    return len(ctx.media_pool.ImportMedia(files) or []) if files else 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=os.path.expanduser("~/ResolveFlow/StarterPack"))
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--import-to-resolve", action="store_true")
    a = ap.parse_args()

    def bar(i, d, t):
        sys.stdout.write("\r%-18s %6.1f%%" % (i, 100.0 * d / t if t else 0))
        sys.stdout.flush()

    rep = download_starter_pack(a.dest, a.manifest, progress=bar)
    print()
    for k, v in rep.items():
        print("%-18s %s %s" % (k, v["status"], v.get("reason", "")))
    if a.import_to_resolve:
        print("Imported", import_into_resolve(rep), "files into Resolve")
