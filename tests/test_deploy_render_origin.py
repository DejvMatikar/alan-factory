from __future__ import annotations

from pathlib import Path

import pytest

from tests._deploy_render_support import (
    EXAMPLE_ORIGIN,
    EXAMPLE_SLOT_URL,
    FIX_HINT,
    REFUSED_EXIT,
    SLOT_URL_KEY,
    plist_string_value,
    render_scratch,
    rendered_target,
    run_render,
    scratch_checkout,
    scratch_home,
)

_LEAK_PROBE = "s3cr3t-token"
_RENDERER = Path(__file__).resolve().parent.parent / "deploy" / "render_plist.sh"
_SENTINEL = b"sentinel-target-bytes\n"

_GITHUB_ORIGIN_FORMS = {
    "https": "https://github.com/example-owner/example-repo",
    "https-dot-git": "https://github.com/example-owner/example-repo.git",
    "scp": "git@github.com:example-owner/example-repo",
    "scp-dot-git": "git@github.com:example-owner/example-repo.git",
    "ssh-url": "ssh://git@github.com/example-owner/example-repo",
    "ssh-url-dot-git": "ssh://git@github.com/example-owner/example-repo.git",
}

_CREDENTIAL_ORIGINS = {
    "https-user-password": f"https://user:{_LEAK_PROBE}@github.com/example-owner/example-repo.git",
    "https-token-only": f"https://{_LEAK_PROBE}@github.com/example-owner/example-repo",
    "https-token-with-at": (
        f"https://user:{_LEAK_PROBE}@extra@github.com/example-owner/example-repo.git"
    ),
    "scp": f"{_LEAK_PROBE}@github.com:example-owner/example-repo.git",
    "ssh-url": f"ssh://user:{_LEAK_PROBE}@github.com/example-owner/example-repo.git",
}

_UNUSABLE_ORIGINS = {
    "non-github-host": "https://gitlab.example/x/y.git",
    "uppercase-host": "https://GitHub.com/example-owner/example-repo.git",
    "ssh-url-with-port": "ssh://git@github.com:22/example-owner/example-repo.git",
    "trailing-slash": "https://github.com/example-owner/example-repo/",
    "plain-http": "http://github.com/example-owner/example-repo.git",
    "space-in-owner": "https://github.com/example owner/example-repo.git",
    "non-ascii-owner": "https://github.com/exämple-owner/example-repo.git",
    "dot-dot-owner": "https://github.com/../example-repo.git",
    "dot-repo": "https://github.com/example-owner/.",
    "empty-repo-after-suffix": "https://github.com/example-owner/.git",
    "sed-metacharacter-owner": "https://github.com/example&owner/example-repo.git",
    "owner-with-leading-hyphen": "https://github.com/-example-owner/example-repo.git",
}

_UNUSABLE_CREDENTIAL_ORIGINS = {
    "gitlab-with-password": f"https://user:{_LEAK_PROBE}@gitlab.example/x/y.git",
    "github-with-port": f"https://user:{_LEAK_PROBE}@github.com:443/example-owner/example-repo.git",
    "github-with-trailing-slash": (
        f"https://user:{_LEAK_PROBE}@github.com/example-owner/example-repo/"
    ),
}


@pytest.mark.parametrize("origin", _GITHUB_ORIGIN_FORMS.values(), ids=_GITHUB_ORIGIN_FORMS.keys())
def test_github_origin_forms_render_normalized_url(tmp_path: Path, origin: str) -> None:
    rendered = render_scratch(tmp_path, origin)
    assert rendered.result.returncode == 0, rendered.result.stderr
    assert plist_string_value(rendered.content, SLOT_URL_KEY) == EXAMPLE_SLOT_URL
    assert rendered.result.stderr == ""


@pytest.mark.parametrize("origin", _CREDENTIAL_ORIGINS.values(), ids=_CREDENTIAL_ORIGINS.keys())
def test_origin_credentials_never_reach_any_output(tmp_path: Path, origin: str) -> None:
    rendered = render_scratch(tmp_path, origin)
    assert rendered.result.returncode == 0, rendered.result.stderr
    content = rendered.content
    for output in (content, rendered.result.stdout, rendered.result.stderr):
        assert _LEAK_PROBE not in output
        assert "user:" not in output
    assert plist_string_value(content, SLOT_URL_KEY) == EXAMPLE_SLOT_URL


