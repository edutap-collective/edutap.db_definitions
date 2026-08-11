"""Apply a generated SQL document to a database."""

import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

logger = logging.getLogger("edutap.db_definitions")

#: The advisory lock every migrating run takes, so only one can be in flight.
#:
#: Derived rather than chosen. A round number like `1` or `42` sits in the same flat,
#: cluster-wide bigint space as every other tool's advisory lock, and the collision
#: would show up as one of them blocking on the other for no visible reason. Python's
#: `hash()` is out for a different reason: `PYTHONHASHSEED` randomises it per process,
#: so two runs would take *different* locks and the guard would protect nothing while
#: looking like it did.
#:
#: Signed, because `bigint` is, and `pg_advisory_lock` takes exactly that.
ADVISORY_LOCK_KEY = int.from_bytes(
    hashlib.blake2b(b"edutap.db_definitions:migrate", digest_size=8).digest(),
    "big",
    signed=True,
)


@contextmanager
def advisory_lock(connection: Connection, key: int = ADVISORY_LOCK_KEY) -> Iterator[None]:
    """Hold a session-scoped advisory lock for the duration of the block.

    Session-scoped (`pg_advisory_lock`) rather than transaction-scoped
    (`pg_advisory_xact_lock`), and that follows from where the race actually is: on
    the **read**, not on the write. The renderer reflects the database to decide what
    is missing, and the statements it produces carry no `IF NOT EXISTS` — so a second
    run that has already decided "this table is absent" will fail a deploy that was
    perfectly correct. The lock therefore has to be held across rendering *and*
    applying, and rendering ends with a rollback (see the caller), which a
    transaction-scoped lock would not survive.

    The lock is advisory: it constrains only those who ask for it. Nothing stops a
    person with `psql` from changing the schema underneath a running migration, and
    nothing here pretends otherwise.
    """
    connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
    logger.debug("Advisory lock %d taken.", key)
    try:
        yield
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        logger.debug("Advisory lock %d released.", key)


def apply_sql(sql: str, url: str, dry_run: bool = False) -> int:
    """Execute the document and return the number of executed statements.

    Counts only schema statements (CREATE TABLE, CREATE INDEX, etc.),
    excluding transaction control directives (BEGIN, COMMIT, SET ROLE) and
    comments. Multi-line statements count as one.

    The document brings its own transaction control (``BEGIN;`` / ``COMMIT;``), so
    the connection runs in AUTOCOMMIT and the script is handed to the driver as
    one unit. Wrapping it in SQLAlchemy's own transaction instead would nest two
    transactions: the script's ``COMMIT`` would end the outer one and the block
    exit would then fail. A failing statement aborts the script's transaction, so
    nothing is left behind.
    """
    if dry_run:
        logger.info("Dry run: %d characters of SQL would be executed.", len(sql))
        return 0
    engine = create_engine(url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(sql)
    finally:
        engine.dispose()
    return _count_schema_statements(sql)


def _split_statements(sql: str) -> list[str]:
    """Split a document on statement boundaries, ignoring dollar-quoted bodies.

    A guarded type creation (``DO $$ BEGIN ... END $$;``) carries semicolons
    inside its body. Splitting on every semicolon would count one such block as
    several statements.
    """
    statements: list[str] = []
    current: list[str] = []
    inside_dollar_quote = False
    index = 0
    while index < len(sql):
        if sql.startswith("$$", index):
            inside_dollar_quote = not inside_dollar_quote
            current.append("$$")
            index += 2
            continue
        character = sql[index]
        if character == ";" and not inside_dollar_quote:
            statements.append("".join(current))
            current = []
        else:
            current.append(character)
        index += 1
    statements.append("".join(current))
    return statements


def _count_schema_statements(sql: str) -> int:
    """Count schema statements, excluding directives and comments."""
    count = 0
    for statement in _split_statements(sql):
        # Remove comments from each line and strip whitespace
        lines = []
        for line in statement.splitlines():
            # Remove comment part (everything after --)
            if "--" in line:
                line = line[: line.index("--")]
            lines.append(line.strip())

        # Join lines and clean up
        clean = " ".join(lines).strip()

        # Skip if empty or a directive (BEGIN, COMMIT, SET ROLE)
        if clean and not any(clean.upper().startswith(d) for d in ["BEGIN", "COMMIT", "SET ROLE"]):
            count += 1

    return count
