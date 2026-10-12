from __future__ import annotations

import functools
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest

from alan.setup_transport import PERMISSIONS_TEXT
from tests._setup_stubs import REPO_ROOT
from tests._setup_support import brew_formulae

COPY_SCRIPT = REPO_ROOT / "scripts" / "setup_macos.sh"
BOOTSTRAP = REPO_ROOT / "setup.sh"
TUTORIALS = REPO_ROOT / "docs" / "tutorials"
SECURITY_TUTORIAL = TUTORIALS / "security.md"
SONAR_TUTORIAL = TUTORIALS / "sonarcloud.md"

BOOTSTRAP_ENTRY = "./setup.sh"
COPY_ENTRY = "scripts/setup_macos.sh"
ENTRY_POINTS = frozenset({BOOTSTRAP_ENTRY, COPY_ENTRY})
ENTRY_SUFFIXES = ("setup.sh", "setup_macos.sh")

PARSE_ARGS = re.compile(r"^parse_args\(\) \{\n(.*?)^\}$", re.MULTILINE | re.DOTALL)
CASE_ARM = re.compile(
    r"^ {6}(--[a-z][a-z-]*)\)(.*?)(?=^ {6}(?:--[a-z][a-z-]*|\*)\))",
    re.MULTILINE | re.DOTALL,
)
STEP_ASSIGNMENT = re.compile(r"\b(?:FROM|ONLY)_STEP=")
GUARD = re.compile(r'"\$(ACCEPTS_[A-Z_]+)" != 1')
STEPS_LINE = re.compile(r'^SETUP_STEPS="([^"]*)"$', re.MULTILINE)
ACCEPTS_LINE = re.compile(r"^(ACCEPTS_[A-Z_]+)=(\d)$", re.MULTILINE)

CODE_SPAN = re.compile(r"```[^\n]*\n(.*?)```|`([^`\n]+)`", re.DOTALL)
CONTINUATION = re.compile(r"\\\n")
PLACEHOLDER_TOKEN = re.compile(r"<[A-Za-z_-]+>")
OPERATOR_CHARS = frozenset("();<>|&")

PLACEHOLDER = re.compile(r"<setup: TODO P1\.10b>")
PERMISSION_LINE = re.compile(r"\s{4}\w.*: ")
PERMISSIONS_SECTION = "## What the transport token can reach"
FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

GITHUB_URL = re.compile(r"(?<![\w.-])github\.com/([^\s)\]`\"']+)")
OWNER_REPO = re.compile(r"<owner>/<repo>(?!\w)")
ACCOUNT_PAGE_SEGMENTS = frozenset({"settings"})
SLUG_SPAN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")

SYNTHETIC_COPY_SCRIPT = """\
SETUP_STEPS="alpha beta"
ACCEPTS_LEFT=1
ACCEPTS_RIGHT=0
parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --zap) require_value "$@"; ONLY_STEP="$2"; shift 2 ;;
      --name) require_value "$@"; NAME="$2"; shift 2 ;;
      --plain) PLAIN=1; shift ;;
      --right)
        if [ "$ACCEPTS_RIGHT" != 1 ]; then
          usage_error "no"
        fi
        shift
        ;;
      *) usage_error "unknown flag: $1" ;;
    esac
  done
}
"""
SYNTHETIC_BOOTSTRAP = 'SETUP_STEPS="alpha gamma beta"\nACCEPTS_RIGHT=1\n'


@dataclass(frozen=True)
class Flag:
    takes_value: bool
    takes_step: bool
    guard: str | None


@dataclass(frozen=True)
class EntryModel:
    steps: frozenset[str]
    accepts: dict[str, int]


@dataclass(frozen=True)
class Command:
    entry: str
    tokens: tuple[str, ...]


@functools.cache
def parse_flags(script: Path = COPY_SCRIPT) -> dict[str, Flag]:
    body = PARSE_ARGS.search(script.read_text(encoding="utf-8"))
    assert body is not None, f"{script.name} has no parse_args() function"
    arms = CASE_ARM.findall(body.group(1))
    assert arms, f"parse_args() in {script.name} has no flag arms"
    flags: dict[str, Flag] = {}
    for name, arm in arms:
        guard = GUARD.search(arm)
        flags[name] = Flag(
            takes_value="require_value" in arm,
            takes_step=STEP_ASSIGNMENT.search(arm) is not None,
            guard=guard.group(1) if guard else None,
        )
    return flags


