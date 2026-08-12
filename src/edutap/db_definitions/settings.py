"""Connection settings for the commands -- and the services -- that talk to a database.

**Why connection settings live in this package at all.** The estate's database is not
one server but a cluster: several nodes replicate, exactly one accepts writes, and
which one that is changes on failover. Getting that right is three separate pieces of
knowledge -- name every node, ask for the writable one, speak TLS -- and none of them
belong to any single service. Left to the services, each rebuilt them, and the ones
that had not yet rebuilt them simply could not reach the database.

Which is what happened: this package's own :class:`Settings` knew a single ``host``
until now, and the deployment worked around it by handing the migration container a
fully assembled DSN from an Ansible task. The cluster knowledge sat in a playbook,
where no other consumer could reach it.

:class:`ClusterSettings` is that knowledge, in one place, for both drivers. The
services subclass it with their own prefix; the DDL tool subclasses it with the
``PG*`` names it always accepted.
"""

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

#: Synchronous driver -- the DDL tool and everything else built on psycopg.
SYNC_DRIVER = "postgresql+psycopg"

#: Asynchronous driver -- the services built on SQLAlchemy's asyncio extension.
ASYNC_DRIVER = "postgresql+asyncpg"


def split_hosts(value: str) -> list[tuple[str, str]]:
    """Split a comma separated host list into (host, port) pairs, port possibly empty.

    Whitespace around the commas is dropped -- such a list acquires spaces sooner or
    later, and a host named ``" pg-a"`` resolves to nothing.

    The port is split off at the last colon, so a bare IPv6 address would be misread.
    That is acceptable here and nowhere near a silent failure: these are DNS names on a
    container overlay, and an address that needed brackets would fail on the first
    connection attempt, loudly.
    """
    pairs = []
    for entry in (part.strip() for part in value.split(",")):
        if not entry:
            continue
        host, _, port = entry.rpartition(":")
        pairs.append((host, port) if host else (port, ""))
    return pairs


class ClusterSettings(BaseSettings):
    """A connection to a Postgres cluster, renderable for either driver.

    Three things this carries that a single ``host`` cannot:

    * **Every node.** :attr:`hosts` is a list, and all of it reaches the driver as
      fallback hosts. Naming one node is what breaks at the next failover.
    * **Which node.** :attr:`target_session_attrs` defaults to ``read-write``, which
      is how libpq and asyncpg alike are told to find the primary. Without it a
      connection lands on a replica, *succeeds*, and only the first write fails -- so
      the mistake surfaces far from its cause.
    * **TLS, spelled per driver.** libpq wants ``sslmode``; asyncpg calls the same
      thing ``ssl`` and takes the same mode names. :meth:`url` translates.

    The multihost form was verified against the pinned versions rather than assumed
    (SQLAlchemy 2.0.51, asyncpg 0.31.0): both PostgreSQL dialects read repeated
    ``host=name:port`` query parameters, asyncpg since SQLAlchemy 2.0.18, and every
    entry needs an explicit port or the dialect raises ``ArgumentError``. What the
    dialects do not consume themselves they pass on to the driver
    (``create_connect_args`` does ``opts.update(url.query)``), which is how
    ``target_session_attrs`` arrives.

    No ``dsn`` field here on purpose. A full DSN carries the password in one string,
    and a service that reads its password from a mounted secret file keeps it out of
    its own environment -- and therefore out of ``docker inspect`` and out of the frame
    locals an error tracker collects. Subclasses that want the escape hatch add it
    themselves; :class:`Settings` below does.
    """

    model_config = SettingsConfigDict(extra="ignore")

    #: Every node of the cluster, comma separated, each optionally with its own port:
    #: ``pg-a,pg-b:5433,pg-c``. Entries without a port get :attr:`port`.
    hosts: str = "postgres"

    #: Default port for entries in :attr:`hosts` that do not carry one.
    port: int = 5432

    database: str = "edutap"
    user: str = "edutap"
    password: SecretStr = SecretStr("")

    #: libpq SSL mode (``require``, ``verify-full``, ...). ``None`` leaves the decision
    #: to the driver, which in turn falls back to ``PGSSLMODE``.
    sslmode: str | None = None

    #: Path to the CA bundle that verifies the server.
    #:
    #: **Reaches the synchronous driver only.** asyncpg accepts a root certificate from
    #: the environment variable ``PGSSLROOTCERT`` and nowhere else -- never as a connect
    #: keyword -- so :meth:`url` leaves it out of an async URL rather than passing
    #: something the driver would reject. A deployment that reads this under its own
    #: prefix must therefore also export ``PGSSLROOTCERT`` for the async services.
    sslrootcert: str | None = None

    #: Which node to accept. ``read-write`` means the primary. ``None`` drops the
    #: requirement and takes whatever answers first -- right for a reader, wrong for
    #: anything that writes.
    target_session_attrs: str | None = "read-write"

    @field_validator("hosts")
    @classmethod
    def _must_name_at_least_one_host(cls, value: str) -> str:
        """Refuse a list that names nothing; an empty URL fails far from its cause."""
        if not split_hosts(value):
            raise ValueError("at least one host is required")
        return value

    @property
    def targets(self) -> list[tuple[str, int]]:
        """Return the (host, port) pairs the driver should try, in order."""
        return [
            (host, int(explicit_port) if explicit_port else self.port)
            for host, explicit_port in split_hosts(self.hosts)
        ]

    def url(self, driver: str = SYNC_DRIVER) -> str:
        """Return the SQLAlchemy URL for `driver`.

        One host keeps the plain ``@host:port/database`` form -- nothing is gained by
        making the common case exotic. Several hosts move into the query string, which
        is the only form that can express more than one.
        """
        targets = self.targets
        query: dict[str, str | tuple[str, ...]] = {}

        host: str | None = None
        port: int | None = None
        if len(targets) == 1:
            host, port = targets[0]
        else:
            query["host"] = tuple(f"{name}:{number}" for name, number in targets)

        if self.target_session_attrs:
            query["target_session_attrs"] = self.target_session_attrs

        if self.sslmode:
            # The same mode names, two spellings: asyncpg's keyword is `ssl`, and it
            # has no `sslmode` at all -- passing one would reach `asyncpg.connect` as
            # an unexpected keyword.
            query["ssl" if driver == ASYNC_DRIVER else "sslmode"] = self.sslmode

        if self.sslrootcert and driver != ASYNC_DRIVER:
            query["sslrootcert"] = self.sslrootcert

        return URL.create(
            driver,
            username=self.user,
            password=self.password.get_secret_value() or None,
            host=host,
            port=port,
            database=self.database,
            query=query,
        ).render_as_string(hide_password=False)


