from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import never_live_wait_bound as script  # type: ignore[import-not-found]  # noqa: E402

from alan.teams._never_live_budget import IDLE_WAIT_MARGIN_S


@pytest.fixture(autouse=True)
def _clear_budget_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_BUDGET_S", raising=False)


def test_main_prints_the_default_idle_wait_bound(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = script.main()
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == str(int(5400.0 - IDLE_WAIT_MARGIN_S))


def test_main_reflects_a_budget_override(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_BUDGET_S", "3600")
    script.main()
    out = capsys.readouterr().out.strip()
    assert out == str(int(3600.0 - IDLE_WAIT_MARGIN_S))


def test_the_script_runs_cleanly_as_a_standalone_process() -> None:
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "never_live_wait_bound.py"
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().isdigit()
