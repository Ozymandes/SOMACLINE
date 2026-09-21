# Six-module hardware — Higgsfield generation ledger

Final production pass. Budget ceiling 80 credits; **spent 45** (balance 267 → 222,
measured before and after). Every generation is listed; nothing was generated
outside this table.

## Settings (identical for every generation)

| setting | value |
|---|---|
| model | `gpt_image_2_5` (GPT Image 2.5), variant `flare` |
| resolution | `2k` |
| quality | `xhigh` (5 credits; `high` was 3) |
| background | `transparent` (true alpha, verified per file) |
| count | 1 |

## Reference media attached

| media id | file |
|---|---|
| `6d3748e0-39ba-460d-afbd-e41ca5c7b18b` | `references/Abbysal_Final.png` — appearance |
| `39929dbc-eef9-47ed-ad62-5b0d1f0d8226` | `references/Abbysal_Module_Map.png` — decomposition |
| `ef96b1d5-1f6e-4ed2-ad7f-fa45acd3050a` | `assets/sprites/specimen/key_01_inactive.png` (approved key) |
| `367f07c8-0dab-49df-843e-be6f1b64dc42` | `assets/sprites/cycle/neutral.png` (approved rocker) |
| `0b0b99e7-7eef-437a-abbd-12f12fd15329` | `assets/sprites/mode/armed.png` (approved mode key) |

Both authoritative references were attached to **every** generation. The three
approved parts were added to the selector generations only, so the housing was
designed around the real parts.

## Generations

| # | job id | module | aspect | refs | verdict | reason |
|---|---|---|---|---|---|---|
| 1 | `da7fba92-8f35-4bcb-894f-41194ff26bfa` | 01 shell | 4:3 | Final, Map | **accepted** | clean alpha, empty face, lugs match; frame drawn at reduced scale |
| 2 | `57d0aa67-b340-44a9-a6f5-6f835774abe6` | 02 header | 21:9 | Final, Map | **accepted** | 4+3 blank bays, empty lamp socket, 4 corner screws |
| 3 | `bab1a006-db68-4e59-a296-60d95e4fa01e` | 03 observation | 4:3 | Final, Map | **accepted** | TRUE transparent aperture 1803×1203 (alpha 0) |
| 4 | `b31bf04d-6c71-434e-a0be-86d326ef5158` | 04 rack | 3:4 | Final, Map | **accepted** | one body, 4 identical rows, all wells blank |
| 5 | `989716f1-2deb-4c46-9c0f-9a3fb7ce2b23` | 05 selector | 21:9 | Final, Map, key, rocker, mode | rejected | wells 1.08:1 (key is 0.96), pitch 366–377, wells 47% of height, wrong end openings, no ledge |
| 6 | `4de1982d-3a3a-4a14-8293-2fdbbc5a276d` | 05 selector | 21:9 | Final, Map, key, rocker, mode | rejected | over-corrected: wells 0.66:1, first well wider, first pitch 401 vs 375 |
| 7 | `b0f6efd7-d3dc-4c85-a314-188a1d36ee73` | 06 footer | 21:9 | Final, Map | **accepted** | 7 bays + terminal, cast badge, blank |
| 8 | `7e1cd992-2b13-427e-b9c4-03b8e2658dd7` | 05 selector | 21:9 | Final, Map, key, rocker, mode | superseded | geometrically usable, but a light plate with small keys; read as unlike the reference's dark trough in the assembled comparison |
| 9 | `e6161f75-64a6-4e72-ae07-3cc44ffb5146` | 05 selector | 21:9 | Final, Map, key, rocker, mode | **accepted** | dark trough as reference, wells 248×258 (= key aspect), sizes within 2 px |

Accepted masters: `masters/`. Rejected/superseded: `rejected/` (on disk, not in git).

## Deterministic post-processing (tools/build_modules.py)

1. alpha contrast stretch (≤8 → 0, ≥248 → 255; edges keep their ramp)
2. crop to the opaque body
3. selector only: the five wells re-cut to one identical rectangle on a
   least-squares uniform pitch (200.66 px runtime, max residual 0.99 px) — the
   generator cannot hold an exact pitch; the keys cover aperture and lip, so
   the trueing is invisible
4. premultiplied Lanczos downsample to the runtime width
