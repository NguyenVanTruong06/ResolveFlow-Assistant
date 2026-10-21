"""
6 Fusion Transition macros (Edit page > Effects > Video Transitions > Fusion Transitions).

Resolve feeds the outgoing clip into MainInput1 ("Background") and the incoming
clip into MainInput2 ("Foreground"). Every macro derives a normalised progress
`p` (0 at the first frame of the transition, 1 at the last) from the comp's
render range, so the effect fits whatever length the editor drags it to.
"""
from __future__ import annotations

from typing import Callable, Dict, List

from .common import clamp01, ease_in, ease_out, hash01, merge, solid
from .lua import Inp, Macro, Pt, Publish, Tool, UserControl

P = "ClipA.Progress"
PROGRESS = "(time - comp.RenderStart) / max(1, comp.RenderEnd - comp.RenderStart)"
CUT = "iif(%s >= 0.5, 1, 0)" % P


def _entry(extra_controls: List[UserControl] = ()) -> List[Tool]:
    a = Tool("ClipA", "PipeRouter", {"Progress": Inp(0.0, expr=PROGRESS)}, pos=(0, -66),
             controls=[UserControl("Progress", "Progress (auto)", default=0, hi=1)] + list(extra_controls))
    b = Tool("ClipB", "PipeRouter", {}, pos=(0, 66))
    return [a, b]


def _switch(mix_expr: str = CUT, x=110) -> Tool:
    return Tool("Switch", "Dissolve", {
        "Background": Inp(src=("ClipA", "Output")),
        "Foreground": Inp(src=("ClipB", "Output")),
        "Mix": Inp(0.0, expr=mix_expr),
    }, pos=(x, 0))


def _mk(name: str, nodes: List[Tool], pubs: List[Publish], out: str, style: str) -> Macro:
    return Macro(name, nodes, pubs, out,
                 main_inputs=[("MainInput1", "ClipA", "Input"), ("MainInput2", "ClipB", "Input")],
                 info={"Style": style, "Kind": "Transition"})


def _half(a: str, b: str) -> str:
    """first half uses `a(2p)`, second half uses `b(2p-1)`"""
    return "iif(%s < 0.5, %s, %s)" % (P, a.replace("X", "(%s * 2)" % P), b.replace("X", "(%s * 2 - 1)" % P))


MB = {"MotionBlur": Inp(1), "Quality": Inp(10), "ShutterAngle": Inp(360), "CenterBias": Inp(0),
      "SampleSpread": Inp(1)}


# 1 ---------------------------------------------------------------------------
def whip_pan() -> Macro:
    nodes = _entry([UserControl("Reverse", "Right -> Left", kind="checkbox", default=0),
                    UserControl("Distance", "Distance", default=1.0, lo=0.25, hi=2)])
    nodes.append(_switch())
    d = "(1 - 2 * ClipA.Reverse) * ClipA.Distance"
    off = _half("-%s * %s" % (d, ease_in("X")), "%s * (1 - %s)" % (d, ease_out("X")))
    nodes.append(Tool("Whip", "Transform", {
        "Input": Inp(src=("Switch", "Output")),
        "Center": Inp(Pt(0.5, 0.5), expr="Point(0.5 + %s, 0.5)" % off),
        "Edges": Inp(1),  # wrap -> seamless at full-width offset
        **MB,
    }, pos=(220, 0)))
    pubs = [Publish("Reverse", "ClipA", "Reverse", "Right -> Left"),
            Publish("Distance", "ClipA", "Distance", "Distance"),
            Publish("Shutter", "Whip", "ShutterAngle", "Motion Blur Shutter"),
            Publish("Quality", "Whip", "Quality", "Blur Quality")]
    return _mk("RF_Whip_Pan", nodes, pubs, "Whip", "Whip Pan Left/Right")


