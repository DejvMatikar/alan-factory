from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parent

_CI_FILENAME = "ci.yml"
_DEPENDABOT_FILENAME = "dependabot.yml"
_CONVENTIONS_FILENAME = "conventions.md.template"

AUTHORITATIVE_CHECKS: dict[str, frozenset[str]] = {
    "python": frozenset({"python-ci"}),
    "node": frozenset({"node-ci"}),
    "dotnet": frozenset({"dotnet-ci"}),
    "ios": frozenset({"ios-ci"}),
}

STACKS: tuple[str, ...] = tuple(sorted(AUTHORITATIVE_CHECKS))


@dataclass(frozen=True)
class StackTemplate:
    stack: str
    ci_workflow_filename: str
    ci_yml: str
    dependabot_yml: str
    conventions_template: str


def _read(stack: str, filename: str) -> str:
    return (_TEMPLATES_DIR / stack / filename).read_text(encoding="utf-8")


def get_stack_template(stack: str | None) -> StackTemplate | None:
    if stack is None or stack not in AUTHORITATIVE_CHECKS:
        return None
    return StackTemplate(
        stack=stack,
        ci_workflow_filename=_CI_FILENAME,
        ci_yml=_read(stack, _CI_FILENAME),
        dependabot_yml=_read(stack, _DEPENDABOT_FILENAME),
        conventions_template=_read(stack, _CONVENTIONS_FILENAME),
    )
