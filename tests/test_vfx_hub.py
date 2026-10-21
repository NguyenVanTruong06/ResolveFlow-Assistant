"""Offline tests: template syntax/wiring, timecode math, carrier PNG, insert flow on a fake Resolve."""
import builtins
import glob
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import resolve_bridge as rb  # noqa: E402
from src.core.fusion import TITLES, build_library, export_title_setting, OVERLAYS  # noqa: E402
from src.core.fusion.validate import check_file, parse  # noqa: E402
from src.core import starter_pack as sp


def test_all_templates_parse_and_wire():
    d = tempfile.mkdtemp()
    written = build_library(d)
    assert len(written["Titles"]) == 8 and len(written["Transitions"]) == 6 and len(written["Generators"]) == 5
    for f in glob.glob(os.path.join(d, "*", "*.setting")):
        assert check_file(f) == [], check_file(f)


def test_params_are_baked_and_vietnamese_survives():
    d = tempfile.mkdtemp()
    for style in TITLES:
        f = export_title_setting(style, {"text": "Xin chào Việt Nam", "color": "#FF0000", "size": 0.11}, d)
        src = open(f, encoding="utf-8").read()
        assert check_file(f) == []
        if style not in ("quote_box",):
            assert "Xin chào Việt Nam" in src, style
        assert "0.11" in src or "0.0825" in src or "0.0605" in src, style


def test_published_inspector_controls():
    d = tempfile.mkdtemp()
    f = export_title_setting("spring_pop", None, d)
    root = parse(open(f, encoding="utf-8").read())
    keys = set(root["Tools"]["RF_Spring_Pop"]["Inputs"].keys())
    assert {"Text", "Font", "Style", "Size", "Red", "Green", "Blue", "Delay", "Speed", "Position"} <= keys


def test_timecode_roundtrip():
    for nominal, drop in ((24, False), (25, False), (30, True), (60, True), (30, False)):
        for fr in (0, 1, 1799, 1800, 17982, 107892, 86400 * 2 + 7):
            tc = rb.frames_to_tc(fr, nominal, drop)
            assert rb.tc_to_frames(tc, nominal, drop) == fr, (nominal, drop, fr, tc)
    assert rb.tc_to_frames("01:00:00:00", 24) == 86400
    assert rb.frames_to_tc(1800, 30, True) == "00:01:00;02"


def test_carrier_png_is_transparent():
    from PIL import Image
    data = rb._png_rgba_transparent(64, 36)
    p = os.path.join(tempfile.mkdtemp(), "c.png")
    open(p, "wb").write(data)
    im = Image.open(p)
    assert im.mode == "RGBA" and im.size == (64, 36) and im.getextrema()[3] == (0, 0)


# ---------------------------------------------------------------- fake Resolve
class FakeComp:
    def __init__(self, name): self.name = name


class FakeItem:
    def __init__(self, start, dur, track):
        self.start, self.dur, self.track = start, dur, track
        self.comps = ["Composition 1"]
        self.name = "carrier"
    def GetStart(self): return self.start
    def GetEnd(self): return self.start + self.dur
    def GetName(self): return self.name
    def SetName(self, n): self.name = n; return True
    def SetClipColor(self, c): return True
    def ImportFusionComp(self, path):
        assert os.path.exists(path) and check_file(path) == []
        self.comps.append(os.path.basename(path)); return FakeComp(path)
    def GetFusionCompNameList(self): return list(self.comps)
    def LoadFusionCompByName(self, n): self.active = n; return True
    def DeleteFusionCompByName(self, n): self.comps.remove(n); return True


class FakeTimeline:
    def __init__(self):
        self.tracks = {1: [FakeItem(86400, 2000, 1)], 2: [FakeItem(86500, 50, 2)]}
        self.locked = {}
    def GetSetting(self, k):
        return {"timelineFrameRate": "24", "timelineResolutionWidth": "320",
                "timelineResolutionHeight": "180", "timelineDropFrameTimecode": "0"}.get(k)
    def GetCurrentTimecode(self): return "01:00:05:00"     # 86520
    def GetTrackCount(self, t): return len(self.tracks)
    def AddTrack(self, t): self.tracks[len(self.tracks) + 1] = []; return True
    def GetItemListInTrack(self, t, i): return self.tracks[i]
    def GetIsTrackLocked(self, t, i): return self.locked.get(i, False)
    def DeleteClips(self, items, ripple=False): return True


class FakeClip:
    def __init__(self, frames): self.frames = frames
    def GetClipProperty(self, k):
        return {"Frames": str(self.frames), "File Path": "/x/rf_carrier_000000.png", "Resolution": "320x180"}.get(k, "")


