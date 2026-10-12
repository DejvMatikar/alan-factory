from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml

from alan import default_profile_writer, setup_sonar
from tests._setup_api_stubs import sha256
from tests._setup_ci_stubs import seed_run
from tests._setup_support import (
    BOT_SENTINEL,
    OWNER,
    SONAR_ADMIN_SENTINEL,
    SONAR_ANALYSIS_SENTINEL,
    Sandbox,
    SetupRun,
    assert_no_secret,
    make_sandbox,
    mutating_calls,
    run_setup,
    seed_running_service,
    seed_secrets_env,
    stored_secrets,
)

REPO = "example-repo"
SLUG = f"{OWNER}/{REPO}"
TYPED = f"{SONAR_ADMIN_SENTINEL}\n{SONAR_ANALYSIS_SENTINEL}\n"
SKIP_NOTE = "[skipped] SonarCloud is optional and was skipped"
PLANTED = "EXAMPLE_KEY=planted-value-1\nEXAMPLE_SECRET=never-echoed-never-echoed\n"


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _never_run(argv: list[str], stdin: str, unavailable: str) -> None:
    raise AssertionError("no tool may run while SonarCloud is skipped")


@pytest.fixture
def no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_sonar, "_run", _never_run)


@pytest.fixture
def engines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


def _configure(tmp_path: Path, stream: io.StringIO) -> Path:
    marker = tmp_path / "home" / "setup.restart_pending"
    setup_sonar.run_configure(SLUG, marker, accept_same_user=False, stream=stream)
    return marker


def _ready(tmp_path: Path) -> Sandbox:
    sandbox = make_sandbox(tmp_path, finish_steps=True, sonar_step=True)
    seed_running_service(sandbox)
    seed_secrets_env(sandbox, PLANTED)
    default_profile_writer.write_profile(sandbox.alan_dir / "engines", OWNER, REPO)
    return sandbox


def _sonar(sandbox: Sandbox, stdin: str | None, *flags: str) -> SetupRun:
    return run_setup(sandbox, "--only", "sonar", *flags, stdin=stdin)


def _marks(sandbox: Sandbox) -> list[str]:
    return sandbox.state_file.read_text(encoding="utf-8").splitlines()


def _block(sandbox: Sandbox) -> object:
    return yaml.safe_load(sandbox.profile_file.read_text(encoding="utf-8")).get("code_quality")


def test_the_admin_prompt_says_sonarcloud_is_optional_and_how_to_skip_it() -> None:
    assert "optional" in setup_sonar.ADMIN_PROMPT
    assert "press Enter to skip" in setup_sonar.ADMIN_PROMPT


def test_the_instructions_say_sonarcloud_is_optional_and_how_to_skip_it() -> None:
    assert "It is optional: press Enter at the admin token prompt to skip it" in (
        setup_sonar.INSTRUCTIONS_TEXT
    )


def test_a_skip_exits_with_a_code_of_its_own() -> None:
    assert setup_sonar.EXIT_SKIPPED == 3


@pytest.mark.usefixtures("no_tools")
def test_on_a_terminal_the_admin_prompt_carries_the_skip_hint_and_an_empty_answer_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompts: list[str] = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("getpass.getpass", answer)
    with pytest.raises(setup_sonar.SonarSkipped):
        _configure(tmp_path, _Terminal(""))
    assert prompts == [setup_sonar.ADMIN_PROMPT]


