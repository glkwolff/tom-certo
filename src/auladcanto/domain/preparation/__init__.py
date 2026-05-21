"""Gabarito preparation pipeline (phase 2A).

The preparation package implements the *graceful fallback* strategy described
in section 3.3 of ``docs/maestro/plans/auladcanto-mcp-mvp.md``:

    1. MIDI database lookup → ``qualidade.nivel = "alta"``.
    2. Cifra/lyric database lookup → ``qualidade.nivel = "media"``.
    3. Audio pipeline (yt-dlp + ffmpeg + demucs + CREPE) → ``qualidade.nivel = "baixa"``.

External services and ML tools are injected so the pipeline is fully
testable without network or heavy ML dependencies.
"""
