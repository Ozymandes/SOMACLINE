//! Parity gate: Rust telemetry + history vs the Python oracle.
//!
//! Golden files are produced by tools/parity_dump.py from the Python
//! implementation (the acceptance oracle). This file replays the EXACT same
//! protocols and asserts agreement.
//!
//! Tolerance policy (deliberate, documented):
//! * Physiology replay: 1e-9 ABSOLUTE. The model is deterministic closed-form
//!   arithmetic on f64; same libm on the same machine gives bit-identical
//!   exp/sin. The only admitted difference: telemetry.json stores the
//!   NORMALISED temperature, so the replay reconstructs
//!   temp_c = 30 + temperature*65 (the dump protocol maps
//!   temp_c -> temperature = (temp_c-30)/65 unclamped, hot in [60,90] C), a
//!   round-trip that can differ by <= 1 ULP from the original temp_c. That
//!   perturbs the EMA outputs by ~1e-15, far inside 1e-9. If a real
//!   arithmetic bug exists it shows up orders of magnitude above this line.
//! * History replay: 1e-4. The push sequence is regenerated with the dump's
//!   sin/cos formulas; numpy's scalar sin can differ from libm by <=1 ULP,
//!   and the rings store f32. Column means over 300 f32 samples keep such
//!   noise at ~1e-13; 1e-4 is generous against real errors.
//!
//! Failures print the channel/field/step and the max error; tolerances are
//! never relaxed without fixing the arithmetic difference first.

use abyssal::signals::Telemetry;
use abyssal::telemetry::history::{Channel, History};

// ---------------------------------------------------------------------------
// Minimal JSON reader, tolerant of Python's `NaN`/`Infinity`/`null` tokens.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
enum Json {
    Null,
    Num(f64),
    Bool(bool),
    Str(String),
    Arr(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

struct Parser<'a> {
    b: &'a [u8],
    i: usize,
}

impl<'a> Parser<'a> {
    fn ws(&mut self) {
        while self.i < self.b.len() && self.b[self.i].is_ascii_whitespace() {
            self.i += 1;
        }
    }

