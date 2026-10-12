from __future__ import annotations

import copy
import os
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from alan import default_profile, engine_registry_store, roots
from alan.control_plane import CONTROL_TOKEN_ENV
from alan.default_profile_writer import GATE_CHECK
from alan.engine_config_validation import (
    CacheEnvValidationError as CacheEnvValidationError,
)
from alan.engine_config_validation import (
    CheckAppId,
    check_posture_orthogonality,
    validate_cache_env,
    validate_flow_graph,
    validate_forge_credential_env_name,
)
from alan.engine_config_validation import (
    FlowGraphPolicy as FlowGraphPolicy,
)
from alan.engine_config_validation import (
    FlowGraphValidationError as FlowGraphValidationError,
)
from alan.engine_config_validation import (
    ForgeConfigValidationError as ForgeConfigValidationError,
)
from alan.engine_config_validation import (
    Posture as Posture,
)
from alan.engine_config_validation import (
    PostureOrthogonalityError as PostureOrthogonalityError,
)
from alan.repo_accent import AccentChoice
from alan.roots import STATE_ISOLATION_ROOTS

_POSTURE_RANK: dict[Posture, int] = {
    Posture.observe: 0,
    Posture.propose: 1,
    Posture.self_merge: 2,
    Posture.autonomous: 3,
}


def posture_rank(posture: Posture) -> int:
    return _POSTURE_RANK[posture]


def live_floored_posture(snapshot_posture: Posture, engine_key: str | None) -> Posture:
    # Read fresh under _registry_lock on every call, never cached — a downgrade
    # must floor the very next enforcement point, not wait for re-dispatch.
    if engine_key is None:
        return snapshot_posture
    live_cfg = lookup_by_slug(engine_key)
    if live_cfg is None:
        return snapshot_posture
    return min(snapshot_posture, live_cfg.posture, key=posture_rank)


def live_hidden(engine_key: str | None) -> bool:
    # Read fresh under _registry_lock on every call, never cached — mirrors
    # live_floored_posture: a workspace's engine_snapshot is stamped once (on
    # first dispatch) and never refreshed, so a hide that happens afterward
    # must still be visible at the very next enforcement point.
    if engine_key is None:
        return False
    live_cfg = lookup_by_slug(engine_key)
    if live_cfg is None:
        return False
    return live_cfg.hidden


_TEAM_MIN_POSTURE: dict[str, Posture] = {
    "research": Posture.propose,
    "brainstorming": Posture.propose,
    "brief_refine": Posture.propose,
    "architecture": Posture.propose,
    "implementation_plan": Posture.propose,
    "decomposer": Posture.propose,
    "implementation": Posture.propose,
    "implementation_fix": Posture.propose,
    "opportunity_scout": Posture.propose,
    "ui_review": Posture.propose,
    "verification": Posture.self_merge,
    "pr_merge": Posture.self_merge,
    "triage": Posture.autonomous,
    "dependency_sweep": Posture.autonomous,
    "deploy_verify": Posture.autonomous,
    "maintenance": Posture.autonomous,
}


def admitted_teams(all_teams: set[str], posture: Posture) -> set[str]:
    rank = posture_rank(posture)
    result: set[str] = set()
    for t in all_teams:
        min_p = _TEAM_MIN_POSTURE.get(t)
        if min_p is not None and posture_rank(min_p) <= rank:
            result.add(t)
    return result


class RepoIdentity(BaseModel, frozen=True):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @classmethod
    def from_slug(cls, s: str) -> RepoIdentity:
        owner, sep, repo = s.partition("/")
        if not sep or not owner or not repo:
            raise ValueError(f"invalid slug: {s!r}")
        return cls(owner=owner, repo=repo)


class CodeQualityConfig(BaseModel, frozen=True):
    project_key: str = Field(min_length=1)
    organization: str = Field(min_length=1)
    host_url: str = Field(min_length=1)
    token_env_var: str = Field(min_length=1)


class RoleConfig(BaseModel, frozen=True):
    model: str = Field(min_length=1)
    effort: str | None = None


