"""`migrate`: what the migration container runs, against a real database.

The rule these tests exist for is one sentence — **it may add and may not take away** —
and it cannot be checked without a database. Whether a diff drops something is a fact
about what is actually there, not about the declarations.
"""

import pytest
from sqlalchemy import text

from edutap.db_definitions import cli

from .conftest import make_definition


def dsn(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


@pytest.mark.integration
def test_an_empty_database_gets_its_tables(installed, engine, monkeypatch, capsys):
    """The first run is a migration too.

    `render_diff` reports a missing table as `add_table` and emits the full
    `CREATE TABLE`, so the empty-database case needs no separate path.
    """
    installed([make_definition("pkg.a", "table_a")])
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))

    assert cli.main(["migrate"]) == 0

    with engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('public.table_a')")).scalar()
    assert "Applied" in capsys.readouterr().out


@pytest.mark.integration
def test_a_second_run_changes_nothing(installed, engine, monkeypatch, capsys):
    """Idempotence, and it has to be observable: a deploy runs this every time."""
    installed([make_definition("pkg.a", "table_a")])
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))
    assert cli.main(["migrate"]) == 0
    capsys.readouterr()

    assert cli.main(["migrate"]) == 0

    assert "nothing to do" in capsys.readouterr().out


@pytest.mark.integration
def test_a_diff_that_would_drop_is_refused_and_the_column_survives(
    installed, engine, monkeypatch, capsys
):
    """The rule this command exists for.

    A column the declarations do not know makes the diff want to drop it. Nothing may
    be applied — not even the additive part of the same diff, because a deploy that
    half-applied and then stopped is worse than one that did not start.
    """
    installed([make_definition("pkg.a", "table_a")])
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))
    assert cli.main(["migrate"]) == 0
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE public.table_a ADD COLUMN surplus text"))
    capsys.readouterr()

    assert cli.main(["migrate"]) == cli.EXIT_REFUSED

    with engine.connect() as connection:
        columns = connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'table_a'"
            )
        ).scalars()
        assert "surplus" in list(columns)


@pytest.mark.integration
def test_the_refusal_names_the_statements_not_just_their_number(
    installed, engine, monkeypatch, capsys
):
    """A red deploy is read in a log window, without the file and without context.

    "1 destructive change detected" sends someone looking; the statement tells them
    what happened.
    """
    installed([make_definition("pkg.a", "table_a")])
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))
    assert cli.main(["migrate"]) == 0
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE public.table_a ADD COLUMN surplus text"))
    capsys.readouterr()

    cli.main(["migrate"])

    error = capsys.readouterr().err
    assert "DROP COLUMN surplus" in error
    assert "Nothing was applied" in error


@pytest.mark.integration
def test_a_dry_run_reports_and_changes_nothing(installed, engine, monkeypatch, capsys):
    installed([make_definition("pkg.a", "table_a")])
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))

    assert cli.main(["migrate", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "would be applied" in out
    assert "CREATE TABLE public.table_a" in out
    with engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('public.table_a')")).scalar() is None


@pytest.mark.integration
def test_the_advisory_lock_is_released_afterwards(installed, engine, monkeypatch):
    """A session-scoped lock outlives its transaction, so releasing it is on us.

    A leaked lock would not fail this run -- it would block the *next* deploy, on a
    connection nobody is looking at.
    """
    installed([make_definition("pkg.a", "table_a")])
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))

    assert cli.main(["migrate"]) == 0

    with engine.connect() as connection:
        held = connection.execute(
            text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
        ).scalar()
    assert held == 0
