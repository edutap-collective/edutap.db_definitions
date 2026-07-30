"""End-to-end tests for the `diff` subcommand.

`diff` is the one command that renders SQL *and* touches a database, so its
call site is where a wrong argument goes unnoticed: without these tests,
deleting `args.ddl_role` from `cli._command_diff` passed the whole suite.
"""

import pytest
from sqlalchemy import text

from edutap.db_definitions import cli
from edutap.db_definitions.render import render_create
from tests.conftest import make_definition


def dsn(engine) -> str:
    return str(engine.url.render_as_string(hide_password=False))


@pytest.mark.integration
def test_diff_writes_the_file_with_the_role_header(installed, engine, monkeypatch, tmp_path):
    definitions = [make_definition("pkg.a", "table_a")]
    installed(definitions)
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))
    target = tmp_path / "diff.sql"

    assert cli.main(["diff", "--out", str(target), "--ddl-role", "edutap_ddl"]) == 0

    document = target.read_text()
    assert "SET ROLE edutap_ddl;" in document
    assert document.index("SET ROLE") < document.index("CREATE TABLE table_a")
    assert document.rstrip().endswith("COMMIT;")


@pytest.mark.integration
def test_diff_prints_to_stdout_and_warns_about_a_missing_role(
    installed, engine, monkeypatch, capsys
):
    installed([make_definition("pkg.a", "table_a")])
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))

    assert cli.main(["diff"]) == 0

    captured = capsys.readouterr()
    assert "CREATE TABLE table_a" in captured.out
    assert "-- NOTE: generated without --ddl-role;" in captured.out
    assert "without --ddl-role" in captured.err


@pytest.mark.integration
def test_diff_reports_foreign_tables_on_stderr(installed, engine, monkeypatch, capsys):
    definitions = [make_definition("pkg.a", "table_a")]
    installed(definitions)
    with engine.begin() as connection:
        connection.execute(text(render_create(definitions)))
        connection.execute(text("CREATE TABLE not_ours (id integer primary key)"))
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))

    assert cli.main(["diff"]) == 0
    assert "not_ours" in capsys.readouterr().err


def test_diff_fails_on_a_contract_violation_without_touching_the_database(
    installed, monkeypatch, capsys
):
    installed([make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")])

    def fail_if_called():
        raise AssertionError("diff must not connect when the contract is violated")

    monkeypatch.setattr(cli, "_connect", fail_if_called)

    assert cli.main(["diff"]) == 1
    assert "table_collision" in capsys.readouterr().err
