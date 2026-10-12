from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TypeVar

from alan.engine_config import ForgeTarget
from alan.forge import ApiResult, PrRead
from tests.fakes.forge import FakeForge
from tests.forge_stubs import api_result, pr_read, readiness_fields

E2E = "Frontend E2E (Playwright, blocking)"
GATE = "gate"
LINT = "Widget Lint"

_REPO_URL = "https://github.com/acme-corp/widget-app"
_DEFAULT_RUN_ID = 7001
_FIRST_JOB_ID = 1000
_T = TypeVar("_T")
_PASSING = {"name": "Widget Typecheck", "conclusion": "SUCCESS", "status": "COMPLETED"}


def _advance(scripted: list[_T]) -> _T:
    return scripted.pop(0) if len(scripted) > 1 else scripted[0]


class PrForge:
    def __init__(
        self,
        fake: FakeForge,
        *,
        head_sha: str,
        base_sha: str = "0" * 40,
        merge_state: str = "BLOCKED",
        review_decision: str = "REVIEW_REQUIRED",
    ) -> None:
        self.fake = fake
        self.head_sha = head_sha
        self.base_sha = base_sha
        self.merge_state = merge_state
        self.review_decision = review_decision
        self.run_ids: dict[str, int] = {}
        self.rerun_run_ids: list[int] = []
        self.rerun_succeeds = True
        self._failing: tuple[str, ...] = ()
        self._reads: list[tuple[str, ...]] = [()]
        self._specs: list[tuple[str, ...]] = [()]
        fake.stub("read_pr", self._read_pr)
        fake.stub("head_sha", self._head_sha)
        fake.stub("reviews", lambda *_a, **_k: None)
        fake.stub("changed_files", lambda *_a, **_k: frozenset[str]())
        fake.stub("commit_check_runs_page", self._check_runs_page)
        fake.stub("rerun_failed_jobs", self._rerun_failed_jobs)
        fake.stub("status_check_rollup", self._status_check_rollup)
        fake.stub("transport_check_run_annotations", self._annotations)
        fake.stub("request_branch_update", lambda *_a, **_k: api_result())

    def script_reads(self, *failing_sets: tuple[str, ...]) -> None:
        self._reads = list(failing_sets)

    def script_specs(self, *spec_sets: tuple[str, ...]) -> None:
        self._specs = list(spec_sets)

    def show_failing(self, failing: tuple[str, ...]) -> None:
        self._failing = failing

    def calls(self, method: str) -> list[tuple[tuple[object, ...], dict[str, object]]]:
        return [(args, kwargs) for name, args, kwargs in self.fake.calls if name == method]

    def _run_id(self, check: str) -> int:
        return self.run_ids.get(check, _DEFAULT_RUN_ID)

    def _job_id(self, check: str) -> int:
        return _FIRST_JOB_ID + self._failing.index(check)

    def _details_url(self, check: str) -> str:
        return f"{_REPO_URL}/actions/runs/{self._run_id(check)}/job/{self._job_id(check)}"

    def _failing_item(self, check: str) -> dict[str, object]:
        return {
            "name": check, "conclusion": "FAILURE", "status": "COMPLETED",
            "detailsUrl": self._details_url(check),
        }

    def _read_pr(
        self, _pr: int, fields: tuple[str, ...], *, forge_target: ForgeTarget, timeout: float,
    ) -> PrRead:
        del forge_target, timeout
        if fields == ("baseRefOid",):
            return pr_read({"baseRefOid": self.base_sha})
        self._failing = _advance(self._reads)
        rollup = [self._failing_item(check) for check in self._failing] + [_PASSING]
        return pr_read(
            readiness_fields(
                self.merge_state, self.review_decision, rollup=rollup, head_sha=self.head_sha,
            )
        )

    def _head_sha(self, _pr: int, *, forge_target: ForgeTarget) -> ApiResult:
        del forge_target
        return api_result(self.head_sha + "\n")

    def _check_runs_page(self, sha: str, *, forge_target: ForgeTarget) -> ApiResult:
        del forge_target
        if sha == self.base_sha:
            return api_result(json.dumps({"check_runs": []}))
        runs: Sequence[dict[str, object]] = [
            {
                "name": check, "status": "completed", "conclusion": "failure",
                "details_url": self._details_url(check), "check_suite": {"id": 9000},
            }
            for check in self._failing
        ]
        return api_result(json.dumps({"check_runs": list(runs)}))

    def _rerun_failed_jobs(self, run_id: int, *, forge_target: ForgeTarget) -> ApiResult:
        del forge_target
        self.rerun_run_ids.append(run_id)
        return api_result() if self.rerun_succeeds else api_result(returncode=1, stderr="refused")

    def _status_check_rollup(
        self, _pr: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        del forge_target, timeout
        rollup = [self._failing_item(check) for check in self._failing]
        return api_result(json.dumps({"statusCheckRollup": rollup}))

    def _annotations(
        self, check_run_id: int, *, forge_target: ForgeTarget, timeout: float,
    ) -> ApiResult:
        del forge_target, timeout
        if E2E not in self._failing or check_run_id != self._job_id(E2E):
            return api_result("[]")
        specs = _advance(self._specs)
        return api_result(
            json.dumps(
                [
                    {
                        "annotation_level": "failure",
                        "path": f"alan/web/e2e/{spec}", "title": "renders", "message": "boom",
                    }
                    for spec in specs
                ]
            )
        )
