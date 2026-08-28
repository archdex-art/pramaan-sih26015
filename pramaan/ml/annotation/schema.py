"""GT-1 annotation schema — FROZEN.

This file is the single most schedule-critical artefact in the ML track, and it
is frozen before the first photograph is taken. The reason is empirical, not
aesthetic: annotation efforts fail because the label set churns halfway through,
invalidating everything already labelled. Risk R-41 in the plan names this as
the second-highest-probability project killer after scope creep.

It matters more now than when the plan was written. The measured rejection of
Mapillary (docs/10) removed the largest external source, so team-collected
photographs are no longer one input among four — they are the primary input. A
schema change after the sprint starts now costs the whole corpus.

## Design rules

1. **Every label is multi-label, not multi-class.** A single photograph can show
   water AND a structure AND exposed soil. Forcing one class per image would
   discard most of the information in a field geotag.
2. **Every label has an explicit "cannot tell" state.** The annotator's
   uncertainty is data. Collapsing "no water" and "cannot see whether there is
   water" into one negative is how a calibrated model becomes over-confident.
3. **`scene_scale` is mandatory on every image.** It is what lets the engine
   refuse to cross-check a close-up against a 30 m pixel (docs §16.2 STEP 5).
4. **No label requires domain expertise a WDT member would not have.** If two
   trained annotators cannot agree on a label from the photograph alone, the
   label does not belong in GT-1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

SCHEMA_VERSION = "gt1-v1"


class Ternary(StrEnum):
    """Answer state for a binary observation.

    ``UNCERTAIN`` is a first-class value, not a missing one. It is exported to
    training as an ignore-mask entry rather than a negative, because a model
    trained to call ambiguous water "no water" learns to be confident when it
    should abstain — precisely the failure the engine's abstention band exists
    to prevent.
    """

    YES = "yes"
    NO = "no"
    UNCERTAIN = "uncertain"


class SceneScale(StrEnum):
    """How much of the world the frame covers.

    Drives the engine's scene-scale gate. Values match
    ``app.services.reconcile.types.SceneScale`` exactly — the annotation tool and
    the engine share one vocabulary, so an annotator's judgement flows into the
    verdict without a translation table nobody maintains.
    """

    CLOSE_UP = "close_up"
    MID = "mid"
    LANDSCAPE = "landscape"
    UNKNOWN = "unknown"


class VegetationDensity(StrEnum):
    """Ordinal cover classes.

    Deliberately four coarse bins rather than a percentage. Annotators cannot
    estimate canopy percentage from a photograph reliably, and a fake continuous
    variable would invite the model to learn precision that does not exist.
    """

    NONE = "none"  # bare, < ~5% cover
    SPARSE = "sparse"  # ~5-30%
    MODERATE = "moderate"  # ~30-70%
    DENSE = "dense"  # > ~70%
    UNCERTAIN = "uncertain"


class ConstructionStage(StrEnum):
    """Mirrors DRISHTI's own status vocabulary where it is visible in a photo.

    DRISHTI records four work statuses. Only three are visually distinguishable:
    nobody can photograph the difference between 'not initiated' and 'a field'.
    That asymmetry is stated here rather than papered over.
    """

    NOT_STARTED = "not_started"  # no works visible
    IN_PROGRESS = "in_progress"  # excavation, material, partial structure
    COMPLETED = "completed"  # finished structure
    NOT_APPLICABLE = "not_applicable"  # no structure is the subject
    UNCERTAIN = "uncertain"


class StructureType(StrEnum):
    """Visually distinguishable structure classes.

    A deliberately shorter list than the 18-category DRISHTI taxonomy: this
    enumerates only what an annotator can separate from a photograph. A farm
    pond and a percolation tank are frequently indistinguishable at close range,
    so they share a class, and the engine gets the authoritative type from the
    MIS record rather than from the photo model.
    """

    NONE = "none"
    MASONRY_CHECK_DAM = "masonry_check_dam"
    EARTHEN_BUND = "earthen_bund"
    EXCAVATED_POND_OR_TANK = "excavated_pond_or_tank"
    TRENCH_OR_CONTOUR_WORK = "trench_or_contour_work"
    GULLY_PLUG = "gully_plug"
    WELL_OR_BOREWELL = "well_or_borewell"
    PLANTATION = "plantation"
    OTHER_STRUCTURE = "other_structure"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class LabelSpec:
    """One question put to the annotator."""

    key: str
    question: str
    kind: str  # "ternary" | "enum"
    #: Allowed values, for enum labels.
    values: tuple[str, ...] = ()
    #: Shown in the tool. Ambiguity resolved here, not in a side channel.
    guidance: str = ""
    #: Labels the engine consumes directly.
    feeds_engine: bool = False


#: THE FROZEN SCHEMA. Changing this list requires bumping SCHEMA_VERSION and
#: re-deriving every split, because a corpus labelled under two schemas cannot
#: be pooled honestly.
LABELS: tuple[LabelSpec, ...] = (
    LabelSpec(
        key="water_present",
        question="Is standing or flowing water visible?",
        kind="ternary",
        guidance=(
            "YES for any visible standing or flowing water, however small, "
            "including muddy or algal water. NO if the bed is visibly dry. "
            "UNCERTAIN if vegetation, shadow or framing hides the bed. Wet mud "
            "without standing water is NO."
        ),
        feeds_engine=True,
    ),
    LabelSpec(
        key="structure_present",
        question="Is a built water-harvesting or soil-conservation structure visible?",
        kind="ternary",
        guidance=(
            "YES for masonry, concrete, earthen embankments, excavated pits, "
            "bunds and trenches that are clearly constructed. NO for natural "
            "features only. UNCERTAIN when an embankment could be a field "
            "boundary — this case is common and must not be forced."
        ),
        feeds_engine=True,
    ),
    LabelSpec(
        key="structure_type",
        question="What kind of structure is it?",
        kind="enum",
        values=tuple(s.value for s in StructureType),
        guidance=(
            "Answer NONE when structure_present is NO. Use "
            "EXCAVATED_POND_OR_TANK for both farm ponds and percolation tanks: "
            "they are routinely indistinguishable at close range, and the "
            "authoritative type comes from the MIS record, not the photograph."
        ),
        feeds_engine=True,
    ),
    LabelSpec(
        key="vegetation_density",
        question="How much vegetation cover is in the frame?",
        kind="enum",
        values=tuple(v.value for v in VegetationDensity),
        guidance=(
            "Judge the ground surface, not the tree canopy overhead. A frame "
            "dominated by sky or road is UNCERTAIN, not NONE."
        ),
        feeds_engine=True,
    ),
    LabelSpec(
        key="exposed_soil",
        question="Is bare soil a significant part of the frame?",
        kind="ternary",
        guidance=(
            "YES when bare earth is roughly a quarter or more of the ground "
            "surface. Freshly excavated spoil counts. A paved road does not."
        ),
        feeds_engine=True,
    ),
    LabelSpec(
        key="erosion_visible",
        question="Is active erosion visible (gully, rill, scour, headcut)?",
        kind="ternary",
        guidance=(
            "YES for incised channels, rills on a slope, or scour around a "
            "structure. A ploughed furrow is NOT erosion — this is the most "
            "common false positive in this label."
        ),
        feeds_engine=True,
    ),
    LabelSpec(
        key="construction_stage",
        question="What stage is the structure at?",
        kind="enum",
        values=tuple(c.value for c in ConstructionStage),
        guidance=(
            "NOT_APPLICABLE when no structure is the subject of the photo. "
            "IN_PROGRESS requires visible evidence of ongoing work: spoil "
            "heaps, formwork, stacked material, machinery, partial walls."
        ),
        feeds_engine=True,
    ),
    LabelSpec(
        key="scene_scale",
        question="How much of the site does the frame cover?",
        kind="enum",
        values=tuple(s.value for s in SceneScale),
        guidance=(
            "CLOSE_UP: a detail filling the frame, no surrounding context "
            "(a pipe outlet, a wall face). MID: the structure and its immediate "
            "surroundings. LANDSCAPE: the wider catchment is visible. This "
            "answer decides whether the engine may cross-check the photo "
            "against a 30 m satellite pixel, so it is never left blank."
        ),
        feeds_engine=True,
    ),
    LabelSpec(
        key="people_present",
        question="Are identifiable people visible?",
        kind="ternary",
        guidance=(
            "Used only to route the image to face blurring before any UI shows "
            "it. Never used as a model target. Field photographs of rural works "
            "routinely contain beneficiaries, labourers and children "
            "(docs §25.3)."
        ),
        feeds_engine=False,
    ),
    LabelSpec(
        key="unusable",
        question="Is the photo unusable (blurred, dark, obstructed, indoors)?",
        kind="ternary",
        guidance=(
            "YES excludes the image from training and evaluation but keeps it "
            "in the corpus with its reason, because the rate of unusable field "
            "photographs is itself a finding worth reporting."
        ),
        feeds_engine=False,
    ),
)

LABELS_BY_KEY: dict[str, LabelSpec] = {spec.key: spec for spec in LABELS}

#: Labels the photo producer converts into engine evidence.
ENGINE_LABELS: tuple[str, ...] = tuple(s.key for s in LABELS if s.feeds_engine)


@dataclass(frozen=True, slots=True)
class Annotation:
    """One annotator's answers for one image."""

    image_id: str
    annotator: str
    schema_version: str
    answers: dict[str, str]
    #: Free-text note, surfaced in adjudication when the case is odd.
    note: str = ""
    #: Seconds spent. Feeds the sprint's throughput estimate honestly.
    duration_s: float | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"annotation was made under schema {self.schema_version!r} but "
                f"this code is {SCHEMA_VERSION!r}. Corpora labelled under two "
                "schemas must not be pooled — re-derive the splits."
            )
        validate_answers(self.answers)


