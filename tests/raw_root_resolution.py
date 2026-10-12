from __future__ import annotations

RAW_ROOT_RESOLUTIONS: frozenset[str] = frozenset()

DYNAMIC_ENV_READS: frozenset[str] = frozenset({
    "alan/_dispatcher_usage_limit.py",
    "alan/claude_cli.py",
    "alan/engine_config.py",
    "alan/secrets_store.py",
    "alan/teams/_env.py",
    "alan/teams/_pr_sonar.py",
})
