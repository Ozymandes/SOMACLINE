//! Abyssal Organism Monitor - native Rust port of the Python console.
//!
//! Module map mirrors the Python package:
//!   signals, physiology      <- core/signals.py, core/physiology.py
//!   telemetry/*              <- telemetry/*
//!   world, viewport, layout, theme, lighting <- core/*
//!   sources, mathforms, species, pointfield, render <- organism/*
//!   skin/*                   <- skin/*
//!   ui/*                     <- ui/*
//!   host                     <- app.py, minus the toolkit (the machine)
//!   app                      <- app.py's GTK adaptation (reference host)
//!   present, winit_host      <- the winit + softbuffer host

#[cfg(feature = "gtk-host")]
pub mod app;
pub mod host;
pub mod present;
#[cfg(feature = "winit-host")]
pub mod winit_host;
pub mod layout;
pub mod lighting;
pub mod mathforms;
pub mod physiology;
pub mod pointfield;
pub mod render;
pub mod signals;
pub mod species;
pub mod sources;
pub mod telemetry;
pub mod theme;
pub mod ui;
pub mod viewport;
pub mod world;
pub mod skin;
