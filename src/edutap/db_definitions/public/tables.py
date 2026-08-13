"""The tables of the contract schema `public`.

They live here rather than with a service because more than one service touches
them: the pass-state consumer writes `pass_state` and `pass_instance`, a person
spooler writes `person_view`, `edutap.image_service` writes `photo` and
`photo_review`, and `edutap.data_provider` reads across them. Declaring
them in the reader was the anomaly this package corrects -- ownership follows the
schema, and `public` belongs to nobody in particular.

Every other schema stays with its service. `edutap.pass_builder` announces its own
tables through an entry point, as before; only the contract schema moved.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from edutap.data_models.vocabulary import (
    HolderState,
    InstanceState,
    IssuanceState,
    WalletType,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from .base import Base


def _utcnow() -> datetime:
    """Timezone-aware now, for the Python-side default."""
    return datetime.now(tz=UTC)


def _timestamp(on_update: bool = False) -> sa.Column:
    """Build a timestamptz column whose value the database computes.

    `server_default` makes the *insert* independent of the writer: a row created by
    plain SQL gets its timestamp without anyone naming it.

    `on_update` is weaker than it looks, and the difference matters for the writers
    of these tables. It is SQLAlchemy's `onupdate`, so the `now()` call is rendered
    into the UPDATE that SQLAlchemy itself issues -- there is no trigger on the
    column. An `INSERT ... ON CONFLICT DO UPDATE` written by hand, which is how a
    spooler or a consumer upserts, leaves the old value in place unless it sets the
    column explicitly.
    """
    kwargs: dict[str, Any] = {"server_default": sa.func.now()}
    if on_update:
        kwargs["onupdate"] = sa.func.now()
    return sa.Column(sa.DateTime(timezone=True), nullable=False, **kwargs)


class PersonView(Base, table=True):
    """One view of one person: the payload a consumer of this view type may see."""

    __tablename__ = "person_view"
    __table_args__ = (
        sa.Index("ix_person_view_view_type", "view_type"),
        # The one lookup this table has that is not by primary key.
        #
        # A spooler writes a row from a source record it read somewhere -- for the LMU
        # spooler a directory DN -- and keeps that identifier inside `data`. When the
        # source record disappears, the identifier is all the spooler has left: the uid
        # was derived from attributes of the very record that is now gone, so the row
        # cannot be found by key. Without this index every such deletion is a
        # sequential scan of the whole table.
        #
        # Composite, and in this order, because the delete filters on both: the view
        # type is the selective prefix, and the extracted key follows. `source_dn` is
        # named as one possible convention rather than a column, which is why this is a
        # functional index on the payload -- another site's spooler may key on
        # something else and simply not use it.
        sa.Index(
            "ix_person_view_source_dn",
            "view_type",
            sa.text("(data ->> 'source_dn')"),
        ),
        # Declared, not inherited: `search_path` would otherwise decide, and it
        # resolves differently per deployment (see tests).
        {"schema": "public"},
    )

    person_uid: str = Field(
        sa_column=sa.Column(sa.String(64, collation="C"), primary_key=True),
        description=(
            "Person identifier, uniquely determinable by the university: ePPN, UUID or "
            "hash. Never interpreted here. Byte collation so comparison and index order "
            "do not depend on a locale."
        ),
    )
    view_type: str = Field(
        sa_column=sa.Column(sa.String(64, collation="C"), primary_key=True),
        description="`full_view` or a speaking slice such as `mensapass`.",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=sa.Column(JSONB, nullable=False),
        description="Flat payload, standard-native names, arrays for multi-valued attributes.",
    )
    photo: dict[str, Any] | None = Field(
        default=None,
        sa_column=sa.Column(JSONB, nullable=True),
        description=(
            "Photograph. JSONB rather than bytea so the source stays open: "
            "{'s3_key': ...} | {'url': ...} | {'base64': ...}. A consumer fetches "
            "the image itself instead of it riding along in every query."
        ),
    )
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp(on_update=True))


class PassState(Base, table=True):
    """One issued pass and where it stands in its life.

    Two axes, deliberately: `issuance_state` is what the issuer did or wants and
    exists even with no instance at all — a pass issued and never installed is
    ISSUED. `holder_state` is the summary of the instances. Only the pass-state
    consumer writes this table, together with `pass_instance` in one transaction,
    which is what keeps the stored `holder_state` from drifting.
    """

    __tablename__ = "pass_state"
    __table_args__ = (
        sa.Index("ix_pass_state_person_uid", "person_uid"),
        sa.Index(
            "ix_pass_state_person_template_wallet",
            "person_uid",
            "pass_template",
            "wallet_type",
        ),
        {"schema": "public"},
    )

    pass_id: str = Field(
        sa_column=sa.Column(sa.String(255), primary_key=True),
        description=(
            "The provider's pass identifier. Not a UUID column: usually a UUID, but "
            "Google Wallet object identifiers carry a prefix and suffix."
        ),
    )
    person_uid: str = Field(
        sa_column=sa.Column(sa.String(64, collation="C"), nullable=False),
        description="No foreign key: a pass exists whether or not a view row currently does.",
    )
    wallet_type: WalletType = Field(
        sa_column=sa.Column(sa.String(32), nullable=False),
        description=(
            "Text column, not a native enum — a new wallet provider must not force a migration."
        ),
    )
    issuance_state: IssuanceState = Field(
        sa_column=sa.Column(sa.String(32), nullable=False),
        description="What the issuer did or wants. Stored and delivered, never validated here.",
    )
    holder_state: HolderState = Field(
        sa_column=sa.Column(sa.String(32), nullable=False),
        description=(
            "Derived from pass_instance, never set by a caller: PRESENT when at least "
            "one instance is ACTIVE, SUSPENDED when instances exist but none is active "
            "and one is suspended, NOT_PRESENT otherwise."
        ),
    )
    version: int = Field(
        default=0,
        sa_column=sa.Column(sa.Integer, nullable=False, server_default="0"),
        description=(
            "Rises on every change of content. Compared against PassInstance.synced_version."
        ),
    )
    pass_template: str = Field(
        sa_column=sa.Column(sa.String(64), nullable=False),
        description="Speaking template key, matching Template.key in edutap.pass_builder.",
    )
    pass_template_variant: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(64), nullable=True),
        description="Variant key; empty means the default variant, modelled as is_default there.",
    )
    provider_raw: dict[str, Any] | None = Field(
        default=None,
        sa_column=sa.Column(JSONB, nullable=True),
        description="What the provider actually said, kept so a later dispute can be settled.",
    )
    last_event_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
        description=(
            "Watermark from the edutap-occurred-at header. The upsert writes only when "
            "this is younger than the stored value, so a late event hits zero rows."
        ),
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp())
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp(on_update=True))


class PassInstance(Base, table=True):
    """One exemplar of a pass at the holder — zero to n per pass.

    What an exemplar is, the platform decides: a device registration or a
    provisioned credential at Apple, the save into the account at Google. That is
    why pass_state cannot carry device-oriented semantics — at Google there would
    be nothing to put in it, at Apple several rows for one column.
    """

    __tablename__ = "pass_instance"
    __table_args__ = ({"schema": "public"},)

    pass_id: str = Field(
        sa_column=sa.Column(
            sa.String(255),
            sa.ForeignKey("public.pass_state.pass_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        description="The pass this exemplar belongs to. Deleting the pass deletes its instances.",
    )
    instance_ref: str = Field(
        sa_column=sa.Column(sa.String(255), primary_key=True),
        description=(
            "The identity under which the platform tracks this exemplar. Apple VAS: "
            "deviceLibraryIdentifier. Apple Access: the provisioned credential. "
            "Google: the fixed literal 'account' — there is exactly one exemplar per "
            "pass and no identifier is given, and a literal keeps the key usable and "
            "the upsert idempotent instead of faking one."
        ),
    )
    instance_state: InstanceState = Field(
        sa_column=sa.Column(sa.String(32), nullable=False),
        description="Text column, not a native enum — same reason as wallet_type.",
    )
    synced_version: int | None = Field(
        default=None,
        sa_column=sa.Column(sa.Integer, nullable=True),
        description=(
            "Which PassState.version this exemplar provably holds. 'Update complete' "
            "means min(synced_version) over the ACTIVE instances equals the pass version."
        ),
    )
    provider_raw: dict[str, Any] | None = Field(
        default=None,
        sa_column=sa.Column(JSONB, nullable=True),
        description="What the platform delivered, verbatim.",
    )
    last_event_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
        description="Watermark, same rule as on PassState.",
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp())
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp(on_update=True))


class Photo(Base, table=True):
    """One photograph of one person, as the system keeps it.

    A row is a version: the sanitised raw image plus its crop, immutable once
    written. A new upload is a new row; the history is kept because a deployment
    that has to evidence every photograph it accepted cannot hold only the current
    one.

    Written by `edutap.image_service` alone. It is read by more than one consumer --
    a data provider, a pass builder, at some deployments a vendor connector reading
    SQL directly -- which is what puts the table here rather than in a schema of its
    own.
    """

    __tablename__ = "photo"
    __table_args__ = (
        # The invariant "at most one active version per person", held by the
        # database rather than by the service. Two reviewers approving different
        # versions in the same second is what a worked queue produces, and an
        # application-level check loses that race by construction.
        sa.Index(
            "uq_photo_one_active_per_person",
            "person_uid",
            unique=True,
            postgresql_where=sa.text("state = 'active'"),
        ),
        # "At most one candidate per person", by the same argument. The writing
        # service clears a previous candidate before inserting the next, but a
        # person with two tabs open is a race, and an application-level check
        # loses it by construction.
        sa.Index(
            "uq_photo_one_draft_per_person",
            "person_uid",
            unique=True,
            postgresql_where=sa.text("state = 'draft'"),
        ),
        # The retention run's query: rejected rows past their deadline.
        sa.Index("ix_photo_state_notified_at", "state", "notified_at"),
        {"schema": "public"},
    )

    person_uid: str = Field(
        sa_column=sa.Column(sa.String(64, collation="C"), primary_key=True),
        description=(
            "Person identifier, uniquely determinable by the institution. Never "
            "interpreted here. Byte collation so comparison and index order do not "
            "depend on a locale."
        ),
    )
    version: str = Field(
        sa_column=sa.Column(sa.String(64, collation="C"), primary_key=True),
        description=(
            "Opaque, sortable upload generation (UUIDv7). Appears in the object path "
            "and in the published reference; interpreted by no one but the writer."
        ),
    )
    state: str = Field(
        sa_column=sa.Column(sa.String(32), nullable=False),
        description=(
            "`draft` | `pending` | `active` | `rejected` | `superseded`. Text, not a "
            "native enum: a new state must not force a migration -- which is what "
            "`draft` demonstrated. A `draft` is uploaded and not yet confirmed by "
            "its owner: no reviewer sees it and no trail entry mentions it."
        ),
    )
    sha256: str = Field(
        sa_column=sa.Column(sa.CHAR(64), nullable=False),
        description=(
            "Of the sanitised image, not of the uploaded file. The claim recorded is "
            "'this image was reviewed', not 'this file was uploaded' -- metadata is "
            "stripped on the way in, so the two differ."
        ),
    )
    evidence_kind: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(32), nullable=True),
        description=(
            "`support_visual` | `id_document` | `eudi_pid`. Null while `pending`: a "
            "version carries evidence once reviewed, and reaches `active` only then."
        ),
    )
    photo_assurance: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(128), nullable=True),
        description=(
            "Assurance of the photograph's provenance, derived from `evidence_kind`. "
            "A statement about the image, not about the person -- a credential "
            "combines it with the person's own value rather than replacing it."
        ),
    )
    recipe: str = Field(
        sa_column=sa.Column(sa.String(64), nullable=False),
        description="Which variant manifest rendered this version's derivatives.",
    )
    draft_details: dict[str, Any] | None = Field(
        default=None,
        sa_column=sa.Column(JSONB, nullable=True),
        description=(
            "The validation report and any rights claims found in the upload, held "
            "only while the version is a `draft`. Produced at upload and belonging "
            "in the review entry, which is written at confirmation -- and the two "
            "are different requests. Moved into that entry and cleared here, "
            "because one report in two places is how the two come apart."
        ),
    )
    rights_declared_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
        description=(
            "When the uploader declared they hold the rights to the image. That "
            "declaration is what carries legal weight; copyright metadata found in "
            "the upload is recorded in the review trail and never evaluated. Null "
            "while the version is a `draft`: the declaration is made when its owner "
            "confirms what they see, so a candidate they discard never carried one."
        ),
    )
    notified_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
        description=(
            "When the person was told of a rejection. The retention clock runs from "
            "here, not from the rejection: someone away for three weeks would "
            "otherwise lose the image before learning it was refused."
        ),
    )
    legal_hold_since: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
        description=(
            "Present means held, and every deletion path skips the row. A timestamp "
            "rather than a flag because it also says since when. Orthogonal to "
            "`state`: a suspicion can strike a version in any of them."
        ),
    )
    legal_hold_by: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(128), nullable=True),
        description="Who placed the hold. Releasing it is a narrower right than placing it.",
    )
    legal_hold_reason: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.Text, nullable=True),
        description="Why it was placed, in the words of whoever placed it.",
    )
    purged_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
        description=(
            "Bytes gone, row kept. Deleting the row instead would take the review "
            "trail with it through the cascade -- which is the evidence being kept."
        ),
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp())
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp(on_update=True))


class PhotoReview(Base, table=True):
    """One transition of one photograph, appended and never changed.

    Every step writes a row: the submission, the approval, the rejection, a
    reactivation, placing and releasing a legal hold, a purge, an expiry. A mistaken
    entry is corrected by a further entry rather than by rewriting the earlier one,
    which is what makes the sequence usable as evidence.

    It outlives the image but not the person: purging clears the bytes and keeps the
    row, while deleting the person removes the `photo` row and takes the trail with
    it through the cascade.
    """

    __tablename__ = "photo_review"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["person_uid", "version"],
            ["public.photo.person_uid", "public.photo.version"],
            ondelete="CASCADE",
        ),
        sa.Index("ix_photo_review_person_uid", "person_uid"),
        {"schema": "public"},
    )

    review_id: UUID = Field(
        default_factory=uuid4,
        sa_column=sa.Column(sa.Uuid(), primary_key=True),
        description="One row per transition, so the key cannot be the version.",
    )
    person_uid: str = Field(
        sa_column=sa.Column(sa.String(64, collation="C"), nullable=False),
        description="Together with `version`, the photograph this concerns.",
    )
    version: str = Field(
        sa_column=sa.Column(sa.String(64, collation="C"), nullable=False),
        description="The version this concerns.",
    )
    occurred_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp())
    actor: str = Field(
        sa_column=sa.Column(sa.String(128), nullable=False),
        description=(
            "Who acted. Stored as the calling service hands it over -- this package "
            "declares the column, it does not authenticate anyone."
        ),
    )
    action: str = Field(
        sa_column=sa.Column(sa.String(32), nullable=False),
        description=(
            "`submit` | `approve` | `reject` | `reactivate` | `hold_set` | "
            "`hold_release` | `purge` | `expire`. Text for the same reason as `state`."
        ),
    )
    evidence_kind: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(32), nullable=True),
        description="Set on `approve`, null on every other action.",
    )
    reason: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.Text, nullable=True),
        description="Free text, required by the writer on a rejection.",
    )
    sha256: str = Field(
        sa_column=sa.Column(sa.CHAR(64), nullable=False),
        description=(
            "Repeated from the photograph on purpose: after a purge the entry would "
            "otherwise say that a photo was refused without being able to say which."
        ),
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=sa.Column(JSONB, nullable=False),
        description=(
            "What the reviewer was shown: the validation report summary, and any "
            "copyright claim found in the upload's metadata. JSONB rather than "
            "columns, because what is shown will grow and nobody queries by it."
        ),
    )
