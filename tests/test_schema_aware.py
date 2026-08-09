"""Comparing against a live database, across schemas.

These are integration tests on purpose. The failure they guard against —
Alembic reporting a difference that is not one — cannot be reproduced against a
fake: it comes from what PostgreSQL's reflection returns, namely
``referred_schema: None`` for a foreign key into the default schema.
"""

import pytest
from sqlalchemy import ARRAY, Column, Enum, ForeignKey, Integer, MetaData, String, Table, text

from edutap.db_definitions.compare import describe_changes, foreign_tables, render_diff
from edutap.db_definitions.definition import (
    NAMING_CONVENTION,
    SchemaDefinition,
    underlying_type,
)
from edutap.db_definitions.execute import apply_sql
from edutap.db_definitions.render import render_create
from tests.conftest import make_definition, make_definition_with_qualified_enum

pytestmark = pytest.mark.integration


def dsn(engine) -> str:
    """Return a connectable URL.

    `render_as_string(hide_password=False)`, not `str(url)`: `str()` masks the
    password as *** and the connection would fail to authenticate.
    """
    return engine.url.render_as_string(hide_password=False)


def cross_schema_definition(with_certificate: bool = True) -> SchemaDefinition:
    """A package owning one contract table and one table of its own."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "pass_state",
        metadata,
        Column("pass_id", String(64), primary_key=True),
        Column("label", String(32)),
        schema="public",
    )
    if with_certificate:
        Table(
            "certificate",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("pass_id", String(64), ForeignKey("public.pass_state.pass_id")),
            schema="pass_builder",
        )
    return SchemaDefinition(name="pkg.builder", metadata=metadata)


@pytest.fixture
def applied(engine_with_schemas):
    """Apply the cross-schema definition and hand back engine and definition."""
    definition = cross_schema_definition()
    apply_sql(render_create([definition]), dsn(engine_with_schemas))
    return engine_with_schemas, definition


def test_a_schema_in_sync_reports_no_change(applied):
    """The regression this task exists for.

    Reflection reports `referred_schema: None` for a key into the default
    schema while the metadata says `public`. Without folding the default schema
    away, this reports remove_fk plus add_fk and `check` is permanently red.
    """
    engine, definition = applied

    with engine.connect() as connection:
        assert describe_changes(connection, [definition]) == []


def test_rendering_a_diff_does_not_mutate_the_declared_metadata(applied):
    """`_requalified` reaches into the caller's tables to restore qualification.

    It hands Alembic columns that belong to the package's own MetaData, and
    Alembic copies an attached column before putting it into the throwaway Table
    it renders from. That is its choice, not ours: were it to stop, or were the
    fold to mutate a type object the copy shares with the original, the caller's
    MetaData would be silently rewired to a table this tool built.
    """
    engine, definition = applied
    with engine.connect() as connection:
        connection.execute(text("DROP TABLE pass_builder.certificate"))
        connection.commit()

    def shape():
        return {
            table.key: [(column.name, column.table.key) for column in table.columns]
            for table in definition.metadata.tables.values()
        }

    before_sql, before_shape = render_create([definition]), shape()

    with engine.connect() as connection:
        render_diff(connection, [definition])

    assert render_create([definition]) == before_sql
    assert shape() == before_shape


def test_a_real_deviation_is_still_reported(applied):
    engine, definition = applied
    with engine.connect() as connection:
        connection.execute(text("ALTER TABLE pass_builder.certificate ADD COLUMN extra integer"))
        connection.commit()

    with engine.connect() as connection:
        changes = describe_changes(connection, [definition])

    assert any("remove_column" in change and "extra" in change for change in changes)


def test_a_missing_table_in_an_owned_schema_is_reported(applied):
    engine, definition = applied
    with engine.connect() as connection:
        connection.execute(text("DROP TABLE pass_builder.certificate"))
        connection.commit()

    with engine.connect() as connection:
        changes = describe_changes(connection, [definition])

    assert any("add_table" in change and "certificate" in change for change in changes)


def test_the_rendered_diff_stays_schema_qualified(applied):
    """Folding is a comparison device; it must not reach the rendered DDL.

    An unqualified `CREATE TABLE pass_state` resolves through the applying
    role's search_path — the very failure `_require_declared_schemas` exists to
    prevent — so every name the diff emits carries its schema, including the
    target of a foreign key into the default schema.
    """
    engine, definition = applied
    with engine.connect() as connection:
        connection.execute(text("DROP TABLE pass_builder.certificate"))
        connection.execute(text("DROP TABLE public.pass_state"))
        connection.commit()

    with engine.connect() as connection:
        sql = render_diff(connection, [definition])

    assert "CREATE TABLE public.pass_state (" in sql
    assert "CREATE TABLE pass_builder.certificate (" in sql
    assert "REFERENCES public.pass_state" in sql


def test_an_altered_default_schema_table_is_qualified_in_the_diff(applied):
    engine, definition = applied
    with engine.connect() as connection:
        connection.execute(text("ALTER TABLE public.pass_state DROP COLUMN label"))
        connection.commit()

    with engine.connect() as connection:
        sql = render_diff(connection, [definition])

    assert "ALTER TABLE public.pass_state ADD COLUMN label" in sql


def test_a_type_declared_in_the_default_schema_does_not_churn(engine_with_schemas):
    """Reflection omits the default schema for a *type* exactly as for a table.

    `contract._unqualified_types` tells package authors to pass `schema=…`, so a
    native enum declared `schema="public"` on a public table is a shape the tool
    recommends. Reflection returns it as `ENUM(schema=None)`, and with
    `compare_type=True` the two do not match: `check` returns 1 for ever and
    `diff` proposes an ALTER COLUMN TYPE — an ACCESS EXCLUSIVE lock on a live
    table — to reach the type the column already has.
    """
    definition = make_definition_with_qualified_enum("pkg.a", type_schema="public")
    apply_sql(render_create([definition]), dsn(engine_with_schemas))

    with engine_with_schemas.connect() as connection:
        assert describe_changes(connection, [definition]) == []
        assert "ALTER COLUMN" not in render_diff(connection, [definition])


def test_an_array_of_a_type_declared_in_the_default_schema_does_not_churn(engine_with_schemas):
    """The same asymmetry, one container deeper — and the fold's sharp edge.

    `underlying_type` exists because `ARRAY(ENUM(...))` creates the very same
    type as a bare `ENUM(...)`, so the fold has to reach through the container.
    Measured, though, `Table.to_metadata` copies a bare `Enum` but hands an
    `ARRAY`'s `item_type` straight through: clearing the schema in place would
    edit the type object the *package* declared, and the package's own
    `render_create` would then create `kind` outside `public` for ever after.
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("kinds", ARRAY(Enum("a", "b", name="kind", schema="public")), nullable=False),
        schema="public",
    )
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)
    apply_sql(render_create([definition]), dsn(engine_with_schemas))
    before = render_create([definition])

    with engine_with_schemas.connect() as connection:
        assert describe_changes(connection, [definition]) == []

    declared_type = underlying_type(metadata.tables["public.thing"].c.kinds.type)
    assert declared_type.schema == "public"
    assert render_create([definition]) == before


