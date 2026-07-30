"""Render baseline DDL from package metadata, without touching a database."""

import re
from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from sqlalchemy import MetaData, Table, create_mock_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import DDLElement

from .definition import NAMING_CONVENTION, SchemaDefinition

_MOCK_URL = "postgresql+psycopg://"

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

_PREPARER = postgresql.dialect().identifier_preparer

_NO_ROLE_NOTE = (
    "-- NOTE: generated without --ddl-role; objects will be owned by whichever "
    "user applies this file."
)
"""Header line that makes a missing ``--ddl-role`` visible.

Applied through the LMU deployment as `postgres`, a file without a role header
produces `postgres`-owned tables, and the deployment's default-privilege grants
silently do not apply. A reviewer of a committed ``schema.sql`` must be able to
tell "deliberately no role" from "forgot the flag".
"""


class RenderError(Exception):
    """The requested SQL document cannot be rendered."""


_REPEATABLE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS "),
    ("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS "),
    ("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS "),
    ("CREATE SEQUENCE ", "CREATE SEQUENCE IF NOT EXISTS "),
)
"""Statements PostgreSQL itself can make repeatable, via ``IF NOT EXISTS``."""

_TYPE_PREFIXES: tuple[str, ...] = ("CREATE TYPE ", "CREATE DOMAIN ")
"""Statements creating a type, which belongs to the schema rather than a table."""

_TYPES_SECTION = "-- ===== types ====="
"""Section comment for the types, which no single package owns."""

_GUARDED_PREFIXES: tuple[str, ...] = (*_TYPE_PREFIXES, "ALTER TABLE ")
"""Statements with no ``IF NOT EXISTS`` form, wrapped in a DO block instead.

PostgreSQL has no ``CREATE TYPE IF NOT EXISTS``, and no such form for the
``ALTER TABLE ... ADD CONSTRAINT`` that a deferred (``use_alter``) foreign key
renders as. The spec promises a repeatable document, so each of these is
wrapped in a block that swallows exactly ``duplicate_object`` and nothing else.
"""


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def _require_definitions(definitions: Sequence[SchemaDefinition]) -> None:
    """Refuse to render a document that would create nothing.

    An empty selection otherwise yields a valid-looking file: it applies without
    complaint and leaves the database untouched, so a typo in ``--packages`` or a
    missing extra shows up as an empty schema much later.
    """
    if not definitions:
        raise RenderError(
            "No packages selected — nothing to render. Check --packages/--exclude "
            "and which eduTAP packages are installed."
        )


def merged_metadata(definitions: Sequence[SchemaDefinition]) -> MetaData:
    """Copy all definitions' tables into one MetaData.

    Foreign keys resolve by table name within one MetaData. A key that crosses a
    package boundary — the very thing ``requires`` exists for — therefore cannot
    resolve while each package's MetaData is looked at on its own: SQLAlchemy
    raises ``NoReferencedTableError`` no matter which order the packages are
    processed in, because the obstacle is MetaData resolution and not ordering.
    Merging first makes the target reachable, and the merged ``sorted_tables``
    then yields the globally correct dependency order.

    Iterating ``metadata.tables.values()`` rather than ``sorted_tables`` is
    load-bearing: ``sorted_tables`` resolves foreign keys and would raise here.

    Merging is also what keeps a comparison honest — Alembic compares one
    MetaData against the whole schema, and comparing package by package would
    report every other package's tables as removed.
    """
    merged = MetaData(naming_convention=_shared_naming_convention(definitions))
    for definition in definitions:
        for table in definition.metadata.tables.values():
            table.to_metadata(merged)
    return merged


def _shared_naming_convention(definitions: Sequence[SchemaDefinition]) -> Mapping[str, Any]:
    """Return the definitions' naming convention.

    The contract check guarantees all selected packages share one convention, so
    the first one is representative. Falling back to the canonical constant keeps
    this callable for an empty sequence.
    """
    for definition in definitions:
        return dict(definition.metadata.naming_convention)
    return dict(NAMING_CONVENTION)


def _emit(metadata: MetaData, tables: Sequence[Table]) -> tuple[list[str], list[str]]:
    """Return the DDL SQLAlchemy itself emits for these tables, types apart.

    Uses SQLAlchemy's own schema generator through a mock engine instead of
    compiling ``CreateTable``/``CreateIndex`` by hand. Hand-rolled emission
    silently drops everything else a schema needs: enum types, explicit
    sequences and the ``ALTER TABLE ... ADD CONSTRAINT`` of a deferred foreign
    key. ``create_all`` produces all of them, in dependency order, and is the
    same mechanism the ``metadata.create_all`` calls this tool replaces used.

    Type creation comes back separately because SQLAlchemy scopes it to the
    *metadata*, not to the table subset: every ``create_all`` on the merged
    metadata emits every enum type of every selected package. Repeating those in
    each package's section would tell a reviewer that one package creates another
    package's type, so the caller places them once instead.
    """
    statements: list[str] = []

    def dump(construct: DDLElement, *args: object, **kwargs: object) -> None:
        statements.append(str(construct.compile(dialect=engine.dialect)).strip())

    engine = create_mock_engine(_MOCK_URL, dump)
    metadata.create_all(engine, tables=list(tables), checkfirst=False)
    types = [_repeatable(s) for s in statements if s.upper().startswith(_TYPE_PREFIXES)]
    rest = [_repeatable(s) for s in statements if not s.upper().startswith(_TYPE_PREFIXES)]
    return types, rest


