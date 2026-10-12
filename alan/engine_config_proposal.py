from __future__ import annotations

import contextlib
import json
import re
import shutil
import threading
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from alan import agent_key, engine_config, engine_presets, engine_stack_detect, explore_chat_repo
from alan.branch_protection_compat import Finding
from alan.claude_cli import ClaudeCliError, run_chat_turn
from alan.engine_config import EngineConfig, ForgeConfig, ForgeTarget, RepoIdentity
from alan.engine_preflight import (
    _check_flow_graph as _check_flow_graph,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._pr_state_paths import (
    _engine_state_dir as _engine_state_dir,  # pyright: ignore[reportPrivateUsage]
)

_CHECKOUT_DIRNAME = "config_proposal_checkout"
INFERENCE_USER_TEXT = "Inspect this repository and propose engine configuration values."
_INFERENCE_MODEL = "sonnet"
_INFERENCE_MAX_TURNS = 40

# repo/forge/ci/code_quality/posture/pool_size/preset/prompt_overrides/dependency_sweep
# are deliberately excluded: identity and safety-critical fields are never model-
# generated, and repository conventions belong to the separate scaffolding flow, not
# the engine configuration delta this pipeline proposes.
_PROPOSABLE_FIELDS: frozenset[str] = frozenset({
    "gate_commands",
    "warmup_commands",
    "base_tools",
    "team_tools",
    "roles",
    "transition_graph",
    "flow_policy",
})

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class ProposalAbortedError(RuntimeError):
    pass


class ProposalUnrepairableError(RuntimeError):
    pass


class ProposalFieldDiff(BaseModel, frozen=True):
    field: str
    baseline: Any
    proposed: Any
    changed: bool


class ConfigProposalResult(BaseModel, frozen=True):
    slug: str
    base_preset: str
    detected_stacks: tuple[str, ...]
    additional_stacks: tuple[str, ...]
    proposed_config: EngineConfig
    diff: tuple[ProposalFieldDiff, ...]
    validation: Finding


_lock_guard = threading.Lock()
_busy_slugs: set[str] = set()


@contextlib.contextmanager
def _slug_lock(slug: str) -> Generator[None, None, None]:
    with _lock_guard:
        if slug in _busy_slugs:
            raise ProposalAbortedError(
                f"a config-proposal pipeline is already running for {slug!r}"
            )
        _busy_slugs.add(slug)
    try:
        yield
    finally:
        with _lock_guard:
            _busy_slugs.discard(slug)


def _checkout_dir(forge: ForgeTarget) -> Path:
    return _engine_state_dir(forge) / _CHECKOUT_DIRNAME


def _provision_checkout(engine_key: str, dest: Path) -> None:
    try:
        explore_chat_repo.provision(engine_key, dest)
    except explore_chat_repo.RepoProvisionError as exc:
        raise ProposalAbortedError(
            f"could not obtain a read-only checkout of {engine_key!r}: {exc}"
        ) from exc


def _proposable_baseline_json(baseline: EngineConfig) -> dict[str, Any]:
    dumped = baseline.model_dump(mode="json")
    return {field: dumped[field] for field in sorted(_PROPOSABLE_FIELDS)}


def _proposal_system_prompt(
    baseline: EngineConfig, detection: engine_stack_detect.StackDetection
) -> str:
    additional = ", ".join(detection.additional_stacks) or "none"
    baseline_json = json.dumps(_proposable_baseline_json(baseline), indent=2)
    return (
        "You are inspecting a target repository's own read-only checkout at your "
        "current working directory, in order to propose an alan engine configuration "
        "for it. This working directory IS the target repository; never assume or "
        "reference any other repository.\n\n"
        f"A deterministic build-file scan recommended the {detection.recommended_preset!r} "
        "stack preset as the closest baseline. It also flagged these additional stacks "
        f"present in the repo: {additional}.\n\n"
        "Below is the baseline preset's current value for every field you may propose:\n"
        f"{baseline_json}\n\n"
        "Inspect the repository, then respond with ONLY a single JSON object — no prose, "
        f"no markdown code fence. Its keys must be a subset of {sorted(_PROPOSABLE_FIELDS)!r}. "
        "Include only the fields whose value should differ from the baseline shown above; "
        "omit any field you want to leave unchanged. Each included field's value must "
        "match that field's shape above."
    )


def _run_inference(
    dest: Path, baseline: EngineConfig, detection: engine_stack_detect.StackDetection
) -> str:
    session_id = uuid.uuid4().hex
    try:
        result = run_chat_turn(
            user_text=INFERENCE_USER_TEXT,
            session_id=session_id,
            resume=False,
            system_prompt=_proposal_system_prompt(baseline, detection),
            model=_INFERENCE_MODEL,
            max_turns=_INFERENCE_MAX_TURNS,
            allowed_tools=None,
            cwd=dest,
            agent=agent_key.ENGINE_CONFIG_PROPOSAL,
        )
    except ClaudeCliError as exc:
        raise ProposalAbortedError(f"inspection inference failed: {exc}") from exc
    return result.result_text


def _extract_json_object(text: str) -> Any:
    candidates: list[str] = []
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1))
    stripped = text.strip()
    candidates.append(stripped)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(candidate)
    raise ProposalUnrepairableError("model output does not contain a parseable JSON object")


