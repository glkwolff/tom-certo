"""Top-level command-line interface for ``auladcanto``.

This module wires the Typer subcommands shipped by the MVP:

* ``init``           — provision ``~/.auladcanto/`` and seed ``SKILL.md`` /
  ``CLAUDE.md``.
* ``calibrar``       — run the four-step microphone calibration and persist
  the result on the student profile.
* ``mcp-server``     — alias for the ``auladcanto-mcp`` console script.
* ``verificar-deps`` — report whether external tooling and optional Python
  extras are reachable.
* ``limpar-cache``   — drop the contents of ``cache/`` and ``sessoes/`` while
  preserving the profile and the seeded templates.

Every subcommand is intentionally side-effect free at import time. They read
``AULADCANTO_HOME`` lazily through :mod:`auladcanto.storage.paths`, which keeps
tests fast and lets the binary co-exist with multiple home directories on the
same machine.
"""

from __future__ import annotations

import asyncio
import shutil
from importlib.resources import as_file, files
from pathlib import Path

import typer

from auladcanto import __version__
from auladcanto.storage.paths import (
    cache_dir,
    ensure_home_exists,
    historico_dir,
    home_dir,
    perfil_path,
    sessoes_dir,
)

app = typer.Typer(
    name="auladcanto",
    help="Local music tutor — CLI companion to the auladcanto-mcp server.",
    no_args_is_help=True,
    add_completion=False,
)


