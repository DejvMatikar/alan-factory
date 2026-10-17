from __future__ import annotations

import atexit
import functools
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from alan import default_profile, default_profile_writer
from tests._setup_stubs import (
    CLAUDE_INSTALLER_URL,
    GIB,
    HOMEBREW_INSTALLER_URL,
    REPO_ROOT,
    ROOT_ENV,
    SHIMS_ENV,
    install_stubs,
    venv_python_text,
    write_shims,
)

SYSTEM_PATH = "/usr/bin:/bin"
WAIT_BUDGET = 3
WAIT_BUDGET_ENVS = (
    "SETUP_HEALTH_ATTEMPTS",
    "SETUP_TOKEN_ATTEMPTS",
    "SETUP_UNLOAD_ATTEMPTS",
    "SETUP_CLAUDE_ATTEMPTS",
    "SETUP_RUN_ATTEMPTS",
    "SETUP_LATCH_ATTEMPTS",
)
PORTABLE_TOOLS = (
    "awk",
    "basename",
    "chmod",
    "cp",
    "cut",
    "dirname",
    "git",
    "grep",
    "head",
    "id",
    "ls",
    "mkdir",
    "mktemp",
    "mv",
    "rm",
    "sed",
    "tr",
)
COPIED_FILES = (
    "setup.sh",
    "scripts/setup_macos.sh",
    "deploy/render_plist.sh",
    "deploy/ai.alan.plist",
    "deploy/run_alan.sh",
    "deploy/render_update_plist.sh",
    "deploy/ai.alan.update.plist",
    "deploy/update_runner.sh",
)
SCRIPT_ENTRIES = ("setup.sh", "scripts/setup_macos.sh")
STEPS_LINE = re.compile(r'^SETUP_STEPS="([^"]*)"$', re.MULTILINE)
FINISH_ONLY_STEPS = ("verifier", "updater")
CI_ONLY_STEPS = ("trigger", "claude", "latch", "report")
SONAR_STEP = "sonar"
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup_macos.sh"
BOOTSTRAP_SCRIPT = REPO_ROOT / "setup.sh"

OWNER = "example-owner"
REPO = "example-repo"
EXAMPLE_ORIGIN = f"https://github.com/{OWNER}/{REPO}.git"
TOKEN_SENTINEL = "SENTINEL-CONTROL-TOKEN-0d1f7c"  # noqa: S105
TRANSPORT_SENTINEL = "SENTINEL-TRANSPORT-TOKEN-3c8f21"  # noqa: S105
AMBIENT_SENTINEL = "SENTINEL-GH-SESSION-TOKEN-5b2e9a"  # noqa: S105
BOT_SENTINEL = "SENTINEL-VERIFIER-TOKEN-7e4a90"  # noqa: S105
SONAR_ADMIN_SENTINEL = "SENTINEL-SONAR-ADMIN-4d2b71"  # noqa: S105
SONAR_ANALYSIS_SENTINEL = "SENTINEL-SONAR-ANALYSIS-9a6c30"  # noqa: S105
SONAR_ADMIN_LOGIN = "example-sonar-admin"
SONAR_ANALYSIS_LOGIN = "example-sonar-analysis"
SONAR_ORGANIZATION = "example-org"
SONAR_PROJECT_KEY = "example-org_example-repo"
BOT_LOGIN = "example-verifier"
BOT_EXPIRY = "2099-06-30 18:30:00 UTC"
ENGINE_SLUG = f"{OWNER}/{REPO}"
HEAD_PLACEHOLDER = "@head"
SERVICE_LABEL = "ai.alan"
UPDATE_LABEL = "ai.alan.update"
CLAUDE_LABEL = "ai.alan.claude-check"

