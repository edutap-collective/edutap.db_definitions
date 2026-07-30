from edutap.db_definitions.settings import Settings


def test_reads_the_pg_variables(monkeypatch):
    monkeypatch.setenv("PGHOST", "db.example")
    monkeypatch.setenv("PGDATABASE", "edutap")
    monkeypatch.setenv("PGUSER", "edutap_ddl")
    monkeypatch.setenv("PGPASSWORD", "secret")
    url = Settings().url()
    assert url.startswith("postgresql+psycopg://edutap_ddl:secret@db.example:5432/edutap")


def test_prefixed_variables_win_over_pg(monkeypatch):
    monkeypatch.setenv("PGHOST", "from-pg")
    monkeypatch.setenv("EDUTAP_DBDEF_HOST", "from-prefix")
    assert "from-prefix" in Settings().url()


def test_dsn_overrides_everything(monkeypatch):
    monkeypatch.setenv("PGHOST", "ignored")
    monkeypatch.setenv("EDUTAP_DBDEF_DSN", "postgresql+psycopg://u:p@h/db")
    assert Settings().url() == "postgresql+psycopg://u:p@h/db"


def test_ssl_settings_become_query_parameters(monkeypatch):
    monkeypatch.setenv("PGSSLMODE", "verify-full")
    monkeypatch.setenv("PGSSLROOTCERT", "/ca_cert.pem")
    url = Settings().url()
    assert "sslmode=verify-full" in url
    assert "sslrootcert=%2Fca_cert.pem" in url or "sslrootcert=/ca_cert.pem" in url


def test_password_is_not_leaked_by_repr(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "secret")
    assert "secret" not in repr(Settings())
