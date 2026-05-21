"""Tests for the ``auladcanto.storage.paths`` module."""

from __future__ import annotations

from pathlib import Path

import pytest

from auladcanto.storage import paths


def test_home_dir_respects_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``AULADCANTO_HOME`` overrides the default ``~/.auladcanto`` location."""
    target = tmp_path / "custom-home"
    monkeypatch.setenv("AULADCANTO_HOME", str(target))
    assert paths.home_dir() == target


def test_home_dir_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the env var, the home dir resolves to ``~/.auladcanto``."""
    monkeypatch.delenv("AULADCANTO_HOME", raising=False)
    assert paths.home_dir() == Path.home() / ".auladcanto"


def test_ensure_home_exists_creates_subdirectories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``ensure_home_exists`` builds the full directory tree idempotently."""
    target = tmp_path / "auladcanto-home"
    monkeypatch.setenv("AULADCANTO_HOME", str(target))

    returned = paths.ensure_home_exists()

    assert returned == target
    assert target.is_dir()
    assert paths.cache_dir().is_dir()
    assert paths.sessoes_dir().is_dir()
    assert paths.historico_dir().is_dir()

    # Calling twice must not raise.
    paths.ensure_home_exists()


def test_path_helpers_use_current_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Helpers re-read the env var on each call (no import-time snapshot)."""
    target = tmp_path / "env-home"
    monkeypatch.setenv("AULADCANTO_HOME", str(target))

    assert paths.perfil_path() == target / "perfil.json"
    assert paths.cache_dir() == target / "cache"
    assert paths.sessoes_dir() == target / "sessoes"
    assert paths.historico_dir() == target / "historico"
