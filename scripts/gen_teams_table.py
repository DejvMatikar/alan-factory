"""Render the Registered-teams table in docs/architecture/teams_taxonomy.md from
the ``alan.teams`` registry (``TEAMS`` / ``TEAM_ROLE_SUMMARIES`` / the
dispatchability sets), so the table can never drift from the code.

Run `uv run python scripts/gen_teams_table.py --check` to verify the doc is
current (used by tests/test_gen_teams_table.py), or `--write` to regenerate
the marker block in place after the registry changes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alan.teams import (
    INITIAL_DISPATCHABLE_TEAMS,
    INTERNAL_ONLY_TEAMS,
    MANUALLY_DISPATCHABLE_TEAMS,
    TEAM_ROLE_SUMMARIES,
    TEAMS,
)

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "architecture" / "teams_taxonomy.md"
BEGIN_MARKER = "<!-- BEGIN GENERATED: teams-table -->"
END_MARKER = "<!-- END GENERATED: teams-table -->"
NO_INSTANCE_SENTINEL = "— (no `TEAMS` instance)"


def check_membership() -> list[str]:
    return sorted(set(TEAMS) - set(TEAM_ROLE_SUMMARIES))


def primitive_for(name: str) -> str:
    team = TEAMS.get(name)
    if team is None:
        return NO_INSTANCE_SENTINEL
    return type(team).__name__


def dispatchability(name: str) -> str:
    if name in INTERNAL_ONLY_TEAMS:
        return "internal-only"
    if name in INITIAL_DISPATCHABLE_TEAMS:
        return "dispatchable"
    if name in MANUALLY_DISPATCHABLE_TEAMS:
        return "dispatchable (post-initial)"
    return "not user-dispatchable (no `TEAMS` entry)"


def render_table() -> str:
    missing = check_membership()
    if missing:
        raise ValueError(
            f"TEAMS entries missing from TEAM_ROLE_SUMMARIES: {missing}"
        )
    lines = [
        "| Name | Primitive | Dispatchability | Summary |",
        "|------|-----------|------------------|---------|",
    ]
    for name in sorted(TEAM_ROLE_SUMMARIES):
        lines.append(
            f"| `{name}` | {primitive_for(name)} | {dispatchability(name)} "
            f"| {TEAM_ROLE_SUMMARIES[name]} |"
        )
    return "\n".join(lines)


def render_block() -> str:
    return f"{BEGIN_MARKER}\n{render_table()}\n{END_MARKER}"


def splice(doc_text: str, block: str) -> str:
    start = doc_text.index(BEGIN_MARKER)
    end = doc_text.index(END_MARKER) + len(END_MARKER)
    return doc_text[:start] + block + doc_text[end:]


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/gen_teams_table.py",
        description=(
            "Regenerate or check the generated Registered-teams table in "
            "docs/architecture/teams_taxonomy.md."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check", action="store_true", help="Exit non-zero if the doc is stale."
    )
    group.add_argument(
        "--write", action="store_true", help="Regenerate the doc in place."
    )
    args = parser.parse_args(argv)

    doc_text = DOC_PATH.read_text()
    updated = splice(doc_text, render_block())

    if args.write:
        DOC_PATH.write_text(updated)
        return 0

    if updated != doc_text:
        print(
            "DRIFT: docs/architecture/teams_taxonomy.md is stale relative to "
            "alan.teams. Run `uv run python scripts/gen_teams_table.py --write` "
            "to regenerate.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
