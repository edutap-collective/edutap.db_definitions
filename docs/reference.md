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
A leading `-- ===== schemas =====` section emits one
`CREATE SCHEMA IF NOT EXISTS` per schema the document needs — every schema a
data table, the version table, a qualified type, or an explicit sequence
lives in.
`public` is never among them: every PostgreSQL database already has it, and
creating it needs `CREATE` on the *database*, a right a plain DDL role has no
reason to hold — emitting it unconditionally would make the document fail
for the very role it is meant to be applied by.
Types are emitted next, in a `-- ===== types =====` section, before the
per-package `-- ===== <package> =====` sections: a type belongs to a schema
rather than to one package.
With `--split`, every file repeats the type creation of every selected
package, not only its own, so that it can be applied on its own without
another file's tables around to supply the types they need.
It therefore also repeats the schema creation of every type in the whole
document: a file may need to create a schema it holds no table of its own
in, purely to house another package's type. `CREATE SCHEMA IF NOT EXISTS`
makes that harmless.
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
The comparison spans every schema the selected packages own, not only the
connection's default schema, and the rendered document opens with a
`CREATE SCHEMA IF NOT EXISTS` for any of them the database does not have yet
— restricted to what is actually missing, unlike `create`'s preamble, because
`CREATE SCHEMA` needs `CREATE` on the *database* even in its
`IF NOT EXISTS` form, and a diff has to remain applicable by a role that only
holds `CREATE` on its own schemas.
Tables in the selected packages' schemas that belong to none of them are
reported, schema-qualified, on standard error as
`Ignored tables of other owners` and otherwise left alone — a shared database
legitimately holds tables of packages this site did not select, even inside
a schema this site owns.
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
The comparison spans every schema the selected packages own, not only the
connection's default schema, and reports a schema the selection needs that
the database lacks entirely — not only a table missing inside a schema that
does exist — as its own deviation, first in the list:

```text
missing_schema: 'history' is needed by the selected packages but does not
exist in the database
```

That line is the one written as a sentence; the rest of the list is
Alembic's own `repr()` of the operation it derived, for example
`('add_column', None, 'thing', <Column ...>)`.
The `None` there is Alembic's tuple form for "the connection's default
schema", not a comparison this tool lost track of — see {doc}`explanation`
for why the comparison folds the default schema away before comparing.
Before comparing, `check` also validates the package contract across the
selected definitions and exits `1` with a `ContractError` message — see
{ref}`exceptions` — if any package uses a different naming
convention, two packages claim the same `version_table`, two packages
define a table of the same name, a package's foreign key references
another selected package's table without declaring that package in
`requires`, or an enum or domain column would be created outside its
table's schema.

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

### `migrate`

Renders the diff, decides on it, and applies it — the one command the
migration container runs. Unlike `diff` followed by `apply` there is no file
in between, and therefore no window in which the database can change between
being read and being written.

```text
edutap-dbdef migrate [--packages LIST] [--exclude LIST] [--ddl-role ROLE] [--dry-run]
```

**It may add and may not take away.** A diff that only adds is applied; a diff
containing `DROP TABLE`, `DROP COLUMN`, `DROP CONSTRAINT` or `DROP INDEX` is
refused, nothing is applied, and the run exits `3`.

| Situation | Exit | Output |
|---|---|---|
| Database matches the definitions | `0` | `Schema is in sync with the definitions; nothing to do.` |
| Only additive changes | `0` | `Applied <N> statement(s).` followed by each statement |
| Anything destructive | `3` | the refused statements, in full, on standard error |
| `--dry-run` | `0` | `Dry run: <N> statement(s) would be applied.` |
| Database unreachable, contract violated | `1` | the error |

Exit `3` is its own code rather than `1`, because a deploy that stops here has
not found something broken — it has found a change that a person has to look
at, and telling the two apart decides whether somebody gets paged.

The refusal prints the statements themselves, not a count. Whoever reads it is
looking at a red deploy in a log window, without the file.

```{warning}
**The rule catches `DROP`, and only `DROP`.** A **type change is applied, not
refused** — `ALTER COLUMN ... TYPE` carries no destructive marker, and
narrowing a type can lose data as thoroughly as dropping a column. Measured on
2026-08-11 and pinned by a test, so that a future change to the classification
has to come past it.

If a type change is in the diff, it belongs in a reviewed SQL step, not in a
deploy.
```

