# abyssal-rust-native — reusable native-translation + optimization workflow

A staged Python→Rust migration workflow for the **Abyssal Organism Monitor**,
built for the installed **pi-subagents** (`subagent` tool, `workflowScriptPath`)
system. The main pi session is the **INTEGRATOR / ARCHITECT**: it owns
architectural decisions, merges, shared-file edits, parity acceptance,
benchmark acceptance and commits. Subagents inspect, profile, benchmark,
research, implement isolated components, review and verify — nothing lands
without the integrator.

## Running a phase

```js
subagent({
  workflowScriptPath: ".pi/workflows/abyssal-rust-native.js",
  cwd: "/home/seeno/abyssal-organism-monitor",
  args: { phase: "A" }            // one of A B C D E F
})
```

| arg | default | meaning |
|---|---|---|
| `phase` | `"A"` | which phase to run |
| `root` | `/home/seeno/abyssal-organism-monitor` | project root |
| `baseline` | `docs/rust-migration/phase-a/parity-spec.md` | parity contract used by B–F |
| `crate` | `rust/console` | parity-host crate for C–F |
| `canIsolate` | `false` | set `true` for Phase E so experiment lanes get managed git worktrees (requires clean tree) |

Each invocation is one bounded fan-out. The integrator reads the returned
dossier, merges accepted work, commits, then launches the next phase.

## Phases, agents, dependencies

| Phase | Lanes (key → agent) | Verification gate | Depends on |
|---|---|---|---|
| **A** Discovery/Baseline | `a-arch` researcher (glm-5.3:high), `a-mem` researcher, `a-cpu` researcher, `a-floor` coder, `a-parity` researcher (glm-5.3:high) → `a-verify` evidence-auditor (glm-5.3:xhigh) | verifier must return `coherent == true` | — |
| **B** Core Translation | per module (`telemetry`, `physiology`, `creature`, `raster`): `b-<m>-impl` coder + `cargo test` host gate → `b-<m>-review` reviewer (glm-5.3:xhigh, instructed to REFUTE) → one revision (`resume`) → `b-<m>-review2` | `cargo test --manifest-path rust/<m>/Cargo.toml` must pass on the host after the child finishes | A coherent |
| **C** GTK Parity Host | `c-host` coder (glm-5.3:high, single writer) → `c-review-visual` design-critic + `c-review-perf` researcher + `c-review-spec` reviewer → one revision → `c-recheck-visual` | `cargo build --release` host gate; structured verdicts; screenshot drift evidence | B merged by integrator |
| **D** Extreme Optimization | `d-buffer`, `d-surface`, `d-font`, `d-cadence`, `d-alloc`, `d-simd`, `d-allocbench` → `d-verify` evidence-auditor | every recommendation needs measured baseline + measured result; noise-level gains rejected | C merged |
| **E** Memory Research | `e-wayland`, `e-cpu`, `e-gpu`, `e-text` (worktree-isolated when `canIsolate`) → `e-verify` evidence-auditor | six-criteria replacement matrix; **never auto-lands** | A floor evidence; clean tree |
| **F** Final Verification | `f-visual` design-critic, `f-math` researcher (xhigh), `f-perf` researcher, `f-memory` evidence-auditor (xhigh), `f-qa` interaction-qa → `f-synthesis` researcher | FINAL-REPORT.md separates FACTS / MEASUREMENTS / INFERENCES / ASPIRATIONAL TARGETS | final integrated tree |

All children run with `context: "fresh"`; reviewers never share the parent
transcript; nothing is accepted because an implementation agent claimed it.

## Reports (project artifacts)

Each phase writes durable reports under `docs/rust-migration/phase-<x>/`,
e.g. phase A produces `architecture.md`, `memory.md`, `cpu-profile.md`,
`framework-floor.md`, `parity-spec.md`, `BASELINE-VERIFY.md`.

## Retry rules

1. Every launch goes through a `launch()` wrapper: **one** automatic fresh
   retry per failed child (key suffix `-retry`).
2. Review loops are capped: **one revision round + re-review** per module;
   still-rejected lanes are returned as `rejected-after-revision` for the
   integrator instead of looping.
3. Blocked lanes (`blocked-implementation`, `blocked-revision`,
   `blocked-host-build`) are surfaced, never silently skipped.
4. Workflow infrastructure failures abort the phase; the integrator inspects
   `subagent({ action: "status" })` and re-runs the phase.

## Concurrency rules

- Read/measure/experiment lanes fan out aggressively (phases A, D, F fully
  parallel; B runs 4 independent lanes concurrently).
- Shared-production-file edits are never parallelized: Phase C uses a single
  writer; Phase B lanes own disjoint crate directories (`rust/telemetry`,
  `rust/physiology`, `rust/creature`, `rust/raster`) and are forbidden from
  creating a shared `rust/Cargo.toml` — the integrator creates the workspace.
- Competing implementations and experiments (Phase E) use managed worktrees.

## Model strategy

GLM-5.3 Flash (`zai/glm-5.3-flash`, the session model) for the broad work —
audits, profiling, implementation, benches, live QA. Raised thinking via
`zai/glm-5.3:high` / `:xhigh` for architecture audits, the parity spec,
parity refutation reviews, renderer/host redesign, optimization-evidence
verification and final verification. Expensive reasoning is not spent on
mechanical tasks.

## Reuse

The shape (parallel discovery → adversarial verify → isolated translation
lanes with refuting review → single-writer parity host → measured
optimization with an evidence verifier → experimental backends → independent
final verification) is generic: point `root`/`baseline`/`crate` at any
generated computational interface to reuse the workflow.
