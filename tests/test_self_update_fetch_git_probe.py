from __future__ import annotations

import base64
import http.server
import os
import stat
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from alan import process_runner, self_update
from alan.self_update import Reason, Verdict
from tests.self_update_fixtures import TRANSPORT_SENTINEL, build_scenario, redirect_env

FETCH_URL = "https://github.com/example-owner/example-repo.git"
CREDENTIAL_QUERY = "protocol=https\nhost=github.com\n\n"
SSH_REWRITE = '[url "ssh://git@github.com/"]\n\tinsteadOf = https://github.com/\n'
DEAD_PROXY = "http://127.0.0.1:9"
PROXY_NAMES = frozenset({"http_proxy", "https_proxy", "all_proxy"})
AMBIENT_NAMES = frozenset({
    "GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT", "GIT_ALLOW_PROTOCOL", "GIT_CURL_VERBOSE", "GIT_TRACE", "GIT_TRACE_CURL",
})
HELPER_OUTPUT = "username=probe-user\\npassword=probe-helper-secret\\n"
ASKPASS_OUTPUT = "probe-askpass-secret"
ENCODED_SENTINEL = base64.b64encode(f"x-access-token:{TRANSPORT_SENTINEL}".encode()).decode()

Arrangement = Callable[["Probe"], tuple[str, dict[str, str]]]


@dataclass(frozen=True)
class Probe:
    root: Path
    repo: Path
    marker: Path
    helper: str
    askpass: str
    ssh: str

    def ambient(
        self, config: str = "", extra: Mapping[str, str] | None = None, *, offline: bool = True
    ) -> dict[str, str]:
        gitconfig = self.root / "gitconfig"
        gitconfig.write_text(config, encoding="utf-8")
        env = {
            name: value
            for name, value in os.environ.items()
            if name.lower() not in PROXY_NAMES
            and name not in AMBIENT_NAMES
            and not name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
        }
        env.update({
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        })
        if offline:
            env.update({"https_proxy": DEAD_PROXY, "http_proxy": DEAD_PROXY})
        env.update(extra or {})
        return env

    def fill(
        self, argv: list[str], env: Mapping[str, str | None]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [*argv, "credential", "fill"],
            env=effective(env),
            input=CREDENTIAL_QUERY,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def fetch(
        self, argv: list[str], env: Mapping[str, str | None]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            argv, env=effective(env), capture_output=True, text=True, timeout=60, check=False
        )


def effective(env: Mapping[str, str | None]) -> dict[str, str]:
    return {name: value for name, value in env.items() if value is not None}


def write_script(path: Path, body: str) -> str:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


@pytest.fixture
def probe(tmp_path: Path) -> Probe:
    root = tmp_path / "probe"
    root.mkdir()
    repo = root / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)  # noqa: S603, S607
    marker = root / "marker"
    record = f'echo ran >> "{marker}"'
    return Probe(
        root,
        repo,
        marker,
        helper=write_script(root / "helper.sh", f"{record}\nprintf '{HELPER_OUTPUT}'"),
        askpass=write_script(root / "askpass.sh", f"{record}\nprintf '{ASKPASS_OUTPUT}'"),
        ssh=write_script(root / "ssh.sh", f"{record}\nexit 1"),
    )


def global_helper(probe: Probe) -> tuple[str, dict[str, str]]:
    return f"[credential]\n\thelper = {probe.helper}\n", {}


def url_scoped_global_helper(probe: Probe) -> tuple[str, dict[str, str]]:
    return f'[credential "https://github.com"]\n\thelper = {probe.helper}\n', {}


def parent_option_helper(probe: Probe) -> tuple[str, dict[str, str]]:
    return "", {"GIT_CONFIG_PARAMETERS": f"'credential.helper={probe.helper}'"}


def askpass_program_variable(probe: Probe) -> tuple[str, dict[str, str]]:
    return "", {"GIT_ASKPASS": probe.askpass}


def core_askpass_setting(probe: Probe) -> tuple[str, dict[str, str]]:
    return f"[core]\n\taskPass = {probe.askpass}\n", {}


def ssh_askpass_variable(probe: Probe) -> tuple[str, dict[str, str]]:
    return "", {"SSH_ASKPASS": probe.askpass}


SOURCES: dict[str, Arrangement] = {
    "global_helper": global_helper,
    "url_scoped_global_helper": url_scoped_global_helper,
    "parent_dash_c_helper": parent_option_helper,
    "git_askpass_program": askpass_program_variable,
    "core_askpass_program": core_askpass_setting,
    "ssh_askpass_program": ssh_askpass_variable,
}


def updater_overlay() -> dict[str, str | None]:
    return self_update._fetch_env(FETCH_URL, TRANSPORT_SENTINEL)


def updater_prefix(probe: Probe) -> list[str]:
    return self_update._fetch_argv(probe.repo, FETCH_URL)[:7]


@pytest.mark.parametrize("arrange", SOURCES.values(), ids=SOURCES.keys())
def test_without_the_updaters_prefix_and_environment_each_ambient_source_supplies_a_credential(
    probe: Probe, arrange: Arrangement
) -> None:
    config, extra = arrange(probe)
    result = probe.fill(["git", "-C", str(probe.repo)], probe.ambient(config, extra))
    assert result.returncode == 0
    assert "password=probe-" in result.stdout
    assert probe.marker.exists()


