"""Comparing against a live database, across schemas.

These are integration tests on purpose. The failure they guard against —
Alembic reporting a difference that is not one — cannot be reproduced against a
fake: it comes from what PostgreSQL's reflection returns, namely
``referred_schema: None`` for a foreign key into a schema on the ``search_path``.
"""

import pytest
from sqlalchemy import (
    ARRAY,
    Column,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    text,
)

from edutap.db_definitions import cli
from edutap.db_definitions.compare import (
    describe_changes,
    foreign_tables,
    missing_schemas,
    render_diff,
)
from edutap.db_definitions.definition import (
    NAMING_CONVENTION,
    SchemaDefinition,
    underlying_type,
)
from edutap.db_definitions.execute import apply_sql
from edutap.db_definitions.render import render_create
from tests.conftest import (
    make_definition,
    make_definition_with_enum_and_sequence,
    make_definition_with_qualified_enum,
)

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


def search_path_definition() -> SchemaDefinition:
    """A package whose foreign key and whose type both point into ``public``.

    Both are things PostgreSQL's reflection reports unqualified once ``public``
    is on the ``search_path``, and both are compared — the key by shape, the
    type because the context sets ``compare_type=True``.
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "pass_state",
        metadata,
        Column("pass_id", String(64), primary_key=True),
        Column("kind", Enum("a", "b", name="kind", schema="public"), nullable=False),
        schema="public",
    )
    Table(
        "certificate",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("pass_id", String(64), ForeignKey("public.pass_state.pass_id")),
        schema="pass_builder",
    )
    return SchemaDefinition(name="pkg.builder", metadata=metadata)


def engine_with_search_path(engine, search_path: str):
    """Return an engine whose connections *start* with the given ``search_path``.

    Set at connect time on purpose. SQLAlchemy determines
    ``dialect.default_schema_name`` once, when the dialect initialises on the
    first connection, so a ``SET search_path`` issued afterwards does not move
    it and the interesting case never arises. A deployment sets a role's path
    with ``ALTER ROLE … SET search_path``, which also applies at connect time,
    so this is the shape a real DDL role has.
    """
    return create_engine(dsn(engine), connect_args={"options": f"-csearch_path={search_path}"})


def test_a_search_path_holding_more_than_the_default_schema_stays_clean(engine_with_schemas):
    """A DDL role with its own service schema on the path must not turn `check` red.

    Reflection omits the schema of everything *visible on the search_path*, not
    just of the default schema, while Alembic keys only the default schema as
    ``None``. With ``search_path = pass_builder, public`` the two rules disagree
    about ``public``, and measured against PostgreSQL 18 the comparison then
    reported ``remove_fk`` + ``add_fk`` + ``modify_type`` for ever, against a
    database that was exactly what `create` had produced.

    `compare._reflection_search_path` closes the gap by pinning the path to the
    default schema while reflecting. Delete it and this test goes red.
    """
    definition = search_path_definition()
    apply_sql(render_create([definition]), dsn(engine_with_schemas))

    scoped = engine_with_search_path(engine_with_schemas, "pass_builder,public")
    try:
        with scoped.connect() as connection:
            assert connection.dialect.default_schema_name == "pass_builder"
            assert describe_changes(connection, [definition]) == []
            sql = render_diff(connection, [definition])
    finally:
        scoped.dispose()

    assert "ALTER TABLE" not in sql
    assert "CONSTRAINT" not in sql


def test_a_real_deviation_is_still_found_under_such_a_search_path(engine_with_schemas):
    """The pin must silence the false positives without silencing the true ones.

    Pinning `search_path` to a single schema is the kind of fix that can pass
    the test above by making the comparison see nothing at all.
    """
    definition = search_path_definition()
    apply_sql(render_create([definition]), dsn(engine_with_schemas))
    with engine_with_schemas.begin() as connection:
        connection.execute(text("ALTER TABLE pass_builder.certificate ADD COLUMN extra integer"))

    scoped = engine_with_search_path(engine_with_schemas, "pass_builder,public")
    try:
        with scoped.connect() as connection:
            changes = describe_changes(connection, [definition])
    finally:
        scoped.dispose()

    assert any("remove_column" in change and "extra" in change for change in changes)


def test_the_connections_search_path_survives_a_comparison(engine_with_schemas):
    """The connection belongs to the caller; the pin must not leak out of the call.

    A caller that goes on to run its own statements on the same connection would
    otherwise find them resolving against a `search_path` this tool set behind
    its back.
    """
    definition = search_path_definition()
    apply_sql(render_create([definition]), dsn(engine_with_schemas))

    scoped = engine_with_search_path(engine_with_schemas, "pass_builder,public")
    try:
        with scoped.connect() as connection:
            before = connection.exec_driver_sql("SHOW search_path").scalar()
            describe_changes(connection, [definition])
            assert connection.exec_driver_sql("SHOW search_path").scalar() == before
            render_diff(connection, [definition])
            assert connection.exec_driver_sql("SHOW search_path").scalar() == before
    finally:
        scoped.dispose()


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

    This is therefore also **the** guard against the comparison mutating the
    caller's metadata, and the only place it can be guarded from: the type is
    the one thing `merged_metadata` and `comparison_metadata` share with the
    package's own MetaData: tables and columns are copied, so a test that
    compares those cannot fail. Do not replace this with a broader-looking one.
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


def test_a_column_whose_key_differs_from_its_name_keeps_its_qualified_type(engine_with_schemas):
    """A package may set `key` freely, and `ColumnCollection` is keyed by it.

    Looking the declared column up with `table.c[name]` therefore misses
    silently and leaves the folded column in place, which renders
    `ADD COLUMN kind kind` — a type name resolved through the applying role's
    search_path.
    """
    before = MetaData(naming_convention=NAMING_CONVENTION)
    Table("thing", before, Column("id", Integer, primary_key=True), schema="public")
    apply_sql(
        render_create([SchemaDefinition(name="pkg.a", metadata=before)]), dsn(engine_with_schemas)
    )

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("kind", Enum("a", "b", name="kind", schema="public"), key="kind_"),
        schema="public",
    )
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)

    with engine_with_schemas.connect() as connection:
        sql = render_diff(connection, [definition])

    assert "ADD COLUMN kind public.kind" in sql


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


def test_a_sequence_in_a_schema_of_its_own_applies_and_checks_clean(engine_with_schemas):
    """The whole round trip for the fourth schema-carrying object in a MetaData.

    Measured before the fix: `create` emitted ``CREATE SEQUENCE IF NOT EXISTS
    seqlib.counter`` with no ``CREATE SCHEMA seqlib``, so `apply` failed with
    ``InvalidSchemaName`` — and `missing_schemas` did not list `seqlib` either,
    so neither `check` reported it nor `diff` created it.
    """
    definition = make_definition_with_enum_and_sequence(
        "pkg.enum", schema="pass_builder", sequence_schema="seqlib"
    )

    apply_sql(render_create([definition]), dsn(engine_with_schemas))

    with engine_with_schemas.connect() as connection:
        assert missing_schemas(connection, [definition]) == []
        assert describe_changes(connection, [definition]) == []
        assert (
            connection.execute(
                text(
                    "SELECT sequence_schema FROM information_schema.sequences "
                    "WHERE sequence_name = 'provider_thing_id_seq'"
                )
            ).scalar()
            == "seqlib"
        )


def test_a_diff_reports_and_creates_the_schema_a_sequence_needs(engine_with_schemas):
    """`check` has to see the missing schema, or `diff` never gets asked to make it.

    It pins the limit alongside it: the diff creates the *schema* but not the
    sequence. Alembic's autogenerate compares tables, not sequences, so a
    sequence that does not exist yet is one of its blind spots — `create` is the
    command that emits ``CREATE SEQUENCE``, and applying this diff on its own
    would fail on the column default that references it. Sharpen this test if
    Alembic ever grows sequence comparison; do not silently drop the assertion.
    """
    definition = make_definition_with_enum_and_sequence(
        "pkg.enum", schema="pass_builder", sequence_schema="seqlib"
    )
    with engine_with_schemas.begin() as connection:
        connection.execute(text("CREATE SCHEMA pass_builder"))

    with engine_with_schemas.connect() as connection:
        assert missing_schemas(connection, [definition]) == ["seqlib"]
        assert any(
            "missing_schema" in change for change in describe_changes(connection, [definition])
        )
        sql = render_diff(connection, [definition])

    assert "CREATE SCHEMA IF NOT EXISTS seqlib;" in sql
    assert "CREATE SEQUENCE" not in sql


def history_only_schema_definition() -> SchemaDefinition:
    """A package keeping its migration history in a schema it holds no table in."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("thing", metadata, Column("id", Integer, primary_key=True), schema="pass_builder")
    return SchemaDefinition(
        name="pkg.a",
        metadata=metadata,
        version_table="alembic_version_pkg_a",
        version_table_schema="history",
    )