def validate_answers(answers: dict[str, str]) -> None:
    """Reject an incomplete or out-of-vocabulary annotation.

    Strict on purpose. A partially answered row that silently defaults to NO is
    how a corpus acquires a systematic negative bias that nobody can detect
    afterwards.
    """
    missing = [spec.key for spec in LABELS if spec.key not in answers]
    if missing:
        raise ValueError(f"annotation is missing required labels: {missing}")
    unknown = sorted(set(answers) - set(LABELS_BY_KEY))
    if unknown:
        raise ValueError(f"annotation has unknown labels: {unknown}")

    for key, value in answers.items():
        spec = LABELS_BY_KEY[key]
        allowed = tuple(t.value for t in Ternary) if spec.kind == "ternary" else spec.values
        if value not in allowed:
            raise ValueError(f"label {key!r} has value {value!r}, which is not in {allowed}")

    # Cross-field consistency. These are the two contradictions annotators
    # actually produce, so they are checked rather than trusted.
    if answers["structure_present"] == Ternary.NO and answers["structure_type"] not in (
        StructureType.NONE,
        StructureType.UNCERTAIN,
    ):
        raise ValueError(
            "structure_present=no but structure_type is a real type; set structure_type=none"
        )
    if (
        answers["structure_present"] == Ternary.YES
        and answers["structure_type"] == StructureType.NONE
    ):
        raise ValueError(
            "structure_present=yes but structure_type=none; choose a type or "
            "mark structure_type=uncertain"
        )


