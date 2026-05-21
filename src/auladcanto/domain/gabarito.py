"""Domain models for the hybrid gabarito (reference) used to compare against user audio.

A *gabarito* describes the canonical pitches, chords, and lyrics of a song. The
model supports three trecho (passage) types so it can represent songs that mix
solo voice, vocal duos, and unisono passages — the typical shape of Brazilian
sertanejo and MPB material the project targets.

The hierarchy mirrors the JSON shape described in section 3.4 of the
implementation plan (``docs/maestro/plans/auladcanto-mcp-mvp.md``) and is the
contract consumed by the preparation pipeline (phase 2A) and the comparator
(phase 3C). The reciprocal serialization format is documented in
``docs/schema-v1.md``.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

TipoTrecho = Literal["solo", "duo", "unissono"]
NivelQualidade = Literal["alta", "media", "baixa"]

_CHORD_PATTERN = re.compile(
    r"^[A-G](?:#|b)?"  # root note
    r"(?:m|M|maj|min|dim|aug|sus|add)?\d?"  # quality
    r"(?:\d{1,2})?"  # extension (e.g. 7, 9, 11, 13)
    r"(?:[#b]\d{1,2})?"  # altered extension (e.g. b5, #9)
    r"(?:/[A-G](?:#|b)?)?"  # optional bass note (slash chord)
    r"$"
)


class NotaSeries(BaseModel):
    """Parallel arrays of pitch (Hz) and timestamp (seconds) samples.

    The two lists must have the same length: ``pitches_hz[i]`` is the
    fundamental frequency observed at ``tempos_s[i]``. A frequency of ``0.0``
    represents an unvoiced/silent frame (this matches the convention used by
    ``mir_eval.melody``).
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    pitches_hz: list[float]
    tempos_s: list[float]

    @model_validator(mode="after")
    def _check_lengths_match(self) -> Self:
        if len(self.pitches_hz) != len(self.tempos_s):
            raise ValueError(
                "NotaSeries: pitches_hz and tempos_s must have the same length "
                f"(got {len(self.pitches_hz)} and {len(self.tempos_s)})"
            )
        return self

    def __len__(self) -> int:
        return len(self.pitches_hz)


class TrechoSolo(BaseModel):
    """A passage sung by a single voice."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["solo"] = "solo"
    inicio_s: float = Field(ge=0.0)
    fim_s: float = Field(ge=0.0)
    voz: NotaSeries

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.fim_s <= self.inicio_s:
            raise ValueError(
                f"TrechoSolo: fim_s ({self.fim_s}) must be greater than inicio_s ({self.inicio_s})"
            )
        return self


class TrechoDuo(BaseModel):
    """A passage with two simultaneous voices (aguda and grave)."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["duo"] = "duo"
    inicio_s: float = Field(ge=0.0)
    fim_s: float = Field(ge=0.0)
    voz_aguda: NotaSeries
    voz_grave: NotaSeries
    intervalo_semitons: int

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.fim_s <= self.inicio_s:
            raise ValueError(
                f"TrechoDuo: fim_s ({self.fim_s}) must be greater than inicio_s ({self.inicio_s})"
            )
        return self


