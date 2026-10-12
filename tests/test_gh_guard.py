from __future__ import annotations

import pathlib
import subprocess
import threading

import pytest

from alan.engine_config import ForgeTarget, RepoIdentity
from alan.forge_gh import (
    _FEEDBACK_QUERY,  # pyright: ignore[reportPrivateUsage]
    _REVIEW_THREADS_QUERY,  # pyright: ignore[reportPrivateUsage]
    GhForge,
)
from alan.teams import _pr_branch
from alan.teams._forge_errors import classify_forge_failure
from tests.gh_guard import (
    GH_STUB_RETURNCODE,
    GH_STUB_STDERR,
    MUTATING_GH_SUBCOMMANDS,
    READ_ONLY_GH_SUBCOMMANDS,
    GhGuardBlocked,
    baseline_key,
    classify,
    drain_background_thread_blocks,
    peek_stubbed_reads,
    popen_refusal,
    refusal_message,
    stubbed_completed_process,
)

_RESOLVE_REVIEW_THREAD_MUTATION = (
    "mutation($threadId:ID!){resolveReviewThread(input:{threadId:$threadId})"
    "{thread{isResolved}}}"
)


def test_classify_permits_pr_list() -> None:
    decision = classify(["gh", "pr", "list", "--repo", "o/r", "--json", "number"])
    assert decision.permitted is True


def test_classify_permits_pr_view() -> None:
    decision = classify(["gh", "pr", "view", "42", "--repo", "o/r", "--json", "state"])
    assert decision.permitted is True


def test_classify_refuses_unenumerated_read_looking_verb() -> None:
    decision = classify(["gh", "repo", "view", "o/r"])
    assert decision.permitted is False


def test_classify_refuses_pr_update_branch_non_regression() -> None:
    decision = classify(["gh", "pr", "update-branch", "42"])
    assert decision.permitted is False
    assert decision.subcommand_pair == ("pr", "update-branch")


def test_classify_permits_review_threads_query_document() -> None:
    decision = classify([
        "gh", "api", "graphql",
        "-f", f"query={_REVIEW_THREADS_QUERY}",
        "-F", "owner=o", "-F", "name=r", "-F", "n=1",
    ])
    assert decision.permitted is True


def test_classify_permits_feedback_query_document() -> None:
    decision = classify([
        "gh", "api", "graphql",
        "-f", f"query={_FEEDBACK_QUERY}",
        "-F", "owner=o", "-F", "name=r", "-F", "n=1",
    ])
    assert decision.permitted is True


def test_classify_refuses_graphql_mutation_document() -> None:
    decision = classify([
        "gh", "api", "graphql",
        "-f", f"query={_RESOLVE_REVIEW_THREAD_MUTATION}",
        "-f", "threadId=PRTI_abc",
    ])
    assert decision.permitted is False


def test_classify_refuses_graphql_document_via_raw_field_carrier() -> None:
    decision = classify([
        "gh", "api", "graphql", "-F", "query=query{viewer{login}}",
    ])
    assert decision.permitted is False


def test_classify_refuses_graphql_document_without_query_field_carrier() -> None:
    decision = classify(["gh", "api", "graphql", "--input", "payload.json"])
    assert decision.permitted is False


def test_classify_refuses_graphql_anonymous_shorthand() -> None:
    decision = classify(["gh", "api", "graphql", "-f", "query={viewer{login}}"])
    assert decision.permitted is False


def test_classify_permits_explicit_get_method() -> None:
    decision = classify(["gh", "api", "repos/o/r/issues", "--method", "GET"])
    assert decision.permitted is True


@pytest.mark.parametrize("method", ["POST", "put", "PATCH", "delete"])
def test_classify_refuses_explicit_mutating_method(method: str) -> None:
    decision = classify(["gh", "api", "repos/o/r/issues", "-X", method])
    assert decision.permitted is False


def test_classify_refuses_implicit_write_field_flag_without_method() -> None:
    decision = classify(["gh", "api", "repos/o/r/issues", "-f", "title=hello"])
    assert decision.permitted is False


