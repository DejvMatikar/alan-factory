from __future__ import annotations

import argparse
import logging
import os
import time

import uvicorn

from alan.bind import bind_refusal, resolve_host, resolve_port


def _configure_logging() -> None:
    logging.Formatter.converter = time.gmtime
    level_name = os.environ.get("ALAN_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("alan").setLevel(level)


def main() -> None:
    parser = argparse.ArgumentParser(prog="alan")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    refusal = bind_refusal(args.host)
    if refusal is not None:
        raise SystemExit(refusal)
    _configure_logging()
    host = resolve_host(args.host)
    port = resolve_port(args.port)
    uvicorn.run("alan.server:served_app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
