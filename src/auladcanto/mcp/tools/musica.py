"""MCP tools for music selection, download and gabarito preparation.

The tools follow the same composition pattern used elsewhere in the project:
external dependencies (yt-dlp search, orchestrator factory) are looked up via
module-level functions so tests can monkeypatch them without standing up real
binaries or network access.

The four tools exposed here are:

* :func:`buscar_musica` — surface up to N candidates for a free-text query and
  ask the user to confirm one before downloading.
* :func:`confirmar_download` — dispatch the orchestrator for a previously
  surfaced candidate and persist the resulting gabarito under
  ``cache_dir() / musica_id / gabarito.json``.
* :func:`verificar_cache` — fast lookup that tells the caller whether a given
  musica id is already prepared.
* :func:`preparar_gabarito` — convenience that does the search + confirm in one
  step when the user knows exactly what they want.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
from collections.abc import Awaitable, Callable
from typing import Any

from auladcanto.domain.preparation.audio_pipeline import (
    AsyncSubprocessRunner,
    AudioPipeline,
    AudioPipelineConfig,
)
from auladcanto.domain.preparation.cifra_search import CifraClubSource, CifraSearch
from auladcanto.domain.preparation.midi_search import BitMidiSource, MidiSearch
from auladcanto.domain.preparation.orchestrator import (
    GabaritoNaoEncontrado,
    GabaritoOrchestrator,
    PreparacaoRequest,
)
from auladcanto.domain.preparation.quality import QualityEvaluator
from auladcanto.storage.paths import cache_dir

_DEFAULT_CANDIDATE_LIMIT = 3
_GABARITO_FILENAME = "gabarito.json"

YtDlpSearcher = Callable[[str, int], Awaitable[list[dict[str, Any]]]]
OrchestratorFactory = Callable[[], GabaritoOrchestrator]


def musica_id_for(titulo: str, artista: str) -> str:
    """Stable 12-char id derived from ``(artista, titulo)`` — mirrors audio pipeline."""
    key = f"{artista.strip().lower()}::{titulo.strip().lower()}".encode()
    return hashlib.sha1(key, usedforsecurity=False).hexdigest()[:12]


async def _default_yt_dlp_search(query: str, limit: int) -> list[dict[str, Any]]:
    """Spawn ``yt-dlp`` to surface up to ``limit`` search candidates.

    Returns a list of ``{"titulo", "artista", "video_id", "duracao_s"}`` dicts.
    Tests inject :data:`_yt_dlp_searcher` to avoid spawning the binary.
    """
    argv = [
        "yt-dlp",
        f"ytsearch{limit}:{query}",
        "--dump-json",
        "--no-playlist",
        "--quiet",
    ]
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _stderr = await process.communicate()
    if process.returncode != 0:
        return []
    candidatos: list[dict[str, Any]] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        candidatos.append(_parse_yt_dlp_entry(entry))
    return candidatos


def _parse_yt_dlp_entry(entry: dict[str, Any]) -> dict[str, Any]:
    titulo = str(entry.get("title", "")).strip()
    artista = str(entry.get("uploader", "") or entry.get("channel", "")).strip()
    video_id = str(entry.get("id", "")).strip()
    duration = entry.get("duration")
    duracao_s = int(duration) if isinstance(duration, (int, float)) else 0
    return {
        "titulo": titulo,
        "artista": artista,
        "video_id": video_id,
        "duracao_s": duracao_s,
    }


def _default_orchestrator_factory() -> GabaritoOrchestrator:
    midi = MidiSearch([BitMidiSource()])
    cifra = CifraSearch(CifraClubSource())
    audio = AudioPipeline(
        config=AudioPipelineConfig(),
        cache_root=cache_dir(),
        subprocess_runner=AsyncSubprocessRunner(),
    )
    return GabaritoOrchestrator(
        midi_search=midi,
        cifra_search=cifra,
        audio_pipeline=audio,
        quality_evaluator=QualityEvaluator(),
    )


_yt_dlp_searcher: YtDlpSearcher = _default_yt_dlp_search
_orchestrator_factory: OrchestratorFactory = _default_orchestrator_factory


def set_yt_dlp_searcher(searcher: YtDlpSearcher) -> None:
    """Override the default yt-dlp searcher (used by tests)."""
    global _yt_dlp_searcher
    _yt_dlp_searcher = searcher


def set_orchestrator_factory(factory: OrchestratorFactory) -> None:
    """Override the default orchestrator factory (used by tests)."""
    global _orchestrator_factory
    _orchestrator_factory = factory


def reset_overrides() -> None:
    """Restore the production defaults (used by tests in their teardown)."""
    global _yt_dlp_searcher, _orchestrator_factory
    _yt_dlp_searcher = _default_yt_dlp_search
    _orchestrator_factory = _default_orchestrator_factory


async def buscar_musica(query: str, limit: int = _DEFAULT_CANDIDATE_LIMIT) -> dict[str, Any]:
    """Surface candidate matches for ``query`` and ask the caller to confirm."""
    if not query.strip():
        return {"candidatos": [], "aguardando_confirmacao": False, "erro": "empty query"}
    candidatos = await _yt_dlp_searcher(query.strip(), limit)
    return {
        "candidatos": candidatos[:limit],
        "aguardando_confirmacao": bool(candidatos),
    }


async def confirmar_download(
    video_id: str,
    *,
    titulo: str | None = None,
    artista: str | None = None,
) -> dict[str, Any]:
    """Prepare the gabarito for ``video_id`` and persist it under the cache dir.

    ``titulo`` and ``artista`` are optional hints: when present they feed the
    orchestrator (MIDI/cifra layers need them). When absent, the function falls
    back to using ``video_id`` for both — the audio pipeline will still work but
    the MIDI/cifra layers will likely miss.
    """
    titulo_final = (titulo or video_id).strip() or video_id
    artista_final = (artista or "").strip()
    musica_id = musica_id_for(titulo_final, artista_final or video_id)

    cached = _load_cached_gabarito(musica_id)
    if cached is not None:
        return {
            "status": "ready",
            "musica_id": musica_id,
            "qualidade_gabarito": cached["qualidade_gabarito"],
        }

    orchestrator = _orchestrator_factory()
    try:
        gabarito = await orchestrator.preparar(
            PreparacaoRequest(titulo=titulo_final, artista=artista_final or video_id)
        )
    except GabaritoNaoEncontrado as exc:
        return {
            "status": "error",
            "musica_id": musica_id,
            "erro": str(exc),
        }

    _persist_gabarito(musica_id, gabarito.model_dump(mode="json"))
    return {
        "status": "ready",
        "musica_id": musica_id,
        "qualidade_gabarito": gabarito.qualidade_gabarito.model_dump(),
    }


def verificar_cache(musica_id: str) -> dict[str, Any]:
    """Return ``{processada, musica_id, qualidade_gabarito?}`` for a candidate id."""
    cached = _load_cached_gabarito(musica_id)
    if cached is None:
        return {"processada": False, "musica_id": musica_id}
    return {
        "processada": True,
        "musica_id": musica_id,
        "qualidade_gabarito": cached["qualidade_gabarito"],
    }


async def preparar_gabarito(titulo: str, artista: str) -> dict[str, Any]:
    """Single-call convenience: build a gabarito straight from ``(titulo, artista)``."""
    if not titulo.strip() or not artista.strip():
        return {"status": "error", "erro": "titulo and artista are required"}
    musica_id = musica_id_for(titulo, artista)
    fake_video_id = shlex.quote(f"{artista}-{titulo}")
    return await confirmar_download(fake_video_id, titulo=titulo, artista=artista) | {
        "musica_id": musica_id,
    }


def _gabarito_path(musica_id: str) -> Any:
    return cache_dir() / musica_id / _GABARITO_FILENAME


def _load_cached_gabarito(musica_id: str) -> dict[str, Any] | None:
    path = _gabarito_path(musica_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _persist_gabarito(musica_id: str, payload: dict[str, Any]) -> None:
    path = _gabarito_path(musica_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


__all__ = [
    "OrchestratorFactory",
    "YtDlpSearcher",
    "buscar_musica",
    "confirmar_download",
    "musica_id_for",
    "preparar_gabarito",
    "reset_overrides",
    "set_orchestrator_factory",
    "set_yt_dlp_searcher",
    "verificar_cache",
]
