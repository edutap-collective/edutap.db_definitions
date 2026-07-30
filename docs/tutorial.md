# Tutorial: generate and apply your first schema

In this tutorial, you set up `edutap.db_definitions`, announce one table
through a tiny example package, generate its SQL, apply that SQL to a real
PostgreSQL database, and confirm the database now matches the definition.
You need Python 3.12 or later and a running Docker daemon.

## Create a virtual environment and install the package

Clone this repository if you have not already, then, from its root, create a
virtual environment and install the package in editable mode.

```shell
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Confirm the command line tool is on your path.

```shell
edutap-dbdef --help
```

You should see the four subcommands: `create`, `diff`, `check`, and `apply`.

```{note}
A real deployment selects known eduTAP packages through extras, for example
`pip install "edutap.db_definitions[pass_builder]"`.
This tutorial builds a small example package of its own instead, so that it
works without access to the eduTAP package index.
See {doc}`how-to` for the full package contract.
```

## Announce one table

Create a second directory next to your clone for the example package — one
level up from the `edutap.db_definitions` checkout you are in now.

```shell
cd ..
mkdir -p dbdef-tutorial-example/src/dbdef_tutorial_example
cd dbdef-tutorial-example
```

Write its `pyproject.toml`.

```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "dbdef-tutorial-example"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["sqlalchemy>=2.0"]

[project.entry-points."edutap.db_definitions"]
schema = "dbdef_tutorial_example.dbdef:definition"

[tool.hatch.build.targets.wheel]
packages = ["src/dbdef_tutorial_example"]
```

Write `src/dbdef_tutorial_example/dbdef.py`.
It defines one table, `widget`, using the canonical naming convention.
Copy the convention's dict literal into the package instead of importing it
— {doc}`how-to` explains why: importing it would give a deployed package a
runtime dependency on a tool that is never deployed.

```python
"""Announces the widget table to edutap.db_definitions."""

from edutap.db_definitions import SchemaDefinition
from sqlalchemy import Column, Integer, MetaData, String, Table

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

Table(
    "widget",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(64), nullable=False),
)

definition = SchemaDefinition(name="dbdef_tutorial_example", metadata=metadata)
```

Add an empty `src/dbdef_tutorial_example/__init__.py`, then install the
package into the same virtual environment.

```shell
touch src/dbdef_tutorial_example/__init__.py
pip install -e .
```

## Generate the SQL

Go back to your `edutap.db_definitions` checkout and run `create`.

```shell
cd ../edutap.db_definitions
edutap-dbdef create --out create.sql
```

Open `create.sql`.
It contains one transaction with one `CREATE TABLE`.

```sql
-- edutap-dbdef create
-- packages: dbdef_tutorial_example (0.1.0)
BEGIN;
-- ===== dbdef_tutorial_example =====
CREATE TABLE IF NOT EXISTS widget (
	id SERIAL NOT NULL,
	name VARCHAR(64) NOT NULL,
	CONSTRAINT pk_widget PRIMARY KEY (id)
);
COMMIT;
```

Notice the header: it records which packages went into the file and at which
version, instead of a timestamp, so that running `create` again produces a
byte-identical file.

## Start a PostgreSQL container

Start a local PostgreSQL 18 instance and point the tool at it.

```shell
docker run --rm -d --name dbdef-tutorial \
  -e POSTGRES_USER=edutap_ddl \
  -e POSTGRES_PASSWORD=edutap_ddl \
  -e POSTGRES_DB=edutap \
  -p 5432:5432 \
  postgres:18-alpine

export PGHOST=localhost
export PGUSER=edutap_ddl
export PGPASSWORD=edutap_ddl
export PGDATABASE=edutap
```

Give the container a few seconds to accept connections before continuing.

## Apply the SQL

```shell
edutap-dbdef apply create.sql
```

You should see:

```console
Executed 1 statements.
```

## Confirm the database is in sync

```shell
edutap-dbdef check
```

You should see:

```console
Schema is in sync with the definitions.
```

`check` exits with status 0 when it printed that line, and with status 1 when
it found a deviation.
You have now generated, applied, and verified a schema without ever letting
a service create its own table.

When you are done, stop the container.

```shell
docker stop dbdef-tutorial
```

Continue with {doc}`how-to` to see how a real eduTAP package announces its
tables and how the LMU deployment applies the generated SQL.
