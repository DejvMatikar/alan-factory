from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.self_update_fixtures import (
    TRANSPORT_SENTINEL,
    isolate_secrets,
    link_executable,
    store_transport_credential,
)

REFUSE_CODE = 17
GIT_FETCH_LOG = "git.fetch.log"

_REFUSE_BODY = "#!/bin/sh\nexit {code}\n"
_SUCCEED_BODY = "#!/bin/sh\nexit 0\n"
_RECORD_BODY = '#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n'
_NPM_BODY = """#!{python}
import sys
from pathlib import Path

argv = sys.argv[1:]
if argv[:2] == ["run", "build"] and "--outDir" in argv:
    out = Path(argv[argv.index("--outDir") + 1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text("<html></html>\\n", encoding="utf-8")
sys.exit(0)
"""
_GH_BODY = """#!{python}
import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
path = argv[-1] if argv else ""
sources = json.loads(Path(sys.argv[0] + ".json").read_text(encoding="utf-8"))
runs = Path(sources["runs"])
checks = Path(sources["checks"])
seen = os.environ.get("GH_TOKEN") == sources["expected"]
with open(sources["log"], "a", encoding="utf-8") as log:
    log.write(("match" if seen else "mismatch") + "\\n")
if not seen:
    sys.exit(1)
if "actions/workflows" in path:
    sys.stdout.write(runs.read_text(encoding="utf-8"))
    sys.exit(0)
if "check-suites" in path:
    sys.stdout.write(checks.read_text(encoding="utf-8"))
    sys.exit(0)
sys.exit(1)
"""
_GIT_BODY = """#!{python}
import base64
import json
import os
import sys
from pathlib import Path

sources = json.loads(Path(sys.argv[0] + ".json").read_text(encoding="utf-8"))
args = sys.argv[1:]
url = next((part for part in args if part.startswith("https://github.com/")), None)
if "fetch" in args and url is not None:
    wanted = "x-access-token:" + sources["expected"]
    header = "Authorization: Basic " + base64.b64encode(wanted.encode()).decode("ascii")
    seen = os.environ.get("GIT_CONFIG_VALUE_1") == header
    with open(sources["log"], "a", encoding="utf-8") as log:
        log.write(("match" if seen else "mismatch") + "\\n")
    if not seen:
        sys.exit(128)
    env = {{k: v for k, v in os.environ.items() if k != "GIT_ALLOW_PROTOCOL"}}
    swapped = [sources["bare"] if part == url else part for part in args]
    os.execve(sources["git"], ["git", *swapped], env)
os.execv(sources["git"], ["git", *args])
"""
_LAUNCHCTL_BODY = """#!{python}
import sys
from pathlib import Path

state = Path(Path(sys.argv[0] + ".path").read_text(encoding="utf-8"))
argv = sys.argv[1:]
sub = argv[0] if argv else ""
loaded = state.read_text(encoding="utf-8").strip() == "1" if state.exists() else True
if sub == "print":
    sys.exit(0 if loaded else 113)
if sub == "bootout":
    state.write_text("0", encoding="utf-8")
    sys.exit(0)
if sub == "bootstrap":
    state.write_text("1", encoding="utf-8")
    sys.exit(0)
sys.exit(0)
"""


def _write_shim(path: Path, body: str) -> None:
    link_executable(path, body)


def refusing_shim(bin_dir: Path, name: str) -> None:
    _write_shim(bin_dir / name, _REFUSE_BODY.format(code=REFUSE_CODE))


def succeeding_shim(bin_dir: Path, name: str) -> None:
    _write_shim(bin_dir / name, _SUCCEED_BODY)


def recording_shim(bin_dir: Path, name: str, log: Path) -> None:
    _write_shim(bin_dir / name, _RECORD_BODY.format(log=log))


def npm_build_shim(bin_dir: Path) -> None:
    _write_shim(bin_dir / "npm", _NPM_BODY.format(python=sys.executable))


def gh_green_shim(bin_dir: Path, runs_file: Path, checks_file: Path) -> None:
    sources = {
        "runs": str(runs_file),
        "checks": str(checks_file),
        "expected": TRANSPORT_SENTINEL,
        "log": str(bin_dir / "gh.calls"),
    }
    (bin_dir / "gh.json").write_text(json.dumps(sources), encoding="utf-8")
    _write_shim(bin_dir / "gh", _GH_BODY.format(python=sys.executable))


def stateful_launchctl_shim(bin_dir: Path, state_file: Path, *, loaded: bool) -> None:
    state_file.write_text("1" if loaded else "0", encoding="utf-8")
    (bin_dir / "launchctl.path").write_text(str(state_file), encoding="utf-8")
    _write_shim(bin_dir / "launchctl", _LAUNCHCTL_BODY.format(python=sys.executable))


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    served_sha = ""

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"ok": True, "served_sha": self.served_sha}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, log_format: str, *args: object) -> None:
        del log_format, args


def start_health_server(served_sha: str) -> tuple[str, http.server.HTTPServer]:
    handler = type("_ShaHandler", (_HealthHandler,), {"served_sha": served_sha})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}/health", server


def stop_health_server(server: http.server.HTTPServer) -> None:
    server.shutdown()
    server.server_close()


def git_fetch_shim(bin_dir: Path, bare: Path) -> None:
    real_git = shutil.which("git")
    assert real_git is not None
    sources = {
        "git": real_git,
        "bare": str(bare),
        "expected": TRANSPORT_SENTINEL,
        "log": str(bin_dir / GIT_FETCH_LOG),
    }
    (bin_dir / "git.json").write_text(json.dumps(sources), encoding="utf-8")
    _write_shim(bin_dir / "git", _GIT_BODY.format(python=sys.executable))


def sealed_env(
    tmp_path: Path,
    bin_dir: Path,
    *,
    credential: str | None = TRANSPORT_SENTINEL,
) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    if credential is not None:
        with pytest.MonkeyPatch.context() as patch:
            isolate_secrets(patch, tmp_path)
            store_transport_credential(value=credential)
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home),
        "ALAN_STATE_ROOT": str(state),
        "ALAN_SECRETS_STORE_ROOT": str(tmp_path / "secrets"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def run_self_update(
    repo: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alan.self_update", *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
