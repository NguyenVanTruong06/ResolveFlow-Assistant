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
    def __init__(self, name, graph, start=0, duration=100):
        self.name, self.graph = name, graph
        self.start = start
        self.duration = duration

    def GetName(self):
        return self.name

    def GetNodeGraph(self, *_):
        return self.graph

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.start + self.duration

    def GetDuration(self):
        return self.duration


class FakeTimeline:
    def __init__(self, graph, items, timecode="01:00:00:00", start_frame=0):
        self.graph, self.items = graph, items
        self.timecode = timecode
        self.start_frame = start_frame

    def GetNodeGraph(self):
        return self.graph

    def GetTrackCount(self, kind):
        return 1 if kind == "video" else 0

    def GetItemListInTrack(self, kind, idx):
        return self.items if kind == "video" and idx == 1 else []

    def GetCurrentTimecode(self):
        return self.timecode

    def GetStartTimecode(self):
        return "01:00:00:00"

    def GetStartFrame(self):
        return self.start_frame


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


def test_apply_lut_to_current_clip_at_A_only_modifies_A(tmp_path):
    graph_tl = FakeGraph()
    graph_a = FakeGraph()
    graph_b = FakeGraph()
    clip_a = FakeItem("Clip_A", graph_a, start=0, duration=100)
    clip_b = FakeItem("Clip_B", graph_b, start=100, duration=100)
    # Timeline start frame 0, playhead at frame 50 -> timecode "01:00:02:02" (24fps: 2*24 + 2 = 50)
    tl = FakeTimeline(graph_tl, [clip_a, clip_b], timecode="01:00:02:02", start_frame=0)
    auto = _auto(tl)

    lut_file = _lut(tmp_path)
    res = auto.apply_lut_to_current_clip(lut_file)

    assert res["status"] == "applied"
    assert res["applied"] == 1
    assert res["clip_name"] == "Clip_A"
    assert graph_a.luts.get(1, "").endswith("look.cube")
    # Clip B and Timeline MUST NOT be modified
    assert graph_b.luts == {}
    assert graph_tl.luts == {}


def test_apply_lut_to_current_clip_at_B_only_modifies_B(tmp_path):
    graph_tl = FakeGraph()
    graph_a = FakeGraph()
    graph_b = FakeGraph()
    clip_a = FakeItem("Clip_A", graph_a, start=0, duration=100)
    clip_b = FakeItem("Clip_B", graph_b, start=100, duration=100)
    # Playhead at frame 150 -> timecode "01:00:06:06" (24fps: 6*24 + 6 = 150)
    tl = FakeTimeline(graph_tl, [clip_a, clip_b], timecode="01:00:06:06", start_frame=0)
    auto = _auto(tl)

    lut_file = _lut(tmp_path)
    res = auto.apply_lut_to_current_clip(lut_file)

    assert res["status"] == "applied"
    assert res["applied"] == 1
    assert res["clip_name"] == "Clip_B"
    assert graph_b.luts.get(1, "").endswith("look.cube")
    # Clip A and Timeline MUST NOT be modified
    assert graph_a.luts == {}
    assert graph_tl.luts == {}


def test_apply_lut_to_current_clip_at_gap_fails_without_modifying_any(tmp_path):
    graph_tl = FakeGraph()
    graph_a = FakeGraph()
    graph_b = FakeGraph()
    clip_a = FakeItem("Clip_A", graph_a, start=0, duration=100)
    clip_b = FakeItem("Clip_B", graph_b, start=100, duration=100)
    # Playhead at frame 300 (gap after clips) -> timecode "01:00:12:12" (24fps: 12*24 + 12 = 300)
    tl = FakeTimeline(graph_tl, [clip_a, clip_b], timecode="01:00:12:12", start_frame=0)
    auto = _auto(tl)

    lut_file = _lut(tmp_path)
    res = auto.apply_lut_to_current_clip(lut_file)

    assert res["status"] == "error"
    assert res["applied"] == 0
    assert not res["ok"]
    assert "Không có clip video nào" in res["message"] or "frame" in res["message"]
    # Neither clip nor timeline modified
    assert graph_a.luts == {}
    assert graph_b.luts == {}
    assert graph_tl.luts == {}


def test_apply_lut_to_current_clip_preserves_existing_lut(tmp_path):
    graph_tl = FakeGraph()
    graph_a = FakeGraph({1: "OldGrade.cube"})
    clip_a = FakeItem("Clip_A", graph_a, start=0, duration=100)
    tl = FakeTimeline(graph_tl, [clip_a], timecode="01:00:02:02", start_frame=0)
    auto = _auto(tl)

    lut_file = _lut(tmp_path)
    res = auto.apply_lut_to_current_clip(lut_file, skip_existing=True)

    assert res["status"] == "skipped"
    assert res["applied"] == 0
    assert res["skipped"] == 1
    assert "chưa thay đổi" in res["message"]
    # Node 1 must still have OldGrade.cube
    assert graph_a.luts[1] == "OldGrade.cube"
    assert graph_tl.luts == {}

