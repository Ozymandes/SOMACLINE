"""The Abyssal specimen catalogue: five mathematical organisms.

Each entry pairs one BODY PLAN from `mathforms` with the archive metadata the
instrument displays and the key plate the selector engraves.

These are five genuinely different organisms, not one solver with its symmetry
order changed. Their taxonomy follows their actual morphology - a ciliated
ribbon is not a radial plume and is not named as though it were - and the
archive codes, class and origin shown on the footer rail and the selector
ledge come from here, so the machine can never describe a specimen it is not
drawing.

BEHAVIOURAL MAPPING
-------------------
All five are driven by the same four physiological channels, so live machine
state reads consistently across the catalogue - but each maps them onto its
own morphology rather than onto a shared animation speed:

    agitation  <- CPU load        motion energy
    pulse      <- temperature     metabolic contraction, thermal stress
    density    <- memory          how much body is expressed
    flux/surge <- I/O + network   peripheral excitation and propagating events

`response` records where each specimen is most legible, which is what makes
switching feel informative rather than decorative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import mathforms as MF
from .mathforms import SourceBody


@dataclass(frozen=True, slots=True)
class Species:
    key: str                     # stable identifier
    name: str                    # binomial, shown as the specimen name
    epithet: str                 # vernacular name
    archive: str                 # archive code
    cls: str                     # class
    origin: str                  # origin
    short: str                   # engraved abbreviation
    morphology: str              # morphology word for the observation field
    symmetry: str                # symmetry description
    plan: str                    # body-plan word, shown in the field heading
    notes: str                   # short archive note
    response: str                # behavioural mapping summary
    source: str          # key into organism.sources.SOURCES

    def build(self, seed: int = 20260920) -> SourceBody:
        """Construct this specimen's organism. Allocates; call once per key."""
        return MF.build(self.source, seed=seed)

    @property
    def symmetry_short(self) -> str:
        """The symmetry as the observation field states it."""
        return self.plan


CATALOGUE: tuple[Species, ...] = (
    Species(
        key="sigmata",
        name="PLUMARIA SIGMATA",
        epithet="THE SIGMOID PLUME",
        archive="AQS-0042",
        cls="MATHEMATICAL FORM",
        origin="SYNTHETIC",
        short="SIGMA",
        morphology="SIGMOID PLUME",
        symmetry="AXIAL / CURVILINEAR",
        plan="AXIAL",
        notes="BIFURCATED FILAMENT SHEET",
        response="LOAD READS AS A RIPPLE DOWN THE BODY",
        source="s01",
    ),
    Species(
        key="coniugata",
        name="DIPLOSOMA CONIUGATA",
        epithet="THE COUPLED PAIR",
        archive="AQS-0117",
        cls="MATHEMATICAL FORM",
        origin="SYNTHETIC",
        short="DIPLO",
        morphology="DIPLOSOMATIC",
        symmetry="PAIRED / ACENTRIC",
        plan="PAIRED",
        notes="TWO BODIES, ONE EQUATION",
        response="I/O CROSSES BETWEEN THE BODIES",
        source="s02",
    ),
    Species(
        key="rostrata",
        name="SYMMETRA ROSTRATA",
        epithet="THE MIRRORED ROSTRUM",
        archive="AQS-0233",
        cls="MATHEMATICAL FORM",
        origin="SYNTHETIC",
        short="ROSTRA",
        morphology="ROSTRATE",
        symmetry="SAGITTAL MIRROR",
        plan="MIRROR",
        notes="EXACT REFLECTION PLANE",
        response="HEAT BREAKS THE MIRROR PLANE",
        source="s03",
    ),
    Species(
        key="quadriplex",
        name="QUADRIPLUMA ARTICULATA",
        epithet="THE QUARTERED PLUME",
        archive="AQS-0308",
        cls="MATHEMATICAL FORM",
        origin="SYNTHETIC",
        short="QUADRI",
        morphology="QUADRIPLUMATE",
        symmetry="FOUR-PART / ARTICULATED",
        plan="QUARTERED",
        notes="FOUR PLUMES, ONE INDEX CLASS",
        response="LOAD DESYNCHRONISES THE FOUR",
        source="s04",
    ),
    Species(
        key="solitaria",
        name="PENNARIA SOLITARIA",
        epithet="THE SOLITARY FEATHER",
        archive="AQS-0451",
        cls="MATHEMATICAL FORM",
        origin="SYNTHETIC",
        short="PENNA",
        morphology="PENNATE",
        symmetry="AXIAL / ARCUATE",
        plan="ARCUATE",
        notes="ONE RACHIS, RIBBED VANE",
        response="THE WHIP GROWS TOWARD THE TIP",
        source="s05",
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
