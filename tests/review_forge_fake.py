from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from alan.forge import active_forge, set_active_forge
from alan.teams import _pr_branch, _pr_ready
from tests.fakes.forge import FakeForge


@dataclass
class FakeReview:
    id: int
    login: str
    state: str
    commit_id: str = ""

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "state": self.state,
            "user": {"login": self.login},
            "commit_id": self.commit_id,
        }


@dataclass
class ReviewForgeFake:
    reviews: list[FakeReview] = field(default_factory=list)
    dismiss_rc: int = 0
    head_sha: str = "head-sha"
    attempted_dismiss_ids: list[int] = field(default_factory=list)
    dismiss_messages: list[str] = field(default_factory=list)
    raw_stream: str | None = None
    stream_available: bool = True
    readiness: _pr_ready.PrReadiness | None = None

    def review_stream(self, pr_number: int, *, forge_target: object) -> str | None:
        del pr_number, forge_target
        if not self.stream_available:
            return None
        if self.raw_stream is not None:
            return self.raw_stream
        return "\n".join(json.dumps(r.as_json()) for r in self.reviews)

    def dismiss_review(
        self, pr_number: int, review_id: int, *, forge_target: object, message: str,
    ) -> bool:
        del pr_number, forge_target
        self.attempted_dismiss_ids.append(review_id)
        self.dismiss_messages.append(message)
        if self.dismiss_rc == 0:
            for r in self.reviews:
                if r.id == review_id:
                    r.state = "DISMISSED"
        return self.dismiss_rc == 0

    def fetch_pr_head_sha(self, pr_number: int, **kwargs: object) -> str | None:
        del pr_number, kwargs
        return self.head_sha

    def check_pr_merge_readiness(
        self, *args: object, **kwargs: object
    ) -> _pr_ready.PrReadiness:
        del args, kwargs
        assert self.readiness is not None
        return self.readiness


def _scriptable_forge() -> FakeForge:
    current = active_forge()
    if isinstance(current, FakeForge):
        return current
    replacement = FakeForge()
    set_active_forge(replacement)
    return replacement


def install(fake: ReviewForgeFake, monkeypatch: pytest.MonkeyPatch) -> None:
    forge = _scriptable_forge()
    forge.stub("reviews", fake.review_stream)
    forge.stub("dismiss_review", fake.dismiss_review)
    monkeypatch.setattr(_pr_branch, "_fetch_pr_head_sha", fake.fetch_pr_head_sha)
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", fake.check_pr_merge_readiness)
    monkeypatch.setenv("ALAN_VERIFIER_LOGIN", "alan-verifier")
