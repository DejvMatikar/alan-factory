from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan.project as project_mod
import alan.project_amendment as project_amendment_mod
import alan.project_doc as project_doc
from alan import server
from alan.errors import AppError
from alan.project_amendment import (
    PENDING_AMENDMENTS_META_KEY,
    PendingAmendment,
    amendment_fingerprint,
    delete_pending_amendment,
    delete_pending_amendments_for_project,
    get_pending_amendment,
    load_pending_amendments,
    propose_amendment,
    record_amendment_approval,
    require_valid_fingerprint,
    resolve_pending_amendment,
)
from alan.queue import Queue
from alan.roots import logs_root


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, ws_root: Path, projects_root: Path
) -> Iterator[TestClient]:
    with TestClient(server.app) as c:
        monkeypatch.setattr(server.app.state, "workspaces_root", ws_root, raising=False)
        yield c


def _create_project(client: TestClient, title: str = "Spine") -> str:
    res = client.post("/api/projects", json={"title": title})
    assert res.status_code == 201
    project_id: str = res.json()["id"]
    return project_id


def _propose(
    queue: Queue,
    project_id: str,
    *,
    vision: str = "Ship the spine",
    scope: str = "In: store. Out: UI.",
    decision_entry: str = "Adopted the four-anchor doc.",
) -> tuple[str, PendingAmendment]:
    return propose_amendment(
        queue,
        project_id,
        vision=vision,
        scope=scope,
        decision_entry=decision_entry,
        now=datetime(2026, 7, 8, tzinfo=UTC),
    )


def test_propose_writes_pending_record_and_does_not_alter_doc(
    client: TestClient, projects_root: Path
) -> None:
    project_id = _create_project(client)
    project = project_mod.read(project_id, root=projects_root)
    queue = server.app.state.queue

    fingerprint, record = _propose(queue, project_id)

    assert re.fullmatch(r"[0-9a-f]{16}", fingerprint)
    assert get_pending_amendment(queue, fingerprint) == record
    assert record.project_id == project_id
    assert record.vision == "Ship the spine"

    _, path = project_doc.ensure_doc(project, root=projects_root)
    sections = project_doc.parse_anchors(path.read_text(encoding="utf-8"))
    assert sections.vision == ""
    assert sections.scope == ""
    assert sections.decision_log == ""


def test_require_valid_fingerprint_accepts_valid_shape() -> None:
    require_valid_fingerprint("0123456789abcdef")


def test_require_valid_fingerprint_rejects_malformed_shape() -> None:
    with pytest.raises(AppError) as exc_info:
        require_valid_fingerprint("not-a-fingerprint")
    assert exc_info.value.status_code == 422


def test_require_valid_fingerprint_rejects_uppercase_hex() -> None:
    with pytest.raises(AppError):
        require_valid_fingerprint("0123456789ABCDEF")


def test_amendment_fingerprint_is_deterministic() -> None:
    fp1 = amendment_fingerprint("p", "v", "s", "d")
    fp2 = amendment_fingerprint("p", "v", "s", "d")
    assert fp1 == fp2
    fp3 = amendment_fingerprint("p", "v2", "s", "d")
    assert fp1 != fp3


def test_amendment_fingerprint_disambiguates_separator_in_free_text() -> None:
    fp_a = amendment_fingerprint("p", "a|b", "c", "d")
    fp_b = amendment_fingerprint("p", "a", "b|c", "d")
    assert fp_a != fp_b


def test_amendment_fingerprint_vision_scope_matches_pre_generalization_raw_hash() -> None:
    import hashlib

    expected = hashlib.sha256(json.dumps(["p", "v", "s", "d"]).encode()).hexdigest()[:16]
    assert amendment_fingerprint("p", "v", "s", "d") == expected


def test_amendment_fingerprint_explicit_vision_scope_kind_matches_default() -> None:
    default_fp = amendment_fingerprint("p", "v", "s", "d")
    explicit_fp = amendment_fingerprint("p", "v", "s", "d", kind="vision_scope", payload={})
    assert default_fp == explicit_fp