def test_a_type_change_in_the_default_schema_renders_qualified(engine_with_schemas):
    """`inherit_schema=True` is the other form `contract` recommends.

    On a table in the default schema the copied type inherits the folded — that
    is, absent — table schema, so the rendered ALTER would name a bare `kind`
    and resolve it through the applying role's search_path.
    """
    before = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        before,
        Column("id", Integer, primary_key=True),
        Column("kind", String(8), nullable=False),
        schema="public",
    )
    apply_sql(
        render_create([SchemaDefinition(name="pkg.a", metadata=before)]), dsn(engine_with_schemas)
    )
    definition = make_definition_with_qualified_enum("pkg.a")

    with engine_with_schemas.connect() as connection:
        sql = render_diff(connection, [definition])

    assert "ALTER TABLE public.thing ALTER COLUMN kind TYPE public.kind" in sql


def test_a_diff_that_needs_a_new_schema_creates_it(engine_with_schemas):
    """A diff is applied, so it owes the schemas its own statements need.

    `render_create` emits them; `render_diff` built its document by hand and did
    not, so the first table of a not-yet-existing schema failed to apply with
    `InvalidSchemaName`.
    """
    apply_sql(
        render_create([cross_schema_definition(with_certificate=False)]),
        dsn(engine_with_schemas),
    )
    definition = cross_schema_definition()

    with engine_with_schemas.connect() as connection:
        sql = render_diff(connection, [definition])

    assert "CREATE SCHEMA IF NOT EXISTS pass_builder;" in sql
    apply_sql(sql, dsn(engine_with_schemas))
    with engine_with_schemas.connect() as connection:
        assert describe_changes(connection, [definition]) == []


