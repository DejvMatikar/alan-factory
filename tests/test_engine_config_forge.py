from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    CodeQualityConfig,
    EngineConfig,
    ForgeConfig,
    ForgeConfigValidationError,
    ForgeTarget,
    MissingEnvVarsError,
    Posture,
    RepoIdentity,
    _secret_env_vars,
    default_engine_config,
    forge_target_for_workspace,
    is_authoritative_check,
    load_yaml,
    lookup_by_slug,
    register,
    registered_secret_env_names,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_RESOLVER_NAME = "forge_target_for_workspace"
_RESOLVER_PARAMS = ("engine_key", "snapshot_config")
_SCAN_EXCLUDED_SEGMENTS = frozenset({"web", "__pycache__"})


def test_forge_config_requires_repo_and_credential_env_name() -> None:
    cfg = ForgeConfig(
        repo=RepoIdentity(owner="a", repo="b"), credential_env_name="MY_TOKEN"
    )
    assert cfg.repo.slug == "a/b"
    assert cfg.credential_env_name == "MY_TOKEN"  # noqa: S105
    assert cfg.authoritative_checks == frozenset()


def test_forge_config_authoritative_checks_accepts_nonempty_set() -> None:
    cfg = ForgeConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",
        authoritative_checks=frozenset({"Check A", "Check B"}),
    )
    assert cfg.authoritative_checks == frozenset({"Check A", "Check B"})


def test_forge_config_is_frozen() -> None:
    cfg = ForgeConfig(
        repo=RepoIdentity(owner="a", repo="b"), credential_env_name="MY_TOKEN"
    )
    with pytest.raises(ValidationError):
        cfg.credential_env_name = "OTHER"  # type: ignore[misc]


def test_forge_config_missing_credential_env_name_raises() -> None:
    with pytest.raises(ValidationError):
        ForgeConfig(repo=RepoIdentity(owner="a", repo="b"))  # type: ignore[call-arg]


def test_forge_config_rejects_empty_credential_env_name() -> None:
    with pytest.raises(ValidationError):
        ForgeConfig(repo=RepoIdentity(owner="a", repo="b"), credential_env_name="")


def test_forge_config_rejects_non_env_var_shaped_credential_name() -> None:
    with pytest.raises(ValidationError, match="environment variable name"):
        ForgeConfig(
            repo=RepoIdentity(owner="a", repo="b"), credential_env_name="not-an-env-var"
        )


def test_forge_config_rejects_inlined_secret_looking_value() -> None:
    with pytest.raises(ValidationError):
        ForgeConfig(
            repo=RepoIdentity(owner="a", repo="b"), credential_env_name="ghp_abc123"
        )


def test_engine_config_forge_defaults_none() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert cfg.forge is None


def test_engine_config_legacy_blob_without_forge_key_deserializes_none() -> None:
    legacy = {
        "repo": {"owner": "a", "repo": "b"},
        "posture": "propose",
        "transition_graph": {"research": ["implementation_plan"]},
    }
    restored = EngineConfig.model_validate(legacy)
    assert restored.forge is None


def test_engine_config_roundtrips_forge_via_json() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="a", repo="b"),
            credential_env_name="MY_TOKEN",
            authoritative_checks=frozenset({"Check A"}),
        ),
    )
    dumped = cfg.model_dump(mode="json")
    restored = EngineConfig.model_validate(dumped)
    assert restored == cfg
    assert restored.forge is not None
    assert restored.forge.authoritative_checks == frozenset({"Check A"})


def test_forge_target_from_forge_config() -> None:
    forge = ForgeConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",
        authoritative_checks=frozenset({"Check A"}),
    )
    target = ForgeTarget.from_forge_config(forge)
    assert target.repo == forge.repo
    assert target.credential_env_name == forge.credential_env_name
    assert target.authoritative_checks == forge.authoritative_checks


def test_forge_target_is_frozen() -> None:
    target = ForgeTarget(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",
        authoritative_checks=frozenset(),
    )
    with pytest.raises(ValidationError):
        target.credential_env_name = "OTHER"  # type: ignore[misc]


