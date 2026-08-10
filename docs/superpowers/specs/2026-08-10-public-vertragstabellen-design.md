# The contract schema moves here — design

**Date:** 2026-08-10
**Status:** decided (A. Loechel), first implementation in this change

A record of a decision at a point in time. It is not rewritten to match a later
state; a changed decision gets a new record.

## The anomaly

`person_view`, `pass_state` and `pass_instance` were declared by
`edutap.data_provider` — the package that **reads** them. Written they are by
somebody else entirely: a person spooler fills `person_view`, and the pass-state
consumer fills the other two. Ownership had become an accident of who needed a model
class first.

That is not merely untidy. It made every writer depend on a FastAPI service with ten
runtime dependencies in order to get three table classes, and it put the definition
of a shared contract inside one of its consumers.

## The decision

**`public` belongs to this package. Every other schema stays with its service.**

The rule is one sentence and checkable: *whoever owns a schema declares it; `public`
is owned by nobody, therefore by the tool.* It is the same shape as the rights rule
in the schema-split design record, where a service's write access is decided by which
schema a table sits in.

`edutap.pass_builder` keeps announcing its tables through an entry point exactly as
before, as will the signing, apple-web and binding schemas.

## What this costs, stated plainly

The package does **not** get smaller. It gains a second role — declarer as well as
collector — and keeps every existing check, because other packages still announce
schemas and can still collide, still claim a history table twice, still point a
foreign key across an undeclared boundary.

Shrinking would have needed the other variant: all tables of all packages here. That
was rejected because it makes a new column in `edutap.pass_builder` a merge request
in a foreign repository, and it takes schema ownership away from services whose
tables nobody else ever sees.

What the move buys is the dependency direction, and that alone is worth it.

## Registered internally, not through an entry point

The built-in definition is seeded in `discovery._load_all` before the entry points
are read. An entry point would be package metadata pointing at the module that reads
it — a longer way round the same corner.

It goes in **first** so that a collision is reported against the built-in name, which
is the direction that reads correctly: the newcomer is the one redeclaring a contract
table, not the other way round.

Selection behaves like any other package: `--include` narrows to exactly what is
asked for, `--exclude` removes it.

## Weight: what a service now installs

The core install is what a *declaration* needs: SQLAlchemy, SQLModel and the shared
vocabulary from `edutap.data_models`. Alembic, the driver and the settings reader
moved to the `cli` extra.

This matters because importing this package stopped being a developer's business:
`edutap.data_provider` imports the tables at runtime, and so will every writer. A
container that reads a database has no use for a migration engine.

A test guards it rather than a comment: a subprocess imports
`edutap.db_definitions.public` and asserts that neither `alembic` nor `psycopg`
appears in `sys.modules`.

## The migration history

`alembic_version_public`, not `alembic_version_data_provider`. The old name stopped
being true the moment the tables moved, and a wrong name in a migration history
outlives everyone who knew why it was wrong.

Moving it in a database that already has rows is one `INSERT` and one `DROP` —
provided the deployed revision matches the repository. That check happens before
anything is written and is not part of this change.

## The ordering hazard

Between this change and the matching one in `edutap.data_provider`, both packages
declare the same three tables. Installed together they produce a `table_collision`
and refuse to render.

That is the contract check doing its job, and it is loud rather than silent, but it
does mean the two changes belong close together. This package's own test suite is
unaffected: `edutap.data_provider` is not among its development dependencies, and the
`data_provider` extra that used to pull it in is gone.

## Python floor

Raised from 3.12 to 3.13, following `edutap.data_models`. The floor is contagious:
this package now depends on it, so it cannot claim a lower one.
