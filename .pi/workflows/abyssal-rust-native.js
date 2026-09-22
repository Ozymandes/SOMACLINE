// =============================================================================
// abyssal-rust-native.js
// Reusable native-translation + optimization workflow for the Abyssal Organism
// Monitor (Python/GTK4 -> Rust/gtk4-rs), built for the pi-subagents
// workflowScript sandbox (tintinweb pi-subagents).
//
// USAGE (from the main pi session, which is the INTEGRATOR / ARCHITECT):
//
//   subagent({
//     workflowScriptPath: ".pi/workflows/abyssal-rust-native.js",
//     cwd: "/home/seeno/abyssal-organism-monitor",
//     args: { phase: "A" }
//   })
//
// One invocation runs ONE phase. The integrator reads the returned dossier,
// merges accepted work, commits, and only then launches the next phase.
//
//   args: {
//     phase:      "A" | "B" | "C" | "D" | "E" | "F"   (default "A")
//     root:       project root     (default /home/seeno/abyssal-organism-monitor)
//     baseline:   parity spec path (default docs/rust-migration/phase-a/parity-spec.md)
//     crate:      parity-host crate path for C..F (default rust/console)
//     canIsolate: true when the git tree is clean, enabling worktree lanes (E)
//   }
//
// PHASES
// ------
//  A  Discovery/Baseline : 5 parallel audits (architecture, memory, CPU,
//                          Rust/GTK framework floor, parity spec) then an
//                          adversarial coherence verifier. GATE: verdict
//                          coherent==true before Phase B may launch.
//  B  Core Translation   : 4 independent lanes (telemetry, physiology,
//                          creature, raster), each: implement standalone crate
//                          (cargo-test host gate) -> fresh parity reviewer that
//                          is instructed to REFUTE parity -> one revision round
//                          (resume) -> re-review. Accepted crates are merged by
//                          the integrator only.
//  C  GTK Parity Host    : one single-writer builder for the gtk4-rs host
//                          (chrome/typography/graphs/input), then 3 parallel
//                          reviews (visual vs golden, performance, parity-spec
//                          gates), one revision round, visual re-check.
//  D  Extreme Opt        : 7 independent measured investigations (buffers,
//                          surfaces, font floor, cadence, allocations, SIMD,
//                          allocators) then an evidence verifier that rejects
//                          noise-level gains. Recommendations only; integrator
//                          applies.
//  E  Memory Research    : EXPERIMENTAL alternate backends (wayland-host,
//                          cpu-present, gpu-present, text-light) in managed
//                          worktrees + a decision-matrix verifier against the
//                          six replacement criteria. Never auto-lands.
//  F  Final Verification : 5 independent final verifiers (visual, math, perf,
//                          memory/smaps accounting, QA+Hyprland torture) then a
//                          synthesis draft separating FACTS / MEASUREMENTS /
//                          INFERENCES / ASPIRATIONAL TARGETS.
//
// AGENT ROUTING (model strategy: GLM-5.3 Flash broadly, raised effort where
// architecture / mathematical parity / renderer design / final verification
// demand it; "zai/glm-5.3:high" and ":xhigh" suffixes raise thinking):
//   researcher        - audits, profiling, research          (flash, medium)
//   coder             - crates, host, floor builds, benches  (flash, high)
//   reviewer          - parity refutation, spec checks       (xhigh/high)
//   evidence-auditor  - baseline coherence, optimization evidence,
//                       smaps accounting                      (xhigh/high)
//   design-critic     - screenshot drift vs golden reference (flash, high)
//   interaction-qa    - live gates + Hyprland torture        (flash, medium)
//
// DEPENDENCIES
//   B depends on A (parity spec + architecture). C depends on B merged by the
//   integrator. D depends on C merged. E depends on A floor evidence showing
//   GTK/Pango/Cairo dominate PSS, and a clean tree (canIsolate). F depends on
//   the integrated final tree.
//
// VERIFICATION GATES
//   - cargo test host gates on every translated crate (B) and the host build
//     (C): a non-zero exit fails the child run explicitly.
//   - structured verdicts (outputSchema) from every reviewer/verifier; reviewer
//     prose is never parsed by the script.
//   - fresh-context reviewers everywhere; reviewers are told to refute.
//   - optimization adoption requires measured baseline + measured result;
//     the D verifier rejects gains within run-to-run noise.
//
// RETRY RULES
//   - Every launch goes through launch(): one automatic fresh retry if the
//     child run fails (ok != true). Keys get a "-retry" suffix.
//   - Review loops are capped at ONE revision round + re-review per lane;
//     still-rejected lanes are returned as state "rejected-after-revision"
//     for the integrator instead of looping forever.
//   - Infrastructure failures abort the workflow; the integrator inspects
//     subagent status and retries the phase.
// =============================================================================

const ROOT = (args && args.root) || "/home/seeno/abyssal-organism-monitor";
const PH = String((args && args.phase) || "A").toUpperCase();
const BASELINE = (args && args.baseline) || "docs/rust-migration/phase-a/parity-spec.md";
const HOSTCRATE = (args && args.crate) || "rust/console";

const FLASH = "zai/glm-5.3-flash";
const HIGH = "zai/glm-5.3:high";
const XHIGH = "zai/glm-5.3:xhigh";

// ---------------------------------------------------------------------------
// Shared context block appended to every child task.
// ---------------------------------------------------------------------------
function preamble() {
  return [
    "PROJECT: Abyssal Organism Monitor at " + ROOT + " - a Python 3 + GTK4 + Cairo + NumPy Linux desktop app. Launch: ./run.sh (python3 -m abyssal.app). Five procedural point-field organisms (species s01-s05) driven by real /proc and /sys telemetry at ~5 Hz, rendered inside six generated hardware-module skins; fonts assets/fonts/astro.ttf and microgrammanormal.ttf.",
    "CODE: abyssal/core/{world,viewport,theme,signals,lighting,layout,physiology}.py | abyssal/organism/{species,mathforms,sources,pointfield,render}.py | abyssal/telemetry/{source,history}.py | abyssal/ui/{console,chrome,segment,selector,fonts,debug}.py | abyssal/skin/{surface,catalog,fascia,modules,hidpi}.py | abyssal/app.py. Largest files: ui/console.py (1932 lines), organism/mathforms.py (747), ui/chrome.py (729).",
    "QA HARNESS: qa/gates.py (GATE 1 geometry + GATE 3 performance), qa/console_gates.py (GATES 4-8), qa/production_gates.py (P1-P12), qa/physiology_gates.py (PH1-PH7), qa/creature_validate.py, qa/perf_live.py (CPU/RSS/FPS focused/unfocused/hidden), qa/shots.py (real-window screenshots to docs/shots), qa/torture.py --shots (GATE 0 Hyprland resize torture), qa/offscreen.py, qa/compare.py, qa/specimen_sheet.py.",
    "DOCS: README.md, docs/ARCHITECTURE.md, docs/CREATURE_EQUATIONS.md (porting rules + s01-s05), docs/VALIDATION.md.",
    "MISSION: translate this app to native Rust (gtk4-rs) with strict behavioral + visual parity, then optimize from measured evidence. The main pi session is the INTEGRATOR/ARCHITECT: it owns architectural decisions, merges, shared-file edits, parity acceptance, benchmark acceptance and commits. You are one specialist lane: do NOT git commit, do NOT spawn subagents, do NOT edit outside your assigned paths.",
    "HONESTY: separate FACTS (cite file:line or exact command) from MEASUREMENTS (numbers + method + sample count) from INFERENCES (label them). If you launch the app or any process to measure, terminate it before finishing. Never present a command you did not run as evidence.",
    ""
  ].join("\n");
}

