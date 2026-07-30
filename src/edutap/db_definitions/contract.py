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


def _undeclared_dependencies(definitions: Sequence[SchemaDefinition]) -> list[ContractViolation]:
    """Report a foreign key into another package that ``requires`` does not declare.

    Rendering resolves cross-package foreign keys through one merged MetaData, so
    an undeclared dependency no longer *fails* — it silently produces a file whose
    statement order depends on the topological sort having no reason to put the
    referenced table first. The declaration is what makes the order right, so a
    missing one is a contract violation and not a warning.

    The target is read from ``ForeignKey.target_fullname``, a plain string:
    touching ``ForeignKey.column`` would resolve the key and raise for exactly
    the case this check is about.
    """
    owner = {
        table: definition.name for definition in definitions for table in definition.table_names
    }
    violations = []
    for definition in definitions:
        for table in definition.metadata.tables.values():
            for key in sorted(table.foreign_keys, key=lambda k: k.target_fullname):
                target_table = key.target_fullname.rsplit(".", 1)[0]
                target_package = owner.get(target_table)
                if target_package is None or target_package == definition.name:
                    continue
                if target_package in definition.requires:
                    continue
                violations.append(
                    ContractViolation(
                        "undeclared_dependency",
                        f"{definition.name}: table {table.name!r} references "
                        f"{target_table!r}, which belongs to {target_package}, but "
                        f"{definition.name} does not declare "
                        f"requires=({target_package!r},).",
                    )
                )
    return violations


def check_contract(definitions: Sequence[SchemaDefinition]) -> list[ContractViolation]:
    """Return every contract violation across the given definitions."""
    return [
        *_table_collisions(definitions),
        *_version_table_collisions(definitions),
        *_convention_deviations(definitions),
        *_undeclared_dependencies(definitions),
    ]


def raise_on_violations(violations: Sequence[ContractViolation]) -> None:
    """Raise :class:`ContractError` listing all violations, if there are any."""
    if violations:
        raise ContractError("\n".join(f"[{v.kind}] {v.message}" for v in violations))