class PromptOverride(BaseModel, frozen=True):
    text: str
    # sha256 hex digest of the (team, role)'s raw pre-substitution base prompt
    # text at the time this override was saved. Unused by the foundation-stage
    # renderer; seeded from day one so the deferred drift-indicator stage needs
    # no migration to start comparing it against the current base's fingerprint.
    base_fingerprint: str


class ForgeConfig(BaseModel, frozen=True):
    repo: RepoIdentity
    credential_env_name: str = Field(min_length=1)
    authoritative_checks: frozenset[str] = Field(default_factory=frozenset)
    aggregate_checks: frozenset[str] = Field(default_factory=frozenset)
    authoritative_check_apps: dict[str, CheckAppId] = Field(default_factory=dict)
    enforce_admins: StrictBool = False

    @field_validator("credential_env_name")
    @classmethod
    def _validate_credential_env_name(cls, v: str) -> str:
        validate_forge_credential_env_name(v)
        return v


class CiConfig(BaseModel, frozen=True):
    main_ci_workflow: str = Field(min_length=1)
    deploy_workflow: str | None = Field(default=None, min_length=1)


_DEFAULT_DEP_SWEEP_CADENCE_S = 7 * 24 * 3600

SCENARIO_TIMEOUT_DEFAULT: float = 120.0
AGGREGATE_BUDGET_DEFAULT: float = 600.0


class DependencySweepConfig(BaseModel, frozen=True):
    enabled: bool = False
    cadence_seconds: int = Field(default=_DEFAULT_DEP_SWEEP_CADENCE_S, gt=0)
    enable_python_vuln_scan: bool = False


class ScenarioConfig(BaseModel, frozen=True):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command_prefixes: list[list[str]] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    timeout_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    aggregate_budget_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check_budget_covers_timeout(self) -> ScenarioConfig:
        effective_timeout = (
            self.timeout_s if self.timeout_s is not None else SCENARIO_TIMEOUT_DEFAULT
        )
        effective_budget = (
            self.aggregate_budget_s
            if self.aggregate_budget_s is not None
            else AGGREGATE_BUDGET_DEFAULT
        )
        if effective_budget < effective_timeout:
            raise ValueError(
                f"scenario.aggregate_budget_s (resolved {effective_budget}) must be >= "
                f"scenario.timeout_s (resolved {effective_timeout})"
            )
        return self


class EngineConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: RepoIdentity
    code_quality: CodeQualityConfig | None = None
    forge: ForgeConfig | None = None
    ci: CiConfig | None = None
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    gate_commands: list[list[str]] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    warmup_commands: list[list[str]] = Field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    cache_env: dict[str, str] = Field(default_factory=dict)
    base_tools: list[str] = Field(default_factory=list)
    team_tools: dict[str, list[str]] = Field(default_factory=dict)
    transition_graph: dict[str, list[str]] = Field(default_factory=dict)
    flow_policy: FlowGraphPolicy | None = None
    posture: Posture = Posture.observe
    hidden: bool = False
    hidden_restore_posture: Posture | None = None
    pool_size: int | None = Field(default=None, gt=0)
    preset: str | None = Field(default=None)
    # Sparse pinned overrides, keyed "<team>/<role>" (alan.prompt_templates.override_key).
    # Only the (team, role) pairs in alan.prompt_templates.OVERRIDABLE_ROLES are ever
    # populated here — enforced at the route layer, not by this model.
    prompt_overrides: dict[str, PromptOverride] = Field(default_factory=dict)
    dependency_sweep: DependencySweepConfig = Field(default_factory=DependencySweepConfig)
    accent: AccentChoice | None = None
    scenario: ScenarioConfig = Field(default_factory=ScenarioConfig)
    unblock_enabled: bool = True


_RESERVED_SANDBOX_ROOT_ENVS: frozenset[str] = STATE_ISOLATION_ROOTS

_CACHE_ISOLATION_OVERRIDES: dict[str, str] = {
    "UV_CACHE_DIR": "ALAN_SLOT_UV_CACHE",
    "npm_config_cache": "ALAN_SLOT_NPM_CACHE",
}


