"""Top-level command-line interface for `auladcanto`.

This module wires Typer subcommands that will be fleshed out in later phases.
For phase 0 (bootstrap) every subcommand is a stub that prints which future
phase will implement it.
"""

from __future__ import annotations

import typer

from auladcanto import __version__

app = typer.Typer(
    name="auladcanto",
    help="Local music tutor — CLI companion to the auladcanto-mcp server.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    """Print version and exit when `--version` is passed."""
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


@app.command("init")
def init() -> None:
    """Provision ``~/.auladcanto/`` (perfil, cache, sessoes, historico)."""
    typer.echo("auladcanto init: TODO (phase 7)")


@app.command("calibrar")
def calibrar() -> None:
    """Calibrate microphone (noise floor, optimal gain, latency)."""
    typer.echo("auladcanto calibrar: TODO (phase 7)")


@app.command("mcp-server")
def mcp_server() -> None:
    """Run the MCP server in the foreground (alternative to the console script)."""
    typer.echo("auladcanto mcp-server: TODO (phase 5)")


@app.command("verificar-deps")
def verificar_deps() -> None:
    """Check external tooling: ffmpeg, yt-dlp, demucs, basic-pitch, crepe."""
    typer.echo("auladcanto verificar-deps: TODO (phase 7)")


@app.command("limpar-cache")
def limpar_cache() -> None:
    """Prune cached gabaritos and intermediate audio artifacts."""
    typer.echo("auladcanto limpar-cache: TODO (phase 7)")


if __name__ == "__main__":  # pragma: no cover — module entry point
    app()