def test_classify_refuses_implicit_write_input_flag_without_method() -> None:
    decision = classify(["gh", "api", "repos/o/r/issues", "--input", "payload.json"])
    assert decision.permitted is False


def test_classify_permits_api_call_without_method_or_field() -> None:
    decision = classify(["gh", "api", "repos/o/r/commits/deadbeef/check-runs"])
    assert decision.permitted is True


def test_classify_refuses_when_global_flag_precedes_subcommand() -> None:
    decision = classify(["gh", "--repo", "o/r", "pr", "view", "1"])
    assert decision.permitted is False


def test_classify_detects_gh_when_argv0_is_bytes() -> None:
    decision = classify([b"gh", "issue", "create", "--title", "x"])
    assert decision.permitted is False
    assert decision.subcommand_pair == ("issue", "create")


def test_classify_detects_gh_when_argv0_is_pathlike() -> None:
    decision = classify([pathlib.PurePosixPath("/usr/local/bin/gh"), "issue", "create"])
    assert decision.permitted is False


def test_classify_refuses_rather_than_drops_bytes_method_flag() -> None:
    decision = classify(["gh", "api", "repos/o/r/issues", b"-X", b"POST"])
    assert decision.permitted is False


def test_classify_refuses_unrecognised_argv_element_type() -> None:
    decision = classify(["gh", "pr", "list", 42])
    assert decision.permitted is False


def test_mutating_tripwire_disjoint_from_read_only_allow_list() -> None:
    assert MUTATING_GH_SUBCOMMANDS.isdisjoint(READ_ONLY_GH_SUBCOMMANDS)


def test_blocked_call_inside_broad_except_still_fails() -> None:
    def _swallow_everything() -> str:
        try:
            subprocess.run(["gh", "gist", "list"], check=False)
        except Exception:  # noqa: BLE001
            return "swallowed"
        return "not reached"

    with pytest.raises(GhGuardBlocked):
        _swallow_everything()


def test_guard_wrapper_blocks_a_refused_read_verb_at_spawn() -> None:
    with pytest.raises(GhGuardBlocked):
        subprocess.run(["gh", "gist", "list"], check=False)


def test_refusal_message_names_pair_and_elides_flag_values() -> None:
    decision = classify([
        "gh", "issue", "create",
        "--title", "s3cr3t-title-value", "--body", "s3cr3t-body-value",
    ])
    message = refusal_message(decision)
    assert "issue create" in message
    assert "--title" in message
    assert "--body" in message
    assert "s3cr3t-title-value" not in message
    assert "s3cr3t-body-value" not in message


def test_refusal_message_warns_about_global_flag_parse_shift() -> None:
    decision = classify(["gh", "--repo", "o/r", "pr", "view", "1"])
    message = refusal_message(decision)
    assert "global flag" in message


def test_classify_marks_gh_invocations_and_only_those() -> None:
    assert classify(["gh", "pr", "list"]).is_gh is True
    assert classify(["true"]).is_gh is False
    assert classify("gh 'unterminated").is_gh is True
    assert classify("gh 'unterminated").permitted is False
    assert classify("ls 'unterminated").is_gh is False
    assert classify([]).is_gh is False


