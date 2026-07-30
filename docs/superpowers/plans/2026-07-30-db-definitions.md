# edutap.db_definitions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `edutap-dbdef` CLI that collects the DB definitions of installed eduTAP packages through entry points and emits reviewable SQL (`create`, `diff`, `check`, `apply`).

**Architecture:** A helper package that is never deployed. `discovery` finds `SchemaDefinition` objects via the `edutap.db_definitions` entry-point group and orders them topologically; `render` turns package `MetaData` into baseline DDL without a database; `compare` diffs the definitions against a live schema using Alembic's autogenerate APIs; `execute` is the only module that writes to a database. `render` and `compare` are pure functions over metadata.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.x, Alembic, psycopg (sync), pydantic-settings, stdlib `argparse`, pytest, testcontainers[postgres], ruff, ty, prek, tox, hatchling.

**Spec:** `docs/superpowers/specs/2026-07-30-db-definitions-design.md`

**Scope note:** This plan builds the tool only. Converting the six existing packages to their own `MetaData` plus an entry point touches other repositories and gets its own plan — the tool is fully testable here against fake packages and a real PostgreSQL container.

## Global Constraints

- Python `>=3.12`; tox matrix over 3.12, 3.13, 3.14.
- Runtime dependencies exactly: `sqlalchemy`, `alembic`, `psycopg`, `pydantic-settings`. **No `sqlmodel`** (packages hand over plain SQLAlchemy `MetaData`), **no CLI framework** (stdlib `argparse`).
- PostgreSQL only, target version 18. Rendering targets the PostgreSQL dialect.
- No `Dockerfile` — this is not a service.
- Licence EUPL-1.2; docs, code comments and commit messages in **English**.
- `src/` layout, PEP 420 namespace package: **no** `src/edutap/__init__.py`.
- Default output must be **byte-identical across runs**: deterministic ordering, no timestamp unless `--timestamp` is passed.
- Destructive statements (`DROP TABLE`, `DROP COLUMN`, `DROP CONSTRAINT`, `DROP INDEX`) are emitted commented out unless `--allow-destructive` is passed.
- `SET ROLE <role>;` header only when `--ddl-role` is passed.
- The canonical naming convention (`NAMING_CONVENTION`) is exported for reference but packages **copy** it; they must not import it.
- Test-first for every behaviour: failing test, then implementation.

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | packaging, dependencies, extras per known package, `edutap-dbdef` script, ruff/ty/pytest config |
| `src/edutap/db_definitions/__init__.py` | public exports: `SchemaDefinition`, `NAMING_CONVENTION` |
| `src/edutap/db_definitions/definition.py` | `SchemaDefinition` dataclass and its validation |
| `src/edutap/db_definitions/discovery.py` | entry-point loading, selection, topological ordering |
| `src/edutap/db_definitions/contract.py` | cross-package contract checks (convention, version tables, collisions) |
| `src/edutap/db_definitions/render.py` | baseline DDL rendering, file header, role header |
| `src/edutap/db_definitions/compare.py` | diff against a live schema, destructive filtering, foreign-table listing |
| `src/edutap/db_definitions/settings.py` | pydantic-settings for the connection |
| `src/edutap/db_definitions/execute.py` | applying SQL, dry-run |
| `src/edutap/db_definitions/cli.py` | the four subcommands |
| `tests/conftest.py` | fake packages with own metadata, entry-point injection, PostgreSQL container fixture |
| `tests/test_*.py` | one module per source module |
| `Makefile`, `tox.ini`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` | tooling |
| `docs/` | Sphinx + MyST, Diataxis structure |

---

### Task 1: Packaging, tooling and CLI skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `src/edutap/db_definitions/__init__.py`, `src/edutap/db_definitions/cli.py`
- Create: `Makefile`, `tox.ini`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`
- Test: `tests/test_cli_skeleton.py`

**Interfaces:**
- Consumes: nothing.
- Produces: console script `edutap-dbdef`; `cli.main(argv: list[str] | None = None) -> int`; `cli.build_parser() -> argparse.ArgumentParser`; `cli.COMMANDS: tuple[str, ...]`; package version readable as `edutap.db_definitions.__version__: str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_skeleton.py
import subprocess
import sys


def test_help_lists_the_four_subcommands():
    result = subprocess.run(
        [sys.executable, "-m", "edutap.db_definitions", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    for command in ("create", "diff", "check", "apply"):
        assert command in result.stdout


def test_main_returns_two_on_missing_subcommand():
    from edutap.db_definitions.cli import main

    assert main([]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_skeleton.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.db_definitions'`

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "edutap.db_definitions"
version = "0.1.0"
description = "Generates the database schema SQL for an eduTAP deployment from the installed eduTAP packages"
readme = "README.md"
requires-python = ">=3.12"
license = "EUPL-1.2"
authors = [{ name = "eduTAP" }]
dependencies = [
    "alembic>=1.13",
    "psycopg>=3.2",
    "pydantic-settings>=2.4",
    "sqlalchemy>=2.0",
]

[project.optional-dependencies]
dev = [
    "pdbp",
    "pytest>=8.2",
    "ruff>=0.6",
    "testcontainers[postgres]>=4.8",
    "ty",
]
docs = ["myst-parser", "sphinx>=8"]
# Known eduTAP packages that own tables. Pick at install time:
#   pip install "edutap.db_definitions[pass_builder,data_provider]"
data_provider = ["edutap.data_provider"]
pass_builder = ["edutap.pass_builder"]

[project.scripts]
edutap-dbdef = "edutap.db_definitions.cli:run"

[tool.hatch.build.targets.wheel]
packages = ["src/edutap"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["integration: needs a PostgreSQL container"]
addopts = "-m 'not integration'"

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "B", "I", "UP", "D", "S"]
ignore = ["D203", "D213"]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["D", "S101"]
```

- [ ] **Step 4: Write the package skeleton**

```python
# src/edutap/db_definitions/__init__.py
"""Generate the database schema SQL for an eduTAP deployment."""

from importlib.metadata import version


__version__ = version("edutap.db_definitions")

__all__ = ["__version__"]
```

```python
# src/edutap/db_definitions/__main__.py
"""Allow `python -m edutap.db_definitions`."""

from .cli import run


if __name__ == "__main__":
    run()
```

```python
# src/edutap/db_definitions/cli.py
"""Command line interface: create, diff, check, apply."""

import argparse
import sys


