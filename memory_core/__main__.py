"""Run Memory Core.

Two modes matter:

``--fake-summarizer``
    The **mock server** for Person 2 (Human Client) and Person 3 (Agent Bridge).  No API
    key, no network, fully deterministic — develop a CLI or an agent adapter against the
    real contract without waiting on anyone (共通設計起點 §7).

default (AIS3)
    The real Summarizer.  Requires ``AIS3_API_KEY``.

Always a single worker.  Memory Core is the only writer of ``state.yaml`` and the MVP
promises exactly one writing process; multi-worker is an explicit non-goal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from .api import create_app
from .config import Settings
from .providers.ais3 import AIS3Summarizer
from .service import MemoryService
from .summarizer import FakeSummarizer, Summarizer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memory_core", description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(".ctfmem/core-config.json"),
        help="non-secret service defaults file (default: .ctfmem/core-config.json)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--runs", type=Path)
    parser.add_argument("--templates", type=Path)
    parser.add_argument(
        "--fake-summarizer",
        action="store_true",
        help="Use the deterministic offline Summarizer (mock server mode).",
    )
    args = parser.parse_args(argv)
    settings = Settings.from_sources(args.config)
    runs_dir = args.runs or settings.runs_dir
    templates_dir = args.templates or settings.templates_dir

    summarizer: Summarizer
    if args.fake_summarizer:
        summarizer = FakeSummarizer()
    else:
        if not settings.ais3_api_key:
            print(
                "AIS3_API_KEY is not set. Either export it, or run with --fake-summarizer "
                "for the offline mock server.",
                file=sys.stderr,
            )
            return 2
        summarizer = AIS3Summarizer(
            base_url=settings.ais3_base_url,
            api_key=settings.ais3_api_key,
            model=settings.ais3_model,
            timeout=settings.ais3_timeout,
        )

    runs_dir.mkdir(parents=True, exist_ok=True)
    service = MemoryService.build(
        runs_dir=runs_dir, templates_dir=templates_dir, summarizer=summarizer
    )
    app = create_app(service)

    mode = "fake" if args.fake_summarizer else f"ais3:{settings.ais3_model}"
    print(f"memory-core: runs={runs_dir} templates={templates_dir} summarizer={mode}")
    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