// ---------------------------------------------------------------------------
// Launch with one automatic fresh retry. Plain promise chain (no async helpers).
// Strips undefined/null params: the sandbox requires JSON values for every
// runs.run param (e.g. model/worktree omitted per lane).
// ---------------------------------------------------------------------------
function launch(key, item) {
  const clean = {};
  Object.keys(item).forEach(function (k) {
    if (item[k] !== undefined && item[k] !== null) clean[k] = item[k];
  });
  function attempt(k, it) {
    return runs.run(k, it).catch(function (err) {
      const why = err && err.message ? err.message : String(err);
      emit("lane " + k + " rejected: " + why);
      return { ok: false, rejected: true, error: why };
    });
  }
  return attempt(key, clean).then(function (first) {
    if (first && first.ok) return first;
    const why = first && first.error ? first.error : "run failed";
    emit("lane " + key + " failed (" + why + "); one fresh retry");
    const retryItem = Object.assign({}, clean, {
      task: String(clean.task) + "\n\nRETRY NOTE: your previous attempt for this exact role failed to produce a valid result (" + why + "). If this was infrastructure (rate limit, transport), wait briefly before heavy work; do not tight-loop. Start over; keep the deliverable complete but concise; finish with the required structured output."
    });
    return attempt(key + "-retry", retryItem);
  });
}

function slim(r) {
  if (!r) return null;
  return {
    key: r.key || null,
    runId: r.runId || null,
    ok: typeof r.ok === "boolean" ? r.ok : null,
    structuredOutput: r.structuredOutput || null,
    outputReference: r.outputReference || null,
    artifactPaths: Array.isArray(r.artifactPaths) ? r.artifactPaths.slice(0, 8) : null,
    output_excerpt: typeof r.output === "string" ? r.output.slice(0, 1200) : null
  };
}

// ---------------------------------------------------------------------------
// Structured-output schemas.
// ---------------------------------------------------------------------------
const AUDIT = {
  type: "object", additionalProperties: true,
  required: ["report_path", "key_findings", "risks"],
  properties: {
    report_path: { type: "string" },
    key_findings: { type: "array", items: { type: "string" }, maxItems: 10 },
    measurements: { type: "string" },
    risks: { type: "array", items: { type: "string" }, maxItems: 8 }
  }
};
const VERIFY = {
  type: "object", additionalProperties: true,
  required: ["coherent", "report_path"],
  properties: {
    coherent: { type: "boolean" },
    blocking_gaps: { type: "array", items: { type: "string" }, maxItems: 10 },
    corrections: { type: "array", items: { type: "string" }, maxItems: 12 },
    report_path: { type: "string" }
  }
};
const IMPL = {
  type: "object", additionalProperties: true,
  required: ["summary", "crate_path", "tests_pass"],
  properties: {
    summary: { type: "string" },
    crate_path: { type: "string" },
    fixtures: { type: "integer" },
    deviations: { type: "array", items: { type: "string" }, maxItems: 8 },
    tests_pass: { type: "boolean" }
  }
};
const REVIEW = {
  type: "object", additionalProperties: true,
  required: ["verdict", "report_path"],
  properties: {
    verdict: { type: "string", enum: ["accepted", "rejected"] },
    drift: { type: "array", items: { type: "string" }, maxItems: 12 },
    required_changes: { type: "array", items: { type: "string" }, maxItems: 12 },
    report_path: { type: "string" }
  }
};
const INVEST = {
  type: "object", additionalProperties: true,
  required: ["area", "recommendation"],
  properties: {
    area: { type: "string" },
    baseline: { type: "string" },
    proposed_change: { type: "string" },
    measured_result: { type: "string" },
    complexity: { type: "string" },
    risk: { type: "string" },
    recommendation: { type: "string", enum: ["adopt", "reject", "needs-more-evidence"] },
    evidence_path: { type: "string" }
  }
};
const FINAL = {
  type: "object", additionalProperties: true,
  required: ["verdict", "report_path"],
  properties: {
    verdict: { type: "string", enum: ["pass", "fail", "conditional"] },
    findings: { type: "array", items: { type: "string" }, maxItems: 12 },
    report_path: { type: "string" }
  }
};

