from __future__ import annotations

import ast
import sys
from pathlib import Path


def _imported_modules(module_name: str) -> set[str]:
    path = Path(sys.modules[module_name].__file__)  # type: ignore[arg-type]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


def test_dep_functions_are_same_objects_as_server_re_exports() -> None:
    import alan.routes._deps as deps
    import alan.server as server

    assert server._queue_dep is deps.queue_dep
    assert server._pubsub_dep is deps.pubsub_dep
    assert server._dispatcher_dep is deps.dispatcher_dep
    assert server._workspaces_root_dep is deps.workspaces_root_dep


def test_workspaces_does_not_import_sibling_route_modules() -> None:
    import alan.routes.workspaces  # noqa: F401

    imports = _imported_modules("alan.routes.workspaces")
    assert "alan.routes.chat" not in imports
    assert "alan.routes.infra" not in imports


def test_infra_does_not_import_sibling_route_modules() -> None:
    import alan.routes.infra  # noqa: F401

    imports = _imported_modules("alan.routes.infra")
    assert "alan.routes.chat" not in imports
    assert "alan.routes.workspaces" not in imports


def test_routes_env_var_constants_are_pinned() -> None:
    from alan.routes._deps import _DISPATCHER_DISABLED_ENV, _QUEUE_DB_ENV, _TRUTHY

    assert _DISPATCHER_DISABLED_ENV == "ALAN_DISABLE_DISPATCHER"
    assert _QUEUE_DB_ENV == "ALAN_QUEUE_DB_PATH"
    assert "on" in _TRUTHY


def test_routes_package_reexports_routers() -> None:
    import alan.routes as routes
    import alan.routes.infra as infra_mod
    import alan.routes.workspaces as workspaces_mod

    assert routes.infra_router is infra_mod.infra_router
    assert routes.workspaces_router is workspaces_mod.workspaces_router
    assert routes.lifespan is infra_mod.lifespan
    assert routes.mount_ui is infra_mod.mount_ui


def test_require_str_list_drops_none_items() -> None:
    from alan.routes._deps import require_str_list

    assert require_str_list([None, "ok", None], "q") == ["ok"]


def test_strip_code_fence_no_newline_in_fence() -> None:
    from alan.routes._deps import strip_code_fence

    assert strip_code_fence("```python") == "```python"


def test_strip_code_fence_no_closing_fence() -> None:
    from alan.routes._deps import strip_code_fence

    assert strip_code_fence("```yaml\nkey: value") == "key: value"
