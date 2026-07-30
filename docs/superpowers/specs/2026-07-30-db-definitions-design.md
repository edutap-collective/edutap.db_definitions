# Design: `edutap.db_definitions` — schema SQL generator for eduTAP packages

Date: 2026-07-30
Status: approved

## Goal

Produce the SQL that defines an eduTAP deployment's database schema, collected
from the DB definitions of the installed eduTAP packages.

The driving constraint is a security rule: **no service creates or alters its own
tables.** A service that reads data runs with a read-only database role and has no
DDL rights at all — otherwise a compromised reader would hold schema privileges.
Schema changes are therefore prepared here, reviewed as SQL, and applied once by a
privileged role.

This package is a **helper, never deployed.** It runs in a development or
deploy-preparation environment that has the wanted eduTAP packages installed, and
emits SQL files. It is not a service, has no image in any stack, and is not part of
any application image.

Non-goals: roles and grants (deployment policy — see *Ownership and grants*),
creating the database itself, data migrations, and any runtime behaviour. The
package knows nothing about application logic, only table definitions.

## Starting point: what exists today

Inventory of the eduTAP/LMU packages that own tables, taken 2026-07-30:

| Package | Tables | Definition style | Migration today | Layout driven by |
|---|---|---|---|---|
| `edutap.pass_builder` | 11: `tenant`, `template`, `template_version`, `template_variant`, `template_asset`, `mapping_rule`, `credential_set`, `secret_blob`, `data_field`, `api_client`, `audit_log` | SQLModel, global metadata | Alembic, 1 revision | us (own domain model) |
| `edutap.apple_wallet_vas_signing_service` | 5: `pass_types`, `signing_certificates`, `api_keys`, `api_key_pass_types`, `signing_audit` | SQLModel, global metadata | `create_all` at app start | Apple (cert/pass-type model) + us |
| `edutap.apple_wallet_vas_account_binding_callback` | 4: `apple_account_binding`, `apple_account_binding_session`, `apple_account_binding_callback_event`, `apple_account_binding_config` | SQLModel, global metadata | `create_all` | Apple (account binding protocol) |
| `edutap.apple_wallet_vas_web_service` | 3: `appledeviceregistry`, `applepassdata`, `applepassregistry` (names derived from class names) | SQLModel, global metadata | `create_all` | Apple (Wallet Web Service) |
| `lmu_edutap_full_view` | 2: `heidi_full_view`, `pass_state` | SQLModel, global metadata | Alembic, 1 revision | HEIDI (vocabularies) + us (JSONB payload) |
| `fastapi-auth-saml-federated` | 3: `samlsession`, `samloutstanding`, `samlseenassertion` | SQLModel, global metadata | `create_all` (async store) | us (session store) |
| `edutap.data_provider` | planned: view table, pass state | — | — | us (under design) |

28 tables in six packages: 13 under Alembic, 15 created by `create_all`. **None**
uses its own `MetaData` — all register on the global `SQLModel.metadata`.

Two conclusions shape this design:

* **Schema stability splits by origin.** The 12 Apple tables follow Apple's
  specifications and change only when Apple changes. The `pass_builder` tables and
  the upcoming `data_provider` tables are our own domain model and will evolve.
* **Policy going forward:** DB models we design ourselves use Alembic; the
  Apple-driven ones do not yet. Retrofitting Alembic there is work, but no
  disadvantage — it is a later step, not a prerequisite for this package.

`lmu_db_migrate` is the LMU-specific prototype of this package: a runner with a
hand-maintained registry that applies `alembic upgrade head` or
`metadata.create_all` per package as role `edutap_ddl`. `edutap.db_definitions`
supersedes it. Its two lasting contributions are adopted here: the `PG*`/prefixed
settings aliases, and running as a dedicated DDL role. Its known gap — one shared
`alembic_version` table for all packages — is fixed (see *Package contract*).

## Ownership and grants

The LMU deployment applies SQL files as the `postgres` superuser
(`swarmed_postgres:run_sql`), while its privilege model hangs on **who creates the
table**: `ALTER DEFAULT PRIVILEGES FOR ROLE edutap_ddl` only grants on objects that
`edutap_ddl` creates. A file applied as `postgres` therefore produces
`postgres`-owned tables and silently bypasses the grants — the collision recorded
as an open point in the ops design (`ansible-app-server`,
`docs/superpowers/specs/2026-07-14-edutap-db-roles-migrations-design.md`).

Generated files therefore carry a role header: with `--ddl-role edutap_ddl`
the file starts with `SET ROLE edutap_ddl;`. Objects are then owned by that role no
matter which user applies the file (`SET ROLE` to one's own role is a no-op; a
superuser may assume any role).

