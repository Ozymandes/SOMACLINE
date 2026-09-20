"""The specimen selector bank: geometry, drawing and hit testing.

This is a PHYSICAL control, not a tab bar. Five latching console keys in a
machined fascia, one per specimen channel.

SEPARATION
----------
`layout()` is a pure function from a rect to key rectangles. It allocates
nothing and touches no pixels, so the same call serves three callers that must
never disagree:

    * drawing          - where to blit each key state
    * hit testing      - which key the pointer is over
    * label placement  - where chrome draws the engraved specimen names

Deriving all three from one function is what stops the visual bank and the
clickable bank drifting apart.

STATE
-----
Each key resolves to exactly one sprite state, in priority order:

    disabled > pressed > latched (this is the live specimen) > focus > idle

`pressed` is momentary and lives in the UI, not the model: it is a timestamp
that decays, so a click always produces a visible mechanical response even if
the switch itself completes in the same frame.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.layout import Rect
from ..skin import catalog as C
from ..skin.surface import draw_sprite, sprite_size

#: Natural proportions of the assembled bank, from tools/build_sprites.py.
#: cap_left(48) + 5 * cell(224) + cap_right(46) by cell height(303).
_CAP_L_W, _CAP_R_W, _CELL_W, _CELL_H = 48.0, 46.0, 224.0, 303.0


def natural_aspect(n: int = 5) -> float:
    return (_CAP_L_W + n * _CELL_W + _CAP_R_W) / _CELL_H


@dataclass(frozen=True, slots=True)
class BankGeometry:
    """Where the bank and every key landed, in widget pixels."""

    x: float
    y: float
    w: float
    h: float
    cap_l: float
    cap_r: float
    cell_w: float
    n: int

    def key_rect(self, i: int) -> Rect:
        return Rect(self.x + self.cap_l + i * self.cell_w, self.y,
                    self.cell_w, self.h)

    def label_rect(self, i: int) -> Rect:
        """The engraved label ledge above a key, in bank-relative proportions.

        Measured off the master: the ledge occupies the top ~14% of a cell and
        is inset from the divider ribs.
        """
        k = self.key_rect(i)
        return Rect(k.x + k.w * 0.10, k.y + k.h * 0.045,
                    k.w * 0.80, k.h * 0.105)

    def face_rect(self, i: int) -> Rect:
        """The keycap face, where a channel index may be drawn."""
        k = self.key_rect(i)
        return Rect(k.x + k.w * 0.10, k.y + k.h * 0.22,
                    k.w * 0.80, k.h * 0.42)

    @property
    def valid(self) -> bool:
        return self.w > 8.0 and self.h > 6.0 and self.cell_w > 2.0


def layout(box: Rect, n: int = 5) -> BankGeometry:
    """Fit the bank inside `box`, preserving its natural aspect, centred.

    Pure. No allocation beyond the returned value, no pixel access.
    """
    aspect = natural_aspect(n)
    w = box.w
    h = w / aspect
    if h > box.h:
        h = box.h
        w = h * aspect
    x = box.x + (box.w - w) * 0.5
    y = box.y + (box.h - h) * 0.5
    k = h / _CELL_H
    cap_l = _CAP_L_W * k
    cap_r = _CAP_R_W * k
    cell_w = (w - cap_l - cap_r) / n
    return BankGeometry(x=x, y=y, w=w, h=h, cap_l=cap_l, cap_r=cap_r,
                        cell_w=cell_w, n=n)


def hit(geo: BankGeometry, px: float, py: float) -> int | None:
    """Index of the key under (px, py), or None.

    Only the key cells are live; the end caps are structure, not controls.
    """
    if not geo.valid:
        return None
    if not (geo.y <= py <= geo.y + geo.h):
        return None
    rel = px - (geo.x + geo.cap_l)
    if rel < 0.0:
        return None
    i = int(rel // geo.cell_w)
    return i if 0 <= i < geo.n else None


def key_state(i: int, active: int, pressed: int | None,
              focus: int | None, disabled: frozenset[int] = frozenset()) -> str:
    """Resolve one key to a sprite state. Order matters; see module docstring."""
    if i in disabled:
        return "disabled"
    if pressed == i:
        return "pressed"
    if i == active:
        return "latched"
    if focus == i:
        return "focus"
    return "idle"


def draw(cr, geo: BankGeometry, active: int, pressed: int | None = None,
         focus: int | None = None, disabled: frozenset[int] = frozenset(),
         alpha: float = 1.0) -> None:
    """Blit the bank: left cap, one cell per channel, right cap."""
    if not geo.valid:
        return
    if sprite_size(C.SELECTOR_CAP_L)[0]:
        draw_sprite(cr, C.SELECTOR_CAP_L, geo.x, geo.y, geo.cap_l, geo.h, alpha)
    for i in range(geo.n):
        st = key_state(i, active, pressed, focus, disabled)
        r = geo.key_rect(i)
        # Cells are butted edge to edge; rounding each independently would
        # open one-pixel seams between them at some sizes.
        x0 = round(r.x)
        x1 = round(r.x + r.w)
        draw_sprite(cr, C.selector_cell(st), x0, geo.y, x1 - x0, geo.h, alpha)
    if sprite_size(C.SELECTOR_CAP_R)[0]:
        draw_sprite(cr, C.SELECTOR_CAP_R,
                    geo.x + geo.cap_l + geo.n * geo.cell_w, geo.y,
                    geo.cap_r, geo.h, alpha)


def lamp_point(geo: BankGeometry, i: int) -> tuple[float, float, float]:
    """Centre and radius of a key's indicator aperture, for the lighting pass."""
    k = geo.key_rect(i)
    return (k.cx, k.y + k.h * 0.855, k.h * 0.075)
