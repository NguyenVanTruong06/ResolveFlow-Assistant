"""
8 animated Title macros (Edit page > Effects > Titles > Fusion Titles).

Every builder returns a `Macro`; call .to_setting() for the template file or
.to_comp(frames) for a carrier composition with the user's values baked in.
All animation is anchored to the start of the title and passes through the
shared `Timing` (TimeSpeed: Delay/Speed) + `OutFade` tail from common.py.
"""
from __future__ import annotations

from typing import Callable, Dict

from .common import (TitleParams, clamp01, color_publish, ease_in, ease_out, hash01, merge,
                     parse_color, solid, tail, text_publish, text_tool)
from .lua import Inp, Key, Macro, Pt, Publish, Spline, Tool, UserControl, smooth_keys

MB = {"MotionBlur": Inp(1), "Quality": Inp(8), "ShutterAngle": Inp(180), "CenterBias": Inp(0),
      "SampleSpread": Inp(1)}


def _outline(rgb=(0, 0, 0), thickness=0.06) -> dict:
    return {"Enabled2": Inp(1), "Thickness2": Inp(thickness),
            "Red2": Inp(rgb[0]), "Green2": Inp(rgb[1]), "Blue2": Inp(rgb[2]), "Alpha2": Inp(1.0)}


# 1 ---------------------------------------------------------------------------
def spring_pop(p: TitleParams) -> Macro:
    """Hormozi-style pop: Scale 0 -> 1.25 -> 0.95 -> 1.0 on flat-tangent Bezier keys + motion blur."""
    text = text_tool("Text1", p, pos=(0, 0), extra=_outline())
    size_curve = Spline("PopSize", smooth_keys([(0, 0.0), (6, 1.25), (10, 0.95), (14, 1.0)]))
    angle_curve = Spline("PopAngle", smooth_keys([(0, -8.0), (6, 3.0), (10, -1.0), (14, 0.0)]),
                         color=(0, 200, 255))
    pop = Tool("Pop", "Transform", {
        "Input": Inp(src=("Text1", "Output")),
        "Size": Inp(src=("PopSize", "Value")),
        "Angle": Inp(src=("PopAngle", "Value")),
        **MB,
    }, pos=(110, 0))
    tl, tp, out = tail(p, "Pop", 220)
    pubs = text_publish("Text1") + [
        Publish("Outline", "Text1", "Thickness2", "Outline Thickness"),
        Publish("OutlineRed", "Text1", "Red2", "Outline Color", group=20),
        Publish("OutlineGreen", "Text1", "Green2", group=20),
        Publish("OutlineBlue", "Text1", "Blue2", group=20),
        Publish("MotionBlur", "Pop", "MotionBlur", "Motion Blur", page="Animation"),
    ] + tp
    return Macro("RF_Spring_Pop", [text, size_curve, angle_curve, pop] + tl, pubs, out,
                 info={"Style": "Alex Hormozi Spring Pop"})


# 2 ---------------------------------------------------------------------------
def highlighter(p: TitleParams) -> Macro:
    """Marker box (Background + RectangleMask) wipes open left->right behind the text."""
    acc = parse_color(p.accent)
    reveal = Spline("SwipeReveal", [
        Key(0, 0.0, rh=(4, 0.0)),
        Key(12, 1.0, lh=(7, 1.0)),
    ], color=(255, 200, 0))
    mask = Tool("Swipe", "RectangleMask", {
        "Width": Inp(0.0, expr="Swipe.BoxWidth * Swipe.Reveal"),
        "Height": Inp(0.12),
        "Center": Inp(Pt(0.5, 0.5), expr="Point(0.5 - Swipe.BoxWidth / 2 + Swipe.Width / 2, 0.5 + Swipe.YOffset)"),
        "CornerRadius": Inp(0.08), "SoftEdge": Inp(0.002), "Angle": Inp(-1.5),
        "Reveal": Inp(src=("SwipeReveal", "Value")),
    }, pos=(0, -66), controls=[
        UserControl("BoxWidth", "Marker Width", default=0.62, lo=0.05, hi=1.2),
        UserControl("YOffset", "Marker Y Offset", default=-0.005, lo=-0.1, hi=0.1),
        UserControl("Reveal", "Reveal", default=0, hi=1),
    ])
    marker = solid("Marker", acc, 0.92, pos=(110, -66), mask="Swipe")
    text = text_tool("Text1", p, pos=(110, 0), color=parse_color(p.color))
    comp = merge("MarkerText", "Marker", "Text1", pos=(220, 0))
    tl, tp, out = tail(p, "MarkerText", 330)
    pubs = text_publish("Text1") + color_publish("Marker", "Marker", "Marker Color", 30) + [
        Publish("MarkerWidth", "Swipe", "BoxWidth", "Marker Width"),
        Publish("MarkerHeight", "Swipe", "Height", "Marker Height"),
        Publish("MarkerY", "Swipe", "YOffset", "Marker Y Offset"),
        Publish("MarkerTilt", "Swipe", "Angle", "Marker Tilt"),
    ] + tp
    return Macro("RF_Highlighter_Swipe", [reveal, mask, marker, text, comp] + tl, pubs, out,
                 info={"Style": "Highlighter Marker Swipe"})