@pytest.mark.usefixtures("no_tools")
def test_end_of_input_on_a_terminal_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def closed(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("getpass.getpass", closed)
    with pytest.raises(setup_sonar.SonarSkipped):
        _configure(tmp_path, _Terminal(""))


@pytest.mark.usefixtures("no_tools")
def test_a_skip_touches_neither_the_profile_nor_the_restart_marker(
    engines: Path, tmp_path: Path
) -> None:
    default_profile_writer.write_profile(engines, OWNER, REPO)
    profile = (engines / "default_profile.yaml").read_bytes()
    with pytest.raises(setup_sonar.SonarSkipped):
        _configure(tmp_path, io.StringIO("\n"))
    assert (engines / "default_profile.yaml").read_bytes() == profile
    assert not (tmp_path / "home" / "setup.restart_pending").exists()


@pytest.mark.usefixtures("no_tools")
def test_a_malformed_admin_answer_is_refused_and_never_taken_for_a_skip(tmp_path: Path) -> None:
    with pytest.raises(setup_sonar.SonarRefused, match="does not look like a SonarCloud token"):
        _configure(tmp_path, io.StringIO("bad token\n"))


@pytest.mark.usefixtures("no_tools")
def test_the_configure_command_exits_three_for_an_empty_admin_answer_and_leaves_the_next_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = tmp_path / "home" / "setup.restart_pending"
    stream = io.StringIO(f"\n{SONAR_ANALYSIS_SENTINEL}\n")
    monkeypatch.setattr("sys.stdin", stream)
    code = setup_sonar.main(["configure", "--engine", SLUG, "--restart-marker", str(marker)])
    printed = capsys.readouterr()
    assert code == setup_sonar.EXIT_SKIPPED
    assert printed.err == ""
    assert stream.readline() == f"{SONAR_ANALYSIS_SENTINEL}\n"


@pytest.mark.parametrize(
    "stdin", ["\n", "", "  \n", None], ids=["enter", "end-of-input", "spaces", "no-stdin"]
)
def test_an_empty_answer_skips_the_step_and_records_and_writes_nothing(
    tmp_path: Path, stdin: str | None
) -> None:
    sandbox = _ready(tmp_path)
    seed_run(sandbox.stub_root, sandbox.ci_run_file)
    profile = sandbox.profile_file.read_bytes()
    run = _sonar(sandbox, stdin)
    assert run.returncode == 0, run.output
    assert SKIP_NOTE in run.stdout
    assert "[failed]" not in run.stdout
    assert "Resume with" not in run.stderr
    assert sandbox.profile_file.read_bytes() == profile
    assert _block(sandbox) is None
    assert mutating_calls(run) == []
    assert stored_secrets(sandbox) == []
    assert not sandbox.restart_marker.exists()
    assert run.called("launchctl", "kickstart") == []
    assert sandbox.ci_run_file.exists()
    assert "sonar" in _marks(sandbox)


def test_the_skip_says_how_to_connect_sonarcloud_later(tmp_path: Path) -> None:
    run = _sonar(_ready(tmp_path), "\n")
    assert run.returncode == 0, run.output
    assert "run scripts/setup_macos.sh --only sonar to connect it later" in run.stdout


def test_the_instructions_tell_the_user_before_the_prompt_that_sonarcloud_is_optional(
    tmp_path: Path,
) -> None:
    run = _sonar(_ready(tmp_path), "\n")
    body = run.stdout[run.stdout.index("== sonar ==") :]
    assert "It is optional: press Enter at the admin token prompt to skip it" in body
    assert "Paste the admin token first, then the analysis token." in body


def test_a_skip_reads_exactly_one_line_so_the_verifier_reads_its_own_token(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    run = run_setup(sandbox, "--from", "sonar", stdin=f"\n{BOT_SENTINEL}\n")
    assert run.returncode == 0, run.output
    assert run.headers() == ["sonar", "verifier", "updater"]
    assert [row["purpose"] for row in stored_secrets(sandbox)] == ["reviewer"]
    assert stored_secrets(sandbox)[0]["sha256"] == sha256(BOT_SENTINEL)
    assert len(run.called("launchctl", "kickstart")) == 1
    assert_no_secret(run, BOT_SENTINEL)


def test_a_later_run_connects_sonarcloud_and_forgets_the_ci_run_recorded_before(
    tmp_path: Path,
) -> None:
    sandbox = _ready(tmp_path)
    seed_run(sandbox.stub_root, sandbox.ci_run_file)
    assert _sonar(sandbox, "\n").returncode == 0
    assert sandbox.ci_run_file.exists()
    connected = _sonar(sandbox, TYPED)
    assert connected.returncode == 0, connected.output
    assert _block(sandbox) is not None
    assert not sandbox.ci_run_file.exists()
    assert len(connected.called("gh", "secret", "set")) == 1
    assert_no_secret(connected, SONAR_ADMIN_SENTINEL, SONAR_ANALYSIS_SENTINEL)


def test_a_refused_configuration_keeps_the_ci_run_recorded_before(tmp_path: Path) -> None:
    sandbox = _ready(tmp_path)
    seed_run(sandbox.stub_root, sandbox.ci_run_file)
    run = _sonar(sandbox, f"{SONAR_ADMIN_SENTINEL}\n")
    assert run.returncode == 1
    assert sandbox.ci_run_file.exists()
