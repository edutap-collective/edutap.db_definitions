import pytest
from sqlalchemy import Boolean, Column, Enum, ForeignKey, Integer, MetaData, Table
from sqlalchemy.dialects.postgresql import ARRAY

from edutap.db_definitions.contract import ContractError, check_contract, raise_on_violations
from edutap.db_definitions.definition import NAMING_CONVENTION, SchemaDefinition
from tests.conftest import (
    make_cross_package_definitions,
    make_definition,
    make_definition_with_domain,
)


def test_a_consistent_set_has_no_violations():
    definitions = [make_definition("pkg.a", "table_a"), make_definition("pkg.b", "table_b")]
    assert check_contract(definitions) == []


def test_table_name_collision_is_reported():
    definitions = [make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")]
    violations = check_contract(definitions)
    assert [v.kind for v in violations] == ["table_collision"]
    assert "shared" in violations[0].message


def test_duplicate_version_table_is_reported():
    definitions = [
        make_definition("pkg.a", "table_a", version_table="alembic_version"),
        make_definition("pkg.b", "table_b", version_table="alembic_version"),
    ]
    assert [v.kind for v in check_contract(definitions)] == ["version_table_collision"]


def test_diverging_naming_convention_is_reported():
    definitions = [
        make_definition("pkg.a", "table_a"),
        make_definition("pkg.b", "table_b", convention={"pk": "primary_%(table_name)s"}),
    ]
    assert [v.kind for v in check_contract(definitions)] == ["naming_convention"]


def test_a_declared_cross_package_foreign_key_is_no_violation():
    provider, consumer = make_cross_package_definitions()
    assert check_contract([provider, consumer]) == []


def test_a_cross_package_foreign_key_without_requires_is_reported():
    """The declaration is what orders the packages; a missing one is a real bug.

    Without `requires`, nothing keeps the referenced table from being created
    after the table referencing it — the file would then fail on an empty
    database, or worse, quietly depend on alphabetical luck.
    """
    provider, consumer = make_cross_package_definitions(declare_requires=False)
    violations = check_contract([provider, consumer])
    assert [v.kind for v in violations] == ["undeclared_dependency"]
    message = violations[0].message
    assert "pkg.consumer" in message
    assert "pkg.provider" in message
    assert "requires" in message


def test_raise_on_violations_is_quiet_when_there_are_none():
    raise_on_violations([])


def test_raise_on_violations_raises_with_all_messages():
    definitions = [make_definition("pkg.a", "shared"), make_definition("pkg.b", "shared")]
    with pytest.raises(ContractError, match="shared"):
        raise_on_violations(check_contract(definitions))


def test_the_same_table_in_the_same_schema_collides():
    a = make_definition("pkg.a", "pass_state", schema="public")
    b = make_definition("pkg.b", "pass_state", schema="public")

    kinds = [violation.kind for violation in check_contract([a, b])]

    assert "table_collision" in kinds


def test_the_same_table_name_in_different_schemas_does_not_collide():
    a = make_definition("pkg.a", "state", schema="alpha")
    b = make_definition("pkg.b", "state", schema="beta")

    assert [v for v in check_contract([a, b]) if v.kind == "table_collision"] == []


def test_the_same_version_table_in_different_schemas_does_not_collide():
    a = make_definition("pkg.a", "one", schema="alpha", version_table="alembic_version")
    b = make_definition("pkg.b", "two", schema="beta", version_table="alembic_version")

    assert [v for v in check_contract([a, b]) if v.kind == "version_table_collision"] == []


def test_the_same_version_table_in_the_same_schema_collides():
    a = make_definition("pkg.a", "one", schema="public", version_table="alembic_version")
    b = make_definition("pkg.b", "two", schema="public", version_table="alembic_version")

    kinds = [violation.kind for violation in check_contract([a, b])]

    assert "version_table_collision" in kinds


def test_a_type_without_a_schema_is_a_violation():
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("flavour", Enum("one", "two", name="flavour"), nullable=False),
        schema="pass_builder",
    )
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)

    violations = [v for v in check_contract([definition]) if v.kind == "unqualified_type"]

    assert len(violations) == 1
    assert "flavour" in violations[0].message
    assert "inherit_schema=True" in violations[0].message


def test_a_type_that_inherits_its_table_schema_is_accepted():
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("flavour", Enum("one", "two", name="flavour", inherit_schema=True), nullable=False),
        schema="pass_builder",
    )
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)

    assert [v for v in check_contract([definition]) if v.kind == "unqualified_type"] == []


