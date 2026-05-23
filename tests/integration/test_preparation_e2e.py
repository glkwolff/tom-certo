"""End-to-end integration test for the preparation pipeline.

Wires a real :class:`GabaritoOrchestrator` against a mocked MIDI search that
yields a realistic :class:`Gabarito` (one solo + one duo). Asserts the result
survives JSON round-trip and inherits the high-confidence quality envelope from
the MIDI layer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from auladcanto.domain.gabarito import (
    Gabarito,
    GabaritoBuilder,
    NotaSeries,
    QualidadeGabarito,
)
from auladcanto.domain.preparation.orchestrator import (
    GabaritoOrchestrator,
    PreparacaoRequest,
)
from auladcanto.domain.preparation.quality import QualityEvaluator


def _make_series(n: int) -> NotaSeries:
    return NotaSeries(
        pitches_hz=[440.0 + i * 2.0 for i in range(n)],
        tempos_s=[float(i) * 0.1 for i in range(n)],
    )


@pytest.mark.integration
async def test_orchestrator_e2e_midi_hit_roundtrips_and_preserves_alta() -> None:
    """A MIDI hit propagates through the orchestrator + evaluator unchanged.

    Asserts:
    - The orchestrator returns the gabarito built by the mocked MIDI layer.
    - The resulting gabarito round-trips through JSON without loss.
    - Quality stays at ``alta`` because the trechos are non-empty, BPM is
      sensible, and the source tag isn't an audio-pipeline marker.
    """
    seeded_gabarito = (
        GabaritoBuilder(
            musica="Faz Parte",
            artista="Bruno e Marrone",
            tom_original="G",
            bpm=96.0,
            qualidade=QualidadeGabarito(nivel="alta", fontes=["bitmidi"]),
        )
        .add_solo(inicio_s=0.0, fim_s=5.0, voz=_make_series(8))
        .add_duo(
            inicio_s=5.0,
            fim_s=15.0,
            voz_aguda=_make_series(10),
            voz_grave=_make_series(10),
            intervalo_semitons=4,
        )
        .add_acorde(tempo_s=0.0, acorde="G")
        .add_acorde(tempo_s=2.0, acorde="Em7")
        .build()
    )

    midi_search = AsyncMock()
    midi_search.buscar = AsyncMock(return_value=seeded_gabarito)
    cifra_search = AsyncMock()
    cifra_search.buscar = AsyncMock(return_value=None)
    audio_pipeline = AsyncMock()
    audio_pipeline.preparar = AsyncMock(return_value=None)

    orchestrator = GabaritoOrchestrator(
        midi_search=midi_search,
        cifra_search=cifra_search,
        audio_pipeline=audio_pipeline,
        quality_evaluator=QualityEvaluator(),
    )

    result = await orchestrator.preparar(
        PreparacaoRequest(titulo="Faz Parte", artista="Bruno e Marrone")
    )

    # Quality stays high (MIDI source, non-empty trechos, sensible BPM).
    assert result.qualidade_gabarito.nivel == "alta"

    # However the duo alert is appended because the music mixes solo + duo.
    duo_alerts = [a for a in result.qualidade_gabarito.alertas if "duo vocal" in a]
    assert len(duo_alerts) == 1

    # Roundtrip through JSON yields an equal Gabarito.
    encoded = result.model_dump_json()
    decoded = Gabarito.model_validate_json(encoded)
    assert decoded == result

    # Cifra and audio layers must remain untouched because MIDI hit first.
    cifra_search.buscar.assert_not_awaited()
    audio_pipeline.preparar.assert_not_awaited()