# 3 ---------------------------------------------------------------------------
_UTF8_CHAR = r"[^\128-\191][\128-\191]*"   # one UTF-8 code point (Vietnamese-safe)


def typewriter(p: TitleParams) -> Macro:
    """
    Character-by-character reveal with a blinking cursor at the typing edge.

    Implemented as a Text+ expression rather than StyledTextFollower on purpose:
    a Follower can fade characters in, but it cannot place a cursor glyph right
    after the last revealed character. The expression slices the string by
    UTF-8 code points, so Vietnamese diacritics never get cut in half.
    """
    n = "floor(max(0, time) / max(0.01, Text1.FramesPerChar))"
    shown = '(string.match(Text1.Content.Value, "^" .. string.rep("%s", %s)) or Text1.Content.Value)' % (
        _UTF8_CHAR, n)
    cursor = "((floor(time / max(1, Text1.BlinkFrames)) % 2 == 0) and Text1.Cursor.Value or \" \")"
    expr = "Text(%s .. %s)" % (shown, cursor)
    text = text_tool("Text1", p, text_value="", pos=(0, 0), extra={
        "StyledText": Inp("", expr=expr),
        "Content": Inp(p.text),
        "Cursor": Inp("|"),
        # left-anchored so the line grows to the right instead of re-centring
        "HorizontalLeftCenterRight": Inp(-1), "HorizontalJustificationNew": Inp(0),
        "Center": Inp(Pt(0.12, 0.5)),
    }, controls=[
        UserControl("Content", "Text", kind="text"),
        UserControl("Cursor", "Cursor Glyph", kind="text"),
        UserControl("FramesPerChar", "Frames per Character", default=2, lo=0.25, hi=10),
        UserControl("BlinkFrames", "Cursor Blink (frames)", default=12, lo=2, hi=30),
    ])
    tl, tp, out = tail(p, "Text1", 110)
    pubs = [
        Publish("Text", "Text1", "Content", "Text"),
        Publish("Font", "Text1", "Font", "Font", group=10),
        Publish("Style", "Text1", "Style", "Style", group=10),
        Publish("Size", "Text1", "Size", "Size"),
        Publish("Red", "Text1", "Red1", "Color", group=11),
        Publish("Green", "Text1", "Green1", group=11),
        Publish("Blue", "Text1", "Blue1", group=11),
        Publish("TextCenter", "Text1", "Center", "Line Start"),
        Publish("Rate", "Text1", "FramesPerChar", "Frames per Character", page="Animation"),
        Publish("Cursor", "Text1", "Cursor", "Cursor Glyph", page="Animation"),
        Publish("Blink", "Text1", "BlinkFrames", "Cursor Blink (frames)", page="Animation"),
    ] + tp
    return Macro("RF_Typewriter_Cursor", [text] + tl, pubs, out,
                 info={"Style": "Typewriter + Blinking Cursor"})


