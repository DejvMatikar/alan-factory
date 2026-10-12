from __future__ import annotations

from datetime import UTC, datetime

from alan.health import _describe_stale_live_process, _severity_action, compute_health_state
from alan.teams.triage_pregate import ClassifiedSurface


def _surface(target_sha: str, served_sha: str = "a" * 40) -> ClassifiedSurface:
    return ClassifiedSurface(
        surface_id=f"stale_live_process:{target_sha[:12]}",
        kind="stale_live_process",
        is_blocked=True,
        blocker_reason="hot_paths",
        obs={
            "served_sha": served_sha,
            "target_sha": target_sha,
            "hot_paths": ["alan/server.py"],
        },
        fp_obs={"served_sha": served_sha, "target_sha": target_sha},
    )


def test_severity_action_stale_live_process_critical_deep_link() -> None:
    sev, action = _severity_action("stale_live_process", set())
    assert sev == "critical"
    assert action == "deep_link"


def test_describe_stale_live_process_singular() -> None:
    obs = {
        "served_sha": "a" * 40,
        "target_sha": "b" * 40,
        "hot_paths": ["alan/server.py"],
    }
    desc = _describe_stale_live_process(obs)
    assert desc.startswith(f"Live process running {'a' * 12} but main is at {'b' * 12}")
    assert "(1 hot-code path not yet loaded)" in desc
    assert desc.endswith(": alan/server.py")


def test_describe_stale_live_process_plural() -> None:
    obs = {
        "served_sha": "a" * 40,
        "target_sha": "b" * 40,
        "hot_paths": ["alan/server.py", "alan/health.py"],
    }
    desc = _describe_stale_live_process(obs)
    assert "(2 hot-code paths not yet loaded)" in desc
    assert desc.endswith(": alan/server.py, alan/health.py")


def test_describe_stale_live_process_filters_non_string_samples() -> None:
    obs = {
        "served_sha": "a" * 40,
        "target_sha": "b" * 40,
        "hot_paths": ["alan/server.py", "alan/health.py", 42, None],
    }
    desc = _describe_stale_live_process(obs)
    assert "(2 hot-code paths not yet loaded)" in desc
    assert "42" not in desc
    assert desc.endswith(": alan/server.py, alan/health.py")


def test_describe_stale_live_process_omits_sample_list_when_empty() -> None:
    obs = {"served_sha": "a" * 40, "target_sha": "b" * 40, "hot_paths": []}
    desc = _describe_stale_live_process(obs)
    assert desc.endswith("(0 hot-code paths not yet loaded)")
    assert ":" not in desc


def test_describe_stale_live_process_bounds_sample_with_more_suffix() -> None:
    hot_paths = [f"alan/mod_{i}.py" for i in range(10)]
    obs = {"served_sha": "a" * 40, "target_sha": "b" * 40, "hot_paths": hot_paths}
    desc = _describe_stale_live_process(obs)
    assert "(10 hot-code paths not yet loaded)" in desc
    assert desc.endswith(": " + ", ".join(hot_paths[:8]) + " (+2 more)")


def test_describe_stale_live_process_non_list_hot_paths_treated_as_empty() -> None:
    obs = {"served_sha": "a" * 40, "target_sha": "b" * 40, "hot_paths": "not-a-list"}
    desc = _describe_stale_live_process(obs)
    assert desc.endswith("(0 hot-code paths not yet loaded)")


def test_describe_stale_live_process_missing_shas_render_empty() -> None:
    desc = _describe_stale_live_process({})
    assert desc.startswith("Live process running  but main is at  (")


def test_compute_health_state_raises_then_clears_then_resurfaces_on_new_target() -> None:
    now = datetime.now(UTC)

    state1 = compute_health_state([_surface("b" * 40)], now)
    assert state1.verdict == "degraded"
    assert [f.finding_id for f in state1.findings] == [f"stale_live_process:{'b' * 12}"]
    assert state1.findings[0].severity == "critical"
    assert state1.findings[0].action == "deep_link"

    state2 = compute_health_state([], now)
    assert state2.verdict == "healthy"
    assert state2.findings == []

    state3 = compute_health_state([_surface("c" * 40)], now)
    assert state3.verdict == "degraded"
    assert [f.finding_id for f in state3.findings] == [f"stale_live_process:{'c' * 12}"]