**Without the option the file says so**, in the same slot:
`-- NOTE: generated without --ddl-role; objects will be owned by whichever user
applies this file.`, and the command repeats it on stderr. Silence there is not
neutral: a reviewer of a committed `schema.sql` cannot otherwise distinguish
"deliberately no role" from "forgot the flag", and the difference decides whether
the deployment's default-privilege grants apply at all.

The role name is validated against `^[A-Za-z_][A-Za-z0-9_$]*$` and emitted through
the dialect's identifier preparer. Both halves matter: the file is destined for a
superuser, so interpolating an unvalidated string is an injection; and an unquoted
`Edutap_DDL` would fold to `edutap_ddl` and silently produce a different owner
than the one asked for.

Grants themselves are **not** generated. Role names and the privilege matrix are
deployment policy, and the deployment's `grants.sql` solves it structurally better:
`ALTER DEFAULT PRIVILEGES` also covers all future tables, whereas generated grants
would need regenerating and reapplying for every new table.

## Package contract

Each package that owns tables defines its own `MetaData` and a base class:

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

All tables of the package inherit from `Base` instead of `SQLModel` directly. This
is the only change needed in existing packages. Verified behaviour: a SQLModel
subclass carrying its own `metadata` attribute registers its tables exclusively on
that metadata; the global `SQLModel.metadata` stays untouched.

Why a per-package `MetaData` at all: `SQLModel.metadata` is a process-wide
singleton. With every package registering there, "the tables of package X" is not
determinable once transitive imports pull other packages in — and a generator that
cannot tell packages apart cannot order them, split them, or diff them.

The naming convention is **copied** into each package, not imported from here.
Importing would give every service a runtime dependency on a tool that is never
deployed. `check` verifies that all packages use the same convention. Unnamed
constraints produce noisy diffs and cannot be altered by name later, which is why
the convention is part of the contract rather than a recommendation.

A package announces itself through an entry point:

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

`name` and `metadata` are required, the rest optional. `alembic_ini` and
`version_table` are carried and validated but unused until the Alembic offline mode
arrives (see *Later*). The per-package `version_table` matters: in a shared
database a single `alembic_version` table would let the packages overwrite each
other's history.

Discovery is by entry point, not by a registry in this package: how a package
migrates is knowledge that belongs to the package, where it cannot go stale, and
third-party packages work without a change here. The registry-shaped part —
*which* packages a site installs — lives in the install line instead (see
*Package selection*).

Ordering is a topological sort over `requires`. Today no foreign key crosses a
package boundary (`pass_state` references `heidi_full_view` inside the same
package); it becomes relevant when the view and pass-state tables move to
`edutap.data_provider` and `lmu_edutap_full_view` only writes them.

Ordering alone is not enough to make such a key work. A `ForeignKey` resolves by
table name **within one `MetaData`**, so a key into another package's `MetaData`
raises `NoReferencedTableError` in any order. `create` and `diff` therefore build
one merged `MetaData` in `requires` order first and render each package's slice of
it: the key resolves, `merged.sorted_tables` gives the globally correct order, and
the per-package `-- ===== name =====` sections stay. `check` enforces the
declaration that makes the order right (see *`check`*).

## Commands

Command name `edutap-dbdef`; the package name stays `edutap.db_definitions`.

| Command | Does | Needs a database |
|---|---|---|
| `create` | renders the baseline DDL of the selected packages into one SQL file | no |
| `diff` | compares the definitions against an existing database, renders the `ALTER` statements | yes, read-only |
| `check` | like `diff` but writes no file: exits non-zero on any deviation | yes, read-only |
| `apply` | applies a previously generated SQL file | yes, read-write |

### `create`

Load entry points → filter to the selection → sort topologically → render each
package's tables from its `MetaData` in dependency order, with indexes and
constraints. Rendered against the PostgreSQL dialect **without a connection**, and
sorted deterministically so that two runs produce byte-identical files and a diff
in the deploy repository stays meaningful.

Rendering uses **SQLAlchemy's own emitter** — `create_mock_engine` plus
`metadata.create_all(engine, checkfirst=False)` — not a hand-rolled loop over
`CreateTable`/`CreateIndex`. A schema is more than tables: native enum types,
explicit sequences and the `ALTER TABLE … ADD CONSTRAINT` of a deferred
(`use_alter`) foreign key all need their own statement, in dependency order.
`create_all` produces them; a hand-rolled loop silently drops them, which would
also make `create` disagree with `diff`, whose Alembic renderer does emit them.

