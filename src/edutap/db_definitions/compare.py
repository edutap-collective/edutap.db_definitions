"""Compare package definitions against a live schema."""

import io
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress

from alembic.autogenerate import compare_metadata, produce_migrations
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.operations.ops import (
    AddColumnOp,
    AlterColumnOp,
    CreateTableOp,
    MigrateOperation,
)
from sqlalchemy import BLANK_SCHEMA, Column, MetaData, Table, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql.schema import RETAIN_SCHEMA
from sqlalchemy.types import SchemaType, TypeEngine

from .definition import SchemaDefinition, underlying_type
from .render import (
    document,
    merged_metadata,
    needed_schemas,
    package_provenance,
    schema_statements,
    sequence_schemas,
    type_schemas,
)

_DESTRUCTIVE = ("DROP TABLE", "DROP COLUMN", "DROP CONSTRAINT", "DROP INDEX")

__all__ = [
    "comparison_metadata",
    "describe_changes",
    "foreign_tables",
    "merged_metadata",
    "missing_schemas",
    "render_diff",
]


def _known_names(definitions: Sequence[SchemaDefinition]) -> set[str]:
    return {name for definition in definitions for name in definition.table_names}


def _owned_schemas(definitions: Sequence[SchemaDefinition]) -> set[str]:
    """Return the schemas the selection is responsible for.

    Delegates to ``render.needed_schemas`` so the two modules cannot disagree.
    It includes a ``version_table_schema`` that holds no data table, which
    matters here twice: such a schema is one ``render_create`` creates, so the
    comparison has to look in it, and ``foreign_tables`` has to report what else
    lives there. Other packages' history tables in a shared history schema are
    still filtered by the ``alembic_version*`` prefix rule.

    Type schemas are deliberately *not* included. A schema that merely houses a
    ``CREATE TYPE`` holds none of our tables, so scanning it would report
    somebody else's tables as foreign.
    """
    return needed_schemas(definitions)


