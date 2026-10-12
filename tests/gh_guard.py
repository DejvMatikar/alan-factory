from __future__ import annotations

import dataclasses
import os
import re
import shlex
import subprocess
import threading
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

READ_ONLY_GH_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset({
    ("pr", "list"),
    ("pr", "view"),
    ("pr", "diff"),
})

MUTATING_GH_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset({
    ("issue", "create"), ("issue", "comment"), ("issue", "close"),
    ("issue", "edit"), ("issue", "delete"),
    ("pr", "create"), ("pr", "merge"), ("pr", "review"),
    ("pr", "comment"), ("pr", "close"), ("pr", "edit"),
    ("pr", "update-branch"),
    ("label", "create"), ("label", "delete"),
})

SAFE_HTTP_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})

GH_STUB_RETURNCODE = 86
GH_STUB_STDERR = (
    "gh stubbed by tests/gh_guard.py: alan's test suite never runs the real gh. "
    "Mock the call (alan.collision.check_collision, alan.predispatch_scan.list_open_prs, "
    "or alan.process_runner.run_subprocess) instead of relying on this stub.\n"
)
AMBIENT_GH_CREDENTIAL_ENV: tuple[str, ...] = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
)
GH_READ_REMEDY = (
    "{key} reached a read-only gh call ({n} call(s): {calls}) that tests/gh_guard.py "
    "stubbed instead of spawning. This test is not in tests/gh_read_baseline.py, and the "
    "baseline only shrinks. Mock the call at its seam: "
    "monkeypatch.setattr(alan.collision, 'check_collision', ...) for route tests that reach "
    "the collision check; patch('alan.predispatch_scan.list_open_prs', ...) for predispatch; "
    "monkeypatch.setattr(alan.process_runner, 'run_subprocess', ...) for team/PR code; or "
    "request the expect_gh_read_stub fixture if exercising the stub is this test's purpose. "
    "Never add an entry to the baseline to make a test pass."
)

_FIELD_OR_INPUT_FLAGS = frozenset({"-f", "-F", "--raw-field", "--field", "--input"})
_QUERY_VALUE_PREFIX = "query="
_LEADING_KEYWORD = re.compile(r"^[A-Za-z_]+")
_NOT_A_GH_INVOCATION: tuple[str, str] = ("", "")
_TEXT_MODE_KWARGS = ("text", "universal_newlines", "encoding", "errors")
_POPEN_REASON = (
    "gh via subprocess.Popen is never permitted in tests: production spawns gh only "
    "through subprocess.run, so a Popen gh is either a new spawn site or a bypass attempt"
)
_EXPECTED_BUT_ABSENT = (
    "{key} requested expect_gh_read_stub but no gh read was stubbed during the test"
)


@dataclass(frozen=True)
class GhDecision:
    permitted: bool
    subcommand_pair: tuple[str, str]
    reason: str
    flags: tuple[str, ...] = ()
    is_gh: bool = True


class GhGuardBlocked(BaseException):
    pass


@dataclass(frozen=True)
class BlockedCall:
    decision: GhDecision
    thread_name: str


@dataclass(frozen=True)
class StubbedRead:
    decision: GhDecision
    test_key: str | None
    thread_name: str


_BLOCKED_LOCK = threading.Lock()
_BLOCKED_CALLS: list[BlockedCall] = []
_STUBBED_READS: list[StubbedRead] = []
_current_test_key: str | None = None


def record_blocked(decision: GhDecision) -> None:
    with _BLOCKED_LOCK:
        _BLOCKED_CALLS.append(BlockedCall(decision, threading.current_thread().name))


def drain_background_thread_blocks() -> list[BlockedCall]:
    with _BLOCKED_LOCK:
        drained = [c for c in _BLOCKED_CALLS if c.thread_name != "MainThread"]
        _BLOCKED_CALLS.clear()
    return drained


def set_current_test(key: str | None) -> None:
    global _current_test_key
    with _BLOCKED_LOCK:
        _current_test_key = key


def record_stubbed_read(decision: GhDecision) -> None:
    with _BLOCKED_LOCK:
        _STUBBED_READS.append(
            StubbedRead(decision, _current_test_key, threading.current_thread().name)
        )


