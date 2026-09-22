//! Pure-stdlib telemetry source: /proc + /sys sampling. Port of `telemetry/source.py`.
//!
//! Designed to be called at ~5 Hz from a GLib timeout on the UI thread:
//! `sample()` must be cheap, non-blocking, and must NEVER panic — on any
//! failure it degrades gracefully and returns the last known-good values.
//!
//! Faithfulness notes:
//! * Same files read, same normalization constants, same sensor priority
//!   table, and byte-identical `notes` / `temp_label` strings (the UI shows
//!   them).
//! * Python catches per-file exceptions and falls back to last-known-good;
//!   the Rust port never panics either and falls back identically.
//! * Python's final "construction failed" fallback cannot happen in Rust
//!   (struct construction is total), so it is intentionally absent.

use crate::signals::Telemetry;

/// Throughput that reads as "fully busy", in bytes/second. Deliberately low:
/// this channel drives a peripheral-event response on the organism.
const IO_FULL_SCALE: f64 = 40.0e6;

/// Temperature normalisation range (Celsius).
const TEMP_LO: f64 = 30.0;
const TEMP_HI: f64 = 95.0;

/// Preferred sensor selection order (chip name, then label substrings to
/// prefer within that chip, in priority order). Empty label list = any temp*.
const CHIP_LABEL_PRIORITY: &[(&str, &[&str])] = &[
    ("k10temp", &["tctl", "tdie"]),
    ("coretemp", &["package id 0"]),
    ("k10temp", &[]),
    ("acpitz", &[]),
    ("amdgpu", &[]),
];

fn read_text(path: &str) -> Option<String> {
    std::fs::read_to_string(path).ok().map(|s| s.trim().to_string())
}

fn read_int(path: &str) -> Option<i64> {
    let raw = read_text(path)?;
    raw.parse::<i64>().ok()
}

/// Process-wide monotonic seconds (Python `time.monotonic()`).
fn monotonic_secs() -> f64 {
    use std::sync::OnceLock;
    use std::time::Instant;
    static BASE: OnceLock<Instant> = OnceLock::new();
    let base = BASE.get_or_init(Instant::now);
    base.elapsed().as_secs_f64()
}

/// A resolved hwmon temperature sensor input file + short display tag.
struct SensorRef {
    input_path: String,
    label_tag: String,
}

/// (chip_name, hwmon_dir) for every readable hwmon device, sorted by path.
fn discover_hwmon_chips() -> Vec<(String, String)> {
    let mut chips: Vec<(String, String)> = Vec::new();
    if let Ok(entries) = std::fs::read_dir("/sys/class/hwmon") {
        let mut paths: Vec<String> = entries
            .flatten()
            .map(|e| e.path().to_string_lossy().to_string())
            .filter(|p| {
                std::path::Path::new(p)
                    .file_name()
                    .and_then(|n| n.to_str())
                    .map(|n| n.starts_with("hwmon"))
                    .unwrap_or(false)
            })
            .collect();
        paths.sort();
        for hwmon_dir in paths {
            if let Some(name) = read_text(&format!("{}/name", hwmon_dir)) {
                if !name.is_empty() {
                    chips.push((name, hwmon_dir));
                }
            }
        }
    }
    chips
}

/// (input_path, label_or_none) for one hwmon chip dir, sorted by input name.
fn temp_inputs_for_chip(hwmon_dir: &str) -> Vec<(String, Option<String>)> {
    let mut results: Vec<(String, Option<String>)> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(hwmon_dir) {
        let mut inputs: Vec<String> = entries
            .flatten()
            .map(|e| e.path().to_string_lossy().to_string())
            .filter(|p| {
                std::path::Path::new(p)
                    .file_name()
                    .and_then(|n| n.to_str())
                    .map(|n| n.starts_with("temp") && n.ends_with("_input"))
                    .unwrap_or(false)
            })
            .collect();
        inputs.sort();
        for input_path in inputs {
            let label_path = input_path.replace("_input", "_label");
            let label = read_text(&label_path);
            results.push((input_path, label.filter(|l| !l.is_empty())));
        }
    }
    results
}

