"""Raster hardware skin: 9-slice panels and state sprites.

This package is the ONLY part of the codebase that touches PNG pixels. It sits
entirely on the render side of the architecture: nothing here is reachable from
telemetry, physiology or organism code, and nothing here can influence
simulation state.
"""

from .surface import (
    NineSlice,
    SkinUnavailable,
    draw_sprite,
    draw_sprite_fit,
    nine_surface,
    sprite,
    sprite_size,
    skin_stats,
)

__all__ = [
    "NineSlice",
    "SkinUnavailable",
    "draw_sprite",
    "draw_sprite_fit",
    "nine_surface",
    "sprite",
    "sprite_size",
    "skin_stats",
]
