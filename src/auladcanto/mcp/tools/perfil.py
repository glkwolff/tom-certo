"""MCP tools for student-profile inspection and microphone calibration."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from auladcanto.domain.analysis.capture import (
    AudioCaptureProtocol,
    CaptureConfig,
    SoundDeviceCapture,
)
from auladcanto.domain.calibration.microfone import (
    CalibradorMicrofone,
    CalibrationConfig,
)
from auladcanto.domain.perfil_aluno import PerfilAluno
from auladcanto.mcp.state import get_state
from auladcanto.storage.paths import historico_dir, perfil_path

CaptureFactory = Callable[[CaptureConfig], AudioCaptureProtocol]


def _default_capture_factory(config: CaptureConfig) -> AudioCaptureProtocol:
    return SoundDeviceCapture(config)


_capture_factory: CaptureFactory = _default_capture_factory


def set_capture_factory(factory: CaptureFactory) -> None:
    """Override the production capture factory (used by tests)."""
    global _capture_factory
    _capture_factory = factory


def reset_overrides() -> None:
    """Restore the default capture factory."""
    global _capture_factory
    _capture_factory = _default_capture_factory


def _load_or_create_perfil() -> PerfilAluno:
    state = get_state()
    if state.perfil is not None:
        return state.perfil
    path = perfil_path()
    if path.exists():
        try:
            state.perfil = PerfilAluno.load(path)
            return state.perfil
        except (OSError, ValueError):
            pass
    state.perfil = PerfilAluno(criado=datetime.now(UTC))
    return state.perfil


def get_perfil_aluno() -> dict[str, Any]:
    """Return the persisted student profile, creating a default one if missing."""
    perfil = _load_or_create_perfil()
    return perfil.model_dump(mode="json")


def get_historico(musica_id: str) -> dict[str, Any]:
    """Return aggregate progress data for ``musica_id``, or an empty shell when none."""
    path = historico_dir() / musica_id / "progresso.json"
    if not path.exists():
        return {"musica_id": musica_id, "sessoes": [], "tem_historico": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"musica_id": musica_id, "sessoes": [], "tem_historico": False}
    if not isinstance(payload, dict):
        return {"musica_id": musica_id, "sessoes": [], "tem_historico": False}
    return {"musica_id": musica_id, "tem_historico": True, **payload}


async def calibrar_microfone(
    config: CalibrationConfig | None = None,
) -> dict[str, Any]:
    """Run the four-step microphone calibration and persist it onto the profile."""
    cfg = config or CalibrationConfig()
    capture = _capture_factory(
        CaptureConfig(sample_rate=cfg.sample_rate, chunk_size=512, channels=1)
    )
    calibrador = CalibradorMicrofone(capture, cfg)
    resultado = await calibrador.calibrar()

    perfil = _load_or_create_perfil()
    updated = perfil.model_copy(update={"calibracao": resultado})
    updated.save(perfil_path())
    state = get_state()
    state.perfil = updated

    return {
        "status": "ok",
        "calibracao": resultado.model_dump(mode="json"),
    }


__all__ = [
    "CaptureFactory",
    "calibrar_microfone",
    "get_historico",
    "get_perfil_aluno",
    "reset_overrides",
    "set_capture_factory",
]
