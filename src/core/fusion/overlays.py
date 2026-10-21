"""
Procedural overlay generators (Edit page > Effects > Generators > Fusion Generators).

These replace downloaded overlay videos: zero licensing risk, resolution-independent,
and tiny. Put them on a track above the footage and set the clip's Composite Mode
in the Inspector where noted (Screen for light leaks, Overlay for grain).
"""
from __future__ import annotations

from typing import Callable, Dict

from .common import merge, solid
from .lua import Inp, Macro, Pt, Publish, Tool, UserControl


def _gen(name, nodes, pubs, out, style, blend_hint=""):
    return Macro(name, nodes, pubs, out, info={"Style": style, "Kind": "Generator",
                                               "CompositeHint": blend_hint or "Normal"})


def letterbox(ratio: float = 2.35) -> Macro:
    mask = Tool("Picture", "RectangleMask", {
        "Center": Inp(Pt(0.5, 0.5)), "Width": Inp(2.0),
        "Height": Inp(0.75, expr="min(1, Picture.SourceAspect / max(0.1, Picture.TargetRatio))"),
        "Invert": Inp(1), "SoftEdge": Inp(0.0),
    }, controls=[UserControl("SourceAspect", "Timeline Aspect (W/H)", default=16 / 9, lo=0.5, hi=3),
                 UserControl("TargetRatio", "Target Ratio", default=ratio, lo=1, hi=3)])
    bars = solid("Bars", (0, 0, 0), 1.0, pos=(110, 0), mask="Picture")
    return _gen("RF_Letterbox", [mask, bars],
                [Publish("Ratio", "Picture", "TargetRatio", "Target Ratio"),
                 Publish("Aspect", "Picture", "SourceAspect", "Timeline Aspect"),
                 Publish("BarAlpha", "Bars", "TopLeftAlpha", "Bar Opacity")], "Bars",
                "Cinematic letterbox bars (2.35 / 2.39 / 1.85)")


def vignette() -> Macro:
    mask = Tool("Oval", "EllipseMask", {
        "Center": Inp(Pt(0.5, 0.5)), "Width": Inp(1.25), "Height": Inp(1.25),
        "SoftEdge": Inp(0.35), "Invert": Inp(1),
    })
    dark = solid("Dark", (0, 0, 0), 0.75, pos=(110, 0), mask="Oval")
    return _gen("RF_Vignette", [mask, dark],
                [Publish("Strength", "Dark", "TopLeftAlpha", "Strength"),
                 Publish("Size", "Oval", "Width", "Size"),
                 Publish("Softness", "Oval", "SoftEdge", "Softness"),
                 Publish("Center", "Oval", "Center", "Center")], "Dark", "Vignette")


def film_grain() -> Macro:
    noise = Tool("Grain", "FastNoise", {
        "Width": Inp(1920), "Height": Inp(1080), "UseFrameFormatSettings": Inp(1),
        "Detail": Inp(10.0), "Contrast": Inp(1.6), "Brightness": Inp(0.0),
        "XScale": Inp(420.0), "SeetheRate": Inp(6.0),
    })
    return _gen("RF_Film_Grain", [noise],
                [Publish("Scale", "Grain", "XScale", "Grain Size (higher = finer)"),
                 Publish("Contrast", "Grain", "Contrast", "Grain Contrast"),
                 Publish("Speed", "Grain", "SeetheRate", "Grain Speed")], "Grain",
                "Film grain", "Overlay (opacity 15-35%)")


