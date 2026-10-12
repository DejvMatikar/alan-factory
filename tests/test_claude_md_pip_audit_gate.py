from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_pip_audit_gate import _GATE_COMMAND

_CLAUDE_MD = Path(__file__).resolve().parent.parent / "CLAUDE.md"
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE_SPAN = re.compile(r"`([^`\n]+)`")
_TRAILING_COMMENT = re.compile(r"\s+#.*$")
_DIRECT_AUDIT_WORD = re.compile(r"(?<![\w.\[/-])pip[-_]audit(?![\w.\]/-])")
_UV_RUN_AUDIT = re.compile(r"\buv\s+run\s+(?:[^\n`;&|]*\s)?pip-audit(?![\w.\]/-])")
_MISSING_GATE = f"no fenced line equals {_GATE_COMMAND!r}"
_DIRECT_AUDIT = "contains a direct pip-audit command"


def _fenced_lines(text: str) -> list[str]:
    return [line for block in _FENCE.findall(text) for line in block.splitlines()]


def _command_lines(text: str) -> list[str]:
    return [*_fenced_lines(text), *_INLINE_SPAN.findall(text)]


def problems(text: str) -> list[str]:
    found: list[str] = []
    commands = [_TRAILING_COMMENT.sub("", line).strip() for line in _fenced_lines(text)]
    if _GATE_COMMAND not in commands:
        found.append(_MISSING_GATE)
    audited = any(_DIRECT_AUDIT_WORD.search(line) for line in _command_lines(text))
    if audited or _UV_RUN_AUDIT.search(text):
        found.append(_DIRECT_AUDIT)
    return found


def _fenced(*lines: str) -> str:
    return "```bash\n" + "\n".join(lines) + "\n```\n"


def test_claude_md_documents_only_the_shared_pip_audit_gate() -> None:
    assert problems(_CLAUDE_MD.read_text(encoding="utf-8")) == []


def test_the_old_inline_form_fails_both_checks() -> None:
    text = _fenced("uv run pip-audit --strict  # gates only pyproject.toml/uv.lock PRs")
    assert problems(text) == [_MISSING_GATE, _DIRECT_AUDIT]


def test_prose_naming_the_command_without_a_fence_fails() -> None:
    text = f"Run `{_GATE_COMMAND}` before pushing.\n"
    assert problems(text) == [_MISSING_GATE]


def test_the_command_outside_a_fence_fails() -> None:
    assert problems(f"{_GATE_COMMAND}\n") == [_MISSING_GATE]


def test_a_trailing_comment_on_the_gate_line_is_accepted() -> None:
    text = _fenced(f"{_GATE_COMMAND}  # gates only pyproject.toml/uv.lock PRs")
    assert problems(text) == []


def test_the_gate_command_with_an_extra_argument_fails() -> None:
    assert problems(_fenced(f"{_GATE_COMMAND} --strict")) == [_MISSING_GATE]


def test_the_gate_beside_a_direct_audit_in_one_fence_fails() -> None:
    text = _fenced(_GATE_COMMAND, "uv run pip-audit --strict")
    assert problems(text) == [_DIRECT_AUDIT]


@pytest.mark.parametrize(
    "direct",
    [
        "uvx pip-audit",
        "python -m pip_audit",
        "uv run --locked pip-audit",
        "pip-audit --strict",
    ],
)
def test_a_direct_audit_invocation_in_a_fence_fails(direct: str) -> None:
    assert problems(_fenced(_GATE_COMMAND, direct)) == [_DIRECT_AUDIT]


def test_an_inline_code_direct_audit_fails() -> None:
    text = _fenced(_GATE_COMMAND) + "Or run `uv run pip-audit` by hand.\n"
    assert problems(text) == [_DIRECT_AUDIT]


def test_a_uv_run_audit_in_prose_fails() -> None:
    text = _fenced(_GATE_COMMAND) + "Never run uv run --locked pip-audit here.\n"
    assert problems(text) == [_DIRECT_AUDIT]


@pytest.mark.parametrize(
    "prose",
    [
        "Never run uv run --python 3.12 pip-audit here.\n",
        "Never run uv run --with foo --python 3.12 pip-audit --strict here.\n",
        "Never chain uv run ruff check . && uv run --python 3.12 pip-audit here.\n",
    ],
)
def test_a_uv_run_audit_with_option_values_in_prose_fails(prose: str) -> None:
    assert problems(_fenced(_GATE_COMMAND) + prose) == [_DIRECT_AUDIT]


def test_the_gate_and_the_ignore_table_on_one_prose_line_are_accepted() -> None:
    text = _fenced(_GATE_COMMAND) + "Run uv run ruff check . and read [tool.pip-audit] after.\n"
    assert problems(text) == []


def test_the_ignore_table_name_in_prose_is_accepted() -> None:
    text = _fenced(_GATE_COMMAND) + "Ignores live in `[tool.pip-audit]` of pyproject.toml.\n"
    assert problems(text) == []


def test_the_gate_module_name_in_prose_is_accepted() -> None:
    text = _fenced(_GATE_COMMAND) + "See `alan/pip_audit_gate.py` for the gate.\n"
    assert problems(text) == []


def test_the_command_among_other_fenced_lines_is_accepted() -> None:
    text = _fenced("uv sync --locked --extra dev", "uv run ruff check .", _GATE_COMMAND)
    assert problems(text) == []
