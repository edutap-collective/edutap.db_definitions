"""Integration tests: compare package definitions against a live PostgreSQL schema."""

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
from tests.conftest import make_cross_package_definitions, make_definition

pytestmark = pytest.mark.integration


def definition_with_extra_column(name: str) -> SchemaDefinition:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "table_a",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("label", String(32), nullable=False),
        Column("note", String(64), nullable=True),
        schema="public",
    )
    return SchemaDefinition(name=name, metadata=metadata)


def test_merged_metadata_holds_all_tables():
    merged = merged_metadata(
        [make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")]
    )
    assert sorted(merged.tables) == ["public.table_a", "public.table_b"]


def test_merged_metadata_resolves_a_cross_package_foreign_key():
    """Merging must not resolve foreign keys while copying.

    Copying via ``sorted_tables`` resolves them table by table and raises
    ``NoReferencedTableError`` for a key whose target lives in another package's
    MetaData; ``tables.values()`` defers resolution until both are merged.
    """
    provider, consumer = make_cross_package_definitions()
    merged = merged_metadata([provider, consumer])
    assert [table.key for table in merged.sorted_tables] == [
        "public.view_source",
        "public.view_state",
    ]


def test_a_cross_package_foreign_key_is_diffed_against_a_live_schema(engine):
    provider, consumer = make_cross_package_definitions()
    definitions = [provider, consumer]
    with engine.begin() as connection:
        connection.execute(text(render_create(definitions)))
    with engine.connect() as connection:
        assert describe_changes(connection, definitions) == []


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
    assert "ALTER TABLE public.table_a ADD COLUMN note" in sql


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


def test_new_table_is_rendered_as_one_intact_create_statement(engine):
    """A package's table not existing yet is the most common real diff: initial
    deployment, or a newly added table. It is also exactly the shape that once
    exposed a bug: Operations.invoke() writes CREATE TABLE across several
    physical lines, and naively appending ';' to every physical line corrupted
    the column list. Assert on the statement's structure, not just substring
    presence, so a regression like that fails this test again.
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "table_a",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("label", String(32), nullable=False),
        Column("note", String(64), nullable=True),
        schema="public",
    )
    definitions = [SchemaDefinition(name="pkg.a", metadata=metadata)]

    with engine.connect() as connection:
        changes = describe_changes(connection, definitions)
        sql = render_diff(connection, definitions)

    assert any("table_a" in change for change in changes)

    lines = sql.splitlines()
    open_index = next(
        i for i, line in enumerate(lines) if line.strip() == "CREATE TABLE public.table_a ("
    )
    close_index = next(i for i, line in enumerate(lines) if line.strip() == ");")
    body = lines[open_index + 1 : close_index]

    # The column/constraint lines are still inside the open statement, so none
    # of them may carry their own terminator -- a corrupted render (';' on
    # every physical line) would fail these three assertions.
    assert body, "expected column definitions between the opening and closing parens"
    assert not any(";" in line for line in body)
    assert any("id" in line for line in body)
    assert any("label" in line for line in body)
    assert any("note" in line for line in body)

    # Exactly one statement terminator closes the whole CREATE TABLE.
    assert sql.count(");") == 1


def test_foreign_tables_are_listed_and_left_alone(engine):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE not_ours (id integer primary key)"))
        connection.execute(text(render_create([make_definition("pkg.a", "table_a")])))
    definitions = [make_definition("pkg.a", "table_a")]
    with engine.connect() as connection:
        assert foreign_tables(connection, definitions) == ["public.not_ours"]
        assert "not_ours" not in render_diff(connection, definitions)


def test_a_declared_non_default_version_table_is_not_foreign(engine):
    """version_table is a free-form string, not a naming convention.

    foreign_tables must exclude it because the package declared it, not because
    it happens to start with "alembic_version" -- this one deliberately does not.
    """
    definition = make_definition("pkg.a", "table_a", version_table="pkg_a_migration_state")
    with engine.begin() as connection:
        connection.execute(text(render_create([definition])))
        connection.execute(
            text("CREATE TABLE pkg_a_migration_state (version_num VARCHAR(32) NOT NULL)")
        )
    with engine.connect() as connection:
        assert foreign_tables(connection, [definition]) == []