COMMANDS: tuple[str, ...] = ("create", "diff", "check", "apply")
"""The subcommands, in help order. The documentation test checks against this."""


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser with all four subcommands."""
    parser = argparse.ArgumentParser(
        prog="edutap-dbdef",
        description="Generate the database schema SQL for an eduTAP deployment.",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("create", help="render the baseline DDL")
    subcommands.add_parser("diff", help="render ALTER statements against a database")
    subcommands.add_parser("check", help="fail if the database deviates from the definitions")
    subcommands.add_parser("apply", help="apply a generated SQL file")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    if args.command is None:
        return 2
    return 0


def run() -> None:
    """Console script entry point."""
    sys.exit(main())
```

- [ ] **Step 5: Install and run the test**

Run: `uv venv && uv pip install -U -e ".[dev]" && .venv/bin/python -m pytest tests/test_cli_skeleton.py -v`
Expected: PASS (both tests)

- [ ] **Step 6: Write the tooling files**

```makefile
# Makefile
#
# Tools run through .venv/bin/python, not `uv run`: a bare `uv run` locks the
# whole project including the org-internal extras (edutap.data_provider,
# edutap.pass_builder), which no public index carries, so resolution fails.
# CI and tox are unaffected — they install the `dev` extra explicitly.
VENV   := .venv
PYTHON := $(VENV)/bin/python

.DEFAULT_GOAL := help

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  %-18s %s\n", $$1, $$2}'

venv: ## Create .venv and install the package with its dev extra
	test -d $(VENV) || uv venv
	uv pip install -U -e ".[dev]"

lint: venv ## Run ruff checks and the type checker
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests
	$(PYTHON) -m ty check src

reformat: venv ## Autoformat and autofix
	$(PYTHON) -m ruff format src tests
	$(PYTHON) -m ruff check --fix src tests

test-local: venv ## Unit tests, no database needed
	$(PYTHON) -m pytest -v

test-integration: venv ## Integration tests against a PostgreSQL container
	$(PYTHON) -m pytest -m integration -v
```

```ini
; tox.ini
[tox]
envlist = py312,py313,py314,lint
isolated_build = true

[testenv]
runner = uv-venv-runner
extras = dev
commands = pytest -v {posargs}

[testenv:lint]
basepython = py312
commands =
    ruff check src tests
    ruff format --check src tests
    ty check src
```

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.5
    hooks:
      - id: ruff-check
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
```

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.12", "3.13", "3.14"]
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v7
        with:
          python-version: ${{ matrix.python }}
      - run: uv pip install --system -e ".[dev]"
      - run: pytest -v
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v7
        with:
          python-version: "3.12"
      - run: uv pip install --system -e ".[dev]"
      - run: ruff check src tests
      - run: ruff format --check src tests
      - run: ty check src
```

- [ ] **Step 7: Verify lint and tests are green**

Run: `make lint && make test-local`
Expected: both pass. If `ty` reports anything, fix it before committing.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src tests Makefile tox.ini .pre-commit-config.yaml .github
git commit -m "feat: add packaging, tooling and the CLI skeleton"
```

---

### Task 2: `SchemaDefinition` and its validation

**Files:**
- Create: `src/edutap/db_definitions/definition.py`
- Modify: `src/edutap/db_definitions/__init__.py`
- Test: `tests/test_definition.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `NAMING_CONVENTION: dict[str, str]`
  - `SchemaDefinition(name: str, metadata: MetaData, requires: tuple[str, ...] = (), alembic_ini: str | None = None, version_table: str | None = None)` — frozen dataclass; `.table_names` property returning `tuple[str, ...]` sorted; `.validate() -> None` raising `DefinitionError`.
  - `DefinitionError(Exception)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_definition.py
import dataclasses

import pytest
from sqlalchemy import Column, Integer, MetaData, Table

from edutap.db_definitions.definition import NAMING_CONVENTION, DefinitionError, SchemaDefinition


def make_metadata(*table_names: str) -> MetaData:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    for name in table_names:
        Table(name, metadata, Column("id", Integer, primary_key=True))
    return metadata


def test_table_names_are_sorted():
    definition = SchemaDefinition(name="pkg", metadata=make_metadata("b", "a"))
    assert definition.table_names == ("a", "b")


def test_validate_accepts_a_minimal_definition():
    SchemaDefinition(name="pkg", metadata=make_metadata("a")).validate()


def test_validate_rejects_an_empty_name():
    with pytest.raises(DefinitionError, match="name"):
        SchemaDefinition(name="", metadata=make_metadata("a")).validate()


def test_validate_rejects_metadata_without_tables():
    with pytest.raises(DefinitionError, match="no tables"):
        SchemaDefinition(name="pkg", metadata=make_metadata()).validate()


def test_validate_rejects_a_version_table_that_is_also_a_data_table():
    metadata = make_metadata("a", "alembic_version_pkg")
    definition = SchemaDefinition(
        name="pkg", metadata=metadata, version_table="alembic_version_pkg"
    )
    with pytest.raises(DefinitionError, match="version_table"):
        definition.validate()


def test_definition_is_frozen():
    definition = SchemaDefinition(name="pkg", metadata=make_metadata("a"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        definition.name = "other"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_definition.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.db_definitions.definition'`

- [ ] **Step 3: Write the implementation**

```python
# src/edutap/db_definitions/definition.py
"""The contract a package uses to announce its tables."""

from dataclasses import dataclass
from dataclasses import field

from sqlalchemy import MetaData


NAMING_CONVENTION: dict[str, str] = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}
"""Canonical constraint naming convention.

Packages COPY this into their own ``MetaData(naming_convention=...)``. They must not
import it: that would give every service a runtime dependency on a tool which is
never deployed. ``contract.check_conventions`` verifies they match.
"""


class DefinitionError(Exception):
    """A package's schema definition is unusable."""


@dataclass(frozen=True)
class SchemaDefinition:
    """What one eduTAP package tells the generator about its tables."""

    name: str
    metadata: MetaData
    requires: tuple[str, ...] = field(default=())
    alembic_ini: str | None = None
    version_table: str | None = None

    @property
    def table_names(self) -> tuple[str, ...]:
        """Return the package's table names, sorted."""
        return tuple(sorted(self.metadata.tables))

    def validate(self) -> None:
        """Raise :class:`DefinitionError` if this definition cannot be used."""
        if not self.name.strip():
            raise DefinitionError("A schema definition needs a non-empty name.")
        if not self.metadata.tables:
            raise DefinitionError(f"{self.name}: metadata has no tables.")
        if self.version_table and self.version_table in self.metadata.tables:
            raise DefinitionError(
                f"{self.name}: version_table {self.version_table!r} is also a data table."
            )
```

- [ ] **Step 4: Export from the package root**

```python
# src/edutap/db_definitions/__init__.py
"""Generate the database schema SQL for an eduTAP deployment."""

from importlib.metadata import version

from .definition import NAMING_CONVENTION
from .definition import DefinitionError
from .definition import SchemaDefinition


__version__ = version("edutap.db_definitions")

__all__ = ["NAMING_CONVENTION", "DefinitionError", "SchemaDefinition", "__version__"]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_definition.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add src/edutap/db_definitions/definition.py src/edutap/db_definitions/__init__.py tests/test_definition.py
git commit -m "feat: add SchemaDefinition with validation and the naming convention"
```

---

### Task 3: Entry-point discovery, selection and ordering

**Files:**
- Create: `src/edutap/db_definitions/discovery.py`
- Create: `tests/conftest.py`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: `SchemaDefinition`, `DefinitionError` from Task 2.
- Produces:
  - `ENTRY_POINT_GROUP = "edutap.db_definitions"`
  - `iter_entry_points() -> Iterable[EntryPoint]` — the seam tests monkeypatch.
  - `load_definitions(include: Sequence[str] | None = None, exclude: Sequence[str] = ()) -> list[SchemaDefinition]` — ordered topologically, validated. Requested but uninstalled packages are logged through `logging.getLogger("edutap.db_definitions")` and skipped.
  - `DiscoveryError(Exception)` for cycles.

- [ ] **Step 1: Write the fake-package fixture**

```python
# tests/conftest.py
"""Fakes that stand in for installed eduTAP packages."""

from dataclasses import dataclass

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table

from edutap.db_definitions.definition import NAMING_CONVENTION, SchemaDefinition


@dataclass
class FakeEntryPoint:
    """Mimics importlib.metadata.EntryPoint for the discovery seam."""

    name: str
    value: object

    def load(self) -> object:
        """Return the object the entry point points at."""
        return self.value


def make_definition(
    name: str,
    *table_names: str,
    requires: tuple[str, ...] = (),
    convention: dict[str, str] | None = None,
    version_table: str | None = None,
) -> SchemaDefinition:
    """Build a SchemaDefinition with simple tables for tests."""
    metadata = MetaData(naming_convention=convention or NAMING_CONVENTION)
    for table_name in table_names:
        Table(
            table_name,
            metadata,
            Column("id", Integer, primary_key=True),
            Column("label", String(32), nullable=False),
        )
    return SchemaDefinition(
        name=name,
        metadata=metadata,
        requires=requires,
        version_table=version_table or f"alembic_version_{name.replace('.', '_')}",
    )


