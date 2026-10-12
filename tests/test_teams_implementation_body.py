from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan import process_runner
from alan.teams import _pr_body, _pr_handover
from alan.teams import implementation as impl_mod
from alan.workspace import Workspace, create
from alan.worktree import WorktreeHandle


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("body-task", "ship the feature", root=tmp_path / "ws")


def _handle(worktree: Path, branch: str) -> WorktreeHandle:
    return WorktreeHandle(path=worktree, base_ref="HEAD", branch=branch)


def test_render_system_prompt_default_engine_includes_pr_body_instructions(
    tmp_path: Path,
) -> None:
    from alan.engine_config import _DEFAULT as engine_default

    prompt = impl_mod.render_system_prompt(engine_default, tmp_path)
    assert _pr_body.PR_BODY_BLOCK_INSTRUCTIONS in prompt


def test_render_system_prompt_generic_engine_includes_pr_body_instructions(
    tmp_path: Path,
) -> None:
    from alan.engine_config import EngineConfig, Posture, RepoIdentity

    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
        gate_commands=[["cargo", "test"]],
        warmup_commands=[["cargo", "fetch"]],
        posture=Posture.autonomous,
    )
    prompt = impl_mod.render_system_prompt(cfg, tmp_path)
    assert _pr_body.PR_BODY_BLOCK_INSTRUCTIONS in prompt


def test_open_pr_uses_authored_body_when_block_present(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_subprocess(
        cmd: list[str], *, cwd: str | None = None, timeout: float = 0, **_k: object,
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://github.com/acme/widget/pull/7\n", stderr="",
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run_subprocess)
    synthesis = (
        "## Summary\nDid the thing.\n\n"
        "```pr_body\n## Why\nExplains the change.\n```\n"
    )
    pr_number = impl_mod._open_pr(  # noqa: SLF001
        _handle(tmp_path, "teams/body-task-inv1"), workspace, synthesis,
    )
    assert pr_number == 7
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    body_index = cmd.index("--body") + 1
    assert cmd[body_index] == "## Why\nExplains the change."


def test_open_pr_falls_back_to_derived_body_without_block(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_subprocess(
        cmd: list[str], *, cwd: str | None = None, timeout: float = 0, **_k: object,
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://github.com/acme/widget/pull/8\n", stderr="",
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run_subprocess)
    synthesis = "## Summary\nDid the thing without a body block."
    pr_number = impl_mod._open_pr(  # noqa: SLF001
        _handle(tmp_path, "teams/body-task-inv1"), workspace, synthesis,
    )
    assert pr_number == 8
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    body_index = cmd.index("--body") + 1
    assert cmd[body_index] == _pr_body.build_pr_body(synthesis)


def test_open_pr_appends_fixes_line_for_declared_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create(
        "closing-task", "ship the feature\n\nDelivers-Issues: 42", root=tmp_path / "ws-closing",
    )
    captured: dict[str, object] = {}

    def fake_run_subprocess(
        cmd: list[str], *, cwd: str | None = None, timeout: float = 0, **_k: object,
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://github.com/acme/widget/pull/10\n", stderr="",
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run_subprocess)
    synthesis = "## Summary\nDid the thing."
    pr_number = impl_mod._open_pr(  # noqa: SLF001
        _handle(tmp_path, "teams/closing-task-inv1"), ws, synthesis,
    )
    assert pr_number == 10
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    body_index = cmd.index("--body") + 1
    assert cmd[body_index].endswith("Fixes #42")


def test_open_pr_salvaged_summary_body_carries_fixes_line_with_single_gh_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create(
        "closing-task", "ship the feature\n\nDelivers-Issues: 42", root=tmp_path / "ws-salvage",
    )
    calls: list[list[str]] = []

    def fake_run_subprocess(
        cmd: list[str], *, cwd: str | None = None, timeout: float = 0, **_k: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://github.com/acme/widget/pull/11\n", stderr="",
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run_subprocess)
    synthesis = _pr_handover.with_salvage_notice(
        "## Summary\nSalvaged commits shipped without a body block.", "salvaged",
    )
    pr_number = impl_mod._open_pr(  # noqa: SLF001
        _handle(tmp_path, "teams/closing-task-inv1"), ws, synthesis,
    )
    assert pr_number == 11
    gh_calls = [c for c in calls if c[0] == "gh"]
    assert len(gh_calls) == 1
    cmd = gh_calls[0]
    body_index = cmd.index("--body") + 1
    assert cmd[body_index].endswith("Fixes #42")


def test_open_pr_truncates_an_oversized_authored_body(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_subprocess(
        cmd: list[str], *, cwd: str | None = None, timeout: float = 0, **_k: object,
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://github.com/acme/widget/pull/9\n", stderr="",
        )

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run_subprocess)
    oversized = "z" * (_pr_body.PR_BODY_MAX_CHARS + 5000)
    synthesis = f"## Summary\nDid the thing.\n\n```pr_body\n{oversized}\n```\n"
    impl_mod._open_pr(_handle(tmp_path, "teams/body-task-inv1"), workspace, synthesis)  # noqa: SLF001
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    body_index = cmd.index("--body") + 1
    assert len(cmd[body_index]) <= _pr_body.PR_BODY_MAX_CHARS
