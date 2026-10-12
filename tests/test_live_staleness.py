from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alan import live_staleness

_A_SHA = "a" * 40
_B_SHA = "b" * 40
_FUTURE_NOW = datetime.now(UTC) + timedelta(seconds=live_staleness.SETTLE_WINDOW_S + 3600)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return repo


def _commit(repo: Path, paths: list[str], message: str) -> str:
    for rel in paths:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _orphan_root(repo: Path, branch: str, paths: list[str], message: str) -> str:
    subprocess.run(
        ["git", "checkout", "--orphan", branch], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "rm", "-rf", "--cached", "."], cwd=repo, check=True, capture_output=True)
    return _commit(repo, paths, message)


def _set_served(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr("alan.live_staleness.served_sha_mod.served_sha", lambda: value)


def _kwargs(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "target_sha": _B_SHA,
        "main_ci_status": "completed",
        "deploy_status": "completed",
        "deploy_obs_empty": False,
        "now": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def test_detect_silence_no_served_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_served(monkeypatch, None)
    finding, reason = live_staleness.detect(**_kwargs())
    assert finding is None
    assert reason == "no_served_sha"


def test_detect_silence_target_not_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_served(monkeypatch, _A_SHA)
    finding, reason = live_staleness.detect(**_kwargs(target_sha="not-a-sha"))
    assert finding is None
    assert reason == "target_not_sha"


def test_detect_silence_target_none_is_not_a_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_served(monkeypatch, _A_SHA)
    finding, reason = live_staleness.detect(**_kwargs(target_sha=None))
    assert finding is None
    assert reason == "target_not_sha"


def test_detect_silence_target_non_string_is_not_a_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_served(monkeypatch, _A_SHA)
    finding, reason = live_staleness.detect(**_kwargs(target_sha=12345))
    assert finding is None
    assert reason == "target_not_sha"


def test_detect_no_divergence(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_served(monkeypatch, _A_SHA)
    finding, reason = live_staleness.detect(**_kwargs(target_sha=_A_SHA))
    assert finding is None
    assert reason == "no_divergence"


def test_detect_silence_main_ci_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_served(monkeypatch, _A_SHA)
    finding, reason = live_staleness.detect(**_kwargs(main_ci_status="in_progress"))
    assert finding is None
    assert reason == "main_ci_incomplete"


def test_detect_silence_deploy_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_served(monkeypatch, _A_SHA)
    finding, reason = live_staleness.detect(**_kwargs(deploy_obs_empty=True))
    assert finding is None
    assert reason == "deploy_unknown"


def test_detect_deploy_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_served(monkeypatch, _A_SHA)
    finding, reason = live_staleness.detect(**_kwargs(deploy_status="in_progress"))
    assert finding is None
    assert reason == "deploy_in_flight"


def test_detect_silence_target_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    served = _commit(repo, ["README.md"], "base")
    _set_served(monkeypatch, served)
    finding, reason = live_staleness.detect(
        **_kwargs(target_sha=_B_SHA, now=_FUTURE_NOW, repo=repo)
    )
    assert finding is None
    assert reason == "target_unreachable"


def test_detect_silence_not_ancestor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    served = _commit(repo, ["README.md"], "base")
    target = _orphan_root(repo, "unrelated", ["alan/server.py"], "unrelated history")
    _set_served(monkeypatch, served)
    finding, reason = live_staleness.detect(
        **_kwargs(target_sha=target, now=_FUTURE_NOW, repo=repo)
    )
    assert finding is None
    assert reason == "not_ancestor"


def test_detect_within_settle_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    served = _commit(repo, ["README.md"], "base")
    target = _commit(repo, ["alan/server.py"], "hot change just now")
    _set_served(monkeypatch, served)
    finding, reason = live_staleness.detect(
        **_kwargs(target_sha=target, now=datetime.now(UTC), repo=repo)
    )
    assert finding is None
    assert reason == "within_settle_window"


def test_detect_hot_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    served = _commit(repo, ["README.md"], "base")
    target = _commit(repo, ["alan/server.py"], "hot change")
    _set_served(monkeypatch, served)
    finding, reason = live_staleness.detect(
        **_kwargs(target_sha=target, now=_FUTURE_NOW, repo=repo)
    )
    assert reason == ""
    assert finding is not None
    assert finding.served_sha == served
    assert finding.target_sha == target
    assert finding.hot_paths == ["alan/server.py"]


def test_detect_hot_paths_filters_safe_paths_from_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    served = _commit(repo, ["README.md"], "base")
    target = _commit(repo, ["alan/server.py", "docs/x.md"], "mixed change")
    _set_served(monkeypatch, served)
    finding, reason = live_staleness.detect(
        **_kwargs(target_sha=target, now=_FUTURE_NOW, repo=repo)
    )
    assert reason == ""
    assert finding is not None
    assert finding.hot_paths == ["alan/server.py"]


def test_detect_safe_paths_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    served = _commit(repo, ["alan/server.py"], "base")
    target = _commit(repo, ["docs/x.md"], "safe change")
    _set_served(monkeypatch, served)
    finding, reason = live_staleness.detect(
        **_kwargs(target_sha=target, now=_FUTURE_NOW, repo=repo)
    )
    assert finding is None
    assert reason == "safe_paths_only"


def test_detect_no_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    served = _commit(repo, ["README.md"], "base")
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "empty"], cwd=repo, check=True
    )
    target = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _set_served(monkeypatch, served)
    finding, reason = live_staleness.detect(
        **_kwargs(target_sha=target, now=_FUTURE_NOW, repo=repo)
    )
    assert finding is None
    assert reason == "no_changes"


def test_detect_silence_error_on_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr("alan.live_staleness.served_sha_mod.served_sha", _boom)
    finding, reason = live_staleness.detect(**_kwargs())
    assert finding is None
    assert reason == "error"


def test_detect_never_invokes_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    served = _commit(repo, ["README.md"], "base")
    target = _commit(repo, ["alan/server.py"], "hot change")
    _set_served(monkeypatch, served)
    real_run = subprocess.run

    def _guarded_run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        argv0 = cmd[0] if isinstance(cmd, (list, tuple)) else cmd
        if argv0 == "gh":
            raise AssertionError(f"unexpected gh invocation: {cmd!r}")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guarded_run)
    live_staleness.detect(**_kwargs(target_sha=target, now=_FUTURE_NOW, repo=repo))
