"""Public API for generating Fusion templates."""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Union

from .common import TitleParams
from .lua import Macro
from .overlays import OVERLAYS
from .titles import TITLES
from .transitions import TRANSITIONS

__all__ = ["TitleParams", "build_title", "export_title_setting", "export_title_comp",
           "build_library", "TITLES", "TRANSITIONS", "OVERLAYS", "template_name"]

# Folder names under  .../Fusion/Templates/Edit/
CATEGORY_DIR = {"title": "Titles", "transition": "Transitions", "generator": "Generators"}


def _params(p: Union[TitleParams, dict, None]) -> TitleParams:
    if p is None:
        return TitleParams()
    if isinstance(p, dict):
        return TitleParams.from_dict(p)
    return p


def build_title(style: str, params: Union[TitleParams, dict, None] = None) -> Macro:
    if style not in TITLES:
        raise KeyError("Unknown title style %r. Options: %s" % (style, ", ".join(TITLES)))
    return TITLES[style](_params(params))


def template_name(style: str) -> str:
    """Name Resolve shows in the Effects Library (= .setting file stem)."""
    return build_title(style).name


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def export_title_setting(style: str, params: Union[TitleParams, dict, None] = None,
                         out_dir: str = ".", file_stem: Optional[str] = None) -> str:
    """Read params (text, font, size, color, duration ...) -> animated .setting file."""
    m = build_title(style, params)
    return _write(os.path.join(out_dir, (file_stem or m.name) + ".setting"), m.to_setting())


def export_title_comp(style: str, params: Union[TitleParams, dict, None], path: str,
                      width: int = 1920, height: int = 1080, fps: float = 30.0) -> str:
    """Same title as a standalone .comp (macro -> MediaOut1) for TimelineItem.ImportFusionComp."""
    p = _params(params)
    m = build_title(style, p)
    return _write(path, m.to_comp(p.duration_frames, width, height, fps))


def build_library(root: str) -> Dict[str, List[str]]:
    """Write every template into root/Titles, root/Transitions, root/Generators."""
    written: Dict[str, List[str]] = {"Titles": [], "Transitions": [], "Generators": []}
    for style in TITLES:
        written["Titles"].append(export_title_setting(style, None, os.path.join(root, "Titles")))
    for fn in TRANSITIONS.values():
        m = fn()
        written["Transitions"].append(_write(os.path.join(root, "Transitions", m.name + ".setting"),
                                             m.to_setting()))
    for fn in OVERLAYS.values():
        m = fn()
        written["Generators"].append(_write(os.path.join(root, "Generators", m.name + ".setting"),
                                            m.to_setting()))
    return written
