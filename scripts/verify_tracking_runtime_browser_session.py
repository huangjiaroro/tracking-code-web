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
import re
from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text, now_utc_iso, parse_html_dom, safe_json_load


DATA_AI_SELECTOR_RE = re.compile(r'^(?:[A-Za-z][A-Za-z0-9_-]*)?\[data-ai-id="(?P<value>(?:\\.|[^"])*)"\]$')
ID_SELECTOR_RE = re.compile(r"^#(?P<id>[A-Za-z][A-Za-z0-9_:\-\.]*)$")
SOURCE_SIGNAL_PATTERNS = (
    ("window.location", "navigation_call_near_track"),
    ("location.href", "navigation_call_near_track"),
    ("requestsubmit(", "form_submit_near_track"),
    ("submit(", "form_submit_near_track"),
    ("goto(", "view_transition_near_track"),
    ("switchview(", "view_transition_near_track"),
    ("settimeout(", "timed_followup_near_track"),
)


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


def resolve_runtime_preflight_path(workspace_dir: Path) -> Path:
    return (workspace_dir / "runtime_browser_preflight.json").resolve()


def load_runtime_preflight_index(workspace_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    artifact = safe_json_load(resolve_runtime_preflight_path(workspace_dir))
    items = artifact.get("items") if isinstance(artifact.get("items"), list) else []
    index: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        event_id = normalize_text(item.get("event_id"))
        if event_id and event_id not in index:
            index[event_id] = item
    return index, artifact if isinstance(artifact, dict) else {}


def selector_data_ai_id(selector: Any) -> str:
    match = DATA_AI_SELECTOR_RE.fullmatch(normalize_text(selector))
    if not match:
        return ""
    return match.group("value").replace('\\"', '"').replace("\\\\", "\\")


def selector_dom_id(selector: Any) -> str:
    match = ID_SELECTOR_RE.fullmatch(normalize_text(selector))
    return match.group("id") if match else ""


def build_dom_id_index(nodes: list[Any]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for node in nodes:
        attrs = node.attrs if isinstance(getattr(node, "attrs", None), dict) else {}
        dom_id = normalize_text(attrs.get("id"))
        if dom_id and dom_id not in index:
            index[dom_id] = node
    return index


def resolve_selector_node(selector: Any, by_data_ai_id: dict[str, Any], by_dom_id: dict[str, Any]) -> Any | None:
    normalized = normalize_text(selector)
    if not normalized:
        return None
    data_ai_id = selector_data_ai_id(normalized)
    if data_ai_id:
        return by_data_ai_id.get(data_ai_id)
    dom_id = selector_dom_id(normalized)
    if dom_id:
        return by_dom_id.get(dom_id)
    return None


def inspect_selector_in_source(selector: str, by_data_ai_id: dict[str, Any], by_dom_id: dict[str, Any]) -> dict[str, Any]:
    node = resolve_selector_node(selector, by_data_ai_id, by_dom_id)
    if node is None:
        return {
            "selector": selector,
            "node_found": False,
            "self_hidden": None,
            "self_disabled": None,
        }

    attrs = node.attrs if isinstance(getattr(node, "attrs", None), dict) else {}
    style_text = normalize_text(attrs.get("style")).lower()
    self_hidden = (
        "hidden" in attrs
        or normalize_text(attrs.get("aria-hidden")).lower() == "true"
        or "display:none" in style_text
        or "visibility:hidden" in style_text
    )
    self_disabled = (
        "disabled" in attrs
        or normalize_text(attrs.get("aria-disabled")).lower() == "true"
    )
    return {
        "selector": selector,
        "node_found": True,
        "tag_name": normalize_text(getattr(node, "tag", "")) or None,
        "data_ai_id": normalize_text(attrs.get("data-ai-id")) or None,
        "dom_id": normalize_text(attrs.get("id")) or None,
        "class_tokens": list(getattr(node, "class_tokens", []) or [])[:6],
        "text_hint": normalize_text(getattr(node, "text", ""))[:120] or None,
        "self_hidden": bool(self_hidden),
        "self_disabled": bool(self_disabled),
    }


def derive_source_signal_hints(preflight_item: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    call_sites = preflight_item.get("call_sites") if isinstance(preflight_item.get("call_sites"), list) else []
    inferred_binding = preflight_item.get("inferred_binding") if isinstance(preflight_item.get("inferred_binding"), dict) else {}
    for call_site in call_sites[:3]:
        if isinstance(call_site, dict):
            parts.append(normalize_text(call_site.get("snippet")))
            parts.append(normalize_text(call_site.get("code")))
    parts.append(normalize_text(inferred_binding.get("listener_code")))
    parts.append(normalize_text(inferred_binding.get("binding_selector_code")))
    haystack = "\n".join(part for part in parts if part).lower()
    return [label for needle, label in SOURCE_SIGNAL_PATTERNS if needle in haystack]


def collect_session_error_entries(session_files: list[Path]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for session_file in session_files:
        payload = safe_json_load(session_file)
        if not isinstance(payload, dict) or not payload:
            continue
        session_id = normalize_text(payload.get("session_id")) or session_file.parent.name
        last_action = payload.get("last_action") if isinstance(payload.get("last_action"), dict) else {}
        error = normalize_text(last_action.get("error"))
        if not error:
            continue
        entries.append(
            {
                "session_id": session_id,
                "history_length": len(payload.get("history") or []) if isinstance(payload.get("history"), list) else 0,
                "generated_at": normalize_text(last_action.get("generated_at")) or None,
                "error": error,
            }
        )
    return entries


def related_session_errors(selector_candidates: list[str], error_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selectors = [normalize_text(selector) for selector in selector_candidates if normalize_text(selector)]
    results: list[dict[str, Any]] = []
    for entry in error_entries:
        error_text = normalize_text(entry.get("error"))
        if not error_text:
            continue
        if selectors and not any(selector in error_text for selector in selectors):
            continue
        results.append(entry)
    return results


def build_source_review_payload(
    event: dict[str, Any],
    preflight_item: dict[str, Any],
    *,
    by_data_ai_id: dict[str, Any],
    by_dom_id: dict[str, Any],
    error_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    selectors: list[str] = []
    inferred_binding = preflight_item.get("inferred_binding") if isinstance(preflight_item.get("inferred_binding"), dict) else {}
    for candidate in (
        inferred_binding.get("preferred_runtime_selector"),
        inferred_binding.get("binding_selector"),
    ):
        text = normalize_text(candidate)
        if text and text not in selectors:
            selectors.append(text)
    for candidate in event.get("selector_candidates") if isinstance(event.get("selector_candidates"), list) else []:
        text = normalize_text(candidate)
        if text and text not in selectors:
            selectors.append(text)

    inspections = [inspect_selector_in_source(selector, by_data_ai_id, by_dom_id) for selector in selectors[:4]]
    suspicion_reasons: list[str] = []
    if any(item.get("self_hidden") is True for item in inspections):
        suspicion_reasons.append("control_hidden_in_source")
    if any(item.get("self_disabled") is True for item in inspections):
        suspicion_reasons.append("control_disabled_in_source")

    matching_errors = related_session_errors(selectors, error_entries)
    if matching_errors and any("not visible" in normalize_text(item.get("error")).lower() for item in matching_errors):
        suspicion_reasons.append("runtime_attempt_failed_on_non_visible_control")

    source_signal_hints = derive_source_signal_hints(preflight_item)
    call_sites = preflight_item.get("call_sites") if isinstance(preflight_item.get("call_sites"), list) else []
    prerequisite_hints = inferred_binding.get("prerequisite_hints") if isinstance(inferred_binding.get("prerequisite_hints"), list) else []
    payload = {
        "event_id": normalize_text(event.get("id")) or None,
        "action": normalize_text(event.get("action")) or None,
        "element_name": normalize_text(event.get("element_name")) or None,
        "requires_agent_source_review": True,
        "selector_inspections": inspections,
        "source_signal_hints": source_signal_hints,
        "preflight": {
            "resolution_status": normalize_text(inferred_binding.get("resolution_status")) or None,
            "preferred_runtime_selector": normalize_text(inferred_binding.get("preferred_runtime_selector")) or None,
            "binding_selector": normalize_text(inferred_binding.get("binding_selector")) or None,
            "view_hint": normalize_text(inferred_binding.get("view_hint")) or None,
            "prerequisite_hints": prerequisite_hints,
            "call_sites": call_sites[:2],
        },
        "runtime_evidence": {
            "related_session_errors": matching_errors[:2],
        },
        "agent_review_rule": (
            "Do not remove this event automatically. Read the source callsite and interaction flow first. "
            "Only remove it if the source confirms that the control is never manually reachable on the real user path."
        ),
    }
    if suspicion_reasons:
        payload["suspicion_reasons"] = suspicion_reasons
    return payload


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


def build_next_action(
    *,
    failure_reason: str,
    workspace_dir: Path,
    uncovered_event_ids: list[str],
    suspected_unreachable_event_ids: list[str],
) -> str:
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
        suspected_preview = ", ".join(suspected_unreachable_event_ids[:5])
        suspected_suffix = "" if len(suspected_unreachable_event_ids) <= 5 else ", ..."
        suspected_clause = ""
        if suspected_preview:
            suspected_clause = (
                " For suspected-unreachable events, read the runtime preflight callsite and source flow first, "
                "and only remove them from design/schema after the source confirms they are not manually reachable"
                f": {suspected_preview}{suspected_suffix}."
            )
        return (
            "Review passed, but some schema events are still uncovered in runtime_browser_session artifacts. "
            f"After one exploration pass, read `runtime_browser_preflight.json` for each uncovered event_id or rerun `{preflight_command}` with targeted ids, locate the real `trackClick(...)` / `trackPageShow(...)` binding, "
            f"derive the trigger node, view, and prerequisite path, then use `runtime_browser_session.py act` / `assert` to capture the remaining events: {preview}{suffix}. "
            + suspected_clause
            + " Then rerun the validation gate."
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
    preflight_index, preflight_artifact = load_runtime_preflight_index(workspace_dir)
    session_root = resolve_session_root(workspace_dir)
    session_files = sorted(session_root.glob("*/session.json"))
    session_error_entries = collect_session_error_entries(session_files)
    preflight_inputs = preflight_artifact.get("inputs") if isinstance(preflight_artifact.get("inputs"), dict) else {}
    target_file = Path(normalize_text(preflight_inputs.get("target_file"))).expanduser().resolve() if normalize_text(preflight_inputs.get("target_file")) else None
    nodes: list[Any] = []
    by_data_ai_id: dict[str, Any] = {}
    by_dom_id: dict[str, Any] = {}
    if target_file and target_file.exists() and target_file.suffix.lower() == ".html":
        nodes, by_data_ai_id, _ = parse_html_dom(target_file)
        by_dom_id = build_dom_id_index(nodes)

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
    source_review_candidates: list[dict[str, Any]] = []
    suspected_unreachable_events: list[dict[str, Any]] = []
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
            preflight_item = preflight_index.get(event_id, {})
            source_review = build_source_review_payload(
                event,
                preflight_item,
                by_data_ai_id=by_data_ai_id,
                by_dom_id=by_dom_id,
                error_entries=session_error_entries,
            )
            source_review_candidates.append(source_review)
            if isinstance(source_review.get("suspicion_reasons"), list) and source_review.get("suspicion_reasons"):
                suspected_unreachable_events.append(source_review)

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
    suspected_unreachable_event_ids = [
        normalize_text(item.get("event_id"))
        for item in suspected_unreachable_events
        if normalize_text(item.get("event_id"))
    ]
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
            "source_review_required_event_ids": [
                normalize_text(item.get("event_id"))
                for item in source_review_candidates
                if normalize_text(item.get("event_id"))
            ],
            "suspected_unreachable_event_ids": suspected_unreachable_event_ids,
            "suspected_unreachable_count": len(suspected_unreachable_events),
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
        "source_review_candidates": source_review_candidates,
        "suspected_unreachable_events": suspected_unreachable_events,
        "sessions": sessions_summary,
        "failure_reason": failure_reason,
        "next_action": (
            "runtime_browser_session coverage passed. It is safe to treat runtime verification as complete."
            if status == "passed"
            else build_next_action(
                failure_reason=failure_reason or "unknown_failure",
                workspace_dir=workspace_dir,
                uncovered_event_ids=uncovered_event_ids,
                suspected_unreachable_event_ids=suspected_unreachable_event_ids,
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