def test_amendment_fingerprint_roadmap_kind_differs_from_vision_scope() -> None:
    vision_scope_fp = amendment_fingerprint("p", "", "", "")
    roadmap_fp = amendment_fingerprint(
        "p", "", "", "", kind="roadmap_reorder", payload={"ordered_ids": ["a", "b"]}
    )
    assert vision_scope_fp != roadmap_fp


def test_amendment_fingerprint_roadmap_kind_payload_key_order_independent() -> None:
    fp1 = amendment_fingerprint(
        "p", "", "", "", kind="roadmap_edit", payload={"item_id": "x", "title": "T"}
    )
    fp2 = amendment_fingerprint(
        "p", "", "", "", kind="roadmap_edit", payload={"title": "T", "item_id": "x"}
    )
    assert fp1 == fp2


def test_amendment_fingerprint_roadmap_kind_sensitive_to_payload_contents() -> None:
    fp1 = amendment_fingerprint(
        "p", "", "", "", kind="roadmap_reorder", payload={"ordered_ids": ["a", "b"]}
    )
    fp2 = amendment_fingerprint(
        "p", "", "", "", kind="roadmap_reorder", payload={"ordered_ids": ["b", "a"]}
    )
    assert fp1 != fp2


def test_propose_amendment_defaults_to_vision_scope_kind_and_empty_payload(
    queue: Queue,
) -> None:
    fingerprint, record = _propose(queue, "proj-a")
    assert record.kind == "vision_scope"
    assert record.payload == {}
    assert get_pending_amendment(queue, fingerprint) == record


def test_propose_amendment_accepts_roadmap_kind_and_payload(queue: Queue) -> None:
    fingerprint, record = propose_amendment(
        queue,
        "proj-a",
        kind="roadmap_reorder",
        payload={"ordered_ids": ["itm_1", "itm_2"]},
        now=datetime(2026, 7, 8, tzinfo=UTC),
    )
    assert record.kind == "roadmap_reorder"
    assert record.payload == {"ordered_ids": ["itm_1", "itm_2"]}
    assert record.vision == ""
    assert record.scope == ""
    assert record.decision_entry == ""
    assert get_pending_amendment(queue, fingerprint) == record


def test_propose_amendment_roadmap_edit_persists_across_reload(queue: Queue) -> None:
    fingerprint, _record = propose_amendment(
        queue,
        "proj-a",
        kind="roadmap_edit",
        payload={"item_id": "itm_1", "title": "New Title"},
        now=datetime(2026, 7, 8, tzinfo=UTC),
    )
    reloaded = load_pending_amendments(queue)[fingerprint]
    assert reloaded.kind == "roadmap_edit"
    assert reloaded.payload == {"item_id": "itm_1", "title": "New Title"}


def test_load_pending_amendments_defaults_kind_and_payload_for_legacy_blob(
    queue: Queue,
) -> None:
    queue.set_meta(
        PENDING_AMENDMENTS_META_KEY,
        json.dumps(
            {
                "fp_legacy": {
                    "project_id": "p",
                    "vision": "v",
                    "scope": "s",
                    "decision_entry": "d",
                    "proposed_by": "chat",
                    "proposed_at": "2026-07-08T00:00:00+00:00",
                }
            }
        ),
    )
    records = load_pending_amendments(queue)
    assert records["fp_legacy"].kind == "vision_scope"
    assert records["fp_legacy"].payload == {}


def test_load_pending_amendments_ignores_unknown_kind_and_non_dict_payload(
    queue: Queue,
) -> None:
    queue.set_meta(
        PENDING_AMENDMENTS_META_KEY,
        json.dumps(
            {
                "fp_bad": {
                    "project_id": "p",
                    "kind": "not_a_real_kind",
                    "payload": "not-a-dict",
                    "proposed_by": "chat",
                    "proposed_at": "2026-07-08T00:00:00+00:00",
                }
            }
        ),
    )
    records = load_pending_amendments(queue)
    assert records["fp_bad"].kind == "vision_scope"
    assert records["fp_bad"].payload == {}


