# edutap.db_definitions

Generates the SQL that defines an eduTAP deployment's database schema, from
the schema definitions of the installed eduTAP packages.

`edutap.db_definitions` is a development-time helper, never a deployed
service.
It runs where you have the wanted eduTAP packages installed, and emits SQL
files; it has no image in any stack and ships no `Dockerfile`.

## Why this exists

No service creates or alters its own tables.
A service that reads data runs with a read-only database role and has no DDL
rights at all — otherwise a compromised reader would hold schema privileges.
Schema changes are therefore prepared here, reviewed as SQL, and applied
once by a privileged role.

`edutap.data_provider` and the HEIDI-Local appliance (a specialised,
proprietary data provider) are the clearest cases: both only read.

## Install

```shell
pip install edutap.db_definitions
```

Add the extras for the eduTAP packages a deployment works with; these are
only available from the eduTAP org sources, not from a public index.

```shell
pip install "edutap.db_definitions[pass_builder,data_provider]"
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