def _slot_cache_base() -> Path:
    return roots.cache_root()


def resolve_cache_env(cache_env: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, subdir in cache_env.items():
        override = _CACHE_ISOLATION_OVERRIDES.get(name)
        raw = os.environ.get(override, "").strip() if override else ""
        out[name] = raw or str(_slot_cache_base() / subdir)
    return out


class ForgeTarget(BaseModel, frozen=True):
    repo: RepoIdentity
    credential_env_name: str = Field(min_length=1)
    authoritative_checks: frozenset[str] = Field(default_factory=frozenset)
    aggregate_checks: frozenset[str] = Field(default_factory=frozenset)
    authoritative_check_apps: dict[str, CheckAppId] = Field(default_factory=dict)
    enforce_admins: StrictBool = False

    @field_validator("credential_env_name")
    @classmethod
    def _validate_credential_env_name(cls, v: str) -> str:
        validate_forge_credential_env_name(v)
        return v

    @classmethod
    def from_forge_config(cls, forge: ForgeConfig) -> ForgeTarget:
        return cls(
            repo=forge.repo,
            credential_env_name=forge.credential_env_name,
            authoritative_checks=forge.authoritative_checks,
            aggregate_checks=forge.aggregate_checks,
            authoritative_check_apps=forge.authoritative_check_apps,
            enforce_admins=forge.enforce_admins,
        )


def is_authoritative_check(target: ForgeTarget, check_name: str) -> bool:
    return check_name in target.authoritative_checks


class MissingEnvVarsError(ValueError):
    pass


class DuplicateEngineError(ValueError):
    pass


class EngineNotFoundError(ValueError):
    pass


class PostureCeilingError(ValueError):
    pass


def _secret_env_vars(config: EngineConfig) -> list[str]:
    names: list[str] = []
    if config.code_quality is not None:
        names.append(config.code_quality.token_env_var)
    if config.forge is not None:
        names.append(config.forge.credential_env_name)
    return names


_registry: dict[RepoIdentity, EngineConfig] = {}
_default_key: RepoIdentity | None = None
_registry_lock = threading.RLock()

_file_ceilings: dict[RepoIdentity, Posture] = {}

_fallback_ceiling: Posture = Posture.autonomous


@dataclass(frozen=True)
class QuarantinedConfig:
    path: Path
    error: str
    repo: RepoIdentity | None


_quarantined_configs: list[QuarantinedConfig] = []


def _check_env_vars(config: EngineConfig) -> None:
    missing = [v for v in _secret_env_vars(config) if v not in os.environ]
    if missing:
        raise MissingEnvVarsError(
            f"Engine config references undefined env vars: {missing!r}"
        )


def _validate_before_register(config: EngineConfig, *, validate_env: bool) -> None:
    if validate_env:
        _check_env_vars(config)
    validate_cache_env(
        config.cache_env,
        _RESERVED_SANDBOX_ROOT_ENVS
        | registered_secret_env_names()
        | {CONTROL_TOKEN_ENV, "PATH", "HOME"},
    )
    if config.flow_policy is not None:
        validate_flow_graph(config.transition_graph, config.flow_policy)
        check_posture_orthogonality(_TEAM_MIN_POSTURE, config.flow_policy)


def _check_duplicate_locked(repo: RepoIdentity) -> None:
    if repo in _registry:
        raise DuplicateEngineError(f"Engine for {repo.slug!r} is already registered")


def _check_exists_locked(repo: RepoIdentity) -> None:
    if repo not in _registry:
        raise EngineNotFoundError(f"Engine for {repo.slug!r} is not registered")


def _insert_locked(config: EngineConfig, *, default: bool) -> None:
    global _default_key
    _registry[config.repo] = config
    if default:
        _default_key = config.repo


def register(
    config: EngineConfig, *, default: bool = False, validate_env: bool = True
) -> None:
    _validate_before_register(config, validate_env=validate_env)
    with _registry_lock:
        _check_duplicate_locked(config.repo)
        _insert_locked(config, default=default)


def register_and_persist(
    config: EngineConfig, *, default: bool = False, validate_env: bool = True
) -> None:
    _validate_before_register(config, validate_env=validate_env)
    with _registry_lock:
        _check_duplicate_locked(config.repo)
        engine_registry_store.write_connected(config.repo.slug, config.model_dump(mode="json"))
        _insert_locked(config, default=default)


def register_replacing(config: EngineConfig, *, default: bool = False) -> None:
    _validate_before_register(config, validate_env=False)
    with _registry_lock:
        _insert_locked(config, default=default)


PROJECTION_EXCLUDE: dict[str, Any] = {
    "posture": True,
    "dependency_sweep": {"enabled"},
    "hidden": True,
    "hidden_restore_posture": True,
    "prompt_overrides": True,
    "accent": True,
}


def apply_stored_projection(file_config: EngineConfig, stored: EngineConfig | None) -> EngineConfig:
    if stored is None:
        return file_config
    dependency_sweep = file_config.dependency_sweep.model_copy(
        update={"enabled": stored.dependency_sweep.enabled}
    )
    # file_config.posture is this repo's reviewed ceiling (see _load_file_backed_engines,
    # which seeds _file_ceilings from the same field). Clamp the pending unhide restore
    # value here too, or a ceiling lowered while the engine is hidden would survive boot
    # and let the next unhide raise posture past the newly reviewed ceiling.
    hidden_restore_posture = stored.hidden_restore_posture
    if hidden_restore_posture is not None:
        hidden_restore_posture = min(
            hidden_restore_posture, file_config.posture, key=posture_rank
        )
    return file_config.model_copy(update={
        "posture": min(file_config.posture, stored.posture, key=posture_rank),
        "dependency_sweep": dependency_sweep,
        "hidden": stored.hidden,
        "hidden_restore_posture": hidden_restore_posture,
        "prompt_overrides": stored.prompt_overrides,
        "accent": stored.accent,
        "unblock_enabled": stored.unblock_enabled,
    })


def update_and_persist(config: EngineConfig, *, validate_env: bool = True) -> EngineConfig:
    _validate_before_register(config, validate_env=validate_env)
    with _registry_lock:
        _check_exists_locked(config.repo)
        # Posture and prompt_overrides are re-read fresh from the registry here, under
        # the same lock as the write — never taken from `config` — so a concurrent
        # posture-dial change or prompt-override save can't be reverted by a config
        # built earlier in the request. This is also what keeps the generic
        # create/update routes from being an unvalidated, unconfirmed side door for
        # prompt_overrides: legitimate changes go through set_posture_and_persist /
        # apply_prompt_override_and_persist, which bypass this revert entirely.
        live = _registry[config.repo]
        if config.posture != live.posture:
            config = config.model_copy(update={"posture": live.posture})
        if config.prompt_overrides != live.prompt_overrides:
            config = config.model_copy(update={"prompt_overrides": live.prompt_overrides})
        engine_registry_store.write_engine(config.repo.slug, config.model_dump(mode="json"))
        _insert_locked(config, default=False)
        return config


def apply_prompt_override_and_persist(
    repo: RepoIdentity, key: str, override: PromptOverride
) -> EngineConfig:
    with _registry_lock:
        _check_exists_locked(repo)
        current = _registry[repo]
        new_overrides = dict(current.prompt_overrides)
        new_overrides[key] = override
        updated = current.model_copy(update={"prompt_overrides": new_overrides})
        engine_registry_store.write_engine(repo.slug, updated.model_dump(mode="json"))
        _insert_locked(updated, default=False)
        return updated


def set_posture_and_persist(repo: RepoIdentity, target: Posture, *, source: str) -> EngineConfig:
    with _registry_lock:
        _check_exists_locked(repo)
        current = _registry[repo]
        if target == current.posture:
            return current
        ceiling = file_ceiling(repo)
        if posture_rank(target) > posture_rank(ceiling):
            raise PostureCeilingError(
                f"posture {target.value!r} exceeds the reviewed ceiling {ceiling.value!r} for "
                f"{repo.slug!r}; raise it in {ceiling_location(repo)}"
            )
        updated = current.model_copy(update={"posture": target})
        engine_registry_store.write_engine(repo.slug, updated.model_dump(mode="json"))
        _insert_locked(updated, default=False)
        engine_registry_store.append_posture_audit(
            repo.slug,
            {"from": current.posture.value, "to": target.value, "source": source},
        )
        return updated


def set_accent_and_persist(repo: RepoIdentity, accent: AccentChoice | None) -> EngineConfig:
    with _registry_lock:
        _check_exists_locked(repo)
        current = _registry[repo]
        if accent == current.accent:
            return current
        updated = current.model_copy(update={"accent": accent})
        engine_registry_store.write_engine(repo.slug, updated.model_dump(mode="json"))
        _insert_locked(updated, default=False)
        return updated


def hide_and_persist(repo: RepoIdentity) -> EngineConfig:
    with _registry_lock:
        _check_exists_locked(repo)
        current = _registry[repo]
        if current.hidden:
            return current
        updated = current.model_copy(
            update={
                "hidden": True,
                "hidden_restore_posture": current.posture,
                "posture": Posture.observe,
            }
        )
        engine_registry_store.write_engine(repo.slug, updated.model_dump(mode="json"))
        _insert_locked(updated, default=False)
        if updated.posture != current.posture:
            engine_registry_store.append_posture_audit(
                repo.slug,
                {"from": current.posture.value, "to": updated.posture.value, "source": "hide"},
            )
        return updated


def unhide_and_persist(repo: RepoIdentity) -> EngineConfig:
    with _registry_lock:
        _check_exists_locked(repo)
        current = _registry[repo]
        if not current.hidden:
            return current
        restore_posture = current.hidden_restore_posture
        if restore_posture is None:
            restore_posture = Posture.observe
        ceiling = file_ceiling(repo)
        restore_posture = min(restore_posture, ceiling, key=posture_rank)
        updated = current.model_copy(
            update={
                "hidden": False,
                "hidden_restore_posture": None,
                "posture": restore_posture,
            }
        )
        engine_registry_store.write_engine(repo.slug, updated.model_dump(mode="json"))
        _insert_locked(updated, default=False)
        if updated.posture != current.posture:
            engine_registry_store.append_posture_audit(
                repo.slug,
                {"from": current.posture.value, "to": updated.posture.value, "source": "unhide"},
            )
        return updated


def unregister_and_persist(repo: RepoIdentity) -> bool:
    global _default_key
    with _registry_lock:
        if repo not in _registry:
            return False
        engine_registry_store.delete_engine(repo.slug)
        del _registry[repo]
        if _default_key == repo:
            _default_key = None
        return True


def delete_engine(repo: RepoIdentity) -> None:
    unregister_and_persist(repo)


def load_persisted_engines() -> list[EngineConfig]:
    loaded: list[EngineConfig] = []
    for payload in engine_registry_store.list_engines():
        config = EngineConfig.model_validate(payload)
        register_replacing(config)
        loaded.append(config)
    return loaded


def get_registered() -> EngineConfig | None:
    with _registry_lock:
        if _default_key is None:
            return None
        return _registry[_default_key]


def lookup_by_repo(repo: RepoIdentity) -> EngineConfig | None:
    with _registry_lock:
        return _registry.get(repo)


def lookup_by_slug(slug: str) -> EngineConfig | None:
    try:
        repo = RepoIdentity.from_slug(slug)
    except ValueError:
        return None
    with _registry_lock:
        return _registry.get(repo)


def set_file_ceilings(
    ceilings: Mapping[RepoIdentity, Posture], *, fallback: Posture = Posture.autonomous
) -> None:
    global _file_ceilings, _fallback_ceiling
    _file_ceilings = dict(ceilings)
    _fallback_ceiling = fallback


def set_default_ceiling(ceilings: Mapping[RepoIdentity, Posture]) -> None:
    global _default_ceiling
    _default_ceiling = dict(ceilings)


def file_ceiling(repo: RepoIdentity) -> Posture:
    return _file_ceilings.get(repo, _default_ceiling.get(repo, _fallback_ceiling))


def ceiling_source(repo: RepoIdentity) -> str:
    layers = (("file", _file_ceilings), ("default", _default_ceiling))
    return next((name for name, layer in layers if repo in layer), "fallback")


def ceiling_location(repo: RepoIdentity) -> str:
    if ceiling_source(repo) == "default":
        return "engines/default_profile.yaml"
    return f"config/engines.d/{repo.repo}.yaml"


def is_file_backed(repo: RepoIdentity) -> bool:
    return repo in _file_ceilings


def set_quarantined_configs(records: Sequence[QuarantinedConfig]) -> None:
    global _quarantined_configs
    _quarantined_configs = list(records)


def quarantined_configs() -> list[QuarantinedConfig]:
    return list(_quarantined_configs)


def _resolve_scope_config(engine_key: str | None) -> EngineConfig:
    if engine_key is not None:
        cfg = lookup_by_slug(engine_key)
        if cfg is not None:
            return cfg
    return get_registered() or default_engine_config()


def canonical_scope_key(engine_key: str | None) -> RepoIdentity:
    return _resolve_scope_config(engine_key).repo


def display_scope_slug(engine_key: str | None) -> str | None:
    key = canonical_scope_key(engine_key)
    if key == canonical_scope_key(None):
        return None
    return key.slug


def same_repo_scope(engine_key: str | None, other: str | None) -> bool:
    return canonical_scope_key(engine_key) == canonical_scope_key(other)


def registered_engines() -> list[EngineConfig]:
    with _registry_lock:
        return list(_registry.values())


def forge_target_for_workspace(
    engine_key: str | None, snapshot_config: EngineConfig | None
) -> ForgeTarget:
    if snapshot_config is not None and snapshot_config.forge is not None:
        return ForgeTarget.from_forge_config(snapshot_config.forge)
    if engine_key is not None:
        cfg = lookup_by_slug(engine_key)
        if cfg is not None and cfg.forge is not None:
            return ForgeTarget.from_forge_config(cfg.forge)
    return DEFAULT_FORGE_TARGET


def registered_secret_env_names() -> frozenset[str]:
    names: set[str] = set()
    for cfg in registered_engines():
        if cfg.forge is not None:
            names.add(cfg.forge.credential_env_name)
        if cfg.code_quality is not None:
            names.add(cfg.code_quality.token_env_var)
    names.add(DEFAULT_FORGE_TARGET.credential_env_name)
    names.add(default_profile.SONAR_TOKEN_ENV)
    return frozenset(names)


def _parse_engine_yaml(path: Path) -> EngineConfig:
    try:
        text = path.read_text()
    except OSError as exc:
        raise OSError(f"{path.name}: cannot read file") from exc
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: invalid YAML") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: expected mapping at root")
    return EngineConfig.model_validate(raw)


