# How-to guides

These guides solve specific problems with `edutap.db_definitions`.
For the full flag list of every subcommand, see {doc}`reference`.
For the reasoning behind the package contract and the `--ddl-role` option, see
{doc}`explanation`.

## Announce a package's tables

This guide shows you how to make an eduTAP package's tables visible to
`edutap.db_definitions`.

Give the package its own `MetaData` and a base class, instead of the global
`SQLModel.metadata` singleton.
Copy the naming convention below verbatim — do not import it from
`edutap.db_definitions`, which would give a deployed service a runtime
dependency on a tool that is never deployed.

```python
# edutap/pass_builder/models/base.py
from sqlalchemy import MetaData
from sqlmodel import SQLModel

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(SQLModel):
    metadata = metadata
```

Let every table of the package inherit from `Base` instead of `SQLModel`
directly.
This is the only change needed in an existing package: a SQLModel subclass
that carries its own `metadata` attribute registers its tables exclusively on
that metadata, and the global `SQLModel.metadata` stays untouched.

Describe the package with a `SchemaDefinition` and announce it through an
entry point in the package's own `pyproject.toml`.

```toml
[project.entry-points."edutap.db_definitions"]
schema = "edutap.pass_builder.models.dbdef:definition"
```

```python
definition = SchemaDefinition(
    name="edutap.pass_builder",
    metadata=metadata,
    requires=(),                                   # ordering, for cross-package FKs
    alembic_ini="alembic.ini",                     # optional
    version_table="alembic_version_pass_builder",  # own history in a shared database
)
```

`name` and `metadata` are required.
Set `requires` to the names of packages whose tables this package's foreign
keys reference — `edutap.db_definitions` sorts packages topologically before
rendering, and resolves such a foreign key by merging the selected packages'
metadata in that order.
A foreign key into another package's table without the matching `requires`
entry is a contract violation: `create`, `diff`, and `check` refuse to run and
name both packages.
Give every package its own `version_table` name if it uses Alembic: a shared
database with one `alembic_version` table for every package would let the
packages overwrite each other's migration history.

If `edutap-dbdef check` reports a `naming_convention` violation for your
package, compare your copy against the block above — a copy-paste drift is
the usual cause.

## Generate the schema for the LMU deployment

This guide shows you how to produce the SQL for an LMU deployment and get it
applied with the right table ownership.

Install `edutap.db_definitions` with the extras for the packages this
deployment runs.

```shell
pip install "edutap.db_definitions[pass_builder,data_provider]"
```

Generate the SQL with `--ddl-role edutap_ddl`.

```shell
edutap-dbdef create --ddl-role edutap_ddl --out schema.sql
```

```{important}
Do not drop `--ddl-role edutap_ddl` here.
The LMU deployment applies SQL files as the `postgres` superuser through
`swarmed_postgres:run_sql`, but its privilege model hangs on *who creates the
table*: `ALTER DEFAULT PRIVILEGES FOR ROLE edutap_ddl` only grants on objects
that `edutap_ddl` itself creates.
`--ddl-role edutap_ddl` adds a `SET ROLE edutap_ddl;` line to the header, so
the tables end up owned by `edutap_ddl` no matter which user applies the
file — `SET ROLE` to one's own role is a no-op, and a superuser may assume
any role.
Without that line, `run_sql` leaves the tables owned by `postgres`, and the
deployment's default-privilege grants silently do not apply to them.
```

Review `schema.sql`, then commit it into the deploy repository.
Apply it with the existing `swarmed_postgres:run_sql` task, the same way any
other administrative SQL file is applied in that deployment.

Alternatively, if the deployment already runs the `migrate.yml` container
that connects as `edutap_ddl` over the standard `PG*` environment variables,
skip the role flag and the deploy-repository commit, and call
`edutap-dbdef apply schema.sql` inside that container instead — the role is
then already correct because of who the container connects as.

## Use `check` as a pre-deploy gate

This guide shows you how to fail a deployment early when the database has
drifted from the definitions, without ever granting the checking role write
access.

Configure a database role that can only read the schema — `check` never
writes.
Point `edutap-dbdef` at it through the `PG*` or `EDUTAP_DBDEF_*` environment
variables — see {doc}`reference` for the full list — then run:

```shell
edutap-dbdef check
```

`check` prints `Schema is in sync with the definitions.` and exits `0` when
there is nothing to do.
When the database deviates, it prints one line per deviation to standard
error and exits `1`.
Wire the exit code into your pipeline so a deviating schema fails the job
instead of silently proceeding to deploy application code against a database
it does not match.

```{tip}
Run `check` with the same package selection (`--packages`/`--exclude`) that
`create` used to generate the schema currently applied.
A different selection reports the other selection's tables as foreign, which
is correct but not what a pre-deploy gate usually wants to see.
```