MUTATING: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gh", ("repo", "create")),
    ("gh", ("repo", "clone")),
    ("gh", ("auth", "login")),
    ("brew", ("install",)),
    ("brew", ("link",)),
    ("launchctl", ("enable",)),
    ("launchctl", ("bootstrap",)),
    ("launchctl", ("bootout",)),
    ("launchctl", ("kickstart",)),
    ("uv", ("sync",)),
    ("npm", ("ci",)),
    ("npm", ("run",)),
    ("npm", ("install",)),
    ("pre-commit", ("install",)),
    ("claude", ("login",)),
    ("claude", ("setup-token",)),
    ("gh", ("secret", "set")),
    ("gh", ("variable", "set")),
    ("gh", ("workflow", "run")),
)
INSTALLER_URLS = (HOMEBREW_INSTALLER_URL, CLAUDE_INSTALLER_URL)
GH_WRITE_METHODS = ("PATCH", "PUT", "POST", "DELETE")
CURL_WRITE_PATHS = ("/api/secrets", "/protect")

_TOKEN_PREFIXES = "|".join(
    ["gh" + "p_", "gh" + "o_", "gh" + "s_", "github" + "_pat_", "sk" + "-ant-", "xox" + "[abpr]-"]
)
_EMAIL_DOMAINS = "|".join(["gm" + "ail", "outlook", "hotmail", "yahoo", "icl" + "oud", "proton"])
LEAK_PATTERNS: dict[str, re.Pattern[str]] = {
    "token-prefix": re.compile(_TOKEN_PREFIXES),
    "personal-email": re.compile(
        r"[A-Za-z0-9._%+-]+@(?:" + _EMAIL_DOMAINS + r")\.[a-z]{2,}", re.IGNORECASE
    ),
    "tailnet-host": re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\." + "net", re.IGNORECASE),
    "machine-name": re.compile(
        r"\b[a-z0-9]+(?:-[a-z0-9]+)*-(?:mac" + "book|mac-" + "(?:studio|mini|pro)|i" + "mac)\\b",
        re.IGNORECASE,
    ),
}


@dataclass(frozen=True)
class Scenario:
    arch: str = "arm64"
    ram_bytes: int | str = 32 * GIB
    disk_free_kb: int | str = 500 * GIB // 1024
    gui_session: bool = True
    port_pids: tuple[int, ...] = ()
    gh_authed: bool = True
    gh_login: str = OWNER
    gh_api_fails: bool = False
    repos_existing: tuple[str, ...] = ()
    clone_empty_attempts: int = 0
    clone_failing_attempts: int = 0
    clone_omits_setup_script: bool = False
    brew_installed: tuple[str, ...] = ()
    brew_install_fails: bool = False
    node_version: str = "v22.12.0"
    claude_installer_installs: bool = True
    bootstrap_fails: bool = False
    no_token: bool = False
    token_value: str = TOKEN_SENTINEL
    served_sha_after_bootstrap: str = HEAD_PLACEHOLDER
    served_sha_after_restart: str = HEAD_PLACEHOLDER
    health_never: bool = False
    health_body: str | None = None
    plutil_fails: bool = False
    uv_fails: bool = False
    npm_fails: bool = False
    bootout_keeps_loaded: bool = False
    visibility: str = "public"
    offers_security_settings: bool = True
    scanning: str | None = "enabled"
    push_protection: str | None = "enabled"
    interaction_limit: str | None = "collaborators_only"
    pr_policy: str | None = "collaborators_only"
    pr_policy_sticks: bool = True
    issues: bool = True
    workflows_enabled: bool = True
    repo_read_fails: bool = False
    limits_read_fails: bool = False
    writes_fail: tuple[str, ...] = ()
    transport_stored: bool = True
    secret_engine: str = ENGINE_SLUG
    secrets_list_garbled: bool = False
    secret_post_status: int = 201
    engine_registered: bool = True
    engine_has_forge: bool = True
    api_reachable: bool = True
    control_token_status: int = 0
    preflight_garbled: bool = False
    protection_matching: bool = True
    protection_unreadable: bool = False
    protect_result: str = "applied"
    preflight_status: dict[str, str] = field(default_factory=dict)
    identity_kind: str = "fine-grained"
    token_login: str = OWNER
    ambient_token: str = AMBIENT_SENTINEL
    ambient_token_fails: bool = False
    bot_token: str = BOT_SENTINEL
    bot_login: str = BOT_LOGIN
    bot_identity_kind: str = "classic"
    bot_expiry: str | None = BOT_EXPIRY
    bot_permission: str = "write"
    bot_repo_visible: bool = True
    bot_invited: bool = False
    reviewer_stored: bool = False
    update_bootstrap_fails: bool = False
    sonar_admin_token: str = SONAR_ADMIN_SENTINEL
    sonar_analysis_token: str = SONAR_ANALYSIS_SENTINEL
    sonar_admin_login: str = SONAR_ADMIN_LOGIN
    sonar_analysis_login: str = SONAR_ANALYSIS_LOGIN
    sonar_organization: str = SONAR_ORGANIZATION
    sonar_project_key: str = SONAR_PROJECT_KEY
    sonar_bound_projects: int = 1
    sonar_unbound_projects: int = 0
    sonar_admin_status: int = 200
    sonar_analysis_status: int = 200
    sonar_analysis_anonymous: bool = False
    sonar_autoscan: str | None = "false"
    sonar_unreachable: bool = False
    gh_variable_fails: bool = False
    gh_secret_fails: bool = False
    ci_dispatch_fails: bool = False
    ci_list_fails: bool = False
    ci_view_fails: bool = False
    commit_reads_fail: bool = False
    ci_run_result: str = "success"
    ci_failed_job: str = "test-backend"
    ci_polls_before_done: int = 1
    ci_run_visible_after: int = 0
    sonar_evidence: str = "app-check"
    sonar_evidence_after_reads: int = 0
    claude_status: str = "logged-in"
    claude_method: str | None = "claude.ai"
    readiness_warning: bool = False
    readiness_extra: tuple[dict[str, str], ...] = ()

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass(frozen=True)
class Call:
    tool: str
    args: tuple[str, ...]
    cwd: str

    def matches(self, tool: str, *prefix: str) -> bool:
        return self.tool == tool and self.args[: len(prefix)] == prefix


