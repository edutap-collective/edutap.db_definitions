import pytest
from sqlalchemy import text
from tests.conftest import make_definition

from edutap.db_definitions.cli import main
from edutap.db_definitions.render import render_create

pytestmark = pytest.mark.integration


def test_check_passes_when_the_schema_matches(installed, engine, monkeypatch, capsys):  # noqa: E501
    definitions = [make_definition("pkg.a", "table_a")]
    installed(definitions)
    with engine.begin() as connection:
        connection.execute(text(render_create(definitions)))
    url = engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", str(url))
    assert main(["check"]) == 0
    assert "in sync" in capsys.readouterr().out


def test_check_fails_and_reports_when_a_table_is_missing(installed, engine, monkeypatch, capsys):  # noqa: E501
    installed([make_definition("pkg.a", "table_a")])
    url = engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", str(url))
    assert main(["check"]) == 1
    assert "table_a" in capsys.readouterr().err


def test_check_fails_on_a_contract_violation_without_touching_the_database(installed, capsys):  # noqa: E501
    installed(
        [
            make_definition("pkg.a", "shared"),
            make_definition("pkg.b", "shared"),
        ]
    )
    assert main(["check"]) == 1
    assert "table_collision" in capsys.readouterr().err