class Settings(ClusterSettings):
    """The DDL tool's own connection: `EDUTAP_DBDEF_*` or the standard `PG*` variables.

    The prefixed names take precedence, so a deployment that already exports ``PG*``
    for other tools can still override a single value for this one.
    """

    model_config = SettingsConfigDict(extra="ignore")

    #: A complete DSN, overriding every field below it. The escape hatch for a caller
    #: that assembles its own -- which is how this tool reached the cluster before
    #: :class:`ClusterSettings` could express it.
    dsn: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EDUTAP_DBDEF_DSN", "DATABASE_URL"),
    )
    #: ``EDUTAP_DBDEF_HOST``, singular, is still accepted. Every field in this class has
    #: a default, so dropping the old name would not fail loudly -- it would quietly
    #: fall back to ``postgres`` and connect to nothing. ``PGHOST`` takes a list in
    #: libpq too, so the name is honest either way.
    hosts: str = Field(
        default="postgres",
        validation_alias=AliasChoices("EDUTAP_DBDEF_HOSTS", "EDUTAP_DBDEF_HOST", "PGHOST"),
    )
    port: int = Field(
        default=5432,
        validation_alias=AliasChoices("EDUTAP_DBDEF_PORT", "PGPORT"),
    )
    database: str = Field(
        default="edutap",
        validation_alias=AliasChoices("EDUTAP_DBDEF_DATABASE", "PGDATABASE"),
    )
    user: str = Field(
        default="edutap_ddl", validation_alias=AliasChoices("EDUTAP_DBDEF_USER", "PGUSER")
    )
    password: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("EDUTAP_DBDEF_PASSWORD", "PGPASSWORD"),
    )
    sslmode: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EDUTAP_DBDEF_SSLMODE", "PGSSLMODE"),
    )
    sslrootcert: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EDUTAP_DBDEF_SSLROOTCERT", "PGSSLROOTCERT"),
    )
    target_session_attrs: str | None = Field(
        default="read-write",
        validation_alias=AliasChoices("EDUTAP_DBDEF_TARGET_SESSION_ATTRS", "PGTARGETSESSIONATTRS"),
    )

    def url(self, driver: str = SYNC_DRIVER) -> str:
        """Return :attr:`dsn` if one was given, otherwise the assembled URL."""
        if self.dsn:
            return self.dsn
        return super().url(driver)
