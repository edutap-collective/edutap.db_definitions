"""Command line interface: create, diff, check, apply."""

import argparse
import sys

COMMANDS: tuple[str, ...] = ("create", "diff", "check", "apply")
"""The subcommands, in help order. The documentation test checks against this."""


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser with all four subcommands."""
    parser = argparse.ArgumentParser(
        prog="edutap-dbdef",
        description="Generate the database schema SQL for an eduTAP deployment.",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("create", help="render the baseline DDL")
    subcommands.add_parser("diff", help="render ALTER statements against a database")
    subcommands.add_parser("check", help="fail if the database deviates from the definitions")
    subcommands.add_parser("apply", help="apply a generated SQL file")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    if args.command is None:
        return 2
    return 0


def run() -> None:
    """Console script entry point."""
    sys.exit(main())
