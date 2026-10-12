"""Print the never-live idle-wait bound (seconds) from the shared budget definition."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alan.teams._never_live_budget import idle_wait_bound_s  # noqa: E402


def main() -> int:
    print(int(idle_wait_bound_s()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