def test_a_foreign_table_in_an_owned_schema_is_reported_qualified(applied):
    engine, definition = applied
    with engine.connect() as connection:
        connection.execute(text("CREATE TABLE pass_builder.someone_elses (id integer)"))
        connection.commit()

    with engine.connect() as connection:
        assert foreign_tables(connection, [definition]) == ["pass_builder.someone_elses"]


def test_a_table_in_a_schema_nobody_selected_is_not_reported(applied):
    engine, definition = applied
    with engine.connect() as connection:
        connection.execute(text("CREATE SCHEMA unrelated"))
        connection.execute(text("CREATE TABLE unrelated.thing (id integer)"))
        connection.commit()

    with engine.connect() as connection:
        assert foreign_tables(connection, [definition]) == []


def test_our_own_tables_are_never_reported_as_foreign(applied):
    engine, definition = applied

    with engine.connect() as connection:
        assert foreign_tables(connection, [definition]) == []


def test_a_version_table_in_an_owned_schema_is_not_reported_as_foreign(engine_with_schemas):
    definition = make_definition("pkg.a", "thing", schema="pass_builder")
    apply_sql(render_create([definition]), dsn(engine_with_schemas))
    with engine_with_schemas.connect() as connection:
        connection.execute(
            text("CREATE TABLE pass_builder.alembic_version_pkg_a (version_num varchar(32))")
        )
        connection.commit()

    with engine_with_schemas.connect() as connection:
        assert foreign_tables(connection, [definition]) == []


def test_a_freely_named_version_table_in_an_owned_schema_is_not_reported_as_foreign(
    engine_with_schemas,
):
    """Only the declared name can cover this shape.

    The test above passes on the ``alembic_version*`` prefix rule alone and stays
    green even if the declared-name half of the union is deleted. This one names
    its history table freely, in a schema that is not the default, so it fails
    unless `foreign_tables` really consults `version_table_key`.
    """
    definition = make_definition(
        "pkg.a", "thing", schema="pass_builder", version_table="pkg_a_migration_state"
    )
    apply_sql(render_create([definition]), dsn(engine_with_schemas))
    with engine_with_schemas.connect() as connection:
        connection.execute(
            text("CREATE TABLE pass_builder.pkg_a_migration_state (version_num varchar(32))")
        )
        connection.commit()

    with engine_with_schemas.connect() as connection:
        assert foreign_tables(connection, [definition]) == []


def test_a_schema_that_only_holds_the_history_table_is_still_ours(engine_with_schemas):
    """`version_table_schema` may name a schema the package holds no data table in.

    `render._needed_schemas` already counts it among a package's schemas, and
    `render_create` creates it. `foreign_tables` and the comparison have to agree
    with that, or the two modules mean different things by "the package's
    schemas".
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        schema="pass_builder",
    )
    definition = SchemaDefinition(
        name="pkg.a",
        metadata=metadata,
        version_table="alembic_version_pkg_a",
        version_table_schema="history",
    )
    apply_sql(render_create([definition]), dsn(engine_with_schemas))
    with engine_with_schemas.connect() as connection:
        connection.execute(text("CREATE TABLE history.someone_elses (id integer)"))
        connection.commit()

    with engine_with_schemas.connect() as connection:
        assert foreign_tables(connection, [definition]) == ["history.someone_elses"]
