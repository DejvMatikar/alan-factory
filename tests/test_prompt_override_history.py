from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from alan import control_plane, server
from alan.prompt_override_history import (
    HistoryEntry,
    PromptOverrideHistoryError,
    _history_dir,  # pyright: ignore[reportPrivateUsage]
    append_entry,
    get_entry,
    list_entries,
)
from alan.routes import engines as engines_route_mod
from alan.teams._base import TeamEvent


@pytest.fixture(autouse=True)
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


def test_append_entry_first_call_is_version_1() -> None:
    entry = append_entry("acme/widget", "implementation", "implementer", "text one", "fp1")
    assert entry == HistoryEntry(
        version=1, text="text one", base_fingerprint="fp1", saved_at=entry.saved_at
    )


def test_append_entry_increments_version_across_calls() -> None:
    first = append_entry("acme/widget", "implementation", "implementer", "v1", "fp1")
    second = append_entry("acme/widget", "implementation", "implementer", "v2", "fp2")
    assert (first.version, second.version) == (1, 2)


def test_saved_at_is_iso8601() -> None:
    entry = append_entry("acme/widget", "implementation", "implementer", "v1", "fp1")
    datetime.fromisoformat(entry.saved_at)


def test_list_entries_empty_for_never_saved_key() -> None:
    assert list_entries("acme/widget", "implementation", "implementer") == []


def test_list_entries_returns_ascending_order() -> None:
    append_entry("acme/widget", "implementation", "implementer", "v1", "fp1")
    append_entry("acme/widget", "implementation", "implementer", "v2", "fp2")
    append_entry("acme/widget", "implementation", "implementer", "v3", "fp3")
    entries = list_entries("acme/widget", "implementation", "implementer")
    assert [e.version for e in entries] == [1, 2, 3]
    assert [e.text for e in entries] == ["v1", "v2", "v3"]


def test_list_entries_isolated_per_team_role() -> None:
    append_entry("acme/widget", "implementation", "implementer", "impl-text", "fp1")
    append_entry("acme/widget", "verification", "judge", "judge-text", "fp2")
    impl = list_entries("acme/widget", "implementation", "implementer")
    judge = list_entries("acme/widget", "verification", "judge")
    assert [e.text for e in impl] == ["impl-text"]
    assert [e.text for e in judge] == ["judge-text"]


def test_list_entries_isolated_per_engine() -> None:
    append_entry("acme/widget", "implementation", "implementer", "acme-text", "fp1")
    append_entry("other/repo", "implementation", "implementer", "other-text", "fp2")
    acme = list_entries("acme/widget", "implementation", "implementer")
    other = list_entries("other/repo", "implementation", "implementer")
    assert [e.text for e in acme] == ["acme-text"]
    assert [e.text for e in other] == ["other-text"]


def test_get_entry_returns_specific_version() -> None:
    append_entry("acme/widget", "implementation", "implementer", "v1", "fp1")
    append_entry("acme/widget", "implementation", "implementer", "v2", "fp2")
    entry = get_entry("acme/widget", "implementation", "implementer", 1)
    assert entry is not None
    assert entry.text == "v1"


def test_get_entry_returns_none_for_missing_version() -> None:
    append_entry("acme/widget", "implementation", "implementer", "v1", "fp1")
    assert get_entry("acme/widget", "implementation", "implementer", 2) is None


def test_get_entry_returns_none_for_never_saved_key() -> None:
    assert get_entry("acme/widget", "implementation", "implementer", 1) is None


def test_history_file_has_owner_only_permissions(engines_root: Path) -> None:
    append_entry("acme/widget", "implementation", "implementer", "v1", "fp1")
    paths = list((engines_root / "prompt_override_history").rglob("*.json"))
    assert len(paths) == 1
    assert (paths[0].stat().st_mode & 0o777) == 0o600


def test_append_entry_never_clobbers_a_pre_claimed_slot() -> None:
    directory = _history_dir("acme/widget", "implementation", "implementer")
    directory.mkdir(parents=True)
    (directory / "000001.json").write_text('{"sentinel": true}', encoding="utf-8")
    entry = append_entry("acme/widget", "implementation", "implementer", "new text", "fp")
    assert entry.version == 2
    assert "sentinel" in (directory / "000001.json").read_text(encoding="utf-8")


def test_claim_slot_raises_when_no_free_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    import alan.prompt_override_history as poh

    monkeypatch.setattr(poh, "_MAX_VERSION", 1)
    poh.append_entry("acme/widget", "implementation", "implementer", "v1", "fp1")
    with pytest.raises(PromptOverrideHistoryError):
        poh.append_entry("acme/widget", "implementation", "implementer", "v2", "fp2")


def test_append_entry_cleans_up_on_write_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(json, "dumps", _boom)
    with pytest.raises(RuntimeError, match="boom"):
        append_entry("acme/widget", "implementation", "implementer", "v1", "fp1")
    directory = _history_dir("acme/widget", "implementation", "implementer")
    assert list(directory.glob("*")) == []


