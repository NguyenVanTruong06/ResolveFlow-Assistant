"""
ResolveFlow Kinetic Typography & Fusion Animation Module
========================================================
Integrates AI Director Text Plans, Fusion Comp generation, carrier clip placement,
and natural language agent loops into DaVinci Resolve.
"""

from src.core.kinetic.kinetic_engine import (
    auto_plan,
    generate_kinetic_prompt,
    validate_plan,
    build_pages,
    page_to_comp,
    make_carrier,
    place_on_timeline,
    CANVAS,
    POS_Y,
    DEMO_WORDS,
    DEMO_PLAN,
    Page,
    WordFx,
)

__all__ = [
    "auto_plan",
    "generate_kinetic_prompt",
    "validate_plan",
    "build_pages",
    "page_to_comp",
    "make_carrier",
    "place_on_timeline",
    "CANVAS",
    "POS_Y",
    "DEMO_WORDS",
    "DEMO_PLAN",
    "Page",
    "WordFx",
]
