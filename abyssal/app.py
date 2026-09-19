"""ABYSSAL ORGANISM MONITOR — host.

HOST CONTRACT
-------------
The window contains exactly ONE widget: a GtkDrawingArea. There is no widget
tree to relayout, no GL context, no texture, no framebuffer, no scene graph and
no render thread. Consequently a resize CANNOT churn rendering state, because
there is no rendering state that outlives a frame.

A resize does exactly one thing: the next draw receives different `width` and
`height` arguments, from which a fresh Layout and Viewport value are computed.
Simulation state is a function of TIME ONLY and is never touched by geometry.

Frame loop:  GdkFrameClock tick -> advance sim -> queue_draw.
Draw:        pure function of (sim state, width, height). No side effects.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .core.layout import LayoutState, resolve
from .core.physiology import PhysiologyModel
from .core.signals import Telemetry
from .core.theme import ABYSS
from .core.viewport import Viewport, isotropy_error
from .organism.plumiradia import Plumiradia
from .organism.render import draw_calibration, draw_organism
from .telemetry.source import TelemetrySource
from .ui.chrome import draw_background, draw_chrome
from .ui.debug import draw_debug

APP_ID = "dev.abyssal.OrganismMonitor"
TELEMETRY_INTERVAL_MS = 500


class Monitor(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, opts: argparse.Namespace) -> None:
        super().__init__(application=app)
        self.opts = opts
        self.set_title("Abyssal Organism Monitor")
        self.set_default_size(opts.width, opts.height)

        # --- simulation state: created ONCE, for the lifetime of the process.
        self.org = Plumiradia(seed=opts.seed)
        self.phys_model = PhysiologyModel()
        self.telemetry_src = TelemetrySource()
        self.telemetry = Telemetry()

        # --- instrumentation (proves the resize contract holds)
        self.frames = 0
        self.resizes = 0
        self.rebuilds = 0          # must stay 0 forever; nothing rebuilds
        self.last_size = (0, 0)
        self.last_state: LayoutState | None = None
        self._fps = 0.0
        self._frame_ms = 0.0
        self._sim_ms = 0.0
        self._draw_ms = 0.0
        self._fps_accum = 0
        self._fps_t0 = time.perf_counter()
        self._last_tick_us = 0
        self.show_debug = opts.debug
        self.show_calibration = opts.calibration
        self._probe = open(opts.probe, "a", buffering=1) if opts.probe else None

        # --- the one and only widget
        self.area = Gtk.DrawingArea()
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        self.area.set_draw_func(self._on_draw)
        self.set_child(self.area)

        self.area.add_tick_callback(self._on_tick)
        GLib.timeout_add(TELEMETRY_INTERVAL_MS, self._on_telemetry)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self.connect("notify::default-width", lambda *_: None)

    # ------------------------------------------------------------------ input
    def _on_key(self, _ctrl, keyval, _code, _mods) -> bool:
        name = Gdk.keyval_name(keyval)
        if name in ("q", "Q", "Escape"):
            self.close()
        elif name == "F1":
            self.show_debug = not self.show_debug
        elif name == "F2":
            self.show_calibration = not self.show_calibration
        elif name in ("f", "F", "F11"):
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.fullscreen()
        else:
            return False
        self.area.queue_draw()
        return True

    # -------------------------------------------------------------- telemetry
    def _on_telemetry(self) -> bool:
        # Telemetry only feeds the physiology smoother and the readout strings.
        # It can never change layout geometry, so a sample can never move the UI.
        self.telemetry = self.telemetry_src.sample()
        return GLib.SOURCE_CONTINUE

    # ------------------------------------------------------------- frame loop
    def _on_tick(self, _widget, clock: Gdk.FrameClock) -> bool:
        now_us = clock.get_frame_time()
        if self._last_tick_us == 0:
            dt = 1.0 / 60.0
        else:
            dt = (now_us - self._last_tick_us) / 1_000_000.0
        self._last_tick_us = now_us
        # Clamp: an occluded/unmapped window stops ticking; on resume the gap
        # must not teleport the organism.
        dt = max(0.0, min(dt, 0.1))

        t0 = time.perf_counter()
        phys = self.phys_model.update(dt, self.telemetry)
        self.org.update(dt, phys)
        self._sim_ms = (time.perf_counter() - t0) * 1000.0

        self.area.queue_draw()
        return GLib.SOURCE_CONTINUE

    # ------------------------------------------------------------------- draw
    def _on_draw(self, area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        t0 = time.perf_counter()

        # THE ENTIRE RESIZE RESPONSE, IN FULL:
        layout = resolve(width, height)
        vp = Viewport.for_stage(layout.stage.x, layout.stage.y,
                                layout.stage.w, layout.stage.h)
        # (that is all — no allocation, no rebuild, no reset)

        if (width, height) != self.last_size:
            self.resizes += 1
            self.last_size = (width, height)
            self._log_probe(layout, vp, "resize")
        if layout.state is not self.last_state:
            self.last_state = layout.state
            self._log_probe(layout, vp, "state")

        draw_background(cr, width, height)
        if layout.stage.valid:
            draw_organism(cr, vp, self.org)
            if self.show_calibration:
                draw_calibration(cr, vp, self.org)
        draw_chrome(cr, layout, self.telemetry, self._fps, self._frame_ms)

        self._draw_ms = (time.perf_counter() - t0) * 1000.0
        if self.show_debug:
            draw_debug(cr, layout, vp, self._debug_info(layout, vp, area))

        self.frames += 1
        self._tick_fps()

    def _tick_fps(self) -> None:
        self._fps_accum += 1
        now = time.perf_counter()
        elapsed = now - self._fps_t0
        if elapsed >= 0.5:
            self._fps = self._fps_accum / elapsed
            self._frame_ms = (elapsed / self._fps_accum) * 1000.0
            self._fps_accum = 0
            self._fps_t0 = now

    def _debug_info(self, layout, vp, area) -> dict:
        scale = area.get_scale_factor()
        try:
            surf = self.get_surface()
            gdk_scale = surf.get_scale() if hasattr(surf, "get_scale") else float(scale)
        except Exception:
            gdk_scale = float(scale)
        p = self.phys_model.current
        return {
            "surf_w": int(layout.width * gdk_scale),
            "surf_h": int(layout.height * gdk_scale),
            "gdk_scale": gdk_scale,
            "fps": self._fps,
            "frame_ms": self._frame_ms,
            "sim_ms": self._sim_ms,
            "draw_ms": self._draw_ms,
            "sim_time": self.org.time,
            "frames": self.frames,
            "resizes": self.resizes,
            "rebuilds": self.rebuilds,
            "ag": p.agitation, "pu": p.pulse, "de": p.density,
            "notes": self.telemetry.notes or "(probing)",
        }

    # --------------------------------------------------------------- QA probe
    def _log_probe(self, layout, vp, event: str) -> None:
        """Append one JSON line per geometry event, for the torture harness.

        Chiefly: `sim_time` must increase monotonically across every resize.
        """
        if not self._probe:
            return
        self._probe.write(json.dumps({
            "event": event,
            "wall": time.time(),
            "w": layout.width, "h": layout.height,
            "state": layout.state.value,
            "stage": [vp.stage_w, vp.stage_h],
            "scale": vp.scale,
            "iso_err": isotropy_error(vp),
            "clips": vp.clips,
            "sim_time": self.org.time,
            "frames": self.frames,
            "resizes": self.resizes,
            "rebuilds": self.rebuilds,
            "fps": self._fps,
        }) + "\n")


class AbyssalApp(Gtk.Application):
    def __init__(self, opts: argparse.Namespace) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.opts = opts

    def do_activate(self) -> None:
        win = Monitor(self, self.opts)
        win.present()
        if self.opts.quit_after:
            GLib.timeout_add(int(self.opts.quit_after * 1000), self._bye, win)

    def _bye(self, win) -> bool:
        win.close()
        self.quit()
        return GLib.SOURCE_REMOVE


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="abyssal-organism-monitor")
    ap.add_argument("--width", type=int, default=900)
    ap.add_argument("--height", type=int, default=700)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--debug", action="store_true", help="start with F1 overlay on")
    ap.add_argument("--calibration", action="store_true",
                    help="start with F2 geometry calibration on")
    ap.add_argument("--probe", metavar="FILE",
                    help="append JSONL geometry events (QA harness)")
    ap.add_argument("--quit-after", type=float, default=0.0,
                    help="seconds, for automated runs")
    opts = ap.parse_args(argv if argv is not None else sys.argv[1:])
    return AbyssalApp(opts).run([])


if __name__ == "__main__":
    raise SystemExit(main())