def test_forge_target_equality_by_value() -> None:
    a = ForgeTarget(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",
        authoritative_checks=frozenset({"x"}),
    )
    b = ForgeTarget(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",
        authoritative_checks=frozenset({"x"}),
    )
    assert a == b


def test_forge_target_authoritative_checks_defaults_to_empty_frozenset() -> None:
    target = ForgeTarget(
        repo=RepoIdentity(owner="a", repo="b"), credential_env_name="MY_TOKEN"
    )
    assert target.authoritative_checks == frozenset()


def test_forge_target_rejects_empty_credential_env_name() -> None:
    with pytest.raises(ValidationError):
        ForgeTarget(repo=RepoIdentity(owner="a", repo="b"), credential_env_name="")


def test_forge_target_rejects_non_env_var_shaped_credential_name() -> None:
    with pytest.raises(ValidationError, match="environment variable name"):
        ForgeTarget(
            repo=RepoIdentity(owner="a", repo="b"), credential_env_name="not-an-env-var"
        )


def test_forge_target_rejects_inlined_secret_looking_value() -> None:
    with pytest.raises(ValidationError):
        ForgeTarget(repo=RepoIdentity(owner="a", repo="b"), credential_env_name="ghp_abc123")


def test_is_authoritative_check_true_for_declared_check() -> None:
    target = ForgeTarget(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",
        authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
    )
    assert is_authoritative_check(target, "SonarCloud Code Analysis") is True


def test_is_authoritative_check_false_for_undeclared_check() -> None:
    target = ForgeTarget(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",
        authoritative_checks=frozenset({"SonarCloud Code Analysis"}),
    )
    assert is_authoritative_check(target, "Some Other Check") is False


def test_is_authoritative_check_always_false_when_checks_empty() -> None:
    target = ForgeTarget(
        repo=RepoIdentity(owner="a", repo="b"),
        credential_env_name="MY_TOKEN",
        authoritative_checks=frozenset(),
    )
    assert is_authoritative_check(target, "SonarCloud Code Analysis") is False
    assert is_authoritative_check(target, "") is False
    assert is_authoritative_check(target, "anything") is False


def test_forge_target_for_workspace_no_engine_key_no_snapshot_uses_default() -> None:
    target = forge_target_for_workspace(engine_key=None, snapshot_config=None)
    assert target.repo.slug == "DejvMatikar/alan"
    assert target.credential_env_name == "ALAN_VERIFIER_GH_TOKEN"  # noqa: S105


def test_forge_target_for_workspace_prefers_snapshot_over_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACME_WIDGET_GH_TOKEN", "fake-token")
    registered = load_yaml(_FIXTURES / "engine_config_forge_secondary.yaml")
    assert lookup_by_slug("acme-corp/widget-app") == registered

    pinned_snapshot = EngineConfig(
        repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="pinned", repo="pinned-repo"),
            credential_env_name="PINNED_TOKEN",
            authoritative_checks=frozenset({"Pinned Check"}),
        ),
    )

    target = forge_target_for_workspace(
        engine_key="acme-corp/widget-app", snapshot_config=pinned_snapshot
    )

    assert target.repo.slug == "pinned/pinned-repo"
    assert target.credential_env_name == "PINNED_TOKEN"  # noqa: S105
    assert target.authoritative_checks == frozenset({"Pinned Check"})


def test_forge_target_for_workspace_falls_back_to_snapshot_when_engine_key_none() -> None:
    snapshot = EngineConfig(
        repo=RepoIdentity(owner="pinned", repo="repo"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="pinned", repo="repo"),
            credential_env_name="PINNED_TOKEN",
            authoritative_checks=frozenset({"Pinned Check"}),
        ),
    )
    target = forge_target_for_workspace(engine_key=None, snapshot_config=snapshot)
    assert target.repo.slug == "pinned/repo"
    assert target.credential_env_name == "PINNED_TOKEN"  # noqa: S105
    assert target.authoritative_checks == frozenset({"Pinned Check"})