@dataclass(frozen=True)
class Sandbox:
    base: Path
    checkout: Path
    home: Path
    bin_dir: Path
    stub_root: Path
    tmp_dir: Path

    @property
    def alan_dir(self) -> Path:
        return self.home / ".alan"

    @property
    def state_file(self) -> Path:
        return self.alan_dir / "setup.state"

    @property
    def queue_db(self) -> Path:
        return self.alan_dir / "queue.db"

    @property
    def profile_file(self) -> Path:
        return self.alan_dir / "engines" / "default_profile.yaml"

    @property
    def secrets_file(self) -> Path:
        return self.alan_dir / "secrets.env"

    @property
    def restart_marker(self) -> Path:
        return self.alan_dir / "setup.restart_pending"

    @property
    def ci_run_file(self) -> Path:
        return self.alan_dir / "setup.ci_run"

    @property
    def latch_file(self) -> Path:
        return self.alan_dir / "engines" / "code_quality.latch"

    @property
    def launch_agents_dir(self) -> Path:
        return self.home / "Library" / "LaunchAgents"

    @property
    def token_file(self) -> Path:
        return self.alan_dir / "state" / "control_token"

    @property
    def logs_dir(self) -> Path:
        return self.alan_dir / "logs"

    @property
    def plist(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"

    def plist_for(self, label: str) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{label}.plist"

    def elsewhere(self) -> Path:
        path = self.base / "elsewhere"
        path.mkdir(exist_ok=True)
        return path

    def alias(self) -> Path:
        path = self.base / "alias"
        if not path.exists():
            path.symlink_to(self.checkout, target_is_directory=True)
        return path

    def read_calls(self) -> list[Call]:
        log = self.stub_root / "calls.log"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [Call(row["tool"], tuple(row["args"]), row["cwd"]) for row in rows]

    def bootstrap_snapshots(self) -> list[dict[str, bool]]:
        log = self.stub_root / "bootstraps.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def handover_args(self) -> list[str] | None:
        path = self.stub_root / "handover.args"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else None

    def head(self) -> str:
        return git(self.checkout, "rev-parse", "HEAD")


@dataclass(frozen=True)
class SetupRun:
    returncode: int
    stdout: str
    stderr: str
    calls: tuple[Call, ...]
    sandbox: Sandbox

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def called(self, tool: str, *prefix: str) -> list[Call]:
        return [call for call in self.calls if call.matches(tool, *prefix)]

    def headers(self) -> list[str]:
        return re.findall(r"^== (\w+) ==$", self.stdout, re.MULTILINE)


def _git_env(home: Path) -> dict[str, str]:
    return {
        "PATH": SYSTEM_PATH,
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def git(cwd: Path, *args: str, home: Path | None = None) -> str:
    identity = ["-c", "user.name=setup-test", "-c", "user.email=setup@example.invalid"]
    done = subprocess.run(
        ["git", *identity, "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        env=_git_env(home or cwd),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return done.stdout.strip()


def _without_steps(checkout: Path, dropped: frozenset[str]) -> None:
    def kept(match: re.Match[str]) -> str:
        steps = " ".join(step for step in match.group(1).split() if step not in dropped)
        return f'SETUP_STEPS="{steps}"'

    for relative in SCRIPT_ENTRIES:
        script = checkout / relative
        script.write_text(
            STEPS_LINE.sub(kept, script.read_text(encoding="utf-8")), encoding="utf-8"
        )


def _dropped_steps(*, finish_steps: bool, sonar_step: bool, ci_steps: bool) -> frozenset[str]:
    dropped = set[str]() if finish_steps else set(FINISH_ONLY_STEPS)
    if not sonar_step:
        dropped.add(SONAR_STEP)
    if not ci_steps:
        dropped.update(CI_ONLY_STEPS)
    return frozenset(dropped)


def _populate_checkout(
    checkout: Path,
    origin: str | None,
    home: Path,
    *,
    finish_steps: bool,
    sonar_step: bool,
    ci_steps: bool,
) -> None:
    for relative in COPIED_FILES:
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, target)
    dropped = _dropped_steps(finish_steps=finish_steps, sonar_step=sonar_step, ci_steps=ci_steps)
    _without_steps(checkout, dropped)
    (checkout / "alan" / "web").mkdir(parents=True)
    (checkout / "alan" / "web" / ".gitkeep").write_text("", encoding="utf-8")
    git(checkout, "init", "-q", "-b", "main", home=home)
    git(checkout, "add", "-A", home=home)
    git(checkout, "commit", "-q", "-m", "scratch checkout", home=home)
    if origin is not None:
        git(checkout, "remote", "add", "origin", origin, home=home)


def _link_portable_tools(bin_dir: Path) -> None:
    for name in PORTABLE_TOOLS:
        found = shutil.which(name, path=SYSTEM_PATH)
        assert found is not None, f"portable tool {name} is missing from {SYSTEM_PATH}"
        (bin_dir / name).symlink_to(found)


@functools.cache
def shared_shims() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="setup-shims-"))
    atexit.register(shutil.rmtree, directory, True)
    write_shims(directory)
    return directory


def make_sandbox(
    base: Path,
    *,
    origin: str | None = EXAMPLE_ORIGIN,
    absent: frozenset[str] = frozenset({"claude"}),
    name: str = "clone",
    finish_steps: bool = False,
    sonar_step: bool = False,
    ci_steps: bool = False,
) -> Sandbox:
    base.mkdir(parents=True, exist_ok=True)
    base = base.resolve()
    sandbox = Sandbox(
        base=base,
        checkout=base / name,
        home=base / "home",
        bin_dir=base / "bin",
        stub_root=base / "stub",
        tmp_dir=base / "tmp",
    )
    for directory in (sandbox.home, sandbox.bin_dir, sandbox.stub_root, sandbox.tmp_dir):
        directory.mkdir()
    _link_portable_tools(sandbox.bin_dir)
    install_stubs(sandbox.bin_dir, shared_shims(), absent=absent)
    _populate_checkout(
        sandbox.checkout,
        origin,
        sandbox.home,
        finish_steps=finish_steps,
        sonar_step=sonar_step,
        ci_steps=ci_steps,
    )
    return sandbox


def run_env(sandbox: Sandbox) -> dict[str, str]:
    return {
        "PATH": str(sandbox.bin_dir),
        "HOME": str(sandbox.home),
        "TMPDIR": str(sandbox.tmp_dir),
        "LANG": "C",
        "HOMEBREW_PREFIX": str(sandbox.base / "homebrew"),
        ROOT_ENV: str(sandbox.stub_root),
        SHIMS_ENV: str(shared_shims()),
        **dict.fromkeys(WAIT_BUDGET_ENVS, str(WAIT_BUDGET)),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


def run_setup(
    sandbox: Sandbox,
    *args: str,
    scenario: Scenario | None = None,
    entry: str = "scripts/setup_macos.sh",
    extra_env: dict[str, str] | None = None,
    unset: tuple[str, ...] = (),
    stdin: str | None = None,
) -> SetupRun:
    (sandbox.stub_root / "scenario.json").write_text(
        (scenario or Scenario()).to_json(), encoding="utf-8"
    )
    before = len(sandbox.read_calls())
    env = {**run_env(sandbox), **(extra_env or {})}
    for name in unset:
        env.pop(name, None)
    feed = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    done = subprocess.run(
        ["/bin/bash", str(sandbox.checkout / entry), *args],
        cwd=sandbox.base,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        **feed,
    )
    return SetupRun(
        done.returncode, done.stdout, done.stderr, tuple(sandbox.read_calls()[before:]), sandbox
    )


def _api_write_method(call: Call) -> str | None:
    if "-X" not in call.args:
        return None
    return call.args[call.args.index("-X") + 1]


def _curl_mutates(call: Call) -> bool:
    if any(url in call.args for url in INSTALLER_URLS):
        return True
    posts = _api_write_method(call) == "POST"
    return posts and call.args[-1].endswith(CURL_WRITE_PATHS)


def is_mutating(call: Call) -> bool:
    if call.tool == "curl":
        return _curl_mutates(call)
    if call.matches("gh", "api"):
        return _api_write_method(call) in GH_WRITE_METHODS
    return any(call.matches(tool, *prefix) for tool, prefix in MUTATING)


def mutating_calls(run: SetupRun) -> list[Call]:
    return [call for call in run.calls if is_mutating(call)]


def preload_agent(
    sandbox: Sandbox,
    label: str = SERVICE_LABEL,
    *,
    checkout: Path | None = None,
    raw: str | None = None,
    pid: int = 4242,
    served_sha: str = HEAD_PLACEHOLDER,
    up: bool = True,
) -> None:
    location = sandbox.checkout if checkout is None else checkout
    sha = sandbox.head() if served_sha == HEAD_PLACEHOLDER else served_sha
    agent = {
        "label": label,
        "checkout": str(location),
        "runner": "update_runner.sh" if label == UPDATE_LABEL else "run_alan.sh",
        "pid": pid,
        "served_sha": sha,
        "up": up,
        "raw": raw,
    }
    path = sandbox.stub_root / "state" / "loaded" / f"{label}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(agent), encoding="utf-8")


def loaded_labels(sandbox: Sandbox) -> list[str]:
    directory = sandbox.stub_root / "state" / "loaded"
    return sorted(path.stem for path in directory.glob("*.json")) if directory.exists() else []


def seed_existing_install(sandbox: Sandbox) -> None:
    sandbox.alan_dir.mkdir(parents=True, exist_ok=True)
    sandbox.queue_db.touch()


def seed_state(sandbox: Sandbox, *steps: str) -> None:
    sandbox.alan_dir.mkdir(parents=True, exist_ok=True)
    sandbox.state_file.write_text("".join(f"{step}\n" for step in steps), encoding="utf-8")


def prepare_for_start(sandbox: Sandbox) -> None:
    for step in ("build", "profile"):
        done = run_setup(sandbox, "--only", step)
        assert done.returncode == 0, done.output


def seed_secrets_env(sandbox: Sandbox, text: str, mode: int = 0o600) -> None:
    sandbox.alan_dir.mkdir(parents=True, exist_ok=True)
    sandbox.secrets_file.write_text(text, encoding="utf-8")
    sandbox.secrets_file.chmod(mode)


def snapshot_files(*paths: Path) -> dict[Path, tuple[bytes, int]]:
    return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}


def leaks(text: str) -> dict[str, list[str]]:
    found = {name: pattern.findall(text) for name, pattern in LEAK_PATTERNS.items()}
    return {name: hits for name, hits in found.items() if hits}


def script_constant(name: str) -> int:
    match = re.search(rf"^{name}=(\d+)$", SETUP_SCRIPT.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None, f"no integer constant {name} in scripts/setup_macos.sh"
    return int(match.group(1))


def brew_formulae() -> list[str]:
    match = re.search(
        r'^BREW_FORMULAE="([^"]*)"$', SETUP_SCRIPT.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert match is not None, "no BREW_FORMULAE line in scripts/setup_macos.sh"
    return match.group(1).split()


def plist_slot_source(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"<key>ALAN_SLOT_REPO_ROOT</key>\s*<string>([^<]*)</string>", text)
    assert match is not None
    return match.group(1)


def all_output(run: SetupRun) -> str:
    calls_text = json.dumps([[call.tool, list(call.args)] for call in run.calls])
    return run.output + calls_text


BOUND_MARKS = ("preflight", "install", "build", "profile", "start")


def seed_bound_service(sandbox: Sandbox, *marks: str) -> None:
    python = sandbox.checkout / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(venv_python_text(), encoding="utf-8")
    python.chmod(0o755)
    sandbox.token_file.parent.mkdir(parents=True, exist_ok=True)
    sandbox.token_file.write_text(TOKEN_SENTINEL, encoding="utf-8")
    seed_existing_install(sandbox)
    seed_state(sandbox, *(marks or BOUND_MARKS))


def seed_running_service(sandbox: Sandbox, *marks: str) -> None:
    seed_bound_service(sandbox, *marks)
    preload_agent(sandbox)


def _require_code_quality(profile_file: Path) -> None:
    loaded = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
    loaded["requirements"] = [*loaded["requirements"], "code_quality"]
    profile_file.write_text(yaml.safe_dump(loaded), encoding="utf-8")


def seed_profile(
    sandbox: Sandbox,
    *,
    organization: str | None = SONAR_ORGANIZATION,
    key: str = SONAR_PROJECT_KEY,
    quality_required: bool = True,
) -> None:
    root = sandbox.alan_dir / "engines"
    default_profile_writer.write_profile(root, OWNER, REPO)
    if quality_required:
        _require_code_quality(root / default_profile.PROFILE_FILE_NAME)
    if organization is not None:
        default_profile_writer.write_code_quality(
            root, OWNER, REPO, organization, key, before_write=lambda: None
        )


def seed_latch(
    sandbox: Sandbox, organization: str = SONAR_ORGANIZATION, key: str = SONAR_PROJECT_KEY
) -> None:
    body = {
        "version": 1,
        "organization": organization,
        "project_key": key,
        "recorded_at": "2026-01-01T00:00:00+00:00",
    }
    sandbox.latch_file.parent.mkdir(parents=True, exist_ok=True)
    sandbox.latch_file.write_text(json.dumps(body), encoding="utf-8")


def stub_log(sandbox: Sandbox, name: str) -> list[dict[str, object]]:
    path = sandbox.stub_root / "state" / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def gh_writes(sandbox: Sandbox) -> list[dict[str, object]]:
    return stub_log(sandbox, "gh_writes.jsonl")


def stored_secrets(sandbox: Sandbox) -> list[dict[str, object]]:
    return stub_log(sandbox, "secrets_written.jsonl")


def assert_no_secret(run: SetupRun, *sentinels: str) -> None:
    surfaces = {
        "stdout": run.stdout,
        "stderr": run.stderr,
        "argv": json.dumps([list(call.args) for call in run.calls]),
    }
    root = run.sandbox.stub_root
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "scenario.json":
            surfaces[str(path.relative_to(root))] = path.read_text(
                encoding="utf-8", errors="replace"
            )
    for sentinel in sentinels:
        assert sentinel
        assert [name for name, text in surfaces.items() if sentinel in text] == []
