"""Smoke tests for the Typer CLI entry point.

The tests never touch the real ``~/.auladcanto/`` — every test that hits the
filesystem points ``AULADCANTO_HOME`` at a ``tmp_path`` via ``monkeypatch`` so
the suite stays hermetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from auladcanto import __version__
from auladcanto.cli import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    """``auladcanto --version`` exits 0 and prints the package version."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert __version__ in result.output


def test_help_lists_all_subcommands() -> None:
    """``auladcanto --help`` advertises every shipped subcommand."""
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


def test_init_creates_home_and_seeds_skill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``init`` creates the home directory tree and copies ``SKILL.md`` into it."""
    target_home = tmp_path / "auladcanto-home"
    monkeypatch.setenv("AULADCANTO_HOME", str(target_home))
    # The interactive prompt asks whether to copy CLAUDE.md into the cwd — say no.
    result = runner.invoke(app, ["init"], input="n\n")
    assert result.exit_code == 0, result.output
    assert target_home.is_dir()
    assert (target_home / "cache").is_dir()
    assert (target_home / "sessoes").is_dir()
    assert (target_home / "historico").is_dir()
    assert (target_home / "SKILL.md").is_file()
    # Skill content sanity check — first heading should mention the persona.
    skill_text = (target_home / "SKILL.md").read_text(encoding="utf-8")
    assert "auladcanto" in skill_text.lower()


def test_init_preserves_existing_skill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``init`` does not overwrite a pre-existing ``SKILL.md`` (user customisations)."""
    target_home = tmp_path / "auladcanto-home"
    target_home.mkdir()
    sentinel = target_home / "SKILL.md"
    sentinel.write_text("# my customised skill\n", encoding="utf-8")
    monkeypatch.setenv("AULADCANTO_HOME", str(target_home))

    result = runner.invoke(app, ["init"], input="n\n")

    assert result.exit_code == 0, result.output
    assert sentinel.read_text(encoding="utf-8") == "# my customised skill\n"


def test_limpar_cache_aborts_when_declined(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``limpar-cache`` is a no-op when the user declines the confirmation prompt."""
    target_home = tmp_path / "auladcanto-home"
    monkeypatch.setenv("AULADCANTO_HOME", str(target_home))
    (target_home / "cache").mkdir(parents=True)
    sentinel = target_home / "cache" / "marker.txt"
    sentinel.write_text("keep me", encoding="utf-8")

    result = runner.invoke(app, ["limpar-cache"], input="n\n")

    assert result.exit_code == 0, result.output
    assert sentinel.exists()


def test_limpar_cache_clears_when_confirmed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When confirmed, ``limpar-cache`` empties ``cache/`` and ``sessoes/`` but keeps the profile."""
    target_home = tmp_path / "auladcanto-home"
    monkeypatch.setenv("AULADCANTO_HOME", str(target_home))
    (target_home / "cache").mkdir(parents=True)
    (target_home / "sessoes").mkdir(parents=True)
    (target_home / "cache" / "stale.json").write_text("{}", encoding="utf-8")
    (target_home / "sessoes" / "old.json").write_text("{}", encoding="utf-8")
    profile = target_home / "perfil.json"
    profile.write_text('{"keep": true}', encoding="utf-8")

    result = runner.invoke(app, ["limpar-cache"], input="y\n")

    assert result.exit_code == 0, result.output
    assert (target_home / "cache").is_dir()
    assert (target_home / "sessoes").is_dir()
    assert list((target_home / "cache").iterdir()) == []
    assert list((target_home / "sessoes").iterdir()) == []
    assert profile.read_text(encoding="utf-8") == '{"keep": true}'


def test_verificar_deps_runs_without_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``verificar-deps`` exits cleanly regardless of which optional deps are present."""
    monkeypatch.setenv("AULADCANTO_HOME", str(tmp_path / "auladcanto-home"))
    result = runner.invoke(app, ["verificar-deps"])
    # Exit code depends on whether ffmpeg is on PATH; just assert it is one of the
    # documented values and that the report mentions ffmpeg either way.
    assert result.exit_code in (0, 1), result.output
    assert "ffmpeg" in result.output
