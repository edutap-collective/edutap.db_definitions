"""Integration tests: compare package definitions against a live PostgreSQL schema."""

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, text
from tests.conftest import make_definition

from edutap.db_definitions.compare import (
    describe_changes,
    foreign_tables,
    merged_metadata,
    render_diff,
)
from edutap.db_definitions.definition import NAMING_CONVENTION, SchemaDefinition
from edutap.db_definitions.render import render_create

pytestmark = pytest.mark.integration


def definition_with_extra_column(name: str) -> SchemaDefinition:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "table_a",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("label", String(32), nullable=False),
        Column("note", String(64), nullable=True),
    )
    return SchemaDefinition(name=name, metadata=metadata)


def test_merged_metadata_holds_all_tables():
    merged = merged_metadata(
        [make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")]
    )
    assert sorted(merged.tables) == ["table_a", "table_b"]


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
    assert "ALTER TABLE table_a ADD COLUMN note" in sql


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


def test_foreign_tables_are_listed_and_left_alone(engine):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE not_ours (id integer primary key)"))
        connection.execute(text(render_create([make_definition("pkg.a", "table_a")])))
    definitions = [make_definition("pkg.a", "table_a")]
    with engine.connect() as connection:
        assert foreign_tables(connection, definitions) == ["not_ours"]
        assert "not_ours" not in render_diff(connection, definitions)
