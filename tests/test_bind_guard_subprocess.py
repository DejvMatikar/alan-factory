from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REMOVED_OPT_IN = "ALAN_ALLOW_NON_LOOPBACK_BIND"
_SERVER_IMPORTED_MARKER = "SERVER_IMPORTED="
_WRAPPER = """
import sys

import uvicorn


def _uvicorn_must_not_run(*args, **kwargs):
    raise RuntimeError("uvicorn.run was reached")


uvicorn.run = _uvicorn_must_not_run
sys.argv = ["alan", *sys.argv[1:]]
from alan import __main__ as entrypoint

try:
    entrypoint.main()
except SystemExit:
    print("SERVER_IMPORTED=" + str("alan.server" in sys.modules))
    raise
"""


def _run_entrypoint(argv: list[str], extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ALAN_HOST", _REMOVED_OPT_IN}
    }
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", _WRAPPER, *argv],
        env=env,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_refused_flag_host_exits_non_zero_before_the_app_loads() -> None:
    result = _run_entrypoint(["--host", "0.0.0.0", "--port", "0"], {})  # noqa: S104
    assert result.returncode == 1
    assert "'0.0.0.0'" in result.stderr
    assert "from --host" in result.stderr
    assert _REMOVED_OPT_IN not in result.stderr
    assert "uvicorn.run was reached" not in result.stderr
    assert f"{_SERVER_IMPORTED_MARKER}False" in result.stdout


def test_refused_env_host_exits_non_zero_before_the_app_loads() -> None:
    result = _run_entrypoint(["--port", "0"], {"ALAN_HOST": "10.20.30.40"})
    assert result.returncode == 1
    assert "'10.20.30.40'" in result.stderr
    assert "from ALAN_HOST" in result.stderr
    assert _REMOVED_OPT_IN not in result.stderr
    assert "uvicorn.run was reached" not in result.stderr
    assert f"{_SERVER_IMPORTED_MARKER}False" in result.stdout


def test_refused_env_host_still_exits_non_zero_with_the_removed_opt_in_set() -> None:
    result = _run_entrypoint(
        ["--port", "0"], {"ALAN_HOST": "10.20.30.40", _REMOVED_OPT_IN: "container"}
    )
    assert result.returncode == 1
    assert "'10.20.30.40'" in result.stderr
    assert "from ALAN_HOST" in result.stderr
    assert _REMOVED_OPT_IN not in result.stderr
    assert "uvicorn.run was reached" not in result.stderr
    assert f"{_SERVER_IMPORTED_MARKER}False" in result.stdout
