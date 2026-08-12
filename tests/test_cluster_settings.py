"""What a connection to a replicating cluster has to get right.

These assert against the REAL dialects rather than against the URL string. A test
that greps a URL for "target_session_attrs" is green just as happily when the
parameter sits somewhere SQLAlchemy never looks -- and the failure would then only
show in production, as a write landing on a read-only replica. So every URL here is
handed to the dialect's `create_connect_args`, and what is asserted is what the
driver would actually receive.

Note that asyncpg is NOT a dependency of this package and is not installed here. The
dialect loads its DBAPI only when a connection is opened, so `create_connect_args`
works without it -- which is the point: the async contract is verified where it is
defined, and the package that actually connects brings the driver.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
from sqlalchemy.engine import make_url

from edutap.db_definitions.settings import (
    ASYNC_DRIVER,
    SYNC_DRIVER,
    ClusterSettings,
    Settings,
)

_CLUSTER = "pg-ccc-01,pg-ccn-01,pg-ccn-02,pg-ccn-03,pg-ccn-04"


def async_kwargs(url: str) -> dict:
    """Return what asyncpg.connect would receive for this URL."""
    return PGDialect_asyncpg().create_connect_args(make_url(url))[1]


def settings(**kwargs) -> ClusterSettings:
    base = dict(database="edutap", user="edutap", password="x")  # noqa: S106
    return ClusterSettings(_env_file=None, **{**base, **kwargs})


# --- the host list ----------------------------------------------------------


def test_every_node_of_the_cluster_reaches_the_async_driver():
    kwargs = async_kwargs(settings(hosts=_CLUSTER).url(ASYNC_DRIVER))

    assert kwargs["host"] == [
        "pg-ccc-01",
        "pg-ccn-01",
        "pg-ccn-02",
        "pg-ccn-03",
        "pg-ccn-04",
    ]
    assert kwargs["port"] == [5432] * 5


def test_every_node_of_the_cluster_survives_the_sync_url():
    url = make_url(settings(hosts=_CLUSTER).url(SYNC_DRIVER))

    # psycopg carries the fallback hosts in the query, libpq resolves them in order.
    assert url.query["host"] == (
        "pg-ccc-01:5432",
        "pg-ccn-01:5432",
        "pg-ccn-02:5432",
        "pg-ccn-03:5432",
        "pg-ccn-04:5432",
    )


def test_a_single_host_keeps_the_plain_form():
    # Nothing gained by making the common case exotic; the URL stays readable and the
    # existing consumers keep the shape they already have.
    url = make_url(settings(hosts="db.example").url())

    assert url.host == "db.example"
    assert url.port == 5432
    assert "host" not in url.query


def test_a_host_may_carry_its_own_port():
    kwargs = async_kwargs(settings(hosts="pg-a:5433,pg-b").url(ASYNC_DRIVER))

    assert kwargs["port"] == [5433, 5432]


def test_surrounding_whitespace_is_tolerated():
    kwargs = async_kwargs(settings(hosts=" pg-a , pg-b ").url(ASYNC_DRIVER))

    assert kwargs["host"] == ["pg-a", "pg-b"]


def test_a_list_that_names_nothing_is_refused():
    with pytest.raises(ValidationError):
        settings(hosts=" , ")


# --- picking the node that accepts writes -----------------------------------


def test_the_writable_node_is_selected_by_default_async():
    # One node of the cluster accepts writes. Without this a connection lands on a
    # replica, succeeds, and only the first INSERT fails -- as "read-only".
    kwargs = async_kwargs(settings(hosts=_CLUSTER).url(ASYNC_DRIVER))

    assert kwargs["target_session_attrs"] == "read-write"


def test_the_writable_node_is_selected_by_default_sync():
    url = make_url(settings(hosts=_CLUSTER).url(SYNC_DRIVER))

    assert url.query["target_session_attrs"] == "read-write"


def test_a_reader_can_drop_the_requirement():
    url = make_url(settings(hosts=_CLUSTER, target_session_attrs=None).url())

    assert "target_session_attrs" not in url.query


# --- TLS, which the two drivers spell differently ---------------------------


def test_the_sync_driver_gets_libpq_spelling():
    url = make_url(settings(hosts="pg-a", sslmode="verify-full", sslrootcert="/ca.pem").url())

    assert url.query["sslmode"] == "verify-full"
    assert url.query["sslrootcert"] == "/ca.pem"


def test_the_async_driver_gets_asyncpg_spelling():
    # asyncpg has no `sslmode` keyword; the parameter is called `ssl` and takes the
    # very same libpq mode names.
    kwargs = async_kwargs(settings(hosts="pg-a", sslmode="verify-full").url(ASYNC_DRIVER))

    assert kwargs["ssl"] == "verify-full"
    assert "sslmode" not in kwargs


def test_the_root_certificate_is_left_out_of_the_async_url():
    # asyncpg accepts `sslrootcert` from PGSSLROOTCERT only, never as a connect
    # keyword -- passing it would raise TypeError on the first connection.
    kwargs = async_kwargs(
        settings(hosts="pg-a", sslmode="verify-full", sslrootcert="/ca.pem").url(ASYNC_DRIVER)
    )

    assert "sslrootcert" not in kwargs


def test_tls_left_unset_reaches_neither_driver():
    assert "ssl" not in async_kwargs(settings(hosts="pg-a").url(ASYNC_DRIVER))
    assert "sslmode" not in make_url(settings(hosts="pg-a").url()).query


# --- credentials ------------------------------------------------------------


def test_a_password_with_reserved_characters_survives_both_urls():
    s = settings(hosts="pg-a,pg-b", password="p@ss:wo/rd?x")  # noqa: S106

    assert async_kwargs(s.url(ASYNC_DRIVER))["password"] == "p@ss:wo/rd?x"  # noqa: S105
    assert make_url(s.url(SYNC_DRIVER)).password == "p@ss:wo/rd?x"  # noqa: S105


def test_the_password_is_not_leaked_by_repr():
    assert "p@ss" not in repr(settings(hosts="pg-a", password="p@ssword"))  # noqa: S106


# --- the DDL tool's own settings still behave -------------------------------


def test_the_cli_settings_inherit_the_cluster_behaviour(monkeypatch):
    monkeypatch.setenv("PGHOST", "pg-a,pg-b")
    monkeypatch.setenv("PGPASSWORD", "secret")

    url = make_url(Settings().url())

    assert url.query["host"] == ("pg-a:5432", "pg-b:5432")
    assert url.query["target_session_attrs"] == "read-write"


def test_the_old_singular_variable_is_still_accepted(monkeypatch):
    # Every field here has a default, so a dropped alias would not fail loudly -- it
    # would quietly fall back to "postgres". Kept for exactly that reason.
    monkeypatch.setenv("EDUTAP_DBDEF_HOST", "from-the-old-name")

    assert make_url(Settings().url()).host == "from-the-old-name"
