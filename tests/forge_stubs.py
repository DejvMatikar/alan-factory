from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast

from alan.forge import (
    ApiResult,
    ChecksRead,
    FileOutcome,
    FileRead,
    JsonObject,
    LoginOutcome,
    LoginRead,
    MergeAttempt,
    PrRead,
    RowsOutcome,
    RowsRead,
    active_forge,
)
from tests.fakes.forge import FakeForge
from tests.gh_guard import GH_STUB_RETURNCODE, GH_STUB_STDERR


def pr_read(
    fields: JsonObject | None = None, *, returncode: int = 0, stderr: str = "",
) -> PrRead:
    return PrRead(
        ok=returncode == 0, fields=fields or {}, returncode=returncode, stderr=stderr,
    )


def checks_read(
    checks: Sequence[JsonObject] = (), *, returncode: int = 0, stderr: str = "",
) -> ChecksRead:
    return ChecksRead(
        ok=returncode == 0, checks=tuple(checks), returncode=returncode, stderr=stderr,
    )


def merge_attempt(*, returncode: int = 0, stderr: str = "") -> MergeAttempt:
    return MergeAttempt(ok=returncode == 0, returncode=returncode, stderr=stderr)


def api_result(stdout: str = "", *, returncode: int = 0, stderr: str = "") -> ApiResult:
    return ApiResult(ok=returncode == 0, stdout=stdout, returncode=returncode, stderr=stderr)


def rows_read(
    rows: Sequence[JsonObject] | None = (), *,
    outcome: RowsOutcome = "ok", returncode: int = 0, stderr: str = "",
) -> RowsRead:
    return RowsRead(
        outcome=outcome,
        rows=tuple(rows) if rows is not None else None,
        returncode=returncode, stderr=stderr,
    )


def login_read(
    login: str | None = "octocat", *,
    outcome: LoginOutcome = "ok", returncode: int = 0, stderr: str = "",
) -> LoginRead:
    return LoginRead(outcome=outcome, login=login, returncode=returncode, stderr=stderr)


def file_read(
    text: str | None = "", *,
    outcome: FileOutcome = "ok", returncode: int = 0, stderr: str = "",
) -> FileRead:
    return FileRead(outcome=outcome, text=text, returncode=returncode, stderr=stderr)


def readiness_fields(
    merge_state: str = "CLEAN", review_decision: str = "", *,
    rollup: Sequence[object] = (), head_sha: str = "", **extra: object,
) -> JsonObject:
    fields: JsonObject = {
        "mergeStateStatus": merge_state,
        "reviewDecision": review_decision,
        "statusCheckRollup": list(rollup),
    }
    if head_sha:
        fields["headRefOid"] = head_sha
    fields.update(extra)
    return fields


def _raise(exc: BaseException) -> Callable[..., object]:
    def _raiser(*_a: object, **_k: object) -> object:
        raise exc

    return _raiser


def always(fake: FakeForge, method: str, result: object) -> None:
    if isinstance(result, BaseException):
        fake.stub(method, _raise(result))
        return
    fake.stub(method, lambda *_a, **_k: result)


def gh_unreachable() -> None:
    always(
        cast(FakeForge, active_forge()), "read_pr",
        pr_read(returncode=GH_STUB_RETURNCODE, stderr=GH_STUB_STDERR),
    )


class ForgeScript:
    def __init__(self, fake: FakeForge) -> None:
        self.fake = fake
        self._pending: dict[str, list[object]] = {}
        self._read_pr_by_fields: dict[tuple[str, ...], list[object]] = {}

    def queue(self, method: str, *results: object) -> None:
        if method not in self._pending:
            self._pending[method] = []
            self.fake.stub(method, self._popper(method))
        self._pending[method].extend(results)

    def queue_read_pr(self, fields: tuple[str, ...], *results: object) -> None:
        self._pending.setdefault("read_pr", [])
        self.fake.stub("read_pr", self._read_pr_router)
        self._read_pr_by_fields.setdefault(fields, []).extend(results)

    def _read_pr_router(self, *args: object, **kwargs: object) -> object:
        fields = cast("tuple[str, ...]", args[1] if len(args) > 1 else kwargs.get("fields"))
        queued = self._read_pr_by_fields.get(fields)
        source = queued if queued else self._pending["read_pr"]
        result = source.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def calls(self, method: str) -> list[tuple[tuple[object, ...], dict[str, object]]]:
        return [(args, kwargs) for name, args, kwargs in self.fake.calls if name == method]

    def review_payloads(self) -> list[JsonObject]:
        return [cast(JsonObject, args[1]) for args, _ in self.calls("post_review")]

    def _popper(self, method: str) -> Callable[..., object]:
        def _next(*_a: object, **_k: object) -> object:
            result = self._pending[method].pop(0)
            if isinstance(result, BaseException):
                raise result
            return result

        return _next
