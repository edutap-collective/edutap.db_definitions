"""The contract a package uses to announce its tables, and its shared type-introspection helper."""

from dataclasses import dataclass, field
from typing import TypeGuard

from sqlalchemy import Enum, MetaData
from sqlalchemy.dialects.postgresql import DOMAIN
from sqlalchemy.schema import Sequence
from sqlalchemy.types import TypeEngine

NAMING_CONVENTION: dict[str, str] = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}
"""Canonical constraint naming convention.

Packages COPY this into their own ``MetaData(naming_convention=...)``. They must not
import it: that would give every service a runtime dependency on a tool which is
never deployed. ``contract.check_contract`` verifies they match.
"""


class DefinitionError(Exception):
    """A package's schema definition is unusable."""


@dataclass(frozen=True)
class SchemaDefinition:
    """What one eduTAP package tells the generator about its tables."""

    name: str
    metadata: MetaData
    requires: tuple[str, ...] = field(default=())
    alembic_ini: str | None = None
    version_table: str | None = None
    version_table_schema: str | None = None

    @property
    def table_names(self) -> tuple[str, ...]:
        """Return the package's schema-qualified table names, sorted."""
        return tuple(sorted(self.metadata.tables))

    @property
    def schemas(self) -> tuple[str, ...]:
        """Return the schemas this package holds tables in, sorted.

        Only meaningful after ``validate`` has passed: an undeclared schema is
        ``None`` and would sort against strings.
        """
        return tuple(
            sorted({table.schema for table in self.metadata.tables.values() if table.schema})
        )

    @property
    def version_table_key(self) -> str | None:
        """Return the qualified name of the migration-history table, if any."""
        if not self.version_table:
            return None
        schema = self.version_table_schema or (self.schemas[0] if len(self.schemas) == 1 else None)
        return f"{schema}.{self.version_table}" if schema else None

    def validate(self) -> None:
        """Raise :class:`DefinitionError` if this definition cannot be used."""
        if not self.name.strip():
            raise DefinitionError("A schema definition needs a non-empty name.")
        if not self.metadata.tables:
            raise DefinitionError(f"{self.name}: metadata has no tables.")
        self._require_declared_schemas()
        self._require_declared_sequence_schemas()
        self._require_a_version_table_schema()
        if self.version_table_key and self.version_table_key in self.metadata.tables:
            raise DefinitionError(
                f"{self.name}: version_table {self.version_table_key!r} is also a data table."
            )

    def _require_declared_schemas(self) -> None:
        """Reject a table that leaves its schema to ``search_path``.

        An unqualified table is not merely untidy, it resolves differently per
        deployment: with ``search_path = "$user", public`` it lands in a schema
        named after the connecting role where one exists, and in ``public``
        where it does not. That is how the same code put its tables in
        ``edutap`` locally and in ``public`` in production without anyone
        noticing. Declaring the schema is what makes the two identical, so the
        tool refuses to guess on a package's behalf.
        """
        undeclared = sorted(
            table.name for table in self.metadata.tables.values() if not table.schema
        )
        if undeclared:
            raise DefinitionError(
                f"{self.name}: these tables declare no schema: {', '.join(undeclared)}. "
                'Add __table_args__ = {"schema": "<name>"} (or schema="<name>" on the '
                "Table) — the schema decides who may write the table, so it cannot be "
                "left to search_path."
            )

    def _require_declared_sequence_schemas(self) -> None:
        """Reject an explicit sequence that leaves its schema to ``search_path``.

        A sequence is a relation, in the same namespace as a table, and it goes
        wrong the same way — only more quietly. Measured, ``Sequence("counter")``
        on a table in ``pass_builder`` renders as a bare
        ``CREATE SEQUENCE IF NOT EXISTS counter``, which lands in ``public``,
        while the table that owns it sits in ``pass_builder``.

        That is worse than the table case, because it is not a deviation
        ``check`` can report. For a table outside the default schema,
        SQLAlchemy's reflection rewrites the column default
        ``nextval('counter'::regclass)`` to ``nextval('"pass_builder".counter')``
        and Alembic casts it back to ``regclass``, so ``check`` does not report
        a difference — it aborts with ``UndefinedTable: relation
        "pass_builder.counter" does not exist``, for ever, against the very
        database ``create`` produced. A document that cannot be checked
        afterwards is refused before it is written.

        Covers the sequences attached to the MetaData as well as those attached
        to a column: the rule is about what the package declared, not about
        which of them today's renderer happens to reach.
        """
        undeclared = sorted(
            sequence.name for sequence in metadata_sequences(self.metadata) if not sequence.schema
        )
        if undeclared:
            raise DefinitionError(
                f"{self.name}: these sequences declare no schema: {', '.join(undeclared)}. "
                'Add schema="<name>" to the Sequence(...) — an unqualified CREATE SEQUENCE '
                "lands wherever search_path resolves, which is not necessarily the schema "
                "of the table that uses it."
            )

    def _require_a_version_table_schema(self) -> None:
        """Demand an explicit schema only where it cannot be derived."""
        if not self.version_table or self.version_table_schema:
            return
        if len(self.schemas) != 1:
            raise DefinitionError(
                f"{self.name}: holds tables in {len(self.schemas)} schemas "
                f"({', '.join(self.schemas)}), so version_table_schema must say which "
                f"one holds {self.version_table!r}."
            )


