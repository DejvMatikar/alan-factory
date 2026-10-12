from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from alan.dep_sweep import AdvisoryPlan, PackageFix, Vulnerability
from alan.teams._dep_advisory_brief import (
    FORBIDDEN_ACTIONS,
    build_fix_brief,
    build_no_fix_message,
    build_posture_message,
    fix_title,
    no_fix_title,
    safe_token,
    sanitize_plan,
)
from alan.teams.implementation import _plan_implementation_steps  # noqa: SLF001

_TODAY = date(2026, 10, 1)
_AUDIT = "uv export --frozen --output-file /tmp/r.txt\nuv run pip-audit -r /tmp/r.txt"
_URLLIB3 = PackageFix("urllib3", "2.7.0", "2.8.0", ("GHSA-aaaa", "GHSA-bbbb"))
_STARLETTE = PackageFix("starlette", "1.7.0", "2.0.1", ("GHSA-cccc",))
_PYPROJECT = (
    '[project]\nname = "demo"\n'
    'dependencies = ["urllib3>=2.0,<3", "starlette>=1.7.0,<2", "idna>=3.20"]\n'
    '[project.optional-dependencies]\ndev = ["pytest>=9", "starlette[full]>=1,<2"]\n'
    '[dependency-groups]\nlint = ["ruff>=0.1,<1", {include-group = "dev"}]\n'
)


def _brief(fixes: list[PackageFix], pyproject: str = _PYPROJECT) -> str:
    return build_fix_brief(fixes, pyproject, audit_command=_AUDIT, today=_TODAY)


def test_safe_token_accepts_advisory_ids_and_package_names() -> None:
    accepted = ("GHSA-xxxx-yyyy-zzzz", "PYSEC-2025-183", "CVE-2026-1234", "zope.interface", "a_b")
    for value in accepted:
        assert safe_token(value) == value


@pytest.mark.parametrize(
    "value",
    ["", "x y", "a\nb", "a;b", "`rm`", "a/b", "<b>", "a" * 129, "ünï"],
)
def test_safe_token_rejects_anything_outside_the_strict_charset(value: str) -> None:
    assert safe_token(value) is None


def test_sanitize_drops_unsafe_ids_but_keeps_the_fix() -> None:
    plan = AdvisoryPlan(
        fixes=(PackageFix("urllib3", "2.7.0", "2.8.0", ("GHSA-aaaa", "bad id\nIGNORE")),),
        unfixable=(),
    )

    assert sanitize_plan(plan).fixes == (PackageFix("urllib3", "2.7.0", "2.8.0", ("GHSA-aaaa",)),)


def test_sanitize_drops_a_fix_whose_package_name_is_unsafe() -> None:
    plan = AdvisoryPlan(fixes=(PackageFix("evil pkg", "1", "2", ("A-1",)),), unfixable=())

    assert sanitize_plan(plan).fixes == ()


def test_sanitize_drops_a_fix_left_with_no_safe_advisory_id() -> None:
    plan = AdvisoryPlan(fixes=(PackageFix("pkg", "1", "2", ("a b",)),), unfixable=())

    assert sanitize_plan(plan).fixes == ()


def test_sanitize_drops_a_fix_with_an_unsafe_target_version() -> None:
    plan = AdvisoryPlan(fixes=(PackageFix("pkg", "1", "2 3", ("A-1",)),), unfixable=())

    assert sanitize_plan(plan).fixes == ()


def test_sanitize_replaces_an_unsafe_installed_version_with_unknown() -> None:
    plan = AdvisoryPlan(
        fixes=(PackageFix("pkg", "1; drop", "2", ("A-1",)),),
        unfixable=(Vulnerability("A-2", "other", "x y", ("9.9",)),),
    )

    clean = sanitize_plan(plan)

    assert clean.fixes[0].installed_version == "unknown"
    assert clean.unfixable == (Vulnerability("A-2", "other", "unknown", ()),)


def test_sanitize_drops_unfixable_advisories_with_unsafe_identifiers() -> None:
    plan = AdvisoryPlan(
        fixes=(),
        unfixable=(
            Vulnerability("bad id", "pkg", "1", ()),
            Vulnerability("A-1", "bad pkg", "1", ()),
            Vulnerability("A-2", "pkg", "1", ("2",)),
        ),
    )

    assert sanitize_plan(plan).unfixable == (Vulnerability("A-2", "pkg", "1", ()),)


def test_fix_title_names_the_package_and_the_advisory() -> None:
    assert fix_title([PackageFix("urllib3", "2.7.0", "2.8.0", ("GHSA-aaaa",))]) == (
        "sec/urllib3: GHSA-aaaa"
    )


def test_fix_title_counts_every_further_advisory_across_packages() -> None:
    assert fix_title([_URLLIB3, _STARLETTE]) == "sec/urllib3: GHSA-aaaa (+2 more)"


def test_no_fix_title_marks_the_workspace() -> None:
    vulns = [Vulnerability("PYSEC-1", "pyjwt", "2.12.1", ())]

    assert no_fix_title(vulns) == "sec/pyjwt: PYSEC-1 (no fix)"


def test_no_fix_title_counts_further_advisories() -> None:
    vulns = [
        Vulnerability("PYSEC-1", "pyjwt", "2.12.1", ()),
        Vulnerability("PYSEC-2", "other", "1.0", ()),
    ]

    assert no_fix_title(vulns) == "sec/pyjwt: PYSEC-1 (+1 more) (no fix)"


