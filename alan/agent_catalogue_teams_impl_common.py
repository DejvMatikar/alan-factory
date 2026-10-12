from __future__ import annotations

from pathlib import Path

from alan.agent_catalogue_helpers import ValueSource
from alan.engine_config import EngineConfig
from alan.launch_spec import effort_for_model
from alan.prompt_templates import is_overridable, override_key
from alan.teams import _native_capture  # pyright: ignore[reportPrivateUsage]
from alan.teams import implementation as _impl
from alan.teams._declared_blocker import DECLARED_BLOCKER_GUIDANCE
from alan.teams._pr_body import PR_BODY_BLOCK_INSTRUCTIONS

NO_GUIDANCE_CHECKOUT = Path("/nonexistent/alan-agent-catalogue-template")

_DERIVED_EFFORT_NOTE = (
    "when no effort is set, alan derives one from the model alias "
    f"(opus {effort_for_model('opus')}, sonnet {effort_for_model('sonnet')}); any other "
    "model leaves the CLI default"
)
NO_ENGINE_EFFORT_DETAIL = (
    "alan passes no explicit effort for this role and never reads the engine role's effort: "
    f"for opus the effort is derived from the model as {effort_for_model('opus')}, for "
    f"sonnet as {effort_for_model('sonnet')}; any other model leaves the CLI default."
)


def engine_model_source(cfg: EngineConfig, role: str, fallback: str) -> ValueSource:
    if role in cfg.roles:
        return ValueSource(
            kind="engine_role",
            detail=f"Model of the engine's '{role}' role.",
            engine_role=role,
        )
    return ValueSource(
        kind="builtin",
        detail=f"The engine defines no '{role}' role, so the built-in default applies: {fallback}.",
    )


def engine_effort_source(cfg: EngineConfig, role: str, fallback: str) -> ValueSource:
    if role in cfg.roles:
        return ValueSource(
            kind="engine_role",
            detail=f"Effort of the engine's '{role}' role; {_DERIVED_EFFORT_NOTE}.",
            engine_role=role,
        )
    return ValueSource(
        kind="builtin",
        detail=f"The engine defines no '{role}' role, so the built-in default applies: "
        f"{fallback}; {_DERIVED_EFFORT_NOTE}.",
    )


def prompt_overridden(cfg: EngineConfig, team: str, role: str) -> bool:
    return is_overridable(team, role) and override_key(team, role) in cfg.prompt_overrides


def worktree_shared_blocks(cfg: EngineConfig, team: str) -> tuple[tuple[str, str], ...]:
    candidates = (
        ("pr_body_block_instructions", PR_BODY_BLOCK_INSTRUCTIONS),
        ("declared_blocker_guidance", DECLARED_BLOCKER_GUIDANCE.rstrip()),
        ("environment_block", _impl.render_environment_block(cfg)),
        ("native_capture_recipe", _native_capture.native_capture_block(cfg, team)),
    )
    return tuple(block for block in candidates if block[1])


__all__ = [
    "NO_ENGINE_EFFORT_DETAIL",
    "NO_GUIDANCE_CHECKOUT",
    "engine_effort_source",
    "engine_model_source",
    "prompt_overridden",
    "worktree_shared_blocks",
]
