from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

MACHINE_READ_COMMENT = re.compile(
    r"^#\s*(?:!|-\*-|noqa|type:|pragma:|nosec|fmt:|ruff:|mypy:|pyright:|isort:|pylint:"
    r"|yapf:|coding[:=])",
    re.IGNORECASE,
)

DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

REMEDY = (
    "Test code carries no comments and no docstrings (CLAUDE.md). The test's name is its "
    "documentation: a case that needs explaining needs renaming, not a comment above it, and "
    "a file that needs a module docstring to say what it covers needs a filename that says it. "
    "Fold what this says into the test name or the PR body and delete it. Only machine-read "
    "directives may stay (# noqa, # type:, # pragma:, # nosec); when one trails prose "
    "(# why  # noqa: X), keep the directive and drop the prose. Silencing this gate by "
    "widening MACHINE_READ_COMMENT is not a fix."
)


def iter_python_files(root: Path, repo_root: Path) -> list[str]:
    return sorted(
        path.relative_to(repo_root).as_posix()
        for path in root.rglob("*.py")
        if not {".venv", "node_modules"}.intersection(path.relative_to(repo_root).parts)
    )


def prose_comments(source: str) -> list[tuple[int, str]]:
    return [
        (token.start[0], token.string)
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type is tokenize.COMMENT and not MACHINE_READ_COMMENT.match(token.string)
    ]


def docstring_lines(source: str) -> list[int]:
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, DOCSTRING_OWNERS):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.append(first.lineno)
    return found


def violations_in(source: str, rel_path: str) -> list[str]:
    found = [(line, f"comment {text.strip()[:72]!r}") for line, text in prose_comments(source)]
    found.extend((line, "docstring") for line in docstring_lines(source))
    return [f"{rel_path}:{line}: {what}" for line, what in sorted(found)]
