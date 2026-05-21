"""Domain model for the persistent student profile (``~/.auladcanto/perfil.json``).

The profile carries calibration results (phase 4), vocal range, and user
preferences. It is the contract between the calibration command, the
transposition heuristic (phase 3B), and the MCP ``get_perfil_aluno`` tool
(phase 5).

The JSON file lives at the path returned by ``storage.paths.perfil_path()``.
File I/O lives on the model itself because the profile is small (≤ a few KB)
and there is no other persistence layer the data needs to flow through.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_NOTE_PATTERN = re.compile(r"^([A-G])(#|b)?(-?\d+)$")

_NOTE_TO_SEMITONE: dict[str, int] = {
    "C": 0,
    "D": 2,
    "E": 4,
    "F": 5,
    "G": 7,
    "A": 9,
    "B": 11,
}

ModoPraticaDefault = Literal["voz", "violao", "ambos"]


def _parse_note_to_midi(nota: str) -> int:
    """Return the MIDI number for a scientific-pitch notation token (e.g. ``"A4" → 69``)."""
    match = _NOTE_PATTERN.match(nota)
    if match is None:
        raise ValueError(
            f"NotaMidi: '{nota}' is not a valid note token "
            "(expected scientific pitch notation, e.g. 'A4', 'C#5', 'Eb3')"
        )
    letter, accidental, octave_str = match.groups()
    semitone = _NOTE_TO_SEMITONE[letter]
    if accidental == "#":
        semitone += 1
    elif accidental == "b":
        semitone -= 1
    octave = int(octave_str)
    return (octave + 1) * 12 + semitone


def _midi_to_hz(midi_number: int) -> float:
    """Equal-tempered conversion: MIDI 69 (A4) anchors at 440 Hz."""
    exponent: float = (midi_number - 69) / 12.0
    return float(440.0 * (2.0**exponent))


class NotaMidi(BaseModel):
    """Scientific-pitch notation token paired with its MIDI number and frequency.

    The three fields must be mutually consistent. Use the
    :meth:`from_nota` classmethod to build a coherent instance from a single
    note token.
    """

    model_config = ConfigDict(extra="forbid")

    nota: str
    midi_number: int = Field(ge=0, le=127)
    hz: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        expected_midi = _parse_note_to_midi(self.nota)
        if expected_midi != self.midi_number:
            raise ValueError(
                "NotaMidi: midi_number "
                f"{self.midi_number} does not match nota '{self.nota}' "
                f"(expected {expected_midi})"
            )
        expected_hz = _midi_to_hz(self.midi_number)
        if abs(expected_hz - self.hz) > 0.01:
            raise ValueError(
                f"NotaMidi: hz {self.hz} does not match midi_number {self.midi_number} "
                f"(expected {expected_hz:.4f})"
            )
        return self

    @classmethod
    def from_nota(cls, nota: str) -> NotaMidi:
        """Build a ``NotaMidi`` from a single scientific-pitch token."""
        midi_number = _parse_note_to_midi(nota)
        return cls(nota=nota, midi_number=midi_number, hz=_midi_to_hz(midi_number))


class FaixaVocal(BaseModel):
    """Vocal range — minimum, maximum, and the comfortable subrange."""

    model_config = ConfigDict(extra="forbid")

    minima: NotaMidi
    maxima: NotaMidi
    confortavel_min: NotaMidi | None = None
    confortavel_max: NotaMidi | None = None

    @model_validator(mode="after")
    def _check_range_bounds(self) -> Self:
        if self.maxima.midi_number < self.minima.midi_number:
            raise ValueError(
                f"FaixaVocal: maxima ({self.maxima.nota}) must be >= minima ({self.minima.nota})"
            )
        if self.confortavel_min is not None and self.confortavel_max is not None:
            if self.confortavel_max.midi_number < self.confortavel_min.midi_number:
                raise ValueError("FaixaVocal: confortavel_max must be >= confortavel_min")
            if self.confortavel_min.midi_number < self.minima.midi_number:
                raise ValueError("FaixaVocal: confortavel_min cannot be below minima")
            if self.confortavel_max.midi_number > self.maxima.midi_number:
                raise ValueError("FaixaVocal: confortavel_max cannot be above maxima")
        return self


class CalibracaoMicrofone(BaseModel):
    """Results from the one-time microphone calibration (phase 4)."""

    model_config = ConfigDict(extra="forbid")

    noise_floor_db: float
    range_dinamico_db: float = Field(ge=0.0)
    pitch_detection_acuracia_pct: float = Field(ge=0.0, le=100.0)
    latencia_aproximada_ms: int = Field(ge=0)
    data_calibracao: datetime


class PreferenciasAluno(BaseModel):
    """User-tunable knobs that affect session behaviour."""

    model_config = ConfigDict(extra="forbid")

    idioma: str = "pt-BR"
    transposicao_automatica: bool = True
    modo_pratica_default: ModoPraticaDefault = "voz"
    sample_rate: int = Field(default=44100, gt=0)
    chunk_size: int = Field(default=512, gt=0)


class PerfilAluno(BaseModel):
    """Persistent student profile stored at ``~/.auladcanto/perfil.json``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    nome: str | None = None
    criado: datetime
    faixa_vocal: FaixaVocal | None = None
    calibracao: CalibracaoMicrofone | None = None
    preferencias: PreferenciasAluno = Field(default_factory=PreferenciasAluno)

    def save(self, path: Path) -> None:
        """Atomically write the profile to ``path`` as indented JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> PerfilAluno:
        """Read and validate a profile from ``path``."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = [
    "CalibracaoMicrofone",
    "FaixaVocal",
    "ModoPraticaDefault",
    "NotaMidi",
    "PerfilAluno",
    "PreferenciasAluno",
]
