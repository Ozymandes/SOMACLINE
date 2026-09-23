# Licensing — unresolved, and blocking publication

Two things must be settled by a human before this repository is made public.
Neither is a code problem and neither was decided here.

## 1. There is no project licence

There is no `LICENSE` file and `rust/Cargo.toml` declares no `license` field
(the key is present but commented out, pointing here). Without one, the
default is "all rights reserved": nobody may legally copy, modify or
redistribute the work, which is usually not what publishing intends.

**Action:** choose a licence, add `LICENSE` at the repository root, and set
`license = "..."` in `rust/Cargo.toml`.

## 2. The two bundled typefaces have no stated provenance

`assets/fonts/` ships two TrueType files, and the application **installs them
into the user's font directory** (`~/.local/share/fonts/abyssal/`) and runs
`fc-cache` on first launch — `rust/src/ui/fonts.rs`. That is redistribution,
and redistribution needs a licence that permits it.

| file | size | family used as | provenance |
|---|---:|---|---|
| `astro.ttf` | 10,608 B | `Astro` — hero type | not documented anywhere in this repository |
| `microgrammanormal.ttf` | 80,644 B | `Microgramma` — technical type | not documented anywhere in this repository |

**Microgramma is a commercial typeface** (Aldo Novarese and Alessandro Butti,
1952; rights held by Monotype/Linotype). A file named `microgrammanormal.ttf`
is the characteristic shape of a free-download-site redistribution of it. If
that is what this is, shipping it in a public repository is a copyright
problem regardless of how the rest of the project is licensed.

**Action, one of:**

- **Verify and document.** If a licence permitting redistribution exists for
  both files, add it under `assets/fonts/` and record it here.
- **Substitute.** Replace with libre faces of similar character and re-check
  parity. This changes the typography, so it changes the product's look — it
  is a design decision, not a packaging one.
- **Stop bundling.** Remove the files and let `ui/fonts.rs` fall through to
  its existing fallback. The code already fails soft: the console runs on its
  mono fallback if the faces are absent. The machine will not look the same.

The code path is deliberately untouched so that whichever route is chosen
works without further changes: `ensure_user_fonts` installs whatever is in
`assets/fonts/` and does nothing at all if the directory is empty.

## Not blocking, but worth a line in the README

- The five specimens are ports of published generative sketches. Their
  equations are reproduced verbatim in `rust/src/sources.rs`, with the
  original source text kept alongside each port. Attribution for those
  sketches belongs in the acknowledgements.
- The hardware sprite set was generated with an image model; the generation
  log is in `assets/modules/GENERATIONS.md`.
