//! The six structural hardware modules, and the bays manufactured into them.
//! Port of skin/modules.py.
//!
//! HOW A MODULE SCALES: MULTI-BAND SLICING. Each module declares STRETCH
//! BANDS per axis - source intervals inside blank recess floors or plain
//! metal. Everything outside a band scales by one uniform factor `k`; the
//! remaining length is shared among the bands in proportion to their source
//! length. `place()` returns the piecewise map; bays and points go through
//! exactly the same map the pixels do.

use std::collections::HashMap;

use cairo::Context as CairoContext;
use cairo::Filter;

use crate::layout::Rect;
use crate::skin::surface::sprite;

/// Piecewise-linear source -> destination map for one axis.
#[derive(Clone, Debug)]
pub struct AxisMap {
    /// breakpoints in source px, ascending
    pub src: Vec<f64>,
    /// matching destination offsets
    pub dst: Vec<f64>,
}

impl AxisMap {
    pub fn new(src: Vec<f64>, dst: Vec<f64>) -> Self {
        AxisMap { src, dst }
    }

    pub fn call(&self, v: f64) -> f64 {
        let (s, d) = (&self.src, &self.dst);
        if v <= s[0] {
            return d[0] + (v - s[0]) * self.slope(0);
        }
        for i in 0..s.len() - 1 {
            if v <= s[i + 1] {
                let t = (v - s[i]) / (s[i + 1] - s[i]).max(1e-9);
                return d[i] + t * (d[i + 1] - d[i]);
            }
        }
        let n = s.len();
        d[n - 1] + (v - s[n - 1]) * self.slope(n - 2)
    }

    fn slope(&self, i: usize) -> f64 {
        (self.dst[i + 1] - self.dst[i]) / (self.src[i + 1] - self.src[i]).max(1e-9)
    }

    pub fn segments(&self) -> impl Iterator<Item = (f64, f64, f64, f64)> + '_ {
        (0..self.src.len() - 1)
            .map(move |i| (self.src[i], self.src[i + 1], self.dst[i], self.dst[i + 1]))
    }
}

/// Fixed parts scale by k; bands share whatever length is left.
fn axis_map(
    size: i32,
    bands: &[(f64, f64)],
    dst: f64,
    k: f64,
) -> AxisMap {
    let fixed = size as f64 - bands.iter().map(|(a, b)| b - a).sum::<f64>();
    let band_len = size as f64 - fixed;
    let slack = dst - fixed * k;
    if bands.is_empty() || band_len <= 0.0 || slack < band_len * 0.15 * k {
        return AxisMap::new(vec![0.0, size as f64], vec![0.0, dst]);
    }
    let stretch = slack / band_len;
    let mut src = vec![0.0];
    let mut out = vec![0.0];
    let mut pos = 0.0;
    for (a, b) in bands {
        pos += (a - src[src.len() - 1]) * k;
        src.push(*a);
        out.push(pos);
        pos += (b - a) * stretch;
        src.push(*b);
        out.push(pos);
    }
    pos += (size as f64 - src[src.len() - 1]) * k;
    src.push(size as f64);
    out.push(pos);
    AxisMap::new(src, out)
}

/// One module mapped onto a rect, with its piecewise axis maps.
#[derive(Clone)]
pub struct Placed {
    pub module: &'static Module,
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
    pub mx: AxisMap,
    pub my: AxisMap,
    pub k: f64,
}

impl Placed {
    pub fn bay(&self, key: &str) -> Rect {
        let Some(&[sx, sy, sw, sh]) = self.module.bay_src(key) else {
            return Rect::NONE;
        };
        self.map_rect(sx, sy, sw, sh)
    }

    pub fn map_rect(&self, sx: f64, sy: f64, sw: f64, sh: f64) -> Rect {
        let (x0, x1) = (self.x + self.mx.call(sx), self.x + self.mx.call(sx + sw));
        let (y0, y1) = (self.y + self.my.call(sy), self.y + self.my.call(sy + sh));
        Rect::new(x0, y0, (x1 - x0).max(0.0), (y1 - y0).max(0.0))
    }

    pub fn point(&self, sx: f64, sy: f64) -> (f64, f64) {
        (self.x + self.mx.call(sx), self.y + self.my.call(sy))
    }

    pub fn rect(&self) -> Rect {
        Rect::new(self.x, self.y, self.w, self.h)
    }
}

