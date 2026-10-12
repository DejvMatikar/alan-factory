from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan import operator_checkout, operator_repos
from alan.engine_config import RepoIdentity
from alan.operator_checkout import CheckoutCloneFailed
from alan.operator_repos import RepoTarget
from tests.conftest import FakeGit, git_reply, register_test_engine

_ORIGIN = "https://example.test/install-owner/install-repo.git"
_INSTALL = RepoIdentity(owner="install-owner", repo="install-repo")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "operator-install-operator-repos"


@pytest.fixture
def install_checkout(root: Path) -> Path:
    return root / "install-owner" / "install-repo"


def _seed(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


def test_the_operator_root_is_a_sibling_of_the_install_named_after_it(
    tmp_path: Path, root: Path
) -> None:
    assert operator_repos.operator_root() == root
    assert root.parent == operator_checkout.install_root().parent == tmp_path
    assert operator_repos.operator_root() != operator_checkout.install_root()
    assert operator_repos.operator_root() != operator_checkout.install_root().parent


def test_the_real_install_never_yields_a_root_inside_itself_or_its_parent() -> None:
    real_install = Path(operator_checkout.__file__).resolve().parent.parent
    real_root = real_install.parent / f"{real_install.name}{operator_repos.ROOT_SUFFIX}"
    assert not real_root.is_relative_to(real_install)
    assert real_root != real_install.parent


@pytest.mark.parametrize(
    ("root_path", "install", "unsafe"),
    [
        ("/a/b-operator-repos", "/a/b", False),
        ("/a/b", "/a/b", True),
        ("/a/b/c", "/a/b", True),
        ("/a/b", "/a/b/c", True),
        ("/a/b/c/d", "/a/b/c", True),
        ("/a/x", "/a/b/c", False),
    ],
    ids=[
        "sibling",
        "the_install",
        "inside_the_install",
        "the_installs_parent",
        "deep_inside_the_install",
        "unrelated",
    ],
)
def test_a_root_that_is_the_install_inside_it_or_its_parent_is_unsafe(
    root_path: str, install: str, unsafe: bool
) -> None:
    assert operator_repos._is_unsafe_root(Path(root_path), Path(install)) is unsafe  # noqa: SLF001


def test_a_root_under_the_alan_state_tree_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_git: FakeGit
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        operator_checkout, "install_root", lambda: home / ".alan" / "slots" / "slot_3"
    )
    with pytest.raises(CheckoutCloneFailed, match="cannot hold the operator checkouts"):
        operator_repos.operator_root()
    with pytest.raises(CheckoutCloneFailed, match="cannot hold the operator checkouts"):
        operator_repos.ensure_all()
    assert fake_git.calls == []


def test_a_symlinked_home_is_compared_by_its_real_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    linked_home = tmp_path / "linked-home"
    linked_home.symlink_to(real_home)
    monkeypatch.setenv("HOME", str(linked_home))
    monkeypatch.setattr(
        operator_checkout, "install_root", lambda: real_home / ".alan" / "slots" / "slot_3"
    )
    with pytest.raises(CheckoutCloneFailed, match="cannot hold the operator checkouts"):
        operator_repos.operator_root()


def test_a_root_symlinked_into_the_alan_state_tree_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_git: FakeGit, root: Path
) -> None:
    home = tmp_path / "home"
    state = home / ".alan" / "state"
    state.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    root.symlink_to(state)
    with pytest.raises(CheckoutCloneFailed, match="cannot hold the operator checkouts"):
        operator_repos.operator_root()
    with pytest.raises(CheckoutCloneFailed, match="cannot hold the operator checkouts"):
        operator_repos.ensure_all()
    assert fake_git.calls == []
    assert list(state.iterdir()) == []


@pytest.mark.parametrize(
    "destination",
    ["operator-install", "operator-install/inner", "."],
    ids=["the_install", "inside_the_install", "the_installs_parent"],
)
def test_a_root_symlinked_onto_the_install_into_it_or_its_parent_is_refused(
    tmp_path: Path, fake_git: FakeGit, root: Path, destination: str
) -> None:
    target = tmp_path / destination
    target.mkdir(parents=True, exist_ok=True)
    root.symlink_to(target)
    with pytest.raises(CheckoutCloneFailed, match="cannot hold the operator checkouts"):
        operator_repos.operator_root()
    assert fake_git.calls == []


