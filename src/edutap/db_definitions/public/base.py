"""Metadata and declarative base of the contract schema.

Unlike a package that owns its own schema, this one may import
:data:`edutap.db_definitions.definition.NAMING_CONVENTION` rather than copying it:
the reason for copying is that a deployed service must not depend on a tool that is
never deployed, and here tool and declaration are the same distribution.

The metadata is still its own object rather than ``SQLModel.metadata``, which is a
process-wide singleton — a generator that cannot tell packages apart cannot order,
split or diff them.
"""

from sqlalchemy import MetaData
from sqlmodel import SQLModel

from ..definition import NAMING_CONVENTION

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(SQLModel):
    """Declarative base binding the contract tables to their own metadata."""

    metadata = metadata
