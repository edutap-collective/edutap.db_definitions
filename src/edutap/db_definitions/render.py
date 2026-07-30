"""Render baseline DDL from package metadata, without touching a database."""

from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

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
        definition.name: _document([definition], _render_package(definition), ddl_role, timestamp)
        for definition in definitions
    }
