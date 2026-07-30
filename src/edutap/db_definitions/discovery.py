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


def _load_all() -> dict[str, SchemaDefinition]:
    definitions: dict[str, SchemaDefinition] = {}
    for point in iter_entry_points():
        definition = point.load()
        if callable(definition):
            definition = definition()
        definition.validate()
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
    return _order(available)
