"""Tests for the CLI skeleton."""

import subprocess
import sys


def test_help_lists_the_four_subcommands():
    """Help output must list all four subcommands."""
    result = subprocess.run(
        [sys.executable, "-m", "edutap.db_definitions", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    for command in ("create", "diff", "check", "apply"):
        assert command in result.stdout


def test_main_returns_two_on_missing_subcommand():
    """Missing subcommand should return exit code 2."""
    from edutap.db_definitions.cli import main

    assert main([]) == 2