def test_a_root_symlinked_to_an_unrelated_folder_is_accepted(
    tmp_path: Path, root: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root.symlink_to(elsewhere)
    assert operator_repos.operator_root() == root


def test_an_owner_folder_symlinked_into_the_alan_state_tree_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_git: FakeGit, root: Path
) -> None:
    home = tmp_path / "home"
    state = home / ".alan" / "state"
    state.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    root.mkdir()
    (root / "install-owner").symlink_to(state)
    with pytest.raises(CheckoutCloneFailed, match="cannot hold the operator checkouts"):
        operator_repos.repo_path(_INSTALL)
    with pytest.raises(CheckoutCloneFailed, match="cannot hold the operator checkouts"):
        operator_repos.ensure_repo(RepoTarget(_INSTALL, _ORIGIN, True))
    assert fake_git.calls == []
    assert list(state.iterdir()) == []


def test_the_filesystem_root_as_install_has_no_safe_operator_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operator_checkout, "install_root", lambda: Path("/"))
    with pytest.raises(CheckoutCloneFailed, match="cannot hold the operator checkouts"):
        operator_repos.operator_root()


def test_a_repository_path_is_owner_then_repo_under_the_root(root: Path) -> None:
    identity = RepoIdentity(owner="Some-Owner", repo="repo.name_2")
    assert operator_repos.repo_path(identity) == root / "Some-Owner" / "repo.name_2"


def test_a_hundred_character_component_is_accepted(root: Path) -> None:
    identity = RepoIdentity(owner="o" * 100, repo="r" * 100)
    assert operator_repos.repo_path(identity) == root / ("o" * 100) / ("r" * 100)


@pytest.mark.parametrize(
    ("owner", "repo"),
    [
        ("..", "r"),
        ("o", ".."),
        (".hidden", "r"),
        ("o", "-leading"),
        ("o/x", "r"),
        ("o", "r/../x"),
        ("o w", "r"),
        ("o", "r;rm"),
        ("o" * 101, "r"),
        ("o", "r" * 101),
        ("ünï", "r"),
        ("o", "r\n"),
    ],
)
def test_a_component_outside_the_safe_pattern_never_becomes_a_path(
    owner: str, repo: str, fake_git: FakeGit
) -> None:
    with pytest.raises(CheckoutCloneFailed, match="not a safe directory name"):
        operator_repos.repo_path(RepoIdentity(owner=owner, repo=repo))
    unsafe = RepoIdentity(owner=owner, repo=repo)
    with pytest.raises(CheckoutCloneFailed, match="not a safe directory name"):
        operator_repos.ensure_repo(RepoTarget(unsafe, clone_url=_ORIGIN, migrates_legacy=True))
    assert fake_git.calls == []


@pytest.mark.parametrize(
    ("origin", "owner", "repo"),
    [
        ("https://github.com/DejvMatikar/alan.git", "DejvMatikar", "alan"),
        ("https://github.com/o/r", "o", "r"),
        ("https://github.com/o/r.git/", "o", "r"),
        ("git@github.com:Owner/Repo.git", "Owner", "Repo"),
        ("ssh://git@host.test/o/r.git", "o", "r"),
        ("https://user:pw@host.test/o/r.git", "o", "r"),
        ("https://host.test/group/sub/r.git", "sub", "r"),
        ("https://host.test/o/repo.github.io", "o", "repo.github.io"),
        ("/srv/git/some-repo.git", "git", "some-repo"),
    ],
)
def test_the_install_identity_is_parsed_from_the_origin_url(
    fake_git: FakeGit, origin: str, owner: str, repo: str
) -> None:
    fake_git.origin = origin
    target = operator_repos.install_target()
    assert target == RepoTarget(RepoIdentity(owner=owner, repo=repo), origin, True)


@pytest.mark.parametrize("origin", ["nonsense", "https://host.test", "o/"])
def test_an_origin_that_names_no_owner_and_repository_is_a_clone_failure(
    fake_git: FakeGit, origin: str
) -> None:
    fake_git.origin = origin
    with pytest.raises(CheckoutCloneFailed, match="does not name an owner and a repository"):
        operator_repos.install_target()


def test_an_install_without_an_origin_is_a_clone_failure(fake_git: FakeGit) -> None:
    fake_git.fail("config", "remote.origin.url", git_reply(stderr="no such key"))
    with pytest.raises(CheckoutCloneFailed, match="no origin remote"):
        operator_repos.install_target()


def test_the_install_identity_adopts_the_casing_of_a_registered_engine(
    fake_git: FakeGit,
) -> None:
    registered = register_test_engine("Install-Owner/INSTALL-repo")
    fake_git.origin = "https://example.test/install-owner/install-repo.git"
    assert operator_repos.install_target().identity == registered


