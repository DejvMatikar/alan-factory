from __future__ import annotations

import sys

from alan.self_update import _cli

raise SystemExit(_cli.main(sys.argv[1:]))