# 2 ---------------------------------------------------------------------------
def impact_zoom() -> Macro:
    nodes = _entry([UserControl("Zoom", "Peak Zoom", default=3.0, lo=1.2, hi=8)])
    nodes.append(_switch())
    size = _half("1 + (ClipA.Zoom - 1) * %s" % ease_in("X"),
                 "1 + (ClipA.Zoom - 1) * (1 - %s)" % ease_out("X"))
    nodes.append(Tool("Punch", "Transform", {
        "Input": Inp(src=("Switch", "Output")),
        "Size": Inp(1.0, expr=size),
        "Edges": Inp(3),  # mirror, no black borders
        **MB,
    }, pos=(220, 0)))
    flash = "max(0, 1 - abs(%s - 0.5) / 0.12)^2 * ClipA.Flash" % P
    nodes.append(Tool("Bloom", "SoftGlow", {
        "Input": Inp(src=("Punch", "Output")), "Threshold": Inp(0.3),
        "Gain": Inp(0.0, expr="3 * %s" % flash), "XGlowSize": Inp(30.0),
    }, pos=(330, 0)))
    nodes[0].controls.append(UserControl("Flash", "Impact Bloom", default=0.6, hi=1))
    pubs = [Publish("Zoom", "ClipA", "Zoom", "Peak Zoom"),
            Publish("Flash", "ClipA", "Flash", "Impact Bloom"),
            Publish("Shutter", "Punch", "ShutterAngle", "Motion Blur Shutter")]
    return _mk("RF_Impact_Zoom", nodes, pubs, "Bloom", "Impact Zoom Blur")


# 3 ---------------------------------------------------------------------------
def camera_shake() -> Macro:
    nodes = _entry([UserControl("Strength", "Shake Strength", default=0.035, hi=0.15),
                    UserControl("Rotation", "Shake Rotation (deg)", default=2.5, hi=15)])
    nodes.append(_switch())
    env = "iif(%s < 0.5, (%s * 2)^4 * 0.35, (1 - (%s - 0.5) * 2)^2)" % (P, P, P)
    nodes.append(Tool("Shake", "Transform", {
        "Input": Inp(src=("Switch", "Output")),
        "Center": Inp(Pt(0.5, 0.5), expr="Point(0.5 + ClipA.Strength * %s * (%s - 0.5) * 2, 0.5 + ClipA.Strength * %s * (%s - 0.5) * 2)"
                     % (env, hash01(1.3), env, hash01(2.9))),
        "Angle": Inp(0.0, expr="ClipA.Rotation * %s * (%s - 0.5) * 2" % (env, hash01(6.1))),
        "Size": Inp(1.0, expr="1 + 2.2 * ClipA.Strength * %s" % env),
        "Edges": Inp(3),
        "MotionBlur": Inp(1), "Quality": Inp(6), "ShutterAngle": Inp(180),
    }, pos=(220, 0)))
    pubs = [Publish("Strength", "ClipA", "Strength", "Shake Strength"),
            Publish("Rotation", "ClipA", "Rotation", "Shake Rotation")]
    return _mk("RF_Camera_Shake", nodes, pubs, "Shake", "Camera Shake Impact")


# 4 ---------------------------------------------------------------------------
def flash_white() -> Macro:
    nodes = _entry([UserControl("Width", "Flash Width", default=0.35, lo=0.05, hi=1),
                    UserControl("Intensity", "Flash Intensity", default=1.0, hi=1)])
    mix = ease_out("(%s - 0.42) / 0.16" % P)
    nodes.append(_switch(mix))
    peak = "max(0, 1 - abs(%s - 0.5) / max(0.01, ClipA.Width / 2))" % P
    nodes.append(solid("White", (1, 1, 1), 1.0, pos=(110, -132)))
    nodes.append(merge("Flash", "Switch", "White", pos=(220, 0),
                       blend=Inp(0.0, expr="ClipA.Intensity * %s^1.5" % peak)))
    nodes.append(Tool("Glow", "SoftGlow", {
        "Input": Inp(src=("Flash", "Output")), "Threshold": Inp(0.25),
        "Gain": Inp(0.0, expr="2.5 * %s" % peak), "XGlowSize": Inp(40.0),
    }, pos=(330, 0)))
    pubs = [Publish("Width", "ClipA", "Width", "Flash Width"),
            Publish("Intensity", "ClipA", "Intensity", "Flash Intensity"),
            Publish("FlashRed", "White", "TopLeftRed", "Flash Color", group=30),
            Publish("FlashGreen", "White", "TopLeftGreen", group=30),
            Publish("FlashBlue", "White", "TopLeftBlue", group=30),
            Publish("GlowSize", "Glow", "XGlowSize", "Glow Size")]
    return _mk("RF_Flash_White_Glow", nodes, pubs, "Glow", "Flash White / Glow Dissolve")


