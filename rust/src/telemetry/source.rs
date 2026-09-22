// PORT-LANE: telemetry/source.rs (owns /proc + /sys sampling, TelemetrySource)
pub struct TelemetrySource;

impl TelemetrySource {
    pub fn new() -> Self { TelemetrySource }
    pub fn sample(&mut self) -> crate::signals::Telemetry {
        crate::signals::Telemetry::new()
    }
}
