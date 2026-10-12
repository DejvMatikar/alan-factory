from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

_FAILURE_LEVEL = "failure"
_SPEC_SUFFIX = ".spec.ts"


@dataclass(frozen=True)
class FailingE2ETest:
    spec: str
    title: str
    message: str = ""


def _spec_path(node: Mapping[str, object]) -> str | None:
    path = node.get("path")
    if isinstance(path, str) and path.endswith(_SPEC_SUFFIX):
        return path
    return None


def _non_empty_title(node: Mapping[str, object]) -> str | None:
    title = node.get("title")
    return title if isinstance(title, str) and title else None


def _coerce_failing_test(raw: object) -> FailingE2ETest | None:
    if not isinstance(raw, Mapping):
        return None
    node = cast("Mapping[str, object]", raw)
    if node.get("annotation_level") != _FAILURE_LEVEL:
        return None
    spec = _spec_path(node)
    title = _non_empty_title(node)
    if spec is None or title is None:
        return None
    message = node.get("message")
    return FailingE2ETest(
        spec=spec,
        title=title,
        message=message if isinstance(message, str) else "",
    )


def parse_failing_e2e_annotations(
    annotations: Sequence[object],
) -> tuple[FailingE2ETest, ...]:
    coerced = (_coerce_failing_test(raw) for raw in annotations)
    return tuple(test for test in coerced if test is not None)


__all__ = ["FailingE2ETest", "parse_failing_e2e_annotations"]