```{note}
A **rename renders as drop + add**, so renaming a column or table stops the
deploy and has to be applied deliberately. That is the intended outcome of the
rule, and it will still surprise someone.

`CREATE INDEX` counts as additive and takes a write lock for the duration of
the build. On a table that is already large this is an outage;
`CREATE INDEX CONCURRENTLY` cannot run inside the transaction the document
wraps everything in, so such an index is a change to prepare by hand.

One drop refuses the **whole** diff, including its additive half. A deploy that
applied what it could and then stopped would leave the schema in a state
neither set of declarations describes, and the next run would diff against
that.
```

While it runs, `migrate` holds a session-scoped PostgreSQL advisory lock, so
two concurrent runs cannot interleave. The lock covers **rendering and
applying** together: the renderer reflects the database to decide what is
missing, and the statements it emits carry no `IF NOT EXISTS`, so a second run
that has already concluded "this table is absent" would fail a deploy that was
correct.

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

`--ddl-role` decides only who owns the objects this tool creates; the roles,
grants, and each package's Alembic `env.py` that go with a schema-per-service
split are somebody else's job — see the {ref}`note in the how-to guide
<ddl-role-scope>`.

(connection-settings)=

## Connection settings

Only `diff`, `check`, `apply` and `migrate` read connection settings; `create`
never connects.
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
| `version_table` | `str \| None` | no, default `None` | the package's own `alembic_version`-style table name; must be unique across the selected packages (by its qualified name, see `version_table_key`) and must not also be a data table |
| `version_table_schema` | `str \| None` | no, default `None`; required when `version_table` is set and the package holds tables in more than one schema | which of the package's schemas holds the history table; derived automatically when the package holds tables in exactly one schema |

`SchemaDefinition` also provides:

`table_names`
: property; the package's schema-qualified table names from
  `metadata.tables` (e.g. `"public.thing"`), sorted.

`schemas`
: property; the sorted, deduplicated schemas the package's tables declare.
  Only meaningful once `validate()` has passed — before that, an undeclared
  table's schema is `None` and is filtered out of this property rather than
  raising.

`version_table_key`
: property; the schema-qualified name of the history table
  (`"<schema>.<version_table>"`), or `None` if `version_table` is unset.
  The schema is `version_table_schema` where given, or the package's single
  schema where `schemas` holds exactly one entry — `validate()` is what
  guarantees the ambiguous case never reaches this property.

`validate()`
: method; raises `DefinitionError` if `name` is empty, if `metadata` has no
  tables, if any table declares no schema, if any explicit `Sequence`
  declares no schema, if `version_table` is set and the
  package's tables span more than one schema without a `version_table_schema`
  to say which one holds it, or if `version_table_key` names a table that
  also exists as a data table in `metadata`.
  Called automatically by discovery before a definition is used.

Every table must declare its schema — `__table_args__ = {"schema": "<name>"}`
on a SQLModel class, or `schema="<name>"` on a raw `Table` — or `validate()`
rejects the definition with the message a package author actually sees:

```text
<package>: these tables declare no schema: <table>. Add __table_args__ =
{"schema": "<name>"} (or schema="<name>" on the Table) — the schema decides
who may write the table, so it cannot be left to search_path.
```

See {doc}`explanation` for why an unqualified table is not merely untidy.

An explicit `Sequence` must declare its schema for the same reason and is
rejected the same way:

```text
<package>: these sequences declare no schema: <sequence>. Add
schema="<name>" to the Sequence(...) — an unqualified CREATE SEQUENCE lands
wherever search_path resolves, which is not necessarily the schema of the
table that uses it.
```

A sequence is a relation, in the same namespace as a table, and it fails
worse: an unqualified one applies cleanly into the wrong schema, and `check`
does not then report a deviation — it aborts with `UndefinedTable`, against
the very database `create` produced.
Note that `diff` creates the *schema* a new sequence needs but not the
sequence itself; Alembic's autogenerate does not compare sequences, so a
brand-new sequence reaches the database through `create` — for a new table.
Measured, the same gap reaches an *existing* table gaining a column that
defaults to a new sequence: `diff` renders the `ALTER TABLE ... ADD COLUMN`
and the `CREATE SCHEMA` the sequence's schema needs, but no
`CREATE SEQUENCE`, and applying it fails the same way. `create` cannot help
there — it renders `CREATE TABLE IF NOT EXISTS`, a no-op against a table
that already exists — so the sequence has to be created by hand ahead of
the diff.

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
  that differs from `NAMING_CONVENTION`, a foreign key into another
  selected package that `requires` does not declare, or an enum or domain
  column (`unqualified_type`) that would be created outside its table's
  schema.
  `create`, `diff`, and `check` load and validate the contract before doing
  their own work; `apply` does not load package definitions at all, since it
  only executes a file it is handed.
  `main()` catches `ContractError` for every subcommand: it prints the
  violation list to standard error and returns exit code `1`.