def test_a_boolean_column_is_not_mistaken_for_an_unqualified_type():
    """`Boolean` is a SchemaType too, and it creates no type on PostgreSQL.

    Measured, not assumed: `isinstance(Column("b", Boolean).type, SchemaType)`
    is True, but `Boolean().schema` raises `AttributeError` — the attribute
    does not exist at all, it does not default to `None`. A check that reads
    it via `getattr(type_, "schema", None)` would therefore silently treat
    every boolean column as one more unqualified type — the most common
    column type there is.
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("active", Boolean, nullable=False),
        schema="pass_builder",
    )
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)

    assert [v for v in check_contract([definition]) if v.kind == "unqualified_type"] == []


def test_an_enum_hidden_inside_an_array_without_a_schema_is_a_violation():
    """`ARRAY(ENUM(...))` renders the same unqualified `CREATE TYPE` a bare enum does.

    Measured: `column.type` is the `ARRAY`, not the `Enum` — the enum sits one
    level down, in `.item_type`. A check that inspects `column.type` alone
    stays silent while `create_all` still emits `CREATE TYPE flagtype AS
    ENUM (...)` outside the table's schema.
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("flags", ARRAY(Enum("a", "b", name="flagtype")), nullable=False),
        schema="pass_builder",
    )
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)

    violations = [v for v in check_contract([definition]) if v.kind == "unqualified_type"]

    assert len(violations) == 1
    assert "flagtype" in violations[0].message


def test_an_enum_hidden_inside_an_array_that_inherits_its_table_schema_is_accepted():
    """Measured: with `inherit_schema=True`, the array's enum renders `CREATE TYPE pb.flagtype`."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "flags",
            ARRAY(Enum("a", "b", name="flagtype", inherit_schema=True)),
            nullable=False,
        ),
        schema="pass_builder",
    )
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)

    assert [v for v in check_contract([definition]) if v.kind == "unqualified_type"] == []


def test_a_non_native_enum_creates_no_type_and_is_not_a_violation():
    """`native_enum=False` renders as a plain `VARCHAR` column.

    Measured: `create_all` emits no `CREATE TYPE` statement at all for such a
    column, so flagging it would abort `create`/`diff`/`check` for a package
    that has done nothing wrong.
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("flavour", Enum("one", "two", name="flavour", native_enum=False), nullable=False),
        schema="pass_builder",
    )
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)

    assert [v for v in check_contract([definition]) if v.kind == "unqualified_type"] == []


def test_a_type_name_shared_by_two_columns_reports_every_site():
    """Measured: two same-named enums still produce only one `CREATE TYPE` between them.

    The second column silently takes on the first one's value list, so fixing
    only the first reported site would leave the second one broken and
    unmentioned.
    """
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "thing_one",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("flavour", Enum("one", "two", name="dup"), nullable=False),
        schema="pass_builder",
    )
    Table(
        "thing_two",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("flavour", Enum("one", "two", name="dup"), nullable=False),
        schema="pass_builder",
    )
    definition = SchemaDefinition(name="pkg.a", metadata=metadata)

    violations = [v for v in check_contract([definition]) if v.kind == "unqualified_type"]

    assert len(violations) == 1
    assert "thing_one.flavour" in violations[0].message
    assert "thing_two.flavour" in violations[0].message


def test_a_foreign_key_across_schemas_still_needs_a_declared_dependency():
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table(
        "certificate",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("pass_id", Integer, ForeignKey("public.pass_state.id")),
        schema="pass_builder",
    )
    borrower = SchemaDefinition(name="pkg.builder", metadata=metadata)
    owner = make_definition("pkg.provider", "pass_state", schema="public")

    kinds = [violation.kind for violation in check_contract([borrower, owner])]

    assert "undeclared_dependency" in kinds


def test_an_unqualified_domain_is_reported():
    """`DOMAIN` always creates a type; it has no `native_enum=False` escape."""
    violations = check_contract([make_definition_with_domain("pkg.d", schema="alpha")])

    kinds = [v.kind for v in violations]
    assert "unqualified_type" in kinds
    assert any(
        "positive_int" in v.message and "alpha.thing.amount" in v.message for v in violations
    )


def test_a_qualified_domain_is_accepted():
    definition = make_definition_with_domain("pkg.d", schema="alpha", type_schema="typelib")

    assert [v for v in check_contract([definition]) if v.kind == "unqualified_type"] == []
