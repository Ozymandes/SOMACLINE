# Licensing

## The source code — MIT

Everything in `rust/`, `abyssal/`, `qa/`, `tools/`, `install.sh` and the
documentation is MIT licensed. See [`LICENSE`](LICENSE).

> The copyright line reads "The Somacline authors". Replace it with your legal
> name or entity before you publish if you want the attribution to be personal.

MIT is compatible with everything Somacline depends on. The Rust crates
(`winit`, `softbuffer`, `cairo-rs`, `pango`, `pangocairo`, `glib`, and the
optional `gtk4`/`gdk4`/`gio` used only by the development reference build) are
MIT or MIT/Apache-2.0. The system libraries the binary links against — cairo,
pango, fontconfig, freetype, harfbuzz, glib — are LGPL or similar, and are
**dynamically** linked, which MIT source does not conflict with.

## What MIT does NOT cover

MIT covers the code. It does not, and cannot, relicense anything third-party
that the project merely uses or generates from.

### Typefaces — not distributed at all

**No font files are in this repository or in any release package.**

The instrument's typography is designed around two third-party faces:

| family | role | where to get it |
|---|---|---|
| **Microgramma** | technical type: labels, rails, microcopy | <https://online-fonts.com/fonts/microgramma> |
| **Astro** | hero type: major titles | <https://www.dafont.com/astro.font> |

Install either one from a source and under a licence appropriate to your own
use. Microgramma is a commercial typeface with a long history of unlicensed
redistribution; the link above is a convenience for finding it, not a statement
that the download there is free or licensed for your purposes — check for
yourself. The DaFont page for Astro currently lists it as 100% free.

Somacline runs without both of them. See README, *Optional typography*.

`.gitignore` excludes `assets/fonts/*.ttf` so a local copy cannot be committed
by accident. `rust/src/ui/fonts.rs` registers whatever the operator has put
there, on the operator's own machine, and does nothing when the directory is
empty.

### Generated assets

The hardware sprite set in `assets/sprites/` and the design sources under
`assets/modules/` and `assets/hardware_v2/` were produced with an image
generation model against fixed references. The generation log — every prompt,
setting and reference — is in `assets/modules/GENERATIONS.md`. Whatever terms
attach to that model's output attach to these files; they are not covered by
the MIT grant above.

### Brand

`docs/brand/` and `packaging/icons/` contain the SOMACLINE wordmark, logo and
banner. Trade marks are not licensed by MIT. Forks should replace them rather
than ship them.

### The organisms

The five specimens are ports of published generative sketches. Each source is
reproduced verbatim alongside its port in `rust/src/sources.rs`, and the
mathematics is credited in the README acknowledgements. The ports are MIT; the
original sketches belong to their authors.