// ===========================================================================
// PHASE A - DISCOVERY / BASELINE
// ===========================================================================
if (PH === "A") {
  emit("Phase A: five parallel audits, then adversarial coherence verification");
  const dir = "docs/rust-migration/phase-a";

  const audits = [
    {
      key: "a-arch", agent: "researcher", model: HIGH, timeoutMs: 2400000,
      output: dir + "/architecture.md", outputSchema: AUDIT,
      label: "Audit Python architecture + translation seams",
      task: preamble() + [
        "ROLE: architecture-audit (Phase A of the Python->Rust migration).",
        "TASK: Map the Python architecture for translation:",
        "1. Module inventory with responsibilities and dependency directions; where data flows: telemetry -> physiology -> organism -> render -> ui.",
        "2. The frame loop: what runs per frame and at what cadence (GLib timeouts in abyssal/app.py, the draw cycle, TELEMETRY_INTERVAL_MS), what is event-driven vs continuous, and what happens when unfocused/hidden.",
        "3. Hot paths for CPU: per-frame math in organism/pointfield.py + organism/mathforms.py + organism/render.py, numpy call sites, per-frame allocations. Rank with evidence (line ranges, call sites).",
        "4. The static/dynamic split: static module-skin chrome vs live-drawn content (docs/ARCHITECTURE.md 'static hardware layer' + skin cache in abyssal/skin/surface.py); where cache boundaries are and when they invalidate.",
        "5. Rust translation boundaries: propose crate seams (telemetry / physiology / creature / raster / host) with the exact Python entry points each replaces; flag any seam that would force shared-file edits (integration risk).",
        "6. GTK behaviors the host must reproduce: resize contract, responsive states, fullscreen, selector input, hidpi (skin/hidpi.py), screenshot/offscreen paths.",
        "WRITE your full report to " + dir + "/architecture.md (create the directory). RETURN the structured output only."
      ].join("\n")
    },
    {
      key: "a-mem", agent: "researcher", timeoutMs: 2400000,
      output: dir + "/memory.md", outputSchema: AUDIT,
      label: "Baseline Python RSS/PSS composition",
      task: preamble() + [
        "ROLE: memory-audit.",
        "TASK: Establish the Python app memory baseline:",
        "1. Launch the app (./run.sh --help first; use --quit-after or run + kill; on the live desktop) OR qa/offscreen.py if a windowed run is impractical. Reach steady state (~60 s), then record RSS from /proc/<pid>/status and PSS from /proc/<pid>/smaps_rollup. Sample 3 times, report all.",
        "2. Enumerate large buffers/surfaces with byte attribution from the CODE (shapes x dtype x counts): decoded module PNGs (assets/modules), cached cairo image surfaces (skin/surface.py), numpy point-field arrays (organism/pointfield.py), telemetry history rings (telemetry/history.py), font/text machinery.",
        "3. Identify duplication: same PNG decoded/scaled more than once, surfaces cached at stale sizes, per-frame numpy temporaries.",
        "4. Quantify the plausible Rust floor for the DATA MODEL alone (excluding GTK/Pango/Cairo): the telemetry + physiology + creature + raster state in bytes.",
        "WRITE to " + dir + "/memory.md. RETURN structured output."
      ].join("\n")
    },
    {
      key: "a-cpu", agent: "researcher", timeoutMs: 2400000,
      output: dir + "/cpu-profile.md", outputSchema: AUDIT,
      label: "Profile simulation/render/GTK/telemetry/font costs",
      task: preamble() + [
        "ROLE: cpu-profile.",
        "TASK: Attribute CPU cost of the running Python app:",
        "1. Start from qa/perf_live.py (focused/unfocused/hidden). Supplement with perf stat / perf record -g or py-spy if already available (a user-local py-spy via pipx/uv is acceptable; do NOT install system packages - if unavailable, say so and fall back to timing instrumentation you write under /tmp).",
        "2. Measure: FPS, frame-time p50/p95, CPU% focused vs unfocused vs hidden, and a per-stage attribution table: organism math (point-field equations), raster/accumulate, cairo draw/paint, GTK bookkeeping, telemetry reads (5 Hz), font/text (Pango layout per frame vs cached).",
        "3. For each stage classify: Python-interpreter overhead vs intrinsic (numpy/Cairo/C) - this determines realistic Rust upside per stage.",
        "WRITE to " + dir + "/cpu-profile.md. RETURN structured output."
      ].join("\n")
    },
    {
      key: "a-floor", agent: "coder", timeoutMs: 3000000,
      output: dir + "/framework-floor.md", outputSchema: AUDIT,
      label: "Build + measure tiny Rust/GTK PSS-CPU floors",
      task: preamble() + [
        "ROLE: framework-floor. Build and measure tiny Rust baselines under /tmp/abyssal-floor/ (NOT inside the project tree):",
        "1. empty binary (init + sleep 30 s): PSS after 10 s.",
        "2. gtk4-rs window, blank content, shown: PSS + idle CPU.",
        "3. gtk4 + cairo drawing a full-window gradient each frame at ~60 fps: PSS + CPU + frame time.",
        "4. gtk4 + cairo + pango loading assets/fonts/astro.ttf (fontconfig user dir or pango fontmap) rendering ~40 static labels per frame: PSS + CPU + frame time.",
        "5. same as 4 but with text layout cached (layouts built once): PSS + CPU + frame time - the typographic floor.",
        "METHOD: one cargo bin per stage, build --release, run 30 s, sample /proc/<pid>/smaps_rollup (Pss) + CPU from /proc/<pid>/stat deltas; 3 runs each, report median + min + max. If gtk4 dev libraries are missing and linking fails, record the exact error and stop that stage (do NOT install system packages).",
        "DELIVERABLE: the floor table = what the Rust+GTK+Cairo+Pango stack itself costs on this machine, per stage. This decides whether Phase E (alternate backends) is needed.",
        "WRITE to " + dir + "/framework-floor.md. Keep all build dirs under /tmp. RETURN structured output."
      ].join("\n")
    },
    {
      key: "a-parity", agent: "researcher", model: HIGH, timeoutMs: 2400000,
      output: dir + "/parity-spec.md", outputSchema: AUDIT,
      label: "Extract exact behavioral/visual invariants + gates",
      task: preamble() + [
        "ROLE: parity-spec. Extract the exact behavioral/visual invariants the Rust port must reproduce - the acceptance contract:",
        "1. Equation invariants: docs/CREATURE_EQUATIONS.md porting rules + the five species s01-s05 (exact forms, every constant, dt / source-time semantics, seating transforms, anatomy metadata). State Python float (f64) semantics and any numpy dtype whose width would change accumulation results.",
        "2. Physiology: core/physiology.py smoothing + activity/stress/excite/tension responses + telemetry pulse propagation; cadence.",
        "3. Layout/visual: resize contract, responsive states, six-module composition, observation-chamber recess geometry, 9-slice rules (and what is NOT 9-sliced), lighting, seven-segment rendering, selector behavior, graduated states.",
        "4. Typography: which faces/sizes/weights appear where (ui/fonts.py, ui/chrome.py, ui/segment.py) + static text caching rules.",
        "5. Telemetry semantics: 60 s bounded history, 5 Hz, exact /proc and /sys fields read (telemetry/source.py), unfocused/hidden behavior.",
        "6. Enumerate EXECUTABLE gates Rust must pass: map every existing qa gate to its Rust equivalent (to be built), plus new parity gates: equation vector fixtures, screenshot delta thresholds, smaps accounting checks. Number them G-R1, G-R2, ...",
        "WRITE to " + dir + "/parity-spec.md as a numbered checkable gate list. RETURN structured output."
      ].join("\n")
    }
  ];

  const only = (args && Array.isArray(args.only)) ? args.only : null;
  const priorDigest = (args && args.digest) || null;
  const laneNote = (args && args.laneNote) || null;
  const selected = only ? audits.filter(function (d) { return only.indexOf(d.key) !== -1; }) : audits;

  const auditResults = await Promise.all(selected.map(function (d) {
    return launch(d.key, {
      agent: d.agent, label: d.label, model: d.model,
      task: d.task + (laneNote ? "\n\nRESUME NOTE (from the integrator): " + laneNote : ""),
      timeoutMs: d.timeoutMs, output: d.output, outputSchema: d.outputSchema,
      context: "fresh"
    });
  }));

  const badLanes = [];
  selected.forEach(function (d, i) {
    const r = auditResults[i];
    if (!r || r.ok !== true) badLanes.push({ lane: d.key, error: (r && r.error) || "no structured result" });
  });
  if (badLanes.length > 0) {
    emit("Phase A stopping cleanly without verification: " + badLanes.length + " lane(s) failed after retry");
    return {
      phase: "A", state: "blocked-lane-failure",
      failed_lanes: badLanes,
      already_complete: (only || []).filter(function (k) {
        return badLanes.every(function (b) { return b.lane !== k; });
      }).concat(audits.filter(function (d) { return !only || only.indexOf(d.key) === -1; }).map(function (d) { return d.key; })),
      preserve: "Reports under docs/rust-migration/phase-a/ are kept. Resume with args {phase:'A', only:[failed lane keys], digest:<same digest>} once infrastructure recovers. Do NOT rerun completed lanes."
    };
  }

  const digestEntries = audits.map(function (d) {
    const idx = selected.indexOf(d);
    if (idx !== -1) {
      const r = auditResults[idx];
      const so = r && r.structuredOutput ? r.structuredOutput : null;
      return { lane: d.key, report: (so && so.report_path) || d.output, key_findings: (so && so.key_findings) || [], risks: (so && so.risks) || [] };
    }
    const prior = priorDigest ? priorDigest[d.key] : null;
    if (prior) {
      return { lane: d.key, report: prior.report || prior.report_path || d.output, key_findings: prior.key_findings || [], risks: prior.risks || [] };
    }
    return { lane: d.key, report: d.output, key_findings: [], risks: [], missing: true };
  });
  const digest = JSON.stringify(digestEntries, null, 1);

  emit("Phase A audits done; adversarial verification of the dossier");
  const verify = await launch("a-verify", {
    agent: "evidence-auditor", model: XHIGH, context: "fresh", timeoutMs: 2400000,
    output: dir + "/BASELINE-VERIFY.md", outputSchema: VERIFY,
    label: "Refute the Phase A baseline dossier",
    task: preamble() + [
      "ROLE: baseline-verify - adversarial verifier for Phase A. Your job is to REFUTE the dossier, not to approve it.",
      "AUDIT DIGEST:",
      digest,
      "TASK: Read the five full reports under " + dir + "/ (architecture.md, memory.md, cpu-profile.md, framework-floor.md, parity-spec.md), then spot-check claims against the code (file:line) and challenge:",
      "- inconsistencies BETWEEN audits (e.g. hot paths that disagree with the CPU profile, memory attribution that disagrees with the code)",
      "- numbers without methods or sample counts; floor measurements with cold-cache or single-run bias",
      "- missing invariants in the parity spec (anything a Rust port could silently get wrong: dt semantics, accumulation order, resize contract, hidden-state behavior, font fallback)",
      "- translation seams that force concurrent edits to shared files (integration risk for Phase B lanes)",
      "Verdict coherent=true ONLY if the integrator can safely plan Phase B and C from this dossier as-is. List blocking_gaps (must fix before Phase B) separately from corrections (non-blocking fixes).",
      "WRITE to " + dir + "/BASELINE-VERIFY.md. RETURN structured output."
    ].join("\n")
  });

  const vso = verify && verify.structuredOutput ? verify.structuredOutput : null;
  return {
    phase: "A",
    state: vso ? (vso.coherent ? "verified-coherent" : "verified-incoherent") : "blocked-verify",
    reports_dir: ROOT + "/" + dir,
    audits: auditResults.map(slim),
    verify: slim(verify),
    next_gate: vso && vso.coherent ? "Integrator may launch Phase B with args {phase:'B'}." : "Integrator must resolve blocking_gaps or infrastructure and re-verify before Phase B."
  };
}

