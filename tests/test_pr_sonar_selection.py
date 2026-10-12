from __future__ import annotations

import json
import subprocess

import pytest

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_sonar
from tests import _pr_branch_support as support

CURRENT_NAME = "SonarCloud Code Analysis"
OTHER_NAME = "SonarCloud Quality Gate"
FIRST_RUN_ID = 1001
SECOND_RUN_ID = 2002
FIRST_SUITE_ID = 5555
SECOND_SUITE_ID = 6666
ANNOTATION = {"path": "src/x.ts", "start_line": 7, "title": "issue", "message": "details"}


@pytest.fixture(autouse=True)
def _default_transport_token(monkeypatch: pytest.MonkeyPatch) -> None:
    support.set_verifier_transport_token(monkeypatch)


def _run(
    run_id: int,
    suite_id: int,
    *,
    name: str = CURRENT_NAME,
    app: str | None = "sonarqubecloud",
    conclusion: str = "failure",
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": run_id,
        "name": name,
        "conclusion": conclusion,
        "output": {"annotations_count": 1},
        "check_suite": {"id": suite_id},
    }
    if app is not None:
        row["app"] = {"slug": app}
    return row


class _Forge:
    def __init__(self, runs: list[dict[str, object]]) -> None:
        self.runs = runs
        self.annotation_ids: list[int] = []
        self.rerequested: list[int] = []

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if cmd[1] == "pr":
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123sha\n", stderr="")
        if "-X" in cmd:
            self.rerequested.append(int(cmd[-1].split("/")[-2]))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "/annotations" in cmd[2]:
            self.annotation_ids.append(int(cmd[2].split("/annotations")[0].split("/")[-1]))
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([ANNOTATION]), stderr="")
        payload = json.dumps({"check_runs": self.runs})
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")


def _installed(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, object]]) -> _Forge:
    forge = _Forge(runs)
    monkeypatch.setattr(process_runner, "run_subprocess", forge)
    return forge


def _annotated(forge: _Forge) -> tuple[_pr_sonar.SonarAnnotation, ...]:
    return _pr_sonar.list_sonarcloud_annotations(42, forge_target=DEFAULT_FORGE_TARGET)


def _retriggered(forge: _Forge) -> bool:
    del forge
    return _pr_sonar.retrigger_sonar_analysis(42, "task-x", forge_target=DEFAULT_FORGE_TARGET)


def test_the_run_with_the_current_name_is_the_one_whose_annotations_are_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forge = _installed(monkeypatch, [_run(FIRST_RUN_ID, FIRST_SUITE_ID)])
    assert len(_annotated(forge)) == 1
    assert forge.annotation_ids == [FIRST_RUN_ID]


def test_the_run_with_the_current_name_is_the_one_whose_suite_is_rerequested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forge = _installed(monkeypatch, [_run(FIRST_RUN_ID, FIRST_SUITE_ID)])
    assert _retriggered(forge) is True
    assert forge.rerequested == [FIRST_SUITE_ID]


@pytest.mark.parametrize("other_first", [True, False], ids=["other-first", "other-last"])
def test_a_sonarqubecloud_run_under_another_name_never_changes_the_annotation_pick(
    monkeypatch: pytest.MonkeyPatch, other_first: bool
) -> None:
    current = _run(FIRST_RUN_ID, FIRST_SUITE_ID)
    other = _run(SECOND_RUN_ID, SECOND_SUITE_ID, name=OTHER_NAME)
    forge = _installed(monkeypatch, [other, current] if other_first else [current, other])
    assert len(_annotated(forge)) == 1
    assert forge.annotation_ids == [FIRST_RUN_ID]


@pytest.mark.parametrize("other_first", [True, False], ids=["other-first", "other-last"])
def test_a_sonarqubecloud_run_under_another_name_never_changes_the_suite_pick(
    monkeypatch: pytest.MonkeyPatch, other_first: bool
) -> None:
    current = _run(FIRST_RUN_ID, FIRST_SUITE_ID)
    other = _run(SECOND_RUN_ID, SECOND_SUITE_ID, name=OTHER_NAME)
    forge = _installed(monkeypatch, [other, current] if other_first else [current, other])
    assert _retriggered(forge) is True
    assert forge.rerequested == [FIRST_SUITE_ID]


def test_the_first_run_with_the_current_name_and_annotations_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = _run(FIRST_RUN_ID, FIRST_SUITE_ID, conclusion="success")
    clean["output"] = {"annotations_count": 0}
    forge = _installed(monkeypatch, [clean, _run(SECOND_RUN_ID, SECOND_SUITE_ID)])
    assert len(_annotated(forge)) == 1
    assert forge.annotation_ids == [SECOND_RUN_ID]


