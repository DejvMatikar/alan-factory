from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

from alan import scaffold_templates
from alan.scaffold_templates import AUTHORITATIVE_CHECKS, STACKS, get_stack_template


def test_stacks_match_authoritative_checks_keys() -> None:
    assert STACKS == tuple(sorted(AUTHORITATIVE_CHECKS))


def test_stacks_are_python_node_dotnet_ios() -> None:
    assert set(STACKS) == {"python", "node", "dotnet", "ios"}


def test_get_stack_template_returns_none_for_unknown_stack() -> None:
    assert get_stack_template("rust") is None


def test_get_stack_template_returns_none_for_none_stack() -> None:
    assert get_stack_template(None) is None


def test_each_stack_authoritative_checks_is_single_entry_frozenset() -> None:
    for stack in STACKS:
        checks = AUTHORITATIVE_CHECKS[stack]
        assert isinstance(checks, frozenset)
        assert len(checks) == 1


def _load_template(stack: str) -> scaffold_templates.StackTemplate:
    template = get_stack_template(stack)
    assert template is not None
    return template


_FULL_SHA_REF = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
_USES_LINE = re.compile(r"^\s*-\s*uses:\s*(?P<ref>\S+)(?P<tail>.*)$", re.MULTILINE)
_VERSION_COMMENT = re.compile(r"\s+# v\d+(?:\.\d+){0,2}")


def _workflow(stack: str) -> dict[str, Any]:
    parsed: object = yaml.safe_load(_load_template(stack).ci_yml)
    assert isinstance(parsed, dict)
    return parsed


def _step_refs(workflow: dict[str, Any]) -> list[str]:
    return [
        step["uses"] for job in workflow["jobs"].values() for step in job["steps"] if "uses" in step
    ]


@pytest.mark.parametrize("stack", STACKS)
def test_scaffold_ci_names_no_self_hosted_runner(stack: str) -> None:
    assert "self" + "-hosted" not in _load_template(stack).ci_yml.lower()


@pytest.mark.parametrize("stack", STACKS)
def test_scaffold_ci_grants_read_only_contents(stack: str) -> None:
    assert _workflow(stack)["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("stack", STACKS)
def test_scaffold_ci_pins_every_action_to_a_full_sha(stack: str) -> None:
    refs = _step_refs(_workflow(stack))
    assert refs
    unpinned = [ref for ref in refs if _FULL_SHA_REF.fullmatch(ref) is None]
    message = f"{stack} ci.yml pins these actions to a tag, not a 40-hex SHA: {unpinned}"
    assert unpinned == [], message


@pytest.mark.parametrize("stack", STACKS)
def test_scaffold_ci_pins_carry_a_version_comment(stack: str) -> None:
    pins = list(_USES_LINE.finditer(_load_template(stack).ci_yml))
    assert pins
    uncommented = [pin["ref"] for pin in pins if _VERSION_COMMENT.fullmatch(pin["tail"]) is None]
    assert uncommented == [], f"{stack} ci.yml pins carry no version comment: {uncommented}"


def test_ios_scaffold_runs_on_a_github_hosted_macos_image() -> None:
    job = _workflow("ios")["jobs"]["test"]
    assert job["runs-on"] == "macos-15"
    assert job["name"] in AUTHORITATIVE_CHECKS["ios"]


def test_get_stack_template_returns_all_three_files_nonempty() -> None:
    for stack in STACKS:
        template = _load_template(stack)
        assert template.stack == stack
        assert template.ci_yml.strip()
        assert template.dependabot_yml.strip()
        assert template.conventions_template.strip()


def test_ci_yml_parses_as_yaml_with_named_test_job() -> None:
    for stack in STACKS:
        template = _load_template(stack)
        parsed = yaml.safe_load(template.ci_yml)
        assert isinstance(parsed, dict)
        jobs = parsed["jobs"]
        assert isinstance(jobs, dict)
        job = jobs["test"]
        assert job["name"] in AUTHORITATIVE_CHECKS[stack]


def test_dependabot_yml_parses_with_updates_list() -> None:
    for stack in STACKS:
        template = _load_template(stack)
        parsed = yaml.safe_load(template.dependabot_yml)
        assert parsed["version"] == 2
        updates = parsed["updates"]
        assert isinstance(updates, list)
        assert len(updates) >= 1
        for entry in updates:
            assert entry["package-ecosystem"]


def test_dependabot_yml_includes_github_actions_ecosystem() -> None:
    for stack in STACKS:
        template = _load_template(stack)
        parsed = yaml.safe_load(template.dependabot_yml)
        ecosystems = {entry["package-ecosystem"] for entry in parsed["updates"]}
        assert "github-actions" in ecosystems


def test_conventions_template_has_project_title_placeholder() -> None:
    for stack in STACKS:
        template = _load_template(stack)
        rendered = template.conventions_template.format(project_title="Widget Co")
        assert "Widget Co" in rendered
        assert "{project_title}" not in rendered


def test_conventions_template_mentions_its_own_authoritative_check() -> None:
    for stack in STACKS:
        template = _load_template(stack)
        (check_name,) = AUTHORITATIVE_CHECKS[stack]
        assert check_name in template.conventions_template


IOS_CONVENTIONS_STRUCTURE = (
    "App/Sources/{AppMain,AppFeature,AppRoute}.swift",
    "Modules/Domain/",
    "Modules/Core/{Persistence,Networking,DesignSystem,Dependencies,UIKitBridge}/",
    "Modules/Features/<Feature>/{Sources,Tests}/",
    "Features -> Core -> Domain",
)


@pytest.mark.parametrize("fragment", IOS_CONVENTIONS_STRUCTURE)
def test_ios_conventions_render_preserves_module_map_and_dependency_direction(
    fragment: str,
) -> None:
    rendered = _load_template("ios").conventions_template.format(project_title="Widget Co")
    assert fragment in rendered


def test_ios_conventions_render_names_the_registered_authoritative_check() -> None:
    rendered = _load_template("ios").conventions_template.format(project_title="Widget Co")
    for check in AUTHORITATIVE_CHECKS["ios"]:
        assert check in rendered
