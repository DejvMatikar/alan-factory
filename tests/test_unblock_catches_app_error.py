from __future__ import annotations

from types import SimpleNamespace
from typing import Any, NoReturn

import pytest

import alan.dispatch as dispatch_mod
import alan.unblock_recipes as recipes_mod
import alan.unblock_sweep as sweep_mod
from alan.errors import AppError


def _raise_app_error(*_args: object, **_kwargs: object) -> NoReturn:
    raise AppError(status_code=404, detail="no unique owner")


def test_dispatch_task_id_is_none_when_ownership_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate: Any = SimpleNamespace(ws=SimpleNamespace(task_id="ws-1"), blocking=None)
    monkeypatch.setattr(dispatch_mod, "owning_task_id", _raise_app_error)
    assert sweep_mod._dispatch_task_id(candidate) is None  # noqa: SLF001


def test_dispatch_task_id_returns_the_owner_when_resolution_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate: Any = SimpleNamespace(ws=SimpleNamespace(task_id="ws-1"), blocking=None)
    monkeypatch.setattr(dispatch_mod, "owning_task_id", lambda *_a, **_kw: "sub-a")
    assert sweep_mod._dispatch_task_id(candidate) == "sub-a"  # noqa: SLF001


def test_resolver_rejection_names_the_missing_resolver_when_no_resolver_is_mapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question: Any = SimpleNamespace(option_effects={})
    blocking: Any = SimpleNamespace(from_team="verification")
    monkeypatch.setattr(dispatch_mod, "resolver_team_for_answer", _raise_app_error)
    rejection = sweep_mod._resolver_rejection(  # noqa: SLF001
        question, blocking, "retry", "pr_merge"
    )
    assert rejection == "the parked question has no resolver for the recipe's answer"


def test_resume_team_declines_when_the_recorded_answer_has_no_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = SimpleNamespace(answer="retry", option_effects={})
    context: Any = SimpleNamespace(latest=SimpleNamespace(from_team="verification"))
    monkeypatch.setattr(recipes_mod, "answered_question", lambda _latest: question)
    monkeypatch.setattr(dispatch_mod, "resolver_team_for_answer", _raise_app_error)
    assert recipes_mod._resume_team(context) == recipes_mod.declined(  # noqa: SLF001
        "the recorded answer has no resolver"
    )
