from __future__ import annotations

CLOCK_MIGRATED: frozenset[str] = frozenset({
    "alan/queue.py",
    "alan/slot_pool.py",
    "alan/dispatcher.py",
    "alan/_dispatcher_loop.py",
    "alan/_dispatcher_usage_limit.py",
})

RAW_CLOCK_READS: frozenset[str] = frozenset()
