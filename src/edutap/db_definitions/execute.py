"""Apply a generated SQL document to a database."""

import logging

from sqlalchemy import create_engine

logger = logging.getLogger("edutap.db_definitions")


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


def _count_schema_statements(sql: str) -> int:
    """Count schema statements, excluding directives and comments."""
    count = 0
    for statement in sql.split(";"):
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
