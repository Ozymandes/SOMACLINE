//! Responsive layout STATES (not a single scaled poster). Port of `core/layout.py`.
//!
//! CONTRACT: `resolve(w, h)` is a pure function of the widget's logical size.
//! It returns an immutable Layout: a set of pixel rects plus a fixed type scale
//! for that state. It allocates nothing and is safe to call every frame.
//!
//! Font sizes are chosen PER STATE, not scaled proportionally with the window.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LayoutState {
    Compact,
    Instrument,
    Archive,
}

impl LayoutState {
    pub fn name(&self) -> &'static str {
        match self {
            LayoutState::Compact => "COMPACT",
            LayoutState::Instrument => "INSTRUMENT",
            LayoutState::Archive => "ARCHIVE",
        }
    }
}

/// Aspect range in which the six-module machine is used at all.
const SIX_MAX_ASPECT: f64 = 2.2;
const SIX_MIN_ASPECT: f64 = 0.72;

// Breakpoints on logical widget width, in px.
pub const BP_INSTRUMENT: f64 = 700.0;
pub const BP_ARCHIVE: f64 = 1100.0;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl Rect {
    pub const fn new(x: f64, y: f64, w: f64, h: f64) -> Rect {
        Rect { x, y, w, h }
    }
    pub const NONE: Rect = Rect::new(0.0, 0.0, 0.0, 0.0);

    #[inline]
    pub fn right(&self) -> f64 {
        self.x + self.w
    }
    #[inline]
    pub fn bottom(&self) -> f64 {
        self.y + self.h
    }
    #[inline]
    pub fn cx(&self) -> f64 {
        self.x + self.w / 2.0
    }
    #[inline]
    pub fn cy(&self) -> f64 {
        self.y + self.h / 2.0
    }
    pub fn inset(&self, dx: f64, dy: Option<f64>) -> Rect {
        let dy = dy.unwrap_or(dx);
        Rect::new(
            self.x + dx,
            self.y + dy,
            (self.w - 2.0 * dx).max(0.0),
            (self.h - 2.0 * dy).max(0.0),
        )
    }
    pub fn valid(&self) -> bool {
        self.w > 1.0 && self.h > 1.0
    }
}

/// Fixed sizes for one layout state, in logical px.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TypeScale {
    pub title: f64,
    pub specimen: f64,
    pub subtitle: f64,
    pub label: f64,
    pub value: f64,
    pub micro: f64,
    pub tracking: f64, // extra letter-spacing for labels, px
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Layout {
    pub state: LayoutState,
    pub width: f64,
    pub height: f64,
    pub pad: f64,
    pub header: Rect,  // title block
    pub stage: Rect,   // organism lives here, and ONLY here
    pub readout: Rect, // telemetry row / strip
    pub meta: Rect,    // extra metadata column (ARCHIVE only; may be invalid)
    pub controls: Rect, // specimen selector bank + mode key (may be invalid)
    pub footer: Rect,  // archive / status rail (may be invalid)
    pub chassis: Rect, // outer machine enclosure (may be invalid)
    pub status: Rect,  // header's secondary status rail (may be invalid)
    pub typ: TypeScale,
    pub show_subtitle: bool,
    pub show_meta: bool,
    pub show_controls: bool,
    pub show_footer: bool,
    pub show_chassis: bool,
    pub show_status: bool,
    pub readout_vertical: bool, // readouts stacked in a column (ARCHIVE side panel)
}

const T_COMPACT: TypeScale = TypeScale {
    title: 9.5, specimen: 13.0, subtitle: 8.0, label: 8.0,
    value: 15.0, micro: 7.5, tracking: 1.1,
};
const T_INSTRUMENT: TypeScale = TypeScale {
    title: 10.5, specimen: 17.0, subtitle: 9.5, label: 9.0,
    value: 21.0, micro: 8.5, tracking: 1.5,
};
const T_ARCHIVE: TypeScale = TypeScale {
    title: 12.0, specimen: 22.0, subtitle: 11.0, label: 10.0,
    value: 26.0, micro: 9.5, tracking: 2.0,
};