def make_definition_with_foreign_key(name: str) -> SchemaDefinition:
    """Build a definition whose second table references the first."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    parent = Table("parent", metadata, Column("id", Integer, primary_key=True))
    Table(
        "child",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey(parent.c.id), nullable=False),
    )
    return SchemaDefinition(name=name, metadata=metadata)


@pytest.fixture
def installed(monkeypatch):
    """Install fake packages into the discovery seam.

    Usage: `installed([make_definition("pkg.a", "table_a")])`
    """

    def install(definitions: list[SchemaDefinition]) -> None:
        from edutap.db_definitions import discovery

        points = [FakeEntryPoint(name=d.name, value=d) for d in definitions]
        monkeypatch.setattr(discovery, "iter_entry_points", lambda: points)

    return install
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_discovery.py
import pytest

from edutap.db_definitions.discovery import DiscoveryError, load_definitions
from tests.conftest import make_definition


def test_loads_all_installed_definitions(installed):
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert [d.name for d in load_definitions()] == ["pkg.a", "pkg.b"]


def test_include_narrows_the_selection(installed):
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert [d.name for d in load_definitions(include=["pkg.b"])] == ["pkg.b"]


def test_exclude_removes_a_package(installed):
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert [d.name for d in load_definitions(exclude=["pkg.a"])] == ["pkg.b"]


def test_a_requested_but_missing_package_is_skipped_not_fatal(installed, caplog):
    installed([make_definition("pkg.a", "table_a")])
    with caplog.at_level("WARNING"):
        loaded = load_definitions(include=["pkg.a", "pkg.absent"])
    assert [d.name for d in loaded] == ["pkg.a"]
    assert "pkg.absent" in caplog.text


def test_requires_determines_the_order(installed):
    installed(
        [
            make_definition("pkg.late", "table_late", requires=("pkg.early",)),
            make_definition("pkg.early", "table_early"),
        ]
    )
    assert [d.name for d in load_definitions()] == ["pkg.early", "pkg.late"]


def test_independent_packages_are_ordered_by_name(installed):
    installed([make_definition("pkg.z", "table_z"), make_definition("pkg.a", "table_a")])
    assert [d.name for d in load_definitions()] == ["pkg.a", "pkg.z"]


def test_a_dependency_cycle_is_an_error(installed):
    installed(
        [
            make_definition("pkg.a", "table_a", requires=("pkg.b",)),
            make_definition("pkg.b", "table_b", requires=("pkg.a",)),
        ]
    )
    with pytest.raises(DiscoveryError, match="cycle"):
        load_definitions()


def test_a_requires_outside_the_selection_is_ignored(installed):
    installed([make_definition("pkg.a", "table_a", requires=("pkg.absent",))])
    assert [d.name for d in load_definitions()] == ["pkg.a"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.db_definitions.discovery'`

- [ ] **Step 4: Write the implementation**

```python
# src/edutap/db_definitions/discovery.py
"""Find the schema definitions of the installed eduTAP packages."""

import logging
from collections.abc import Iterable
from collections.abc import Sequence
from graphlib import CycleError
from graphlib import TopologicalSorter
from importlib.metadata import EntryPoint
from importlib.metadata import entry_points

from .definition import SchemaDefinition


ENTRY_POINT_GROUP = "edutap.db_definitions"

logger = logging.getLogger("edutap.db_definitions")


class DiscoveryError(Exception):
    """The installed definitions cannot be ordered."""


def iter_entry_points() -> Iterable[EntryPoint]:
    """Return the entry points of the group. Seam for tests."""
    return entry_points(group=ENTRY_POINT_GROUP)


def _load_all() -> dict[str, SchemaDefinition]:
    definitions: dict[str, SchemaDefinition] = {}
    for point in iter_entry_points():
        definition = point.load()
        if callable(definition):
            definition = definition()
        definition.validate()
        definitions[definition.name] = definition
    return definitions


def _order(definitions: dict[str, SchemaDefinition]) -> list[SchemaDefinition]:
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for name, definition in sorted(definitions.items()):
        # A `requires` entry outside the selection is not an error: a site may run
        # only part of the stack. Ordering then simply has nothing to enforce.
        sorter.add(name, *(r for r in definition.requires if r in definitions))
    try:
        return [definitions[name] for name in sorter.static_order()]
    except CycleError as error:
        raise DiscoveryError(f"Dependency cycle between packages: {error.args[1]}") from error


def load_definitions(
    include: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
) -> list[SchemaDefinition]:
    """Return the selected definitions, validated and topologically ordered."""
    available = _load_all()
    if include is not None:
        for name in include:
            if name not in available:
                logger.warning("Package %s was requested but is not installed — skipping.", name)
        available = {name: d for name, d in available.items() if name in set(include)}
    if exclude:
        available = {name: d for name, d in available.items() if name not in set(exclude)}
    return _order(available)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_discovery.py -v`
Expected: PASS (8 tests). `TopologicalSorter.static_order()` yields dependencies first; independent nodes come out in insertion order, which is why `_order` iterates `sorted(definitions.items())`.

- [ ] **Step 6: Commit**

```bash
git add src/edutap/db_definitions/discovery.py tests/conftest.py tests/test_discovery.py
git commit -m "feat: discover, select and topologically order package definitions"
```

---

### Task 4: Cross-package contract checks

**Files:**
- Create: `src/edutap/db_definitions/contract.py`
- Test: `tests/test_contract.py`

**Interfaces:**
- Consumes: `SchemaDefinition`, `NAMING_CONVENTION`.
- Produces:
  - `ContractViolation(NamedTuple)` with fields `kind: str`, `message: str`.
  - `check_contract(definitions: Sequence[SchemaDefinition]) -> list[ContractViolation]` — empty list means the set is consistent.
  - `raise_on_violations(violations: Sequence[ContractViolation]) -> None` raising `ContractError`.
  - `ContractError(Exception)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contract.py
import pytest

from edutap.db_definitions.contract import ContractError, check_contract, raise_on_violations
from tests.conftest import make_definition


def test_a_consistent_set_has_no_violations():
    definitions = [make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")]
    assert check_contract(definitions) == []


def test_table_name_collision_is_reported():
    definitions = [make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")]
    violations = check_contract(definitions)
    assert [v.kind for v in violations] == ["table_collision"]
    assert "shared" in violations[0].message


def test_duplicate_version_table_is_reported():
    definitions = [
        make_definition("pkg.a", "table_a", version_table="alembic_version"),
        make_definition("pkg.b", "table_b", version_table="alembic_version"),
    ]
    assert [v.kind for v in check_contract(definitions)] == ["version_table_collision"]


def test_diverging_naming_convention_is_reported():
    definitions = [
        make_definition("pkg.a", "table_a"),
        make_definition("pkg.b", "table_b", convention={"pk": "primary_%(table_name)s"}),
    ]
    assert [v.kind for v in check_contract(definitions)] == ["naming_convention"]


def test_raise_on_violations_is_quiet_when_there_are_none():
    raise_on_violations([])


def test_raise_on_violations_raises_with_all_messages():
    definitions = [make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")]
    with pytest.raises(ContractError, match="shared"):
        raise_on_violations(check_contract(definitions))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.db_definitions.contract'`

- [ ] **Step 3: Write the implementation**

```python
# src/edutap/db_definitions/contract.py
"""Checks that a set of package definitions can share one database."""

from collections import defaultdict
from collections.abc import Sequence
from typing import NamedTuple

from .definition import NAMING_CONVENTION
from .definition import SchemaDefinition


class ContractError(Exception):
    """The selected packages cannot share one database."""


class ContractViolation(NamedTuple):
    """One problem found across the selected definitions."""

    kind: str
    message: str


def _table_collisions(definitions: Sequence[SchemaDefinition]) -> list[ContractViolation]:
    owners: dict[str, list[str]] = defaultdict(list)
    for definition in definitions:
        for table in definition.table_names:
            owners[table].append(definition.name)
    return [
        ContractViolation(
            "table_collision",
            f"Table {table!r} is defined by more than one package: {', '.join(names)}.",
        )
        for table, names in sorted(owners.items())
        if len(names) > 1
    ]


def _version_table_collisions(
    definitions: Sequence[SchemaDefinition],
) -> list[ContractViolation]:
    owners: dict[str, list[str]] = defaultdict(list)
    for definition in definitions:
        if definition.version_table:
            owners[definition.version_table].append(definition.name)
    return [
        ContractViolation(
            "version_table_collision",
            f"version_table {table!r} is claimed by: {', '.join(names)}. "
            "Each package needs its own migration history.",
        )
        for table, names in sorted(owners.items())
        if len(names) > 1
    ]


def _convention_deviations(definitions: Sequence[SchemaDefinition]) -> list[ContractViolation]:
    violations = []
    for definition in definitions:
        convention = dict(definition.metadata.naming_convention)
        if convention != NAMING_CONVENTION:
            violations.append(
                ContractViolation(
                    "naming_convention",
                    f"{definition.name}: naming convention differs from the canonical one "
                    f"(got {sorted(convention.items())}).",
                )
            )
    return violations


def check_contract(definitions: Sequence[SchemaDefinition]) -> list[ContractViolation]:
    """Return every contract violation across the given definitions."""
    return [
        *_table_collisions(definitions),
        *_version_table_collisions(definitions),
        *_convention_deviations(definitions),
    ]


def raise_on_violations(violations: Sequence[ContractViolation]) -> None:
    """Raise :class:`ContractError` listing all violations, if there are any."""
    if violations:
        raise ContractError("\n".join(f"[{v.kind}] {v.message}" for v in violations))
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_contract.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/edutap/db_definitions/contract.py tests/test_contract.py
git commit -m "feat: check that selected packages can share one database"
```

---

### Task 5: `create` — baseline DDL rendering plus CLI

**Files:**
- Create: `src/edutap/db_definitions/render.py`
- Modify: `src/edutap/db_definitions/cli.py`
- Test: `tests/test_render.py`, `tests/test_cli_create.py`

**Interfaces:**
- Consumes: `load_definitions`, `check_contract`, `raise_on_violations`, `SchemaDefinition`.
- Produces:
  - `render_create(definitions: Sequence[SchemaDefinition], ddl_role: str | None = None, timestamp: str | None = None) -> str`
  - `render_create_split(definitions: Sequence[SchemaDefinition], ddl_role: str | None = None, timestamp: str | None = None) -> dict[str, str]` — keyed by package name.
- CLI: `edutap-dbdef create [--out PATH] [--split DIR] [--ddl-role ROLE] [--timestamp] [--packages a,b] [--exclude a,b]`; writes to stdout when neither `--out` nor `--split` is given.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render.py
from edutap.db_definitions.render import render_create, render_create_split
from tests.conftest import make_definition, make_definition_with_foreign_key


def test_renders_create_table_if_not_exists():
    sql = render_create([make_definition("pkg.a", "table_a")])
    assert "CREATE TABLE IF NOT EXISTS table_a" in sql


def test_wraps_everything_in_one_transaction():
    sql = render_create([make_definition("pkg.a", "table_a")])
    assert sql.startswith("-- edutap-dbdef create")
    assert "BEGIN;" in sql
    assert sql.rstrip().endswith("COMMIT;")


def test_header_lists_the_packages():
    sql = render_create([make_definition("pkg.a", "table_a")])
    assert "-- packages: pkg.a" in sql


def test_no_timestamp_by_default_so_output_is_reproducible():
    first = render_create([make_definition("pkg.a", "table_a")])
    second = render_create([make_definition("pkg.a", "table_a")])
    assert first == second
    assert "generated:" not in first


def test_timestamp_is_included_when_given():
    sql = render_create([make_definition("pkg.a", "table_a")], timestamp="2026-07-30T12:00:00Z")
    assert "-- generated: 2026-07-30T12:00:00Z" in sql


def test_ddl_role_adds_a_set_role_header():
    sql = render_create([make_definition("pkg.a", "table_a")], ddl_role="edutap_ddl")
    assert "SET ROLE edutap_ddl;" in sql
    assert sql.index("SET ROLE") < sql.index("CREATE TABLE")


def test_without_ddl_role_there_is_no_set_role():
    assert "SET ROLE" not in render_create([make_definition("pkg.a", "table_a")])


def test_each_package_gets_a_section_comment():
    sql = render_create([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert "-- ===== pkg.a =====" in sql
    assert sql.index("-- ===== pkg.a =====") < sql.index("-- ===== pkg.b =====")


def test_tables_are_ordered_by_dependency():
    sql = render_create([make_definition_with_foreign_key("pkg.fk")])
    assert sql.index("CREATE TABLE IF NOT EXISTS parent") < sql.index(
        "CREATE TABLE IF NOT EXISTS child"
    )


def test_indexes_are_rendered_after_their_table():
    from sqlalchemy import Column, Index, Integer, MetaData, Table

    from edutap.db_definitions.definition import NAMING_CONVENTION, SchemaDefinition

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    table = Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("owner", Integer),
    )
    Index(None, table.c.owner)
    sql = render_create([SchemaDefinition(name="pkg.idx", metadata=metadata)])
    assert "CREATE INDEX IF NOT EXISTS ix_thing_owner" in sql
    assert sql.index("CREATE TABLE IF NOT EXISTS thing") < sql.index("CREATE INDEX")


def test_split_returns_one_document_per_package():
    documents = render_create_split(
        [make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")]
    )
    assert sorted(documents) == ["pkg.a", "pkg.b"]
    assert "table_a" in documents["pkg.a"]
    assert "table_b" not in documents["pkg.a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.db_definitions.render'`

- [ ] **Step 3: Write the implementation**

```python
# src/edutap/db_definitions/render.py
"""Render baseline DDL from package metadata, without touching a database."""

from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex
from sqlalchemy.schema import CreateTable

from .definition import SchemaDefinition


_DIALECT = postgresql.dialect()


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def _statement(construct: CreateTable | CreateIndex) -> str:
    return str(construct.compile(dialect=_DIALECT)).strip() + ";"


def _render_table(table: Table) -> list[str]:
    statements = [_statement(CreateTable(table, if_not_exists=True))]
    for index in sorted(table.indexes, key=lambda i: i.name or ""):
        statements.append(_statement(CreateIndex(index, if_not_exists=True)))
    return statements


def _render_package(definition: SchemaDefinition) -> list[str]:
    lines = [f"-- ===== {definition.name} ====="]
    # sorted_tables is dependency order: a table comes after everything it references.
    for table in definition.metadata.sorted_tables:
        lines.extend(_render_table(table))
    return lines


def _header(definitions: Sequence[SchemaDefinition], timestamp: str | None) -> list[str]:
    packages = ", ".join(f"{d.name} ({_package_version(d.name)})" for d in definitions)
    lines = ["-- edutap-dbdef create", f"-- packages: {packages}"]
    if timestamp:
        lines.append(f"-- generated: {timestamp}")
    return lines


def _document(
    definitions: Sequence[SchemaDefinition],
    body: list[str],
    ddl_role: str | None,
    timestamp: str | None,
) -> str:
    lines = [*_header(definitions, timestamp), "BEGIN;"]
    if ddl_role:
        lines.append(f"SET ROLE {ddl_role};")
    lines.extend(body)
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def render_create(
    definitions: Sequence[SchemaDefinition],
    ddl_role: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Render the baseline DDL of all definitions into one SQL document."""
    body: list[str] = []
    for definition in definitions:
        body.extend(_render_package(definition))
    return _document(definitions, body, ddl_role, timestamp)


