"""Instrument chrome: the quiet lab-panel typography around the organism.

Immediate-mode Cairo + PangoCairo. Two entry points, both safe to call every
frame:

    draw_background(cr, w, h)
    draw_chrome(cr, layout, tel, fps, frame_ms)

Design rules enforced here
--------------------------
* Hairlines and type only. No boxes, no brackets, no glow.
* Nothing is ever drawn outside the Rect it belongs to. Text is fitted by
  shrinking (bounded at ~7px), then by dropping the element entirely.
* Degenerate rects (``not rect.valid``) draw nothing at all.
* All Pango state -- font descriptions, letter-spacing attribute lists, cap
  heights, background gradients -- is cached in module-level dicts. A frame
  allocates essentially nothing beyond the formatted value strings.

``frame_ms`` is accepted for API stability; the FPS readout is driven by
``fps`` alone and the panel deliberately stays quiet about frame timing.
"""

from __future__ import annotations

import math
import socket

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Pango, PangoCairo  # noqa: E402

from abyssal.core.layout import Layout  # noqa: E402
from abyssal.core.signals import Telemetry  # noqa: E402
from abyssal.core.theme import (  # noqa: E402
    ABYSS,
    AMBER,
    CYAN,
    FONT_MONO,
    FONT_MONO_FALLBACK,
    FRAME,
    INK,
    INK_BRIGHT,
    INK_DIM,
    LIME,
    RULE,
    rgba,
)

__all__ = ["draw_background", "draw_chrome"]


# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

TITLE = "ABYSSAL ORGANISM MONITOR"
SPECIMEN = "PLUMIRADIA QUADRILOBATA"
SUBTITLE = "THE QUARTERED PLUME"

MIN_PT = 7.0                 # never render type smaller than this
CAP_RATIO = 0.74             # fallback cap-height guess before measurement
FPS_FULL = 75.0              # FPS meter maps 0..75
WARN_LEVEL = 0.85            # above this a value turns AMBER

_W_NORMAL = Pango.Weight.NORMAL
_W_MEDIUM = Pango.Weight.MEDIUM

_FAMILY = "%s,%s,monospace" % (FONT_MONO, FONT_MONO_FALLBACK)

_LABELS = ("CPU", "TEMP", "MEM", "FPS")
# (index, warns?) -- FPS never warns; a high frame rate is not a fault.
_WARNS = (True, True, True, False)

try:
    _HOST = socket.gethostname().split(".")[0].upper() or "LOCAL"
except Exception:  # pragma: no cover - hostname is best effort only
    _HOST = "LOCAL"

_META_ROWS = (
    ("CLASS", "SYNTHETIC"),
    ("MORPH", "QUADRILOBATE"),
    ("LOCUS", _HOST),
    ("STATE", ""),          # filled with the layout state at draw time
)


# --------------------------------------------------------------------------
# cached pango plumbing
# --------------------------------------------------------------------------

_PCTX = PangoCairo.font_map_get_default().create_context()
_LAY = Pango.Layout.new(_PCTX)
_LAY.set_single_paragraph_mode(True)

_FD_CACHE: dict[tuple[float, int], Pango.FontDescription] = {}
_ATTR_CACHE: dict[int, Pango.AttrList] = {}
_CAP_CACHE: dict[tuple[float, int], float] = {}
_BG_CACHE: dict[int, object] = {}

_SCALE = float(Pango.SCALE)

_BG_STEPS = 6
_BG_TOP_SPAN = 0.40
_BG_BOT_SPAN = 0.32


def _fd(size: float, weight: int) -> Pango.FontDescription:
    key = (size, weight)
    fd = _FD_CACHE.get(key)
    if fd is None:
        fd = Pango.FontDescription()
        fd.set_family(_FAMILY)
        fd.set_weight(weight)
        fd.set_absolute_size(int(size * _SCALE))
        _FD_CACHE[key] = fd
    return fd


def _attrs(tracking: float) -> Pango.AttrList | None:
    if tracking <= 0.01:
        return None
    key = int(round(tracking * 100.0))
    al = _ATTR_CACHE.get(key)
    if al is None:
        al = Pango.AttrList()
        al.insert(Pango.attr_letter_spacing_new(int(round(tracking * _SCALE))))
        _ATTR_CACHE[key] = al
    return al


