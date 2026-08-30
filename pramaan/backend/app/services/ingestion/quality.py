"""The quality gate — classical CV, no model, and a refusal it can defend.

A blurred or blown-out photograph that enters as `photo` evidence is permanent.
It is hashed into a verdict's `bundle_digest`, that verdict is signed into the
append-only adjudication ledger, and neither can be edited afterwards — that
immutability is the point of the ledger and it applies just as firmly to bad
inputs as to good ones. So the only place a bad photograph can be stopped is
before it is stored, and the only honest way to stop it is to refuse the upload
and say which measurement failed.

The alternative — accept everything and let the engine down-weight it — was
rejected for two reasons. First, the engine has no term for "the photograph is
unreadable": `photo` agreement is a statement about what is *in* the frame, and
a grey rectangle produces a confident-looking neutral rather than an abstention.
Second, a refusal at upload time reaches the one person who can fix it, standing
at the site, while a down-weighted verdict reaches a monitoring officer three
weeks later who cannot.

## Why no OpenCV and no model

Variance-of-Laplacian and a histogram are twenty lines of numpy each, and numpy
is already in the dependency tree. Adding OpenCV would add ~90 MB to the image
for two convolutions. A learned no-reference quality model would be a second
thing to calibrate, explain and defend, for a decision that is genuinely a
threshold on a physical measurement.

## Honesty about the thresholds

Every constant below was set by inspecting sharp and deliberately-defocused
photographs and picking a value with clear margin on both sides. They are
**tuned by inspection, not fitted**: there is no labelled blur dataset for this
project, no ROC curve was computed, and no operating point was chosen against a
measured false-rejection rate. Saying so is not modesty — a threshold presented
as calibrated is a claim about a measurement that was never made, and the whole
system's credibility rests on not making those.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from PIL import Image

#: Long edge, in pixels, that every image is scaled to before measurement.
#:
#: Load-bearing, not cosmetic. Variance-of-Laplacian scales with the amount of
#: detail per pixel, so the *same photograph* at 12 MP and at 640 px scores
#: differently by more than an order of magnitude. Without a fixed measurement
#: resolution the threshold below would mean "reject old phones", and a resized
#: copy of a rejected image would pass. 1024 keeps enough detail to distinguish
#: focus from defocus while making the measurement cost independent of the
#: camera.
MEASURE_LONG_EDGE = 1024

#: Minimum variance of the Laplacian, on the 0-255 greyscale of a
#: `MEASURE_LONG_EDGE` image, for the frame to count as in focus.
#:
#: Tuned by inspection against a synthetic outdoor scene (sky gradient, textured
#: ground, a jointed masonry wall, water) re-encoded as JPEG, and against the
#: project's own UI screenshots as a sharp upper bound. Measured on this
#: machine: sharp 1000-2400; Gaussian blur radius 1 px, 148; radius 1.5 px, 39;
#: radius 2 px, 10-25. Radius 1.5 px is about where a check dam's crest and
#: joint lines stop being locatable in the frame, so the threshold belongs
#: between 39 and 148. 60 sits in that gap nearer the reject end, and the
#: asymmetry is deliberate: a wrongly refused photograph costs an officer a
#: second walk to the site, which is recoverable, while a wrongly accepted one
#: is hashed into a signed verdict, which is not.
BLUR_MIN_VARIANCE = 60.0

#: A pixel at or below this level is treated as crushed to black, and at or
#: above `CLIP_WHITE_LEVEL` as blown to white. Not 0 and 255: JPEG quantisation
#: and sensor noise mean a genuinely clipped region lands within a couple of
#: levels of the rail rather than exactly on it.
CLIP_BLACK_LEVEL = 4
CLIP_WHITE_LEVEL = 251

#: Fraction of the frame that may be clipped before the exposure is called
#: unusable.
#:
#: Set well above what a "correctly exposed" rule would use, and the first draft
#: of 0.45 was measurably wrong: a sharp frame with the top half of the sky
#: blown out — an entirely normal Indian midday photograph in which the
#: structure is perfectly visible — clips 0.50 and was being refused. The gate
#: is looking for a frame where the *subject* cannot be seen, so the bar is a
#: clear majority of the frame on one rail. A lens cap, a flash washout and a
#: pocket photograph all clip well past 0.9.
CLIP_MAX_FRACTION = 0.60

#: Minimum spread between the 1st and 99th percentile of the greyscale.
#: Percentiles rather than min/max so a handful of hot pixels or one specular
#: highlight cannot make a flat grey frame look like it has range. A value below
#: this is a photograph of fog, a lens cap, or an over-compressed screenshot.
MIN_DYNAMIC_RANGE = 40.0

#: Flag vocabulary. Closed, and stored verbatim in `field_images.quality_flags`,
#: so the register can group by it and an officer sees the same word the gate
#: used. Extend deliberately: a new string here is a new value in a column that
#: already has rows.
FLAG_BLURRED = "blurred"
FLAG_CLIPPED_BLACK = "clipped_black"
FLAG_CLIPPED_WHITE = "clipped_white"
FLAG_LOW_DYNAMIC_RANGE = "low_dynamic_range"
FLAG_TOO_SMALL = "too_small"

#: Below this on either edge there is not enough image to measure, let alone to
#: identify a structure in. Also the guard that keeps the Laplacian's interior
#: slice non-empty.
MIN_EDGE_PX = 64


@dataclass(frozen=True, slots=True)
class QualityReport:
    """The gate's verdict on one image.

    `passed` is not derivable from `flags` being empty by a caller that does not
    know which flags are fatal, so it is computed once, here, and the caller is
    spared inventing a second rule. Frozen for the same reason the engine's
    dataclasses are: a report that can be mutated after the decision is a report
    that can be made to say the upload passed.
    """

    blur_score: float
    exposure_ok: bool
    passed: bool
    flags: tuple[str, ...]

    @property
    def first_flag(self) -> str | None:
        """The flag to name in a refusal. `flags` is built in severity order."""
        return self.flags[0] if self.flags else None


def _greyscale(image: Image.Image) -> NDArray[np.float64]:
    """Luminance array at the fixed measurement resolution.

    `Image.LANCZOS` for the downscale: a box or nearest resample aliases
    high-frequency detail into the result, which *raises* the Laplacian variance
    of a blurred image and would let a soft photograph pass by virtue of being
    large. The resample filter is part of the measurement.
    """
    grey = image.convert("L")
    long_edge = max(grey.width, grey.height)
    if long_edge > MEASURE_LONG_EDGE:
        scale = MEASURE_LONG_EDGE / float(long_edge)
        grey = grey.resize(
            (max(1, round(grey.width * scale)), max(1, round(grey.height * scale))),
            Image.LANCZOS,
        )
    return np.asarray(grey, dtype=np.float64)


def laplacian_variance(grey: NDArray[np.float64]) -> float:
    """Variance of the 4-neighbour discrete Laplacian over the array's interior.

    The kernel is the standard 5-point stencil::

        0  1  0
        1 -4  1
        0  1  0

    applied by slicing rather than by a convolution routine, because that is one
    expression, allocates one temporary, and needs no scipy. The border row and
    column are excluded rather than padded: any padding invents pixel values,
    and edge-replication in particular writes zeros into the Laplacian there,
    which drags the variance down for small images specifically.
    """
    if grey.shape[0] < 3 or grey.shape[1] < 3:
        # Not an error: `assess` has already flagged the image as too small.
        # Returning 0.0 keeps this function total, so a caller cannot get an
        # exception out of a pure measurement.
        return 0.0
    lap = (
        grey[:-2, 1:-1] + grey[2:, 1:-1] + grey[1:-1, :-2] + grey[1:-1, 2:] - 4.0 * grey[1:-1, 1:-1]
    )
    return float(lap.var())


def exposure_flags(grey: NDArray[np.float64]) -> tuple[str, ...]:
    """Clipping and dynamic-range flags from the greyscale histogram.

    Three independent conditions, reported independently. A frame can be both
    crushed in the shadows and flat, and collapsing that into one "bad
    exposure" flag would tell the officer to fix the wrong thing.
    """
    flags: list[str] = []
    total = float(grey.size)
    if total <= 0:  # pragma: no cover - `assess` rejects empty images first
        return ()

    if float(np.count_nonzero(grey <= CLIP_BLACK_LEVEL)) / total > CLIP_MAX_FRACTION:
        flags.append(FLAG_CLIPPED_BLACK)
    if float(np.count_nonzero(grey >= CLIP_WHITE_LEVEL)) / total > CLIP_MAX_FRACTION:
        flags.append(FLAG_CLIPPED_WHITE)

    low, high = np.percentile(grey, (1.0, 99.0))
    if float(high) - float(low) < MIN_DYNAMIC_RANGE:
        flags.append(FLAG_LOW_DYNAMIC_RANGE)
    return tuple(flags)


def assess(image: Image.Image) -> QualityReport:
    """Measure one image and decide whether it may become evidence.

    Order matters. `too_small` is checked first and short-circuits, because
    every subsequent measurement on a 20 px image is arithmetic on noise, and a
    report carrying a blur score computed from nothing would be read as a
    measurement.

    Blur is listed before exposure in `flags` so the refusal message names the
    condition an officer can actually correct at the site.
    """
    grey = _greyscale(image)
    if min(image.width, image.height) < MIN_EDGE_PX:
        return QualityReport(
            blur_score=0.0,
            exposure_ok=False,
            passed=False,
            flags=(FLAG_TOO_SMALL,),
        )

    blur_score = laplacian_variance(grey)
    exposure = exposure_flags(grey)

    flags: list[str] = []
    if blur_score < BLUR_MIN_VARIANCE:
        flags.append(FLAG_BLURRED)
    flags.extend(exposure)

    return QualityReport(
        blur_score=round(blur_score, 3),
        exposure_ok=not exposure,
        passed=not flags,
        flags=tuple(flags),
    )
