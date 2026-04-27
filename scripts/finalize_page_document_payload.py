#!/usr/bin/env python3
"""
Refresh page_document_save_payload.json with runtime element geometry and optionally save it.

This runs after runtime_browser_session coverage has passed. It keeps the existing tracking
document shape, but fills region rectangles from the latest headless browser snapshots so a
browser plugin can locate highlights more precisely than static selectors alone.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

from apply_llm_output import (
    find_element_catalog_candidate,
    find_field_catalog_candidate,
    find_section_catalog_candidate,
    http_post_json,
    infer_business_success,
    load_catalog_items,
    normalize_catalog_element,
    normalize_catalog_field,
    normalize_catalog_section,
    resolve_element_catalog_path,
    resolve_field_catalog_path,
    resolve_section_catalog_path,
)
from tracking_llm_utils import normalize_text, now_utc_iso, safe_json_load
from tracking_runtime_config import resolve_runtime_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finalize page_document_save_payload.json after runtime verification.")
    parser.add_argument("--workspace-dir", required=True, help="Workspace session directory.")
    parser.add_argument("--payload", default="", help="Payload path. Defaults to workspace page_document_save_payload.json.")
    parser.add_argument("--save", action="store_true", help="Call tracking/page_document/save after refreshing payload.")
    parser.add_argument("--tracking-env", default="", help="Optional tracking environment override.")
    parser.add_argument("--tracking-base-url", default="", help="Optional tracking API base URL override.")
    parser.add_argument("--save-endpoint", default="tracking/page_document/save", help="Save endpoint path.")
    parser.add_argument("--save-timeout", type=int, default=30, help="Save API timeout in seconds.")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = safe_json_load(path)
    if not payload:
        raise SystemExit(f"{label} not found or invalid: {path}")
    return payload


def latest_state_files(workspace_dir: Path) -> list[Path]:
    root = workspace_dir / "runtime_browser_sessions"
    return sorted(path.resolve() for path in root.glob("*/states/state_*.json") if path.is_file())


def css_escape_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def format_field_id(value: Any) -> Any:
    text = normalize_text(value)
    if text and re.fullmatch(r"\d+", text):
        return int(text)
    return text or None


def selector_keys(region: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    anchor = region.get("anchor") if isinstance(region.get("anchor"), dict) else {}
    data_ai_id = normalize_text(region.get("data_ai_id") or anchor.get("data-ai-id"))
    if data_ai_id:
        keys.add(f'[data-ai-id="{css_escape_value(data_ai_id)}"]')

    selectors = anchor.get("selector_candidates") if isinstance(anchor.get("selector_candidates"), list) else []
    for selector in selectors:
        text = normalize_text(selector)
        if text:
            keys.add(text)

    stable = anchor.get("stable_attributes") if isinstance(anchor.get("stable_attributes"), dict) else {}
    dom_id = normalize_text(stable.get("id"))
    if dom_id:
        keys.add(f"#{dom_id}")
    return keys


def iter_runtime_elements(state: dict[str, Any]) -> list[dict[str, Any]]:
    ui_state = state.get("ui_state") if isinstance(state.get("ui_state"), dict) else {}
    elements: list[dict[str, Any]] = []
    for key in ("clickable_elements", "active_elements"):
        raw_items = ui_state.get(key)
        if isinstance(raw_items, list):
            elements.extend(item for item in raw_items if isinstance(item, dict))
    return elements


def element_match_keys(element: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    selector = normalize_text(element.get("selector_hint"))
    if selector:
        keys.add(selector)
    data_ai_id = normalize_text(element.get("data_ai_id"))
    if data_ai_id:
        keys.add(f'[data-ai-id="{css_escape_value(data_ai_id)}"]')
    dom_id = normalize_text(element.get("id"))
    if dom_id:
        keys.add(f"#{dom_id}")
    return keys


def build_runtime_locator_index(workspace_dir: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for state_file in latest_state_files(workspace_dir):
        state = safe_json_load(state_file)
        if not state:
            continue
        page = state.get("page") if isinstance(state.get("page"), dict) else {}
        viewport = page.get("viewport") if isinstance(page.get("viewport"), dict) else {}
        for element in iter_runtime_elements(state):
            rect = element.get("rect") if isinstance(element.get("rect"), dict) else {}
            if not rect:
                continue
            locator = {
                "state_path": str(state_file),
                "generated_at": normalize_text(state.get("generated_at")) or None,
                "viewport": copy.deepcopy(viewport),
                "selector_hint": normalize_text(element.get("selector_hint")) or None,
                "data_ai_id": normalize_text(element.get("data_ai_id")) or None,
                "rect": {
                    "x": int(round(float(rect.get("x") or 0))),
                    "y": int(round(float(rect.get("y") or 0))),
                    "width": int(round(float(rect.get("width") or 0))),
                    "height": int(round(float(rect.get("height") or 0))),
                },
            }
            for key in element_match_keys(element):
                index[key] = locator
    return index


def normalized_box(rect: dict[str, Any], viewport: dict[str, Any]) -> dict[str, float]:
    width = max(1.0, float(viewport.get("width") or 1))
    height = max(1.0, float(viewport.get("height") or 1))
    return {
        "top_ratio": round(float(rect.get("y") or 0) / height, 6),
        "left_ratio": round(float(rect.get("x") or 0) / width, 6),
        "width_ratio": round(float(rect.get("width") or 0) / width, 6),
        "height_ratio": round(float(rect.get("height") or 0) / height, 6),
    }


def apply_locator(region: dict[str, Any], locator_index: dict[str, dict[str, Any]]) -> bool:
    locator = None
    for key in selector_keys(region):
        locator = locator_index.get(key)
        if locator:
            break
    if not locator:
        return False

    rect = locator.get("rect") if isinstance(locator.get("rect"), dict) else {}
    viewport = locator.get("viewport") if isinstance(locator.get("viewport"), dict) else {}
    region["region"] = {
        "top": rect.get("y", 0),
        "left": rect.get("x", 0),
        "width": rect.get("width", 0),
        "height": rect.get("height", 0),
    }
    region["normalized_box"] = normalized_box(rect, viewport)
    anchor = region.setdefault("anchor", {})
    if isinstance(anchor, dict):
        anchor["runtime_locator"] = locator
    region["locator_status"] = "runtime_resolved"
    return True


def refresh_payload(payload: dict[str, Any], workspace_dir: Path) -> dict[str, Any]:
    for local_placeholder_key, local_placeholder_value in (
        ("page_binding_id", "pb_local_file"),
        ("project_id", "ext-local-openclaw"),
    ):
        if normalize_text(payload.get(local_placeholder_key)) == local_placeholder_value:
            payload.pop(local_placeholder_key, None)

    locator_index = build_runtime_locator_index(workspace_dir)
    draft = payload.get("draft_document") if isinstance(payload.get("draft_document"), dict) else {}
    page_identity = draft.get("page_identity") if isinstance(draft.get("page_identity"), dict) else {}
    draft["page_identity"] = page_identity
    page_identity["origin"] = "127.0.0.1"
    url_name = Path(normalize_text(page_identity.get("url"))).name
    if not url_name:
        prepare = safe_json_load(workspace_dir / "prepare_context.json")
        url_name = Path(normalize_text(prepare.get("workspace_html"))).name
    if url_name:
        page_identity["url"] = url_name
        page_identity.setdefault("route_key", url_name)
        page_identity.setdefault("route_pattern", f"^{url_name}$")

    regions = draft.get("regions") if isinstance(draft.get("regions"), list) else []
    resolved = 0
    for region in regions:
        if isinstance(region, dict) and apply_locator(region, locator_index):
            resolved += 1

    change_set = payload.get("change_set") if isinstance(payload.get("change_set"), dict) else {}
    payload["change_set"] = change_set
    change_set["added_regions"] = [copy.deepcopy(region) for region in regions if isinstance(region, dict)]
    change_set["updated_regions"] = []
    change_set.setdefault("deleted_region_ids", [])
    change_set.setdefault("rebound_regions", [])

    draft["finalized_at"] = now_utc_iso()
    draft["locator_resolution"] = {
        "status": "runtime_resolved" if resolved else "unresolved",
        "resolved_region_count": resolved,
        "region_count": len([region for region in regions if isinstance(region, dict)]),
        "runtime_locator_count": len(locator_index),
    }
    return {
        "resolved_region_count": resolved,
        "region_count": draft["locator_resolution"]["region_count"],
        "runtime_locator_count": len(locator_index),
    }


def load_catalogs(workspace_dir: Path) -> dict[str, list[dict[str, Any]]]:
    prepare = safe_json_load(workspace_dir / "prepare_context.json")
    if not prepare:
        return {"sections": [], "elements": [], "fields": []}
    return {
        "sections": [normalize_catalog_section(item) for item in load_catalog_items(resolve_section_catalog_path(prepare))],
        "elements": [normalize_catalog_element(item) for item in load_catalog_items(resolve_element_catalog_path(prepare))],
        "fields": [normalize_catalog_field(item) for item in load_catalog_items(resolve_field_catalog_path(prepare))],
    }


def enrich_region_catalog_ids(region: dict[str, Any], catalogs: dict[str, list[dict[str, Any]]]) -> None:
    section_candidate = find_section_catalog_candidate(
        catalogs.get("sections") or [],
        region.get("section_id") or region.get("sectionId"),
        region.get("section_code") or region.get("sectionCode"),
        region.get("section_name") or region.get("sectionName"),
    )
    section_id = normalize_text(region.get("section_id") or region.get("sectionId") or section_candidate.get("section_id"))
    if section_id:
        region["section_id"] = section_id

    element_candidate = find_element_catalog_candidate(
        catalogs.get("elements") or [],
        region.get("element_id") or region.get("elementId"),
        region.get("element_code") or region.get("elementCode"),
        region.get("element_name") or region.get("elementName"),
    )
    element_id = normalize_text(region.get("element_id") or region.get("elementId") or element_candidate.get("element_id"))
    if element_id:
        region["element_id"] = element_id

    action_fields = region.get("action_fields") if isinstance(region.get("action_fields"), list) else []
    for field in action_fields:
        if not isinstance(field, dict):
            continue
        field_candidate = find_field_catalog_candidate(
            catalogs.get("fields") or [],
            field.get("id") or field.get("field_id") or field.get("fieldId"),
            field.get("fieldCode") or field.get("field_code") or field.get("code"),
            field.get("fieldName") or field.get("field_name") or field.get("name"),
        )
        field_id = normalize_text(field.get("id") or field.get("field_id") or field.get("fieldId") or field_candidate.get("field_id"))
        field.pop("field_id", None)
        field.pop("fieldId", None)
        if field_id:
            field["id"] = format_field_id(field_id)


def enrich_payload_catalog_ids(payload: dict[str, Any], workspace_dir: Path) -> dict[str, Any]:
    catalogs = load_catalogs(workspace_dir)
    draft = payload.get("draft_document") if isinstance(payload.get("draft_document"), dict) else {}
    regions = draft.get("regions") if isinstance(draft.get("regions"), list) else []
    for region in regions:
        if isinstance(region, dict):
            enrich_region_catalog_ids(region, catalogs)

    change_set = payload.get("change_set") if isinstance(payload.get("change_set"), dict) else {}
    for key in ("added_regions", "updated_regions", "rebound_regions"):
        items = change_set.get(key) if isinstance(change_set.get(key), list) else []
        for region in items:
            if isinstance(region, dict):
                enrich_region_catalog_ids(region, catalogs)

    return {
        "section_catalog_count": len(catalogs.get("sections") or []),
        "element_catalog_count": len(catalogs.get("elements") or []),
        "field_catalog_count": len(catalogs.get("fields") or []),
    }


def save_payload(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    runtime_config = resolve_runtime_config(
        repo_root(),
        overrides={
            "tracking_env": normalize_text(args.tracking_env),
            "tracking_base_url": normalize_text(args.tracking_base_url),
        },
    )
    base_url = normalize_text(args.tracking_base_url or runtime_config.get("tracking_base_url"))
    if not base_url:
        raise SystemExit("tracking_base_url is missing.")
    response = http_post_json(
        base_url=base_url,
        endpoint=normalize_text(args.save_endpoint) or "tracking/page_document/save",
        body=payload,
        cert_path=normalize_text(runtime_config.get("cert_path")) or None,
        cert_password=normalize_text(runtime_config.get("cert_password")) or None,
        timeout=max(1, int(args.save_timeout)),
    )
    return {
        "base_url": base_url,
        "endpoint": normalize_text(args.save_endpoint) or "tracking/page_document/save",
        "response": response,
        "business_success": infer_business_success(response),
    }


def main() -> int:
    args = parse_args()
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    payload_path = Path(args.payload).expanduser().resolve() if normalize_text(args.payload) else workspace_dir / "page_document_save_payload.json"
    payload = load_json(payload_path, label="page document save payload")
    catalog_result = enrich_payload_catalog_ids(payload, workspace_dir)
    locator_result = refresh_payload(payload, workspace_dir)
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    draft_document_path = workspace_dir / "draft_document.json"
    change_set_path = workspace_dir / "change_set.json"
    if isinstance(payload.get("draft_document"), dict):
        draft_document_path.write_text(json.dumps(payload["draft_document"], ensure_ascii=False, indent=2), encoding="utf-8")
    if isinstance(payload.get("change_set"), dict):
        change_set_path.write_text(json.dumps(payload["change_set"], ensure_ascii=False, indent=2), encoding="utf-8")

    save_result = None
    save_response_path = None
    if args.save:
        save_result = save_payload(args, payload)
        save_response_path = workspace_dir / "final_save_api_response.json"
        save_response_path.write_text(json.dumps(save_result["response"], ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "ok": save_result is None or save_result.get("business_success") is not False,
        "payload_path": str(payload_path),
        "draft_document_path": str(draft_document_path),
        "change_set_path": str(change_set_path),
        "catalog_result": catalog_result,
        "locator_result": locator_result,
        "save_api_called": bool(args.save),
        "save_api_disabled": not bool(args.save),
        "save_api_disabled_reason": "dry_run_no_save_flag" if not args.save else None,
        "save_api_response_path": str(save_response_path) if save_response_path else None,
        "save_api_business_success": save_result.get("business_success") if save_result else None,
        "save_api_base_url": save_result.get("base_url") if save_result else None,
        "save_api_endpoint": save_result.get("endpoint") if save_result else normalize_text(args.save_endpoint),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
