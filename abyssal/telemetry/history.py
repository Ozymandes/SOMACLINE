"""Bounded telemetry history for the graph wells.

Sampled at the TELEMETRY cadence (app.TELEMETRY_HZ), never at render FPS, so
a graph's time axis means seconds regardless of how fast the window draws.

Each channel is a preallocated float32 ring. `push` is O(1) and allocates
nothing; `window(n_cols)` reduces the whole span to at most one value per
plot column (column mean) for drawing, with a cached index table so the
reduction is a single NumPy call per frame.
"""

from __future__ import annotations

import numpy as np

#: Span shown by every graph, in seconds.
SPAN_S = 60.0


class Ring:
    __slots__ = ("cap", "_buf", "_n", "_head", "_idx_cache", "version")

    def __init__(self, cap: int) -> None:
        self.cap = int(cap)
        self._buf = np.zeros(self.cap, dtype=np.float32)
        self._n = 0
        self._head = 0          # next write position
        self._idx_cache: dict[int, np.ndarray] = {}
        #: bumps on every push; lets a renderer cache the trace between samples
        self.version = 0

    def __len__(self) -> int:
        return self._n

    def push(self, v: float) -> None:
        self._buf[self._head] = v
        self._head = (self._head + 1) % self.cap
        if self._n < self.cap:
            self._n += 1
        self.version += 1

    def last(self, default: float = 0.0) -> float:
        if not self._n:
            return default
        return float(self._buf[(self._head - 1) % self.cap])

    def values(self) -> np.ndarray:
        """Oldest -> newest, length len(self). A view-free copy."""
        if self._n < self.cap:
            return self._buf[: self._n].copy()
        return np.concatenate((self._buf[self._head:], self._buf[: self._head]))

    def peak(self) -> float:
        return float(self._buf[: self._n].max()) if self._n else 0.0

    def window(self, cols: int) -> np.ndarray:
        """The full span reduced to `cols` column means, oldest first.

        Missing history (a freshly started monitor) is returned as NaN so the
        trace starts where data starts instead of dropping to zero.
        """
        cols = max(2, int(cols))
        v = self.values()
        full = np.full(self.cap, np.nan, dtype=np.float32)
        if len(v):
            full[self.cap - len(v):] = v
        edges = self._idx_cache.get(cols)
        if edges is None:
            edges = np.linspace(0, self.cap, cols + 1).astype(np.int64)
            if len(self._idx_cache) > 8:
                self._idx_cache.clear()
            self._idx_cache[cols] = edges
        with np.errstate(invalid="ignore"):
            s = np.add.reduceat(np.nan_to_num(full), edges[:-1])
            c = np.add.reduceat((~np.isnan(full)).astype(np.float32), edges[:-1])
            out = np.where(c > 0, s / np.maximum(c, 1.0), np.nan)
        return out.astype(np.float32)


class History:
    """One ring per telemetry channel, all at the same cadence."""

    CHANNELS = ("cpu", "thermal", "memory", "frame")

    def __init__(self, hz: float) -> None:
        self.hz = float(hz)
        self.cap = int(round(SPAN_S * self.hz))
        self.rings = {k: Ring(self.cap) for k in self.CHANNELS}

    def __getitem__(self, key: str) -> Ring:
        return self.rings[key]

    def push(self, cpu_pct: float, temp_c: float | None, mem_gb: float,
             frame_ms: float) -> None:
        self.rings["cpu"].push(cpu_pct)
        self.rings["thermal"].push(np.nan if temp_c is None else temp_c)
        self.rings["memory"].push(mem_gb)
        self.rings["frame"].push(frame_ms)

    @property
    def nbytes(self) -> int:
        return sum(r._buf.nbytes for r in self.rings.values())
