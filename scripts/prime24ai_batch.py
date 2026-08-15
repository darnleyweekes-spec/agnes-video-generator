#!/usr/bin/env python3
"""Sequential Prime24AI prospect video batch runner.

The batch is intentionally sequential so one failed prospect does not corrupt or
starve another Agnes task. Failures are isolated and reported in the final JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.prime24ai_outreach import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PROSPECTS,
    DEFAULT_SERVER_URL,
    DEFAULT_TEMPLATE,
    AgnesRunError,
    ProspectValidationError,
    load_prospects,
    run_prospect,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prospects", default=str(DEFAULT_PROSPECTS))
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--voice", default="en-US-GuyNeural")
    parser.add_argument("--poll", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument(
        "--ids",
        nargs="*",
        help="Optional subset of prospect ids. Defaults to every prospect in the file.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        prospects = load_prospects(args.prospects)
    except (ProspectValidationError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1

    requested = set(args.ids or [])
    if requested:
        known = {prospect.id for prospect in prospects}
        unknown = sorted(requested.difference(known))
        if unknown:
            print(
                json.dumps(
                    {"status": "failed", "error": f"unknown prospect id(s): {', '.join(unknown)}"},
                    ensure_ascii=False,
                )
            )
            return 1
        prospects = [prospect for prospect in prospects if prospect.id in requested]

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    results = []
    failures = 0

    for prospect in prospects:
        try:
            result = run_prospect(
                prospect,
                server_url=args.server_url,
                output_dir=args.output_dir,
                template_path=args.template,
                voice=args.voice,
                dry_run=args.dry_run,
                poll_interval=args.poll,
                timeout=args.timeout,
            )
        except (ProspectValidationError, AgnesRunError, OSError) as exc:
            failures += 1
            result = {
                "prospect_id": prospect.id,
                "company": prospect.company,
                "status": "failed",
                "error": str(exc),
            }
        results.append(result)

    summary = {
        "status": "completed" if failures == 0 else "completed_with_failures",
        "dry_run": args.dry_run,
        "total": len(results),
        "completed": len(results) - failures,
        "failed": failures,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