def _prepare(text: str, size: float, weight: int, tracking: float) -> None:
    _LAY.set_width(-1)
    _LAY.set_ellipsize(Pango.EllipsizeMode.NONE)
    _LAY.set_font_description(_fd(size, weight))
    _LAY.set_attributes(_attrs(tracking))
    _LAY.set_text(text, -1)


def _text_w(text: str, size: float, weight: int, tracking: float) -> float:
    """Visual advance width, with the trailing half letter-space removed."""
    if not text:
        return 0.0
    _prepare(text, size, weight, tracking)
    w = _LAY.get_size()[0] / _SCALE
    return max(0.0, w - tracking)


def _cap(size: float, weight: int = _W_NORMAL) -> float:
    """Cap height in px -- the visual height of an all-caps line."""
    key = (size, weight)
    c = _CAP_CACHE.get(key)
    if c is None:
        _prepare("H", size, weight, 0.0)
        ink = _LAY.get_extents()[0]
        c = ink.height / _SCALE
        if c <= 0.0:
            c = size * CAP_RATIO
        _CAP_CACHE[key] = c
    return c


def _fit(text: str, size: float, weight: int, tracking: float,
         max_w: float) -> float:
    """Largest size <= ``size`` whose text fits ``max_w``. 0.0 = does not fit."""
    if not text or max_w <= 1.0:
        return 0.0
    w = _text_w(text, size, weight, tracking)
    if w <= max_w:
        return size
    # Mono advance is very close to linear in size; one guess plus a couple of
    # corrective steps converges immediately.
    guess = size * (max_w / w)
    s = math.floor(guess * 2.0) / 2.0
    for _ in range(4):
        if s < MIN_PT:
            return 0.0
        if _text_w(text, s, weight, tracking) <= max_w:
            return s
        s -= 0.5
    return 0.0


def _show(cr, text: str, size: float, weight: int, tracking: float,
          x: float, baseline: float, rgb, alpha: float = 1.0,
          align: str = "l", max_w: float = -1.0) -> float:
    """Draw one line, positioned by baseline. Returns its visual width."""
    if not text or size < MIN_PT - 0.01:
        return 0.0
    _prepare(text, size, weight, tracking)
    w = _LAY.get_size()[0] / _SCALE - tracking
    if max_w > 0.0 and w > max_w:
        # last-resort guard: ellipsize rather than bleed out of the rect
        _LAY.set_width(int((max_w + tracking) * _SCALE))
        _LAY.set_ellipsize(Pango.EllipsizeMode.END)
        w = min(w, max_w)
    if align == "r":
        ox = x - w
    elif align == "c":
        ox = x - w * 0.5
    else:
        ox = x
    ox -= tracking * 0.5
    cr.save()
    if alpha >= 0.999:
        cr.set_source_rgb(rgb[0], rgb[1], rgb[2])
    else:
        cr.set_source_rgba(*rgba(rgb, alpha))
    cr.move_to(ox, baseline - _LAY.get_baseline() / _SCALE)
    PangoCairo.show_layout(cr, _LAY)
    cr.restore()
    return w


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------

def _hairline(cr, x0: float, x1: float, y: float, rgb=RULE,
              alpha: float = 1.0) -> None:
    if x1 - x0 < 1.0:
        return
    cr.save()
    cr.set_line_width(1.0)
    if alpha >= 0.999:
        cr.set_source_rgb(rgb[0], rgb[1], rgb[2])
    else:
        cr.set_source_rgba(*rgba(rgb, alpha))
    yy = math.floor(y) + 0.5
    cr.move_to(x0, yy)
    cr.line_to(x1, yy)
    cr.stroke()
    cr.restore()


def _meter(cr, x: float, y: float, w: float, level: float, warn: bool) -> None:
    """2px understated bar: RULE track, CYAN (or AMBER) fill."""
    if w < 4.0:
        return
    yy = math.floor(y) + 0.0
    cr.set_source_rgb(RULE[0], RULE[1], RULE[2])
    cr.rectangle(x, yy, w, 2.0)
    cr.fill()
    lv = 0.0 if level < 0.0 else (1.0 if level > 1.0 else level)
    if lv <= 0.004:
        return
    fill = AMBER if warn else CYAN
    cr.set_source_rgba(*rgba(fill, 0.88))
    cr.rectangle(x, yy, max(1.5, w * lv), 2.0)
    cr.fill()


