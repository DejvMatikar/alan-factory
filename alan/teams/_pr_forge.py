from __future__ import annotations

from alan import engine_config as _engine_config_mod
from alan.engine_config import (
    EngineConfig,
    ForgeTarget,
    RoleConfig,
    forge_target_for_workspace,
)
from alan.workspace import Workspace

GH_TIMEOUT_S = 60.0


def forge_target_for_ws(workspace: Workspace) -> ForgeTarget:
    snapshot = workspace.meta.engine_snapshot
    snapshot_config = snapshot.config if snapshot is not None else None
    return forge_target_for_workspace(workspace.meta.engine_key, snapshot_config)


def governing_config_for_ws(workspace: Workspace) -> EngineConfig:
    snapshot = workspace.meta.engine_snapshot
    if snapshot is not None and snapshot.config is not None:
        return snapshot.config
    engine_key = workspace.meta.engine_key
    if engine_key is not None:
        cfg = _engine_config_mod.lookup_by_slug(engine_key)
        if cfg is not None:
            return cfg
    return _engine_config_mod.get_registered() or _engine_config_mod.default_engine_config()


def _config_with_ci(workspace: Workspace) -> EngineConfig:
    snapshot = workspace.meta.engine_snapshot
    if snapshot is not None and snapshot.config is not None and snapshot.config.ci is not None:
        return snapshot.config
    engine_key = workspace.meta.engine_key
    if engine_key is not None:
        cfg = _engine_config_mod.lookup_by_slug(engine_key)
        if cfg is not None and cfg.ci is not None:
            return cfg
    return _engine_config_mod.default_engine_config()


def deploy_workflow_for_ws(workspace: Workspace) -> str | None:
    ci = _config_with_ci(workspace).ci
    return ci.deploy_workflow if ci is not None else None


def role_model_effort(
    cfg: EngineConfig, role: str, default: RoleConfig
) -> tuple[str, str | None]:
    role_cfg = cfg.roles.get(role, default)
    return role_cfg.model, role_cfg.effort
