"""Cifra Club + Musixmatch adapters and the search facade that combines them.

The cifra fallback layer produces a *partial* gabarito: it carries
``acordes_violao`` (from Cifra Club) and ``letra_timestamped`` (from
Musixmatch) but no ``trechos`` — there is no melody source on this path.
The quality envelope is ``"media"`` because the chord chart is human-curated
but the melody must be reconstructed by the student against the chord changes
and lyrics.

External services are reached through an injected ``httpx.AsyncClient`` so
tests can use ``MockTransport`` without touching the network.
"""

from __future__ import annotations

import re
from typing import Protocol

import httpx

from auladcanto.domain.gabarito import (
    AcordeViolao,
    Gabarito,
    LetraLinha,
    QualidadeGabarito,
)

_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_BPM_FALLBACK = 100.0

_CHORD_TOKEN_RE = re.compile(
    r"\b([A-G](?:#|b)?(?:m|M|maj|min|dim|aug|sus|add)?\d?(?:\d{1,2})?(?:[#b]\d{1,2})?(?:/[A-G](?:#|b)?)?)\b"
)
_BPM_HINT_RE = re.compile(r"bpm[^0-9]{0,5}(\d{2,3})", re.IGNORECASE)
_TOM_HINT_RE = re.compile(r"tom[^A-G]{0,5}([A-G](?:#|b)?m?)", re.IGNORECASE)


class CifraSource(Protocol):
    """Adapter contract for a single cifra/lyric service.

    Returns a ``(acordes, letra, bpm_estimado, tom)`` tuple on hit, or ``None``
    on miss. Either ``acordes`` or ``letra`` may be empty depending on which
    service the concrete adapter wraps. ``bpm_estimado`` may be ``None`` when
    the source did not surface a tempo hint.
    """

    async def buscar(
        self, titulo: str, artista: str
    ) -> tuple[list[AcordeViolao], list[LetraLinha], float | None, str | None] | None: ...


class CifraClubSource:
    """Cifra Club (https://www.cifraclub.com.br) adapter.

    The site exposes per-song HTML at ``/{slug-artista}/{slug-musica}/`` where
    chord/lyric blocks are wrapped in ``<pre>`` with chord tokens in ``<b>``
    tags. The adapter performs a very lenient text extraction so it survives
    HTML changes: it pulls *any* token matching the project's chord regex.
    """

    BASE_URL = "https://www.cifraclub.com.br"
    SOURCE_TAG = "cifraclub"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)

    async def buscar(
        self, titulo: str, artista: str
    ) -> tuple[list[AcordeViolao], list[LetraLinha], float | None, str | None] | None:
        path = f"/{_slugify(artista)}/{_slugify(titulo)}/"
        url = f"{self.BASE_URL}{path}"
        try:
            response = await self._client.get(url)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None

        body = response.text
        acordes = _parse_chords_from_html(body)
        if not acordes:
            return None

        bpm = _parse_bpm_hint(body)
        tom = _parse_tom_hint(body)
        return acordes, [], bpm, tom


class MusixmatchSource:
    """Musixmatch (https://www.musixmatch.com) adapter — timestamped lyrics only.

    Musixmatch occasionally exposes timed-lyrics JSON via its sync API; the
    real implementation will live in a follow-up batch. Until then the
    adapter remains a wired-in stub so the search facade composes cleanly.
    """

    SOURCE_TAG = "musixmatch"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)

    async def buscar(
        self, titulo: str, artista: str
    ) -> tuple[list[AcordeViolao], list[LetraLinha], float | None, str | None] | None:
        raise NotImplementedError("MusixmatchSource adapter is not implemented yet")


class CifraSearch:
    """Combines a chord source and an optional lyric source into a partial gabarito.

    The chord source is required — if it returns ``None`` the whole layer
    misses. The lyric source is optional; when it returns ``None`` (or raises
    ``NotImplementedError``) the produced gabarito carries an empty
    ``letra_timestamped`` list and an alert flagging the omission.
    """

    def __init__(
        self,
        cifra_source: CifraSource,
        lyric_source: CifraSource | None = None,
    ) -> None:
        self._cifra_source = cifra_source
        self._lyric_source = lyric_source

    async def buscar(self, titulo: str, artista: str) -> Gabarito | None:
        chord_hit = await self._safe_call(self._cifra_source, titulo, artista)
        if chord_hit is None:
            return None
        acordes, _ignored_letra, bpm_hint, tom_hint = chord_hit

        letra: list[LetraLinha] = []
        fontes: list[str] = [_source_tag(self._cifra_source)]
        alertas: list[str] = []

        if self._lyric_source is not None:
            lyric_hit = await self._safe_call(self._lyric_source, titulo, artista)
            if lyric_hit is not None:
                _ignored_acordes, letra, lyric_bpm, lyric_tom = lyric_hit
                fontes.append(_source_tag(self._lyric_source))
                if bpm_hint is None:
                    bpm_hint = lyric_bpm
                if tom_hint is None:
                    tom_hint = lyric_tom
            else:
                alertas.append("timestamped lyrics unavailable")
        else:
            alertas.append("timestamped lyrics source not configured")

        return Gabarito(
            musica=titulo,
            artista=artista,
            tom_original=tom_hint or "C",
            bpm=bpm_hint or _DEFAULT_BPM_FALLBACK,
            qualidade_gabarito=QualidadeGabarito(
                nivel="media",
                fontes=fontes,
                alertas=alertas,
            ),
            trechos=[],
            acordes_violao=acordes,
            letra_timestamped=letra,
        )

    @staticmethod
    async def _safe_call(
        source: CifraSource, titulo: str, artista: str
    ) -> tuple[list[AcordeViolao], list[LetraLinha], float | None, str | None] | None:
        try:
            return await source.buscar(titulo, artista)
        except NotImplementedError:
            return None
        except Exception:
            return None


def _parse_chords_from_html(html: str) -> list[AcordeViolao]:
    acordes: list[AcordeViolao] = []
    tempo_s = 0.0
    seen_tokens: list[str] = []
    for match in _CHORD_TOKEN_RE.finditer(html):
        token = match.group(1)
        if _looks_like_word(html, match.start(), match.end()):
            continue
        try:
            acordes.append(AcordeViolao(tempo_s=tempo_s, acorde=token))
        except ValueError:
            continue
        seen_tokens.append(token)
        tempo_s += 2.0
    return acordes


def _looks_like_word(html: str, start: int, end: int) -> bool:
    before = html[start - 1] if start > 0 else ""
    after = html[end] if end < len(html) else ""
    return (before.isalpha() and before.islower()) or (after.isalpha() and after.islower())


def _parse_bpm_hint(text: str) -> float | None:
    match = _BPM_HINT_RE.search(text)
    if match is None:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _parse_tom_hint(text: str) -> str | None:
    match = _TOM_HINT_RE.search(text)
    if match is None:
        return None
    return match.group(1)


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return cleaned.strip("-")


def _source_tag(source: CifraSource) -> str:
    tag = getattr(source, "SOURCE_TAG", None)
    if isinstance(tag, str) and tag:
        return tag
    return source.__class__.__name__.lower()


__all__ = [
    "CifraClubSource",
    "CifraSearch",
    "CifraSource",
    "MusixmatchSource",
]
