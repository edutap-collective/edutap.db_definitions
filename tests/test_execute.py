import pytest
from sqlalchemy import inspect

from edutap.db_definitions.cli import main
from edutap.db_definitions.execute import apply_sql
from edutap.db_definitions.render import render_create
from tests.conftest import (
    make_definition,
    make_definition_with_deferred_foreign_key,
    make_definition_with_enum_and_sequence,
)

pytestmark = pytest.mark.integration


def test_apply_creates_the_tables(engine):
    sql = render_create([make_definition("pkg.a", "table_a")])
    url = str(engine.url.render_as_string(hide_password=False))
    executed = apply_sql(sql, url)
    assert executed == 1
    assert "table_a" in inspect(engine).get_table_names()


def test_a_dollar_quoted_block_counts_as_one_statement(engine):
    """A guarded type creation is one statement, not one per inner semicolon.

    ``DO $$ BEGIN ... EXCEPTION ...; END $$;`` carries semicolons inside its
    body. Counting them would report four statements where three ran.
    """
    sql = render_create([make_definition_with_enum_and_sequence("pkg.enum")])
    url = str(engine.url.render_as_string(hide_password=False))
    executed = apply_sql(sql, url)
    # the guarded CREATE TYPE, the CREATE SEQUENCE and the CREATE TABLE
    assert executed == 3


def test_multiple_tables_count_correctly(engine):
    # Two tables should count as 2 statements
    sql = render_create([make_definition("pkg.a", "table_a", "table_b")])
    url = str(engine.url.render_as_string(hide_password=False))
    executed = apply_sql(sql, url)
    assert executed == 2
    assert "table_a" in inspect(engine).get_table_names()
    assert "table_b" in inspect(engine).get_table_names()


def test_dry_run_changes_nothing(engine):
    sql = render_create([make_definition("pkg.a", "table_a")])
    url = str(engine.url.render_as_string(hide_password=False))
    assert apply_sql(sql, url, dry_run=True) == 0
    assert "table_a" not in inspect(engine).get_table_names()


def test_apply_is_repeatable(engine):
    sql = render_create([make_definition("pkg.a", "table_a")])
    url = str(engine.url.render_as_string(hide_password=False))
    apply_sql(sql, url)
    apply_sql(sql, url)
    assert "table_a" in inspect(engine).get_table_names()


def test_a_document_with_a_type_and_a_sequence_applies_twice(engine):
    """The spec promises a repeatable file, and not only for tables.

    A second apply must not fail with "type provider already exists" or
    "relation provider_thing_id_seq already exists".
    """
    sql = render_create([make_definition_with_enum_and_sequence("pkg.enum")])
    url = str(engine.url.render_as_string(hide_password=False))
    apply_sql(sql, url)
    apply_sql(sql, url)
    assert "provider_thing" in inspect(engine).get_table_names()


def test_a_document_with_a_deferred_foreign_key_applies_twice(engine):
    sql = render_create([make_definition_with_deferred_foreign_key("pkg.alter")])
    url = str(engine.url.render_as_string(hide_password=False))
    apply_sql(sql, url)
    apply_sql(sql, url)
    constraints = inspect(engine).get_foreign_keys("second")
    assert [c["name"] for c in constraints] == ["fk_second_first_id_first"]


def test_cli_apply_reads_the_file(engine, tmp_path, monkeypatch):
    target = tmp_path / "create.sql"
    target.write_text(render_create([make_definition("pkg.a", "table_a")]))
    url = str(engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", url)
    assert main(["apply", str(target)]) == 0
    assert "table_a" in inspect(engine).get_table_names()


def test_a_failing_statement_rolls_everything_back(engine):
    from sqlalchemy.exc import ProgrammingError

    url = str(engine.url.render_as_string(hide_password=False))
    sql = (
        "BEGIN;\nCREATE TABLE good (id integer primary key);\n"
        "SELECT nonexistent_function();\nCOMMIT;\n"
    )
    with pytest.raises(ProgrammingError):
        apply_sql(sql, url)
    assert "good" not in inspect(engine).get_table_names()
