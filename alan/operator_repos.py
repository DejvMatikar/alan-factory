from __future__ import annotations

import os
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from alan import engine_config, operator_checkout
from alan.engine_config import RepoIdentity
from alan.operator_checkout import CheckoutCloneFailed, CheckoutResult, redact

ROOT_SUFFIX: Final[str] = "-operator-repos"
MAX_WARNINGS: Final[int] = 5
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_ORIGIN_SLUG = re.compile(r"(?:^|[/:])(?P<owner>[^/:@]+)/(?P<repo>[^/:]+?)(?:\.git)?/?$")
_WARNING_SEPARATOR: Final[str] = "; "


@dataclass(frozen=True)
class RepoTarget:
    identity: RepoIdentity
    clone_url: str
    migrates_legacy: bool


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _is_unsafe_candidate(candidate: Path, homes: set[Path], installs: set[Path]) -> bool:
    return any(candidate.is_relative_to(home) for home in homes) or any(
        candidate.is_relative_to(install) or candidate == install.parent for install in installs
    )


def _is_unsafe_root(root: Path, install: Path) -> bool:
    alan_home = Path.home() / ".alan"
    homes = {alan_home, _real(alan_home)}
    installs = {install, _real(install)}
    candidates = {root, _real(root)}
    return any(_is_unsafe_candidate(candidate, homes, installs) for candidate in candidates)


def _refuse_unsafe(path: Path, install: Path) -> None:
    if _is_unsafe_root(path, install):
        raise CheckoutCloneFailed(
            redact(f"{path} cannot hold the operator checkouts: it is the install, inside it, "
                   "its parent, or under ~/.alan")
        )


def operator_root() -> Path:
    install = operator_checkout.install_root()
    root = install.parent / f"{install.name}{ROOT_SUFFIX}"
    _refuse_unsafe(root, install)
    return root


def repo_path(identity: RepoIdentity) -> Path:
    for part in (identity.owner, identity.repo):
        if _COMPONENT.fullmatch(part) is None:
            raise CheckoutCloneFailed(redact(f"{identity.slug!r} is not a safe directory name"))
    path = operator_root() / identity.owner / identity.repo
    _refuse_unsafe(path, operator_checkout.install_root())
    return path


def same_repo(left: RepoIdentity, right: RepoIdentity) -> bool:
    return left.slug.lower() == right.slug.lower()


def _registered_identity(parsed: RepoIdentity) -> RepoIdentity:
    registered = (
        cfg.repo for cfg in engine_config.registered_engines() if same_repo(cfg.repo, parsed)
    )
    return next(registered, parsed)


def install_target() -> RepoTarget:
    url = operator_checkout.install_origin_url()
    match = _ORIGIN_SLUG.search(url)
    if match is None:
        raise CheckoutCloneFailed(
            "the install's origin remote does not name an owner and a repository"
        )
    parsed = RepoIdentity(owner=match["owner"], repo=match["repo"])
    return RepoTarget(identity=_registered_identity(parsed), clone_url=url, migrates_legacy=True)


def optional_install_target() -> RepoTarget | None:
    try:
        return install_target()
    except CheckoutCloneFailed:
        return None


def _github_target(identity: RepoIdentity) -> RepoTarget:
    return RepoTarget(
        identity=identity,
        clone_url=f"https://github.com/{identity.slug}.git",
        migrates_legacy=False,
    )


def target_for(identity: RepoIdentity, install: RepoTarget | None) -> RepoTarget:
    if install is not None and same_repo(install.identity, identity):
        return install
    return _github_target(identity)


def combine_warnings(warnings: Sequence[str | None]) -> str | None:
    kept = [warning for warning in warnings if warning]
    if len(kept) > MAX_WARNINGS:
        kept = [*kept[:MAX_WARNINGS], f"and {len(kept) - MAX_WARNINGS} more"]
    return _WARNING_SEPARATOR.join(kept) or None


def prefixed(identity: RepoIdentity, warning: str | None) -> str | None:
    return redact(f"{identity.slug}: {warning}") if warning else None


def _migrate_legacy(target: Path) -> str | None:
    legacy = operator_checkout.legacy_checkout_path()
    if not (legacy / ".git").exists():
        return None
    if target.exists():
        return redact(f"legacy operator checkout {legacy} left in place: {target} already exists")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        legacy.rename(target)
    except OSError as exc:
        return redact(f"legacy operator checkout {legacy} not moved: {exc.strerror}")
    return None


def _clone_fresh(url: str, path: Path) -> None:
    existed = path.exists()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CheckoutCloneFailed(
            redact(f"cannot create {path.parent}: {exc.strerror or type(exc).__name__}")
        ) from exc
    try:
        operator_checkout.clone(url, path)
    except CheckoutCloneFailed:
        if not existed:
            shutil.rmtree(path, ignore_errors=True)
        raise


def ensure_repo(target: RepoTarget) -> CheckoutResult:
    path = repo_path(target.identity)
    migration_warning = _migrate_legacy(path) if target.migrates_legacy else None
    if (path / ".git").exists():
        update_warning = operator_checkout.fast_forward(path)
        return CheckoutResult(path, combine_warnings([migration_warning, update_warning]))
    _clone_fresh(target.clone_url, path)
    return CheckoutResult(path, migration_warning)


def _ensure_quietly(target: RepoTarget) -> str | None:
    try:
        result = ensure_repo(target)
    except CheckoutCloneFailed as exc:
        return prefixed(target.identity, str(exc))
    return prefixed(target.identity, result.warning)


def _other_targets(install: RepoTarget) -> list[RepoTarget]:
    others: dict[str, RepoTarget] = {}
    for cfg in engine_config.registered_engines():
        if not cfg.hidden and not same_repo(cfg.repo, install.identity):
            others.setdefault(cfg.repo.slug.lower(), _github_target(cfg.repo))
    return list(others.values())


def ensure_all() -> CheckoutResult:
    root = operator_root()
    install = install_target()
    warnings = [prefixed(install.identity, ensure_repo(install).warning)]
    warnings.extend(_ensure_quietly(target) for target in _other_targets(install))
    return CheckoutResult(root, combine_warnings(warnings))


__all__ = [
    "MAX_WARNINGS",
    "ROOT_SUFFIX",
    "RepoTarget",
    "combine_warnings",
    "ensure_all",
    "ensure_repo",
    "install_target",
    "operator_root",
    "optional_install_target",
    "prefixed",
    "repo_path",
    "same_repo",
    "target_for",
]