def _dot(cr, x: float, y: float, r: float = 2.2) -> None:
    cr.save()
    cr.set_source_rgba(*rgba(LIME, 0.9))
    cr.arc(x, y, r, 0.0, math.tau)
    cr.fill()
    cr.restore()


# --------------------------------------------------------------------------
# background
# --------------------------------------------------------------------------

def draw_background(cr, w: float, h: float) -> None:
    """Flat ABYSS field with a barely-there vignette toward FRAME."""
    cr.save()
    cr.set_source_rgb(ABYSS[0], ABYSS[1], ABYSS[2])
    cr.rectangle(0.0, 0.0, w, h)
    cr.fill()

    if h > 8.0:
        key = int(h)
        grad = _BG_CACHE.get(key)
        if grad is None:
            if len(_BG_CACHE) > 12:
                _BG_CACHE.clear()
            grad = cairo.LinearGradient(0.0, 0.0, 0.0, h)
            # Quadratic falloff on both edges: the alpha derivative reaches
            # zero at the junction, so there is no visible crease.
            for i in range(_BG_STEPS + 1):
                f = i / _BG_STEPS
                a = (1.0 - f) ** 2
                grad.add_color_stop_rgba(f * _BG_TOP_SPAN, *rgba(FRAME, 0.50 * a))
                grad.add_color_stop_rgba(1.0 - f * _BG_BOT_SPAN,
                                         *rgba(FRAME, 0.38 * a))
            _BG_CACHE[key] = grad
        cr.set_source(grad)
        cr.rectangle(0.0, 0.0, w, h)
        cr.fill()
    cr.restore()


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------

def _header(cr, L: Layout) -> None:
    r = L.header
    if not r.valid:
        return
    t = L.type

    rule_y = math.floor(r.bottom) - 0.5
    avail_h = rule_y - r.y - 3.0
    avail_w = r.w
    if avail_h < MIN_PT * 0.7 or avail_w < 8.0:
        return

    # --- specimen: the anchor. Fitted horizontally, then vertically. --------
    s_spec = _fit(SPECIMEN, t.specimen, _W_MEDIUM, 0.0, avail_w)
    if s_spec <= 0.0:
        return
    cap_spec = _cap(s_spec, _W_MEDIUM)
    if cap_spec > avail_h:
        s_spec = _fit(SPECIMEN, math.floor(avail_h / CAP_RATIO * 2.0) / 2.0,
                      _W_MEDIUM, 0.0, avail_w)
        if s_spec <= 0.0:
            return
        cap_spec = _cap(s_spec, _W_MEDIUM)
        if cap_spec > avail_h:
            return
    w_spec = _text_w(SPECIMEN, s_spec, _W_MEDIUM, 0.0)

    # --- title: dropped before the specimen is ever compromised ------------
    s_title = _fit(TITLE, t.title, _W_NORMAL, t.tracking, avail_w)
    cap_title = _cap(s_title) if s_title > 0.0 else 0.0
    gap = max(4.0, t.title * 0.62)
    if s_title > 0.0 and cap_title + gap + cap_spec > avail_h:
        s_title = 0.0
        cap_title = 0.0

    block_h = cap_spec + (cap_title + gap if s_title > 0.0 else 0.0)
    top = r.y + max(0.0, (avail_h - block_h) * 0.45)

    y_title = top + cap_title if s_title > 0.0 else 0.0
    y_spec = top + block_h

    w_title = 0.0
    if s_title > 0.0:
        w_title = _show(cr, TITLE, s_title, _W_NORMAL, t.tracking,
                        r.x, y_title, INK_DIM, 1.0, "l", avail_w)

    _show(cr, SPECIMEN, s_spec, _W_MEDIUM, 0.0, r.x, y_spec,
          INK_BRIGHT, 1.0, "l", avail_w)

    # --- subtitle: right-aligned on the specimen baseline, or dropped ------
    if L.show_subtitle:
        s_sub = _fit(SUBTITLE, t.subtitle, _W_NORMAL, t.tracking,
                     max(0.0, avail_w - w_spec - t.specimen * 1.6))
        if s_sub > 0.0:
            _show(cr, SUBTITLE, s_sub, _W_NORMAL, t.tracking,
                  r.right, y_spec, INK_DIM, 1.0, "r")

    # --- one small nominal dot, top right ----------------------------------
    dot_r = max(1.6, t.micro * 0.26)
    dot_x = r.right - dot_r
    if s_title > 0.0:
        if r.x + w_title + dot_r * 6.0 < dot_x:
            _dot(cr, dot_x, y_title - cap_title * 0.42, dot_r)
    elif r.x + w_spec + dot_r * 6.0 < dot_x:
        _dot(cr, dot_x, y_spec - cap_spec * 0.42, dot_r)

    _hairline(cr, r.x, r.right, rule_y)