@pytest.mark.usefixtures("expect_gh_read_stub")
def test_permitted_read_returns_stub_without_spawning(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sentinel = tmp_path / "spawned"
    script = bin_dir / "gh"
    script.write_text(f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    result = subprocess.run(
        ["gh", "pr", "list", "--json", "number"], capture_output=True, text=True, check=False,
    )
    assert result.returncode == GH_STUB_RETURNCODE
    assert result.stdout == ""
    assert result.stderr == GH_STUB_STDERR
    assert not sentinel.exists()


@pytest.mark.usefixtures("expect_gh_read_stub")
def test_permitted_read_is_attributed_to_the_current_test(
    request: pytest.FixtureRequest,
) -> None:
    subprocess.run(["gh", "pr", "view", "1"], capture_output=True, text=True, check=False)
    reads = peek_stubbed_reads()
    assert len(reads) == 1
    assert reads[0].test_key == baseline_key(request.node.nodeid)
    assert reads[0].thread_name == "MainThread"
    assert reads[0].decision.subcommand_pair == ("pr", "view")


@pytest.mark.usefixtures("expect_gh_read_stub")
def test_background_thread_read_is_stubbed_not_raised() -> None:
    outcome: list[object] = []

    def _call() -> None:
        try:
            outcome.append(
                subprocess.run(["gh", "pr", "diff", "1"], capture_output=True, check=False)
            )
        except BaseException as exc:  # noqa: BLE001
            outcome.append(exc)

    worker = threading.Thread(target=_call, name="gh-read-probe")
    worker.start()
    worker.join()
    assert isinstance(outcome[0], subprocess.CompletedProcess)
    assert outcome[0].returncode == GH_STUB_RETURNCODE
    assert outcome[0].stdout == b""
    assert peek_stubbed_reads()[0].thread_name == "gh-read-probe"
    assert drain_background_thread_blocks() == []


def test_stubbed_completed_process_is_bytes_without_text_kwargs() -> None:
    result = stubbed_completed_process(["gh", "pr", "list"], {"capture_output": True})
    assert result.returncode == GH_STUB_RETURNCODE
    assert result.stdout == b""
    assert result.stderr == GH_STUB_STDERR.encode()


def test_stubbed_completed_process_is_text_with_encoding_kwarg() -> None:
    result = stubbed_completed_process(["gh", "pr", "list"], {"encoding": "utf-8"})
    assert result.stdout == ""
    assert result.stderr == GH_STUB_STDERR


def test_stubbed_completed_process_raises_called_process_error_when_check_requested() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        stubbed_completed_process(["gh", "pr", "list"], {"check": True, "text": True})
    assert excinfo.value.returncode == GH_STUB_RETURNCODE
    assert excinfo.value.stderr == GH_STUB_STDERR


def test_stub_stderr_classifies_as_unknown_forge_failure() -> None:
    assert classify_forge_failure(returncode=GH_STUB_RETURNCODE, stderr=GH_STUB_STDERR) == "unknown"


@pytest.mark.usefixtures("expect_gh_read_stub")
def test_hot_wrappers_treat_the_stub_as_gh_unavailable() -> None:
    assert _pr_branch.list_open_prs() == []
    target = ForgeTarget(repo=RepoIdentity(owner="o", repo="r"), credential_env_name="GH_TOKEN")
    assert GhForge().changed_files(1, forge_target=target) is None
    listed = GhForge().open_prs(forge_target=target, limit=100, timeout=5.0)
    assert listed.outcome == "failed"
    assert listed.returncode == GH_STUB_RETURNCODE


def test_popen_of_a_permitted_read_verb_is_refused() -> None:
    with pytest.raises(GhGuardBlocked, match="Popen"):
        subprocess.Popen(["gh", "pr", "list"])


def test_popen_refusal_keeps_pair_and_flags() -> None:
    refused = popen_refusal(classify(["gh", "pr", "list", "--json", "number"]))
    assert refused.permitted is False
    assert refused.subcommand_pair == ("pr", "list")
    assert refused.flags == ("--json",)
    assert refused.is_gh is True


def test_non_gh_popen_is_delegated() -> None:
    assert subprocess.Popen(["true"]).wait() == 0


def test_path_like_gh_command_is_refused_not_delegated(tmp_path: pathlib.Path) -> None:
    fake_gh = tmp_path / "gh"
    fake_gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_gh.chmod(0o755)
    decision = classify(fake_gh)
    assert decision.is_gh is True
    assert decision.permitted is False
    with pytest.raises(GhGuardBlocked):
        subprocess.run(fake_gh, check=False)
    with pytest.raises(GhGuardBlocked):
        subprocess.run(bytes(fake_gh), check=False)
    with pytest.raises(GhGuardBlocked):
        subprocess.Popen(fake_gh)


def test_path_like_non_gh_command_is_delegated(tmp_path: pathlib.Path) -> None:
    script = tmp_path / "not-gh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    assert classify(script).is_gh is False
    assert subprocess.run(script, check=False).returncode == 0
    assert subprocess.run(bytes(script), check=False).returncode == 0