# 4 ---------------------------------------------------------------------------
def neon_pulse(p: TitleParams) -> Macro:
    acc = parse_color(p.accent if p.accent else p.color)
    text = text_tool("Text1", p, pos=(0, 0), color=acc)
    wave = "(0.5 + 0.5 * sin(2 * pi * time / max(1, Glow1.Period)))"
    glow1 = Tool("Glow1", "SoftGlow", {
        "Input": Inp(src=("Text1", "Output")),
        "Threshold": Inp(0.0),
        "Gain": Inp(2.0, expr="Glow1.BaseGain + Glow1.PulseAmount * %s" % wave),
        "XGlowSize": Inp(12.0, expr="Glow1.BaseSize * (0.7 + 0.6 * %s)" % wave),
    }, pos=(110, 0), controls=[
        UserControl("BaseGain", "Glow Gain", default=1.6, hi=6),
        UserControl("PulseAmount", "Pulse Amount", default=1.4, hi=6),
        UserControl("BaseSize", "Glow Size", default=14, lo=1, hi=60),
        UserControl("Period", "Pulse Period (frames)", default=30, lo=4, hi=120),
    ])
    glow2 = Tool("Glow2", "SoftGlow", {
        "Input": Inp(src=("Glow1", "Output")),
        "Threshold": Inp(0.0), "Gain": Inp(0.8), "XGlowSize": Inp(45.0),
    }, pos=(220, 0))
    # neon "tube ignition" flicker during the first 14 frames
    flick = "iif(time < 14, iif(%s > 0.45, 1, 0.12), 1)" % hash01(7.13)
    canvas_on = solid("Dark", (0, 0, 0), 0.0, pos=(330, -66))
    ignite = merge("Ignite", "Dark", "Glow2", pos=(330, 0), blend=Inp(1.0, expr=flick))
    tl, tp, out = tail(p, "Ignite", 440)
    pubs = text_publish("Text1", "Text") + [
        Publish("Gain", "Glow1", "BaseGain", "Glow Gain", page="Glow"),
        Publish("Pulse", "Glow1", "PulseAmount", "Pulse Amount", page="Glow"),
        Publish("GlowSize", "Glow1", "BaseSize", "Glow Size", page="Glow"),
        Publish("Period", "Glow1", "Period", "Pulse Period (frames)", page="Glow"),
        Publish("Halo", "Glow2", "Gain", "Outer Halo", page="Glow"),
    ] + tp
    return Macro("RF_Neon_Glow_Pulse", [text, glow1, glow2, canvas_on, ignite] + tl, pubs, out,
                 info={"Style": "Neon Glow Pulse"})


# 5 ---------------------------------------------------------------------------
def rgb_glitch(p: TitleParams) -> Macro:
    """Three pure R/G/B instanced copies, jittered independently for the first N frames."""
    text = text_tool("Text1", p, pos=(0, 0))
    nodes = [text]
    chans = [("R", (1, 0, 0), 1.7), ("G", (0, 1, 0), 3.1), ("B", (0, 0, 1), 5.3)]
    prev = None
    x = 110
    active = "iif(time < Glitch.Frames, 1, 0)"
    for tag, rgb, seed in chans:
        inst = Tool("Text" + tag, "TextPlus", {
            "Red1": Inp(float(rgb[0])), "Green1": Inp(float(rgb[1])), "Blue1": Inp(float(rgb[2])),
        }, pos=(x, -66 * (len(nodes))), instance_of="Text1")
        jit = ("Point(0.5 + %s * Glitch.Amount * (%s - 0.5), 0.5 + %s * Glitch.Amount * 0.25 * (%s - 0.5))"
               % (active, hash01(seed), active, hash01(seed + 11.0)))
        xf = Tool("Xf" + tag, "Transform", {
            "Input": Inp(src=("Text" + tag, "Output")),
            "Center": Inp(Pt(0.5, 0.5), expr=jit),
        }, pos=(x + 110, -66 * len(nodes)))
        nodes += [inst, xf]
        if prev is None:
            prev = "Xf" + tag
        else:
            m = merge("Add" + tag, prev, "Xf" + tag, pos=(x + 220, -66 * len(nodes)), mode="Screen")
            nodes.append(m)
            prev = "Add" + tag
    main_jit = ("Point(0.5 + %s * Glitch.Amount * 0.4 * (%s - 0.5), 0.5)" % (active, hash01(9.7)))
    glitch = Tool("Glitch", "Transform", {
        "Input": Inp(src=("Text1", "Output")),
        "Center": Inp(Pt(0.5, 0.5), expr=main_jit),
    }, pos=(330, 0), controls=[
        UserControl("Frames", "Glitch Frames", default=8, lo=0, hi=48),
        UserControl("Amount", "Glitch Amount", default=0.08, hi=0.4),
    ])
    # main layer drops out on random frames so the split channels read clearly
    drop = "iif(time < Glitch.Frames, iif(%s > 0.5, 1, 0), 1)" % hash01(4.2)
    top = merge("Split", prev, "Glitch", pos=(440, 0), blend=Inp(1.0, expr=drop))
    nodes += [glitch, top]
    tl, tp, out = tail(p, "Split", 550)
    pubs = text_publish("Text1") + [
        Publish("Frames", "Glitch", "Frames", "Glitch Frames", page="Animation"),
        Publish("Amount", "Glitch", "Amount", "Glitch Amount", page="Animation"),
    ] + tp
    return Macro("RF_Cyberpunk_RGB_Glitch", nodes + tl, pubs, out,
                 info={"Style": "Cyberpunk RGB Split Glitch"})


