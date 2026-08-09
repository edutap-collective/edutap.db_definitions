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

Give every table its schema.
This is not optional: `edutap-dbdef` refuses a definition that leaves it to
`search_path` to decide — see {doc}`explanation` for why.
On a SQLModel class, set `__table_args__`; on a raw SQLAlchemy `Table`, pass
`schema=`.

```python
class Certificate(Base, table=True):
    __table_args__ = {"schema": "edutap_pass_builder"}

    id: int = Field(primary_key=True)
```

Give an enum or domain column its schema the same way, since SQLAlchemy scopes
a type to the *metadata* rather than to the table that uses it: pass
`inherit_schema=True` so the type takes the schema of the table it is used
on — the common case — or `schema="<name>"` to pin one explicitly.

```python
Column("kind", Enum("a", "b", name="kind", inherit_schema=True))
```

Skipping this is a contract violation, not a silent default: `check_contract`
reports it as `unqualified_type`, and `create`, `diff`, and `check` refuse to
run rather than create the type wherever `search_path` happens to resolve —
typically `public`, a namespace every package then shares.
Only a native enum or a `DOMAIN` needs this; `Enum(..., native_enum=False)`
renders as a plain `VARCHAR` and creates no type at all, so it is not
checked.
The check walks columns, so a type that is attached only to the `MetaData`
(`Enum(..., metadata=metadata)`) and never assigned to a column is invisible
to it — give every type you declare a column to live on.

````{warning}
A `DOMAIN` reaches the database **without its constraints**.
Measured on SQLAlchemy 2.0.51: rendering copies each table's columns, and
`DOMAIN.copy()` silently drops `default`, `not_null`, `check`,
`constraint_name` and `collation`.
A domain declared

```python
DOMAIN("positive_int", Integer, schema="typelib",
       default="1", not_null=True, check="VALUE > 0")
```

is created as `CREATE DOMAIN typelib.positive_int AS INTEGER;` — an alias that
accepts `-5` and `NULL`.
`check` does not catch it either: Alembic does not compare a domain's
constraints, so the schema is reported as in sync.

This is a data-integrity hazard, not a cosmetic gap.
Until SQLAlchemy fixes the copy, do not rely on a domain to enforce anything.
Put the rule where the tool does carry it — a `CheckConstraint` on the column,
or a `NOT NULL` on the column itself — and keep the domain for the type alias
alone.
````

Give an explicit `Sequence` its schema too.

```python
counter = Sequence("certificate_id_seq", schema="edutap_pass_builder")
```

A sequence is a relation, in the same namespace as a table, so a bare
`CREATE SEQUENCE` lands wherever `search_path` resolves — not necessarily
where the table whose column defaults to it lives.
`validate()` rejects it, and unlike the table case there would be no second
chance to notice: an unqualified sequence *applies* cleanly into the wrong
schema, and `check` afterwards does not report a deviation, it aborts with
`UndefinedTable`.
This concerns only sequences you write out; the implicit one behind an
autoincrementing integer primary key belongs to its table and needs nothing.

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
Write the target schema-qualified too, `ForeignKey("public.pass_state.id")`
rather than `ForeignKey("pass_state.id")`: an unqualified target string
escapes this check entirely — nothing about it names a package for the check
to compare against `requires` — so nothing stops the run here. It still
fails, just later and from SQLAlchemy itself (`NoReferencedTableError`),
with a message that never mentions `requires`.
Give every package its own `version_table` name if it uses Alembic: a shared
database with one `alembic_version` table for every package would let the
packages overwrite each other's migration history.
Add `version_table_schema` too once the package holds tables in more than one
schema — `validate()` cannot otherwise tell which of them holds the history
table. With exactly one schema, it is derived automatically and can be left
unset.

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
A file generated without the flag says so in its header — if you find a
`-- NOTE: generated without --ddl-role; ...` line in a `schema.sql` destined
for this deployment, regenerate it with the flag.
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
