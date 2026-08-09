"""Comparing against a live database, across schemas.

These are integration tests on purpose. The failure they guard against —
Alembic reporting a difference that is not one — cannot be reproduced against a
fake: it comes from what PostgreSQL's reflection returns, namely
``referred_schema: None`` for a foreign key into the default schema.
"""

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, text

from edutap.db_definitions.compare import describe_changes, foreign_tables, render_diff
from edutap.db_definitions.definition import NAMING_CONVENTION, SchemaDefinition
from edutap.db_definitions.execute import apply_sql
from edutap.db_definitions.render import render_create
from tests.conftest import make_definition

pytestmark = pytest.mark.integration


def cross_schema_definition() -> SchemaDefinition:
    """A package owning one contract table and one table of its own."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "pass_state",
        metadata,
        Column("pass_id", String(64), primary_key=True),
        Column("label", String(32)),
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


@pytest.fixture
def applied(engine_with_schemas):
    """Apply the cross-schema definition and hand back engine and definition."""
    definition = cross_schema_definition()
    # render_as_string(hide_password=False), not str(url): str() masks the
    # password as *** and the connection would fail to authenticate. This is the
    # form the existing tests in test_ownership.py use.
    apply_sql(
        render_create([definition]),
        engine_with_schemas.url.render_as_string(hide_password=False),
    )
    return engine_with_schemas, definition


def test_a_schema_in_sync_reports_no_change(applied):
    engine, definition = applied

    with engine.connect() as connection:
        assert describe_changes(connection, [definition]) == []


def test_a_cross_schema_foreign_key_does_not_churn(applied):
    """The regression this task exists for.

    Reflection reports `referred_schema: None` for a key into the default
    schema while the metadata says `public`. Without folding the default schema
    away, every run reports remove_fk plus add_fk and `check` is permanently red.
    """
    engine, definition = applied

    with engine.connect() as connection:
        first = describe_changes(connection, [definition])
    with engine.connect() as connection:
        second = describe_changes(connection, [definition])

    assert first == []
    assert second == []


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
    apply_sql(
        render_create([definition]),
        engine_with_schemas.url.render_as_string(hide_password=False),
    )
    with engine_with_schemas.connect() as connection:
        connection.execute(
            text("CREATE TABLE pass_builder.alembic_version_pkg_a (version_num varchar(32))")
        )
        connection.commit()

    with engine_with_schemas.connect() as connection:
        assert foreign_tables(connection, [definition]) == []
