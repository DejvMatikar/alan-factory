from __future__ import annotations

import re

from alan.engine_config import (
    EngineConfig,
    ForgeConfig,
    Posture,
    RepoIdentity,
    ScenarioConfig,
    default_engine_config,
)

_ENV_NAME_SANITIZE_RE = re.compile(r"[^A-Z0-9_]")

_PRESET_REPOS: dict[str, RepoIdentity] = {
    "dotnet": RepoIdentity(owner="_preset", repo="dotnet"),
    "python": RepoIdentity(owner="_preset", repo="python"),
    "node": RepoIdentity(owner="_preset", repo="node"),
    "ios": RepoIdentity(owner="_preset", repo="ios"),
}


def _skeleton(name: str) -> EngineConfig:
    base = default_engine_config()
    return base.model_copy(update={
        "repo": _PRESET_REPOS[name],
        "code_quality": None,
        "forge": None,
        "ci": None,
        "posture": Posture.observe,
        "preset": name,
    })


def dotnet_preset() -> EngineConfig:
    cfg = _skeleton("dotnet")
    return cfg.model_copy(update={
        "base_tools": [*cfg.base_tools, "Bash(dotnet:*)"],
        "gate_commands": [
            ["dotnet", "build"],
            ["dotnet", "test"],
            ["dotnet", "format", "--verify-no-changes"],
        ],
        "warmup_commands": [["dotnet", "restore"]],
        "cache_env": {"NUGET_PACKAGES": "nuget"},
    })


def python_preset() -> EngineConfig:
    cfg = _skeleton("python")
    return cfg.model_copy(update={
        "gate_commands": [
            ["uv", "run", "pytest"],
            ["uv", "run", "ruff", "check", "."],
        ],
        "warmup_commands": [["uv", "sync"]],
        "cache_env": {"UV_CACHE_DIR": "uv"},
    })


def node_preset() -> EngineConfig:
    cfg = _skeleton("node")
    return cfg.model_copy(update={
        "gate_commands": [
            ["npm", "run", "test"],
            ["npm", "run", "lint"],
        ],
        "warmup_commands": [["npm", "ci"]],
        "cache_env": {"npm_config_cache": "npm"},
    })


def ios_preset() -> EngineConfig:
    cfg = _skeleton("ios")
    return cfg.model_copy(update={
        "base_tools": [
            *cfg.base_tools,
            "Bash(xcodebuild:*)",
            "Bash(tuist:*)",
            "Bash(swiftlint:*)",
            "Bash(swift:*)",
            "Bash(swift-format:*)",
            "Bash(periphery:*)",
            "Bash(xcrun:*)",
            "Bash(lizard:*)",
            "Bash(bash scripts/gate.sh:*)",
        ],
        "gate_commands": [["bash", "scripts/gate.sh"]],
        "warmup_commands": [["tuist", "install"]],
        "cache_env": {"DERIVED_DATA_PATH": "deriveddata"},
        "scenario": ScenarioConfig(
            command_prefixes=[
                ["xcodebuild"], ["tuist"], ["swift"], ["xcrun"], ["python3"],
            ],
            timeout_s=900.0,
            aggregate_budget_s=900.0,
        ),
    })


PRESETS: dict[str, EngineConfig] = {
    "dotnet": dotnet_preset(),
    "python": python_preset(),
    "node": node_preset(),
    "ios": ios_preset(),
}


def get_preset(name: str) -> EngineConfig | None:
    return PRESETS.get(name)


def list_presets() -> list[tuple[str, EngineConfig]]:
    return [(name, PRESETS[name]) for name in sorted(PRESETS)]


def _credential_env_name(owner: str, repo: str) -> str:
    raw = f"{owner}_{repo}_gh_token".upper()
    sanitized = _ENV_NAME_SANITIZE_RE.sub("_", raw)
    return f"_{sanitized}" if sanitized[:1].isdigit() else sanitized


def build_config(name: str, owner: str, repo: str) -> EngineConfig | None:
    preset = get_preset(name)
    if preset is None:
        return None
    repo_identity = RepoIdentity(owner=owner, repo=repo)
    forge = ForgeConfig(
        repo=repo_identity, credential_env_name=_credential_env_name(owner, repo)
    )
    return preset.model_copy(update={"repo": repo_identity, "forge": forge})
