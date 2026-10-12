from __future__ import annotations

METRIC = (
    "source lines belonging to at least one exact-text duplicate block "
    "(no identifier normalization) of at least 70 significant tokens "
    "spanning at least 10 source lines, deduplicated by (file, line)"
)

BLOCK_MIN_LINES = 10
BLOCK_MIN_TOKENS = 70

DUPLICATED_LINE_BUDGET = 595