def test_forge_target_for_workspace_unregistered_key_falls_back_to_snapshot() -> None:
    snapshot = EngineConfig(
        repo=RepoIdentity(owner="pinned", repo="repo"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="pinned", repo="repo"),
            credential_env_name="PINNED_TOKEN",
            authoritative_checks=frozenset({"Pinned Check"}),
        ),
    )
    target = forge_target_for_workspace(
        engine_key="ghost/repo-does-not-exist", snapshot_config=snapshot
    )
    assert target.repo.slug == "pinned/repo"
    assert target.credential_env_name == "PINNED_TOKEN"  # noqa: S105


def test_forge_target_for_workspace_unregistered_key_no_snapshot_falls_back_to_default() -> (
    None
):
    target = forge_target_for_workspace(
        engine_key="ghost/repo-does-not-exist", snapshot_config=None
    )
    assert target.repo.slug == "DejvMatikar/alan"
    assert target.credential_env_name == "ALAN_VERIFIER_GH_TOKEN"  # noqa: S105


def test_forge_target_for_workspace_registered_engine_without_forge_falls_back_to_snapshot() -> (
    None
):
    register(EngineConfig(repo=RepoIdentity(owner="no-forge", repo="engine")))
    snapshot = EngineConfig(
        repo=RepoIdentity(owner="no-forge", repo="engine"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="pinned", repo="repo"),
            credential_env_name="PINNED_TOKEN",
            authoritative_checks=frozenset(),
        ),
    )

    target = forge_target_for_workspace(
        engine_key="no-forge/engine", snapshot_config=snapshot
    )

    assert target.repo.slug == "pinned/repo"
    assert target.credential_env_name == "PINNED_TOKEN"  # noqa: S105


def test_forge_target_for_workspace_no_forge_engine_no_snapshot_falls_back_to_default() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="no-forge", repo="engine")))

    target = forge_target_for_workspace(engine_key="no-forge/engine", snapshot_config=None)

    assert target.repo.slug == "DejvMatikar/alan"


def test_forge_target_for_workspace_pre_deploy_snapshot_falls_back_to_default() -> None:
    legacy_blob = {
        "repo": {"owner": "acme-corp", "repo": "widget-app"},
        "posture": "propose",
    }
    legacy_snapshot = EngineConfig.model_validate(legacy_blob)
    assert legacy_snapshot.forge is None

    target = forge_target_for_workspace(engine_key=None, snapshot_config=legacy_snapshot)

    assert target.repo.slug == "DejvMatikar/alan"
    assert target.credential_env_name == "ALAN_VERIFIER_GH_TOKEN"  # noqa: S105
    assert target.authoritative_checks == frozenset()


def test_forge_target_for_workspace_never_returns_empty_target() -> None:
    target = forge_target_for_workspace(engine_key=None, snapshot_config=None)
    assert target.repo.owner
    assert target.repo.repo
    assert target.credential_env_name


def test_secondary_forge_fixture_distinct_from_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACME_WIDGET_GH_TOKEN", "fake-token")
    secondary = load_yaml(_FIXTURES / "engine_config_forge_secondary.yaml")
    default = default_engine_config()

    assert secondary.forge is not None
    assert default.forge is not None
    assert secondary.forge.repo != default.forge.repo
    assert secondary.forge.credential_env_name != default.forge.credential_env_name
    assert secondary.forge.authoritative_checks != default.forge.authoritative_checks
    assert secondary.forge.repo.slug == "acme-corp/widget-app"
    assert secondary.forge.credential_env_name == "ACME_WIDGET_GH_TOKEN"  # noqa: S105
    assert secondary.forge.authoritative_checks == frozenset({"Widget CI"})
    assert secondary.posture == Posture.propose


def test_nonpython_forge_fixture_loads_with_distinct_repo_and_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUSTCO_GH_TOKEN", "fake-token")
    cfg = load_yaml(_FIXTURES / "engine_config_forge_nonpython.yaml")

    assert cfg.code_quality is None
    assert cfg.forge is not None
    assert cfg.forge.repo.slug == "rustco/cargo-service"
    assert cfg.forge.credential_env_name == "RUSTCO_GH_TOKEN"  # noqa: S105
    for cmd in cfg.gate_commands:
        assert cmd[0] not in {"uv", "npm"}
    for cmd in cfg.warmup_commands:
        assert cmd[0] not in {"uv", "npm"}


