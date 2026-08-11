"""The contract schema this package declares itself."""

import subprocess
import sys

from edutap.db_definitions.contract import check_contract
from edutap.db_definitions.discovery import load_definitions
from edutap.db_definitions.public import NAME, VERSION_TABLE, definition

from .conftest import make_definition


def test_it_declares_the_contract_tables():
    assert definition.table_names == (
        "public.pass_instance",
        "public.pass_state",
        "public.person_view",
        "public.photo",
        "public.photo_review",
    )


def test_every_table_says_which_schema_it_is_in():
    # An unqualified table resolves through search_path and lands somewhere different
    # per deployment. validate() is what refuses to guess.
    definition.validate()
    assert definition.schemas == ("public",)


def test_the_history_table_is_named_after_the_schema_not_after_a_service():
    # alembic_version_data_provider stopped being true the moment the tables moved,
    # and a wrong name in a migration history outlives everyone who knew why.
    assert VERSION_TABLE == "alembic_version_public"
    assert definition.version_table_key == "public.alembic_version_public"


def test_it_is_found_without_an_entry_point():
    # Registered internally: an entry point here would be package metadata pointing
    # at the module that reads it.
    found = {definition.name for definition in load_definitions()}
    assert NAME in found


def test_it_can_be_selected_and_deselected_like_any_other():
    assert [d.name for d in load_definitions(include=[NAME])] == [NAME]
    assert NAME not in {d.name for d in load_definitions(exclude=[NAME])}


def test_it_satisfies_the_contract_it_enforces():
    assert check_contract([definition]) == []


def test_a_package_redeclaring_a_contract_table_is_refused():
    # The case this guards: edutap.data_provider still declaring person_view while
    # this package declares it too. Two owners for one table is exactly what the
    # collision check exists to catch, and the pair must not render.
    other = make_definition("edutap.somewhere", "person_view")
    violations = check_contract([definition, other])
    assert [v.kind for v in violations] == ["table_collision"]
    assert "person_view" in violations[0].message


def test_declaring_does_not_drag_in_the_command_line_dependencies():
    # The whole point of the cli extra. Importing the declarations must not pull in a
    # migration engine: edutap.data_provider imports them at runtime, in a container
    # that has no business carrying alembic. A subprocess, because the test session
    # has the dev extra installed and would find alembic imported anyway.
    code = (
        "import sys; import edutap.db_definitions.public; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in {'alembic', 'psycopg'}))"
    )
    result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "[]"
