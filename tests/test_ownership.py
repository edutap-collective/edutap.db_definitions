"""The security rule this package exists for, tested against a real database.

Asserting that the string ``SET ROLE`` appears in a document proves nothing
about ownership. The LMU deployment applies files as the `postgres` superuser,
and its privilege model hangs on *who creates the table*:
``ALTER DEFAULT PRIVILEGES FOR ROLE edutap_ddl`` only grants on objects that
`edutap_ddl` itself creates. So the thing to test is `pg_class.relowner`.
"""

import pytest
from sqlalchemy import text

from edutap.db_definitions.cli import main
from edutap.db_definitions.render import render_create
from tests.conftest import make_definition

pytestmark = pytest.mark.integration

DDL_ROLE = "ddl_test"


def table_owner(engine, table: str) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname = :name"),
            {"name": table},
        ).scalar_one()


@pytest.fixture
def ddl_role(engine):
    """A non-superuser role that may create tables in the public schema."""
    with engine.begin() as connection:
        # CREATE ROLE has no IF NOT EXISTS; the role outlives the schema reset.
        connection.execute(
            text(
                f"DO $$ BEGIN CREATE ROLE {DDL_ROLE}; "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
            )
        )
        connection.execute(text(f"GRANT CREATE, USAGE ON SCHEMA public TO {DDL_ROLE}"))
    return DDL_ROLE


def test_a_file_generated_with_ddl_role_produces_tables_owned_by_that_role(
    installed, engine, monkeypatch, tmp_path, ddl_role
):
    installed([make_definition("pkg.a", "table_a")])
    target = tmp_path / "schema.sql"
    assert main(["create", "--out", str(target), "--ddl-role", ddl_role]) == 0

    monkeypatch.setenv("EDUTAP_DBDEF_DSN", str(engine.url.render_as_string(hide_password=False)))
    assert main(["apply", str(target)]) == 0

    assert table_owner(engine, "table_a") == ddl_role


def test_without_ddl_role_the_applying_user_owns_the_tables(engine, monkeypatch, tmp_path):
    """The case `--ddl-role` exists to prevent, made visible instead of assumed."""
    document = render_create([make_definition("pkg.a", "table_a")])
    target = tmp_path / "schema.sql"
    target.write_text(document)

    monkeypatch.setenv("EDUTAP_DBDEF_DSN", str(engine.url.render_as_string(hide_password=False)))
    assert main(["apply", str(target)]) == 0

    assert table_owner(engine, "table_a") == engine.url.username
    assert "-- NOTE: generated without --ddl-role;" in document


def test_a_document_applies_to_a_database_that_has_none_of_its_schemas(
    engine, monkeypatch, tmp_path
):
    definition = make_definition("pkg.a", "thing", schema="pass_builder")
    path = tmp_path / "schema.sql"
    path.write_text(render_create([definition]))
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS pass_builder CASCADE"))
        connection.commit()

    monkeypatch.setenv("EDUTAP_DBDEF_DSN", engine.url.render_as_string(hide_password=False))
    assert main(["apply", str(path)]) == 0

    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT table_schema FROM information_schema.tables WHERE table_name = 'thing'")
        ).scalar_one()
    assert found == "pass_builder"
