"""MCP tools for live session control (start, pause, batch retrieval, context)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from auladcanto.domain.analysis.buffer import BatchBuffer, ClosedBatch
from auladcanto.domain.analysis.capture import (
    AudioCaptureProtocol,
    CaptureConfig,
    SoundDeviceCapture,
)
from auladcanto.domain.gabarito import Gabarito
from auladcanto.mcp.state import get_state
from auladcanto.mcp.tools.musica import verificar_cache
from auladcanto.storage.paths import cache_dir, sessoes_dir

Modo = Literal["voz", "violao", "ambos"]
VozEscolhida = Literal["aguda", "grave", "solo", "n/a"]

CaptureFactory = Callable[[CaptureConfig], AudioCaptureProtocol]
BatchAnalyzer = Callable[[ClosedBatch, Gabarito, str, str], Awaitable[dict[str, Any]]]

_BATCH_DURATION_SECONDS = 30
_DEFAULT_SAMPLE_RATE = 44100
_DEFAULT_CHUNK_SIZE = 512


def _default_capture_factory(config: CaptureConfig) -> AudioCaptureProtocol:
    return SoundDeviceCapture(config)


async def _default_batch_analyzer(
    batch: ClosedBatch,
    gabarito: Gabarito,
    musica_id: str,
    voz_escolhida: str,
) -> dict[str, Any]:
    """Placeholder analyzer that emits a schema-shaped stub.

    Phase 3B/3C will replace this with the real analyzer chain; until then we
    surface a minimal but schema-valid :class:`BatchReport` dict so the MCP
    surface can be exercised end-to-end.
    """
    del gabarito
    return {
        "schema_version": 1,
        "batch_numero": batch.batch_numero,
        "timestamp": batch.ended_at.isoformat(),
        "musica_id": musica_id,
        "duracao_segundos": max(1, round(batch.total_samples / max(batch.sample_rate, 1))),
        "posicao_musica": f"{batch.batch_numero * _BATCH_DURATION_SECONDS}s",
        "voz_escolhida": voz_escolhida,
        "timing": {
            "bpm_usuario": 0.0,
            "bpm_gabarito": 0.0,
            "desvio_bpm": 0.0,
            "acelerando_no_batch": False,
            "irregularidade_ritmica": 0.0,
        },
        "pitch": {
            "notas_corretas_pct": 0.0,
            "precisao_oitava_pct": 0.0,
            "desvio_padrao_cents": 0.0,
            "ataque_predominante": "indeterminado",
            "momentos_criticos": [],
        },
        "vibrato": {"detectado": False},
        "respiracao": {"respiros_detectados": 0, "respiros": [], "alerta_sem_respiro": False},
        "volume": {
            "media_normalizada": 0.0,
            "quedas_abruptas": 0,
            "projecao_geral": "fraca",
        },
    }


_capture_factory: CaptureFactory = _default_capture_factory
_batch_analyzer: BatchAnalyzer = _default_batch_analyzer
_background_task: asyncio.Task[None] | None = None
_buffer: BatchBuffer | None = None


def set_capture_factory(factory: CaptureFactory) -> None:
    """Override the production capture factory (used by tests)."""
    global _capture_factory
    _capture_factory = factory


def set_batch_analyzer(analyzer: BatchAnalyzer) -> None:
    """Override the production batch analyzer (used by tests)."""
    global _batch_analyzer
    _batch_analyzer = analyzer


def reset_overrides() -> None:
    """Restore production defaults and tear down any background task."""
    global _capture_factory, _batch_analyzer, _background_task, _buffer
    _capture_factory = _default_capture_factory
    _batch_analyzer = _default_batch_analyzer
    if _background_task is not None and not _background_task.done():
        _background_task.cancel()
    _background_task = None
    if _buffer is not None:
        _buffer.stop()
    _buffer = None


def _load_gabarito(musica_id: str) -> Gabarito | None:
    path = cache_dir() / musica_id / "gabarito.json"
    if not path.exists():
        return None
    try:
        return Gabarito.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def iniciar_sessao(
    musica_id: str,
    modo: Modo,
    voz_escolhida: VozEscolhida = "n/a",
) -> dict[str, Any]:
    """Start a live practice session for ``musica_id`` in the requested ``modo``."""
    cache_status = verificar_cache(musica_id)
    if not cache_status.get("processada"):
        return {
            "status": "error",
            "erro": f"musica '{musica_id}' is not in cache; call confirmar_download first",
        }

    gabarito = _load_gabarito(musica_id)
    if gabarito is None:
        return {"status": "error", "erro": f"gabarito for '{musica_id}' could not be loaded"}

    state = get_state()
    if state.sessao.is_active:
        return {
            "status": "error",
            "erro": "another session is already active; call pausar_sessao first",
        }

    state.sessao = type(state.sessao)(
        musica_id=musica_id,
        modo=modo,
        voz_escolhida=voz_escolhida,
        started_at=datetime.now(UTC),
        batches=[],
        is_active=True,
        is_paused=False,
    )

    global _background_task, _buffer
    config = CaptureConfig(
        sample_rate=_DEFAULT_SAMPLE_RATE,
        chunk_size=_DEFAULT_CHUNK_SIZE,
        channels=1,
    )
    capture = _capture_factory(config)
    _buffer = BatchBuffer(capture, batch_duration_seconds=_BATCH_DURATION_SECONDS)
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def _pump() -> None:
        async def _producer() -> None:
            assert _buffer is not None
            try:
                await _buffer.run(queue)
            finally:
                await queue.put(_SENTINEL)

        producer = asyncio.create_task(_producer())
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                report = await _batch_analyzer(item, gabarito, musica_id, voz_escolhida)
                state.sessao.batches.append(report)
        finally:
            if not producer.done():
                producer.cancel()
            with suppress_cancelled():
                await producer

    _background_task = asyncio.create_task(_pump())

    return {
        "status": "started",
        "musica_id": musica_id,
        "modo": modo,
        "voz_escolhida": voz_escolhida,
        "batch_duration_s": _BATCH_DURATION_SECONDS,
    }


async def pausar_sessao() -> dict[str, Any]:
    """Stop the active session, persist its state under ``sessoes_dir``."""
    state = get_state()
    if not state.sessao.is_active:
        return {"status": "noop", "mensagem": "no session is active"}

    global _background_task, _buffer
    if _buffer is not None:
        _buffer.stop()
    if _background_task is not None:
        with suppress_cancelled():
            await _background_task
    _background_task = None
    _buffer = None

    state.sessao.is_active = False
    state.sessao.is_paused = True

    musica_id = state.sessao.musica_id or "unknown"
    started_at = state.sessao.started_at or datetime.now(UTC)
    timestamp = started_at.strftime("%Y%m%dT%H%M%S")
    sessoes_dir().mkdir(parents=True, exist_ok=True)
    path = sessoes_dir() / f"{timestamp}-{musica_id}.json"
    payload = {
        "musica_id": musica_id,
        "modo": state.sessao.modo,
        "voz_escolhida": state.sessao.voz_escolhida,
        "started_at": started_at.isoformat(),
        "batches": list(state.sessao.batches),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "status": "paused",
        "musica_id": musica_id,
        "batches_persisted": len(state.sessao.batches),
        "session_path": str(path),
    }


def get_batch_atual() -> dict[str, Any]:
    """Return the most recently closed batch, or a stub when none exist yet."""
    state = get_state()
    if not state.sessao.batches:
        return {"batch": None, "mensagem": "no batch yet"}
    return {"batch": state.sessao.batches[-1]}


def get_contexto_sessao() -> dict[str, Any]:
    """Return the full list of batches accumulated by the current session."""
    state = get_state()
    return {
        "musica_id": state.sessao.musica_id,
        "modo": state.sessao.modo,
        "voz_escolhida": state.sessao.voz_escolhida,
        "started_at": state.sessao.started_at.isoformat() if state.sessao.started_at else None,
        "is_active": state.sessao.is_active,
        "batches": list(state.sessao.batches),
    }


class _Sentinel:
    """End-of-stream marker pushed onto the batch queue when the buffer drains."""


_SENTINEL = _Sentinel()


class suppress_cancelled:
    """Tiny ``contextlib.suppress``-style helper for :class:`asyncio.CancelledError`."""

    def __enter__(self) -> suppress_cancelled:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        del exc, tb
        return exc_type is not None and issubclass(exc_type, asyncio.CancelledError)

    async def __aenter__(self) -> suppress_cancelled:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        del exc, tb
        return exc_type is not None and issubclass(exc_type, asyncio.CancelledError)


__all__ = [
    "BatchAnalyzer",
    "CaptureFactory",
    "Modo",
    "VozEscolhida",
    "get_batch_atual",
    "get_contexto_sessao",
    "iniciar_sessao",
    "pausar_sessao",
    "reset_overrides",
    "set_batch_analyzer",
    "set_capture_factory",
]