def test_nonpython_forge_fixture_authoritative_checks_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUSTCO_GH_TOKEN", "fake-token")
    cfg = load_yaml(_FIXTURES / "engine_config_forge_nonpython.yaml")
    assert cfg.forge is not None
    assert cfg.forge.authoritative_checks == frozenset()


def test_nonpython_forge_fixture_empty_checks_never_read_as_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUSTCO_GH_TOKEN", "fake-token")
    cfg = load_yaml(_FIXTURES / "engine_config_forge_nonpython.yaml")
    assert cfg.forge is not None
    target = ForgeTarget.from_forge_config(cfg.forge)

    assert is_authoritative_check(target, "SonarCloud Code Analysis") is False
    assert is_authoritative_check(target, "cargo test") is False


def test_nonpython_forge_fixture_resolves_via_workspace_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUSTCO_GH_TOKEN", "fake-token")
    load_yaml(_FIXTURES / "engine_config_forge_nonpython.yaml")

    target = forge_target_for_workspace(
        engine_key="rustco/cargo-service", snapshot_config=None
    )

    assert target.repo.slug == "rustco/cargo-service"
    assert target.credential_env_name == "RUSTCO_GH_TOKEN"  # noqa: S105
    assert target.authoritative_checks == frozenset()


def test_secret_env_vars_includes_forge_credential_only() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="a", repo="b"), credential_env_name="MY_FORGE_TOKEN"
        ),
    )
    assert _secret_env_vars(cfg) == ["MY_FORGE_TOKEN"]


def test_secret_env_vars_includes_both_code_quality_and_forge() -> None:
    cfg = default_engine_config()
    assert _secret_env_vars(cfg) == ["SONAR_TOKEN", "ALAN_VERIFIER_GH_TOKEN"]


def test_register_rejects_missing_forge_credential_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MY_FORGE_TOKEN", raising=False)
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="a", repo="b"), credential_env_name="MY_FORGE_TOKEN"
        ),
    )
    with pytest.raises(MissingEnvVarsError, match="MY_FORGE_TOKEN"):
        register(cfg)


def test_register_succeeds_when_forge_credential_env_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_FORGE_TOKEN", "fake-token")
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="a", repo="b"), credential_env_name="MY_FORGE_TOKEN"
        ),
    )
    register(cfg)
    assert lookup_by_slug("a/b") == cfg


def test_forge_config_validation_error_reexported_from_engine_config() -> None:
    assert issubclass(ForgeConfigValidationError, ValueError)


def test_registered_secret_env_names_includes_default_when_registry_empty() -> None:
    assert registered_secret_env_names() == frozenset({
        "ALAN_VERIFIER_GH_TOKEN", "SONAR_TOKEN",
    })


def test_registered_secret_env_names_includes_secondary_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACME_WIDGET_GH_TOKEN", "fake-token")
    load_yaml(_FIXTURES / "engine_config_forge_secondary.yaml")

    names = registered_secret_env_names()

    assert names == frozenset({
        "ALAN_VERIFIER_GH_TOKEN", "SONAR_TOKEN", "ACME_WIDGET_GH_TOKEN",
    })


def test_registered_secret_env_names_skips_engines_without_forge_or_code_quality() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="no-forge", repo="engine")))

    names = registered_secret_env_names()

    assert names == frozenset({"ALAN_VERIFIER_GH_TOKEN", "SONAR_TOKEN"})


def test_registered_secret_env_names_dedupes_default_registered_twice() -> None:
    register(default_engine_config(), default=True, validate_env=False)

    names = registered_secret_env_names()

    assert names == frozenset({"ALAN_VERIFIER_GH_TOKEN", "SONAR_TOKEN"})


def test_registered_secret_env_names_includes_registered_code_quality_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTHER_SONAR_TOKEN", "fake-token")
    register(
        EngineConfig(
            repo=RepoIdentity(owner="other-co", repo="other-repo"),
            code_quality=CodeQualityConfig(
                project_key="other",
                organization="other-co",
                host_url="https://sonarcloud.io",
                token_env_var="OTHER_SONAR_TOKEN",
            ),
        )
    )

    names = registered_secret_env_names()

    assert names == frozenset({
        "ALAN_VERIFIER_GH_TOKEN", "SONAR_TOKEN", "OTHER_SONAR_TOKEN",
    })


