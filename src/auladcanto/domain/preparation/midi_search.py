"""Public-MIDI-database adapters and the search facade that fans out across them.

The orchestrator's first fallback layer asks :class:`MidiSearch` to look up a
song. ``MidiSearch`` walks a list of injected :class:`MidiSource` adapters and
returns the first successful hit. Adapters are responsible for talking to a
specific service (BitMIDI, FreeMidi, MidiWorld, …) and downloading a ``.mid``
binary, then handing it off to :func:`midi_bytes_to_gabarito` for parsing.

All network access flows through an injected ``httpx.AsyncClient``, so the
tests can swap in a ``MockTransport`` to avoid touching the network.
"""

from __future__ import annotations

from typing import Protocol

import httpx
import pretty_midi

from auladcanto.domain.gabarito import (
    Gabarito,
    GabaritoBuilder,
    NotaSeries,
    QualidadeGabarito,
)

_DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_TRECHO_NOTES = 200
_DEFAULT_FRAMES_PER_NOTE = 2
_MIN_TRECHO_DURATION_S = 0.05


class MidiSource(Protocol):
    """Adapter contract for a single public MIDI database.

    ``buscar`` returns ``None`` on miss (no match, parse failure, HTTP error
    that is *not* fatal). It must not raise for routine failures — exceptions
    are reserved for programmer errors or hard infrastructure problems the
    caller wants surfaced.
    """

    async def buscar(self, titulo: str, artista: str) -> Gabarito | None: ...


class BitMidiSource:
    """BitMIDI (https://bitmidi.com) adapter.

    The site exposes a JSON search endpoint at ``/api/search`` returning
    ``{"PageData": {"results": [{"downloadUrl": "..."}, ...]}}`` and serves
    raw ``.mid`` blobs over HTTPS. The adapter is deliberately conservative —
    it returns ``None`` for any non-200 response, missing fields or unparseable
    payload.
    """

    BASE_URL = "https://bitmidi.com"
    SOURCE_TAG = "bitmidi"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)

    async def buscar(self, titulo: str, artista: str) -> Gabarito | None:
        query = f"{artista} {titulo}".strip()
        search_url = f"{self.BASE_URL}/api/search"
        try:
            response = await self._client.get(search_url, params={"q": query})
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None

        download_url = _extract_first_download_url(payload)
        if download_url is None:
            return None

        if download_url.startswith("/"):
            download_url = f"{self.BASE_URL}{download_url}"

        try:
            blob_response = await self._client.get(download_url)
        except httpx.HTTPError:
            return None
        if blob_response.status_code != 200:
            return None
        midi_bytes = blob_response.content

        try:
            return midi_bytes_to_gabarito(
                midi_bytes,
                titulo=titulo,
                artista=artista,
                source_tag=self.SOURCE_TAG,
            )
        except MidiParseError:
            return None


class FreeMidiSource:
    """Stub for FreeMidi (https://freemidi.org).

    Wired into the search list now so the orchestrator can exercise the
    fallback chain; the actual HTML scraping is deferred to a follow-up batch.
    """

    SOURCE_TAG = "freemidi"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)

    async def buscar(self, titulo: str, artista: str) -> Gabarito | None:
        raise NotImplementedError("FreeMidiSource adapter is not implemented yet")


class MidiWorldSource:
    """Stub for MidiWorld (https://www.midiworld.com)."""

    SOURCE_TAG = "midiworld"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)

    async def buscar(self, titulo: str, artista: str) -> Gabarito | None:
        raise NotImplementedError("MidiWorldSource adapter is not implemented yet")


class MidiSearch:
    """Walks a list of MIDI sources and returns the first successful hit.

    Exceptions raised by individual sources are caught and treated as misses
    so a single broken adapter cannot poison the whole fallback chain.
    ``NotImplementedError`` is treated the same way so the stub adapters can
    sit in the chain harmlessly.
    """

    def __init__(self, sources: list[MidiSource]) -> None:
        self._sources = sources

    async def buscar(self, titulo: str, artista: str) -> Gabarito | None:
        for source in self._sources:
            try:
                result = await source.buscar(titulo, artista)
            except NotImplementedError:
                continue
            except Exception:
                continue
            if result is not None:
                return result
        return None


