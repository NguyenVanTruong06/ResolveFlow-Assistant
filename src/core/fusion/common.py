"""Shared parameters, node factories and expression snippets for all templates."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple

from .lua import Inp, Pt, Publish, Tool, UserControl

RGB = Tuple[float, float, float]


def parse_color(c) -> RGB:
    """'#FFD400' | 'FFD400' | (255,212,0) | (1.0,0.83,0) -> floats 0..1"""
    if isinstance(c, str):
        h = c.strip().lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) != 6:
            raise ValueError("Bad hex color %r" % c)
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore
    r, g, b = c[:3]
    if max(r, g, b) > 1.0:
        return (r / 255.0, g / 255.0, b / 255.0)
    return (float(r), float(g), float(b))


@dataclass
class TitleParams:
    """Everything the app UI lets a user change. Unused fields are ignored per style."""
    text: str = "RESOLVEFLOW"
    subtitle: str = "Subtitle goes here"
    font: str = "Open Sans"
    style: str = "Bold"
    size: float = 0.08                 # Text+ size (fraction of frame width)
    color: object = "#FFFFFF"
    accent: object = "#FFD400"         # marker / box / glow / line colour
    duration_frames: int = 90          # used for .comp carrier & fade-out
    delay_frames: float = 0.0          # animation start offset
    speed: float = 1.0                 # animation speed multiplier
    position: Tuple[float, float] = (0.5, 0.5)
    fade_in_frames: float = 0.0
    fade_out_frames: float = 8.0

    @classmethod
    def from_dict(cls, d: dict) -> "TitleParams":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        if "position" in known:
            known["position"] = tuple(known["position"])
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------
# Expression snippets (Fusion SimpleExpressions are Lua expressions)
# ----------------------------------------------------------------------------
def clamp01(x: str) -> str:
    return "min(1, max(0, %s))" % x


def ease_out(x: str) -> str:            # cubic out
    return "(1 - (1 - %s)^3)" % clamp01(x)


def ease_in(x: str) -> str:             # cubic in
    return "(%s^3)" % clamp01(x)


def hash01(seed: float, hold: str = "1") -> str:
    """Deterministic pseudo-random 0..1, constant for `hold` frames (step curve)."""
    return "((sin(floor(time / max(1, %s)) * 12.9898 + %s) * 43758.5453) %% 1)" % (hold, seed)


def text_tool(name: str, p: TitleParams, *, text_value: Optional[str] = None,
              size: Optional[float] = None, color: Optional[RGB] = None,
              pos=(0, 0), center=(0.5, 0.5), extra: Optional[dict] = None,
              controls: Optional[List[UserControl]] = None) -> Tool:
    r, g, b = color if color is not None else parse_color(p.color)
    inputs = {
        "Width": Inp(1920), "Height": Inp(1080), "UseFrameFormatSettings": Inp(1),
        "StyledText": Inp(value=p.text if text_value is None else text_value),
        "Font": Inp(p.font), "Style": Inp(p.style),
        "Size": Inp(size if size is not None else p.size),
        "Center": Inp(Pt(*center)),
        "Red1": Inp(r), "Green1": Inp(g), "Blue1": Inp(b), "Alpha1": Inp(1.0),
    }
    if extra:
        inputs.update(extra)
    return Tool(name, "TextPlus", inputs, pos=pos, controls=controls or [])


def solid(name: str, rgb: RGB, alpha: float = 1.0, pos=(0, 0), mask: Optional[str] = None) -> Tool:
    inputs = {
        "Width": Inp(1920), "Height": Inp(1080), "UseFrameFormatSettings": Inp(1),
        "TopLeftRed": Inp(rgb[0]), "TopLeftGreen": Inp(rgb[1]), "TopLeftBlue": Inp(rgb[2]),
        "TopLeftAlpha": Inp(alpha),
    }
    if mask:
        inputs["EffectMask"] = Inp(src=(mask, "Mask"))
    return Tool(name, "Background", inputs, pos=pos)


def merge(name: str, bg: str, fg: str, pos=(0, 0), mode: Optional[str] = None,
          blend: Optional[Inp] = None, center=None) -> Tool:
    inputs = {"Background": Inp(src=(bg, "Output")), "Foreground": Inp(src=(fg, "Output")),
              "PerformDepthMerge": Inp(0)}
    if mode:
        inputs["ApplyMode"] = Inp(mode)
    if blend is not None:
        inputs["Blend"] = blend
    if center is not None:
        inputs["Center"] = center
    return Tool(name, "Merge", inputs, pos=pos)


def tail(p: TitleParams, src: str, x: float) -> Tuple[List[Tool], List[Publish], str]:
    """
    Standard ending for every title:
      src -> Timing (TimeSpeed: Delay + Speed) -> OutFade (Merge over transparent canvas)
    OutFade carries Position, fade-in/out and is the macro output.
    """
    canvas = solid("Canvas", (0, 0, 0), alpha=0.0, pos=(x, -66))
    timing = Tool("Timing", "TimeSpeed", {
        "Input": Inp(src=(src, "Output")),
        "Speed": Inp(p.speed), "Delay": Inp(p.delay_frames),
        "InterpolateBetweenFrames": Inp(0),
    }, pos=(x, 0))
    blend = ("min(iif(OutFade.InFrames > 0, %s, 1), iif(OutFade.OutFrames > 0, %s, 1))" % (
        clamp01("(time - comp.RenderStart - Timing.Delay) / max(1, OutFade.InFrames)"),
        clamp01("(comp.RenderEnd - time) / max(1, OutFade.OutFrames)")))
    fade = Tool("OutFade", "Merge", {
        "Background": Inp(src=("Canvas", "Output")),
        "Foreground": Inp(src=("Timing", "Output")),
        "Center": Inp(Pt(*p.position)),
        "Blend": Inp(1.0, expr=blend),
        "PerformDepthMerge": Inp(0),
    }, pos=(x + 110, 0), controls=[
        UserControl("InFrames", "Fade In (frames)", default=p.fade_in_frames, hi=60),
        UserControl("OutFrames", "Fade Out (frames)", default=p.fade_out_frames, hi=60),
    ])
    pubs = [
        Publish("Delay", "Timing", "Delay", "Delay (frames)", page="Animation"),
        Publish("Speed", "Timing", "Speed", "Animation Speed", page="Animation"),
        Publish("Position", "OutFade", "Center", "Position"),
        Publish("FadeIn", "OutFade", "InFrames", "Fade In (frames)", page="Animation"),
        Publish("FadeOut", "OutFade", "OutFrames", "Fade Out (frames)", page="Animation"),
    ]
    return [canvas, timing, fade], pubs, "OutFade"


def text_publish(tool: str, label: str = "Text", key_prefix: str = "", source: str = "StyledText",
                 group_base: int = 10) -> List[Publish]:
    k = key_prefix
    return [
        Publish(k + "Text", tool, source, label),
        Publish(k + "Font", tool, "Font", label + " Font", group=group_base),
        Publish(k + "Style", tool, "Style", label + " Style", group=group_base),
        Publish(k + "Size", tool, "Size", label + " Size"),
        Publish(k + "Red", tool, "Red1", label + " Color", group=group_base + 1),
        Publish(k + "Green", tool, "Green1", group=group_base + 1),
        Publish(k + "Blue", tool, "Blue1", group=group_base + 1),
    ]


def color_publish(key: str, tool: str, label: str, group: int, prefix: str = "TopLeft") -> List[Publish]:
    return [
        Publish(key + "Red", tool, prefix + "Red", label, group=group),
        Publish(key + "Green", tool, prefix + "Green", group=group),
        Publish(key + "Blue", tool, prefix + "Blue", group=group),
    ]