def light_leak(warm=(1.0, 0.45, 0.12)) -> Macro:
    noise = Tool("Leak", "FastNoise", {
        "Width": Inp(1920), "Height": Inp(1080), "UseFrameFormatSettings": Inp(1),
        "Detail": Inp(2.0), "Contrast": Inp(2.4), "Brightness": Inp(-0.15),
        "XScale": Inp(1.4), "SeetheRate": Inp(0.08), "Center": Inp(Pt(0.5, 0.5),
            expr="Point(0.5 + 0.25 * sin(time / 70), 0.5)"),
    })
    tint = solid("Tint", warm, 1.0, pos=(0, -66))
    col = merge("Colorize", "Leak", "Tint", pos=(110, 0), mode="Multiply")
    glow = Tool("Bloom", "SoftGlow", {"Input": Inp(src=("Colorize", "Output")), "Threshold": Inp(0.0),
                                       "Gain": Inp(1.2), "XGlowSize": Inp(80.0)}, pos=(220, 0))
    return _gen("RF_Light_Leak", [noise, tint, col, glow],
                [Publish("TintRed", "Tint", "TopLeftRed", "Leak Color", group=30),
                 Publish("TintGreen", "Tint", "TopLeftGreen", group=30),
                 Publish("TintBlue", "Tint", "TopLeftBlue", group=30),
                 Publish("Drift", "Leak", "SeetheRate", "Drift Speed"),
                 Publish("Brightness", "Leak", "Brightness", "Brightness")], "Bloom",
                "Light leak", "Screen")


def rec_frame() -> Macro:
    frame_mask = Tool("FrameMask", "RectangleMask", {
        "Center": Inp(Pt(0.5, 0.5)), "Width": Inp(0.92), "Height": Inp(0.88),
        "Solid": Inp(0), "BorderWidth": Inp(0.0015), "SoftEdge": Inp(0.0),
    })
    frame = solid("Frame", (1, 1, 1), 0.85, pos=(110, 0), mask="FrameMask")
    common = {"Width": Inp(1920), "Height": Inp(1080), "UseFrameFormatSettings": Inp(1),
              "Font": Inp("Open Sans"), "Style": Inp("Bold")}
    dot = Tool("Dot", "TextPlus", {**common, "StyledText": Inp(u"●"), "Size": Inp(0.05),
                                   "Center": Inp(Pt(0.075, 0.9)), "Red1": Inp(1.0), "Green1": Inp(0.1),
                                   "Blue1": Inp(0.1)}, pos=(0, -66))
    rec = Tool("Rec", "TextPlus", {**common, "StyledText": Inp("REC"), "Size": Inp(0.04),
                                   "Center": Inp(Pt(0.125, 0.9))}, pos=(0, -132))
    tc = Tool("Clock", "TextPlus", {
        **common, "Size": Inp(0.032), "Center": Inp(Pt(0.86, 0.9)), "Font": Inp("Courier New"),
        "StyledText": Inp("00:00:00:00", expr='Text(string.format("%02d:%02d:%02d:%02d", '
                                               'floor(time / Clock.FPS / 3600), floor(time / Clock.FPS / 60) % 60, '
                                               'floor(time / Clock.FPS) % 60, floor(time % Clock.FPS)))'),
    }, pos=(0, 66), controls=[UserControl("FPS", "Frame Rate", default=30, lo=1, hi=120)])
    blink = merge("BlinkDot", "Frame", "Dot", pos=(220, -66),
                  blend=Inp(1.0, expr="iif(floor(time / max(1, Clock.FPS / 2)) % 2 == 0, 1, 0)"))
    m2 = merge("WithRec", "BlinkDot", "Rec", pos=(330, -66))
    m3 = merge("WithClock", "WithRec", "Clock", pos=(440, 0))
    return _gen("RF_REC_Frame", [frame_mask, frame, dot, rec, tc, blink, m2, m3],
                [Publish("FPS", "Clock", "FPS", "Frame Rate"),
                 Publish("Label", "Rec", "StyledText", "Label"),
                 Publish("FrameOpacity", "Frame", "TopLeftAlpha", "Frame Opacity")], "WithClock",
                "Camcorder REC frame")


OVERLAYS: Dict[str, Callable[[], Macro]] = {
    "letterbox": letterbox,
    "vignette": vignette,
    "film_grain": film_grain,
    "light_leak": light_leak,
    "rec_frame": rec_frame,
}