def load_yaml(path: Path | str) -> EngineConfig:
    config = _parse_engine_yaml(Path(path))
    register(config)
    return config


_ENGINE_FILE_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml"})


def _engine_yaml_paths(directory: Path | str) -> list[Path]:
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []
    return sorted(
        p for p in dir_path.iterdir() if p.is_file() and p.suffix in _ENGINE_FILE_SUFFIXES
    )


def load_directory(directory: Path | str) -> list[EngineConfig]:
    return [load_yaml(p) for p in _engine_yaml_paths(directory)]


def _parse_unique_or_raise(paths: list[Path]) -> list[EngineConfig]:
    loaded: list[EngineConfig] = []
    seen: set[RepoIdentity] = set()
    for p in paths:
        config = _parse_engine_yaml(p)
        if config.repo in seen:
            raise DuplicateEngineError(f"Engine for {config.repo.slug!r} is already registered")
        seen.add(config.repo)
        loaded.append(config)
    return loaded


def import_legacy_directory_once(directory: Path | str) -> list[EngineConfig]:
    if engine_registry_store.list_engines():
        return []
    loaded = _parse_unique_or_raise(_engine_yaml_paths(directory))
    for config in loaded:
        engine_registry_store.write_connected(config.repo.slug, config.model_dump(mode="json"))
    return loaded