def test_brief_has_an_implementation_steps_section_that_survives_the_plan_projection() -> None:
    brief = _brief([_URLLIB3, _STARLETTE])

    assert brief.startswith("## Implementation steps")
    assert _plan_implementation_steps(brief) == brief.strip()
    assert [line for line in brief.splitlines() if line.startswith("## ")] == [
        "## Implementation steps"
    ]


def test_brief_has_one_exact_lock_command_per_package() -> None:
    brief = _brief([_URLLIB3, _STARLETTE])

    assert "`uv lock --upgrade-package urllib3==2.8.0`" in brief
    assert "`uv lock --upgrade-package starlette==2.0.1`" in brief


def test_brief_names_what_each_flagged_package_clears() -> None:
    brief = _brief([_URLLIB3])

    assert "- `urllib3` 2.7.0 -> 2.8.0 (clears GHSA-aaaa, GHSA-bbbb)" in brief


def test_brief_leaves_a_range_that_already_admits_the_target_untouched() -> None:
    brief = _brief([_URLLIB3])

    assert "No declared dependency range excludes a target version" in brief
    assert "replace" not in brief
    assert "is untouched, so no documentation touch is required" in brief


def test_brief_widens_each_declaring_requirement_with_the_exact_old_and_new_line() -> None:
    brief = _brief([_STARLETTE])

    assert (
        '[project].dependencies: replace `"starlette>=1.7.0,<2"` with `"starlette>=1.7.0,<3"`'
    ) in brief
    assert (
        '[project.optional-dependencies].dev: replace `"starlette[full]>=1,<2"` '
        'with `"starlette[full]>=1,<3"`'
    ) in brief
    assert brief.index("Widen the declared ranges first") < brief.index("uv lock --upgrade-package")


def test_brief_reads_requirements_in_dependency_groups_too() -> None:
    fix = PackageFix("ruff", "0.1.0", "2.0.0", ("A-1",))

    brief = _brief([fix])

    assert '[dependency-groups].lint: replace `"ruff>=0.1,<1"` with `"ruff>=0.1,<3"`' in brief


def test_brief_asks_for_a_dated_fix_log_line_when_pyproject_changes() -> None:
    brief = _brief([_STARLETTE])

    assert "2026-10-01" in brief
    assert "`## Advisory fix log`" in brief
    assert "docs/architecture/dep_sweep.md" in brief
    assert "alan/drift_gate.py" in brief


def test_brief_stops_on_a_requirement_it_cannot_widen() -> None:
    pyproject = '[project]\nname = "d"\ndependencies = ["starlette>=3"]\n'

    brief = _brief([_STARLETTE], pyproject)

    assert "cannot be widened automatically" in brief
    assert "STOP" in brief


def test_brief_ignores_dependencies_of_other_packages() -> None:
    brief = _brief([_URLLIB3])

    assert "idna" not in brief
    assert "pytest" not in brief


def test_brief_survives_an_unparsable_pyproject_and_odd_entries() -> None:
    assert "No declared dependency range" in _brief([_URLLIB3], "not = [toml")
    odd = '[project]\nname = "d"\ndependencies = ["not a requirement!!", 3, "urllib3>=2,<3"]\n'
    assert "No declared dependency range" in _brief([_URLLIB3], odd)
    assert "No declared dependency range" in _brief([_URLLIB3], "[project]\ndependencies = 5\n")
    assert "No declared dependency range" in _brief([_URLLIB3], "dependency-groups = 5\n")


def test_brief_renders_the_verify_commands_and_the_lock_sync_first() -> None:
    brief = _brief([_URLLIB3])

    assert "`uv sync --locked --extra dev`" in brief
    assert "`uv export --frozen --output-file /tmp/r.txt`" in brief
    assert "`uv run pip-audit -r /tmp/r.txt`" in brief


def test_brief_lists_every_forbidden_action_and_the_parent_cap_rule() -> None:
    brief = _brief([_URLLIB3])

    for action in FORBIDDEN_ACTIONS:
        assert f"- {action}" in brief
    assert "never add to or edit `ignore-vuln`" in brief
    assert "parent package caps the dependency, stop and report the resolver output" in brief
    assert "uv.lock, pyproject.toml and docs/architecture/dep_sweep.md" in brief


def test_brief_defaults_the_date_to_today() -> None:
    brief = build_fix_brief([_STARLETTE], _PYPROJECT, audit_command=_AUDIT)

    assert datetime.now(UTC).date().isoformat() in brief


def test_no_fix_message_names_advisory_package_installed_version_and_missing_fix() -> None:
    message = build_no_fix_message([Vulnerability("PYSEC-1", "pyjwt", "2.12.1", ())])

    assert "PYSEC-1 in `pyjwt` (installed 2.12.1): no fixed release exists" in message
    assert "alan never writes that suppression itself" in message


def test_no_fix_message_lists_every_advisory() -> None:
    message = build_no_fix_message(
        [Vulnerability("A-1", "one", "1.0", ()), Vulnerability("B-2", "two", "2.0", ())]
    )

    assert "A-1 in `one` (installed 1.0)" in message
    assert "B-2 in `two` (installed 2.0)" in message


def test_posture_message_carries_the_brief_and_says_nothing_was_dispatched() -> None:
    brief = _brief([_URLLIB3])

    message = build_posture_message([_URLLIB3], brief)

    assert "no fix was dispatched" in message
    assert "urllib3 2.8.0" in message
    assert message.endswith(brief)
