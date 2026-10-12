from __future__ import annotations

import argparse
import sys

from alan import default_profile, default_profile_writer, engine_preflight, roots

STATE_LATCHED = "latched"
STATE_NO_PROJECT = "no-project"
STATE_NOT_LATCHED = "not-latched"
STATE_NOT_CONNECTED = "not-connected"
STATE_UNREPORTED = "unreported"
EXIT_BY_STATE = {STATE_LATCHED: 0, STATE_NO_PROJECT: 1, STATE_NOT_LATCHED: 2}

_NO_PROJECT_MESSAGE = (
    "the installed profile names no SonarCloud project for this repository; "
    "run the sonar step first"
)


class LatchRefused(RuntimeError):
    pass


def _project(slug: str) -> default_profile.CodeQualityProfile | None:
    owner, _, repo = slug.partition("/")
    try:
        return default_profile_writer.installed_code_quality(roots.engines_root(), owner, repo)
    except (ValueError, OSError):
        return None


def _recorded() -> engine_preflight.CodeQualityLatch | None:
    try:
        return engine_preflight.read_code_quality_latch()
    except (ValueError, OSError):
        return None


def latch_state(slug: str) -> str:
    project = _project(slug)
    if project is None:
        return STATE_NO_PROJECT
    recorded = _recorded()
    if recorded is None:
        return STATE_NOT_LATCHED
    wanted = (project.organization, project.project_key)
    held = (recorded.organization, recorded.project_key)
    return STATE_LATCHED if held == wanted else STATE_NOT_LATCHED


def _requires_code_quality(slug: str) -> bool:
    owner, _, repo = slug.partition("/")
    try:
        return default_profile_writer.requires_code_quality(roots.engines_root(), owner, repo)
    except (ValueError, OSError):
        return True


def standing(slug: str) -> str:
    state = latch_state(slug)
    if state == STATE_LATCHED or _requires_code_quality(slug):
        return state
    return STATE_NOT_CONNECTED if state == STATE_NO_PROJECT else STATE_UNREPORTED


def write_latch(slug: str) -> None:
    project = _project(slug)
    if project is None:
        raise LatchRefused(_NO_PROJECT_MESSAGE)
    try:
        engine_preflight.write_code_quality_latch(project.organization, project.project_key)
    except OSError:
        raise LatchRefused("the SonarCloud latch could not be written") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m alan.setup_latch")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("write", "state", "standing"):
        commands.add_parser(name).add_argument("--engine", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "standing":
        print(standing(args.engine))
        return 0
    if args.command == "state":
        state = latch_state(args.engine)
        print(state)
        return EXIT_BY_STATE[state]
    try:
        write_latch(args.engine)
    except LatchRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
