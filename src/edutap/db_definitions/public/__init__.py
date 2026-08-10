"""The contract schema `public`, declared by this package rather than by a service.

Every other schema is announced by the service that owns it, through an entry point.
`public` has no such owner: `person_view`, `pass_state` and `pass_instance` are
written by consumers and read by `edutap.data_provider`, and the reader declaring
what the writers fill was the anomaly this package corrects.

Registered internally rather than through an entry point of its own. The registration
is one line in :mod:`edutap.db_definitions.discovery`; announcing itself to itself
through package metadata would be a longer way round the same corner.
"""

from ..definition import SchemaDefinition
from . import tables  # noqa: F401  importing registers the tables on the metadata
from .base import Base, metadata

#: Name under which the contract schema appears in `--include` / `--exclude` and in
#: every contract violation message.
NAME = "edutap.db_definitions.public"

#: The migration history of the contract schema.
#:
#: `alembic_version_public`, not `alembic_version_data_provider`: the name has to say
#: whose history it is, and it stopped being the data provider's the moment the
#: tables moved. A wrong name in a migration history outlives everyone who knew why.
VERSION_TABLE = "alembic_version_public"

definition = SchemaDefinition(
    name=NAME,
    metadata=metadata,
    version_table=VERSION_TABLE,
)

__all__ = ["NAME", "VERSION_TABLE", "Base", "definition", "metadata"]
