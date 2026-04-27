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


def load_catalog_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = safe_json_load(path)
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def resolve_catalog_path(prepare: dict[str, Any], workspace_dir: Path, key: str, default_name: str) -> Path:
    catalog = prepare.get(key) if isinstance(prepare.get(key), dict) else {}
    path_text = normalize_text(catalog.get("path"))
    if path_text:
        return Path(path_text).expanduser().resolve()
    return (workspace_dir / default_name).resolve()


def normalize_catalog_section(item: dict[str, Any]) -> dict[str, str | None]:
    return {
        "section_id": normalize_text(item.get("section_id") or item.get("sectionId") or item.get("id")) or None,
        "section_name": normalize_text(item.get("section_name") or item.get("sectionName") or item.get("functionName") or item.get("name")) or None,
        "section_code": normalize_text(item.get("section_code") or item.get("sectionCode") or item.get("functionCode") or item.get("code")) or None,
    }


def normalize_catalog_element(item: dict[str, Any]) -> dict[str, str | None]:
    return {
        "element_id": normalize_text(item.get("element_id") or item.get("elementId") or item.get("id")) or None,
        "element_name": normalize_text(item.get("element_name") or item.get("elementName") or item.get("controlName") or item.get("name")) or None,
        "element_code": normalize_text(item.get("element_code") or item.get("elementCode") or item.get("controlCode") or item.get("code")) or None,
    }


def normalize_catalog_field(item: dict[str, Any]) -> dict[str, str | None]:
    return {
        "field_id": normalize_text(item.get("field_id") or item.get("fieldId") or item.get("id")) or None,
        "field_name": normalize_text(item.get("field_name") or item.get("fieldName") or item.get("name")) or None,
        "field_code": normalize_text(item.get("field_code") or item.get("fieldCode") or item.get("code")) or None,
        "data_type": normalize_text(item.get("data_type") or item.get("dataType") or item.get("type")) or None,
        "action": normalize_text(item.get("action")) or None,
        "remark": normalize_text(item.get("remark")) or None,
    }


def find_catalog_entry(items: list[dict[str, str | None]], id_key: str, id_value: str) -> dict[str, str | None]:
    normalized_id = normalize_text(id_value)
    if not normalized_id:
        return {}
    for item in items:
        if normalize_text(item.get(id_key)) == normalized_id:
            return item
    return {}


def find_field_catalog_candidate(
    catalog_fields: list[dict[str, str | None]],
    field_id: Any,
    field_code: Any,
    field_name: Any,
) -> dict[str, str | None]:
    normalized_field_id = normalize_text(field_id)
    normalized_field_code = normalize_text(field_code).lower()
    normalized_field_name = normalize_text(field_name).lower()
    if normalized_field_id:
        match = find_catalog_entry(catalog_fields, "field_id", normalized_field_id)
        if match:
            return match
    if normalized_field_code:
        for item in catalog_fields:
            if normalize_text(item.get("field_code")).lower() == normalized_field_code:
                return item
    if normalized_field_name:
        for item in catalog_fields:
            if normalize_text(item.get("field_name")).lower() == normalized_field_name:
                return item
    return {}


def format_field_id(value: Any) -> Any:
    text = normalize_text(value)
    if text and re.fullmatch(r"\d+", text):
        return int(text)
    return text or None


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


def normalize_action_fields(
    raw_fields: Any,
    *,
    field: str,
    default_action: str,
    catalog_fields: list[dict[str, str | None]],
) -> list[dict[str, Any]]:
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
        field_id = normalize_text(item.get("id") or item.get("field_id") or item.get("fieldId"))
        catalog_match = find_field_catalog_candidate(catalog_fields, field_id, field_code, field_name)
        data_type = require_non_empty_string(
            item.get("dataType") or item.get("data_type") or catalog_match.get("data_type") or "string",
            field=f"{field}[{index}].dataType",
        )
        action = normalize_text(item.get("action") or catalog_match.get("action") or default_action)
        normalized_item = {
            "fieldName": normalize_text(catalog_match.get("field_name")) or field_name,
            "fieldCode": normalize_text(catalog_match.get("field_code")) or field_code,
            "dataType": data_type,
            "action": action,
            "remark": normalize_text(item.get("remark") or catalog_match.get("remark")) or None,
        }
        if field_id:
            if not catalog_match:
                raise ValueError(f"{field}[{index}].id '{field_id}' is not found in all_fields_catalog.json")
            catalog_name = normalize_text(catalog_match.get("field_name"))
            catalog_code = normalize_text(catalog_match.get("field_code"))
            if catalog_name and field_name and catalog_name != field_name:
                raise ValueError(
                    f"{field}[{index}] id '{field_id}' conflicts with fieldName '{field_name}' (catalog: '{catalog_name}')"
                )
            if catalog_code and field_code and catalog_code != field_code:
                raise ValueError(
                    f"{field}[{index}] id '{field_id}' conflicts with fieldCode '{field_code}' (catalog: '{catalog_code}')"
                )
        resolved_field_id = field_id or normalize_text(catalog_match.get("field_id"))
        if resolved_field_id:
            normalized_item["id"] = format_field_id(resolved_field_id)
        results.append(normalized_item)
    return results