def peek_stubbed_reads() -> list[StubbedRead]:
    with _BLOCKED_LOCK:
        return list(_STUBBED_READS)


def drain_stubbed_reads() -> list[StubbedRead]:
    with _BLOCKED_LOCK:
        drained = list(_STUBBED_READS)
        _STUBBED_READS.clear()
    return drained


def stubbed_completed_process(
    cmd: Any, kwargs: Mapping[str, Any],
) -> subprocess.CompletedProcess[Any]:
    text_mode = any(kwargs.get(name) for name in _TEXT_MODE_KWARGS)
    stdout: str | bytes = "" if text_mode else b""
    stderr: str | bytes = GH_STUB_STDERR if text_mode else GH_STUB_STDERR.encode()
    if kwargs.get("check"):
        raise subprocess.CalledProcessError(GH_STUB_RETURNCODE, cmd, stdout, stderr)
    return subprocess.CompletedProcess(cmd, GH_STUB_RETURNCODE, stdout, stderr)


def popen_refusal(decision: GhDecision) -> GhDecision:
    return dataclasses.replace(decision, permitted=False, reason=_POPEN_REASON)


def strip_ambient_gh_credentials(environ: MutableMapping[str, str]) -> list[str]:
    return [name for name in AMBIENT_GH_CREDENTIAL_ENV if environ.pop(name, None) is not None]


def baseline_key(nodeid: str) -> str:
    return nodeid.split("[", 1)[0]


def gh_read_ratchet_failure(
    key: str, reads: Sequence[StubbedRead], *, listed: bool, expected: bool,
) -> str | None:
    if expected:
        return None if reads else _EXPECTED_BUT_ABSENT.format(key=key)
    if not reads or listed:
        return None
    calls = "; ".join(
        f"gh {' '.join(r.decision.subcommand_pair).strip()} [{r.thread_name}]" for r in reads
    )
    return GH_READ_REMEDY.format(key=key, n=len(reads), calls=calls)


