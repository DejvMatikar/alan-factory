from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from alan.self_update import _origin
from tests._deploy_render_support import (
    REFUSED_EXIT,
    SLOT_URL_KEY,
    plist_string_value,
    render_scratch,
)

_ROOT = Path(__file__).resolve().parent.parent
_RENDERER = _ROOT / "deploy" / "render_plist.sh"
_SETUP = _ROOT / "scripts" / "setup_macos.sh"
_GRAMMAR_PREFIXES = (
    "ALNUM_CHARS=",
    "NAME_CHARS=",
    "OWNER_RE=",
    "REPO_RE=",
    "HTTPS_ORIGIN_RE=",
    "SSH_ORIGIN_RE=",
    "SCP_ORIGIN_RE=",
)
_SETUP_PROBE = (
    '. "$1"; if parse_origin "$2"; then printf "%s %s\\n" "$ORIGIN_OWNER" "$ORIGIN_REPO"; '
    'else printf "refused\\n"; fi'
)
_SLUG = ("example-owner", "example-repo")
_LONGEST_OWNER = "o" * 39
_LONGEST_REPO = "r" * 100

PARITY_TABLE: dict[str, tuple[str, tuple[str, str] | None]] = {
    "https": ("https://github.com/example-owner/example-repo", _SLUG),
    "https-dot-git": ("https://github.com/example-owner/example-repo.git", _SLUG),
    "https-userinfo": ("https://user@github.com/example-owner/example-repo.git", _SLUG),
    "https-user-password": ("https://user:pw@github.com/example-owner/example-repo.git", _SLUG),
    "scp": ("git@github.com:example-owner/example-repo", _SLUG),
    "scp-dot-git": ("git@github.com:example-owner/example-repo.git", _SLUG),
    "scp-without-user": ("github.com:example-owner/example-repo.git", _SLUG),
    "ssh-url": ("ssh://git@github.com/example-owner/example-repo", _SLUG),
    "ssh-url-dot-git": ("ssh://git@github.com/example-owner/example-repo.git", _SLUG),
    "ssh-url-without-user": ("ssh://github.com/example-owner/example-repo.git", _SLUG),
    "mixed-case-names-are-kept": (
        "https://github.com/Some-Owner/My.Repo_name-1",
        ("Some-Owner", "My.Repo_name-1"),
    ),
    "single-character-names": ("https://github.com/o/r", ("o", "r")),
    "dot-github-repository": ("https://github.com/o/.github", ("o", ".github")),
    "dot-git-suffix-is-stripped-once": ("https://github.com/o/r.git.git", ("o", "r.git")),
    "longest-owner": (f"https://github.com/{_LONGEST_OWNER}/r", (_LONGEST_OWNER, "r")),
    "longest-repository": (f"https://github.com/o/{_LONGEST_REPO}", ("o", _LONGEST_REPO)),
    "longest-repository-before-suffix": (
        f"https://github.com/o/{_LONGEST_REPO}.git",
        ("o", _LONGEST_REPO),
    ),
    "uppercase-host": ("https://GitHub.com/example-owner/example-repo", None),
    "uppercase-host-scp": ("git@GITHUB.com:example-owner/example-repo.git", None),
    "explicit-port": ("https://github.com:443/example-owner/example-repo", None),
    "explicit-port-ssh-url": ("ssh://git@github.com:22/example-owner/example-repo.git", None),
    "trailing-slash": ("https://github.com/example-owner/example-repo/", None),
    "trailing-slash-after-suffix": ("https://github.com/example-owner/example-repo.git/", None),
    "trailing-space": ("https://github.com/example-owner/example-repo ", None),
    "leading-space": (" https://github.com/example-owner/example-repo", None),
    "other-host": ("https://gitlab.com/example-owner/example-repo", None),
    "lookalike-host": ("https://github.com.example.net/example-owner/example-repo", None),
    "lookalike-userinfo": ("https://github.com@example.net/example-owner/example-repo", None),
    "plain-http": ("http://github.com/example-owner/example-repo", None),
    "git-scheme": ("git://github.com/example-owner/example-repo", None),
    "extra-segment": ("https://github.com/example-owner/example-repo/extra", None),
    "owner-only": ("https://github.com/example-owner", None),
    "owner-with-trailing-slash": ("https://github.com/example-owner/", None),
    "empty-owner": ("https://github.com//example-repo", None),
    "empty-repository-after-suffix": ("https://github.com/example-owner/.git", None),
    "dot-repository": ("https://github.com/example-owner/.", None),
    "dot-dot-repository": ("https://github.com/example-owner/..", None),
    "dot-dot-owner": ("https://github.com/../example-repo", None),
    "owner-with-leading-hyphen": ("https://github.com/-example-owner/example-repo", None),
    "owner-too-long": (f"https://github.com/{'o' * 40}/example-repo", None),
    "owner-with-dot": ("https://github.com/example.owner/example-repo", None),
    "owner-with-underscore": ("https://github.com/example_owner/example-repo", None),
    "repository-too-long": (f"https://github.com/example-owner/{'r' * 101}", None),
    "repository-too-long-before-suffix": (
        f"https://github.com/example-owner/{'r' * 101}.git",
        None,
    ),
    "space-in-repository": ("https://github.com/example-owner/example repo", None),
    "non-ascii-owner": ("https://github.com/exämple-owner/example-repo", None),
    "metacharacter-owner": ("https://github.com/example&owner/example-repo", None),
    "query-string": ("https://github.com/example-owner/example-repo?ref=main", None),
    "fragment": ("https://github.com/example-owner/example-repo#fragment", None),
    "percent-escape": ("https://github.com/example%2Fowner/example-repo", None),
    "local-path": ("/srv/git/example-repo.git", None),
}