/// Pick a state from the *effective* width. Height participates: a very short
/// window is demoted.
pub fn classify(width: f64, height: f64) -> LayoutState {
    let mut effective = width;
    let aspect = width / height.max(1.0);
    if aspect > SIX_MAX_ASPECT || aspect < SIX_MIN_ASPECT {
        return LayoutState::Compact;
    }
    if height < 420.0 {
        effective = effective.min(BP_INSTRUMENT - 1.0);
    }
    if height < 560.0 {
        effective = effective.min(BP_ARCHIVE - 1.0);
    }
    if effective < BP_INSTRUMENT {
        LayoutState::Compact
    } else if effective < BP_ARCHIVE {
        LayoutState::Instrument
    } else {
        LayoutState::Archive
    }
}

// --- the six-module machine -------------------------------------------------
// Module rectangles read off references/Abbysal_Final.png (1448x1086).
const REF_CHASSIS: (f64, f64, f64, f64) = (40.0, 30.0, 1368.0, 1028.0);
const REF_HEADER: (f64, f64, f64, f64) = (55.0, 55.0, 1340.0, 145.0);
const REF_STAGE: (f64, f64, f64, f64) = (52.0, 222.0, 770.0, 568.0);
const REF_SELECTOR: (f64, f64, f64, f64) = (52.0, 800.0, 770.0, 152.0);
const REF_RACK: (f64, f64, f64, f64) = (838.0, 222.0, 534.0, 730.0);
const REF_FOOTER: (f64, f64, f64, f64) = (48.0, 962.0, 1350.0, 78.0);

/// Natural width/height of the selector module (1400x264).
const BANK_ASPECT: f64 = 1400.0 / 264.0;
/// The widest the observation window may become before the rack takes width.
const STAGE_MAX_ASPECT: f64 = 1.62;
const RACK_MAX_SHARE: f64 = 0.44;
const RACK_MIN_SHARE: f64 = 0.30;

// --- outer chassis ---------------------------------------------------------
const CHASSIS_MIN_W: f64 = 420.0;
const CHASSIS_MIN_H: f64 = 330.0;
const CHASSIS_MX: f64 = 40.0 / 1448.0;
const CHASSIS_MY: f64 = 30.0 / 1086.0;

/// COMPACT arrangement: header rail and telemetry strip heights, in px.
const COMPACT_HEAD: f64 = 38.0;
const COMPACT_STRIP: f64 = 52.0;

/// Pure: how tall the selector module is for a given column width.
pub fn bank_height(state: LayoutState, stage_w: f64, avail_h: f64) -> f64 {
    if state == LayoutState::Compact {
        return 0.0;
    }
    (stage_w / BANK_ASPECT).min(avail_h).max(0.0)
}

/// Type measured off references/Abbysal_Final.png at its own scale (s = 1).
const REF_TYPE: TypeScale = TypeScale {
    title: 18.0, specimen: 26.0, subtitle: 16.0, label: 14.5,
    value: 30.0, micro: 12.5, tracking: 2.6,
};

fn machine_type(s: f64, _base: TypeScale) -> TypeScale {
    let k = s.max(0.5).min(1.9);
    TypeScale {
        title: REF_TYPE.title * k,
        specimen: REF_TYPE.specimen * k,
        subtitle: REF_TYPE.subtitle * k,
        label: REF_TYPE.label * k,
        value: REF_TYPE.value * k,
        micro: (REF_TYPE.micro * k).max(7.0),
        tracking: (REF_TYPE.tracking * k).max(1.0),
    }
}