def test_an_unrelated_registered_engine_does_not_change_the_install_identity(
    fake_git: FakeGit,
) -> None:
    del fake_git
    register_test_engine("acme/widget")
    assert operator_repos.install_target().identity == _INSTALL


def test_the_optional_install_target_is_none_when_the_origin_cannot_be_read(
    fake_git: FakeGit,
) -> None:
    assert operator_repos.optional_install_target() == operator_repos.install_target()
    fake_git.fail("config", "remote.origin.url", git_reply(stderr="no such key"))
    assert operator_repos.optional_install_target() is None


def test_the_installs_own_repository_is_cloned_from_its_origin_and_migrates_the_legacy_folder(
    fake_git: FakeGit,
) -> None:
    del fake_git
    target = operator_repos.target_for(_INSTALL, operator_repos.install_target())
    assert target == RepoTarget(_INSTALL, _ORIGIN, True)


def test_the_installs_repository_matches_case_insensitively(fake_git: FakeGit) -> None:
    del fake_git
    install = operator_repos.install_target()
    shouted = RepoIdentity(owner="INSTALL-OWNER", repo="Install-Repo")
    assert operator_repos.target_for(shouted, install) is install


def test_another_repository_is_cloned_from_github_and_never_migrates() -> None:
    target = operator_repos.target_for(RepoIdentity(owner="acme", repo="widget"), None)
    assert target == RepoTarget(
        RepoIdentity(owner="acme", repo="widget"), "https://github.com/acme/widget.git", False
    )


def test_another_repository_beside_a_known_install_is_still_a_github_clone(
    fake_git: FakeGit,
) -> None:
    del fake_git
    target = operator_repos.target_for(
        RepoIdentity(owner="acme", repo="widget"), operator_repos.install_target()
    )
    assert target.clone_url == "https://github.com/acme/widget.git"
    assert target.migrates_legacy is False


def test_no_warnings_combine_to_none() -> None:
    assert operator_repos.combine_warnings([]) is None
    assert operator_repos.combine_warnings([None, ""]) is None


def test_warnings_are_joined_in_order_skipping_empty_ones() -> None:
    assert operator_repos.combine_warnings(["a: one", None, "b: two"]) == "a: one; b: two"


def test_five_warnings_are_all_kept() -> None:
    warnings = [f"w{i}" for i in range(operator_repos.MAX_WARNINGS)]
    assert operator_repos.combine_warnings(warnings) == "; ".join(warnings)


def test_more_than_five_warnings_keep_five_and_count_the_rest() -> None:
    warnings = [f"w{i}" for i in range(8)]
    assert operator_repos.combine_warnings(warnings) == "w0; w1; w2; w3; w4; and 3 more"


def test_a_prefixed_warning_names_the_repository_and_hides_credentials() -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    warning = operator_repos.prefixed(identity, "failed https://u:pw@host.test/x.git")
    assert warning == "acme/widget: failed https://***@host.test/x.git"


def test_an_absent_warning_stays_absent_when_prefixed() -> None:
    identity = RepoIdentity(owner="acme", repo="widget")
    assert operator_repos.prefixed(identity, None) is None
    assert operator_repos.prefixed(identity, "") is None


def test_first_use_creates_the_parent_and_clones_into_it(
    fake_git: FakeGit, root: Path, install_checkout: Path
) -> None:
    target = operator_repos.install_target()
    result = operator_repos.ensure_repo(target)
    assert result.path == install_checkout
    assert result.warning is None
    assert (install_checkout / ".git").is_dir()
    clone_args, clone_cwd, _ = fake_git.calls_of("clone")[0]
    assert clone_args == ["clone", "--", _ORIGIN, str(install_checkout)]
    assert clone_cwd == root / "install-owner"
    assert fake_git.verbs() == ["config", "clone"]


def test_a_later_use_fetches_then_fast_forwards_and_never_resets_or_clones(
    fake_git: FakeGit, install_checkout: Path
) -> None:
    _seed(install_checkout)
    (install_checkout / "local-work.txt").write_text("keep me")
    result = operator_repos.ensure_repo(RepoTarget(_INSTALL, _ORIGIN, False))
    assert result.path == install_checkout
    assert result.warning is None
    assert fake_git.verbs() == ["fetch", "merge"]
    assert fake_git.calls[0][0] == ["fetch", "--quiet"]
    assert fake_git.calls[1][0] == ["merge", "--ff-only", "--quiet", "@{upstream}"]
    assert {cwd for _args, cwd, _t in fake_git.calls} == {install_checkout}
    assert (install_checkout / "local-work.txt").read_text() == "keep me"