def test_get_entry_returns_none_for_corrupt_json() -> None:
    directory = _history_dir("acme/widget", "implementation", "implementer")
    directory.mkdir(parents=True)
    (directory / "000001.json").write_text("not json", encoding="utf-8")
    assert get_entry("acme/widget", "implementation", "implementer", 1) is None


def test_get_entry_returns_none_for_schema_mismatch() -> None:
    directory = _history_dir("acme/widget", "implementation", "implementer")
    directory.mkdir(parents=True)
    (directory / "000001.json").write_text('{"version": 1}', encoding="utf-8")
    assert get_entry("acme/widget", "implementation", "implementer", 1) is None


def test_list_entries_skips_unreadable_files() -> None:
    append_entry("acme/widget", "implementation", "implementer", "v1", "fp1")
    directory = _history_dir("acme/widget", "implementation", "implementer")
    (directory / "000002.json").write_text("corrupt", encoding="utf-8")
    entries = list_entries("acme/widget", "implementation", "implementer")
    assert [e.version for e in entries] == [1]


_COMPOSED_V1 = "prompt v1 {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}"
_COMPOSED_V2 = "prompt v2 {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}"
_WHOLESALE_V1 = "judge v1. VERDICT: line and DIFF_FINDINGS_CRITICAL: line."
_WHOLESALE_V2 = "judge v2. VERDICT: line and DIFF_FINDINGS_CRITICAL: line."


@pytest.fixture
def secrets_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "secrets"
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(root))
    return root


@pytest.fixture
def workspaces_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def client(
    engines_root: Path, secrets_root: Path, workspaces_root: Path
) -> Iterator[TestClient]:
    _ = engines_root, secrets_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: workspaces_root  # pyright: ignore[reportPrivateUsage]
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.pop(server._workspaces_root_dep, None)  # pyright: ignore[reportPrivateUsage]


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _create(client: TestClient, owner: str = "acme", repo: str = "widget") -> None:
    res = client.post(
        "/api/engines",
        json={"repo": {"owner": owner, "repo": repo}},
        headers=_control_headers(),
    )
    assert res.status_code == 201


def _set_posture(
    client: TestClient, posture: str, owner: str = "acme", repo: str = "widget"
) -> None:
    res = client.post(
        f"/api/engines/{owner}/{repo}/posture",
        json={"posture": posture, "confirm": True},
        headers=_control_headers(),
    )
    assert res.status_code == 200


def _save_override(
    client: TestClient,
    *,
    owner: str = "acme",
    repo: str = "widget",
    team: str = "implementation",
    role: str = "implementer",
    text: str = _COMPOSED_V1,
    confirm: bool = True,
) -> Response:
    res: Response = client.post(
        f"/api/engines/{owner}/{repo}/prompt-overrides",
        json={"team": team, "role": role, "text": text, "confirm": confirm},
        headers=_control_headers(),
    )
    return res


def _history(
    client: TestClient,
    *,
    owner: str = "acme",
    repo: str = "widget",
    team: str = "implementation",
    role: str = "implementer",
    headers: dict[str, str] | None = None,
) -> Response:
    res: Response = client.get(
        f"/api/engines/{owner}/{repo}/prompt-overrides/history",
        params={"team": team, "role": role},
        headers=headers,
    )
    return res


def _rollback(
    client: TestClient,
    *,
    owner: str = "acme",
    repo: str = "widget",
    team: str = "implementation",
    role: str = "implementer",
    version: int,
    confirm: bool = True,
    headers: dict[str, str] | None = None,
) -> Response:
    res: Response = client.post(
        f"/api/engines/{owner}/{repo}/prompt-overrides/rollback",
        json={"team": team, "role": role, "version": version, "confirm": confirm},
        headers=headers if headers is not None else _control_headers(),
    )
    return res


def test_save_appends_history_entry_version_1(client: TestClient) -> None:
    _create(client)
    res = _save_override(client, text=_COMPOSED_V1)
    assert res.status_code == 200

    body = _history(client).json()
    assert body["team"] == "implementation"
    assert body["role"] == "implementer"
    assert [e["version"] for e in body["entries"]] == [1]
    assert body["entries"][0]["text"] == _COMPOSED_V1
    assert body["drifted"] is False


def test_history_returns_newest_first_after_second_save(client: TestClient) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    _save_override(client, text=_COMPOSED_V2)
    body = _history(client).json()
    assert [e["version"] for e in body["entries"]] == [2, 1]
    assert body["entries"][0]["text"] == _COMPOSED_V2


def test_history_empty_and_not_drifted_when_no_override_saved(client: TestClient) -> None:
    _create(client)
    body = _history(client).json()
    assert body["entries"] == []
    assert body["drifted"] is False


def test_history_missing_engine_returns_404(client: TestClient) -> None:
    res = _history(client)
    assert res.status_code == 404


def test_history_invalid_team_role_returns_422(client: TestClient) -> None:
    _create(client)
    res = _history(client, team="pr_merge", role="merger")
    assert res.status_code == 422