def test_the_first_run_with_the_current_name_and_a_suite_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suiteless = _run(FIRST_RUN_ID, FIRST_SUITE_ID)
    del suiteless["check_suite"]
    forge = _installed(monkeypatch, [suiteless, _run(SECOND_RUN_ID, SECOND_SUITE_ID)])
    assert _retriggered(forge) is True
    assert forge.rerequested == [SECOND_SUITE_ID]


def test_a_run_with_the_current_name_and_no_app_is_still_picked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forge = _installed(monkeypatch, [_run(FIRST_RUN_ID, FIRST_SUITE_ID, app=None)])
    assert len(_annotated(forge)) == 1
    assert _retriggered(forge) is True
    assert forge.rerequested == [FIRST_SUITE_ID]


@pytest.mark.parametrize("app", ["github-actions", None])
def test_a_run_under_another_name_from_another_app_is_never_picked_by_either_function(
    monkeypatch: pytest.MonkeyPatch, app: str | None
) -> None:
    forge = _installed(monkeypatch, [_run(FIRST_RUN_ID, FIRST_SUITE_ID, name=OTHER_NAME, app=app)])
    assert _annotated(forge) == ()
    assert _retriggered(forge) is False
    assert forge.annotation_ids == []
    assert forge.rerequested == []


def test_a_sonarqubecloud_run_under_another_name_is_picked_when_no_run_has_the_current_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = _run(SECOND_RUN_ID, SECOND_SUITE_ID, name=OTHER_NAME)
    unrelated = _run(FIRST_RUN_ID, FIRST_SUITE_ID, name="Other", app=None)
    forge = _installed(monkeypatch, [unrelated, other])
    assert len(_annotated(forge)) == 1
    assert _retriggered(forge) is True
    assert forge.annotation_ids == [SECOND_RUN_ID]
    assert forge.rerequested == [SECOND_SUITE_ID]


def test_the_fallback_is_not_consulted_while_a_run_has_the_current_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = _run(FIRST_RUN_ID, FIRST_SUITE_ID, conclusion="success")
    clean["output"] = {"annotations_count": 0}
    del clean["check_suite"]
    other = _run(SECOND_RUN_ID, SECOND_SUITE_ID, name=OTHER_NAME)
    forge = _installed(monkeypatch, [clean, other])
    assert _annotated(forge) == ()
    assert _retriggered(forge) is False
    assert forge.annotation_ids == []
    assert forge.rerequested == []


@pytest.mark.parametrize("name", [CURRENT_NAME, OTHER_NAME])
def test_a_github_actions_run_is_never_picked_whatever_its_name(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    forge = _installed(
        monkeypatch, [_run(FIRST_RUN_ID, FIRST_SUITE_ID, name=name, app="github-actions")]
    )
    assert _annotated(forge) == ()
    assert _retriggered(forge) is False
    assert forge.annotation_ids == []
    assert forge.rerequested == []


def test_a_github_actions_run_with_the_current_name_hands_the_pick_to_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    impostor = _run(FIRST_RUN_ID, FIRST_SUITE_ID, app="github-actions")
    other = _run(SECOND_RUN_ID, SECOND_SUITE_ID, name=OTHER_NAME)
    forge = _installed(monkeypatch, [impostor, other])
    assert len(_annotated(forge)) == 1
    assert _retriggered(forge) is True
    assert forge.annotation_ids == [SECOND_RUN_ID]
    assert forge.rerequested == [SECOND_SUITE_ID]


def test_a_github_actions_run_never_displaces_a_real_run_with_the_current_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    impostor = _run(FIRST_RUN_ID, FIRST_SUITE_ID, app="github-actions")
    forge = _installed(monkeypatch, [impostor, _run(SECOND_RUN_ID, SECOND_SUITE_ID)])
    assert len(_annotated(forge)) == 1
    assert _retriggered(forge) is True
    assert forge.annotation_ids == [SECOND_RUN_ID]
    assert forge.rerequested == [SECOND_SUITE_ID]


@pytest.mark.parametrize(
    "app",
    ["sonarqubecloud", 5, {"slug": 5}, {}, None],
    ids=["slug-text", "int", "slug-int", "empty", "none"],
)
def test_an_app_that_is_not_a_slug_mapping_is_treated_as_no_app(app: object) -> None:
    row: dict[str, object] = {"id": 1, "name": CURRENT_NAME, "app": app}
    other: dict[str, object] = {"id": 2, "name": OTHER_NAME, "app": app}
    assert _pr_sonar._sonar_runs([row, other, "text", 3]) == [row]  # noqa: SLF001