```sql
-- edutap-dbdef create
-- packages: edutap.data_provider (0.1.0), edutap.pass_builder (0.1.0)
BEGIN;
SET ROLE edutap_ddl;          -- or a NOTE line without --ddl-role
-- ===== types =====
DO $$ BEGIN CREATE TYPE provider AS ENUM (…); EXCEPTION … END $$;
-- ===== edutap.data_provider =====
CREATE TABLE IF NOT EXISTS person_view ( … );
…
COMMIT;
```

Types come first, in one section of their own: a type belongs to the schema
rather than to a package — SQLAlchemy scopes its creation to the `MetaData`, not
to a single table — and repeating it per package section would suggest that one
package creates another package's type. `create --split` repeats them in each
file, which the guarded blocks make harmless, because each file has to stand
alone.

The header records tool and package versions, so it stays traceable which state a
file came from. A **timestamp is opt-in** (`--timestamp`): by default the output
must be byte-identical across runs, and a timestamp would defeat exactly that — the
package versions carry the provenance instead. One transaction around everything:
PostgreSQL runs DDL transactionally, so an abort leaves no half-built schema.

`IF NOT EXISTS` makes the file repeatable — for tables, indexes and sequences.
PostgreSQL has **no `IF NOT EXISTS` form** for `CREATE TYPE`, nor for the
`ALTER TABLE … ADD CONSTRAINT` a deferred foreign key renders as. Those statements
are therefore wrapped in

```sql
DO $$ BEGIN
    CREATE TYPE provider AS ENUM ('apple', 'google');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
```

so that the repeatability promise holds for the whole document and not only for
its tables. The block swallows exactly `duplicate_object`; every other error still
aborts the transaction.

### `diff`

Connects read-only and compares the collected metadata against the live schema via
Alembic's `compare_metadata` — the same machinery behind
`alembic revision --autogenerate`. The comparison is not reimplemented.

Destructive statements (`DROP TABLE`, `DROP COLUMN`, `DROP CONSTRAINT`) are emitted
**commented out** and marked; `--allow-destructive` enables them. Tables present in
the database but belonging to no selected package are ignored and listed — a shared
database legitimately holds foreign tables (`alembic_version*`, tables of packages
this site did not select).

Known limits, stated in the output: renames are not detected (they appear as drop +
add), some type changes render incompletely, and data migrations are out of scope
for a diff. The output is meant to be read before it runs.

### `check`

`diff` without a file: empty diff → exit 0, otherwise exit 1 plus a readable list of
deviations. Intended for CI and as a pre-deploy gate — with mostly stable schemas
this is the everyday use, and it is the same machinery.

It additionally verifies the package contract: identical naming convention across
packages, unique `version_table` per package, no table-name collision between two
packages, and no foreign key into another package that `requires` does not declare.
The table-name collision must fail hard: in a shared database it would mean silent
data loss. The undeclared dependency must fail too, because with the merged
`MetaData` it no longer breaks loudly — it would quietly produce a file whose
ordering happens to be right or wrong.

### `apply`

Applies a file, with `--dry-run` for a rehearsal. It never generates: first
`create`/`diff` writes a file, then a human reads it, then `apply`. The detour
through the file is the review point, deliberately.

### Output shape

`create` and `diff` write **one** file across all packages. `create --split`
produces one file per package, for deployments that apply packages separately;
`diff` has no `--split`, because an `ALTER` set is only reviewable as a whole.

## Package selection

Known packages are optional extras:

```bash
pip install "edutap.db_definitions[data_provider,pass_builder,full_view]"
```

The install line — a `Makefile` target at LMU — decides which packages a site
works with. At runtime `--packages` / `--exclude` narrow it further. A selected but
uninstalled package is logged and skipped, not an error: otherwise every site would
be forced to install every package.

## Configuration and execution

Only `diff`, `check` and `apply` connect. Configuration through pydantic-settings
with double aliases: `EDUTAP_DBDEF_*` **or** the standard `PG*` variables
(`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE`,
`PGSSLROOTCERT`), plus `EDUTAP_DBDEF_DSN` (or `DATABASE_URL`) for a ready-made URL
that overrides the individual fields. There is deliberately no `--dsn` command line
flag: the environment variable covers the same need, and connection details on a
command line end up in shell history and process listings. This matches what the LMU ops runner
already passes to its migration container, including
`PGTARGETSESSIONATTRS=read-write` and `verify-full` against the internal CA.

The driver is **synchronous `psycopg`, not `asyncpg`** — a deliberate deviation from
the async-first house rule. Alembic's `compare_metadata` and DDL rendering are
synchronous APIs, and a CLI has no concurrency to gain. That `pass_builder` runs
`asyncpg` in production is irrelevant here: this tool reads metadata, never the
service's engine.

