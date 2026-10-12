from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VIOLATING_SOURCE = "import os\nx=1\n"


def _ruff_report(filename: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--stdin-filename", filename, "-"],
        input=_VIOLATING_SOURCE,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_private_main_release_metadata() -> None:
    config = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "release" in config["tool"]["pytest"]["ini_options"]["norecursedirs"]
    assert "release" in config["tool"]["ruff"]["extend-exclude"]
    assert config["tool"]["ruff"]["force-exclude"] is True
    assert config["project"]["license"] == "MIT"
    assert "subscription" not in config["project"]["description"].lower()

    licence = (_REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License")
    assert re.search(r"^Copyright \(c\) \d{4} \S+$", licence, re.MULTILINE)

    assert _ruff_report("release/probe.py").returncode == 0
    assert _ruff_report("probe.py").returncode == 1
