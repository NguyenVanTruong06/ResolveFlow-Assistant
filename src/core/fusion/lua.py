"""
Fusion .setting / .comp serializer.

Fusion stores macros and comps as Lua table constructors, e.g.

    { Tools = ordered() { Text1 = TextPlus { Inputs = { Size = Input { Value = 0.08, }, }, }, }, }

This module builds that text from small Python objects so every template is
generated (and parameterised) from code instead of hand-edited strings.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

INDENT = "\t"


# ----------------------------------------------------------------------------
# Low-level Lua values
# ----------------------------------------------------------------------------
class Raw:
    """Verbatim Lua source (already valid)."""

    def __init__(self, src: str):
        self.src = src


class Call:
    """`Name { ... }`  or  `ordered() { ... }` constructor."""

    def __init__(self, name: str, body: Union[dict, list, None] = None, ordered: bool = False):
        self.name = name
        self.body = body if body is not None else {}
        self.ordered = ordered


def lua_str(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32:
            out.append("\\%d" % o)
        else:
            out.append(ch)  # UTF-8 (Vietnamese etc.) is written as-is
    out.append('"')
    return "".join(out)


def lua_num(v: Union[int, float]) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if math.isnan(v) or math.isinf(v):
        raise ValueError("NaN/Inf cannot be written to a .setting")
    r = repr(float(v))
    if r.endswith(".0"):
        r = r[:-2]
    return r


def _key(k) -> str:
    if isinstance(k, (int, float)) and not isinstance(k, bool):
        return "[%s]" % lua_num(k)
    k = str(k)
    if k.isidentifier():
        return k
    return "[%s]" % lua_str(k)


def dump(v: Any, level: int = 0) -> str:
    pad = INDENT * level
    pad1 = INDENT * (level + 1)
    if v is None:
        return "nil"
    if isinstance(v, Raw):
        return v.src
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return lua_num(v)
    if isinstance(v, str):
        return lua_str(v)
    if isinstance(v, Call):
        head = "ordered() " if v.ordered else (v.name + " ")
        return head + dump(v.body, level)
    if isinstance(v, (list, tuple)):
        if not v:
            return "{}"
        if all(isinstance(x, (int, float, str, bool)) for x in v) and len(v) <= 6:
            return "{ " + ", ".join(dump(x) for x in v) + " }"
        return "{\n" + "".join(pad1 + dump(x, level + 1) + ",\n" for x in v) + pad + "}"
    if isinstance(v, dict):
        if not v:
            return "{ }"
        lines = []
        for k, val in v.items():
            if isinstance(k, _Pos):  # positional entry inside a keyed table
                lines.append(pad1 + dump(val, level + 1) + ",\n")
            else:
                lines.append(pad1 + _key(k) + " = " + dump(val, level + 1) + ",\n")
        return "{\n" + "".join(lines) + pad + "}"
    raise TypeError("Cannot serialise %r" % (v,))


class _Pos:
    """Marker key for positional values in a mixed table (spline keyframes)."""
    _n = 0

    def __init__(self):
        _Pos._n += 1
        self.n = _Pos._n

    def __hash__(self):
        return self.n


# ----------------------------------------------------------------------------
# Fusion building blocks
# ----------------------------------------------------------------------------
def Inp(value: Any = None, expr: Optional[str] = None, src: Optional[Tuple[str, str]] = None) -> Call:
    """Fusion `Input { ... }`.  src=("ToolName","Output") connects a node."""
    body: Dict[str, Any] = {}
    if src is not None:
        body["SourceOp"] = src[0]
        body["Source"] = src[1]
    if value is not None:
        body["Value"] = value
    if expr is not None:
        body["Expression"] = expr
    return Call("Input", body)


def Pt(x: float, y: float) -> list:
    return [float(x), float(y)]


def out(tool: str, socket: str = "Output") -> Call:
    return Inp(src=(tool, socket))


@dataclass
class UserControl:
    """A custom slider/text/checkbox added to a tool (Fusion 'UserControls')."""
    name: str
    label: str
    kind: str = "slider"          # slider | text | checkbox | point
    default: Any = 0
    lo: float = 0.0
    hi: float = 1.0
    page: str = "Controls"

    def table(self) -> dict:
        t: Dict[str, Any] = {"LINKS_Name": self.label, "ICS_ControlPage": self.page}
        if self.kind == "text":
            t.update(LINKID_DataType="Text", INPID_InputControl="TextEditControl",
                     TEC_Lines=2, INP_Default=0)
        elif self.kind == "checkbox":
            t.update(LINKID_DataType="Number", INPID_InputControl="CheckboxControl",
                     INP_Integer=True, INP_Default=float(self.default))
        else:
            t.update(LINKID_DataType="Number", INPID_InputControl="SliderControl",
                     INP_Default=float(self.default), INP_MinScale=float(self.lo),
                     INP_MaxScale=float(self.hi))
        return t

    def initial_input(self) -> Call:
        return Inp(value=self.default)


@dataclass
class Tool:
    name: str
    type: str
    inputs: Dict[str, Call] = field(default_factory=dict)
    pos: Tuple[float, float] = (0.0, 0.0)
    controls: List[UserControl] = field(default_factory=list)
    instance_of: Optional[str] = None   # Fusion instanced tool (shares un-overridden inputs)
    extra: Dict[str, Any] = field(default_factory=dict)

    def entry(self) -> Tuple[str, Call]:
        body: Dict[str, Any] = {}
        if self.instance_of:
            body["SourceOp"] = self.instance_of
        body["CtrlWZoom"] = False
        inputs = dict(self.inputs)
        for uc in self.controls:
            inputs.setdefault(uc.name, uc.initial_input())
        body["Inputs"] = inputs
        body["ViewInfo"] = Call("OperatorInfo", {"Pos": [float(self.pos[0]), float(self.pos[1])]})
        if self.controls:
            body["UserControls"] = Call("", {uc.name: uc.table() for uc in self.controls}, ordered=True)
        body.update(self.extra)
        return self.name, Call(self.type, body)


@dataclass
class Key:
    frame: float
    value: float
    lh: Optional[Tuple[float, float]] = None
    rh: Optional[Tuple[float, float]] = None
    linear: bool = False


def smooth_keys(points: Sequence[Tuple[float, float]], tension: float = 1 / 3) -> List[Key]:
    """Flat-tangent (ease) Bezier keys: every key is a local extreme -> springy overshoot."""
    keys = []
    for i, (f, v) in enumerate(points):
        lh = rh = None
        if i > 0:
            pf = points[i - 1][0]
            lh = (f - (f - pf) * tension, v)
        if i < len(points) - 1:
            nf = points[i + 1][0]
            rh = (f + (nf - f) * tension, v)
        keys.append(Key(f, v, lh, rh))
    return keys


@dataclass
class Spline:
    """BezierSpline animation curve, connected with Inp(src=(name,'Value'))."""
    name: str
    keys: List[Key]
    color: Tuple[int, int, int] = (225, 0, 225)

    def entry(self) -> Tuple[str, Call]:
        kf: Dict[Any, Any] = {}
        for k in self.keys:
            t: Dict[Any, Any] = {_Pos(): float(k.value)}
            if k.lh:
                t["LH"] = [float(k.lh[0]), float(k.lh[1])]
            if k.rh:
                t["RH"] = [float(k.rh[0]), float(k.rh[1])]
            t["Flags"] = {"Linear": bool(k.linear)}
            kf[float(k.frame)] = t
        return self.name, Call("BezierSpline", {
            "SplineColor": {"Red": self.color[0], "Green": self.color[1], "Blue": self.color[2]},
            "NameSet": True,
            "KeyFrames": kf,
        })


@dataclass
class Publish:
    """A control shown in the Edit-page Inspector (MacroOperator InstanceInput)."""
    key: str
    tool: str
    source: str
    name: Optional[str] = None
    default: Any = None
    group: Optional[int] = None
    page: str = "Controls"

    def call(self) -> Call:
        b: Dict[str, Any] = {"SourceOp": self.tool, "Source": self.source}
        if self.name:
            b["Name"] = self.name
        if self.group is not None:
            b["ControlGroup"] = self.group
        if self.default is not None:
            b["Default"] = self.default
        b["Page"] = self.page
        return Call("InstanceInput", b)


@dataclass
class Macro:
    name: str
    nodes: List[Union[Tool, Spline]]
    publish: List[Publish]
    output_tool: str
    output_socket: str = "Output"
    # extra main inputs for transitions/effects: (key, tool, socket)
    main_inputs: List[Tuple[str, str, str]] = field(default_factory=list)
    info: Dict[str, str] = field(default_factory=dict)

    def tools_table(self) -> Call:
        tbl = {}
        for n in self.nodes:
            k, v = n.entry()
            if k in tbl:
                raise ValueError("Duplicate tool name %s in %s" % (k, self.name))
            tbl[k] = v
        return Call("", tbl, ordered=True)

    def operator(self) -> Call:
        names = {n.name for n in self.nodes}
        inputs: Dict[str, Any] = {}
        for key, tool, sock in self.main_inputs:
            assert tool in names, (self.name, tool)
            inputs[key] = Call("InstanceInput", {"SourceOp": tool, "Source": sock})
        for p in self.publish:
            assert p.tool in names, (self.name, p.tool)
            if p.key in inputs:
                raise ValueError("Duplicate published key %s" % p.key)
            inputs[p.key] = p.call()
        assert self.output_tool in names, (self.name, self.output_tool)
        body = {
            "CtrlWZoom": False,
            "NameSet": True,
            "CustomData": dict(self.info) if self.info else {"Generator": "ResolveFlow-Assistant"},
            "Inputs": Call("", inputs, ordered=True),
            "Outputs": {"MainOutput1": Call("InstanceOutput", {
                "SourceOp": self.output_tool, "Source": self.output_socket})},
            "ViewInfo": Call("GroupInfo", {"Pos": [0.0, 0.0]}),
            "Tools": self.tools_table(),
        }
        return Call("MacroOperator", body)

    # --- file formats -------------------------------------------------------
    def to_setting(self) -> str:
        doc = {"Tools": Call("", {self.name: self.operator()}, ordered=True),
               "ActiveTool": self.name}
        return dump(doc) + "\n"

    def to_comp(self, frames: int, width: int = 1920, height: int = 1080, fps: float = 30.0) -> str:
        """A standalone Fusion composition: macro -> MediaOut1 (carrier for ImportFusionComp)."""
        end = max(1, int(frames)) - 1
        media_out = Call("MediaOut", {
            "CtrlWZoom": False,
            "Inputs": {"Index": Inp(value="0"),
                       "Input": Inp(src=(self.name, "MainOutput1"))},
            "ViewInfo": Call("OperatorInfo", {"Pos": [220.0, 0.0]}),
        })
        comp = Call("Composition", {
            "CurrentTime": 0,
            "RenderRange": [0, end],
            "GlobalRange": [0, end],
            "CurrentID": 1,
            "HiQ": True,
            "PlaybackUpdateMode": 0,
            "Version": "DaVinci Resolve / ResolveFlow-Assistant",
            "SavedOutputs": 0,
            "HeldTools": 0,
            "DisabledTools": 0,
            "LockedTools": 0,
            "AudioOffset": 0,
            "Resumable": True,
            "OutputClips": [],
            "Tools": Call("", {self.name: self.operator(), "MediaOut1": media_out}, ordered=True),
            "Frames": [],
            "Prefs": {"Comp": {"FrameFormat": {"Width": int(width), "Height": int(height),
                                                 "Rate": float(fps)}}},
        })
        return dump(comp) + "\n"
