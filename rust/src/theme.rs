//! Abyssal palette. Near-black field, pale cyan / silver-blue instrument ink.
//! Port of `core/theme.py`.

pub type RGB = (f64, f64, f64);

const fn h(hex: u32) -> RGB {
    (
        ((hex >> 16) & 0xff) as f64 / 255.0,
        ((hex >> 8) & 0xff) as f64 / 255.0,
        (hex & 0xff) as f64 / 255.0,
    )
}

// Field
pub const ABYSS: RGB = h(0x05070b); // page background
pub const ABYSS_DEEP: RGB = h(0x030407);
pub const FRAME: RGB = h(0x0d131c); // dark blue-black framing
pub const RULE: RGB = h(0x17202c); // hairlines

// Ink, in four ranks. The rank carries the meaning, so a label's importance
// is legible before it is read - and so "dim" never has to mean "guess".
pub const INK_DIM: RGB = h(0x4a5c6e); // tertiary / dormant
pub const INK_TECH: RGB = h(0x8ab4cc); // secondary technical microtype
pub const INK: RGB = h(0x8fa6bd); // body text
pub const INK_BRIGHT: RGB = h(0xd6e6f2); // headings / values
pub const CYAN: RGB = h(0x7fd4e8); // organism core accent
pub const CYAN_DEEP: RGB = h(0x2d6d86); // organism falloff
pub const SILVER: RGB = h(0xa9c4d6); // filament silver-blue
pub const LIME: RGB = h(0xb8e04a); // sparing status accent
pub const AMBER: RGB = h(0xe0a54a); // sparing warning accent

#[inline]
pub fn rgba(c: RGB, a: f64) -> (f64, f64, f64, f64) {
    (c.0, c.1, c.2, a)
}

pub const FONT_MONO: &str = "JetBrainsMono Nerd Font";
pub const FONT_MONO_FALLBACK: &str = "Noto Sans Mono";

// Display faces, shipped in assets/fonts and installed by ui::fonts.
//   FONT_DISPLAY  Astro        major titles
//   FONT_TECH     Microgramma  technical/support type. Never numerics.
pub const FONT_DISPLAY: &str = "Astro";
pub const FONT_TECH: &str = "Microgramma";
