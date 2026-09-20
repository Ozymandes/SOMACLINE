"""Shared value types crossing workstream boundaries.

Raw OS telemetry and organism physiology are deliberately SEPARATE types.
telemetry/ produces Telemetry. core.physiology maps Telemetry -> Physiology.
organism/ consumes only Physiology and has no idea the OS exists.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Telemetry:
    """One sample of real machine state. 0..1 fields are normalised."""

    cpu_load: float = 0.0          # 0..1
    memory_pressure: float = 0.0   # 0..1
    temperature: float = 0.0       # 0..1 (0 when unavailable)
    io_rate: float = 0.0           # 0..1  block + network throughput

    temp_available: bool = False
    io_available: bool = False

    # Raw, for display only.
    cpu_pct: float = 0.0
    mem_used_gb: float = 0.0
    mem_total_gb: float = 0.0
    temp_c: float | None = None
    temp_label: str = "--"
    io_mb_s: float = 0.0
    net_mb_s: float = 0.0
    notes: str = ""


@dataclass(frozen=True, slots=True)
class Physiology:
    """Organism drive parameters. Smoothed, bounded, unitless."""

    agitation: float = 0.15   # 0..1  plume motion energy      <- cpu
    pulse: float = 0.20       # 0..1  breathing rate / expansion <- temperature
    density: float = 0.35     # 0..1  filament fullness          <- memory
    flux: float = 0.0         # 0..1  peripheral event energy    <- io / net

    #: Rises sharply on an I/O or network burst and decays over seconds, so a
    #: transient shows on the creature as a propagating event rather than as a
    #: step change. Distinct from `flux`, which is the smoothed level.
    surge: float = 0.0        # 0..1

    # Always-present idle life, so the creature breathes with no load at all.
    vitality: float = 1.0
