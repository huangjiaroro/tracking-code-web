#!/usr/bin/env python3
"""
Verify runtime_browser_session artifacts against tracking schema coverage.

This verifier does not drive the browser itself. Instead, it inspects
`runtime_browser_sessions/` and checks whether exploratory sessions have
captured the schema events that should be reachable in the workspace copy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text, now_utc_iso, safe_json_load


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify exploratory runtime_browser_session artifacts against tracking_schema coverage.")
    parser.add_argument("--workspace-dir", required=True, help="Path to the tracking workspace session directory.")
    parser.add_argument("--schema-path", default="", help="Optional path to tracking_schema.json.")
    parser.add_argument("--output", default="", help="Output path. Default: <workspace-dir>/runtime_browser_verification.json")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    return parser.parse_args()


def resolve_existing_file(value: str) -> Path | None:
    text = normalize_text(value)
    if not text:
        return None
    candidate = Path(text).expanduser().resolve()
    return candidate if candidate.exists() else None


def resolve_schema_path(args: argparse.Namespace, workspace_dir: Path) -> Path:
    explicit = resolve_existing_file(args.schema_path)
    if explicit:
        return explicit
    return (workspace_dir / "tracking_schema.json").resolve()


def resolve_output_path(args: argparse.Namespace, workspace_dir: Path) -> Path:
    explicit = normalize_text(args.output)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (workspace_dir / "runtime_browser_verification.json").resolve()


def resolve_session_root(workspace_dir: Path) -> Path:
    return (workspace_dir / "runtime_browser_sessions").resolve()


def load_schema_events(schema: dict[str, Any]) -> list[dict[str, Any]]:
    events = schema.get("events") if isinstance(schema.get("events"), list) else []
    normalized: list[dict[str, Any]] = []
    for item in events:
        if not isinstance(item, dict):
            continue
        event_id = normalize_text(item.get("id"))
        if not event_id:
            continue
        normalized.append(
            {
                "id": event_id,
                "action": normalize_text(item.get("action")) or None,
                "element_name": normalize_text(item.get("element_name")) or None,
                "selector_candidates": item.get("selector_candidates") if isinstance(item.get("selector_candidates"), list) else [],
            }
        )
    return normalized


def normalize_captured_events_from_state(state_payload: dict[str, Any]) -> list[dict[str, Any]]:
    tracking = state_payload.get("tracking") if isinstance(state_payload.get("tracking"), dict) else {}
    captured_events = tracking.get("captured_events") if isinstance(tracking.get("captured_events"), list) else []
    normalized: list[dict[str, Any]] = []
    if captured_events:
        for item in captured_events:
            if not isinstance(item, dict):
                continue
            event_id = normalize_text(item.get("id"))
            if not event_id:
                continue
            normalized.append(
                {
                    "id": event_id,
                    "action": normalize_text(item.get("action")) or None,
                }
            )
        return normalized

    captured_event_ids = tracking.get("captured_event_ids") if isinstance(tracking.get("captured_event_ids"), list) else []
    for value in captured_event_ids:
        event_id = normalize_text(value)
        if not event_id:
            continue
        normalized.append({"id": event_id, "action": None})
    return normalized


def normalize_assertion_history(session_payload: dict[str, Any]) -> list[dict[str, Any]]:
    history = session_payload.get("assertion_history") if isinstance(session_payload.get("assertion_history"), list) else []
    normalized = [item for item in history if isinstance(item, dict)]
    if normalized:
        return normalized
    last_assertion = session_payload.get("last_assertion")
    return [last_assertion] if isinstance(last_assertion, dict) and last_assertion else []


def assertion_expected_event_ids(assertion: dict[str, Any]) -> list[str]:
    expected_reports = assertion.get("expected_reports") if isinstance(assertion.get("expected_reports"), list) else []
    return sorted(
        {
            normalize_text(item.get("id"))
            for item in expected_reports
            if isinstance(item, dict) and normalize_text(item.get("id"))
        }
    )


def build_next_action(*, failure_reason: str, workspace_dir: Path, uncovered_event_ids: list[str]) -> str:
    runtime_python = '.workspace/runtime-verify-venv/bin/python'
    preflight_command = (
        f"python3 scripts/prepare_runtime_browser_preflight.py "
        f'--workspace-dir "{workspace_dir}" --json'
    )
    start_command = (
        f"{runtime_python} scripts/runtime_browser_session.py start "
        f'--workspace-dir "{workspace_dir}" --session-id agent-loop --reset --json'
    )
    if failure_reason == "no_runtime_browser_sessions":
        return (
            "Review passed, but no runtime_browser_session artifacts were found. "
            f"Run `{preflight_command}` first, then initialize the runtime env with `python3 scripts/setup_runtime_verify_env.py --json`, "
            f"then run `{start_command}` "
            "and continue with `act` / `assert` until schema events are triggered."
        )
    if failure_reason == "no_reports_captured":
        return (
            "Review passed, but the existing runtime_browser_session artifacts have not captured any weblog reports yet. "
            "Use `runtime_browser_session.py act` to trigger real interactions. If one exploration pass still captures nothing, "
            "read `runtime_browser_preflight.json` or rerun the source preflight and retry with the real trigger path before rerunning this verifier."
        )
    if failure_reason == "schema_events_not_covered":
        preview = ", ".join(uncovered_event_ids[:5])
        suffix = "" if len(uncovered_event_ids) <= 5 else ", ..."
        return (
            "Review passed, but some schema events are still uncovered in runtime_browser_session artifacts. "
            f"After one exploration pass, read `runtime_browser_preflight.json` for each uncovered event_id or rerun `{preflight_command}` with targeted ids, locate the real `trackClick(...)` / `trackPageShow(...)` binding, "
            f"derive the trigger node, view, and prerequisite path, then use `runtime_browser_session.py act` / `assert` to capture the remaining events: {preview}{suffix}. "
            "Then rerun the validation gate."
        )
    return "Inspect runtime_browser_verification.json, continue runtime_browser_session exploration, then rerun the validation gate."


def main() -> int:
    args = parse_args()
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    output_path = resolve_output_path(args, workspace_dir)
    schema_path = resolve_schema_path(args, workspace_dir)
    schema = safe_json_load(schema_path)
    if not schema:
        raise SystemExit(f"Schema not found or invalid: {schema_path}")

    expected_events = load_schema_events(schema)
    session_root = resolve_session_root(workspace_dir)
    session_files = sorted(session_root.glob("*/session.json"))

    global_captured_ids: set[str] = set()
    global_captured_keys: set[tuple[str, str | None]] = set()
    sessions_summary: list[dict[str, Any]] = []
    state_count = 0
    matched_assertion_count = 0

    for session_file in session_files:
        session_payload = safe_json_load(session_file)
        if not isinstance(session_payload, dict) or not session_payload:
            continue
        session_dir = session_file.parent
        session_id = normalize_text(session_payload.get("session_id")) or session_dir.name
        state_files = sorted((session_dir / "states").glob("state_*.json"))
        state_count += len(state_files)
        session_captured_ids: set[str] = set()
        session_captured_keys: set[tuple[str, str | None]] = set()
        for state_file in state_files:
            state_payload = safe_json_load(state_file)
            if not isinstance(state_payload, dict) or not state_payload:
                continue
            for event in normalize_captured_events_from_state(state_payload):
                event_id = normalize_text(event.get("id"))
                action = normalize_text(event.get("action")) or None
                if not event_id:
                    continue
                session_captured_ids.add(event_id)
                session_captured_keys.add((event_id, action))
                global_captured_ids.add(event_id)
                global_captured_keys.add((event_id, action))

        assertion_history = normalize_assertion_history(session_payload)
        matched_assertions = [item for item in assertion_history if bool(item.get("matched"))]
        matched_assertion_count += len(matched_assertions)
        sessions_summary.append(
            {
                "session_id": session_id,
                "history_length": len(session_payload.get("history") or []) if isinstance(session_payload.get("history"), list) else 0,
                "state_count": len(state_files),
                "captured_event_ids": sorted(session_captured_ids),
                "matched_assertion_count": len(matched_assertions),
                "matched_assertion_event_ids": sorted(
                    {
                        event_id
                        for assertion in matched_assertions
                        for event_id in assertion_expected_event_ids(assertion)
                    }
                ),
                "last_state_path": normalize_text(session_payload.get("last_state_path")) or None,
                "last_screenshot_path": normalize_text(session_payload.get("last_screenshot_path")) or None,
                "last_assertion": session_payload.get("last_assertion") if isinstance(session_payload.get("last_assertion"), dict) else {},
                "session_file": str(session_file.resolve()),
            }
        )

    uncovered_events: list[dict[str, Any]] = []
    for event in expected_events:
        event_id = normalize_text(event.get("id"))
        action = normalize_text(event.get("action")) or None
        if not event_id:
            continue
        covered = False
        if global_captured_keys:
            covered = (event_id, action) in global_captured_keys or (event_id, None) in global_captured_keys
        if not covered:
            covered = event_id in global_captured_ids
        if not covered:
            uncovered_events.append(event)

    if not sessions_summary:
        failure_reason = "no_runtime_browser_sessions"
        status = "failed"
    elif not global_captured_ids:
        failure_reason = "no_reports_captured"
        status = "failed"
    elif uncovered_events:
        failure_reason = "schema_events_not_covered"
        status = "failed"
    else:
        failure_reason = None
        status = "passed"

    uncovered_event_ids = [normalize_text(item.get("id")) for item in uncovered_events if normalize_text(item.get("id"))]
    result = {
        "ok": status == "passed",
        "status": status,
        "generated_at": now_utc_iso(),
        "workspace_dir": str(workspace_dir),
        "schema_path": str(schema_path),
        "artifacts": {
            "runtime_browser_verification_json": str(output_path),
            "runtime_browser_sessions_dir": str(session_root),
            "runtime_browser_preflight_json": str((workspace_dir / "runtime_browser_preflight.json").resolve()),
        },
        "summary": {
            "session_count": len(sessions_summary),
            "state_count": state_count,
            "schema_event_count": len(expected_events),
            "captured_event_count": len(global_captured_ids),
            "covered_event_count": len(expected_events) - len(uncovered_events),
            "uncovered_event_ids": uncovered_event_ids,
            "matched_assertion_count": matched_assertion_count,
        },
        "captured_event_ids": sorted(global_captured_ids),
        "captured_events": [
            {
                "id": event_id,
                "action": action,
            }
            for event_id, action in sorted(global_captured_keys)
            if event_id
        ],
        "uncovered_events": uncovered_events,
        "sessions": sessions_summary,
        "failure_reason": failure_reason,
        "next_action": (
            "runtime_browser_session coverage passed. It is safe to treat runtime verification as complete."
            if status == "passed"
            else build_next_action(
                failure_reason=failure_reason or "unknown_failure",
                workspace_dir=workspace_dir,
                uncovered_event_ids=uncovered_event_ids,
            )
        ),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