/// Probe /sys/class/hwmon once and pick the best temperature sensor.
/// Returns (sensor_ref_or_none, note_string).
fn select_sensor() -> (Option<SensorRef>, String) {
    let chips = discover_hwmon_chips();
    if chips.is_empty() {
        return (None, "no hwmon chips found".to_string());
    }

    let mut by_name: Vec<(String, Vec<String>)> = Vec::new();
    for (name, path) in &chips {
        match by_name.iter_mut().find(|(n, _)| n == name) {
            Some((_, dirs)) => dirs.push(path.clone()),
            None => by_name.push((name.clone(), vec![path.clone()])),
        }
    }

    // Try the priority list first.
    for (chip_name, label_prefs) in CHIP_LABEL_PRIORITY {
        let Some(dirs) = by_name.iter().find(|(n, _)| n == chip_name).map(|(_, d)| d) else {
            continue;
        };
        for hwmon_dir in dirs {
            let inputs = temp_inputs_for_chip(hwmon_dir);
            if inputs.is_empty() {
                continue;
            }
            if !label_prefs.is_empty() {
                for pref in *label_prefs {
                    for (input_path, label) in &inputs {
                        if let Some(label) = label {
                            if label.to_lowercase().contains(pref) {
                                return (
                                    Some(SensorRef {
                                        input_path: input_path.clone(),
                                        label_tag: chip_name.to_uppercase(),
                                    }),
                                    format!("temp={chip_name}:{label}"),
                                );
                            }
                        }
                    }
                }
                // no matching label on this chip's inputs; try the next dir.
                continue;
            } else {
                // Accept the first readable input on this chip.
                let (input_path, label) = &inputs[0];
                let shown = label.clone().unwrap_or_else(|| "temp1".to_string());
                return (
                    Some(SensorRef {
                        input_path: input_path.clone(),
                        label_tag: chip_name.to_uppercase(),
                    }),
                    format!("temp={chip_name}:{shown}"),
                );
            }
        }
    }

    // Fall back: any hwmon temp*_input at all.
    for (name, hwmon_dir) in &chips {
        let inputs = temp_inputs_for_chip(hwmon_dir);
        if let Some((input_path, label)) = inputs.first() {
            let shown = label.clone().unwrap_or_else(|| "temp1".to_string());
            return (
                Some(SensorRef {
                    input_path: input_path.clone(),
                    label_tag: name.to_uppercase(),
                }),
                format!("temp={name}:{shown} (fallback)"),
            );
        }
    }

    (None, "no readable temp*_input found".to_string())
}

fn read_int_from_meminfo_key(key: &str) -> Option<i64> {
    let text = std::fs::read_to_string("/proc/meminfo").ok()?;
    for line in text.lines() {
        if line.starts_with(&format!("{key}:")) {
            if let Some(field) = line.split_whitespace().nth(1) {
                return field.parse::<i64>().ok();
            }
        }
    }
    None
}

/// Stdlib-only sampler of CPU load, memory pressure, and temperature.
pub struct TelemetrySource {
    // CPU delta baseline.
    prev_cpu_total: Option<i64>,
    prev_cpu_idle: Option<i64>,

    // Last-known-good values for graceful degradation.
    last_cpu_load: f64,
    last_cpu_pct: f64,
    last_mem_pressure: f64,
    last_mem_used_gb: f64,
    last_mem_total_gb: f64,
    last_temp: f64,
    last_temp_c: Option<f64>,

    prev_io: Option<(f64, i64, i64)>,
    last_io_rate: f64,
    last_io_mb: f64,
    last_net_mb: f64,

    #[allow(dead_code)]
    mem_total_kb: Option<i64>,
    sensor: Option<SensorRef>,
    notes: String,
}

impl Default for TelemetrySource {
    fn default() -> Self {
        Self::new()
    }
}

impl TelemetrySource {
    pub fn new() -> TelemetrySource {
        let mem_total_kb = read_int_from_meminfo_key("MemTotal");
        let last_mem_total_gb = mem_total_kb.unwrap_or(0) as f64 / (1024.0 * 1024.0);

        let (sensor, sensor_note) = select_sensor();

        TelemetrySource {
            prev_cpu_total: None,
            prev_cpu_idle: None,
            last_cpu_load: 0.0,
            last_cpu_pct: 0.0,
            last_mem_pressure: 0.0,
            last_mem_used_gb: 0.0,
            last_mem_total_gb,
            last_temp: 0.0,
            last_temp_c: None,
            prev_io: None,
            last_io_rate: 0.0,
            last_io_mb: 0.0,
            last_net_mb: 0.0,
            mem_total_kb,
            sensor,
            notes: format!("cpu=/proc/stat mem=/proc/meminfo {sensor_note}"),
        }
    }

