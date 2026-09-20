"""Procedural seven-segment electronic display.

WHY PROCEDURAL RATHER THAN A FONT
---------------------------------
No seven-segment face is installed on this machine, and more importantly a
raster or outline font gives no control over the things that actually sell a
vintage readout: the unlit "ghost" segments that stay faintly visible, segment
thickness independent of glyph height, the chamfered segment ends of a real LED
module, and a bloom that tracks the lit colour. All of those are one-liners
here and impossible with a font.

Drawing is pure Cairo path work, so it stays crisp at any size and needs no
cache. Geometry is authored in a unit cell and scaled by the requested digit
height, which is why the readouts survive every layout state unblurred.

The look: vintage digital watch / laboratory electronics / aerospace
instrumentation. Restrained. The bloom is a whisper, not a neon sign.
"""

from __future__ import annotations

from dataclasses import dataclass

# Segment identity, in the conventional order:
#      a
#    f   b
#      g
#    e   c
#      d
SEG_A, SEG_B, SEG_C, SEG_D, SEG_E, SEG_F, SEG_G = range(7)

#: Which segments are lit for each supported character.
GLYPHS: dict[str, tuple[int, ...]] = {
    "0": (SEG_A, SEG_B, SEG_C, SEG_D, SEG_E, SEG_F),
    "1": (SEG_B, SEG_C),
    "2": (SEG_A, SEG_B, SEG_G, SEG_E, SEG_D),
    "3": (SEG_A, SEG_B, SEG_G, SEG_C, SEG_D),
    "4": (SEG_F, SEG_G, SEG_B, SEG_C),
    "5": (SEG_A, SEG_F, SEG_G, SEG_C, SEG_D),
    "6": (SEG_A, SEG_F, SEG_G, SEG_E, SEG_C, SEG_D),
    "7": (SEG_A, SEG_B, SEG_C),
    "8": (SEG_A, SEG_B, SEG_C, SEG_D, SEG_E, SEG_F, SEG_G),
    "9": (SEG_A, SEG_B, SEG_C, SEG_D, SEG_F, SEG_G),
    "-": (SEG_G,),
    "_": (SEG_D,),
    # Unit and status letters. A real laboratory readout spells its own unit
    # in the SAME segment technology as its digits - the unit is part of the
    # display, not a caption printed next to it. Only the letters that a
    # seven-segment cell can form unambiguously are defined; anything else
    # falls back to the technical unit layer (see draw_unit).
    "A": (SEG_A, SEG_B, SEG_C, SEG_E, SEG_F, SEG_G),
    "C": (SEG_A, SEG_D, SEG_E, SEG_F),
    "E": (SEG_A, SEG_D, SEG_E, SEG_F, SEG_G),
    "F": (SEG_A, SEG_E, SEG_F, SEG_G),
    "G": (SEG_A, SEG_C, SEG_D, SEG_E, SEG_F),
    "H": (SEG_B, SEG_C, SEG_E, SEG_F, SEG_G),
    "J": (SEG_B, SEG_C, SEG_D),
    "L": (SEG_D, SEG_E, SEG_F),
    "P": (SEG_A, SEG_B, SEG_E, SEG_F, SEG_G),
    "S": (SEG_A, SEG_C, SEG_D, SEG_F, SEG_G),
    "U": (SEG_B, SEG_C, SEG_D, SEG_E, SEG_F),
    "Y": (SEG_B, SEG_C, SEG_D, SEG_F, SEG_G),
    "b": (SEG_C, SEG_D, SEG_E, SEG_F, SEG_G),
    "c": (SEG_D, SEG_E, SEG_G),
    "d": (SEG_B, SEG_C, SEG_D, SEG_E, SEG_G),
    "h": (SEG_C, SEG_E, SEG_F, SEG_G),
    "n": (SEG_C, SEG_E, SEG_G),
    "o": (SEG_C, SEG_D, SEG_E, SEG_G),
    "r": (SEG_E, SEG_G),
    "t": (SEG_D, SEG_E, SEG_F, SEG_G),
    "u": (SEG_C, SEG_D, SEG_E),
}

#: Letters a seven-segment cell cannot form. Rendered by the unit layer.
UNSEGMENTABLE = frozenset("KMQRVWXZ")

#: Characters that occupy a narrow cell rather than a full digit cell.
#: The degree ring is one of these: it is a real element of the display glass,
#: sized and positioned from the digit height like every other segment, so
#: "83\u00b0C" reads as one readout rather than digits with a caption beside them.
NARROW = {".": 0.34, ":": 0.34, "/": 0.58, "'": 0.30, "\u00b0": 0.46,
          "%": 0.66, " ": 0.34}


@dataclass(frozen=True, slots=True)
class SegmentStyle:
    """Appearance of one readout. Colours are linear 0..1 RGB."""

    lit: tuple[float, float, float] = (0.62, 0.86, 0.94)
    thickness: float = 0.118      # of digit HEIGHT
    slant: float = 0.045          # italic shear, fraction of height
    gap: float = 0.030            # gap between adjacent segments, of height
    aspect: float = 0.560         # digit width / height
    tracking: float = 0.190       # inter-digit space, of height
    ghost: float = 0.070          # unlit segment alpha
    bloom: float = 0.30           # halo strength, 0 disables
    alpha: float = 1.0


