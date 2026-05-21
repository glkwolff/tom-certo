"""Audio-pipeline fallback: yt-dlp → ffmpeg → demucs → CREPE / Basic Pitch.

This is the *last* layer of the graceful-fallback chain. It is the slow and
hardware-sensitive path — it spawns subprocesses to download audio, normalize
it, separate the stems, and run an offline pitch tracker on the vocals.
It always emits a gabarito with ``qualidade.nivel = "baixa"`` so the Claude
in SKILL.md hedges its feedback accordingly.

The pipeline is wired around two seams so the tests never need the real
binaries on disk:

* **subprocess_runner** — an injectable protocol that wraps
  ``asyncio.create_subprocess_exec``. Tests pass a fake that writes the
  expected output files into the cache directory and returns immediately.
* **pitch tracker imports** — ``crepe`` and ``basic_pitch`` are imported lazily
  inside the methods that need them, raising
  :class:`MissingAudioDependencyError` with a clear remediation message when
  the extras are not installed.
"""

from __future__ import annotations

import asyncio
import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from auladcanto.domain.gabarito import (
    AcordeViolao,
    Gabarito,
    GabaritoBuilder,
    NotaSeries,
    QualidadeGabarito,
)

VozPitchEngine = Literal["crepe", "basic-pitch"]
InstrumentoPitchEngine = Literal["basic-pitch", "crepe"]


class MissingAudioDependencyError(RuntimeError):
    """Raised when an optional audio dependency (crepe, basic_pitch, …) is missing."""


@dataclass(frozen=True)
class AudioPipelineConfig:
    """Configurable knobs for the audio fallback pipeline.

    Defaults mirror plan decisions D3 (htdemucs_6s) and D4 (CREPE for voice,
    Basic Pitch for instruments).
    """

    yt_dlp_path: str = "yt-dlp"
    ffmpeg_path: str = "ffmpeg"
    demucs_path: str = "demucs"
    demucs_model: str = "htdemucs_6s"
    voz_pitch_engine: VozPitchEngine = "crepe"
    instrumento_pitch_engine: InstrumentoPitchEngine = "basic-pitch"
    yt_dlp_search_count: int = 3
    target_sample_rate: int = 44100


@dataclass(frozen=True)
class SeparatedStems:
    """Filesystem layout of the demucs output the pipeline cares about."""

    vocals_path: Path
    guitar_path: Path
    other_path: Path


@dataclass(frozen=True)
class SubprocessResult:
    """Minimal subprocess outcome the pipeline inspects."""

    returncode: int
    stdout: bytes
    stderr: bytes


class SubprocessRunner(Protocol):
    """Adapter contract for spawning subprocesses.

    Implementations must run ``argv`` and return a :class:`SubprocessResult`.
    They must not raise on non-zero exit codes — the caller decides what
    counts as fatal.
    """

    async def run(self, argv: list[str], *, cwd: Path | None = None) -> SubprocessResult: ...


class AsyncSubprocessRunner:
    """Default subprocess runner backed by ``asyncio.create_subprocess_exec``."""

    async def run(self, argv: list[str], *, cwd: Path | None = None) -> SubprocessResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd is not None else None,
        )
        stdout, stderr = await process.communicate()
        return SubprocessResult(
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
        )


