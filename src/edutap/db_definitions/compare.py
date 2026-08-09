"""Compare package definitions against a live schema."""

import io
from collections.abc import Iterable, Iterator, Sequence

from alembic.autogenerate import compare_metadata, produce_migrations
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.operations.ops import AddColumnOp, CreateTableOp, MigrateOperation
from sqlalchemy import BLANK_SCHEMA, MetaData, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.sql.schema import RETAIN_SCHEMA

from .definition import SchemaDefinition
from .render import document, merged_metadata, package_provenance

_DESTRUCTIVE = ("DROP TABLE", "DROP COLUMN", "DROP CONSTRAINT", "DROP INDEX")

__all__ = [
    "comparison_metadata",
    "describe_changes",
    "foreign_tables",
    "merged_metadata",
    "render_diff",
]


def _known_names(definitions: Sequence[SchemaDefinition]) -> set[str]:
    return {name for definition in definitions for name in definition.table_names}


def _owned_schemas(definitions: Sequence[SchemaDefinition]) -> set[str]:
    return {schema for definition in definitions for schema in definition.schemas}


def comparison_metadata(
    connection: Connection, definitions: Sequence[SchemaDefinition]
) -> MetaData:
    """Return the merged metadata with the connection's default schema folded away.

    PostgreSQL's reflection omits the schema of anything in the default schema:
    a foreign key into ``public.pass_state`` comes back as
    ``referred_schema: None``. The declared metadata says ``'public'``, Alembic
    compares the two tuples, finds them different, and emits ``remove_fk``
    followed by ``add_fk`` on every single run — a `check` that can never go
    green and a `diff` that proposes dropping and re-adding a healthy key.

    Folding is not the same as dropping the declaration. Packages keep declaring
    their schema explicitly, which is what makes the tool's claim and the
    database agree; this copy exists solely so the comparison sees the shape
    reflection produces. Rendering, the contract checks and ``foreign_tables``
    all keep using the declared names.

    The default schema is read from the connection rather than assumed to be
    ``public``: with ``search_path = "$user", public`` and a matching schema it
    is the role's name instead, and folding the wrong one would reintroduce the
    very churn this prevents.
    """
    default_schema = connection.dialect.default_schema_name
    merged = merged_metadata(definitions)
    folded = MetaData(naming_convention=merged.naming_convention)

    def referred_schema_fn(table, to_schema, constraint, referred_schema):
        # BLANK_SCHEMA clears it; returning None would mean "leave unchanged".
        return BLANK_SCHEMA if referred_schema == default_schema else RETAIN_SCHEMA

    for table in merged.tables.values():
        # BLANK_SCHEMA is rejected here (it is concatenated into the table key);
        # None is right, because `folded` itself carries no schema. SQLAlchemy's
        # annotation omits None although the implementation documents and handles
        # it: `schema is None` means "take the target MetaData's schema", which
        # for `folded` is no schema at all.
        schema = None if table.schema == default_schema else table.schema
        table.to_metadata(
            folded,
            schema=schema,  # ty: ignore[invalid-argument-type]
            referred_schema_fn=referred_schema_fn,
        )
    return folded


def _context(connection: Connection, definitions: Sequence[SchemaDefinition]) -> MigrationContext:
    default_schema = connection.dialect.default_schema_name
    known = _known_names(definitions)
    owned = _owned_schemas(definitions)

    def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
        # Schemas and tables of packages this site did not select are none of
        # our business. Alembic passes None for the default schema, in both the
        # schema name and the parent of a table, so both are normalised here.
        if type_ == "schema":
            return (name or default_schema) in owned
        if type_ == "table" and name is not None:
            schema = parent_names.get("schema_name") or default_schema
            return f"{schema}.{name}" in known
        return True

    return MigrationContext.configure(
        connection=connection,
        opts={"include_name": include_name, "include_schemas": True, "compare_type": True},
    )


def describe_changes(connection: Connection, definitions: Sequence[SchemaDefinition]) -> list[str]:
    """Return one readable line per deviation; empty when the schema is in sync."""
    diffs = compare_metadata(
        _context(connection, definitions), comparison_metadata(connection, definitions)
    )
    return [repr(diff) for diff in diffs]


