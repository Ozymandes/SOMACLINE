"""Pure-stdlib telemetry source: /proc + /sys sampling for the abyssal monitor.

No third-party dependencies (no psutil). Designed to be called at ~2 Hz from
a GLib timeout on the UI thread: `sample()` must be cheap, non-blocking, and
must NEVER raise -- on any failure it degrades gracefully and returns the
last known-good values instead.

Usage:
    python3 -m abyssal.telemetry.source
"""

from __future__ import annotations

import glob
import os

from abyssal.core.signals import Telemetry

# Temperature normalisation range (Celsius).
_TEMP_LO = 30.0
_TEMP_HI = 95.0

# Preferred sensor selection order (chip name, then label substrings to prefer
# within that chip, in priority order). Empty label list = accept any temp*.
_CHIP_LABEL_PRIORITY: list[tuple[str, list[str]]] = [
    ("k10temp", ["tctl", "tdie"]),
    ("coretemp", ["package id 0"]),
    ("k10temp", []),
    ("acpitz", []),
    ("amdgpu", []),
]


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class _SensorRef:
    """A resolved hwmon temperature sensor input file + short display tag."""

    __slots__ = ("input_path", "label_tag")

    def __init__(self, input_path: str, label_tag: str) -> None:
        self.input_path = input_path
        self.label_tag = label_tag


def _discover_hwmon_chips() -> list[tuple[str, str]]:
    """Return list of (chip_name, hwmon_dir) for every readable hwmon device."""
    chips: list[tuple[str, str]] = []
    try:
        for hwmon_dir in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            name = _read_text(os.path.join(hwmon_dir, "name"))
            if name:
                chips.append((name, hwmon_dir))
    except OSError:
        pass
    return chips


def _temp_inputs_for_chip(hwmon_dir: str) -> list[tuple[str, str | None]]:
    """Return list of (input_path, label_or_none) for a hwmon chip dir."""
    results: list[tuple[str, str | None]] = []
    try:
        for input_path in sorted(glob.glob(os.path.join(hwmon_dir, "temp*_input"))):
            label_path = input_path.replace("_input", "_label")
            label = _read_text(label_path)
            results.append((input_path, label))
    except OSError:
        pass
    return results


def _select_sensor() -> tuple[_SensorRef | None, str]:
    """Probe /sys/class/hwmon once and pick the best temperature sensor.

    Returns (sensor_ref_or_None, note_string).
    """
    chips = _discover_hwmon_chips()
    if not chips:
        return None, "no hwmon chips found"

    by_name: dict[str, list[str]] = {}
    for name, path in chips:
        by_name.setdefault(name, []).append(path)

    # Try the priority list first.
    for chip_name, label_prefs in _CHIP_LABEL_PRIORITY:
        dirs = by_name.get(chip_name)
        if not dirs:
            continue
        for hwmon_dir in dirs:
            inputs = _temp_inputs_for_chip(hwmon_dir)
            if not inputs:
                continue
            if label_prefs:
                for pref in label_prefs:
                    for input_path, label in inputs:
                        if label and pref in label.lower():
                            return (
                                _SensorRef(input_path, chip_name.upper()),
                                f"temp={chip_name}:{label}",
                            )
                # no matching label on this chip's inputs; fall through to any
                # input on this chip further below only if label_prefs empty.
                continue
            else:
                # Accept the first readable input on this chip.
                input_path, label = inputs[0]
                return (
                    _SensorRef(input_path, chip_name.upper()),
                    f"temp={chip_name}:{label or 'temp1'}",
                )

    # Fall back: any hwmon temp*_input at all.
    for name, hwmon_dir in chips:
        inputs = _temp_inputs_for_chip(hwmon_dir)
        if inputs:
            input_path, label = inputs[0]
            return (
                _SensorRef(input_path, name.upper()),
                f"temp={name}:{label or 'temp1'} (fallback)",
            )

    return None, "no readable temp*_input found"


