"""Command line interface: create, diff, check, apply, migrate."""

import argparse
import pathlib
import sys
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from .compare import describe_changes, foreign_tables, render_diff, rendered_diff
from .contract import ContractError, check_contract, raise_on_violations
from .definition import DefinitionError
from .discovery import DiscoveryError, load_definitions
from .render import RenderError, render_create, render_create_split


def subcommand_names(parser: argparse.ArgumentParser) -> tuple[str, ...]:
    """Return a parser's subcommand names, in the order they were added.

    argparse offers no public API for this, so the subparser action is looked up
    directly. It is worth the private attribute: the documentation drift guard in
    the tests treats this list as the authority on what subcommands exist, and a
    hand-maintained copy would let a fifth subcommand escape that guard.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return tuple(action.choices)
    return ()


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--packages", type=_csv, default=None, help="only these packages")
    parser.add_argument("--exclude", type=_csv, default=[], help="skip these packages")


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser with all four subcommands."""
    parser = argparse.ArgumentParser(
        prog="edutap-dbdef",
        description="Generate the database schema SQL for an eduTAP deployment.",
    )
    subcommands = parser.add_subparsers(dest="command")

    create = subcommands.add_parser("create", help="render the baseline DDL")
    _add_selection_arguments(create)
    create.add_argument("--out", type=pathlib.Path, default=None, help="write to this file")
    create.add_argument(
        "--split", type=pathlib.Path, default=None, help="write one file per package into DIR"
    )
    create.add_argument("--ddl-role", default=None, help="emit SET ROLE <role> in the header")
    create.add_argument(
        "--timestamp", action="store_true", help="add a generation timestamp (breaks byte equality)"
    )

    diff = subcommands.add_parser("diff", help="render ALTER statements against a database")
    _add_selection_arguments(diff)
    diff.add_argument("--out", type=pathlib.Path, default=None, help="write to this file")
    diff.add_argument("--ddl-role", default=None, help="emit SET ROLE <role> in the header")
    diff.add_argument(
        "--allow-destructive", action="store_true", help="emit DROP statements uncommented"
    )

    check = subcommands.add_parser(
        "check", help="fail if the database deviates from the definitions"
    )
    _add_selection_arguments(check)

    apply_command = subcommands.add_parser("apply", help="apply a generated SQL file")
    apply_command.add_argument("file", type=pathlib.Path, help="the SQL file to apply")
    apply_command.add_argument("--dry-run", action="store_true", help="do not execute anything")

    migrate = subcommands.add_parser(
        "migrate", help="bring the database in line, refusing anything destructive"
    )
    _add_selection_arguments(migrate)
    migrate.add_argument("--ddl-role", default=None, help="emit SET ROLE <role> in the header")
    migrate.add_argument(
        "--dry-run", action="store_true", help="report what would be applied, change nothing"
    )
    return parser


COMMANDS: tuple[str, ...] = subcommand_names(build_parser())
"""The subcommands, in help order, derived from the parser itself."""


def _load_checked(args: argparse.Namespace):
    definitions = load_definitions(include=args.packages, exclude=args.exclude)
    raise_on_violations(check_contract(definitions))
    return definitions


def _warn_without_ddl_role(args: argparse.Namespace) -> None:
    """Say on stderr what the document says in its header.

    Whoever generates a file sees this; whoever reviews it later sees the header
    line. Neither should have to guess whether the flag was left out on purpose.
    """
    if not args.ddl_role:
        sys.stderr.write(
            "NOTE: generated without --ddl-role; objects will be owned by whichever "
            "user applies this file, and default-privilege grants for a DDL role "
            "will not apply to them.\n"
        )


def _command_create(args: argparse.Namespace) -> int:
    definitions = _load_checked(args)
    _warn_without_ddl_role(args)
    stamp = datetime.now(tz=UTC).isoformat(timespec="seconds") if args.timestamp else None
    if args.split:
        args.split.mkdir(parents=True, exist_ok=True)
        for name, document in render_create_split(definitions, args.ddl_role, stamp).items():
            (args.split / f"{name}.sql").write_text(document)
        return 0
    document = render_create(definitions, args.ddl_role, stamp)
    if args.out:
        args.out.write_text(document)
    else:
        sys.stdout.write(document)
    return 0


def _connect():
    from sqlalchemy import create_engine

    from .settings import Settings

    return create_engine(Settings().url())


def _command_diff(args: argparse.Namespace) -> int:
    definitions = _load_checked(args)
    _warn_without_ddl_role(args)
    engine = _connect()
    try:
        with engine.connect() as connection:
            document = render_diff(connection, definitions, args.ddl_role, args.allow_destructive)
            skipped = foreign_tables(connection, definitions)
    finally:
        engine.dispose()
    if skipped:
        sys.stderr.write(f"Ignored tables of other owners: {', '.join(skipped)}\n")
    if args.out:
        args.out.write_text(document)
    else:
        sys.stdout.write(document)
    return 0