# 6 ---------------------------------------------------------------------------
def paper_cutout(p: TitleParams) -> Macro:
    """Paper backing (thick outline instance) with noise-torn edges + on-twos stepped jitter."""
    paper_rgb = parse_color(p.accent) if p.accent else (1, 1, 1)
    text = text_tool("Text1", p, pos=(0, 0), color=parse_color(p.color))
    paper = Tool("PaperShape", "TextPlus", {
        # fill follows the outline colour so one Inspector swatch drives the whole card
        "Red1": Inp(paper_rgb[0], expr="PaperShape.Red2"),
        "Green1": Inp(paper_rgb[1], expr="PaperShape.Green2"),
        "Blue1": Inp(paper_rgb[2], expr="PaperShape.Blue2"),
        **_outline(paper_rgb, 0.16),
    }, pos=(0, -66), instance_of="Text1")
    noise = Tool("TearNoise", "FastNoise", {
        "Width": Inp(1920), "Height": Inp(1080), "UseFrameFormatSettings": Inp(1),
        "Detail": Inp(6.0), "Contrast": Inp(2.2), "XScale": Inp(55.0),
    }, pos=(0, -132))
    torn = Tool("Torn", "Displace", {
        "Input": Inp(src=("PaperShape", "Output")),
        "Foreground": Inp(src=("TearNoise", "Output")),
        "Type": Inp(1), "XRefraction": Inp(0.006), "YRefraction": Inp(0.006),
    }, pos=(110, -66))
    shadow_off = Tool("ShadowOff", "Transform", {
        "Input": Inp(src=("Torn", "Output")), "Center": Inp(Pt(0.506, 0.492)),
    }, pos=(220, -132))
    shadow_dark = Tool("ShadowTint", "BrightnessContrast", {
        "Input": Inp(src=("ShadowOff", "Output")), "Gain": Inp(0.0), "Alpha": Inp(0),
    }, pos=(330, -132))
    sh = merge("PaperShadow", "ShadowTint", "Torn", pos=(330, -66))
    card = merge("Card", "PaperShadow", "Text1", pos=(440, 0))
    hold = "Jitter.Hold"
    jitter = Tool("Jitter", "Transform", {
        "Input": Inp(src=("Card", "Output")),
        "Angle": Inp(0.0, expr="Jitter.Wobble * (%s - 0.5) * 2" % hash01(3.3, hold)),
        "Center": Inp(Pt(0.5, 0.5), expr="Point(0.5 + Jitter.Shake * (%s - 0.5), 0.5 + Jitter.Shake * (%s - 0.5))"
                     % (hash01(5.1, hold), hash01(8.9, hold))),
        # stop-motion entrance: 0 -> 1.15 -> 1 in held steps
        "Size": Inp(1.0, expr="iif(time < 2*%s, 0.4, iif(time < 4*%s, 1.15, 1))" % (hold, hold)),
    }, pos=(550, 0), controls=[
        UserControl("Hold", "Step (hold frames)", default=3, lo=1, hi=8),
        UserControl("Wobble", "Rotation Jitter (deg)", default=3.0, hi=15),
        UserControl("Shake", "Position Jitter", default=0.004, hi=0.03),
    ])
    tl, tp, out = tail(p, "Jitter", 660)
    pubs = text_publish("Text1") + color_publish("Paper", "PaperShape", "Paper Color", 30, prefix="") + [
        Publish("PaperBorder", "PaperShape", "Thickness2", "Paper Border"),
        Publish("Tear", "Torn", "XRefraction", "Edge Roughness"),
        Publish("Hold", "Jitter", "Hold", "Step (hold frames)", page="Animation"),
        Publish("Wobble", "Jitter", "Wobble", "Rotation Jitter", page="Animation"),
        Publish("Shake", "Jitter", "Shake", "Position Jitter", page="Animation"),
    ] + tp
    # color_publish with empty prefix targets Red/Green/Blue: remap to Text+ element 1+2
    for pub in pubs:
        if pub.tool == "PaperShape" and pub.source in ("Red", "Green", "Blue"):
            pub.source = pub.source + "2"
    nodes = [text, paper, noise, torn, shadow_off, shadow_dark, sh, card, jitter]
    return Macro("RF_Paper_Cutout_Jitter", nodes + tl, pubs, out,
                 info={"Style": "Paper Cutout / Stop-motion Jitter"})


