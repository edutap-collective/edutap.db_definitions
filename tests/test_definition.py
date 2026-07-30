import dataclasses

import pytest
from sqlalchemy import Column, Integer, MetaData, Table

from edutap.db_definitions.definition import NAMING_CONVENTION, DefinitionError, SchemaDefinition


def make_metadata(*table_names: str) -> MetaData:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    for name in table_names:
        Table(name, metadata, Column("id", Integer, primary_key=True))
    return metadata


def test_table_names_are_sorted():
    definition = SchemaDefinition(name="pkg", metadata=make_metadata("b", "a"))
    assert definition.table_names == ("a", "b")


def test_validate_accepts_a_minimal_definition():
    SchemaDefinition(name="pkg", metadata=make_metadata("a")).validate()


def test_validate_rejects_an_empty_name():
    with pytest.raises(DefinitionError, match="name"):
        SchemaDefinition(name="", metadata=make_metadata("a")).validate()


def test_validate_rejects_metadata_without_tables():
    with pytest.raises(DefinitionError, match="no tables"):
        SchemaDefinition(name="pkg", metadata=make_metadata()).validate()


def test_validate_rejects_a_version_table_that_is_also_a_data_table():
    metadata = make_metadata("a", "alembic_version_pkg")
    definition = SchemaDefinition(
        name="pkg", metadata=metadata, version_table="alembic_version_pkg"
    )
    with pytest.raises(DefinitionError, match="version_table"):
        definition.validate()


def test_definition_is_frozen():
    definition = SchemaDefinition(name="pkg", metadata=make_metadata("a"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        definition.name = "other"
