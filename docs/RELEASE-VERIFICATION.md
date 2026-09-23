# Release verification — SOMACLINE v1.0.0 candidate

Everything below was run against the shipping build on this machine
(Arch/Omarchy, Hyprland 0.56.2, 2560×1600 @ 120 Hz, display scale 1.6,
16 cores). Ranges are the spread over repeated runs, not a single sample.

## Build

| | |
|---|---|
| clean build | `cargo clean` removed 10 207 files / 8.1 GiB, then a full release build of the winit host in **45.7 s** |
| binary | `somacline`, **2.58 MB**, stripped, LTO, `panic = "abort"` |
| warnings | **0** in the library, both feature sets |

## Tests

| suite | result |
|---|---|
| `cargo test --release --no-default-features --features winit-host` | **49 passed, 0 failed** |
| `cargo test --release` (the GTK reference) | **46 passed, 0 failed** |

Five of those gates are byte-for-byte assertions that the optimised
composition equals the straightforward one, and one — added in this pass — is
the specimen-switch flicker gate described below.

## Visual parity against the Python reference

`examples/compose` drives the real composition path over the same
deterministic fixture as `qa/offscreen.py`.

| size | ds | state | mean abs | p99 | within 12 | exact |
|---|---|---|---:|---:|---:|---:|
| 900×700 | 1.0 | INSTRUMENT | 0.0234/255 | 0 | 99.983 % | 99.966 % |
| 900×700 | 1.5 | INSTRUMENT | 0.0392 | 0 | 99.974 % | 99.952 % |
| 900×700 | 2.0 | INSTRUMENT | 0.0080 | 0 | 99.995 % | 99.988 % |
| 1400×880 | 1.5 | ARCHIVE | 0.0433 | 0 | 99.974 % | 99.951 % |
| 600×520 | 1.5 | COMPACT | 0.0143 | 0 | 99.989 % | 99.975 % |
| 1000×420 | 1.25 | COMPACT wide | 0.0105 | 0 | 99.992 % | 99.979 % |

The residual is the header clock and nothing else. On the worst fixture the
four 32×32 tiles that exceed tolerance are all at (1856–1888, 96–128) — the
clock band — and **two runs of the Python reference itself differ there by
more** (six tiles, mean 0.0527 against 0.0433).

## Performance

781×468 logical (1250×749 device), INSTRUMENT, specimen 0, `rebuilds = 0`,
via `qa/perf_focused.py`, which asserts the window is focused before and after
the dwell and rejects any sample not at the focused cadence.

| | |
|---|---:|
| PSS | **42.2 – 45.6 MB** |
| RSS | **53.7 – 56.8 MB** |
| anonymous | **29.6 – 32.7 MB** |
| CPU, focused | **14.75 – 16.3 %** |
| CPU, unfocused visible (30 FPS) | **9.5 – 12.25 %** |
| CPU, covered by a window | **0.25 %** |
| CPU, off-workspace | **0.12 – 0.25 %** |
| FPS | **60.0** |
| draw, windowed mean | **1.11 – 1.42 ms** |
| threads | **2** |
| startup to mapped window | **0.21 s** |

## Interaction — `qa/release_interaction.py`

**13/13.** Every documented control driven against the real window and read
back off the screen.

    PASS  digits 1-5 select five DISTINCT specimens   min pairwise title diff 212
    PASS  n advances the specimen
    PASS  p steps back
    PASS  Right advances the specimen
    PASS  Left steps back
    PASS  m cycles the mode key
    PASS  F1 toggles the diagnostic overlay on and off
    PASS  F2 toggles calibration geometry on and off
    PASS  clicking an engraved selector key selects that specimen
    PASS  F11 enters fullscreen
    PASS  F11 leaves fullscreen
    PASS  q quits                                     exit code 0
    PASS  silent on stdout and stderr                 0 / 0 bytes

## The flicker, found and fixed in this pass

