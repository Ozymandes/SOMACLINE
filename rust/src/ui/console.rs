//! Console presentation model + layer/regional surface management.
//!
//! PORT-LANE NOTE: this file owns the PUBLIC API the host (app.rs) codes
//! against; the host lane fills in the internals (chrome, modules, graphs).
//! Python counterpart: ui/console.py. Layers and regions are cached cairo
//! surfaces whose pixels are written exactly once when their content changes
//! (a GSK snapshot may hold them); the id is a monotonic slot key so the
//! host's texture cache can never go stale.

use crate::layout::{Layout, Rect};
use crate::signals::Telemetry;
use crate::species::Species;
use crate::telemetry::History;
use std::cell::RefCell;
use std::rc::Rc;

/// Which control was hit by hit_controls().
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CtlKind {
    Key,
    Cycle,
    Mode,
}

/// A cached raster layer. `id` is stable for the lifetime of the surface;
/// a content change produces a NEW id and surface (pixels written once).
#[derive(Clone)]
pub struct Layer {
    pub id: u64,
    pub surface: cairo::ImageSurface,
}

/// One live region: a cached surface plus its logical-pixel placement.
#[derive(Clone)]
pub struct Region {
    pub layer: Layer,
    pub rect: Rect,
}

/// Console presentation state, mutated by the host and read by the renderer.
pub struct ConsoleModel {
    pub species: &'static Species,
    pub active: usize,
    pub history: Rc<RefCell<History>>,
    pub diag: bool,
    /// "inactive" | "armed" | "active" | "error"
    pub mode_state: String,
    pub focus: Option<usize>,
    pub pressed: Option<usize>,
    pub switches: usize,
    // live observation-field values (refreshed by the host each frame)
    pub phase: f64,
    pub rotation: f64,
    pub behavior: String,
    pub flux: f64,
    pub surge: f64,
    pub magnification: f64,
    pub field_mm: f64,
}

impl ConsoleModel {
    pub fn new(
        species: &'static Species,
        active: usize,
        history: Rc<RefCell<History>>,
        diag: bool,
    ) -> ConsoleModel {
        ConsoleModel {
            species,
            active,
            history,
            diag,
            mode_state: "inactive".to_string(),
            focus: None,
            pressed: None,
            switches: 0,
            phase: 0.0,
            rotation: 0.08,
            behavior: "STABLE".to_string(),
            flux: 0.0,
            surge: 0.0,
            magnification: 0.1,
            field_mm: 0.01,
        }
    }
}

/// The glass rect: where the organism is drawn, and ONLY where.
pub fn stage_content(_l: &Layout) -> Rect {
    // PORT-LANE: ui/console.py::stage_content
    Rect::NONE
}

/// Pure hit test against the selector bank / mode key.
pub fn hit_controls(_l: &Layout, _px: f64, _py: f64) -> Option<(CtlKind, usize)> {
    // PORT-LANE: ui/console.py::hit_controls
    None
}

/// Cached console renderer: owns the static-chrome and live-region surfaces.
pub struct Renderer {
    next_id: u64,
}

impl Default for Renderer {
    fn default() -> Self {
        Self::new()
    }
}

impl Renderer {
    pub fn new() -> Renderer {
        Renderer { next_id: 1 }
    }

    fn fresh(&mut self, w: i32, h: i32) -> Layer {
        let surface = cairo::ImageSurface::create(cairo::Format::ARgb32, w.max(1), h.max(1))
            .expect("surface");
        let id = self.next_id;
        self.next_id += 1;
        Layer { id, surface }
    }

    /// LAYERS 0-1: background, shell, glass, graticule. None when nothing to draw.
    pub fn layer_under(&mut self, _l: &Layout, _m: &ConsoleModel) -> Option<Layer> {
        // PORT-LANE: ui/console.py::layer_under
        None
    }

    /// LAYER 3: the structural modules and static type. None when nothing.
    pub fn layer_over(
        &mut self,
        _l: &Layout,
        _m: &ConsoleModel,
        _tel: &Telemetry,
    ) -> Option<Layer> {
        // PORT-LANE: ui/console.py::layer_over
        None
    }

    /// LAYERS 4-6: live regions, each re-rendered only on its own change.
    pub fn regions(
        &mut self,
        _l: &Layout,
        _m: &ConsoleModel,
        _tel: &Telemetry,
        _fps: f64,
        _frame_ms: f64,
        _under: Option<&Layer>,
        _over: Option<&Layer>,
    ) -> Vec<Region> {
        // PORT-LANE: ui/console.py::regions
        Vec::new()
    }
}
