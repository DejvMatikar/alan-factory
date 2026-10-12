from __future__ import annotations

import pytest

from alan import dep_sweep
from alan.dep_sweep import (
    AdvisoryPlan,
    PackageFix,
    Vulnerability,
    lowest_clearing_version,
    plan_fixes,
    widen_requirement,
)


def _vuln(
    vuln_id: str,
    fixes: tuple[str, ...],
    *,
    package: str = "urllib3",
    installed: str = "2.7.0",
) -> Vulnerability:
    return Vulnerability(
        id=vuln_id, package=package, installed_version=installed, fix_versions=fixes
    )


def test_target_is_the_lowest_fix_not_the_latest() -> None:
    plan = plan_fixes([_vuln("A-1", ("2.7.5", "2.8.0", "2.9.1"))], frozenset())

    assert plan.fixes == (PackageFix("urllib3", "2.7.0", "2.7.5", ("A-1",)),)
    assert plan.unfixable == ()


def test_two_advisories_on_one_package_take_the_smallest_version_clearing_both() -> None:
    plan = plan_fixes(
        [_vuln("A-1", ("2.7.5",)), _vuln("B-2", ("2.8.0", "2.9.0"))], frozenset()
    )

    assert plan.fixes == (PackageFix("urllib3", "2.7.0", "2.8.0", ("A-1", "B-2")),)


def test_cross_branch_advisories_resolve_to_a_version_clearing_every_advisory() -> None:
    plan = plan_fixes(
        [
            _vuln("A-1", ("1.26.18", "2.0.7"), installed="1.26.5"),
            _vuln("B-2", ("2.0.2",), installed="1.26.5"),
        ],
        frozenset(),
    )

    assert [fix.target_version for fix in plan.fixes] == ["2.0.7"]


def test_advisories_fixed_on_separate_release_branches_still_resolve_to_one_fix() -> None:
    plan = plan_fixes(
        [
            _vuln("A-1", ("1.26.18",), installed="1.26.5"),
            _vuln("B-2", ("2.0.2",), installed="1.26.5"),
        ],
        frozenset(),
    )

    assert [fix.target_version for fix in plan.fixes] == ["2.0.2"]
    assert plan.unfixable == ()


def test_a_package_with_no_clearing_target_is_unfixable_as_a_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dep_sweep, "lowest_clearing_version", lambda _vulns: None)
    fixable = _vuln("A-1", ("2.8.0",))
    stuck = _vuln("B-2", ())

    plan = plan_fixes([fixable, stuck], frozenset())

    assert plan == AdvisoryPlan(fixes=(), unfixable=(fixable, stuck))


def test_same_branch_fix_wins_over_a_later_major_when_it_clears_everything() -> None:
    plan = plan_fixes(
        [_vuln("A-1", ("1.26.18", "2.0.7"), installed="1.26.5")], frozenset()
    )

    assert [fix.target_version for fix in plan.fixes] == ["1.26.18"]


def test_pre_release_and_non_pep440_fix_strings_are_never_targets() -> None:
    plan = plan_fixes(
        [_vuln("A-1", ("2.8.0rc1", "not-a-version", "2.8.0", "2.9.0.dev1"))], frozenset()
    )

    assert [fix.target_version for fix in plan.fixes] == ["2.8.0"]


def test_an_advisory_fixed_only_by_pre_releases_is_unfixable() -> None:
    vuln = _vuln("A-1", ("2.8.0rc1", "junk"))

    plan = plan_fixes([vuln], frozenset())

    assert plan == AdvisoryPlan(fixes=(), unfixable=(vuln,))


def test_an_advisory_with_no_fix_versions_is_unfixable() -> None:
    vuln = _vuln("A-1", ())

    assert plan_fixes([vuln], frozenset()).unfixable == (vuln,)


def test_fix_versions_at_or_below_the_installed_version_are_not_candidates() -> None:
    vuln = _vuln("A-1", ("2.6.0", "2.7.0"))

    assert plan_fixes([vuln], frozenset()).unfixable == (vuln,)


def test_an_unparsable_installed_version_keeps_every_stable_fix_as_a_candidate() -> None:
    plan = plan_fixes([_vuln("A-1", ("1.0.0", "2.0.0"), installed="weird")], frozenset())

    assert [fix.target_version for fix in plan.fixes] == ["1.0.0"]


def test_suppressed_ids_are_dropped_before_planning() -> None:
    plan = plan_fixes(
        [_vuln("PYSEC-1", ("9.9.9",)), _vuln("A-1", ("2.8.0",))], frozenset({"PYSEC-1"})
    )

    assert plan.fixes == (PackageFix("urllib3", "2.7.0", "2.8.0", ("A-1",)),)


