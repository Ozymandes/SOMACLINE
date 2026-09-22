//! The Abyssal specimen catalogue: five mathematical organisms.
//! Port of `organism/species.py`.
//!
//! Each entry pairs one BODY PLAN from `mathforms` with the archive metadata
//! the instrument displays and the key plate the selector engraves. The
//! archive codes, class and origin shown on the footer rail and the selector
//! ledge come from here, so the machine can never describe a specimen it is
//! not drawing.

use crate::mathforms::{build, SourceBody};

#[derive(Clone, Copy, Debug)]
pub struct Species {
    pub key: &'static str,      // stable identifier
    pub name: &'static str,     // binomial, shown as the specimen name
    pub epithet: &'static str,  // vernacular name
    pub archive: &'static str,  // archive code
    pub cls: &'static str,      // class
    pub origin: &'static str,   // origin
    pub short: &'static str,    // engraved abbreviation
    pub morphology: &'static str, // morphology word for the observation field
    pub symmetry: &'static str, // symmetry description
    pub plan: &'static str,     // body-plan word, shown in the field heading
    pub notes: &'static str,    // short archive note
    pub response: &'static str, // behavioural mapping summary
    pub source: &'static str,   // key into sources::SOURCES
}

impl Species {
    /// Construct this specimen's organism. Allocates; call once per key.
    pub fn build(&self, seed: u64) -> SourceBody {
        build(self.source, seed)
    }

    /// The symmetry as the observation field states it.
    pub fn symmetry_short(&self) -> &'static str {
        self.plan
    }
}

pub static CATALOGUE: [Species; 5] = [
    Species {
        key: "sigmata",
        name: "PLUMARIA SIGMATA",
        epithet: "THE SIGMOID PLUME",
        archive: "AQS-0042",
        cls: "MATHEMATICAL FORM",
        origin: "SYNTHETIC",
        short: "SIGMA",
        morphology: "SIGMOID PLUME",
        symmetry: "AXIAL / CURVILINEAR",
        plan: "AXIAL",
        notes: "BIFURCATED FILAMENT SHEET",
        response: "LOAD READS AS A RIPPLE DOWN THE BODY",
        source: "s01",
    },
    Species {
        key: "coniugata",
        name: "DIPLOSOMA CONIUGATA",
        epithet: "THE COUPLED PAIR",
        archive: "AQS-0117",
        cls: "MATHEMATICAL FORM",
        origin: "SYNTHETIC",
        short: "DIPLO",
        morphology: "DIPLOSOMATIC",
        symmetry: "PAIRED / ACENTRIC",
        plan: "PAIRED",
        notes: "TWO BODIES, ONE EQUATION",
        response: "I/O CROSSES BETWEEN THE BODIES",
        source: "s02",
    },
    Species {
        key: "rostrata",
        name: "SYMMETRA ROSTRATA",
        epithet: "THE MIRRORED ROSTRUM",
        archive: "AQS-0233",
        cls: "MATHEMATICAL FORM",
        origin: "SYNTHETIC",
        short: "ROSTRA",
        morphology: "ROSTRATE",
        symmetry: "SAGITTAL MIRROR",
        plan: "MIRROR",
        notes: "EXACT REFLECTION PLANE",
        response: "HEAT BREAKS THE MIRROR PLANE",
        source: "s03",
    },
    Species {
        key: "quadriplex",
        name: "QUADRIPLUMA ARTICULATA",
        epithet: "THE QUARTERED PLUME",
        archive: "AQS-0308",
        cls: "MATHEMATICAL FORM",
        origin: "SYNTHETIC",
        short: "QUADRI",
        morphology: "QUADRIPLUMATE",
        symmetry: "FOUR-PART / ARTICULATED",
        plan: "QUARTERED",
        notes: "FOUR PLUMES, ONE INDEX CLASS",
        response: "LOAD DESYNCHRONISES THE FOUR",
        source: "s04",
    },
    Species {
        key: "solitaria",
        name: "PENNARIA SOLITARIA",
        epithet: "THE SOLITARY FEATHER",
        archive: "AQS-0451",
        cls: "MATHEMATICAL FORM",
        origin: "SYNTHETIC",
        short: "PENNA",
        morphology: "PENNATE",
        symmetry: "AXIAL / ARCUATE",
        plan: "ARCUATE",
        notes: "ONE RACHIS, RIBBED VANE",
        response: "THE WHIP GROWS TOWARD THE TIP",
        source: "s05",
    },
];

pub const COUNT: usize = CATALOGUE.len();

/// Short labels engraved on the selector keys, in bank order.
pub fn key_labels() -> Vec<&'static str> {
    CATALOGUE.iter().map(|s| s.short).collect()
}

pub fn by_index(i: usize) -> &'static Species {
    &CATALOGUE[i % COUNT]
}

pub fn by_key(key: &str) -> Option<&'static Species> {
    CATALOGUE.iter().find(|s| s.key == key)
}

pub fn index_of(key: &str) -> usize {
    for (i, s) in CATALOGUE.iter().enumerate() {
        if s.key == key {
            return i;
        }
    }
    0
}
