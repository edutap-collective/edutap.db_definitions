"""Checks that a set of package definitions can share one database."""

from collections import defaultdict
from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import Enum
from sqlalchemy.dialects.postgresql import DOMAIN
from sqlalchemy.types import TypeEngine

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
    """Report two packages claiming the same migration-history table.

    Keyed on the qualified name: two packages may both call their history table
    ``alembic_version`` as long as they keep it in their own schema, which is
    the normal case once every package owns one.
    """
    owners: dict[str, list[str]] = defaultdict(list)
    for definition in definitions:
        key = definition.version_table_key
        if key:
            owners[key].append(definition.name)
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


def _underlying_type(type_: TypeEngine) -> TypeEngine:
    """Follow ``ARRAY`` (and any other container) down to the type it wraps.

    ``ARRAY(ENUM(...))`` renders the very same unqualified ``CREATE TYPE`` as
    a bare ``ENUM(...)`` column does: measured, ``ARRAY(Enum(...)).item_type``
    is the ``Enum`` instance, and it is that instance's ``.schema`` — not the
    ``ARRAY``'s, which has none — that decides where the type is created. A
    check that inspects ``column.type`` alone stays silent on every enum or
    domain hidden inside an array.
    """
    item_type = getattr(type_, "item_type", None)
    while item_type is not None:
        type_ = item_type
        item_type = getattr(type_, "item_type", None)
    return type_


def _unqualified_types(definitions: Sequence[SchemaDefinition]) -> list[ContractViolation]:
    """Report an enum or domain type that does not say which schema it lives in.

    SQLAlchemy scopes a type to the *metadata*, not to the table that uses it.
    A type declared without a schema therefore renders as ``CREATE TYPE
    flavour`` and lands in whatever ``search_path`` resolves to — typically
    ``public`` — while the table using it sits in its owner's schema.

    With rights granted per schema that is not cosmetic: it puts one service's
    type into the contract schema, where the naming space is shared and a second
    package can collide with it.

    Matched on ``Enum`` and ``DOMAIN`` rather than on the base ``SchemaType``,
    which is the tempting but wrong test: ``Boolean`` is a ``SchemaType`` too,
    but it carries no ``schema`` attribute at all — ``Boolean().schema`` raises
    ``AttributeError`` rather than returning ``None``. A check that reads it
    via ``getattr(type_, "schema", None)`` would silently treat every boolean
    column as one more unqualified type, the most common column type there is.

    A non-native ``Enum`` (``native_enum=False``) is excluded on purpose: it
    renders as a plain ``VARCHAR`` column, PostgreSQL creates no type for it,
    and flagging it aborts ``create``/``diff``/``check`` — via
    :func:`raise_on_violations` — for a package that has done nothing wrong.
    ``DOMAIN`` has no such switch; it always creates a type.

    Every occurrence of a name is collected into one violation instead of
    reporting only the first: measured, two columns that share a type name
    still produce only one ``CREATE TYPE`` between them, so the second column
    silently takes on the first one's value list — fixing only the first
    reported site would leave that behind unnoticed.
    """
    violations = []
    for definition in definitions:
        sites: dict[str, list[str]] = defaultdict(list)
        for table in definition.metadata.tables.values():
            for column in table.columns:
                type_ = _underlying_type(column.type)
                is_unqualifiable_type = isinstance(type_, DOMAIN) or (
                    isinstance(type_, Enum) and type_.native_enum
                )
                if not is_unqualifiable_type or type_.schema:
                    continue
                name = type_.name
                if name is None:
                    # Unreachable in practice: a native Enum without a name fails to
                    # compile (`CompileError: PostgreSQL ENUM type requires a name`),
                    # and DOMAIN takes its name as a required positional argument.
                    continue
                sites[name].append(f"{table.key}.{column.name}")
        for name, locations in sorted(sites.items()):
            violations.append(
                ContractViolation(
                    "unqualified_type",
                    f"{definition.name}: type {name!r} on {', '.join(locations)} "
                    "declares no schema, so it would be created outside its table's "
                    "schema. Pass inherit_schema=True (or schema=…) on the type.",
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
        *_unqualified_types(definitions),
    ]


def raise_on_violations(violations: Sequence[ContractViolation]) -> None:
    """Raise :class:`ContractError` listing all violations, if there are any."""
    if violations:
        raise ContractError("\n".join(f"[{v.kind}] {v.message}" for v in violations))