    // -- block I/O and network -----------------------------------------
    /// Combined block + network throughput, normalised.
    ///
    /// Both counters are monotonic totals, so a rate needs two samples; the
    /// first call therefore reports nothing rather than a spike. Any failure
    /// degrades to "unavailable".
    fn sample_io(&mut self) -> (f64, bool, f64, f64) {
        let now = monotonic_secs();
        let mut blk: i64 = 0;
        match std::fs::read_to_string("/proc/diskstats") {
            Ok(text) => {
                let mut failed = false;
                for line in text.lines() {
                    let p: Vec<&str> = line.split_whitespace().collect();
                    if p.len() < 10 {
                        continue;
                    }
                    let name = p[2];
                    // Whole devices only: partitions would double count.
                    if name.starts_with("loop")
                        || name.starts_with("ram")
                        || name.starts_with("zram")
                        || name.starts_with("dm-")
                    {
                        continue;
                    }
                    if name
                        .chars()
                        .last()
                        .map(|c| c.is_ascii_digit())
                        .unwrap_or(false)
                        && !name.starts_with("nvme")
                    {
                        continue;
                    }
                    if name.starts_with("nvme") && name.contains('p') {
                        continue;
                    }
                    // sectors read (p[5]) + sectors written (p[9]), * 512.
                    match (p[5].parse::<i64>(), p[9].parse::<i64>()) {
                        (Ok(a), Ok(b)) => blk = blk.saturating_add((a + b) * 512),
                        _ => {
                            failed = true;
                            break;
                        }
                    }
                }
                if failed {
                    return (self.last_io_rate, false, self.last_io_mb, self.last_net_mb);
                }
            }
            Err(_) => {
                return (self.last_io_rate, false, self.last_io_mb, self.last_net_mb);
            }
        }

        let mut net: i64 = 0;
        match std::fs::read_to_string("/proc/net/dev") {
            Ok(text) => {
                let mut failed = false;
                for line in text.lines().skip(2) {
                    let Some((iface, rest)) = line.split_once(':') else {
                        // Python: no ':' -> partition gives ('', '', line);
                        // rest would have no fields -> IndexError -> fail.
                        failed = true;
                        break;
                    };
                    if iface.trim() == "lo" {
                        continue;
                    }
                    let p: Vec<&str> = rest.split_whitespace().collect();
                    if p.len() < 9 {
                        failed = true;
                        break;
                    }
                    match (p[0].parse::<i64>(), p[8].parse::<i64>()) {
                        (Ok(a), Ok(b)) => net = net.saturating_add(a + b),
                        _ => {
                            failed = true;
                            break;
                        }
                    }
                }
                if failed {
                    net = 0;
                }
            }
            Err(_) => {
                net = 0;
            }
        }

        let prev = self.prev_io.replace((now, blk, net));
        let Some(prev) = prev else {
            return (0.0, false, 0.0, 0.0);
        };
        let dt = now - prev.0;
        if dt <= 0.0 {
            return (self.last_io_rate, true, self.last_io_mb, self.last_net_mb);
        }
        let d_blk = (blk - prev.1).max(0) as f64 / dt;
        let d_net = (net - prev.2).max(0) as f64 / dt;
        let rate = ((d_blk + d_net) / IO_FULL_SCALE).min(1.0);
        self.last_io_rate = rate;
        self.last_io_mb = d_blk / 1e6;
        self.last_net_mb = d_net / 1e6;
        (rate, true, self.last_io_mb, self.last_net_mb)
    }

    // -- CPU -----------------------------------------------------------
    /// Return (cpu_load 0..1, cpu_pct 0..100). Never panics.
    fn sample_cpu(&mut self) -> (f64, f64) {
        let line = match std::fs::read_to_string("/proc/stat") {
            Ok(text) => match text.lines().next() {
                Some(l) => l.to_string(),
                None => return (self.last_cpu_load, self.last_cpu_pct),
            },
            Err(_) => return (self.last_cpu_load, self.last_cpu_pct),
        };

        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.is_empty() || parts[0] != "cpu" {
            return (self.last_cpu_load, self.last_cpu_pct);
        }
        let mut fields: Vec<i64> = Vec::with_capacity(parts.len() - 1);
        for p in &parts[1..] {
            match p.parse::<i64>() {
                Ok(v) => fields.push(v),
                Err(_) => return (self.last_cpu_load, self.last_cpu_pct),
            }
        }
        if fields.len() < 4 {
            return (self.last_cpu_load, self.last_cpu_pct);
        }

        // user nice system idle iowait irq softirq steal guest guest_nice
        let idle_fields = fields[3] + if fields.len() > 4 { fields[4] } else { 0 };
        let total: i64 = fields.iter().sum();

        let Some(prev_total) = self.prev_cpu_total else {
            self.prev_cpu_total = Some(total);
            self.prev_cpu_idle = Some(idle_fields);
            self.last_cpu_load = 0.0;
            self.last_cpu_pct = 0.0;
            return (0.0, 0.0);
        };

        let total_delta = total - prev_total;
        let idle_delta = match self.prev_cpu_idle {
            Some(prev_idle) => idle_fields - prev_idle,
            None => 0,
        };

        self.prev_cpu_total = Some(total);
        self.prev_cpu_idle = Some(idle_fields);

        if total_delta <= 0 {
            return (self.last_cpu_load, self.last_cpu_pct);
        }

        let busy_delta = total_delta - idle_delta;
        let load = busy_delta as f64 / total_delta as f64;
        let load = load.clamp(0.0, 1.0);

        self.last_cpu_load = load;
        self.last_cpu_pct = load * 100.0;
        (self.last_cpu_load, self.last_cpu_pct)
    }

