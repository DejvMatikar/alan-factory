from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from tests._setup_stubs import CLAUDE_INSTALLER_URL, HOMEBREW_INSTALLER_URL
from tests._setup_support import (
    BOOTSTRAP_SCRIPT,
    LEAK_PATTERNS,
    SETUP_SCRIPT,
    leaks,
    script_constant,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = {"setup.sh": BOOTSTRAP_SCRIPT, "scripts/setup_macos.sh": SETUP_SCRIPT}
SETUP_DOC = REPO_ROOT / "docs" / "architecture" / "setup.md"
DEPLOY_DOC = REPO_ROOT / "docs" / "architecture" / "deploy.md"
ARCHITECTURE_INDEX = REPO_ROOT / "docs" / "architecture" / "README.md"

BASH4_ONLY: dict[str, re.Pattern[str]] = {
    "associative array": re.compile(r"\b(?:declare|local|typeset)\s+-[A-Za-z]*A"),
    "nameref": re.compile(r"\b(?:declare|local|typeset)\s+-[A-Za-z]*n\b"),
    "mapfile": re.compile(r"\bmapfile\b"),
    "readarray": re.compile(r"\breadarray\b"),
    "case modification": re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?:,,?|\^\^?)\}"),
    "transformation": re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*@[UuLlQEPAaK]\}"),
    "append both streams": re.compile(r"&>>"),
    "pipe both streams": re.compile(r"\|&"),
    "coproc": re.compile(r"\bcoproc\b"),
    "variable test": re.compile(r"\[\[\s+-v\s"),
    "case fallthrough": re.compile(r";;?&"),
    "wait -n": re.compile(r"\bwait\s+-n\b"),
    "globstar": re.compile(r"\bglobstar\b"),
    "lastpipe": re.compile(r"\blastpipe\b"),
}
BASH4_SAMPLES: dict[str, str] = {
    "associative array": "declare -A table",
    "nameref": "local -n ref=name",
    "mapfile": "mapfile -t lines < file",
    "readarray": "readarray -t lines",
    "case modification": 'echo "${name,,}"',
    "transformation": 'echo "${name@U}"',
    "append both streams": "cmd &>> log",
    "pipe both streams": "cmd |& tee log",
    "coproc": "coproc worker { cmd; }",
    "variable test": "[[ -v name ]]",
    "case fallthrough": "case x in a) cmd ;& b) cmd ;; esac",
    "wait -n": "wait -n",
    "globstar": "shopt -s globstar",
    "lastpipe": "shopt -s lastpipe",
}
TRACING: dict[str, re.Pattern[str]] = {
    "set -x": re.compile(r"\bset\s+-[A-Za-z]*x"),
    "set -o xtrace": re.compile(r"\bset\s+-o\s+xtrace"),
    "xtrace": re.compile(r"xtrace", re.IGNORECASE),
    "BASH_XTRACEFD": re.compile(r"BASH_XTRACEFD"),
    "shebang -x": re.compile(r"^#!.*\s-[A-Za-z]*x", re.MULTILINE),
}
BIND_STEPS = ["credential", "harden", "protect", "confirm"]
FULL_STEPS = [
    "preflight",
    "install",
    "build",
    "profile",
    "start",
    *BIND_STEPS,
    "sonar",
    "verifier",
    "trigger",
    "claude",
    "updater",
    "latch",
    "report",
]
BSD_ONLY_OPTIONS: dict[str, re.Pattern[str]] = {
    "sed in place": re.compile(r"\bsed\s+(?:-[A-Za-z]+\s+)*-[A-Za-z]*i"),
    "stat format": re.compile(r"\bstat\s+-[A-Za-z]*f"),
    "date offset": re.compile(r"\bdate\s+-[A-Za-z]*[jv]"),
    "mktemp prefix": re.compile(r"\bmktemp\s+(?:-[A-Za-z]+\s+)*-t\b"),
    "ls flags": re.compile(r"\bls\s+-[A-Za-z]*[@Oe]"),
    "xargs replace": re.compile(r"\bxargs\s+-[A-Za-z]*J"),
    "clipboard": re.compile(r"\b(?:pbcopy|pbpaste)\b"),
    "open": re.compile(r"(?:^|[\s;|&(])open\s"),
    "md5": re.compile(r"\bmd5\s"),
}
BSD_ONLY_SAMPLES: dict[str, str] = {
    "sed in place": "sed -i '' 's/a/b/' file",
    "stat format": "stat -f %Lp file",
    "date offset": "date -v+1d",
    "mktemp prefix": "mktemp -t setup",
    "ls flags": "ls -@ file",
    "xargs replace": "xargs -J % cmd %",
    "clipboard": "pbcopy < file",
    "open": "open https://example.invalid",
    "md5": "md5 file",
}
BIND_FUNCTION = re.compile(
    r"^(?P<name>step_(?:credential|harden|protect|confirm)|bind_\w+|run_transport_body"
    r"|record_failure|report_failures|step_failed|state_has|binding_complete)\(\) \{\n"
    r"(?P<body>.*?)^\}$",
    re.MULTILINE | re.DOTALL,
)
FORBIDDEN_CALLS: dict[str, re.Pattern[str]] = {
    "claude sign-in": re.compile(r"\bclaude\s+(?:login|auth|config|mcp|setup-token)\b"),
    "setup-token": re.compile(r"setup-token"),
    "security tool": re.compile(r"(?:^|[\s;|&(])security\s", re.MULTILINE),
    "forced link": re.compile(r"\bbrew\s+link\b"),
    "power settings": re.compile(r"\bpmset\b"),
    "hard-coded homebrew prefix": re.compile(r"/opt/homebrew"),
}


