"""Heuristic quality evaluator for prepared gabaritos.

The evaluator never raises — it returns a new :class:`Gabarito` whose
``qualidade_gabarito`` field may be downgraded and/or carry additional alerts
compared to the input. The heuristics are intentionally conservative and
side-effect free so the orchestrator can apply them after any of the three
fallback layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from auladcanto.domain.gabarito import (
    Gabarito,
    NivelQualidade,
    QualidadeGabarito,
)

_NIVEL_ORDER: Final[dict[NivelQualidade, int]] = {"baixa": 0, "media": 1, "alta": 2}

_AUDIO_PIPELINE_SOURCE_TOKENS: Final[frozenset[str]] = frozenset(
    {"demucs+crepe", "demucs+basic-pitch", "basic-pitch", "crepe"}
)


@dataclass(frozen=True)
class QualityThresholds:
    """Tunable thresholds controlling the heuristic downgrade rules."""

    bpm_min: float = 40.0
    bpm_max: float = 240.0
    audio_pipeline_cap: NivelQualidade = "media"


class QualityEvaluator:
    """Re-evaluates a :class:`Gabarito` and adjusts its quality envelope.

    The evaluator inspects the gabarito's structure (trecho counts, source
    tags, BPM) and produces a *new* gabarito with a potentially lower
    ``nivel`` and an extended ``alertas`` list. The input is left
    untouched — callers should always reassign:

        gabarito = evaluator.avaliar(gabarito)
    """

    def __init__(self, thresholds: QualityThresholds | None = None) -> None:
        self._thresholds = thresholds or QualityThresholds()

    def avaliar(self, gabarito: Gabarito) -> Gabarito:
        nivel = gabarito.qualidade_gabarito.nivel
        alertas: list[str] = list(gabarito.qualidade_gabarito.alertas)

        nivel = self._apply_audio_pipeline_cap(gabarito, nivel)
        nivel, alertas = self._apply_empty_trechos_rule(gabarito, nivel, alertas)
        alertas = self._apply_duo_alert(gabarito, alertas)
        alertas = self._apply_bpm_alert(gabarito, alertas)

        nova_qualidade = QualidadeGabarito(
            nivel=nivel,
            fontes=list(gabarito.qualidade_gabarito.fontes),
            alertas=alertas,
        )
        return gabarito.model_copy(update={"qualidade_gabarito": nova_qualidade})

    def _apply_audio_pipeline_cap(
        self, gabarito: Gabarito, nivel: NivelQualidade
    ) -> NivelQualidade:
        fontes = set(gabarito.qualidade_gabarito.fontes)
        if fontes & _AUDIO_PIPELINE_SOURCE_TOKENS:
            return _min_nivel(nivel, self._thresholds.audio_pipeline_cap)
        return nivel

    def _apply_empty_trechos_rule(
        self,
        gabarito: Gabarito,
        nivel: NivelQualidade,
        alertas: list[str],
    ) -> tuple[NivelQualidade, list[str]]:
        if len(gabarito.trechos) == 0:
            new_alertas = list(alertas)
            new_alertas.append("no melody segments")
            return "baixa", new_alertas
        return nivel, alertas

    def _apply_duo_alert(self, gabarito: Gabarito, alertas: list[str]) -> list[str]:
        if len(gabarito.trechos) == 0:
            return alertas
        tipos = {trecho.tipo for trecho in gabarito.trechos}
        if "duo" in tipos and tipos & {"solo", "unissono"}:
            total_dur = sum(t.fim_s - t.inicio_s for t in gabarito.trechos)
            duo_dur = sum(t.fim_s - t.inicio_s for t in gabarito.trechos if t.tipo == "duo")
            if total_dur > 0:
                pct = round(100 * duo_dur / total_dur)
                new_alertas = list(alertas)
                new_alertas.append(f"duo vocal detected in {pct}% of music")
                return new_alertas
        return alertas

    def _apply_bpm_alert(self, gabarito: Gabarito, alertas: list[str]) -> list[str]:
        if gabarito.bpm < self._thresholds.bpm_min or gabarito.bpm > self._thresholds.bpm_max:
            new_alertas = list(alertas)
            new_alertas.append(f"unusual BPM ({gabarito.bpm:g})")
            return new_alertas
        return alertas


def _min_nivel(left: NivelQualidade, right: NivelQualidade) -> NivelQualidade:
    """Return the lower (more conservative) of two confidence levels."""
    return left if _NIVEL_ORDER[left] <= _NIVEL_ORDER[right] else right


__all__ = ["QualityEvaluator", "QualityThresholds"]