Reported from real use: switching specimen made the engraved name in the
header flicker between the new specimen and the previous one. No screenshot
showed it, because every individual frame was internally correct.

A frame that rebuilt the static picture recorded only the glass and the
changed regions as its dirty set. When `under`/`over` change, every pixel
changed, and the understatement was paid one frame later in the other pooled
buffer — which, at age 2, still held the frame from before the switch.

| | without the fix | with the fix |
|---|---:|---:|
| unit gate, frame 7 after a switch | 38 514 of 3 745 000 bytes differ | **0** |
| live: 15 switches × 6 grabs of the name at 100 ms | **14 of 15 disagreed** (worst diff 215) | **0 of 15** (worst diff 0) |

The pre-existing bit-identity gate passes either way — it never switched
specimen, which is exactly the blind spot. The new gate
`switching_specimen_does_not_leave_the_old_chrome_in_the_other_buffer` does.

## Soak — `qa/release_soak.py`

Each round: all five specimens by key, cycle and mode, a fullscreen round
trip, float plus three sizes, untile, and a wait past the residency review.

| run | rounds | PSS across rounds | verdict |
|---|---:|---|---|
| first | 6 | 80.5 → 82.9 MB | stable |
| second | 3 | 78.6 → 88.7 → 82.9 MB | stable |

Threads stay at 2. Zero bytes reach stdout or stderr. Twelve open file
descriptors, of which the only anonymous ones are `memfd:softbuffer` ×2 and
`memfd:smithay-client-toolkit` — shared memory, not files on disk.

Read those numbers against the window size. Surfaces scale with window area,
so a round that ends tiled larger legitimately costs more. Held at one size,
an off-workspace round trip is flat: **44.5 → 45.4 → 45.7 MB**.

## Launch and quit

Ten consecutive `--quit-after` runs: **all exit 0**, **0 bytes on stdout**,
**0 bytes on stderr** in total.

## Linkage

    no libgtk / libgdk / libgsk / libgraphene
    shared objects: 40
    binary: 2.58 MB

`libglib` is present: pango and cairo need it, and it does not bring GTK.

## Layouts and scaling

COMPACT (600×520), INSTRUMENT (900×700) and ARCHIVE (1400×880) captured live
at display scale 1.6, each more than `SETTLE_S` after its resize so the frame
is drawn with the sprite masters released and re-decoded. All three correct.
Fullscreen 1600×1000 logical / 2560×1600 device round-trips correctly.

## The packaged run path

`./install.sh --prefix <scratch> --no-build`, then the repository's `assets/`
directory **renamed away**, then the installed binary run from `/tmp`:

- window mapped, class `dev.abyssal.OrganismMonitor`, 900×700;
- full typography, full sprite set, selector bank, 60 FPS;
- 0 bytes on stdout and stderr.

`--uninstall` removes everything it installed. Two files survive —
`mimeinfo.cache` and `icon-theme.cache` — which are generated by
`update-desktop-database` and `gtk-update-icon-cache` and belong to the prefix,
not to this application. `DESTDIR=… --prefix /usr` stages cleanly: five files
plus 74 asset files, 23 MB.

## Brand integration and history cleanup

Run after the verification above, then re-verified from a fresh clone.

### Identity

`dev.somacline.Somacline` / window title `Somacline` / binary `somacline` /
assets at `<prefix>/share/somacline/`. The Rust crate stays `abyssal`
internally. The engraved faceplate still carries the instrument's model
designation, `ABYSSAL ORGANISM MONITOR` — see the note at the end.

### Icon

Extracted from `docs/brand/somacline-logo.png`, which holds six variants. The
app-icon badge is the standalone mark in the top right, taken by its own alpha
bounding box at (1267, 85)–(1590, 407) — 324×324 native, transparent — squared
and resampled with Lanczos to 256/128/64/48/32, installed into hicolor.

### Typefaces, verified both ways