# Palette for the instrument's three readout moods.
CYAN = SegmentStyle(lit=(0.60, 0.86, 0.95))
AMBER = SegmentStyle(lit=(0.98, 0.64, 0.16))
RED = SegmentStyle(lit=(0.92, 0.26, 0.22))
PALE = SegmentStyle(lit=(0.84, 0.90, 0.95))


def _seg_poly(kind: str, x0: float, y0: float, x1: float, y1: float, t: float):
    """Chamfered segment body, as a real LED module has.

    `kind` is 'h' or 'v'; (x0,y0)-(x1,y1) is the segment's centre line.
    """
    h = t * 0.5
    if kind == "h":
        return ((x0, y0), (x0 + h, y0 - h), (x1 - h, y0 - h),
                (x1, y0), (x1 - h, y0 + h), (x0 + h, y0 + h))
    return ((x0, y0), (x0 + h, y0 + h), (x0 + h, y1 - h),
            (x0, y1), (x0 - h, y1 - h), (x0 - h, y0 + h))


def _cell_segments(w: float, h: float, t: float, gap: float):
    """Centre lines of all seven segments inside a w x h digit cell.

    A segment's centre line runs between the points where its chamfered tips
    converge, so the tips reach `gap` short of the cell edge and the corners
    mitre without overlapping. Insetting by the full half-thickness as well
    would leave each segment shorter than it is thick - a row of diamonds
    rather than a numeral.
    """
    hh = t * 0.5
    mid = h * 0.5
    xl, xr = hh, w - hh                    # vertical centre lines
    yt, yb = hh, h - hh                    # horizontal centre lines
    hx0, hx1 = gap + hh, w - gap - hh      # horizontal span
    vy_top = gap + hh                      # vertical span, upper pair
    vy_mid_hi = mid - gap * 0.5
    vy_mid_lo = mid + gap * 0.5
    vy_bot = h - gap - hh
    return {
        SEG_A: ("h", hx0, yt, hx1, yt),
        SEG_D: ("h", hx0, yb, hx1, yb),
        SEG_G: ("h", hx0, mid, hx1, mid),
        SEG_F: ("v", xl, vy_top, xl, vy_mid_hi),
        SEG_E: ("v", xl, vy_mid_lo, xl, vy_bot),
        SEG_B: ("v", xr, vy_top, xr, vy_mid_hi),
        SEG_C: ("v", xr, vy_mid_lo, xr, vy_bot),
    }


def measure(text: str, height: float, st: SegmentStyle = CYAN) -> float:
    """Advance width of `text` at `height`, matching what draw() emits."""
    dw = height * st.aspect
    track = height * st.tracking
    w = 0.0
    for i, ch in enumerate(text):
        if i:
            w += track
        w += height * NARROW[ch] if ch in NARROW else dw
    return w


def _path_poly(cr, pts, ox: float, oy: float, shear: float, h: float) -> None:
    for i, (px, py) in enumerate(pts):
        # Shear about the cell's vertical centre so digits lean like a watch.
        sx = ox + px + (h * 0.5 - py) * shear
        sy = oy + py
        if i == 0:
            cr.move_to(sx, sy)
        else:
            cr.line_to(sx, sy)
    cr.close_path()


def draw(cr, text: str, x: float, y: float, height: float,
         st: SegmentStyle = CYAN) -> float:
    """Draw `text` with its LEFT-TOP at (x, y). Returns the advance width.

    Unsupported characters are skipped rather than raising: a readout must
    never be able to take the frame down.
    """
    if height < 2.0 or not text:
        return 0.0

    dw = height * st.aspect
    t = max(1.0, height * st.thickness)
    gap = height * st.gap
    track = height * st.tracking
    shear = st.slant
    r, g, b = st.lit
    a = max(0.0, min(1.0, st.alpha))

    geo = _cell_segments(dw, height, t, gap)
    cx = x

    for i, ch in enumerate(text):
        if i:
            cx += track
        if ch in NARROW:
            nw = height * NARROW[ch]
            _draw_narrow(cr, ch, cx, y, nw, height, t, st, a)
            cx += nw
            continue
        lit = GLYPHS.get(ch)
        if lit is None:
            cx += dw
            continue
        on = set(lit)

        # Ghost pass: every unlit segment, barely there. This is what makes an
        # LED module read as hardware rather than as floating numerals.
        if st.ghost > 0.0:
            cr.set_source_rgba(r, g, b, st.ghost * a)
            for sid, (kind, ax, ay, bx, by) in geo.items():
                if sid in on:
                    continue
                _path_poly(cr, _seg_poly(kind, ax, ay, bx, by, t), cx, y, shear, height)
                cr.fill()

        # Bloom pass: a soft halo, approximated by one fattened low-alpha copy.
        if st.bloom > 0.0 and on:
            cr.set_source_rgba(r, g, b, 0.16 * st.bloom * a)
            cr.set_line_width(t * 0.9)
            cr.set_line_join(1)  # ROUND
            for sid in on:
                kind, ax, ay, bx, by = geo[sid]
                _path_poly(cr, _seg_poly(kind, ax, ay, bx, by, t), cx, y, shear, height)
                cr.stroke()

        cr.set_source_rgba(r, g, b, a)
        for sid in on:
            kind, ax, ay, bx, by = geo[sid]
            _path_poly(cr, _seg_poly(kind, ax, ay, bx, by, t), cx, y, shear, height)
            cr.fill()
        cx += dw

    return cx - x


