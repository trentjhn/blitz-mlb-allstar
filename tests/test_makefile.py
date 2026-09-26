"""make serve: the port the README states unless the command line picks one, and no install."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def dry_run(*args: str, env: dict[str, str] | None = None) -> str:
    """The commands make serve would run, without running them."""
    result = subprocess.run(
        ["make", "-n", *args, "serve"],
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_serve_ignores_a_port_from_the_environment():
    assert "http.server 8080 --bind 127.0.0.1" in dry_run(env={"PORT": "3000"})


def test_serve_takes_a_port_from_the_command_line():
    assert "http.server 3000 --bind 127.0.0.1" in dry_run("PORT=3000")


def test_serve_never_installs_even_when_the_requirements_look_newer():
    # -W treats requirements.txt as just changed, which is what makes the install targets run.
    assert "pip" not in dry_run("-W", "requirements.txt")
