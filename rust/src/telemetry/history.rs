//! Bounded telemetry history for the graph wells. Port of `telemetry/history.py`.
//!
//! Sampled at the TELEMETRY cadence (5 Hz), never at render FPS, so a graph's
//! time axis means seconds regardless of how fast the window draws.
//!
//! Each channel is a preallocated f32 ring. `push` is O(1) and allocates
//! nothing; `window(cols)` reduces the whole span to at most one value per
//! plot column (column mean), with a cached edge table so the reduction is a
//! single pass per frame.
//!
//! Faithfulness notes:
//! * `push` converts f64 -> f32 exactly like NumPy assignment (round-to-nearest).
//! * `window` reproduces `np.add.reduceat` semantics INCLUDING the empty-bin
//!   quirk: a bin whose edges coincide yields the raw element at the edge
//!   rather than a sum. (With the app's cap=300 and cols<=300 the edges are
//!   distinct, but the port keeps the exact behaviour.)
//! * `peak` propagates NaN like `np.max` (the thermal ring carries NaN gaps),
//!   it does not ignore them the way `f32::max` would.

use std::collections::HashMap;

/// Span shown by every graph, in seconds.
pub const SPAN_S: f64 = 60.0;

/// The four telemetry channels, in `History::CHANNELS` order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Channel {
    Cpu,
    Thermal,
    Memory,
    Frame,
}

impl Channel {
    pub const ALL: [Channel; 4] =
        [Channel::Cpu, Channel::Thermal, Channel::Memory, Channel::Frame];

    pub fn name(&self) -> &'static str {
        match self {
            Channel::Cpu => "cpu",
            Channel::Thermal => "thermal",
            Channel::Memory => "memory",
            Channel::Frame => "frame",
        }
    }
}

/// Python's round(): banker's rounding (half to even), not half away.
fn py_round(x: f64) -> f64 {
    let f = x.fract();
    if f == 0.5 || f == -0.5 {
        let r = x.round(); // half away from zero
        if (r as i64) % 2 != 0 {
            r - x.signum()
        } else {
            r
        }
    } else {
        x.round()
    }
}

pub struct Ring {
    pub cap: usize,
    buf: Vec<f32>,
    n: usize,
    head: usize, // next write position
    idx_cache: HashMap<usize, Vec<i64>>,
    /// Bumps on every push; lets a renderer cache the trace between samples.
    pub version: u64,
}

impl Ring {
    pub fn new(cap: usize) -> Ring {
        Ring {
            cap,
            buf: vec![0.0; cap],
            n: 0,
            head: 0,
            idx_cache: HashMap::new(),
            version: 0,
        }
    }

    pub fn len(&self) -> usize {
        self.n
    }

    pub fn is_empty(&self) -> bool {
        self.n == 0
    }

    pub fn push(&mut self, v: f64) {
        self.buf[self.head] = v as f32;
        self.head = (self.head + 1) % self.cap;
        if self.n < self.cap {
            self.n += 1;
        }
        self.version += 1;
    }

    pub fn last(&self) -> f64 {
        self.last_or(0.0)
    }

    pub fn last_or(&self, default: f64) -> f64 {
        if self.n == 0 {
            return default;
        }
        self.buf[(self.head + self.cap - 1) % self.cap] as f64
    }

    /// Oldest -> newest, length len(self). A copy, not a view.
    pub fn values(&self) -> Vec<f32> {
        if self.n < self.cap {
            self.buf[..self.n].to_vec()
        } else {
            let mut out = Vec::with_capacity(self.cap);
            out.extend_from_slice(&self.buf[self.head..]);
            out.extend_from_slice(&self.buf[..self.head]);
            out
        }
    }

    /// `np.max` over the live prefix: NaN propagates (unlike `f32::max`).
    pub fn peak(&self) -> f64 {
        if self.n == 0 {
            return 0.0;
        }
        let mut max = f32::NEG_INFINITY;
        for &v in &self.buf[..self.n] {
            if v.is_nan() {
                return f64::NAN;
            }
            if v > max {
                max = v;
            }
        }
        max as f64
    }

