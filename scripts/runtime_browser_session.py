#!/usr/bin/env python3
"""
Drive a reproducible headless browser session from the command line.

The session is persisted as action history under the workspace so an agent can:
- inspect the current page state in a real browser
- execute one or more constrained browser actions
- inspect newly captured tracking reports after each action
- assert whether a target event/report has already been observed

This avoids hard-coding a full end-to-end case up front. Instead, an agent can
loop over `state -> act -> state/assert` and decide the next move dynamically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text, now_utc_iso, safe_json_load
from runtime_browser_support import (
    DEFAULT_CHROME_EXECUTABLE,
    DEFAULT_TIMEOUT_MS,
    DEFAULT_VIEWPORT_HEIGHT,
    DEFAULT_VIEWPORT_WIDTH,
    capture_reports,
    create_context,
    ensure_playwright,
    match_expected_reports,
    perform_step,
    read_capture,
    resolve_target_file,
    sanitize_case_id,
)


SESSION_VERSION = "runtime_browser_session_v1"
DEFAULT_SESSION_ID = "default"
DEFAULT_MAX_ACTIONS = 40
DEFAULT_MAX_VISIBLE_TEXTS = 30
DEFAULT_MAX_ACTIVE_ELEMENTS = 30
DEFAULT_MAX_REPORTS = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect and drive a reproducible headless browser session.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Create or refresh a browser session and capture the current state.")
    add_workspace_args(start)
    start.add_argument("--schema-path", default="", help="Optional tracking_schema.json path.")
    start.add_argument("--target-file", default="", help="Optional target HTML file path.")
    start.add_argument(
        "--browser-executable",
        default="",
        help=(
            "Optional Chrome/Chromium executable path. Defaults to system Chrome when available, "
            "otherwise Playwright Chromium."
        ),
    )
    start.add_argument("--headless", action="store_true", help="Force headless mode.")
    start.add_argument("--headed", action="store_true", help="Force headed mode.")
    start.add_argument("--viewport-width", type=int, default=DEFAULT_VIEWPORT_WIDTH, help="Viewport width.")
    start.add_argument("--viewport-height", type=int, default=DEFAULT_VIEWPORT_HEIGHT, help="Viewport height.")
    start.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help="Default action timeout in ms.")
    start.add_argument("--max-actions", type=int, default=DEFAULT_MAX_ACTIONS, help="Maximum clickable elements to snapshot.")
    start.add_argument("--reset", action="store_true", help="Reset any existing action history before capturing state.")
    start.add_argument("--json", action="store_true", help="Print JSON result.")

    state = subparsers.add_parser("state", help="Replay the saved session history and snapshot the current page state.")
    add_workspace_args(state)
    state.add_argument("--max-actions", type=int, default=DEFAULT_MAX_ACTIONS, help="Maximum clickable elements to snapshot.")
    state.add_argument("--json", action="store_true", help="Print JSON result.")

    act = subparsers.add_parser("act", help="Replay session history, execute new actions, then snapshot the resulting page state.")
    add_workspace_args(act)
    act.add_argument("--step-json", default="", help="Single JSON object or array of step objects.")
    act.add_argument("--step-file", default="", help="Path to a JSON file containing one step object or an array of steps.")
    act.add_argument("--max-actions", type=int, default=DEFAULT_MAX_ACTIONS, help="Maximum clickable elements to snapshot.")
    act.add_argument("--json", action="store_true", help="Print JSON result.")

    assert_parser = subparsers.add_parser("assert", help="Replay session history and assert captured reports against an expected payload.")
    add_workspace_args(assert_parser)
    assert_parser.add_argument("--event-id", default="", help="Shortcut expected report id.")
    assert_parser.add_argument("--action", default="", help="Optional action when using --event-id.")
    assert_parser.add_argument("--expected-report-json", default="", help="Expected report object/list as JSON.")
    assert_parser.add_argument("--expected-report-file", default="", help="Path to a JSON file containing the expected report object/list.")
    assert_parser.add_argument("--ordered", action="store_true", help="When asserting a report list, require ordered matching.")
    assert_parser.add_argument("--max-actions", type=int, default=DEFAULT_MAX_ACTIONS, help="Maximum clickable elements to snapshot.")
    assert_parser.add_argument("--json", action="store_true", help="Print JSON result.")

    return parser.parse_args()


def add_workspace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace-dir", required=True, help="Path to the tracking workspace session directory.")
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID, help=f"Logical browser session id. Default: {DEFAULT_SESSION_ID}")


def resolve_existing_file(value: str) -> Path | None:
    text = normalize_text(value)
    if not text:
        return None
    candidate = Path(text).expanduser().resolve()
    return candidate if candidate.exists() else None


def resolve_schema_path(explicit: str, workspace_dir: Path) -> Path:
    existing = resolve_existing_file(explicit)
    if existing:
        return existing
    return (workspace_dir / "tracking_schema.json").resolve()


def resolve_browser_executable(explicit: str) -> str:
    candidate = normalize_text(explicit)
    if candidate:
        path = Path(candidate).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"Browser executable not found: {path}")
        return str(path)
    default_path = Path(DEFAULT_CHROME_EXECUTABLE)
    return str(default_path.resolve()) if default_path.exists() else ""


def resolve_session_root(workspace_dir: Path) -> Path:
    return (workspace_dir / "runtime_browser_sessions").resolve()


def resolve_session_dir(workspace_dir: Path, session_id: str) -> Path:
    safe_id = sanitize_case_id(session_id, DEFAULT_SESSION_ID)
    return (resolve_session_root(workspace_dir) / safe_id).resolve()


def resolve_session_file(workspace_dir: Path, session_id: str) -> Path:
    return (resolve_session_dir(workspace_dir, session_id) / "session.json").resolve()


def resolve_runtime_browser_preflight_path(workspace_dir: Path) -> Path:
    return (workspace_dir / "runtime_browser_preflight.json").resolve()


def resolve_state_dir(workspace_dir: Path, session_id: str) -> Path:
    return (resolve_session_dir(workspace_dir, session_id) / "states").resolve()


def resolve_screenshot_dir(workspace_dir: Path, session_id: str) -> Path:
    return (resolve_session_dir(workspace_dir, session_id) / "screenshots").resolve()


def copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False)) if value is not None else None


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quoted_cli_arg(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def preflight_prepare_command(workspace_dir: Path, schema_path: Path, target_file: Path) -> str:
    return " ".join(
        [
            "python3 scripts/prepare_runtime_browser_preflight.py",
            f"--workspace-dir {quoted_cli_arg(str(workspace_dir))}",
            f"--schema-path {quoted_cli_arg(str(schema_path))}",
            f"--target-file {quoted_cli_arg(str(target_file))}",
            "--json",
        ]
    )


def preflight_failure_message(
    workspace_dir: Path,
    *,
    schema_path: Path,
    target_file: Path,
    reason: str,
) -> str:
    preflight_path = resolve_runtime_browser_preflight_path(workspace_dir)
    return (
        "runtime_browser_session requires a fresh source preflight before browser actions can run.\n"
        f"Reason: {reason}\n"
        f"Expected preflight: {preflight_path}\n"
        "Regenerate it with:\n"
        f"  {preflight_prepare_command(workspace_dir, schema_path, target_file)}\n"
        "Then read runtime_browser_preflight.json and rerun runtime_browser_session.py."
    )


def validate_runtime_browser_preflight(
    workspace_dir: Path,
    *,
    schema_path: Path,
    target_file: Path,
) -> dict[str, Any]:
    preflight_path = resolve_runtime_browser_preflight_path(workspace_dir)
    payload = safe_json_load(preflight_path)
    if not payload:
        raise SystemExit(
            preflight_failure_message(
                workspace_dir,
                schema_path=schema_path,
                target_file=target_file,
                reason="preflight file is missing or invalid JSON.",
            )
        )

    if normalize_text(payload.get("status")).lower() != "prepared":
        raise SystemExit(
            preflight_failure_message(
                workspace_dir,
                schema_path=schema_path,
                target_file=target_file,
                reason=f"preflight status must be 'prepared', got {normalize_text(payload.get('status')) or 'empty'}.",
            )
        )

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if int(summary.get("event_count") or 0) <= 0:
        raise SystemExit(
            preflight_failure_message(
                workspace_dir,
                schema_path=schema_path,
                target_file=target_file,
                reason="preflight did not resolve any schema events.",
            )
        )

    preflight_workspace = normalize_text(payload.get("workspace_dir"))
    if preflight_workspace and Path(preflight_workspace).expanduser().resolve() != workspace_dir:
        raise SystemExit(
            preflight_failure_message(
                workspace_dir,
                schema_path=schema_path,
                target_file=target_file,
                reason=f"preflight points to a different workspace: {preflight_workspace}",
            )
        )

    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    preflight_schema_path = normalize_text(inputs.get("schema_path") or payload.get("schema_path"))
    preflight_target_file = normalize_text(inputs.get("target_file") or payload.get("target_file"))
    if not preflight_schema_path or not preflight_target_file:
        raise SystemExit(
            preflight_failure_message(
                workspace_dir,
                schema_path=schema_path,
                target_file=target_file,
                reason="preflight is missing schema_path/target_file metadata.",
            )
        )

    if Path(preflight_schema_path).expanduser().resolve() != schema_path:
        raise SystemExit(
            preflight_failure_message(
                workspace_dir,
                schema_path=schema_path,
                target_file=target_file,
                reason=f"preflight schema_path does not match current schema: {preflight_schema_path}",
            )
        )

    if Path(preflight_target_file).expanduser().resolve() != target_file:
        raise SystemExit(
            preflight_failure_message(
                workspace_dir,
                schema_path=schema_path,
                target_file=target_file,
                reason=f"preflight target_file does not match current target source: {preflight_target_file}",
            )
        )

    expected_schema_sha = normalize_text(inputs.get("schema_sha256"))
    expected_target_sha = normalize_text(inputs.get("target_file_sha256"))
    if not expected_schema_sha or not expected_target_sha:
        raise SystemExit(
            preflight_failure_message(
                workspace_dir,
                schema_path=schema_path,
                target_file=target_file,
                reason="preflight is missing schema/target file fingerprints. Regenerate it.",
            )
        )

    current_schema_sha = file_sha256(schema_path)
    current_target_sha = file_sha256(target_file)
    if expected_schema_sha != current_schema_sha:
        raise SystemExit(
            preflight_failure_message(
                workspace_dir,
                schema_path=schema_path,
                target_file=target_file,
                reason="tracking_schema.json changed after preflight was generated.",
            )
        )
    if expected_target_sha != current_target_sha:
        raise SystemExit(
            preflight_failure_message(
                workspace_dir,
                schema_path=schema_path,
                target_file=target_file,
                reason="target source file changed after preflight was generated.",
            )
        )
    return payload


def resolve_session_schema_and_target(workspace_dir: Path, session: dict[str, Any]) -> tuple[Path, Path]:
    schema_text = normalize_text(session.get("schema_path"))
    schema_path = Path(schema_text).expanduser().resolve() if schema_text else resolve_schema_path("", workspace_dir)
    if not schema_path.exists():
        raise SystemExit(f"Session schema_path not found: {schema_path}")

    target_text = normalize_text(session.get("target_file"))
    if not target_text:
        raise SystemExit("Session is missing target_file. Create a fresh browser session with `start`.")
    target_file = Path(target_text).expanduser().resolve()
    if not target_file.exists():
        raise SystemExit(f"Session target_file not found: {target_file}")
    return schema_path, target_file


def load_session(workspace_dir: Path, session_id: str) -> dict[str, Any]:
    session_file = resolve_session_file(workspace_dir, session_id)
    payload = safe_json_load(session_file)
    if not payload:
        raise SystemExit(
            f"Browser session not found: {session_file}\n"
            "Create one first with `python3 scripts/runtime_browser_session.py start ...`."
        )
    return payload


def save_session(workspace_dir: Path, session: dict[str, Any]) -> Path:
    session_dir = resolve_session_dir(workspace_dir, normalize_text(session.get("session_id")) or DEFAULT_SESSION_ID)
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = (session_dir / "session.json").resolve()
    session["updated_at"] = now_utc_iso()
    session_file.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    return session_file


def normalize_step_list(payload: Any, *, source: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    raise SystemExit(f"{source} must be a JSON object or an array of JSON objects.")


def load_json_value(path: Path, *, label: str) -> Any:
    if not path.exists():
        raise SystemExit(f"{label} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} is not valid JSON: {path} ({exc})") from exc


def load_steps_from_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    raw_json = normalize_text(args.step_json)
    raw_file = normalize_text(args.step_file)
    if bool(raw_json) == bool(raw_file):
        raise SystemExit("Provide exactly one of --step-json or --step-file.")

    if raw_json:
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid --step-json: {exc}") from exc
        return normalize_step_list(payload, source="--step-json")

    path = Path(raw_file).expanduser().resolve()
    payload = load_json_value(path, label="Step file")
    return normalize_step_list(payload, source=str(path))


def load_expected_reports_from_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    event_id = normalize_text(args.event_id)
    json_text = normalize_text(args.expected_report_json)
    file_text = normalize_text(args.expected_report_file)

    source_count = int(bool(event_id)) + int(bool(json_text)) + int(bool(file_text))
    if source_count != 1:
        raise SystemExit("Provide exactly one of --event-id, --expected-report-json, or --expected-report-file.")

    if event_id:
        report = {"id": event_id}
        action = normalize_text(args.action)
        if action:
            report["action"] = action
        return [report]

    if json_text:
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid --expected-report-json: {exc}") from exc
        return normalize_step_list(payload, source="--expected-report-json")

    path = Path(file_text).expanduser().resolve()
    payload = load_json_value(path, label="Expected report file")
    return normalize_step_list(payload, source=str(path))


def build_clickable_elements(page: Any, max_actions: int) -> list[dict[str, Any]]:
    payload = page.evaluate(
        """
        (limit) => {
          function isVisible(el) {
            const style = window.getComputedStyle(el);
            if (!style) return false;
            if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          }

          function interactionReason(el) {
            const tag = el.tagName.toLowerCase();
            const role = (el.getAttribute("role") || "").toLowerCase();
            const classes = Array.from(el.classList || []).filter(Boolean);
            const style = window.getComputedStyle(el);
            const tabindex = el.getAttribute("tabindex");
            const classHint = classes.find((cls) => /(btn|button|option|action|link|tab|chip|item)/i.test(cls));
            if (["button", "a", "select", "textarea", "summary"].includes(tag)) return tag;
            if (tag === "input" && (el.getAttribute("type") || "").toLowerCase() !== "hidden") return tag;
            if (["button", "link", "tab", "menuitem", "checkbox", "radio", "switch"].includes(role)) return `role:${role}`;
            if (el.hasAttribute("onclick")) return "onclick";
            if (tabindex !== null && Number(tabindex) >= 0) return "tabindex";
            if (classHint) return `class:${classHint}`;
            if (style && style.cursor === "pointer") return "cursor:pointer";
            return "";
          }

          function selectorHint(el, index) {
            const id = el.getAttribute("id");
            if (id) return `#${id}`;
            const dataAiId = el.getAttribute("data-ai-id");
            if (dataAiId) return `[data-ai-id="${dataAiId}"]`;
            const classes = Array.from(el.classList || []).filter(Boolean).slice(0, 3);
            if (classes.length) return `${el.tagName.toLowerCase()}.${classes.join(".")}`;
            return `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`;
          }

          const results = [];
          const seen = new Set();
          for (const [index, el] of Array.from(document.querySelectorAll("body *")).entries()) {
            if (!isVisible(el)) continue;
            const reason = interactionReason(el);
            if (!reason) continue;
            const hint = selectorHint(el, index);
            if (seen.has(hint)) continue;
            seen.add(hint);
            const rect = el.getBoundingClientRect();
            results.push({
              candidate_reason: reason,
              selector_hint: hint,
              tag_name: el.tagName.toLowerCase(),
              id: el.getAttribute("id") || null,
              data_ai_id: el.getAttribute("data-ai-id") || null,
              role: el.getAttribute("role") || null,
              name: el.getAttribute("name") || null,
              text: (el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 160),
              disabled: !!el.disabled,
              checked: typeof el.checked === "boolean" ? el.checked : null,
              value: typeof el.value === "string" ? String(el.value).slice(0, 80) : null,
              rect: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
              }
            });
            if (results.length >= limit) break;
          }
          return results;
        }
        """,
        max(1, int(max_actions)),
    )
    return payload if isinstance(payload, list) else []


def build_active_elements(page: Any, limit: int) -> list[dict[str, Any]]:
    payload = page.evaluate(
        """
        (limit) => {
          const interestingClassPattern = /(active|show|open|current|selected|visible)/i;

          function isVisible(el) {
            const style = window.getComputedStyle(el);
            if (!style) return false;
            if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          }

          function selectorHint(el, index) {
            const id = el.getAttribute("id");
            if (id) return `#${id}`;
            const dataAiId = el.getAttribute("data-ai-id");
            if (dataAiId) return `[data-ai-id="${dataAiId}"]`;
            const classes = Array.from(el.classList || []).filter(Boolean).slice(0, 3);
            if (classes.length) return `${el.tagName.toLowerCase()}.${classes.join(".")}`;
            return `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`;
          }

          const nodes = Array.from(document.querySelectorAll("[id], [data-ai-id], [role='dialog'], [aria-current], .active, .show, .open, .current, .selected"));
          const results = [];
          const seen = new Set();
          for (const [index, el] of nodes.entries()) {
            if (!isVisible(el)) continue;
            const id = el.getAttribute("id") || null;
            const dataAiId = el.getAttribute("data-ai-id") || null;
            const role = el.getAttribute("role") || null;
            const ariaCurrent = el.getAttribute("aria-current") || null;
            const interestingClasses = Array.from(el.classList || []).filter((cls) => interestingClassPattern.test(cls)).slice(0, 5);
            if (!id && !dataAiId && !role && !ariaCurrent && interestingClasses.length === 0) continue;
            const hint = selectorHint(el, index);
            if (seen.has(hint)) continue;
            seen.add(hint);
            results.push({
              selector_hint: hint,
              tag_name: el.tagName.toLowerCase(),
              id,
              data_ai_id: dataAiId,
              role,
              aria_current: ariaCurrent,
              class_names: interestingClasses,
              text: (el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 160)
            });
            if (results.length >= limit) break;
          }
          return results;
        }
        """,
        max(1, int(limit)),
    )
    return payload if isinstance(payload, list) else []


def build_visible_texts(page: Any, limit: int) -> list[str]:
    payload = page.evaluate(
        """
        (limit) => {
          const selectors = [
            "h1",
            "h2",
            "h3",
            "button",
            "a[href]",
            "[role='button']",
            "label",
            "p",
            "li",
            "[data-ai-id]"
          ].join(",");

          function isVisible(el) {
            const style = window.getComputedStyle(el);
            if (!style) return false;
            if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          }

          const texts = [];
          const seen = new Set();
          for (const el of Array.from(document.querySelectorAll(selectors))) {
            if (!isVisible(el)) continue;
            const text = (el.textContent || "").replace(/\\s+/g, " ").trim();
            if (!text || seen.has(text)) continue;
            seen.add(text);
            texts.push(text.slice(0, 180));
            if (texts.length >= limit) break;
          }
          return texts;
        }
        """,
        max(1, int(limit)),
    )
    return payload if isinstance(payload, list) else []


def build_tracking_summary(workspace_dir: Path, reports: list[dict[str, Any]]) -> dict[str, Any]:
    captured_events = sorted(
        {
            (
                normalize_text(item.get("id")),
                normalize_text(item.get("action")) or None,
            )
            for item in reports
            if isinstance(item, dict) and normalize_text(item.get("id"))
        }
    )
    captured_event_ids = sorted(
        {
            normalize_text(item.get("id"))
            for item in reports
            if isinstance(item, dict) and normalize_text(item.get("id"))
        }
    )
    summary: dict[str, Any] = {
        "captured_events": [
            {
                "id": event_id,
                "action": action,
            }
            for event_id, action in captured_events
        ],
        "captured_event_ids": captured_event_ids,
        "recent_reports": [copy_json(item) for item in reports[-DEFAULT_MAX_REPORTS:]],
    }

    schema = safe_json_load((workspace_dir / "tracking_schema.json").resolve())
    if isinstance(schema, dict):
        events = schema.get("events") if isinstance(schema.get("events"), list) else []
        schema_event_ids = [
            normalize_text(item.get("id"))
            for item in events
            if isinstance(item, dict) and normalize_text(item.get("id"))
        ]
        captured_set = set(captured_event_ids)
        summary["schema_event_count"] = len(schema_event_ids)
        summary["remaining_schema_event_ids"] = [event_id for event_id in schema_event_ids if event_id not in captured_set]

    return summary


def snapshot_state(page: Any, workspace_dir: Path, session: dict[str, Any], *, max_actions: int) -> dict[str, Any]:
    session_id = normalize_text(session.get("session_id")) or DEFAULT_SESSION_ID
    state_dir = resolve_state_dir(workspace_dir, session_id)
    screenshot_dir = resolve_screenshot_dir(workspace_dir, session_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    snapshot_index = int(session.get("last_snapshot_index") or 0) + 1
    state_path = (state_dir / f"state_{snapshot_index:04d}.json").resolve()
    screenshot_path = (screenshot_dir / f"state_{snapshot_index:04d}.png").resolve()
    page.screenshot(path=str(screenshot_path), full_page=True)

    capture = read_capture(page)
    reports = capture_reports(capture)
    payload = {
        "ok": True,
        "status": "ready",
        "generated_at": now_utc_iso(),
        "session_id": session_id,
        "history_length": len(session.get("history") or []),
        "target_file": normalize_text(session.get("target_file")) or None,
        "page": {
            "url": page.url,
            "title": page.title(),
            "viewport": {
                "width": int(session.get("viewport_width") or DEFAULT_VIEWPORT_WIDTH),
                "height": int(session.get("viewport_height") or DEFAULT_VIEWPORT_HEIGHT),
            },
        },
        "ui_state": {
            "active_elements": build_active_elements(page, DEFAULT_MAX_ACTIVE_ELEMENTS),
            "clickable_elements": build_clickable_elements(page, max_actions),
            "visible_texts": build_visible_texts(page, DEFAULT_MAX_VISIBLE_TEXTS),
        },
        "tracking": {
            "capture_count": len(capture),
            "report_count": len(reports),
            **build_tracking_summary(workspace_dir, reports),
        },
        "artifacts": {
            "state_path": str(state_path),
            "screenshot_path": str(screenshot_path),
        },
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    session["last_snapshot_index"] = snapshot_index
    session["last_state_path"] = str(state_path)
    session["last_screenshot_path"] = str(screenshot_path)
    session["last_capture_count"] = len(capture)
    session["last_report_count"] = len(reports)
    return payload


def browser_summary(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "configured_executable": normalize_text(session.get("browser_executable")) or None,
        "backend": normalize_text(session.get("browser_backend")) or None,
        "launch_warning": normalize_text(session.get("browser_launch_warning")) or None,
        "headless": bool(session.get("headless", True)),
    }


def launch_browser(playwright: Any, session: dict[str, Any]) -> Any:
    browser_executable = normalize_text(session.get("browser_executable"))
    headless = bool(session.get("headless", True))
    if browser_executable:
        try:
            browser = playwright.chromium.launch(
                executable_path=browser_executable,
                headless=headless,
            )
            session["browser_backend"] = "explicit_executable"
            session["browser_launch_warning"] = None
            return browser
        except Exception as exc:
            session["browser_backend"] = "playwright_chromium_fallback"
            session["browser_launch_warning"] = (
                f"Failed to launch configured browser '{browser_executable}': {exc}. "
                "Falling back to Playwright Chromium."
            )
    browser = playwright.chromium.launch(headless=headless)
    if not browser_executable:
        session["browser_backend"] = "playwright_chromium"
        session["browser_launch_warning"] = None
    return browser


def open_page(browser: Any, session: dict[str, Any]) -> tuple[Any, Any]:
    context = create_context(
        browser,
        viewport_width=int(session.get("viewport_width") or DEFAULT_VIEWPORT_WIDTH),
        viewport_height=int(session.get("viewport_height") or DEFAULT_VIEWPORT_HEIGHT),
    )
    page = context.new_page()
    page.set_default_timeout(int(session.get("timeout_ms") or DEFAULT_TIMEOUT_MS))
    target_file = Path(normalize_text(session.get("target_file"))).expanduser().resolve()
    page.goto(target_file.as_uri(), wait_until="domcontentloaded")
    return context, page


def replay_history(page: Any, session: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    timeout_ms = int(session.get("timeout_ms") or DEFAULT_TIMEOUT_MS)
    for index, step in enumerate(session.get("history") or []):
        if not isinstance(step, dict):
            raise SystemExit(f"Session history step {index + 1} is not a JSON object.")
        try:
            results.append(perform_step(page, step, default_timeout_ms=timeout_ms))
        except Exception as exc:
            raise SystemExit(f"Failed to replay session history step {index + 1}: {exc}") from exc
    return results


def refresh_existing_session(session: dict[str, Any], args: argparse.Namespace, workspace_dir: Path) -> dict[str, Any]:
    schema_path = resolve_schema_path(args.schema_path, workspace_dir)
    schema = safe_json_load(schema_path)
    if not schema:
        raise SystemExit(f"Schema not found or invalid: {schema_path}")

    target_file = resolve_target_file(args, schema, workspace_dir).resolve()
    browser_executable = resolve_browser_executable(args.browser_executable)
    headless = not args.headed if args.headless or args.headed else True
    history = [] if args.reset else [copy_json(item) for item in session.get("history") or [] if isinstance(item, dict)]

    return {
        "version": SESSION_VERSION,
        "created_at": normalize_text(session.get("created_at")) or now_utc_iso(),
        "updated_at": now_utc_iso(),
        "session_id": normalize_text(args.session_id) or normalize_text(session.get("session_id")) or DEFAULT_SESSION_ID,
        "workspace_dir": str(workspace_dir),
        "schema_path": str(schema_path),
        "target_file": str(target_file),
        "browser_executable": browser_executable,
        "headless": headless,
        "viewport_width": max(1, int(args.viewport_width)),
        "viewport_height": max(1, int(args.viewport_height)),
        "timeout_ms": max(0, int(args.timeout_ms)),
        "history": history,
        "last_snapshot_index": 0 if args.reset else int(session.get("last_snapshot_index") or 0),
        "last_state_path": None if args.reset else normalize_text(session.get("last_state_path")) or None,
        "last_screenshot_path": None if args.reset else normalize_text(session.get("last_screenshot_path")) or None,
        "last_capture_count": 0 if args.reset else int(session.get("last_capture_count") or 0),
        "last_report_count": 0 if args.reset else int(session.get("last_report_count") or 0),
        "last_action": {} if args.reset else copy_json(session.get("last_action")) if isinstance(session.get("last_action"), dict) else {},
        "assertion_history": (
            []
            if args.reset
            else [copy_json(item) for item in session.get("assertion_history") or [] if isinstance(item, dict)]
        ),
        "last_assertion": {} if args.reset else copy_json(session.get("last_assertion")) if isinstance(session.get("last_assertion"), dict) else {},
    }


def start_session(args: argparse.Namespace) -> dict[str, Any]:
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    schema_path = resolve_schema_path(args.schema_path, workspace_dir)
    schema = safe_json_load(schema_path)
    if not schema:
        raise SystemExit(f"Schema not found or invalid: {schema_path}")
    target_file = resolve_target_file(args, schema, workspace_dir).resolve()
    validate_runtime_browser_preflight(workspace_dir, schema_path=schema_path, target_file=target_file)

    session_dir = resolve_session_dir(workspace_dir, args.session_id)
    if args.reset and session_dir.exists():
        shutil.rmtree(session_dir)

    existing = safe_json_load(resolve_session_file(workspace_dir, args.session_id))
    session = refresh_existing_session(existing if isinstance(existing, dict) else {}, args, workspace_dir)

    sync_playwright, _, _ = ensure_playwright()
    with sync_playwright() as playwright:
        browser = launch_browser(playwright, session)
        try:
            context, page = open_page(browser, session)
            try:
                replay_history(page, session)
                state = snapshot_state(page, workspace_dir, session, max_actions=args.max_actions)
            finally:
                context.close()
        finally:
            browser.close()

    session_file = save_session(workspace_dir, session)
    result = {
        "ok": True,
        "status": "ready",
        "generated_at": now_utc_iso(),
        "workspace_dir": str(workspace_dir),
        "session_file": str(session_file),
        "session": {
            "session_id": session["session_id"],
            "target_file": session["target_file"],
            "browser": browser_summary(session),
            "history_length": len(session.get("history") or []),
        },
        "state": state,
    }
    return result


def snapshot_existing_session(args: argparse.Namespace) -> dict[str, Any]:
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    session = load_session(workspace_dir, args.session_id)
    schema_path, target_file = resolve_session_schema_and_target(workspace_dir, session)
    validate_runtime_browser_preflight(workspace_dir, schema_path=schema_path, target_file=target_file)

    sync_playwright, _, _ = ensure_playwright()
    with sync_playwright() as playwright:
        browser = launch_browser(playwright, session)
        try:
            context, page = open_page(browser, session)
            try:
                replay_history(page, session)
                state = snapshot_state(page, workspace_dir, session, max_actions=args.max_actions)
            finally:
                context.close()
        finally:
            browser.close()

    session_file = save_session(workspace_dir, session)
    return {
        "ok": True,
        "status": "ready",
        "generated_at": now_utc_iso(),
        "workspace_dir": str(workspace_dir),
        "session_file": str(session_file),
        "browser": browser_summary(session),
        "state": state,
    }


def act_on_session(args: argparse.Namespace) -> dict[str, Any]:
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    session = load_session(workspace_dir, args.session_id)
    schema_path, target_file = resolve_session_schema_and_target(workspace_dir, session)
    validate_runtime_browser_preflight(workspace_dir, schema_path=schema_path, target_file=target_file)
    new_steps = load_steps_from_args(args)
    timeout_ms = int(session.get("timeout_ms") or DEFAULT_TIMEOUT_MS)

    sync_playwright, _, _ = ensure_playwright()
    with sync_playwright() as playwright:
        browser = launch_browser(playwright, session)
        try:
            context, page = open_page(browser, session)
            try:
                replay_history(page, session)
                reports_before = capture_reports(read_capture(page))
                completed_step_specs: list[dict[str, Any]] = []
                completed_step_results: list[dict[str, Any]] = []
                action_error: str | None = None

                for step_index, step in enumerate(new_steps):
                    try:
                        step_result = perform_step(page, step, default_timeout_ms=timeout_ms)
                    except Exception as exc:
                        action_error = f"Failed to execute new step {step_index + 1}: {exc}"
                        break
                    completed_step_specs.append(copy_json(step))
                    completed_step_results.append(step_result)

                reports_after = capture_reports(read_capture(page))
                new_reports = reports_after[len(reports_before) :]
                session["history"] = [copy_json(item) for item in session.get("history") or [] if isinstance(item, dict)] + completed_step_specs
                session["last_action"] = {
                    "generated_at": now_utc_iso(),
                    "requested_step_count": len(new_steps),
                    "completed_step_count": len(completed_step_specs),
                    "completed_steps": completed_step_results,
                    "new_report_count": len(new_reports),
                    "new_reports": [copy_json(item) for item in new_reports],
                    "error": action_error,
                }
                state = snapshot_state(page, workspace_dir, session, max_actions=args.max_actions)
            finally:
                context.close()
        finally:
            browser.close()

    session_file = save_session(workspace_dir, session)
    status = "ready" if not session.get("last_action", {}).get("error") else "action_failed"
    return {
        "ok": status == "ready",
        "status": status,
        "generated_at": now_utc_iso(),
        "workspace_dir": str(workspace_dir),
        "session_file": str(session_file),
        "browser": browser_summary(session),
        "last_action": copy_json(session.get("last_action")),
        "state": state,
    }


def assert_session_reports(args: argparse.Namespace) -> dict[str, Any]:
    workspace_dir = Path(args.workspace_dir).expanduser().resolve()
    session = load_session(workspace_dir, args.session_id)
    schema_path, target_file = resolve_session_schema_and_target(workspace_dir, session)
    validate_runtime_browser_preflight(workspace_dir, schema_path=schema_path, target_file=target_file)
    expected_reports = load_expected_reports_from_args(args)

    sync_playwright, _, _ = ensure_playwright()
    with sync_playwright() as playwright:
        browser = launch_browser(playwright, session)
        try:
            context, page = open_page(browser, session)
            try:
                replay_history(page, session)
                capture = read_capture(page)
                reports = capture_reports(capture)
                matched, matched_reports, failure = match_expected_reports(
                    expected_reports,
                    reports,
                    ordered=bool(args.ordered),
                    page=page,
                )
                state = snapshot_state(page, workspace_dir, session, max_actions=args.max_actions)
            finally:
                context.close()
        finally:
            browser.close()

    session["last_assertion"] = {
        "generated_at": now_utc_iso(),
        "expected_reports": copy_json(expected_reports),
        "ordered": bool(args.ordered),
        "matched": matched,
        "matched_reports": copy_json(matched_reports),
        "failure": copy_json(failure),
    }
    assertion_history = [copy_json(item) for item in session.get("assertion_history") or [] if isinstance(item, dict)]
    assertion_history.append(copy_json(session["last_assertion"]))
    session["assertion_history"] = assertion_history
    session_file = save_session(workspace_dir, session)

    return {
        "ok": matched,
        "status": "matched" if matched else "not_matched",
        "generated_at": now_utc_iso(),
        "workspace_dir": str(workspace_dir),
        "session_file": str(session_file),
        "browser": browser_summary(session),
        "assertion": copy_json(session.get("last_assertion")),
        "state": state,
    }


def emit_result(result: dict[str, Any], *, as_json: bool) -> int:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if as_json:
        print(text)
    else:
        print(text)
    return 0 if result.get("ok") else 1


def main() -> int:
    args = parse_args()
    if args.command == "start":
        return emit_result(start_session(args), as_json=args.json)
    if args.command == "state":
        return emit_result(snapshot_existing_session(args), as_json=args.json)
    if args.command == "act":
        return emit_result(act_on_session(args), as_json=args.json)
    if args.command == "assert":
        return emit_result(assert_session_reports(args), as_json=args.json)
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