def _repair_or_reject(raw_text: str, baseline: EngineConfig) -> EngineConfig:
    parsed = _extract_json_object(raw_text)
    if not isinstance(parsed, dict):
        raise ProposalUnrepairableError("model output is not a JSON object")
    parsed_dict = cast(dict[str, Any], parsed)
    proposable = {k: v for k, v in parsed_dict.items() if k in _PROPOSABLE_FIELDS}
    merged = {**baseline.model_dump(mode="json"), **proposable}
    try:
        return EngineConfig.model_validate(merged)
    except ValidationError as exc:
        raise ProposalUnrepairableError(
            f"model output does not fit the engine config shape: {exc}"
        ) from exc


def _field_diff(baseline: EngineConfig, candidate: EngineConfig) -> tuple[ProposalFieldDiff, ...]:
    base_json = baseline.model_dump(mode="json")
    cand_json = candidate.model_dump(mode="json")
    return tuple(
        ProposalFieldDiff(
            field=field,
            baseline=base_json[field],
            proposed=cand_json[field],
            changed=base_json[field] != cand_json[field],
        )
        for field in sorted(_PROPOSABLE_FIELDS)
    )


def validate_draft(cfg: EngineConfig) -> Finding:
    return _check_flow_graph(cfg)


def _run_pipeline(
    engine_key: str,
    repo: RepoIdentity,
    forge: ForgeConfig,
    *,
    base_preset: str | None,
) -> ConfigProposalResult:
    forge_target = ForgeTarget.from_forge_config(forge)
    dest = _checkout_dir(forge_target)
    try:
        _provision_checkout(engine_key, dest)
        detection = engine_stack_detect.detect_stack(dest)
        preset_name = base_preset or detection.recommended_preset
        baseline = engine_presets.build_config(preset_name, repo.owner, repo.repo)
        if baseline is None:
            raise ProposalAbortedError(f"unknown base preset {preset_name!r}")
        # forge is not a proposable field, but build_config regenerates a fresh
        # ForgeConfig (dropping the registered engine's authoritative_checks etc.);
        # carry the real forge through so the proposal can't clobber it on apply.
        baseline = baseline.model_copy(update={"forge": forge})
        raw_text = _run_inference(dest, baseline, detection)
        candidate = _repair_or_reject(raw_text, baseline)
        validation = validate_draft(candidate)
        diff = _field_diff(baseline, candidate)
        return ConfigProposalResult(
            slug=engine_key,
            base_preset=preset_name,
            detected_stacks=detection.detected_stacks,
            additional_stacks=detection.additional_stacks,
            proposed_config=candidate,
            diff=diff,
            validation=validation,
        )
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def propose_config(engine_key: str, *, base_preset: str | None = None) -> ConfigProposalResult:
    cfg = engine_config.lookup_by_slug(engine_key)
    if cfg is None or cfg.forge is None:
        raise ProposalAbortedError(
            f"engine {engine_key!r} is not registered with a forge configured — cannot "
            "provision a read-only checkout of it to inspect"
        )
    with _slug_lock(engine_key):
        return _run_pipeline(engine_key, cfg.repo, cfg.forge, base_preset=base_preset)
