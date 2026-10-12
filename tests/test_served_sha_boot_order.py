from __future__ import annotations

import inspect

from alan import deploy_signal, served_sha
from alan.routes import infra


def _lifespan_source() -> str:
    return inspect.getsource(infra.lifespan)


def test_served_sha_warmed_before_deploy_gate_sweep() -> None:
    source = _lifespan_source()
    assert source.index("served_sha()") < source.index("promote_covered_jobs(queue)")


def test_served_sha_warmed_before_dispatcher_start() -> None:
    source = _lifespan_source()
    assert source.index("served_sha()") < source.index("dispatcher.start()")


def test_served_sha_is_memoized_not_lazily_reread() -> None:
    assert hasattr(served_sha.served_sha, "cache_clear")


def test_merge_is_served_never_shells_out_to_read_head_itself() -> None:
    source = inspect.getsource(deploy_signal)
    assert "rev-parse" not in source


def test_merge_is_served_relies_on_the_shared_served_sha_cache() -> None:
    source = inspect.getsource(deploy_signal.merge_is_served)
    assert "served_sha()" in source
