import pytest
from tests.conftest import make_definition

from edutap.db_definitions.discovery import DiscoveryError, load_definitions


def test_loads_all_installed_definitions(installed):
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert [d.name for d in load_definitions()] == ["pkg.a", "pkg.b"]


def test_include_narrows_the_selection(installed):
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert [d.name for d in load_definitions(include=["pkg.b"])] == ["pkg.b"]


def test_exclude_removes_a_package(installed):
    installed([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert [d.name for d in load_definitions(exclude=["pkg.a"])] == ["pkg.b"]


def test_a_requested_but_missing_package_is_skipped_not_fatal(installed, caplog):
    installed([make_definition("pkg.a", "table_a")])
    with caplog.at_level("WARNING"):
        loaded = load_definitions(include=["pkg.a", "pkg.absent"])
    assert [d.name for d in loaded] == ["pkg.a"]
    assert "pkg.absent" in caplog.text


def test_requires_determines_the_order(installed):
    installed(
        [
            make_definition("pkg.late", "table_late", requires=("pkg.early",)),
            make_definition("pkg.early", "table_early"),
        ]
    )
    assert [d.name for d in load_definitions()] == ["pkg.early", "pkg.late"]


def test_independent_packages_are_ordered_by_name(installed):
    installed([make_definition("pkg.z", "table_z"), make_definition("pkg.a", "table_a")])
    assert [d.name for d in load_definitions()] == ["pkg.a", "pkg.z"]


def test_a_dependency_cycle_is_an_error(installed):
    installed(
        [
            make_definition("pkg.a", "table_a", requires=("pkg.b",)),
            make_definition("pkg.b", "table_b", requires=("pkg.a",)),
        ]
    )
    with pytest.raises(DiscoveryError, match="cycle"):
        load_definitions()


def test_a_requires_outside_the_selection_is_ignored(installed):
    installed([make_definition("pkg.a", "table_a", requires=("pkg.absent",))])
    assert [d.name for d in load_definitions()] == ["pkg.a"]