# ---------------------------------------------------------------------------
# Root callback / --version
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    """Print version and exit when ``--version`` is passed."""
    if value:
        typer.echo(__version__)
        raise typer.Exit(code=0)


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the auladcanto version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Root callback — handles global flags such as ``--version``."""
    # Body intentionally empty; ``--version`` is handled in the callback.
    return None


# ---------------------------------------------------------------------------
# Helpers — template copying
# ---------------------------------------------------------------------------


def _copy_template(template_name: str, destination: Path, *, overwrite: bool) -> bool:
    """Copy a packaged template file to ``destination``.

    Returns ``True`` when the file was written, ``False`` when it was skipped
    (already present and ``overwrite`` is ``False``). Uses
    :func:`importlib.resources.files` so the lookup works both from a wheel and
    from an editable checkout.
    """
    if destination.exists() and not overwrite:
        return False
    resource = files("auladcanto.templates").joinpath(template_name)
    with as_file(resource) as src_path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_path, destination)
    return True


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command("init")
def init() -> None:
    """Provision ``~/.auladcanto/`` and seed the Claude Code skill templates."""
    root = ensure_home_exists()
    typer.echo(f"auladcanto: ensured home directory at {root}")

    skill_destination = root / "SKILL.md"
    if _copy_template("SKILL.md", skill_destination, overwrite=False):
        typer.echo(f"auladcanto: wrote {skill_destination}")
    else:
        typer.echo(
            f"auladcanto: {skill_destination} already exists — preserving your customisations"
        )

    cwd_claude = Path.cwd() / "CLAUDE.md"
    copy_claude = typer.confirm(
        f"Copy the auladcanto persona to {cwd_claude}?",
        default=False,
    )
    if copy_claude:
        overwrite = True
        if cwd_claude.exists():
            overwrite = typer.confirm(
                f"{cwd_claude} already exists. Overwrite?",
                default=False,
            )
        if overwrite:
            _copy_template("CLAUDE.md", cwd_claude, overwrite=True)
            typer.echo(f"auladcanto: wrote {cwd_claude}")
        else:
            typer.echo("auladcanto: skipped CLAUDE.md (kept existing file)")
    else:
        typer.echo("auladcanto: skipped CLAUDE.md (not copied)")

    typer.echo("")
    typer.echo("Next steps:")
    typer.echo("  1. Run `auladcanto calibrar` to calibrate your microphone.")
    typer.echo("  2. Register the MCP server with Claude Code:")
    typer.echo("       claude mcp add auladcanto -- auladcanto-mcp")
    typer.echo("  3. Open Claude Code and ask it to teach you a song.")


# ---------------------------------------------------------------------------
# calibrar
# ---------------------------------------------------------------------------


def _run_calibration() -> None:
    """Run the four-step calibration and persist the result on the profile."""
    # Imported lazily so ``auladcanto --help`` keeps working when the audio
    # extra is not installed (sounddevice import only happens at start() time
    # but CaptureConfig is still cheap, and the MissingAudioDependencyError
    # raised below remains the canonical signal for the missing extra).
    from datetime import UTC, datetime

    from auladcanto.domain.analysis.capture import CaptureConfig, SoundDeviceCapture
    from auladcanto.domain.calibration.microfone import (
        CalibradorMicrofone,
        CalibrationConfig,
    )
    from auladcanto.domain.perfil_aluno import PerfilAluno

    ensure_home_exists()

    config = CalibrationConfig()
    capture = SoundDeviceCapture(CaptureConfig(sample_rate=config.sample_rate))
    calibrador = CalibradorMicrofone(capture=capture, config=config)

    passo_labels: dict[str, str] = {
        "silencio": "Stay quiet (room tone)",
        "fala": "Speak at normal volume",
        "escala": "Sing a comfortable scale (la-la-la, low to high)",
        "latencia": "Finalising latency measurement",
    }

    def on_progress(passo: str, segundos_restantes: int) -> None:
        label = passo_labels.get(passo, passo)
        if segundos_restantes > 0:
            typer.echo(f"  [{passo}] {label} — {segundos_restantes}s")
        else:
            typer.echo(f"  [{passo}] {label}")

    typer.echo("auladcanto: starting microphone calibration...")
    resultado = asyncio.run(calibrador.calibrar(on_progress=on_progress))
    typer.echo("auladcanto: calibration complete")

    target = perfil_path()
    perfil = PerfilAluno.load(target) if target.exists() else PerfilAluno(criado=datetime.now(UTC))
    perfil.calibracao = resultado
    perfil.save(target)

    typer.echo(f"  noise floor:      {resultado.noise_floor_db:.1f} dBFS")
    typer.echo(f"  dynamic range:    {resultado.range_dinamico_db:.1f} dB")
    typer.echo(f"  pitch accuracy:   {resultado.pitch_detection_acuracia_pct:.1f}%")
    typer.echo(f"  latency:          {resultado.latencia_aproximada_ms} ms")
    typer.echo(f"auladcanto: profile saved to {target}")


@app.command("calibrar")
def calibrar() -> None:
    """Calibrate microphone (noise floor, dynamic range, pitch, latency)."""
    from auladcanto.domain.analysis.capture import MissingAudioDependencyError

    try:
        _run_calibration()
    except MissingAudioDependencyError as exc:
        typer.echo(f"auladcanto: {exc}", err=True)
        typer.echo(
            'auladcanto: install the audio extra with `pip install -e ".[audio]"` and retry.',
            err=True,
        )
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# mcp-server
# ---------------------------------------------------------------------------


@app.command("mcp-server")
def mcp_server() -> None:
    """Run the MCP server in the foreground (alternative to the console script)."""
    from auladcanto.mcp.server import main

    raise SystemExit(main())


# ---------------------------------------------------------------------------
# verificar-deps
# ---------------------------------------------------------------------------


_BIN_HINTS: dict[str, dict[str, str]] = {
    "ffmpeg": {
        "debian": "sudo apt install ffmpeg",
        "fedora": "sudo dnf install ffmpeg",
        "arch": "sudo pacman -S ffmpeg",
        "macos": "brew install ffmpeg",
        "windows": "choco install ffmpeg",
    },
    "yt-dlp": {
        "any": "pip install yt-dlp  (or: pipx install yt-dlp)",
    },
}


def _check_binary(name: str) -> bool:
    """Print presence/absence of ``name`` on ``PATH``; return True if present."""
    location = shutil.which(name)
    if location:
        typer.echo(f"  [ok]      {name}: {location}")
        return True
    typer.echo(f"  [missing] {name}: not found on PATH")
    for label, hint in _BIN_HINTS.get(name, {}).items():
        typer.echo(f"            install ({label}): {hint}")
    return False


def _check_python_module(module: str, extra_hint: str) -> bool:
    """Try importing ``module``; print status and an install hint when missing."""
    try:
        __import__(module)
    except ImportError as exc:
        typer.echo(f"  [missing] {module}: {exc}")
        typer.echo(f"            install: {extra_hint}")
        return False
    typer.echo(f"  [ok]      {module}")
    return True


@app.command("verificar-deps")
def verificar_deps() -> None:
    """Check external tooling: ffmpeg, yt-dlp, audio stack, MCP SDK."""
    typer.echo("auladcanto: checking external binaries...")
    ffmpeg_ok = _check_binary("ffmpeg")
    _check_binary("yt-dlp")

    typer.echo("")
    typer.echo("auladcanto: checking optional Python modules...")
    _check_python_module("sounddevice", 'pip install -e ".[audio]"')
    _check_python_module("aubio", 'pip install -e ".[audio]"  (unsupported on Python 3.13+)')
    _check_python_module("crepe", "pip install crepe  (TensorFlow-based pitch detector)")
    _check_python_module("basic_pitch", "pip install basic-pitch  (Spotify polyphonic transcriber)")
    _check_python_module("demucs", "pip install demucs  (source separation)")
    _check_python_module("mcp", 'pip install -e ".[mcp]"')

    typer.echo("")
    if ffmpeg_ok:
        typer.echo("auladcanto: ffmpeg is present — core features available.")
        raise typer.Exit(code=0)
    typer.echo(
        "auladcanto: ffmpeg is missing — audio download/preparation will fail.",
        err=True,
    )
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# limpar-cache
# ---------------------------------------------------------------------------


@app.command("limpar-cache")
def limpar_cache() -> None:
    """Prune cached gabaritos and in-progress sessions (keeps profile + templates)."""
    cache = cache_dir()
    sessoes = sessoes_dir()
    typer.echo("auladcanto: this will delete:")
    typer.echo(f"  - {cache} (cached gabaritos and downloaded audio)")
    typer.echo(f"  - {sessoes} (in-progress and recently-completed sessions)")
    typer.echo(f"auladcanto: profile, history and templates under {home_dir()} are preserved.")
    typer.echo(f"auladcanto: history dir {historico_dir()} is preserved.")

    confirmed = typer.confirm("Proceed?", default=False)
    if not confirmed:
        typer.echo("auladcanto: aborted; nothing was deleted.")
        raise typer.Exit(code=0)

    for target in (cache, sessoes):
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        typer.echo(f"auladcanto: cleared {target}")

    typer.echo("auladcanto: cache cleared.")


if __name__ == "__main__":  # pragma: no cover — module entry point
    app()
