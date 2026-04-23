#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text, now_utc_iso, safe_json_load


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate agent-provided app/business recommendation against local catalogs."
    )
    parser.add_argument("--prepare-context", required=True, help="Path to prepare_context.json.")
    parser.add_argument("--agent-json", required=True, help="Path to agent recommendation JSON.")
    parser.add_argument(
        "--output",
        default="",
        help="Output path. Default: <workspace_dir>/app_business_recommendation.json",
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
    if isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    if isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    return []


def resolve_app_catalog_path(prepare: dict[str, Any], workspace_dir: Path) -> Path:
    app_catalog = prepare.get("app_catalog") if isinstance(prepare.get("app_catalog"), dict) else {}
    explicit = normalize_text(app_catalog.get("path"))
    return Path(explicit).expanduser().resolve() if explicit else (workspace_dir / "all_apps_catalog.json").resolve()


def resolve_business_catalog_path(prepare: dict[str, Any], workspace_dir: Path) -> Path:
    business_catalog = prepare.get("business_catalog") if isinstance(prepare.get("business_catalog"), dict) else {}
    explicit = normalize_text(business_catalog.get("path"))
    return (
        Path(explicit).expanduser().resolve()
        if explicit
        else (workspace_dir / "all_business_lines_catalog.json").resolve()
    )


def normalize_app_item(item: dict[str, Any]) -> dict[str, str]:
    return {
        "app_id": normalize_text(item.get("app_id") or item.get("appId") or item.get("id")),
        "app_code": normalize_text(item.get("app_code") or item.get("appCode") or item.get("app_sign") or item.get("appSign")),
        "app_name": normalize_text(item.get("app_name") or item.get("appName") or item.get("name")),
    }


def normalize_business_item(item: dict[str, Any]) -> dict[str, str]:
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
    }


def normalize_recommendation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    recommendation = payload.get("recommendation") if isinstance(payload.get("recommendation"), dict) else payload
    alternatives = payload.get("alternatives")
    if not isinstance(alternatives, list):
        alternatives = []
    return {
        "recommendation": recommendation,
        "alternatives": [item for item in alternatives if isinstance(item, dict)],
    }


def resolve_app_record(app_items: list[dict[str, str]], app_id: str, app_code: str) -> dict[str, str]:
    normalized_app_id = normalize_text(app_id)
    normalized_app_code = normalize_text(app_code).lower()

    by_id = {}
    by_code = {}
    for item in app_items:
        if item.get("app_id"):
            by_id[item["app_id"]] = item
        if item.get("app_code"):
            by_code[item["app_code"].lower()] = item

    record_id = by_id.get(normalized_app_id) if normalized_app_id else None
    record_code = by_code.get(normalized_app_code) if normalized_app_code else None

    if not record_id:
        raise ValueError(f"app_id not found in app catalog: {normalized_app_id or '<empty>'}")
    if not record_code:
        raise ValueError(f"app_code not found in app catalog: {normalized_app_code or '<empty>'}")
    if record_id != record_code:
        raise ValueError("app_id and app_code point to different app records")
    return record_id


def resolve_business_record(
    business_items: list[dict[str, str]],
    business_code: str,
    app_id: str,
) -> dict[str, str]:
    normalized_business_code = normalize_text(business_code).lower()
    if not normalized_business_code:
        raise ValueError("business_code is required")

    for item in business_items:
        item_code = normalize_text(item.get("business_code")).lower()
        if item_code != normalized_business_code:
            continue
        bound_app_id = normalize_text(item.get("app_id"))
        if bound_app_id and bound_app_id != normalize_text(app_id):
            raise ValueError(
                f"business_code '{business_code}' belongs to app_id '{bound_app_id}', not '{app_id}'"
            )
        return item
    raise ValueError(f"business_code not found in business catalog: {business_code}")


def validate_candidate(
    candidate: dict[str, Any],
    app_items: list[dict[str, str]],
    business_items: list[dict[str, str]],
    *,
    require_reason: bool,
) -> dict[str, Any]:
    app_id = normalize_text(candidate.get("app_id") or candidate.get("appId"))
    app_code = normalize_text(candidate.get("app_code") or candidate.get("appCode"))
    business_code = normalize_text(candidate.get("business_code") or candidate.get("businessCode"))
    reason = normalize_text(candidate.get("reason"))

    if not app_id:
        raise ValueError("recommendation.app_id is required")
    if not app_code:
        raise ValueError("recommendation.app_code is required")
    if not business_code:
        raise ValueError("recommendation.business_code is required")
    if require_reason and not reason:
        raise ValueError("recommendation.reason is required")

    app_record = resolve_app_record(app_items, app_id, app_code)
    business_record = resolve_business_record(business_items, business_code, app_record.get("app_id", ""))

    return {
        "app_id": app_record.get("app_id"),
        "app_code": app_record.get("app_code"),
        "app_name": app_record.get("app_name") or None,
        "business_code": business_record.get("business_code"),
        "business_line": business_record.get("business_line") or None,
        "reason": reason or None,
    }


def main() -> int:
    args = parse_args()
    prepare_path = Path(args.prepare_context).expanduser().resolve()
    agent_json_path = Path(args.agent_json).expanduser().resolve()

    prepare = safe_json_load(prepare_path)
    if not prepare:
        raise SystemExit(f"Invalid prepare context: {prepare_path}")

    workspace_dir = Path(str(prepare.get("workspace_dir") or "")).expanduser().resolve()
    if not normalize_text(str(workspace_dir)):
        raise SystemExit("prepare_context.workspace_dir is missing")
    workspace_dir.mkdir(parents=True, exist_ok=True)

    app_catalog_path = resolve_app_catalog_path(prepare, workspace_dir)
    business_catalog_path = resolve_business_catalog_path(prepare, workspace_dir)
    app_items = [normalize_app_item(item) for item in load_catalog_items(app_catalog_path)]
    business_items = [normalize_business_item(item) for item in load_catalog_items(business_catalog_path)]

    if not app_items:
        raise SystemExit(f"App catalog is empty or invalid: {app_catalog_path}")
    if not business_items:
        raise SystemExit(f"Business catalog is empty or invalid: {business_catalog_path}")

    payload = read_json_object(agent_json_path)
    normalized = normalize_recommendation_payload(payload)
    recommendation = normalized["recommendation"]
    alternatives = normalized["alternatives"]

    if not isinstance(recommendation, dict):
        raise SystemExit("Agent payload must contain a recommendation object.")

    try:
        validated_recommendation = validate_candidate(
            recommendation,
            app_items,
            business_items,
            require_reason=True,
        )
    except ValueError as exc:
        raise SystemExit(f"Invalid recommendation: {exc}")

    validated_alternatives: list[dict[str, Any]] = []
    for item in alternatives[:3]:
        try:
            validated_alternatives.append(
                validate_candidate(item, app_items, business_items, require_reason=False)
            )
        except ValueError:
            continue

    result = {
        "ok": True,
        "validated_at": now_utc_iso(),
        "session_id": prepare.get("session_id"),
        "workspace_dir": str(workspace_dir),
        "app_catalog_path": str(app_catalog_path),
        "business_catalog_path": str(business_catalog_path),
        "recommended": validated_recommendation,
        "alternatives": validated_alternatives,
        "source_agent_json": str(agent_json_path),
    }

    output_path = (
        Path(args.output).expanduser().resolve()
        if normalize_text(args.output)
        else (workspace_dir / "app_business_recommendation.json").resolve()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(output_path)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