def test_check_reports_an_owned_schema_the_database_lacks(
    installed, engine_with_schemas, monkeypatch, capsys
):
    """A missing schema is a deviation, and only `check` can catch this one.

    A missing *data* schema surfaces as `add_table` for the tables inside it. A
    schema that only holds the migration history has no table in the metadata at
    all, so nothing in the comparison mentions it: `check` said "in sync" while
    the diff carried a lone `CREATE SCHEMA`. A deployment gating `diff` on
    `check` therefore never created it, and Alembic's first
    `CREATE TABLE alembic_version_…` failed at the *next* migration — the very
    failure `render.needed_schemas` exists to prevent.
    """
    definition = history_only_schema_definition()
    installed([definition])
    with engine_with_schemas.begin() as connection:
        connection.execute(text("CREATE SCHEMA pass_builder"))
        connection.execute(text("CREATE TABLE pass_builder.thing (id SERIAL NOT NULL PRIMARY KEY)"))
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine_with_schemas))

    assert cli.main(["check"]) == 1
    assert "history" in capsys.readouterr().err


def test_check_passes_once_the_history_schema_exists(
    installed, engine_with_schemas, monkeypatch, capsys
):
    definition = history_only_schema_definition()
    installed([definition])
    apply_sql(render_create([definition]), dsn(engine_with_schemas))
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine_with_schemas))

    assert cli.main(["check"]) == 0
    assert "in sync" in capsys.readouterr().out


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

    `render.needed_schemas` already counts it among a package's schemas, and
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