def test_a_failed_update_is_a_warning_and_the_checkout_is_still_returned(
    fake_git: FakeGit, install_checkout: Path
) -> None:
    _seed(install_checkout)
    fake_git.fail(
        "fetch", "install-repo", git_reply(stderr="fatal: could not read https://t:k@host.test/r")
    )
    result = operator_repos.ensure_repo(RepoTarget(_INSTALL, _ORIGIN, False))
    assert result.path == install_checkout
    assert result.warning is not None
    assert "fetch failed" in result.warning
    assert "t:k" not in result.warning
    assert fake_git.verbs() == ["fetch"]


def test_a_failed_fresh_clone_removes_its_partial_directory_and_reports_the_failure(
    fake_git: FakeGit, install_checkout: Path
) -> None:
    fake_git.fail(
        "clone", "install-repo", git_reply(returncode=128, stderr="fatal: early EOF"), partial=True
    )
    with pytest.raises(CheckoutCloneFailed, match="clone failed: fatal: early EOF"):
        operator_repos.ensure_repo(RepoTarget(_INSTALL, _ORIGIN, False))
    assert not install_checkout.exists()


def test_a_timed_out_fresh_clone_removes_its_partial_directory(
    fake_git: FakeGit, install_checkout: Path
) -> None:
    fake_git.fail(
        "clone", "install-repo", subprocess.TimeoutExpired(["git", "clone"], 1), partial=True
    )
    with pytest.raises(CheckoutCloneFailed, match="timed out"):
        operator_repos.ensure_repo(RepoTarget(_INSTALL, _ORIGIN, False))
    assert not install_checkout.exists()


def test_a_failed_clone_leaves_a_directory_that_was_there_before_alone(
    fake_git: FakeGit, install_checkout: Path
) -> None:
    install_checkout.mkdir(parents=True)
    (install_checkout / "keep.txt").write_text("not a checkout")
    fake_git.fail("clone", "install-repo", git_reply(returncode=128, stderr="fatal: not empty"))
    with pytest.raises(CheckoutCloneFailed):
        operator_repos.ensure_repo(RepoTarget(_INSTALL, _ORIGIN, False))
    assert (install_checkout / "keep.txt").read_text() == "not a checkout"


def test_an_origin_embedding_credentials_is_refused_before_any_clone(
    fake_git: FakeGit, install_checkout: Path
) -> None:
    target = RepoTarget(_INSTALL, "https://user:s3cret@example.test/o/r.git", True)
    with pytest.raises(CheckoutCloneFailed, match="embeds credentials") as excinfo:
        operator_repos.ensure_repo(target)
    assert "s3cret" not in str(excinfo.value)
    assert fake_git.calls == []
    assert not install_checkout.exists()


def test_a_parent_that_cannot_be_created_is_a_clone_failure(
    fake_git: FakeGit, root: Path
) -> None:
    root.mkdir()
    (root / "install-owner").write_text("a file where the owner folder should be")
    with pytest.raises(CheckoutCloneFailed, match="cannot create"):
        operator_repos.ensure_repo(RepoTarget(_INSTALL, _ORIGIN, False))
    assert fake_git.calls == []


def test_ensure_all_with_no_other_repository_holds_only_the_install_checkout(
    fake_git: FakeGit, root: Path, install_checkout: Path
) -> None:
    result = operator_repos.ensure_all()
    assert result.path == root
    assert result.warning is None
    assert [path.name for path in root.iterdir()] == ["install-owner"]
    assert [path.name for path in (root / "install-owner").iterdir()] == ["install-repo"]
    assert (install_checkout / ".git").is_dir()
    assert fake_git.verbs() == ["config", "clone"]


def test_ensure_all_holds_a_checkout_of_every_connected_repository(
    fake_git: FakeGit, root: Path
) -> None:
    register_test_engine("acme/widget")
    register_test_engine("acme/gadget")
    register_test_engine("other/tool")
    result = operator_repos.ensure_all()
    assert result.warning is None
    assert sorted(path.name for path in root.iterdir()) == ["acme", "install-owner", "other"]
    assert sorted(path.name for path in (root / "acme").iterdir()) == ["gadget", "widget"]
    urls = [args[2] for args, _cwd, _t in fake_git.calls_of("clone")]
    assert urls == [
        _ORIGIN,
        "https://github.com/acme/widget.git",
        "https://github.com/acme/gadget.git",
        "https://github.com/other/tool.git",
    ]


