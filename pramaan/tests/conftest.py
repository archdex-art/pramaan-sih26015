"""Fixtures shared by every engine test suite.

The bundle *builders* live in `tests/bundles.py`; see the note at the end of
that file for why they are not here.
"""

from __future__ import annotations

import pytest
from bundles import EngineConfig


@pytest.fixture
def cfg() -> EngineConfig:
    return EngineConfig()
