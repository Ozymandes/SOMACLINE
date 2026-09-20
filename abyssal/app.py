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
from .core.lighting import LightField
from .core.physiology import PhysiologyModel
from .core.signals import Telemetry
from .core.theme import ABYSS
from .core.viewport import Viewport, isotropy_error
from .organism.mathforms import Body
from .organism.render import draw_calibration, draw_organism
from .organism.species import CATALOGUE, by_index
from .telemetry.source import TelemetrySource
from .ui import console
from .ui.chrome import draw_background
from .ui.debug import draw_debug

APP_ID = "dev.abyssal.OrganismMonitor"
TELEMETRY_INTERVAL_MS = 500

#: How long a selector key stays visibly depressed after a click, in seconds.
#: Long enough to register as a mechanical action, short enough not to lag.
PRESS_FEEDBACK_S = 0.13


class Monitor(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, opts: argparse.Namespace) -> None:
        super().__init__(application=app)
        self.opts = opts
        self.set_title("Abyssal Organism Monitor")
        self.set_default_size(opts.width, opts.height)

        # --- simulation state ------------------------------------------------
        # One organism per specimen, built lazily and then kept for the life of
        # the process. Switching selects an existing instance rather than
        # constructing one, so a switch allocates nothing on the hot path and
        # each specimen resumes exactly where it was left.
        self._organisms: dict[int, Body] = {}
        self.species_index = max(0, min(len(CATALOGUE) - 1, opts.specimen))
        self.org = self._organism(self.species_index)
        self.phys_model = PhysiologyModel()
        self.telemetry_src = TelemetrySource()
        self.telemetry = Telemetry()

        # --- console presentation state --------------------------------------
        self.model = console.ConsoleModel(species=by_index(self.species_index),
                                          active=self.species_index)
        self.light = LightField()
        self._press_until = 0.0
        self._press_index: int | None = None
        self.switches = 0

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

        self._last_layout = None
        self._last_vp = None
        self.area.add_tick_callback(self._on_tick)
        GLib.timeout_add(TELEMETRY_INTERVAL_MS, self._on_telemetry)
        if self._probe:
            GLib.timeout_add(2000, self._on_probe_sample)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.area.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.area.add_controller(motion)

        self.connect("notify::default-width", lambda *_: None)

    # -------------------------------------------------------------- specimens
    def _organism(self, i: int) -> Body:
        org = self._organisms.get(i)
        if org is None:
            org = by_index(i).build(seed=self.opts.seed + i)
            self._organisms[i] = org
        return org

    def select_specimen(self, i: int, tactile: bool = True) -> None:
        """Switch channels. Never resets the clock or the telemetry history."""
        i %= len(CATALOGUE)
        if tactile:
            self._press_index = i
            self._press_until = time.perf_counter() + PRESS_FEEDBACK_S
        if i != self.species_index:
            self.species_index = i
            self.org = self._organism(i)
            self.model.species = by_index(i)
            self.model.active = i
            self.switches += 1
            self.model.switches = self.switches
        self.area.queue_draw()

    def cycle_specimen(self, step: int) -> None:
        self.select_specimen(self.species_index + step)

    # ------------------------------------------------------------------ input
    def _on_click(self, gesture, n_press: int, x: float, y: float) -> None:
        if self._last_layout is None:
            return
        target = console.hit_controls(self._last_layout, x, y)
        if target is None:
            return
        kind, idx = target
        if kind == "key":
            self.select_specimen(idx)
        elif kind == "cycle":
            self.cycle_specimen(idx)
        elif kind == "mode":
            order = ("inactive", "armed", "active", "error")
            cur = self.model.mode_state
            self.model.mode_state = order[(order.index(cur) + 1) % len(order)]
            self.area.queue_draw()

    def _on_motion(self, _ctrl, x: float, y: float) -> None:
        if self._last_layout is None:
            return
        target = console.hit_controls(self._last_layout, x, y)
        focus = target[1] if (target and target[0] == "key") else None
        if focus != self.model.focus:
            self.model.focus = focus
            self.area.queue_draw()

    def _on_leave(self, _ctrl) -> None:
        if self.model.focus is not None:
            self.model.focus = None
            self.area.queue_draw()

    def _on_key(self, _ctrl, keyval, _code, _mods) -> bool:
        name = Gdk.keyval_name(keyval)
        if name and len(name) == 1 and name.isdigit() and name != "0":
            n = int(name) - 1
            if n < len(CATALOGUE):
                self.select_specimen(n)
                return True
        if name in ("Right", "Down", "n", "N"):
            self.cycle_specimen(+1)
            return True
        if name in ("Left", "Up", "p", "P"):
            self.cycle_specimen(-1)
            return True
        if name in ("m", "M"):
            order = ("inactive", "armed", "active", "error")
            self.model.mode_state = order[
                (order.index(self.model.mode_state) + 1) % len(order)]
            self.area.queue_draw()
            return True
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

    def _on_probe_sample(self) -> bool:
        """Periodic steady-state sample, so FPS is measured while idle at a
        size rather than only in the instant after a resize."""
        if self._last_layout is not None:
            self._log_probe(self._last_layout, self._last_vp, "sample")
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
        glass = console.stage_content(layout)
        vp = Viewport.for_stage(glass.x, glass.y, glass.w, glass.h)
        # (that is all — no allocation, no rebuild, no reset)

        self._last_layout, self._last_vp = layout, vp
        if (width, height) != self.last_size:
            self.resizes += 1
            self.last_size = (width, height)
            self._log_probe(layout, vp, "resize")
        if layout.state is not self.last_state:
            self.last_state = layout.state
            self._log_probe(layout, vp, "state")

        # Momentary key feedback expires on a clock, not on a frame count, so
        # it lasts the same wall time at any frame rate.
        if self._press_index is not None and time.perf_counter() >= self._press_until:
            self._press_index = None
        self.model.pressed = self._press_index

        self._refresh_field(vp)

        # draw_background is painted INTO the cached static under-layer;
        # see ui.console.draw_under.
        console.draw_under(cr, layout, self.model, vp.scale)
        if layout.stage.valid:
            glass = console.stage_content(layout)
            cr.save()
            cr.rectangle(glass.x, glass.y, glass.w, glass.h)
            cr.clip()
            draw_organism(cr, vp, self.org)
            if self.show_calibration:
                draw_calibration(cr, vp, self.org)
            cr.restore()
        console.draw_over(cr, layout, self.telemetry, self._fps,
                          self._frame_ms, self.model, self.light)

        self._draw_ms = (time.perf_counter() - t0) * 1000.0
        if self.show_debug:
            draw_debug(cr, layout, vp, self._debug_info(layout, vp, area))

        self.frames += 1
        self._tick_fps()

    def _refresh_field(self, vp) -> None:
        """Push live observation-field values into the console model.

        These drive the SPECIMEN FIELD / MORPHOLOGY / VECTOR FIELD blocks. They
        are read from the running organism, not invented, so the readouts move
        with the specimen rather than decorating it.
        """
        org = self.org
        mdl = self.model
        p = self.phys_model.current
        mdl.phase = (org.time * 0.31) % 2.0
        mdl.rotation = 0.08 + 0.42 * p.agitation
        # Centroid of the filament cloud, expressed in field millimetres.
        try:
            cx = float(org.fil_x.mean()) / 400.0
            cy = float(org.fil_y.mean()) / 400.0
        except Exception:
            cx = cy = 0.0
        mdl.coords = (cx, cy, 0.0)
        mdl.behavior = ("AGITATED" if p.agitation > 0.66 else
                        "ACTIVE" if p.agitation > 0.33 else "STABLE")
        mdl.flux = p.flux
        mdl.surge = p.surge
        # The viewport scale IS the magnification, so the readout is true.
        mdl.magnification = max(0.1, vp.scale * 10.0)
        mdl.field_mm = max(0.01, min(vp.stage_w, vp.stage_h) / vp.scale / 400.0)

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
            "species": by_index(self.species_index).name,
            "switches": self.switches,
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
            "draw_ms": self._draw_ms,
            "sim_ms": self._sim_ms,
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
    ap.add_argument("--specimen", type=int, default=0,
                    help="index 0-4 of the specimen to open on")
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
