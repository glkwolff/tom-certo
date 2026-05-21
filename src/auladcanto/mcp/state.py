"""Process-wide state for the running MCP server (phase 5).

The MCP server is a single-process, single-user app, so the state is held in a
module-level singleton. The state is split into two parts:

* :class:`SessionState` — the live practice session (music id, mode, batches
  emitted so far). Resets on every :func:`auladcanto.mcp.tools.sessao.iniciar_sessao`.
* :class:`ServerState` — the longer-lived process state (profile, current
  session). One instance per process.

Tests reach in through :func:`reset_state` to clear the singleton between cases
so each test starts from a clean slate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from auladcanto.domain.perfil_aluno import PerfilAluno


@dataclass
class SessionState:
    """Mutable state for the currently-running practice session."""

    musica_id: str | None = None
    modo: str | None = None
    voz_escolhida: str = "n/a"
    started_at: datetime | None = None
    batches: list[dict[str, Any]] = field(default_factory=list)
    is_active: bool = False
    is_paused: bool = False


@dataclass
class ServerState:
    """Top-level state singleton for the MCP server process."""

    perfil: PerfilAluno | None = None
    sessao: SessionState = field(default_factory=SessionState)


_state: ServerState | None = None


def get_state() -> ServerState:
    """Return the process-wide :class:`ServerState`, creating it on first call."""
    global _state
    if _state is None:
        _state = ServerState()
    return _state


def reset_state() -> None:
    """Drop the cached singleton so the next :func:`get_state` returns a fresh one."""
    global _state
    _state = None


__all__ = ["ServerState", "SessionState", "get_state", "reset_state"]