class TelemetrySource:
    """Stdlib-only sampler of CPU load, memory pressure, and temperature."""

    def __init__(self) -> None:
        # CPU delta baseline.
        self._prev_cpu_total: int | None = None
        self._prev_cpu_idle: int | None = None

        # Last-known-good values for graceful degradation.
        self._last_cpu_load = 0.0
        self._last_cpu_pct = 0.0
        self._last_mem_pressure = 0.0
        self._last_mem_used_gb = 0.0
        self._last_mem_total_gb = 0.0
        self._last_temp = 0.0
        self._last_temp_c: float | None = None

        self._mem_total_kb: int | None = None
        try:
            mem_total = _read_int_from_meminfo_line("MemTotal")
            self._mem_total_kb = mem_total
            if mem_total:
                self._last_mem_total_gb = mem_total / (1024.0 * 1024.0)
        except Exception:
            self._mem_total_kb = None

        sensor_ref: _SensorRef | None = None
        sensor_note = "temp probe failed"
        try:
            sensor_ref, sensor_note = _select_sensor()
        except Exception:
            sensor_ref, sensor_note = None, "temp probe raised"

        self._sensor: _SensorRef | None = sensor_ref
        self._notes = f"cpu=/proc/stat mem=/proc/meminfo {sensor_note}"

    # -- CPU -----------------------------------------------------------

    def _sample_cpu(self) -> tuple[float, float]:
        """Return (cpu_load 0..1, cpu_pct 0..100). Never raises."""
        try:
            with open("/proc/stat", "r") as f:
                line = f.readline()
        except OSError:
            return self._last_cpu_load, self._last_cpu_pct

        try:
            parts = line.split()
            if not parts or parts[0] != "cpu":
                return self._last_cpu_load, self._last_cpu_pct
            fields = [int(x) for x in parts[1:]]
        except (ValueError, IndexError):
            return self._last_cpu_load, self._last_cpu_pct

        if len(fields) < 4:
            return self._last_cpu_load, self._last_cpu_pct

        # user nice system idle iowait irq softirq steal guest guest_nice
        idle_fields = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
        total = sum(fields)

        if self._prev_cpu_total is None:
            self._prev_cpu_total = total
            self._prev_cpu_idle = idle_fields
            self._last_cpu_load = 0.0
            self._last_cpu_pct = 0.0
            return 0.0, 0.0

        total_delta = total - self._prev_cpu_total
        idle_delta = idle_fields - self._prev_cpu_idle if self._prev_cpu_idle is not None else 0

        self._prev_cpu_total = total
        self._prev_cpu_idle = idle_fields

        if total_delta <= 0:
            return self._last_cpu_load, self._last_cpu_pct

        busy_delta = total_delta - idle_delta
        load = busy_delta / total_delta
        load = max(0.0, min(1.0, load))

        self._last_cpu_load = load
        self._last_cpu_pct = load * 100.0
        return self._last_cpu_load, self._last_cpu_pct

    # -- Memory ----------------------------------------------------------

    def _sample_memory(self) -> tuple[float, float, float]:
        """Return (memory_pressure 0..1, mem_used_gb, mem_total_gb). Never raises."""
        try:
            mem_total_kb = None
            mem_available_kb = None
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        mem_total_kb = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        mem_available_kb = int(line.split()[1])
                    if mem_total_kb is not None and mem_available_kb is not None:
                        break
        except (OSError, ValueError, IndexError):
            return self._last_mem_pressure, self._last_mem_used_gb, self._last_mem_total_gb

        if not mem_total_kb:
            return self._last_mem_pressure, self._last_mem_used_gb, self._last_mem_total_gb
        if mem_available_kb is None:
            mem_available_kb = mem_total_kb

        pressure = 1.0 - (mem_available_kb / mem_total_kb)
        pressure = max(0.0, min(1.0, pressure))

        used_kb = mem_total_kb - mem_available_kb
        used_gb = used_kb / (1024.0 * 1024.0)
        total_gb = mem_total_kb / (1024.0 * 1024.0)

        self._last_mem_pressure = pressure
        self._last_mem_used_gb = used_gb
        self._last_mem_total_gb = total_gb
        return pressure, used_gb, total_gb

    # -- Temperature -------------------------------------------------------

    def _sample_temp(self) -> tuple[float, bool, float | None, str]:
        """Return (temperature 0..1, temp_available, temp_c, temp_label). Never raises."""
        if self._sensor is None:
            return 0.0, False, None, "--"

        raw = _read_int(self._sensor.input_path)
        if raw is None:
            # Sensor went away / unreadable this sample; degrade but keep label
            # so the UI shows it's the same sensor, just stale/unavailable.
            if self._last_temp_c is not None:
                return self._last_temp, True, self._last_temp_c, self._sensor.label_tag
            return 0.0, False, None, "--"

        temp_c = raw / 1000.0
        norm = (temp_c - _TEMP_LO) / (_TEMP_HI - _TEMP_LO)
        norm = max(0.0, min(1.0, norm))

        self._last_temp = norm
        self._last_temp_c = temp_c
        return norm, True, temp_c, self._sensor.label_tag

    # -- Public API ----------------------------------------------------------

    def sample(self) -> Telemetry:
        try:
            cpu_load, cpu_pct = self._sample_cpu()
        except Exception:
            cpu_load, cpu_pct = self._last_cpu_load, self._last_cpu_pct

        try:
            mem_pressure, mem_used_gb, mem_total_gb = self._sample_memory()
        except Exception:
            mem_pressure = self._last_mem_pressure
            mem_used_gb = self._last_mem_used_gb
            mem_total_gb = self._last_mem_total_gb

        try:
            temperature, temp_available, temp_c, temp_label = self._sample_temp()
        except Exception:
            temperature, temp_available, temp_c, temp_label = 0.0, False, None, "--"

        try:
            return Telemetry(
                cpu_load=cpu_load,
                memory_pressure=mem_pressure,
                temperature=temperature,
                temp_available=temp_available,
                cpu_pct=cpu_pct,
                mem_used_gb=mem_used_gb,
                mem_total_gb=mem_total_gb,
                temp_c=temp_c,
                temp_label=temp_label,
                notes=self._notes,
            )
        except Exception:
            # Absolute last resort: never raise out of sample().
            return Telemetry(notes="telemetry construction failed")