class TrechoUnissono(BaseModel):
    """A passage with two voices singing the same melody (unissono)."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["unissono"] = "unissono"
    inicio_s: float = Field(ge=0.0)
    fim_s: float = Field(ge=0.0)
    voz: NotaSeries

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.fim_s <= self.inicio_s:
            raise ValueError(
                "TrechoUnissono: fim_s "
                f"({self.fim_s}) must be greater than inicio_s ({self.inicio_s})"
            )
        return self


Trecho = Annotated[TrechoSolo | TrechoDuo | TrechoUnissono, Field(discriminator="tipo")]


class AcordeViolao(BaseModel):
    """A guitar chord change at a given timestamp."""

    model_config = ConfigDict(extra="forbid")

    tempo_s: float = Field(ge=0.0)
    acorde: str

    @model_validator(mode="after")
    def _check_chord_token(self) -> Self:
        if not _CHORD_PATTERN.match(self.acorde):
            raise ValueError(
                f"AcordeViolao: '{self.acorde}' is not a recognized chord token "
                "(expected e.g. 'G', 'Em7', 'C#m7b5', 'D/F#')"
            )
        return self


class LetraLinha(BaseModel):
    """A timestamped lyrics line."""

    model_config = ConfigDict(extra="forbid")

    tempo_s: float = Field(ge=0.0)
    texto: str


class QualidadeGabarito(BaseModel):
    """Quality envelope describing how trustworthy a gabarito is.

    ``nivel`` is the headline confidence; ``fontes`` lists the data sources
    used to build it (e.g. ``["bitmidi"]`` for a high-confidence MIDI find or
    ``["demucs+crepe"]`` for the audio fallback); ``alertas`` carries
    human-readable warnings the SKILL.md/Claude should surface to the user.
    """

    model_config = ConfigDict(extra="forbid")

    nivel: NivelQualidade
    fontes: list[str]
    alertas: list[str] = Field(default_factory=list)


class Gabarito(BaseModel):
    """Full reference description of a song.

    The list of ``trechos`` must be sorted by ``inicio_s`` and must not
    overlap. ``acordes_violao`` and ``letra_timestamped`` are optional and
    may be empty when the upstream pipeline could not extract them.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    musica: str
    artista: str
    tom_original: str
    bpm: float = Field(gt=0.0)
    qualidade_gabarito: QualidadeGabarito
    trechos: list[Trecho]
    acordes_violao: list[AcordeViolao] = Field(default_factory=list)
    letra_timestamped: list[LetraLinha] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_trechos_sorted_and_non_overlapping(self) -> Self:
        previous_fim: float | None = None
        previous_inicio: float | None = None
        for trecho in self.trechos:
            if previous_inicio is not None and trecho.inicio_s < previous_inicio:
                raise ValueError(
                    "Gabarito: trechos must be sorted by inicio_s "
                    f"(found {trecho.inicio_s} after {previous_inicio})"
                )
            if previous_fim is not None and trecho.inicio_s < previous_fim:
                raise ValueError(
                    "Gabarito: trechos overlap "
                    f"(trecho starting at {trecho.inicio_s} overlaps previous ending at "
                    f"{previous_fim})"
                )
            previous_inicio = trecho.inicio_s
            previous_fim = trecho.fim_s
        return self


class GabaritoBuilder:
    """Fluent builder for assembling a ``Gabarito`` in tests and fixtures.

    The builder defers validation until ``build()`` is called so callers can
    chain ``add_*`` operations in any order — the final ``Gabarito`` validator
    enforces sorting and non-overlap. The builder is intentionally lenient on
    its own inputs (no per-step validation) to keep ergonomic test setup.
    """

    def __init__(
        self,
        *,
        musica: str,
        artista: str,
        tom_original: str,
        bpm: float,
        qualidade: QualidadeGabarito,
    ) -> None:
        self._musica = musica
        self._artista = artista
        self._tom_original = tom_original
        self._bpm = bpm
        self._qualidade = qualidade
        self._trechos: list[TrechoSolo | TrechoDuo | TrechoUnissono] = []
        self._acordes: list[AcordeViolao] = []
        self._letra: list[LetraLinha] = []

    def add_solo(self, *, inicio_s: float, fim_s: float, voz: NotaSeries) -> GabaritoBuilder:
        self._trechos.append(TrechoSolo(inicio_s=inicio_s, fim_s=fim_s, voz=voz))
        return self

    def add_duo(
        self,
        *,
        inicio_s: float,
        fim_s: float,
        voz_aguda: NotaSeries,
        voz_grave: NotaSeries,
        intervalo_semitons: int,
    ) -> GabaritoBuilder:
        self._trechos.append(
            TrechoDuo(
                inicio_s=inicio_s,
                fim_s=fim_s,
                voz_aguda=voz_aguda,
                voz_grave=voz_grave,
                intervalo_semitons=intervalo_semitons,
            )
        )
        return self

    def add_unissono(self, *, inicio_s: float, fim_s: float, voz: NotaSeries) -> GabaritoBuilder:
        self._trechos.append(TrechoUnissono(inicio_s=inicio_s, fim_s=fim_s, voz=voz))
        return self

    def add_acorde(self, *, tempo_s: float, acorde: str) -> GabaritoBuilder:
        self._acordes.append(AcordeViolao(tempo_s=tempo_s, acorde=acorde))
        return self

    def add_letra(self, *, tempo_s: float, texto: str) -> GabaritoBuilder:
        self._letra.append(LetraLinha(tempo_s=tempo_s, texto=texto))
        return self

    def build(self) -> Gabarito:
        return Gabarito(
            musica=self._musica,
            artista=self._artista,
            tom_original=self._tom_original,
            bpm=self._bpm,
            qualidade_gabarito=self._qualidade,
            trechos=list(self._trechos),
            acordes_violao=list(self._acordes),
            letra_timestamped=list(self._letra),
        )


__all__ = [
    "AcordeViolao",
    "Gabarito",
    "GabaritoBuilder",
    "LetraLinha",
    "NivelQualidade",
    "NotaSeries",
    "QualidadeGabarito",
    "TipoTrecho",
    "Trecho",
    "TrechoDuo",
    "TrechoSolo",
    "TrechoUnissono",
]