type BayTable = &'static [(&'static str, [f64; 4])];

/// One structural hardware module.
#[derive(Clone, Copy)]
pub struct Module {
    pub name: &'static str,
    pub sw: i32,
    pub sh: i32,
    pub bands_x: &'static [(f64, f64)],
    pub bands_y: &'static [(f64, f64)],
    /// The module's OUTER fasteners, (cx, cy, r) in source px.
    pub screws: &'static [(f64, f64, f64)],
    bays_fn: fn() -> BayTable,
}

impl Module {
    pub fn aspect(&self) -> f64 {
        self.sw as f64 / self.sh as f64
    }

    pub fn available(&self) -> bool {
        sprite(self.name).is_some()
    }

    pub fn bays(&self) -> BayTable {
        (self.bays_fn)()
    }

    /// Public bay lookup on the SOURCE table (used by the parity tests).
    pub fn bay_src_pub(&self, key: &str) -> Option<&[f64; 4]> {
        self.bay_src(key)
    }

    fn bay_src(&self, key: &str) -> Option<&[f64; 4]> {
        self.bays().iter().find(|(k, _)| *k == key).map(|(_, v)| v)
    }

    /// Map the module onto `r`. `k` is the scale of the fixed parts; by
    /// default the uniform factor that fits the module inside `r`.
    pub fn place(&'static self, r: Rect, k: Option<f64>) -> Placed {
        let w = r.w.max(1.0);
        let h = r.h.max(1.0);
        let k = k.unwrap_or_else(|| (w / self.sw as f64).min(h / self.sh as f64));
        let mx = axis_map(self.sw, self.bands_x, w, k);
        let my = axis_map(self.sh, self.bands_y, h, k);
        Placed { module: self, x: r.x, y: r.y, w, h, mx, my, k }
    }

    pub fn draw(&'static self, cr: &CairoContext, r: Rect, k: Option<f64>, alpha: f64) -> Placed {
        let p = self.place(r, k);
        let Some(surf) = sprite(self.name) else {
            return p;
        };
        // Scale the real source dimensions: the registry is authored against
        // the build size, and a rebuilt sprite may differ by a pixel.
        let fx = surf.width() as f64 / self.sw as f64;
        let fy = surf.height() as f64 / self.sh as f64;
        for (sx0, sx1, dx0, dx1) in p.mx.segments() {
            if sx1 - sx0 <= 0.0 || dx1 - dx0 <= 0.0 {
                continue;
            }
            for (sy0, sy1, dy0, dy1) in p.my.segments() {
                if sy1 - sy0 <= 0.0 || dy1 - dy0 <= 0.0 {
                    continue;
                }
                let _ = cr.save();
                // Tile edges are snapped outward by a hair so neighbouring
                // tiles overlap instead of leaving an antialiased seam.
                cr.rectangle(
                    p.x + dx0 - 0.02,
                    p.y + dy0 - 0.02,
                    dx1 - dx0 + 0.04,
                    dy1 - dy0 + 0.04,
                );
                cr.clip();
                cr.translate(p.x + dx0, p.y + dy0);
                cr.scale(
                    (dx1 - dx0) / ((sx1 - sx0) * fx),
                    (dy1 - dy0) / ((sy1 - sy0) * fy),
                );
                cr.translate(-sx0 * fx, -sy0 * fy);
                let _ = cr.set_source_surface(&surf, 0.0, 0.0);
                let pat = cr.source();
                pat.set_filter(Filter::Good);
                pat.set_extend(cairo::Extend::Pad);
                if alpha >= 1.0 {
                    let _ = cr.paint();
                } else {
                    let _ = cr.paint_with_alpha(alpha);
                }
                let _ = cr.restore();
            }
        }
        p
    }
}

// ---------------------------------------------------------------------------
// 01 SHELL - 1600x1172. Nothing passes through it: it is the bottom layer.
pub static SHELL: Module = Module {
    name: "module/shell",
    sw: 1600,
    sh: 1172,
    bands_x: &[(180.0, 1420.0)],
    bands_y: &[(190.0, 360.0), (470.0, 725.0), (835.0, 1000.0)],
    screws: &[],
    bays_fn: || BAYS_SHELL,
};
static BAYS_SHELL: BayTable = &[("face", [97.0, 135.0, 1407.0, 921.0])];