**PostgreSQL only**, target version 18. Rendering targets the PostgreSQL dialect;
other dialects are not a goal, which avoids special cases around JSONB, `Uuid` and
collations.

**No Dockerfile** — a deliberate exception to the house rule that every web service
ships a Docker test environment. This is not a service. A deployment that wants a
migration image builds it in its own repository with the extras it needs.

## Deployment integration (LMU)

Two supported paths, no Ansible change required for either:

1. Generate, review, commit the SQL into the deploy repository, apply it with the
   existing `swarmed_postgres:run_sql` — using `--ddl-role edutap_ddl` so ownership
   is right despite `run_sql` connecting as `postgres`.
2. Keep the existing `migrate.yml` container (already connecting as `edutap_ddl`
   over `PG*` env) and call `edutap-dbdef apply` inside it.

The ops design left this as an open dev decision; this spec answers it. Once either
path runs, the legacy `db/schema.sql` import can be removed as planned there.

## Architecture

```
src/edutap/db_definitions/
    __init__.py       # public exports: SchemaDefinition
    definition.py     # SchemaDefinition dataclass + validation
    discovery.py      # entry-point loading, selection, topological ordering
    contract.py       # cross-package checks: convention, version tables, collisions
    render.py         # DDL rendering (create) against the PostgreSQL dialect
    compare.py        # diff/check via alembic compare_metadata
    execute.py        # apply, dry-run, connection handling
    settings.py       # pydantic-settings (EDUTAP_DBDEF_* / PG*)
    cli.py            # the four subcommands
```

Each module has one job: `discovery` knows about installed packages, `render` and
`compare` produce SQL text, `execute` is the only module that writes to a database,
`settings` is the only one that reads the environment. `render` and `compare` are
pure functions over metadata and are testable without a database.

## Testing

Test-first: every behaviour gets a failing test first.

* **Unit tests** (`make test-local`), no database: render DDL against the dialect
  and assert on the text, using fake packages that carry their own `MetaData` and
  are announced through an entry-point fixture (the pattern `edutap.webhook_heidi`
  uses for its queue backends). Covered: deterministic ordering, role header with
  and without `--ddl-role`, destructive statements commented out, topological
  ordering, table-name collision between two packages, diverging naming convention,
  selected-but-missing package.
* **Integration tests** (`make test-integration`): `diff`, `check` and `apply`
  against a real PostgreSQL through `testcontainers[postgres]` — the technique
  `pass_builder` already uses. Covered: empty diff after `create`, an added column
  showing up as `ALTER`, `check` exit codes, `apply --dry-run` changing nothing.

## Tooling

`uv` for environment and dependencies; runtime dependencies `sqlalchemy`,
`alembic`, `psycopg`, `pydantic-settings` — and nothing else. Notably **not**
`sqlmodel`: packages hand over plain SQLAlchemy `MetaData` objects, so this tool
never touches SQLModel itself. The CLI uses stdlib `argparse`; four subcommands do
not justify a CLI framework. Dev extra with
`pytest`, `testcontainers[postgres]`, `ruff`, `ty`, `pdbp`. `ruff` for lint and
format, `ty` for type checking, `prek` as hook runner, `tox` over 3.12/3.13/3.14.
GitHub Actions mirrors the local checks. `Makefile` targets `lint`, `reformat`,
`test-local`, `test-integration`. Documentation with Sphinx + MyST following
Diataxis, including a how-to for the LMU deploy path.

## Later

* **Alembic offline mode.** Render `alembic upgrade <current>:head --sql` per
  package for packages that maintain a migration history with data migrations —
  the one thing a metadata diff cannot do. The entry point already carries
  `alembic_ini` and `version_table` so this arrives without a contract change.
* **Retrofit Alembic** in the Apple-driven packages, and drop their `create_all`
  calls at app start. Until then those services still create their own tables,
  which is exactly what the security rule forbids — so the conversion is the
  precondition for the rule actually holding.
* **Schema-qualified tables.** Everything is rendered into the connection's
  default schema; a package that puts tables into a named schema is not supported
  yet and is not rejected either.

## Open points

* The `create_all` calls in the four affected packages must be removed once
  `db_definitions` produces their schema; until then both paths exist in parallel.
* `lmu_db_migrate` can be retired after the switch; nothing needs to be carried
  over except the extras selection in the LMU `Makefile`.
* Whether the generated SQL is committed to the deploy repository or produced as a
  build artefact is a deployment decision, not settled here.
