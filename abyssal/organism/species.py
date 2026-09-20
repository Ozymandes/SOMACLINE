"""The Abyssal specimen catalogue.

Five specimens of one family. Every entry pairs a `Morphology` (what it looks
like and how it moves) with the archive metadata the instrument displays.

All five share the suffix -RADIA: a radial body plan with a luminous core, N
identical lobes, a rachis per lobe and paired filaments along it. They differ
in symmetry order, filament economy and motion temperament, not in kind.

BEHAVIOURAL MAPPING
-------------------
Every specimen is driven by the same three physiological channels, so live
machine state reads consistently across the catalogue:

    agitation  <- CPU load          filament motion energy
    pulse      <- temperature       breathing rate and radial expansion
    density    <- memory pressure   how many filaments are lit

`response` records where each specimen is most legible, which is what makes
switching feel informative rather than decorative.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import morphology as M
from .morphology import Morphology


@dataclass(frozen=True, slots=True)
class Species:
    key: str                 # stable identifier
    name: str                # binomial, shown as the specimen name
    epithet: str             # vernacular name
    archive: str             # archive code
    cls: str                 # class
    origin: str              # origin
    short: str               # engraved on the selector key
    morphology: str          # morphology word for the observation field
    symmetry: str            # symmetry description
    notes: str               # short archive note
    response: str            # behavioural mapping summary
    morph: Morphology

    @property
    def lobes(self) -> int:
        return self.morph.n_lobe


CATALOGUE: tuple[Species, ...] = (
    Species(
        key="quadrilobata",
        morphology="QUADRILOBATE",
        short="QUADRI",
        name="PLUMIRADIA QUADRILOBATA",
        epithet="THE QUARTERED PLUME",
        archive="AQS-0042",
        cls="MATHEMATICAL ORGANISM",
        origin="SYNTHETIC",
        symmetry="QUADRILATERAL",
        notes="PLUME MORPHOLOGY",
        response="BROAD VANES; LOAD READS AS SWAY",
        morph=M.QUADRILOBATA,
    ),
    Species(
        key="trispira",
        morphology="HELICATE",
        short="TRISPIRA",
        name="HELICORADIA TRISPIRA",
        epithet="THE SPIRALLED TRINE",
        archive="AQS-0117",
        cls="MATHEMATICAL ORGANISM",
        origin="SYNTHETIC",
        symmetry="TRILATERAL",
        notes="HELICAL TORSION",
        response="CURL DEEPENS UNDER LOAD",
        morph=M.TRISPIRA,
    ),
    Species(
        key="pentafida",
        morphology="CILIATE",
        short="PENTA",
        name="CILIARADIA PENTAFIDA",
        epithet="THE FIVEFOLD CILIUM",
        archive="AQS-0233",
        cls="MATHEMATICAL ORGANISM",
        origin="SYNTHETIC",
        symmetry="PENTARADIAL",
        notes="DENSE CILIATION",
        response="MEMORY READS AS FILAMENT COUNT",
        morph=M.PENTAFIDA,
    ),
    Species(
        key="hexastoma",
        morphology="UMBELLATE",
        short="HEXA",
        name="UMBELLIRADIA HEXASTOMA",
        epithet="THE HEXATE UMBEL",
        archive="AQS-0308",
        cls="MATHEMATICAL ORGANISM",
        origin="SYNTHETIC",
        symmetry="HEXARADIAL",
        notes="UMBELLATE CANOPY",
        response="THERMAL READS AS CANOPY BREATH",
        morph=M.HEXASTOMA,
    ),
    Species(
        key="bifida",
        morphology="FLAGELLATE",
        short="BIFIDA",
        name="VIBRISSARADIA BIFIDA",
        epithet="THE CLEFT VIBRISSA",
        archive="AQS-0451",
        cls="MATHEMATICAL ORGANISM",
        origin="SYNTHETIC",
        symmetry="BILATERAL",
        notes="SPARSE FLAGELLATION",
        response="HIGHEST MOTION SENSITIVITY",
        morph=M.BIFIDA,
    ),
)

COUNT = len(CATALOGUE)

#: Short labels engraved on the selector keys, in bank order.
KEY_LABELS: tuple[str, ...] = tuple(s.short for s in CATALOGUE)


def by_index(i: int) -> Species:
    return CATALOGUE[i % COUNT]


def by_key(key: str) -> Species | None:
    for s in CATALOGUE:
        if s.key == key:
            return s
    return None


def index_of(key: str) -> int:
    for i, s in enumerate(CATALOGUE):
        if s.key == key:
            return i
    return 0
