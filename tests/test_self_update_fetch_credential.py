from __future__ import annotations

import ast
import base64
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from alan import (
    deploy_refresh,
    process_runner,
    roots,
    secrets_store,
    self_update,
    self_update_github,
)
from alan.self_update import Reason, Verdict, check
from tests.self_update_fixtures import (
    OWNER,
    REPO,
    SLUG,
    TRANSPORT_SENTINEL,
    FetchEnv,
    Scenario,
    build_scenario,
    commit,
    completed,
    count_credential_reads,
    decoded_header_credential,
    forbid_credential_reads,
    git,
    is_credentialed_fetch,
    make_shallow_clone,
    redirect_env,
    store_transport_credential,
)

ROOT = Path(__file__).resolve().parent.parent
DEPLOY_REFRESH = ROOT / "alan" / "deploy_refresh.py"
PACKAGE_DIR = ROOT / "alan" / "self_update"
FETCH_URL = f"https://github.com/{OWNER}/{REPO}.git"
HEADER_KEY = f"http.{FETCH_URL}.extraHeader"
PATCHED_VALUE = "patched-value-not-a-credential"
CHANGED_VALUE = "changed-value-not-a-credential"
MIXED_SLUG = "MixedCase-Owner/Repo-Name"
MIXED_VALUE = "mixed-case-value-not-a-credential"
AMBIENT_VALUE = "ambient-value-not-a-credential"
ENCODED_SENTINEL = base64.b64encode(f"x-access-token:{TRANSPORT_SENTINEL}".encode()).decode()
EXPECTED_ARGV_TAIL = [
    "-c", "credential.helper=", "-c", "core.askPass=", "fetch", "--no-tags",
    "--no-write-fetch-head", "--refmap=", FETCH_URL, "+refs/heads/main:refs/deploy/target",
]
ORIGINS = {
    "https": f"https://github.com/{OWNER}/{REPO}",
    "https_dot_git": f"https://github.com/{OWNER}/{REPO}.git",
    "ssh_scheme": f"ssh://git@github.com/{OWNER}/{REPO}.git",
    "scp_style": f"git@github.com:{OWNER}/{REPO}.git",
}
REWRITE_TARGET = "https://mirror.example.invalid/"
REWRITE_PREFIX = "https://github.com/"
UNRELATED_PREFIX = "https://gitlab.example.com/"
ENVIRONMENT_REWRITES = {
    "config_count": {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{REWRITE_TARGET}.insteadOf",
        "GIT_CONFIG_VALUE_0": REWRITE_PREFIX,
    },
    "config_parameters": {
        "GIT_CONFIG_PARAMETERS": f"'url.{REWRITE_TARGET}.insteadOf={REWRITE_PREFIX}'"
    },
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    redirect_env(monkeypatch, tmp_path)


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Scenario:
    return build_scenario(monkeypatch, tmp_path)


def functions_reaching(source: str, seed: str) -> set[str]:
    functions = [
        node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef)
    ]
    reaching = {seed}
    grown = True
    while grown:
        grown = False
        for function in functions:
            called = {
                inner.func.id if isinstance(inner.func, ast.Name) else inner.func.attr
                for inner in ast.walk(function)
                if isinstance(inner, ast.Call)
                and isinstance(inner.func, (ast.Name, ast.Attribute))
            }
            if function.name not in reaching and called & reaching:
                reaching.add(function.name)
                grown = True
    return reaching


def names_in(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.name for alias in node.names)
    return found


def fetch_env(scenario: Scenario) -> FetchEnv:
    ((_, env),) = scenario.fetch_calls
    assert env is not None
    return env


def write_rewrite_config(tmp_path: Path, prefix: str) -> Path:
    config = tmp_path / "rewrite.gitconfig"
    config.write_text(f'[url "{REWRITE_TARGET}"]\n\tinsteadOf = {prefix}\n', encoding="utf-8")
    return config


def rewrite_in_global_config(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = write_rewrite_config(tmp_path, REWRITE_PREFIX)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))