def metadata_sequences(metadata: MetaData) -> tuple[Sequence, ...]:
    """Return every explicit ``Sequence`` in this MetaData, ordered by qualified key.

    Reads ``MetaData._sequences``, for which SQLAlchemy offers no public
    accessor. It is the same collection SQLAlchemy's own ``create_all`` renders
    sequences from, so reading it is what keeps this tool's idea of "the
    sequences a document declares" identical to the one that produces the
    document. Measured, it holds both the sequences attached to a column and
    those attached to the MetaData alone.

    Implicit sequences are not in it, which is correct: the one an
    autoincrementing ``Integer`` primary key gets is created by, and dropped
    with, its table, and never carries a schema of its own.

    Shared between ``definition`` (which sequences declare no schema) and
    ``render`` (which schemas a qualified sequence needs) so the two cannot
    drift apart on what "the sequences of a package" means.
    """
    sequences = metadata._sequences
    return tuple(sequences[key] for key in sorted(sequences))


def underlying_type(type_: TypeEngine) -> TypeEngine:
    """Follow ``ARRAY`` (and any other container) down to the type it wraps.

    ``ARRAY(ENUM(...))`` renders the very same unqualified ``CREATE TYPE`` as
    a bare ``ENUM(...)`` column does: measured, ``ARRAY(Enum(...)).item_type``
    is the ``Enum`` instance, and it is that instance's ``.schema`` — not the
    ``ARRAY``'s, which has none — that decides where the type is created. A
    check that inspects ``column.type`` alone stays silent on every enum or
    domain hidden inside an array.

    Shared between ``contract`` (which types are unqualified) and ``render``
    (which schemas a qualified type needs) so the two cannot drift apart on
    what "the type a column actually creates" means.
    """
    item_type = getattr(type_, "item_type", None)
    while item_type is not None:
        type_ = item_type
        item_type = getattr(type_, "item_type", None)
    return type_


def creates_a_schema_bound_type(type_: TypeEngine) -> TypeGuard[Enum | DOMAIN]:
    """Whether this type creates a database object that lives in a schema.

    True for a native ``Enum`` and for ``DOMAIN``: those render the
    ``CREATE TYPE`` and ``CREATE DOMAIN`` statements, and only they carry a
    ``schema`` worth reading. Everything else is False, including two shapes
    that look like they should not be:

    ``Boolean`` is a ``SchemaType`` as well, and it has **no ``schema``
    attribute at all** — ``Boolean().schema`` raises ``AttributeError``, so a
    test written against the base class does not merely misreport the most
    common column type there is, it crashes on it.

    ``Enum(native_enum=False)`` renders as ``VARCHAR`` and creates nothing, so
    treating it as schema-bound would report a violation, and through
    ``raise_on_violations`` abort ``create``, ``diff`` and ``check``, for a
    package that did nothing wrong.

    One predicate rather than one per module: this rule was written out three
    times — twice correctly under two different names, once against
    ``SchemaType``, which crashed on any boolean column. Ask this function.
    """
    if isinstance(type_, DOMAIN):
        return True
    return isinstance(type_, Enum) and bool(type_.native_enum)
