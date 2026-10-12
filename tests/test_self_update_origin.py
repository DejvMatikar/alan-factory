from __future__ import annotations

from pathlib import Path

import pytest

from alan import process_runner
from alan.self_update import _origin
from tests._deploy_render_support import EXAMPLE_ORIGIN, scratch_checkout
from tests.self_update_fixtures import completed

_EXPECTED = ("example-owner", "example-repo")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example-owner/example-repo",
        "https://github.com/example-owner/example-repo.git",
        "https://user@github.com/example-owner/example-repo",
        "https://user:secret@github.com/example-owner/example-repo.git",
        "ssh://git@github.com/example-owner/example-repo.git",
        "ssh://github.com/example-owner/example-repo",
        "git@github.com:example-owner/example-repo.git",
        "git@github.com:example-owner/example-repo",
        "github.com:example-owner/example-repo.git",
    ],
)
def test_accepted_github_origin_forms_yield_owner_and_repository(url: str) -> None:
    assert _origin.parse_origin(url) == _EXPECTED


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/Some-Owner/My.Repo_name-1", ("Some-Owner", "My.Repo_name-1")),
        ("https://github.com/o/r", ("o", "r")),
        ("https://github.com/o/.github", ("o", ".github")),
        ("https://github.com/o/r.git.git", ("o", "r.git")),
        (f"https://github.com/{'o' * 39}/{'r' * 100}", ("o" * 39, "r" * 100)),
        (f"https://github.com/o/{'r' * 100}.git", ("o", "r" * 100)),
    ],
)
def test_owner_and_repository_characters_github_allows_are_accepted(
    url: str, expected: tuple[str, str]
) -> None:
    assert _origin.parse_origin(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://GitHub.COM/example-owner/example-repo",
        "git@GITHUB.com:example-owner/example-repo.git",
        "https://github.com:443/example-owner/example-repo",
        "ssh://git@github.com:22/example-owner/example-repo",
        "https://github.com/example-owner/example-repo/",
        "https://github.com/example-owner/example-repo.git/",
        "https://github.com/example-owner/example-repo ",
        " https://github.com/example-owner/example-repo",
        "https://github.com/example-owner/example-repo\n",
    ],
)
def test_forms_the_renderer_refuses_are_rejected(url: str) -> None:
    assert _origin.parse_origin(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/example-owner/example-repo",
        "git@gitlab.com:example-owner/example-repo.git",
        "https://github.com.example.net/example-owner/example-repo",
        "https://github.com@example.net/example-owner/example-repo",
        "https://ghe.example.com/example-owner/example-repo",
        "ssh://git@ghe.example.com/example-owner/example-repo.git",
        "http://github.com/example-owner/example-repo",
        "git://github.com/example-owner/example-repo",
    ],
)
def test_other_hosts_and_schemes_are_rejected(url: str) -> None:
    assert _origin.parse_origin(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example-owner/example-repo/extra",
        "git@github.com:example-owner/example-repo/extra.git",
        "https://github.com/example-owner",
        "https://github.com/example-owner/",
        "https://github.com//example-repo",
        "https://github.com/example-owner//",
        "git@github.com:/example-repo",
        "https://github.com/example-owner/.git",
    ],
)
def test_paths_without_exactly_one_owner_and_one_repository_are_rejected(url: str) -> None:
    assert _origin.parse_origin(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/../example-repo",
        "https://github.com/example-owner/..",
        "https://github.com/example-owner/.",
        "https://github.com/-example-owner/example-repo",
        "https://github.com/example owner/example-repo",
        "https://github.com/example-owner/example repo",
        "https://github.com/example-owner/example-repo?ref=main",
        "https://github.com/example-owner/example-repo#fragment",
        "https://github.com/example%2Fowner/example-repo",
        "https://github.com/" + "o" * 40 + "/example-repo",
        "https://github.com/example-owner/" + "r" * 101,
        "https://github.com/example-owner/" + "r" * 101 + ".git",
    ],
)
def test_owner_or_repository_with_unsafe_characters_is_rejected(url: str) -> None:
    assert _origin.parse_origin(url) is None


@pytest.mark.parametrize(
    "url", ["", "   ", "/srv/git/example-repo.git", "example-repo", "github.com"]
)
def test_values_that_are_not_remote_urls_are_rejected(url: str) -> None:
    assert _origin.parse_origin(url) is None


def test_the_slot_url_is_the_normalized_https_clone_url() -> None:
    assert _origin.slot_repo_url(*_EXPECTED) == "https://github.com/example-owner/example-repo.git"


def test_the_pinned_environment_replaces_the_origin_url_with_the_slot_url() -> None:
    environment = _origin.pinned_environment(*_EXPECTED)
    assert environment == {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "remote.origin.url",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "remote.origin.url",
        "GIT_CONFIG_VALUE_1": "https://github.com/example-owner/example-repo.git",
    }


@pytest.mark.parametrize(
    "origin",
    [
        EXAMPLE_ORIGIN,
        "git@github.com:example-owner/example-repo",
        "https://gitlab.example.com/example-owner/example-repo.git",
        "https://GitHub.com/example-owner/example-repo.git",
        "https://github.com/other-owner/other-repo.git",
        "/srv/git/example-repo.git",
    ],
)
def test_git_reports_the_pinned_slot_url_whatever_the_checkout_origin_holds(
    tmp_path: Path, origin: str
) -> None:
    repo = scratch_checkout(tmp_path, origin)
    result = process_runner.run_subprocess(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        cwd=None,
        timeout=30.0,
        env=_origin.pinned_environment(*_EXPECTED),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == EXAMPLE_ORIGIN


def test_read_origin_strips_only_trailing_newlines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        lambda *a, **kw: completed(stdout=" https://github.com/o/r \n\n"),
    )
    assert _origin.read_origin(Path("repo")) == " https://github.com/o/r "


def test_read_origin_is_empty_when_git_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        process_runner,
        "run_subprocess",
        lambda *a, **kw: completed(stdout="https://github.com/o/r\n", returncode=2),
    )
    assert _origin.read_origin(Path("repo")) == ""


def test_read_origin_reads_the_given_checkout(tmp_path: Path) -> None:
    repo = scratch_checkout(tmp_path, EXAMPLE_ORIGIN)
    assert _origin.read_origin(repo) == EXAMPLE_ORIGIN


def test_read_origin_is_empty_for_a_checkout_without_an_origin(tmp_path: Path) -> None:
    repo = scratch_checkout(tmp_path, None)
    assert _origin.read_origin(repo) == ""
    assert _origin.resolve_origin(repo) is None


def test_read_origin_ignores_the_callers_git_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = scratch_checkout(
        tmp_path, "https://github.com/other-owner/other-repo.git", name="other"
    )
    repo = scratch_checkout(tmp_path, EXAMPLE_ORIGIN)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert _origin.read_origin(repo) == EXAMPLE_ORIGIN


def test_resolve_origin_yields_owner_and_repository_for_a_usable_checkout(
    tmp_path: Path,
) -> None:
    repo = scratch_checkout(tmp_path, EXAMPLE_ORIGIN)
    assert _origin.resolve_origin(repo) == _EXPECTED


@pytest.mark.parametrize(
    "url",
    [
        "https://GitHub.com/example-owner/example-repo.git",
        "https://github.com:443/example-owner/example-repo.git",
        "https://github.com/example-owner/example-repo/",
    ],
)
def test_resolve_origin_refuses_what_the_renderer_refuses(tmp_path: Path, url: str) -> None:
    repo = scratch_checkout(tmp_path, url)
    assert _origin.resolve_origin(repo) is None

