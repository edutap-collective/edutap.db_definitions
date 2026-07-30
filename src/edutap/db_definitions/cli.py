"""Command line interface: create, diff, check, apply."""

import argparse
import pathlib
import sys
from datetime import UTC, datetime

from .compare import describe_changes, foreign_tables, render_diff
from .contract import ContractError, check_contract, raise_on_violations
from .discovery import load_definitions
from .render import render_create, render_create_split

COMMANDS: tuple[str, ...] = ("create", "diff", "check", "apply")
"""The subcommands, in help order. The documentation test checks against this."""


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

    subcommands.add_parser("apply", help="apply a generated SQL file")
    return parser


def _load_checked(args: argparse.Namespace):
    definitions = load_definitions(include=args.packages, exclude=args.exclude)
    raise_on_violations(check_contract(definitions))
    return definitions


def _command_create(args: argparse.Namespace) -> int:
    definitions = _load_checked(args)
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
    except ContractError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    return 0


def run() -> None:
    """Console script entry point."""
    sys.exit(main())
