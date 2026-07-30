"""Compare package definitions against a live schema."""

import io
from collections.abc import Iterable, Iterator, Sequence

from alembic.autogenerate import compare_metadata, produce_migrations
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.operations.ops import MigrateOperation
from sqlalchemy import MetaData, inspect
from sqlalchemy.engine import Connection

from .definition import NAMING_CONVENTION, SchemaDefinition

_DESTRUCTIVE = ("DROP TABLE", "DROP COLUMN", "DROP CONSTRAINT", "DROP INDEX")


def merged_metadata(definitions: Sequence[SchemaDefinition]) -> MetaData:
    """Copy all definitions' tables into one MetaData for comparison.

    Alembic compares one MetaData against the whole schema; comparing package by
    package would report every other package's tables as removed. Merging keeps
    the comparison honest about what this deployment actually owns.
    """
    merged = MetaData(naming_convention=NAMING_CONVENTION)
    for definition in definitions:
        for table in definition.metadata.sorted_tables:
            table.to_metadata(merged)
    return merged


def _known_names(definitions: Sequence[SchemaDefinition]) -> set[str]:
    return {name for definition in definitions for name in definition.table_names}


def _context(connection: Connection, definitions: Sequence[SchemaDefinition]) -> MigrationContext:
    known = _known_names(definitions)

    def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
        # Tables of packages this site did not select are none of our business.
        if type_ == "table" and name is not None:
            return name in known
        return True

    return MigrationContext.configure(
        connection=connection,
        opts={"include_name": include_name, "compare_type": True},
    )


def describe_changes(connection: Connection, definitions: Sequence[SchemaDefinition]) -> list[str]:
    """Return one readable line per deviation; empty when the schema is in sync."""
    diffs = compare_metadata(_context(connection, definitions), merged_metadata(definitions))
    return [repr(diff) for diff in diffs]


def foreign_tables(connection: Connection, definitions: Sequence[SchemaDefinition]) -> list[str]:
    """Return the database's tables that belong to no selected package."""
    # "Ours" is the union of two things, both kept deliberately -- do not collapse
    # this to just one of them:
    #   - the declared names: each definition's data tables plus its own
    #     version_table, which is a free-form string (e.g. "pkg_migration_state")
    #     and must not be guessed at by a naming convention;
    #   - the "alembic_version*" prefix on its own, kept because in a shared
    #     eduTAP database such a table is in practice ours even when the package
    #     that owns it is not part of the current --packages/--exclude selection.
    # Dropping the prefix rule would misreport another selection's version table
    # as foreign; dropping the declared names would misreport a package's own
    # non-default-named version_table as foreign.
    ours = _known_names(definitions) | {d.version_table for d in definitions if d.version_table}
    present = set(inspect(connection).get_table_names())
    return sorted(name for name in present - ours if not name.startswith("alembic_version"))


def _leaf_ops(ops: Iterable[MigrateOperation]) -> Iterator[MigrateOperation]:
    """Flatten Alembic's operation tree into individually invokable operations.

    ``UpgradeOps.ops`` mixes leaf operations (``AddColumnOp``, ``CreateTableOp``, ...)
    with ``ModifyTableOps`` containers that group several alterations to one table.
    ``Operations.invoke`` has no dispatch registered for containers, only for the
    leaves inside them, so they must be unwrapped recursively before invoking.
    """
    for op in ops:
        nested = getattr(op, "ops", None)
        if nested is not None:
            yield from _leaf_ops(nested)
        else:
            yield op


def render_diff(
    connection: Connection,
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
    # upgrade_ops is typed Optional (it covers a multi-database mode we never use);
    # produce_migrations always populates it for the single MetaData we pass in.
    upgrade_ops = migrations.upgrade_ops.ops if migrations.upgrade_ops is not None else []
    for operation in _leaf_ops(upgrade_ops):
        operations.invoke(operation)

    lines = [
        "-- edutap-dbdef diff",
        "-- Limits: renames appear as drop + add, some type changes render incompletely,",
        "-- and data migrations are out of scope. Read this before applying it.",
        "BEGIN;",
    ]
    if ddl_role:
        lines.append(f"SET ROLE {ddl_role};")
    # Operations.invoke() writes one full (possibly multi-line, e.g. CREATE TABLE)
    # statement per call, each already terminated with a semicolon and separated
    # from the next by a blank line. Split on statement boundaries, not physical
    # lines, so a destructive marker in one statement never swallows another and a
    # multi-line CREATE TABLE body is not mangled by per-line semicolon insertion.
    for block in buffer.getvalue().split("\n\n"):
        statement = block.strip()
        if not statement:
            continue
        if not statement.endswith(";"):
            statement += ";"
        destructive = any(marker in statement.upper() for marker in _DESTRUCTIVE)
        if destructive and not allow_destructive:
            commented = "\n".join(
                f"-- DESTRUCTIVE, enable with --allow-destructive: {stmt_line}"
                for stmt_line in statement.splitlines()
            )
            lines.append(commented)
        else:
            lines.append(statement)
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"
