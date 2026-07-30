"""Every foreseeable failure reaches the operator as a message, not a traceback.

`main()` used to catch only `ContractError`, so a `requires` cycle, a broken
installed package, `apply` on a missing file and a wrong `PGHOST` all printed a
stack trace — for an operator preparing a privileged schema change, that is the
difference between a readable refusal and a puzzle.
"""

import pytest
from sqlalchemy.exc import OperationalError

from edutap.db_definitions import cli
from tests.conftest import make_definition


def test_a_dependency_cycle_is_reported(installed, capsys):
    installed(
        [
            make_definition("pkg.a", "table_a", requires=("pkg.b",)),
            make_definition("pkg.b", "table_b", requires=("pkg.a",)),
        ]
    )
    assert cli.main(["create"]) == 1
    assert "cycle" in capsys.readouterr().err


def test_an_unusable_definition_is_reported(installed, capsys):
    from sqlalchemy import MetaData

    from edutap.db_definitions.definition import NAMING_CONVENTION, SchemaDefinition

    installed(
        [
            SchemaDefinition(
                name="pkg.empty",
                metadata=MetaData(naming_convention=NAMING_CONVENTION),
            )
        ]
    )
    assert cli.main(["create", "--packages", "pkg.empty"]) == 1
    assert "no tables" in capsys.readouterr().err


def test_an_invalid_ddl_role_is_reported(installed, capsys):
    installed([make_definition("pkg.a", "table_a")])
    assert cli.main(["create", "--ddl-role", "no; way"]) == 1
    assert "not a valid PostgreSQL identifier" in capsys.readouterr().err


def test_apply_on_a_missing_file_is_reported(tmp_path, capsys):
    assert cli.main(["apply", str(tmp_path / "absent.sql")]) == 1
    assert "absent.sql" in capsys.readouterr().err


def test_an_unwritable_output_path_is_reported(installed, tmp_path, capsys):
    installed([make_definition("pkg.a", "table_a")])
    assert cli.main(["create", "--out", str(tmp_path / "missing_dir" / "schema.sql")]) == 1
    assert "schema.sql" in capsys.readouterr().err


def test_a_database_failure_is_reported(installed, monkeypatch, capsys):
    installed([make_definition("pkg.a", "table_a")])

    def refuse():
        raise OperationalError("SELECT 1", {}, Exception("could not translate host name"))

    monkeypatch.setattr(cli, "_connect", refuse)
    assert cli.main(["check"]) == 1
    err = capsys.readouterr().err
    assert "could not translate host name" in err
    assert "Traceback" not in err


def test_an_unexpected_error_is_not_swallowed(installed, monkeypatch):
    """Only the foreseeable failures are turned into messages.

    A bug in this package must still surface as a traceback rather than as a
    plain exit code that reads like a normal refusal.
    """
    installed([make_definition("pkg.a", "table_a")])

    def explode():
        raise ZeroDivisionError("a real bug")

    monkeypatch.setattr(cli, "_connect", explode)
    with pytest.raises(ZeroDivisionError):
        cli.main(["check"])
