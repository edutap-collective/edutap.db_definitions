"""Fakes that stand in for installed eduTAP packages."""

from dataclasses import dataclass

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table

from edutap.db_definitions.definition import NAMING_CONVENTION, SchemaDefinition


@dataclass
class FakeEntryPoint:
    """Mimics importlib.metadata.EntryPoint for the discovery seam."""

    name: str
    value: object

    def load(self) -> object:
        """Return the object the entry point points at."""
        return self.value


def make_definition(
    name: str,
    *table_names: str,
    requires: tuple[str, ...] = (),
    convention: dict[str, str] | None = None,
    version_table: str | None = None,
) -> SchemaDefinition:
    """Build a SchemaDefinition with simple tables for tests."""
    metadata = MetaData(naming_convention=convention or NAMING_CONVENTION)
    for table_name in table_names:
        Table(
            table_name,
            metadata,
            Column("id", Integer, primary_key=True),
            Column("label", String(32), nullable=False),
        )
    return SchemaDefinition(
        name=name,
        metadata=metadata,
        requires=requires,
        version_table=version_table or f"alembic_version_{name.replace('.', '_')}",
    )


def make_definition_with_foreign_key(name: str) -> SchemaDefinition:
    """Build a definition whose second table references the first."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    parent = Table("parent", metadata, Column("id", Integer, primary_key=True))
    Table(
        "child",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey(parent.c.id), nullable=False),
    )
    return SchemaDefinition(name=name, metadata=metadata)


@pytest.fixture
def installed(monkeypatch):
    """Install fake packages into the discovery seam.

    Usage: `installed([make_definition("pkg.a", "table_a")])`
    """

    def install(definitions: list[SchemaDefinition]) -> None:
        from edutap.db_definitions import discovery

        points = [FakeEntryPoint(name=d.name, value=d) for d in definitions]
        monkeypatch.setattr(discovery, "iter_entry_points", lambda: points)

    return install
