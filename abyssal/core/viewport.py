"""The ONE place where logical world units become device pixels.

RESIZE CONTRACT
---------------
A resize produces a new Viewport value and nothing else. It must not:
  * rebuild organism geometry
  * reset or re-seed simulation state
  * allocate or free any buffer, texture or surface
  * reparent, hide or destroy anything

Viewport is an immutable value object. Constructing one is a handful of
floating point operations, so it is created fresh on every single frame; there
is no cache to invalidate and therefore no cache to get wrong.

Scale is UNIFORM on both axes by construction (a single float), so a circle in
world space is a circle on screen at every window size. There is no code path
in this project that can produce a non-uniform scale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .world import WORLD_RADIUS, WORLD_SIZE


@dataclass(frozen=True, slots=True)
class Viewport:
    """Maps world units -> widget pixels for one rectangular stage."""

    # The stage this viewport fills, in widget pixels.
    stage_x: float
    stage_y: float
    stage_w: float
    stage_h: float

    # Uniform world-units -> pixels factor, and the pixel centre of the stage.
    scale: float
    cx: float
    cy: float

    @staticmethod
    def for_stage(x: float, y: float, w: float, h: float) -> "Viewport":
        """Fit the WORLD_SIZE square into the stage rect, letterboxed, centred.

        Uses min(w, h) so the world square is never stretched. The organism is
        centred in the stage, which keeps all four lobes symmetric about the
        stage centre regardless of the stage's aspect ratio.
        """
        w = max(float(w), 1.0)
        h = max(float(h), 1.0)
        scale = min(w, h) / WORLD_SIZE
        return Viewport(
            stage_x=float(x),
            stage_y=float(y),
            stage_w=w,
            stage_h=h,
            scale=scale,
            cx=float(x) + w / 2.0,
            cy=float(y) + h / 2.0,
        )

    # -- conversions ------------------------------------------------------
    def px(self, wx: float, wy: float) -> tuple[float, float]:
        """World point -> widget pixel point."""
        return (self.cx + wx * self.scale, self.cy + wy * self.scale)

    def length(self, world_len: float) -> float:
        """World length -> pixel length (same on both axes, by contract)."""
        return world_len * self.scale

    # -- diagnostics ------------------------------------------------------
    @property
    def organism_px_radius(self) -> float:
        return WORLD_RADIUS * self.scale

    @property
    def clips(self) -> bool:
        """True if the organism's design radius cannot fit in the stage."""
        r = self.organism_px_radius
        return (r > self.stage_w / 2.0 + 0.5) or (r > self.stage_h / 2.0 + 0.5)

    def describe(self) -> str:
        return (
            f"stage {self.stage_w:.0f}x{self.stage_h:.0f}"
            f" @{self.stage_x:.0f},{self.stage_y:.0f}"
            f"  scale {self.scale:.4f}"
            f"  r {self.organism_px_radius:.0f}px"
        )


def isotropy_error(vp: Viewport) -> float:
    """GATE 1 helper: max deviation from circularity of a unit world circle.

    Returns 0.0 for a correct viewport. Any non-zero value is a bug.
    """
    pts = [vp.px(math.cos(t) * 100.0, math.sin(t) * 100.0) for t in
           [i * math.tau / 64 for i in range(64)]]
    radii = [math.hypot(px - vp.cx, py - vp.cy) for px, py in pts]
    return max(radii) - min(radii)
