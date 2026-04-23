#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from tracking_llm_utils import DEFAULT_ALLOWED_ACTIONS, normalize_text, now_utc_iso, parse_html_dom, safe_json_load

CAMEL_CASE_RE = re.compile(r"^[a-z][A-Za-z0-9]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate agent-provided llm_output JSON before applying tracking draft generation."
    )
    parser.add_argument("--prepare-context", required=True, help="Path to prepare_context.json.")
    parser.add_argument("--agent-json", required=True, help="Path to agent llm_output JSON.")
    parser.add_argument(
        "--output",
        default="",
        help="Output path. Default: <workspace_dir>/llm_output.json",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Invalid JSON file: {path} ({exc})")
    if not isinstance(payload, dict):
        raise SystemExit(f"JSON root must be an object: {path}")
    return payload


def require_non_empty_string(value: Any, *, field: str) -> str:
    text = normalize_text(value)
    if not text:
        raise ValueError(f"{field} is required")
    return text


def require_camel_case(value: Any, *, field: str) -> str:
    text = require_non_empty_string(value, field=field)
    if not CAMEL_CASE_RE.fullmatch(text):
        raise ValueError(f"{field} must be camelCase, got '{text}'")
    return text


def normalize_action(value: Any, *, field: str) -> str:
    action = require_non_empty_string(value, field=field).lower()
    if action not in DEFAULT_ALLOWED_ACTIONS:
        allowed = ",".join(sorted(DEFAULT_ALLOWED_ACTIONS))
        raise ValueError(f"{field} must be one of: {allowed}; got '{action}'")
    return action


def normalize_action_fields(raw_fields: Any, *, field: str, default_action: str) -> list[dict[str, Any]]:
    if raw_fields is None:
        return []
    if not isinstance(raw_fields, list):
        raise ValueError(f"{field} must be an array")
    results: list[dict[str, Any]] = []
    for index, item in enumerate(raw_fields):
        if not isinstance(item, dict):
            raise ValueError(f"{field}[{index}] must be an object")
        field_name = require_non_empty_string(item.get("fieldName") or item.get("field_name"), field=f"{field}[{index}].fieldName")
        field_code = require_camel_case(
            item.get("fieldCode") or item.get("field_code"),
            field=f"{field}[{index}].fieldCode",
        )
        data_type = require_non_empty_string(item.get("dataType") or item.get("data_type") or "string", field=f"{field}[{index}].dataType")
        action = normalize_text(item.get("action") or default_action).lower()
        if action not in DEFAULT_ALLOWED_ACTIONS:
            action = default_action
        results.append(
            {
                "fieldName": field_name,
                "fieldCode": field_code,
                "dataType": data_type,
                "action": action,
                "remark": normalize_text(item.get("remark")) or None,
            }
        )
    return results


def normalize_region(
    region: dict[str, Any],
    *,
    index: int,
    known_data_ai_ids: set[str],
) -> dict[str, Any]:
    data_ai_id = require_non_empty_string(
        region.get("data_ai_id") or region.get("dataAiId") or region.get("data-ai-id"),
        field=f"regions[{index}].data_ai_id",
    )
    if data_ai_id not in known_data_ai_ids:
        raise ValueError(f"regions[{index}].data_ai_id '{data_ai_id}' is not found in workspace HTML")

    action = normalize_action(region.get("action"), field=f"regions[{index}].action")
    action_id = require_camel_case(
        region.get("action_id") or region.get("actionId"),
        field=f"regions[{index}].action_id",
    )
    action_fields = normalize_action_fields(
        region.get("action_fields"),
        field=f"regions[{index}].action_fields",
        default_action=action,
    )

    normalized_region: dict[str, Any] = {
        "data_ai_id": data_ai_id,
        "action": action,
        "action_id": action_id,
        "action_fields": action_fields,
    }
    if normalize_text(region.get("section_name")):
        normalized_region["section_name"] = normalize_text(region.get("section_name"))
    if normalize_text(region.get("element_name")):
        normalized_region["element_name"] = normalize_text(region.get("element_name"))
    if region.get("section_code") is not None:
        normalized_region["section_code"] = require_camel_case(
            region.get("section_code"),
            field=f"regions[{index}].section_code",
        )
    if region.get("element_code") is not None:
        normalized_region["element_code"] = require_camel_case(
            region.get("element_code"),
            field=f"regions[{index}].element_code",
        )
    return normalized_region


def main() -> int:
    args = parse_args()
    prepare_path = Path(args.prepare_context).expanduser().resolve()
    agent_json_path = Path(args.agent_json).expanduser().resolve()

    prepare = safe_json_load(prepare_path)
    if not prepare:
        raise SystemExit(f"Invalid prepare context: {prepare_path}")
    workspace_dir = Path(str(prepare.get("workspace_dir") or "")).expanduser().resolve()
    workspace_html = Path(str(prepare.get("workspace_html") or "")).expanduser().resolve()
    if not workspace_html.exists():
        raise SystemExit(f"workspace_html not found: {workspace_html}")

    payload = read_json_object(agent_json_path)
    page_name = require_non_empty_string(payload.get("page_name"), field="page_name")
    page_code = require_camel_case(payload.get("page_code"), field="page_code")
    regions = payload.get("regions")
    if not isinstance(regions, list) or not regions:
        raise SystemExit("regions must be a non-empty array")

    _, by_data_ai_id, _ = parse_html_dom(workspace_html)
    known_data_ai_ids = set(by_data_ai_id.keys())

    normalized_regions: list[dict[str, Any]] = []
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            raise SystemExit(f"regions[{index}] must be an object")
        try:
            normalized_regions.append(
                normalize_region(region, index=index, known_data_ai_ids=known_data_ai_ids)
            )
        except ValueError as exc:
            raise SystemExit(str(exc))

    normalized_payload = {
        "page_name": page_name,
        "page_code": page_code,
        "regions": normalized_regions,
    }

    output_path = (
        Path(args.output).expanduser().resolve()
        if normalize_text(args.output)
        else (workspace_dir / "llm_output.json").resolve()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(normalized_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "ok": True,
        "validated_at": now_utc_iso(),
        "workspace_dir": str(workspace_dir),
        "workspace_html": str(workspace_html),
        "source_agent_json": str(agent_json_path),
        "output_path": str(output_path),
        "region_count": len(normalized_regions),
        "dropped_optional_keys": ["page_runtime_hints", "runtime_hints"],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
