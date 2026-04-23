#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_browser_preflight_utils import build_runtime_browser_preflight, write_runtime_browser_preflight
from tracking_llm_utils import normalize_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare source-first preflight guidance for runtime_browser_session verification."
    )
    parser.add_argument("--workspace-dir", required=True, help="Path to the tracking workspace session directory.")
    parser.add_argument("--schema-path", default="", help="Optional path to tracking_schema.json.")
    parser.add_argument("--target-file", default="", help="Optional target HTML/JS file path.")
    parser.add_argument(
        "--event-id",
        action="append",
        default=[],
        help="Optional event_id filter. Repeat for multiple ids.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output path. Default: <workspace-dir>/runtime_browser_preflight.json",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def resolve_output_path(args: argparse.Namespace, workspace_dir: Path) -> Path:
    explicit = normalize_text(args.output)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (workspace_dir / "runtime_browser_preflight.json").resolve()


def main() -> int:
    args = parse_args()
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    event_ids = {
        normalize_text(event_id)
        for event_id in args.event_id
        if normalize_text(event_id)
    }
    payload = build_runtime_browser_preflight(
        workspace_dir=workspace_dir,
        schema_path_text=args.schema_path,
        target_file_text=args.target_file,
        event_ids=event_ids or None,
    )
    output_path = resolve_output_path(args, workspace_dir)
    write_runtime_browser_preflight(output_path, payload)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
