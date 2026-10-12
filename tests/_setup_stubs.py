from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT_ENV = "SETUP_STUB_ROOT"
SHIMS_ENV = "SETUP_STUB_SHIMS"
VENV_PYTHON = "venv-python"
UPDATE_LABEL = "ai.alan.update"
CLAUDE_CHECK_LABEL = "ai.alan.claude-check"
HOMEBREW_INSTALLER_URL = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
CLAUDE_INSTALLER_URL = "https://claude.ai/install.sh"
SONAR_ORIGIN = "https://sonarcloud.io"
TOOLS = (
    "gh",
    "launchctl",
    "brew",
    "curl",
    "claude",
    "security",
    "uv",
    "npm",
    "node",
    "pre-commit",
    "plutil",
    "sysctl",
    "lsof",
    "df",
    "uname",
    "sleep",
)
SLEEP_CEILING = 2000
GIB = 1024**3
HANDOVER_SCRIPT = (
    "#!/bin/bash\n"
    'printf \'%s\\n\' "$@" > "$SETUP_STUB_ROOT/handover.args"\n'
    'printf \'%s\\n\' "$0" > "$SETUP_STUB_ROOT/handover.entry"\n'
)
_GIT_IDENTITY = (
    "-c",
    "user.name=setup-test",
    "-c",
    "user.email=setup@example.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "init.defaultBranch=main",
)


def shim_text(tool: str) -> str:
    return (
        "#!/bin/bash\n"
        f"exec {shlex.quote(sys.executable)} -I -S "
        f"{shlex.quote(str(Path(__file__).resolve()))} {tool} \"$@\"\n"
    )


def venv_python_text() -> str:
    return (
        "#!/bin/bash\n"
        f"PYTHONDONTWRITEBYTECODE=1 PYTHONPATH={shlex.quote(str(REPO_ROOT))} "
        f"exec {shlex.quote(sys.executable)} \"$@\"\n"
    )


def write_shims(directory: Path) -> None:
    texts = {tool: shim_text(tool) for tool in TOOLS} | {VENV_PYTHON: venv_python_text()}
    for name, text in texts.items():
        shim = directory / name
        shim.write_text(text, encoding="utf-8")
        shim.chmod(0o755)


def install_stubs(bin_dir: Path, shims: Path, *, absent: frozenset[str] = frozenset()) -> None:
    for tool in TOOLS:
        if tool not in absent:
            (bin_dir / tool).symlink_to(shims / tool)


