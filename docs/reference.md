# Reference

Command name: `edutap-dbdef`.
Package name: `edutap.db_definitions`.
Entry-point group a package uses to announce its tables: `edutap.db_definitions`.

## Commands

### `create`

Renders the baseline DDL of the selected packages into one SQL document,
against the PostgreSQL dialect, without connecting to a database.

```text
edutap-dbdef create [--packages PACKAGES] [--exclude EXCLUDE]
                     [--out OUT] [--split SPLIT]
                     [--ddl-role DDL_ROLE] [--timestamp]
```

| Flag | Argument | Default | Meaning |
|---|---|---|---|
| `--packages` | comma-separated names | all installed | render only these packages |
| `--exclude` | comma-separated names | none | skip these packages |
| `--out` | path | none (stdout) | write the document to this file |
| `--split` | directory path | none | write one file per package into this directory, named `<package>.sql`, instead of one combined file |
| `--ddl-role` | role name | none | add `SET ROLE <role>;` to the document header; without it the header carries a `-- NOTE: generated without --ddl-role; ...` line instead and the command repeats that note on standard error |
| `--timestamp` | flag | off | add a `-- generated: <ISO 8601 UTC timestamp>` header line |

The document is rendered by SQLAlchemy's own schema emitter, so it contains
every object the tables need, not only the tables themselves: enum types,
explicit sequences, indexes, and the `ALTER TABLE ... ADD CONSTRAINT` of a
deferred foreign key.
Types are emitted once, in a leading `-- ===== types =====` section, before the
per-package `-- ===== <package> =====` sections: a type belongs to the schema
rather than to one package.
With `--split`, every file repeats the type creation so that each file can be
applied on its own.
It is repeatable: `CREATE TABLE`, `CREATE INDEX`, and `CREATE SEQUENCE` carry
`IF NOT EXISTS`, and the statements PostgreSQL has no `IF NOT EXISTS` form
for — `CREATE TYPE` and `ALTER TABLE ... ADD CONSTRAINT` — are wrapped in a
`DO $$ ... EXCEPTION WHEN duplicate_object THEN NULL; END $$;` block.
That block swallows exactly `duplicate_object`; any other error still aborts
the surrounding transaction.

Two runs with the same package selection produce a byte-identical document
unless `--timestamp` is given — the header records package versions instead,
which is what makes a diff in a deploy repository meaningful.
`--split` and `--out` both name a destination; passing both is accepted, but
`--split` takes effect and `--out` is ignored, because the code checks
`--split` first.
An empty selection — a typo in `--packages`, or no eduTAP package installed —
is refused with a `RenderError` and exit code `1` rather than written out as a
valid-looking document that creates nothing.

### `diff`

Connects to a database read-only and renders the `ALTER` statements that
bring it in line with the selected packages' definitions, via Alembic's
`compare_metadata`.

```text
edutap-dbdef diff [--packages PACKAGES] [--exclude EXCLUDE]
                   [--out OUT] [--ddl-role DDL_ROLE]
                   [--allow-destructive]
```

| Flag | Argument | Default | Meaning |
|---|---|---|---|
| `--packages` | comma-separated names | all installed | compare only these packages |
| `--exclude` | comma-separated names | none | skip these packages |
| `--out` | path | none (stdout) | write the document to this file |
| `--ddl-role` | role name | none | add `SET ROLE <role>;` to the document header; without it the header carries a `-- NOTE: generated without --ddl-role; ...` line instead and the command repeats that note on standard error |
| `--allow-destructive` | flag | off | emit `DROP TABLE`/`DROP COLUMN`/`DROP CONSTRAINT`/`DROP INDEX` statements uncommented instead of commented out |

Needs a database connection, configured as described under
{ref}`connection-settings`.
The role only needs read access.
Like `create` and `check`, `diff` validates the package contract across the
selected definitions before comparing — see the `ContractError` case under
`check` and {ref}`exceptions`.
Tables present in the database that belong to no selected package are
reported on standard error and otherwise ignored — a shared database
legitimately holds tables of packages this site did not select.
Known limits, restated in the document itself: renames are not detected
(they appear as a drop and an add), some type changes render incompletely,
and data migrations are out of scope.