@pytest.mark.parametrize("arrange", SOURCES.values(), ids=SOURCES.keys())
def test_the_updaters_prefix_and_environment_consult_no_ambient_credential_source(
    probe: Probe, arrange: Arrangement
) -> None:
    config, extra = arrange(probe)
    env = {**probe.ambient(config, extra), **updater_overlay()}
    result = probe.fill(updater_prefix(probe), env)
    assert result.returncode != 0
    assert "password=" not in result.stdout
    assert not probe.marker.exists()


def test_the_updater_prefix_is_git_dash_c_repo_with_both_credential_resets() -> None:
    prefix = self_update._fetch_argv(Path("repo"), FETCH_URL)[:7]
    assert prefix == [
        "git", "-C", "repo", "-c", "credential.helper=", "-c", "core.askPass=",
    ]


def test_an_ambient_rewrite_to_ssh_never_reaches_the_ssh_command(probe: Probe) -> None:
    ambient = probe.ambient(SSH_REWRITE, {"GIT_SSH_COMMAND": probe.ssh})
    argv = self_update._fetch_argv(probe.repo, FETCH_URL)
    result = probe.fetch(argv, {**ambient, **updater_overlay()})
    assert result.returncode != 0
    assert not probe.marker.exists()


def test_the_same_rewrite_reaches_the_ssh_command_without_the_protocol_allowlist(
    probe: Probe,
) -> None:
    ambient = probe.ambient(SSH_REWRITE, {"GIT_SSH_COMMAND": probe.ssh})
    argv = self_update._fetch_argv(probe.repo, FETCH_URL)
    overlay = {**updater_overlay(), "GIT_ALLOW_PROTOCOL": "https:ssh"}
    result = probe.fetch(argv, {**ambient, **overlay})
    assert result.returncode != 0
    assert probe.marker.exists()


def test_a_decision_under_an_ambient_rewrite_to_ssh_ends_fetch_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, probe: Probe
) -> None:
    real = process_runner.run_subprocess
    scenario = build_scenario(monkeypatch, tmp_path)
    monkeypatch.setattr(process_runner, "run_subprocess", real)
    for name, value in probe.ambient(SSH_REWRITE, {"GIT_SSH_COMMAND": probe.ssh}).items():
        monkeypatch.setenv(name, value)
    record = self_update.check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert record.fetched_sha is None
    assert not probe.marker.exists()


@pytest.fixture
def loopback() -> Iterator[tuple[str, list[list[str]]]]:
    seen: list[list[str]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.headers.get_all("Authorization") or [])
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="probe"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, log_format: str, *args: object) -> None:
            del log_format, args

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/example-owner/example-repo.git", seen
    finally:
        server.shutdown()
        server.server_close()


def test_git_sends_exactly_the_one_expected_header_and_consults_no_ambient_source(
    probe: Probe, loopback: tuple[str, list[list[str]]]
) -> None:
    url, seen = loopback
    origin = url.rsplit("/", 2)[0]
    config = (
        f"[credential]\n\thelper = {probe.helper}\n"
        f'[credential "{origin}"]\n\thelper = {probe.helper}\n'
        f"[core]\n\taskPass = {probe.askpass}\n"
    )
    ambient = probe.ambient(
        config,
        {
            "GIT_ASKPASS": probe.askpass,
            "SSH_ASKPASS": probe.askpass,
            "GIT_CONFIG_PARAMETERS": f"'credential.helper={probe.helper}'",
        },
        offline=False,
    )
    overlay = {**self_update._fetch_env(url, TRANSPORT_SENTINEL), "GIT_ALLOW_PROTOCOL": "http"}
    result = probe.fetch(self_update._fetch_argv(probe.repo, url), {**ambient, **overlay})
    assert result.returncode != 0
    assert seen == [[f"Basic {ENCODED_SENTINEL}"]]
    assert not probe.marker.exists()
    assert TRANSPORT_SENTINEL not in result.stdout + result.stderr
    assert ENCODED_SENTINEL not in result.stdout + result.stderr


def test_an_inherited_curl_verbose_variable_never_reaches_the_credentialed_fetch(
    probe: Probe, loopback: tuple[str, list[list[str]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, seen = loopback
    for name in [name for name in os.environ if name.lower() in PROXY_NAMES]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CURL_VERBOSE", "1")
    overlay = {**self_update._fetch_env(url, TRANSPORT_SENTINEL), "GIT_ALLOW_PROTOCOL": "http"}
    result = process_runner.run_subprocess(
        self_update._fetch_argv(probe.repo, url), cwd=None, timeout=60, env=overlay
    )
    assert result.returncode != 0
    assert seen == [[f"Basic {ENCODED_SENTINEL}"]]
    assert "== Info" not in result.stderr
    assert "Authorization" not in result.stderr
    assert TRANSPORT_SENTINEL not in result.stdout + result.stderr
    assert ENCODED_SENTINEL not in result.stdout + result.stderr


def test_the_same_inherited_curl_verbose_variable_traces_the_fetch_when_it_is_not_removed(
    probe: Probe, loopback: tuple[str, list[list[str]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, _ = loopback
    for name in [name for name in os.environ if name.lower() in PROXY_NAMES]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CURL_VERBOSE", "1")
    overlay = {
        **self_update._fetch_env(url, TRANSPORT_SENTINEL),
        "GIT_ALLOW_PROTOCOL": "http",
        "GIT_CURL_VERBOSE": "1",
    }
    result = process_runner.run_subprocess(
        self_update._fetch_argv(probe.repo, url), cwd=None, timeout=60, env=overlay
    )
    assert "== Info" in result.stderr
