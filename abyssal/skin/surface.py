"""Sprite loading, scale-safe 9-slice rendering, and a bounded surface cache.

WHY A CACHE EXISTS HERE AT ALL
------------------------------
The rest of this project deliberately has no persistent rendering state: a
resize recomputes pure values and nothing is retained. That guarantee is about
GEOMETRY, and it is preserved -- nothing in this module can influence a Layout,
a Viewport or the simulation.

What is cached is purely derived pixels: "this panel, rendered at this exact
integer size". A miss costs a re-render, never a crash and never a visual
difference, because the cache key IS the full set of inputs. Dropping the whole
cache between any two frames would change performance and nothing else. That is
the property that makes it safe here.

PREMULTIPLIED ALPHA
-------------------
Surfaces come from `cairo.ImageSurface.create_from_png`, which produces ARGB32
with alpha already premultiplied. That is exactly what Cairo's compositor wants,
so the fringing warned about in assets/hardware_v2/INVENTORY.md cannot occur --
provided nobody hand-decodes these PNGs into a surface elsewhere. Nobody does.

9-SLICE CONTRACT
----------------
A panel is split by four insets (left, top, right, bottom):

      corners  -- drawn 1:1, never scaled, so screws and bevel crests stay sharp
      edges    -- stretched along ONE axis only
      centre   -- stretched freely

Consequently a panel can be drawn at any size without smearing its fasteners
and without anisotropic distortion of its corner geometry.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import cairo

from . import hidpi

SPRITE_ROOT = Path(__file__).resolve().parent.parent.parent / "assets" / "sprites"

# Bounded so a long resize drag cannot grow memory without limit. Each entry is
# one rendered panel; 64 covers every panel in every layout state several times
# over, which is what matters for steady-state hit rate.
_MAX_SCALED = 64

#: Floor on the stretched middle band, as a share of the drawn panel. When a
#: panel is drawn smaller than its own borders they are scaled down together
#: to leave at least this much centre; without it a tall bezel drawn short
#: becomes two touching borders and a two-pixel slit of glass.
MIN_MIDDLE_SHARE = 0.34

#: Floor on the proportional border scale. A 9-sliced border is authored at the
#: sprite's own size; drawn far below it, a border that stayed 1:1 would eat the
#: aperture - the observation bezel is authored 1280x900 with 178px of metal
#: vertically, which at a 489px stage is 36% of the field. Borders therefore
#: scale by how far below its authored size the panel is drawn, using ONE
#: factor for both axes so corners stay square and no screw is ovalised.
MIN_BORDER_SCALE = 0.45

_base: dict[str, cairo.ImageSurface | None] = {}
_scaled: "OrderedDict[tuple, cairo.ImageSurface]" = OrderedDict()
_stats = {"loads": 0, "hits": 0, "misses": 0, "missing": 0}


class SkinUnavailable(Exception):
    """Raised only by strict callers; normal drawing degrades silently."""


# --------------------------------------------------------------------- load
def sprite(name: str) -> cairo.ImageSurface | None:
    """Load `assets/sprites/<name>.png` once. None if absent.

    A missing sprite is not fatal: the app must still run (and still be
    testable) on a checkout where the asset pass has not been built.
    """
    if name in _base:
        return _base[name]
    path = SPRITE_ROOT / (name + ".png")
    surf: cairo.ImageSurface | None
    try:
        surf = cairo.ImageSurface.create_from_png(str(path))
        _stats["loads"] += 1
    except Exception:
        surf = None
        _stats["missing"] += 1
    _base[name] = surf
    return surf


def sprite_size(name: str) -> tuple[int, int]:
    s = sprite(name)
    if s is None:
        return (0, 0)
    return (s.get_width(), s.get_height())


def skin_stats() -> dict:
    return dict(_stats, cached=len(_scaled), loaded=sum(1 for v in _base.values() if v))


# ---------------------------------------------------------------- 9-slice
@dataclass(frozen=True, slots=True)
class NineSlice:
    """A panel plus the four insets that make it scale safely.

    `pad_*` are the CONTENT insets measured in the SOURCE sprite: how far into
    the artwork the usable recess begins. `content()` maps them through the
    9-slice rather than using them directly, because where a pad lands when
    drawn depends on which band it falls in:

        pad <= inset   it sits in the FIXED border band, and moves only by the
                       shrink factor
        pad >  inset   it sits in the STRETCHED middle, so it moves with the
                       stretch

    Treating them as drawn-space offsets breaks both ways: a pad smaller than
    its inset lands inside the bevel (text drawn on the metal crest), and a pad
    inside the middle drifts as the panel is stretched.

    They differ from `left`/`top` because those are padded outward to keep a
    rounded corner inside a fixed corner tile. A caller placing a readout in
    the glass asks `content()`.
    """

    name: str
    left: int
    top: int
    right: int
    bottom: int
    pad_l: float = 0.0
    pad_t: float = 0.0
    pad_r: float = 0.0
    pad_b: float = 0.0    # all in pixels

    def min_size(self) -> tuple[int, int]:
        return (self.left + self.right + 1, self.top + self.bottom + 1)

    def content(self, x: float, y: float, w: float, h: float
                ) -> tuple[float, float, float, float]:
        """The usable recess inside this panel drawn at (x, y, w, h).

        If the panel is drawn smaller than its own borders the insets are
        scaled down together, mirroring what _render_nine does, so the recess
        stays inside the panel instead of inverting.
        """
        sw, sh = sprite_size(self.name)
        kx, ky = _shrink(self, w, h)
        pl = _map_pad(self.pad_l, self.left, sw - self.left - self.right,
                      self.left * kx, w - (self.left + self.right) * kx)
        pr = _map_pad(self.pad_r, self.right, sw - self.left - self.right,
                      self.right * kx, w - (self.left + self.right) * kx)
        pt = _map_pad(self.pad_t, self.top, sh - self.top - self.bottom,
                      self.top * ky, h - (self.top + self.bottom) * ky)
        pb = _map_pad(self.pad_b, self.bottom, sh - self.top - self.bottom,
                      self.bottom * ky, h - (self.top + self.bottom) * ky)
        return (x + pl, y + pt, max(0.0, w - pl - pr), max(0.0, h - pt - pb))


def _map_pad(pad: float, inset: int, src_mid: float,
             dst_inset: float, dst_mid: float) -> float:
    """Map one source-space content pad into drawn space through the 9-slice.

    Inside the fixed border it only shrinks; past it, it rides the stretch.
    """
    if inset <= 0:
        return pad
    if pad <= inset:
        return pad * (dst_inset / inset)
    if src_mid <= 0.0:
        return dst_inset
    return dst_inset + (pad - inset) * (dst_mid / src_mid)


def _shrink(ns: "NineSlice", w: float, h: float) -> tuple[float, float]:
    """Per-axis factor applied to BOTH the slice borders and the content pads.

    They must share one factor. The borders say where the metal is drawn and
    the pads say where the recess begins; scaling one without the other makes
    the app believe the glass starts somewhere the metal is still being
    painted, which is how a shrunken bezel ends up covering its own viewport.
    """
    sw, sh = sprite_size(ns.name)
    if sw > 0 and sh > 0:
        k = min(w / float(sw), h / float(sh))
        kx = ky = max(MIN_BORDER_SCALE, min(1.0, k))
    else:
        kx = ky = 1.0
    lr = (ns.left + ns.right) * kx
    tb = (ns.top + ns.bottom) * ky
    max_lr = w * (1.0 - MIN_MIDDLE_SHARE)
    max_tb = h * (1.0 - MIN_MIDDLE_SHARE)
    if lr > max_lr and lr > 0:
        kx *= max(0.0, max_lr / lr)
    if tb > max_tb and tb > 0:
        ky *= max(0.0, max_tb / tb)
    return (kx, ky)


def _blit_region(cr: cairo.Context, src: cairo.ImageSurface,
                 sx: float, sy: float, sw: float, sh: float,
                 dx: float, dy: float, dw: float, dh: float) -> None:
    """Draw src[sx,sy,sw,sh] into dst[dx,dy,dw,dh]."""
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return
    cr.save()
    cr.rectangle(dx, dy, dw, dh)
    cr.clip()
    cr.translate(dx, dy)
    cr.scale(dw / sw, dh / sh)
    cr.set_source_surface(src, -sx, -sy)
    pat = cr.get_source()
    pat.set_filter(cairo.FILTER_GOOD)
    pat.set_extend(cairo.EXTEND_PAD)
    cr.paint()
    cr.restore()


def _render_nine(ns: NineSlice, w: int, h: int) -> cairo.ImageSurface | None:
    src = sprite(ns.name)
    if src is None:
        return None
    sw, sh = src.get_width(), src.get_height()

    # Shrink borders (and, via the same factor, the content pads) so the
    # stretched middle never collapses and opposite corners never overlap.
    kx, ky = _shrink(ns, w, h)
    l, r = int(ns.left * kx), int(ns.right * kx)
    t, b = int(ns.top * ky), int(ns.bottom * ky)

    out = hidpi.surface(w, h)
    cr = cairo.Context(out)
    cr.set_operator(cairo.OPERATOR_SOURCE)

    smx, smy = sw - ns.left - ns.right, sh - ns.top - ns.bottom     # src middles
    dmx, dmy = w - l - r, h - t - b                                 # dst middles

    cols = ((0, ns.left, 0, l),
            (ns.left, smx, l, dmx),
            (sw - ns.right, ns.right, w - r, r))
    rows = ((0, ns.top, 0, t),
            (ns.top, smy, t, dmy),
            (sh - ns.bottom, ns.bottom, h - b, b))

    for sy, sh_, dy, dh_ in rows:
        for sx, sw_, dx, dw_ in cols:
            _blit_region(cr, src, sx, sy, sw_, sh_, dx, dy, dw_, dh_)
    out.flush()
    return out


def nine_surface(ns: NineSlice, w: float, h: float) -> cairo.ImageSurface | None:
    """Cached 9-sliced panel at an exact integer size."""
    wi, hi = int(round(w)), int(round(h))
    if wi < 1 or hi < 1:
        return None
    key = ("9", ns.name, ns.left, ns.top, ns.right, ns.bottom, wi, hi,
           hidpi.scale())
    hit = _scaled.get(key)
    if hit is not None:
        _scaled.move_to_end(key)
        _stats["hits"] += 1
        return hit
    _stats["misses"] += 1
    surf = _render_nine(ns, wi, hi)
    if surf is None:
        return None
    _scaled[key] = surf
    while len(_scaled) > _MAX_SCALED:
        _scaled.popitem(last=False)
    return surf


def draw_nine(cr: cairo.Context, ns: NineSlice,
              x: float, y: float, w: float, h: float, alpha: float = 1.0) -> bool:
    surf = nine_surface(ns, w, h)
    if surf is None:
        return False
    cr.save()
    cr.translate(round(x), round(y))
    cr.set_source_surface(surf, 0, 0)
    if alpha >= 0.999:
        cr.paint()
    else:
        cr.paint_with_alpha(max(0.0, alpha))
    cr.restore()
    return True


# ---------------------------------------------------------------- sprites
def _scaled_sprite(name: str, w: int, h: int) -> cairo.ImageSurface | None:
    key = ("s", name, w, h, hidpi.scale())
    hit = _scaled.get(key)
    if hit is not None:
        _scaled.move_to_end(key)
        _stats["hits"] += 1
        return hit
    src = sprite(name)
    if src is None:
        return None
    _stats["misses"] += 1
    out = hidpi.surface(w, h)
    cr = cairo.Context(out)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.scale(w / src.get_width(), h / src.get_height())
    cr.set_source_surface(src, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.paint()
    out.flush()
    _scaled[key] = out
    while len(_scaled) > _MAX_SCALED:
        _scaled.popitem(last=False)
    return out


def draw_sprite(cr: cairo.Context, name: str,
                x: float, y: float, w: float, h: float, alpha: float = 1.0) -> bool:
    """Draw a sprite stretched to exactly w x h."""
    wi, hi = int(round(w)), int(round(h))
    if wi < 1 or hi < 1:
        return False
    surf = _scaled_sprite(name, wi, hi)
    if surf is None:
        return False
    cr.save()
    cr.translate(round(x), round(y))
    cr.set_source_surface(surf, 0, 0)
    if alpha >= 0.999:
        cr.paint()
    else:
        cr.paint_with_alpha(max(0.0, alpha))
    cr.restore()
    return True


def draw_sprite_fit(cr: cairo.Context, name: str,
                    cx: float, cy: float, target_h: float,
                    alpha: float = 1.0) -> tuple[float, float]:
    """Draw centred on (cx, cy) at `target_h`, PRESERVING ASPECT.

    Used for screws, lamps, knobs and keys -- anything whose proportions must
    never be distorted by the panel it sits on. Returns the drawn (w, h).
    """
    sw, sh = sprite_size(name)
    if sw == 0 or sh == 0 or target_h < 1:
        return (0.0, 0.0)
    k = target_h / sh
    w, h = sw * k, target_h
    draw_sprite(cr, name, cx - w / 2.0, cy - h / 2.0, w, h, alpha)
    return (w, h)


def draw_sprite_rot90(cr: cairo.Context, name: str,
                      x: float, y: float, w: float, h: float,
                      alpha: float = 1.0) -> bool:
    """Draw a sprite rotated a quarter turn, filling (x, y, w, h).

    The handle rails on the reference chassis run VERTICALLY down the side
    members, but the part was generated horizontally. Rotating at draw time
    beats generating a second asset that would only drift from the first.
    """
    src = sprite(name)
    if src is None or w < 1 or h < 1:
        return False
    cr.save()
    cr.translate(x + w, y)
    cr.rotate(1.5707963267948966)
    # after the rotation the sprite's own width runs down the screen
    surf = _scaled_sprite(name, int(round(h)), int(round(w)))
    if surf is None:
        cr.restore()
        return False
    cr.set_source_surface(surf, 0, 0)
    if alpha >= 0.999:
        cr.paint()
    else:
        cr.paint_with_alpha(max(0.0, alpha))
    cr.restore()
    return True


def fit_box(name: str, box_w: float, box_h: float) -> tuple[float, float]:
    """Largest aspect-preserving size of `name` fitting inside a box."""
    sw, sh = sprite_size(name)
    if sw == 0 or sh == 0:
        return (0.0, 0.0)
    k = min(box_w / sw, box_h / sh)
    return (sw * k, sh * k)


def clear_cache() -> None:
    """Drop derived surfaces. Purely a memory operation; changes no output."""
    _scaled.clear()
