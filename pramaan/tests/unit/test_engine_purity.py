"""The reconciliation engine must be provably pure.

A comment saying "pure function" is worthless — it rots the first time someone
adds a convenient ``datetime.now()``. This test walks the AST of every module in
``app/services/reconcile`` and asserts the package imports nothing that could
introduce IO, a clock, randomness, or a database.

Why this matters commercially, not just aesthetically: docs §21.3 promises a
verdict can be recomputed byte-identically from its lineage record. That promise
is what makes machine output usable as government evidence. A single hidden
clock read breaks it silently — the verdict still looks fine, it just cannot be
reproduced tomorrow.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parents[2] / "backend" / "app" / "services" / "reconcile"

#: Top-level modules the engine must never reach for. Deliberately broad:
#: anything that touches the world, the clock, the OS, or a socket.
FORBIDDEN_MODULES = frozenset(
    {
        "asyncio",
        "boto3",
        "celery",
        "datetime",
        "fastapi",
        "geopandas",
        "httpx",
        "io",
        "logging",
        "numpy",
        "os",
        "pathlib",
        "psycopg",
        "random",
        "rasterio",
        "redis",
        "requests",
        "secrets",
        "shapely",
        "socket",
        "sqlalchemy",
        "subprocess",
        "sys",
        "tempfile",
        "time",
        "torch",
        "urllib",
        "uuid",
    }
)

#: Attribute calls that are impure even when the module is allowed.
FORBIDDEN_CALLS = frozenset({"now", "today", "utcnow", "monotonic", "perf_counter"})


def engine_modules() -> list[Path]:
    mods = sorted(ENGINE_DIR.glob("*.py"))
    assert mods, f"no engine modules found under {ENGINE_DIR}"
    return mods


def top_level(name: str) -> str:
    return name.split(".", 1)[0]


@pytest.mark.parametrize("module_path", engine_modules(), ids=lambda p: p.name)
def test_module_imports_nothing_impure(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if top_level(alias.name) in FORBIDDEN_MODULES:
                    offenders.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and top_level(node.module) in FORBIDDEN_MODULES:
                offenders.append(f"line {node.lineno}: from {node.module} import ...")
            if node.level and node.level > 0:
                # Relative imports would let the engine reach outside the package
                # without naming what it reached for.
                offenders.append(f"line {node.lineno}: relative import (level={node.level})")

    assert not offenders, (
        f"{module_path.name} imports impure modules — the engine must stay a pure "
        f"function of its evidence bundle:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("module_path", engine_modules(), ids=lambda p: p.name)
def test_module_calls_no_clock(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    offenders = [
        f"line {node.lineno}: .{node.func.attr}()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in FORBIDDEN_CALLS
    ]
    assert not offenders, (
        f"{module_path.name} reads a clock — a verdict must be reproducible from "
        f"its lineage record, which a timestamp makes impossible:\n  " + "\n  ".join(offenders)
    )


def test_engine_only_imports_its_own_package_and_stdlib_types() -> None:
    """Whatever the engine does import must be either stdlib typing-ish or itself."""
    allowed_prefixes = ("app.services.reconcile", "dataclasses", "enum", "typing", "__future__")
    bad: list[str] = []
    for module_path in engine_modules():
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if not name.startswith(allowed_prefixes):
                    bad.append(f"{module_path.name}:{node.lineno} -> {name}")
    assert not bad, (
        "engine reached outside its allowed import surface "
        f"{allowed_prefixes}:\n  " + "\n  ".join(bad)
    )