def comparison_metadata(
    connection: Connection, definitions: Sequence[SchemaDefinition]
) -> MetaData:
    """Return the merged metadata with the connection's default schema folded away.

    Alembic represents the connection's default schema as ``None``: it reflects
    it under that key, hands ``include_name`` ``None`` for it, and keys its
    ``conn_table_names`` by it. The declared metadata says ``'public'``, so
    without folding, every table in the default schema is compared against
    nothing and reported as ``add_table`` — measured, with the fold removed and
    reflection fully qualified.

    Folding is not the same as dropping the declaration. Packages keep declaring
    their schema explicitly, which is what makes the tool's claim and the
    database agree; this copy exists solely so the comparison sees the shape
    Alembic produces. Rendering, the contract checks and ``foreign_tables`` all
    keep using the declared names.

    Three things carry a schema and all three have to be folded: the table, the
    foreign key's target, and the column's *type*. They cannot be folded
    separately: measured, folding the table while leaving its foreign key
    qualified raises ``NoReferencedTableError`` whenever the *referenced* table
    itself sits in the default schema — that is exactly the key the fold
    clears, while SQLAlchemy's own fallback (what ``Table.to_metadata`` does
    with no ``referred_schema_fn``) still points the constraint at the
    fully-qualified original name, and the two no longer meet. Where the
    referenced table lives elsewhere, that same fallback's fully-qualified name
    still matches the fold's untouched key, so the comparison proceeds — not
    because leaving the foreign key unfolded is sound, only because that
    particular key was never the one at risk; the *table's* own key still needs
    the fold, and a package's foreign keys are not written with this fallback
    in mind. Types follow for the same reason of consistency — with
    ``compare_type=True`` a type declared ``schema="public"`` (the form
    ``contract._unqualified_types`` recommends) would otherwise be reported as
    ``modify_type`` on every run.

    The default schema is read from the connection rather than assumed to be
    ``public``: with ``search_path = pass_builder, public`` it is
    ``pass_builder`` instead, and folding the wrong one would reintroduce the
    very churn this prevents. What makes the reflected side agree with the fold
    is :func:`_reflection_search_path`, which is where the reasoning about
    PostgreSQL's own omission rule now lives — read the two together.
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
        copy = table.to_metadata(
            folded,
            schema=schema,  # ty: ignore[invalid-argument-type]
            referred_schema_fn=referred_schema_fn,
        )
        for column in copy.columns:
            column.type = _folded_type(column.type, default_schema)
    return folded


def _folded_type(type_: TypeEngine, default_schema: str | None) -> TypeEngine:
    """Return ``type_`` with the default schema cleared off the type it creates.

    Rebuilds rather than assigns to ``type_.schema``, because the copy is not as
    deep as it looks: measured, ``Table.to_metadata`` copies a bare ``Enum`` but
    hands an ``ARRAY``'s ``item_type`` straight through, so the innermost type of
    an ``ARRAY(Enum(...))`` is the *same object* the calling package declared.
    Clearing its schema in place would reach out of this throwaway copy and edit
    the package's own metadata.

    Containers are unwrapped through ``item_type`` the way ``underlying_type``
    defines it, so this and ``contract``/``render`` agree on which type a column
    actually creates.
    """
    if underlying_type(type_) is type_:
        if not isinstance(type_, SchemaType) or type_.schema != default_schema:
            return type_
        # SQLAlchemy 2.0.51's DOMAIN.copy() silently drops default, not_null,
        # check, constraint_name and collation (measured). Harmless *here*: the
        # comparison needs the type's name and schema, and Alembic does not
        # compare a domain's constraints at all.
        #
        # Not harmless in general, and what used to stand here — that rendering
        # always goes through the declared type, never a copy — was false.
        # `render.merged_metadata` copies every table with `Table.to_metadata`,
        # which reaches this same `DOMAIN.copy()`, so `create` emits a domain
        # stripped of its CHECK. See `render.merged_metadata` and the limits
        # section of docs/explanation.md; do not reuse this helper for anything
        # that renders from the fold.
        unqualified = type_.copy()
        unqualified.schema = None
        return unqualified
    item_type = _folded_type(type_.item_type, default_schema)  # ty: ignore[unresolved-attribute]
    if item_type is type_.item_type:  # ty: ignore[unresolved-attribute]
        return type_
    return type_.adapt(type(type_), item_type=item_type)


@contextmanager
def _reflection_search_path(connection: Connection) -> Iterator[None]:
    """Pin ``search_path`` to the default schema for the duration of a reflection.

    PostgreSQL's reflection omits the schema of everything **visible on the
    ``search_path``**, not of everything in the default schema — measured, a
    foreign key into ``public.pass_state`` comes back as
    ``referred_schema: None`` whenever ``public`` is on the path, whatever
    ``current_schema()`` happens to be. Alembic's rule is the narrower one: only
    the default schema is ``None`` (see :func:`comparison_metadata`). The two
    coincide exactly when the path holds nothing but the default schema, and
    disagree the moment it holds anything else.

    That disagreement is not hypothetical. A DDL role whose ``search_path`` is
    ``pass_builder, public`` reflects a key into ``public`` as unqualified while
    the fold — keyed on ``pass_builder`` — leaves the declared ``'public'``
    standing. Measured against PostgreSQL 18, ``check`` then reports
    ``remove_fk`` plus ``add_fk`` plus ``modify_type`` on a database that is
    exactly what ``create`` produced, on every run and for ever.

    Pinning the path is what makes the two rules agree, and it is a smaller
    intervention than it looks: it changes nothing about *which* objects are
    reflected — every inspector call in this module names its schema — only
    whether the names come back qualified.

    ``pg_catalog`` would qualify *everything* instead, which reads like the
    tidier answer and is not: Alembic would still key the default schema as
    ``None``, so the fold would have to stay, and it cannot be applied to only
    part of the metadata (again, see :func:`comparison_metadata`).

    The setting is restored on the way out. ``SET`` is transactional in
    PostgreSQL, so an aborted transaction rolls it back on its own — which is
    why a restore that fails because the block left the transaction in
    ``InFailedSqlTransaction`` is suppressed rather than allowed to mask the
    error that put it there.
    """
    previous = connection.exec_driver_sql("SHOW search_path").scalar()
    _set_search_path(connection, connection.dialect.default_schema_name or "")
    try:
        yield
    finally:
        with suppress(DBAPIError):
            _set_search_path(connection, previous or "")


def _set_search_path(connection: Connection, value: str) -> None:
    """Set ``search_path`` through ``set_config`` so the value stays a bound parameter.

    ``SET search_path TO …`` takes an identifier list, which would have to be
    quoted by hand and is fed here with a value read back out of the database.
    ``set_config`` takes it as a string, so the driver binds it.
    """
    connection.execute(text("SELECT set_config('search_path', :value, false)"), {"value": value})


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


def missing_schemas(connection: Connection, definitions: Sequence[SchemaDefinition]) -> list[str]:
    """Return the schemas the selection needs that the database does not have.

    Includes the schemas of qualified types and of explicit sequences, which
    need creating just as a table schema does, and a ``version_table_schema``
    holding no data table at all.
    """
    present = set(inspect(connection).get_schema_names())
    required = (
        needed_schemas(definitions) | type_schemas(definitions) | sequence_schemas(definitions)
    )
    return sorted(required - present)


def describe_changes(connection: Connection, definitions: Sequence[SchemaDefinition]) -> list[str]:
    """Return one readable line per deviation; empty when the schema is in sync.

    Missing schemas come first, and not only because a missing schema explains
    the missing tables under it. A schema that holds nothing but a package's
    migration history has no table in the metadata, so the table comparison
    cannot mention it: ``check`` reported "in sync" while ``diff`` carried a lone
    ``CREATE SCHEMA``, and a deployment gating ``diff`` on ``check`` never
    created it. Alembic's first ``CREATE TABLE alembic_version_…`` then failed at
    the *next* migration, which is the failure ``render.needed_schemas`` exists
    to prevent.
    """
    absent = [
        f"missing_schema: {schema!r} is needed by the selected packages "
        "but does not exist in the database"
        for schema in missing_schemas(connection, definitions)
    ]
    with _reflection_search_path(connection):
        diffs = compare_metadata(
            _context(connection, definitions), comparison_metadata(connection, definitions)
        )
    return [*absent, *(repr(diff) for diff in diffs)]


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
    out of tidiness: their columns and types still come from the folded copy, so
    a foreign key of ``pass_builder.certificate`` into ``public.pass_state``
    would render as a bare ``REFERENCES pass_state``.

    Anything carrying a column or a type is taken from the declared table rather
    than patched, because the fold reaches inside those too. Both forms the
    contract recommends for a type need it: ``schema=…`` is cleared by
    ``comparison_metadata``, and ``inherit_schema=True`` re-inherits the folded —
    absent — table schema when the column is copied.
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
            if isinstance(op, AddColumnOp):
                # `or` is not available here: a Column overloads __bool__ to raise.
                added = _declared_column(table, op.column.name)
                if added is not None:
                    op.column = added
            if isinstance(op, AlterColumnOp) and op.modify_type is not None:
                target = _declared_column(table, op.column_name)
                if target is not None:
                    op.modify_type = target.type
        # Constraint operations keep their schemas in ``op.kw`` rather than as
        # attributes, and a foreign key keeps two, so both places are swept.
        keywords = getattr(op, "kw", None)
        for field in _SCHEMA_FIELDS:
            if getattr(op, field, _ABSENT) is None:
                setattr(op, field, default_schema)
            if isinstance(keywords, dict) and keywords.get(field, _ABSENT) is None:
                keywords[field] = default_schema
        yield op


def _declared_column(table: Table, name: str) -> Column | None:
    """Return the table's column of that name, or None.

    Looks the column up by ``.name``, not through ``table.c[...]``:
    ``ColumnCollection`` is keyed by ``.key``, which a package is free to set to
    something else (``Column("kind", key="kind_")``). Indexing would then miss
    silently and leave the folded column in place.
    """
    return next((column for column in table.columns if column.name == name), None)


def render_diff(
    connection: Connection,
    definitions: Sequence[SchemaDefinition],
    ddl_role: str | None = None,
    allow_destructive: bool = False,
) -> str:
    """Render the ALTER statements that bring the database in line."""
    with _reflection_search_path(connection):
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
    return document(header, [*_create_schemas(connection, definitions), *body], ddl_role)


def _create_schemas(connection: Connection, definitions: Sequence[SchemaDefinition]) -> list[str]:
    """Return the ``CREATE SCHEMA`` statements this diff owes its own body.

    A diff is applied, so it is responsible for the schemas its statements need,
    exactly as ``render_create`` is: without this the first table of a
    not-yet-existing schema fails with ``InvalidSchemaName``.

    Restricted to the schemas the database does not have yet, which
    ``render_create`` cannot do because it never sees a connection. A diff is
    generated against the very database it is applied to, and measured,
    ``CREATE SCHEMA`` needs CREATE on the *database* even in its
    ``IF NOT EXISTS`` form: PostgreSQL checks the privilege before the existence
    test, so a role without that right gets ``InsufficientPrivilege`` even for a
    schema that is already there. Emitting these unconditionally would break the
    diff for the very role it is meant to be applied by — the same reasoning
    that keeps ``public`` out of ``render._ALWAYS_PRESENT``.
    """
    return schema_statements(set(missing_schemas(connection, definitions)))