### `check`

Behaves like `diff` without writing a document: it reports whether the
database deviates from the definitions and sets the process exit code
accordingly.

```text
edutap-dbdef check [--packages PACKAGES] [--exclude EXCLUDE]
```

| Flag | Argument | Default | Meaning |
|---|---|---|---|
| `--packages` | comma-separated names | all installed | compare only these packages |
| `--exclude` | comma-separated names | none | skip these packages |

Needs a database connection, read-only.
Prints `Schema is in sync with the definitions.` and exits `0` when there is
nothing to do.
Otherwise prints `Schema deviates from the definitions:` followed by one
line per deviation on standard error, and exits `1`.
Before comparing, `check` also validates the package contract across the
selected definitions and exits `1` with a `ContractError` message — see
{ref}`exceptions` — if any package uses a different naming
convention, two packages claim the same `version_table`, two packages
define a table of the same name, or a package's foreign key references
another selected package's table without declaring that package in
`requires`.

### `apply`

Applies a previously generated SQL document to a database.
`apply` never generates SQL itself: it only executes a file that `create` or
`diff` produced and a human has reviewed.

```text
edutap-dbdef apply [--dry-run] FILE
```

| Argument | Meaning |
|---|---|
| `FILE` | positional; path to the SQL file to apply |
| `--dry-run` | do not execute anything; report how many characters of SQL would run |

Needs a database connection with write access.
The document supplies its own `BEGIN;`/`COMMIT;`, so `apply` runs the
connection in `AUTOCOMMIT` and hands the whole document to the driver as one
unit rather than nesting it in a second transaction.
On success it prints `Executed <N> statements.`, counting schema statements
only — `BEGIN`, `COMMIT`, `SET ROLE`, and comments are not counted.
With `--dry-run` it still reads `FILE`, but does not open a database
connection; it prints `Dry run, nothing executed.` and exits `0`.

(ddl-role)=

## The `--ddl-role` header

`create` and `diff` share one document header, so `--ddl-role` behaves
identically in both.

The role name must be a plain PostgreSQL identifier
(`^[A-Za-z_][A-Za-z0-9_$]*$`); anything else is refused with a `RenderError`
rather than interpolated into a file that a privileged role will apply.
The name is emitted through the PostgreSQL identifier preparer, so a
mixed-case role keeps its case (`--ddl-role Edutap_DDL` renders
`SET ROLE "Edutap_DDL";`) instead of silently folding to lower case and
producing a different owner.

Omitting the flag is never silent: the header carries

```sql
-- NOTE: generated without --ddl-role; objects will be owned by whichever user applies this file.
```

and the same note goes to standard error when the document is generated.

(connection-settings)=

## Connection settings

Only `diff`, `check`, and `apply` read connection settings; `create` never
connects.
Every setting has a prefixed name and, for most, a standard `PG*` alias; the
prefixed name takes precedence when both are set.

| Setting | `EDUTAP_DBDEF_*` variable | `PG*` alias | Default |
|---|---|---|---|
| DSN | `EDUTAP_DBDEF_DSN` | `DATABASE_URL` | none |
| Host | `EDUTAP_DBDEF_HOST` | `PGHOST` | `postgres` |
| Port | `EDUTAP_DBDEF_PORT` | `PGPORT` | `5432` |
| Database | `EDUTAP_DBDEF_DATABASE` | `PGDATABASE` | `edutap` |
| User | `EDUTAP_DBDEF_USER` | `PGUSER` | `edutap_ddl` |
| Password | `EDUTAP_DBDEF_PASSWORD` | `PGPASSWORD` | empty |
| SSL mode | `EDUTAP_DBDEF_SSLMODE` | `PGSSLMODE` | none |
| SSL root certificate | `EDUTAP_DBDEF_SSLROOTCERT` | `PGSSLROOTCERT` | none |

