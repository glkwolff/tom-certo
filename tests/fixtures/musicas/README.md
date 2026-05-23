# `tests/fixtures/musicas/`

Reserved directory for audio fixtures used by the test suite.

## Status: empty for the MVP

The MVP ships **no real audio files** in this directory. Every test that
needs audio synthesises a deterministic numpy waveform inline (see
`tests/golden/test_batch_scenarios.py` and `tests/integration/test_e2e.py`)
so the test suite stays:

- **Reproducible** — the same code generates the same samples on every machine.
- **Light** — no binary blobs in version control, no LFS bill, no copyright
  exposure.
- **Fast** — no disk I/O before the analyzer runs.

If you find yourself reaching for a real recording to make a test pass, first
ask whether a synthetic signal (sine, FM, white noise, click train) can
exercise the same code path. Most of the analyzers were designed against
synthetic inputs precisely so they could be unit-tested without fixtures.

## Adding new fixtures

When a future test genuinely needs a real recording (e.g. validating a
production pitch detector against a vocal take that no synthetic signal can
mimic), the file must obey **all** of the following rules:

1. **Naming convention**: `<artist-slug>__<song-slug>.wav`
   - `<artist-slug>` and `<song-slug>` are lowercase, ASCII, hyphen-separated.
   - The double underscore (`__`) separates artist from song so the two halves
     can be split unambiguously by tooling.
   - Example: `bruno-marrone__faz-parte.wav`
2. **Format**: 16-bit PCM WAV, **44.1 kHz mono**.
3. **Length**: **60 seconds maximum**. The pipeline operates on 30 s batches
   so anything longer than 60 s is almost certainly excessive for a test.
4. **Volume**: peak-normalised to roughly -3 dBFS (room to spare so the
   silence-threshold heuristics in `RespiracaoAnalyzer` and `BatchBuffer`
   behave the same way they do on real microphone input).
5. **Copyright**: the file **MUST NOT contain copyrighted audio**. Acceptable
   sources include:
   - Original recordings authored by the project contributors (with explicit
     permission to redistribute under the project license).
   - Public-domain or Creative-Commons-licensed material with the license
     terms documented in this README under "Provenance".
   - Synthetic instrument renders the contributor generated themselves.

   When in doubt, *do not commit the file*. Generate a synthetic equivalent
   instead.
6. **Provenance**: add an entry to the table below describing where the file
   came from and under what license. Tests that load the file should import
   its filename via a constant defined in `tests/fixtures/__init__.py` so the
   provenance can be cross-checked when the fixture is touched.

## Provenance

| Filename | Source | License | Added by | Date |
| --- | --- | --- | --- | --- |
| _(none)_ | — | — | — | — |