// ===========================================================================
// PHASE B - CORE TRANSLATION (4 independent lanes, refute-based review)
// ===========================================================================
if (PH === "B") {
  emit("Phase B: four translation lanes with cargo-test gates and refuting parity reviews");
  const dir = "docs/rust-migration/phase-b";

  const MODULES = [
    {
      key: "telemetry", crate: "rust/telemetry",
      scope: "abyssal/telemetry/source.py + abyssal/telemetry/history.py: the 5 Hz acquisition loop reading /proc and /sys (exact fields, parse rules, fallbacks), bounded 60-second histories (ring semantics, downsampling if any), and the public model API the physiology crate consumes.",
      fixtures: "Capture telemetry fixture vectors by running the Python source reader on this machine and freezing the parsed values (plus a synthetic replay fixture); test ring bounds and 5 Hz cadence math deterministically."
    },
    {
      key: "physiology", crate: "rust/physiology",
      scope: "abyssal/core/physiology.py: the PhysiologyModel - smoothing, activity/stress/excite/tension responses to telemetry, and the propagating pulse. Operation order and dt semantics must match Python exactly (f64).",
      fixtures: "Generate golden vectors by executing the Python PhysiologyModel across a scripted telemetry timeline (include the qa/physiology_gates.py PH1-PH7 scenarios); freeze inputs+outputs as JSON fixtures."
    },
    {
      key: "creature", crate: "rust/creature",
      scope: "abyssal/organism/species.py + mathforms.py + sources.py: the canonical five equations (s01-s05) with source-time parity, seating transforms, and anatomy metadata. Follow docs/CREATURE_EQUATIONS.md porting rules to the letter; Python float = f64; keep operation ORDER identical.",
      fixtures: "Generate per-species point vectors (multiple timestamps, multiple dt values, edge clamps) by executing the Python organism modules; also port qa/creature_validate.py expectations as tests."
    },
    {
      key: "raster", crate: "rust/raster",
      scope: "abyssal/organism/pointfield.py + abyssal/organism/render.py (the numeric core): point-field buffers, density/excitation/hue accumulation, lighting application - with a zero-allocation hot path (preallocated buffers, no per-point heap traffic) and accumulation ORDER matching numpy exactly (float addition is not associative; match loop order and dtype promotion).",
      fixtures: "Generate accumulate/step vectors from Python for representative point sets; test bit-level or tolerance parity per the baseline parity spec; include a benchmark test proving no allocation in the hot loop."
    }
  ];

  function implTask(m) {
    return preamble() + [
      "ROLE: " + m.key + " implementer (Phase B lane).",
      "FIRST read docs/rust-migration/phase-a/parity-spec.md and docs/rust-migration/phase-a/architecture.md.",
      "BUILD a standalone crate at " + m.crate + "/ : own Cargo.toml, edition 2021, NO cargo workspace file, zero runtime dependencies unless the parity spec demands one (dev-dependencies: none preferred).",
      "SCOPE: " + m.scope,
      "PARITY RULES: strict numeric parity with Python (f64); identical operation order; identical clamps/boundary conditions; document every unavoidable deviation in " + m.crate + "/PARITY.md with the reason and measured magnitude.",
      "FIXTURES: " + m.fixtures,
      "TESTS: cargo tests load fixtures from " + m.crate + "/tests/fixtures/ and assert the tolerances the parity spec defines (default: exact equality where the computation is deterministic, else the spec tolerance).",
      "BOUNDARIES: create/modify files ONLY under " + m.crate + "/ and /tmp scratch. Do NOT create rust/Cargo.toml or touch sibling crates or Python code. Do NOT git commit.",
      "FINISH: cargo test --manifest-path " + m.crate + "/Cargo.toml green. The host re-runs this command as a verification gate after you finish - a failing gate fails your run.",
      "REPORT (structured): summary, crate path, fixture count, deviations, tests_pass."
    ].join("\n");
  }

  function reviewTask(m, impl) {
    return preamble() + [
      "ROLE: parity-review for lane " + m.key + " - your job is to REFUTE parity, not to approve it.",
      "The implementer claims: " + JSON.stringify(impl && impl.structuredOutput ? impl.structuredOutput : {}),
      "The crate is " + m.crate + "/.",
      "ATTACK: (1) Re-derive the math from docs/CREATURE_EQUATIONS.md + the Python source (" + m.scope.split(":")[0] + ") and diff against the Rust line by line - constants, operation order, dt/source-time handling, clamps, seating transforms. (2) Check fixture honesty: were tolerances widened to hide drift? Independently regenerate 2-3 fixture vectors yourself by running the Python code, and compare. (3) Run cargo test --manifest-path " + m.crate + "/Cargo.toml yourself and inspect what the tests actually assert. (4) For the raster lane, verify accumulation order vs numpy and the no-allocation claim. (5) Check the parity spec gate list for invariants the crate does not cover yet.",
      "Verdict 'rejected' if ANY unexplained numeric drift, missing invariant, or dishonest fixture. required_changes must be concrete: file, symbol, what to change.",
      "WRITE the full review to " + dir + "/" + m.key + "-review.md. RETURN structured verdict."
    ].join("\n");
  }

  function reReviewTask(m, rev) {
    return preamble() + [
      "ROLE: parity-review (round 2) for lane " + m.key + " after revision. The crate is " + m.crate + "/.",
      "The first review REJECTED with: " + JSON.stringify(rev && rev.structuredOutput ? rev.structuredOutput : {}),
      "Verify each required_change was actually applied (not cosmetic), re-run cargo test --manifest-path " + m.crate + "/Cargo.toml, and re-check the original attack list (constants, order, dt/source-time, fixtures honesty). Verdict 'accepted' only with zero outstanding drift.",
      "UPDATE " + dir + "/" + m.key + "-review.md with a round-2 section. RETURN structured verdict."
    ].join("\n");
  }

  function moduleLane(m) {
    return launch("b-" + m.key + "-impl", {
      agent: "coder", task: implTask(m), context: "fresh", timeoutMs: 3000000,
      gate: "cargo test --manifest-path " + m.crate + "/Cargo.toml",
      output: dir + "/" + m.key + "-impl-report.md", outputSchema: IMPL,
      label: "Implement " + m.key + " crate"
    }).then(function (impl) {
      if (!impl || !impl.ok || !impl.runId) {
        return { module: m.key, state: "blocked-implementation", impl: slim(impl) };
      }
      return launch("b-" + m.key + "-review", {
        agent: "reviewer", model: XHIGH, context: "fresh", timeoutMs: 2400000,
        output: dir + "/" + m.key + "-review.md", outputSchema: REVIEW,
        label: "Refute " + m.key + " parity",
        task: reviewTask(m, impl)
      }).then(function (rev) {
        const verdict = rev && rev.structuredOutput ? rev.structuredOutput.verdict : null;
        if (verdict !== "rejected") {
          return { module: m.key, state: verdict === "accepted" ? "accepted" : "unreviewed", impl: slim(impl), review: slim(rev) };
        }
        emit(m.key + " rejected; running one revision round");
        return runs.run("b-" + m.key + "-impl-rev1", {
          resume: impl.runId, timeoutMs: 2400000,
          task: [
            "Parity review REJECTED your crate with these required changes:",
            JSON.stringify(rev.structuredOutput || {}, null, 1),
            "Full review: " + dir + "/" + m.key + "-review.md",
            "Apply exactly these changes inside " + m.crate + "/ - do not widen scope, do not weaken tests or tolerances. Re-run cargo test --manifest-path " + m.crate + "/Cargo.toml until green. Finish with the same structured report."
          ].join("\n")
        }).then(function (impl2) {
          if (!impl2 || !impl2.ok) {
            return { module: m.key, state: "blocked-revision", impl: slim(impl), review: slim(rev) };
          }
          return launch("b-" + m.key + "-review2", {
            agent: "reviewer", model: XHIGH, context: "fresh", timeoutMs: 2400000,
            output: dir + "/" + m.key + "-review-round2.md", outputSchema: REVIEW,
            label: "Re-refute " + m.key + " after revision",
            task: reReviewTask(m, rev)
          }).then(function (rev2) {
            const v2 = rev2 && rev2.structuredOutput ? rev2.structuredOutput.verdict : "unknown";
            return {
              module: m.key,
              state: v2 === "accepted" ? "accepted-after-revision" : "rejected-after-revision",
              impl: slim(impl2), review: slim(rev2), first_review: slim(rev)
            };
          });
        });
      });
    });
  }

  const lanes = await Promise.all(MODULES.map(moduleLane));

  return {
    phase: "B",
    reports_dir: ROOT + "/" + dir,
    lanes: lanes,
    next_gate: "Integrator merges only lanes in an accepted state, commits, then launches Phase C with args {phase:'C'}."
  };
}