// 02 HEADER - 1800x185.
pub static HEADER: Module = Module {
    name: "module/header",
    sw: 1800,
    sh: 185,
    bands_x: &[(110.0, 600.0), (700.0, 1095.0), (1430.0, 1690.0)],
    bands_y: &[(32.0, 94.0), (131.0, 162.0)],
    screws: &[
        (40.0, 42.0, 20.0),
        (1761.0, 42.0, 20.0),
        (40.0, 141.0, 21.0),
        (1760.0, 140.5, 20.5),
    ],
    bays_fn: || BAYS_HEADER,
};
static BAYS_HEADER: BayTable = &[
    ("title", [79.0, 21.0, 586.0, 84.0]),
    ("epithet", [678.0, 21.0, 435.0, 85.0]),
    ("live", [1126.0, 21.0, 264.0, 84.0]),
    ("live_lamp", [1163.0, 39.0, 54.0, 51.0]),
    ("live_window", [1239.0, 44.0, 127.0, 45.0]),
    ("clock", [1402.0, 21.0, 312.0, 85.0]),
    ("rail_0", [86.0, 124.0, 537.0, 45.0]),
    ("rail_1", [636.0, 124.0, 523.0, 45.0]),
    ("rail_2", [1172.0, 124.0, 542.0, 45.0]),
];

// 03 OBSERVATION - 1300x948, aperture fully transparent.
pub static OBSERVATION: Module = Module {
    name: "module/observation",
    sw: 1300,
    sh: 948,
    bands_x: &[(260.0, 740.0), (815.0, 1040.0)],
    bands_y: &[(200.0, 750.0)],
    screws: &[
        (50.0, 51.5, 28.5),
        (1250.5, 51.5, 28.5),
        (50.5, 891.5, 29.0),
        (1250.0, 891.5, 29.0),
    ],
    bays_fn: || BAYS_OBSERVATION,
};
static BAYS_OBSERVATION: BayTable = &[
    ("aperture", [128.0, 128.0, 1044.0, 697.0]),
    ("glass", [120.0, 121.0, 1059.0, 709.0]),
];

// 04 RACK - 900x1213. Four rows on a 294.7 px pitch.
const ROW_Y: [f64; 4] = [0.0, 294.0, 589.0, 884.0];