def test_history_endpoint_needs_no_control_token(client: TestClient) -> None:
    _create(client)
    _save_override(client)
    res = _history(client, headers={})
    assert res.status_code == 200


def test_drift_false_when_override_reflects_current_base(client: TestClient) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    assert _history(client).json()["drifted"] is False


def test_drift_true_after_base_prompt_text_changes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    monkeypatch.setitem(
        engines_route_mod._BASE_PROMPT_TEXT,  # pyright: ignore[reportPrivateUsage]
        ("implementation", "implementer"),
        "a whole new base prompt",
    )
    assert _history(client).json()["drifted"] is True


def test_rollback_restores_prior_version_exact_text(client: TestClient) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    _save_override(client, text=_COMPOSED_V2)
    res = _rollback(client, version=1, confirm=True)
    assert res.status_code == 200
    assert (
        res.json()["prompt_overrides"]["implementation/implementer"]["text"]
        == _COMPOSED_V1
    )


def test_rollback_appends_new_history_entry_not_destructive(client: TestClient) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    _save_override(client, text=_COMPOSED_V2)
    _rollback(client, version=1, confirm=True)
    body = _history(client).json()
    assert [e["version"] for e in body["entries"]] == [3, 2, 1]
    assert body["entries"][0]["text"] == _COMPOSED_V1
    assert body["entries"][2]["text"] == _COMPOSED_V1


def test_rollback_missing_version_returns_404(client: TestClient) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    res = _rollback(client, version=99, confirm=True)
    assert res.status_code == 404


def test_rollback_missing_engine_returns_404(client: TestClient) -> None:
    res = _rollback(client, version=1, confirm=True)
    assert res.status_code == 404


def test_rollback_invalid_team_role_returns_422(client: TestClient) -> None:
    _create(client)
    res = client.post(
        "/api/engines/acme/widget/prompt-overrides/rollback",
        json={"team": "pr_merge", "role": "merger", "version": 1, "confirm": True},
        headers=_control_headers(),
    )
    assert res.status_code == 422


def test_rollback_rejects_historical_entry_missing_required_placeholder(
    client: TestClient,
) -> None:
    _create(client)
    append_entry("acme/widget", "implementation", "implementer", "missing all placeholders", "fp")
    res = _rollback(client, version=1, confirm=True)
    assert res.status_code == 422


def test_rollback_composed_override_at_observe_posture_needs_no_confirmation(
    client: TestClient,
) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    _save_override(client, text=_COMPOSED_V2)
    res = _rollback(client, version=1, confirm=False)
    assert res.status_code == 200


def test_rollback_verification_requires_confirmation_and_leaves_config_unchanged(
    client: TestClient,
) -> None:
    _create(client)
    _save_override(client, team="verification", role="judge", text=_WHOLESALE_V1)
    res = _rollback(client, team="verification", role="judge", version=1, confirm=False)
    assert res.status_code == 409
    stored = client.get("/api/engines/acme/widget").json()
    assert stored["prompt_overrides"]["verification/judge"]["text"] == _WHOLESALE_V1


def test_rollback_verification_with_confirm_persists(client: TestClient) -> None:
    _create(client)
    _save_override(client, team="verification", role="judge", text=_WHOLESALE_V1)
    _save_override(client, team="verification", role="judge", text=_WHOLESALE_V2)
    res = _rollback(client, team="verification", role="judge", version=1, confirm=True)
    assert res.status_code == 200
    assert (
        res.json()["prompt_overrides"]["verification/judge"]["text"] == _WHOLESALE_V1
    )


def test_rollback_composed_override_requires_confirmation_when_engine_can_self_merge(
    client: TestClient,
) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    _set_posture(client, "self_merge")
    _save_override(client, text=_COMPOSED_V2)
    res = _rollback(client, version=1, confirm=False)
    assert res.status_code == 409


def test_rollback_preserves_original_base_fingerprint_not_recomputed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    monkeypatch.setitem(
        engines_route_mod._BASE_PROMPT_TEXT,  # pyright: ignore[reportPrivateUsage]
        ("implementation", "implementer"),
        "a whole new base prompt",
    )
    _save_override(client, text=_COMPOSED_V2)
    assert _history(client).json()["drifted"] is False

    _rollback(client, version=1, confirm=True)

    assert _history(client).json()["drifted"] is True


def test_rollback_without_control_token_returns_401(client: TestClient) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    res = _rollback(client, version=1, confirm=True, headers={})
    assert res.status_code == 401


def test_rollback_wrong_control_token_returns_403(client: TestClient) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    res = _rollback(
        client,
        version=1,
        confirm=True,
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_rollback_publishes_engine_updated_event(client: TestClient) -> None:
    _create(client)
    _save_override(client, text=_COMPOSED_V1)
    _save_override(client, text=_COMPOSED_V2)
    events: list[TeamEvent] = []
    server.app.state.pubsub.subscribe_all(events.append)
    res = _rollback(client, version=1, confirm=True)
    assert res.status_code == 200
    matches = [e for e in events if e.kind == "engine_updated"]
    assert len(matches) == 1
