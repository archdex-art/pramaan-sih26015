"""Perceptual hashing — catching the same photograph filed twice.

The fraud this defends against is the cheapest one available: photograph one
completed check dam, file it against four interventions, collect four payments.
A cryptographic hash catches nothing, because re-saving a JPEG changes every
byte. A perceptual hash survives re-compression, resizing and mild colour
adjustment, which is exactly the set of transformations an office copy-paste
performs.

## The algorithm, and why this one

DCT-based pHash: greyscale, downscale to 32x32, two-dimensional DCT-II, keep the
top-left 8x8 low-frequency block, drop the DC term, and set one bit per
coefficient according to whether it exceeds the median of the block. 64 bits.

Dropping the DC term matters: it is total brightness, so keeping it makes the
hash sensitive to exposure, which is the one thing that legitimately differs
between two photographs of the same structure taken minutes apart. Comparing
against the median rather than the mean matters for the same reason — the median
is unmoved by a handful of extreme coefficients from a specular highlight.

The DCT is built here from a cosine basis matrix rather than taken from scipy.
`D @ block @ D.T` is one line, the matrix is 32x32 and constant, and it saves a
dependency whose only other use would be this. Orthonormal scaling is omitted
deliberately: every coefficient is scaled by the same constant, the comparison
is against the median of those same coefficients, and the resulting bits are
identical. A normalisation factor here would be arithmetic nobody can check.

## What it does not catch

A crop, a rotation past a few degrees, or a heavy perspective warp all defeat
pHash — the low-frequency structure genuinely changes. The documented P1 fallback
is ORB keypoint matching, which survives crops and moderate viewpoint change at
roughly a hundred times the cost per comparison. It is **not** implemented here
and must not be faked: a dedupe check that claims crop resistance and does not
have it is worse than one that states its limit, because the limit is what tells
a monitoring officer when to look with their own eyes.

## The signed-BIGINT problem

`field_images.phash` is `BIGINT`, which in PostgreSQL is *signed* 64-bit. A
pHash is a 64-bit unsigned pattern and the top bit is set roughly half the time,
so half of all hashes overflow the column. Storing them as `numeric`, or as a
16-character hex string, were both rejected: the column exists with a B-tree
index on it, changing it needs a migration, and the reinterpretation is exact
and reversible. So the conversion is explicit, in one pair of functions, and
tested at the boundary — `to_signed`/`to_unsigned` are a two's-complement
reinterpretation, not an arithmetic shift, and they lose nothing.

The trap that follows: Hamming distance must be computed on the *unsigned*
values. Python integers are arbitrary-precision, so `-1 ^ 1` is `-2`, and
`bin(-2).count("1")` is 1 rather than 63. Every comparison therefore goes
through `hamming`, which converts first.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from PIL import Image

#: Working resolution before the DCT. 32x32 is the conventional choice: it is
#: four times the 8x8 block that is kept, so the retained coefficients describe
#: genuinely low-frequency structure rather than the resample filter's ringing.
_HASH_INPUT = 32

#: Side of the retained low-frequency block. 8x8 = 64 coefficients, one of which
#: (the DC term) is discarded, leaving 63 informative bits plus one forced bit —
#: see `phash` for how the 64th is filled.
_HASH_BLOCK = 8

_UINT64_SPAN = 1 << 64
_SIGN_BIT = 1 << 63

#: Hamming distance at or below which two images are treated as the same
#: photograph and the upload is refused.
#:
#: Measured on this machine, over synthetic scenes re-encoded as JPEG: a
#: re-encode at a different quality moves the hash 0-4 bits, a 50 % resize 0
#: bits, a 25 % resize 4, a 20 % brightness lift 4, a 15 % contrast lift 0.
#: Against 1770 pairs of structurally different scenes the minimum distance was
#: 10, the 1st percentile 20 and the median 32, with none at or below 6. So 6
#: sits above every transformation-noise figure observed and below every
#: distinct-image figure observed.
#:
#: Tuned by inspection, not fitted: those scenes are synthetic, there is no
#: labelled duplicate set for this project, and no false-accept rate has been
#: measured on real field photographs. A threshold presented as calibrated
#: would be a claim about a measurement that was never made.
DUPLICATE_MAX_DISTANCE = 6

#: Above `DUPLICATE_MAX_DISTANCE` and at or below this, the upload is accepted
#: and the nearest existing record is named in the response.
#:
#: This band is two photographs of the same subject from almost the same spot —
#: legitimate (a before/after pair, two officers at one site) and also what a
#: lightly-edited duplicate looks like. An 85 % centre crop of one scene
#: measured 12 here, which is the module docstring's crop limitation showing up
#: as a number: the crop lands in this band rather than in the refusal band.
#: Refusing the band would block real work; ignoring it would hide the
#: resemblance. So it is recorded and shown, and a human decides — the same
#: shape as the rest of this system, where the machine reports and a named
#: officer concludes. 0.1 % of the distinct-scene pairs above also fall here,
#: which is the cost of that choice and is why the band accepts rather than
#: refuses.
SIMILAR_MAX_DISTANCE = 12


def _dct_matrix(n: int) -> NDArray[np.float64]:
    """The DCT-II basis, unnormalised: `M[k, i] = cos(pi * k * (2i + 1) / 2n)`."""
    k = np.arange(n, dtype=np.float64).reshape(n, 1)
    i = np.arange(n, dtype=np.float64).reshape(1, n)
    return np.cos(np.pi * k * (2.0 * i + 1.0) / (2.0 * n))


#: Constant for a fixed input size, so it is built once per process rather than
#: once per upload. 32x32 float64 is 8 KB; the alternative is 1024 cosines per
#: photograph for a matrix that can never differ.
_DCT = _dct_matrix(_HASH_INPUT)


def phash(image: Image.Image) -> int:
    """The 64-bit unsigned perceptual hash of an image.

    Returns the *unsigned* value. Callers storing it must pass it through
    `to_signed`; the split is deliberate, so that the number this function
    returns is the number the algorithm defines and the database's limitation is
    visible at the point where it applies.
    """
    grey = image.convert("L").resize((_HASH_INPUT, _HASH_INPUT), Image.LANCZOS)
    pixels = np.asarray(grey, dtype=np.float64)

    coefficients = _DCT @ pixels @ _DCT.T
    block = coefficients[:_HASH_BLOCK, :_HASH_BLOCK]

    # `[1:]` drops the DC term: it is total brightness, and a hash that tracks
    # exposure would call two photographs of the same wall in different light
    # different images.
    values = block.flatten()[1:]
    median = float(np.median(values))

    bits = 0
    for value in values:
        bits = (bits << 1) | int(float(value) > median)
    # 63 comparisons produce 63 bits; the value is shifted once more so the
    # result occupies the full 64-bit word the column is sized for, with the
    # low bit always zero. A constant bit contributes nothing to any Hamming
    # distance, which is why padding is safe — and it is padded rather than
    # left 63-bit so `to_signed`'s boundary behaviour is exercised by real
    # hashes rather than only by tests.
    return bits << 1


def to_signed(value: int) -> int:
    """Reinterpret a 64-bit unsigned pattern as PostgreSQL's signed `BIGINT`.

    Two's-complement reinterpretation, not arithmetic: the bit pattern is
    unchanged and `to_unsigned` recovers the input exactly.
    """
    if not 0 <= value < _UINT64_SPAN:
        raise ValueError(f"phash {value} is not a 64-bit unsigned value")
    return value - _UINT64_SPAN if value >= _SIGN_BIT else value


def to_unsigned(value: int) -> int:
    """The 64-bit pattern of `value`, as an unsigned integer.

    Accepts either representation, and is therefore idempotent:
    `to_unsigned(to_signed(h)) == h` and `to_unsigned(h) == h`. That is not
    laxity — the two input ranges, `[-2**63, 2**63)` for a `BIGINT` read back
    from PostgreSQL and `[0, 2**64)` for a freshly computed hash, overlap only
    on `[0, 2**63)` where both spellings denote the same pattern, so there is
    nothing to disambiguate.

    Making it total over the union is what lets `hamming` be called with a
    stored hash on one side and a just-computed one on the other, which is the
    only comparison this module actually performs. A stricter signature would
    have made that call site raise for every hash with the top bit set — half of
    them — and it did, once, before this was fixed.
    """
    if not -_SIGN_BIT <= value < _UINT64_SPAN:
        raise ValueError(f"{value} is not a 64-bit value in either representation")
    return value + _UINT64_SPAN if value < 0 else value


def hamming(left: int, right: int) -> int:
    """Number of differing bits between two hashes, given either sign.

    Converts both operands to unsigned before the XOR. Skipping that step is the
    defect described in the module docstring: Python's XOR on a negative
    operand produces a negative result whose `bit_count` is the count of set
    bits in its *magnitude*, which for two nearly-identical hashes with the top
    bit set reports a large distance instead of a small one — a duplicate that
    sails through.
    """
    return (to_unsigned(left) ^ to_unsigned(right)).bit_count()