_BASE_TOOLS: list[str] = [
    "Read",
    "Grep",
    "Glob",
    "Bash(uv run:*)",
    "Bash(npm run:*)",
    "Bash(npm --prefix:*)",
    "Bash(npx:*)",
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(git status:*)",
    "Bash(git show:*)",
    "Bash(git blame:*)",
    "Bash(git ls-files:*)",
    "Bash(ls:*)",
    "Bash(cat:*)",
    "Bash(pwd:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(wc:*)",
    "Bash(diff:*)",
    "Bash(nl:*)",
    "Bash(git rev-parse:*)",
    "Bash(git merge-base:*)",
    "Bash(git rev-list:*)",
    "Bash(git ls-tree:*)",
    "Bash(git cat-file:*)",
    "Bash(git describe:*)",
    "Bash(git diff-tree:*)",
]

_IMPL_EXTRA_TOOLS: list[str] = [
    "Edit",
    "Write",
    "Bash(git:*)",
    "Bash(uv:*)",
    "Bash(npm:*)",
    "Bash(node:*)",
    "Bash(python:*)",
    "Bash(python3:*)",
    "Bash(pytest:*)",
    "Bash(mkdir:*)",
    "Bash(mv:*)",
    "Bash(cp:*)",
    "Bash(rm:*)",
]