When a DSN is set (`EDUTAP_DBDEF_DSN` or `DATABASE_URL`), it is used as-is
and every other setting above is ignored.
Otherwise the individual settings are assembled into a
`postgresql+psycopg://` URL.
The driver is synchronous `psycopg`, not `asyncpg`: Alembic's
`compare_metadata` and DDL rendering are synchronous APIs, and a CLI has no
concurrency to gain.

The DSN is not available as a command-line flag, only through the
environment (`EDUTAP_DBDEF_DSN` or `DATABASE_URL`).

## `SchemaDefinition`

`edutap.db_definitions.SchemaDefinition` is a frozen dataclass.
A package constructs one and exposes it through the `edutap.db_definitions`
entry-point group, either as the object itself or as a zero-argument
callable that returns one.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `name` | `str` | yes | the package's name, used in headers, `--packages`/`--exclude` selection, and error messages |
| `metadata` | `sqlalchemy.MetaData` | yes | the package's own metadata; must not be `SQLModel.metadata` or another package's metadata |
| `requires` | `tuple[str, ...]` | no, default `()` | names of packages this one's tables depend on; orders the packages and makes their metadata resolvable as one, which is what lets a foreign key cross a package boundary. Required whenever such a foreign key exists — a missing entry is a contract violation |
| `alembic_ini` | `str \| None` | no, default `None` | path to the package's `alembic.ini`; carried and validated, unused until Alembic offline mode |
| `version_table` | `str \| None` | no, default `None` | the package's own `alembic_version`-style table name; must be unique across the selected packages and must not also be a data table |

`SchemaDefinition` also provides:

`table_names`
: property; the package's table names from `metadata.tables`, sorted.

`validate()`
: method; raises `DefinitionError` if `name` is empty, if `metadata` has no
  tables, or if `version_table` names a table that also exists as a data
  table in `metadata`.
  Called automatically by discovery before a definition is used.

`edutap.db_definitions.NAMING_CONVENTION` is the canonical constraint naming
convention every package's `MetaData` must copy — see {doc}`how-to`.
`check` compares each package's convention against this constant.

(exceptions)=

## Exceptions

`edutap.db_definitions.DefinitionError`
: a package's `SchemaDefinition` cannot be used, raised by
  `SchemaDefinition.validate()`.
  Only the *selected* definitions are validated, so an unusable definition in
  a package this run excludes is not an error here.
  Caught by `main()`: message to standard error, exit code `1`.

`edutap.db_definitions.discovery.DiscoveryError`
: the installed definitions cannot be used together, raised by
  `load_definitions()` when `requires` describes a dependency cycle between
  packages, or when two entry points announce the same package `name` — the
  latter would silently drop one package's tables from every document.
  Caught by `main()`: message to standard error, exit code `1`.

  An entry point that cannot be imported at all (a broken installed package)
  is not an error: it is logged with the entry point's name and skipped, so
  one broken package a site does not use cannot block a run that excludes it.
  A selected package that never appears is reported by the
  "requested but is not installed" warning.

`OSError` and `sqlalchemy.exc.SQLAlchemyError`
: an unreadable input file, an unwritable output path, or a failing database
  connection.
  Caught by `main()`: one line to standard error, exit code `1`.
  Errors that indicate a bug in this package are deliberately *not* caught and
  still surface as a traceback.

`edutap.db_definitions.render.RenderError`
: the requested document cannot be rendered — no package is selected, or
  `--ddl-role` is not a valid PostgreSQL identifier.
  Caught by `main()`: it prints the message to standard error and returns
  exit code `1`.

`edutap.db_definitions.contract.ContractError`
: the selected packages cannot share one database, raised by
  `raise_on_violations()` after `check_contract()` found one or more
  violations — a table name owned by more than one package, a
  `version_table` claimed by more than one package, a naming convention
  that differs from `NAMING_CONVENTION`, or a foreign key into another
  selected package that `requires` does not declare.
  `create`, `diff`, and `check` load and validate the contract before doing
  their own work; `apply` does not load package definitions at all, since it
  only executes a file it is handed.
  `main()` catches `ContractError` for every subcommand: it prints the
  violation list to standard error and returns exit code `1`.
