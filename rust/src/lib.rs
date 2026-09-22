//! Abyssal Organism Monitor - native Rust port of the Python console.
//!
//! Module map mirrors the Python package:
//!   signals, physiology      <- core/signals.py, core/physiology.py
//!   telemetry/*              <- telemetry/*
//!   world, viewport, layout, theme, lighting <- core/*
//!   sources, mathforms, species, pointfield, render <- organism/*
//!   skin/*                   <- skin/*
//!   ui/*                     <- ui/*
//!   app                      <- app.py (the GTK host)

pub mod app;
pub mod host;
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