_ROLES: dict[str, RoleConfig] = {
    "implementer": RoleConfig(model="sonnet", effort="xhigh"),
    "remediator": RoleConfig(model="opus", effort="xhigh"),
    "judge": RoleConfig(model="opus", effort=None),
    "diff_reviewer": RoleConfig(model="sonnet", effort=None),
    "failure_scanner": RoleConfig(model="haiku", effort=None),
}

_WEB_DIR = "alan/web"
_ALAN_PKG = "alan/"

_GATE_COMMANDS: list[list[str]] = [
    ["uv", "run", "--no-active", "pytest"],
    ["uv", "run", "--no-active", "ruff", "check", "."],
    ["uv", "run", "--no-active", "mypy", _ALAN_PKG, "tests/"],
    ["uv", "run", "--no-active", "pyright"],
    [
        "uv", "run", "--no-active", "lizard",
        "--CCN", "7", "--length", "60",
        "--languages", "python", "--exclude", _WEB_DIR + "/*", _ALAN_PKG,
    ],
    ["uv", "run", "--no-active", "xenon", "--max-modules", "A", "--max-average", "A", _ALAN_PKG],
    ["uv", "run", "--no-active", "lint-imports"],
    ["uv", "run", "--no-active", "bandit", "-c", "pyproject.toml", "-r", _ALAN_PKG],
    ["uv", "run", "--no-active", "vulture", "--min-confidence", "80", _ALAN_PKG, "tests/"],
    ["uv", "run", "--no-active", "pip-audit", "--strict", "--disable-pip"],
    ["npm", "--prefix", _WEB_DIR, "run", "typecheck"],
    ["npm", "--prefix", _WEB_DIR, "run", "lint"],
    ["npm", "--prefix", _WEB_DIR, "run", "test:coverage"],
    ["npm", "--prefix", _WEB_DIR, "run", "lint:knip"],
    ["npm", "--prefix", _WEB_DIR, "run", "lint:deps"],
    ["npm", "--prefix", _WEB_DIR, "run", "lint:dup"],
]