# 5 ---------------------------------------------------------------------------
def rgb_glitch() -> Macro:
    """Per-channel offsets: (image x pure R/G/B solid) recombined with Screen."""
    nodes = _entry([UserControl("Amount", "Glitch Amount", default=0.06, hi=0.3),
                    UserControl("Hold", "Glitch Hold (frames)", default=2, lo=1, hi=6)])
    nodes.append(_switch())
    env = "sin(pi * %s)^2" % P
    prev = None
    for i, (tag, rgb, seed) in enumerate([("R", (1, 0, 0), 1.1), ("G", (0, 1, 0), 4.7), ("B", (0, 0, 1), 8.3)]):
        y = -132 + 132 * i
        nodes.append(Tool("Off" + tag, "Transform", {
            "Input": Inp(src=("Switch", "Output")),
            "Center": Inp(Pt(0.5, 0.5), expr="Point(0.5 + ClipA.Amount * %s * (%s - 0.5) * 2, 0.5 + ClipA.Amount * 0.3 * %s * (%s - 0.5))"
                         % (env, hash01(seed, "ClipA.Hold"), env, hash01(seed + 3, "ClipA.Hold"))),
            "Edges": Inp(3),
        }, pos=(220, y)))
        nodes.append(solid("Solid" + tag, rgb, 1.0, pos=(330, y - 33)))
        nodes.append(merge("Only" + tag, "Solid" + tag, "Off" + tag, pos=(330, y), mode="Multiply"))
        if prev is None:
            prev = "Only" + tag
        else:
            nodes.append(merge("Sum" + tag, prev, "Only" + tag, pos=(440, y), mode="Screen"))
            prev = "Sum" + tag
    pubs = [Publish("Amount", "ClipA", "Amount", "Glitch Amount"),
            Publish("Hold", "ClipA", "Hold", "Glitch Hold (frames)")]
    return _mk("RF_RGB_Glitch", nodes, pubs, prev, "RGB Glitch Transition")


# 6 ---------------------------------------------------------------------------
def spin_roll() -> Macro:
    nodes = _entry([UserControl("Degrees", "Spin Degrees (90/180)", default=180, lo=45, hi=360),
                    UserControl("ZoomOut", "Zoom Punch", default=0.6, hi=2)])
    nodes.append(_switch())
    ang = _half("ClipA.Degrees * %s" % ease_in("X"), "-ClipA.Degrees * (1 - %s)" % ease_out("X"))
    nodes.append(Tool("Spin", "Transform", {
        "Input": Inp(src=("Switch", "Output")),
        "Angle": Inp(0.0, expr=ang),
        "Size": Inp(1.0, expr="1 + ClipA.ZoomOut * sin(pi * %s)" % P),
        "Edges": Inp(3),
        **MB,
    }, pos=(220, 0)))
    pubs = [Publish("Degrees", "ClipA", "Degrees", "Spin Degrees"),
            Publish("Zoom", "ClipA", "ZoomOut", "Zoom Punch"),
            Publish("Shutter", "Spin", "ShutterAngle", "Motion Blur Shutter")]
    return _mk("RF_Spin_Roll", nodes, pubs, "Spin", "Spin Roll 90/180")


TRANSITIONS: Dict[str, Callable[[], Macro]] = {
    "whip_pan": whip_pan,
    "impact_zoom": impact_zoom,
    "camera_shake": camera_shake,
    "flash_white": flash_white,
    "rgb_glitch": rgb_glitch,
    "spin_roll": spin_roll,
}