def append_baseline_key(path: Path, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(key + "\n")


def _flag_names(tokens: list[str]) -> tuple[str, ...]:
    return tuple(sorted({t.split("=", 1)[0] for t in tokens if t.startswith("-")}))


def _extract_method(tokens: list[str]) -> str | None:
    for i, tok in enumerate(tokens):
        if tok in ("-X", "--method") and i + 1 < len(tokens):
            return tokens[i + 1]
        if tok.startswith("-X") and len(tok) > 2:
            return tok[2:]
        if tok.startswith("--method="):
            return tok.split("=", 1)[1]
    return None


def _has_field_or_input_flag(tokens: list[str]) -> bool:
    return any(tok.split("=", 1)[0] in _FIELD_OR_INPUT_FLAGS for tok in tokens)


def _extract_graphql_document(tokens: list[str]) -> str | None:
    found: list[str] = []
    for i, tok in enumerate(tokens):
        if tok in ("-f", "--raw-field") and i + 1 < len(tokens):
            val = tokens[i + 1]
            if val.startswith(_QUERY_VALUE_PREFIX):
                found.append(val[len(_QUERY_VALUE_PREFIX):])
    if len(found) != 1:
        return None
    return found[0]


def _classify_graphql(
    tokens: list[str], pair: tuple[str, str], flags: tuple[str, ...],
) -> GhDecision:
    document = _extract_graphql_document(tokens)
    if document is None:
        return GhDecision(
            False, pair,
            "graphql document not carried by exactly one -f/--raw-field query= value",
            flags,
        )
    match = _LEADING_KEYWORD.match(document.strip())
    if match is None or match.group(0) != "query":
        return GhDecision(False, pair, "graphql document is not a query operation", flags)
    return GhDecision(True, pair, "graphql query document", flags)


def _classify_rest_api(
    positional: list[str], tokens: list[str], flags: tuple[str, ...],
) -> GhDecision:
    endpoint = positional[1] if len(positional) >= 2 else ""
    pair = ("api", endpoint)
    method = _extract_method(tokens)
    if method is not None:
        if method.upper() in SAFE_HTTP_METHODS:
            return GhDecision(True, pair, "explicit safe HTTP method", flags)
        return GhDecision(False, pair, "explicit mutating HTTP method", flags)
    if _has_field_or_input_flag(tokens):
        return GhDecision(
            False, pair, "implicit write: field/input flag present without an explicit method",
            flags,
        )
    return GhDecision(True, pair, "implicit read: no method or field/input flag", flags)


def _classify_api(
    positional: list[str], tokens: list[str], flags: tuple[str, ...],
) -> GhDecision:
    endpoint = positional[1] if len(positional) >= 2 else ""
    if endpoint == "graphql":
        return _classify_graphql(tokens, ("api", endpoint), flags)
    return _classify_rest_api(positional, tokens, flags)


def _classify_gh_tokens(tokens: list[str]) -> GhDecision:
    positional = [t for t in tokens if not t.startswith("-")]
    flags = _flag_names(tokens)
    if not positional:
        return GhDecision(
            False, _NOT_A_GH_INVOCATION,
            "no subcommand parsed (a global flag before the subcommand shifts the parse)",
            flags,
        )
    if positional[0] == "api":
        return _classify_api(positional, tokens, flags)
    pair = (positional[0], positional[1]) if len(positional) >= 2 else (positional[0], "")
    if pair in READ_ONLY_GH_SUBCOMMANDS:
        return GhDecision(True, pair, "recognised read-only subcommand", flags)
    return GhDecision(False, pair, "subcommand not on the read-only allow-list", flags)


def _basename_is_gh(token: str) -> bool:
    return os.path.basename(token) == "gh"


def _decode_argv_element(tok: object) -> str | None:
    if isinstance(tok, str):
        return tok
    if isinstance(tok, (bytes, os.PathLike)):
        try:
            return os.fsdecode(tok)
        except (UnicodeDecodeError, TypeError, ValueError):
            return None
    return None


def _classify_shell_string(cmd: str) -> GhDecision:
    try:
        argv = shlex.split(cmd)
    except ValueError:
        parts = cmd.split(None, 1)
        if parts and _basename_is_gh(parts[0]):
            return GhDecision(False, _NOT_A_GH_INVOCATION, "unparseable shell string invoking gh")
        return GhDecision(True, _NOT_A_GH_INVOCATION, "not a gh invocation", is_gh=False)
    return classify(argv)


def _classify_single_command(cmd: object) -> GhDecision:
    decoded = _decode_argv_element(cmd)
    if decoded is None or not _basename_is_gh(decoded):
        return GhDecision(True, _NOT_A_GH_INVOCATION, "not a gh invocation", is_gh=False)
    return GhDecision(
        False, _NOT_A_GH_INVOCATION,
        "gh invoked as a bare path-like or bytes command with no argv to classify",
    )


def classify(cmd: object) -> GhDecision:
    if isinstance(cmd, str):
        return _classify_shell_string(cmd)
    if isinstance(cmd, (bytes, os.PathLike)):
        return _classify_single_command(cmd)
    if not isinstance(cmd, (list, tuple)) or not cmd:
        return GhDecision(True, _NOT_A_GH_INVOCATION, "not a gh invocation", is_gh=False)
    first = _decode_argv_element(cmd[0])
    if first is None or not _basename_is_gh(first):
        return GhDecision(True, _NOT_A_GH_INVOCATION, "not a gh invocation", is_gh=False)
    tokens: list[str] = []
    for element in cmd[1:]:
        decoded = _decode_argv_element(element)
        if decoded is None:
            return GhDecision(
                False, _NOT_A_GH_INVOCATION,
                "argv element of unrecognised type or undecodable bytes; "
                "refusing rather than silently dropping it",
            )
        tokens.append(decoded)
    return _classify_gh_tokens(tokens)


def refusal_message(decision: GhDecision) -> str:
    subcommand, verb = decision.subcommand_pair
    pair_display = f"{subcommand} {verb}".strip() or "(unparsed)"
    flags_display = ", ".join(decision.flags) if decision.flags else "(none)"
    return (
        f"gh guard refused 'gh {pair_display}' (flags: {flags_display}): {decision.reason}. "
        "Mock this call in the test (patch subprocess.run/Popen, or the wrapping helper); "
        "never widen tests/gh_guard.py's read-only allow-list to make a test pass. "
        "If this looks wrong, check whether a global flag was placed before the subcommand "
        "(e.g. 'gh --repo o/r pr view 1') — that shifts the positional parse and produces "
        "exactly this refusal."
    )
