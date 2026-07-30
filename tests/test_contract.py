import pytest

from edutap.db_definitions.contract import ContractError, check_contract, raise_on_violations
from tests.conftest import make_cross_package_definitions, make_definition


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


def test_a_declared_cross_package_foreign_key_is_no_violation():
    provider, consumer = make_cross_package_definitions()
    assert check_contract([provider, consumer]) == []


def test_a_cross_package_foreign_key_without_requires_is_reported():
    """The declaration is what orders the packages; a missing one is a real bug.

    Without `requires`, nothing keeps the referenced table from being created
    after the table referencing it — the file would then fail on an empty
    database, or worse, quietly depend on alphabetical luck.
    """
    provider, consumer = make_cross_package_definitions(declare_requires=False)
    violations = check_contract([provider, consumer])
    assert [v.kind for v in violations] == ["undeclared_dependency"]
    message = violations[0].message
    assert "pkg.consumer" in message
    assert "pkg.provider" in message
    assert "requires" in message


def test_raise_on_violations_is_quiet_when_there_are_none():
    raise_on_violations([])


def test_raise_on_violations_raises_with_all_messages():
    definitions = [make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")]
    with pytest.raises(ContractError, match="shared"):
        raise_on_violations(check_contract(definitions))
