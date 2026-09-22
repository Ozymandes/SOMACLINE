"""The specimen selector: five engraved creature keys in a mounting trough.

WHAT THIS IS
------------
Not a tab bar, and no longer a bank of generic mode switches. Each key is a
bespoke console key whose face carries the ENGRAVED MORPHOLOGY of one of the
five mathematical organisms, in two authored states: raised/unlit and
seated/illuminated. The artwork is the specimen's identity, so it is never
tinted, recoloured or redrawn at runtime - selecting a specimen swaps to that
key's own active plate.

THE MOUNTING IS DELIBERATELY QUIET
----------------------------------
The keys are the hero. Everything around them is the minimum structure needed
to make them read as installed rather than pasted on: one dark recessed
trough, a thin backing rail, a seat shadow under each key, hairline divider
ribs, and an engraved channel identifier beneath. No second bezel, no
competing housing.

SEPARATION
----------
`layout()` is a pure function from a rect to key rectangles. It allocates
nothing and touches no pixels, so the same call serves three callers that must
never disagree:

    * drawing          - where to blit each key plate
    * hit testing      - which key the pointer is over
    * label placement  - where the engraved channel identifiers go

Deriving all three from one function is what stops the visible bank and the
clickable bank drifting apart.

SCALING
-------
These are raster hardware plates. They are drawn with ONE uniform scale factor
and their authored aspect, always. A row wider than the keys need is resolved
by spacing and centring, never by stretching.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.layout import Rect
from ..skin import catalog as C
from ..skin.surface import draw_sprite, sprite_size

#: Authored proportions of one key plate (tools/build_sprites.py emits 256 x
#: 267). Kept as a constant so layout can size the bank without loading pixels.
KEY_ASPECT = 256.0 / 267.0

#: Gap between adjacent keys, as a share of key width. Enough air that each
#: engraving reads as its own object, tight enough that the five read as one
#: instrument.
_GAP_SHARE = 0.17

#: Height of the engraved identifier ledge beneath the keys, as a share of key
#: height, and the floor below which it is dropped rather than crushed.
_LEDGE_SHARE = 0.145
_LEDGE_MIN = 8.0

#: Wall of the mounting trough, as a share of its height.
_WALL = 0.055

#: Share of the trough width the five keys may occupy. The remainder is the
#: metal at each end that seats the auxiliary controls, which keeps the five
#: specimen keys CENTRED on the observation glass above them.
_SPAN_SHARE = 0.78

#: Auxiliary control heights, as a share of key height, and their authored
#: aspects (tools/build_sprites.py emits 160x226 and 192x113).
_MODE_H = 0.55
_CYCLE_H = 0.33
_MODE_ASPECT = 160.0 / 226.0
_CYCLE_ASPECT = 192.0 / 113.0

#: Mechanical travel of a pressed key, as a share of key height.
PRESS_TRAVEL = 0.022

#: The keys' seat, measured off the SELECTOR module at its authored size.
#: The trough interior runs y 27..235 (source px), the aperture hole
#: 65.5..203.5, the key bay 36.0..204.0 and the identifier ledge 207.5.
#: At bay height a key left 10.6 px of dark trough above it and only 4.8 px
#: below before the ledge divider - it read pushed UP out of its well.
#: SEAT_DROP is one shared downward translation, in key heights, of half
#: that difference, which balances the two reveals (7.6 above, 7.8 below)
#: while keeping the key's shadow clear of the ledge band its diagnostics
#: labels live in. One shared transform: equal scale, equal baseline and
#: equal pitch are untouched.
SEAT_DROP = 3.0 / 168.0


@dataclass(frozen=True, slots=True)
class BankGeometry:
    """Where the trough, every key and every identifier landed, in pixels."""

    x: float
    y: float
    w: float
    h: float
    key_x: float          # left edge of key 0
    key_y: float
    key_w: float
    key_h: float
    pitch: float          # key_w + gap
    ledge: float          # height of the identifier ledge, 0 if dropped
    n: int
    #: Auxiliary seats at each end of the trough. Invalid when there is no
    #: metal to spare, in which case those controls simply are not fitted.
    aux_l: Rect = Rect(0.0, 0.0, 0.0, 0.0)
    aux_r: Rect = Rect(0.0, 0.0, 0.0, 0.0)
    #: Top of the engraved label ledge when the mounting plate provides one
    #: below the key (the six-module selector); 0 means directly under it.
    ledge_y: float = 0.0

    def key_rect(self, i: int) -> Rect:
        return Rect(self.key_x + i * self.pitch, self.key_y,
                    self.key_w, self.key_h)

    def label_rect(self, i: int) -> Rect:
        """The engraved identifier ledge beneath key `i`."""
        k = self.key_rect(i)
        return Rect(k.x, self.ledge_y or k.bottom, k.w, self.ledge)

    def lamp_point(self, i: int) -> tuple[float, float, float]:
        """Centre and radius of the key's illuminated strip, for the light pass.

        The authored active plate lights a bar across the top of the key; the
        lighting pass only adds the faint catch that bar would throw onto the
        metal immediately around it.
        """
        k = self.key_rect(i)
        return (k.cx, k.y + k.h * 0.13, k.w * 0.30)

    @property
    def keys_rect(self) -> Rect:
        """The bounding box of the five keys, for seating the backing rail."""
        return Rect(self.key_x, self.key_y,
                    self.pitch * (self.n - 1) + self.key_w, self.key_h)

    @property
    def valid(self) -> bool:
        return self.w > 8.0 and self.key_w > 6.0 and self.key_h > 6.0


def from_module(P, n: int = 5) -> BankGeometry:
    """Key geometry from the placed SELECTOR module's own trued wells. Pure.

    The module's well region scales by ONE factor (its stretch bands lie
    outside the wells), so the keys keep their authored aspect and one exact
    pitch by construction.
    """
    k0 = P.bay("key_0")
    k1 = P.bay("key_1")
    ledge = P.bay("ledge_0")
    if not k0.valid:
        return layout(Rect(0.0, 0.0, 0.0, 0.0), n)
    lh = ledge.h if ledge.h >= _LEDGE_MIN else 0.0
    r = P.rect
    # The measured seat: every key sits SEAT_DROP of its own height lower in
    # its well (see the constant above). Hit testing, drawing and labels all
    # read key_rect, so one field keeps them agreed.
    return BankGeometry(x=r.x, y=r.y, w=r.w, h=r.h,
                        key_x=k0.x, key_y=k0.y + k0.h * SEAT_DROP,
                        key_w=k0.w, key_h=k0.h,
                        pitch=k1.x - k0.x, ledge=lh, n=n,
                        aux_l=P.bay("rocker"), aux_r=P.bay("mode"),
                        ledge_y=ledge.y + (ledge.h - lh) * 0.5)


def natural_aspect(n: int = 5) -> float:
    """Trough width / trough height at the bank's natural proportions.

    Used by `core.layout` to size the control row from the stage width without
    loading a single pixel, so layout stays a pure function of the widget size.
    """
    inner = 1.0 - 2.0 * _WALL
    key_h = inner * (1.0 - _LEDGE_SHARE)
    key_w = key_h * KEY_ASPECT
    span = n * key_w + (n - 1) * key_w * _GAP_SHARE
    return span / _SPAN_SHARE


def layout(box: Rect, n: int = 5) -> BankGeometry:
    """Fit the bank inside `box`, aspect-true and centred. Pure.

    Height is the binding constraint: the keys take the height they are given
    and the row is centred in whatever width is available. A box wider than the
    keys need leaves metal at both ends, which is what the trough is for - it
    is never spent stretching a raster plate.
    """
    if box.w < 12.0 or box.h < 10.0:
        return BankGeometry(box.x, box.y, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                            0.0, 0.0, n)
    wall = max(2.0, box.h * _WALL)
    inner_h = box.h - wall * 2.0
    inner_w = box.w - wall * 2.0

    ledge = inner_h * _LEDGE_SHARE
    if ledge < _LEDGE_MIN:
        ledge = 0.0
    key_h = inner_h - ledge
    key_w = key_h * KEY_ASPECT
    gap = key_w * _GAP_SHARE
    span = n * key_w + (n - 1) * gap

    budget = min(inner_w, box.w * _SPAN_SHARE)
    if span > budget:
        # Too narrow at this height: shrink the keys uniformly on BOTH axes so
        # the authored plate is never squeezed horizontally.
        k = budget / span
        key_w *= k
        key_h *= k
        gap *= k
        ledge *= k
        if ledge < _LEDGE_MIN:
            ledge = 0.0
        span = budget

    key_x = box.x + (box.w - span) * 0.5
    key_y = box.y + (box.h - (key_h + ledge)) * 0.5 + key_h * SEAT_DROP

    # Auxiliary seats in the metal left over at each end.
    end = key_x - box.x - wall
    cy = key_y + key_h * 0.5
    cyc_h = key_h * _CYCLE_H
    cyc_w = cyc_h * _CYCLE_ASPECT
    mode_h = key_h * _MODE_H
    mode_w = mode_h * _MODE_ASPECT
    aux_l = (Rect(box.x + wall + (end - cyc_w) * 0.5, cy - cyc_h * 0.5,
                  cyc_w, cyc_h)
             if end > cyc_w * 1.20 else Rect(0.0, 0.0, 0.0, 0.0))
    aux_r = (Rect(box.right - wall - (end + mode_w) * 0.5, cy - mode_h * 0.5,
                  mode_w, mode_h)
             if end > mode_w * 1.60 else Rect(0.0, 0.0, 0.0, 0.0))

    return BankGeometry(x=box.x, y=box.y, w=box.w, h=box.h,
                        key_x=key_x, key_y=key_y, key_w=key_w, key_h=key_h,
                        pitch=key_w + gap, ledge=ledge, n=n,
                        aux_l=aux_l, aux_r=aux_r)


def hit(geo: BankGeometry, px: float, py: float) -> int | None:
    """Index of the key under (px, py), or None.

    Tests the KEY PLATES, not the trough: the mounting is structure, not a
    control, so the clickable area is exactly what the eye sees as a button.
    """
    if not geo.valid:
        return None
    if not (geo.key_y <= py <= geo.key_y + geo.key_h):
        return None
    rel = px - geo.key_x
    if rel < 0.0:
        return None
    i = int(rel // geo.pitch)
    if not (0 <= i < geo.n):
        return None
    # Reject the gap between two keys: the pitch includes it.
    if rel - i * geo.pitch > geo.key_w:
        return None
    return i


def key_state(i: int, active: int, pressed: int | None,
              focus: int | None, disabled: frozenset[int] = frozenset()) -> str:
    """Resolve one key to a plate. Order matters.

    disabled > pressed > latched (the live specimen) > focus > idle.
    `pressed` and `latched` both show the ILLUMINATED plate - a key being
    pressed is a key being selected - and they differ by mechanical travel,
    which is drawn, not painted into the artwork.
    """
    if i in disabled:
        return "disabled"
    if pressed == i:
        return "pressed"
    if i == active:
        return "latched"
    if focus == i:
        return "focus"
    return "idle"


def plate(state: str) -> str:
    """Which of the two authored plates a resolved state uses."""
    return "active" if state in ("pressed", "latched") else "inactive"


def sprite_name(i: int, state: str) -> str:
    return C.specimen_key(i, plate(state))


def sprites_available(n: int = 5) -> bool:
    return all(sprite_size(C.specimen_key(i, s))[0] > 0
               for i in range(n) for s in ("inactive", "active"))


def draw_keys(cr, geo: BankGeometry, active: int, pressed: int | None = None,
              focus: int | None = None,
              disabled: frozenset[int] = frozenset(),
              alpha: float = 1.0) -> None:
    """Blit the five key plates. The mounting is drawn by the caller first.

    Each plate is drawn at the geometry's own key size, which preserves the
    authored aspect by construction; a pressed key is offset downward by its
    mechanical travel instead of being redrawn.
    """
    if not geo.valid:
        return
    for i in range(geo.n):
        st = key_state(i, active, pressed, focus, disabled)
        r = geo.key_rect(i)
        dy = geo.key_h * PRESS_TRAVEL if st == "pressed" else 0.0
        a = alpha * (0.42 if st == "disabled" else 1.0)
        draw_sprite(cr, sprite_name(i, st), r.x, r.y + dy, r.w, r.h, a)
