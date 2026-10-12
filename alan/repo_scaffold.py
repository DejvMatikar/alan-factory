from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ValidationError

from alan import (
    engine_config,
    github_repos,
    process_runner,
    scaffold_templates,
    secrets_store,
    store,
)
from alan.branch_protection_compat import (
    ApplyOutcome,
    Finding,
    apply_branch_protection,
)
from alan.engine_config import EngineConfig, ForgeConfig, ForgeTarget
from alan.forge import active_forge
from alan.scaffold_templates import StackTemplate
from alan.teams import _pr_push
from alan.teams._pr_state_paths import (
    _engine_state_dir as _engine_state_dir,  # pyright: ignore[reportPrivateUsage]
)

_STATE_FILE = "repo_onboarding.json"
_SCAFFOLD_BRANCH = "alan/repo-scaffold"
_SCAFFOLD_PR_TITLE = "chore: onboarding scaffold (CI, Dependabot, conventions)"
_SCAFFOLD_PR_BODY = (
    "Automated onboarding scaffold opened by alan: a stack-matched CI workflow, a "
    "Dependabot configuration, and a mechanically-assembled conventions document. "
    "Review and merge to continue onboarding."
)
_SCAFFOLD_COMMIT_MESSAGE = "chore: onboarding scaffold (CI, Dependabot, conventions)"
_CONVENTIONS_FILENAME = "CONVENTIONS.md"
_DEPENDABOT_PATH = (".github", "dependabot.yml")
_WORKFLOWS_DIR = (".github", "workflows")

_GH_TIMEOUT_S = 60.0
_CLONE_TIMEOUT_S = 120.0
_PR_URL_RE = re.compile(r"/pull/(\d+)")


class ScaffoldError(RuntimeError):
    pass


class OnboardingState(BaseModel, frozen=True):
    slug: str
    stack: str | None = None
    scaffold_pr_number: int | None = None
    scaffold_pr_url: str | None = None
    authoritative_checks: frozenset[str] = frozenset()
    protection_applied_keys: frozenset[str] = frozenset()
    protection_deferred: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class _OpenedPr:
    number: int
    url: str


def _require_forge(cfg: EngineConfig) -> ForgeConfig:
    if cfg.forge is None:
        raise ScaffoldError(f"engine {cfg.repo.slug!r} has no forge configured")
    return cfg.forge


def _forge_target(cfg: EngineConfig) -> ForgeTarget:
    return ForgeTarget.from_forge_config(_require_forge(cfg))


def _state_path(forge: ForgeTarget) -> Path:
    return _engine_state_dir(forge) / _STATE_FILE


def _persist_state(forge: ForgeTarget, state: OnboardingState) -> None:
    path = _state_path(forge)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        store.write_text_mkstemp(path, state.model_dump_json(), prefix=".repo_onboarding_")
    except OSError:
        pass


def _load_state(forge: ForgeTarget) -> OnboardingState | None:
    try:
        raw = _state_path(forge).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return OnboardingState.model_validate_json(raw)
    except ValidationError:
        return None


def onboarding_status(cfg: EngineConfig) -> OnboardingState:
    forge = _forge_target(cfg)
    return _load_state(forge) or OnboardingState(slug=forge.repo.slug)


def _author_token(forge: ForgeTarget) -> str:
    return (
        secrets_store.resolve_secret(
            forge.repo.slug, secrets_store.TRANSPORT_PURPOSE,
            env_fallback=forge.credential_env_name,
        )
        or ""
    ).strip()


def _parse_pr_row(parsed: object) -> _OpenedPr | None:
    if not isinstance(parsed, (list, tuple)) or not parsed:
        return None
    row = cast("list[object] | tuple[object, ...]", parsed)[0]
    if not isinstance(row, dict):
        return None
    row_dict = cast("dict[str, object]", row)
    number, url = row_dict.get("number"), row_dict.get("url")
    if not isinstance(number, int) or not isinstance(url, str):
        return None
    return _OpenedPr(number=number, url=url)


def _find_existing_pr(forge: ForgeTarget, branch: str) -> _OpenedPr | None:
    result = active_forge().prs_by_head(
        branch, ("number", "url"), forge_target=forge, limit=1, timeout=_GH_TIMEOUT_S,
    )
    return _parse_pr_row(result.rows) if result.outcome == "ok" else None


