from dataclasses import replace

import pytest

from edutap.db_definitions.render import RenderError, render_create, render_create_split
from tests.conftest import (
    make_cross_package_definitions,
    make_definition,
    make_definition_with_deferred_foreign_key,
    make_definition_with_enum_and_sequence,
    make_definition_with_foreign_key,
)


def test_renders_create_table_if_not_exists():
    sql = render_create([make_definition("pkg.a", "table_a")])
    assert "CREATE TABLE IF NOT EXISTS public.table_a" in sql


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


def test_without_ddl_role_the_document_says_so():
    """Silence is indistinguishable from a forgotten flag.

    A reviewer reading a committed schema.sql cannot tell "deliberately no
    role" from "forgot --ddl-role", and the deployment's default-privilege
    grants silently do not apply to whatever the applying user then owns.
    """
    sql = render_create([make_definition("pkg.a", "table_a")])
    assert "-- NOTE: generated without --ddl-role;" in sql
    assert "owned by whichever user applies this file" in sql


def test_a_ddl_role_that_is_not_an_identifier_is_rejected():
    """The role name lands in a file destined for a superuser."""
    with pytest.raises(RenderError, match="not a valid PostgreSQL identifier"):
        render_create(
            [make_definition("pkg.a", "table_a")],
            ddl_role="edutap_ddl; DROP TABLE table_a",
        )


def test_a_mixed_case_ddl_role_keeps_its_case():
    """Unquoted identifiers fold to lower case, silently producing another owner."""
    sql = render_create([make_definition("pkg.a", "table_a")], ddl_role="Edutap_DDL")
    assert 'SET ROLE "Edutap_DDL";' in sql