    fn value(&mut self) -> Result<Json, String> {
        self.ws();
        match self.b.get(self.i) {
            None => Err("eof".into()),
            Some(b'{') => {
                self.i += 1;
                let mut out = Vec::new();
                self.ws();
                if self.b.get(self.i) == Some(&b'}') {
                    self.i += 1;
                    return Ok(Json::Obj(out));
                }
                loop {
                    self.ws();
                    let key = match self.value()? {
                        Json::Str(s) => s,
                        _ => return Err("object key must be string".into()),
                    };
                    self.ws();
                    if self.b.get(self.i) != Some(&b':') {
                        return Err("expected ':'".into());
                    }
                    self.i += 1;
                    let val = self.value()?;
                    out.push((key, val));
                    self.ws();
                    match self.b.get(self.i) {
                        Some(b',') => {
                            self.i += 1;
                        }
                        Some(b'}') => {
                            self.i += 1;
                            return Ok(Json::Obj(out));
                        }
                        _ => return Err("expected ',' or '}'".into()),
                    }
                }
            }
            Some(b'[') => {
                self.i += 1;
                let mut out = Vec::new();
                self.ws();
                if self.b.get(self.i) == Some(&b']') {
                    self.i += 1;
                    return Ok(Json::Arr(out));
                }
                loop {
                    let val = self.value()?;
                    out.push(val);
                    self.ws();
                    match self.b.get(self.i) {
                        Some(b',') => {
                            self.i += 1;
                        }
                        Some(b']') => {
                            self.i += 1;
                            return Ok(Json::Arr(out));
                        }
                        _ => return Err("expected ',' or ']'".into()),
                    }
                }
            }
            Some(b'"') => {
                self.i += 1;
                let mut s = String::new();
                while let Some(&c) = self.b.get(self.i) {
                    self.i += 1;
                    match c {
                        b'"' => return Ok(Json::Str(s)),
                        b'\\' => {
                            // The golden files carry no escapes; accept the
                            // next byte verbatim if one ever appears.
                            if let Some(&e) = self.b.get(self.i) {
                                s.push(e as char);
                                self.i += 1;
                            }
                        }
                        _ => s.push(c as char),
                    }
                }
                Err("unterminated string".into())
            }
            Some(c) if c.is_ascii_digit() || *c == b'-' || *c == b'+' => {
                let start = self.i;
                self.i += 1;
                while let Some(&c) = self.b.get(self.i) {
                    if c.is_ascii_digit()
                        || matches!(c, b'-' | b'+' | b'.' | b'e' | b'E')
                    {
                        self.i += 1;
                    } else {
                        break;
                    }
                }
                let text = std::str::from_utf8(&self.b[start..self.i]).unwrap();
                text.parse::<f64>()
                    .map(Json::Num)
                    .map_err(|e| format!("bad number {text:?}: {e}"))
            }
            Some(_) => {
                // Bare identifiers: NaN, Infinity, -Infinity, null, true, false.
                let start = self.i;
                while let Some(&c) = self.b.get(self.i) {
                    if c.is_ascii_alphabetic() || c == b'-' {
                        self.i += 1;
                    } else {
                        break;
                    }
                }
                let word = std::str::from_utf8(&self.b[start..self.i]).unwrap();
                match word {
                    "NaN" => Ok(Json::Num(f64::NAN)),
                    "Infinity" => Ok(Json::Num(f64::INFINITY)),
                    "-Infinity" => Ok(Json::Num(f64::NEG_INFINITY)),
                    "null" | "None" => Ok(Json::Null),
                    "true" => Ok(Json::Bool(true)),
                    "false" => Ok(Json::Bool(false)),
                    other => Err(format!("unexpected token {other:?}")),
                }
            }
        }
    }
}

fn parse_json(text: &str) -> Json {
    let mut p = Parser { b: text.as_bytes(), i: 0 };
    match p.value() {
        Ok(v) => v,
        Err(e) => panic!("golden JSON parse error: {e}"),
    }
}

impl Json {
    fn num(&self) -> f64 {
        match self {
            Json::Num(v) => *v,
            other => panic!("expected number, got {other:?}"),
        }
    }
    fn opt_num(&self) -> Option<f64> {
        match self {
            Json::Num(v) => Some(*v),
            _ => None,
        }
    }
    fn boolean(&self) -> bool {
        match self {
            Json::Bool(v) => *v,
            other => panic!("expected bool, got {other:?}"),
        }
    }
    fn arr(&self) -> &[Json] {
        match self {
            Json::Arr(a) => a,
            other => panic!("expected array, got {other:?}"),
        }
    }
    fn get(&self, key: &str) -> &Json {
        match self {
            Json::Obj(o) => o
                .iter()
                .find(|(k, _)| k == key)
                .map(|(_, v)| v)
                .unwrap_or_else(|| panic!("missing key {key:?}")),
            other => panic!("expected object for key {key:?}, got {other:?}"),
        }
    }
}

fn golden_path(name: &str) -> String {
    format!("{}/tests/golden/{name}", env!("CARGO_MANIFEST_DIR"))
}

fn load_golden(name: &str) -> Json {
    let text = std::fs::read_to_string(golden_path(name))
        .unwrap_or_else(|e| panic!("cannot read golden {name}: {e}"));
    parse_json(&text)
}

// ---------------------------------------------------------------------------
// Physiology replay
// ---------------------------------------------------------------------------

#[test]
fn physiology_replay_matches_python() {
    let tele = load_golden("telemetry.json");
    let physio = load_golden("physio.json");

    let inputs = tele.arr();
    let steps = physio.get("steps").arr();
    let dt = physio.get("dt").num();
    assert_eq!(inputs.len(), steps.len(), "protocol length mismatch");

    let mut model = abyssal::physiology::PhysiologyModel::new();
    let mut max_err = 0.0f64;
    let mut worst = (0usize, 0usize);

    for (i, (in_json, want_json)) in inputs.iter().zip(steps.iter()).enumerate() {
        let temperature = in_json.get("temperature").num();
        let temp_available = in_json.get("temp_available").boolean();
        // See tolerance note: reconstruct temp_c from the normalised value.
        let tel = Telemetry {
            cpu_load: in_json.get("cpu_load").num(),
            memory_pressure: in_json.get("memory_pressure").num(),
            temperature,
            io_rate: in_json.get("io_rate").num(),
            temp_available,
            io_available: in_json.get("io_available").boolean(),
            temp_c: if temp_available {
                Some(30.0 + temperature * 65.0)
            } else {
                None
            },
            ..Telemetry::new()
        };
        let render = (0.5 + 0.5 * ((i as f64) / 91.0).sin()) * 0.4;
        let p = model.update(dt, &tel, render);

        let got = [
            p.agitation, p.pulse, p.density, p.flux, p.surge,
            p.vitality, p.activity, p.stress, p.excite, p.tension,
        ];
        let want = want_json.arr();
        assert_eq!(got.len(), want.len());
        for (j, (g, w)) in got.iter().zip(want.iter()).enumerate() {
            let err = (g - w.num()).abs();
            if err > max_err {
                max_err = err;
                worst = (i, j);
            }
        }
    }

    println!(
        "physiology replay: {} steps, max abs err = {max_err:.3e} at step {} field {}",
        steps.len(),
        worst.0,
        worst.1
    );
    assert!(
        max_err <= 1e-9,
        "physiology replay diverged: max abs err {max_err:.3e} (step {} field {}), tolerance 1e-9",
        worst.0,
        worst.1
    );
}

// ---------------------------------------------------------------------------
// History replay
// ---------------------------------------------------------------------------

#[test]
fn history_replay_matches_python() {
    let golden = load_golden("history.json");

    let mut hist = History::new(5.0);
    // tools/parity_dump.py::dump_history push sequence.
    for i in 0..300i32 {
        let fi = i as f64;
        let cpu = 3.0 + 90.0 * (0.5 + 0.5 * (fi / 31.0).sin());
        let temp = if i % 7 == 0 {
            None
        } else {
            Some(55.0 + 25.0 * (0.5 + 0.5 * (fi / 19.0).cos()))
        };
        let mem = 5.0 + 20.0 * (0.5 + 0.5 * (fi / 13.0).sin());
        let frame = 16.0 + 2.0 * (fi / 5.0).sin();
        hist.push(cpu, temp, mem, frame);
    }

    let mut max_err = 0.0f64;
    let mut max_rel = 0.0f64;
    for ch in Channel::ALL {
        let name = ch.name();
        let want = golden.get(name);
        let ring = hist.ring(ch);

        assert_eq!(ring.len(), want.get("len").num() as usize, "{name}: len");

        let got_last = ring.last();
        let want_last = want.get("last").num();
        if got_last.is_nan() || want_last.is_nan() {
            assert!(
                got_last.is_nan() && want_last.is_nan(),
                "{name}: last NaN mismatch (got {got_last}, want {want_last})"
            );
        } else {
            max_err = max_err.max((got_last - want_last).abs());
        }

        let got_peak = ring.peak();
        let want_peak = want.get("peak").num();
        if want_peak.is_nan() || got_peak.is_nan() {
            assert!(
                want_peak.is_nan() && got_peak.is_nan(),
                "{name}: peak NaN mismatch (got {got_peak}, want {want_peak})"
            );
        } else {
            let e = (got_peak - want_peak).abs();
            max_err = max_err.max(e);
            max_rel = max_rel.max(e / want_peak.abs().max(1e-9));
        }

        let got_win = {
            let r = hist.ring_mut(ch);
            r.window(48)
        };
        let want_win = want.get("window48").arr();
        assert_eq!(got_win.len(), want_win.len(), "{name}: window48 length");
        for (k, (g, w)) in got_win.iter().zip(want_win.iter()).enumerate() {
            let want_v = w.opt_num(); // null -> None -> NaN gap
            match want_v {
                None => assert!(
                    g.is_nan(),
                    "{name}: window[{k}] expected NaN gap, got {g}"
                ),
                Some(wv) => {
                    assert!(
                        !g.is_nan(),
                        "{name}: window[{k}] unexpected NaN, want {wv}"
                    );
                    let e = ((g - wv as f32).abs()) as f64;
                    max_err = max_err.max(e);
                    max_rel = max_rel.max(e / wv.abs().max(1e-9));
                }
            }
        }
    }

    println!("history replay: max abs err = {max_err:.3e}, max rel err = {max_rel:.3e}");
    assert!(
        max_err <= 1e-4,
        "history replay diverged: max abs err {max_err:.3e}, tolerance 1e-4"
    );
}

// ---------------------------------------------------------------------------
// Live smoke test for TelemetrySource
// ---------------------------------------------------------------------------

#[test]
fn telemetry_source_live_smoke() {
    let mut src = abyssal::telemetry::source::TelemetrySource::new();
    for i in 0..3 {
        let t = src.sample();
        assert!(
            (0.0..=1.0).contains(&t.cpu_load),
            "sample {i}: cpu_load {} out of range",
            t.cpu_load
        );
        assert!(
            (0.0..=1.0).contains(&t.memory_pressure),
            "sample {i}: memory_pressure {} out of range",
            t.memory_pressure
        );
        assert!(
            (0.0..=1.0).contains(&t.temperature),
            "sample {i}: temperature {} out of range",
            t.temperature
        );
        assert!(
            t.mem_total_gb > 0.0,
            "sample {i}: mem_total_gb {} not positive",
            t.mem_total_gb
        );
        assert_eq!(
            t.temp_available,
            t.temp_c.is_some(),
            "sample {i}: temp_available/temp_c inconsistent"
        );
        assert!(!t.notes.is_empty(), "sample {i}: notes empty");
        println!(
            "sample {i}: cpu={:.3} mem={:.2}/{:.2}GB temp={:?} io={:.3} net={:.3} notes={}",
            t.cpu_load, t.mem_used_gb, t.mem_total_gb, t.temp_c, t.io_mb_s, t.net_mb_s, t.notes
        );
        if i < 2 {
            std::thread::sleep(std::time::Duration::from_millis(120));
        }
    }
}