def _slot_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


def _expected(slug: tuple[str, str] | None) -> str | None:
    return None if slug is None else _slot_url(*slug)


def _renderer_url(tmp_path: Path, origin: str) -> str | None:
    rendered = render_scratch(tmp_path, origin)
    if rendered.result.returncode == REFUSED_EXIT:
        assert not rendered.target.exists()
        return None
    assert rendered.result.returncode == 0, rendered.result.stderr
    return plist_string_value(rendered.content, SLOT_URL_KEY)


def _setup_url(tmp_path: Path, origin: str) -> str | None:
    result = subprocess.run(
        ["/bin/bash", "-c", _SETUP_PROBE, "_", str(_SETUP), origin],
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    answer = result.stdout.strip()
    if answer == "refused":
        return None
    owner, repo = answer.split(" ")
    return _slot_url(owner, repo)


def _updater_url(origin: str) -> str | None:
    parsed = _origin.parse_origin(origin)
    return None if parsed is None else _origin.slot_repo_url(*parsed)


def _grammar_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.startswith(_GRAMMAR_PREFIXES)]


def _function_block(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"{name} is not defined in {path.name}"
    return match.group(0)


def test_the_table_covers_the_uppercase_host_the_explicit_port_and_the_trailing_slash() -> None:
    for name in ("uppercase-host", "explicit-port", "trailing-slash"):
        assert PARITY_TABLE[name][1] is None
    assert any(slug is not None for _, slug in PARITY_TABLE.values())


@pytest.mark.parametrize(("origin", "slug"), PARITY_TABLE.values(), ids=PARITY_TABLE.keys())
def test_the_renderer_derives_the_expected_slot_url(
    tmp_path: Path, origin: str, slug: tuple[str, str] | None
) -> None:
    assert _renderer_url(tmp_path, origin) == _expected(slug)


@pytest.mark.parametrize(("origin", "slug"), PARITY_TABLE.values(), ids=PARITY_TABLE.keys())
def test_setup_derives_the_expected_slot_url(
    tmp_path: Path, origin: str, slug: tuple[str, str] | None
) -> None:
    assert _setup_url(tmp_path, origin) == _expected(slug)


@pytest.mark.parametrize(("origin", "slug"), PARITY_TABLE.values(), ids=PARITY_TABLE.keys())
def test_the_self_updater_derives_the_expected_slot_url(
    origin: str, slug: tuple[str, str] | None
) -> None:
    assert _updater_url(origin) == _expected(slug)


def test_the_grammar_lines_are_byte_identical_in_setup_and_the_renderer() -> None:
    renderer = _grammar_lines(_RENDERER)
    assert [line.split("=")[0] + "=" for line in renderer] == list(_GRAMMAR_PREFIXES)
    assert renderer == _grammar_lines(_SETUP)


def test_the_origin_parser_is_byte_identical_in_setup_and_the_renderer() -> None:
    assert _function_block(_RENDERER, "parse_origin") == _function_block(_SETUP, "parse_origin")
