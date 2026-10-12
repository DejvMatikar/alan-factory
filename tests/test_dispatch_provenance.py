from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from alan.dispatch_provenance import UNRECORDED, DispatchProvenance, DispatchSurface
from alan.prep_chat import PrepChatKind, dispatch_surface
from alan.workspace import Meta, Status


def test_dispatch_provenance_is_frozen() -> None:
    provenance = DispatchProvenance()
    with pytest.raises(ValidationError):
        provenance.surface = DispatchSurface.API_DIRECT


def test_dispatch_surface_has_exactly_ten_members() -> None:
    assert len(DispatchSurface) == 10


def test_dispatch_surface_members_are_snake_case_values() -> None:
    assert {member.value for member in DispatchSurface} == {
        "unrecorded",
        "api_direct",
        "build_chat",
        "explore_chat",
        "triage_sweep",
        "dep_sweep_parent",
        "dep_sweep_pr",
        "dep_advisory_check",
        "dep_advisory_fix",
        "project_auto_continue",
    }


def test_unrecorded_singleton_surface_is_unrecorded() -> None:
    assert UNRECORDED.surface == DispatchSurface.UNRECORDED


def test_unrecorded_singleton_actor_is_none() -> None:
    assert UNRECORDED.actor is None


def test_unrecorded_singleton_prep_chat_session_id_is_none() -> None:
    assert UNRECORDED.prep_chat_session_id is None


def test_default_dispatch_provenance_is_unrecorded() -> None:
    assert DispatchProvenance() == UNRECORDED


@pytest.mark.parametrize("actor", ["a", "issue-driver", "a" * 64])
def test_valid_actor_is_accepted(actor: str) -> None:
    assert DispatchProvenance(actor=actor).actor == actor


def test_empty_actor_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DispatchProvenance(actor="")


def test_uppercase_actor_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DispatchProvenance(actor="A")


def test_actor_starting_with_hyphen_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DispatchProvenance(actor="-a")


def test_actor_over_max_length_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DispatchProvenance(actor="a" * 65)


def test_actor_with_space_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DispatchProvenance(actor="a b")


def test_actor_with_period_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DispatchProvenance(actor="a.")


def test_actor_with_unicode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DispatchProvenance(actor="âctor")


def test_dispatch_surface_none_kind_maps_to_api_direct() -> None:
    assert dispatch_surface(None) == DispatchSurface.API_DIRECT


def test_dispatch_surface_build_kind_maps_to_build_chat() -> None:
    assert dispatch_surface(PrepChatKind.BUILD) == DispatchSurface.BUILD_CHAT


def test_dispatch_surface_explore_kind_maps_to_explore_chat() -> None:
    assert dispatch_surface(PrepChatKind.EXPLORE) == DispatchSurface.EXPLORE_CHAT


def test_meta_dispatch_provenance_field_rejects_reassignment() -> None:
    meta = Meta(
        task_id="t",
        title="t",
        description="d",
        status=Status.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        meta.dispatch_provenance = DispatchProvenance(surface=DispatchSurface.API_DIRECT)
