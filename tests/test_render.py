from tests.conftest import make_definition, make_definition_with_foreign_key

from edutap.db_definitions.render import render_create, render_create_split


def test_renders_create_table_if_not_exists():
    sql = render_create([make_definition("pkg.a", "table_a")])
    assert "CREATE TABLE IF NOT EXISTS table_a" in sql


def test_wraps_everything_in_one_transaction():
    sql = render_create([make_definition("pkg.a", "table_a")])
    assert sql.startswith("-- edutap-dbdef create")
    assert "BEGIN;" in sql
    assert sql.rstrip().endswith("COMMIT;")


def test_header_lists_the_packages():
    sql = render_create([make_definition("pkg.a", "table_a")])
    assert "-- packages: pkg.a" in sql


def test_no_timestamp_by_default_so_output_is_reproducible():
    first = render_create([make_definition("pkg.a", "table_a")])
    second = render_create([make_definition("pkg.a", "table_a")])
    assert first == second
    assert "generated:" not in first


def test_timestamp_is_included_when_given():
    sql = render_create([make_definition("pkg.a", "table_a")], timestamp="2026-07-30T12:00:00Z")
    assert "-- generated: 2026-07-30T12:00:00Z" in sql


def test_ddl_role_adds_a_set_role_header():
    sql = render_create([make_definition("pkg.a", "table_a")], ddl_role="edutap_ddl")
    assert "SET ROLE edutap_ddl;" in sql
    assert sql.index("SET ROLE") < sql.index("CREATE TABLE")


def test_without_ddl_role_there_is_no_set_role():
    assert "SET ROLE" not in render_create([make_definition("pkg.a", "table_a")])


def test_each_package_gets_a_section_comment():
    sql = render_create([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert "-- ===== pkg.a =====" in sql
    assert sql.index("-- ===== pkg.a =====") < sql.index("-- ===== pkg.b =====")


def test_tables_are_ordered_by_dependency():
    sql = render_create([make_definition_with_foreign_key("pkg.fk")])
    assert sql.index("CREATE TABLE IF NOT EXISTS parent") < sql.index(
        "CREATE TABLE IF NOT EXISTS child"
    )


def test_indexes_are_rendered_after_their_table():
    from sqlalchemy import Column, Index, Integer, MetaData, Table

    from edutap.db_definitions.definition import NAMING_CONVENTION, SchemaDefinition

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    table = Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("owner", Integer),
    )
    Index(None, table.c.owner)
    sql = render_create([SchemaDefinition(name="pkg.idx", metadata=metadata)])
    assert "CREATE INDEX IF NOT EXISTS ix_thing_owner" in sql
    assert sql.index("CREATE TABLE IF NOT EXISTS thing") < sql.index("CREATE INDEX")


def test_split_returns_one_document_per_package():
    documents = render_create_split(
        [make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")]
    )
    assert sorted(documents) == ["pkg.a", "pkg.b"]
    assert "table_a" in documents["pkg.a"]
    assert "table_b" not in documents["pkg.a"]
