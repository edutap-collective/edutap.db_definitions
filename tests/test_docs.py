"""Documentation stays in sync with the implemented CLI.

These tests do not check prose quality — they check the one thing that rots
silently: a flag or subcommand mentioned in the docs that the CLI does not
actually have (or vice versa for the reference page).
"""

import pathlib
import re
import subprocess
import sys

from edutap.db_definitions.cli import COMMANDS

ROOT = pathlib.Path(__file__).parent.parent


def cli_help() -> str:
    """Return the top-level help plus the help of every subcommand."""
    texts = [_run([])]
    texts.extend(_run([command]) for command in COMMANDS)
    return "\n".join(texts)


def _run(arguments: list[str]) -> str:
    # arguments always comes from COMMANDS (this module), never external input.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "edutap.db_definitions", *arguments, "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_every_subcommand_is_documented():
    reference = (ROOT / "docs" / "reference.md").read_text()
    for command in COMMANDS:
        assert f"`{command}`" in reference, f"{command} missing from reference.md"


def test_readme_mentions_no_flag_the_cli_does_not_have():
    readme = (ROOT / "README.md").read_text()
    help_text = cli_help()
    for flag in sorted(set(re.findall(r"--[a-z][a-z-]+", readme))):
        assert flag in help_text, f"{flag} in README but not in the CLI"
