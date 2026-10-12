from __future__ import annotations

import argparse
import json
import plistlib
import sys
from pathlib import Path
from typing import cast

from alan import setup_transport

LABEL = "ai.alan.claude-check"
NO_METHOD_LABEL = "method not reported"
_METHODS: tuple[tuple[str, str], ...] = (
    ("claude.ai", "Claude account"),
    ("console", "Anthropic Console"),
    ("oauth_token", "OAuth token"),
    ("api_key", "API key"),
    ("bedrock", "Amazon Bedrock"),
    ("vertex", "Google Vertex AI"),
    ("foundry", "Microsoft Foundry"),
)
METHOD_LABELS: dict[str, str] = dict(_METHODS)
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_UNFINISHED = 3

_WRAPPER = 'out=$("$0" auth status 2>&1); printf "%s\\n%s\\n" "$?" "$out"'
_UNREADABLE_CLAUDE_MESSAGE = "the claude executable must be given as an absolute path"


class ClaudeCheckRefused(RuntimeError):
    pass


def plist_text(claude: str, log: Path) -> str:
    if not Path(claude).is_absolute():
        raise ClaudeCheckRefused(_UNREADABLE_CLAUDE_MESSAGE)
    manifest = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", "-c", _WRAPPER, claude],
        "RunAtLoad": True,
        "StandardOutPath": str(log),
        "StandardErrorPath": "/dev/null",
        "ProcessType": "Background",
    }
    return plistlib.dumps(manifest, fmt=plistlib.FMT_XML).decode("utf-8")


def _split_log(text: str) -> tuple[int, str] | None:
    head, _, rest = text.partition("\n")
    if not head.strip().isdigit():
        return None
    return int(head), rest


def _answer(output: str) -> dict[str, object]:
    try:
        parsed = cast("object", json.loads(output))
    except json.JSONDecodeError:
        return {}
    return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else {}


def _method_line(answer: dict[str, object]) -> str:
    method = answer.get("authMethod")
    label = METHOD_LABELS.get(method.lower()) if isinstance(method, str) else None
    return NO_METHOD_LABEL if label is None else f"method: {label}"


def verdict(log: Path) -> tuple[int, list[str]]:
    try:
        finished = _split_log(log.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        finished = None
    if finished is None:
        return EXIT_UNFINISHED, []
    code, output = finished
    answer = _answer(output)
    line = _method_line(answer)
    if code == 0 and answer.get("loggedIn") is not False:
        return EXIT_PASS, ["pass", line]
    return EXIT_FAIL, ["fail", line]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m alan.setup_claude_check")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("plist")
    make.add_argument("--claude", required=True)
    make.add_argument("--plist", type=Path, required=True)
    make.add_argument("--log", type=Path, required=True)
    result = commands.add_parser("verdict")
    result.add_argument("--log", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verdict":
        code, lines = verdict(args.log)
        for line in lines:
            print(line)
        return code
    try:
        setup_transport.write_private_file(args.plist, plist_text(args.claude, args.log))
    except (ClaudeCheckRefused, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
