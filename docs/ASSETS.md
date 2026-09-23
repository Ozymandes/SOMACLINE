# Assets: what ships, what does not

The runtime payload is **21 MB**: the sprite set the console is manufactured
from, and the two display faces. Everything else under `assets/` is the
material those were generated from, and it stays in the repository for
reproducibility without going anywhere near an install.

`install.sh` copies exactly `assets/sprites` and `assets/fonts` into
`<prefix>/share/abyssal/assets/`. Nothing else is installed.

## Ships

| path | size | what it is |
|---|---:|---|
| `assets/sprites/` | 20.15 MB, 71 PNG + 1 JSON | the hardware skin: frames, modules, lamps, mode and cycle keys, selector cells, specimen keys, parts |
| `assets/fonts/` | 0.09 MB, 2 TTF | Astro (hero type) and Microgramma (technical type) — **see `LICENSING.md` before publishing** |

### The sprite set, by family

| family | files | referenced from |
|---|---:|---|
| `frame/` | 14 | `skin::catalog` nine-slices: bezels, housings, wells, troughs, rails, fascia |
| `module/` | 6 PNG + `build.json` | the six hardware modules the console is assembled from |
| `lamp/` | 12 | `lamp/{primary,small}_{off,standby,nominal,active,warning,critical}` |
| `selector/` | 8 | the bank, its caps, and the five cell states |
| `specimen/` | 10 | `specimen/key_{01..05}_{active,inactive}` |
| `part/` | 12 | screws, rivets, knobs, rockers, tabs, brackets |
| `mode/` | 4 | `mode/{inactive,armed,active,error}` |
| `cycle/` | 3 | `cycle/{prev,neutral,next}` |
| `kit/` | 2 | control and fastener kits |

Five sprites (`frame/chassis`, `frame/plate_wide`, `frame/plaque_kit`,
`kit/controls`, `kit/fasteners`) and the twelve in `part/` are not referenced
by name from the Rust source today. They are **kept and shipped anyway**:
together they are 3.3 MB of a 20 MB payload, a missing sprite fails soft and
silently rather than loudly, and trimming a texture set on a static grep is a
poor trade against a silent visual defect. If the payload ever needs to
shrink, that is where to look — with a runtime trace, not a grep.

## Does not ship

| path | size on disk | what it is |
|---|---:|---|
| `assets/modules/masters/` | 34 MB | the six accepted 2K module generations — the source `tools/build_sprites.py` cuts the runtime sprites from |
| `assets/modules/{GENERATIONS,INVENTORY}.md` | 7 KB | the generation log: every prompt, setting and reference |
| `assets/hardware_v2/` | 177 MB | the hardware generation archive: 2K masters, rejected candidates, contact sheet. Mostly untracked by git already |
| `references/` | 3.3 MB | the two reference images the console was matched to |
| `docs/shots/` | 20 MB | product screenshots and reference comparisons |
| `abyssal/` | 0.3 MB | the Python reference implementation — the parity oracle, not a second product |
| `qa/`, `tools/` | 0.2 MB | harnesses and the asset build pipeline |
| `rust/tests/golden/` | 5.9 MB | golden fixture dumps for the parity gates |
| `rust/floor/` | small | the framework-floor probe cited by `docs/rust-migration/framework-floor.md` |

## How the runtime finds them

`skin::asset_roots` probes, in order:

1. `$ABYSSAL_ASSETS/<kind>` — an outright override, for anything unusual;
2. `$CARGO_MANIFEST_DIR/../assets/<kind>` — running from a checkout via cargo;
3. `<exe>/../share/abyssal/assets/<kind>` — installed at `<prefix>/bin`;
4. `<exe>/../../share/abyssal/assets/<kind>` — installed at `<prefix>/lib/abyssal`;
5. `<exe>/../../../assets/<kind>` and one level further — `rust/target/release`;
6. `/usr/share/abyssal/assets/<kind>`;
7. `assets/<kind>` relative to the working directory.

The first candidate that actually holds the asset wins — it probes rather than
assumes. A missing asset is never fatal: the console runs on its fallbacks.

Verified: with the repository's `assets/` directory renamed away, a binary
installed into a scratch prefix rendered the full console — typography,
sprites, selector bank, 60 FPS — from `<prefix>/share/abyssal/assets`.
