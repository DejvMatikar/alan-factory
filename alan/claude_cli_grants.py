from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from alan import default_profile, roots
from alan.control_token_store import CONTROL_TOKEN_FILENAME, CONTROL_TOKEN_META_FILENAME

WRITE_TOOLS: tuple[str, ...] = ("Write", "Edit", "NotebookEdit")

ACCEPT_EDITS_MODE = "acceptEdits"

CREDENTIAL_BASH_DENIES: tuple[str, ...] = (
    "Bash(security:*)",
    "Bash(git credential:*)",
    "Bash(gh auth:*)",
    "Bash(git diff --no-index:*)",
    "Bash(git blame --contents:*)",
)

GENERAL_EXEC_GRANTS: tuple[str, ...] = (
    "Bash(uv run:*)",
    "Bash(npx:*)",
    "Bash(npm --prefix:*)",
)

_PROMPT_INSTRUCTED_TOOLS: tuple[str, ...] = ("lizard", "xenon", "vulture", "pytest")

PROMPT_INSTRUCTED_GRANTS: tuple[str, ...] = (
    *(f"Bash(uv run {tool}:*)" for tool in _PROMPT_INSTRUCTED_TOOLS),
    *(f"Bash(uv run --no-active {tool}:*)" for tool in _PROMPT_INSTRUCTED_TOOLS),
    "Bash(npm --prefix alan/web run:*)",
    "Bash(npm --prefix alan/web ci:*)",
)

_SECRETS_ENV_FILENAME = "secrets.env"
_CLAUDE_CREDENTIALS_FILENAME = ".credentials.json"

_SAFE_TOKEN = re.compile(r"[\w./=:@+,%^-]+")
_PLAIN_TOOL = re.compile(r"[A-Za-z0-9_][\w.-]*")
_INTERPRETER = re.compile(
    r"python[\d.]*|node|bash|sh|zsh|dash|env|perl|ruby|php|deno|bun|npx|uvx|uv|xargs|exec|eval"
)

CHAT_ISSUE_READ_TOOL = "Bash(gh issue view:*)"

CHAT_ISSUE_SCOPE_CLAUSE = (
    "You can read GitHub issues in the repository this session is bound to "
    "— by issue number or by a pasted issue URL — via `gh issue view`, and "
    "should read one whenever the user references it rather than declining "
    "as if you could not. A pasted URL names the repository it belongs to: "
    "if that repository is the one this session is bound to, read the "
    "trailing issue number out of the URL yourself and run `gh issue view "
    "<number>` in this same turn. An issue in a different repository is "
    "out of scope: decline it rather than fetching it.\n"
)

PREP_CHAT_SCOPE_CLAUSE = (
    "You can read a preparation conversation — an earlier build-chat or "
    "explore-chat session, whether still in progress or already archived "
    "onto the workspace it produced — via WebFetch whenever the user "
    "pastes a link to it, rather than declining as if you could not. Take "
    "the session id out of the pasted link yourself and fetch "
    "{prep_chat_origin}/api/build-chat/sessions/<id> (or "
    "/api/explore-chat/sessions/<id> for an explore session) — always "
    "against that host, never a loopback host the pasted link may show, "
    "which WebFetch cannot reach once it upgrades the request to https. "
    "An already-archived session's link now redirects to its transcript; "
    "WebFetch follows that redirect for you, so the same fetch reads it "
    "whether the conversation is still live or already archived. This "
    "session is itself bound to {repo_label}, and the fetched response "
    "names the repository the read conversation is bound to in its "
    "`engine_key` field (`null` there means alan's own repository). Read "
    "and act on the fetched conversation only when its repository is the "
    "one this session is bound to; a preparation conversation bound to a "
    "different repository is out of scope — decline it and tell the user "
    "why, rather than reading it, exactly like a GitHub issue from a "
    "different repository.\n"
)

OWN_REPO_LABEL = "alan (this repository)"

BUILD_REPO_BINDING = (
    "## Repository binding\n"
    "This conversation is bound to {repo_label}. Investigate only that "
    "repository's files and contents through your read-only tools — never "
    "read or reference a different repository's code in its place. Reading "
    "a preparation conversation is a narrow exception, not a violation of "
    "this binding: that conversation is served by the orchestration "
    "service itself, never by a repository.\n"
)