// ===========================================================================
// PHASE C - GTK PARITY HOST
// ===========================================================================
if (PH === "C") {
  emit("Phase C: single-writer gtk4-rs parity host, then three parallel reviews");
  const dir = "docs/rust-migration/phase-c";

  const host = await launch("c-host", {
    agent: "coder", model: HIGH, context: "fresh", timeoutMs: 3600000,
    gate: "cargo build --release --manifest-path " + HOSTCRATE + "/Cargo.toml",
    output: dir + "/host-notes.md", outputSchema: IMPL,
    label: "Build the gtk4-rs parity host",
    task: preamble() + [
      "ROLE: gtk-host builder (Phase C). The core crates (telemetry, physiology, creature, raster) are integrated under rust/ - inventory them first (ls rust/, read their public APIs and PARITY.md files).",
      "BUILD the parity host crate " + HOSTCRATE + "/ (own Cargo.toml, no workspace file):",
      "1. gtk4-rs application mirroring abyssal/app.py: window, the resize contract + responsive states (docs/ARCHITECTURE.md), fullscreen, selector + keyboard input, hidpi.",
      "2. Static chrome: compose the six module PNGs (assets/modules/) as cached surfaces exactly per abyssal/skin/{surface,modules,fascia}.py cache rules (active-layout-only, stale-size eviction).",
      "3. Typography: register assets/fonts/astro.ttf + microgrammanormal.ttf (fontconfig user dir or Pango fontmap), render all static text cached; seven-segment per ui/segment.py.",
      "4. Live content: observation chamber drawn through the creature + raster crates; telemetry rack graphs: bounded 60-second history rendered programmatically (ui/console.py); physiology wiring; 5 Hz telemetry via the telemetry crate.",
      "5. Frame loop: match the Python cadence policy (continuous draw; time-advancement semantics for unfocused/hidden per app.py GLib sources).",
      "6. CLI parity: support the flags abyssal/app.py supports (run python3 -m abyssal.app --help; at minimum --quit-after) plus add --screenshot PATH [--width W] [--height H] for deterministic capture.",
      "7. Visual gate: produce Rust screenshots and compare against the Python golden images (run qa/shots.py for fresh goldens or reuse docs/shots): GATE 1 geometry equivalents must hold.",
      "BOUNDARIES: edit ONLY inside " + HOSTCRATE + "/ (plus your /tmp scratch). If an integrated core crate needs a change, do NOT edit it - record the needed change in your report. Do NOT git commit.",
      "FINISH: cargo build --release --manifest-path " + HOSTCRATE + "/Cargo.toml green (host gate re-runs it) + at least one screenshot emitted under " + dir + "/shots/.",
      "WRITE build notes to " + dir + "/host-notes.md. RETURN structured output."
    ].join("\n")
  });

  if (!host || !host.ok || !host.runId) {
    return { phase: "C", state: "blocked-host-build", host: slim(host) };
  }

  emit("Phase C host built; parallel visual / performance / spec reviews");
  const reviews = await Promise.all([
    launch("c-review-visual", {
      agent: "design-critic", context: "fresh", timeoutMs: 2400000,
      output: dir + "/review-visual.md", outputSchema: REVIEW,
      label: "Screenshot drift vs Python golden",
      task: preamble() + [
        "ROLE: visual-review. The Rust parity host (" + HOSTCRATE + ") was just built; its screenshots are under " + dir + "/shots/ (build your own if missing: run the host with --screenshot).",
        "TASK: Compare Rust screenshots against the Python golden reference (fresh goldens via qa/shots.py, plus docs/shots and references/Abbysal_Final.png for the module map). Report CONCRETE DRIFT ONLY, each with pixel-region evidence: geometry offsets (px), color deltas (hex/RGB + where), typography mismatches (face/size/weight/position), missing or extra elements, alpha/compositing errors, 9-slice artifacts.",
        "Run qa/compare.py against the Rust screenshot if the tooling permits; otherwise do a side-by-side numeric comparison yourself (Python + PIL is acceptable for measurement).",
        "verdict 'rejected' if any drift exceeds what the baseline parity spec allows; drift list = required_changes (concrete, actionable).",
        "WRITE to " + dir + "/review-visual.md. RETURN structured verdict."
      ].join("\n")
    }),
    launch("c-review-perf", {
      agent: "researcher", context: "fresh", timeoutMs: 2400000,
      output: dir + "/review-perf.md", outputSchema: REVIEW,
      label: "Profile Rust host CPU/PSS/allocations",
      task: preamble() + [
        "ROLE: performance-review for the new Rust host (" + HOSTCRATE + ").",
        "TASK: Measure focused/unfocused/hidden CPU%, PSS from /proc/<pid>/smaps_rollup, frame-time p50/p95, and allocation behavior (heap sampling or a global-allocator counter build under /tmp). Compare against the Python numbers in docs/rust-migration/phase-a/memory.md and cpu-profile.md - same method, same durations, 3 samples each.",
        "This is a review: flag regressions or missed parity (e.g. wrong cadence when hidden) as required_changes; verdict 'rejected' only for real regressions or parity violations, not for 'could be faster'.",
        "WRITE to " + dir + "/review-perf.md. RETURN structured verdict."
      ].join("\n")
    }),
    launch("c-review-spec", {
      agent: "reviewer", model: HIGH, context: "fresh", timeoutMs: 2400000,
      output: dir + "/review-spec.md", outputSchema: REVIEW,
      label: "Check host against parity-spec gate list",
      task: preamble() + [
        "ROLE: spec-review for the Rust host (" + HOSTCRATE + ").",
        "TASK: Walk docs/rust-migration/phase-a/parity-spec.md gate list (G-R1...) against the implementation: resize contract + responsive states, selector + keyboard, fullscreen, hidpi, telemetry cadence + bounded history, physiology wiring, static chrome caching rules, typography registration + caching, CLI flags, screenshot path. Read the code; run the host where a behavior needs proof (close every process you start).",
        "verdict 'rejected' with required_changes for each unmet gate. WRITE to " + dir + "/review-spec.md. RETURN structured verdict."
      ].join("\n")
    })
  ]);

  const findings = reviews.map(function (r, i) {
    return { review: ["visual", "perf", "spec"][i], result: slim(r) };
  });
  const needsFix = reviews.some(function (r) {
    return r && r.structuredOutput && r.structuredOutput.verdict === "rejected";
  });

  let revised = null;
  if (needsFix) {
    emit("Phase C reviews found drift; one revision round");
    revised = await runs.run("c-host-rev1", {
      resume: host.runId, timeoutMs: 3600000,
      task: [
        "The parallel reviews REJECTED the host with these findings:",
        JSON.stringify(findings, null, 1),
        "Full reviews: " + dir + "/review-visual.md, review-perf.md, review-spec.md.",
        "Apply every concrete required_change inside " + HOSTCRATE + "/ (core crates: report needed changes, do not edit them). Rebuild release, regenerate screenshots, and make sure cargo build --release passes. Finish with the same structured report."
      ].join("\n")
    });
    const vcheck = await launch("c-recheck-visual", {
      agent: "design-critic", context: "fresh", timeoutMs: 1800000,
      output: dir + "/review-visual-round2.md", outputSchema: REVIEW,
      label: "Re-check visual drift after revision",
      task: preamble() + [
        "ROLE: visual-review round 2 after host revision. Rust screenshots under " + dir + "/shots/ (regenerate if stale). Previous drift: " + JSON.stringify((reviews[0] && reviews[0].structuredOutput) || {}, null, 1),
        "Verify each previous finding is resolved with pixel evidence; flag any NEW drift introduced by the revision. WRITE to " + dir + "/review-visual-round2.md. RETURN structured verdict."
      ].join("\n")
    });
    return {
      phase: "C", state: "revised",
      host: slim(host), reviews: findings, revision: slim(revised), visual_recheck: slim(vcheck),
      next_gate: "Integrator merges " + HOSTCRATE + ", commits, then Phase D with args {phase:'D'}."
    };
  }

  return {
    phase: "C", state: "accepted-first-pass",
    host: slim(host), reviews: findings,
    next_gate: "Integrator merges " + HOSTCRATE + ", commits, then Phase D with args {phase:'D'}."
  };
}

