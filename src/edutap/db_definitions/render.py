"""Render baseline DDL from package metadata, without touching a database."""

from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version

from sqlalchemy import MetaData, Table, create_mock_engine
from sqlalchemy.schema import DDLElement

from .definition import SchemaDefinition

_MOCK_URL = "postgresql+psycopg://"

_REPEATABLE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS "),
    ("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS "),
    ("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS "),
    ("CREATE SEQUENCE ", "CREATE SEQUENCE IF NOT EXISTS "),
)
"""Statements PostgreSQL itself can make repeatable, via ``IF NOT EXISTS``."""

_GUARDED_PREFIXES: tuple[str, ...] = (
    "CREATE TYPE ",
    "CREATE DOMAIN ",
    "ALTER TABLE ",
)
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


def _emit(metadata: MetaData, tables: Sequence[Table]) -> list[str]:
    """Return the DDL statements SQLAlchemy itself emits for these tables.

    Uses SQLAlchemy's own schema generator through a mock engine instead of
    compiling ``CreateTable``/``CreateIndex`` by hand. Hand-rolled emission
    silently drops everything else a schema needs: enum types, explicit
    sequences and the ``ALTER TABLE ... ADD CONSTRAINT`` of a deferred foreign
    key. ``create_all`` produces all of them, in dependency order, and is the
    same mechanism the ``metadata.create_all`` calls this tool replaces used.
    """
    statements: list[str] = []

    def dump(construct: DDLElement, *args: object, **kwargs: object) -> None:
        statements.append(str(construct.compile(dialect=engine.dialect)).strip())

    engine = create_mock_engine(_MOCK_URL, dump)
    metadata.create_all(engine, tables=list(tables), checkfirst=False)
    return [_repeatable(statement) for statement in statements]


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


def _render_package(definition: SchemaDefinition) -> list[str]:
    """Render one package's section, in dependency order."""
    metadata = definition.metadata
    # sorted_tables is dependency order: a table comes after everything it references.
    return [f"-- ===== {definition.name} =====", *_emit(metadata, metadata.sorted_tables)]


def _header(definitions: Sequence[SchemaDefinition], timestamp: str | None) -> list[str]:
    packages = ", ".join(f"{d.name} ({_package_version(d.name)})" for d in definitions)
    lines = ["-- edutap-dbdef create", f"-- packages: {packages}"]
    if timestamp:
        lines.append(f"-- generated: {timestamp}")
    return lines


def _document(
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
    return _document(_header(definitions, timestamp), body, ddl_role)


def render_create_split(
    definitions: Sequence[SchemaDefinition],
    ddl_role: str | None = None,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Render one SQL document per package."""
    return {
        definition.name: _document(
            _header([definition], timestamp),
            _render_package(definition),
            ddl_role,
        )
        for definition in definitions
    }
