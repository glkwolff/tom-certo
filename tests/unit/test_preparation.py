"""Unit tests for the phase-2A preparation pipeline.

Covers:
* :class:`GabaritoOrchestrator` — fallback chain selection and miss-all error.
* :class:`MidiSearch` — ordered source walking and first-non-None semantics.
* :class:`BitMidiSource` — search/download flow via ``httpx.MockTransport``.
* :class:`CifraClubSource` and :class:`CifraSearch` — HTML parsing + facade.
* :class:`AudioPipeline` — subprocess seam wiring with a fake runner.
* :class:`QualityEvaluator` — per-rule unit tests for each heuristic.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pretty_midi
import pytest

from auladcanto.domain.gabarito import (
    AcordeViolao,
    Gabarito,
    GabaritoBuilder,
    LetraLinha,
    NotaSeries,
    QualidadeGabarito,
)
from auladcanto.domain.preparation.audio_pipeline import (
    AudioPipeline,
    AudioPipelineConfig,
    SubprocessResult,
)
from auladcanto.domain.preparation.cifra_search import (
    CifraClubSource,
    CifraSearch,
)
from auladcanto.domain.preparation.midi_search import (
    BitMidiSource,
    MidiSearch,
)
from auladcanto.domain.preparation.orchestrator import (
    GabaritoNaoEncontrado,
    GabaritoOrchestrator,
    PreparacaoRequest,
)
from auladcanto.domain.preparation.quality import (
    QualityEvaluator,
    QualityThresholds,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_series(n: int) -> NotaSeries:
    return NotaSeries(
        pitches_hz=[440.0 + i for i in range(n)],
        tempos_s=[float(i) * 0.1 for i in range(n)],
    )


def _make_gabarito(
    *,
    nivel: str = "alta",
    fontes: list[str] | None = None,
    alertas: list[str] | None = None,
    bpm: float = 120.0,
    with_solo: bool = True,
) -> Gabarito:
    builder = GabaritoBuilder(
        musica="X",
        artista="Y",
        tom_original="C",
        bpm=bpm,
        qualidade=QualidadeGabarito(
            nivel=nivel,  # type: ignore[arg-type]
            fontes=fontes or ["bitmidi"],
            alertas=alertas or [],
        ),
    )
    if with_solo:
        builder.add_solo(inicio_s=0.0, fim_s=1.0, voz=_make_series(3))
    return builder.build()


def _make_midi_bytes() -> bytes:
    """Build a minimal valid MIDI binary in-memory."""
    midi = pretty_midi.PrettyMIDI()
    instrument = pretty_midi.Instrument(program=0)
    instrument.notes.append(pretty_midi.Note(velocity=100, pitch=60, start=0.0, end=0.5))
    instrument.notes.append(pretty_midi.Note(velocity=100, pitch=62, start=0.6, end=1.1))
    midi.instruments.append(instrument)
    buf = io.BytesIO()
    midi.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# GabaritoOrchestrator.preparar
# ---------------------------------------------------------------------------


async def test_orchestrator_returns_midi_hit_when_first_layer_succeeds() -> None:
    """When MIDI layer hits, cifra/audio layers must not be called."""
    expected = _make_gabarito()
    midi = AsyncMock()
    midi.buscar = AsyncMock(return_value=expected)
    cifra = AsyncMock()
    cifra.buscar = AsyncMock(return_value=None)
    audio = AsyncMock()
    audio.preparar = AsyncMock(return_value=None)
    evaluator = AsyncMock()
    evaluator.avaliar = lambda g: g  # type: ignore[assignment]

    orch = GabaritoOrchestrator(midi, cifra, audio, evaluator)
    result = await orch.preparar(PreparacaoRequest(titulo="X", artista="Y"))

    assert result is expected
    midi.buscar.assert_awaited_once_with("X", "Y")
    cifra.buscar.assert_not_awaited()
    audio.preparar.assert_not_awaited()


async def test_orchestrator_falls_through_to_cifra_when_midi_misses() -> None:
    """MIDI miss leads to cifra hit, audio layer untouched."""
    expected = _make_gabarito(nivel="media", fontes=["cifraclub"])
    midi = AsyncMock()
    midi.buscar = AsyncMock(return_value=None)
    cifra = AsyncMock()
    cifra.buscar = AsyncMock(return_value=expected)
    audio = AsyncMock()
    audio.preparar = AsyncMock(return_value=None)
    evaluator = AsyncMock()
    evaluator.avaliar = lambda g: g  # type: ignore[assignment]

    orch = GabaritoOrchestrator(midi, cifra, audio, evaluator)
    result = await orch.preparar(PreparacaoRequest(titulo="X", artista="Y"))

    assert result is expected
    cifra.buscar.assert_awaited_once_with("X", "Y")
    audio.preparar.assert_not_awaited()


async def test_orchestrator_falls_through_to_audio_when_midi_and_cifra_miss() -> None:
    """Both MIDI and cifra layers miss → audio fallback is invoked."""
    expected = _make_gabarito(nivel="baixa", fontes=["demucs+crepe"])
    midi = AsyncMock()
    midi.buscar = AsyncMock(return_value=None)
    cifra = AsyncMock()
    cifra.buscar = AsyncMock(return_value=None)
    audio = AsyncMock()
    audio.preparar = AsyncMock(return_value=expected)
    evaluator = AsyncMock()
    evaluator.avaliar = lambda g: g  # type: ignore[assignment]

    orch = GabaritoOrchestrator(midi, cifra, audio, evaluator)
    result = await orch.preparar(PreparacaoRequest(titulo="X", artista="Y"))

    assert result is expected
    audio.preparar.assert_awaited_once_with("X", "Y")


async def test_orchestrator_raises_gabarito_nao_encontrado_when_everything_misses() -> None:
    """If every layer misses (audio returns None too), raise GabaritoNaoEncontrado."""
    midi = AsyncMock()
    midi.buscar = AsyncMock(return_value=None)
    cifra = AsyncMock()
    cifra.buscar = AsyncMock(return_value=None)
    audio = AsyncMock()
    # _try_audio swallows exceptions and returns None
    audio.preparar = AsyncMock(side_effect=RuntimeError("boom"))
    evaluator = AsyncMock()
    evaluator.avaliar = lambda g: g  # type: ignore[assignment]

    orch = GabaritoOrchestrator(midi, cifra, audio, evaluator)
    with pytest.raises(GabaritoNaoEncontrado) as exc:
        await orch.preparar(PreparacaoRequest(titulo="Foo", artista="Bar"))

    assert exc.value.titulo == "Foo"
    assert exc.value.artista == "Bar"


# ---------------------------------------------------------------------------
# MidiSearch
# ---------------------------------------------------------------------------


async def test_midi_search_returns_first_non_none_source() -> None:
    """Walks sources in order; first to return a Gabarito wins."""
    expected = _make_gabarito()

    source_a = AsyncMock()
    source_a.buscar = AsyncMock(return_value=None)
    source_b = AsyncMock()
    source_b.buscar = AsyncMock(return_value=expected)
    source_c = AsyncMock()
    source_c.buscar = AsyncMock(return_value=_make_gabarito())

    search = MidiSearch([source_a, source_b, source_c])
    result = await search.buscar("t", "a")

    assert result is expected
    source_a.buscar.assert_awaited_once()
    source_b.buscar.assert_awaited_once()
    # third source is short-circuited
    source_c.buscar.assert_not_awaited()


async def test_midi_search_with_empty_sources_returns_none() -> None:
    """Edge case: an empty source list yields None without errors."""
    assert await MidiSearch([]).buscar("t", "a") is None


async def test_midi_search_skips_sources_that_raise() -> None:
    """A source that raises NotImplementedError or any Exception is treated as miss."""
    expected = _make_gabarito()
    failing = AsyncMock()
    failing.buscar = AsyncMock(side_effect=NotImplementedError("stub"))
    crashing = AsyncMock()
    crashing.buscar = AsyncMock(side_effect=RuntimeError("network"))
    happy = AsyncMock()
    happy.buscar = AsyncMock(return_value=expected)

    search = MidiSearch([failing, crashing, happy])
    assert await search.buscar("t", "a") is expected


# ---------------------------------------------------------------------------
# BitMidiSource (httpx MockTransport)
# ---------------------------------------------------------------------------


async def test_bitmidi_source_returns_parsed_gabarito_for_valid_search() -> None:
    """A valid search payload + downloadable MIDI yields a parsed Gabarito."""
    midi_bytes = _make_midi_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/search":
            return httpx.Response(
                200,
                json={
                    "PageData": {
                        "results": [{"downloadUrl": "/download/song.mid"}],
                    }
                },
            )
        if request.url.path == "/download/song.mid":
            return httpx.Response(200, content=midi_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        source = BitMidiSource(client=client)
        result = await source.buscar("Faz Parte", "Bruno e Marrone")

    assert result is not None
    assert result.musica == "Faz Parte"
    assert result.artista == "Bruno e Marrone"
    assert result.qualidade_gabarito.nivel == "alta"
    assert "bitmidi" in result.qualidade_gabarito.fontes
    assert len(result.trechos) >= 1


async def test_bitmidi_source_returns_none_when_search_has_no_results() -> None:
    """Empty results list → no download attempt, returns None."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"PageData": {"results": []}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        source = BitMidiSource(client=client)
        assert await source.buscar("Foo", "Bar") is None


