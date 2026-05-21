"""Smoke tests for the Typer CLI entry point."""

from __future__ import annotations

from typer.testing import CliRunner

from auladcanto import __version__
from auladcanto.cli import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    """`auladcanto --version` exits 0 and prints the package version."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert __version__ in result.output


def test_help_lists_all_subcommands() -> None:
    """`auladcanto --help` advertises every stub subcommand."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    expected_commands = (
        "init",
        "calibrar",
        "mcp-server",
        "verificar-deps",
        "limpar-cache",
    )
    for cmd in expected_commands:
        assert cmd in result.output, f"missing subcommand {cmd!r} in --help output"


def test_init_subcommand_is_stub() -> None:
    """The `init` subcommand is wired but still a phase-7 stub."""
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert "phase 7" in result.output.lower()
