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


def _module_path(point: EntryPoint) -> str | None:
    """Return the dotted module path an entry point's value targets, if known.

    A broken entry point never reaches ``point.load()`` successfully, so its
    package name (``SchemaDefinition.name``) is never learned. The module path
    it was declared against — the part before the ``:`` in
    ``"pkg.sub.dbdef:definition"`` — is the only pre-load signal available, and
    by convention (see docs/how-to.md) it lives inside the package's own dotted
    namespace, so it can still be matched against a requested package name.
    """
    value = getattr(point, "value", None)
    if not isinstance(value, str):
        return None
    module_path, _, _ = value.partition(":")
    return module_path or None


def _declares_package(point: EntryPoint, name: str) -> bool:
    """Whether a (possibly broken) entry point's module path belongs to ``name``."""
    module_path = _module_path(point)
    if module_path is None:
        return False
    return module_path == name or module_path.startswith(f"{name}.")


def _load_all() -> tuple[dict[str, SchemaDefinition], list[tuple[EntryPoint, str]]]:
    """Load every announced definition, keyed by package name.

    Loading is deliberately separate from validating: a site legitimately has
    eduTAP packages installed that this selection does not use, and one of them
    being broken must not fail a call that filters it out anyway. A load
    failure is therefore always logged and skipped here; whether it is also
    fatal depends on whether the package it belongs to was actually selected,
    which only ``load_definitions`` can decide. The failed entry points are
    returned alongside the definitions so it can do so. (A list, not a dict
    keyed by entry point: the fakes tests install are plain, unhashable
    dataclasses.)
    """
    definitions: dict[str, SchemaDefinition] = {}
    announced_by: dict[str, str] = {}
    failed: list[tuple[EntryPoint, str]] = []
    for point in iter_entry_points():
        try:
            definition = point.load()
            if callable(definition):
                definition = definition()
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            logger.warning(
                "Entry point %s could not be loaded (%s) — skipping.",
                _describe(point),
                reason,
            )
            failed.append((point, reason))
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
    return definitions, failed


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
    """Return the selected definitions, validated and topologically ordered.

    A broken entry point is only fatal when the package it belongs to was part
    of the selection this call cares about:

    - ``include`` given: fatal only for names listed in ``include``. A broken
      package the caller did not ask for is the "one broken, unrelated
      package" the loading/validating split exists to tolerate.
    - ``include`` omitted: every installed package is implicitly in scope —
      there is no filter to make any of them "unrelated" — so a broken one is
      fatal too, unless it was named in ``exclude``. This is the default
      invocation a deploy pipeline runs, and a document that silently drops a
      package's tables there is exactly the failure class this tool exists to
      prevent.

    Either way, a genuinely absent package (no entry point announces it at
    all) only ever logs a warning and is never fatal.
    """
    available, failed = _load_all()
    exclude_set = set(exclude)
    if include is not None:
        for name in include:
            if name in available:
                continue
            broken = next(
                (reason for point, reason in failed if _declares_package(point, name)),
                None,
            )
            if broken is not None:
                raise DiscoveryError(
                    f"Package {name!r} was requested but its entry point failed to "
                    f"load ({broken}). The package is installed, not missing — fix "
                    "the entry point, or drop the name from the selection if it "
                    "should not be part of this document."
                )
            logger.warning("Package %s was requested but is not installed — skipping.", name)
        available = {name: d for name, d in available.items() if name in set(include)}
    else:
        for point, reason in failed:
            if any(_declares_package(point, excluded) for excluded in exclude_set):
                continue
            raise DiscoveryError(
                f"Entry point {_describe(point)} failed to load ({reason}) and no "
                "selection was given, so its package is implicitly part of this "
                "document. Fix the entry point, or exclude the package by name "
                "if it should not be part of this document."
            )
    if exclude_set:
        available = {name: d for name, d in available.items() if name not in exclude_set}
    # Validate the survivors, not every installed package: an unusable definition
    # in a package this site excluded is that package's problem, not this run's.
    for definition in available.values():
        definition.validate()
    return _order(available)
