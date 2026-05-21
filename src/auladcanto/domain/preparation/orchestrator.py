"""Top-level orchestrator for the graceful-fallback gabarito pipeline.

Implements decision **D14** from the plan: each request walks the three
fallback layers in order and returns the first hit, attaching a quality
envelope that downstream Claude can use to hedge its feedback.

The orchestrator is intentionally minimal — it is pure composition. All
adapters (MIDI search, cifra search, audio pipeline, quality evaluator) are
injected via the constructor so the unit tests can substitute mocks for
every external dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from auladcanto.domain.gabarito import Gabarito


class GabaritoNaoEncontrado(Exception):
    """Raised when every fallback layer misses for the requested song."""

    def __init__(self, titulo: str, artista: str) -> None:
        super().__init__(
            f"GabaritoNaoEncontrado: no gabarito could be produced for "
            f"'{titulo}' by '{artista}' across MIDI, cifra and audio layers"
        )
        self.titulo = titulo
        self.artista = artista


@dataclass(frozen=True)
class PreparacaoRequest:
    """One song-preparation request handed to :meth:`GabaritoOrchestrator.preparar`.

    ``forcar_audio_pipeline`` short-circuits the MIDI/cifra lookups and goes
    straight to the audio pipeline — useful for power users who know the
    online sources will mis-identify the song or who want a fresh
    re-analysis.
    """

    titulo: str
    artista: str
    forcar_audio_pipeline: bool = False


class _MidiSearchLike(Protocol):
    async def buscar(self, titulo: str, artista: str) -> Gabarito | None: ...


class _CifraSearchLike(Protocol):
    async def buscar(self, titulo: str, artista: str) -> Gabarito | None: ...


class _AudioPipelineLike(Protocol):
    async def preparar(self, titulo: str, artista: str) -> Gabarito: ...


class _QualityEvaluatorLike(Protocol):
    def avaliar(self, gabarito: Gabarito) -> Gabarito: ...


class GabaritoOrchestrator:
    """Composes the three fallback layers + the quality evaluator.

    Layer responsibilities:

    1. :class:`MidiSearch` — public MIDI databases → ``qualidade.nivel = "alta"``.
    2. :class:`CifraSearch` — Cifra Club + Musixmatch → ``qualidade.nivel = "media"``.
    3. :class:`AudioPipeline` — yt-dlp + demucs + CREPE → ``qualidade.nivel = "baixa"``.

    The :class:`QualityEvaluator` runs after each successful layer and may
    downgrade ``nivel`` or attach alerts based on structural heuristics
    (empty trechos, duos, anomalous BPM, etc.).
    """

    def __init__(
        self,
        midi_search: _MidiSearchLike,
        cifra_search: _CifraSearchLike,
        audio_pipeline: _AudioPipelineLike,
        quality_evaluator: _QualityEvaluatorLike,
    ) -> None:
        self._midi_search = midi_search
        self._cifra_search = cifra_search
        self._audio_pipeline = audio_pipeline
        self._quality_evaluator = quality_evaluator

    async def preparar(self, request: PreparacaoRequest) -> Gabarito:
        if not request.forcar_audio_pipeline:
            midi_hit = await self._try_midi(request)
            if midi_hit is not None:
                return self._quality_evaluator.avaliar(midi_hit)

            cifra_hit = await self._try_cifra(request)
            if cifra_hit is not None:
                return self._quality_evaluator.avaliar(cifra_hit)

        audio_hit = await self._try_audio(request)
        if audio_hit is not None:
            return self._quality_evaluator.avaliar(audio_hit)

        raise GabaritoNaoEncontrado(request.titulo, request.artista)

    async def _try_midi(self, request: PreparacaoRequest) -> Gabarito | None:
        try:
            return await self._midi_search.buscar(request.titulo, request.artista)
        except Exception:
            return None

    async def _try_cifra(self, request: PreparacaoRequest) -> Gabarito | None:
        try:
            return await self._cifra_search.buscar(request.titulo, request.artista)
        except Exception:
            return None

    async def _try_audio(self, request: PreparacaoRequest) -> Gabarito | None:
        try:
            return await self._audio_pipeline.preparar(request.titulo, request.artista)
        except Exception:
            return None


__all__ = [
    "GabaritoNaoEncontrado",
    "GabaritoOrchestrator",
    "PreparacaoRequest",
]