| state | result |
|---|---|
| **neither face anywhere** — repository copy moved aside, `~/.local/share/fonts` cleared, `fc-cache -f`, `fc-list` finding 0 | launches, renders completely on fontconfig substitutions: every module composes, nothing overflows, no missing glyphs, 60 FPS, **0 bytes on stderr** |
| **operator supplies them** in `assets/fonts/` | registered on launch, `fc-list` finds 2, header renders in the display faces (header strip differs by 214 from the fallback render) |

No font file is in the repository, in any commit at HEAD, or in any install:
`find <prefix> -iname '*.ttf' -o -iname '*.otf'` → **0**.

### History cleanup

Backed up first: `git bundle create --all` → `~/somacline-pre-rewrite/`
(437 MB, `git bundle verify` reports "records a complete history", all three
refs), with `refs-before.txt` and `HEAD-before.txt` beside it.

The plan was `rust/floor/target` only. Auditing the blob weight first showed
`rust/target/` had *also* been committed early on — a 105 MB `libgtk4` rlib
among others — before it was gitignored in `e4d50e1`. Removing only the first
would have left 598 MB of build output in history, so both went:

    git filter-branch --index-filter         'git rm -r --cached --ignore-unmatch rust/target rust/floor/target'         --tag-name-filter cat -- --all

| | before | after |
|---|---:|---:|
| blobs under either build dir, all history | 996.2 MB | **0** |
| `.git` | 439 MB | **181 MB** |
| fresh clone, total | — | **276 MB** (181 MB `.git` + 88.5 MB tree) |

Every one of the five affected commits carries source as well, so none became
empty and the history shape is unchanged: 48 commits on the branch, 33 on
master, 15 ahead. The tag `pre-convergence` was reported "unchanged" — its
commit predates the artifacts, carries none, and is still an ancestor of both
branches, so it remains valid.

What remains in history is real content: `docs` 125.5 MB (screenshots
regenerated across the project's life), `assets` 47.0 MB, `rust` 9.1 MB.

### Re-verified from a fresh clone

`git clone` of the rewritten repository into a scratch directory, then:

| | |
|---|---|
| clean release build | **42.3 s**, binary 2.58 MB |
| tests | **49 passed, 0 failed** |
| `./install.sh --prefix <scratch> --no-build` | 5 files + 72 assets, 21 MB, **0 font files** |
| installed binary, run from `/tmp` | window `dev.somacline.Somacline` / `Somacline`, full instrument, 61 FPS, **0 bytes on stdout and stderr** |
| `--uninstall` | clean |

## Known and not fixed

**A pre-existing compositor artifact.** Under Hyprland 0.56.2 at fractional
scale, a capture taken shortly after a float/resize/move sequence can come
back with faint pixels of the window behind ours on the chassis. It was
suspected to be the dirty-region work and it is not: builds predating all of
it reproduce the artifact in the same places, in the same sequence, and
presenting the whole surface does not heal it. Captured side by side. It is
outside this process.

**A resize hitch.** A resize after four seconds of stillness costs one ~161 ms
frame instead of ~56 ms, because the sprite masters are decoded again. This is
the deliberate price of handing ~30 MB back while the window sits idle.

**The faceplate is not rebranded.** The instrument's engraved header reads
ABYSSAL ORGANISM MONITOR, its model designation — the footer rail already
carries the model number AOM-1 and the serial AQS-0042. Changing it is six
string literals, three in Rust (`ui/chrome.rs:43`, `ui/console.rs:1065`,
`ui/console.rs:1254`) and three in the Python oracle (`ui/chrome.py:67`,
`ui/console.py:683`, `ui/console.py:840`). They must move together or the
parity fixtures break in the header band; no golden fixture contains the
string, so nothing would need regenerating. It is a rendered-pixel change that
was not requested, so it was not made.

**Injection flakiness in the harness, not the app.** `wtype` drops roughly two
key presses in five on this compositor. `qa/release_interaction.py` retries
until the window reacts and reports how many presses each control needed; the
counts are the injector's, not the application's.
