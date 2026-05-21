"""Filesystem layout for auladcanto runtime state.

The home directory is configurable via the ``AULADCANTO_HOME`` environment
variable (resolved lazily so tests can ``monkeypatch.setenv`` before the
helpers are called). The default is ``~/.auladcanto/``.

The directory tree is intentionally **not** created at import time — call
:func:`ensure_home_exists` from the CLI's ``init`` command (or any code path
that genuinely needs the dirs) to keep imports side-effect free.
"""

from __future__ import annotations

import os
from pathlib import Path


def _home() -> Path:
    """Return the auladcanto home directory.

    Reads ``AULADCANTO_HOME`` at call time so tests can override it via
    ``monkeypatch.setenv`` after the module has already been imported.
    """
    override = os.environ.get("AULADCANTO_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".auladcanto"


# Public module-level constant. Snapshot of the home dir at import time;
# tests that need to override the location should prefer the helper functions
# below (which re-read the env var on every call).
AULADCANTO_HOME: Path = _home()


def home_dir() -> Path:
    """Return the resolved auladcanto home directory (re-reads env on each call)."""
    return _home()


def perfil_path() -> Path:
    """Path to the user profile JSON (``~/.auladcanto/perfil.json``)."""
    return home_dir() / "perfil.json"


def cache_dir() -> Path:
    """Directory holding cached gabaritos and downloaded audio."""
    return home_dir() / "cache"


def sessoes_dir() -> Path:
    """Directory holding in-progress and recently-completed sessions."""
    return home_dir() / "sessoes"


def historico_dir() -> Path:
    """Directory holding per-song historical progress files."""
    return home_dir() / "historico"


def ensure_home_exists() -> Path:
    """Create the auladcanto directory tree idempotently.

    Returns the resolved home directory. Safe to call repeatedly — each
    ``mkdir`` uses ``exist_ok=True``.
    """
    root = home_dir()
    root.mkdir(parents=True, exist_ok=True)
    for child in (cache_dir(), sessoes_dir(), historico_dir()):
        child.mkdir(parents=True, exist_ok=True)
    return root
