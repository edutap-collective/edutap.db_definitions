"""Find the schema definitions of the installed eduTAP packages."""

import logging
from collections.abc import Iterable, Sequence
from graphlib import CycleError, TopologicalSorter
from importlib.metadata import EntryPoint, entry_points

from .definition import SchemaDefinition

ENTRY_POINT_GROUP = "edutap.db_definitions"

logger = logging.getLogger("edutap.db_definitions")


class DiscoveryError(Exception):
    """The installed definitions cannot be ordered."""


def iter_entry_points() -> Iterable[EntryPoint]:
    """Return the entry points of the group. Seam for tests."""
    return entry_points(group=ENTRY_POINT_GROUP)


def _describe(point: EntryPoint) -> str:
    """Name one entry point the way its package declares it."""
    value = getattr(point, "value", None)
    return f"{point.name} = {value}" if isinstance(value, str) else str(point.name)


def _load_all() -> dict[str, SchemaDefinition]:
    """Load every announced definition, keyed by package name.

    Loading is deliberately separate from validating: a site legitimately has
    eduTAP packages installed that this selection does not use, and one of them
    being broken must not fail a call that filters it out anyway. A load failure
    is therefore logged with the entry point's name and skipped; if the package
    was selected, the selection step warns that the name never appeared.
    """
    definitions: dict[str, SchemaDefinition] = {}
    announced_by: dict[str, str] = {}
    for point in iter_entry_points():
        try:
            definition = point.load()
            if callable(definition):
                definition = definition()
        except Exception as error:
            logger.warning(
                "Entry point %s could not be loaded (%s: %s) — skipping.",
                _describe(point),
                type(error).__name__,
                error,
            )
            continue
        if definition.name in announced_by:
            raise DiscoveryError(
                f"Two entry points announce the package name {definition.name!r}: "
                f"{announced_by[definition.name]} and {_describe(point)}. "
                "One would silently replace the other, so the tables of one "
                "package would be missing from every generated document."
            )
        announced_by[definition.name] = _describe(point)
        definitions[definition.name] = definition
    return definitions


def _order(definitions: dict[str, SchemaDefinition]) -> list[SchemaDefinition]:
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for name, definition in sorted(definitions.items()):
        # A `requires` entry outside the selection is not an error: a site may run
        # only part of the stack. Ordering then simply has nothing to enforce.
        sorter.add(name, *(r for r in definition.requires if r in definitions))
    try:
        return [definitions[name] for name in sorter.static_order()]
    except CycleError as error:
        raise DiscoveryError(f"Dependency cycle between packages: {error.args[1]}") from error


def load_definitions(
    include: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
) -> list[SchemaDefinition]:
    """Return the selected definitions, validated and topologically ordered."""
    available = _load_all()
    if include is not None:
        for name in include:
            if name not in available:
                logger.warning("Package %s was requested but is not installed — skipping.", name)
        available = {name: d for name, d in available.items() if name in set(include)}
    if exclude:
        available = {name: d for name, d in available.items() if name not in set(exclude)}
    # Validate the survivors, not every installed package: an unusable definition
    # in a package this site excluded is that package's problem, not this run's.
    for definition in available.values():
        definition.validate()
    return _order(available)