def read_entry_values(path: Path) -> tuple[str | None, dict[str, int]]:
    text = path.read_text(encoding="utf-8")
    steps = STEPS_LINE.search(text)
    accepts = {name: int(value) for name, value in ACCEPTS_LINE.findall(text)}
    return (steps.group(1) if steps else None), accepts


@functools.cache
def entry_model(
    entry: str, copy_script: Path = COPY_SCRIPT, bootstrap: Path = BOOTSTRAP
) -> EntryModel:
    steps, accepts = read_entry_values(copy_script)
    assert steps is not None, f"{copy_script.name} has no SETUP_STEPS line"
    if entry == BOOTSTRAP_ENTRY:
        own_steps, own_accepts = read_entry_values(bootstrap)
        steps = own_steps or steps
        accepts = {**accepts, **own_accepts}
    return EntryModel(steps=frozenset(steps.split()), accepts=accepts)


def spans_of(text: str) -> list[str]:
    return [fenced or inline for fenced, inline in CODE_SPAN.findall(text)]


def setup_lines(text: str) -> list[str]:
    return [
        line
        for span in spans_of(text)
        for line in CONTINUATION.sub(" ", span).splitlines()
        if any(suffix in line for suffix in ENTRY_SUFFIXES)
    ]


def tokenise(line: str) -> list[str]:
    lexer = shlex.shlex(PLACEHOLDER_TOKEN.sub("value", line), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def is_operator(token: str) -> bool:
    return set(token) <= OPERATOR_CHARS


def split_commands(line: str) -> tuple[list[Command], list[str]]:
    groups: list[tuple[str, list[str]]] = []
    strays: list[str] = []
    open_group: list[str] | None = None
    for token in tokenise(line):
        if token in ENTRY_POINTS:
            open_group = []
            groups.append((token, open_group))
        elif is_operator(token):
            open_group = None
        elif open_group is not None:
            open_group.append(token)
        elif token.endswith(ENTRY_SUFFIXES):
            strays.append(token)
    return [Command(entry, tuple(tokens)) for entry, tokens in groups], strays


def extract_commands(text: str) -> tuple[list[Command], list[str]]:
    commands: list[Command] = []
    strays: list[str] = []
    for line in setup_lines(text):
        found, stray = split_commands(line)
        commands.extend(found)
        strays.extend(stray)
    return commands, strays


def flag_problem(command: Command, index: int) -> str | None:
    token = command.tokens[index]
    flag = parse_flags().get(token)
    if flag is None:
        return f"{token} is not a setup flag"
    model = entry_model(command.entry)
    if flag.guard is not None and model.accepts.get(flag.guard, 0) != 1:
        return f"{token} is not accepted by {command.entry}"
    if not flag.takes_value:
        return None
    following = command.tokens[index + 1 : index + 2]
    if not following or following[0].startswith("-"):
        return f"{token} has no value"
    if flag.takes_step and following[0] not in model.steps:
        return f"{token} names {following[0]}, which is not a step of {command.entry}"
    return None


def command_problems(command: Command) -> list[str]:
    problems: list[str] = []
    index = 0
    while index < len(command.tokens):
        problem = flag_problem(command, index)
        if problem is not None:
            problems.append(problem)
        flag = parse_flags().get(command.tokens[index])
        index += 2 if flag is not None and flag.takes_value else 1
    return problems


def violations(name: str, text: str) -> list[str]:
    commands, strays = extract_commands(text)
    found = [f"{name}: {stray} is not a spelling of a setup entry point" for stray in strays]
    for command in commands:
        found.extend(f"{name}: {command.entry}: {problem}" for problem in command_problems(command))
    return found


def tutorial_problems(texts: dict[str, str]) -> list[str]:
    problems: list[str] = []
    found = 0
    for name, text in texts.items():
        found += len(extract_commands(text)[0])
        problems.extend(violations(name, text))
        if PLACEHOLDER.search(text):
            problems.append(f"{name}: contains a setup placeholder")
    if not found:
        problems.append("no tutorial contains a setup command")
    return problems


def tutorial_texts() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(TUTORIALS.glob("*.md"))}


def printed_permissions() -> list[str]:
    return [line.strip() for line in PERMISSIONS_TEXT.splitlines() if PERMISSION_LINE.match(line)]