    // -- Memory ----------------------------------------------------------
    /// Return (memory_pressure 0..1, mem_used_gb, mem_total_gb). Never panics.
    fn sample_memory(&mut self) -> (f64, f64, f64) {
        let fallback = || (self.last_mem_pressure, self.last_mem_used_gb, self.last_mem_total_gb);
        let text = match std::fs::read_to_string("/proc/meminfo") {
            Ok(t) => t,
            Err(_) => return fallback(),
        };
        let mut mem_total_kb: Option<i64> = None;
        let mut mem_available_kb: Option<i64> = None;
        for line in text.lines() {
            if line.starts_with("MemTotal:") {
                if let Some(f) = line.split_whitespace().nth(1) {
                    match f.parse::<i64>() {
                        Ok(v) => mem_total_kb = Some(v),
                        Err(_) => return fallback(),
                    }
                } else {
                    return fallback();
                }
            } else if line.starts_with("MemAvailable:") {
                if let Some(f) = line.split_whitespace().nth(1) {
                    match f.parse::<i64>() {
                        Ok(v) => mem_available_kb = Some(v),
                        Err(_) => return fallback(),
                    }
                } else {
                    return fallback();
                }
            }
            if mem_total_kb.is_some() && mem_available_kb.is_some() {
                break;
            }
        }

        let Some(total) = mem_total_kb else {
            return fallback();
        };
        if total == 0 {
            return fallback();
        }
        let available = mem_available_kb.unwrap_or(total);

        let pressure = 1.0 - (available as f64 / total as f64);
        let pressure = pressure.clamp(0.0, 1.0);

        let used_kb = total - available;
        let used_gb = used_kb as f64 / (1024.0 * 1024.0);
        let total_gb = total as f64 / (1024.0 * 1024.0);

        self.last_mem_pressure = pressure;
        self.last_mem_used_gb = used_gb;
        self.last_mem_total_gb = total_gb;
        (pressure, used_gb, total_gb)
    }

    // -- Temperature -------------------------------------------------------
    /// Return (temperature 0..1, temp_available, temp_c, temp_label). Never panics.
    fn sample_temp(&mut self) -> (f64, bool, Option<f64>, String) {
        let (input_path, label_tag) = match self.sensor.as_ref() {
            Some(s) => (s.input_path.clone(), s.label_tag.clone()),
            None => return (0.0, false, None, "--".to_string()),
        };

        let raw = match read_int(&input_path) {
            Some(r) => r,
            None => {
                // Sensor went away / unreadable this sample; degrade but keep
                // the label so the UI shows it's the same sensor, just stale.
                if let Some(last_c) = self.last_temp_c {
                    return (self.last_temp, true, Some(last_c), label_tag);
                }
                return (0.0, false, None, "--".to_string());
            }
        };

        let temp_c = raw as f64 / 1000.0;
        let norm = (temp_c - TEMP_LO) / (TEMP_HI - TEMP_LO);
        let norm = norm.clamp(0.0, 1.0);

        self.last_temp = norm;
        self.last_temp_c = Some(temp_c);
        (norm, true, Some(temp_c), label_tag)
    }

    // -- Public API ----------------------------------------------------------
    pub fn sample(&mut self) -> Telemetry {
        let (cpu_load, cpu_pct) = self.sample_cpu();
        let (mem_pressure, mem_used_gb, mem_total_gb) = self.sample_memory();
        let (temperature, temp_available, temp_c, temp_label) = self.sample_temp();
        let (io_rate, io_available, io_mb_s, net_mb_s) = self.sample_io();

        Telemetry {
            cpu_load,
            memory_pressure: mem_pressure,
            temperature,
            temp_available,
            cpu_pct,
            mem_used_gb,
            mem_total_gb,
            temp_c,
            temp_label,
            io_rate,
            io_available,
            io_mb_s,
            net_mb_s,
            notes: self.notes.clone(),
        }
    }
}
