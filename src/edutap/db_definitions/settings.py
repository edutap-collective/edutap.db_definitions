"""Connection settings for the commands that talk to a database."""

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Reads `EDUTAP_DBDEF_*` or the standard `PG*` variables.

    The prefixed names take precedence, so a deployment that already exports `PG*`
    for other tools can still override a single value for this one.
    """

    model_config = SettingsConfigDict(extra="ignore")

    dsn: str | None = Field(
        default=None,
        validation_alias=AliasChoices("EDUTAP_DBDEF_DSN", "DATABASE_URL"),
    )
    host: str = Field(
        default="postgres",
        validation_alias=AliasChoices("EDUTAP_DBDEF_HOST", "PGHOST"),
    )
    port: int = Field(
        default=5432,
        validation_alias=AliasChoices("EDUTAP_DBDEF_PORT", "PGPORT"),
    )
    database: str = Field(
        default="edutap", validation_alias=AliasChoices("EDUTAP_DBDEF_DATABASE", "PGDATABASE")
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

    def url(self) -> str:
        """Return the SQLAlchemy URL for a synchronous psycopg connection."""
        if self.dsn:
            return self.dsn
        query = {}
        if self.sslmode:
            query["sslmode"] = self.sslmode
        if self.sslrootcert:
            query["sslrootcert"] = self.sslrootcert
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password.get_secret_value() or None,
            host=self.host,
            port=self.port,
            database=self.database,
            query=query,
        ).render_as_string(hide_password=False)
