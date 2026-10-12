from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

from alan import base_ref as _base_ref

LOAD_BEARING_FILES: frozenset[str] = frozenset({
    "alan/server.py",
    "alan/routes/infra.py",
    "alan/routes/_deps.py",
    "alan/claude_cli.py",
    "alan/__main__.py",
    "deploy/ai.alan.plist",
    ".github/workflows/ci-deploy.yml",
    "pyproject.toml",
    "scripts/deploy_restart_decision.py",
    "alan/served_sha.py",
    "alan/deploy_refresh.py",
    "scripts/deploy_refresh.py",
})

_DOC_PATTERNS: tuple[str, ...] = (
    "docs/architecture/**",
    "CLAUDE.md",
    "docs/AGENTIC_CODING_GUIDE.md",
)


def _is_doc_target(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in _DOC_PATTERNS)


def doc_target_hint() -> str:
    parts = [
        f"a path under `{pat[:-2]}`" if pat.endswith("/**") else f"`{pat}`"
        for pat in _DOC_PATTERNS
    ]
    return ", ".join(parts[:-1]) + ", or " + parts[-1]


_SAFE_BASE_REF = re.compile(r"\A[0-9A-Za-z][0-9A-Za-z._/~^-]*\Z")


class BaseUnresolvedError(RuntimeError):
    pass


def _resolve_cwd(cwd: Path | None) -> Path:
    return cwd if cwd is not None else Path.cwd()


def changed_paths(base_ref: str | None = None, cwd: Path | None = None) -> list[str]:
    resolved_cwd = _resolve_cwd(cwd)
    if base_ref is None:
        resolved = _base_ref.resolve(resolved_cwd)
        if resolved is None:
            raise BaseUnresolvedError(
                "could not resolve origin/main: git fetch origin main failed, "
                "timed out, or git was unavailable"
            )
        base_ref = resolved
    if not _SAFE_BASE_REF.fullmatch(base_ref):
        raise ValueError(f"unsafe base ref: {base_ref!r}")
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=str(resolved_cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git diff failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return [p for p in result.stdout.splitlines() if p]


def drift_gate_violated(paths: list[str]) -> bool:
    if not any(p in LOAD_BEARING_FILES for p in paths):
        return False
    return not any(_is_doc_target(p) for p in paths)


def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m alan.drift_gate",
        description="Check whether load-bearing file changes include a required doc update.",
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--base-ref", default=None)
    args = parser.parse_args(argv)
    try:
        paths = changed_paths(args.base_ref)
    except BaseUnresolvedError:
        print("DRIFT GATE: base unresolved — check skipped", file=sys.stderr)
        sys.exit(0)
    except (RuntimeError, ValueError) as exc:
        print(f"DRIFT GATE: {exc}", file=sys.stderr)
        sys.exit(1)
    if drift_gate_violated(paths):
        lb = sorted(p for p in paths if p in LOAD_BEARING_FILES)
        touched = ", ".join(lb)
        print(
            "DRIFT GATE: load-bearing files changed without a doc update.\n"
            f"Touched: {touched}\n"
            "Update docs/architecture/*.md, CLAUDE.md, or docs/AGENTIC_CODING_GUIDE.md "
            "to reflect the change, or add the 'drift-ok' PR label for a human-approved bypass.",
            file=sys.stderr,
        )
        if args.check:
            sys.exit(1)


if __name__ == "__main__":
    _main()