def _code(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _hits(patterns: dict[str, re.Pattern[str]], text: str) -> list[str]:
    return [name for name, pattern in patterns.items() if pattern.search(text)]


def _steps(path: Path) -> list[str]:
    match = re.search(r'^SETUP_STEPS="([^"]*)"$', path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None
    return match.group(1).split()


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_setup_script_starts_with_the_bash_shebang(name: str) -> None:
    first = SCRIPTS[name].read_text(encoding="utf-8").splitlines()[0]
    assert first == "#!/bin/bash"


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_setup_script_is_executable(name: str) -> None:
    assert os.access(SCRIPTS[name], os.X_OK)


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_setup_script_parses_under_the_system_bash(name: str) -> None:
    done = subprocess.run(
        ["/bin/bash", "-n", str(SCRIPTS[name])], capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_setup_script_stops_on_errors_and_undefined_variables(name: str) -> None:
    assert re.search(r"^set -euo pipefail$", SCRIPTS[name].read_text(encoding="utf-8"), re.M)


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_setup_script_uses_bash_4_only_syntax(name: str) -> None:
    assert _hits(BASH4_ONLY, _code(SCRIPTS[name])) == []


@pytest.mark.parametrize("construct", BASH4_SAMPLES)
def test_the_bash_4_scan_flags_each_banned_construct(construct: str) -> None:
    assert construct in _hits(BASH4_ONLY, BASH4_SAMPLES[construct])


def test_the_bash_4_scan_passes_the_constructs_bash_3_2_has() -> None:
    sample = 'x="${y#*/}"; z="${y%.git}"; [[ $a =~ $re ]]; printf "%s" "$x"; case $x in a) ;; esac'
    assert _hits(BASH4_ONLY, sample) == []


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_setup_script_traces_its_own_execution(name: str) -> None:
    assert _hits(TRACING, SCRIPTS[name].read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "sample",
    ["set -x", "set -euxo pipefail", "set -o xtrace", "export BASH_XTRACEFD=9", "#!/bin/bash -x"],
)
def test_the_tracing_scan_flags_each_way_to_trace(sample: str) -> None:
    assert _hits(TRACING, sample) != []


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_setup_script_signs_in_to_claude_calls_security_or_forces_links(name: str) -> None:
    assert _hits(FORBIDDEN_CALLS, _code(SCRIPTS[name])) == []


@pytest.mark.parametrize(
    "sample",
    [
        "claude login",
        "claude setup-token",
        "security find-generic-password",
        "brew link --force node",
        "pmset -a sleep 0",
        "PATH=/opt/homebrew/bin:$PATH",
    ],
)
def test_the_forbidden_call_scan_flags_each_forbidden_call(sample: str) -> None:
    assert _hits(FORBIDDEN_CALLS, sample) != []


def test_the_only_eval_is_the_homebrew_shell_environment() -> None:
    engine = re.findall(r"^.*\beval\b.*$", _code(SETUP_SCRIPT), re.MULTILINE)
    assert [line.strip() for line in engine] == ['eval "$(brew shellenv)"']
    assert re.search(r"\beval\b", _code(BOOTSTRAP_SCRIPT)) is None


def test_the_control_token_and_secrets_file_are_never_read() -> None:
    code = _code(SETUP_SCRIPT)
    files = r"\$(?:CONTROL_TOKEN_FILE|SECRETS_FILE)"
    readers = r"\b(?:cat|source|read|head|tail|grep|sed|awk)\b"
    assert re.search(rf"<\s*\"?{files}", code) is None
    assert re.search(rf"{readers}[^\n]*{files}", code) is None
    assert re.search(r"\bsource\s|^\.\s", code, re.MULTILINE) is None


def test_no_credential_name_is_handled_by_either_script() -> None:
    names = re.compile(r"(?:GH_TOKEN|SONAR_TOKEN|ANTHROPIC|API_KEY|PASSWORD|VERIFIER_GH)", re.I)
    for path in SCRIPTS.values():
        assert names.search(_code(path)) is None


def test_neither_script_names_the_gh_token_variable_even_in_a_comment() -> None:
    for path in SCRIPTS.values():
        assert "GH_TOKEN" not in path.read_text(encoding="utf-8")


def _private_file_lines(path: Path) -> list[str]:
    mentions = re.compile(r"control\.header|transport\.body|BIND_HEADER_FILE|\$body\b")
    return [line for line in _code(path).splitlines() if mentions.search(line)]


def test_the_shell_only_hands_the_private_files_to_curl_and_python_and_never_reads_them() -> None:
    readers = re.compile(r"\b(?:cat|source|read|head|tail|grep|sed|awk|cut|tr|od|xxd|base64)\b")
    lines = _private_file_lines(SETUP_SCRIPT)
    assert len(lines) >= 5
    assert [line for line in lines if readers.search(line)] == []


def test_the_private_files_are_written_by_python_and_the_body_reaches_curl_on_stdin() -> None:
    code = _code(SETUP_SCRIPT)
    assert "alan.setup_transport control-header --out" in code
    assert "alan.setup_transport transport-body" in code
    assert '--data-binary @- < "$body"' in code
    assert re.search(r"--data(?:-raw|-urlencode|-ascii)?[ =][^@]", code) is None


def test_the_private_directory_is_removed_by_the_one_exit_trap_main_installs_first() -> None:
    code = _code(SETUP_SCRIPT)
    assert re.findall(r"^[ \t]*trap\b.*$", code, re.MULTILINE) == ["  trap on_exit EXIT"]
    main_body = code[code.index("\nmain() {") :]
    assert main_body.index("trap on_exit EXIT") < main_body.index("parse_args")
    on_exit = re.search(r"^on_exit\(\) \{\n(?P<body>.*?)^\}$", code, re.MULTILINE | re.DOTALL)
    assert on_exit is not None
    assert "bind_cleanup" in on_exit.group("body")


def test_the_header_states_the_four_credentials_the_script_handles() -> None:
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    comment = " ".join(
        line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")
    )
    assert "never handles a credential" not in comment
    assert "Handles exactly one credential" not in comment
    assert "Handles exactly two credentials" not in comment
    assert (
        "Handles exactly four credentials, the GitHub transport token, the verifier bot's "
        "token and the two SonarCloud tokens (an admin token and an analysis token), and only "
        "through a mode-600 file or stdin" in comment
    )
    assert "--use-gh-token" in comment
    assert "--accept-sonar-user-token" in comment
    assert "--check-claude" in comment
    assert "it does not sign in to Claude" in comment


def test_the_shell_never_reads_the_reviewer_files_or_the_login_and_reason_files() -> None:
    readers = re.compile(r"\b(?:cat|source|read|tail|grep|sed|awk|cut|tr|od|xxd|base64)\b")
    mentions = re.compile(r"reviewer\.body|reviewer\.login|verifier_helper login")
    lines = [line for line in _code(SETUP_SCRIPT).splitlines() if mentions.search(line)]
    assert len(lines) >= 4
    assert [line for line in lines if readers.search(line)] == []


def test_the_verifier_token_reaches_python_on_standard_input_and_curl_on_its_stdin() -> None:
    code = _code(SETUP_SCRIPT)
    assert "alan.setup_verifier" in code
    assert 'bind_post_secret "$BIND_DIR/reviewer.body"' in code
    assert re.search(r"--(?:token|value|password)\b", code) is None
    assert re.search(r"echo[^\n]*\$\{?(?:body|token)", code) is None


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_setup_script_carries_an_identity_or_credential_pattern(name: str) -> None:
    assert leaks(SCRIPTS[name].read_text(encoding="utf-8")) == {}


def test_the_setup_doc_carries_no_identity_or_credential_pattern() -> None:
    assert leaks(SETUP_DOC.read_text(encoding="utf-8")) == {}


def test_the_leak_patterns_cover_four_kinds() -> None:
    kinds = ["machine-name", "personal-email", "tailnet-host", "token-prefix"]
    assert sorted(LEAK_PATTERNS) == kinds


def _brew_formulae() -> list[str]:
    match = re.search(
        r'^BREW_FORMULAE="([^"]*)"$', SETUP_SCRIPT.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match is not None
    return match.group(1).split()


def test_the_setup_script_installs_tmux_beside_the_other_formulae() -> None:
    formulae = _brew_formulae()
    assert formulae.count("tmux") == 1
    assert {"git", "gh", "uv", "node@22", "gitleaks", "pre-commit"} <= set(formulae)
    assert len(formulae) == len(set(formulae))


def _homebrew_check_bodies() -> dict[str, str]:
    code = _code(SETUP_SCRIPT)
    bodies: dict[str, str] = {}
    for name in ("refuse_unwritable_homebrew", "missing_formulae", "formula_present"):
        found = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}$", code, re.MULTILINE | re.DOTALL)
        assert found is not None, name
        bodies[name] = found.group(1)
    return bodies


def test_the_homebrew_check_never_looks_up_an_account_or_changes_the_prefix() -> None:
    forbidden = re.compile(
        r"\bstat\b|\bwhoami\b|\bid\s+-|\bls\s+-l|\bdscl\b|\bchown\b|\bchmod\b|\bsudo\b|\bsu\b"
        r"|\bshellenv\b"
    )
    for name, body in _homebrew_check_bodies().items():
        assert forbidden.search(body) is None, name
        assert re.search(r"\bBREW_PREFIX=", body) is None, name


def test_the_setup_doc_describes_the_unwritable_homebrew_refusal_and_the_path_limitation() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    assert "**Unwritable Homebrew prefix.**" in doc
    limitations = doc.split("\n## Known limitations\n", 1)[1].split("\n## ", 1)[0]
    assert "another account's Homebrew that is not on the current user's PATH" in limitations


def test_the_setup_doc_lists_tmux_as_the_operator_terminal_prerequisite() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    assert "`git gh uv node@22 gitleaks pre-commit tmux`" in doc
    assert "operator terminal's system prerequisite" in doc
    assert "operator_terminal.md" in doc


def _deploy_operator_section() -> str:
    doc = DEPLOY_DOC.read_text(encoding="utf-8")
    assert doc.count("\n## Operator terminal\n") == 1
    return doc.split("\n## Operator terminal\n", 1)[1].split("\n## ", 1)[0]


def test_the_deploy_runbook_has_an_operator_terminal_section_linking_the_design_doc() -> None:
    section = _deploy_operator_section()
    assert "](operator_terminal.md)" in section
    assert (REPO_ROOT / "docs" / "architecture" / "operator_terminal.md").is_file()
    for topic in ("`tmux`", "first time", "--ff-only", "restart", "memory"):
        assert topic in section


def test_the_deploy_runbook_keeps_memory_sharing_optional_and_manual() -> None:
    section = _deploy_operator_section()
    assert "optional, manual, never automated" in section
    assert "Neither alan nor setup does this" in section


def test_the_deploy_operator_section_names_no_owner_path_account_or_machine() -> None:
    section = _deploy_operator_section()
    assert leaks(section) == {}
    assert re.search(r"(?<![A-Za-z0-9_])/(?:Users|home)/\w", section) is None
    assert re.search(r"github\.com|\.local\b", section) is None


def test_the_installer_urls_the_stubs_answer_are_the_ones_the_script_fetches() -> None:
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert f'HOMEBREW_INSTALLER_URL="{HOMEBREW_INSTALLER_URL}"' in text
    assert f'CLAUDE_INSTALLER_URL="{CLAUDE_INSTALLER_URL}"' in text


def test_every_step_the_engine_lists_has_a_step_function() -> None:
    engine = SETUP_SCRIPT.read_text(encoding="utf-8")
    for step in _steps(SETUP_SCRIPT):
        assert re.search(rf"^step_{step}\(\) \{{$", engine, re.MULTILINE)


def test_the_engine_lists_the_full_setup_steps_in_order() -> None:
    assert _steps(SETUP_SCRIPT) == FULL_STEPS


def test_the_sonar_step_sits_directly_after_confirm_and_before_the_verifier() -> None:
    steps = _steps(SETUP_SCRIPT)
    assert steps.index("confirm") + 1 == steps.index("sonar")
    assert steps.index("sonar") + 1 == steps.index("verifier")


def test_the_ci_claude_updater_latch_and_report_steps_close_the_list_in_that_order() -> None:
    steps = _steps(SETUP_SCRIPT)
    assert steps[steps.index("verifier") :] == [
        "verifier",
        "trigger",
        "claude",
        "updater",
        "latch",
        "report",
    ]
    assert steps[-1] == "report"


def test_the_bootstrap_lists_the_same_steps_with_copy_directly_after_install() -> None:
    assert _steps(BOOTSTRAP_SCRIPT) == [*FULL_STEPS[:2], "copy", *FULL_STEPS[2:]]


def test_the_binding_functions_use_no_option_that_only_bsd_tools_have() -> None:
    bodies = {match["name"]: match["body"] for match in BIND_FUNCTION.finditer(_code(SETUP_SCRIPT))}
    assert set(bodies) >= {f"step_{step}" for step in BIND_STEPS}
    assert {"bind_api", "bind_verdict", "bind_apply_setting", "bind_secret_dir"} <= set(bodies)
    assert {name: _hits(BSD_ONLY_OPTIONS, body) for name, body in bodies.items()} == {
        name: [] for name in bodies
    }


@pytest.mark.parametrize("construct", BSD_ONLY_SAMPLES)
def test_the_bsd_only_option_scan_flags_each_banned_construct(construct: str) -> None:
    assert construct in _hits(BSD_ONLY_OPTIONS, BSD_ONLY_SAMPLES[construct])


def test_the_bsd_only_option_scan_passes_the_portable_forms_the_steps_use() -> None:
    sample = "mktemp -d \"$T/x.XXXXXX\"; sed -n '2,$p' f; head -n 1 f; awk -v s=a 'x' f; rm -rf d"
    assert _hits(BSD_ONLY_OPTIONS, sample) == []


def test_the_bootstrap_entry_inserts_only_the_copy_step_after_install() -> None:
    engine = _steps(SETUP_SCRIPT)
    bootstrap = _steps(BOOTSTRAP_SCRIPT)
    assert bootstrap == [*engine[: engine.index("install") + 1], "copy", *engine[2:]]
    assert re.search(r"^step_copy\(\) \{$", BOOTSTRAP_SCRIPT.read_text(encoding="utf-8"), re.M)


def test_the_bootstrap_entry_sources_the_engine_and_hands_control_to_main() -> None:
    text = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")
    assert '. "$BOOTSTRAP_DIR/scripts/setup_macos.sh"' in text
    assert text.rstrip().endswith('main "$@"')
    assert 'if [ "${BASH_SOURCE[0]}" = "$0" ]; then' in SETUP_SCRIPT.read_text(encoding="utf-8")


def test_the_step_engine_does_not_run_when_sourced_by_the_bootstrap() -> None:
    done = subprocess.run(
        ["/bin/bash", "-c", f'HOME=/nonexistent-home; . "{SETUP_SCRIPT}"; echo sourced'],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent-home"},
    )
    assert done.stdout.strip() == "sourced"


def test_the_profile_writer_sits_directly_above_the_default_profile_in_the_import_layers() -> None:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    layers = next(
        contract["layers"]
        for contract in data["tool"]["importlinter"]["contracts"]
        if contract.get("type") == "layers"
    )
    assert layers.index("default_profile_writer") + 1 == layers.index("default_profile")


def _layers() -> list[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contracts = data["tool"]["importlinter"]["contracts"]
    return next(c["layers"] for c in contracts if c.get("type") == "layers")


def test_the_setup_modules_sit_between_drift_gate_and_github_repos_in_the_import_layers() -> None:
    layers = _layers()
    assert layers.index("drift_gate") < layers.index("setup_verifier")
    assert layers.index("setup_verifier") < layers.index("setup_transport")
    assert layers.index("setup_transport") < layers.index("setup_verdicts")
    assert layers.index("setup_verdicts") < layers.index("github_repos")


def test_the_verifier_sonar_and_claude_check_modules_sit_between_drift_gate_and_transport() -> None:
    layers = _layers()
    assert layers.index("drift_gate") + 1 == layers.index("setup_verifier")
    assert layers.index("setup_verifier") + 1 == layers.index("setup_sonar")
    assert layers.index("setup_sonar") + 1 == layers.index("setup_claude_check")
    assert layers.index("setup_claude_check") + 1 == layers.index("setup_transport")


def test_the_latch_module_sits_directly_above_engine_preflight_in_the_import_layers() -> None:
    layers = _layers()
    assert layers.index("setup_latch") + 1 == layers.index("engine_preflight")


def test_the_architecture_index_lists_the_setup_doc_on_one_line() -> None:
    index = ARCHITECTURE_INDEX.read_text(encoding="utf-8").splitlines()
    lines = [line for line in index if line.startswith("- [setup.md](setup.md)")]
    assert len(lines) == 1
    assert "closing line" in lines[0]
    assert "transport credential" in lines[0]
    assert "repo hardening" in lines[0]


def test_the_setup_doc_states_the_invariants_and_the_recorded_values() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    for heading in (
        "### The existing-install guard",
        "### The profile is written before the first boot",
        "### Setup never signs in to Claude",
        "### The closing line (PD5)",
        "## Known limitations",
        "## Accepted risk",
        "## Precedence",
    ):
        assert heading in doc
    assert f"`MIN_RAM_GB={script_constant('MIN_RAM_GB')}`" in doc
    assert f"`MIN_DISK_GB={script_constant('MIN_DISK_GB')}`" in doc
    assert "never says it is complete" in doc
    assert "not ready to dispatch" in doc
    assert "~/.alan/queue.db" in doc
    assert "fails closed" in doc
    assert "Exit 3" in doc


def test_the_setup_doc_records_the_binding_steps_in_order() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    assert "preflight install build profile start credential harden protect confirm" in doc
    for step in BIND_STEPS:
        assert f"`{step}`" in doc
    assert "## The transport credential" in doc
    assert "### Visibility-dependent hardening" in doc
    assert "### Protect and confirm" in doc
    assert "### Failures that do not stop the run" in doc


def test_the_setup_doc_states_the_verifier_and_updater_steps_in_order() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    order = " ".join(FULL_STEPS)
    assert f"`{order}`" in doc
    for step in ("sonar", "verifier", "updater"):
        assert f"`{step}`" in doc
    for heading in (
        "### Setup handles four credentials",
        "## SonarCloud",
        "## The verifier bot",
        "## The restart marker",
        "## The updater agent",
    ):
        assert heading in doc


def test_the_setup_doc_states_the_verifier_rules_the_marker_and_the_updater() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    for phrase in (
        "read_fd0_line",
        "one `ALAN_VERIFIER_LOGIN=` line",
        "Bash never reads the file",
        "setup.restart_pending",
        "launchctl kickstart -k ai.alan",
        "served_sha == HEAD",
        "render_update_plist.sh",
        "`Resume with: scripts/setup_macos.sh --from verifier`",
        "`Resume with: scripts/setup_macos.sh --from updater`",
        "no expiry reminder will fire",
        "`push`, `maintain` or `admin`",
        "user/repository_invitations",
    ):
        assert phrase in doc


def test_the_setup_doc_states_the_sonar_step_its_flag_and_its_rules() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    for phrase in (
        "`--accept-sonar-user-token`",
        "byte-identical to the admin token is always refused",
        "`write_code_quality`",
        "`before_write`",
        "sonar_reload",
        "gh secret set",
        "`Resume with: scripts/setup_macos.sh --from sonar`",
        "The user never types the organization or the project key",
        "P3.7 rehearsal",
        "`SONAR_ORGANIZATION`",
        "`SONAR_PROJECT_KEY`",
    ):
        assert phrase in doc
    assert "handles exactly these four" in doc
    assert "alan never handles a Claude credential" in doc


def test_the_setup_doc_no_longer_says_one_or_two_credentials_or_that_no_updater_exists() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    assert "### Setup handles two credentials" not in doc
    assert "Setup handles exactly these two" not in doc
    assert "### Setup handles exactly one credential" not in doc
    assert "handles exactly one credential" not in doc
    assert "no updater agent exists to run it" not in doc
    assert "its contents are never read or printed" not in doc


def test_the_setup_doc_quotes_the_permissions_the_token_needs_and_why() -> None:
    from alan.setup_transport import PERMISSIONS_TEXT

    doc = SETUP_DOC.read_text(encoding="utf-8")
    permissions = [
        line.strip() for line in PERMISSIONS_TEXT.splitlines() if re.match(r"\s{4}\w.*: ", line)
    ]
    assert "Administration: Read and write" in permissions
    for line in permissions:
        assert line in doc
    assert "Why Administration write" in doc or "why Administration write" in doc
    assert "--use-gh-token" in doc


def test_the_setup_doc_names_a_pr_policy_that_never_reads_back_as_an_expected_failure() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    assert "permanent failure of this one row" in doc
    assert "--from confirm" in doc
    assert "re-attempts the write" in doc


def test_the_setup_doc_says_where_the_credential_lives_and_how_slot_clones_use_it() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    assert "POST /api/secrets" in doc
    assert "<owner>/<repo>:transport" in doc
    assert "`slot_pool`" in doc
    assert "no restart" in doc
    assert "curl 7.55" in doc
    assert "mode-600" in doc


def test_the_setup_doc_rewords_the_control_token_rule_and_drops_the_old_limitation() -> None:
    doc = SETUP_DOC.read_text(encoding="utf-8")
    assert "The control token is never read" not in doc
    assert "Bash never reads the control token" in doc
    assert "alan.setup_transport" in doc
    assert "Slot-clone git credentials" not in doc
    assert "unresolved until P1.9b" not in doc


def test_both_scripts_accept_and_forward_the_sonar_flag_without_a_value() -> None:
    engine = _code(SETUP_SCRIPT)
    bootstrap = _code(BOOTSTRAP_SCRIPT)
    assert "--accept-sonar-user-token) ACCEPT_SONAR_USER=1; shift ;;" in engine
    assert 'sonar_flag="--accept-sonar-user-token"' in bootstrap
    assert "$session_flag $sonar_flag" in bootstrap


def test_the_shell_passes_the_sonar_helper_only_the_engine_the_marker_path_and_the_flag() -> None:
    lines = [line.strip() for line in _code(SETUP_SCRIPT).splitlines()]
    calls = [line for line in lines if line.startswith("if sonar_helper ")]
    assert calls == [
        'if sonar_helper configure --engine "$BIND_ENGINE" --restart-marker "$RESTART_MARKER" '
        '$accept 2> "$BIND_DIR/sonar.reason"; then',
        'if sonar_helper configured --engine "$BIND_ENGINE"; then',
    ]


def test_the_sonar_helper_runs_with_the_engines_root_pinned_like_the_profile_writer() -> None:
    code = _code(SETUP_SCRIPT)
    pinned = 'ALAN_ENGINES_ROOT="$ENGINES_DIR" "$REPO_DIR/.venv/bin/python" -m alan.setup_sonar'
    assert pinned in code


def test_the_sonar_restart_is_deferred_while_the_verifier_is_still_selected() -> None:
    code = _code(SETUP_SCRIPT)
    reload = re.search(r"^sonar_reload\(\) \{\n(?P<body>.*?)^\}$", code, re.MULTILINE | re.DOTALL)
    assert reload is not None
    assert "step_selected verifier" in reload.group("body")
    assert "reload_service" in reload.group("body")