// ===========================================================================
// PHASE D - EXTREME OPTIMIZATION (independent measured investigations)
// ===========================================================================
if (PH === "D") {
  emit("Phase D: seven independent measured optimization investigations");
  const dir = "docs/rust-migration/phase-d";
  const METHOD = [
    "METHOD (mandatory): report baseline BEFORE the change, proposed change, measured result AFTER (same method, 3+ samples, report variance), complexity/risk, and recommendation adopt|reject|needs-more-evidence. Gains within run-to-run noise must be reported as noise, not wins. Prototype in /tmp or a scratch branch; do NOT edit integrated crates or commit; deliver production patches as proposals in your report.",
    ""
  ].join("\n");

  const invs = [
    { key: "d-buffer", agent: "coder", label: "Point-field storage layout", extra: "Investigate point-field buffer storage: f32/u16/u8 component opportunities vs parity cost (quantization error vs the parity spec tolerances), inactive-species memory (five species but how many simultaneously active?), scratch-buffer reuse across frames. Prototype + measure allocation and PSS delta on a /tmp benchmark harness linked to the integrated raster crate." },
    { key: "d-surface", agent: "coder", label: "Surface/texture duplication", extra: "Investigate decoded PNG / cairo / GDK texture duplication in the host: are module PNGs decoded more than once, at multiple scales, or held at stale sizes? Prototype active-layout-only caching + stale-size eviction; measure PSS delta on resize stress vs current." },
    { key: "d-font", agent: "researcher", label: "Measured Pango/font memory floor", extra: "Establish the ACTUAL Pango + fontconfig + font memory floor for this app (two faces, static text): measure PSS with fonts registered vs not, with layouts cached vs rebuilt per frame, via /proc/<pid>/smaps_rollup deltas on the host binary. NO speculation - every number measured. Identify what part of remaining PSS is font stack vs GTK core." },
    { key: "d-cadence", agent: "researcher", label: "Focused/unfocused/hidden scheduling", extra: "Compare Python's cadence policy (app.py GLib sources: what keeps running when unfocused/hidden) against the Rust host's. Investigate a hidden-state policy where the simulation advances analytically (no render) and resumes rendering on exposure - check Python semantics first so parity is preserved. Measure CPU% in each state for both implementations." },
    { key: "d-alloc", agent: "coder", label: "Hot-loop allocation proof", extra: "PROVE hot-loop allocation behavior of the integrated crates + host: build a /tmp harness with a counting global allocator (or dhat) around a representative 60 s run; report allocations/sec in steady state, top allocation sites, and whether the zero-allocation claims in the raster crate hold. Evidence only - no change needed if clean." },
    { key: "d-simd", agent: "coder", label: "Vectorization benchmark", extra: "Test vectorization ONLY where the profile says the accumulate/equation kernels matter: benchmark current scalar kernels vs iterator restructurings / explicit chunks (stable Rust; avoid nightly) on representative sizes; measure speedup AND parity (fixtures must still pass bit-for-bit or within spec tolerance). Recommend only if measured gain is real and parity-safe." },
    { key: "d-allocbench", agent: "coder", label: "Allocator comparison", extra: "Benchmark system allocator vs jemalloc/mimalloc (dev-only swap in a /tmp harness, never a committed default without evidence): PSS + frame-time + allocation latency on the host workload. Recommend only if measured deltas are material and consistent across 3+ runs." }
  ];

  const investigations = await Promise.all(invs.map(function (d) {
    return launch(d.key, {
      agent: d.agent, context: "fresh", timeoutMs: 2700000,
      output: dir + "/" + d.key.slice(2) + ".md", outputSchema: INVEST,
      label: d.label,
      task: preamble() + [
        "ROLE: " + d.key.slice(2) + "-optimizer (Phase D investigation). The Rust host (" + HOSTCRATE + ") and core crates under rust/ are integrated and passing.",
        "SCOPE: " + d.extra,
        METHOD
      ].join("\n")
    });
  }));

  emit("Phase D investigations done; evidence verifier rejects noise");
  const dverify = await launch("d-verify", {
    agent: "evidence-auditor", model: HIGH, context: "fresh", timeoutMs: 2400000,
    output: dir + "/OPTIMIZATION-VERDICT.md", outputSchema: VERIFY,
    label: "Reject noise-level optimizations",
    task: preamble() + [
      "ROLE: optimization evidence verifier. Seven investigations returned:",
      JSON.stringify(investigations.map(function (r, i) { return { area: invs[i].key, result: r && r.structuredOutput }; }), null, 1),
      "Full reports under " + dir + "/.",
      "TASK: For each investigation: check the measurement method (sample counts, variance, same-method baseline), reject recommendations whose gains are within noise, reject changes with unacceptable visual/behavioral/parity cost, and flag claims not supported by the evidence in the report. Produce the final adopt/reject list for the integrator with one-line justifications.",
      "coherent=true means the adopt list is safe to apply. WRITE to " + dir + "/OPTIMIZATION-VERDICT.md. RETURN structured output (put the adopt list in corrections, blockers in blocking_gaps)."
    ].join("\n")
  });

  return {
    phase: "D",
    reports_dir: ROOT + "/" + dir,
    investigations: investigations.map(slim),
    verdict: slim(dverify),
    next_gate: "Integrator applies only verifier-approved optimizations, re-runs parity gates, commits."
  };
}

