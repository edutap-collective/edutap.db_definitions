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


@dataclass
class BrokenEntryPoint:
    """An entry point of an installed package that cannot be imported."""

    name: str
    value: str = "broken.package.dbdef:definition"

    def load(self) -> object:
        """Fail the way a broken installed package fails."""
        raise ImportError("No module named 'broken'")


def make_definition(
    name: str,
    *table_names: str,
    requires: tuple[str, ...] = (),
    convention: dict[str, str] | None = None,
    version_table: str | None = None,
    schema: str = "public",
) -> SchemaDefinition:
    """Build a SchemaDefinition with simple tables for tests."""
    metadata = MetaData(naming_convention=convention or NAMING_CONVENTION)
    for table_name in table_names:
        Table(
            table_name,
            metadata,
            Column("id", Integer, primary_key=True),
            Column("label", String(32), nullable=False),
            schema=schema,
        )
    return SchemaDefinition(
        name=name,
        metadata=metadata,
        requires=requires,
        version_table=version_table or f"alembic_version_{name.replace('.', '_')}",
    )


def make_definition_with_foreign_key(name: str, schema: str = "public") -> SchemaDefinition:
    """Build a definition whose second table references the first."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    parent = Table("parent", metadata, Column("id", Integer, primary_key=True), schema=schema)
    Table(
        "child",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey(parent.c.id), nullable=False),
        schema=schema,
    )
    return SchemaDefinition(name=name, metadata=metadata)


def make_definition_with_enum_and_sequence(name: str, schema: str = "public") -> SchemaDefinition:
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
        schema=schema,
    )
    return SchemaDefinition(name=name, metadata=metadata)


def make_definition_with_deferred_foreign_key(
    name: str, schema: str = "public"
) -> SchemaDefinition:
    """Build a definition whose foreign key is added by a separate ALTER TABLE."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("first", metadata, Column("id", Integer, primary_key=True), schema=schema)
    Table(
        "second",
        metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "first_id",
            Integer,
            ForeignKey(f"{schema}.first.id", use_alter=True, name="fk_second_first_id_first"),
        ),
        schema=schema,
    )
    return SchemaDefinition(name=name, metadata=metadata)


def make_cross_package_definitions(
    declare_requires: bool = True,
    schema: str = "public",
) -> tuple[SchemaDefinition, SchemaDefinition]:
    """Two packages where the second one's table references the first one's.

    This is the shape `requires` exists for: `edutap.data_provider` owning the
    view table that `lmu_edutap_full_view` writes a state row against. The
    foreign key names its target as a string, so it stays unresolved until
    something puts both tables into one MetaData.
    """
    provider_metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("view_source", provider_metadata, Column("id", Integer, primary_key=True), schema=schema)

    consumer_metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "view_state",
        consumer_metadata,
        Column("id", Integer, primary_key=True),
        Column("source_id", Integer, ForeignKey(f"{schema}.view_source.id"), nullable=False),
        schema=schema,
    )

    provider = SchemaDefinition(name="pkg.provider", metadata=provider_metadata)
    consumer = SchemaDefinition(
        name="pkg.consumer",
        metadata=consumer_metadata,
        requires=("pkg.provider",) if declare_requires else (),
    )
    return provider, consumer


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
def installed_entry_points(monkeypatch):
    """Install arbitrary entry points into the discovery seam.

    Usage: `installed_entry_points([FakeEntryPoint("schema", definition)])`
    """

    def install(points: list[object]) -> None:
        from edutap.db_definitions import discovery

        monkeypatch.setattr(discovery, "iter_entry_points", lambda: list(points))

    return install


@pytest.fixture
def installed(installed_entry_points):
    """Install fake packages into the discovery seam.

    Usage: `installed([make_definition("pkg.a", "table_a")])`
    """

    def install(definitions: list[SchemaDefinition]) -> None:
        installed_entry_points([FakeEntryPoint(name=d.name, value=d) for d in definitions])

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
