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
never deployed. ``contract.check_conventions`` verifies they match.
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

    @property
    def table_names(self) -> tuple[str, ...]:
        """Return the package's table names, sorted."""
        return tuple(sorted(self.metadata.tables))

    def validate(self) -> None:
        """Raise :class:`DefinitionError` if this definition cannot be used."""
        if not self.name.strip():
            raise DefinitionError("A schema definition needs a non-empty name.")
        if not self.metadata.tables:
            raise DefinitionError(f"{self.name}: metadata has no tables.")
        if self.version_table and self.version_table in self.metadata.tables:
            raise DefinitionError(
                f"{self.name}: version_table {self.version_table!r} is also a data table."
            )
