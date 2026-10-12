from __future__ import annotations

import re
from pathlib import Path

from alan import setup_claude_check, setup_verdicts
from tests._setup_support import leaks

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHITECTURE = REPO_ROOT / "docs" / "architecture"
SETUP_DOC = ARCHITECTURE / "setup.md"
SONAR_DOC = ARCHITECTURE / "sonarcloud.md"
INDEX = ARCHITECTURE / "README.md"
FULL_ORDER = (
    "preflight install build profile start credential harden protect confirm sonar verifier "
    "trigger claude updater latch report"
)


def _setup() -> str:
    return SETUP_DOC.read_text(encoding="utf-8")


def _section(doc: str, heading: str) -> str:
    assert doc.count(f"\n{heading}\n") == 1
    return doc.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]


def test_the_setup_doc_states_the_new_steps_in_order() -> None:
    doc = _setup()
    assert f"`{FULL_ORDER}`" in doc
    numbered = re.findall(r"^\d+\. `(\w+)`", doc, re.MULTILINE)
    steps = FULL_ORDER.split()
    assert numbered[: len(steps) + 1] == [*steps[:2], "copy", *steps[2:]]


def test_the_setup_doc_states_both_new_flags() -> None:
    doc = _setup()
    flags = doc.split("Flags: ", 1)[1].split("\n", 1)[0]
    assert "`--accept-sonar-user-token`" in flags
    assert "`--no-sonar`" in flags
    assert "`--check-claude`" in flags
    assert "names `scripts/setup_macos.sh --check-claude`" in doc
    assert "exits 2 together with `--from`, `--only` or `--uninstall`" in doc


def test_the_setup_doc_states_the_narrowed_secrets_env_promise() -> None:
    doc = _setup()
    assert "narrowed, not dropped" in doc
    assert "Bash never reads or prints it" in doc
    assert "the one writer of its contents is `alan.setup_verifier`" in doc
    assert "rewrites the single `ALAN_VERIFIER_LOGIN=` line and no other" in doc
    assert "The `trigger`, `claude`, `latch` and `report` steps never touch it." in doc


def test_the_setup_doc_states_the_credentials_setup_handles() -> None:
    doc = _setup()
    assert "### Setup handles four credentials" in doc
    assert "handles exactly these four" in doc
    assert "The steps after `verifier` handle no credential at all" in doc
    assert "alan never handles a Claude credential" in doc


def test_the_setup_doc_states_the_claude_check_rule() -> None:
    section = _section(_setup(), "## The Claude check")
    for phrase in (
        "`ai.alan.claude-check`",
        "`BIND_DIR`",
        "`launchctl bootstrap gui/<uid>`",
        "boots the agent out and deletes its file on pass, on fail and on timeout",
        "the single `on_exit` trap",
        "A pre-run sweep",
        "the `--uninstall` sweep",
        "`pass` or `fail`",
        "`method not reported`",
        "It never echoes any `claude` output",
        "setup never signs in for them",
        "`Resume with: scripts/setup_macos.sh --check-claude`",
    ):
        assert phrase in section


def test_the_claude_check_section_names_every_method_label() -> None:
    assert set(setup_claude_check.METHOD_LABELS.values())
    assert "METHOD_LABELS" in _section(_setup(), "## The Claude check")


def test_the_setup_doc_states_the_trigger_and_the_latch_rules() -> None:
    section = _section(_setup(), "## The CI trigger and the latch")
    for phrase in (
        "gh workflow run tests.yml --repo <owner>/<repo> --ref main",
        "`~/.alan/setup.ci_run`",
        "no second run",
        "exactly one fresh run",
        "`gate` job",
        "`sonarqubecloud`",
        "never counts",
        "later than the run's creation time",
        "`alan/setup_latch.py`",
        "directly above `alan.engine_preflight`",
        "`--from trigger`",
        "`--from latch`",
        "Free plan does not analyse private projects",
        "P3.7 rehearsal",
    ):
        assert phrase in section


def test_the_setup_doc_states_the_final_report() -> None:
    section = _section(_setup(), "## The final report")
    for phrase in (
        "owned P1.9b findings",
        "SonarCloud latched",
        "verifier stored and distinct",
        "Claude check",
        "updater loaded",
        "`setup.restart_pending`",
        "`dispatch_readiness`",
        "every `not_ready` or `unknown` entry, for any engine, fails",
        "a `warning`",
        "the earliest failed step in SETUP_STEPS order",
        "`setup is complete: ...`",
    ):
        assert phrase in section


