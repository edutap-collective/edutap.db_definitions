# Changelog

## 0.2.1 (unreleased)

- `public.photo` gains the state `draft`: a version uploaded but not yet
  confirmed by its owner. No reviewer sees it and no row in
  `public.photo_review` mentions it. The column stays `varchar`, which is what
  its own description promised — a new state must not force a migration.
- New partial unique index `uq_photo_one_draft_per_person`, holding "at most one
  candidate per person" the same way `uq_photo_one_active_per_person` holds "at
  most one active version". The writing service clears a previous candidate
  before inserting the next, but a person with two tabs open is a race, and an
  application-level check loses it by construction.
- New nullable column `public.photo.draft_details`. The validation report is
  produced when a file is uploaded and belongs in the review entry, which is
  written when its owner confirms — two different requests, so it has to wait
  somewhere that is not process memory. It moves into the entry on confirmation
  and is cleared, because one report in two places is how the two come apart.
- `public.photo.rights_declared_at` becomes nullable. The rights declaration is
  made when the person confirms the candidate they were shown, not when the
  bytes arrive, so a candidate they discard never carried one. Previously the
  column was `NOT NULL` with a default, which would have dated a declaration
  nobody had made yet.

## 0.1.0 (unreleased)

- Initial release: `create`, `diff`, `check` and `apply` over the
  `edutap.db_definitions` entry-point group.
- Schema-aware definitions: every table must declare its schema, `create`
  emits the `CREATE SCHEMA` statements a document needs, and `check`/`diff`
  compare against every schema the selection owns instead of only the
  connection's default one.
- `check` now also reports a schema the selection needs that the database
  lacks entirely, and exits non-zero for it.
- `check` and `diff` pin the connection's `search_path` to its default schema
  while reflecting. A DDL role whose `search_path` carries its own service
  schema (`pass_builder, public`) previously reported a permanent, spurious
  `remove_fk`/`add_fk`/`modify_type` for a schema that was in sync.
- `foreign_tables` now reports schema-qualified names and is scoped to the
  selected packages' schemas.
- New contract violation `unqualified_type` for an enum or domain that would
  be created outside its table's schema.
- `validate()` now also requires an explicit `Sequence` to declare its
  schema, and `create`/`check`/`diff` create the schema a qualified sequence
  needs. An unqualified sequence previously applied into `public` while its
  table sat elsewhere, after which `check` aborted with `UndefinedTable`
  rather than reporting a deviation.
- Documented a data-integrity hazard: a PostgreSQL `DOMAIN` is created
  without its `DEFAULT`, `NOT NULL` and `CHECK`, because SQLAlchemy 2.0's
  `DOMAIN.copy()` drops them and Alembic does not compare them. Pinned by
  tests so a future SQLAlchemy fix is noticed.
- New optional `SchemaDefinition.version_table_schema` field, required only
  when a package holds tables in more than one schema and sets
  `version_table`.
- Two tables join the contract schema: `public.photo`, one immutable row per
  uploaded version of a person's photograph, and `public.photo_review`, the
  append-only trail of every transition. `edutap.image_service` is their only
  writer; several packages read them, which is what puts them in `public`.
  "At most one active version per person" is held by a partial unique index
  rather than by the writing service, because two reviewers approving in the
  same second is what a worked queue produces.
