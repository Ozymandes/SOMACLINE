//! F1 debug overlay. Port of ui/debug.py. PORT-LANE: internals.

/// Everything the overlay prints; built by the host each frame it is shown.
#[derive(Clone, Debug, Default)]
pub struct DebugInfo {
    pub surf_w: i64,
    pub surf_h: i64,
    pub gdk_scale: f64,
    pub fps: f64,
    pub frame_ms: f64,
    pub sim_ms: f64,
    pub draw_ms: f64,
    pub sim_time: f64,
    pub frames: u64,
    pub resizes: u64,
    pub rebuilds: u64,
    pub ag: f64,
    pub pu: f64,
    pub de: f64,
    pub species: String,
    pub switches: usize,
    pub notes: String,
}

pub fn draw_debug(
    _cr: &cairo::Context,
    _l: &crate::layout::Layout,
    _vp: &crate::viewport::Viewport,
    _info: &DebugInfo,
) {
    // PORT-LANE: ui/debug.py::draw_debug
}