def test_the_report_section_lists_every_step_a_readiness_requirement_maps_to() -> None:
    section = _section(_setup(), "## The final report")
    for requirement, step in setup_verdicts.STEP_BY_REQUIREMENT.items():
        assert f"`{requirement}` `{step}`" in section
    assert f"any other `{setup_verdicts.UNMAPPED_REQUIREMENT_STEP}`" in section


def test_the_closing_line_section_no_longer_names_p1_9c_as_pending() -> None:
    doc = _setup()
    closing = doc.split("### The closing line (PD5)\n", 1)[1].split("\n## ", 1)[0]
    assert "P1.9c" not in closing
    assert "the final report" in closing
    assert "Setup never says it is complete except in the verdict of the `report` step" in closing


def test_the_known_limitations_no_longer_carry_the_p1_9c_entries() -> None:
    doc = _setup()
    limitations = doc.split("## Known limitations\n", 1)[1].split("\n## ", 1)[0]
    assert "P1.9c" not in limitations
    assert "stays unresolved" not in limitations
    assert "no updater agent" not in limitations
    assert "no updater agent exists to run it" not in doc


def test_no_line_of_the_setup_doc_calls_p1_9c_pending() -> None:
    for line in _setup().splitlines():
        assert not ("P1.9c" in line and "pending" in line.lower())


def test_the_setup_doc_still_carries_no_identity_or_credential_pattern() -> None:
    assert leaks(_setup()) == {}


def test_the_sonarcloud_doc_explains_how_a_copy_is_configured_and_why_it_never_blocks() -> None:
    doc = SONAR_DOC.read_text(encoding="utf-8")
    section = doc.split("\n## How a copy's SonarCloud is configured\n", 1)[1].split("\n## ", 1)[0]
    for phrase in (
        "`scripts/setup_macos.sh`'s `sonar` step",
        "`SONAR_ORGANIZATION`",
        "`SONAR_PROJECT_KEY`",
        "`SONAR_TOKEN`",
        "`--accept-sonar-user-token`",
        "It never blocks a merge",
        "PD15",
        "only `gate` authoritative",
        "the `trigger` step dispatches one `tests.yml` run on `main`",
        "`sonarqubecloud`",
        "code_quality.latch",
        "A Sonar-named check posted by `github-actions`",
        "SonarCloud stays advisory throughout",
        "P3.7 rehearsal",
    ):
        assert phrase in section


def test_the_setup_doc_says_sonarcloud_is_optional_and_describes_skipping_it() -> None:
    section = _section(_setup(), "## SonarCloud")
    for phrase in (
        "advisory and optional",
        "press Enter to skip",
        "`SonarSkipped`",
        "exits 3",
        "elif [ $? = 3 ]",
        "`--no-sonar`",
        "sets no repository variable and no secret",
        "\"already configured\"",
        "deletes `~/.alan/setup.ci_run`",
        "a missing admin token is a skip, not a failure",
    ):
        assert phrase in section


def test_the_setup_doc_describes_the_latch_and_report_for_an_install_without_sonarcloud() -> None:
    doc = _setup()
    latch = _section(doc, "## The CI trigger and the latch")
    for phrase in (
        "`python -m alan.setup_latch standing`",
        "`not-connected`",
        "`unreported`",
        "always waits on the recorded run",
        "writes the latch only when a SonarCloud project is recorded",
        "[warning] SonarCloud has not reported",
    ):
        assert phrase in latch
    report = _section(doc, "## The final report")
    assert "SonarCloud: not connected (optional)" in report
    assert "`requirements` comparison drops only that one entry" in doc
    assert "`code_quality` is no longer a default requirement" in doc


def test_the_sonarcloud_doc_says_a_copy_is_complete_without_it() -> None:
    doc = SONAR_DOC.read_text(encoding="utf-8")
    section = doc.split("\n## How a copy's SonarCloud is configured\n", 1)[1].split("\n## ", 1)[0]
    for phrase in (
        "SonarCloud is optional on a copy",
        "\"SonarCloud: not connected (optional)\"",
        "pressing Enter",
        "`--no-sonar`",
        "\"already configured\"",
        "`code_quality` is no longer a default one",
        "The owner's install is unchanged",
    ):
        assert phrase in section
    assert "mandatory setup that is still in place" not in doc


def test_the_architecture_index_names_the_new_setup_topics_on_the_one_setup_line() -> None:
    lines = [
        line
        for line in INDEX.read_text(encoding="utf-8").splitlines()
        if line.startswith("- [setup.md](setup.md)")
    ]
    assert len(lines) == 1
    for topic in ("verifier bot", "SonarCloud latch", "--check-claude", "final report"):
        assert topic in lines[0]
