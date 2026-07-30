"""Generate the database schema SQL for an eduTAP deployment."""

from importlib.metadata import version

from .definition import NAMING_CONVENTION, DefinitionError, SchemaDefinition

__version__ = version("edutap.db_definitions")

__all__ = ["NAMING_CONVENTION", "DefinitionError", "SchemaDefinition", "__version__"]
