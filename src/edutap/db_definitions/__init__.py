"""Generate the database schema SQL for an eduTAP deployment."""

from importlib.metadata import version

__version__ = version("edutap.db_definitions")

__all__ = ["__version__"]