def foreign_tables(connection: Connection, definitions: Sequence[SchemaDefinition]) -> list[str]:
    """Return the tables in the selected packages' schemas that belong to none of them.

    Scoped to the schemas the selection owns. A table in a schema nobody
    selected is not "foreign", it is simply somebody else's business — and after
    the split, most of the database is somebody else's business.

    "Ours" is the union of two things, both kept deliberately -- do not collapse
    this to just one of them:
      - the declared names: each definition's data tables plus its own
        version_table, which is a free-form string (e.g. "pkg_migration_state")
        and must not be guessed at by a naming convention;
      - the "alembic_version*" prefix on its own, kept because in a shared
        eduTAP database such a table is in practice ours even when the package
        that owns it is not part of the current --packages/--exclude selection.
    Dropping the prefix rule would misreport another selection's version table
    as foreign; dropping the declared names would misreport a package's own
    non-default-named version_table as foreign.
    """
    ours = _known_names(definitions) | {
        key for key in (d.version_table_key for d in definitions) if key
    }
    inspector = inspect(connection)
    present = {
        f"{schema}.{name}"
        for schema in _owned_schemas(definitions)
        for name in inspector.get_table_names(schema=schema)
    }
    return sorted(
        qualified
        for qualified in present - ours
        if not qualified.rsplit(".", 1)[1].startswith("alembic_version")
    )


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


_SCHEMA_FIELDS = ("schema", "source_schema", "referent_schema")
"""The names Alembic keeps a schema under, as an attribute or an ``op.kw`` entry."""

_ABSENT = object()
"""Sentinel telling "this operation has no such field" from "the field is None"."""


def _requalified(
    ops: Iterable[MigrateOperation], declared: MetaData, default_schema: str | None
) -> Iterator[MigrateOperation]:
    """Put the default schema back on operations derived from the folded metadata.

    ``comparison_metadata`` folds the default schema away so the comparison sees
    the shape reflection produces. The operations Alembic derives from it inherit
    that fold, and rendering them unchanged emits ``CREATE TABLE table_a`` where
    the package declared ``public.table_a`` — DDL whose target depends on the
    applying role's ``search_path``. That is the one thing this tool exists to
    prevent (see ``SchemaDefinition._require_declared_schemas``), so the fold is
    undone again before anything is rendered.

    Only the fold is undone, never a schema somebody left out: every declared
    table names its schema, and ``include_name`` keeps everything else out of the
    comparison, so a ``None`` here can only be the folded default.

    Operations on a table outside the default schema are rewritten too, and not
    out of tidiness: their columns still come from the folded copy, so a foreign
    key of ``pass_builder.certificate`` into ``public.pass_state`` would render
    as a bare ``REFERENCES pass_state``.
    """
    for op in ops:
        schema = getattr(op, "schema", _ABSENT)
        table_name = getattr(op, "table_name", None)
        effective = default_schema if schema is None else schema
        table = (
            declared.tables.get(f"{effective}.{table_name}")
            if isinstance(effective, str) and isinstance(table_name, str)
            else None
        )
        if table is not None:
            if isinstance(op, CreateTableOp):
                # Rebuilt from the declared table rather than patched: the op's
                # columns come from the folded copy, so an inline foreign key or
                # a qualified enum type would keep rendering unqualified even
                # once the table itself carries its schema again.
                yield CreateTableOp.from_table(table)
                continue
            if isinstance(op, AddColumnOp) and op.column.name in table.c:
                op.column = table.c[op.column.name]
        # Constraint operations keep their schemas in ``op.kw`` rather than as
        # attributes, and a foreign key keeps two, so both places are swept.
        for field in _SCHEMA_FIELDS:
            if getattr(op, field, _ABSENT) is None:
                setattr(op, field, default_schema)
            keywords = getattr(op, "kw", None)
            if isinstance(keywords, dict) and keywords.get(field, _ABSENT) is None:
                keywords[field] = default_schema
        yield op


def render_diff(
    connection: Connection,
    definitions: Sequence[SchemaDefinition],
    ddl_role: str | None = None,
    allow_destructive: bool = False,
) -> str:
    """Render the ALTER statements that bring the database in line."""
    migrations = produce_migrations(
        _context(connection, definitions), comparison_metadata(connection, definitions)
    )
    buffer = io.StringIO()
    offline = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    operations = Operations(offline)
    # upgrade_ops is typed Optional (it covers a multi-database mode we never use);
    # produce_migrations always populates it for the single MetaData we pass in.
    upgrade_ops = migrations.upgrade_ops.ops if migrations.upgrade_ops is not None else []
    requalified = _requalified(
        _leaf_ops(upgrade_ops),
        merged_metadata(definitions),
        connection.dialect.default_schema_name,
    )
    for operation in requalified:
        operations.invoke(operation)

    header = [
        "-- edutap-dbdef diff",
        f"-- packages: {package_provenance(definitions)}",
        "-- Limits: renames appear as drop + add, some type changes render incompletely,",
        "-- and data migrations are out of scope. Read this before applying it.",
    ]
    body: list[str] = []
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
            body.append(commented)
        else:
            body.append(statement)
    return document(header, body, ddl_role)
