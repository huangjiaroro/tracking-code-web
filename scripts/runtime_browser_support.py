#!/usr/bin/env python3
"""
Shared browser-session utilities for runtime tracking verification.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from tracking_llm_utils import normalize_text


DEFAULT_CHROME_EXECUTABLE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_TIMEOUT_MS = 2500
DEFAULT_VIEWPORT_WIDTH = 390
DEFAULT_VIEWPORT_HEIGHT = 844
WEBLOG_CDN_FRAGMENT = "weblog/0.0.5/weblog.js"
BACKWASH_FRAGMENT = "backWash_v5.5.js"
SPECIAL_MATCHER_KEYS = {"$regex", "$non_empty", "$from_dom", "$from_eval", "$value"}


def ensure_playwright() -> Any:
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ModuleNotFoundError as exc:
        setup_script = (Path(__file__).resolve().parent / "setup_runtime_verify_env.py").resolve()
        raise SystemExit(
            "Python package 'playwright' is not installed in the selected runtime environment.\n"
            "Initialize the standard runtime verification environment first, for example:\n"
            f"  python3 {setup_script} --json"
        ) from exc
    return sync_playwright, TimeoutError, Error


def existing_file(path_text: str) -> Path | None:
    text = normalize_text(path_text)
    if not text:
        return None
    path = Path(text).expanduser().resolve()
    return path if path.exists() else None


def resolve_target_file(args: argparse.Namespace, schema: dict[str, Any], workspace_dir: Path) -> Path:
    explicit = existing_file(args.target_file)
    if explicit:
        return explicit

    for key in ("implementation_target_html", "workspace_html"):
        candidate = existing_file(str(schema.get(key) or ""))
        if candidate:
            return candidate

    html_files = sorted(path.resolve() for path in workspace_dir.glob("*.html"))
    if html_files:
        return html_files[0]
    raise SystemExit("Implementation target HTML not found. Pass --target-file explicitly.")


def sanitize_case_id(value: Any, fallback: str) -> str:
    text = normalize_text(value)
    if not text:
        return fallback
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text).strip("_")
    return safe or fallback


def capture_init_script() -> str:
    return """
(() => {
  if (window.__trackingCapture) {
    return;
  }

  function clonePayload(value) {
    try {
      return JSON.parse(JSON.stringify(value === undefined ? null : value));
    } catch (error) {
      return { "__capture_error__": String(error), "__raw__": String(value) };
    }
  }

  const capture = [];
  window.__trackingCapture = capture;
  window.__pushTrackingCapture = function(kind, payload) {
    capture.push({
      kind: kind,
      payload: clonePayload(payload),
      timestamp: Date.now()
    });
  };

  window.weblog = window.weblog || {};
  window.weblog.setConfig = function(config) {
    window.__pushTrackingCapture("setConfig", config);
  };
  window.weblog.report = function(payload) {
    window.__pushTrackingCapture("report", payload);
  };
})();
""".strip()


def weblog_stub_script() -> str:
    return """
window.weblog = window.weblog || {};
window.weblog.setConfig = window.weblog.setConfig || function(config) {
  if (window.__pushTrackingCapture) {
    window.__pushTrackingCapture("setConfig", config);
  }
};
window.weblog.report = window.weblog.report || function(payload) {
  if (window.__pushTrackingCapture) {
    window.__pushTrackingCapture("report", payload);
  }
};
""".strip()


def backwash_stub_script() -> str:
    return """
