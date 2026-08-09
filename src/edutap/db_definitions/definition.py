"""The contract a package uses to announce its tables."""

from dataclasses import dataclass, field

from sqlalchemy import MetaData

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