def test_each_package_gets_a_section_comment():
    sql = render_create([make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")])
    assert "-- ===== pkg.a =====" in sql
    assert sql.index("-- ===== pkg.a =====") < sql.index("-- ===== pkg.b =====")


def test_tables_are_ordered_by_dependency():
    sql = render_create([make_definition_with_foreign_key("pkg.fk")])
    assert sql.index("CREATE TABLE IF NOT EXISTS public.parent") < sql.index(
        "CREATE TABLE IF NOT EXISTS public.child"
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


def test_a_native_enum_type_is_created_before_the_table_that_uses_it():
    """A table is not the only schema object a package needs created.

    `edutap.pass_builder` uses native PostgreSQL enum types. Rendering only
    ``CREATE TABLE`` leaves ``provider provider NOT NULL`` referring to a type
    that does not exist, and the file fails in the privileged apply step.
    """
    sql = render_create([make_definition_with_enum_and_sequence("pkg.enum")])
    assert "CREATE TYPE provider AS ENUM ('apple', 'google');" in sql
    assert sql.index("CREATE TYPE provider") < sql.index(
        "CREATE TABLE IF NOT EXISTS public.provider_thing"
    )


def test_a_type_creation_is_wrapped_so_the_document_stays_repeatable():
    """PostgreSQL has no ``CREATE TYPE IF NOT EXISTS``; a DO block replaces it."""
    sql = render_create([make_definition_with_enum_and_sequence("pkg.enum")])
    assert "DO $$ BEGIN" in sql
    assert "EXCEPTION WHEN duplicate_object THEN NULL;" in sql


def test_a_type_is_created_once_and_before_every_package_section():
    """SQLAlchemy scopes type creation to the metadata, not to a table subset.

    Rendering package by package from the merged metadata therefore reports every
    type for every package. Repeating them would tell a reviewer that one package
    creates another package's type, so they go into one section of their own.
    """
    sql = render_create(
        [
            make_definition_with_enum_and_sequence("pkg.enum"),
            make_definition("pkg.plain", "table_plain"),
        ]
    )
    assert sql.count("CREATE TYPE provider") == 1
    assert sql.index("-- ===== types =====") < sql.index("-- ===== pkg.enum =====")
    assert sql.index("CREATE TYPE provider") < sql.index("CREATE TABLE IF NOT EXISTS")


def test_an_explicit_sequence_is_created_and_the_column_keeps_its_default():
    sql = render_create([make_definition_with_enum_and_sequence("pkg.enum")])
    assert "CREATE SEQUENCE IF NOT EXISTS provider_thing_id_seq;" in sql
    assert "nextval('provider_thing_id_seq'" in sql


def test_a_deferred_foreign_key_is_rendered_as_an_alter_table():
    """``use_alter=True`` moves the constraint out of the CREATE TABLE body.

    Rendering only ``CreateTable`` dropped it silently, leaving a table with no
    foreign key at all.
    """
    sql = render_create([make_definition_with_deferred_foreign_key("pkg.alter")])
    assert "ADD CONSTRAINT fk_second_first_id_first FOREIGN KEY(first_id)" in sql


def test_a_foreign_key_across_a_package_boundary_renders():
    """`requires` promises cross-package foreign keys; rendering must deliver them.

    Rendering each package's own MetaData in isolation raised
    ``NoReferencedTableError`` no matter the order, because the obstacle is
    MetaData resolution and not ordering. The merged metadata resolves it, and
    its global topological order puts the referenced table first.
    """
    provider, consumer = make_cross_package_definitions()
    sql = render_create([provider, consumer])
    assert "REFERENCES public.view_source (id)" in sql
    assert sql.index("CREATE TABLE IF NOT EXISTS public.view_source") < sql.index(
        "CREATE TABLE IF NOT EXISTS public.view_state"
    )


def test_cross_package_rendering_keeps_the_section_comments():
    provider, consumer = make_cross_package_definitions()
    sql = render_create([provider, consumer])
    assert sql.index("-- ===== pkg.provider =====") < sql.index("-- ===== pkg.consumer =====")
    provider_section = sql[
        sql.index("-- ===== pkg.provider =====") : sql.index("-- ===== pkg.consumer =====")
    ]
    assert "view_source" in provider_section
    assert "view_state" not in provider_section


def test_split_renders_a_cross_package_foreign_key_per_package():
    provider, consumer = make_cross_package_definitions()
    documents = render_create_split([provider, consumer])
    assert "CREATE TABLE IF NOT EXISTS public.view_source" in documents["pkg.provider"]
    assert "REFERENCES public.view_source (id)" in documents["pkg.consumer"]
    assert "CREATE TABLE IF NOT EXISTS public.view_source" not in documents["pkg.consumer"]


def test_an_empty_selection_is_refused():
    """A valid-looking document that creates nothing is the worst outcome.

    It applies cleanly, so nothing complains — and the deployment ends up with
    no tables because `--packages` had a typo or no extra was installed.
    """
    with pytest.raises(RenderError, match="No packages selected"):
        render_create([])


def test_an_empty_selection_is_refused_for_split():
    with pytest.raises(RenderError, match="No packages selected"):
        render_create_split([])


def test_split_returns_one_document_per_package():
    documents = render_create_split(
        [make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")]
    )
    assert sorted(documents) == ["pkg.a", "pkg.b"]
    assert "table_a" in documents["pkg.a"]
    assert "table_b" not in documents["pkg.a"]


def test_the_document_creates_the_schemas_it_needs():
    definition = make_definition("pkg.a", "thing", schema="pass_builder")

    document = render_create([definition])

    assert "CREATE SCHEMA IF NOT EXISTS pass_builder;" in document


def test_public_is_never_created():
    definition = make_definition("pkg.a", "thing", schema="public")

    document = render_create([definition])

    assert "CREATE SCHEMA" not in document


def test_a_schema_is_created_before_the_first_table_that_needs_it():
    definition = make_definition("pkg.a", "thing", schema="pass_builder")

    document = render_create([definition])

    assert document.index("CREATE SCHEMA IF NOT EXISTS pass_builder;") < document.index(
        "CREATE TABLE IF NOT EXISTS pass_builder.thing"
    )


def test_a_schema_is_created_once_even_when_two_packages_share_it():
    a = make_definition("pkg.a", "one", schema="shared")
    b = make_definition("pkg.b", "two", schema="shared")

    document = render_create([a, b])

    assert document.count("CREATE SCHEMA IF NOT EXISTS shared;") == 1


def test_each_split_file_creates_its_own_schemas():
    a = make_definition("pkg.a", "one", schema="alpha")
    b = make_definition("pkg.b", "two", schema="beta")

    documents = render_create_split([a, b])

    assert "CREATE SCHEMA IF NOT EXISTS alpha;" in documents["pkg.a"]
    assert "CREATE SCHEMA IF NOT EXISTS beta;" not in documents["pkg.a"]
    assert "CREATE SCHEMA IF NOT EXISTS beta;" in documents["pkg.b"]


def test_a_schema_that_only_holds_the_version_table_is_created_too():
    """`version_table_schema` may name a schema no data table lives in.

    Without this, Alembic's first `CREATE TABLE alembic_version_…` fails on a
    schema nobody created — and it fails at the deployment's next migration,
    not while generating the document, so nothing points back to here.
    """
    definition = replace(
        make_definition("pkg.a", "thing", schema="pass_builder"),
        version_table_schema="history",
    )

    document = render_create([definition])

    assert "CREATE SCHEMA IF NOT EXISTS history;" in document