# --------------------------------------------------------------------------
# metadata block (ARCHIVE)
# --------------------------------------------------------------------------

def _meta_block(cr, L: Layout) -> None:
    """ARCHIVE: a quiet identity block at the top of the side column."""
    m = L.meta
    if not m.valid or m.w < 60.0 or m.h < 24.0:
        return

    t = L.type
    s_lab = t.micro
    s_val = t.label
    cap_val = _cap(s_val)
    rows = len(_META_ROWS)

    usable = m.h - 4.0
    if usable < rows * (cap_val + 3.0):
        return
    row_h = min(usable / rows, cap_val * 2.5)

    lab_w = 0.0
    for lab, _ in _META_ROWS:
        w = _text_w(lab, s_lab, _W_NORMAL, t.tracking)
        if w > lab_w:
            lab_w = w
    val_x = m.x + lab_w + max(10.0, t.label * 1.2)
    val_max = m.right - val_x
    if val_max < 30.0:
        val_x = m.x + lab_w + 8.0
        val_max = m.right - val_x
        if val_max < 16.0:
            return

    y = m.y + 2.0
    last = y + cap_val
    for i in range(rows):
        lab, val = _META_ROWS[i]
        if not val:
            val = L.state.value
        base = y + i * row_h + cap_val
        if base > m.bottom - 2.0:
            break
        _show(cr, lab, s_lab, _W_NORMAL, t.tracking, m.x, base,
              INK_DIM, 1.0, "l", lab_w + t.tracking)
        _show(cr, val, s_val, _W_NORMAL, 0.0, m.right, base,
              INK, 1.0, "r", val_max)
        last = base

    # Close the block with one hairline; the layout gap does the rest of the
    # separating from the gauges below.
    _hairline(cr, m.x, m.right,
              min(m.bottom - 1.0, last + row_h * 0.60))


# --------------------------------------------------------------------------
# readouts
# --------------------------------------------------------------------------

def _value_candidates(idx: int, tel: Telemetry, fps: float):
    """Preference-ordered ``(text, split)`` pairs for one readout.

    ``split`` is the index at which the quiet part of the value begins: the
    unit or the denominator, drawn a step down in brightness so the magnitude
    reads first.
    """
    if idx == 0:
        s = "%d%%" % int(tel.cpu_pct + 0.5)
        return ((s, len(s) - 1),)
    if idx == 1:
        if not tel.temp_available or tel.temp_c is None:
            return (("--", 0),)
        c = int(tel.temp_c + 0.5)
        a = "%d°C" % c
        b = "%d°" % c
        return ((a, len(a) - 2), (b, len(b) - 1))
    if idx == 2:
        used = tel.mem_used_gb
        total = tel.mem_total_gb
        pct = "%d%%" % int(tel.memory_pressure * 100.0 + 0.5)
        if total <= 0.0:
            return ((pct, len(pct) - 1),)
        a = "%.1f/%.1fG" % (used, total)
        b = "%.1fG" % used
        return ((a, a.index("/")), (b, len(b) - 1), (pct, len(pct) - 1))
    f = fps if fps == fps else 0.0  # NaN guard
    if f < 0.0:
        f = 0.0
    s = "%d" % int(f + 0.5)
    return ((s, len(s)),)


def _levels(idx: int, tel: Telemetry, fps: float) -> float:
    if idx == 0:
        return tel.cpu_load
    if idx == 1:
        return tel.temperature
    if idx == 2:
        return tel.memory_pressure
    f = fps if fps == fps else 0.0
    return f / FPS_FULL


