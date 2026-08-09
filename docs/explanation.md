# Explanation

## Why no service creates its own tables

`edutap.db_definitions` exists to serve a single security rule: a service
that reads data runs with a read-only database role and holds no DDL rights
at all.
The reasoning is ordinary least privilege, but it is worth spelling out what
it buys.
A service that can `CREATE`, `ALTER`, or `DROP` its own tables can, if
compromised, also do those things to any table its role can reach in the
same database — a JSON deserialization bug in a read endpoint has no
business being able to rewrite the schema.
Removing DDL rights from every running service removes that entire class of
escalation, regardless of what the application code does or does not
validate.

That only works if schema changes still happen somewhere.
This package is where they happen: it renders the SQL a deployment needs,
a human reviews that SQL, and a privileged role — never the same role a
service authenticates as — applies it.
The review step is not incidental.
`apply` deliberately never generates; the detour through a file that
`create` or `diff` wrote is the point at which a person looks at what is
about to happen to a shared, stateful resource before it happens.

`edutap.data_provider` and the HEIDI-Local appliance are the clearest cases
for why this matters at LMU: both only read, from a database that other
packages also write to.
Nothing in their code needs to know how to create a table, so nothing in
their code is given the ability to.

That leaves the read-only role itself, and the grants that back it, for
someone to create — see the {ref}`note in the how-to guide <ddl-role-scope>`
for where that work lives and why it stays out of this tool.

## Why a per-package `MetaData`

SQLModel gives every model a home on `SQLModel.metadata` by default, a
single object shared by every model in the process.
That is convenient inside one service, where "all my tables" and "everything
on `SQLModel.metadata`" are the same set.
It stops being convenient the moment a tool needs to reason about several
packages at once, because importing one package can pull in others
transitively, and every model those other packages define registers on the
same shared metadata too.
At that point, "the tables of package X" is no longer a question the global
metadata can answer — there is only one bucket, and everything eventually
ends up in it.

`edutap.db_definitions` needs that answer to do anything at all: to order
packages topologically by their declared dependencies, to render one
package's tables in isolation for `--split`, to detect two packages that
accidentally define a table of the same name, and to diff a database against
exactly the packages a site selected rather than against whatever else
happened to be importable.
None of that is possible without a metadata object per package.
The fix is small — a package inherits its models from a `Base` that carries
its own `MetaData` instead of the default `SQLModel` — but it is not
optional, and it is why {doc}`how-to` is quite insistent about it.

The naming convention that goes with that metadata is copied into each
package rather than imported from this one, and deliberately so.
Importing it would give every deployed service a runtime dependency on a
tool that itself is never deployed, purely to reach a `dict` literal.
`check` verifies the copies still agree, which is a small price for keeping
the dependency graph pointing the right way.

## Why the schema is mandatory

An unqualified table name is not resolved by this tool, it is resolved by
PostgreSQL's `search_path` at whatever moment the table gets created.
With the common `search_path = "$user", public`, that means the very same
`CREATE TABLE thing` lands in a schema named after the connecting role
wherever one exists, and falls through to `public` wherever it does not —
which is exactly how the same code has ended up with its tables in `edutap`
locally and in `public` in production, unnoticed, because nothing in either
run was wrong on its own terms.
Declaring `schema=` on every table is what makes the two runs identical
instead of merely usually identical, so `SchemaDefinition.validate()` refuses
to guess on a package's behalf — see {doc}`reference` for the exact rule and
the message a package author sees.

## Why the diff is generated, not hand-written

`diff` and `check` do not implement their own comparison logic.
They call Alembic's `compare_metadata`, the same machinery
`alembic revision --autogenerate` uses to write migration scripts, and hand
the result to Alembic's own offline SQL renderer.
Reimplementing schema comparison would mean re-deriving a large set of
PostgreSQL-specific rules — how column types compare across driver
representations, how server defaults compare, how indexes and constraints
are matched by name — that Alembic has already worked out and tested against
real databases for years.
There is no reason to prefer a smaller, newer, less-exercised version of the
same logic over the one the ecosystem already trusts for exactly this
purpose.

