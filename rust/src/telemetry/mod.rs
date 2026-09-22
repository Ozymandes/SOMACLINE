//! Telemetry sampling and bounded history. Ports of telemetry/*.

pub mod history;
pub mod source;

pub use history::History;
pub use source::TelemetrySource;