// ===========================================================================
// PHASE E - EXTREME MEMORY RESEARCH (experimental, isolated)
// ===========================================================================
if (PH === "E") {
  const canIsolate = !!(args && args.canIsolate);
  emit("Phase E: experimental alternate backends (isolated=" + canIsolate + ")");
  const dir = "docs/rust-migration/phase-e";
  const CRITERIA = [
    "A replacement backend may be RECOMMENDED only if it: (1) preserves visual parity (screenshot deltas within parity spec), (2) preserves input + resizing (selector, keyboard, resize contract, fullscreen), (3) preserves typography (both faces, static text caching, seven-segment), (4) preserves physiology + telemetry semantics, (5) passes the Hyprland torture test (qa/torture.py methodology), (6) MATERIALLY reduces PSS and/or CPU vs the GTK host (beyond noise). Report the matrix row for all six, honestly, including FAILs."
  ].join("\n");

  const experiments = [
    { key: "e-wayland", agent: "coder", model: HIGH, label: "Minimal native Wayland host", extra: "Evaluate a minimal native Wayland host (wayland-client / smithay-client-toolkit): enough composition (module surfaces + live chamber + cached text) to measure honestly. Prototype in the worktree under rust-experiments/wayland-host/; measure PSS/CPU/frame-time vs the GTK host with the same method; fill the six-criteria matrix." },
    { key: "e-cpu", agent: "coder", model: HIGH, label: "CPU raster + minimal presentation", extra: "Evaluate pure CPU rasterization + minimal presentation (softbuffer / drm dumb buffers; no GTK): rasterize the same composition with the integrated raster crate and present via a scanout buffer. Prototype under rust-experiments/cpu-present/; measure; fill the six-criteria matrix." },
    { key: "e-gpu", agent: "coder", model: HIGH, label: "Minimal GPU presentation", extra: "Evaluate minimal GPU presentation (vulkano/wgpu or raw GL): the real driver-stack PSS cost on THIS machine (mesa/radv) is the question - measure the floor of an almost-empty GPU app, then the full composition. Prototype under rust-experiments/gpu-present/; measure; fill the six-criteria matrix." },
    { key: "e-text", agent: "researcher", model: HIGH, label: "Lightweight text stack", extra: "ONLY because the Phase A framework floor shows Pango/fontconfig cost: evaluate a lightweight text stack (cosmic-text / fontdue / ab_glyph) for exactly this app's needs: two faces, static label caching, seven-segment. Judge typography parity risk honestly (hinting, subpixel positions, letter-spacing); prototype a label-sheet comparison under rust-experiments/text-light/; measure PSS delta; fill the six-criteria matrix." }
  ];

  const results = await Promise.all(experiments.map(function (d) {
    return launch(d.key, {
      agent: d.agent, model: d.model, context: "fresh", timeoutMs: 3000000,
      worktree: canIsolate || undefined,
      output: dir + "/" + d.key.slice(2) + ".md", outputSchema: INVEST,
      label: d.label,
      task: preamble() + [
        "ROLE: " + d.key.slice(2) + " researcher (Phase E EXPERIMENT). This is exploratory: the proven GTK host stays the reference; you are measuring whether a lighter backend could ever replace it.",
        "CONTEXT: docs/rust-migration/phase-a/framework-floor.md shows what GTK/Cairo/Pango cost on this machine; docs/rust-migration/phase-d/ has the current optimization state. The integrated host is " + HOSTCRATE + ".",
        "SCOPE: " + d.extra,
        "RULES: prototype ONLY inside your assigned rust-experiments/ subtree (or worktree); never touch " + HOSTCRATE + " or the core crates; measure with 3+ samples and report variance; close every process you start.",
        CRITERIA,
        ""
      ].join("\n")
    });
  }));

  emit("Phase E experiments done; decision matrix synthesis");
  const everify = await launch("e-verify", {
    agent: "evidence-auditor", model: XHIGH, context: "fresh", timeoutMs: 2400000,
    output: dir + "/BACKEND-DECISION.md", outputSchema: VERIFY,
    label: "Consolidate backend decision matrix",
    task: preamble() + [
      "ROLE: backend decision verifier. Four experimental backend investigations returned:",
      JSON.stringify(results.map(function (r, i) { return { experiment: experiments[i].key, result: r && r.structuredOutput }; }), null, 1),
      "Full reports under " + dir + "/.",
      "TASK: Consolidate the six-criteria decision matrix per backend from the actual evidence (re-check the reports; flag unsupported claims). The GTK Rust host remains the implementation unless a backend passes ALL six criteria. If none passes, say so plainly and name the closest option + what evidence is still missing.",
      "coherent=true means the matrix is evidence-complete. WRITE to " + dir + "/BACKEND-DECISION.md. RETURN structured output."
    ].join("\n")
  });

  return {
    phase: "E",
    reports_dir: ROOT + "/" + dir,
    experiments: results.map(slim),
    decision: slim(everify),
    next_gate: "Integrator decides: keep GTK host (default) or schedule a follow-up replacement effort. Nothing lands from Phase E automatically."
  };
}