def render_create_split(
    definitions: Sequence[SchemaDefinition],
    ddl_role: str | None = None,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Render one SQL document per package."""
    return {
        definition.name: _document(
            [definition], _render_package(definition), ddl_role, timestamp
        )
        for definition in definitions
    }
```

- [ ] **Step 4: Run the render tests**

Run: `.venv/bin/python -m pytest tests/test_render.py -v`
Expected: PASS (11 tests). If `CreateIndex(..., if_not_exists=True)` is unsupported by the installed SQLAlchemy, drop the argument and adjust the test to expect `CREATE INDEX ix_thing_owner`.

- [ ] **Step 5: Write the failing CLI test**

```python
# tests/test_cli_create.py
from edutap.db_definitions.cli import main
from tests.conftest import make_definition


def test_create_writes_the_file(installed, tmp_path, capsys):
    installed([make_definition("pkg.a", "table_a")])
    target = tmp_path / "create.sql"
    assert main(["create", "--out", str(target)]) == 0
    assert "CREATE TABLE IF NOT EXISTS table_a" in target.read_text()


def test_create_prints_to_stdout_without_out(installed, capsys):
    installed([make_definition("pkg.a", "table_a")])
    assert main(["create"]) == 0
    assert "CREATE TABLE IF NOT EXISTS table_a" in capsys.readouterr().out


def test_create_split_writes_one_file_per_package(installed, tmp_path):
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert main(["create", "--split", str(tmp_path)]) == 0
    assert (tmp_path / "pkg.a.sql").exists()
    assert (tmp_path / "pkg.b.sql").exists()


def test_create_honours_packages_and_ddl_role(installed, tmp_path):
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    target = tmp_path / "create.sql"
    assert main(["create", "--out", str(target), "--packages", "pkg.b", "--ddl-role", "ddl"]) == 0
    content = target.read_text()
    assert "table_b" in content
    assert "table_a" not in content
    assert "SET ROLE ddl;" in content


def test_create_fails_on_a_contract_violation(installed, capsys):
    installed([make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")])
    assert main(["create"]) == 1
    assert "table_collision" in capsys.readouterr().err
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_create.py -v`
Expected: FAIL — `create` currently returns 0 and writes nothing.

- [ ] **Step 7: Wire the `create` subcommand**

```python
# src/edutap/db_definitions/cli.py — replace the module with this
"""Command line interface: create, diff, check, apply."""

import argparse
import pathlib
import sys
from datetime import datetime
from datetime import timezone

from .contract import ContractError
from .contract import check_contract
from .contract import raise_on_violations
from .discovery import load_definitions
from .render import render_create
from .render import render_create_split


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--packages", type=_csv, default=None, help="only these packages")
    parser.add_argument("--exclude", type=_csv, default=[], help="skip these packages")


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser with all four subcommands."""
    parser = argparse.ArgumentParser(
        prog="edutap-dbdef",
        description="Generate the database schema SQL for an eduTAP deployment.",
    )
    subcommands = parser.add_subparsers(dest="command")

    create = subcommands.add_parser("create", help="render the baseline DDL")
    _add_selection_arguments(create)
    create.add_argument("--out", type=pathlib.Path, default=None, help="write to this file")
    create.add_argument(
        "--split", type=pathlib.Path, default=None, help="write one file per package into DIR"
    )
    create.add_argument("--ddl-role", default=None, help="emit SET ROLE <role> in the header")
    create.add_argument(
        "--timestamp", action="store_true", help="add a generation timestamp (breaks byte equality)"
    )

    subcommands.add_parser("diff", help="render ALTER statements against a database")
    subcommands.add_parser("check", help="fail if the database deviates from the definitions")
    subcommands.add_parser("apply", help="apply a generated SQL file")
    return parser


def _load_checked(args: argparse.Namespace):
    definitions = load_definitions(include=args.packages, exclude=args.exclude)
    raise_on_violations(check_contract(definitions))
    return definitions


def _command_create(args: argparse.Namespace) -> int:
    definitions = _load_checked(args)
    stamp = datetime.now(tz=timezone.utc).isoformat(timespec="seconds") if args.timestamp else None
    if args.split:
        args.split.mkdir(parents=True, exist_ok=True)
        for name, document in render_create_split(definitions, args.ddl_role, stamp).items():
            (args.split / f"{name}.sql").write_text(document)
        return 0
    document = render_create(definitions, args.ddl_role, stamp)
    if args.out:
        args.out.write_text(document)
    else:
        sys.stdout.write(document)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    if args.command is None:
        return 2
    try:
        if args.command == "create":
            return _command_create(args)
    except ContractError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    return 0


def run() -> None:
    """Console script entry point."""
    sys.exit(main())
```

- [ ] **Step 8: Run all tests**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS (all tests so far)

- [ ] **Step 9: Commit**

```bash
git add src/edutap/db_definitions/render.py src/edutap/db_definitions/cli.py tests/test_render.py tests/test_cli_create.py
git commit -m "feat: render baseline DDL and wire the create command"
```

---

### Task 6: Connection settings

**Files:**
- Create: `src/edutap/db_definitions/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` (pydantic-settings) with fields `dsn: str | None`, `host: str = "postgres"`, `port: int = 5432`, `database: str = "edutap"`, `user: str = "edutap_ddl"`, `password: SecretStr`, `sslmode: str | None`, `sslrootcert: str | None`, and `url() -> str` rendering a `postgresql+psycopg://` URL.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings.py
from edutap.db_definitions.settings import Settings


def test_reads_the_pg_variables(monkeypatch):
    monkeypatch.setenv("PGHOST", "db.example")
    monkeypatch.setenv("PGDATABASE", "edutap")
    monkeypatch.setenv("PGUSER", "edutap_ddl")
    monkeypatch.setenv("PGPASSWORD", "secret")
    url = Settings().url()
    assert url.startswith("postgresql+psycopg://edutap_ddl:secret@db.example:5432/edutap")


def test_prefixed_variables_win_over_pg(monkeypatch):
    monkeypatch.setenv("PGHOST", "from-pg")
    monkeypatch.setenv("EDUTAP_DBDEF_HOST", "from-prefix")
    assert "from-prefix" in Settings().url()


def test_dsn_overrides_everything(monkeypatch):
    monkeypatch.setenv("PGHOST", "ignored")
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", "postgresql+psycopg://u:p@h/db")
    assert Settings().url() == "postgresql+psycopg://u:p@h/db"


def test_ssl_settings_become_query_parameters(monkeypatch):
    monkeypatch.setenv("PGSSLMODE", "verify-full")
    monkeypatch.setenv("PGSSLROOTCERT", "/ca_cert.pem")
    url = Settings().url()
    assert "sslmode=verify-full" in url
    assert "sslrootcert=%2Fca_cert.pem" in url or "sslrootcert=/ca_cert.pem" in url


def test_password_is_not_leaked_by_repr(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "secret")
    assert "secret" not in repr(Settings())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.db_definitions.settings'`

- [ ] **Step 3: Write the implementation**

```python
# src/edutap/db_definitions/settings.py
"""Connection settings for the commands that talk to a database."""

from pydantic import AliasChoices
from pydantic import Field
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Reads `EDUTAP_DBDEF_*` or the standard `PG*` variables.

    The prefixed names take precedence, so a deployment that already exports `PG*`
    for other tools can still override a single value for this one.
    """

    model_config = SettingsConfigDict(extra="ignore")

    dsn: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EDUTAP_DBDEF_DSN", "DATABASE_URL"),
    )
    host: str = Field(default="postgres", validation_alias=AliasChoices("EDUTAP_DBDEF_HOST", "PGHOST"))
    port: int = Field(default=5432, validation_alias=AliasChoices("EDUTAP_DBDEF_PORT", "PGPORT"))
    database: str = Field(
        default="edutap", validation_alias=AliasChoices("EDUTAP_DBDEF_DATABASE", "PGDATABASE")
    )
    user: str = Field(
        default="edutap_ddl", validation_alias=AliasChoices("EDUTAP_DBDEF_USER", "PGUSER")
    )
    password: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("EDUTAP_DBDEF_PASSWORD", "PGPASSWORD"),
    )
    sslmode: str | None = Field(
        default=None, validation_alias=AliasChoices("EDUTAP_DBDEF_SSLMODE", "PGSSLMODE")
    )
    sslrootcert: str | None = Field(
        default=None, validation_alias=AliasChoices("EDUTAP_DBDEF_SSLROOTCERT", "PGSSLROOTCERT")
    )

    def url(self) -> str:
        """Return the SQLAlchemy URL for a synchronous psycopg connection."""
        if self.dsn:
            return self.dsn
        query = {}
        if self.sslmode:
            query["sslmode"] = self.sslmode
        if self.sslrootcert:
            query["sslrootcert"] = self.sslrootcert
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password.get_secret_value() or None,
            host=self.host,
            port=self.port,
            database=self.database,
            query=query,
        ).render_as_string(hide_password=False)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_settings.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/edutap/db_definitions/settings.py tests/test_settings.py
git commit -m "feat: add connection settings with PG and prefixed aliases"
```

---

### Task 7: `diff` — comparison against a live schema plus CLI

**Files:**
- Create: `src/edutap/db_definitions/compare.py`
- Modify: `src/edutap/db_definitions/cli.py`
- Modify: `tests/conftest.py` (PostgreSQL container fixture)
- Test: `tests/test_compare.py`

**Interfaces:**
- Consumes: `SchemaDefinition`, `Settings`, `load_definitions`, `check_contract`.
- Produces:
  - `merged_metadata(definitions: Sequence[SchemaDefinition]) -> MetaData`
  - `describe_changes(connection, definitions) -> list[str]` — human-readable deviations, empty when in sync.
  - `render_diff(connection, definitions, ddl_role: str | None = None, allow_destructive: bool = False) -> str`
  - `foreign_tables(connection, definitions) -> list[str]` — tables in the database owned by no selected package.
- CLI: `edutap-dbdef diff [--out PATH] [--ddl-role ROLE] [--allow-destructive] [--packages …] [--exclude …]`

Why a merged metadata: Alembic compares one `MetaData` against the whole schema, so
comparing package by package would report every other package's tables as
"remove_table". All selected packages are therefore copied into one `MetaData` via
`Table.to_metadata()`, and unknown table names are filtered out with `include_name`
so foreign tables in a shared database are left alone.

- [ ] **Step 1: Add the container fixture**

```python
# tests/conftest.py — append
@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Start a PostgreSQL container and return a psycopg URL for it."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:18-alpine", driver="psycopg") as container:
        yield container.get_connection_url()


@pytest.fixture
def engine(postgres_url):
    """A fresh engine on an empty public schema."""
    from sqlalchemy import create_engine, text

    engine = create_engine(postgres_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_compare.py
import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, text

from edutap.db_definitions.compare import (
    describe_changes,
    foreign_tables,
    merged_metadata,
    render_diff,
)
from edutap.db_definitions.definition import NAMING_CONVENTION, SchemaDefinition
from edutap.db_definitions.render import render_create
from tests.conftest import make_definition

pytestmark = pytest.mark.integration


def definition_with_extra_column(name: str) -> SchemaDefinition:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "table_a",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("label", String(32), nullable=False),
        Column("note", String(64), nullable=True),
    )
    return SchemaDefinition(name=name, metadata=metadata)


def test_merged_metadata_holds_all_tables():
    merged = merged_metadata([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert sorted(merged.tables) == ["table_a", "table_b"]


def test_no_changes_after_applying_create(engine):
    definitions = [make_definition("pkg.a", "table_a")]
    with engine.begin() as connection:
        connection.execute(text(render_create(definitions)))
    with engine.connect() as connection:
        assert describe_changes(connection, definitions) == []
        assert render_diff(connection, definitions).count("ALTER") == 0


def test_added_column_is_reported_and_rendered(engine):
    with engine.begin() as connection:
        connection.execute(text(render_create([make_definition("pkg.a", "table_a")])))
    definitions = [definition_with_extra_column("pkg.a")]
    with engine.connect() as connection:
        changes = describe_changes(connection, definitions)
        sql = render_diff(connection, definitions)
    assert any("note" in change for change in changes)
    assert "ALTER TABLE table_a ADD COLUMN note" in sql


def test_destructive_statements_are_commented_out_by_default(engine):
    with engine.begin() as connection:
        connection.execute(text(render_create([definition_with_extra_column("pkg.a")])))
    definitions = [make_definition("pkg.a", "table_a")]
    with engine.connect() as connection:
        sql = render_diff(connection, definitions)
    assert "-- DESTRUCTIVE" in sql
    assert not any(
        line.strip().startswith("ALTER") and "DROP COLUMN" in line for line in sql.splitlines()
    )


def test_destructive_statements_can_be_enabled(engine):
    with engine.begin() as connection:
        connection.execute(text(render_create([definition_with_extra_column("pkg.a")])))
    definitions = [make_definition("pkg.a", "table_a")]
    with engine.connect() as connection:
        sql = render_diff(connection, definitions, allow_destructive=True)
    assert any(
        line.strip().startswith("ALTER") and "DROP COLUMN" in line for line in sql.splitlines()
    )


def test_foreign_tables_are_listed_and_left_alone(engine):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE not_ours (id integer primary key)"))
        connection.execute(text(render_create([make_definition("pkg.a", "table_a")])))
    definitions = [make_definition("pkg.a", "table_a")]
    with engine.connect() as connection:
        assert foreign_tables(connection, definitions) == ["not_ours"]
        assert "not_ours" not in render_diff(connection, definitions)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -m integration tests/test_compare.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.db_definitions.compare'`

- [ ] **Step 4: Write the implementation**

```python
# src/edutap/db_definitions/compare.py
"""Compare package definitions against a live schema."""

import io
from collections.abc import Sequence

from alembic.autogenerate import compare_metadata
from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData
from sqlalchemy import inspect

from .definition import NAMING_CONVENTION
from .definition import SchemaDefinition


_DESTRUCTIVE = ("DROP TABLE", "DROP COLUMN", "DROP CONSTRAINT", "DROP INDEX")


def merged_metadata(definitions: Sequence[SchemaDefinition]) -> MetaData:
    """Copy all definitions' tables into one MetaData for comparison."""
    merged = MetaData(naming_convention=NAMING_CONVENTION)
    for definition in definitions:
        for table in definition.metadata.sorted_tables:
            table.to_metadata(merged)
    return merged


def _known_names(definitions: Sequence[SchemaDefinition]) -> set[str]:
    return {name for definition in definitions for name in definition.table_names}


def _context(connection, definitions: Sequence[SchemaDefinition]) -> MigrationContext:
    known = _known_names(definitions)

    def include_name(name, type_, parent_names):
        # Tables of packages this site did not select are none of our business.
        if type_ == "table" and name is not None:
            return name in known
        return True

    return MigrationContext.configure(
        connection=connection,
        opts={"include_name": include_name, "compare_type": True},
    )


def describe_changes(connection, definitions: Sequence[SchemaDefinition]) -> list[str]:
    """Return one readable line per deviation; empty when the schema is in sync."""
    diffs = compare_metadata(_context(connection, definitions), merged_metadata(definitions))
    return [repr(diff) for diff in diffs]


def foreign_tables(connection, definitions: Sequence[SchemaDefinition]) -> list[str]:
    """Return the database's tables that belong to no selected package."""
    known = _known_names(definitions)
    present = set(inspect(connection).get_table_names())
    return sorted(name for name in present - known if not name.startswith("alembic_version"))


def render_diff(
    connection,
    definitions: Sequence[SchemaDefinition],
    ddl_role: str | None = None,
    allow_destructive: bool = False,
) -> str:
    """Render the ALTER statements that bring the database in line."""
    migrations = produce_migrations(_context(connection, definitions), merged_metadata(definitions))
    buffer = io.StringIO()
    offline = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    operations = Operations(offline)
    for operation in migrations.upgrade_ops.ops:
        operations.invoke(operation)

    lines = [
        "-- edutap-dbdef diff",
        "-- Limits: renames appear as drop + add, some type changes render incompletely,",
        "-- and data migrations are out of scope. Read this before applying it.",
        "BEGIN;",
    ]
    if ddl_role:
        lines.append(f"SET ROLE {ddl_role};")
    for line in buffer.getvalue().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        destructive = any(marker in stripped.upper() for marker in _DESTRUCTIVE)
        if destructive and not allow_destructive:
            lines.append(f"-- DESTRUCTIVE, enable with --allow-destructive: {stripped}")
        else:
            lines.append(stripped)
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 5: Run the integration tests**

Run: `.venv/bin/python -m pytest -m integration tests/test_compare.py -v`
Expected: PASS (6 tests). Docker must be running. If `Operations.invoke` writes statements without a trailing semicolon, append one in the loop; adjust the tests only if the SQL is semantically identical.

- [ ] **Step 6: Wire the `diff` subcommand**

In `build_parser`, replace the bare `diff` parser:

```python
    diff = subcommands.add_parser("diff", help="render ALTER statements against a database")
    _add_selection_arguments(diff)
    diff.add_argument("--out", type=pathlib.Path, default=None, help="write to this file")
    diff.add_argument("--ddl-role", default=None, help="emit SET ROLE <role> in the header")
    diff.add_argument(
        "--allow-destructive", action="store_true", help="emit DROP statements uncommented"
    )
```

Add the command function and dispatch:

```python
def _connect():
    from sqlalchemy import create_engine

    from .settings import Settings

    return create_engine(Settings().url())


def _command_diff(args: argparse.Namespace) -> int:
    definitions = _load_checked(args)
    engine = _connect()
    try:
        with engine.connect() as connection:
            document = render_diff(
                connection, definitions, args.ddl_role, args.allow_destructive
            )
            skipped = foreign_tables(connection, definitions)
    finally:
        engine.dispose()
    if skipped:
        sys.stderr.write(f"Ignored tables of other owners: {', '.join(skipped)}\n")
    if args.out:
        args.out.write_text(document)
    else:
        sys.stdout.write(document)
    return 0
```

with `from .compare import foreign_tables, render_diff` at the top and
`if args.command == "diff": return _command_diff(args)` in `main`.

- [ ] **Step 7: Run everything**

Run: `.venv/bin/python -m pytest -v && .venv/bin/python -m pytest -m integration -v && make lint`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add src/edutap/db_definitions/compare.py src/edutap/db_definitions/cli.py tests/conftest.py tests/test_compare.py
git commit -m "feat: diff the definitions against a live schema"
```

---

### Task 8: `check` — the drift gate

**Files:**
- Modify: `src/edutap/db_definitions/cli.py`
- Test: `tests/test_cli_check.py`

**Interfaces:**
- Consumes: `describe_changes`, `check_contract`, `load_definitions`.
- Produces: CLI `edutap-dbdef check [--packages …] [--exclude …]` — exit 0 when in sync, exit 1 with a report otherwise.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_check.py
import pytest
from sqlalchemy import text

from edutap.db_definitions.cli import main
from edutap.db_definitions.render import render_create
from tests.conftest import make_definition

pytestmark = pytest.mark.integration


def test_check_passes_when_the_schema_matches(installed, engine, monkeypatch, capsys):
    definitions = [make_definition("pkg.a", "table_a")]
    installed(definitions)
    with engine.begin() as connection:
        connection.execute(text(render_create(definitions)))
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", str(engine.url.render_as_string(hide_password=False)))
    assert main(["check"]) == 0
    assert "in sync" in capsys.readouterr().out


def test_check_fails_and_reports_when_a_table_is_missing(installed, engine, monkeypatch, capsys):
    installed([make_definition("pkg.a", "table_a")])
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", str(engine.url.render_as_string(hide_password=False)))
    assert main(["check"]) == 1
    assert "table_a" in capsys.readouterr().err


def test_check_fails_on_a_contract_violation_without_touching_the_database(installed, capsys):
    installed([make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")])
    assert main(["check"]) == 1
    assert "table_collision" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -m integration tests/test_cli_check.py -v`
Expected: FAIL — `check` returns 0 unconditionally.

- [ ] **Step 3: Wire the `check` subcommand**

In `build_parser`, replace the bare `check` parser:

```python
    check = subcommands.add_parser(
        "check", help="fail if the database deviates from the definitions"
    )
    _add_selection_arguments(check)
```

Add the command and dispatch (`from .compare import describe_changes`):

```python
def _command_check(args: argparse.Namespace) -> int:
    definitions = _load_checked(args)
    engine = _connect()
    try:
        with engine.connect() as connection:
            changes = describe_changes(connection, definitions)
            skipped = foreign_tables(connection, definitions)
    finally:
        engine.dispose()
    if skipped:
        sys.stderr.write(f"Ignored tables of other owners: {', '.join(skipped)}\n")
    if changes:
        sys.stderr.write("Schema deviates from the definitions:\n")
        for change in changes:
            sys.stderr.write(f"  {change}\n")
        return 1
    sys.stdout.write("Schema is in sync with the definitions.\n")
    return 0
```

`_load_checked` runs the contract check **before** `_connect()`, so a contract
violation never needs a database. Careful with the third test though: asserting only
the exit code and the message does **not** pin that ordering, because `create_engine`
and `Settings().url()` are both lazy and succeed without any environment — the test
would pass with the order reversed. Pin it by replacing `cli._connect` with a
function that raises if it is called (corrected during execution, 2026-07-30).

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest -m integration tests/test_cli_check.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/edutap/db_definitions/cli.py tests/test_cli_check.py
git commit -m "feat: add the check command as a drift gate"
```

---

### Task 9: `apply` — executing a generated file

**Files:**
- Create: `src/edutap/db_definitions/execute.py`
- Modify: `src/edutap/db_definitions/cli.py`
- Test: `tests/test_execute.py`

**Interfaces:**
- Consumes: `Settings`.
- Produces:
  - `apply_sql(sql: str, url: str, dry_run: bool = False) -> int` — returns the number of executed statements; 0 on dry-run.
  - CLI `edutap-dbdef apply FILE [--dry-run]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execute.py
import pytest
from sqlalchemy import inspect, text

from edutap.db_definitions.cli import main
from edutap.db_definitions.execute import apply_sql
from edutap.db_definitions.render import render_create
from tests.conftest import make_definition

pytestmark = pytest.mark.integration


def test_apply_creates_the_tables(engine):
    sql = render_create([make_definition("pkg.a", "table_a")])
    executed = apply_sql(sql, str(engine.url.render_as_string(hide_password=False)))
    assert executed >= 1
    assert "table_a" in inspect(engine).get_table_names()


def test_dry_run_changes_nothing(engine):
    sql = render_create([make_definition("pkg.a", "table_a")])
    assert apply_sql(sql, str(engine.url.render_as_string(hide_password=False)), dry_run=True) == 0
    assert "table_a" not in inspect(engine).get_table_names()


def test_apply_is_repeatable(engine):
    sql = render_create([make_definition("pkg.a", "table_a")])
    url = str(engine.url.render_as_string(hide_password=False))
    apply_sql(sql, url)
    apply_sql(sql, url)
    assert "table_a" in inspect(engine).get_table_names()


def test_cli_apply_reads_the_file(engine, tmp_path, monkeypatch):
    target = tmp_path / "create.sql"
    target.write_text(render_create([make_definition("pkg.a", "table_a")]))
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", str(engine.url.render_as_string(hide_password=False)))
    assert main(["apply", str(target)]) == 0
    assert "table_a" in inspect(engine).get_table_names()


def test_a_failing_statement_rolls_everything_back(engine):
    from sqlalchemy.exc import ProgrammingError

    url = str(engine.url.render_as_string(hide_password=False))
    sql = "BEGIN;\nCREATE TABLE good (id integer primary key);\nSELECT nonexistent_function();\nCOMMIT;\n"
    with pytest.raises(ProgrammingError):
        apply_sql(sql, url)
    assert "good" not in inspect(engine).get_table_names()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest -m integration tests/test_execute.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'edutap.db_definitions.execute'`

- [ ] **Step 3: Write the implementation**

```python
# src/edutap/db_definitions/execute.py
"""Apply a generated SQL document to a database."""

import logging

from sqlalchemy import create_engine


logger = logging.getLogger("edutap.db_definitions")


def apply_sql(sql: str, url: str, dry_run: bool = False) -> int:
    """Execute the document and return the number of executed statements.

    The document brings its own transaction control (``BEGIN;`` / ``COMMIT;``), so the
    connection runs in AUTOCOMMIT and the script is handed to the driver as one unit.
    Wrapping it in SQLAlchemy's own transaction instead would nest two transactions:
    the script's ``COMMIT`` would end the outer one and the block exit would then fail.
    A failing statement aborts the script's transaction, so nothing is left behind.
    """
    if dry_run:
        logger.info("Dry run: %d characters of SQL would be executed.", len(sql))
        return 0
    engine = create_engine(url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(sql)
    finally:
        engine.dispose()
    # Count only body statements: BEGIN/COMMIT/SET ROLE are transaction control,
    # comments are not statements, and a multi-line CREATE TABLE counts once
    # (corrected during execution 2026-07-30 — the naive per-line count reported
    # three statements for a document that created one table).
    return _count_statements(sql)
```

- [ ] **Step 4: Wire the `apply` subcommand**

In `build_parser`, replace the bare `apply` parser:

```python
    apply_command = subcommands.add_parser("apply", help="apply a generated SQL file")
    apply_command.add_argument("file", type=pathlib.Path, help="the SQL file to apply")
    apply_command.add_argument(
        "--dry-run", action="store_true", help="do not execute anything"
    )
```

Add the command and dispatch:

```python
def _command_apply(args: argparse.Namespace) -> int:
    from .execute import apply_sql
    from .settings import Settings

    executed = apply_sql(args.file.read_text(), Settings().url(), args.dry_run)
    sys.stdout.write(
        "Dry run, nothing executed.\n" if args.dry_run else f"Executed {executed} statements.\n"
    )
    return 0
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest -m integration tests/test_execute.py -v && .venv/bin/python -m pytest -v && make lint`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/edutap/db_definitions/execute.py src/edutap/db_definitions/cli.py tests/test_execute.py
git commit -m "feat: apply generated SQL with a dry-run mode"
```

---

### Task 10: Documentation and release readiness

**Files:**
- Modify: `README.md`
- Create: `docs/index.md`, `docs/tutorial.md`, `docs/how-to.md`, `docs/reference.md`, `docs/explanation.md`, `docs/conf.py`
- Create: `CHANGES.md`
- Test: `tests/test_docs.py`

**Interfaces:**
- Consumes: everything built so far.
- Produces: a buildable Sphinx site and a README that matches the implemented CLI.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_docs.py
import pathlib
import re
import subprocess
import sys

from edutap.db_definitions.cli import COMMANDS

ROOT = pathlib.Path(__file__).parent.parent


def cli_help() -> str:
    """Return the top-level help plus the help of every subcommand."""
    texts = [_run([])]
    texts.extend(_run([command]) for command in COMMANDS)
    return "\n".join(texts)


def _run(arguments: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "edutap.db_definitions", *arguments, "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_every_subcommand_is_documented():
    reference = (ROOT / "docs" / "reference.md").read_text()
    for command in COMMANDS:
        assert f"`{command}`" in reference, f"{command} missing from reference.md"


def test_readme_mentions_no_flag_the_cli_does_not_have():
    readme = (ROOT / "README.md").read_text()
    help_text = cli_help()
    for flag in sorted(set(re.findall(r"--[a-z][a-z-]+", readme))):
        assert flag in help_text, f"{flag} in README but not in the CLI"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_docs.py -v`
Expected: FAIL — `docs/reference.md` does not exist.

- [ ] **Step 3: Write the documentation**

`docs/index.md` — one paragraph on purpose plus a toctree over the four pages.

`docs/tutorial.md` — walk through: create a venv, install with one extra, run
`edutap-dbdef create --out create.sql`, read the file, start a PostgreSQL container,
`edutap-dbdef apply create.sql`, then `edutap-dbdef check` showing "in sync".

`docs/how-to.md` — three how-tos:
1. *Announce a package's tables*: the `MetaData` + `Base` snippet, the copied
   `NAMING_CONVENTION`, the `SchemaDefinition` object and the entry-point block —
   copied verbatim from the spec's *Package contract* section.
2. *Generate the schema for the LMU deployment*: install with the extras, run
   `create --ddl-role edutap_ddl --out schema.sql`, commit the file into the deploy
   repository, apply it with `swarmed_postgres:run_sql`. Explain why `--ddl-role`
   matters: `run_sql` connects as `postgres`, and the deployment's
   `ALTER DEFAULT PRIVILEGES` only grant on tables owned by `edutap_ddl`.
3. *Use `check` as a pre-deploy gate*: exit codes, and that it needs read-only
   access only.

`docs/reference.md` — every subcommand with its flags, the environment variables
(`EDUTAP_DBDEF_*` and `PG*`), the `SchemaDefinition` fields and the exceptions.

`docs/explanation.md` — why the tool exists (the no-DDL-for-services rule), why per
package `MetaData` (the `SQLModel.metadata` singleton), why the diff is generated
rather than hand-written, and the known limits of autogenerate (renames, type
changes, no data migrations).

`docs/conf.py`:

```python
"""Sphinx configuration."""

project = "edutap.db_definitions"
extensions = ["myst_parser"]
myst_enable_extensions = ["colon_fence", "deflist"]
exclude_patterns = ["_build"]
html_theme = "alabaster"
```

- [ ] **Step 4: Update README and CHANGES**

`README.md` gets: what it is, the "never deployed" sentence, install with extras,
the four commands with a one-line example each, and a pointer to `docs/`. Replace
the "Status: planned" line — it is no longer planned.

`CHANGES.md`:

```markdown
# Changelog

## 0.1.0 (unreleased)

- Initial release: `create`, `diff`, `check` and `apply` over the
  `edutap.db_definitions` entry-point group.
```

- [ ] **Step 5: Build the docs and run the tests**

Run: `uv pip install -e ".[docs]" && .venv/bin/python -m sphinx -W docs docs/_build/html && .venv/bin/python -m pytest -v`
Expected: docs build without warnings, all tests pass

- [ ] **Step 6: Full verification**

Run: `make lint && make test-local && make test-integration && uvx tox -e py312`
Expected: everything green

- [ ] **Step 7: Commit**

```bash
git add README.md CHANGES.md docs tests/test_docs.py
git commit -m "docs: add the Sphinx documentation and align the README"
```

---

## Verification checklist

- [ ] `make lint` clean (ruff check, ruff format, ty)
- [ ] `make test-local` green without Docker running
- [ ] `make test-integration` green with Docker running
- [ ] `uvx tox` green over 3.12, 3.13, 3.14
- [ ] `edutap-dbdef create` output is byte-identical across two runs
- [ ] `edutap-dbdef check` exits 1 on a missing table and 0 after `apply`
- [ ] `sphinx-build -W` builds without warnings
- [ ] No `Dockerfile` in the repository
- [ ] `pip show edutap.db_definitions` lists exactly four runtime dependencies

## Follow-up work (separate plans)

1. **Convert the six existing packages** — own `MetaData`, copied naming
   convention, `SchemaDefinition` plus entry point, and removal of the `create_all`
   calls at app start. One task per repository: `edutap.pass_builder`,
   `lmu_edutap_full_view`, `edutap.apple_wallet_vas_signing_service`,
   `edutap.apple_wallet_vas_account_binding_callback`,
   `edutap.apple_wallet_vas_web_service`, `fastapi-auth-saml-federated`.
2. **Alembic offline mode** — `alembic upgrade <current>:head --sql` per package for
   packages with data migrations.
3. **Retire `lmu_db_migrate`** once the LMU deployment uses the generated SQL.
