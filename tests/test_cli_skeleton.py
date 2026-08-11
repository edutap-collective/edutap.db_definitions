"""Tests for the CLI skeleton."""

import subprocess
import sys


def test_help_lists_the_four_subcommands():
    """Help output must list all four subcommands."""
    from edutap.db_definitions.cli import COMMANDS

    result = subprocess.run(
        [sys.executable, "-m", "edutap.db_definitions", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    for command in COMMANDS:
        assert command in result.stdout


def test_commands_is_derived_from_the_parser():
    """COMMANDS is what the documentation drift guard checks against.

    Deriving it from the parser is what keeps a newly added subcommand from
    escaping that guard; this test pins the four that exist today.
    """
    from edutap.db_definitions.cli import COMMANDS

    assert COMMANDS == ("create", "diff", "check", "apply", "migrate")


def test_main_returns_two_on_missing_subcommand():
    """Missing subcommand should return exit code 2."""
    from edutap.db_definitions.cli import main

    assert main([]) == 2