def test_default_forge_target_matches_resolver_fallback() -> None:
    assert DEFAULT_FORGE_TARGET == forge_target_for_workspace(None, None)


def _is_absent(node: ast.expr | None) -> bool:
    return node is None or (isinstance(node, ast.Constant) and node.value is None)


def _is_default_resolver_call(call: ast.Call) -> bool:
    func = call.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if name != _RESOLVER_NAME:
        return False
    bound: dict[str, ast.expr] = dict(zip(_RESOLVER_PARAMS, call.args, strict=False))
    bound.update({kw.arg: kw.value for kw in call.keywords if kw.arg in _RESOLVER_PARAMS})
    return all(_is_absent(bound.get(param)) for param in _RESOLVER_PARAMS)


class _ImportTimeResolverCalls(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        if _is_default_resolver_call(node):
            self.hits.append(node.lineno)
        self.generic_visit(node)

    def _visit_defaults(self, args: ast.arguments) -> None:
        for default in (*args.defaults, *args.kw_defaults):
            if default is not None:
                self.visit(default)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_defaults(node.args)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_defaults(node.args)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_defaults(node.args)


def _import_time_resolver_calls(source: str) -> list[int]:
    visitor = _ImportTimeResolverCalls()
    visitor.visit(ast.parse(source))
    return visitor.hits


@pytest.mark.parametrize(
    "source",
    [
        "X = forge_target_for_workspace(None, None)",
        "X = forge_target_for_workspace(engine_key=None, snapshot_config=None)",
        "X = m.forge_target_for_workspace(None, None)",
        "X = forge_target_for_workspace(None, snapshot_config=None)",
        "class C:\n    X = forge_target_for_workspace(None, None)",
        "def g(a=forge_target_for_workspace(None, None)):\n    pass",
        "def g(*, a=forge_target_for_workspace(None, None)):\n    pass",
        "async def g(a=forge_target_for_workspace(None, None)):\n    pass",
        "@deco(forge_target_for_workspace(None, None))\ndef g():\n    pass",
        "@deco(forge_target_for_workspace(None, None))\nasync def g():\n    pass",
        "f = lambda a=forge_target_for_workspace(None, None): a",
    ],
)
def test_resolver_predicate_flags_import_time_default_calls(source: str) -> None:
    assert len(_import_time_resolver_calls(source)) == 1


@pytest.mark.parametrize(
    "source",
    [
        "X = forge_target_for_workspace('x', None)",
        "X = forge_target_for_workspace(None, cfg)",
        "X = forge_target_for_workspace(engine_key='x')",
        "X = forge_target_for_workspace(engine_key=None, snapshot_config=cfg)",
        "def g():\n    return forge_target_for_workspace(None, None)",
        "async def g():\n    return forge_target_for_workspace(None, None)",
        "class C:\n    def m(self):\n        return forge_target_for_workspace(None, None)",
        "f = lambda: forge_target_for_workspace(None, None)",
        "X = some_other_resolver(None, None)",
    ],
)
def test_resolver_predicate_ignores_calls_that_do_not_run_at_import_time(source: str) -> None:
    assert _import_time_resolver_calls(source) == []


def _scanned_source_files() -> list[Path]:
    engine_config_module = _REPO_ROOT / "alan" / "engine_config.py"
    candidates = [*(_REPO_ROOT / "alan").rglob("*.py"), *(_REPO_ROOT / "scripts").glob("*.py")]
    return sorted(
        path
        for path in candidates
        if path != engine_config_module
        and not _SCAN_EXCLUDED_SEGMENTS.intersection(path.relative_to(_REPO_ROOT).parts)
    )


def test_no_module_level_default_resolver_call_outside_engine_config() -> None:
    offenders = {
        str(path.relative_to(_REPO_ROOT)): hits
        for path in _scanned_source_files()
        if (hits := _import_time_resolver_calls(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}
