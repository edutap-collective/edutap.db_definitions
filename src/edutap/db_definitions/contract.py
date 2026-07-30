"""Checks that a set of package definitions can share one database."""

from collections import defaultdict
from collections.abc import Sequence
from typing import NamedTuple

from .definition import NAMING_CONVENTION, SchemaDefinition


class ContractError(Exception):
    """The selected packages cannot share one database."""


class ContractViolation(NamedTuple):
    """One problem found across the selected definitions."""

    kind: str
    message: str


def _table_collisions(definitions: Sequence[SchemaDefinition]) -> list[ContractViolation]:
    owners: dict[str, list[str]] = defaultdict(list)
    for definition in definitions:
        for table in definition.table_names:
            owners[table].append(definition.name)
    return [
        ContractViolation(
            "table_collision",
            f"Table {table!r} is defined by more than one package: {', '.join(names)}.",
        )
        for table, names in sorted(owners.items())
        if len(names) > 1
    ]


def _version_table_collisions(
    definitions: Sequence[SchemaDefinition],
) -> list[ContractViolation]:
    owners: dict[str, list[str]] = defaultdict(list)
    for definition in definitions:
        if definition.version_table:
            owners[definition.version_table].append(definition.name)
    return [
        ContractViolation(
            "version_table_collision",
            f"version_table {table!r} is claimed by: {', '.join(names)}. "
            "Each package needs its own migration history.",
        )
        for table, names in sorted(owners.items())
        if len(names) > 1
    ]


def _convention_deviations(definitions: Sequence[SchemaDefinition]) -> list[ContractViolation]:
    violations = []
    for definition in definitions:
        convention = dict(definition.metadata.naming_convention)
        if convention != NAMING_CONVENTION:
            violations.append(
                ContractViolation(
                    "naming_convention",
                    f"{definition.name}: naming convention differs from the canonical one "
                    f"(got {sorted(convention.items())}).",
                )
            )
    return violations


def check_contract(definitions: Sequence[SchemaDefinition]) -> list[ContractViolation]:
    """Return every contract violation across the given definitions."""
    return [
        *_table_collisions(definitions),
        *_version_table_collisions(definitions),
        *_convention_deviations(definitions),
    ]


def raise_on_violations(violations: Sequence[ContractViolation]) -> None:
    """Raise :class:`ContractError` listing all violations, if there are any."""
    if violations:
        raise ContractError("\n".join(f"[{v.kind}] {v.message}" for v in violations))