@dataclass(frozen=True)
class Ctx:
    root: Path
    scenario: dict[str, Any]

    @property
    def state(self) -> Path:
        return self.root / "state"

    def flag(self, name: str, default: Any) -> Any:
        return self.scenario.get(name, default)

    def log(self, tool: str, args: list[str]) -> None:
        row = {"tool": tool, "args": args, "cwd": os.getcwd()}
        with (self.root / "calls.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def count(self, name: str) -> int:
        path = self.state / f"{name}.count"
        return int(path.read_text(encoding="utf-8")) if path.exists() else 0

    def bump(self, name: str) -> int:
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / f"{name}.count"
        count = int(path.read_text(encoding="utf-8")) + 1 if path.exists() else 1
        path.write_text(str(count), encoding="utf-8")
        return count

    def agent_path(self, label: str) -> Path:
        return self.state / "loaded" / f"{label}.json"

    def load_agent(self, label: str) -> dict[str, Any] | None:
        path = self.agent_path(label)
        if not path.exists():
            return None
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def save_agent(self, agent: dict[str, Any]) -> None:
        path = self.agent_path(agent["label"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(agent), encoding="utf-8")


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *_GIT_IDENTITY, *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _resolve_sha(value: str, checkout: str) -> str:
    return _git(Path(checkout), "rev-parse", "HEAD") if value == "@head" else value


def stub_uname(ctx: Ctx, args: list[str]) -> int:
    print(ctx.flag("arch", "arm64"))
    return 0


def stub_sysctl(ctx: Ctx, args: list[str]) -> int:
    if args != ["-n", "hw.memsize"]:
        return 1
    print(ctx.flag("ram_bytes", 32 * GIB))
    return 0


def stub_df(ctx: Ctx, args: list[str]) -> int:
    print("Filesystem 1024-blocks Used Available Capacity Mounted on")
    print(f"/dev/stub 976490576 100000000 {ctx.flag('disk_free_kb', 500 * GIB // 1024)} 10% /")
    return 0


def stub_lsof(ctx: Ctx, args: list[str]) -> int:
    pids = ctx.flag("port_pids", [])
    for pid in pids:
        print(pid)
    return 0 if pids else 1


def stub_sleep(ctx: Ctx, args: list[str]) -> int:
    return 99 if ctx.bump("sleep") > SLEEP_CEILING else 0


def stub_security(ctx: Ctx, args: list[str]) -> int:
    print("stub: security must never be called", file=sys.stderr)
    return 1


def stub_claude(ctx: Ctx, args: list[str]) -> int:
    if args == ["--version"]:
        print("2.0.0 (Claude Code)")
        return 0
    if args == ["auth", "status"]:
        return int(_ci_stubs().claude_auth_status(ctx))
    print("stub: claude may only be asked for its version or its auth status", file=sys.stderr)
    return 1


def stub_node(ctx: Ctx, args: list[str]) -> int:
    (ctx.root / "node.path").write_text(os.environ["PATH"], encoding="utf-8")
    print(ctx.flag("node_version", "v22.12.0"))
    return 0


def stub_plutil(ctx: Ctx, args: list[str]) -> int:
    if ctx.flag("plutil_fails", False):
        print(f"{args[-1]}: Malformed", file=sys.stderr)
        return 1
    print(f"{args[-1]}: OK")
    return 0


def _installer_script(target: str, tool: str) -> str:
    return (
        f'mkdir -p "$(dirname {target})"\n'
        f"/bin/ln -s {shlex.quote(str(Path(os.environ[SHIMS_ENV]) / tool))} {target}\n"
    )


def _homebrew_installer() -> str:
    return (
        'if [ -n "${HOMEBREW_PREFIX:-}" ]; then\n'
        + _installer_script('"$HOMEBREW_PREFIX/bin/brew"', "brew")
        + "fi\n"
    )


def _sibling(name: str) -> Any:
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


def _api_stubs() -> Any:
    return _sibling("_setup_api_stubs")


def _sonar_stubs() -> Any:
    return _sibling("_setup_sonar_stubs")


def _ci_stubs() -> Any:
    return _sibling("_setup_ci_stubs")


def stub_curl(ctx: Ctx, args: list[str]) -> int:
    url = next((arg for arg in args if arg.startswith("http")), "")
    if url.endswith("/api/health"):
        return _curl_health(ctx)
    if url == HOMEBREW_INSTALLER_URL:
        print(_homebrew_installer())
        return 0
    if url == CLAUDE_INSTALLER_URL:
        installs = ctx.flag("claude_installer_installs", True)
        print(_installer_script('"$HOME/.local/bin/claude"', "claude") if installs else "true")
        return 0
    if url.startswith(SONAR_ORIGIN):
        return int(_sonar_stubs().curl(ctx, args))
    if "/api/" in url:
        return int(_api_stubs().curl(ctx, args))
    return 22


def _curl_health(ctx: Ctx) -> int:
    agent = ctx.load_agent("ai.alan")
    if ctx.flag("health_never", False) or agent is None or not agent["up"]:
        return 7
    body = ctx.flag("health_body", None)
    if body is not None:
        print(body)
        return 0
    api = _api_stubs()
    world = api.World(ctx)
    readiness = _ci_stubs().readiness(
        ctx, api._secret_rows(world), world.get("last_distinct", "unknown")
    )
    body = {
        "ok": True,
        "queue_paused": False,
        "served_sha": agent["served_sha"],
        "dispatch_readiness": readiness,
    }
    print(json.dumps(body))
    return 0


def _brew_installed(ctx: Ctx) -> list[str]:
    path = ctx.state / "brew_installed"
    recorded = path.read_text(encoding="utf-8").split() if path.exists() else []
    return sorted({*ctx.flag("brew_installed", []), *recorded})


def _brew_prefix(ctx: Ctx) -> Path:
    return Path(os.environ.get("HOMEBREW_PREFIX") or ctx.root / "brewprefix")


def _brew_install(ctx: Ctx, formula: str) -> int:
    if ctx.flag("brew_install_fails", False):
        return 1
    ctx.state.mkdir(parents=True, exist_ok=True)
    with (ctx.state / "brew_installed").open("a", encoding="utf-8") as handle:
        handle.write(formula + "\n")
    return 0


def stub_brew(ctx: Ctx, args: list[str]) -> int:
    prefix = _brew_prefix(ctx)
    installed = _brew_installed(ctx)
    for formula in installed:
        (prefix / "opt" / formula / "bin").mkdir(parents=True, exist_ok=True)
    verb = args[0] if args else ""
    if verb == "--prefix":
        print(prefix)
    elif verb == "shellenv":
        print(f'export HOMEBREW_PREFIX="{prefix}"')
        print(f'export HOMEBREW_CELLAR="{prefix}/Cellar"')
    elif verb == "list":
        if args[-1] not in installed:
            return 1
        print(f"{args[-1]} 1.0")
    elif verb == "install":
        return _brew_install(ctx, args[1])
    return 0


def _gh_auth_status(ctx: Ctx, args: list[str]) -> int:
    authed = ctx.flag("gh_authed", True) or (ctx.state / "gh_authed").exists()
    return 0 if authed else 1


def _gh_auth_login(ctx: Ctx, args: list[str]) -> int:
    ctx.state.mkdir(parents=True, exist_ok=True)
    (ctx.state / "gh_authed").write_text("1", encoding="utf-8")
    return 0


def _gh_auth_token(ctx: Ctx, args: list[str]) -> int:
    if ctx.flag("ambient_token_fails", False):
        return 1
    print(ctx.flag("ambient_token", "stub-ambient-token"))
    return 0


def _repos(ctx: Ctx) -> list[str]:
    path = ctx.state / "repos"
    recorded = path.read_text(encoding="utf-8").split() if path.exists() else []
    return [*ctx.flag("repos_existing", []), *recorded]


def _gh_repo_view(ctx: Ctx, args: list[str]) -> int:
    return 0 if args[2] in _repos(ctx) else 1


def _gh_repo_create(ctx: Ctx, args: list[str]) -> int:
    ctx.state.mkdir(parents=True, exist_ok=True)
    with (ctx.state / "repos").open("a", encoding="utf-8") as handle:
        handle.write(args[2] + "\n")
    return 0


def _gh_repo_clone(ctx: Ctx, args: list[str]) -> int:
    slug, dest = args[2], Path(args[3])
    attempt = ctx.bump("clone")
    if attempt <= int(ctx.flag("clone_failing_attempts", 0)):
        dest.mkdir()
        print("fatal: repository not found", file=sys.stderr)
        return 128
    _git(dest.parent, "init", "-q", str(dest))
    _git(dest, "remote", "add", "origin", f"https://github.com/{slug}.git")
    if attempt <= int(ctx.flag("clone_empty_attempts", 0)):
        print("warning: You appear to have cloned an empty repository.", file=sys.stderr)
        return 0
    (dest / "scripts").mkdir()
    name = "notes.txt" if ctx.flag("clone_omits_setup_script", False) else "setup_macos.sh"
    (dest / "scripts" / name).write_text(HANDOVER_SCRIPT, encoding="utf-8")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "template contents")
    return 0


def _sonar_gh(name: str) -> Callable[[Ctx, list[str]], int]:
    def handler(ctx: Ctx, args: list[str]) -> int:
        return int(getattr(_sonar_stubs(), name)(ctx, args))

    return handler


def _ci_gh(name: str) -> Callable[[Ctx, list[str]], int]:
    def handler(ctx: Ctx, args: list[str]) -> int:
        return int(getattr(_ci_stubs(), name)(ctx, args))

    return handler


_GH: dict[str, Callable[[Ctx, list[str]], int]] = {
    "workflow run": _ci_gh("workflow_run"),
    "run list": _ci_gh("run_list"),
    "run view": _ci_gh("run_view"),
    "auth status": _gh_auth_status,
    "auth login": _gh_auth_login,
    "auth token": _gh_auth_token,
    "repo view": _gh_repo_view,
    "repo create": _gh_repo_create,
    "repo clone": _gh_repo_clone,
    "variable set": _sonar_gh("gh_variable_set"),
    "variable list": _sonar_gh("gh_variable_list"),
    "secret set": _sonar_gh("gh_secret_set"),
    "secret list": _sonar_gh("gh_secret_list"),
}


def stub_gh(ctx: Ctx, args: list[str]) -> int:
    if args[:1] == ["api"]:
        return int(_api_stubs().gh_api(ctx, args))
    handler = _GH.get(" ".join(args[:2]))
    if handler is None:
        print("stub: unexpected gh call", file=sys.stderr)
        return 86
    return handler(ctx, args)


def stub_uv(ctx: Ctx, args: list[str]) -> int:
    if ctx.flag("uv_fails", False):
        return 1
    python = Path.cwd() / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.unlink(missing_ok=True)
    python.symlink_to(Path(os.environ[SHIMS_ENV]) / VENV_PYTHON)
    return 0


def stub_npm(ctx: Ctx, args: list[str]) -> int:
    if ctx.flag("npm_fails", False):
        return 1
    if args[:2] == ["run", "build"]:
        Path("dist").mkdir(exist_ok=True)
        Path("dist/index.html").write_text("<html></html>", encoding="utf-8")
    return 0


def stub_pre_commit(ctx: Ctx, args: list[str]) -> int:
    hook = Path(_git(Path.cwd(), "rev-parse", "--git-path", "hooks/pre-commit"))
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n", encoding="utf-8")
    hook.chmod(0o755)
    return 0


def _launchctl_print(ctx: Ctx, args: list[str]) -> int:
    parts = args[0].split("/")
    if len(parts) == 2:
        return 0 if ctx.flag("gui_session", True) else 113
    agent = ctx.load_agent(parts[2])
    if agent is None:
        print("Could not find service", file=sys.stderr)
        return 113
    if agent["raw"] is not None:
        print(agent["raw"])
        return 0
    checkout = agent["checkout"]
    print(
        f"{args[0]} = {{\n\tstate = running\n\n\tprogram = /bin/bash\n\targuments = {{\n"
        f"\t\t/bin/bash\n\t\t{checkout}/deploy/{agent['runner']}\n\t}}\n\n"
        f"\tworking directory = {checkout}\n\n\tpid = {agent['pid']}\n}}"
    )
    return 0


def _launchctl_bootstrap(ctx: Ctx, args: list[str]) -> int:
    if ctx.flag("bootstrap_fails", False):
        print("Bootstrap failed: 5: Input/output error", file=sys.stderr)
        return 5
    manifest = plistlib.loads(Path(args[1]).read_bytes())
    checkout = str(Path(manifest["ProgramArguments"][-1]).parent.parent)
    if manifest["Label"] == CLAUDE_CHECK_LABEL:
        return int(_ci_stubs().bootstrap_claude(ctx, manifest))
    if manifest["Label"] == UPDATE_LABEL:
        return int(_api_stubs().finish_stubs().bootstrap_update(ctx, checkout))
    home = Path(os.environ["HOME"]) / ".alan"
    seen = {
        "state_exists": (home / "setup.state").exists(),
        "profile_exists": (home / "engines" / "default_profile.yaml").exists(),
        "log_dir_exists": (home / "logs").is_dir(),
    }
    with (ctx.root / "bootstraps.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(seen) + "\n")
    served = _resolve_sha(ctx.flag("served_sha_after_bootstrap", "@head"), checkout)
    ctx.save_agent(
        {
            "label": manifest["Label"],
            "checkout": checkout,
            "runner": "run_alan.sh",
            "pid": 4242,
            "served_sha": served,
            "up": True,
            "raw": None,
            "view": _ci_stubs().service_view(),
        }
    )
    (home / "state").mkdir(parents=True, exist_ok=True)
    (home / "queue.db").touch()
    if not ctx.flag("no_token", False):
        (home / "state" / "control_token").write_text(
            ctx.flag("token_value", "sentinel-token"), encoding="utf-8"
        )
    return 0


def _launchctl_kickstart(ctx: Ctx, args: list[str]) -> int:
    agent = ctx.load_agent(args[-1].rsplit("/", 1)[-1])
    if agent is None:
        return 113
    if args[0] == "-k":
        agent["served_sha"] = _resolve_sha(
            ctx.flag("served_sha_after_restart", "@head"), agent["checkout"]
        )
        agent["up"] = True
        agent["view"] = _ci_stubs().service_view()
        ctx.save_agent(agent)
    return 0


def _launchctl_bootout(ctx: Ctx, args: list[str]) -> int:
    path = ctx.agent_path(args[0].rsplit("/", 1)[-1])
    if not path.exists():
        return 113
    if not ctx.flag("bootout_keeps_loaded", False):
        path.unlink()
    return 0


_LAUNCHCTL: dict[str, Callable[[Ctx, list[str]], int]] = {
    "print": _launchctl_print,
    "enable": lambda ctx, args: 0,
    "bootstrap": _launchctl_bootstrap,
    "kickstart": _launchctl_kickstart,
    "bootout": _launchctl_bootout,
}


def stub_launchctl(ctx: Ctx, args: list[str]) -> int:
    handler = _LAUNCHCTL.get(args[0]) if args else None
    return 64 if handler is None else handler(ctx, args[1:])


HANDLERS: dict[str, Callable[[Ctx, list[str]], int]] = {
    "gh": stub_gh,
    "launchctl": stub_launchctl,
    "brew": stub_brew,
    "curl": stub_curl,
    "claude": stub_claude,
    "security": stub_security,
    "uv": stub_uv,
    "npm": stub_npm,
    "node": stub_node,
    "pre-commit": stub_pre_commit,
    "plutil": stub_plutil,
    "sysctl": stub_sysctl,
    "lsof": stub_lsof,
    "df": stub_df,
    "uname": stub_uname,
    "sleep": stub_sleep,
}


def run(argv: list[str]) -> int:
    tool, args = argv[0], argv[1:]
    root = Path(os.environ[ROOT_ENV])
    scenario = json.loads((root / "scenario.json").read_text(encoding="utf-8"))
    ctx = Ctx(root, scenario)
    ctx.log(tool, args)
    return HANDLERS[tool](ctx, args)


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
