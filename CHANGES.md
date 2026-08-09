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
- `foreign_tables` now reports schema-qualified names and is scoped to the
  selected packages' schemas.
- New contract violation `unqualified_type` for an enum or domain that would
  be created outside its table's schema.
- New optional `SchemaDefinition.version_table_schema` field, required only
  when a package holds tables in more than one schema and sets
  `version_table`.
