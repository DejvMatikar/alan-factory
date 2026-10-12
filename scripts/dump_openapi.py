"""Export alan's OpenAPI schema to a file, byte-stable.

Thin argparse wrapper around alan.server.openapi_json. See
docs/architecture/health.md for the response shapes this schema now
describes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alan.server import openapi_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dump_openapi",
        description="Export alan's OpenAPI schema to a file, or to stdout.",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    schema = openapi_json()
    if args.out is None:
        sys.stdout.write(schema)
        print("wrote OpenAPI schema to stdout", file=sys.stderr)
        return 0
    args.out.write_text(schema, encoding="utf-8")
    print(f"wrote OpenAPI schema to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
