"""Emit a config/engines.d/<slug>.yaml body from a live persisted engine record.

Read-only: never writes to ~/.alan/engines or config/engines.d/ itself. Pipe the
output to a file to actually bring a file-less engine under version control:

    uv run python scripts/export_engine_configs.py example-org/demo > config/engines.d/demo.yaml

Only engines registered via POST /api/engines (register_and_persist) have a
persisted record to export from -- the built-in default engine is a pure
in-code fallback (alan.engine_config.default_engine_config()) unless an
operator has changed one of its live dials, which is the same event that
first persists it.

prompt_overrides is dropped from the rendered YAML: override text stays out
of the tracked tree (the build brief's boundary), and it would be inert in
the file anyway -- apply_stored_projection always re-sources it from the
persisted record at load time.
"""

from __future__ import annotations

import argparse
import sys

import yaml

from alan.engine_config import EngineConfig, RepoIdentity
from alan.engine_registry_store import read_engine


def load_live_config(slug: str) -> EngineConfig:
    identity = RepoIdentity.from_slug(slug)
    payload = read_engine(identity.slug)
    if payload is None:
        raise SystemExit(f"no persisted engine record for {slug!r} under ~/.alan/engines")
    return EngineConfig.model_validate(payload)


def render_yaml(config: EngineConfig) -> str:
    payload = config.model_dump(mode="json", exclude={"prompt_overrides"})
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit a config/engines.d/<slug>.yaml body from a live persisted engine record."
    )
    parser.add_argument("slug", help="owner/repo slug, e.g. example-org/demo")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_live_config(args.slug)
    sys.stdout.write(render_yaml(config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