class AudioPipeline:
    """yt-dlp → ffmpeg → demucs → pitch-tracker → Gabarito.

    Each step writes to a per-song subdirectory under ``cache_root`` so the
    intermediates remain inspectable. The pipeline never raises for routine
    user-facing errors — it surfaces them through alerts on the produced
    gabarito's quality envelope.
    """

    SOURCE_TAG = "demucs+crepe"

    def __init__(
        self,
        config: AudioPipelineConfig,
        cache_root: Path,
        subprocess_runner: SubprocessRunner | None = None,
    ) -> None:
        self._config = config
        self._cache_root = cache_root
        self._runner = subprocess_runner or AsyncSubprocessRunner()

    async def preparar(self, titulo: str, artista: str) -> Gabarito:
        song_dir = self._cache_root / _song_hash(titulo, artista)
        song_dir.mkdir(parents=True, exist_ok=True)

        raw_audio = await self._search_and_download(titulo, artista, song_dir)
        normalized = await self._normalize(raw_audio, song_dir)
        stems = await self._separate_stems(normalized, song_dir)
        voz_series = await self._voz_para_notas(stems.vocals_path)
        acordes = await self._violao_para_acordes(stems.guitar_path)

        builder = GabaritoBuilder(
            musica=titulo,
            artista=artista,
            tom_original="C",
            bpm=100.0,
            qualidade=QualidadeGabarito(
                nivel="baixa",
                fontes=[self._fonte_tag()],
                alertas=["audio pipeline used, accuracy limited"],
            ),
        )

        if len(voz_series) > 0:
            inicio = float(voz_series.tempos_s[0])
            fim = float(voz_series.tempos_s[-1])
            if fim <= inicio:
                fim = inicio + 0.05
            builder.add_solo(inicio_s=inicio, fim_s=fim, voz=voz_series)

        for chord in acordes:
            builder.add_acorde(tempo_s=chord.tempo_s, acorde=chord.acorde)

        return builder.build()

    async def _search_and_download(self, titulo: str, artista: str, song_dir: Path) -> Path:
        output_template = str(song_dir / "raw.%(ext)s")
        query = f"ytsearch{self._config.yt_dlp_search_count}:{artista} {titulo}"
        argv = [
            self._config.yt_dlp_path,
            "-x",
            "--audio-format",
            "wav",
            "-o",
            output_template,
            "--no-playlist",
            "--quiet",
            query,
        ]
        result = await self._runner.run(argv, cwd=song_dir)
        if result.returncode != 0:
            raise AudioPipelineError(
                "yt-dlp failed", argv=argv, stderr=result.stderr.decode("utf-8", errors="replace")
            )
        return song_dir / "raw.wav"

    async def _normalize(self, audio_path: Path, song_dir: Path) -> Path:
        output = song_dir / "normalized.wav"
        argv = [
            self._config.ffmpeg_path,
            "-y",
            "-i",
            str(audio_path),
            "-ar",
            str(self._config.target_sample_rate),
            "-ac",
            "1",
            "-af",
            "loudnorm",
            str(output),
        ]
        result = await self._runner.run(argv)
        if result.returncode != 0:
            raise AudioPipelineError(
                "ffmpeg failed", argv=argv, stderr=result.stderr.decode("utf-8", errors="replace")
            )
        return output

    async def _separate_stems(self, audio_path: Path, song_dir: Path) -> SeparatedStems:
        stems_dir = song_dir / "stems"
        stems_dir.mkdir(parents=True, exist_ok=True)
        argv = [
            self._config.demucs_path,
            "-n",
            self._config.demucs_model,
            "-o",
            str(stems_dir),
            str(audio_path),
        ]
        result = await self._runner.run(argv)
        if result.returncode != 0:
            raise AudioPipelineError(
                "demucs failed", argv=argv, stderr=result.stderr.decode("utf-8", errors="replace")
            )
        return SeparatedStems(
            vocals_path=stems_dir / "vocals.wav",
            guitar_path=stems_dir / "guitar.wav",
            other_path=stems_dir / "other.wav",
        )

    async def _voz_para_notas(self, vocals_path: Path) -> NotaSeries:
        if not vocals_path.exists():
            return NotaSeries(pitches_hz=[], tempos_s=[])
        if self._config.voz_pitch_engine == "crepe":
            return _run_crepe(vocals_path)
        return _run_basic_pitch_as_pitch_series(vocals_path)

    async def _violao_para_acordes(self, guitar_path: Path) -> list[AcordeViolao]:
        # TODO(phase-2A-followup): infer chord changes from the separated
        # guitar stem. For the MVP the audio path yields no chord chart —
        # the chord-aware fallback comes from cifra_search.
        if not guitar_path.exists():
            return []
        return []

    def _fonte_tag(self) -> str:
        if self._config.voz_pitch_engine == "crepe":
            return "demucs+crepe"
        return "demucs+basic-pitch"


class AudioPipelineError(RuntimeError):
    """Raised when an external subprocess invocation fails fatally."""

    def __init__(self, message: str, *, argv: list[str], stderr: str) -> None:
        super().__init__(f"{message}: {shlex.join(argv)}\nstderr:\n{stderr}")
        self.argv = argv
        self.stderr = stderr


def _song_hash(titulo: str, artista: str) -> str:
    key = f"{artista.strip().lower()}::{titulo.strip().lower()}".encode()
    return hashlib.sha1(key, usedforsecurity=False).hexdigest()[:12]


def _run_crepe(vocals_path: Path) -> NotaSeries:
    try:
        import crepe  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        raise MissingAudioDependencyError(
            "CREPE is required for the audio pipeline; install with: pip install -e .[audio]"
        ) from exc
    raise MissingAudioDependencyError(
        "CREPE adapter not implemented in MVP scaffold; install full audio extras and "
        "wire the call in a follow-up batch"
    )


def _run_basic_pitch_as_pitch_series(vocals_path: Path) -> NotaSeries:
    try:
        import basic_pitch  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        raise MissingAudioDependencyError(
            "basic-pitch is required for the audio pipeline; install with: pip install -e .[audio]"
        ) from exc
    raise MissingAudioDependencyError(
        "basic-pitch adapter not implemented in MVP scaffold; install full audio extras "
        "and wire the call in a follow-up batch"
    )


__all__ = [
    "AsyncSubprocessRunner",
    "AudioPipeline",
    "AudioPipelineConfig",
    "AudioPipelineError",
    "InstrumentoPitchEngine",
    "MissingAudioDependencyError",
    "SeparatedStems",
    "SubprocessResult",
    "SubprocessRunner",
    "VozPitchEngine",
]
