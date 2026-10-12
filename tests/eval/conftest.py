from __future__ import annotations

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    markexpr = str(config.getoption("markexpr") or "")
    if "eval" in markexpr:
        return
    skip_eval = pytest.mark.skip(reason="opt in via `-m eval`")
    for item in items:
        if item.get_closest_marker("eval") is not None:
            item.add_marker(skip_eval)