fn rack_bays() -> BayTable {
    use std::sync::OnceLock;
    static RACK_BAYS: OnceLock<Vec<(&'static str, [f64; 4])>> = OnceLock::new();
    RACK_BAYS.get_or_init(|| {
        let mut b: Vec<(&'static str, [f64; 4])> = Vec::new();
        for (i, &oy) in ROW_Y.iter().enumerate() {
            let tail = if i < 3 { 146.0 } else { 141.0 };
            let ntail = if i < 3 { 102.0 } else { 99.0 };
            let oy = oy;
            b.push((leak_key(&format!("r{i}_plate")), [112.0, 55.0 + oy, 108.0, 38.0]));
            b.push((leak_key(&format!("r{i}_title")), [230.0, 55.0 + oy, 431.0, 35.0]));
            b.push((leak_key(&format!("r{i}_graph")), [99.0, 116.0 + oy, 266.0, tail]));
            b.push((leak_key(&format!("r{i}_numeric")), [409.0, 116.0 + oy, 252.0, ntail]));
            b.push((leak_key(&format!("r{i}_meter")), [409.0, 233.0 + oy, 254.0, 30.0]));
            b.push((leak_key(&format!("r{i}_state")), [705.0, 116.0 + oy, 106.0, tail]));
            for (j, &lx) in [697.0, 732.0, 767.0, 801.0].iter().enumerate() {
                b.push((
                    leak_key(&format!("r{i}_lamp{j}")),
                    [lx - 13.0, 56.0 + oy, 26.0, 26.0],
                ));
            }
            b.push((leak_key(&format!("r{i}_row")), [69.0, 35.0 + oy, 767.0, 250.0]));
        }
        b
    })
}

// 05 SELECTOR - 1400x264. Wells trued to one size on one exact pitch.
pub const SEL_PITCH: f64 = 200.6596;
pub const SEL_X0: f64 = 294.0374;

fn selector_bays() -> BayTable {
    use std::sync::OnceLock;
    static SEL_BAYS: OnceLock<Vec<(&'static str, [f64; 4])>> = OnceLock::new();
    SEL_BAYS.get_or_init(|| {
        let mut b: Vec<(&'static str, [f64; 4])> = Vec::new();
        for i in 0..5 {
            let cx = SEL_X0 + i as f64 * SEL_PITCH;
            b.push((leak_key(&format!("well_{i}")), [cx - 66.5, 65.5, 133.0, 138.0]));
            b.push((leak_key(&format!("key_{i}")), [cx - 80.539, 36.0, 161.079, 168.0]));
            b.push((leak_key(&format!("ledge_{i}")), [cx - 82.0, 207.5, 164.0, 26.0]));
        }
        b.push(("rocker", [66.5, 99.15, 110.0, 64.7]));
        b.push(("mode", [1225.4, 75.0, 89.2, 126.0]));
        b.push(("rocker_opening", [77.0, 115.0, 89.0, 33.0]));
        b.push(("mode_opening", [1236.0, 85.0, 68.0, 106.0]));
        b.push(("trough", [32.0, 27.0, 1336.0, 208.0]));
        b
    })
}

thread_local! {
    static KEY_POOL: std::cell::RefCell<HashMap<String, &'static str>> =
        std::cell::RefCell::new(HashMap::new());
}

fn leak_key(s: &str) -> &'static str {
    KEY_POOL.with_borrow_mut(|pool| {
        if let Some(k) = pool.get(s) {
            return *k;
        }
        let leaked: &'static str = Box::leak(s.to_string().into_boxed_str());
        pool.insert(s.to_string(), leaked);
        leaked
    })
}

pub static RACK: Module = Module {
    name: "module/rack",
    sw: 900,
    sh: 1213,
    bands_x: &[(245.0, 335.0), (440.0, 630.0)],
    bands_y: &[(140.0, 215.0), (434.0, 509.0), (729.0, 804.0), (1024.0, 1099.0)],
    screws: &[
        (41.5, 40.5, 17.5),
        (860.5, 41.0, 17.5),
        (37.5, 1174.0, 17.0),
        (861.0, 1174.0, 17.0),
    ],
    bays_fn: rack_bays,
};

pub static SELECTOR: Module = Module {
    name: "module/selector",
    sw: 1400,
    sh: 264,
    bands_x: &[(180.0, 205.0), (1185.0, 1218.0)],
    bands_y: &[],
    screws: &[
        (25.5, 26.5, 15.5),
        (1374.5, 26.5, 15.5),
        (25.5, 237.5, 15.5),
        (1374.0, 237.5, 15.5),
    ],
    bays_fn: selector_bays,
};

// 06 FOOTER - 1800x93. Seven information bays and a terminal bay.
const FOOT_BAYS: [(f64, f64); 7] = [
    (237.0, 168.0),
    (418.0, 154.0),
    (585.0, 146.0),
    (744.0, 146.0),
    (903.0, 146.0),
    (1061.0, 145.0),
    (1219.0, 147.0),
];

pub static FOOTER: Module = Module {
    name: "module/footer",
    sw: 1800,
    sh: 93,
    bands_x: &[
        (249.0, 393.0),
        (430.0, 560.0),
        (597.0, 719.0),
        (756.0, 878.0),
        (915.0, 1037.0),
        (1073.0, 1194.0),
        (1231.0, 1354.0),
        (1392.0, 1733.0),
    ],
    bands_y: &[(34.0, 64.0)],
    screws: &[
        (22.0, 22.5, 13.0),
        (1777.5, 22.5, 13.5),
        (23.0, 70.0, 13.0),
        (1777.5, 70.0, 13.0),
    ],
    bays_fn: footer_bays,
};

fn footer_bays() -> BayTable {
    use std::sync::OnceLock;
    static FOOTER_BAYS: OnceLock<Vec<(&'static str, [f64; 4])>> = OnceLock::new();
    FOOTER_BAYS.get_or_init(|| {
        let mut b: Vec<(&'static str, [f64; 4])> = FOOT_BAYS
            .iter()
            .enumerate()
            .map(|(i, &(x, w))| (leak_key(&format!("bay_{i}")), [x, 26.0, w, 47.0]))
            .collect();
        b.push(("terminal", [1378.0, 26.0, 369.0, 47.0]));
        b.push(("badge", [43.0, 21.0, 185.0, 54.0]));
        b
    })
}

pub static ALL: [Module; 6] = [SHELL, HEADER, OBSERVATION, RACK, SELECTOR, FOOTER];
