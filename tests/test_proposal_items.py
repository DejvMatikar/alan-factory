from __future__ import annotations

from datetime import UTC, datetime

from alan import proposal_items
from alan.handover import Handover, OpenQuestion
from alan.project import Project, ProjectStatus
from alan.project_roadmap import RoadmapItem


def _handover(
    task_id: str,
    *,
    from_team: str = "implementation",
    open_questions: list[OpenQuestion] | None = None,
    to_team_suggestions: list[str] | None = None,
    full_synthesis: str = "synthesis text",
    pr_number: int | None = None,
) -> Handover:
    return Handover(
        handover_id="h1",
        handover_chain_id="h1",
        handover_type="forward",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv1",
        to_team_suggestions=to_team_suggestions or [],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=full_synthesis,
        open_questions=open_questions or [],
        pr_number=pr_number,
    )


def test_resolve_project_burn_none_when_project_id_is_none() -> None:
    assert proposal_items.resolve_project_burn(None, [], [], {}) == (None, None)


def test_propose_team_prefers_next_hop_over_suggestions() -> None:
    h = _handover("ws1", to_team_suggestions=["verification"])
    h = h.model_copy(update={"next_hop": "research"})
    assert proposal_items.propose_team(h) == "research"


def test_propose_team_falls_back_to_to_team_suggestions_in_order() -> None:
    h = _handover("ws1", to_team_suggestions=["decomposer", "research", "verification"])
    assert proposal_items.propose_team(h) == "research"


def test_propose_team_none_without_latest_handover() -> None:
    assert proposal_items.propose_team(None) is None


def test_propose_team_none_when_no_candidate_dispatchable() -> None:
    h = _handover("ws1", to_team_suggestions=["decomposer"])
    assert proposal_items.propose_team(h) is None


def test_next_item_proposal_item_builds_direct_dispatch_decision_card() -> None:
    project = Project(
        id="proj1",
        title="Proj",
        status=ProjectStatus.ACTIVE,
        autonomy="propose",
        engine_key="sprocket-co/sprocket",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    item = RoadmapItem(id="itm_1", title="Ship the widget", text="Full item body here.")

    card = proposal_items.next_item_proposal_item(project, item)

    assert card.type == "decision"
    assert card.fingerprint is None
    assert card.workspace_id is None
    assert card.task_id is None
    assert card.project_id == "proj1"
    assert card.engine_key == "sprocket-co/sprocket"
    assert "Ship the widget" in card.title
    assert card.prepared_context == "Full item body here."
    assert card.autonomy == "propose"
    assert len(card.actions) == 1
    action = card.actions[0]
    assert action.kind == "direct_dispatch"
    assert action.url == "/api/projects/proj1/roadmap/advance"
    assert "Ship the widget" in action.label


def test_next_item_proposal_item_truncates_long_item_text() -> None:
    project = Project(
        id="proj1",
        title="Proj",
        status=ProjectStatus.ACTIVE,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    item = RoadmapItem(id="itm_1", title="Long", text="x" * 600)

    card = proposal_items.next_item_proposal_item(project, item)

    assert len(card.prepared_context) == proposal_items._EXCERPT_MAX  # pyright: ignore[reportPrivateUsage]
