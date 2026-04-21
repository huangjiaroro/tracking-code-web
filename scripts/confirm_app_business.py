#!/usr/bin/env python3
"""
Confirm or override app/business selection and persist it for later steps.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text, now_utc_iso, safe_json_load


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Confirm app and business for tracking session.")
    parser.add_argument(
        "--prepare-context",
        required=True,
        help="Path to prepare_context.json from prepare_tracking_context.py.",
    )
    parser.add_argument("--app-id", default="", help="Confirmed app_id.")
    parser.add_argument("--app-code", default="", help="Confirmed app_code.")
    parser.add_argument("--app-name", default="", help="Optional app name.")
    parser.add_argument("--business-code", default="", help="Confirmed business_code.")
    parser.add_argument("--business-line", default="", help="Optional business display name.")
    parser.add_argument(
        "--output",
        default="",
        help="Output json path. Default: <workspace_dir>/app_business_confirm.json",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser.parse_args()


def load_catalog_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        items = parsed.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def normalize_app_candidate(item: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "app_id": normalize_text(item.get("app_id") or item.get("appId") or item.get("id")),
        "app_code": normalize_text(item.get("app_code") or item.get("appCode") or item.get("app_sign") or item.get("appSign")),
        "app_name": normalize_text(item.get("app_name") or item.get("appName") or item.get("name")),
        "app_key": normalize_text(item.get("app_key") or item.get("appKey")),
        "_source": source,
    }


def normalize_business_candidate(item: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "business_code": normalize_text(
            item.get("business_code") or item.get("businessCode") or item.get("biz_code") or item.get("bizCode")
        ),
        "business_line": normalize_text(
            item.get("business_line")
            or item.get("businessLine")
            or item.get("business_name")
            or item.get("businessName")
            or item.get("name")
            or item.get("label")
            or item.get("text")
        ),
        "app_id": normalize_text(item.get("app_id") or item.get("appId")),
        "_source": source,
    }


def dedupe_candidates(candidates: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    result: list[dict[str, Any]] = []
    for item in candidates:
        key = tuple(normalize_text(item.get(field)).lower() for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def find_app_candidate(candidates: list[dict[str, Any]], app_id: str, app_code: str, app_name: str) -> tuple[dict[str, Any], str]:
    normalized_app_id = normalize_text(app_id)
    normalized_app_code = normalize_text(app_code).lower()
    normalized_app_name = normalize_text(app_name).lower()

    if normalized_app_id:
        for item in candidates:
            if normalize_text(item.get("app_id")) == normalized_app_id:
                return item, normalize_text(item.get("_source"))
    if normalized_app_code:
        for item in candidates:
            if normalize_text(item.get("app_code")).lower() == normalized_app_code:
                return item, normalize_text(item.get("_source"))
    if normalized_app_name:
        for item in candidates:
            if normalize_text(item.get("app_name")).lower() == normalized_app_name:
                return item, normalize_text(item.get("_source"))
    return {}, ""


def find_business_candidate(
    candidates: list[dict[str, Any]],
    business_code: str,
    business_line: str,
) -> tuple[dict[str, Any], str]:
    normalized_business_code = normalize_text(business_code).lower()
    normalized_business_line = normalize_text(business_line).lower()

    if normalized_business_code:
        for item in candidates:
            if normalize_text(item.get("business_code")).lower() == normalized_business_code:
                return item, normalize_text(item.get("_source"))
    if normalized_business_line:
        for item in candidates:
            if normalize_text(item.get("business_line")).lower() == normalized_business_line:
                return item, normalize_text(item.get("_source"))
    return {}, ""


def resolve_app_catalog_path(prepare: dict[str, Any], workspace_dir: Path) -> Path:
    app_catalog = prepare.get("app_catalog") if isinstance(prepare.get("app_catalog"), dict) else {}
    path_text = normalize_text(app_catalog.get("path"))
    if path_text:
        return Path(path_text).expanduser().resolve()
    return workspace_dir / "all_apps_catalog.json"


def resolve_business_catalog_path(prepare: dict[str, Any], workspace_dir: Path) -> Path:
    business_catalog = prepare.get("business_catalog") if isinstance(prepare.get("business_catalog"), dict) else {}
    path_text = normalize_text(business_catalog.get("path"))
    if path_text:
        return Path(path_text).expanduser().resolve()
    return workspace_dir / "all_business_lines_catalog.json"


def resolve_selection_source(
    manual_values: list[str],
    matched_source: str,
    fallback: str,
) -> str:
    if any(normalize_text(value) for value in manual_values):
        return "manual_input"
    return normalize_text(matched_source) or fallback


def main() -> int:
    args = parse_args()
    prepare_path = Path(args.prepare_context).expanduser().resolve()
    prepare = safe_json_load(prepare_path)
    if not prepare:
        raise SystemExit(f"Invalid prepare context: {prepare_path}")

    workspace_dir = Path(str(prepare.get("workspace_dir"))).expanduser().resolve()
    app_rec = prepare.get("app_recommendation") if isinstance(prepare.get("app_recommendation"), dict) else {}
    app_rec_item = app_rec.get("recommended") if isinstance(app_rec.get("recommended"), dict) else {}
    app_candidates_raw = app_rec.get("candidates") if isinstance(app_rec.get("candidates"), list) else []
    app_catalog_path = resolve_app_catalog_path(prepare, workspace_dir)
    app_catalog_items_raw = load_catalog_items(app_catalog_path)
    app_candidates = dedupe_candidates(
        [normalize_app_candidate(item, "recommendation_candidates") for item in app_candidates_raw if isinstance(item, dict)]
        + [normalize_app_candidate(item, "catalog") for item in app_catalog_items_raw if isinstance(item, dict)],
        ("app_id", "app_code", "app_name"),
    )

    biz_rec = (
        prepare.get("business_line_recommendation")
        if isinstance(prepare.get("business_line_recommendation"), dict)
        else {}
    )
    biz_rec_item = biz_rec.get("recommended") if isinstance(biz_rec.get("recommended"), dict) else {}
    biz_candidates_raw = biz_rec.get("candidates") if isinstance(biz_rec.get("candidates"), list) else []
    business_catalog_path = resolve_business_catalog_path(prepare, workspace_dir)
    business_catalog_items_raw = load_catalog_items(business_catalog_path)
    biz_candidates = dedupe_candidates(
        [
            normalize_business_candidate(item, "recommendation_candidates")
            for item in biz_candidates_raw
            if isinstance(item, dict)
        ]
        + [
            normalize_business_candidate(item, "catalog")
            for item in business_catalog_items_raw
            if isinstance(item, dict)
        ],
        ("business_code", "business_line", "app_id"),
    )

    manual_app_input = any(normalize_text(value) for value in [args.app_id, args.app_code, args.app_name])
    manual_business_input = any(normalize_text(value) for value in [args.business_code, args.business_line])

    input_app_id = normalize_text(args.app_id)
    input_app_code = normalize_text(args.app_code)
    input_app_name = normalize_text(args.app_name)
    app_id = input_app_id if manual_app_input else normalize_text(app_rec_item.get("app_id"))
    app_code = input_app_code if manual_app_input else normalize_text(app_rec_item.get("app_code"))
    app_name = input_app_name if manual_app_input else normalize_text(app_rec_item.get("app_name"))
    app_from_candidates, app_match_source = find_app_candidate(app_candidates, app_id, app_code, app_name)
    if not app_name:
        app_name = normalize_text(app_from_candidates.get("app_name"))
    if not app_code:
        app_code = normalize_text(app_from_candidates.get("app_code"))
    if not app_id:
        app_id = normalize_text(app_from_candidates.get("app_id"))
    app_key = normalize_text(app_from_candidates.get("app_key"))
    app_selection_source = resolve_selection_source(
        [args.app_id, args.app_code, args.app_name],
        app_match_source,
        "recommended",
    )

    input_business_code = normalize_text(args.business_code)
    input_business_line = normalize_text(args.business_line)
    business_code = input_business_code if manual_business_input else normalize_text(biz_rec_item.get("business_code"))
    business_line = input_business_line if manual_business_input else normalize_text(biz_rec_item.get("business_line"))
    biz_from_candidates, business_match_source = find_business_candidate(
        biz_candidates,
        business_code,
        business_line,
    )
    if not business_code:
        business_code = normalize_text(biz_from_candidates.get("business_code"))
    if not business_line:
        business_line = normalize_text(biz_from_candidates.get("business_line"))
    business_selection_source = resolve_selection_source(
        [args.business_code, args.business_line],
        business_match_source,
        "recommended",
    )

    output_path = (
        Path(args.output).expanduser().resolve()
        if normalize_text(args.output)
        else workspace_dir / "app_business_confirm.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "ok": True,
        "confirmed_at": now_utc_iso(),
        "session_id": prepare.get("session_id"),
        "workspace_dir": str(workspace_dir),
        "workspace_html": prepare.get("workspace_html"),
        "source_html": prepare.get("source_html"),
        "app_id": app_id or None,
        "app_code": app_code or None,
        "app_name": app_name or None,
        "app_key": app_key or None,
        "business_code": business_code or None,
        "business_line": business_line or None,
        "app_catalog_path": str(app_catalog_path),
        "business_catalog_path": str(business_catalog_path),
        "selection_source": {
            "app": app_selection_source,
            "business": business_selection_source,
        },
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(output_path)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