def test_a_fully_suppressed_run_plans_nothing() -> None:
    plan = plan_fixes([_vuln("PYSEC-1", ())], ["PYSEC-1"])

    assert plan == AdvisoryPlan(fixes=(), unfixable=())


def test_a_package_that_also_has_an_open_proposal_is_still_planned() -> None:
    plan = plan_fixes([_vuln("A-1", ("2.8.0",), package="requests", installed="2.0.0")], ())

    assert [fix.package for fix in plan.fixes] == ["requests"]


def test_a_package_with_one_fixable_and_one_unfixable_advisory_gets_both_outcomes() -> None:
    fixable = _vuln("A-1", ("2.8.0",))
    stuck = _vuln("B-2", ())

    plan = plan_fixes([fixable, stuck], frozenset())

    assert plan.fixes == (PackageFix("urllib3", "2.7.0", "2.8.0", ("A-1",)),)
    assert plan.unfixable == (stuck,)


def test_package_names_are_grouped_by_canonical_name() -> None:
    plan = plan_fixes(
        [
            _vuln("A-1", ("2.0.0",), package="Foo_Bar", installed="1.0.0"),
            _vuln("B-2", ("2.1.0",), package="foo-bar", installed="1.0.0"),
        ],
        frozenset(),
    )

    assert plan.fixes == (PackageFix("Foo_Bar", "1.0.0", "2.1.0", ("A-1", "B-2")),)


def test_fixes_are_ordered_by_canonical_package_name() -> None:
    plan = plan_fixes(
        [
            _vuln("Z-1", ("2.0.0",), package="zeta", installed="1.0.0"),
            _vuln("A-1", ("2.0.0",), package="Alpha", installed="1.0.0"),
        ],
        frozenset(),
    )

    assert [fix.package for fix in plan.fixes] == ["Alpha", "zeta"]


def test_duplicate_advisory_reports_collapse_to_one_id() -> None:
    plan = plan_fixes([_vuln("A-1", ("2.8.0",)), _vuln("A-1", ("2.8.0",))], frozenset())

    assert plan.fixes[0].advisory_ids == ("A-1",)


def test_lowest_clearing_version_is_none_for_an_empty_input() -> None:
    assert lowest_clearing_version([]) is None


def test_lowest_clearing_version_is_none_when_one_advisory_has_no_fix() -> None:
    assert lowest_clearing_version([_vuln("A-1", ("2.8.0",)), _vuln("B-2", ())]) is None


@pytest.mark.parametrize(
    ("requirement", "target", "expected"),
    [
        ("starlette>=1.7.0,<2", "1.8.0", "starlette>=1.7.0,<2"),
        ("starlette>=1.7.0,<2", "2.0.1", "starlette>=1.7.0,<3"),
        ("foo>=0.4,<0.5", "0.5.3", "foo>=0.4,<0.6"),
        ("foo>=0.4,<0.5", "0.4.9", "foo>=0.4,<0.5"),
        ("foo>=0.0.1,<0.0.5", "0.0.7", "foo>=0.0.1,<0.1"),
        ("foo==1.2.3", "1.2.5", "foo==1.2.5"),
        ("foo==1.2.*", "1.3.1", "foo==1.3.1"),
        ("foo===1.0", "1.1", "foo==1.1"),
        ("uvicorn[standard]>=0.53.0", "0.60.0", "uvicorn[standard]>=0.53.0"),
        ("foo", "3.0", "foo"),
        (
            "foo[a,b]>=1,<2; python_version<'3.12'",
            "2.1.0",
            "foo[a,b]>=1,<3; python_version<'3.12'",
        ),
        ("foo<=1.5", "1.6", "foo<2"),
        ("foo~=1.4.2", "2.0.0", "foo>=1.4.2,<3"),
        ("foo!=1.5,>=1.0", "1.5", "foo>=1.0"),
        ("foo>=1.0,<2,<3", "2.5", "foo>=1.0,<3"),
        ("foo @ https://example.com/foo-9.whl", "9.0", "foo @ https://example.com/foo-9.whl"),
        (
            "foo >= 1.0, < 2 ; python_version < '3.12'",
            "2.1",
            "foo>=1.0,<3; python_version < '3.12'",
        ),
    ],
)
def test_widen_requirement_matrix(requirement: str, target: str, expected: str) -> None:
    assert widen_requirement(requirement, target) == expected


@pytest.mark.parametrize(
    ("requirement", "target"),
    [
        ("foo>=3", "2.0"),
        ("foo>3", "2.0"),
        ("not a requirement!!", "2.0"),
        ("foo<2", "not-a-version"),
        ("foo (>=1.0,<2)", "3.0"),
    ],
)
def test_widen_requirement_refuses_what_it_cannot_widen(requirement: str, target: str) -> None:
    assert widen_requirement(requirement, target) is None