    fn edges_for(&mut self, cols: usize) -> Vec<i64> {
        if let Some(hit) = self.idx_cache.get(&cols) {
            return hit.clone();
        }
        if self.idx_cache.len() > 8 {
            self.idx_cache.clear();
        }
        // np.linspace(0, cap, cols+1).astype(np.int64): step = cap/cols,
        // edge[k] = trunc(k * step), last edge exactly cap.
        let step = self.cap as f64 / cols as f64;
        let mut edges: Vec<i64> = (0..cols).map(|k| ((k as f64) * step) as i64).collect();
        edges.push(self.cap as i64);
        self.idx_cache.insert(cols, edges.clone());
        edges
    }

    /// The full span reduced to `cols` column means, oldest first.
    ///
    /// Missing history (a freshly started monitor) comes back as NaN so the
    /// trace starts where data starts instead of dropping to zero.
    pub fn window(&mut self, cols: i32) -> Vec<f32> {
        let cols = (cols.max(2)) as usize;
        let v = self.values();
        let mut full = vec![f32::NAN; self.cap];
        if !v.is_empty() {
            full[self.cap - v.len()..].copy_from_slice(&v);
        }
        let edges = self.edges_for(cols);

        let mut out = Vec::with_capacity(cols);
        for i in 0..cols {
            let lo = edges[i] as usize;
            if i + 1 < cols {
                let hi_edge = edges[i + 1];
                if hi_edge == edges[i] as i64 {
                    // reduceat empty-bin quirk: raw element at the edge.
                    let e = full[lo.min(self.cap - 1)];
                    let c = if e.is_nan() { 0.0f32 } else { 1.0f32 };
                    out.push(if c > 0.0 { e / 1.0 } else { f32::NAN });
                    continue;
                }
                let hi = hi_edge as usize;
                out.push(column_mean(&full, lo, hi));
            } else {
                out.push(column_mean(&full, lo, self.cap));
            }
        }
        out
    }
}

/// Sum of non-NaN values and their count over [lo, hi), as
/// `where(c > 0, s / max(c, 1), NaN)`, accumulated in index order like
/// `np.add.reduceat` (same f32 rounding order).
fn column_mean(full: &[f32], lo: usize, hi: usize) -> f32 {
    let mut s = 0.0f32;
    let mut c = 0.0f32;
    for &x in &full[lo..hi] {
        if x.is_nan() {
            continue;
        }
        s += x;
        c += 1.0;
    }
    if c > 0.0 {
        s / c.max(1.0)
    } else {
        f32::NAN
    }
}

/// One ring per telemetry channel, all at the same cadence.
pub struct History {
    pub hz: f64,
    pub cap: usize,
    rings: [Ring; 4],
}

impl History {
    pub const CHANNELS: [Channel; 4] = Channel::ALL;

    pub fn new(hz: f64) -> History {
        let cap = py_round(SPAN_S * hz) as usize;
        History {
            hz,
            cap,
            rings: [
                Ring::new(cap),
                Ring::new(cap),
                Ring::new(cap),
                Ring::new(cap),
            ],
        }
    }

    pub fn ring(&self, channel: Channel) -> &Ring {
        match channel {
            Channel::Cpu => &self.rings[0],
            Channel::Thermal => &self.rings[1],
            Channel::Memory => &self.rings[2],
            Channel::Frame => &self.rings[3],
        }
    }

    pub fn ring_mut(&mut self, channel: Channel) -> &mut Ring {
        match channel {
            Channel::Cpu => &mut self.rings[0],
            Channel::Thermal => &mut self.rings[1],
            Channel::Memory => &mut self.rings[2],
            Channel::Frame => &mut self.rings[3],
        }
    }

    /// Python keeps dict order (cpu, thermal, memory, frame).
    pub fn get_by_name(&self, name: &str) -> Option<&Ring> {
        Channel::ALL
            .iter()
            .find(|c| c.name() == name)
            .map(|&c| self.ring(c))
    }

    pub fn push(&mut self, cpu_pct: f64, temp_c: Option<f64>, mem_gb: f64, frame_ms: f64) {
        self.rings[0].push(cpu_pct);
        self.rings[1].push(match temp_c {
            None => f64::NAN,
            Some(t) => t,
        });
        self.rings[2].push(mem_gb);
        self.rings[3].push(frame_ms);
    }

    pub fn nbytes(&self) -> usize {
        self.rings.iter().map(|r| r.cap * 4).sum()
    }
}
