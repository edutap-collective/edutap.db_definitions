# edutap.db_definitions

`edutap.db_definitions` generates the SQL that defines an eduTAP deployment's
database schema, collected from the schema definitions of the installed eduTAP
packages.
It exists to serve one security rule: no service creates or alters its own
tables.
A service that reads data runs with a read-only database role and no DDL
rights at all; schema changes are prepared with this tool, reviewed as SQL,
and applied once by a privileged role.
The package is a development-time helper, never a deployed service — it emits
SQL files and ships no `Dockerfile`.

```{toctree}
:maxdepth: 2

tutorial
how-to
reference
explanation
```