/// INSTRUMENT / ARCHIVE: the full six-module machine.
fn six_module(w: f64, h: f64, state: LayoutState, base: TypeScale) -> Layout {
    let c = Rect::new(
        w * CHASSIS_MX,
        h * CHASSIS_MY,
        w * (1.0 - 2.0 * CHASSIS_MX),
        h * (1.0 - 2.0 * CHASSIS_MY),
    );
    let (rx, ry, rw, rh) = REF_CHASSIS;
    let sx = c.w / rw;
    let sy = c.h / rh;
    let s = sx.min(sy);
    let t = machine_type(s, base);

    let x = |v: f64| c.x + (v - rx) * sx;
    let y = |v: f64| c.y + (v - ry) * sy;

    let (hx, hy, hw, hh) = REF_HEADER;
    let header = Rect::new(x(hx), y(hy), hw * sx, hh * sy);
    let (fx, fy, fw, fh) = REF_FOOTER;
    let footer = Rect::new(x(fx), y(fy), fw * sx, fh * sy);

    let body_top = y(REF_STAGE.1);
    let body_bot = y(REF_RACK.1 + REF_RACK.3);
    let body_h = body_bot - body_top;

    // Rack: manufactured proportions from the HEIGHT scale, then the right
    // margin exactly as the reference seats it (36 px of 1368).
    let margin_r = (rx + rw - (REF_RACK.0 + REF_RACK.2)) * s;
    let left = x(REF_STAGE.0);
    let gap = (REF_RACK.0 - (REF_STAGE.0 + REF_STAGE.2)) * s;
    let mut rack_w = REF_RACK.2 * sy;
    let mut col_w = c.right() - margin_r - rack_w - gap - left;
    let stage_h_est = body_h * (REF_STAGE.3 / REF_RACK.3);
    if col_w > stage_h_est * STAGE_MAX_ASPECT {
        col_w = stage_h_est * STAGE_MAX_ASPECT;
    }
    rack_w = c.right() - margin_r - gap - left - col_w;
    rack_w = (c.w * RACK_MIN_SHARE).max((c.w * RACK_MAX_SHARE).min(rack_w));
    col_w = c.right() - margin_r - rack_w - gap - left;
    let readout = Rect::new(c.right() - margin_r - rack_w, body_top, rack_w, body_h);

    // Selector: its own aspect, never stretched vertically.
    let sel_gap = (REF_SELECTOR.1 - (REF_STAGE.1 + REF_STAGE.3)) * sy;
    let sel_h = bank_height(state, col_w, REF_SELECTOR.3 * sy * 1.30);
    let controls = Rect::new(left, body_bot - sel_h, col_w, sel_h);
    let stage = Rect::new(
        left,
        body_top,
        col_w,
        (controls.y - sel_gap - body_top).max(0.0),
    );

    Layout {
        state,
        width: w,
        height: h,
        pad: 0.0,
        header,
        stage,
        readout,
        meta: Rect::NONE,
        controls,
        footer,
        chassis: c,
        status: Rect::NONE,
        typ: t,
        show_subtitle: true,
        show_meta: false,
        show_controls: sel_h > 20.0,
        show_footer: true,
        show_chassis: true,
        show_status: false,
        readout_vertical: true,
    }
}

/// COMPACT: a portable configuration, not a crushed machine.
fn compact(w: f64, h: f64, t: TypeScale) -> Layout {
    let show_chassis = w >= CHASSIS_MIN_W && h >= CHASSIS_MIN_H;
    let (c, content) = if show_chassis {
        let c = Rect::new(
            w * CHASSIS_MX,
            h * CHASSIS_MY,
            w * (1.0 - 2.0 * CHASSIS_MX),
            h * (1.0 - 2.0 * CHASSIS_MY),
        );
        let wall = (c.w.min(c.h) * 0.030).max(8.0);
        (c, c.inset(wall, Some(wall)))
    } else {
        (Rect::NONE, Rect::new(8.0, 8.0, (w - 16.0).max(0.0), (h - 16.0).max(0.0)))
    };
    let gap = 6.0;
    let head_h = COMPACT_HEAD.min(content.h * 0.16);
    let header = Rect::new(content.x, content.y, content.w, head_h);
    let strip_h = COMPACT_STRIP.min(content.h * 0.20);
    let readout = Rect::new(content.x, content.bottom() - strip_h, content.w, strip_h);
    let stage = Rect::new(
        content.x,
        header.bottom() + gap,
        content.w,
        (readout.y - gap - header.bottom() - gap).max(0.0),
    );
    Layout {
        state: LayoutState::Compact,
        width: w,
        height: h,
        pad: 8.0,
        header,
        stage,
        readout,
        meta: Rect::NONE,
        controls: Rect::NONE,
        footer: Rect::NONE,
        chassis: c,
        status: Rect::NONE,
        typ: t,
        show_subtitle: false,
        show_meta: false,
        show_controls: false,
        show_footer: false,
        show_chassis: show_chassis,
        show_status: false,
        readout_vertical: false,
    }
}

pub fn resolve(width: f64, height: f64) -> Layout {
    let w = width.max(1.0);
    let h = height.max(1.0);
    let state = classify(w, h);
    let t = match state {
        LayoutState::Compact => T_COMPACT,
        LayoutState::Instrument => T_INSTRUMENT,
        LayoutState::Archive => T_ARCHIVE,
    };
    match state {
        LayoutState::Compact => compact(w, h, t),
        _ => six_module(w, h, state, t),
    }
}