def _pick_value(idx: int, tel: Telemetry, fps: float, size: float,
                max_w: float) -> tuple[str, int, float]:
    """Choose the richest value string that fits, shrinking only as a last
    resort. Never returns something wider than ``max_w``."""
    cands = _value_candidates(idx, tel, fps)
    for s, split in cands:
        if _text_w(s, size, _W_NORMAL, 0.0) <= max_w:
            return s, split, size
    last, split = cands[-1]
    fitted = _fit(last, size, _W_NORMAL, 0.0, max_w)
    if fitted > 0.0:
        return last, split, fitted
    return "", 0, 0.0


def _show_value(cr, text: str, split: int, size: float, x: float,
                baseline: float, warn: bool, align: str, max_w: float) -> None:
    """Value in two tones: magnitude bright, unit one step quieter."""
    if not text:
        return
    head = text[:split]
    tail = text[split:]
    col = AMBER if warn else INK_BRIGHT
    if not head:
        _show(cr, tail, size, _W_NORMAL, 0.0, x, baseline,
              col if warn else INK_DIM, 1.0, align, max_w)
        return
    if align == "r":
        total = _text_w(text, size, _W_NORMAL, 0.0)
        if total > max_w > 0.0:
            total = max_w
        left = x - total
    else:
        left = x
    hw = _show(cr, head, size, _W_NORMAL, 0.0, left, baseline, col, 1.0,
               "l", max_w)
    if tail:
        if warn:
            _show(cr, tail, size, _W_NORMAL, 0.0, left + hw, baseline,
                  AMBER, 0.55, "l", max_w - hw)
        else:
            _show(cr, tail, size, _W_NORMAL, 0.0, left + hw, baseline,
                  INK, 1.0, "l", max_w - hw)


def _readout_cell(cr, x: float, y: float, w: float, h: float, label: str,
                  value: str, split: int, vsize: float, lsize: float,
                  tracking: float, level: float, warn: bool,
                  meter_w: float) -> None:
    """Stacked cell: label / value / meter, vertically centred in (y, h)."""
    if w < 8.0 or h < 6.0:
        return
    cap_l = _cap(lsize) if lsize > 0.0 else 0.0
    cap_v = _cap(vsize) if vsize > 0.0 else 0.0

    g1 = max(3.0, lsize * 0.72)
    g2 = max(4.0, vsize * 0.48)
    show_label = lsize > 0.0
    show_meter = meter_w >= 6.0

    def need() -> float:
        n = cap_v
        if show_label:
            n += cap_l + g1
        if show_meter:
            n += g2 + 2.0
        return n

    if need() > h:                      # 1. tighten the gaps
        g1 = 2.0
        g2 = 3.0
    if need() > h and show_meter:       # 2. lose the meter
        show_meter = False
    if need() > h and show_label:
        # 3. a modest trim of the value is worth more than losing the label,
        #    but only a modest one -- never shrink type into mush.
        target = h - cap_l - g1
        if target > MIN_PT * CAP_RATIO:
            ns = math.floor(target / CAP_RATIO * 2.0) / 2.0
            ns = max(MIN_PT, min(vsize, ns))
            if ns >= vsize * 0.7 and _cap(ns) + cap_l + g1 <= h:
                vsize = ns
                cap_v = _cap(ns)
    if need() > h and show_label:       # 4. lose the label
        show_label = False
    if need() > h:                      # 5. only now shrink the value hard
        vsize = math.floor(h / CAP_RATIO * 2.0) / 2.0
        if vsize < MIN_PT:
            return
        cap_v = _cap(vsize)
        if cap_v > h:
            return

    top = y + max(0.0, (h - need()) * 0.5)
    if show_label:
        _show(cr, label, lsize, _W_NORMAL, tracking, x, top + cap_l,
              INK_DIM, 1.0, "l", w)
        top += cap_l + g1
    hot = warn and level > WARN_LEVEL
    _show_value(cr, value, split, vsize, x, top + cap_v, hot, "l", w)
    top += cap_v
    if show_meter:
        _meter(cr, x, top + g2, meter_w, level, hot)


def _readouts_row(cr, L: Layout, tel: Telemetry, fps: float) -> None:
    r = L.readout
    t = L.type
    cell = r.w / 4.0
    gutter = min(18.0, max(4.0, cell * 0.11))
    inner = cell - gutter
    if inner < 10.0:
        return
    meter_cap = min(inner, t.value * 9.0)

    lsize = t.label
    if _text_w("TEMP", lsize, _W_NORMAL, t.tracking) > inner:
        lsize = _fit("TEMP", lsize, _W_NORMAL, t.tracking, inner)

    for i in range(4):
        x = r.x + i * cell
        value, split, vsize = _pick_value(i, tel, fps, t.value, inner)
        if not value:
            continue
        _readout_cell(cr, x, r.y, inner, r.h, _LABELS[i], value, split, vsize,
                      lsize, t.tracking, _levels(i, tel, fps), _WARNS[i],
                      meter_cap)