_WARMUP_COMMANDS: list[list[str]] = [
    ["uv", "sync", "--locked", "--extra", "dev"],
    ["npm", "--prefix", _WEB_DIR, "ci"],
]

_WEB_RESEARCH_TOOLS: list[str] = [
    "WebSearch",
    "WebFetch",
    "Bash(python3 -m alan.source_fetch:*)",
]

_MAINTENANCE_EXTRA_TOOLS: list[str] = [
    "Bash(gh run list:*)",
    "Bash(ps:*)",
    "Bash(stat:*)",
    "Bash(find:*)",
]

_TEAM_TOOLS: dict[str, list[str]] = {
    "implementation": list(_IMPL_EXTRA_TOOLS),
    "implementation_fix": list(_IMPL_EXTRA_TOOLS),
    "research": list(_WEB_RESEARCH_TOOLS),
    "brainstorming": list(_WEB_RESEARCH_TOOLS),
    "opportunity_scout": list(_WEB_RESEARCH_TOOLS),
    "maintenance": list(_MAINTENANCE_EXTRA_TOOLS),
}

_DEFAULT_FLOW_POLICY = FlowGraphPolicy(
    verifier_teams=frozenset({"verification"}),
    merge_teams=frozenset({"pr_merge"}),
    terminal_teams=frozenset({"deploy_verify", "maintenance", "triage", "ui_review"}),
    internal_only_teams=frozenset({
        "brief_refine",
        "decomposer",
        "dependency_sweep",
    }),
    restricted_predecessors={
        "brief_refine": frozenset(),
        "decomposer": frozenset({"implementation_plan"}),
        "dependency_sweep": frozenset(),
    },
    revisit_cap=3,
)

