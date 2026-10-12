from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from alan import agent_key, claude_cli_grants, launch_spec, operator_prompt, operator_repos, roots
from alan.driver_loop_cmd import env_policy
from alan.engine_config import RepoIdentity
from alan.launch_spec import EnvEntry, LaunchSpec, ProcessSpec
from alan.operator_prompt import Scope

CLAUDE_EXECUTABLE: Final[str] = launch_spec.CLAUDE_EXECUTABLE
_DOCS_DIRNAME: Final[str] = "docs"
_TERM_ENTRY: Final[EnvEntry] = EnvEntry("TERM", "fixed_override")


@dataclass(frozen=True)
class LaunchPlan:
    cwd: Path
    argv: tuple[str, ...]
    warning: str | None


def operator_spec(
    scope: Scope, prompt: str | None, add_dirs: Sequence[Path]
) -> LaunchSpec:
    return LaunchSpec(
        layout="operator",
        agent=agent_key.operator_session(scope.value),
        prompt_arg=prompt,
        add_dirs=tuple(str(path.absolute()) for path in add_dirs),
        denied=tuple(claude_cli_grants.credential_deny_rules()),
        process=ProcessSpec(detached=True, env=(*env_policy(), _TERM_ENTRY)),
    )


def _argv(scope: Scope, prompt: str | None, add_dirs: Sequence[Path]) -> tuple[str, ...]:
    return tuple(launch_spec.to_argv(operator_spec(scope, prompt, add_dirs)))


def global_plan() -> LaunchPlan:
    checkouts = operator_repos.ensure_all()
    add_dirs = (roots.workspaces_root(), roots.logs_root(), roots.state_root())
    argv = _argv(Scope.FLEET, None, add_dirs)
    return LaunchPlan(cwd=checkouts.path, argv=argv, warning=checkouts.warning)


def repo_plan(identity: RepoIdentity) -> LaunchPlan:
    target = operator_repos.target_for(identity, operator_repos.optional_install_target())
    checkout = operator_repos.ensure_repo(target)
    return LaunchPlan(
        cwd=checkout.path, argv=_argv(Scope.REPO, None, ()), warning=checkout.warning
    )


def workspace_plan(identity: RepoIdentity, *, task_id: str, workspace_dir: Path) -> LaunchPlan:
    install = operator_repos.install_target()
    install_checkout = operator_repos.ensure_repo(install)
    target = operator_repos.target_for(identity, install)
    shared = target == install
    checkout = install_checkout if shared else operator_repos.ensure_repo(target)
    docs_dir = install_checkout.path / _DOCS_DIRNAME
    logs_dir = roots.logs_root()
    prompt = operator_prompt.workspace_prompt(
        task_id=task_id,
        repo_slug=target.identity.slug,
        workspace_dir=workspace_dir.absolute(),
        logs_dir=logs_dir.absolute(),
        docs_dir=docs_dir.absolute(),
    )
    add_dirs = [workspace_dir, logs_dir, *([] if shared else [docs_dir])]
    docs_warning = operator_repos.prefixed(install.identity, install_checkout.warning)
    warning = operator_repos.combine_warnings([checkout.warning, None if shared else docs_warning])
    argv = _argv(Scope.WORKSPACE, prompt, add_dirs)
    return LaunchPlan(cwd=checkout.path, argv=argv, warning=warning)


__all__ = [
    "CLAUDE_EXECUTABLE",
    "LaunchPlan",
    "global_plan",
    "operator_spec",
    "repo_plan",
    "workspace_plan",
]
