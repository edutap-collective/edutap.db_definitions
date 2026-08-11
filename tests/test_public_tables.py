"""The design of the contract tables, column by column.

Moved here with the declarations from `edutap.data_provider`, where they were
`tests/test_models.py`. They assert what the tables *are* -- the composite key, the
byte collation, the watermark one table has and the other does not, the foreign key
that is deliberately absent -- so they belong with the declarations rather than with
one of their readers.

What is not here is `test_public.py`'s subject: that the definition exists, is found
without an entry point, and satisfies the contract. Two tests from the original file
went away with the move -- they asserted the `SchemaDefinition` this package used to
announce and the entry point that announced it, and both are covered from the other
side now.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateIndex

from edutap.db_definitions.definition import NAMING_CONVENTION
from edutap.db_definitions.public import metadata
from edutap.db_definitions.public.tables import PassState, PersonView


def test_tables_live_on_the_package_metadata_only():
    from sqlmodel import SQLModel

    assert set(metadata.tables) == {
        "public.person_view",
        "public.pass_state",
        "public.pass_instance",
    }
    assert "public.person_view" not in SQLModel.metadata.tables


def test_contract_tables_declare_the_public_schema_explicitly():
    """Without the declaration the target schema depends on `search_path`.

    Measured 2026-08-09: with role `edutap` and a schema of the same name the
    tables landed in `edutap` locally, while production resolved to `public` —
    two deployments of one package with different layouts. Declaring the schema
    removes the ambiguity.
    """
    for name in ("person_view", "pass_state", "pass_instance"):
        assert metadata.tables[f"public.{name}"].schema == "public"


def test_person_view_carries_a_photo_reference():
    """JSONB, not bytea: the source stays open — `s3_key`, `url` or `base64`.

    A consumer then fetches the image itself instead of it being carried through
    every query.
    """
    column = metadata.tables["public.person_view"].columns["photo"]
    assert isinstance(column.type, JSONB)
    assert column.nullable


def test_naming_convention_is_the_canonical_one():
    assert dict(metadata.naming_convention) == NAMING_CONVENTION


def test_person_view_has_a_composite_primary_key():
    table = metadata.tables["public.person_view"]
    assert [column.name for column in table.primary_key.columns] == ["person_uid", "view_type"]


def test_person_view_keys_use_byte_collation():
    table = metadata.tables["public.person_view"]
    for name in ("person_uid", "view_type"):
        assert table.columns[name].type.collation == "C"


def test_person_view_indexes_view_type_for_whole_view_reads():
    table = metadata.tables["public.person_view"]
    indexed = {tuple(column.name for column in index.columns) for index in table.indexes}
    assert ("view_type",) in indexed


def test_person_view_indexes_the_source_identifier_a_spooler_deletes_by():
    """The one lookup on this table that is not by primary key.

    A spooler that loses its source record cannot find the row by key: the uid was
    derived from attributes of the record that just disappeared. It searches by the
    identifier it kept in `data` instead, and without an index that is a sequential
    scan of the whole table on every deletion.

    Rendered rather than inspected, because a functional index over a JSONB key is not
    a column and `index.columns` does not show it -- asserting on the metadata alone
    would pass while the SQL says something else.
    """
    table = metadata.tables["public.person_view"]
    by_name = {index.name: index for index in table.indexes}
    assert "ix_person_view_source_dn" in by_name

    statement = CreateIndex(by_name["ix_person_view_source_dn"])
    rendered = str(statement.compile(dialect=postgresql.dialect()))

    assert "view_type" in rendered
    assert "data ->> 'source_dn'" in rendered


def test_person_view_deliberately_has_no_watermark():
    """Unlike `pass_state`, `person_view` guards nothing against a late write.

    The Kafka event here is only a trigger to re-read LDAP; the row's freshness
    depends on when that read happened, not on when the event arrived, so a
    watermark on the event time would reject exactly the write carrying newer
    data. See "Why `pass_state` has a watermark and `person_view` does not" in
    docs/explanation.md. Nailed down here so a later "unify the two tables"
    change finds resistance instead of silence.
    """
    assert "last_event_at" not in metadata.tables["public.person_view"].columns


def test_pass_state_identifier_is_a_string_not_a_uuid():
    """Usually a UUID, but Google object identifiers carry a prefix and suffix."""
    column = metadata.tables["public.pass_state"].columns["pass_id"]
    assert isinstance(column.type, sa.String)
    assert column.type.length == 255
    assert column.primary_key


def test_pass_state_separates_issuance_from_holder():
    table = metadata.tables["public.pass_state"]
    assert "issuance_state" in table.columns
    assert "holder_state" in table.columns
    assert "state" not in table.columns


def test_pass_state_counts_a_version():
    """The counter the instances are compared against via synced_version."""
    column = metadata.tables["public.pass_state"].columns["version"]
    assert isinstance(column.type, sa.Integer)
    assert not column.nullable


def test_pass_state_carries_the_watermark():
    """The event carries the state here, so the event time is the right measure.

    The upsert writes only when edutap-occurred-at is younger than last_event_at;
    a late event then hits zero rows instead of overwriting a newer state.
    """
    column = metadata.tables["public.pass_state"].columns["last_event_at"]
    assert isinstance(column.type, sa.DateTime)
    assert column.type.timezone
    assert not column.nullable


def test_pass_state_keeps_the_provider_native_value():
    """If Google later claims something else, this is the only way to settle it."""
    column = metadata.tables["public.pass_state"].columns["provider_raw"]
    assert isinstance(column.type, JSONB)
    assert column.nullable


def test_pass_state_does_not_reference_the_person_view():
    """No foreign key: a pass exists whether or not a view row currently does."""
    table = metadata.tables["public.pass_state"]
    assert table.foreign_keys == set()


def test_pass_state_indexes_the_question_readers_ask():
    table = metadata.tables["public.pass_state"]
    indexed = {tuple(column.name for column in index.columns) for index in table.indexes}
    assert ("person_uid", "pass_template", "wallet_type") in indexed


def test_vocabulary_columns_are_text_not_native_enums():
    pass_state = metadata.tables["public.pass_state"]
    for name in ("wallet_type", "issuance_state", "holder_state"):
        assert isinstance(pass_state.columns[name].type, sa.String)
        assert not isinstance(pass_state.columns[name].type, sa.Enum)

    instance_state = metadata.tables["public.pass_instance"].columns["instance_state"]
    assert isinstance(instance_state.type, sa.String)
    assert not isinstance(instance_state.type, sa.Enum)


def test_variant_is_optional_because_a_default_exists():
    assert metadata.tables["public.pass_state"].columns["pass_template_variant"].nullable


def test_pass_instance_is_keyed_by_pass_and_platform_reference():
    """instance_ref is what the platform calls this exemplar.

    Apple VAS: the deviceLibraryIdentifier — the device IS the exemplar. Apple
    Access: the provisioned credential. Google: the fixed literal 'account',
    because there is exactly one exemplar per pass and no identifier is given.
    """
    table = metadata.tables["public.pass_instance"]
    assert [column.name for column in table.primary_key.columns] == ["pass_id", "instance_ref"]


def test_pass_instance_cascades_from_the_pass():
    table = metadata.tables["public.pass_instance"]
    foreign_key = next(iter(table.columns["pass_id"].foreign_keys))
    assert foreign_key.column.table.fullname == "public.pass_state"
    assert foreign_key.ondelete == "CASCADE"


def test_pass_instance_records_which_version_it_provably_holds():
    """Nullable: an instance can exist before anything is known about its version."""
    column = metadata.tables["public.pass_instance"].columns["synced_version"]
    assert isinstance(column.type, sa.Integer)
    assert column.nullable


def test_pass_instance_has_no_separate_device_column():
    """instance_ref already carries the platform's identity for the exemplar.

    A second device column would hold the same string at Apple VAS and none at
    Google — two columns for one statement. Device detail, if ever needed, is in
    provider_raw as the platform delivered it.
    """
    assert "device_ref" not in metadata.tables["public.pass_instance"].columns


def test_models_are_usable_as_python_objects():
    view = PersonView(person_uid="x@lmu.de", view_type="full_view", data={"surname": "Doe"})
    assert view.data["surname"] == "Doe"
    state = PassState(
        pass_id="3388000000022195611.abc",
        person_uid="x@lmu.de",
        wallet_type="GOOGLE_ST",
        issuance_state="ISSUED",
        holder_state="NOT_PRESENT",
        pass_template="mensapass",
        last_event_at=datetime.now(UTC),
    )
    assert state.pass_template_variant is None


def test_the_python_side_default_is_timezone_aware():
    """`tz=UTC`, not a naive local time.

    Mutation testing found `datetime.now(tz=UTC)` could become `datetime.now(tz=None)`
    unnoticed. Both columns are `timestamptz`, so a naive value would be interpreted
    against the server's time zone -- an `updated_at` silently wrong by the offset of
    whichever machine happened to write the row, and wrong differently per machine.
    """
    from edutap.db_definitions.public.tables import _utcnow

    now = _utcnow()

    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(None)