def test_load_pending_amendments_defaults_kind_for_unhashable_kind_value(
    queue: Queue,
) -> None:
    queue.set_meta(
        PENDING_AMENDMENTS_META_KEY,
        json.dumps(
            {
                "fp_unhashable": {
                    "project_id": "p",
                    "kind": ["not", "a", "kind"],
                    "proposed_by": "chat",
                    "proposed_at": "2026-07-08T00:00:00+00:00",
                }
            }
        ),
    )
    records = load_pending_amendments(queue)
    assert records["fp_unhashable"].kind == "vision_scope"


def test_record_amendment_approval_includes_kind_and_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    record = PendingAmendment(
        project_id="proj-a",
        kind="roadmap_reorder",
        vision="",
        scope="",
        decision_entry="",
        payload={"ordered_ids": ["itm_1"]},
        proposed_by="chat",
        proposed_at="2026-07-08T00:00:00+00:00",
    )
    record_amendment_approval("fp1", record, now=datetime(2026, 7, 8, tzinfo=UTC))
    audit_path = logs_root() / "project_amendment_audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(lines[-1])
    assert entry["kind"] == "roadmap_reorder"
    assert entry["payload"] == {"ordered_ids": ["itm_1"]}


def test_approve_applies_and_deletes_pending(client: TestClient, projects_root: Path) -> None:
    project_id = _create_project(client)
    project = project_mod.read(project_id, root=projects_root)
    queue = server.app.state.queue
    fingerprint, _record = _propose(
        queue,
        project_id,
        vision="Ship the spine",
        scope="In: store.",
        decision_entry="Adopt vision.",
    )

    res = client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert res.status_code == 200
    body = res.json()
    assert body["fingerprint"] == fingerprint
    assert body["project_id"] == project_id

    assert get_pending_amendment(queue, fingerprint) is None

    _, path = project_doc.ensure_doc(project, root=projects_root)
    sections = project_doc.parse_anchors(path.read_text(encoding="utf-8"))
    assert sections.vision == "Ship the spine"
    assert sections.scope == "In: store."
    assert "Adopt vision." in sections.decision_log


def test_approve_never_touches_discovered_facts(client: TestClient, projects_root: Path) -> None:
    project_id = _create_project(client)
    project = project_mod.read(project_id, root=projects_root)
    project, path = project_doc.ensure_doc(project, root=projects_root)
    project_doc.append_discovered_fact(project, "pre-existing fact", root=projects_root)
    before = project_doc.parse_anchors(path.read_text(encoding="utf-8"))

    queue = server.app.state.queue
    fingerprint, _record = _propose(queue, project_id)
    client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")

    after = project_doc.parse_anchors(path.read_text(encoding="utf-8"))
    assert after.discovered_facts == before.discovered_facts
    assert "pre-existing fact" in after.discovered_facts


