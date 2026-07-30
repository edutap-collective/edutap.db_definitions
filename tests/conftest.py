"""Fakes that stand in for installed eduTAP packages."""

from dataclasses import dataclass

import pytest
from sqlalchemy import (
    Column,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    Sequence,
    String,
    Table,
)

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


def make_definition_with_enum_and_sequence(name: str) -> SchemaDefinition:
    """Build a definition using schema objects that are not tables or indexes.

    A native PostgreSQL enum type and an explicit sequence both need their own
    ``CREATE`` statement before the table that uses them. `edutap.pass_builder`
    uses native enum types, so this is the shape of a real target package.
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    sequence = Sequence("provider_thing_id_seq")
    Table(
        "provider_thing",
        metadata,
        Column(
            "id",
            Integer,
            sequence,
            server_default=sequence.next_value(),
            primary_key=True,
        ),
        Column("provider", Enum("apple", "google", name="provider"), nullable=False),
    )
    return SchemaDefinition(name=name, metadata=metadata)


def make_definition_with_deferred_foreign_key(name: str) -> SchemaDefinition:
    """Build a definition whose foreign key is added by a separate ALTER TABLE."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("first", metadata, Column("id", Integer, primary_key=True))
    Table(
        "second",
        metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "first_id",
            Integer,
            ForeignKey("first.id", use_alter=True, name="fk_second_first_id_first"),
        ),
    )
    return SchemaDefinition(name=name, metadata=metadata)


@pytest.fixture(autouse=True)
def clean_database_environment(monkeypatch):
    """Remove ambient connection variables so settings tests see only what they set.

    Settings reads the environment at instantiation. A developer shell or CI runner
    that exports PGPORT, PGHOST or DATABASE_URL would otherwise make these tests
    pass or fail for reasons unrelated to the code.
    """
    for name in (
        "DATABASE_URL",
        "PGDATABASE",
        "PGHOST",
        "PGPASSWORD",
        "PGPORT",
        "PGSSLMODE",
        "PGSSLROOTCERT",
        "PGUSER",
        "EDUTAP_DBDEF_DATABASE",
        "EDUTAP_DBDEF_DSN",
        "EDUTAP_DBDEF_HOST",
        "EDUTAP_DBDEF_PASSWORD",
        "EDUTAP_DBDEF_PORT",
        "EDUTAP_DBDEF_SSLMODE",
        "EDUTAP_DBDEF_SSLROOTCERT",
        "EDUTAP_DBDEF_USER",
    ):
        monkeypatch.delenv(name, raising=False)


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


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Start a PostgreSQL container and return a psycopg URL for it."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:18-alpine", driver="psycopg") as container:
        yield container.get_connection_url()


@pytest.fixture
def engine(postgres_url):
    """A fresh engine on an empty public schema."""
    from sqlalchemy import create_engine, text

    engine = create_engine(postgres_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()
