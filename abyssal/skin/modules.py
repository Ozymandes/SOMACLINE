"""The six structural hardware modules, and the bays manufactured into them.

WHAT THIS IS
------------
The final machine is built from exactly six generated modules (see
references/Abbysal_Module_Map.png):

    01 SHELL        master chassis, the bottom layer
    02 HEADER       two-row system fascia
    03 OBSERVATION  scope bezel with a TRUE transparent aperture
    04 RACK         the four-row telemetry rack, one coherent body
    05 SELECTOR     key mounting plate, five trued transparent wells
    06 FOOTER       bottom status rail

Every module is a sprite PLUS the measured source-pixel rectangle of each
recess cut into it. Live content is drawn INTO those recesses by name.

HOW A MODULE SCALES: MULTI-BAND SLICING
---------------------------------------
A 9-slice has one stretchable band per axis. These modules have many
compartments, and stretching one middle band would drag a divider rib or a
screw with it. Each module therefore declares its own list of STRETCH BANDS
per axis - source intervals that lie inside blank recess floors or plain
metal. Everything outside a band is scaled by one uniform factor `k` (screws
stay round, ribs stay the same width); the remaining length on that axis is
shared among the bands in proportion to their source length.

`place()` returns the piecewise map; bays and points go through exactly the
same map the pixels do, so a bay a caller is handed is where the metal is.
Drawing happens once per size into the static cache (ui.console), never per
frame.

Bays below were measured off the runtime sprites by connected-component
analysis (tools/build_modules.py --measure), not by eye.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cairo

from ..core.layout import Rect
from .surface import sprite, sprite_size


@dataclass(frozen=True, slots=True)
class AxisMap:
    """Piecewise-linear source -> destination map for one axis."""

    src: tuple[float, ...]    # breakpoints in source px, ascending
    dst: tuple[float, ...]    # matching destination offsets

    def __call__(self, v: float) -> float:
        s, d = self.src, self.dst
        if v <= s[0]:
            return d[0] + (v - s[0]) * self._slope(0)
        for i in range(len(s) - 1):
            if v <= s[i + 1]:
                t = (v - s[i]) / max(1e-9, s[i + 1] - s[i])
                return d[i] + t * (d[i + 1] - d[i])
        return d[-1] + (v - s[-1]) * self._slope(len(s) - 2)

    def _slope(self, i: int) -> float:
        return (self.dst[i + 1] - self.dst[i]) / max(1e-9, self.src[i + 1] - self.src[i])

    def segments(self):
        for i in range(len(self.src) - 1):
            yield self.src[i], self.src[i + 1], self.dst[i], self.dst[i + 1]


def _axis(size: int, bands: tuple[tuple[float, float], ...], dst: float,
          k: float) -> AxisMap:
    """Fixed parts scale by k; bands share whatever length is left.

    If the fixed parts alone would overflow `dst` (no band slack), the whole
    axis falls back to one uniform factor, which can only shrink it - the
    module then never exceeds its rect.
    """
    fixed = size - sum(b - a for a, b in bands)
    band_len = size - fixed
    slack = dst - fixed * k
    if not bands or band_len <= 0 or slack < band_len * 0.15 * k:
        return AxisMap((0.0, float(size)), (0.0, dst))
    stretch = slack / band_len
    src = [0.0]
    out = [0.0]
    pos = 0.0
    for a, b in bands:
        pos += (a - src[-1]) * k
        src.append(float(a))
        out.append(pos)
        pos += (b - a) * stretch
        src.append(float(b))
        out.append(pos)
    pos += (size - src[-1]) * k
    src.append(float(size))
    out.append(pos)
    return AxisMap(tuple(src), tuple(out))


@dataclass(frozen=True, slots=True)
class Placed:
    module: "Module"
    x: float
    y: float
    w: float
    h: float
    mx: AxisMap
    my: AxisMap
    k: float

    def bay(self, key: str) -> Rect:
        src = self.module.bays.get(key)
        if src is None:
            return Rect(0.0, 0.0, 0.0, 0.0)
        return self.map_rect(*src)

    def map_rect(self, sx: float, sy: float, sw: float, sh: float) -> Rect:
        x0, x1 = self.x + self.mx(sx), self.x + self.mx(sx + sw)
        y0, y1 = self.y + self.my(sy), self.y + self.my(sy + sh)
        return Rect(x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))

    def point(self, sx: float, sy: float) -> tuple[float, float]:
        return (self.x + self.mx(sx), self.y + self.my(sy))

    @property
    def rect(self) -> Rect:
        return Rect(self.x, self.y, self.w, self.h)


@dataclass(frozen=True, slots=True)
class Module:
    name: str
    sw: int
    sh: int
    bands_x: tuple[tuple[float, float], ...]
    bands_y: tuple[tuple[float, float], ...]
    bays: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    #: The module's OUTER fasteners, (cx, cy, r) in source px, measured off
    #: the sprite. qa/production_gates.py asserts no two modules put a screw
    #: in the same place - the doubled-fastener fault of earlier builds.
    screws: tuple[tuple[float, float, float], ...] = ()

    @property
    def aspect(self) -> float:
        return self.sw / float(self.sh)

    @property
    def available(self) -> bool:
        return sprite_size(self.name)[0] > 0

    def place(self, r: Rect, k: float | None = None) -> Placed:
        """Map the module onto `r`. `k` is the scale of the fixed parts;
        by default the uniform factor that fits the module inside `r`."""
        w, h = max(1.0, r.w), max(1.0, r.h)
        if k is None:
            k = min(w / self.sw, h / self.sh)
        mx = _axis(self.sw, self.bands_x, w, k)
        my = _axis(self.sh, self.bands_y, h, k)
        return Placed(self, r.x, r.y, w, h, mx, my, k)

    def draw(self, cr, r: Rect, k: float | None = None,
             alpha: float = 1.0) -> Placed:
        p = self.place(r, k)
        surf = sprite(self.name)
        if surf is None:
            return p
        # Scale the real source dimensions: the registry is authored against
        # the build size, and a rebuilt sprite may differ by a pixel.
        fx = surf.get_width() / float(self.sw)
        fy = surf.get_height() / float(self.sh)
        for sx0, sx1, dx0, dx1 in p.mx.segments():
            if sx1 - sx0 <= 0 or dx1 - dx0 <= 0:
                continue
            for sy0, sy1, dy0, dy1 in p.my.segments():
                if sy1 - sy0 <= 0 or dy1 - dy0 <= 0:
                    continue
                cr.save()
                # Tile edges are snapped outward by a hair so neighbouring
                # tiles overlap instead of leaving an antialiased seam.
                cr.rectangle(p.x + dx0 - 0.02, p.y + dy0 - 0.02,
                             dx1 - dx0 + 0.04, dy1 - dy0 + 0.04)
                cr.clip()
                cr.translate(p.x + dx0, p.y + dy0)
                cr.scale((dx1 - dx0) / ((sx1 - sx0) * fx),
                         (dy1 - dy0) / ((sy1 - sy0) * fy))
                cr.translate(-sx0 * fx, -sy0 * fy)
                cr.set_source_surface(surf, 0, 0)
                pat = cr.get_source()
                pat.set_filter(cairo.FILTER_GOOD)
                pat.set_extend(cairo.EXTEND_PAD)
                if alpha >= 1.0:
                    cr.paint()
                else:
                    cr.paint_with_alpha(alpha)
                cr.restore()
        return p


# ---------------------------------------------------------------------------
# 01 SHELL - 1600x1172. Nothing passes through it: it is the bottom layer.
#     Bands avoid the side lugs (y 375-449, 742-816) and the corners.
SHELL = Module(
    "module/shell", 1600, 1172,
    bands_x=((180.0, 1420.0),),
    bands_y=((190.0, 360.0), (470.0, 725.0), (835.0, 1000.0)),
    bays={"face": (97.0, 135.0, 1407.0, 921.0)},
    screws=(),            # the shell carries none: every screw is a child's
)

# 02 HEADER - 1800x185. Bands are the x ranges that are recess floor in BOTH
#     rows, so no rib, screw or lamp socket is ever stretched.
HEADER = Module(
    "module/header", 1800, 185,
    bands_x=((110.0, 600.0), (700.0, 1095.0), (1430.0, 1690.0)),
    bands_y=((32.0, 94.0), (131.0, 162.0)),
    bays={
        "title": (79.0, 21.0, 586.0, 84.0),
        "epithet": (678.0, 21.0, 435.0, 85.0),
        "live": (1126.0, 21.0, 264.0, 84.0),
        "live_lamp": (1163.0, 39.0, 54.0, 51.0),
        "live_window": (1239.0, 44.0, 127.0, 45.0),
        "clock": (1402.0, 21.0, 312.0, 85.0),
        "rail_0": (86.0, 124.0, 537.0, 45.0),
        "rail_1": (636.0, 124.0, 523.0, 45.0),
        "rail_2": (1172.0, 124.0, 542.0, 45.0),
    },
    screws=((40.0, 42.0, 20.0), (1761.0, 42.0, 20.0),
            (40.0, 141.0, 21.0), (1760.0, 140.5, 20.5)),
)

# 03 OBSERVATION - 1300x948, aperture fully transparent.
#     Bands skip the two small lip notches at x 198-247 and 754-799.
OBSERVATION = Module(
    "module/observation", 1300, 948,
    bands_x=((260.0, 740.0), (815.0, 1040.0)),
    bands_y=((200.0, 750.0),),
    bays={
        "aperture": (128.0, 128.0, 1044.0, 697.0),
        # the cyan-black glass edge; live content is clipped to this
        "glass": (120.0, 121.0, 1059.0, 709.0),
    },
    screws=((50.0, 51.5, 28.5), (1250.5, 51.5, 28.5),
            (50.5, 891.5, 29.0), (1250.0, 891.5, 29.0)),
)

# 04 RACK - 900x1213. Four rows on a 294.7 px pitch.
_ROW_Y = (0.0, 294.0, 589.0, 884.0)


def _rack_bays() -> dict[str, tuple[float, float, float, float]]:
    b: dict[str, tuple[float, float, float, float]] = {}
    for i, oy in enumerate(_ROW_Y):
        b[f"r{i}_plate"] = (112.0, 55.0 + oy, 108.0, 38.0)
        b[f"r{i}_title"] = (230.0, 55.0 + oy, 431.0, 35.0)
        b[f"r{i}_graph"] = (99.0, 116.0 + oy, 266.0, 146.0 if i < 3 else 141.0)
        b[f"r{i}_numeric"] = (409.0, 116.0 + oy, 252.0, 102.0 if i < 3 else 99.0)
        b[f"r{i}_meter"] = (409.0, 233.0 + oy, 254.0, 30.0)
        b[f"r{i}_state"] = (705.0, 116.0 + oy, 106.0, 146.0 if i < 3 else 141.0)
        for j, lx in enumerate((697.0, 732.0, 767.0, 801.0)):
            b[f"r{i}_lamp{j}"] = (lx - 13.0, 56.0 + oy, 26.0, 26.0)
        b[f"r{i}_row"] = (69.0, 35.0 + oy, 767.0, 250.0)
    return b


RACK = Module(
    "module/rack", 900, 1213,
    # x: inside the graph well and the numeric well (the title bay is blank)
    bands_x=((245.0, 335.0), (440.0, 630.0)),
    # y: inside each row's well block, clear of the top strip and meter
    bands_y=tuple((140.0 + oy, 215.0 + oy) for oy in _ROW_Y),
    bays=_rack_bays(),
    screws=((41.5, 40.5, 17.5), (860.5, 41.0, 17.5),
            (37.5, 1174.0, 17.0), (861.0, 1174.0, 17.0)),
)

# 05 SELECTOR - 1400x264 (generation 4: a dark trough in a thin screwed
#     frame, as the reference has it). Wells trued to one size on one exact
#     pitch by tools/build_modules.py (pitch 200.66, max residual < 1 px).
SEL_PITCH = 200.6596
SEL_X0 = 294.0374   # first aperture centre, from assets/sprites/module/build.json


def _selector_bays() -> dict[str, tuple[float, float, float, float]]:
    b: dict[str, tuple[float, float, float, float]] = {}
    for i in range(5):
        cx = SEL_X0 + i * SEL_PITCH
        b[f"well_{i}"] = (cx - 66.5, 65.5, 133.0, 138.0)
        # 168 tall x 168 * 256/267 wide: the approved key's exact aspect. It
        # covers the aperture (65.5-203.5) and its lip ring, and stops above
        # the identifier band so the two never touch.
        b[f"key_{i}"] = (cx - 80.539, 36.0, 161.079, 168.0)
        # the engraved identifier, on the trough floor beneath the key
        b[f"ledge_{i}"] = (cx - 82.0, 207.5, 164.0, 26.0)
    # the approved controls at their authored aspects (1.70, 0.708), covering
    # their openings
    b["rocker"] = (66.5, 99.15, 110.0, 64.7)
    b["mode"] = (1225.4, 75.0, 89.2, 126.0)
    b["rocker_opening"] = (77.0, 115.0, 89.0, 33.0)
    b["mode_opening"] = (1236.0, 85.0, 68.0, 106.0)
    b["trough"] = (32.0, 27.0, 1336.0, 208.0)
    return b


SELECTOR = Module(
    "module/selector", 1400, 264,
    bands_x=((180.0, 205.0), (1185.0, 1218.0)),
    bands_y=(),
    bays=_selector_bays(),
    screws=((25.5, 26.5, 15.5), (1374.5, 26.5, 15.5),
            (25.5, 237.5, 15.5), (1374.0, 237.5, 15.5)),
)

# 06 FOOTER - 1800x93. Seven information bays and a terminal bay.
_FOOT_BAYS = ((237.0, 168.0), (418.0, 154.0), (585.0, 146.0), (744.0, 146.0),
              (903.0, 146.0), (1061.0, 145.0), (1219.0, 147.0))
FOOTER = Module(
    "module/footer", 1800, 93,
    bands_x=tuple((x + 12.0, x + w - 12.0) for x, w in _FOOT_BAYS)
    + ((1392.0, 1733.0),),
    bands_y=((34.0, 64.0),),
    bays=dict({f"bay_{i}": (x, 26.0, w, 47.0) for i, (x, w) in enumerate(_FOOT_BAYS)},
              terminal=(1378.0, 26.0, 369.0, 47.0),
              badge=(43.0, 21.0, 185.0, 54.0)),
    screws=((22.0, 22.5, 13.0), (1777.5, 22.5, 13.5),
            (23.0, 70.0, 13.0), (1777.5, 70.0, 13.0)),
)

ALL = (SHELL, HEADER, OBSERVATION, RACK, SELECTOR, FOOTER)
