"""Abyssal palette. Near-black field, pale cyan / silver-blue instrument ink."""

RGB = tuple[float, float, float]


def _h(s: str) -> RGB:
    s = s.lstrip("#")
    return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0)


# Field
ABYSS        = _h("05070b")   # page background
ABYSS_DEEP   = _h("03040 7".replace(" ", ""))
FRAME        = _h("0d131c")   # dark blue-black framing
RULE         = _h("17202c")   # hairlines

# Ink
INK_DIM      = _h("4a5c६e".replace("६", "6"))
INK          = _h("8fa6bd")   # body text
INK_BRIGHT   = _h("d6e6f2")   # headings / values
CYAN         = _h("7fd4e8")   # organism core accent
CYAN_DEEP    = _h("2d6d86")   # organism falloff
SILVER       = _h("a9c4d6")   # filament silver-blue
LIME         = _h("b8e04a")   # sparing status accent
AMBER        = _h("e0a54a")   # sparing warning accent


def rgba(c: RGB, a: float) -> tuple[float, float, float, float]:
    return (c[0], c[1], c[2], a)


FONT_MONO = "JetBrainsMono Nerd Font"
FONT_MONO_FALLBACK = "Noto Sans Mono"