This has a consequence worth naming: `edutap.db_definitions` inherits
Alembic's blind spots along with its correctness.

## Why the comparison folds the default schema away

Alembic represents the connection's default schema as `None`.
It reflects that schema under that key, and it keys the tables it found by it.
A package's declaration, by the rule above, always says `'public'`
explicitly.
Compared literally, the two would differ on every single run, and every table
in the default schema would be reported as an `add_table` for a table that is
plainly already there — a comparison that can never go green is worse than no
comparison at all.

The comparison therefore runs against a throwaway copy of the declared
metadata, built once per comparison, with the connection's default schema
folded away to match the shape Alembic produces: the table itself, a
foreign key's target, and a column's type all fold the same way.
That is not a stylistic choice — folding the table while leaving its foreign
key qualified makes the key unresolvable inside the copy, and SQLAlchemy
raises `NoReferencedTableError` before the comparison starts.
Folding is not the same as dropping the declaration.
The package keeps declaring its schema explicitly — that is what makes the
tool's claim and the database agree in the first place — and every other
consumer of the metadata (`create`, the contract checks, `foreign_tables`)
keeps using the name the package actually wrote.
Only this one throwaway copy is massaged, and only because reflection leaves
no other way to compare like with like.
Anything derived from the fold that is actually written to a file — `diff`'s
SQL — is requalified again before rendering: the mandatory-schema rule above
applies to generated DDL just as much as to a package's own declaration, so
the fold cannot be allowed to leak into it.

A later reader who was not there for this reasoning has one place it becomes
visible: the diagnostic lines `check` prints for a change it found are
Alembic's own `repr()` of an operation against the *folded* copy, not against
what a package wrote.
A default-schema table therefore appears in that list as `thing`, not
`public.thing` — for example `('add_column', None, 'thing', <Column ...>)` —
and the `None` there is the folded default schema, not a place the tool
forgot to qualify.
It is worth knowing before it is mistaken for one: nothing about that line
was left unfinished, it is exactly the copy built to compare cleanly,
reported by a library that never saw the declared name at all.

### Why the comparison pins `search_path`

Alembic's rule — only the default schema is `None` — is narrower than
PostgreSQL's.
Reflection omits the schema of everything *visible on the `search_path`*, so
a foreign key into `public.pass_state` comes back as `referred_schema: None`
whenever `public` is on the path, whatever the default schema happens to be.
The two rules coincide only when the path holds nothing but the default
schema.

A DDL role set up the way a schema-per-service split invites — `ALTER ROLE
edutap_ddl SET search_path = pass_builder, public` — breaks that coincidence.
The default schema is then `pass_builder`, so the fold clears `pass_builder`
and leaves the declared `'public'` standing, while reflection reports the key
into `public` unqualified.
Measured against PostgreSQL 18, `check` then reports `remove_fk`, `add_fk`
and `modify_type` on every run, and `diff` proposes dropping and re-adding a
healthy foreign key — against a database that is exactly what `create`
produced.

For the duration of a reflection, `check` and `diff` therefore pin the
connection's `search_path` to its own default schema and restore it
afterwards.
That makes PostgreSQL's omission rule and Alembic's `None` rule the same rule.
It changes nothing about *which* objects are inspected — every lookup this
tool makes names its schema explicitly — only whether the names come back
qualified.

You do not need to configure anything for this, and in particular you should
not "fix" it by narrowing the DDL role's `search_path` yourself: the tool
handles the path it is given.

## The known limits of autogenerate

`compare_metadata` compares two static pictures of a schema — the Python
metadata and the live database — and describes the operations that turn one
into the other.
It cannot see intent, only shape, and some intentions are indistinguishable
from a different intention when only the shape is visible.

A column rename is the clearest case.
Renaming `template.label` to `template.title` produces a metadata that, read
in isolation, differs from the database by one column added and one column
removed.
`compare_metadata` reports exactly that: an add and a drop, not a rename,
because nothing in a `MetaData` object records that the new column
*used to be* the old one.
The generated statements would work — the data in the dropped column is
simply gone — which is exactly why `diff` renders drops as commented-out
DDL by default and requires `--allow-destructive` to enable them.
A renamed column reviewed carelessly through that gate looks identical to an
actually-dropped one.

