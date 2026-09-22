//! Diagnostic overlay. Kept permanently in the product, toggled with F1.
//! Port of ui/debug.py.
//!
//! Hiding diagnostics is how scaling bugs survive. This shows the numbers
//! that would expose them: physical size, logical size, scale factor,
//! viewport bounds, render scale, and the simulation clock.

use cairo::FontSlant;
use cairo::FontWeight;

use crate::layout::Layout;
use crate::theme::{RULE, LIME};
use crate::viewport::{self, Viewport};

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

pub fn draw_debug(cr: &cairo::Context, layout: &Layout, vp: &Viewport, info: &DebugInfo) {
    let lines = [
        format!(
            "WIDGET   {:.0} x {:.0} logical",
            layout.width, layout.height
        ),
        format!(
            "SURFACE  {} x {} device   scale {:.2}",
            info.surf_w, info.surf_h, info.gdk_scale
        ),
        format!("STATE    {}", layout.state.name()),
        format!(
            "STAGE    {:.0} x {:.0} @ {:.0},{:.0}",
            vp.stage_w, vp.stage_h, vp.stage_x, vp.stage_y
        ),
        format!(
            "RSCALE   {:.5} px/world   organism r={:.1}px",
            vp.scale,
            vp.organism_px_radius()
        ),
        format!(
            "ISOTROPY {:.2e} px   clip={}",
            viewport::isotropy_error(vp),
            vp.clips()
        ),
        format!(
            "FPS      {:.1}   frame {:.2}ms  (sim {:.2} draw {:.2})",
            info.fps, info.frame_ms, info.sim_ms, info.draw_ms
        ),
        format!(
            "SIM      t={:.2}s  frames={}  resizes={}  rebuilds={}",
            info.sim_time, info.frames, info.resizes, info.rebuilds
        ),
        format!(
            "PHYS     ag={:.2} pu={:.2} de={:.2}",
            info.ag, info.pu, info.de
        ),
        format!("TELEM    {}", info.notes),
    ];

    let _ = cr.save();
    cr.select_font_face(
        crate::theme::FONT_MONO,
        FontSlant::Normal,
        FontWeight::Normal,
    );
    let size = 11.0f64;
    cr.set_font_size(size);
    let line_h = size * 1.45;

    let mut box_w = 0.0f64;
    for s in &lines {
        let ext = cr.text_extents(s).expect("text extents");
        box_w = box_w.max(ext.width());
    }
    box_w += 20.0;
    let box_h = line_h * lines.len() as f64 + 16.0;
    let x = 10.0;
    let y = 10.0f64.max(layout.height - box_h - 10.0);

    cr.set_source_rgba(0.01, 0.02, 0.03, 0.88);
    cr.rectangle(x, y, box_w, box_h);
    let _ = cr.fill();
    cr.set_source_rgba(RULE.0, RULE.1, RULE.2, 0.9);
    cr.set_line_width(1.0);
    cr.rectangle(x + 0.5, y + 0.5, box_w - 1.0, box_h - 1.0);
    let _ = cr.stroke();

    cr.set_source_rgba(LIME.0, LIME.1, LIME.2, 0.78);
    let mut ty = y + 10.0 + size;
    for s in &lines {
        cr.move_to(x + 10.0, ty);
        let _ = cr.show_text(s);
        ty += line_h;
    }
    let _ = cr.restore();
}