def _read_int_from_meminfo_line(key: str) -> int | None:
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith(key + ":"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def self_test() -> None:
    import time

    print("=== TelemetrySource self_test ===")
    src = TelemetrySource()
    print(f"probe notes: {src._notes}")

    for i in range(5):
        t = src.sample()
        print(f"[{i}] {t}")

        for name, val in (
            ("cpu_load", t.cpu_load),
            ("memory_pressure", t.memory_pressure),
            ("temperature", t.temperature),
        ):
            assert 0.0 <= val <= 1.0, f"{name}={val} out of [0,1] range"

        if i < 4:
            time.sleep(0.3)

    print("normal-path sampling OK: all 0..1 fields within range, no exceptions.")

    # --- Failure-path test: monkeypatch to nonexistent files/dirs. ---
    print("--- failure path test ---")
    bad_src = TelemetrySource()
    # Force sensor lookup to a nonexistent path.
    bad_src._sensor = _SensorRef("/nonexistent/path/temp1_input", "FAKE")

    import builtins

    real_open = builtins.open

    def _boom_open(path, *args, **kwargs):
        if isinstance(path, str) and (
            path.startswith("/proc/stat")
            or path.startswith("/proc/meminfo")
            or path.startswith("/nonexistent")
        ):
            raise OSError(f"simulated failure for {path}")
        return real_open(path, *args, **kwargs)

    builtins.open = _boom_open
    try:
        t = bad_src.sample()
        print(f"degraded sample: {t}")
        assert t.temp_available is False or t.temp_c is not None
        assert 0.0 <= t.cpu_load <= 1.0
        assert 0.0 <= t.memory_pressure <= 1.0
        assert 0.0 <= t.temperature <= 1.0
        # Calling again must still not raise.
        t2 = bad_src.sample()
        assert 0.0 <= t2.cpu_load <= 1.0
    finally:
        builtins.open = real_open

    print("failure-path test OK: degraded gracefully, no exception raised.")
    print("=== self_test PASSED ===")


if __name__ == "__main__":
    self_test()