# 7 ---------------------------------------------------------------------------
def lower_third(p: TitleParams) -> Macro:
    acc = parse_color(p.accent)
    grow = ease_out("time / 12")
    line_mask = Tool("LineMask", "RectangleMask", {
        "Width": Inp(0.0, expr="LineMask.LineWidth * %s" % grow),
        "Height": Inp(0.004), "Center": Inp(Pt(0.5, 0.5)), "SoftEdge": Inp(0.0),
    }, pos=(0, -132), controls=[
        UserControl("LineWidth", "Line Width", default=0.42, lo=0.05, hi=1.0),
        UserControl("Gap", "Text Gap", default=0.045, lo=0.0, hi=0.15),
    ])
    line = solid("Line", acc, 1.0, pos=(110, -132), mask="LineMask")
    up = ease_out("(time - 6) / 14")
    down = ease_out("(time - 9) / 14")
    tmask = Tool("TitleMask", "RectangleMask", {
        "Center": Inp(Pt(0.5, 0.65)), "Width": Inp(2.0), "Height": Inp(0.3), "SoftEdge": Inp(0.0)},
        pos=(0, -66))
    smask = Tool("SubMask", "RectangleMask", {
        "Center": Inp(Pt(0.5, 0.35)), "Width": Inp(2.0), "Height": Inp(0.3), "SoftEdge": Inp(0.0)},
        pos=(0, 66))
    title = text_tool("Title", p, pos=(110, -66), size=p.size * 0.75, extra={
        "Center": Inp(Pt(0.5, 0.5), expr="Point(0.5, 0.5 - 0.06 + (0.06 + LineMask.Gap) * %s)" % up),
        "EffectMask": Inp(src=("TitleMask", "Mask")),
    })
    sub = text_tool("Subtitle", p, pos=(110, 66), text_value=p.subtitle, size=p.size * 0.42,
                    color=(0.85, 0.85, 0.85), extra={
        "Style": Inp("Regular"),
        "Center": Inp(Pt(0.5, 0.5), expr="Point(0.5, 0.5 + 0.05 - (0.05 + LineMask.Gap * 0.8) * %s)" % down),
        "EffectMask": Inp(src=("SubMask", "Mask")),
    })
    m1 = merge("LineTitle", "Line", "Title", pos=(220, -66))
    m2 = merge("LineTitleSub", "LineTitle", "Subtitle", pos=(330, 0))
    p2 = TitleParams(**{**p.to_dict(), "position": p.position if p.position != (0.5, 0.5) else (0.5, 0.2)})
    tl, tp, out = tail(p2, "LineTitleSub", 440)
    pubs = (text_publish("Title", "Title", "") + text_publish("Subtitle", "Subtitle", "Sub", group_base=40)
            + color_publish("Line", "Line", "Line Color", 30)
            + [Publish("LineWidth", "LineMask", "LineWidth", "Line Width"),
               Publish("LineThickness", "LineMask", "Height", "Line Thickness"),
               Publish("Gap", "LineMask", "Gap", "Text Gap")] + tp)
    nodes = [line_mask, line, tmask, smask, title, sub, m1, m2]
    return Macro("RF_Cinematic_Lower_Third", nodes + tl, pubs, out,
                 info={"Style": "Cinematic Lower Third"})


