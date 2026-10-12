from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from alan.engine_presets import PRESETS
from alan.routes.engines import ConfigProposalRequest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_TS = _REPO_ROOT / "alan" / "web" / "src" / "api.ts"

_UNION_RE = re.compile(r"export type ConfigProposalBasePreset =([^;]+);")
_CONST_RE = re.compile(
    r"export const CONFIG_PROPOSAL_BASE_PRESETS: "
    r"readonly ConfigProposalBasePreset\[\] = \[([^\]]*)\]"
)
_QUOTED_RE = re.compile(r'"([^"]+)"')

_FRONTEND_FIX = (
    'add it to ConfigProposalBasePreset and CONFIG_PROPOSAL_BASE_PRESETS in alan/web/src/api.ts'
)
_BACKEND_FIX = "add it to ConfigProposalRequest.base_preset in alan/routes/engines.py"


def _extract(pattern: re.Pattern[str], source: str, surface: str) -> set[str]:
    match = pattern.search(source)
    if match is None:
        raise AssertionError(
            f"could not find {surface} in {_API_TS.relative_to(_REPO_ROOT)} — "
            "the declaration was renamed or reformatted; update this test's pattern"
        )
    return set(_QUOTED_RE.findall(match.group(1)))


def _backend_literal_names() -> set[str]:
    annotation = ConfigProposalRequest.model_fields["base_preset"].annotation
    names: set[str] = set()
    for arg in get_args(annotation):
        if isinstance(arg, str):
            names.add(arg)
        else:
            names.update(inner for inner in get_args(arg) if isinstance(inner, str))
    return names


def drift_message(surface: str, expected: set[str], found: set[str], fix: str) -> str:
    missing = sorted(expected - found)
    extra = sorted(found - expected)
    return (
        f"{surface} is out of sync with alan.engine_presets.PRESETS: "
        f"missing={missing} unexpected={extra} — {fix}"
    )


def test_backend_config_proposal_literal_covers_every_preset() -> None:
    found = _backend_literal_names()
    expected = set(PRESETS)
    assert found == expected, drift_message(
        "ConfigProposalRequest.base_preset", expected, found, _BACKEND_FIX
    )


def test_frontend_union_type_covers_every_preset() -> None:
    found = _extract(_UNION_RE, _API_TS.read_text(encoding="utf-8"), "ConfigProposalBasePreset")
    expected = set(PRESETS)
    assert found == expected, drift_message(
        "ConfigProposalBasePreset", expected, found, _FRONTEND_FIX
    )


def test_frontend_const_array_covers_every_preset() -> None:
    found = _extract(
        _CONST_RE, _API_TS.read_text(encoding="utf-8"), "CONFIG_PROPOSAL_BASE_PRESETS"
    )
    expected = set(PRESETS)
    assert found == expected, drift_message(
        "CONFIG_PROPOSAL_BASE_PRESETS", expected, found, _FRONTEND_FIX
    )


def test_drift_message_names_the_missing_preset_and_the_fix() -> None:
    message = drift_message(
        "CONFIG_PROPOSAL_BASE_PRESETS", {"dotnet", "swift"}, {"dotnet"}, _FRONTEND_FIX
    )
    assert "missing=['swift']" in message
    assert "alan/web/src/api.ts" in message


def test_extract_raises_when_the_declaration_was_renamed() -> None:
    try:
        _extract(_UNION_RE, "export type SomethingElse = 'dotnet';", "ConfigProposalBasePreset")
    except AssertionError as exc:
        assert "update this test's pattern" in str(exc)
    else:  # pragma: no cover - the extractor must not silently return an empty set
        raise AssertionError("_extract must fail loudly when the declaration is missing")