def _draw_narrow(cr, ch: str, x: float, y: float, w: float, h: float,
                 t: float, st: SegmentStyle, a: float) -> None:
    r, g, b = st.lit
    cr.set_source_rgba(r, g, b, a)
    s = t * 0.82
    if ch == ".":
        cr.rectangle(x + (w - s) * 0.5, y + h - s, s, s)
        cr.fill()
    elif ch == ":":
        cr.rectangle(x + (w - s) * 0.5, y + h * 0.32 - s * 0.5, s, s)
        cr.rectangle(x + (w - s) * 0.5, y + h * 0.70 - s * 0.5, s, s)
        cr.fill()
    elif ch == "'":
        cr.rectangle(x + (w - s) * 0.5, y + h * 0.10, s, s * 1.4)
        cr.fill()
    elif ch == "/":
        cr.set_line_width(t * 0.80)
        cr.move_to(x + w * 0.88, y + h * 0.10)
        cr.line_to(x + w * 0.12, y + h * 0.90)
        cr.stroke()
    elif ch == "%":
        # Per-cent as a display element: two apertures and a virgule, struck
        # at segment weight so it sits with the digits.
        cr.set_line_width(max(1.0, t * 0.74))
        cr.move_to(x + w * 0.86, y + h * 0.16)
        cr.line_to(x + w * 0.14, y + h * 0.88)
        cr.stroke()
        d = max(1.5, t * 1.25)
        cr.rectangle(x + w * 0.06, y + h * 0.10, d, d)
        cr.rectangle(x + w * 0.94 - d, y + h * 0.90 - d, d, d)
        cr.fill()
    elif ch == "\u00b0":
        # A ring in the upper third of the cell, struck at segment thickness
        # so it carries the same weight as the digits beside it.
        rad = w * 0.42
        cr.set_line_width(max(1.0, t * 0.74))
        if st.bloom > 0.0:
            cr.set_source_rgba(r, g, b, 0.16 * st.bloom * a)
            cr.set_line_width(max(1.4, t * 1.35))
            cr.new_path()
            cr.arc(x + w * 0.5, y + h * 0.20 + rad, rad, 0.0, 6.283185307179586)
            cr.stroke()
            cr.set_source_rgba(r, g, b, a)
            cr.set_line_width(max(1.0, t * 0.74))
        cr.new_path()
        cr.arc(x + w * 0.5, y + h * 0.20 + rad, rad, 0.0, 6.283185307179586)
        cr.stroke()


def draw_right(cr, text: str, right_x: float, y: float, height: float,
               st: SegmentStyle = CYAN) -> float:
    """Right-aligned variant: readouts stay pinned while their digits change."""
    w = measure(text, height, st)
    draw(cr, text, right_x - w, y, height, st)
    return w


def fit_height(text: str, box_w: float, box_h: float,
               st: SegmentStyle = CYAN) -> float:
    """Largest digit height where `text` fits inside the box."""
    if not text:
        return 0.0
    unit = measure(text, 1.0, st)
    if unit <= 0.0:
        return 0.0
    return max(0.0, min(box_h, box_w / unit))


def draw_unit(cr, text: str, x: float, y: float, digit_h: float,
              st: SegmentStyle = CYAN, scale: float = 0.54) -> float:
    """Draw a UNIT beside a readout, in the same display technology.

    `y` and `digit_h` describe the MAIN digits. The unit is rendered at
    `scale` of that height and bottom-aligned to the digits' own baseline, so
    "83" + "\u00b0C" or "8.2/13.5" + "G" sit on one optical line and read as a
    single instrument rather than a number with a label stuck beside it.

    Returns the advance width.
    """
    if not text or digit_h < 4.0:
        return 0.0
    uh = digit_h * scale
    uy = y + digit_h - uh
    # A unit is a caption within the display: slightly dimmer than the value
    # it qualifies, which is what stops it competing with the digits.
    us = SegmentStyle(lit=st.lit, thickness=st.thickness * 1.10,
                      slant=st.slant, gap=st.gap, aspect=st.aspect,
                      tracking=st.tracking * 0.9, ghost=st.ghost * 0.55,
                      bloom=st.bloom * 0.8, alpha=st.alpha * 0.92)
    return draw(cr, text, x, uy, uh, us)


def measure_unit(text: str, digit_h: float, st: SegmentStyle = CYAN,
                 scale: float = 0.54) -> float:
    return measure(text, digit_h * scale, st) if text else 0.0
