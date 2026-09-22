//! The hardware registry: which sprite, and how it is allowed to scale.
//! Port of skin/catalog.py.

use crate::skin::surface::NineSlice;

// --- 9-sliceable frames ---------------------------------------------------
pub static OBSERVATION_BEZEL: NineSlice = NineSlice::new(
    "frame/observation_bezel", 150, 140, 146, 144, 96.0, 85.0, 100.0, 93.0,
);
pub static SEGMENT_HOUSING: NineSlice = NineSlice::new(
    "frame/segment_housing", 120, 70, 120, 95, 87.0, 48.0, 87.0, 69.0,
);
pub static SEGMENT_HOUSING_WIDE: NineSlice = NineSlice::new(
    "frame/segment_housing_wide", 74, 47, 74, 43, 55.0, 35.0, 55.0, 32.0,
);
// pad_b is the BEVEL only, not the sprite's caption ledge.
pub static GRAPH_WELL: NineSlice = NineSlice::new(
    "frame/graph_well", 72, 44, 72, 110, 51.0, 30.0, 52.0, 44.0,
);
pub static METER_TROUGH: NineSlice = NineSlice::new(
    "frame/meter_trough", 53, 31, 53, 42, 39.0, 23.0, 39.0, 31.0,
);
pub static AUX_FRAME: NineSlice = NineSlice::new(
    "frame/aux_frame", 74, 50, 74, 111, 55.0, 37.0, 55.0, 82.0,
);

// Generic console metal: the recessed plaque. Insets deliberately small so a
// short rail keeps a usable interior. Content pads are the RECESSED FIELD,
// not the first bevel step.
pub static PLATE: NineSlice = NineSlice::new(
    "frame/plate_recessed", 24, 18, 24, 18, 32.0, 30.0, 26.0, 28.0,
);

// --- state sprite families -------------------------------------------------
pub const LAMP_STATES: [&str; 6] =
    ["off", "standby", "active", "nominal", "warning", "critical"];

/// size: 'small' | 'primary'; state: one of LAMP_STATES.
pub fn lamp(size: &str, state: &str) -> String {
    let state = if LAMP_STATES.contains(&state) { state } else { "off" };
    format!("lamp/{size}_{state}")
}

pub const SELECTOR_STATES: [&str; 5] =
    ["idle", "focus", "pressed", "latched", "disabled"];

/// The canonical specimen keys: one bespoke engraved plate per organism, in a
/// raised/unlit and a seated/illuminated state.
pub const SPECIMEN_KEY_PLATES: [&str; 2] = ["inactive", "active"];

/// Sprite name for specimen `index` (0-based) in `plate` state.
pub fn specimen_key(index: usize, plate: &str) -> String {
    let plate = if SPECIMEN_KEY_PLATES.contains(&plate) { plate } else { "inactive" };
    format!("specimen/key_{:02}_{plate}", index % 5 + 1)
}

pub fn selector_cell(state: &str) -> String {
    let state = if SELECTOR_STATES.contains(&state) { state } else { "idle" };
    format!("selector/cell_{state}")
}

pub const SELECTOR_CAP_L: &str = "selector/cap_left";
pub const SELECTOR_CAP_R: &str = "selector/cap_right";

pub const MODE_STATES: [&str; 4] = ["inactive", "armed", "active", "error"];

pub fn mode_key(state: &str) -> String {
    let state = if MODE_STATES.contains(&state) { state } else { "inactive" };
    format!("mode/{state}")
}

pub const CYCLE_STATES: [&str; 3] = ["neutral", "prev", "next"];

pub fn cycle_key(state: &str) -> String {
    let state = if CYCLE_STATES.contains(&state) { state } else { "neutral" };
    format!("cycle/{state}")
}

/// Natural aspect of one selector cell (tools/build_sprites.py: 224 x 303).
pub const SELECTOR_CELL_ASPECT: f64 = 224.0 / 303.0;
pub const SELECTOR_CAP_ASPECT: f64 = 48.0 / 303.0;
