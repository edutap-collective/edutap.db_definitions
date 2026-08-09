# Changelog

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
