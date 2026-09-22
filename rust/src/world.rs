//! Logical world definition. The simulation lives here and NEVER sees pixels.
//!
//! CONTRACT (binding on every module):
//!   * The organism is authored in a fixed square world of WORLD_SIZE units,
//!     centred on (0, 0). Valid coordinates are [-WORLD_HALF, +WORLD_HALF].
//!   * WORLD_RADIUS is the design radius: the organism's outermost reach at
//!     full expansion. Everything must fit inside it so nothing ever clips.
//!   * Nothing in telemetry/ or the organism modules may touch pixels, widget
//!     sizes or GTK. Conversion happens in exactly one place: `viewport`.

pub const WORLD_SIZE: f64 = 1000.0;
pub const WORLD_HALF: f64 = WORLD_SIZE / 2.0;

/// The organism must stay within this radius so it never touches the frame.
pub const WORLD_RADIUS: f64 = 460.0;