window.backWash = window.backWash || {
  startApp: function() {}
};
""".strip()


def is_weblog_request(url: str) -> bool:
    lowered = url.lower()
    return "weblog.js" in lowered or WEBLOG_CDN_FRAGMENT in lowered


def is_backwash_request(url: str) -> bool:
    lowered = url.lower()
    return ("backwash" in lowered and lowered.endswith(".js")) or BACKWASH_FRAGMENT.lower() in lowered


def read_capture(page: Any) -> list[dict[str, Any]]:
    capture = page.evaluate("() => (window.__trackingCapture || []).slice()")
    return capture if isinstance(capture, list) else []


def capture_reports(capture_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for item in capture_items:
        if not isinstance(item, dict):
            continue
        if normalize_text(item.get("kind")) != "report":
            continue
        payload = item.get("payload")
        if isinstance(payload, dict):
            reports.append(payload)
    return reports


def normalize_matcher_snapshot(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list):
        return [normalize_matcher_snapshot(item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalize_matcher_snapshot(item) for key, item in value.items()}
    return value


def is_special_matcher(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    keys = set(value.keys())
    return keys.issubset(SPECIAL_MATCHER_KEYS) and bool(keys)


def resolve_dom_matcher(page: Any, matcher: dict[str, Any]) -> Any:
    if "resolved" in matcher:
        return normalize_matcher_snapshot(matcher.get("resolved"))
    selector = normalize_text(matcher.get("selector"))
    if not selector:
        raise ValueError("$from_dom matcher requires non-empty 'selector'.")
    kind = normalize_text(matcher.get("kind") or matcher.get("type") or "text").lower()
    attr = normalize_text(matcher.get("attr") or matcher.get("name"))
    nth = matcher.get("nth")
    nth_index = int(nth) if nth is not None else 0
    payload = {
        "selector": selector,
        "kind": kind,
        "attr": attr,
        "nth": nth_index,
    }
    result = page.evaluate(
        """
        ({ selector, kind, attr, nth }) => {
          const nodes = Array.from(document.querySelectorAll(selector));
          const target = nodes[nth || 0];
          if (!target) {
            return null;
          }
          if (kind === "text") {
            return (target.textContent || "").replace(/\\s+/g, " ").trim();
          }
          if (kind === "value") {
            return typeof target.value === "string" ? target.value : null;
          }
          if (kind === "html") {
            return target.innerHTML;
          }
          if (kind === "attr") {
            return attr ? target.getAttribute(attr) : null;
          }
          if (kind === "count") {
            return nodes.length;
          }
          return null;
        }
        """,
        payload,
    )
    return normalize_matcher_snapshot(result)


def resolve_eval_matcher(page: Any, matcher: dict[str, Any]) -> Any:
    if "resolved" in matcher:
        return normalize_matcher_snapshot(matcher.get("resolved"))
    expression = normalize_text(matcher.get("expression") or matcher.get("script"))
    if not expression:
        raise ValueError("$from_eval matcher requires non-empty 'expression'.")
    result = page.evaluate(expression, matcher.get("arg"))
    return normalize_matcher_snapshot(result)


def match_special_matcher(expected: dict[str, Any], actual: Any, *, page: Any | None) -> bool:
    checks: list[bool] = []

    if "$value" in expected:
        checks.append(is_subset(expected.get("$value"), actual, page=page))

    if "$non_empty" in expected:
        want_non_empty = bool(expected.get("$non_empty"))
        if isinstance(actual, str):
            is_non_empty = normalize_text(actual) != ""
        elif isinstance(actual, (list, dict)):
            is_non_empty = len(actual) > 0
        else:
            is_non_empty = actual is not None
        checks.append(is_non_empty is want_non_empty)

    if "$regex" in expected:
        pattern = normalize_text(expected.get("$regex"))
        checks.append(bool(re.search(pattern, normalize_text(actual))))

    if "$from_dom" in expected:
        if page is None:
            checks.append(False)
        else:
            resolved = resolve_dom_matcher(page, expected.get("$from_dom") if isinstance(expected.get("$from_dom"), dict) else {})
            checks.append(is_subset(resolved, actual, page=page))

    if "$from_eval" in expected:
        if page is None:
            checks.append(False)
        else:
            resolved = resolve_eval_matcher(page, expected.get("$from_eval") if isinstance(expected.get("$from_eval"), dict) else {})
            checks.append(is_subset(resolved, actual, page=page))

    return all(checks) if checks else False


def is_subset(expected: Any, actual: Any, *, page: Any | None = None) -> bool:
    if is_special_matcher(expected):
        return match_special_matcher(expected, actual, page=page)

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, value in expected.items():
            if key not in actual:
                return False
            if not is_subset(value, actual[key], page=page):
                return False
        return True

    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            return False
        return all(is_subset(item, actual[index], page=page) for index, item in enumerate(expected))

    return expected == actual


def match_expected_reports(
    expected_reports: list[dict[str, Any]],
    actual_reports: list[dict[str, Any]],
    *,
    ordered: bool,
    page: Any | None = None,
) -> tuple[bool, list[dict[str, Any]], dict[str, Any] | None]:
    if not expected_reports:
        return True, [], None

    if ordered:
        matches: list[dict[str, Any]] = []
        cursor = 0
        for expected in expected_reports:
            found = False
            while cursor < len(actual_reports):
                candidate = actual_reports[cursor]
                cursor += 1
                if not is_subset(expected, candidate, page=page):
                    continue
                matches.append(candidate)
                found = True
                break
            if not found:
                return False, matches, expected
        return True, matches, None

    remaining = list(actual_reports)
    matches = []
    for expected in expected_reports:
        matched_index = None
        for index, candidate in enumerate(remaining):
            if is_subset(expected, candidate, page=page):
                matched_index = index
                matches.append(candidate)
                break
        if matched_index is None:
            return False, matches, expected
        del remaining[matched_index]
    return True, matches, None


def resolve_timeout(value: Any, fallback: int) -> int:
    try:
        resolved = int(value)
    except Exception:
        return fallback
    return resolved if resolved >= 0 else fallback


def perform_wait(page: Any, wait_spec: dict[str, Any], *, default_timeout_ms: int) -> None:
    wait_type = normalize_text(wait_spec.get("type")).lower()
    timeout_ms = resolve_timeout(wait_spec.get("timeout_ms"), default_timeout_ms)

    if wait_type in {"selector", "wait_selector"}:
        selector = normalize_text(wait_spec.get("selector"))
        if not selector:
            raise ValueError("selector wait requires 'selector'")
        state = normalize_text(wait_spec.get("state")).lower() or "visible"
        page.locator(selector).first.wait_for(state=state, timeout=timeout_ms)
        return

    if wait_type in {"function", "wait_function"}:
        expression = normalize_text(wait_spec.get("expression") or wait_spec.get("script"))
        if not expression:
            raise ValueError("function wait requires 'expression'")
        page.wait_for_function(expression, arg=wait_spec.get("arg"), timeout=timeout_ms)
        return

    if wait_type in {"timeout", "sleep"}:
        duration_ms = resolve_timeout(wait_spec.get("duration_ms") or wait_spec.get("ms"), default_timeout_ms)
        page.wait_for_timeout(duration_ms)
        return

    raise ValueError(f"Unsupported wait type: {wait_type or '<empty>'}")


def perform_after_wait(page: Any, after_wait: Any, *, default_timeout_ms: int) -> None:
    if isinstance(after_wait, dict):
        perform_wait(page, after_wait, default_timeout_ms=default_timeout_ms)
        return
    if isinstance(after_wait, list):
        for item in after_wait:
            perform_after_wait(page, item, default_timeout_ms=default_timeout_ms)
        return
    if after_wait is None:
        return
    try:
        duration_ms = int(after_wait)
    except Exception:
        return
    if duration_ms > 0:
        page.wait_for_timeout(duration_ms)


def perform_step(page: Any, step: dict[str, Any], *, default_timeout_ms: int) -> dict[str, Any]:
    step_type = normalize_text(step.get("type")).lower()
    label = normalize_text(step.get("label")) or step_type or "step"
    timeout_ms = resolve_timeout(step.get("timeout_ms"), default_timeout_ms)
    delay_ms = resolve_timeout(step.get("delay_ms"), 0)
    repeat = resolve_timeout(step.get("repeat"), 1)
    repeat = repeat if repeat > 0 else 1
    nth = step.get("nth")
    nth_index = int(nth) if nth is not None else None
    step_result: dict[str, Any] = {
        "label": label,
        "type": step_type,
        "repeat": repeat,
    }

    for _ in range(repeat):
        if step_type == "click":
            selector = normalize_text(step.get("selector"))
            if not selector:
                raise ValueError("click step requires 'selector'")
            locator = page.locator(selector)
            target = locator.nth(nth_index) if nth_index is not None else locator.first
            target.click(timeout=timeout_ms, force=bool(step.get("force")))
            step_result["selector"] = selector
            if nth_index is not None:
                step_result["nth"] = nth_index
        elif step_type == "wait_selector":
            selector = normalize_text(step.get("selector"))
            state = normalize_text(step.get("state")).lower() or "visible"
            perform_wait(page, {"type": "selector", **step}, default_timeout_ms=default_timeout_ms)
            step_result["selector"] = selector
            step_result["state"] = state
        elif step_type == "wait_function":
            expression = normalize_text(step.get("expression") or step.get("script"))
            perform_wait(page, {"type": "function", **step}, default_timeout_ms=default_timeout_ms)
            step_result["expression"] = expression
        elif step_type == "evaluate":
            expression = normalize_text(step.get("expression") or step.get("script"))
            if not expression:
                raise ValueError("evaluate step requires 'expression'")
            step_result["value"] = page.evaluate(expression, step.get("arg"))
            step_result["expression"] = expression
        elif step_type == "sleep":
            duration_ms = resolve_timeout(step.get("duration_ms") or step.get("ms"), default_timeout_ms)
            page.wait_for_timeout(duration_ms)
            step_result["duration_ms"] = duration_ms
        else:
            raise ValueError(f"Unsupported step type: {step_type or '<empty>'}")

        after_wait = step.get("after_wait")
        if after_wait is not None:
            perform_after_wait(page, after_wait, default_timeout_ms=default_timeout_ms)

        if delay_ms > 0:
            page.wait_for_timeout(delay_ms)

    return step_result


def build_route_handler() -> Any:
    weblog_source = weblog_stub_script()
    backwash_source = backwash_stub_script()

    def handle_route(route: Any) -> None:
        request = route.request
        url = request.url
        if is_weblog_request(url):
            route.fulfill(status=200, content_type="application/javascript", body=weblog_source)
            return
        if is_backwash_request(url):
            route.fulfill(status=200, content_type="application/javascript", body=backwash_source)
            return
        if request.resource_type in {"image", "media", "font"}:
            route.abort()
            return
        route.continue_()

    return handle_route


def create_context(browser: Any, *, viewport_width: int, viewport_height: int) -> Any:
    context = browser.new_context(
        viewport={"width": viewport_width, "height": viewport_height},
        is_mobile=True,
        has_touch=True,
        ignore_https_errors=True,
    )
    context.add_init_script(capture_init_script())
    context.route("**/*", build_route_handler())
    return context