def documented_permissions(text: str) -> list[str]:
    _, _, tail = text.partition(PERMISSIONS_SECTION)
    block = FENCE.search(tail.split("\n## ", 1)[0])
    assert block is not None, f"security.md has no fenced block under {PERMISSIONS_SECTION!r}"
    return [line.strip() for line in block.group(1).splitlines() if line.strip()]


def permission_differences(
    printed: list[str], documented: list[str]
) -> tuple[list[str], list[str]]:
    missing = [line for line in printed if line not in documented]
    extra = [line for line in documented if line not in printed]
    return missing, extra


def repository_problems(name: str, text: str) -> list[str]:
    problems: list[str] = []
    for match in GITHUB_URL.finditer(text):
        path = match.group(1)
        if path.split("/", 1)[0] in ACCOUNT_PAGE_SEGMENTS:
            continue
        if not OWNER_REPO.match(path):
            problems.append(f"{name}: github.com/{path} is not <owner>/<repo>")
    for span in spans_of(text):
        if SLUG_SPAN.fullmatch(span) and not (REPO_ROOT / span).exists():
            problems.append(f"{name}: {span} is a repository slug, not <owner>/<repo>")
    return problems


def test_the_flag_model_is_read_from_the_setup_script() -> None:
    flags = parse_flags()
    assert {"--from", "--only", "--check-claude", "--template", "--no-sonar"} <= flags.keys()
    assert flags["--only"] == Flag(takes_value=True, takes_step=True, guard=None)
    assert flags["--from"] == Flag(takes_value=True, takes_step=True, guard=None)
    assert flags["--owner"] == Flag(takes_value=True, takes_step=False, guard=None)
    assert flags["--no-sonar"] == Flag(takes_value=False, takes_step=False, guard=None)
    assert flags["--check-claude"] == Flag(
        takes_value=False, takes_step=False, guard="ACCEPTS_CHECK_CLAUDE"
    )
    assert flags["--template"] == Flag(takes_value=True, takes_step=False, guard="ACCEPTS_TEMPLATE")


def test_each_entry_point_reads_its_own_restrictions_and_steps() -> None:
    bootstrap = entry_model(BOOTSTRAP_ENTRY)
    in_copy = entry_model(COPY_ENTRY)
    assert bootstrap.accepts["ACCEPTS_TEMPLATE"] == 1
    assert bootstrap.accepts["ACCEPTS_CHECK_CLAUDE"] == 0
    assert in_copy.accepts["ACCEPTS_TEMPLATE"] == 0
    assert in_copy.accepts["ACCEPTS_CHECK_CLAUDE"] == 1
    assert "copy" in bootstrap.steps
    assert "copy" not in in_copy.steps
    assert {"sonar", "verifier", "credential"} <= in_copy.steps


def test_the_flag_model_follows_whatever_script_it_is_pointed_at(tmp_path: Path) -> None:
    script = tmp_path / "copy.sh"
    script.write_text(SYNTHETIC_COPY_SCRIPT, encoding="utf-8")
    assert parse_flags(script) == {
        "--zap": Flag(takes_value=True, takes_step=True, guard=None),
        "--name": Flag(takes_value=True, takes_step=False, guard=None),
        "--plain": Flag(takes_value=False, takes_step=False, guard=None),
        "--right": Flag(takes_value=False, takes_step=False, guard="ACCEPTS_RIGHT"),
    }


def test_the_bootstrap_model_overlays_its_own_values_on_the_copy_scripts(tmp_path: Path) -> None:
    copy_script = tmp_path / "copy.sh"
    copy_script.write_text(SYNTHETIC_COPY_SCRIPT, encoding="utf-8")
    bootstrap = tmp_path / "bootstrap.sh"
    bootstrap.write_text(SYNTHETIC_BOOTSTRAP, encoding="utf-8")
    in_copy = entry_model(COPY_ENTRY, copy_script, bootstrap)
    overlaid = entry_model(BOOTSTRAP_ENTRY, copy_script, bootstrap)
    assert in_copy.steps == {"alpha", "beta"}
    assert in_copy.accepts == {"ACCEPTS_LEFT": 1, "ACCEPTS_RIGHT": 0}
    assert overlaid.steps == {"alpha", "gamma", "beta"}
    assert overlaid.accepts == {"ACCEPTS_LEFT": 1, "ACCEPTS_RIGHT": 1}


