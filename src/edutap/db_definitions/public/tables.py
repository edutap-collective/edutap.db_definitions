"""The three tables of the contract schema `public`.

They live here rather than with a service because more than one service touches
them: the pass-state consumer writes `pass_state` and `pass_instance`, a person
spooler writes `person_view`, and `edutap.data_provider` reads all three. Declaring
them in the reader was the anomaly this package corrects -- ownership follows the
schema, and `public` belongs to nobody in particular.

Every other schema stays with its service. `edutap.pass_builder` announces its own
tables through an entry point, as before; only the contract schema moved.
"""

from datetime import UTC, datetime
from typing import Any

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
    """Build a timestamptz column maintained by the database."""
    kwargs: dict[str, Any] = {"server_default": sa.func.now()}
    if on_update:
        kwargs["onupdate"] = sa.func.now()
    return sa.Column(sa.DateTime(timezone=True), nullable=False, **kwargs)


class PersonView(Base, table=True):
    """One view of one person: the payload a consumer of this view type may see."""

    __tablename__ = "person_view"
    __table_args__ = (
        sa.Index("ix_person_view_view_type", "view_type"),
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