class MidiParseError(Exception):
    """Raised when MIDI bytes cannot be turned into a :class:`Gabarito`."""


def midi_bytes_to_gabarito(
    midi_bytes: bytes,
    *,
    titulo: str,
    artista: str,
    source_tag: str,
) -> Gabarito:
    """Parse a MIDI binary into a :class:`Gabarito` with ``qualidade=alta``.

    The parser is intentionally minimal — it takes the first non-drum
    instrument it can find and emits a single :class:`TrechoSolo` per note,
    capping at :data:`_MAX_TRECHO_NOTES` to keep the resulting payload sane.
    Successive note bounds are clamped so the validator's non-overlap rule
    holds even when the source MIDI has chordal overlaps.
    """

    if not midi_bytes:
        raise MidiParseError("MIDI payload is empty")

    try:
        midi = _load_midi(midi_bytes)
    except Exception as exc:  # pretty_midi raises a variety of types
        raise MidiParseError(f"failed to parse MIDI bytes: {exc}") from exc

    instrument = _pick_melody_instrument(midi)
    if instrument is None:
        raise MidiParseError("no melodic instrument found in MIDI file")

    bpm = _extract_bpm(midi)
    notes = sorted(instrument.notes, key=lambda n: n.start)[:_MAX_TRECHO_NOTES]
    if not notes:
        raise MidiParseError("instrument has no notes")

    builder = GabaritoBuilder(
        musica=titulo,
        artista=artista,
        tom_original=_infer_tom_original(notes),
        bpm=bpm,
        qualidade=QualidadeGabarito(nivel="alta", fontes=[source_tag]),
    )

    last_fim = 0.0
    for note in notes:
        inicio = max(float(note.start), last_fim)
        fim = max(float(note.end), inicio + _MIN_TRECHO_DURATION_S)
        voz = _note_to_series(note.pitch, inicio, fim)
        builder.add_solo(inicio_s=inicio, fim_s=fim, voz=voz)
        last_fim = fim

    return builder.build()


def _extract_first_download_url(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    page_data = payload.get("PageData")
    if not isinstance(page_data, dict):
        return None
    results = page_data.get("results")
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None
    url = first.get("downloadUrl")
    if not isinstance(url, str) or not url:
        return None
    return url


def _load_midi(midi_bytes: bytes) -> pretty_midi.PrettyMIDI:
    import io

    return pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))


def _pick_melody_instrument(midi: pretty_midi.PrettyMIDI) -> pretty_midi.Instrument | None:
    melodic = [inst for inst in midi.instruments if not inst.is_drum and inst.notes]
    if not melodic:
        return None
    return max(melodic, key=lambda inst: len(inst.notes))


def _extract_bpm(midi: pretty_midi.PrettyMIDI) -> float:
    _times, tempi = midi.get_tempo_changes()
    if len(tempi) == 0:
        return 120.0
    bpm = float(tempi[0])
    if bpm <= 0:
        return 120.0
    return bpm


def _infer_tom_original(notes: list[pretty_midi.Note]) -> str:
    pitch_classes = [n.pitch % 12 for n in notes]
    dominant = max(set(pitch_classes), key=pitch_classes.count)
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return str(names[dominant])


def _note_to_series(midi_pitch: int, inicio_s: float, fim_s: float) -> NotaSeries:
    hz = float(440.0 * (2.0 ** ((midi_pitch - 69) / 12.0)))
    pitches = [hz] * _DEFAULT_FRAMES_PER_NOTE
    if _DEFAULT_FRAMES_PER_NOTE == 1:
        tempos = [inicio_s]
    else:
        step = (fim_s - inicio_s) / (_DEFAULT_FRAMES_PER_NOTE - 1)
        tempos = [inicio_s + i * step for i in range(_DEFAULT_FRAMES_PER_NOTE)]
    return NotaSeries(pitches_hz=pitches, tempos_s=tempos)


__all__ = [
    "BitMidiSource",
    "FreeMidiSource",
    "MidiParseError",
    "MidiSearch",
    "MidiSource",
    "MidiWorldSource",
    "midi_bytes_to_gabarito",
]
