"""Domain models for the v1 batch report emitted every ~30s during a session.

A ``BatchReport`` is the structured JSON the analysis pipeline (phase 3B) hands
back to the MCP layer (phase 5) so the Claude in SKILL.md (phase 6) can give
contextualized feedback to the student. The shape mirrors section 3.5 of the
implementation plan; each metric sub-object is produced by a dedicated
analyzer (pitch, vibrato, respiracao, ataque, timing, transposicao).

Schema v1 is documented in ``docs/schema-v1.md``. The ``schema_version`` field
is the upgrade hook: SKILL.md will refuse to interpret payloads it does not
recognize (current ceiling is 1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CURRENT_SCHEMA_VERSION = 1

AtaquePredominante = Literal["direto", "under_shoot", "over_shoot", "instavel", "indeterminado"]
VibratoNaturalidade = Literal["natural", "lento_tremulo", "rapido_tenso"]
TipoRespiro = Literal["rapido_insuficiente", "normal", "preparatorio_longo"]
ProjecaoGeral = Literal["fraca", "boa", "forte"]
Tendencia = Literal["melhorando", "estavel", "piorando", "acelerando", "desacelerando", "n/a"]
VozEscolhida = Literal["aguda", "grave", "solo", "n/a"]


class TimingMetrics(BaseModel):
    """Tempo metrics derived from onset analysis over the 30s window."""

    model_config = ConfigDict(extra="forbid")

    bpm_usuario: float = Field(ge=0.0)
    bpm_gabarito: float = Field(ge=0.0)
    desvio_bpm: float
    acelerando_no_batch: bool
    irregularidade_ritmica: float = Field(ge=0.0, le=1.0)


class MomentoCritico(BaseModel):
    """A single pitch error worth surfacing to the student."""

    model_config = ConfigDict(extra="forbid")

    timestamp_ms: int = Field(ge=0)
    nota_alvo: str
    erro_cents: int


class PitchMetrics(BaseModel):
    """Pitch accuracy metrics over the 30s window."""

    model_config = ConfigDict(extra="forbid")

    notas_corretas_pct: float = Field(ge=0.0, le=100.0)
    precisao_oitava_pct: float = Field(ge=0.0, le=100.0)
    desvio_padrao_cents: float = Field(ge=0.0)
    ataque_predominante: AtaquePredominante
    momentos_criticos: list[MomentoCritico] = Field(default_factory=list)


class VibratoMetrics(BaseModel):
    """Vibrato presence and qualitative classification."""

    model_config = ConfigDict(extra="forbid")

    detectado: bool
    frequencia_hz: float | None = Field(default=None, ge=0.0)
    naturalidade: VibratoNaturalidade | None = None


class Respiro(BaseModel):
    """One detected breath with timing and quality classification."""

    model_config = ConfigDict(extra="forbid")

    timestamp_ms: int = Field(ge=0)
    duracao_ms: int = Field(ge=0)
    tipo: TipoRespiro


class RespiracaoMetrics(BaseModel):
    """Breath metrics for the 30s window."""

    model_config = ConfigDict(extra="forbid")

    respiros_detectados: int = Field(ge=0)
    respiros: list[Respiro] = Field(default_factory=list)
    alerta_sem_respiro: bool = False


class VolumeMetrics(BaseModel):
    """Loudness / projection metrics for the 30s window."""

    model_config = ConfigDict(extra="forbid")

    media_normalizada: float = Field(ge=0.0, le=1.0)
    quedas_abruptas: int = Field(ge=0)
    projecao_geral: ProjecaoGeral


class ComparacaoBatchAnterior(BaseModel):
    """Trends versus the previous batch — populated from batch 2 onwards."""

    model_config = ConfigDict(extra="forbid")

    bpm_tendencia: Tendencia
    pitch_tendencia: Tendencia
    respiro_tendencia: Tendencia


class TransposicaoDetectada(BaseModel):
    """Heuristic detection that the student is singing in a different key."""

    model_config = ConfigDict(extra="forbid")

    detectada: bool
    semitons: int = 0
    confianca: float = Field(default=0.0, ge=0.0, le=1.0)


class BatchReport(BaseModel):
    """The v1 JSON report produced for each 30s analysis window."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CURRENT_SCHEMA_VERSION
    batch_numero: int = Field(ge=0)
    timestamp: datetime
    musica_id: str
    duracao_segundos: int = Field(default=30, gt=0)
    posicao_musica: str
    voz_escolhida: VozEscolhida = "n/a"
    timing: TimingMetrics
    pitch: PitchMetrics
    vibrato: VibratoMetrics
    respiracao: RespiracaoMetrics
    volume: VolumeMetrics
    transposicao_detectada: TransposicaoDetectada | None = None
    comparacao_batch_anterior: ComparacaoBatchAnterior | None = None

    @model_validator(mode="after")
    def _check_schema_version(self) -> Self:
        if self.schema_version > CURRENT_SCHEMA_VERSION:
            raise ValueError(
                "BatchReport: schema_version "
                f"{self.schema_version} is newer than this build supports "
                f"(max {CURRENT_SCHEMA_VERSION}); please upgrade auladcanto-mcp"
            )
        if self.schema_version < 1:
            raise ValueError(
                f"BatchReport: schema_version must be >= 1 (got {self.schema_version})"
            )
        return self

    def to_json(self, indent: int = 2) -> str:
        """Serialize to a JSON string. ``indent`` mirrors ``json.dumps``."""
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, s: str) -> BatchReport:
        """Parse a JSON string into a ``BatchReport``."""
        return cls.model_validate_json(s)


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "AtaquePredominante",
    "BatchReport",
    "ComparacaoBatchAnterior",
    "MomentoCritico",
    "PitchMetrics",
    "ProjecaoGeral",
    "RespiracaoMetrics",
    "Respiro",
    "Tendencia",
    "TimingMetrics",
    "TipoRespiro",
    "TransposicaoDetectada",
    "VibratoMetrics",
    "VibratoNaturalidade",
    "VolumeMetrics",
    "VozEscolhida",
]