def rewrite_in_repository_config(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    git(scenario.repo, "config", f"url.{REWRITE_TARGET}.insteadOf", REWRITE_PREFIX)


REWRITES = {
    "global_config": rewrite_in_global_config,
    "repository_config": rewrite_in_repository_config,
}


def read_count(monkeypatch: pytest.MonkeyPatch, run: Callable[[], object]) -> int:
    reads = count_credential_reads(monkeypatch)
    run()
    return len(reads)


def test_the_owners_fetch_helpers_are_the_functions_that_reach_fetch_target() -> None:
    reaching = functions_reaching(DEPLOY_REFRESH.read_text(encoding="utf-8"), "fetch_target")
    assert reaching == {"fetch_target", "refresh"}


def test_no_updater_file_names_a_function_that_reaches_the_owners_fetch() -> None:
    reaching = functions_reaching(DEPLOY_REFRESH.read_text(encoding="utf-8"), "fetch_target")
    files = sorted(PACKAGE_DIR.glob("*.py"))
    assert files
    for path in files:
        assert not names_in(path) & reaching, path.name


def test_the_updater_decides_without_ever_calling_the_owners_fetch(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the owner's fetch was called")

    monkeypatch.setattr(deploy_refresh, "fetch_target", forbidden)
    monkeypatch.setattr(deploy_refresh, "refresh", forbidden)
    scenario.set_green()
    outcome, ctx = self_update.decide(scenario.repo)
    assert (outcome.verdict, outcome.reason) == (Verdict.GREEN, Reason.CI_PROVED_GREEN)
    assert ctx.fetched == scenario.fetched


def test_a_shallow_checkout_decides_without_reading_the_credential(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shallow = make_shallow_clone(scenario.pair, tmp_path)
    forbid_credential_reads(monkeypatch)
    record = check(shallow)
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.SHALLOW_CLONE)
    assert record.fetched_sha is None
    assert scenario.fetch_calls == []


def test_an_off_main_checkout_decides_without_reading_the_credential(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(scenario.repo, "checkout", "-q", "-b", "topic")
    forbid_credential_reads(monkeypatch)
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.NOT_ON_MAIN)
    assert record.fetched_sha is None
    assert scenario.fetch_calls == []


@pytest.mark.parametrize(
    "url",
    ["https://gitlab.example.com/o/n", "git@ghe.example.com:o/n.git", "/srv/local/repo.git"],
)
def test_a_non_github_origin_is_blocked_before_the_fetch_without_reading_the_credential(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    git(scenario.repo, "remote", "set-url", "origin", url)
    forbid_credential_reads(monkeypatch)
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.BLOCKED, Reason.NO_GITHUB_ORIGIN)
    assert record.fetched_sha is None
    assert scenario.fetch_calls == []


def test_an_up_to_date_decision_reads_the_credential_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = build_scenario(monkeypatch, tmp_path, behind=False)
    assert read_count(monkeypatch, lambda: check(current.repo)) == 1
    assert len(current.fetch_calls) == 1


def test_a_rejected_decision_reads_the_credential_once(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = self_update.rejected_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{scenario.fetched}\n", encoding="utf-8")
    assert read_count(monkeypatch, lambda: check(scenario.repo)) == 1
    assert scenario.router.calls == []


def test_a_dirty_decision_reads_the_credential_once(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    (scenario.repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    assert read_count(monkeypatch, lambda: check(scenario.repo)) == 1
    assert scenario.router.calls == []


def test_a_diverged_decision_reads_the_credential_once(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit(scenario.repo, "local.txt")
    assert read_count(monkeypatch, lambda: check(scenario.repo)) == 1
    assert scenario.router.calls == []


def test_a_forced_decision_reads_the_credential_once(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert read_count(monkeypatch, lambda: self_update.decide(scenario.repo, force=True)) == 1
    assert scenario.router.calls == []


def test_a_pinned_decision_reads_the_credential_once_for_the_fetch(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = scenario.fetched
    reads = count_credential_reads(monkeypatch)
    outcome, ctx = self_update.decide(scenario.repo, pin=pin, force=True)
    assert outcome.verdict is Verdict.GREEN
    assert ctx.fetched == pin
    assert len(reads) == 1


def test_a_failed_fetch_decision_reads_the_credential_once(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario.router.fail_fetch(completed(returncode=128))
    assert read_count(monkeypatch, lambda: check(scenario.repo)) == 1


def test_a_pending_check_with_no_run_reads_the_credential_twice(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario.set_runs()
    assert read_count(monkeypatch, lambda: check(scenario.repo)) == 2


def test_a_green_check_reads_the_credential_three_times(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario.set_green()
    assert read_count(monkeypatch, lambda: check(scenario.repo)) == 3


def test_a_read_immediately_precedes_every_credentialed_spawn(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, ...]] = []
    real_root = roots.secrets_store_root
    routed = process_runner.run_subprocess

    def reading() -> Path:
        events.append(("read",))
        return real_root()

    def spawning(
        cmd: Sequence[str], *, cwd: str | None, timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if is_credentialed_fetch(cmd) or cmd[0] == "gh":
            events.append(("spawn", cmd[0]))
        return routed(cmd, cwd=cwd, timeout=timeout, env=env)

    monkeypatch.setattr(roots, "secrets_store_root", reading)
    monkeypatch.setattr(process_runner, "run_subprocess", spawning)
    scenario.set_green()
    check(scenario.repo)
    assert events == [
        ("read",), ("spawn", "git"),
        ("read",), ("spawn", "gh"),
        ("read",), ("spawn", "gh"),
    ]


def test_the_credential_is_never_cached_between_decisions(scenario: Scenario) -> None:
    scenario.set_green()
    check(scenario.repo)
    store_transport_credential(value=CHANGED_VALUE)
    scenario.router.expected = CHANGED_VALUE
    check(scenario.repo)
    credentials = [decoded_header_credential(env) for _, env in scenario.fetch_calls]
    assert credentials == [
        f"x-access-token:{TRANSPORT_SENTINEL}",
        f"x-access-token:{CHANGED_VALUE}",
    ]


def test_the_fetch_uses_the_shared_stored_value_reader(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(self_update_github, "_stored_value", lambda owner, repo: PATCHED_VALUE)
    scenario.router.expected = PATCHED_VALUE
    scenario.set_runs()
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.PENDING, Reason.NO_PUSH_RUN)
    assert decoded_header_credential(fetch_env(scenario)) == f"x-access-token:{PATCHED_VALUE}"


def test_a_mixed_case_origin_fetches_with_the_value_stored_under_the_mixed_case_slug(
    scenario: Scenario,
) -> None:
    git(scenario.repo, "remote", "set-url", "origin", f"https://github.com/{MIXED_SLUG}.git")
    secrets_store.write_secret(MIXED_SLUG, "transport", MIXED_VALUE)
    scenario.router.expected = MIXED_VALUE
    scenario.set_runs()
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.PENDING, Reason.NO_PUSH_RUN)
    assert decoded_header_credential(fetch_env(scenario)) == f"x-access-token:{MIXED_VALUE}"
    assert scenario.fetch_calls[0][0][-2] == f"https://github.com/{MIXED_SLUG}.git"


def test_a_record_stored_only_under_the_lowercased_slug_fails_the_fetch(
    scenario: Scenario,
) -> None:
    git(scenario.repo, "remote", "set-url", "origin", f"https://github.com/{MIXED_SLUG}.git")
    secrets_store.write_secret(MIXED_SLUG.lower(), "transport", MIXED_VALUE)
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert record.fetched_sha is None
    assert scenario.fetch_calls == []


def test_ambient_token_variables_never_stand_in_for_a_missing_record(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "ALAN_VERIFIER_GH_TOKEN"):
        monkeypatch.setenv(name, AMBIENT_VALUE)
    secrets_store.delete_secret(SLUG, "transport")
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert scenario.fetch_calls == []
    assert scenario.router.calls == []


def test_the_fetch_command_line_is_the_pinned_credential_free_shape(scenario: Scenario) -> None:
    scenario.set_runs()
    check(scenario.repo)
    ((argv, _),) = scenario.fetch_calls
    assert argv == ["git", "-C", str(scenario.repo), *EXPECTED_ARGV_TAIL]
    assert all(TRANSPORT_SENTINEL not in part for part in argv)
    assert all(ENCODED_SENTINEL not in part for part in argv)


def test_the_credential_travels_only_as_a_basic_header_in_the_git_child_environment(
    scenario: Scenario,
) -> None:
    scenario.set_runs()
    check(scenario.repo)
    env = fetch_env(scenario)
    assert {key: value for key, value in env.items() if key.startswith("GIT_CONFIG_")} == {
        "GIT_CONFIG_PARAMETERS": "",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": HEADER_KEY,
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": HEADER_KEY,
        "GIT_CONFIG_VALUE_1": f"Authorization: Basic {ENCODED_SENTINEL}",
    }
    values = {key: value for key, value in env.items() if value is not None}
    assert all(TRANSPORT_SENTINEL not in value for value in values.values())
    assert [key for key, value in values.items() if ENCODED_SENTINEL in value] == [
        "GIT_CONFIG_VALUE_1"
    ]


def test_the_fetch_environment_blanks_every_ambient_prompt_trace_and_transport_source(
    scenario: Scenario,
) -> None:
    scenario.set_runs()
    check(scenario.repo)
    env = fetch_env(scenario)
    for key in ("GIT_ASKPASS", "SSH_ASKPASS", "GIT_TRACE", "GIT_TRACE_CURL", "GIT_TRACE_PACKET"):
        assert env[key] == ""
    for key in ("GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF"):
        assert env[key] == ""
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ALLOW_PROTOCOL"] == "https"
    assert env["GIT_TRACE_REDACT"] == "1"


@pytest.mark.parametrize("name", ["GIT_CURL_VERBOSE", "GIT_SSL_NO_VERIFY"])
def test_the_fetch_environment_removes_the_presence_only_variables_from_the_child(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "0")
    scenario.set_runs()
    check(scenario.repo)
    env = fetch_env(scenario)
    assert name in env
    assert env[name] is None


@pytest.mark.parametrize("url", ORIGINS.values(), ids=ORIGINS.keys())
def test_every_origin_form_fetches_the_parsed_repository_over_https(
    scenario: Scenario, url: str
) -> None:
    git(scenario.repo, "remote", "set-url", "origin", url)
    scenario.set_runs()
    record = check(scenario.repo)
    assert record.fetched_sha == scenario.fetched
    ((argv, _),) = scenario.fetch_calls
    assert argv[-2] == FETCH_URL
    assert "origin" not in argv


@pytest.mark.parametrize("arrange", REWRITES.values(), ids=REWRITES.keys())
def test_a_rewrite_of_the_fetch_url_to_another_https_host_fails_the_fetch_closed(
    scenario: Scenario,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arrange: Callable[..., None],
) -> None:
    git(scenario.repo, "remote", "set-url", "origin", ORIGINS["scp_style"])
    arrange(scenario, monkeypatch, tmp_path)
    scenario.set_green()
    reads = count_credential_reads(monkeypatch)
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert record.fetched_sha is None
    assert scenario.fetch_calls == []
    assert scenario.router.calls == []
    assert len(reads) == 1


@pytest.mark.parametrize("pinned", [False, True], ids=["forced", "pinned"])
def test_a_rewrite_of_the_fetch_url_fails_forced_and_pinned_runs_closed(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pinned: bool
) -> None:
    git(scenario.repo, "remote", "set-url", "origin", ORIGINS["scp_style"])
    rewrite_in_global_config(scenario, monkeypatch, tmp_path)
    pin = scenario.fetched if pinned else None
    outcome, ctx = self_update.decide(scenario.repo, pin=pin, force=True)
    assert (outcome.verdict, outcome.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert ctx.fetched == ""
    assert scenario.fetch_calls == []


def test_a_rewrite_of_an_unrelated_prefix_does_not_fail_the_fetch(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    git(scenario.repo, "remote", "set-url", "origin", ORIGINS["scp_style"])
    config = write_rewrite_config(tmp_path, UNRELATED_PREFIX)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    scenario.set_runs()
    record = check(scenario.repo)
    assert record.fetched_sha == scenario.fetched
    assert len(scenario.fetch_calls) == 1


@pytest.mark.parametrize(
    "variables", ENVIRONMENT_REWRITES.values(), ids=ENVIRONMENT_REWRITES.keys()
)
def test_a_rewrite_the_fetch_child_never_inherits_does_not_fail_the_fetch(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, variables: Mapping[str, str]
) -> None:
    git(scenario.repo, "remote", "set-url", "origin", ORIGINS["scp_style"])
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    scenario.set_runs()
    record = check(scenario.repo)
    assert record.fetched_sha == scenario.fetched
    assert len(scenario.fetch_calls) == 1


def test_the_rewrite_probe_carries_no_credential_and_precedes_the_credentialed_fetch(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    probes: list[tuple[list[str], FetchEnv | None]] = []
    routed = process_runner.run_subprocess

    def spawning(
        cmd: Sequence[str], *, cwd: str | None, timeout: float,
        env: FetchEnv | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if "ls-remote" in cmd:
            events.append("probe")
            probes.append((list(cmd), env))
        if is_credentialed_fetch(cmd):
            events.append("fetch")
        return routed(cmd, cwd=cwd, timeout=timeout, env=env)

    monkeypatch.setattr(process_runner, "run_subprocess", spawning)
    scenario.set_runs()
    check(scenario.repo)
    assert events == ["probe", "fetch"]
    ((argv, env),) = probes
    assert argv == ["git", "-C", str(scenario.repo), "ls-remote", "--get-url", FETCH_URL]
    assert env is not None
    assert env["GIT_CONFIG_COUNT"] == "0"
    assert env["GIT_CONFIG_PARAMETERS"] == ""
    assert env["GIT_CURL_VERBOSE"] is None
    values = [value for value in env.values() if value is not None]
    assert not any(TRANSPORT_SENTINEL in value or ENCODED_SENTINEL in value for value in values)
    assert not any(key.startswith("GIT_CONFIG_KEY_") for key in env)


def test_a_failed_rewrite_probe_fails_the_fetch_closed(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    routed = process_runner.run_subprocess

    def spawning(
        cmd: Sequence[str], *, cwd: str | None, timeout: float,
        env: FetchEnv | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if "ls-remote" in cmd:
            return completed(returncode=128)
        return routed(cmd, cwd=cwd, timeout=timeout, env=env)

    monkeypatch.setattr(process_runner, "run_subprocess", spawning)
    record = check(scenario.repo)
    assert (record.verdict, record.reason) == (Verdict.ERROR, Reason.FETCH_FAILED)
    assert scenario.fetch_calls == []