def normalize_region(
    region: dict[str, Any],
    *,
    index: int,
    known_data_ai_ids: set[str],
    catalog_sections: list[dict[str, str | None]],
    catalog_elements: list[dict[str, str | None]],
    catalog_fields: list[dict[str, str | None]],
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
        catalog_fields=catalog_fields,
    )

    normalized_region: dict[str, Any] = {
        "data_ai_id": data_ai_id,
        "action": action,
        "action_id": action_id,
        "action_fields": action_fields,
    }
    if normalize_text(region.get("region_id")):
        normalized_region["region_id"] = normalize_text(region.get("region_id"))
    if normalize_text(region.get("id")):
        normalized_region["id"] = normalize_text(region.get("id"))
    if normalize_text(region.get("section_name")):
        normalized_region["section_name"] = normalize_text(region.get("section_name"))
    if normalize_text(region.get("element_name")):
        normalized_region["element_name"] = normalize_text(region.get("element_name"))
    section_id = normalize_text(region.get("section_id") or region.get("sectionId"))
    if section_id:
        catalog_match = find_catalog_entry(catalog_sections, "section_id", section_id)
        if not catalog_match:
            raise ValueError(f"regions[{index}].section_id '{section_id}' is not found in all_sections_catalog.json")
        catalog_name = normalize_text(catalog_match.get("section_name"))
        catalog_code = normalize_text(catalog_match.get("section_code"))
        region_name = normalize_text(region.get("section_name"))
        region_code = normalize_text(region.get("section_code"))
        if catalog_name and region_name and catalog_name != region_name:
            raise ValueError(
                f"regions[{index}].section_id '{section_id}' conflicts with section_name '{region_name}' (catalog: '{catalog_name}')"
            )
        if catalog_code and region_code and catalog_code != region_code:
            raise ValueError(
                f"regions[{index}].section_id '{section_id}' conflicts with section_code '{region_code}' (catalog: '{catalog_code}')"
            )
        normalized_region["section_id"] = section_id
    if region.get("section_code") is not None:
        normalized_region["section_code"] = require_camel_case(
            region.get("section_code"),
            field=f"regions[{index}].section_code",
        )
    element_id = normalize_text(region.get("element_id") or region.get("elementId"))
    if element_id:
        catalog_match = find_catalog_entry(catalog_elements, "element_id", element_id)
        if not catalog_match:
            raise ValueError(f"regions[{index}].element_id '{element_id}' is not found in all_elements_catalog.json")
        catalog_name = normalize_text(catalog_match.get("element_name"))
        catalog_code = normalize_text(catalog_match.get("element_code"))
        region_name = normalize_text(region.get("element_name"))
        region_code = normalize_text(region.get("element_code"))
        if catalog_name and region_name and catalog_name != region_name:
            raise ValueError(
                f"regions[{index}].element_id '{element_id}' conflicts with element_name '{region_name}' (catalog: '{catalog_name}')"
            )
        if catalog_code and region_code and catalog_code != region_code:
            raise ValueError(
                f"regions[{index}].element_id '{element_id}' conflicts with element_code '{region_code}' (catalog: '{catalog_code}')"
            )
        normalized_region["element_id"] = element_id
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
    section_catalog_path = resolve_catalog_path(prepare, workspace_dir, "section_catalog", "all_sections_catalog.json")
    element_catalog_path = resolve_catalog_path(prepare, workspace_dir, "element_catalog", "all_elements_catalog.json")
    field_catalog_path = resolve_catalog_path(prepare, workspace_dir, "field_catalog", "all_fields_catalog.json")
    catalog_sections = [normalize_catalog_section(item) for item in load_catalog_items(section_catalog_path)]
    catalog_elements = [normalize_catalog_element(item) for item in load_catalog_items(element_catalog_path)]
    catalog_fields = [normalize_catalog_field(item) for item in load_catalog_items(field_catalog_path)]

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
                normalize_region(
                    region,
                    index=index,
                    known_data_ai_ids=known_data_ai_ids,
                    catalog_sections=catalog_sections,
                    catalog_elements=catalog_elements,
                    catalog_fields=catalog_fields,
                )
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