def test_the_bootstrap_model_keeps_the_copy_scripts_steps_when_it_lists_none(
    tmp_path: Path,
) -> None:
    copy_script = tmp_path / "copy.sh"
    copy_script.write_text(SYNTHETIC_COPY_SCRIPT, encoding="utf-8")
    bootstrap = tmp_path / "bootstrap.sh"
    bootstrap.write_text("ACCEPTS_RIGHT=1\n", encoding="utf-8")
    assert entry_model(BOOTSTRAP_ENTRY, copy_script, bootstrap).steps == {"alpha", "beta"}


def test_a_script_without_a_parse_args_function_is_refused(tmp_path: Path) -> None:
    script = tmp_path / "blank.sh"
    script.write_text("echo nothing\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="has no parse_args"):
        parse_flags(script)


def test_a_parse_args_function_without_flag_arms_is_refused(tmp_path: Path) -> None:
    script = tmp_path / "empty.sh"
    script.write_text("parse_args() {\n  :\n}\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="has no flag arms"):
        parse_flags(script)


def test_a_script_without_a_steps_line_is_refused(tmp_path: Path) -> None:
    script = tmp_path / "blank.sh"
    script.write_text("echo nothing\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="has no SETUP_STEPS"):
        entry_model(COPY_ENTRY, script, BOOTSTRAP)


def test_the_tutorials_hold_no_placeholder_and_only_valid_setup_commands() -> None:
    texts = tutorial_texts()
    assert texts
    assert tutorial_problems(texts) == []


def test_every_tutorial_named_for_setup_carries_a_setup_command() -> None:
    texts = tutorial_texts()
    for name in ("sonarcloud.md", "claude-sign-in.md", "verifier-bot.md", "security.md"):
        assert extract_commands(texts[name])[0], name


def test_a_tutorial_set_with_no_setup_command_fails() -> None:
    texts = {"a.md": "Run `claude` and then `/status`.\n\n```\nls -la\n```\n"}
    assert tutorial_problems(texts) == ["no tutorial contains a setup command"]
    assert tutorial_problems({}) == ["no tutorial contains a setup command"]


def test_a_tutorial_that_holds_the_placeholder_fails() -> None:
    marker = "<setup: TODO " + "P1" + "." + "10b>"
    texts = {"a.md": f"Run:\n\n`{marker}`\n\nOr `./setup.sh`.\n"}
    assert tutorial_problems(texts) == ["a.md: contains a setup placeholder"]


def test_the_placeholder_pattern_ignores_a_real_command() -> None:
    assert not PLACEHOLDER.search("Run `scripts/setup_macos.sh --only verifier` now.")


@pytest.mark.parametrize(
    "text",
    [
        "`./setup.sh`",
        "`./setup.sh --no-sonar`",
        "`./setup.sh --use-gh-token --owner <org>`",
        "`./setup.sh --from copy`",
        "`scripts/setup_macos.sh --check-claude`",
        "`scripts/setup_macos.sh --only verifier`",
        "`scripts/setup_macos.sh --from sonar --no-sonar`",
        "`scripts/setup_macos.sh --only credential --use-gh-token`",
        "`scripts/setup_macos.sh --only sonar --accept-sonar-user-token`",
        "`scripts/setup_macos.sh --uninstall`",
        "```\nscripts/setup_macos.sh --only \\\n  verifier\n```",
        "```sh\ncd copy\nscripts/setup_macos.sh --check-claude\n./setup.sh --no-sonar\n```",
        "`scripts/setup_macos.sh --only verifier | tee out --jq`",
        "`scripts/setup_macos.sh --only verifier && gh api --jq .login`",
        "`./setup.sh --no-sonar > out.log`",
        "Pass `--no-sonar` or `--jq` to skip it.",
        "Run `claude`, then `/status`, then `/login`.",
    ],
)
def test_valid_setup_commands_are_accepted(text: str) -> None:
    assert violations("case.md", text + "\n") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("`scripts/setup_macos.sh --frobnicate`", "--frobnicate is not a setup flag"),
        ("`./setup.sh --frobnicate`", "--frobnicate is not a setup flag"),
        ("`./setup.sh --check-claude`", "--check-claude is not accepted by ./setup.sh"),
        (
            "`scripts/setup_macos.sh --template <owner>/<repo>`",
            "--template is not accepted by scripts/setup_macos.sh",
        ),
        (
            "`scripts/setup_macos.sh --only nonesuch`",
            "--only names nonesuch, which is not a step of scripts/setup_macos.sh",
        ),
        (
            "`scripts/setup_macos.sh --from nonesuch`",
            "--from names nonesuch, which is not a step of scripts/setup_macos.sh",
        ),
        (
            "`./setup.sh --from nonesuch`",
            "--from names nonesuch, which is not a step of ./setup.sh",
        ),
        (
            "`scripts/setup_macos.sh --from copy`",
            "--from names copy, which is not a step of scripts/setup_macos.sh",
        ),
        ("`scripts/setup_macos.sh --only`", "--only has no value"),
        ("`scripts/setup_macos.sh --only --no-sonar`", "--only has no value"),
        ("`scripts/setup_macos.sh --owner`", "--owner has no value"),
        ("`scripts/setup_macos.sh --only sonar --jq .login`", "--jq is not a setup flag"),
        ("`./setup.sh --jq .login`", "--jq is not a setup flag"),
        ("`scripts/setup_macos.sh --only sonar verify`", "verify is not a setup flag"),
        ("`scripts/setup_macos.sh -x`", "-x is not a setup flag"),
    ],
)
def test_invalid_setup_commands_are_rejected(text: str, expected: str) -> None:
    found = violations("case.md", text + "\n")
    assert any(expected in message for message in found), found
    assert all(message.startswith("case.md: ") for message in found)


@pytest.mark.parametrize(
    "text",
    [
        "`setup.sh --no-sonar`",
        "`bash setup.sh`",
        "`/tmp/setup.sh --no-sonar`",
        "`~/alan/scripts/setup_macos.sh --only sonar`",
        "`./scripts/setup_macos.sh --only sonar`",
        "`setup_macos.sh --check-claude`",
    ],
)
def test_a_misspelt_entry_point_is_rejected(text: str) -> None:
    found = violations("case.md", text + "\n")
    assert any("is not a spelling of a setup entry point" in message for message in found), found


def test_a_command_stops_at_a_control_operator() -> None:
    commands, strays = extract_commands(
        "`scripts/setup_macos.sh --only verifier && gh api --jq .login`\n"
        "`./setup.sh --no-sonar > out.log`\n"
    )
    assert commands == [
        Command(COPY_ENTRY, ("--only", "verifier")),
        Command(BOOTSTRAP_ENTRY, ("--no-sonar",)),
    ]
    assert strays == []


def test_a_second_command_on_one_line_is_checked_on_its_own() -> None:
    found = violations(
        "case.md", "`./setup.sh --no-sonar ; scripts/setup_macos.sh --check-claude --jq`\n"
    )
    assert found == ["case.md: scripts/setup_macos.sh: --jq is not a setup flag"]


def test_placeholders_and_continuations_are_read_as_one_command() -> None:
    commands, _ = extract_commands("```\n./setup.sh --owner <org> \\\n  --no-sonar\n```\n")
    assert commands == [Command(BOOTSTRAP_ENTRY, ("--owner", "value", "--no-sonar"))]


def test_a_fenced_block_is_read_line_by_line() -> None:
    commands, _ = extract_commands(
        "```sh\ncd copy\nscripts/setup_macos.sh --check-claude\n./setup.sh --no-sonar\n```\n"
    )
    assert [command.entry for command in commands] == [COPY_ENTRY, BOOTSTRAP_ENTRY]


def test_the_security_tutorial_lists_exactly_the_permissions_setup_prints() -> None:
    printed = printed_permissions()
    documented = documented_permissions(SECURITY_TUTORIAL.read_text(encoding="utf-8"))
    assert printed
    assert permission_differences(printed, documented) == ([], [])
    assert documented == printed


def test_a_permission_setup_prints_but_the_tutorial_omits_is_reported_missing() -> None:
    printed = printed_permissions()
    missing, extra = permission_differences(printed, printed[1:])
    assert missing == [printed[0]]
    assert extra == []


def test_a_permission_the_tutorial_adds_beyond_setup_is_reported_extra() -> None:
    printed = printed_permissions()
    missing, extra = permission_differences(printed, [*printed, "Secrets: Read and write"])
    assert missing == []
    assert extra == ["Secrets: Read and write"]


def test_the_printed_permissions_are_the_indented_lines_and_not_the_access_line() -> None:
    printed = printed_permissions()
    assert "Administration: Read and write" in printed
    assert not any(line.startswith("Repository access") for line in printed)
    assert len(printed) == 9


def test_the_permission_block_must_sit_in_the_transport_section() -> None:
    with pytest.raises(AssertionError, match="no fenced block"):
        documented_permissions("# Security\n\n## Other\n\n```\nContents: Read-only\n```\n")


def test_the_permission_block_stops_at_the_next_section() -> None:
    text = (
        f"{PERMISSIONS_SECTION}\n\n```\nContents: Read-only\n```\n\n"
        "## Next\n\n```\nActions: Read-only\n```\n"
    )
    assert documented_permissions(text) == ["Contents: Read-only"]


def test_the_security_tutorial_says_what_administration_write_allows() -> None:
    text = SECURITY_TUTORIAL.read_text(encoding="utf-8")
    assert "branch protection" in text
    assert "change repository settings" in text
    assert "scoped to `<owner>/<repo>` only" in text


def test_every_repository_reference_in_the_tutorials_is_owner_slash_repo() -> None:
    problems: list[str] = []
    for name, text in tutorial_texts().items():
        problems.extend(repository_problems(name, text))
    assert not problems


@pytest.mark.parametrize(
    "text",
    [
        "See https://github.com/<owner>/<repo>/invitations.",
        "See [tokens](https://github.com/settings/tokens).",
        "Open github.com/settings/tokens now.",
        "Use `<owner>/<repo>` here.",
        "The folder `docs/tutorials` exists.",
        "See [docs](https://docs.github.com/en/authentication).",
    ],
)
def test_allowed_repository_references_pass(text: str) -> None:
    assert repository_problems("case.md", text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("See https://github.com/acme/widgets/issues.", "github.com/acme/widgets/issues"),
        ("See github.com/acme/widgets.", "github.com/acme/widgets."),
        ("See https://github.com/acme.", "github.com/acme."),
        ("See https://github.com/<owner>/other.", "github.com/<owner>/other"),
        ("Clone `acme/widgets` first.", "acme/widgets is a repository slug"),
    ],
)
def test_other_repository_references_are_rejected(text: str, expected: str) -> None:
    found = repository_problems("case.md", text)
    assert any(expected in message for message in found), found


