# edutap.db_definitions

Declares the **contract schema** `public` and generates the SQL that defines an
eduTAP deployment's database schema — its own tables plus those the installed eduTAP
packages announce.

The **commands** run where the wanted eduTAP packages are installed, and that is the
whole mechanism: the schema they render is a function of the versions present. They
serve two audiences — a developer preparing and reviewing a change, and a **migration
container that runs once per deploy** and then exits.

```{note}
This package once stated it would never be deployed and carry no `Dockerfile`. That
changed on 2026-08-11: rendering on one machine and applying on another silently
assumes both have the same package versions, and nothing enforced it. The container
closes that gap by construction. It **adds only** — a diff that drops anything fails
the deploy instead. See
[the design record](docs/superpowers/specs/2026-08-11-migration-container-design.md).
```

The **declarations** are different. `person_view`, `pass_state` and `pass_instance`
are imported at runtime by the services that read and write them, so the core install
carries nothing a container has no use for — see [Install](#install).

## Why this exists

**One schema has no owner.** `person_view` is written by a person spooler,
`pass_state` and `pass_instance` by the pass-state consumer, and all three are read
by `edutap.data_provider`. Until now the *reader* declared them, which is backwards:
ownership was an accident of who happened to need a model class first. `public`
therefore belongs to this package, and every other schema stays with its service.

No service creates or alters its own tables.
A service that reads data runs with a read-only database role and has no DDL
rights at all — otherwise a compromised reader would hold schema privileges.
Schema changes are therefore prepared here, reviewed as SQL, and applied
once by a privileged role.

`edutap.data_provider` and the HEIDI-Local appliance (a specialised,
proprietary data provider) are the clearest cases: both only read.

## Install

Two installs, because there are two audiences.

**To import the contract tables** — what a service that reads or writes them does:

```shell
pip install edutap.db_definitions
```

That is SQLAlchemy, SQLModel and the shared vocabulary, and nothing else. No
migration engine, no database driver.

**To run the commands:**

```shell
pip install "edutap.db_definitions[cli]"
```

Add the extras for the eduTAP packages that announce schemas of their own; these are
only available from the eduTAP org sources, not from a public index.

```shell
pip install "edutap.db_definitions[cli,pass_builder]"
```

```{note}
There is no `data_provider` extra any more. That package no longer owns tables — it
imports them from here. Installing a version that still declares them alongside this
one makes the contract check refuse the pair, which is the correct outcome.
```

## Commands

Four subcommands over the `edutap-dbdef` entry point.
See [`docs/reference.md`](docs/reference.md) for every flag, and
[`docs/how-to.md`](docs/how-to.md) for the LMU deploy path.

`create` — render the baseline DDL of the selected packages, without a
database connection.

```shell
edutap-dbdef create --out schema.sql
```

`diff` — connect read-only and render the `ALTER` statements that bring the
database in line with the definitions.

```shell
edutap-dbdef diff
```

`check` — like `diff`, but writes no file and exits non-zero on any
deviation; a pre-deploy gate.

```shell
edutap-dbdef check
```

`apply` — apply a previously generated SQL file, with write access.

```shell
edutap-dbdef apply schema.sql
```

## Documentation

The full documentation — tutorial, how-to guides, reference, and
explanation — lives under `docs/` and is built with Sphinx.

```shell
uv pip install -e ".[docs]"
.venv/bin/python -m sphinx docs docs/_build/html
```

## Development setup

Bootstrap the development environment:

```shell
make venv
```

Then run checks and tests:

```shell
make lint        # Run ruff and type checker
make test-local  # Unit tests (no database)
```

For integration tests against a PostgreSQL container, Docker must be
running:

```shell
make test-integration
```

On macOS with Homebrew, `psycopg` needs Homebrew's `libpq` on the dynamic
linker path — it is keg-only and not linked into `/opt/homebrew/lib` by
default.
If `import psycopg` fails with "libpq library not found", export
`DYLD_LIBRARY_PATH` before running pytest directly:

```shell
export DYLD_LIBRARY_PATH=/opt/homebrew/opt/libpq/lib
.venv/bin/python -m pytest -m integration -v
```

Exporting it and then calling `make test-integration` does not work: macOS
strips `DYLD_*` variables when it execs `/bin/sh`, and `make` runs every
recipe line through `/bin/sh`, so the variable never reaches `pytest`.
Run the `pytest` command above directly instead of through `make` on
affected machines.
