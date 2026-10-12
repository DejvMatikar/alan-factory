from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import operator_checkout, operator_repos
from alan.teams._forge_errors import classify_forge_failure
from tests.gh_guard import (
    AMBIENT_GH_CREDENTIAL_ENV,
    GH_STUB_RETURNCODE,
    strip_ambient_gh_credentials,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

_VERIFIER_ENV_VAR = "ALAN_VERIFIER_GH_TOKEN"
_GH_ENV_VAR = "GH_TOKEN"
_VERIFIER_SENTINEL = "ghp_seeded_by_safeguard_test"
_GH_SENTINEL = "ghp_gh_token_sentinel"


@pytest.fixture(autouse=True, scope="module")
def _seed_ambient_tokens() -> Iterator[None]:
    prior_verifier = os.environ.get(_VERIFIER_ENV_VAR)
    prior_gh = os.environ.get(_GH_ENV_VAR)
    os.environ[_VERIFIER_ENV_VAR] = _VERIFIER_SENTINEL
    os.environ[_GH_ENV_VAR] = _GH_SENTINEL
    yield
    if prior_verifier is None:
        os.environ.pop(_VERIFIER_ENV_VAR, None)
    else:
        os.environ[_VERIFIER_ENV_VAR] = prior_verifier
    if prior_gh is None:
        os.environ.pop(_GH_ENV_VAR, None)
    else:
        os.environ[_GH_ENV_VAR] = prior_gh


def test_verifier_token_stripped_by_default() -> None:
    assert _VERIFIER_ENV_VAR not in os.environ


def test_unrelated_gh_token_not_stripped() -> None:
    assert os.environ.get(_GH_ENV_VAR) == _GH_SENTINEL


def test_test_can_set_verifier_token_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_VERIFIER_ENV_VAR, "ghp_explicit_in_test")
    assert os.environ.get(_VERIFIER_ENV_VAR) == "ghp_explicit_in_test"


def test_stub_gh_dir_is_first_on_path() -> None:
    stub_dir = _REPO_ROOT / "tests" / "bin"
    assert os.environ["PATH"].split(os.pathsep)[0] == str(stub_dir)
    assert shutil.which("gh") == str(stub_dir / "gh")


def test_stub_gh_script_is_executable() -> None:
    assert os.access(_REPO_ROOT / "tests" / "bin" / "gh", os.X_OK)


def test_stub_gh_script_refuses_with_the_stub_exit_code() -> None:
    result = subprocess.run(
        ["/bin/sh", str(_REPO_ROOT / "tests" / "bin" / "gh"), "pr", "list"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == GH_STUB_RETURNCODE
    assert result.stdout == ""
    assert "tests/bin/gh" in result.stderr
    assert classify_forge_failure(returncode=result.returncode, stderr=result.stderr) == "unknown"


def test_gh_config_dir_is_an_empty_directory_outside_home_config() -> None:
    config_dir = Path(os.environ["GH_CONFIG_DIR"])
    assert config_dir.is_dir()
    assert not any(config_dir.iterdir())
    assert not config_dir.is_relative_to(Path.home() / ".config")


def test_operator_install_root_is_redirected_into_the_test_tmp_dir(tmp_path: Path) -> None:
    redirected = operator_checkout.install_root()
    assert redirected.is_relative_to(tmp_path)
    assert redirected != _REPO_ROOT
    assert operator_checkout.legacy_checkout_path().parent == tmp_path


def test_operator_repos_root_and_legacy_checkout_never_leave_the_test_tmp_dir(
    tmp_path: Path,
) -> None:
    root = operator_repos.operator_root()
    real_parent = Path(operator_checkout.__file__).resolve().parent.parent.parent
    assert root.is_relative_to(tmp_path)
    assert operator_checkout.legacy_checkout_path().is_relative_to(tmp_path)
    assert not root.is_relative_to(real_parent / _REPO_ROOT.name)
    assert not root.is_relative_to(Path.home() / ".alan")


def test_operator_git_is_refused_by_default(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="operator checkout's real git runner"):
        operator_checkout.run_git(["status"], cwd=tmp_path, timeout=1.0)


def test_strip_ambient_gh_credentials_pops_only_the_named_vars() -> None:
    environ = {
        "GH_TOKEN": "a", "GITHUB_TOKEN": "b", "GH_ENTERPRISE_TOKEN": "c",
        "GITHUB_ENTERPRISE_TOKEN": "d", _VERIFIER_ENV_VAR: "e", "OTHER": "f",
    }
    popped = strip_ambient_gh_credentials(environ)
    assert popped == list(AMBIENT_GH_CREDENTIAL_ENV)
    assert environ == {_VERIFIER_ENV_VAR: "e", "OTHER": "f"}
    assert strip_ambient_gh_credentials(environ) == []