def _assert_refused(result_stderr: str, result_stdout: str, origin: str | None) -> None:
    assert result_stdout == ""
    assert FIX_HINT in result_stderr
    assert "origin is not a GitHub repository" in result_stderr
    assert len(result_stderr.splitlines()) == 1
    if origin is not None:
        assert origin not in result_stderr


@pytest.mark.parametrize("origin", _UNUSABLE_ORIGINS.values(), ids=_UNUSABLE_ORIGINS.keys())
def test_unusable_origin_is_refused_with_a_named_fix_and_nothing_is_written(
    tmp_path: Path, origin: str
) -> None:
    rendered = render_scratch(tmp_path, origin)
    result = rendered.result
    assert result.returncode == REFUSED_EXIT, result.stderr
    _assert_refused(result.stderr, result.stdout, origin)
    assert list(rendered.home.iterdir()) == []


@pytest.mark.parametrize(
    "origin", _UNUSABLE_CREDENTIAL_ORIGINS.values(), ids=_UNUSABLE_CREDENTIAL_ORIGINS.keys()
)
def test_a_refused_origin_never_echoes_its_credential(tmp_path: Path, origin: str) -> None:
    rendered = render_scratch(tmp_path, origin)
    result = rendered.result
    assert result.returncode == REFUSED_EXIT, result.stderr
    for output in (result.stdout, result.stderr):
        assert _LEAK_PROBE not in output
        assert "user:" not in output
    assert list(rendered.home.iterdir()) == []


def test_repository_without_origin_is_refused(tmp_path: Path) -> None:
    rendered = render_scratch(tmp_path, None)
    assert rendered.result.returncode == REFUSED_EXIT, rendered.result.stderr
    _assert_refused(rendered.result.stderr, rendered.result.stdout, None)
    assert list(rendered.home.iterdir()) == []


def test_non_git_directory_is_refused(tmp_path: Path) -> None:
    rendered = render_scratch(tmp_path, None, git=False)
    assert rendered.result.returncode == REFUSED_EXIT, rendered.result.stderr
    assert not (rendered.repo / ".git").exists()
    _assert_refused(rendered.result.stderr, rendered.result.stdout, None)
    assert list(rendered.home.iterdir()) == []


@pytest.mark.parametrize("target_kind", ["file", "symlink"])
def test_a_refusal_leaves_the_existing_launch_configuration_byte_identical(
    tmp_path: Path, target_kind: str
) -> None:
    repo = scratch_checkout(tmp_path, "https://gitlab.example/x/y.git")
    home = scratch_home(tmp_path)
    target = rendered_target(home)
    target.parent.mkdir(parents=True)
    kept = tmp_path / "kept.plist"
    kept.write_bytes(_SENTINEL)
    if target_kind == "file":
        target.write_bytes(_SENTINEL)
    else:
        target.symlink_to(kept)

    result = run_render(repo, home)

    assert result.returncode == REFUSED_EXIT, result.stderr
    assert target.is_symlink() is (target_kind == "symlink")
    assert target.read_bytes() == _SENTINEL
    assert kept.read_bytes() == _SENTINEL
    assert [path.name for path in target.parent.iterdir()] == [target.name]


def test_the_renderer_no_longer_carries_any_default_slot_repository() -> None:
    source = _RENDERER.read_text(encoding="utf-8")
    assert "FALLBACK" not in source
    assert "DejvMatikar" not in source
    assert "default slot clone source" not in source


def test_origin_is_read_from_script_checkout_not_caller(tmp_path: Path) -> None:
    other = scratch_checkout(
        tmp_path, "https://github.com/other-owner/other-repo.git", name="other"
    )
    repo = scratch_checkout(tmp_path, EXAMPLE_ORIGIN)
    home = scratch_home(tmp_path)

    result = run_render(repo, home, cwd=other, extra_env={"GIT_DIR": str(other / ".git")})

    assert result.returncode == 0, result.stderr
    content = rendered_target(home).read_text(encoding="utf-8")
    assert plist_string_value(content, SLOT_URL_KEY) == EXAMPLE_SLOT_URL
    assert "other-owner" not in content