// ===========================================================================
// PHASE F - INDEPENDENT FINAL VERIFICATION
// ===========================================================================
if (PH === "F") {
  emit("Phase F: five independent final verifiers, then FACTS/MEASUREMENTS synthesis");
  const dir = "docs/rust-migration/phase-f";

  const finals = [
    {
      key: "f-visual", agent: "design-critic", model: null, outputSchema: FINAL,
      label: "Final screenshot comparison vs Python",
      task: [
        "ROLE: visual-final verifier. Independent final check: generate FRESH screenshots of the Rust host (" + HOSTCRATE + " --screenshot) and FRESH Python goldens (qa/shots.py). Compare at 2+ window sizes. Report every drift with pixel-region evidence and a final verdict pass|fail|conditional (conditional = drift within parity-spec tolerance).",
        "WRITE to " + dir + "/visual-final.md. RETURN structured verdict."
      ].join("\n")
    },
    {
      key: "f-math", agent: "researcher", model: XHIGH, outputSchema: FINAL,
      label: "Independent equation/physiology parity",
      task: [
        "ROLE: math-final verifier. Independently verify equation + physiology parity: do NOT trust the crate fixtures - generate NEW golden vectors from the Python modules yourself (multiple species, timestamps, dt values, telemetry timelines), run the Rust crates' public APIs against them (a /tmp harness is fine), and report max abs/rel error per species/model vs the parity spec tolerances. Verdict pass only if all within tolerance.",
        "WRITE to " + dir + "/math-final.md. RETURN structured verdict."
      ].join("\n")
    },
    {
      key: "f-perf", agent: "researcher", model: null, outputSchema: FINAL,
      label: "Independent benchmark repetition",
      task: [
        "ROLE: perf-final verifier. Repeat the benchmark suite independently: focused/unfocused/hidden CPU%, frame-time p50/p95, 3+ samples, for BOTH the Python app and the Rust host, same method and durations. Compare against the numbers claimed in docs/rust-migration/phase-d/ and phase-c reports; flag any claim your measurements contradict. Verdict reflects whether the claimed performance results reproduce.",
        "WRITE to " + dir + "/perf-final.md. RETURN structured verdict."
      ].join("\n")
    },
    {
      key: "f-memory", agent: "evidence-auditor", model: XHIGH, outputSchema: FINAL,
      label: "smaps_rollup accounting audit",
      task: [
        "ROLE: memory-final verifier. Measure the Rust host's memory from /proc/<pid>/smaps_rollup (Rss/Pss/Shared/Private, 3+ samples, steady state) and compare against the Python baseline (docs/rust-migration/phase-a/memory.md). Verify NO misleading accounting: PSS not RSS for shared-library comparisons, both apps measured at the same window size and state, font/GTK shared pages counted consistently. Report per-category composition and the honest final PSS delta.",
        "WRITE to " + dir + "/memory-final.md. RETURN structured verdict."
      ].join("\n")
    },
    {
      key: "f-qa", agent: "interaction-qa", model: null, outputSchema: FINAL,
      label: "All gates + Hyprland torture",
      task: [
        "ROLE: qa-final verifier on the live desktop. Run the FULL gate suite and report PASS/FAIL per gate: (1) Python suite must still pass untouched: qa/gates.py, qa/console_gates.py, qa/production_gates.py, qa/physiology_gates.py. (2) Rust equivalents built during the migration (find them under rust/ and docs/rust-migration/). (3) qa/torture.py --shots methodology against the RUST host: real Hyprland resize torture, screenshots as evidence. Close every process you start. Verdict pass only if everything passes.",
        "WRITE to " + dir + "/qa-final.md. RETURN structured verdict."
      ].join("\n")
    }
  ];

  const verdicts = await Promise.all(finals.map(function (d) {
    return launch(d.key, {
      agent: d.agent, model: d.model || FLASH, context: "fresh", timeoutMs: 2700000,
      output: dir + "/" + d.key.slice(2) + "-final.md", outputSchema: d.outputSchema,
      label: d.label,
      task: preamble() + d.task
    });
  }));

  emit("Phase F verdicts in; synthesis draft");
  const synthesis = await launch("f-synthesis", {
    agent: "researcher", model: HIGH, context: "fresh", timeoutMs: 1800000,
    output: dir + "/FINAL-REPORT.md", outputSchema: VERIFY,
    label: "Draft final report (FACTS/MEASUREMENTS/INFERENCES/TARGETS)",
    task: preamble() + [
      "ROLE: final-report synthesizer. Five independent final verdicts:",
      JSON.stringify(verdicts.map(function (r, i) { return { area: finals[i].key, result: r && r.structuredOutput }; }), null, 1),
      "Prior phase reports: docs/rust-migration/phase-a .. phase-e.",
      "TASK: Draft docs/rust-migration/FINAL-REPORT.md with FOUR clearly separated sections: FACTS (what exists, with paths), MEASUREMENTS (numbers + methods + samples), INFERENCES (labeled, with basis), ASPIRATIONAL TARGETS (not achieved, explicitly marked). Quote the five verdicts faithfully; do not upgrade a conditional to a pass; do not average away failures.",
      "RETURN structured output: coherent=true if every verifier verdict is faithfully represented; blocking_gaps = failing gates; corrections = caveats the integrator must state publicly."
    ].join("\n")
  });

  return {
    phase: "F",
    reports_dir: ROOT + "/" + dir,
    final_report: ROOT + "/" + dir + "/FINAL-REPORT.md",
    verdicts: verdicts.map(slim),
    synthesis: slim(synthesis),
    next_gate: "Integrator reviews FINAL-REPORT.md, accepts or demands fixes. The integrator, not the agents, declares victory."
  };
}

return { error: "unknown phase", phase: PH, valid: ["A", "B", "C", "D", "E", "F"] };
