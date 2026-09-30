from src.core.resolve_api import ResolveAutomation


class FakeGraph:
    def __init__(self, luts=None, accept=True):
        self.luts = dict(luts or {})     # {node_index: lut}
        self.nodes = max(self.luts, default=1)
        self.accept = accept

    def GetNumNodes(self):
        return self.nodes

    def GetLUT(self, i):
        return self.luts.get(i, "")

    def SetLUT(self, i, path):
        if not self.accept:
            return False
        self.luts[i] = path
        return True


class FakeItem:
    def __init__(self, name, graph):
        self.name, self.graph = name, graph

    def GetName(self):
        return self.name

    def GetNodeGraph(self, *_):
        return self.graph


class FakeTimeline:
    def __init__(self, graph, items):
        self.graph, self.items = graph, items

    def GetNodeGraph(self):
        return self.graph

    def GetTrackCount(self, kind):
        return 1 if kind == "video" else 0

    def GetItemListInTrack(self, kind, idx):
        return self.items


class FakeProject:
    refreshed = 0

    def RefreshLUTList(self):
        FakeProject.refreshed += 1
        return True


def _auto(timeline):
    a = ResolveAutomation()
    a.resolve, a.current_project = object(), FakeProject()
    a.get_active_timeline = lambda: timeline
    return a


def _lut(tmp_path):
    p = tmp_path / "look.cube"
    p.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
    return str(p)


def test_timeline_scope_applies_when_no_existing_lut(tmp_path):
    tl = FakeTimeline(FakeGraph(), [])
    stats = _auto(tl).apply_look_lut(_lut(tmp_path), "timeline", True, log_callback=lambda m: None)
    assert stats == {"applied": 1, "skipped": 0, "failed": 0} and tl.graph.luts[1].endswith("look.cube")


def test_existing_user_lut_is_preserved(tmp_path):
    tl = FakeTimeline(FakeGraph({1: "MyLook.cube"}), [])
    stats = _auto(tl).apply_look_lut(_lut(tmp_path), "timeline", True, log_callback=lambda m: None)
    assert stats["skipped"] == 1 and stats["applied"] == 0 and tl.graph.luts[1] == "MyLook.cube"


def test_overwrite_when_user_disables_skip(tmp_path):
    tl = FakeTimeline(FakeGraph({1: "MyLook.cube"}), [])
    stats = _auto(tl).apply_look_lut(_lut(tmp_path), "timeline", False, log_callback=lambda m: None)
    assert stats["applied"] == 1 and tl.graph.luts[1].endswith("look.cube")


def test_clip_scope_skips_clips_with_lut(tmp_path):
    graded = FakeItem("A", FakeGraph({2: "Mine.cube"}))   # LUT nằm ở node 2 vẫn phải bị phát hiện
    plain = FakeItem("B", FakeGraph())
    stats = _auto(FakeTimeline(FakeGraph(), [graded, plain])).apply_look_lut(
        _lut(tmp_path), "clips", True, log_callback=lambda m: None)
    assert stats == {"applied": 1, "skipped": 1, "failed": 0}
    assert 1 not in graded.graph.luts and plain.graph.luts[1].endswith("look.cube")


def test_scan_existing_luts_reports_clips_and_timeline():
    tl = FakeTimeline(FakeGraph({1: "T.cube"}), [FakeItem("A", FakeGraph({1: "X.cube"})), FakeItem("B", FakeGraph())])
    info = _auto(tl).scan_existing_luts()
    assert info["timeline"] == ["T.cube"] and info["total_clips"] == 2 and info["clips"] == [("A", ["X.cube"])]


def test_missing_file_and_no_timeline():
    logs = []
    assert _auto(None).apply_look_lut("khong_co.cube", log_callback=logs.append)["failed"] == 1
    assert _auto(None).scan_existing_luts() == {"timeline": [], "clips": [], "total_clips": 0}