def _readouts_column(cr, L: Layout, tel: Telemetry, fps: float) -> None:
    """ARCHIVE side column: label left / value right, meter spanning beneath."""
    r = L.readout
    t = L.type
    if r.w < 60.0:
        return

    cap_v = _cap(t.value)
    cap_l = _cap(t.label)
    gap_m = max(8.0, t.value * 0.46)
    item_h = cap_v + gap_m + 2.0
    # Keep the ladder off the rect edges so the first gauge does not kiss the
    # header rule and the last does not crowd the mode word.
    inset = min(r.h * 0.07, cap_v * 0.85)
    if r.h - 2.0 * inset < item_h:
        inset = 0.0
    ry = r.y + inset
    rh = r.h - 2.0 * inset
    # Spread the four gauges down the column, but cap the stride so a very
    # tall window does not turn the panel into four lost specks.
    slot = min((rh - item_h) / 3.0, item_h * 4.6)
    if slot < item_h:
        slot = max(item_h, (rh - item_h) / 3.0)
    block = slot * 3.0 + item_h
    if block > rh:
        slot = max(0.0, (rh - item_h) / 3.0)
        block = slot * 3.0 + item_h
    top = ry + max(0.0, (rh - block) * 0.5)

    lab_w = 0.0
    for lab in _LABELS:
        w = _text_w(lab, t.label, _W_NORMAL, t.tracking)
        if w > lab_w:
            lab_w = w
    val_max = max(20.0, r.w - lab_w - t.label * 1.4)

    for i in range(4):
        y = top + i * slot
        if y + item_h > r.bottom + 0.5:
            break
        value, split, vsize = _pick_value(i, tel, fps, t.value, val_max)
        if not value:
            continue
        base = y + cap_v
        level = _levels(i, tel, fps)
        warn = _WARNS[i] and level > WARN_LEVEL
        if cap_l <= cap_v:
            _show(cr, _LABELS[i], t.label, _W_NORMAL, t.tracking, r.x, base,
                  INK_DIM, 1.0, "l", lab_w + t.tracking)
        _show_value(cr, value, split, vsize, r.right, base, warn, "r", val_max)
        _meter(cr, r.x, base + gap_m, r.w, level, warn)


# --------------------------------------------------------------------------
# mode word
# --------------------------------------------------------------------------

def _mode_word(cr, L: Layout) -> None:
    t = L.type
    word = L.state.value
    band_top = L.readout.bottom if L.readout.valid else L.height - L.pad
    band_h = L.height - band_top
    if band_h < 4.0:
        band_top = L.height - L.pad
        band_h = L.pad
    cap = _cap(t.micro)
    size = t.micro
    if cap > band_h - 1.0:
        size = _fit(word, math.floor((band_h - 1.0) / CAP_RATIO * 2.0) / 2.0,
                    _W_NORMAL, t.tracking, L.width - 2.0 * L.pad)
        if size <= 0.0:
            return
        cap = _cap(size)
    size = _fit(word, size, _W_NORMAL, t.tracking, L.width - 2.0 * L.pad)
    if size <= 0.0:
        return
    base = band_top + (band_h - _cap(size)) * 0.5 + _cap(size)
    base = min(base, L.height - 1.0)
    _show(cr, word, size, _W_NORMAL, t.tracking, L.width - L.pad, base,
          INK_DIM, 0.85, "r")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def draw_chrome(cr, layout: Layout, tel: Telemetry, fps: float,
                frame_ms: float) -> None:
    cr.save()
    cr.set_antialias(cairo.ANTIALIAS_DEFAULT)
    _header(cr, layout)
    if layout.show_meta:
        _meta_block(cr, layout)
    if layout.readout.valid:
        if layout.readout_vertical:
            _readouts_column(cr, layout, tel, fps)
        else:
            _readouts_row(cr, layout, tel, fps)
    _mode_word(cr, layout)
    cr.restore()