def test_no_tutorial_recommends_the_template_flag() -> None:
    for name, text in tutorial_texts().items():
        assert "--template" not in text, name


def test_the_tutorials_carry_the_commands_the_brief_names() -> None:
    text = tutorial_texts()
    assert text["claude-sign-in.md"].count("`scripts/setup_macos.sh --check-claude`") == 2
    assert "`./setup.sh`" in text["verifier-bot.md"]
    assert "`scripts/setup_macos.sh --only verifier`" in text["verifier-bot.md"]
    assert "`./setup.sh --use-gh-token`" in text["security.md"]
    assert "`scripts/setup_macos.sh --only credential --use-gh-token`" in text["security.md"]
    assert "(sonarcloud.md)" in text["security.md"]
    assert "`./setup.sh --no-sonar`" in text["sonarcloud.md"]
    assert "`scripts/setup_macos.sh --only sonar`" in text["sonarcloud.md"]
    assert "--accept-sonar-user-token" in text["sonarcloud.md"]


def test_the_check_claude_flag_is_never_shown_with_the_bootstrap_entry() -> None:
    for text in tutorial_texts().values():
        for command in extract_commands(text)[0]:
            assert not (command.entry == BOOTSTRAP_ENTRY and "--check-claude" in command.tokens)


def test_the_sonar_tutorial_states_no_plan_limit_and_says_it_is_optional() -> None:
    text = SONAR_TUTORIAL.read_text(encoding="utf-8").lower()
    assert "lines of code" not in text
    assert "private projects" not in text
    assert "optional" in text
    assert "press enter" in text


def _dedicated_user_section() -> str:
    text = SECURITY_TUTORIAL.read_text(encoding="utf-8")
    assert text.count("\n## Use a dedicated macOS user\n") == 1
    return text.split("\n## Use a dedicated macOS user\n", 1)[1].split("\n## ", 1)[0]


def test_the_security_tutorial_shows_setups_exact_formula_list() -> None:
    assert f"`brew install {' '.join(brew_formulae())}`" in _dedicated_user_section()


def test_the_security_tutorial_requires_a_separate_account_and_a_path_step() -> None:
    section = _dedicated_user_section()
    assert "a separate account, not your main one" in section
    assert "the account that owns Homebrew" in section
    assert "PATH" in section
    assert "`brew shellenv`" in section


def test_the_security_tutorial_drops_the_old_sonarcloud_claim() -> None:
    text = SECURITY_TUTORIAL.read_text(encoding="utf-8")
    assert "reports on every pull request" not in text
    assert "never blocks a merge" not in text