# 8 ---------------------------------------------------------------------------
def quote_box(p: TitleParams) -> Macro:
    acc = parse_color(p.accent)
    box_mask = Tool("BoxMask", "RectangleMask", {
        "Center": Inp(Pt(0.5, 0.5)), "Width": Inp(0.62), "Height": Inp(0.3),
        "CornerRadius": Inp(0.12), "SoftEdge": Inp(0.0),
    }, pos=(0, -132))
    border_mask = Tool("BorderMask", "RectangleMask", {
        "Center": Inp(Pt(0.5, 0.5), expr="BoxMask.Center"),
        "Width": Inp(0.62, expr="BoxMask.Width"), "Height": Inp(0.3, expr="BoxMask.Height"),
        "CornerRadius": Inp(0.12, expr="BoxMask.CornerRadius"),
        "Solid": Inp(0), "BorderWidth": Inp(0.0025), "SoftEdge": Inp(0.0),
    }, pos=(0, -198))
    box = solid("Box", (0.06, 0.06, 0.07), 0.72, pos=(110, -132), mask="BoxMask")
    border = solid("Border", acc, 1.0, pos=(110, -198), mask="BorderMask")
    quote = text_tool("Quote", p, pos=(110, -66), size=p.size * 0.55,
                      text_value=u"“" + p.text + u"”",
                      extra={"Style": Inp("Italic"), "Center": Inp(Pt(0.5, 0.53))})
    author = text_tool("Author", p, pos=(110, 0), size=p.size * 0.32, text_value=u"— " + p.subtitle,
                       color=acc, extra={"Style": Inp("Regular"), "Center": Inp(Pt(0.5, 0.42))})
    m1 = merge("BoxBorder", "Box", "Border", pos=(220, -132))
    m2 = merge("BoxQuote", "BoxBorder", "Quote", pos=(330, -66))
    m3 = merge("BoxAll", "BoxQuote", "Author", pos=(440, 0))
    scale = Tool("GentleScale", "Transform", {
        "Input": Inp(src=("BoxAll", "Output")),
        "Size": Inp(1.0, expr="GentleScale.From + (1 - GentleScale.From) * %s" % ease_out("time / max(1, GentleScale.Frames)")),
    }, pos=(550, 0), controls=[
        UserControl("From", "Start Scale", default=0.92, lo=0.5, hi=1.0),
        UserControl("Frames", "Scale Duration", default=20, lo=1, hi=90),
    ])
    p2 = TitleParams(**{**p.to_dict(), "fade_in_frames": p.fade_in_frames or 15})
    tl, tp, out = tail(p2, "GentleScale", 660)
    pubs = (text_publish("Quote", "Quote") + text_publish("Author", "Author", "Author", group_base=40) + [
        Publish("BoxWidth", "BoxMask", "Width", "Box Width"),
        Publish("BoxHeight", "BoxMask", "Height", "Box Height"),
        Publish("Corner", "BoxMask", "CornerRadius", "Corner Radius"),
        Publish("BorderWidth", "BorderMask", "BorderWidth", "Border Width"),
        Publish("BoxAlpha", "Box", "TopLeftAlpha", "Box Opacity"),
    ] + color_publish("Border", "Border", "Border Color", 30)
        + [Publish("StartScale", "GentleScale", "From", "Start Scale", page="Animation"),
           Publish("ScaleFrames", "GentleScale", "Frames", "Scale Duration", page="Animation")] + tp)
    nodes = [box_mask, border_mask, box, border, quote, author, m1, m2, m3, scale]
    return Macro("RF_Minimal_Quote_Box", nodes + tl, pubs, out, info={"Style": "Minimalist Quote Box"})


TITLES: Dict[str, Callable[[TitleParams], Macro]] = {
    "spring_pop": spring_pop,
    "highlighter": highlighter,
    "typewriter": typewriter,
    "neon_pulse": neon_pulse,
    "rgb_glitch": rgb_glitch,
    "paper_cutout": paper_cutout,
    "lower_third": lower_third,
    "quote_box": quote_box,
}