_BUILD_NO_IMPL_HEAD = (
    "## Read-only investigation posture\n"
    "Allowed tools: Read, Grep, Glob, WebSearch, WebFetch, and Bash limited "
    "to `git log`, `git diff`, `ls`, `cat`, `gh pr list`, `gh pr view`, "
    "`gh issue view`. "
    "Forbidden: Write, Edit, NotebookEdit, and any non-read Bash. "
    "If a tool call is rejected, do not retry.\n"
    "Never claim you implemented, edited, created, modified, or fixed "
    "anything. You shape the request; you do not build it.\n"
    f"{CHAT_ISSUE_SCOPE_CLAUSE}"
)

_EXPLORE_NO_IMPL_HEAD = (
    "## Read-only investigation posture\n"
    "Allowed tools: Read, Grep, Glob, WebSearch, WebFetch, and Bash limited "
    "to `git log`, `git diff`, `ls`, `cat`, `gh pr list`, `gh pr view`, "
    "`gh issue view`. "
    "Forbidden: Write, Edit, NotebookEdit, and any non-read Bash. "
    "If a tool call is rejected, do not retry.\n"
    "Never claim you implemented, edited, created, modified, or fixed "
    "anything. You shape and brief the work; you do not perform it.\n"
    f"{CHAT_ISSUE_SCOPE_CLAUSE}"
)


def configured_prep_chat_origin() -> str | None:
    return default_profile.resolved().profile.prep_chat_origin


def prep_chat_scope_clause(repo_label: str, prep_chat_origin: str | None) -> str:
    if prep_chat_origin is None:
        return ""
    return PREP_CHAT_SCOPE_CLAUSE.format(prep_chat_origin=prep_chat_origin, repo_label=repo_label)


def build_no_impl(repo_label: str, prep_chat_origin: str | None) -> str:
    return _BUILD_NO_IMPL_HEAD + prep_chat_scope_clause(repo_label, prep_chat_origin)


def explore_no_impl(repo_label: str, prep_chat_origin: str | None) -> str:
    return _EXPLORE_NO_IMPL_HEAD + prep_chat_scope_clause(repo_label, prep_chat_origin)


NO_TOOLS_BLOCK = (
    "## No tools in this invocation\n"
    "You have no tools in this invocation. Never call, attempt, describe or "
    "simulate a tool call, and never write a tool-call or tool-result block: "
    "do not announce that you will check, read, search or open anything. "
    "Everything you need is already in this prompt. You must still emit every "
    "mandatory machine-readable line your role requires, in the exact form "
    "your role specifies.\n"
    "Only if your role's output contract asks for a judgement grounded in "
    "repository evidence: state plainly that the evidence is absent from this "
    "prompt, then proceed and give your judgement. If your role is to "
    "reproduce text verbatim or write a short summary, do not mention missing "
    "evidence or missing tools at all."
)


def _path_or_none(raw: str | None) -> Path | None:
    stripped = (raw or "").strip()
    return Path(stripped) if stripped else None


def _gh_config_dir() -> Path:
    explicit = _path_or_none(os.environ.get("GH_CONFIG_DIR"))
    if explicit is not None:
        return explicit
    xdg = _path_or_none(os.environ.get("XDG_CONFIG_HOME"))
    return (xdg if xdg is not None else Path.home() / ".config") / "gh"


def _claude_credential_files() -> list[Path]:
    config_dirs = [Path.home() / ".claude"]
    override = _path_or_none(os.environ.get("CLAUDE_CONFIG_DIR"))
    if override is not None:
        config_dirs.append(override)
    return [config_dir / _CLAUDE_CREDENTIALS_FILENAME for config_dir in config_dirs]


def alan_secret_files() -> list[Path]:
    state = roots.state_root()
    return [
        state.parent / _SECRETS_ENV_FILENAME,
        state / CONTROL_TOKEN_FILENAME,
        state / CONTROL_TOKEN_META_FILENAME,
    ]


