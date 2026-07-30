import pytest
from sqlalchemy import MetaData

from edutap.db_definitions.definition import (
    NAMING_CONVENTION,
    DefinitionError,
    SchemaDefinition,
)
from edutap.db_definitions.discovery import DiscoveryError, load_definitions
from tests.conftest import BrokenEntryPoint, FakeEntryPoint, make_definition


def definition_without_tables(name: str) -> SchemaDefinition:
    """A definition that fails validation."""
    return SchemaDefinition(name=name, metadata=MetaData(naming_convention=NAMING_CONVENTION))


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


def test_two_entry_points_for_the_same_package_name_are_an_error(installed_entry_points):
    """Last-wins would make a whole package's tables vanish from the output.

    The contract check never sees the collision either, because only one of the
    two definitions ever reaches it.
    """
    installed_entry_points(
        [
            FakeEntryPoint(name="schema", value=make_definition("pkg.a", "table_a")),
            FakeEntryPoint(name="schema_too", value=make_definition("pkg.a", "table_other")),
        ]
    )
    with pytest.raises(DiscoveryError) as error:
        load_definitions()
    message = str(error.value)
    assert "pkg.a" in message
    assert "schema" in message
    assert "schema_too" in message


def test_a_broken_unrelated_entry_point_does_not_fail_a_filtered_call(
    installed_entry_points, caplog
):
    """One broken installed package must not break a selection that excludes it.

    A site legitimately has packages installed that it does not use here;
    validating every entry point before filtering made such a package fatal.
    """
    installed_entry_points(
        [
            FakeEntryPoint(name="schema", value=make_definition("pkg.a", "table_a")),
            BrokenEntryPoint(name="broken_schema"),
        ]
    )
    with caplog.at_level("WARNING"):
        loaded = load_definitions(include=["pkg.a"])
    assert [d.name for d in loaded] == ["pkg.a"]
    assert "broken_schema" in caplog.text


def test_a_selected_broken_package_is_a_fatal_error(installed_entry_points, caplog):
    """A broken package that was explicitly requested must not vanish silently.

    Skipping every load failure regardless of the selection let `create
    --packages pkg.a,pkg.b` exit 0 and write a document that only contains
    pkg.a's tables when pkg.b's entry point raised. A deploy pipeline that only
    checks the exit code would then apply an incomplete schema.
    """
    installed_entry_points(
        [
            FakeEntryPoint(name="schema", value=make_definition("pkg.a", "table_a")),
            BrokenEntryPoint(name="schema", value="pkg.b.models.dbdef:definition"),
        ]
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(DiscoveryError) as error:
            load_definitions(include=["pkg.a", "pkg.b"])
    message = str(error.value)
    assert "pkg.b" in message
    assert "No module named 'broken'" in message
    # The failure must not be misreported as an absent package.
    assert "is not installed" not in message


def test_a_selected_broken_package_error_names_the_underlying_error(installed_entry_points):
    """The raised error must carry the exception type and message, not just a name."""
    installed_entry_points([BrokenEntryPoint(name="schema", value="pkg.b.dbdef:definition")])
    with pytest.raises(DiscoveryError, match="ImportError"):
        load_definitions(include=["pkg.b"])


def test_a_genuinely_absent_requested_package_still_only_warns(installed_entry_points, caplog):
    """A name with no matching entry point at all is 'not installed', not 'broken'.

    Distinct from a broken-but-selected package: nothing here ever tried and
    failed to load pkg.absent, so it must keep the original, non-fatal path.
    """
    installed_entry_points([BrokenEntryPoint(name="schema", value="pkg.b.dbdef:definition")])
    with caplog.at_level("WARNING"):
        loaded = load_definitions(include=["pkg.absent"])
    assert loaded == []
    assert "pkg.absent" in caplog.text
    assert "is not installed" in caplog.text


def test_a_broken_package_is_fatal_by_default_with_no_selection(installed_entry_points):
    """No `include` means every installed package is implicitly requested.

    A deploy pipeline runs `create` with no `--packages` filter; nothing there
    makes a broken installed package "unrelated", so it must not be silently
    dropped from the generated document either.
    """
    installed_entry_points(
        [
            FakeEntryPoint(name="schema", value=make_definition("pkg.a", "table_a")),
            BrokenEntryPoint(name="schema", value="pkg.b.dbdef:definition"),
        ]
    )
    with pytest.raises(DiscoveryError, match="pkg.b"):
        load_definitions()


def test_a_broken_package_excluded_by_name_is_not_fatal_with_no_selection(
    installed_entry_points, caplog
):
    """An `exclude`d broken package is exactly as 'not this run's problem' as ever."""
    installed_entry_points(
        [
            FakeEntryPoint(name="schema", value=make_definition("pkg.a", "table_a")),
            BrokenEntryPoint(name="schema", value="pkg.b.dbdef:definition"),
        ]
    )
    with caplog.at_level("WARNING"):
        loaded = load_definitions(exclude=["pkg.b"])
    assert [d.name for d in loaded] == ["pkg.a"]


def test_an_invalid_definition_outside_the_selection_is_not_fatal(installed):
    installed([make_definition("pkg.a", "table_a"), definition_without_tables("pkg.empty")])
    assert [d.name for d in load_definitions(include=["pkg.a"])] == ["pkg.a"]


def test_an_invalid_definition_inside_the_selection_is_reported(installed):
    installed([make_definition("pkg.a", "table_a"), definition_without_tables("pkg.empty")])
    with pytest.raises(DefinitionError, match="no tables"):
        load_definitions(include=["pkg.empty"])