def test_approve_audit_before_mutate(
    client: TestClient, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = _create_project(client)
    queue = server.app.state.queue
    fingerprint, _record = _propose(queue, project_id)

    order: list[str] = []
    real_record = record_amendment_approval
    real_apply = project_doc.apply_vision_scope_amendment

    def _tracked_record(*args: Any, **kwargs: Any) -> None:
        order.append("audit")
        real_record(*args, **kwargs)

    def _tracked_apply(*args: Any, **kwargs: Any) -> Path:
        order.append("mutate")
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(project_amendment_mod, "record_amendment_approval", _tracked_record)
    monkeypatch.setattr(project_doc, "apply_vision_scope_amendment", _tracked_apply)

    res = client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert res.status_code == 200
    assert order == ["audit", "mutate"]


def test_approve_rejects_invalid_fingerprint_format(
    client: TestClient, projects_root: Path
) -> None:
    project_id = _create_project(client)
    res = client.post(f"/api/projects/{project_id}/amendments/not-a-fingerprint/approve")
    assert res.status_code == 422


def test_approve_404_for_unknown_fingerprint(client: TestClient, projects_root: Path) -> None:
    project_id = _create_project(client)
    res = client.post(f"/api/projects/{project_id}/amendments/{'a' * 16}/approve")
    assert res.status_code == 404


def test_approve_404_when_project_id_mismatches_pending_record(
    client: TestClient, projects_root: Path
) -> None:
    project_a = _create_project(client, "A")
    project_b = _create_project(client, "B")
    queue = server.app.state.queue
    fingerprint, _record = _propose(queue, project_a)

    res = client.post(f"/api/projects/{project_b}/amendments/{fingerprint}/approve")
    assert res.status_code == 404
    assert get_pending_amendment(queue, fingerprint) is not None


def test_approve_second_call_404s_without_reapplying(
    client: TestClient, projects_root: Path
) -> None:
    project_id = _create_project(client)
    project = project_mod.read(project_id, root=projects_root)
    queue = server.app.state.queue
    fingerprint, _record = _propose(queue, project_id)

    first = client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert first.status_code == 200

    second = client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert second.status_code == 404

    _, path = project_doc.ensure_doc(project, root=projects_root)
    assert path.read_text(encoding="utf-8").count("Adopted the four-anchor doc.") == 1


def test_approve_404s_and_never_mutates_when_claim_loses_race(
    client: TestClient, projects_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = _create_project(client)
    project = project_mod.read(project_id, root=projects_root)
    queue = server.app.state.queue
    fingerprint, _record = _propose(queue, project_id)

    monkeypatch.setattr(
        project_amendment_mod,
        "claim_pending_amendment",
        lambda _queue, _fingerprint: None,
    )

    res = client.post(f"/api/projects/{project_id}/amendments/{fingerprint}/approve")
    assert res.status_code == 404
    assert get_pending_amendment(queue, fingerprint) is not None

    _, path = project_doc.ensure_doc(project, root=projects_root)
    sections = project_doc.parse_anchors(path.read_text(encoding="utf-8"))
    assert sections.vision == ""


def test_resolve_pending_amendment_none_when_never_proposed(queue: Queue, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    assert resolve_pending_amendment(queue, "0" * 16, root=root) is None


def test_resolve_pending_amendment_stale_when_project_missing(
    queue: Queue, tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    fingerprint, _record = _propose(queue, "missing-project")
    assert resolve_pending_amendment(queue, fingerprint, root=root) is None


def test_resolve_pending_amendment_closed_when_archived(queue: Queue, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project = project_mod.create("Spine", root=root)
    project_mod.update(project, {"status": project_mod.ProjectStatus.ARCHIVED}, root=root)
    fingerprint, _record = _propose(queue, project.id)
    assert resolve_pending_amendment(queue, fingerprint, root=root) is None


def test_resolve_pending_amendment_returns_record_and_project(
    queue: Queue, tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    project = project_mod.create("Spine", root=root)
    fingerprint, record = _propose(queue, project.id)

    resolved = resolve_pending_amendment(queue, fingerprint, root=root)
    assert resolved is not None
    resolved_record, resolved_project = resolved
    assert resolved_record == record
    assert resolved_project.id == project.id


def test_load_pending_amendments_malformed_drops_no_crash(queue: Queue) -> None:
    queue.set_meta(PENDING_AMENDMENTS_META_KEY, "{not json")
    assert load_pending_amendments(queue) == {}

    queue.set_meta(PENDING_AMENDMENTS_META_KEY, json.dumps([1, 2, 3]))
    assert load_pending_amendments(queue) == {}

    queue.set_meta(
        PENDING_AMENDMENTS_META_KEY,
        json.dumps(
            {
                "fp0": "not-a-dict",
                "fp1": {"vision": "V"},
                "fp2": {"project_id": "p", "vision": 5, "proposed_by": "", "proposed_at": None},
            }
        ),
    )
    records = load_pending_amendments(queue)
    assert set(records) == {"fp2"}
    assert records["fp2"].project_id == "p"
    assert records["fp2"].vision == ""
    assert records["fp2"].proposed_by == "chat"


def test_delete_pending_amendment_noop_when_missing(queue: Queue) -> None:
    delete_pending_amendment(queue, "deadbeefdeadbeef")
    assert load_pending_amendments(queue) == {}


def test_claim_pending_amendment_returns_and_removes_record(queue: Queue) -> None:
    fingerprint, record = _propose(queue, "proj")

    claimed = project_amendment_mod.claim_pending_amendment(queue, fingerprint)

    assert claimed == record
    assert load_pending_amendments(queue) == {}
    assert project_amendment_mod.claim_pending_amendment(queue, fingerprint) is None


def test_claim_pending_amendment_concurrent_only_one_winner(queue: Queue) -> None:
    fingerprint, record = _propose(queue, "proj")
    barrier = threading.Barrier(2)
    results: list[PendingAmendment | None] = []
    results_lock = threading.Lock()

    def _claim() -> None:
        barrier.wait()
        result = project_amendment_mod.claim_pending_amendment(queue, fingerprint)
        with results_lock:
            results.append(result)

    t1 = threading.Thread(target=_claim)
    t2 = threading.Thread(target=_claim)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    winners = [r for r in results if r is not None]
    assert winners == [record]
    assert load_pending_amendments(queue) == {}


def test_concurrent_propose_two_projects_no_clobber(queue: Queue) -> None:
    barrier = threading.Barrier(2)

    def _write(pid: str) -> None:
        barrier.wait()
        _propose(queue, pid)

    t1 = threading.Thread(target=_write, args=("proj-a",))
    t2 = threading.Thread(target=_write, args=("proj-b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    records = load_pending_amendments(queue)
    assert {r.project_id for r in records.values()} == {"proj-a", "proj-b"}


def test_delete_pending_amendments_for_project_removes_only_matching_records(
    queue: Queue,
) -> None:
    fp_a, _record_a = _propose(queue, "proj-a")
    fp_b, _record_b = _propose(queue, "proj-b")

    delete_pending_amendments_for_project(queue, "proj-a")

    records = load_pending_amendments(queue)
    assert set(records) == {fp_b}
    assert get_pending_amendment(queue, fp_a) is None
    assert get_pending_amendment(queue, fp_b) is not None


def test_delete_pending_amendments_for_project_removes_multiple_records_for_same_project(
    queue: Queue,
) -> None:
    fp1, _r1 = _propose(queue, "proj-a", vision="V1")
    fp2, _r2 = _propose(queue, "proj-a", vision="V2")
    fp3, _r3 = _propose(queue, "proj-b")

    delete_pending_amendments_for_project(queue, "proj-a")

    records = load_pending_amendments(queue)
    assert set(records) == {fp3}
    assert get_pending_amendment(queue, fp1) is None
    assert get_pending_amendment(queue, fp2) is None


def test_delete_pending_amendments_for_project_noop_when_none_pending(queue: Queue) -> None:
    fp, _record = _propose(queue, "proj-b")

    delete_pending_amendments_for_project(queue, "proj-a")

    assert load_pending_amendments(queue) == {fp: get_pending_amendment(queue, fp)}


def test_delete_pending_amendments_for_project_does_not_touch_audit_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, queue: Queue
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fingerprint, record = _propose(queue, "proj-a")
    record_amendment_approval(fingerprint, record, now=datetime(2026, 7, 8, tzinfo=UTC))
    audit_path = logs_root() / "project_amendment_audit.jsonl"
    before = audit_path.read_text(encoding="utf-8")

    delete_pending_amendments_for_project(queue, "proj-a")

    assert audit_path.read_text(encoding="utf-8") == before
