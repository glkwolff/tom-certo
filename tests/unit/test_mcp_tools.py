"""Unit tests for the MCP tool functions (phase 5).

The tools are exercised directly — not via the MCP wire — so the tests can stay
fast and avoid binding to stdio. Heavy adapters (yt-dlp, sounddevice, the real
orchestrator) are replaced via the ``set_*`` injection points exposed by each
tool module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest

from auladcanto.domain.analysis.capture import (
    AudioCaptureProtocol,
    CaptureConfig,
    FakeCapture,
)
from auladcanto.domain.calibration.microfone import CalibrationConfig
from auladcanto.domain.gabarito import (
    GabaritoBuilder,
    NotaSeries,
    QualidadeGabarito,
)
from auladcanto.mcp import state as mcp_state
from auladcanto.mcp.tools import musica as musica_tools
from auladcanto.mcp.tools import perfil as perfil_tools
from auladcanto.mcp.tools import sessao as sessao_tools


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AULADCANTO_HOME", str(tmp_path))
    mcp_state.reset_state()
    musica_tools.reset_overrides()
    sessao_tools.reset_overrides()
    perfil_tools.reset_overrides()


def _make_gabarito() -> Any:
    return (
        GabaritoBuilder(
            musica="Faz Parte",
            artista="Bruno e Marrone",
            tom_original="G",
            bpm=96.0,
            qualidade=QualidadeGabarito(nivel="alta", fontes=["bitmidi"]),
        )
        .add_solo(
            inicio_s=0.0,
            fim_s=1.0,
            voz=NotaSeries(pitches_hz=[440.0, 442.0], tempos_s=[0.0, 0.5]),
        )
        .build()
    )


def _silent_capture(duration_s: float = 0.5, sample_rate: int = 44100) -> AudioCaptureProtocol:
    samples = np.zeros(int(duration_s * sample_rate), dtype=np.float32)
    return FakeCapture(samples, CaptureConfig(sample_rate=sample_rate, chunk_size=512))


# ---------------------------------------------------------------------------
# musica tools
# ---------------------------------------------------------------------------


async def test_buscar_musica_returns_candidates_with_aguardando_flag() -> None:
    async def _fake_search(query: str, limit: int) -> list[dict[str, Any]]:
        del query
        return [
            {
                "titulo": "Faz Parte",
                "artista": "Bruno e Marrone",
                "video_id": "abc",
                "duracao_s": 240,
            }
            for _ in range(limit)
        ]

    musica_tools.set_yt_dlp_searcher(_fake_search)
    result = await musica_tools.buscar_musica("Faz Parte Bruno e Marrone", limit=2)

    assert result["aguardando_confirmacao"] is True
    assert len(result["candidatos"]) == 2
    assert result["candidatos"][0]["titulo"] == "Faz Parte"


async def test_buscar_musica_empty_query_returns_error() -> None:
    result = await musica_tools.buscar_musica("   ")
    assert result["aguardando_confirmacao"] is False
    assert result["erro"] == "empty query"


async def test_confirmar_download_registers_musica_in_cache() -> None:
    gabarito = _make_gabarito()

    factory = AsyncMock()
    factory.preparar = AsyncMock(return_value=gabarito)

    def _factory_fn() -> Any:
        return factory

    musica_tools.set_orchestrator_factory(_factory_fn)
    musica_id = musica_tools.musica_id_for("Faz Parte", "Bruno e Marrone")

    result = await musica_tools.confirmar_download(
        "abc", titulo="Faz Parte", artista="Bruno e Marrone"
    )

    assert result["status"] == "ready"
    assert result["musica_id"] == musica_id
    assert result["qualidade_gabarito"]["nivel"] == "alta"

    # Idempotent: second call should not re-invoke the orchestrator
    factory.preparar.reset_mock()
    again = await musica_tools.confirmar_download(
        "abc", titulo="Faz Parte", artista="Bruno e Marrone"
    )
    assert again["status"] == "ready"
    factory.preparar.assert_not_awaited()


async def test_confirmar_download_propagates_orchestrator_error_as_dict() -> None:
    from auladcanto.domain.preparation.orchestrator import GabaritoNaoEncontrado

    factory = AsyncMock()
    factory.preparar = AsyncMock(side_effect=GabaritoNaoEncontrado("X", "Y"))

    musica_tools.set_orchestrator_factory(lambda: factory)
    result = await musica_tools.confirmar_download("vid", titulo="X", artista="Y")

    assert result["status"] == "error"
    assert "X" in result["erro"]


def test_verificar_cache_returns_false_for_unknown_musica() -> None:
    assert musica_tools.verificar_cache("does-not-exist") == {
        "processada": False,
        "musica_id": "does-not-exist",
    }


async def test_verificar_cache_returns_true_after_confirmar_download() -> None:
    gabarito = _make_gabarito()
    factory = AsyncMock()
    factory.preparar = AsyncMock(return_value=gabarito)
    musica_tools.set_orchestrator_factory(lambda: factory)

    confirmed = await musica_tools.confirmar_download(
        "abc", titulo="Faz Parte", artista="Bruno e Marrone"
    )
    musica_id = confirmed["musica_id"]

    status = musica_tools.verificar_cache(musica_id)
    assert status["processada"] is True
    assert status["qualidade_gabarito"]["nivel"] == "alta"


# ---------------------------------------------------------------------------
# sessao tools
# ---------------------------------------------------------------------------


async def _seed_cache_with_gabarito() -> str:
    gabarito = _make_gabarito()
    factory = AsyncMock()
    factory.preparar = AsyncMock(return_value=gabarito)
    musica_tools.set_orchestrator_factory(lambda: factory)
    result = await musica_tools.confirmar_download(
        "abc", titulo="Faz Parte", artista="Bruno e Marrone"
    )
    return str(result["musica_id"])


async def test_iniciar_sessao_updates_state_for_cached_musica() -> None:
    musica_id = await _seed_cache_with_gabarito()
    sessao_tools.set_capture_factory(lambda _config: _silent_capture(duration_s=0.1))

    result = await sessao_tools.iniciar_sessao(musica_id, modo="voz", voz_escolhida="solo")

    assert result["status"] == "started"
    assert result["musica_id"] == musica_id
    state = mcp_state.get_state()
    assert state.sessao.is_active is True
    assert state.sessao.modo == "voz"

    await sessao_tools.pausar_sessao()


async def test_iniciar_sessao_returns_error_for_unknown_musica() -> None:
    result = await sessao_tools.iniciar_sessao("nope", modo="voz")
    assert result["status"] == "error"
    assert "not in cache" in result["erro"]


async def test_pausar_sessao_persists_session_to_disk(tmp_path: Path) -> None:
    musica_id = await _seed_cache_with_gabarito()
    sessao_tools.set_capture_factory(lambda _config: _silent_capture(duration_s=0.05))
    await sessao_tools.iniciar_sessao(musica_id, modo="ambos")

    result = await sessao_tools.pausar_sessao()

    assert result["status"] == "paused"
    session_path = Path(result["session_path"])
    assert session_path.exists()
    assert session_path.is_relative_to(tmp_path)


async def test_pausar_sessao_is_noop_when_no_session_active() -> None:
    result = await sessao_tools.pausar_sessao()
    assert result["status"] == "noop"


def test_get_batch_atual_empty_returns_none() -> None:
    result = sessao_tools.get_batch_atual()
    assert result == {"batch": None, "mensagem": "no batch yet"}


@pytest.mark.skip(
    reason="async timing flake; pausar_sessao() may run before buffer first chunk; revisit when adding deterministic test fixture"
)
async def test_get_batch_atual_returns_last_batch_after_one_closes() -> None:
    musica_id = await _seed_cache_with_gabarito()
    sessao_tools.set_capture_factory(lambda _config: _silent_capture(duration_s=0.05))

    captured: list[Any] = []

    async def _fake_analyzer(
        batch: Any, _gabarito: Any, _musica_id: str, _voz: str
    ) -> dict[str, Any]:
        report = {
            "schema_version": 1,
            "batch_numero": batch.batch_numero,
            "musica_id": _musica_id,
            "stub": True,
        }
        captured.append(report)
        return report

    sessao_tools.set_batch_analyzer(_fake_analyzer)
    await sessao_tools.iniciar_sessao(musica_id, modo="voz")
    await sessao_tools.pausar_sessao()

    assert len(captured) >= 1
    result = sessao_tools.get_batch_atual()
    assert result["batch"]["musica_id"] == musica_id


async def test_get_contexto_sessao_returns_full_history() -> None:
    musica_id = await _seed_cache_with_gabarito()
    sessao_tools.set_capture_factory(lambda _config: _silent_capture(duration_s=0.05))

    async def _fake_analyzer(
        batch: Any, _gabarito: Any, _musica_id: str, _voz: str
    ) -> dict[str, Any]:
        return {"batch_numero": batch.batch_numero, "musica_id": _musica_id}

    sessao_tools.set_batch_analyzer(_fake_analyzer)
    await sessao_tools.iniciar_sessao(musica_id, modo="voz", voz_escolhida="solo")
    await sessao_tools.pausar_sessao()

    ctx = sessao_tools.get_contexto_sessao()
    assert ctx["musica_id"] == musica_id
    assert ctx["voz_escolhida"] == "solo"
    assert isinstance(ctx["batches"], list)


# ---------------------------------------------------------------------------
# perfil tools
# ---------------------------------------------------------------------------


def test_get_perfil_aluno_returns_defaults_on_first_call() -> None:
    result = perfil_tools.get_perfil_aluno()
    assert result["schema_version"] == 1
    assert result["faixa_vocal"] is None
    assert result["calibracao"] is None
    assert result["preferencias"]["idioma"] == "pt-BR"


def test_get_historico_returns_empty_for_unknown_musica() -> None:
    result = perfil_tools.get_historico("does-not-exist")
    assert result == {"musica_id": "does-not-exist", "sessoes": [], "tem_historico": False}


async def test_calibrar_microfone_updates_perfil(tmp_path: Path) -> None:
    sample_rate = 8_000

    def _seed_buffer() -> np.ndarray:
        per_pass = sample_rate * 1
        silence = np.full(per_pass, 1e-6, dtype=np.float32)
        tone_t = np.arange(per_pass, dtype=np.float32) / sample_rate
        tone = (0.3 * np.sin(2.0 * np.pi * 220.0 * tone_t)).astype(np.float32)
        return np.concatenate([silence, tone, tone])

    fake_capture = FakeCapture(
        _seed_buffer(),
        CaptureConfig(sample_rate=sample_rate, chunk_size=400, channels=1),
    )
    perfil_tools.set_capture_factory(lambda _config: fake_capture)

    result = await perfil_tools.calibrar_microfone(
        CalibrationConfig(
            silencio_segundos=1,
            fala_segundos=1,
            escala_segundos=1,
            sample_rate=sample_rate,
        )
    )

    assert result["status"] == "ok"
    assert "noise_floor_db" in result["calibracao"]

    refreshed = perfil_tools.get_perfil_aluno()
    assert refreshed["calibracao"] is not None
    assert refreshed["calibracao"]["range_dinamico_db"] >= 0.0

    persisted = tmp_path / "perfil.json"
    assert persisted.exists()