def _repeatable(statement: str) -> str:
    """Make one statement safe to run against a schema that already has it."""
    upper = statement.upper()
    for prefix, replacement in _REPEATABLE_PREFIXES:
        if upper.startswith(prefix):
            return replacement + statement[len(prefix) :] + ";"
    if upper.startswith(_GUARDED_PREFIXES):
        body = "\n".join(f"    {line}" for line in statement.splitlines())
        return f"DO $$ BEGIN\n{body};\nEXCEPTION WHEN duplicate_object THEN NULL;\nEND $$;"
    return statement + ";"


def _render_package(merged: MetaData, definition: SchemaDefinition) -> tuple[list[str], list[str]]:
    """Render one package's section, in the merged metadata's dependency order.

    ``merged.sorted_tables`` is the global dependency order: a table comes after
    everything it references, across package boundaries too. Taking this
    package's slice of that order keeps the per-package sections a reviewer
    reads by while the statements inside them stay correctly ordered.

    Returns the type statements separately, for the caller to place once.
    """
    owned = set(definition.metadata.tables)
    tables = [table for table in merged.sorted_tables if table.key in owned]
    types, rest = _emit(merged, tables)
    return types, [f"-- ===== {definition.name} =====", *rest]


def package_provenance(definitions: Sequence[SchemaDefinition]) -> str:
    """Return the ``name (version)`` list a document's header records."""
    return ", ".join(f"{d.name} ({_package_version(d.name)})" for d in definitions)


def _header(definitions: Sequence[SchemaDefinition], timestamp: str | None) -> list[str]:
    lines = ["-- edutap-dbdef create", f"-- packages: {package_provenance(definitions)}"]
    if timestamp:
        lines.append(f"-- generated: {timestamp}")
    return lines


def _role_statement(ddl_role: str) -> str:
    """Return the ``SET ROLE`` line for a validated, correctly quoted role name.

    Two distinct harms are prevented here. The obvious one is injection: this
    string ends up in a file that a superuser applies. The quiet one is case
    folding — an unquoted ``Edutap_DDL`` becomes ``edutap_ddl``, so the objects
    end up owned by a different role than the one asked for, with no error.
    """
    if not _IDENTIFIER.match(ddl_role):
        raise RenderError(
            f"{ddl_role!r} is not a valid PostgreSQL identifier and cannot be used as a DDL role."
        )
    return f"SET ROLE {_PREPARER.quote(ddl_role)};"


def document(
    header_lines: Sequence[str],
    body: Sequence[str],
    ddl_role: str | None,
) -> str:
    """Assemble one reviewable SQL document.

    The single place where the header of a generated document is built: `create`
    and `diff` share it, so a hardening of this load-bearing part cannot apply to
    only one of the two commands.
    """
    lines = [*header_lines, "BEGIN;"]
    lines.append(_role_statement(ddl_role) if ddl_role else _NO_ROLE_NOTE)
    lines.extend(body)
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def render_create(
    definitions: Sequence[SchemaDefinition],
    ddl_role: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Render the baseline DDL of all definitions into one SQL document."""
    _require_definitions(definitions)
    merged = merged_metadata(definitions)
    types: list[str] = []
    sections: list[str] = []
    for definition in definitions:
        package_types, package_body = _render_package(merged, definition)
        # A type belongs to the schema, not to a package, and SQLAlchemy reports
        # all of the metadata's types for every package. One copy, before the
        # first table that could use it.
        types.extend(statement for statement in package_types if statement not in types)
        sections.extend(package_body)
    preamble = [_TYPES_SECTION, *types] if types else []
    return document(_header(definitions, timestamp), [*preamble, *sections], ddl_role)


def render_create_split(
    definitions: Sequence[SchemaDefinition],
    ddl_role: str | None = None,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Render one SQL document per package.

    Each file has to stand on its own — a deployment applies them separately —
    so every file repeats the type creation. That is safe: the guarded blocks
    make a type that already exists a no-op.
    """
    _require_definitions(definitions)
    merged = merged_metadata(definitions)
    documents: dict[str, str] = {}
    for definition in definitions:
        types, body = _render_package(merged, definition)
        preamble = [_TYPES_SECTION, *types] if types else []
        documents[definition.name] = document(
            _header([definition], timestamp), [*preamble, *body], ddl_role
        )
    return documents
