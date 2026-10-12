from __future__ import annotations

METRIC = (
    "lines containing at least one non-whitespace character "
    "(blank/whitespace-only lines excluded)"
)

TEST_FILE_CAP = 1000
SOURCE_MODULE_CAP = 800
CLAUDE_MD_BYTE_CEILING = 14000

PINNED_TEST_FILES: dict[str, int] = {
    "tests/test_reconciler.py": 2768,
    "tests/test_routes_workspaces.py": 2496,
    "tests/test_server_workspaces.py": 2451,
    "tests/test_teams_pr_merge.py": 1898,
    "tests/test_handover.py": 2154,
    "tests/test_routes_engines.py": 2113,
    "tests/test_teams_base.py": 1806,
    "tests/test_teams_implementation.py": 1771,
    "tests/test_workflow.py": 1743,
    "tests/test_slot_pool.py": 1637,
    "tests/test_claude_cli.py": 1688,
    "tests/test_teams_implementation_fix.py": 1673,
    "tests/test_routes_projects.py": 1632,
    "tests/test_teams_dependency_sweep.py": 1558,
    "tests/test_workspace.py": 1520,
    "tests/test_teams_triage.py": 1485,
    "tests/test_triage_pregate.py": 1398,
    "tests/test_claude_cli_streaming.py": 1391,
    "tests/test_collision.py": 1381,
    "tests/test_workspace_index.py": 1368,
    "tests/test_loop_guard.py": 1274,
    "tests/test_routes_explore_chat.py": 1054,
    "tests/test_chat_router.py": 1042,
    "tests/test_engine_config.py": 1033,
    "tests/test_routes_build_chat.py": 1016,
}

PINNED_TEST_TOTAL = 41350
PINNED_TEST_COUNT = 25

PINNED_SOURCE_MODULES: dict[str, int] = {
    "alan/dispatcher.py": 983,
    "alan/claude_cli.py": 1410,
    "alan/teams/pr_merge.py": 1203,
    "alan/routes/workspaces.py": 936,
    "alan/reconciler.py": 876,
    "alan/teams/_base.py": 855,
}

PINNED_SOURCE_TOTAL = 6263
PINNED_SOURCE_COUNT = 6

PINNED_FRONTEND_TEST_FILES: dict[str, int] = {
    "alan/web/src/api.test.ts": 3405,
    "alan/web/src/useChatSessionGeneric.test.ts": 1295,
}

PINNED_FRONTEND_TEST_TOTAL = 4700
PINNED_FRONTEND_TEST_COUNT = 2
