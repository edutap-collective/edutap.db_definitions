import dataclasses
from dataclasses import replace

import pytest
from sqlalchemy import Column, Integer, MetaData, Table

from edutap.db_definitions.definition import NAMING_CONVENTION, DefinitionError, SchemaDefinition
from tests.conftest import make_definition


def make_metadata(*table_names: str, schema: str | None = None) -> MetaData:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    for name in table_names:
        Table(name, metadata, Column("id", Integer, primary_key=True), schema=schema)
    return metadata


def test_table_names_are_sorted():
    definition = SchemaDefinition(name="pkg", metadata=make_metadata("b", "a"))
    assert definition.table_names == ("a", "b")


def test_validate_accepts_a_minimal_definition():
    SchemaDefinition(name="pkg", metadata=make_metadata("a", schema="public")).validate()


def test_validate_rejects_an_empty_name():
    with pytest.raises(DefinitionError, match="name"):
        SchemaDefinition(name="", metadata=make_metadata("a")).validate()


def test_validate_rejects_metadata_without_tables():
    with pytest.raises(DefinitionError, match="no tables"):
        SchemaDefinition(name="pkg", metadata=make_metadata()).validate()


def test_validate_rejects_a_version_table_that_is_also_a_data_table():
    metadata = make_metadata("a", "alembic_version_pkg", schema="public")
    definition = SchemaDefinition(
        name="pkg", metadata=metadata, version_table="alembic_version_pkg"
    )
    with pytest.raises(DefinitionError, match="version_table"):
        definition.validate()


def test_definition_is_frozen():
    definition = SchemaDefinition(name="pkg", metadata=make_metadata("a"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        definition.name = "other"


def test_a_table_without_a_schema_is_rejected():
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("thing", metadata, Column("id", Integer, primary_key=True))
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)

    with pytest.raises(DefinitionError) as error:
        definition.validate()

    assert "thing" in str(error.value)
    assert "schema" in str(error.value)


def test_the_schemas_of_a_definition_are_reported_sorted():
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("second", metadata, Column("id", Integer, primary_key=True), schema="zulu")
    Table("first", metadata, Column("id", Integer, primary_key=True), schema="alpha")
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)

    assert definition.schemas == ("alpha", "zulu")


def test_a_single_schema_package_derives_the_schema_of_its_version_table():
    """The field stays None; the derivation lives in `version_table_key`.

    `SchemaDefinition` is frozen, so the field can only hold what the package
    declared. Deriving in the property keeps one answer instead of two that can
    drift apart.
    """
    definition = make_definition("pkg.a", "thing", schema="pass_builder")

    definition.validate()

    assert definition.version_table_schema is None
    assert definition.version_table_key == "pass_builder.alembic_version_pkg_a"


def test_a_multi_schema_package_must_name_its_version_table_schema():
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("one", metadata, Column("id", Integer, primary_key=True), schema="alpha")
    Table("two", metadata, Column("id", Integer, primary_key=True), schema="beta")
    definition = SchemaDefinition(
        name="pkg.a", metadata=metadata, version_table="alembic_version_a"
    )

    with pytest.raises(DefinitionError) as error:
        definition.validate()

    assert "version_table_schema" in str(error.value)


def test_an_explicit_version_table_schema_wins_over_the_derived_one():
    definition = make_definition("pkg.a", "thing", schema="pass_builder")
    explicit = replace(definition, version_table_schema="public")

    explicit.validate()

    assert explicit.version_table_key == "public.alembic_version_pkg_a"


def test_the_version_table_may_not_also_be_a_data_table():
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("history", metadata, Column("id", Integer, primary_key=True), schema="alpha")
    definition = SchemaDefinition(
        name="pkg.a",
        metadata=metadata,
        version_table="history",
        version_table_schema="alpha",
    )

    with pytest.raises(DefinitionError) as error:
        definition.validate()

    assert "history" in str(error.value)