Some type changes render incompletely for related reasons: PostgreSQL does
not always accept a bare `ALTER COLUMN ... TYPE` for every source-to-target
type pair, and where a `USING` clause is needed to tell PostgreSQL how to
reinterpret existing values, nothing in a metadata diff can invent one — that
clause encodes exactly the kind of intent a static comparison cannot see.

Sequences are a blind spot of a different kind: `compare_metadata` compares
tables, not sequences, so an explicit `Sequence` that does not exist in the
database yet produces no `CREATE SEQUENCE` in a `diff` — whether the column
that defaults to it belongs to a brand-new table or to one already in the
database.
Measured on an *existing* table gaining a column: `diff` renders
`CREATE SCHEMA IF NOT EXISTS seqlib2;` and
`ALTER TABLE ... ADD COLUMN n INTEGER DEFAULT nextval('seqlib2.counter2')`,
with no `CREATE SEQUENCE` anywhere in the document, and applying it fails the
same `UndefinedTable` way as the new-table case.
The `CREATE SCHEMA` its schema needs *is* emitted — that part `check` and
`diff` do know about — but the sequence itself arrives through `create`.

For a genuinely new table, that is the whole fix: the baseline document is
the right instrument for a new object, so run `create` and the sequence is
in it.
For a new column on an existing table, `create` is not an option — it
renders `CREATE TABLE IF NOT EXISTS`, a no-op against a table the database
already has, so the new column never reaches it either — and `diff` is the
only route left, with the sequence missing from what it produces.
The operator has to hand-write the `CREATE SEQUENCE` statement, using the
schema and name the diff's own `nextval(...)` call already names, and apply
it — ahead of the diff, or folded into the same reviewed file — before the
`ALTER TABLE ... ADD COLUMN` that depends on it.

Data migrations sit outside this entirely.
A comparison between two schemas has nothing to say about the values that
should occupy a new column, or about reshaping data that a structural change
left in the wrong place.
That is real migration work, and it is why the *Later* section of the design
keeps Alembic's own upgrade scripts — `alembic_ini` and `version_table` are
already part of the package contract for this reason — as the answer for
packages whose migrations carry data, rather than trying to stretch a
metadata diff to cover it.

### A domain loses its constraints on the way out

One limit is not Alembic's and is worth separating from the rest, because it
silently weakens the database rather than merely failing to describe it.

Rendering goes through `merged_metadata`, which copies every table with
`Table.to_metadata`; that copies every column with `Column._copy`; and that,
for a `DOMAIN`, calls SQLAlchemy 2.0.51's `DOMAIN.copy()`, which drops
`default`, `not_null`, `check`, `constraint_name` and `collation` (measured).
A domain declared with `DEFAULT '1' NOT NULL CHECK (VALUE > 0)` is created as
`CREATE DOMAIN typelib.positive_int AS INTEGER;`, so the column accepts values
its own declaration forbids.
`check` then reports the schema as in sync, because Alembic does not compare a
domain's constraints — the two blind spots line up and leave nothing to notice.

The loss belongs to SQLAlchemy, not to this tool, and the honest repair is to
rebuild the declared type onto the copied column in `merged_metadata` rather
than to work around it further downstream.
It has not been built: it means reconstructing a `DOMAIN` field by field
against a constructor signature that may change, for a construct no eduTAP
package uses today.
What is built is a pair of tests, one rendering-level and one against a live
database, that pin the current output — so a SQLAlchemy release that fixes
`DOMAIN.copy()` turns those tests red instead of changing generated DDL
unnoticed.
{doc}`how-to` says what to do in the meantime.

None of this is a defect to route around inside `edutap.db_definitions`.
It is the reason `diff` prints its limits into the document it generates,
and the reason `apply` insists that a human reads that document before
anything runs against a real database.