async def test_bitmidi_source_returns_none_on_http_error() -> None:
    """A non-200 response from the search endpoint yields None."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        source = BitMidiSource(client=client)
        assert await source.buscar("Foo", "Bar") is None


# ---------------------------------------------------------------------------
# CifraClubSource (httpx MockTransport)
# ---------------------------------------------------------------------------


async def test_cifra_club_source_parses_chord_tokens_from_html() -> None:
    """Lenient parser extracts ≥2 chord tokens from a minimal HTML chart."""
    html = """
    <html><body>
    <pre>
     G       Em7        C        D
    Hello world this is a chord chart
    </pre>
    <span>tom: G | bpm 96</span>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        source = CifraClubSource(client=client)
        result = await source.buscar("Faz Parte", "Bruno e Marrone")

    assert result is not None
    acordes, _letra, bpm, tom = result
    assert len(acordes) >= 2
    assert all(isinstance(a, AcordeViolao) for a in acordes)
    assert bpm == 96.0
    assert tom == "G"


async def test_cifra_club_source_returns_none_on_404() -> None:
    """Missing song page yields None — the layer simply misses."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        source = CifraClubSource(client=client)
        assert await source.buscar("Foo", "Bar") is None


# ---------------------------------------------------------------------------
# CifraSearch (facade)
# ---------------------------------------------------------------------------


async def test_cifra_search_builds_partial_gabarito_with_letra_lines() -> None:
    """Combining a chord source + a lyric source yields ≥2 LetraLinha entries."""
    acordes = [
        AcordeViolao(tempo_s=0.0, acorde="G"),
        AcordeViolao(tempo_s=2.0, acorde="Em7"),
    ]
    letra = [
        LetraLinha(tempo_s=0.5, texto="primeira linha"),
        LetraLinha(tempo_s=3.0, texto="segunda linha"),
    ]
    chord_src = AsyncMock()
    chord_src.SOURCE_TAG = "cifraclub"
    chord_src.buscar = AsyncMock(return_value=(acordes, [], 100.0, "G"))

    lyric_src = AsyncMock()
    lyric_src.SOURCE_TAG = "musixmatch"
    lyric_src.buscar = AsyncMock(return_value=([], letra, None, None))

    search = CifraSearch(chord_src, lyric_src)
    gabarito = await search.buscar("T", "A")

    assert gabarito is not None
    assert gabarito.qualidade_gabarito.nivel == "media"
    assert gabarito.qualidade_gabarito.fontes == ["cifraclub", "musixmatch"]
    assert len(gabarito.letra_timestamped) == 2
    assert len(gabarito.acordes_violao) == 2
    assert gabarito.tom_original == "G"
    assert gabarito.bpm == 100.0


async def test_cifra_search_misses_when_chord_source_returns_none() -> None:
    """No chord source hit → whole facade returns None."""
    chord_src = AsyncMock()
    chord_src.SOURCE_TAG = "cifraclub"
    chord_src.buscar = AsyncMock(return_value=None)

    search = CifraSearch(chord_src)
    assert await search.buscar("T", "A") is None


# ---------------------------------------------------------------------------
# AudioPipeline (FakeSubprocessRunner)
# ---------------------------------------------------------------------------


@dataclass
class _FakeSubprocessRunner:
    """Test double that materialises the file outputs each pipeline step expects."""

    cache_root: Path

    async def run(self, argv: list[str], *, cwd: Path | None = None) -> SubprocessResult:
        # Identify which step we're emulating by inspecting argv[0]
        tool = Path(argv[0]).name
        if tool == "yt-dlp":
            # Emit the raw.wav file the pipeline expects
            output_dir = cwd or self.cache_root
            (output_dir / "raw.wav").write_bytes(b"RIFF....fakeaudio")
        elif tool == "ffmpeg":
            # ffmpeg writes the file specified as the last positional argument
            output_path = Path(argv[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"RIFF....normalized")
        elif tool == "demucs":
            # demucs writes vocals.wav and guitar.wav under -o <dir>
            try:
                idx = argv.index("-o")
                stems_dir = Path(argv[idx + 1])
            except (ValueError, IndexError):  # pragma: no cover - defensive
                stems_dir = self.cache_root / "stems"
            stems_dir.mkdir(parents=True, exist_ok=True)
            (stems_dir / "vocals.wav").write_bytes(b"RIFF....vocals")
            (stems_dir / "guitar.wav").write_bytes(b"RIFF....guitar")
            (stems_dir / "other.wav").write_bytes(b"RIFF....other")
        return SubprocessResult(returncode=0, stdout=b"", stderr=b"")


async def test_audio_pipeline_produces_low_quality_gabarito(tmp_path: Path) -> None:
    """End-to-end pipeline with fake runners produces a baixa-quality Gabarito."""
    runner = _FakeSubprocessRunner(cache_root=tmp_path)
    pipeline = AudioPipeline(
        config=AudioPipelineConfig(),
        cache_root=tmp_path,
        subprocess_runner=runner,
    )

    # Patch the pitch tracker at its module boundary (it would otherwise raise
    # MissingAudioDependencyError because the MVP scaffold isn't wired yet).
    fake_voz = NotaSeries(pitches_hz=[440.0, 442.0], tempos_s=[0.0, 0.25])
    with patch(
        "auladcanto.domain.preparation.audio_pipeline._run_crepe",
        return_value=fake_voz,
    ):
        gabarito = await pipeline.preparar("Faz Parte", "Bruno e Marrone")

    assert gabarito.qualidade_gabarito.nivel == "baixa"
    assert "demucs+crepe" in gabarito.qualidade_gabarito.fontes
    assert any("audio pipeline" in a for a in gabarito.qualidade_gabarito.alertas)
    assert len(gabarito.trechos) == 1


# ---------------------------------------------------------------------------
# QualityEvaluator — per-rule unit tests
# ---------------------------------------------------------------------------


def test_quality_evaluator_downgrades_empty_trechos_to_baixa() -> None:
    """No trechos → nivel becomes 'baixa' and 'no melody segments' alert added."""
    gabarito = _make_gabarito(nivel="alta", fontes=["bitmidi"], with_solo=False)
    out = QualityEvaluator().avaliar(gabarito)
    assert out.qualidade_gabarito.nivel == "baixa"
    assert "no melody segments" in out.qualidade_gabarito.alertas


def test_quality_evaluator_adds_duo_alert_with_percentage() -> None:
    """Mix of solo+duo adds 'duo vocal detected in N% of music' with right pct."""
    builder = GabaritoBuilder(
        musica="X",
        artista="Y",
        tom_original="C",
        bpm=120.0,
        qualidade=QualidadeGabarito(nivel="alta", fontes=["bitmidi"]),
    )
    # 2s of solo + 8s of duo → 80% duo
    builder.add_solo(inicio_s=0.0, fim_s=2.0, voz=_make_series(3))
    builder.add_duo(
        inicio_s=2.0,
        fim_s=10.0,
        voz_aguda=_make_series(3),
        voz_grave=_make_series(3),
        intervalo_semitons=3,
    )
    gabarito = builder.build()

    out = QualityEvaluator().avaliar(gabarito)
    duo_alerts = [a for a in out.qualidade_gabarito.alertas if "duo vocal" in a]
    assert len(duo_alerts) == 1
    assert "80%" in duo_alerts[0]


def test_quality_evaluator_caps_audio_pipeline_source_to_media() -> None:
    """A gabarito tagged with 'demucs+crepe' caps at 'media' even if input says 'alta'."""
    gabarito = _make_gabarito(nivel="alta", fontes=["demucs+crepe"])
    out = QualityEvaluator().avaliar(gabarito)
    assert out.qualidade_gabarito.nivel == "media"


def test_quality_evaluator_adds_bpm_alert_when_out_of_range() -> None:
    """BPM outside [40, 240] triggers an 'unusual BPM' alert."""
    too_fast = _make_gabarito(nivel="alta", bpm=300.0)
    out = QualityEvaluator().avaliar(too_fast)
    assert any("unusual BPM" in a for a in out.qualidade_gabarito.alertas)


def test_quality_evaluator_adds_bpm_alert_when_below_floor() -> None:
    """BPM below 40 also triggers an 'unusual BPM' alert."""
    too_slow = _make_gabarito(nivel="alta", bpm=20.0)
    out = QualityEvaluator().avaliar(too_slow)
    assert any("unusual BPM" in a for a in out.qualidade_gabarito.alertas)


def test_quality_evaluator_leaves_normal_case_unchanged() -> None:
    """A well-formed alta gabarito with sensible BPM is not downgraded."""
    gabarito = _make_gabarito(nivel="alta", fontes=["bitmidi"], bpm=120.0)
    out = QualityEvaluator().avaliar(gabarito)
    assert out.qualidade_gabarito.nivel == "alta"
    assert out.qualidade_gabarito.alertas == []


def test_quality_evaluator_respects_custom_thresholds() -> None:
    """Custom thresholds widen/narrow the BPM band as configured."""
    gabarito = _make_gabarito(nivel="alta", bpm=300.0)
    evaluator = QualityEvaluator(QualityThresholds(bpm_min=10.0, bpm_max=500.0))
    out = evaluator.avaliar(gabarito)
    assert not any("unusual BPM" in a for a in out.qualidade_gabarito.alertas)