def _command_check(args: argparse.Namespace) -> int:
    definitions = _load_checked(args)
    engine = _connect()
    try:
        with engine.connect() as connection:
            changes = describe_changes(connection, definitions)
            skipped = foreign_tables(connection, definitions)
    finally:
        engine.dispose()
    if skipped:
        sys.stderr.write(f"Ignored tables of other owners: {', '.join(skipped)}\n")
    if changes:
        sys.stderr.write("Schema deviates from the definitions:\n")
        for change in changes:
            sys.stderr.write(f"  {change}\n")
        return 1
    sys.stdout.write("Schema is in sync with the definitions.\n")
    return 0


def _command_apply(args: argparse.Namespace) -> int:
    from .execute import apply_sql
    from .settings import Settings

    executed = apply_sql(args.file.read_text(), Settings().url(), args.dry_run)
    sys.stdout.write(
        "Dry run, nothing executed.\n" if args.dry_run else f"Executed {executed} statements.\n"
    )
    return 0


#: Exit code for a diff this command refuses to apply.
#:
#: Its own code, distinct from 1. A deploy that stops here has found nothing broken --
#: it has found a change that a person has to look at, and telling that apart from "the
#: database is unreachable" is the difference between paging someone and filing a task.
EXIT_REFUSED = 3


def _command_migrate(args: argparse.Namespace) -> int:
    """Bring the database in line with the definitions, or refuse and say why.

    Additive changes are applied; anything that drops is refused and ends the run. The
    asymmetry is the whole point: a change that only adds fails, at worst, on something
    that already exists, while a drop removes something that is not coming back -- and
    no deploy at three in the morning gets to decide that on its own.

    The advisory lock is held across **rendering and applying**, because the race is on
    the read: the renderer reflects the database to decide what is missing, and the
    statements it produces carry no `IF NOT EXISTS`. Two runs that both decide "this
    table is absent" would have the second one fail a deploy that was correct.
    """
    # Imported here rather than at module scope, like the other database commands:
    # the core install carries no driver, and importing one at import time would make
    # `create` unusable wherever `[cli]` is not installed.
    from .execute import advisory_lock, apply_sql
    from .settings import Settings

    definitions = _load_checked(args)
    _warn_without_ddl_role(args)
    engine = _connect()
    try:
        with engine.connect() as connection, advisory_lock(connection):
            diff = rendered_diff(connection, definitions, args.ddl_role)
            skipped = foreign_tables(connection, definitions)
            # Reflection took an AccessShareLock on every table it looked at. Applying
            # runs on its own connection, and an ALTER TABLE would wait for this one --
            # which is why the lock above is session-scoped and survives this rollback.
            connection.rollback()

            if skipped:
                sys.stderr.write(f"Ignored tables of other owners: {', '.join(skipped)}\n")

            if diff.destructive:
                _report_refusal(diff.destructive)
                return EXIT_REFUSED

            if not diff.additive:
                sys.stdout.write("Schema is in sync with the definitions; nothing to do.\n")
                return 0

            if args.dry_run:
                _report_pending(diff.additive)
                return 0

            executed = apply_sql(diff.document, Settings().url())
            sys.stdout.write(f"Applied {executed} statement(s).\n")
            for statement in diff.additive:
                sys.stdout.write(f"  {_one_line(statement)}\n")
            return 0
    finally:
        engine.dispose()


def _report_refusal(statements: tuple[str, ...]) -> None:
    """Say what was refused, in full, on stderr.

    The statements themselves and not a count. Whoever reads this is looking at a red
    deploy in a log window, without the file and usually without the context -- "1
    destructive change detected" tells them to go looking, the statement tells them
    what happened.
    """
    sys.stderr.write(
        f"Refused: the diff would drop something ({len(statements)} statement(s)).\n"
        "Nothing was applied. A drop is not a deploy-time decision -- prepare it as SQL,\n"
        "have it reviewed, and apply it deliberately.\n"
    )
    for statement in statements:
        sys.stderr.write(f"  {_one_line(statement)}\n")


def _report_pending(statements: tuple[str, ...]) -> None:
    """Say what a real run would apply."""
    sys.stdout.write(f"Dry run: {len(statements)} statement(s) would be applied.\n")
    for statement in statements:
        sys.stdout.write(f"  {_one_line(statement)}\n")


def _one_line(statement: str) -> str:
    """Collapse a possibly multi-line statement onto one log line."""
    return " ".join(statement.split())


def _first_line(error: Exception) -> str:
    """Return an error's first line; SQLAlchemy's messages carry several."""
    return str(error).strip().splitlines()[0]


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    if args.command is None:
        return 2
    try:
        if args.command == "create":
            return _command_create(args)
        if args.command == "diff":
            return _command_diff(args)
        if args.command == "check":
            return _command_check(args)
        if args.command == "apply":
            return _command_apply(args)
        if args.command == "migrate":
            return _command_migrate(args)
    # Every failure an operator can cause becomes a message and exit code 1.
    # A traceback here is noise at best: this runs while preparing a schema
    # change that a privileged role will apply.
    except (ContractError, DefinitionError, DiscoveryError, RenderError) as error:
        sys.stderr.write(f"{error}\n")
        return 1
    except OSError as error:
        sys.stderr.write(f"Cannot read or write file: {error}\n")
        return 1
    except SQLAlchemyError as error:
        sys.stderr.write(f"Database error: {_first_line(error)}\n")
        return 1
    return 0


def run() -> None:
    """Console script entry point."""
    sys.exit(main())
