"""Pitch comparator package (phase 3C).

This package compares the user's sung pitch contour against the canonical
:class:`auladcanto.domain.gabarito.Gabarito`. It provides two collaborators:

* :class:`auladcanto.domain.comparator.aligner.Aligner` resamples the user
  pitch series onto the reference time grid for each ``Trecho`` intersecting
  the current 30s batch. It uses ``mir_eval.melody.resample_melody_series``
  when the ``[audio]`` extra is installed and falls back to a numpy linear
  interpolation otherwise; an optional dynamic-time-warping path handles the
  "user sang faster/slower than reference" case.
* :class:`auladcanto.domain.comparator.score.Scorer` consumes those aligned
  pairs and produces the pitch portion of the v1 ``BatchReport`` — raw pitch
  accuracy (±50 cents), chroma (octave-agnostic) accuracy, voicing recall,
  false-positive voicing, and the top-N momentos críticos surfaced to the
  student.

The split mirrors section 3.5 of ``docs/maestro/plans/auladcanto-mcp-mvp.md``:
``aligner.py`` owns the *temporal* problem (matching ref and user time axes)
while ``score.py`` owns the *metric* problem (turning aligned pairs into
human-readable percentages).
"""
