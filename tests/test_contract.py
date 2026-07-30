import pytest
from tests.conftest import make_definition

from edutap.db_definitions.contract import ContractError, check_contract, raise_on_violations


def test_a_consistent_set_has_no_violations():
    definitions = [make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")]
    assert check_contract(definitions) == []


def test_table_name_collision_is_reported():
    definitions = [make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")]
    violations = check_contract(definitions)
    assert [v.kind for v in violations] == ["table_collision"]
    assert "shared" in violations[0].message


def test_duplicate_version_table_is_reported():
    definitions = [
        make_definition("pkg.a", "table_a", version_table="alembic_version"),
        make_definition("pkg.b", "table_b", version_table="alembic_version"),
    ]
    assert [v.kind for v in check_contract(definitions)] == ["version_table_collision"]


def test_diverging_naming_convention_is_reported():
    definitions = [
        make_definition("pkg.a", "table_a"),
        make_definition("pkg.b", "table_b", convention={"pk": "primary_%(table_name)s"}),
    ]
    assert [v.kind for v in check_contract(definitions)] == ["naming_convention"]


def test_raise_on_violations_is_quiet_when_there_are_none():
    raise_on_violations([])


def test_raise_on_violations_raises_with_all_messages():
    definitions = [make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")]
    with pytest.raises(ContractError, match="shared"):
        raise_on_violations(check_contract(definitions))