class FakeFolder:
    def __init__(self, name): self.name, self.clips, self.subs = name, [], []
    def GetName(self): return self.name
    def GetSubFolderList(self): return self.subs
    def GetClipList(self): return self.clips


class FakePool:
    def __init__(self, tl):
        self.root, self.tl, self.cur = FakeFolder("Master"), tl, None
    def GetRootFolder(self): return self.root
    def AddSubFolder(self, parent, name):
        f = FakeFolder(name); parent.subs.append(f); return f
    def GetCurrentFolder(self): return self.cur
    def SetCurrentFolder(self, f): self.cur = f; return True
    def ImportMedia(self, infos):
        c = FakeClip(infos[0]["EndIndex"] + 1); self.cur.clips.append(c); return [c]
    def AppendToTimeline(self, infos):
        i = infos[0]
        it = FakeItem(i["recordFrame"], i["endFrame"] - i["startFrame"] + 1, i["trackIndex"])
        self.tl.tracks[i["trackIndex"]].append(it); self.last = i; return [it]


class FakeProject:
    def __init__(self): self.tl = FakeTimeline(); self.pool = FakePool(self.tl)
    def GetCurrentTimeline(self): return self.tl
    def GetMediaPool(self): return self.pool


class FakeResolve:
    def __init__(self): self.p = FakeProject()
    def GetProjectManager(self): return self
    def GetCurrentProject(self): return self.p
    def OpenPage(self, n): return True


def test_insert_flow_on_fake_resolve(monkeypatch=None):
    os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()
    fake = FakeResolve()
    builtins.resolve = fake
    try:
        item = rb.insert_title_at_playhead("spring_pop", {"text": "XIN CHÀO", "duration_frames": 72})
        last = fake.p.pool.last
        assert last["recordFrame"] == 86520
        # V2 is occupied at 86500-86550 -> next_free goes to new V3
        assert last["trackIndex"] == 3, last
        assert last["endFrame"] - last["startFrame"] + 1 == 72
        assert item.active.endswith(".comp") and item.comps == [item.active]
        # free spot: lands on V2 exactly
        fake.p.tl.GetCurrentTimecode = lambda: "01:00:10:00"
        rb.insert_title_at_playhead("lower_third", {"text": "A", "duration_frames": 48})
        assert fake.p.pool.last["trackIndex"] == 2
    finally:
        del builtins.resolve



def test_starter_pack_downloader_offline():
    import json, zipfile, pathlib
    from src.core import starter_pack as sp
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "a.mp3").write_bytes(b"ID3fake")
    with zipfile.ZipFile(d / "p.zip", "w") as z:
        z.writestr("Audio/click_001.ogg", b"OggS")
        z.writestr("Audio/readme.txt", b"x")
        z.writestr("../../evil.ogg", b"bad")
    man = {"policy": {"allowed_licenses": ["CC0-1.0"]},
           "sfx": [{"id": "a", "label": "A", "type": "file", "url": (d / "a.mp3").as_uri(), "license": "CC0-1.0"},
                   {"id": "p", "label": "P", "type": "zip", "url": (d / "p.zip").as_uri(), "license": "CC0-1.0",
                    "pick": ["*click*"]},
                   {"id": "x", "label": "X", "type": "file", "url": (d / "a.mp3").as_uri(), "license": "CC-BY-NC"}],
           "bgm": []}
    mp = d / "m.json"
    mp.write_text(json.dumps(man))
    rep = sp.download_starter_pack(str(d / "out"), str(mp))
    assert rep["a"]["status"] == "ok" and rep["p"]["status"] == "ok" and rep["x"]["status"] == "skipped"
    assert len(rep["p"]["files"]) == 1 and rep["p"]["files"][0].endswith("click_001.ogg")
    assert not (d / "evil.ogg").exists()
    (d / "a.mp3").write_bytes(b"tampered")
    rep2 = sp.download_starter_pack(str(d / "out2"), str(mp))
    assert rep2["a"]["status"] == "rejected"


def test_manifest_shape():
    import json
    m = json.load(open(os.path.join(os.path.dirname(__file__), "..", "assets", "starter_pack_manifest.json"), encoding="utf-8"))
    assert len(m["sfx"]) >= 15 and len(m["bgm"]) >= 6 and len(m["overlays"]) >= 10
    from src.core.fusion import OVERLAYS
    names = {f().name for f in OVERLAYS.values()}
    for o in m["overlays"]:
        if "template" in o:
            assert o["template"] in names
    for e in m["sfx"] + m["bgm"]:
        if "url" in e:
            assert e["url"].startswith("https://")
        if "license" in e:
            assert e["license"] == "CC0-1.0"


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); n += 1; print("PASS", name)
    print("%d tests passed" % n)