@dataclass(frozen=True, slots=True)
class SplitPolicy:
    """How GT-1 is divided. Geographic, never random.

    Random splits leak. Field photographs come in bursts at one site, and
    Mapillary-style sequences are worse — near-duplicate frames metres apart. A
    random split puts near-duplicates on both sides and inflates every reported
    metric. Splitting by micro-watershed guarantees a test site was never seen.
    """

    train_fraction: float = 0.6
    val_fraction: float = 0.2
    test_fraction: float = 0.2
    #: The unit that is never split across sides.
    group_by: str = "micro_watershed_code"
    #: Sources permitted in each side. LUCAS is EU imagery: legitimate for
    #: pre-training and for fitting a linear probe, never for reporting Indian
    #: accuracy. Enforced here, not left to reviewer memory.
    test_sources_allowed: tuple[str, ...] = ("team_collected", "commons")
    train_sources_allowed: tuple[str, ...] = (
        "team_collected",
        "commons",
        "lucas",
        "mapillary_culvert",
    )

    def __post_init__(self) -> None:
        total = self.train_fraction + self.val_fraction + self.test_fraction
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"split fractions must sum to 1.0, got {total}")
        leaked = set(self.test_sources_allowed) - set(self.train_sources_allowed)
        if leaked:
            raise ValueError(f"test sources not present in train vocabulary: {leaked}")


@dataclass(frozen=True, slots=True)
class SprintTarget:
    """The annotation sprint's budget, with the post-Mapillary revision applied."""

    minimum_images: int = 800
    target_images: int = 1200
    #: Fraction double-annotated to compute Cohen's kappa. Below ~10% the kappa
    #: confidence interval is too wide to mean anything.
    double_annotated_fraction: float = 0.12
    #: Measured, not assumed: seconds per image at steady state.
    assumed_seconds_per_image: float = 25.0
    source_mix: dict[str, int] = field(
        default_factory=lambda: {
            # Revised after Mapillary was rejected on measured evidence
            # (docs/10): team collection is now the primary source, not one of
            # four.
            "team_collected": 700,
            "commons": 200,
            "mapillary_culvert": 50,
        }
    )

    def person_hours(self, images: int | None = None) -> float:
        n = images if images is not None else self.target_images
        effective = n * (1.0 + self.double_annotated_fraction)
        return effective * self.assumed_seconds_per_image / 3600.0