_TRANSITION_GRAPH: dict[str, list[str]] = {
    "research": ["brainstorming", "architecture", "implementation_plan"],
    "brainstorming": ["research", "architecture", "implementation_plan"],
    "architecture": ["implementation_plan", "research"],
    "implementation_plan": ["brainstorming", "research", "decomposer", "architecture"],
    "decomposer": ["implementation", "architecture", "research"],
    "implementation": ["verification"],
    "implementation_fix": ["verification"],
    "verification": ["implementation_fix", "pr_merge"],
    "pr_merge": ["implementation_fix", "deploy_verify"],
    "deploy_verify": [],
    "maintenance": [],
    "triage": [],
    "opportunity_scout": ["research", "brainstorming"],
    # ui_review's to_team_suggestions is always () — this is a terminal,
    # zero-outgoing-edge node so the router resolves a completed run to a
    # clean "done" (matching maintenance/triage/deploy_verify) rather than
    # "escalate: unknown from_team" (select_next_team._resolve_forward
    # treats a from_team absent from the graph as an error, not a pause).
    "ui_review": [],
}


def _with_aggregate_checks(forge_fields: dict[str, Any]) -> dict[str, Any]:
    declared = frozenset(forge_fields["authoritative_checks"]) & {GATE_CHECK}
    return {**forge_fields, "aggregate_checks": declared}


def _default_engine_fields() -> dict[str, Any]:
    fields = default_profile.resolved().engine_fields()
    if fields["forge"] is not None:
        fields["forge"] = _with_aggregate_checks(fields["forge"])
    return fields


def default_engine_config() -> EngineConfig:
    return EngineConfig(
        **_default_engine_fields(),
        roles=dict(_ROLES),
        gate_commands=copy.deepcopy(_GATE_COMMANDS),
        warmup_commands=copy.deepcopy(_WARMUP_COMMANDS),
        cache_env={"UV_CACHE_DIR": "uv", "npm_config_cache": "npm"},
        base_tools=list(_BASE_TOOLS),
        team_tools=copy.deepcopy(_TEAM_TOOLS),
        transition_graph=copy.deepcopy(_TRANSITION_GRAPH),
        flow_policy=_DEFAULT_FLOW_POLICY,
    )


def _default_forge_target() -> ForgeTarget:
    return ForgeTarget(**_with_aggregate_checks(default_profile.resolved().forge_fields()))


_DEFAULT: EngineConfig = default_engine_config()
DEFAULT_FORGE_TARGET: ForgeTarget = _default_forge_target()
_default_ceiling: dict[RepoIdentity, Posture] = {_DEFAULT.repo: _DEFAULT.posture}
register(_DEFAULT, default=True, validate_env=False)