def _clone_repo(forge: ForgeTarget, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    remote_url = f"https://github.com/{forge.repo.slug}.git"
    result = process_runner.run_subprocess(
        ["git", *secrets_store.GIT_CRED_RESET_ARGS, "clone", "--", remote_url, str(dest)],
        cwd=None, timeout=_CLONE_TIMEOUT_S, env=secrets_store.transport_env(forge),
    )
    if result.returncode != 0:
        raise ScaffoldError(
            f"git clone failed (rc={result.returncode}): {(result.stderr or '')[:300]}"
        )


def _checkout_scaffold_branch(repo_path: Path) -> None:
    result = process_runner.run_subprocess(
        ["git", "checkout", "-b", _SCAFFOLD_BRANCH], cwd=str(repo_path), timeout=_GH_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise ScaffoldError(f"git checkout -b failed: {(result.stderr or '')[:300]}")


def _render_conventions(template_text: str, project_title: str, project_doc: str) -> str:
    header = template_text.format(project_title=project_title or "This project")
    doc_section = project_doc.strip() or "_No project doc yet._"
    return f"{header}\n\n## Project context\n\n{doc_section}\n"


def _write_scaffold_files(
    repo_path: Path, template: StackTemplate, project_title: str, project_doc: str
) -> None:
    workflows_dir = repo_path.joinpath(*_WORKFLOWS_DIR)
    workflows_dir.mkdir(parents=True, exist_ok=True)
    (workflows_dir / template.ci_workflow_filename).write_text(template.ci_yml, encoding="utf-8")
    repo_path.joinpath(*_DEPENDABOT_PATH).write_text(template.dependabot_yml, encoding="utf-8")
    conventions = _render_conventions(template.conventions_template, project_title, project_doc)
    (repo_path / _CONVENTIONS_FILENAME).write_text(conventions, encoding="utf-8")


def _run_scaffold_git_step(
    args: list[str], repo_path: Path, step: str
) -> subprocess.CompletedProcess[str]:
    result = process_runner.run_subprocess(args, cwd=str(repo_path), timeout=_GH_TIMEOUT_S)
    if result.returncode != 0:
        raise ScaffoldError(f"git {step} failed: {(result.stderr or '')[:300]}")
    return result


def _commit_scaffold_files(repo_path: Path) -> bool:
    _run_scaffold_git_step(["git", "add", "-A"], repo_path, "add")
    status = _run_scaffold_git_step(["git", "status", "--porcelain"], repo_path, "status")
    if not (status.stdout or "").strip():
        return False
    _run_scaffold_git_step(
        ["git", "-c", "user.email=alan-bot@users.noreply.github.com",
         "-c", "user.name=alan-bot", "commit", "-m", _SCAFFOLD_COMMIT_MESSAGE],
        repo_path, "commit",
    )
    return True


def _create_scaffold_pr(repo_path: Path, forge: ForgeTarget) -> _OpenedPr | None:
    result = active_forge().create_pr(
        _SCAFFOLD_PR_TITLE, _SCAFFOLD_PR_BODY, _SCAFFOLD_BRANCH,
        forge_target=forge, workdir=repo_path, timeout=_GH_TIMEOUT_S,
    )
    if result.returncode != 0:
        return None
    stdout = (result.stdout or "").strip()
    match = _PR_URL_RE.search(stdout)
    if match is None:
        return None
    return _OpenedPr(number=int(match.group(1)), url=stdout.splitlines()[-1].strip())


def _open_or_find_scaffold_pr(
    forge: ForgeTarget, template: StackTemplate, project_title: str, project_doc: str
) -> _OpenedPr:
    token = _author_token(forge)
    if not token:
        raise ScaffoldError(f"no transport credential for {forge.repo.slug!r}")
    found = _find_existing_pr(forge, _SCAFFOLD_BRANCH)
    if found is not None:
        return found
    with tempfile.TemporaryDirectory(prefix="alan-scaffold-") as tmp_dir:
        repo_path = Path(tmp_dir) / "repo"
        _clone_repo(forge, repo_path)
        _checkout_scaffold_branch(repo_path)
        _write_scaffold_files(repo_path, template, project_title, project_doc)
        if not _commit_scaffold_files(repo_path):
            raise ScaffoldError("scaffold produced no file changes to commit")
        push_result = _pr_push.push_branch(
            repo_path, _SCAFFOLD_BRANCH, f"repo_scaffold:{forge.repo.slug}", forge_target=forge,
        )
        if push_result.outcome is not _pr_push.PushOutcome.PUSHED:
            reason = push_result.detail or "no further detail from git"
            raise ScaffoldError(f"git push failed: {reason}")
        pr = _create_scaffold_pr(repo_path, forge)
    if pr is None:
        raise ScaffoldError("gh pr create failed for scaffold PR")
    return pr


def _union_authoritative_checks(
    cfg: EngineConfig, forge: ForgeConfig, checks: frozenset[str]
) -> frozenset[str]:
    unioned = forge.authoritative_checks | checks
    if unioned == forge.authoritative_checks:
        return unioned
    updated = cfg.model_copy(
        update={"forge": forge.model_copy(update={"authoritative_checks": unioned})}
    )
    try:
        engine_config.update_and_persist(updated, validate_env=False)
    except engine_config.EngineNotFoundError:
        pass
    return unioned


def scaffold(cfg: EngineConfig, *, project_title: str, project_doc: str) -> OnboardingState:
    forge = _forge_target(cfg)
    existing = _load_state(forge)
    if existing is not None and existing.scaffold_pr_number is not None:
        return existing
    stack = cfg.preset
    template = scaffold_templates.get_stack_template(stack)
    if template is None or stack is None:
        raise ScaffoldError(f"no scaffold template registered for preset {stack!r}")
    pr = _open_or_find_scaffold_pr(forge, template, project_title, project_doc)
    checks = _union_authoritative_checks(
        cfg, _require_forge(cfg), scaffold_templates.AUTHORITATIVE_CHECKS[stack]
    )
    state = OnboardingState(
        slug=forge.repo.slug,
        stack=stack,
        scaffold_pr_number=pr.number,
        scaffold_pr_url=pr.url,
        authoritative_checks=checks,
        protection_applied_keys=existing.protection_applied_keys if existing else frozenset(),
        protection_deferred=existing.protection_deferred if existing else (),
    )
    _persist_state(forge, state)
    return state


def protect(cfg: EngineConfig) -> OnboardingState:
    forge = _forge_target(cfg)
    outcomes: tuple[ApplyOutcome, ...] = apply_branch_protection(forge)
    existing = _load_state(forge) or OnboardingState(slug=forge.repo.slug)
    applied = frozenset(o.key for o in outcomes if o.status == "applied")
    deferred = tuple(o.finding for o in outcomes if o.finding is not None)
    state = existing.model_copy(
        update={"protection_applied_keys": applied, "protection_deferred": deferred}
    )
    _persist_state(forge, state)
    return state


def _finding(
    check: str, status: Literal["pass", "fail", "unknown"], message: str,
    *, remedy: str = "", remedy_url: str = "",
) -> Finding:
    return Finding(
        check=check, status=status, message=message, remedy=remedy, remedy_url=remedy_url
    )


def _check_push_repo(forge: ForgeTarget) -> Finding:
    try:
        env = secrets_store.transport_env(forge)
    except secrets_store.MissingSecretError:
        return _finding(
            "push_repository", "unknown", "cannot verify — no transport credential yet",
            remedy=(
                "push the repository's initial content to GitHub, then mint a "
                "transport credential"
            ),
        )
    remote_url = f"https://github.com/{forge.repo.slug}.git"
    try:
        result = subprocess.run(
            ["git", "ls-remote", remote_url, "HEAD"],
            capture_output=True, text=True, timeout=_GH_TIMEOUT_S, env={**os.environ, **env},
        )
    except (OSError, subprocess.TimeoutExpired):
        return _finding(
            "push_repository", "unknown", "git ls-remote raised",
            remedy="retry once network/git access is restored",
        )
    if result.returncode != 0 or not (result.stdout or "").strip():
        return _finding(
            "push_repository", "fail", "repository has no default-branch HEAD yet",
            remedy="push the existing repository's initial commit(s) to GitHub",
        )
    return _finding("push_repository", "pass", "repository reachable with a HEAD commit")


def _check_credentials(forge: ForgeTarget) -> Finding:
    try:
        secrets_store.transport_env(forge)
    except secrets_store.MissingSecretError as exc:
        return _finding(
            "mint_credentials", "fail", str(exc),
            remedy=(
                "write a transport secret (author PAT) and a reviewer secret (reviewer "
                "PAT) for this repo via the secrets store"
            ),
        )
    reviewer_token = secrets_store.resolve_secret(
        forge.repo.slug, secrets_store.REVIEWER_PURPOSE, env_fallback=None
    )
    if not reviewer_token:
        return _finding(
            "mint_credentials", "fail", "no reviewer credential configured",
            remedy=(
                "write a reviewer secret for this repo via the secrets store, distinct "
                "from the transport credential"
            ),
        )
    return _finding("mint_credentials", "pass", "author and reviewer credentials configured")


def _check_workflow_write_capability(forge: ForgeTarget) -> Finding:
    token = _author_token(forge)
    if not token:
        return _finding(
            "workflow_write", "unknown", "cannot verify — no transport credential yet",
            remedy="mint a transport credential, then this check runs",
        )
    scope = github_repos.probe_workflow_scope(token)
    if scope == "present":
        return _finding(
            "workflow_write", "pass", "transport credential has the 'workflow' scope",
        )
    if scope == "absent":
        return _finding(
            "workflow_write", "fail",
            (
                "this credential is a classic personal access token without the "
                "'workflow' scope; the setup action always writes a CI workflow file "
                "and GitHub rejects workflow-file pushes from tokens lacking it"
            ),
            remedy=(
                "regenerate the token with the 'workflow' checkbox ticked, then save "
                "it again under the same repository and purpose"
            ),
        )
    return _finding(
        "workflow_write", "unknown",
        "cannot verify workflow-file write permission for this credential",
        remedy=(
            "if this is a fine-grained personal access token, grant it 'Workflows' "
            "write access under repository permissions — alan cannot probe a "
            "fine-grained token's scopes without writing to the repository itself"
        ),
    )


def _check_collaborator_access() -> Finding:
    return _finding(
        "grant_reviewer_collaborator_access", "unknown",
        "alan cannot verify collaborator permissions from here",
        remedy="grant the reviewer bot account collaborator access (push) on the repository",
    )


def _view_scaffold_pr_state(forge: ForgeTarget, pr_number: int) -> tuple[str | None, str]:
    try:
        result = active_forge().pr_state(pr_number, forge_target=forge, timeout=_GH_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None, "gh pr view raised"
    if result.returncode != 0:
        return None, (result.stderr or "")[:300] or "gh pr view failed"
    return (result.stdout or "").strip(), ""


def _check_scaffold_pr_merged(forge: ForgeTarget, state: OnboardingState) -> Finding:
    if state.scaffold_pr_number is None:
        return _finding(
            "review_and_merge_scaffold_pr", "fail", "scaffold has not been run yet",
            remedy="run the scaffold action to open the scaffold pull request",
        )
    remedy_url = state.scaffold_pr_url or ""
    token = _author_token(forge)
    if not token:
        return _finding(
            "review_and_merge_scaffold_pr", "unknown",
            "no credential available to check PR state",
            remedy="configure a transport credential, then retry", remedy_url=remedy_url,
        )
    pr_state, error = _view_scaffold_pr_state(forge, state.scaffold_pr_number)
    if pr_state is None:
        return _finding(
            "review_and_merge_scaffold_pr", "unknown", error,
            remedy="retry once network/gh access is restored", remedy_url=remedy_url,
        )
    if pr_state == "MERGED":
        return _finding(
            "review_and_merge_scaffold_pr", "pass",
            f"PR #{state.scaffold_pr_number} merged", remedy_url=remedy_url,
        )
    return _finding(
        "review_and_merge_scaffold_pr", "fail",
        f"PR #{state.scaffold_pr_number} not yet merged (state={pr_state or 'unknown'})",
        remedy="review and merge the scaffold pull request", remedy_url=remedy_url,
    )


def build_checklist(cfg: EngineConfig, state: OnboardingState) -> tuple[Finding, ...]:
    forge = _forge_target(cfg)
    boundary = (
        _check_push_repo(forge),
        _check_credentials(forge),
        _check_workflow_write_capability(forge),
        _check_collaborator_access(),
        _check_scaffold_pr_merged(forge, state),
    )
    return boundary + state.protection_deferred


def overall_status(checklist: tuple[Finding, ...]) -> Literal["pass", "fail", "unknown"]:
    statuses = {f.status for f in checklist}
    if "fail" in statuses:
        return "fail"
    if "unknown" in statuses:
        return "unknown"
    return "pass"