def test_ensure_all_skips_hidden_repositories(fake_git: FakeGit, root: Path) -> None:
    register_test_engine("acme/widget")
    register_test_engine("acme/secret", hidden=True)
    operator_repos.ensure_all()
    assert not (root / "acme" / "secret").exists()
    assert [args[2] for args, _c, _t in fake_git.calls_of("clone")].count(
        "https://github.com/acme/secret.git"
    ) == 0


def test_ensure_all_does_not_clone_the_install_twice_when_it_is_registered(
    fake_git: FakeGit, root: Path
) -> None:
    register_test_engine("Install-Owner/install-repo")
    operator_repos.ensure_all()
    assert len(fake_git.calls_of("clone")) == 1
    assert [path.name for path in root.iterdir()] == ["Install-Owner"]


def test_ensure_all_keeps_one_checkout_for_repositories_that_differ_only_in_case(
    fake_git: FakeGit,
) -> None:
    register_test_engine("acme/widget")
    register_test_engine("ACME/Widget")
    operator_repos.ensure_all()
    assert len(fake_git.calls_of("clone")) == 2


def test_a_failed_install_clone_is_a_failure_not_a_warning(fake_git: FakeGit) -> None:
    register_test_engine("acme/widget")
    fake_git.fail("clone", "install-repo", git_reply(returncode=128, stderr="fatal: offline"))
    with pytest.raises(CheckoutCloneFailed, match="clone failed: fatal: offline"):
        operator_repos.ensure_all()
    assert len(fake_git.calls_of("clone")) == 1


def test_another_repositorys_clone_failure_is_a_prefixed_redacted_warning(
    fake_git: FakeGit, root: Path
) -> None:
    register_test_engine("acme/widget")
    register_test_engine("acme/gadget")
    fake_git.fail(
        "clone",
        "acme/widget",
        git_reply(returncode=128, stderr="fatal: https://user:s3cret@github.com/acme/widget"),
    )
    result = operator_repos.ensure_all()
    assert result.warning is not None
    assert result.warning.startswith("acme/widget: clone failed: ")
    assert "s3cret" not in result.warning
    assert "gadget" not in result.warning
    assert (root / "acme" / "gadget" / ".git").is_dir()
    assert (root / "install-owner" / "install-repo" / ".git").is_dir()
    assert not (root / "acme" / "widget").exists()


def test_every_update_failure_is_a_prefixed_warning_including_the_installs(
    fake_git: FakeGit, root: Path
) -> None:
    register_test_engine("acme/widget")
    _seed(root / "install-owner" / "install-repo")
    _seed(root / "acme" / "widget")
    fake_git.fail("fetch", "install-repo", git_reply(stderr="boom one"))
    fake_git.fail("fetch", "widget", git_reply(stderr="boom two"))
    result = operator_repos.ensure_all()
    assert result.warning is not None
    parts = result.warning.split("; ")
    assert parts[0].startswith("install-owner/install-repo: checkout not updated (fetch failed)")
    assert parts[1].startswith("acme/widget: checkout not updated (fetch failed)")
    assert "boom one" in parts[0]
    assert "boom two" in parts[1]


def test_warnings_are_capped_at_five_entries_plus_a_count(fake_git: FakeGit) -> None:
    for index in range(7):
        register_test_engine(f"acme/repo{index}")
        fake_git.fail("clone", f"acme/repo{index}", git_reply(returncode=1, stderr="nope"))
    result = operator_repos.ensure_all()
    assert result.warning is not None
    parts = result.warning.split("; ")
    assert len(parts) == 6
    assert parts[:5] == [f"acme/repo{index}: clone failed: nope" for index in range(5)]
    assert parts[5] == "and 2 more"


def test_a_registered_repository_with_an_unsafe_name_is_a_warning_not_a_failure(
    fake_git: FakeGit,
) -> None:
    register_test_engine("bad owner/widget")
    result = operator_repos.ensure_all()
    assert result.warning == "bad owner/widget: 'bad owner/widget' is not a safe directory name"
    assert len(fake_git.calls_of("clone")) == 1


def test_a_timed_out_other_clone_is_a_warning(fake_git: FakeGit) -> None:
    register_test_engine("acme/widget")
    fake_git.fail("clone", "acme/widget", subprocess.TimeoutExpired(["git", "clone"], 1))
    result = operator_repos.ensure_all()
    assert result.warning == "acme/widget: clone failed: timed out"