def alan_secret_dirs() -> list[Path]:
    return [roots.secrets_store_root(), roots.engines_root()]


def credential_files() -> list[Path]:
    return [*alan_secret_files(), *_claude_credential_files()]


def credential_dirs() -> list[Path]:
    return [*alan_secret_dirs(), _gh_config_dir()]


def _path_forms(path: Path) -> list[str]:
    return list(dict.fromkeys([str(path.absolute()), str(path.resolve())]))


def credential_read_rules() -> list[str]:
    file_rules = [f"Read(/{form})" for path in credential_files() for form in _path_forms(path)]
    dir_rules = [f"Read(/{form}/**)" for path in credential_dirs() for form in _path_forms(path)]
    return [*file_rules, *dir_rules]


def credential_deny_rules() -> list[str]:
    return list(dict.fromkeys([*credential_read_rules(), *CREDENTIAL_BASH_DENIES]))


def _is_plain_tool(token: str) -> bool:
    return bool(_PLAIN_TOOL.fullmatch(token)) and not _INTERPRETER.fullmatch(token)


def _uv_run_tool(command: Sequence[str]) -> str | None:
    if list(command[:2]) != ["uv", "run"]:
        return None
    rest = list(command[2:])
    if rest[:1] == ["--no-active"]:
        rest = rest[1:]
    if rest and _is_plain_tool(rest[0]):
        return rest[0]
    return None


def _npm_prefix_dir(command: Sequence[str]) -> str | None:
    if len(command) < 5 or list(command[:2]) != ["npm", "--prefix"] or command[3] != "run":
        return None
    return command[2] if _SAFE_TOKEN.fullmatch(command[2]) else None


def _is_argument(word: str) -> bool:
    return not word.startswith("-") and word != "run" and not _INTERPRETER.fullmatch(word)


def _full_command_grant(command: Sequence[str]) -> list[str]:
    safe = all(_SAFE_TOKEN.fullmatch(word) for word in command)
    if safe and any(_is_argument(word) for word in command):
        return [f"Bash({' '.join(command)}:*)"]
    return []


def _grants_for_gate(command: Sequence[str]) -> list[str]:
    tool = _uv_run_tool(command)
    if tool is not None:
        return [f"Bash(uv run {tool}:*)", f"Bash(uv run --no-active {tool}:*)"]
    prefix_dir = _npm_prefix_dir(command)
    if prefix_dir is not None:
        return [f"Bash(npm --prefix {prefix_dir} run:*)"]
    return _full_command_grant(command)


def gate_tool_grants(gate_commands: Sequence[Sequence[str]]) -> list[str]:
    return [grant for command in gate_commands for grant in _grants_for_gate(command)]


def narrowed_tools(tools: Iterable[str]) -> list[str]:
    return [tool for tool in tools if tool not in GENERAL_EXEC_GRANTS]


def with_gate_reachability(
    tools: Iterable[str], gate_commands: Sequence[Sequence[str]]
) -> list[str]:
    granted = [*tools, *gate_tool_grants(gate_commands), *PROMPT_INSTRUCTED_GRANTS]
    return list(dict.fromkeys(granted))


def disallowed_tools_args(*, deny_all_tools: bool, permission_mode: str) -> list[str]:
    if deny_all_tools:
        return ["--disallowedTools", "*"]
    write_denies = [] if permission_mode == ACCEPT_EDITS_MODE else [" ".join(WRITE_TOOLS)]
    return ["--disallowedTools", *write_denies, *credential_deny_rules()]


def _effective_tools(allowed_tools: list[str] | None, permission_mode: str) -> list[str]:
    tools = allowed_tools or []
    return tools if permission_mode == ACCEPT_EDITS_MODE else narrowed_tools(tools)


def grant_args(
    *,
    allowed_tools: list[str] | None,
    add_dirs: list[str] | None,
    deny_all_tools: bool,
    permission_mode: str = "auto",
) -> list[str]:
    if deny_all_tools:
        return []
    args: list[str] = []
    tools = _effective_tools(allowed_tools, permission_mode)
    if tools:
        args.extend(["--allowedTools", *tools])
    if add_dirs:
        args.extend(["--add-dir", *add_dirs])
    return args
