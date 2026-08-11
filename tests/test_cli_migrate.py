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
def test_a_mixed_diff_is_refused_whole(installed, engine, monkeypatch):
    """One drop poisons the whole diff, including its additive half.

    A deploy that applied what it could and then stopped would leave the schema in a
    state neither the old nor the new declarations describe -- and the next run would
    diff against that. Refusing everything keeps the database on a state somebody once
    decided on.
    """
    installed([make_definition("pkg.a", "table_a")])
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))
    assert cli.main(["migrate"]) == 0
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE public.table_a ADD COLUMN surplus text"))
    # A second declared table makes the same diff carry an addition as well.
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])

    assert cli.main(["migrate"]) == cli.EXIT_REFUSED

    with engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('public.table_b')")).scalar() is None


@pytest.mark.integration
def test_a_narrowed_column_type_is_not_caught_by_the_rule(installed, engine, monkeypatch):
    """A known limit, pinned so nobody mistakes it for a guarantee.

    The rule classifies four statements as destructive, all of them `DROP`. A type
    change is not among them: `ALTER COLUMN ... TYPE` carries no marker, so `migrate`
    applies it -- and narrowing a type can lose data just as thoroughly as a drop.

    This test does not assert that the behaviour is *right*. It asserts what it is, so
    that a future change to `_DESTRUCTIVE` has to come past this line rather than
    silently alter what a deploy is allowed to do.
    """
    installed([make_definition("pkg.a", "table_a")])
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", dsn(engine))
    assert cli.main(["migrate"]) == 0
    with engine.connect() as connection:
        declared = connection.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='table_a' "
                "AND data_type IN ('text','character varying') ORDER BY ordinal_position"
            )
        ).all()
    if not declared:
        pytest.skip("fixture table has no text column to narrow")
    column = declared[0][0]
    with engine.begin() as connection:
        connection.execute(
            text(f"ALTER TABLE public.table_a ALTER COLUMN {column} TYPE varchar(10)")
        )

    result = cli.main(["migrate"])

    assert result == 0, "a type change is applied, not refused -- see the docstring"


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


@pytest.mark.integration
def test_the_advisory_lock_actually_excludes_a_second_holder(engine):
    """That the lock is *taken* is what the guard rests on, and it needs proving.

    The release test above passes just as happily against a lock that was never
    effective. This one holds it and has a second session try: `pg_try_advisory_lock`
    returns without blocking, so a false answer is the proof rather than a timeout.
    """
    from edutap.db_definitions.execute import ADVISORY_LOCK_KEY, advisory_lock

    with engine.connect() as holder, engine.connect() as other:
        with advisory_lock(holder):
            taken = other.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
            ).scalar()
            assert taken is False, "a second run could migrate at the same time"

        # And once released, the next run gets it.
        assert (
            other.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
            ).scalar()
            is True
        )
        other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
