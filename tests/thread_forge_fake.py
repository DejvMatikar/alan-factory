from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CallRecord:
    argv: list[str]


def thread_node(
    thread_id: str,
    *,
    author_login: str,
    total_count: int | None,
    is_resolved: bool = False,
    path: str = "alan/foo.py",
    line: int = 10,
    body: str = "finding",
) -> dict[str, Any]:
    comments: dict[str, Any] = {"nodes": [{"body": body, "author": {"login": author_login}}]}
    if total_count is not None:
        comments["totalCount"] = total_count
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "isOutdated": False,
        "path": path,
        "line": line,
        "comments": comments,
    }


def review_threads_stdout(nodes: list[dict[str, Any]]) -> str:
    return json.dumps({
        "data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": nodes}}}}
    })


def resolve_mutation_stdout(is_resolved: bool) -> str:
    return json.dumps({"data": {"resolveReviewThread": {"thread": {"isResolved": is_resolved}}}})


@dataclass
class ThreadForgeFake:
    calls: list[CallRecord] = field(default_factory=list)

    def run_subprocess(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        timeout: float = 0,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, env
        self.calls.append(CallRecord(argv=list(cmd)))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
