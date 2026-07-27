"""Launch the local web client.

``python -m human_client.web --templates ./templates``

There is no ``--host`` flag. The server binds loopback by construction, because it is
unauthenticated and can write to the whiteboard; making the bind address configurable would
turn one careless flag into an open write API.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from ..client import MemoryCoreClient
from ..config import config_from_args
from .app import create_web_app
from .catalog import load_catalog

HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_CORE_TIMEOUT = 330.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m human_client.web",
        description="Serve the local human whiteboard UI against a running Memory Core.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--api-url", default=None, help="Memory Core base URL.")
    parser.add_argument("--actor", default=None, help="Attributed to every write.")
    parser.add_argument(
        "--templates",
        type=Path,
        required=True,
        help=(
            "Directory of template files, read once for technique titles and the template "
            "picker. Point it at the same directory Memory Core was started with."
        ),
    )
    parser.add_argument("--timeout", type=float, default=None, help="Read timeout, seconds.")
    parser.add_argument(
        "--core-timeout",
        type=float,
        default=DEFAULT_CORE_TIMEOUT,
        help=(
            "Read timeout for the routes that can reach the Summarizer: ingest, query, and "
            "creation with an initial input. It must be at least Memory Core's own "
            "summarizer read budget multiplied by the number of times an ingest may "
            "re-summarize after a concurrent commit."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = config_from_args(args)

    try:
        catalog = load_catalog(args.templates)
    except ValueError as exc:
        print(f"ctfmem-web: {exc}", file=sys.stderr)
        return 2

    app = create_web_app(
        client=MemoryCoreClient(config.api_url, timeout=config.timeout),
        summarizing_client=MemoryCoreClient(config.api_url, timeout=args.core_timeout),
        config=config,
        catalog=catalog,
        port=args.port,
    )

    print(f"ctfmem-web  http://{HOST}:{args.port}")
    print(f"  core      {config.api_url}")
    print(f"  actor     {config.actor}")
    print(f"  templates {catalog.directory} ({len(catalog.templates)} usable)")
    print(f"  timeouts  {config.timeout}s reads, {args.core_timeout}s summarizer routes")
    for problem in catalog.problems:
        print(f"  warning   {problem}")

    uvicorn.run(app, host=HOST, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
